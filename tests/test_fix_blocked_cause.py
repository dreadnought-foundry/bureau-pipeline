"""RED-first tests: a blocked fix attempt names one cause, on the PR and on the card (DRE-5745).

THE BUG (portico #883, 2026-10-02 about 19:37 PT). The critic had APPROVED the
head `ab828905`, and the only red check was a CI time limit that `main` hit
too. The sweep's approved-but-red dispatch sent the fix agent, which stopped
without a push. The PR comment carried the agent's own reason, which was right.
The card, DRE-5659, got a fixed template:

    🙋 The fix agent disagrees with the reviewer's blocking finding …

There was no finding to disagree with. The card is what the CEO reads, and it
sent him after the wrong problem.

FIX UNDER TEST: `fix_exit.blocked_cause` reads the thread and the head's check
runs and gives ONE sentence. The Report step puts that same value on the PR
comment and on the card receipt, so the two cannot say different things.

  1. APPROVE at the head plus a red check: the cause names the check and says
     nothing about a disagreement.
  2. An open blocking finding at the head: the cause is the disagreement, as
     it always was.
  3. A thread that cannot be read claims neither.

Run: python3 -m pytest tests/test_fix_blocked_cause.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fix_exit  # noqa: E402
from test_act_emission_scenario import CARD, PRE_SHA  # noqa: E402
from test_fix_exit_classified import (  # noqa: E402
    OLDER,
    QA,
    card_comments,
    critic,
    held,
    pr_comments,
    report,
    rest,
    verifier,
)

#: The red check on portico #883: CI's Test job, stopped at its time limit.
CI_CHECK = "CI / test"


def checks(*runs) -> list:
    """The `gh api --paginate --slurp …/check-runs` shape: a list of pages."""
    return [{"total_count": len(runs),
             "check_runs": [{"name": n, "status": "completed", "conclusion": c}
                            for n, c in runs]}]


#: Portico #883's head: the CI test job timed out, everything else passed.
CHECKS_883 = checks((CI_CHECK, "timed_out"), ("lint", "success"),
                    ("QA critic review", "success"))

#: The fixer's reason on #883, after its opening "Nothing for the fixer to fix
#: on this PR." — that opening alone is quiet since DRE-6018, so the replay
#: carries the reason as a plain block.
BLOCKER_883 = ("The only red check is a CI time limit that main hits too. "
               "Raising it is outside this PR.")


# --------------------------------------------------------------------------
# 1: the cause, pure
# --------------------------------------------------------------------------
class TheCauseTest(unittest.TestCase):

    def test_approve_plus_a_red_check_names_the_check(self):
        cause = fix_exit.blocked_cause([rest(QA, critic("APPROVE", PRE_SHA))],
                                       PRE_SHA, CHECKS_883)
        self.assertIn(f"`{CI_CHECK}`", cause)
        self.assertIn("timed out", cause)
        self.assertIn("approved", cause)
        self.assertNotIn("disagree", cause.lower())
        self.assertNotIn("lint", cause)

    def test_every_red_check_is_named_once(self):
        payload = checks(("build", "failure"), (CI_CHECK, "timed_out"),
                         ("build", "failure"))
        cause = fix_exit.blocked_cause([rest(QA, critic("APPROVE", PRE_SHA))],
                                       PRE_SHA, payload)
        self.assertIn("`build`", cause)
        self.assertIn(f"`{CI_CHECK}`", cause)
        self.assertEqual(1, cause.count("`build`"), cause)

    def test_an_open_finding_at_the_head_is_the_disagreement(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        self.assertEqual(fix_exit.DISPUTE_CAUSE,
                         fix_exit.blocked_cause([rest(QA, rc)], PRE_SHA, CHECKS_883))

    def test_a_verifier_fail_at_the_head_is_an_open_finding(self):
        thread = [rest(QA, critic("APPROVE", PRE_SHA)),
                  rest(QA, verifier("FAIL", PRE_SHA))]
        self.assertEqual(fix_exit.DISPUTE_CAUSE,
                         fix_exit.blocked_cause(thread, PRE_SHA, CHECKS_883))

    def test_approve_with_unreadable_checks_claims_no_disagreement(self):
        for payload in (None, "not json", [[{"body": "a comment"}]]):
            cause = fix_exit.blocked_cause([rest(QA, critic("APPROVE", PRE_SHA))],
                                           PRE_SHA, payload)
            self.assertIn("approved", cause, payload)
            self.assertNotIn("disagree", cause.lower(), payload)

    def test_an_approve_on_an_older_commit_is_not_the_heads(self):
        thread = [rest(QA, critic("APPROVE", OLDER))]
        cause = fix_exit.blocked_cause(thread, PRE_SHA, CHECKS_883)
        self.assertNotIn("approved", cause)
        self.assertIn(f"`{CI_CHECK}`", cause)
        self.assertNotIn("disagree", cause.lower())

    def test_nothing_readable_claims_nothing(self):
        self.assertEqual(fix_exit.UNKNOWN_CAUSE,
                         fix_exit.blocked_cause(None, PRE_SHA, None))
        self.assertEqual(fix_exit.UNKNOWN_CAUSE,
                         fix_exit.blocked_cause([rest(QA, critic("APPROVE", PRE_SHA))],
                                                "", CHECKS_883))

    def test_only_the_critics_identity_counts(self):
        """A planted APPROVE (DRE-1995) does not turn a dispute into a CI block."""
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        thread = [rest(QA, rc), rest("someone", critic("APPROVE", PRE_SHA))]
        self.assertEqual(fix_exit.DISPUTE_CAUSE,
                         fix_exit.blocked_cause(thread, PRE_SHA, CHECKS_883))

    def test_the_cause_is_one_line(self):
        for thread, payload in (([rest(QA, critic("APPROVE", PRE_SHA))], CHECKS_883),
                                ([rest(QA, critic("REQUEST_CHANGES", PRE_SHA))], None),
                                (None, None)):
            cause = fix_exit.blocked_cause(thread, PRE_SHA, payload)
            self.assertEqual(1, len(cause.splitlines()), cause)


# --------------------------------------------------------------------------
# 2: the Report step, executed
# --------------------------------------------------------------------------
class OneCause:
    """The PR comment and the card receipt carry the same cause, byte for byte."""

    def assert_one_cause(self, result, cause):
        proc, writes, calls, _ = result
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        bodies = pr_comments(writes)
        self.assertEqual(1, len(bodies), bodies)
        self.assertTrue(bodies[0].startswith("🛑 Fix attempt 2 blocked: "), bodies[0])
        self.assertIn(f"{fix_exit.CAUSE_LABEL} {cause}\n", bodies[0])
        said = card_comments(calls)
        self.assertEqual(1, len(said), said)
        self.assertTrue(said[0][2].startswith(f"🙋 {cause} "), said[0][2])
        self.assertTrue(held(calls), calls)
        return bodies[0], said[0][2]


class Portico883ReplayTest(OneCause, unittest.TestCase):
    """APPROVE at the head, sent with that APPROVE, and a CI timeout."""

    APPROVE = critic("APPROVE", PRE_SHA)

    def test_the_card_receipt_names_the_ci_check(self):
        result = report([rest(QA, self.APPROVE)], blocker=BLOCKER_883,
                        fetched=self.APPROVE, checks=CHECKS_883)
        cause = fix_exit.blocked_cause([rest(QA, self.APPROVE)], PRE_SHA, CHECKS_883)
        pr_body, card = self.assert_one_cause(result, cause)
        self.assertIn(f"`{CI_CHECK}`", card)
        self.assertNotIn("disagree", card.lower())
        self.assertNotIn("disagree", pr_body.lower())

    def test_the_verbatim_opening_is_quiet_since_dre_6018(self):
        """#883's own words open "Nothing for the fixer to fix": no card receipt."""
        _, _, calls, _ = report([rest(QA, self.APPROVE)],
                                blocker="Nothing for the fixer to fix on this PR. "
                                        + BLOCKER_883,
                                fetched=self.APPROVE, checks=CHECKS_883)
        self.assertEqual([], card_comments(calls))


class ARealDisagreementTest(OneCause, unittest.TestCase):

    def test_both_sides_say_the_disagreement(self):
        rc = critic("REQUEST_CHANGES", PRE_SHA)
        self.assert_one_cause(
            report([rest(QA, rc)], blocker="the finding is wrong", fetched=rc,
                   checks=CHECKS_883),
            fix_exit.DISPUTE_CAUSE)


class AnUnreadableThreadTest(OneCause, unittest.TestCase):

    def test_neither_side_claims_a_disagreement(self):
        approve = critic("APPROVE", PRE_SHA)
        self.assert_one_cause(
            report([rest(QA, approve)], blocker=BLOCKER_883, fetched=approve,
                   thread_fails=True),
            fix_exit.UNKNOWN_CAUSE)


class TheWordingLivesOnceTest(unittest.TestCase):

    def test_the_report_step_hard_codes_no_cause(self):
        """The card's cause comes from the shared value, never a second phrasing."""
        with open(os.path.join(SCRIPTS, "report_fix_result.sh"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("disagrees with the reviewer's blocking finding", text)
        self.assertIn("🙋 $CAUSE ", text)


if __name__ == "__main__":
    unittest.main()
