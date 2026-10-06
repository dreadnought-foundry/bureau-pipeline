#!/usr/bin/env python3
"""A default-branch CI run whose jobs GitHub never gave a machine is re-run
once, by the reconcile sweep (DRE-5901). Stdlib only.

THE INCIDENT (2026-10-05, PT). The console repo's CI on main at 559c8b9 ended
`cancelled`: every job carried "The job was not acquired by Runner of type
hosted even after multiple attempts". Nothing failed and no test ran. The
release train ships only a commit whose CI concluded success, so it left
without the commit at 12:20, 12:50, 13:20 and 13:36, and the console stayed on
the previous release for more than two hours until the operator re-ran the run
by hand (run 37362862791). The re-run lost one job the same way and needed a
second hand re-run.

WHY THE SWEEP AND NOT THE MEDIC. The medic is woken on `failure` and
`timed_out` only (`scripts/medic_wake.py`), and that door is written into every
product repo's stub, so a cancelled run never reaches it and widening it is a
stub change in every repo. The reconcile sweep runs every fifteen minutes in
every repo that consumes the pipeline, from the shared reusable workflow, and
already re-runs a crashed run with `gh run rerun --failed`. The rule lives
here, the wiring is `reconcile.rerun_runner_lost_ci`, and nothing in either
names a repo.

THE RULE, per workflow on the default branch:

  * The candidate is the NEWEST CI run of that workflow — a `push` run (the
    merge gate's `COMMIT_EVENTS`) at no review path, judged exactly as the
    release train judges which runs gate a commit. An older run was superseded
    by a newer push, and a newer commit's CI is what the train will read.
  * It is `completed` and concluded `cancelled`. A `failure` is the medic's,
    which retries once; two retriers on one run would race.
  * Every job that did not end green (`merge_gate.GREEN_CONCLUSIONS`) carries
    GitHub's "not acquired by Runner" annotation. A job cancelled by a newer
    push ("Canceling since a higher priority waiting request…"), by a person
    ("The run was canceled by @…") or by its timeout carries another, and one
    such job leaves the whole run alone. Annotations that cannot be read are
    not "not acquired": the run is left alone and the line says so.
  * On attempt 1 the failed and cancelled jobs are re-run once — `gh run rerun
    <id> --failed` — and one receipt line names the repo, the run, the jobs
    and the reason. The re-run moves the run to attempt 2, so GitHub's own
    attempt counter is the record that it happened: no later pass re-runs it.
  * On any later attempt the run is NOT re-run again. A runner that is lost
    twice is an outage, not a blip, and looping on it would only deepen the
    queue. It is reported once, as a receipt line naming the run.

"REPORTED ONCE" WITHOUT A WRITE. The report is a line in the sweep's log and
step summary; nothing is posted anywhere it could be counted later. So each
scheduled pass reports only what finished between the previous scheduled
pass's start and its own (`window_from`): consecutive scheduled passes tile
time, and each lost attempt falls in exactly one of their windows. A pass the
schedule did not start (a hand dispatch, a relay event) defers to the next
scheduled one. A window that cannot be read reports anyway — a repeated line
is cheaper than an outage nobody sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import merge_gate

#: GitHub's wording on a job no runner picked up, lowercased for the match.
NOT_ACQUIRED = "not acquired by runner"

#: Every receipt line opens with this; reconcile.yml lifts them into the
#: sweep's step summary by it.
PREFIX = "runner-lost:"

#: The sweep's own runs that count as a pass that ran: a cancelled or skipped
#: sweep reported nothing, so the window reaches back past it.
_PASS_RAN = frozenset({"success", "failure"})


class Client(Protocol):
    """What the decision asks GitHub. Every read answers None when it could
    not be read — never an empty list, which would mean "there is nothing"."""

    def runs(self) -> list | None: ...

    def jobs(self, run_id: int, attempt: int) -> list | None: ...

    def annotations(self, job_id: int) -> list | None: ...

    def rerun(self, run_id: int) -> None: ...  # raises when refused

    def report_window(self) -> Window | None: ...


@dataclass(frozen=True)
class Window:
    """The completion times this pass reports: `start <= t < end`. `start`
    None is open-ended (no earlier scheduled pass ran); `defer` set means this
    pass reports nothing and says why."""

    start: str | None = None
    end: str | None = None
    defer: str = ""


@dataclass(frozen=True)
class Judgement:
    """`lost` — every non-green job was never given a machine; `other` — a
    cancellation of any other kind; `unknown` — it could not be read."""

    kind: str
    jobs: tuple = ()
    why: str = ""


def _when(raw) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_ci_run(run: dict) -> bool:
    """A run the release train would count as CI of its commit."""
    return (run.get("event") in merge_gate.COMMIT_EVENTS
            and run.get("path") not in merge_gate.DEFAULT_REVIEW_WORKFLOWS)


def candidates(runs, branch: str) -> list:
    """The newest CI run of each workflow on `branch`, kept only when it
    finished `cancelled`."""
    newest: dict = {}
    for run in sorted((r for r in runs or () if isinstance(r, dict)),
                      key=lambda r: r.get("created_at") or "", reverse=True):
        if run.get("head_branch") != branch or not is_ci_run(run):
            continue
        newest.setdefault(run.get("workflow_id") or run.get("path"), run)
    return [r for r in newest.values()
            if r.get("status") == "completed" and r.get("conclusion") == "cancelled"]


def judge(jobs, annotations) -> Judgement:
    """Whether every non-green job carries the "not acquired" annotation.

    `annotations` is the client's reader, asked per job and only until the
    answer is known: the first job that was cancelled for another reason
    settles it, so a run a person cancelled costs one read."""
    if jobs is None:
        return Judgement("unknown", why="its jobs could not be read")
    lost = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if (job.get("conclusion") or "") in merge_gate.GREEN_CONCLUSIONS:
            continue
        name = job.get("name") or f"job {job.get('id')}"
        notes = annotations(job.get("id"))
        if notes is None:
            return Judgement("unknown", why=f"the annotations on {name!r} could not be read")
        if not any(NOT_ACQUIRED in str((n or {}).get("message") or "").lower()
                   for n in notes):
            return Judgement("other", why=f"{name!r} was not cancelled for a lost runner")
        lost.append(name)
    if not lost:
        return Judgement("other", why="no job ended other than green")
    return Judgement("lost", tuple(lost))


def window_from(own: dict, previous) -> Window:
    """This pass's report window, off GitHub's record of the sweep's runs.

    `own` is this pass's run; `previous` is the same workflow's runs. The
    window ends where this pass started and begins where the newest earlier
    SCHEDULED pass that ran started."""
    event = own.get("event") or "an unknown event"
    if event != "schedule":
        return Window(defer=(f"this pass was started by {event}, so the next "
                             "scheduled pass reports it"))
    created = own.get("created_at") or ""
    earlier = [
        r for r in previous or () if isinstance(r, dict)
        and r.get("id") != own.get("id")
        and r.get("event") == "schedule"
        and r.get("status") == "completed"
        and r.get("conclusion") in _PASS_RAN
        and (r.get("created_at") or "") < created
    ]
    last = max(earlier, key=lambda r: r.get("created_at") or "", default=None)
    start = (last.get("run_started_at") or last.get("created_at")) if last else None
    return Window(start=start, end=own.get("run_started_at") or created)


def due(window: Window | None, completed_at) -> bool:
    """Whether a lost attempt that finished at `completed_at` is this pass's
    to report. An unreadable window (None) or completion time reports."""
    if window is None:
        return True
    if window.defer:
        return False
    when = _when(completed_at)
    if when is None:
        return True
    start, end = _when(window.start), _when(window.end)
    return (start is None or when >= start) and (end is None or when < end)


def _jobs_text(jobs) -> str:
    return ", ".join(jobs)


def rerun_receipt(repo: str, run: dict, jobs) -> str:
    return (
        f"{PREFIX} re-ran {repo} run {run.get('id')} ({run.get('name') or 'CI'}, "
        f"attempt {run.get('run_attempt') or 1}, {(run.get('head_sha') or '')[:7]}) — "
        f"failed and cancelled jobs: {_jobs_text(jobs)} — reason: GitHub cancelled "
        "every one \"not acquired by Runner\", so no machine ran them, nothing "
        "failed and the commit's CI never finished; re-run once (DRE-5901)"
    )


def outage_receipt(repo: str, run: dict, jobs) -> str:
    return (
        f"{PREFIX} not re-run: {repo} run {run.get('id')} "
        f"({run.get('name') or 'CI'}, attempt {run.get('run_attempt')}, "
        f"{(run.get('head_sha') or '')[:7]}) — jobs: {_jobs_text(jobs)} — "
        "reason: \"not acquired by Runner\" again on a re-run attempt; a runner "
        "outage, reported once and left for a person, never re-run in a loop "
        "(DRE-5901)"
    )


def sweep(repo: str, branch: str, client: Client) -> list:
    """One pass. Returns the lines to print; a line opening `ERROR:` is a
    write GitHub refused, and the caller fails the run on it."""
    runs = client.runs()
    if runs is None:
        return [f"runner-lost: could not read {repo}'s CI runs on {branch} — "
                "re-running nothing this sweep"]
    lines: list = []
    window_read = False
    window: Window | None = None
    for run in candidates(runs, branch):
        run_id = run.get("id")
        attempt = int(run.get("run_attempt") or 1)
        verdict = judge(client.jobs(run_id, attempt), client.annotations)
        if verdict.kind == "unknown":
            lines.append(f"runner-lost: could not judge {repo} run {run_id} "
                         f"({verdict.why}) — left alone this sweep")
            continue
        if verdict.kind != "lost":
            continue
        if attempt <= 1:
            try:
                client.rerun(run_id)
            except Exception as e:  # noqa: BLE001 — every refusal is reported
                lines.append(f"ERROR: runner-lost: re-running {repo} run {run_id} "
                             f"was refused ({e}) — tried again next sweep")
                continue
            lines.append(rerun_receipt(repo, run, verdict.jobs))
            continue
        if not window_read:
            window, window_read = client.report_window(), True
        if due(window, run.get("updated_at")):
            lines.append(outage_receipt(repo, run, verdict.jobs))
    return lines
