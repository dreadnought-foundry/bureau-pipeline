#!/usr/bin/env python3
"""A canceled check on an approved pull request is re-run once, never sent a
fix agent (DRE-3072). Stdlib only, and no network: the reads and the writes
are `reconcile.fix_approved_but_red`'s.

THE RULE, the CEO's of 2026-09-05 11:30 PT: "A cancelled CI check is UNKNOWN,
not red. The approved-but-red sweep re-runs a cancelled check once; only a
check that then FAILS is red and gets a fix agent. A cancellation never
dispatches a fix agent, because a fix agent cannot fix a cancellation."

THE INCIDENTS. portico #415, 2026-09-03: `infra — typecheck & test` was
canceled by its own 10-minute limit and the sweep dispatched a fix agent at a
head whose only red was that one check. agent-bureau #3502, 2026-10-09, head
a3fa0c44: `Console backend shard 1` was canceled at its 15-minute limit, and
`Console backend (pytest)`, the job that sums the shards, read `failure` because
of it. Both were in CI run 38023358661. The sweep dispatched; the fix run lost
its machine and sent the card to Triage (DRE-6572); a second fix run on the same
commit found nothing to fix.

WHY PER WORKFLOW RUN. Leaving `cancelled` out of the red set alone changes
nothing on the 2026-10-09 case: the summary job's `failure` dispatches by
itself. So the head's red checks are grouped by the workflow run each belongs
to, read off `details_url` (`…/actions/runs/<run id>/job/<job id>`), and a run
holding any `cancelled` check is UNKNOWN as a whole — a `failure` beside it
included. A run with red checks and no `cancelled` one is red exactly as
before, and a red check with no run to read (another app's check) is too.

WHAT HAPPENS TO AN UNKNOWN RUN (`decide`):

  * still going — waited on; GitHub refuses to re-run an unfinished run;
  * attempt 1, no re-run receipt for this head and run — re-run once, failed
    and canceled jobs only (`gh run rerun <id> --failed`), and one receipt;
  * attempt 1 with that receipt — waited on: the re-run was asked for;
  * a later attempt — never re-run again, and no fix agent. A person is told
    once, naming the job, bound to the head and the run.

"Once" is read off the run's own attempt number, as `runner_lost` reads it, and
off a worker-bot receipt bound to the head sha and the run id. After a re-run
the run is on attempt 2: a canceled job that passes and leaves something red
is rule 5's, and the fix agent goes; a job canceled again is told once. Nothing
loops. The hygiene agent's own check re-run (`hyg-check-rerun`) is a different
rule, and a run it re-ran is on attempt 2 here, so it is never re-run twice.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: What the sweep has always counted as red on an approved head.
RED_CONCLUSIONS = ("failure", "timed_out", "cancelled")
CANCELLED = "cancelled"

#: The two acts (config/pipeline-acts.json). Each tag is the idempotency key:
#: a worker-bot receipt carrying it beside the head sha and the run id is the
#: record that it was said.
RERUN_ACT = "canceled-check-rerun"
RERUN_TAG = "canceled-check-rerun"
AGAIN_ACT = "canceled-check-again"
AGAIN_TAG = "canceled-check-again"

_RUN_URL = re.compile(r"/actions/runs/(\d+)/job/\d+")

# GitHub's two wordings for a job stopped by its `timeout-minutes`:
# "…maximum execution time of 15m0s" and "…maximum execution time of 360 minutes."
_LIMIT = re.compile(
    r"maximum execution time of\s+"
    r"(?:(?P<minutes_word>\d+)\s+minutes?\b"
    r"|(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?)",
    re.IGNORECASE,
)


class Split(NamedTuple):
    """`red` — the checks the fix agent is sent at, as before; `unknown` — the
    red checks of each run holding a `cancelled` one, keyed by run id (None
    for a canceled check with no run behind it)."""

    red: tuple
    unknown: dict


class Step(NamedTuple):
    """`rerun` · `tell` · `wait` · `done` · `unread`, and why."""

    action: str
    why: str = ""


def is_review(check: dict) -> bool:
    """A review-owned check, left out of this sweep as it always was."""
    return str(check.get("name") or "").endswith("review")


def run_of(check: dict) -> int | None:
    """The workflow run a check run belongs to, off its `details_url`."""
    found = _RUN_URL.search(str(check.get("details_url") or ""))
    return int(found.group(1)) if found else None


def split(check_runs) -> Split:
    """The head's red checks, split into red runs and unknown runs."""
    by_run: dict = {}
    for check in check_runs or ():
        if not isinstance(check, dict) or is_review(check):
            continue
        if (check.get("conclusion") or "") not in RED_CONCLUSIONS:
            continue
        by_run.setdefault(run_of(check), []).append(check)
    red: list = []
    unknown: dict = {}
    for run_id, checks in by_run.items():
        canceled = [c for c in checks if c.get("conclusion") == CANCELLED]
        if canceled and run_id is None:
            # No run to group by: each canceled check is unknown on its own,
            # and a failure beside it is red as before.
            unknown.setdefault(None, tuple(canceled))
            red.extend(c for c in checks if c.get("conclusion") != CANCELLED)
        elif canceled:
            unknown[run_id] = tuple(checks)
        else:
            red.extend(checks)
    return Split(tuple(red), unknown)


def canceled_jobs(checks) -> list:
    """The names of the canceled checks among `checks`, in order."""
    return [str(c.get("name") or f"job {c.get('id')}")
            for c in checks or () if c.get("conclusion") == CANCELLED]


def limit(annotations) -> str | None:
    """The time limit a job's annotations say it was stopped at, as
    "15-minute", or None when none of them names one."""
    for note in annotations or ():
        found = _LIMIT.search(str((note or {}).get("message") or ""))
        if not found:
            continue
        if found.group("minutes_word"):
            return f"{int(found.group('minutes_word'))}-minute"
        h, m, s = (int(found.group(k) or 0) for k in ("h", "m", "s"))
        if not (h or m or s):
            continue
        if s:
            return f"{h * 3600 + m * 60 + s}-second"
        return f"{h * 60 + m}-minute"
    return None


def _marker(tag: str, sha: str, run_id) -> re.Pattern:
    return re.compile(rf"{re.escape(tag)} @{re.escape(sha)} run {run_id}\b")


def said(bodies, tag: str, sha: str, run_id) -> bool:
    """Whether a receipt with `tag`, bound to this head and run, is in
    `bodies` — the worker bot's comments, which the caller has filtered."""
    if not sha:
        return False
    marker = _marker(tag, sha, run_id)
    return any(marker.search(body or "") for body in bodies or ())


def decide(run_id, run: dict | None, sha: str, bodies) -> Step:
    """What to do about one unknown run. `run` is GitHub's record of it (None
    when it could not be read); `bodies` the worker bot's comments on the
    pull request."""
    if run_id is None:
        return Step("unread", "a canceled check with no workflow run to re-run")
    if not isinstance(run, dict):
        return Step("unread", f"run {run_id} could not be read")
    if run.get("status") != "completed":
        return Step("wait", f"run {run_id} is still {run.get('status') or 'going'}")
    attempt = int(run.get("run_attempt") or 1)
    if attempt <= 1:
        if said(bodies, RERUN_TAG, sha, run_id):
            return Step("wait", f"run {run_id} was re-run once already for this head")
        return Step("rerun", f"run {run_id} attempt 1 holds a canceled check")
    if said(bodies, AGAIN_TAG, sha, run_id):
        return Step("done", f"run {run_id} attempt {attempt} was told once already")
    return Step("tell", f"run {run_id} attempt {attempt} holds a canceled check again")


def _jobs(jobs) -> str:
    return ", ".join(f"`{j}`" for j in jobs) or "a job"


def rerun_receipt(number, sha: str, run_id, jobs, limit_text: str | None) -> str:
    """The receipt for a re-run (act `canceled-check-rerun`). Its first line
    reads "canceled at its <limit> limit, re-running" when the job's
    annotation named one, and "canceled, re-running" when it did not."""
    what = (f"canceled at its {limit_text} limit, re-running" if limit_text
            else "canceled, re-running")
    return (
        f"🔁 {RERUN_TAG} @{sha} run {run_id}: {_jobs(jobs)} {what}.\n\n"
        f"A canceled check has not failed — it was stopped, and there is "
        f"nothing in this pull request's code for a fix agent to fix. So the "
        f"sweep re-ran the failed and canceled jobs of workflow run {run_id} "
        f"once, and sent no fix agent. If a job still fails on the re-run, "
        f"that failure is red and the fix agent is sent as usual; if it is "
        f"canceled again, a person is told. PR #{number}, said once per commit "
        f"and run (DRE-3072)."
    )


def again_receipt(number, sha: str, run: dict, jobs) -> str:
    """The told-once hold for a run canceled again on a later attempt (act
    `canceled-check-again`)."""
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    return (
        f"🛑 {AGAIN_TAG} @{sha} run {run_id}: {_jobs(jobs)} was canceled again, "
        f"on attempt {attempt} of workflow run {run_id}.\n\n"
        f"The sweep re-runs a canceled check once and never sends a fix agent "
        f"at a cancellation, so nothing automatic is coming for PR #{number}. "
        f"A job canceled twice is usually stopping at its own time limit: a "
        f"person should look at why, and re-run it or change the limit. Said "
        f"once per commit and run (DRE-3072)."
    )
