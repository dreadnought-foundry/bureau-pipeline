"""RED-first tests for the QA-critic review gate (DRE-1888).

Origin: the adversarial critic (qa-review.yml) only ran on agent-dispatched
branches (`agent/DRE-N-*`). Operator-routed cards — the ones the pipeline's
repo-scoped agent tokens can't author, so the operator opens the PR by hand on
a `fix/DRE-N-...` / `feat/DRE-N-...` branch — were SKIPPED. Operator work thus
merged with no real critic verdict, dodging the gate every normal card PR
passes.

The fix opts operator-routed CARD PRs in: a PR whose head branch carries a
linked Linear card (`DRE-<n>`, the same signal linear-sync uses to close the
loop) gets the critic, whatever the branch prefix. A truly chrome-only PR
(no linked card) stays skippable so the gate never blocks non-card work.

These tests must FAIL before should_review_pr.py exists / before qa-review.yml
broadens its guard, and PASS after.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import should_review_pr  # noqa: E402


class ShouldReviewTest(unittest.TestCase):
    # --- operator-routed card PRs: NOW reviewed (the DRE-1888 fix) -------

    def test_operator_fix_branch_with_card_is_reviewed(self):
        # The exact shape that was skipping before: an operator-authored
        # bureau-pipeline fix on a fix/DRE-N branch.
        self.assertTrue(
            should_review_pr.should_review("fix/DRE-1885-dont-park-building-card")
        )

    def test_operator_feat_branch_with_card_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("feat/DRE-1888-critic-on-operator-prs")
        )

    def test_docs_branch_with_card_is_reviewed(self):
        # A card is a card regardless of the conventional-commit prefix.
        self.assertTrue(should_review_pr.should_review("docs/DRE-1900-adr"))

    def test_bare_card_branch_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("DRE-1773"))

    # --- lowercase card refs: reviewed + normalized (DRE-2003) ----------
    # The workflow-level contains() guard is case-INsensitive but this
    # authoritative gate was not: a lowercase `ops/dre-N-...` branch started
    # the review job yet returned review=false — a silent review bypass
    # (bit us live 2026-07-09; three security PRs re-pushed as uppercase
    # twins).

    def test_lowercase_ops_branch_with_card_is_reviewed(self):
        self.assertTrue(should_review_pr.should_review("ops/dre-123-x"))

    def test_uppercase_agent_branch_still_reviewed(self):
        self.assertTrue(should_review_pr.should_review("agent/DRE-9-x"))

    def test_chore_deps_without_card_still_skipped(self):
        # Case-insensitivity must not over-match: no card ref, still skip.
        self.assertFalse(should_review_pr.should_review("chore/deps"))

    def test_card_in_branch_normalizes_lowercase_to_uppercase(self):
        # Linear identifiers are uppercase; `dre-123` must resolve to card
        # DRE-123, so the extractor normalizes before anything uses it.
        self.assertEqual(
            should_review_pr.card_in_branch("ops/dre-123-x"), "DRE-123"
        )

    def test_card_in_branch_normalizes_mixed_case(self):
        self.assertEqual(
            should_review_pr.card_in_branch("fix/Dre-42-y"), "DRE-42"
        )

    # --- normal pipeline PRs: STILL reviewed (no regression) ------------

    def test_agent_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("agent/DRE-1759-engine-drilldown")
        )

    def test_agent_branch_even_without_card_is_reviewed(self):
        # Legacy convention is honored on the prefix alone.
        self.assertTrue(should_review_pr.should_review("agent/some-task"))

    # --- dependabot PRs: reviewed (DRE-2039) -----------------------------
    # The merge gate auto-merges grouped minor/patch bumps ONLY on a
    # SHA-bound critic APPROVE — so dependabot/** branches must get the
    # critic, or the dependency rail is dead wiring that waits forever.

    def test_dependabot_pip_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review("dependabot/pip/pip-minor-patch-0a1b2c")
        )

    def test_dependabot_actions_branch_is_reviewed(self):
        self.assertTrue(
            should_review_pr.should_review(
                "dependabot/github_actions/actions/checkout-6"
            )
        )

    # --- chrome-only PRs: STILL skippable -------------------------------

    def test_chore_branch_without_card_is_skipped(self):
        self.assertFalse(should_review_pr.should_review("chore/bump-deps"))

    def test_docs_branch_without_card_is_skipped(self):
        self.assertFalse(should_review_pr.should_review("docs/readme-typo"))

    def test_empty_branch_is_skipped(self):
        self.assertFalse(should_review_pr.should_review(""))

    def test_none_branch_is_skipped(self):
        self.assertFalse(should_review_pr.should_review(None))

    # --- card extraction matches the pipeline's DRE-N convention --------

    def test_card_in_branch_picks_first_reference(self):
        self.assertEqual(
            should_review_pr.card_in_branch("fix/DRE-1885-then-DRE-1999"),
            "DRE-1885",
        )

    def test_card_in_branch_none_when_absent(self):
        self.assertIsNone(should_review_pr.card_in_branch("chore/bump-deps"))


class CliTest(unittest.TestCase):
    """CLI: exit 0 == review (run the critic); exit 1 == skip. Stdout carries
    a `review=true|false` line the workflow captures as a step output."""

    def _run(self, branch):
        return subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "should_review_pr.py"),
             branch],
            capture_output=True, text=True,
        )

    def test_cli_exit_0_and_review_true_on_operator_card_branch(self):
        p = self._run("fix/DRE-1885-dont-park")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_0_on_agent_branch(self):
        p = self._run("agent/DRE-1-x")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)

    def test_cli_exit_1_and_review_false_on_chrome_branch(self):
        p = self._run("chore/bump-deps")
        self.assertEqual(p.returncode, 1)
        self.assertIn("review=false", p.stdout)

    def test_cli_exit_0_and_review_true_on_lowercase_card_branch(self):
        # DRE-2003: the lowercase shape that silently bypassed the critic.
        p = self._run("ops/dre-1987-merge-gate-verdict-authorship")
        self.assertEqual(p.returncode, 0)
        self.assertIn("review=true", p.stdout)
        # The card it reports must be the real (uppercase) Linear identifier.
        self.assertIn("DRE-1987", p.stdout)


if __name__ == "__main__":
    unittest.main()
