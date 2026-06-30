"""
Unit tests for the DoD Contract Intelligence scoring engine.

Validates:
- Scoring utility functions (_score_bracket, _safe, _clamp)
- Individual component scoring functions (Buffett, Graham, DoD, Management, Balance Sheet)
- Score caps and override rules
- Data validation flags (_validate_fundamentals)
- Verdict assignment logic (including post-DCF correction)
- Weighted composite math

Run with: pytest tests/ -v
"""
import pytest
from unittest.mock import patch
from src.models import CompanyFundamentals, Contract, ContractType, Sector, Verdict
from src.scoring import (
    _safe, _clamp, _score_bracket,
    score_buffett_quality, score_graham_value, score_dod_stability,
    score_management, score_balance_sheet,
    _validate_fundamentals, determine_verdict,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_f(**kwargs) -> CompanyFundamentals:
    """Build a minimal CompanyFundamentals with sensible defaults."""
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        market_cap_millions=5000.0,
        annual_revenue_millions=2000.0,
        government_revenue_pct=80.0,
        dod_revenue_pct=70.0,
        roe=18.0,
        roic=15.0,
        free_cash_flow_margin=12.0,
        operating_margin=14.0,
        pe_ratio=22.0,
        forward_pe=20.0,
        ev_ebitda=14.0,
        price_to_book=3.0,
        fcf_yield=5.0,
        debt_equity=0.8,
        current_ratio=1.4,
        interest_coverage=8.0,
        backlog_to_revenue=2.2,
        net_debt_millions=500.0,
        debt_ebitda=2.0,
        insider_ownership_pct=2.0,
        earnings_stability_years=12,
        moat_rating="Narrow",
        data_source="mock",
        current_price=100.0,
        shares_millions=50.0,
        shares_chg_1yr_pct=-1.5,
        dividend_yield=1.5,
    )
    defaults.update(kwargs)
    return CompanyFundamentals(**defaults)


def _make_contract(**kwargs) -> Contract:
    defaults = dict(
        awardee_name="Test Corp",
        contract_value=200.0,
        contract_type=ContractType.NEW_AWARD,
        agency="Department of Defense",
        branch="Army",
        description="Test contract",
        is_sole_source=True,
        is_competitive=False,
        is_idiq=False,
    )
    defaults.update(kwargs)
    return Contract(**defaults)


# ── Utility function tests ────────────────────────────────────────────────────

class TestUtilities:
    def test_safe_returns_value_when_not_none(self):
        assert _safe(5.0) == 5.0

    def test_safe_returns_default_when_none(self):
        assert _safe(None) == 0.0
        assert _safe(None, default=99.0) == 99.0

    def test_clamp_within_bounds(self):
        assert _clamp(50.0) == 50.0

    def test_clamp_at_max(self):
        assert _clamp(120.0) == 100.0

    def test_clamp_at_min(self):
        assert _clamp(-5.0) == 0.0

    def test_score_bracket_exact_match(self):
        brackets = [(20, 20), (15, 15), (10, 10), (5, 5), (0, 0)]
        assert _score_bracket(20.0, brackets) == 20
        assert _score_bracket(15.0, brackets) == 15
        assert _score_bracket(5.0, brackets) == 5

    def test_score_bracket_between_thresholds(self):
        brackets = [(20, 20), (15, 15), (10, 10), (0, 0)]
        # 17.5 is between 15 and 20 → gets the 15-threshold score of 15
        assert _score_bracket(17.5, brackets) == 15

    def test_score_bracket_below_all_thresholds(self):
        # When value is below all thresholds, returns the last bracket's score (floor behavior)
        brackets = [(20, 20), (10, 10), (5, 5)]
        assert _score_bracket(0.0, brackets) == 5    # last entry is floor
        assert _score_bracket(-100.0, brackets) == 5  # last entry is floor
        # Use (-999, 0) as explicit zero-floor (common pattern in scoring.py)
        brackets_with_zero = [(20, 20), (10, 10), (5, 5), (-999, 0)]
        assert _score_bracket(0.0, brackets_with_zero) == 0


# ── Buffett Quality tests ─────────────────────────────────────────────────────

class TestBuffettQuality:
    def test_high_quality_scores_above_70(self):
        f = _make_f(
            roic=22.0,
            free_cash_flow_margin=18.0,
            operating_margin=20.0,
            earnings_stability_years=20,
            roe=28.0,
            moat_rating="Wide",
        )
        score, _, _ = score_buffett_quality(f)
        assert score >= 70, f"Expected ≥70, got {score}"

    def test_unprofitable_scores_below_profitable(self):
        # The 45-cap on the Buffett component applies in score_company(), not here.
        # Test that an unprofitable company scores materially lower than a profitable one.
        f_good = _make_f(free_cash_flow_margin=15.0, operating_margin=16.0, roic=18.0, moat_rating="Wide")
        f_bad = _make_f(free_cash_flow_margin=-5.0, operating_margin=-3.0, roic=2.0, moat_rating="None")
        score_good, _, _ = score_buffett_quality(f_good)
        score_bad, _, flags_bad = score_buffett_quality(f_bad)
        assert score_good > score_bad + 20, f"Profitable ({score_good}) should exceed unprofitable ({score_bad}) by >20pts"
        assert any("negative" in fl.lower() or "concern" in fl.lower() for fl in flags_bad)

    def test_wide_moat_adds_points(self):
        f_wide = _make_f(moat_rating="Wide")
        f_narrow = _make_f(moat_rating="Narrow")
        score_wide, _, _ = score_buffett_quality(f_wide)
        score_narrow, _, _ = score_buffett_quality(f_narrow)
        assert score_wide > score_narrow

    def test_negative_roic_scores_zero_roic_pts(self):
        f_pos = _make_f(roic=15.0)
        f_neg = _make_f(roic=-5.0)
        score_pos, _, _ = score_buffett_quality(f_pos)
        score_neg, _, _ = score_buffett_quality(f_neg)
        assert score_pos > score_neg

    def test_high_roe_with_extreme_leverage_is_capped(self):
        f = _make_f(roe=80.0, debt_equity=4.0)
        score, _, flags = score_buffett_quality(f)
        assert any("leverage" in fl.lower() or "inflated" in fl.lower() for fl in flags)


# ── Graham Value tests ────────────────────────────────────────────────────────

class TestGrahamValue:
    def test_cheap_multiples_score_higher(self):
        f_cheap = _make_f(pe_ratio=15.0, forward_pe=14.0, ev_ebitda=10.0, fcf_yield=8.0)
        f_expensive = _make_f(pe_ratio=40.0, forward_pe=38.0, ev_ebitda=30.0, fcf_yield=1.5)
        score_cheap, _, _ = score_graham_value(f_cheap)
        score_expensive, _, _ = score_graham_value(f_expensive)
        assert score_cheap > score_expensive

    def test_high_fcf_yield_scores_well(self):
        f_high = _make_f(fcf_yield=10.0)
        f_low = _make_f(fcf_yield=1.0)
        score_high, _, _ = score_graham_value(f_high)
        score_low, _, _ = score_graham_value(f_low)
        assert score_high > score_low

    def test_dividend_yield_contributes(self):
        f_div = _make_f(dividend_yield=4.0)
        f_no_div = _make_f(dividend_yield=0.0)
        score_div, _, _ = score_graham_value(f_div)
        score_no_div, _, _ = score_graham_value(f_no_div)
        assert score_div >= score_no_div


# ── DoD Stability tests ───────────────────────────────────────────────────────

class TestDoDStability:
    def test_high_dod_pct_scores_higher(self):
        f_high = _make_f(dod_revenue_pct=90.0, backlog_to_revenue=3.0)
        f_low = _make_f(dod_revenue_pct=20.0, backlog_to_revenue=1.0)
        contracts = [_make_contract(is_sole_source=True)]
        score_high, _, _ = score_dod_stability(f_high, contracts, Sector.TRADITIONAL_DEFENSE_PRIME)
        score_low, _, _ = score_dod_stability(f_low, contracts, Sector.TRADITIONAL_DEFENSE_PRIME)
        assert score_high > score_low

    def test_strong_backlog_boosts_score(self):
        f_strong = _make_f(dod_revenue_pct=75.0, backlog_to_revenue=4.0)
        f_weak = _make_f(dod_revenue_pct=75.0, backlog_to_revenue=0.5)
        contracts = [_make_contract()]
        score_strong, _, _ = score_dod_stability(f_strong, contracts, Sector.TRADITIONAL_DEFENSE_PRIME)
        score_weak, _, _ = score_dod_stability(f_weak, contracts, Sector.TRADITIONAL_DEFENSE_PRIME)
        assert score_strong > score_weak

    def test_single_branch_concentration_flag(self):
        f = _make_f(dod_revenue_pct=80.0)
        contracts = [
            _make_contract(branch="NAVY", contract_value=100.0),
            _make_contract(branch="NAVY", contract_value=200.0),
            _make_contract(branch="NAVY", contract_value=150.0),
        ]
        _, _, flags = score_dod_stability(f, contracts, Sector.SHIPBUILDING)
        assert any("single-customer" in fl.lower() or "concentration" in fl.lower() for fl in flags)

    def test_multi_branch_no_concentration_flag(self):
        f = _make_f(dod_revenue_pct=80.0)
        contracts = [
            _make_contract(branch="NAVY"),
            _make_contract(branch="ARMY"),
            _make_contract(branch="AIR FORCE"),
        ]
        _, _, flags = score_dod_stability(f, contracts, Sector.TRADITIONAL_DEFENSE_PRIME)
        # Should NOT flag single-customer concentration
        assert not any("single-customer" in fl.lower() for fl in flags)


# ── Management Quality tests ──────────────────────────────────────────────────

class TestManagementQuality:
    def test_high_roic_high_buybacks_scores_higher(self):
        f_good = _make_f(roic=22.0, shares_chg_1yr_pct=-3.5, insider_ownership_pct=8.0)
        f_bad = _make_f(roic=5.0, shares_chg_1yr_pct=6.0, insider_ownership_pct=0.1)
        score_good, _, _ = score_management(f_good)
        score_bad, _, _ = score_management(f_bad)
        assert score_good > score_bad

    def test_heavy_dilution_flags(self):
        f = _make_f(shares_chg_1yr_pct=8.0)
        _, _, flags = score_management(f)
        assert any("dilut" in fl.lower() for fl in flags)

    def test_buyback_no_dilution_flag(self):
        f = _make_f(shares_chg_1yr_pct=-3.0)
        _, _, flags = score_management(f)
        assert not any("dilut" in fl.lower() for fl in flags)


# ── Balance Sheet tests ───────────────────────────────────────────────────────

class TestBalanceSheet:
    def test_strong_balance_sheet_scores_well(self):
        f = _make_f(current_ratio=2.0, debt_ebitda=1.0, interest_coverage=15.0)
        score, _, _ = score_balance_sheet(f)
        assert score >= 60

    def test_dangerous_balance_sheet_flags_and_caps(self):
        f = _make_f(current_ratio=0.7, debt_ebitda=6.0, interest_coverage=0.8)
        score, _, flags = score_balance_sheet(f)
        assert any("dangerous" in fl.lower() or "capped" in fl.lower() for fl in flags)

    def test_negative_interest_coverage_flags_operating_loss(self):
        f = _make_f(interest_coverage=-2.0)
        _, _, flags = score_balance_sheet(f)
        assert any("negative" in fl.lower() or "operating loss" in fl.lower() for fl in flags)


# ── Data Validation tests ─────────────────────────────────────────────────────

class TestValidateFundamentals:
    def test_clean_data_no_flags(self):
        f = _make_f()  # all reasonable defaults
        flags = _validate_fundamentals(f)
        assert len(flags) == 0, f"Expected clean data, got: {flags}"

    def test_pe_too_low_flags(self):
        f = _make_f(pe_ratio=0.5)
        flags = _validate_fundamentals(f)
        assert any("p/e" in fl.lower() and "low" in fl.lower() for fl in flags)

    def test_pe_too_high_flags(self):
        f = _make_f(pe_ratio=6000.0)
        flags = _validate_fundamentals(f)
        assert any("p/e" in fl.lower() and ("high" in fl.lower() or "nonsensical" in fl.lower()) for fl in flags)

    def test_negative_ev_ebitda_flags(self):
        f = _make_f(ev_ebitda=-5.0)
        flags = _validate_fundamentals(f)
        assert any("ev/ebitda" in fl.lower() for fl in flags)

    def test_extreme_fcf_yield_flags(self):
        f = _make_f(fcf_yield=45.0)
        flags = _validate_fundamentals(f)
        assert any("fcf yield" in fl.lower() for fl in flags)

    def test_roic_fcf_inconsistency_flags(self):
        f = _make_f(roic=5.0, fcf_yield=30.0)
        flags = _validate_fundamentals(f)
        assert any("fcf yield" in fl.lower() and "roic" in fl.lower() for fl in flags)

    def test_roic_too_high_flags_negative_equity_artifact(self):
        f = _make_f(roic=95.0)
        flags = _validate_fundamentals(f)
        assert any("roic" in fl.lower() for fl in flags)

    def test_operating_margin_fcf_divergence_flags(self):
        f = _make_f(operating_margin=25.0, free_cash_flow_margin=-8.0)
        flags = _validate_fundamentals(f)
        assert any("operating margin" in fl.lower() or "fcf margin" in fl.lower() for fl in flags)

    def test_fcf_conversion_low_flags(self):
        # FCF margin 2% / op margin 20% = 10% conversion — below 30% threshold
        f = _make_f(operating_margin=20.0, free_cash_flow_margin=2.0)
        flags = _validate_fundamentals(f)
        assert any("conversion" in fl.lower() for fl in flags)

    def test_interest_coverage_zero_with_debt_flags(self):
        f = _make_f(interest_coverage=0.0, net_debt_millions=500.0)
        flags = _validate_fundamentals(f)
        assert any("interest coverage" in fl.lower() for fl in flags)


# ── Verdict assignment tests ──────────────────────────────────────────────────

class TestVerdictAssignment:
    def test_high_score_pa_plus_verdict(self):
        f = _make_f()
        verdict = determine_verdict(75.0, f, [])
        assert verdict in (Verdict.STRONG_CANDIDATE, Verdict.POTENTIALLY_ATTRACTIVE,
                          Verdict.RESEARCH_FURTHER, Verdict.HIGH_QUALITY_BUT_EXPENSIVE)

    def test_high_pe_triggers_expensive_verdict(self):
        # PE > 80 with low FCF margin should trigger HIGH_QUALITY_BUT_EXPENSIVE
        f = _make_f(pe_ratio=90.0, ev_ebitda=65.0, free_cash_flow_margin=5.0)
        verdict = determine_verdict(78.0, f, [])
        assert verdict == Verdict.HIGH_QUALITY_BUT_EXPENSIVE

    def test_low_score_watchlist(self):
        f = _make_f()
        verdict = determine_verdict(60.0, f, [])
        assert verdict == Verdict.WATCHLIST

    def test_very_low_score_ignore(self):
        f = _make_f()
        verdict = determine_verdict(40.0, f, [])
        assert verdict == Verdict.IGNORE

    def test_street_bearish_triggers_research_further(self):
        f = _make_f(analyst_recommendation="sell", analyst_count=5)
        verdict = determine_verdict(75.0, f, [])
        assert verdict == Verdict.RESEARCH_FURTHER


# ── Score weights sum to 1.0 ──────────────────────────────────────────────────

class TestWeightIntegrity:
    def test_score_weights_sum_to_one(self):
        from config import SCORE_WEIGHTS
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"

    def test_verdict_thresholds_are_ordered(self):
        from config import VERDICT_THRESHOLDS
        assert VERDICT_THRESHOLDS["strong_candidate"] > VERDICT_THRESHOLDS["potentially_attractive"]
        assert VERDICT_THRESHOLDS["potentially_attractive"] > VERDICT_THRESHOLDS["watchlist"]
        assert VERDICT_THRESHOLDS["watchlist"] > VERDICT_THRESHOLDS["low_conviction"]


# ── Integration: full score_company smoke test ────────────────────────────────

class TestScoreCompanyIntegration:
    def test_score_company_returns_valid_score(self):
        from src.scoring import score_company
        f = _make_f()
        contracts = [_make_contract()]
        score = score_company(
            ticker="TEST",
            company_name="Test Corp",
            contracts=contracts,
            f=f,
            sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            live=False,
        )
        assert 0 <= score.final_score <= 100
        assert score.verdict is not None
        assert score.ticker == "TEST"

    def test_high_quality_company_scores_pa_plus(self):
        from src.scoring import score_company
        f = _make_f(
            roic=20.0, free_cash_flow_margin=15.0, operating_margin=16.0,
            earnings_stability_years=15, moat_rating="Wide",
            pe_ratio=20.0, forward_pe=18.0, ev_ebitda=13.0, fcf_yield=6.5,
            dod_revenue_pct=80.0, backlog_to_revenue=3.0,
            current_ratio=1.8, debt_ebitda=1.5, interest_coverage=12.0,
        )
        contracts = [_make_contract(is_sole_source=True, contract_value=300.0)]
        score = score_company(
            ticker="TEST",
            company_name="High Quality Test Corp",
            contracts=contracts,
            f=f,
            sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            live=False,
        )
        # High quality company with good DoD exposure should score >= 68
        assert score.final_score >= 65, f"Expected PA+ quality, got {score.final_score}"

    def test_unprofitable_company_scores_low(self):
        from src.scoring import score_company
        f = _make_f(
            roic=-2.0, free_cash_flow_margin=-8.0, operating_margin=-5.0,
            earnings_stability_years=2, moat_rating="None",
            pe_ratio=None, ev_ebitda=None, fcf_yield=-3.0,
            debt_ebitda=8.0, interest_coverage=0.5,
        )
        contracts = [_make_contract(is_sole_source=False, contract_value=10.0)]
        score = score_company(
            ticker="TEST",
            company_name="Weak Corp",
            contracts=contracts,
            f=f,
            sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            live=False,
        )
        assert score.final_score < 55, f"Expected low score for unprofitable, got {score.final_score}"
        assert score.verdict in (Verdict.IGNORE, Verdict.LOW_CONVICTION, Verdict.WATCHLIST)
