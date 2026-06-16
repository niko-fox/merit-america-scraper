"""Employer tier (letter) + tier_score (composite) + per-region selection.

Tier letter  = employer quality only (curated registry, Brave fallback).
Tier score   = composite of employer weight + salary + employment + recency +
               location, so the strongest *overall* postings float to the top
               of each region.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from rapidfuzz import fuzz, process

from merit import employer_intel
from merit.companies_config import CompaniesConfig
from merit.normalize import Job

log = logging.getLogger(__name__)

TIER_WEIGHT = {"S": 100, "A": 70, "B": 40, "C": 10}


def _normalize_company(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\b(inc|ltd|llc|corp|corporation|company|co|limited|the)\b\.?", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_tier(company_name: str, cfg: CompaniesConfig) -> str:
    """S/A/B/C for a raw company name. Registry first, then Brave intel.

    Mirrors the guarded token_set_ratio match in src/rank.py so big names map
    cleanly while tiny aliases don't cross-contaminate.
    """
    if not company_name:
        return "C"
    query = _normalize_company(company_name)
    if not query:
        return "C"

    normalized_lookup = {_normalize_company(k): k for k in cfg.lookup_keys()}
    match = process.extractOne(
        query, list(normalized_lookup.keys()), scorer=fuzz.token_set_ratio
    )
    if match and match[1] >= cfg.fuzzy_threshold:
        matched_key_norm = match[0]
        key_tokens = set(matched_key_norm.split())
        query_tokens = set(query.split())
        if key_tokens.issubset(query_tokens) or query_tokens.issubset(key_tokens) \
                or matched_key_norm == query:
            tier = cfg.tier_for(normalized_lookup[matched_key_norm])
            if tier:
                return tier

    # Registry miss -> hybrid: ask Brave employer intel (fail-open to C).
    return employer_intel.grade(company_name)


def _days_since(iso_date_str: str) -> int | None:
    if not iso_date_str:
        return None
    try:
        d = datetime.fromisoformat(iso_date_str.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            d = date.fromisoformat(iso_date_str[:10])
        except ValueError:
            return None
    return (date.today() - d).days


def _salary_score(job: Job) -> int:
    if job.salary_min is None or job.salary_max is None:
        return 0
    mid = (job.salary_min + job.salary_max) / 2
    strong = (job.salary_period == "hourly" and mid >= 45) or \
             (job.salary_period != "hourly" and mid >= 90000)
    return 20 if strong else 10


def _employment_score(employment_type: str) -> int:
    e = (employment_type or "").lower()
    if "full" in e:
        return 10
    if "contract" in e or "temp" in e or "freelance" in e:
        return 6
    if "intern" in e:
        return 4
    if "part" in e:
        return 3
    return 5  # unknown — neutral-ish


def _recency_score(posted_date: str) -> int:
    days = _days_since(posted_date)
    if days is None:
        return 0
    return round(max(0, 14 - days) / 14 * 10)


def _location_score(job: Job) -> int:
    if job.region:
        return 10
    loc = (job.location or "").lower()
    if "remote" in loc:
        return 5
    return 0


def score(job: Job, companies: CompaniesConfig) -> None:
    """Populate job.tier and job.tier_score in place. Assumes job.region set."""
    job.tier = resolve_tier(job.company, companies)
    job.tier_score = (
        TIER_WEIGHT.get(job.tier, 0)
        + _salary_score(job)
        + _employment_score(job.employment_type)
        + _recency_score(job.posted_date)
        + _location_score(job)
    )


def apply(jobs, companies: CompaniesConfig):
    for j in jobs:
        score(j, companies)
        yield j
