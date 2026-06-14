"""Todo-entry card-validation gate — workflow wiring (DRE-1405).

Unit tests on the pure `missing()` function can't catch a gate that runs in the
wrong place or downstream steps that forgot the bounce guard. These pin the YAML
structure of agent-task.yml and plan.yml:
  - the gate runs as the FIRST real step, BEFORE the card flips to In Progress;
  - the agent/implement step is guarded so a bounced card never builds.
"""

import os
import re
import unittest

WF_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def steps_order(workflow: str) -> list[str]:
    """Ordered list of step `name:` values for the workflow file."""
    src = open(os.path.join(WF_DIR, workflow)).read()
    return re.findall(r"^\s*-\s*name:\s*(.+?)\s*$", src, re.MULTILINE)


def src(workflow: str) -> str:
    return open(os.path.join(WF_DIR, workflow)).read()


class AgentTaskWiringTest(unittest.TestCase):
    WF = "agent-task.yml"

    def test_gate_step_exists(self):
        self.assertTrue(
            any("validation gate" in s.lower() for s in steps_order(self.WF)),
            "agent-task.yml must have a card-validation gate step",
        )

    def test_gate_runs_before_in_progress(self):
        order = [s.lower() for s in steps_order(self.WF)]
        gate = next(i for i, s in enumerate(order) if "validation gate" in s)
        in_prog = next(i for i, s in enumerate(order) if "in progress" in s)
        self.assertLess(gate, in_prog, "gate must run before Card → In Progress")

    def test_gate_calls_validate_card(self):
        self.assertIn("validate_card.py", src(self.WF))

    def test_gate_step_has_id_and_bounced_guard(self):
        body = src(self.WF)
        self.assertRegex(body, r"id:\s*gate")
        # Downstream steps must skip on a bounce.
        self.assertIn("steps.gate.outputs.bounced", body)

    def test_implement_step_guarded_against_bounce(self):
        # The expensive agent step must not run for a bounced card.
        body = src(self.WF)
        m = re.search(r"name:\s*Implement card(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Implement card step not found")
        self.assertIn("steps.gate.outputs.bounced", m.group(1))


class PlanWiringTest(unittest.TestCase):
    WF = "plan.yml"

    def test_gate_step_exists(self):
        self.assertTrue(
            any("validation gate" in s.lower() for s in steps_order(self.WF)),
            "plan.yml must have a card-validation gate step",
        )

    def test_gate_runs_before_route(self):
        order = [s.lower() for s in steps_order(self.WF)]
        gate = next(i for i, s in enumerate(order) if "validation gate" in s)
        route = next(i for i, s in enumerate(order) if "plan or activate" in s)
        self.assertLess(gate, route, "gate must run before the plan/activate route")

    def test_gate_calls_validate_card(self):
        self.assertIn("validate_card.py", src(self.WF))

    def test_route_step_guarded_against_bounce(self):
        body = src(self.WF)
        m = re.search(r"name:\s*Route — plan or activate(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Route step not found")
        self.assertIn("steps.gate.outputs.bounced", m.group(1))


if __name__ == "__main__":
    unittest.main()
