"""Tests for rentbuster.dedup — address deduplication."""

from __future__ import annotations

import pytest

from rentbuster.dedup import deduplicate
from rentbuster.models import EnergyLabel, Listing, Source


def _listing(source: Source, source_id: str, street: str, num: str, add: str = "", **kw) -> Listing:
    return Listing(
        source=source,
        source_id=source_id,
        url=f"http://example.com/{source_id}",
        street=street,
        house_number=num,
        house_number_addition=add,
        **kw,
    )


class TestDeduplicate:
    def test_no_duplicates_unchanged(self):
        listings = [
            _listing(Source.PARARIUS, "1", "Keizersgracht", "10"),
            _listing(Source.PARARIUS, "2", "Prinsengracht", "20"),
        ]
        result = deduplicate(listings)
        assert len(result) == 2

    def test_same_address_kept_once(self):
        listings = [
            _listing(Source.PARARIUS, "1", "Keizersgracht", "10"),
            _listing(Source.RENTBUSTER_NL, "2", "Keizersgracht", "10"),
        ]
        result = deduplicate(listings)
        assert len(result) == 1

    def test_pararius_preferred_over_rentbuster_nl(self):
        rb = _listing(Source.RENTBUSTER_NL, "rb-1", "Keizersgracht", "10",
                      rb_estimated_max_rent=800.0, rb_savings=200.0, rb_confidence="high")
        par = _listing(Source.PARARIUS, "par-1", "Keizersgracht", "10")
        result = deduplicate([rb, par])
        assert len(result) == 1
        assert result[0].source == Source.PARARIUS
        # rb fields should be merged into the pararius listing
        assert result[0].rb_estimated_max_rent == 800.0

    def test_rentbuster_nl_data_merged_into_pararius(self):
        par = _listing(Source.PARARIUS, "par-1", "Keizersgracht", "10")
        rb = _listing(Source.RENTBUSTER_NL, "rb-1", "Keizersgracht", "10",
                      rb_estimated_max_rent=750.0, rb_savings=100.0)
        result = deduplicate([par, rb])
        assert len(result) == 1
        assert result[0].source == Source.PARARIUS
        assert result[0].rb_estimated_max_rent == 750.0

    def test_case_insensitive_address_matching(self):
        listings = [
            _listing(Source.PARARIUS, "1", "Keizersgracht", "10"),
            _listing(Source.RENTBUSTER_NL, "2", "keizersgracht", "10"),
        ]
        result = deduplicate(listings)
        assert len(result) == 1

    def test_different_house_numbers_not_deduped(self):
        listings = [
            _listing(Source.PARARIUS, "1", "Keizersgracht", "10"),
            _listing(Source.PARARIUS, "2", "Keizersgracht", "11"),
        ]
        result = deduplicate(listings)
        assert len(result) == 2

    def test_fill_missing_energy_label(self):
        par = _listing(Source.PARARIUS, "1", "Keizersgracht", "10")
        par2 = _listing(Source.PARARIUS, "2", "Keizersgracht", "10",
                        energy_label=EnergyLabel.B)
        result = deduplicate([par, par2])
        assert len(result) == 1
        assert result[0].energy_label == EnergyLabel.B
