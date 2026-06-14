"""Todo-entry card-validation gate — the cmd_gate behavior (DRE-1405).

The cases the gate must get right (linear_ops is stubbed so no network). Since
the fix-first extension the gate REPAIRS what it can rather than bouncing — the
fine-grained repair/proceed/bounce cases live in test_validate_card_autofix.py.
These pin the surviving invariants:
  - clean card        → no bounce, bounced=false, untouched
  - missing repo, NOT inferable → comment + Backlog, bounced=true
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

    def __init__(self, state, description, labels, title="A card",
                 children=0, project=None):
        self._state = state
        self._description = description
        self._labels = list(labels)
        self._title = title
        self._children = children
        self._project = project
        self.comments: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added_labels: list[tuple[str, str]] = []
        self.descriptions: list[tuple[str, str]] = []

    def get_issue(self, identifier):
        return {"id": "x", "identifier": identifier, "state": {"name": self._state}}

    def gql(self, query, variables=None):
        return {
            "issue": {
                "title": self._title,
                "description": self._description,
                "labels": {"nodes": [{"name": n} for n in self._labels]},
                "children": {"nodes": [{"id": i} for i in range(self._children)]},
                "project": ({"name": self._project} if self._project else None),
            }
        }

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def cmd_state(self, identifier, state):
        self.states.append((identifier, state))

    def add_label(self, identifier, label):
        self.added_labels.append((identifier, label))
        self._labels.append(label)

    def set_description(self, identifier, body):
        self.descriptions.append((identifier, body))
        self._description = body


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

    def test_missing_repo_not_inferable_bounces(self):
        # No **Repo:** line, no repo:/initiative: label, no project → the one
        # case the fix-first gate still bounces (can't guess a repo).
        fake = FakeLinear("Todo", "no repo line", ["agent:engineer"], project=None)
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])
        self.assertIn("Repo:", fake.comments[0][1])
        self.assertIn("Returned to Backlog", fake.comments[0][1])

    def test_missing_agent_label_is_auto_fixed_not_bounced(self):
        # Behavior change (fix-first): a card with a resolvable repo but no agent
        # label is REPAIRED (engineer inferred) and proceeds — no longer bounced.
        fake = FakeLinear("Todo", "**Repo:** atlas", [], title="Fix it")
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "agent:engineer"), fake.added_labels)
        self.assertEqual(fake.states, [])

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

    def test_triage_uninferable_repo_bounces(self):
        # Triage planner card, no repo and nothing to infer one from → bounce.
        fake = FakeLinear("Triage", "no repo", ["agent:planner"], project=None)
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])


if __name__ == "__main__":
    unittest.main()
