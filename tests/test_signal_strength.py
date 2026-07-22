"""
Unit tests for the Signal Strength (conviction score) computation in report.py.

The 0-10 scale breaks down as:
  Score quality  0-3: ≥75→3, ≥68→2, ≥63→1
  Bear MoS       0-3: ≥5%→3, ≥0%→2, ≥-15%→1
  Data grade     0-2: A(≥90%)→2, B(≥75%)→1
  Stability      0-1: ≥3 run history, spread ≤3pts → 1
  No data flags  0-1: no "data check" red flags → 1
"""
import pytest
from dataclasses import dataclass, field
from typing import List, Optional
from src.report import _compute_conviction_score
from src.models import (
    CompanyScore, ComponentScore, Verdict, Sector, Contract,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

@dataclass
class _MockDCF:
    bear_mos: Optional[float]


def _make_score(
    final_score: float = 70.0,
    bear_mos: float = 5.0,
    data_completeness_pct: float = 95.0,
    red_flags: List[str] = None,
    verdict: Verdict = Verdict.POTENTIALLY_ATTRACTIVE,
) -> CompanyScore:
    """Build a minimal CompanyScore for conviction score tests."""
    def _comp(raw: float = 70.0) -> ComponentScore:
        return ComponentScore(raw=raw, weight=0.25, explanation="test")

    dcf_obj = _MockDCF(bear_mos=bear_mos) if bear_mos is not None else _MockDCF(bear_mos=None)

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
        final_score=final_score,
        verdict=verdict,
        overall_explanation="test",
    )
    s.data_completeness_pct = data_completeness_pct
    s.red_flags = red_flags or []
    s.dcf = dcf_obj
    return s


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestScoreComponent:
    """Score position relative to PA+ threshold (0-3 pts)."""

    def test_score_above_75_earns_3_pts(self):
        s = _make_score(final_score=76.0, bear_mos=None)
        pts, _ = _compute_conviction_score(s, None, {})
        # 3 (score) + 0 (no bear) + 2 (data A) + 0 (no history) + 1 (no flags) = 6
        assert pts >= 6

    def test_score_in_68_to_75_earns_2_pts(self):
        s_low = _make_score(final_score=67.9, bear_mos=None)
        s_high = _make_score(final_score=68.0, bear_mos=None)
        pts_low, _ = _compute_conviction_score(s_low, None, {})
        pts_high, _ = _compute_conviction_score(s_high, None, {})
        assert pts_high > pts_low

    def test_score_below_63_earns_0_pts(self):
        s = _make_score(final_score=60.0, bear_mos=None)
        pts, _ = _compute_conviction_score(s, None, {})
        # 0 (score) + 0 (no bear) + 2 (data A) + 0 (no history) + 1 (no flags) = 3
        assert pts == 3


class TestBearMoSComponent:
    """Bear-case margin of safety (0-3 pts)."""

    def test_positive_bear_mos_above_5_earns_3_pts(self):
        s = _make_score(final_score=75.0, bear_mos=6.0, data_completeness_pct=95.0)
        pts, _ = _compute_conviction_score(s, None, {})
        # 3 (score) + 3 (bear) + 2 (data) + 0 (history) + 1 (flags) = 9
        assert pts == 9

    def test_bear_mos_zero_to_5_earns_2_pts(self):
        s_hi = _make_score(final_score=75.0, bear_mos=5.1)  # just above 5 → 3 pts
        s_lo = _make_score(final_score=75.0, bear_mos=4.9)  # below 5 → 2 pts
        pts_hi, _ = _compute_conviction_score(s_hi, None, {})
        pts_lo, _ = _compute_conviction_score(s_lo, None, {})
        assert pts_hi - pts_lo == 1

    def test_bear_mos_negative_but_within_minus_15_earns_1_pt(self):
        s = _make_score(final_score=75.0, bear_mos=-10.0)
        pts, _ = _compute_conviction_score(s, None, {})
        # 3 (score) + 1 (bear -10) + 2 (data A) + 0 + 1 = 7
        assert pts == 7

    def test_bear_mos_worse_than_minus_15_earns_0_pts(self):
        s_ok = _make_score(final_score=75.0, bear_mos=-14.9)
        s_bad = _make_score(final_score=75.0, bear_mos=-16.0)
        pts_ok, _ = _compute_conviction_score(s_ok, None, {})
        pts_bad, _ = _compute_conviction_score(s_bad, None, {})
        assert pts_ok > pts_bad

    def test_no_bear_iv_earns_0_pts(self):
        s = _make_score(final_score=75.0, bear_mos=None)
        pts, _ = _compute_conviction_score(s, None, {})
        # 3 (score) + 0 (no bear) + 2 (data A) + 0 + 1 = 6
        assert pts == 6


class TestDataComponent:
    """Data completeness grade (0-2 pts)."""

    def test_grade_A_earns_2_pts(self):
        s_a = _make_score(data_completeness_pct=95.0, bear_mos=None, final_score=63.0)
        s_b = _make_score(data_completeness_pct=80.0, bear_mos=None, final_score=63.0)
        pts_a, _ = _compute_conviction_score(s_a, None, {})
        pts_b, _ = _compute_conviction_score(s_b, None, {})
        assert pts_a - pts_b == 1  # A vs B = 1 pt difference

    def test_grade_below_B_earns_0_pts(self):
        s = _make_score(data_completeness_pct=55.0, bear_mos=None, final_score=63.0)
        pts, _ = _compute_conviction_score(s, None, {})
        # 1 (score ≥63) + 0 (no bear) + 0 (data D) + 0 + 1 = 2
        assert pts == 2


class TestStabilityComponent:
    """Score run-history stability (0-1 pts)."""

    def test_stable_history_earns_1_pt(self):
        s = _make_score(final_score=75.0, bear_mos=5.0)
        history = {
            "TEST": [
                {"score": 74.0}, {"score": 75.0}, {"score": 75.5}  # spread 1.5 → stable
            ]
        }
        pts, _ = _compute_conviction_score(s, None, history)
        # 3+3+2+1+1 = 10
        assert pts == 10

    def test_volatile_history_earns_0_pts(self):
        s = _make_score(final_score=75.0, bear_mos=5.0)
        history = {
            "TEST": [
                {"score": 60.0}, {"score": 75.0}, {"score": 78.0}  # spread 18 → volatile
            ]
        }
        pts, _ = _compute_conviction_score(s, None, history)
        # 3+3+2+0+1 = 9
        assert pts == 9

    def test_insufficient_history_earns_0_pts(self):
        s = _make_score(final_score=75.0, bear_mos=5.0)
        history = {"TEST": [{"score": 75.0}, {"score": 76.0}]}  # only 2 runs
        pts, _ = _compute_conviction_score(s, None, history)
        # 3+3+2+0+1 = 9
        assert pts == 9


class TestDataFlagPenalty:
    """Data validation flags reduce conviction (0 or 1 pt)."""

    def test_no_data_flags_earns_bonus_pt(self):
        # score≥63(1) + no_bear(0) + data_A(2) + no_history(0) + no_flags(1) = 4
        s = _make_score(final_score=63.0, bear_mos=None, red_flags=[])
        pts, _ = _compute_conviction_score(s, None, {})
        assert pts == 4

    def test_data_check_flag_removes_bonus(self):
        # same setup minus 1 for data flag → 3
        s = _make_score(
            final_score=63.0, bear_mos=None,
            red_flags=["⚠️ DATA CHECK: dividend payout ratio 120%"]
        )
        pts, _ = _compute_conviction_score(s, None, {})
        assert pts == 3

    def test_non_data_flag_does_not_remove_bonus(self):
        # "DCF:" flag is not a data check flag → no penalty → still 4
        s = _make_score(
            final_score=63.0, bear_mos=None,
            red_flags=["DCF: 45% overvalued at current price"]
        )
        pts, _ = _compute_conviction_score(s, None, {})
        assert pts == 4


class TestBoundaries:
    """Score is always 0–10, never negative, never above 10."""

    def test_maximum_score_is_10(self):
        s = _make_score(final_score=80.0, bear_mos=10.0, data_completeness_pct=100.0)
        history = {"TEST": [{"score": 80.0}, {"score": 80.0}, {"score": 80.5}]}
        pts, _ = _compute_conviction_score(s, None, history)
        assert pts <= 10

    def test_minimum_score_is_zero(self):
        s = _make_score(final_score=40.0, bear_mos=-50.0, data_completeness_pct=20.0)
        pts, _ = _compute_conviction_score(s, None, {})
        assert pts >= 0
