#!/usr/bin/env python3
"""Model-death decision for agent-fix's no-progress guard (DRE-2018, stdlib).

Origin (2026-07-10, DeltaSolv token outage): when the model died mid-fix-run
(execution result {"is_error": true} — API outage, exhausted subscription),
agent-fix's post-run guard saw "no new commit", posted "🛑 Fix attempt N
pushed no new commit" and parked the card needs-human/Plan Review — an
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
  retry    — is_error death under the cap: post the OUTAGE_TAG marker comment;
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

check_agent_result.py stays the single source of truth for reading the
execution result (is_error detection + list-shaped payload tolerance).
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
# Substring shared by BOTH worker-bot push markers ("🔧 Fix attempt N pushed…"
# and "🔀 Conflict resolution round N pushed…"). A push means the branch moved
# forward — it clears the current run of consecutive deaths.
PUSH_MARKER = "pushed — CI and critic review re-running"
# The worker (dispatch) bot identity, sans the "[bot]" login suffix. Only its
# comments count toward the cap or clear it (DRE-1995 discipline).
WORKER_LOGIN = "agent-bureau-bot"
RETRY_CAP = 2  # retry at most twice (deaths 1,2), then hold on the 3rd


def _comment_login(comment: dict) -> str:
    """Login of a REST/GraphQL comment record ('' if absent), suffix intact."""
    for key in ("user", "author"):
        who = comment.get(key)
        if isinstance(who, dict) and who.get("login"):
            return who["login"]
    return ""


def consecutive_prior_deaths(
    comments: list | None, *, worker_login: str = WORKER_LOGIN
) -> int:
    """Count worker-bot OUTAGE_TAG markers SINCE the last successful push.

    DRE-2018 review finding: the retry cap must fire on three deaths *in a
    row*, not on every death marker the PR has ever carried. A recovered
    outage episode (deaths that were retried, then a push that succeeded)
    must not pre-exhaust the cap for a fresh, unrelated outage weeks later.

    Walk newest→oldest; a worker-bot push marker ends the run (return what we
    have). Only worker-bot-authored comments are considered — a forged marker
    or a forged push from any other identity is transparent (DRE-1995)."""
    deaths = 0
    for comment in reversed(comments or []):
        if _comment_login(comment).removesuffix("[bot]") != worker_login:
            continue
        body = comment.get("body") or ""
        if PUSH_MARKER in body:
            break  # a successful push clears the consecutive run
        if OUTAGE_TAG in body:
            deaths += 1
    return deaths


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
    run_url: str = "",
    cap: int = RETRY_CAP,
) -> Decision:
    """Decide escalate/retry/hold for a no-progress fix run, given the
    execution result and the prior worker-bot OUTAGE_TAG comment count."""
    if not check_agent_result.is_error_death(execution):
        # The model ran and still pushed nothing (or there is no result file
        # to prove an outage) — keep today's escalation, unchanged.
        return Decision("escalate", "")
    run_suffix = f" Run: {run_url}" if run_url else ""
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

    With --comments-json (the workflow's path), the prior-deaths count is
    DERIVED from the PR's comment list — scoped to consecutive worker-bot
    deaths since the last push — and any positional <prior_deaths> is ignored.
    Without it, the positional integer is used (kept for the unit tests).

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
        prior = consecutive_prior_deaths(_load_comments(comments_path))
    else:
        prior = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0
    d = decide(
        check_agent_result._load_execution(exec_path), prior, run_url=run_url
    )
    print(d.action)
    print()
    print(d.comment)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
