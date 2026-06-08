"""
DCF / Owner Earnings Valuation Module

Implements a Buffett-style owner earnings DCF with:
  - 3 scenarios: bear / base / bull
  - Government revenue durability adjustment to discount rate
  - Intrinsic value range + margin of safety vs current price
  - Implied growth rate reverse-DCF (what is the market pricing in?)
  - Plain-English verdict

Owner Earnings (Buffett definition):
  Net Income
  + Depreciation & Amortization
  - Maintenance Capex (estimated as % of D&A or revenue)
  = Owner Earnings

We use FCF as a proxy when owner earnings can't be derived directly.

Discount rate:
  Base WACC = 9%  (reasonable for US defense/gov services)
  Adjustments:
    - High DoD concentration + sole-source → -1.0% (more durable cash flows)
    - Wide moat → -0.5%
    - Narrow moat → 0%
    - No moat → +1.0%
    - High leverage (D/E > 1.5) → +0.5%
    - Small cap (<$2B) → +1.5%
    - Unprofitable → +2.0%
    - Government revenue <20% → +0.5% (less durable for this thesis)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from src.models import CompanyFundamentals, Sector


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    label: str                      # "Bear" / "Base" / "Bull"
    revenue_growth_yr1_5: float     # % annual growth, years 1-5
    revenue_growth_yr6_10: float    # % annual growth, years 6-10
    terminal_growth: float          # % perpetual growth after year 10
    fcf_margin: float               # % FCF margin assumed
    discount_rate: float            # % WACC used
    intrinsic_value_per_share: Optional[float]
    intrinsic_value_total_millions: Optional[float]
    margin_of_safety_pct: Optional[float]  # positive = undervalued
    notes: str = ""


@dataclass
class DCFResult:
    ticker: str
    company_name: str
    current_price: Optional[float]
    shares_outstanding_millions: Optional[float]
    market_cap_millions: Optional[float]
    base_owner_earnings_millions: Optional[float]
    discount_rate_base: float
    discount_rate_adjustments: List[str]

    bear: Optional[ScenarioResult] = None
    base: Optional[ScenarioResult] = None
    bull: Optional[ScenarioResult] = None

    implied_growth_rate: Optional[float] = None   # reverse DCF
    fair_value_range_low: Optional[float] = None  # bear intrinsic value
    fair_value_range_high: Optional[float] = None # bull intrinsic value
    central_estimate: Optional[float] = None      # base intrinsic value

    verdict: str = ""
    margin_of_safety_base: Optional[float] = None
    valuation_note: str = ""
    data_quality: str = ""   # "good" / "partial" / "insufficient"
    caveats: List[str] = field(default_factory=list)

    # Score contribution (0-100) for integration with main scoring engine
    valuation_score: float = 50.0


# ── Main entry point ──────────────────────────────────────────────────────────

def run_dcf(
    f: CompanyFundamentals,
    sector: Sector,
    contracts_sole_source: bool = False,
    current_price: Optional[float] = None,
    shares_millions: Optional[float] = None,
) -> DCFResult:
    """
    Run a full 3-scenario DCF and return a DCFResult.

    If current_price is None, skips margin-of-safety calculation but
    still produces intrinsic value estimates and implied growth.
    """
    ticker = f.ticker
    name   = f.company_name or ticker

    caveats = []
    data_quality = _assess_data_quality(f, caveats)

    # ── Base owner earnings ───────────────────────────────────────────────────
    oe_millions, oe_notes = _owner_earnings(f, caveats)

    # ── Discount rate ─────────────────────────────────────────────────────────
    wacc, rate_adjustments = _discount_rate(f, sector, contracts_sole_source)

    # ── Growth assumptions by sector + quality ────────────────────────────────
    bear_g1, bear_g2, base_g1, base_g2, bull_g1, bull_g2 = _growth_assumptions(f, sector)
    terminal_g = _terminal_growth(sector, f)

    # Note when growth is anchored to actual company revenue data
    actual_growth = f.revenue_growth_1yr
    if actual_growth is not None and -10 < actual_growth < 60:
        caveats.append(
            f"Growth anchored to actual revenue growth ({actual_growth:+.1f}% YoY) blended with "
            f"sector defaults — bear/base/bull yr1–5: {bear_g1:.0f}%/{base_g1:.0f}%/{bull_g1:.0f}%."
        )

    # ── Engineering/construction sector caveat ────────────────────────────────
    # FCF-based DCF systematically understates intrinsic value for project-based
    # engineering firms (KBR, AECOM, Fluor). Working capital cycles, contract billing
    # timing (front-loaded invoicing, retainage holdbacks), and lump-sum fixed-price
    # contract risk all depress reported FCF vs normalized earning power.
    # Sector EV/EBITDA multiples (8–12x) are a more reliable primary signal.
    if sector == Sector.INFRASTRUCTURE_CONSTRUCTION:
        _fcf_m = f.free_cash_flow_margin or 0
        if 0 <= _fcf_m < 6.0:
            _ev_note = (f" Cross-reference: current EV/EBITDA {f.ev_ebitda:.1f}x vs sector 8–12x median."
                        if f.ev_ebitda else "")
            caveats.append(
                f"Engineering/construction company with thin FCF margin (~{_fcf_m:.1f}%). "
                "FCF-based DCF likely understates intrinsic value for this sector — "
                "use EV/EBITDA as the primary valuation anchor, not DCF MoS." + _ev_note
            )

    # ── FCF margin scenarios ──────────────────────────────────────────────────
    fcf_base = _safe(f.free_cash_flow_margin, 8.0)
    if fcf_base >= 0:
        # Profitable: bear = 25% margin compression, bull = 25% expansion
        fcf_bear = max(fcf_base * 0.75, 2.0)
        fcf_bull = min(fcf_base * 1.25, 35.0)
    else:
        # Unprofitable: must NOT invert ordering.
        # max(negative * 0.75, 2.0) would give bear a *positive* FCF floor while
        # min(negative * 1.25, 35.0) gives bull an *even more negative* margin —
        # completely backwards. Instead: bear = losses deepen, bull = losses narrow.
        fcf_bear = fcf_base * 1.25   # 25% worse (more negative)
        fcf_bull = fcf_base * 0.50   # 50% improvement (less negative, toward breakeven)

    # ── Shares + price ────────────────────────────────────────────────────────
    mc = f.market_cap_millions
    if shares_millions is None and mc and current_price and current_price > 0:
        shares_millions = mc / current_price
    if current_price is None and mc and shares_millions and shares_millions > 0:
        current_price = mc / shares_millions  # both in millions → ratio is $/share

    rev = f.annual_revenue_millions or 0

    # ── Run scenarios ─────────────────────────────────────────────────────────
    scenarios = []
    for label, g1, g2, fcf_m in [
        ("Bear", bear_g1, bear_g2, fcf_bear),
        ("Base", base_g1, base_g2, fcf_base),
        ("Bull", bull_g1, bull_g2, fcf_bull),
    ]:
        iv_total, iv_per_share, mos = _dcf_calc(
            base_earnings=oe_millions,
            base_revenue=rev,
            growth_yr1_5=g1,
            growth_yr6_10=g2,
            terminal_growth=terminal_g,
            fcf_margin=fcf_m,
            discount_rate=wacc,
            shares_millions=shares_millions,
            current_price=current_price,
            use_revenue_based=(oe_millions is None),
        )

        note_parts = [oe_notes] if oe_notes else []
        if oe_millions is None:
            note_parts.append("Revenue-based FCF projection used (owner earnings unavailable).")

        scenarios.append(ScenarioResult(
            label=label,
            revenue_growth_yr1_5=g1,
            revenue_growth_yr6_10=g2,
            terminal_growth=terminal_g,
            fcf_margin=fcf_m,
            discount_rate=wacc,
            intrinsic_value_per_share=iv_per_share,
            intrinsic_value_total_millions=iv_total,
            margin_of_safety_pct=mos,
            notes=" ".join(note_parts),
        ))

    bear_s, base_s, bull_s = scenarios

    # ── EV → Equity Value adjustment ─────────────────────────────────────────
    # _dcf_calc discounts free cash flows to the firm → Enterprise Value.
    # Equity Value = EV − Net Debt. Per-share figures must reflect this.
    # Net cash (negative net_debt) increases equity value above EV.
    net_debt = f.net_debt_millions or 0
    if net_debt != 0 and shares_millions and shares_millions > 0:
        adj_per_share = net_debt / shares_millions  # positive = reduces equity IV
        for s in [bear_s, base_s, bull_s]:
            if s.intrinsic_value_per_share is not None:
                s.intrinsic_value_per_share = round(
                    s.intrinsic_value_per_share - adj_per_share, 2
                )
                if current_price and current_price > 0 and s.intrinsic_value_per_share >= 0:
                    s.margin_of_safety_pct = round(
                        (s.intrinsic_value_per_share - current_price) / current_price * 100, 1
                    )
                elif s.intrinsic_value_per_share < 0:
                    s.margin_of_safety_pct = None  # negative IV — MoS undefined
        label = f"${abs(net_debt):.0f}M net {'debt' if net_debt > 0 else 'cash'}"
        caveats.append(
            f"{label} deducted from EV to compute equity intrinsic value per share."
        )

    # ── Implied growth rate (reverse DCF) ────────────────────────────────────
    implied_g = None
    if current_price and oe_millions and oe_millions > 0 and shares_millions:
        implied_g = _reverse_dcf(
            current_price=current_price,
            base_earnings=oe_millions,
            shares_millions=shares_millions,
            discount_rate=wacc,
            terminal_growth=terminal_g,
            fcf_margin=fcf_base,
            net_debt_millions=net_debt,
        )

    # ── Verdict ───────────────────────────────────────────────────────────────
    mos_base = base_s.margin_of_safety_pct
    verdict, val_note, val_score = _verdict(mos_base, base_s, bear_s, bull_s, implied_g, f, caveats)

    return DCFResult(
        ticker=ticker,
        company_name=name,
        current_price=current_price,
        shares_outstanding_millions=shares_millions,
        market_cap_millions=mc,
        base_owner_earnings_millions=oe_millions,
        discount_rate_base=wacc,
        discount_rate_adjustments=rate_adjustments,
        bear=bear_s,
        base=base_s,
        bull=bull_s,
        implied_growth_rate=implied_g,
        fair_value_range_low=bear_s.intrinsic_value_per_share,
        fair_value_range_high=bull_s.intrinsic_value_per_share,
        central_estimate=base_s.intrinsic_value_per_share,
        verdict=verdict,
        margin_of_safety_base=mos_base,
        valuation_note=val_note,
        data_quality=data_quality,
        caveats=caveats,
        valuation_score=val_score,
    )


# ── Owner earnings ────────────────────────────────────────────────────────────

def _owner_earnings(f: CompanyFundamentals, caveats: list) -> Tuple[Optional[float], str]:
    """
    Owner Earnings = FCF proxy from margin × revenue.
    Returns (owner_earnings_millions, note).
    """
    rev = f.annual_revenue_millions
    fcf_margin = f.free_cash_flow_margin

    if rev and fcf_margin is not None:
        oe = rev * fcf_margin / 100.0
        if oe <= 0:
            caveats.append("Owner earnings are negative — DCF intrinsic value not meaningful. "
                           "Use revenue-based projection only.")
            return None, "Negative FCF — revenue projection used."
        return round(oe, 1), f"FCF margin {fcf_margin:.1f}% × revenue ${rev:.0f}M"

    caveats.append("FCF margin or revenue missing — using revenue-based projection with assumed margin.")
    return None, "Owner earnings unavailable."


# ── Discount rate ─────────────────────────────────────────────────────────────

def _discount_rate(
    f: CompanyFundamentals,
    sector: Sector,
    sole_source: bool,
) -> Tuple[float, List[str]]:
    """
    Build an adjusted WACC with explicit rationale for each adjustment.
    Returns (rate_pct, list_of_adjustments).
    """
    rate = 9.0
    adj = []

    # DoD concentration durability discount
    dod = f.dod_revenue_pct or 0
    if dod >= 70 and sole_source:
        rate -= 1.0
        adj.append("−1.0%: High DoD concentration (≥70%) + sole-source → more durable cash flows")
    elif dod >= 50:
        rate -= 0.5
        adj.append("−0.5%: Solid DoD concentration (≥50%) → meaningful revenue stability")
    elif dod < 20 and f.government_revenue_pct and f.government_revenue_pct < 30:
        rate += 0.5
        adj.append("+0.5%: Low government revenue concentration → less durable for this thesis")

    # Moat
    moat = (f.moat_rating or "None").strip()
    if moat == "Wide":
        rate -= 0.5
        adj.append("−0.5%: Wide economic moat → reduced competitive risk")
    elif moat == "None":
        rate += 1.0
        adj.append("+1.0%: No identified moat → higher competitive risk premium")

    # Leverage
    de = f.debt_equity or 0
    if de > 2.0:
        rate += 0.75
        adj.append(f"+0.75%: High leverage (D/E {de:.1f}x) → financial risk premium")
    elif de > 1.5:
        rate += 0.5
        adj.append(f"+0.5%: Elevated leverage (D/E {de:.1f}x)")

    # Size
    mc = f.market_cap_millions or 0
    if mc < 1000:
        rate += 1.5
        adj.append("+1.5%: Small cap (<$1B) → liquidity and concentration risk")
    elif mc < 3000:
        rate += 0.75
        adj.append("+0.75%: Small-mid cap (<$3B) → modest size premium")

    # Profitability
    fcf = f.free_cash_flow_margin or 0
    if fcf < 0:
        rate += 2.0
        adj.append("+2.0%: Negative FCF → significant execution risk premium")
    elif fcf < 3:
        rate += 0.5
        adj.append("+0.5%: Very thin FCF margin → limited downside protection")

    # Sector-specific
    if sector in (Sector.SHIPBUILDING, Sector.ENERGY_NUCLEAR):
        rate -= 0.25
        adj.append("−0.25%: Sector (shipbuilding/nuclear) has structural sole-source dynamics")

    return round(rate, 2), adj


# ── Growth assumptions ────────────────────────────────────────────────────────

def _growth_assumptions(
    f: CompanyFundamentals,
    sector: Sector,
) -> Tuple[float, float, float, float, float, float]:
    """
    Returns (bear_g1, bear_g2, base_g1, base_g2, bull_g1, bull_g2)
    where g1 = years 1-5 annual growth, g2 = years 6-10 annual growth.
    """
    # Sector base growth rates
    sector_growth = {
        Sector.SHIPBUILDING:              (3, 2, 5, 3, 8, 5),
        Sector.ENERGY_NUCLEAR:            (4, 3, 6, 4, 9, 6),
        Sector.TRADITIONAL_DEFENSE_PRIME: (2, 2, 4, 3, 7, 4),
        Sector.AEROSPACE:                 (3, 2, 5, 3, 8, 5),
        Sector.CYBERSECURITY:             (8, 5, 14, 9, 22, 14),
        Sector.AI_DATA_SOFTWARE:          (10, 6, 18, 11, 28, 18),
        Sector.CLOUD_IT_SERVICES:         (5, 3, 8, 5, 14, 9),
        Sector.MILITARY_HEALTHCARE:       (3, 2, 5, 3, 7, 4),
        Sector.PHARMACEUTICAL_BIOTECH:    (4, 3, 7, 5, 12, 8),
        Sector.MEDICAL_DEVICES:           (5, 3, 8, 5, 13, 8),
        Sector.LOGISTICS:                 (2, 1, 4, 3, 7, 4),
        Sector.INFRASTRUCTURE_CONSTRUCTION: (3, 2, 5, 4, 9, 6),
        Sector.SPACE:                     (8, 5, 15, 9, 25, 15),
        Sector.INDUSTRIAL_COMPONENTS:     (3, 2, 5, 3, 9, 5),
    }
    defaults = (2, 1, 4, 3, 8, 5)
    bear_g1, bear_g2, base_g1, base_g2, bull_g1, bull_g2 = sector_growth.get(sector, defaults)

    # Anchor to actual company revenue growth when available.
    # Pure sector defaults ignore whether a company is actually growing fast or slow —
    # a company growing 30% YoY should not get the same base case as one growing 4%.
    # Blend: 60% actual + 40% sector default for yr1-5 (mean-revert more in yr6-10).
    actual = f.revenue_growth_1yr
    if actual is not None and -10 < actual < 60:
        # Base yr1–5: 60% actual, 40% sector
        base_g1 = round(actual * 0.60 + base_g1 * 0.40, 1)
        base_g1 = max(-2.0, min(base_g1, 40.0))
        # Bull yr1–5: ~85% of recent momentum sustained; at least 3pp above base
        bull_g1 = round(max(actual * 0.85, bull_g1 * 0.90), 1)
        bull_g1 = max(bull_g1, base_g1 + 3.0)
        bull_g1 = min(bull_g1, 50.0)
        # Bear yr1–5: 40% of actual growth (significant deceleration)
        bear_g1 = round(min(actual * 0.40, bear_g1), 1)
        bear_g1 = max(bear_g1, -5.0)
        # Yr6–10: stronger mean-reversion to sector long-run rates
        actual_tapered = actual * 0.45
        base_g2 = round(actual_tapered * 0.40 + base_g2 * 0.60, 1)
        base_g2 = max(1.0, base_g2)
        bull_g2 = round(bull_g1 * 0.55, 1)
        bull_g2 = max(bull_g2, base_g2 + 1.0)
        bull_g2 = min(bull_g2, 25.0)
        bear_g2 = round(bear_g1 * 0.65, 1)
        bear_g2 = max(bear_g2, -2.0)

    # Adjust bear down if FCF is negative (already struggling)
    if (f.free_cash_flow_margin or 0) < 0:
        bear_g1 = max(bear_g1 - 2, -5)
        bear_g2 = max(bear_g2 - 2, -3)

    # Boost bull if wide moat (pricing power)
    if (f.moat_rating or "") == "Wide":
        bull_g1 = min(bull_g1 + 2, 50)
        bull_g2 = min(bull_g2 + 2, 25)

    return bear_g1, bear_g2, base_g1, base_g2, bull_g1, bull_g2


def _terminal_growth(sector: Sector, f: CompanyFundamentals) -> float:
    """Perpetual growth rate after year 10 — conservative, GDP-anchored."""
    dod = f.dod_revenue_pct or 0
    # High-DoD companies are constrained by defense budget growth (~2-3% real)
    if dod >= 70:
        return 2.5
    high_growth = {Sector.CYBERSECURITY, Sector.AI_DATA_SOFTWARE, Sector.SPACE}
    if sector in high_growth:
        return 3.5
    return 3.0


# ── DCF calculation ───────────────────────────────────────────────────────────

def _dcf_calc(
    base_earnings: Optional[float],
    base_revenue: float,
    growth_yr1_5: float,
    growth_yr6_10: float,
    terminal_growth: float,
    fcf_margin: float,
    discount_rate: float,
    shares_millions: Optional[float],
    current_price: Optional[float],
    use_revenue_based: bool = False,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    10-year DCF + Gordon Growth terminal value.
    Returns (total_iv_millions, iv_per_share, margin_of_safety_pct).
    """
    if use_revenue_based or base_earnings is None:
        if base_revenue <= 0:
            return None, None, None
        # Project revenue, apply FCF margin to get cash flows
        start = base_revenue
    else:
        # Project owner earnings directly
        start = base_earnings

    r = discount_rate / 100.0
    tg = terminal_growth / 100.0
    g1 = growth_yr1_5 / 100.0
    g2 = growth_yr6_10 / 100.0

    pv = 0.0
    current = start

    for yr in range(1, 11):
        g = g1 if yr <= 5 else g2
        current = current * (1 + g)
        if use_revenue_based:
            cf = current * (fcf_margin / 100.0)
        else:
            cf = current
        pv += cf / ((1 + r) ** yr)

    # Terminal value (Gordon Growth)
    terminal_cf = current * (1 + tg)
    if use_revenue_based:
        terminal_cf = current * (fcf_margin / 100.0) * (1 + tg)

    if r <= tg:
        # Discount rate <= terminal growth — DCF breaks down; use conservative cap
        tv = terminal_cf / 0.03
    else:
        tv = terminal_cf / (r - tg)

    tv_pv = tv / ((1 + r) ** 10)
    total_pv = pv + tv_pv

    iv_per_share = None
    mos = None
    if shares_millions and shares_millions > 0:
        iv_per_share = round(total_pv / shares_millions, 2)
        if current_price and current_price > 0 and iv_per_share >= 0:
            # MoS is only computed when IV ≥ 0. A negative IV means the business is
            # projected to destroy capital — (negative_IV - price) / price is
            # arithmetically valid but investment-meaningless (implies stock must fall
            # >100% to reach "fair value"). Suppress it; the _verdict function handles
            # negative IV separately with a solvency-risk verdict.
            mos = round((iv_per_share - current_price) / current_price * 100, 1)

    return round(total_pv, 1), iv_per_share, mos


# ── Reverse DCF ───────────────────────────────────────────────────────────────

def _reverse_dcf(
    current_price: float,
    base_earnings: float,
    shares_millions: float,
    discount_rate: float,
    terminal_growth: float,
    fcf_margin: float,
    net_debt_millions: float = 0.0,
) -> Optional[float]:
    """
    Binary search: what constant growth rate makes DCF = current market price?
    Returns implied annual growth rate (%) for years 1-10.

    The DCF produces Enterprise Value. We solve for the growth rate that makes
    EV = equity market cap + net debt (i.e., current Enterprise Value).
    """
    if shares_millions <= 0 or base_earnings <= 0:
        return None

    # Target is enterprise value (equity market cap + net debt)
    target_iv = current_price * shares_millions + (net_debt_millions or 0)

    lo, hi = -5.0, 50.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        iv_total, _, _ = _dcf_calc(
            base_earnings=base_earnings,
            base_revenue=0,
            growth_yr1_5=mid,
            growth_yr6_10=mid * 0.6,
            terminal_growth=terminal_growth,
            fcf_margin=fcf_margin,
            discount_rate=discount_rate,
            shares_millions=shares_millions,
            current_price=None,
            use_revenue_based=False,
        )
        if iv_total is None:
            return None
        if abs(iv_total - target_iv) < target_iv * 0.001:
            break
        if iv_total < target_iv:
            lo = mid
        else:
            hi = mid

    return round(mid, 1)


# ── Verdict ───────────────────────────────────────────────────────────────────

def _verdict(
    mos_base: Optional[float],
    base_s: ScenarioResult,
    bear_s: ScenarioResult,
    bull_s: ScenarioResult,
    implied_g: Optional[float],
    f: CompanyFundamentals,
    caveats: list,
) -> Tuple[str, str, float]:
    """Returns (verdict_str, valuation_note, valuation_score_0_to_100)."""

    # Negative intrinsic value — company burns more cash than it generates; DCF IV is below zero.
    # Check this BEFORE the mos_base is None check, because we explicitly suppress MoS
    # when IV < 0 (making mos_base None); the "price unknown" path must not win over this.
    iv_base = base_s.intrinsic_value_per_share
    if iv_base is not None and iv_base < 0:
        note = (
            f"All DCF scenarios produce negative intrinsic value (base: ${iv_base:.2f}/share) "
            "because projected free cash flows are negative — the business is currently destroying "
            "capital rather than creating it. DCF margin-of-safety is not meaningful here; this is "
            "a fundamental solvency question, not a valuation one."
        )
        caveats.append(
            "Negative intrinsic value: company projected to destroy capital under all scenarios. "
            "Do not use MoS % for this company — it is arithmetically misleading when IV < 0."
        )
        return "Negative IV — capital destruction risk", note, 5.0

    if mos_base is None:
        caveats.append("Cannot compute margin of safety — current price or shares unavailable.")
        note = "Intrinsic value range computed but margin of safety requires current share price."
        iv_low  = bear_s.intrinsic_value_per_share
        iv_high = bull_s.intrinsic_value_per_share
        if iv_base:
            note += f" Base IV estimate: ${iv_base:.2f}/share (range: ${iv_low:.2f}–${iv_high:.2f})." if iv_low and iv_high else ""
        return "Price unknown — verify manually", note, 50.0

    # Margin of safety thresholds
    if mos_base >= 35:
        verdict = "Significantly Undervalued"
        score = 92.0
    elif mos_base >= 20:
        verdict = "Undervalued — Attractive Entry"
        score = 82.0
    elif mos_base >= 10:
        verdict = "Modestly Undervalued"
        score = 72.0
    elif mos_base >= 0:
        verdict = "Fairly Valued"
        score = 60.0
    elif mos_base >= -15:
        verdict = "Modestly Overvalued"
        score = 42.0
    elif mos_base >= -30:
        verdict = "Overvalued"
        score = 28.0
    else:
        verdict = "Significantly Overvalued"
        score = 12.0

    # Build plain-English note
    iv_base  = base_s.intrinsic_value_per_share
    iv_low   = bear_s.intrinsic_value_per_share
    iv_high  = bull_s.intrinsic_value_per_share
    price    = f.market_cap_millions  # proxy if no share price

    parts = []
    if iv_base and iv_low and iv_high:
        parts.append(
            f"Base intrinsic value estimate: ${iv_base:.2f}/share "
            f"(bear: ${iv_low:.2f} — bull: ${iv_high:.2f})."
        )
    parts.append(f"Margin of safety at base case: {mos_base:+.1f}%.")

    if implied_g is not None:
        parts.append(
            f"Reverse DCF: the current market price implies ~{implied_g:.1f}% annual "
            f"growth for 10 years at a {base_s.discount_rate:.1f}% discount rate. "
        )
        if implied_g > 20:
            parts.append("That is an aggressive assumption — high execution risk.")
            score = min(score, 35.0)
            caveats.append(f"Market pricing in {implied_g:.1f}% growth — very optimistic; "
                           "any deceleration could reprice the stock sharply.")
        elif implied_g > 12:
            parts.append("Achievable but requires consistent execution.")
        elif implied_g < 3:
            parts.append("Market appears to be pricing in near-stagnation — "
                         "potentially pessimistic if business fundamentals are sound.")

    # Bear case check — if bear IV is below current price, downside is real
    bear_mos = bear_s.margin_of_safety_pct
    if bear_mos is not None and bear_mos < -20:
        parts.append(
            f"Bear case ({bear_s.revenue_growth_yr1_5:.0f}% growth) implies "
            f"{abs(bear_mos):.0f}% downside — meaningful downside risk if thesis disappoints."
        )
        caveats.append("Bear case shows significant downside. Position sizing discipline is important.")

    # Large commercial company warning — DCF uses total-company FCF, not DoD portion.
    # For companies where DoD revenue is a small fraction of total, a favorable MoS
    # reflects the commercial business, not the DoD investment catalyst.
    dod_pct = getattr(f, "dod_revenue_pct", None)
    mkt_cap = getattr(f, "market_cap_millions", None)
    if dod_pct is not None and dod_pct < 20 and mkt_cap is not None and mkt_cap > 15_000:
        caveat_txt = (
            f"DCF uses total-company FCF — DoD revenue is only ~{dod_pct:.0f}% of the business. "
            f"The {mos_base:+.0f}% margin of safety reflects the entire enterprise (including "
            "commercial/government-other revenue), NOT the DoD contract thesis specifically. "
            "Treat as a total-company valuation check, not a DoD catalyst entry signal."
        )
        parts.append(f"⚠ {caveat_txt}")
        caveats.append(caveat_txt)
        # Suppress MoS benefit from inflating the valuation score
        score = min(score, 45.0)

    note = " ".join(parts)
    return verdict, note, round(score, 1)


# ── Data quality assessment ───────────────────────────────────────────────────

def _assess_data_quality(f: CompanyFundamentals, caveats: list) -> str:
    missing = []
    if f.annual_revenue_millions is None:
        missing.append("revenue")
    if f.free_cash_flow_margin is None:
        missing.append("FCF margin")
    if f.market_cap_millions is None:
        missing.append("market cap")

    if len(missing) == 0:
        return "good"
    elif len(missing) <= 1:
        caveats.append(f"Missing: {', '.join(missing)}. DCF confidence is partial.")
        return "partial"
    else:
        caveats.append(f"Missing key inputs ({', '.join(missing)}). DCF is illustrative only.")
        return "insufficient"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val: Optional[float], default: float = 0.0) -> float:
    return val if val is not None else default
