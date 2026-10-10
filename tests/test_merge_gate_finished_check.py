"""The merge gate reads a check as finished when GitHub has given it a passing
result and a finish time and its run has finished (DRE-6570).

On 2026-10-09 an approved pull request with every check passed sat unmerged
for two and a half hours: dreadnought-foundry/bureau-pipeline#894, head
92cca12e. Check run 114111594633 (`act registry consumers`, in run
38017709857, Pipeline Tests) finished at 19:38:45 PT and GitHub recorded
`conclusion: success` and `completed_at` for it — but left its `status` at
`in_progress`, and it never changed. The run itself read `completed` /
`success` from 19:53:49. Condition 1 read only the status word, so it
answered `1 of 10 check runs not green — wait` at 19:54 and again when the
sweep woke it at 21:54, and nothing told anybody. An operator re-ran the job
by hand at 22:22 and the pull request merged 28 seconds later.

The rule: a check run counts as finished and green, whatever its `status`
says, when its conclusion is in GREEN_CONCLUSIONS, its `completed_at` is set,
and the workflow run it belongs to reads `completed` in the workflow-runs
record the gate already holds. Any piece missing — or a record that could not
be read — waits exactly as before. When the rule is used, the run log says so
in one `note=` line naming the check.

The records below are GitHub's own, read back on 2026-10-10 with
`gh api …/check-runs/114111594633`, `…/actions/runs/38017709857/attempts/1`
and its job list, as they stood at 21:54 PT.

Run: python3 -m pytest tests/test_merge_gate_finished_check.py -v
"""

from __future__ import annotations

import copy
import json
import subprocess  # nosec B404 — fixed argv, our own script
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_gate.py"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import merge_gate  # noqa: E402
from test_merge_gate_one_read import run_gate, view  # noqa: E402

HEAD = "92cca12e815db0bdcde47d0057834453fe7305e4"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
APPROVE = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
}]

TESTS_SUITE = 103011847703     # Pipeline Tests, run 38017709857
REVIEW_SUITE = 103011848493    # QA Review (pr-review.yml), excluded
GATE_SUITE = 103012192615      # Merge Gate, skipped on the review event
CRITIC_SUITE = 103011841819    # the critic's own check, no workflow run
TESTS_RUN = 38017709857
FROZEN_ID = 114111594633
FROZEN_NAME = "act registry consumers"

NOTE = (f'check "{FROZEN_NAME}" read as finished: conclusion success, '
        "completed 19:38 PT, its run completed — GitHub still lists it in "
        "progress")
OLD_WAIT = "1 of 10 check runs not green"


def _job(check_id, name, started, completed, status="completed",
         conclusion="success"):
    """One Pipeline Tests job as `GET commits/{sha}/check-runs` lists it."""
    return {
        "id": check_id, "name": name, "head_sha": HEAD,
        "status": status, "conclusion": conclusion,
        "started_at": started, "completed_at": completed,
        "check_suite": {"id": TESTS_SUITE},
        "app": {"slug": "github-actions"},
        "details_url": ("https://github.com/dreadnought-foundry/bureau-pipeline"
                        f"/actions/runs/{TESTS_RUN}/job/{check_id}"),
    }


def frozen(**overrides) -> dict:
    """Check run 114111594633 exactly as GitHub still returns it."""
    run = _job(FROZEN_ID, FROZEN_NAME, "2026-10-10T02:38:39Z",
               "2026-10-10T02:38:45Z", status="in_progress")
    run.update(overrides)
    return run


def check_runs_2154() -> list:
    """The eleven check runs on 92cca12e at 21:54 PT, newest first: the ten
    the gate counts, plus the QA Review job its verified origin excludes."""
    return [
        _job(114114383039, "scripts unit tests",
             "2026-10-10T02:53:25Z", "2026-10-10T02:53:48Z"),
        {"id": 114111979988, "name": "call / evaluate", "head_sha": HEAD,
         "status": "completed", "conclusion": "skipped",
         "started_at": "2026-10-10T02:40:43Z",
         "completed_at": "2026-10-10T02:40:43Z",
         "check_suite": {"id": GATE_SUITE}},
        {"id": 114111979559, "name": "call / resolve", "head_sha": HEAD,
         "status": "completed", "conclusion": "skipped",
         "started_at": "2026-10-10T02:40:43Z",
         "completed_at": "2026-10-10T02:40:43Z",
         "check_suite": {"id": GATE_SUITE}},
        {"id": 114111971248, "name": "QA critic review", "head_sha": HEAD,
         "status": "completed", "conclusion": "success",
         "started_at": "2026-10-10T02:40:40Z",
         "completed_at": "2026-10-10T02:40:40Z",
         "check_suite": {"id": CRITIC_SUITE},
         "app": {"slug": "agent-bureau-bot"}},
        {"id": 114111596110, "name": "call / review", "head_sha": HEAD,
         "status": "completed", "conclusion": "success",
         "started_at": "2026-10-10T02:38:41Z",
         "completed_at": "2026-10-10T02:40:47Z",
         "check_suite": {"id": REVIEW_SUITE}},
        _job(114111594649, "scripts unit tests (part 3)",
             "2026-10-10T02:38:40Z", "2026-10-10T02:47:11Z"),
        frozen(),
        _job(114111594631, "scripts unit tests (part 1)",
             "2026-10-10T02:38:39Z", "2026-10-10T02:53:23Z"),
        _job(114111594622, "scripts unit tests (part 4)",
             "2026-10-10T02:38:39Z", "2026-10-10T02:46:37Z"),
        _job(114111594596, "TDD commit discipline",
             "2026-10-10T02:38:40Z", "2026-10-10T02:38:46Z"),
        _job(114111594595, "scripts unit tests (part 2)",
             "2026-10-10T02:38:40Z", "2026-10-10T02:49:47Z"),
    ]


def workflow_runs_2154(tests_status="completed") -> list:
    """`GET actions/runs?head_sha=92cca12e` at 21:54 PT: Pipeline Tests on
    attempt 1, finished at 19:53:49 PT."""
    return [
        {"id": 38017836823, "name": "Merge Gate",
         "path": ".github/workflows/self-merge-gate.yml",
         "event": "pull_request_review", "status": "completed",
         "conclusion": "skipped", "check_suite_id": GATE_SUITE,
         "run_attempt": 1},
        {"id": 38017710191, "name": "QA Review",
         "path": ".github/workflows/pr-review.yml", "event": "pull_request",
         "status": "completed", "conclusion": "success",
         "check_suite_id": REVIEW_SUITE, "run_attempt": 1},
        {"id": TESTS_RUN, "name": "Pipeline Tests",
         "path": ".github/workflows/tests.yml", "event": "pull_request",
         "status": tests_status,
         "conclusion": "success" if tests_status == "completed" else None,
         "check_suite_id": TESTS_SUITE, "run_attempt": 1,
         "updated_at": "2026-10-10T02:53:49Z"},
    ]


def with_frozen(runs: list, replacement: dict) -> list:
    return [replacement if r.get("id") == FROZEN_ID else r for r in runs]


def condition_1(check_runs, workflow_runs):
    """The full condition 1, over the records as main() derives them."""
    if workflow_runs is None:
        suites, unfinished = frozenset(), None
    else:
        suites = merge_gate.review_suite_ids(
            workflow_runs, merge_gate.DEFAULT_REVIEW_WORKFLOWS)
        unfinished = merge_gate.unfinished_runs(workflow_runs)
    return merge_gate.evaluate_checks(check_runs, suites, unfinished,
                                      workflow_runs=workflow_runs)


def full_decision(check_runs, workflow_runs, comments=APPROVE):
    if workflow_runs is None:
        suites, unfinished = frozenset(), None
    else:
        suites = merge_gate.review_suite_ids(
            workflow_runs, merge_gate.DEFAULT_REVIEW_WORKFLOWS)
        unfinished = merge_gate.unfinished_runs(workflow_runs)
    return merge_gate.decide(
        HEAD, QA_LOGIN, check_runs, comments, suites,
        compare_status="ahead", unfinished_runs=unfinished,
        workflow_runs=workflow_runs,
    )


# --------------------------------------------------------------------------
# 1. The replay: 92cca12e at 21:54 PT
# --------------------------------------------------------------------------
def test_the_fixture_is_tonights_record():
    runs = check_runs_2154()
    suites = merge_gate.review_suite_ids(
        workflow_runs_2154(), merge_gate.DEFAULT_REVIEW_WORKFLOWS)
    counted = [r for r in runs if r["check_suite"]["id"] not in suites]
    assert len(counted) == 10
    others = [r for r in counted if r["id"] != FROZEN_ID]
    assert all(r["status"] == "completed" for r in others)
    assert {r["conclusion"] for r in others} == {"success", "skipped"}
    assert frozen()["status"] == "in_progress"


def test_without_the_runs_record_tonights_answer_stands():
    """Non-vacuous: the same check runs with no workflow-runs record are
    today's wait, word for word."""
    decision = merge_gate.evaluate_checks(check_runs_2154(), frozenset(
        {REVIEW_SUITE}))
    assert decision is not None and decision.action == "wait"
    assert f"{OLD_WAIT} — wait" == decision.reason


def test_condition_1_reads_the_frozen_check_as_green():
    assert condition_1(check_runs_2154(), workflow_runs_2154()) is None


def test_the_precheck_gives_the_same_answer():
    assert merge_gate.precheck(check_runs_2154(), workflow_runs_2154()) is None


def test_the_gate_goes_on_to_its_other_conditions():
    """Approved at the head: the decision is the merge. Without the approval
    it is the critic's wait — condition 1 no longer answers either way."""
    decision = full_decision(check_runs_2154(), workflow_runs_2154())
    assert decision.action == "merge", decision.reason
    no_verdict = full_decision(check_runs_2154(), workflow_runs_2154(),
                               comments=[])
    assert no_verdict.action == "wait"
    assert OLD_WAIT not in no_verdict.reason
    assert "no critic verdict" in no_verdict.reason


# --------------------------------------------------------------------------
# 2. The run log says so
# --------------------------------------------------------------------------
def test_the_merge_carries_one_line_naming_the_check():
    decision = full_decision(check_runs_2154(), workflow_runs_2154())
    assert decision.notes.count(NOTE) == 1, decision.notes
    assert [n for n in decision.notes if FROZEN_NAME in n] == [NOTE]


def test_a_later_wait_still_carries_the_line():
    """The rule was used even when another condition waits, so the line
    rides on that decision too."""
    decision = full_decision(check_runs_2154(), workflow_runs_2154(),
                             comments=[])
    assert NOTE in decision.notes


def test_a_check_github_lists_completed_carries_no_line():
    runs = with_frozen(check_runs_2154(), frozen(status="completed"))
    decision = full_decision(runs, workflow_runs_2154())
    assert decision.action == "merge"
    assert not [n for n in decision.notes if "read as finished" in n]


def test_the_note_names_a_queued_status_as_github_gives_it():
    note = merge_gate.finished_check_note(frozen(status="queued"))
    assert note.endswith("— GitHub still lists it queued"), note


def _cli(tmp_path, check_runs, workflow_runs_payload) -> subprocess.CompletedProcess:
    cr = tmp_path / "check-runs.json"
    wr = tmp_path / "workflow-runs.json"
    cm = tmp_path / "comments.json"
    cp = tmp_path / "compare.json"
    cr.write_text(json.dumps([{"total_count": len(check_runs),
                               "check_runs": check_runs}]))
    wr.write_text(workflow_runs_payload)
    cm.write_text(json.dumps([APPROVE]))
    cp.write_text(json.dumps({"status": "ahead"}))
    return subprocess.run(  # nosec B603 B607 — fixed argv, our own script
        [sys.executable, str(SCRIPT),
         "--head-sha", HEAD, "--qa-login", QA_LOGIN,
         "--check-runs-file", str(cr), "--comments-file", str(cm),
         "--workflow-runs-file", str(wr), "--compare-file", str(cp)],
        capture_output=True, text=True, check=False)


def test_the_decision_script_prints_the_line(tmp_path):
    proc = _cli(tmp_path, check_runs_2154(),
                json.dumps({"workflow_runs": workflow_runs_2154()}))
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert f"note={NOTE}" in lines, proc.stdout
    assert "decision=merge" in lines, proc.stdout


def test_the_unreadable_runs_record_waits_through_the_script(tmp_path):
    proc = _cli(tmp_path, check_runs_2154(),
                merge_gate.UNREADABLE_WORKFLOW_RUNS)
    assert proc.returncode == 0, proc.stderr
    assert "decision=wait" in proc.stdout.splitlines(), proc.stdout
    # No origin record, so the review job counts too: eleven, as before.
    assert "reason=1 of 11 check runs not green — wait" in proc.stdout.splitlines()
    assert "read as finished" not in proc.stdout


def test_the_gate_script_merges_tonights_head_and_logs_the_line():
    """End to end through evaluate_and_merge.sh: the precheck no longer
    stops the gate, the full decision merges, and the log carries the line."""
    gate = run_gate({
        "view": view(headRefOid=HEAD, headRefName="agent/finished-check"),
        "check_runs": check_runs_2154(),
        "workflow_runs": workflow_runs_2154(),
    }, seed=(APPROVE[0]["body"],))
    assert gate.proc.returncode == 0, gate.explain()
    assert gate.decision() == "merge", gate.explain()
    assert len(gate.merges) == 1, gate.explain()
    assert f"note={NOTE}" in gate.proc.stdout.splitlines(), gate.explain()
    assert OLD_WAIT not in gate.proc.stdout


# --------------------------------------------------------------------------
# 3. Fail-closed: anything missing waits exactly as before
# --------------------------------------------------------------------------
FAIL_CLOSED = {
    "no conclusion": (frozen(conclusion=None), workflow_runs_2154()),
    "empty conclusion": (frozen(conclusion=""), workflow_runs_2154()),
    "no completed_at": (frozen(completed_at=None), workflow_runs_2154()),
    "unparseable completed_at": (frozen(completed_at="soon"),
                                 workflow_runs_2154()),
    "run still in progress": (frozen(),
                              workflow_runs_2154(tests_status="in_progress")),
    "run queued for a re-run": (frozen(),
                                workflow_runs_2154(tests_status="queued")),
    "run absent from the record": (frozen(), [
        r for r in workflow_runs_2154() if r["id"] != TESTS_RUN]),
    "runs record unreadable": (frozen(), None),
    "a failure": (frozen(conclusion="failure"), workflow_runs_2154()),
    "cancelled": (frozen(conclusion="cancelled"), workflow_runs_2154()),
}


@pytest.mark.parametrize("case", list(FAIL_CLOSED), ids=list(FAIL_CLOSED))
def test_fail_closed_still_waits(case):
    check, workflow_runs = FAIL_CLOSED[case]
    runs = with_frozen(check_runs_2154(), copy.deepcopy(check))
    decision = condition_1(runs, workflow_runs)
    assert decision is not None and decision.action == "wait", case
    early = merge_gate.precheck(runs, workflow_runs)
    assert early is not None and early.action == "wait", case
    assert early.reason == decision.reason
    full = full_decision(runs, workflow_runs)
    assert full.action == "wait", case
    assert not [n for n in full.notes if "read as finished" in n], case


@pytest.mark.parametrize("case", [
    "no conclusion", "no completed_at", "run still in progress",
    "a failure",
])
def test_each_fail_closed_case_is_tonights_answer(case):
    """The check-run count itself still names the frozen check as not green
    — the rule never fired, rather than another condition catching it."""
    check, workflow_runs = FAIL_CLOSED[case]
    runs = with_frozen(check_runs_2154(), copy.deepcopy(check))
    decision = condition_1(runs, workflow_runs)
    assert decision.reason == f"{OLD_WAIT} — wait", case


def test_an_in_progress_failure_is_not_green():
    runs = with_frozen(check_runs_2154(), frozen(conclusion="failure"))
    decision = condition_1(runs, workflow_runs_2154())
    assert decision is not None and decision.action == "wait"
    assert OLD_WAIT in decision.reason


# --------------------------------------------------------------------------
# 4. The join: by check suite, else by the run id in details_url
# --------------------------------------------------------------------------
def test_the_join_by_details_url_when_the_suite_is_absent():
    runs = with_frozen(check_runs_2154(), frozen(check_suite=None))
    assert condition_1(runs, workflow_runs_2154()) is None
    assert merge_gate.precheck(runs, workflow_runs_2154()) is None


def test_the_details_url_names_a_run_that_is_not_finished():
    runs = with_frozen(check_runs_2154(), frozen(check_suite=None))
    decision = condition_1(runs, workflow_runs_2154(tests_status="in_progress"))
    assert decision is not None and decision.action == "wait"


def test_no_suite_and_no_details_url_waits():
    runs = with_frozen(check_runs_2154(),
                       frozen(check_suite=None, details_url=None))
    decision = condition_1(runs, workflow_runs_2154())
    assert decision is not None and decision.reason == f"{OLD_WAIT} — wait"


def test_a_details_url_naming_another_run_waits():
    other = frozen(check_suite=None, details_url=(
        "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/"
        f"{TESTS_RUN + 1}/job/{FROZEN_ID}"))
    runs = with_frozen(check_runs_2154(), other)
    decision = condition_1(runs, workflow_runs_2154())
    assert decision is not None and decision.reason == f"{OLD_WAIT} — wait"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
