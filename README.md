# Merit America Job Scraper

Scrapes **junior / lower-intermediate** LinkedIn job postings across Merit America's
nine career tracks, classifies them into four US regions, scores and tiers each
posting, and writes the top ~50–60 per region to a single **crash-safe master CSV**.

## Career tracks (verticals)

IT support · data analytics · UX design · cybersecurity · project management ·
human resources · supply chain · semiconductor · operations

## How it works

```
LinkedIn (guest endpoint, nationwide)
  → region classify (West Coast / Midwest / South / East Coast)
  → vertical filter (junior/lower-intermediate gate)
  → rank (employer tier + composite score)
  → dedup
  → crash-safe CSV  (output/merit_jobs.csv)
```

- **Employer tiering is hybrid.** A curated registry (`config/companies.yaml`,
  tiers S/A/B/C) is the backbone; companies not in it are graded at runtime via
  the **Brave Search API** (set `BRAVE_API_KEY`). Unknown companies are logged to
  `data/merit_unmatched_companies.log` so you can promote them into the registry.
- **Crash-safe output.** Every scored job is appended to a durable partial
  (`output/_partial/all_scored.csv`); the master CSV is rebuilt **atomically**
  from that partial every 5 minutes, so a mid-run kill never loses data.

## Output columns (`output/merit_jobs.csv`)

| Col | Field | Col | Field |
|-----|-------|-----|-------|
| A | Region | H | Salary Band |
| B | Shortlist (TRUE = top pick) | I | Employment Type |
| C | Role | J | Date Posted |
| D | Company | K | Job URL |
| E | Tier (S/A/B/C) | L | Job Description |
| F | Tier Score | M | Vertical |
| G | Location (City, ST) | | |

## Run it

```bash
pip install -r requirements.txt

# Full run → output/merit_jobs.csv
python -m merit.main

# Smoke test (print only, no writes, no LinkedIn detail fetch)
python -m merit.main --dry-run --limit 12 --no-details

# Low-volume real run
python -m merit.main --limit 40
```

Useful flags: `--per-region N` (default 55), `--shortlist-top N` (default 8),
`--posted-within-days N` (default 14), `--resume` (keep existing partial for
crash recovery).

## Configuration (`config/`)

- **`roles.yaml`** — per-vertical title keywords + two-tier exclusions. Hard
  exclusions always drop (senior/lead/director+, level III/IV); soft exclusions
  (manager/supervisor) drop *unless* a junior signal is present, so
  "Junior Project Manager" survives but "IT Support Manager" doesn't.
- **`companies.yaml`** — employer tier registry. Add companies surfaced in the
  unmatched log.
- **`regions.yaml`** — US state → region map. Pure-remote-no-state and non-US
  postings are dropped (can't fill the Region column).

## GitHub Action

`.github/workflows/scrape.yml` runs weekly and on manual dispatch. It commits
the master CSV + dedup/cache databases back to the repo and uploads the CSV as a
workflow artifact (`if: always()`, so it survives a timeout).

**Required secret:** `BRAVE_API_KEY` (repo → Settings → Secrets → Actions).
Without it, employer grading falls back to tier C for any company not in the
curated registry.
