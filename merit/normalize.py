"""Job dataclass + field cleaners. All scrapers yield Job objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Job:
    """Normalized job posting — one row per instance in the sheet."""
    source: str           # "linkedin" | "agency:<name>" | "wellfound"
    job_id: str           # globally unique within source (used for dedup)
    title: str
    company: str
    location: str
    url: str
    description: str = ""          # truncated to ~500 chars when written
    posted_date: str = ""          # ISO date string ("" if unknown)
    seniority_level: str = ""      # from LI metadata
    employment_type: str = ""      # from LI metadata
    job_function: str = ""         # from LI metadata
    company_url: str = ""

    # Salary band — populated when available, else blank.
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""      # "CAD" | "USD" | ""
    salary_period: str = ""        # "annual" | "hourly" | ""
    salary_raw: str = ""           # original matched text (audit aid)

    # Assigned downstream:
    bucket: str = ""               # "senior" | "junior" | "" (dropped)
    region: str = ""               # Merit only: "West Coast"|"Midwest"|"South"|"East Coast"
    vertical: str = ""             # Merit only: career track slug (e.g. "it_support")
    tier: str = "C"
    tier_score: int = 0
    auto_shortlist: bool = False   # set TRUE for top N per bucket by tier_score
    scraped_date: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())

    def dedup_key(self) -> str:
        return f"{self.source}:{self.job_id}"


def truncate(text: str, n: int = 500) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
