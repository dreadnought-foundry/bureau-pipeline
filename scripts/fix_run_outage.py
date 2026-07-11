#!/usr/bin/env python3
"""Fix-run outage guard: model-death is an outage, not an agent failure
(DRE-2018, stdlib only).

Origin (2026-07-10, DeltaSolv token-outage post-incident): when the fix
agent's model died mid-run ({"is_error": true} — API outage, exhausted
subscription), agent-fix's no-progress guard saw only "no new commit" and
posted "🛑 Fix attempt N pushed no new commit", parking the card
needs-human/Plan Review — an escalation that blames the fix agent and
misleads the CEO's queue. The agent-task path already distinguishes
model-death (check_agent_result + dead_run) and requeues; this gives
agent-fix's Report step the same split, PR-scoped:

  escalate — the model RAN and pushed nothing (genuine no-progress or a
             missing/unreadable result — can't prove an outage, fail-safe
             to today's behavior): the workflow's existing park shell.
  retry    — model died (is_error) under the outage cap: a plain-English
             "the AI service was unavailable — will retry automatically"
             comment tagged OUTAGE_TAG (reconcile.retry_outage_fixes
             re-dispatches on it, ~15 min later — which also gives the
             outage time to clear; an immediate self-requeue would die
             straight back into it). NO park, NO fix-attempt burned.
  hold     — is_error but the outage repeated cap+1 runs in a row: stop
             retrying into a dead service; park for a human with a comment
             that blames the OUTAGE, not the work. Deliberately UNtagged so
             the reconcile sweep stops dispatching and the loop terminates.

The comment bodies must never contain the fix loop's own counter markers
("🔧 Fix attempt", "pushed — CI and critic review re-running",
"🔀 Conflict resolution") — those strings ARE the attempt budgets and the
fix-vs-conflict router in agent-fix.yml's Resolve step — nor a verdict
marker (untrusted-content standard). Pinned by tests.

Called from agent-fix.yml's Report step (prior count is worker-bot-authored
comments containing OUTAGE_TAG — DRE-1995 discipline, forged markers must
not burn the cap):

    fix_run_outage.py decide <exec-json-path> <prior_outages> [--pr N]
        [--run-url U] [--pr-comment-out F] [--card-comment-out F]

Prints the action; writes the comment bodies only for retry/hold (a stale
file must never leak into the escalate arm's messaging).
"""

from __future__ import annotations

import sys

import check_agent_result

# Machine tag for the retry comment (house pattern: dead_run's DEAD_TAG).
# agent-fix counts prior outages by it and reconcile re-dispatches on it.
OUTAGE_TAG = "fix-outage-retry"
# The cap-reached comment's tag — must NOT contain OUTAGE_TAG as a substring.
HOLD_TAG = "fix-outage-held"
RETRY_CAP = 2  # retry at most twice (outages 1,2), hold on the 3rd — mirrors dead_run.REQUEUE_CAP


class Decision:
    """What the Report step should do about a no-progress fix run.

    action       — "escalate" (today's park shell) / "retry" / "hold"
    pr_comment   — body to post on the PR ("" for escalate)
    card_comment — body to post on the Linear card ("" for escalate)
    """

    def __init__(self, action: str, pr_comment: str = "", card_comment: str = ""):
        self.action = action
        self.pr_comment = pr_comment
        self.card_comment = card_comment


def decide(
    execution: dict | None,
    prior_outages: int,
    *,
    pr_number: str = "",
    run_url: str = "",
    cap: int = RETRY_CAP,
) -> Decision:
    """Split model-death from ran-and-pushed-nothing for a no-progress run.

    `execution` is the parsed Claude execution result (check_agent_result
    owns is_error detection — single source of truth with agent-task);
    `prior_outages` is the count of prior worker-bot OUTAGE_TAG comments.
    """
    if not check_agent_result.is_error_death(execution):
        return Decision("escalate")

    run_suffix = f" Run: {run_url}" if run_url else ""
    pr_ref = f"PR #{pr_number}" if pr_number else "this card's PR"
    if prior_outages >= cap:
        return Decision(
            "hold",
            f"🚨 {HOLD_TAG}: the AI service was unavailable for "
            f"{prior_outages + 1} fix runs in a row — pausing automatic "
            f"retries so the pipeline stops dying into an outage. This is a "
            f"service problem, not a fault in the work on this branch; retry "
            f"once service is back.{run_suffix}",
            f"🙋 The AI service was unavailable for {prior_outages + 1} fix "
            f"attempts in a row on {pr_ref} — that looks like an extended "
            f"outage, not a problem with the work. Once service is back, "
            f"move this card to **Todo** to retry or to **Backlog** to drop "
            f"it.{run_suffix}",
        )
    return Decision(
        "retry",
        f"⚠️ {OUTAGE_TAG}: the AI service was unavailable during this fix "
        f"run (outage {prior_outages + 1}/{cap + 1}) — the run died before "
        f"it could work, no changes were made, and no fix-attempt budget was "
        f"consumed. The pipeline will retry automatically on the next "
        f"reconcile sweep; no action needed.{run_suffix}",
        f"⚠️ The AI service was unavailable during the fix run on {pr_ref} — "
        f"the run died before it could work, so nothing changed. It will "
        f"retry automatically; no action needed.{run_suffix}",
    )


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "decide":
        print(
            "usage: fix_run_outage.py decide <exec-json-path> <prior_outages> "
            "[--pr N] [--run-url U] [--pr-comment-out F] [--card-comment-out F]"
        )
        return 2
    rest = argv[1:]
    opts = {}
    for flag, key in (
        ("--pr", "pr"),
        ("--run-url", "run_url"),
        ("--pr-comment-out", "pr_out"),
        ("--card-comment-out", "card_out"),
    ):
        if flag in rest:
            i = rest.index(flag)
            if i + 1 < len(rest):
                opts[key] = rest[i + 1]
            del rest[i : i + 2]
    exec_path = rest[0] if rest else ""
    prior = int(rest[1]) if len(rest) > 1 and rest[1].lstrip("-").isdigit() else 0

    d = decide(
        check_agent_result._load_execution(exec_path),
        prior,
        pr_number=opts.get("pr", ""),
        run_url=opts.get("run_url", ""),
    )
    if d.action != "escalate":
        for key, body in (("pr_out", d.pr_comment), ("card_out", d.card_comment)):
            if opts.get(key):
                with open(opts[key], "w") as f:
                    f.write(body)
    print(d.action)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
