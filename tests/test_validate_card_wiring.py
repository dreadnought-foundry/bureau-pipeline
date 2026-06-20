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

    def test_children_validated_before_plan_review(self):
        # DRE-1715: the planner's created children are validated through the same
        # validate_card core BEFORE the epic moves to Plan Review / completes.
        order = [s.lower() for s in steps_order(self.WF)]
        validate = next(
            (i for i, s in enumerate(order) if "validate created children" in s), None
        )
        self.assertIsNotNone(validate, "plan.yml must validate created children (DRE-1715)")
        review = next(i for i, s in enumerate(order) if "plan review" in s)
        self.assertLess(validate, review, "children must be validated before Plan Review")

    def test_child_validation_uses_check_children(self):
        # Reuse the existing gate (check-children), not a parallel checker.
        body = src(self.WF)
        m = re.search(
            r"name:\s*Validate created children(.*?)(?:\n      - name:|\Z)", body, re.S
        )
        self.assertIsNotNone(m, "Validate created children step not found")
        step = m.group(1)
        self.assertIn("validate_card.py check-children", step)
        # A failed sweep must fail the plan (non-zero exit), not pass silently.
        self.assertIn("exit 1", step)


class PlanModelFallbackWiringTest(unittest.TestCase):
    """Planner-path model-fallback wiring (DRE-1354).

    The planner's primary model is Fable; when Fable is unavailable (404s,
    2026-06-14) a re-dispatched plan run must ride Opus instead of re-dying on
    the same dead model — exactly the engineer fix, applied to plan.yml. These
    pin the YAML so the planner agent is dispatched with the SELECTED model,
    not a hard-pinned one, and that a prior is_error death drives the switch.
    """

    WF = "plan.yml"

    def test_select_model_step_exists(self):
        self.assertTrue(
            any("select model" in s.lower() for s in steps_order(self.WF)),
            "plan.yml must have a Select model step (DRE-1354)",
        )

    def test_select_model_runs_before_plan_epic(self):
        order = [s.lower() for s in steps_order(self.WF)]
        sel = next(i for i, s in enumerate(order) if "select model" in s)
        plan = next(i for i, s in enumerate(order) if s == "plan epic")
        self.assertLess(sel, plan, "Select model must run before Plan epic")

    def test_select_calls_model_fallback_for_planner_role(self):
        body = src(self.WF)
        self.assertIn("model_fallback.py", body)
        # planner direction specifically — not the engineer default. The select
        # command may wrap across a line-continuation, so allow backslash+newline
        # whitespace between the verb and the role.
        self.assertRegex(body, r"select\s+(?:\\\s*)?planner")

    def test_select_step_guarded_against_bounce(self):
        body = src(self.WF)
        m = re.search(r"name:\s*Select model(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Select model step not found")
        self.assertIn("steps.gate.outputs.bounced", m.group(1))

    def test_plan_epic_uses_selected_model_not_hardpin(self):
        body = src(self.WF)
        m = re.search(r"name:\s*Plan epic(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Plan epic step not found")
        plan_step = m.group(1)
        # The dispatched model must come from the Select-model step's output, so
        # a Fable outage actually swings the planner to Opus.
        self.assertIn("steps.model.outputs.model", plan_step)
        # And the dead hard-pin must be gone from the dispatch.
        self.assertNotIn("--model claude-fable-5", plan_step)

    def test_planner_heartbeat_records_model_attempt(self):
        # AC: the heartbeat/comment records which model each attempt used, using
        # the same machine-parseable marker the selector reads back.
        body = src(self.WF)
        self.assertIn("model-attempt:", body)

    def test_is_error_death_recorded_for_fallback(self):
        # A planner is_error death must stamp a model-error: marker so the medic
        # rerun's Select-model step swings to the alternate model. Uses the same
        # detection (check_agent_result) and marker (model_fallback) as engineer.
        body = src(self.WF)
        m = re.search(r"name:\s*Record is_error death(.*?)(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "Record is_error death step not found")
        step = m.group(1)
        # always(): a dead run is exactly when this must fire.
        self.assertIn("always()", step)
        self.assertIn("check_agent_result", step)
        self.assertIn("error_marker", step)


if __name__ == "__main__":
    unittest.main()
