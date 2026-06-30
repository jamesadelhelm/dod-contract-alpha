"""
Unit tests for the DCF valuation module.

Validates:
- _dcf_calc: core 10-year DCF math (IV, MoS, terminal value)
- _owner_earnings: FCF proxy derivation
- _discount_rate: WACC adjustments
- _growth_assumptions: sector/data-blend logic
- _terminal_growth: sector defaults
- run_dcf: integration (scenarios, reverse DCF, verdict)

Run with: pytest tests/ -v
"""
import pytest
from src.models import CompanyFundamentals, Sector
from src.dcf import (
    run_dcf,
    _owner_earnings,
    _discount_rate,
    _growth_assumptions,
    _terminal_growth,
    _dcf_calc,
    _reverse_dcf,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_f(**kwargs) -> CompanyFundamentals:
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        annual_revenue_millions=1000.0,
        free_cash_flow_margin=10.0,
        operating_margin=12.0,
        market_cap_millions=5000.0,
        current_price=50.0,
        shares_millions=100.0,
        dod_revenue_pct=70.0,
        government_revenue_pct=75.0,
        moat_rating="Narrow",
        debt_equity=0.5,
        roic=12.0,
        roe=15.0,
    )
    defaults.update(kwargs)
    return CompanyFundamentals(**defaults)


# ── Owner Earnings ─────────────────────────────────────────────────────────────

class TestOwnerEarnings:
    def test_basic_oe_from_fcf_and_revenue(self):
        f = _make_f(annual_revenue_millions=1000.0, free_cash_flow_margin=10.0)
        oe, note = _owner_earnings(f, [])
        assert oe == 100.0  # 10% of $1000M

    def test_negative_fcf_returns_none(self):
        f = _make_f(annual_revenue_millions=1000.0, free_cash_flow_margin=-5.0)
        oe, note = _owner_earnings(f, [])
        assert oe is None

    def test_missing_revenue_returns_none(self):
        f = _make_f(annual_revenue_millions=None, free_cash_flow_margin=10.0)
        oe, note = _owner_earnings(f, [])
        assert oe is None

    def test_missing_fcf_margin_returns_none(self):
        f = _make_f(annual_revenue_millions=1000.0, free_cash_flow_margin=None)
        oe, note = _owner_earnings(f, [])
        assert oe is None

    def test_zero_fcf_margin_returns_none(self):
        # Zero FCF means zero owner earnings, which is falsy → treated as negative
        f = _make_f(annual_revenue_millions=1000.0, free_cash_flow_margin=0.0)
        oe, note = _owner_earnings(f, [])
        assert oe is None  # 0.0 is not > 0


# ── Discount Rate ─────────────────────────────────────────────────────────────

class TestDiscountRate:
    def test_base_rate_is_nine(self):
        # Narrow moat, moderate DoD (50%), no leverage, large cap → small adjustments
        f = _make_f(dod_revenue_pct=50.0, moat_rating="Narrow", debt_equity=0.3,
                    market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate, adj = _discount_rate(f, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        # Narrow moat = no adjustment, 50% DoD = -0.5%, large cap = no size premium
        assert abs(rate - 8.5) < 0.1

    def test_wide_moat_reduces_rate(self):
        f_wide = _make_f(dod_revenue_pct=60.0, moat_rating="Wide",
                         market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        f_none = _make_f(dod_revenue_pct=60.0, moat_rating="None",
                         market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate_wide, _ = _discount_rate(f_wide, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        rate_none, _ = _discount_rate(f_none, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        assert rate_wide < rate_none

    def test_sole_source_high_dod_reduces_rate(self):
        f_base = _make_f(dod_revenue_pct=75.0, moat_rating="Narrow",
                         market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate_no_ss, _ = _discount_rate(f_base, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        rate_ss, _ = _discount_rate(f_base, Sector.TRADITIONAL_DEFENSE_PRIME, True)
        # Sole source with >=70% DoD gives extra -0.5pp (total -1.0pp instead of -0.5pp)
        assert rate_ss < rate_no_ss

    def test_small_cap_premium(self):
        f_small = _make_f(dod_revenue_pct=50.0, moat_rating="Narrow",
                          market_cap_millions=500.0, free_cash_flow_margin=10.0)
        f_large = _make_f(dod_revenue_pct=50.0, moat_rating="Narrow",
                          market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate_small, _ = _discount_rate(f_small, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        rate_large, _ = _discount_rate(f_large, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        assert rate_small > rate_large + 1.0  # at least 1.5pp size premium

    def test_low_dod_adds_premium(self):
        f = _make_f(dod_revenue_pct=10.0, moat_rating="Narrow",
                    market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate, adj = _discount_rate(f, Sector.AI_DATA_SOFTWARE, False)
        # Very low DoD (<15%) = +3.0pp model risk premium
        assert any("+3.0%" in a for a in adj)

    def test_negative_fcf_adds_premium(self):
        f = _make_f(dod_revenue_pct=60.0, moat_rating="Narrow",
                    market_cap_millions=10_000.0, free_cash_flow_margin=-5.0)
        rate_neg, _ = _discount_rate(f, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        f_pos = _make_f(dod_revenue_pct=60.0, moat_rating="Narrow",
                        market_cap_millions=10_000.0, free_cash_flow_margin=10.0)
        rate_pos, _ = _discount_rate(f_pos, Sector.TRADITIONAL_DEFENSE_PRIME, False)
        assert rate_neg > rate_pos + 1.5


# ── Terminal Growth ───────────────────────────────────────────────────────────

class TestTerminalGrowth:
    def test_nuclear_higher_than_service(self):
        f = _make_f()
        tg_nuclear = _terminal_growth(Sector.ENERGY_NUCLEAR, f)
        tg_service = _terminal_growth(Sector.CONSULTING_SERVICES, f)
        assert tg_nuclear >= tg_service

    def test_terminal_growth_bounded(self):
        for sector in [Sector.TRADITIONAL_DEFENSE_PRIME, Sector.AI_DATA_SOFTWARE,
                       Sector.SPACE, Sector.SHIPBUILDING, Sector.ENERGY_NUCLEAR]:
            f = _make_f()
            tg = _terminal_growth(sector, f)
            # Terminal growth should be reasonable: 1.5% to 4%
            assert 1.0 <= tg <= 5.0, f"{sector}: terminal growth {tg} out of reasonable range"


# ── DCF Calculation ───────────────────────────────────────────────────────────

class TestDcfCalc:
    def test_positive_iv_profitable_company(self):
        # $100M owner earnings, 8% growth for 10yr, 9% WACC, 3% terminal growth
        # 100M shares, $50 price
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=100.0,
            base_revenue=1000.0,
            growth_yr1_5=8.0,
            growth_yr6_10=4.0,
            terminal_growth=3.0,
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=100.0,
            current_price=50.0,
        )
        assert total_iv is not None and total_iv > 0
        assert iv_per_share is not None and iv_per_share > 0
        assert mos is not None

    def test_mos_positive_when_undervalued(self):
        # Force very high IV by using low discount rate and high growth
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=500.0,  # massive earnings for small cap
            base_revenue=5000.0,
            growth_yr1_5=10.0,
            growth_yr6_10=6.0,
            terminal_growth=3.0,
            fcf_margin=10.0,
            discount_rate=8.0,
            shares_millions=100.0,
            current_price=10.0,  # very cheap vs $500M earnings
        )
        assert mos is not None and mos > 0

    def test_mos_negative_when_overvalued(self):
        # Very small earnings relative to high price
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=1.0,
            base_revenue=100.0,
            growth_yr1_5=5.0,
            growth_yr6_10=3.0,
            terminal_growth=2.0,
            fcf_margin=1.0,
            discount_rate=10.0,
            shares_millions=100.0,
            current_price=500.0,  # absurdly high vs $1M earnings
        )
        assert mos is not None and mos < 0

    def test_high_discount_rate_reduces_iv(self):
        kwargs = dict(
            base_earnings=100.0,
            base_revenue=1000.0,
            growth_yr1_5=8.0,
            growth_yr6_10=4.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
            shares_millions=100.0,
            current_price=50.0,
        )
        iv_low, _, _ = _dcf_calc(**kwargs, discount_rate=8.0)
        iv_high, _, _ = _dcf_calc(**kwargs, discount_rate=12.0)
        assert iv_low > iv_high

    def test_higher_growth_increases_iv(self):
        kwargs = dict(
            base_earnings=100.0,
            base_revenue=1000.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=100.0,
            current_price=50.0,
        )
        iv_slow, _, _ = _dcf_calc(**kwargs, growth_yr1_5=2.0, growth_yr6_10=2.0)
        iv_fast, _, _ = _dcf_calc(**kwargs, growth_yr1_5=10.0, growth_yr6_10=6.0)
        assert iv_fast > iv_slow

    def test_no_shares_returns_none_per_share(self):
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=100.0,
            base_revenue=1000.0,
            growth_yr1_5=5.0,
            growth_yr6_10=3.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=None,
            current_price=50.0,
        )
        assert total_iv is not None
        assert iv_per_share is None
        assert mos is None

    def test_discount_rate_lte_terminal_growth_uses_conservative_tv(self):
        # When r <= tg the formula breaks — should use fallback (no exception)
        total_iv, _, _ = _dcf_calc(
            base_earnings=100.0,
            base_revenue=1000.0,
            growth_yr1_5=5.0,
            growth_yr6_10=3.0,
            terminal_growth=10.0,  # terminal growth > discount rate
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=100.0,
            current_price=50.0,
        )
        assert total_iv is not None and total_iv > 0  # no exception, conservative result

    def test_revenue_based_projection(self):
        # use_revenue_based=True: should project revenue then apply FCF margin
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=None,
            base_revenue=1000.0,
            growth_yr1_5=5.0,
            growth_yr6_10=3.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=100.0,
            current_price=50.0,
            use_revenue_based=True,
        )
        assert total_iv is not None and total_iv > 0

    def test_zero_base_revenue_returns_none(self):
        total_iv, iv_per_share, mos = _dcf_calc(
            base_earnings=None,
            base_revenue=0.0,
            growth_yr1_5=5.0,
            growth_yr6_10=3.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
            discount_rate=9.0,
            shares_millions=100.0,
            current_price=50.0,
            use_revenue_based=True,
        )
        assert total_iv is None


# ── Reverse DCF ───────────────────────────────────────────────────────────────

class TestReverseDcf:
    def test_implied_growth_is_reasonable(self):
        # For a moderately valued company, implied growth should be in single/low-double digits
        implied_g = _reverse_dcf(
            current_price=50.0,
            base_earnings=100.0,  # $100M earnings
            shares_millions=100.0,  # 100M shares → $1 EPS
            discount_rate=9.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
        )
        # $50 price vs $1 EPS = 50x PE. Implied growth will be high but should be finite.
        assert implied_g is not None
        assert 0 < implied_g < 100  # sanity range: 0-100% implied growth

    def test_cheap_price_implies_low_growth(self):
        implied_low_price = _reverse_dcf(
            current_price=5.0,
            base_earnings=100.0,
            shares_millions=100.0,
            discount_rate=9.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
        )
        implied_high_price = _reverse_dcf(
            current_price=500.0,
            base_earnings=100.0,
            shares_millions=100.0,
            discount_rate=9.0,
            terminal_growth=2.5,
            fcf_margin=10.0,
        )
        # A lower price should imply less growth is priced in
        if implied_low_price is not None and implied_high_price is not None:
            assert implied_low_price < implied_high_price


# ── Integration: run_dcf ─────────────────────────────────────────────────────

class TestRunDcf:
    def test_high_quality_defense_prime_produces_three_scenarios(self):
        f = _make_f(
            ticker="GD",
            annual_revenue_millions=42_000.0,
            free_cash_flow_margin=8.0,
            dod_revenue_pct=75.0,
            market_cap_millions=48_000.0,
            current_price=300.0,
            shares_millions=260.0,
            moat_rating="Narrow",
            debt_equity=0.6,
            revenue_cagr_3yr=5.0,
            revenue_growth_forward=4.5,
            revenue_growth_1yr=5.5,
        )
        result = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME, contracts_sole_source=False)
        assert result.bear is not None
        assert result.base is not None
        assert result.bull is not None

    def test_bear_iv_lt_base_lt_bull(self):
        f = _make_f(
            annual_revenue_millions=5_000.0,
            free_cash_flow_margin=10.0,
            dod_revenue_pct=70.0,
            market_cap_millions=8_000.0,
            current_price=80.0,
            shares_millions=100.0,
            moat_rating="Narrow",
        )
        result = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME)
        if all(s is not None for s in [result.bear, result.base, result.bull]):
            b_iv = result.bear.intrinsic_value_per_share
            m_iv = result.base.intrinsic_value_per_share
            u_iv = result.bull.intrinsic_value_per_share
            if all(v is not None for v in [b_iv, m_iv, u_iv]):
                assert b_iv <= m_iv <= u_iv, "Bear IV should be ≤ Base IV ≤ Bull IV"

    def test_missing_revenue_gives_insufficient_quality(self):
        f = _make_f(annual_revenue_millions=None, free_cash_flow_margin=None)
        result = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME)
        assert result.data_quality == "insufficient"

    def test_unprofitable_company_skips_or_handles_iv(self):
        f = _make_f(
            annual_revenue_millions=1_000.0,
            free_cash_flow_margin=-8.0,  # burning cash
            market_cap_millions=500.0,
            current_price=5.0,
            shares_millions=100.0,
        )
        # Should not raise; may return None IVs but not crash
        result = run_dcf(f, Sector.AI_DATA_SOFTWARE)
        assert result is not None

    def test_verdict_populated(self):
        f = _make_f(
            annual_revenue_millions=5_000.0,
            free_cash_flow_margin=10.0,
            dod_revenue_pct=70.0,
            market_cap_millions=3_000.0,
            current_price=30.0,
            shares_millions=100.0,
        )
        result = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME)
        assert result.verdict != ""

    def test_sole_source_lowers_discount_rate(self):
        f = _make_f(
            annual_revenue_millions=2_000.0,
            free_cash_flow_margin=12.0,
            dod_revenue_pct=80.0,
            market_cap_millions=5_000.0,
            current_price=50.0,
            shares_millions=100.0,
            moat_rating="Narrow",
        )
        result_no_ss = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME, contracts_sole_source=False)
        result_ss    = run_dcf(f, Sector.TRADITIONAL_DEFENSE_PRIME, contracts_sole_source=True)
        assert result_ss.discount_rate_base <= result_no_ss.discount_rate_base

    def test_growth_blend_uses_actual_data_when_available(self):
        # With revenue_cagr_3yr set, base growth should be anchored to actual data,
        # not defaulting to the sector floor. Verify by checking that the base growth
        # scenario year 1-5 reflects the blend (not just a fixed sector default).
        f_high = _make_f(
            annual_revenue_millions=2_000.0,
            free_cash_flow_margin=10.0,
            dod_revenue_pct=70.0,
            market_cap_millions=5_000.0,
            current_price=50.0,
            shares_millions=100.0,
            revenue_cagr_3yr=15.0,
            revenue_growth_forward=12.0,
            revenue_growth_1yr=14.0,
        )
        f_slow = _make_f(
            annual_revenue_millions=2_000.0,
            free_cash_flow_margin=10.0,
            dod_revenue_pct=70.0,
            market_cap_millions=5_000.0,
            current_price=50.0,
            shares_millions=100.0,
            revenue_cagr_3yr=1.0,
            revenue_growth_forward=1.5,
            revenue_growth_1yr=1.2,
        )
        result_high = run_dcf(f_high, Sector.TRADITIONAL_DEFENSE_PRIME)
        result_slow = run_dcf(f_slow, Sector.TRADITIONAL_DEFENSE_PRIME)
        # High-growth company should have a higher base IV
        if result_high.base and result_slow.base:
            iv_high = result_high.base.intrinsic_value_per_share
            iv_slow = result_slow.base.intrinsic_value_per_share
            if iv_high is not None and iv_slow is not None:
                assert iv_high > iv_slow
