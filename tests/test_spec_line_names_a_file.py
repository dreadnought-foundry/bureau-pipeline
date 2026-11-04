"""A card's `**Spec:**` line names a file, `docs/specs/<slug>.md` (DRE-9067).

OpenSpec was parked on 2026-11-02 (Product Playbook 1.5): a spec is now one
Markdown file in the product repo. The pipeline's own text described only the
older `openspec/changes/<id>/` directory, so a card reading
`**Spec:** docs/specs/<slug>.md` never tripped the engineer's "Spec first"
rule, and the planner was not told to read a spec at all.

Each assertion is content-shaped on purpose: the standard and the two briefs
are read by agents, and the critic's card context is the one place the review
names the line. A rule nothing reads back is a rule that gets edited away.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_card_context as rcc  # noqa: E402

STANDARD = ROOT / "standards" / "card-quality.md"
ENGINEER = ROOT / "briefs" / "engineer.md"
PLANNER = ROOT / "briefs" / "planner.md"

FILE_LINE = "**Spec:** docs/specs/<slug>.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the documents
    hard-wrap at 80 columns."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _bullet(text: str, opener: str) -> str:
    """The list item opening with `opener`, up to the next top-level bullet
    or heading."""
    at = text.find(opener)
    assert at >= 0, f"no list item opens with {opener!r}"
    rest = text[at:]
    end = re.compile(r"^(- |#)", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


class CardQualityStandardTest(unittest.TestCase):
    def setUp(self):
        self.item = _bullet(_read(STANDARD), "- **`**Spec:**")

    def test_the_example_is_a_spec_file(self):
        self.assertIn(f"`{FILE_LINE}`", self.item)

    def test_a_directory_line_is_still_read_for_older_cards(self):
        self.assertIn("openspec/changes/<id>/", self.item)
        self.assertRegex(self.item, _loose("before 2026-11-02"))

    def test_openspec_is_named_only_in_the_older_card_note(self):
        hits = [line for line in _read(STANDARD).splitlines() if "openspec" in line.lower()]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn(hits[0], self.item)


class EngineerBriefTest(unittest.TestCase):
    def setUp(self):
        self.rule = _bullet(_read(ENGINEER), "- **Spec first**")

    def test_the_rule_fires_on_any_spec_line(self):
        self.assertRegex(self.rule, _loose("any `**Spec:**` line"))
        self.assertIn(FILE_LINE, self.rule)

    def test_a_file_is_read_as_the_spec(self):
        self.assertRegex(self.rule, _loose("names a file, read that file"))

    def test_a_directory_is_read_as_before(self):
        self.assertRegex(self.rule, _loose("names a directory"))
        self.assertIn("specs/*/spec.md", self.rule)
        self.assertIn("design.md", self.rule)

    def test_conformance_and_divergence_still_hold(self):
        self.assertRegex(self.rule, _loose("conform to its declared interfaces and names"))
        self.assertRegex(self.rule, _loose("justification in the PR description"))


class PlannerBriefTest(unittest.TestCase):
    def setUp(self):
        self.brief = _read(PLANNER)
        self.rule = _bullet(self.brief, "- **Spec refs**")

    def test_the_planner_reads_the_spec_before_writing_cards(self):
        self.assertRegex(self.rule, _loose("read the spec it names"))
        self.assertRegex(self.rule, _loose("before you write"))

    def test_the_planner_copies_the_line_onto_every_card(self):
        self.assertRegex(self.rule, _loose("same `**Spec:**` line onto every card"))

    def test_the_sub_issue_template_carries_the_line(self):
        at = self.brief.find("## Sub-issue description template")
        template = self.brief[at: self.brief.find("```", self.brief.find("```", at) + 3)]
        self.assertIn("**Spec:**", template)


class CriticCardContextTest(unittest.TestCase):
    """The critic is told the spec may be a file or a directory, never a
    directory only."""

    PHRASE = "the spec the card's **Spec:** line names, a file or a directory"

    def test_card_mode_names_a_file_or_a_directory(self):
        ctx = rcc.build_context("DRE-123", "agent/DRE-123-widget", "")
        self.assertIn(self.PHRASE, ctx)
        self.assertNotIn("**Spec:** directory", ctx)

    def test_no_card_mode_names_a_file_or_a_directory(self):
        ctx = rcc.build_context("", "feature/widget", "")
        self.assertIn("no spec a **Spec:** line names, a file or a directory", ctx)
        self.assertNotIn("**Spec:** directory", ctx)

    def test_the_script_never_calls_the_spec_a_directory_only(self):
        source = _read(ROOT / "scripts" / "review_card_context.py")
        self.assertEqual(source.count("**Spec:** directory"), 0)


if __name__ == "__main__":
    unittest.main()
