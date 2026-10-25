"""RED-first tests for weighing the candidate's own unit suite (DRE-6968).

On 2026-10-11 `stable` moved three times onto commits whose own `Pipeline
Tests` run on `main` was failing, or still running and about to fail: Promote
Channel runs 38167947123, 38169771118 and 38170321716, each printing
`harness-passed-promoting … harness green, strictly ahead. Mirror check:
passed`. The promotion asked the harness and agent-bureau's mirror tests about
a commit and never asked about the pipeline's own unit suite. Two pull
requests that each passed alone (#1031, #1030) failed together on `main`.

`with_unit_suite` is the decision only, in `with_mirror`'s shape: it weighs
the candidate's `Pipeline Tests` run, handed to it as a small record, against
a decision that would move the channel. With no `--unit-suite` on the command
line nothing changes — the workflow that hands the run over is a second card.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import mirror_check  # noqa: E402
import promote_channel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

SHA = "9e6fc4d1" + "0" * 32
OPERATOR = "sid"
REASON = "the red test polices test files, not the pipeline"

#: The runs measured on 2026-10-11.
RED = {"run_id": 38167776550, "status": "completed", "conclusion": "failure"}
TIMED_OUT = {"run_id": 38167776550, "status": "completed",
             "conclusion": "timed_out"}
RUNNING = {"run_id": 38167358867, "status": "in_progress", "conclusion": None}
QUEUED = {"run_id": 38167358867, "status": "queued", "conclusion": None}
GREEN = {"run_id": 38171950338, "status": "completed", "conclusion": "success"}
CANCELLED = {"status": "completed", "conclusion": "cancelled"}

MIRROR_PASSED = {"status": mirror_check.MIRROR_PASSED, "tests": 43,
                 "agent_bureau_sha": "a" * 40, "failing": [], "already_red": []}


def _promoting(outcome=promote_channel.OUTCOME_PROMOTING):
    return promote_channel.Decision(
        True, f"promoting stable to {SHA} — harness green, strictly ahead.",
        outcome)


def _weigh(unit, decision=None, **kw):
    return promote_channel.with_unit_suite(
        _promoting() if decision is None else decision, unit, sha=SHA, **kw)


class OutcomeNamesTest(unittest.TestCase):
    def test_the_three_outcomes_are_named(self):
        self.assertEqual(promote_channel.OUTCOME_UNIT_RED, "unit-suite-red")
        self.assertEqual(promote_channel.OUTCOME_UNIT_NOT_FINISHED,
                         "unit-suite-not-finished")
        self.assertEqual(promote_channel.OUTCOME_UNIT_UNPROVEN,
                         "unit-suite-unproven")


class WithUnitSuiteTest(unittest.TestCase):
    def test_a_refusing_decision_is_returned_unchanged(self):
        held = promote_channel.Decision(
            False, "channel HELD", promote_channel.OUTCOME_HELD)
        for unit in (RED, RUNNING, None, GREEN):
            with self.subTest(unit=unit):
                self.assertEqual(_weigh(unit, held), held)

    def test_a_decision_outside_the_promoting_outcomes_is_returned_unchanged(self):
        odd = promote_channel.Decision(True, "something else", "not-a-promotion")
        self.assertEqual(_weigh(RED, odd), odd)

    def test_a_green_suite_leaves_a_promotion_as_it_was(self):
        for decision in (_promoting(),
                         _promoting(promote_channel.OUTCOME_BY_HAND),
                         _promoting(promote_channel.OUTCOME_BY_HAND_FORCED)):
            for unit in (GREEN, {"status": "completed", "conclusion": "success"}):
                with self.subTest(outcome=decision.outcome, unit=unit):
                    self.assertEqual(_weigh(unit, decision), decision)

    def test_a_red_suite_refuses(self):
        for unit in (RED, TIMED_OUT):
            with self.subTest(conclusion=unit["conclusion"]):
                decision = _weigh(unit)
                self.assertFalse(decision.promote)
                self.assertEqual(decision.outcome,
                                 promote_channel.OUTCOME_UNIT_RED)
                self.assertIn(SHA, decision.reason)
                self.assertIn("38167776550", decision.reason)
                self.assertIn("failed on main", decision.reason)

    def test_an_unfinished_suite_refuses_and_says_stable_waits(self):
        for unit in (RUNNING, QUEUED):
            with self.subTest(status=unit["status"]):
                decision = _weigh(unit)
                self.assertFalse(decision.promote)
                self.assertEqual(decision.outcome,
                                 promote_channel.OUTCOME_UNIT_NOT_FINISHED)
                self.assertIn("38167358867", decision.reason)
                self.assertIn("waits", decision.reason)

    def test_an_unreadable_or_inconclusive_record_refuses_as_unproven(self):
        for unit in (None, [RED], {}, CANCELLED, "failure",
                     {"status": "completed", "conclusion": None}):
            with self.subTest(unit=unit):
                decision = _weigh(unit)
                self.assertFalse(decision.promote)
                self.assertEqual(decision.outcome,
                                 promote_channel.OUTCOME_UNIT_UNPROVEN)
                self.assertIn(SHA, decision.reason)

    def test_the_by_hand_routes_are_weighed_too(self):
        for outcome in (promote_channel.OUTCOME_BY_HAND,
                        promote_channel.OUTCOME_BY_HAND_FORCED):
            with self.subTest(outcome=outcome):
                decision = _weigh(RED, _promoting(outcome))
                self.assertFalse(decision.promote)
                self.assertEqual(decision.outcome,
                                 promote_channel.OUTCOME_UNIT_RED)

    def test_force_promotes_past_each_refusal_and_names_what_it_overrode(self):
        for unit, overrode in (
                (RED, promote_channel.OUTCOME_UNIT_RED),
                (RUNNING, promote_channel.OUTCOME_UNIT_NOT_FINISHED),
                (None, promote_channel.OUTCOME_UNIT_UNPROVEN)):
            with self.subTest(overrode=overrode):
                decision = _weigh(
                    unit, _promoting(promote_channel.OUTCOME_BY_HAND),
                    force=True, actor=OPERATOR, reason=REASON)
                self.assertTrue(decision.promote)
                self.assertEqual(decision.outcome,
                                 promote_channel.OUTCOME_BY_HAND_FORCED)
                self.assertIn(OPERATOR, decision.reason)
                self.assertIn(REASON, decision.reason)
                self.assertIn(overrode, decision.reason)
                self.assertIn("unit suite", decision.reason)

    def test_force_on_a_green_suite_changes_nothing(self):
        decision = _promoting(promote_channel.OUTCOME_BY_HAND)
        self.assertEqual(
            _weigh(GREEN, decision, force=True, actor=OPERATOR, reason=REASON),
            decision)


class CliTest(unittest.TestCase):
    """`--unit-suite` on the command line, and its absence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, argv, combined=None):
        statuses = self.tmp / "s.json"
        statuses.write_text(json.dumps(combined if combined is not None else {
            "statuses": [{"context": promote_channel.STATUS_CONTEXT,
                          "state": "success"}]}))
        out = self.tmp / "gh_output"
        out.write_text("")
        printed = io.StringIO()
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(printed):
                rc = promote_channel.main(
                    ["--sha", SHA, "--statuses-file", str(statuses), *argv])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        self.assertEqual(rc, 0)
        return printed.getvalue(), out.read_text()

    def _unit_file(self, record):
        path = self.tmp / "unit_suite.json"
        path.write_text(json.dumps(record))
        return str(path)

    def test_without_the_flag_the_outputs_are_the_proof_alone(self):
        """The receipt and outputs `main` wrote before this card, rebuilt from
        `evaluate` — the decision this card does not touch."""
        for argv, kw in (
                (["--ancestry", "ahead"], {"ancestry": "ahead"}),
                (["--ancestry", "ahead", "--hold", "who=Ada paused"],
                 {"ancestry": "ahead", "hold": "who=Ada paused"})):
            with self.subTest(argv=argv):
                printed, written = self._run(argv)
                expected = promote_channel.evaluate(
                    {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                                   "state": "success"}]}, SHA, **kw)
                self.assertEqual(printed,
                                 f"{expected.outcome}: {expected.reason}\n")
                self.assertEqual(written, (
                    f"promote={'true' if expected.promote else 'false'}\n"
                    f"reason={' '.join(expected.reason.split())}\n"
                    f"outcome={expected.outcome}\n"))

    def test_a_red_suite_file_refuses(self):
        printed, written = self._run(
            ["--ancestry", "ahead", "--unit-suite", self._unit_file(RED)])
        self.assertTrue(printed.startswith(
            f"{promote_channel.OUTCOME_UNIT_RED}:"), printed)
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_RED}\n", written)

    def test_a_missing_file_is_unproven(self):
        _, written = self._run(
            ["--ancestry", "ahead", "--unit-suite",
             str(self.tmp / "nowhere.json")])
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_UNPROVEN}\n",
                      written)

    def test_a_file_that_is_not_json_is_unproven(self):
        path = self.tmp / "garbage.json"
        path.write_text("not json {")
        _, written = self._run(
            ["--ancestry", "ahead", "--unit-suite", str(path)])
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_UNPROVEN}\n",
                      written)

    def test_a_green_suite_file_promotes(self):
        _, written = self._run(
            ["--ancestry", "ahead", "--unit-suite", self._unit_file(GREEN)])
        self.assertIn("promote=true", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_PROMOTING}\n", written)

    def test_a_red_suite_is_answered_before_the_mirror_check(self):
        mirror = self.tmp / "mirror_result.json"
        mirror.write_text(json.dumps(MIRROR_PASSED))
        _, written = self._run(
            ["--ancestry", "ahead", "--unit-suite", self._unit_file(RED),
             "--mirror-result", str(mirror)])
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_RED}\n", written)
        self.assertNotIn("mirror=", written)

    def test_a_refusal_before_it_is_not_reported_as_a_unit_suite_one(self):
        _, written = self._run(
            ["--ancestry", "ahead", "--hold", "who=Ada paused",
             "--unit-suite", self._unit_file(RED)])
        self.assertIn(f"outcome={promote_channel.OUTCOME_HELD}\n", written)

    def test_force_on_the_by_hand_route_passes_a_red_suite(self):
        _, written = self._run(
            ["--manual", "--force", "--reason", REASON, "--actor", OPERATOR,
             "--ancestry", "ahead", "--trunk", "behind",
             "--unit-suite", self._unit_file(RED)])
        self.assertIn("promote=true", written)
        self.assertIn(
            f"outcome={promote_channel.OUTCOME_BY_HAND_FORCED}\n", written)

    def test_force_on_the_automatic_route_is_never_passed(self):
        """A `workflow_run` carrying a stray `--force` must not reach the
        forced arm — the caller passes force only with `--manual`."""
        _, written = self._run(
            ["--force", "--reason", REASON, "--actor", OPERATOR,
             "--ancestry", "ahead", "--unit-suite", self._unit_file(RED)])
        self.assertIn("promote=false", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_RED}\n", written)


class DocsTest(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "self-hosting.md").read_text()

    def test_the_receipt_table_carries_the_three_outcomes(self):
        for outcome in ("unit-suite-red", "unit-suite-not-finished",
                        "unit-suite-unproven"):
            with self.subTest(outcome=outcome):
                self.assertTrue(outcome in self.doc,
                                f"{outcome} is not in docs/self-hosting.md")
                rows = [line for line in self.doc.splitlines()
                        if line.startswith(f"| `{outcome}` |")]
                self.assertEqual(len(rows), 1, outcome)

    def test_what_did_not_move_says_the_unit_suite_is_weighed(self):
        flat = " ".join(self.doc.split())
        self.assertIn("DRE-6968", flat)
        self.assertIn("`--unit-suite`", flat)


if __name__ == "__main__":
    unittest.main()
