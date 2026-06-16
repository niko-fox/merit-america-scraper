"""Vertical classifier + junior/lower-intermediate gate.

A title is kept iff:
  1. No hard_exclusion matches (senior+, director+, etc.), AND
  2. No soft_exclusion matches UNLESS a junior_signal is also present
     (keeps "Junior Project Manager", drops bare "IT Support Manager"), AND
  3. At least one vertical keyword matches.

The matched vertical (first hit, in YAML order) is assigned to job.vertical.
"""
from __future__ import annotations

import re

from merit.config import RolesConfig
from merit.normalize import Job


def _whole_word(title_lc: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", title_lc) is not None


def classify(job: Job, roles: RolesConfig) -> str | None:
    """Return the vertical slug, or None to drop."""
    title = (job.title or "").lower()
    if not title:
        return None

    # 1. Hard exclusions — always drop.
    for pat in roles.hard_exclusions:
        if pat.search(title):
            return None

    # 2. Soft exclusions — drop unless a junior signal rescues the title.
    soft_hit = any(pat.search(title) for pat in roles.soft_exclusions)
    if soft_hit:
        has_signal = any(pat.search(title) for pat in roles.junior_signals)
        if not has_signal:
            return None

    # 3. Vertical keyword match (first vertical in YAML order wins).
    for slug, keywords in roles.verticals.items():
        for kw in keywords:
            if _whole_word(title, kw):
                return slug
    return None


def apply(jobs, roles: RolesConfig):
    """Set job.vertical in place; yield only kept jobs."""
    for j in jobs:
        v = classify(j, roles)
        if v:
            j.vertical = v
            yield j
