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

from rentbuster.address import split_street_number
from rentbuster.models import EnergyLabel, Listing, Source

log = logging.getLogger(__name__)

BASE_URL = "https://rent-buster.nl"
_LISTINGS_PER_PAGE = 10

# RSC listing card props: {"imageUrl":...,"type":"Appartement"}
_LISTING_RE = re.compile(r'\{"imageUrl":.*?"type":"[^"]+"\}')
# Dutch postal code embedded in address string: "1068LD" or "1068 LD"
_POSTAL_RE = re.compile(r"\b(\d{4})\s*([A-Z]{2})\b")

# rent-buster.nl reports Dutch (and occasionally English) property types. Map them onto the
# English values used in profiles (apartment / studio / room / house) so the filter works.
_PROPERTY_TYPES: dict[str, str] = {
    "appartement": "apartment",
    "apartment": "apartment",
    "penthouse": "apartment",
    "maisonnette": "apartment",
    "bovenwoning": "apartment",
    "benedenwoning": "apartment",
    "portiekwoning": "apartment",
    "galerijflat": "apartment",
    "flat": "apartment",
    "studio": "studio",
    "kamer": "room",
    "room": "room",
    "huis": "house",
    "woonhuis": "house",
    "eengezinswoning": "house",
    "herenhuis": "house",
    "hoekwoning": "house",
    "tussenwoning": "house",
    "villa": "house",
}


def normalize_property_type(raw: str | None) -> str:
    """'Appartement' → 'apartment', 'Penthouse (appartement)' → 'apartment', 'Kamer' → 'room'.

    Unknown values are returned lower-cased so they show up in logs instead of being silently
    treated as apartments.
    """
    t = (raw or "").strip().lower()
    if not t:
        return "apartment"
    if t in _PROPERTY_TYPES:
        return _PROPERTY_TYPES[t]
    for key, value in _PROPERTY_TYPES.items():
        if key in t:
            return value
    log.debug("rent-buster.nl: unknown property type %r", raw)
    return t


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

    street, number, addition = split_street_number(street_part)
    return street, number, addition, postal_code


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

    prop_type = normalize_property_type(str(item.get("type") or ""))

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
