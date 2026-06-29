#!/usr/bin/env python3
"""Classify a failed pipeline run: is it a CRITIC INFRA-CRASH the medic must
NOT re-run, or a normal failure it should retry/diagnose? (stdlib only.)

DRE-1921 (the #1 quota-burn fix). When the QA critic *job* crashes on
INFRASTRUCTURE — a GitHub rate-limit (`API rate limit exceeded for installation
ID …`) or an auth/startup death — qa-review.yml deliberately posts a NEUTRAL
"could not run (infra error)" comment and then FAILS the job loudly "for medic
visibility" (qa-review.yml's final step). That FAILURE is a `workflow_run`
event the medic watches, so the medic:

  1. `retry` job → `gh run rerun --failed` re-runs the critic, which hits the
     SAME rate-limit and crashes again (deepening the limit), then
  2. `diagnose` job → spins up a diagnosis agent (more GitHub API + inference).

Each iteration burns more of the bot's GitHub quota, which extends the
rate-limit window — a self-reinforcing loop. On 2026-06-28 six PRs were stuck
in it and it burned the bot's GitHub quota twice.

The bug is that the medic treats a critic INFRA-CRASH (no verdict, reviewer was
DOWN) the same as a real failure worth retrying. It is not: re-running a
critic against an exhausted rate-limit cannot succeed and only makes the limit
worse. The medic must instead BACK OFF — neither rerun nor diagnose — and let
a later natural trigger (a new push, the rate-limit window resetting) re-review.

A REAL `REQUEST_CHANGES` verdict is unaffected: that is not a crash, the critic
ran, and it routes through agent-fix.yml (which fires only on
"VERDICT: REQUEST_CHANGES"), never through this path.

Detection — an infra-crash leaves a fingerprint in the FAILED RUN'S LOGS:
  * the critic posted the neutral marker comment whose body the qa-review job
    also echoes — "QA Critic could not run (infra error)"; and/or
  * a rate-limit / auth signature appears: "API rate limit exceeded",
    "rate limit", "HTTP 403" + "rate", "secondary rate limit", "is_error".

We classify off the run name + its failed-step log text. Only the QA-Review
workflow can be a *critic* infra-crash (other workflows' rate-limits are real
failures the medic should still retry/diagnose once).

CLI:
    python3 medic_classify.py <workflow-name> <log-file>
prints `infra_crash=true` or `infra_crash=false` (and a human line on stderr);
exit 0 either way. The caller (medic.yml) reads the stdout line.
"""

from __future__ import annotations

import re
import sys

# The exact neutral marker qa-review.yml posts + echoes when the critic crashes
# on infra (qa-review.yml "Post verdict or neutral status" step). Matching this
# is the strongest, most specific signal — it means the CRITIC itself declared
# an infra failure, not a code rejection.
CRITIC_NEUTRAL_MARKER = "QA Critic could not run (infra error)"

# Rate-limit / auth-death signatures (case-insensitive). A GitHub rate-limit is
# the documented 2026-06-28 cause; an auth/startup death (is_error, no inference)
# is the other infra-crash mode the critic gate already fail-closes on.
_INFRA_SIGNATURES = (
    re.compile(r"api rate limit exceeded", re.I),
    re.compile(r"secondary rate limit", re.I),
    re.compile(r"\brate limit\b", re.I),
    re.compile(r"x-ratelimit-remaining[\"'\s:]+0", re.I),
    re.compile(r"403[^\n]*rate", re.I),
)


def _is_qa_review(workflow_name: str) -> bool:
    """Only the QA-Review workflow can be a *critic* infra-crash. Match the
    reusable workflow's name ("QA Review (reusable)") and any stub that embeds
    "QA Review", case-insensitively, so a renamed consuming stub still matches.
    """
    return "qa review" in (workflow_name or "").lower()


def is_critic_infra_crash(workflow_name: str, log_text: str) -> bool:
    """True iff this failed run is a QA-critic infra-crash the medic must NOT
    rerun/diagnose. Requires (a) it is the QA-Review workflow AND (b) the logs
    carry the neutral critic marker OR a rate-limit/auth signature.
    """
    if not _is_qa_review(workflow_name):
        return False
    text = log_text or ""
    if CRITIC_NEUTRAL_MARKER in text:
        return True
    return any(sig.search(text) for sig in _INFRA_SIGNATURES)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    workflow_name, log_file = (argv + ["", ""])[:2]
    crash = is_critic_infra_crash(workflow_name, _read(log_file))
    print(f"infra_crash={'true' if crash else 'false'}")
    if crash:
        print(
            "medic classify: QA critic INFRA-CRASH (rate-limit/auth) — backing "
            "off, NOT rerunning (would deepen the limit and loop).",
            file=sys.stderr,
        )
    else:
        print(
            "medic classify: not a critic infra-crash — normal medic handling.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
