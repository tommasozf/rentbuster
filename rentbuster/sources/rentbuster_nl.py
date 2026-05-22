"""rent-buster.nl HTTP scraper.

rent-buster.nl is a Next.js app — we extract listing data from the __NEXT_DATA__
JSON blob embedded in the HTML. Field paths are based on observed page structure;
they may need adjustment if the site is updated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import requests

from rentbuster.models import Listing, Source
from rentbuster.sources.pararius import _parse_price

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Try both with and without www prefix
_BASE_URLS = ["https://www.rent-buster.nl", "https://rent-buster.nl"]


class RentbusterNLSource:
    name = "rentbuster_nl"

    def __init__(
        self,
        city: str = "amsterdam",
        max_pages: int = 10,
        user_agent: str = "Mozilla/5.0 (compatible; RentBuster/2.0)",
    ) -> None:
        self.city = city.lower()
        self.max_pages = max_pages
        self._session = self._make_session(user_agent)
        self._base_url: str | None = None

    def _make_session(self, user_agent: str) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
                "Connection": "keep-alive",
            }
        )
        return session

    def _resolve_base_url(self) -> str | None:
        """Determine which domain (www. or bare) responds."""
        for url in _BASE_URLS:
            try:
                resp = self._session.get(url, timeout=10, allow_redirects=True)
                if resp.ok:
                    log.debug("rent-buster.nl: using base URL %s", url)
                    return url
            except requests.RequestException:
                continue
        return None

    def _fetch_page_html(self, page: int) -> str | None:
        if self._base_url is None:
            self._base_url = self._resolve_base_url()
            if self._base_url is None:
                log.error("rent-buster.nl: domain not reachable")
                return None

        url = f"{self._base_url}/feed?page={page}&city={self.city}"
        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("rent-buster.nl: page %d fetch failed: %s", page, exc)
            return None

    def _extract_next_data(self, html: str) -> dict | None:
        m = _NEXT_DATA_RE.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def _parse_next_data(self, data: dict) -> list[dict]:
        """Extract listing items from __NEXT_DATA__ JSON.

        Tries several known paths since the structure may vary by page type.
        """
        page_props = data.get("props", {}).get("pageProps", {})
        # Try common Next.js data paths
        for path in (
            ["listings"],
            ["data", "listings"],
            ["initialData", "listings"],
            ["feed"],
            ["items"],
        ):
            obj = page_props
            for key in path:
                if isinstance(obj, dict):
                    obj = obj.get(key)
                else:
                    obj = None
                    break
            if isinstance(obj, list) and obj:
                return obj
        return []

    def _item_to_listing(self, item: dict) -> Listing | None:
        """Convert a raw rent-buster.nl item dict to a Listing."""
        # Extract identifiers
        source_id = str(item.get("id") or item.get("listing_id") or "")
        url = item.get("url") or item.get("link") or item.get("pararius_url") or ""
        if not source_id and not url:
            return None
        if not source_id:
            source_id = url.rstrip("/").split("/")[-1]

        # Address fields
        street = item.get("street") or item.get("straat") or ""
        house_number = str(item.get("house_number") or item.get("huisnummer") or "")
        addition = str(item.get("house_number_addition") or item.get("toevoeging") or "")
        postal_code = item.get("postal_code") or item.get("postcode") or ""
        city = item.get("city") or item.get("stad") or self.city

        # Price
        asking_rent_raw = item.get("rent") or item.get("price") or item.get("huurprijs") or 0
        asking_rent = int(asking_rent_raw) if isinstance(asking_rent_raw, (int, float)) else _parse_price(str(asking_rent_raw))

        # Size
        surface = item.get("surface") or item.get("oppervlak") or item.get("surface_area") or 0
        try:
            surface = int(surface)
        except (TypeError, ValueError):
            surface = 0

        # Rent-buster analysis fields
        rb_max_rent = None
        rb_savings = None
        rb_confidence = None

        for key in ("max_rent", "maximum_rent", "wws_max_rent", "legal_max_rent"):
            if item.get(key) is not None:
                try:
                    rb_max_rent = float(item[key])
                except (TypeError, ValueError):
                    pass
                break

        asking = float(asking_rent)
        if rb_max_rent is not None and asking > 0:
            rb_savings = asking - rb_max_rent

        for key in ("confidence", "betrouwbaarheid", "certainty"):
            if item.get(key) is not None:
                rb_confidence = str(item[key])
                break

        return Listing(
            source=Source.RENTBUSTER_NL,
            source_id=source_id,
            url=url,
            street=street,
            house_number=house_number,
            house_number_addition=addition,
            postal_code=postal_code,
            city=city,
            asking_rent=asking_rent,
            surface_area_m2=surface,
            rb_estimated_max_rent=rb_max_rent,
            rb_savings=rb_savings,
            rb_confidence=rb_confidence,
        )

    async def fetch_listings(self) -> list[Listing]:
        """Fetch all pages from rent-buster.nl; runs in an executor to keep sync requests."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_listings_sync)

    def _fetch_listings_sync(self) -> list[Listing]:
        listings: list[Listing] = []

        for page in range(1, self.max_pages + 1):
            html = self._fetch_page_html(page)
            if not html:
                break

            data = self._extract_next_data(html)
            if not data:
                log.warning("rent-buster.nl: no __NEXT_DATA__ on page %d", page)
                break

            items = self._parse_next_data(data)
            if not items:
                log.info("rent-buster.nl: no listings on page %d, stopping", page)
                break

            new = [self._item_to_listing(item) for item in items]
            valid = [l for l in new if l is not None]
            listings.extend(valid)
            log.info("rent-buster.nl: page %d → %d listings", page, len(valid))

            if len(valid) < len(items) // 2:
                # Too many parse failures — probably at end of data
                break
            time.sleep(1)

        return listings

    async def close(self) -> None:
        self._session.close()
