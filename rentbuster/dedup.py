"""Address-based deduplication across listing sources."""

from __future__ import annotations

from rentbuster.models import Listing, Source

# Source priority for deduplication (higher index = higher priority).
# Pararius has the richest detail pages and wins over all others.
# Funda and Kamernet are original source listings surfaced via rent-buster.nl.
_SOURCE_PRIORITY: dict[Source, int] = {
    Source.RENTBUSTER_NL: 0,
    Source.KAMERNET: 1,
    Source.FUNDA: 2,
    Source.PARARIUS: 3,
}


def _priority(source: Source) -> int:
    return _SOURCE_PRIORITY.get(source, 0)


def deduplicate(listings: list[Listing]) -> list[Listing]:
    """Group listings by normalised address and merge duplicates.

    When the same address appears across sources, prefer the higher-priority
    listing (Pararius > Funda > Kamernet > rentbuster_nl) and merge
    rent-buster.nl cross-reference data into it where available.
    For same-source duplicates, keep the first occurrence. Listings without a house number
    (Pararius hides it on some ads) are never merged.
    """
    seen: dict[str, Listing] = {}
    result: list[Listing] = []

    for listing in listings:
        if not listing.house_number:
            # Without a house number every "Frans Halsstraat" would collapse into one entry.
            result.append(listing)
            continue
        key = listing.address_key
        if key not in seen:
            seen[key] = listing
            result.append(listing)
        else:
            existing = seen[key]
            incoming_priority = _priority(listing.source)
            existing_priority = _priority(existing.source)

            if incoming_priority > existing_priority:
                # Incoming has higher priority — promote it and carry rb fields
                _merge_rb_fields(listing, existing)
                _fill_missing(listing, existing)
                seen[key] = listing
                result[result.index(existing)] = listing
            elif incoming_priority < existing_priority:
                # Existing has higher priority — enrich it with rb cross-ref data
                _merge_rb_fields(existing, listing)
                _fill_missing(existing, listing)
            else:
                # Same source duplicate — fill any missing fields from the duplicate
                _fill_missing(existing, listing)

    return result


def _merge_rb_fields(target: Listing, rb_source: Listing) -> None:
    """Copy rent-buster.nl cross-reference fields from rb_source into target."""
    if rb_source.rb_points is not None and target.rb_points is None:
        target.rb_points = rb_source.rb_points
    if rb_source.rb_estimated_max_rent is not None and target.rb_estimated_max_rent is None:
        target.rb_estimated_max_rent = rb_source.rb_estimated_max_rent
    if rb_source.rb_savings is not None and target.rb_savings is None:
        target.rb_savings = rb_source.rb_savings
    if rb_source.rb_confidence is not None and target.rb_confidence is None:
        target.rb_confidence = rb_source.rb_confidence


def _fill_missing(target: Listing, source: Listing) -> None:
    """Copy non-empty fields from source into target where target has empty values."""
    for attr in (
        "postal_code",
        "neighborhood",
        "construction_year",
        "energy_label",
        "description",
        "agency_name",
    ):
        if not getattr(target, attr) and getattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    if not target.images and source.images:
        target.images = source.images
