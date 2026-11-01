"""A PROOF row that waits on the operator's press is refused at the first
critic, for every press and every repo (DRE-7950).

A press only a person can make after merge — a machine update, a night-window
apply — belongs on its own OPERATOR card, and the PROOF is blocked by that
card and reads its result. DRE-7917 is that card for DRE-7919. Until this card
the plan critic refused a person's row only for a listed sandbox press on an
agent-bureau PROOF (DRE-7779), so every other press was still written as
`waiting for the operator's press: <the press>`, and that is how proof records
came to park on the operator.

The fixture is epic DRE-7912 as filed, trimmed to one build card, the
operator step and the PROOF, and the same PROOF with the step written as the
operator's-press row instead.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_person_press_refused.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.abspath(os.path.join(ROOT, "scripts")))
sys.path.insert(0, HERE)

import plan_critic  # noqa: E402
import plan_proof_reach as ppr  # noqa: E402
import plan_seam_gate  # noqa: E402
import proof_record  # noqa: E402
from test_plan_proof_reach import (  # noqa: E402
    _Cli, fixture as sandbox_fixture, fixture_criteria, proof, read, sandbox_line,
    sandbox_lines, split)

FIXTURE = os.path.join(HERE, "fixtures", "plan-after-merge-operator-step.json")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")

OPERATOR_ROW = ppr.PERSON_ROWS[0]
CEO_ROW = ppr.PERSON_ROWS[1]


def plans() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def press_lines(lines):
    return [l for l in lines if "waits on the operator's press" in l]


def press_line(ident: str, criterion: str) -> str:
    return ppr.FINDING_PERSON_PRESS.format(
        proof=ident, criterion=proof_record._cut(criterion, ppr.CUT))


class TheSentence(unittest.TestCase):

    def test_the_sentence(self):
        self.assertEqual(
            ppr.FINDING_PERSON_PRESS,
            "{proof}: criterion “{criterion}” waits on the operator's press, a "
            "step only a person can make after merge — file that press as an "
            "OPERATOR card this PROOF is blocked by, and write the criterion as "
            "the reading of that card's result.")

    def test_the_rows_are_kept_as_they_are(self):
        # `plan_ceo_steps` reads both rows; this card reads only the first.
        self.assertEqual(("waiting for the operator's press:", "needs the CEO's press:"),
                         ppr.PERSON_ROWS)

    def test_the_standard_quotes_it_beside_the_sandbox_sentence(self):
        with open(STANDARD, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(ppr.FINDING_PERSON_PRESS, text)
        here = text.index(ppr.FINDING_PERSON_PRESS)
        self.assertLess(abs(here - text.index(ppr.FINDING_SANDBOX_PRESS)), 1000)


class TheDre7912Plan(unittest.TestCase):
    """The fixture, checked twice."""

    def test_the_step_as_an_operator_card_the_proof_is_blocked_by(self):
        plan = plans()["operator_card_plan"]
        self.assertTrue(ppr.is_operator_card(plan[1], "DRE-7919"))
        self.assertIn("DRE-7917", plan[2]["blocked_by"])
        self.assertIn("DRE-7917", ppr.criteria(plan[2]["body"])[0])
        got = read(plan)
        self.assertEqual([], got.lines)
        self.assertEqual(1, got.proofs)

    def test_the_step_as_the_operators_press_row(self):
        fx = plans()
        self.assertEqual(f"{OPERATOR_ROW} update the helper on studio-1", fx["press_row"])
        plan = fx["press_row_plan"]
        self.assertEqual([fx["press_row"]], ppr.criteria(plan[-1]["body"]))
        lines = read(plan).lines
        self.assertEqual([press_line("DRE-7919", fx["press_row"])], lines)
        self.assertTrue(lines[0].startswith("DRE-7919: criterion “"), lines[0])
        self.assertIn(f"“{fx['press_row']}”", lines[0])

    def test_the_operator_rule_still_fires_for_the_card_unblocked(self):
        plan = plans()["operator_card_plan"]
        plan[2] = dict(plan[2], blocked_by=["DRE-7913"])
        lines = read(plan).lines
        self.assertEqual(1, len(lines), lines)
        self.assertEqual(lines[0], ppr.FINDING_OPERATOR.format(
            proof="DRE-7919",
            criterion=proof_record._cut(ppr.criteria(plan[2]["body"])[0], ppr.CUT),
            operator="DRE-7917", title=plan[1]["title"]))


class EveryRepoEveryPress(unittest.TestCase):

    ROW = f"{OPERATOR_ROW} on studio-1, apply 3 Large Heavy + 4 Light in the night window"

    def labeled(self, *labels):
        return dict(proof("DRE-7960", self.ROW), labels=list(labels) + ["agent:ops"])

    def test_a_proof_for_another_repo(self):
        for repo in ("repo:bureau-pipeline", "repo:portico"):
            lines = read([self.labeled(repo)]).lines
            self.assertEqual([press_line("DRE-7960", self.ROW)], lines, repo)

    def test_a_proof_read_for_the_caller(self):
        for labels in ((), ("repo:not-in-the-map",)):
            for caller in ("agent-bureau", "bureau-pipeline", False):
                lines = read([self.labeled(*labels)], caller_slug=caller).lines
                self.assertEqual([press_line("DRE-7960", self.ROW)], lines,
                                 (labels, caller))

    def test_any_case_and_a_curly_apostrophe(self):
        for row in (self.ROW.upper(), self.ROW.replace("'", "’"),
                    "Not observed. " + self.ROW):
            lines = read([proof("DRE-7961", row)]).lines
            self.assertEqual([press_line("DRE-7961", row)], lines, row)

    def test_a_long_criterion_is_cut(self):
        row = self.ROW + ", then run `make runner-bench` and paste both tables"
        lines = read([proof("DRE-7962", row)]).lines
        self.assertEqual([press_line("DRE-7962", row)], lines)
        self.assertIn(f"“{proof_record._cut(row, ppr.CUT)}”", lines[0])

    def test_a_criterion_naming_the_press_without_the_row_is_not_read(self):
        text = "The operator's press on studio-1 is recorded on DRE-7917."
        self.assertEqual([], press_lines(read([proof("DRE-7963", text)]).lines))


class OneCriterionOneFinding(unittest.TestCase):

    def test_a_listed_sandbox_press_on_an_agent_bureau_proof(self):
        rec = sandbox_fixture()
        lines = read([rec]).lines
        for n in (2, 3, 4):
            self.assertTrue(fixture_criteria()[n - 1].startswith(OPERATOR_ROW))
            mine = [l for l in lines
                    if proof_record._cut(fixture_criteria()[n - 1], ppr.CUT) in l]
            self.assertEqual([sandbox_line(n)], mine, n)

    def test_the_sandbox_and_the_press_findings_together(self):
        lines = read([sandbox_fixture()]).lines
        self.assertEqual([sandbox_line(n) for n in (2, 3, 4, 5)], sandbox_lines(lines))
        self.assertEqual([press_line("DRE-7900", fixture_criteria()[n - 1])
                          for n in (6, 7)], press_lines(lines))
        self.assertEqual(6, len(lines), lines)


class TheCeosPress(unittest.TestCase):

    def test_a_ceos_press_row_is_not_refused(self):
        row = f"{CEO_ROW} approve the price on the plan page"
        self.assertEqual([], read([proof("DRE-7970", row)]).lines)
        for repo in ("repo:bureau-pipeline", "repo:portico"):
            rec = dict(proof("DRE-7970", row), labels=[repo, "agent:ops"])
            self.assertEqual([], read([rec]).lines, repo)

    def test_the_fixtures_ceos_row_is_not_a_press_finding(self):
        rec = sandbox_fixture(labels=["repo:bureau-pipeline", "agent:ops"])
        lines = read([rec], caller_slug="agent-bureau").lines
        ceo = proof_record._cut(fixture_criteria()[4], ppr.CUT)
        self.assertEqual([], [l for l in press_lines(lines) if ceo in l])


class TheCommand(_Cli):

    def test_it_is_appended_to_the_structural_file_and_is_a_send_back(self):
        fx = plans()
        structural = self.write("structural.txt", "")
        code, out = self.run_findings(fx["press_row_plan"], "--structural-file", structural)
        self.assertEqual(0, code)
        want = press_line("DRE-7919", fx["press_row"])
        self.assertEqual([want], [l for l in self.text("structural.txt").splitlines() if l])
        self.assertIn(want, out)

        result = self.write("result.md", plan_critic.result_line(plan_critic.PASS) + "\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(0, plan_seam_gate.main(
                ["gate", "--result-file", result, "--structural-file", structural]))
        rewritten = self.text("result.md")
        verdict, _ = plan_critic.read_result(rewritten)
        self.assertEqual(plan_critic.SEND_BACK, verdict)
        self.assertIn(want, rewritten)

    def test_the_plan_as_filed_says_nothing(self):
        code, out = self.run_findings(plans()["operator_card_plan"])
        self.assertEqual(0, code)
        self.assertIn("no criterion out of reach — 1 criteria read on 1 proof card(s)", out)


if __name__ == "__main__":
    unittest.main()
