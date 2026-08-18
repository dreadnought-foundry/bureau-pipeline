"""RED-first tests: a workflow absent BY DESIGN is not an unreadable one (DRE-2525).

THE BUG (live, 2026-08-18): the sweep asks GitHub for `agent-fix.yml`'s runs on
every repo it sweeps, unconditionally. `dreadnought-foundry/bureau-harness` does
not have that stub, so the read fails and the sweep records:

    ERROR: gh run list --repo dreadnought-foundry/bureau-harness --workflow
    agent-fix.yml --limit 10 --json status (Actions read) failed — rc=1: HTTP
    404: workflow agent-fix.yml not found on the default branch. Treating as
    UNREADABLE; callers fail closed, and the sweep goes red rather than act on
    fabricated data.

Cost: 61 consecutive failed runs over 18h37m, ~61 emails a day, in a repo that
produced 3,074 runs in seven days — 23% of all fleet workflow runs — while
GitHub spend sat at 90% of budget.

WHAT MUST NOT CHANGE. The fail-closed posture is correct and deliberate
(DRE-2034): an unreadable Actions listing answers BUSY, is recorded, and the
sweep goes red. Only ONE thing is wrong — two different 404s are conflated:

  * the workflow FILE is not on the target repo's default branch: this consumer
    legitimately lacks an optional stub, nothing is stalled, and no run of a
    non-existent workflow can be in flight. Skip the check, stay green.
  * a revoked permission, a missing scope, an unreadable ref: still UNREADABLE,
    still fails closed, still red. Collapsing this into the case above would
    hide a real permission failure — the same mistake in the other direction.

FIX UNDER TEST — the distinction is drawn at the read boundary, on the FAILURE
path only, and drawn POSITIVELY rather than by sniffing gh's error text: when
the Actions read fails, reconcile asks the contents API (not the Actions API)
whether `.github/workflows/<file>` is on the default branch, and only then
decides whether that failure was a failure at all. Absence is provable only
from a listing that was read and parsed and does NOT contain the file.
Unreadable, empty or unparseable listings prove nothing, so the failure is
recorded and the guard fails closed exactly as it does today. A read that
SUCCEEDS is never second-guessed, so a healthy sweep costs no extra API call.

Run: cd bureau-pipeline && python3 -m pytest tests/test_absent_workflow_is_not_unreadable.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
# The suite's house default, NOT this file's subject repo. setdefault means the
# FIRST test module imported wins for the whole session, and this filename sorts
# ahead of every other one — seeding a novel REPO here silently re-pointed
# reconcile.REPO for test_close_epics / test_promote_only /
# test_dead_run_budget_reset, which failed only in full-suite collection order.
# The harness identity is pinned per test by the autouse fixture below instead.
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

# The repo from the incident, and the stub it does not have.
HARNESS = "dreadnought-foundry/bureau-harness"
FIX_STUB = "agent-fix.yml"

# What GitHub actually answers when the workflow file is not on the default
# branch, and what it answers when the token may not read the Actions API.
NOT_FOUND = (
    f"HTTP 404: workflow {FIX_STUB} not found on the default branch "
    f"(https://api.github.com/repos/{HARNESS}/actions/workflows/{FIX_STUB})"
)
FORBIDDEN = (
    "HTTP 403: Resource not accessible by integration "
    f"(https://api.github.com/repos/{HARNESS}/actions/workflows/{FIX_STUB})"
)

# The stubs a normal consumer repo does have.
PRESENT = ["agent-task.yml", FIX_STUB, "qa-review.yml", "merge-gate.yml"]
ABSENT = ["agent-task.yml", "qa-review.yml", "merge-gate.yml"]


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    """reconcile.REPO/REPO_SLUG are bound at import; pin them so this file
    reads as the harness sweep regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO", HARNESS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-harness")


@pytest.fixture(autouse=True)
def _quiet_failure_rail():
    """The read-failure rail is module state shared by 2,300 tests — snapshot
    and restore it so a recorded failure here cannot leak."""
    before = list(reconcile._read_failures)
    yield
    reconcile._read_failures[:] = before


def _stub(workflows=PRESENT, run_list=(0, "[]", ""), contents=None, calls=None):
    """An argv-dispatching subprocess.run stub covering both seams:
    the contents read of .github/workflows and the Actions run listing."""
    if calls is None:
        calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "/contents/" in joined:
            rc, out, err = contents if contents is not None else (
                0,
                json.dumps([{"name": n, "type": "file"} for n in workflows]),
                "",
            )
            return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        if argv[1:3] == ["run", "list"]:
            return SimpleNamespace(
                returncode=run_list[0], stdout=run_list[1], stderr=run_list[2]
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run, calls


def _busy(**kw):
    """Run the busy guard under a stub; returns (answer, argv list, new failures)."""
    fake_run, calls = _stub(**kw)
    before = len(reconcile._read_failures)
    with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch"}), patch.object(
        reconcile.subprocess, "run", side_effect=fake_run
    ):
        answer = reconcile._actions_runs_busy(FIX_STUB)
    return answer, calls, reconcile._read_failures[before:]


def _run_listings(calls):
    return [c for c in calls if c[1:3] == ["run", "list"]]


def _contents_reads(calls):
    return [c for c in calls if any("/contents/" in a for a in c)]


# --------------------------------------------------------------------------
# 1: the workflow file is absent — skip the check, stay GREEN
# --------------------------------------------------------------------------
def test_absent_workflow_skips_the_check_and_stays_green():
    """The incident, exactly: the harness has no agent-fix stub. No run of a
    workflow that does not exist can be in flight, so there is nothing to
    check and nothing to report."""
    answer, calls, failures = _busy(workflows=ABSENT, run_list=(1, "", NOT_FOUND))
    assert answer is False, "an absent workflow cannot have a run in flight"
    assert failures == [], (
        "an optional stub this consumer legitimately lacks is not a read "
        f"failure — the sweep must stay green, got {failures}"
    )


def test_absent_workflow_costs_one_probe_and_no_recorded_failure():
    """What the 61 red runs actually cost was the RECORDED failure, not the
    read. The read still happens once; the sweep no longer goes red for it."""
    _, calls, failures = _busy(workflows=ABSENT, run_list=(1, "", NOT_FOUND))
    assert len(_run_listings(calls)) == 1
    assert len(_contents_reads(calls)) == 1, (
        "the absence must be settled with exactly one contents read"
    )
    assert failures == []


def test_a_successful_read_is_never_second_guessed():
    """The happy path must cost NOTHING extra. This is not just economy: the
    suite's gh stubs raise on unexpected calls, so a probe on the healthy path
    would break twelve existing tests — the strictness is a feature."""
    _, calls, failures = _busy(workflows=PRESENT, run_list=(0, "[]", ""))
    assert _contents_reads(calls) == [], (
        f"a readable listing needs no absence probe: {_contents_reads(calls)}"
    )
    assert failures == []


# --------------------------------------------------------------------------
# 2: a permission failure is STILL unreadable, STILL red
# --------------------------------------------------------------------------
def test_permission_failure_is_still_unreadable_and_still_red():
    """The distinction that must survive: the file IS on the default branch, so
    a 403 is a real permission failure. Fail closed, record it, go red."""
    answer, calls, failures = _busy(workflows=PRESENT, run_list=(1, "", FORBIDDEN))
    assert _run_listings(calls), "the read must still be attempted"
    assert answer is True, "unreadable answers BUSY (DRE-2034) — never 'idle'"
    assert failures and any("403" in f or "rc=1" in f for f in failures), (
        f"the permission failure must be recorded so the sweep exits 1, got {failures}"
    )


def test_a_404_with_the_file_present_is_still_unreadable():
    """Same status code, different cause. A 404 while the workflow IS on the
    default branch is not "absent by design" — it is an unreadable ref or a
    scope the token does not have, and collapsing it would hide that."""
    answer, calls, failures = _busy(workflows=PRESENT, run_list=(1, "", NOT_FOUND))
    assert _run_listings(calls), "the read must still be attempted"
    assert answer is True
    assert failures, "a 404 that absence does not explain must still be recorded"


@pytest.mark.parametrize(
    "contents",
    [
        (1, "", "HTTP 403: Resource not accessible by integration"),
        (1, "", "HTTP 404: Not Found"),
        (0, "[]", ""),
        (0, "", ""),
        (0, "not json", ""),
        (0, '{"message": "Not Found"}', ""),
    ],
)
def test_an_unproven_absence_falls_back_to_the_old_behaviour(contents):
    """Absence is provable ONLY from a listing that was read and parsed and
    lacks the file. Unreadable, empty, garbage and non-list answers prove
    nothing — proceed to the Actions read and fail closed, as today."""
    answer, calls, failures = _busy(contents=contents, run_list=(1, "", NOT_FOUND))
    assert _run_listings(calls), (
        "an unproven absence must not skip the read — that would fail OPEN"
    )
    assert answer is True
    assert failures, "the unreadable Actions read must still be recorded"


# --------------------------------------------------------------------------
# 3: a present workflow behaves exactly as it did before
# --------------------------------------------------------------------------
def test_present_and_idle_reads_as_not_busy():
    answer, calls, failures = _busy(workflows=PRESENT, run_list=(0, "[]", ""))
    assert _run_listings(calls)
    assert answer is False
    assert failures == []


def test_present_and_running_reads_as_busy():
    answer, _, failures = _busy(
        workflows=PRESENT, run_list=(0, '[{"status": "in_progress"}]', "")
    )
    assert answer is True
    assert failures == []


def test_unparseable_run_listing_still_fails_closed():
    """The other DRE-2034 guard, untouched: a listing that parses to nothing
    usable answers BUSY and is recorded. The read SUCCEEDED, so absence is not
    even a candidate explanation and must not be consulted."""
    answer, calls, failures = _busy(workflows=PRESENT, run_list=(0, "<html>", ""))
    assert answer is True
    assert failures
    assert _contents_reads(calls) == []


def test_the_quiet_no_token_path_is_unchanged():
    """Without GH_DISPATCH_TOKEN the read goes through the silent gh() seam,
    which discards rc — so failure is indistinguishable from empty and there
    is nothing to adjudicate. Local runs and the 2,300 unit tests take this
    path; it must behave exactly as before."""
    fake_run, calls = _stub(workflows=ABSENT, run_list=(1, "", NOT_FOUND))
    before = len(reconcile._read_failures)
    with patch.dict(os.environ, {}, clear=False), patch.object(
        reconcile.subprocess, "run", side_effect=fake_run
    ):
        os.environ.pop("GH_DISPATCH_TOKEN", None)
        answer = reconcile._actions_runs_busy(FIX_STUB)
    assert answer is False, 'the quiet path reads "" as "[]" — not busy, as before'
    assert reconcile._read_failures[before:] == []
    assert _contents_reads(calls) == [], "nothing to adjudicate on the quiet path"


# --------------------------------------------------------------------------
# the presence probe itself
# --------------------------------------------------------------------------
def _probe(rc=0, stdout="", stderr="", calls=None):
    if calls is None:
        calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    with patch.object(reconcile.subprocess, "run", side_effect=fake_run):
        return reconcile.workflow_on_default_branch(FIX_STUB), calls


def test_probe_reads_the_workflows_directory_on_the_default_branch():
    listing = json.dumps([{"name": n, "type": "file"} for n in PRESENT])
    answer, calls = _probe(stdout=listing)
    assert answer is True
    endpoint = " ".join(calls[0])
    assert f"repos/{HARNESS}/contents/.github/workflows" in endpoint, (
        f"the probe must read the workflows directory, got: {endpoint}"
    )
    assert "?ref=" not in endpoint, (
        "the default branch is the question — pinning a ref would answer a "
        "different one"
    )


def test_probe_reports_absence_from_a_listing_that_lacks_the_file():
    answer, _ = _probe(stdout=json.dumps([{"name": n} for n in ABSENT]))
    assert answer is False


@pytest.mark.parametrize(
    "rc,stdout",
    [(1, ""), (0, ""), (0, "[]"), (0, "not json"), (0, '{"message": "Not Found"}')],
)
def test_probe_answers_none_when_it_cannot_prove_anything(rc, stdout):
    """None is 'I cannot tell', and it must never be mistaken for absence —
    an empty directory listing from a real repo is not a thing git can store."""
    answer, _ = _probe(rc=rc, stdout=stdout)
    assert answer is None


def test_the_probe_does_not_touch_the_actions_api():
    """Structural: the probe must stay on the contents API. The Actions API is
    the surface whose permission is in doubt, and the AST guard in
    test_reconcile_actions_reads_403.py forbids reading it via gh()."""
    _, calls = _probe(stdout=json.dumps([{"name": n} for n in PRESENT]))
    joined = " ".join(calls[0])
    assert "/actions/" not in joined, f"the probe reached the Actions API: {joined}"


# --------------------------------------------------------------------------
# the four callers: fail closed on UNREADABLE, exactly as they do today
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "func_name",
    ["unstick_conflicts", "fix_approved_but_red", "retry_dead_fix_runs",
     "restart_answered_blockers"],
)
def test_callers_still_fail_closed_when_the_read_is_unreadable(func_name):
    """The DRE-2034 discipline, re-asserted after this change: with the stub
    present and the Actions read 403ing, no caller may dispatch."""
    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "/contents/" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"name": n, "type": "file"} for n in PRESENT]),
                stderr="",
            )
        if argv[1:3] == ["run", "list"]:
            return SimpleNamespace(returncode=1, stdout="", stderr=FORBIDDEN)
        if argv[1:3] == ["pr", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"number": 48, '
                    '"headRefName": "agent/DRE-2525-absent-workflow", '
                    '"headRefOid": "' + "d" * 40 + '", '
                    '"mergeStateStatus": "DIRTY", "comments": []}]'
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    with patch.dict(os.environ, {"GH_DISPATCH_TOKEN": "ghs_dispatch"}), patch.object(
        reconcile.subprocess, "run", side_effect=fake_run
    ), patch.object(reconcile, "card_parked_for_human", return_value=False):
        try:
            getattr(reconcile, func_name)()
        except Exception:
            pass  # unrelated downstream shape; the dispatch assertion is the point
    assert _run_listings(calls), (
        f"{func_name} never reached its busy read under this stub, so the "
        "assertion below would pass vacuously — fix the stub shape"
    )
    dispatched = [c for c in calls if c[1:3] == ["workflow", "run"]]
    assert not dispatched, (
        f"{func_name} dispatched while its busy read was unreadable. An "
        "unreadable listing must still fail CLOSED."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
