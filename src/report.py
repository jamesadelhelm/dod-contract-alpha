"""
Report generator: produces a detailed markdown analyst-style report.
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Dict
from src.models import CompanyScore, Contract, Verdict, Sector, SpecialistTierStatus


VERDICT_EMOJI = {
    Verdict.STRONG_CANDIDATE: "🟢",
    Verdict.RESEARCH_FURTHER: "🟡",
    Verdict.POTENTIALLY_ATTRACTIVE: "🟡",
    Verdict.WATCHLIST: "🔵",
    Verdict.HIGH_QUALITY_BUT_EXPENSIVE: "🟠",
    Verdict.LOW_CONVICTION: "⚪",
    Verdict.IGNORE: "🔴",
}

# ── Position sizing constants ─────────────────────────────────────────────────
_DOGE_CLUSTER     = {"BAH", "LDOS", "SAIC", "ACN", "CACI", "AMTM", "PLTR"}
_AEROSPACE_CLUSTER = {"LMT", "NOC", "GE", "TXT", "RTX", "BA", "HII", "AVAV"}
_DOGE_CAP_PCT     = 10.0
_AEROSPACE_CAP_PCT = 15.0
_BASE_POSITION_PCT = 6.0  # full-conviction PA+ max per name


def _compute_position_size(s: CompanyScore) -> tuple[float, str]:
    """Return (size_pct, sizing_rationale) for a single company.
    Base 6% per PA+ name, scaled by bear-case MoS severity."""
    is_pa_plus = s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
    )
    b_mos = s.dcf.bear_mos if s.dcf else None
    is_overvalued = any(
        "overvalued at" in f.lower() or "dcf:" in f.lower()
        for f in (s.red_flags or [])
    )
    if not is_pa_plus or is_overvalued:
        return 0.0, ""
    if b_mos is None:
        return 3.0, "Half-size (no bear MoS data)"
    elif b_mos > 0:
        return 6.0, "Full — bear case confirms MoS"
    elif b_mos >= -15:
        return 4.5, "75% — mild tail risk"
    elif b_mos >= -30:
        return 3.0, "50% — elevated tail risk"
    else:
        return 1.5, "25% — survive the bear scenario"

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

    strong   = [s for s in ranked_scores if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.RESEARCH_FURTHER)]
    attract  = [s for s in ranked_scores if s.verdict == Verdict.POTENTIALLY_ATTRACTIVE]
    expensive= [s for s in ranked_scores if s.verdict == Verdict.HIGH_QUALITY_BUT_EXPENSIVE]
    watchlist= [s for s in ranked_scores if s.verdict == Verdict.WATCHLIST]

    # Pre-compute tiers for executive summary
    _pa_plus = [s for s in ranked_scores if s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
    )]
    _tier1_ex = [s for s in _pa_plus
                 if s.dcf and s.dcf.bear_mos is not None and s.dcf.bear_mos > 0
                 and not any("overvalued at" in f.lower() or "dcf:" in f.lower() for f in (s.red_flags or []))]
    _tier2_ex = [s for s in _pa_plus if s not in _tier1_ex
                 and not any("overvalued at" in f.lower() or "dcf:" in f.lower() for f in (s.red_flags or []))]
    _deploy_rows_ex = [(s, *_compute_position_size(s)) for s in ranked_scores]
    _total_pct_ex   = sum(pct for _, pct, _ in _deploy_rows_ex if pct > 0)

    # Auto-generate executive summary
    exec_parts = []
    if _tier1_ex:
        top = _tier1_ex[0]
        b = top.dcf.bear_mos if top.dcf else None
        bstr = f"🛡️ +{b:.0f}% bear MoS" if b and b > 0 else ""
        if top.dcf:
            exec_parts.append(
                f"**{top.ticker}** is the highest-conviction name "
                f"(score {top.final_score:.0f}, +{top.dcf.margin_of_safety_base:.0f}% base MoS, "
                f"{bstr}): the market is pricing in only "
                f"{top.dcf.implied_growth_rate:.0f}%/yr growth "
                "despite a strong and growing defense franchise."
            )
        else:
            exec_parts.append(f"**{top.ticker}** is the highest-conviction name: score {top.final_score:.0f}.")
    if _tier2_ex:
        names = ", ".join(
            f"**{s.ticker}** (base +{s.dcf.margin_of_safety_base:.0f}%, bear {s.dcf.bear_mos:.0f}%)"
            if s.dcf else f"**{s.ticker}**"
            for s in _tier2_ex
        )
        exec_parts.append(
            f"Research Priority names with base-case undervaluation but meaningful tail risk: {names}."
        )
    total_pa = len(_pa_plus)
    if total_pa == 0:
        exec_parts.append("No Potentially Attractive names in this batch — hold cash and wait.")
    exec_parts.append(
        f"Actionable position weight: **{_total_pct_ex:.1f}% of portfolio** — "
        "high cash discipline is correct when opportunities are limited."
    )

    lines += [
        "# DoD Contract Intelligence Report",
        "",
        f"> **Generated:** {run_date}  ",
        f"> **Contracts analyzed:** {len(all_contracts)} &nbsp;|&nbsp; "
        f"**Public companies scored:** {len(ranked_scores)} &nbsp;|&nbsp; "
        f"**Private/unresolved:** {len(private_contracts)} ({unmatched_str}) — see Section 8",
        "",
        "> ⚠️ **DISCLAIMER:** Research tool only. Not investment advice. All scores require",
        "> independent verification. Consult a licensed financial advisor before any investment decision.",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
    ]
    for part in exec_parts:
        lines.append(part)
        lines.append("")
    lines += ["---", ""]

    # ── 1. Action Summary ─────────────────────────────────────────────────────
    lines += [
        "## 1. Action Summary",
        "",
        "Highest-priority companies from this contract batch, ranked by composite score.",
        "",
        "| # | Ticker | Price | Company | Sector | Score | MoS | Bear | Data | Verdict |",
        "|---|--------|------:|---------|--------|------:|----:|-----:|-----:|---------|",
    ]
    for i, s in enumerate(ranked_scores, 1):
        emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
        data_str = f"{s.data_completeness_pct:.0f}%"
        if s.data_completeness_pct < 50:
            data_str += "⚠"
        f_ctx = (fundamentals_map or {}).get(s.ticker)
        price_str = f"${f_ctx.current_price:.0f}" if f_ctx and f_ctx.current_price else "—"
        mos_str = "—"
        bear_str = "—"
        is_ignore = s.verdict == Verdict.IGNORE
        is_pa_plus = s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
        if s.dcf and s.dcf.margin_of_safety_base is not None:
            mos_val = s.dcf.margin_of_safety_base
            # Suppress positive MoS for Ignore-rated: high MoS on a low-quality name looks
            # like a buy signal but is usually a DCF artifact (e.g. CNC commercial FCF).
            if is_ignore and mos_val > 0:
                mos_str = "—†"
            else:
                mos_str = "+0%" if abs(mos_val) < 0.5 else f"{mos_val:+.0f}%"
        if s.dcf and s.dcf.bear_mos is not None:
            bm = s.dcf.bear_mos
            if is_ignore and bm > 0:
                bear_str = "—†"  # same logic: suppress commercial bear MoS artifact
            elif bm > 0 and is_pa_plus:
                bear_str = f"🛡️{bm:+.0f}%"
            else:
                bear_str = "+0%" if abs(bm) < 0.5 else f"{bm:+.0f}%"
        lines.append(
            f"| {i} | **{s.ticker}** | {price_str} | {s.company_name} | {s.sector.value} "
            f"| **{s.final_score:.1f}** | {mos_str} | {bear_str} | {data_str} | {emoji} {s.verdict.value} |"
        )

    lines += [
        "",
        "† MoS suppressed for Ignore-rated companies — high MoS on a low-quality name"
        " is usually a DCF artifact (e.g. high FCF yield from non-DoD business lines)."
        " Full DCF detail in Section 2b.",
        "**Bear MoS** = bear-case DCF margin of safety. 🛡️ = positive even in the downside scenario"
        " (downside protection confirmed). Negative = thesis must be right for capital to be safe.",
        "",
        "**Signal counts:**",
        f"- 🟢 Strong Candidate / Research Further: **{len(strong)}**",
        f"- 🟡 Potentially Attractive: **{len(attract)}**",
        f"- 🟠 High Quality But Expensive: **{len(expensive)}**",
        f"- 🔵 Watchlist: **{len(watchlist)}**",
        "",
    ]

    # ── Portfolio-level sector concentration warning ───────────────────────────
    # Group actionable names (Potentially Attractive + Watchlist) by sector risk bucket
    # to alert investors to hidden concentration risk.
    from collections import defaultdict
    actionable = [s for s in ranked_scores if s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE,
        Verdict.HIGH_QUALITY_BUT_EXPENSIVE, Verdict.WATCHLIST
    )]
    # Map sectors to risk buckets
    _DOGE_RISK_SECTORS = {
        "AI / Data / Software", "Cloud / IT Services", "Consulting / Services"
    }
    _AEROSPACE_SECTORS = {"Aerospace", "Traditional Defense Prime"}
    _HEALTHCARE_SECTORS = {"Military Healthcare"}
    bucket_names = defaultdict(list)
    for s in actionable:
        sec = s.sector.value
        if sec in _DOGE_RISK_SECTORS:
            bucket_names["Federal IT / Consulting (DOGE risk)"].append(s.ticker)
        elif sec in _AEROSPACE_SECTORS:
            bucket_names["Aerospace / Defense Prime"].append(s.ticker)
        elif sec in _HEALTHCARE_SECTORS:
            bucket_names["Military Healthcare"].append(s.ticker)
    concentration_notes = []
    for bucket, tickers in bucket_names.items():
        if len(tickers) >= 3:
            concentration_notes.append(
                f"⚠️ **{bucket}**: {', '.join(tickers)} — "
                f"{len(tickers)} companies share this risk factor. "
                "Investing across all represents concentrated exposure to a single macro theme."
            )
        elif len(tickers) == 2:
            concentration_notes.append(
                f"📌 **{bucket}**: {', '.join(tickers)} — correlated risk; "
                "consider sizing as a pair rather than independent positions."
            )
    if concentration_notes:
        lines.append("**Portfolio concentration notes:**")
        for note in concentration_notes:
            lines.append(f"- {note}")
        lines.append("")

    # ── Signal tiers for capital deployment ────────────────────────────────────
    # Organize actionable names by conviction level based on composite score,
    # base MoS, and bear-case MoS. Not investment advice — organizes signals.
    tier1 = []  # PA+ with positive bear MoS
    tier2 = []  # PA+ with negative bear MoS (moderate tail risk)
    tier3 = []  # Watchlist with positive base MoS
    tier4 = []  # Watchlist/PA with overvaluation flag (wait for entry)
    for s in ranked_scores:
        is_pa_plus = s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
        is_watchlist = s.verdict == Verdict.WATCHLIST
        b_mos = s.dcf.bear_mos if s.dcf else None
        base_mos = s.dcf.margin_of_safety_base if s.dcf else None
        is_overvalued = any("overvalued at" in f.lower() or "dcf:" in f.lower() for f in (s.red_flags or []))
        # Only flag as "Wait for Entry" if quality is Watchlist+; Low Conviction/Ignore
        # with high DCF multiples isn't a "wait for entry" — it's just a pass.
        is_quality_enough = s.verdict not in (Verdict.LOW_CONVICTION, Verdict.IGNORE)
        if is_overvalued and is_quality_enough:
            tier4.append(s.ticker)
        elif is_pa_plus and b_mos is not None and b_mos > 0:
            tier1.append(s.ticker)
        elif is_pa_plus:
            tier2.append(s.ticker)
        elif is_watchlist and base_mos is not None and base_mos > 5:
            tier3.append(s.ticker)

    if tier1 or tier2 or tier3 or tier4:
        lines.append("**Signal tiers (based on composite score + DCF MoS):**")
        if tier1:
            lines.append(f"- 🟢 **Highest Conviction** — positive bear-case MoS: {', '.join(tier1)}")
        if tier2:
            lines.append(f"- 🟡 **Research Priority** — positive base MoS, tail risk in bear case: {', '.join(tier2)}")
        if tier3:
            lines.append(f"- 🔵 **Monitor** — Watchlist quality with positive base MoS: {', '.join(tier3)}")
        if tier4:
            lines.append(f"- ⏳ **Wait for Entry** — overvalued at current price: {', '.join(tier4)}")
        lines += [
            "",
            "> Signal tiers summarize the above signals. Not investment advice.",
            "> Verify all DCF assumptions and 10-K before any capital deployment.",
            "",
        ]

    # ── Capital Deployment Guidance ───────────────────────────────────────────
    # Convert signal tiers into a concrete position sizing table.
    # Base: 6% per PA+ name, scaled by bear-MoS severity.
    # Cluster caps prevent correlated exposure from exceeding concentration limits.
    deployable_rows = []
    for s in ranked_scores:
        size_pct, rationale = _compute_position_size(s)
        if size_pct > 0:
            b_mos = s.dcf.bear_mos if s.dcf else None
            bear_iv = s.dcf.bear_iv if s.dcf else None
            f_ds = (fundamentals_map or {}).get(s.ticker)
            cur_price = f_ds.current_price if f_ds and f_ds.current_price else None
            tier_label = (
                "🟢 Highest Conviction" if (b_mos is not None and b_mos > 0)
                else "🟡 Research Priority"
            )
            bmos_str = (f"🛡️ {b_mos:+.0f}%" if b_mos > 0 else f"{b_mos:+.0f}%") if b_mos is not None else "—"
            # Entry target: bear IV is the price at which downside scenario breaks even.
            # For Highest Conviction (positive bear MoS), bear IV > current price — already safe.
            # For Research Priority, bear IV < current price — shows where bear case breaks even.
            if bear_iv is not None and bear_iv > 0:
                entry_str = f"${bear_iv:.0f} (bear IV)"
            else:
                entry_str = "—"
            deployable_rows.append((s.ticker, tier_label, bmos_str, entry_str, rationale, size_pct, cur_price))

    if deployable_rows:
        total_pct = sum(r[5] for r in deployable_rows)
        cash_pct = 100.0 - total_pct

        lines += [
            "**Position sizing guidance (% of equity portfolio):**",
            "",
            "| Ticker | Now | Entry Target | Tier | Bear MoS | Sizing Logic | Weight |",
            "|--------|----:|-------------:|------|:--------:|:-------------|-------:|",
        ]
        for ticker, tier_label, bmos_str, entry_str, rationale, size_pct, cur_price in deployable_rows:
            price_str = f"${cur_price:.0f}" if cur_price else "—"
            lines.append(
                f"| {ticker} | {price_str} | {entry_str} | {tier_label} | {bmos_str} | {rationale} | {size_pct:.1f}% |"
            )
        lines += [
            "",
            f"**Actionable weight: {total_pct:.1f}% &nbsp;|&nbsp; "
            f"Hold cash / await better entries: {cash_pct:.1f}%**",
            "",
        ]

        # Cluster cap reporting (size_pct is index 5 now)
        doge_pct  = sum(r[5] for r in deployable_rows if r[0] in _DOGE_CLUSTER)
        aero_pct  = sum(r[5] for r in deployable_rows if r[0] in _AEROSPACE_CLUSTER)
        doge_tickers = [r[0] for r in deployable_rows if r[0] in _DOGE_CLUSTER]
        aero_tickers = [r[0] for r in deployable_rows if r[0] in _AEROSPACE_CLUSTER]
        cluster_lines = []
        if doge_tickers:
            flag = "⚠️ EXCEEDS CAP — scale back proportionally" if doge_pct > _DOGE_CAP_PCT else "✅"
            comp_str = " + ".join(f"{t} {next(r[5] for r in deployable_rows if r[0]==t):.1f}%" for t in doge_tickers)
            cluster_lines.append(
                f"- Federal IT / DOGE risk (cap {_DOGE_CAP_PCT:.0f}%): "
                f"{comp_str} = {doge_pct:.1f}% {flag}"
            )
        if aero_tickers:
            flag = "⚠️ EXCEEDS CAP — scale back proportionally" if aero_pct > _AEROSPACE_CAP_PCT else "✅"
            comp_str = " + ".join(f"{t} {next(r[5] for r in deployable_rows if r[0]==t):.1f}%" for t in aero_tickers)
            cluster_lines.append(
                f"- Aerospace / Defense Prime (cap {_AEROSPACE_CAP_PCT:.0f}%): "
                f"{comp_str} = {aero_pct:.1f}% {flag}"
            )
        if cluster_lines:
            lines.append("Cluster concentration caps:")
            lines += cluster_lines
            lines.append("")

        lines += [
            "> Sizing: 6% max per PA+ name. Scaled by bear-MoS: >0% → full, ≥−15% → 75%, ≥−30% → 50%, <−30% → 25%.",
            "> High cash weight is correct discipline when conviction names are scarce — avoid forcing capital into inferior positions.",
            "> This is a sizing framework, not investment advice. Verify 10-K before any capital deployment.",
            "",
        ]

    lines += ["---", "", ]

    # ── 2. Valuation Snapshot ─────────────────────────────────────────────────
    lines += [
        "## 2. Valuation Snapshot",
        "",
        "### 2a. Market Multiples",
        "",
        "| Ticker | Price | P/E | Fwd P/E | EV/EBITDA | FCF Yield | Div Yld | Share Δ YoY | D/E | Graham |",
        "|--------|------:|----:|--------:|----------:|----------:|--------:|------------:|----:|-------:|",
    ]
    for s in ranked_scores:
        f = (fundamentals_map or {}).get(s.ticker)
        if f is None:
            from src.fundamentals import get_fundamentals_or_stub
            f = get_fundamentals_or_stub(s.ticker, live=live)
        price = f"${f.current_price:.2f}" if f.current_price else "—"
        pe    = f"{f.pe_ratio:.0f}x"    if f.pe_ratio             else "—"
        fpe   = f"{f.forward_pe:.0f}x"  if f.forward_pe           else "—"
        ev    = f"{f.ev_ebitda:.0f}x"   if f.ev_ebitda            else "—"
        fcfy  = f"{f.fcf_yield:.1f}%"   if f.fcf_yield            else "—"
        divy  = f"{f.dividend_yield:.1f}%" if f.dividend_yield is not None else "—"
        sc    = f"{f.shares_chg_1yr_pct:+.1f}%" if f.shares_chg_1yr_pct is not None else "—"
        if f.shares_chg_1yr_pct is not None:
            sc += " 🔺" if f.shares_chg_1yr_pct > 3 else (" ✅" if f.shares_chg_1yr_pct < -2 else "")
        de    = f"{f.debt_equity:.1f}x" if f.debt_equity is not None else "—"
        lines.append(
            f"| {s.ticker} | {price} | {pe} | {fpe} | {ev} | {fcfy} | {divy} | {sc} | {de} | {s.graham_value.raw:.0f} |"
        )

    lines += [
        "",
        "### 2b. DCF Intrinsic Value Estimates",
        "",
        "> 3-scenario owner-earnings DCF. **MoS** = (IV − Price) / Price. Positive = stock trades below intrinsic value.",
        "> **Bear MoS** = downside scenario margin of safety. 🛡️ = still positive in the bear case — highest conviction entry.",
        "> Reverse DCF shows what growth rate the current price is already pricing in.",
        "> **These are estimates, not predictions. Bear/Base/Bull range is the thinking framework; use Reverse DCF as the sanity check.**",
        "",
        "| Ticker | Price | Bear IV | Base IV | Bull IV | Bear MoS | MoS (Base) | Reverse DCF | Discount Rate | DCF Verdict |",
        "|--------|------:|--------:|--------:|--------:|---------:|-----------:|------------:|--------------:|-------------|",
    ]
    for s in ranked_scores:
        f_ctx = (fundamentals_map or {}).get(s.ticker)
        price_str = f"${f_ctx.current_price:.0f}" if f_ctx and f_ctx.current_price else "—"
        if s.dcf:
            d = s.dcf
            bear  = f"${d.bear_iv:.0f}"  if d.bear_iv  is not None else "—"
            base  = f"${d.base_iv:.0f}"  if d.base_iv  is not None else "—"
            bull  = f"${d.bull_iv:.0f}"  if d.bull_iv  is not None else "—"
            mos   = f"{d.margin_of_safety_base:+.0f}%" if d.margin_of_safety_base is not None else "—"
            bmos  = (f"🛡️ {d.bear_mos:+.0f}%" if d.bear_mos > 0 else f"{d.bear_mos:+.0f}%") if d.bear_mos is not None else "—"
            impl  = f"{d.implied_growth_rate:.0f}%/yr" if d.implied_growth_rate is not None else "—"
            rate  = f"{d.discount_rate_base:.1f}%"
            verd  = d.verdict[:30] if d.verdict else "—"
        else:
            bear = base = bull = mos = bmos = impl = rate = verd = "—"
        lines.append(
            f"| {s.ticker} | {price_str} | {bear} | {base} | {bull} | {bmos} | {mos} | {impl} | {rate} | {verd} |"
        )

    # ── WACC sensitivity note for PA+ names ─────────────────────────────────
    # Approximation: dIV/IV ≈ -TV_pct / (WACC - terminal_g) per +1% WACC
    # Terminal value = ~70% of total IV for a 10-yr DCF with 3% terminal growth.
    pa_plus_dcf = [
        s for s in ranked_scores
        if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
        and s.dcf and s.dcf.base_iv is not None and s.dcf.base_iv > 0
    ]
    if pa_plus_dcf:
        sensitivity_parts = []
        for s in pa_plus_dcf:
            d = s.dcf
            f_c = (fundamentals_map or {}).get(s.ticker)
            if d.discount_rate_base and d.bear_growth is not None:
                # Terminal growth is stored per-scenario in bear; use base scenario's terminal growth
                # Estimate: terminal_g ≈ 2.5–3.5% depending on sector, use 3.0 as midpoint
                tg_est = 0.03
                wacc = d.discount_rate_base / 100.0
                if wacc > tg_est:
                    tv_pct = 0.70  # TV as fraction of total IV
                    # dIV/IV = -tv_pct * dr/(r-g); for dr=1pp: drop% = tv_pct/(r-g)*100
                    iv_drop_pct = tv_pct / (wacc - tg_est) * 0.01  # fractional drop for +1pp WACC
                    new_iv = d.base_iv * (1 - iv_drop_pct)
                    sensitivity_parts.append(
                        f"**{s.ticker}**: base IV ${d.base_iv:.0f} → ~${new_iv:.0f} at +1% WACC "
                        f"({iv_drop_pct*100:.0f}% drop per +1pp)"
                    )
        if sensitivity_parts:
            lines += [
                "",
                "**WACC sensitivity (PA+ names):** A +1% increase in the discount rate reduces "
                "intrinsic value by the amounts below — driven by terminal value sensitivity. "
                "Use these as a stress test: does the thesis survive a higher-rate environment?",
                "",
            ]
            for sp in sensitivity_parts:
                lines.append(f"> {sp}")
            lines.append("")

    lines += ["", "---", ""]

    # ── 3. Red Flags ──────────────────────────────────────────────────────────
    lines += ["## 3. Red Flags", ""]
    has_flags = False
    for s in ranked_scores:
        if s.red_flags:
            has_flags = True
            emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
            lines.append(f"**{emoji} {s.ticker} — {s.company_name} ({s.final_score:.1f})**")
            for flag in s.red_flags:
                lines.append(f"- 🚩 {flag}")
            lines.append("")
    if not has_flags:
        lines.append("No major red flags identified in this batch.")
    lines += ["", "---", ""]

    # ── 4. Analyst Consensus & Price Momentum ─────────────────────────────────
    lines += [
        "## 4. Market Context",
        "",
        "> **Short interest** > 15% flags informed bearish positioning against our thesis.",
        "> **Off-high** and **1yr return** distinguish temporary dislocation from structural decline.",
        "> ⚠️ symbols mark divergence between our score and the Street consensus.",
        "",
        "| Ticker | Price | 52W Low–High | Off High | 1Yr Rtn | Short % | Target | Upside | Analysts | Consensus | Next Earnings |",
        "|--------|------:|--------------|:--------:|:-------:|:-------:|-------:|-------:|:--------:|:---------:|:-------------:|",
    ]
    for s in ranked_scores:
        f_a = (fundamentals_map or {}).get(s.ticker)
        if not f_a:
            lines.append(f"| {s.ticker} | — | — | — | — | — | — | — | — | — | — |")
            continue
        price_str = f"${f_a.current_price:.2f}" if f_a.current_price else "—"
        range_str = (
            f"${f_a.price_52w_low:.0f}–${f_a.price_52w_high:.0f}"
            if f_a.price_52w_low and f_a.price_52w_high else "—"
        )
        off_str  = f"{f_a.pct_off_52w_high:+.0f}%" if f_a.pct_off_52w_high is not None else "—"
        ret_str  = f"{f_a.return_1yr:+.1f}%" if f_a.return_1yr is not None else "—"
        short_str= f"{f_a.short_pct_of_float:.1f}%" if f_a.short_pct_of_float is not None else "—"
        if f_a.short_pct_of_float is not None and f_a.short_pct_of_float > 15 and s.final_score >= 58:
            short_str = f"⚠️{short_str}"
        tgt_str  = f"${f_a.analyst_target_price:.0f}" if f_a.analyst_target_price else "—"
        up_str   = f"{f_a.upside_to_target:+.0f}%" if f_a.upside_to_target is not None else "—"
        n_str    = str(f_a.analyst_count) if f_a.analyst_count else "—"
        rec_str  = f_a.analyst_recommendation or "—"
        if f_a.analyst_recommendation in ("sell", "underperform") and s.final_score >= 58:
            rec_str = f"⚠️{rec_str}"
        earn_str = "—"
        if f_a.next_earnings_date:
            try:
                from datetime import datetime as _dt3
                days_out = (_dt3.strptime(f_a.next_earnings_date, "%Y-%m-%d") - _dt3.now()).days
                earn_str = f"{f_a.next_earnings_date} ({days_out}d)"
                if days_out <= 14:
                    earn_str = f"⚠️{earn_str}"
            except Exception:
                earn_str = f_a.next_earnings_date
        lines.append(
            f"| {s.ticker} | {price_str} | {range_str} | {off_str} | {ret_str} "
            f"| {short_str} | {tgt_str} | {up_str} | {n_str} | {rec_str} | {earn_str} |"
        )
    lines += ["", "---", ""]

    # ── 5. Specialist Tier ────────────────────────────────────────────────────
    lines += [
        "## 5. Specialist Tier",
        "",
        "Mid-cap ($400M–$15B), high-DoD-concentration (≥35%) companies where contract",
        "signals are most actionable — sell-side coverage is thin (3–8 analysts vs. 25+",
        "for large primes) so material contracts may not yet be in consensus models.",
        "",
    ]
    in_tier    = [s for s in ranked_scores if s.specialist and s.specialist.status.value == "In Tier"]
    near_tier  = [s for s in ranked_scores if s.specialist and s.specialist.status.value == "Near Tier"]
    large_prime= [s for s in ranked_scores if s.specialist and s.specialist.status.value == "Large Prime"]

    if in_tier:
        lines += [
            "### 🎯 In-Tier (Sweet Spot)",
            "",
            "| Ticker | Score | Mkt Cap | DoD Rev% | Contract/Rev% | Sole Source | Bonus |",
            "|--------|------:|--------:|:--------:|:-------------:|:-----------:|------:|",
        ]
        for s in in_tier:
            sp = s.specialist
            mc_str  = f"${sp.market_cap_millions:.0f}M" if sp.market_cap_millions else "—"
            dod_str = f"{sp.dod_revenue_pct:.0f}%"      if sp.dod_revenue_pct is not None else "—"
            cvr_str = f"{sp.contract_to_revenue_pct:.1f}%" if sp.contract_to_revenue_pct is not None else "—"
            ss_str  = "✅" if sp.is_sole_source else "—"
            bonus_str = f"+{sp.score_adjustment:.1f}"   if sp.score_adjustment > 0 else "0"
            lines.append(
                f"| **{s.ticker}** | {s.final_score:.1f} | {mc_str} | {dod_str} | {cvr_str} | {ss_str} | {bonus_str} |"
            )
        lines.append("")
        for s in in_tier:
            sp = s.specialist
            lines += [
                f"**{s.ticker}** — {s.company_name}",
                f"*{sp.rationale}*",
                f"*Coverage: {sp.analyst_coverage_note}*",
                "",
            ]

    if near_tier:
        lines += [
            "### 🔍 Near-Tier",
            "",
            "| Ticker | Score | Mkt Cap | DoD Rev% | Contract/Rev% | Bonus |",
            "|--------|------:|--------:|:--------:|:-------------:|------:|",
        ]
        for s in near_tier:
            sp = s.specialist
            mc_str  = f"${sp.market_cap_millions:.0f}M" if sp.market_cap_millions else "—"
            dod_str = f"{sp.dod_revenue_pct:.0f}%"      if sp.dod_revenue_pct is not None else "—"
            cvr_str = f"{sp.contract_to_revenue_pct:.1f}%" if sp.contract_to_revenue_pct is not None else "—"
            bonus_str = f"+{sp.score_adjustment:.1f}"   if sp.score_adjustment > 0 else "0"
            lines.append(f"| {s.ticker} | {s.final_score:.1f} | {mc_str} | {dod_str} | {cvr_str} | {bonus_str} |")
        lines.append("")

    if large_prime:
        primes_str = ", ".join(s.ticker for s in large_prime)
        lines += [
            "### 🏭 Large-Cap Primes (No Specialist Bonus)",
            "",
            f"Contract news for **{primes_str}** is typically priced in by institutional desks",
            "within hours. They appear in the main rankings but receive no specialist bonus.",
            "",
        ]

    lines += ["---", ""]

    # ── 6. Government Funding Durability ──────────────────────────────────────
    lines += [
        "## 6. Government Funding Durability",
        "",
        "| Ticker | DoD Rev% | Gov Rev% | Backlog/Rev | Moat | Sole Source | DoD Stability Score |",
        "|--------|:--------:|:--------:|:-----------:|:----:|:-----------:|:-------------------:|",
    ]
    for s in ranked_scores:
        f = (fundamentals_map or {}).get(s.ticker)
        if f is None:
            from src.fundamentals import get_fundamentals_or_stub
            f = get_fundamentals_or_stub(s.ticker, live=live)
        ss       = "Yes" if any(c.is_sole_source for c in s.recent_contracts) else "No"
        dod_pct  = f"{f.dod_revenue_pct:.0f}%"       if f.dod_revenue_pct is not None else "—"
        gov_pct  = f"{f.government_revenue_pct:.0f}%" if f.government_revenue_pct is not None else "—"
        bl       = f"{f.backlog_to_revenue:.1f}x"     if f.backlog_to_revenue else "—"
        lines.append(
            f"| {s.ticker} | {dod_pct} | {gov_pct} | {bl} | {f.moat_rating or '—'} | {ss} | {s.dod_stability.raw:.0f} |"
        )
    lines += ["", "---", ""]

    # ── 7. Company Deep Dives ─────────────────────────────────────────────────
    lines += [
        "## 7. Company Deep Dives",
        "",
        "Detailed score breakdown, recent contracts, and investment analysis for each company.",
        "Companies listed highest-to-lowest score.",
        "",
    ]

    for s in ranked_scores:
        emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
        f_ctx  = (fundamentals_map or {}).get(s.ticker)

        # Market context line
        ctx_parts = []
        if f_ctx:
            if f_ctx.current_price:
                ctx_parts.append(f"**${f_ctx.current_price:.2f}**")
            if f_ctx.price_52w_high and f_ctx.price_52w_low:
                ctx_parts.append(f"52W ${f_ctx.price_52w_low:.0f}–${f_ctx.price_52w_high:.0f}")
                if f_ctx.pct_off_52w_high is not None:
                    ctx_parts.append(f"({f_ctx.pct_off_52w_high:+.0f}% off high)")
            if f_ctx.return_1yr is not None:
                ctx_parts.append(f"1yr {f_ctx.return_1yr:+.1f}%")
            if f_ctx.analyst_target_price and f_ctx.upside_to_target is not None:
                ctx_parts.append(
                    f"Street target ${f_ctx.analyst_target_price:.0f} ({f_ctx.upside_to_target:+.0f}% upside)"
                )
            if f_ctx.analyst_recommendation and f_ctx.analyst_count:
                ctx_parts.append(
                    f"Consensus **{f_ctx.analyst_recommendation}** ({f_ctx.analyst_count} analysts)"
                )
            if f_ctx.short_pct_of_float is not None and f_ctx.short_pct_of_float > 10:
                ctx_parts.append(f"Short {f_ctx.short_pct_of_float:.1f}% of float")
            if f_ctx.next_earnings_date:
                try:
                    from datetime import datetime as _dt2
                    days_out = (_dt2.strptime(f_ctx.next_earnings_date, "%Y-%m-%d") - _dt2.now()).days
                    earn_tag = " ⚠️" if days_out <= 14 else ""
                    ctx_parts.append(f"Earnings {f_ctx.next_earnings_date} ({days_out}d){earn_tag}")
                except Exception:
                    ctx_parts.append(f"Earnings {f_ctx.next_earnings_date}")
        mkt_line = " | ".join(ctx_parts) if ctx_parts else "*Market data unavailable*"

        lines += [
            f"### {emoji} {s.ticker} — {s.company_name}",
            f"**Sector:** {s.sector.value} &nbsp;|&nbsp; **Score: {s.final_score:.1f}/100** &nbsp;|&nbsp; **Verdict: {s.verdict.value}**",
            "",
            f"> {mkt_line}",
            "",
            "#### Score Breakdown",
            "",
            "| Component | Score | Weight | Visual |",
            "|-----------|------:|-------:|--------|",
            _score_table_row("Buffett Quality", s.buffett_quality.raw, 25),
            _score_table_row("Graham Value",    s.graham_value.raw,    20),
            _score_table_row("DoD Stability",   s.dod_stability.raw,   20),
            _score_table_row("Management",      s.management.raw,      15),
            _score_table_row("Contract Catalyst", s.contract_catalyst.raw, 10),
            _score_table_row("Balance Sheet",   s.balance_sheet.raw,   10),
            f"| **FINAL (weighted)** | **{s.final_score:.1f}** | 100% | {_bar(s.final_score)} |",
            "",
            f"*{s.overall_explanation}*",
            "",
            "#### Component Details",
            "",
            f"- **Buffett ({s.buffett_quality.raw:.0f}):** {s.buffett_quality.explanation}",
            f"- **Graham ({s.graham_value.raw:.0f}):** {s.graham_value.explanation}",
            f"- **DoD ({s.dod_stability.raw:.0f}):** {s.dod_stability.explanation}",
            f"- **Management ({s.management.raw:.0f}):** {s.management.explanation}",
            f"- **Catalyst ({s.contract_catalyst.raw:.0f}):** {s.contract_catalyst.explanation}",
            f"- **Balance Sheet ({s.balance_sheet.raw:.0f}):** {s.balance_sheet.explanation}",
            "",
        ]

        # DCF deep dive
        if s.dcf:
            d = s.dcf
            lines += [
                "#### DCF Detail",
                "",
                f"*Discount rate: **{d.discount_rate_base:.1f}%***",
            ]
            for adj in d.discount_rate_adjustments:
                lines.append(f"- {adj}")
            lines += [
                "",
                "| Scenario | Rev Growth Yr1–5 | IV/Share | MoS vs Price |",
                "|----------|:----------------:|:--------:|:------------:|",
            ]
            for label, g, iv, mos in [
                ("🐻 Bear", d.bear_growth, d.bear_iv, d.bear_mos),
                ("📊 Base", d.base_growth, d.base_iv, d.margin_of_safety_base),
                ("🐂 Bull", d.bull_growth, d.bull_iv, d.bull_mos),
            ]:
                g_str  = f"{g:.0f}%/yr" if g  is not None else "—"
                iv_str = f"${iv:.2f}"   if iv is not None else "—"
                mos_str= f"{mos:+.1f}%" if mos is not None else "—"
                lines.append(f"| {label} | {g_str} | {iv_str} | {mos_str} |")
            lines.append("")
            if d.implied_growth_rate is not None:
                lines.append(
                    f"*Reverse DCF: current price implies **{d.implied_growth_rate:.1f}%/yr** growth "
                    f"for 10 years at {d.discount_rate_base:.1f}% discount rate.*"
                )
                lines.append("")
            if d.valuation_note:
                lines.append(f"*{d.valuation_note}*")
                lines.append("")
            if d.caveats:
                lines.append("**DCF caveats:**")
                for c in d.caveats:
                    lines.append(f"- {c}")
                lines.append("")

        # Key signals callout for PA+ companies
        is_pa_plus = s.verdict in (
            Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
        )
        if is_pa_plus and s.dcf:
            d = s.dcf
            key_sigs = []
            if d.implied_growth_rate is not None and d.base_growth is not None:
                actual_growth = None
                f_ksc = (fundamentals_map or {}).get(s.ticker)
                if f_ksc and hasattr(f_ksc, "revenue_growth_1yr") and f_ksc.revenue_growth_1yr is not None:
                    actual_growth = f_ksc.revenue_growth_1yr  # already in % form (e.g. 3.7 = 3.7%)
                implied = d.implied_growth_rate
                base_g  = d.base_growth
                if actual_growth is not None and implied < actual_growth - 3:
                    key_sigs.append(
                        f"Market prices in only **{implied:.0f}%/yr growth**; "
                        f"actual YoY revenue growth is {actual_growth:+.1f}%. "
                        f"The stock prices in near-stagnation — if the business merely sustains "
                        f"at {base_g:.0f}%/yr (base case), intrinsic value is "
                        f"${d.base_iv:.0f}/share (+{d.margin_of_safety_base:.0f}% MoS)."
                    )
                elif implied < base_g - 2:
                    key_sigs.append(
                        f"Market prices in {implied:.0f}%/yr growth vs. base-case assumption of "
                        f"{base_g:.0f}%/yr — the current price embeds a meaningful pessimism premium."
                    )
            if d.bear_mos is not None:
                if d.bear_mos > 0:
                    key_sigs.append(
                        f"🛡️ **Bear-case downside protection confirmed**: even in the downside scenario "
                        f"({d.bear_growth:.0f}%/yr growth), intrinsic value exceeds the current price "
                        f"by {d.bear_mos:.0f}%. This is the strongest class of entry signal."
                    )
                elif d.bear_mos > -20:
                    key_sigs.append(
                        f"Bear-case MoS {d.bear_mos:.0f}% — modest tail risk. Base case remains "
                        f"attractive; size position to absorb a {abs(d.bear_mos):.0f}% drawdown."
                    )
                else:
                    key_sigs.append(
                        f"Bear-case MoS {d.bear_mos:.0f}% — significant tail risk if thesis disappoints. "
                        "The scenario where the thesis is wrong loses more than one-third of capital; "
                        "keep position small enough to survive that outcome (see Capital Deployment)."
                    )
            # Insider buying as a confirming or contradicting signal
            f_ins = (fundamentals_map or {}).get(s.ticker)
            if f_ins and hasattr(f_ins, "insider_net_pct_6m") and f_ins.insider_net_pct_6m is not None:
                ins_pct = f_ins.insider_net_pct_6m * 100
                if ins_pct >= 15:
                    key_sigs.append(
                        f"Insider signal: net buying of **+{ins_pct:.0f}%** of held shares over the "
                        "past 6 months — management putting capital behind the thesis."
                    )
                elif ins_pct <= -15:
                    key_sigs.append(
                        f"Insider signal: net selling of **{ins_pct:.0f}%** of held shares over the "
                        "past 6 months — management reducing exposure; warrants scrutiny."
                    )

            sz, sz_logic = _compute_position_size(s)
            if sz > 0:
                key_sigs.append(
                    f"Recommended weight: **{sz:.1f}%** of portfolio — {sz_logic}."
                )
            if key_sigs:
                lines += ["#### Key Signals", ""]
                for sig in key_sigs:
                    lines.append(f"> {sig}")
                lines.append("")

        # Recent contracts
        if s.recent_contracts:
            lines += ["#### Recent DoD Contracts", ""]
            for c in s.recent_contracts:
                funded_note = (
                    f" (${c.funded_amount:.0f}M funded)"
                    if c.funded_amount and c.funded_amount != c.contract_value else ""
                )
                ss_note   = " — **SOLE SOURCE**" if c.is_sole_source else ""
                idiq_note = " — *IDIQ ceiling*"  if c.is_idiq else ""
                lines += [
                    f"- **{_fmt_millions(c.contract_value)}{funded_note}** — {c.contract_type.value}{ss_note}{idiq_note}",
                    f"  *{(c.agency or 'Unknown agency')}*",
                    f"  {c.description[:200]}{'...' if len(c.description) > 200 else ''}",
                    f"  Completion: {c.completion_date or 'N/A'}",
                    "",
                ]

        # Investment analysis
        lines += [
            "#### Investment Analysis",
            "",
            f"**Why it matters:** {s.why_it_matters}",
            "",
            f"**Why it might not matter:** {s.why_it_might_not_matter}",
            "",
            "**Key risks:**",
        ]
        for r in s.key_risks:
            lines.append(f"- {r}")
        lines += ["", "**Verify next:**"]
        for v in s.what_to_verify:
            lines.append(f"- {v}")

        if s.red_flags:
            lines += ["", "**⚠️ Red flags:**"]
            for f_item in s.red_flags:
                lines.append(f"- 🚩 {f_item}")

        if s.low_ticker_confidence:
            lines += ["", "**⚠️ LOW TICKER CONFIDENCE** — verify parent company mapping manually."]

        lines += ["", "---", ""]

    # ── 8. Private Companies / Coverage Gap ───────────────────────────────────
    lines += [
        "## 8. Private Companies / Coverage Gap",
        "",
        f"> **{len(private_contracts)} contracts totaling {unmatched_str} could not be matched to a public ticker.**",
        "> Review below — some may be resolvable by adding entries to `data/ticker_map.yaml`.",
        "",
        "| Awardee | Parent (if known) | Value | Sector |",
        "|---------|-------------------|------:|--------|",
    ]
    for c in private_contracts:
        parent = c.parent_company or "—"
        lines.append(
            f"| {c.awardee_name[:40]} | {parent[:30]} | {_fmt_millions(c.contract_value)} | {c.sector.value} |"
        )
    lines += ["", "---", ""]

    # ── 9. Contract Awards ────────────────────────────────────────────────────
    lines += [
        "## 9. Contract Awards",
        "",
        "All contracts analyzed, sorted by value.",
        "",
        "| Awardee | Ticker | Value | Type | Branch | Sector |",
        "|---------|--------|------:|------|--------|--------|",
    ]
    for c in sorted(all_contracts, key=lambda x: x.contract_value or 0, reverse=True):
        ticker_str = c.ticker or ("*private*" if c.parent_company else "*unknown*")
        lines.append(
            f"| {c.awardee_name[:35]} | {ticker_str} | {_fmt_millions(c.contract_value)} "
            f"| {c.contract_type.value} | {(c.branch or c.agency or '')[:25]} | {c.sector.value} |"
        )
    lines += ["", "---", ""]

    # ── 10. Sector Peer Comparison ────────────────────────────────────────────
    lines += [
        "## 10. Sector Peer Comparison",
        "",
        "> Premium/discount vs. the median of peers in the same sector appearing in this analysis.",
        "> Only sectors with ≥2 companies are shown.",
        "",
    ]
    from collections import defaultdict as _dd
    import statistics as _stats

    sector_buckets: dict = _dd(list)
    for s in ranked_scores:
        f_s = (fundamentals_map or {}).get(s.ticker)
        sector_buckets[s.sector.value].append((s, f_s))

    peer_written = False
    for sector_name, members in sorted(sector_buckets.items()):
        if len(members) < 2:
            continue
        peer_written = True
        pes   = [f_s.pe_ratio  for _, f_s in members if f_s and f_s.pe_ratio  is not None]
        evs   = [f_s.ev_ebitda for _, f_s in members if f_s and f_s.ev_ebitda is not None]
        fcfys = [f_s.fcf_yield for _, f_s in members if f_s and f_s.fcf_yield is not None]
        med_pe   = round(_stats.median(pes),   1) if len(pes)   >= 2 else None
        med_ev   = round(_stats.median(evs),   1) if len(evs)   >= 2 else None
        med_fcfy = round(_stats.median(fcfys), 1) if len(fcfys) >= 2 else None
        med_str  = " | ".join(filter(None, [
            f"P/E median {med_pe:.1f}x"     if med_pe   is not None else None,
            f"EV/EBITDA median {med_ev:.1f}x" if med_ev is not None else None,
            f"FCF yield median {med_fcfy:.1f}%" if med_fcfy is not None else None,
        ]))
        lines += [
            f"#### {sector_name}",
            f"*{med_str or 'Insufficient data'}*",
            "",
            "| Ticker | P/E | vs Med | EV/EBITDA | vs Med | FCF Yield | vs Med | Score |",
            "|--------|----:|-------:|----------:|-------:|----------:|-------:|------:|",
        ]
        def _vs(val, med, pct=False):
            if val is None or med is None:
                return "—"
            delta = val - med
            sign  = "+" if delta >= 0 else ""
            return f"{sign}{delta:.1f}%" if pct else f"{sign}{delta:.1f}x"
        for s, f_s in sorted(members, key=lambda x: x[0].final_score, reverse=True):
            pe_str = f"{f_s.pe_ratio:.0f}x"  if f_s and f_s.pe_ratio  is not None else "—"
            ev_str = f"{f_s.ev_ebitda:.0f}x" if f_s and f_s.ev_ebitda is not None else "—"
            fy_str = f"{f_s.fcf_yield:.1f}%" if f_s and f_s.fcf_yield is not None else "—"
            lines.append(
                f"| {s.ticker} | {pe_str} | {_vs(f_s.pe_ratio if f_s else None, med_pe)} "
                f"| {ev_str} | {_vs(f_s.ev_ebitda if f_s else None, med_ev)} "
                f"| {fy_str} | {_vs(f_s.fcf_yield if f_s else None, med_fcfy, pct=True)} "
                f"| {s.final_score:.1f} |"
            )
        lines.append("")

    if not peer_written:
        lines.append("*No sectors with multiple companies in this analysis.*")
        lines.append("")
    lines += ["---", ""]

    # ── 11. Data Quality & Limitations ───────────────────────────────────────
    if live:
        fund_caveat = [
            "**Fundamentals:** Live from yfinance. Gov revenue %, DoD %, backlog, and moat rating",
            "come from the curated overlay (`data/mock_fundamentals.json`) — verify against",
            "the latest 10-K before acting. P/E, EV/EBITDA, FCF yield may diverge from",
            "Bloomberg/FactSet; treat as directional.",
        ]
    else:
        fund_caveat = [
            "**Fundamentals:** Offline mock mode — all figures are static estimates.",
            "Run `python main.py` (without `--no-live`) to fetch real-time data from yfinance.",
        ]

    lines += [
        "## 11. Data Quality & Limitations",
        "",
        "| Issue | Detail |",
        "|-------|--------|",
        "| USAspending data lag | 30–90 days. Contracts from the last ~6 weeks may be missing. |",
        "| yfinance accuracy | P/E, EV/EBITDA, FCF yield are directional — not Bloomberg precision. |",
        "| Overlay staleness | DoD%, backlog, moat are manually maintained. Verify vs. latest 10-K. |",
        "| IDIQ ceilings | USAspending shows obligated amounts, not ceiling. Ceiling ≠ guaranteed revenue. |",
        "| Sector classification | Keyword-based on short descriptions. Ticker overrides applied for common misclassifications. |",
        "| Earnings stability | yfinance caps at 4 years. Established primes need `earnings_stability_years` set in overlay. |",
        "| No backtesting | Scoring weights are constructed from first principles — not empirically validated on returns. |",
        "| Graham calibration | Brackets calibrated for defense universe (18–30x P/E = fair). Verdict thresholds adjusted accordingly. |",
        "| DCF sensitivity | Terminal value is 60–80% of total. Use reverse DCF (implied growth) as the primary sanity check. |",
        "",
    ] + fund_caveat + [
        "",
        "**This report is NOT investment advice.**",
        "All figures require independent verification. Consult a licensed financial advisor.",
        "",
        "---",
        "",
        f"*DoD Contract Intelligence Agent v1.0 | {run_date}*",
    ]

    return "\n".join(lines)


def save_report(content: str, path: str) -> None:
    with open(path, "w") as f:
        f.write(content)
    print(f"Report saved → {path}")
