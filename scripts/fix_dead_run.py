#!/usr/bin/env python3
"""Model-death decision for agent-fix's no-progress guard (DRE-2018, stdlib).

Origin (2026-07-10, DeltaSolv token outage): when the model died mid-fix-run
(execution result {"is_error": true} — API outage, exhausted subscription),
agent-fix's post-run guard saw "no new commit", posted "🛑 Fix attempt N
pushed no new commit" and parked the card needs-human/the human-park lane — an
escalation that blames the fix agent and misleads the CEO's queue. The
agent-task path already distinguishes model-death (check_agent_result +
dead_run's requeue cap); this module is the fix-loop counterpart.

Called from agent-fix.yml's Report step when the head SHA did not advance:

    python3 fix_dead_run.py decide <execution-json-path> \
        --comments-json <pr-comments-json> [--run-url U]

The retry cap is scoped to CONSECUTIVE deaths since the last successful push
(consecutive_prior_deaths), not every death marker the PR ever carried — a
recovered outage episode must not pre-exhaust the cap for a fresh one.

Prints the action on line 1, a blank line, then the PR comment body (empty
for "escalate" — the workflow keeps its own escalation text):

  escalate — the model RAN and still pushed nothing (or there is no result
             file to prove otherwise): today's park-for-human path, unchanged.
  retry    — API/model death under the cap: post the OUTAGE_TAG marker comment;
             the reconcile sweep re-dispatches the fix agent on it (nothing
             event-driven re-fires agent-fix once the qa-bot's
             REQUEST_CHANGES trigger is consumed). No fix-attempt budget is
             burned and the card is NOT parked.
  hold     — the death after RETRY_CAP straight deaths (the medic's cap
             pattern): park for a human with honest outage wording. The hold
             comment deliberately OMITS the marker (it must not count itself
             into the next DEATHS read, and it must be the newest worker-bot
             comment so the sweep stops) and OPENS with 🛑 so fix_context.py
             shows it to any later fix run as an unanswered blocker.
  retry-turns / hold-turns — the same two shapes for a run that RAN OUT OF
             TURNS (DRE-2312), on their own marker and their own budget.

TURN EXHAUSTION IS NOT AN OUTAGE (DRE-2312). Until this card, every is_error
death routed through the outage path above: portico PR #170 spent 16 minutes,
hit the 60-turn cap resolving a three-hunk conflict, and the card was told
"🛑 The AI service failed 3 fix runs in a row … died with an API/model error
each time". PR #234 spent ~$15 across three 61-turn runs, each announced as
"the AI service was unavailable… No fix-attempt budget was used" — a claim
that was false twice over and defeated the breaker it was reporting to.

What turn exhaustion IS: a failed attempt. It is reported honestly (the cap,
the turns, the dollars), it consumes budget, and it is retried at MOST ONCE.
Once, not zero: the two DRE-2695 attempts ran the same card on the same model
and diverged by nearly ten minutes at the same milestone — agent runs are
stochastic and the cap is a race the second attempt can win. Once, not twice:
by the second exhaustion the evidence says the task exceeds one run, and a
third identical attempt just buys another $5 of the same wall.

check_agent_result.py stays the single source of truth for reading AND
classifying the execution result (list-shaped payload tolerance, and the
turn-exhaustion / API-death split every caller shares).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_agent_result  # noqa: E402

# The worker-bot PR-comment marker the reconcile sweep re-dispatches on and
# the Report step counts toward the cap. Must never collide with the markers
# other reads route on ("🔧 Fix attempt", "🔀 Conflict resolution", the
# "pushed — CI and critic review re-running" push marker, a leading 🛑).
OUTAGE_TAG = "fix-run-model-death"
# The turn-exhaustion counterpart (DRE-2312). Its own marker, its own budget:
# the two classes have different causes and different remedies, so a fix run
# that ran out of steps must neither spend nor read the outage budget. As with
# DEAD_TAG/RESET_TAG in dead_run.py, counting is substring-based — neither tag
# may contain the other, and tests/test_turn_exhaustion_not_outage.py pins it.
TURN_CAP_TAG = "fix-run-turn-exhaustion"
# Both retry markers, for the reconcile sweep: the newest worker-bot comment
# carrying either one is a promised retry nothing else will fire.
RETRY_MARKERS = (OUTAGE_TAG, TURN_CAP_TAG)
# Substring shared by BOTH worker-bot push markers ("🔧 Fix attempt N pushed…"
# and "🔀 Conflict resolution round N pushed…"). A push means the branch moved
# forward — it clears the current run of consecutive deaths.
PUSH_MARKER = "pushed — CI and critic review re-running"
# The worker (dispatch) bot identity, sans the "[bot]" login suffix. Only its
# comments count toward the cap or clear it (DRE-1995 discipline).
WORKER_LOGIN = "agent-bureau-bot"
RETRY_CAP = 2  # retry at most twice (deaths 1,2), then hold on the 3rd
# Turn exhaustion gets ONE retry (exhaustion 1), then holds on the 2nd. The
# variance between two attempts is real and worth one coin-flip; a third
# identical attempt is not (DRE-2312).
TURN_RETRY_CAP = 1


def _comment_login(comment: dict) -> str:
    """Login of a REST/GraphQL comment record ('' if absent), suffix intact."""
    for key in ("user", "author"):
        who = comment.get(key)
        if isinstance(who, dict) and who.get("login"):
            return who["login"]
    return ""


def consecutive_prior_markers(
    comments: list | None, tag: str, *, worker_login: str = WORKER_LOGIN
) -> int:
    """Count worker-bot `tag` markers SINCE the last successful push.

    DRE-2018 review finding: the retry cap must fire on three deaths *in a
    row*, not on every death marker the PR has ever carried. A recovered
    outage episode (deaths that were retried, then a push that succeeded)
    must not pre-exhaust the cap for a fresh, unrelated outage weeks later.

    Walk newest→oldest; a worker-bot push marker ends the run (return what we
    have). Only worker-bot-authored comments are considered — a forged marker
    or a forged push from any other identity is transparent (DRE-1995).

    Parameterised by tag (DRE-2312) so outage deaths and turn exhaustions are
    counted on separate budgets by the same rules."""
    count = 0
    for comment in reversed(comments or []):
        if _comment_login(comment).removesuffix("[bot]") != worker_login:
            continue
        body = comment.get("body") or ""
        if PUSH_MARKER in body:
            break  # a successful push clears the consecutive run
        if tag in body:
            count += 1
    return count


def consecutive_prior_deaths(
    comments: list | None, *, worker_login: str = WORKER_LOGIN
) -> int:
    """Consecutive worker-bot API/model deaths since the last push."""
    return consecutive_prior_markers(
        comments, OUTAGE_TAG, worker_login=worker_login
    )


def _load_comments(path: str) -> list:
    """Read the REST issues/comments JSON array; [] on any read/parse error."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


class Decision:
    """What to do about a fix run that pushed no new commit.

    action  — "escalate" (today's park-for-human), "retry" (outage marker,
              no park, no budget burn) or "hold" (outage cap reached)
    comment — PR comment body for retry/hold; "" for escalate
    """

    def __init__(self, action: str, comment: str):
        self.action = action
        self.comment = comment

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Decision)
            and self.action == other.action
            and self.comment == other.comment
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Decision({self.action!r}, {self.comment!r})"


def decide(
    execution: dict | None,
    prior_deaths: int,
    *,
    prior_exhaustions: int = 0,
    run_url: str = "",
    cap: int = RETRY_CAP,
    turn_cap: int = TURN_RETRY_CAP,
) -> Decision:
    """Decide escalate/retry/hold for a no-progress fix run, given the
    execution result and the prior worker-bot marker counts.

    `prior_deaths` counts OUTAGE_TAG markers, `prior_exhaustions` counts
    TURN_CAP_TAG markers — separate budgets, because an outage and a turn
    ceiling are different failures with different remedies (DRE-2312). A PR
    that survived two outages last week must not have its first turn
    exhaustion escalated on that exhausted budget, and vice versa."""
    death = check_agent_result.classify_death(execution)
    if death == check_agent_result.DEATH_NONE:
        # The model ran and still pushed nothing (or there is no result file
        # to prove otherwise) — keep today's escalation, unchanged.
        return Decision("escalate", "")
    run_suffix = f" Run: {run_url}" if run_url else ""
    if death == check_agent_result.DEATH_TURN_EXHAUSTION:
        facts = check_agent_result.turn_exhaustion_facts(execution)
        if prior_exhaustions >= turn_cap:
            # Two in a row: the task does not fit in one run. Say so, and stop
            # buying identical attempts. 🛑 so fix_context.py shows it to any
            # later fix run as an unanswered blocker; no marker, so the
            # reconcile sweep stops here.
            return Decision(
                "hold-turns",
                f"🛑 The last {prior_exhaustions + 1} fix runs on this PR ran "
                f"out of steps before pushing anything — the most recent one "
                f"hit {facts}. Both were full runs that did real work, so the "
                f"evidence now says this fix does not fit inside one run: it "
                f"needs splitting into smaller pieces, or a larger turn "
                f"budget. Holding for a human rather than paying for a third "
                f"identical attempt.{run_suffix}",
            )
        return Decision(
            "retry-turns",
            f"⚡ {TURN_CAP_TAG}: the fix run ran out of steps — it hit "
            f"{facts} and stopped before pushing anything. A full fix run was "
            f"spent on it. How far an agent gets varies run to run, so the "
            f"pipeline retries this once on the next reconcile sweep "
            f"(turn exhaustion {prior_exhaustions + 1}/{turn_cap + 1}); if the "
            f"retry hits the cap too, the work needs splitting into smaller "
            f"pieces or a larger turn budget.{run_suffix}",
        )
    if prior_deaths >= cap:
        return Decision(
            "hold",
            f"🛑 The AI service failed {prior_deaths + 1} fix runs in a row "
            f"on this PR (died with an API/model error each time) — an outage "
            f"this persistent needs a human decision, so the pipeline is "
            f"holding rather than retrying forever.{run_suffix}",
        )
    return Decision(
        "retry",
        f"⚡ {OUTAGE_TAG}: the fix run died with an API/model error — the AI "
        f"service was unavailable, not a failed fix. No fix-attempt budget "
        f"was used; the pipeline will retry automatically on the next "
        f"reconcile sweep (death {prior_deaths + 1}/{cap + 1}).{run_suffix}",
    )


def main(argv: list[str]) -> int:
    """CLI for the workflow:

      decide <execution-json-path> [<prior_deaths>] \
          [--comments-json PATH] [--run-url U]

    With --comments-json (the workflow's path), BOTH prior counts — outage
    deaths and turn exhaustions — are DERIVED from the PR's comment list,
    each scoped to consecutive worker-bot markers since the last push, and any
    positional <prior_deaths> is ignored. Without it, the positional integer is
    used for whichever class this execution result belongs to (kept for the
    unit tests).

    Prints the action on line 1, then a blank line, then the comment body.
    """
    if not argv or argv[0] != "decide":
        print("usage: fix_dead_run.py decide <execution-json-path> "
              "[<prior_deaths>] [--comments-json PATH] [--run-url U]")
        return 2
    rest = argv[1:]
    run_url = ""
    if "--run-url" in rest:
        i = rest.index("--run-url")
        if i + 1 < len(rest):
            run_url = rest[i + 1]
        del rest[i : i + 2]
    comments_path = ""
    if "--comments-json" in rest:
        i = rest.index("--comments-json")
        if i + 1 < len(rest):
            comments_path = rest[i + 1]
        del rest[i : i + 2]
    exec_path = rest[0] if rest else ""
    if comments_path:
        comments = _load_comments(comments_path)
        prior = consecutive_prior_markers(comments, OUTAGE_TAG)
        prior_exhaustions = consecutive_prior_markers(comments, TURN_CAP_TAG)
    else:
        prior = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0
        prior_exhaustions = prior
    d = decide(
        check_agent_result._load_execution(exec_path),
        prior,
        prior_exhaustions=prior_exhaustions,
        run_url=run_url,
    )
    print(d.action)
    print()
    print(d.comment)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
