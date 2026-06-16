"""SQLite-backed dedup of seen job_ids (Merit America — own DB).

One row per (source, job_id) we've written. Mirrors src/dedup.py but with a
separate database so Merit and Canada runs never collide.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "merit_seen.sqlite"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS seen (
            source TEXT NOT NULL,
            job_id TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            PRIMARY KEY (source, job_id)
        )
        """
    )
    return c


def filter_new(jobs):
    """Yield only jobs whose (source, job_id) is not yet in DB."""
    with _conn() as c:
        for j in jobs:
            cur = c.execute(
                "SELECT 1 FROM seen WHERE source=? AND job_id=?",
                (j.source, j.job_id),
            )
            if cur.fetchone() is None:
                yield j


def mark_seen(jobs: list) -> None:
    today = date.today().isoformat()
    with _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO seen (source, job_id, first_seen) VALUES (?, ?, ?)",
            [(j.source, j.job_id, today) for j in jobs],
        )
        c.commit()


def count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
