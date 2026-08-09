"""agents.yaml is the consumer contract for the console roster (DRE-1335):
every agent workflow has an entry, and budgets match the workflow text they
describe — drift here means the console lies about the fleet.

The MODEL half of that contract moved to config/models.yaml (DRE-2316): the
registry's `model:` is a generated mirror of it, and the workflow no longer
carries the id at all, so the check here is that the workflow SELECTS from the
config rather than pinning a string."""

import os
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load():
    with open(os.path.join(ROOT, "agents.yaml")) as f:
        return yaml.safe_load(f)["agents"]


class AgentsRegistryTest(unittest.TestCase):
    def test_every_agent_workflow_has_an_entry(self):
        covered = {a["workflow"].split("/")[-1] for a in load()}
        agent_workflows = {"agent-task.yml", "agent-fix.yml", "qa-review.yml",
                           "plan.yml", "medic.yml"}
        self.assertEqual(agent_workflows, covered & agent_workflows)

    def test_turns_match_workflow_text(self):
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            self.assertIn(f"--max-turns {a['maxTurns']}", src,
                          f"{a['name']}: maxTurns {a['maxTurns']} not in {a['workflow']}")

    def test_workflows_resolve_the_model_through_the_config(self):
        """The roster's `model:` is a GENERATED mirror of config/models.yaml
        (DRE-2316) — so it is no longer a literal to grep for in the workflow.

        The old assertion (`model` appears verbatim in the workflow source) is
        exactly what a hardcoded `--model claude-sonnet-4-6` satisfied, which is
        how the critic/verifier/medic bypassed selection while this test stayed
        green. The contract now: the workflow SELECTS, and pins nothing."""
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            self.assertIn("model_fallback.py select", src,
                          f"{a['name']}: {a['workflow']} does not select a model")
            self.assertNotRegex(
                src, r"--model\s+claude-",
                f"{a['name']}: {a['workflow']} hardcodes a model id")

    def test_every_agent_has_a_valid_category(self):
        # category groups the roster in the console by business function
        # (product/development/operations/marketing/sales);
        # purely additive/display — no dispatch impact.
        allowed = {"product", "development", "operations", "marketing", "sales"}
        for a in load():
            self.assertIn("category", a, f"{a['name']}: missing category")
            self.assertIn(a["category"], allowed,
                          f"{a['name']}: category {a['category']!r} not in {allowed}")

    def test_brief_paths_exist_when_set(self):
        for a in load():
            if a.get("briefPath"):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, a["briefPath"])),
                                f"{a['name']}: missing {a['briefPath']}")


if __name__ == "__main__":
    unittest.main()
