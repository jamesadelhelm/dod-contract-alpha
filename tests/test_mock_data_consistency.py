"""
Internal consistency check for data/mock_fundamentals.json: market_cap_millions
must be reasonably close to shares_millions * current_price — a basic
arithmetic identity (market cap = shares outstanding * price), not a matter
of interpretation.

Five tickers (BA, AVAV, VVX, AMTM, FLR) were found off by 15-63%, most
traceable to corporate actions the curated data hadn't caught up with
(AVAV's BlueHalo stock-funded acquisition roughly doubled its share count;
AMTM's share count grew ~46% YoY post-spin; Boeing has issued substantial
new equity since the 737 MAX crisis). market_cap_millions was corrected to
match shares x price (using verified real share counts where a clear
corporate action explained the drift) for each. This test keeps that
specific class of drift from silently reaccumulating; a generous tolerance
avoids flagging ordinary rounding noise in hand-curated illustrative data.
"""
import json

from config import MOCK_FUNDAMENTALS_PATH

_TOLERANCE_PCT = 15.0


def _load_mock():
    return json.loads(MOCK_FUNDAMENTALS_PATH.read_text())


def test_market_cap_matches_shares_times_price():
    mock = _load_mock()
    violations = []
    for ticker, entry in mock.items():
        mc = entry.get("market_cap_millions")
        shares = entry.get("shares_millions")
        price = entry.get("current_price")
        if not (mc and shares and price):
            continue
        implied = shares * price
        diff_pct = abs(implied - mc) / mc * 100
        if diff_pct > _TOLERANCE_PCT:
            violations.append((ticker, mc, implied, diff_pct))

    assert not violations, (
        "market_cap_millions diverges from shares_millions * current_price "
        f"by more than {_TOLERANCE_PCT:.0f}% for: "
        + ", ".join(
            f"{t} (given=${mc:,.0f}M, shares*price=${implied:,.0f}M, {diff:.0f}% off)"
            for t, mc, implied, diff in violations
        )
    )
