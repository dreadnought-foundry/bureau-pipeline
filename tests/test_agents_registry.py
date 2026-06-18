"""agents.yaml is the consumer contract for the console roster (DRE-1335):
every agent workflow has an entry, and budgets/models match the workflow
text they describe — drift here means the console lies about the fleet."""

import os
import re
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

    def test_models_and_turns_match_workflow_text(self):
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            self.assertIn(a["model"], src,
                          f"{a['name']}: model {a['model']} not in {a['workflow']}")
            self.assertIn(f"--max-turns {a['maxTurns']}", src,
                          f"{a['name']}: maxTurns {a['maxTurns']} not in {a['workflow']}")

    def test_every_agent_has_a_valid_category(self):
        # category groups the roster in the console (build/review/system);
        # purely additive/display — no dispatch impact.
        allowed = {"build", "review", "system"}
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
