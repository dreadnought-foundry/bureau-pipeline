"""A canceled check on an approved pull request is re-run once, never sent a
fix agent (DRE-3072).

The CEO's decision, 2026-09-05 11:30 PT: a cancelled CI check is UNKNOWN, not
red. The approved-but-red sweep re-runs a cancelled check once; only a check
that then FAILS is red and gets a fix agent.

The case it is replayed against: agent-bureau #3502, head a3fa0c44, on
2026-10-09. `Console backend shard 1` was cancelled at its 15-minute limit,
which made `Console backend (pytest)`, the job that sums the shards, read
`failure`. Both are in CI run 38023358661, attempt 1. The sweep counted both
as red and dispatched a fix agent at a pull request with nothing wrong in it.
Leaving `cancelled` out of the red set alone would not have stopped it — the
summary job's `failure` dispatches by itself — so the rule is per workflow
run: a run holding a `cancelled` check is unknown as a whole.

Two halves:

  1. `canceled_check`, the pure decision — which checks are red, which runs
     are unknown, and what to do about each unknown run.
  2. `reconcile.fix_approved_but_red`, the wiring — replayed end to end with
     `gh` faked, counting the re-runs, the fix dispatches and the receipts.

Run: python3 -m pytest tests/test_canceled_check.py -v
"""

from __future__ import annotations

import ast
import copy
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import canceled_check  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "canceled-check-a3fa0c44.json"
RUN_ID = 38023358661
SHARD = "Console backend shard 1"
SHARD_JOB = 114129034599
PYTEST = "Console backend (pytest)"


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _with(fx: dict, **conclusions) -> list:
    """The fixture's check runs with the named checks' conclusions replaced."""
    runs = copy.deepcopy(fx["check_runs"])
    for run in runs:
        if run["name"] in conclusions:
            run["conclusion"] = conclusions[run["name"]]
    return runs


def _attempt(fx: dict, attempt: int, **extra) -> dict:
    return {**fx["run"], "run_attempt": attempt, **extra}


# --------------------------------------------------------------------------
# 1. The pure decision
# --------------------------------------------------------------------------


def test_the_replay_splits_into_one_unknown_run_and_nothing_red():
    fx = fixture()
    split = canceled_check.split(fx["check_runs"])
    assert split.red == ()
    assert list(split.unknown) == [RUN_ID]
    assert sorted(c["name"] for c in split.unknown[RUN_ID]) == [PYTEST, SHARD]


def test_a_failure_in_a_run_with_no_cancelled_check_is_red_as_today():
    fx = fixture()
    split = canceled_check.split(_with(fx, **{SHARD: "success"}))
    assert [c["name"] for c in split.red] == [PYTEST]
    assert split.unknown == {}


def test_timed_out_is_red_and_does_not_make_its_run_unknown():
    fx = fixture()
    split = canceled_check.split(_with(fx, **{SHARD: "timed_out"}))
    assert sorted(c["name"] for c in split.red) == [PYTEST, SHARD]
    assert split.unknown == {}


def test_a_cancelled_check_unknowns_only_its_own_run():
    """A failure in ANOTHER workflow run stays red: rule 5 is per run."""
    fx = fixture()
    runs = _with(fx, **{"TDD commit discipline": "failure"})
    split = canceled_check.split(runs)
    assert [c["name"] for c in split.red] == ["TDD commit discipline"]
    assert list(split.unknown) == [RUN_ID]


def test_a_review_check_is_left_out_as_today():
    fx = fixture()
    runs = _with(fx, **{"qa-review / review": "cancelled", SHARD: "success",
                        PYTEST: "success"})
    split = canceled_check.split(runs)
    assert split.red == () and split.unknown == {}


def test_the_run_is_read_off_the_details_url():
    assert canceled_check.run_of({
        "details_url": "https://github.com/o/r/actions/runs/38023358661/job/114129034599"
    }) == RUN_ID
    assert canceled_check.run_of({"details_url": "https://example.com/x"}) is None
    assert canceled_check.run_of({}) is None


def test_a_failure_with_no_run_to_read_is_red_as_today():
    """A check run with no Actions run behind it (another app's check)
    counted as red before this card, and still does."""
    split = canceled_check.split([
        {"name": "external", "status": "completed", "conclusion": "failure"}])
    assert [c["name"] for c in split.red] == ["external"]


def test_a_cancelled_check_with_no_run_is_never_red():
    split = canceled_check.split([
        {"name": "external", "status": "completed", "conclusion": "cancelled"}])
    assert split.red == ()
    assert list(split.unknown) == [None]


@pytest.mark.parametrize("message, limit", [
    ("The job has exceeded the maximum execution time of 15m0s", "15-minute"),
    ("The job has exceeded the maximum execution time of 1h30m0s", "90-minute"),
    ("The job running on runner GitHub Actions 2 has exceeded the maximum "
     "execution time of 360 minutes.", "360-minute"),
    ("The operation was canceled.", None),
    ("Process completed with exit code 1.", None),
])
def test_the_limit_is_read_off_the_annotation(message, limit):
    assert canceled_check.limit([{"message": message}]) == limit


def test_no_annotations_name_no_limit():
    assert canceled_check.limit([]) is None
    assert canceled_check.limit(None) is None


def test_attempt_one_with_no_receipt_is_re_run():
    fx = fixture()
    step = canceled_check.decide(RUN_ID, _attempt(fx, 1), fx["head_sha"], [])
    assert step.action == "rerun"


def test_attempt_one_with_a_receipt_for_this_head_and_run_waits():
    fx = fixture()
    said = [canceled_check.rerun_receipt(3502, fx["head_sha"], RUN_ID, [SHARD], "15-minute")]
    step = canceled_check.decide(RUN_ID, _attempt(fx, 1), fx["head_sha"], said)
    assert step.action == "wait"


def test_a_receipt_for_another_head_or_run_does_not_count():
    fx = fixture()
    other_head = [canceled_check.rerun_receipt(3502, "b" * 40, RUN_ID, [SHARD], None)]
    other_run = [canceled_check.rerun_receipt(3502, fx["head_sha"], 380233586610, [SHARD], None)]
    for said in (other_head, other_run):
        step = canceled_check.decide(RUN_ID, _attempt(fx, 1), fx["head_sha"], said)
        assert step.action == "rerun", said


def test_a_later_attempt_is_told_once_and_never_re_run():
    fx = fixture()
    step = canceled_check.decide(RUN_ID, _attempt(fx, 2), fx["head_sha"], [])
    assert step.action == "tell"
    said = [canceled_check.again_receipt(3502, fx["head_sha"], _attempt(fx, 2), [SHARD])]
    step = canceled_check.decide(RUN_ID, _attempt(fx, 2), fx["head_sha"], said)
    assert step.action == "done"


def test_a_run_still_going_is_waited_on():
    """GitHub refuses to re-run a run that has not finished."""
    fx = fixture()
    step = canceled_check.decide(
        RUN_ID, _attempt(fx, 1, status="in_progress"), fx["head_sha"], [])
    assert step.action == "wait"


def test_an_unread_run_is_left_alone():
    fx = fixture()
    assert canceled_check.decide(RUN_ID, None, fx["head_sha"], []).action == "unread"
    assert canceled_check.decide(None, None, fx["head_sha"], []).action == "unread"


def test_the_re_run_receipt_names_the_limit_from_the_annotation():
    fx = fixture()
    body = canceled_check.rerun_receipt(3502, fx["head_sha"], RUN_ID, [SHARD], "15-minute")
    assert "canceled at its 15-minute limit, re-running" in body
    assert fx["head_sha"] in body and str(RUN_ID) in body and SHARD in body


def test_the_re_run_receipt_without_a_limit():
    fx = fixture()
    body = canceled_check.rerun_receipt(3502, fx["head_sha"], RUN_ID, [SHARD], None)
    assert "canceled, re-running" in body
    assert "limit" not in body.split("\n")[0]


def test_the_told_once_receipt_names_the_job():
    fx = fixture()
    body = canceled_check.again_receipt(3502, fx["head_sha"], _attempt(fx, 2), [SHARD])
    assert SHARD in body and fx["head_sha"] in body and str(RUN_ID) in body


def test_the_decision_makes_no_network_call():
    """Pure: the module imports nothing that can reach a network or a process."""
    tree = ast.parse((ROOT / "scripts" / "canceled_check.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"subprocess", "urllib", "http", "socket", "requests",
                           "reconcile", "gh_read_retry", "linear_ops"}, imported


def test_both_receipts_are_registry_rows():
    names = pipeline_act.acts()
    assert canceled_check.RERUN_ACT in names and canceled_check.AGAIN_ACT in names
    assert pipeline_act.tag(canceled_check.RERUN_ACT) == canceled_check.RERUN_TAG
    assert pipeline_act.tag(canceled_check.AGAIN_ACT) == canceled_check.AGAIN_TAG
    assert pipeline_act.kind(canceled_check.RERUN_ACT) == "recovery"
    assert pipeline_act.kind(canceled_check.AGAIN_ACT) == "hold"
    assert pipeline_act.problems() == []


# --------------------------------------------------------------------------
# 2. The sweep, replayed
# --------------------------------------------------------------------------

WORKER = "agent-bureau-bot"


class FakeGh:
    """`gh` as `fix_approved_but_red` calls it, answering off the fixture."""

    def __init__(self, check_runs, run, annotations, prs):
        self.check_runs = check_runs
        self.run = run
        self.annotations = annotations
        self.prs = prs
        self.calls: list = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(self.prs)
        joined = " ".join(args)
        if "/annotations" in joined:
            for job, notes in self.annotations.items():
                if f"check-runs/{job}/annotations" in joined:
                    return json.dumps(notes)
            return "[]"
        if "/check-runs" in joined:
            return json.dumps([{"total_count": len(self.check_runs),
                                "check_runs": self.check_runs}])
        if f"actions/runs/{RUN_ID}" in joined:
            return json.dumps(self.run) if self.run is not None else ""
        if "/git/commits/" in joined:
            return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
        return ""


def approved_pr(fx: dict, notes=()) -> dict:
    sha = fx["head_sha"]
    comments = [{"author": {"login": "agent-bureau-qa-bot"},
                 "body": f"QA Critic\nVERDICT: APPROVE @{sha}"}]
    comments += [{"author": {"login": WORKER}, "body": body} for body in notes]
    return {"number": fx["pr"], "headRefName": "agent/DRE-6556-open-set",
            "headRefOid": sha, "mergeStateStatus": "BLOCKED", "comments": comments}


class Sweep:
    """One pass of `fix_approved_but_red`, recording what it did."""

    def __init__(self, check_runs, run, annotations=None, notes=(), refuse=False):
        fx = fixture()
        self.fake = FakeGh(check_runs, run,
                           fx["annotations"] if annotations is None else annotations,
                           [approved_pr(fx, notes)])
        self.refuse = refuse
        self.reruns: list = []
        self.fixes: list = []
        self.posted: list = []

    def _dispatch(self, *args):
        if args[:2] == ("run", "rerun"):
            if self.refuse:
                raise reconcile.ReconcileWriteError(
                    f"gh {' '.join(args)} failed rc=1: HTTP 403")
            self.reruns.append(args)
        else:
            self.fixes.append(args)

    def run(self) -> Sweep:
        with mock.patch.dict(os.environ, {"GH_DISPATCH_TOKEN": ""}), \
                mock.patch.object(reconcile, "gh", side_effect=self.fake), \
                mock.patch.object(reconcile, "gh_dispatch", side_effect=self._dispatch), \
                mock.patch.object(reconcile, "_post_pr_note",
                                  side_effect=lambda n, b: self.posted.append((n, b)) or True), \
                mock.patch.object(reconcile, "_actions_runs_busy", return_value=False), \
                mock.patch.object(reconcile, "fix_dispatch_blocked", return_value=False), \
                mock.patch.object(reconcile, "fix_agent_absent_hold", return_value=False), \
                mock.patch.object(reconcile, "_write_failures", []) as failures:
            reconcile.fix_approved_but_red()
            self.write_failures = list(failures)
        return self


@pytest.fixture(autouse=True)
def _no_dispatch_token(monkeypatch):
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)


def test_replay_a3fa0c44_re_runs_that_run_once_and_dispatches_no_fix():
    fx = fixture()
    sweep = Sweep(fx["check_runs"], _attempt(fx, 1)).run()
    assert sweep.fixes == []
    assert sweep.reruns == [("run", "rerun", str(RUN_ID), "--failed",
                             "--repo", reconcile.REPO)]
    assert len(sweep.posted) == 1
    number, body = sweep.posted[0]
    assert number == fx["pr"]
    assert "canceled at its 15-minute limit, re-running" in body
    assert fx["head_sha"] in body and str(RUN_ID) in body
    trailer = pipeline_act.read_trailer(body)
    assert trailer and trailer["act"] == canceled_check.RERUN_ACT
    assert sweep.write_failures == []


def test_a_second_pass_on_attempt_one_starts_nothing_and_posts_nothing():
    fx = fixture()
    first = Sweep(fx["check_runs"], _attempt(fx, 1)).run()
    receipt = first.posted[0][1]
    second = Sweep(fx["check_runs"], _attempt(fx, 1), notes=[receipt]).run()
    assert second.reruns == [] and second.posted == [] and second.fixes == []


def test_attempt_two_cancelled_again_is_told_once_and_not_re_run():
    fx = fixture()
    sweep = Sweep(fx["check_runs"], _attempt(fx, 2)).run()
    assert sweep.reruns == [] and sweep.fixes == []
    assert len(sweep.posted) == 1
    body = sweep.posted[0][1]
    assert SHARD in body and fx["head_sha"] in body
    trailer = pipeline_act.read_trailer(body)
    assert trailer and trailer["act"] == canceled_check.AGAIN_ACT
    again = Sweep(fx["check_runs"], _attempt(fx, 2), notes=[body]).run()
    assert again.reruns == [] and again.fixes == [] and again.posted == []


def test_attempt_two_with_the_shard_green_and_the_summary_red_dispatches_the_fix():
    fx = fixture()
    sweep = Sweep(_with(fx, **{SHARD: "success"}), _attempt(fx, 2)).run()
    assert sweep.reruns == [] and sweep.posted == []
    assert len(sweep.fixes) == 1
    assert sweep.fixes[0][:2] == ("workflow", "run")
    assert f"pr_number={fx['pr']}" in sweep.fixes[0]


def test_portico_415_one_cancelled_check_is_re_run_and_not_dispatched():
    """portico #415's shape: the only red on the head is one cancelled check."""
    fx = fixture()
    runs = [{"id": 99, "name": "infra — typecheck & test", "status": "completed",
             "conclusion": "cancelled",
             "details_url": f"https://github.com/o/portico/actions/runs/{RUN_ID}/job/99"},
            {"id": 98, "name": "web", "status": "completed", "conclusion": "success",
             "details_url": f"https://github.com/o/portico/actions/runs/{RUN_ID}/job/98"}]
    notes = {"99": [{"message": "The job has exceeded the maximum execution time of 10m0s"}]}
    sweep = Sweep(runs, _attempt(fx, 1), annotations=notes).run()
    assert sweep.fixes == []
    assert len(sweep.reruns) == 1 and len(sweep.posted) == 1
    assert "canceled at its 10-minute limit, re-running" in sweep.posted[0][1]


def test_a_failure_in_a_run_with_no_cancelled_check_dispatches_as_today():
    fx = fixture()
    for conclusion in ("failure", "timed_out"):
        runs = _with(fx, **{SHARD: "success", PYTEST: conclusion})
        sweep = Sweep(runs, _attempt(fx, 1)).run()
        assert sweep.reruns == [] and sweep.posted == [], conclusion
        assert len(sweep.fixes) == 1, conclusion


def test_with_no_limit_in_the_annotation_the_receipt_reads_canceled_re_running():
    fx = fixture()
    sweep = Sweep(fx["check_runs"], _attempt(fx, 1), annotations={}).run()
    assert len(sweep.posted) == 1
    assert "canceled, re-running" in sweep.posted[0][1]
    assert "limit" not in sweep.posted[0][1].split("\n")[0]


def test_a_refused_re_run_is_a_write_failure_and_never_a_fix_dispatch():
    fx = fixture()
    sweep = Sweep(fx["check_runs"], _attempt(fx, 1), refuse=True).run()
    assert sweep.fixes == [] and sweep.posted == []
    assert len(sweep.write_failures) == 1
    assert str(RUN_ID) in sweep.write_failures[0]


def test_an_unreadable_run_dispatches_nothing():
    fx = fixture()
    sweep = Sweep(fx["check_runs"], None).run()
    assert sweep.fixes == [] and sweep.reruns == [] and sweep.posted == []


def test_an_unknown_run_does_not_hide_a_red_run_beside_it():
    """Rule 5 still holds for the other run on the same head."""
    fx = fixture()
    runs = _with(fx, **{"TDD commit discipline": "failure"})
    sweep = Sweep(runs, _attempt(fx, 1)).run()
    assert len(sweep.reruns) == 1
    assert len(sweep.fixes) == 1


# --------------------------------------------------------------------------
# 3. Fail-fast collateral (DRE-6780)
# --------------------------------------------------------------------------
#
# agent-bureau #3563, head 1d7a19445, CI run 38092938543 on attempt 2,
# 2026-10-10: `Console web (vitest) (1)` failed at 16:17:01 PT and the matrix
# canceled its sibling `Console web (vitest) (2)` 22 seconds later. Nothing was
# at a time limit. The sweep read the run as "canceled twice" and told a person,
# and two ordinary test failures sat red for 85 minutes with no fix agent. A
# canceled check that finished AFTER a failure in its own run, and whose
# annotations name no time limit, is collateral: dropped from the split, and
# the run is red on its failures.

FIXTURE_1D7A = ROOT / "tests" / "fixtures" / "canceled-check-1d7a1944.json"
RUN_1D7A = 38092938543
VITEST_1 = "Console web (vitest) (1)"
VITEST_2 = "Console web (vitest) (2)"
VITEST_2_JOB = 115210000103
SHARD_3 = "Console backend shard 3"
FAILED_1D7A = sorted([VITEST_1, SHARD_3, PYTEST])


def fixture_1d7a() -> dict:
    return json.loads(FIXTURE_1D7A.read_text(encoding="utf-8"))


def _at(runs: list, name: str, completed_at) -> list:
    """`runs` with the named check's `completed_at` replaced (None drops it)."""
    runs = copy.deepcopy(runs)
    for run in runs:
        if run["name"] == name:
            if completed_at is None:
                run.pop("completed_at", None)
            else:
                run["completed_at"] = completed_at
    return runs


def test_the_1d7a_fixture_is_the_cards_table():
    fx = fixture_1d7a()
    assert fx["run"]["id"] == RUN_1D7A and fx["run"]["run_attempt"] == 2
    assert fx["head_sha"] == "1d7a19445" + "0" * 31
    by_name = {c["name"]: c for c in fx["check_runs"]}
    assert [(n, by_name[n]["conclusion"], by_name[n]["completed_at"]) for n in (
        VITEST_1, VITEST_2, SHARD_3, PYTEST)] == [
        (VITEST_1, "failure", "2026-10-10T23:17:01Z"),
        (VITEST_2, "cancelled", "2026-10-10T23:17:23Z"),
        (SHARD_3, "failure", "2026-10-10T23:24:55Z"),
        (PYTEST, "failure", "2026-10-10T23:25:00Z"),
    ]
    assert canceled_check.limit(fx["annotations"][str(VITEST_2_JOB)]) is None


def test_the_collateral_candidate_is_the_canceled_sibling_of_an_earlier_failure():
    fx = fixture_1d7a()
    assert [c["name"] for c in canceled_check.collateral_candidates(
        fx["check_runs"])] == [VITEST_2]


def test_the_october_9_shard_is_no_candidate_its_summary_failed_after_it():
    """agent-bureau #3502: the shard was canceled FIRST, so nothing is read."""
    assert canceled_check.collateral_candidates(fixture()["check_runs"]) == ()


def test_a_collateral_cancellation_is_neither_red_nor_unknown():
    fx = fixture_1d7a()
    split = canceled_check.split(fx["check_runs"], {VITEST_2_JOB: None})
    assert split.unknown == {}
    assert sorted(c["name"] for c in split.red) == FAILED_1D7A
    assert VITEST_2 not in [c["name"] for c in split.red]


def test_with_no_limits_handed_in_the_run_is_unknown_as_today():
    """Fail closed: annotations that could not be read keep it UNKNOWN."""
    fx = fixture_1d7a()
    for limits in (None, {}):
        split = canceled_check.split(fx["check_runs"], limits)
        assert split.red == ()
        assert list(split.unknown) == [RUN_1D7A]
        assert sorted(c["name"] for c in split.unknown[RUN_1D7A]) == sorted(
            FAILED_1D7A + [VITEST_2])


def test_an_annotation_naming_a_time_limit_keeps_it_unknown():
    """A job stopped by its own limit after a sibling failed stays UNKNOWN."""
    fx = fixture_1d7a()
    split = canceled_check.split(fx["check_runs"], {VITEST_2_JOB: "15-minute"})
    assert split.red == () and list(split.unknown) == [RUN_1D7A]


@pytest.mark.parametrize("why, runs", [
    ("no failed check in its run finished before it",
     lambda fx: _at(fx["check_runs"], VITEST_1, "2026-10-10T23:17:30Z")),
    ("the failed check finished at the same instant",
     lambda fx: _at(fx["check_runs"], VITEST_1, "2026-10-10T23:17:23Z")),
    ("the same instant, written with an offset",
     lambda fx: _at(fx["check_runs"], VITEST_1, "2026-10-10T16:17:23-07:00")),
    ("the canceled check has no completed_at",
     lambda fx: _at(fx["check_runs"], VITEST_2, None)),
    ("the canceled check's completed_at is unreadable",
     lambda fx: _at(fx["check_runs"], VITEST_2, "not a time")),
    ("the earlier failure has no completed_at",
     lambda fx: _at(fx["check_runs"], VITEST_1, None)),
])
def test_the_canceled_check_stays_unknown_when(why, runs):
    fx = fixture_1d7a()
    runs = runs(fx)
    # Only the earliest failure is moved; the others finished after the shard.
    assert canceled_check.collateral_candidates(runs) == (), why
    split = canceled_check.split(runs, {VITEST_2_JOB: None})
    assert split.red == (), why
    assert list(split.unknown) == [RUN_1D7A], why


def test_instants_are_compared_after_parsing():
    """16:17:01 PT written with its offset is still earlier than 23:17:23Z."""
    fx = fixture_1d7a()
    runs = _at(fx["check_runs"], VITEST_1, "2026-10-10T16:17:01-07:00")
    split = canceled_check.split(runs, {VITEST_2_JOB: None})
    assert split.unknown == {}


def test_a_failure_in_another_run_makes_no_collateral():
    fx = fixture_1d7a()
    runs = _with(fx, **{VITEST_1: "success", SHARD_3: "success", PYTEST: "success",
                        "TDD commit discipline": "failure"})
    runs = _at(runs, "TDD commit discipline", "2026-10-10T23:10:20Z")
    assert canceled_check.collateral_candidates(runs) == ()
    split = canceled_check.split(runs, {VITEST_2_JOB: None})
    assert [c["name"] for c in split.red] == ["TDD commit discipline"]
    assert list(split.unknown) == [RUN_1D7A]


def test_a_timed_out_sibling_is_not_a_failure_for_this_rule():
    fx = fixture_1d7a()
    runs = _with(fx, **{VITEST_1: "timed_out", SHARD_3: "success", PYTEST: "success"})
    assert canceled_check.collateral_candidates(runs) == ()


def test_a_second_canceled_check_that_is_not_collateral_keeps_the_run_unknown():
    """Only the collateral check is dropped; another cancellation decides."""
    fx = fixture_1d7a()
    runs = _with(fx, **{"Console backend shard 1": "cancelled"})
    split = canceled_check.split(runs, {VITEST_2_JOB: None,
                                        115210000109: "15-minute"})
    assert split.red == ()
    names = sorted(c["name"] for c in split.unknown[RUN_1D7A])
    assert VITEST_2 not in names and "Console backend shard 1" in names


def test_the_october_9_case_stays_unknown_even_told_no_limit():
    """The order rule alone keeps it: the summary failed after the shard."""
    split = canceled_check.split(fixture()["check_runs"], {SHARD_JOB: None})
    assert split.red == () and list(split.unknown) == [RUN_ID]


class FakeGh1d7a(FakeGh):
    """`FakeGh` for run 38092938543, with the head commit's age and the
    annotation reads that could not be answered set by the test."""

    def __init__(self, *args, committed: str, unreadable=()):
        super().__init__(*args)
        self.committed = committed
        self.unreadable = {str(j) for j in unreadable}

    def __call__(self, *args):
        joined = " ".join(args)
        if "/git/commits/" in joined:
            self.calls.append(args)
            return json.dumps({"committer": {"date": self.committed}})
        if f"actions/runs/{RUN_1D7A}" in joined:
            self.calls.append(args)
            return json.dumps(self.run) if self.run is not None else ""
        if any(f"check-runs/{j}/annotations" in joined for j in self.unreadable):
            self.calls.append(args)
            return ""
        return super().__call__(*args)

    def annotation_reads(self) -> list:
        return [" ".join(a) for a in self.calls if "/annotations" in " ".join(a)]


def _ago(minutes: int) -> str:
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Sweep1d7a(Sweep):
    """One pass of `fix_approved_but_red` over agent-bureau #3563."""

    def __init__(self, check_runs=None, run=None, committed=None, notes=(),
                 unreadable=()):
        fx = fixture_1d7a()
        self.fake = FakeGh1d7a(
            fx["check_runs"] if check_runs is None else check_runs,
            fx["run"] if run is None else run,
            fx["annotations"], [approved_pr(fx, notes)],
            committed=committed or _ago(45), unreadable=unreadable)
        self.refuse = False
        self.reruns: list = []
        self.fixes: list = []
        self.posted: list = []


def _no_canceled_check_receipt(posted) -> bool:
    acts = [(pipeline_act.read_trailer(body) or {}).get("act") for _, body in posted]
    return not ({canceled_check.RERUN_ACT, canceled_check.AGAIN_ACT} & set(acts))


def test_replay_1d7a_dispatches_one_fix_and_no_re_run_or_receipt():
    fx = fixture_1d7a()
    sweep = Sweep1d7a(committed=_ago(45)).run()
    assert sweep.reruns == []
    assert len(sweep.fixes) == 1
    assert sweep.fixes[0][:2] == ("workflow", "run")
    assert f"pr_number={fx['pr']}" in sweep.fixes[0]
    assert sweep.posted == [] and _no_canceled_check_receipt(sweep.posted)
    assert sweep.write_failures == []


def test_replay_1d7a_younger_than_20_minutes_is_held_by_the_age_gate_only():
    sweep = Sweep1d7a(committed=_ago(5)).run()
    assert sweep.fixes == [] and sweep.reruns == []
    assert sweep.posted == [] and _no_canceled_check_receipt(sweep.posted)
    # The run was read as red, not unknown: its Actions record was never read.
    assert not any(f"actions/runs/{RUN_1D7A}" in " ".join(a) for a in sweep.fake.calls)


def test_a_collateral_run_on_attempt_one_is_not_re_run():
    fx = fixture_1d7a()
    sweep = Sweep1d7a(run={**fx["run"], "run_attempt": 1}).run()
    assert sweep.reruns == []
    assert _no_canceled_check_receipt(sweep.posted) and sweep.posted == []
    assert len(sweep.fixes) == 1


def test_the_limit_is_read_only_for_the_collateral_candidate():
    sweep = Sweep1d7a().run()
    reads = sweep.fake.annotation_reads()
    assert len(reads) == 1 and f"check-runs/{VITEST_2_JOB}/annotations" in reads[0]


def test_the_october_9_replay_reads_no_annotation_on_attempt_two():
    """A pass with no earlier-finished failure beside a cancellation costs
    nothing new."""
    fx = fixture()
    sweep = Sweep(fx["check_runs"], _attempt(fx, 2)).run()
    assert not [a for a in sweep.fake.calls if "/annotations" in " ".join(a)]


def test_an_unreadable_annotation_keeps_the_run_unknown_and_told_once():
    sweep = Sweep1d7a(unreadable=[VITEST_2_JOB]).run()
    assert sweep.fixes == [] and sweep.reruns == []
    assert len(sweep.posted) == 1
    trailer = pipeline_act.read_trailer(sweep.posted[0][1])
    assert trailer and trailer["act"] == canceled_check.AGAIN_ACT


def test_a_time_limit_after_a_sibling_failed_keeps_the_run_unknown():
    fx = fixture_1d7a()
    sweep = Sweep1d7a()
    sweep.fake.annotations = {**fx["annotations"], str(VITEST_2_JOB): [
        {"message": "The job has exceeded the maximum execution time of 15m0s"}]}
    sweep.run()
    assert sweep.fixes == [] and sweep.reruns == []
    assert len(sweep.posted) == 1
    trailer = pipeline_act.read_trailer(sweep.posted[0][1])
    assert trailer and trailer["act"] == canceled_check.AGAIN_ACT


def test_attempt_two_with_only_a_canceled_check_is_still_told_nothing_is_coming():
    """No failed check in the run: today's `canceled-check-again`, wording and
    all, and no fix agent."""
    fx = fixture_1d7a()
    runs = _with(fx, **{VITEST_1: "success", SHARD_3: "success", PYTEST: "success"})
    sweep = Sweep1d7a(check_runs=runs).run()
    assert sweep.fixes == [] and sweep.reruns == []
    assert len(sweep.posted) == 1
    body = sweep.posted[0][1]
    trailer = pipeline_act.read_trailer(body)
    assert trailer and trailer["act"] == canceled_check.AGAIN_ACT
    assert VITEST_2 in body and fx["head_sha"] in body
    assert "nothing automatic is coming" in body
