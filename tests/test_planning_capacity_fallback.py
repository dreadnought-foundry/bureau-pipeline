"""RED-first: planning falls to Opus 5 the moment Fable is out of capacity (DRE-3970).

What broke. `claude-fable-5-1` refused every planning call on 2026-09-12/13, and
again on 2026-09-14, with one sentence under `is_error: true`:

    You've hit your monthly spend limit. Switch to another model to continue.

The planner's ladder has Opus 5 beneath Fable, but nothing walked down it in the
run that was refused. The classify step read the ladder's top rung directly,
treated the refusal as a transport failure, failed the run, and on the retry
parked the card in the CEO's queue (portico run 34924370626, DRE-3949). The
planner step reached Opus only on the NEXT attempt, after the DRE-3824 death
marker was read (DRE-3693: 1 turn, 646 ms, $0, then a rerun).

CEO decision 2026-09-14: "When it's out of Fable 5, it should just default back
to Opus 5." So:

  A. ONE reader says whether a refusal is a model out of capacity —
     `model_fallback.capacity_refusal`. A spend limit, a usage limit, a rate
     limit, or overload is. A run that did real work is never read as one,
     whatever words are in its text: the turn-cap subtype, more than one turn,
     or any spend vetoes it (the DRE-3499 lesson — the words appear in logs of
     agents that merely READ the standard quoting them).
  B. The classifier, refused for capacity, makes the SAME call once on the next
     rung, and says so on the receipt. A refusal of any other kind makes no
     second call.
  C. The groomer shares that call seam and the same rule.
  D. The selector skips a rung this run has already seen out of capacity, with
     its own reason on the note; plan.yml's Select model feeds it the
     classifier's `fell_from`, so the planner starts on Opus in the same run.
  E. Every planner claude-code-action step carries the ladder's next rung as the
     CLI's `--fallback-model`, computed by the selector, never a literal.
  F. The judgement ladder is Fable-first again — DRE-3969's hotfix is undone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import model_fallback as mf  # noqa: E402
import planning_classify  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
FABLE51 = "claude-fable-5-1"
# The planner ladder's Opus rung — `claude-opus-5-5` since DRE-4836
# (2026-09-25), when the adoption moved it on all three ladders.
OPUS55 = "claude-opus-5-5"
SONNET = "claude-sonnet-4-6"

# The sentence, verbatim, from portico run 34924370626 (2026-09-15 03:17 UTC).
SPEND_LIMIT = ("You've hit your monthly spend limit. Switch to another model to "
               "continue.")
# The CLI envelope that carried it: `is_error` under `subtype: success`, one
# turn, nothing spent, sub-second (DRE-3693's planner record, 2026-09-12).
REFUSAL_RECORD = {"type": "result", "subtype": "success", "is_error": True,
                  "num_turns": 1, "total_cost_usd": 0, "duration_ms": 646,
                  "result": SPEND_LIMIT}


@pytest.fixture(autouse=True)
def _fresh_cache():
    mf.clear_availability_cache()
    yield
    mf.clear_availability_cache()


# --------------------------------------------------------------------------- #
# A. one reader for "out of capacity"                                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", [
    SPEND_LIMIT,
    "You've hit your limit · resets 8:30pm (UTC)",
    "Claude AI usage limit reached",
    'HTTP 429: {"type":"error","error":{"type":"rate_limit_error"}}',
    'HTTP 529: {"type":"error","error":{"type":"overloaded_error"}}',
    # The CLI's own refusal sentences, read out of the installed Claude Code
    # 2.1.271 binary's strings (DRE-3970) — the same wall in other words.
    "You've reached your Fable limit.",
    "You're out of usage credits. Switch to another model",
    "You've hit your team's shared budget. Switch to another model",
    "You've hit your channel's monthly spend limit.",
])
def test_a_capacity_refusal_is_recognised(text):
    assert mf.capacity_refusal(text)


def test_the_real_refusal_record_is_a_capacity_refusal():
    assert mf.capacity_refusal(SPEND_LIMIT, record=REFUSAL_RECORD)


@pytest.mark.parametrize("text", [
    "Not logged in · Please run /login",
    "HTTP 401: invalid x-api-key",
    "HTTP 500: internal server error",
    "the classification call ran past 525s",
    "",
])
def test_other_failures_are_not_capacity(text):
    assert not mf.capacity_refusal(text)


def test_a_long_genuine_failure_is_not_out_of_capacity_whatever_it_says():
    # A run that worked for 51 turns and spent real money did not meet a
    # capacity wall on its first call — even if its text quotes one.
    record = {"subtype": "success", "is_error": True, "num_turns": 51,
              "total_cost_usd": 6.4, "duration_ms": 900_000,
              "result": "I read the standard: " + SPEND_LIMIT}
    assert not mf.capacity_refusal(record["result"], record=record)


def test_the_turn_cap_vetoes_the_words():
    record = {"subtype": "error_max_turns", "is_error": True,
              "result": "Reached maximum number of turns (140). rate_limit_error"}
    assert not mf.capacity_refusal(record["result"], record=record)
    assert not mf.capacity_refusal(
        "Reached maximum number of turns (140) — hit your limit")


def test_any_spend_vetoes_the_words():
    record = dict(REFUSAL_RECORD, total_cost_usd=0.02)
    assert not mf.capacity_refusal(SPEND_LIMIT, record=record)


# --------------------------------------------------------------------------- #
# B. the classifier falls in the same call                                     #
# --------------------------------------------------------------------------- #

def _refusing_on(refused: str, answer: str, *, error=None):
    """A call seam that refuses `refused` and answers every other model."""
    seen: list[str] = []

    def call(model, prompt, **kwargs):
        seen.append(model)
        if model == refused:
            raise error or planning_classify.TransportError(
                "the classification call reported subtype 'success', is_error "
                f"True: {SPEND_LIMIT}", "success")
        return planning_classify.Answer(text=answer, model=model)

    call.seen = seen
    return call


def _one_off_answer() -> str:
    return json.dumps({"shape": "one-off",
                       "why": "one file, one pull request, and no decision in it",
                       "tells": [1, 2], "decision": False})


def _card() -> dict:
    return {"identifier": "DRE-3949", "title": "A small change",
            "description": "Change one file.", "labels": ["repo:portico"],
            "has_children": False}


def test_a_fable_spend_limit_refusal_is_answered_by_opus_in_the_same_classify():
    call = _refusing_on(FABLE51, _one_off_answer())
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51, OPUS55], "one call per rung, Fable then Opus"
    assert not decision.transport, "Opus answered — nothing failed to reach a model"
    assert decision.refusal is None
    assert decision.answered
    assert decision.asked == FABLE51
    assert decision.model == OPUS55
    assert decision.fell_from == FABLE51
    receipt = planning_classify.model_receipt(
        decision.asked, decision.model, because=decision.fell_because)
    assert receipt.startswith("DEGRADED"), receipt
    assert FABLE51 in receipt and OPUS55 in receipt
    assert "out of capacity" in receipt
    assert "model-error:" not in receipt, "a receipt must never read as a death"


def test_the_classifier_step_outputs_carry_fell_from():
    call = _refusing_on(FABLE51, _one_off_answer())
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    pairs = dict(planning_classify._model_pairs(decision))
    assert pairs["fell_from"] == FABLE51
    assert "out of capacity" in pairs["receipt"]


def test_a_clean_classify_carries_no_fell_from():
    call = _refusing_on("nobody", _one_off_answer())
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51]
    assert decision.fell_from is None
    assert dict(planning_classify._model_pairs(decision))["fell_from"] == ""


def test_a_refusal_that_is_not_capacity_makes_no_second_call():
    error = planning_classify.TransportError(
        "the classification call exited 1: Not logged in · Please run /login",
        "exit 1")
    call = _refusing_on(FABLE51, _one_off_answer(), error=error)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51]
    assert decision.transport


def test_a_long_genuine_failure_makes_no_second_call():
    record = {"subtype": "success", "is_error": True, "num_turns": 30,
              "total_cost_usd": 4.2, "result": SPEND_LIMIT}
    error = planning_classify.TransportError(
        f"the classification call reported subtype 'success', is_error True: "
        f"{SPEND_LIMIT}", "success", record=record)
    call = _refusing_on(FABLE51, _one_off_answer(), error=error)
    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert call.seen == [FABLE51]
    assert decision.transport


def test_opus_refused_too_is_still_a_transport_failure_after_one_fall():
    # Two rungs out of capacity: the fall is ONE step, then the DRE-3074
    # requeue budget takes over as before. Never a walk to the bottom in a loop.
    seen = []

    def call(model, prompt, **kwargs):
        seen.append(model)
        raise planning_classify.TransportError(
            f"is_error True: {SPEND_LIMIT}", "success")

    decision = planning_classify.classify(_card(), call=call, model=FABLE51)
    assert seen == [FABLE51, OPUS55]
    assert decision.transport
    assert decision.asked == FABLE51


def test_the_claude_code_transport_hands_the_record_to_the_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")

    class Done:
        returncode = 1
        stdout = json.dumps(REFUSAL_RECORD)
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(planning_classify.TransportError) as caught:
        planning_classify._call_claude_code(FABLE51, "prompt")
    assert caught.value.record is not None
    assert caught.value.record.get("num_turns") == 1
    assert mf.capacity_refusal(str(caught.value), record=caught.value.record)


# --------------------------------------------------------------------------- #
# C. the groomer shares the seam and the rule                                  #
# --------------------------------------------------------------------------- #

def test_the_groomer_falls_to_opus_on_a_fable_capacity_refusal():
    rows = [{"identifier": "DRE-1", "title": "DRE-1 does a thing", "labels": [],
             "priority": 0, "age_days": 1, "body": ""}]
    answer = "DRE-1 | now | it is wanted"
    call = _refusing_on(FABLE51, answer)
    pack = groom_context.pack(now="2026-09-05T12:00:00Z")
    result = groom_judgement.run(rows, pack, call=call, model=FABLE51)
    assert call.seen == [FABLE51, OPUS55]
    assert result.asked == FABLE51
    assert result.answered == OPUS55
    assert result.calls == 2
    assert result.problem is None, result.problem


# --------------------------------------------------------------------------- #
# D. the selector, and plan.yml's Select model                                 #
# --------------------------------------------------------------------------- #

def test_a_rung_out_of_capacity_is_skipped_with_its_own_reason():
    decision = mf.select_with_reasons(
        "planner", probe=lambda m: True, out_of_capacity=[FABLE51])
    assert decision["model"] == OPUS55
    assert decision["degraded"]
    assert decision["skipped"] == [{"model": FABLE51, "reason": mf._SKIP_CAPACITY}]
    note = mf.selection_note(decision)
    assert note.startswith("DEGRADED")
    assert "out of capacity" in note
    assert "died" not in note, "capacity is not a death on this card"
    assert mf.ERROR_MARKER_PREFIX not in note


def test_the_fallback_is_the_rung_below_the_chosen_one():
    assert mf.fallback_for("planner", FABLE51) == OPUS55
    assert mf.fallback_for("planner", OPUS55) == SONNET
    assert mf.fallback_for("planner", SONNET) is None
    assert mf.fallback_for("planner", "gpt-9") is None


def _run_cli(*args):
    env = dict(os.environ)
    env["BUREAU_FAKE_AVAILABLE"] = json.dumps({FABLE51: True, OPUS55: True, SONNET: True})
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "model_fallback.py"),
                           *args], capture_output=True, text=True, env=env)


def test_the_cli_takes_out_of_capacity_and_writes_the_fallback(tmp_path):
    fb = tmp_path / "fallback.txt"
    out = _run_cli("select", "planner", "--out-of-capacity", FABLE51,
                   "--fallback-file", str(fb))
    assert out.stdout.strip() == OPUS55, out.stderr
    assert fb.read_text().strip() == SONNET
    fb2 = tmp_path / "fallback2.txt"
    out = _run_cli("select", "planner", "--out-of-capacity", "",
                   "--fallback-file", str(fb2))
    assert out.stdout.strip() == FABLE51, out.stderr
    assert fb2.read_text().strip() == OPUS55


def _step(name: str) -> dict:
    doc = yaml.safe_load(WF.read_text())
    for job in doc["jobs"].values():
        for s in job.get("steps") or []:
            if (s.get("name") or "") == name:
                return s
    raise AssertionError(f"plan.yml has no step named {name!r}")


def _run_select_step(tmp: Path, *, fell_from: str):
    scripts = tmp / ".bureau-pipeline" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "model_fallback.py", scripts)
    shutil.copytree(ROOT / "config", tmp / ".bureau-pipeline" / "config")
    (scripts / "linear_ops.py").write_text("print(0)\n")
    step = _step("Select model")
    run = step["run"].replace("${{ github.event.client_payload.identifier }}", "DRE-3949")
    out_file = tmp / "gh_output"
    env = dict(os.environ)
    env.update({
        "GITHUB_OUTPUT": str(out_file), "GITHUB_STEP_SUMMARY": str(tmp / "summary"),
        "RUNNER_TEMP": str(tmp), "LINEAR_API_KEY": "stub",
        "FELL_FROM": fell_from,
        "BUREAU_FAKE_AVAILABLE": json.dumps({FABLE51: True, OPUS55: True, SONNET: True}),
    })
    proc = subprocess.run(["bash", "-e", "-c", run], cwd=tmp, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dict(line.split("=", 1) for line in out_file.read_text().splitlines()
                if "=" in line)


def test_select_model_reads_the_classifiers_fell_from():
    env = _step("Select model").get("env") or {}
    assert env.get("FELL_FROM") == "${{ steps.classify.outputs.fell_from }}"


def test_a_classifier_that_fell_off_fable_starts_the_planner_on_opus(tmp_path):
    out = _run_select_step(tmp_path, fell_from=FABLE51)
    assert out["model"] == OPUS55
    assert out["why"].startswith("DEGRADED")
    assert "out of capacity" in out["why"]
    assert out["fallback_arg"] == f"--fallback-model {SONNET}"


def test_a_clean_classify_keeps_the_planner_on_fable(tmp_path):
    out = _run_select_step(tmp_path, fell_from="")
    assert out["model"] == FABLE51
    assert out["fallback_arg"] == f"--fallback-model {OPUS55}"


# --------------------------------------------------------------------------- #
# E. every planner step carries the fallback                                   #
# --------------------------------------------------------------------------- #

PLANNER_STEPS = (
    "Plan epic",
    "Re-plan after send-back",
    "Re-plan after the second critic sent it back",
    "Wave route — write the wave plan",
)


@pytest.mark.parametrize("name", PLANNER_STEPS)
def test_every_planner_step_carries_the_selected_fallback(name):
    args = _step(name)["with"]["claude_args"]
    assert "--model ${{ steps.model.outputs.model }}" in args
    assert "${{ steps.model.outputs.fallback_arg }}" in args
    assert "--fallback-model claude-" not in args, "computed, never a literal"


# --------------------------------------------------------------------------- #
# F. Fable-first again                                                         #
# --------------------------------------------------------------------------- #

def test_the_judgement_ladder_is_fable_first_again():
    assert mf.ladder_for("planner") == [FABLE51, OPUS55, SONNET]
    assert FABLE51 not in mf.CONFIG["excluded"]
    assert mf.policy_errors(yaml.safe_load(
        (ROOT / "config" / "models.yaml").read_text())) == []
