"""Tests for rentbuster.models."""

from __future__ import annotations

from rentbuster.models import EnergyLabel, Listing, Source, normalize_address


class TestEnergyLabel:
    def test_from_string_basic(self):
        assert EnergyLabel.from_string("A") == EnergyLabel.A
        assert EnergyLabel.from_string("B") == EnergyLabel.B
        assert EnergyLabel.from_string("G") == EnergyLabel.G

    def test_from_string_plus_labels(self):
        assert EnergyLabel.from_string("A+") == EnergyLabel.A_PLUS
        assert EnergyLabel.from_string("A++") == EnergyLabel.A_PLUS_PLUS
        assert EnergyLabel.from_string("A+++") == EnergyLabel.A_PLUS_PLUS_PLUS
        assert EnergyLabel.from_string("A++++") == EnergyLabel.A_PLUS_PLUS_PLUS_PLUS

    def test_from_string_lowercase(self):
        assert EnergyLabel.from_string("a") == EnergyLabel.A
        assert EnergyLabel.from_string("b") == EnergyLabel.B

    def test_from_string_none(self):
        assert EnergyLabel.from_string(None) is None
        assert EnergyLabel.from_string("") is None
        assert EnergyLabel.from_string("Z") is None


class TestNormalizeAddress:
    def test_basic(self):
        key = normalize_address("Keizersgracht", "123", "")
        assert key == "keizersgracht|123|"

    def test_with_addition(self):
        key = normalize_address("Keizersgracht", "123", "A")
        assert key == "keizersgracht|123|a"

    def test_trailing_period_stripped(self):
        key = normalize_address("Keizersgr.", "10", "")
        assert not key.startswith("keizersgr.")
        assert key == "keizersgr|10|"

    def test_same_address_different_case(self):
        k1 = normalize_address("Prinsengracht", "100", "")
        k2 = normalize_address("prinsengracht", "100", "")
        assert k1 == k2


class TestListing:
    def test_address_key(self):
        listing = Listing(
            source=Source.PARARIUS,
            source_id="x",
            url="http://example.com",
            street="Keizersgracht",
            house_number="10",
            house_number_addition="B",
        )
        assert listing.address_key == "keizersgracht|10|b"

    def test_to_db_params_keys(self):
        listing = Listing(source=Source.PARARIUS, source_id="x", url="http://example.com")
        params = listing.to_db_params()
        required_keys = {
            "source",
            "source_id",
            "url",
            "asking_rent",
            "wws_is_bustable",
            "woz_value",
            "woz_verified",
            "images",
            "wws_flags",
        }
        assert required_keys.issubset(params.keys())
