"""Which press is the CEO's, as the four documents a proof is written from say it (DRE-6842).

Two proof cards reached the CEO's list on 2026-10-10 on a row worded
`needs the CEO's press`: DRE-6638 (one project's Builders set to Sonnet 5.5, on
the sandbox project) and DRE-6663 (Not doing pressed on one groom card).
Neither press was his. The CEO decided on 2026-10-07 that the operator makes
signed-in console presses for proofs without asking, and the sandbox project
exists so a press can be made without touching a real one.

So the rule is written where a proof is written and run: the planner's brief,
the proof brief, the card-quality standard and the run's own prompt in
`proof-task.yml`. A press is the CEO's only when it is a business decision; a
press on the sandbox project or under the operator's standing permission of
2026-10-07 is the operator's row, written in the waiting grammar
`proof_record.row_waiting` already reads. Each test below fails, naming the
file, when a document drops the rule or teaches a grammar the dispatcher does
not read.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
# `reconcile` reads `os.environ["REPO"]` at import, and `proof_dispatch`
# imports it; `test_proof_record.py` sets it the same way.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402 — the operator's-press event the hold is written for
import proof_record  # noqa: E402
from test_american_english_standard import BRITISH  # noqa: E402

PLANNER = ROOT / "briefs" / "planner.md"
PROOF = ROOT / "briefs" / "proof.md"
STANDARD = ROOT / "standards" / "card-quality.md"
PROOF_TASK = ROOT / ".github" / "workflows" / "proof-task.yml"

PROOF_CARD_HEADING = "## Every epic ends with a proof card"
PRESS_HEADING = "## A press only the CEO can make, and his answer"

ACTION = "anthropics/claude-code-action"
ITEM_4 = "4. A criterion only the CEO's own press can satisfy"
ITEM_5 = "5. A row you cannot observe as worded"

CEO_ROW = "needs the CEO's press:"
OPERATOR_ROW = "waiting for the operator's press:"
#: The operator's row as the documents quote it, in full.
OPERATOR_GRAMMAR = "Not observed. waiting for the operator's press: <the press>"

SANDBOX = "the sandbox project"
STANDING = "standing permission of 2026-10-07"
BUSINESS = "a business decision of his"

#: The phrases of item 4 that tests in other files pin (DRE-6842, edit 4).
ITEM_4_PINNED = (
    ITEM_4,
    "Not observed. needs the CEO's press: <the press>",
    "/tmp/agent-escalation.txt",
    "Never ask twice",
)

SANDBOX_PRESS = "set the sandbox project's Builders to Sonnet 5.5"
OPERATOR_CELL = f"Not observed. waiting for the operator's press: {SANDBOX_PRESS}"
DATED_CELL = "Not observed. waiting for the weekly report of 2026-10-18"


def _flat(text: str) -> str:
    """Whitespace collapsed — the documents hard-wrap, and a phrase falling
    across a line break is not a document that stopped saying it."""
    return " ".join(text.split())


def _section(path: Path, heading: str) -> str:
    """From the line opening `heading` to the next `## ` heading of `path`."""
    text = path.read_text(encoding="utf-8")
    found = re.search(rf"^{re.escape(heading)}.*?(?=^## )", text, re.S | re.M)
    assert found, f"{path.name} has no `{heading}` section"
    return found.group(0)


def _prompts() -> list[str]:
    """The prompt of every agent arm, found by the action it uses."""
    doc = yaml.safe_load(PROOF_TASK.read_text(encoding="utf-8"))
    steps = next(iter(doc["jobs"].values()))["steps"]
    return [s["with"]["prompt"] for s in steps
            if str(s.get("uses") or "").split("@")[0] == ACTION]


def _item_4() -> str:
    prompt = _prompts()[0]
    start = prompt.find(ITEM_4)
    end = prompt.find(ITEM_5)
    assert 0 <= start < end, "proof-task.yml's prompt has no item 4 before item 5"
    return prompt[start:end]


#: Each document, by the name a failure gives it, and the passage the rule lives in.
PASSAGES = {
    "briefs/planner.md": lambda: _section(PLANNER, PROOF_CARD_HEADING),
    "briefs/proof.md": lambda: _section(PROOF, PRESS_HEADING),
    "standards/card-quality.md": lambda: _section(STANDARD, PROOF_CARD_HEADING),
    ".github/workflows/proof-task.yml": _item_4,
}

#: The documents that teach the operator's row grammar in full.
TEACHERS = ("briefs/planner.md", "briefs/proof.md", ".github/workflows/proof-task.yml")


class EveryDocumentSaysWhosePressTest(unittest.TestCase):
    def test_the_sandbox_and_the_standing_permission_are_the_operators(self):
        for name, passage in PASSAGES.items():
            text = _flat(passage())
            with self.subTest(name):
                self.assertIn(SANDBOX, text, f"{name} no longer names {SANDBOX}")
                self.assertIn(STANDING, text, f"{name} no longer names the {STANDING}")
                self.assertIn("operator's", text, f"{name} no longer names the operator's row")

    def test_the_ceos_row_is_for_a_business_decision_only(self):
        for name, passage in PASSAGES.items():
            text = _flat(passage())
            with self.subTest(name):
                self.assertIn(CEO_ROW, text, f"{name} no longer quotes `{CEO_ROW}`")
                self.assertIn(BUSINESS, text,
                              f"{name} no longer says the CEO's press is a {BUSINESS}")

    def test_the_operators_row_is_quoted_in_the_waiting_grammar(self):
        for name in TEACHERS:
            text = _flat(PASSAGES[name]())
            with self.subTest(name):
                self.assertIn(f"`{OPERATOR_GRAMMAR}`", text,
                              f"{name} does not quote the operator's row as `{OPERATOR_GRAMMAR}`")

    def test_the_planner_names_the_two_presses_that_were_the_operators(self):
        text = _flat(PASSAGES["briefs/planner.md"]())
        for card in ("DRE-6638", "DRE-6663"):
            self.assertIn(card, text, f"briefs/planner.md no longer names {card}")

    def test_the_prompt_points_at_the_proof_brief_for_whose_press_it_is(self):
        self.assertIn("briefs/proof.md", _flat(_item_4()),
                      "proof-task.yml's item 4 does not name briefs/proof.md")

    def test_the_passages_are_american_english(self):
        for name, passage in PASSAGES.items():
            words = set(re.findall(r"[a-z]+", passage().lower()))
            with self.subTest(name):
                self.assertFalse(words & set(BRITISH),
                                 f"{name}: British spellings {sorted(words & set(BRITISH))}")


class TheGrammarIsTheOneTheDispatcherReadsTest(unittest.TestCase):
    def test_the_operators_row_opens_with_the_waiting_opening(self):
        self.assertTrue(OPERATOR_GRAMMAR.startswith(proof_record.WAITING_OPENING))
        self.assertTrue(OPERATOR_GRAMMAR.startswith(f"Not observed. {OPERATOR_ROW}"))

    def test_a_row_in_the_operator_grammar_is_a_waiting_row_naming_the_press(self):
        self.assertEqual(f"the operator's press: {SANDBOX_PRESS}",
                         proof_record.row_waiting(OPERATOR_CELL))

    def test_the_event_is_the_one_the_dispatcher_writes_the_operators_hold_for(self):
        event = proof_record.row_waiting(
            OPERATOR_GRAMMAR.replace("<the press>", SANDBOX_PRESS))
        self.assertTrue(event.startswith(proof_dispatch.OPERATOR_PRESS), event)

    def test_the_holds_needs_half_opens_with_the_operators_press(self):
        criterion = f"{SANDBOX_PRESS} — {CEO_ROW} {SANDBOX_PRESS}"
        hold = linear_ops.proof_waiting_line(criterion, proof_record.row_waiting(OPERATOR_CELL))
        needs = hold.rsplit(" — needs ", 1)[1]
        self.assertTrue(needs.startswith("the operator's press:"), hold)

    def test_a_dated_events_needs_half_does_not(self):
        hold = linear_ops.proof_waiting_line("The weekly report names v3",
                                             proof_record.row_waiting(DATED_CELL))
        needs = hold.rsplit(" — needs ", 1)[1]
        self.assertEqual("the weekly report of 2026-10-18", needs)
        self.assertFalse(needs.startswith("the operator's press:"), hold)


class TheWorkflowPromptTest(unittest.TestCase):
    def test_the_three_arms_carry_one_prompt(self):
        prompts = _prompts()
        self.assertEqual(3, len(prompts), "an attempt and two rate-limit retries")
        self.assertEqual(1, len(set(prompts)), "the arms' prompts differ")

    def test_item_4_still_carries_the_phrases_other_tests_pin(self):
        text = _item_4()
        for phrase in ITEM_4_PINNED:
            self.assertIn(phrase, _flat(text), f"item 4 lost {phrase!r}")


class ThePressSectionKeepsTheAnswerTest(unittest.TestCase):
    def test_the_answer_rows_are_unchanged(self):
        text = _section(PROOF, PRESS_HEADING)
        self.assertIn('    Met. by the CEO\'s press, his words at <PT>: "<his words>"\n', text)
        self.assertIn("    Dropped by the CEO at <PT>\n", text)


if __name__ == "__main__":
    unittest.main()
