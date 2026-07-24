"""
Keeps README.md's stated data/ticker_map.yaml entry counts in sync with the
actual file. This drifted once already (README said "210-entry ... 161
public tickers, 49 private suppressions" while the file had grown to 228
entries / 179 public / 49 private) after later commits added entries without
updating the docs. This test doesn't prevent the count from changing — it
just makes sure README.md is updated in the same PR that changes the map.
"""
import re
from pathlib import Path

import yaml

from config import TICKER_MAP_PATH, BASE_DIR

README_PATH = BASE_DIR / "README.md"


def _counts():
    d = yaml.safe_load(TICKER_MAP_PATH.read_text())
    total = len(d)
    public = sum(1 for v in d.values() if isinstance(v, dict) and v.get("ticker") not in (None, "null"))
    private = total - public
    return total, public, private


def test_readme_matches_ticker_map_entry_counts():
    total, public, private = _counts()
    readme = README_PATH.read_text()

    assert f"{total}-entry curated subsidiary map" in readme, (
        f"README pipeline diagram says a different entry count than the actual "
        f"{total}-entry data/ticker_map.yaml — update the README when the map changes."
    )
    assert f"{total}-entry `data/ticker_map.yaml`: {public} public tickers, {private} explicit private suppressions" in readme, (
        f"README's Ticker Resolution section doesn't match the actual counts "
        f"(total={total}, public={public}, private={private})."
    )
