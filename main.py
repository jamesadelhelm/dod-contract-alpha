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
from src.fundamentals import get_fundamentals_or_stub, get_macro_context
from src.scoring import score_company
from src.report import generate_report, save_report
from src.models import CompanyScore, CompanyFundamentals, Verdict, Sector
from config import REPORTS_DIR, TICKER_SECTOR_OVERRIDES, DATA_DIR


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
    p.add_argument("--min-liquidity", type=float, default=0.0, dest="min_liquidity",
                   help="Exclude companies where avg daily dollar volume < this value in millions (e.g. 2 = $2M/day)")
    p.add_argument("--watch", action="store_true",
                   help="Continuous monitoring mode: re-run every 24h, print only material changes")
    p.add_argument("--watch-interval", type=int, default=86400, dest="watch_interval",
                   help="Seconds between watch-mode re-runs (default: 86400 = 24h)")
    p.add_argument("--portfolio", action="store_true",
                   help="Show portfolio review: P&L and thesis-intact check for positions in data/portfolio.json")
    p.add_argument("--portfolio-size", type=float, default=None, dest="portfolio_size",
                   help="Total investable equity in dollars (e.g. 100000 for $100K). "
                        "When set, the report shows exact dollar amounts and share counts for each position.")
    p.add_argument("--alert-email", default=None, dest="alert_email",
                   help="Email address for material-change alerts in --watch mode. "
                        "Requires DOD_AGENT_SMTP_PASSWORD env var (Gmail app password recommended).")
    p.add_argument("--smtp-server", default="smtp.gmail.com", dest="smtp_server",
                   help="SMTP server for alert emails (default: smtp.gmail.com)")
    p.add_argument("--smtp-from", default=None, dest="smtp_from",
                   help="SMTP from address (defaults to --alert-email)")
    p.add_argument("--brief", action="store_true",
                   help="Generate a condensed 1-page executive summary: Action Summary + thesis + key risks only. "
                        "Omits DCF detail, Section 3-11. Ideal for emailing to a PM.")
    return p.parse_args()


_LAST_SCORES_PATH    = DATA_DIR / "last_scores.json"
_SCORE_HISTORY_PATH  = DATA_DIR / "score_history.json"
_PORTFOLIO_PATH      = DATA_DIR / "portfolio.json"
_HISTORY_MAX_ENTRIES = 30


def _load_score_history() -> dict:
    """Load rolling score history (up to _HISTORY_MAX_ENTRIES per ticker)."""
    try:
        if _SCORE_HISTORY_PATH.exists():
            return json.loads(_SCORE_HISTORY_PATH.read_text())
    except Exception:
        pass
    return {}


def _update_score_history(scores: list, fundamentals_map: dict) -> None:
    """Append current run to the rolling history file."""
    try:
        history = _load_score_history()
        run_date = datetime.now().strftime("%Y-%m-%d")
        for s in scores:
            f = fundamentals_map.get(s.ticker)
            entry = {
                "score": round(s.final_score, 1),
                "verdict": s.verdict.value,
                "bear_mos": round(s.dcf.bear_mos, 1) if s.dcf and s.dcf.bear_mos is not None else None,
                "date": run_date,
            }
            ticker_history = history.get(s.ticker, [])
            # Avoid duplicate entries for the same calendar date
            if ticker_history and ticker_history[-1].get("date") == run_date:
                ticker_history[-1] = entry  # overwrite same-day entry
            else:
                ticker_history.append(entry)
            history[s.ticker] = ticker_history[-_HISTORY_MAX_ENTRIES:]
        _SCORE_HISTORY_PATH.write_text(json.dumps(history, indent=2))
    except Exception:
        pass


def _load_last_scores() -> dict:
    """Load previous run scores for delta comparison."""
    try:
        if _LAST_SCORES_PATH.exists():
            return json.loads(_LAST_SCORES_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_last_scores(scores: list, fundamentals_map: dict) -> None:
    """Persist current run scores for next-run delta comparison."""
    try:
        data = {}
        for s in scores:
            f = fundamentals_map.get(s.ticker)
            data[s.ticker] = {
                "score": round(s.final_score, 1),
                "verdict": s.verdict.value,
                "price": round(f.current_price, 2) if f and f.current_price else None,
                "base_mos": round(s.dcf.margin_of_safety_base, 1) if s.dcf and s.dcf.margin_of_safety_base is not None else None,
                "bear_mos": round(s.dcf.bear_mos, 1) if s.dcf and s.dcf.bear_mos is not None else None,
                "date": datetime.now().strftime("%Y-%m-%d"),
            }
        _LAST_SCORES_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _load_portfolio() -> dict:
    """Load portfolio positions from data/portfolio.json."""
    try:
        if _PORTFOLIO_PATH.exists():
            raw = json.loads(_PORTFOLIO_PATH.read_text())
            # Strip comment keys used in the template
            return {k: v for k, v in raw.items() if not k.startswith("_comment")}
    except Exception:
        pass
    return {}


def _print_portfolio_review(portfolio: dict, scores: list, fundamentals_map: dict) -> None:
    """
    Print a portfolio review table comparing held positions against current scores.
    Detects thesis changes (verdict downgrade, bear MoS sign flip, score decay).
    """
    if not portfolio:
        return

    _PA_PLUS = {"Strong Candidate", "Research Further", "Potentially Attractive"}

    score_map = {s.ticker: s for s in scores}
    total_market_value = 0.0
    total_cost_value   = 0.0
    total_pnl          = 0.0
    any_alert          = False

    print("\n" + "=" * 70)
    print("  PORTFOLIO REVIEW")
    print("=" * 70)
    print(f"{'Ticker':<7} {'Shares':>6} {'Cost':>8} {'Now':>8} {'P&L $':>9} {'P&L%':>6} "
          f"{'Score':>6} {'Bear':>6}  {'Thesis Status'}")
    print("-" * 80)

    review_rows = []
    for ticker, pos in portfolio.items():
        cost    = pos.get("cost_basis", 0.0)
        shares  = pos.get("shares", 0)
        t_score = pos.get("thesis_score")
        t_verd  = pos.get("thesis_verdict", "")
        t_bear  = pos.get("thesis_bear_mos")

        s = score_map.get(ticker)
        f = fundamentals_map.get(ticker)
        cur_price = (f.current_price if f and f.current_price else None)

        # P&L
        if cur_price and shares:
            market_val = cur_price * shares
            cost_val   = cost * shares
            pnl_abs    = market_val - cost_val
            pnl_pct    = (cur_price - cost) / cost * 100 if cost else 0
            total_market_value += market_val
            total_cost_value   += cost_val
            total_pnl          += pnl_abs
            now_str  = f"${cur_price:.2f}"
            pnl_str  = f"${pnl_abs:+.0f}"
            pnlp_str = f"{pnl_pct:+.1f}%"
        else:
            now_str = pnl_str = pnlp_str = "—"

        # Thesis status
        if s:
            cur_verdict  = s.verdict.value
            cur_score    = s.final_score
            cur_bear     = s.dcf.bear_mos if s.dcf else None
            was_pa_plus  = t_verd in _PA_PLUS
            now_pa_plus  = cur_verdict in _PA_PLUS
            bear_flipped = (
                t_bear is not None and cur_bear is not None
                and (t_bear > 0) != (cur_bear > 0)
            )
            score_decay  = (t_score is not None and cur_score < t_score - 3)

            if was_pa_plus and cur_verdict == "Ignore":
                status = "🔴 SELL — verdict collapsed"
                any_alert = True
            elif was_pa_plus and not now_pa_plus:
                status = f"🟠 REDUCE — downgraded to {cur_verdict}"
                any_alert = True
            elif bear_flipped and was_pa_plus:
                status = "⚠️  REVIEW — bear MoS sign flipped"
                any_alert = True
            elif score_decay:
                status = f"⚠️  WATCH — score -({cur_score - t_score:.1f}) since entry"
                any_alert = True
            else:
                status = "✅ Thesis intact"

            score_str = f"{cur_score:.1f}"
            bear_str = (
                f"🛡+{cur_bear:.0f}%" if cur_bear and cur_bear > 0
                else (f"{cur_bear:.0f}%" if cur_bear is not None else "—")
            )
        else:
            status = "— (not in current run)"
            score_str = "—"
            bear_str  = "—"

        review_rows.append((ticker, shares, cost, now_str, pnl_str, pnlp_str,
                             score_str, bear_str, status))

    for ticker, shares, cost, now_str, pnl_str, pnlp_str, score_str, bear_str, status in review_rows:
        print(f"{ticker:<7} {shares:>6} ${cost:>7.2f} {now_str:>8} {pnl_str:>9} {pnlp_str:>6} "
              f"{score_str:>6} {bear_str:>6}  {status}")

    print("-" * 80)
    if total_cost_value > 0:
        total_pnl_pct = total_pnl / total_cost_value * 100
        print(f"  Total cost: ${total_cost_value:,.0f} | "
              f"Market value: ${total_market_value:,.0f} | "
              f"P&L: ${total_pnl:+,.0f} ({total_pnl_pct:+.1f}%)")
    if any_alert:
        print("\n  ⚠️  ACTION REQUIRED: Position management signals above. See Changes Since Last Run in report.")
    else:
        print("  ✅ All positions: thesis intact")
    print("=" * 70 + "\n")


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
                    svc_note = " (backlog suppressed for services)" if dominant_sector in _SERVICES_SECTORS else ""
                    raw_cagr = xbrl.get("rev_cagr_3yr")
                    cagr_note = " ⚠ spinoff/divestiture artifact — suppressed" if raw_cagr is not None and raw_cagr < -12 else ""
                    print(f" backlog={b2r} fcf3yr={fcf3} cagr3yr={cagr}{svc_note}{cagr_note}")
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

    # ── Macro context ─────────────────────────────────────────────────────────
    print("[3b/4] Fetching macro context (10-yr yield)...")
    macro_ctx = get_macro_context(live=live)
    if macro_ctx.ten_year_yield is not None:
        delta_str = f"{macro_ctx.rate_delta_pp:+.2f}pp vs DCF baseline" if macro_ctx.rate_delta_pp is not None else ""
        print(f"       10-yr yield: {macro_ctx.ten_year_yield:.2f}% {delta_str}")
    elif macro_ctx.fetch_error:
        print(f"       10-yr yield: unavailable ({macro_ctx.fetch_error})")
    else:
        print("       10-yr yield: unavailable (offline mode)")

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
    if args.min_liquidity > 0:
        before = len(scores)
        def _above_liquidity_floor(s):
            f = fundamentals_map.get(s.ticker)
            if f is None or f.avg_daily_volume is None or f.current_price is None:
                return True  # no volume data — pass through
            dollar_vol_m = f.avg_daily_volume * f.current_price / 1_000_000
            return dollar_vol_m >= args.min_liquidity
        scores = [s for s in scores if _above_liquidity_floor(s)]
        dropped = before - len(scores)
        if dropped:
            print(f'Liquidity filter (>${args.min_liquidity:.0f}M/day) dropped {dropped} company/companies.')
    if args.specialist_only:
        scores = [s for s in scores
                  if s.specialist and s.specialist.status.value in ('In Tier', 'Near Tier')]
        print(f'Specialist filter applied — {len(scores)} companies remain.')

    if args.top:
        scores = scores[:args.top]

    # ── Step 4: Print summary ─────────────────────────────────────────────────
    last_scores   = _load_last_scores()
    score_history = _load_score_history()
    print("\n[4/4] Results\n")
    print(f"{'#':<3} {'Ticker':<8} {'Score':>6} {'Chg':>5} {'Price':>7} {'Data':>5} {'MoS':>6} {'Bear':>6}  {'Verdict':<28} {'Sector'}")
    print("-" * 115)
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
            mv = s.dcf.margin_of_safety_base
            is_ignore = s.verdict == Verdict.IGNORE
            if is_ignore and mv > 0:
                mos_str = "—†"   # suppress: commercial DCF artifact, not a DoD signal
            else:
                mos_str = "+0%" if abs(mv) < 0.5 else f"{mv:+.0f}%"
        bear_str = "N/A"
        if s.dcf and s.dcf.bear_mos is not None:
            bm = s.dcf.bear_mos
            is_pa_plus = s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
            is_ignore = s.verdict == Verdict.IGNORE
            if is_ignore and bm > 0:
                bear_str = "—†"  # suppress: same reason as MoS
            elif bm > 0 and is_pa_plus:
                bear_str = f"🛡{bm:+.0f}%"
            else:
                bear_str = "+0%" if abs(bm) < 0.5 else f"{bm:+.0f}%"
        f_row = fundamentals_map.get(s.ticker)
        price_str = f"${f_row.current_price:.0f}" if f_row and f_row.current_price else "  —"
        prev = last_scores.get(s.ticker)
        if prev:
            delta = s.final_score - prev["score"]
            chg_str = f"{delta:+.1f}" if abs(delta) >= 0.1 else "  ="
        else:
            chg_str = " new"
        print(f"{i:<3} {s.ticker:<8} {s.final_score:>6.1f} {chg_str:>5} {price_str:>7} {data_str:>5} {mos_str:>6} {bear_str:>7}  {emoji} {s.verdict.value:<26} {s.sector.value}")

    # Portfolio review — auto-enabled if data/portfolio.json exists
    portfolio = _load_portfolio()
    if portfolio and (args.portfolio or _PORTFOLIO_PATH.exists()):
        _print_portfolio_review(portfolio, scores, fundamentals_map)

    # Save scores for next-run delta comparison (only when live and not mock)
    if live and args.source != "mock":
        _save_last_scores(scores, fundamentals_map)
        _update_score_history(scores, fundamentals_map)

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
            last_scores=last_scores,
            score_history=score_history,
            macro_context=macro_ctx,
            portfolio=portfolio,
            portfolio_size=args.portfolio_size,
            brief=getattr(args, "brief", False),
        )
        save_report(report_content, output_path)
        print(f"\nReport → {output_path}")

    print("\nDone.\n")
    return scores


def _send_alert_email(
    alerts: list,
    to_email: str,
    from_email: str = None,
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> bool:
    """Send a material-change alert email via SMTP.

    Credentials: set DOD_AGENT_SMTP_PASSWORD in your environment.
    For Gmail: generate an App Password at myaccount.google.com/apppasswords
    and export DOD_AGENT_SMTP_PASSWORD=<16-char app password>.
    """
    import smtplib
    from email.mime.text import MIMEText

    password = os.environ.get("DOD_AGENT_SMTP_PASSWORD")
    if not password:
        print("  ⚠️  DOD_AGENT_SMTP_PASSWORD not set — email alert skipped")
        return False

    from_email = from_email or to_email
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    body = (
        f"DoD Contract Intelligence Agent — Material Changes Detected\n"
        f"{run_date}\n"
        f"{'─' * 60}\n\n"
        + "\n".join(alerts)
        + "\n\n"
        + "─" * 60
        + "\nThis is an automated alert from your DoD Contract Intelligence Agent.\n"
        + "Run `python main.py` to see the full report.\n"
    )

    msg = MIMEText(body)
    msg["Subject"] = f"[DoD Agent] Material Changes — {run_date}"
    msg["From"] = from_email
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(from_email, password)
            server.send_message(msg)
        print(f"  📧 Alert email sent → {to_email}")
        return True
    except Exception as e:
        print(f"  ⚠️  Email alert failed: {e}")
        return False


def _check_material_changes(scores: list, last_scores: dict, fundamentals_map: dict) -> list[str]:
    """Return a list of alert strings for conditions that warrant immediate attention."""
    _PA_PLUS = {"Strong Candidate", "Research Further", "Potentially Attractive"}
    alerts = []
    for s in scores:
        prev = last_scores.get(s.ticker)
        is_pa_plus_now = s.verdict.value in _PA_PLUS
        was_pa_plus    = prev and prev.get("verdict") in _PA_PLUS

        # Exit signals
        if was_pa_plus and s.verdict.value == "Ignore":
            alerts.append(f"🔴 SELL {s.ticker} — verdict collapsed PA+ → Ignore (score {prev['score']:.0f} → {s.final_score:.0f})")
        elif was_pa_plus and not is_pa_plus_now:
            alerts.append(f"🟠 REDUCE {s.ticker} — downgraded PA+ → {s.verdict.value} (score {prev['score']:.0f} → {s.final_score:.0f})")

        # Bear MoS sign flip on a PA+ name
        if is_pa_plus_now and prev and s.dcf and s.dcf.bear_mos is not None:
            old_bear = prev.get("bear_mos")
            if old_bear is not None and old_bear > 0 and s.dcf.bear_mos < 0:
                alerts.append(f"⚠️  REVIEW {s.ticker} — bear MoS flipped {old_bear:+.0f}% → {s.dcf.bear_mos:+.0f}%: downside protection lost")

        # Earnings imminent for PA+ names
        if is_pa_plus_now:
            f_w = fundamentals_map.get(s.ticker)
            if f_w and f_w.next_earnings_date:
                try:
                    days_out = (datetime.strptime(f_w.next_earnings_date, "%Y-%m-%d") - datetime.now()).days
                    if 0 < days_out <= 7:
                        alerts.append(f"📅 EARNINGS {s.ticker} in {days_out} day(s) — position automatically halved until post-report")
                except Exception:
                    pass

        # New highest-conviction entry
        if is_pa_plus_now and not was_pa_plus and s.dcf and s.dcf.bear_mos is not None and s.dcf.bear_mos > 0:
            alerts.append(f"🟢 NEW BUY {s.ticker} — entered Highest Conviction tier (score {s.final_score:.0f}, bear MoS 🛡 {s.dcf.bear_mos:+.0f}%)")

    return alerts


def _run_watch_loop(args) -> None:
    """Continuous monitoring: re-run every watch_interval seconds, print only material alerts."""
    import time
    print(f"Watch mode active — polling every {args.watch_interval // 3600}h {(args.watch_interval % 3600) // 60}m. Ctrl-C to stop.\n")
    iteration = 0
    while True:
        iteration += 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"[{now_str}] Run #{iteration}...", end="", flush=True)
        try:
            scores = main()
            if scores:
                last = _load_last_scores()
                # Re-fetch fundamentals map from last run (stored in last_scores for prices)
                # Use the scores we just got
                alerts = []
                # We need fundamentals_map — re-build a minimal version from last_scores prices
                # (full fundamentals not returned by main(); alerts use last_scores for prior state)
                print(f" {len(scores)} companies scored.")
                last_after = _load_last_scores()
                # Build prior state from last run's last_scores BEFORE this run
                alerts_txt = []
                for s in scores:
                    prev = last.get(s.ticker)
                    is_pa_plus_now = s.verdict.value in {"Strong Candidate", "Research Further", "Potentially Attractive"}
                    was_pa_plus = prev and prev.get("verdict") in {"Strong Candidate", "Research Further", "Potentially Attractive"}
                    if was_pa_plus and s.verdict.value == "Ignore":
                        alerts_txt.append(f"  🔴 SELL {s.ticker}: PA+ → Ignore")
                    elif was_pa_plus and not is_pa_plus_now:
                        alerts_txt.append(f"  🟠 REDUCE {s.ticker}: PA+ → {s.verdict.value}")
                    if is_pa_plus_now and prev and s.dcf and s.dcf.bear_mos is not None:
                        old_b = prev.get("bear_mos")
                        if old_b is not None and old_b > 0 > s.dcf.bear_mos:
                            alerts_txt.append(f"  ⚠️  REVIEW {s.ticker}: bear MoS {old_b:+.0f}% → {s.dcf.bear_mos:+.0f}%")
                    if is_pa_plus_now and not was_pa_plus and s.dcf and s.dcf.bear_mos and s.dcf.bear_mos > 0:
                        alerts_txt.append(f"  🟢 NEW BUY {s.ticker}: Highest Conviction (score {s.final_score:.0f})")
                if alerts_txt:
                    print("\n" + "=" * 50)
                    print("  MATERIAL CHANGES — ACTION REQUIRED")
                    print("=" * 50)
                    for a in alerts_txt:
                        print(a)
                    print("=" * 50 + "\n")
                    # Send email alert if configured
                    if getattr(args, "alert_email", None):
                        _send_alert_email(
                            alerts_txt,
                            to_email=args.alert_email,
                            from_email=getattr(args, "smtp_from", None),
                            smtp_server=getattr(args, "smtp_server", "smtp.gmail.com"),
                        )
                else:
                    print("  No material changes.")
            else:
                print(" no scores returned.")
        except KeyboardInterrupt:
            print("\nWatch mode stopped.")
            return
        except Exception as e:
            print(f" ERROR: {e}")
        try:
            time.sleep(args.watch_interval)
        except KeyboardInterrupt:
            print("\nWatch mode stopped.")
            return


if __name__ == "__main__":
    args_check = parse_args()
    if args_check.watch:
        _run_watch_loop(args_check)
    else:
        main()
