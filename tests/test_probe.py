"""Readiness must track process health, not collection history.

Regression guard for the 2026-09-07 deploy: a pod rolled out at 14:39 UTC with
RUN_AT_UTC_HOUR=5 and a recent successful cycle had nothing due, never set the
old STATE["ready"], and tripped ProgressDeadlineExceeded on a healthy collector.
"""
from estate_analytics.main import probe

FRESH = {"started": False, "collected": False, "last": None}
RUNNING = {"started": True, "collected": False, "last": None}
COLLECTED = {"started": True, "collected": True, "last": {"at": "2026-09-07T05:00:00+00:00"}}

def test_not_ready_before_schema_is_ensured():
    assert probe("/ready", FRESH)[0] == 503

def test_ready_once_the_loop_runs_even_with_no_cycle_yet():
    """The bug: this returned 503 for up to ~14h after a deploy."""
    assert probe("/ready", RUNNING)[0] == 200

def test_ready_stays_ready_after_a_cycle():
    assert probe("/ready", COLLECTED)[0] == 200

def test_health_is_200_even_before_startup_completes():
    assert probe("/health", FRESH)[0] == 200

def test_unknown_path_404s():
    assert probe("/nope", RUNNING)[0] == 404

def test_payload_still_exposes_collection_state():
    """Readiness stops reporting it, so the payload must."""
    _, payload = probe("/ready", COLLECTED)
    assert payload["collected"] is True
    assert payload["last"]["at"] == "2026-09-07T05:00:00+00:00"
    _, payload = probe("/ready", RUNNING)
    assert payload["collected"] is False
