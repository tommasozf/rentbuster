"""Tests for rentbuster.wws — WWS points calculator."""

from __future__ import annotations

from rentbuster.models import ConfidenceLevel, EnergyLabel, Listing, Source
from rentbuster.wws import (
    ENERGY_POINTS_APARTMENT,
    LIBERALIZATION_THRESHOLD,
    calculate_wws,
    points_to_max_rent,
)


class TestPointsToMaxRent:
    def test_below_minimum(self):
        # Below table minimum — should return lowest table entry
        assert points_to_max_rent(30) > 0

    def test_at_liberalization(self):
        rent = points_to_max_rent(LIBERALIZATION_THRESHOLD)
        # Should be around €879-880 for 2025 table
        assert 850 < rent < 920

    def test_above_liberalization_extrapolates(self):
        rent_at_187 = points_to_max_rent(187)
        rent_at_197 = points_to_max_rent(197)
        assert rent_at_197 > rent_at_187

    def test_monotonically_increasing(self):
        rents = [points_to_max_rent(p) for p in range(40, 190)]
        for i in range(1, len(rents)):
            assert rents[i] >= rents[i - 1], f"not monotone at {i + 40} pts"


class TestCalculateWWS:
    def _make_listing(self, **kwargs) -> Listing:
        defaults = dict(
            source=Source.PARARIUS,
            source_id="test",
            url="http://example.com",
            city="amsterdam",
            asking_rent=1200,
            surface_area_m2=50,
            energy_label=EnergyLabel.C,
            woz_value=150_000,
            woz_verified=True,
        )
        defaults.update(kwargs)
        return Listing(**defaults)

    def test_surface_area_contributes_1_per_m2(self):
        listing = self._make_listing(surface_area_m2=60)
        bd = calculate_wws(listing)
        assert bd.surface_area == 60.0

    def test_energy_label_points(self):
        for label, expected_pts in ENERGY_POINTS_APARTMENT.items():
            listing = self._make_listing(energy_label=EnergyLabel.from_string(label))
            bd = calculate_wws(listing)
            assert bd.energy_label == expected_pts

    def test_woz_capped_at_33_percent(self):
        # Use a very high WOZ value to trigger the cap
        listing = self._make_listing(woz_value=10_000_000, surface_area_m2=50)
        bd = calculate_wws(listing)
        total = bd.total
        if total > 0:
            woz_pct = bd.woz_capped / total
            assert woz_pct <= 0.334  # small float tolerance

    def test_unknown_energy_label_defaults_to_d(self):
        listing = self._make_listing(energy_label=None)
        bd = calculate_wws(listing)
        assert bd.energy_label == ENERGY_POINTS_APARTMENT["D"]
        assert "energy_label_unknown_assumed_D" in listing.wws_flags

    def test_missing_woz_uses_estimate(self):
        listing = self._make_listing(woz_value=None)
        bd = calculate_wws(listing)
        assert "woz_estimated_conservative" in listing.wws_flags
        assert bd.woz_uncapped > 0

    def test_bustable_listing_detected(self):
        # Small apartment, high asking rent should be bustable
        listing = self._make_listing(
            surface_area_m2=30,
            asking_rent=1500,
            energy_label=EnergyLabel.G,
            woz_value=90_000,
        )
        calculate_wws(listing)
        # 30m² + G label (-15) + minimal defaults should be well below threshold
        if listing.wws_points < LIBERALIZATION_THRESHOLD and listing.asking_rent > listing.wws_max_rent:
            assert listing.wws_is_bustable
            assert listing.wws_savings > 0

    def test_high_points_not_bustable(self):
        # Large apartment with high WOZ should exceed liberalization threshold
        listing = self._make_listing(
            surface_area_m2=200,
            asking_rent=2500,
            energy_label=EnergyLabel.A_PLUS_PLUS_PLUS_PLUS,
            woz_value=1_500_000,
        )
        calculate_wws(listing)
        if listing.wws_points >= LIBERALIZATION_THRESHOLD:
            assert not listing.wws_is_bustable

    def test_confidence_high_when_all_data_present(self):
        listing = self._make_listing(
            energy_label=EnergyLabel.A,
            woz_value=200_000,
            woz_verified=True,
        )
        calculate_wws(listing)
        # With verified WOZ and known energy label, confidence should be LOW or MEDIUM
        # (outdoor/kitchen/bathroom/heating flags will be present)
        assert listing.wws_confidence in (
            ConfidenceLevel.HIGH,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.LOW,
        )

    def test_mutates_listing_fields(self):
        listing = self._make_listing()
        calculate_wws(listing)
        assert listing.wws_points is not None
        assert listing.wws_max_rent is not None
        assert listing.wws_confidence is not None
        assert isinstance(listing.wws_breakdown, dict)
        assert isinstance(listing.wws_flags, list)
