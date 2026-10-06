"""RED-first: a classifier call that ends at the turn limit with no answer is asked once on the next rung (DRE-5979).

What broke. From about 20:15 PT on 2026-10-05 the planning classifier's one-turn, no-tool Claude Code call ended,
on nearly every card, with

    planning classify: the transport failed: the classification call reported subtype 'error_max_turns',
    is_error True: None

(DRE-5202's run 37491599406; DRE-5948, DRE-5949, DRE-5958 and DRE-5959 the same). Each card was parked in Green
Light as plumbing, so one-off cards never reached the critic. The same card's real prompt classified correctly from
the operator's machine on Claude Code 2.1.290 and 2.1.291, on `claude-fable-5-1`, and Fable planned fine in the
same CI. The run kept nothing of what the CLI streamed, so the cause could not be read from it.

So:

  A. A call that ends `error_max_turns` within two turns and carries no result text is a call that gave no answer.
     The classifier's own call has no tools and does no work, so it is asked once on the next rung, in the same
     step, exactly as a capacity refusal is (DRE-3970). It is NOT a capacity refusal: `capacity_refusal` keeps
     vetoing the turn cap, because for an agent the turn cap means real work. So it sets no `fell_from` (the
     planner still starts on Fable, which plans fine), and the receipt says the asked model "gave no answer".
  B. A turn-limit end that carries an answer, or that ran more than two turns, makes no second call.
  C. Every transport error from an error envelope carries a bounded `stream:` summary of what the CLI wrote, so
     the next failure names its cause in the run log.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402
import planning_classify  # noqa: E402

FABLE51 = "claude-fable-5-1"
OPUS55 = "claude-opus-5-5"

# The envelope shape the failing CI runs reported: the turn cap, an error, and no result text.
NO_ANSWER_RECORD = {"type": "result", "subtype": "error_max_turns", "is_error": True,
                    "num_turns": 2, "result": None}


@pytest.fixture(autouse=True)
def _fresh_cache():
    mf.clear_availability_cache()
    yield
    mf.clear_availability_cache()


def _card() -> dict:
    return {"identifier": "DRE-5202", "title": "the credential liveness probe keeps taking an account's usage",
            "description": "Change one file.", "labels": ["repo:agent-bureau"], "has_children": False}


def _one_off_answer() -> str:
    return json.dumps({"shape": "one-off", "why": "one file, one pull request, and no decision in it",
                       "tells": [1, 2], "decision": False})


def _no_answer_on(refused: str, *, record=None):
    """A call seam that ends `refused` at the turn limit with no answer, and answers every other model."""
    seen: list[str] = []
    rec = dict(NO_ANSWER_RECORD) if record is None else record

    def call(model, prompt, **kwargs):
        seen.append(model)
        if model == refused:
            raise planning_classify.TransportError(
                "the classification call reported subtype 'error_max_turns', is_error True: None",
                "error_max_turns", record=rec)
        return planning_classify.Answer(text=_one_off_answer(), model=model)

    call.seen = seen
    return call


# --------------------------------------------------------------------------- #
# A. no answer within the turn limit falls once to the next rung               #
# --------------------------------------------------------------------------- #

def test_a_turn_limit_end_with_no_answer_is_answered_by_the_next_rung():
    call = _no_answer_on(FABLE51)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51, OPUS55], "one call per rung, Fable then Opus"
    assert not decision.transport
    assert decision.refusal is None
    assert decision.answered
    assert decision.asked == FABLE51
    assert decision.model == OPUS55


def test_it_is_not_a_capacity_refusal_so_the_planner_keeps_fable():
    call = _no_answer_on(FABLE51)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert decision.fell_from is None, "the planner plans fine on Fable; only a capacity refusal demotes it"
    pairs = dict(planning_classify._model_pairs(decision))
    assert pairs["fell_from"] == ""
    receipt = pairs["receipt"]
    assert receipt.startswith("DEGRADED"), receipt
    assert "gave no answer" in receipt
    assert "out of capacity" not in receipt
    assert "model-error:" not in receipt


def test_the_turn_cap_is_still_not_capacity_for_an_agent():
    assert not mf.capacity_refusal("the classification call reported subtype 'error_max_turns'",
                                   record=NO_ANSWER_RECORD)


def test_the_helper_reads_the_record():
    err = planning_classify.TransportError("x", "error_max_turns", record=NO_ANSWER_RECORD)
    assert planning_classify.turn_limit_without_answer(err)
    assert not planning_classify.turn_limit_without_answer(
        planning_classify.TransportError("x", "exit 1"))


# --------------------------------------------------------------------------- #
# B. an answer, or real work, makes no second call                             #
# --------------------------------------------------------------------------- #

def test_a_turn_limit_end_that_carries_an_answer_makes_no_second_call():
    record = dict(NO_ANSWER_RECORD, result='{"shape": "one-off"}')
    call = _no_answer_on(FABLE51, record=record)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51]
    assert decision.transport


def test_a_long_run_that_hit_the_cap_makes_no_second_call():
    record = dict(NO_ANSWER_RECORD, num_turns=12)
    call = _no_answer_on(FABLE51, record=record)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51]
    assert decision.transport


def test_both_rungs_without_an_answer_is_still_one_fall_then_a_transport_failure():
    seen = []

    def call(model, prompt, **kwargs):
        seen.append(model)
        raise planning_classify.TransportError("no answer", "error_max_turns", record=dict(NO_ANSWER_RECORD))

    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert seen == [FABLE51, OPUS55]
    assert decision.transport


# --------------------------------------------------------------------------- #
# C. the failure says what the CLI streamed                                    #
# --------------------------------------------------------------------------- #

def _stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events)


def test_an_error_envelope_names_the_stream_in_its_message(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    stdout = _stream(
        {"type": "system", "subtype": "init", "model": FABLE51, "permissionMode": "auto",
         "tools": ["Read", "Bash", "Grep"], "mcp_servers": []},
        {"type": "assistant", "message": {"id": "m1", "stop_reason": "tool_use",
                                          "content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        dict(NO_ANSWER_RECORD, permission_denials=[{"tool_name": "Read"}]),
    )

    class Done:
        returncode = 1
        stderr = "something on stderr"

    Done.stdout = stdout
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(planning_classify.TransportError) as caught:
        planning_classify._call_claude_code(FABLE51, "prompt")
    message = str(caught.value)
    assert "error_max_turns" in message
    assert "stream:" in message
    assert "permissionMode=auto" in message
    assert "tools=3" in message
    assert "tool_use=['Read']" in message
    assert "num_turns=2" in message
    assert "denials=1" in message
    assert len(message) < 1600, "bounded: it rides a log line and a receipt"
    assert caught.value.record is not None, "the record still reaches the fallback reader"
