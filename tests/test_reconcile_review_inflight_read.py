"""The in-flight review read is DRE-1254 again, on the read path.

DRE-1254 (2026-06-12) found that every `gh workflow run` in this sweep ran
under the minted App token, which lacks Actions:write — GitHub answered
"HTTP 403: Resource not accessible by integration", the silent ``gh()``
helper discarded it, and the sweep printed "dispatching" while nothing ran.
The write path was fixed twice over: failures raise, and the call runs under
``GH_DISPATCH_TOKEN`` (the stub's github.token, which holds actions:write).

``_review_dispatch_in_flight()`` reads the SAME Actions API and never got
either half. It still calls the silent ``gh()`` under the App token, and
``json.loads("")`` raises ValueError, which it catches and turns into
``return True`` — "a review is still in flight". That is fail-closed and
correct for a TRANSIENT read failure (re-dispatching would cancel a live run
via the stub's concurrency group, the DRE-2032 watchdog-kills-its-patient
class). It is catastrophic for a PERMANENT one: the answer is always True,
so every receipt-bearing head waits forever, the sweep stays green, and the
log reassures with "waiting, never double-dispatching".

Reproduced live 2026-08-17 against DeltaSolv/deltasolv with a real minted
bot token — the exact call the sweep makes:

    gh run list --repo DeltaSolv/deltasolv --workflow qa-review.yml \
       --event workflow_dispatch --limit 50 --json status
    -> HTTP 403: Resource not accessible by integration
       (https://api.github.com/repos/DeltaSolv/deltasolv/actions/workflows/qa-review.yml)

Both the workflow-scoped and the repo-wide runs endpoints 403 for the App,
so this is not a query-shape problem — it is the wrong token. Dependabot PRs
#211, #212 and #213 sat wedged behind it through every sweep for a full day,
their dispatched reviews having already concluded (failure) hours earlier.

DESIRED behavior (these tests express it; they FAIL on the unfixed code and
pass after the fix):

  1. The in-flight read runs under ``GH_DISPATCH_TOKEN`` when set, exactly
     as the dispatch does.
  2. An unreadable listing is LOUD — recorded in ``_read_failures`` so the
     sweep exits 1 and medic sees it — while STILL returning True, because
     cancelling a live review remains the worse failure.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_review_inflight_read.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("REPO", "DeltaSolv/deltasolv")
os.environ.setdefault("REPO_SLUG", "deltasolv")

import reconcile  # noqa: E402

# The verbatim stderr GitHub returns to the App token for this read.
_REAL_403 = (
    "HTTP 403: Resource not accessible by integration "
    "(https://api.github.com/repos/DeltaSolv/deltasolv/actions/"
    "workflows/qa-review.yml)"
)


def _run_stub(returncode: int, stdout: str, calls: list):
    """subprocess.run stub recording argv/env of the run-list read."""

    def fake_run(argv, **kwargs):
        calls.append({"argv": list(argv), "env": kwargs.get("env")})
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr="" if returncode == 0 else _REAL_403,
        )

    return fake_run


def test_inflight_read_uses_dispatch_token_when_set():
    """The Actions runs API is unreadable to the App token, so this read must
    run under GH_DISPATCH_TOKEN — the same swap gh_dispatch already does."""
    calls: list = []
    with patch.dict(
        os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch", "GH_TOKEN": "ghs_app"}
    ):
        with patch.object(
            reconcile.subprocess, "run", side_effect=_run_stub(0, "[]", calls)
        ):
            reconcile._review_dispatch_in_flight()

    assert calls, "the in-flight read should have shelled out to gh"
    env = calls[0]["env"]
    assert env is not None and env.get("GH_TOKEN") == "ghs_dispatch", (
        "the in-flight read must execute with GH_TOKEN=GH_DISPATCH_TOKEN — the "
        "App token 403s on the Actions runs API, which silently pins the answer "
        "to 'in flight' forever (DRE-1254, read path)"
    )


def test_unreadable_listing_is_recorded_not_silent():
    """A 403 must land in _read_failures so the sweep exits 1 and goes red.

    Without this the stall is invisible: green sweep, reassuring log line,
    and every receipt-bearing head waits on a run that concluded long ago.
    """
    before = list(reconcile._read_failures)
    calls: list = []
    try:
        with patch.object(
            reconcile.subprocess, "run", side_effect=_run_stub(1, "", calls)
        ):
            reconcile._review_dispatch_in_flight()
        new = reconcile._read_failures[len(before):]
        assert new, (
            "an unreadable in-flight listing must be recorded as a read failure "
            "(the sweep then exits 1); silently answering 'in flight' is how "
            "#211/#212/#213 wedged for a day behind a green sweep"
        )
        assert any("403" in f or "rc=1" in f for f in new), (
            f"the recorded failure should carry the gh error; got {new}"
        )
    finally:
        del reconcile._read_failures[len(before):]


def test_unreadable_listing_still_answers_in_flight():
    """Fail-CLOSED stays: an unreadable listing must NOT be read as 'nothing
    running'. Re-dispatching over a live review cancels it via the stub's
    concurrency group (DRE-2032) — visibility is the fix, not recklessness."""
    before = list(reconcile._read_failures)
    try:
        with patch.object(
            reconcile.subprocess, "run", side_effect=_run_stub(1, "", [])
        ):
            assert reconcile._review_dispatch_in_flight() is True, (
                "an unreadable listing must still answer 'in flight' — never "
                "risk cancelling a live review"
            )
    finally:
        del reconcile._read_failures[len(before):]


def test_all_completed_runs_are_not_in_flight():
    """The healthy path is unchanged: every run completed -> not in flight.

    This is the state DeltaSolv/deltasolv was actually in while the sweep
    insisted a review was running: 50 of 50 dispatched review runs completed.
    """
    stdout = '[{"status": "completed"}, {"status": "completed"}]'
    with patch.object(reconcile.subprocess, "run", side_effect=_run_stub(0, stdout, [])):
        assert reconcile._review_dispatch_in_flight() is False


def test_a_running_run_is_in_flight():
    """And a genuinely live run still blocks the re-dispatch."""
    stdout = '[{"status": "completed"}, {"status": "in_progress"}]'
    with patch.object(reconcile.subprocess, "run", side_effect=_run_stub(0, stdout, [])):
        assert reconcile._review_dispatch_in_flight() is True
