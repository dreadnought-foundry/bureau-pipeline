"""agents.yaml is the consumer contract for the console roster (DRE-1335):
every agent workflow has an entry, and budgets match the workflow text they
describe — drift here means the console lies about the fleet.

The MODEL half of that contract moved to config/models.yaml (DRE-2316): the
registry's `model:` is a generated mirror of it, and the workflow no longer
carries the id at all, so the check here is that the workflow SELECTS from the
config rather than pinning a string."""

import os
import re
import sys
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pr_size_strategy as pss  # noqa: E402

# `--max-turns 80`, or the size-step output qa-review.yml selects (DRE-2466).
_TURNS_RE = re.compile(
    r"--max-turns\s+(?:(\d+)|\$\{\{\s*steps\.size\.outputs\.(\w+)\s*\}\})"
)
_SIZE_OUTPUTS = ("max_turns", "retry_max_turns")


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
        """The roster's budget must be one the workflow actually runs with.

        DRE-2466: qa-review.yml no longer carries its ceiling as a literal —
        it sizes the diff first and interpolates the budget the strategy
        selected. An expression is RESOLVED here through the same table the
        workflow reads (standard strategy, the path a review normally takes),
        so the console still cannot drift from the workflow; it just can no
        longer be checked by grepping for a number."""
        for a in load():
            src = open(os.path.join(ROOT, a["workflow"])).read()
            declared = [
                int(literal) if literal else pss.turn_budget("standard")[
                    _SIZE_OUTPUTS.index(name)
                ]
                for literal, name in _TURNS_RE.findall(src)
            ]
            self.assertIn(a["maxTurns"], declared,
                          f"{a['name']}: maxTurns {a['maxTurns']} is not a "
                          f"ceiling {a['workflow']} runs with ({declared})")

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
