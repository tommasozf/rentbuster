"""WOZ value lookup via public APIs.

Two-step lookup (no API key required):
  1. PDOK locatieserver → nummeraanduiding_id (BAG address ID)
  2. Kadaster LV-WOZ API → WOZ value for that address

Both APIs are public, free, and require no registration.
Verified endpoints 2026-05-22 from wozwaardeloket.nl/assets/endpoints.json.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

from rentbuster.models import Listing

log = logging.getLogger(__name__)

_PDOK_URL = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
_WOZ_URL = "https://api.kadaster.nl/lvwoz/wozwaardeloket-api/v1/wozwaarde/nummeraanduiding"

# Conservative WOZ estimates per m² when lookup fails, by city.
# Based on 2025/2026 municipal WOZ averages — still conservative vs. market.
WOZ_ESTIMATE_PER_M2: dict[str, int] = {
    "amsterdam": 6000,
    "rotterdam": 3500,
    "den haag": 3800,
    "the hague": 3800,
    "utrecht": 4500,
}
WOZ_ESTIMATE_DEFAULT = 3500


@dataclass
class WOZResult:
    value: int  # EUR
    reference_date: str | None  # ISO date string (peildatum)
    verified: bool
    source: str  # "kadaster" or "estimated"


def _pdok_search(query: str, session: requests.Session) -> str | None:
    """Search PDOK locatieserver and return the nummeraanduiding_id of the top result."""
    try:
        resp = session.get(
            _PDOK_URL,
            params={"q": query, "fq": "type:adres", "rows": "1"},
            headers={"Accept": "application/json"},
            timeout=8,
        )
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
        if docs:
            return str(docs[0].get("nummeraanduiding_id") or "")
    except Exception as exc:
        log.debug("pdok lookup failed for %r: %s", query, exc)
    return None


def _pdok_nummeraanduiding(
    postal_code: str,
    house_number: str,
    addition: str,
    session: requests.Session,
    street: str = "",
    city: str = "",
) -> str | None:
    """Resolve a Dutch address to a BAG nummeraanduiding_id via PDOK locatieserver."""
    # Try postal code + house number first (most precise)
    if postal_code:
        q = f"{postal_code.replace(' ', '')} {house_number}"
        if addition:
            q += f" {addition}"
        nid = _pdok_search(q, session)
        if nid:
            return nid

    # Fallback: street + house number + city (when postal code is missing)
    if street and city:
        q = f"{street} {house_number}"
        if addition:
            q += f" {addition}"
        q += f" {city}"
        nid = _pdok_search(q, session)
        if nid:
            log.debug("pdok: resolved via street+city for %s %s %s", street, house_number, city)
            return nid

    return None


def _fetch_woz_for_nid(nid: str, session: requests.Session) -> WOZResult | None:
    """Fetch WOZ value from Kadaster for a given nummeraanduiding ID."""
    nid_padded = nid.zfill(16)
    try:
        resp = session.get(
            f"{_WOZ_URL}/{nid_padded}",
            headers={
                "Accept": "application/json",
                "Origin": "https://www.wozwaardeloket.nl",
                "Referer": "https://www.wozwaardeloket.nl/",
            },
            timeout=10,
        )
        if not resp.ok:
            log.debug("woz: kadaster returned %s for %s", resp.status_code, nid_padded)
            return None
        data = resp.json()
    except Exception as exc:
        log.warning("woz: kadaster request failed: %s", exc)
        return None

    waarden = data.get("wozWaarden") or []
    if not waarden:
        log.debug("woz: no wozWaarden in response for nid %s", nid_padded)
        return None

    most_recent = max(waarden, key=lambda w: w.get("peildatum") or "")
    value = most_recent.get("vastgesteldeWaarde")
    if not value:
        return None

    return WOZResult(
        value=int(value),
        reference_date=most_recent.get("peildatum"),
        verified=True,
        source="kadaster",
    )


def lookup_woz_kadaster(
    postal_code: str,
    house_number: str,
    addition: str,
    session: requests.Session | None = None,
    street: str = "",
    city: str = "",
) -> WOZResult | None:
    """Lookup WOZ value via PDOK geocoding + Kadaster LV-WOZ API. No API key needed."""
    _session = session or requests.Session()

    nid = _pdok_nummeraanduiding(
        postal_code, house_number, addition, _session, street=street, city=city
    )
    if nid:
        result = _fetch_woz_for_nid(nid, _session)
        if result:
            return result

    # If exact match failed and there's an addition, retry without it (sibling unit)
    if addition:
        log.debug("woz: retrying without addition for %s %s", postal_code, house_number)
        nid_sibling = _pdok_nummeraanduiding(
            postal_code, house_number, "", _session, street=street, city=city
        )
        if nid_sibling and nid_sibling != (nid or ""):
            result = _fetch_woz_for_nid(nid_sibling, _session)
            if result:
                log.debug("woz: sibling match for %s %s", postal_code, house_number)
                return WOZResult(
                    value=result.value,
                    reference_date=result.reference_date,
                    verified=True,
                    source="kadaster_sibling",
                )

    log.debug("woz: no nummeraanduiding for %s %s %s", postal_code, house_number, addition)
    return None


def estimate_woz(city: str, surface_area_m2: int) -> WOZResult:
    """Conservative WOZ estimate when Kadaster lookup is unavailable."""
    city_key = (city or "").lower().strip()
    per_m2 = WOZ_ESTIMATE_PER_M2.get(city_key, WOZ_ESTIMATE_DEFAULT)
    m2 = surface_area_m2 or 50
    return WOZResult(
        value=m2 * per_m2,
        reference_date=str(date.today()),
        verified=False,
        source="estimated",
    )


_shared_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _shared_session
    if _shared_session is None:
        _shared_session = requests.Session()
    return _shared_session


def lookup_woz(listing: Listing) -> WOZResult:
    """Resolve WOZ value for a listing; mutates listing WOZ fields.

    If listing already has a woz_value (e.g. from rent-buster.nl), skips lookup.
    Falls back to per-m² estimate if Kadaster lookup fails.
    """
    if listing.woz_value and listing.woz_verified:
        return WOZResult(
            value=listing.woz_value,
            reference_date=listing.woz_reference_date,
            verified=True,
            source="existing",
        )

    result: WOZResult | None = None

    if listing.house_number and (listing.postal_code or (listing.street and listing.city)):
        result = lookup_woz_kadaster(
            postal_code=listing.postal_code,
            house_number=listing.house_number,
            addition=listing.house_number_addition,
            session=_get_session(),
            street=listing.street,
            city=listing.city,
        )

    if result is None:
        result = estimate_woz(listing.city, listing.surface_area_m2)

    listing.woz_value = result.value
    listing.woz_reference_date = result.reference_date
    listing.woz_verified = result.verified
    # Store source for downstream use (wws.py reads _woz_source)
    listing._woz_source = result.source  # type: ignore[attr-defined]
    return result
