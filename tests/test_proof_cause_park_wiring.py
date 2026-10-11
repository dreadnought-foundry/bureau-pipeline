"""The proof run's park step parks on cause rows too (DRE-6613).

`proof-task.yml`'s `Report proof result to Linear` step parked a card for one
reason: an escalation file naming a press only the CEO can make. Its second
reason is an escalation file carrying `cause:` lines and naming no press, and
for that park the step composes nothing: the hold's two halves come from
`proof_cause_park.py hold` and the question from `proof_cause_park.py
question`, the module that owns the grammar (DRE-6612). The three writes —
the hold, the question, the move — stay the step's own, so the lane contract
gains no record and `green_light_rows.py` reads the step as it did.

A file naming a press AND carrying cause lines parks as a press, with the
cause lines set under the question's three lines, so the dispatcher's return
on the CEO's answer still fires for the press.

The step's script is EXECUTED here the way `tests/test_proof_task_workflow.py`
executes it — its `_StepHarness`, imported — with the real
`proof_cause_park.py` and its siblings beside the stubbed `linear_ops.py`.
The prompt's cause step is read off every agent arm.
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


def _rows(text: str) -> list:
    return proof_cause_park.rows_from_lines(text.splitlines())


class CauseParkStepTest(ptw._StepHarness):
    """The result step, executed, with the real `proof_cause_park.py`."""

    STEP = ptw.RESULT_STEP

    def setUp(self):
        super().setUp()
        scripts = self.tmp / ".bureau-pipeline" / "scripts"
        # A link, so the module's own `Path(__file__).resolve().parent` finds
        # the real `linear_ops`, `proof_reach` and `proof_record` it imports —
        # while the step's shell runs the stubbed `linear_ops.py` beside it.
        (scripts / "proof_cause_park.py").symlink_to(ROOT / "scripts" / "proof_cause_park.py")
        (scripts / "console_escalation.py").symlink_to(ROOT / "scripts" / "console_escalation.py")

    def _run(self, escalation: str, *, description: str = ptw.DESCRIPTION,
             refuse_hold: bool = False, not_advancing: bool = False) -> list:
        self.escalation.write_text(escalation, encoding="utf-8")
        env = {
            "FAKE_PR": "",
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
        done, posted, _ = self._exec(env)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        for call in posted:
            self.assertEqual(["linear_ops", "DRE-77"], [call[0], call[2]], call)
            if call[1] == "comment":
                self.assertTrue(call[3].rstrip().endswith(f"Run: {ptw.RUN_URL}"), call[3])
        return posted

    def _summary(self) -> str:
        return self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""

    # --- a file of cause rows and no press --------------------------------

    def test_cause_rows_hold_then_ask_then_park_through_the_module(self):
        posted = self._run(CAUSE_ONLY)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        hold, ask, park = posted
        rows = _rows(CAUSE_ONLY)
        self.assertEqual(2, len(rows))
        # The module's halves, unchanged by the step.
        self.assertEqual(list(proof_cause_park.hold(rows)), hold[3:])
        self.assertIn("the operator's decision on 2 row(s)", hold[4])
        self.assertNotIn(proof_record.CEO_PRESS, hold[3] + hold[4])
        self.assertEqual(PARK_MOVE, tuple(park[3:]))
        # The module's question, the Recommendation read off the file's own.
        self.assertTrue(ask[3].startswith(
            f"{proof_cause_park.QUESTION_OPENING} 2 row(s) as worded, and asks:"), ask[3])
        self.assertEqual(proof_cause_park.question(
            rows, recommendation=RECOMMENDATION, run_url=ptw.RUN_URL), ask[3])
        lines = ask[3].split("\n")
        for prefix in (console_escalation.FINDING_PREFIX, console_escalation.QUESTION_PREFIX):
            self.assertTrue(any(l.startswith(prefix) for l in lines), prefix)
        self.assertIn(f"{console_escalation.RECOMMENDATION_PREFIX} {RECOMMENDATION}", lines)
        for line in CAUSE_LINES:
            self.assertIn(line, lines)
        self.assertNotIn(ptw.PARK_RECEIPT, ask[3])
        self.assertIn("proof park: cause rows (2)", self._summary())
        self.assertNotIn("proof park: press", self._summary())

    def test_a_card_press_the_file_does_not_name_is_not_a_press(self):
        # ptw.DESCRIPTION marks a press criterion; CAUSE_ONLY never names it.
        self.assertIn("needs the CEO's press:", ptw.DESCRIPTION)
        posted = self._run(CAUSE_ONLY)
        self.assertTrue(posted[1][3].startswith(CAUSE_RECEIPT), posted[1][3])
        self.calls.unlink()
        posted_free = self._run(CAUSE_ONLY, description=NO_PRESS_DESCRIPTION)
        self.assertEqual([c[3:] for c in posted], [c[3:] for c in posted_free])

    def test_a_file_that_recommends_nothing_asks_with_the_module_s_none(self):
        text = CAUSE_ONLY.replace(
            f"{console_escalation.RECOMMENDATION_PREFIX} {RECOMMENDATION}",
            f"{console_escalation.RECOMMENDATION_PREFIX} none given — the operator decides")
        posted = self._run(text)
        self.assertEqual(proof_cause_park.question(_rows(text), run_url=ptw.RUN_URL),
                         posted[1][3])

    def test_a_refused_cause_hold_still_asks_and_parks_and_says_so(self):
        posted = self._run(CAUSE_ONLY, refuse_hold=True)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertTrue(posted[1][3].startswith(CAUSE_RECEIPT))
        self.assertEqual(PARK_MOVE, tuple(posted[2][3:]))
        self.assertIn("proof park: the hold could not be posted for DRE-77", self._summary())

    def test_a_refused_cause_move_leaves_the_hold_and_question_and_says_so(self):
        posted = self._run(CAUSE_ONLY, not_advancing=True)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertIn("the hold and the question are posted, and the park did not "
                      "move DRE-77 to Green Light", self._summary())

    def test_a_cause_park_that_moved_says_nothing_of_a_refusal(self):
        self._run(CAUSE_ONLY)
        self.assertNotIn("did not move", self._summary())
        self.assertNotIn("could not", self._summary())

    def test_cause_lines_the_module_refuses_still_park_as_the_file_reads(self):
        # A word outside the four: the module exits 1, and the park is never
        # lost to it — the file parks the way every escalation parked before.
        text = CAUSE_ONLY.replace("cause: state-gone", "cause: vanished")
        with self.assertRaises(ValueError):
            _rows(text)
        posted = self._run(text)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertTrue(posted[1][3].startswith(ptw.PARK_RECEIPT), posted[1][3])
        self.assertEqual(PARK_MOVE, tuple(posted[2][3:]))
        self.assertIn("proof park: the cause rows could not be read for DRE-77", self._summary())

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

    def test_a_press_the_card_does_not_mark_is_still_a_press(self):
        # No `needs the CEO's press:` criterion on the card: the file's own
        # sentence is the press line, as the press path has always read it.
        posted = self._run(PRESS_AND_CAUSES, description=NO_PRESS_DESCRIPTION)
        self.assertEqual(["proof-waiting", "comment", "advance"], [c[1] for c in posted])
        self.assertEqual(f"the CEO's press: {PRESS_ONLY.strip()}", posted[0][4])
        self.assertTrue(posted[1][3].startswith(ptw.PARK_RECEIPT))

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
            "FAKE_PR": "OPEN\thttps://github.example/acme/widgets/pull/9",
            "CARD": "DRE-77", "CARD_DESCRIPTION": ptw.DESCRIPTION,
            "RECORD_BRANCH": "agent/DRE-77-proof-record",
            "ESCALATION_FILE": str(self.escalation),
            "BUREAU_SERVER_URL": "https://github.example",
            "BUREAU_REPOSITORY": "acme/widgets", "BUREAU_RUN_ID": "42",
            "CLAUDE_EXECUTION_FILE": ""})
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(["comment", "advance", "proof-waiting", "comment", "advance"],
                         [c[1] for c in posted])
        self.assertTrue(posted[3][3].startswith(CAUSE_RECEIPT))
        self.assertEqual([("In Review", "Hand-work"), PARK_MOVE],
                         [tuple(c[3:]) for c in posted if c[1] == "advance"])


class CauseParkStepSourceTest(unittest.TestCase):
    """What the step runs, read off its own lines."""

    def _run_text(self) -> str:
        return ptw.step_shell.step_shell(ptw._step(ptw.RESULT_STEP))

    def test_the_module_is_run_for_the_hold_and_the_question(self):
        run = self._run_text()
        self.assertIn('python3 .bureau-pipeline/scripts/proof_cause_park.py hold '
                      '--escalation-file "$ESCALATION_FILE"', run)
        self.assertIn('python3 .bureau-pipeline/scripts/proof_cause_park.py question '
                      '--escalation-file "$ESCALATION_FILE" --run-url "$RUN_URL"', run)
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
