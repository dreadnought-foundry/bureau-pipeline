"""A `Pipeline Tests` job on main that runs past its usual time with no
progress is canceled and re-run once (DRE-8652).

The case it is replayed against: Pipeline Tests run 40502923604, a push to
main at head 69b518ceb on 2026-10-27. Its job `scripts unit tests (walk
tests/test_console_project_model_keeps_the_suite_green.py shard 5 of 8)`
printed its last line at 5:20:53 AM PT and was stopped by its own 20-minute
limit at 5:40:43 AM PT. Nobody acted until 10:47 AM PT, when a bot re-ran the
failed jobs and attempt 2 passed in six minutes. The fixture holds that run's
job records, an excerpt of the canceled job's log and the history it is judged
against, read once from GitHub's Actions API, so nothing here touches the
network: `gh` is stubbed at `subprocess.run`.

Run: python3 -m pytest tests/test_hung_job.py -v
"""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import canceled_check  # noqa: E402
import hung_job  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "hung-job-40502923604.json"
SOURCE = ROOT / "scripts" / "hung_job.py"
REPO = "dreadnought-foundry/bureau-pipeline"
RUN_ID = 40502923604
SHARD = ("scripts unit tests (walk tests/test_console_project_model_keeps_the_"
         "suite_green.py shard 5 of 8)")
SHARD_JOB = 122184991649


#: The runner every job of the 10-27 fixture is given (DRE-8817). The
#: fixture's records carry no runner name, and a job on an unknown service is
#: never re-run, so the tests that re-run it name one — a placeholder, not
#: the machine the 10-27 run used.
FIXTURE_RUNNER = "GitHub Actions 1000001"
#: The `ci` column those tests read: both Mac pools, never GitHub's own.
OTHER_COLUMN = '["self-hosted", "bureau-light"]'


def fixture() -> dict:
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for jobs in fx["jobs"].values():
        for job in jobs:
            job["runner_name"] = job.get("runner_name") or FIXTURE_RUNNER
    return fx


@pytest.fixture(autouse=True)
def _ci_column(monkeypatch):
    """The `CI_RUNS_ON` channel-watch.yml hands the watch, naming a service
    the fixture's jobs did not run on — so every re-run below is today's."""
    monkeypatch.setenv("CI_RUNS_ON", OTHER_COLUMN)


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def shard(jobs) -> dict:
    return next(j for j in jobs if j["name"] == SHARD)


# --- the stubbed `gh` -------------------------------------------------------

class FakeGh:
    """`gh api` as `hung_job` calls it, answering off a state built from the
    fixture. Every call is recorded; a POST is a write."""

    def __init__(self, fx: dict):
        self.fx = fx
        self.watched: list = []
        self.runs: dict = {}
        self.jobs: dict = {}
        self.history_runs = fx["history"]["runs"]
        self.history_jobs = {int(k): v for k, v in fx["history"]["jobs"].items()}
        self.annotations = {int(k): v for k, v in fx["annotations"].items()}
        self.logs = {int(k): v for k, v in fx["logs"].items()}
        self.fail: list = []      # path regexes whose GET fails
        self.refuse: list = []    # path regexes whose POST is refused
        self.calls: list = []     # (method, path)
        # A run's status sequence after a cancel: reads pop from the front;
        # once empty the run reads `completed`.
        self.after_cancel: dict = {}
        self.canceled: set = set()

    # The record a run read answers with.
    def _run(self, run_id: int) -> dict:
        record = dict(self.runs[run_id])
        if run_id in self.canceled:
            pending = self.after_cancel.get(run_id) or []
            if pending:
                record["status"] = pending.pop(0)
            else:
                record["status"] = "completed"
                record["conclusion"] = "cancelled"
        return record

    def posts(self) -> list:
        return [path for method, path in self.calls if method == "POST"]

    def __call__(self, argv, **_kw):
        assert argv[0] == "gh" and argv[1] == "api", argv
        method = "GET"
        rest = list(argv[2:])
        if "-X" in rest:
            i = rest.index("-X")
            method = rest[i + 1]
            del rest[i:i + 2]
        if "--method" in rest:
            i = rest.index("--method")
            method = rest[i + 1]
            del rest[i:i + 2]
        rest = [a for a in rest if a != "--allow-escape-sequences"]
        assert len(rest) == 1, argv
        path = rest[0]
        self.calls.append((method, path))
        if method == "POST":
            return self._post(argv, path)
        for pattern in self.fail:
            if re.search(pattern, path):
                return self._done(argv, 1, '{"message":"Server Error"}',
                                  "gh: Server Error (HTTP 502)")
        body = self._get(path)
        if body is None:
            return self._done(argv, 1, '{"message":"Not Found"}',
                              "gh: Not Found (HTTP 404)")
        if isinstance(body, str):
            return self._done(argv, 0, body, "")
        return self._done(argv, 0, json.dumps(body), "")

    @staticmethod
    def _done(argv, code, out, err):
        return subprocess.CompletedProcess(argv, code, out, err)

    def _post(self, argv, path):
        for pattern in self.refuse:
            if re.search(pattern, path):
                return self._done(argv, 1, '{"message":"Cannot cancel"}',
                                  "gh: Cannot cancel (HTTP 409)")
        found = re.search(r"/actions/runs/(\d+)/(cancel|force-cancel|rerun-failed-jobs)$",
                          path)
        assert found, path
        run_id, act = int(found.group(1)), found.group(2)
        if act in ("cancel", "force-cancel"):
            self.canceled.add(run_id)
        return self._done(argv, 0, "", "")

    def _get(self, path):
        base = f"repos/{REPO}/actions"
        path_only, _, query = path.partition("?")
        if path_only == f"{base}/workflows/tests.yml/runs":
            if "status=success" in query:
                return {"total_count": len(self.history_runs),
                        "workflow_runs": self.history_runs}
            return {"total_count": len(self.watched),
                    "workflow_runs": [self._run(r["id"]) if r["id"] in self.runs
                                      else r for r in self.watched]}
        found = re.fullmatch(rf"{re.escape(base)}/runs/(\d+)", path_only)
        if found:
            run_id = int(found.group(1))
            return self._run(run_id) if run_id in self.runs else None
        found = re.fullmatch(rf"{re.escape(base)}/runs/(\d+)/attempts/(\d+)/jobs",
                             path_only)
        if found:
            jobs = self.jobs.get((int(found.group(1)), int(found.group(2))))
            return None if jobs is None else {"total_count": len(jobs), "jobs": jobs}
        found = re.fullmatch(rf"{re.escape(base)}/runs/(\d+)/jobs", path_only)
        if found:
            jobs = self.history_jobs.get(int(found.group(1)))
            return None if jobs is None else {"total_count": len(jobs), "jobs": jobs}
        found = re.fullmatch(rf"repos/{re.escape(REPO)}/check-runs/(\d+)/annotations",
                             path_only)
        if found:
            return self.annotations.get(int(found.group(1)), [])
        found = re.fullmatch(rf"{re.escape(base)}/jobs/(\d+)/logs", path_only)
        if found:
            return self.logs.get(int(found.group(1)))
        return None


@pytest.fixture
def gh(monkeypatch):
    """A FakeGh wired in at `subprocess.run`, with no real waiting."""
    fake = FakeGh(fixture())
    monkeypatch.setattr(hung_job.subprocess, "run", fake)
    monkeypatch.setattr(hung_job, "_sleep", lambda _s: None)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return fake


def run_main(monkeypatch, capsys, argv, now) -> tuple[int, list]:
    monkeypatch.setattr(hung_job, "_now", lambda: at(now))
    code = hung_job.main(argv)
    out = capsys.readouterr().out
    return code, [line for line in out.splitlines() if line.strip()]


def running_incident(fx: dict) -> tuple[dict, list]:
    """Run 40502923604 as a pass at 5:34 AM PT would have read it: attempt 1
    still going, the shard in its test step since 5:20:47 AM PT, every other
    job finished green."""
    run = dict(fx["attempts"]["1"], status="in_progress", conclusion=None,
               run_attempt=1, name="Pipeline Tests", event="push",
               head_branch="main")
    jobs = copy.deepcopy([j for j in fx["jobs"]["1"]
                          if j["conclusion"] in ("success", "skipped")])
    live = copy.deepcopy(shard(fx["jobs"]["1"]))
    live.update(status="in_progress", conclusion=None, completed_at=None)
    steps = []
    for step in live["steps"]:
        if step["number"] < 5:
            steps.append(step)
        elif step["number"] == 5:
            steps.append(dict(step, status="in_progress", conclusion=None,
                              completed_at=None))
        else:
            steps.append(dict(step, status="queued", conclusion=None,
                              started_at=None, completed_at=None))
    live["steps"] = steps
    jobs.append(live)
    return run, jobs


def finished_incident(fx: dict) -> tuple[dict, list]:
    """Run 40502923604 as it stood at 6:26 AM PT: attempt 1 finished
    `cancelled`, nobody had re-run it yet."""
    run = dict(fx["attempts"]["1"], name="Pipeline Tests", event="push",
               head_branch="main")
    return run, copy.deepcopy(fx["jobs"]["1"])


def watch_running(gh: FakeGh, run: dict, jobs: list) -> None:
    gh.watched = [run]
    gh.runs[run["id"]] = run
    gh.jobs[(run["id"], run["run_attempt"])] = jobs


# --- the usual time and the limit ------------------------------------------

def test_the_constants_are_the_cards():
    assert hung_job.TAG == "hung-job:"
    assert hung_job.HISTORY_RUNS == 10
    assert hung_job.MIN_HISTORY == 5
    assert hung_job.FACTOR == 1.5
    assert hung_job.MARGIN_MINUTES == 5
    assert hung_job.PASS_MINUTES == (4, 14, 24, 34, 44, 54)


def _history(fx):
    return [(r["id"], fx["history"]["jobs"].get(str(r["id"]), []))
            for r in fx["history"]["runs"]]


def test_the_shards_usual_time_is_under_eight_minutes_from_ten_green_runs():
    fx = fixture()
    usual = hung_job.usual_time(SHARD, _history(fx))
    assert usual.minutes is not None and 6 <= usual.minutes < 8
    assert len(usual.runs) == hung_job.HISTORY_RUNS
    assert usual.runs == tuple(r["id"] for r in fx["history"]["runs"][:10])


def test_the_limit_is_the_larger_of_one_and_a_half_times_and_plus_five():
    assert hung_job.limit_minutes(6.0) == 11.0
    assert hung_job.limit_minutes(10.0) == 15.0
    assert hung_job.limit_minutes(20.0) == 30.0


def test_only_the_exact_job_name_counts():
    fx = fixture()
    history = [(rid, [dict(j, name=j["name"].replace("shard 5 of 8", "shard 5 of 7"))
                      for j in jobs]) for rid, jobs in _history(fx)]
    usual = hung_job.usual_time(SHARD, history)
    assert usual.minutes is None and usual.runs == ()


def test_four_green_runs_are_no_usual_time():
    fx = fixture()
    history = _history(fx)[:4]
    usual = hung_job.usual_time(SHARD, history)
    assert usual.minutes is None
    assert len(usual.runs) == 4


def test_a_job_that_did_not_succeed_is_not_history():
    fx = fixture()
    history = [(rid, [dict(j, conclusion="failure") if j["name"] == SHARD else j
                      for j in jobs]) for rid, jobs in _history(fx)]
    assert hung_job.usual_time(SHARD, history).minutes is None


# --- the running rule --------------------------------------------------------

def _usual(minutes=6.05, k=10):
    return hung_job.Usual(minutes, tuple(range(k)))


def test_a_running_job_past_its_limit_with_no_step_since_its_usual_time_is_hung():
    _run, jobs = running_incident(fixture())
    verdict = hung_job.judge_running(shard(jobs), _usual(), at("2026-10-27T12:34:00Z"))
    assert verdict.hung
    assert hung_job.pt(verdict.quiet_since) == "5:20:47 AM PT"


def test_a_running_job_whose_newest_step_started_after_its_usual_time_is_not_hung():
    _run, jobs = running_incident(fixture())
    job = shard(jobs)
    job["steps"][4]["started_at"] = "2026-10-27T12:27:00Z"
    verdict = hung_job.judge_running(job, _usual(), at("2026-10-27T12:34:00Z"))
    assert not verdict.hung


def test_a_running_job_whose_newest_step_completed_after_its_usual_time_is_not_hung():
    _run, jobs = running_incident(fixture())
    job = shard(jobs)
    job["steps"][3]["completed_at"] = "2026-10-27T12:30:00Z"
    verdict = hung_job.judge_running(job, _usual(), at("2026-10-27T12:34:00Z"))
    assert not verdict.hung


def test_a_running_job_inside_its_limit_is_not_hung():
    _run, jobs = running_incident(fixture())
    verdict = hung_job.judge_running(shard(jobs), _usual(), at("2026-10-27T12:30:00Z"))
    assert not verdict.hung


# --- the finished rule -------------------------------------------------------

def _finished(job, usual, log, notes=()):
    return hung_job.judge_finished(job, usual, lambda: list(notes), lambda: log)


def test_the_canceled_shard_is_hung_on_its_finished_record():
    fx = fixture()
    verdict = _finished(shard(fx["jobs"]["1"]), _usual(), fx["logs"][str(SHARD_JOB)])
    assert verdict.hung
    assert hung_job.pt(verdict.quiet_since) == "5:20:53 AM PT"


def test_output_after_its_usual_time_is_a_persons_cancel():
    fx = fixture()
    log = fx["logs"][str(SHARD_JOB)].replace(
        "2026-10-27T12:40:36.9014838Z ##[error]",
        "2026-10-27T12:30:00.0000000Z tests/x.py::test_y PASSED [ 41%]\n"
        "2026-10-27T12:40:36.9014838Z ##[error]")
    verdict = _finished(shard(fx["jobs"]["1"]), _usual(), log)
    assert not verdict.hung


def test_a_line_after_the_cancel_line_does_not_count_as_output():
    fx = fixture()
    log = fx["logs"][str(SHARD_JOB)]
    assert "2026-10-27T12:40:37.1673010Z Post job cleanup." in log
    assert _finished(shard(fx["jobs"]["1"]), _usual(), log).hung


def test_a_canceled_job_short_of_its_limit_with_no_limit_named_is_not_hung():
    fx = fixture()
    job = dict(shard(fx["jobs"]["1"]), completed_at="2026-10-27T12:28:00Z")
    verdict = _finished(job, _usual(), fx["logs"][str(SHARD_JOB)])
    assert not verdict.hung


def test_a_canceled_job_short_of_its_limit_stopped_by_its_own_limit_counts():
    fx = fixture()
    job = dict(shard(fx["jobs"]["1"]), completed_at="2026-10-27T12:30:00Z")
    notes = [{"message": "The job has exceeded the maximum execution time of 10m0s"}]
    verdict = _finished(job, _usual(), fx["logs"][str(SHARD_JOB)], notes)
    assert verdict.hung


def test_a_failed_job_is_never_hung():
    fx = fixture()
    job = dict(shard(fx["jobs"]["1"]), conclusion="failure")
    assert not _finished(job, _usual(), fx["logs"][str(SHARD_JOB)]).hung


# --- the pass clock ----------------------------------------------------------

@pytest.mark.parametrize("moment, expected", [
    ("2026-10-27T12:31:25Z", "2026-10-27T12:34:00Z"),
    ("2026-10-27T12:34:00Z", "2026-10-27T12:34:00Z"),
    ("2026-10-27T12:34:01Z", "2026-10-27T12:44:00Z"),
    ("2026-10-27T12:55:00Z", "2026-10-27T13:04:00Z"),
    ("2026-10-27T23:58:00Z", "2026-10-28T00:04:00Z"),
])
def test_the_next_pass_is_on_the_cron_minutes(moment, expected):
    assert hung_job.next_pass(at(moment)) == at(expected)


# --- the replay --------------------------------------------------------------

def _replay_state(gh: FakeGh) -> None:
    fx = gh.fx
    gh.runs[RUN_ID] = fx["run"]
    gh.jobs[(RUN_ID, 1)] = fx["jobs"]["1"]
    gh.jobs[(RUN_ID, 2)] = fx["jobs"]["2"]


def test_replay_40502923604(gh, monkeypatch, capsys):
    _replay_state(gh)
    code, lines = run_main(monkeypatch, capsys,
                           ["replay", "--repo", REPO, "--run", str(RUN_ID)],
                           "2026-11-01T18:00:00Z")
    assert code == 0
    line = next(x for x in lines if f'job="{SHARD}"' in x)
    assert line.startswith(f"hung-job: replay run={RUN_ID} ")
    usual = float(re.search(r" usual=([\d.]+)m ", line).group(1))
    assert usual < 8
    runs = re.search(r" from=(\d+) runs \(([^)]*)\) ", line)
    assert int(runs.group(1)) >= 5
    ids = [int(x) for x in re.findall(r"\d+", runs.group(2))]
    assert len(ids) == int(runs.group(1))
    assert all(i in {r["id"] for r in gh.fx["history"]["runs"]} for i in ids)
    assert "quiet-since=5:20:53 AM PT" in line
    when = re.search(r" would-cancel-at=(\d+):(\d\d):(\d\d) AM PT ", line)
    assert when and when.group(1) == "5" and 30 <= int(when.group(2)) <= 40
    assert "stopped-at=5:40:43 AM PT" in line
    assert line.endswith("— re-run once")
    assert f"hung-job: replay run={RUN_ID} attempt 2 success at 10:53:47 AM PT" in lines
    assert gh.posts() == []


def test_replay_reads_attempt_one_and_history_from_before_the_run(gh, monkeypatch, capsys):
    _replay_state(gh)
    run_main(monkeypatch, capsys, ["replay", "--repo", REPO, "--run", str(RUN_ID)],
             "2026-11-01T18:00:00Z")
    paths = [p for _m, p in gh.calls]
    assert any(p.startswith(f"repos/{REPO}/actions/runs/{RUN_ID}/attempts/1/jobs")
               for p in paths)
    listing = next(p for p in paths if "status=success" in p)
    query = parse_qs(listing.partition("?")[2])
    assert query["branch"] == ["main"]
    assert query["created"] == ["<2026-10-27T12:20:01Z"]
    assert any(p == f"repos/{REPO}/actions/jobs/{SHARD_JOB}/logs" for p in paths)


def test_replay_of_a_job_that_stopped_before_its_pass_names_the_re_run_pass(
        gh, monkeypatch, capsys):
    _replay_state(gh)
    jobs = copy.deepcopy(gh.fx["jobs"]["1"])
    # Past its limit, but stopped before the 5:34 AM PT pass reached it. Only
    # the job's own stop moves, so the step records cannot settle it.
    shard(jobs)["completed_at"] = "2026-10-27T12:33:50Z"
    gh.jobs[(RUN_ID, 1)] = jobs
    code, lines = run_main(monkeypatch, capsys,
                           ["replay", "--repo", REPO, "--run", str(RUN_ID)],
                           "2026-11-01T18:00:00Z")
    assert code == 0
    line = next(x for x in lines if f'job="{SHARD}"' in x)
    assert " would-cancel-at=none would-rerun-at=5:34:00 AM PT " in line
    assert "stopped-at=5:33:50 AM PT" in line
    assert line.endswith("— re-run once")
    assert gh.posts() == []


def test_replay_without_a_later_attempt_prints_no_attempt_line(gh, monkeypatch, capsys):
    _replay_state(gh)
    gh.runs[RUN_ID] = dict(gh.fx["attempts"]["1"])
    _code, lines = run_main(monkeypatch, capsys,
                            ["replay", "--repo", REPO, "--run", str(RUN_ID)],
                            "2026-11-01T18:00:00Z")
    assert any("re-run once" in x for x in lines)
    assert not any(" attempt 2 " in x for x in lines)


def test_replay_of_an_unread_log_is_unknown_and_writes_nothing(gh, monkeypatch, capsys):
    _replay_state(gh)
    gh.fail.append(r"/logs$")
    _code, lines = run_main(monkeypatch, capsys,
                            ["replay", "--repo", REPO, "--run", str(RUN_ID)],
                            "2026-11-01T18:00:00Z")
    assert any(x.startswith(f"hung-job: unknown run={RUN_ID} ") for x in lines)
    assert gh.posts() == []


# --- the watch: a running run -----------------------------------------------

NOW_RUNNING = "2026-10-27T12:34:00Z"  # the 5:34 AM PT pass


def test_a_hung_job_on_attempt_one_is_canceled_then_re_run(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.after_cancel[RUN_ID] = ["in_progress", "in_progress"]
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0
    base = f"repos/{REPO}/actions/runs/{RUN_ID}"
    assert gh.posts() == [f"{base}/cancel", f"{base}/rerun-failed-jobs"]
    # The re-run was asked for only after a read of the run said completed.
    rerun_at = gh.calls.index(("POST", f"{base}/rerun-failed-jobs"))
    cancel_at = gh.calls.index(("POST", f"{base}/cancel"))
    reads = [i for i, (m, p) in enumerate(gh.calls) if m == "GET" and p == base
             and cancel_at < i < rerun_at]
    assert len(reads) == 3  # two in_progress, then completed
    line = next(x for x in lines if x.startswith("hung-job: canceled "))
    assert line.startswith(f'hung-job: canceled run={RUN_ID} attempt=1 job="{SHARD}" ')
    assert "quiet-since=5:20:47 AM PT" in line
    assert line.endswith("— re-run requested")


def test_a_cancel_that_does_not_land_is_force_canceled(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    polls = hung_job.CANCEL_WAIT_SECONDS // hung_job.POLL_SECONDS
    gh.after_cancel[RUN_ID] = ["in_progress"] * (polls + 1)
    code, _lines = run_main(monkeypatch, capsys,
                            ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0
    base = f"repos/{REPO}/actions/runs/{RUN_ID}"
    assert gh.posts() == [f"{base}/cancel", f"{base}/force-cancel",
                          f"{base}/rerun-failed-jobs"]


def test_a_run_that_never_finishes_is_not_re_run_this_pass(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    polls = (hung_job.CANCEL_WAIT_SECONDS + hung_job.FORCE_WAIT_SECONDS) // hung_job.POLL_SECONDS
    gh.after_cancel[RUN_ID] = ["in_progress"] * (polls + 5)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    base = f"repos/{REPO}/actions/runs/{RUN_ID}"
    assert gh.posts() == [f"{base}/cancel", f"{base}/force-cancel"]
    assert code == 0
    assert any(x.startswith(f"hung-job: canceled run={RUN_ID} ") for x in lines)


def test_an_unread_run_after_its_cancel_is_not_re_run_this_pass(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.fail.append(rf"/actions/runs/{RUN_ID}$")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0
    assert gh.posts() == [f"repos/{REPO}/actions/runs/{RUN_ID}/cancel"]
    line = next(x for x in lines if x.startswith(f"hung-job: canceled run={RUN_ID} "))
    assert line.endswith("— no re-run yet: whether the run finished could not be "
                         "read; the next pass re-runs it")


def test_a_hung_job_on_attempt_two_is_canceled_and_never_re_run(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    run["run_attempt"] = 2
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0
    base = f"repos/{REPO}/actions/runs/{RUN_ID}"
    assert gh.posts() == [f"{base}/cancel"]
    line = next(x for x in lines if x.startswith("hung-job: canceled "))
    assert "attempt=2" in line
    assert line.endswith("— no re-run: already re-run once")


def test_a_job_still_moving_is_watched_and_left_running(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    shard(jobs)["steps"][4]["started_at"] = "2026-10-27T12:28:00Z"
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0 and gh.posts() == []
    line = next(x for x in lines if x.startswith("hung-job: watching "))
    assert re.fullmatch(
        rf'hung-job: watching run={RUN_ID} attempt=1 job="{re.escape(SHARD)}" '
        r"elapsed=[\d.]+m usual=[\d.]+m limit=[\d.]+m from=10 runs", line)


def test_a_job_with_four_green_runs_is_no_history_and_never_canceled(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.history_runs = gh.fx["history"]["runs"][:4]
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], "2026-10-27T13:30:00Z")
    assert code == 0 and gh.posts() == []
    assert f'hung-job: no-history run={RUN_ID} job="{SHARD}" from=4 runs (needs 5)' in lines


def test_without_apply_no_post_is_made(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys, ["watch", "--repo", REPO], NOW_RUNNING)
    assert code == 0
    assert gh.posts() == []
    assert any(x.startswith(f"hung-job: would-cancel run={RUN_ID} ") for x in lines)


def test_only_push_runs_of_pipeline_tests_on_main_are_watched(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    others = [dict(run, id=1, event="pull_request", head_branch="agent/x"),
              dict(run, id=2, event="schedule"),
              dict(run, id=3, name="Integration Harness")]
    for other in others:
        gh.runs[other["id"]] = other
        gh.jobs[(other["id"], 1)] = jobs
    gh.watched = others
    code, _lines = run_main(monkeypatch, capsys,
                            ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0 and gh.posts() == []
    listing = next(p for _m, p in gh.calls if "/workflows/tests.yml/runs" in p)
    assert "branch=main" in listing and "event=push" in listing


# --- the watch: a finished run ----------------------------------------------

NOW_FINISHED = "2026-10-27T13:34:00Z"  # 6:34 AM PT, before anyone acted


def test_a_finished_cancelled_attempt_one_run_quiet_past_its_usual_time_is_re_run(
        gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0
    assert gh.posts() == [f"repos/{REPO}/actions/runs/{RUN_ID}/rerun-failed-jobs"]
    line = next(x for x in lines if x.startswith("hung-job: rerun "))
    assert line.startswith(f'hung-job: rerun run={RUN_ID} job="{SHARD}" — finished '
                           "cancelled after 20.")
    assert "quiet since 5:20:53 AM PT; attempt 2 requested" in line


def test_a_finished_run_with_output_after_its_usual_time_is_left_alone(
        gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.logs[SHARD_JOB] = gh.logs[SHARD_JOB].replace(
        "2026-10-27T12:40:36.9014838Z ##[error]",
        "2026-10-27T12:39:00.0000000Z tests/x.py::test_y PASSED [ 41%]\n"
        "2026-10-27T12:40:36.9014838Z ##[error]")
    code, _lines = run_main(monkeypatch, capsys,
                            ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []


def test_a_finished_run_on_attempt_two_is_never_re_run(gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    run["run_attempt"] = 2
    watch_running(gh, run, jobs)
    # Attempt 1's jobs read as hung, as they would on the real API, so only
    # the attempt itself can keep the run from a second re-run.
    gh.jobs[(RUN_ID, 1)] = jobs
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []
    assert not any(x.startswith("hung-job: unknown ") for x in lines)


def test_a_finished_run_older_than_six_hours_is_left_alone(gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, _lines = run_main(monkeypatch, capsys,
                            ["watch", "--repo", REPO, "--apply"], "2026-10-27T19:30:00Z")
    assert code == 0 and gh.posts() == []


# --- refusals and failed reads ----------------------------------------------

def test_a_refused_cancel_prints_its_line_carries_on_and_exits_one(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    second, second_jobs = finished_incident(gh.fx)
    second = dict(second, id=RUN_ID + 1)
    watch_running(gh, run, jobs)
    gh.runs[second["id"]] = second
    gh.jobs[(second["id"], 1)] = second_jobs
    gh.watched = [run, second]
    gh.refuse.append(rf"/runs/{RUN_ID}/cancel$")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], "2026-10-27T13:30:00Z")
    assert code == 1
    assert any(x.startswith(f"hung-job: refused run={RUN_ID} ") and "cancel" in x
               for x in lines)
    # The other run was still served.
    assert f"repos/{REPO}/actions/runs/{RUN_ID + 1}/rerun-failed-jobs" in gh.posts()
    assert f"repos/{REPO}/actions/runs/{RUN_ID}/rerun-failed-jobs" not in gh.posts()


def test_a_refused_re_run_prints_its_line_and_exits_one(gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.refuse.append(r"/rerun-failed-jobs$")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 1
    assert any(x.startswith(f"hung-job: refused run={RUN_ID} ") and "re-run" in x
               for x in lines)


def test_a_refused_re_run_after_a_cancel_exits_one(gh, monkeypatch, capsys):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.refuse.append(r"/rerun-failed-jobs$")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 1
    assert any(x.startswith(f"hung-job: canceled run={RUN_ID} ") and "refused" in x
               for x in lines)


@pytest.mark.parametrize("failing", [
    r"/attempts/\d+/jobs\?",         # the run's jobs
    r"workflows/tests\.yml/runs\?.*status=success",  # the history listing
    r"/runs/\d+/jobs\?",             # a history run's jobs
])
def test_a_failed_read_on_a_running_run_is_unknown_and_acts_on_nothing(
        gh, monkeypatch, capsys, failing):
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.fail.append(failing)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0 and gh.posts() == []
    assert any(x.startswith(f"hung-job: unknown run={RUN_ID} ") for x in lines)


def test_a_failed_log_read_on_a_finished_run_is_unknown_and_acts_on_nothing(
        gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    gh.fail.append(r"/logs$")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []
    assert any(x.startswith(f"hung-job: unknown run={RUN_ID} ") for x in lines)


def test_a_failed_run_list_is_unknown_and_exits_zero(gh, monkeypatch, capsys):
    gh.fail.append(r"workflows/tests\.yml/runs")
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0 and gh.posts() == []
    assert any(x.startswith("hung-job: unknown run=") for x in lines)


def test_the_lines_go_to_the_step_summary(gh, monkeypatch, capsys, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    _code, lines = run_main(monkeypatch, capsys, ["watch", "--repo", REPO], NOW_RUNNING)
    written = summary.read_text(encoding="utf-8")
    assert lines and all(line in written for line in lines)


# --- the log reading is canceled_check's ------------------------------------

def test_the_log_reading_is_imported_from_canceled_check_not_copied():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assigned = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    assigned.add(name.id)
    assert not assigned & {"CANCEL_LINE", "_STAMP", "_ANSI"}, assigned
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("The operation was canceled" in s for s in literals)
    assert not any(r"\d{4}-\d{2}-\d{2}T" in s for s in literals)
    used = {(n.value.id, n.attr) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    assert {("canceled_check", "CANCEL_LINE"), ("canceled_check", "_STAMP"),
            ("canceled_check", "_ANSI")} <= used
    assert canceled_check.CANCEL_LINE == "##[error]The operation was canceled."


def test_the_module_docstring_says_a_running_jobs_log_cannot_be_read():
    doc = (ast.get_docstring(ast.parse(SOURCE.read_text(encoding="utf-8"))) or "").lower()
    assert "log" in doc and "only after the job ends" in doc
    assert "step" in doc


def test_timestamps_are_pacific():
    assert hung_job.pt(datetime(2026, 10, 27, 12, 20, 53, tzinfo=timezone.utc)) == "5:20:53 AM PT"
    assert hung_job.pt(datetime(2026, 10, 27, 17, 53, 47, tzinfo=timezone.utc)) == "10:53:47 AM PT"
    assert hung_job.pt(datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)
                       + timedelta(seconds=0)) == "12:00:00 PM PT"


# --- never back onto the service it hung on (DRE-8817) ------------------------
#
# Before `watch` asks for the re-run of a run on attempt 1 it asks
# `runner_service.gate` with the jobs it judged hung, column `ci` and the
# `CI_RUNS_ON` value channel-watch.yml hands it. Held, it prints a `held` line
# and asks for nothing; a running hung run is still canceled. A later pass
# inside RECENT_HOURS re-runs it once the column has moved.

ON_HOSTED = '["ubuntu-latest"]'


def test_a_finished_hung_run_whose_column_still_names_its_service_is_held(
        gh, monkeypatch, capsys):
    monkeypatch.setenv("CI_RUNS_ON", ON_HOSTED)
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []
    line = next(x for x in lines if x.startswith("hung-job: held "))
    assert line.startswith(f'hung-job: held run={RUN_ID} job="{SHARD}" — finished cancelled')
    for part in (FIXTURE_RUNNER, "github-hosted", "`ci`", ON_HOSTED):
        assert part in line, part


def test_a_later_pass_after_the_column_moved_re_runs(gh, monkeypatch, capsys):
    monkeypatch.setenv("CI_RUNS_ON", ON_HOSTED)
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    run_main(monkeypatch, capsys, ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert gh.posts() == []
    monkeypatch.setenv("CI_RUNS_ON", '["runs-on"]')
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], "2026-10-27T15:34:00Z")
    assert code == 0
    assert gh.posts() == [f"repos/{REPO}/actions/runs/{RUN_ID}/rerun-failed-jobs"]
    assert any(x.startswith("hung-job: rerun ") for x in lines)


def test_a_running_hung_run_is_still_canceled_but_not_re_run_when_held(
        gh, monkeypatch, capsys):
    monkeypatch.setenv("CI_RUNS_ON", ON_HOSTED)
    run, jobs = running_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_RUNNING)
    assert code == 0
    assert gh.posts() == [f"repos/{REPO}/actions/runs/{RUN_ID}/cancel"]
    line = next(x for x in lines if x.startswith("hung-job: canceled "))
    assert "held" in line and FIXTURE_RUNNER in line and "github-hosted" in line


def test_an_unknown_runner_holds_the_re_run(gh, monkeypatch, capsys):
    run, jobs = finished_incident(gh.fx)
    shard(jobs)["runner_name"] = None
    watch_running(gh, run, jobs)
    code, lines = run_main(monkeypatch, capsys,
                           ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []
    line = next(x for x in lines if x.startswith("hung-job: held "))
    assert "unknown" in line


def test_an_unset_ci_column_holds_the_re_run(gh, monkeypatch, capsys):
    monkeypatch.delenv("CI_RUNS_ON", raising=False)
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    code, _lines = run_main(monkeypatch, capsys,
                            ["watch", "--repo", REPO, "--apply"], NOW_FINISHED)
    assert code == 0 and gh.posts() == []


def test_without_apply_a_held_re_run_is_printed_as_would_hold(gh, monkeypatch, capsys):
    monkeypatch.setenv("CI_RUNS_ON", ON_HOSTED)
    run, jobs = finished_incident(gh.fx)
    watch_running(gh, run, jobs)
    _code, lines = run_main(monkeypatch, capsys, ["watch", "--repo", REPO], NOW_FINISHED)
    assert gh.posts() == []
    assert any(x.startswith(f"hung-job: would-hold run={RUN_ID} ") for x in lines)


def test_the_held_decision_is_runner_services():
    source = SOURCE.read_text(encoding="utf-8")
    assert "runner_service.gate(" in source
    assert "import runner_service" in source
