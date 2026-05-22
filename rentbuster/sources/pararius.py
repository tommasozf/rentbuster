"""Pararius.com scraper using Playwright.

CRITICAL: CSS selectors below were written based on Pararius DOM inspection
(May 2025). If selectors stop matching, re-inspect pararius.com/apartments/amsterdam
in a browser with DevTools to update them.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

from rentbuster.models import EnergyLabel, Listing, Source

log = logging.getLogger(__name__)

BASE_URL = "https://www.pararius.com"

# ── Parsing helpers (module-level, pure functions for testability) ──────────────

_PRICE_RE = re.compile(r"[\d.,]+")
_AREA_RE = re.compile(r"(\d+)\s*m")
_POSTAL_RE = re.compile(r"\b(\d{4})\s*([A-Z]{2})\b")
_ADDRESS_RE = re.compile(
    r"^(.+?)\s+(\d+)\s*[-–]?\s*([A-Za-z0-9]*)$"
)


def _parse_price(text: str) -> int:
    """Extract integer EUR/month from strings like '€ 1,450 /month' or '1.450'."""
    if not text:
        return 0
    cleaned = text.replace(".", "").replace(",", "")
    nums = _PRICE_RE.findall(cleaned)
    if not nums:
        return 0
    try:
        return int(nums[0])
    except (ValueError, IndexError):
        return 0


def _parse_area(text: str) -> int:
    """Extract integer m² from strings like '75 m²' or '75m2'."""
    if not text:
        return 0
    m = _AREA_RE.search(text)
    return int(m.group(1)) if m else 0


def _parse_energy_label(text: str) -> EnergyLabel | None:
    """Extract energy label from text."""
    if not text:
        return None
    text = text.strip()
    return EnergyLabel.from_string(text)


def _extract_postal_code(text: str) -> str | None:
    """Match Dutch postal code pattern like '1015 CJ' or '1015CJ'."""
    m = _POSTAL_RE.search(text.upper())
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def _parse_address(text: str) -> tuple[str, str, str]:
    """Parse 'Street 123-A' or 'Van der Pekstraat 42 II' into (street, number, addition).

    Returns ('', '', '') on parse failure.
    """
    if not text:
        return "", "", ""
    text = text.strip()
    m = _ADDRESS_RE.match(text)
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    # Fallback: try to find number at the end
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], parts[1], ""
    return text, "", ""


def _parse_rooms(text: str) -> int:
    """Extract room count from '3 rooms' or '3 kamers'."""
    m = re.search(r"(\d+)", text or "")
    return int(m.group(1)) if m else 0


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

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        log.debug("pararius: browser launched")

    async def _get_page(self, url: str):
        """Navigate to a URL and return the page object."""
        await self._ensure_browser()
        page = await self._browser.new_page()
        page.set_default_timeout(30_000)
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=15_000)
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
                page = await self._get_page(url)
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
            log.info("pararius: page %d → %d listings (total so far: %d)", page_num, len(new), len(listings))

            if page_num < self.max_pages:
                delay = random.uniform(2.0, 5.0)
                await asyncio.sleep(delay)

        if self.fetch_details and listings:
            log.info("pararius: fetching detail pages for %d listings", len(listings))
            for listing in listings:
                try:
                    await self.fetch_listing_details(listing)
                except Exception as exc:
                    log.warning("pararius: detail fetch failed for %s: %s", listing.source_id, exc)
                await asyncio.sleep(random.uniform(1.0, self.detail_delay))

        return listings

    async def _parse_listing_cards(self, page) -> list[Listing]:
        """Parse listing cards from a search results page."""
        listings: list[Listing] = []

        # Try multiple selector patterns — Pararius may change DOM structure
        card_selectors = [
            "article.listing-search-item",
            "li.search-list__item--listing",
            "[class*='listing-search-item']",
            "article[data-object-url-title]",
        ]
        cards = []
        for selector in card_selectors:
            cards = await page.query_selector_all(selector)
            if cards:
                log.debug("pararius: found %d cards with selector %r", len(cards), selector)
                break

        if not cards:
            log.warning("pararius: no listing cards found on page — selectors may need updating")
            return []

        for card in cards:
            try:
                listing = await self._parse_card(card)
                if listing:
                    listings.append(listing)
            except Exception as exc:
                log.debug("pararius: card parse error: %s", exc)

        return listings

    async def _parse_card(self, card) -> Listing | None:
        """Extract a Listing from a single search result card element."""
        # Get the listing detail URL (used as source_id anchor)
        link = await card.query_selector("a[href*='/apartment/']")
        if not link:
            link = await card.query_selector("a.listing-search-item__link")
        if not link:
            link = await card.query_selector("h2 a, h3 a")
        if not link:
            return None

        href = await link.get_attribute("href") or ""
        if not href:
            return None
        url = href if href.startswith("http") else f"{BASE_URL}{href}"
        # Extract source_id from URL path (last path segment)
        source_id = url.rstrip("/").split("/")[-1]
        if not source_id:
            return None

        # Title / address
        title_el = await card.query_selector("h2, h3, .listing-search-item__title")
        title = (await title_el.inner_text()).strip() if title_el else ""
        street, house_number, addition = _parse_address(title)

        # Price
        price_el = await card.query_selector(
            ".listing-search-item__price, [class*='price'], span[class*='price']"
        )
        price_text = (await price_el.inner_text()).strip() if price_el else ""
        asking_rent = _parse_price(price_text)

        # Surface area
        area_el = await card.query_selector(
            ".listing-search-item__surface, [class*='surface'], [class*='size']"
        )
        area_text = (await area_el.inner_text()).strip() if area_el else ""
        surface_area = _parse_area(area_text)

        # Rooms
        rooms_el = await card.query_selector("[class*='rooms'], [class*='kamers']")
        rooms_text = (await rooms_el.inner_text()).strip() if rooms_el else ""
        num_rooms = _parse_rooms(rooms_text)

        # Property type (from URL or card)
        property_type = "apartment"
        if "/studio/" in url:
            property_type = "studio"

        # Agency
        agency_el = await card.query_selector("[class*='agent'], [class*='agency'], [class*='broker']")
        agency_name = (await agency_el.inner_text()).strip() if agency_el else ""

        return Listing(
            source=Source.PARARIUS,
            source_id=source_id,
            url=url,
            street=street,
            house_number=house_number,
            house_number_addition=addition,
            city=self.city,
            asking_rent=asking_rent,
            surface_area_m2=surface_area,
            num_rooms=num_rooms,
            property_type=property_type,
            agency_name=agency_name,
        )

    async def fetch_listing_details(self, listing: Listing) -> None:
        """Visit the detail page and enrich the listing with additional fields."""
        page = await self._get_page(listing.url)
        try:
            await self._parse_detail_page(page, listing)
        finally:
            await page.close()

    async def _parse_detail_page(self, page, listing: Listing) -> None:
        """Extract detail-page fields into an existing Listing."""
        # Full address with postal code
        address_text = ""
        for sel in ("address", "[class*='address']", "[itemprop='address']"):
            el = await page.query_selector(sel)
            if el:
                address_text = (await el.inner_text()).strip()
                break

        postal = _extract_postal_code(address_text or await page.content())
        if postal:
            listing.postal_code = postal

        # If we didn't get the street from the card, try from detail
        if not listing.street and address_text:
            street, num, add = _parse_address(address_text)
            if street:
                listing.street = street
                listing.house_number = num
                listing.house_number_addition = add

        # Features table (dt/dd pairs)
        dts = await page.query_selector_all("dt")
        dds = await page.query_selector_all("dd")
        for dt, dd in zip(dts, dds):
            key = (await dt.inner_text()).strip().lower()
            val = (await dd.inner_text()).strip()
            if not val:
                continue
            if "energy" in key or "energie" in key:
                listing.energy_label = _parse_energy_label(val)
            elif "bouwjaar" in key or "construction" in key or "built" in key:
                try:
                    listing.construction_year = int(re.search(r"\d{4}", val).group())
                except (AttributeError, ValueError):
                    pass
            elif "interior" in key or "interieur" in key or "furnish" in key:
                listing.interior = val
            elif "neighborhood" in key or "buurt" in key or "wijk" in key:
                listing.neighborhood = val

        # Description
        for sel in ("[class*='description']", "[class*='tekst']", "section[class*='description']"):
            el = await page.query_selector(sel)
            if el:
                listing.description = (await el.inner_text()).strip()
                break

        # Images
        img_els = await page.query_selector_all("img[src*='pararius']")
        images = []
        for img in img_els[:6]:
            src = await img.get_attribute("src") or await img.get_attribute("data-src") or ""
            if src and src not in images:
                images.append(src)
        if images:
            listing.images = images

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        log.debug("pararius: browser closed")
