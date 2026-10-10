"""plan.yml holds an epic whose blocker epic is open before it claims a planner
slot (DRE-6671).

THE WIRING UNDER TEST:

  1. one step, `id: epic_wait`, after `Card-validation gate` and before the
     planner slot step (`id: slot`), runs `epic_wait.py hold` with the card
     through `env:` (DRE-1996) on the planner's own Linear key (DRE-5589);
  2. it exits 0 on every path, and a crash of the script before it answers is
     `held=false` and a `::warning::`, never a red planner run;
  3. a run the gate HELD claims no planner slot and reaches no classifier, no
     shape step, no route step, no model step and no Green Light writer —
     evaluated by walking every step's `if:` through the step outputs it names
     (`test_planner_queue_wiring.reach`), never by reading step names;
  4. the non-vacuous twin: a run the gate did not hold can still reach every
     one of them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_wait_wiring.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_planner_queue_wiring import (  # noqa: E402
    PLANNER_KEY_EXPR,
    ROOT,
    by_id,
    gate,
    index_where,
    must_not_run,
    reach,
    steps,
)

EPIC_WAIT = "epic_wait"
SCRIPT = os.path.join(ROOT, "scripts", "epic_wait.py")
HELD = {"steps.epic_wait.outputs.held": "true"}
NOT_HELD = {"steps.epic_wait.outputs.held": "false",
            "steps.slot.outputs.admitted": "true"}
# The steps the card names, by `id:` — each must be skipped on a held run.
NAMED = ("slot", "classify", "shape", "route", "claude")


def _at(ident: str) -> int:
    return index_where(lambda s: s.get("id") == ident)


class TheGateStep(unittest.TestCase):
    def setUp(self):
        self.step = by_id(EPIC_WAIT)
        self.run_block = str(self.step.get("run") or "")
        self.env = self.step.get("env") or {}

    def test_it_sits_after_the_card_gate_and_before_the_slot(self):
        at = _at(EPIC_WAIT)
        self.assertGreater(at, _at("gate"))
        self.assertLess(at, _at("slot"))
        self.assertLess(at, _at("dedupe"))

    def test_it_runs_the_hold_with_the_card_and_the_output_file(self):
        self.assertIn('python3 .bureau-pipeline/scripts/epic_wait.py hold "$CARD"',
                      self.run_block)
        self.assertIn('--github-output "$GITHUB_OUTPUT"', self.run_block)

    def test_the_cli_accepts_the_flag_the_step_passes(self):
        out = subprocess.run([sys.executable, SCRIPT, "hold", "--help"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("--github-output", out)

    def test_every_value_arrives_through_env(self):
        self.assertNotIn("${{", self.run_block)
        self.assertEqual(self.env.get("CARD"),
                         "${{ github.event.client_payload.identifier }}")
        self.assertEqual(self.env.get("LINEAR_API_KEY"), PLANNER_KEY_EXPR)
        self.assertEqual(self.env.get("LINEAR_API_KEY_FALLBACK"),
                         "${{ secrets.LINEAR_API_KEY }}")

    def test_it_is_skipped_on_a_bounced_card(self):
        self.assertIn("steps.gate.outputs.bounced != 'true'", gate(self.step))

    def test_it_exits_zero_on_every_path(self):
        self.assertNotIn("continue-on-error", self.step,
                         "a failed-but-continued step would leave `held` unset")
        self.assertIn("||", self.run_block)
        self.assertIn("held=false", self.run_block)
        self.assertIn("::warning::", self.run_block)


class AHeldRun(unittest.TestCase):
    def test_the_slot_classifier_shape_and_planner_are_all_skipped(self):
        verdict = reach(HELD)
        for ident in NAMED:
            self.assertIs(verdict[_at(ident)], False,
                          f"step {ident!r} is reachable on a held run")

    def test_it_reaches_no_model_step_no_route_and_no_green_light_writer(self):
        verdict = reach(HELD)
        names = [s.get("name") for s in steps()]
        for i, why in must_not_run().items():
            self.assertIs(verdict[i], False,
                          f"{why} {names[i]!r} is reachable on a held run")

    def test_the_duplicate_guard_does_not_run_either(self):
        self.assertIs(reach(HELD)[_at("dedupe")], False)

    def test_a_run_not_held_can_still_reach_every_one_of_them(self):
        """The non-vacuous twin: the walk is not marking everything false."""
        verdict = reach(NOT_HELD)
        for ident in NAMED:
            self.assertIsNot(verdict[_at(ident)], False, ident)
        for i in must_not_run():
            self.assertIsNot(verdict[i], False, steps()[i].get("name"))


if __name__ == "__main__":
    unittest.main()
