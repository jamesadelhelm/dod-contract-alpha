# DoD Contract Intelligence Agent

An automated equity research pipeline that ingests U.S. Department of Defense contract awards
from USAspending.gov, resolves awardees to public tickers, fetches live market fundamentals,
and scores each company through a Buffett/Graham value framework — delivering a ranked analyst
report in under 5 minutes.

> **Research tool only. Not investment advice.**

---

## The Problem It Solves

The DoD awards **$700–800 billion in contracts each year**. Those awards are public record, but
the signal is buried: 1,000+ contracts per run, cryptic awardee names like
`HEALTH NET FEDERAL SERVICES LLC` or `ELECTRIC BOAT CORPORATION`, and no link to any stock ticker.

A human analyst would need days to cross-reference this data with SEC filings and market
fundamentals. This tool does it in minutes — automatically resolving subsidiary names to public
tickers, fetching live fundamentals, running a DCF, and ranking every company by investment quality.

---

## Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  STEP 1: FETCH                                                              │
│  USAspending.gov API → current DoD fiscal year (Oct 1 → today)             │
│  Up to 1,000 procurement contracts, ≥$5M, sorted by value descending       │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────────────┐
│  STEP 2: RESOLVE                                                            │
│  Awardee name → public ticker (3-pass pipeline)                             │
│   Pass 1: 210-entry curated subsidiary map  (ELECTRIC BOAT → GD)           │
│   Pass 2: Prefix/fuzzy match  (HUMANA GOVERNMENT BUSINESS → HUM)           │
│   Pass 3: SEC EDGAR company index fallback  (~10,000 tickers, cached)      │
│  Unresolved names are flagged as private/unknown (shown in Coverage Gap)   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────────────┐
│  STEP 3: ENRICH                                                             │
│   yfinance (live):  price, P/E, Fwd P/E, EV/EBITDA, FCF yield, short %,   │
│                     share count Δ, dividend yield, earnings calendar,       │
│                     analyst consensus, 52-week range                        │
│   Curated overlay:  DoD revenue %, government revenue %, backlog/revenue,  │
│                     moat rating, earnings stability years                   │
│  Sector classifier: keyword voting on contract descriptions → 15 sectors   │
│  Ticker overrides:  correct systematic misclassifications (BAH→AI/Data,    │
│                     LDOS→Cloud IT, RTX→Defense Prime, etc.)                │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────────────┐
│  STEP 4: SCORE                                                              │
│  6-component framework (0–100 each, weighted):                              │
│   Buffett Quality   25%  — ROIC, FCF margin, earnings stability, moat      │
│   Graham Value      20%  — P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B        │
│   DoD Stability     20%  — DoD revenue %, backlog, sole-source position    │
│   Management        15%  — ROIC, FCF consistency, insider ownership        │
│   Contract Catalyst 10%  — contract size vs. revenue, sole-source, IDIQ   │
│   Balance Sheet     10%  — current ratio, Debt/EBITDA, interest coverage  │
│  + 3-scenario DCF (bear/base/bull) with reverse DCF (implied growth rate) │
│  + Specialist Tier bonus for mid-cap, high-DoD-concentration companies    │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────────────┐
│  STEP 5: REPORT                                                             │
│  Ranked markdown report with 11 sections:                                  │
│   1. Action Summary (ranked table + signal counts)                         │
│   2. Valuation Snapshot (multiples + full DCF table)                       │
│   3. Red Flags                                                              │
│   4. Market Context (consensus, short interest, price momentum)             │
│   5. Specialist Tier analysis                                               │
│   6. Government Funding Durability                                          │
│   7. Company Deep Dives (score breakdown + contracts + investment thesis)  │
│   8. Private Companies / Coverage Gap                                      │
│   9. Contract Awards (all 1,000 sorted by value)                           │
│  10. Sector Peer Comparison                                                 │
│  11. Data Quality & Limitations                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

**Requirements:** Python 3.9+ &nbsp;|&nbsp; Internet access for USAspending + yfinance

```bash
git clone https://github.com/jamesadelhelm/dod-contract-alpha.git
cd dod-contract-alpha
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Live run (recommended) — fetches real contracts + real fundamentals
python3 main.py

# Offline demo (no network required)
python3 main.py --source mock --no-live
```

Output: `reports/report_YYYYMMDD_HHMM.md` — open in VS Code, Obsidian, or any markdown viewer.

**Typical run time:** 2–5 minutes (1,000 contracts, 25–35 public companies, live yfinance)

---

## Sample Run

### Terminal output

```
============================================================
  DoD Contract Intelligence Agent
============================================================
  Contracts: usaspending | Fundamentals: yfinance (live)

[1/4] Loading contracts (source=usaspending)...
[USAspending] Fetching FY contracts (2025-10-01 → 2026-06-07, min $5M)
[USAspending] Fetched 1000 awards
      Loaded 1000 contracts.
[2/4] Grouping contracts by company...
      Public tickers: 29 | Private/unknown: 354
[3/4] Scoring companies...

[4/4] Results

#   Ticker    Score  Data    MoS  Verdict                      Sector
------------------------------------------------------------------------------------------
1   BAH        70.6  100%   +21%  🟡 Potentially Attractive     AI / Data / Software
2   GD         69.5  100%   +51%  🟡 Potentially Attractive     Shipbuilding
3   ACN        69.4  100%  +152%  🟡 Potentially Attractive     Cloud / IT Services
4   LDOS       69.0  100%    +4%  🟡 Potentially Attractive     Cloud / IT Services
5   SAIC       65.1  100%    +4%  🔵 Watchlist                  Cloud / IT Services
6   NOC        62.9  100%   -65%  🔵 Watchlist                  Aerospace
7   LMT        61.6  100%   -63%  🔵 Watchlist                  Aerospace
8   HII        61.0  100%   +34%  🔵 Watchlist                  Shipbuilding
9   TXT        60.3  100%   -27%  🔵 Watchlist                  Aerospace
10  ACM        60.1  100%   -54%  🔵 Watchlist                  Infrastructure / Construction
...
29  SHIM       23.7   56%  -584%  🔴 Ignore                     Infrastructure / Construction

Private/unmatched: 354 contracts ($251,885M unresolved)

Report → reports/report_20260607_1544.md
```

### What the report looks like (Section 1 — Action Summary)

```
## 1. Action Summary

| # | Ticker | Company                            | Sector              | Score | MoS   | Data | Verdict                  |
|---|--------|------------------------------------|---------------------|------:|------:|-----:|--------------------------|
| 1 | BAH    | Booz Allen Hamilton                | AI / Data / Software| 70.6  | +21%  | 100% | 🟡 Potentially Attractive|
| 2 | GD     | General Dynamics Corporation       | Shipbuilding        | 69.5  | +51%  | 100% | 🟡 Potentially Attractive|
| 3 | ACN    | Accenture plc                      | Cloud / IT Services | 69.4  | +152% | 100% | 🟡 Potentially Attractive|
| 4 | LDOS   | Leidos Holdings                    | Cloud / IT Services | 69.0  | +4%   | 100% | 🟡 Potentially Attractive|
```

### What the report looks like (Section 2b — DCF Table)

```
## 2b. DCF Intrinsic Value Estimates

| Ticker | Price | Bear IV | Base IV | Bull IV | MoS (Base) | Reverse DCF | Discount Rate | DCF Verdict              |
|--------|------:|--------:|--------:|--------:|-----------:|------------:|--------------:|--------------------------|
| BAH    | $79   | $48     | $96     | $383    | +21%       | 3%/yr       | 9.2%          | Undervalued              |
| GD     | $346  | $387    | $523    | $772    | +51%       | 2%/yr       | 7.8%          | Significantly Undervalued|
| ACN    | $178  | $336    | $448    | $679    | +152%      | -5%/yr      | 9.0%          | Significantly Undervalued|
| LDOS   | $124  | $104    | $129    | $163    | +4%        | 4%/yr       | 8.5%          | Fairly Valued            |
| NOC    | $544  | $143    | $190    | $311    | -65%       | 17%/yr      | 8.0%          | Significantly Overvalued |
| BA     | $215  | $7      | $28     | $44     | -87%       | 32%/yr      | 10.2%         | Significantly Overvalued |
```

> **Reading the DCF:** MoS = (Intrinsic Value − Price) / Price. Positive = stock trading below what
> the model thinks it's worth. Reverse DCF answers "what growth rate does the current price require?"
> BA's price implies 32%/yr growth for 10 years — the sanity check that flags it as a pass regardless
> of any other signal.

---

## Usage

```bash
# Default: current DoD fiscal year (Oct 1 → today) + live yfinance fundamentals
python3 main.py

# Filters
python3 main.py --min-score 65         # only companies scoring >= 65
python3 main.py --top 10               # top 10 by score
python3 main.py --specialist-only      # mid-cap, high-DoD-concentration only

# Output
python3 main.py --output my_report.md  # custom output path
python3 main.py --json                 # also emit a JSON scores file
python3 main.py --no-report            # scores to terminal only

# Data sources
python3 main.py --source mock --no-live   # fully offline (demo mode)
python3 main.py --source live             # scrape defense.gov instead of USAspending

# EDGAR enrichment (slow — fetches 10-K for each ticker)
python3 main.py --edgar
```

---

## Scoring Framework

```
Final Score = Buffett(25%) + Graham(20%) + DoD(20%) + Management(15%) + Catalyst(10%) + BalanceSheet(10%)
```

### Component Breakdown

| Component | Weight | What It Measures | Key Signals |
|-----------|:------:|-----------------|-------------|
| **Buffett Quality** | 25% | Is this a durable, high-quality business? | ROIC ≥ 15%, FCF margin ≥ 12%, earnings stable 5+ years, economic moat |
| **Graham Value** | 20% | Is it trading at a reasonable price? | P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B — calibrated for 18–30x defense universe |
| **DoD Stability** | 20% | How durable is the government revenue? | DoD revenue %, backlog/revenue ratio, sole-source position, sector durability |
| **Management Quality** | 15% | Is management allocating capital well? | ROIC trend, FCF consistency, insider ownership, share count discipline |
| **Contract Catalyst** | 10% | Is this contract meaningful to the thesis? | Contract size as % of revenue, funded vs. ceiling, sole-source, duration |
| **Balance Sheet** | 10% | Can the company survive a downturn? | Current ratio, Debt/EBITDA, interest coverage |

### Verdict System

| Score | Verdict | Meaning |
|------:|---------|---------|
| ≥ 78 | 🟢 **Strong Candidate** | High conviction — worth building a full model |
| ≥ 68, Street bearish | 🟡 **Research Further** | Market disagrees with our quality read — investigate why |
| ≥ 68, expensive multiples | 🟠 **High Quality But Expensive** | Great business, wait for a better entry |
| 68–77 | 🟡 **Potentially Attractive** | Strong fundamentals — begin primary research |
| 58–67 | 🔵 **Watchlist** | Monitor for price weakness or catalyst |
| 48–57 | ⚪ **Low Conviction** | Marginal quality or limited data |
| < 48 | 🔴 **Ignore** | Pass — fails quality or value threshold |

> **Calibration note:** Thresholds are set for the defense/government services universe.
> Graham's 1930s brackets assume ≤12x P/E as "cheap" — defense primes legitimately trade at
> 18–30x. The scoring ceiling for a quality defense company is ~72–80, not 85–90 like a
> consumer compounder. Thresholds are adjusted 5–7 pts down so the tool produces actionable
> signals within this universe.

### Score Caps

| Condition | Effect |
|-----------|--------|
| Negative FCF **and** negative operating margin | Buffett score capped at 45 |
| IDIQ contract with < 25% funded | Catalyst score capped at 40 |
| Current ratio < 1.0 **and** Debt/EBITDA > 4.0 | Final score capped at 65 |

---

## DCF Valuation

**3-scenario (bear / base / bull) 10-year owner-earnings DCF + reverse DCF.**

| Parameter | Logic |
|-----------|-------|
| **Owner earnings** | FCF margin × revenue; revenue-based if FCF is negative |
| **Discount rate** | 9% base ± adjustments for DoD concentration, moat, leverage, size, profitability |
| **Growth yr 1–5** | 60% actual company revenue growth + 40% sector default, clipped to −10%/+60% |
| **Growth yr 6–10** | Mean-reverts toward sector long-run rate |
| **Terminal growth** | 2.5–3.5% depending on sector and DoD concentration |
| **EV → Equity** | Enterprise value − net debt / shares outstanding = equity per share IV |
| **Reverse DCF** | Solves for the growth rate that justifies the current price — key sanity check |

**Reading the output:** Bear/base/bull gives a range of outcomes. The reverse DCF is the primary
sanity check — if the current price requires 20%+/yr growth for 10 years, skip it.

---

## Specialist Tier

The highest-signal segment: **mid-cap ($400M–$15B), high-DoD-concentration (≥35%)** companies
where a single contract can be 10–20% of annual revenue, but sell-side coverage is thin
(3–8 analysts vs. 25+ for large primes like LMT or NOC).

Contract news for **large primes is priced in within hours** by institutional desks — the edge
is in the tier below, where material awards may not yet be in consensus models.

- **In-tier bonus:** +6 pts to final score
- **Near-tier bonus:** +3 pts (approaching size or concentration threshold)
- **Large primes excluded:** LMT, NOC, RTX, GD, BA, HII, LHX, TXT, L3H

---

## Red Flags

Automatically flagged and surfaced in Section 3 of the report:

| Signal | Threshold |
|--------|-----------|
| Analyst consensus bearish | Sell/Underperform, ≥ 3 analysts |
| Earnings proximity | ≤ 14 days to next report ⚠️ |
| Share dilution | > +5% YoY growth in share count |
| Short interest | > 15% of float (flag) / > 25% (significant) |
| Margin contraction | Operating margin down > 3pp YoY |
| Gross margin contraction | Down > 2pp YoY |
| Leverage | Debt/EBITDA > 4.0x |
| Interest coverage | < 1.5x |
| Current ratio | < 1.0 |
| IDIQ funded ratio | Funded < 25% of ceiling |

---

## Ticker Resolution

USAspending awardee names are often subsidiary or division names. Resolution runs in 3 passes:

1. **Curated map** — 210-entry `data/ticker_map.yaml`: 161 public tickers, 49 explicit private suppressions
2. **Prefix/fuzzy match** — detects parent brand at start of subsidiary name. Threshold: similarity ≥ 0.55 when matched key ≥ 6 chars
3. **EDGAR fallback** — full SEC company index (~10,000 tickers), cached locally

Matches below 0.70 confidence are flagged `⚠ LOW TICKER CONFIDENCE`. Unresolved awardees
appear in Section 8 (Coverage Gap) with total contract value.

**Selected non-obvious mappings:**

| USAspending Awardee | Ticker | Why |
|--------------------|--------|-----|
| HEALTH NET FEDERAL SERVICES LLC | CNC | Acquired by Centene 2016 |
| ELECTRIC BOAT CORPORATION | GD | GD subsidiary since 1952 |
| BATH IRON WORKS | GD | GD subsidiary since 1995 |
| NATIONAL STEEL AND SHIPBUILDING CO | GD | NASSCO acquired by GD 1999 |
| UNITEDHEALTH MILITARY & VETERANS | UNH | TRICARE East subsidiary |
| VERTEX AEROSPACE LLC | VVX | Merged into V2X 2022 |
| FLUOR MARINE PROPULSION LLC | FLR | Naval nuclear JV, majority Fluor |
| OLIN WINCHESTER LLC | OLN | Winchester brand owned by Olin |
| SPACE EXPLORATION TECHNOLOGIES | *null* | SpaceX is private |

---

## Customization

### Adding a ticker mapping

Edit `data/ticker_map.yaml` — keys are normalized to lowercase:

```yaml
# Public company subsidiary
bath iron works:
  ticker: GD
  parent: General Dynamics
  confidence: 0.98
  notes: Acquired 1995

# Confirmed private — suppresses EDGAR false positives
peraton:
  ticker: null
  parent: Peraton (Veritas Capital)
  confidence: 1.0
  notes: Private since 2021 Perspecta acquisition
```

Validate before committing:
```bash
python3 -c "import yaml; yaml.safe_load(open('data/ticker_map.yaml').read()); print('valid')"
```

To force re-resolution of a cached awardee: `rm data/resolved_cache.json`

### Improving fundamentals coverage

`data/mock_fundamentals.json` supplements yfinance with fields it can't reliably provide.
Add an entry for any ticker where DoD revenue %, backlog, or moat rating matters:

```json
"VVX": {
  "company_name": "V2X Inc",
  "dod_revenue_pct": 95,
  "government_revenue_pct": 98,
  "backlog_to_revenue": 1.6,
  "moat_rating": "Narrow",
  "earnings_stability_years": 8
}
```

Do not duplicate fields yfinance provides live: price, P/E, Fwd P/E, EV/EBITDA, FCF yield,
P/B, D/E, current ratio, revenue growth, insider %, short interest, dividend yield,
share count change, next earnings date, analyst consensus, 52W range.

### Fixing a sector misclassification

If a company's contracts describe logistics work but the company is actually an IT services
firm, add a `TICKER_SECTOR_OVERRIDES` entry in `config.py`:

```python
TICKER_SECTOR_OVERRIDES = {
    "LDOS": "Cloud / IT Services",   # Leidos: IT/tech services, not logistics
    "ACM":  "Infrastructure / Construction",  # AECOM: engineering firm
    ...
}
```

Sector drives the DCF growth assumptions and terminal rate — misclassification compounds.

---

## Limitations

| Issue | Detail |
|-------|--------|
| **USAspending data lag** | 30–90 days. Contracts from the last ~6 weeks may be missing. Fiscal-year mode is used by default to capture all major awards. |
| **yfinance accuracy** | P/E, EV/EBITDA, FCF yield can diverge from Bloomberg/FactSet. Treat as directional. |
| **Overlay staleness** | DoD %, backlog, moat rating are manually maintained — verify against the latest 10-K or earnings call. |
| **IDIQ ceilings** | USAspending shows obligated task orders, not contract ceiling. Ceiling ≠ guaranteed revenue. |
| **Sector classification** | Keyword-based on short descriptions. Ticker overrides applied for common misclassifications. |
| **Earnings stability cap** | yfinance returns max 4 years of history. Established primes need `earnings_stability_years` set in the overlay. |
| **Graham calibration** | P/E brackets calibrated for 18–30x defense universe. Verdict thresholds adjusted accordingly. |
| **DCF sensitivity** | Terminal value is 60–80% of total. Use reverse DCF as the primary sanity check — not the scenario IVs. |
| **No backtesting** | Scoring weights are constructed from first principles, not empirically validated on historical returns. This is the single most important limitation. |
| **First-pass screen only** | Not a substitute for reading the 10-K, listening to earnings calls, or building your own model. |

---

## Project Structure

```
dod_contract_agent/
├── main.py                      # CLI entry point
├── config.py                    # Weights, thresholds, sector keywords, specialist tier
├── requirements.txt
├── data/
│   ├── ticker_map.yaml          # Curated awardee → ticker map (210 entries)
│   ├── mock_fundamentals.json   # DoD%, backlog, moat overlay (edit to improve scores)
│   ├── sample_contracts.json    # Mock contracts for offline testing
│   ├── edgar_company_index.json # Auto-generated SEC company index cache
│   └── resolved_cache.json      # Auto-generated EDGAR lookup cache
└── src/
    ├── models.py                # Dataclasses: Contract, CompanyFundamentals, CompanyScore
    ├── fetch_usaspending.py     # USAspending API client (fiscal year mode)
    ├── parse_contracts.py       # Contract parsing and enrichment
    ├── entity_resolution.py     # 3-pass awardee → ticker pipeline
    ├── classify_sector.py       # Keyword sector classifier (15 sectors)
    ├── fundamentals.py          # yfinance + overlay merger
    ├── scoring.py               # 6-component scoring engine + verdict logic
    ├── dcf.py                   # 3-scenario DCF + reverse DCF
    ├── report.py                # 11-section markdown report generator
    └── edgar.py                 # SEC 10-K extraction (--edgar flag)
```

---

*Contract data: public domain (USAspending.gov). Market data: yfinance (subject to their terms of service).*
