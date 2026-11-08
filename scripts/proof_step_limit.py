#!/usr/bin/env python3
"""A proof step of the sweep ends at its own limit, green (DRE-9101).

Reconcile's `call / sweep` job ran out its 10-minute job limit 38 times
between 2026-10-25 and 2026-11-03, on every runner service. Each time a proof
step — `Dispatch proof runs` or `Proof overdue` — held a silent wait of 205 to
348 s, and the job died in whichever one was running when the ten minutes ran
out. A job-limit death concludes `cancelled` and counts as a hung job against
the machine it ran on, although the fault is the job's.

`run` bounds one proof command inside its step. It runs the command as a child
process, its output passed straight through, and stops it when the limit runs
out: SIGTERM, then SIGKILL `KILL_GRACE` seconds later if it is still running.
Both go to the child's whole process group, so a `gh` the proof script started
is stopped with it — the step pipes this script through `tee`, which ends only
when every writer of the pipe is gone. A child stopped this way is a CUT pass,
and the script prints, to stdout:

    ::warning title=Proof step cut at its limit::<name> was cut at its <n> s limit (DRE-9101); the next full sweep runs the pass again
    proof-step-limit: cut step="<name>" limit=<n>s

and exits 0. A child that ends by itself exits with its own status, so a real
failure of a proof script stays red. With `--until`, the limit is the seconds
left until that moment, and a command with no time left is not started and is
reported cut with `limit=0s`.

## Why a cut ends green

`medic.yml` fires on a run that concluded `failure` or `timed_out`, and
`medic_classify.py` has no class for a step that ran out its own limit. A cut
that failed its step would turn about a third of Reconcile runs red, and the
medic would re-run each one — a second full sweep — and then file a
"Pipeline failure" card. A cut pass is a stopped process, like a job-limit
death: the next full sweep runs the pass again.

## The alarm (`--alarm`, `Dispatch proof runs` only)

An 80 s limit cuts every dispatch pass that meets a long wait, and while the
wait is steady no proof run is dispatched. That is accepted, and told. After a
cut pass the script reads this repo's previous completed runs of the calling
workflow (`GITHUB_WORKFLOW_REF`), at most `LOOK_BACK_RUNS` of them, as
`GH_READ_TOKEN`, and takes the first `LOOK_BACK_PASSES` whose step of the same
name ran — a card-scoped pass, whose dispatch step was `skipped`, is passed
over. A previous pass counts as cut when that step ran for at least the
limit; no clean pass in the 83 sweeps read on 2026-11-03 ran past 31 s. It
prints

    proof-step-limit: dispatch cut on <k> of the last <m> full passes

and when this pass and the three before it were all cut it raises one WARN
console notice, key `proof-dispatch-cut:<repo slug>` (console kind
`pipeline_notice:proof-dispatch-cut:<repo slug>`), naming the four runs with
their PT start times. A dispatch pass that ends by itself resolves the notice,
as `read_fallback_alert.settle` resolves its own.

The look-back and the notice call share one `ALARM_TIMEOUT` (15 s). One that
fails, or does not answer in time, or a notice the door did not take, prints

    proof-step-limit: alarm UNKNOWN (<reason>)

raises nothing and never changes the step's status.

## The vendor boundary (`standards/vendor-boundaries.md`)

* Q1, the actor: the reconcile reusable already raises notices
  (`read-fallback:`, `standing-defect:`), and the console door admits it.
* Q2, the credentials: the look-back goes out as `GH_READ_TOKEN`, the pool
  App the sweep's reads ride; the job already holds `id-token: write`, which
  the notice client needs. A token that cannot read Actions is `alarm UNKNOWN`.
* Q3, a retry: a re-run raises the same key and the door answers `repeat`.
* Q4, the limits: the door refuses a key outside `^[A-Za-z0-9_.:-]{1,120}$`,
  and `proof-dispatch-cut:<slug>` is inside it.
* Q5, a crash mid-flow: a cut before the alarm leaves the notice as the last
  pass left it, and the next pass settles it.

## Usage

    python3 scripts/proof_step_limit.py run --step <name> \\
        (--limit <seconds> | --until <epoch seconds>) [--alarm] -- <command…>
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TAG = "proof-step-limit:"
#: Seconds between the SIGTERM and the SIGKILL.
KILL_GRACE = 5
#: The alarm's whole budget: the look-back and the notice call together.
ALARM_TIMEOUT = 15
#: How far back the look-back reads, and how many passes that ran it takes.
LOOK_BACK_RUNS = 20
LOOK_BACK_PASSES = 3
NOTICE_PREFIX = "proof-dispatch-cut"
SEVERITY = "WARN"
CARD = "DRE-9101"
_PT = ZoneInfo("America/Los_Angeles")


def notice_key(repo_slug: str) -> str:
    return f"{NOTICE_PREFIX}:{repo_slug}"


def _say(text: str) -> None:
    print(f"{TAG} {text}", flush=True)


# ── the limit ───────────────────────────────────────────────────────────────


def _signal_group(child: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(child.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_limited(command: list[str], limit: float) -> tuple[bool, int]:
    """`(cut, status)`: the command run as a child in its own process group,
    stopped when `limit` seconds have passed."""
    child = subprocess.Popen(command, start_new_session=True)  # nosec B603 — the step's own argv

    def forward(signum, _frame):
        # The runner stopping this step stops the child's group with it.
        _signal_group(child, signum)
        sys.exit(128 + signum)

    before = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            return False, _status(child.wait(timeout=limit))
        except subprocess.TimeoutExpired:
            pass
        _signal_group(child, signal.SIGTERM)
        try:
            child.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            pass
        # Always, after the grace: a grandchild that ignored the SIGTERM still
        # holds the step's pipe open after the child itself has gone.
        _signal_group(child, signal.SIGKILL)
        child.wait()
        return True, 0
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)


def _status(code: int) -> int:
    """A child killed by a signal of its own is the shell's 128 + n."""
    return 128 - code if code < 0 else code


def _print_cut(name: str, limit: int) -> None:
    print(f"::warning title=Proof step cut at its limit::{name} was cut at its {limit} s "
          f"limit ({CARD}); the next full sweep runs the pass again", flush=True)
    _say(f'cut step="{name}" limit={limit}s')


# ── the alarm ───────────────────────────────────────────────────────────────


class _Unknown(Exception):
    """The alarm could not read or say what it needed; the reason is printed."""


def _left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise _Unknown(f"no answer within {ALARM_TIMEOUT:g} s")
    return left


def _gh(deadline: float, *args: str):
    """One `gh` read as GH_READ_TOKEN, its JSON answer, inside the deadline."""
    env = dict(os.environ)
    token = os.environ.get("GH_READ_TOKEN")
    if token:
        env["GH_TOKEN"] = token
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, env=env,  # nosec B603 B607
                              timeout=_left(deadline), check=False)
    except subprocess.TimeoutExpired as e:
        raise _Unknown(f"gh {' '.join(args[:2])} did not answer within "
                       f"{ALARM_TIMEOUT:g} s") from e
    except OSError as e:
        raise _Unknown(f"gh could not run: {e}") from e
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise _Unknown(f"gh {' '.join(args[:2])} exited {done.returncode}"
                       + (f": {said[-1][:200]}" if said else ""))
    try:
        return json.loads(done.stdout)
    except ValueError as e:
        raise _Unknown(f"gh {' '.join(args[:2])} answered no JSON") from e


def _within(deadline: float, call):
    """`call()`'s answer, or `_Unknown` when it raises or outlasts the
    deadline — the thread is abandoned, and the process ends without it."""
    answer: dict = {}

    def work():
        try:
            answer["value"] = call()
        except BaseException as e:  # noqa: BLE001 — handed to the caller below
            answer["error"] = e

    worker = threading.Thread(target=work, daemon=True, name="proof-step-limit-notice")
    worker.start()
    worker.join(_left(deadline))
    if worker.is_alive():
        raise _Unknown(f"the notice call did not answer within {ALARM_TIMEOUT:g} s")
    if "error" in answer:
        error = answer["error"]
        raise _Unknown(f"the notice call failed: {type(error).__name__}: {error}")
    return answer["value"]


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _pt(stamp: str) -> str:
    when = _when(stamp).astimezone(_PT)
    return f"{when.strftime('%I:%M %p').lstrip('0')} PT on {when.strftime('%Y-%m-%d')}"


def _workflow() -> str:
    ref = os.environ.get("GITHUB_WORKFLOW_REF") or ""
    path = ref.split("@", 1)[0]
    if "/.github/workflows/" not in path:
        raise _Unknown("GITHUB_WORKFLOW_REF names no workflow file")
    return path.rsplit("/", 1)[-1]


def _step_seconds(view: dict, step: str) -> float | None:
    """How long the run's step ran, or None when it did not run."""
    for job in view.get("jobs") or ():
        for found in job.get("steps") or ():
            if found.get("name") != step:
                continue
            if found.get("conclusion") in (None, "", "skipped"):
                return None
            if not found.get("startedAt") or not found.get("completedAt"):
                return None
            return (_when(found["completedAt"]) - _when(found["startedAt"])).total_seconds()
    return None


def look_back(step: str, limit: int, deadline: float) -> tuple[dict, list[tuple[dict, bool]]]:
    """This run, and up to `LOOK_BACK_PASSES` previous `(run, cut)` whose
    `step` ran, newest first."""
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    if not repo:
        raise _Unknown("no REPO")
    this = str(os.environ.get("GITHUB_RUN_ID") or "")
    runs = _gh(deadline, "run", "list", "--repo", repo, "--workflow", _workflow(),
               "--limit", str(LOOK_BACK_RUNS),
               "--json", "databaseId,status,conclusion,createdAt")
    if not isinstance(runs, list):
        raise _Unknown("gh run list answered no list")
    current = next((r for r in runs if str(r.get("databaseId")) == this), None)
    if current is None:
        raise _Unknown(f"this run ({this or 'no GITHUB_RUN_ID'}) is not in the listing")
    passes: list[tuple[dict, bool]] = []
    for run in runs:
        if run is current or run.get("status") != "completed":
            continue
        view = _gh(deadline, "run", "view", str(run.get("databaseId")), "--repo", repo,
                   "--json", "jobs")
        seconds = _step_seconds(view if isinstance(view, dict) else {}, step)
        if seconds is None:
            continue
        passes.append((run, seconds >= limit))
        if len(passes) == LOOK_BACK_PASSES:
            break
    return current, passes


def _run_line(repo: str, run: dict) -> str:
    server = os.environ.get("GITHUB_SERVER_URL") or "https://github.com"
    run_id = run.get("databaseId")
    return f"- run {run_id}, started {_pt(run['createdAt'])}: {server}/{repo}/actions/runs/{run_id}"


def title(repo_slug: str, passes: int) -> str:
    return f"{repo_slug}'s proof runs have not been dispatched for {passes} sweeps in a row"


def body(repo_slug: str, repo: str, runs: list[dict], step: str, limit: int) -> str:
    named = "\n".join(_run_line(repo, run) for run in runs)
    return (f"`{step}` was cut at its {limit} s limit on each of the last {len(runs)} full "
            f"{repo_slug} sweeps ({CARD}):\n\n{named}\n\nA cut pass dispatches nothing, so "
            "no proof run is started while this stands. This alert resolves on the first "
            "dispatch pass that ends by itself.")


def alarm_after_cut(step: str, limit: int, repo_slug: str) -> None:
    deadline = time.monotonic() + ALARM_TIMEOUT
    try:
        current, passes = look_back(step, limit, deadline)
        cut = 1 + sum(1 for _, was_cut in passes if was_cut)
        _say(f"dispatch cut on {cut} of the last {1 + len(passes)} full passes")
        if len(passes) < LOOK_BACK_PASSES or cut < 1 + LOOK_BACK_PASSES:
            return
        import pipeline_notice  # noqa: PLC0415 — the console only when it is told
        repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
        runs = [current] + [run for run, _ in passes]
        outcome = _within(deadline, lambda: pipeline_notice.raise_notice(
            notice_key(repo_slug), severity=SEVERITY, title=title(repo_slug, len(runs)),
            body=body(repo_slug, repo, runs, step, limit), fallback=None))
        if outcome == "lost":
            raise _Unknown("notice lost")
    except _Unknown as e:
        _say(f"alarm UNKNOWN ({e})")


def alarm_after_clean(repo_slug: str) -> None:
    deadline = time.monotonic() + ALARM_TIMEOUT
    try:
        import pipeline_notice  # noqa: PLC0415 — the console only when it is told
        outcome = _within(deadline, lambda: pipeline_notice.resolve_notice(notice_key(repo_slug)))
        if outcome == "resolve-refused":
            raise _Unknown("notice resolve-refused")
    except _Unknown as e:
        _say(f"alarm UNKNOWN ({e})")


def _repo_slug() -> str:
    slug = os.environ.get("REPO_SLUG")
    if slug:
        return slug
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    return repo.rsplit("/", 1)[-1].lower()


# ── the CLI ─────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proof_step_limit.py")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run one proof command under its step's limit")
    run.add_argument("--step", required=True, help="the step's name, as the cut line says it")
    bound = run.add_mutually_exclusive_group(required=True)
    bound.add_argument("--limit", type=int, help="seconds the command may run")
    bound.add_argument("--until", type=int, help="the epoch second the command must end by")
    run.add_argument("--alarm", action="store_true",
                     help="tell a dispatch cut pass after pass (Dispatch proof runs only)")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not command:
        _parser().error("run needs a command after --")
    # `--until` waits out exactly the time left, and says it in whole
    # seconds, rounded up: 119.4 s left of a 120 s budget is `limit=120s`.
    left = args.until - time.time() if args.until is not None else float(args.limit)
    limit = max(0, math.ceil(left))
    if left <= 0:
        cut, status = True, 0
    else:
        cut, status = run_limited(command, left)
    if cut:
        _print_cut(args.step, limit)
    if args.alarm:
        if cut:
            alarm_after_cut(args.step, limit, _repo_slug())
        else:
            alarm_after_clean(_repo_slug())
    return status


if __name__ == "__main__":
    sys.exit(main())
