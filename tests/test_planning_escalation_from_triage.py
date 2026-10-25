"""RED-first tests: a planning escalation can leave from Triage (DRE-7178).

`planning_escalation.escalate` reads the card's live lane and asks `moved_on`
whether the card is still the escalation's to move. `moved_on` reads any lane
outside `ORIGIN`'s segment (Intake, Planning, Green Light) as moved on, and
Triage is in `off-flow`, so on a card in Triage `escalate` posted a stand-down
note telling the CEO nothing was needed, wrote no state and returned
`parked=False`: the Triage repair's question never reached Green Light.

`from_lane` names the one lane outside the segment the caller is moving the
card FROM. Only `triage_repair.py` passes it, always `"Triage"`. Every other
caller passes nothing and reads exactly as before — `test_planning_escalation.py`
and `test_planning_escalation_prior_answer.py` pin that, unchanged.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planning_escalation_from_triage.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import planning_escalation  # noqa: E402
from triage_repair_fakes import TriageRepairBoard  # noqa: E402

CARD = "DRE-9001"
QUESTION = (
    "Finding: the card asks for both a nightly report and a weekly one, and "
    "only one of them can be built.\n"
    "Question: Should the report go out every night, or once a week?\n"
    "Recommendation: once a week — the readers asked for fewer emails, and a "
    "weekly report is cheaper to run."
)


def _issue(lane: str) -> dict:
    return {"identifier": CARD, "state": {"name": lane}}


def _board(lane: str = "Triage") -> TriageRepairBoard:
    board = TriageRepairBoard()
    board.add(CARD, lane=lane, labels=["repo:agent-bureau"])
    return board


def _escalation_notes(board) -> list[str]:
    return [b for b in board.bodies(CARD) if planning_escalation.ESCALATION_TAG in b]


def _stand_downs(board) -> list[str]:
    return [b for b in board.bodies(CARD) if planning_escalation.STOOD_DOWN_TAG in b]


class TestTheKeyword:
    def test_both_take_from_lane_and_default_it_to_none(self):
        for fn in (planning_escalation.moved_on, planning_escalation.escalate):
            param = inspect.signature(fn).parameters["from_lane"]
            assert param.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__
            assert param.default is None, fn.__name__

    def test_stood_down_comment_takes_it_too(self):
        param = inspect.signature(planning_escalation.stood_down_comment).parameters
        assert param["from_lane"].default is None


class TestMovedOn:
    def test_without_from_lane_triage_is_moved_on_as_today(self):
        assert planning_escalation.moved_on(_issue("Triage"), []) == "it is in Triage"

    def test_from_lane_triage_keeps_a_triage_card_the_escalations(self):
        assert planning_escalation.moved_on(_issue("Triage"), [],
                                            from_lane="Triage") is None

    def test_from_lane_never_excuses_another_lane_outside_the_segment(self):
        assert planning_escalation.moved_on(
            _issue("In Progress"), [], from_lane="Triage") == "it is in In Progress"

    def test_the_segment_still_reads_as_ours_with_from_lane(self):
        assert planning_escalation.moved_on(_issue("Planning"), [],
                                            from_lane="Triage") is None

    def test_the_routing_verdict_fact_is_unchanged_by_from_lane(self):
        verdict = "🧭 routing-verdict: **FLEET** — Buildable unattended in one pull request."
        said = planning_escalation.moved_on(_issue("Triage"), [verdict],
                                            from_lane="Triage")
        assert said is not None and "routing verdict" in said
        assert "it is in" not in said


class TestStoodDownComment:
    def test_names_from_lane_where_it_names_origin(self):
        note = planning_escalation.stood_down_comment(
            CARD, "it is in In Progress", QUESTION, from_lane="Triage")
        assert "moved on from Triage" in note
        assert f"moved on from {planning_escalation.ORIGIN}" not in note

    def test_without_from_lane_it_names_origin_as_today(self):
        note = planning_escalation.stood_down_comment(
            CARD, "it is in In Progress", QUESTION)
        assert f"moved on from {planning_escalation.ORIGIN}" in note


class TestEscalateFromTriage:
    def test_without_from_lane_a_triage_card_stands_down_and_no_state_is_written(self):
        board = _board("Triage")
        with board.patched():
            outcome = planning_escalation.escalate(linear_ops, CARD, QUESTION)
        assert outcome.parked is False
        assert outcome.stood_down == "it is in Triage"
        assert board.lane(CARD) == "Triage"
        assert [kind for kind, _ in board.writes(CARD)] == ["comment"]
        assert len(_stand_downs(board)) == 1 and not _escalation_notes(board)

    def test_with_from_lane_triage_the_card_parks_in_green_light(self):
        board = _board("Triage")
        with board.patched():
            outcome = planning_escalation.escalate(linear_ops, CARD, QUESTION,
                                                   from_lane="Triage")
        assert outcome.parked is True and outcome.stood_down is None
        assert board.lane(CARD) == planning_escalation.destination() == "Green Light"
        assert len(_escalation_notes(board)) == 1 and not _stand_downs(board)
        # The note lands before the move, as on every other route.
        assert [kind for kind, _ in board.writes(CARD)] == ["comment", "state"]

    def test_with_from_lane_a_card_moved_elsewhere_still_stands_down(self):
        board = _board("In Progress")
        with board.patched():
            outcome = planning_escalation.escalate(linear_ops, CARD, QUESTION,
                                                   from_lane="Triage")
        assert outcome.parked is False
        assert outcome.stood_down == "it is in In Progress"
        assert board.lane(CARD) == "In Progress"
        notes = _stand_downs(board)
        assert len(notes) == 1 and "moved on from Triage" in notes[0]
        assert not [kind for kind, _ in board.writes(CARD) if kind == "state"]
