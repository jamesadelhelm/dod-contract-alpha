"""
DoD Contract Intelligence Agent — Main CLI

Usage:
  python main.py                           # Live contracts + live fundamentals (default)
  python main.py --days 90                 # Extend lookback window to 90 days
  python main.py --specialist-only         # Mid-cap, high-DoD companies only
  python main.py --min-score 65            # High-conviction threshold
  python main.py --no-live                 # Offline: use mock fundamentals only (no yfinance)
  python main.py --source mock             # Fully offline: mock contracts + mock fundamentals
  python main.py --output my_report.md
  python main.py --top 10
  python main.py --json
  python main.py --no-report               # Print scores only, no markdown
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Allow imports from project root

from src.parse_contracts import load_and_enrich
from src.fundamentals import get_fundamentals_or_stub
from src.scoring import score_company
from src.report import generate_report, save_report
from src.models import CompanyScore, CompanyFundamentals, Verdict, Sector
from config import REPORTS_DIR, TICKER_SECTOR_OVERRIDES


def parse_args():
    p = argparse.ArgumentParser(description="DoD Contract Intelligence Agent")
    p.add_argument("--source", choices=["mock", "live", "usaspending"], default="usaspending",
                   help="Data source: usaspending (default) | live (defense.gov scrape) | mock (offline)")
    p.add_argument("--output", default=None,
                   help="Output markdown file path (default: reports/report_YYYYMMDD.md)")
    p.add_argument("--top", type=int, default=None,
                   help="Limit to top N companies by score")
    p.add_argument("--json", action="store_true",
                   help="Also save a JSON file with raw scores")
    p.add_argument("--no-report", action="store_true",
                   help="Skip markdown report generation; print scores only")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="Only include companies scoring >= this value")
    p.add_argument("--days", type=int, default=30,
                   help="Days back to fetch from USAspending (default: 30, used with --source usaspending)")
    p.add_argument("--edgar", action="store_true",
                   help="Fetch 10-K data from SEC EDGAR to get real gov revenue %% and backlog")
    p.add_argument("--xbrl", action="store_true",
                   help="Fetch EDGAR XBRL structured data: 3-yr normalized FCF + backlog for shipbuilding sectors")
    p.add_argument("--no-live", action="store_true", dest="no_live", default=False,
                   help="Use mock/offline fundamentals instead of yfinance (for offline testing)")
    p.add_argument("--specialist-only", action="store_true",
                   help="Filter to mid-cap, high-DoD-concentration companies only (sweet spot tier)")
    p.add_argument("--min-market-cap", type=float, default=0.0, dest="min_market_cap",
                   help="Exclude companies with market cap below this value in millions (e.g. 500 = $500M)")
    return p.parse_args()


def main():
    args = parse_args()
    live = not args.no_live  # True by default; --no-live disables yfinance

    print("=" * 60)
    print("  DoD Contract Intelligence Agent")
    print("=" * 60)
    fundamentals_mode = "yfinance (live)" if live else "mock (offline)"
    print(f"  Contracts: {args.source} | Fundamentals: {fundamentals_mode}")

    # ── Step 1: Load and enrich contracts ────────────────────────────────────
    print(f"\n[1/4] Loading contracts (source={args.source})...")
    contracts = load_and_enrich(source=args.source, days_back=args.days)
    print(f"      Loaded {len(contracts)} contracts.")

    # ── Step 2: Group by ticker ───────────────────────────────────────────────
    print("[2/4] Grouping contracts by company...")
    ticker_groups: dict[str, list] = defaultdict(list)
    private_contracts = []

    for c in contracts:
        if c.ticker:
            ticker_groups[c.ticker].append(c)
        else:
            private_contracts.append(c)

    print(f"      Public tickers: {len(ticker_groups)} | Private/unknown: {len(private_contracts)}")

    # ── Step 3: Score companies ───────────────────────────────────────────────
    print("[3/4] Scoring companies...")
    scores: list[CompanyScore] = []
    fundamentals_map: dict[str, CompanyFundamentals] = {}

    for ticker, ticker_contracts in ticker_groups.items():
        # Use sector weighted by contract value, not count.
        # A company with one $200M energy contract and ten $5M logistics
        # contracts should be classified as Energy, not Logistics.
        sector_votes = defaultdict(float)
        for c in ticker_contracts:
            sector_votes[c.sector] += (c.contract_value or 0)
        dominant_sector = max(sector_votes, key=lambda k: sector_votes[k])

        # Apply ticker-level sector override for companies whose USAspending
        # contract descriptions systematically mislabel their primary sector
        # (e.g. BAH intelligence contracts → Space; RTX IDIQ with vague text → Unclear).
        if ticker.upper() in TICKER_SECTOR_OVERRIDES:
            try:
                dominant_sector = Sector(TICKER_SECTOR_OVERRIDES[ticker.upper()])
            except ValueError:
                pass  # keep voted sector if override value is stale

        # Get fundamentals
        c0 = ticker_contracts[0]
        company_name = c0.parent_company or ticker
        f = get_fundamentals_or_stub(ticker, company_name, live=live)
        fundamentals_map[ticker] = f

        # EDGAR overlay — fetch 10-K and update fundamentals with primary-source data
        if args.edgar:
            try:
                from src.edgar import fetch_edgar_data, overlay_edgar_into_fundamentals
                print(f"  [EDGAR] Fetching 10-K for {ticker}...", end="", flush=True)
                edgar_result = fetch_edgar_data(ticker)
                overlay_edgar_into_fundamentals(f, edgar_result)
                conf = edgar_result.extraction_confidence
                gov = f"{edgar_result.us_government_revenue_pct:.0f}%" if edgar_result.us_government_revenue_pct else "n/a"
                dod = f"{edgar_result.dod_revenue_pct:.0f}%" if edgar_result.dod_revenue_pct else "n/a"
                backlog = f"{edgar_result.backlog_to_revenue:.1f}x" if edgar_result.backlog_to_revenue else "n/a"
                print(f" conf={conf} | gov={gov} | dod={dod} | backlog={backlog}")
            except Exception as e:
                print(f" EDGAR failed: {e}")

        # XBRL overlay — structured EDGAR data: 3yr normalized FCF + backlog for capital-intensive sectors
        if args.xbrl:
            try:
                from src.edgar import fetch_xbrl_financials, overlay_xbrl_into_fundamentals
                from src.models import Sector as _Sector
                print(f"  [XBRL] {ticker}...", end="", flush=True)
                xbrl = fetch_xbrl_financials(ticker)
                if xbrl:
                    # Only use XBRL backlog for capital-intensive sectors where
                    # RevenueRemainingPerformanceObligation ≈ management backlog.
                    # For IT services (BAH, LDOS, SAIC), RPO is much smaller than
                    # management-reported backlog (which includes unfunded orders).
                    _SERVICES_SECTORS = {
                        _Sector.AI_DATA_SOFTWARE, _Sector.CLOUD_IT_SERVICES, _Sector.CONSULTING_SERVICES
                    }
                    xbrl_for_overlay = dict(xbrl)
                    if dominant_sector in _SERVICES_SECTORS:
                        xbrl_for_overlay.pop("backlog_to_rev", None)  # keep FCF, drop backlog
                    overlay_xbrl_into_fundamentals(f, xbrl_for_overlay)
                    b2r = f"{xbrl.get('backlog_to_rev'):.2f}x" if xbrl.get('backlog_to_rev') is not None else "n/a"
                    fcf3 = f"{xbrl.get('fcf_margin_3yr'):.1f}%" if xbrl.get('fcf_margin_3yr') is not None else "n/a"
                    cagr = f"{xbrl.get('rev_cagr_3yr'):.1f}%" if xbrl.get('rev_cagr_3yr') is not None else "n/a"
                    svc_note = "(backlog suppressed for services)" if dominant_sector in _SERVICES_SECTORS else ""
                    print(f" backlog={b2r} fcf3yr={fcf3} cagr3yr={cagr} {svc_note}")
                else:
                    print(" no data")
            except Exception as e:
                print(f" XBRL failed: {e}")

        score = score_company(
            ticker=ticker,
            company_name=company_name,
            contracts=ticker_contracts,
            f=f,
            sector=dominant_sector,
            live=live,
        )
        scores.append(score)

    # Sort by final score descending
    scores.sort(key=lambda s: s.final_score, reverse=True)

    # Apply filters
    if args.min_score > 0:
        scores = [s for s in scores if s.final_score >= args.min_score]
    if args.min_market_cap > 0:
        before = len(scores)
        def _above_cap_floor(s):
            f = fundamentals_map.get(s.ticker)
            if f is None or f.market_cap_millions is None:
                return True  # no data — pass through rather than silently drop
            return f.market_cap_millions >= args.min_market_cap
        scores = [s for s in scores if _above_cap_floor(s)]
        dropped = before - len(scores)
        if dropped:
            print(f'Market cap filter (>=${args.min_market_cap:.0f}M) dropped {dropped} company/companies.')
    if args.specialist_only:
        scores = [s for s in scores
                  if s.specialist and s.specialist.status.value in ('In Tier', 'Near Tier')]
        print(f'Specialist filter applied — {len(scores)} companies remain.')

    if args.top:
        scores = scores[:args.top]

    # ── Step 4: Print summary ─────────────────────────────────────────────────
    print("\n[4/4] Results\n")
    print(f"{'#':<3} {'Ticker':<8} {'Score':>6} {'Data':>5} {'MoS':>6} {'Bear':>6}  {'Verdict':<28} {'Sector'}")
    print("-" * 98)
    verdict_emoji_map = {
        Verdict.STRONG_CANDIDATE: "🟢",
        Verdict.RESEARCH_FURTHER: "🟡",
        Verdict.POTENTIALLY_ATTRACTIVE: "🟡",
        Verdict.HIGH_QUALITY_BUT_EXPENSIVE: "🟠",
        Verdict.WATCHLIST: "🔵",
        Verdict.LOW_CONVICTION: "⚪",
        Verdict.IGNORE: "🔴",
    }
    for i, s in enumerate(scores, 1):
        emoji = verdict_emoji_map.get(s.verdict, " ")
        data_str = f"{s.data_completeness_pct:.0f}%"
        if s.data_completeness_pct < 50:
            data_str += "⚠"
        mos_str = "N/A"
        if s.dcf and s.dcf.margin_of_safety_base is not None:
            mos_str = f"{s.dcf.margin_of_safety_base:+.0f}%"
        bear_str = "N/A"
        if s.dcf and s.dcf.bear_mos is not None:
            bm = s.dcf.bear_mos
            is_pa_plus = s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
            bear_str = f"🛡{bm:+.0f}%" if (bm > 0 and is_pa_plus) else f"{bm:+.0f}%"
        print(f"{i:<3} {s.ticker:<8} {s.final_score:>6.1f} {data_str:>5} {mos_str:>6} {bear_str:>7}  {emoji} {s.verdict.value:<26} {s.sector.value}")

    unmatched_value = sum(c.contract_value for c in private_contracts if c.contract_value)
    print(f"\nPrivate/unmatched: {len(private_contracts)} contracts (${unmatched_value:.0f}M unresolved)")

    # ── JSON output ───────────────────────────────────────────────────────────
    if args.json:
        json_path = REPORTS_DIR / f"scores_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        json_data = []
        for s in scores:
            json_data.append({
                "ticker": s.ticker,
                "company_name": s.company_name,
                "sector": s.sector.value,
                "final_score": s.final_score,
                "buffett_quality": s.buffett_quality.raw,
                "graham_value": s.graham_value.raw,
                "dod_stability": s.dod_stability.raw,
                "management": s.management.raw,
                "contract_catalyst": s.contract_catalyst.raw,
                "balance_sheet": s.balance_sheet.raw,
                "verdict": s.verdict.value,
                "explanation": s.overall_explanation,
                "red_flags": s.red_flags,
            })
        with open(json_path, "w") as f:
            json.dump(json_data, f, indent=2)
        print(f"\nJSON scores saved → {json_path}")

    # ── Markdown report ───────────────────────────────────────────────────────
    if not args.no_report:
        output_path = args.output or str(
            REPORTS_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
        )
        run_date = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
        report_content = generate_report(
            ranked_scores=scores,
            private_contracts=private_contracts,
            all_contracts=contracts,
            run_date=run_date,
            live=live,
            fundamentals_map=fundamentals_map,
        )
        save_report(report_content, output_path)
        print(f"\nReport → {output_path}")

    print("\nDone.\n")
    return scores


if __name__ == "__main__":
    main()
