"""RED-first tests for DRE-8785: a fix run that had nothing to fix, because the
critic approved the head first, does not pass an approved-but-red pull
request over.

Observed on agent-bureau #4445 (DRE-8513) on 2026-11-01 (PT). Fix attempt 1
pushed head `89e65c3f` at 9:16 AM and the critic approved it at 9:18 AM. Fix
attempt 2, dispatched for the older review, ended at 9:19 AM on the worker
bot's quiet line: "🔕 Fix attempt 2 pushed no new commit (head `89e65c3f`) and
none is needed: the critic has approved this commit since the review this
round was sent to fix." CI went red on one test at 9:49 AM. Every
`fix_approved_but_red` pass read that line as DRE-6583's failed fix run and
passed the head over, and at 6:44 PM the sweep parked the card in Green Light.

The rule: the quiet line `fix_exit.py` writes for its `approved` outcome
carries a marker the writer owns, and `_no_commit_fix_runs` does not count a
line holding it. Every other no-commit line at the head — the `nothing-to-fix`
quiet line, the escalation line, an unmarked line — still counts, as DRE-6583
requires.

Run: python3 -m pytest tests/test_approved_but_red_noop_fix.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_convergence  # noqa: E402
import fix_exit  # noqa: E402
import reconcile  # noqa: E402
from test_approved_but_red_starvation import (  # noqa: E402
    _critic,
    _pr_912,
    _Pass,
    _worker,
)

#: #4445's head after fix attempt 1. Its first eight are read off the
#: thread; the rest is fixture.
SHA_4445 = "89e65c3f" + "4" * 32
OLDER = "71a0b2c3" + "5" * 32

#: The answers trailer report_fix_result.sh appends below the quiet line.
TRAILER = ("🧷 answers: dreadnought-foundry/agent-bureau#4445 · card DRE-8513 "
           "· verdict 71a0b2c3")


def _quiet(kind: str, attempt: str = "2") -> str:
    """The quiet line as the writer posts it, trailer and all."""
    return fix_exit.quiet_line(kind, attempt, SHA_4445) + "\n\n" + TRAILER + "\n"


def _pr_4445(*extra: dict) -> dict:
    """The thread at 9:49 AM PT: a REQUEST_CHANGES on the older head, fix
    attempt 1's push, the APPROVE at `89e65c3f`, then attempt 2's no-op."""
    return {
        "number": 4445,
        "headRefName": "agent/DRE-8513-runners-tab-spot-losses",
        "headRefOid": SHA_4445,
        "mergeStateStatus": "BLOCKED",
        "comments": [
            {"author": {"login": "agent-bureau-qa-bot"},
             "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{OLDER} content:abc"},
            _worker("🔧 Fix attempt 1 pushed — CI and critic review re-running."),
            _critic(SHA_4445),
            _worker(_quiet(fix_exit.APPROVED)),
            *extra,
        ],
    }


class ReplayTest(unittest.TestCase):
    """The 9:49 AM PT pass: approved, red, and only a "none is needed" line."""

    def test_the_fix_agent_is_dispatched(self):
        run = _Pass([_pr_4445()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=4445"], run.log)

    def test_the_head_is_not_counted_as_a_failed_fix_run(self):
        self.assertEqual(reconcile._no_commit_fix_runs(_pr_4445()), 0)

    def test_no_pass_over_is_logged(self):
        run = _Pass([_pr_4445()])
        self.assertNotIn("pushed no commit", run.log)

    def test_the_line_is_still_a_no_commit_line(self):
        # The fix run's own convergence halt still counts it: two quiet rounds
        # on one commit stop the loop, whichever outcome they had.
        line = fix_exit.quiet_line(fix_exit.APPROVED, "2", SHA_4445)
        self.assertIn(fix_convergence.NO_PROGRESS, line)
        self.assertIn(SHA_4445[:8], line)


class DRE6583StillHoldsTest(unittest.TestCase):
    """A fix run sent to fix something that pushed nothing still passes the
    head over."""

    def test_a_nothing_to_fix_line_at_the_head_still_counts(self):
        pr = _pr_4445(_worker(_quiet(fix_exit.NOTHING, "3")))
        self.assertEqual(reconcile._no_commit_fix_runs(pr), 1)
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=912"], run.log)

    def test_an_escalation_line_at_the_head_still_counts(self):
        pr = _pr_4445(_worker(
            "🛑 Fix attempt 3 pushed no new commit (branch still at "
            "`89e65c3f`) — a person is needed."))
        run = _Pass([pr, _pr_912()])
        self.assertEqual(run.dispatched_prs(), ["pr_number=912"], run.log)


class TheMarkerIsTheWritersTest(unittest.TestCase):
    """The outcome is read off a marker `fix_exit.py` writes, never prose."""

    def test_the_approved_quiet_line_carries_the_marker(self):
        line = fix_exit.quiet_line(fix_exit.APPROVED, "2", SHA_4445)
        self.assertIn(fix_exit.outcome_marker(fix_exit.APPROVED), line)
        self.assertTrue(fix_exit.overtaken_by_approval(line))

    def test_the_nothing_to_fix_quiet_line_does_not(self):
        line = fix_exit.quiet_line(fix_exit.NOTHING, "2", SHA_4445)
        self.assertNotIn(fix_exit.outcome_marker(fix_exit.APPROVED), line)
        self.assertFalse(fix_exit.overtaken_by_approval(line))

    def test_the_marker_is_hidden_and_the_line_stays_one_line(self):
        marker = fix_exit.outcome_marker(fix_exit.APPROVED)
        self.assertTrue(marker.startswith("<!--") and marker.endswith("-->"))
        for kind in (fix_exit.APPROVED, fix_exit.NOTHING):
            line = fix_exit.quiet_line(kind, "2", SHA_4445)
            self.assertEqual(1, len(line.splitlines()), line)

    def test_the_approved_prose_without_the_marker_still_counts(self):
        # The English is not the signal: the same sentence with the marker
        # stripped is an ordinary no-commit line.
        marker = fix_exit.outcome_marker(fix_exit.APPROVED)
        prose = _quiet(fix_exit.APPROVED).replace(marker, "")
        self.assertNotIn(marker, prose)
        pr = _pr_4445()
        pr["comments"][-1] = _worker(prose)
        self.assertEqual(reconcile._no_commit_fix_runs(pr), 1)

    def test_the_marker_only_counts_on_the_first_line(self):
        # A comment that quotes an approved quiet line below its own first
        # line is not one.
        marker = fix_exit.outcome_marker(fix_exit.APPROVED)
        body = ("🛑 Fix attempt 3 pushed no new commit (branch still at "
                f"`89e65c3f`) — a person is needed.\n\n> {marker}")
        self.assertFalse(fix_exit.overtaken_by_approval(body))
        pr = _pr_4445()
        pr["comments"][-1] = _worker(body)
        self.assertEqual(reconcile._no_commit_fix_runs(pr), 1)


if __name__ == "__main__":
    unittest.main()
