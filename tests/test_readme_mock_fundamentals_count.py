"""
Keeps README.md's stated data/mock_fundamentals.json entry count in sync
with the actual file. The pipeline diagram said "44-entry" / "44 defense
and adjacent companies" while the Customization section correctly said
"46-entry" a few hundred lines later — the file itself has 46 entries, so
the pipeline diagram had drifted out of sync with a later, correct update
elsewhere in the same document.
"""
import json

from config import MOCK_FUNDAMENTALS_PATH, BASE_DIR

README_PATH = BASE_DIR / "README.md"


def test_readme_matches_mock_fundamentals_entry_count():
    count = len(json.loads(MOCK_FUNDAMENTALS_PATH.read_text()))
    readme = README_PATH.read_text()

    assert f"{count}-entry database" in readme, (
        f"README pipeline diagram's overlay entry count doesn't match the actual "
        f"{count}-entry data/mock_fundamentals.json — update the README when entries are added/removed."
    )
    assert f"{count}-entry curated database" in readme, (
        f"README's Customization section overlay entry count doesn't match the actual "
        f"{count}-entry data/mock_fundamentals.json."
    )
