"""RED-first tests for DRE-5901 — a CI run on the default branch whose jobs
GitHub cancelled with "not acquired by Runner" is re-run once by itself.

THE INCIDENT (2026-10-05, PT): agent-bureau's CI on main at 559c8b9 ended
`cancelled`. Every job carried "The job was not acquired by Runner of type
hosted even after multiple attempts" — nothing failed and no test ran. The
release train only ships a commit whose CI concluded success, so it left
without the commit four times, and the console stayed on the previous release
for more than two hours until the operator re-ran the run by hand (run
37362862791). The re-run lost one job the same way and needed a second hand
re-run.

WHY NOTHING CAUGHT IT: the medic wakes on `failure` and `timed_out` only
(scripts/medic_wake.py), and that door is in every product repo's stub, so a
cancelled run never reaches it. The reconcile sweep runs in every repo that
consumes the pipeline, every fifteen minutes, from the shared reusable
workflow — that is the seam this card uses.

UNDER TEST — `scripts/runner_lost.py` (the decision, over a fake GitHub
client) and `reconcile.rerun_runner_lost_ci` (the sweep's wiring):

  * a completed default-branch CI run whose every non-success job carries the
    annotation has its failed and cancelled jobs re-run (`gh run rerun <id>
    --failed`), once per run attempt, with one receipt line naming the repo,
    the run id, the jobs and the reason;
  * a run cancelled by a newer push, by a person or by a job timeout is left
    alone;
  * a second "not acquired" cancellation on the re-run attempt is not re-run
    again, and is reported once;
  * nothing names a repo: the rule is the sweep's, and the sweep is every
    repo's.

Run: cd bureau-pipeline && python3 -m pytest tests/test_runner_lost_rerun.py -v
"""
from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402
import runner_lost  # noqa: E402

REPO = "dreadnought-foundry/agent-bureau"
SHA = "559c8b9" + "0" * 33

#: GitHub's own wording, copied from run 37362862791.
NOT_ACQUIRED = ("The job was not acquired by Runner of type hosted even after "
                "multiple attempts")
#: What GitHub writes on a job a newer push superseded through a concurrency
#: group, on a job a person cancelled, and on a job that ran out of time.
SUPERSEDED = ("Canceling since a higher priority waiting request for "
              "'CI-refs/heads/main' exists")
BY_A_PERSON = "The run was canceled by @operator."
TIMED_OUT = ("The job running on runner GitHub Actions 12 has exceeded the "
             "maximum execution time of 30 minutes.")


def _run(run_id=37362862791, *, attempt=1, status="completed",
         conclusion="cancelled", event="push", branch="main",
         path=".github/workflows/ci.yml", workflow_id=11, name="CI",
         created="2026-10-05T19:21:00Z", updated="2026-10-05T19:40:00Z"):
    return {
        "id": run_id, "name": name, "path": path, "workflow_id": workflow_id,
        "event": event, "head_branch": branch, "head_sha": SHA,
        "status": status, "conclusion": conclusion, "run_attempt": attempt,
        "created_at": created, "updated_at": updated,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def _job(job_id, name, conclusion="cancelled"):
    return {"id": job_id, "name": name, "status": "completed",
            "conclusion": conclusion}


class FakeGitHub:
    """The four reads and one write the decision makes, recorded.

    `rerun` behaves the way GitHub does: the run's attempt moves on by one and
    it is queued again, so the next listing shows the new attempt."""

    def __init__(self, runs, jobs=None, annotations=None, window=None,
                 refuse=None):
        self._runs = [dict(r) for r in runs]
        self._jobs = jobs or {}
        self._annotations = annotations or {}
        self._window = window
        self._refuse = refuse
        self.reruns: list = []
        self.annotation_reads: list = []
        self.window_reads = 0

    def runs(self):
        return [dict(r) for r in self._runs]

    def jobs(self, run_id, attempt):
        return self._jobs.get((run_id, attempt), self._jobs.get(run_id))

    def annotations(self, job_id):
        self.annotation_reads.append(job_id)
        value = self._annotations.get(job_id, [])
        if value is None:
            return None
        return [{"message": m} for m in value]

    def rerun(self, run_id):
        if self._refuse:
            raise RuntimeError(self._refuse)
        self.reruns.append(run_id)
        for run in self._runs:
            if run["id"] == run_id:
                run["run_attempt"] += 1
                run["status"] = "queued"
                run["conclusion"] = None

    def report_window(self):
        self.window_reads += 1
        return self._window


def _lost_world(**kw):
    """Run 37362862791 as it stood at 12:21 PT: three jobs, every one of them
    cancelled with GitHub's "not acquired" annotation."""
    jobs = [_job(1, "Lint · Guards"), _job(2, "Tests (1/2)"), _job(3, "Tests (2/2)")]
    return FakeGitHub(
        [_run()],
        jobs={37362862791: jobs},
        annotations={1: [NOT_ACQUIRED], 2: [NOT_ACQUIRED], 3: [NOT_ACQUIRED]},
        **kw,
    )


# --------------------------------------------------------------------------
# 1: the lost run is re-run once, with one receipt line
# --------------------------------------------------------------------------
def test_a_run_whose_every_job_was_never_given_a_machine_is_re_run():
    gh = _lost_world()
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == [37362862791]
    receipts = [line for line in lines if line.startswith(runner_lost.PREFIX)]
    assert len(receipts) == 1, lines
    line = receipts[0]
    assert REPO in line
    assert "37362862791" in line
    for job in ("Lint · Guards", "Tests (1/2)", "Tests (2/2)"):
        assert job in line
    assert "not acquired by Runner" in line
    assert "\n" not in line


def test_the_re_run_happens_once_per_attempt_however_many_sweeps_follow():
    """The sweep runs every fifteen minutes. Once the run is re-run it is a
    new attempt, queued, and no later pass re-runs it again."""
    gh = _lost_world()
    for _ in range(4):
        runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == [37362862791]


def test_jobs_that_succeeded_or_were_skipped_do_not_stop_the_re_run():
    """Only the NON-SUCCESS jobs have to carry the annotation; a green job is
    not a reason to leave the rest stranded."""
    gh = FakeGitHub(
        [_run()],
        jobs={37362862791: [_job(1, "Lint · Guards"),
                            _job(2, "Tests", "success"),
                            _job(3, "Deploy preview", "skipped")]},
        annotations={1: [NOT_ACQUIRED]},
    )
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == [37362862791]
    receipt = [line for line in lines if line.startswith(runner_lost.PREFIX)][0]
    assert "Lint · Guards" in receipt
    assert "Tests," not in receipt and "Deploy preview" not in receipt
    assert gh.annotation_reads == [1], "a green job's annotations are never read"


# --------------------------------------------------------------------------
# 2: every other kind of cancellation is left alone
# --------------------------------------------------------------------------
@pytest.mark.parametrize("why", [SUPERSEDED, BY_A_PERSON, TIMED_OUT],
                         ids=["newer-push", "a-person", "job-timeout"])
def test_a_run_cancelled_for_any_other_reason_is_not_re_run(why):
    gh = FakeGitHub(
        [_run()],
        jobs={37362862791: [_job(1, "Lint · Guards"), _job(2, "Tests")]},
        annotations={1: [why], 2: [why]},
    )
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []
    assert not [line for line in lines if line.startswith(runner_lost.PREFIX)
                and "re-ran" in line], lines


def test_a_run_a_newer_push_superseded_is_not_re_run_even_if_its_jobs_were_lost():
    """A newer CI run of the same workflow on the branch carries the newer
    commit; the superseded one is never the train's candidate, whatever its
    jobs say."""
    older = _run(100, created="2026-10-05T19:00:00Z")
    newer = _run(200, status="in_progress", conclusion=None,
                 created="2026-10-05T19:05:00Z")
    gh = FakeGitHub([newer, older],
                    jobs={100: [_job(1, "Tests")]},
                    annotations={1: [NOT_ACQUIRED]})
    runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []


def test_one_lost_job_beside_one_a_person_cancelled_is_not_re_run():
    gh = FakeGitHub(
        [_run()],
        jobs={37362862791: [_job(1, "Lint · Guards"), _job(2, "Tests")]},
        annotations={1: [NOT_ACQUIRED], 2: [BY_A_PERSON]},
    )
    runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []


def test_a_job_with_no_annotation_at_all_is_not_a_lost_runner():
    gh = FakeGitHub(
        [_run()],
        jobs={37362862791: [_job(1, "Lint · Guards"), _job(2, "Tests")]},
        annotations={1: [NOT_ACQUIRED], 2: []},
    )
    runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []


def test_annotations_that_cannot_be_read_re_run_nothing():
    """Unknown is not "not acquired": the sweep fails closed and says so."""
    gh = FakeGitHub(
        [_run()],
        jobs={37362862791: [_job(1, "Lint · Guards")]},
        annotations={1: None},
    )
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []
    assert any("37362862791" in line and "could not" in line for line in lines), lines


@pytest.mark.parametrize("run", [
    _run(event="schedule"),
    _run(event="workflow_dispatch"),
    _run(branch="agent/DRE-1-widget"),
    _run(path=".github/workflows/qa-review.yml", name="QA Review"),
    _run(conclusion="failure"),
    _run(status="in_progress", conclusion=None),
], ids=["nightly", "hand-dispatch", "pr-branch", "review", "failure-is-the-medics",
        "still-running"])
def test_only_a_finished_cancelled_default_branch_ci_run_is_a_candidate(run):
    gh = FakeGitHub([run], jobs={37362862791: [_job(1, "Tests")]},
                    annotations={1: [NOT_ACQUIRED]})
    runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []


def test_each_workflow_on_the_branch_is_judged_on_its_own_newest_run():
    """A push starts several workflows. A lost Lint run is re-run although
    the CI run for the same commit is still going."""
    ci = _run(300, status="in_progress", conclusion=None, workflow_id=11,
              created="2026-10-05T19:21:00Z")
    lint = _run(301, workflow_id=12, path=".github/workflows/lint.yml",
                name="Lint", created="2026-10-05T19:21:01Z")
    gh = FakeGitHub([lint, ci], jobs={301: [_job(9, "Guards")]},
                    annotations={9: [NOT_ACQUIRED]})
    runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == [301]


def test_a_refused_re_run_is_an_error_and_claims_nothing():
    gh = _lost_world(refuse="HTTP 403: Resource not accessible by integration")
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []
    errors = [line for line in lines if line.startswith("ERROR:")]
    assert len(errors) == 1 and "37362862791" in errors[0], lines
    assert not [line for line in lines if line.startswith(runner_lost.PREFIX)
                and "re-ran" in line], lines


def test_an_unreadable_listing_re_runs_nothing():
    gh = _lost_world()
    gh.runs = lambda: None
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []
    assert lines and "could not" in lines[0]


# --------------------------------------------------------------------------
# 3: the re-run attempt is lost too — reported once, never re-run again
# --------------------------------------------------------------------------
WINDOW = runner_lost.Window(start="2026-10-05T21:30:00Z", end="2026-10-05T21:45:00Z")


def _lost_again(window=WINDOW, updated="2026-10-05T21:40:00Z"):
    return FakeGitHub(
        [_run(attempt=2, updated=updated)],
        jobs={(37362862791, 2): [_job(1, "Lint · Guards"),
                                 _job(2, "Tests", "success")]},
        annotations={1: [NOT_ACQUIRED]},
        window=window,
    )


def test_a_second_lost_attempt_is_not_re_run_and_is_reported():
    gh = _lost_again()
    lines = runner_lost.sweep(REPO, "main", gh)
    assert gh.reruns == []
    reports = [line for line in lines if line.startswith(runner_lost.PREFIX)]
    assert len(reports) == 1, lines
    assert "37362862791" in reports[0] and REPO in reports[0]
    assert "Lint · Guards" in reports[0]
    assert "attempt 2" in reports[0]
    assert "not re-run" in reports[0]


def test_the_second_lost_attempt_is_reported_once_across_sweeps():
    """Each scheduled pass reports what finished between the previous
    scheduled pass's start and its own: the windows tile, so the outage is
    named by exactly one pass."""
    first = _lost_again(window=WINDOW)
    later = _lost_again(window=runner_lost.Window(start="2026-10-05T21:45:00Z",
                                                  end="2026-10-05T22:00:00Z"))
    reported = runner_lost.sweep(REPO, "main", first) + runner_lost.sweep(REPO, "main", later)
    assert len([line for line in reported if line.startswith(runner_lost.PREFIX)]) == 1
    assert first.reruns == [] and later.reruns == []


def test_a_pass_that_is_not_scheduled_leaves_the_report_to_the_next_one():
    gh = _lost_again(window=runner_lost.Window(defer="started by repository_dispatch"))
    lines = runner_lost.sweep(REPO, "main", gh)
    assert not [line for line in lines if line.startswith(runner_lost.PREFIX)]
    assert gh.reruns == []


def test_a_window_that_cannot_be_read_still_reports_the_outage():
    """Visible beats tidy: an unreadable window may repeat the line, it never
    hides the outage."""
    gh = _lost_again(window=None)
    lines = runner_lost.sweep(REPO, "main", gh)
    assert len([line for line in lines if line.startswith(runner_lost.PREFIX)]) == 1
    assert gh.reruns == []


def test_the_window_is_read_only_when_there_is_something_to_report():
    gh = _lost_world(window=WINDOW)
    runner_lost.sweep(REPO, "main", gh)
    assert gh.window_reads == 0


# --------------------------------------------------------------------------
# 4: the window — read off GitHub's record of the sweep's own runs
# --------------------------------------------------------------------------
def _sweep_run(run_id, *, event="schedule", conclusion="success",
               created, started=None, status="completed"):
    return {"id": run_id, "event": event, "status": status,
            "conclusion": conclusion, "created_at": created,
            "run_started_at": started or created}


def test_the_window_runs_from_the_previous_scheduled_pass_to_this_one():
    own = _sweep_run(5, created="2026-10-05T22:00:00Z", status="in_progress",
                     conclusion=None, started="2026-10-05T22:00:05Z")
    previous = [
        _sweep_run(4, created="2026-10-05T21:45:00Z", started="2026-10-05T21:45:04Z"),
        _sweep_run(3, created="2026-10-05T21:30:00Z"),
    ]
    window = runner_lost.window_from(own, previous)
    assert window == runner_lost.Window(start="2026-10-05T21:45:04Z",
                                        end="2026-10-05T22:00:05Z")


def test_a_previous_pass_that_never_ran_does_not_close_the_window():
    """A sweep GitHub never gave a machine either reported nothing, so the
    window reaches back past it."""
    own = _sweep_run(5, created="2026-10-05T22:00:00Z", status="in_progress",
                     conclusion=None)
    previous = [
        _sweep_run(4, created="2026-10-05T21:45:00Z", conclusion="cancelled"),
        _sweep_run(3, created="2026-10-05T21:30:00Z", conclusion="failure"),
    ]
    assert runner_lost.window_from(own, previous).start == "2026-10-05T21:30:00Z"


def test_a_pass_not_started_by_the_schedule_defers_the_report():
    own = _sweep_run(5, event="repository_dispatch", created="2026-10-05T22:00:00Z",
                     status="in_progress", conclusion=None)
    window = runner_lost.window_from(own, [])
    assert window.defer and "repository_dispatch" in window.defer


@pytest.mark.parametrize("when,due", [
    ("2026-10-05T21:30:00Z", True),
    ("2026-10-05T21:44:59Z", True),
    ("2026-10-05T21:45:00Z", False),
    ("2026-10-05T21:29:59Z", False),
])
def test_due_is_half_open(when, due):
    assert runner_lost.due(WINDOW, when) is due


# --------------------------------------------------------------------------
# 5: the sweep's wiring — every repo, the same rule
# --------------------------------------------------------------------------
class _Actions:
    """`reconcile._actions_read`, answered from a table keyed on the API
    path, every call recorded."""

    def __init__(self, table):
        self.table = table
        self.calls: list = []

    def __call__(self, args):
        self.calls.append(args)
        path = args[1] if args[:1] == ("api",) else ""
        for prefix, payload in self.table.items():
            if path.startswith(prefix):
                return json.dumps(payload), None
        return None, "rc=1: HTTP 404"


@pytest.mark.parametrize("repo", ["dreadnought-foundry/portico",
                                  "DeltaSolv/deltasolv"])
def test_the_sweep_re_runs_a_lost_run_in_whichever_repo_it_sweeps(monkeypatch, capsys, repo):
    run = _run(4242)
    actions = _Actions({
        f"repos/{repo}/actions/runs?": {"workflow_runs": [run]},
        f"repos/{repo}/actions/runs/4242/attempts/1/jobs": {
            "jobs": [_job(77, "Tests")]},
    })
    dispatched: list = []
    monkeypatch.setattr(reconcile, "REPO", repo)
    monkeypatch.setattr(reconcile, "_default_branch", "main")
    monkeypatch.setattr(reconcile, "_actions_read", actions)
    monkeypatch.setattr(reconcile, "gh_dispatch", lambda *a: dispatched.append(a))
    monkeypatch.setattr(reconcile, "_check_run_annotations",
                        lambda job_id: [{"message": NOT_ACQUIRED}])
    reconcile._write_failures.clear()

    reconcile.rerun_runner_lost_ci()

    assert dispatched == [("run", "rerun", "4242", "--failed", "--repo", repo)]
    out = capsys.readouterr().out
    assert repo in out and "4242" in out and "Tests" in out
    listing = actions.calls[0][1]
    assert listing.startswith(f"repos/{repo}/actions/runs?")
    assert "branch=main" in listing and "event=push" in listing
    assert reconcile._write_failures == []


def test_a_refused_re_run_turns_the_sweep_red(monkeypatch):
    run = _run(4242)
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "_default_branch", "main")
    monkeypatch.setattr(reconcile, "_actions_read", _Actions({
        f"repos/{REPO}/actions/runs?": {"workflow_runs": [run]},
        f"repos/{REPO}/actions/runs/4242/attempts/1/jobs": {"jobs": [_job(77, "Tests")]},
    }))

    def refuse(*args):
        raise reconcile.ReconcileWriteError("gh run rerun failed rc=1: HTTP 403")

    monkeypatch.setattr(reconcile, "gh_dispatch", refuse)
    monkeypatch.setattr(reconcile, "_check_run_annotations",
                        lambda job_id: [{"message": NOT_ACQUIRED}])
    reconcile._write_failures.clear()
    try:
        reconcile.rerun_runner_lost_ci()
        assert len(reconcile._write_failures) == 1
        assert "4242" in reconcile._write_failures[0]
    finally:
        reconcile._write_failures.clear()


@pytest.mark.parametrize("answer", [
    '{\n  "message": "Bad credentials",\n  "status": "401"\n}',
    "main\nfeature", "has space", "../main", "",
])
def test_a_default_branch_that_is_not_a_ref_name_reads_nothing(monkeypatch, capsys, answer):
    """`default_branch()` is the silent `gh()`, which hands back a failed
    call's JSON error body on stdout. That body is not a branch: no CI listing
    is asked for, and the sweep says it could not read the branch."""
    actions = _Actions({})
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "_default_branch", answer)
    monkeypatch.setattr(reconcile, "_actions_read", actions)
    monkeypatch.setattr(reconcile, "_degraded", [])
    reconcile._write_failures.clear()

    reconcile.rerun_runner_lost_ci()

    assert actions.calls == []
    assert "DEGRADED: runner-lost:" in capsys.readouterr().out
    assert reconcile._write_failures == []


def test_the_sweep_reads_its_window_off_its_own_scheduled_runs(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setenv("GITHUB_RUN_ID", "900")
    own = dict(_sweep_run(900, created="2026-10-05T22:00:00Z", status="in_progress",
                          conclusion=None, started="2026-10-05T22:00:05Z"),
               workflow_id=55)
    actions = _Actions({
        f"repos/{REPO}/actions/runs/900": own,
        f"repos/{REPO}/actions/workflows/55/runs": {"workflow_runs": [
            own, _sweep_run(899, created="2026-10-05T21:45:00Z",
                            started="2026-10-05T21:45:04Z")]},
    })
    monkeypatch.setattr(reconcile, "_actions_read", actions)
    window = reconcile._RunnerLostOps().report_window()
    assert window == runner_lost.Window(start="2026-10-05T21:45:04Z",
                                        end="2026-10-05T22:00:05Z")
    assert "event=schedule" in actions.calls[1][1]


def test_the_backstop_runs_in_every_full_sweep():
    """In `main()`'s backstop tuple, so every repo's scheduled sweep runs it —
    and not behind the off-rail fence, which only stands down Linear writers."""
    source = inspect.getsource(reconcile.main)
    tuple_text = source[source.index("for backstop in ("):source.index("):", source.index("for backstop in ("))]
    assert "rerun_runner_lost_ci," in tuple_text
    assert "rerun_runner_lost_ci" not in reconcile.OFF_RAIL_SKIPPED


def test_nothing_in_the_rule_names_a_repo():
    text = (ROOT / "scripts" / "runner_lost.py").read_text(encoding="utf-8")
    for slug in ("agent-bureau", "portico", "atlas", "deltasolv", "bureau-pipeline"):
        assert slug not in text.lower(), slug


def test_the_sweep_summary_carries_the_receipt_lines():
    """The run log is four passes an hour that nobody opens; the receipt lines
    are lifted into the step summary beside the spend."""
    text = (ROOT / ".github" / "workflows" / "reconcile.yml").read_text(encoding="utf-8")
    assert "runner-lost:" in text
