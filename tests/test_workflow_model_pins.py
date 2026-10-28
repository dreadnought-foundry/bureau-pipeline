"""No workflow pins a model id; every agent workflow selects one (DRE-2316).

Carried out of tests/test_model_config.py when DRE-6982 deleted it with the
generator it tested: these checks read only the workflow files, and nothing
else holds the whole directory to them. tests/test_agents_registry.py holds
the roster's own workflows to selecting; this file sweeps every workflow.
"""

import glob
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted(glob.glob(str(ROOT / ".github" / "workflows" / "*.yml")))


class WorkflowModelPinTest(unittest.TestCase):
    """The registry alone cannot see workflow drift — so read the workflows.

    Five hardcoded `--model claude-sonnet-4-6` strings (qa-review x2, verify x2,
    medic) and one `--model claude-opus-5` (agent-fix) bypassed selection
    entirely. A hardcoded id in a workflow is drift by construction: it cannot
    be changed by editing the config.
    """

    # Agent name -> the workflow that must resolve its model from the config.
    SELECTORS = {
        "critic": "qa-review.yml",
        "verifier": "verify.yml",
        "medic": "medic.yml",
        "fixer": "agent-fix.yml",
        "planner": "plan.yml",
        "repairer": "red-main-repair.yml",
    }

    def test_no_workflow_hardcodes_a_model_id(self):
        offenders = {}
        for path in WORKFLOWS:
            hits = re.findall(r"--model\s+(claude-[a-z0-9.-]+)", Path(path).read_text())
            if hits:
                offenders[os.path.basename(path)] = sorted(set(hits))
        self.assertEqual(
            offenders, {},
            "workflows must take their model from the selector, "
            f"model_fallback.py select — hardcoded pins found: {offenders}",
        )

    def test_every_agent_workflow_selects_through_model_fallback(self):
        for agent, wf in self.SELECTORS.items():
            with self.subTest(agent=agent):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertRegex(
                    body,
                    r"model_fallback\.py\s+select\s+(?:\\\s*)?" + re.escape(agent),
                    f"{wf} must select the {agent} model through the selector",
                )

    def test_shared_agent_task_selects_by_role(self):
        # engineer/frontend/devops share agent-task.yml and are routed by the
        # label-derived role, so it selects with "$ROLE" rather than a name.
        body = (ROOT / ".github" / "workflows" / "agent-task.yml").read_text()
        self.assertIn('model_fallback.py select "$ROLE"', body)

    # The selected model, or — in a shadow replay only — the model the replay's
    # caller named, handed on by the step that read its effort level
    # (DRE-7193, tests/test_qa_review_shadow.py; DRE-7194,
    # tests/test_verify_shadow.py). Never a literal id either way.
    SELECTED_MODEL = re.compile(
        r"--model \$\{\{ (?:inputs\.shadow && steps\.shadow_model\.outputs\.model \|\| )?"
        r"steps\.model\.outputs\.model \}\}")

    def test_agent_steps_consume_the_selected_model(self):
        # A Select-model step that nothing reads is theatre: the claude_args
        # block must interpolate the step output.
        for wf in ("qa-review.yml", "verify.yml", "medic.yml"):
            with self.subTest(workflow=wf):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertRegex(body, self.SELECTED_MODEL)

    def test_selected_model_is_used_by_every_claude_invocation(self):
        # qa-review and verify each run TWO agent attempts (attempt 1 + retry);
        # both must ride the selected model, not just the first.
        for wf, expected in (("qa-review.yml", 2), ("verify.yml", 2)):
            with self.subTest(workflow=wf):
                body = (ROOT / ".github" / "workflows" / wf).read_text()
                self.assertEqual(
                    len(self.SELECTED_MODEL.findall(body)), expected,
                    f"{wf}: both agent attempts must use the selected model",
                )



if __name__ == "__main__":
    unittest.main()
