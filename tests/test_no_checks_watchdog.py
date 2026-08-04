"""RED-first tests for the no-checks watchdog (DRE-2261).

THE BUG (live, twice): a conflicted PR emits no workflow events at all —
GitHub cannot build its test-merge commit, so CI, the critic, and the merge
gate never run. unstick_conflicts is the repair, but it (correctly) refuses
a human-parked card (fix_dispatch_blocked, DRE-2024 — five identical doomed
fix runs in one evening), and NOTHING else ever looked: no red check, no
verdict, no failed run, just a PR indistinguishable from a healthy one.
DRE-2180 sat that way five hours; portico PR #21 thirteen days.

FIX UNDER TEST — reconcile.flag_no_checks_prs(), run on every full sweep:
any open, non-draft agent/* PR whose head commit has ZERO check runs after
NO_CHECKS_MINUTES gets ONE plain-English comment on its linked card naming
the PR, branch, mergeStateStatus, silence duration, and the card's park
state — REGARDLESS of needs-human / Plan Review, because parked IS the
invisible case. Noticing is not dispatching: the watchdog never starts an
agent (the DRE-2024 gate stays), and it adds no hold label (for an unparked
card that label would stand down unstick_conflicts and CREATE the very
invisibility it alarms on). Checks merely queued/in progress mean the
pipeline CAN see the PR — not reported. The NO_CHECKS_TAG + PR-number
comment is the idempotency marker (the flag_stranded pattern): one report
per PR, not one per sweep.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""

import json
import os
import re
import sys
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import reconcile  # noqa: E402

SHA = "a" * 40


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _pr(number=21, branch="agent/DRE-2180-conflicted", mstate="DIRTY",
        draft=False, sha=SHA):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "mergeStateStatus": mstate,
        "isDraft": draft,
    }


def _fake_gh(prs, statuses='[]', head_age_min=999.0, calls=None):
    """Stub reconcile.gh: PR listing, per-sha check-run statuses (the jq'd
    [.check_runs[].status] payload), and the head commit's committer date."""
    def gh(*args):
        if calls is not None:
            calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api" and "/check-runs" in args[1]:
            return statuses
        if args[0] == "api" and "/git/commits/" in args[1]:
            return json.dumps({"committer": {"date": _iso(head_age_min)}})
        return ""
    return gh


def sweep(prs, statuses='[]', head_age_min=999.0, bodies=(), parked=False):
    """Run flag_no_checks_prs once; returns (comments, dispatches, gh_calls,
    add_label mock). `bodies` are the linked card's existing comments;
    `parked` is the card's human-park state (Plan Review / needs-human)."""
    comments, dispatches, gh_calls = [], [], []
    with mock.patch.object(
        reconcile, "gh",
        side_effect=_fake_gh(prs, statuses, head_age_min, gh_calls),
    ), mock.patch.object(
        reconcile, "gh_dispatch", side_effect=lambda *a: dispatches.append(a),
    ), mock.patch.object(
        reconcile.linear_ops, "comment_bodies", return_value=list(bodies),
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda ident, body: comments.append((ident, body)),
    ), mock.patch.object(
        reconcile.linear_ops, "add_label",
    ) as add_label, mock.patch.object(
        reconcile, "card_parked_for_human", return_value=parked,
    ):
        reconcile.flag_no_checks_prs()
    return comments, dispatches, gh_calls, add_label


class ReportContentTest(unittest.TestCase):
    def test_silent_pr_reported_with_number_branch_state_and_duration(self):
        comments, _, _, _ = sweep([_pr()])
        self.assertEqual(len(comments), 1)
        ident, body = comments[0]
        self.assertEqual(ident, "DRE-2180")
        self.assertIn("#21", body)
        self.assertIn("agent/DRE-2180-conflicted", body)
        self.assertIn("DIRTY", body)
        self.assertRegex(body, r"\d+ minutes")

    def test_report_is_machine_marked_with_the_tag(self):
        """🚨 must stay a machine marker (never proof-of-life, never a
        blocker-clearing human reply), and the tag must be greppable."""
        comments, _, _, _ = sweep([_pr()])
        body = comments[0][1]
        self.assertTrue(body.startswith("🚨"))
        self.assertIn(reconcile.NO_CHECKS_TAG, body)
        self.assertIn("🚨", reconcile._AGENT_COMMENT_PREFIXES)
        self.assertFalse("🚨".startswith(reconcile._LIFE_PREFIXES))


class ParkStateTest(unittest.TestCase):
    def test_parked_cards_silent_pr_is_still_reported(self):
        """The whole point: needs-human suppresses the REPAIR (DRE-2024,
        correct), but it must never suppress the ALERT — that suppression is
        exactly how DRE-2180 and portico #21 went invisible."""
        comments, _, _, _ = sweep([_pr()], parked=True)
        self.assertEqual(len(comments), 1)
        self.assertIn("human-parked", comments[0][1])

    def test_unparked_cards_report_says_not_parked(self):
        comments, _, _, _ = sweep([_pr()], parked=False)
        self.assertEqual(len(comments), 1)
        self.assertIn("NOT human-parked", comments[0][1])


class NeverDispatchesTest(unittest.TestCase):
    def test_no_workflow_run_call_on_this_path(self):
        """Noticing is not dispatching — no agent may ever start from this
        watchdog, parked or not, or the DRE-2024 loop comes straight back."""
        for parked in (True, False):
            _, dispatches, gh_calls, _ = sweep([_pr()], parked=parked)
            self.assertEqual(dispatches, [])
            for call in gh_calls:
                self.assertNotEqual(call[:2], ("workflow", "run"))

    def test_no_hold_label_added(self):
        """Alert-only: stamping needs-human on an UNPARKED card would stand
        down unstick_conflicts (fix_dispatch_blocked reads the label) and
        CREATE the invisibility this watchdog exists to report."""
        _, _, _, add_label = sweep([_pr()])
        add_label.assert_not_called()


class IdempotencyTest(unittest.TestCase):
    def test_three_sweeps_one_report(self):
        posted = []
        for _ in range(3):
            comments, _, _, _ = sweep([_pr()], bodies=[b for _, b in posted])
            posted.extend(comments)
        self.assertEqual(len(posted), 1)

    def test_a_different_prs_old_report_does_not_suppress(self):
        """The marker is per-PR (tag + PR number), and #210's marker must not
        substring-match #21's — a new attempt PR on the same card still
        alarms."""
        stale = f"🚨 {reconcile.NO_CHECKS_TAG} PR #210: older attempt …"
        comments, _, _, _ = sweep([_pr(number=21)], bodies=[stale])
        self.assertEqual(len(comments), 1)


class ExclusionsTest(unittest.TestCase):
    def test_queued_or_in_progress_checks_not_reported(self):
        """Queued/running checks mean the pipeline CAN see the PR — only a
        head commit with zero check runs at all is invisible."""
        comments, _, _, _ = sweep([_pr()], statuses='["queued", "in_progress"]')
        self.assertEqual(comments, [])

    def test_completed_checks_not_reported(self):
        comments, _, _, _ = sweep([_pr()], statuses='["completed"]')
        self.assertEqual(comments, [])

    def test_draft_prs_excluded(self):
        comments, _, _, _ = sweep([_pr(draft=True)])
        self.assertEqual(comments, [])

    def test_non_agent_branches_excluded(self):
        comments, _, _, _ = sweep([_pr(branch="dependabot/pip/pytest-9.2")])
        self.assertEqual(comments, [])

    def test_fresh_head_under_threshold_not_reported(self):
        """Under NO_CHECKS_MINUTES: check spin-up and the 15-minute
        lost-event re-push (retrigger_dead_heads) get their chance first."""
        comments, _, _, _ = sweep([_pr()], head_age_min=5)
        self.assertEqual(comments, [])

    def test_unreadable_check_runs_not_reported(self):
        """DRE-2034 read discipline: a 403 parsed as emptiness is NOT 'zero
        checks' — never alarm on fabricated data."""
        comments, _, _, _ = sweep([_pr()], statuses="")
        self.assertEqual(comments, [])

    def test_agent_branch_without_card_ref_does_not_crash(self):
        comments, dispatches, _, _ = sweep([_pr(branch="agent/experiment")])
        self.assertEqual(comments, [])
        self.assertEqual(dispatches, [])


class ThresholdTest(unittest.TestCase):
    def test_threshold_is_a_named_minutes_constant(self):
        """Same family as WATCHDOG_MINUTES: a named, env-overridable minutes
        constant — the DRE-2180 five-hour silence must exceed it many times
        over so the first sweep past the threshold reports."""
        self.assertIsInstance(reconcile.NO_CHECKS_MINUTES, int)
        self.assertGreater(reconcile.NO_CHECKS_MINUTES, 0)
        self.assertLessEqual(reconcile.NO_CHECKS_MINUTES, 5 * 60)


class WiringTest(unittest.TestCase):
    def _main_mocks(self):
        return [
            mock.patch.object(reconcile, name) for name in (
                "unstick_conflicts", "retrigger_dead_heads",
                "fix_approved_but_red", "retry_dead_fix_runs",
                "review_dependabot_prs", "check_dependabot_capacity",
                "promote_ready",
            )
        ] + [
            mock.patch.object(reconcile, "flag_stranded", return_value=set()),
            mock.patch.object(reconcile, "active_cards", return_value=[]),
            mock.patch.object(reconcile, "backlog_children", return_value=[]),
        ]

    def test_full_sweep_runs_the_watchdog(self):
        reconcile._write_failures.clear()
        with mock.patch.object(reconcile, "flag_no_checks_prs") as wd:
            for m in self._main_mocks():
                self.enterContext(m)
            reconcile.main()
        wd.assert_called_once_with()

    def test_conflicts_only_mode_does_not_run_it(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "flag_no_checks_prs") as wd:
            reconcile.main(conflicts_only=True)
        wd.assert_not_called()

    def test_promote_only_mode_does_not_run_it(self):
        reconcile._write_failures.clear()
        with mock.patch.object(reconcile, "flag_no_checks_prs") as wd:
            for m in self._main_mocks():
                self.enterContext(m)
            reconcile.main(promote_only=True)
        wd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
