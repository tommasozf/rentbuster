"""Main pipeline: fetch → dedup → WOZ → WWS → persist → notify."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

from rentbuster.config import Settings
from rentbuster.db import Database
from rentbuster.dedup import deduplicate
from rentbuster.models import Listing
from rentbuster.notify import NotifierBundle
from rentbuster.profile import Profile
from rentbuster.sources import build_sources
from rentbuster.woz import lookup_woz
from rentbuster.wws import calculate_wws

log = logging.getLogger(__name__)


class RentBuster:
    """Stateful pipeline. One instance per process."""

    def __init__(
        self,
        settings: Settings,
        profile: Profile,
        db: Database | None,
        notifiers: NotifierBundle,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.db = db
        self.notifiers = notifiers
        self.dry_run = dry_run
        self.seen_ids: set[tuple[str, str]] = db.load_seen_source_ids() if db else set()

    def check_once(self) -> None:
        log.info("=" * 60)
        log.info("check at %s — profile=%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.profile.name)

        self.notifiers.process_commands()

        # 1. Fetch from all sources
        sources = build_sources(self.settings, self.profile)
        all_listings: list[Listing] = []

        for source in sources:
            try:
                source_listings = asyncio.run(source.fetch_listings())
                log.info("%s: fetched %d listings", source.name, len(source_listings))
                all_listings.extend(source_listings)
            except Exception as exc:
                log.error("%s: fetch failed: %s", source.name, exc)
            finally:
                try:
                    asyncio.run(source.close())
                except Exception:
                    pass

        if not all_listings:
            log.info("no listings returned from any source")
            if self.db and not self.dry_run:
                self.db.log_scrape_run("all", 0, 0, 0, "no listings")
            return

        # 2. Deduplicate by address
        deduped = deduplicate(all_listings)
        log.info("total=%d after dedup=%d", len(all_listings), len(deduped))

        # 3. Filter new listings
        new_listings = [
            l for l in deduped
            if (l.source.value, l.source_id) not in self.seen_ids
        ]
        for l in new_listings:
            self.seen_ids.add((l.source.value, l.source_id))

        log.info("new=%d", len(new_listings))

        if not new_listings:
            if self.db and not self.dry_run:
                self.db.mark_disappeared()
            return

        # 4. WOZ lookup (check DB cache first)
        for listing in new_listings:
            self._resolve_woz(listing)

        # 5. Calculate WWS points
        for listing in new_listings:
            calculate_wws(listing)
            log.debug(
                "  %s %s — %s pts → €%s/mo (bustable=%s)",
                listing.street,
                listing.house_number,
                listing.wws_points,
                listing.wws_max_rent,
                listing.wws_is_bustable,
            )

        # 6. Persist all new listings
        if self.db and not self.dry_run:
            for listing in new_listings:
                self.db.upsert_listing(listing)
            self.db.touch_listings(
                "all",
                [l.source_id for l in deduped],
            )

        # 7. Filter bustable listings
        bustable = [
            l for l in new_listings
            if l.wws_is_bustable
            and (l.wws_savings or 0) >= self.profile.wws.min_savings
        ]
        log.info(
            "new=%d bustable=%d (min_savings=€%d)",
            len(new_listings),
            len(bustable),
            self.profile.wws.min_savings,
        )

        for l in bustable:
            log.info(
                "  BUSTABLE: %s %s — €%d asking, €%.0f max, savings €%.0f (conf=%s)",
                l.street,
                l.house_number,
                l.asking_rent,
                l.wws_max_rent or 0,
                l.wws_savings or 0,
                l.wws_confidence.value if l.wws_confidence else "?",
            )

        # 8. Notify
        if bustable:
            if self.dry_run:
                log.info("dry-run: skipping notifications for %d bustable listings", len(bustable))
            else:
                self.notifiers.send_listings(bustable)

        # 9. Cleanup
        if self.db and not self.dry_run:
            self.db.mark_disappeared()
            self.db.log_scrape_run("all", len(all_listings), len(new_listings), len(bustable))

    def _resolve_woz(self, listing: Listing) -> None:
        """Check DB cache, then Kadaster API, then estimate."""
        # Check DB cache first
        if self.db and listing.postal_code and listing.house_number:
            cached = self.db.get_cached_woz(
                listing.postal_code,
                listing.house_number,
                listing.house_number_addition,
            )
            if cached:
                listing.woz_value = cached["value"]
                listing.woz_reference_date = cached["reference_date"]
                listing.woz_verified = cached["verified"]
                return

        result = lookup_woz(listing, self.settings.kadaster_api_key)

        # Cache the result
        if self.db and listing.postal_code and listing.house_number:
            self.db.cache_woz(
                listing.postal_code,
                listing.house_number,
                listing.house_number_addition,
                result.value,
                result.reference_date,
                result.verified,
                result.source,
            )

    def run_forever(self) -> None:
        self._print_banner()
        while True:
            try:
                self.check_once()
            except Exception as exc:
                log.exception("unhandled error in main loop: %s", exc)

            wait = random.randint(self.settings.check_interval_min, self.settings.check_interval_max)
            log.info("sleeping %ds until next check", wait)
            try:
                time.sleep(wait)
            except KeyboardInterrupt:
                log.info("interrupted — exiting")
                break

    def _print_banner(self) -> None:
        log.info("RentBuster starting")
        log.info("  profile:   %s (%s)", self.profile.name, self.profile.source_path)
        log.info("  city:      %s", self.profile.search.city)
        log.info("  interval:  %d-%ds", self.settings.check_interval_min, self.settings.check_interval_max)
        log.info("  database:  %s", "yes" if self.db else "no (DB writes skipped)")
        log.info(
            "  notifiers: %s",
            ", ".join(self.notifiers.active_names) if self.notifiers.active else "none",
        )
        log.info("  dry-run:   %s", "yes" if self.dry_run else "no")
