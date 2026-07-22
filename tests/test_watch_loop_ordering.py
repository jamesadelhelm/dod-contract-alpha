"""
Regression test for _run_watch_loop()'s prior-state capture ordering.

main() writes the current run's scores to data/last_scores.json as one of
its last steps (via _save_last_scores). _run_watch_loop() previously called
_load_last_scores() AFTER main() returned, which reads back the state that
main() had just written for the run that just happened, not the run before
it. Every comparison in the loop (SELL / REDUCE / REVIEW / NEW BUY) compared
the current run against itself, so the alert system could never fire
regardless of what actually changed between runs. The fix captures prior
state before calling main().

This test doesn't need real scoring — it only verifies the call order.
"""
from unittest.mock import patch, call

import main as main_module


def test_last_scores_loaded_before_main_runs():
    call_order = []

    def _fake_load_last_scores():
        call_order.append("load_last_scores")
        return {}

    def _fake_main():
        call_order.append("main")
        return []  # no scores -> loop logs "no scores returned" and continues

    args = type("Args", (), {
        "watch_interval": 1, "alert_email": None, "smtp_from": None, "smtp_server": "smtp.gmail.com",
    })()

    with patch.object(main_module, "_load_last_scores", side_effect=_fake_load_last_scores), \
         patch.object(main_module, "main", side_effect=_fake_main), \
         patch("time.sleep", side_effect=KeyboardInterrupt):
        main_module._run_watch_loop(args)

    assert call_order == ["load_last_scores", "main"], (
        f"expected _load_last_scores() to run before main(), got: {call_order}"
    )
