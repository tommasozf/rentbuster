"""Main pipeline: fetch → dedup → WOZ → WWS → persist → notify."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime

from rentbuster.config import Settings
from rentbuster.db import Database
from rentbuster.dedup import deduplicate
from rentbuster.llm import LLMExtraction, extract_batch
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

    @staticmethod
    async def _fetch_and_close(source) -> list:
        try:
            return await source.fetch_listings()
        finally:
            with contextlib.suppress(Exception):
                await source.close()

    def check_once(self) -> None:
        log.info("=" * 60)
        log.info("check at %s — profile=%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.profile.name)

        self.notifiers.process_commands()

        # 1. Fetch from all sources
        sources = build_sources(self.settings, self.profile)
        all_listings: list[Listing] = []

        for source in sources:
            log.info("starting %s...", source.name)
            try:
                source_listings = asyncio.run(self._fetch_and_close(source))
                log.info("%s: fetched %d listings", source.name, len(source_listings))
                all_listings.extend(source_listings)
            except Exception as exc:
                log.error("%s: fetch failed: %s", source.name, exc)

        if not all_listings:
            log.info("no listings returned from any source")
            if self.db and not self.dry_run:
                self.db.log_scrape_run("all", 0, 0, 0, "no listings")
            return

        # 2. Apply profile filters (rooms, property type)
        before = len(all_listings)
        all_listings = self._apply_search_filters(all_listings)
        if len(all_listings) < before:
            log.info(
                "filtered %d → %d listings (max_rooms=%s, types=%s)",
                before,
                len(all_listings),
                self.profile.search.max_rooms or "any",
                self.profile.search.property_types or "any",
            )

        # 3. Deduplicate by address
        deduped = deduplicate(all_listings)
        log.info("total=%d after dedup=%d", len(all_listings), len(deduped))

        # 4. Filter new listings
        new_listings = [ls for ls in deduped if (ls.source.value, ls.source_id) not in self.seen_ids]
        for ls in new_listings:
            self.seen_ids.add((ls.source.value, ls.source_id))

        log.info("new=%d", len(new_listings))

        if not new_listings:
            if self.db and not self.dry_run:
                by_source: dict[str, list[str]] = {}
                for ls in deduped:
                    by_source.setdefault(ls.source.value, []).append(ls.source_id)
                for src, ids in by_source.items():
                    self.db.touch_listings(src, ids)
                self.db.mark_disappeared()
            return

        # 5. WOZ lookup (check DB cache first)
        need_woz = [ls for ls in new_listings if not (ls.woz_value and ls.woz_verified)]
        if need_woz:
            log.info(
                "WOZ lookup for %d listings (skipping %d with verified WOZ)...",
                len(need_woz),
                len(new_listings) - len(need_woz),
            )
        for i, listing in enumerate(new_listings):
            self._resolve_woz(listing)
            if need_woz and (i + 1) % 50 == 0:
                log.info("  WOZ progress: %d/%d", i + 1, len(new_listings))

        # 6. LLM feature extraction (optional)
        extractions: dict[str, LLMExtraction] = {}
        if self.settings.llm_enabled and self.settings.gemini_api_key:
            extractions = extract_batch(new_listings, self.settings.gemini_api_key, self.settings.llm_model)

        # 6b. Merge LLM suitability fields when detail parsing didn't find them
        for listing in new_listings:
            key = f"{listing.source.value}:{listing.source_id}"
            ext = extractions.get(key)
            if ext:
                if listing.suitable_for_students is None:
                    listing.suitable_for_students = ext.suitable_for_students
                if listing.suitable_for_sharing is None:
                    listing.suitable_for_sharing = ext.suitable_for_sharing
                if listing.guarantor_accepted is None:
                    listing.guarantor_accepted = ext.guarantor_accepted

        # 6c. Re-apply suitability filters now that LLM has filled in missing fields
        before_refilter = len(new_listings)
        new_listings = self._apply_search_filters(new_listings)
        if len(new_listings) < before_refilter:
            log.info(
                "post-LLM suitability filter removed %d listings",
                before_refilter - len(new_listings),
            )

        # 7. Calculate WWS points
        for listing in new_listings:
            key = f"{listing.source.value}:{listing.source_id}"
            calculate_wws(listing, llm_extraction=extractions.get(key))
            log.debug(
                "  %s %s — %s pts → €%s/mo (bustable=%s)",
                listing.street,
                listing.house_number,
                listing.wws_points,
                listing.wws_max_rent,
                listing.wws_is_bustable,
            )

        # 7. Persist all new listings
        if self.db and not self.dry_run:
            for listing in new_listings:
                self.db.upsert_listing(listing)
            by_source: dict[str, list[str]] = {}
            for ls in deduped:
                by_source.setdefault(ls.source.value, []).append(ls.source_id)
            for src, ids in by_source.items():
                self.db.touch_listings(src, ids)

        # 8. Filter bustable listings
        bustable = [
            ls
            for ls in new_listings
            if ls.wws_is_bustable and (ls.wws_savings or 0) >= self.profile.wws.min_savings
        ]
        log.info(
            "new=%d bustable=%d (min_savings=€%d)",
            len(new_listings),
            len(bustable),
            self.profile.wws.min_savings,
        )

        for bl in bustable:
            log.info(
                "  BUSTABLE: %s %s — €%d asking, €%.0f max, savings €%.0f (conf=%s)",
                bl.street,
                bl.house_number,
                bl.asking_rent,
                bl.wws_max_rent or 0,
                bl.wws_savings or 0,
                bl.wws_confidence.value if bl.wws_confidence else "?",
            )

        # 9. Notify
        if bustable:
            if self.dry_run:
                log.info("dry-run: skipping notifications for %d bustable listings", len(bustable))
            else:
                self.notifiers.send_listings(bustable)

        # 10. Cleanup
        if self.db and not self.dry_run:
            self.db.mark_disappeared()
            self.db.log_scrape_run("all", len(all_listings), len(new_listings), len(bustable))

        # Write heartbeat for Docker HEALTHCHECK
        try:
            import time as _time

            with open("/tmp/rentbuster_last_run", "w") as _f:
                _f.write(str(int(_time.time())))
        except Exception:
            pass

    def _apply_search_filters(self, listings: list[Listing]) -> list[Listing]:
        search = self.profile.search
        allowed_types = {t.lower() for t in search.property_types}
        result = []
        for ls in listings:
            if search.max_rooms and ls.num_rooms > search.max_rooms:
                continue
            if allowed_types and ls.property_type and ls.property_type.lower() not in allowed_types:
                continue
            if search.must_allow_students and ls.suitable_for_students is False:
                continue
            if search.must_allow_sharing and ls.suitable_for_sharing is False:
                continue
            if search.must_accept_guarantor and ls.guarantor_accepted is False:
                continue
            result.append(ls)
        return result

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
                listing._woz_source = cached.get("source", "")  # type: ignore[attr-defined]
                return

        result = lookup_woz(listing)

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
