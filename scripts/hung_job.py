#!/usr/bin/env python3
"""A `Pipeline Tests` job on main that runs past its usual time with no
progress is canceled and re-run once (DRE-8652). Stdlib only; the reads and
the writes are `gh api`, with the token in `GH_TOKEN`.

THE INCIDENT (2026-10-27, PT). Pipeline Tests run 40502923604, a push to main
at head 69b518ceb, started at 5:20 AM. Its job `scripts unit tests (walk
tests/test_console_project_model_keeps_the_suite_green.py shard 5 of 8)`
printed its last line at 5:20:53 AM and was canceled by its own 20-minute
`timeout-minutes` at 5:40:43 AM; in the 27 push runs on main before it, that
shard always finished in under 8 minutes. The run ended `cancelled`, Promote
Channel refused to move `stable` (`unit-suite-unproven`), and Red-Main Repair
skipped it because the run was not a `failure`. Nobody acted until 10:47 AM,
when a bot re-ran the failed jobs; attempt 2 passed in six minutes and
Promote Channel moved `stable` at 10:58 AM. The re-run was all it needed, and
it came five hours late.

WHAT IS WATCHED. Push runs of `Pipeline Tests` on `main`, and nothing else:
no pull-request run, no nightly `schedule` run, no other workflow. The
Integration Harness holds the sandbox seat, and canceling it mid-scenario is
a different question.

A JOB'S USUAL TIME is the longest duration among that same job's last
`HISTORY_RUNS` runs on main that concluded `success` — same workflow, exact
same job name — read from the workflow's `success` runs on main created
before the run being judged. With fewer than `MIN_HISTORY` of them the job has
no usual time and is never canceled; its own `timeout-minutes` still applies.
Its LIMIT is the larger of `FACTOR` × its usual time and its usual time plus
`MARGIN_MINUTES`.

WHEN A JOB COUNTS AS HUNG.

  * Running: it has run past its limit, and no step of the job has started or
    completed since the job passed its usual time. GitHub serves a job's log
    only after the job ends, so step progress is the only output this module
    can read while a job is running.
  * Finished: it concluded `cancelled`; it ran at least its limit or its
    annotations say its own time limit stopped it; and its log has no line
    timestamped later than its start plus its usual time before the cancel
    line. The log is read the way `canceled_check` reads it — its
    `CANCEL_LINE`, `_STAMP` and `_ANSI`, imported, never copied.

WHAT `watch` DOES, in one pass:

  * A running run with a hung job: cancel the run, wait up to
    `CANCEL_WAIT_SECONDS` for it to finish, force-cancel it if it has not and
    wait up to `FORCE_WAIT_SECONDS` more. On attempt 1, once a read of the run
    says `completed`, ask for a re-run of its failed and canceled jobs — GitHub
    refuses to re-run a run that has not finished. On attempt 2 or later,
    cancel only: the one re-run is spent.
  * A run on attempt 1 that finished `cancelled` in the last `RECENT_HOURS`
    and holds a job hung on its finished record: ask for the re-run once.
    That covers a job its own `timeout-minutes` stopped before any pass saw
    it, and a pass that died between its cancel and its re-run.
  * A run a person canceled while its jobs were still printing is left alone.

NEVER BACK ONTO THE SERVICE IT HUNG ON (DRE-8817). Before a re-run of a run
on attempt 1, `runner_service.gate` is asked with the jobs judged hung,
column `ci` — every job in tests.yml reads it — and the `CI_RUNS_ON` value
channel-watch.yml hands this module. While that column still names the
service a hung job ran on, or either is unknown, nothing is re-run and a
`held` line says so, naming the job, its runner, its service, the column and
its value. A running hung run is still canceled: a cancel is not a re-run. A
later pass inside `RECENT_HOURS` of the run's finish asks again and re-runs it
once the column has moved; after that the run leaves the watch as before.

"Once" is read off the run's own `run_attempt`, as `canceled_check` and
`runner_lost` read it: `rerun-failed-jobs` moves the run to attempt 2, and
attempt 2 is never re-run. No receipt is stored anywhere. Two passes never
overlap, because the workflow that runs this serializes them.

A read that fails (the run list, a job list, the history or a log) prints an
`unknown` line and acts on nothing in that run. An act GitHub refuses (a
cancel or a re-run) prints its line, the pass carries on with the other runs,
and the exit is 1 at the end. Without `--apply` every act is printed as
`would-…` and nothing is written.

THE IDENTITY (standards/vendor-boundaries.md Q1). The workflow hands this
module the bureau App's token, never `GITHUB_TOKEN`: a re-run asked for with
`GITHUB_TOKEN` starts no workflow when it finishes, so Promote Channel would
never wake. The 10-27 re-run was asked for by `agent-bureau-bot[bot]`, and its
completion woke Promote Channel run 40554972524.

`replay --run <id>` reads a finished run's attempt-1 jobs and prints what a
pass would have done, and when — `would-cancel-at` is the first pass on the
workflow's cron minutes (`PASS_MINUTES`) at or after the job's start plus its
limit. It never writes.

    python3 scripts/hung_job.py watch --repo <owner/name> [--apply]
    python3 scripts/hung_job.py replay --repo <owner/name> --run <id>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — fixed argv, the gh CLI
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import canceled_check
import merge_gate
import runner_service
from red_main_repair import failure_reason

#: Every line this module prints opens with this.
TAG = "hung-job:"

#: The one workflow watched, by its file and its name, and where.
WORKFLOW = "Pipeline Tests"
WORKFLOW_FILE = "tests.yml"
BRANCH = "main"
EVENT = "push"

#: A job's usual time: the longest of its last HISTORY_RUNS green runs on
#: main, and none with fewer than MIN_HISTORY of them.
HISTORY_RUNS = 10
MIN_HISTORY = 5
#: Its limit: the larger of FACTOR × the usual time and the usual time plus
#: MARGIN_MINUTES.
FACTOR = 1.5
MARGIN_MINUTES = 5

#: The workflow's cron minutes (`4-59/10`); the replay's `would-cancel-at`
#: reads them.
PASS_MINUTES = (4, 14, 24, 34, 44, 54)

#: How far back a finished `cancelled` run is still re-run.
RECENT_HOURS = 6

#: How long a pass waits on a cancel, and on a force-cancel after it, and how
#: often it reads the run meanwhile.
CANCEL_WAIT_SECONDS = 300
FORCE_WAIT_SECONDS = 300
POLL_SECONDS = 15

#: How many green runs one history listing reads, and how many runs one
#: watch listing reads.
HISTORY_PAGE = 30
WATCH_PAGE = 100
#: The most pages one job list is read across.
JOB_PAGES = 5

#: How long one `gh api` call may take before it counts as failed.
GH_TIMEOUT_SECONDS = 60

CANCELLED = canceled_check.CANCELLED
_PT = ZoneInfo("America/Los_Angeles")


class Unread(Exception):
    """A read that failed; its text says what could not be read."""


class Refused(Exception):
    """An act GitHub refused; its text says why."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# --- reading times -----------------------------------------------------------

def _when(raw) -> datetime | None:
    """An API timestamp as an aware instant, or None."""
    try:
        at = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def pt(at: datetime | None) -> str:
    """An instant as "5:20:53 AM PT"."""
    if at is None:
        return "unknown"
    return at.astimezone(_PT).strftime("%I:%M:%S %p").lstrip("0") + " PT"


def _minutes(value: float) -> str:
    return f"{value:.1f}"


def _duration(job: dict) -> float | None:
    """A finished job's minutes, start to finish, or None."""
    start, end = _when(job.get("started_at")), _when(job.get("completed_at"))
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() / 60


def next_pass(at: datetime) -> datetime:
    """The first pass on `PASS_MINUTES` at or after `at`."""
    hour = at.replace(minute=0, second=0, microsecond=0)
    for _ in range(26):
        for minute in PASS_MINUTES:
            candidate = hour + timedelta(minutes=minute)
            if candidate >= at:
                return candidate
        hour += timedelta(hours=1)
    raise ValueError(f"no pass after {at}")  # unreachable with PASS_MINUTES set


# --- the usual time ----------------------------------------------------------

@dataclass(frozen=True)
class Usual:
    """A job's usual minutes (None when it has no usual time) and the green
    runs it was read from, newest first."""

    minutes: float | None
    runs: tuple = ()

    @property
    def limit(self) -> float | None:
        return None if self.minutes is None else limit_minutes(self.minutes)


def limit_minutes(usual: float) -> float:
    """The larger of FACTOR × `usual` and `usual` plus MARGIN_MINUTES."""
    return max(FACTOR * usual, usual + MARGIN_MINUTES)


def usual_time(name: str, history: Iterable) -> Usual:
    """The usual time of the job called exactly `name`, off `history` — the
    green runs newest first, each as (run id, its jobs). Iterated lazily, so
    runs past the HISTORY_RUNS-th match are never read."""
    found: list = []
    for run_id, jobs in history:
        for job in jobs or ():
            if (isinstance(job, dict) and job.get("name") == name
                    and job.get("conclusion") == "success"):
                minutes = _duration(job)
                if minutes is not None:
                    found.append((run_id, minutes))
                    break
        if len(found) >= HISTORY_RUNS:
            break
    runs = tuple(run_id for run_id, _m in found)
    if len(found) < MIN_HISTORY:
        return Usual(None, runs)
    return Usual(max(m for _r, m in found), runs)


# --- the verdicts ------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """Whether a job is hung, why not when it is not, when it went quiet, and
    how long it had run."""

    hung: bool
    why: str = ""
    quiet_since: datetime | None = None
    elapsed: float = 0.0


def _step_moments(job: dict) -> list:
    moments = []
    for step in job.get("steps") or ():
        if not isinstance(step, dict):
            continue
        for key in ("started_at", "completed_at"):
            at = _when(step.get(key))
            if at is not None:
                moments.append(at)
    return moments


def judge_running(job: dict, usual: Usual, now: datetime) -> Verdict:
    """A running job is hung when it has run past its limit and no step has
    started or completed since it passed its usual time."""
    start = _when(job.get("started_at"))
    if start is None:
        return Verdict(False, "it has no start time")
    elapsed = (now - start).total_seconds() / 60
    if usual.minutes is None:
        return Verdict(False, "it has no usual time", elapsed=elapsed)
    newest = max(_step_moments(job), default=start)
    newest = max(newest, start)
    if elapsed <= usual.limit:
        return Verdict(False, "inside its limit", newest, elapsed)
    if newest > start + timedelta(minutes=usual.minutes):
        return Verdict(False, f"a step moved at {pt(newest)}", newest, elapsed)
    return Verdict(True, "", newest, elapsed)


def quiet_since(log_text) -> tuple:
    """(the newest timestamp before the first cancel line, whether there is a
    cancel line), read the way `canceled_check.failed_tests` reads a log."""
    newest = None
    for raw in str(log_text or "").splitlines():
        line = canceled_check._ANSI.sub("", raw.lstrip("﻿").rstrip("\r"))
        stamp = canceled_check._STAMP.match(line)
        body = canceled_check._STAMP.sub("", line, count=1)
        if body.startswith(canceled_check.CANCEL_LINE):
            return newest, True
        if stamp:
            at = _stamp(stamp.group(0))
            if at is not None and (newest is None or at > newest):
                newest = at
    return newest, False


def _stamp(text: str) -> datetime | None:
    """A runner timestamp ("2026-10-27T12:20:53.2169436Z ") as an instant; the
    runner writes seven fractional digits, and six are kept."""
    text = text.strip().removesuffix("Z")
    whole, _dot, fraction = text.partition(".")
    return _when(f"{whole}.{(fraction + '000000')[:6]}+00:00" if fraction
                 else f"{whole}+00:00")


def judge_finished(job: dict, usual: Usual, annotations: Callable,
                   log: Callable) -> Verdict:
    """A finished job is hung when it concluded `cancelled`, ran at least its
    limit or was stopped by its own time limit, and printed nothing after its
    start plus its usual time before the cancel line. `annotations` and `log`
    are read only when the answer needs them."""
    conclusion = job.get("conclusion") or "no conclusion"
    if conclusion != CANCELLED:
        return Verdict(False, f"it concluded {conclusion}")
    start = _when(job.get("started_at"))
    ran = _duration(job)
    if start is None or ran is None:
        return Verdict(False, "it has no start or finish time")
    if usual.minutes is None:
        return Verdict(False, "it has no usual time", elapsed=ran)
    if ran < usual.limit and canceled_check.limit(annotations()) is None:
        return Verdict(False, f"it was canceled after {_minutes(ran)}m, short of "
                       f"its {_minutes(usual.limit)}m limit", elapsed=ran)
    newest, canceled = quiet_since(log())
    if not canceled:
        return Verdict(False, "its log has no cancel line", elapsed=ran)
    newest = start if newest is None or newest < start else newest
    if newest > start + timedelta(minutes=usual.minutes):
        return Verdict(False, f"it was still printing at {pt(newest)}", newest, ran)
    return Verdict(True, "", newest, ran)


def would_cancel_at(job: dict, usual: Usual) -> datetime | None:
    """The pass that would have canceled `job` while it ran: the first one at
    or after its start plus its limit, if the job was still running then and
    no step had moved since its usual time. None when no pass would have."""
    start = _when(job.get("started_at"))
    if start is None or usual.minutes is None:
        return None
    moment = next_pass(start + timedelta(minutes=usual.limit))
    stopped = _when(job.get("completed_at"))
    if stopped is not None and moment >= stopped:
        return None
    since = start + timedelta(minutes=usual.minutes)
    if any(since < at <= moment for at in _step_moments(job)):
        return None
    return moment


# --- GitHub ------------------------------------------------------------------

def _gh(args: list) -> tuple:
    """`gh <args>` as (exit code, stdout, stderr); never raises."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True,
                              timeout=GH_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"gh could not run: {type(exc).__name__}"
    return done.returncode, done.stdout or "", done.stderr or ""


class GitHub:
    """The Actions API for one repository. A read raises `Unread`; an act
    raises `Refused`. Job lists are kept for the pass, so two judgements
    against the same history read each green run's jobs once."""

    def __init__(self, repo: str):
        self.repo = repo
        self._jobs: dict = {}
        self._listings: dict = {}

    def _get(self, path: str, what: str):
        code, out, err = _gh(["api", path])
        if code != 0:
            raise Unread(f"{what} could not be read ({failure_reason(out, err)})")
        try:
            return json.loads(out)
        except ValueError:
            raise Unread(f"{what} could not be read (the answer was not JSON)") from None

    def _post(self, path: str, what: str) -> None:
        code, out, err = _gh(["api", "-X", "POST", path])
        if code != 0:
            raise Refused(f"{what} was refused ({failure_reason(out, err)})")

    def _runs(self) -> str:
        return f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs"

    def watched(self) -> list:
        body = self._get(f"{self._runs()}?branch={BRANCH}&event={EVENT}"
                         f"&per_page={WATCH_PAGE}", "the run list")
        runs = body.get("workflow_runs") if isinstance(body, dict) else None
        if not isinstance(runs, list):
            raise Unread("the run list could not be read (no workflow_runs)")
        return [r for r in runs if isinstance(r, dict) and is_watched(r)]

    def run(self, run_id) -> dict:
        body = self._get(f"repos/{self.repo}/actions/runs/{run_id}", f"run {run_id}")
        if not isinstance(body, dict):
            raise Unread(f"run {run_id} could not be read (not a run record)")
        return body

    def jobs(self, run_id, attempt=None) -> list:
        """A run's jobs on `attempt`, or on its latest attempt when None."""
        key = (run_id, attempt)
        if key in self._jobs:
            return self._jobs[key]
        base = (f"repos/{self.repo}/actions/runs/{run_id}/attempts/{attempt}/jobs"
                if attempt is not None else
                f"repos/{self.repo}/actions/runs/{run_id}/jobs")
        what = (f"the jobs of attempt {attempt}" if attempt is not None
                else f"the jobs of green run {run_id}")
        jobs: list = []
        for page in range(1, JOB_PAGES + 1):
            path = base + "?per_page=100" + (f"&page={page}" if page > 1 else "")
            body = self._get(path, what)
            listed = body.get("jobs") if isinstance(body, dict) else None
            if not isinstance(listed, list):
                raise Unread(f"{what} could not be read (no jobs)")
            jobs.extend(j for j in listed if isinstance(j, dict))
            if not listed or len(jobs) >= int(body.get("total_count") or 0):
                break
        self._jobs[key] = jobs
        return jobs

    def history(self, before: str):
        """The green runs on main created before `before`, newest first, each
        as (run id, its jobs) — a generator, so a run's jobs are read only
        when a judgement reaches it."""
        if before not in self._listings:
            body = self._get(
                f"{self._runs()}?branch={BRANCH}&status=success"
                f"&per_page={HISTORY_PAGE}&created={quote('<' + before, safe='')}",
                "the history")
            runs = body.get("workflow_runs") if isinstance(body, dict) else None
            if not isinstance(runs, list):
                raise Unread("the history could not be read (no workflow_runs)")
            self._listings[before] = [r for r in runs if isinstance(r, dict)]
        for run in self._listings[before]:
            yield run.get("id"), self.jobs(run.get("id"))

    def annotations(self, job_id) -> list:
        body = self._get(f"repos/{self.repo}/check-runs/{job_id}/annotations",
                         f"the annotations of job {job_id}")
        if not isinstance(body, list):
            raise Unread(f"the annotations of job {job_id} could not be read")
        return body

    def log(self, job_id) -> str:
        path = f"repos/{self.repo}/actions/jobs/{job_id}/logs"
        code, out, err = _gh(["api", "--allow-escape-sequences", path])
        if code != 0 or not out:
            reason = failure_reason(out, err) if code != 0 else "it was empty"
            raise Unread(f"the log of job {job_id} could not be read ({reason})")
        return out

    def cancel(self, run_id) -> None:
        self._post(f"repos/{self.repo}/actions/runs/{run_id}/cancel", "the cancel")

    def force_cancel(self, run_id) -> None:
        self._post(f"repos/{self.repo}/actions/runs/{run_id}/force-cancel",
                   "the force-cancel")

    def rerun(self, run_id) -> None:
        self._post(f"repos/{self.repo}/actions/runs/{run_id}/rerun-failed-jobs",
                   "the re-run")


def is_watched(run: dict) -> bool:
    """A push run of Pipeline Tests on main."""
    return (run.get("name") == WORKFLOW and run.get("event") == EVENT
            and run.get("head_branch") == BRANCH)


def _usual_of(gh: GitHub, run: dict, name: str) -> Usual:
    return usual_time(name, gh.history(str(run.get("created_at") or "")))


# --- the lines ---------------------------------------------------------------

def _job(job: dict) -> str:
    return f'job="{job.get("name") or "job " + str(job.get("id"))}"'


def _no_history(run_id, job: dict, usual: Usual) -> str:
    return (f"{TAG} no-history run={run_id} {_job(job)} from={len(usual.runs)} "
            f"runs (needs {MIN_HISTORY})")


def _canceled_line(verb: str, run: dict, job: dict, usual: Usual,
                   verdict: Verdict, tail: str) -> str:
    return (f"{TAG} {verb} run={run.get('id')} attempt={_attempt(run)} {_job(job)} "
            f"elapsed={_minutes(verdict.elapsed)}m usual={_minutes(usual.minutes)}m "
            f"from={len(usual.runs)} runs quiet-since={pt(verdict.quiet_since)} {tail}")


def _rerun_line(verb: str, run: dict, job: dict, usual: Usual, verdict: Verdict,
                requested: str) -> str:
    return (f"{TAG} {verb} run={run.get('id')} {_job(job)} — finished cancelled "
            f"after {_minutes(verdict.elapsed)}m against a usual "
            f"{_minutes(usual.minutes)}m, quiet since {pt(verdict.quiet_since)}; "
            f"attempt 2 {requested}")


def _held(hung, ci_runs_on: str) -> runner_service.Gate:
    """Whether the re-run of `hung`'s run would go back onto the service a
    hung job ran on (DRE-8817): column `ci`, every hung job a hang."""
    return runner_service.gate([(job, runner_service.HANG) for job, _u, _v in hung],
                               "ci", {"ci": ci_runs_on})


def _attempt(run: dict) -> int:
    try:
        return int(run.get("run_attempt") or 1)
    except (TypeError, ValueError):
        return 1


# --- the watch ---------------------------------------------------------------

def _wait(gh: GitHub, run_id, seconds: int) -> bool | None:
    """Whether the run reads `completed` within `seconds`; None when a read
    of it failed."""
    for _ in range(max(1, seconds // POLL_SECONDS)):
        _sleep(POLL_SECONDS)
        try:
            if gh.run(run_id).get("status") == "completed":
                return True
        except Unread:
            return None
    return False


def _tend_running(gh: GitHub, run: dict, now: datetime, apply: bool, say,
                  ci_runs_on: str = "") -> bool:
    """One running run. Returns True when an act was refused."""
    run_id, attempt = run.get("id"), _attempt(run)
    running = [j for j in gh.jobs(run_id, attempt)
               if j.get("status") == "in_progress"]
    judged = [(job, _usual_of(gh, run, str(job.get("name") or "")))
              for job in running]
    hung = []
    for job, usual in judged:
        if usual.minutes is None:
            say(_no_history(run_id, job, usual))
            continue
        verdict = judge_running(job, usual, now)
        if verdict.hung:
            hung.append((job, usual, verdict))
            continue
        say(f"{TAG} watching run={run_id} attempt={attempt} {_job(job)} "
            f"elapsed={_minutes(verdict.elapsed)}m usual={_minutes(usual.minutes)}m "
            f"limit={_minutes(usual.limit)}m from={len(usual.runs)} runs")
    if not hung:
        return False
    spent = "— no re-run: already re-run once"
    held = _held(hung, ci_runs_on) if attempt <= 1 else None
    holding = (f"— no re-run: held, {held.why}; a later pass re-runs it once the "
               "`ci` column names another service") if held and not held.allow else ""
    if not apply:
        tail = spent if attempt > 1 else (
            f"— would hold the re-run: {held.why}" if holding else "— would request a re-run")
        for job, usual, verdict in hung:
            say(_canceled_line("would-cancel", run, job, usual, verdict, tail))
        return False
    try:
        gh.cancel(run_id)
    except Refused as e:
        for job, _usual, _verdict in hung:
            say(f"{TAG} refused run={run_id} cancel {_job(job)} — {e}")
        return True
    refused = False
    done = _wait(gh, run_id, CANCEL_WAIT_SECONDS)
    if done is False:
        try:
            gh.force_cancel(run_id)
        except Refused as e:
            for job, _usual, _verdict in hung:
                say(f"{TAG} refused run={run_id} force-cancel {_job(job)} — {e}")
            return True
        done = _wait(gh, run_id, FORCE_WAIT_SECONDS)
    if attempt > 1:
        tail = spent
    elif holding:
        tail = holding
    elif done is None:
        tail = ("— no re-run yet: whether the run finished could not be read; "
                "the next pass re-runs it")
    elif not done:
        tail = ("— no re-run yet: the run had not finished "
                f"{(CANCEL_WAIT_SECONDS + FORCE_WAIT_SECONDS) // 60} minutes after "
                "its cancel; the next pass re-runs it")
    else:
        try:
            gh.rerun(run_id)
            tail = "— re-run requested"
        except Refused as e:
            tail, refused = f"— re-run refused: {e}", True
    for job, usual, verdict in hung:
        say(_canceled_line("canceled", run, job, usual, verdict, tail))
    return refused


def _recently_cancelled(run: dict, now: datetime) -> bool:
    finished = _when(run.get("updated_at"))
    return (run.get("status") == "completed" and run.get("conclusion") == CANCELLED
            and _attempt(run) == 1 and finished is not None
            and now - finished <= timedelta(hours=RECENT_HOURS))


def _judge_canceled(gh: GitHub, run: dict, jobs) -> list:
    """(job, usual, verdict) for each canceled job of a finished run."""
    judged = []
    for job in jobs:
        if job.get("conclusion") != CANCELLED:
            continue
        usual = _usual_of(gh, run, str(job.get("name") or ""))
        verdict = judge_finished(job, usual, lambda j=job: gh.annotations(j.get("id")),
                                 lambda j=job: gh.log(j.get("id")))
        judged.append((job, usual, verdict))
    return judged


def _tend_finished(gh: GitHub, run: dict, apply: bool, say,
                   ci_runs_on: str = "") -> bool:
    """One attempt-1 run that finished `cancelled`. True when refused."""
    run_id = run.get("id")
    judged = _judge_canceled(gh, run, gh.jobs(run_id, 1))
    hung = []
    for job, usual, verdict in judged:
        if usual.minutes is None:
            say(_no_history(run_id, job, usual))
        elif verdict.hung:
            hung.append((job, usual, verdict))
        else:
            say(f"{TAG} left-alone run={run_id} {_job(job)} — {verdict.why}")
    if not hung:
        return False
    held = _held(hung, ci_runs_on)
    if not held.allow:
        verb = "held" if apply else "would-hold"
        for job, usual, verdict in hung:
            say(_rerun_line(verb, run, job, usual, verdict,
                            f"not requested — {held.why}; a later pass re-runs it "
                            "once the `ci` column names another service"))
        return False
    if not apply:
        for job, usual, verdict in hung:
            say(_rerun_line("would-rerun", run, job, usual, verdict, "would be requested"))
        return False
    try:
        gh.rerun(run_id)
    except Refused as e:
        for job, _usual, _verdict in hung:
            say(f"{TAG} refused run={run_id} re-run {_job(job)} — {e}")
        return True
    for job, usual, verdict in hung:
        say(_rerun_line("rerun", run, job, usual, verdict, "requested"))
    return False


def watch(gh: GitHub, now: datetime, apply: bool, say, ci_runs_on: str | None = None) -> int:
    """One pass over the watched runs. 0, or 1 when an act was refused.
    `ci_runs_on` is the `ci` column's value, read from `CI_RUNS_ON` when not
    given (DRE-8817)."""
    if ci_runs_on is None:
        ci_runs_on = os.environ.get(runner_service.ENV["ci"], "")
    try:
        runs = gh.watched()
    except Unread as e:
        say(f"{TAG} unknown run=- {e} — nothing acted on this pass")
        return 0
    refused = False
    for run in runs:
        try:
            if run.get("status") != "completed":
                refused |= _tend_running(gh, run, now, apply, say, ci_runs_on)
            elif _recently_cancelled(run, now):
                refused |= _tend_finished(gh, run, apply, say, ci_runs_on)
        except Unread as e:
            say(f"{TAG} unknown run={run.get('id')} {e} — nothing acted on in this run")
    return 1 if refused else 0


# --- the replay --------------------------------------------------------------

def _replay_line(run_id, job: dict, usual: Usual, verdict: Verdict) -> str:
    head = (f"{TAG} replay run={run_id} {_job(job)} usual={_minutes(usual.minutes)}m "
            f"from={len(usual.runs)} runs ({', '.join(str(r) for r in usual.runs)}) "
            f"limit={_minutes(usual.limit)}m")
    if not verdict.hung:
        return f"{head} — not hung: {verdict.why}"
    moment = would_cancel_at(job, usual)
    if moment is not None:
        when = f"would-cancel-at={pt(moment)}"
    else:
        stopped = _when(job.get("completed_at"))
        when = ("would-cancel-at=none would-rerun-at="
                f"{pt(next_pass(stopped)) if stopped else 'unknown'}")
    return (f"{head} quiet-since={pt(verdict.quiet_since)} {when} "
            f"stopped-at={pt(_when(job.get('completed_at')))} — re-run once")


def replay(gh: GitHub, run_id, say) -> int:
    """What a pass would have done about a finished run. Never writes."""
    try:
        run = gh.run(run_id)
        if run.get("status") != "completed":
            say(f"{TAG} unknown run={run_id} is still {run.get('status') or 'going'} "
                "— the replay reads a finished run")
            return 1
        rows = []
        for job in gh.jobs(run_id, 1):
            if (job.get("conclusion") or "") in merge_gate.GREEN_CONCLUSIONS:
                continue
            usual = _usual_of(gh, run, str(job.get("name") or ""))
            verdict = judge_finished(
                job, usual, lambda j=job: gh.annotations(j.get("id")),
                lambda j=job: gh.log(j.get("id")))
            rows.append((job, usual, verdict))
        later = {n: gh.jobs(run_id, n) for n in range(2, _attempt(run) + 1)}
    except Unread as e:
        say(f"{TAG} unknown run={run_id} {e} — nothing replayed")
        return 1
    for job, usual, verdict in rows:
        if usual.minutes is None:
            say(f"{_no_history(run_id, job, usual)} — never canceled")
            continue
        say(_replay_line(run_id, job, usual, verdict))
        if not verdict.hung:
            continue
        for n, jobs in later.items():
            again = next((j for j in jobs if j.get("name") == job.get("name")), None)
            if again is None:
                say(f"{TAG} replay run={run_id} attempt {n} has no {_job(job)}")
            else:
                say(f"{TAG} replay run={run_id} attempt {n} "
                    f"{again.get('conclusion') or again.get('status')} at "
                    f"{pt(_when(again.get('completed_at')))}")
    return 0


# --- the command line --------------------------------------------------------

def _say(line: str) -> None:
    print(line, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(line + "\n\n")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="one pass over the watched runs")
    w.add_argument("--repo", required=True)
    w.add_argument("--apply", action="store_true",
                   help="cancel and re-run; without it every act is printed as would-…")
    r = sub.add_parser("replay", help="what a pass would have done about a run")
    r.add_argument("--repo", required=True)
    r.add_argument("--run", required=True, type=int)
    args = parser.parse_args(argv)
    gh = GitHub(args.repo)
    if args.command == "watch":
        return watch(gh, _now(), args.apply, _say)
    return replay(gh, args.run, _say)


if __name__ == "__main__":
    sys.exit(main())
