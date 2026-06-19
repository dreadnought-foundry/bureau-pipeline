"""Propose (research-and-propose) workflow — wiring (DRE-1657).

propose.yml is a per-card analog of plan.yml: it RESEARCHES a card and posts a
plain-English proposed approach, writing NO code. These pin the YAML so the
read-only guarantee, the validation gate, the model selection, the Linear
state/label transitions, and the cost cap can't silently regress — a unit test
on a helper can't catch a workflow that, say, re-enabled Edit/Write or forgot
the bounce guard.
"""

import os
import re
import unittest

WF_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
WF = "propose.yml"


def steps_order(workflow: str) -> list[str]:
    src = open(os.path.join(WF_DIR, workflow)).read()
    return re.findall(r"^\s*-\s*name:\s*(.+?)\s*$", src, re.MULTILINE)


def src(workflow: str) -> str:
    return open(os.path.join(WF_DIR, workflow)).read()


def step_body(workflow: str, name: str) -> str:
    body = src(workflow)
    m = re.search(
        rf"name:\s*{re.escape(name)}(.*?)(?:\n      - name:|\Z)", body, re.S
    )
    assert m is not None, f"{name!r} step not found in {workflow}"
    return m.group(1)


class ProposeReadOnlyTest(unittest.TestCase):
    """The defining invariant: this run can NEVER write code."""

    def test_toolset_is_read_only(self):
        step = step_body(WF, "Research and propose")
        # Allowed inspection tools present...
        self.assertRegex(step, r'--allowedTools\s+"[^"]*Read[^"]*"')
        m = re.search(r'--allowedTools\s+"([^"]*)"', step)
        self.assertIsNotNone(m, "no --allowedTools on the propose agent step")
        tools = m.group(1)
        for allowed in ("Bash", "Read", "Glob", "Grep"):
            self.assertIn(allowed, tools, f"{allowed} should be allowed")
        # ...and the write tools explicitly absent.
        self.assertNotIn("Edit", tools, "Edit must NOT be in the propose toolset")
        self.assertNotIn("Write", tools, "Write must NOT be in the propose toolset")

    def test_no_branch_or_pr_step(self):
        body = src(WF)
        self.assertNotIn("gh pr create", body, "propose must not open a PR")
        self.assertNotIn("git push", body, "propose must not push")
        self.assertNotIn("git checkout -b", body, "propose must not create a branch")

    def test_max_turns_capped_low(self):
        step = step_body(WF, "Research and propose")
        m = re.search(r"--max-turns\s+(\d+)", step)
        self.assertIsNotNone(m, "propose agent must cap --max-turns")
        self.assertLessEqual(int(m.group(1)), 30, "--max-turns must be capped low (<=30)")


class ProposeGateTest(unittest.TestCase):
    def test_gate_step_exists_and_calls_validate(self):
        self.assertTrue(
            any("validation gate" in s.lower() for s in steps_order(WF)),
            "propose.yml must have a card-validation gate step",
        )
        self.assertIn("validate_card.py", src(WF))

    def test_gate_runs_before_proposing(self):
        order = [s.lower() for s in steps_order(WF)]
        gate = next(i for i, s in enumerate(order) if "validation gate" in s)
        proposing = next(i for i, s in enumerate(order) if "proposing" in s)
        self.assertLess(gate, proposing, "gate must run before Card → Proposing")

    def test_expensive_steps_guarded_against_bounce(self):
        # The agent + state transitions must skip for a bounced card.
        for name in ("Research and propose", "Card → Proposing", "Card → Proposed (+ proposed label)"):
            self.assertIn(
                "steps.gate.outputs.bounced",
                step_body(WF, name),
                f"{name} must be guarded by the bounce gate",
            )


class ProposeModelSelectionTest(unittest.TestCase):
    """Mirrors agent-task.yml's engineer model selection (DRE-1490/1354)."""

    def test_select_model_step_exists(self):
        self.assertTrue(
            any("select model" in s.lower() for s in steps_order(WF)),
            "propose.yml must select a model via the shared fallback ladder",
        )
        self.assertIn("model_fallback.py", src(WF))

    def test_select_runs_before_agent(self):
        order = [s.lower() for s in steps_order(WF)]
        sel = next(i for i, s in enumerate(order) if "select model" in s)
        agent = next(i for i, s in enumerate(order) if "research and propose" in s)
        self.assertLess(sel, agent, "Select model must run before the propose agent")

    def test_agent_uses_selected_model(self):
        self.assertIn("steps.model.outputs.model", step_body(WF, "Research and propose"))


class ProposeLinearTransitionsTest(unittest.TestCase):
    def test_sets_proposing_then_proposed(self):
        order = [s.lower() for s in steps_order(WF)]
        proposing = next(i for i, s in enumerate(order) if s.strip() == "card → proposing")
        proposed = next(i for i, s in enumerate(order) if "card → proposed" in s)
        self.assertLess(proposing, proposed, "Proposing must precede Proposed")
        body = src(WF)
        self.assertIn('"Proposing"', body, "must set the Proposing state")
        self.assertIn('"Proposed"', body, "must set the Proposed state")

    def test_uses_linear_ops_state_and_label_helpers(self):
        # Reuse the same idempotent helpers the existing workflows use.
        body = src(WF)
        self.assertIn("linear_ops.py state", body)
        self.assertIn("linear_ops.py add-label", body)
        self.assertIn("add-label \"$CARD\" proposed", body)

    def test_posts_proposal_via_linear_comment(self):
        # The proposal reaches the human via a Linear comment (agent + backstop).
        body = src(WF)
        self.assertIn("linear_ops.py comment", body)


if __name__ == "__main__":
    unittest.main()
