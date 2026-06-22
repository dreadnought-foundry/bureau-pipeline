"""Todo-entry card-validation gate — FIX-FIRST behavior (DRE-1405 extension).

The gate no longer just bounces a malformed card: it REPAIRS it in place and
lets it proceed, bouncing ONLY when the repo cannot be deterministically
inferred (the one case where a fix would be a wrong-repo guess).

Two layers are pinned here:
  - the pure inference core (`infer_agent_label`, `infer_repo`) — no I/O; and
  - `cmd_gate`'s fix-first decision + the exact Linear mutations it performs
    (add_label, set_description, comment) with linear_ops stubbed.

Inference rules (mirrors the relay's REPO_MAP convention — see validate_card):
  agent label: title has [EPIC] OR card has children → agent:planner; else
               agent:engineer.
  repo:        initiative:<x> label (2a) wins over project-name prefix (2b);
               candidate slug = identity except the documented alias
               bureau→agent-bureau; the candidate must be a real repo
               (VALID_SLUGS) or the card is bounced (never a wrong-repo guess).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import validate_card  # noqa: E402


# --- Pure inference core (no I/O) -------------------------------------------


class InferAgentLabelTest(unittest.TestCase):
    def test_epic_title_infers_planner(self):
        self.assertEqual(
            validate_card.infer_agent_label("[EPIC] Allergen program", False, []),
            "agent:planner",
        )

    def test_card_with_children_infers_planner(self):
        # An epic in practice — it has sub-issues even without the [EPIC] tag.
        self.assertEqual(
            validate_card.infer_agent_label("Allergen program", True, []),
            "agent:planner",
        )

    def test_normal_card_infers_engineer(self):
        self.assertEqual(
            validate_card.infer_agent_label("Fix the thing", False, []),
            "agent:engineer",
        )

    def test_epic_tag_case_insensitive(self):
        self.assertEqual(
            validate_card.infer_agent_label("[epic] thing", False, []),
            "agent:planner",
        )


class InferRepoTest(unittest.TestCase):
    def test_initiative_label_bureau_maps_to_agent_bureau(self):
        # The label slug `bureau` is NOT the repo slug — it aliases to agent-bureau.
        slug, source = validate_card.infer_repo(["initiative:bureau"], None)
        self.assertEqual(slug, "agent-bureau")
        self.assertIn("initiative", source)

    def test_initiative_label_atlas_maps_to_atlas(self):
        slug, _ = validate_card.infer_repo(["initiative:atlas"], None)
        self.assertEqual(slug, "atlas")

    def test_project_name_prefix_bureau_console(self):
        slug, source = validate_card.infer_repo([], "Bureau: Console")
        self.assertEqual(slug, "agent-bureau")
        self.assertIn("project", source)

    def test_project_name_prefix_atlas(self):
        slug, _ = validate_card.infer_repo([], "Atlas: Allergen Pivot")
        self.assertEqual(slug, "atlas")

    def test_project_name_prefix_deltasolv(self):
        slug, _ = validate_card.infer_repo([], "DeltaSolv: Phase 6 — QA, Demo & Launch")
        self.assertEqual(slug, "deltasolv")

    def test_initiative_wins_over_project(self):
        # 2a (initiative) takes precedence over 2b (project).
        slug, source = validate_card.infer_repo(["initiative:atlas"], "Bureau: Console")
        self.assertEqual(slug, "atlas")
        self.assertIn("initiative", source)

    def test_unknown_project_prefix_returns_none(self):
        # Dev Sandbox / Foundry / unknown — no recognized product repo.
        self.assertEqual(validate_card.infer_repo([], "Dev Sandbox"), (None, None))
        self.assertEqual(validate_card.infer_repo([], "Foundry: WS3 Playbook"), (None, None))
        self.assertEqual(validate_card.infer_repo([], "Some Random Project"), (None, None))

    def test_no_initiative_no_project_returns_none(self):
        self.assertEqual(validate_card.infer_repo([], None), (None, None))

    def test_initiative_foundry_resolves_to_unmapped_slug(self):
        # Rule 5 reachability: an initiative whose slug is a REAL candidate but
        # NOT a real repo (Dreadnought Foundry spans no single product repo).
        # infer_repo returns the concrete slug; the gate must reject it.
        slug, _ = validate_card.infer_repo(["initiative:foundry"], None)
        self.assertEqual(slug, "foundry")
        self.assertNotIn(slug, validate_card.VALID_SLUGS)

    def test_valid_slugs_includes_agent_bureau(self):
        self.assertIn("agent-bureau", validate_card.VALID_SLUGS)
        self.assertEqual(
            validate_card.VALID_SLUGS,
            {"atlas", "deltasolv", "vericorr", "agent-bureau", "agent-bureau-demo"},
        )


# --- cmd_gate fix-first behavior (linear_ops stubbed) -----------------------


class FakeLinear:
    """Stub of the linear_ops surface the fix-first cmd_gate touches.

    Captures every mutation so tests assert the exact repair performed.
    """

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
        self.descriptions: list[tuple[str, str]] = []  # (identifier, new body)

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


class GateFixFirstTest(unittest.TestCase):
    def _run(self, fake) -> bool:
        emitted = {}
        with mock.patch.dict(sys.modules, {"linear_ops": fake}), mock.patch.object(
            validate_card, "_emit", lambda b: emitted.__setitem__("bounced", b)
        ):
            validate_card.cmd_gate("DRE-999")
        return emitted["bounced"]

    # --- epic / normal agent-label inference ---

    def test_epic_missing_agent_label_gets_planner_and_proceeds(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", [], title="[EPIC] Big thing")
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "agent:planner"), fake.added_labels)
        self.assertEqual(fake.states, [])  # not bounced
        self.assertTrue(any("Auto-fixed" in c[1] for c in fake.comments))

    def test_normal_card_missing_agent_label_gets_engineer_and_proceeds(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", [], title="Fix a bug")
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "agent:engineer"), fake.added_labels)
        self.assertEqual(fake.states, [])

    # --- repo inference ---

    def test_missing_repo_initiative_bureau_infers_agent_bureau(self):
        fake = FakeLinear("Todo", "Do the thing.", ["agent:engineer", "initiative:bureau"])
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "repo:agent-bureau"), fake.added_labels)
        # DRE-1699: only the repo:<slug> LABEL is written — the deprecated
        # **Repo:** stamp is no longer prepended to the description.
        self.assertEqual(fake.descriptions, [])
        self.assertEqual(fake.states, [])

    def test_missing_repo_project_console_infers_agent_bureau(self):
        fake = FakeLinear(
            "Todo", "Do the thing.", ["agent:engineer"], project="Bureau: Console"
        )
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "repo:agent-bureau"), fake.added_labels)
        # DRE-1699: label only, no stamp written to the description.
        self.assertEqual(fake.descriptions, [])
        self.assertEqual(fake.states, [])

    def test_missing_repo_no_initiative_unknown_project_bounces(self):
        fake = FakeLinear(
            "Todo", "Do the thing.", ["agent:engineer"], project="Dev Sandbox"
        )
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])
        # No repo: label / description written when we can't infer.
        self.assertEqual(fake.descriptions, [])
        self.assertFalse(any(l[1].startswith("repo:") for l in fake.added_labels))

    def test_missing_repo_no_project_bounces(self):
        fake = FakeLinear("Todo", "Do the thing.", ["agent:engineer"], project=None)
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])

    def test_inference_yields_unmapped_slug_bounces(self):
        # initiative:foundry → "foundry" → real candidate, NOT a real repo.
        # Must bounce, never guess a repo that isn't in VALID_SLUGS.
        fake = FakeLinear(
            "Todo", "Do the thing.", ["agent:engineer", "initiative:foundry"]
        )
        self.assertTrue(self._run(fake))
        self.assertEqual(fake.states, [("DRE-999", "Backlog")])
        self.assertEqual(fake.descriptions, [])

    # --- no-op paths ---

    def test_clean_card_untouched_no_autofix_comment(self):
        fake = FakeLinear("Todo", "**Repo:** atlas", ["agent:engineer"])
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.added_labels, [])
        self.assertEqual(fake.descriptions, [])
        self.assertEqual(fake.comments, [])  # no 🔧 comment on an already-clean card
        self.assertEqual(fake.states, [])

    def test_already_in_progress_untouched(self):
        fake = FakeLinear("In Progress", "no repo, no labels", [], title="[EPIC] x")
        self.assertFalse(self._run(fake))
        self.assertEqual(fake.added_labels, [])
        self.assertEqual(fake.descriptions, [])
        self.assertEqual(fake.comments, [])
        self.assertEqual(fake.states, [])

    # --- combined repair: missing BOTH label and repo, both inferable ---

    def test_missing_both_repaired_and_proceeds(self):
        fake = FakeLinear(
            "Todo", "Build the allergen program.", ["initiative:atlas"],
            title="[EPIC] Allergen",
        )
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "agent:planner"), fake.added_labels)
        self.assertIn(("DRE-999", "repo:atlas"), fake.added_labels)
        # DRE-1699: repo label only — no **Repo:** stamp written.
        self.assertEqual(fake.descriptions, [])
        self.assertEqual(fake.states, [])
        # Single auto-fix comment naming what was added + the inference source.
        fixes = [c for c in fake.comments if "Auto-fixed" in c[1]]
        self.assertEqual(len(fixes), 1)
        self.assertIn("agent:planner", fixes[0][1])
        self.assertIn("repo:atlas", fixes[0][1])

    def test_missing_repo_but_no_agent_label_both_handled(self):
        # repo inferable from project, agent label inferred from normal title.
        fake = FakeLinear(
            "Todo", "Do it.", [], title="Plain card", project="VeriCorr: Forms"
        )
        self.assertFalse(self._run(fake))
        self.assertIn(("DRE-999", "agent:engineer"), fake.added_labels)
        self.assertIn(("DRE-999", "repo:vericorr"), fake.added_labels)
        self.assertEqual(fake.states, [])


if __name__ == "__main__":
    unittest.main()
