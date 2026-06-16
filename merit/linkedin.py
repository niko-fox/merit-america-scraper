"""LinkedIn guest-endpoint scraper.

Uses the unauthenticated HTML endpoints:
  - /jobs-guest/jobs/api/seeMoreJobPostings/search  (list)
  - /jobs-guest/jobs/api/jobPosting/{id}            (detail)

Be polite — randomize delays, rotate UA, back off on 429/999.
At 2x/week cadence our footprint is <500 calls/run.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Iterator
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from merit import salary as salary_mod
from merit.http import make_session, polite_sleep, rotate_ua
from merit.normalize import Job, truncate

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
BROWSE_URL = "https://www.linkedin.com/jobs/search"

# LinkedIn location strings — plain text works more reliably than geoId.
LOCATIONS = [
    "Toronto, Ontario, Canada",
    "Vancouver, British Columbia, Canada",
    "Montreal, Quebec, Canada",
]

# f_E experience-level codes: 1=Intern, 2=Entry, 3=Associate, 4=Mid-senior.
# We pull 1,2,3 for junior bucket and 3,4 for senior bucket. Mid-senior (4)
# still yields IC roles; director+ gets caught by our title exclusions.
EXPERIENCE_LEVELS = "1,2,3,4"

# Posted in last 7 days — we run twice/week, so this comfortably covers gaps.
POSTED_WINDOW = "r604800"

_JOBID_RE = re.compile(r"-(\d{8,})")  # tail of /view/title-at-company-<digits>


def _job_id_from_card(card) -> str | None:
    """Extract numeric job id from a card. Tries data-entity-urn first, then href."""
    urn = card.get("data-entity-urn") or card.get("data-id")
    if urn and isinstance(urn, str) and ":" in urn:
        tail = urn.rsplit(":", 1)[-1]
        if tail.isdigit():
            return tail
    a = card.select_one("a.base-card__full-link, a.base-card__full-link--custom")
    if a and a.get("href"):
        m = _JOBID_RE.search(a["href"])
        if m:
            return m.group(1)
    return None


def _parse_search_html(html: str) -> list[dict]:
    """Return a list of lightweight dicts — one per card."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("li div.base-card, li div.job-search-card")
    out: list[dict] = []
    for c in cards:
        job_id = _job_id_from_card(c)
        if not job_id:
            continue
        title_el = c.select_one("h3.base-search-card__title")
        company_el = c.select_one("h4.base-search-card__subtitle a, h4.base-search-card__subtitle")
        loc_el = c.select_one("span.job-search-card__location")
        time_el = c.select_one("time")
        link_el = c.select_one("a.base-card__full-link, a.base-card__full-link--custom")

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = loc_el.get_text(strip=True) if loc_el else ""
        posted = (time_el.get("datetime") if time_el else "") or ""
        url = (link_el.get("href") if link_el else "") or ""
        # Strip tracking params — the clean form is https://www.linkedin.com/jobs/view/<id>
        url = url.split("?", 1)[0]
        company_link = ""
        if company_el and company_el.name == "a":
            company_link = (company_el.get("href") or "").split("?", 1)[0]

        out.append(
            {
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "posted_date": posted,
                "url": url,
                "company_url": company_link,
            }
        )
    return out


def _parse_detail_html(html: str) -> dict:
    """Extract seniority/employment/function + description from detail page."""
    soup = BeautifulSoup(html, "lxml")
    criteria = {}
    for li in soup.select("ul.description__job-criteria-list li"):
        header = li.select_one("h3.description__job-criteria-subheader")
        value = li.select_one("span.description__job-criteria-text")
        if header and value:
            criteria[header.get_text(strip=True).lower()] = value.get_text(strip=True)

    desc_el = soup.select_one(
        "div.show-more-less-html__markup, div.description__text"
    )
    description = desc_el.get_text("\n", strip=True) if desc_el else ""

    return {
        "seniority_level": criteria.get("seniority level", ""),
        "employment_type": criteria.get("employment type", ""),
        "job_function": criteria.get("job function", ""),
        "description": description,
    }


def _get(session: requests.Session, url: str, referer: str | None = None,
         max_retry: int = 1) -> str | None:
    """GET with UA rotation and one soft retry on transient throttle."""
    headers = {}
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Dest"] = "empty"
    rotate_ua(session)
    for attempt in range(max_retry + 1):
        try:
            r = session.get(url, headers=headers, timeout=20)
        except requests.RequestException as e:
            log.warning("request error %s: %s", url, e)
            return None
        if r.status_code == 200:
            return r.text
        if r.status_code in (429, 999, 403):
            log.warning("LI throttle %s on %s (attempt %d)", r.status_code, url, attempt)
            if attempt < max_retry:
                polite_sleep(60, 120)
                continue
            return None
        if r.status_code == 404:
            return None
        log.warning("LI unexpected status %s on %s", r.status_code, url)
        return None
    return None


def _fetch_list(session: requests.Session, keyword: str, location: str,
                start: int, experience_levels: str = EXPERIENCE_LEVELS,
                posted_window: str = POSTED_WINDOW) -> list[dict]:
    params = (
        f"?keywords={quote_plus(keyword)}"
        f"&location={quote_plus(location)}"
        f"&f_TPR={posted_window}"
        f"&f_E={experience_levels}"
        f"&start={start}"
    )
    browse_ref = f"{BROWSE_URL}?keywords={quote_plus(keyword)}&location={quote_plus(location)}"
    html = _get(session, SEARCH_URL + params, referer=browse_ref)
    if not html:
        return []
    return _parse_search_html(html)


def _fetch_detail(session: requests.Session, job_id: str, list_url: str) -> dict:
    url = DETAIL_URL.format(job_id=job_id)
    html = _get(session, url, referer=list_url)
    if not html:
        return {}
    return _parse_detail_html(html)


def scrape(keywords: Iterable[str], *, limit: int | None = None,
           fetch_details: bool = True,
           locations: Iterable[str] | None = None,
           experience_levels: str = EXPERIENCE_LEVELS,
           posted_window: str = POSTED_WINDOW,
           default_currency: str = "CAD") -> Iterator[Job]:
    """Yield Job objects for each (keyword × location) query.

    Args:
        keywords: role search terms (e.g. "copywriter", "graphic designer")
        limit: soft cap on TOTAL jobs. When set, divided fairly across keywords
               so one high-volume term (e.g. "copywriter") can't starve others.
        fetch_details: hit the detail endpoint for seniority + description + salary
        locations: LinkedIn location strings to search. Defaults to the Canada
               cities (LOCATIONS); Merit passes ["United States"] for a
               nationwide sweep, classified into regions downstream.
        experience_levels: LinkedIn f_E codes. Defaults to "1,2,3,4"; Merit
               passes "1,2,3" (intern/entry/associate) for junior focus.
        default_currency: fallback currency for salary parsing ("CAD" | "USD").
    """
    search_locations = list(locations) if locations is not None else LOCATIONS
    session = make_session()
    count = 0
    seen_in_run: set[str] = set()  # within-run dedup across overlapping queries
    consecutive_empty = 0

    keywords = list(keywords)
    # Fair-share: each keyword gets at most (limit / num_keywords) jobs.
    # min 2 so small caps still yield something per term.
    per_term_cap: int | None = None
    if limit:
        per_term_cap = max(2, limit // max(1, len(keywords)))

    # Hard time budget — workflow timeout is 90 min; leave 20 min for
    # rank/dedup/sheet write so we always get SOME output rather than a
    # silent kill mid-scrape that wipes the batch.
    TIME_BUDGET_S = 70 * 60
    started = time.monotonic()

    for kw in keywords:
        if time.monotonic() - started > TIME_BUDGET_S:
            log.warning("scrape time budget reached; stopping at keyword '%s'", kw)
            return
        kw_count = 0
        for loc in search_locations:
            # Fixed 2-page depth (was randomized 2-or-3; one page saves ~20% reqs).
            pages = 2
            list_url = (
                f"{BROWSE_URL}?keywords={quote_plus(kw)}&location={quote_plus(loc)}"
            )
            first_page_size = 0
            for page_ix in range(pages):
                start = page_ix * 10
                cards = _fetch_list(session, kw, loc, start, experience_levels,
                                    posted_window)
                if page_ix == 0:
                    first_page_size = len(cards)
                polite_sleep()

                # Silent-block heuristic: if first page of a normally productive
                # query is empty, LI is likely throttling this IP.
                if page_ix == 0 and first_page_size == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        log.error(
                            "3 consecutive empty first-pages — probable LI throttle; aborting"
                        )
                        return
                    break
                consecutive_empty = 0

                if not cards:
                    break

                for c in cards:
                    key = f"linkedin:{c['job_id']}"
                    if key in seen_in_run:
                        continue
                    seen_in_run.add(key)

                    job = Job(
                        source="linkedin",
                        job_id=c["job_id"],
                        title=c["title"],
                        company=c["company"],
                        location=c["location"],
                        url=c["url"],
                        posted_date=c["posted_date"],
                        company_url=c["company_url"],
                    )

                    if fetch_details:
                        detail = _fetch_detail(session, c["job_id"], list_url)
                        polite_sleep()
                        job.seniority_level = detail.get("seniority_level", "")
                        job.employment_type = detail.get("employment_type", "")
                        job.job_function = detail.get("job_function", "")
                        full_desc = detail.get("description", "")
                        # Salary: scan full (untruncated) description.
                        s = salary_mod.extract(full_desc, default_currency=default_currency)
                        job.salary_min = s.min
                        job.salary_max = s.max
                        job.salary_currency = s.currency
                        job.salary_period = s.period
                        job.salary_raw = s.raw
                        job.description = truncate(full_desc, 500)

                    yield job
                    count += 1
                    kw_count += 1
                    if limit and count >= limit:
                        return
                    if per_term_cap and kw_count >= per_term_cap:
                        break
                if per_term_cap and kw_count >= per_term_cap:
                    break
            if per_term_cap and kw_count >= per_term_cap:
                break
