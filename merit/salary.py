"""Best-effort salary extraction from job description text.

LinkedIn and most Canadian postings either embed salary in the description
("$65,000 - $80,000 CAD annually") or leave it out entirely. We do a
regex sweep and return structured fields when confident; otherwise blanks.

Prefer ATS-native salary fields (Greenhouse/Lever/Workable JSON) and only
fall back to this parser when those are missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Money number: $65,000 | 65,000 | 65k | 65K | 65000 | $65.5k
_MONEY = r"(?:C?A?\$|USD|CAD|US\$)?\s?(\d{2,3}(?:[,\s]\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]|\d{4,6})"
# Range separator: "-", "–", "to"
_SEP = r"\s*(?:-|–|—|to)\s*"
_RANGE_RE = re.compile(rf"{_MONEY}{_SEP}{_MONEY}", re.IGNORECASE)
# Dedicated hourly range, requires $ prefix to avoid "2-5 years" false positives.
_HOURLY_RANGE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?\s?(\d{1,3}(?:\.\d+)?)"
    r"(?:\s*(?:CAD|USD|US\$|CA\$))?\s*(?:/|per)?\s*(?:hour|hr|hourly)",
    re.IGNORECASE,
)

# Currency cues anywhere in the ±80-char neighbourhood of the match.
_CURRENCY_RE = re.compile(r"\b(CAD|USD|CA\$|C\$|US\$|\bU\.S\.)\b", re.IGNORECASE)
# Period cues.
_HOURLY_RE = re.compile(r"\b(per\s*hour|hourly|/\s*hour|/hr|an hour)\b", re.IGNORECASE)
_ANNUAL_RE = re.compile(r"\b(per\s*year|annual(?:ly)?|/\s*year|/yr|a year)\b", re.IGNORECASE)


@dataclass
class Salary:
    min: int | None = None
    max: int | None = None
    currency: str = ""
    period: str = ""
    raw: str = ""


def _parse_money(token: str) -> int | None:
    t = token.strip().upper().replace("$", "").replace(",", "").replace(" ", "")
    for pfx in ("CAD", "USD", "CA", "US", "C", "U"):
        if t.startswith(pfx):
            t = t[len(pfx) :]
    try:
        if t.endswith("K"):
            return int(float(t[:-1]) * 1000)
        if "." in t:
            return int(float(t))
        return int(t)
    except ValueError:
        return None


def extract(text: str, default_currency: str = "CAD") -> Salary:
    """Return best salary match from free text; empty Salary if none found.

    `default_currency` is used when no explicit CAD/USD cue appears near the
    match — "CAD" for Canadian postings, "USD" for US (Merit America).
    """
    if not text:
        return Salary()

    # Hourly pass first (narrower pattern, less ambiguous).
    m = _HOURLY_RANGE_RE.search(text)
    if m:
        try:
            lo = int(float(m.group(1)))
            hi = int(float(m.group(2)))
            if 10 <= lo <= hi <= 300:
                window = text[max(0, m.start() - 80): m.end() + 80]
                cur_match = _CURRENCY_RE.search(window)
                currency = default_currency
                if cur_match:
                    tok = cur_match.group(1).upper().replace(".", "").replace("$", "")
                    if tok in ("USD", "US", "U"):
                        currency = "USD"
                return Salary(min=lo, max=hi, currency=currency,
                              period="hourly", raw=m.group(0).strip())
        except (ValueError, IndexError):
            pass

    # Look for the first plausible range.
    for m in _RANGE_RE.finditer(text):
        lo = _parse_money(m.group(1))
        hi = _parse_money(m.group(2))
        if lo is None or hi is None:
            continue
        # Sanity gates — avoid catching "2023 - 2025" or phone numbers.
        if hi < lo:
            continue
        # Plausible annual: 25k–500k. Plausible hourly: 15–200.
        is_hourlyish = lo < 300 and hi < 400
        is_annualish = lo >= 15000 and hi <= 800_000
        if not (is_hourlyish or is_annualish):
            continue

        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        window = text[start:end]

        period = ""
        if _HOURLY_RE.search(window) or is_hourlyish:
            period = "hourly"
        elif _ANNUAL_RE.search(window) or is_annualish:
            period = "annual"

        cur_match = _CURRENCY_RE.search(window)
        currency = ""
        if cur_match:
            tok = cur_match.group(1).upper().replace(".", "").replace("$", "")
            if tok in ("CAD", "CA", "C"):
                currency = "CAD"
            elif tok in ("USD", "US", "U"):
                currency = "USD"
        # No explicit cue → fall back to the caller's default.
        if not currency:
            currency = default_currency

        return Salary(
            min=lo,
            max=hi,
            currency=currency,
            period=period,
            raw=m.group(0).strip(),
        )
    return Salary()


def format_for_sheet(s: Salary) -> str:
    """Human-readable single cell, e.g. '$65,000 - $80,000 CAD (annual)'."""
    if s.min is None or s.max is None:
        return ""
    if s.period == "hourly":
        body = f"${s.min} - ${s.max} {s.currency} /hr"
    else:
        body = f"${s.min:,} - ${s.max:,} {s.currency}"
        if s.period:
            body += f" ({s.period})"
    return body
