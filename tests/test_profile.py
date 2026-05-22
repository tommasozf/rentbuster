from __future__ import annotations

import pytest

from rentbuster.profile import PROFILES_DIR, Profile, SearchConfig, WWSConfig, load_profile

RENTBUSTER_PROFILES = ["amsterdam"]
OLD_PROFILES = ["generic", "student-amsterdam", "young-professional-randstad", "family-utrecht"]


def test_amsterdam_profile_loads():
    profile = load_profile("amsterdam")
    assert profile.name == "amsterdam"
    assert profile.description
    assert isinstance(profile.search, SearchConfig)
    assert profile.search.city == "amsterdam"
    assert profile.search.max_rent > 0
    assert isinstance(profile.wws, WWSConfig)
    assert profile.wws.min_savings >= 0


def test_profile_from_path():
    path = PROFILES_DIR / "amsterdam.yaml"
    profile = load_profile(str(path))
    assert profile.name == "amsterdam"


def test_missing_profile_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("definitely-does-not-exist")


def test_profile_from_dict_requires_keys():
    with pytest.raises(ValueError):
        Profile.from_dict({"description": "x"})  # missing name + search


def test_search_config_defaults():
    sc = SearchConfig.from_dict({})
    assert sc.city == "amsterdam"
    assert sc.max_rent == 2500
    assert "apartment" in sc.property_types


def test_wws_config_defaults():
    wws = WWSConfig.from_dict({})
    assert wws.bustable_only is True
    assert wws.min_savings == 50
    assert wws.default_outdoor_points < 0
