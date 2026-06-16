"""US location parsing + region classification for Merit America.

LinkedIn location strings look like:
  "Austin, TX"               -> ("Austin", "TX")
  "Austin, Texas"            -> ("Austin", "TX")
  "Austin, Texas, United States"
  "San Francisco Bay Area"   -> ("San Francisco", "CA")  (metro alias)
  "United States (Remote)"   -> (None, None)             (unplaceable)
  "Remote"                   -> (None, None)
"""
from __future__ import annotations

import re

from merit.config import RegionsConfig

US_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington d.c.": "DC",
}
_VALID_ABBR = set(US_STATE_ABBR.values())

# Common LinkedIn "metro / area" strings → representative (city, state).
METRO_ALIASES = {
    "san francisco bay area": ("San Francisco", "CA"),
    "greater san francisco": ("San Francisco", "CA"),
    "silicon valley": ("San Jose", "CA"),
    "greater los angeles": ("Los Angeles", "CA"),
    "greater seattle area": ("Seattle", "WA"),
    "greater seattle": ("Seattle", "WA"),
    "greater new york city area": ("New York", "NY"),
    "new york city metropolitan area": ("New York", "NY"),
    "greater boston": ("Boston", "MA"),
    "greater chicago area": ("Chicago", "IL"),
    "greater chicago": ("Chicago", "IL"),
    "dallas-fort worth metroplex": ("Dallas", "TX"),
    "dallas-fort worth": ("Dallas", "TX"),
    "greater houston": ("Houston", "TX"),
    "greater phoenix area": ("Phoenix", "AZ"),
    "greater philadelphia": ("Philadelphia", "PA"),
    "washington dc-baltimore area": ("Washington", "DC"),
    "atlanta metropolitan area": ("Atlanta", "GA"),
    "greater minneapolis-st. paul area": ("Minneapolis", "MN"),
    "austin, texas metropolitan area": ("Austin", "TX"),
    "denver metropolitan area": ("Denver", "CO"),
}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def parse_city_state(raw: str) -> tuple[str | None, str | None]:
    """Best-effort (city, 2-letter state) from a LinkedIn location string.

    Returns (None, None) when no US state can be determined.
    """
    if not raw:
        return None, None
    text = raw.strip()
    lc = text.lower()

    # Metro alias hit (check before stripping).
    for alias, (city, st) in METRO_ALIASES.items():
        if alias in lc:
            return city, st

    # Strip work-mode suffixes: "(Remote)", "(Hybrid)", "(On-site)".
    text = re.sub(r"\s*\((remote|hybrid|on-?site|in office)\)\s*", "", text,
                  flags=re.IGNORECASE).strip()

    parts = [p.strip() for p in text.split(",") if p.strip()]
    # Drop a trailing "United States" / "USA".
    if parts and parts[-1].lower() in ("united states", "usa", "us", "u.s.", "u.s.a."):
        parts = parts[:-1]
    if not parts:
        return None, None

    # Find a state token scanning from the end (state usually follows city).
    city: str | None = None
    state: str | None = None
    for i in range(len(parts) - 1, -1, -1):
        tok = parts[i]
        tl = tok.lower()
        if tok.upper() in _VALID_ABBR:
            state = tok.upper()
        elif tl in US_STATE_ABBR:
            state = US_STATE_ABBR[tl]
        if state is not None:
            if i > 0:
                city = parts[i - 1]
            break

    if state is None:
        # No resolvable state (e.g. bare "Remote" or non-US).
        return None, None
    return city, state


def is_remote(raw: str) -> bool:
    return bool(raw) and bool(_REMOTE_RE.search(raw))


def region_for(state: str | None, cfg: RegionsConfig) -> str | None:
    """Map a 2-letter state to its region, or None if unknown."""
    if not state:
        return None
    return cfg.state_to_region.get(state.upper())


def classify(raw_location: str, cfg: RegionsConfig
             ) -> tuple[str | None, str, str | None]:
    """Resolve a raw location to (region, 'City, ST' display, state).

    region is None when the posting cannot be placed (e.g. pure remote with no
    state, or a non-US location). Caller decides whether to drop.
    """
    city, state = parse_city_state(raw_location)
    region = region_for(state, cfg)
    if state:
        display = f"{city}, {state}" if city else state
        if is_remote(raw_location):
            display += " (Remote)"
    elif is_remote(raw_location):
        display = "Remote"
    else:
        display = (raw_location or "").strip()
    return region, display, state
