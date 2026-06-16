"""Shared HTTP session with UA rotation and jittered sleep.

Keep this lightweight — all scrapers share one Session per run to reuse the
TCP connection pool. LinkedIn is the only source that needs careful pacing;
agency JSON APIs can be hit at normal speed.
"""
from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

# Small pool of real modern desktop browsers. Keep fresh every ~6 months.
USER_AGENTS = [
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Safari / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Firefox / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return s


def rotate_ua(session: requests.Session) -> None:
    session.headers["User-Agent"] = random.choice(USER_AGENTS)


def polite_sleep(lo: float = 4.0, hi: float = 10.0) -> None:
    """Jittered sleep between requests to a single origin."""
    d = random.uniform(lo, hi)
    log.debug("sleep %.2fs", d)
    time.sleep(d)
