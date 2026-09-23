#!/usr/bin/env python3
"""The repair loop's memory: which steps the clock has already killed (DRE-4674).

`red_main_repair.decide` used to know only about the run in front of it, so a
step that timed out on every commit and on every retry read as infrastructure
every single time. Portico's `main` failed `infra — typecheck & test` on four
commits in a row from 2026-09-22 19:40 PT, each retried once, every time on
`##[error]The action 'Test' has timed out after 12 minutes` — a deterministic
cause (the suite had outgrown the step's limit) that the repair loop declined
to look at four times over ~12 hours. The red main surfaced when a person
asked.

This module is the fetch that gives the decision a memory. It runs in
`red-main-repair.yml` BEFORE the decide step and writes one document:

    repair_history.py gather --repo <owner/name> --workflow-id <id> \\
        --run-id <id> --run-attempt <n> --branch <default> \\
        --workflow-path <.github/workflows/ci.yml> --head-sha <sha> \\
        --current-log /tmp/red-main-log.txt --out /tmp/repair-history.json

    {"current": <entry>, "prior": [<entry>, …]}
    <entry> = {run_id, run_attempt, workflow_path, head_sha, log, jobs:
               [{name, conclusion, steps: [{name, conclusion}]}]}

`prior` is this run's PREVIOUS ATTEMPT (when `run_attempt > 1`, first — it is
the most recent thing that happened) followed by up to the 3 most recent
COMPLETED runs of the same workflow on the default branch, older than this
one. `decide` compares (workflow path, job name, step name) exactly, so those
three fields are what every entry exists to carry.

Two properties carry the design, in this order:

  * **A failed read is "no history", never "repeated".** Main is already red
    and the bookkeeping is worth less than the fix, so nothing here raises and
    nothing here fails the step: a gather that cannot answer writes
    `FETCH-FAILED`, which `red_main_repair.load_history` reads as "nothing is
    known about earlier runs" and which lands the decision exactly where it
    lands today. The direction matters — inventing a repeat dispatches an
    agent at an innocent commit, and missing one costs what DRE-4674 cost.
  * **The event describes the current run; only its jobs are fetched.** The
    workflow path, the head sha and the attempt come from the `workflow_run`
    payload the caller was handed, and the failed-step log is the one the job
    has already pulled for the classifier — no second fetch of the same bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — the gh CLI, fixed args, shell=False
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: How many earlier runs on the default branch the rule may look back over.
#: Three is the card's window: far enough that a retried commit and the two
#: after it are all in view, near enough that a step fixed last week is not
#: still on this decision's record.
PRIOR_RUNS = 3

#: What the run listing asks for. One more than the window, because the
#: current run is itself a run of this workflow on this branch and can appear
#: in its own listing — it is dropped, and the spare keeps the window full.
LIST_PER_PAGE = PRIOR_RUNS + 1

#: The marker a failed gather writes. Deliberately not JSON: `decide` reads
#: anything it cannot parse as "no history", so the marker needs no second
#: reader and cannot be mistaken for an empty-but-valid document.
FETCH_FAILED = "FETCH-FAILED"


class _Gh:
    """The three GitHub reads this module makes, in one object so a test can
    hand a fake in. Every one of them may raise; every caller expects it."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def completed_runs(self, workflow_id, branch: str, per_page: int):
        """The newest completed runs of ONE workflow on ONE branch, newest
        first — GitHub's own ordering, which is what makes "older than this
        one" readable without comparing timestamps."""
        payload = self._json(
            "api",
            f"repos/{self.repo}/actions/workflows/{workflow_id}/runs"
            f"?branch={branch}&status=completed&per_page={per_page}",
        )
        return (payload or {}).get("workflow_runs") or []

    def jobs_of(self, run_id, attempt=None):
        """The jobs of a run, or of one specific ATTEMPT of it. The attempt
        form is the whole of "the previous attempt of this run": GitHub keeps
        each attempt's jobs at its own path and the plain one answers the
        latest."""
        path = f"repos/{self.repo}/actions/runs/{run_id}"
        path += "/jobs" if attempt is None else f"/attempts/{attempt}/jobs"
        return self._json("api", path)

    def failed_log(self, run_id, attempt=None) -> str:
        """That run's failed-step log — the same `--log-failed` fetch the
        classifier already makes for the current run, which is where the
        runner prints the limit it ran past."""
        args = ["run", "view", str(run_id), "--repo", self.repo, "--log-failed"]
        if attempt is not None:
            args += ["--attempt", str(attempt)]
        return self._text(*args)

    @staticmethod
    def _run(*args: str) -> str:
        # B603/B607: program-constructed args, shell=False, gh resolves via
        # PATH by design — the same call shape repair_card.py makes.
        done = subprocess.run(  # nosec B603 B607
            ["gh", *args], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError(
                f"gh {' '.join(args)} failed rc={done.returncode}: "
                f"{done.stderr.strip()[:300]}")
        return done.stdout or ""

    @classmethod
    def _json(cls, *args: str):
        return json.loads(cls._run(*args) or "{}")

    @classmethod
    def _text(cls, *args: str) -> str:
        return cls._run(*args)


def _steps(job) -> list:
    """One job's steps, narrowed to the two fields the rule compares on."""
    return [
        {"name": step.get("name") or "",
         "conclusion": step.get("conclusion") or ""}
        for step in (job or {}).get("steps") or ()
        if isinstance(step, dict)
    ]


def _jobs(payload) -> list:
    """The `…/jobs` payload, narrowed the same way. A run with no jobs at all
    is a run nothing can be read off — an empty list says so."""
    return [
        {"name": job.get("name") or "",
         "conclusion": job.get("conclusion") or "",
         "steps": _steps(job)}
        for job in (payload or {}).get("jobs") or ()
        if isinstance(job, dict)
    ]


def _entry(*, run_id, run_attempt, workflow_path, head_sha, jobs, log) -> dict:
    return {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_path": workflow_path or "",
        "head_sha": head_sha or "",
        "log": log or "",
        "jobs": jobs,
    }


def _prior_entry(gh, run) -> dict | None:
    """One earlier run, or None when it could not be read.

    A run this pass cannot read is simply not in the history. Less history
    only ever loses a repeat, and losing one lands on today's behaviour; the
    alternative — dropping the whole document over one run — would lose the
    others too.
    """
    run_id = (run or {}).get("id")
    if run_id is None:
        return None
    try:
        jobs = _jobs(gh.jobs_of(run_id))
    except Exception as exc:  # noqa: BLE001 — a red main outranks this fetch
        print(f"repair history: run {run_id}'s jobs could not be read ({exc}) "
              "— it is left out of the comparison", file=sys.stderr)
        return None
    return _entry(
        run_id=run_id,
        run_attempt=run.get("run_attempt") or 1,
        workflow_path=run.get("path"),
        head_sha=run.get("head_sha"),
        jobs=jobs,
        log=_log_or_empty(gh, run_id),
    )


def _log_or_empty(gh, run_id, attempt=None) -> str:
    """That run's failed-step log, or "" — never an exception.

    An unreadable log is not an unreadable run: a job GitHub concluded
    `timed_out` still says so through the jobs API, which is the half of the
    rule this failure must not take away.
    """
    try:
        return gh.failed_log(run_id, attempt)
    except Exception as exc:  # noqa: BLE001
        print(f"repair history: run {run_id}'s failed-step log could not be "
              f"read ({exc}) — its job conclusions still count",
              file=sys.stderr)
        return ""


def gather(*, repo: str, workflow_id, run_id, run_attempt: int, branch: str,
           workflow_path: str, head_sha: str, current_log: str, gh) -> dict | None:
    """The history document, or None when there is no usable one.

    None means exactly one thing: the CURRENT run's jobs could not be read, so
    there is no "same step" on this side to compare anything against and the
    document would be inert. Every other read failure narrows the history and
    the rest still stands.
    """
    try:
        current_jobs = _jobs(gh.jobs_of(run_id))
    except Exception as exc:  # noqa: BLE001
        print(f"repair history: this run's own jobs could not be read ({exc}) "
              "— no history is written, and the decision falls back to its "
              "behaviour without one", file=sys.stderr)
        return None

    prior: list = []

    # The previous ATTEMPT first: Portico retried every commit once and the
    # retry timed out identically, so the nearest evidence of a repeat is
    # inside this very run.
    if (run_attempt or 1) > 1:
        previous = (run_attempt or 1) - 1
        try:
            attempt_jobs = _jobs(gh.jobs_of(run_id, previous))
        except Exception as exc:  # noqa: BLE001
            print(f"repair history: attempt {previous} of this run could not "
                  f"be read ({exc}) — it is left out of the comparison",
                  file=sys.stderr)
        else:
            prior.append(_entry(
                run_id=run_id, run_attempt=previous,
                workflow_path=workflow_path, head_sha=head_sha,
                jobs=attempt_jobs,
                log=_log_or_empty(gh, run_id, previous),
            ))

    try:
        listing = gh.completed_runs(workflow_id, branch, LIST_PER_PAGE)
    except Exception as exc:  # noqa: BLE001
        print(f"repair history: the earlier runs of this workflow could not "
              f"be listed ({exc}) — only what is already gathered is written",
              file=sys.stderr)
        listing = []

    candidates = [
        run for run in listing or ()
        if isinstance(run, dict)
        # Never this run: it is a completed run of this workflow on this
        # branch and appears in its own listing, where it would match itself.
        and str(run.get("id")) != str(run_id)
        # A run that has not finished has half-written jobs and proves nothing.
        and (run.get("status") or "completed") == "completed"
    ][:PRIOR_RUNS]

    for run in candidates:
        entry = _prior_entry(gh, run)
        if entry is not None:
            prior.append(entry)

    return {
        "current": _entry(
            run_id=run_id, run_attempt=run_attempt or 1,
            workflow_path=workflow_path, head_sha=head_sha,
            jobs=current_jobs, log=current_log,
        ),
        "prior": prior,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _read_text(path: str) -> str:
    """The log the classifier already fetched. Missing is "" — the run's job
    conclusions still carry the other half of the rule."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather")
    g.add_argument("--repo", required=True)
    g.add_argument("--workflow-id", required=True)
    g.add_argument("--run-id", required=True)
    g.add_argument("--run-attempt", default="1")
    g.add_argument("--branch", required=True)
    g.add_argument("--workflow-path", default="")
    g.add_argument("--head-sha", default="")
    g.add_argument("--current-log", default="")
    g.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        attempt = int(args.run_attempt or 1)
    except ValueError:
        attempt = 1

    try:
        doc = gather(
            repo=args.repo,
            workflow_id=args.workflow_id,
            run_id=args.run_id,
            run_attempt=attempt,
            branch=args.branch,
            workflow_path=args.workflow_path,
            head_sha=args.head_sha,
            current_log=_read_text(args.current_log),
            gh=_Gh(args.repo),
        )
    except Exception as exc:  # noqa: BLE001 — see the module docstring: this
        # step may narrow the decision, never break it. A red main is already
        # the emergency; a gather that dies must not become a second one.
        print(f"repair history: gather failed ({exc}) — writing the "
              "no-history marker", file=sys.stderr)
        doc = None

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(FETCH_FAILED + "\n" if doc is None else json.dumps(doc))

    if doc is None:
        print("repair history: no history written", file=sys.stderr)
    else:
        print(f"repair history: {len(doc['prior'])} earlier run(s) gathered "
              f"for comparison", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
