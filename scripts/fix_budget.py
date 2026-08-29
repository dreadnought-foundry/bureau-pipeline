#!/usr/bin/env python3
"""Decide what a dispatched fix run may do on a held PR (DRE-2813).

Origin (2026-08-29, PR #199 / DRE-2721): the two documented ways to release a
held PR cancelled each other out, and doing both left the PR held forever with
no notice.

    04:17:37  🛑 Fix budget exhausted — held for a human decision
    15:27:14  a human posts **Operator decision** — the sweep is now armed
    15:29:35  a hand `workflow_dispatch` of agent-fix: the budget is still
              spent, so the run fixes nothing and concludes `success`
    15:29:46  that run posts a FRESH 🛑 hold as the worker bot
    15:33:55  reconcile.restart_answered_blockers sees a worker-bot comment
              newer than the answer and correctly stands down — permanently

Every component behaved as designed. What was wrong is that a dispatch which
could not do any work still manufactured a bot comment that outranked a
standing answer, and it punished exactly the right instinct: answering AND
pushing the button left the operator worse off than answering alone.

THE RULE, in one place, read by the fix job through the CLI below:

  * budget left            → run (unchanged)
  * budget spent, no answer standing or already used
                           → hold (unchanged: the 🛑 the workflow posts)
  * budget spent, an answer the loop has not acted on, dispatched BY HAND
                           → noop. Post no hold. Post one tagged notice
                             (fix_context.NOOP_TAG) that the arming rule
                             ignores, saying the sweep owns this restart.
  * budget spent, an answer the loop has not acted on, dispatched by the
    pipeline (the reconcile sweep, github-actions)
                           → run, on ONE re-armed attempt. Without this the
                             restart the noop defers to would itself find the
                             budget spent and repeat the same hold, and "the
                             sweep will act" would be a promise the loop
                             cannot keep. Exactly one: a further attempt after
                             the same answer holds again, so an answer buys
                             one attempt the way it buys one dispatch.

Hand-vs-pipeline is the caller's to determine; the workflow reads it from the
event (`gh workflow run` under a workflow's github.token initiates as
`github-actions` — DRE-2053), never from anything a commenter can write.

Contract with agent-fix.yml:
  argv: decide --comments-file (the raw REST payload of
    GET /repos/{repo}/issues/{pr}/comments — flat, or the array-of-pages
    `gh api --paginate --slurp` emits) --worker-login --mode fix|conflict
    --hand-dispatch true|false --pr N [--env-out F] [--note-out F]
    [--summary-out F].
  --env-out    shell-sourceable ACTION/ATTEMPT/ATTEMPTS/REARMED/POST lines,
               every value from a fixed vocabulary (a word or an integer), so
               sourcing it can never execute thread text.
  --note-out   the PR comment body to post, EMPTY when nothing should be
               posted (the notice is idempotent per answer).
  --summary-out the one-line run-record note; on a refusal it opens with
               "NO WORK DONE:" so a dispatch that did nothing cannot read as
               a plain success.
  exit 0 = decided; exit 2 = malformed input (loud — a silently absent thread
    is how a held PR looks exactly like an unanswered one).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

import fix_context

# The two budgets, kept separate on purpose (the PR #13 lesson): conflict
# churn from main moving must not consume the review budget. The markers are
# the comment bodies the Report step posts, counted by the worker identity
# only (DRE-1995 — a planted marker must never burn a budget).
BUDGETS = {
    "fix": ("🔧 Fix attempt", 3),
    "conflict": ("🔀 Conflict resolution", 5),
}


class Outcome:
    """What this dispatch may do, and what it owes the PR and the run log."""

    def __init__(self, action, attempt, attempts, rearmed, note, summary):
        self.action = action          # run | hold | noop
        self.attempt = attempt        # the 1-based number of THIS attempt
        self.attempts = attempts      # markers already on the thread
        self.rearmed = rearmed        # running on an operator decision
        self.note = note              # PR comment body, or None
        self.summary = summary        # one line for the run record

    def env(self) -> str:
        return (
            f"ACTION={self.action}\n"
            f"ATTEMPT={self.attempt}\n"
            f"ATTEMPTS={self.attempts}\n"
            f"REARMED={'true' if self.rearmed else 'false'}\n"
            f"POST={'true' if self.note else 'false'}\n"
        )


def _is_worker(c: dict, worker_login: str) -> bool:
    return (c.get("user") or {}).get("login") == worker_login


def count_markers(comments, worker_login: str, marker: str, start: int = 0) -> int:
    """Budget markers on the thread, WORKER-AUTHORED ONLY (DRE-1995): any
    commenter can mimic "🔧 Fix attempt" or "🔀 Conflict resolution", and a
    forged one would burn a budget and park the card. Pinned by
    tests/test_agent_fix_identity_gate.py, which follows this counter here
    from the workflow's inline jq."""
    return sum(
        1
        for c in comments[start:]
        if _is_worker(c, worker_login) and marker in (c.get("body") or "")
    )


def _index_of(comments, comment) -> int:
    return next((i for i, c in enumerate(comments) if c is comment), -1)


def _already_noticed(comments, worker_login: str, since: int) -> bool:
    return any(
        fix_context.is_noop_notice(c, worker_login) for c in comments[since + 1 :]
    )


def _notice(pr: Optional[int], standing: bool) -> str:
    """The PR comment a no-work dispatch posts INSTEAD of a repeat hold.

    It has to tell the two states of a held PR apart — "already answered, the
    sweep will act" and "still waiting for an answer" — because a repeated
    hold made the first look exactly like the second."""
    where = f"PR #{pr}" if pr else "this PR"
    if standing:
        next_step = (
            "The pipeline sweep has it: it reads your answer on its next pass "
            "and starts the fix loop for you, normally within about 15 "
            "minutes. Nothing more is needed from you."
        )
    else:
        next_step = (
            "The pipeline sweep has already picked your answer up and a fix "
            "run is on its way. Nothing more is needed from you."
        )
    return (
        f"🟡 {fix_context.NOOP_TAG}: this Agent Fix run did nothing, and that "
        "is the correct outcome.\n\n"
        f"The fix budget for {where} is spent AND an operator decision is "
        f"already standing here, so a hand dispatch has no attempt to add. "
        f"{next_step}\n\n"
        "This notice is deliberately the one comment the restart sweep "
        "ignores (DRE-2813), so it cannot cancel your answer the way a "
        "repeated hold comment would. See docs/held-pr-recovery.md."
    )


def decide(
    comments,
    worker_login: str,
    mode: str = "fix",
    hand_dispatch: bool = False,
    pr: Optional[int] = None,
) -> Outcome:
    """The whole rule (see the module docstring). Pure: every input is the
    thread, the mode and how the run was started."""
    marker, cap = BUDGETS[mode]
    attempts = count_markers(comments, worker_login, marker)
    where = f"PR #{pr}" if pr else "this PR"

    if attempts < cap:
        return Outcome(
            "run", attempts + 1, attempts, False, None,
            f"fix budget: attempt {attempts + 1} of {cap} on {where}",
        )

    # Budget spent. An operator decision is new input — the human act the
    # hold was written to ask for — but only until the loop acts on it.
    decision = fix_context.operator_decision(comments, worker_login)
    used = (
        count_markers(
            comments, worker_login, marker, _index_of(comments, decision) + 1
        )
        if decision is not None
        else 0
    )
    if decision is None or used:
        why = (
            "no operator decision is standing"
            if decision is None
            else "the standing operator decision already bought an attempt"
        )
        return Outcome(
            "hold", attempts + 1, attempts, False, None,
            f"NO WORK DONE: the {mode} budget for {where} is spent and {why} "
            "— holding for a human decision.",
        )

    if hand_dispatch:
        standing = fix_context.standing_decision(comments, worker_login) is not None
        at = _index_of(comments, decision)
        note = (
            None
            if _already_noticed(comments, worker_login, at)
            else _notice(pr, standing)
        )
        return Outcome(
            "noop", attempts + 1, attempts, False, note,
            f"NO WORK DONE: an operator decision is already standing on "
            f"{where} — the reconcile sweep owns this restart, so this hand "
            "dispatch changed nothing.",
        )

    return Outcome(
        "run", attempts + 1, attempts, True, None,
        f"fix budget: re-armed by an operator decision on {where} "
        f"(attempt {attempts + 1}, one per answer)",
    )


def _write(path: Optional[str], text: str) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["decide"])
    parser.add_argument("--comments-file", required=True)
    parser.add_argument("--worker-login", required=True)
    parser.add_argument("--mode", choices=sorted(BUDGETS), default="fix")
    parser.add_argument("--hand-dispatch", default="false")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--env-out")
    parser.add_argument("--note-out")
    parser.add_argument("--summary-out")
    args = parser.parse_args(argv)

    try:
        with open(args.comments_file, encoding="utf-8") as fh:
            comments = fix_context.flatten_pages(json.load(fh))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"fix_budget: malformed comments payload: {exc}", file=sys.stderr)
        return 2

    outcome = decide(
        comments,
        args.worker_login,
        mode=args.mode,
        hand_dispatch=args.hand_dispatch == "true",
        pr=args.pr,
    )
    _write(args.env_out, outcome.env())
    _write(args.note_out, outcome.note or "")
    _write(args.summary_out, outcome.summary + "\n")
    # Counts and the decision only — never a body (DRE-1996 log-amplification).
    print(
        f"fix-budget: mode={args.mode} attempts={outcome.attempts} "
        f"action={outcome.action} rearmed={str(outcome.rearmed).lower()} "
        f"notice={'yes' if outcome.note else 'no'}"
    )
    print(outcome.summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
