"""RED-first tests for DRE-7209 — the merge gate admits the literal `bot/onboard`.

The onboarding process opens two routing pull requests — one here, one in
agent-bureau — on the one literal branch `bot/onboard`, and they must be
reviewed and merged by the pipeline, not by a person. Until this card the gate
admitted exactly one bot branch by name, `bot/standards-sync`, so a routing PR
would sit forever.

The CEO's signed answer of 2026-09-15 allows literal bot branch names only,
never a `bot/` prefix, so every test below pairs the admitted name with the
near-misses that must stay out: `bot/onboard-x`, `bot/other`, `bot/onboardx`.

Three places answer "does the gate take this branch?", and each is read LIVE:
  1. merge-gate.yml's `workflow_run` leg — the job `if:` is rendered the way
     GitHub renders it (scripts/fix_concurrency.py), so a CI completion on the
     branch reaches the gate job;
  2. scripts/evaluate_and_merge.sh's `case "$BRANCH"` — the step the job runs
     re-checks the branch, and is executed here in bash for each name;
  3. reconcile.PIPELINE_BRANCH_PREFIXES — the sweep's broad ownership question,
     so it does not tell the PR "nothing is coming"; the two narrow tuples stay
     narrow, as they do for `bot/standards-sync` (no card, no fix agent).

The channel precondition (the onboarding epic's contract) reads the literal
`bot/onboard` in merge-gate.yml, reconcile.py and check_tdd_commits.py at
`stable`, so the last class pins that the string is present in all three.

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_onboard_branch.py -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
EVALUATE = ROOT / "scripts" / "evaluate_and_merge.sh"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_concurrency as fc  # noqa: E402
import reconcile  # noqa: E402

ONBOARD = "bot/onboard"
NEAR_MISSES = ("bot/onboard-x", "bot/other", "bot/onboardx")
PR = 7209


def _evaluate_if() -> str:
    cond = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"].get("if")
    assert cond, "merge-gate.yml job 'evaluate' has no `if:`"
    return str(cond)


def workflow_run_event(branch: str) -> dict:
    """A CI workflow completing for a same-repo pull_request run on `branch`,
    the way the gate's `workflow_run` leg sees it. `resolve` is skipped
    because the run names its pull request."""
    return {
        "github": {
            "event_name": "workflow_run",
            "event": {
                "workflow_run": {
                    "name": "Pipeline Tests",
                    "event": "pull_request",
                    "head_branch": branch,
                    "pull_requests": [{"number": PR, "head": {"ref": branch}}],
                },
            },
        },
        "inputs": {},
        "needs": {"resolve": {"result": "skipped", "outputs": {"pr": ""}}},
    }


def gate_job_runs(branch: str) -> bool:
    return fc._truthy(fc.evaluate(_evaluate_if(), workflow_run_event(branch)))


def _shell_branch_pattern() -> str:
    """The pattern list of the step's `case "$BRANCH" in <list>)` gate."""
    m = re.search(r'case "\$BRANCH" in ([^)]+)\)', EVALUATE.read_text())
    assert m is not None, "evaluate_and_merge.sh: no branch case statement"
    return m.group(1)


def shell_admits(branch: str) -> bool:
    """Run the live case statement in bash against `branch`."""
    script = (f'case "$1" in {_shell_branch_pattern()}) echo admit;; '
              f'*) echo skip;; esac')
    out = subprocess.run(["bash", "-c", script, "gate", branch],
                         capture_output=True, text=True, check=True).stdout
    return out.strip() == "admit"


class WorkflowRunLegTest(unittest.TestCase):
    """merge-gate.yml: a CI completion on exactly `bot/onboard` reaches the
    gate job, and nothing that merely resembles it does."""

    def test_ci_on_the_onboard_branch_reaches_the_gate_job(self):
        self.assertTrue(gate_job_runs(ONBOARD))

    def test_near_misses_do_not_reach_the_gate_job(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(gate_job_runs(branch))

    def test_the_standards_sync_branch_still_reaches_the_gate_job(self):
        # The second literal joins the first; it does not replace it.
        self.assertTrue(gate_job_runs("bot/standards-sync"))

    def test_the_branch_is_admitted_as_a_literal_never_a_prefix(self):
        # A `startsWith(…, 'bot/onboard')` would admit `bot/onboardx` — the
        # near-miss test says so behaviorally; this names the shape.
        cond = _evaluate_if()
        self.assertIn("github.event.workflow_run.head_branch == 'bot/onboard'",
                      cond)
        self.assertNotIn("'bot/onboard", cond.replace("'bot/onboard'", ""))
        self.assertNotIn("'bot/'", cond)


class EvaluateStepBranchGateTest(unittest.TestCase):
    """The step the gate job runs re-checks the branch before it reads
    anything; a branch the job admits but the step skips would sit forever
    exactly as it does today."""

    def test_the_step_admits_the_onboard_branch(self):
        self.assertTrue(shell_admits(ONBOARD))

    def test_the_step_skips_near_misses(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(shell_admits(branch))

    def test_the_step_still_admits_every_other_pipeline_branch(self):
        for branch in ("agent/DRE-1-x", "repair/red-main-1",
                       "dependabot/pip/x-1.2", "bot/standards-sync"):
            with self.subTest(branch=branch):
                self.assertTrue(shell_admits(branch))


class ReconcileOwnershipTest(unittest.TestCase):
    """reconcile.py: the broad question owns the branch, the narrow two do
    not — the same terms `bot/standards-sync` has."""

    def test_the_broad_tuple_carries_the_onboard_branch(self):
        self.assertIn(ONBOARD, reconcile.PIPELINE_BRANCH_PREFIXES)
        self.assertTrue(reconcile.pipeline_owns(ONBOARD))

    def test_the_narrow_tuples_do_not(self):
        self.assertNotIn(ONBOARD, reconcile.CARD_BRANCH_PREFIXES)
        self.assertNotIn(ONBOARD, reconcile.FIX_BRANCH_PREFIXES)
        self.assertFalse(reconcile.card_branch(ONBOARD))
        self.assertFalse(reconcile.fix_eligible(ONBOARD))

    def test_no_bot_prefix_is_owned(self):
        self.assertNotIn("bot/", reconcile.PIPELINE_BRANCH_PREFIXES)
        self.assertFalse(reconcile.pipeline_owns("bot/other"))


class ChannelPreconditionLiteralTest(unittest.TestCase):
    """The onboarding routing step refuses until these three files at
    `stable` each contain the literal `bot/onboard`."""

    def test_each_file_the_precondition_reads_carries_the_literal(self):
        for rel in (".github/workflows/merge-gate.yml", "scripts/reconcile.py",
                    "scripts/check_tdd_commits.py"):
            with self.subTest(path=rel):
                self.assertIn(ONBOARD, (ROOT / rel).read_text())


if __name__ == "__main__":
    unittest.main()
