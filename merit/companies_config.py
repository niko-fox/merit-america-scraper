"""Company tier registry loader (canonical + aliases → S/A/B/C)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CompanyEntry:
    canonical: str
    tier: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class CompaniesConfig:
    entries: list[CompanyEntry]
    fuzzy_threshold: int = 88

    def lookup_keys(self) -> list[str]:
        """All names (canonical + aliases) flattened for fuzzy matching."""
        keys: list[str] = []
        for e in self.entries:
            keys.append(e.canonical)
            keys.extend(e.aliases)
        return keys

    def tier_for(self, matched_name: str) -> str | None:
        """Map a matched alias/canonical back to its tier."""
        for e in self.entries:
            if matched_name == e.canonical or matched_name in e.aliases:
                return e.tier
        return None


def load_companies(path: Path) -> CompaniesConfig:
    data = yaml.safe_load(path.read_text())
    entries = [
        CompanyEntry(canonical=e["canonical"], tier=e["tier"],
                     aliases=e.get("aliases", []) or [])
        for e in data["companies"]
    ]
    return CompaniesConfig(entries=entries,
                           fuzzy_threshold=data.get("fuzzy_threshold", 88))
