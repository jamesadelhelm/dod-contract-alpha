"""
Regression tests for src/edgar.py overlay functions.

Covers a bug where overlay_xbrl_into_fundamentals() wrote the 3-year
normalized FCF margin to a nonexistent `fcf_margin` attribute instead of
the real CompanyFundamentals field `free_cash_flow_margin`. Because
CompanyFundamentals is a plain (non-slotted) dataclass, the typo did not
raise — it silently created a stray attribute nothing else reads, so the
`--xbrl` flag's headline "3-yr normalized FCF margin" feature never
actually reached the DCF or scoring engine.
"""
from src.edgar import overlay_xbrl_into_fundamentals
from src.models import CompanyFundamentals


def _make_f(**kwargs) -> CompanyFundamentals:
    defaults = dict(ticker="TEST", company_name="Test Corp")
    defaults.update(kwargs)
    return CompanyFundamentals(**defaults)


class TestXBRLFCFMarginOverlay:
    def test_fcf_margin_3yr_writes_to_free_cash_flow_margin(self):
        f = _make_f(free_cash_flow_margin=None)
        overlay_xbrl_into_fundamentals(f, {"fcf_margin_3yr": 9.5})
        assert f.free_cash_flow_margin == 9.5

    def test_does_not_leave_a_stray_fcf_margin_attribute(self):
        f = _make_f(free_cash_flow_margin=None)
        overlay_xbrl_into_fundamentals(f, {"fcf_margin_3yr": 9.5})
        assert not hasattr(f, "fcf_margin")

    def test_overwrites_existing_ttm_value_with_normalized_3yr(self):
        # XBRL 3yr-normalized FCF margin is authoritative over yfinance TTM.
        f = _make_f(free_cash_flow_margin=2.0)
        overlay_xbrl_into_fundamentals(f, {"fcf_margin_3yr": 11.2})
        assert f.free_cash_flow_margin == 11.2

    def test_no_fcf_key_leaves_field_untouched(self):
        f = _make_f(free_cash_flow_margin=6.5)
        overlay_xbrl_into_fundamentals(f, {"backlog_to_rev": 2.1})
        assert f.free_cash_flow_margin == 6.5


class TestXBRLOtherFieldsOverlay:
    def test_backlog_to_rev_writes_to_backlog_to_revenue(self):
        f = _make_f()
        overlay_xbrl_into_fundamentals(f, {"backlog_to_rev": 3.4})
        assert f.backlog_to_revenue == 3.4

    def test_rev_cagr_3yr_writes_to_revenue_cagr_3yr(self):
        f = _make_f()
        overlay_xbrl_into_fundamentals(f, {"rev_cagr_3yr": 6.1})
        assert f.revenue_cagr_3yr == 6.1

    def test_extreme_negative_cagr_suppressed_as_spinoff_artifact(self):
        f = _make_f()
        overlay_xbrl_into_fundamentals(f, {"rev_cagr_3yr": -15.7})
        assert f.revenue_cagr_3yr is None

    def test_latest_rev_dollars_converted_to_millions(self):
        f = _make_f(annual_revenue_millions=None)
        overlay_xbrl_into_fundamentals(f, {"latest_rev": 21_000_000_000})
        assert f.annual_revenue_millions == 21_000.0

    def test_latest_rev_does_not_override_existing_revenue(self):
        f = _make_f(annual_revenue_millions=5000.0)
        overlay_xbrl_into_fundamentals(f, {"latest_rev": 21_000_000_000})
        assert f.annual_revenue_millions == 5000.0

    def test_empty_xbrl_dict_is_a_no_op(self):
        f = _make_f(free_cash_flow_margin=6.5, backlog_to_revenue=1.2)
        overlay_xbrl_into_fundamentals(f, {})
        assert f.free_cash_flow_margin == 6.5
        assert f.backlog_to_revenue == 1.2
