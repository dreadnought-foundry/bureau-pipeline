"""RED-first tests for DRE-5802 — recovering a crashed review must clear the
dead run's red check, not leave it on the head beside a fresh verdict.

THE BUG (proof DRE-5232, docs/verifier-fail-proof-2026-10.md §7): a critic
run died on a revoked token and left a failed `call / review` check on the
pull request's head. A re-review dispatched with `gh workflow run` gave an
APPROVE, but a workflow_dispatch run is attributed to the default branch, so
its check lands on main and the dead run's red check stays on the head. The
console then read the PR as APPROVED_BLOCKED with no `checks_green_at`, and
the "approved PR not merging" alert, which answers only for MERGE_GATE,
could not fire. The sweep's own recovery took the same route:
`recover_crashed_reviews` → `_nudge` → `gh workflow run`. Re-running the
ORIGINAL run (`gh run rerun 37220489170`) turned the check green and the
console's row moved to MERGE_GATE.

FIX UNDER TEST — `recover_crashed_reviews` re-runs the crashed review run at
the head (`gh run rerun <id> --failed`, the call limit recovery already
makes), so its new attempt replaces the red check on the head. It falls back
to the old dispatch only when no crashed run can be found or the re-run is
refused, because re-reviewing with a stale red check is still better than not
re-reviewing. A re-run in flight shows as a live `call / review` at the head,
and the sweep must leave it alone even where the head-bound check (DRE-2291)
still reads as crashed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_crashed_review_rerun.py -v
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
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

SHA = "a" * 40
REPO = "dreadnought-foundry/atlas"
TAG = reconcile.CRASHED_REVIEW_DISPATCH_TAG
BOUND = reconcile.HEAD_REVIEW_CHECK_NAME

CRASHED_CHECKS = json.dumps([["completed", "failure", "call / review"]])


@pytest.fixture(autouse=True)
def _product_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._pr_listing = None
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._pr_listing = None


def _pr(number=141, comments=()):
    return {
        "number": number,
        "headRefName": "agent/DRE-2290-widget",
        "headRefOid": SHA,
        "mergeStateStatus": "BLOCKED",
        "isDraft": False,
        "comments": list(comments),
    }


def _run(run_id, conclusion="failure", status="completed",
         created="2026-10-04T17:30:00Z", event="pull_request"):
    """One row of `gh run list --commit <head> --json …` for the review stub."""
    return {
        "databaseId": run_id,
        "status": status,
        "conclusion": conclusion,
        "createdAt": created,
        "event": event,
        "url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def _sweep(prs, checks=CRASHED_CHECKS, head_runs=(), dispatch_runs=(),
           rerun_rc=0):
    """Run recover_crashed_reviews once against a stubbed `gh`.

    `head_runs` answers the run listing filtered to the head commit (the
    crashed run to re-run); `dispatch_runs` answers the workflow_dispatch
    in-flight probe. Every re-run and dispatch is recorded."""
    state = {"calls": [], "reruns": [], "dispatches": [], "receipts": [],
             "reports": []}

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        args = list(argv[1:])
        state["calls"].append(args)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["pr", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(prs), stderr="")
        if args[0] == "api" and "/check-runs" in args[1]:
            return SimpleNamespace(returncode=0, stdout=checks, stderr="")
        if args[:2] == ["run", "list"]:
            rows = head_runs if "--commit" in args else dispatch_runs
            return SimpleNamespace(returncode=0, stdout=json.dumps(list(rows)),
                                   stderr="")
        if args[:2] == ["run", "rerun"]:
            state["reruns"].append(args)
            if rerun_rc:
                return SimpleNamespace(returncode=rerun_rc, stdout="",
                                       stderr="HTTP 403: run too old to re-run")
            return ok
        if args[:2] == ["workflow", "run"]:
            state["dispatches"].append(args)
            return ok
        if args[:2] == ["pr", "comment"]:
            state["receipts"].append(args[args.index("--body") + 1])
            return ok
        raise AssertionError(f"unexpected gh call: {argv}")

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
         patch.object(reconcile.linear_ops, "comment_bodies",
                      side_effect=lambda ident: []), \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=lambda ident, body:
                      state["reports"].append((ident, body))):
        reconcile.recover_crashed_reviews()
    return state


def test_recovery_reruns_the_crashed_run_instead_of_dispatching():
    """ACCEPTANCE (DRE-5802): the recovery call is a re-run of the dead run
    itself, so its new attempt replaces the red `call / review` on the head.
    A `gh workflow run` dispatch lands its check on main and leaves the red
    one standing, which is what kept the proof's PR reading APPROVED_BLOCKED."""
    state = _sweep([_pr()], head_runs=[_run(37220489170)])
    assert state["reruns"] == [
        ["run", "rerun", "37220489170", "--failed", "--repo", REPO]
    ], "the crashed run at the head must be re-run, failed jobs only"
    assert state["dispatches"] == [], (
        "a dispatched re-review cannot clear the dead run's red check — "
        "it must not be the recovery call when the run is known"
    )
    assert len(state["receipts"]) == 1, "the re-run is still receipted"
    body = state["receipts"][0]
    assert TAG in body and SHA in body, (
        "the receipt keeps the tag + full head sha — the per-head cap and "
        "the runner-environment hold both count it"
    )
    assert "37220489170" in body, "the receipt names the run it re-ran"
    assert "NOT a code rejection" in body
    assert reconcile._write_failures == []


def test_reruns_the_newest_crashed_run_at_the_head():
    """Two dead runs at one head: the newest is the check GitHub reads for
    the name, so re-running it is what puts a fresh attempt on top. Green and
    still-running rows are never re-run."""
    runs = [
        _run(100, conclusion="cancelled", created="2026-10-04T17:00:00Z"),
        _run(300, conclusion="success", created="2026-10-04T17:50:00Z"),
        _run(200, conclusion="timed_out", created="2026-10-04T17:20:00Z"),
    ]
    state = _sweep([_pr()], head_runs=runs)
    assert [r[2] for r in state["reruns"]] == ["200"]
    assert state["dispatches"] == []


def test_no_crashed_run_at_the_head_falls_back_to_the_dispatch():
    """With no run to re-run (none found at the head), the old dispatch
    still re-reviews the PR — a review with a stale red check beside it is
    better than none."""
    state = _sweep([_pr()], head_runs=[])
    assert state["reruns"] == []
    assert len(state["dispatches"]) == 1
    assert "pr_number=141" in state["dispatches"][0]
    assert len(state["receipts"]) == 1


def test_refused_rerun_falls_back_to_the_dispatch():
    """GitHub refuses a re-run of an old run. The sweep then dispatches, so
    the PR is still re-reviewed, and says in the log why the red check may
    stay."""
    state = _sweep([_pr()], head_runs=[_run(37220489170)], rerun_rc=1)
    assert len(state["reruns"]) == 1
    assert len(state["dispatches"]) == 1, (
        "a refused re-run must not leave the crashed review un-retried"
    )
    assert len(state["receipts"]) == 1, "one retry, one receipt — not two"


def test_a_rerun_in_flight_is_left_alone_although_the_bound_check_crashed():
    """A re-run's new attempt is a pull_request run, so the workflow_dispatch
    in-flight probe cannot see it — its live `call / review` at the head is
    the only sign. The head-bound check still carries the crash it recorded,
    and DRE-2291's filter would hide the live check behind it: the next sweep
    would read the head as crashed with its one retry spent and report the
    reviewer down while the re-run is still reviewing."""
    checks = json.dumps([
        ["completed", "failure", BOUND],
        ["in_progress", "", "call / review"],
    ])
    receipt = {
        "author": {"login": "agent-bureau-bot"},
        "body": f"🔁 {TAG} @{SHA}: re-ran the crashed review run",
        "createdAt": "2026-10-04T17:40:00Z",
    }
    state = _sweep([_pr(comments=[receipt])], checks=checks,
                   head_runs=[_run(1, status="in_progress", conclusion="")])
    assert state["reruns"] == [] and state["dispatches"] == []
    assert state["reports"] == [], (
        "a review still running is not an outage — no reviewer-down report"
    )
