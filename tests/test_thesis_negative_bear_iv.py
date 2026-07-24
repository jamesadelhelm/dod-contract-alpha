"""
Regression test: the One-Line Investment Thesis's "3-yr return" figure must
not silently corrupt into a Python complex number when the bear-case DCF
intrinsic value per share is negative.

_ann3()'s guard was `if iv and cur > 0`, which only excludes iv == 0, not
iv < 0. A negative bear_iv is a real, reachable DCF outcome (dcf.py's
EV-to-equity net-debt adjustment can push bear-case per-share value below
zero for heavily levered names). Raising a negative number to a fractional
power in Python returns a complex number, and Python's string formatting
does not raise on formatting a complex number with `:+.0f` — it silently
prints something like "-69+54j%" embedded in the published thesis sentence
instead of erroring or being caught by any existing guard.
"""
from src.report import generate_report
from src.models import (
    CompanyScore, ComponentScore, Verdict, Sector, CompanyFundamentals, DCFResult,
)


def _make_score(bear_iv: float) -> CompanyScore:
    def _comp(raw: float = 75.0) -> ComponentScore:
        return ComponentScore(raw=raw, weight=0.25, explanation="test")

    s = CompanyScore(
        ticker="TEST",
        company_name="Test Corp",
        sector=Sector.TRADITIONAL_DEFENSE_PRIME,
        buffett_quality=_comp(), graham_value=_comp(), dod_stability=_comp(),
        management=_comp(), contract_catalyst=_comp(), balance_sheet=_comp(),
        final_score=76.0,
        verdict=Verdict.POTENTIALLY_ATTRACTIVE,
        overall_explanation="test",
    )
    s.data_completeness_pct = 90.0
    s.red_flags = []
    s.dcf = DCFResult(
        ticker="TEST",
        base_iv=150.0,
        bull_iv=220.0,
        bear_iv=bear_iv,
        margin_of_safety_base=20.0,
        bear_mos=None if bear_iv is None or bear_iv <= 0 else 5.0,
        bear_growth=2.0,
        implied_growth_rate=6.0,
        base_growth=5.0,
        discount_rate_base=9.0,
    )
    return s


def _fundamentals() -> CompanyFundamentals:
    return CompanyFundamentals(
        ticker="TEST",
        current_price=100.0,
        moat_rating="Wide",
        dod_revenue_pct=60.0,
        backlog_to_revenue=2.0,
        dividend_yield=1.5,
    )


def test_negative_bear_iv_does_not_produce_complex_number_artifact():
    s = _make_score(bear_iv=-12.0)
    report = generate_report(
        ranked_scores=[s],
        private_contracts=[],
        all_contracts=[],
        run_date="2026-01-01 00:00 UTC",
        fundamentals_map={"TEST": _fundamentals()},
    )
    assert "j%" not in report
    assert "+0j" not in report and "-0j" not in report


def test_positive_bear_iv_still_shows_a_3yr_return_range():
    s = _make_score(bear_iv=80.0)
    report = generate_report(
        ranked_scores=[s],
        private_contracts=[],
        all_contracts=[],
        run_date="2026-01-01 00:00 UTC",
        fundamentals_map={"TEST": _fundamentals()},
    )
    assert "3-yr return:" in report
    assert "j%" not in report
