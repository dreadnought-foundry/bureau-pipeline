"""RED-first tests for event-driven conflict sweeps (2026-06-12).

Origin: PR #1348 (DRE-1277) sat conflicted for ~1h between sibling merges
because the only conflict detector was the 15-minute reconcile cron — which
GitHub runs late under load. A merge to main is exactly the event that
creates new conflicts in sibling PRs, so linear-sync (which already fires
on every merge) gains a conflict-sweep job calling
`reconcile.py --conflicts-only`: just the DIRTY-PR backstop, none of the
staleness/promotion machinery (that stays on the cron).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402


class ConflictsOnlyModeTest(unittest.TestCase):
    """`reconcile.py --conflicts-only` runs the conflict backstop and nothing else."""

    def test_conflicts_only_runs_just_unstick_conflicts(self):
        with mock.patch.object(reconcile, "unstick_conflicts") as unstick, \
             mock.patch.object(reconcile, "retrigger_dead_heads") as dead_heads, \
             mock.patch.object(reconcile, "fix_approved_but_red") as approved_red, \
             mock.patch.object(reconcile, "active_cards") as cards, \
             mock.patch.object(reconcile, "promote_ready") as promote:
            reconcile.main(conflicts_only=True)
        unstick.assert_called_once_with()
        dead_heads.assert_not_called()
        approved_red.assert_not_called()
        cards.assert_not_called()
        promote.assert_not_called()

    def test_conflicts_only_write_failure_still_fails_the_run(self):
        # DRE-1254 lesson holds in this mode too: a dispatch we claimed to
        # make but didn't must turn the run red so medic sees it.
        with mock.patch.object(
            reconcile, "unstick_conflicts",
            side_effect=reconcile.ReconcileWriteError("dispatch failed"),
        ):
            with self.assertRaises(SystemExit):
                reconcile.main(conflicts_only=True)

    def test_default_mode_unchanged_runs_all_backstops_and_promotion(self):
        with mock.patch.object(reconcile, "unstick_conflicts") as unstick, \
             mock.patch.object(reconcile, "retrigger_dead_heads") as dead_heads, \
             mock.patch.object(reconcile, "check_dependabot_capacity"), \
             mock.patch.object(reconcile, "fix_approved_but_red") as approved_red, \
             mock.patch.object(reconcile, "active_cards", return_value=[]) as cards, \
             mock.patch.object(reconcile, "promote_ready") as promote, \
             mock.patch.object(reconcile, "backlog_children", return_value=[]):
            reconcile.main()
        unstick.assert_called_once_with()
        dead_heads.assert_called_once_with()
        approved_red.assert_called_once_with()
        # Two reads since DRE-1993: the stranded-card watchdog's lane sweep
        # (Planning included) plus the nudge loop's default read.
        self.assertIn(mock.call(reconcile.WATCHDOG_LANES), cards.call_args_list)
        self.assertIn(mock.call(), cards.call_args_list)
        promote.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class ConflictSweepJobTokenTest(unittest.TestCase):
    """The linear-sync conflict-sweep job must pass GH_DISPATCH_TOKEN
    (the stub-granted github.token) — the minted App token cannot dispatch
    workflows (HTTP 403, same failure reconcile.yml already fixed)."""

    def test_conflict_sweep_sets_dispatch_token(self):
        wf = os.path.join(
            os.path.dirname(__file__), "..", ".github", "workflows", "linear-sync.yml"
        )
        src = open(wf).read()
        sweep = src.split("conflict-sweep:", 1)[1]
        self.assertIn("GH_DISPATCH_TOKEN: ${{ github.token }}", sweep)
