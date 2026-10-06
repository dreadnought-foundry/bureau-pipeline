#!/usr/bin/env python3
"""Which run conclusions wake the Pipeline Medic (DRE-3625). Stdlib only.

The reusable medic (`.github/workflows/medic.yml`) is woken by a
`workflow_run` event for every completed pipeline run and decides, job by job,
whether the run it was woken for needs it at all. That rule used to be the
literal `github.event.workflow_run.conclusion == 'failure'`, written in nine
job `if:` sites and declared nowhere — and one short: a run that concluded
`timed_out` never woke the medic, though it died as surely as a failure did.

The operator's decision (DRE-3530, 2026-09-10): wake on `failure` and
`timed_out`; stay silent on `cancelled` — a sweep superseded by its own
concurrency group, which nothing is wrong with — as on `success` and
`skipped`.

One cancellation is not left to nobody (DRE-5901): a default-branch CI run
whose jobs GitHub cancelled "not acquired by Runner" is re-run once by the
reconcile sweep (`scripts/runner_lost.py`), which runs in every repo — the
medic's door stays as it is.

A workflow cannot import this module, so it reads it the only way it can:
`expression()` renders the GitHub Actions expression, every medic gate
carries the rendered text byte for byte, and `tests/test_medic_wake_set.py`
holds the two to each other. The shape is a parenthesized `||` group rather
than `contains(fromJSON(...))` because `tests/test_medic_environment_hold.py`
replays the medic's gates through an evaluator that reads exactly that shape
and raises on a function call.

    python3 scripts/medic_wake.py     # prints the expression
"""

from __future__ import annotations

# The conclusions that wake the medic, in the order the expression reads them.
WAKE_ON = ("failure", "timed_out")

# Every conclusion the medic stays silent on, and why.
SILENT_ON = {
    "success": "the run did what it was for; there is nothing to repair",
    "skipped": "the run chose not to run; a skip is green, not a death",
    "cancelled": (
        "a sweep superseded by its own concurrency group; the newer run "
        "carries the work (operator, DRE-3530, 2026-09-10)"
    ),
}

_FIELD = "github.event.workflow_run.conclusion"


def expression() -> str:
    """The GitHub Actions expression that is true exactly on `WAKE_ON`."""
    return "(" + " || ".join(f"{_FIELD} == '{c}'" for c in WAKE_ON) + ")"


if __name__ == "__main__":
    print(expression())
