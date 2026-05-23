"""Tests for LLM extraction point calculations and integration with WWS."""

from rentbuster.llm import LLMExtraction
from rentbuster.models import EnergyLabel, Listing, Source
from rentbuster.wws import calculate_wws


def _make_listing(**kw) -> Listing:
    defaults = dict(
        source=Source.PARARIUS,
        source_id="test-1",
        url="https://pararius.com/test",
        street="Keizersgracht",
        house_number="123",
        city="amsterdam",
        asking_rent=1800,
        surface_area_m2=60,
        energy_label=EnergyLabel.C,
        woz_value=300_000,
        woz_verified=True,
        description="Spacious apartment with balcony and modern kitchen.",
    )
    defaults.update(kw)
    return Listing(**defaults)


class TestLLMExtractionPoints:
    def test_no_outdoor_gives_negative_points(self):
        ext = LLMExtraction(has_private_outdoor=False, outdoor_shared=False)
        assert ext.outdoor_points == -5.0

    def test_private_balcony_points(self):
        ext = LLMExtraction(has_private_outdoor=True, outdoor_area_m2=5, outdoor_shared=False)
        assert ext.outdoor_points == 10.0  # 5m² * 2.0

    def test_shared_garden_points(self):
        ext = LLMExtraction(has_private_outdoor=False, outdoor_area_m2=20, outdoor_shared=True)
        assert ext.outdoor_points == 15.0  # 20m² * 0.75

    def test_outdoor_no_area_gives_zero(self):
        ext = LLMExtraction(has_private_outdoor=True, outdoor_area_m2=None)
        assert ext.outdoor_points == 0.0

    def test_kitchen_quality_points(self):
        assert LLMExtraction(kitchen_quality="minimal").kitchen_points == 4.0
        assert LLMExtraction(kitchen_quality="standard").kitchen_points == 7.0
        assert LLMExtraction(kitchen_quality="well_equipped").kitchen_points == 10.0
        assert LLMExtraction(kitchen_quality="luxury").kitchen_points == 13.0

    def test_bathroom_quality_points(self):
        assert LLMExtraction(bathroom_quality="basic").bathroom_points == 3.0
        assert LLMExtraction(bathroom_quality="standard").bathroom_points == 5.0
        assert LLMExtraction(bathroom_quality="full").bathroom_points == 8.0
        assert LLMExtraction(bathroom_quality="luxury").bathroom_points == 12.0

    def test_bathroom_bathtub_bonus(self):
        ext = LLMExtraction(bathroom_quality="standard", has_bathtub=True)
        assert ext.bathroom_points == 7.0  # 5.0 + 2.0

    def test_bathroom_bathtub_no_bonus_for_full(self):
        ext = LLMExtraction(bathroom_quality="full", has_bathtub=True)
        assert ext.bathroom_points == 8.0  # no extra — already included

    def test_second_bathroom_bonus(self):
        ext = LLMExtraction(bathroom_quality="basic", has_second_bathroom=True)
        assert ext.bathroom_points == 6.0  # 3.0 + 3.0

    def test_heating_type_points(self):
        assert LLMExtraction(heating_type="central").heating_points == 2.0
        assert LLMExtraction(heating_type="district").heating_points == 2.0
        assert LLMExtraction(heating_type="individual").heating_points == 1.0
        assert LLMExtraction(heating_type="floor").heating_points == 3.0
        assert LLMExtraction(heating_type="unknown").heating_points == 2.0


class TestWWSWithLLMExtraction:
    def test_llm_extraction_replaces_defaults(self):
        listing = _make_listing()
        ext = LLMExtraction(
            has_private_outdoor=True,
            outdoor_area_m2=8,
            kitchen_quality="well_equipped",
            bathroom_quality="full",
            heating_type="floor",
        )
        bd = calculate_wws(listing, llm_extraction=ext)

        assert bd.outdoor_space == 16.0  # 8m² * 2.0
        assert bd.kitchen == 10.0
        assert bd.bathroom == 8.0
        assert bd.heating == 3.0
        assert "llm_extracted" in bd.flags
        assert "outdoor_space_assumed_none" not in bd.flags

    def test_without_llm_uses_defaults(self):
        listing = _make_listing()
        bd = calculate_wws(listing, llm_extraction=None)

        assert bd.outdoor_space == -5.0
        assert bd.kitchen == 4.0
        assert bd.bathroom == 3.0
        assert bd.heating == 2.0
        assert "outdoor_space_assumed_none" in bd.flags
        assert "llm_extracted" not in bd.flags

    def test_llm_extraction_affects_bustability(self):
        listing = _make_listing(asking_rent=900, surface_area_m2=45)
        bd_default = calculate_wws(_make_listing(asking_rent=900, surface_area_m2=45))
        _was_bustable = listing.wws_is_bustable

        listing2 = _make_listing(asking_rent=900, surface_area_m2=45)
        ext = LLMExtraction(
            has_private_outdoor=True,
            outdoor_area_m2=15,
            kitchen_quality="luxury",
            bathroom_quality="luxury",
            has_second_bathroom=True,
            heating_type="floor",
        )
        calculate_wws(listing2, llm_extraction=ext)
        # Higher points from LLM → potentially not bustable anymore
        assert listing2.wws_points > bd_default.total

    def test_llm_confidence_higher(self):
        listing_default = _make_listing()
        calculate_wws(listing_default)

        listing_llm = _make_listing()
        ext = LLMExtraction(kitchen_quality="standard", bathroom_quality="standard", heating_type="central")
        calculate_wws(listing_llm, llm_extraction=ext)

        # LLM extraction removes 4 assumed flags, replaced by 1 "llm_extracted"
        default_flags = [
            f
            for f in listing_default.wws_flags
            if f.startswith(("outdoor_", "kitchen_", "bathroom_", "heating_"))
        ]
        assert len(default_flags) == 4
        assert "llm_extracted" in listing_llm.wws_flags
