"""The proof-reach check is wired where the seam is, and the standard quotes
its two sentences (DRE-6606).

`scripts/plan_proof_reach.py findings` runs in every `plan.yml` step that runs
`scripts/plan_seam.py findings`, straight after it, appending to the same
structural file — so `plan_seam_gate.py gate` forwards both into the critic's
result and the round is a `SEND_BACK` whatever the model wrote. Its
`operator-cards --out` read runs first, in the same step, into the file the
check reads. The review route's mechanical step posts a note and writes no
structural file, so the check appends to the note there and nothing else.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_proof_reach_wiring.py -v
"""

from __future__ import annotations

import os
import re
import shlex
import sys
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(os.path.join(ROOT, "scripts")))

import plan_proof_reach as ppr  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
OPERATOR_CARDS = "${{ runner.temp }}/plan-operator-cards.json"
REVIEW_MECHANICAL = "Mechanical findings — the revised plan (review)"

SEAM = "plan_seam.py findings"
REACH = "plan_proof_reach.py findings"
OPERATORS = "plan_proof_reach.py operator-cards"


def steps() -> list[dict]:
    with open(WORKFLOW, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    return [s for job in (doc.get("jobs") or {}).values()
            for s in (job or {}).get("steps") or [] if isinstance(s, dict)]


def commands(run: str) -> list[str]:
    """The step's script as one logical line per command."""
    return [line.strip() for line in re.sub(r"\\\n\s*", " ", run).splitlines()
            if line.strip()]


def index_of(lines: list[str], needle: str) -> int:
    hits = [i for i, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} lines run {needle!r}: {lines}")
    return hits[0]


def flag(line: str, name: str):
    words = shlex.split(line)
    return words[words.index(name) + 1] if name in words else None


class TheSeamSitesRunTheCheck(unittest.TestCase):

    def seam_steps(self) -> list[dict]:
        found = [s for s in steps() if SEAM in str(s.get("run") or "")]
        self.assertGreaterEqual(len(found), 2, "the seam runs before both pre-stage rounds")
        return found

    def test_every_seam_step_runs_the_check_straight_after_it(self):
        for step in self.seam_steps():
            name = step.get("name")
            lines = commands(step["run"])
            seam, reach = index_of(lines, SEAM), index_of(lines, REACH)
            self.assertLess(seam, reach, name)
            between = [l for l in lines[seam + 1:reach] if "python3" in l]
            self.assertEqual([], [l for l in between if OPERATORS not in l],
                             f"{name}: only the operator-cards read sits between "
                             "the seam and the check")

    def test_the_same_structural_detail_note_and_children(self):
        for step in self.seam_steps():
            name = step.get("name")
            lines = commands(step["run"])
            seam, reach = lines[index_of(lines, SEAM)], lines[index_of(lines, REACH)]
            structural = flag(seam, "--structural-file")
            self.assertTrue(structural, name)
            self.assertEqual(structural, flag(reach, "--structural-file"), name)
            self.assertEqual(flag(seam, "--detail-file"), flag(reach, "--detail-file"), name)
            self.assertEqual(flag(seam, "--note-file"), flag(reach, "--note-file"), name)
            self.assertEqual(seam.split("<")[-1].strip(), reach.split("<")[-1].strip(),
                             f"{name}: the check reads the seam's children-json")

    def test_operator_cards_runs_first_into_the_file_the_check_reads(self):
        for step in self.seam_steps():
            name = step.get("name")
            lines = commands(step["run"])
            ops, reach = index_of(lines, OPERATORS), index_of(lines, REACH)
            self.assertLess(ops, reach, name)
            self.assertEqual(OPERATOR_CARDS, flag(lines[ops], "--out"), name)
            self.assertEqual(OPERATOR_CARDS,
                             flag(lines[reach], "--operator-cards-file"), name)

    def test_the_check_is_told_the_caller(self):
        for step in self.seam_steps():
            lines = commands(step["run"])
            self.assertTrue(flag(lines[index_of(lines, REACH)], "--caller"),
                            step.get("name"))
            self.assertIn("REPO_SLUG", step.get("env") or {}, step.get("name"))


class TheReviewSiteGetsTheNoteOnly(unittest.TestCase):

    def step(self) -> dict:
        hits = [s for s in steps() if s.get("name") == REVIEW_MECHANICAL]
        self.assertEqual(1, len(hits))
        return hits[0]

    def test_it_appends_to_the_note_and_writes_no_structural_file(self):
        step = self.step()
        lines = commands(step["run"])
        reach = lines[index_of(lines, REACH)]
        self.assertEqual("$NOTE", flag(reach, "--note-file"))
        self.assertIsNone(flag(reach, "--structural-file"))
        self.assertLess(index_of(lines, "plan_critic.py mechanical"), index_of(lines, REACH))
        self.assertLess(index_of(lines, OPERATORS), index_of(lines, REACH))
        self.assertLess(index_of(lines, REACH), index_of(lines, "linear_ops.py comment"))


class TheStandardQuotesTheSentences(unittest.TestCase):

    def test_both_sentences_are_quoted_as_the_module_writes_them(self):
        with open(STANDARD, encoding="utf-8") as fh:
            text = fh.read()
        for sentence in (ppr.FINDING_REACH, ppr.FINDING_OPERATOR):
            self.assertIn(sentence, text)

    def test_it_sits_beside_the_seam_paragraph(self):
        with open(STANDARD, encoding="utf-8") as fh:
            text = fh.read()
        seam = text.index("scripts/plan_seam_gate.py")
        here = text.index(ppr.FINDING_REACH)
        self.assertLess(abs(here - seam), 4000)
        self.assertIn("scripts/plan_proof_reach.py", text)


if __name__ == "__main__":
    unittest.main()
