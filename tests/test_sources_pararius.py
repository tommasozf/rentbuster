"""Tests for pararius.py module-level parsing helpers."""

from __future__ import annotations

from rentbuster.models import EnergyLabel
from rentbuster.sources.pararius import (
    ParariusSource,
    _extract_postal_code,
    _parse_address,
    _parse_area,
    _parse_energy_label,
    _parse_price,
)


class TestParsePrice:
    def test_euro_with_comma(self):
        assert _parse_price("€ 1,450 /month") == 1450

    def test_euro_with_period_thousands(self):
        assert _parse_price("€1.450") == 1450

    def test_plain_number(self):
        assert _parse_price("1500") == 1500

    def test_empty_string(self):
        assert _parse_price("") == 0

    def test_no_number(self):
        assert _parse_price("contact for price") == 0


class TestParseArea:
    def test_with_m2_symbol(self):
        assert _parse_area("75 m²") == 75

    def test_with_m2_text(self):
        assert _parse_area("75m2") == 75

    def test_no_number(self):
        assert _parse_area("unknown") == 0

    def test_empty(self):
        assert _parse_area("") == 0


class TestParseEnergyLabel:
    def test_basic_labels(self):
        assert _parse_energy_label("A") == EnergyLabel.A
        assert _parse_energy_label("G") == EnergyLabel.G

    def test_plus_labels(self):
        assert _parse_energy_label("A+") == EnergyLabel.A_PLUS
        assert _parse_energy_label("A++") == EnergyLabel.A_PLUS_PLUS

    def test_empty(self):
        assert _parse_energy_label("") is None

    def test_invalid(self):
        assert _parse_energy_label("Z") is None


class TestExtractPostalCode:
    def test_spaced_format(self):
        assert _extract_postal_code("Amsterdam 1015 CJ") == "1015 CJ"

    def test_no_space(self):
        assert _extract_postal_code("1015CJ Amsterdam") == "1015 CJ"

    def test_no_match(self):
        assert _extract_postal_code("No postal code here") is None


class TestParseAddress:
    def test_simple(self):
        prop_type, street, num, add = _parse_address("Keizersgracht 123")
        assert street == "Keizersgracht"
        assert num == "123"
        assert add == ""

    def test_with_property_type_prefix(self):
        prop_type, street, num, add = _parse_address("Flat Prins Hendrikkade 86 A")
        assert prop_type == "apartment"
        assert street == "Prins Hendrikkade"
        assert num == "86"
        assert add == "A"

    def test_with_letter_addition(self):
        prop_type, street, num, add = _parse_address("Flat Prins Hendrikkade 86 A")
        assert street == "Prins Hendrikkade"
        assert num == "86"
        assert add == "A"

    def test_multi_word_street(self):
        prop_type, street, num, add = _parse_address("Van der Pekstraat 42")
        assert "Pekstraat" in street
        assert num == "42"

    def test_studio_type(self):
        prop_type, street, num, add = _parse_address("Studio Keizersgracht 10")
        assert prop_type == "studio"

    def test_empty(self):
        prop_type, street, num, add = _parse_address("")
        assert street == ""
        assert num == ""


class TestDetailCandidates:
    def _listing(self, sid, rooms=2, ptype="apartment"):
        from rentbuster.models import Listing, Source

        return Listing(source=Source.PARARIUS, source_id=sid, url="u", num_rooms=rooms, property_type=ptype)

    def test_known_and_filtered_listings_are_skipped(self):
        src = ParariusSource(max_rooms=3, property_types=["apartment"], skip_detail_ids={"known"})
        listings = [
            self._listing("known"),
            self._listing("too-big", rooms=5),
            self._listing("house", ptype="house"),
            self._listing("new"),
        ]
        candidates, known, filtered = src._select_detail_candidates(listings)
        assert [ls.source_id for ls in candidates] == ["new"]
        assert known == 1
        assert filtered == 2

    def test_build_sources_passes_pararius_ids_only(self):
        from rentbuster.config import Settings
        from rentbuster.profile import load_profile
        from rentbuster.sources import build_sources

        settings = Settings(_env_file=None, rentbuster_nl_enabled=False)
        sources = build_sources(
            settings, load_profile("amsterdam"), seen_ids={("pararius", "a"), ("funda", "b")}
        )
        assert isinstance(sources[0], ParariusSource)
        assert sources[0].skip_detail_ids == {"a"}
