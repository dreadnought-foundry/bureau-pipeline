"""RED-first: a classifier call that ends at the turn limit with no answer is asked once on the next rung (DRE-5979).

What broke. From about 20:15 PT on 2026-10-05 the planning classifier's one-turn, no-tool Claude Code call ended,
on nearly every card, with

    planning classify: the transport failed: the classification call reported subtype 'error_max_turns',
    is_error True: None

(DRE-5202's run 37491599406; DRE-5948, DRE-5949, DRE-5958 and DRE-5959 the same). Each card was parked in Green
Light as plumbing, so one-off cards never reached the critic. The same card's real prompt classified correctly from
the operator's machine on Claude Code 2.1.290 and 2.1.291, on Fable 5.1, and Fable planned fine in the
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

These are rules of the classifier, so they run against the frozen model files (DRE-6885): `planning_classify` and the
selector it falls through are imported from a `model_fixture.fixture_tree` built with `regenerate=False`, the selector
on the frozen home document (`model_fixture.fixture_home()`, through `fixture_module(..., doc=...)`, DRE-6990), and the
rungs a test expects are the first and second of the planner's group in that document, never a literal. A console save
that moves the planner or changes its group leaves them alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import model_fixture  # noqa: E402

# The model files a console save rewrites; everything else the classifier reads is no save's to change.
_MODEL_FILES = ("models.yaml", "model-prices.yaml", "model-choices.json")


def _classifier_tree(dest: Path) -> Path:
    """Today's scripts on the frozen model files, with what else the classifier reads — the lane contract and shape
    vocabulary under `config/`, and the brief and standard its prompt is cut from — as committed."""
    shutil.copytree(ROOT / "config", dest / "config", ignore=shutil.ignore_patterns(*_MODEL_FILES))
    for name in ("briefs", "standards"):
        shutil.copytree(ROOT / name, dest / name)
    return model_fixture.fixture_tree(dest, regenerate=False)


HOME = model_fixture.fixture_home()

# The classifier, and the selector it imported, are the tree's; the selector is on the frozen document.
_TREE = tempfile.TemporaryDirectory()
planning_classify = model_fixture.fixture_module(_classifier_tree(Path(_TREE.name) / "tree"), "planning_classify",
                                                 doc=HOME)
mf = planning_classify.model_fallback

# The planner's walk on the frozen document, named for its place: the rung a call is asked on, and the one below it.
_PLANNER = model_fixture.ladder(HOME, HOME["agents"]["planner"])
TOP, NEXT = _PLANNER[0], _PLANNER[1]

# The envelope shape the failing CI runs reported: the turn cap, an error, and no result text.
NO_ANSWER_RECORD = {"type": "result", "subtype": "error_max_turns", "is_error": True,
                    "num_turns": 2, "result": None}


@pytest.fixture(autouse=True)
def _fresh_cache():
    mf.clear_availability_cache()
    yield
    mf.clear_availability_cache()


def teardown_module():
    _TREE.cleanup()


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

def test_the_classifier_s_selector_walks_the_document():
    assert mf.HOME_STATUS == "read", "the selector is not on the document"
    assert mf.ladder_for("planner") == _PLANNER


def test_a_turn_limit_end_with_no_answer_is_answered_by_the_next_rung():
    call = _no_answer_on(TOP)
    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert call.seen == [TOP, NEXT], "one call per rung, the top rung then the one below it"
    assert not decision.transport
    assert decision.refusal is None
    assert decision.answered
    assert decision.asked == TOP
    assert decision.model == NEXT


def test_it_is_not_a_capacity_refusal_so_the_planner_keeps_its_top_rung():
    call = _no_answer_on(TOP)
    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert decision.fell_from is None, "the planner plans fine on its top rung; only a capacity refusal demotes it"
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
    call = _no_answer_on(TOP, record=record)
    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert call.seen == [TOP]
    assert decision.transport


def test_a_long_run_that_hit_the_cap_makes_no_second_call():
    record = dict(NO_ANSWER_RECORD, num_turns=12)
    call = _no_answer_on(TOP, record=record)
    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert call.seen == [TOP]
    assert decision.transport


def test_both_rungs_without_an_answer_is_still_one_fall_then_a_transport_failure():
    seen = []

    def call(model, prompt, **kwargs):
        seen.append(model)
        raise planning_classify.TransportError("no answer", "error_max_turns", record=dict(NO_ANSWER_RECORD))

    decision = planning_classify.classify(_card(), call=call, model=TOP)
    assert seen == [TOP, NEXT]
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
        {"type": "system", "subtype": "init", "model": TOP, "permissionMode": "auto",
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
        planning_classify._call_claude_code(TOP, "prompt")
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
