"""Todo-entry card-validation gate — the cmd_gate behavior (DRE-1405).

The four cases the gate must get right (linear_ops is stubbed so no network):
  - clean card        → no bounce, bounced=false
  - missing repo      → comment + Backlog, bounced=true
  - missing agent lbl → comment + Backlog, bounced=true
  - already past Todo  → untouched, bounced=false (gate validates the Todo-entry
                         transition only; never drags work backward)
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import validate_card  # noqa: E402


class FakeLinear:
    """Stub of the linear_ops module surface cmd_gate touches."""

    def __init__(self, state, description, labels):
        self._state = state
        self._description = description
        self._labels = labels
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []

    def get_issue(self, identifier):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        return {
            "issue": {
                "description": self._description,
                "labels": {"nodes": [{"name": n} for n in self._labels]},
            }
        }

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state):
        self.states.append((identifier, state))


class GateBehaviorTest(unittest.TestCase):
    def _run(self, fake) -> bool:
        # cmd_gate does `import linear_ops` lazily; inject the fake.
        emitted = {}
        with mock.patch.dict(sys.modules, {"linear_ops": fake}), mock.patch.object(
            validate_card, "_emit", lambda b: emitted.__setitem__("bounced", b)
        ):
            validate_card.cmd_gate("DRE-999")
        return emitted["bounced"]

    def test_clean_card_proceeds(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", ["agent:engineer"])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.comments, [])
        self.assertEqual(fake.states, [])

    def test_missing_repo_bounces(self):
        fake = FakeLinear("Todo", "no repo line", ["agent:engineer"])
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])
        self.assertIn("Repo:", fake.comments[0][1])
        self.assertIn("Returned to Backlog", fake.comments[0][1])

    def test_missing_agent_label_bounces(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", [])
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])
        self.assertIn("agent:", fake.comments[0][1])

    def test_already_in_progress_untouched(self):
        # A card already past Todo is NEVER bounced, even if malformed.
        fake = FakeLinear("In Progress", "no repo, no labels", [])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.comments, [])
        self.assertEqual(fake.states, [])

    def test_in_qa_untouched(self):
        fake = FakeLinear("In QA", "", [])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.states, [])

    def test_planner_triage_clean_proceeds(self):
        # agent:planner epics enter via Triage (the planner-triage path).
        fake = FakeLinear("Triage", "**Repo:** atlas", ["agent:planner"])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.states, [])

    def test_triage_malformed_bounces(self):
        fake = FakeLinear("Triage", "no repo", ["agent:planner"])
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])


if __name__ == "__main__":
    unittest.main()
