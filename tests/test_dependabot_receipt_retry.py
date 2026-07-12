"""RED-first tests for DRE-2071 — a dependabot dispatch receipt must not
permanently block re-dispatch after a CRASHED review; retry bounded per head.

THE BUG (bit twice live, 2026-07-12): review_dependabot_prs() posts a
sha-bound receipt so a review is dispatched once per head (DRE-2047/2049).
But when the dispatched review run CRASHES (infra failure — no verdict ever
posted), the receipt still stands: the rail never retries that head, and
recovery required a human every time (morning: 27 agent-bureau reviews
crashed pre-DRE-2052, operator rebased everything; evening: atlas/deltasolv
crashed on the v3 actor/secrets gaps, operator hand-dispatched 6 reviews).

FIX UNDER TEST — the receipt becomes OUTCOME-AWARE:
  1. At sweep time, a receipt-bearing head with no bound verdict resolves
     the dispatched run's state from GitHub (`gh run list` on the review
     stub's workflow_dispatch runs): a queued/in_progress run means the
     review is still in flight — never re-dispatch (the stub's per-PR
     concurrency group would CANCEL the live run, the DRE-2032
     watchdog-kills-its-patient class); no in-flight run means the
     dispatched review CONCLUDED without a verdict — a green review always
     posts one, so this is the failure/cancelled crash case.
  2. A crashed run's receipt does NOT block: dispatch again, post a second
     receipt.
  3. Bounded (bp#50, the medic-loop lesson — never infinite-retry a
     crashing reviewer): at most DEPENDABOT_RECEIPT_CAP (= 2) worker-bot
     receipts per head sha. At the cap, stop and surface on the
     fail-loudly rail (ERROR line + _write_failures → red sweep run)
     instead of looping.
  4. An unreadable run listing reads as IN FLIGHT (fail closed — never
     risk cancelling a live review on an API blip).
  5. A verdict bound to the current head settles the PR exactly as today.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

SHA = "a" * 40

TAG = getattr(reconcile, "DEPENDABOT_DISPATCH_TAG", "dependabot-review-dispatch")


@pytest.fixture(autouse=True)
def _selfhost_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _pr(comments=(), number=93, sha=SHA):
    return {
        "number": number,
        "headRefName": "dependabot/github_actions/actions-minor-patch-0f5a1b2c3d",
        "author": {"login": "dependabot"},
        "headRefOid": sha,
        "mergeStateStatus": "CLEAN",
        "comments": list(comments),
    }


def _verdict(sha):
    """A genuine qa-bot verdict comment, sha-bound (DRE-1990 shape)."""
    return {
        "author": {"login": "agent-bureau-qa-bot"},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}\n\nRoutine grouped bump.",
    }


def _receipt(sha=SHA, author="agent-bureau-bot"):
    """The sweep's own once-per-sha dispatch receipt (worker-bot authored)."""
    return {
        "author": {"login": author},
        "body": f"🔁 {TAG} @{sha}: critic dispatched via workflow_dispatch (DRE-2047)",
    }


def _run_factory(state):
    """subprocess.run stub covering exactly the gh calls this backstop makes:
    pr list (the scan), run list (the dispatched run's outcome), workflow run
    (the dispatch), pr comment (the receipt)."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["prs"]), stderr=""
            )
        if argv[1] == "run" and argv[2] == "list":
            state["run_list_calls"].append(argv)
            if state.get("runs_rc"):
                return SimpleNamespace(
                    returncode=1, stdout="", stderr="HTTP 403: rate limited"
                )
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["runs"]), stderr=""
            )
        if argv[1] == "workflow" and argv[2] == "run":
            state["dispatches"].append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "pr" and argv[2] == "comment":
            state["receipts"].append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call: {argv}")

    return fake_run


def _sweep(prs, runs=(), runs_rc=0):
    state = {
        "prs": prs,
        "runs": list(runs),
        "runs_rc": runs_rc,
        "run_list_calls": [],
        "dispatches": [],
        "receipts": [],
    }
    with patch.object(reconcile.subprocess, "run", side_effect=_run_factory(state)):
        reconcile.review_dependabot_prs()
    return state


def _dispatched_numbers(state):
    return [
        int(arg.removeprefix("pr_number="))
        for argv in state["dispatches"]
        for arg in argv
        if arg.startswith("pr_number=")
    ]


# --------------------------------------------------------------------------
# The retry: a crashed run's receipt no longer blocks
# --------------------------------------------------------------------------
def test_crashed_receipt_with_no_verdict_retries_once():
    """ACCEPTANCE: one receipt, no verdict, no review run in flight — the
    dispatched review concluded without a verdict (crashed). The sweep must
    dispatch again and post a SECOND sha-bound receipt, so a transient critic
    crash self-heals without human action."""
    state = _sweep(
        [_pr(comments=[_receipt()])],
        runs=[{"status": "completed"}],
    )
    assert len(state["dispatches"]) == 1, (
        "a crashed review run's receipt must not block the retry"
    )
    assert "pr_number=93" in state["dispatches"][0]
    assert len(state["receipts"]) == 1, "the retry must post its own receipt"
    body = state["receipts"][0][state["receipts"][0].index("--body") + 1]
    assert TAG in body and SHA in body, (
        "the retry receipt carries the same tag + full head sha — that pair "
        "is what the cap counts"
    )
    assert not reconcile._write_failures, "one retry is healthy, not an error"


def test_two_crashed_receipts_stop_retrying_and_fail_loudly(capsys):
    """ACCEPTANCE (bp#50): after the second crash the sweep must NOT loop —
    no dispatch, and the stall surfaces on the fail-loudly rail (an ERROR
    line + a recorded failure so the sweep run goes red)."""
    state = _sweep(
        [_pr(comments=[_receipt(), _receipt()])],
        runs=[{"status": "completed"}],
    )
    assert state["dispatches"] == [], (
        "two receipts per head sha is the retry cap — a persistently "
        "crashing reviewer must never be dispatched a third time"
    )
    assert state["receipts"] == []
    assert reconcile._write_failures, (
        "the capped head must be recorded so the sweep run exits red"
    )
    err = capsys.readouterr().err
    assert "ERROR" in err and "93" in err, (
        "the stall must surface as a loud ERROR line naming the PR"
    )


def test_receipt_cap_is_two():
    assert reconcile.DEPENDABOT_RECEIPT_CAP == 2, (
        "at most 2 dispatched reviews per head sha — the bp#50 bound"
    )


def test_verdict_present_suppresses_retry_and_error():
    """A successful run that posts a bound verdict behaves exactly as today:
    settled — no dispatch, no run lookup, no error, even past the cap."""
    state = _sweep(
        [_pr(comments=[_receipt(), _receipt(), _verdict(SHA)])],
        runs=[{"status": "completed"}],
    )
    assert state["dispatches"] == []
    assert not reconcile._write_failures, (
        "a verdict-settled head is healthy — the cap error must not fire"
    )
    assert state["run_list_calls"] == [], (
        "a bound verdict settles the head — no run lookup needed"
    )


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting"])
def test_in_flight_review_run_defers_the_retry(status):
    """ACCEPTANCE: no duplicate dispatch while a review run is still in
    flight — the stub's per-PR concurrency group (cancel-in-progress) would
    CANCEL the live run, manufacturing the very crash being retried."""
    state = _sweep(
        [_pr(comments=[_receipt()])],
        runs=[{"status": status}],
    )
    assert state["dispatches"] == [], (
        f"a {status} review run means the dispatched review may still "
        "produce its verdict — wait, never double-dispatch"
    )
    assert not reconcile._write_failures, "waiting is healthy, not an error"


def test_in_flight_run_defers_the_cap_error_too():
    """Two receipts + a run still in flight is attempt 2 IN PROGRESS, not a
    stall — the loud error fires only once the second run has crashed."""
    state = _sweep(
        [_pr(comments=[_receipt(), _receipt()])],
        runs=[{"status": "in_progress"}],
    )
    assert state["dispatches"] == []
    assert not reconcile._write_failures


def test_unreadable_run_listing_reads_as_in_flight(capsys):
    """Fail CLOSED on an API blip: an unreadable run listing must never be
    taken as "nothing in flight" — a re-dispatch on that fabricated
    emptiness would cancel a live review (DRE-2034 read discipline)."""
    state = _sweep([_pr(comments=[_receipt()])], runs_rc=1)
    assert state["dispatches"] == [], (
        "an unreadable run listing must defer the retry, not trigger it"
    )


def test_fresh_pr_dispatches_without_a_run_lookup():
    """The receipt-free fast path is unchanged (and costs no extra API
    call): a fresh dependabot head dispatches exactly as today, even while
    another PR's review run is in flight."""
    state = _sweep([_pr()], runs=[{"status": "in_progress"}])
    assert len(state["dispatches"]) == 1
    assert state["run_list_calls"] == [], (
        "a head with no receipt has no dispatched run to resolve"
    )


def test_forged_receipts_do_not_count_toward_the_cap():
    """DRE-1998 discipline: only worker-bot receipts count. A forger must
    not be able to exhaust the retry cap and freeze the head — two planted
    receipts plus one real crashed one is attempt 1, so the retry fires."""
    state = _sweep(
        [_pr(comments=[
            _receipt(author="mallory"),
            _receipt(author="mallory"),
            _receipt(),
        ])],
        runs=[{"status": "completed"}],
    )
    assert len(state["dispatches"]) == 1, (
        "forged receipts are invisible — the single real crashed receipt "
        "leaves one retry in the budget"
    )
    assert not reconcile._write_failures


def test_stale_receipts_do_not_count_toward_the_cap():
    """Receipts bound to a superseded head are dead: a rebase re-arms the
    NEW head with a fresh budget — two old-sha receipts plus a crash on the
    new head must not read as capped."""
    state = _sweep(
        [_pr(comments=[_receipt(sha="b" * 40), _receipt(sha="b" * 40), _receipt()])],
        runs=[{"status": "completed"}],
    )
    assert len(state["dispatches"]) == 1
    assert not reconcile._write_failures


def test_retries_share_the_per_sweep_cap_oldest_first():
    """Retries and fresh dispatches drain through the same DRE-2049 pacing
    quota, oldest PR first — a retry is a full critic run and must not dodge
    the burst bound."""
    state = _sweep(
        [
            _pr(number=104),
            _pr(number=103),
            _pr(number=102, comments=[_receipt()]),
            _pr(number=101, comments=[_receipt()]),
        ],
        runs=[{"status": "completed"}],
    )
    assert _dispatched_numbers(state) == [101, 102, 103], (
        "crashed-receipt retries and fresh heads share the per-sweep cap, "
        f"oldest first — got {_dispatched_numbers(state)}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
