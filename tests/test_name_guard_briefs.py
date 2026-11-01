"""The two writers' briefs each state the no-names rule in one line and point at the guard (DRE-8172).

The proof run writes the record, its pull request body and its comments, and
the critic writes its review comment. Both go through one seam,
`scripts/name_redact.py` (DRE-8166), which redacts a person's name, email or
sign-in id to a role and whose guard refuses what still carries one. A run
reads its brief, not that module, so each brief states the rule in one
paragraph and names the module — and never restates the roster or its forms,
which live in `scripts/name_roster.py` (DRE-8165).

These tests hold the shared phrase, the module path and the two roles to the
constants that define them, and hold the rule to one paragraph per brief.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import name_roster  # noqa: E402

PROOF = ROOT / "briefs" / "proof.md"
CRITIC = ROOT / "briefs" / "critic.md"

#: The phrase both briefs and this test share, word for word (the card's contract).
PHRASE = "no person's name, email or sign-in id"
#: The seam and guard, as DRE-8166 names it.
MODULE = "scripts/name_redact.py"


def paragraphs(text: str) -> list[str]:
    """Each blank-line-separated block, its whitespace collapsed."""
    return [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", text)]


def section(text: str, heading: str) -> str:
    """The body under `heading`, up to the next `## ` heading."""
    start = text.index(heading + "\n") + len(heading) + 1
    nxt = text.find("\n## ", start)
    return text[start:] if nxt == -1 else text[start:nxt]


def rule(text: str) -> list[str]:
    """Every paragraph that carries the phrase."""
    return [p for p in paragraphs(text) if PHRASE in p]


class ProofBriefRule(unittest.TestCase):
    def setUp(self) -> None:
        self.text = PROOF.read_text()
        self.record = section(self.text, "## The record")

    def test_phrase_is_on_one_physical_line(self):
        # A grep for the phrase finds it: it is never wrapped across a line.
        self.assertIn(PHRASE, self.text)

    def test_rule_is_one_paragraph_under_the_record(self):
        self.assertEqual(len(rule(self.text)), 1, "the rule is stated once in the brief")
        self.assertEqual(len(rule(self.record)), 1, "the rule sits under `## The record`")

    def test_rule_names_what_it_covers(self):
        (line,) = rule(self.record)
        for surface in ("record", "pull request body", "comment"):
            self.assertIn(surface, line)

    def test_rule_names_both_roles(self):
        (line,) = rule(self.record)
        self.assertIn(f"`{name_roster.ROLE_CEO}` for the CEO", line)
        self.assertIn(f"`{name_roster.ROLE_PERSON}` for anyone else", line)

    def test_rule_names_the_guard_that_refuses_the_push(self):
        (line,) = rule(self.record)
        self.assertIn(f"`{MODULE}`", line)
        self.assertIn("pre-push guard refuses a push", line)
        self.assertIn("never the name", line)

    def test_rule_does_not_restate_the_roster(self):
        (line,) = rule(self.record)
        self.assertNotIn("name_roster", line)


class CriticBriefRule(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CRITIC.read_text()

    def test_phrase_is_on_one_physical_line(self):
        self.assertIn(PHRASE, self.text)

    def test_rule_is_one_paragraph(self):
        self.assertEqual(len(rule(self.text)), 1)

    def test_rule_covers_the_comment_and_its_commands(self):
        (line,) = rule(self.text)
        self.assertIn("comment", line)
        self.assertIn("login", line)
        self.assertIn("inside a command you ran", line)

    def test_rule_names_the_seam_that_holds_it(self):
        (line,) = rule(self.text)
        self.assertIn(f"`{MODULE}`", line)
        self.assertIn("held at its guard", line)

    def test_rule_does_not_restate_the_roster(self):
        (line,) = rule(self.text)
        self.assertNotIn("name_roster", line)


class SharedContract(unittest.TestCase):
    def test_module_and_roles_exist_as_named(self):
        self.assertTrue((ROOT / MODULE).is_file())
        self.assertEqual(name_roster.ROLE_CEO, "[the CEO]")
        self.assertEqual(name_roster.ROLE_PERSON, "[a person]")

    def test_proof_reach_check_still_passes(self):
        # Every `brief` sentence in config/proof-reach.json still appears in briefs/proof.md.
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_reach.py"), "check"],
                             cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
