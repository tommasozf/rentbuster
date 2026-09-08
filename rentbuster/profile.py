"""Profile: search criteria + WWS config, loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass
class WWSConfig:
    bustable_only: bool = True
    min_savings: int = 50
    # Only alert when rent-buster.nl's own calculation also says the ad is bustable
    # (listings without rent-buster.nl data are unaffected).
    require_rb_agreement: bool = False
    default_outdoor_points: float = -5.0
    default_kitchen_points: float = 4.0
    default_bathroom_points: float = 8.0
    default_heating_points: float = 2.0  # per heated room

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WWSConfig:
        return cls(
            bustable_only=bool(data.get("bustable_only", True)),
            min_savings=int(data.get("min_savings", 50)),
            require_rb_agreement=bool(data.get("require_rb_agreement", False)),
            default_outdoor_points=float(data.get("default_outdoor_points", -5.0)),
            default_kitchen_points=float(data.get("default_kitchen_points", 4.0)),
            default_bathroom_points=float(data.get("default_bathroom_points", 8.0)),
            default_heating_points=float(data.get("default_heating_points", 2.0)),
        )


@dataclass
class SearchConfig:
    city: str = "amsterdam"
    max_rent: int = 2500
    min_size: int = 0
    max_rooms: int = 0
    property_types: list[str] = field(default_factory=lambda: ["apartment", "studio"])
    pararius_max_pages: int = 5
    rentbuster_nl_max_pages: int = 10
    must_allow_students: bool = False
    must_allow_sharing: bool = False
    must_accept_guarantor: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchConfig:
        return cls(
            city=str(data.get("city", "amsterdam")),
            max_rent=int(data.get("max_rent", 2500)),
            min_size=int(data.get("min_size", 0)),
            max_rooms=int(data.get("max_rooms", 0)),
            property_types=list(data.get("property_types") or ["apartment", "studio"]),
            pararius_max_pages=int(data.get("pararius_max_pages", 5)),
            rentbuster_nl_max_pages=int(data.get("rentbuster_nl_max_pages", 10)),
            must_allow_students=bool(data.get("must_allow_students", False)),
            must_allow_sharing=bool(data.get("must_allow_sharing", False)),
            must_accept_guarantor=bool(data.get("must_accept_guarantor", False)),
        )


@dataclass
class Profile:
    name: str
    description: str
    search: SearchConfig
    wws: WWSConfig
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> Profile:
        missing = [k for k in ("name", "search") if k not in data]
        if missing:
            raise ValueError(f"Profile YAML missing required keys: {missing}")
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            search=SearchConfig.from_dict(data.get("search") or {}),
            wws=WWSConfig.from_dict(data.get("wws") or {}),
            source_path=source_path,
        )


def load_profile(name_or_path: str) -> Profile:
    """Load a profile by name (looks up profiles/<name>.yaml) or by path."""
    candidate = Path(name_or_path)
    if candidate.suffix in (".yaml", ".yml") and candidate.exists():
        path = candidate
    else:
        path = PROFILES_DIR / f"{name_or_path}.yaml"
        if not path.exists():
            available = sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))
            raise FileNotFoundError(
                f"Profile not found: {name_or_path!r}. Looked for {path}. Available profiles: {available}"
            )
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Profile.from_dict(data, source_path=path)
