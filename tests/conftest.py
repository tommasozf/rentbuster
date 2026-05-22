"""Shared test fixtures for rentbuster tests."""

from __future__ import annotations

import pytest

from rentbuster.models import EnergyLabel, Listing, Source


@pytest.fixture
def basic_listing() -> Listing:
    return Listing(
        source=Source.PARARIUS,
        source_id="test-1",
        url="https://pararius.com/apartments/amsterdam/test-1",
        street="Keizersgracht",
        house_number="123",
        house_number_addition="",
        postal_code="1015 CJ",
        city="amsterdam",
        asking_rent=1500,
        surface_area_m2=60,
        num_rooms=3,
        energy_label=EnergyLabel.C,
        woz_value=180_000,
        woz_verified=True,
    )


@pytest.fixture
def bustable_listing() -> Listing:
    """A listing that should be well below the liberalization threshold with high asking rent."""
    return Listing(
        source=Source.PARARIUS,
        source_id="test-bustable",
        url="https://pararius.com/apartments/amsterdam/test-bustable",
        street="Plantage Middenlaan",
        house_number="42",
        city="amsterdam",
        asking_rent=1800,
        surface_area_m2=45,
        energy_label=EnergyLabel.D,
        woz_value=150_000,
        woz_verified=True,
    )
