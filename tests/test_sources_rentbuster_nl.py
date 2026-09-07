"""Tests for rentbuster.sources.rentbuster_nl parsing helpers."""

from __future__ import annotations

from rentbuster.models import Source
from rentbuster.sources.rentbuster_nl import _item_to_listing, _parse_address, normalize_property_type


class TestNormalizePropertyType:
    def test_dutch_apartment_variants(self):
        assert normalize_property_type("Appartement") == "apartment"
        assert normalize_property_type("appartement") == "apartment"
        assert normalize_property_type("Penthouse (appartement)") == "apartment"
        assert normalize_property_type("Maisonnette") == "apartment"
        assert normalize_property_type("Bovenwoning") == "apartment"

    def test_english_passthrough(self):
        assert normalize_property_type("apartment") == "apartment"
        assert normalize_property_type("Studio") == "studio"

    def test_rooms_and_houses(self):
        assert normalize_property_type("Kamer") == "room"
        assert normalize_property_type("room") == "room"
        assert normalize_property_type("Eengezinswoning") == "house"
        assert normalize_property_type("Huis") == "house"

    def test_unknown_kept_lowercase(self):
        assert normalize_property_type("Woonboot") == "woonboot"

    def test_empty_defaults_to_apartment(self):
        assert normalize_property_type("") == "apartment"
        assert normalize_property_type(None) == "apartment"


class TestItemToListing:
    def _item(self, **kw):
        base = {
            "propertyId": "123",
            "fullUrl": "https://www.funda.nl/detail/huur/amsterdam/appartement-x/123/",
            "address": "Osdorper Ban 21-F, 1068LD Amsterdam",
            "city": "Amsterdam",
            "price": 1800,
            "size": 55,
            "rooms": 3,
            "type": "Appartement",
        }
        base.update(kw)
        return base

    def test_type_is_normalized(self):
        listing = _item_to_listing(self._item())
        assert listing is not None
        assert listing.property_type == "apartment"
        assert listing.source == Source.FUNDA

    def test_room_type(self):
        listing = _item_to_listing(self._item(type="Kamer", fullUrl="https://kamernet.nl/x/1"))
        assert listing.property_type == "room"
        assert listing.source == Source.KAMERNET


class TestParseAddress:
    def test_hyphen_letter_addition(self):
        assert _parse_address("Osdorper Ban 21-F, 1068LD Amsterdam") == ("Osdorper Ban", "21", "F", "1068 LD")

    def test_plain_number(self):
        assert _parse_address("Keizersgracht 123, 1015 CJ Amsterdam") == (
            "Keizersgracht",
            "123",
            "",
            "1015 CJ",
        )
