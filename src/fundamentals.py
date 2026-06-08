"""
Fundamentals loader.

Priority chain (per ticker):
  1. yfinance  — live market data, income statement, balance sheet
  2. mock_fundamentals.json — curated overrides (gov revenue %, backlog, moat, ROIC)
  3. stub — all None, scores conservatively

The mock file acts as an *overlay* on top of yfinance, filling fields
yfinance doesn't provide (gov revenue %, DoD %, backlog/rev, moat rating, ROIC).
This gives us the best of both: live prices/multiples + curated strategic context.

Usage:
    from src.fundamentals import get_fundamentals_or_stub
    f = get_fundamentals_or_stub("BWXT")           # auto mode
    f = get_fundamentals_or_stub("BWXT", live=True) # force yfinance
"""

from __future__ import annotations
import json
from typing import Optional, Dict
from src.models import CompanyFundamentals
from config import MOCK_FUNDAMENTALS_PATH

# ── Mock cache ────────────────────────────────────────────────────────────────
_MOCK_CACHE: Dict[str, dict] = {}        # raw dicts from JSON
_LIVE_CACHE: Dict[str, CompanyFundamentals] = {}  # yfinance results


def _load_mock_raw() -> Dict[str, dict]:
    global _MOCK_CACHE
    if not _MOCK_CACHE:
        with open(MOCK_FUNDAMENTALS_PATH) as f:
            _MOCK_CACHE = json.load(f)
    return _MOCK_CACHE


# ── yfinance fetcher ──────────────────────────────────────────────────────────

def get_fundamentals_from_yfinance(ticker: str) -> Optional[CompanyFundamentals]:
    """
    Fetch live fundamentals. Extracts every useful field yfinance exposes,
    then overlays curated fields from mock_fundamentals.json for anything
    yfinance doesn't reliably provide (gov revenue %, ROIC, backlog, moat).
    """
    try:
        import yfinance as yf
    except ImportError:
        print("[yfinance] Not installed. Run: pip install yfinance")
        return None

    try:
        stock = yf.Ticker(ticker)
        info  = stock.info or {}

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            print(f"[yfinance] No data returned for {ticker}")
            return None

        # ── Basic size ────────────────────────────────────────────────────────
        mc  = info.get("marketCap")
        rev = info.get("totalRevenue")
        mc_m  = _m(mc)
        rev_m = _m(rev)

        # ── Margins ───────────────────────────────────────────────────────────
        op_margin = _pct(info.get("operatingMargins"))
        fcf_margin = _derive_fcf_margin(info)

        # ── ROIC: derive from yfinance cash flow / balance sheet ──────────────
        roic = _derive_roic(stock, info)

        # ── FCF yield ─────────────────────────────────────────────────────────
        fcf_yield = None
        fcf = info.get("freeCashflow")
        mc_raw = info.get("marketCap")
        if fcf and mc_raw and mc_raw > 0:
            fcf_yield = round(fcf / mc_raw * 100, 2)

        # ── Valuation ─────────────────────────────────────────────────────────
        pe       = info.get("trailingPE")
        fwd_pe   = info.get("forwardPE")
        ev_ebitda = info.get("enterpriseToEbitda")
        pb       = info.get("priceToBook")

        # Sanity-cap absurd yfinance PE values
        if pe and (pe > 2000 or pe < 0):
            pe = None
        if fwd_pe and (fwd_pe > 2000 or fwd_pe < 0):
            fwd_pe = None

        # ── Balance sheet ─────────────────────────────────────────────────────
        de             = _de_ratio(info)
        current_ratio  = info.get("currentRatio")
        net_debt_m     = _net_debt(info)
        debt_ebitda    = _debt_ebitda(info)
        interest_cov   = _interest_coverage(info, stock)

        # ── Earnings stability: count profitable years from financials ────────
        earn_stability = _earnings_stability(stock)

        # ── Ownership ─────────────────────────────────────────────────────────
        insider_pct = _pct(info.get("heldPercentInsiders"))

        # Share count and price for DCF
        shares_m = None
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares_out = info.get("sharesOutstanding")
        if shares_out:
            shares_m = round(shares_out / 1_000_000, 1)
        elif mc_m and price and price > 0:
            shares_m = round(mc_m / price, 1)

        # Gross margin — reliable from yfinance info
        gross_margin_raw = info.get("grossMargins")
        gross_margin = round(float(gross_margin_raw) * 100, 1) if gross_margin_raw is not None else None

        # Revenue growth (1yr YoY) — from yfinance info; fall back to income stmt
        rev_growth_raw = info.get("revenueGrowth")
        if rev_growth_raw is not None:
            revenue_growth_1yr = round(float(rev_growth_raw) * 100, 1)
        else:
            revenue_growth_1yr = _derive_revenue_growth(stock)

        # Staleness check — warn if most recent filing > 180 days old
        stale_note = ""
        most_recent_ts = info.get("mostRecentQuarter") or info.get("lastFiscalYearEnd")
        if most_recent_ts:
            from datetime import datetime as _dt
            days_old = (_dt.now().timestamp() - most_recent_ts) / 86400
            if days_old > 180:
                stale_note = f" ⚠️ STALE: most recent filing ~{int(days_old / 30)}mo ago — verify current data."

        # ── Analyst consensus ─────────────────────────────────────────────────
        analyst_count = info.get("numberOfAnalystOpinions")
        analyst_target = info.get("targetMeanPrice")
        analyst_rec    = info.get("recommendationKey")
        upside_to_target = None
        if analyst_target and price and price > 0:
            upside_to_target = round((analyst_target - price) / price * 100, 1)

        # ── Price momentum ────────────────────────────────────────────────────
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w  = info.get("fiftyTwoWeekLow")
        pct_off_high = None
        if high_52w and price and high_52w > 0:
            pct_off_high = round((price - high_52w) / high_52w * 100, 1)
        return_1yr = _pct(info.get("52WeekChange"))

        # ── Short interest ────────────────────────────────────────────────────
        short_pct_float = _pct(info.get("shortPercentOfFloat"))
        short_ratio     = info.get("shortRatio")  # days to cover (float)
        if isinstance(short_ratio, (int, float)) and short_ratio > 200:
            short_ratio = None  # yfinance sometimes returns junk values

        # ── Capital return ────────────────────────────────────────────────────
        # yfinance has two dividend yield fields:
        #   trailingAnnualDividendYield — consistently fractional (0.025 = 2.5%); preferred
        #   dividendYield               — inconsistent format; some tickers return 0.09 for 9%,
        #                                 others return 0.09 for 0.09% (TXT bug: actual 0.088%)
        # Prefer trailingAnnualDividendYield; fall back to dividendYield with heuristic.
        # Cap at 20% — defense/industrial yields above that are data errors.
        _trailing_dy = info.get("trailingAnnualDividendYield")
        _dy_raw      = info.get("dividendYield")
        if _trailing_dy is not None:
            _tdy = float(_trailing_dy) * 100  # fractional → percent
            div_yield = round(_tdy, 2) if 0 <= _tdy <= 20 else None
        elif _dy_raw is not None:
            _dy = float(_dy_raw)
            div_yield = round(_dy if _dy > 0.3 else _dy * 100, 2)
            div_yield = div_yield if div_yield <= 20 else None
        else:
            div_yield = None
        payout_rat  = _pct(info.get("payoutRatio"))
        shares_chg  = _derive_shares_change(stock)

        # ── Next earnings date ────────────────────────────────────────────────
        next_earn = _get_next_earnings(info, stock)

        # ── Margin trends (YoY delta in percentage points) ────────────────────
        op_margin_delta = None
        gross_margin_delta_val = None
        try:
            _inc = stock.income_stmt
            if _inc is not None and not _inc.empty and len(_inc.columns) >= 2:
                c0, c1 = _inc.columns[0], _inc.columns[1]
                r0 = _row(_inc, ["Total Revenue", "Revenue"], c0)
                r1 = _row(_inc, ["Total Revenue", "Revenue"], c1)
                if r0 and r1 and r0 > 0 and r1 > 0:
                    o0 = _row(_inc, ["Operating Income", "EBIT", "Operating Profit"], c0)
                    o1 = _row(_inc, ["Operating Income", "EBIT", "Operating Profit"], c1)
                    g0 = _row(_inc, ["Gross Profit"], c0)
                    g1 = _row(_inc, ["Gross Profit"], c1)
                    if o0 is not None and o1 is not None:
                        op_margin_delta = round(o0 / r0 * 100 - o1 / r1 * 100, 1)
                    if g0 is not None and g1 is not None:
                        gross_margin_delta_val = round(g0 / r0 * 100 - g1 / r1 * 100, 1)
        except Exception:
            pass

        f_live = CompanyFundamentals(
            ticker=ticker,
            company_name=info.get("longName", ticker),
            market_cap_millions=mc_m,
            annual_revenue_millions=rev_m,
            current_price=price,
            shares_millions=shares_m,
            # These need overlay from curated data — yfinance doesn't have them
            government_revenue_pct=None,
            dod_revenue_pct=None,
            backlog_to_revenue=None,
            moat_rating=None,
            # Live fields
            roe=_pct(info.get("returnOnEquity")),
            roic=roic,
            free_cash_flow_margin=fcf_margin,
            operating_margin=op_margin,
            gross_margin=gross_margin,
            revenue_growth_1yr=revenue_growth_1yr,
            pe_ratio=pe,
            forward_pe=fwd_pe,
            ev_ebitda=ev_ebitda,
            price_to_book=pb,
            fcf_yield=fcf_yield,
            debt_equity=de,
            current_ratio=current_ratio,
            interest_coverage=interest_cov,
            net_debt_millions=net_debt_m,
            debt_ebitda=debt_ebitda,
            insider_ownership_pct=insider_pct,
            earnings_stability_years=earn_stability,
            data_source="yfinance",
            data_notes=(f"Live yfinance data. MarketCap=${mc_m:.0f}M." if mc_m else "Live yfinance data.") + stale_note,
            analyst_count=analyst_count,
            analyst_target_price=analyst_target,
            analyst_recommendation=analyst_rec,
            upside_to_target=upside_to_target,
            price_52w_high=high_52w,
            price_52w_low=low_52w,
            pct_off_52w_high=pct_off_high,
            return_1yr=return_1yr,
            operating_margin_delta=op_margin_delta,
            gross_margin_delta=gross_margin_delta_val,
            short_pct_of_float=short_pct_float,
            short_ratio_days=short_ratio,
            dividend_yield=div_yield,
            payout_ratio=payout_rat,
            shares_chg_1yr_pct=shares_chg,
            next_earnings_date=next_earn,
        )

        # ── Overlay curated fields from mock ──────────────────────────────────
        mock_raw = _load_mock_raw().get(ticker.upper(), {})
        if mock_raw:
            _overlay(f_live, mock_raw)
            f_live.data_source = "yfinance+overlay"
            f_live.data_notes += " Curated overlay: gov_rev%, DoD%, backlog, moat, ROIC."

        return f_live

    except Exception as e:
        print(f"[yfinance] Error fetching {ticker}: {e}")
        return None


def _overlay(f: CompanyFundamentals, mock: dict) -> None:
    """
    Fill in fields from the curated overlay dict.

    Two categories:
    - overlay_if_none: only apply when yfinance returned nothing (live data wins)
    - always_override: curated value always wins because yfinance is unreliable
      (e.g. earnings_stability_years — yfinance is capped at 4 years of history)
    """
    overlay_if_none = [
        "government_revenue_pct", "dod_revenue_pct", "backlog_to_revenue",
        "moat_rating", "roic",
    ]
    for fld in overlay_if_none:
        if getattr(f, fld) is None and fld in mock:
            setattr(f, fld, mock[fld])

    always_override = ["earnings_stability_years"]
    for fld in always_override:
        if fld in mock and mock[fld] is not None:
            setattr(f, fld, mock[fld])


# ── ROIC derivation ───────────────────────────────────────────────────────────

def _derive_roic(stock, info: dict) -> Optional[float]:
    """
    ROIC = NOPAT / Invested Capital
    NOPAT  ≈ Operating Income × (1 - tax rate)
    IC     ≈ Total Assets - Current Liabilities - Cash
    Uses annual financials from yfinance.
    """
    try:
        bs  = stock.balance_sheet     # columns = dates
        inc = stock.income_stmt

        if bs is None or inc is None or bs.empty or inc.empty:
            return None

        # Most recent column
        bs_col  = bs.columns[0]
        inc_col = inc.columns[0]

        op_income = _row(inc, ["Operating Income", "EBIT", "Operating Profit"], inc_col)
        total_assets = _row(bs, ["Total Assets"], bs_col)
        current_liab = _row(bs, ["Current Liabilities", "Total Current Liabilities"], bs_col)
        cash         = _row(bs, ["Cash And Cash Equivalents", "Cash", "Cash And Short Term Investments"], bs_col)

        if op_income is None or total_assets is None:
            return None

        tax_rate = 0.21  # US statutory; good enough for screening
        nopat    = op_income * (1 - tax_rate)
        ic       = total_assets - (current_liab or 0) - (cash or 0)
        if ic <= 0:
            return None

        roic = round(nopat / ic * 100, 1)
        return roic if -50 < roic < 200 else None  # sanity bounds

    except Exception:
        return None


def _derive_revenue_growth(stock) -> Optional[float]:
    """YoY revenue growth from the two most recent annual income statements."""
    try:
        inc = stock.income_stmt
        if inc is None or inc.empty or len(inc.columns) < 2:
            return None
        cols = inc.columns
        rev_current = _row(inc, ["Total Revenue", "Revenue"], cols[0])
        rev_prior   = _row(inc, ["Total Revenue", "Revenue"], cols[1])
        if rev_current and rev_prior and rev_prior > 0:
            return round((rev_current - rev_prior) / rev_prior * 100, 1)
    except Exception:
        pass
    return None


def _row(df, names: list, col) -> Optional[float]:
    import math
    for name in names:
        if name in df.index:
            val = df.loc[name, col]
            try:
                result = float(val)
                if math.isnan(result) or math.isinf(result):
                    continue
                return result
            except (TypeError, ValueError):
                continue
    return None


# ── Balance sheet helpers ─────────────────────────────────────────────────────

def _de_ratio(info: dict) -> Optional[float]:
    de = info.get("debtToEquity")
    if de is not None:
        # yfinance returns this as a percentage sometimes (e.g. 55.2 = 0.552)
        # Normalize to a ratio
        if de > 20:  # clearly a % value
            return round(de / 100, 2)
        return round(de, 2)
    return None


def _net_debt(info: dict) -> Optional[float]:
    total_debt = info.get("totalDebt", 0) or 0
    cash       = info.get("totalCash", 0) or 0
    if total_debt or cash:
        return round((total_debt - cash) / 1_000_000, 1)
    return None


def _debt_ebitda(info: dict) -> Optional[float]:
    total_debt = info.get("totalDebt")
    ebitda     = info.get("ebitda")
    if total_debt and ebitda and ebitda > 0:
        return round(total_debt / ebitda, 2)
    return None


def _interest_coverage(info: dict, stock) -> Optional[float]:
    """
    EBIT / |Interest Expense| from income statement.
    Positive = EBIT covers interest; negative = operating loss cannot service debt.
    yfinance stores interest expense as a negative number (expense convention), so we
    divide by abs(int_exp) to get the correct signed coverage ratio.
    """
    try:
        inc = stock.income_stmt
        if inc is None or inc.empty:
            return None
        col = inc.columns[0]
        ebit    = _row(inc, ["EBIT", "Operating Income"], col)
        int_exp = _row(inc, ["Interest Expense", "Interest Expense Non Operating"], col)
        if ebit is not None and int_exp is not None and int_exp != 0:
            return round(ebit / abs(int_exp), 1)
    except Exception:
        pass
    return None


def _earnings_stability(stock) -> Optional[int]:
    """
    Count consecutive years of positive net income.

    LIMITATION: yfinance returns at most 4 years of income statement history.
    The maximum this function can return is 4, regardless of actual track record.
    Companies with 20+ year records score identically to 4-year-old companies
    unless overridden via mock_fundamentals.json (earnings_stability_years field).
    Always provide this field in the curated overlay for established defense primes.
    """
    try:
        inc = stock.income_stmt
        if inc is None or inc.empty:
            return None
        count = 0
        for col in inc.columns:
            ni = _row(inc, ["Net Income", "Net Income Common Stockholders"], col)
            if ni is not None and ni > 0:
                count += 1
            else:
                break
        return count if count > 0 else 0
    except Exception:
        return None


def _derive_shares_change(stock) -> Optional[float]:
    """
    % change in diluted shares outstanding YoY.
    Negative = buyback (good), Positive = dilution (bad).
    """
    try:
        inc = stock.income_stmt
        if inc is None or inc.empty or len(inc.columns) < 2:
            return None
        c0, c1 = inc.columns[0], inc.columns[1]
        s0 = _row(inc, ["Diluted Average Shares", "Basic Average Shares"], c0)
        s1 = _row(inc, ["Diluted Average Shares", "Basic Average Shares"], c1)
        if s0 and s1 and s1 > 0:
            chg = round((s0 - s1) / s1 * 100, 1)
            # Sanity bounds: >25% change either way is likely a split/merger, not organic
            return chg if -25 < chg < 25 else None
    except Exception:
        pass
    return None


def _get_next_earnings(info: dict, stock) -> Optional[str]:
    """Return next upcoming earnings date as 'YYYY-MM-DD' string, or None."""
    from datetime import datetime as _dt
    ts = info.get("earningsTimestamp")
    if ts:
        try:
            dt = _dt.fromtimestamp(int(ts))
            if dt > _dt.now():
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        cal = stock.calendar
        if cal is not None and isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
            if not hasattr(dates, "__iter__"):
                dates = [dates]
            for ed in dates:
                try:
                    if hasattr(ed, "to_pydatetime"):
                        ed = ed.to_pydatetime()
                    if ed > _dt.now():
                        return ed.strftime("%Y-%m-%d")
                except Exception:
                    continue
    except Exception:
        pass
    return None


# ── Margin helpers ────────────────────────────────────────────────────────────

def _derive_fcf_margin(info: dict) -> Optional[float]:
    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    if fcf and rev and rev > 0:
        return round(fcf / rev * 100, 2)
    return None


def _pct(val) -> Optional[float]:
    if val is None:
        return None
    return round(float(val) * 100, 2)


def _m(val) -> Optional[float]:
    """Convert raw dollar value to millions."""
    if val is None:
        return None
    return round(val / 1_000_000, 1)


# ── Public interface ──────────────────────────────────────────────────────────

def get_fundamentals(ticker: str) -> Optional[CompanyFundamentals]:
    """Load from mock only (used for pure mock runs)."""
    raw = _load_mock_raw().get(ticker.upper())
    if raw is None:
        return None
    # Filter to only valid CompanyFundamentals fields; guards against stale JSON keys
    # that no longer exist in the dataclass (would raise TypeError on **raw).
    import dataclasses
    valid_fields = {f.name for f in dataclasses.fields(CompanyFundamentals)} - {"ticker", "data_source"}
    filtered = {k: v for k, v in raw.items() if k in valid_fields}
    return CompanyFundamentals(ticker=ticker, data_source="mock", **filtered)


def get_fundamentals_or_stub(
    ticker: str,
    company_name: str = "",
    live: bool = False,
) -> CompanyFundamentals:
    """
    Main entry point.
    live=True  → try yfinance first, fall back to mock then stub
    live=False → mock only, fall back to stub
    """
    if live:
        if ticker in _LIVE_CACHE:
            return _LIVE_CACHE[ticker]
        f = get_fundamentals_from_yfinance(ticker)
        if f:
            _LIVE_CACHE[ticker] = f
            return f
        # yfinance failed — fall through to mock
        print(f"[fundamentals] yfinance failed for {ticker}, falling back to mock")

    # Mock
    f = get_fundamentals(ticker)
    if f:
        return f

    # Stub — unknown company, all None
    return CompanyFundamentals(
        ticker=ticker,
        company_name=company_name or ticker,
        data_source="none",
        data_notes="No data available. Scores are conservative floor estimates. Verify manually.",
    )
