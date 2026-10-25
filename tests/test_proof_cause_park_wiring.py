"""The proof run's escalation is sorted by what it names (DRE-6613, DRE-6840).

`proof-task.yml`'s `Report proof result to Linear` step reads the run's
escalation file three ways. A file naming a press parks as a press. A file of
cause rows and nothing else — the three declared lines, its Question
`proof_cause_park.QUESTION` word for word, and its `cause:` lines — is handed,
once the record pull request is OPEN, to the one writer DRE-6837 gives a CLI,
`proof_cause_park.py park`, which holds the card `In Review` for the operator
and asks the operator's question; the step posts no hold, no question and no
move of its own there. Without an open record pull request that file is
handed to nobody and nothing is held. Anything else is a decision for the
CEO, parked in `Green Light` as the press is, under its own opening.

A file naming a press AND carrying cause lines parks as a press, with the
cause lines set under the question's three lines, so the dispatcher's return
on the CEO's answer still fires for the press.

The step's script is EXECUTED here the way `tests/test_proof_task_workflow.py`
executes it — its `_StepHarness`, imported — with an argv-recording stub of
`proof_cause_park.py` (the real one's `park` would write) beside the stubbed
`linear_ops.py`. The prompt's cause step is read off every agent arm.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import console_escalation  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_record  # noqa: E402
import test_proof_task_workflow as ptw  # noqa: E402 — the step harness, imported

#: What the hold the press path posts says, and the dispatcher matches.
PRESS = "the CEO's press: Approve the release in the console"
CAUSE_RECEIPT = proof_cause_park.QUESTION_OPENING

CAUSE_LINES = (
    "cause: state-gone — “The release page names v3, read as the proof-reader "
    "identity” — the page answered 404 at 14:02 UTC",
    "cause: out-of-reach — “The CEO closes this card after reading the record” "
    "— the proof identity cannot read who closed a card",
)

#: The three declared lines the prompt asks for, then one line per cause row.
RECOMMENDATION = ("Accept both rows as out of date — the page was retired and the "
                  "close is not the proof identity's to read")
CAUSE_ONLY = (
    f"{console_escalation.FINDING_PREFIX} Two rows of the record cannot be "
    "observed as worded.\n"
    f"{console_escalation.QUESTION_PREFIX} {proof_cause_park.QUESTION}\n"
    f"{console_escalation.RECOMMENDATION_PREFIX} {RECOMMENDATION}\n"
    + "\n".join(CAUSE_LINES) + "\n")

#: The existing press path's escalation, byte for byte the one
#: `test_exit_3_an_escalation_holds_then_asks_then_parks` writes.
PRESS_ONLY = ("Approve the release in the console and say what "
              "you saw, or drop the criterion?\n")

#: The press sentence and the cause rows, sharing the one file.
PRESS_AND_CAUSES = PRESS_ONLY + "\n".join(CAUSE_LINES) + "\n"

#: The card's description with no press criterion at all.
NO_PRESS_DESCRIPTION = "- [ ] The release page names v3\n- [ ] The banner reads v3\n"

PARK_MOVE = ("Green Light", "Hand-work,In Review")

#: The record's pull request, open.
OPEN_PR = "OPEN\thttps://github.example/acme/widgets/pull/9"

#: The writer stub's result line, the lane it read.
RESULT = "left DRE-77 in In Review for the operator"

#: What the step says, and prints, when cause rows meet no open pull request.
NOTHING_HELD = ("proof park: cause rows with no open record pull request for DRE-77 "
                "— nothing held")

#: A business question of the run's own, beside the same cause rows.
DECISION = CAUSE_ONLY.replace(
    proof_cause_park.QUESTION,
    "Keep the weekly report on Saturdays, or move it to Monday?")


def _rows(text: str) -> list:
    return proof_cause_park.rows_from_lines(text.splitlines())


class CauseParkStepTest(ptw._StepHarness):
    """The result step, executed, with a stub `proof_cause_park.py`."""

    STEP = ptw.RESULT_STEP

    def setUp(self):
        super().setUp()
        scripts = self.tmp / ".bureau-pipeline" / "scripts"
        # The writer's argv, recorded: the real module's `park` would import
        # the real `linear_ops` and write.
        (scripts / "proof_cause_park.py").write_text(ptw._PROOF_CAUSE_PARK_STUB)
        (scripts / "console_escalation.py").symlink_to(ROOT / "scripts" / "console_escalation.py")

    def _run(self, escalation: str, *, description: str = ptw.DESCRIPTION,
             pr: str = "", refuse_hold: bool = False, not_advancing: bool = False,
             park_exit: int | None = None) -> list:
        self.escalation.write_text(escalation, encoding="utf-8")
        env = {
            "FAKE_PR": pr,
            "CARD": "DRE-77",
            "CARD_DESCRIPTION": description,
            "RECORD_BRANCH": "agent/DRE-77-proof-record",
            "ESCALATION_FILE": str(self.escalation),
            "BUREAU_SERVER_URL": "https://github.example",
            "BUREAU_REPOSITORY": "acme/widgets",
            "BUREAU_RUN_ID": "42",
            "CLAUDE_EXECUTION_FILE": "",
        }
        if refuse_hold:
            env["FAKE_REFUSE_HOLD"] = "1"
        if not_advancing:
            env["FAKE_NOT_ADVANCING"] = "1"
        if park_exit is not None:
            env["FAKE_PARK_EXIT"] = str(park_exit)
        done, posted, _ = self._exec(env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.stdout = done.stdout
        self.parks = [c for c in posted if c[0] == "proof_cause_park"]
        posted = [c for c in posted if c[0] != "proof_cause_park"]
        for call in posted:
            self.assertEqual(["linear_ops", "DRE-77"], [call[0], call[2]], call)
            if call[1] == "comment":
                self.assertTrue(call[3].rstrip().endswith(f"Run: {ptw.RUN_URL}"), call[3])
        return posted

    def _summary(self) -> str:
        return self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""

    # --- a file of cause rows and no press, the record pull request open --

    def _park_argv(self, *extra) -> list:
        return ["proof_cause_park", "park", "DRE-77", "--escalation-file",
                str(self.escalation), "--run-url", ptw.RUN_URL, *extra]

    def _assert_handed_to_the_writer(self, posted: list) -> None:
        """The writer's call, and of the step's own writes only the pull
        request's report and its In Review move."""
        self.assertEqual([self._park_argv()], self.parks)
        self.assertEqual(["comment", "advance"], [c[1] for c in posted])
        self.assertTrue(posted[0][3].startswith("🤖 PR opened:"), posted[0][3])
        self.assertEqual(("In Review", "Hand-work"), tuple(posted[1][3:]))
        for call in posted:
            self.assertNotEqual("proof-waiting", call[1])
            self.assertFalse(call[-1].startswith("🙋"), call)
        self.assertNotIn("only the CEO can make", self._summary())

    def test_cause_rows_go_to_the_one_writer_and_the_step_writes_none_of_its_own(self):
        posted = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION, pr=OPEN_PR)
        self._assert_handed_to_the_writer(posted)
        self.assertEqual(2, len(_rows(CAUSE_ONLY)))
        # The writer's own result line, quoted, closes the run's log.
        self.assertIn(f"proof result: record pull request, then {RESULT}\n", self.stdout)
        self.assertIn(f"proof result: record pull request, then {RESULT}", self._summary())
        self.assertNotIn("proof park: press", self._summary())
        self.assertNotIn("proof park: decision", self._summary())

    def test_a_card_press_the_file_does_not_name_is_not_a_press(self):
        # ptw.DESCRIPTION marks a press criterion; CAUSE_ONLY never names it.
        self.assertIn("needs the CEO's press:", ptw.DESCRIPTION)
        posted = self._run(CAUSE_ONLY, pr=OPEN_PR)
        self._assert_handed_to_the_writer(posted)
        parked = self.parks
        self.calls.unlink()
        posted_free = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION, pr=OPEN_PR)
        self._assert_handed_to_the_writer(posted_free)
        self.assertEqual(parked, self.parks)

    def test_a_file_that_recommends_nothing_leaves_the_recommendation_to_the_writer(self):
        text = CAUSE_ONLY.replace(
            f"{console_escalation.RECOMMENDATION_PREFIX} {RECOMMENDATION}",
            f"{console_escalation.RECOMMENDATION_PREFIX} none given — the operator decides")
        posted = self._run(text, pr=OPEN_PR)
        # The writer reads the file's Recommendation line itself (DRE-6837).
        self._assert_handed_to_the_writer(posted)
        self.assertNotIn("--recommendation", self.parks[0])

    def test_a_refused_hold_writer_is_never_called_on_cause_rows(self):
        posted = self._run(CAUSE_ONLY, pr=OPEN_PR, refuse_hold=True)
        self._assert_handed_to_the_writer(posted)
        self.assertNotIn("the hold could not be posted", self._summary())

    def test_cause_rows_never_ask_for_the_green_light_move(self):
        posted = self._run(CAUSE_ONLY, pr=OPEN_PR, not_advancing=True)
        self._assert_handed_to_the_writer(posted)
        self.assertNotIn("Green Light", self._summary())

    def test_a_cause_park_that_completed_says_nothing_of_a_refusal(self):
        self._run(CAUSE_ONLY, pr=OPEN_PR)
        self.assertNotIn("did not move", self._summary())
        self.assertNotIn("could not", self._summary())

    def test_a_writer_that_refuses_posts_nothing_and_moves_nothing(self):
        # Exit 1 — a refused write, or a cause line the writer cannot read:
        # never the press or decision park, which would put the operator's
        # question in the CEO's queue. The dispatcher's `_caused` finishes it.
        text = CAUSE_ONLY.replace("cause: state-gone", "cause: vanished")
        with self.assertRaises(ValueError):
            _rows(text)
        posted = self._run(text, pr=OPEN_PR, park_exit=1)
        self._assert_handed_to_the_writer(posted)
        self.assertIn("::warning::", self.stdout)
        self.assertIn("proof park: the cause park could not be completed for DRE-77 — "
                      "the dispatcher's next pass parks the record off its rows",
                      self._summary())
        self.assertNotIn(RESULT, self.stdout)

    def test_a_writer_that_finds_no_cause_row_sends_the_file_to_the_decision(self):
        posted = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION, pr=OPEN_PR,
                           park_exit=2)
        self.assertEqual([self._park_argv()], self.parks)
        self.assertEqual(["comment", "advance", "proof-waiting", "comment", "advance"],
                         [c[1] for c in posted])
        self.assertTrue(posted[3][3].startswith(ptw.DECISION_RECEIPT), posted[3][3])
        self.assertEqual(PARK_MOVE, tuple(posted[4][3:]))
        self.assertIn("proof park: decision", self._summary())

    # --- the same file with no open record pull request --------------------

    def _assert_nothing_held(self, posted: list) -> None:
        self.assertEqual([], self.parks)
        for call in posted:
            self.assertNotIn(call[1], ("proof-waiting", "advance"), call)
            self.assertFalse(call[-1].startswith("🙋"), call)
        self.assertIn("::warning::the cause rows were written with no open record pull "
                      "request for DRE-77; nothing is held — the dispatcher reads this "
                      "run off the card", self.stdout)
        self.assertIn(NOTHING_HELD, self._summary())
        self.assertNotIn("only the CEO can make", self._summary())

    def test_no_pull_request_holds_nothing_and_reports_no_record(self):
        posted = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION, pr="")
        self._assert_nothing_held(posted)
        self.assertEqual(["comment"], [c[1] for c in posted])
        self.assertTrue(posted[0][3].startswith(ptw.NO_RECORD), posted[0][3])
        self.assertNotIn("could not be read", posted[0][3])
        self.assertIn("proof result: no record\n", self.stdout)

    def test_an_unreadable_pull_request_holds_nothing_and_says_it_may_exist(self):
        posted = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION, pr="unreadable")
        self._assert_nothing_held(posted)
        self.assertEqual(["comment"], [c[1] for c in posted])
        self.assertTrue(posted[0][3].startswith(ptw.NO_RECORD), posted[0][3])
        self.assertIn("could not be read, so one may exist", posted[0][3])
        self.assertIn("proof result: no record\n", self.stdout)

    def test_a_merged_record_holds_nothing(self):
        posted = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION,
                           pr="MERGED\thttps://github.example/acme/widgets/pull/9")
        self._assert_nothing_held(posted)
        self.assertEqual(["comment"], [c[1] for c in posted])
        self.assertTrue(posted[0][3].startswith("🤖 PR already merged:"), posted[0][3])
        self.assertIn("proof result: record already merged\n", self.stdout)

    # --- a decision of the run's own beside cause rows ----------------------

    def _assert_decision(self, posted: list) -> str:
        self.assertEqual([], self.parks)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted][-3:])
        hold, ask, park = posted[-3:]
        self.assertTrue(hold[4].startswith(proof_record.CEO_PRESS), hold[4])
        self.assertTrue(ask[3].startswith(ptw.DECISION_RECEIPT), ask[3])
        self.assertNotIn(ptw.PARK_RECEIPT, ask[3])
        self.assertIsNotNone(console_escalation.parse(ask[3]), ask[3])
        lines = ask[3].split("\n")
        last_of_three = max(i for i, l in enumerate(lines)
                            if l.startswith(console_escalation.RECOMMENDATION_PREFIX))
        for line in CAUSE_LINES:
            self.assertEqual(1, lines.count(line), line)
            self.assertGreater(lines.index(line), last_of_three, ask[3])
        self.assertEqual(PARK_MOVE, tuple(park[3:]))
        self.assertIn("proof park: decision", self._summary())
        return ask[3]

    def test_a_business_question_beside_cause_rows_is_the_ceo_s_decision(self):
        ask = self._assert_decision(self._run(DECISION, description=NO_PRESS_DESCRIPTION,
                                              pr=OPEN_PR))
        self.assertIn("Keep the weekly report on Saturdays, or move it to Monday?", ask)
        self.calls.unlink()
        # And with no pull request found: the move out of either lane handles it.
        self._assert_decision(self._run(DECISION, description=NO_PRESS_DESCRIPTION))

    # --- a file naming a press and no cause line --------------------------

    def test_a_press_alone_parks_exactly_as_before(self):
        posted = self._run(PRESS_ONLY)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        hold, ask, park = posted
        self.assertEqual([f"needs {PRESS}", PRESS], hold[3:])
        self.assertTrue(ask[3].startswith(ptw.PARK_RECEIPT), ask[3])
        # The comment the press path has always built, untouched by the cause
        # wiring: the receipt, the file completed, the run.
        completed = console_escalation.complete(
            PRESS_ONLY, question="Make the press and say what you saw, or drop the criterion?",
            who="the proof run")
        self.assertEqual(f"{ptw.PARK_RECEIPT}\n\n{completed}\n\nRun: {ptw.RUN_URL}", ask[3])
        self.assertNotIn(CAUSE_RECEIPT, ask[3])
        self.assertEqual(PARK_MOVE, tuple(park[3:]))
        self.assertIn("proof park: press", self._summary())
        self.assertNotIn("cause rows", self._summary())

    # --- a press and cause rows in the one file ----------------------------

    def _assert_press_with_causes(self, posted: list) -> str:
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        hold, ask, park = posted
        self.assertEqual([f"needs {PRESS}", PRESS], hold[3:])
        self.assertTrue(ask[3].startswith(ptw.PARK_RECEIPT), ask[3])
        self.assertNotIn(CAUSE_RECEIPT, ask[3])
        lines = ask[3].split("\n")
        said = console_escalation.parse(ask[3])
        self.assertIsNotNone(said, ask[3])
        last_of_three = max(i for i, l in enumerate(lines)
                            if l.startswith(console_escalation.RECOMMENDATION_PREFIX))
        for line in CAUSE_LINES:
            self.assertEqual(1, lines.count(line), line)
            self.assertGreater(lines.index(line), last_of_three, ask[3])
        self.assertEqual(PARK_MOVE, tuple(park[3:]))
        self.assertIn("proof park: press", self._summary())
        return ask[3]

    def test_a_press_and_cause_rows_park_as_the_press_with_the_rows_under_it(self):
        ask = self._assert_press_with_causes(self._run(PRESS_AND_CAUSES))
        self.assertIn(PRESS_ONLY.strip(), ask)
        self.assertEqual("", ask.split("\n")[-2], "a blank line before the run")

    def test_a_declared_press_file_keeps_its_rows_under_its_three_lines(self):
        declared = PRESS_ONLY + "\n".join(CAUSE_LINES) + "\n" + console_escalation.render(
            console_escalation.Escalation(
                finding="The release row needs the CEO's press",
                question="Approve the release in the console, or drop the criterion?",
                recommendation="Approve it", why="every other row is met")) + "\n"
        self._assert_press_with_causes(self._run(declared))

    def test_a_file_naming_no_press_the_card_marks_is_a_decision(self):
        # No `needs the CEO's press:` criterion on the card, and the file never
        # says the CEO's press: a decision, held on the file's own first line.
        posted = self._run(PRESS_AND_CAUSES, description=NO_PRESS_DESCRIPTION)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertEqual(f"the CEO's press: {PRESS_ONLY.strip()}", posted[0][4])
        self.assertTrue(posted[1][3].startswith(ptw.DECISION_RECEIPT), posted[1][3])
        self.assertNotIn(ptw.PARK_RECEIPT, posted[1][3])
        self.assertEqual(PARK_MOVE, tuple(posted[2][3:]))
        self.assertEqual([], self.parks)

    def test_three_lines_that_name_the_ceo_s_press_are_a_press(self):
        text = CAUSE_ONLY.replace(
            "Two rows of the record cannot be observed as worded.",
            "The release row needs the CEO's press: approve the release.")
        posted = self._run(text, description=NO_PRESS_DESCRIPTION)
        self.assertIn(proof_record.CEO_PRESS, posted[0][4])
        self.assertTrue(posted[1][3].startswith(ptw.PARK_RECEIPT))
        self.assertIn("proof park: press", self._summary())

    def test_a_record_pull_request_then_a_cause_park(self):
        self.escalation.write_text(CAUSE_ONLY, encoding="utf-8")
        done, posted, _ = self._exec({
            "FAKE_PR": OPEN_PR,
            "CARD": "DRE-77", "CARD_DESCRIPTION": ptw.DESCRIPTION,
            "RECORD_BRANCH": "agent/DRE-77-proof-record",
            "ESCALATION_FILE": str(self.escalation),
            "BUREAU_SERVER_URL": "https://github.example",
            "BUREAU_REPOSITORY": "acme/widgets", "BUREAU_RUN_ID": "42",
            "CLAUDE_EXECUTION_FILE": ""})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(["comment", "advance"],
                         [c[1] for c in posted if c[0] == "linear_ops"])
        self.assertEqual([["proof_cause_park", "park", "DRE-77", "--escalation-file",
                           str(self.escalation), "--run-url", ptw.RUN_URL]],
                         [c for c in posted if c[0] == "proof_cause_park"])
        self.assertEqual([("In Review", "Hand-work")],
                         [tuple(c[3:]) for c in posted if c[1] == "advance"])
        self.assertTrue(done.stdout.rstrip("\n").endswith(
            f"proof result: record pull request, then {RESULT}"), done.stdout)


class CauseParkStepSourceTest(unittest.TestCase):
    """What the step runs, read off its own lines."""

    def _run_text(self) -> str:
        return ptw.step_shell.step_shell(ptw._step(ptw.RESULT_STEP))

    def test_the_one_writer_is_run_for_cause_rows(self):
        run = self._run_text()
        self.assertIn('python3 .bureau-pipeline/scripts/proof_cause_park.py park "$CARD" '
                      '--escalation-file "$ESCALATION_FILE" --run-url "$RUN_URL"', run)
        # The writer owns the hold and the question: the step runs neither half.
        self.assertNotIn("proof_cause_park.py hold", run)
        self.assertNotIn("proof_cause_park.py question", run)
        self.assertIn('linear_ops.py proof-waiting "$CARD" "$OBSERVED" "$NEEDS"', run)

    def test_the_step_quotes_none_of_the_module_s_words(self):
        run = self._run_text()
        for words in (proof_cause_park.QUESTION_OPENING, proof_cause_park.QUESTION,
                      "the operator's decision", "row(s)"):
            self.assertNotIn(words, run)

    def test_one_move_and_the_lane_writes_stay_the_three(self):
        runs = ptw._runs()
        self.assertEqual(3, len(re.findall(r"linear_ops\.py\s+advance\b", runs)))
        # The result step's two: review, and the one park both reasons share.
        self.assertEqual(2, len(re.findall(r"linear_ops\.py\s+advance\b", self._run_text())))

    def test_green_light_rows_check_passes_with_no_new_record(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "green_light_rows.py"),
                               "check"], cwd=ROOT, capture_output=True, text=True,
                              env={"PATH": os.environ["PATH"]}, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("0 problem(s)", done.stdout)
        park_rows = [l for l in done.stdout.splitlines()
                     if l.strip().startswith("proof-task.yml#")]
        self.assertEqual(1, len(park_rows), park_rows)
        self.assertIn(ptw.RESULT_STEP, park_rows[0])


#: The cause step's own phrases, whitespace collapsed.
CAUSE_STEP_PHRASES = (
    "`Not observed. cause: <cause-word> — <what the run saw instead>`",
    "one of the four words briefs/proof.md gives",
    "AFTER the pull request is open, when the record carries at least one such row",
    "write /tmp/agent-escalation.txt once for the card in the three declared lines",
    "`cause: <cause-word> — “<criterion, cut to 70>” — <what was seen instead>`",
    "A press and cause rows share the one file",
    "Never ask twice.",
)


def _prompts() -> list:
    return [" ".join(step["with"]["prompt"].split()) for _i, step in ptw._agent_steps()]


class PromptCauseStepTest(unittest.TestCase):
    """Every arm tells the run to write its cause rows, in the module's grammar."""

    def test_every_arm_carries_the_cause_step_and_the_arms_are_equal(self):
        prompts = _prompts()
        self.assertEqual(3, len(prompts))
        for prompt in prompts:
            for phrase in CAUSE_STEP_PHRASES:
                self.assertIn(phrase, prompt)
        self.assertEqual(1, len(set(prompts)), "the arms differ")

    def test_the_cause_step_follows_the_press_step_and_precedes_the_marker(self):
        prompt = _prompts()[0]
        press = prompt.index("4. A criterion only the CEO's own press can satisfy")
        cause = prompt.index("5. A row you cannot observe as worded")
        marker = prompt.index("6. Last, post the marker")
        self.assertLess(press, cause)
        self.assertLess(cause, marker)

    def test_the_rows_the_prompt_asks_for_are_the_ones_the_module_reads(self):
        prompt = _prompts()[0]
        row = re.search(r"`(Not observed\. cause: <cause-word> — [^`]*)`", prompt).group(1)
        self.assertTrue(row.startswith(proof_record.CAUSE_OPENING), row)
        template = re.search(r"`(cause: <cause-word> — [^`]*)`", prompt).group(1)
        filled = (template.replace("<cause-word>", "state-gone")
                  .replace("<criterion, cut to 70>", "The release page names v3")
                  .replace("<what was seen instead>", "the page answered 404"))
        self.assertEqual([proof_cause_park.CauseRow(
            "The release page names v3", "state-gone", "the page answered 404")],
            proof_cause_park.rows_from_lines([filled]))

    def test_the_phrases_the_arms_test_pins_are_still_there(self):
        for prompt in (step["with"]["prompt"] for _i, step in ptw._agent_steps()):
            for phrase in ("briefs/proof.md", "GH_TOKEN=$GH_READ_TOKEN gh api", "**Files:**",
                           "agent/${{ github.event.client_payload.identifier }}-proof-record",
                           "/tmp/agent-escalation.txt"):
                self.assertIn(phrase, prompt)
            self.assertNotIn("TDD", prompt)


if __name__ == "__main__":
    unittest.main()
