"""Tests for rentbuster.address — shared address parsing and dedup keys."""

from __future__ import annotations

from rentbuster.address import normalize_address, split_street_number
from rentbuster.sources.pararius import _parse_address as pararius_parse
from rentbuster.sources.rentbuster_nl import _parse_address as rb_parse


class TestSplitStreetNumber:
    def test_plain(self):
        assert split_street_number("Keizersgracht 123") == ("Keizersgracht", "123", "")

    def test_multi_word_street(self):
        assert split_street_number("Van der Pekstraat 42") == ("Van der Pekstraat", "42", "")

    def test_spaced_addition(self):
        assert split_street_number("Prins Hendrikkade 86 A") == ("Prins Hendrikkade", "86", "A")

    def test_hyphen_and_spaces_normalise_the_same(self):
        assert split_street_number("Rustenburgerstraat 146-A20") == ("Rustenburgerstraat", "146", "A20")
        assert split_street_number("Rustenburgerstraat 146 A 20") == ("Rustenburgerstraat", "146", "A20")
        assert split_street_number("Rustenburgerstraat 146A20") == ("Rustenburgerstraat", "146", "A20")

    def test_floor_addition(self):
        assert split_street_number("Jan Pieter Heijestraat 112 3") == ("Jan Pieter Heijestraat", "112", "3")

    def test_ordinal_street_prefix_is_not_a_number(self):
        assert split_street_number("1e Helmersstraat 12 hs") == ("1e Helmersstraat", "12", "hs")

    def test_no_number(self):
        assert split_street_number("Frans Halsstraat") == ("Frans Halsstraat", "", "")

    def test_empty(self):
        assert split_street_number("") == ("", "", "")


class TestNormalizeAddress:
    def test_postcode_key_when_available(self):
        assert normalize_address("Rustenburgerstraat", "146", "A20", "1073 GJ") == "1073gj|146|a20"

    def test_street_key_without_postcode(self):
        assert normalize_address("Keizersgracht", "123", "") == "keizersgracht|123|"

    def test_addition_separators_ignored(self):
        assert normalize_address("X", "1", "-A", "1000 AA") == normalize_address("X", "1", "A", "1000AA")


class TestSameHomeAcrossSources:
    def test_pararius_and_rentbuster_nl_agree(self):
        _, p_street, p_num, p_add = pararius_parse("Flat Rustenburgerstraat 146 A 20")
        r_street, r_num, r_add, r_pc = rb_parse("Rustenburgerstraat 146-A20, 1073GJ Amsterdam")
        assert normalize_address(p_street, p_num, p_add, "1073 GJ") == normalize_address(
            r_street, r_num, r_add, r_pc
        )
