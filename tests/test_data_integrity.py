"""
Cross-file data integrity checks between config.py's CURATED_GOV_REVENUE_PCT
and data/mock_fundamentals.json's per-ticker dod_revenue_pct.

CURATED_GOV_REVENUE_PCT is documented (config.py) as "total US-government
revenue % (DoD + IC + civil agencies)" — DoD-only revenue is by definition a
subset of that total, so for any ticker present in both sources, the curated
total-government figure can never be lower than the overlay's DoD-only
figure. A violation means the two sources have drifted (one updated after a
corporate restructuring, spinoff, or 10-K refresh, the other not) — exactly
the "overlay staleness" risk the README's Limitations section calls out.
This was found and fixed for GE, KBR, ACM, IBM, and VSAT in this repo; this
test exists so the class of bug doesn't silently reappear.
"""
import json

from config import CURATED_GOV_REVENUE_PCT, MOCK_FUNDAMENTALS_PATH


def _load_mock():
    return json.loads(MOCK_FUNDAMENTALS_PATH.read_text())


def test_total_gov_pct_never_below_dod_only_pct():
    mock = _load_mock()
    violations = []
    for ticker, total_gov_pct in CURATED_GOV_REVENUE_PCT.items():
        entry = mock.get(ticker)
        if not entry:
            continue
        dod_pct = entry.get("dod_revenue_pct")
        if dod_pct is None:
            continue
        if total_gov_pct < dod_pct - 0.01:  # tolerate float rounding noise
            violations.append((ticker, total_gov_pct, dod_pct))

    assert not violations, (
        "CURATED_GOV_REVENUE_PCT (total US government %) is lower than the "
        "DoD-only % in mock_fundamentals.json for: "
        + ", ".join(f"{t} (total={g}% < DoD={d}%)" for t, g, d in violations)
    )
