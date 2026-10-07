"""RED-first: a card the classifier could not read goes to Triage, never to the CEO's Green Light (DRE-5975).

What happened. On 2026-10-05 and 10-06 the planning classifier's call ended, on card after card, with the CLI's
`error_max_turns` and no answer (DRE-5883, 5958, 5959, 5960, 5951, 5202, 3698). The classifier logged it as "the
transport failed", wrote a reason saying it "could not reach its model", spent its one retry, and then parked the card
in **Green Light** through the hand-planning escalation. On the board each one read "a question for you — the agent
could not decide this on its own", and in the side panel Approve was greyed out, because there was nothing to approve.
The CEO hit three of them on 10-06 and could do nothing with any of them.

DRE-5979 (bureau-pipeline #759) made most of those calls answer, by asking the next rung. This card is the rest:

  1. NAMED FOR WHAT IT WAS. A call that came back from the model with a result envelope did reach the model. Its log
     line and its reason say the model gave no classification and name the CLI's subtype, and never say "transport" or
     "could not reach". A call that really never reached a model (a 429, a CLI that did not start) keeps its words.
  2. TRIAGE, NOT GREEN LIGHT. A card still unread after its one retry carries no recommendation and no question, so it
     goes to Triage, the operator's queue, by `planning_escalation.park_unread`. Its note opens with the stall exit's
     tag, `dedupe_dispatch.STALL_PARK_TAG`, because that is what the plan-gate honors so the relay does not re-plan a
     card the moment it enters Triage (DRE-5277); it posts no `planning-escalation`, and it moves nothing when the card
     has already left Planning.
  3. THE WORKFLOW ROUTES BY IT. plan.yml's CEO-park step no longer runs for a transport park; a separate step runs
     `park-unread` for it. And the first failure's note stops promising the CEO a question.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import planning_classify  # noqa: E402
import planning_escalation  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
CARD = "DRE-5960"
TRIAGE = lane_contract.lane("Triage")["name"]

#: The envelope the failing CI runs reported (DRE-5960's run 37420973830).
NO_ANSWER_RECORD = {"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 2,
                    "result": None}


def _no_answer_error() -> planning_classify.TransportError:
    return planning_classify.TransportError(
        "the classification call reported subtype 'error_max_turns', is_error True: None",
        "error_max_turns", record=dict(NO_ANSWER_RECORD))


# --------------------------------------------------------------------------- #
# 1. named for what it was                                                     #
# --------------------------------------------------------------------------- #

def test_a_model_that_answered_without_a_classification_is_not_called_a_transport_failure(capsys):
    reason = planning_classify._transport(_no_answer_error())
    log = capsys.readouterr().err
    assert "error_max_turns" in log
    assert "transport failed" not in log
    assert "error_max_turns" in reason
    assert "could not reach" not in reason
    assert "transport" not in reason.lower()
    assert planning_escalation.refusal(reason) is None, planning_escalation.refusal(reason)


def test_a_call_that_never_reached_a_model_keeps_its_words(capsys):
    reason = planning_classify._transport(planning_classify.TransportError("HTTP 429: busy", "HTTP 429"))
    assert "transport failed" in capsys.readouterr().err
    assert "could not reach its model" in reason and "429" in reason


def test_a_fall_after_no_answer_logs_what_the_cli_streamed(capsys):
    """Since #759 the next rung answers, so the run never reaches `_transport` and the `stream:` summary DRE-5979
    put in the error never reached a log: six falls on agent-bureau on 10-06 and not one cause recorded. The fall's
    own line carries it."""
    def call(model, prompt, **kwargs):
        if model == "claude-fable-5-1":
            raise planning_classify.TransportError(
                "the classification call reported subtype 'error_max_turns', is_error True: None"
                " | stream: events=system/initx1,resultx1; permissionMode=auto tools=26",
                "error_max_turns", record=dict(NO_ANSWER_RECORD))
        return planning_classify.Answer(text="{}", model=model)

    planning_classify.call_with_capacity_fallback(call, "claude-fable-5-1", "prompt")
    log = capsys.readouterr().err
    assert "gave no answer" in log
    assert "stream: events=system/initx1" in log and "tools=26" in log


# --------------------------------------------------------------------------- #
# 2. Triage, not Green Light                                                   #
# --------------------------------------------------------------------------- #

class _Card:
    """One card in a lane, with writes recorded rather than posted."""

    def __init__(self, lane: str = planning_escalation.ORIGIN, comments=()):
        self.lane = lane
        self.comments = list(comments)
        self.posted: list[str] = []
        self.states: list[str] = []
        self.events: list[str] = []

    def run(self, fn):
        def post(identifier, body):
            self.posted.append(body)
            self.comments.append(body)
            self.events.append("comment")

        def move(identifier, lane, *rest, **kw):
            self.states.append(lane)
            self.events.append("state")
            self.lane = lane

        def read(identifier, **kw):
            return {"id": "issue-id", "identifier": identifier, "title": "a card", "team": {"id": "team-id"},
                    "state": {"name": self.lane, "type": "unstarted"}, "labels": {"nodes": []},
                    "children": {"nodes": []}}

        def timeline(identifier):
            return [{"body": b, "createdAt": "2026-10-06T23:00:00Z"} for b in self.comments]

        with patch.object(linear_ops, "comment_timeline", side_effect=timeline), \
                patch.object(linear_ops, "comment_bodies", side_effect=lambda i, **kw: list(self.comments)), \
                patch.object(linear_ops, "cmd_comment", side_effect=post), \
                patch.object(linear_ops, "cmd_state", side_effect=move), \
                patch.object(linear_ops, "get_issue", side_effect=read), \
                patch.object(linear_ops, "count_comments",
                             side_effect=lambda i, needle, **kw: sum(1 for b in self.comments if needle in b)):
            return fn()


REASON = planning_escalation.no_answer_reason("error_max_turns") if hasattr(
    planning_escalation, "no_answer_reason") else "the classifier's model gave no classification"


def _park(card: _Card, *extra: str) -> int:
    return card.run(lambda: planning_escalation.main(["park-unread", CARD, "--why", REASON, *extra]))


def test_an_unread_card_parks_in_triage_not_green_light():
    card = _Card()
    assert _park(card) == 0
    assert card.states == [TRIAGE]
    assert TRIAGE != planning_escalation.destination(), "Triage is not the CEO's decision queue"


def test_the_note_lands_first_and_carries_the_tag_the_plan_gate_honors():
    card = _Card()
    _park(card)
    assert card.events == ["comment", "state"], "the note lands before the move, or the relay re-plans the card"
    note = card.posted[0]
    assert dedupe_dispatch.STALL_PARK_TAG in note
    assert dedupe_dispatch.parked_for_a_person([], note), "the plan-gate must refuse a dispatch on entering Triage"


def test_the_note_is_for_the_operator_and_asks_the_ceo_nothing():
    card = _Card()
    _park(card)
    note = card.posted[0]
    assert planning_escalation.ESCALATION_TAG not in note, "no planning-escalation, so no Green Light question"
    assert "Triage" in note and "Planning" in note, "it says where it is and the way back"
    assert "error_max_turns" in note
    assert planning_escalation.jargon(note) == ()


def test_a_retry_re_asserts_the_move_and_writes_no_second_note():
    card = _Card()
    _park(card)
    card.lane = planning_escalation.ORIGIN
    _park(card)
    assert len(card.posted) == 1
    assert card.states == [TRIAGE, TRIAGE]


def test_a_card_that_already_left_planning_is_left_alone():
    card = _Card(lane="In Progress")
    assert _park(card) == 0
    assert card.states == []
    assert card.posted == []


# --------------------------------------------------------------------------- #
# 3. the workflow routes by it                                                 #
# --------------------------------------------------------------------------- #

def _steps() -> list:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["plan"]["steps"]


def test_the_ceo_park_step_never_runs_for_a_transport_failure():
    step = next(s for s in _steps() if "planning_escalation.py escalate" in (s.get("run") or "")
                and "classifier-escalation" in (s.get("run") or ""))
    assert "steps.classify.outputs.transport != 'true'" in step["if"]
    assert "--transport" not in step["run"]


def test_a_transport_failure_that_outlived_its_retry_runs_park_unread():
    step = next(s for s in _steps() if "planning_escalation.py park-unread" in (s.get("run") or ""))
    assert "steps.classify.outputs.escalate == 'true'" in step["if"]
    assert "steps.classify.outputs.transport == 'true'" in step["if"]
    assert "classifier-escalation" in step["run"]


def test_the_first_failure_note_no_longer_promises_the_ceo_a_question():
    note = planning_escalation.transport_comment(CARD, "HTTP 429")
    assert "comes to you as a question" not in note
    assert "Triage" in note


def test_the_first_failure_note_of_a_call_the_model_answered_never_says_it_could_not_reach_it():
    """The headline sits above the reason, so a headline saying "could not reach its model" over an `error_max_turns`
    reason contradicted itself on every one of the seven cards from 10-05/06. The headline says only what both kinds
    share; the reason says which it was, and the budget tag is untouched."""
    note = planning_escalation.transport_comment(CARD, planning_escalation.no_answer_reason("error_max_turns"))
    assert "could not reach" not in note
    assert "error_max_turns" in note
    assert note.startswith(f"{planning_escalation.TRANSPORT_MARK} {planning_escalation.TRANSPORT_TAG}: {CARD} ")


def test_the_first_failure_note_of_a_429_still_says_the_model_was_unreachable():
    note = planning_escalation.transport_comment(CARD, planning_classify._transport(
        planning_classify.TransportError("HTTP 429: busy", "HTTP 429")))
    assert "could not reach its model" in note and "429" in note


def test_the_requeue_step_log_does_not_claim_the_model_was_unreachable():
    step = next(s for s in _steps() if "planning_escalation.py requeue" in (s.get("run") or ""))
    error = next(line for line in step["run"].splitlines() if "::error::" in line)
    assert "could not reach" not in error
    assert "retried once" in error
