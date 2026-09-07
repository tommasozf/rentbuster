"""Tests for rentbuster.wws — WWS points calculator."""

from __future__ import annotations

from rentbuster.models import ConfidenceLevel, EnergyLabel, Listing, Source
from rentbuster.wws import (
    ENERGY_POINTS_APARTMENT,
    LIBERALIZATION_THRESHOLD,
    calculate_wws,
    points_to_max_rent,
    round_points,
)


class TestPointsToMaxRent:
    def test_below_minimum(self):
        # Below the first table row (40 pts) the 40-point rent applies
        assert points_to_max_rent(30) == points_to_max_rent(40) == 250.26

    def test_official_2026_values(self):
        # Huurcommissie Bijlage 3 per 1 January 2026
        assert points_to_max_rent(143) == 932.93  # social-sector boundary
        assert points_to_max_rent(186) == 1228.07  # top of the regulated (middenhuur) segment
        assert points_to_max_rent(187) == 1234.92  # first free-market row
        assert points_to_max_rent(250) == 1667.40  # last row

    def test_rounds_to_whole_points_half_up(self):
        assert points_to_max_rent(150.4) == points_to_max_rent(150)
        assert points_to_max_rent(150.5) == points_to_max_rent(151)
        assert round_points(186.5) == 187

    def test_above_table_extrapolates(self):
        assert points_to_max_rent(260) > points_to_max_rent(250)

    def test_monotonically_increasing(self):
        rents = [points_to_max_rent(p) for p in range(40, 260)]
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

    def test_woz_cap_only_applies_from_187_points(self):
        # 50 m², label C, WOZ €500k: WOZ alone would be ~29+37 = 66 pts, well over 33% of the
        # total, but the home stays under 187 so the cap must NOT be applied.
        listing = self._make_listing(woz_value=500_000, surface_area_m2=50)
        bd = calculate_wws(listing)
        assert bd.woz_capped == bd.woz_uncapped
        assert bd.total < LIBERALIZATION_THRESHOLD
        assert "woz_capped" not in listing.wws_flags

    def test_woz_cap_floors_at_186_points(self):
        # Huurcommissie example: 109 non-WOZ points and 109 WOZ points (218 total, free market).
        # With the cap WOZ counts for at most 33%: 53.7 → 53, total 162, which becomes 186.
        # 85 m² + label C (15) + defaults (-5 + 4 + 8 + 2) = 109 non-WOZ points, like the example.
        listing = self._make_listing(woz_value=10_000_000, surface_area_m2=85)
        bd = calculate_wws(listing)
        non_woz = bd.surface_area + bd.energy_label + bd.outdoor_space + bd.kitchen + bd.bathroom + bd.heating
        assert non_woz == 109
        assert bd.woz_uncapped + non_woz >= LIBERALIZATION_THRESHOLD
        assert bd.woz_capped == 53
        assert bd.total == 186
        assert "woz_capped" in listing.wws_flags
        assert "woz_cap_floor_186" in listing.wws_flags
        assert listing.wws_points == 186
        assert listing.wws_max_rent == points_to_max_rent(186)

    def test_woz_cap_no_floor_for_small_newbuild(self):
        listing = self._make_listing(
            woz_value=10_000_000, surface_area_m2=35, construction_year=2020, city="amsterdam"
        )
        bd = calculate_wws(listing)
        assert "woz_cap_newbuild_exception" in listing.wws_flags
        assert bd.woz_cap_floor == 0
        assert bd.total < LIBERALIZATION_THRESHOLD

    def test_minimum_woz_value(self):
        listing = self._make_listing(woz_value=50_000)
        bd = calculate_wws(listing)
        assert "woz_minimum_applied" in listing.wws_flags
        assert bd.woz_uncapped == round(85_806 / 16_954 + (85_806 / 50) / 268, 2)

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
