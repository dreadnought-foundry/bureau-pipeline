"""Say why the `stable` channel is held, and what it needs next (DRE-8653).

`stable` was held 32 times between 2026-10-01 and 10-31, for 78 hours in all.
Two holds ran 14 to 16 hours overnight, and one needed the CEO to say "force
it" (10-10). The console's channel-health monitor says the channel is quiet,
and nothing says why or what it needs. On 10-27 the answer was "a re-run of one
canceled test job", and it stood for five hours.

This module answers both, once a pass. It reads whether the channel is held
and since when, writes a diagnosis that names the failing (or stuck) job and what is
needed next — `NEXT_STEPS`, a re-run, a fix or a person — and raises it as one
console alert through the notice door (`pipeline_notice.py`) at 2 hours and
before the 06:30 PT briefing. It resolves the alert on the pass that finds no
hold. The workflow that runs it every 10 minutes is its own card, and so is the
briefing's ordering; both read the contract below.

IS IT HELD, AND SINCE WHEN
--------------------------
Read the `stable` tag and `main`'s head, then compare `stable...main`. The
channel is held when `stable` does not carry `main`'s head. The hold started
when the oldest FIRST-PARENT commit on `main` that `stable` does not carry was
committed — a merge's own branch commits never landed on `main` by
themselves, and a bot branch cut the evening before would otherwise start the
hold the evening before. Its age runs from then.

These reads are the hold itself. When one fails the pass is `unknown`: it
names what it could not read and raises nothing and resolves nothing, because
an unread channel is not a held one and not a carried one either.

THE DIAGNOSIS
-------------
Four reads, made only for a held channel:

  * the newest decision Promote Channel made: the line its `decide` step
    prints, `<outcome>: <reason>`, read from the log of the newest completed
    `Promote Channel` run. The run log and not the `pipeline-channel`
    deployment, on purpose: a log needs only `actions: read`, which the
    workflow token and the proof-reader identity both hold;
  * the `CHANNEL_HOLD` value, from the environment;
  * the `Pipeline Tests` push runs and `Integration Harness` runs on `main` for
    the commits `stable` does not carry, newest first, with their jobs and
    `run_attempt`;
  * GitHub's own record of each run's status.

The failing job is the earliest-completing job that concluded `failure`,
`cancelled` or `timed_out`, in the newest commit's runs that have one. A
roll-up job that only sums other jobs (`ROLLUP_JOBS`) is passed over when one
of its parts failed before it, or with it. With none failed, a job still
running, or not yet started, is named only once the job itself has run 2
hours — from its own start, or from its attempt's start when it has not
started — never from the hold's age: a merge train keeps the hold old while
each new run is young (DRE-8778). It is named as a stuck job, never a failing
one.

What is needed next is exactly one of `NEXT_STEPS`:

  * a re-run — the failing job was canceled, timed out, or lost its runner
    (it failed with no step failed), or the stuck job's run needs canceling
    and re-running, on attempt 1;
  * a fix — a job failed at a step (a test or a harness scenario), or the
    decision is `mirror-tests-failed`;
  * a person — the run is already on attempt 2 or later, the decision is
    `channel-held` (paused by hand, the `CHANNEL_HOLD` text quoted),
    `mirror-check-blocked` or a by-hand refusal, or a diagnosis read failed
    (named).

With no failing job, the decision's own step answers (`OUTCOME_NEXT`). Every
`OUTCOME_*` of `promote_channel` maps to one step there, or is declared
`NOT_A_HOLD` — and a test enumerates them off the module, so a new outcome
fails it until somebody says which.

WHEN IT RAISES
--------------
  * At 2 hours: on the pass that first finds the hold `ALERT_AFTER_MINUTES`
    old, and once in each later hour so the diagnosis stays current —
    `(age − 120) mod 60 < --interval-minutes`.
  * Before the briefing: on every pass between `MORNING_START` and
    `MORNING_END` PT that finds a hold at least `MORNING_FLOOR_MINUTES` old.
    Under 45 minutes a commit is usually still being proven: the harness's own
    budget is 40.
  * Resolved: on every pass that finds no hold. A resolve of an alert that is
    not open changes nothing, which is the door's own rule.

THE CONTRACT
------------
    python3 scripts/channel_hold.py watch --repo <owner/name> \\
        --interval-minutes <n> [--apply] [--at <ISO time>]

`GH_TOKEN` needs only `contents: read` and `actions: read`; `CHANNEL_HOLD` is
read from the environment. Exit 0 on every pass, `unknown` included, and 1
only when the notice client reports `lost`. The notice key is `NOTICE_KEY`
(console kind `pipeline_notice:channel_hold:stable`), at WARN, with a fallback
card titled `FALLBACK_TITLE` — one title, so the fallback finds its own open
card and never files a second. One line per pass on stdout, under `TAG`:

    channel-hold: none — stable carries main <sha7>
    channel-hold: held since <h:mm AM/PM PT> age=<m>m next=<re-run|fix|person> job="<name>" run=<url> — <raise|morning-raise|quiet>
    channel-hold: unknown <what could not be read>

With `--apply` the client's `pipeline-notice:` line follows. Without it, or
with `--at`, the door is never called and a raise prints as `would-raise` (or
`would-morning-raise`); the title and body it would send go to stderr.

THE REPLAY (`--at`)
-------------------
Every read is taken as of that moment: `stable` from the newest Promote
Channel run that finished before it with a promoting decision (`… promoting
stable to <sha>`), never the tag as it stands today; `main` as the commits
committed by then; each run's attempt as it stood then
(`…/attempts/<n>/jobs` when a later attempt started after it), each job as
finished, running or not started then; and the decision of the newest Promote
Channel run that finished before it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pipeline_notice  # noqa: E402
import promote_channel as pc  # noqa: E402
from gh_read_retry import GhReadError, read as gh_read  # noqa: E402

TAG = "channel-hold:"
NOTICE_KEY = "channel_hold:stable"
SEVERITY = "WARN"

#: What a held channel needs next — exactly one, every time.
NEXT_STEPS = ("a re-run", "a fix", "a person")
RERUN, FIX, PERSON = NEXT_STEPS

#: When it raises: see WHEN IT RAISES above. Pacific time, daylight saving
#: included.
ALERT_AFTER_MINUTES = 120
MORNING_START = "06:00"
MORNING_END = "06:29"
MORNING_FLOOR_MINUTES = 45
PACIFIC = ZoneInfo("America/Los_Angeles")

#: The card the alert is filed as when the door cannot take it. One title, so
#: `linear_ops.find_open` finds the card it filed and comments there.
FALLBACK_TITLE = "bureau-pipeline: the stable channel is held"
FALLBACK = pipeline_notice.Fallback(
    title=FALLBACK_TITLE, lane=pipeline_notice.DEFAULT_LANE,
    labels=("repo:bureau-pipeline",))

#: The workflows it reads, by file, and the names they carry.
UNIT_SUITE_FILE = "tests.yml"          # pc.UNIT_SUITE, "Pipeline Tests"
HARNESS_FILE = "harness.yml"           # "Integration Harness"
PROMOTE_FILE = "promote-channel.yml"   # "Promote Channel"
#: (file, the event its runs on main are read for — None for any).
PROVING = ((UNIT_SUITE_FILE, "push"), (HARNESS_FILE, None))

#: The conclusions that name a failing job.
FAILURE = "failure"
CANCELLED = "cancelled"
TIMED_OUT = "timed_out"
FAILING = (FAILURE, CANCELLED, TIMED_OUT)
RUNNING = "in_progress"
QUEUED = "queued"

#: Jobs that only sum other jobs. A part is a job named `<roll-up> (…)`.
ROLLUP_JOBS = ("scripts unit tests",)

#: The decisions that are not a hold: the channel moved, or the run was never
#: about the channel, or the channel already carries the candidate.
NOT_A_HOLD = frozenset({*pc.PROMOTING_OUTCOMES, pc.OUTCOME_NOT_MAIN,
                        pc.OUTCOME_NOT_AHEAD})

#: Every other decision, and the step it needs when no failing job says more.
OUTCOME_NEXT = {
    pc.OUTCOME_CANCELLED: RERUN,
    pc.OUTCOME_FAILED: FIX,
    pc.OUTCOME_HELD: PERSON,
    pc.OUTCOME_BLOCKED: RERUN,
    pc.OUTCOME_QUEUE_STALL: RERUN,
    pc.OUTCOME_UNPROVEN: RERUN,
    pc.OUTCOME_FORCE_NEEDS_REASON: PERSON,
    pc.OUTCOME_FORCE_NOT_OPERATOR: PERSON,
    pc.OUTCOME_NOT_ON_TRUNK: PERSON,
    pc.OUTCOME_MIRROR_FAILED: FIX,
    pc.OUTCOME_MIRROR_BLOCKED: PERSON,
    pc.OUTCOME_UNIT_RED: FIX,
    pc.OUTCOME_UNIT_NOT_FINISHED: RERUN,
    pc.OUTCOME_UNIT_UNPROVEN: RERUN,
}
#: The decisions that are a person's whatever the jobs say.
PERSON_OUTCOMES = frozenset(o for o, step in OUTCOME_NEXT.items() if step == PERSON)
DECISION_OUTCOMES = frozenset(NOT_A_HOLD | set(OUTCOME_NEXT))

#: The decision line in a run log: GitHub's timestamp, then the outcome token
#: first, as `promote_channel.main` prints it.
DECISION_LINE = re.compile(r"^(?:\S+Z )?(?P<outcome>[a-z][a-z-]*): (?P<reason>.+)$")
PROMOTED_SHA = re.compile(
    rf"\b(?:promoting|moving|creating) {re.escape(pc.CHANNEL)} (?:to|at) "
    r"(?P<sha>[0-9a-f]{7,40})\b")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: Newer `gh` refuses a body carrying terminal escapes — a run log does —
#: without this flag, and older `gh` does not know it, so it is passed only
#: when asked for.
ESCAPES_FLAG = "--allow-escape-sequences"

#: Read bounds. Past them the module says what it did not read.
COMPARE_PAGE = 100
COMPARE_PAGES = 10
RUNS_PAGE = 20
JOBS_PAGE = 100
JOBS_PAGES = 3
COMMITS_READ = 10
PROMOTE_RUNS = 100
#: Promote Channel logs one live pass reads looking for a decision line.
DECISION_LOGS = 10


class Unreadable(RuntimeError):
    """GitHub did not answer a read. Never a value, never an empty record."""


@dataclass(frozen=True)
class Commit:
    sha: str
    committed: datetime


@dataclass(frozen=True)
class Job:
    name: str
    run_url: str
    attempt: int
    state: str               # a conclusion, or RUNNING / QUEUED
    started: datetime | None
    completed: datetime | None
    failed_step: str | None
    began: datetime | None = None   # its attempt's start, for a job not yet started


@dataclass(frozen=True)
class Decision:
    outcome: str
    reason: str
    run_url: str


@dataclass(frozen=True)
class Pass:
    """One pass: the line it prints and what it would send."""

    line: str
    action: str              # raise / morning-raise / quiet / resolve / unknown
    title: str = ""
    body: str = ""
    step: str | None = None
    job: str = ""
    url: str = ""


# ── time ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(text) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_at(text: str) -> datetime:
    """`--at`, as an aware UTC moment; a naive time is read as UTC."""
    moment = _when(text)
    if moment is None:
        raise ValueError(f"--at {text!r} is not an ISO time")
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pt_clock(moment: datetime) -> str:
    """`5:19 AM` — Pacific, daylight saving included."""
    local = moment.astimezone(PACIFIC)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _sha7(sha: str) -> str:
    return (sha or "")[:7]


def _short(step: str) -> str:
    return step.split(" ", 1)[1]


# ── reading GitHub ──────────────────────────────────────────────────────────


class GhReader:
    """GitHub's REST answers through the shared read seam."""

    @staticmethod
    def _log(line: str) -> None:
        print(line, file=sys.stderr)

    def read_json(self, path: str):
        try:
            out = gh_read(["api", path], log=self._log)
        except GhReadError as e:
            raise Unreadable(str(e)) from e
        if not out:
            raise Unreadable(f"the answer to {path} was empty")
        try:
            return json.loads(out)
        except ValueError as e:
            raise Unreadable(f"the answer to {path} was not JSON") from e

    def read_text(self, path: str) -> str:
        try:
            return gh_read(["api", path], log=self._log)
        except GhReadError as e:
            if ESCAPES_FLAG not in (e.stderr or ""):
                raise Unreadable(str(e)) from e
        try:
            return gh_read(["api", ESCAPES_FLAG, path], log=self._log)
        except GhReadError as e:
            raise Unreadable(str(e)) from e


def _read(reader, path: str, what: str):
    try:
        answer = reader.read_json(path)
    except Unreadable as e:
        raise Unreadable(f"{what} — {e}") from e
    if not isinstance(answer, (dict, list)):
        raise Unreadable(f"{what} — GitHub's answer was not a record")
    return answer


def _ref_sha(reader, repo: str, ref: str, what: str) -> str:
    """The commit a ref names, through an annotated tag when it is one."""
    obj = (_read(reader, f"repos/{repo}/git/ref/{ref}", what) or {})
    obj = obj.get("object") if isinstance(obj, dict) else None
    if isinstance(obj, dict) and obj.get("type") == "tag":
        tag = _read(reader, f"repos/{repo}/git/tags/{obj.get('sha')}", what)
        obj = tag.get("object") if isinstance(tag, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(sha, str) or not sha:
        raise Unreadable(f"{what} — GitHub named no commit")
    return sha


def _compare(reader, repo: str, base: str, head: str) -> tuple[str, dict]:
    """GitHub's compare status for `base...head`, and every commit `head` has
    that `base` lacks, by sha."""
    what = f"{pc.CHANNEL}...{pc.TRUNK} ({_sha7(base)}...{_sha7(head)})"
    listed: list = []
    status, total = None, None
    for page in range(1, COMPARE_PAGES + 1):
        answer = _read(reader, f"repos/{repo}/compare/{base}...{head}"
                               f"?per_page={COMPARE_PAGE}&page={page}", what)
        status = answer.get("status")
        total = answer.get("total_commits")
        got = answer.get("commits")
        if not isinstance(got, list) or not isinstance(total, int):
            raise Unreadable(f"{what} — the answer carried no list of commits")
        listed.extend(got)
        if len(listed) >= total or not got:
            break
    if status not in (pc.AHEAD, pc.BEHIND, pc.IDENTICAL, pc.DIVERGED):
        raise Unreadable(f"{what} — GitHub's compare status was {status!r}")
    if status in (pc.AHEAD, pc.DIVERGED) and len(listed) < total:
        raise Unreadable(f"{what} — it lists {total} commits and only "
                         f"{len(listed)} were read")
    return status, {c.get("sha"): c for c in listed if isinstance(c, dict)}


def first_parent(lacking: dict, head: str) -> tuple[list, str | None]:
    """`main`'s first-parent commits `stable` lacks, newest first, and the
    first one it carries (where the walk stopped)."""
    chain, sha = [], head
    while sha in lacking:
        record = lacking[sha]
        committed = _when(((record.get("commit") or {}).get("committer") or {}).get("date"))
        if committed is None:
            raise Unreadable(f"the commit date of {_sha7(sha)} on {pc.TRUNK}")
        chain.append(Commit(sha, committed))
        parents = record.get("parents") or []
        sha = parents[0].get("sha") if parents and isinstance(parents[0], dict) else None
    return chain, sha


def decision_in(text: str) -> tuple[str, str] | None:
    """The last decision line in a Promote Channel log: (outcome, reason)."""
    found = None
    for raw in (text or "").splitlines():
        m = DECISION_LINE.match(ANSI.sub("", raw).strip())
        if m and m.group("outcome") in DECISION_OUTCOMES:
            found = (m.group("outcome"), m.group("reason").strip())
    return found


def _run_decision(reader, repo: str, run: dict) -> Decision | None:
    what = f"the log of Promote Channel run {run.get('id')}"
    jobs = _read(reader, f"repos/{repo}/actions/runs/{run.get('id')}/jobs"
                         f"?per_page={RUNS_PAGE}", what).get("jobs") or []
    for job in jobs:
        if job.get("status") != "completed" or job.get("conclusion") == "skipped":
            continue
        try:
            text = reader.read_text(f"repos/{repo}/actions/jobs/{job.get('id')}/logs")
        except Unreadable as e:
            raise Unreadable(f"{what} — {e}") from e
        found = decision_in(text)
        if found:
            return Decision(found[0], found[1], str(run.get("html_url") or ""))
    return None


def promotions(reader, repo: str, at: datetime | None,
               *, want_stable: bool) -> tuple[Decision | None, str | None]:
    """The newest Promote Channel decision (as of `at`), and — when
    `want_stable` — the sha the newest promoting one moved `stable` to."""
    query = f"status=completed&per_page={PROMOTE_RUNS}"
    if at is not None:
        query += "&created=" + urllib.parse.quote(f"<={iso(at)}", safe="")
    what = "Promote Channel's runs" + (f" before {iso(at)}" if at else "")
    runs = _read(reader, f"repos/{repo}/actions/workflows/{PROMOTE_FILE}/runs?{query}",
                 what).get("workflow_runs")
    if not isinstance(runs, list):
        raise Unreadable(f"{what} — the answer carried no list of runs")
    runs = sorted((r for r in runs if isinstance(r, dict)),
                  key=lambda r: str(r.get("created_at") or ""), reverse=True)
    newest, stable, read = None, None, 0
    for run in runs:
        finished = _when(run.get("updated_at"))
        if at is not None and (finished is None or finished > at):
            continue
        if not want_stable and read >= DECISION_LOGS:
            break
        read += 1
        decision = _run_decision(reader, repo, run)
        if decision is None:
            continue
        newest = newest or decision
        if not want_stable:
            break
        if decision.outcome in pc.PROMOTING_OUTCOMES:
            m = PROMOTED_SHA.search(decision.reason)
            if m:
                stable = m.group("sha")
                break
    return newest, stable


def _attempt_as_of(reader, repo: str, run: dict, at: datetime | None) -> tuple[int, dict]:
    """The attempt a run stood on at `at`, and that attempt's record."""
    n = run.get("run_attempt") if isinstance(run.get("run_attempt"), int) else 1
    record = run
    while at is not None and n > 1:
        started = _when(record.get("run_started_at"))
        if started is not None and started <= at:
            break
        n -= 1
        record = _read(reader, f"repos/{repo}/actions/runs/{run.get('id')}/attempts/{n}",
                       f"attempt {n} of run {run.get('id')}")
    return n, record


def _jobs(reader, repo: str, run_id, n: int) -> list:
    what = f"the jobs of run {run_id} attempt {n}"
    jobs: list = []
    for page in range(1, JOBS_PAGES + 1):
        answer = _read(reader, f"repos/{repo}/actions/runs/{run_id}/attempts/{n}/jobs"
                               f"?per_page={JOBS_PAGE}&page={page}", what)
        got = answer.get("jobs")
        if not isinstance(got, list):
            raise Unreadable(f"{what} — the answer carried no list of jobs")
        jobs.extend(got)
        total = answer.get("total_count")
        if not got or not isinstance(total, int) or len(jobs) >= total:
            break
    return jobs


def _job_as_of(job: dict, at: datetime | None) -> tuple[str, datetime | None]:
    started = _when(job.get("started_at"))
    completed = _when(job.get("completed_at"))
    status = job.get("status")
    if at is None:
        if status == "completed":
            return str(job.get("conclusion") or ""), completed
        return (RUNNING if status == RUNNING else QUEUED), None
    if status == "completed" and completed is not None and completed <= at:
        return str(job.get("conclusion") or ""), completed
    if started is not None and started <= at:
        return RUNNING, None
    return QUEUED, None


def commit_jobs(reader, repo: str, sha: str, at: datetime | None) -> list:
    """Every job of the proving runs on `main` for one commit, as of `at`."""
    found = []
    for workflow, event in PROVING:
        query = f"branch={pc.TRUNK}&head_sha={sha}&per_page={RUNS_PAGE}"
        if event:
            query += f"&event={event}"
        what = f"the {workflow} runs of {_sha7(sha)}"
        runs = _read(reader, f"repos/{repo}/actions/workflows/{workflow}/runs?{query}",
                     what).get("workflow_runs")
        if not isinstance(runs, list):
            raise Unreadable(f"{what} — the answer carried no list of runs")
        for run in runs:
            created = _when(run.get("created_at"))
            if at is not None and (created is None or created > at):
                continue
            n, record = _attempt_as_of(reader, repo, run, at)
            # A re-run keeps the run's `created_at`; its attempt's start moves.
            began = _when(record.get("run_started_at")) or created
            url = str(run.get("html_url") or "")
            if n != run.get("run_attempt"):
                url = f"{url}/attempts/{n}"
            for job in _jobs(reader, repo, run.get("id"), n):
                state, completed = _job_as_of(job, at)
                step = next((s.get("name") for s in job.get("steps") or []
                             if s.get("conclusion") == FAILURE), None)
                found.append(Job(str(job.get("name") or ""), url, n, state,
                                 _when(job.get("started_at")), completed,
                                 step if state == FAILURE else None, began))
    return found


def failing_job(jobs: list) -> Job | None:
    """The earliest-completing failed job, a roll-up passed over for a part
    that failed before it or with it."""
    failed = [j for j in jobs if j.state in FAILING and j.completed is not None]

    def summed(j: Job) -> bool:
        return j.name in ROLLUP_JOBS and any(
            p.name.startswith(f"{j.name} (") and p.completed <= j.completed
            for p in failed)

    pool = [j for j in failed if not summed(j)]
    return min(pool, key=lambda j: j.completed) if pool else None


def going_since(job: Job) -> datetime | None:
    """When a running job started, or a not-started one's attempt began."""
    if job.state == RUNNING and job.started is not None:
        return job.started
    return job.began


def running_job(jobs: list, now: datetime) -> Job | None:
    """The longest-going job still running, else one not yet started — only
    once it has gone `ALERT_AFTER_MINUTES` by its own clock."""
    for state in (RUNNING, QUEUED):
        stuck = [j for j in jobs if j.state == state and going_since(j) is not None
                 and (now - going_since(j)).total_seconds() >= ALERT_AFTER_MINUTES * 60]
        if stuck:
            return min(stuck, key=going_since)
    return None


# ── the reading ─────────────────────────────────────────────────────────────


@dataclass
class Reading:
    held: bool
    carried: str                      # main's head, when not held
    chain: list                       # first-parent commits stable lacks, newest first
    since: datetime | None = None
    age: int = 0
    decision: Decision | None = None
    failing: Job | None = None
    unread: tuple = ()


def read_hold(reader, repo: str, *, now: datetime,
              at: datetime | None = None) -> Reading:
    """Held or not, and since when. Raises Unreadable for any read the answer
    rests on — the pass is then `unknown`."""
    decision = None
    if at is None:
        stable = _ref_sha(reader, repo, f"tags/{pc.CHANNEL}", f"the {pc.CHANNEL} tag")
    else:
        try:
            decision, stable = promotions(reader, repo, at, want_stable=True)
        except Unreadable as e:
            raise Unreadable(f"where {pc.CHANNEL} stood at {iso(at)} — {e}") from e
        if not stable:
            raise Unreadable(f"where {pc.CHANNEL} stood at {iso(at)} — no Promote "
                             f"Channel run before it says it promoted {pc.CHANNEL}")
    main = _ref_sha(reader, repo, f"heads/{pc.TRUNK}", f"{pc.TRUNK}'s head")
    status, lacking = _compare(reader, repo, stable, main)
    if status in (pc.IDENTICAL, pc.BEHIND):
        return Reading(False, main if at is None else stable, [])
    chain, _carried = first_parent(lacking, main)
    if not chain:
        raise Unreadable(f"{pc.CHANNEL}...{pc.TRUNK} — {pc.TRUNK}'s head is not "
                         "among the commits it lists")
    if at is not None:
        chain = [c for c in chain if c.committed <= at]
        if not chain:
            return Reading(False, stable, [])
    since = chain[-1].committed
    age = max(0, int((now - since).total_seconds() // 60))
    reading = Reading(True, "", chain, since, age)
    reading.decision = decision
    return reading


def diagnose(reader, repo: str, reading: Reading, *, at: datetime | None,
             now: datetime) -> Reading:
    """The diagnosis reads. A failure here is named, never raised: the hold
    is known, and what could not be read is a person's to look at."""
    unread = []
    if at is None:
        try:
            reading.decision, _ = promotions(reader, repo, None, want_stable=False)
        except Unreadable as e:
            unread.append(f"Promote Channel's newest decision ({e})")
    running = None
    for commit in reading.chain[:COMMITS_READ]:
        try:
            jobs = commit_jobs(reader, repo, commit.sha, at)
        except Unreadable as e:
            unread.append(str(e))
            continue
        found = failing_job(jobs)
        if found:
            reading.failing = found
            break
        running = running or running_job(jobs, now)
    if reading.failing is None and running:
        reading.failing = running
    reading.unread = tuple(unread)
    return reading


# ── the diagnosis ───────────────────────────────────────────────────────────


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _held_text(hold_text: str, decision: Decision | None) -> str:
    """The hold's own words: `CHANNEL_HOLD`, else the decision's record of it."""
    said = _one_line(hold_text)
    if said:
        return said
    m = re.search(r"Reason on record: (.+?)\. (?:Clear|A by-hand)",
                  decision.reason if decision else "")
    return m.group(1) if m else ""


def next_step(reading: Reading, hold_text: str = "") -> tuple[str, str]:
    """(one of NEXT_STEPS, the one sentence why)."""
    decision, job = reading.decision, reading.failing
    outcome = decision.outcome if decision else None
    if outcome == pc.OUTCOME_HELD:
        said = _held_text(hold_text, decision)
        return PERSON, (f"the channel is paused by hand — CHANNEL_HOLD reads "
                        f"\"{said}\"; clear it to let {pc.CHANNEL} move."
                        if said else
                        "the channel is paused by hand, and CHANNEL_HOLD is now "
                        "empty — the next Promote Channel run moves it.")
    if outcome in PERSON_OUTCOMES:
        return PERSON, (f"Promote Channel refused with {outcome}, which a re-run "
                        f"does not clear: {_one_line(decision.reason)}")
    if job and job.attempt >= 2:
        return PERSON, (f"the run is already on attempt {job.attempt} and the job "
                        f"{_doing(job)}, so a re-run has not cleared it.")
    if reading.unread:
        return PERSON, ("the diagnosis could not read "
                        + "; ".join(reading.unread) + ".")
    if outcome == pc.OUTCOME_MIRROR_FAILED:
        return FIX, ("agent-bureau's mirror tests fail on this commit "
                     "(mirror-tests-failed), so agent-bureau's mirror needs a fix.")
    if job:
        if job.state == FAILURE and job.failed_step:
            return FIX, (f"the job failed at step \"{job.failed_step}\" on attempt "
                         f"{job.attempt}, so the code needs a fix before "
                         f"{pc.CHANNEL} can move.")
        if job.state not in FAILING:
            since = ("it started" if job.state == RUNNING else "its attempt began")
            return RERUN, (f"the job {_doing(job)} on attempt {job.attempt}, "
                           f"{ALERT_AFTER_MINUTES // 60} hours or more after "
                           f"{since}, so it is stuck: cancel the run and "
                           "re-run it.")
        return RERUN, (f"the job {_doing(job)} on attempt {job.attempt}, so "
                       "re-running its failed jobs is the next step.")
    if outcome in OUTCOME_NEXT:
        return OUTCOME_NEXT[outcome], (
            f"no job has failed, and Promote Channel's newest decision is "
            f"{outcome}: {_one_line(decision.reason)}")
    return PERSON, ("no job has failed and no Promote Channel decision names "
                    "why the channel is held.")


def _doing(job: Job) -> str:
    if job.state == CANCELLED:
        return "was canceled"
    if job.state == TIMED_OUT:
        return "timed out"
    if job.state == FAILURE:
        return ("failed at step \"" + job.failed_step + "\"" if job.failed_step
                else "failed with no step failed, which is a lost runner")
    if job.state == RUNNING:
        return "is still running"
    return "has still not started"


def job_label(reading: Reading) -> tuple[str, str]:
    """(the failing job's name or what stands for it, the run to open)."""
    decision = reading.decision
    if decision and decision.outcome == pc.OUTCOME_HELD:
        return "no failing job — paused by hand", decision.run_url or "none"
    if reading.failing:
        return reading.failing.name, reading.failing.run_url or "none"
    if decision:
        return f"no failing job — {decision.outcome}", decision.run_url or "none"
    return "no failing job found", "none"


def job_word(reading: Reading) -> str:
    """`Stuck job` for a job named because it is still going, else
    `Failing job`."""
    decision, job = reading.decision, reading.failing
    if decision and decision.outcome == pc.OUTCOME_HELD:
        return "Failing job"
    return "Stuck job" if job and job.state not in FAILING else "Failing job"


def title(reading: Reading, step: str, label: str) -> str:
    return f"stable held since {pt_clock(reading.since)} PT — needs {step}: {label}"


def body(reading: Reading, step: str, why: str, label: str, url: str) -> str:
    oldest = reading.chain[-1].sha
    return "\n".join([
        f"{job_word(reading)}: {label} ({url})",
        f"Needs: {step} — {why}",
        f"Held since: {iso(reading.since)} ({pt_clock(reading.since)} PT)",
        f"Waiting on stable: {len(reading.chain)} commit(s), oldest {_sha7(oldest)}",
    ])


def raise_window(age: int, now: datetime, interval: int) -> str | None:
    """`raise`, `morning-raise`, or None — see WHEN IT RAISES."""
    if age >= ALERT_AFTER_MINUTES and (age - ALERT_AFTER_MINUTES) % 60 < interval:
        return "raise"
    clock = now.astimezone(PACIFIC).strftime("%H:%M")
    if MORNING_START <= clock <= MORNING_END and age >= MORNING_FLOOR_MINUTES:
        return "morning-raise"
    return None


# ── one pass ────────────────────────────────────────────────────────────────


def watch(reader, repo: str, *, interval: int, at: datetime | None = None,
          apply: bool = False, hold_text: str = "",
          now: datetime | None = None) -> Pass:
    """One pass, decided and worded. Calls no door: `main` does."""
    now = at or now or _now()
    try:
        reading = read_hold(reader, repo, now=now, at=at)
    except Unreadable as e:
        return Pass(f"{TAG} unknown {_one_line(str(e))}", "unknown")
    if not reading.held:
        return Pass(f"{TAG} none — {pc.CHANNEL} carries {pc.TRUNK} "
                    f"{_sha7(reading.carried)}", "resolve")
    reading = diagnose(reader, repo, reading, at=at, now=now)
    step, why = next_step(reading, hold_text)
    label, url = job_label(reading)
    action = raise_window(reading.age, now, interval) or "quiet"
    word = action if action == "quiet" or (apply and at is None) else f"would-{action}"
    line = (f"{TAG} held since {pt_clock(reading.since)} PT age={reading.age}m "
            f"next={_short(step)} job=\"{label}\" run={url} — {word}")
    return Pass(line, action, title(reading, step, label),
                body(reading, step, why, label, url), step, label, url)


def _door(seen: Pass) -> str | None:
    """Raise or resolve through the notice client; its outcome."""
    if seen.action in ("raise", "morning-raise"):
        linked = seen.url.startswith("https://")
        return pipeline_notice.raise_notice(
            NOTICE_KEY, severity=SEVERITY, title=seen.title, body=seen.body,
            action_url=seen.url if linked else None,
            action_label="Open the run" if linked else None,
            fallback=FALLBACK)
    if seen.action == "resolve":
        return pipeline_notice.resolve_notice(NOTICE_KEY)
    return None


def main(argv=None, *, reader=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="one pass: read, diagnose, raise or resolve")
    w.add_argument("--repo", required=True, help="owner/name")
    w.add_argument("--interval-minutes", type=int, required=True,
                   help="minutes between scheduled passes")
    w.add_argument("--apply", action="store_true",
                   help="call the notice door; without it, say what would be sent")
    w.add_argument("--at", default=None,
                   help="replay this ISO moment, every read as of then; never "
                        "calls the door")
    args = parser.parse_args(argv)
    if args.interval_minutes < 1:
        parser.error("--interval-minutes must be at least 1")
    try:
        at = parse_at(args.at) if args.at else None
    except ValueError as e:
        parser.error(str(e))
    seen = watch(reader or GhReader(), args.repo, interval=args.interval_minutes,
                 at=at, apply=args.apply, hold_text=os.environ.get("CHANNEL_HOLD", ""))
    print(seen.line, flush=True)
    live = args.apply and at is None
    if seen.title and seen.action != "quiet" and not live:
        print(f"{TAG} would send: {seen.title}\n{seen.body}", file=sys.stderr)
    if not live:
        return 0
    return 1 if _door(seen) == "lost" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
