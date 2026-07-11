"""RED-first tests for the human-park dispatch gate (DRE-2024).

Observed live 2026-07-10 on DeltaSolv PR #120 (DRE-2009): the fix loop
failed to converge (five identical max-turns deaths), the card was correctly
parked needs-human / Plan Review — and the reconcile sweep's DIRTY-PR
backstop kept dispatching the identical doomed Agent Fix run every cycle
(runs 29115842272, 29122046329, 29125603420, 29128546908), burning an agent
run + Claude tokens per sweep, indefinitely.

The rule: human-parked means the loop is over until a human acts. NO
reconcile backstop (unstick_conflicts, fix_approved_but_red,
retry_dead_fix_runs) may dispatch agent-fix for a PR whose card sits in the
Plan Review lane or carries the needs-human label. Same family as the
medic↔critic loop-break (bureau-pipeline #50).
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_dead_run  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402

SHA = "a" * 40


def _issue(state="In QA", labels=()):
    return {
        "issue": {
            "state": {"name": state},
            "labels": {"nodes": [{"name": name} for name in labels]},
        }
    }


class BranchCardTest(unittest.TestCase):
    """Branch → card mapping: the gate keys off the DRE-N in the head ref."""

    def test_extracts_card_from_agent_branch(self):
        self.assertEqual(
            reconcile.branch_card("agent/DRE-2009-mobile-capture"), "DRE-2009"
        )

    def test_lowercase_ref_normalizes_upper(self):
        # agent-fix.yml upper-cases its own extraction; the gate must match.
        self.assertEqual(reconcile.branch_card("agent/dre-7-slug"), "DRE-7")

    def test_no_card_ref_returns_none(self):
        self.assertIsNone(reconcile.branch_card("repair/red-main-123"))


class CardParkedForHumanTest(unittest.TestCase):
    """The Linear-side check: Plan Review lane OR needs-human label = parked."""

    def _parked(self, payload):
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            return reconcile.card_parked_for_human("DRE-2009")

    def test_plan_review_lane_is_parked(self):
        self.assertTrue(self._parked(_issue(state="Plan Review")))

    def test_needs_human_label_is_parked(self):
        self.assertTrue(self._parked(_issue(state="In QA", labels=["needs-human"])))

    def test_needs_human_label_matches_case_insensitively(self):
        self.assertTrue(self._parked(_issue(state="In QA", labels=["Needs-Human"])))

    def test_active_unlabeled_card_is_not_parked(self):
        self.assertFalse(self._parked(_issue(state="In QA", labels=["repo:deltasolv"])))

    def test_unreadable_card_fails_safe_to_parked(self):
        # A Linear blip must not dispatch into a possibly-parked card: skip
        # this sweep (the next one retries), never guess "not parked".
        with mock.patch.object(
            linear_ops, "gql", side_effect=linear_ops.LinearError("api down")
        ):
            self.assertTrue(reconcile.card_parked_for_human("DRE-2009"))


def _fake_gh(prs, busy="[]", failed_checks="0"):
    def gh(*args):
        if args[:2] == ("run", "list"):
            return busy
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api" and "/check-runs" in args[1]:
            return failed_checks
        if args[0] == "api" and "/git/commits/" in args[1]:
            # Old commit: comfortably past every staleness threshold.
            return json.dumps({"committer": {"date": "2026-01-01T00:00:00Z"}})
        return ""
    return gh


def _run_backstop(backstop, prs, parked, failed_checks="0"):
    """Run a reconcile PR backstop with `parked` card identifiers parked."""
    calls = []
    with mock.patch.object(
        reconcile, "gh", side_effect=_fake_gh(prs, failed_checks=failed_checks)
    ), mock.patch.object(
        reconcile, "gh_dispatch", side_effect=lambda *a: calls.append(a)
    ), mock.patch.object(
        reconcile, "card_parked_for_human", side_effect=lambda c: c in parked
    ):
        backstop()
    return calls


class UnstickConflictsParkGateTest(unittest.TestCase):
    """The DIRTY-PR backstop — the exact loop that hit DeltaSolv PR #120."""

    def _pr(self, branch="agent/DRE-2009-mobile-capture"):
        return {"number": 120, "headRefName": branch, "mergeStateStatus": "DIRTY"}

    def test_parked_card_is_not_dispatched(self):
        calls = _run_backstop(
            reconcile.unstick_conflicts, [self._pr()], parked={"DRE-2009"}
        )
        self.assertEqual(calls, [])

    def test_unparked_card_still_dispatches(self):
        # Non-vacuous twin: the identical PR dispatches when the card is live.
        calls = _run_backstop(
            reconcile.unstick_conflicts, [self._pr()], parked=set()
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("agent-fix.yml", " ".join(calls[0]))

    def test_branch_without_card_ref_still_dispatches(self):
        # No DRE-N in the ref = no card to consult; the backstop must not
        # silently drop such PRs.
        calls = _run_backstop(
            reconcile.unstick_conflicts,
            [self._pr(branch="agent/experiment-no-card")],
            parked={"DRE-2009"},
        )
        self.assertEqual(len(calls), 1)


class FixApprovedButRedParkGateTest(unittest.TestCase):
    """The approved-but-red backstop dispatches agent-fix too — same gate."""

    def _pr(self):
        return {
            "number": 120,
            "headRefName": "agent/DRE-2009-mobile-capture",
            "headRefOid": SHA,
            "mergeStateStatus": "BLOCKED",
            "comments": [
                {
                    "author": {"login": "agent-bureau-qa-bot"},
                    "body": f"QA Critic\nVERDICT: APPROVE @{SHA}",
                }
            ],
        }

    def test_parked_card_is_not_dispatched(self):
        calls = _run_backstop(
            reconcile.fix_approved_but_red,
            [self._pr()],
            parked={"DRE-2009"},
            failed_checks="2",
        )
        self.assertEqual(calls, [])

    def test_unparked_card_still_dispatches(self):
        calls = _run_backstop(
            reconcile.fix_approved_but_red,
            [self._pr()],
            parked=set(),
            failed_checks="2",
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("agent-fix.yml", " ".join(calls[0]))


class RetryDeadFixRunsParkGateTest(unittest.TestCase):
    """The dead-fix-run retry backstop — its marker survives a later park."""

    def _pr(self):
        return {
            "number": 120,
            "headRefName": "agent/DRE-2009-mobile-capture",
            "mergeStateStatus": "BLOCKED",
            "comments": [
                {
                    "author": {"login": "agent-bureau-bot"},
                    "body": f"⚡ {fix_dead_run.OUTAGE_TAG}: the fix run died",
                }
            ],
        }

    def test_parked_card_is_not_dispatched(self):
        calls = _run_backstop(
            reconcile.retry_dead_fix_runs, [self._pr()], parked={"DRE-2009"}
        )
        self.assertEqual(calls, [])

    def test_unparked_card_still_dispatches(self):
        calls = _run_backstop(
            reconcile.retry_dead_fix_runs, [self._pr()], parked=set()
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("agent-fix.yml", " ".join(calls[0]))


if __name__ == "__main__":
    unittest.main()
