"""RED-first tests for the silent-agent-death requeue (2026-06-12).

Origin: two engineer runs died overnight without opening PRs (no blocker
note either); recovery waited on the 180-minute staleness timer twice and
cost the console walking-skeleton ~6 hours. The fix: (a) the run itself
requeues the card immediately via a new, testable `count-comments` helper
in linear_ops (capped at 2 dead runs, then hold), and (b) the staleness
timer becomes a 60-minute backstop only.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")

import linear_ops  # noqa: E402


def _comments_payload(bodies):
    return {"issue": {"comments": {"nodes": [{"body": b} for b in bodies]}}}


class CountCommentsTest(unittest.TestCase):
    """linear_ops.count_comments(identifier, needle) -> int"""

    def test_counts_only_matching_comments(self):
        with mock.patch.object(linear_ops, "gql", return_value=_comments_payload([
            "⚠️ Agent run ended with no PR and no blocker note — requeued",
            "🤖 PR opened: https://example.test/pr/1",
            "⚠️ Agent run ended with no PR and no blocker note — requeued",
        ])):
            n = linear_ops.count_comments("DRE-1", "ended with no PR and no blocker")
        self.assertEqual(n, 2)

    def test_zero_when_no_matches(self):
        with mock.patch.object(linear_ops, "gql", return_value=_comments_payload([
            "🔎 QA Critic — VERDICT: APPROVE",
        ])):
            self.assertEqual(linear_ops.count_comments("DRE-1", "no blocker"), 0)

    def test_handles_none_bodies(self):
        with mock.patch.object(linear_ops, "gql", return_value=_comments_payload([None, "x no blocker x"])):
            self.assertEqual(linear_ops.count_comments("DRE-1", "no blocker"), 1)

    def test_cli_prints_count(self):
        with mock.patch.object(linear_ops, "gql", return_value=_comments_payload(["abc", "abc"])):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                linear_ops.cmd_count_comments("DRE-1", "abc")
            self.assertEqual(buf.getvalue().strip(), "2")


class StalenessBackstopTest(unittest.TestCase):
    def test_in_progress_backstop_is_60_minutes(self):
        os.environ.setdefault("GH_TOKEN", "x")
        import reconcile
        self.assertEqual(reconcile.STALE_MINUTES["In Progress"], 60)

    def test_other_thresholds_unchanged(self):
        import reconcile
        self.assertEqual(reconcile.STALE_MINUTES["Todo"], 15)
        self.assertEqual(reconcile.STALE_MINUTES["In QA"], 120)
        self.assertEqual(reconcile.STALE_MINUTES["In Review"], 60)


class BlockerImmunityRegression(unittest.TestCase):
    """Pin the parent-epic blocker immunity shipped earlier (DRE-1233 class)."""

    def test_parent_epic_ref_is_not_a_blocker(self):
        import reconcile
        card = {
            "identifier": "DRE-10",
            "parent": {"identifier": "DRE-1"},
            "description": "**Blocked by:** DRE-1, DRE-9\nSerialize after: all other DRE-1 work",
            "inverseRelations": {"nodes": []},
        }
        self.assertEqual(reconcile.blockers_of(card), {"DRE-9"})

    def test_self_ref_is_not_a_blocker(self):
        import reconcile
        card = {
            "identifier": "DRE-10",
            "parent": None,
            "description": "Blocked by: DRE-10, DRE-7",
            "inverseRelations": {"nodes": []},
        }
        self.assertEqual(reconcile.blockers_of(card), {"DRE-7"})


if __name__ == "__main__":
    unittest.main()
