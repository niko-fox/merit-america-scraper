"""Brave-backed employer-quality grading for companies not in the curated
registry (hybrid tiering).

For an unknown company we query the Brave Web Search API once, derive a coarse
prominence signal, and map it to a tier letter (A/B/C). Every result is cached
in data/merit_employer_cache.sqlite so a company is graded at most once across
runs. Misses are appended to data/merit_unmatched_companies.log for manual
promotion into config/merit/companies.yaml.

Fail-open: no API key, network error, or rate-limit -> return "C". The run
never crashes because of employer intel.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DB = DATA_DIR / "merit_employer_cache.sqlite"
UNMATCHED_LOG = DATA_DIR / "merit_unmatched_companies.log"

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# Signals that a company is prominent / well-known.
_AUTHORITY_DOMAINS = (
    "wikipedia.org", "crunchbase.com", "bloomberg.com", "forbes.com",
    "reuters.com", "sec.gov", "linkedin.com/company",
)

_MIN_INTERVAL_S = 1.1  # Brave free tier ~1 req/s.
_last_call = 0.0


def _normalize(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\b(inc|ltd|llc|corp|corporation|company|co|limited|the)\b\.?", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _cache_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CACHE_DB)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS employer_tier (
            name_norm TEXT PRIMARY KEY,
            tier TEXT NOT NULL,
            graded_at TEXT NOT NULL
        )
        """
    )
    return c


def _cache_get(name_norm: str) -> str | None:
    with _cache_conn() as c:
        row = c.execute(
            "SELECT tier FROM employer_tier WHERE name_norm=?", (name_norm,)
        ).fetchone()
        return row[0] if row else None


def _cache_put(name_norm: str, tier: str) -> None:
    from datetime import date
    with _cache_conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO employer_tier (name_norm, tier, graded_at) "
            "VALUES (?, ?, ?)",
            (name_norm, tier, date.today().isoformat()),
        )
        c.commit()


def _log_unmatched(name: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with UNMATCHED_LOG.open("a", encoding="utf-8") as f:
            f.write(name.strip() + "\n")
    except OSError:
        pass


def _rate_limit() -> None:
    global _last_call
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _brave_signal(company: str, api_key: str) -> int:
    """Return a prominence score (0-3) from a single Brave query, or -1 on error."""
    _rate_limit()
    try:
        r = requests.get(
            BRAVE_ENDPOINT,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": f"{company} company", "count": 10},
            timeout=15,
        )
    except requests.RequestException as e:
        log.warning("Brave request error for %r: %s", company, e)
        return -1
    if r.status_code == 429:
        log.warning("Brave rate-limited (429) on %r", company)
        return -1
    if r.status_code != 200:
        log.warning("Brave status %s on %r", r.status_code, company)
        return -1

    try:
        results = r.json().get("web", {}).get("results", []) or []
    except ValueError:
        return -1

    score = 0
    blob = " ".join(
        f"{x.get('url', '')} {x.get('title', '')}".lower() for x in results
    )
    # Authority domains in the result set.
    if any(d in blob for d in _AUTHORITY_DOMAINS):
        score += 1
    # Wikipedia specifically is a strong "well-known" signal.
    if "wikipedia.org" in blob:
        score += 1
    # A healthy result set (Brave returned a full page) suggests a real,
    # searchable employer rather than a tiny local shop.
    if len(results) >= 8:
        score += 1
    return score


def grade(company: str) -> str:
    """Tier letter (A/B/C) for an unregistered company. Fail-open to 'C'."""
    name_norm = _normalize(company)
    if not name_norm:
        return "C"

    cached = _cache_get(name_norm)
    if cached:
        return cached

    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        # No key configured — record the miss, default to C, don't cache
        # (so a later run with a key can still grade it).
        _log_unmatched(company)
        return "C"

    signal = _brave_signal(company, api_key)
    if signal < 0:
        # Transient failure — default C, don't poison the cache.
        return "C"

    # Map prominence score to a conservative tier.
    tier = {3: "A", 2: "B", 1: "B"}.get(signal, "C")
    _cache_put(name_norm, tier)
    if tier == "C":
        _log_unmatched(company)
    return tier
