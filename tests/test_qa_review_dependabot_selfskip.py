"""RED-first tests for DRE-2067 — the REUSABLE qa-review must self-skip
dependabot-actor'd pull_request runs; fleet stubs carry no guard of their own.

THE BUG (live, 2026-07-12, deltasolv run 29204391739): DRE-2047 put the
dependabot skip on bureau-pipeline's OWN stub (pr-review.yml), so only this
repo skips the doomed runs. Fleet repos' qa-review stubs (atlas/deltasolv,
and the onboarding scaffold) call the reusable directly with
`secrets: inherit`; under a dependabot[bot] actor that inherit satisfies
required-secret validation but delivers GitHub's separate — empty for us —
Dependabot store, so the reusable's FIRST step (the qa-bot token mint) dies:

    ##[error]The 'client-id' (or deprecated 'app-id') input must be set
    to a non-empty string.

Every dependabot PR on every consuming repo shows a red crashed review.

FIX UNDER TEST: the guard moves INTO the reusable, on the review job's
JOB-LEVEL `if` (the only point evaluated before the mint step that crashes),
as the LEADING `&&` conjunct so no pull_request entry path can dodge it:

    (github.event_name != 'pull_request' || github.actor != 'dependabot[bot]')

A job-level `if` that evaluates false SKIPS the job — a clean green/neutral
run, never a failed step — and the reconcile sweep's workflow_dispatch
(DRE-2047) stays the real review route for dependabot PRs, passing the
guard's event_name disjunct untouched. verify.yml carries the same guard:
its agent/-branch gate happens to exclude dependabot heads today, but
qa-review's gate was equally narrow before DRE-1888 broadened it — the
guard pins the invariant against the same drift. Reaches the fleet at the
next tag promotion (v4) with zero stub churn.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# The exact guard conjunct. Actor-based on purpose: the empty Dependabot
# secrets store is keyed to the TRIGGERING ACTOR, not the PR author — a
# human pushing to a dependabot/ branch gets normal secrets and the
# event-driven review must still run for them (mirrors pr-review.yml's
# DRE-2047 stub guard verbatim).
GUARD = "(github.event_name != 'pull_request' || github.actor != 'dependabot[bot]')"


def _job(workflow: str, job: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return doc["jobs"][job]


def _job_if(workflow: str, job: str) -> str:
    # `if: >` folds to a single line at parse time on the runner; normalize
    # whitespace the same way so the assertions see what GitHub evaluates.
    return " ".join((_job(workflow, job).get("if") or "").split())


class TestReusableQaReviewSelfSkip:
    def test_review_job_skips_dependabot_pull_request_runs(self):
        """ACCEPTANCE: a dependabot pull_request event on ANY consuming repo
        must skip before the token mint can crash on the empty store."""
        assert GUARD in _job_if("qa-review.yml", "review"), (
            "the reusable qa-review's review job must itself skip "
            "dependabot-actor'd pull_request runs — fleet stubs call it "
            "with `secrets: inherit` and no guard, so the empty Dependabot "
            "store crashes the qa-bot token mint (DRE-2067)"
        )

    def test_guard_is_a_job_level_if_so_the_skip_is_a_clean_success(self):
        """The skip must read as green/neutral, not a crash: only a
        JOB-level `if` is evaluated before the first step (the mint that
        dies on empty secrets), and a false job `if` SKIPS the job rather
        than failing any step."""
        job = _job("qa-review.yml", "review")
        assert GUARD in " ".join((job.get("if") or "").split()), (
            "the dependabot guard must live on the review JOB's `if` — a "
            "step-level guard is too late (the mint step crashes first) and "
            "an exiting step would not be a clean skip"
        )

    def test_guard_is_the_leading_conjunct_no_entry_path_dodges_it(self):
        """An OR'd guard would be vacuous — dependabot/ heads already
        satisfy the branch disjuncts (DRE-1888 broadened them). The guard
        must AND against the whole entry expression."""
        cond = _job_if("qa-review.yml", "review")
        assert cond.startswith(GUARD + " &&"), (
            f"the guard must be the leading && conjunct of the review job's "
            f"if — got: {cond!r}"
        )

    def test_workflow_dispatch_reviews_unaffected(self):
        """ACCEPTANCE: the reconcile-driven workflow_dispatch path
        (DRE-2047) is the REAL review route for dependabot PRs — the guard's
        event_name disjunct lets every non-pull_request trigger through, and
        the workflow_dispatch entry clause must survive the edit."""
        cond = _job_if("qa-review.yml", "review")
        assert "github.event_name != 'pull_request'" in cond, (
            "the guard must scope its actor check to pull_request events "
            "only — workflow_dispatch runs initiate as github-actions and "
            "must review dependabot PRs with full secrets"
        )
        assert "github.event_name == 'workflow_dispatch'" in cond, (
            "the workflow_dispatch entry path must remain"
        )

    def test_dependabot_branch_entry_path_is_preserved(self):
        """A NON-dependabot actor on a dependabot/ head (a human push, the
        merge-gate's qa-bot update-branch synchronize) has normal secrets —
        its event-driven review must still start (DRE-2039), so the guard
        must not delete the branch clause."""
        assert (
            "startsWith(github.event.pull_request.head.ref, 'dependabot/')"
            in _job_if("qa-review.yml", "review")
        )


class TestReusableVerifySelfSkip:
    def test_verify_job_carries_the_same_guard(self):
        """verify.yml is pull_request-triggered by the same fleet stub shape
        and mints from the same empty store under a dependabot actor. Its
        agent/-branch gate excludes dependabot heads today, but that is the
        same accident qa-review had before DRE-1888 broadened its gate —
        pin the invariant with the identical leading conjunct."""
        cond = _job_if("verify.yml", "verify")
        assert GUARD in cond and cond.startswith(GUARD + " &&"), (
            f"verify's job gate must lead with the dependabot guard — "
            f"got: {cond!r}"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
