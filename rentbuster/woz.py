"""WOZ value lookup — Kadaster API with conservative estimate fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

from rentbuster.models import Listing

log = logging.getLogger(__name__)

_KADASTER_URL = "https://api.kadaster.nl/lvwoz/wozwaardeloket-api/v1/wozwaarde"

# Conservative WOZ estimates per m² when Kadaster lookup fails, by city (intentionally low)
WOZ_ESTIMATE_PER_M2: dict[str, int] = {
    "amsterdam": 3000,
    "rotterdam": 2000,
    "den haag": 2200,
    "the hague": 2200,
    "utrecht": 2500,
}
WOZ_ESTIMATE_DEFAULT = 2000


@dataclass
class WOZResult:
    value: int  # EUR
    reference_date: str | None  # ISO date string
    verified: bool
    source: str  # "kadaster" or "estimated"


def lookup_woz_kadaster(
    postal_code: str,
    house_number: str,
    addition: str,
    api_key: str,
) -> WOZResult | None:
    """Query the Kadaster WOZ API. Returns None on any failure.

    NOTE: Field names in the response are educated guesses — verify against
    the actual Kadaster API documentation and test with a real key.
    """
    params: dict = {"postcode": postal_code.replace(" ", ""), "huisnummer": house_number}
    if addition:
        params["huisnummertoevoeging"] = addition

    try:
        resp = requests.get(
            _KADASTER_URL,
            params=params,
            headers={"X-Api-Key": api_key},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.warning("kadaster api request failed: %s", exc)
        return None

    if not resp.ok:
        log.warning("kadaster api returned %s for %s %s", resp.status_code, postal_code, house_number)
        return None

    try:
        data = resp.json()
    except Exception:
        log.warning("kadaster api returned non-json response")
        return None

    # Try several known/possible response shapes
    waarden = (
        data.get("wozWaarden")
        or data.get("waarden")
        or data.get("wozwaarden")
        or []
    )
    if not waarden:
        log.debug("kadaster: no woz values in response for %s %s", postal_code, house_number)
        return None

    # Take most recent by peildatum
    def _date_key(w: dict) -> str:
        return w.get("peildatum") or w.get("waardepeildatum") or ""

    most_recent = max(waarden, key=_date_key)
    value = (
        most_recent.get("vastgesteldeWaarde")
        or most_recent.get("wozwaarde")
        or most_recent.get("waarde")
    )
    if not value:
        return None

    reference_date = most_recent.get("peildatum") or most_recent.get("waardepeildatum")
    return WOZResult(
        value=int(value),
        reference_date=reference_date,
        verified=True,
        source="kadaster",
    )


def estimate_woz(city: str, surface_area_m2: int) -> WOZResult:
    """Return a conservative WOZ estimate when Kadaster is unavailable."""
    city_key = (city or "").lower().strip()
    per_m2 = WOZ_ESTIMATE_PER_M2.get(city_key, WOZ_ESTIMATE_DEFAULT)
    m2 = surface_area_m2 or 50
    return WOZResult(
        value=m2 * per_m2,
        reference_date=str(date.today()),
        verified=False,
        source="estimated",
    )


def lookup_woz(listing: Listing, api_key: str | None) -> WOZResult:
    """Resolve WOZ value for a listing; mutates listing WOZ fields.

    Tries Kadaster API first (if api_key and address available), falls back to estimate.
    """
    result: WOZResult | None = None

    if api_key and listing.postal_code and listing.house_number:
        result = lookup_woz_kadaster(
            postal_code=listing.postal_code,
            house_number=listing.house_number,
            addition=listing.house_number_addition,
            api_key=api_key,
        )

    if result is None:
        result = estimate_woz(listing.city, listing.surface_area_m2)

    listing.woz_value = result.value
    listing.woz_reference_date = result.reference_date
    listing.woz_verified = result.verified
    return result
