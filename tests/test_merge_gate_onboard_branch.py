"""Tests for DRE-7209 and DRE-8658 — the merge gate admits the onboarding branches.

The onboarding process opens two routing pull requests — one here, one in
agent-bureau — and they must be reviewed and merged by the pipeline, not by a
person. DRE-7209 admitted the one literal branch `bot/onboard`. DRE-8658 adds
the per-declaration family `bot/onboard-<slug>` beside it (agent-bureau's epic
DRE-8613), so one stuck onboarding stops blocking the others; the literal stays
admitted, because agent-bureau#4362 is still open on it.

The CEO's signed answer of 2026-09-15 allows literal bot branch names only,
never a `bot/` prefix. The family is the one named exception: a slug-shaped
suffix after the literal, the whole name matching
`^bot/onboard-[a-z0-9][a-z0-9-]*$` (agent-bureau's `onboarding.SLUG_RE`). So
every test below pairs the admitted names with the near-misses that must stay
out: `bot/other`, `bot/onboardx`, `bot/onboard/acme`, and — in the step, which
re-checks the slug — `bot/onboard-`, `bot/onboard-Acme`, `bot/onboard-acme_x`.
`bot/onboard/<slug>` is not the shape: Git cannot hold that ref while
`refs/heads/bot/onboard` exists.

Three places answer "does the gate take this branch?", and each is read LIVE:
  1. merge-gate.yml's `workflow_run` leg — the job `if:` is rendered the way
     GitHub renders it (scripts/fix_concurrency.py), so a CI completion on the
     branch reaches the gate job;
  2. scripts/evaluate_and_merge.sh's `case "$BRANCH"` — the step the job runs
     re-checks the branch, and its branch gate is executed here in bash for
     each name;
  3. reconcile.PIPELINE_BRANCH_PREFIXES — the sweep's broad ownership question,
     so it does not tell the PR "nothing is coming"; the two narrow tuples stay
     narrow, as they do for `bot/standards-sync` (no card, no fix agent). Its
     `bot/onboard` entry already owns `bot/onboard-<slug>` by prefix, so the
     test for it is a pin of behavior that held before DRE-8658.

The channel precondition (the onboarding epic's contract) reads the literal
`bot/onboard` in merge-gate.yml, reconcile.py and check_tdd_commits.py at
`stable`, and DRE-8613's per-slug marker `bot/onboard-` in merge-gate.yml and
evaluate_and_merge.sh, so the last class pins that each string is present.

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
#: DRE-8658: one branch per onboarding declaration, `bot/onboard-<slug>`.
SLUG_BRANCHES = ("bot/onboard-acme", "bot/onboard-onboard-sandbox-5")
SLUG_MARKER = "bot/onboard-"
NEAR_MISSES = ("bot/other", "bot/onboardx", "bot/onboard/acme")
#: Shapes the job's `startsWith` lets through and the step's slug check skips.
BAD_SLUGS = ("bot/onboard-", "bot/onboard-Acme", "bot/onboard-acme_x")
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


def _shell_branch_gate() -> str:
    """The step's branch gate: every line from its first `case "$BRANCH" in`
    up to the card read, comments dropped."""
    lines = EVALUATE.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.startswith('case "$BRANCH" in')), None)
    assert start is not None, "evaluate_and_merge.sh: no branch case statement"
    end = next(i for i in range(start, len(lines))
               if lines[i].startswith("CARD="))
    return "\n".join(ln for ln in lines[start:end]
                     if not ln.lstrip().startswith("#"))


def shell_admits(branch: str) -> bool:
    """Run the live branch gate in bash against `branch`: a skip prints the
    step's skip line and exits, an admit falls through to the next line."""
    script = (f'set -euo pipefail\nBRANCH="$1"\n{_shell_branch_gate()}\n'
              'echo admit')
    out = subprocess.run(["bash", "-c", script, "gate", branch],
                         capture_output=True, text=True, check=True).stdout
    lines = out.strip().splitlines()
    if lines == ["admit"]:
        return True
    assert lines == ["not an agent branch — skip"], out
    return False


class WorkflowRunLegTest(unittest.TestCase):
    """merge-gate.yml: a CI completion on `bot/onboard` or a `bot/onboard-…`
    branch reaches the gate job, and nothing that merely resembles them
    does."""

    def test_ci_on_the_onboard_branch_reaches_the_gate_job(self):
        self.assertTrue(gate_job_runs(ONBOARD))

    def test_ci_on_a_per_slug_onboard_branch_reaches_the_gate_job(self):
        for branch in SLUG_BRANCHES:
            with self.subTest(branch=branch):
                self.assertTrue(gate_job_runs(branch))

    def test_near_misses_do_not_reach_the_gate_job(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(gate_job_runs(branch))

    def test_the_standards_sync_branch_still_reaches_the_gate_job(self):
        # The family joins the literals; it does not replace them.
        self.assertTrue(gate_job_runs("bot/standards-sync"))

    def test_the_family_is_the_literal_plus_a_dash_never_a_bot_prefix(self):
        # A `startsWith(…, 'bot/onboard')` would admit `bot/onboardx` — the
        # near-miss test says so behaviorally; this names the shape.
        cond = _evaluate_if()
        self.assertIn("github.event.workflow_run.head_branch == 'bot/onboard'",
                      cond)
        self.assertIn(
            "startsWith(github.event.workflow_run.head_branch, 'bot/onboard-')",
            cond)
        rest = cond.replace("'bot/onboard'", "").replace("'bot/onboard-'", "")
        self.assertNotIn("'bot/onboard", rest)
        self.assertNotIn("'bot/'", cond)


class EvaluateStepBranchGateTest(unittest.TestCase):
    """The step the gate job runs re-checks the branch before it reads
    anything; a branch the job admits but the step skips would sit forever
    exactly as it does today."""

    def test_the_step_admits_the_onboard_branch(self):
        self.assertTrue(shell_admits(ONBOARD))

    def test_the_step_admits_a_per_slug_onboard_branch(self):
        for branch in SLUG_BRANCHES:
            with self.subTest(branch=branch):
                self.assertTrue(shell_admits(branch))

    def test_the_step_skips_near_misses(self):
        for branch in NEAR_MISSES:
            with self.subTest(branch=branch):
                self.assertFalse(shell_admits(branch))

    def test_the_step_skips_a_suffix_that_is_not_a_slug(self):
        # The job's `startsWith` lets these through; the step is the check
        # that the whole name is `bot/onboard-` and a slug.
        for branch in BAD_SLUGS:
            with self.subTest(branch=branch):
                self.assertTrue(gate_job_runs(branch))
                self.assertFalse(shell_admits(branch))

    def test_the_step_still_admits_every_other_pipeline_branch(self):
        for branch in ("agent/DRE-1-x", "repair/red-main-1",
                       "dependabot/pip/x-1.2", "bot/standards-sync",
                       "bot/test-durations"):
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

    def test_a_per_slug_branch_is_owned_through_the_onboard_entry(self):
        # A pin, green before DRE-8658 and after: `_has_prefix` matches with
        # `startswith`, so the `bot/onboard` entry owns `bot/onboard-acme`.
        # It guards a later narrowing of the tuple from orphaning the family.
        self.assertTrue(reconcile.pipeline_owns("bot/onboard-acme"))
        self.assertFalse(reconcile.card_branch("bot/onboard-acme"))
        self.assertFalse(reconcile.fix_eligible("bot/onboard-acme"))

    def test_no_bot_prefix_is_owned(self):
        self.assertNotIn("bot/", reconcile.PIPELINE_BRANCH_PREFIXES)
        self.assertFalse(reconcile.pipeline_owns("bot/other"))


class ChannelPreconditionLiteralTest(unittest.TestCase):
    """The onboarding routing step refuses until these files at `stable`
    carry the literals it reads."""

    def test_each_file_the_precondition_reads_carries_the_literal(self):
        for rel in (".github/workflows/merge-gate.yml", "scripts/reconcile.py",
                    "scripts/check_tdd_commits.py"):
            with self.subTest(path=rel):
                self.assertIn(ONBOARD, (ROOT / rel).read_text())

    def test_the_gate_files_carry_the_per_slug_marker(self):
        # DRE-8613's channel marker. check_tdd_commits.py is the third marker
        # file, and the test-first relief card puts it there, not this one.
        for path in (WORKFLOW, EVALUATE):
            with self.subTest(path=path.name):
                text = path.read_text()
                self.assertIn(SLUG_MARKER, text)
                self.assertIn(ONBOARD, text)


if __name__ == "__main__":
    unittest.main()
