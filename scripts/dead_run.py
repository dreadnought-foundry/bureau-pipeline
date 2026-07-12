#!/usr/bin/env python3
"""Dead-run requeue + hold-cap decision, unified across death classes (stdlib).

A card whose agent dies with NO PR is requeued at most REQUEUE_CAP times, then
HELD for a human (Backlog + needs-human label) so the pipeline stops looping
(DRE-1403). Three death classes share ONE cap, counted by the `dead-run-requeue`
comment tag:

  - silent  : ran out of turns with no PR/blocker (agent-task Report step)
  - hung     : timed out, never reached Report (reconcile sweep)
  - is_error : an API/model death mid-run (DRE-1354) — PREVIOUSLY this failed the
               job and the medic re-ran it on the SAME model, bypassing the cap,
               so DRE-1300 looped 18×. Now an is_error death counts toward the
               same cap AND records which model died (`model-error:`), so the
               requeue's next attempt selects the ALTERNATE model
               (see model_fallback.py).

A CANCELLED run is NOT a death class (DRE-2074): when the agent step's outcome
is `cancelled` (the job timeout, or an external/concurrency cancel), the agent
was killed while still working — it did not die. The old code read the
`always()` Report step's "no PR, no blocker" as a silent death right at the
45-minute job timeout and parked healthy long builds (DRE-2070 was killed 4×
mid-work, hold posted at the 45-minute mark while the run was in_progress on
GitHub with a ⏳ receipt 6 minutes old). decide(cancelled=True) returns the
"defer" action: ONE informational comment WITHOUT the DEAD_TAG (it must not
increment the shared cap), no state move, no hold label — regardless of the
prior count. The reconcile sweep's authoritative run-status check (DRE-2032)
owns the requeue once the run has actually CONCLUDED without a PR: dead-run
handling as today, never over a live run.

This module is the no-I/O core that decides — given the prior dead count and the
death class — whether to REQUEUE (→ Todo) or HOLD (→ Backlog + needs-human), and
what comment(s) to post. The workflow does the Linear writes; the decision is
unit-tested here so the "is_error counts toward the cap" regression is pinned.
"""

from __future__ import annotations

import sys

DEAD_TAG = "dead-run-requeue"
HOLD_LABEL = "needs-human"
REQUEUE_CAP = 2  # requeue at most twice (attempts 1,2,3), then hold

# model_fallback writes the same prefix; kept in sync via the shared constant.
ERROR_MARKER_PREFIX = "model-error:"


class Decision:
    """What to do about a dead run.

    action   — "requeue" (→ Todo), "hold" (→ Backlog + needs-human label), or
               "defer" (cancelled run: post the receipt, change NOTHING —
               the reconcile sweep requeues off the run's real conclusion)
    comments — comment bodies to post, in order (each one that contains DEAD_TAG
               also increments the shared cap for the NEXT death)
    """

    def __init__(self, action: str, comments: list[str]):
        self.action = action
        self.comments = comments

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Decision)
            and self.action == other.action
            and self.comments == other.comments
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Decision({self.action!r}, {self.comments!r})"


def decide(
    prior_dead: int,
    *,
    is_error: bool = False,
    error_model: str | None = None,
    cancelled: bool = False,
    run_url: str = "",
    cap: int = REQUEUE_CAP,
) -> Decision:
    """Decide requeue-vs-hold for a death given the prior `dead-run-requeue`
    count on the card.

    `is_error`/`error_model`: this death was an API/model error on `error_model`
    — record a `model-error:` marker so the requeue switches models, and (the
    DRE-1354 contract) count it toward the SAME cap as silent/hung deaths.

    `cancelled` (DRE-2074): the agent step was cancelled — killed by the job
    timeout or an external cancel while still working, NOT a death. Wins over
    every other input, including a prior count at the cap: the answer is
    always "defer" with a no-DEAD_TAG receipt, and the reconcile sweep
    requeues (with the existing cap) only after the run has actually
    concluded without a PR.
    """
    run_suffix = f" Run: {run_url}" if run_url else ""
    if cancelled:
        # The receipt must not carry DEAD_TAG (it would increment the shared
        # cap) and must not start with a ⏳/🧠 proof-of-life prefix (it would
        # suppress the reconcile sweep's eventual requeue).
        return Decision(
            "defer",
            [
                "🤖 run cancelled mid-build (GitHub job timeout or an external "
                "cancel) — the agent was killed while still working, so this "
                "does NOT count as a dead run (DRE-2074). If the run concluded "
                "without a PR, the reconcile sweep requeues it from GitHub's "
                f"own conclusion — never over a live run.{run_suffix}"
            ],
        )
    cause = (
        "API/model error (is_error)"
        if is_error
        else "no PR and no blocker note"
    )
    error_marker_line = ""
    if is_error and error_model:
        # Standalone marker so model_fallback.select_model picks the alternate
        # on the next attempt; on its OWN line so it survives any later edit.
        error_marker_line = f"\n{ERROR_MARKER_PREFIX} {error_model}"

    if prior_dead >= cap:
        names = ""
        if is_error and error_model:
            names = f" (last model tried: {error_model})"
        return Decision(
            "hold",
            [
                f"🚨 held-for-human ({DEAD_TAG} cap reached): agent died with "
                f"{cause} for the {prior_dead + 1}th time{names} — parked in "
                f"Backlog with the '{HOLD_LABEL}' label so the relay and the "
                f"reconcile sweep stop looping. A human must split/fix the card "
                f"and clear the label to retry.{run_suffix}{error_marker_line}"
            ],
        )
    return Decision(
        "requeue",
        [
            f"🪦 {DEAD_TAG}: agent died with {cause} — requeued to Todo for a "
            f"fresh attempt (dead run {prior_dead + 1}/{cap + 1})."
            f"{run_suffix}{error_marker_line}"
        ],
    )


def main(argv: list[str]) -> int:
    """CLI for the workflow:

      decide <prior_dead> [--is-error] [--error-model M] [--cancelled] [--run-url U]

    Prints (to stdout) the action on the first line, then a blank line, then the
    comment body. The workflow reads line 1 for the branch and posts the body.
    """
    if not argv:
        print("usage: dead_run.py decide <prior_dead> [--is-error] "
              "[--error-model M] [--cancelled] [--run-url U]")
        return 2
    cmd, *rest = argv
    if cmd != "decide":
        print(f"unknown command {cmd!r}")
        return 2
    prior_dead = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else 0
    is_error = "--is-error" in rest
    cancelled = "--cancelled" in rest
    error_model = None
    run_url = ""
    for flag, target in (("--error-model", "model"), ("--run-url", "url")):
        if flag in rest:
            i = rest.index(flag)
            if i + 1 < len(rest):
                if target == "model":
                    error_model = rest[i + 1]
                else:
                    run_url = rest[i + 1]
    d = decide(
        prior_dead,
        is_error=is_error,
        error_model=error_model,
        cancelled=cancelled,
        run_url=run_url,
    )
    print(d.action)
    print()
    print(d.comments[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
