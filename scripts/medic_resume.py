#!/usr/bin/env python3
"""The note a resume leaves (DRE-7526). Stdlib only.

When a pipeline workflow fails twice — the original run and the medic's one
automatic retry — the medic's `diagnose` job runs a diagnosis agent. Until
DRE-7526 every diagnosis was delivered as `run-failure-diagnosed`, a hold
whose next actor is the operator, even when the diagnosis said the cause was
a passing outside fault: 201 stops in the week of weekly report DRE-7512, the
biggest single pile of waiting time.

So the agent also writes the failure's class to `.bureau-pipeline/
medic-verdict.txt`, one of the five `medic_next_step.CLASSES` (DRE-7793;
it wrote `resume` or `hold` until then). `medic_next_step.py read` and
`decide` turn the class into one step, and `transient-infrastructure` — an
outside fault a fresh attempt would clear — owes `requeue-once`.

On `requeue-once` the `diagnose` job re-runs the failed jobs of the same run
once more, and only on its second attempt (`run_attempt == 2`, in the step's
own `if:`), so attempt 2 becomes attempt 3 and never further. `summary()`
writes the report into the medic run's step summary, naming the attempt the
rerun starts. That attempt is the failed run's attempt plus one, computed
here rather than read back from GitHub: the rerun has not begun when the note
is written. Since DRE-7799 a `card` target also gets the diagnosis and the
medic's `medic-next-step` receipt on the card; any other target gets nothing
posted, as before.

A card still waiting out a limit or a vendor refusal is the wait's
(DRE-7799): the medic posts nothing on it, moves nothing and starts no
re-run, and `summary(..., waiting=<card>)` is the note it leaves instead.

    python3 scripts/medic_resume.py summary --report .bureau-pipeline/medic-report.md \\
        --attempt 2 --run-url <failed run url> --workflow "<workflow name>" \\
        [--waiting <card>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def resumed_attempt(failed_attempt: int) -> int:
    """The attempt a rerun of `failed_attempt` starts."""
    return failed_attempt + 1


def summary(report: str, *, failed_attempt: int, run_url: str, workflow: str,
            waiting: str | None = None) -> str:
    """The step-summary note a resume leaves: one line naming the attempt it
    started, then the diagnosis report whole. With `waiting`, the card whose
    limit-death marker still stands, the line says the medic left that card
    alone instead."""
    if waiting:
        head = (
            f"Waiting: {waiting} is waiting out a limit or a vendor refusal — "
            f"its newest limit-death marker still stands — so the medic posted "
            f"nothing on it, moved nothing and started no re-run of {workflow} "
            f"run {run_url}. Limit recovery re-enters the stage that died once "
            f"the wait is over, and that run's result is the one that counts."
        )
    else:
        attempt = resumed_attempt(failed_attempt)
        head = (
            f"Resumed: the medic re-ran the failed jobs of {workflow} run {run_url} "
            f"as attempt {attempt}. The diagnosis read the failure as an outside "
            f"service fault a fresh attempt can clear, so the run was re-run "
            f"rather than held for a person. If attempt {attempt} fails, its "
            f"diagnosis is delivered as usual."
        )
    body = report.strip() or "The diagnosis agent wrote no report."
    return f"{head}\n\n{body}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary", help="print the step-summary note for a resume")
    s.add_argument("--report", required=True)
    s.add_argument("--attempt", type=int, required=True,
                   help="the failed run's attempt")
    s.add_argument("--run-url", required=True)
    s.add_argument("--workflow", required=True)
    s.add_argument("--waiting", default=None,
                   help="the card left alone because its wait still stands")
    args = parser.parse_args(argv)

    try:
        report = Path(args.report).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        report = ""
    sys.stdout.write(summary(report, failed_attempt=args.attempt,
                             run_url=args.run_url, workflow=args.workflow,
                             waiting=args.waiting))
    return 0


if __name__ == "__main__":
    sys.exit(main())
