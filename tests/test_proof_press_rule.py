"""Whose press it is, as the documents a proof is written from say it (DRE-6842, DRE-7778).

Two proof cards reached the CEO's list on 2026-10-10 on a row worded
`needs the CEO's press`: DRE-6638 (one project's Builders set to Sonnet 5.5, on
the sandbox project) and DRE-6663 (Not doing pressed on one groom card).
Neither press was his. The CEO decided on 2026-10-07 that the operator makes
signed-in console presses for proofs without asking, and DRE-6842 wrote every
press on the sandbox project as the operator's row.

On 2026-10-22, at 9:40 PM PT, 10 of the 15 open agent-bureau proof records
were waiting on such a press, the oldest since 2026-10-08. That night the CEO
restored his 2026-10-04 intent for DRE-5809 — "make sure you never need me" —
and DRE-7778 wrote it down (epic DRE-7772): a press on the sandbox project
`agent-bureau-demo` that is on the standard's one list is the proof run's own
step, a criterion opening `Sandbox press:`, made as the console proof-reader.
The standard states it once and carries the list. The proof brief and the
planner's brief point to it. Every other press the operator can make keeps the
operator's row, and only a business decision of his keeps the CEO's.

Each test below fails, naming the file, when a document drops the rule, drops
the list or its line, gives a press on the list as the operator's row, or
teaches a grammar the dispatcher does not read.

DRE-7954 (epic DRE-7944) took the operator's row out of the proof run's own
instructions — `briefs/proof.md` and `proof-task.yml`'s prompt. A press
outside the sandbox is an OPERATOR card the proof is blocked by, and the run
reads its result; an older card still wording a person's press as a wait gets
`operator-step-unblocked`, so its record closes with a fix card. Only the CEO's
press stays, for his business decisions. The planner's brief and the standard
keep the operator's row until the sibling card that rewrites them, so the
operator's-row tests below read those two documents only.
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
import plan_proof_reach  # noqa: E402 — the person rows the plan critic reads
import proof_dispatch  # noqa: E402 — the operator's-press event the hold is written for
import proof_record  # noqa: E402
from test_american_english_standard import BRITISH  # noqa: E402

PLANNER = ROOT / "briefs" / "planner.md"
PROOF = ROOT / "briefs" / "proof.md"
STANDARD = ROOT / "standards" / "card-quality.md"
PROOF_TASK = ROOT / ".github" / "workflows" / "proof-task.yml"

PROOF_CARD_HEADING = "## Every epic ends with a proof card"
PRESS_HEADING = "## A press only the CEO can make, and his answer"
TOUCH_HEADING = "## What you may touch"
IDENTITIES_HEADING = "## Identities"

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

#: The criterion prefix the prompt card, the reach card and the plan critic read.
SANDBOX_OPENER = "Sandbox press:"
#: The standard's list line; the bullets under it are the one list.
LIST_LINE = "The sandbox presses, the one list (DRE-7772):"
#: The seven presses the epic asked for, in the epic's order.
ASKED = ("createTask", "snoozeTask", "markTaskDone", "makeTaskCard",
         "answerInitiativeDraftQuestion", "sendProjectAgentMessage",
         "answerDeskPermission")
#: The presses DRE-7776's `sandbox-press dropped:` comment named. On
#: 2026-10-23 it dropped `answerInitiativeDraftQuestion`: the console's owner
#: gate admits only a platform operator it can sign for, so the proof-reader
#: is refused on `agent-bureau-demo` too, and the press is not in agent-bureau's
#: `SANDBOX_PRESS_MUTATIONS`.
DROPPED = ("answerInitiativeDraftQuestion",)
PRESSES = tuple(p for p in ASKED if p not in DROPPED)
#: The plain words of each press, as a document might name one in prose.
PLAIN_WORDS = {
    "createTask": "make a task",
    "snoozeTask": "snooze a task",
    "markTaskDone": "mark a task done",
    "makeTaskCard": "file a task's card",
    "answerInitiativeDraftQuestion": "answer a draft's question",
    "sendProjectAgentMessage": "send a Desk message",
    "answerDeskPermission": "answer a Desk Allow",
}

#: The reach declaration will quote this sentence as its `brief` (the contract).
#: No declaration in `config/` carries it yet: DRE-7779, the epic's reach card,
#: adds the reading, and from then `proof_reach.py check` holds the two equal.
#: Until it lands, this literal is the only copy the brief is held to.
REACH_SENTENCE = (
    "On the sandbox project `agent-bureau-demo`, and only there, the run "
    "presses as the console proof-reader, through agent-bureau's "
    "`scripts/console_proof_reader.py press`.")
#: A listed press the live console refuses — a cause row, never a person's
#: wait (DRE-7954): no person presses in the run's place.
REFUSED_ROW = ("Not observed. cause: out-of-reach — the console refused the "
               "proof-reader: <reason>")
#: The row an older card's person's press gets (DRE-7954): the cause word
#: `proof_reach.causes()` declares for an operator step the proof is not
#: blocked by, so the record closes with a fix card (DRE-7952).
UNBLOCKED = "Not observed. cause: operator-step-unblocked —"
#: The phrases no proof-run instruction may say after DRE-7954, matched
#: case-insensitively with backticks removed and whitespace collapsed. The
#: sibling card that rewrites `briefs/planner.md` and the standard greps all
#: three documents for the same list.
NO_PERSON_WAIT = (
    "waiting for the operator's press",
    "accepted by operator decision",
    "put to the operator",
    "held for the operator",
    "the operator writes the decision",
    "for the operator's decision",
    "holds the card for the operator",
    "the operator runs a proof",
)
#: A shadow run's sandbox row, word for word as the prompt card says it.
SHADOW_ROW = ("Not observed. cause: out-of-reach — a shadow run makes no "
              "sandbox press")
FIXTURE = "SANDBOX FIXTURE (proof DRE-<n>)"

#: The phrases of item 4 that tests in other files pin (DRE-6842, edit 4).
ITEM_4_PINNED = (
    ITEM_4,
    "Not observed. needs the CEO's press: <the press>",
    "/tmp/agent-escalation.txt",
    "Never ask twice",
)

# A sandbox press that is not on the list — a model setting — so under
# DRE-7778 it is still the operator's row, exactly as DRE-6638 was.
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


def _sentences(text: str) -> list[str]:
    """The flattened text cut at each sentence's end."""
    return re.split(r"(?<=[.:])\s+(?=[A-Z`*(])", _flat(text))


def _says_together(text: str, *needles: str) -> bool:
    """One sentence of `text` carries every needle."""
    return any(all(n in s for n in needles) for s in _sentences(text))


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


def _list_bullets(text: str) -> list[str] | None:
    """The lines under the list line, to the first blank line, or None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == LIST_LINE:
            out = []
            for after in lines[i + 1:]:
                if not after.strip():
                    break
                out.append(after)
            return out
    return None


def _operator_rows(text: str) -> list[str]:
    """Each operator's row a document writes: the quoted span that carries
    `waiting for the operator's press:`, or, where the row is not quoted (a
    code line, or prose), the words after it to the clause's end."""
    flat = _flat(text)
    quoted = re.compile(r"`[^`]*`")
    rows = [q for q in quoted.findall(flat) if OPERATOR_ROW in q]
    bare = quoted.sub("``", flat)
    rows += re.findall(re.escape(OPERATOR_ROW) + r"[^.,;`]*", bare)
    return rows


#: Each document, by the name a failure gives it, and the passage the rule lives in.
PASSAGES = {
    "briefs/planner.md": lambda: _section(PLANNER, PROOF_CARD_HEADING),
    "briefs/proof.md": lambda: "\n".join(
        _section(PROOF, h) for h in (TOUCH_HEADING, IDENTITIES_HEADING, PRESS_HEADING)),
    "standards/card-quality.md": lambda: _section(STANDARD, PROOF_CARD_HEADING),
}
BRIEFS = ("briefs/planner.md", "briefs/proof.md")
#: The documents that still teach the operator's row, until the sibling card
#: of DRE-7954 rewrites them. `briefs/proof.md` left the list with DRE-7954.
OPERATOR_PASSAGES = {name: PASSAGES[name]
                     for name in ("briefs/planner.md", "standards/card-quality.md")}


def _plain(text: str) -> str:
    """Lowercased, backticks removed, whitespace collapsed — how the phrase
    list is matched (DRE-7954)."""
    return _flat(text.replace("`", "")).lower()


def _blocks(text: str) -> list[str]:
    """The text cut before each `## ` heading and each paragraph that opens
    with a bold lead (`**…**`) — a block is one rule and its code lines."""
    return re.split(r"(?m)^(?=## |\*\*)", text)


class EveryDocumentSaysWhosePressTest(unittest.TestCase):
    def test_a_press_on_the_list_is_the_runs_opening_sandbox_press(self):
        for name, passage in PASSAGES.items():
            text = _flat(passage())
            with self.subTest(name):
                self.assertIn(f"`{SANDBOX_OPENER}`", text,
                              f"{name} no longer writes a listed press as `{SANDBOX_OPENER}`")
                self.assertIn("console proof-reader", text,
                              f"{name} no longer says the run presses as the console proof-reader")
                self.assertIn("`agent-bureau-demo`", text,
                              f"{name} no longer names the sandbox project `agent-bureau-demo`")

    def test_no_document_gives_a_listed_press_as_the_operators_row(self):
        for name, passage in PASSAGES.items():
            rows = _operator_rows(passage())
            with self.subTest(name):
                for row in rows:
                    for op in PRESSES:
                        self.assertNotIn(op, row,
                                         f"{name} gives `{op}`, a press on the list, "
                                         f"as the operator's row: {row!r}")
                        self.assertNotIn(PLAIN_WORDS[op], row,
                                         f"{name} gives {PLAIN_WORDS[op]!r}, a press on "
                                         f"the list, as the operator's row: {row!r}")
                    self.assertNotIn("DRE-7711", row,
                                     f"{name} gives DRE-7711's press, on the list, as "
                                     f"the operator's row: {row!r}")

    def test_a_sandbox_press_not_on_the_list_is_the_operators_row(self):
        for name, passage in OPERATOR_PASSAGES.items():
            with self.subTest(name):
                self.assertTrue(_says_together(passage(), "not on the list", "operator's"),
                                f"{name} no longer says a sandbox press not on the list "
                                "is the operator's row")
                self.assertIn("DRE-6638", _flat(passage()),
                              f"{name} no longer names DRE-6638, the press not on the list")

    def test_the_operators_row_stays_for_a_press_outside_the_sandbox(self):
        for name, passage in OPERATOR_PASSAGES.items():
            text = _flat(passage())
            with self.subTest(name):
                self.assertTrue(_says_together(passage(), "outside the sandbox", "operator"),
                                f"{name} no longer teaches the operator's row for a press "
                                "outside the sandbox")
                self.assertIn(STANDING, text, f"{name} no longer names the {STANDING}")
                self.assertIn(OPERATOR_ROW, text, f"{name} no longer quotes `{OPERATOR_ROW}`")

    def test_the_ceos_row_is_for_a_business_decision_only(self):
        for name, passage in PASSAGES.items():
            text = _flat(passage())
            with self.subTest(name):
                self.assertIn(CEO_ROW, text, f"{name} no longer quotes `{CEO_ROW}`")
                self.assertIn(BUSINESS, text,
                              f"{name} no longer says the CEO's press is a {BUSINESS}")

    def test_the_operators_row_is_quoted_in_the_waiting_grammar(self):
        """Quoted inline in backticks, or as an indented code line the way
        `briefs/proof.md` quotes its other rows."""
        for name in OPERATOR_PASSAGES:
            if name not in BRIEFS:
                continue
            raw = PASSAGES[name]()
            quoted = (f"`{OPERATOR_GRAMMAR}`" in _flat(raw)
                      or f"\n    {OPERATOR_GRAMMAR}\n" in raw)
            with self.subTest(name):
                self.assertTrue(quoted,
                                f"{name} does not quote the operator's row as `{OPERATOR_GRAMMAR}`")

    def test_the_passages_are_american_english(self):
        for name, passage in PASSAGES.items():
            words = set(re.findall(r"[a-z]+", passage().lower()))
            with self.subTest(name):
                self.assertFalse(words & set(BRITISH),
                                 f"{name}: British spellings {sorted(words & set(BRITISH))}")


class TheStandardStatesItOnceTest(unittest.TestCase):
    NAME = "standards/card-quality.md"

    def setUp(self):
        self.raw = _section(STANDARD, PROOF_CARD_HEADING)
        self.text = _flat(self.raw)

    def test_the_ceos_decision_and_the_command(self):
        for needle in ("2026-10-22", "DRE-7772", "Demo tenant",
                       "`scripts/console_proof_reader.py press`",
                       "**sandbox press**", "`repo:agent-bureau`"):
            self.assertIn(needle, self.text, f"{self.NAME} no longer says {needle!r}")
        self.assertTrue(_says_together(self.raw, "lists the ids", "record"),
                        f"{self.NAME} no longer says the run lists the ids it made in the record")

    def test_the_list_line_carries_the_presses_in_order(self):
        bullets = _list_bullets(self.raw)
        self.assertIsNotNone(bullets, f"{self.NAME} has no line `{LIST_LINE}`")
        ops = []
        for line in bullets:
            found = re.match(r"- `([A-Za-z]+)` — \S", line)
            self.assertTrue(found, f"{self.NAME}: a list bullet is not "
                                   f"'- `<operation>` — <press>': {line!r}")
            ops.append(found.group(1))
        self.assertEqual(list(PRESSES), ops,
                         f"{self.NAME}: the list is not the presses DRE-7776 shipped, in order")
        for op in DROPPED:
            self.assertNotIn(f"`{op}`", "\n".join(bullets),
                             f"{self.NAME} lists `{op}`, which DRE-7776 dropped")

    def test_the_list_is_the_copy_of_agent_bureaus(self):
        for needle in ("`SANDBOX_PRESS_MUTATIONS`", "`console/backend/proof_reader_presses.py`"):
            self.assertIn(needle, self.text, f"{self.NAME} no longer names {needle}")

    def test_a_press_not_on_the_list_is_two_cards_the_proof_is_blocked_by(self):
        for needle in ("`scripts/plan_proof_reach.py`", "`SANDBOX_PRESSES`"):
            self.assertIn(needle, self.text, f"{self.NAME} no longer names {needle}")
        self.assertTrue(_says_together(self.raw, "blocked by both"),
                        f"{self.NAME} no longer says the PROOF is blocked by both cards")

    def test_it_adds_no_numbered_line(self):
        numbered = re.findall(r"^\d+\. ", self.raw, re.M)
        self.assertEqual(5, len(numbered), f"{self.NAME}: the five conditions are five lines")


class TheBriefsPointToTheListTest(unittest.TestCase):
    def test_both_briefs_point_to_the_list_line(self):
        for name in BRIEFS:
            with self.subTest(name):
                self.assertIn(LIST_LINE, _flat(PASSAGES[name]()),
                              f"{name} does not point to the standard's list line")
                self.assertIn("standards/card-quality.md", _flat(PASSAGES[name]()),
                              f"{name} does not name the standard the list lives in")

    def test_neither_brief_restates_the_list(self):
        for name in BRIEFS:
            raw = PASSAGES[name]()
            with self.subTest(name):
                for op in ASKED:
                    self.assertNotRegex(raw, rf"(?m)^\s*- `{op}` —",
                                        f"{name} restates the list's `{op}` bullet")
                self.assertIsNone(_list_bullets(raw) or None,
                                  f"{name} restates the list under its line")


class TheProofBriefTest(unittest.TestCase):
    NAME = "briefs/proof.md"

    def test_the_read_only_sentence_names_the_exception(self):
        touch = _section(PROOF, TOUCH_HEADING)
        opening = _flat(touch.split("\n\n", 1)[0])
        self.assertIn("sandbox presses", opening,
                      f"{self.NAME}'s read-only sentence does not name the sandbox presses")
        self.assertIn("one live write a proof run may make outside the sandbox presses",
                      _flat(touch), f"{self.NAME}'s ration counter is no longer the one "
                                    "live write outside the sandbox presses")

    def test_the_third_exception(self):
        touch = _flat(_section(PROOF, TOUCH_HEADING))
        for needle in ("**sandbox presses**", "`markTaskDone`", FIXTURE,
                       "left on the sandbox", SHADOW_ROW, "DRE-7776", "DRE-7783",
                       "`repo:agent-bureau-demo`"):
            self.assertIn(needle, touch, f"{self.NAME}'s third exception lost {needle!r}")

    def test_the_reach_sentence_is_verbatim(self):
        self.assertIn(_flat(REACH_SENTENCE), _flat(PROOF.read_text(encoding="utf-8")),
                      f"{self.NAME} does not carry the reach sentence verbatim")

    def test_the_console_proof_reader_bullet(self):
        ids = _flat(_section(PROOF, IDENTITIES_HEADING))
        for needle in (_flat(REACH_SENTENCE), "`scripts/console_proof_reader.py refusal-probe`",
                       "`outcome`", "`ids`", "`leak`", "`refused`", "`failed`", "`FORBIDDEN`",
                       "Not observed. cause: out-of-reach — <the command's reason>"):
            self.assertIn(needle, ids, f"{self.NAME}'s console proof-reader bullet lost {needle!r}")

    def test_the_operators_press_rewritten(self):
        press = _section(PROOF, PRESS_HEADING)
        self.assertTrue(_says_together(press, f"`{SANDBOX_OPENER}`", "`Met.`"),
                        f"{self.NAME} no longer says a `{SANDBOX_OPENER}` row reads `Met.`")
        self.assertTrue(_says_together(press, "DRE-6638", "not on the list"),
                        f"{self.NAME} no longer gives DRE-6638's reason: not on the list")
        self.assertTrue(_says_together(press, "DRE-7711", "`createTask`"),
                        f"{self.NAME} no longer names DRE-7711's `createTask` as the run's")
        self.assertIn("DRE-6663", _flat(press), f"{self.NAME} no longer names DRE-6663")

    def test_when_the_console_is_not_ready(self):
        press = _section(PROOF, PRESS_HEADING)
        self.assertIn("**When the console is not ready.**", press,
                      f"{self.NAME} has no 'When the console is not ready' paragraph")
        quoted = (f"`{REFUSED_ROW}`" in _flat(press) or f"\n    {REFUSED_ROW}\n" in press)
        self.assertTrue(quoted, f"{self.NAME} does not quote the console-refused row "
                                f"`{REFUSED_ROW}`")
        self.assertTrue(_says_together(press, "criterion written wrong"),
                        f"{self.NAME} no longer says a press the command refuses is a "
                        "criterion written wrong")


class ThePlannerBriefTest(unittest.TestCase):
    NAME = "briefs/planner.md"

    def setUp(self):
        self.raw = _section(PLANNER, PROOF_CARD_HEADING)
        self.text = _flat(self.raw)

    def test_one_example_criterion_in_the_block_style(self):
        examples = re.findall(
            rf"(?m)^    - \[ \] {re.escape(SANDBOX_OPENER)}.*\n(?:      .*\n)*", self.raw)
        self.assertEqual(1, len(examples),
                         f"{self.NAME} has no one `- [ ] {SANDBOX_OPENER}` example criterion")
        example = _flat(examples[0])
        for needle in ("JSON object", "`outcome`", "`ids`", "`createTask`"):
            self.assertIn(needle, example,
                          f"{self.NAME}'s `{SANDBOX_OPENER}` example does not quote {needle}")

    def test_the_record_quotes_the_json_object_never_a_tagged_line(self):
        self.assertTrue(_says_together(self.raw, "JSON object", "never a tagged line"),
                        f"{self.NAME} no longer says the record quotes the command's JSON object")
        self.assertIn("`config/proof-reach.json`", self.text,
                      f"{self.NAME} no longer says why: the reach file keeps this repo's lines")

    def test_dre_7711_is_a_press_the_run_now_makes(self):
        self.assertIn("DRE-7711", self.text, f"{self.NAME} no longer names DRE-7711")
        self.assertIn("DRE-6663", self.text, f"{self.NAME} no longer names DRE-6663")

    def test_a_press_not_on_the_list_adds_two_cards(self):
        self.assertTrue(_says_together(self.raw, "two cards"),
                        f"{self.NAME} no longer says a plan adds the standard's two cards")

    def test_the_plan_critic_refuses_a_listed_press_on_another_row(self):
        self.assertTrue(_says_together(self.raw, "plan critic refuses", f"`{SANDBOX_OPENER}`")
                        or _says_together(self.raw, "plan critic refuses", "`repo:agent-bureau`"),
                        f"{self.NAME} no longer says the plan critic refuses a listed press "
                        "written on another row")


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

    def test_the_console_refused_row_is_a_cause_row_never_a_wait(self):
        """A listed press the live console refuses waits on nobody (DRE-7954):
        it is an `out-of-reach` cause row, a gap its record closes with."""
        cell = REFUSED_ROW.replace("<reason>", "FORBIDDEN")
        self.assertIsNone(proof_record.row_waiting(cell), cell)
        self.assertEqual("out-of-reach", proof_record.row_cause(cell), cell)

    def test_an_older_cards_persons_press_is_a_cause_row(self):
        cell = f"{UNBLOCKED} no OPERATOR card blocks the proof, and the press was not made"
        self.assertIsNone(proof_record.row_waiting(cell), cell)
        self.assertEqual("operator-step-unblocked", proof_record.row_cause(cell), cell)

    def test_a_met_sandbox_row_and_the_shadow_row_get_no_hold(self):
        self.assertIsNone(proof_record.row_waiting("Met. made task t-1, marked done"))
        self.assertIsNone(proof_record.row_waiting(SHADOW_ROW))

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
    """Only what holds before and after the sibling card that rewrites the
    proof run's own prompt."""

    NAME = ".github/workflows/proof-task.yml"

    def test_the_three_arms_carry_one_prompt(self):
        prompts = _prompts()
        self.assertEqual(3, len(prompts), "an attempt and two rate-limit retries")
        self.assertEqual(1, len(set(prompts)), f"{self.NAME}: the arms' prompts differ")

    def test_item_4_still_carries_the_phrases_other_tests_pin(self):
        text = _flat(_item_4())
        for phrase in ITEM_4_PINNED:
            self.assertIn(phrase, text, f"{self.NAME}: item 4 lost {phrase!r}")

    def test_item_4_says_whose_press_it_is(self):
        text = _flat(_item_4())
        for phrase in (SANDBOX, CEO_ROW, BUSINESS, UNBLOCKED, "briefs/proof.md"):
            self.assertIn(phrase, text, f"{self.NAME}: item 4 no longer says {phrase!r}")

    def test_no_arm_waits_on_the_operators_press(self):
        """DRE-7954: every arm's prompt — the three copies of the press
        passage — gives no press to the operator to wait on."""
        for i, prompt in enumerate(_prompts()):
            with self.subTest(arm=i):
                self.assertFalse("waiting for the operator's press" in _plain(prompt),
                                 f"{self.NAME}: arm {i} still words a press as the operator's wait")

    def test_item_4_reads_an_operator_card_the_proof_is_blocked_by(self):
        text = _flat(_item_4())
        self.assertTrue(_says_together(_item_4(), "outside the sandbox", "OPERATOR card",
                                       "blocked by"),
                        f"{self.NAME}: item 4 does not say a press outside the sandbox is an "
                        "OPERATOR card the proof is blocked by")
        self.assertIn(REFUSED_ROW.split(" — the console")[0], text,
                      f"{self.NAME}: item 4 does not write a refused press as a cause row")


class WhatHasNotLandedIsSaidToBePendingTest(unittest.TestCase):
    """Two sibling cards finish this rule after it merges: DRE-7781 rewrites
    the proof run's own prompt, and DRE-7779 adds the plan critic's check and
    `scripts/plan_proof_reach.py`'s `SANDBOX_PRESSES`. Until each lands, the
    documents name it as pending, never as present."""

    PROMPT_CARD = "DRE-7781"
    REACH_CARD = "DRE-7779"

    def test_the_prompt_no_longer_lags_the_brief(self):
        """DRE-7781 rewrote the prompt, so the brief no longer says the prompt
        wins until it does (DRE-7954)."""
        press = _section(PROOF, PRESS_HEADING)
        self.assertFalse(_says_together(press, self.PROMPT_CARD, "prompt wins"),
                         "briefs/proof.md still says the prompt wins until "
                         f"{self.PROMPT_CARD} rewrites it")
        self.assertNotIn("reached every proof run", _flat(press),
                         "briefs/proof.md still says the rule reached every proof run "
                         "at the merge")

    def test_the_standard_names_the_card_that_adds_the_constant(self):
        reach = (ROOT / "scripts" / "plan_proof_reach.py").read_text(encoding="utf-8")
        if "SANDBOX_PRESSES" in reach:
            self.skipTest(f"{self.REACH_CARD} has landed the constant")
        self.assertTrue(_says_together(_section(STANDARD, PROOF_CARD_HEADING),
                                       "`scripts/plan_proof_reach.py`", self.REACH_CARD),
                        "standards/card-quality.md names `scripts/plan_proof_reach.py`'s "
                        f"`SANDBOX_PRESSES` without the card that adds it, {self.REACH_CARD}")

    def test_the_planner_names_the_card_that_adds_the_check(self):
        self.assertTrue(_says_together(_section(PLANNER, PROOF_CARD_HEADING),
                                       "plan critic refuses", self.REACH_CARD),
                        "briefs/planner.md says the plan critic refuses a listed press "
                        f"without the card that adds the check, {self.REACH_CARD}")


class TheProofBriefNamesNoPersonARowWaitsOnTest(unittest.TestCase):
    """DRE-7954: the proof run's brief names no person a row, a hold or a
    card waits on, but the CEO for a business decision of his."""

    NAME = "briefs/proof.md"

    def setUp(self):
        self.raw = PROOF.read_text(encoding="utf-8")
        self.press = _section(PROOF, PRESS_HEADING)

    def test_none_of_the_phrases_appears(self):
        text = _plain(self.raw)
        for phrase in NO_PERSON_WAIT:
            with self.subTest(phrase):
                self.assertFalse(phrase.lower() in text, f"{self.NAME} still says {phrase!r}")

    def test_the_ceos_press_is_named_only_for_his_business_decisions(self):
        ceo = plan_proof_reach.PERSON_ROWS[1]
        self.assertIn(ceo, _flat(self.press), f"{self.NAME} no longer quotes `{ceo}`")
        blocks = [b for b in _blocks(self.raw) if ceo in _flat(b)]
        self.assertTrue(blocks)
        for block in blocks:
            with self.subTest(block[:60]):
                self.assertIn(block, self.press,
                              f"{self.NAME} quotes `{ceo}` outside the CEO's press section")
                self.assertTrue(BUSINESS in _flat(block)
                                or block.startswith("**When you are dispatched again"),
                                f"{self.NAME} quotes `{ceo}` in a block that is not about "
                                f"{BUSINESS}: {_flat(block)[:120]!r}")
        self.assertTrue(_says_together(self.press, BUSINESS, "Green Light"),
                        f"{self.NAME} no longer says the CEO's business decision goes to "
                        "him in Green Light")

    def test_a_press_outside_the_sandbox_is_an_operator_card(self):
        self.assertTrue(_says_together(self.press, "outside the sandbox", "OPERATOR card",
                                       "blocked by"),
                        f"{self.NAME} does not say a press outside the sandbox is an "
                        "OPERATOR card the proof is blocked by")
        self.assertTrue(_says_together(self.press, "never a row"),
                        f"{self.NAME} does not say such a press is never a row in the record")
        self.assertTrue(_says_together(self.press, "reads", "result"),
                        f"{self.NAME} does not say the run reads the OPERATOR card's result")

    def test_an_older_cards_persons_press_closes_with_a_fix_card(self):
        press = _flat(self.press)
        self.assertIn(UNBLOCKED, press,
                      f"{self.NAME} does not record an older card's person's press "
                      f"as `{UNBLOCKED} …`")
        self.assertIn("DRE-7944", press, f"{self.NAME} no longer names DRE-7944")
        self.assertTrue(_says_together(self.press, "fix card"),
                        f"{self.NAME} does not say that row closes with a fix card")
        self.assertIn(STANDING, press, f"{self.NAME} no longer names the {STANDING}")
        self.assertTrue(_says_together(self.press, "DRE-6638", "not on the list"),
                        f"{self.NAME} no longer gives DRE-6638's reason: not on the list")

    def test_the_holds_are_the_pipelines_own(self):
        flat = _flat(self.raw)
        self.assertTrue(_says_together(self.raw, "`🔬 proof-waiting`", "the pipeline lifts"),
                        f"{self.NAME} does not say the pipeline lifts its own waiting hold")
        self.assertTrue(_says_together(self.raw, "time", "never a person"),
                        f"{self.NAME} does not say a hold waits for a time, never a person")
        self.assertNotIn("once the operator records", flat,
                         f"{self.NAME} still re-runs a waiting row once the operator records it")
        self.assertNotIn("held for an operator", flat,
                         f"{self.NAME} still holds a card for an operator")


class TheIdentitiesBulletKeepsItsParagraphsTest(unittest.TestCase):
    def test_the_sign_in_paragraph_is_its_own_paragraph(self):
        ids = _section(PROOF, IDENTITIES_HEADING)
        self.assertTrue("with that reason.\n\n  Every sign-in you hold was made" in ids,
                        "briefs/proof.md joined the sign-in paragraph to the one above it")


class ThePressSectionKeepsTheAnswerTest(unittest.TestCase):
    def test_the_answer_rows_are_unchanged(self):
        text = _section(PROOF, PRESS_HEADING)
        self.assertIn('    Met. by the CEO\'s press, his words at <PT>: "<his words>"\n', text)
        self.assertIn("    Dropped by the CEO at <PT>\n", text)


if __name__ == "__main__":
    unittest.main()
