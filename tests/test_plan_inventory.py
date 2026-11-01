"""An epic built from an approved mockup is checked against the mockup's
committed inventory, row by row, before the plan reaches the CEO (DRE-8612).

The CEO corrected builds 27 times in the stuck-work research of 2026-10-31.
On 10-19 the Initiatives screens shipped without their chevrons: nobody checked
the planner's cards one-to-one against the mockup, though committed inventories
existed (DRE-7683, DRE-7692). `scripts/plan_inventory.py` reads them at plan
time. Every inventory row is covered by an open child's criterion, word for
word, or by a deferral with a reason — or an `inventory:` line lands in the
structural file the seam gate forwards into the first critic's result.

Run: python3 -m pytest tests/test_plan_inventory.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 — runs this repo's own scripts
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plan_inventory  # noqa: E402

SCRIPT = SCRIPTS / "plan_inventory.py"
SEAM_GATE = SCRIPTS / "plan_seam_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"
FIXTURE = ROOT / "tests" / "fixtures" / "plan-inventory-initiatives-2026-10-19"

CEO = "plan_ceo_steps.py findings"
INVENTORY = "plan_inventory.py findings"
DESCRIPTION = "linear_ops.py description"

MOCKUP = "console/design/approved/tasks-final-2026-10-18.html"
DESIGN_MOCKUP = "design/approved/initiatives/timeline.html"
TRACKER_MOCKUP = "/Users/sid/agent-bureau-tracker/mockups/initiatives-part-4.html"
INV = "console/design/approved/tasks-inventory.md"
INV_2 = "console/design/approved/initiatives-inventory.md"

ROW_A = "Task row | #board | Title and owner"
ROW_B = "Task row chevron | #board | Chevron"
CRIT_A = "Each task renders as a row with its title and owner."
CRIT_B = "Each task row carries an expand chevron."

TABLE = ("# Tasks inventory\n\n"
         "| Element | Frame | Detail |\n"
         "|---|---|---|\n"
         "| Task row | #board | Title and owner |\n"
         "| Task row chevron | #board | Chevron |\n")


def body(*criteria: str) -> str:
    rows = "\n".join(f"- [ ] {c}" for c in criteria)
    return f"## Problem\n\nSomething.\n\n## Acceptance criteria\n\n{rows}\n"


def child(ident: str, *criteria: str, state: str = "Queued", extra: str = "") -> dict:
    return {"identifier": ident, "body": extra + body(*criteria),
            "labels": ["agent:frontend", "repo:agent-bureau"],
            "parent": "DRE-9000", "state": state}


CHILD_1 = child("DRE-9001", CRIT_A)
CHILD_2 = child("DRE-9002", CRIT_B)


def covered(row: str, card: str, criterion: str, inventory: str = INV) -> dict:
    return {"inventory": inventory, "row": row, "card": card, "criterion": criterion}


def deferred(row: str, reason: str, inventory: str = INV) -> dict:
    return {"inventory": inventory, "row": row, "deferred": reason}


COVERAGE = [covered(ROW_A, "DRE-9001", CRIT_A), covered(ROW_B, "DRE-9002", CRIT_B)]


def artifact(inventory_line: str | None = f"**Inventory:** {INV}",
             coverage=COVERAGE, mention: str = "", raw_block: str | None = None) -> str:
    text = "# Plan\n\n## Summary\n\nWe build the Tasks screen.\n\n"
    if mention:
        text += f"The approved mockup is {mention}.\n\n"
    if inventory_line:
        text += inventory_line + "\n\n"
    text += "## The cards\n\n- DRE-9001\n- DRE-9002\n\n"
    if raw_block is not None:
        text += f"```inventory-coverage\n{raw_block}\n```\n\n"
    elif coverage is not None:
        text += f"```inventory-coverage\n{json.dumps(coverage, indent=1)}\n```\n\n"
    text += "## Risk assessment\n\nLow.\n"
    return text


def epic(mention: str = MOCKUP) -> str:
    return f"## Problem\n\nBuild it from the approved mockup: {mention}\n"


def finding_lines(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if line.startswith(plan_inventory.FINDING_PREFIX)]


class Run:
    """The CLI as the workflow calls it: a scratch checkout as the working
    directory, the inventories at their repo-relative paths inside it."""

    def __init__(self, records, artifact_text: str | None, epic_text: str | None,
                 files: dict | None = None, structural: str = ""):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        for rel, text in (files or {}).items():
            path = self.checkout / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.artifact = self.root / "plan-artifact.md"
        if artifact_text is not None:
            self.artifact.write_text(artifact_text, encoding="utf-8")
        self.epic = self.root / "plan-epic-description.md"
        if epic_text is not None:
            self.epic.write_text(epic_text, encoding="utf-8")
        self.structural = self.root / "plan-structural.txt"
        if structural:
            self.structural.write_text(structural, encoding="utf-8")
        self.note = self.root / "plan-mechanical.md"
        self.note.write_text("Mechanical findings\n", encoding="utf-8")
        self.done = subprocess.run(  # nosec B603 — this repo's own script
            [sys.executable, str(SCRIPT), "findings",
             "--artifact", str(self.artifact),
             "--epic-file", str(self.epic),
             "--structural-file", str(self.structural),
             "--note-file", str(self.note)],
            input=json.dumps(records), capture_output=True, text=True,
            timeout=60, cwd=self.checkout)

    def structural_text(self) -> str:
        return (self.structural.read_text(encoding="utf-8")
                if self.structural.exists() else "")

    def lines(self) -> list[str]:
        return finding_lines(self.structural_text())

    def note_text(self) -> str:
        return self.note.read_text(encoding="utf-8")

    def close(self):
        self.tmp.cleanup()


class CliCase(unittest.TestCase):

    def run_cli(self, records=(CHILD_1, CHILD_2), artifact_text=None,
                epic_text=None, files=None, structural: str = "") -> Run:
        run = Run(list(records), artifact_text, epic_text,
                  {INV: TABLE} if files is None else files, structural)
        self.addCleanup(run.close)
        self.assertEqual(0, run.done.returncode, run.done.stderr)
        return run


# --- A plan that names no approved mockup -----------------------------------

class APlanThatNamesNoMockup(CliCase):

    def test_appends_nothing_and_says_none(self):
        run = self.run_cli(artifact_text=artifact(inventory_line=None, coverage=None),
                           epic_text="## Problem\n\nA backend change.\n")
        self.assertFalse(run.structural.exists(), run.structural_text())
        self.assertIn("Inventory: none — the plan names no approved mockup",
                      run.note_text())

    def test_an_inventory_line_alone_does_not_fire(self):
        """The trigger is the mockup, never the inventory line."""
        run = self.run_cli(artifact_text=artifact(coverage=None),
                           epic_text="## Problem\n\nNothing designed.\n")
        self.assertFalse(run.structural.exists(), run.structural_text())


# --- The trigger: each path, from each of the three texts -------------------

class TheTrigger(CliCase):
    """Each text names the mockup; the artifact carries no `**Inventory:**`
    line, so a trigger that fires is one `inventory:` line naming the path."""

    MOCKUPS = (MOCKUP, DESIGN_MOCKUP, TRACKER_MOCKUP)
    BARE = staticmethod(lambda: artifact(inventory_line=None, coverage=None))

    def fire(self, where: str, path: str) -> Run:
        records = [CHILD_1, CHILD_2]
        art, ep = self.BARE(), "## Problem\n\nNothing named here.\n"
        if where == "epic":
            ep = epic(path)
        elif where == "artifact":
            art = artifact(inventory_line=None, coverage=None, mention=path)
        else:
            records = [child("DRE-9001", CRIT_A,
                             extra=f"Built from `{path}`.\n\n"), CHILD_2]
        return self.run_cli(records, art, ep)

    def test_each_path_fires_from_each_text(self):
        for where in ("epic", "artifact", "child"):
            for path in self.MOCKUPS:
                with self.subTest(where=where, path=path):
                    run = self.fire(where, path)
                    lines = run.lines()
                    self.assertEqual(1, len(lines), run.structural_text())
                    self.assertIn(path, lines[0])
                    self.assertIn("**Inventory:**", lines[0])

    def assert_silent(self, records, art, ep):
        run = self.run_cli(records, art, ep)
        self.assertFalse(run.structural.exists(), run.structural_text())
        self.assertIn("Inventory: none", run.note_text())

    def test_an_inventory_path_on_its_own_does_not_fire(self):
        self.assert_silent([CHILD_1], self.BARE(), epic(INV))

    def test_a_screen_png_does_not_fire(self):
        png = "console/design/images/screens/desktop/initiatives.png"
        self.assert_silent([child("DRE-9001", CRIT_A, extra=f"**Design:** {png}\n\n")],
                           artifact(inventory_line=None, coverage=None, mention=png),
                           epic(png))

    def test_a_path_only_in_a_done_childs_body_does_not_fire(self):
        done = child("DRE-9001", CRIT_A, state="Done", extra=f"Built from {MOCKUP}.\n\n")
        self.assert_silent([done, CHILD_2], self.BARE(),
                           "## Problem\n\nNothing named here.\n")

    def test_an_html_file_outside_a_mockups_folder_does_not_fire(self):
        self.assert_silent([CHILD_1], self.BARE(),
                           epic("/Users/sid/agent-bureau-tracker/drafts/initiatives.html"))

    def test_trailing_punctuation_is_not_part_of_the_path(self):
        run = self.run_cli([CHILD_1], self.BARE(), f"Built from {MOCKUP}.\n")
        self.assertIn(MOCKUP, run.lines()[0])
        self.assertNotIn(f"{MOCKUP}.", run.lines()[0])


# --- 1. No **Inventory:** line ----------------------------------------------

class NoInventoryLine(CliCase):

    def test_one_line_naming_the_mockup(self):
        run = self.run_cli(artifact_text=artifact(inventory_line=None),
                           epic_text=epic(MOCKUP))
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(MOCKUP, lines[0])

    def test_a_mockup_in_the_checkout_names_the_inventories_beside_it(self):
        files = {MOCKUP: "<html></html>\n", INV: TABLE, INV_2: TABLE}
        run = self.run_cli(artifact_text=artifact(inventory_line=None),
                           epic_text=epic(MOCKUP), files=files)
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(INV, lines[0])
        self.assertIn(INV_2, lines[0])

    def test_a_mockup_outside_the_checkout_names_no_inventory(self):
        """The mockup path is card text; nothing outside the checkout is listed."""
        outside = "../outside/design/approved/tasks-final.html"
        files = {outside: "<html></html>\n",
                 "../outside/design/approved/secret-inventory.md": TABLE}
        run = self.run_cli(artifact_text=artifact(inventory_line=None),
                           epic_text=epic(outside), files=files)
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(outside, lines[0])
        self.assertNotIn("secret-inventory.md", lines[0])

    def test_a_mockup_not_in_the_checkout_names_no_inventory(self):
        run = self.run_cli(artifact_text=artifact(inventory_line=None),
                           epic_text=epic(MOCKUP), files={INV: TABLE})
        self.assertNotIn(INV, run.lines()[0])


# --- 2. An inventory that cannot be read ------------------------------------

class AnUnreadableInventory(CliCase):

    def test_absent_from_the_checkout(self):
        run = self.run_cli(artifact_text=artifact(), epic_text=epic(), files={})
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(INV, lines[0])
        self.assertIn("could not be read", lines[0])
        self.assertIn("absent from this checkout", lines[0])
        self.assertNotIn("Inventory: none", run.note_text())

    def test_holding_no_table_rows(self):
        empty = "# Tasks inventory\n\n| Element | Frame |\n|---|---|\n\nProse only.\n"
        run = self.run_cli(artifact_text=artifact(coverage=[]), epic_text=epic(),
                           files={INV: empty})
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn(INV, lines[0])
        self.assertIn("could not be read", lines[0])
        self.assertIn("holds no table rows", lines[0])
        self.assertNotIn("Inventory: none", run.note_text())

    def test_a_path_outside_the_checkout(self):
        run = self.run_cli(artifact_text=artifact(inventory_line="**Inventory:** ../x-inventory.md"),
                           epic_text=epic())
        lines = [line for line in run.lines() if "../x-inventory.md" in line]
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn("could not be read", lines[0])

    def test_prose_after_the_path_is_not_part_of_it(self):
        run = self.run_cli(artifact_text=artifact(
            inventory_line=f"**Inventory:** `{INV}` (committed 10-17)"),
            epic_text=epic())
        self.assertFalse(run.structural.exists(), run.structural_text())

    def test_several_on_one_line(self):
        line = f"**Inventory:** `{INV}`, {INV_2}"
        run = self.run_cli(artifact_text=artifact(inventory_line=line),
                           epic_text=epic())
        unread = [x for x in run.lines() if "could not be read" in x]
        self.assertEqual(1, len(unread), run.structural_text())
        self.assertIn(INV_2, unread[0])


# --- What a row is ----------------------------------------------------------

class TheRows(unittest.TestCase):

    def test_two_tables_give_the_exact_keys(self):
        text = ("# Inventory\n\n"
                "| Element | Frame |\n"
                "| :--- | ---: |\n"
                "| Header   title |  #top |\n"
                "|Row  chevron|#list|\n"
                "\nSome prose between the tables.\n\n"
                "| Surface | State | Note |\n"
                "|---|---|---|\n"
                "| Empty state | #empty | No   items yet |\n")
        self.assertEqual(
            ["Header title | #top", "Row chevron | #list",
             "Empty state | #empty | No items yet"],
            plan_inventory.rows(text))

    def test_a_row_written_three_ways_is_one_key(self):
        for written in ("Task row|#board|Title and owner",
                        "| Task row | #board | Title and owner |",
                        "  Task row |  #board | Title  and owner "):
            with self.subTest(written=written):
                self.assertEqual(ROW_A, plan_inventory.key(written))

    EMPTY_CELLS = ("| Element | Frame | Detail |\n"
                   "|---|---|---|\n"
                   "| Chevron | #timeline |  |\n"
                   "| Chevron |  | Rotates open |\n"
                   "|  | #timeline | Divider |\n")

    def test_every_key_is_its_own_key(self):
        """A planner copies a key from the note or a finding; it must match."""
        found = plan_inventory.rows(self.EMPTY_CELLS + TABLE)
        self.assertEqual(5, len(found))
        for row in found:
            with self.subTest(row=row):
                self.assertEqual(row, plan_inventory.key(row))
                self.assertEqual(row, plan_inventory.key(" ".join(row.split())))

    def test_an_empty_trailing_cell_is_one_key_written_four_ways(self):
        row = plan_inventory.rows(self.EMPTY_CELLS)[0]
        for written in ("| Chevron | #timeline |  |", "Chevron | #timeline |",
                        "Chevron | #timeline | ", "Chevron | #timeline"):
            with self.subTest(written=written):
                self.assertEqual(row, plan_inventory.key(written))

    def test_an_empty_middle_cell_keeps_its_place(self):
        row = plan_inventory.rows(self.EMPTY_CELLS)[1]
        self.assertEqual(row, plan_inventory.key("Chevron | | Rotates open"))
        self.assertNotEqual(row, plan_inventory.key("Chevron | Rotates open"))


# --- What covers a row ------------------------------------------------------

class WhatCoversARow(CliCase):

    def assert_row_b_uncovered(self, coverage, records=(CHILD_1, CHILD_2)):
        run = self.run_cli(records, artifact(coverage=coverage), epic())
        uncovered = [line for line in run.lines()
                     if ROW_B in line and "no record covers" in line]
        self.assertEqual(1, len(uncovered), run.structural_text())
        self.assertIn(INV, uncovered[0])
        self.assertFalse(any(ROW_A in line for line in run.lines()), run.structural_text())
        return run

    def test_every_row_covered_word_for_word_is_no_line(self):
        run = self.run_cli(artifact_text=artifact(), epic_text=epic())
        self.assertFalse(run.structural.exists(), run.structural_text())

    def test_whitespace_in_the_criterion_is_collapsed(self):
        spaced = [COVERAGE[0], covered(ROW_B, "DRE-9002", "Each  task row\ncarries an expand chevron.")]
        run = self.run_cli(artifact_text=artifact(coverage=spaced), epic_text=epic())
        self.assertFalse(run.structural.exists(), run.structural_text())

    def test_a_deferral_with_a_reason_covers_it(self):
        run = self.run_cli(artifact_text=artifact(
            coverage=[COVERAGE[0], deferred(ROW_B, "Tasks part 2 draws it")]),
            epic_text=epic())
        self.assertFalse(run.structural.exists(), run.structural_text())

    def test_a_paraphrased_criterion(self):
        run = self.assert_row_b_uncovered(
            [COVERAGE[0], covered(ROW_B, "DRE-9002", "Task rows have a chevron.")])
        self.assertTrue(any("DRE-9002" in line and "criteria" in line
                            for line in run.lines()), run.structural_text())

    def test_a_card_that_is_not_a_child(self):
        run = self.assert_row_b_uncovered(
            [COVERAGE[0], covered(ROW_B, "DRE-4444", CRIT_B)])
        self.assertTrue(any("DRE-4444" in line and "not an open child" in line
                            for line in run.lines()), run.structural_text())

    def test_a_done_child(self):
        self.assert_row_b_uncovered(
            COVERAGE, records=(CHILD_1, child("DRE-9002", CRIT_B, state="Done")))

    def test_an_empty_deferral(self):
        self.assert_row_b_uncovered([COVERAGE[0], deferred(ROW_B, "  ")])

    def test_a_screen_level_record_does_not_cover_a_row(self):
        """The 10-19 chevrons were a detail inside a carded screen."""
        self.assert_row_b_uncovered(
            [COVERAGE[0], covered("Tasks screen", "DRE-9002", CRIT_B)])

    def test_a_row_with_an_empty_last_cell_is_covered_by_the_key_its_finding_quotes(self):
        table = TABLE + "| Divider | #board |  |\n"
        first = self.run_cli(artifact_text=artifact(), epic_text=epic(),
                             files={INV: table})
        quoted = re.findall(r"row “([^”]*)”", "\n".join(first.lines()))
        self.assertEqual(1, len(quoted), first.structural_text())
        run = self.run_cli(artifact_text=artifact(
            coverage=COVERAGE + [deferred(quoted[0], "Tasks part 2 draws it")]),
            epic_text=epic(), files={INV: table})
        self.assertFalse(run.structural.exists(), run.structural_text())

    def test_a_deferral_naming_a_card_that_is_not_a_child_says_so_on_the_note(self):
        """The reason is a whole deferral; the card it names is reported to
        the first critic, never sent back."""
        record = dict(deferred(ROW_B, "Tasks part 2 draws it"), card="DRE-4444")
        run = self.run_cli(artifact_text=artifact(coverage=[COVERAGE[0], record]),
                           epic_text=epic())
        self.assertFalse(run.structural.exists(), run.structural_text())
        note = [line for line in run.note_text().splitlines() if ROW_B in line]
        self.assertEqual(1, len(note), run.note_text())
        self.assertIn("deferred — Tasks part 2 draws it", note[0])
        self.assertIn("DRE-4444 is not an open child", note[0])

    def test_a_record_for_a_row_the_inventory_lacks(self):
        run = self.run_cli(artifact_text=artifact(
            coverage=COVERAGE + [covered("Ghost row | #board | Nothing", "DRE-9001", CRIT_A)]),
            epic_text=epic())
        lines = run.lines()
        self.assertEqual(1, len(lines), run.structural_text())
        self.assertIn("Ghost row | #board | Nothing", lines[0])
        self.assertIn("matches no row", lines[0])


# --- 3. A missing or unparseable coverage block -----------------------------

class TheCoverageBlock(CliCase):

    TWO = f"**Inventory:** {INV}, {INV_2}"

    def assert_one_line_per_inventory(self, art):
        run = self.run_cli(artifact_text=art, epic_text=epic(),
                           files={INV: TABLE, INV_2: TABLE.replace("Task", "Goal")})
        lines = run.lines()
        self.assertEqual(2, len(lines), run.structural_text())
        self.assertIn(INV, lines[0])
        self.assertIn(INV_2, lines[1])
        for line in lines:
            self.assertIn("2 rows", line)
            self.assertNotIn(ROW_A, line)

    def test_no_block(self):
        self.assert_one_line_per_inventory(artifact(inventory_line=self.TWO, coverage=None))

    def test_a_block_that_does_not_parse(self):
        self.assert_one_line_per_inventory(
            artifact(inventory_line=self.TWO, raw_block='[{"row": "unclosed'))

    def test_a_block_that_is_not_a_list(self):
        self.assert_one_line_per_inventory(
            artifact(inventory_line=self.TWO, raw_block='{"row": "x"}'))

    def test_a_block_outside_the_cards_section_is_not_read(self):
        art = artifact(coverage=None).replace(
            "## Summary\n", "## Summary\n\n```inventory-coverage\n"
            + json.dumps(COVERAGE) + "\n```\n")
        run = self.run_cli(artifact_text=art, epic_text=epic())
        self.assertEqual(1, len(run.lines()), run.structural_text())


# --- The note ---------------------------------------------------------------

class TheNote(CliCase):

    def test_one_summary_line_and_one_line_per_row(self):
        files = {INV: TABLE + "| Empty state | #empty | No tasks |\n"}
        coverage = [COVERAGE[0], deferred("Empty state | #empty | No tasks", "Tasks part 2")]
        run = self.run_cli(artifact_text=artifact(coverage=coverage), epic_text=epic(),
                           files=files)
        note = run.note_text()
        self.assertTrue(note.startswith("Mechanical findings\n"), note)
        self.assertIn(f"Inventory: {INV} — 3 rows: 1 covered, 1 deferred, 1 uncovered", note)
        self.assertIn(f"{ROW_A} → DRE-9001 “{CRIT_A}”", note)
        self.assertIn("Empty state | #empty | No tasks → deferred — Tasks part 2", note)
        self.assertIn(f"{ROW_B} → UNCOVERED", note)

    def test_an_unread_epic_says_so_and_the_check_runs_on(self):
        run = self.run_cli(artifact_text=artifact(inventory_line=None, mention=MOCKUP),
                           epic_text=None)
        self.assertIn("description could not be read", run.note_text())
        self.assertEqual(1, len(run.lines()), run.structural_text())

    def test_an_unread_epic_does_not_send_back_a_plan_naming_no_mockup(self):
        run = self.run_cli(artifact_text=artifact(inventory_line=None, coverage=None),
                           epic_text=None)
        self.assertFalse(run.structural.exists(), run.structural_text())
        self.assertIn("description could not be read", run.note_text())
        self.assertIn("Inventory: none", run.note_text())


# --- The structural file ----------------------------------------------------

class TheStructuralFile(CliCase):

    def test_it_is_appended_to_and_never_truncated(self):
        before = "ceo-step: DRE-9001 needs the CEO\n"
        run = self.run_cli(artifact_text=artifact(inventory_line=None),
                           epic_text=epic(), structural=before)
        text = run.structural_text()
        self.assertTrue(text.startswith(before), text)
        self.assertEqual(1, len(run.lines()), text)

    def test_an_exception_is_a_line_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp, "a.md")
            art.write_text(artifact(), encoding="utf-8")
            ep = Path(tmp, "e.md")
            ep.write_text(epic(), encoding="utf-8")
            structural = Path(tmp, "s.txt")
            note = Path(tmp, "n.md")
            with mock.patch.object(plan_inventory, "findings",
                                   side_effect=RuntimeError("boom")), \
                    mock.patch("sys.stdin", io.StringIO(json.dumps([CHILD_1]))), \
                    mock.patch("sys.stdout", io.StringIO()):
                code = plan_inventory.main(
                    ["findings", "--artifact", str(art), "--epic-file", str(ep),
                     "--structural-file", str(structural), "--note-file", str(note)])
            self.assertEqual(0, code)
            lines = finding_lines(structural.read_text(encoding="utf-8"))
            self.assertEqual(1, len(lines))
            self.assertTrue(lines[0].startswith(
                "inventory: the check could not run — "), lines[0])
            self.assertIn("boom", lines[0])
            self.assertNotIn("Inventory: none", note.read_text(encoding="utf-8"))

    def test_no_input_at_all_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run(  # nosec B603 — this repo's own script
                [sys.executable, str(SCRIPT), "findings",
                 "--artifact", os.path.join(tmp, "missing.md"),
                 "--epic-file", os.path.join(tmp, "missing-epic.md"),
                 "--structural-file", os.path.join(tmp, "s.txt")],
                input="", capture_output=True, text=True, timeout=60, cwd=tmp)
            self.assertEqual(0, done.returncode, done.stderr)


# --- The replay: the 10-19 Initiatives plan ---------------------------------

class TheReplay(unittest.TestCase):
    """A reconstruction of the 10-19 plan: every inventory row carded but the
    chevron. The check finds exactly that row, and the seam gate turns the
    critic's PASS into a SEND_BACK."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.checkout = base / "checkout"
        inv = self.checkout / INV_2
        inv.parent.mkdir(parents=True)
        shutil.copy(FIXTURE / "inventory.md", inv)
        self.structural = base / "plan-structural.txt"
        self.note = base / "plan-mechanical.md"
        self.done = subprocess.run(  # nosec B603 — this repo's own script
            [sys.executable, str(SCRIPT), "findings",
             "--artifact", str(FIXTURE / "artifact.md"),
             "--epic-file", str(FIXTURE / "epic.md"),
             "--structural-file", str(self.structural),
             "--note-file", str(self.note)],
            input=(FIXTURE / "children.json").read_text(encoding="utf-8"),
            capture_output=True, text=True, timeout=60, cwd=self.checkout)
        self.assertEqual(0, self.done.returncode, self.done.stderr)

    def test_the_fixture_is_labeled_a_reconstruction(self):
        heading = (FIXTURE / "inventory.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("RECONSTRUCTION", heading.upper())

    def test_no_child_criterion_mentions_a_chevron(self):
        records = json.loads((FIXTURE / "children.json").read_text(encoding="utf-8"))
        self.assertNotIn("chevron", json.dumps(records).lower())

    def test_exactly_one_line_quoting_the_chevron_row(self):
        lines = finding_lines(self.structural.read_text(encoding="utf-8"))
        self.assertEqual(1, len(lines), lines)
        self.assertIn("Initiative row expand chevron | #timeline | Chevron", lines[0])

    def test_the_seam_gate_sends_a_pass_back(self):
        import plan_critic
        result_file = Path(self.tmp.name, "result.md")
        result_file.write_text(plan_critic.result_line(plan_critic.PASS)
                               + "\nEvery card is sound.\n", encoding="utf-8")
        gate = subprocess.run(  # nosec B603 — this repo's own script
            [sys.executable, str(SEAM_GATE), "gate",
             "--result-file", str(result_file),
             "--structural-file", str(self.structural)],
            capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(0, gate.returncode, gate.stderr)
        result, reason = plan_critic.read_result(result_file.read_text(encoding="utf-8"))
        self.assertEqual(plan_critic.SEND_BACK, result)
        self.assertIn("chevron", reason)


# --- Wiring: plan.yml runs it after every CEO-step check --------------------

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


def _stdin(line: str):
    words = shlex.split(line)
    return words[words.index("<") + 1] if "<" in words else None


class EveryCeoStepRunsTheInventoryCheck(unittest.TestCase):
    """Discovered, never counted: a fourth step running the CEO-step check
    fails here until it runs this one too."""

    def found(self) -> list[dict]:
        steps = [s for s in _steps() if CEO in str(s.get("run") or "")]
        self.assertTrue(steps, "no plan.yml step runs the CEO-step check")
        return steps

    def test_each_runs_it_right_after_the_ceo_step_check(self):
        for step in self.found():
            lines = _commands(step["run"])
            self.assertGreater(_index_of(lines, INVENTORY), _index_of(lines, CEO),
                               step.get("name"))

    def test_the_same_artifact_note_structural_file_and_children(self):
        for step in self.found():
            lines = _commands(step["run"])
            ceo, inv = lines[_index_of(lines, CEO)], lines[_index_of(lines, INVENTORY)]
            for name in ("--artifact", "--note-file", "--structural-file"):
                self.assertEqual(_flag(ceo, name), _flag(inv, name),
                                 f"{step.get('name')}: {name}")
            self.assertTrue(_flag(inv, "--note-file"), step.get("name"))
            self.assertEqual(_stdin(ceo), _stdin(inv), step.get("name"))
            self.assertTrue(_stdin(inv), step.get("name"))

    def test_the_epic_file_is_saved_from_the_epics_description(self):
        for step in self.found():
            lines = _commands(step["run"])
            inv_at = _index_of(lines, INVENTORY)
            epic_file = _flag(lines[inv_at], "--epic-file")
            self.assertTrue(epic_file, step.get("name"))
            desc_at = _index_of(lines, DESCRIPTION)
            self.assertLess(desc_at, inv_at, step.get("name"))
            words = shlex.split(lines[desc_at])
            self.assertEqual("$EPIC", words[words.index("description") + 1],
                             step.get("name"))
            self.assertEqual(epic_file, words[words.index(">") + 1], step.get("name"))

    def test_before_the_note_is_posted(self):
        for step in self.found():
            lines = _commands(step["run"])
            self.assertLess(_index_of(lines, INVENTORY),
                            _index_of(lines, "linear_ops.py comment"), step.get("name"))


if __name__ == "__main__":
    unittest.main()
