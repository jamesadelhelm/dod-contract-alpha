"""
Unit tests for _estimate_graham_expansion_price / _what_would_change in report.py.

Regression coverage for a bug where the "Multiple expansion" scenario in the
What Would Change My Mind section computed a nonsensical dollar delta:

    pct_rise_to_flip = (final_score - 68) / weight / 100 * base_iv
    new_price = current_price + pct_rise_to_flip

This mixed a raw-score fraction with an intrinsic-value dollar figure and added
it directly to price, producing target prices unrelated to how far the stock
would actually need to rally to compress Graham Value enough to flip the
verdict. The fix simulates the rally directly (same technique as
_estimate_graham_upgrade_price, scanning upward instead of downward) by
re-scoring Graham Value with price-scaled multiples.
"""
import pytest
from src.report import _estimate_graham_expansion_price, _what_would_change
from src.models import (
    CompanyScore, ComponentScore, Verdict, Sector, DCFResult, CompanyFundamentals,
)


def _make_f(**kwargs) -> CompanyFundamentals:
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_price=100.0,
        pe_ratio=20.0,
        forward_pe=18.0,
        ev_ebitda=12.0,
        fcf_yield=5.0,
        price_to_book=3.0,
        earnings_stability_years=10,
        dividend_yield=2.0,
    )
    defaults.update(kwargs)
    return CompanyFundamentals(**defaults)


def _make_score(final_score: float = 72.0, graham_raw: float = 65.0) -> CompanyScore:
    def _comp(raw: float) -> ComponentScore:
        return ComponentScore(raw=raw, weight=0.20, explanation="test")

    s = CompanyScore(
        ticker="TEST",
        company_name="Test Corp",
        sector=Sector.TRADITIONAL_DEFENSE_PRIME,
        buffett_quality=_comp(70.0),
        graham_value=_comp(graham_raw),
        dod_stability=_comp(70.0),
        management=_comp(70.0),
        contract_catalyst=_comp(70.0),
        balance_sheet=_comp(70.0),
        final_score=final_score,
        verdict=Verdict.POTENTIALLY_ATTRACTIVE,
        overall_explanation="test",
    )
    s.dcf = DCFResult(ticker="TEST", base_iv=110.0, current_price=100.0)
    return s


class TestGrahamExpansionPrice:
    def test_returns_price_above_current(self):
        s = _make_score(final_score=74.0, graham_raw=70.0)
        f = _make_f(current_price=100.0)
        price = _estimate_graham_expansion_price(s, f)
        assert price is not None
        assert price > f.current_price

    def test_none_when_score_already_below_threshold(self):
        s = _make_score(final_score=65.0)
        f = _make_f()
        assert _estimate_graham_expansion_price(s, f) is None

    def test_none_when_no_price(self):
        s = _make_score(final_score=74.0)
        f = _make_f(current_price=None)
        assert _estimate_graham_expansion_price(s, f) is None

    def test_higher_score_requires_larger_rally(self):
        # A name deep into PA+ territory needs a bigger rally to flip than one
        # just barely above the 68 threshold.
        f = _make_f(current_price=100.0)
        s_marginal = _make_score(final_score=69.0, graham_raw=70.0)
        s_deep = _make_score(final_score=85.0, graham_raw=70.0)
        p_marginal = _estimate_graham_expansion_price(s_marginal, f)
        p_deep = _estimate_graham_expansion_price(s_deep, f)
        if p_marginal is not None and p_deep is not None:
            assert p_deep >= p_marginal


class TestWhatWouldChangeNarrative:
    def test_multiple_expansion_line_is_percentage_based(self):
        s = _make_score(final_score=74.0, graham_raw=70.0)
        f = _make_f(current_price=100.0)
        lines = _what_would_change(s, f)
        text = "\n".join(lines)
        assert "Multiple expansion" in text
        # The old bug produced a raw dollar increment with no % sign and an
        # implausible magnitude; the fix always reports a percentage rally.
        expansion_lines = [l for l in lines if "Multiple expansion" in l]
        assert expansion_lines
        assert "%" in expansion_lines[0]
