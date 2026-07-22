"""
End-to-end smoke tests: run the actual CLI entry point against the bundled
offline mock data and verify the pipeline completes and produces a sane
report. Unlike the rest of the suite, these exercise main.py, the full
contract -> fundamentals -> scoring -> DCF -> report pipeline wiring, and
markdown generation together — the kind of "did I wire this up right" bug
that unit tests on individual functions can't catch (e.g. a renamed
keyword argument between main.py and generate_report(), or a report
section crashing only when real mock data flows through it end to end).

Runs entirely offline (--source mock --no-live) so it has no network
dependency and stays fast enough for the normal test suite.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args, timeout=30):
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestMockPipelineSmoke:
    def test_scores_only_run_exits_cleanly(self):
        result = _run_cli("--source", "mock", "--no-live", "--no-report")
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert "Results" in result.stdout

    def test_full_report_generation(self, tmp_path):
        out_path = tmp_path / "smoke_report.md"
        result = _run_cli(
            "--source", "mock", "--no-live", "--output", str(out_path)
        )
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert out_path.exists()

        content = out_path.read_text()
        assert len(content) > 5000  # a real report is tens of KB, not a stub

        # Core sections that should always render for the 20-company mock universe
        for heading in [
            "# DoD Contract Intelligence Report",
            "## Executive Summary",
            "## 1. Action Summary",
            "## 2. Valuation Snapshot",
            "## 3. Red Flags",
        ]:
            assert heading in content, f"missing section: {heading}"

        # No leaked Python error text or unresolved format placeholders
        assert "Traceback (most recent call last)" not in content
        assert "{s.ticker}" not in content  # unresolved f-string artifact
        assert "None%" not in content

    def test_brief_mode_runs_and_is_condensed(self, tmp_path):
        out_path = tmp_path / "smoke_brief.md"
        result = _run_cli(
            "--source", "mock", "--no-live", "--brief", "--output", str(out_path)
        )
        assert result.returncode == 0, result.stderr
        content = out_path.read_text()
        assert "Executive Summary" in content
        # Brief mode explicitly omits the full DCF/contract-listing sections
        assert "## 9. Contract Awards" not in content

    def test_top_and_min_score_filters_compose(self, tmp_path):
        out_path = tmp_path / "smoke_filtered.md"
        result = _run_cli(
            "--source", "mock", "--no-live", "--top", "5", "--min-score", "0",
            "--output", str(out_path),
        )
        assert result.returncode == 0, result.stderr
        assert out_path.exists()

    def test_json_scores_output_is_valid_json(self, tmp_path):
        result = _run_cli(
            "--source", "mock", "--no-live", "--no-report", "--json"
        )
        assert result.returncode == 0, result.stderr
        match = None
        for line in result.stdout.splitlines():
            if "JSON scores saved" in line:
                match = line.split("→", 1)[1].strip()
        assert match, "expected a 'JSON scores saved → <path>' line in stdout"
        json_path = REPO_ROOT / match
        try:
            assert json_path.exists()
            data = json.loads(json_path.read_text())
            assert isinstance(data, list) and len(data) > 0
            assert "ticker" in data[0] and "final_score" in data[0]
        finally:
            json_path.unlink(missing_ok=True)
