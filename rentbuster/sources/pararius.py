"""Pararius.com scraper using Playwright.

CSS selectors verified against pararius.com/apartments/amsterdam on 2026-05-22.
Uses playwright-stealth to bypass Cloudflare Turnstile bot detection.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

from rentbuster.models import EnergyLabel, Listing, Source

log = logging.getLogger(__name__)

BASE_URL = "https://www.pararius.com"

# Property type prefixes in listing titles (stripped before address parsing)
_PROPERTY_TYPE_PREFIXES = {
    "flat": "apartment",
    "apartment": "apartment",
    "studio": "studio",
    "room": "room",
    "house": "house",
    "villa": "house",
}

# ── Parsing helpers (module-level pure functions for testability) ──────────────

_PRICE_RE = re.compile(r"[\d]+")
_AREA_RE = re.compile(r"(\d+)\s*m")
_POSTAL_RE = re.compile(r"\b(\d{4})\s*([A-Z]{2})\b")
_ADDRESS_RE = re.compile(r"^(.+?)\s+(\d+[-–]?\d*)\s*([A-Za-z]*)$")
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20[0-2]\d)\b")


def _parse_price(text: str) -> int:
    """Extract integer EUR/month from strings like '€2,600 pcm' or '€ 1.450'."""
    if not text:
        return 0
    # Strip everything that's not a digit (period/comma as thousands sep → remove)
    cleaned = text.replace(".", "").replace(",", "").replace("\xa0", "")
    nums = _PRICE_RE.findall(cleaned)
    for n in nums:
        try:
            val = int(n)
            if val > 100:  # skip tiny numbers that are price-per-m² etc.
                return val
        except ValueError:
            continue
    return 0


def _parse_area(text: str) -> int:
    """Extract integer m² from strings like '75 m²' or '75m2'."""
    if not text:
        return 0
    m = _AREA_RE.search(text)
    return int(m.group(1)) if m else 0


def _parse_energy_label(text: str) -> EnergyLabel | None:
    """Extract energy label from a string like 'D' or 'A++'."""
    if not text:
        return None
    return EnergyLabel.from_string(text.strip().split()[0])


def _extract_postal_code(text: str) -> str | None:
    """Match Dutch postal code like '1012 AE' or '1012AE'."""
    m = _POSTAL_RE.search(text.upper())
    return f"{m.group(1)} {m.group(2)}" if m else None


def _parse_address(title: str) -> tuple[str, str, str, str]:
    """Parse 'Flat Prins Hendrikkade 86 A' → (property_type, street, number, addition).

    Strips a known property type prefix word, then parses the remainder.
    Returns (property_type, street, house_number, addition).
    """
    if not title:
        return "apartment", "", "", ""

    words = title.strip().split()
    property_type = "apartment"
    if words and words[0].lower() in _PROPERTY_TYPE_PREFIXES:
        property_type = _PROPERTY_TYPE_PREFIXES[words[0].lower()]
        words = words[1:]

    address = " ".join(words)
    m = _ADDRESS_RE.match(address)
    if m:
        street = m.group(1).strip()
        number = m.group(2).strip()
        addition = m.group(3).strip()
        return property_type, street, number, addition

    # Fallback: find last digit sequence
    parts = address.rsplit(" ", 1)
    if len(parts) == 2 and re.match(r"\d", parts[1]):
        return property_type, parts[0], parts[1], ""

    return property_type, address, "", ""


def _parse_rooms(text: str) -> int:
    """Extract room count from '3 rooms' or '3 kamers'."""
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


def _parse_construction_year(text: str) -> int | None:
    m = _YEAR_RE.search(text or "")
    return int(m.group()) if m else None


# ── Pararius source ────────────────────────────────────────────────────────────

class ParariusSource:
    name = "pararius"

    def __init__(
        self,
        city: str = "amsterdam",
        max_rent: int = 0,
        min_size: int = 0,
        max_pages: int = 5,
        headless: bool = True,
        detail_delay: float = 2.0,
        fetch_details: bool = True,
    ) -> None:
        self.city = city.lower()
        self.max_rent = max_rent
        self.min_size = min_size
        self.max_pages = max_pages
        self.headless = headless
        self.detail_delay = detail_delay
        self.fetch_details = fetch_details
        self._browser = None
        self._context = None
        self._playwright = None

    def _build_search_url(self, page: int = 1) -> str:
        path = f"/apartments/{self.city}"
        if self.max_rent:
            path += f"/0-{self.max_rent}"
        if page > 1:
            path += f"/page-{page}"
        return f"{BASE_URL}{path}"

    async def _ensure_browser(self):
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        # Apply stealth to all new pages in this context
        self._stealth = Stealth()
        log.debug("pararius: browser launched")

    async def _new_page(self):
        await self._ensure_browser()
        page = await self._context.new_page()
        await self._stealth.apply_stealth_async(page)
        page.set_default_timeout(30_000)
        return page

    async def _get_page(self, url: str, wait_for_listings: bool = False):
        page = await self._new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            if wait_for_listings:
                try:
                    await page.wait_for_selector(
                        "li.search-list__item--listing", timeout=12_000
                    )
                except Exception:
                    await asyncio.sleep(3)  # fallback if no listings found
            else:
                await asyncio.sleep(2)
        except Exception as exc:
            log.warning("pararius: page load issue for %s: %s", url, exc)
        return page

    async def fetch_listings(self) -> list[Listing]:
        listings: list[Listing] = []
        seen_ids: set[str] = set()

        for page_num in range(1, self.max_pages + 1):
            url = self._build_search_url(page_num)
            log.info("pararius: fetching page %d — %s", page_num, url)

            try:
                page = await self._get_page(url, wait_for_listings=True)
                page_listings = await self._parse_listing_cards(page)
                await page.close()
            except Exception as exc:
                log.error("pararius: error on page %d: %s", page_num, exc)
                break

            new = [l for l in page_listings if l.source_id not in seen_ids]
            if not new:
                log.info("pararius: no new listings on page %d, stopping", page_num)
                break

            for l in new:
                seen_ids.add(l.source_id)
            listings.extend(new)
            log.info("pararius: page %d → %d listings (total: %d)", page_num, len(new), len(listings))

            if page_num < self.max_pages:
                await asyncio.sleep(random.uniform(2.0, 5.0))

        if self.fetch_details and listings:
            log.info("pararius: fetching details for %d listings", len(listings))
            for listing in listings:
                try:
                    await self.fetch_listing_details(listing)
                except Exception as exc:
                    log.warning("pararius: detail fetch failed for %s: %s", listing.source_id, exc)
                await asyncio.sleep(random.uniform(1.0, self.detail_delay))

        return listings

    async def _parse_listing_cards(self, page) -> list[Listing]:
        # Verified selector: li.search-list__item--listing (2026-05-22)
        cards = await page.query_selector_all("li.search-list__item--listing")
        if not cards:
            log.warning("pararius: no listing cards found — selector may need updating")
            return []

        listings = []
        for card in cards:
            try:
                listing = await self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                log.debug("pararius: card parse error: %s", exc)

        return listings

    async def _parse_card(self, card) -> Listing | None:
        # Link — verified: a.listing-search-item__link--depiction
        link = await card.query_selector("a.listing-search-item__link--depiction")
        if not link:
            return None
        href = await link.get_attribute("href") or ""
        if not href:
            return None

        # URL format: /apartment-for-rent/amsterdam/9f9d422e/prins-hendrikkade
        url = f"{BASE_URL}{href}"
        parts = href.rstrip("/").split("/")
        # source_id is the UUID-like segment (3rd from end in the path)
        source_id = parts[-2] if len(parts) >= 2 else parts[-1]

        # Title / address — verified: h3 or .listing-search-item__title
        title_el = await card.query_selector("h2.listing-search-item__title, h3.listing-search-item__title")
        if not title_el:
            title_el = await card.query_selector(".listing-search-item__title")
        title = (await title_el.inner_text()).strip() if title_el else ""
        property_type, street, house_number, addition = _parse_address(title)

        # Sub-title contains postal code and neighborhood — verified: .listing-search-item__sub-title
        sub_title_el = await card.query_selector(".listing-search-item__sub-title")
        sub_title = (await sub_title_el.inner_text()).strip() if sub_title_el else ""
        postal_code = _extract_postal_code(sub_title) or ""

        # Neighborhood: text after city name in parentheses: "1012 AE Amsterdam (Burgwallen-Oude Zijde)"
        neighborhood = ""
        neigh_m = re.search(r"\(([^)]+)\)", sub_title)
        if neigh_m:
            neighborhood = neigh_m.group(1)

        # Price — verified: .listing-search-item__price
        price_el = await card.query_selector(".listing-search-item__price")
        price_text = (await price_el.inner_text()).strip() if price_el else ""
        asking_rent = _parse_price(price_text)

        # Features: "70 m²\n2 rooms\nFurnished" — verified: .listing-search-item__features
        features_el = await card.query_selector(".listing-search-item__features")
        features_text = (await features_el.inner_text()).strip() if features_el else ""
        surface_area = _parse_area(features_text)
        num_rooms = 0
        interior = ""
        for line in features_text.split("\n"):
            line = line.strip()
            if "room" in line.lower() or "kamer" in line.lower():
                num_rooms = _parse_rooms(line)
            elif any(kw in line.lower() for kw in ("furnished", "unfurnished", "bare", "gemeubileerd")):
                interior = line

        # Agency — verified: .listing-search-item__agent
        agency_el = await card.query_selector(".listing-search-item__agent")
        agency_name = (await agency_el.inner_text()).strip() if agency_el else ""

        # Image
        img_el = await card.query_selector("img.picture__image")
        images = []
        if img_el:
            src = await img_el.get_attribute("src") or ""
            if src:
                images = [src]

        return Listing(
            source=Source.PARARIUS,
            source_id=source_id,
            url=url,
            street=street,
            house_number=house_number,
            house_number_addition=addition,
            postal_code=postal_code,
            city=self.city,
            neighborhood=neighborhood,
            asking_rent=asking_rent,
            surface_area_m2=surface_area,
            num_rooms=num_rooms,
            property_type=property_type,
            interior=interior,
            images=images,
        )

    async def fetch_listing_details(self, listing: Listing) -> None:
        """Visit the detail page and enrich listing with energy label, year, description, etc."""
        page = await self._get_page(listing.url)
        try:
            await self._parse_detail_page(page, listing)
        finally:
            await page.close()

    async def _parse_detail_page(self, page, listing: Listing) -> None:
        # Postal code + neighborhood from detail — verified: .listing-detail-summary__location
        loc_el = await page.query_selector(".listing-detail-summary__location")
        if loc_el:
            loc_text = (await loc_el.inner_text()).strip()
            pc = _extract_postal_code(loc_text)
            if pc and not listing.postal_code:
                listing.postal_code = pc
            neigh_m = re.search(r"\(([^)]+)\)", loc_text)
            if neigh_m and not listing.neighborhood:
                listing.neighborhood = neigh_m.group(1)

        # Agency name
        agency_el = await page.query_selector(".listing-detail-summary__agent, [class*=agent-summary]")
        if agency_el and not listing.agency_name:
            listing.agency_name = (await agency_el.inner_text()).strip()

        # Available from
        avail_el = await page.query_selector(".listing-detail-summary__availability")
        if avail_el:
            listing.available_from = (await avail_el.inner_text()).strip()

        # Feature dt/dd table — verified structure (2026-05-22)
        dts = await page.query_selector_all("dt")
        dds = await page.query_selector_all("dd")
        for dt, dd in zip(dts, dds):
            key = (await dt.inner_text()).strip().lower()
            val = (await dd.inner_text()).strip()
            if not val or "more info" in val.lower():
                continue

            if "energy rating" in key or "energielabel" in key:
                listing.energy_label = _parse_energy_label(val)
            elif "year of construction" in key or "bouwjaar" in key:
                listing.construction_year = _parse_construction_year(val)
            elif "interior" in key or "interieur" in key:
                if not listing.interior:
                    listing.interior = val.split("\n")[0]
            elif "living area" in key or "woonoppervlak" in key:
                area = _parse_area(val)
                if area and not listing.surface_area_m2:
                    listing.surface_area_m2 = area
            elif "number of rooms" in key or "aantal kamers" in key:
                rooms = _parse_rooms(val)
                if rooms and not listing.num_rooms:
                    listing.num_rooms = rooms
            elif "available" in key and not listing.available_from:
                listing.available_from = val
            elif "type of house" in key and not listing.property_type:
                listing.property_type = val.split("\n")[0].lower()

        # Description — verified: [class*=description]
        desc_el = await page.query_selector("[class*=description] p, [class*=description]")
        if desc_el and not listing.description:
            listing.description = (await desc_el.inner_text()).strip()

        # Images — look for picture elements
        img_els = await page.query_selector_all("img.picture__image")
        images = []
        for img in img_els[:8]:
            src = await img.get_attribute("src") or ""
            if src and src not in images and "pararius" not in src:
                # Filter out logos etc. — real listing images are from casco-media-prod CDN
                pass
            if src and src not in images:
                images.append(src)
        if images:
            listing.images = images[:6]

    async def close(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        log.debug("pararius: browser closed")
