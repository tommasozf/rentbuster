"""Core data model shared by all rentbuster modules."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rentbuster.address import normalize_address  # noqa: F401  (re-exported; used by address_key)


class EnergyLabel(str, Enum):
    A_PLUS_PLUS_PLUS_PLUS = "A++++"
    A_PLUS_PLUS_PLUS = "A+++"
    A_PLUS_PLUS = "A++"
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"

    @classmethod
    def from_string(cls, s: str | None) -> EnergyLabel | None:
        if not s:
            return None
        s = s.strip().upper()
        for member in cls:
            if member.value.upper() == s:
                return member
        return None


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


class Source(str, Enum):
    PARARIUS = "pararius"
    RENTBUSTER_NL = "rentbuster_nl"
    FUNDA = "funda"
    KAMERNET = "kamernet"


@dataclass
class Listing:
    # Identity
    source: Source
    source_id: str
    url: str

    # Address
    street: str = ""
    house_number: str = ""
    house_number_addition: str = ""
    postal_code: str = ""
    city: str = ""
    neighborhood: str = ""

    # Property
    asking_rent: int = 0  # EUR/month
    surface_area_m2: int = 0
    num_rooms: int = 0
    energy_label: EnergyLabel | None = None
    construction_year: int | None = None
    property_type: str = ""
    interior: str = ""
    description: str = ""
    images: list[str] = field(default_factory=list)
    available_from: str = ""
    agency_name: str = ""

    # WWS results (filled by wws.py)
    wws_points: float | None = None
    wws_max_rent: float | None = None
    wws_savings: float | None = None
    wws_is_bustable: bool = False
    wws_confidence: ConfidenceLevel | None = None
    wws_breakdown: dict[str, Any] = field(default_factory=dict)
    wws_flags: list[str] = field(default_factory=list)

    # WOZ data (filled by woz.py)
    woz_value: int | None = None
    woz_reference_date: str | None = None
    woz_verified: bool = False

    # Tenant suitability (from detail page or LLM)
    suitable_for_students: bool | None = None
    suitable_for_sharing: bool | None = None
    guarantor_accepted: bool | None = None

    # Rent-buster.nl cross-ref
    rb_estimated_max_rent: float | None = None
    rb_savings: float | None = None
    rb_confidence: str | None = None

    # Bust score (computed by wws.py)
    bust_score: float = 0.0

    @property
    def address_key(self) -> str:
        return normalize_address(self.street, self.house_number, self.house_number_addition, self.postal_code)

    def to_db_params(self) -> dict:
        return {
            "source": self.source.value,
            "source_id": self.source_id,
            "url": self.url,
            "street": self.street,
            "house_number": self.house_number,
            "house_number_addition": self.house_number_addition,
            "postal_code": self.postal_code,
            "city": self.city,
            "neighborhood": self.neighborhood,
            "address_key": self.address_key,
            "asking_rent": self.asking_rent,
            "surface_area_m2": self.surface_area_m2,
            "num_rooms": self.num_rooms,
            "energy_label": self.energy_label.value if self.energy_label else None,
            "construction_year": self.construction_year,
            "property_type": self.property_type,
            "interior": self.interior,
            "description": self.description,
            "images": json.dumps(self.images),
            "available_from": self.available_from or None,
            "agency_name": self.agency_name,
            "wws_points": self.wws_points,
            "wws_max_rent": self.wws_max_rent,
            "wws_savings": self.wws_savings,
            "wws_is_bustable": self.wws_is_bustable,
            "wws_confidence": self.wws_confidence.value if self.wws_confidence else None,
            "wws_breakdown": json.dumps(self.wws_breakdown),
            "wws_flags": json.dumps(self.wws_flags),
            "woz_value": self.woz_value,
            "woz_reference_date": self.woz_reference_date,
            "woz_verified": self.woz_verified,
            "suitable_for_students": self.suitable_for_students,
            "suitable_for_sharing": self.suitable_for_sharing,
            "guarantor_accepted": self.guarantor_accepted,
            "rb_estimated_max_rent": self.rb_estimated_max_rent,
            "rb_savings": self.rb_savings,
            "rb_confidence": self.rb_confidence,
            "bust_score": self.bust_score,
        }
