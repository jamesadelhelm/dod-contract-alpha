# DoD Contract Intelligence Agent

An automated equity research pipeline that ingests U.S. Department of Defense contract awards
from USAspending.gov, resolves awardees to public tickers, fetches live market fundamentals,
and scores each company through a Buffett/Graham value framework — delivering a ranked analyst
report in under 30 seconds.

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
+-----------------------------------------------------------------------------+
|  STEP 1: FETCH                                                              |
|  USAspending.gov API -> current DoD fiscal year (Oct 1 -> today)           |
|  Up to 1,000 procurement contracts, >=$5M, sorted by value descending      |
+----------------------------------+------------------------------------------+
                                   |
+----------------------------------v------------------------------------------+
|  STEP 2: RESOLVE                                                            |
|  Awardee name -> public ticker (3-pass pipeline)                           |
|   Pass 1: 210-entry curated subsidiary map  (ELECTRIC BOAT -> GD)         |
|   Pass 2: Prefix/fuzzy match  (HUMANA GOVERNMENT BUSINESS -> HUM)         |
|   Pass 3: SEC EDGAR company index fallback  (~10,000 tickers, cached)     |
|  Unresolved names are flagged as private/unknown (shown in Coverage Gap)  |
+----------------------------------+------------------------------------------+
                                   |
+----------------------------------v------------------------------------------+
|  STEP 3: ENRICH                                                             |
|   yfinance (live):  price, P/E, Fwd P/E, EV/EBITDA, FCF yield, short %,  |
|                     share count chg, dividend yield, earnings calendar,    |
|                     analyst consensus, 52-week range, ROIC (derived)      |
|   Curated overlay:  44-entry database -- DoD revenue %, gov revenue %,    |
|                     backlog/revenue, moat rating, earnings stability yrs   |
|                     (supplements or corrects yfinance for 44 defense and  |
|                     adjacent companies including RTX, BA, LHX, CACI,      |
|                     HON, OSK, CNC, UNH, VSAT, and all major primes)       |
|  Sector classifier: keyword voting on contract descriptions -> 15 sectors |
|  Ticker overrides:  correct systematic misclassifications (BAH->AI/Data,  |
|                     LDOS->Cloud IT, RTX->Defense Prime, etc.)             |
|  Macro context:     ^TNX (10-yr yield) + ^IRX (3-mo T-bill) fetched live |
|                     Rate delta vs DCF baseline Rf (4.5%) -> adjusted IVs  |
|                     FY2026 DoD budget note + yield curve shape signal      |
+----------------------------------+------------------------------------------+
                                   |
+----------------------------------v------------------------------------------+
|  STEP 4: SCORE                                                              |
|  6-component framework (0-100 each, weighted):                             |
|   Buffett Quality   25%  -- ROIC, FCF margin, earnings stability, moat    |
|   Graham Value      20%  -- P/E, Fwd P/E, EV/EBITDA, FCF yield, P/B,     |
|                             dividend yield (calibrated for defense univ.)  |
|   DoD Stability     20%  -- DoD revenue %, backlog, sole-source position  |
|   Management        15%  -- ROIC, FCF consistency, insider ownership      |
|   Contract Catalyst 10%  -- contract size vs. revenue, sole-source, IDIQ |
|   Balance Sheet     10%  -- current ratio, Debt/EBITDA, interest coverage |
|                             (negative IC = operating loss -> flagged)      |
|  + 3-scenario DCF (bear/base/bull) + reverse DCF (implied growth rate)   |
|  + Specialist Tier bonus for mid-cap, high-DoD-concentration companies   |
+----------------------------------+------------------------------------------+
                                   |
+----------------------------------v------------------------------------------+
|  STEP 5: REPORT                                                             |
|  Ranked markdown report with Macro Context box + 12 sections:             |
|  Macro Context       (live 10-yr yield, rate-adjusted IVs, budget note)  |
|  Changes Since Last Run  (score/verdict/bear MoS deltas vs. prior run)   |
|   1. Action Summary  (price, score, MoS, bear MoS, signal tiers,         |
|                        entry prices, BUY/Start 75%/50% action labels)     |
|   2. Valuation Snapshot (multiples + full DCF table)                      |
|   3. Red Flags                                                             |
|   4. Market Context (consensus, short interest, price momentum)           |
|   5. Specialist Tier analysis                                              |
|   6. Government Funding Durability                                         |
|   7. Company Deep Dives (score breakdown + contracts + thesis)            |
|   8. Private Companies / Coverage Gap                                     |
|   9. Contract Awards (all 1,000 sorted by value)                          |
|  10. Sector Peer Comparison                                                |
|  11. Data Quality & Limitations                                            |
+-----------------------------------------------------------------------------+
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

**Typical run time:** ~30 seconds — 1,000 contracts fetched, resolved, scored, and reported.

---

## Sample Run

### Terminal output

```
============================================================
  DoD Contract Intelligence Agent
============================================================
  Contracts: usaspending | Fundamentals: yfinance (live)

[1/4] Loading contracts (source=usaspending)...
[USAspending] Fetching FY contracts (2025-10-01 -> 2026-06-08, min $5M)
[USAspending] Fetched 1000 awards (days_back=30)
[USAspending] Normalized 1000 contracts
      Loaded 1000 contracts.
[2/4] Grouping contracts by company...
      Public tickers: 32 | Private/unknown: 341
[3/4] Scoring companies...
[3b/4] Fetching macro context (10-yr yield)...
       10-yr yield: 4.53% +0.03pp vs DCF baseline

[4/4] Results

#   Ticker    Score   Chg    Price  Data    MoS   Bear  Verdict                      Sector
-------------------------------------------------------------------------------------------------------------------
1   BAH        71.8     =     $79  100%   +54%    -24%  Potentially Attractive       AI / Data / Software
2   LDOS       70.6     =    $123  100%   +29%    -12%  Potentially Attractive       Cloud / IT Services
3   GD         70.2     =    $341  100%   +37%  S+12%   Potentially Attractive       Shipbuilding
4   SAIC       65.7     =    $113  100%    +0%    -33%  Watchlist                    Cloud / IT Services
5   LMT        64.8     =    $520  100%   -58%    -66%  Watchlist                    Aerospace
6   NOC        64.7     =    $541  100%   -64%    -73%  Watchlist                    Aerospace
...
29  CNC        42.1     =     $60   94%     -+      -+  Ignore                       Military Healthcare
30  VSAT       40.6     =      —    94%   -92%    N/A   Ignore                       Space
31  BA         38.1     =    $195   94%   -88%    -97%  Ignore                       Aerospace
32  SHIM       27.6   new      $4   69%    N/A     N/A  Ignore                       Infrastructure

Private/unmatched: 341 contracts ($242,863M unresolved)

Report -> reports/report_20260608_HHMM.md
```

> `S` = bear MoS shield (🛡️ in terminal). `-+` = MoS suppressed for Ignore-rated companies —
> high MoS on a low-quality name is a DCF artifact, not a signal (e.g. CNC's commercial FCF yield).
> `+0%` = rounds to zero, not a display bug. `Chg` column shows score delta vs. prior run
> (`=` = no change, `new` = first appearance, `+/-X.X` = score moved). Scores persist in
> `data/last_scores.json` after each live run.

### What the report looks like (Section 1 — Action Summary)

```
## 1. Action Summary

| # | Ticker | Price | Company                       | Sector               | Score | MoS  | Bear    | Data | Verdict                   |
|---|--------|------:|-------------------------------|----------------------|------:|-----:|--------:|-----:|---------------------------|
| 1 | BAH    | $79   | Booz Allen Hamilton           | AI / Data / Software | 71.8  | +54% | -24%    | 100% | Potentially Attractive    |
| 2 | LDOS   | $123  | Leidos Holdings               | Cloud / IT Services  | 70.6  | +29% | -12%    | 100% | Potentially Attractive    |
| 3 | GD     | $260  | General Dynamics Corporation  | Shipbuilding         | 70.2  | +37% | S+12%   | 100% | Potentially Attractive    |
| 4 | SAIC   | $160  | Science Applications Intl     | Cloud / IT Services  | 65.7  | +0%  | -33%    | 100% | Watchlist                 |
...
|29 | CNC    | $60   | Centene Corporation           | Military Healthcare  | 42.1  | -+   | -+      |  94% | Ignore                    |
```

> **Price column** — current market price at time of run. Use as the entry price anchor.
> **MoS** = (Base IV - Price) / Price. Positive = stock below intrinsic value (base case).
> **Bear** = margin of safety in the pessimistic scenario. S = shield (positive in bear case).
> **-+** = MoS suppressed for Ignore-rated companies — see Section 2b for full DCF detail.

### What the report looks like (Section 2b — DCF Table)

```
## 2b. DCF Intrinsic Value Estimates

| Ticker | Price | Bear IV | Base IV | Bull IV | Bear MoS  | MoS (Base) | Reverse DCF | Rate  | DCF Verdict               |
|--------|------:|--------:|--------:|--------:|----------:|-----------:|------------:|------:|---------------------------|
| GD     | $260  | $293    | $356    | $525    | S+12%     | +37%       | 1%/yr       | 7.8%  | Significantly Undervalued |
| BAH    | $79   | $60     | $122    | $240    | -24%      | +54%       | 3%/yr       | 9.2%  | Significantly Undervalued |
| LDOS   | $123  | $108    | $158    | $268    | -12%      | +29%       | 3%/yr       | 8.5%  | Undervalued               |
| LMT    | $518  | $155    | $215    | $380    | -66%      | -58%       | 15%/yr      | 8.8%  | Significantly Overvalued  |
| BA     | $195  | $6      | $22     | $39     | -97%      | -88%       | 30%/yr      | 10.5% | Significantly Overvalued  |
| SHIM   | $4    | --      | --      | --      | --        | --         | --          | 13.5% | Negative IV               |
```

> **Reading the DCF:** MoS = (Intrinsic Value - Price) / Price. Positive = stock trading below
> intrinsic value. Reverse DCF answers "what growth rate does the current price require?"
> BA's price implies 30%/yr for 10 years — the sanity check that immediately flags it as a pass.
>
> **Bear MoS** = margin of safety in the bear-case scenario. S (Shield, displayed as 🛡️ in
> terminal) = stock is still undervalued even in the pessimistic scenario — the single most
> important signal for position sizing. GD's S+12% means you still have margin of safety
> if growth disappoints. BAH's -24% means the thesis must hold; DOGE cuts would hurt.
>
> **ACN's +70% MoS** reflects its commercial FCF (DoD is ~8% of revenue) — not a DoD thesis.
> The tool caps ACN at 60 (Watchlist, not Potentially Attractive) because its DoD exposure
> is too small to outrank pure-DoD plays. An explicit caveat appears in Section 2b when
> DoD revenue < 20% and market cap > $15B.
>
> **SHIM's `--` MoS** indicates negative intrinsic value (all DCF scenarios project negative FCF).
> The model shows "Negative IV -- capital destruction risk" because negative IV is a solvency
> question, not a valuation one.
>
> **For Ignore-rated companies** (CNC, HUM, UNH), the Action Summary shows `-+`
> (suppressed) because positive MoS on a low-quality name is a DCF artifact, not a signal.
> Full detail remains in Section 2b.

---

## Usage

```bash
# Default: current DoD fiscal year (Oct 1 → today) + live yfinance fundamentals
python3 main.py

# Filters
python3 main.py --min-score 65         # only companies scoring >= 65
python3 main.py --top 10               # top 10 by score
python3 main.py --specialist-only      # mid-cap, high-DoD-concentration only
python3 main.py --min-market-cap 500   # drop micro-caps below $500M market cap
python3 main.py --min-liquidity 2      # drop names with < $2M/day avg dollar volume

# Output
python3 main.py --output my_report.md  # custom output path
python3 main.py --json                 # also emit a JSON scores file
python3 main.py --no-report            # scores to terminal only

# Data sources
python3 main.py --source mock --no-live   # fully offline (demo mode)
python3 main.py --source live             # scrape defense.gov instead of USAspending

# EDGAR enrichment (slow — fetches 10-K for each ticker)
python3 main.py --edgar

# EDGAR XBRL (recommended for production) — 3-yr normalized FCF + shipbuilding backlog
python3 main.py --xbrl

# Continuous monitoring — re-run every 24h, print only material changes (SELL/REDUCE/new BUY)
python3 main.py --watch
python3 main.py --watch --watch-interval 3600   # hourly (e.g. during volatile periods)
```

---

## Signal Tiers

The Action Summary produces a Signal Tiers box that organizes actionable names:

| Tier | Criteria | Current Example |
|------|---------|-----------------|
| 🟢 **Highest Conviction** | PA+, bear MoS > 0 | GD (S+12% bear) |
| 🟡 **Research Priority** | PA+, positive base MoS, negative bear MoS | BAH (-24% bear), LDOS (-12% bear) |
| 🔵 **Monitor** | Watchlist, positive base MoS > 5% | HII (+12% base MoS) |
| ⏳ **Wait for Entry** | Overvaluation flag active (base MoS < -35%) | LMT, NOC, GE, RTX |

The tier labels directly answer "what do I do today?" without requiring cross-referencing multiple
report sections. They're derived from composite scores, base MoS, and bear-case MoS.

**Position Sizing Table** in Section 1 now includes:
- **Now** — current market price (entry price anchor)
- **Entry Target** — bear IV: the price at which even the pessimistic DCF scenario breaks even.
  For Highest Conviction names (positive bear MoS), bear IV > current price; you are already
  "inside" the bear case safety margin. For Research Priority names, bear IV shows the "back up
  the truck" price where the thesis becomes risk-free.
- **Action** — explicit label: **BUY** (bear MoS positive → enter now), **Start 75%** (mild
  tail risk: -15% to 0%), **Start 50%** (elevated tail risk: -30% to -15%),
  **Speculative 25%** (severe tail: below -30%)
- **Score delta (console)** — `Chg` column shows change vs. prior run; persisted in
  `data/last_scores.json`
- **Changes Since Last Run (report)** — dedicated section flags score moves ≥ 0.5 pts,
  verdict upgrades/downgrades, and bear MoS sign flips (most critical signal)
- **Position Management Signals** — exit rules embedded in Changes Since Last Run:
  - 🔴 **SELL** — name was PA+, now Ignore: thesis broken down, exit position
  - 🟠 **REDUCE** — name was PA+, now Watchlist/below: trim to half, re-evaluate next run
  - ⚠️ **REVIEW** — bear MoS flipped negative on a PA+ name: reduce to 75% sizing
- **Liquidity warnings** — PA+ names below $2M/day avg dollar volume are flagged in Section 1.
  Filter with `--min-liquidity 2` to exclude illiquid names from rankings entirely.
- **Earnings pre-announcement sizing** — position size is automatically halved when a PA+
  name has earnings within 21 days. Binary event risk (beat/miss gaps) are independent of
  thesis quality; sizing is restored to full on the next run post-earnings.
- **Watchlist buy triggers** — Signal Tiers lists the base IV for each Watchlist/overvalued
  name: "LMT becomes PA+ below $X" directly answers "when would I buy this?"
- **What Would Change My Mind** — Generated for every PA+ name in the Company Deep Dive
  (Section 7). Answers the most important question before deploying capital: *"What specific
  event would cause me to exit this position?"* Two-part output:

  1. **Component fragility table** — For each of the 6 scoring components, shows the raw-score
     drop required to flip the verdict from PA+ to Watchlist, ranked from most to least fragile.
     🔴 Critical = can drop ≤10 raw pts before verdict flips; 🟡 Moderate = 11–20 pts; 🟢 Resilient.

  2. **Thesis-break scenario narratives** — 3–4 specific real-world events that would flip the
     verdict, derived from the 2 most fragile components plus a DCF rate sensitivity scenario:
     - *Quality deterioration*: FCF margin / ROIC drop that reduces Buffett Quality score
     - *Multiple expansion*: rally to X price where MoS compresses → Graham Value drops
     - *DoD contract loss*: concentration drop that hits DoD Stability score
     - *Rate spike*: 10-yr yield rise required to erase the 🛡️ bear-case shield

     For GD (score 70.2, 2.2pts to flip): "A rate rise of +1.11pp (10-yr → 5.64%) erases
     the bear-case downside protection. Reduce to 75% sizing if 10-yr approaches 5.5%."

  Exit rule embedded: "If any ❌ scenario materializes AND verdict flips on next run → execute
  the REDUCE signal from Changes Since Last Run."

- **Score trend arrows** — Changes Since Last Run shows ↑ / ↓ / → based on the rolling
  30-run score history in `data/score_history.json`. Trends require ≥3 runs; shown as `—` until then.
- **Pre-Deployment Conviction Checklist** — Generated for every PA+ name in the Company Deep
  Dive (Section 7). Answers the 5 questions a real investor must check before executing:
  1. **Earnings timing** ✅/⚠️/❌ — Is the stock within the pre-earnings binary-event window?
     ❌ blocks entirely (<7d); ⚠️ notes the auto-halved sizing (<21d); ✅ confirms clear window.
  2. **Street consensus** ✅/⚠️ — Buy/strong buy = aligned; hold = cautious (contrarian opportunity
     if thesis holds); sell = flag to re-examine the thesis before sizing up.
  3. **Price positioning** ✅/⚠️ — ≤−10% off 52-week high = fair entry; ≥−3% = near highs, consider
     waiting; near all-time high = ⚠️, sizing discipline critical.
  4. **Insider activity** ✅/⚠️/❌ — Net buying >10% = management aligned; selling >20% = ⚠️;
     heavy selling >40% = ❌, re-examine thesis.
  5. **Macro rate check** ✅/⚠️ — Is the live 10-yr yield within 0.5pp of DCF baseline Rf (4.5%)?
     >0.5pp above baseline = ⚠️ (IVs shown are optimistic), with shield-break test for bear IV.

  Output: **✅ Ready to Deploy** (all clear → execute at full sizing), **⚠️ Conditional Deploy**
  (cautions only → proceed at 50% or review), **❌ Hold** (any blocking issue → do not execute).

  Example:
  ```
  | Check             | Status | Detail                                                      |
  |-------------------|:------:|-------------------------------------------------------------|
  | Earnings timing   |  ✅   | Next earnings: 2026-09-15 (98d) — clear of binary event window |
  | Street consensus  |  ✅   | buy consensus (15 analysts) | target $420 (+23%)              |
  | Price positioning |  ✅   | -18% off 52-week high | 45% from 52w low — fair entry       |
  | Insider activity  |  ✅   | Net buying +22% of held shares (6m)                          |
  | Macro rate check  |  ✅   | 10-yr yield 4.53% ≈ DCF baseline (+0.03pp) — IVs valid      |

  ✅ Ready to Deploy — All checks clear. Execute at up to 6.0% per Capital Deployment guidance.
  ```

- **Macro Context box** — Top of every report. Fetches live 10-yr Treasury yield (^TNX) and
  3-month T-bill rate (^IRX) from yfinance. Computes delta vs DCF baseline Rf (4.5%) and shows
  rate-adjusted intrinsic values for all PA+ names. Yield curve inversion is flagged when
  10-yr minus 3-mo spread turns negative. Example output:

  ```
  | Indicator                      | Live  | DCF Baseline | Δ        |
  |--------------------------------|------:|-------------:|---------:|
  | 10-yr Treasury Yield (Rf proxy)| 4.80% | 4.50%        | +0.30pp  |
  | 3-mo T-Bill                    | 5.25% | —            | —        |
  | DoD Budget FY2026              | $895B | —            | +3.3% YoY|

  > Rate environment: 10-yr (4.80%) is +0.30pp above DCF baseline.
  > DCF intrinsic values are ~3.2% lower than shown at current rates.
  > Rate-adjusted IVs: GD base IV $380 → $368 | bear IV $293 → $284 vs $341 now
  ```

  At +0.30pp above baseline, GD's bear IV drops from $380 to ~$368 — still above the current
  price, confirming the 🛡️ shield. If rates spike +1pp, bear IV drops to ~$339 — below current
  price, the shield breaks. This context is critical before sizing a position.

---

## Scoring Framework

```
Final Score = Buffett(25%) + Graham(20%) + DoD(20%) + Management(15%) + Catalyst(10%) + BalanceSheet(10%)
```

### Component Breakdown

| Component | Weight | What It Measures | Key Signals |
|-----------|:------:|-----------------|-------------|
| **Buffett Quality** | 25% | Is this a durable, high-quality business? | ROIC ≥ 15%, FCF margin ≥ 12%, earnings stable 5+ years, economic moat |
| **Graham Value** | 20% | Is it trading at a reasonable price? | P/E, Fwd P/E, EV/EBITDA, FCF yield ≥ 6%, P/B, dividend yield — calibrated for 18–30x defense universe |
| **DoD Stability** | 20% | How durable is the government revenue? | DoD revenue %, backlog/revenue ratio, sole-source position, sector durability |
| **Management Quality** | 15% | Is management allocating capital well? | ROIC, FCF consistency, insider ownership, share count discipline (buybacks vs. dilution) |
| **Contract Catalyst** | 10% | Is this contract meaningful to the thesis? | Contract size as % of revenue, funded vs. ceiling (IDIQ haircut), sole-source, duration |
| **Balance Sheet** | 10% | Can the company survive a downturn? | Current ratio, Debt/EBITDA, interest coverage (negative IC = operating loss flagged separately) |

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
| DoD revenue **< 15%** of total | Final score capped at 60 — commercial metrics drive the score, not DoD exposure |
| DoD revenue **15–24%** of total | Final score capped at 65 — prevents large commercial companies from outranking pure-DoD plays |

---

## DCF Valuation

**3-scenario (bear / base / bull) 10-year owner-earnings DCF + reverse DCF.**

| Parameter | Logic |
|-----------|-------|
| **Owner earnings** | FCF margin x revenue; revenue-based if FCF is negative |
| **FCF margin** | 3-year normalized average from EDGAR XBRL (--xbrl flag); falls back to yfinance TTM |
| **Discount rate** | 9% base +/- adjustments for DoD concentration, moat, leverage, size, profitability |
| **DoD WACC penalty** | +3% for DoD < 15%, +1% for < 25%, +0.5% for < 40% (commercial revenue risk) |
| **Growth anchor** | 60% analyst forward consensus + 40% TTM actual (both from yfinance) |
| **Growth yr 1-5** | Blended anchor x 60% + sector default x 40%; bear = 40% of anchor; bull = 85% |
| **Growth yr 6-10** | Mean-reverts toward sector long-run rate |
| **Terminal growth** | 2.5-3.5% depending on sector and DoD concentration |
| **EV to Equity** | Enterprise value - net debt / shares = equity per share IV |
| **WACC sensitivity** | +1% WACC reduces IV by ~10-17% (TV dominates; shown per PA+ name in report) |
| **Reverse DCF** | Solves for the growth rate that justifies the current price -- key sanity check |

**Reading the output:** Bear/base/bull gives a range of outcomes. The reverse DCF is the primary
sanity check -- if the current price requires 20%+/yr growth for 10 years, skip it.

**The blended growth anchor** prevents two failure modes: (1) using only TTM, which anchors to
BAH's -6% DOGE revenue drop and produces an unnecessarily pessimistic base case; (2) using only
forward consensus, which misses current-period headwinds. 60/40 forward/TTM preserves both
analyst outlook and current reality.

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
| Interest coverage negative | EBIT < 0 — operating loss cannot service debt (insolvency risk if sustained) |
| Interest coverage dangerously low | IC < 1.5x |
| Current ratio | < 1.0 |
| IDIQ funded ratio | Funded < 25% of ceiling |
| DCF overvaluation | MoS < −35% (non-infrastructure) — includes reverse DCF implied growth rate as sanity check |

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

`data/mock_fundamentals.json` is a 44-entry curated database that supplements yfinance with
fields it cannot reliably provide, and serves as the full data source for offline (`--no-live`) runs.

**Fields applied as overlay on top of yfinance (live runs):**
These override yfinance only when yfinance returns None:
- `dod_revenue_pct`, `government_revenue_pct` — not available from yfinance
- `backlog_to_revenue` — not available from yfinance
- `moat_rating` — subjective; must be set manually ("Wide" / "Narrow" / "None")
- `roic` — derived from financial statements; override if yfinance ROIC is unreliable

**Always overrides yfinance:**
- `earnings_stability_years` — yfinance caps at 4 years; established primes need the real number

**To add a new ticker** (minimum useful entry):
```json
"PSN": {
  "dod_revenue_pct": 48,
  "government_revenue_pct": 78,
  "backlog_to_revenue": 1.8,
  "moat_rating": "Narrow",
  "earnings_stability_years": 8
}
```

Full financial data (price, P/E, margins, etc.) can also be added for offline-mode accuracy,
but yfinance wins in live runs — don't expect those fields to override live data.

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
| **USAspending data lag** | 30–90 days. Contracts from the last ~6 weeks may be missing. Fiscal-year mode captures all major awards back to Oct 1. |
| **yfinance accuracy** | P/E, EV/EBITDA, FCF yield can diverge from Bloomberg/FactSet by 5–15%. Treat as directional — verify before acting. |
| **Overlay staleness** | DoD %, backlog, moat rating are manually maintained. The tool flags when these are estimated vs. verified. Always confirm against the latest 10-K or earnings call transcript. |
| **IDIQ ceilings** | USAspending records obligated task orders, not total contract ceiling. An IDIQ ceiling of $500M with $50M funded (10%) is a much weaker catalyst than it appears. The tool applies a haircut and flags this. |
| **Sector classification** | Keyword-based on short contract descriptions. Ticker overrides applied for 20 known systematic misclassifications. Add new ones in `TICKER_SECTOR_OVERRIDES` in `config.py`. |
| **Earnings stability cap** | yfinance returns max 4 years of income statement history. When this cap is hit, the tool raises a flag in the Buffett component. Add `earnings_stability_years` to the overlay for established companies. |
| **Large commercial companies** | ACN, IBM, HON have strong scores driven by business quality, but DoD contracts are marginal to their investment thesis. When DoD revenue < 20% and market cap > $15B, the tool adds an explicit ⚠ caveat to the DCF section and caps the valuation score at 45. A separate DoD concentration cap applies to the final composite score: < 15% DoD → capped at 60, 15–24% DoD → capped at 65. This prevents a pristine commercial compounder from ranking above a pure-DoD specialist. Read the "Why It Might Not Matter" section for these names. |
| **MoS for non-defense companies** | Companies like CNC, HUM, UNH have high FCF from their commercial business (Medicaid, Medicare Advantage) that inflates the DCF Margin of Safety. MoS is suppressed (`—†`) in the Action Summary for Ignore-rated companies to prevent this from being mistaken for a buy signal. |
| **Negative intrinsic value** | Companies with persistent negative FCF (SHIM, AVAV in down cycles) produce negative DCF intrinsic values. The tool replaces the misleading MoS% with "Negative IV — capital destruction risk" and shows `—` in all tables — a solvency alert, not a valuation alert. |
| **FCF margin fallback** | yfinance's `freeCashflow` info field is sometimes missing even when the cashflow statement has the data. The tool reads the cashflow statement directly as fallback. For production runs, use `--xbrl` to source a 3-year normalized FCF margin from SEC EDGAR XBRL data — more stable than any single yfinance TTM figure. |
| **Dividend yield normalization** | yfinance's `dividendYield` is inconsistently formatted across tickers; the tool now prefers `trailingAnnualDividendYield` (always fractional) and falls back to `dividendYield` only when needed. |
| **Graham calibration** | P/E brackets calibrated for 18–30x defense universe. Dividend yield replaces current ratio in Graham Value to avoid double-counting with the Balance Sheet component. |
| **DCF sensitivity** | Terminal value is 60–80% of the total intrinsic value. Use the reverse DCF (implied growth rate) as the primary sanity check — not the absolute scenario IVs. |
| **D/E ratio parsing** | yfinance returns `debtToEquity` as a percentage (e.g., 19.3 = 19.3% = 0.193× ratio), not as a direct ratio. The tool divides by 100 consistently — older code had a threshold bug where values ≤ 20 were taken as-is, causing AVAV's 0.19× D/E to appear as 19.3× and inflating its discount rate by ~0.75pp. |
| **Infrastructure/Construction DCF** | FCF-based DCF systematically understates value for engineering and construction firms (KBR, AECOM, Parsons). Working capital cycles and billing timing compress reported FCF even when economic returns are healthy. For these companies, the tool adds a caveat directing to EV/EBITDA as the primary valuation anchor and suppresses the overvaluation flag. |
| **3-year revenue CAGR context** | A single-year revenue decline can be cyclical (budget timing, contract transitions) or structural. When 1yr revenue falls > 5% but 3yr CAGR is positive, the tool adds context: "3yr CAGR +X% — decline may be cyclical rather than structural; monitor next 2 quarters before concluding trend reversal." |
| **Portfolio concentration** | When ≥ 3 actionable names share a common risk factor (Federal IT/DOGE exposure, Aerospace prime concentration), the Action Summary adds a ⚠️ cluster warning so sector risk is visible at the portfolio level — not just per-company. |
| **No backtesting** | Scoring weights are constructed from first principles, not empirically validated on historical returns. This is the single most important limitation for real capital deployment. |
| **Liquidity** | Avg daily dollar volume is shown as a warning when < $2M for PA+ names. Use `--min-liquidity 2` to exclude them from rankings. Volume data from yfinance `averageVolume10days`; not available in offline mock mode. |
| **Score trend (minimum 3 runs)** | Trend arrows (↑ ↓ →) in Changes Since Last Run require ≥3 entries in `data/score_history.json`. They show `—` until then. History is appended on every live run, one entry per calendar day per ticker. |
| **Earnings sizing (live only)** | Pre-announcement position halving requires `next_earnings_date` from yfinance. Offline mock mode has no earnings dates so the rule never fires in mock runs. |
| **First-pass screen only** | Not a substitute for reading the 10-K, listening to earnings calls, or building your own discounted cash flow model. Use this tool to decide where to spend your research time, not to make the final call. |

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
