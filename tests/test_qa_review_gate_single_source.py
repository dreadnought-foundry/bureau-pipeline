"""RED-first tests for DRE-2250 — the review decision must live in ONE place.

THE BUG: whether the critic reviews a PR was decided by branch name, twice —
once in qa-review.yml's job `if:` and again in should_review_pr.py — using the
same rule expressed in two languages. Any branch that matched neither
(`fix/…`, `model/…`, `db/…`) was silently classified chrome-only and got NO
adversarial verdict. No red check, no note; the PR just looked green. On
2026-07-29 all five open PRs across the fleet were in that state.

Two copies of one rule is also how DRE-2003 happened: the YAML `contains()`
was case-insensitive and the Python regex was not, so a lowercase `ops/dre-N-`
branch STARTED the review job and then decided review=false — a silent bypass
that cost three re-pushed security PRs.

THE FIX, in two halves:

  1. Policy moves out of YAML entirely. The job `if:` keeps only MECHANICAL
     guards — conditions that must be evaluated before the first step runs
     (the dependabot self-skip, DRE-2067, whose whole point is to skip before
     the qa-bot token mint crashes on an empty secrets store). It no longer
     looks at the head ref at all.
  2. should_review_pr.py becomes the real single source of truth, and its
     policy becomes "review every PR" — no branch-name inference left to drift
     against.

The structural assertion below is the durable half. Pinning the CURRENT
condition would just re-freeze today's list; asserting that NO head-ref policy
may live in the job gate is what stops the whole class from coming back.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# The mechanical guard that MUST stay on the job `if:` — it is the only point
# evaluated before the token-mint step it exists to protect (DRE-2067).
DEPENDABOT_GUARD = (
    "(github.event_name != 'pull_request' || github.actor != 'dependabot[bot]')"
)


def _job_if(workflow: str, job: str) -> str:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    # `if: >` folds to one line on the runner; normalize the same way so the
    # assertions see what GitHub actually evaluates.
    return " ".join((doc["jobs"][job].get("if") or "").split())


class TestPolicyLivesInExactlyOnePlace:
    def test_job_gate_carries_no_branch_name_policy(self):
        """THE regression guard for this whole class.

        Any `head.ref` test in the job gate is policy, and policy in YAML is a
        second copy of a rule that already lives in should_review_pr.py. The
        two then drift — silently, because a job that never starts writes no
        verdict and shows no red check.
        """
        cond = _job_if("qa-review.yml", "review")
        assert "head.ref" not in cond, (
            "the review job's `if:` must not branch on the head ref — that is "
            "review POLICY, and it belongs in should_review_pr.py alone. Two "
            "copies of one rule is exactly how DRE-2003's silent bypass "
            f"happened. Got: {cond!r}"
        )

    def test_mechanical_dependabot_guard_survives(self):
        """The dependabot skip is NOT policy — it is a mechanical constraint.
        A dependabot-actor'd run gets GitHub's separate (empty) secrets store,
        so the qa-bot mint crashes red with zero review. Only a job-level `if:`
        is evaluated early enough to prevent that, so this one stays."""
        cond = _job_if("qa-review.yml", "review")
        # It is the whole condition today. Tolerate a future additional
        # conjunct, but never the guard being OR'd away or demoted — either
        # would let a dependabot-actor'd run reach the mint and crash.
        assert cond == DEPENDABOT_GUARD or cond.startswith(
            DEPENDABOT_GUARD + " &&"
        ), (
            f"the dependabot mechanical guard must be the review job's whole "
            f"`if:` or its leading conjunct — got: {cond!r}"
        )

    def test_gate_is_the_mechanical_guard_and_nothing_else(self):
        """The gate should carry the dependabot guard alone.

        The event allowlist went too: this is a REUSABLE workflow, and which
        events call it is the caller stub's decision, not this file's. A
        second-guessing clause here can only ever subtract from what a stub
        asked for — silently, since a job that never starts writes no verdict.
        """
        assert _job_if("qa-review.yml", "review") == DEPENDABOT_GUARD

    def test_workflow_dispatch_route_survives(self):
        """The manual/reconcile-driven review route must keep working — it is
        the only way to review a dependabot PR, and the door a human uses to
        review anything on demand. Nothing in the gate may exclude it."""
        cond = _job_if("qa-review.yml", "review")
        # The guard scopes its actor check to pull_request, so a
        # workflow_dispatch run satisfies it on the first disjunct.
        assert "github.event_name != 'pull_request'" in cond

    def test_verify_gate_is_not_widened_by_this_change(self):
        """Scope pin: this card changes WHO GETS REVIEWED, not who gets
        verified or merged. verify.yml keeps its own gate."""
        cond = _job_if("verify.yml", "verify")
        assert cond.startswith(DEPENDABOT_GUARD + " &&")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
