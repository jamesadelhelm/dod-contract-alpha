"""
Regression test for the insider-activity check in _conviction_checklist().

The elif chain checked `ins_pct <= -20` before `ins_pct <= -40`. Since -20 is
the less extreme threshold, any value <= -40 always matched the -20 branch
first, making the "heavy selling" (<=-40%, ❌, +2 warnings) branch dead code
that could never execute — every heavy-selling scenario silently downgraded
to the milder "warrants scrutiny" (⚠️, +1 warning) label the README says is
reserved for selling in the -20% to -40% band.
"""
from src.report import _conviction_checklist
from src.models import CompanyScore, ComponentScore, Verdict, Sector, CompanyFundamentals


def _make_score() -> CompanyScore:
    def _comp(raw: float = 75.0) -> ComponentScore:
        return ComponentScore(raw=raw, weight=0.25, explanation="test")

    s = CompanyScore(
        ticker="TEST",
        company_name="Test Corp",
        sector=Sector.TRADITIONAL_DEFENSE_PRIME,
        buffett_quality=_comp(), graham_value=_comp(), dod_stability=_comp(),
        management=_comp(), contract_catalyst=_comp(), balance_sheet=_comp(),
        final_score=75.0,
        verdict=Verdict.POTENTIALLY_ATTRACTIVE,
        overall_explanation="test",
    )
    s.data_completeness_pct = 95.0
    return s


def _make_f(insider_net_pct_6m: float) -> CompanyFundamentals:
    return CompanyFundamentals(ticker="TEST", insider_net_pct_6m=insider_net_pct_6m)


def _insider_row(lines):
    return next(l for l in lines if l.startswith("| Insider activity"))


class TestInsiderActivityThresholds:
    def test_heavy_selling_below_40pct_is_hard_fail(self):
        # -45% net selling must hit the ❌ "heavy" branch, not the ⚠️ one.
        s = _make_score()
        f = _make_f(insider_net_pct_6m=-0.45)
        lines = _conviction_checklist(s, f, size_pct=6.0)
        row = _insider_row(lines)
        assert "❌" in row
        assert "Heavy insider" in row

    def test_moderate_selling_between_20_and_40pct_is_soft_warning(self):
        s = _make_score()
        f = _make_f(insider_net_pct_6m=-0.25)
        lines = _conviction_checklist(s, f, size_pct=6.0)
        row = _insider_row(lines)
        assert "⚠️" in row
        assert "Heavy insider" not in row

    def test_net_buying_is_a_pass(self):
        s = _make_score()
        f = _make_f(insider_net_pct_6m=0.15)
        lines = _conviction_checklist(s, f, size_pct=6.0)
        row = _insider_row(lines)
        assert "✅" in row

    def test_heavy_selling_blocks_deployment(self):
        # A ❌ on any check must produce the hard "Hold — Do Not Deploy" verdict.
        s = _make_score()
        f = _make_f(insider_net_pct_6m=-0.50)
        lines = _conviction_checklist(s, f, size_pct=6.0)
        text = "\n".join(lines)
        assert "Hold — Do Not Deploy" in text
