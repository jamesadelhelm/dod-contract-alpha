"""
Report generator: produces a detailed markdown analyst-style report.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from typing import List, Dict
from src.models import CompanyScore, Contract, Verdict, Sector, SpecialistTierStatus


VERDICT_EMOJI = {
    Verdict.STRONG_CANDIDATE: "🟢",
    Verdict.RESEARCH_FURTHER: "🟡",   # high score but analyst divergence — needs diligence
    Verdict.POTENTIALLY_ATTRACTIVE: "🟡",
    Verdict.WATCHLIST: "🔵",
    Verdict.HIGH_QUALITY_BUT_EXPENSIVE: "🟠",
    Verdict.LOW_CONVICTION: "⚪",
    Verdict.IGNORE: "🔴",
}

SCORE_BAR_CHARS = 20


def _bar(score: float, max_score: float = 100, width: int = 20) -> str:
    filled = int(round(score / max_score * width))
    return "█" * filled + "░" * (width - filled)


def _fmt_millions(v: float) -> str:
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:.0f}M"


def _score_table_row(label: str, score: float, weight_pct: int) -> str:
    return f"| {label:<30} | {score:>6.1f}/100 | {weight_pct:>4}% | {_bar(score)} |"


def generate_report(
    ranked_scores: List[CompanyScore],
    private_contracts: List[Contract],
    all_contracts: List[Contract],
    run_date: str = None,
    live: bool = True,
    fundamentals_map: Dict = None,
) -> str:
    run_date = run_date or datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    unmatched_value = sum(c.contract_value for c in private_contracts if c.contract_value)
    unmatched_str = f"${unmatched_value:.0f}M" if unmatched_value else "$0M"

    lines += [
        "# 📊 DoD Contract Intelligence Report",
        "",
        f"> **Generated:** {run_date}  ",
        f"> **Contracts Analyzed:** {len(all_contracts)}  ",
        f"> **Public Companies Identified:** {len(ranked_scores)}  ",
        f"> **Private / No Ticker:** {len(private_contracts)} ({unmatched_str} unresolved — see Section 9)  ",
        "",
        "> ⚠️ **IMPORTANT DISCLAIMER:** This report is for research and informational purposes only.",
        "> It does not constitute investment advice, a recommendation to buy or sell any security,",
        "> or a solicitation of any investment. All scores must be independently verified.",
        "> Past contract wins do not guarantee future performance.",
        "> Consult a licensed financial advisor before making any investment decisions.",
        "",
        "---",
        "",
    ]

    # ── 1. Executive Summary ──────────────────────────────────────────────────
    lines += [
        "## 1. Executive Summary",
        "",
        "This report analyzes recent DoD contract awards to identify publicly traded companies",
        "with potentially durable, high-quality exposure to U.S. government funding.",
        "Companies are scored using a Buffett/Graham-style framework emphasizing business quality,",
        "conservative valuation, government revenue durability, and balance sheet strength.",
        "",
        "**Coverage includes:** traditional defense, aerospace, shipbuilding, space, cybersecurity,",
        "AI/data, cloud/IT, military healthcare, pharmaceuticals, medical devices, logistics,",
        "infrastructure, energy/nuclear, and industrial components.",
        "",
        "**Analytical philosophy:** A large contract does not automatically make a good investment.",
        "A well-managed company with durable moat, reasonable valuation, and strong government",
        "relationships matters far more than any single contract headline.",
        "",
    ]

    # Top-line summary
    strong = [s for s in ranked_scores if s.verdict in [Verdict.STRONG_CANDIDATE, Verdict.RESEARCH_FURTHER]]
    attractive = [s for s in ranked_scores if s.verdict == Verdict.POTENTIALLY_ATTRACTIVE]
    expensive = [s for s in ranked_scores if s.verdict == Verdict.HIGH_QUALITY_BUT_EXPENSIVE]
    watchlist = [s for s in ranked_scores if s.verdict == Verdict.WATCHLIST]

    lines += [
        f"| Verdict | Companies |",
        f"|---------|-----------|",
        f"| 🟢 Strong Candidate / Research Further | {len(strong)} |",
        f"| 🟡 Potentially Attractive | {len(attractive)} |",
        f"| 🟠 High Quality But Expensive | {len(expensive)} |",
        f"| 🔵 Watchlist | {len(watchlist)} |",
        "",
        "---",
        "",
    ]

    # ── 2. Top Ranked Public Companies ───────────────────────────────────────
    lines += [
        "## 2. Top Ranked Public Companies",
        "",
        "> **Data**: % of 16 key fundamental inputs that are real (non-stub) values.",
        "> **MoS**: Margin of safety vs. DCF base-case intrinsic value. Positive = stock is cheap.",
        "",
        "| # | Ticker | Company | Sector | Score | Data | MoS | Verdict |",
        "|---|--------|---------|--------|-------|------|-----|---------|",
    ]
    for i, s in enumerate(ranked_scores, 1):
        emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
        data_str = f"{s.data_completeness_pct:.0f}%"
        if s.data_completeness_pct < 50:
            data_str += " ⚠️"
        mos_str = "N/A"
        if s.dcf and s.dcf.margin_of_safety_base is not None:
            mos_val = s.dcf.margin_of_safety_base
            mos_str = f"{mos_val:+.0f}%"
        lines.append(
            f"| {i} | **{s.ticker}** | {s.company_name} | {s.sector.value} "
            f"| **{s.final_score:.1f}** | {data_str} | {mos_str} | {emoji} {s.verdict.value} |"
        )
    lines += ["", "---", ""]

    # ── 3. New Contract Signals ───────────────────────────────────────────────
    lines += [
        "## 3. New Contract Signals",
        "",
        "Raw contract awards analyzed in this report:",
        "",
        "| Awardee | Ticker | Value | Funded | Type | Agency | Sector |",
        "|---------|--------|-------|--------|------|--------|--------|",
    ]
    for c in sorted(all_contracts, key=lambda x: x.contract_value or 0, reverse=True):
        ticker_str = c.ticker or ("*private*" if c.parent_company else "*unknown*")
        funded_str = _fmt_millions(c.funded_amount) if c.funded_amount else "N/A"
        lines.append(
            f"| {c.awardee_name[:35]} | {ticker_str} | {_fmt_millions(c.contract_value)} "
            f"| {funded_str} | {c.contract_type.value} | {(c.agency or '')[:25]} | {c.sector.value} |"
        )
    lines += ["", "---", ""]


    # ── 3b. Specialist Tier ────────────────────────────────────────────────────
    lines += [
        '## 3b. Specialist Tier Analysis',
        '',
        'This section surfaces companies in the mid-cap, high-DoD-concentration sweet spot',
        'where contract signals are most actionable — before institutional coverage fully',
        'catches up. Large-cap primes are excluded; their contract news is priced in immediately.',
        '',
        '**Criteria:** Market cap $400M–$15B | DoD revenue ≥35% | Contract ≥3% of revenue',
        '',
    ]

    in_tier  = [s for s in ranked_scores if s.specialist and s.specialist.status.value == "In Tier"]
    near_tier = [s for s in ranked_scores if s.specialist and s.specialist.status.value == "Near Tier"]
    large_primes = [s for s in ranked_scores if s.specialist and s.specialist.status.value == "Large Prime"]

    if in_tier:
        lines += ["### 🎯 In-Tier Companies (Sweet Spot)", ""]
        lines += ["| Ticker | Score | Mkt Cap | DoD Rev% | Contract/Rev% | Sole Source | Bonus | Status |",
                  "|--------|-------|---------|----------|---------------|-------------|-------|--------|"]
        for s in in_tier:
            sp = s.specialist
            mc_str  = f"${sp.market_cap_millions:.0f}M" if sp.market_cap_millions else "N/A"
            dod_str = f"{sp.dod_revenue_pct:.0f}%" if sp.dod_revenue_pct is not None else "N/A"
            cvr_str = f"{sp.contract_to_revenue_pct:.1f}%" if sp.contract_to_revenue_pct is not None else "N/A"
            ss_str  = "✅" if sp.is_sole_source else "—"
            bonus_str = f"+{sp.score_adjustment:.1f}" if sp.score_adjustment > 0 else "0"
            lines.append(f"| **{s.ticker}** | {s.final_score:.1f} | {mc_str} | {dod_str} | {cvr_str} | {ss_str} | {bonus_str} | {sp.status.value} |")
        lines.append("")
        for s in in_tier:
            sp = s.specialist
            lines += [
                f"**{s.ticker} — {s.company_name}**",
                f"*{sp.rationale}*",
                f"*Coverage: {sp.analyst_coverage_note}*",
                "",
            ]

    if near_tier:
        lines += ["### 🔍 Near-Tier Companies (Worth Monitoring)", ""]
        lines += ["| Ticker | Score | Mkt Cap | DoD Rev% | Contract/Rev% | Bonus |",
                  "|--------|-------|---------|----------|---------------|-------|"]
        for s in near_tier:
            sp = s.specialist
            mc_str  = f"${sp.market_cap_millions:.0f}M" if sp.market_cap_millions else "N/A"
            dod_str = f"{sp.dod_revenue_pct:.0f}%" if sp.dod_revenue_pct is not None else "N/A"
            cvr_str = f"{sp.contract_to_revenue_pct:.1f}%" if sp.contract_to_revenue_pct is not None else "N/A"
            bonus_str = f"+{sp.score_adjustment:.1f}" if sp.score_adjustment > 0 else "0"
            lines.append(f"| {s.ticker} | {s.final_score:.1f} | {mc_str} | {dod_str} | {cvr_str} | {bonus_str} |")
        lines.append("")

    if large_primes:
        lines += ["### 🏭 Large-Cap Primes (Excluded from Specialist Filter)", ""]
        lines.append("Contract news for these companies is typically priced in by institutional desks")
        lines.append("within hours of announcement. They appear in the main rankings but receive no specialist bonus.")
        lines.append("")
        prime_tickers = ", ".join(s.ticker for s in large_primes)
        lines.append(f"Identified: {prime_tickers}")
        lines.append("")

    lines += ["---", ""]

    # ── 4–6. Individual Company Deep Dives ───────────────────────────────────
    lines += [
        "## 4. Buffett/Graham Quality Review & Scoring Detail",
        "",
    ]

    for s in ranked_scores:
        emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
        f_ctx = (fundamentals_map or {}).get(s.ticker)
        # Build market context line from live data when available
        ctx_parts = []
        if f_ctx:
            if f_ctx.current_price:
                ctx_parts.append(f"Price: **${f_ctx.current_price:.2f}**")
            if f_ctx.price_52w_high and f_ctx.price_52w_low:
                hi, lo = f_ctx.price_52w_high, f_ctx.price_52w_low
                ctx_parts.append(f"52W: ${lo:.2f}–${hi:.2f}")
                if f_ctx.pct_off_52w_high is not None:
                    ctx_parts.append(f"({f_ctx.pct_off_52w_high:+.0f}% off high)")
            if f_ctx.return_1yr is not None:
                ctx_parts.append(f"1yr return: {f_ctx.return_1yr:+.1f}%")
            if f_ctx.analyst_target_price:
                ctx_parts.append(f"Analyst target: ${f_ctx.analyst_target_price:.2f}")
                if f_ctx.upside_to_target is not None:
                    ctx_parts.append(f"({f_ctx.upside_to_target:+.1f}% upside)")
            if f_ctx.analyst_recommendation and f_ctx.analyst_count:
                ctx_parts.append(
                    f"Consensus: **{f_ctx.analyst_recommendation}** ({f_ctx.analyst_count} analysts)"
                )
            if f_ctx.short_pct_of_float is not None:
                short_warn = " ⚠️" if f_ctx.short_pct_of_float > 15 else ""
                ctx_parts.append(f"Short float: {f_ctx.short_pct_of_float:.1f}%{short_warn}")
            if f_ctx.next_earnings_date:
                from datetime import datetime as _dt2
                try:
                    days_out = (_dt2.strptime(f_ctx.next_earnings_date, "%Y-%m-%d") - _dt2.now()).days
                    earn_warn = " ⚠️" if days_out <= 14 else ""
                    ctx_parts.append(f"Earnings: {f_ctx.next_earnings_date} ({days_out}d){earn_warn}")
                except Exception:
                    ctx_parts.append(f"Earnings: {f_ctx.next_earnings_date}")
        mkt_line = " | ".join(ctx_parts) if ctx_parts else "*Market data unavailable in this run mode*"

        lines += [
            f"### {emoji} {s.ticker} — {s.company_name}",
            f"**Sector:** {s.sector.value}  |  **Final Score: {s.final_score:.1f}/100**  |  **Verdict: {s.verdict.value}**",
            "",
            f"> {mkt_line}",
            "",
            "#### Score Breakdown",
            "",
            f"| Component | Score | Weight | Visual |",
            f"|-----------|-------|--------|--------|",
            _score_table_row("Buffett Quality", s.buffett_quality.raw, 25),
            _score_table_row("Graham Value", s.graham_value.raw, 20),
            _score_table_row("DoD Stability", s.dod_stability.raw, 20),
            _score_table_row("Management Quality", s.management.raw, 15),
            _score_table_row("Contract Catalyst", s.contract_catalyst.raw, 10),
            _score_table_row("Balance Sheet", s.balance_sheet.raw, 10),
            f"| **FINAL (weighted)** | **{s.final_score:.1f}** | 100% | {_bar(s.final_score)} |",
            "",
            f"*{s.overall_explanation}*",
            "",
            "#### Score Component Details",
            "",
            f"- **Buffett Quality ({s.buffett_quality.raw:.0f}/100):** {s.buffett_quality.explanation}",
            f"- **Graham Value ({s.graham_value.raw:.0f}/100):** {s.graham_value.explanation}",
            f"- **DoD Stability ({s.dod_stability.raw:.0f}/100):** {s.dod_stability.explanation}",
            f"- **Management ({s.management.raw:.0f}/100):** {s.management.explanation}",
            f"- **Contract Catalyst ({s.contract_catalyst.raw:.0f}/100):** {s.contract_catalyst.explanation}",
            f"- **Balance Sheet ({s.balance_sheet.raw:.0f}/100):** {s.balance_sheet.explanation}",
            "",
        ]

        # Recent contracts for this company
        if s.recent_contracts:
            lines += ["#### Recent DoD Contracts", ""]
            for c in s.recent_contracts:
                funded_note = f" (${c.funded_amount:.0f}M funded)" if c.funded_amount and c.funded_amount != c.contract_value else ""
                ss_note = " — **SOLE SOURCE**" if c.is_sole_source else ""
                idiq_note = " — *IDIQ ceiling*" if c.is_idiq else ""
                lines += [
                    f"- **{_fmt_millions(c.contract_value)}{funded_note}** — {c.contract_type.value}{ss_note}{idiq_note}",
                    f"  - *{(c.agency or 'Unknown agency')}*",
                    f"  - {c.description[:200]}{'...' if len(c.description) > 200 else ''}",
                    f"  - Completion: {c.completion_date or 'N/A'}",
                    "",
                ]

        lines += [
            "#### Investment Analysis",
            "",
            f"**Why it matters:** {s.why_it_matters}",
            "",
            f"**Why it might not matter:** {s.why_it_might_not_matter}",
            "",
            "**Key Risks:**",
        ]
        for r in s.key_risks:
            lines.append(f"- {r}")
        lines += [
            "",
            "**What to Verify Next:**",
        ]
        for v in s.what_to_verify:
            lines.append(f"- {v}")

        if s.red_flags:
            lines += ["", "**⚠️ Red Flags:**"]
            for f_item in s.red_flags:
                lines.append(f"- 🚩 {f_item}")

        if s.low_ticker_confidence:
            lines.append("")
            lines.append("**⚠️ LOW TICKER CONFIDENCE** — Verify parent company mapping manually.")

        lines += ["", "---", ""]

    # ── 5. Government Funding Durability Summary ──────────────────────────────
    lines += [
        "## 5. Government Funding Durability Summary",
        "",
        "| Ticker | DoD Rev % | Gov Rev % | Backlog/Rev | Moat | Sole Source | DoD Stability Score |",
        "|--------|-----------|-----------|-------------|------|-------------|---------------------|",
    ]
    for s in ranked_scores:
        f = (fundamentals_map or {}).get(s.ticker)
        if f is None:
            from src.fundamentals import get_fundamentals_or_stub
            f = get_fundamentals_or_stub(s.ticker, live=live)
        ss = "Yes" if any(c.is_sole_source for c in s.recent_contracts) else "No"
        dod_pct = f"{f.dod_revenue_pct:.0f}%" if f.dod_revenue_pct is not None else "N/A"
        gov_pct = f"{f.government_revenue_pct:.0f}%" if f.government_revenue_pct is not None else "N/A"
        bl = f"{f.backlog_to_revenue:.1f}x" if f.backlog_to_revenue else "N/A"
        lines.append(
            f"| {s.ticker} | {dod_pct} | {gov_pct} | {bl} | {f.moat_rating or 'N/A'} | {ss} | {s.dod_stability.raw:.0f} |"
        )
    lines += ["", "---", ""]

    # ── 6. Valuation Notes + DCF ─────────────────────────────────────────────
    lines += [
        "## 6. Valuation Analysis",
        "",
        "### 6a. Market Multiples",
        "",
        "| Ticker | P/E | Fwd P/E | EV/EBITDA | FCF Yield | Div Yield | Share Chg YoY | D/E | Graham Score |",
        "|--------|-----|---------|-----------|-----------|-----------|---------------|-----|-------------|",
    ]
    for s in ranked_scores:
        f = (fundamentals_map or {}).get(s.ticker)
        if f is None:
            from src.fundamentals import get_fundamentals_or_stub
            f = get_fundamentals_or_stub(s.ticker, live=live)
        pe   = f"{f.pe_ratio:.0f}x"    if f.pe_ratio             else "N/A"
        fpe  = f"{f.forward_pe:.0f}x"  if f.forward_pe           else "N/A"
        ev   = f"{f.ev_ebitda:.0f}x"   if f.ev_ebitda            else "N/A"
        fcfy = f"{f.fcf_yield:.1f}%"   if f.fcf_yield            else "N/A"
        divy = f"{f.dividend_yield:.1f}%" if f.dividend_yield is not None else "—"
        sc   = f"{f.shares_chg_1yr_pct:+.1f}%" if f.shares_chg_1yr_pct is not None else "N/A"
        # Flag dilution > 3% and buyback < -2%
        if f.shares_chg_1yr_pct is not None:
            if f.shares_chg_1yr_pct > 3:
                sc += " 🔺"  # dilution
            elif f.shares_chg_1yr_pct < -2:
                sc += " ✅"  # buyback
        de   = f"{f.debt_equity:.1f}x" if f.debt_equity is not None else "N/A"
        lines.append(
            f"| {s.ticker} | {pe} | {fpe} | {ev} | {fcfy} | {divy} | {sc} | {de} | {s.graham_value.raw:.0f} |"
        )
    lines += ["", "### 6b. DCF Intrinsic Value Estimates", ""]
    lines += [
        "> Buffett-style owner earnings DCF with 3 scenarios. Discount rate is adjusted",
        "> for DoD revenue concentration, moat, leverage, and size. Margin of safety (MoS)",
        "> is positive when intrinsic value exceeds current price.",
        "> **These are estimates, not predictions. Treat ranges as a thinking framework.**",
        "",
        "| Ticker | Bear IV | Base IV | Bull IV | MoS (Base) | Implied Growth | DCF Verdict | Score |",
        "|--------|---------|---------|---------|------------|----------------|-------------|-------|",
    ]
    for s in ranked_scores:
        if s.dcf:
            d = s.dcf
            bear = f"${d.bear_iv:.0f}" if d.bear_iv else "N/A"
            base = f"${d.base_iv:.0f}" if d.base_iv else "N/A"
            bull = f"${d.bull_iv:.0f}" if d.bull_iv else "N/A"
            mos  = f"{d.margin_of_safety_base:+.0f}%" if d.margin_of_safety_base is not None else "N/A"
            impl = f"{d.implied_growth_rate:.0f}%/yr" if d.implied_growth_rate is not None else "N/A"
            verd = d.verdict[:28] if d.verdict else "N/A"
            score = f"{d.valuation_score:.0f}"
        else:
            bear = base = bull = mos = impl = verd = score = "N/A"
        lines.append(f"| {s.ticker} | {bear} | {base} | {bull} | {mos} | {impl} | {verd} | {score} |")
    lines += [""]

    # DCF deep dive per company
    lines += ["### 6c. DCF Detail by Company", ""]
    for s in ranked_scores:
        if not s.dcf:
            continue
        d = s.dcf
        lines += [f"**{s.ticker} — {s.company_name}**", ""]

        # Discount rate build-up
        lines.append(f"*Discount rate: {d.discount_rate_base:.2f}% (base 9.0%)*")
        for adj in d.discount_rate_adjustments:
            lines.append(f"  - {adj}")
        lines.append("")

        # Scenario table
        lines += [
            "| Scenario | Rev Growth Yr1-5 | IV/Share | MoS vs Price |",
            "|----------|-----------------|----------|--------------|",
        ]
        scenarios = [
            ("🐻 Bear", d.bear_growth, d.bear_iv, d.bear_mos),
            ("📊 Base", d.base_growth, d.base_iv, d.margin_of_safety_base),
            ("🐂 Bull", d.bull_growth, d.bull_iv, d.bull_mos),
        ]
        for label, g, iv, mos in scenarios:
            g_str  = f"{g:.0f}%/yr"     if g  is not None else "N/A"
            iv_str = f"${iv:.2f}"       if iv is not None else "N/A"
            mos_str= f"{mos:+.1f}%"     if mos is not None else "price unknown"
            lines.append(f"| {label} | {g_str} | {iv_str} | {mos_str} |")
        lines.append("")

        if d.implied_growth_rate is not None:
            lines.append(
                f"*Reverse DCF: current price implies **{d.implied_growth_rate:.1f}%/yr** "
                f"growth for 10 years at {d.discount_rate_base:.1f}% discount rate.*"
            )
            lines.append("")

        if d.valuation_note:
            lines.append(f"*{d.valuation_note}*")
            lines.append("")

        if d.caveats:
            lines.append("**DCF Caveats:**")
            for c in d.caveats:
                lines.append(f"- {c}")
            lines.append("")

    # ── 6d. Analyst Consensus & Price Momentum ───────────────────────────────
    lines += [
        "### 6d. Analyst Consensus & Price Momentum",
        "",
        "> **Why this matters:** Our scoring model is independent of the Street, but meaningful",
        "> analyst divergence (high score + sell consensus) is a required diligence flag —",
        "> not a veto, but a reason to understand *why* the Street disagrees before deploying capital.",
        "> 52-week position context distinguishes temporary dislocation from structural decline.",
        "",
        "| Ticker | Price | 52W Low | 52W High | Off High | 1Yr Rtn | Short % | Short Days | Target | Upside | # Analysts | Consensus | Next Earnings |",
        "|--------|-------|---------|----------|----------|---------|---------|------------|--------|--------|------------|-----------|---------------|",
    ]
    for s in ranked_scores:
        f_a = (fundamentals_map or {}).get(s.ticker)
        if not f_a:
            lines.append(f"| {s.ticker} | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        price_str = f"${f_a.current_price:.2f}" if f_a.current_price else "N/A"
        lo_str    = f"${f_a.price_52w_low:.2f}" if f_a.price_52w_low else "N/A"
        hi_str    = f"${f_a.price_52w_high:.2f}" if f_a.price_52w_high else "N/A"
        off_str   = f"{f_a.pct_off_52w_high:+.0f}%" if f_a.pct_off_52w_high is not None else "N/A"
        ret_str   = f"{f_a.return_1yr:+.1f}%" if f_a.return_1yr is not None else "N/A"
        # Short interest — flag high short % in context of positive score
        short_str = f"{f_a.short_pct_of_float:.1f}%" if f_a.short_pct_of_float is not None else "N/A"
        if f_a.short_pct_of_float is not None and f_a.short_pct_of_float > 15 and s.final_score >= 65:
            short_str = f"⚠️ {short_str}"
        shrt_days = f"{f_a.short_ratio_days:.1f}d" if f_a.short_ratio_days else "N/A"
        tgt_str   = f"${f_a.analyst_target_price:.2f}" if f_a.analyst_target_price else "N/A"
        up_str    = f"{f_a.upside_to_target:+.1f}%" if f_a.upside_to_target is not None else "N/A"
        n_str     = str(f_a.analyst_count) if f_a.analyst_count else "N/A"
        rec_str   = f_a.analyst_recommendation or "N/A"
        if f_a.analyst_recommendation in ("sell", "underperform") and s.final_score >= 65:
            rec_str = f"⚠️ {rec_str}"
        # Next earnings
        earn_str = "N/A"
        if f_a.next_earnings_date:
            try:
                from datetime import datetime as _dt3
                days_out = (_dt3.strptime(f_a.next_earnings_date, "%Y-%m-%d") - _dt3.now()).days
                earn_str = f"{f_a.next_earnings_date} ({days_out}d)"
                if days_out <= 14:
                    earn_str = f"⚠️ {earn_str}"
            except Exception:
                earn_str = f_a.next_earnings_date
        lines.append(
            f"| {s.ticker} | {price_str} | {lo_str} | {hi_str} | {off_str} | {ret_str} "
            f"| {short_str} | {shrt_days} | {tgt_str} | {up_str} | {n_str} | {rec_str} | {earn_str} |"
        )
    lines += [""]

    # ── 6e. Sector Peer Comparison (Relative Valuation) ──────────────────────
    lines += [
        "### 6e. Sector Peer Comparison — Relative Valuation",
        "",
        "> Each company's P/E, EV/EBITDA, and FCF yield are shown relative to the",
        "> median of peers in the same sector appearing in this analysis.",
        "> Premium = trading above sector median. Discount = below median.",
        "> Sectors with only one company are excluded (no peer group to compare).",
        "",
    ]

    from collections import defaultdict as _dd
    import statistics as _stats

    # Build sector buckets with fundamentals
    sector_buckets: dict = _dd(list)
    for s in ranked_scores:
        f_s = (fundamentals_map or {}).get(s.ticker)
        sector_buckets[s.sector.value].append((s, f_s))

    peer_section_written = False
    for sector_name, members in sorted(sector_buckets.items()):
        if len(members) < 2:
            continue
        peer_section_written = True

        # Compute sector medians (exclude None)
        pes   = [f_s.pe_ratio   for _, f_s in members if f_s and f_s.pe_ratio   is not None]
        evs   = [f_s.ev_ebitda  for _, f_s in members if f_s and f_s.ev_ebitda  is not None]
        fcfys = [f_s.fcf_yield  for _, f_s in members if f_s and f_s.fcf_yield  is not None]

        med_pe   = round(_stats.median(pes),   1) if len(pes)   >= 2 else None
        med_ev   = round(_stats.median(evs),   1) if len(evs)   >= 2 else None
        med_fcfy = round(_stats.median(fcfys), 1) if len(fcfys) >= 2 else None

        med_str = []
        if med_pe   is not None: med_str.append(f"P/E median: {med_pe:.1f}x")
        if med_ev   is not None: med_str.append(f"EV/EBITDA median: {med_ev:.1f}x")
        if med_fcfy is not None: med_str.append(f"FCF Yield median: {med_fcfy:.1f}%")

        lines += [
            f"#### {sector_name}",
            f"*{' | '.join(med_str) if med_str else 'Insufficient data for medians'}*",
            "",
            "| Ticker | P/E | vs Median | EV/EBITDA | vs Median | FCF Yield | vs Median | Score |",
            "|--------|-----|-----------|-----------|-----------|-----------|-----------|-------|",
        ]
        for s, f_s in sorted(members, key=lambda x: x[0].final_score, reverse=True):
            pe_str  = f"{f_s.pe_ratio:.0f}x"   if f_s and f_s.pe_ratio   is not None else "N/A"
            ev_str  = f"{f_s.ev_ebitda:.0f}x"  if f_s and f_s.ev_ebitda  is not None else "N/A"
            fy_str  = f"{f_s.fcf_yield:.1f}%"  if f_s and f_s.fcf_yield  is not None else "N/A"

            # Premium/discount vs sector median (percentage points for multiples)
            def _vs(val, med, fmt_pct=False):
                if val is None or med is None:
                    return "—"
                delta = val - med
                sign  = "+" if delta >= 0 else ""
                return f"{sign}{delta:.1f}%" if fmt_pct else f"{sign}{delta:.1f}x"

            pe_vs  = _vs(f_s.pe_ratio   if f_s else None, med_pe)
            ev_vs  = _vs(f_s.ev_ebitda  if f_s else None, med_ev)
            fy_vs  = _vs(f_s.fcf_yield  if f_s else None, med_fcfy, fmt_pct=True)

            lines.append(
                f"| {s.ticker} | {pe_str} | {pe_vs} | {ev_str} | {ev_vs} | {fy_str} | {fy_vs} | {s.final_score:.1f} |"
            )
        lines.append("")

    if not peer_section_written:
        lines.append("*No sectors with multiple companies in this analysis — peer comparison not available.*")
        lines.append("")

    lines += ["---", ""]

    # ── 7. Red Flags ──────────────────────────────────────────────────────────
    lines += ["## 7. Red Flags", ""]
    has_flags = False
    for s in ranked_scores:
        if s.red_flags:
            has_flags = True
            lines.append(f"**{s.ticker} — {s.company_name}:**")
            for f_item in s.red_flags:
                lines.append(f"- 🚩 {f_item}")
            lines.append("")
    if not has_flags:
        lines.append("No major red flags identified in this batch.")
    lines += ["", "---", ""]

    # ── 8. Companies to Research Further ─────────────────────────────────────
    lines += [
        "## 8. Companies to Research Further",
        "",
    ]
    research = [s for s in ranked_scores if s.verdict in [
        Verdict.STRONG_CANDIDATE, Verdict.RESEARCH_FURTHER,
        Verdict.POTENTIALLY_ATTRACTIVE, Verdict.HIGH_QUALITY_BUT_EXPENSIVE
    ]]
    if research:
        for s in research:
            emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
            lines += [
                f"### {emoji} {s.ticker}",
                f"**Score:** {s.final_score:.1f} | **Verdict:** {s.verdict.value} | **Sector:** {s.sector.value}",
                "",
                f"{s.why_it_matters}",
                "",
                "**Suggested research steps:**",
            ]
            for v in s.what_to_verify:
                lines.append(f"- {v}")
            lines.append("")
    else:
        lines.append("No companies met the threshold for priority research in this batch.")
    lines += ["---", ""]

    # ── 9. Private Companies / No Ticker Found ────────────────────────────────
    lines += [
        "## 9. Private Companies / No Ticker Found",
        "",
        f"> **{len(private_contracts)} contracts totaling {unmatched_str} could not be matched to a public ticker.**",
        "> This is your coverage gap. Review the table below — some may be resolvable via `ticker_map.yaml`.",
        "",
        "These awardees received contracts but no public ticker was identified.",
        "They may represent competitive intelligence about industry trends or future IPO candidates.",
        "",
        "| Awardee | Parent (if known) | Contract Value | Sector | Notes |",
        "|---------|-------------------|----------------|--------|-------|",
    ]
    for c in private_contracts:
        parent = c.parent_company or "Unknown"
        lines.append(
            f"| {c.awardee_name[:40]} | {parent[:30]} | {_fmt_millions(c.contract_value)} "
            f"| {c.sector.value} | {c.investment_relevance_notes[:50] or '-'} |"
        )
    lines += ["", "---", ""]

    # ── 10. Data Quality Caveats ──────────────────────────────────────────────
    if live:
        fundamentals_caveat = [
            "**Fundamentals data:**",
            "- Market prices, margins, ratios, and balance sheet figures are fetched live from **yfinance**.",
            "- Government revenue %, DoD revenue %, backlog/revenue, and moat rating come from a curated",
            "  overlay (`fundamentals_overlay.json`) and should be verified against SEC filings.",
            "- P/E, EV/EBITDA, FCF yield may differ slightly from Bloomberg/FactSet due to yfinance",
            "  calculation methodology — treat as directional indicators, not precision figures.",
        ]
    else:
        fundamentals_caveat = [
            "**Fundamentals data:**",
            "- All financial data in this report is **approximate** (offline mock mode).",
            "- Figures have not been verified against SEC filings or earnings releases.",
            "- P/E, EV/EBITDA, FCF yield, and other multiples are static estimates and may be stale.",
            "- Run with live mode (`python main.py`) to fetch real-time data from yfinance.",
        ]

    lines += [
        "## 10. Data Quality Caveats",
        "",
    ] + fundamentals_caveat + [
        "",
        "**Contract data:**",
        "- Contract values reflect the ceiling or announced value, not necessarily obligated/funded amounts.",
        "- IDIQ contract ceilings are not guaranteed revenue — actual task order funding may be a",
        "  small fraction of the ceiling. Always check USAspending.gov for actual obligations.",
        "- Modifications may include previously counted scope — not all modifications represent",
        "  new incremental revenue.",
        "",
        "**Ticker mapping:**",
        "- Resolution uses two layers: (1) `ticker_map.yaml` for subsidiaries/legacy names,",
        "  then (2) automatic fuzzy-match against the full SEC EDGAR company index (~10k+ tickers).",
        "- EDGAR index is cached locally and refreshed weekly.",
        "- Confidence scores below 0.70 should be verified before any research action.",
        "- Joint ventures and consortium awards may not map cleanly to a single public ticker.",
        "",
        "**Scoring:**",
        "- Scores are algorithmic estimates based on imperfect inputs and should not be",
        "  treated as precision instruments.",
        "- Sector classification is keyword-based and may misclassify ambiguous contracts.",
        "- The 'Contract Catalyst' score is intentionally conservative — large contracts",
        "  for large companies rarely move the fundamental investment thesis.",
        "",
        "**This report is NOT investment advice.**",
        "All figures require independent verification. Consult a licensed financial advisor.",
        "",
        "---",
        "",
        f"*Report generated by DoD Contract Intelligence Agent v0.1 (MVP) | {run_date}*",
    ]

    return "\n".join(lines)


def save_report(content: str, path: str) -> None:
    with open(path, "w") as f:
        f.write(content)
    print(f"Report saved → {path}")
