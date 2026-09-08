"""Todo-entry card-validation gate — the pure validation core (DRE-1405).

Origin (2026-06-13): label-less cards/epics (DRE-1393, DRE-1380–1391, created
with NO labels) got stuck In Progress or sat undispatchable. A card is clean
to enter Todo only if it has BOTH a resolvable repo (a `**Repo:** <slug>` line
OR a `repo:<slug>` label) and an agent-role label (any `agent:*`). These tests
pin the no-I/O core; YAML wiring is pinned in test_validate_card_wiring.py.
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from validate_card import WANT_AGENT, WANT_REPO, missing  # noqa: E402


class MissingTest(unittest.TestCase):
    def test_clean_card_frontmatter_repo_and_agent_label(self):
        self.assertEqual(
            missing("**Repo:** atlas\n\nDo the thing.", ["agent:engineer"]),
            [],
        )

    def test_clean_card_repo_label_instead_of_frontmatter(self):
        # Rule 1 is satisfied by a repo: label even with no **Repo:** line.
        self.assertEqual(
            missing("Do the thing.", ["agent:engineer", "repo:atlas"]),
            [],
        )

    def test_label_only_card_is_valid_canonical_form(self):
        # DRE-1699: the repo:<slug> LABEL is the source of truth — a card with
        # the label and NO **Repo:** stamp anywhere in the body is fully valid.
        self.assertEqual(
            missing("Just plain body, no stamp at all.", ["agent:engineer", "repo:atlas"]),
            [],
        )

    def test_legacy_stamp_only_card_still_accepted(self):
        # DRE-1699: the **Repo:** stamp is a DEPRECATED fallback retained only so
        # legacy cards (created before the label became canonical) still route.
        # A stamp with no repo:<slug> label remains valid.
        self.assertEqual(
            missing("**Repo:** atlas\n\nDo the thing.", ["agent:engineer"]),
            [],
        )

    def test_clean_epic_planner(self):
        # Epics are just agent:planner cards — same rules.
        self.assertEqual(
            missing("**Repo:** atlas\n\nBuild the allergen program.", ["agent:planner"]),
            [],
        )

    def test_missing_agent_label_only(self):
        out = missing("**Repo:** atlas\n\nDo the thing.", [])
        self.assertEqual(len(out), 1)
        self.assertIn("agent:", out[0])

    def test_missing_repo_only(self):
        out = missing("Do the thing, no repo here.", ["agent:engineer"])
        self.assertEqual(len(out), 1)
        self.assertIn("Repo:", out[0])

    def test_missing_both_lists_both(self):
        out = missing("Totally label-less and repo-less.", [])
        self.assertEqual(len(out), 2)

    def test_agent_label_is_prefix_not_enumerated(self):
        # Task says "agent:engineer, agent:planner, ... etc." — any agent:* role
        # counts, including roles we haven't enumerated yet (devops, qa-reviewer).
        for role in ("agent:devops", "agent:qa-reviewer", "agent:security", "agent:anything"):
            self.assertEqual(missing("**Repo:** atlas", [role]), [], role)

    def test_repo_label_must_have_a_slug(self):
        # A bare "repo:" with no slug does not resolve a repo.
        out = missing("Do the thing.", ["agent:engineer", "repo:"])
        self.assertEqual(len(out), 1)
        self.assertIn("Repo:", out[0])

    def test_repo_frontmatter_in_code_fence_does_not_count(self):
        # Mirrors the relay's fenced-code-strip: a **Repo:** line inside a code
        # block is documentation, not real routing frontmatter.
        desc = "Example:\n```\n**Repo:** atlas\n```\nNo real repo line."
        out = missing(desc, ["agent:engineer"])
        self.assertEqual(len(out), 1)
        self.assertIn("Repo:", out[0])

    def test_case_insensitive_labels(self):
        self.assertEqual(missing("**Repo:** atlas", ["Agent:Engineer"]), [])

    # --- DRE-2874: an initiative label is never required ----------------------

    def test_initiative_is_never_required(self):
        # Every caller now asks the same question. A card with repo + role is
        # clean whether or not it carries an `initiative:<x>` label, so nothing
        # reports the label as a gap.
        self.assertEqual(missing("**Repo:** atlas", ["agent:engineer"]), [])
        self.assertEqual(
            missing("**Repo:** atlas", ["agent:engineer", "initiative:bureau"]), []
        )
        self.assertEqual(missing("no repo here", []), [WANT_REPO, WANT_AGENT])

    def test_missing_has_no_require_initiative_switch(self):
        # DRE-2874: the kwarg is GONE, not defaulted off. A caller asking for the
        # old behaviour must fail loudly rather than be silently ignored.
        self.assertNotIn(
            "require_initiative", inspect.signature(missing).parameters
        )
        with self.assertRaises(TypeError):
            missing("**Repo:** atlas", ["agent:engineer"], require_initiative=True)

    def test_no_initiative_vocabulary_survives_in_the_gate(self):
        # The words only ever existed to name that gap. Nothing reports it now,
        # so nothing may still carry the vocabulary for it.
        import validate_card

        self.assertFalse(hasattr(validate_card, "WANT_INITIATIVE"))
        self.assertFalse(hasattr(validate_card, "_has_initiative_label"))


if __name__ == "__main__":
    unittest.main()
