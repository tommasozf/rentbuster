"""Tests for rentbuster.woz — WOZ value lookup."""

from __future__ import annotations

import pytest

from rentbuster.models import Listing, Source
from rentbuster.woz import WOZ_ESTIMATE_DEFAULT, WOZ_ESTIMATE_PER_M2, estimate_woz, lookup_woz


class TestEstimateWoz:
    def test_amsterdam_higher_than_rotterdam(self):
        ams = estimate_woz("amsterdam", 60)
        rot = estimate_woz("rotterdam", 60)
        assert ams.value > rot.value

    def test_returns_unverified(self):
        result = estimate_woz("amsterdam", 50)
        assert not result.verified
        assert result.source == "estimated"

    def test_unknown_city_uses_default(self):
        result = estimate_woz("groningen", 50)
        assert result.value == 50 * WOZ_ESTIMATE_DEFAULT

    def test_zero_area_uses_fallback(self):
        result = estimate_woz("amsterdam", 0)
        # Should not be zero — fallback to 50m²
        assert result.value > 0


class TestLookupWoz:
    def test_no_api_key_returns_estimate(self):
        listing = Listing(
            source=Source.PARARIUS,
            source_id="x",
            url="http://example.com",
            city="amsterdam",
            surface_area_m2=60,
        )
        result = lookup_woz(listing, api_key=None)
        assert not result.verified
        assert listing.woz_value is not None
        assert listing.woz_verified is False

    def test_mutates_listing(self):
        listing = Listing(
            source=Source.PARARIUS,
            source_id="x",
            url="http://example.com",
            city="rotterdam",
            surface_area_m2=50,
        )
        lookup_woz(listing, api_key=None)
        assert listing.woz_value == 50 * WOZ_ESTIMATE_PER_M2["rotterdam"]
        assert listing.woz_reference_date is not None
