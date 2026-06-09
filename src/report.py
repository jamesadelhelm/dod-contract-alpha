"""
Report generator: produces a detailed markdown analyst-style report.
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Dict, Optional
from src.models import CompanyScore, Contract, Verdict, Sector, SpecialistTierStatus, MacroContext


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


def _compute_position_size(s: CompanyScore, f=None) -> tuple[float, str]:
    """Return (size_pct, sizing_rationale) for a single company.
    Base 6% per PA+ name, scaled by bear-case MoS severity.
    Auto-halved when earnings are within 21 days (binary event risk)."""
    from datetime import datetime as _dt
    is_pa_plus = s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
    )
    b_mos = s.dcf.bear_mos if s.dcf else None
    is_overvalued = any(
        "overvalued at" in fstr.lower() or "dcf:" in fstr.lower()
        for fstr in (s.red_flags or [])
    )
    if not is_pa_plus or is_overvalued:
        return 0.0, ""
    if b_mos is None:
        size_pct, rationale = 3.0, "Half-size (no bear MoS data)"
    elif b_mos > 0:
        size_pct, rationale = 6.0, "Full — bear case confirms MoS"
    elif b_mos >= -15:
        size_pct, rationale = 4.5, "75% — mild tail risk"
    elif b_mos >= -30:
        size_pct, rationale = 3.0, "50% — elevated tail risk"
    else:
        size_pct, rationale = 1.5, "25% — survive the bear scenario"

    # Halve sizing within 21 days of earnings — binary event risk (beat/miss) can
    # gap the stock ±10% regardless of thesis quality.
    if f and getattr(f, "next_earnings_date", None):
        try:
            days_out = (_dt.strptime(f.next_earnings_date, "%Y-%m-%d") - _dt.now()).days
            if 0 < days_out <= 21:
                size_pct = round(size_pct * 0.5, 1)
                rationale = f"{rationale} → halved (earnings in {days_out}d ⚠️)"
        except Exception:
            pass

    return size_pct, rationale

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


def _data_confidence_grade(pct: float) -> tuple[str, str]:
    """Return (letter_grade, emoji) for a data completeness percentage."""
    if pct >= 90:
        return "A", "✅"
    if pct >= 75:
        return "B", "✅"
    if pct >= 60:
        return "C", "⚠️"
    if pct >= 50:
        return "D", "⚠️"
    return "F", "❌"


def _score_trend(ticker: str, current_score: float, history: dict) -> str:
    """Return a trend arrow based on rolling history: ↑ / ↓ / → / (empty)."""
    entries = (history or {}).get(ticker, [])
    if len(entries) < 3:
        return ""
    # Use last 3 entries (not including the current run, which isn't in history yet)
    recent = [e["score"] for e in entries[-3:]]
    avg_old = sum(recent[:2]) / 2
    avg_new = recent[-1]
    if avg_new - avg_old >= 1.0:
        return " ↑"
    elif avg_old - avg_new >= 1.0:
        return " ↓"
    return " →"


def _generate_changes_section(ranked_scores, last_scores, fundamentals_map, score_history=None) -> List[str]:
    """Generate 'Changes Since Last Run' markdown lines from persisted last_scores.json."""
    if not last_scores:
        return ["*No prior run data — this is the first recorded run.*", ""]

    run_dates = [v.get("date") for v in last_scores.values() if v.get("date")]
    last_date = max(run_dates) if run_dates else "unknown"

    current_tickers = {s.ticker for s in ranked_scores}
    last_tickers = set(last_scores.keys())

    changes = []
    new_entries = []
    for s in ranked_scores:
        prev = last_scores.get(s.ticker)
        if prev is None:
            new_entries.append(s.ticker)
            continue
        score_delta = s.final_score - prev["score"]
        verdict_changed = s.verdict.value != prev.get("verdict")
        old_bear = prev.get("bear_mos")
        new_bear = s.dcf.bear_mos if s.dcf else None
        bear_flipped = (
            old_bear is not None and new_bear is not None
            and (old_bear > 0) != (new_bear > 0)
        )
        if abs(score_delta) >= 0.5 or verdict_changed or bear_flipped:
            changes.append({
                "ticker": s.ticker,
                "score_delta": score_delta,
                "old_score": prev["score"],
                "new_score": s.final_score,
                "old_verdict": prev.get("verdict", "—"),
                "new_verdict": s.verdict.value,
                "verdict_changed": verdict_changed,
                "old_bear": old_bear,
                "new_bear": new_bear,
                "bear_flipped": bear_flipped,
            })
    removed = [t for t in last_tickers if t not in current_tickers]

    if not changes and not new_entries and not removed:
        return [f"*No significant changes since {last_date}.*", ""]

    lines = [f"*Compared to run on {last_date}*", ""]

    if changes:
        changes.sort(key=lambda x: abs(x["score_delta"]), reverse=True)
        lines += [
            "| Ticker | Score Δ | Trend | Old → New Score | Verdict Change | Bear MoS Change |",
            "|--------|--------:|:-----:|----------------:|----------------|-----------------|",
        ]
        for ch in changes:
            delta_str = f"{ch['score_delta']:+.1f}" if abs(ch['score_delta']) >= 0.1 else "="
            trend = _score_trend(ch["ticker"], ch["new_score"], score_history)
            verdict_str = (
                f"{ch['old_verdict']} → **{ch['new_verdict']}**"
                if ch["verdict_changed"] else ch["new_verdict"]
            )
            if ch["bear_flipped"]:
                bear_str = f"**{ch['old_bear']:+.0f}% → {ch['new_bear']:+.0f}%** ⚠️ sign flip"
            elif ch["old_bear"] is not None and ch["new_bear"] is not None:
                bd = ch["new_bear"] - ch["old_bear"]
                bear_str = (
                    f"{ch['new_bear']:+.0f}% ({bd:+.0f})" if abs(bd) >= 1
                    else f"{ch['new_bear']:+.0f}%"
                )
            else:
                bear_str = "—"
            lines.append(
                f"| {ch['ticker']} | {delta_str} | {trend.strip() or '—'} | "
                f"{ch['old_score']:.1f} → {ch['new_score']:.1f} | "
                f"{verdict_str} | {bear_str} |"
            )
        lines.append("")

    if new_entries:
        lines.append(f"**New in this run:** {', '.join(new_entries)}")
        lines.append("")
    if removed:
        lines.append(f"**No longer appearing:** {', '.join(removed)}")
        lines.append("")

    # ── Exit / position management signals ───────────────────────────────────
    # These fire when a previously PA+ name has deteriorated. Real capital
    # requires knowing when to exit — not just when to enter.
    _PA_PLUS_VERDICTS = {
        "Strong Candidate", "Research Further", "Potentially Attractive"
    }
    _WATCHLIST_OR_BELOW = {"Watchlist", "High Quality But Expensive", "Low Conviction", "Ignore"}
    exit_signals = []
    for ch in changes:
        was_pa_plus = ch["old_verdict"] in _PA_PLUS_VERDICTS
        now_ignore  = ch["new_verdict"] == "Ignore"
        now_below   = ch["new_verdict"] in _WATCHLIST_OR_BELOW
        bear_turned_negative = (
            ch["bear_flipped"] and ch["old_bear"] is not None and ch["old_bear"] > 0
            and ch["new_bear"] is not None and ch["new_bear"] < 0
        )
        if was_pa_plus and now_ignore:
            exit_signals.append(
                f"🔴 **SELL {ch['ticker']}** — was PA+ ({ch['old_verdict']}), "
                f"now Ignore (score {ch['old_score']:.0f} → {ch['new_score']:.0f}). "
                "Thesis has broken down. Exit position."
            )
        elif was_pa_plus and now_below:
            exit_signals.append(
                f"🟠 **REDUCE {ch['ticker']}** — downgraded from PA+ ({ch['old_verdict']}) "
                f"to {ch['new_verdict']} (score {ch['old_score']:.0f} → {ch['new_score']:.0f}). "
                "Trim position to half; re-evaluate next run."
            )
        elif bear_turned_negative and ch["old_verdict"] in _PA_PLUS_VERDICTS:
            exit_signals.append(
                f"⚠️ **REVIEW {ch['ticker']}** — bear MoS flipped negative "
                f"({ch['old_bear']:+.0f}% → {ch['new_bear']:+.0f}%). Downside protection lost; "
                "reduce to Research Priority sizing (75%) until bear case recovers."
            )

    if exit_signals:
        lines += [
            "**Position Management Signals:**",
            "",
        ]
        for sig in exit_signals:
            lines.append(f"> {sig}")
        lines.append("")

    return lines


def _what_would_change(
    s: CompanyScore,
    f,
    macro: Optional[MacroContext] = None,
) -> List[str]:
    """
    'What would change my mind' analysis for a single PA+ company.

    Computes component fragility (which component needs the smallest absolute
    drop in raw score to flip the verdict from PA+ to Watchlist), then
    maps the most fragile components to specific real-world scenarios.

    PA+ threshold = 68. Points to flip = final_score − 68.
    Component raw-score drop to flip = (points_to_flip) / weight.
    """
    PA_PLUS_FLOOR  = 68.0
    points_to_flip = s.final_score - PA_PLUS_FLOOR

    weights = {
        "Buffett Quality": (s.buffett_quality.raw,   0.25),
        "Graham Value":    (s.graham_value.raw,       0.20),
        "DoD Stability":   (s.dod_stability.raw,      0.20),
        "Management":      (s.management.raw,         0.15),
        "Contract Catalyst":(s.contract_catalyst.raw, 0.10),
        "Balance Sheet":   (s.balance_sheet.raw,      0.10),
    }

    rows = []
    for comp_name, (raw, wt) in weights.items():
        raw_drop_needed = points_to_flip / wt  # how many raw pts this component must drop
        rows.append((comp_name, raw, wt, raw_drop_needed))

    rows.sort(key=lambda r: r[3])  # most fragile first

    lines = [
        "#### What Would Change My Mind",
        "",
        f"*Current score: **{s.final_score:.1f}** — needs to drop **{points_to_flip:.1f} pts** to fall below PA+ threshold (68).*",
        "",
        "| Component | Raw Score | Weight | Raw-pts Drop Needed | Fragility |",
        "|-----------|----------:|-------:|--------------------:|:---------:|",
    ]
    for comp_name, raw, wt, drop_needed in rows:
        fragility = (
            "🔴 Critical" if drop_needed <= 10 else
            "🟡 Moderate" if drop_needed <= 20 else
            "🟢 Resilient"
        )
        lines.append(
            f"| {comp_name} | {raw:.0f} | {wt*100:.0f}% | −{drop_needed:.1f} pts | {fragility} |"
        )
    lines.append("")

    # ── Scenario narratives for the 2 most fragile components ────────────────
    scenarios = []
    seen_comps = set()
    for comp_name, raw, wt, drop_needed in rows[:3]:
        seen_comps.add(comp_name)
        if comp_name == "Graham Value":
            if f and f.current_price and s.dcf and s.dcf.base_iv:
                pct_rise_to_flip = (s.final_score - PA_PLUS_FLOOR) / wt / 100 * s.dcf.base_iv
                new_price = f.current_price + pct_rise_to_flip
                scenarios.append(
                    f"📈 **Multiple expansion**: If the stock rallies to ~${new_price:.0f} "
                    f"(+{pct_rise_to_flip:.0f} from ${f.current_price:.0f}), the MoS compresses "
                    f"and Graham Value score drops ~{drop_needed:.0f} pts → verdict flips to Watchlist. "
                    "Don't chase momentum; set a maximum entry price and hold discipline."
                )
            else:
                scenarios.append(
                    f"📈 **Multiple expansion**: A significant price run-up without a corresponding "
                    f"improvement in earnings would compress FCF yield and P/E, dropping Graham Value "
                    f"~{drop_needed:.0f} raw pts → verdict flips."
                )

        elif comp_name == "DoD Stability":
            dod = f.dod_revenue_pct if f and f.dod_revenue_pct else 0
            required_drop = drop_needed * (dod / 100) if dod > 0 else None
            doge_note = " (DOGE budget cuts are the primary tail risk for this component)" if s.sector.value in ("AI / Data / Software", "Cloud / IT Services", "Consulting / Services") else ""
            if required_drop is not None:
                scenarios.append(
                    f"🏛️ **DoD contract loss**: DoD revenue concentration is currently {dod:.0f}%. "
                    f"A reduction sufficient to drop DoD Stability ~{drop_needed:.0f} raw pts would flip the verdict. "
                    f"Monitor quarterly contract announcements and recompete results.{doge_note}"
                )
            else:
                scenarios.append(
                    f"🏛️ **DoD concentration decline**: DoD Stability is the watch metric. "
                    f"Needs to drop ~{drop_needed:.0f} raw pts to flip verdict.{doge_note}"
                )

        elif comp_name == "Buffett Quality":
            fcf = f.free_cash_flow_margin if f and f.free_cash_flow_margin else None
            roic = f.roic if f and f.roic else None
            fcf_note = f" (current FCF margin: {fcf:.1f}%)" if fcf else ""
            roic_note = f" / ROIC {roic:.1f}%" if roic else ""
            scenarios.append(
                f"⚠️ **Quality deterioration**: FCF margin compression{fcf_note}{roic_note} would reduce "
                f"Buffett Quality by the required ~{drop_needed:.0f} pts. "
                "Watch the earnings trend across 2 consecutive quarters before assuming the thesis has broken."
            )

        elif comp_name == "Management":
            scenarios.append(
                f"👔 **Management signal**: An abrupt CEO transition, poor capital allocation "
                f"(large dilutive acquisition or equity offering), or insider distribution exceeding 40% "
                f"over 6 months would reduce Management score by ~{drop_needed:.0f} pts → verdict flips."
            )

        elif comp_name == "Balance Sheet":
            de = f.debt_equity if f and f.debt_equity else None
            de_note = f" (current D/E: {de:.1f}x)" if de else ""
            scenarios.append(
                f"🏦 **Balance sheet deterioration**: Significant leverage increase{de_note}, "
                f"interest coverage dropping below 3×, or a credit downgrade would reduce "
                f"Balance Sheet score by ~{drop_needed:.0f} pts → verdict flips."
            )

        elif comp_name == "Contract Catalyst":
            scenarios.append(
                f"📋 **Contract pipeline dries up**: A sustained period without material new awards "
                f"or meaningful IDIQ task orders would reduce Contract Catalyst by ~{drop_needed:.0f} pts. "
                "Watch quarterly USAspending data for award velocity."
            )

    # DCF bear MoS scenario (always worth including for PA+ names)
    if s.dcf and s.dcf.bear_mos is not None and macro and macro.rate_delta_pp is not None:
        sensitivity = 0.70 / (0.09 - 0.025)  # ~10.8x
        rate_to_break_shield = None
        if s.dcf.bear_mos > 0:
            # At what additional rate rise does bear MoS flip negative?
            # sensitivity is pct-IV-drop per pp-WACC. bear_mos is %. So:
            # rate_to_break (pp) = bear_mos_pct / sensitivity_pct_per_pp
            rate_to_break = s.dcf.bear_mos / sensitivity  # pp of 10-yr rise
            total_yield = (macro.ten_year_yield or 4.5) + rate_to_break
            scenarios.append(
                f"📈 **Rate spike**: Bear MoS is currently 🛡️ +{s.dcf.bear_mos:.0f}%. "
                f"A rate rise of +{rate_to_break:.2f}pp (10-yr reaching ~{total_yield:.2f}%) "
                f"would erase the bear-case downside protection. "
                "If yield approaches this level, reduce to 75% sizing and re-run."
            )
        elif s.dcf.bear_mos < -10:
            scenarios.append(
                f"⚠️ **Rate sensitivity**: Bear MoS is {s.dcf.bear_mos:.0f}%. At current rates "
                f"({(macro.ten_year_yield or 4.5):.2f}%), the bear case already implies capital loss. "
                "A further +50bps in 10-yr yield makes the bear IV meaningfully worse. "
                "Keep to 50% sizing until bear MoS improves."
            )

    if scenarios:
        lines += [
            "**Thesis-break scenarios (in order of fragility):**",
            "",
        ]
        for sc in scenarios:
            lines.append(f"> {sc}")
        lines.append("")

    lines += [
        "> *Exit rule: If any ❌ scenario materializes AND the PA+ verdict flips to Watchlist on the*",
        "> *next full run, execute the REDUCE signal from the Changes Since Last Run section.*",
        "",
    ]
    return lines


def _generate_macro_context_section(
    macro: Optional[MacroContext],
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    Render a Macro Context box showing the current interest rate environment
    and its impact on all DCF intrinsic values shown in this report.

    The DCF uses base WACC = 9%, which implies Rf ≈ 4.5% (beta~1, ERP~4.5%).
    When the 10-yr yield deviates from 4.5%, intrinsic values shift:
      +1pp WACC → -10.8% IV (terminal value dominates a 10-yr DCF at 9%/2.5%).
    """
    lines = ["## Macro Context", ""]

    if macro is None or (macro.ten_year_yield is None and macro.fetch_error):
        err = getattr(macro, "fetch_error", "unavailable")
        lines += [
            f"> ⚠️ Rate data unavailable ({err}). "
            "DCF intrinsic values assume Rf = 4.5% (9% base WACC). "
            "Verify current 10-yr Treasury yield before deploying capital.",
            "",
        ]
    else:
        yield_str = f"{macro.ten_year_yield:.2f}%" if macro.ten_year_yield is not None else "—"
        tbill_str = f"{macro.three_month_yield:.2f}%" if macro.three_month_yield is not None else "—"
        delta_str = f"{macro.rate_delta_pp:+.2f}pp" if macro.rate_delta_pp is not None else "—"

        # Yield curve inversion signal
        curve_note = ""
        if macro.ten_year_yield is not None and macro.three_month_yield is not None:
            spread = macro.ten_year_yield - macro.three_month_yield
            if spread < -0.5:
                curve_note = f" ⚠️ Inverted yield curve (spread {spread:+.2f}pp) — historically precedes recession within 12–18 months."
            elif spread < 0:
                curve_note = f" Flat/slightly inverted (spread {spread:+.2f}pp)."
            else:
                curve_note = f" Normal curve (spread +{spread:.2f}pp)."

        lines += [
            f"| Indicator | Live | DCF Baseline | Δ |",
            f"|-----------|-----:|-------------:|--:|",
            f"| 10-yr Treasury Yield (Rf proxy) | **{yield_str}** | 4.50% | {delta_str} |",
            f"| 3-mo T-Bill | {tbill_str} | — | — |",
            f"| DoD Budget FY2026 | ${macro.defense_budget_bn:.0f}B | — | +{macro.defense_budget_growth_pct:.1f}% YoY |",
            "",
        ]

        if macro.ten_year_yield is not None and macro.rate_delta_pp is not None:
            impact = macro.iv_impact_pct or 0.0
            if abs(impact) < 1.0:
                impact_msg = (
                    f"10-yr yield ({macro.ten_year_yield:.2f}%) is within **1pp of DCF baseline (4.5%)**. "
                    "DCF intrinsic values are essentially at-rate — no material adjustment needed."
                )
            elif impact > 0:
                impact_msg = (
                    f"10-yr yield ({macro.ten_year_yield:.2f}%) is **{macro.rate_delta_pp:.2f}pp below DCF baseline**. "
                    f"Lower rates → DCF intrinsic values are approximately **{impact:.1f}% higher** than they'd be "
                    f"at the baseline Rf. Favorable for IV but watch for rate normalization risk."
                )
            else:
                impact_msg = (
                    f"10-yr yield ({macro.ten_year_yield:.2f}%) is **{macro.rate_delta_pp:+.2f}pp above DCF baseline (4.5%)**. "
                    f"Higher rates → DCF intrinsic values are approximately **{abs(impact):.1f}% lower** than shown. "
                    f"Apply a mental haircut to IVs below."
                )

            lines.append(f"> 📊 **Rate environment:** {impact_msg}{curve_note}")
            lines.append("")

            # Show rate-adjusted IVs for each PA+ name
            pa_plus = [
                s for s in ranked_scores
                if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
                and s.dcf and s.dcf.base_iv is not None and s.dcf.base_iv > 0
            ]
            if pa_plus and abs(impact) >= 1.0:
                lines.append(
                    f"> **Rate-adjusted intrinsic values** ({macro.rate_delta_pp:+.2f}pp rate delta, "
                    f"~{impact:.1f}% IV impact):"
                )
                for s in pa_plus:
                    adj_base = s.dcf.base_iv * (1 + impact / 100)
                    adj_bear = (s.dcf.bear_iv * (1 + impact / 100)) if s.dcf.bear_iv else None
                    f_m = (fundamentals_map or {}).get(s.ticker)
                    cur = f_m.current_price if f_m and f_m.current_price else None
                    cur_str = f" vs ${cur:.0f} now" if cur else ""
                    bear_adj_str = f" | bear IV → ${adj_bear:.0f}" if adj_bear else ""
                    lines.append(
                        f">   - **{s.ticker}**: base IV ${s.dcf.base_iv:.0f} → **${adj_base:.0f}**{bear_adj_str}{cur_str}"
                    )
                lines.append("")
        else:
            lines += [
                "> Rate data unavailable. DCF intrinsic values assume Rf = 4.5% (9% base WACC).",
                "",
            ]

    # Defense budget note (always shown)
    lines += [
        "> 🏛️ **Defense budget:** FY2026 DoD topline $895B (+3.3% vs FY2025). "
        "Topline growth supports the bear-case revenue assumptions for defense primes. "
        "DOGE-driven efficiency initiatives remain a headwind for IT services and consulting revenue.",
        "",
    ]

    return lines


def _generate_portfolio_section(
    portfolio: dict,
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    Generate a Portfolio Review section for the markdown report.
    Reads held positions from data/portfolio.json and compares against current scores.
    """
    if not portfolio:
        return []

    _PA_PLUS = {"Strong Candidate", "Research Further", "Potentially Attractive"}
    score_map = {s.ticker: s for s in ranked_scores}

    lines = [
        "## Portfolio Review",
        "",
        "> *Positions from `data/portfolio.json` compared against current scores.*",
        "> *Thesis-intact check: ✅ = verdict unchanged; ⚠️ = watch; 🟠 = REDUCE; 🔴 = SELL.*",
        "",
        "| Ticker | Shares | Cost | Now | P&L $ | P&L % | Score | Bear MoS | Thesis Status |",
        "|--------|-------:|-----:|----:|------:|------:|------:|---------:|---------------|",
    ]

    alerts = []
    total_cost = 0.0
    total_mkt  = 0.0

    for ticker, pos in portfolio.items():
        cost   = pos.get("cost_basis", 0.0)
        shares = pos.get("shares", 0)
        t_verd = pos.get("thesis_verdict", "")
        t_score = pos.get("thesis_score")
        t_bear  = pos.get("thesis_bear_mos")

        s = score_map.get(ticker)
        f = (fundamentals_map or {}).get(ticker)
        cur_price = f.current_price if f and f.current_price else None

        price_str = f"${cur_price:.2f}" if cur_price else "—"
        cost_str  = f"${cost:.2f}"

        if cur_price and shares:
            mkt  = cur_price * shares
            cv   = cost * shares
            pnl  = mkt - cv
            pnlp = (cur_price - cost) / cost * 100 if cost else 0
            total_cost += cv
            total_mkt  += mkt
            pnl_str  = f"${pnl:+.0f}"
            pnlp_str = f"{pnlp:+.1f}%"
        else:
            pnl_str = pnlp_str = "—"

        if s:
            cur_v    = s.verdict.value
            cur_scr  = s.final_score
            cur_bear = s.dcf.bear_mos if s.dcf else None
            was_pa   = t_verd in _PA_PLUS
            now_pa   = cur_v in _PA_PLUS
            bear_flipped = (
                t_bear is not None and cur_bear is not None
                and (t_bear > 0) != (cur_bear > 0)
            )
            score_decay = t_score is not None and cur_scr < t_score - 3

            if was_pa and cur_v == "Ignore":
                status = "🔴 SELL"
                alerts.append(f"🔴 **SELL {ticker}** — verdict collapsed from PA+ to Ignore.")
            elif was_pa and not now_pa:
                status = f"🟠 REDUCE → {cur_v}"
                alerts.append(f"🟠 **REDUCE {ticker}** — downgraded from PA+ to {cur_v}.")
            elif bear_flipped and was_pa:
                status = "⚠️ REVIEW (bear flip)"
                alerts.append(f"⚠️ **REVIEW {ticker}** — bear MoS sign flipped: thesis must be re-examined.")
            elif score_decay:
                status = f"⚠️ WATCH (score −{t_score - cur_scr:.1f})"
                alerts.append(f"⚠️ **WATCH {ticker}** — score has declined {t_score - cur_scr:.1f} pts since entry.")
            else:
                status = "✅ Intact"

            scr_str  = f"{cur_scr:.1f}"
            bear_str = (
                f"🛡️ +{cur_bear:.0f}%" if cur_bear and cur_bear > 0
                else (f"{cur_bear:.0f}%" if cur_bear is not None else "—")
            )
        else:
            status = "— (not in run)"
            scr_str = bear_str = "—"

        lines.append(
            f"| **{ticker}** | {shares} | {cost_str} | {price_str} | {pnl_str} | {pnlp_str} | {scr_str} | {bear_str} | {status} |"
        )

    lines.append("")

    if total_cost > 0:
        total_pnl  = total_mkt - total_cost
        total_pnlp = total_pnl / total_cost * 100
        lines.append(
            f"**Portfolio totals:** Cost ${total_cost:,.0f} | "
            f"Market value ${total_mkt:,.0f} | "
            f"P&L ${total_pnl:+,.0f} ({total_pnlp:+.1f}%)"
        )
        lines.append("")

    if alerts:
        lines += ["**⚠️ Action required:**", ""]
        for a in alerts:
            lines.append(f"> {a}")
        lines.append("")
    else:
        lines.append("✅ **All positions: thesis intact** — no action required this run.")
        lines.append("")

    lines += [
        "> To update positions, edit `data/portfolio.json`. "
        "See `data/portfolio_template.json` for format.",
        "",
        "---", "",
    ]
    return lines


def _conviction_checklist(
    s: CompanyScore,
    f,
    size_pct: float,
    macro: Optional[MacroContext] = None,
) -> List[str]:
    """
    Generate a pre-deployment conviction checklist for a single PA+ company.
    Returns markdown lines including a header, a table, and a pass/fail verdict.

    Five checks run in this order:
    1. Earnings timing — is the stock within the pre-earnings binary-event window?
    2. Street consensus — is the Street aligned or sharply contra our thesis?
    3. Price positioning — is the stock near its 52-week high (chasing) or washed out?
    4. Insider activity — are insiders net buyers or net sellers over the past 6 months?
    5. Macro rate check — is the live 10-yr yield within 1pp of DCF baseline Rf?
    """
    from datetime import datetime as _dt4

    checks = []
    warnings = 0

    # ── 1. Earnings timing ─────────────────────────────────────────────────────
    if f and getattr(f, "next_earnings_date", None):
        try:
            days_out = (_dt4.strptime(f.next_earnings_date, "%Y-%m-%d") - _dt4.now()).days
            if days_out <= 0:
                status, detail = "✅", f"Earnings have passed — full-size position permitted"
            elif days_out <= 7:
                status, detail = "❌", f"Earnings in **{days_out}d** — binary risk, hold off until post-report"
                warnings += 2  # hard block
            elif days_out <= 21:
                status, detail = "⚠️", f"Earnings in **{days_out}d** — position **auto-halved to {size_pct:.1f}%** (earnings risk window)"
                warnings += 1
            else:
                status, detail = "✅", f"Next earnings: {f.next_earnings_date} ({days_out}d) — clear of binary event window"
        except Exception:
            status, detail = "—", f"Earnings date: {f.next_earnings_date} (could not parse)"
    else:
        status, detail = "—", "Earnings date unavailable — verify before sizing"

    checks.append(("Earnings timing", status, detail))

    # ── 2. Street consensus ────────────────────────────────────────────────────
    if f and f.analyst_recommendation and f.analyst_count:
        rec = f.analyst_recommendation.lower()
        n   = f.analyst_count
        tgt_str = f" | target ${f.analyst_target_price:.0f} ({f.upside_to_target:+.0f}%)" if f.analyst_target_price and f.upside_to_target else ""
        if rec in ("strong_buy", "buy"):
            status, detail = "✅", f"**{rec}** consensus ({n} analysts){tgt_str} — Street aligned with our thesis"
        elif rec in ("hold", "neutral"):
            status, detail = "⚠️", f"**hold** consensus ({n} analysts){tgt_str} — Street cautious; our model is more bullish (contrarian opportunity if thesis holds)"
            warnings += 1
        else:
            status, detail = "⚠️", f"**{rec}** consensus ({n} analysts){tgt_str} — bearish Street vs. our PA+ rating; verify thesis is sound before sizing up"
            warnings += 1
    else:
        status, detail = "—", "No analyst consensus data — check manually before deploying"

    checks.append(("Street consensus", status, detail))

    # ── 3. Price positioning (vs 52-week range) ────────────────────────────────
    if f and f.pct_off_52w_high is not None:
        off = f.pct_off_52w_high  # negative = below 52w high
        range_str = ""
        if f.price_52w_low and f.price_52w_high and f.current_price:
            rng = f.price_52w_high - f.price_52w_low
            if rng > 0:
                pct_from_low = (f.current_price - f.price_52w_low) / rng * 100
                range_str = f" | {pct_from_low:.0f}% from 52w low"
        if off <= -25:
            status, detail = "✅", f"{off:+.0f}% off 52-week high{range_str} — significant pullback, not chasing"
        elif off <= -10:
            status, detail = "✅", f"{off:+.0f}% off 52-week high{range_str} — reasonable pullback, fair entry"
        elif off <= -3:
            status, detail = "⚠️", f"{off:+.0f}% off 52-week high{range_str} — near highs; consider waiting for a 5–10% pullback"
            warnings += 1
        else:
            status, detail = "⚠️", f"At or near **52-week high** ({off:+.0f}%){range_str} — entering at all-time highs; position sizing discipline is critical"
            warnings += 1
    else:
        status, detail = "—", "52-week range data unavailable"

    checks.append(("Price positioning", status, detail))

    # ── 4. Insider activity ────────────────────────────────────────────────────
    if f and getattr(f, "insider_net_pct_6m", None) is not None:
        ins_pct = f.insider_net_pct_6m * 100
        if ins_pct >= 10:
            status, detail = "✅", f"Net insider **buying** (+{ins_pct:.0f}% of held shares, 6m) — management aligned with thesis"
        elif ins_pct <= -20:
            status, detail = "⚠️", f"Net insider **selling** ({ins_pct:.0f}% of held shares, 6m) — warrants scrutiny (could be planned selling, but verify)"
            warnings += 1
        elif ins_pct <= -40:
            status, detail = "❌", f"Heavy insider **selling** ({ins_pct:.0f}% of held shares, 6m) — significant insider distribution; re-examine thesis"
            warnings += 2
        else:
            status, detail = "✅", f"Insider activity neutral ({ins_pct:+.0f}% net, 6m) — no clear signal"
    else:
        status, detail = "—", "Insider transaction data unavailable — check SEC Form 4 filings"

    checks.append(("Insider activity", status, detail))

    # ── 5. Macro rate environment ──────────────────────────────────────────────
    if macro and macro.ten_year_yield is not None and macro.rate_delta_pp is not None:
        delta = macro.rate_delta_pp
        impact = macro.iv_impact_pct or 0.0
        if abs(delta) < 0.5:
            status, detail = "✅", f"10-yr yield {macro.ten_year_yield:.2f}% ≈ DCF baseline ({delta:+.2f}pp) — IVs valid as shown"
        elif delta > 0:
            status, detail = "⚠️", (
                f"10-yr yield {macro.ten_year_yield:.2f}% is **{delta:+.2f}pp above DCF baseline** — "
                f"rate-adjusted IVs are ~{abs(impact):.1f}% lower than shown. "
                + ("Bear IV still positive — shield intact." if s.dcf and s.dcf.bear_mos is not None and s.dcf.bear_mos + impact > 0
                   else "Verify bear IV holds at current rates.")
            )
            if abs(impact) >= 5:
                warnings += 1
        else:
            status, detail = "✅", (
                f"10-yr yield {macro.ten_year_yield:.2f}% is **{delta:.2f}pp below DCF baseline** — "
                f"IVs are ~{abs(impact):.1f}% higher than baseline; favorable rate environment"
            )
    else:
        status, detail = "—", "Rate data unavailable — fetch live (^TNX) before deploying capital"

    checks.append(("Macro rate check", status, detail))

    # ── 6. Data confidence ─────────────────────────────────────────────────────
    pct = getattr(s, "data_completeness_pct", None)
    if pct is not None:
        grade, _ = _data_confidence_grade(pct)
        if pct >= 75:
            status, detail = "✅", f"Data completeness **{pct:.0f}%** (grade {grade}) — key metrics fully populated"
        elif pct >= 60:
            status, detail = "⚠️", (
                f"Data completeness **{pct:.0f}%** (grade {grade}) — some key fields missing. "
                "Score could be off by ±3–5 pts; verify missing fundamentals before sizing up."
            )
            warnings += 1
        elif pct >= 50:
            status, detail = "⚠️", (
                f"Data completeness **{pct:.0f}%** (grade {grade}) — multiple key fields missing. "
                "Treat score as directional only. Confirm via 10-K before deploying."
            )
            warnings += 1
        else:
            status, detail = "❌", (
                f"Data completeness **{pct:.0f}%** (grade {grade}) — too many data gaps. "
                "Score confidence is too low for capital deployment. "
                "Add fundamentals to `data/mock_fundamentals.json` first."
            )
            warnings += 2
    else:
        status, detail = "—", "Data completeness unknown"

    checks.append(("Data confidence", status, detail))

    # ── Render ─────────────────────────────────────────────────────────────────
    lines = [
        "#### Pre-Deployment Checklist",
        "",
        "| Check | Status | Detail |",
        "|-------|:------:|--------|",
    ]
    for label, st, det in checks:
        lines.append(f"| {label} | {st} | {det} |")
    lines.append("")

    fails    = sum(1 for _, st, _ in checks if st == "❌")
    hard_go  = fails == 0 and warnings == 0
    soft_go  = fails == 0 and warnings > 0

    if hard_go:
        lines.append(
            f"**✅ Ready to Deploy** — All checks clear. "
            f"Execute at up to **{size_pct:.1f}%** per Capital Deployment guidance."
        )
    elif soft_go:
        lines.append(
            f"**⚠️ Conditional Deploy** — {warnings} caution(s) flagged. "
            f"Review highlighted items before executing. If comfortable, proceed at "
            f"**{size_pct:.1f}%** (or reduce 50% until cautions resolved)."
        )
    else:
        lines.append(
            f"**❌ Hold — Do Not Deploy** — {fails} blocking issue(s). "
            "Resolve flagged items before initiating position."
        )

    lines.append("")
    return lines


def _generate_pa_buy_priority(
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
    macro: Optional[MacroContext] = None,
) -> List[str]:
    """
    Side-by-side ranking of PA+ names: 'Which do I buy first today?'

    Priority: bear MoS > 0 first (downside protection confirmed), then composite score.
    Shows current price vs. bear IV entry target, market pessimism premium, action label.
    Only rendered when 2+ PA+ names are present (single-name screen doesn't need comparison).
    """
    PA_PLUS = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}
    pa_names = [
        s for s in ranked_scores if s.verdict in PA_PLUS
        and not any("overvalued at" in fl.lower() or "dcf:" in fl.lower()
                    for fl in (s.red_flags or []))
    ]

    if len(pa_names) < 2:
        return []

    def _priority_key(s):
        b = s.dcf.bear_mos if s.dcf else None
        return (0 if (b is not None and b > 0) else 1, -s.final_score)

    sorted_pa = sorted(pa_names, key=_priority_key)

    lines = [
        "### 1b. Buy Priority — Which PA+ Name Do I Buy First?",
        "",
        "> **Priority order:** names with bear-case downside protection (🛡 bear MoS > 0) ranked first, then by score.",
        "> **Gap to entry:** how far current price must fall to reach bear IV. +ve (🟢) = bear IV above price — no pullback needed.",
        "> **Mkt vs Base:** reverse-DCF implied growth minus our base-case growth. Negative = market more pessimistic than us.",
        "",
        "| # | Ticker | Score | Bear MoS | Price | Bear IV | Gap to Entry | Mkt vs Base | Size | Action |",
        "|---|--------|------:|:--------:|------:|--------:|:------------:|:-----------:|-----:|:------:|",
    ]

    rank_labels = ["🥇", "🥈", "🥉"] + [f"#{i}" for i in range(4, 12)]

    for i, s in enumerate(sorted_pa):
        f = (fundamentals_map or {}).get(s.ticker)
        sz, _ = _compute_position_size(s, f)

        bear_mos  = s.dcf.bear_mos if s.dcf else None
        bear_iv   = s.dcf.bear_iv  if s.dcf else None
        impl_g    = s.dcf.implied_growth_rate if s.dcf else None
        base_g    = s.dcf.base_growth if s.dcf else None
        cur_price = f.current_price if f and f.current_price else None

        bear_mos_str = (
            f"🛡️ +{bear_mos:.0f}%" if bear_mos is not None and bear_mos > 0
            else (f"{bear_mos:.0f}%" if bear_mos is not None else "—")
        )
        price_str   = f"${cur_price:.0f}" if cur_price else "—"
        bear_iv_str = f"${bear_iv:.0f}"   if bear_iv  else "—"

        if bear_iv and cur_price and cur_price > 0:
            gap_pct = (bear_iv - cur_price) / cur_price * 100
            gap_str = f"+{gap_pct:.0f}% 🟢" if gap_pct >= 0 else f"{gap_pct:.0f}%"
        else:
            gap_str = "—"

        pess_str = (
            f"{(impl_g - base_g):+.0f}pp"
            if impl_g is not None and base_g is not None else "—"
        )

        sz_str = f"{sz:.1f}%" if sz > 0 else "—"

        if bear_mos is not None and bear_mos > 0:
            action = "**BUY**"
        elif bear_mos is not None and bear_mos >= -15:
            action = "Start 75%"
        elif bear_mos is not None and bear_mos >= -30:
            action = "Start 50%"
        elif bear_mos is not None:
            action = "25% only"
        else:
            action = "—"

        rank_str = rank_labels[i] if i < len(rank_labels) else f"#{i+1}"
        lines.append(
            f"| {rank_str} | **{s.ticker}** | {s.final_score:.1f} | {bear_mos_str} "
            f"| {price_str} | {bear_iv_str} | {gap_str} | {pess_str} | {sz_str} | {action} |"
        )

    lines.append("")

    # Narrative
    top = sorted_pa[0]
    top_f = (fundamentals_map or {}).get(top.ticker)
    top_bear_mos = top.dcf.bear_mos if top.dcf else None
    top_impl_g   = top.dcf.implied_growth_rate if top.dcf else None
    top_base_g   = top.dcf.base_growth if top.dcf else None

    reasoning = []
    if top_bear_mos is not None and top_bear_mos > 0:
        reasoning.append(
            f"🥇 **{top.ticker}** is the highest-priority entry — the bear DCF scenario still "
            f"yields +{top_bear_mos:.0f}% margin of safety. No pullback required before initiating."
        )
    else:
        reasoning.append(
            f"🥇 **{top.ticker}** ranks first by score ({top.final_score:.1f}) but no name "
            "in this screen has bear-case protection. Use reduced sizing across the board."
        )
    if top_impl_g is not None and top_base_g is not None and top_impl_g < top_base_g - 2:
        reasoning.append(
            f"Market prices in only **{top_impl_g:.0f}%/yr growth** for {top.ticker} vs. "
            f"our base-case {top_base_g:.0f}%/yr — market pessimism premium embedded in price."
        )
    if len(sorted_pa) >= 2:
        second = sorted_pa[1]
        sz2, _ = _compute_position_size(second, (fundamentals_map or {}).get(second.ticker))
        reasoning.append(
            f"🥈 **{second.ticker}** is the second entry — deploy at **{sz2:.0f}%** alongside {top.ticker}."
        )

    lines.append("**Deployment reasoning:**\n")
    for r in reasoning:
        lines.append(f"> {r}")

    cluster_warnings = []
    all_pa_tickers = {s.ticker for s in sorted_pa}
    if all_pa_tickers & _DOGE_CLUSTER:
        cluster_warnings.append(
            f"⚠️ Federal IT cluster risk: {', '.join(sorted(all_pa_tickers & _DOGE_CLUSTER))} — combined cap {_DOGE_CAP_PCT:.0f}%"
        )
    if all_pa_tickers & _AEROSPACE_CLUSTER:
        cluster_warnings.append(
            f"⚠️ Aerospace/Defense cluster: {', '.join(sorted(all_pa_tickers & _AEROSPACE_CLUSTER))} — combined cap {_AEROSPACE_CAP_PCT:.0f}%"
        )
    for cw in cluster_warnings:
        lines.append(f">\n> {cw}")

    lines.append("")
    return lines


def generate_report(
    ranked_scores: List[CompanyScore],
    private_contracts: List[Contract],
    all_contracts: List[Contract],
    run_date: str = None,
    live: bool = True,
    fundamentals_map: Dict = None,
    last_scores: Dict = None,
    score_history: Dict = None,
    macro_context: Optional[MacroContext] = None,
    portfolio: Dict = None,
    portfolio_size: Optional[float] = None,
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
    _deploy_rows_ex = [(s, *_compute_position_size(s, (fundamentals_map or {}).get(s.ticker))) for s in ranked_scores]
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
    ]

    # ── Macro Context ─────────────────────────────────────────────────────────
    lines += _generate_macro_context_section(macro_context, ranked_scores, fundamentals_map or {})
    lines += ["---", ""]

    # ── Portfolio Review (if positions present) ───────────────────────────────
    if portfolio:
        lines += _generate_portfolio_section(portfolio, ranked_scores, fundamentals_map or {})

    lines += [
        "## Executive Summary",
        "",
    ]
    for part in exec_parts:
        lines.append(part)
        lines.append("")
    lines += ["---", ""]

    # ── Changes Since Last Run ────────────────────────────────────────────────
    lines += [
        "## Changes Since Last Run",
        "",
    ]
    lines += _generate_changes_section(ranked_scores, last_scores, fundamentals_map, score_history)
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

    # ── Liquidity warnings for PA+ names ──────────────────────────────────────
    # Flag when a high-conviction name can't absorb meaningful capital.
    # $2M/day threshold: a 1% of portfolio position in a $500K account = $5K,
    # which can be filled in under 30 minutes at $2M/day.
    _LIQUIDITY_THRESHOLD_M = 2.0
    pa_plus_names = [s for s in ranked_scores if s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
    )]
    liquidity_warnings = []
    for s in pa_plus_names:
        f_liq = (fundamentals_map or {}).get(s.ticker)
        if f_liq and f_liq.avg_daily_volume and f_liq.current_price:
            dollar_vol_m = f_liq.avg_daily_volume * f_liq.current_price / 1_000_000
            if dollar_vol_m < _LIQUIDITY_THRESHOLD_M:
                liquidity_warnings.append(
                    f"⚠️ **{s.ticker}** avg daily volume ~${dollar_vol_m:.1f}M — "
                    f"below ${_LIQUIDITY_THRESHOLD_M:.0f}M threshold. "
                    "Limit individual orders to < 5% of daily volume to avoid moving the market."
                )
    if liquidity_warnings:
        lines.append("**Liquidity warnings (PA+ names):**")
        for w in liquidity_warnings:
            lines.append(f"- {w}")
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
        lines.append("")

        # Watchlist buy triggers: show the price at which each overvalued/watchlist
        # name would cross into PA+ territory (base MoS ≥ 0 → base IV = entry trigger).
        watchlist_triggers = []
        for s in ranked_scores:
            is_watchlist_q = s.verdict in (Verdict.WATCHLIST, Verdict.HIGH_QUALITY_BUT_EXPENSIVE)
            is_overval = any("overvalued at" in f.lower() or "dcf:" in f.lower() for f in (s.red_flags or []))
            if (is_watchlist_q or is_overval) and s.dcf and s.dcf.base_iv:
                f_wt = (fundamentals_map or {}).get(s.ticker)
                cur = f_wt.current_price if f_wt and f_wt.current_price else None
                gap_str = ""
                if cur and cur > s.dcf.base_iv:
                    gap_pct = (cur - s.dcf.base_iv) / cur * 100
                    gap_str = f" — {gap_pct:.0f}% above trigger"
                watchlist_triggers.append(
                    f"**{s.ticker}**: buy below **${s.dcf.base_iv:.0f}** (base IV){gap_str}"
                )
        if watchlist_triggers:
            lines.append("**Watchlist buy triggers** (price at which name enters PA+ territory):")
            for wt in watchlist_triggers:
                lines.append(f"- {wt}")
            lines.append("")

        lines += [
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
        f_ds = (fundamentals_map or {}).get(s.ticker)
        size_pct, rationale = _compute_position_size(s, f_ds)
        if size_pct > 0:
            b_mos = s.dcf.bear_mos if s.dcf else None
            bear_iv = s.dcf.bear_iv if s.dcf else None
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
            # Explicit action label: removes ambiguity about what to do right now.
            if b_mos is not None and b_mos > 0:
                action_str = "**BUY**"
            elif b_mos is not None and b_mos >= -15:
                action_str = "Start 75%"
            elif b_mos is not None and b_mos >= -30:
                action_str = "Start 50%"
            elif b_mos is not None:
                action_str = "Speculative 25%"
            else:
                action_str = "—"
            deployable_rows.append((s.ticker, tier_label, bmos_str, entry_str, action_str, rationale, size_pct, cur_price))

    if deployable_rows:
        total_pct = sum(r[6] for r in deployable_rows)
        cash_pct = 100.0 - total_pct

        # If portfolio_size provided, add dollar amount and share count columns
        if portfolio_size and portfolio_size > 0:
            header = "| Ticker | Now | Entry Target | Action | Tier | Bear MoS | Sizing Logic | Weight | $ Amount | Shares |"
            divider = "|--------|----:|-------------:|:------:|------|:--------:|:-------------|-------:|---------:|-------:|"
        else:
            header = "| Ticker | Now | Entry Target | Action | Tier | Bear MoS | Sizing Logic | Weight |"
            divider = "|--------|----:|-------------:|:------:|------|:--------:|:-------------|-------:|"

        lines += [
            "**Position sizing guidance (% of equity portfolio):**",
            "",
            header, divider,
        ]
        for ticker, tier_label, bmos_str, entry_str, action_str, rationale, size_pct, cur_price in deployable_rows:
            price_str = f"${cur_price:.0f}" if cur_price else "—"
            row = f"| {ticker} | {price_str} | {entry_str} | {action_str} | {tier_label} | {bmos_str} | {rationale} | {size_pct:.1f}% |"
            if portfolio_size and portfolio_size > 0:
                dollar_amt = portfolio_size * size_pct / 100.0
                if cur_price and cur_price > 0:
                    n_shares = int(dollar_amt / cur_price)
                    shares_str = f"{n_shares:,}"
                else:
                    shares_str = "—"
                row += f" ${dollar_amt:,.0f} | {shares_str} |"
            lines.append(row)

        lines += [
            "",
            f"**Actionable weight: {total_pct:.1f}%** ({f'${portfolio_size * total_pct / 100:,.0f} of ${portfolio_size:,.0f}' if portfolio_size else ''})"
            f"&nbsp;|&nbsp; **Hold cash: {cash_pct:.1f}%**"
            + (f" (${portfolio_size * cash_pct / 100:,.0f})" if portfolio_size else ""),
            "",
        ]

        # Cluster cap reporting (size_pct is index 6 now — after action_str was added at index 4)
        doge_pct  = sum(r[6] for r in deployable_rows if r[0] in _DOGE_CLUSTER)
        aero_pct  = sum(r[6] for r in deployable_rows if r[0] in _AEROSPACE_CLUSTER)
        doge_tickers = [r[0] for r in deployable_rows if r[0] in _DOGE_CLUSTER]
        aero_tickers = [r[0] for r in deployable_rows if r[0] in _AEROSPACE_CLUSTER]
        cluster_lines = []
        if doge_tickers:
            flag = "⚠️ EXCEEDS CAP — scale back proportionally" if doge_pct > _DOGE_CAP_PCT else "✅"
            comp_str = " + ".join(f"{t} {next(r[6] for r in deployable_rows if r[0]==t):.1f}%" for t in doge_tickers)
            cluster_lines.append(
                f"- Federal IT / DOGE risk (cap {_DOGE_CAP_PCT:.0f}%): "
                f"{comp_str} = {doge_pct:.1f}% {flag}"
            )
        if aero_tickers:
            flag = "⚠️ EXCEEDS CAP — scale back proportionally" if aero_pct > _AEROSPACE_CAP_PCT else "✅"
            comp_str = " + ".join(f"{t} {next(r[6] for r in deployable_rows if r[0]==t):.1f}%" for t in aero_tickers)
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

    # ── 1b. PA+ Buy Priority ──────────────────────────────────────────────────
    lines += _generate_pa_buy_priority(ranked_scores, fundamentals_map or {}, macro=macro_context)

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

            f_ksd = (fundamentals_map or {}).get(s.ticker)
            sz, sz_logic = _compute_position_size(s, f_ksd)
            if sz > 0:
                key_sigs.append(
                    f"Recommended weight: **{sz:.1f}%** of portfolio — {sz_logic}."
                )
            if key_sigs:
                lines += ["#### Key Signals", ""]
                for sig in key_sigs:
                    lines.append(f"> {sig}")
                lines.append("")

            # Pre-deployment conviction checklist — only for PA+ names
            f_chk = (fundamentals_map or {}).get(s.ticker)
            sz_chk, _ = _compute_position_size(s, f_chk)
            lines += _conviction_checklist(s, f_chk, sz_chk, macro=macro_context)

            # What would change my mind — thesis-break scenario analysis
            lines += _what_would_change(s, f_chk, macro=macro_context)

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

    # ── Per-company data completeness breakdown ───────────────────────────────
    # Helps users prioritize which entries to improve in mock_fundamentals.json.
    _KEY_FIELDS = [
        ("roic",                   "ROIC"),
        ("free_cash_flow_margin",  "FCF margin"),
        ("operating_margin",       "Op margin"),
        ("pe_ratio",               "P/E"),
        ("ev_ebitda",              "EV/EBITDA"),
        ("fcf_yield",              "FCF yield"),
        ("dod_revenue_pct",        "DoD rev %"),
        ("backlog_to_revenue",     "Backlog/Rev"),
        ("current_ratio",          "Current ratio"),
        ("earnings_stability_years", "Earn stability"),
    ]
    if ranked_scores and fundamentals_map:
        completeness_rows = []
        for s in ranked_scores:
            f_q = (fundamentals_map or {}).get(s.ticker)
            if f_q is None:
                continue
            missing = [label for field, label in _KEY_FIELDS if getattr(f_q, field, None) is None]
            grade, grade_emoji = _data_confidence_grade(s.data_completeness_pct)
            completeness_rows.append((s.ticker, s.data_completeness_pct, grade, grade_emoji, missing))

        completeness_rows.sort(key=lambda r: r[1])  # worst first

        comp_lines = [
            "### Data Completeness by Company",
            "",
            "> Companies with grade D or F should have fundamentals added to `data/mock_fundamentals.json`",
            "> before deploying capital based on their scores.",
            "",
            "| Ticker | Completeness | Grade | Missing Key Fields |",
            "|--------|:------------:|:-----:|-------------------|",
        ]
        for ticker, pct, grade, emoji, missing in completeness_rows:
            missing_str = ", ".join(missing) if missing else "—"
            comp_lines.append(f"| {ticker} | {pct:.0f}% | {emoji} {grade} | {missing_str} |")
        comp_lines.append("")
        lines += comp_lines

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
