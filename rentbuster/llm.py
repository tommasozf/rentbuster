"""LLM-based feature extraction from listing descriptions.

Uses Gemini Flash to analyze listing text and extract WWS-relevant details
(outdoor space, kitchen, bathroom, heating) that can't be reliably
parsed from structured fields alone.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from rentbuster.models import Listing

log = logging.getLogger(__name__)

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "has_private_outdoor": {
            "type": "BOOLEAN",
            "description": "Whether the listing mentions ANY private outdoor space (balcony, terrace, garden, roof terrace, loggia, patio).",
        },
        "outdoor_area_m2": {
            "type": "INTEGER",
            "nullable": True,
            "description": "Estimated outdoor space in m², if mentioned or inferable. null if not mentioned. When only described qualitatively, estimate conservatively: 'small balcony' = 3, 'balcony' = 5, 'large terrace' = 12, 'garden' = 15.",
        },
        "outdoor_shared": {
            "type": "BOOLEAN",
            "description": "True if the outdoor space is shared (communal garden, shared roof terrace). False if private.",
        },
        "kitchen_quality": {
            "type": "STRING",
            "enum": ["minimal", "standard", "well_equipped", "luxury"],
            "description": "Kitchen quality level. 'minimal' = basic sink and stovetop only or no detail given. 'standard' = mentions oven OR dishwasher. 'well_equipped' = mentions oven AND dishwasher or 3+ appliances. 'luxury' = describes high-end/designer kitchen with many appliances.",
        },
        "kitchen_appliances": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "List of mentioned kitchen appliances/fixtures (e.g. 'dishwasher', 'oven', 'fridge', 'microwave', 'induction_hob', 'extractor').",
        },
        "bathroom_quality": {
            "type": "STRING",
            "enum": ["basic", "standard", "full", "luxury"],
            "description": "Bathroom quality. 'basic' = toilet + shower only or no detail. 'standard' = shower + sink + toilet. 'full' = mentions bathtub OR separate shower + bath. 'luxury' = describes high-end finishes, double sink, rain shower, etc.",
        },
        "has_bathtub": {
            "type": "BOOLEAN",
            "description": "Whether a bathtub/bath is mentioned.",
        },
        "has_second_bathroom": {
            "type": "BOOLEAN",
            "description": "Whether a second toilet or bathroom is mentioned.",
        },
        "heating_type": {
            "type": "STRING",
            "enum": ["central", "district", "individual", "floor", "unknown"],
            "description": "Heating type. 'central' = central heating (CV-ketel). 'district' = stadsverwarming/district heating. 'individual' = per-room heaters. 'floor' = floor heating (vloerverwarming). 'unknown' = not mentioned.",
        },
        "confidence_notes": {
            "type": "STRING",
            "description": "Brief note on confidence: what was explicitly stated vs. inferred.",
        },
        "suitable_for_students": {
            "type": "BOOLEAN",
            "nullable": True,
            "description": "Whether the listing explicitly allows students. null if not mentioned.",
        },
        "suitable_for_sharing": {
            "type": "BOOLEAN",
            "nullable": True,
            "description": "Whether the listing explicitly allows sharing / co-tenants. null if not mentioned.",
        },
        "guarantor_accepted": {
            "type": "BOOLEAN",
            "nullable": True,
            "description": "Whether a guarantor/borg is accepted. null if not mentioned.",
        },
    },
    "required": [
        "has_private_outdoor",
        "outdoor_area_m2",
        "outdoor_shared",
        "kitchen_quality",
        "kitchen_appliances",
        "bathroom_quality",
        "has_bathtub",
        "has_second_bathroom",
        "heating_type",
        "confidence_notes",
        "suitable_for_students",
        "suitable_for_sharing",
        "guarantor_accepted",
    ],
}

_SYSTEM_PROMPT = """\
You extract property features from Dutch rental listing descriptions for the WWS \
(Woningwaarderingsstelsel) housing points system. \
The listing may be in Dutch or English.

IMPORTANT RULES:
- Be CONSERVATIVE. When uncertain, choose lower/fewer points. Under-counting is safer \
than over-counting because it makes the listing more likely to be flagged as overpriced.
- Only report what is explicitly mentioned or strongly implied. Do not guess.
- "Gemeubileerd"/"furnished" does NOT imply kitchen appliances are built-in fixtures.
- A "kitchen" with no further detail = "minimal".
- If outdoor space is mentioned without size, estimate conservatively.
- For tenant suitability fields, only set true/false if EXPLICITLY stated; otherwise null.
- Dutch terms: balkon = balcony, dakterras = roof terrace, tuin = garden, \
terras = terrace, loggia = loggia, vloerverwarming = floor heating, \
CV-ketel = central heating, stadsverwarming = district heating, \
vaatwasser = dishwasher, oven, koelkast = fridge, magnetron = microwave, \
ligbad = bathtub, douche = shower, wastafel = sink, 2e toilet = second toilet, \
studenten = students, gedeeld = shared, garant/borg = guarantor.\
"""


@dataclass
class LLMExtraction:
    has_private_outdoor: bool = False
    outdoor_area_m2: int | None = None
    outdoor_shared: bool = False
    kitchen_quality: str = "minimal"
    kitchen_appliances: list[str] = field(default_factory=list)
    bathroom_quality: str = "basic"
    has_bathtub: bool = False
    has_second_bathroom: bool = False
    heating_type: str = "unknown"
    confidence_notes: str = ""
    suitable_for_students: bool | None = None
    suitable_for_sharing: bool | None = None
    guarantor_accepted: bool | None = None

    @property
    def outdoor_points(self) -> float:
        if not self.has_private_outdoor and not self.outdoor_shared:
            return -5.0
        area = self.outdoor_area_m2 or 0
        if area <= 0:
            return 0.0
        rate = 0.75 if self.outdoor_shared else 2.0
        return area * rate

    @property
    def kitchen_points(self) -> float:
        return {"minimal": 4.0, "standard": 7.0, "well_equipped": 10.0, "luxury": 13.0}.get(
            self.kitchen_quality, 4.0
        )

    @property
    def bathroom_points(self) -> float:
        base = {"basic": 3.0, "standard": 5.0, "full": 8.0, "luxury": 12.0}.get(self.bathroom_quality, 3.0)
        if self.has_bathtub and self.bathroom_quality not in ("full", "luxury"):
            base += 2.0
        if self.has_second_bathroom:
            base += 3.0
        return base

    @property
    def heating_points(self) -> float:
        return {
            "central": 2.0,
            "district": 2.0,
            "individual": 1.0,
            "floor": 3.0,
            "unknown": 2.0,
        }.get(self.heating_type, 2.0)


def _build_user_message(listing: Listing) -> str:
    parts = [f"Address: {listing.street} {listing.house_number}, {listing.city}"]
    if listing.surface_area_m2:
        parts.append(f"Size: {listing.surface_area_m2} m²")
    if listing.num_rooms:
        parts.append(f"Rooms: {listing.num_rooms}")
    if listing.interior:
        parts.append(f"Interior: {listing.interior}")
    if listing.property_type:
        parts.append(f"Type: {listing.property_type}")
    parts.append("")
    parts.append("=== LISTING DESCRIPTION ===")
    desc = (listing.description or "").strip()
    if desc:
        parts.append(desc[:3000])
    else:
        parts.append("(no description available)")
    return "\n".join(parts)


def _parse_response(data: dict) -> LLMExtraction:
    def _nullable_bool(key: str) -> bool | None:
        v = data.get(key)
        return None if v is None else bool(v)

    return LLMExtraction(
        has_private_outdoor=bool(data.get("has_private_outdoor", False)),
        outdoor_area_m2=data.get("outdoor_area_m2"),
        outdoor_shared=bool(data.get("outdoor_shared", False)),
        kitchen_quality=data.get("kitchen_quality", "minimal"),
        kitchen_appliances=data.get("kitchen_appliances", []),
        bathroom_quality=data.get("bathroom_quality", "basic"),
        has_bathtub=bool(data.get("has_bathtub", False)),
        has_second_bathroom=bool(data.get("has_second_bathroom", False)),
        heating_type=data.get("heating_type", "unknown"),
        confidence_notes=data.get("confidence_notes", ""),
        suitable_for_students=_nullable_bool("suitable_for_students"),
        suitable_for_sharing=_nullable_bool("suitable_for_sharing"),
        guarantor_accepted=_nullable_bool("guarantor_accepted"),
    )


_DEFAULT_MODEL = "gemini-2.5-flash-lite"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 30


def _call_gemini(client, model: str, user_msg: str) -> dict | None:
    from google.genai import types

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_msg,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                    temperature=0.0,
                ),
            )
            return json.loads(response.text)
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                wait = _RETRY_BASE_DELAY * (2**attempt)
                log.warning("llm: rate limited, waiting %ds (attempt %d/%d)", wait, attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
                continue
            raise
    return None


def extract_listing_features(
    listing: Listing,
    api_key: str,
    model: str = _DEFAULT_MODEL,
) -> LLMExtraction | None:
    if not listing.description and not listing.interior:
        log.debug("llm: skipping %s %s — no description", listing.street, listing.house_number)
        return None

    try:
        from google import genai
    except ImportError:
        log.error("llm: google-genai package not installed — run: uv add google-genai")
        return None

    client = genai.Client(api_key=api_key)
    user_msg = _build_user_message(listing)

    try:
        data = _call_gemini(client, model, user_msg)
        if data:
            return _parse_response(data)
    except Exception as exc:
        log.warning("llm: API call failed for %s %s: %s", listing.street, listing.house_number, exc)
    return None


def extract_batch(
    listings: list[Listing],
    api_key: str,
    model: str = _DEFAULT_MODEL,
) -> dict[str, LLMExtraction]:
    results: dict[str, LLMExtraction] = {}
    candidates = [ls for ls in listings if ls.description or ls.interior]
    if not candidates:
        return results

    log.info("llm: extracting features for %d/%d listings with descriptions", len(candidates), len(listings))
    for i, listing in enumerate(candidates):
        key = f"{listing.source.value}:{listing.source_id}"
        extraction = extract_listing_features(listing, api_key, model)
        if extraction:
            results[key] = extraction
            log.debug(
                "llm: %d/%d %s %s → outdoor=%.0f kitchen=%.0f bathroom=%.0f heating=%.0f",
                i + 1,
                len(candidates),
                listing.street,
                listing.house_number,
                extraction.outdoor_points,
                extraction.kitchen_points,
                extraction.bathroom_points,
                extraction.heating_points,
            )
        if i < len(candidates) - 1:
            time.sleep(0.2)
    log.info("llm: extracted features for %d/%d listings", len(results), len(candidates))
    return results
