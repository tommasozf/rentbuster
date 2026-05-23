"""WWS (Woningwaarderingsstelsel) points calculator for zelfstandige woonruimte.

Implements the 2024/2025 Dutch social housing points system. Targets meergezinswoningen
(apartments) in Amsterdam.

IMPORTANT: The rent table (POINTS_TO_MAX_RENT_TABLE) is derived via linear interpolation
between known anchor points and MUST be verified against the official Bijlage 3 publication:
https://www.huurcommissie.nl/onderwerpen/wws
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Two-part WOZ formula divisors (Bijlage 1, 2025)
WOZ_DIVISOR_PART1 = 16_954
WOZ_DIVISOR_PART2 = 268
WOZ_MAX_PERCENTAGE = 0.33  # WOZ points cannot exceed 33% of total

# Conservative defaults when detail data is missing (intentionally low = more likely flagged)
DEFAULT_OUTDOOR_POINTS = -5.0  # no outdoor space known
DEFAULT_KITCHEN_POINTS = 4.0  # minimal kitchen
DEFAULT_BATHROOM_POINTS = 3.0  # minimal bathroom
DEFAULT_HEATING_POINTS = 2.0  # basic heating

# Bijlage 3 — rent table anchor points (Huurcommissie 2025).
# Linear interpolation used between these anchors; extrapolated above 187 pts.
# VERIFY these values against the official 2025 publication before use in production.
_RENT_TABLE_ANCHORS: list[tuple[int, float]] = [
    (40, 303.62),
    (50, 342.83),
    (60, 382.04),
    (70, 421.37),
    (80, 460.69),
    (90, 499.90),
    (100, 539.23),
    (110, 578.54),
    (120, 617.86),
    (130, 657.07),
    (140, 718.88),
    (145, 739.55),
    (150, 761.34),
    (155, 785.00),
    (160, 808.06),
    (165, 829.30),
    (170, 849.62),
    (175, 862.68),
    (180, 871.74),
    (185, 876.65),
    (186, 878.16),
    (187, 879.66),
]


def _build_rent_table() -> dict[int, float]:
    """Interpolate between anchor points to build a per-integer-point lookup table."""
    table: dict[int, float] = {}
    anchors = sorted(_RENT_TABLE_ANCHORS)
    for i in range(len(anchors) - 1):
        p0, r0 = anchors[i]
        p1, r1 = anchors[i + 1]
        for pts in range(p0, p1):
            frac = (pts - p0) / (p1 - p0)
            table[pts] = round(r0 + frac * (r1 - r0), 2)
    # Include the final anchor
    table[anchors[-1][0]] = anchors[-1][1]
    return table


_RENT_TABLE = _build_rent_table()
_EXTRAPOLATE_RATE = 5.87  # EUR/point above liberalization threshold


def points_to_max_rent(points: float) -> float:
    """Return the maximum legal monthly rent for a given WWS point total.

    For points >= LIBERALIZATION_THRESHOLD the apartment is free-market, but we still
    return the extrapolated value so callers can reason about it.
    """
    pts_int = int(points)
    if pts_int < 40:
        # Below minimum table entry — use anchor value
        return _RENT_TABLE.get(40, 303.62)
    if pts_int in _RENT_TABLE:
        # Interpolate fractional points between this and next entry
        base = _RENT_TABLE[pts_int]
        if points > pts_int and (pts_int + 1) in _RENT_TABLE:
            frac = points - pts_int
            base += frac * (_RENT_TABLE[pts_int + 1] - base)
        return round(base, 2)
    # Extrapolate above table
    return round(_RENT_TABLE.get(187, 879.66) + (points - 187) * _EXTRAPOLATE_RATE, 2)


# ── Breakdown dataclass ────────────────────────────────────────────────────────


@dataclass
class WWSBreakdown:
    surface_area: float = 0.0
    energy_label: float = 0.0
    woz_uncapped: float = 0.0
    woz_capped: float = 0.0
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
            "outdoor_space": self.outdoor_space,
            "kitchen": self.kitchen,
            "bathroom": self.bathroom,
            "heating": self.heating,
            "total": self.total,
        }


# ── Calculator ─────────────────────────────────────────────────────────────────


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

    # 3. WOZ value — two-part formula with 33% cap
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

    # 33% cap: WOZ points cannot exceed 33% of total
    subtotal_without_woz = (
        bd.surface_area + bd.energy_label + bd.outdoor_space + bd.kitchen + bd.bathroom + bd.heating
    )
    max_woz = (WOZ_MAX_PERCENTAGE / (1 - WOZ_MAX_PERCENTAGE)) * subtotal_without_woz
    bd.woz_capped = round(min(woz_uncapped, max_woz), 2)

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
