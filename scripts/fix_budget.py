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

WHAT "BUDGET LEFT" MEANS, in fix mode (DRE-2817). It used to mean "fewer
than three attempt markers on the thread". It now means the loop has not had
`fix_convergence.STOP_BUDGET` CONSECUTIVE non-converging review rounds and
has not reached `fix_convergence.CEILING` attempts. A round that finds
something new, leaves the earlier fixes holding and stays in scope spends
nothing; a round that re-finds an earlier defect or reports a regression
spends one, so a circling loop now stops at two attempts where it used to
get three. Everything below is unchanged by that: the operator-decision
re-arm, the hand-dispatch noop, and the conflict budget (five rounds,
counted by attempt — main moving under a branch is not a convergence
question and the two budgets have been separate since PR #13).

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
    --hand-dispatch true|false --pr N [--critic-login L] [--env-out F]
    [--note-out F] [--summary-out F] [--classification-out F].
  --env-out    shell-sourceable ACTION/ATTEMPT/ATTEMPTS/REARMED/POST and
               STOPPED_BY/NONCONVERGING/STOP/CEILING lines, every value from
               a fixed vocabulary (a word or an integer), so sourcing it can
               never execute thread text.
  --note-out   the PR comment body to post, EMPTY when nothing should be
               posted (the notice is idempotent per answer).
  --classification-out
               the one-line convergence receipt (DRE-2817), appended to the
               attempt marker on a run and to the hold on a stop, so the
               pull request records every round's classification and "why
               did this stop" needs no reading of four verdicts. EMPTY in
               conflict mode, which is not a convergence question.
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
import fix_convergence

# The two budgets, kept separate on purpose (the PR #13 lesson): conflict
# churn from main moving must not consume the review budget. The markers are
# the comment bodies the Report step posts, counted by the worker identity
# only (DRE-1995 — a planted marker must never burn a budget).
#
# The number beside each marker is the ATTEMPT CEILING. For conflict mode it
# is the whole budget, unchanged. For fix mode it is only the runaway
# backstop since DRE-2817 — what normally stops that loop is consecutive
# non-convergence, decided by fix_convergence, and the ceiling exists so a
# loop that keeps producing new findings forever still ends.
BUDGETS = {
    "fix": ("🔧 Fix attempt", fix_convergence.CEILING),
    "conflict": ("🔀 Conflict resolution", 5),
}


class Outcome:
    """What this dispatch may do, and what it owes the PR and the run log."""

    def __init__(self, action, attempt, attempts, rearmed, note, summary,
                 classification="", stopped_by=None, streak=0, ceiling=0):
        self.action = action          # run | hold | noop
        self.attempt = attempt        # the 1-based number of THIS attempt
        self.attempts = attempts      # markers already on the thread
        self.rearmed = rearmed        # running on an operator decision
        self.note = note              # PR comment body, or None
        self.summary = summary        # one line for the run record
        self.classification = classification  # the DRE-2817 receipt line
        self.stopped_by = stopped_by  # None | non-convergence | ceiling
        self.streak = streak          # consecutive non-converging rounds
        self.ceiling = ceiling        # the attempt ceiling for this mode

    def env(self) -> str:
        return (
            f"ACTION={self.action}\n"
            f"ATTEMPT={self.attempt}\n"
            f"ATTEMPTS={self.attempts}\n"
            f"REARMED={'true' if self.rearmed else 'false'}\n"
            f"POST={'true' if self.note else 'false'}\n"
            f"STOPPED_BY={self.stopped_by or 'none'}\n"
            f"NONCONVERGING={self.streak}\n"
            f"STOP={fix_convergence.STOP_BUDGET}\n"
            f"CEILING={self.ceiling}\n"
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
    critic_login: str = fix_convergence.CRITIC_LOGIN,
) -> Outcome:
    """The whole rule (see the module docstring). Pure: every input is the
    thread, the mode and how the run was started."""
    marker, cap = BUDGETS[mode]
    attempts = count_markers(comments, worker_login, marker)
    where = f"PR #{pr}" if pr else "this PR"

    # WHAT SPENDS THE BUDGET, per mode. Fix mode measures convergence off the
    # critic's own verdicts (DRE-2817); conflict mode counts rounds, because
    # main moving under a branch says nothing about whether the work is
    # converging.
    if mode == "fix":
        state = fix_convergence.state(comments, attempts, critic_login)
        stopped_by, streak = state.stopped_by, state.streak
        classification = state.receipt()
        spent_because = state.hold_reason()
    else:
        stopped_by = "budget" if attempts >= cap else None
        streak = 0
        classification = ""
        spent_because = f"all {cap} rounds are spent"

    kw = dict(classification=classification, stopped_by=stopped_by,
              streak=streak, ceiling=cap)

    if stopped_by is None:
        room = (f"{cap - attempts} of {cap} attempts left" if mode != "fix"
                else f"{streak} of {fix_convergence.STOP_BUDGET} stop-budget "
                     f"spent, attempt {attempts + 1} of a {cap} ceiling")
        return Outcome(
            "run", attempts + 1, attempts, False, None,
            f"{mode} budget: {room} on {where}", **kw,
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
            f"NO WORK DONE: the {mode} loop on {where} stopped because "
            f"{spent_because}, and {why} — holding for a human decision.",
            **kw,
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
            **kw,
        )

    return Outcome(
        "run", attempts + 1, attempts, True, None,
        f"{mode} budget: re-armed by an operator decision on {where} "
        f"(attempt {attempts + 1}, one per answer)",
        **kw,
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
    # The critic identity, defaulted rather than passed: agent-fix.yml mints
    # only the worker App's token, so the fix job cannot derive this one from
    # an app-slug. The literal lives in fix_concurrency.py, on DRE-2120's
    # roster, and the flag exists so a test can drive a different one.
    parser.add_argument("--critic-login", default=fix_convergence.CRITIC_LOGIN)
    parser.add_argument("--env-out")
    parser.add_argument("--note-out")
    parser.add_argument("--summary-out")
    parser.add_argument("--classification-out")
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
        critic_login=args.critic_login,
    )
    _write(args.env_out, outcome.env())
    _write(args.note_out, outcome.note or "")
    _write(args.summary_out, outcome.summary + "\n")
    _write(args.classification_out, outcome.classification)
    # Counts and the decision only — never a body (DRE-1996 log-amplification).
    print(
        f"fix-budget: mode={args.mode} attempts={outcome.attempts} "
        f"action={outcome.action} rearmed={str(outcome.rearmed).lower()} "
        f"notice={'yes' if outcome.note else 'no'} "
        f"nonconverging={outcome.streak} stopped_by={outcome.stopped_by or 'none'}"
    )
    print(outcome.summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
