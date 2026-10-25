"""A plan whose child needs the CEO, and whose up-front question does not name
that child, is sent back at the first critic (DRE-7523).

DRE-7521 put the rule in `standards/card-quality.md`: every step that needs the
CEO is listed in the one question he answers before the work starts. Portico's
portals release stood stuck for more than ten hours (DRE-7494) on a hand step
the proof session (DRE-7235) found two laps in. DRE-7752 checked one such step,
a held repo's hold; this check reads the rest off the plan's own children.

A child needs the CEO when it is open and is an operator card
(`plan_proof_reach.is_operator_card`), or carries an acceptance criterion on a
person's row (`plan_proof_reach.PERSON_ROWS`). It is named when a paragraph
opening `Step that needs the CEO:` (`plan_held_repo.paragraphs`) names its
identifier as a whole token.

Run: python3 -m pytest tests/test_plan_ceo_steps.py -v
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess  # nosec B404 — runs this repo's own script
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan_ceo_steps  # noqa: E402
import plan_held_repo  # noqa: E402

SCRIPT = SCRIPTS / "plan_ceo_steps.py"
WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"

HELD = "plan_held_repo.py findings"
CEO = "plan_ceo_steps.py findings"

OPERATOR_ID = "DRE-9101"
OPERATOR_TITLE = "OPERATOR: portico: republish the portals document as a moderator"
PROOF_ID = "DRE-9102"
PROOF_TITLE = "PROOF: portico: the portals release is live"
FLEET_ID = "DRE-9103"
FLEET_TITLE = "portico: render the portals list from the new endpoint"

PRESS = ("needs the CEO's press: he republishes the portals document from his "
         "own console session")


def _body(*criteria: str) -> str:
    rows = "\n".join(f"- [ ] {c}" for c in criteria)
    return f"## Problem\n\nSomething.\n\n## Acceptance\n\n{rows}\n"


OPERATOR = {
    "identifier": OPERATOR_ID,
    "title": OPERATOR_TITLE,
    "body": _body("The document is republished."),
    "labels": ["operator-step", "no-code", "repo:portico"],
    "state": "Queued",
}
PROOF = {
    "identifier": PROOF_ID,
    "title": PROOF_TITLE,
    "body": _body("The record quotes the release line.", PRESS,
                  "The CEO closes this card after reading the record."),
    "labels": ["agent:engineer", "repo:portico"],
    "state": "Queued",
}
FLEET = {
    "identifier": FLEET_ID,
    "title": FLEET_TITLE,
    "body": _body("The list renders from the new endpoint."),
    "labels": ["agent:engineer", "repo:portico"],
    "state": "Queued",
}


def plan(*named: str) -> str:
    """A plan artifact whose up-front question names `named`."""
    text = ("# Plan\n\nWe build the portals release in three cards.\n\n"
            "## Risk assessment\n\nLow. The work is additive.\n\n")
    for ident in named:
        text += (f"{plan_held_repo.OPENER} {ident} — the CEO republishes the "
                 "document from his own session.\n\n")
    return text


def split(records):
    """The two board reads: `children-json` (state, body, labels) and
    `children-detail` (title, body, labels, blocked_by)."""
    cards = [{"identifier": r["identifier"], "body": r["body"],
              "labels": r["labels"], "parent": "DRE-9100", "state": r["state"]}
             for r in records]
    details = [{"identifier": r["identifier"], "title": r["title"],
                "body": r["body"], "labels": r["labels"], "blocked_by": []}
               for r in records]
    return cards, details


def ceo_lines(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if line.startswith(plan_ceo_steps.FINDING_PREFIX)]


class Run:
    """The CLI as the workflow calls it, over scratch files."""

    def __init__(self, records, artifact: str | None, structural: str = ""):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        cards, details = split(records)
        self.detail = base / "detail.json"
        self.detail.write_text(json.dumps(details), encoding="utf-8")
        self.artifact = base / "plan-artifact.md"
        if artifact is not None:
            self.artifact.write_text(artifact, encoding="utf-8")
        self.structural = base / "structural.txt"
        if structural:
            self.structural.write_text(structural, encoding="utf-8")
        self.note = base / "note.md"
        self.note.write_text("Mechanical findings\n", encoding="utf-8")
        self.done = subprocess.run(  # nosec B603 — this repo's own script
            [sys.executable, str(SCRIPT), "findings",
             "--artifact", str(self.artifact),
             "--detail-file", str(self.detail),
             "--structural-file", str(self.structural),
             "--note-file", str(self.note)],
            input=json.dumps(cards), capture_output=True, text=True, timeout=60,
            cwd=ROOT)

    def structural_text(self) -> str:
        return (self.structural.read_text(encoding="utf-8")
                if self.structural.exists() else "")

    def lines(self) -> list[str]:
        return ceo_lines(self.structural_text())

    def close(self):
        self.tmp.cleanup()


class CliCase(unittest.TestCase):

    def run_cli(self, records, artifact, structural: str = "") -> Run:
        run = Run(records, artifact, structural)
        self.addCleanup(run.close)
        self.assertEqual(0, run.done.returncode, run.done.stderr)
        return run


class AnOperatorStepChild(CliCase):

    def test_unnamed_is_one_line_naming_the_card_and_its_title(self):
        run = self.run_cli([OPERATOR, FLEET], plan())
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(OPERATOR_ID, lines[0])
        self.assertIn(OPERATOR_TITLE, lines[0])

    def test_named_by_its_identifier_is_no_line(self):
        run = self.run_cli([OPERATOR, FLEET], plan(OPERATOR_ID))
        self.assertEqual([], run.lines(), run.structural_text())

    def test_named_only_outside_the_question_is_still_a_line(self):
        artifact = plan() + f"## Cards\n\n- {OPERATOR_ID}: {OPERATOR_TITLE}\n"
        run = self.run_cli([OPERATOR], artifact)
        self.assertEqual(1, len(run.lines()), run.structural_text())

    def test_a_longer_identifier_does_not_name_it(self):
        """`DRE-91010` is not `DRE-9101`: the name is a whole token."""
        run = self.run_cli([OPERATOR], plan(OPERATOR_ID + "0"))
        self.assertEqual(1, len(run.lines()), run.structural_text())

    def test_a_delivered_operator_card_is_not_read(self):
        done = dict(OPERATOR, state="Done")
        run = self.run_cli([done], plan())
        self.assertEqual([], run.lines(), run.structural_text())

    def test_the_hold_label_makes_it_one(self):
        held = dict(OPERATOR, labels=["needs-human", "no-code", "repo:portico"])
        run = self.run_cli([held], plan())
        self.assertEqual(1, len(run.lines()), run.structural_text())


class AProofChildOnAPersonsRow(CliCase):

    def test_unnamed_is_a_line_quoting_the_criterion(self):
        run = self.run_cli([PROOF, FLEET], plan())
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(PROOF_ID, lines[0])
        self.assertIn(PRESS, lines[0])

    def test_named_is_no_line(self):
        run = self.run_cli([PROOF, FLEET], plan(PROOF_ID))
        self.assertEqual([], run.lines(), run.structural_text())

    def test_the_operators_press_is_a_persons_row_too(self):
        row = "waiting for the operator's press: the hosted zone is delegated"
        proof = dict(PROOF, body=_body("The record quotes it.", row))
        run = self.run_cli([proof], plan())
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(row, lines[0])

    def test_a_proof_with_no_persons_row_is_not_read(self):
        proof = dict(PROOF, body=_body("The record quotes the release line."))
        run = self.run_cli([proof], plan())
        self.assertEqual([], run.lines(), run.structural_text())


class AFleetChild(CliCase):

    def test_unnamed_is_no_line(self):
        run = self.run_cli([FLEET], plan())
        self.assertEqual([], run.lines(), run.structural_text())

    def test_named_is_no_line(self):
        run = self.run_cli([FLEET], plan(FLEET_ID))
        self.assertEqual([], run.lines(), run.structural_text())

    def test_a_plan_with_none_appends_nothing(self):
        run = self.run_cli([FLEET], plan())
        self.assertFalse(run.structural.exists(),
                         "a plan with no child that needs the CEO opened the file")


class TheStructuralFile(CliCase):

    def test_it_is_appended_to_and_never_truncated(self):
        before = "seam: two epics in one plan\n"
        run = self.run_cli([OPERATOR], plan(), structural=before)
        text = run.structural_text()
        self.assertTrue(text.startswith(before), text)
        self.assertEqual(1, len(run.lines()), text)

    def test_one_line_per_child_that_is_not_named(self):
        run = self.run_cli([OPERATOR, PROOF, FLEET], plan(PROOF_ID))
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(OPERATOR_ID, lines[0])

    def test_an_unread_plan_is_never_a_pass(self):
        run = self.run_cli([OPERATOR, PROOF, FLEET], None)
        lines = run.lines()
        self.assertEqual(2, len(lines), run.structural_text())
        self.assertTrue(any(OPERATOR_ID in line for line in lines), lines)
        self.assertTrue(any(PROOF_ID in line for line in lines), lines)
        self.assertTrue(all("could not be read" in line for line in lines), lines)

    def test_the_note_says_what_was_read(self):
        run = self.run_cli([OPERATOR, FLEET], plan())
        note = run.note.read_text(encoding="utf-8")
        self.assertTrue(note.startswith("Mechanical findings\n"), note)
        self.assertIn(plan_ceo_steps.NOTE_PREFIX, note)
        self.assertIn(OPERATOR_ID, note)

    def test_no_input_at_all_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(  # nosec B603 — this repo's own script
                [sys.executable, str(SCRIPT), "findings",
                 "--artifact", os.path.join(tmp, "missing.md"),
                 "--detail-file", os.path.join(tmp, "missing.json"),
                 "--structural-file", os.path.join(tmp, "s.txt")],
                input="", capture_output=True, text=True, timeout=60, cwd=ROOT)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "s.txt")))


class TheFirstCriticGateSendsItBack(unittest.TestCase):
    """The line lands in the structural file the seam gate reads, so the round
    is a `SEND_BACK` whatever the model wrote."""

    def test_a_ceo_step_line_is_a_structural_finding(self):
        import plan_seam_gate
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "structural.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(plan_ceo_steps.findings(
                    *split([OPERATOR]), plan())[0] + "\n")
            lines = plan_seam_gate.structural_lines(path)
            self.assertEqual(1, len(lines))
            import plan_critic
            result, _ = plan_critic.read_result(plan_seam_gate.rewrite("", lines))
            self.assertEqual(plan_critic.SEND_BACK, result)


# --- Wiring: plan.yml runs it wherever it runs the held-repo check ----------

def _steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [s for job in (doc.get("jobs") or {}).values()
            for s in (job or {}).get("steps") or [] if isinstance(s, dict)]


def _commands(run: str) -> list[str]:
    return [line.strip() for line in re.sub(r"\\\n\s*", " ", run).splitlines()
            if line.strip()]


def _index_of(lines: list[str], needle: str) -> int:
    hits = [i for i, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} lines run {needle!r}: {lines}")
    return hits[0]


def _flag(line: str, name: str):
    words = shlex.split(line)
    return words[words.index(name) + 1] if name in words else None


class EveryHeldRepoStepRunsTheCeoStepCheck(unittest.TestCase):

    def found(self) -> list[dict]:
        return [s for s in _steps() if HELD in str(s.get("run") or "")]

    def test_the_three_places(self):
        """The first critic's round, the revision's round and the epic's note
        on the review route — the three steps that run the held-repo check."""
        held = self.found()
        self.assertEqual(3, len(held), [s.get("name") for s in held])
        ceo = [s for s in _steps() if CEO in str(s.get("run") or "")]
        self.assertEqual([s.get("name") for s in held], [s.get("name") for s in ceo])

    def test_the_same_artifact_note_and_structural_file(self):
        for step in self.found():
            lines = _commands(step["run"])
            held, ceo = lines[_index_of(lines, HELD)], lines[_index_of(lines, CEO)]
            for name in ("--artifact", "--note-file", "--structural-file"):
                self.assertEqual(_flag(held, name), _flag(ceo, name),
                                 f"{step.get('name')}: {name}")
            self.assertTrue(_flag(ceo, "--artifact"), step.get("name"))
            self.assertTrue(_flag(ceo, "--note-file"), step.get("name"))

    def test_on_the_two_board_reads_the_proof_reach_reads(self):
        for step in self.found():
            lines = _commands(step["run"])
            ceo = lines[_index_of(lines, CEO)]
            self.assertEqual("$DETAIL", _flag(ceo, "--detail-file"), step.get("name"))
            words = shlex.split(ceo)
            self.assertIn("<", words, ceo)
            self.assertEqual("$CHILDREN", words[words.index("<") + 1], ceo)

    def test_the_first_critics_rounds_append_to_the_structural_file(self):
        structural = [s for s in self.found()
                      if "plan-structural.txt" in str(s.get("run") or "")]
        self.assertEqual(2, len(structural), [s.get("name") for s in structural])
        for step in structural:
            lines = _commands(step["run"])
            self.assertEqual("${{ runner.temp }}/plan-structural.txt",
                             _flag(lines[_index_of(lines, CEO)], "--structural-file"),
                             step.get("name"))

    def test_before_the_note_is_posted(self):
        for step in self.found():
            lines = _commands(step["run"])
            self.assertLess(_index_of(lines, CEO),
                            _index_of(lines, "linear_ops.py comment"), step.get("name"))


if __name__ == "__main__":
    unittest.main()
