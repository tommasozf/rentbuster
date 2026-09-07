"""rent-buster.nl HTTP scraper.

rent-buster.nl uses Next.js App Router with RSC (React Server Components) streaming.
Listing data is embedded inline in the RSC payload as JSON props on each card component.
The RSC endpoint is fetched with the 'RSC: 1' header; listing cards match the pattern
{"imageUrl":...,"type":"..."} within each RSC line.

Field paths verified against rent-buster.nl/feed on 2026-05-22.
City filter requires title-case names (e.g. 'Amsterdam', not 'amsterdam').
Pagination via ?page=N (1-indexed).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import requests

from rentbuster.models import EnergyLabel, Listing, Source

log = logging.getLogger(__name__)

BASE_URL = "https://rent-buster.nl"
_LISTINGS_PER_PAGE = 10

# RSC listing card props: {"imageUrl":...,"type":"Appartement"}
_LISTING_RE = re.compile(r'\{"imageUrl":.*?"type":"[^"]+"\}')
# Dutch postal code embedded in address string: "1068LD" or "1068 LD"
_POSTAL_RE = re.compile(r"\b(\d{4})\s*([A-Z]{2})\b")
# House number with optional letter/hyphen suffix: "21-F", "34C", "86"
_ADDR_RE = re.compile(r"^(.+?)\s+(\d+[-–]?\d*[A-Za-z]?)\s*([A-Za-z0-9-]*)$")


def _parse_address(address: str) -> tuple[str, str, str, str]:
    """Parse 'Osdorper Ban 21-F, 1068LD Amsterdam' → (street, number, addition, postal_code).

    The address string from rent-buster.nl is 'Street N[-A], POSTALCity'.
    """
    postal_code = ""
    street_part = address

    comma_idx = address.find(",")
    if comma_idx != -1:
        street_part = address[:comma_idx].strip()
        after_comma = address[comma_idx + 1 :].upper()
        pc_m = _POSTAL_RE.search(after_comma)
        if pc_m:
            postal_code = f"{pc_m.group(1)} {pc_m.group(2)}"

    m = _ADDR_RE.match(street_part)
    if m:
        street = m.group(1).strip()
        number_raw = m.group(2).strip()
        addition_raw = m.group(3).strip()
        # "21-F" → number="21", addition="F"
        # "21-F" → number="21", addition="F" (letter-only suffix = addition)
        hyp_letter = re.match(r"^(\d+)[-–]([A-Za-z])$", number_raw)
        if hyp_letter and not addition_raw:
            return street, hyp_letter.group(1), hyp_letter.group(2), postal_code
        # "34C-22" → addition starts with "-", keep as part of number
        if addition_raw.startswith(("-", "–")):
            return street, number_raw + addition_raw, "", postal_code
        return street, number_raw, addition_raw, postal_code

    return street_part, "", "", postal_code


def _item_to_listing(item: dict[str, Any]) -> Listing | None:
    """Convert a rent-buster.nl RSC listing props dict to a Listing."""
    prop_id = str(item.get("propertyId") or "")
    full_url = str(item.get("fullUrl") or "")
    if not prop_id:
        return None

    source = Source.RENTBUSTER_NL
    if "funda.nl" in full_url:
        source = Source.FUNDA
    elif "kamernet.nl" in full_url:
        source = Source.KAMERNET

    address_raw = str(item.get("address") or "")
    street, house_number, addition, postal_code = _parse_address(address_raw)

    city = str(item.get("city") or "").lower()

    try:
        asking_rent = int(item.get("price") or 0)
    except (TypeError, ValueError):
        asking_rent = 0

    try:
        surface = int(item.get("size") or 0)
    except (TypeError, ValueError):
        surface = 0

    try:
        num_rooms = int(item.get("rooms") or 0)
    except (TypeError, ValueError):
        num_rooms = 0

    energy_label = EnergyLabel.from_string(str(item.get("energyLabel") or ""))

    try:
        woz_value = int(item.get("woz") or 0) or None
    except (TypeError, ValueError):
        woz_value = None
    woz_verified = woz_value is not None  # rent-buster.nl provides real WOZ from Kadaster

    try:
        build_year = int(item.get("buildYear") or 0) or None
    except (TypeError, ValueError):
        build_year = None

    try:
        wws_points = float(item.get("points") or 0) or None
    except (TypeError, ValueError):
        wws_points = None

    try:
        rb_max = float(item.get("reducedPrice") or 0) or None
    except (TypeError, ValueError):
        rb_max = None

    rb_savings = None
    if rb_max is not None and asking_rent > 0:
        rb_savings = float(asking_rent) - rb_max

    furnished = item.get("furnished", False)
    interior = "furnished" if furnished else ""

    prop_type = str(item.get("type") or "apartment").lower()

    images = []
    img = str(item.get("imageUrl") or "")
    if img:
        images = [img]

    return Listing(
        source=source,
        source_id=prop_id,
        url=full_url,
        street=street,
        house_number=house_number,
        house_number_addition=addition,
        postal_code=postal_code,
        city=city,
        asking_rent=asking_rent,
        surface_area_m2=surface,
        num_rooms=num_rooms,
        property_type=prop_type,
        interior=interior,
        energy_label=energy_label,
        woz_value=woz_value,
        woz_verified=woz_verified,
        construction_year=build_year,
        wws_points=wws_points,
        rb_points=wws_points,
        rb_estimated_max_rent=rb_max,
        rb_savings=rb_savings,
        images=images,
    )


class RentbusterNLSource:
    name = "rentbuster_nl"

    def __init__(
        self,
        city: str = "amsterdam",
        max_pages: int = 25,
        user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ) -> None:
        self.city = city
        # rent-buster.nl city filter requires title-case (e.g. "Amsterdam")
        self._city_param = city.title()
        self.max_pages = max_pages
        self._session = self._make_session(user_agent)

    def _make_session(self, user_agent: str) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/x-component, */*",
                "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
                "RSC": "1",
                "Next-Router-State-Tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22feed%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
                "Connection": "keep-alive",
            }
        )
        return session

    def _fetch_page_rsc(self, page: int) -> str | None:
        url = f"{BASE_URL}/feed?city={self._city_param}&page={page}"
        try:
            resp = self._session.get(url, timeout=(8, 20))  # (connect, read)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("rent-buster.nl: page %d fetch failed: %s", page, exc)
            return None

    def _parse_rsc(self, rsc_text: str) -> list[Listing]:
        """Extract listing objects from RSC streaming payload."""
        import json

        listings = []
        for raw in _LISTING_RE.findall(rsc_text):
            try:
                item = json.loads(raw)
                listing = _item_to_listing(item)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                log.debug("rent-buster.nl: parse error: %s", exc)
        return listings

    def _fetch_listings_sync(self) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            rsc = self._fetch_page_rsc(page)
            if not rsc:
                break

            page_listings = self._parse_rsc(rsc)
            if not page_listings:
                log.info("rent-buster.nl: no listings on page %d, stopping", page)
                break

            new = [ls for ls in page_listings if ls.source_id not in seen]
            if not new:
                log.info("rent-buster.nl: no new listings on page %d, stopping", page)
                break

            for ls in new:
                seen.add(ls.source_id)
            listings.extend(new)
            log.info("rent-buster.nl: page %d → %d listings (total: %d)", page, len(new), len(listings))

            if len(page_listings) < _LISTINGS_PER_PAGE:
                # Last page
                break

            time.sleep(1)

        return listings

    async def fetch_listings(self) -> list[Listing]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_listings_sync)

    async def close(self) -> None:
        self._session.close()
