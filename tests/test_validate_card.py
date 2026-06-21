"""Todo-entry card-validation gate — the pure validation core (DRE-1405).

Origin (2026-06-13): label-less cards/epics (DRE-1393, DRE-1380–1391, created
with NO labels) got stuck In Progress or sat undispatchable. A card is clean
to enter Todo only if it has BOTH a resolvable repo (a `**Repo:** <slug>` line
OR a `repo:<slug>` label) and an agent-role label (any `agent:*`). These tests
pin the no-I/O core; YAML wiring is pinned in test_validate_card_wiring.py.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from validate_card import missing  # noqa: E402


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

    # --- DRE-1722: initiative is OPT-IN (default off keeps the Todo gate same) -

    def test_initiative_not_required_by_default(self):
        # The Todo-entry gate calls missing() with the default; a clean card with
        # repo + role but NO initiative is still clean — unchanged behavior.
        self.assertEqual(missing("**Repo:** atlas", ["agent:engineer"]), [])

    def test_require_initiative_flags_when_absent(self):
        out = missing("**Repo:** atlas", ["agent:engineer"], require_initiative=True)
        self.assertEqual(len(out), 1)
        self.assertIn("initiative:", out[0])

    def test_require_initiative_clean_when_present(self):
        self.assertEqual(
            missing(
                "**Repo:** atlas",
                ["agent:engineer", "initiative:bureau"],
                require_initiative=True,
            ),
            [],
        )

    def test_require_initiative_bare_label_does_not_count(self):
        # A bare "initiative:" with no slug does not satisfy the requirement.
        out = missing("**Repo:** atlas", ["agent:engineer", "initiative:"], require_initiative=True)
        self.assertEqual(len(out), 1)
        self.assertIn("initiative:", out[0])

    def test_require_initiative_lists_all_three_gaps(self):
        out = missing("no repo here", [], require_initiative=True)
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
