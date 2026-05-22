"""Apprise notifier — 100+ channels via a single URL scheme."""

from __future__ import annotations

import logging

from rentbuster.models import Listing

log = logging.getLogger(__name__)


class AppriseNotifier:
    name = "apprise"

    def __init__(self, urls: str) -> None:
        self.urls = [u.strip() for u in urls.split(",") if u.strip()]

    def send_listings(self, listings: list[Listing]) -> None:
        if not listings:
            return

        try:
            import apprise
        except ImportError:
            log.error("apprise package not installed")
            return

        ap = apprise.Apprise()
        for url in self.urls:
            ap.add(url)

        for listing in listings:
            title, body = self._format_listing(listing)
            try:
                ap.notify(title=title, body=body)
                log.info("apprise: sent for %s/%s", listing.source.value, listing.source_id)
            except Exception as exc:
                log.warning("apprise notify failed: %s", exc)

    def _format_listing(self, listing: Listing) -> tuple[str, str]:
        address = f"{listing.street} {listing.house_number}"
        if listing.house_number_addition:
            address += f"-{listing.house_number_addition}"
        city = listing.city.title() if listing.city else "Unknown"

        savings = listing.wws_savings or 0
        max_rent = listing.wws_max_rent or 0
        points = listing.wws_points or 0
        conf = listing.wws_confidence.value if listing.wws_confidence else "unknown"

        title = f"Bustable: {address}, {city} — save €{savings:.0f}/mo"
        body = (
            f"Asking: €{listing.asking_rent}/mo\n"
            f"Max legal ({points:.0f} pts): €{max_rent:.0f}/mo\n"
            f"Savings: €{savings:.0f}/mo (€{savings * 12:.0f}/yr)\n"
            f"Confidence: {conf}\n"
            f"URL: {listing.url}"
        )
        return title, body
