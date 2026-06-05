# DoD Contract Intelligence Agent

Monitors DoD contract awards and generates **Buffett/Graham-style analyst research reports** identifying publicly traded companies with durable government revenue exposure.

> ⚠️ **For research purposes only. Not investment advice.**

---

## Quick Start

### 1. First-time setup (run once)

```bash
cd dod_contract_agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run (live contracts + live fundamentals, default)

```bash
source venv/bin/activate
python3 main.py
```

### 3. Offline / mock mode (no internet required)

```bash
source venv/bin/activate
python3 main.py --source mock --no-live
```

---

## All Commands

```bash
# Default: live USAspending contracts + live yfinance fundamentals
python3 main.py

# ── Recommended: high-conviction runs ────────────────────────────────────────
# USAspending lags 1–3 weeks, so extend the window to fill the pipeline

# 90-day window — more contracts, better company coverage
python3 main.py --days 90

# Specialist sweet spot only: mid-cap ($400M–$15B), high DoD concentration
python3 main.py --days 90 --specialist-only

# High-conviction threshold (score ≥ 65)
python3 main.py --days 90 --min-score 65

# Both filters combined — tightest, most actionable output
python3 main.py --days 90 --specialist-only --min-score 65
# ─────────────────────────────────────────────────────────────────────────────

# Top 10 companies only
python3 main.py --top 10

# Also save a JSON scores file
python3 main.py --json

# Pull 10-K data from SEC EDGAR (real gov revenue % and backlog)
python3 main.py --edgar

# Print scores to terminal only, skip full report
python3 main.py --no-report

# Custom report output path
python3 main.py --output reports/my_report.md

# Scrape defense.gov directly instead of USAspending (same-day)
python3 main.py --source live

# Offline mode — no internet required (mock contracts + mock fundamentals)
python3 main.py --source mock --no-live
```

> **Every session:** run `source venv/bin/activate` first if your terminal prompt doesn't show `(venv)`.

---

## What It Does

1. **Ingests** DoD contract awards (mock data or live from USAspending.gov / defense.gov)
2. **Parses** each contract: awardee, value, funded amount, type, agency, sector
3. **Maps** awardees to public tickers — first via `ticker_map.yaml` (subsidiaries, legacy names, known mappings), then via automatic fuzzy-match against the full SEC EDGAR company index as a fallback
4. **Classifies** contracts into sectors (defense, cyber, healthcare, AI, logistics, etc.)
5. **Scores** each company across 6 weighted dimensions
6. **Generates** a ranked markdown report with full score transparency

Reports are saved to `reports/report_YYYYMMDD_HHMM.md` — open in VS Code, Obsidian, or any markdown viewer.

---

## Scoring Framework (0–100)

| Component | Weight | What It Measures |
|-----------|--------|-----------------|
| **Buffett Quality** | 25% | Economic moat, ROIC, FCF margin, operating margin, earnings stability, ROE |
| **Graham Value** | 20% | P/E, forward P/E, EV/EBITDA, FCF yield, Price-to-Book, current ratio, earnings track record |
| **DoD Stability** | 20% | DoD revenue %, backlog/revenue, sole-source position, sector durability. If DoD% is unknown, a conservative sector-based estimate is used and flagged. |
| **Management Quality** | 15% | ROIC, FCF execution, earnings consistency, insider ownership, debt discipline |
| **Contract Catalyst** | 10% | Contract/revenue ratio, funded %, IDIQ haircut, sole-source, duration |
| **Balance Sheet** | 10% | Current ratio, Debt/EBITDA, interest coverage, net debt position |

### Verdict Scale

| Score | Verdict |
|-------|---------|
| ≥ 85 | 🟢 Strong Candidate |
| 75–84 | 🟡 Potentially Attractive |
| 65–74 | 🔵 Watchlist |
| 50–64 | ⚪ Low Conviction |
| < 50 | 🔴 Ignore |

---

## Project Structure

```
dod_contract_agent/
├── README.md                  ← this file
├── requirements.txt
├── main.py                    ← CLI entry point
├── config.py                  ← weights, thresholds, paths, sector keywords
├── setup_live.sh              ← one-shot setup script
├── data/
│   ├── ticker_map.yaml        ← subsidiary → public ticker mapping (editable)
│   ├── sample_contracts.json  ← mock contract data (16 examples)
│   ├── mock_fundamentals.json ← mock financial data per ticker
│   ├── edgar_company_index.json  ← auto-generated: SEC company index cache (refreshed weekly)
│   └── resolved_cache.json      ← auto-generated: EDGAR lookup results cache
├── src/
│   ├── models.py              ← data classes
│   ├── parse_contracts.py     ← load from JSON or scrape defense.gov
│   ├── fetch_usaspending.py   ← USAspending.gov API client
│   ├── entity_resolution.py  ← awardee name → ticker mapping
│   ├── edgar_company_lookup.py ← automatic ticker resolution via SEC company index
│   ├── classify_sector.py     ← keyword-based sector classifier
│   ├── fundamentals.py        ← load fundamentals (mock or yfinance)
│   ├── edgar.py               ← SEC EDGAR 10-K fetcher
│   ├── scoring.py             ← full scoring engine (6 components)
│   ├── dcf.py                 ← DCF / owner earnings valuation
│   └── report.py              ← markdown report generator
└── reports/                   ← generated reports saved here
```

---

## Customization

### Ticker resolution (automatic + manual)

Most public companies are resolved automatically: the tool first checks `ticker_map.yaml`, then falls back to fuzzy-matching against the full SEC EDGAR company index (~10k companies). Results are cached in `data/resolved_cache.json`.

You only need to edit `ticker_map.yaml` for **subsidiaries or legacy names** where the awardee name has no obvious relationship to the parent ticker — e.g. "Bath Iron Works" → GD, "URS Group" → ACM (AECOM acquisition), or confirmed private companies you want to suppress from the unknown list.

```yaml
subsidiary name lowercase:
  ticker: TICK
  parent: Parent Company Name
  confidence: 0.95
  notes: optional
```

To force a re-resolution of a previously cached awardee name (e.g. after adding it to `ticker_map.yaml`), delete `data/resolved_cache.json`.

### Improve fundamentals for a specific company

`data/mock_fundamentals.json` is a curated overlay — it doesn't replace yfinance, it fills the gaps yfinance can't provide. When `--live` is active (the default), yfinance supplies real-time prices and margins; the overlay then patches in the fields yfinance lacks.

**Fields that always come from the overlay** (yfinance is unreliable for these):
- `earnings_stability_years` — yfinance is capped at 4 years of history; the overlay holds the real track record (e.g. 20 years for AT&T)

**Fields the overlay adds when yfinance has nothing**:
- `government_revenue_pct`, `dod_revenue_pct`, `backlog_to_revenue`, `moat_rating`, `roic`

Add an entry to the overlay whenever a new public company shows up via EDGAR auto-resolution and you want accurate DoD revenue / backlog / moat data in the score. The minimum useful entry is just the DoD-specific fields:

```json
"TICK": {
  "company_name": "Company Name",
  "dod_revenue_pct": 45,
  "government_revenue_pct": 60,
  "backlog_to_revenue": 1.8,
  "moat_rating": "Narrow",
  "earnings_stability_years": 12
}
```

### Adjust scoring weights
Edit `config.py` → `SCORE_WEIGHTS`. Must sum to 1.0.

### Adjust specialist tier thresholds
Edit `config.py` → `SPECIALIST_TIER`. Controls market cap band, min DoD %, and score bonus.

---

## Data Sources

| Source | Command flag | Notes |
|--------|-------------|-------|
| USAspending.gov API | *(default)* | Free, no API key, best structured data. Paginates up to 500 results. Data typically lags 1–3 weeks. |
| yfinance | *(default)* | Live prices, margins, ratios — fetched automatically |
| defense.gov scrape | `--source live` | Same-day announcements |
| SEC EDGAR | `--edgar` | Real gov revenue % and backlog from 10-Ks |
| Mock data | `--source mock --no-live` | Offline, 16 sample contracts — no internet required |

---

## Caveats

1. **Fundamentals overlay** (`mock_fundamentals.json`) covers ~22 well-known companies. Any ticker resolved via EDGAR that isn't in the overlay will have its DoD revenue %, backlog, and moat rating estimated or missing — add an entry to improve its score accuracy.
2. **DoD revenue % estimation** — when a company's DoD% isn't in the overlay, the tool estimates it from the contract sector (e.g. Shipbuilding → ~51%, Infrastructure → ~25%) with a 45% discount and a flag. Treat estimated DoD% scores as directional only.
3. **IDIQ ceilings ≠ revenue.** The funded amount is what matters. Ceiling-only IDIQs are penalized aggressively in the Contract Catalyst score.
4. **Contract modifications** may include previously counted work.
5. **Ticker mapping** uses a two-layer system: a manual `ticker_map.yaml` for subsidiaries/legacy names, then automatic fuzzy-match against the SEC EDGAR company index. Low-confidence matches (< 0.70) are flagged. The EDGAR index is cached locally and refreshed weekly.
6. **Sector classification** is keyword-based and uses word-boundary matching (so "engine" does not match "engineering"). Common Superfund/remediation terminology is covered; unusual contract language may still fall through to "Unclear."
7. **Valuation multiples** change daily. Scores are snapshots.
8. **This is not investment advice.**

---

## Design Philosophy

> *"A great company at a fair price beats a fair company at a great price."*

- Contract size alone cannot make a mediocre company attractive
- Extreme valuation multiples are penalized regardless of growth
- IDIQ ceilings are discounted aggressively vs. actually funded work
- Sole-source positions in critical national security programs receive structural moat credit
- Non-traditional DoD sectors (healthcare, pharma, logistics) are weighted fairly

This is a **screening tool**, not a trading signal.
