"""RED-first tests for DRE-7577 — the merge gate admits the literal `bot/test-durations`.

The nightly `Pipeline Tests` run re-measures `config/unit-test-durations.json`
once fewer than 95% of the test files carry recorded seconds, and opens or
updates one pull request on the one literal branch `bot/test-durations`
(`tests/test_tests_workflow_durations_refresh.py`). That pull request carries
no card, and it must be merged by the pipeline, not by a person, or the
record falls under its 90% floor anyway and every pull request that adds a
test file goes red on files other pull requests added.

The CEO's signed answer of 2026-09-15 allows literal bot branch names only,
never a `bot/` prefix, so every test below pairs the admitted name with the
near-misses that must stay out: `bot/test-durations-x`, `bot/test-duration`,
`bot/other`. It joins the way `bot/onboard` did (DRE-7209,
`tests/test_merge_gate_onboard_branch.py`), and the three places that answer
"does the gate take this branch?" are read LIVE the same way:
  1. merge-gate.yml's `workflow_run` leg, rendered the way GitHub renders it
     (scripts/fix_concurrency.py);
  2. scripts/evaluate_and_merge.sh's `case "$BRANCH"`, executed in bash;
  3. reconcile.PIPELINE_BRANCH_PREFIXES — the broad tuple only; the two
     narrow tuples stay narrow (no card to work, no fix agent).

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_durations_branch.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402
from test_merge_gate_onboard_branch import (  # noqa: E402
    _evaluate_if,
    gate_job_runs,
    shell_admits,
)

DURATIONS = "bot/test-durations"
NEAR_MISSES = ("bot/test-durations-x", "bot/test-duration", "bot/other")


class WorkflowRunLegTest(unittest.TestCase):
    """merge-gate.yml: a CI completion on exactly `bot/test-durations`
    reaches the gate job, and nothing that merely resembles it does."""

    def test_ci_on_the_durations_branch_reaches_the_gate_job(self):
        self.assertTrue(gate_job_runs(DURATIONS))

    def test_near_misses_do_not_reach_the_gate_job(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(gate_job_runs(branch))

    def test_the_other_bot_branches_still_reach_the_gate_job(self):
        for branch in ("bot/standards-sync", "bot/onboard"):
            with self.subTest(branch=branch):
                self.assertTrue(gate_job_runs(branch))

    def test_the_branch_is_admitted_as_a_literal_never_a_prefix(self):
        cond = _evaluate_if()
        self.assertIn(
            "github.event.workflow_run.head_branch == 'bot/test-durations'", cond)
        self.assertNotIn("'bot/test-duration",
                         cond.replace("'bot/test-durations'", ""))
        self.assertNotIn("'bot/'", cond)


class EvaluateStepBranchGateTest(unittest.TestCase):
    """The step the gate job runs re-checks the branch before it reads
    anything; a branch the job admits but the step skips would sit forever."""

    def test_the_step_admits_the_durations_branch(self):
        self.assertTrue(shell_admits(DURATIONS))

    def test_the_step_skips_near_misses(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(shell_admits(branch))

    def test_the_step_still_admits_every_other_pipeline_branch(self):
        for branch in ("agent/DRE-1-x", "repair/red-main-1",
                       "dependabot/pip/x-1.2", "bot/standards-sync",
                       "bot/onboard"):
            with self.subTest(branch=branch):
                self.assertTrue(shell_admits(branch))


class ReconcileOwnershipTest(unittest.TestCase):
    """reconcile.py: the broad question owns the branch, the narrow two do
    not — the terms `bot/standards-sync` and `bot/onboard` have."""

    def test_the_broad_tuple_carries_the_durations_branch(self):
        self.assertIn(DURATIONS, reconcile.PIPELINE_BRANCH_PREFIXES)
        self.assertTrue(reconcile.pipeline_owns(DURATIONS))

    def test_the_narrow_tuples_do_not(self):
        self.assertNotIn(DURATIONS, reconcile.CARD_BRANCH_PREFIXES)
        self.assertNotIn(DURATIONS, reconcile.FIX_BRANCH_PREFIXES)
        self.assertFalse(reconcile.card_branch(DURATIONS))
        self.assertFalse(reconcile.fix_eligible(DURATIONS))

    def test_no_bot_prefix_and_no_shorter_name_is_owned(self):
        self.assertNotIn("bot/", reconcile.PIPELINE_BRANCH_PREFIXES)
        for branch in ("bot/other", "bot/test-duration"):
            with self.subTest(branch=branch):
                self.assertFalse(reconcile.pipeline_owns(branch))


if __name__ == "__main__":
    unittest.main()
