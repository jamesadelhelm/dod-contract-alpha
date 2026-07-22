"""
Regression test for src/scoring.py _generate_narrative()'s key_risks list.

Covers a bug where the operating-margin-compression risk flag read
getattr(f, "op_margin_delta", None) instead of the real CompanyFundamentals
field "operating_margin_delta" (see line ~1157 in the same file for the
correct usage). The typo made the getattr() call always return None, so the
risk flag for companies with meaningfully shrinking operating margins never
fired.
"""
from src.models import CompanyFundamentals, Sector
from src.scoring import _generate_narrative


def _make_f(**kwargs) -> CompanyFundamentals:
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        market_cap_millions=5000.0,
        dod_revenue_pct=70.0,
        backlog_to_revenue=1.0,
        debt_ebitda=1.0,
        pe_ratio=15.0,
    )
    defaults.update(kwargs)
    return CompanyFundamentals(**defaults)


class TestOperatingMarginRiskFlag:
    def test_flags_meaningful_operating_margin_compression(self):
        f = _make_f(operating_margin_delta=-5.0)
        _why, _why_not, risks, _verify = _generate_narrative(
            "TEST", f, contracts=[], sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            bq=50.0, gv=50.0, ds=50.0,
        )
        assert any("compressed" in r.lower() for r in risks)

    def test_no_flag_when_margin_stable(self):
        f = _make_f(operating_margin_delta=-1.0)
        _why, _why_not, risks, _verify = _generate_narrative(
            "TEST", f, contracts=[], sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            bq=50.0, gv=50.0, ds=50.0,
        )
        assert not any("compressed" in r.lower() for r in risks)

    def test_no_flag_when_data_missing(self):
        f = _make_f(operating_margin_delta=None)
        _why, _why_not, risks, _verify = _generate_narrative(
            "TEST", f, contracts=[], sector=Sector.TRADITIONAL_DEFENSE_PRIME,
            bq=50.0, gv=50.0, ds=50.0,
        )
        assert not any("compressed" in r.lower() for r in risks)
