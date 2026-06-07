# DoD Contract Intelligence Agent

A Buffett/Graham-style investment screening tool that ingests U.S. Department of Defense contract awards from USAspending.gov and produces a ranked, analyst-grade markdown report covering publicly traded companies with durable government revenue exposure.

> **Disclaimer:** For research and informational purposes only. Not investment advice. Verify all data independently. Consult a licensed financial advisor before making any investment decisions.

---

## Quick Start

```bash
git clone https://github.com/jamesadelhelm/dod-contract-alpha.git
cd dod-contract-alpha
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run (live USAspending contracts + live yfinance fundamentals)
python3 main.py
```

Reports are written to `reports/report_YYYYMMDD_HHMM.md`.

---

## All Commands

```bash
# ── Default: USAspending fiscal year + live fundamentals ─────────────────────
python3 main.py

# ── Filters ──────────────────────────────────────────────────────────────────
python3 main.py --min-score 65          # only companies scoring >= 65
python3 main.py --top 10                # top 10 by score
python3 main.py --specialist-only       # mid-cap, high-DoD-concentration only

# ── Output ───────────────────────────────────────────────────────────────────
python3 main.py --output reports/my_report.md
python3 main.py --json                  # also emit a JSON scores file
python3 main.py --no-report             # print scores to terminal, skip report

# ── Data sources ─────────────────────────────────────────────────────────────
python3 main.py --source live           # scrape defense.gov (same-day announcements)
python3 main.py --source mock           # offline: mock contracts + live fundamentals
python3 main.py --source mock --no-live # fully offline: no network calls

# ── Enrichment ───────────────────────────────────────────────────────────────
python3 main.py --edgar                 # pull gov revenue % and backlog from SEC 10-Ks
```

> **Every session:** run `source venv/bin/activate` first if your terminal prompt doesn't show `(venv)`.

---

## What It Does

1. **Fetches DoD contract awards** from USAspending.gov — current fiscal year (Oct 1 → today), up to 1,000 contracts sorted by value descending. Uses fiscal year mode by default because USAspending has a 30-90 day processing lag; a "last N days" window often returns near-zero results.
2. **Resolves awardees to public tickers** via a 3-pass pipeline: exact match in a curated subsidiary map (700+ entries) → prefix/fuzzy match → SEC EDGAR company index fallback.
3. **Fetches live fundamentals** from yfinance: prices, margins, multiples, short interest, dividend yield, share count change YoY, next earnings date, analyst consensus, 52-week range.
4. **Scores each company** on 6 weighted components (0–100 scale each).
5. **Runs a 3-scenario DCF** (bear/base/bull) with growth anchored to the company's actual recent revenue growth blended with sector defaults.
6. **Generates a markdown report** with 10 sections covering rankings, deep dives, valuation, analyst consensus, short interest, capital return signals, sector peers, red flags, and private-company coverage gaps.

---

## Scoring Framework (0–100)

| Component | Weight | What It Measures |
|-----------|--------|-----------------|
| **Buffett Quality** | 25% | Economic moat, ROIC, FCF margin, operating margin, earnings stability, ROE |
| **Graham Value** | 20% | P/E, Fwd P/E, EV/EBITDA, FCF yield, Price-to-Book, current ratio |
| **DoD Stability** | 20% | DoD revenue %, backlog/revenue, sole-source position, sector durability |
| **Management Quality** | 15% | ROIC, FCF execution, earnings consistency, insider ownership, debt discipline |
| **Contract Catalyst** | 10% | Contract/revenue ratio, funded %, IDIQ haircut, sole-source, contract duration |
| **Balance Sheet** | 10% | Current ratio, Debt/EBITDA, interest coverage, net debt position |

```
Final Score = Buffett(25%) + Graham(20%) + DoD(20%) + Management(15%) + Catalyst(10%) + BalanceSheet(10%)
```

### Verdict Scale

| Score | Verdict |
|-------|---------|
| >= 85 | 🟢 Strong Candidate |
| 75–84 | 🟡 Potentially Attractive |
| 75–84 (Street bearish) | 🟡 Research Further — quality conflicts with analyst consensus |
| 75–84 (P/E > 80x or EV/EBITDA > 60x) | 🟠 High Quality But Expensive |
| 65–74 | 🔵 Watchlist |
| 50–64 | ⚪ Low Conviction |
| < 50 | 🔴 Ignore |

### Score Override Rules

| Condition | Override |
|-----------|---------|
| Negative FCF **and** negative operating margin | Buffett score capped at 45 |
| IDIQ with < 25% funded | Catalyst score capped at 40 |
| Current ratio < 1.0 **and** Debt/EBITDA > 4.0 | Final score capped at 65 |

---

## Red Flags

| Condition | Threshold | Flag |
|-----------|-----------|------|
| Analyst consensus | Sell/Underperform, >= 3 analysts | Street is negative — verify thesis |
| Operating margin | Contracting > 3pp YoY | Cost pressure / contract mix shift |
| Gross margin | Contracting > 2pp YoY | Pricing power erosion |
| Short interest | > 15% of float | Elevated informed short position |
| Short interest | > 25% of float | Significant — identify and refute the short thesis |
| Share count | +5% YoY dilution | Dilution destroying equity value |
| Earnings proximity | <= 14 days to next report | Binary catalyst — size accordingly |
| Current ratio | < 1.0 | Liquidity concern |
| Leverage | Debt/EBITDA > 4.0x | High leverage |
| Interest coverage | < 1.5x | Dangerously low |
| IDIQ ceiling | Funded < 25% of ceiling | Aspirational ceiling, not committed revenue |

---

## DCF Valuation

Each company gets a 3-scenario (bear/base/bull) 10-year DCF plus a reverse DCF (implied growth rate at current price).

| Parameter | Method |
|-----------|--------|
| Owner earnings | FCF margin × revenue; falls back to revenue-based projection if FCF negative |
| Discount rate | 9% base, adjusted for DoD concentration, moat, leverage, size, profitability |
| Year 1–5 growth | 60% actual 1yr revenue growth + 40% sector default; clipped to −10%/+60% |
| Year 6–10 growth | Mean-reverts to sector long-run rate |
| Terminal growth | 2.5–3.5% depending on sector and DoD concentration |
| Reverse DCF | Binary search for implied constant growth at current market price |

Reports include a caveat noting when growth is company-anchored, with explicit bear/base/bull year 1–5 rates.

---

## Ticker Resolution

Awardee names in USAspending are often subsidiaries, divisions, or legacy entity names. Resolution runs in 3 passes:

1. **Exact match** in `data/ticker_map.yaml` (700+ entries, normalized lowercase)
2. **Prefix/fuzzy match** — catches parent brand at the start of subsidiary names. Forces similarity >= 0.55 when the parent key is >= 6 characters and the awardee starts with it (e.g., `HUMANA GOVERNMENT BUSINESS INC` → HUM).
3. **SEC EDGAR fallback** — queries the full SEC company index (~10,000+ tickers), cached locally in `data/resolved_cache.json`

Matches below 0.70 confidence are flagged in the report. Private/unmatched awardees appear in Section 9 with total contract value.

**Key subsidiary mappings:**

| Awardee Name (USAspending) | Ticker | Notes |
|----------------------------|--------|-------|
| HUMANA GOVERNMENT BUSINESS INC | HUM | TRICARE |
| HEALTH NET FEDERAL SERVICES LLC | CNC | Centene, TRICARE West |
| UNITEDHEALTH MILITARY & VETERANS SERVICES | UNH | TRICARE East |
| VERTEX AEROSPACE LLC / VECTRUS SYSTEMS | VVX | V2X post-merger |
| NATIONAL STEEL AND SHIPBUILDING COMPANY | GD | NASSCO subsidiary |
| ELECTRIC BOAT CORPORATION, BATH IRON WORKS | GD | General Dynamics |
| GENERAL DYNAMICS ORDNANCE AND TACTICAL SYSTEMS | GD | General Dynamics |
| FLUOR MARINE PROPULSION LLC | FLR | Fluor |
| OLIN WINCHESTER LLC | OLN | Olin Corporation |
| CACI INTERNATIONAL | CACI | — |
| SPACE EXPLORATION TECHNOLOGIES CORP. | null | SpaceX is private; SPCX is an ETF |
| TRIWEST HEALTHCARE ALLIANCE | null | Private (Health Care Service Corp JV) |
| BECHTEL (all entities) | null | Private |
| GENERAL ATOMICS AERONAUTICAL SYSTEMS | null | Private |
| SIERRA NEVADA COMPANY | null | Private |
| MITRE CORPORATION, AEROSPACE CORPORATION | null | FFRDC nonprofit |
| PERATON | null | Private (Veritas Capital) |

---

## Specialist Tier

The "specialist sweet spot" is mid-cap companies with high DoD concentration — the segment where contract signals are most actionable before institutional coverage catches up.

**Criteria:** Market cap $400M–$15B | DoD revenue >= 35% | Contract value >= 3% of annual revenue

| Status | Score Bonus |
|--------|-------------|
| In Tier (all 3 criteria) | +6 pts |
| Near Tier (2 of 3 criteria) | +3 pts |
| Large Prime (LMT, NOC, RTX, GD, BA, HII, LHX, TXT, L3H) | 0 — excluded; already priced in |
| Too Small (< $400M) | 0 — liquidity/sizing concern |

---

## Report Structure

| Section | Content |
|---------|---------|
| 1 | Executive Summary — verdict distribution |
| 2 | Top Ranked Companies — score, data quality %, margin of safety |
| 3 | New Contract Signals — all contracts analyzed |
| 3b | Specialist Tier — in-tier, near-tier, large primes |
| 4 | Buffett/Graham Deep Dives — score breakdown, contracts, narrative, short interest, next earnings, red flags |
| 5 | Government Funding Durability — DoD%, gov%, backlog, moat, sole source |
| 6a | Market Multiples — P/E, Fwd P/E, EV/EBITDA, FCF yield, div yield, share change YoY, D/E |
| 6b | DCF Summary — bear/base/bull intrinsic value, margin of safety, implied growth rate |
| 6c | DCF Detail — per company: discount rate build-up, scenario table, reverse DCF, caveats |
| 6d | Analyst Consensus & Momentum — price, 52W range, % off high, 1yr return, short %, days to cover, analyst target, upside, consensus, next earnings |
| 6e | Sector Peer Comparison — each company vs. sector median P/E, EV/EBITDA, FCF yield |
| 7 | Red Flags — all flagged conditions |
| 8 | Research Further — buy-tier and watch-tier with suggested diligence steps |
| 9 | Private / No Ticker — unmatched awardees with total contract value |
| 10 | Data Quality Caveats |

---

## Data Sources

| Source | Default? | Notes |
|--------|----------|-------|
| USAspending.gov API | Yes | Current DoD fiscal year (Oct 1 → today), sorted by contract value, up to 1,000 contracts. No API key. |
| yfinance | Yes | Live prices, margins, multiples, short interest, dividend yield, share count, earnings calendar, analyst consensus |
| `data/mock_fundamentals.json` | Yes (overlay) | Curated supplement: gov revenue %, DoD %, backlog, moat rating, earnings stability years |
| defense.gov HTML | `--source live` | Same-day announcements. Uses `curl_cffi` for Cloudflare bypass. |
| SEC EDGAR 10-Ks | `--edgar` | Real government revenue % and backlog from annual filings |
| Mock data | `--source mock` | Offline testing via `data/sample_contracts.json` |

---

## Project Structure

```
dod_contract_agent/
├── main.py                        # CLI entry point
├── config.py                      # Scoring weights, thresholds, specialist tier config
├── requirements.txt
├── data/
│   ├── ticker_map.yaml            # 700+ awardee -> ticker mappings (editable)
│   ├── mock_fundamentals.json     # Curated overlay: gov%, DoD%, backlog, moat
│   ├── sample_contracts.json      # Mock contract data for offline testing
│   ├── edgar_company_index.json   # Auto-generated: SEC company index cache
│   └── resolved_cache.json        # Auto-generated: EDGAR lookup results cache
├── src/
│   ├── models.py                  # Dataclasses: Contract, CompanyFundamentals, CompanyScore
│   ├── fetch_usaspending.py       # USAspending.gov API client (fiscal year mode)
│   ├── parse_contracts.py         # Contract parsing and enrichment pipeline
│   ├── entity_resolution.py       # 3-pass awardee -> ticker resolution
│   ├── edgar_company_lookup.py    # SEC EDGAR company index lookup + cache
│   ├── classify_sector.py         # Keyword-based sector classifier
│   ├── fundamentals.py            # yfinance fetcher + curated overlay merger
│   ├── scoring.py                 # 6-component scoring engine + verdict + red flags
│   ├── dcf.py                     # 3-scenario DCF + reverse DCF
│   ├── report.py                  # Markdown report generator (10 sections)
│   └── edgar.py                   # Optional SEC 10-K extraction (--edgar flag)
└── reports/                       # Generated markdown and JSON reports
```

---

## Configuration

All weights, thresholds, and tier parameters live in `config.py`:

```python
SCORE_WEIGHTS = {
    "buffett_quality":   0.25,
    "graham_value":      0.20,
    "dod_stability":     0.20,
    "management":        0.15,
    "contract_catalyst": 0.10,
    "balance_sheet":     0.10,
}

VERDICT_THRESHOLDS = {
    "strong_candidate":       85,
    "potentially_attractive": 75,
    "watchlist":              65,
    "low_conviction":         50,
}

SPECIALIST_TIER = {
    "market_cap_floor_millions":   400,
    "market_cap_ceiling_millions": 15_000,
    "min_dod_revenue_pct":         35,
    "min_contract_to_revenue_pct": 3.0,
    "score_bonus_in_tier":         6.0,
    "score_bonus_near_tier":       3.0,
}
```

---

## Adding Tickers

Edit `data/ticker_map.yaml` to add subsidiary names or suppress EDGAR false positives:

```yaml
# Public company subsidiary
my subsidiary name llc:
  ticker: TICK
  parent: Parent Company Name
  confidence: 0.95
  notes: Optional note

# Confirmed private company
genuinely private company:
  ticker: null
  parent: Private Company Name
  confidence: 1.0
  notes: Reason it has no public ticker
```

Validate before committing:
```bash
python3 -c "import yaml; yaml.safe_load(open('data/ticker_map.yaml').read()); print('valid')"
```

> **YAML note:** Values containing `:` must be quoted (e.g., `"BAE Systems (London-listed; OTC ADR BAESY)"`).

To force re-resolution of a cached awardee after editing the map, delete `data/resolved_cache.json`.

---

## Improving Fundamentals Coverage

`data/mock_fundamentals.json` is a curated overlay on top of yfinance. Add an entry whenever a new ticker resolves via EDGAR and you want accurate DoD revenue / backlog / moat data in the score:

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

Fields yfinance supplies live (do not duplicate in overlay): price, P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B, D/E, current ratio, revenue growth, insider %, short interest, dividend yield, share count change, next earnings date, analyst consensus, 52-week range.

---

## Limitations

| Limitation | Impact |
|-----------|--------|
| USAspending 30-90 day lag | Most recent contracts may not appear yet. Fiscal year mode mitigates by pulling the full FY window. |
| yfinance data quality | P/E, EV/EBITDA, FCF yield may differ from Bloomberg/FactSet. Treat as directional. |
| Curated overlay staleness | Gov revenue %, DoD %, backlog, moat rating are manually maintained. Verify against latest 10-K. |
| IDIQ ceiling vs. obligations | Contract values reflect announced ceiling, not obligated funding. Always check actual task order obligations on USAspending. |
| Sector classification | Keyword-based on contract descriptions, which are often brief. Many contracts fall through to "Unclear." |
| Ticker confidence | EDGAR fuzzy matches below 0.70 should be verified manually before acting. |
| Scores are algorithmic | Not a substitute for fundamental research. Use as a first-pass screen only. |

---

## Design Philosophy

- **Contract size alone cannot make a mediocre company attractive** — quality and valuation must independently support the thesis.
- **IDIQ ceilings are discounted aggressively** vs. actually funded task orders.
- **Sole-source positions in critical national security programs** receive structural moat credit.
- **Non-traditional DoD sectors** (healthcare, pharma, logistics) are weighted fairly alongside traditional defense primes.
- **Growth in the DCF is company-anchored** — actual revenue growth blended with sector defaults, not generic top-down assumptions.
- **Short interest is a diligence gate** — elevated short positions trigger explicit flags requiring thesis refutation before acting.

This is a **screening tool**, not a trading signal.

---

*Contract data is public (USAspending.gov). Fundamental data from yfinance is subject to their terms of service.*
