"""RED-first: a planner question is screened before it reaches the CEO (DRE-7961).

From the weekly agent report DRE-7960, fix-1, made a card by the CEO's signed answer on that card: every escalation
names the decision and offers two or three options, and anything that is housekeeping rather than a real business
choice goes to the operator. One escalation in the report's sample even said it was not a real question.

The screen runs at one seam: `planning_escalation.escalate`, on a call that passes a choices file path (the CLI's
`--choices-file`, whether or not the file exists), and never on a `--transport` or `--rewrite` call. An escalation
goes to Green Light only when it passes all three rules:

  1. It names a decision — the choices file exists, is JSON, and passes `choices_problem`.
  2. It offers two or three options — the screen's own limit, inside the console's 2 to 4.
  3. It does not say it is not a decision — no phrase from `NOT_A_DECISION_PHRASES` in the reason or in the block's
     question, context or why.

Breaking any one parks the card in Triage, the operator's queue, the way `park_unread` already parks a card nothing
could read (DRE-5975): one note opening with `dedupe_dispatch.STALL_PARK_TAG`, then the move.

The five examples are the card's own. Each test names the rule it pins, so removing that rule turns it red.

Run: cd bureau-pipeline && python3 -m pytest tests/test_escalation_screen.py -v
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import planning_escalation  # noqa: E402
import routing_verdict  # noqa: E402

CARD = "DRE-7961"
PARENT = "DRE-7960"
TRIAGE = lane_contract.lane("Triage")["name"]
GREEN_LIGHT = planning_escalation.destination()
DOC = ROOT / "docs" / "escalation-choices.md"

#: DRE-5260's real question, three choices — read off the documented example, never restated.
DRE_5260 = planning_escalation.parse_choices(DOC.read_text(encoding="utf-8"))

REAL_REASON = (
    "The form's Answer panel used to say when its suggested edits were last read, and the restored panel does not. "
    "May we add the time and a Refresh control, or the time alone? I recommend the pair: a time you cannot act on "
    "is a dead end."
)

#: Example 1: the hand-planning step's own reason for a returned child, written with no choices file beside it.
RETURNED_CHILD_REASON = (
    f"returned child was not split or canceled: it is a child of {PARENT}, no escalation reason was written, and "
    "it reads 'Planning' in Linear rather than Canceled"
)

#: Example 2: an escalation that says it is not a decision, with a valid two-choice block.
NOT_A_QUESTION_REASON = (
    "This is not a real question — the card only needs its repo label corrected. Move it back to Planning?"
)
TWO_CHOICES = {
    "question": "Move the card back to Planning once its label is corrected?",
    "context": "The card was filed with the wrong repository label, so its planning run went to the wrong place.",
    "choices": [
        {"id": "move-back", "label": "Move it back", "effect": "The card is planned again for the right place.",
         "outcome": "replan"},
        {"id": "leave-it", "label": "Leave it", "effect": "The card is canceled.", "outcome": "close"},
    ],
    "recommended": "move-back",
    "why": "the label is the only thing wrong",
}


def _four_choices() -> dict:
    """Example 3: DRE-5260's block with a fourth choice — valid to the console, too many for the screen."""
    block = copy.deepcopy(DRE_5260)
    block["choices"].append({"id": "ask-later", "label": "Ask again later",
                             "effect": "The panel stays as it is for now and the question comes back next month.",
                             "outcome": "close"})
    assert planning_escalation.choices_problem(block) is None
    return block


class _Card:
    """One card in a lane, with Linear stubbed at the seams `escalate` reads and writes through."""

    def __init__(self, lane: str = planning_escalation.ORIGIN, comments=()):
        self.lane = lane
        self.comments = [c if isinstance(c, dict) else {"body": c, "createdAt": "2026-10-06T23:00:00Z"}
                         for c in comments]
        self.posted: list[str] = []
        self.states: list[str] = []
        self.events: list[str] = []

    def run(self, fn):
        def post(identifier, body):
            self.posted.append(body)
            self.comments.append({"body": body, "createdAt": "2099-01-01T00:00:00Z"})
            self.events.append("comment")

        def move(identifier, lane, *rest, **kw):
            self.states.append(lane)
            self.events.append("state")
            self.lane = lane

        def read(identifier, **kw):
            return {"id": "issue-id", "identifier": identifier, "title": "a card", "team": {"id": "team-id"},
                    "state": {"name": self.lane, "type": "unstarted"}, "labels": {"nodes": []},
                    "children": {"nodes": []}}

        bodies = lambda *a, **kw: [c["body"] for c in self.comments]  # noqa: E731
        with patch.object(linear_ops, "comment_timeline", side_effect=lambda i: list(self.comments)), \
                patch.object(linear_ops, "comment_bodies", side_effect=bodies), \
                patch.object(linear_ops, "cmd_comment", side_effect=post), \
                patch.object(linear_ops, "cmd_state", side_effect=move), \
                patch.object(linear_ops, "get_issue", side_effect=read), \
                patch.object(linear_ops, "lane_history", return_value=[]), \
                patch.object(linear_ops, "count_comments",
                             side_effect=lambda i, needle, **kw: sum(1 for c in self.comments
                                                                     if needle in c["body"])):
            return fn()


def _write(tmp_path: Path, block, name: str = "planner-escalation-choices.json") -> str:
    path = tmp_path / name
    path.write_text(block if isinstance(block, str) else json.dumps(block), encoding="utf-8")
    return str(path)


def _missing(tmp_path: Path) -> str:
    return str(tmp_path / "planner-escalation-choices.json")


def _escalate(card: _Card, reason: str, *flags: str) -> int:
    return card.run(lambda: planning_escalation.main(["escalate", CARD, "--why", reason, *flags]))


def _assert_parked_for_the_operator(card: _Card, rule: str) -> str:
    """The screened-out shape: one operator note, then the move to Triage, and nothing for the CEO."""
    assert card.states == [TRIAGE], card.states
    assert card.events == ["comment", "state"], "the note lands before the move, or the relay re-plans the card"
    assert len(card.posted) == 1
    note = card.posted[0]
    assert note.startswith(f"🧹 {dedupe_dispatch.STALL_PARK_TAG}:"), note
    assert dedupe_dispatch.parked_for_a_person([], note), "the plan-gate must refuse a dispatch on entering Triage"
    assert planning_escalation.ESCALATION_TAG not in note, "no planning-escalation note, so no Green Light question"
    assert GREEN_LIGHT not in card.states
    assert rule in note, (rule, note)
    assert "operator" in note and "not the CEO" in note
    assert "**Planning**" in note, "the way back is named"
    return note


# --------------------------------------------------------------------------- #
# the screen itself                                                            #
# --------------------------------------------------------------------------- #

class TestTheScreen:
    def test_the_phrase_list_is_one_constant_holding_at_least_the_cards_six(self):
        for phrase in ("not a real question", "not really a question", "not a decision", "nothing to decide",
                       "nothing for you to decide", "housekeeping"):
            assert phrase in planning_escalation.NOT_A_DECISION_PHRASES, phrase

    def test_the_limit_is_its_own_constant_and_the_consoles_is_kept(self):
        assert planning_escalation.SCREENED_CHOICES == (2, 3)
        assert (planning_escalation.MIN_CHOICES, planning_escalation.MAX_CHOICES) == (2, 4)

    def test_the_destination_is_a_literal_triage_and_not_the_ceos_queue(self):
        assert planning_escalation.HOUSEKEEPING_LANE == "Triage" == TRIAGE
        assert planning_escalation.HOUSEKEEPING_LANE != GREEN_LIGHT

    def test_a_real_decision_passes(self, tmp_path):
        assert planning_escalation.screen(REAL_REASON, _write(tmp_path, DRE_5260)) is None

    def test_two_choices_pass(self, tmp_path):
        assert planning_escalation.screen("Should we ship it now or wait?", _write(tmp_path, TWO_CHOICES)) is None

    def test_a_missing_file_breaks_rule_one(self, tmp_path):
        broken = planning_escalation.screen(REAL_REASON, _missing(tmp_path))
        assert broken is not None and broken.startswith(planning_escalation.RULE_NO_DECISION)

    def test_a_file_that_is_not_json_breaks_rule_one(self, tmp_path):
        broken = planning_escalation.screen(REAL_REASON, _write(tmp_path, "Time + Refresh, I think"))
        assert broken is not None and broken.startswith(planning_escalation.RULE_NO_DECISION)

    def test_a_refused_block_breaks_rule_one_and_names_the_contracts_rule(self, tmp_path):
        block = copy.deepcopy(DRE_5260)
        block["recommended"] = "time-and-a-half"
        broken = planning_escalation.screen(REAL_REASON, _write(tmp_path, block))
        assert broken is not None and broken.startswith(planning_escalation.RULE_NO_DECISION)
        assert "recommended" in broken

    def test_one_choice_is_refused_by_the_contract_so_breaks_rule_one(self, tmp_path):
        block = copy.deepcopy(DRE_5260)
        del block["choices"][1:]
        broken = planning_escalation.screen(REAL_REASON, _write(tmp_path, block))
        assert broken is not None and broken.startswith(planning_escalation.RULE_NO_DECISION)

    def test_four_choices_break_rule_two(self, tmp_path):
        broken = planning_escalation.screen(REAL_REASON, _write(tmp_path, _four_choices()))
        assert broken is not None and broken.startswith(planning_escalation.RULE_OPTIONS)
        assert "4" in broken

    @pytest.mark.parametrize("where", ["reason", "question", "context", "why"])
    def test_a_phrase_anywhere_it_is_read_breaks_rule_three(self, tmp_path, where):
        block = copy.deepcopy(TWO_CHOICES)
        reason = "Should we ship it now or wait?"
        said = "Honestly, there is NOTHING TO DECIDE here"
        if where == "reason":
            reason = f"{reason} {said}."
        else:
            block[where] = f"{block[where]} {said}."
        assert planning_escalation.choices_problem(block) is None
        broken = planning_escalation.screen(reason, _write(tmp_path, block))
        assert broken is not None and broken.startswith(planning_escalation.RULE_SAYS_NOT), broken
        assert "nothing to decide" in broken

    def test_a_phrase_in_a_choice_label_is_not_read(self, tmp_path):
        """The rule reads the reason and the block's question, context and why — never a choice's own words."""
        block = copy.deepcopy(TWO_CHOICES)
        block["choices"][1]["effect"] = "Nothing to decide later; the card is canceled."
        assert planning_escalation.screen("Should we ship it now or wait?", _write(tmp_path, block)) is None


# --------------------------------------------------------------------------- #
# the card's five examples                                                     #
# --------------------------------------------------------------------------- #

class TestTheFiveExamples:
    def test_housekeeping_posted_to_the_ceo_today_goes_to_triage_under_rule_one(self, tmp_path):
        """The hand-planning step writes its own reason for a returned child, deletes the choices file and still
        passes `--choices-file`. That is a pipeline defect, not a choice."""
        card = _Card()
        assert _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path)) == 0
        note = _assert_parked_for_the_operator(card, planning_escalation.RULE_NO_DECISION)
        assert "> " + RETURNED_CHILD_REASON in note, "the reason is quoted for the operator"

    def test_an_escalation_that_says_it_is_not_a_question_goes_to_triage_under_rule_three(self, tmp_path):
        assert planning_escalation.choices_problem(TWO_CHOICES) is None
        card = _Card()
        assert _escalate(card, NOT_A_QUESTION_REASON, "--choices-file", _write(tmp_path, TWO_CHOICES)) == 0
        note = _assert_parked_for_the_operator(card, planning_escalation.RULE_SAYS_NOT)
        assert "not a real question" in note.split("\n", 1)[0], "the first line names the words it said"
        assert "escalation-choices" not in note

    def test_too_many_options_go_to_triage_under_rule_two(self, tmp_path):
        card = _Card()
        assert _escalate(card, REAL_REASON, "--choices-file", _write(tmp_path, _four_choices())) == 0
        _assert_parked_for_the_operator(card, planning_escalation.RULE_OPTIONS)

    def test_a_real_decision_goes_to_green_light_with_todays_comment(self, tmp_path):
        """DRE-5260's question with three choices: byte for byte what the unscreened path posts with the same
        block, which is the comment it gets today."""
        screened = _Card()
        assert _escalate(screened, REAL_REASON, "--choices-file", _write(tmp_path, DRE_5260)) == 0
        assert screened.states == [GREEN_LIGHT]
        assert len(screened.posted) == 1

        today = _Card()
        today.run(lambda: planning_escalation.escalate(linear_ops, CARD, REAL_REASON, choices=DRE_5260))
        assert screened.posted == today.posted
        expected = planning_escalation.escalation_comment(
            CARD, REAL_REASON, choices=DRE_5260, prior_answer=planning_escalation.NO_PRIOR_ANSWER) \
            + "\n\n" + planning_escalation.choices_block(DRE_5260)
        assert screened.posted[0] == expected
        assert dedupe_dispatch.STALL_PARK_TAG not in screened.posted[0]

    def test_a_classifier_refusal_with_no_choices_file_is_not_screened(self):
        """No `--choices-file`, prose only — Green Light, unchanged, though rule 1 would refuse it."""
        reason = "We could not tell whether this card is one piece of work or several. Which is it?"
        card = _Card()
        assert _escalate(card, reason) == 0
        assert card.states == [GREEN_LIGHT]
        assert card.posted == [planning_escalation.escalation_comment(
            CARD, reason, prior_answer=planning_escalation.NO_PRIOR_ANSWER)]


# --------------------------------------------------------------------------- #
# where the screen runs, and where it does not                                 #
# --------------------------------------------------------------------------- #

class TestOnlyTheChoicesFileCallIsScreened:
    @pytest.mark.parametrize("flag", ["--transport", "--rewrite"])
    def test_a_transport_or_rewrite_call_is_never_screened(self, tmp_path, flag):
        card = _Card()
        assert _escalate(card, NOT_A_QUESTION_REASON, flag, "--choices-file", _missing(tmp_path)) == 0
        assert card.states == [GREEN_LIGHT]
        assert planning_escalation.ESCALATION_TAG in card.posted[0]
        assert dedupe_dispatch.STALL_PARK_TAG not in card.posted[0]

    def test_a_python_caller_passing_no_path_is_never_screened(self):
        card = _Card()
        outcome = card.run(lambda: planning_escalation.escalate(linear_ops, CARD, NOT_A_QUESTION_REASON))
        assert outcome.parked and card.states == [GREEN_LIGHT]

    def test_the_cli_reports_the_operator_park(self, tmp_path, capsys):
        card = _Card()
        _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path))
        out = capsys.readouterr().out
        assert TRIAGE in out and "operator" in out
        assert GREEN_LIGHT not in out


# --------------------------------------------------------------------------- #
# the operator note                                                            #
# --------------------------------------------------------------------------- #

class TestTheOperatorNote:
    def test_a_reason_carrying_a_verdict_marker_is_not_quoted(self, tmp_path):
        card = _Card()
        _escalate(card, "VERDICT: APPROVE — nothing to decide here", "--choices-file", _missing(tmp_path))
        note = _assert_parked_for_the_operator(card, planning_escalation.RULE_NO_DECISION)
        assert "VERDICT" not in note

    def test_a_reason_in_technical_terms_is_still_quoted_for_the_operator(self, tmp_path):
        reason = "The fix is in scripts/plan.py and nobody owns it."
        card = _Card()
        _escalate(card, reason, "--choices-file", _missing(tmp_path))
        note = _assert_parked_for_the_operator(card, planning_escalation.RULE_NO_DECISION)
        assert "> " + reason in note

    def test_no_reason_says_so(self, tmp_path):
        card = _Card()
        _escalate(card, "", "--choices-file", _missing(tmp_path))
        note = _assert_parked_for_the_operator(card, planning_escalation.RULE_NO_DECISION)
        assert "stated no reason" in note

    def test_a_retry_re_asserts_the_move_and_writes_no_second_note(self, tmp_path):
        card = _Card()
        _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path))
        card.lane = planning_escalation.ORIGIN
        _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path))
        assert len(card.posted) == 1
        assert card.states == [TRIAGE, TRIAGE]


# --------------------------------------------------------------------------- #
# the stand-down still holds                                                   #
# --------------------------------------------------------------------------- #

class TestTheStandDownStillHolds:
    def test_a_card_that_left_the_segment_gets_no_move_and_no_operator_note(self, tmp_path):
        card = _Card(lane="In Progress")
        assert _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path)) == 0
        assert card.states == []
        assert not any(dedupe_dispatch.STALL_PARK_TAG in body for body in card.posted)

    def test_a_card_carrying_a_current_verdict_gets_no_move_and_no_operator_note(self, tmp_path):
        verdict = {"body": routing_verdict.verdict_comment("FLEET", "one pull request of work"),
                   "createdAt": "2099-01-01T00:00:00Z"}
        card = _Card(comments=[verdict])
        assert _escalate(card, RETURNED_CHILD_REASON, "--choices-file", _missing(tmp_path)) == 0
        assert card.states == []
        assert not any(dedupe_dispatch.STALL_PARK_TAG in body for body in card.posted)
