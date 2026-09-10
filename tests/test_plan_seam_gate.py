"""The seam gate — the one mechanical finding that is a decision (DRE-3398).

Every other mechanical finding is INPUT: `plan_critic.py mechanical` prints its
list, the note lands on the epic, and the model decides what to do about it. On
DRE-3164 two critics read a fifteen-card plan across six rounds and never named
the seam, so for THIS finding input was not enough. The gate rewrites the
critic's result file before `plan_critic.py decide` reads it, and the round is a
send-back with the seam as its reason whatever the model wrote.

**The seam sentence is never typed in this file.** It comes from DRE-3395's
reader over DRE-3395's fixture — `plan_seam.seam_findings(fixture)` — so if the
definition of the waiting set ever moves, this suite moves with it rather than
asserting a stale string. `len(lines) == 1` is asserted FIRST, in its own test
and in `setUp`, so a fixture that stops yielding one seam fails here loudly
instead of quietly passing a gate test against nothing.

The gate knows nothing about what a seam is: it forwards lines. Everything it
composes with is `plan_critic`'s existing grammar (`read_result`,
`result_line`, `findings_block`, `further_findings`), and nothing in
`plan_critic.py` is edited by the card this suite belongs to — the marker
regex, `trusted_bodies`, `parse_markers`, `send_backs` and the two-round bound
are untouched.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_seam_gate.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
WF = os.path.join(ROOT, ".github", "workflows", "plan.yml")
STANDARD = os.path.join(ROOT, "standards", "plan-critic.md")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "dre-3164-children-2026-09-05.json")
sys.path.insert(0, os.path.abspath(SCRIPTS))

import plan_critic as pc  # noqa: E402
import plan_seam  # noqa: E402
import plan_seam_gate as gate  # noqa: E402

#: The shared path, spelled once here and once in `plan.yml`. The mechanical
#: step writes it, both decision steps read it, and the re-check removes it.
STRUCTURAL_PATH = "${{ runner.temp }}/plan-structural.txt"


def seam_lines() -> list[str]:
    """DRE-3395's reading of DRE-3395's fixture. The ONLY source of the seam
    sentence in this suite — nothing here types it."""
    with open(FIXTURE, encoding="utf-8") as f:
        return plan_seam.seam_findings(json.load(f))


def steps() -> list[dict]:
    with open(WF, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


def step_named(fragment: str) -> dict:
    for s in steps():
        if fragment.lower() in (s.get("name") or "").lower():
            return s
    raise AssertionError(f"no step named like {fragment!r}")


def step_with_id(step_id: str) -> dict:
    for s in steps():
        if s.get("id") == step_id:
            return s
    raise AssertionError(f"no step with id {step_id!r}")


class TheFixtureIsTheSourceOfTheSentence(unittest.TestCase):
    """Asserted before anything else in this file depends on it."""

    def test_the_fixture_yields_exactly_one_seam(self):
        lines = seam_lines()
        self.assertEqual(len(lines), 1,
                         "DRE-3395's fixture no longer yields one seam — every "
                         "assertion below is about a line that moved")
        self.assertTrue(lines[0].strip())

    def test_the_prefix_is_one_constant(self):
        self.assertEqual(
            gate.PREFIX,
            "The mechanical check found this before the critic read the plan: ")


class GateBase(unittest.TestCase):
    def setUp(self):
        self.lines = seam_lines()
        self.assertEqual(len(self.lines), 1)
        self.tmp = tempfile.mkdtemp()
        self.result = os.path.join(self.tmp, "plan-critic-pre.md")
        self.structural = os.path.join(self.tmp, "plan-structural.txt")
        with open(self.structural, "w", encoding="utf-8") as f:
            for line in self.lines:
                f.write(line + "\n")

    def write_result(self, text: str) -> None:
        with open(self.result, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    def run_gate(self, structural: str | None = None):
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_seam_gate.py"), "gate",
             "--result-file", self.result,
             "--structural-file", self.structural if structural is None
             else structural],
            capture_output=True, text=True)

    def decide(self, stage: str = "pre"):
        self.gho = os.path.join(self.tmp, "out")
        self.note = os.path.join(self.tmp, "note.md")
        self.record = os.path.join(self.tmp, "record.txt")
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "plan_critic.py"), "decide",
             "--stage", stage, "--epic", "DRE-3164",
             "--result-file", self.result,
             "--github-output", self.gho,
             "--note-file", self.note, "--record-file", self.record],
            input=json.dumps([]), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out


class APassOverASeamIsASendBack(GateBase):
    """AC2. The critic passed the plan; the round is a send-back anyway, and
    the reason is the mechanical check's sentence."""

    def setUp(self):
        super().setUp()
        self.write_result(pc.result_line(pc.PASS) + "\n"
                          "Every card carries criteria and a repo.\n")
        out = self.run_gate()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.decide()

    def test_the_decision_holds_the_round_with_the_seam_as_its_reason(self):
        written = self.read(self.gho)
        self.assertIn("action=hold", written)
        self.assertIn(f"result={pc.SEND_BACK}", written)
        self.assertIn(f"reason={gate.PREFIX}{self.lines[0]}", written)

    def test_the_reason_is_exactly_the_prefix_plus_the_readers_line(self):
        result, reason = pc.read_result(self.read(self.result))
        self.assertEqual(result, pc.SEND_BACK)
        self.assertEqual(reason, gate.PREFIX + self.lines[0])

    def test_the_seam_is_counted_among_the_findings(self):
        """Two: the headline carries the attribution and the numbered list
        carries the finding itself, which is the line the re-plan acts on."""
        self.assertIn(f"findings_count={len(self.lines) + 1}", self.read(self.gho))
        self.assertEqual(pc.all_findings(self.read(self.result)),
                         [gate.PREFIX + self.lines[0]] + self.lines)

    def test_the_note_names_the_seam_and_says_the_check_found_it(self):
        note = self.read(self.note)
        self.assertIn("🛑", note)
        self.assertIn(self.lines[0], note)
        self.assertIn(gate.PREFIX.strip().rstrip(":"), note)

    def test_the_record_is_one_marker_line_carrying_the_send_back(self):
        record = self.read(self.record).strip()
        self.assertEqual(len(record.splitlines()), 1)
        self.assertEqual(record, pc.marker(pc.STAGE_PRE, 1, pc.SEND_BACK,
                                           gate.PREFIX + self.lines[0], 0))
        self.assertEqual(pc.send_backs([record], pc.STAGE_PRE), 1)
        self.assertEqual(len(pc.parse_markers([record])), 1)

    def test_the_critics_own_text_survives_below_ours(self):
        """Indented four spaces, so its numbered lines are not findings of this
        round and its own header cannot overturn the first result line."""
        body = self.read(self.result)
        self.assertIn("    Every card carries criteria and a repo.", body)
        self.assertIn(f"    {pc.result_line(pc.PASS)}", body)


class ACrashedCriticIsStillASendBack(GateBase):
    """A round that wrote no verdict is `NO_RESULT`, and the seam still decides
    it — a critic that never ran must not let a two-epic plan through."""

    def test_an_empty_result_file_becomes_the_seam_send_back(self):
        self.write_result("")
        self.assertEqual(self.run_gate().returncode, 0)
        self.decide()
        written = self.read(self.gho)
        self.assertIn("action=hold", written)
        self.assertIn(f"result={pc.SEND_BACK}", written)
        self.assertIn(f"reason={gate.PREFIX}{self.lines[0]}", written)

    def test_a_missing_result_file_becomes_the_seam_send_back(self):
        self.assertFalse(os.path.exists(self.result))
        self.assertEqual(self.run_gate().returncode, 0)
        self.decide()
        self.assertIn(f"result={pc.SEND_BACK}", self.read(self.gho))


class ACriticSendBackKeepsItsOwnReason(GateBase):
    """AC4. The critic already found the worst gap; the seam does not displace
    it, it joins the list at the top."""

    def setUp(self):
        super().setUp()
        self.write_result(
            pc.result_line(pc.SEND_BACK, "DRE-9001 has no acceptance criteria")
            + "\n"
            "1. DRE-9002: no repo label\n"
            "2. DRE-9003: collides with DRE-9004 on plan.yml\n")
        out = self.run_gate()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.decide()

    def test_the_first_line_is_still_the_critics(self):
        result, reason = pc.read_result(self.read(self.result))
        self.assertEqual(result, pc.SEND_BACK)
        self.assertEqual(reason, "DRE-9001 has no acceptance criteria")
        self.assertIn("reason=DRE-9001 has no acceptance criteria",
                      self.read(self.gho))

    def test_the_seam_lines_are_the_first_numbered_findings(self):
        found = pc.all_findings(self.read(self.result))
        self.assertEqual(found, [
            "DRE-9001 has no acceptance criteria",
            self.lines[0],
            "DRE-9002: no repo label",
            "DRE-9003: collides with DRE-9004 on plan.yml",
        ])

    def test_the_seam_reaches_the_findings_output_and_its_count(self):
        written = self.read(self.gho)
        self.assertIn("findings_count=4", written)
        self.assertIn(self.lines[0], written)


class NoSeamChangesNothing(GateBase):
    """AC3. An empty structural file is a check that ran and found nothing."""

    def setUp(self):
        super().setUp()
        with open(self.structural, "w", encoding="utf-8") as f:
            f.write("")

    def test_the_result_file_is_byte_identical(self):
        text = pc.result_line(pc.PASS) + "\nEvery card carries criteria.\n"
        self.write_result(text)
        before = open(self.result, "rb").read()
        self.assertEqual(self.run_gate().returncode, 0)
        self.assertEqual(open(self.result, "rb").read(), before)
        self.assertEqual(self.read(self.result), text)

    def test_a_pass_decides_exactly_as_before(self):
        self.write_result(pc.result_line(pc.PASS) + "\n")
        self.assertEqual(self.run_gate().returncode, 0)
        self.decide()
        written = self.read(self.gho)
        self.assertIn("action=proceed", written)
        self.assertIn(f"result={pc.PASS}", written)
        self.assertIn("findings_count=0", written)

    def test_a_send_back_keeps_its_own_findings_untouched(self):
        text = (pc.result_line(pc.SEND_BACK, "DRE-9001 has no criteria") + "\n"
                "1. DRE-9002: no repo label\n")
        self.write_result(text)
        self.assertEqual(self.run_gate().returncode, 0)
        self.assertEqual(self.read(self.result), text)


class AnAbsentStructuralFileIsARefusal(GateBase):
    """AC3. A mechanical step that never ran must not read as "no seam"."""

    def test_the_gate_exits_non_zero_with_the_reason_on_stderr(self):
        self.write_result(pc.result_line(pc.PASS) + "\n")
        missing = os.path.join(self.tmp, "never-written.txt")
        out = self.run_gate(structural=missing)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(missing, out.stderr)
        self.assertIn("seam", out.stderr.lower())

    def test_it_leaves_the_result_file_alone(self):
        text = pc.result_line(pc.PASS) + "\n"
        self.write_result(text)
        self.run_gate(structural=os.path.join(self.tmp, "never-written.txt"))
        self.assertEqual(self.read(self.result), text)


class TheRailCarriesTheWiring(unittest.TestCase):
    """AC5, read off `plan.yml` itself — one shared path, on the pre-approval
    chain only."""

    PRE_MECHANICAL = ("Mechanical findings — posted before the critic reads them",
                      "Mechanical findings — the revised plan")

    def run_of(self, step: dict) -> str:
        return str(step.get("run") or "")

    def test_both_pre_stage_mechanical_steps_read_the_seam(self):
        for name in self.PRE_MECHANICAL:
            step = step_named(name)
            run = self.run_of(step)
            self.assertIn("plan_seam.py findings", run, name)
            self.assertIn(STRUCTURAL_PATH, run, name)
            self.assertLess(run.index("plan_critic.py mechanical"),
                            run.index("plan_seam.py findings"),
                            f"{name}: the seam is read before the mechanical "
                            "note it appends to exists")

    def test_the_revised_plan_step_is_the_pre_stage_one(self):
        """`Mechanical findings — the revised plan (after approval)` is
        DRE-3282's and must not be the step matched above."""
        step = step_named("Mechanical findings — the revised plan")
        self.assertNotIn("after approval", step.get("name") or "")

    def test_both_pre_decisions_gate_before_they_decide(self):
        for step_id in ("pre1", "pre2"):
            run = self.run_of(step_with_id(step_id))
            self.assertIn("plan_seam_gate.py gate", run, step_id)
            self.assertIn(STRUCTURAL_PATH, run, step_id)
            self.assertLess(run.index("plan_seam_gate.py gate"),
                            run.index("plan_critic.py decide"),
                            f"{step_id}: the decision reads the file before the "
                            "gate rewrites it")

    def test_the_re_check_removes_the_structural_file(self):
        run = self.run_of(step_named("Re-check the revised plan"))
        self.assertRegex(run, r"rm -f [^\n]*plan-structural\.txt")

    def test_the_replan_is_told_a_seam_is_two_epics(self):
        step = step_named("Re-plan after send-back")
        prompt = str((step.get("with") or {}).get("prompt") or "")
        self.assertIn("second epic", prompt)
        self.assertIn("wait on observing", prompt)

    def test_no_activate_route_step_names_the_gate_or_the_path(self):
        for s in steps():
            cond = str(s.get("if") or "")
            if "activate" not in cond:
                continue
            blob = json.dumps(s)
            name = s.get("name") or s.get("id") or "<unnamed>"
            for token in ("plan_seam.py", "plan_seam_gate.py",
                          "plan-structural.txt"):
                self.assertNotIn(token, blob,
                                 f"{name} is on the activate route and names "
                                 f"{token} — the post-approval chain is "
                                 "DRE-3282's and this card leaves it alone")

    def test_the_pre_stage_reads_are_saved_to_files(self):
        for name in self.PRE_MECHANICAL:
            run = self.run_of(step_named(name))
            self.assertIn("plan-children.json", run, name)
            self.assertIn("plan-children-detail.json", run, name)


class TheStandardCarriesTheParagraph(unittest.TestCase):
    """AC6."""

    def text(self) -> str:
        with open(STANDARD, encoding="utf-8") as f:
            return f.read()

    def test_the_finding_sentence_is_written_verbatim(self):
        self.assertIn(seam_lines()[0], self.text())

    def test_it_names_both_the_reader_and_the_gate(self):
        text = self.text()
        self.assertIn("scripts/plan_seam.py", text)
        self.assertIn("scripts/plan_seam_gate.py", text)

    def test_the_input_sentence_carries_the_exception(self):
        """"the findings are posted to the epic before the model reads them"
        is no longer true of all of them."""
        line = [l for l in self.text().splitlines()
                if "posted to the epic before the model reads them" in l]
        self.assertTrue(line, "the sentence this paragraph amends is gone")
        window = self.text().split(
            "posted to the epic before the model reads them", 1)[1][:200]
        self.assertIn("seam", window.lower())

    def test_after_approval_the_seam_is_still_input(self):
        self.assertIn("DRE-3400", self.text())


if __name__ == "__main__":
    unittest.main()
