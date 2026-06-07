# DoD Contract Intelligence Agent

Screens U.S. Department of Defense contract awards against a Buffett/Graham quality framework to surface publicly traded companies worth researching as investment candidates.

> **Research tool only. Not investment advice.**

---

## Quick Start

**Requirements:** Python 3.9+

```bash
git clone https://github.com/jamesadelhelm/dod-contract-alpha.git
cd dod-contract-alpha
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

Output: `reports/report_YYYYMMDD_HHMM.md` — open in VS Code, Obsidian, or any markdown viewer. A typical run fetches ~300–1,000 contracts and scores 20–40 public companies in 2–5 minutes.

---

## How It Works

1. **Fetch** — pulls the current DoD fiscal year (Oct 1 → today) from USAspending.gov, up to 1,000 contracts sorted by value. Fiscal year mode is default because USAspending has a 30–90 day processing lag; a rolling "last N days" window returns near-zero results.
2. **Resolve** — maps awardee names to public tickers via 3-pass pipeline: exact match in a curated 210-entry subsidiary map → prefix/fuzzy match → SEC EDGAR company index fallback.
3. **Enrich** — fetches live fundamentals from yfinance (price, margins, multiples, short interest, dividend yield, share count change, earnings calendar, analyst consensus) overlaid with a curated file for fields yfinance doesn't expose (DoD revenue %, backlog, moat rating).
4. **Score** — runs 6-component Buffett/Graham scoring (0–100) plus a 3-scenario DCF.
5. **Report** — generates a ranked markdown report with deep dives, valuation tables, red flags, and a private-company coverage gap section.

---

## Usage

```bash
# Default: USAspending fiscal year + live fundamentals
python3 main.py

# Filters
python3 main.py --min-score 65          # only companies scoring >= 65
python3 main.py --top 10                # top 10 by score
python3 main.py --specialist-only       # mid-cap, high-DoD-concentration only

# Output
python3 main.py --output reports/my_report.md
python3 main.py --json                  # also emit a JSON scores file
python3 main.py --no-report             # scores to terminal only

# Data sources
python3 main.py --source live           # scrape defense.gov (same-day, less structured)
python3 main.py --source mock --no-live # fully offline, no network calls

# Enrichment
python3 main.py --edgar                 # pull gov revenue % and backlog from SEC 10-Ks
```

---

## Scoring Framework

```
Final Score = Buffett(25%) + Graham(20%) + DoD(20%) + Management(15%) + Catalyst(10%) + BalanceSheet(10%)
```

| Component | Weight | What It Measures |
|-----------|--------|-----------------|
| Buffett Quality | 25% | ROIC, FCF margin, operating margin, earnings stability, ROE, economic moat |
| Graham Value | 20% | P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B, current ratio |
| DoD Stability | 20% | DoD revenue %, backlog/revenue, sole-source position, sector durability |
| Management Quality | 15% | ROIC, FCF execution, earnings consistency, insider ownership, debt discipline |
| Contract Catalyst | 10% | Contract/revenue ratio, funded %, IDIQ haircut, sole-source, duration |
| Balance Sheet | 10% | Current ratio, Debt/EBITDA, interest coverage, net debt |

### Verdicts

| Score | Verdict |
|-------|---------|
| >= 78 | 🟢 Strong Candidate |
| >= 68, Street bearish (sell/underperform, >= 3 analysts) | 🟡 Research Further |
| >= 68, expensive (P/E > 80x or EV/EBITDA > 60x) and FCF margin < 15% | 🟠 High Quality But Expensive |
| 68–77 | 🟡 Potentially Attractive |
| 58–67 | 🔵 Watchlist |
| 48–57 | ⚪ Low Conviction |
| < 48 | 🔴 Ignore |

Override verdicts (Research Further, High Quality But Expensive) take priority at any score >= 68.

> **Threshold note:** These are calibrated for the defense/government services universe, not Graham's 1930s absolute standards. Defense primes legitimately trade at 18–30x P/E — a 25x P/E company earns partial Graham credit, pushing the scoring ceiling to ~72–80 rather than 85–90. Thresholds are set 5–7 pts below general-market baselines so the tool produces actionable signals within this universe.

### Score Caps

| Condition | Cap |
|-----------|-----|
| Negative FCF and negative operating margin | Buffett score capped at 45 |
| IDIQ with < 25% funded | Catalyst score capped at 40 |
| Current ratio < 1.0 and Debt/EBITDA > 4.0 | Final score capped at 65 |

---

## Red Flags

| Condition | Threshold |
|-----------|-----------|
| Analyst consensus | Sell/Underperform, >= 3 analysts |
| Operating margin contraction | > 3pp YoY |
| Gross margin contraction | > 2pp YoY |
| Short interest | > 15% of float (flag) / > 25% (significant) |
| Share count growth | > +5% YoY dilution |
| Earnings proximity | <= 14 days to next report |
| Current ratio | < 1.0 |
| Leverage | Debt/EBITDA > 4.0x |
| Interest coverage | < 1.5x |
| IDIQ funded ratio | Funded < 25% of ceiling |

---

## DCF Valuation

3-scenario (bear/base/bull) 10-year DCF plus reverse DCF (implied growth at current price).

- **Owner earnings:** FCF margin × revenue; falls back to revenue-based if FCF is negative
- **Discount rate:** 9% base, adjusted ±0.5–2.0% for DoD concentration, moat, leverage, size, profitability
- **Growth (yr 1–5):** 60% actual company revenue growth + 40% sector default, clipped to −10%/+60%
- **Growth (yr 6–10):** mean-reverts to sector long-run rate
- **Terminal growth:** 2.5–3.5% depending on sector and DoD concentration

---

## Ticker Resolution

USAspending awardee names are often subsidiary or division names. Resolution runs in 3 passes:

1. **Exact match** — 210-entry curated map (`data/ticker_map.yaml`): 161 public tickers, 49 explicit private suppressions
2. **Prefix/fuzzy match** — detects parent brand at the start of a subsidiary name (e.g., `HUMANA GOVERNMENT BUSINESS INC` → HUM). Threshold: similarity >= 0.55 when the matched key is >= 6 characters.
3. **EDGAR fallback** — full SEC company index (~10,000 tickers), cached locally in `data/resolved_cache.json`

Matches below 0.70 confidence are flagged. Unresolved awardees appear in Section 9 with total contract value.

**Selected mappings** (the non-obvious ones):

| Awardee (USAspending) | Ticker | Why non-obvious |
|-----------------------|--------|-----------------|
| HUMANA GOVERNMENT BUSINESS INC | HUM | Subsidiary brand, not parent name |
| HEALTH NET FEDERAL SERVICES LLC | CNC | Acquired by Centene 2016 |
| UNITEDHEALTH MILITARY & VETERANS SERVICES | UNH | TRICARE East subsidiary |
| VERTEX AEROSPACE LLC / VECTRUS SYSTEMS | VVX | Merged into V2X 2022 |
| NATIONAL STEEL AND SHIPBUILDING CO | GD | NASSCO acquired by GD 1999 |
| ELECTRIC BOAT CORPORATION | GD | GD subsidiary since 1952 |
| BATH IRON WORKS | GD | GD subsidiary since 1995 |
| FLUOR MARINE PROPULSION LLC | FLR | Naval nuclear JV, majority Fluor |
| OLIN WINCHESTER LLC | OLN | Winchester brand owned by Olin |
| SPACE EXPLORATION TECHNOLOGIES CORP. | null | SpaceX is private (SPCX is an ETF — not the same) |

---

## Specialist Tier

Mid-cap companies with high DoD revenue concentration are the highest-signal segment: contract awards are material to their revenue, but sell-side coverage is thin (3–8 analysts vs. 25+ for large primes).

**In-tier criteria:** Market cap $400M–$15B | DoD revenue >= 35% | Contract >= 3% of annual revenue

Score bonus: +6 pts in-tier, +3 pts near-tier. Large primes (LMT, NOC, RTX, GD, BA, HII, LHX, TXT, L3H) are excluded — contract news is already priced in within hours.

---

## Customization

### Adding tickers

Edit `data/ticker_map.yaml`. All keys are normalized to lowercase:

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

Validate YAML before committing (values containing `:` must be quoted):
```bash
python3 -c "import yaml; yaml.safe_load(open('data/ticker_map.yaml').read()); print('valid')"
```

To force re-resolution of a cached awardee, delete `data/resolved_cache.json`.

### Improving fundamentals coverage

`data/mock_fundamentals.json` supplements yfinance with fields it can't reliably provide. Add an entry for any ticker where DoD revenue %, backlog, or moat rating matters to the score:

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

Fields yfinance provides live — do not duplicate: price, P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B, D/E, current ratio, revenue growth, insider %, short interest, dividend yield, share count change, next earnings date, analyst consensus, 52W range.

---

## Limitations

| Issue | Detail |
|-------|--------|
| USAspending data lag | 30–90 days. Fiscal year mode captures all major awards but contracts from the last ~6 weeks may be missing. |
| yfinance accuracy | P/E, EV/EBITDA, FCF yield can diverge from Bloomberg/FactSet. Treat as directional. |
| Curated overlay staleness | DoD %, backlog, moat rating are manually maintained — verify against the latest 10-K or earnings call. |
| IDIQ ceilings | USAspending "Award Amount" reflects obligated amounts (task orders placed), not ceiling. Ceiling is not available via the search API. |
| Sector classification | Keyword-based on short USAspending descriptions. Many fall through to "Unclear." Sector determines DCF growth assumptions and terminal growth rate — misclassification compounds. |
| EDGAR false positives | Fuzzy matching can resolve a private defense company to an unrelated public company with a similar name. Explicit `null` entries in `ticker_map.yaml` suppress known bad matches. |
| Earnings stability cap | yfinance returns 4 years of income statement history maximum. `earnings_stability_years` is capped at 4 for companies not in the curated overlay — a 50-year track record scores the same as a 4-year-old company. Set this field in `mock_fundamentals.json` for all established primes. |
| Graham score calibration | P/E brackets follow Graham's 1930s value criteria (≤12x = full marks). Defense primes legitimately trade at 18–30x. The Graham score should be read as a relative comparison within the defense cohort, not an absolute quality gate. Verdict thresholds are calibrated down 5–7 pts to account for this. |
| DCF is a thinking framework | The 3-scenario DCF produces intrinsic value estimates, not predictions. Terminal value typically accounts for 60–80% of the total. Small changes in discount rate or terminal growth produce large swings in output. Use the reverse DCF (implied growth rate) as the primary sanity check. |
| No backtesting | Scoring weights (25%/20%/20%/15%/10%/10%) are constructed from first principles. There is no empirical evidence that higher scores have historically predicted better returns in this universe. This is the single most important limitation. |
| Scores are algorithmic | First-pass screen only. Not a substitute for reading the 10-K, listening to earnings calls, or building your own model. |

---

## Project Structure

```
dod_contract_agent/
├── main.py                      # CLI entry point and argument parsing
├── config.py                    # Scoring weights, thresholds, specialist tier, sector keywords
├── requirements.txt
├── data/
│   ├── ticker_map.yaml          # Curated awardee -> ticker map (edit this to add subsidiaries)
│   ├── mock_fundamentals.json   # DoD%, backlog, moat overlay (edit this to improve scores)
│   ├── sample_contracts.json    # Mock contracts for offline testing
│   ├── edgar_company_index.json # Auto-generated SEC company index cache
│   └── resolved_cache.json      # Auto-generated EDGAR lookup cache
└── src/
    ├── models.py                # Dataclasses: Contract, CompanyFundamentals, CompanyScore
    ├── fetch_usaspending.py     # USAspending API client (fiscal year mode)
    ├── parse_contracts.py       # Contract parsing, enrichment, defense.gov scraper
    ├── entity_resolution.py    # 3-pass awardee -> ticker pipeline
    ├── edgar_company_lookup.py  # SEC EDGAR index lookup + caching
    ├── classify_sector.py       # Keyword sector classifier
    ├── fundamentals.py          # yfinance + overlay merger
    ├── scoring.py               # Scoring engine, verdict logic, red flags
    ├── dcf.py                   # 3-scenario DCF + reverse DCF
    ├── report.py                # Markdown report generator
    └── edgar.py                 # SEC 10-K extraction (--edgar flag)
```

---

*Contract data: public domain (USAspending.gov). Market data: yfinance (subject to their terms of service).*
