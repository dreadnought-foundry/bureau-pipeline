"""The declared footprint, actually parsed (DRE-3040).

`briefs/planner.md` calls the `**Files:**` line "the INPUT to the ordering", and
until this module existed no script read it. Four of DRE-3019's five children
wrote it as `**Files: **` — a stray space inside the bold markers — and nothing
noticed, because nothing was looking.

One section per acceptance criterion, plus the two parsing properties the card
names:

  1. `Files:` is PARSED, and the bold-spacing variants real cards wrote parse
     the same as the template's `**Files:**`.
  2. ROOT-LEVEL FILES ARE FILES. `README.md`, `CHANGELOG.md`, `package.json`,
     `tsconfig.json` — exactly the hot files `standards/engineering.md` warns
     about — were invisible to the old path regex, which required a `/`.
  3. A MISSING SECTION IS A REFUSAL, never a silent empty set. "This card
     declares no files" and "this card forgot to say" are different facts, and
     collapsing them is how a collision check passes with nothing to check
     (standards/console-honesty.md rule 1).
  4. Only the DECLARED section counts. A path named in an acceptance criterion
     is not a footprint — the footprint is what the planner committed to.

The five DRE-3019 children are the fixture, captured verbatim from the board
(`tests/fixtures/dre-3019-children.json`), because the defect this card fixes
was found on those five cards and a synthetic body would not have reproduced it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_footprint.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, SCRIPTS)

import plan_footprint as pf  # noqa: E402


def _children():
    with open(os.path.join(FIXTURES, "dre-3019-children.json"), encoding="utf-8") as f:
        return json.load(f)


class TheFilesLineIsParsed(unittest.TestCase):
    """(1) Every spelling a real card has used reaches the same set."""

    def test_the_template_spelling(self):
        self.assertEqual(
            pf.declared_files("**Files:** `scripts/plan_critic.py`\n"),
            {"scripts/plan_critic.py"},
        )

    def test_the_stray_space_inside_the_bold_markers(self):
        """`**Files: **` — what four of DRE-3019's five children wrote."""
        self.assertEqual(
            pf.declared_files("**Files: **`README.md`\n"), {"README.md"}
        )

    def test_bold_around_the_word_only(self):
        self.assertEqual(pf.declared_files("**Files**: `a/b.py`\n"), {"a/b.py"})

    def test_no_bold_at_all(self):
        self.assertEqual(pf.declared_files("Files: a/b.py\n"), {"a/b.py"})

    def test_a_list_item_declaration(self):
        self.assertEqual(pf.declared_files("- **Files:** `a/b.py`\n"), {"a/b.py"})

    def test_the_declaration_wraps_onto_the_next_line(self):
        """The brief's own template wraps the value under the label."""
        body = (
            "**Files:** `scripts/plan_footprint.py`, `scripts/plan_critic.py`,\n"
            "            `tests/test_plan_footprint.py`\n"
            "\n"
            "## Acceptance criteria\n"
            "- [ ] `scripts/never_named.py` is not in the footprint\n"
        )
        self.assertEqual(
            pf.declared_files(body),
            {
                "scripts/plan_footprint.py",
                "scripts/plan_critic.py",
                "tests/test_plan_footprint.py",
            },
        )

    def test_a_heading_ends_the_section(self):
        body = "**Files:** `a/b.py`\n## Acceptance criteria\n- [ ] `c/d.py` exists\n"
        self.assertEqual(pf.declared_files(body), {"a/b.py"})

    def test_a_second_declaration_ends_the_section(self):
        body = "**Files:** `a/b.py`\n**Blocked by:** DRE-1\n"
        self.assertEqual(pf.declared_files(body), {"a/b.py"})

    def test_the_first_declaration_wins(self):
        """A card declares ONE footprint. A later line cannot widen it."""
        body = "**Files:** `a/b.py`\n\nprose\n\n**Files:** `c/d.py`\n"
        self.assertEqual(pf.declared_files(body), {"a/b.py"})


class RootLevelFilesAreFiles(unittest.TestCase):
    """(2) The old regex required a `/`, so the hot files were invisible."""

    def test_readme_is_a_file(self):
        self.assertEqual(pf.declared_files("**Files:** `README.md`\n"), {"README.md"})

    def test_the_four_hot_files(self):
        body = "**Files:** `README.md`, `CHANGELOG.md`, `package.json`, `tsconfig.json`\n"
        self.assertEqual(
            pf.declared_files(body),
            {"README.md", "CHANGELOG.md", "package.json", "tsconfig.json"},
        )

    def test_a_leading_dot_slash_is_the_same_file(self):
        self.assertEqual(pf.declared_files("**Files:** `./README.md`\n"), {"README.md"})

    def test_prose_abbreviations_are_not_files(self):
        """Admitting root-level names is what makes this a real risk: `e.g.`
        and `i.e.` are dotted tokens with no `/` to disqualify them."""
        body = "**Files:** `README.md` — the front page, e.g. the stamp, i.e. one line.\n"
        self.assertEqual(pf.declared_files(body), {"README.md"})


class AMissingSectionIsARefusal(unittest.TestCase):
    """(3) The acceptance criterion, and the whole point of the module: a
    silent empty set is a collision check with nothing to check."""

    def test_no_files_section_raises(self):
        body = "Do the thing.\n## Acceptance criteria\n- [ ] it is done\n"
        with self.assertRaises(pf.FootprintMissing):
            pf.declared_files(body)

    def test_an_empty_body_raises(self):
        with self.assertRaises(pf.FootprintMissing):
            pf.declared_files("")

    def test_a_declared_but_empty_footprint_is_NOT_a_refusal(self):
        """`**Files:** none — nothing is committed by this card.` is an
        ANSWER. DRE-3032, the DEMO card, wrote exactly that and it is right."""
        self.assertEqual(
            pf.declared_files("**Files:** none — nothing is committed by this card.\n"),
            set(),
        )

    def test_cards_without_footprint_names_the_card(self):
        cards = [
            {"identifier": "DRE-1", "body": "**Files:** `a/b.py`\n"},
            {"identifier": "DRE-2", "body": "no declaration here\n"},
        ]
        self.assertEqual(pf.cards_without_footprint(cards), ["DRE-2"])

    def test_footprints_skips_the_card_that_declared_nothing(self):
        """A card with no section contributes NO files rather than an empty
        set that reads like a checked, clean footprint."""
        cards = [
            {"identifier": "DRE-1", "body": "**Files:** `a/b.py`\n"},
            {"identifier": "DRE-2", "body": "no declaration here\n"},
        ]
        self.assertEqual(pf.footprints(cards), {"DRE-1": {"a/b.py"}})


class OnlyTheDeclaredSectionCounts(unittest.TestCase):
    """(4) The footprint is what the planner COMMITTED to, not every path the
    card happens to mention."""

    def test_a_path_in_an_acceptance_criterion_is_not_a_footprint(self):
        body = (
            "**Files:** `README.md`\n"
            "\n"
            "## Acceptance criteria\n"
            "- [ ] `scripts/exercised.sh` rewrites the stamp\n"
        )
        self.assertEqual(pf.declared_files(body), {"README.md"})

    def test_two_cards_that_only_MENTION_one_file_do_not_collide(self):
        """The mention is a paragraph of its own — a non-blank line directly
        under the label is the declaration WRAPPING, which is what the brief's
        template does, so the blank line is load-bearing here."""
        cards = [
            {"identifier": "DRE-1", "body": "**Files:** `a.md`\n\nsee `shared/x.py`\n"},
            {"identifier": "DRE-2", "body": "**Files:** `b.md`\n\nsee `shared/x.py`\n"},
        ]
        self.assertEqual(pf.collisions(cards), {})

    def test_two_cards_declaring_one_root_file_collide(self):
        cards = [
            {"identifier": "DRE-1", "body": "**Files:** `README.md`\n"},
            {"identifier": "DRE-2", "body": "**Files:** `README.md`, `a/b.py`\n"},
        ]
        self.assertEqual(pf.collisions(cards), {"README.md": ["DRE-1", "DRE-2"]})

    def test_one_card_naming_a_file_twice_is_not_a_collision(self):
        cards = [{"identifier": "DRE-1", "body": "**Files:** `a.md`, `a.md`\n"}]
        self.assertEqual(pf.collisions(cards), {})


class TheDre3019Children(unittest.TestCase):
    """The five cards the defect was found on, verbatim from the board."""

    def setUp(self):
        self.cards = _children()
        self.assertEqual(len(self.cards), 5, "the fixture is the five children")

    def test_every_child_declares_a_footprint(self):
        self.assertEqual(pf.cards_without_footprint(self.cards), [])

    def test_the_footprints_are_read_off_the_bold_spaced_line(self):
        got = {k: sorted(v) for k, v in pf.footprints(self.cards).items()}
        self.assertEqual(got["DRE-3026"], ["README.md"])
        self.assertEqual(got["DRE-3027"], ["CHANGELOG.md"])
        self.assertEqual(
            got["DRE-3028"], ["scripts/exercised.sh", "scripts/exercised.test.ts"]
        )
        self.assertEqual(got["DRE-3032"], [])

    def test_the_root_level_hot_files_are_seen(self):
        """The two files the old regex could not see at all."""
        seen = set().union(*pf.footprints(self.cards).values())
        self.assertIn("README.md", seen)
        self.assertIn("CHANGELOG.md", seen)

    def test_the_proof_card_shares_both_root_files_with_its_siblings(self):
        """DRE-3031 declares `README.md` and `CHANGELOG.md` too. It is
        `blockedBy` both owners so the plan is safe — but the collision is real
        and the check must SAY so rather than be unable to see it."""
        found = pf.collisions(self.cards)
        self.assertEqual(found.get("README.md"), ["DRE-3026", "DRE-3031"])
        self.assertEqual(found.get("CHANGELOG.md"), ["DRE-3027", "DRE-3031"])


class TheCli(unittest.TestCase):
    """The seam a workflow or a planner can call."""

    def _run(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_footprint.py"), *args],
            input=stdin, capture_output=True, text=True,
        )

    def test_footprints_prints_the_declared_set_and_the_missing_cards(self):
        cards = [
            {"identifier": "DRE-1", "body": "**Files:** `README.md`\n"},
            {"identifier": "DRE-2", "body": "nothing declared\n"},
        ]
        out = self._run("footprints", stdin=json.dumps(cards))
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["declared"], {"DRE-1": ["README.md"]})
        self.assertEqual(got["missing"], ["DRE-2"])

    def test_collisions_prints_the_shared_paths(self):
        out = self._run("collisions", stdin=json.dumps(_children()))
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertEqual(got["README.md"], ["DRE-3026", "DRE-3031"])


if __name__ == "__main__":
    unittest.main()
