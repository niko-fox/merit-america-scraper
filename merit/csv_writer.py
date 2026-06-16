"""Crash-safe CSV output for Merit America.

Two layers:
  1. Append-only partial  (output/_partial/all_scored.csv): every scored,
     deduped job is appended as a fully-rendered row the moment it survives the
     pipeline. Appending is the cheapest durable operation — a mid-run kill
     loses nothing already written.
  2. Atomic master        (output/merit_jobs.csv): rebuilt ENTIRELY from the
     partial on every flush. We write a temp file then os.replace() it into
     place (atomic on POSIX), so a reader never sees a half-written file, and
     the master is always a pure function of the durable partial.

A fresh run rotates any existing partial to a timestamped .bak first; pass
resume=True to keep appending to the existing partial (crash recovery).
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

from merit import regions as regions_mod
from merit.config import RegionsConfig
from merit import salary as salary_mod
from merit.normalize import Job

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
MASTER_PATH = OUTPUT_DIR / "merit_jobs.csv"
PARTIAL_DIR = OUTPUT_DIR / "_partial"
PARTIAL_PATH = PARTIAL_DIR / "all_scored.csv"

# Master columns A–M (M = Vertical, an addition beyond the A–L spec).
HEADERS = [
    "Region", "Shortlist", "Role", "Company", "Tier", "Tier Score",
    "Location", "Salary Band", "Employment Type", "Date Posted",
    "Job URL", "Job Description", "Vertical",
]
# Partial carries the same columns plus a dedup key (kept out of the master).
PARTIAL_HEADERS = HEADERS + ["DedupKey"]

_IDX_REGION = 0
_IDX_SHORTLIST = 1
_IDX_TIER_SCORE = 5


def normalize_employment(raw: str) -> str:
    if not raw:
        return ""
    r = raw.lower()
    if "intern" in r:
        return "Internship"
    if "full" in r and "time" in r:
        return "Full-time"
    if "part" in r and "time" in r:
        return "Part-time"
    if any(k in r for k in ("contract", "temporary", "temp", "freelance", "contractor")):
        return "Contract"
    return raw


def _master_row(job: Job, regions_cfg: RegionsConfig) -> list:
    _region, location_display, _state = regions_mod.classify(job.location, regions_cfg)
    sal = salary_mod.format_for_sheet(
        salary_mod.Salary(
            min=job.salary_min, max=job.salary_max, currency=job.salary_currency,
            period=job.salary_period, raw=job.salary_raw,
        )
    ) or "Not listed"
    return [
        job.region,
        "TRUE" if job.auto_shortlist else "",
        job.title,
        job.company,
        job.tier,
        job.tier_score,
        location_display,
        sal,
        normalize_employment(job.employment_type),
        job.posted_date,
        job.url,
        job.description,
        job.vertical,
    ]


# ---------------------------------------------------------------- partial ----

def init_partial(resume: bool) -> None:
    """Prepare the partial file. Fresh run rotates any existing partial to a
    timestamped backup; resume keeps it in place."""
    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)
    if PARTIAL_PATH.exists() and not resume:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = PARTIAL_DIR / f"all_scored.{ts}.bak.csv"
        PARTIAL_PATH.replace(bak)
        log.info("rotated previous partial to %s", bak.name)
    if not PARTIAL_PATH.exists():
        with PARTIAL_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(PARTIAL_HEADERS)


def append_jobs(jobs: list[Job], regions_cfg: RegionsConfig) -> int:
    """Append fully-rendered rows for `jobs` to the partial. Flushed + fsync'd
    so the bytes hit disk before we move on."""
    if not jobs:
        return 0
    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not PARTIAL_PATH.exists()
    with PARTIAL_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(PARTIAL_HEADERS)
        for j in jobs:
            w.writerow(_master_row(j, regions_cfg) + [j.dedup_key()])
        f.flush()
        os.fsync(f.fileno())
    return len(jobs)


def _read_partial() -> list[list[str]]:
    if not PARTIAL_PATH.exists():
        return []
    with PARTIAL_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[1:] if rows else []  # drop header


def load_partial_keys() -> set[str]:
    """DedupKeys already captured in the partial (for --resume seeding)."""
    keys: set[str] = set()
    for row in _read_partial():
        if row:
            keys.add(row[-1])
    return keys


# ----------------------------------------------------------------- master ----

def rebuild_master(regions_cfg: RegionsConfig, per_region: int = 55,
                   shortlist_top: int = 8) -> dict:
    """Rebuild the curated master CSV from the durable partial, atomically.

    Dedups by key (keeps the highest-scoring instance), keeps the top
    `per_region` rows per region by Tier Score, flags the top `shortlist_top`
    as Shortlist=TRUE, and writes in configured region order.
    """
    rows = _read_partial()

    # Dedup by key, keeping the highest tier_score seen for that key.
    best: dict[str, list[str]] = {}
    for row in rows:
        if len(row) < len(PARTIAL_HEADERS):
            continue
        key = row[-1]
        try:
            sc = float(row[_IDX_TIER_SCORE])
        except (ValueError, IndexError):
            sc = -1
        prev = best.get(key)
        if prev is None or sc > float(prev[_IDX_TIER_SCORE] or -1):
            best[key] = row

    # Group by region, select top N, mark shortlist.
    by_region: dict[str, list[list[str]]] = {r: [] for r in regions_cfg.region_order}
    for row in best.values():
        region = row[_IDX_REGION]
        if region in by_region:
            by_region[region].append(row)

    counts: dict[str, int] = {}
    out_rows: list[list[str]] = []
    for region in regions_cfg.region_order:
        ranked = sorted(
            by_region[region],
            key=lambda r: float(r[_IDX_TIER_SCORE] or -1),
            reverse=True,
        )[:per_region]
        for i, row in enumerate(ranked):
            master = row[:len(HEADERS)]            # drop DedupKey
            master[_IDX_SHORTLIST] = "TRUE" if i < shortlist_top else ""
            out_rows.append(master)
        counts[region] = len(ranked)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MASTER_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(out_rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, MASTER_PATH)  # atomic

    counts["total"] = len(out_rows)
    return counts
