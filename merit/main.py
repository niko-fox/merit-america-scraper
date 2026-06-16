"""Merit America orchestrator: scrape → region → vertical → rank → dedup →
crash-safe CSV.

Usage:
    python -m merit.main                      # full run → output/merit_jobs.csv
    python -m merit.main --dry-run            # print only, no file writes/dedup
    python -m merit.main --limit 40           # cap total jobs (smoke / low-vol)
    python -m merit.main --no-details         # skip detail fetch (no salary/desc)
    python -m merit.main --resume             # keep existing partial (crash recovery)

One run sweeps all nine verticals nationwide, classifies into four US regions,
and curates the top ~50–60 per region into a single master CSV. Partial results
are flushed to disk every 5 minutes so a mid-run kill never loses data.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

from merit import config as cfg_mod
from merit import csv_writer, dedup, filter as filter_mod, rank, regions as regions_mod
from merit.normalize import Job
from merit import linkedin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("merit.main")

DEFAULT_POSTED_WITHIN_DAYS = 14
DEFAULT_PER_REGION = 55
DEFAULT_SHORTLIST_TOP = 8
FLUSH_INTERVAL_S = 5 * 60          # user requirement: save every 5 minutes
POSTED_WINDOW_14D = "r1209600"     # LinkedIn f_TPR: jobs posted in last 14 days

# Broad LinkedIn search terms per vertical. The title-level vertical filter
# (config/merit/roles.yaml) does the precision work, so keep these wide.
LINKEDIN_SEARCH_TERMS = [
    # IT support
    "help desk", "it support", "desktop support", "technical support",
    # Data analytics
    "data analyst", "business analyst", "business intelligence analyst",
    # UX design
    "ux designer", "ui designer", "product designer",
    # Cybersecurity
    "security analyst", "soc analyst", "cybersecurity analyst",
    # Project management
    "project coordinator", "junior project manager", "project analyst",
    # Human resources
    "hr coordinator", "hr generalist", "recruiting coordinator", "hr assistant",
    # Supply chain
    "supply chain analyst", "logistics coordinator", "procurement analyst",
    # Semiconductor
    "semiconductor technician", "manufacturing technician",
    "process technician", "equipment technician",
    # Operations
    "operations analyst", "operations coordinator", "operations associate",
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Print results; skip CSV writes + dedup insert.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap total jobs scraped (smoke / low-volume).")
    p.add_argument("--no-details", action="store_true",
                   help="Skip LinkedIn detail fetch (no salary/description).")
    p.add_argument("--per-region", type=int, default=DEFAULT_PER_REGION,
                   help=f"Max jobs kept per region (default {DEFAULT_PER_REGION}).")
    p.add_argument("--shortlist-top", type=int, default=DEFAULT_SHORTLIST_TOP,
                   help=f"Flag top N per region as Shortlist (default {DEFAULT_SHORTLIST_TOP}).")
    p.add_argument("--posted-within-days", type=int, default=DEFAULT_POSTED_WITHIN_DAYS,
                   help=f"Drop jobs older than N days (default {DEFAULT_POSTED_WITHIN_DAYS}).")
    p.add_argument("--resume", action="store_true",
                   help="Keep the existing partial file instead of rotating it.")
    return p.parse_args()


def _make_is_recent(posted_within_days: int):
    if posted_within_days <= 0:
        return lambda j: True
    cutoff = date.today() - timedelta(days=posted_within_days)

    def is_recent(j: Job) -> bool:
        if not j.posted_date:
            return True
        try:
            d = datetime.fromisoformat(j.posted_date.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                d = date.fromisoformat(j.posted_date[:10])
            except ValueError:
                return True
        return d >= cutoff
    return is_recent


def _process_batch(buffer: list[Job], args, roles_cfg, companies_cfg,
                   regions_cfg, is_recent) -> list[Job]:
    """Run the pipeline on a buffered batch; return jobs written to the partial."""
    if not buffer:
        return []

    # 1. Region classify — drop anything we can't place in one of the 4 regions.
    placed: list[Job] = []
    for j in buffer:
        region, _display, _state = regions_mod.classify(j.location, regions_cfg)
        if region is None:
            continue  # pure-remote-no-state / non-US / unmapped → unplaceable
        j.region = region
        placed.append(j)

    # 2. Vertical filter (junior/lower-intermediate gate) — sets j.vertical.
    placed = list(filter_mod.apply(placed, roles_cfg))
    # 3. Recency.
    placed = [j for j in placed if is_recent(j)]
    # 4. Dedup (skip on dry-run so we still see output).
    new = placed if args.dry_run else list(dedup.filter_new(placed))
    if not new:
        return []
    # 5. Rank — tier letter (registry → Brave) + composite score. Only here so
    #    Brave is queried for genuinely new, in-scope postings.
    new = list(rank.apply(new, companies_cfg))

    if args.dry_run:
        for j in sorted(new, key=lambda x: x.tier_score, reverse=True)[:25]:
            print(
                f"[{j.tier}/{j.tier_score:>3}] {j.region:<11} {j.vertical:<18} | "
                f"{j.title} @ {j.company} | {j.location}"
            )
    else:
        csv_writer.append_jobs(new, regions_cfg)
        dedup.mark_seen(new)
    return new


def main():
    args = parse_args()

    roles_cfg = cfg_mod.load_roles()
    companies_cfg = cfg_mod.load_companies()
    regions_cfg = cfg_mod.load_regions()
    is_recent = _make_is_recent(args.posted_within_days)

    if not args.dry_run:
        csv_writer.init_partial(resume=args.resume)

    buffer: list[Job] = []
    last_flush = time.monotonic()
    total_written = 0

    def flush(reason: str) -> None:
        nonlocal last_flush, total_written
        if not buffer:
            return
        log.info("flush (%s): %d buffered jobs", reason, len(buffer))
        written = _process_batch(buffer, args, roles_cfg, companies_cfg,
                                 regions_cfg, is_recent)
        total_written += len(written)
        buffer.clear()
        if not args.dry_run:
            counts = csv_writer.rebuild_master(
                regions_cfg, per_region=args.per_region,
                shortlist_top=args.shortlist_top,
            )
            log.info("  master rebuilt: %s", counts)
        last_flush = time.monotonic()

    def maybe_flush() -> None:
        if time.monotonic() - last_flush >= FLUSH_INTERVAL_S:
            flush("interval")

    log.info("scraping LinkedIn nationwide (%d search terms)", len(LINKEDIN_SEARCH_TERMS))
    for job in linkedin.scrape(
        LINKEDIN_SEARCH_TERMS,
        limit=args.limit,
        fetch_details=not args.no_details,
        locations=["United States"],
        experience_levels="1,2,3",        # intern / entry / associate
        posted_window=POSTED_WINDOW_14D,
        default_currency="USD",
    ):
        buffer.append(job)
        maybe_flush()

    flush("final")

    log.info("run complete: %d new rows captured this run", total_written)
    if not args.dry_run:
        counts = csv_writer.rebuild_master(
            regions_cfg, per_region=args.per_region,
            shortlist_top=args.shortlist_top,
        )
        log.info("final master: %s -> %s", counts, csv_writer.MASTER_PATH)
        if total_written < 3:
            log.warning("Only %d new jobs — possible LI block or dedup saturation.",
                        total_written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
