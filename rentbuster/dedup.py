"""Address-based deduplication across listing sources."""

from __future__ import annotations

from rentbuster.models import Listing, Source, normalize_address


def deduplicate(listings: list[Listing]) -> list[Listing]:
    """Group listings by normalised address and merge duplicates.

    When the same address appears across sources, prefer the Pararius listing
    (richer detail) and merge rent-buster.nl cross-reference data into it.
    For same-source duplicates, keep the first occurrence.
    """
    seen: dict[str, Listing] = {}
    result: list[Listing] = []

    for listing in listings:
        key = listing.address_key
        if key not in seen:
            seen[key] = listing
            result.append(listing)
        else:
            existing = seen[key]
            # If incoming is Pararius and existing is not, swap (Pararius has more detail)
            if listing.source == Source.PARARIUS and existing.source != Source.PARARIUS:
                # Merge rentbuster_nl fields into the incoming Pararius listing
                _merge_rb_fields(listing, existing)
                seen[key] = listing
                result[result.index(existing)] = listing
            elif listing.source == Source.RENTBUSTER_NL and existing.source == Source.PARARIUS:
                # Enrich existing Pararius listing with rentbuster_nl data
                _merge_rb_fields(existing, listing)
            else:
                # Same source duplicate — fill any missing fields from the duplicate
                _fill_missing(existing, listing)

    return result


def _merge_rb_fields(target: Listing, rb_source: Listing) -> None:
    """Copy rent-buster.nl cross-reference fields from rb_source into target."""
    if rb_source.rb_estimated_max_rent is not None and target.rb_estimated_max_rent is None:
        target.rb_estimated_max_rent = rb_source.rb_estimated_max_rent
    if rb_source.rb_savings is not None and target.rb_savings is None:
        target.rb_savings = rb_source.rb_savings
    if rb_source.rb_confidence is not None and target.rb_confidence is None:
        target.rb_confidence = rb_source.rb_confidence


def _fill_missing(target: Listing, source: Listing) -> None:
    """Copy non-empty fields from source into target where target has empty values."""
    for attr in (
        "postal_code", "neighborhood", "construction_year",
        "energy_label", "description", "agency_name",
    ):
        if not getattr(target, attr) and getattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    if not target.images and source.images:
        target.images = source.images
