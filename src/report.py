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


def _compute_conviction_score(
    s: CompanyScore,
    f,
    score_history: dict = None,
) -> tuple[int, str]:
    """
    Compute a 0–10 Signal Strength score that answers: "How much should I trust
    this signal before deploying capital?"

    Components:
      0–3 pts: Composite score position relative to PA+ threshold
      0–3 pts: Bear-case margin of safety (downside protection quality)
      0–2 pts: Data completeness (grade A/B vs. C/D/F)
      0–1 pt : Score stability across multiple runs
      0–1 pt : No data validation red flags

    Returns (score_int, rationale_str).
    """
    pts = 0
    parts = []

    # ── Component 1: composite score relative to PA+ threshold (68) ──────────
    fs = s.final_score
    if fs >= 75:
        pts += 3
        parts.append("score ≥75 (+3)")
    elif fs >= 68:
        pts += 2
        parts.append("score ≥68 (+2)")
    elif fs >= 63:
        pts += 1
        parts.append("score ≥63 (+1)")
    else:
        parts.append("score <63 (+0)")

    # ── Component 2: bear-case MoS ──────────────────────────────────────────
    bm = s.dcf.bear_mos if s.dcf else None
    if bm is None:
        parts.append("no bear IV (+0)")
    elif bm >= 5:
        pts += 3
        parts.append(f"bear MoS +{bm:.0f}% (+3)")
    elif bm >= 0:
        pts += 2
        parts.append(f"bear MoS {bm:.0f}% (+2)")
    elif bm >= -15:
        pts += 1
        parts.append(f"bear MoS {bm:.0f}% (+1)")
    else:
        parts.append(f"bear MoS {bm:.0f}% (+0)")

    # ── Component 3: data completeness ───────────────────────────────────────
    pct = getattr(s, "data_completeness_pct", 0.0) or 0.0
    grade, _ = _data_confidence_grade(pct)
    if grade in ("A",):
        pts += 2
        parts.append("data A (+2)")
    elif grade in ("B",):
        pts += 1
        parts.append("data B (+1)")
    else:
        parts.append(f"data {grade} (+0)")

    # ── Component 4: score stability ─────────────────────────────────────────
    hist = (score_history or {}).get(s.ticker, [])
    hist_scores = [h["score"] for h in hist if "score" in h]
    if len(hist_scores) >= 3:
        spread = max(hist_scores) - min(hist_scores)
        if spread <= 3:
            pts += 1
            parts.append(f"stable ({spread:.1f}pt spread, +1)")
        else:
            parts.append(f"volatile ({spread:.1f}pt spread, +0)")
    else:
        parts.append("history <3 runs (+0)")

    # ── Component 5: no data validation flags ─────────────────────────────────
    data_flags = [fl for fl in (s.red_flags or []) if "data check" in fl.lower()]
    if not data_flags:
        pts += 1
        parts.append("no data flags (+1)")
    else:
        parts.append(f"{len(data_flags)} data flag(s) (+0)")

    # ── Label ────────────────────────────────────────────────────────────────
    if pts >= 9:
        label = "Maximum conviction — deploy full sizing"
    elif pts >= 7:
        label = "High conviction — normal sizing"
    elif pts >= 5:
        label = "Moderate — start at 50%, watch for confirmation"
    elif pts >= 3:
        label = "Low — research priority, not yet actionable"
    else:
        label = "Insufficient — data gaps or model uncertainty too high"

    rationale = " | ".join(parts)
    return pts, label


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
    _COMP_LABELS = {"bq": "Buffett", "gv": "Graham", "ds": "DoD",
                    "mq": "Mgmt", "cc": "Catalyst", "bs": "BalSheet"}
    _COMP_WEIGHTS = {"bq": 0.25, "gv": 0.20, "ds": 0.20, "mq": 0.15, "cc": 0.10, "bs": 0.10}

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

        # Component attribution: compare stored component scores to current values
        attribution = None
        prev_comps = prev.get("components")
        if abs(score_delta) >= 3.0 and prev_comps:
            curr_comps = {
                "bq": s.buffett_quality.raw,
                "gv": s.graham_value.raw,
                "ds": s.dod_stability.raw,
                "mq": s.management.raw,
                "cc": s.contract_catalyst.raw,
                "bs": s.balance_sheet.raw,
            }
            deltas = []
            for k in _COMP_LABELS:
                old_c = prev_comps.get(k)
                new_c = curr_comps.get(k)
                if old_c is not None and new_c is not None:
                    weighted_delta = (new_c - old_c) * _COMP_WEIGHTS[k]
                    if abs(weighted_delta) >= 0.3:
                        deltas.append((k, weighted_delta))
            deltas.sort(key=lambda x: x[1])  # most negative first
            if deltas:
                top = deltas[:2] if score_delta < 0 else deltas[-2:]
                parts = []
                for k, wd in top:
                    direction = "↓" if wd < 0 else "↑"
                    parts.append(f"{_COMP_LABELS[k]} {direction}{abs(wd):.1f}pt")
                attribution = ", ".join(parts)

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
                "attribution": attribution,
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
            # Inline attribution note for large moves
            if ch.get("attribution") and abs(ch["score_delta"]) >= 3.0:
                direction = "drivers" if ch["score_delta"] > 0 else "drags"
                lines.append(f"  *Score change {direction}: {ch['attribution']}*")
        lines.append("")

    if new_entries:
        lines.append(f"**New in this run:** {', '.join(new_entries)}")
        lines.append("")
    if removed:
        if len(removed) > 6:
            # Large drop-off usually indicates source/filter switch rather than real signal.
            # Suppress the full list to avoid noise; show count only.
            lines.append(
                f"**Universe change:** {len(removed)} companies from the prior run are not in "
                "this run's contract universe — likely a source switch (live→mock or filter change)."
            )
        else:
            lines.append(f"**No longer appearing:** {', '.join(sorted(removed))}")
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
            expansion_price = _estimate_graham_expansion_price(s, f) if f else None
            if expansion_price and f and f.current_price:
                pct_rise = (expansion_price / f.current_price - 1) * 100
                scenarios.append(
                    f"📈 **Multiple expansion**: If the stock rallies to ~${expansion_price:.0f} "
                    f"(+{pct_rise:.0f}% from ${f.current_price:.0f}), the MoS compresses "
                    f"and Graham Value score drops enough to flip the verdict to Watchlist. "
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
        elif ins_pct <= -40:
            status, detail = "❌", f"Heavy insider **selling** ({ins_pct:.0f}% of held shares, 6m) — significant insider distribution; re-examine thesis"
            warnings += 2
        elif ins_pct <= -20:
            status, detail = "⚠️", f"Net insider **selling** ({ins_pct:.0f}% of held shares, 6m) — warrants scrutiny (could be planned selling, but verify)"
            warnings += 1
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


def _estimate_graham_upgrade_price(s: CompanyScore, f) -> Optional[float]:
    """
    For a Watchlist name, estimate the stock price at which its score would cross the
    PA+ threshold (68). Only Graham Value (weight 20%) is price-sensitive in the short run;
    returns the target price or None when no upgrade is achievable via price alone.
    """
    from src.scoring import score_graham_value
    import copy, dataclasses

    if f is None or f.current_price is None or f.current_price <= 0:
        return None

    score_gap = 68.0 - s.final_score
    if score_gap <= 0:
        return None

    # Graham weight is 20%: need graham_raw to increase by score_gap / 0.20
    graham_raw_needed_gain = score_gap / 0.20
    if graham_raw_needed_gain > 60:  # can't gain that much from Graham alone
        return None

    cur_price = f.current_price

    # Binary-search price: try multipliers from 0.50 to 0.99 in fine steps
    for mult_10 in range(99, 49, -1):
        mult = mult_10 / 100.0
        new_price = cur_price * mult

        # Build a modified fundamentals copy with price-adjusted multiples
        f_dict = {field: getattr(f, field, None) for field in f.__dataclass_fields__}

        # Scale P/E proportionally (EPS unchanged)
        if f.pe_ratio is not None and f.pe_ratio > 0:
            f_dict["pe_ratio"] = f.pe_ratio * mult
        # Scale Forward P/E proportionally
        if f.forward_pe is not None and f.forward_pe > 0:
            f_dict["forward_pe"] = f.forward_pe * mult
        # FCF yield increases as price drops (FCF/share unchanged)
        if f.fcf_yield is not None and f.fcf_yield > 0:
            f_dict["fcf_yield"] = f.fcf_yield / mult
        # P/B decreases proportionally (book value unchanged)
        if f.price_to_book is not None and f.price_to_book > 0:
            f_dict["price_to_book"] = f.price_to_book * mult
        # EV/EBITDA: approximate — equity portion of EV changes, debt stays
        # Use: new_ev_ebitda ≈ ev_ebitda × mult if fully equity-financed (conservative)
        if f.ev_ebitda is not None and f.ev_ebitda > 0:
            f_dict["ev_ebitda"] = f.ev_ebitda * mult

        try:
            f_copy = f.__class__(**f_dict)
            new_graham_raw, _, _ = score_graham_value(f_copy)
        except Exception:
            continue

        # Estimate new total score: replace Graham contribution
        graham_gain = new_graham_raw - s.graham_value.raw
        projected_score = s.final_score + graham_gain * 0.20

        if projected_score >= 68.0:
            return new_price

    return None


def _estimate_graham_expansion_price(s: CompanyScore, f) -> Optional[float]:
    """
    For a PA+ name, estimate the stock price at which a rally would compress
    Graham Value enough to flip the verdict below the PA+ threshold (68).

    Mirrors _estimate_graham_upgrade_price but scans upward (multiple expansion
    compresses P/E, FCF yield, P/B, EV/EBITDA) instead of downward.
    """
    from src.scoring import score_graham_value

    if f is None or f.current_price is None or f.current_price <= 0:
        return None

    score_gap = s.final_score - 68.0
    if score_gap <= 0:
        return None

    cur_price = f.current_price

    # Binary-search-style scan: try multipliers from 1.01x to 2.50x rally
    for mult_10 in range(101, 251):
        mult = mult_10 / 100.0
        new_price = cur_price * mult

        f_dict = {field: getattr(f, field, None) for field in f.__dataclass_fields__}

        if f.pe_ratio is not None and f.pe_ratio > 0:
            f_dict["pe_ratio"] = f.pe_ratio * mult
        if f.forward_pe is not None and f.forward_pe > 0:
            f_dict["forward_pe"] = f.forward_pe * mult
        if f.fcf_yield is not None and f.fcf_yield > 0:
            f_dict["fcf_yield"] = f.fcf_yield / mult
        if f.price_to_book is not None and f.price_to_book > 0:
            f_dict["price_to_book"] = f.price_to_book * mult
        if f.ev_ebitda is not None and f.ev_ebitda > 0:
            f_dict["ev_ebitda"] = f.ev_ebitda * mult

        try:
            f_copy = f.__class__(**f_dict)
            new_graham_raw, _, _ = score_graham_value(f_copy)
        except Exception:
            continue

        graham_delta = new_graham_raw - s.graham_value.raw
        projected_score = s.final_score + graham_delta * 0.20

        if projected_score < 68.0:
            return new_price

    return None


def _generate_watchlist_upgrade_targets(
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    For Watchlist names with score 58–67, estimate the stock price (via Graham Value
    sensitivity) at which their score would cross the PA+ threshold of 68.
    Answers: 'If LMT drops to $X, would it become a PA+ name?'
    Only renders when price-only upgrades are achievable; shows a note otherwise.
    """
    near_threshold = [
        s for s in ranked_scores
        if s.verdict == Verdict.WATCHLIST and 58 <= s.final_score <= 67
    ]
    if not near_threshold:
        return []

    candidates = []
    quality_bottleneck = []
    for s in near_threshold:
        f = (fundamentals_map or {}).get(s.ticker)
        if f is None or f.current_price is None:
            continue

        upgrade_price = _estimate_graham_upgrade_price(s, f)
        if upgrade_price is None:
            quality_bottleneck.append(s.ticker)
            continue

        gap_pct = (upgrade_price / f.current_price - 1) * 100  # negative = needs to fall
        # Only show if a plausible drop (10%–55%) achieves the upgrade
        if gap_pct > -10 or gap_pct < -55:
            quality_bottleneck.append(s.ticker)
            continue

        base_iv = s.dcf.base_iv if s.dcf else None
        candidates.append((s, f, upgrade_price, gap_pct, base_iv))

    lines = [
        "### 1c. Watchlist — Price-to-Upgrade Targets",
        "",
        "> At what price would these Watchlist names cross the PA+ threshold (score ≥ 68)?",
        "> Estimate uses Graham Value sensitivity to price (P/E, FCF yield, P/B, EV/EBITDA).",
        "> Quality components (Buffett, DoD Stability, Management) are assumed unchanged.",
        "",
    ]

    if candidates:
        lines += [
            "| Ticker | Now | Upgrade Price | Drop Needed | Base IV | Score Now | Est. Score at Target |",
            "|--------|----:|--------------:|:-----------:|--------:|----------:|---------------------:|",
        ]
        for s, f, upgrade_price, gap_pct, base_iv in sorted(candidates, key=lambda x: -x[3]):
            price_str   = f"${f.current_price:.0f}"
            upgrade_str = f"${upgrade_price:.0f}"
            gap_str     = f"{gap_pct:.0f}%"
            biv_str     = f"${base_iv:.0f}" if base_iv else "—"
            gap_gain    = 68.0 - s.final_score
            est_score   = f"~{s.final_score + gap_gain:.0f}"
            lines.append(
                f"| {s.ticker} | {price_str} | {upgrade_str} | {gap_str} | {biv_str} "
                f"| {s.final_score:.1f} | {est_score} |"
            )
        lines += [
            "",
            "> A name crossing PA+ requires a re-run to confirm — the upgrade price is an *estimate*, "
            "not a guaranteed trigger. Catalyst events (contract wins, earnings beats) can also promote "
            "a name via Buffett Quality or DoD Stability improvement independent of price.",
        ]

    if quality_bottleneck:
        note = (
            f"> **{', '.join(quality_bottleneck)}**: Watchlist due to quality/stability gaps — "
            "a price drop alone cannot push these to PA+. Requires DoD revenue expansion, "
            "FCF margin improvement, or a material contract win to improve non-Graham components."
        )
        lines.append(note)

    lines.append("")
    return lines


def _generate_sector_allocation(
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    Summarise the implied sector weights from the Capital Deployment PA+ sizing table.
    Flags over-concentration (>30% in one sector) and PA+ names with sizing blocked by
    overvaluation flags (size = 0% despite PA+ verdict).
    """
    from collections import defaultdict as _dd

    PA_PLUS = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}
    sector_weights: dict = _dd(float)
    sector_tickers: dict = _dd(list)
    blocked: list = []
    total_deployed = 0.0

    for s in ranked_scores:
        if s.verdict not in PA_PLUS:
            continue
        f = (fundamentals_map or {}).get(s.ticker)
        size_pct, _ = _compute_position_size(s, f)
        if size_pct == 0:
            blocked.append(s.ticker)
            continue
        sector_weights[s.sector.value] += size_pct
        sector_tickers[s.sector.value].append(s.ticker)
        total_deployed += size_pct

    if not sector_weights or total_deployed == 0:
        return []

    lines = [
        "**Implied Sector Allocation (PA+ names only):**",
        "",
        "| Sector | Weight | % of Deployed | Tickers | Note |",
        "|--------|-------:|:-------------:|---------|------|",
    ]

    for sector_name, weight in sorted(sector_weights.items(), key=lambda x: -x[1]):
        tickers_str = ", ".join(sector_tickers[sector_name])
        pct_of_deployed = weight / total_deployed * 100
        if pct_of_deployed > 30:
            note = "⚠️ concentrated"
        elif pct_of_deployed > 20:
            note = "🟡 moderate"
        else:
            note = "✅"
        lines.append(f"| {sector_name} | {weight:.1f}% | {pct_of_deployed:.0f}% | {tickers_str} | {note} |")

    if blocked:
        lines.append("")
        lines.append(f"> ⚠️ Sizing blocked (overvaluation flags active): **{', '.join(blocked)}** — "
                     "score qualifies but DCF shows stock above intrinsic value. Wait for a pullback.")

    lines += ["", ""]
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


def _generate_sector_peer_comparison(
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    For each sector with 2+ scored companies, show a side-by-side comparison of key
    valuation and quality metrics. Helps fund managers identify relative value within
    a cohort before selecting which name to own.
    """
    from collections import defaultdict as _dd

    sector_groups: dict = _dd(list)
    for s in ranked_scores:
        sector_groups[s.sector].append(s)

    multi = {k: v for k, v in sector_groups.items() if len(v) >= 2}
    if not multi:
        return []

    lines = [
        "### 1d. Sector Peer Comparison",
        "",
        "> For each sector with two or more companies in the screen, this table shows"
        " valuation and quality metrics side-by-side so you can identify the best-value"
        " name within each cohort. ⭐ = highest composite score in that sector.",
        "",
    ]

    for sector, scores in sorted(multi.items(), key=lambda x: x[0].value):
        scores_sorted = sorted(scores, key=lambda s: -s.final_score)
        best_ticker = scores_sorted[0].ticker

        lines += [
            f"**{sector.value}** ({len(scores)} companies)",
            "",
            "| | Ticker | Score | EV/EBITDA | FCF Yield | ROIC | Base IV Upside | Bear MoS | Verdict |",
            "|--|--------|------:|:---------:|:---------:|:----:|:--------------:|:--------:|---------|",
        ]

        for s in scores_sorted:
            f = (fundamentals_map or {}).get(s.ticker)
            ev   = f.ev_ebitda         if f and f.ev_ebitda   is not None else None
            fcfy = f.fcf_yield         if f and f.fcf_yield   is not None else None
            roic = f.roic              if f and f.roic         is not None else None
            base_up = s.dcf.margin_of_safety_base if s.dcf and s.dcf.margin_of_safety_base is not None else None
            bear_m  = s.dcf.bear_mos              if s.dcf and s.dcf.bear_mos  is not None else None

            ev_str   = f"{ev:.1f}x"    if ev   is not None else "—"
            fcfy_str = f"{fcfy:.1f}%"  if fcfy is not None else "—"
            roic_str = f"{roic:.1f}%"  if roic is not None else "—"
            up_str   = (f"+{base_up:.0f}% 🟢" if base_up is not None and base_up > 0
                        else (f"{base_up:.0f}%" if base_up is not None else "—"))
            bear_str = (f"🛡️ +{bear_m:.0f}%" if bear_m is not None and bear_m > 0
                        else (f"{bear_m:.0f}%" if bear_m is not None else "—"))

            star = "⭐" if s.ticker == best_ticker else ""
            vshort = {
                "Strong Candidate": "SC ✅",
                "Potentially Attractive": "PA+ 🟡",
                "Research Further": "RF 🟡",
                "Watchlist": "Watch 🔵",
                "Low Conviction": "Low ⚪",
                "Ignore": "Ignore 🔴",
                "High Quality But Expensive": "Expensive 🟠",
            }.get(s.verdict.value, s.verdict.value[:12])

            lines.append(
                f"| {star} | **{s.ticker}** | {s.final_score:.1f}"
                f" | {ev_str} | {fcfy_str} | {roic_str}"
                f" | {up_str} | {bear_str} | {vshort} |"
            )

        lines.append("")

    return lines


def _generate_tier2_entry_targets(
    ranked_scores: List[CompanyScore],
    fundamentals_map: Dict,
) -> List[str]:
    """
    For PA+ names where the bear-case DCF is negative (Tier 2: base MoS positive,
    bear MoS negative), show the specific price at which each name becomes a
    full-conviction Tier 1 buy. That price is simply the bear-case intrinsic value —
    when price = bear_iv, bear_mos = 0.

    This is the most actionable output for a patient, price-disciplined fund manager.
    """
    PA_PLUS = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}

    tier2 = [
        s for s in ranked_scores
        if s.verdict in PA_PLUS
        and s.dcf
        and s.dcf.bear_mos is not None
        and s.dcf.bear_mos < 0
        and s.dcf.bear_iv is not None
        and not any("overvalued at" in fl.lower() or "dcf:" in fl.lower()
                    for fl in (s.red_flags or []))
    ]

    if not tier2:
        return []

    lines = [
        "### 1e. Tier 2 → Full-Conviction Entry Prices",
        "",
        "> These PA+ names have a positive **base-case** margin of safety but a **negative bear-case** MoS.",
        "> The table shows the price at which each name crosses into Tier 1 (bear MoS ≥ 0).",
        "> That price equals the bear-case intrinsic value — the floor scenario already bakes in",
        "> a growth slowdown and multiple compression. Initiating at or below this price means",
        "> **even the pessimistic scenario still pays you**.",
        "",
        "| Ticker | Current Price | Bear IV (Target Entry) | Drop Needed | Base IV | Bear MoS Now | Comment |",
        "|--------|:------------:|:---------------------:|:-----------:|--------:|:------------:|---------|",
    ]

    for s in sorted(tier2, key=lambda s: -(s.dcf.bear_mos or 0)):
        f = (fundamentals_map or {}).get(s.ticker)
        cur = f.current_price if f and f.current_price else None
        bear_iv  = s.dcf.bear_iv
        base_iv  = s.dcf.base_iv
        bear_mos = s.dcf.bear_mos

        if cur and cur > 0:
            drop_pct = (bear_iv - cur) / cur * 100
            drop_str = f"{drop_pct:.0f}%"
        else:
            drop_str = "—"

        cur_str  = f"${cur:.0f}"     if cur     else "—"
        biv_str  = f"${bear_iv:.0f}" if bear_iv else "—"
        base_str = f"${base_iv:.0f}" if base_iv else "—"
        mos_str  = f"{bear_mos:.0f}%"

        if bear_mos >= -10:
            comment = "Close — minor pullback achieves Tier 1"
        elif bear_mos >= -20:
            comment = "Moderate gap — watch for market weakness"
        else:
            comment = "Wide gap — needs significant re-rating"

        lines.append(
            f"| **{s.ticker}** | {cur_str} | {biv_str} | {drop_str}"
            f" | {base_str} | {mos_str} | {comment} |"
        )

    lines += [
        "",
        "> 💡 **How to use:** Set a limit order at or near the Bear IV column price."
        " If the stock reaches that level, the bear-case scenario still yields positive returns"
        " — making it a structurally lower-risk entry than initiating at today's price.",
        "",
    ]
    return lines


def _generate_brief_report(
    ranked_scores: List[CompanyScore],
    all_contracts: List[Contract],
    run_date: str,
    fundamentals_map: Dict = None,
    macro_context = None,
    score_history: Dict = None,
    data_source_note: Optional[str] = None,
) -> str:
    """
    Condensed executive summary — 1-page PM-ready format.
    Includes: macro context, Action Summary, PA+ thesis+signal+R/R, key risks.
    Omits: DCF detail, full contract tables, Section 3-11 analysis.
    """
    run_date = run_date or datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# DoD Contract Intelligence — Executive Summary",
        f"*{run_date} | --brief mode | Full report: `python main.py` without --brief*",
        "",
    ]
    if data_source_note:
        lines += [f"> 🔶 **DATA SOURCE FALLBACK:** {data_source_note}", ""]

    # Macro context box (compact)
    if macro_context and macro_context.ten_year_yield is not None:
        rf = macro_context.ten_year_yield
        delta = rf - 4.5
        delta_str = f"{delta:+.2f}pp vs 4.5% baseline"
        direction = "above" if delta > 0 else "below"
        lines += [
            f"> **Rates:** 10-yr {rf:.2f}% ({delta_str}) — IVs are ~{abs(delta)*10:.1f}% "
            f"{'lower' if delta > 0 else 'higher'} than baseline.",
            "",
        ]

    # PA+ action table (compact)
    pa_plus = [s for s in ranked_scores if s.verdict in (
        Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER
    )]
    lines += [
        "## Actionable Names",
        "",
        "| Ticker | Score | Price | Bear MoS | Signal | Action |",
        "|--------|------:|------:|---------:|:------:|--------|",
    ]
    for s in ranked_scores:
        f_ctx = (fundamentals_map or {}).get(s.ticker)
        price_str = f"${f_ctx.current_price:.0f}" if f_ctx and f_ctx.current_price else "—"
        if s.dcf and s.dcf.bear_mos is not None:
            bm = s.dcf.bear_mos
            if bm >= 0:
                bear_str = f"🛡️ +{bm:.0f}%"
            else:
                bear_str = f"{bm:+.0f}%"
        else:
            bear_str = "—"

        signal_str = f"{s.signal_strength}/10"

        # Action label
        if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER):
            if s.dcf and s.dcf.bear_mos is not None:
                bm = s.dcf.bear_mos
                if bm >= 0:
                    action = "**BUY**"
                elif bm >= -15:
                    action = "Start 75%"
                elif bm >= -30:
                    action = "Start 50%"
                else:
                    action = "25% only"
            else:
                action = "Research"
        elif s.verdict == Verdict.WATCHLIST:
            action = "Monitor"
        elif s.verdict == Verdict.HIGH_QUALITY_BUT_EXPENSIVE:
            action = "Wait for entry"
        else:
            action = "Pass"

        lines.append(
            f"| {s.ticker} | {s.final_score:.1f} | {price_str} | {bear_str} | {signal_str} | {action} |"
        )
    lines.append("")

    # One-line thesis per PA+ name
    if pa_plus:
        lines += ["## Investment Thesis (PA+ names)", ""]
        for s in pa_plus:
            f_ctx = (fundamentals_map or {}).get(s.ticker)
            # Reproduce one-liner from deep dive
            parts = []
            if f_ctx and f_ctx.current_price and s.dcf and s.dcf.base_iv:
                cur = f_ctx.current_price
                base_iv = s.dcf.base_iv
                mos = s.dcf.margin_of_safety_base
                parts.append(f"${cur:.0f} → IV ${base_iv:.0f} ({mos:+.0f}%)")
            if f_ctx:
                moat = (getattr(f_ctx, "moat_rating", None) or "").strip()
                dod = f_ctx.dod_revenue_pct
                bl = f_ctx.backlog_to_revenue
                if moat and moat != "None" and dod:
                    bl_str = f", {bl:.1f}× BL" if bl else ""
                    parts.append(f"{moat}-moat, {dod:.0f}% DoD{bl_str}")
            if s.dcf and s.dcf.bear_mos is not None:
                bm = s.dcf.bear_mos
                parts.append(f"🛡️ Bear +{bm:.0f}%" if bm >= 0 else f"Bear {bm:.0f}%")
            if parts:
                lines.append(f"**{s.ticker}** ({s.company_name}): {' | '.join(parts)}")
                # Key risk (first red flag if any)
                if s.red_flags:
                    lines.append(f"  ⚠️ *Top risk: {s.red_flags[0][:120]}*")
                lines.append("")

    lines += [
        "---",
        f"*Full report: `python main.py` | Brief mode omits DCF detail, Sections 3-11, contract tables.*",
        "*Not investment advice. Verify all figures against source filings before acting.*",
    ]
    return "\n".join(lines)


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
    brief: bool = False,
    data_source_note: Optional[str] = None,
) -> str:
    # Store authoritative signal_strength (history-aware) on each CompanyScore so all
    # sections — including --brief mode, which returns before the full-report body
    # below — can reference s.signal_strength rather than recomputing inline or
    # silently showing the dataclass default of 0.
    for s in ranked_scores:
        f_ctx_ss = (fundamentals_map or {}).get(s.ticker)
        s.signal_strength, _ = _compute_conviction_score(s, f_ctx_ss, score_history or {})

    if brief:
        return _generate_brief_report(
            ranked_scores=ranked_scores,
            all_contracts=all_contracts,
            run_date=run_date,
            fundamentals_map=fundamentals_map,
            macro_context=macro_context,
            score_history=score_history,
            data_source_note=data_source_note,
        )
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
    _is_not_overvalued = lambda s: not any(
        "overvalued at" in f.lower() or "dcf:" in f.lower() for f in (s.red_flags or [])
    )
    # Tier 1: bear MoS >= 0 (downside protection confirmed) AND not overvalued
    _tier1_ex = [s for s in _pa_plus
                 if s.dcf and s.dcf.bear_mos is not None and s.dcf.bear_mos >= 0
                 and _is_not_overvalued(s)]
    # Tier 2: PA+ and not overvalued but bear MoS negative (tail risk present)
    _tier2_ex = [s for s in _pa_plus if s not in _tier1_ex and _is_not_overvalued(s)]
    # Blocked: PA+ verdict but overvalued by DCF
    _blocked_ex = [s for s in _pa_plus if not _is_not_overvalued(s)]

    _deploy_rows_ex = [(s, *_compute_position_size(s, (fundamentals_map or {}).get(s.ticker))) for s in ranked_scores]
    _total_pct_ex   = sum(pct for _, pct, _ in _deploy_rows_ex if pct > 0)
    _cash_pct_ex    = 100.0 - _total_pct_ex

    # ── Build enhanced executive summary ─────────────────────────────────────
    exec_lines = []

    # Macro environment note
    if macro_context and macro_context.ten_year_yield is not None:
        dcf_rf = 4.5
        delta = macro_context.ten_year_yield - dcf_rf
        if abs(delta) > 0.5:
            direction = "above" if delta > 0 else "below"
            exec_lines.append(
                f"⚡ **Rate environment:** 10-yr yield {macro_context.ten_year_yield:.2f}% "
                f"is {abs(delta):.2f}pp {direction} DCF baseline (4.50%) — "
                + ("intrinsic values are ~{:.0f}% lower than stated.".format(abs(delta) * 10)
                   if delta > 0 else "intrinsic values may be slightly optimistic.")
            )
        else:
            exec_lines.append(
                f"✅ **Rate environment:** 10-yr yield {macro_context.ten_year_yield:.2f}% "
                f"≈ DCF baseline (+{delta:+.2f}pp) — intrinsic values are valid as stated."
            )

    # PA+ landscape
    if _tier1_ex:
        shield_names = ", ".join(
            f"**{s.ticker}** ({'+' if (s.dcf.bear_mos or 0) >= 0 else ''}"
            f"{s.dcf.bear_mos:.0f}% bear MoS)"
            if s.dcf else f"**{s.ticker}**"
            for s in _tier1_ex
        )
        top = _tier1_ex[0]
        exec_lines.append(
            f"🛡️ **Highest conviction ({len(_tier1_ex)} name{'s' if len(_tier1_ex)>1 else ''}):** "
            f"{shield_names} — downside protection confirmed at current prices."
        )
        if top.dcf and top.dcf.implied_growth_rate is not None and top.dcf.base_growth is not None:
            pessimism_pp = top.dcf.implied_growth_rate - top.dcf.base_growth
            if pessimism_pp < -2:
                exec_lines.append(
                    f"  Market pricing in {top.dcf.implied_growth_rate:.0f}%/yr for **{top.ticker}** "
                    f"vs. our base case of {top.dcf.base_growth:.0f}%/yr — "
                    f"{abs(pessimism_pp):.0f}pp pessimism premium embedded in the price."
                )

    if _tier2_ex:
        tail_names = ", ".join(
            f"**{s.ticker}** (score {s.final_score:.0f}, base "
            f"+{s.dcf.margin_of_safety_base:.0f}%, bear {s.dcf.bear_mos:.0f}%)"
            if s.dcf and s.dcf.bear_mos is not None else f"**{s.ticker}** (score {s.final_score:.0f})"
            for s in _tier2_ex
        )
        exec_lines.append(
            f"🟡 **Starter positions ({len(_tier2_ex)} name{'s' if len(_tier2_ex)>1 else ''}):** "
            f"{tail_names} — good quality, size to bear-case risk."
        )

    if _blocked_ex:
        blocked_str = ", ".join(f"**{s.ticker}**" for s in _blocked_ex)
        exec_lines.append(
            f"⚠️ **Quality above price:** {blocked_str} score PA+ but DCF shows overvaluation — "
            "wait for a pullback before sizing up."
        )

    if not _pa_plus:
        exec_lines.append("**No Potentially Attractive names in this batch — hold cash and wait.**")

    # Near-threshold watchlist note
    _near_wl = [s for s in ranked_scores if s.verdict == Verdict.WATCHLIST and s.final_score >= 63]
    if _near_wl:
        near_str = ", ".join(f"**{s.ticker}** ({s.final_score:.0f})" for s in _near_wl)
        exec_lines.append(
            f"🔵 **Near-threshold Watchlist:** {near_str} — within 5 pts of PA+; "
            "monitor for contract wins or multiple compression."
        )

    # Deployable capital summary
    exec_lines.append(
        f"📊 **Deployable: {_total_pct_ex:.1f}% of portfolio** | "
        f"**Cash held: {_cash_pct_ex:.1f}%** — "
        + ("High cash is correct discipline with limited conviction names."
           if _cash_pct_ex > 80 else
           "Diversified deployment across conviction names." if _cash_pct_ex < 50 else
           "Moderate deployment; reserve cash for watchlist upgrades.")
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
    ]
    if data_source_note:
        lines += [
            f"> 🔶 **DATA SOURCE FALLBACK:** {data_source_note}",
            "",
        ]
    lines += [
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
    for part in exec_lines:
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
        "| # | Ticker | Price | Company | Sector | Score | MoS | Bear | Sig | Verdict |",
        "|---|--------|------:|---------|--------|------:|----:|-----:|----:|---------|",
    ]
    for i, s in enumerate(ranked_scores, 1):
        emoji = VERDICT_EMOJI.get(s.verdict, "⚪")
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
        sig_str = f"{s.signal_strength}/10"
        lines.append(
            f"| {i} | **{s.ticker}** | {price_str} | {s.company_name} | {s.sector.value} "
            f"| **{s.final_score:.1f}** | {mos_str} | {bear_str} | {sig_str} | {emoji} {s.verdict.value} |"
        )

    lines += [
        "",
        "† MoS suppressed for Ignore-rated companies — high MoS on a low-quality name"
        " is usually a DCF artifact (e.g. high FCF yield from non-DoD business lines)."
        " Full DCF detail in Section 2b.",
        "**Bear MoS** = bear-case DCF margin of safety. 🛡️ = positive even in the downside scenario"
        " (downside protection confirmed). Negative = thesis must be right for capital to be safe.",
        "**Sig** = Signal Strength 0–10 (score quality + bear MoS protection + data completeness +"
        " score stability). ≥7 = high conviction, 5–6 = moderate, ≤4 = research required.",
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

    # ── Portfolio scenario P&L (when portfolio_size is set) ──────────────────
    if portfolio_size and portfolio_size > 0 and deployable_rows:
        bear_total = 0.0
        base_total = 0.0
        bull_total = 0.0
        pnl_rows   = []
        for ticker, _, _, _, _, _, size_pct, cur_price in deployable_rows:
            s_pnl = next((s for s in ranked_scores if s.ticker == ticker), None)
            if not s_pnl or not s_pnl.dcf:
                continue
            dollar_alloc = portfolio_size * size_pct / 100.0
            bear_iv  = s_pnl.dcf.bear_iv
            base_iv  = s_pnl.dcf.base_iv
            bull_iv  = s_pnl.dcf.bull_iv
            bear_mos = s_pnl.dcf.bear_mos
            base_mos = s_pnl.dcf.margin_of_safety_base
            bull_mos = s_pnl.dcf.bull_mos
            if cur_price and cur_price > 0:
                bear_pnl = dollar_alloc * (bear_mos / 100.0) if bear_mos is not None else None
                base_pnl = dollar_alloc * (base_mos / 100.0) if base_mos is not None else None
                bull_pnl = dollar_alloc * (bull_mos / 100.0) if bull_mos is not None else None
                pnl_rows.append((ticker, dollar_alloc, bear_pnl, base_pnl, bull_pnl, bear_mos, base_mos, bull_mos))
                if bear_pnl is not None:
                    bear_total += bear_pnl
                if base_pnl is not None:
                    base_total += base_pnl
                if bull_pnl is not None:
                    bull_total += bull_pnl

        if pnl_rows:
            lines += [
                "**Portfolio Scenario Analysis** *(based on DCF intrinsic values)*",
                "",
                "> Shows estimated portfolio-level P&L if prices converge to DCF intrinsic values.",
                "> Not a return forecast — reflects where our model says fair value is today.",
                "",
                "| Ticker | Allocation | 🐻 Bear P&L | 📊 Base P&L | 🐂 Bull P&L |",
                "|--------|----------:|:-----------:|:-----------:|:-----------:|",
            ]
            for ticker, alloc, bear_pnl, base_pnl, bull_pnl, bm, bam, bum in pnl_rows:
                def _pnl_fmt(pnl, mos):
                    if pnl is None or mos is None:
                        return "—"
                    sign = "+" if pnl >= 0 else ""
                    return f"{sign}${pnl:,.0f} ({sign}{mos:.0f}%)"
                lines.append(
                    f"| {ticker} | ${alloc:,.0f}"
                    f" | {_pnl_fmt(bear_pnl, bm)}"
                    f" | {_pnl_fmt(base_pnl, bam)}"
                    f" | {_pnl_fmt(bull_pnl, bum)} |"
                )
            # Totals row
            base_pct_total = base_total / portfolio_size * 100
            bear_pct_total = bear_total / portfolio_size * 100
            bull_pct_total = bull_total / portfolio_size * 100
            lines.append(
                f"| **Total** | **${portfolio_size * total_pct / 100:,.0f}** (deployed)"
                f" | **{'+' if bear_total >= 0 else ''}${bear_total:,.0f} ({bear_pct_total:+.1f}% port)**"
                f" | **{'+' if base_total >= 0 else ''}${base_total:,.0f} ({base_pct_total:+.1f}% port)**"
                f" | **{'+' if bull_total >= 0 else ''}${bull_total:,.0f} ({bull_pct_total:+.1f}% port)** |"
            )
            lines += ["", ""]

    # ── Sector allocation summary (after sizing table) ────────────────────────
    lines += _generate_sector_allocation(ranked_scores, fundamentals_map or {})

    # ── 1b. PA+ Buy Priority ──────────────────────────────────────────────────
    lines += _generate_pa_buy_priority(ranked_scores, fundamentals_map or {}, macro=macro_context)

    # ── 1c. Watchlist Upgrade Targets ─────────────────────────────────────────
    lines += _generate_watchlist_upgrade_targets(ranked_scores, fundamentals_map or {})

    # ── 1d. Sector Peer Comparison ────────────────────────────────────────────
    lines += _generate_sector_peer_comparison(ranked_scores, fundamentals_map or {})

    # ── 1e. Tier 2 → Full-Conviction Entry Prices ─────────────────────────────
    lines += _generate_tier2_entry_targets(ranked_scores, fundamentals_map or {})

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

    # ── WACC sensitivity table for PA+ names ────────────────────────────────
    # Approximation: dIV/IV ≈ -TV_pct / (WACC - terminal_g) per +1% WACC
    # Terminal value = ~70% of total IV for a 10-yr DCF with 3% terminal growth.
    pa_plus_dcf = [
        s for s in ranked_scores
        if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER)
        and s.dcf and s.dcf.base_iv is not None and s.dcf.base_iv > 0
    ]
    if pa_plus_dcf:
        tg_est = 0.03
        tv_pct = 0.70

        def _wacc_adj_iv(base_iv, wacc_pct, delta_pp):
            wacc = wacc_pct / 100.0
            if wacc <= tg_est:
                return base_iv
            iv_drop = tv_pct / (wacc - tg_est) * (delta_pp / 100.0)
            return base_iv * (1 - iv_drop)

        rows = []
        for s in pa_plus_dcf:
            d = s.dcf
            f_c = (fundamentals_map or {}).get(s.ticker)
            cur = f_c.current_price if f_c else None
            if d.discount_rate_base and d.base_iv:
                iv_05  = _wacc_adj_iv(d.base_iv, d.discount_rate_base, 0.5)
                iv_10  = _wacc_adj_iv(d.base_iv, d.discount_rate_base, 1.0)
                iv_15  = _wacc_adj_iv(d.base_iv, d.discount_rate_base, 1.5)
                # Shield survives at +1pp? (bear IV at +1pp still > current price)
                if d.bear_iv is not None and cur:
                    bear_adj = _wacc_adj_iv(d.bear_iv, d.discount_rate_base, 1.0)
                    shield_str = "✅" if bear_adj > cur else "❌"
                else:
                    shield_str = "—"
                rows.append((s.ticker, d.discount_rate_base, d.base_iv, iv_05, iv_10, iv_15, shield_str))

        if rows:
            lines += [
                "",
                "**2c. WACC Sensitivity — PA+ Names**",
                "",
                "> A +1pp rise in the discount rate reduces intrinsic value by ~{:.0f}–{:.0f}% "
                "(terminal value sensitivity). Does the bear-case shield survive a rate spike?".format(
                    min(tv_pct / (r[1]/100 - tg_est) * 1 for r in rows if r[1]/100 > tg_est),
                    max(tv_pct / (r[1]/100 - tg_est) * 1 for r in rows if r[1]/100 > tg_est),
                ),
                "",
                "| Ticker | WACC | Base IV | +0.5pp | +1.0pp | +1.5pp | 🛡️ Survives +1pp? |",
                "|--------|:----:|--------:|-------:|-------:|-------:|:-----------------:|",
            ]
            for ticker, wacc, base_iv, iv05, iv10, iv15, shield in rows:
                lines.append(
                    f"| {ticker} | {wacc:.1f}% | ${base_iv:.0f} | ${iv05:.0f} | ${iv10:.0f} | ${iv15:.0f} | {shield} |"
                )
            lines.append("")

            # Terminal growth sensitivity — the other half of DCF sensitivity
            # dIV/IV ≈ (TV_pct / (WACC - tg_base)) * (dtg / WACC - tg)
            # Simplified: IV(tg) ≈ base_iv * (WACC - tg_base) / (WACC - tg_new)
            lines += [
                "**Terminal Growth Sensitivity — PA+ Names**",
                "",
                "> Terminal growth rate drives 60–80% of intrinsic value in a 10-yr DCF.",
                "> Stress-test: how does base IV change if the long-run growth assumption is wrong?",
                "> Default terminal rate: 2.5–3.5% (sector-adjusted). Sensitivity shows IV at ±0.5pp.",
                "",
                "| Ticker | Term Rate | Base IV | −0.5pp TG | −1.0pp TG | +0.5pp TG |",
                "|--------|:---------:|--------:|----------:|----------:|----------:|",
            ]
            for s_tg in pa_plus_dcf:
                d = s_tg.dcf
                if not d.discount_rate_base or not d.base_iv:
                    continue
                wacc = d.discount_rate_base / 100.0
                tg_base = 0.03  # default; in practice sector-specific but we use 3% for sensitivity
                def _tg_adj_iv(base_iv, wacc_f, tg_b, dtg):
                    new_tg = tg_b + dtg
                    if wacc_f <= new_tg or wacc_f <= tg_b:
                        return base_iv
                    # Gordon growth terminal value adjustment: TV ∝ 1/(WACC - tg)
                    tv_frac = tv_pct  # same 70% TV fraction
                    non_tv = (1 - tv_frac)
                    # Scale only the terminal value portion
                    tv_base = base_iv * tv_frac
                    tv_new  = tv_base * (wacc_f - tg_b) / (wacc_f - new_tg)
                    return base_iv * non_tv + tv_new
                iv_m05 = _tg_adj_iv(d.base_iv, wacc, tg_base, -0.005)
                iv_m10 = _tg_adj_iv(d.base_iv, wacc, tg_base, -0.010)
                iv_p05 = _tg_adj_iv(d.base_iv, wacc, tg_base, +0.005)
                lines.append(
                    f"| {s_tg.ticker} | {tg_base*100:.1f}% | ${d.base_iv:.0f} "
                    f"| ${iv_m05:.0f} | ${iv_m10:.0f} | ${iv_p05:.0f} |"
                )
            lines.append("")
            lines.append(
                "> **Reading:** If the long-run industry growth rate settles 1pp lower than assumed, "
                "intrinsic value falls proportionally to how much WACC − TG compresses. "
                "Names with WACC close to TG are most sensitive. Use reverse DCF "
                "(Section 2b) to check what growth rate the current price already implies."
            )
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
    import datetime as _dt_mod
    _today = _dt_mod.date.today()
    _fy_start_month = 10  # DoD FY starts Oct 1
    _fy_start = (
        _dt_mod.date(_today.year, _fy_start_month, 1) if _today.month >= _fy_start_month
        else _dt_mod.date(_today.year - 1, _fy_start_month, 1)
    )
    _days_elapsed = (_today - _fy_start).days or 1
    _annualize = 365.0 / _days_elapsed  # scale YTD contracts to annual run rate

    lines += [
        "## 6. Government Funding Durability",
        "",
        f"> **YTD contract velocity** ({_fy_start} → {_today}): new FY contract awards captured in "
        "the 1,000-contract sample vs. historical DoD revenue run-rate. 📈 accelerating / "
        "➡️ on-track (±15%) / 📉 below run-rate. **Note:** Large primes (GD, LMT, NOC) have "
        "hundreds of contracts/yr — the 1,000-award sample captures only their largest awards, "
        "understating their true activity. This metric is most reliable for specialist/mid-cap names.",
        "",
        "| Ticker | DoD Rev% | Gov Rev% | Backlog/Rev | Moat | Sole Source | YTD Contracts | Velocity | DoD Stability |",
        "|--------|:--------:|:--------:|:-----------:|:----:|:-----------:|:-------------:|:--------:|:-------------:|",
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

        # YTD contract velocity — contract_value is already in millions
        ytd_total_m = sum(
            getattr(c, "contract_value", 0) or 0 for c in s.recent_contracts
        )
        ytd_str = _fmt_millions(ytd_total_m) if ytd_total_m > 0 else "—"

        velocity_str = "—"
        if (ytd_total_m > 0
                and f.annual_revenue_millions is not None and f.annual_revenue_millions > 0
                and f.dod_revenue_pct is not None):
            historical_dod_rev_m = f.annual_revenue_millions * f.dod_revenue_pct / 100.0
            annual_run_rate_m = ytd_total_m * _annualize
            ratio = annual_run_rate_m / historical_dod_rev_m
            if ratio >= 1.15:
                velocity_str = f"📈 {ratio:.1f}×"
            elif ratio >= 0.85:
                velocity_str = f"➡️ {ratio:.1f}×"
            else:
                velocity_str = f"📉 {ratio:.1f}×"

        lines.append(
            f"| {s.ticker} | {dod_pct} | {gov_pct} | {bl} | {f.moat_rating or '—'} "
            f"| {ss} | {ytd_str} | {velocity_str} | {s.dod_stability.raw:.0f} |"
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
        ]

        # ── One-Line Thesis (PA+ only) ─────────────────────────────────────────
        PA_PLUS_V2 = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}
        if s.verdict in PA_PLUS_V2 and f_ctx and f_ctx.current_price and s.dcf:
            d = s.dcf
            cur = f_ctx.current_price
            moat = (getattr(f_ctx, "moat_rating", None) or "").strip()
            moat_str = f"{moat}-moat" if moat and moat != "None" else "No-moat"
            dod_pct  = f_ctx.dod_revenue_pct
            bl       = f_ctx.backlog_to_revenue
            base_mos = d.margin_of_safety_base
            bear_mos = d.bear_mos
            base_iv  = d.base_iv

            # Build thesis components
            parts = []
            if base_iv and base_mos is not None:
                parts.append(f"${cur:.0f} → base IV ${base_iv:.0f} ({base_mos:+.0f}% upside)")
            if moat_str and dod_pct is not None:
                bl_str = f", {bl:.1f}× backlog" if bl else ""
                parts.append(f"{moat_str}, {dod_pct:.0f}% DoD revenue{bl_str}")
            if bear_mos is not None:
                if bear_mos > 0:
                    parts.append(f"🛡️ Bear case confirms MoS (+{bear_mos:.0f}%)")
                elif bear_mos >= -15:
                    parts.append(f"Bear risk modest ({bear_mos:.0f}%)")
                else:
                    parts.append(f"Bear downside {bear_mos:.0f}% — size carefully")

            # Return range (3-yr annualized)
            def _ann3(iv):
                if iv and cur > 0:
                    div = (f_ctx.dividend_yield or 0.0) / 100.0
                    return ((iv / cur) ** (1/3) - 1 + div) * 100
                return None
            ann_bear = _ann3(d.bear_iv)
            ann_bull = _ann3(d.bull_iv)
            if ann_bear is not None and ann_bull is not None:
                parts.append(f"3-yr return: {ann_bear:+.0f}% to {ann_bull:+.0f}%/yr")

            if parts:
                lines.append(f"**Thesis:** {' | '.join(parts)}.")
                lines.append("")

            # Revenue visibility from backlog
            bl = f_ctx.backlog_to_revenue if f_ctx else None
            if bl is not None and bl > 0:
                if bl >= 3.0:
                    vis_label = f"✅ {bl:.1f}× backlog — {bl:.1f}+ years of forward revenue locked in. Exceptional visibility."
                elif bl >= 2.0:
                    vis_label = f"✅ {bl:.1f}× backlog — ~{bl:.1f} years of revenue visibility. Strong."
                elif bl >= 1.0:
                    vis_label = f"🟡 {bl:.1f}× backlog — roughly one year of revenue in the pipeline."
                else:
                    vis_label = f"⚠️ {bl:.1f}× backlog — sub-1× coverage. Monitor book-to-bill for pipeline erosion."
                lines.append(f"> **Revenue Visibility:** {vis_label}")
                lines.append("")

        # ── Signal Strength (conviction score) — PA+ only ─────────────────────
        if s.verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER):
            conv_pts, conv_label = _compute_conviction_score(s, f_ctx, score_history)
            filled = "●" * conv_pts
            empty  = "○" * (10 - conv_pts)
            lines.append(
                f"**Signal Strength: {conv_pts}/10** {filled}{empty}  "
                f"*{conv_label}*"
            )
            lines.append("")

        lines += [
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

        # Management capital allocation summary — for PA+ names with sufficient data
        _PA_PLUS_VERDICTS = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}
        if s.verdict in _PA_PLUS_VERDICTS and f_ctx:
            mgmt_lines = []
            sc = getattr(f_ctx, "shares_chg_1yr_pct", None)
            ins = getattr(f_ctx, "insider_ownership_pct", None)
            roic = getattr(f_ctx, "roic", None)
            if sc is not None:
                if sc <= -3:
                    mgmt_lines.append(f"**Share count:** {sc:+.1f}%/yr — active buyback program, returning capital to shareholders ✅")
                elif sc <= -1:
                    mgmt_lines.append(f"**Share count:** {sc:+.1f}%/yr — modest buyback pace")
                elif sc >= 5:
                    mgmt_lines.append(f"**Share count:** {sc:+.1f}%/yr — significant dilution ⚠️ (equity offerings or SBC)")
                elif sc >= 2:
                    mgmt_lines.append(f"**Share count:** {sc:+.1f}%/yr — mild dilution (SBC)")
                else:
                    mgmt_lines.append(f"**Share count:** {sc:+.1f}%/yr — roughly stable")
            if ins is not None:
                if ins >= 10:
                    mgmt_lines.append(f"**Insider ownership:** {ins:.1f}% — founder/key executive alignment is high ✅")
                elif ins >= 3:
                    mgmt_lines.append(f"**Insider ownership:** {ins:.1f}% — meaningful skin in the game")
                else:
                    mgmt_lines.append(f"**Insider ownership:** {ins:.1f}% — low; management incentive is primarily option/bonus-based")
            if roic is not None:
                if roic >= 20:
                    mgmt_lines.append(f"**ROIC:** {roic:.1f}% — exceptional capital allocation; reinvestment generates strong returns ✅")
                elif roic >= 15:
                    mgmt_lines.append(f"**ROIC:** {roic:.1f}% — above cost of capital; management creates value when reinvesting")
                elif roic >= 10:
                    mgmt_lines.append(f"**ROIC:** {roic:.1f}% — adequate but not exceptional")
                else:
                    mgmt_lines.append(f"**ROIC:** {roic:.1f}% — below typical cost of capital; growth may not add value ⚠️")
            if mgmt_lines:
                lines.append("**Capital Allocation Quality:**")
                for ml in mgmt_lines:
                    lines.append(f"- {ml}")
                lines.append("")

        # DCF deep dive
        if s.dcf:
            d = s.dcf
            # EVA spread — only when ROIC is available
            eva_line = ""
            if f_ctx:
                roic = getattr(f_ctx, "roic", None)
                if roic is not None and roic != 0 and d.discount_rate_base is not None:
                    eva_spread = roic - d.discount_rate_base
                    if eva_spread > 5:
                        eva_line = f"✅ EVA spread: ROIC {roic:.1f}% − WACC {d.discount_rate_base:.1f}% = **+{eva_spread:.1f}pp** — company creates significant economic value when it reinvests."
                    elif eva_spread > 0:
                        eva_line = f"✅ EVA spread: ROIC {roic:.1f}% − WACC {d.discount_rate_base:.1f}% = **+{eva_spread:.1f}pp** — reinvestment creates value (positive spread)."
                    elif eva_spread > -3:
                        eva_line = f"⚠️ EVA spread: ROIC {roic:.1f}% − WACC {d.discount_rate_base:.1f}% = **{eva_spread:.1f}pp** — near breakeven; growth adds modest value."
                    else:
                        eva_line = f"❌ EVA spread: ROIC {roic:.1f}% − WACC {d.discount_rate_base:.1f}% = **{eva_spread:.1f}pp** — growth destroys value at current returns; watch for ROIC improvement."

            lines += [
                "#### DCF Detail",
                "",
                f"*Discount rate: **{d.discount_rate_base:.1f}%***",
            ]
            if eva_line:
                lines.append(f"*{eva_line}*")
                lines.append("")
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

            # ── Expected Return analysis (PA+ names only) ─────────────────────
            PA_PLUS_V = {Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE, Verdict.RESEARCH_FURTHER}
            if s.verdict in PA_PLUS_V and f_ctx and f_ctx.current_price and f_ctx.current_price > 0:
                cur = f_ctx.current_price
                div_yield = (f_ctx.dividend_yield or 0.0) / 100.0

                def _ann_return(iv, yrs=3):
                    if iv is None or iv <= 0 or cur <= 0:
                        return None
                    price_return = (iv / cur) ** (1.0 / yrs) - 1.0
                    return (price_return + div_yield) * 100.0

                ann_bear = _ann_return(d.bear_iv)
                ann_base = _ann_return(d.base_iv)
                ann_bull = _ann_return(d.bull_iv)

                lines += ["#### Expected Return (3-Year Horizon)", ""]
                lines.append(
                    "> Assumes price converges to DCF intrinsic value over 3 years. "
                    "Includes dividend yield. **Not a return guarantee — just the implied "
                    "math if our DCF is right.**"
                )
                lines.append("")
                lines += [
                    "| Scenario | Target IV | 3-Yr Ann. Return | Verdict |",
                    "|----------|----------:|:----------------:|---------|",
                ]
                for label, iv, ann in [
                    ("🐻 Bear", d.bear_iv, ann_bear),
                    ("📊 Base", d.base_iv, ann_base),
                    ("🐂 Bull", d.bull_iv, ann_bull),
                ]:
                    iv_str  = f"${iv:.0f}" if iv is not None else "—"
                    ann_str = f"{ann:+.1f}%/yr" if ann is not None else "—"
                    if ann is None:
                        verdict_str = "—"
                    elif ann >= 15:
                        verdict_str = "Exceptional"
                    elif ann >= 10:
                        verdict_str = "Attractive"
                    elif ann >= 5:
                        verdict_str = "Adequate"
                    elif ann >= 0:
                        verdict_str = "Thin"
                    else:
                        verdict_str = "⚠️ Negative — thesis must hold"
                    lines.append(f"| {label} | {iv_str} | {ann_str} | {verdict_str} |")

                lines.append("")

                # Break-even note for names where bear case is negative return
                if ann_bear is not None and ann_bear < 0 and d.bear_iv is not None:
                    # How many years to break even in bear case?
                    if d.bear_iv > 0 and cur > 0 and d.bear_iv < cur:
                        import math as _math
                        with_div = True
                        # price_return × yrs ≈ ann years; solve (bear_iv/cur)^(1/yrs) × (1+div)^yrs = 1
                        # Simplified: just find yrs where (bear_iv/cur)^(1/yrs) + div_yield ≥ 0
                        for yrs in range(4, 16):
                            if _ann_return(d.bear_iv, yrs) is not None and _ann_return(d.bear_iv, yrs) >= 0:
                                lines.append(
                                    f"> ⚠️ Bear case break-even: ~{yrs} years to recover cost "
                                    f"(bear IV ${d.bear_iv:.0f} vs. current ${cur:.0f}). "
                                    "Sizing discipline critical — avoid full position in bear scenario."
                                )
                                lines.append("")
                                break

                # ── Risk/Reward ratio ──────────────────────────────────────────
                if d.margin_of_safety_base is not None and d.bear_mos is not None:
                    base_up = d.margin_of_safety_base
                    bear_dn = d.bear_mos
                    if base_up > 0:
                        if bear_dn >= 0:
                            rr_str   = "∞"
                            rr_label = "★★★ Asymmetric — even the pessimistic scenario pays you"
                        else:
                            rr = base_up / abs(bear_dn)
                            rr_str   = f"{rr:.1f}:1"
                            if rr >= 3.0:
                                rr_label = "★★★ Excellent — upside dwarfs tail risk"
                            elif rr >= 2.0:
                                rr_label = "★★ Good — base upside well in excess of bear risk"
                            elif rr >= 1.5:
                                rr_label = "★ Adequate — manageable downside"
                            else:
                                rr_label = "⚠️ Unfavorable — bear risk rivals base upside; size conservatively"
                        lines.append(
                            f"> **Risk/Reward:** Base upside **+{base_up:.0f}%** vs. bear downside"
                            f" **{bear_dn:+.0f}%** → R/R **{rr_str}** — {rr_label}"
                        )
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

        # Contract quality scorecard
        if s.recent_contracts:
            n = len(s.recent_contracts)
            n_ss    = sum(1 for c in s.recent_contracts if c.is_sole_source)
            n_idiq  = sum(1 for c in s.recent_contracts if c.is_idiq)
            avg_val = (sum(c.contract_value for c in s.recent_contracts) / n) if n else 0
            ss_pct  = n_ss / n * 100 if n else 0

            # Pricing type breakdown (uses new pricing_type field from USAspending codes)
            n_fp  = sum(1 for c in s.recent_contracts if c.pricing_type == "Fixed-Price")
            n_cp  = sum(1 for c in s.recent_contracts if c.pricing_type == "Cost-Plus")
            n_tm  = sum(1 for c in s.recent_contracts if c.pricing_type == "T&M")
            n_known = n_fp + n_cp + n_tm

            ss_note = (
                f"**{ss_pct:.0f}% sole-source** ({n_ss}/{n})" if ss_pct >= 50
                else f"{ss_pct:.0f}% sole-source ({n_ss}/{n})"
            )
            if n_known > 0:
                fp_pct = n_fp / n_known * 100
                cp_pct = n_cp / n_known * 100
                pricing_note = f"pricing mix: {fp_pct:.0f}% fixed-price / {cp_pct:.0f}% cost-plus"
                if n_tm > 0:
                    pricing_note += f" / {n_tm/n_known*100:.0f}% T&M"
            else:
                pricing_note = "pricing type: not available in this sample"
            idiq_note = f"{n_idiq}/{n} IDIQ" if n_idiq > 0 else ""
            avg_note = f"avg {_fmt_millions(avg_val)} per award"

            quality_parts = [ss_note, pricing_note, avg_note]
            if idiq_note:
                quality_parts.append(idiq_note)

            lines += [
                "#### Contract Quality Scorecard",
                "",
                f"> {n} contract{'s' if n != 1 else ''} in sample — {' | '.join(quality_parts)}",
            ]
            if ss_pct >= 60:
                lines.append(
                    "> ✅ High sole-source rate — strong competitive moat in this customer relationship."
                )
            elif ss_pct < 20 and n >= 3:
                lines.append(
                    "> ⚠️ Low sole-source rate — mostly competitive awards; contract renewal is not guaranteed."
                )
            if n_known > 0:
                fp_pct = n_fp / n_known * 100
                cp_pct = n_cp / n_known * 100
                if cp_pct >= 70:
                    lines.append(
                        f"> ✅ High cost-plus rate ({cp_pct:.0f}%) — government reimburses costs plus fixed fee. "
                        "Revenue and margin are highly predictable; company bears minimal execution risk."
                    )
                elif fp_pct >= 70:
                    lines.append(
                        f"> ⚠️ High fixed-price rate ({fp_pct:.0f}%) — company bears cost overrun risk. "
                        "Strong margin when execution is on-track; verify no active cost-overrun programs "
                        "in the latest 10-K (see Section 3 Red Flags for program concentration signals)."
                    )
                elif fp_pct >= 40:
                    lines.append(
                        f"> Revenue quality: mixed pricing — {fp_pct:.0f}% fixed-price, {cp_pct:.0f}% cost-plus. "
                        "Balanced margin predictability. Review which programs are fixed-price before sizing."
                    )
            if n_idiq > 0:
                idiq_pct = n_idiq / n * 100
                lines.append(
                    f"> ⚠️ {idiq_pct:.0f}% of contracts are IDIQ (ceiling only) — "
                    "actual funded value may be significantly lower than headline amounts."
                )
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

    # ── Score stability across runs ───────────────────────────────────────────
    # Multi-run consistency is the most under-rated signal for fund conviction.
    # A company that scores 71 every run is much more investable than one that
    # swings 58–79. Stability = score range ≤ 4 pts over ≥ 3 runs.
    if score_history:
        import statistics as _st_stats
        stability_rows = []
        for s in ranked_scores:
            hist = score_history.get(s.ticker, [])
            if len(hist) < 2:
                continue
            scores_h = [h["score"] for h in hist if "score" in h]
            if len(scores_h) < 2:
                continue
            score_min = min(scores_h)
            score_max = max(scores_h)
            spread    = score_max - score_min
            n_runs    = len(scores_h)
            trend_scores = scores_h[-3:]  # last 3 runs
            if len(trend_scores) >= 2:
                trend_delta = trend_scores[-1] - trend_scores[0]
                if trend_delta >= 1.5:
                    trend_str = f"▲ +{trend_delta:.1f}"
                elif trend_delta <= -1.5:
                    trend_str = f"▼ {trend_delta:.1f}"
                else:
                    trend_str = "→ Stable"
            else:
                trend_str = "—"
            if spread <= 2:
                stability = "✅ High"
            elif spread <= 5:
                stability = "🟡 Moderate"
            elif spread <= 10:
                stability = "⚠️ Low"
            else:
                stability = "❌ Very Low"
            stability_rows.append((s.ticker, n_runs, score_min, score_max, spread, trend_str, stability))

        if stability_rows:
            stability_rows.sort(key=lambda r: r[4], reverse=True)
            lines += [
                "### Score Stability (Multi-Run History)",
                "",
                "> Score consistency across runs builds fund conviction. A narrow range"
                " (≤2 pts) means the signal is robust to data timing. A wide range (>10 pts)"
                " means data gaps or contract timing cause score volatility — treat with caution.",
                "",
                "| Ticker | Runs | Score Range | Spread | Trend (last 3) | Stability |",
                "|--------|:----:|:-----------:|:------:|:--------------:|-----------|",
            ]
            for ticker, n, lo, hi, spread, trend, stab in stability_rows:
                lines.append(
                    f"| {ticker} | {n} | {lo:.1f}–{hi:.1f} | {spread:.1f} pts | {trend} | {stab} |"
                )
            lines.append("")

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
        "### Methodology Transparency",
        "",
        "> **How the scoring weights were determined:**",
        "> The 25/20/20/15/10/10 weights are judgment-calibrated, not regression-fitted.",
        "> Buffett Quality (25%) and DoD Stability (20%) are co-equal dominant signals because",
        "> defense investing is fundamentally about durable business quality married to government",
        "> contract durability — neither alone is sufficient. Graham Value (20%) is third because",
        "> we are value-investors: quality at a fair price beats quality at any price.",
        "> Management (15%) reflects the importance of capital allocation over full cycles.",
        "> Catalyst (10%) and Balance Sheet (10%) are tiebreakers — important but secondary.",
        "",
        "> **What this means for investment decisions:**",
        "> Treat scores as a *relative ranking* within the defense universe, not an absolute signal.",
        "> A score of 72 vs. 68 is not a precise statement; it means the 72 name has stronger",
        "> fundamentals across these six dimensions. The DCF margin of safety is the primary",
        "> sizing signal — scores determine which names to model; MoS determines position size.",
        "",
        "> **Known calibration gaps:**",
        "> (1) Weights have not been backtested against realized returns. In a universe of ~35",
        "> liquid defense names, statistically valid backtesting requires >10 years of data",
        "> and careful control for market beta — this has not been done.",
        "> (2) The Buffett Quality component borrows from consumer/tech compounder frameworks",
        "> (ROIC, FCF margin). Defense companies structurally have lower ROIC than consumer",
        "> compounders (cost-plus contracts cap returns by regulation). The brackets have been",
        "> adjusted down 3–5 pts, but the calibration is subjective.",
        "> (3) Black swan risk (program cancellation, sequestration, geopolitical pivot) is not",
        "> quantified in any score component. The bear-case DCF is the closest proxy.",
        "",
        "> **How to improve confidence before fund deployment:**",
        "> (1) Run ≥5 live runs and verify score stability (Section 11 table). Names with spread",
        "> ≤2 pts have robust signals; names with >5 pts spread need more data.",
        "> (2) Verify all PA+ names' DoD%, backlog, and moat rating against the latest 10-K.",
        "> (3) For any position >2% of fund NAV, supplement with a full bottoms-up model.",
        "> (4) Apply the Pre-Deployment Conviction Checklist (Section 7) before executing.",
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
