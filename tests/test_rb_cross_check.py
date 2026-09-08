"""Tests for the rent-buster.nl cross-check."""

from __future__ import annotations

from rentbuster.dedup import deduplicate
from rentbuster.models import Listing, Source
from rentbuster.notify.discord import _rb_summary
from rentbuster.profile import WWSConfig
from rentbuster.wws import rb_agrees


def _listing(**kw):
    base = dict(source=Source.PARARIUS, source_id="x", url="u", asking_rent=2000)
    base.update(kw)
    return Listing(**base)


def test_rb_agrees_without_rb_data():
    assert rb_agrees(_listing())


def test_rb_agrees_when_both_bustable():
    assert rb_agrees(_listing(rb_points=160, rb_estimated_max_rent=1050))


def test_rb_disagrees_when_free_market_or_under_max():
    assert not rb_agrees(_listing(rb_points=220, rb_estimated_max_rent=1459))
    assert not rb_agrees(_listing(rb_points=180, rb_estimated_max_rent=2100))


def test_rb_points_survive_dedup():
    par = _listing(street="Amstelkade", house_number="26", postal_code="1078 AD")
    rb = _listing(
        source=Source.FUNDA,
        source_id="y",
        street="Amstelkade",
        house_number="26",
        postal_code="1078 AD",
        rb_points=225,
        rb_estimated_max_rent=1493,
    )
    merged = deduplicate([rb, par])
    assert len(merged) == 1
    assert merged[0].source == Source.PARARIUS
    assert merged[0].rb_points == 225
    assert merged[0].rb_estimated_max_rent == 1493


def test_profile_flag_defaults_off():
    assert WWSConfig.from_dict({}).require_rb_agreement is False
    assert WWSConfig.from_dict({"require_rb_agreement": True}).require_rb_agreement is True


def test_rb_summary_text():
    assert _rb_summary(_listing(rb_points=220, rb_estimated_max_rent=1459)) == "220 pts → free market"
    assert _rb_summary(_listing(rb_points=162, rb_estimated_max_rent=1042)) == "162 pts • max €1,042"
