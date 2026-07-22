"""
Regression test: --brief mode must populate s.signal_strength before
rendering, not leave every score at the CompanyScore dataclass default of 0.

generate_report() used to compute signal_strength in a loop that ran AFTER
the `if brief: return _generate_brief_report(...)` early return, so brief
reports always rendered "0/10" for every single company regardless of its
actual conviction score. The fix moved the computation above the branch so
both report modes share it.
"""
from dataclasses import dataclass
from typing import Optional

from src.report import generate_report
from src.models import CompanyScore, ComponentScore, Verdict, Sector, CompanyFundamentals


@dataclass
class _MockDCF:
    bear_mos: Optional[float]
    base_iv: Optional[float] = None
    margin_of_safety_base: Optional[float] = None
    implied_growth_rate: Optional[float] = None
    base_growth: Optional[float] = None


def _make_score() -> CompanyScore:
    def _comp(raw: float = 75.0) -> ComponentScore:
        return ComponentScore(raw=raw, weight=0.25, explanation="test")

    s = CompanyScore(
        ticker="TEST",
        company_name="Test Corp",
        sector=Sector.TRADITIONAL_DEFENSE_PRIME,
        buffett_quality=_comp(),
        graham_value=_comp(),
        dod_stability=_comp(),
        management=_comp(),
        contract_catalyst=_comp(),
        balance_sheet=_comp(),
        final_score=76.0,
        verdict=Verdict.POTENTIALLY_ATTRACTIVE,
        overall_explanation="test",
    )
    s.data_completeness_pct = 95.0
    s.red_flags = []
    s.dcf = _MockDCF(bear_mos=6.0, base_iv=150.0, margin_of_safety_base=20.0)
    return s


def test_brief_mode_populates_signal_strength():
    s = _make_score()
    assert s.signal_strength == 0  # dataclass default, pre-report-generation

    generate_report(
        ranked_scores=[s],
        private_contracts=[],
        all_contracts=[],
        run_date="2026-01-01 00:00 UTC",
        brief=True,
        fundamentals_map={"TEST": CompanyFundamentals(ticker="TEST", current_price=100.0)},
    )

    # With final_score 75+, positive bear MoS, and clean data, conviction
    # scoring must produce something above the zero-value default.
    assert s.signal_strength > 0


def test_brief_report_body_does_not_show_zero_for_strong_score():
    s = _make_score()
    report = generate_report(
        ranked_scores=[s],
        private_contracts=[],
        all_contracts=[],
        run_date="2026-01-01 00:00 UTC",
        brief=True,
        fundamentals_map={"TEST": CompanyFundamentals(ticker="TEST", current_price=100.0)},
    )
    assert "0/10" not in report
