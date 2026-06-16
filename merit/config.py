"""Merit America config loaders.

Company registry shares the Canada schema, so we reuse src.config.load_companies.
Roles (verticals + two-tier exclusions) and regions are Merit-specific.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from merit.companies_config import CompaniesConfig, load_companies as _load_companies

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class RolesConfig:
    # vertical slug -> list of lowercase title keywords
    verticals: dict[str, list[str]]
    hard_exclusions: list[re.Pattern]
    soft_exclusions: list[re.Pattern]
    junior_signals: list[re.Pattern]

    def all_keywords(self) -> list[str]:
        out: list[str] = []
        for kws in self.verticals.values():
            out.extend(kws)
        return out


@dataclass
class RegionsConfig:
    # 2-letter state code -> region name
    state_to_region: dict[str, str]
    drop_unplaceable_remote: bool = True
    region_order: list[str] = field(default_factory=list)


def load_roles(path: Path | None = None) -> RolesConfig:
    path = path or CONFIG_DIR / "roles.yaml"
    data = yaml.safe_load(path.read_text())
    verticals = {
        slug: [k.lower() for k in kws]
        for slug, kws in data["verticals"].items()
    }
    compile_all = lambda key: [re.compile(p, re.IGNORECASE) for p in data.get(key, [])]
    return RolesConfig(
        verticals=verticals,
        hard_exclusions=compile_all("hard_exclusions"),
        soft_exclusions=compile_all("soft_exclusions"),
        junior_signals=compile_all("junior_signals"),
    )


def load_regions(path: Path | None = None) -> RegionsConfig:
    path = path or CONFIG_DIR / "regions.yaml"
    data = yaml.safe_load(path.read_text())
    state_to_region: dict[str, str] = {}
    order: list[str] = []
    for region, states in data["regions"].items():
        order.append(region)
        for st in states:
            state_to_region[st.upper()] = region
    return RegionsConfig(
        state_to_region=state_to_region,
        drop_unplaceable_remote=bool(data.get("drop_unplaceable_remote", True)),
        region_order=order,
    )


def load_companies(path: Path | None = None) -> CompaniesConfig:
    return _load_companies(path or CONFIG_DIR / "companies.yaml")
