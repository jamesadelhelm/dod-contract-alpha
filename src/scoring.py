"""
Scoring engine for DoD Contract Intelligence Agent.

Implements the 6-component framework:
  1. Buffett Quality Score      (25%)
  2. Graham Value Score         (20%)
  3. DoD Stability Score        (20%)
  4. Management Quality Score   (15%)
  5. Contract Catalyst Score    (10%)
  6. Balance Sheet Score        (10%)

All scores are 0–100. Each function returns a (score, explanation, flags) tuple.
Scoring is fully deterministic and transparent.
"""

from __future__ import annotations
from typing import List, Tuple, Optional
from src.dcf import run_dcf as _run_dcf
from src.models import (
    Contract, CompanyFundamentals, ComponentScore, CompanyScore,
    ContractType, Sector, Verdict, SpecialistProfile, SpecialistTierStatus
)
from config import SCORE_WEIGHTS, VERDICT_THRESHOLDS, OVERRIDE_RULES


# ── Utility ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _safe(val: Optional[float], default: float = 0.0) -> float:
    return val if val is not None else default


_COMPLETENESS_FIELDS = [
    "pe_ratio", "forward_pe", "ev_ebitda", "fcf_yield", "price_to_book",
    "current_ratio", "roic", "free_cash_flow_margin", "operating_margin",
    "earnings_stability_years", "roe", "debt_equity", "debt_ebitda",
    "interest_coverage", "dod_revenue_pct", "backlog_to_revenue",
]


def _compute_data_completeness(f) -> float:
    populated = sum(1 for field in _COMPLETENESS_FIELDS if getattr(f, field, None) is not None)
    return round(populated / len(_COMPLETENESS_FIELDS) * 100)


def _score_bracket(value: float, brackets: list) -> float:
    """
    brackets: list of (threshold, score) tuples, sorted descending by threshold.
    Returns the score for the first threshold the value exceeds.
    """
    for threshold, score in brackets:
        if value >= threshold:
            return score
    return brackets[-1][1] if brackets else 0.0


# ── 1. Buffett Quality Score ──────────────────────────────────────────────────

def score_buffett_quality(f: CompanyFundamentals) -> Tuple[float, str, List[str]]:
    """
    Measures whether this is a high-quality durable business.
    Max: 100. Weight: 25%.
    """
    flags = []
    points = 0.0
    details = []

    # Economic moat (moat_rating) — 20 pts
    moat = (f.moat_rating or "None").strip()
    if moat == "Wide":
        points += 20
        details.append("Wide economic moat (+20)")
    elif moat == "Narrow":
        points += 11
        details.append("Narrow economic moat (+11)")
    else:
        points += 2
        details.append("No identified moat (+2)")
        flags.append("No clear economic moat")

    # ROIC — 20 pts
    roic = _safe(f.roic)
    if roic > 0:
        roic_pts = _score_bracket(roic, [
            (20, 20), (15, 17), (12, 14), (9, 10), (6, 6), (3, 3), (0.1, 1)
        ])
        points += roic_pts
        details.append(f"ROIC {roic:.1f}% (+{roic_pts:.0f})")
        if roic < 8:
            flags.append(f"ROIC is low ({roic:.1f}%)")
    else:
        details.append("ROIC unavailable or negative (+0)")
        flags.append("ROIC not available or negative — conservative score applied")

    # Free cash flow margin — 15 pts
    fcf_margin = _safe(f.free_cash_flow_margin)
    fcf_pts = _score_bracket(fcf_margin, [
        (20, 15), (14, 13), (10, 11), (7, 8), (4, 5), (0.1, 2), (-999, 0)
    ])
    points += fcf_pts
    details.append(f"FCF margin {fcf_margin:.1f}% (+{fcf_pts:.0f})")
    if fcf_margin <= 0:
        flags.append("Negative free cash flow — concern for capital discipline")

    # Operating margin — 15 pts
    op_margin = _safe(f.operating_margin)
    op_pts = _score_bracket(op_margin, [
        (18, 15), (14, 13), (10, 11), (7, 8), (4, 5), (0.1, 2), (-999, 0)
    ])
    points += op_pts
    details.append(f"Operating margin {op_margin:.1f}% (+{op_pts:.0f})")
    if op_margin < 5:
        flags.append(f"Low operating margin ({op_margin:.1f}%)")

    # Earnings stability — 15 pts
    stab = _safe(f.earnings_stability_years)
    stab_pts = _score_bracket(stab, [
        (20, 15), (15, 13), (10, 10), (7, 8), (5, 5), (3, 3), (1, 1), (0, 0)
    ])
    points += stab_pts
    details.append(f"Earnings stability {stab:.0f} yrs (+{stab_pts:.0f})")
    if stab < 5:
        flags.append(f"Short earnings history ({stab:.0f} years)")

    # ROE — 15 pts (with caveat for leverage-inflated ROE)
    roe = _safe(f.roe)
    if roe > 0:
        roe_pts = _score_bracket(roe, [
            (30, 15), (20, 13), (15, 11), (10, 8), (5, 4), (0.1, 1)
        ])
        # Cap if ROE is inflated by very high debt
        if roe > 40 and _safe(f.debt_equity) > 2.0:
            roe_pts = min(roe_pts, 10)
            flags.append(f"High ROE ({roe:.1f}%) may be leverage-inflated (D/E {f.debt_equity:.1f}x)")
        points += roe_pts
        details.append(f"ROE {roe:.1f}% (+{roe_pts:.0f})")
    else:
        details.append(f"ROE {roe:.1f}% — negative (+0)")
        flags.append("Negative ROE")

    score = _clamp(points)

    # Override: unprofitable with dilution
    if fcf_margin <= 0 and op_margin <= 0:
        cap = OVERRIDE_RULES["unprofitable_high_dilution_max_buffett"]
        if score > cap:
            score = cap
            flags.append(f"Score capped at {cap} — unprofitable business")

    explanation = (
        f"Buffett Quality Score: {score:.0f}/100. "
        + " | ".join(details)
    )
    return _clamp(score), explanation, flags


# ── 2. Graham Value Score ─────────────────────────────────────────────────────

def score_graham_value(f: CompanyFundamentals) -> Tuple[float, str, List[str]]:
    """
    Measures whether the stock is reasonably priced and financially safe.
    Max: 100. Weight: 20%.
    """
    flags = []
    points = 0.0
    details = []

    # P/E ratio — 20 pts
    pe = f.pe_ratio
    if pe is None or pe <= 0:
        details.append("P/E not available or negative (+0)")
        flags.append("No valid P/E ratio — unprofitable or data missing")
    else:
        # Lower P/E = more points (Graham value discipline)
        pe_pts = 0
        if pe <= 12:
            pe_pts = 20
        elif pe <= 16:
            pe_pts = 17
        elif pe <= 20:
            pe_pts = 13
        elif pe <= 25:
            pe_pts = 10
        elif pe <= 35:
            pe_pts = 6
        elif pe <= 60:
            pe_pts = 2
        else:
            pe_pts = 0
            flags.append(f"Very high P/E ({pe:.0f}x) — significant valuation risk")
        points += pe_pts
        details.append(f"P/E {pe:.1f}x (+{pe_pts:.0f})")

    # Forward P/E — 10 pts
    fpe = f.forward_pe
    if fpe and fpe > 0:
        if fpe <= 14:
            fpe_pts = 10
        elif fpe <= 18:
            fpe_pts = 8
        elif fpe <= 22:
            fpe_pts = 6
        elif fpe <= 30:
            fpe_pts = 3
        elif fpe <= 50:
            fpe_pts = 1
        else:
            fpe_pts = 0
        points += fpe_pts
        details.append(f"Fwd P/E {fpe:.1f}x (+{fpe_pts:.0f})")

    # EV/EBITDA — 15 pts
    ev = f.ev_ebitda
    if ev and ev > 0:
        if ev <= 10:
            ev_pts = 15
        elif ev <= 13:
            ev_pts = 12
        elif ev <= 16:
            ev_pts = 9
        elif ev <= 22:
            ev_pts = 5
        elif ev <= 35:
            ev_pts = 2
        else:
            ev_pts = 0
            flags.append(f"Very high EV/EBITDA ({ev:.0f}x)")
        points += ev_pts
        details.append(f"EV/EBITDA {ev:.1f}x (+{ev_pts:.0f})")
    else:
        details.append("EV/EBITDA not available (+0)")

    # Free cash flow yield — 20 pts
    fcfy = f.fcf_yield
    if fcfy and fcfy > 0:
        if fcfy >= 7:
            fcfy_pts = 20
        elif fcfy >= 5:
            fcfy_pts = 16
        elif fcfy >= 3.5:
            fcfy_pts = 12
        elif fcfy >= 2:
            fcfy_pts = 7
        elif fcfy >= 1:
            fcfy_pts = 3
        else:
            fcfy_pts = 1
        points += fcfy_pts
        details.append(f"FCF yield {fcfy:.1f}% (+{fcfy_pts:.0f})")
    elif fcfy is not None and fcfy <= 0:
        flags.append("Negative FCF yield — no margin of safety from cash generation")
        details.append(f"FCF yield {(_safe(fcfy)):.1f}% (+0)")

    # Price-to-Book — 15 pts (Graham margin-of-safety signal)
    # Graham famously required P/B < 1.5; <1.0 was his "net-net" threshold.
    pb = f.price_to_book
    if pb is not None and pb > 0:
        if pb <= 1.0:
            pb_pts = 15
        elif pb <= 1.5:
            pb_pts = 12
        elif pb <= 2.5:
            pb_pts = 9
        elif pb <= 4.0:
            pb_pts = 5
        elif pb <= 8.0:
            pb_pts = 2
        else:
            pb_pts = 0
        points += pb_pts
        details.append(f"P/B {pb:.1f}x (+{pb_pts:.0f})")
    else:
        details.append("P/B not available (+0)")

    # Earnings stability as Graham signal — 10 pts
    stab = _safe(f.earnings_stability_years)
    if stab >= 10:
        points += 10
        details.append(f"Earnings stability {stab:.0f} yrs (+10)")
    elif stab >= 7:
        points += 7
        details.append(f"Earnings stability {stab:.0f} yrs (+7)")
    elif stab >= 5:
        points += 4
        details.append(f"Earnings stability {stab:.0f} yrs (+4)")
    else:
        details.append(f"Earnings stability {stab:.0f} yrs (+0)")
        flags.append("Insufficient earnings track record for Graham-style confidence")

    # Current ratio — 10 pts
    cr = f.current_ratio
    if cr is not None:
        if cr >= 2.0:
            cr_pts = 10
        elif cr >= 1.5:
            cr_pts = 8
        elif cr >= 1.2:
            cr_pts = 5
        elif cr >= 1.0:
            cr_pts = 2
        else:
            cr_pts = 0
            flags.append(f"Current ratio below 1.0 ({cr:.1f}) — liquidity concern")
        points += cr_pts
        details.append(f"Current ratio {cr:.1f}x (+{cr_pts:.0f})")

    score = _clamp(points)
    explanation = (
        f"Graham Value Score: {score:.0f}/100. "
        + " | ".join(details)
    )
    return score, explanation, flags


# ── 3. DoD / Government Revenue Stability Score ───────────────────────────────

def score_dod_stability(
    f: CompanyFundamentals,
    contracts: List[Contract],
    sector: Sector
) -> Tuple[float, str, List[str]]:
    """
    Measures how durable and secure the government funding stream is.
    Max: 100. Weight: 20%.
    """
    flags = []
    points = 0.0
    details = []

    # % revenue from DoD — 25 pts
    # When the curated overlay has no DoD% data, infer a conservative estimate
    # from the contract sector rather than defaulting to 0. A company receiving
    # a Shipbuilding contract almost certainly has high DoD concentration;
    # treating it identically to a consumer company that won a catering deal is
    # inaccurate. We apply a 55% discount to the sector estimate and flag it.
    _SECTOR_DOD_ESTIMATES = {
        Sector.SHIPBUILDING: 92,
        Sector.TRADITIONAL_DEFENSE_PRIME: 78,
        Sector.ENERGY_NUCLEAR: 72,
        Sector.AEROSPACE: 65,
        Sector.MILITARY_HEALTHCARE: 55,
        Sector.SPACE: 50,
        Sector.INFRASTRUCTURE_CONSTRUCTION: 45,
        Sector.CYBERSECURITY: 42,
        Sector.CONSULTING_SERVICES: 38,
        Sector.LOGISTICS: 35,
        Sector.AI_DATA_SOFTWARE: 32,
        Sector.INDUSTRIAL_COMPONENTS: 28,
        Sector.MEDICAL_DEVICES: 24,
        Sector.PHARMACEUTICAL_BIOTECH: 22,
        Sector.CLOUD_IT_SERVICES: 18,
        Sector.UNCLEAR: 22,
    }
    if f.dod_revenue_pct is not None:
        dod_pct = f.dod_revenue_pct
        dod_estimated = False
    else:
        raw_estimate = _SECTOR_DOD_ESTIMATES.get(sector, 22)
        dod_pct = raw_estimate * 0.55   # conservative 45% discount for uncertainty
        dod_estimated = True
        flags.append(
            f"DoD revenue % not in curated data — estimated ~{dod_pct:.0f}% "
            f"(sector '{sector.value}' implies ~{raw_estimate:.0f}%, discounted 45%). Verify manually."
        )

    if dod_pct >= 70:
        d_pts = 25
    elif dod_pct >= 50:
        d_pts = 20
    elif dod_pct >= 30:
        d_pts = 14
    elif dod_pct >= 15:
        d_pts = 8
    elif dod_pct >= 5:
        d_pts = 4
    else:
        d_pts = 0
        if not dod_estimated:
            flags.append(f"Low DoD revenue concentration ({dod_pct:.0f}%)")
    points += d_pts
    if dod_estimated:
        details.append(f"DoD rev ~{dod_pct:.0f}% est. (+{d_pts:.0f})")
    else:
        details.append(f"DoD rev {dod_pct:.0f}% (+{d_pts:.0f})")

    # Backlog / annual revenue — 20 pts
    bl = _safe(f.backlog_to_revenue)
    if bl >= 3.5:
        bl_pts = 20
    elif bl >= 2.5:
        bl_pts = 16
    elif bl >= 1.5:
        bl_pts = 11
    elif bl >= 0.75:
        bl_pts = 6
    elif bl > 0:
        bl_pts = 2
    else:
        bl_pts = 0
        flags.append("No visible backlog data")
    points += bl_pts
    if bl > 0:
        details.append(f"Backlog/Rev {bl:.1f}x (+{bl_pts:.0f})")

    # Sole-source or highly specialized presence — 20 pts
    has_sole_source = any(c.is_sole_source for c in contracts)
    is_critical_sector = sector in [
        Sector.SHIPBUILDING, Sector.ENERGY_NUCLEAR,
        Sector.TRADITIONAL_DEFENSE_PRIME, Sector.AEROSPACE
    ]
    if has_sole_source and is_critical_sector:
        ss_pts = 20
        details.append("Sole-source + critical sector (+20)")
    elif has_sole_source:
        ss_pts = 13
        details.append("Sole-source work present (+13)")
    elif is_critical_sector:
        ss_pts = 10
        details.append("Mission-critical sector (+10)")
    else:
        ss_pts = 4
        details.append("No sole-source identified (+4)")
    points += ss_pts

    # Multi-year / long-duration contracts — 15 pts
    long_contracts = [c for c in contracts if c.completion_date and "203" in str(c.completion_date)]
    if long_contracts:
        points += 15
        details.append(f"Long-duration contract visibility (+15)")
    elif contracts:
        points += 6
        details.append("Near-term contracts only (+6)")

    # Sector durability bonus — 20 pts
    # Highest for sectors tied to strategic national priorities
    durable_sectors = {
        Sector.SHIPBUILDING: 20,
        Sector.ENERGY_NUCLEAR: 20,
        Sector.TRADITIONAL_DEFENSE_PRIME: 18,
        Sector.AEROSPACE: 16,
        Sector.MILITARY_HEALTHCARE: 16,
        Sector.CYBERSECURITY: 14,
        Sector.AI_DATA_SOFTWARE: 12,
        Sector.SPACE: 12,
        Sector.CLOUD_IT_SERVICES: 10,
        Sector.MEDICAL_DEVICES: 10,
        Sector.PHARMACEUTICAL_BIOTECH: 9,
        Sector.LOGISTICS: 8,
        Sector.INDUSTRIAL_COMPONENTS: 8,
        Sector.INFRASTRUCTURE_CONSTRUCTION: 7,
        Sector.CONSULTING_SERVICES: 5,
        Sector.UNCLEAR: 3,
    }
    sec_pts = durable_sectors.get(sector, 5)
    points += sec_pts
    details.append(f"Sector durability [{sector.value}] (+{sec_pts:.0f})")

    score = _clamp(points)
    explanation = (
        f"DoD Stability Score: {score:.0f}/100. "
        + " | ".join(details)
    )
    return score, explanation, flags


# ── 4. Management Quality Score ───────────────────────────────────────────────

def score_management(f: CompanyFundamentals) -> Tuple[float, str, List[str]]:
    """
    Measures whether management acts like good stewards of capital.
    Max: 100. Weight: 15%.

    Intentionally distinct from Buffett Quality: focuses on stewardship signals
    (ROIC as returns on investment, share count discipline, FCF execution, skin
    in the game, debt restraint) rather than business quality signals (moat, margins).
    earnings_stability is NOT used here — it already appears in Buffett and Graham.
    """
    flags = []
    points = 0.0
    details = []

    # ROIC as capital allocation signal — 25 pts
    roic = _safe(f.roic)
    if roic >= 18:
        r_pts = 25
    elif roic >= 13:
        r_pts = 20
    elif roic >= 9:
        r_pts = 15
    elif roic >= 6:
        r_pts = 8
    elif roic > 0:
        r_pts = 3
    else:
        r_pts = 0
        flags.append("ROIC not positive — capital allocation quality unproven")
    points += r_pts
    details.append(f"ROIC {roic:.1f}% (+{r_pts:.0f})")

    # Share count discipline — 25 pts
    # Buybacks = management returning capital; dilution = management consuming it.
    # This is a distinct management signal not captured anywhere else in the framework.
    shares_chg = getattr(f, "shares_chg_1yr_pct", None)
    if shares_chg is not None:
        if shares_chg < -5:
            sc_pts = 25
            details.append(f"Shares {shares_chg:+.1f}% YoY — meaningful buyback (+25)")
        elif shares_chg < -2:
            sc_pts = 20
            details.append(f"Shares {shares_chg:+.1f}% YoY — modest buyback (+20)")
        elif shares_chg <= 1:
            sc_pts = 15
            details.append(f"Shares ~flat YoY (+15)")
        elif shares_chg <= 3:
            sc_pts = 8
            details.append(f"Shares {shares_chg:+.1f}% YoY — minor dilution (+8)")
        elif shares_chg <= 7:
            sc_pts = 3
            details.append(f"Shares {shares_chg:+.1f}% YoY — material dilution (+3)")
            flags.append(f"Material share dilution ({shares_chg:+.1f}% YoY) — equity value erosion")
        else:
            sc_pts = 0
            details.append(f"Shares {shares_chg:+.1f}% YoY — aggressive dilution (+0)")
            flags.append(f"Aggressive dilution ({shares_chg:+.1f}% YoY) — destroying equity value")
    else:
        sc_pts = 10  # unknown: neutral default, no bonus, no penalty
        details.append("Share count change N/A (+10)")
    points += sc_pts

    # FCF margin as execution quality — 25 pts
    fcf = _safe(f.free_cash_flow_margin)
    if fcf >= 12:
        f_pts = 25
    elif fcf >= 8:
        f_pts = 19
    elif fcf >= 5:
        f_pts = 13
    elif fcf >= 2:
        f_pts = 7
    elif fcf >= 0:
        f_pts = 2
    else:
        f_pts = 0
        flags.append("Negative FCF — execution quality concern")
    points += f_pts
    details.append(f"FCF margin {fcf:.1f}% (+{f_pts:.0f})")

    # Insider ownership — skin in the game — 15 pts
    insider = _safe(f.insider_ownership_pct)
    if insider >= 10:
        i_pts = 15
    elif insider >= 5:
        i_pts = 11
    elif insider >= 2:
        i_pts = 7
    elif insider >= 0.5:
        i_pts = 4
    else:
        i_pts = 1
    points += i_pts
    details.append(f"Insider ownership {insider:.1f}% (+{i_pts:.0f})")
    if insider < 1 and f.insider_ownership_pct is not None:
        flags.append(f"Very low insider ownership ({insider:.1f}%) — management incentive alignment weak")

    # Debt discipline — 10 pts
    de = _safe(f.debt_equity)
    debt_ebitda = _safe(f.debt_ebitda)
    if de <= 0.5 or debt_ebitda <= 1.5:
        d_pts = 10
    elif de <= 1.0 or debt_ebitda <= 2.5:
        d_pts = 7
    elif de <= 2.0 or debt_ebitda <= 3.5:
        d_pts = 4
    else:
        d_pts = 1
        flags.append(f"High leverage (D/E {de:.1f}x, Debt/EBITDA {debt_ebitda:.1f}x)")
    points += d_pts
    details.append(f"Debt discipline (+{d_pts:.0f})")

    score = _clamp(points)
    explanation = (
        f"Management Quality Score: {score:.0f}/100. "
        + " | ".join(details)
    )
    return score, explanation, flags


# ── 5. Contract Catalyst Score ────────────────────────────────────────────────

def score_contract_catalyst(
    contracts: List[Contract],
    f: CompanyFundamentals
) -> Tuple[float, str, List[str]]:
    """
    Measures whether recent contracts materially improve the investment story.
    Max: 100. Weight: 10%.
    """
    flags = []
    details = []

    if not contracts:
        return 0.0, "No contracts to evaluate.", ["No contracts found"]

    # Use largest contract for type/funding/duration details
    best = max(contracts, key=lambda c: c.funded_amount or c.contract_value or 0)
    # Use total portfolio value for revenue and market cap materiality signals
    total_value = sum(c.contract_value or 0 for c in contracts)

    funded = best.funded_amount or (best.contract_value or 0)
    best_value = best.contract_value or 0
    rev = _safe(f.annual_revenue_millions, 1)
    mc = _safe(f.market_cap_millions, 1)

    # Total contract portfolio / annual revenue — 25 pts
    val_to_rev = (total_value / rev * 100) if rev > 0 else 0
    if val_to_rev >= 15:
        vr_pts = 25
    elif val_to_rev >= 8:
        vr_pts = 18
    elif val_to_rev >= 4:
        vr_pts = 12
    elif val_to_rev >= 2:
        vr_pts = 7
    elif val_to_rev >= 0.5:
        vr_pts = 3
    else:
        vr_pts = 1
    n_contracts = len(contracts)
    details.append(f"Portfolio({n_contracts} contracts ${total_value:.0f}M)/Revenue {val_to_rev:.1f}% (+{vr_pts:.0f})")

    # Funded amount / best contract value (IDIQ haircut on most significant award) — 20 pts
    if best_value > 0:
        funded_ratio = funded / best_value
    else:
        funded_ratio = 1.0

    if best.is_idiq and funded_ratio < 0.3:
        idiq_pts = 4
        details.append(f"IDIQ: funded {funded_ratio*100:.0f}% of ceiling (+{idiq_pts:.0f})")
        flags.append(
            f"IDIQ contract: only ${funded:.0f}M funded of ${best_value:.0f}M ceiling "
            f"({funded_ratio*100:.0f}%). Ceiling is aspirational — cap catalyst score."
        )
        # Apply IDIQ ceiling cap
        vr_pts = min(vr_pts, OVERRIDE_RULES["idiq_ceiling_only_max_catalyst"] // 3)
    else:
        idiq_pts = 20 if funded_ratio >= 0.95 else int(funded_ratio * 20)
        details.append(f"Funded ratio {funded_ratio*100:.0f}% (+{idiq_pts:.0f})")

    # Contract type quality — 20 pts
    if best.is_sole_source:
        ct_pts = 20
        details.append("Sole-source award (+20)")
    elif best.contract_type == ContractType.NEW_AWARD and best.is_competitive:
        ct_pts = 15
        details.append("Competitive new award (+15)")
    elif best.contract_type == ContractType.NEW_AWARD:
        ct_pts = 12
        details.append("New award (+12)")
    elif best.contract_type == ContractType.OPTION_EXERCISE:
        ct_pts = 10
        details.append("Option exercise — incumbent validated (+10)")
    elif best.contract_type == ContractType.MODIFICATION:
        ct_pts = 7
        details.append("Contract modification (+7)")
    elif best.contract_type == ContractType.IDIQ:
        ct_pts = 5
        details.append("IDIQ ceiling (+5)")
    else:
        ct_pts = 4
        details.append(f"Contract type: {best.contract_type.value} (+4)")

    # Total contract portfolio / market cap signal — 20 pts
    val_to_mc = (total_value / mc * 100) if mc > 0 else 0
    if val_to_mc >= 5:
        mc_pts = 20
    elif val_to_mc >= 2:
        mc_pts = 14
    elif val_to_mc >= 0.5:
        mc_pts = 8
    elif val_to_mc >= 0.1:
        mc_pts = 3
    else:
        mc_pts = 1
    details.append(f"Contract/Mkt Cap {val_to_mc:.2f}% (+{mc_pts:.0f})")

    # Long-duration / multi-year signal — 15 pts
    if best.completion_date and any(yr in str(best.completion_date) for yr in ["203", "204"]):
        dur_pts = 15
        details.append("Long-duration (2030+) contract (+15)")
    elif best.completion_date and "202" in str(best.completion_date):
        dur_pts = 6
        details.append("Near-term contract (+6)")
    else:
        dur_pts = 4
        details.append("Duration unknown (+4)")

    total = vr_pts + idiq_pts + ct_pts + mc_pts + dur_pts
    score = _clamp(total)

    # IDIQ ceiling-only cap
    if best.is_idiq and funded_ratio < 0.25:
        cap = OVERRIDE_RULES["idiq_ceiling_only_max_catalyst"]
        if score > cap:
            score = cap
            flags.append(f"Catalyst score capped at {cap} — IDIQ ceiling with minimal funded amount")

    explanation = (
        f"Contract Catalyst Score: {score:.0f}/100. "
        + f"Best contract: {best.awardee_name} ${best_value:.0f}M ({best.contract_type.value}). "
        + " | ".join(details)
    )
    return _clamp(score), explanation, flags


# ── 6. Balance Sheet Score ────────────────────────────────────────────────────

def score_balance_sheet(f: CompanyFundamentals) -> Tuple[float, str, List[str]]:
    """
    Measures downside protection and financial resilience.
    Max: 100. Weight: 10%.
    """
    flags = []
    points = 0.0
    details = []

    # Current ratio — 25 pts
    cr = f.current_ratio
    if cr is not None:
        if cr >= 2.0:
            c_pts = 25
        elif cr >= 1.5:
            c_pts = 20
        elif cr >= 1.2:
            c_pts = 14
        elif cr >= 1.0:
            c_pts = 7
        else:
            c_pts = 0
            flags.append(f"Current ratio below 1.0 ({cr:.1f}) — potential liquidity stress")
        points += c_pts
        details.append(f"Current ratio {cr:.1f}x (+{c_pts:.0f})")
    else:
        details.append("Current ratio N/A (+0)")

    # Net debt / EBITDA — 30 pts
    de = _safe(f.debt_ebitda)
    if de <= 0.5:
        dd_pts = 30
    elif de <= 1.5:
        dd_pts = 24
    elif de <= 2.5:
        dd_pts = 16
    elif de <= 3.5:
        dd_pts = 8
    elif de <= 5.0:
        dd_pts = 3
    else:
        dd_pts = 0
        flags.append(f"Debt/EBITDA of {de:.1f}x is very high")
    points += dd_pts
    details.append(f"Debt/EBITDA {de:.1f}x (+{dd_pts:.0f})")

    # Interest coverage — 25 pts
    ic = f.interest_coverage
    if ic is None:
        # If net debt is negative (net cash), assume high coverage
        if _safe(f.net_debt_millions) < 0:
            ic_pts = 20
            details.append("Net cash position — no coverage concern (+20)")
            points += ic_pts
        else:
            details.append("Interest coverage N/A (+0)")
    else:
        if ic >= 12:
            ic_pts = 25
        elif ic >= 8:
            ic_pts = 19
        elif ic >= 5:
            ic_pts = 12
        elif ic >= 3:
            ic_pts = 6
        elif ic >= 1.5:
            ic_pts = 2
        else:
            ic_pts = 0
            flags.append(f"Interest coverage dangerously low ({ic:.1f}x)")
        points += ic_pts
        details.append(f"Interest coverage {ic:.1f}x (+{ic_pts:.0f})")

    # Net cash vs debt position — 20 pts
    net_debt = _safe(f.net_debt_millions)
    rev = _safe(f.annual_revenue_millions, 1)
    nd_ratio = net_debt / rev if rev > 0 else 0

    if nd_ratio < -0.05:  # net cash
        nd_pts = 20
        details.append(f"Net cash position (${abs(net_debt):.0f}M) (+20)")
    elif nd_ratio < 0.10:
        nd_pts = 16
        details.append(f"Minimal net debt (+16)")
    elif nd_ratio < 0.25:
        nd_pts = 11
        details.append(f"Moderate net debt (+11)")
    elif nd_ratio < 0.50:
        nd_pts = 6
        details.append(f"Elevated net debt (+6)")
    else:
        nd_pts = 1
        flags.append(f"High net debt relative to revenue")
    points += nd_pts

    score = _clamp(points)

    # Cap the component score when balance sheet is distressed.
    # Only apply when current_ratio is actually known — missing data should not
    # default to 1.5 (passing) and silently skip the cap for leveraged companies.
    if f.current_ratio is not None and f.current_ratio < 1.0 and de > 4.0:
        if score > 40:
            score = 40
            flags.append("Balance sheet is distressed — component score capped at 40")

    explanation = (
        f"Balance Sheet Score: {score:.0f}/100. "
        + " | ".join(details)
    )
    return _clamp(score), explanation, flags


# ── Final Score + Verdict ─────────────────────────────────────────────────────

def compute_final_score(
    buffett: float,
    graham: float,
    dod: float,
    mgmt: float,
    catalyst: float,
    bs: float,
) -> float:
    w = SCORE_WEIGHTS
    return _clamp(
        buffett * w["buffett_quality"]
        + graham * w["graham_value"]
        + dod * w["dod_stability"]
        + mgmt * w["management"]
        + catalyst * w["contract_catalyst"]
        + bs * w["balance_sheet"]
    )


def determine_verdict(
    final_score: float,
    f: CompanyFundamentals,
    all_flags: List[str],
) -> Verdict:
    t = VERDICT_THRESHOLDS

    # Valuation override
    pe = f.pe_ratio or 0
    ev = f.ev_ebitda or 0
    expensive = (pe > 80 or ev > 60) and (f.free_cash_flow_margin or 0) < 15

    # Analyst divergence — if Street is materially bearish vs. our high score,
    # surface as RESEARCH_FURTHER rather than a clean buy signal.
    # Rationale: our model has an independent view, but 3+ analysts saying "sell"
    # is a red flag that warrants extra diligence, not a veto.
    analyst_rec = getattr(f, "analyst_recommendation", None)
    analyst_n   = getattr(f, "analyst_count", 0) or 0
    street_bearish = analyst_rec in ("sell", "underperform") and analyst_n >= 3

    if final_score >= t["strong_candidate"]:
        if expensive:
            return Verdict.HIGH_QUALITY_BUT_EXPENSIVE
        if street_bearish:
            return Verdict.RESEARCH_FURTHER
        return Verdict.STRONG_CANDIDATE

    if final_score >= t["potentially_attractive"]:
        if expensive:
            return Verdict.HIGH_QUALITY_BUT_EXPENSIVE
        if street_bearish:
            return Verdict.RESEARCH_FURTHER
        return Verdict.POTENTIALLY_ATTRACTIVE

    if final_score >= t["watchlist"]:
        return Verdict.WATCHLIST

    if final_score >= t["low_conviction"]:
        return Verdict.LOW_CONVICTION

    return Verdict.IGNORE


# ── Top-level: score a company ────────────────────────────────────────────────

def score_company(
    ticker: str,
    company_name: str,
    contracts: List[Contract],
    f: Optional[CompanyFundamentals] = None,
    sector: Optional[Sector] = None,
    live: bool = False,
) -> CompanyScore:
    from src.fundamentals import get_fundamentals_or_stub

    if f is None:
        f = get_fundamentals_or_stub(ticker, company_name, live=live)

    if sector is None:
        sector = contracts[0].sector if contracts else Sector.UNCLEAR

    # Compute components
    bq_raw, bq_exp, bq_flags = score_buffett_quality(f)
    gv_raw, gv_exp, gv_flags = score_graham_value(f)
    ds_raw, ds_exp, ds_flags = score_dod_stability(f, contracts, sector)
    mq_raw, mq_exp, mq_flags = score_management(f)
    cc_raw, cc_exp, cc_flags = score_contract_catalyst(contracts, f)
    bs_raw, bs_exp, bs_flags = score_balance_sheet(f)

    final = compute_final_score(bq_raw, gv_raw, ds_raw, mq_raw, cc_raw, bs_raw)
    all_flags = bq_flags + gv_flags + ds_flags + mq_flags + cc_flags + bs_flags

    # Balance sheet danger override on final.
    # Require actual current_ratio data — missing data must not default to 1.5
    # and silently allow a 5x-leveraged company to skip this cap.
    if f.current_ratio is not None and f.current_ratio < 1.0 and _safe(f.debt_ebitda) > 4.0:
        cap = OVERRIDE_RULES["dangerous_balance_sheet_max_final"]
        if final > cap:
            final = cap
            all_flags.append(f"Final score capped at {cap} — dangerous balance sheet")

    # Analyst divergence flag — Street is bearish while our model is positive
    analyst_rec = getattr(f, "analyst_recommendation", None)
    analyst_n   = getattr(f, "analyst_count", 0) or 0
    if analyst_rec in ("sell", "underperform") and analyst_n >= 3:
        all_flags.append(
            f"Analyst consensus: '{analyst_rec}' ({analyst_n} analysts) — "
            "Street is negative; our score diverges; verify thesis independently"
        )

    # Margin contraction flags — early warning for quality deterioration
    op_delta = getattr(f, "operating_margin_delta", None)
    gm_delta = getattr(f, "gross_margin_delta", None)
    if op_delta is not None and op_delta < -3.0:
        all_flags.append(
            f"Operating margin contracting {op_delta:+.1f}pp YoY — "
            "cost pressures or contract mix shift; verify in latest 10-Q"
        )
    if gm_delta is not None and gm_delta < -2.0:
        all_flags.append(
            f"Gross margin contracting {gm_delta:+.1f}pp YoY — "
            "pricing power erosion or input cost inflation"
        )

    # Short interest — informed capital expressing a negative view
    short_pct = getattr(f, "short_pct_of_float", None)
    if short_pct is not None and short_pct > 25:
        all_flags.append(
            f"Very high short interest ({short_pct:.1f}% of float) — "
            "significant informed short position against this thesis; "
            "identify and refute the short thesis before deploying capital"
        )
    elif short_pct is not None and short_pct > 15:
        all_flags.append(
            f"Elevated short interest ({short_pct:.1f}% of float) — "
            "meaningful short position; verify thesis is not already consensus"
        )

    # Share dilution — capital allocation failure hidden from ROIC
    shares_chg = getattr(f, "shares_chg_1yr_pct", None)
    if shares_chg is not None and shares_chg > 5:
        all_flags.append(
            f"Share count grew {shares_chg:+.1f}% YoY — dilution is destroying equity value; "
            "check for M&A financing, equity compensation burn, or ongoing raises"
        )

    # Near-term earnings — binary risk to position sizing
    next_earn = getattr(f, "next_earnings_date", None)
    if next_earn:
        try:
            from datetime import datetime as _dt
            days_to_earn = (_dt.strptime(next_earn, "%Y-%m-%d") - _dt.now()).days
            if 0 <= days_to_earn <= 14:
                all_flags.append(
                    f"Earnings report in {days_to_earn} days ({next_earn}) — "
                    "near-term binary catalyst; size position accordingly"
                )
        except Exception:
            pass

    # Specialist tier scoring — apply bonus/penalty before verdict
    specialist = score_specialist_tier(ticker, f, contracts)
    final = _clamp(final + specialist.score_adjustment)

    verdict = determine_verdict(final, f, all_flags)

    # DCF valuation — run after final score, attach result
    has_sole_source = any(c.is_sole_source for c in contracts)
    try:
        dcf_full = _run_dcf(f, sector, contracts_sole_source=has_sole_source,
                              current_price=f.current_price,
                              shares_millions=f.shares_millions)
        # Map to slim model stub for storage
        from src.models import DCFResult as DCFStub
        dcf = DCFStub(
            ticker=ticker,
            verdict=dcf_full.verdict,
            central_estimate=dcf_full.central_estimate,
            fair_value_range_low=dcf_full.fair_value_range_low,
            fair_value_range_high=dcf_full.fair_value_range_high,
            margin_of_safety_base=dcf_full.margin_of_safety_base,
            implied_growth_rate=dcf_full.implied_growth_rate,
            valuation_score=dcf_full.valuation_score,
            valuation_note=dcf_full.valuation_note,
            data_quality=dcf_full.data_quality,
            caveats=dcf_full.caveats,
            discount_rate_base=dcf_full.discount_rate_base,
            discount_rate_adjustments=dcf_full.discount_rate_adjustments,
            bear_iv=dcf_full.bear.intrinsic_value_per_share if dcf_full.bear else None,
            base_iv=dcf_full.base.intrinsic_value_per_share if dcf_full.base else None,
            bull_iv=dcf_full.bull.intrinsic_value_per_share if dcf_full.bull else None,
            bear_mos=dcf_full.bear.margin_of_safety_pct if dcf_full.bear else None,
            bull_mos=dcf_full.bull.margin_of_safety_pct if dcf_full.bull else None,
            bear_growth=dcf_full.bear.revenue_growth_yr1_5 if dcf_full.bear else None,
            base_growth=dcf_full.base.revenue_growth_yr1_5 if dcf_full.base else None,
            bull_growth=dcf_full.bull.revenue_growth_yr1_5 if dcf_full.bull else None,
            current_price=dcf_full.current_price,
        )
    except Exception as e:
        dcf = None

    # Narrative
    why_matters, why_not, risks, verify = _generate_narrative(ticker, f, contracts, sector, bq_raw, gv_raw, ds_raw)

    data_completeness = _compute_data_completeness(f)

    return CompanyScore(
        ticker=ticker,
        company_name=f.company_name or company_name,
        sector=sector,
        buffett_quality=ComponentScore(
            raw=bq_raw, weight=SCORE_WEIGHTS["buffett_quality"],
            explanation=bq_exp, flags=bq_flags
        ),
        graham_value=ComponentScore(
            raw=gv_raw, weight=SCORE_WEIGHTS["graham_value"],
            explanation=gv_exp, flags=gv_flags
        ),
        dod_stability=ComponentScore(
            raw=ds_raw, weight=SCORE_WEIGHTS["dod_stability"],
            explanation=ds_exp, flags=ds_flags
        ),
        management=ComponentScore(
            raw=mq_raw, weight=SCORE_WEIGHTS["management"],
            explanation=mq_exp, flags=mq_flags
        ),
        contract_catalyst=ComponentScore(
            raw=cc_raw, weight=SCORE_WEIGHTS["contract_catalyst"],
            explanation=cc_exp, flags=cc_flags
        ),
        balance_sheet=ComponentScore(
            raw=bs_raw, weight=SCORE_WEIGHTS["balance_sheet"],
            explanation=bs_exp, flags=bs_flags
        ),
        final_score=round(final, 1),
        verdict=verdict,
        overall_explanation=f"Weighted composite: Buffett({bq_raw:.0f}×25%) + Graham({gv_raw:.0f}×20%) + DoD({ds_raw:.0f}×20%) + Mgmt({mq_raw:.0f}×15%) + Catalyst({cc_raw:.0f}×10%) + BS({bs_raw:.0f}×10%) = {final:.1f}",
        recent_contracts=contracts,
        why_it_matters=why_matters,
        why_it_might_not_matter=why_not,
        key_risks=risks,
        what_to_verify=verify,
        red_flags=[flag for flag in all_flags if any(kw in flag.lower() for kw in
            ["score capped", "capped at", "dangerous", "dilution", "negative fcf",
             "negative roe", "contracting", "consensus", "short interest",
             "destroying", "binary catalyst", "distressed"])],
        low_ticker_confidence=any(c.ticker_confidence < OVERRIDE_RULES["low_ticker_confidence_flag_threshold"] for c in contracts if c.ticker == ticker),
        specialist=specialist,
        dcf=dcf,
        data_completeness_pct=data_completeness,
    )


def _generate_narrative(
    ticker: str,
    f: CompanyFundamentals,
    contracts: List[Contract],
    sector: Sector,
    bq: float,
    gv: float,
    ds: float,
) -> tuple:
    """Generate qualitative narrative fields."""
    contract_summary = f"{len(contracts)} contract(s) totaling ${sum(c.contract_value for c in contracts):.0f}M" if contracts else "No contracts"

    # Why it matters
    matters_parts = []
    if ds >= 70:
        matters_parts.append(f"strong government revenue durability ({sector.value})")
    if bq >= 70:
        matters_parts.append("high-quality business characteristics")
    if any(c.is_sole_source for c in contracts):
        matters_parts.append("sole-source contract position implies pricing power and switching costs")
    if _safe(f.backlog_to_revenue) >= 2.0:
        matters_parts.append(f"backlog of {_safe(f.backlog_to_revenue):.1f}x revenue provides multi-year visibility")
    if gv >= 70:
        matters_parts.append("stock appears reasonably valued relative to earnings and cash flow")
    why_matters = (
        f"{contract_summary}. " + (". ".join(matters_parts).capitalize() + "." if matters_parts else "")
    )

    # Why it might not matter
    not_matters_parts = []
    if _safe(f.dod_revenue_pct) < 20:
        not_matters_parts.append(f"DoD revenue is only ~{_safe(f.dod_revenue_pct):.0f}% of total — government contracts may not move the needle materially")
    if _safe(f.market_cap_millions) > 50000:
        not_matters_parts.append("large market cap means individual contracts rarely create meaningful investment signal")
    if any(c.is_idiq for c in contracts) and not any(c.is_sole_source for c in contracts):
        not_matters_parts.append("IDIQ ceiling contracts may have low funded amounts — headline value is aspirational")
    if gv < 50:
        not_matters_parts.append("current valuation may already reflect visible contract pipeline")
    why_not = ". ".join(not_matters_parts) + "." if not_matters_parts else "No major caveats identified at this time."

    # Key risks
    risks = []
    if _safe(f.debt_ebitda) > 3.0:
        risks.append(f"Elevated leverage ({f.debt_ebitda:.1f}x Debt/EBITDA)")
    if f.pe_ratio is not None and f.pe_ratio > 80:
        risks.append(f"Very high valuation (P/E {f.pe_ratio:.0f}x) requires sustained high growth")
    if _safe(f.dod_revenue_pct) > 80:
        risks.append("High government concentration — vulnerable to budget cuts, CR, or program cancellation")
    if sector == Sector.MILITARY_HEALTHCARE:
        risks.append("TRICARE contract recompetes and managed care cost pressures are structural risks")
    if sector in [Sector.AI_DATA_SOFTWARE, Sector.CYBERSECURITY]:
        risks.append("Competitive intensity in defense tech is rising; contract losses can be sudden")
    if not risks:
        risks.append("See individual component flags for specific concerns")

    # What to verify
    verify = [
        f"Confirm fundamentals via latest 10-K/10-Q (data source: {f.data_source})",
        "Verify government revenue % and backlog from most recent earnings call",
        "Check for any contract protest activity (Government Accountability Office database)",
        "Review most recent DoD budget request for program funding continuity",
    ]
    if any(c.is_idiq for c in contracts):
        verify.append("Confirm actual task order funding vs IDIQ ceiling — check USAspending.gov")

    return why_matters, why_not, risks, verify


# ── Specialist Tier Scoring ───────────────────────────────────────────────────

def score_specialist_tier(
    ticker: str,
    f: CompanyFundamentals,
    contracts: List[Contract],
) -> "SpecialistProfile":
    """
    Evaluate whether this company sits in the mid-cap, high-concentration
    'specialist sweet spot' where contract signals are most actionable.

    Returns a SpecialistProfile with a score_adjustment (+0 to +6) that
    is added to the final score in score_company().

    The bonus is intentionally modest — this is a tiebreaker that surfaces
    under-followed names, not a way to make a bad company look good.
    """
    from src.models import SpecialistProfile, SpecialistTierStatus
    from config import SPECIALIST_TIER, LARGE_CAP_PRIMES

    mc    = f.market_cap_millions
    dod   = f.dod_revenue_pct
    rev   = f.annual_revenue_millions
    ss    = any(c.is_sole_source for c in contracts)
    total_cv = sum(c.contract_value for c in contracts)
    cv_to_rev = (total_cv / rev * 100) if rev else None

    floor   = SPECIALIST_TIER["market_cap_floor_millions"]
    ceiling = SPECIALIST_TIER["market_cap_ceiling_millions"]
    ss_ceil = SPECIALIST_TIER["sole_source_ceiling_override_millions"]
    min_dod = SPECIALIST_TIER["min_dod_revenue_pct"]
    min_cvr = SPECIALIST_TIER["min_contract_to_revenue_pct"]
    bonus_in   = SPECIALIST_TIER["score_bonus_in_tier"]
    bonus_near = SPECIALIST_TIER["score_bonus_near_tier"]

    # ── Disqualifiers ─────────────────────────────────────────────────────────
    if ticker.upper() in LARGE_CAP_PRIMES:
        return SpecialistProfile(
            status=SpecialistTierStatus.LARGE_PRIME,
            market_cap_millions=mc,
            dod_revenue_pct=dod,
            contract_to_revenue_pct=cv_to_rev,
            is_sole_source=ss,
            score_adjustment=0.0,
            rationale=(
                f"{ticker} is a large-cap defense prime with extensive institutional coverage. "
                "Contract awards are typically priced in quickly. "
                "No specialist tier bonus applied."
            ),
            analyst_coverage_note="20+ sell-side analysts; contract news reaches consensus models same day.",
        )

    if mc is not None and mc < floor:
        return SpecialistProfile(
            status=SpecialistTierStatus.TOO_SMALL,
            market_cap_millions=mc,
            dod_revenue_pct=dod,
            contract_to_revenue_pct=cv_to_rev,
            is_sole_source=ss,
            score_adjustment=0.0,
            rationale=(
                f"Market cap ${mc:.0f}M is below the ${floor:.0f}M liquidity floor. "
                "Position sizing, bid-ask spreads, and institutional accessibility are concerns."
            ),
            analyst_coverage_note="Micro-cap; limited institutional coverage.",
        )

    if dod is not None and dod < min_dod:
        # Check if at least somewhat meaningful
        near = dod >= (min_dod * 0.6)
        return SpecialistProfile(
            status=SpecialistTierStatus.NEAR_TIER if near else SpecialistTierStatus.LOW_GOV_CONC,
            market_cap_millions=mc,
            dod_revenue_pct=dod,
            contract_to_revenue_pct=cv_to_rev,
            is_sole_source=ss,
            score_adjustment=bonus_near * 0.5 if near else 0.0,
            rationale=(
                f"DoD revenue is ~{dod:.0f}% of total — below the {min_dod:.0f}% threshold "
                "for the specialist filter. Government contracts are a secondary revenue stream. "
                + ("Partial credit applied — concentration is meaningful even if below threshold." if near else
                   "Primary thesis is commercial; DoD exposure is a small bonus.")
            ),
            analyst_coverage_note="Commercial-dominant business; DoD contracts tracked by a subset of analysts.",
        )

    # ── Sole-source ceiling override ──────────────────────────────────────────
    # A company above the market cap ceiling can still qualify if it has
    # uniquely specialized, sole-source work (e.g. BWXT for naval nuclear).
    above_ceiling = mc is not None and mc > ceiling
    sole_source_override = above_ceiling and ss and mc <= ss_ceil

    # ── In-tier check ─────────────────────────────────────────────────────────
    size_ok = (mc is None) or (floor <= mc <= ceiling) or sole_source_override
    dod_ok  = (dod is None) or (dod >= min_dod)
    cvr_ok  = (cv_to_rev is None) or (cv_to_rev >= min_cvr)

    # Build dimension-by-dimension rationale
    size_note = (
        f"Market cap ${mc:.0f}M — squarely in mid-cap sweet spot." if mc and floor <= mc <= ceiling
        else f"Market cap ${mc:.0f}M — above ceiling but sole-source override applies." if sole_source_override
        else f"Market cap unknown — cannot confirm size." if mc is None
        else f"Market cap ${mc:.0f}M — outside ideal band."
    )
    dod_note = (
        f"DoD revenue ~{dod:.0f}% — strong government concentration." if dod and dod >= 60
        else f"DoD revenue ~{dod:.0f}% — solid concentration above threshold." if dod and dod >= min_dod
        else "DoD revenue concentration unknown."
    )
    cvr_note = (
        f"Contracts represent ~{cv_to_rev:.1f}% of annual revenue — highly material." if cv_to_rev is not None and cv_to_rev >= 10
        else f"Contracts represent ~{cv_to_rev:.1f}% of annual revenue — meaningful signal." if cv_to_rev is not None and cv_to_rev >= min_cvr
        else f"Contracts represent ~{cv_to_rev:.1f}% of annual revenue — below materiality threshold." if cv_to_rev is not None
        else "Revenue data unavailable — contract materiality cannot be assessed."
    )
    ss_note = "Sole-source position implies pricing power, switching costs, and durable moat." if ss else ""

    # ── Score adjustment ──────────────────────────────────────────────────────
    if size_ok and dod_ok and cvr_ok:
        # Full in-tier bonus — scale it by how deep in the sweet spot
        bonus = bonus_in
        # Extra credit for sole-source + very high concentration
        if ss and dod and dod >= 70:
            bonus = min(bonus_in + 2.0, 10.0)
        # Slight reduction if size data is missing
        if mc is None:
            bonus = bonus_near
        status = SpecialistTierStatus.IN_TIER

    elif (size_ok and dod_ok) or (size_ok and cvr_ok) or (dod_ok and cvr_ok):
        # Two of three dimensions met
        bonus = bonus_near
        status = SpecialistTierStatus.NEAR_TIER

    else:
        bonus = 0.0
        status = SpecialistTierStatus.NEAR_TIER if size_ok or dod_ok else SpecialistTierStatus.LOW_GOV_CONC

    # Estimate analyst coverage band
    if mc:
        if mc < 2000:
            cov_note = "Small-mid cap; typically 3–8 sell-side analysts. Contract news may not reach models immediately."
        elif mc < 8000:
            cov_note = "Mid-cap; typically 8–15 analysts. Meaningful contract wins can move consensus estimates."
        else:
            cov_note = "Large-mid cap; 15–20+ analysts. Well-followed but not as saturated as mega-cap primes."
    else:
        cov_note = "Coverage unknown — verify analyst count independently."

    rationale_parts = [size_note, dod_note, cvr_note]
    if ss_note:
        rationale_parts.append(ss_note)
    if bonus > 0:
        rationale_parts.append(
            f"Specialist tier bonus of +{bonus:.1f} pts applied to final score."
        )

    return SpecialistProfile(
        status=status,
        market_cap_millions=mc,
        dod_revenue_pct=dod,
        contract_to_revenue_pct=round(cv_to_rev, 1) if cv_to_rev is not None else None,
        is_sole_source=ss,
        score_adjustment=round(bonus, 1),
        rationale=" ".join(rationale_parts),
        analyst_coverage_note=cov_note,
    )
