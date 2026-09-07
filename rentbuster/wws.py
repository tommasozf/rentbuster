"""WWS (Woningwaarderingsstelsel) points calculator for zelfstandige woonruimte.

Implements the Dutch WWS points system (Huurcommissie policy book, 1 January 2026). Targets meergezinswoningen
(apartments) in Amsterdam.

The points-to-rent table (Bijlage 3) lives in rentbuster/data/huurprijsgrenzen.json and is
republished by the Huurcommissie every 1 January.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rentbuster.models import ConfidenceLevel, Listing
from rentbuster.woz import WOZ_ESTIMATE_DEFAULT, WOZ_ESTIMATE_PER_M2

if TYPE_CHECKING:
    from rentbuster.llm import LLMExtraction

# ── Constants ──────────────────────────────────────────────────────────────────

LIBERALIZATION_THRESHOLD = 187  # points; above this the apartment is free-market

# Energy label points for meergezinswoningen (apartments), Bijlage 1 table 2025
ENERGY_POINTS_APARTMENT: dict[str, float] = {
    "A++++": 58.0,
    "A+++": 53.0,
    "A++": 48.0,
    "A+": 43.0,
    "A": 37.0,
    "B": 30.0,
    "C": 15.0,
    "D": 11.0,
    "E": -4.0,
    "F": -9.0,
    "G": -15.0,
}

# WOZ points (rubriek 11), amounts valid per 1 January 2026 (waardepeildatum 1 January 2025)
WOZ_DIVISOR_PART1 = 16_954  # 1 point per €16,954 of WOZ value
WOZ_DIVISOR_PART2 = 268  # 1 point per €268 of WOZ value per m²
WOZ_MINIMUM_VALUE = 85_806  # WOZ values below this are raised to it
# The "cap op de WOZ": WOZ may make up at most 33% of the total, but only for homes that
# reach 187 points without the cap. If the cap then drags such a home under 187, it is
# valued at 186 points. Small new-builds (< 40 m², Amsterdam/Utrecht, delivered 2018-2022)
# are exempt from that floor.
WOZ_MAX_PERCENTAGE = 0.33
WOZ_CAP_FLOOR_POINTS = 186
_NEWBUILD_EXCEPTION_CITIES = {"amsterdam", "utrecht"}

# Conservative defaults when detail data is missing (intentionally low = more likely flagged)
DEFAULT_OUTDOOR_POINTS = -5.0  # no outdoor space known
DEFAULT_KITCHEN_POINTS = 4.0  # minimal kitchen
DEFAULT_BATHROOM_POINTS = 3.0  # minimal bathroom
DEFAULT_HEATING_POINTS = 2.0  # basic heating

# Bijlage 3 — maximale huurprijsgrenzen (Huurcommissie), loaded from rentbuster/data/huurprijsgrenzen.json.
# The table is republished every 1 January; see the JSON file for the source URL and valid_from date.
_TABLE_PATH = Path(__file__).resolve().parent / "data" / "huurprijsgrenzen.json"


def _load_rent_table() -> tuple[dict[int, float], str]:
    with _TABLE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    table = {int(k): float(v) for k, v in data["max_rent_by_points"].items()}
    return table, data["valid_from"]


_RENT_TABLE, RENT_TABLE_VALID_FROM = _load_rent_table()
_RENT_TABLE_MIN = min(_RENT_TABLE)
_RENT_TABLE_MAX = max(_RENT_TABLE)
# Per-point step used above the last row of the table (only free-market homes get there).
_RENT_TABLE_STEP = _RENT_TABLE[_RENT_TABLE_MAX] - _RENT_TABLE[_RENT_TABLE_MAX - 1]


def round_points(points: float) -> int:
    """Round a WWS total to whole points the way the Huurcommissie does (half rounds up)."""
    return int(math.floor(points + 0.5))


def points_to_max_rent(points: float) -> float:
    """Return the maximum legal monthly rent for a WWS point total.

    Points are rounded to whole points first, then looked up in Bijlage 3. Below the first
    row the first row applies; above the last row the value is extrapolated (those homes are
    free-market anyway, the number is only there for ranking).
    """
    pts = round_points(points)
    if pts <= _RENT_TABLE_MIN:
        return _RENT_TABLE[_RENT_TABLE_MIN]
    if pts in _RENT_TABLE:
        return _RENT_TABLE[pts]
    return round(_RENT_TABLE[_RENT_TABLE_MAX] + (pts - _RENT_TABLE_MAX) * _RENT_TABLE_STEP, 2)


# ── Breakdown dataclass ────────────────────────────────────────────────────────


@dataclass
class WWSBreakdown:
    surface_area: float = 0.0
    energy_label: float = 0.0
    woz_uncapped: float = 0.0
    woz_capped: float = 0.0
    woz_cap_floor: float = 0.0  # points added to reach 186 when the cap pushed a home under 187
    outdoor_space: float = 0.0
    kitchen: float = 0.0
    bathroom: float = 0.0
    heating: float = 0.0
    flags: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return (
            self.surface_area
            + self.energy_label
            + self.woz_capped
            + self.woz_cap_floor
            + self.outdoor_space
            + self.kitchen
            + self.bathroom
            + self.heating
        )

    def to_dict(self) -> dict:
        return {
            "surface_area": self.surface_area,
            "energy_label": self.energy_label,
            "woz_uncapped": self.woz_uncapped,
            "woz_capped": self.woz_capped,
            "woz_cap_floor": self.woz_cap_floor,
            "outdoor_space": self.outdoor_space,
            "kitchen": self.kitchen,
            "bathroom": self.bathroom,
            "heating": self.heating,
            "total": self.total,
        }


# ── Calculator ─────────────────────────────────────────────────────────────────


def _is_small_newbuild_exception(listing: Listing) -> bool:
    """Small new-build (< 40 m², delivered 2018-2022, COROP Amsterdam/Utrecht): no 186 floor."""
    return (
        0 < (listing.surface_area_m2 or 0) < 40
        and listing.construction_year is not None
        and 2018 <= listing.construction_year <= 2022
        and (listing.city or "").lower().strip() in _NEWBUILD_EXCEPTION_CITIES
    )


def calculate_wws(listing: Listing, llm_extraction: LLMExtraction | None = None) -> WWSBreakdown:
    """Calculate WWS points for a listing; mutates listing in place with results.

    If llm_extraction is provided, its values replace the conservative defaults
    for outdoor space, kitchen, bathroom, and heating.
    """
    bd = WWSBreakdown()
    flags: list[str] = []

    # 1. Surface area — 1 point per m²
    if listing.surface_area_m2 and listing.surface_area_m2 > 0:
        bd.surface_area = float(listing.surface_area_m2)
    else:
        bd.surface_area = 0.0
        flags.append("surface_area_unknown")

    # 2. Energy label
    if listing.energy_label:
        bd.energy_label = ENERGY_POINTS_APARTMENT.get(listing.energy_label.value, 11.0)
    else:
        bd.energy_label = ENERGY_POINTS_APARTMENT["D"]
        flags.append("energy_label_unknown_assumed_D")

    # 3. WOZ value — two-part formula (the cap is applied after the other components are known)
    city_key = (listing.city or "").lower().strip()
    woz_source = getattr(listing, "_woz_source", None)
    if listing.woz_value and listing.woz_value > 0:
        woz = listing.woz_value
        if not listing.woz_verified:
            flags.append("woz_estimated")
        elif woz_source == "kadaster_sibling":
            flags.append("woz_sibling")
    else:
        m2 = listing.surface_area_m2 or 50
        per_m2 = WOZ_ESTIMATE_PER_M2.get(city_key, WOZ_ESTIMATE_DEFAULT)
        woz = m2 * per_m2
        flags.append("woz_estimated_conservative")

    if woz < WOZ_MINIMUM_VALUE:
        woz = WOZ_MINIMUM_VALUE
        flags.append("woz_minimum_applied")

    m2 = listing.surface_area_m2 or 50
    part_i = woz / WOZ_DIVISOR_PART1
    part_ii = (woz / m2) / WOZ_DIVISOR_PART2
    woz_uncapped = part_i + part_ii
    bd.woz_uncapped = round(woz_uncapped, 2)

    # 4-7. Outdoor, kitchen, bathroom, heating — use LLM values or defaults
    if llm_extraction:
        bd.outdoor_space = llm_extraction.outdoor_points
        bd.kitchen = llm_extraction.kitchen_points
        bd.bathroom = llm_extraction.bathroom_points
        bd.heating = llm_extraction.heating_points
        flags.append("llm_extracted")
    else:
        bd.outdoor_space = DEFAULT_OUTDOOR_POINTS
        flags.append("outdoor_space_assumed_none")
        bd.kitchen = DEFAULT_KITCHEN_POINTS
        flags.append("kitchen_assumed_minimal")
        bd.bathroom = DEFAULT_BATHROOM_POINTS
        flags.append("bathroom_assumed_minimal")
        bd.heating = DEFAULT_HEATING_POINTS
        flags.append("heating_assumed_basic")

    # WOZ cap: only for homes that reach 187 points without it. Capped WOZ points are rounded
    # down to whole points. If the cap drags the home under 187 it is valued at 186 points,
    # unless it is a small 2018-2022 new-build in Amsterdam/Utrecht.
    subtotal_without_woz = (
        bd.surface_area + bd.energy_label + bd.outdoor_space + bd.kitchen + bd.bathroom + bd.heating
    )
    uncapped_total = subtotal_without_woz + woz_uncapped
    bd.woz_capped = bd.woz_uncapped
    if uncapped_total >= LIBERALIZATION_THRESHOLD:
        max_woz = (WOZ_MAX_PERCENTAGE / (1 - WOZ_MAX_PERCENTAGE)) * subtotal_without_woz
        if woz_uncapped > max_woz:
            bd.woz_capped = float(math.floor(max_woz))
            flags.append("woz_capped")
            capped_total = subtotal_without_woz + bd.woz_capped
            if capped_total < LIBERALIZATION_THRESHOLD:
                if _is_small_newbuild_exception(listing):
                    flags.append("woz_cap_newbuild_exception")
                else:
                    bd.woz_cap_floor = WOZ_CAP_FLOOR_POINTS - capped_total
                    flags.append("woz_cap_floor_186")

    bd.flags = flags

    total = bd.total
    max_rent = points_to_max_rent(total)
    is_regulated = total < LIBERALIZATION_THRESHOLD
    is_bustable = is_regulated and listing.asking_rent > max_rent
    savings = listing.asking_rent - max_rent if is_bustable else 0.0

    # Confidence: based on data completeness
    critical_missing = sum(
        [
            "woz_estimated_conservative" in flags,
            "surface_area_unknown" in flags,
        ]
    )
    if critical_missing == 0 and len(flags) <= 3:
        confidence = ConfidenceLevel.HIGH
    elif critical_missing == 0:
        confidence = ConfidenceLevel.MEDIUM
    elif critical_missing == 1:
        confidence = ConfidenceLevel.LOW
    else:
        confidence = ConfidenceLevel.VERY_LOW

    # Confidence multipliers for bust_score
    _CONFIDENCE_MULT = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.5, "VERY_LOW": 0.3}
    confidence_mult = _CONFIDENCE_MULT.get(confidence.value, 0.5)
    if listing.woz_verified and "woz_sibling" in flags:
        woz_mult = 0.9  # sibling unit — slightly less certain than exact match
    elif listing.woz_verified:
        woz_mult = 1.0
    else:
        woz_mult = 0.7
    bust_score = (savings or 0) * confidence_mult * woz_mult

    # Mutate listing
    listing.wws_points = round(total, 2)
    listing.wws_max_rent = max_rent
    listing.wws_savings = round(savings, 2) if is_bustable else None
    listing.wws_is_bustable = is_bustable
    listing.wws_confidence = confidence
    listing.wws_breakdown = bd.to_dict()
    listing.wws_flags = flags
    listing.bust_score = round(bust_score, 2)

    return bd
