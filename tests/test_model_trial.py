"""RED-first tests for DRE-5121 — a model trial checks STRENGTH, not just an
answer.

On 2026-09-28 `model-trial.yml` passed Claude Sonnet 5.5 on the pinned Claude
Code 2.1.282 (run 36479502126: `claude-sonnet-5-5: passed — completed, 3 turns,
7s`). The answer was right and nothing died, so by the rules DRE-3897 wrote the
trial passed. But 2.1.282 has no entry for the model, and ran it on its
unknown-model defaults: a 200K context window and a 32K output cap, against
the 1M and 128K the Models API lists. An adoption on that evidence would have
put every reviewer on a fifth of its context with nothing failing.

So the scoring step now reads two more numbers out of the run itself — the
`contextWindow` and `maxOutputTokens` Claude Code recorded for the model in the
execution record's `modelUsage` — and compares them with the Models API's
`max_input_tokens` and `max_tokens`, which a step before it writes to
`$RUNNER_TEMP/model-limits.json` (`claude_code_pin.py limits`). A run below
either is `outcome=degraded`: never `passed`, and its summary names both
numbers. A run whose strength cannot be read at all is `failed`, with the
reason named — absence is not health, the rule this workflow already applies
to a missing execution record.

This file EXECUTES the verify step's `run:` block, as
tests/test_model_trial_workflow.py does, against the fixture the card names.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_trial.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRIAL = ROOT / ".github" / "workflows" / "model-trial.yml"

MODEL = "claude-sonnet-5-5"
CONFIG_FILE = "config/models.yaml"
ANSWER_FILE = "trial-answer.txt"

CONFIG = """\
ladders:
  workhorse:
    - model: a-model
  advisory:
    - model: b-model
"""
ANSWER = "ladders=2\n"

# The Models API's numbers for the model (the card: "1M and 128K").
API_LIMITS = {"model": MODEL, "max_input_tokens": 1_000_000, "max_tokens": 128_000}


def _record(context_window, max_output) -> dict:
    """A clean run's result record, billed to the model at the given limits —
    the shape Claude Code writes (`modelUsage.<model>.contextWindow`)."""
    usage = {"inputTokens": 1200, "outputTokens": 40, "costUSD": 0.01}
    if context_window is not None:
        usage["contextWindow"] = context_window
    if max_output is not None:
        usage["maxOutputTokens"] = max_output
    return {"type": "result", "is_error": False, "subtype": "success",
            "num_turns": 3, "duration_ms": 7000, "total_cost_usd": 0.01,
            "modelUsage": {MODEL: usage}}


# The card's fixture: 2.1.282 ran Sonnet 5.5 at 200K / 32K.
DEGRADED_RUN = _record(200_000, 32_000)
FULL_RUN = _record(1_000_000, 128_000)


def _doc() -> dict:
    return yaml.safe_load(TRIAL.read_text())


def _steps() -> list:
    return [s for job in _doc()["jobs"].values() for s in job.get("steps") or []]


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step {step_id!r} in {TRIAL.name}")


def _score(tmp_path: Path, execution, *, limits=API_LIMITS, answer=ANSWER,
           claude_outcome="success"):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / CONFIG_FILE).write_text(CONFIG)
    link = tmp_path / ".bureau-pipeline"
    if not link.exists():
        link.symlink_to(ROOT)
    if answer is not None:
        (tmp_path / ANSWER_FILE).write_text(answer)
    exec_file = tmp_path / "execution.json"
    if execution is not None:
        # The whole message list, ending with the result record — the shape
        # the vendor action writes.
        exec_file.write_text(json.dumps([{"type": "system", "subtype": "init"},
                                         execution]))
    limits_file = tmp_path / "model-limits.json"
    if limits is not None:
        limits_file.write_text(json.dumps(limits))

    script = tmp_path / "verify.sh"
    script.write_text(str(_step("verify")["run"]))
    out = tmp_path / "github_output"
    out.write_text("")
    step_summary = tmp_path / "step_summary"
    step_summary.write_text("")
    env = dict(os.environ)
    env.update({
        "MODEL": MODEL,
        "CLAUDE_OUTCOME": claude_outcome,
        "CLAUDE_EXECUTION_FILE": str(exec_file),
        "ANSWER_FILE": ANSWER_FILE,
        "CONFIG_FILE": CONFIG_FILE,
        "LIMITS_FILE": str(limits_file),
        "BUREAU_SERVER_URL": "https://github.com",
        "BUREAU_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
        "BUREAU_RUN_ID": "36479502126",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(step_summary),
        "RUNNER_TEMP": str(tmp_path),
    })
    proc = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines()
                   if "=" in line)
    return outputs, step_summary.read_text()


# --------------------------------------------------------------------------- #
# 1. The card's fixture                                                        #
# --------------------------------------------------------------------------- #

def test_a_run_at_200k_and_32k_is_degraded_never_passed(tmp_path):
    out, _ = _score(tmp_path, DEGRADED_RUN)
    assert out["outcome"] == "degraded"
    assert out["outcome"] != "passed"


def test_the_degraded_summary_names_both_numbers_on_both_sides(tmp_path):
    out, _ = _score(tmp_path, DEGRADED_RUN)
    summary = out["summary"]
    assert summary.startswith(f"{MODEL}: degraded — ")
    for number in ("200,000", "1,000,000", "32,000", "128,000"):
        assert number in summary, (number, summary)
    assert "\n" not in summary
    assert "3 turns" in summary and "7s" in summary


def test_the_same_run_at_full_limits_passes(tmp_path):
    out, _ = _score(tmp_path, FULL_RUN)
    assert out["outcome"] == "passed"
    assert out["summary"].startswith(f"{MODEL}: passed — completed,")


@pytest.mark.parametrize("context_window,max_output", [
    (200_000, 128_000),   # the window alone is short
    (1_000_000, 32_000),  # the output cap alone is short
])
def test_below_either_limit_is_degraded(tmp_path, context_window, max_output):
    out, _ = _score(tmp_path, _record(context_window, max_output))
    assert out["outcome"] == "degraded"


def test_the_step_summary_records_the_strength_either_way(tmp_path):
    _, summary = _score(tmp_path, FULL_RUN)
    assert "1,000,000" in summary and "128,000" in summary


# --------------------------------------------------------------------------- #
# 2. What cannot be read is not full strength                                  #
# --------------------------------------------------------------------------- #

def test_no_models_api_limits_is_failed_and_named(tmp_path):
    out, _ = _score(tmp_path, FULL_RUN, limits=None)
    assert out["outcome"] == "failed"
    assert "full strength" in out["summary"]


def test_limits_the_api_step_could_not_read_are_failed(tmp_path):
    out, _ = _score(tmp_path, FULL_RUN,
                    limits={"model": MODEL, "unknown": "the Models API returned 529"})
    assert out["outcome"] == "failed"
    assert "529" in out["summary"]


def test_a_run_that_recorded_no_limits_is_failed(tmp_path):
    out, _ = _score(tmp_path, _record(None, None))
    assert out["outcome"] == "failed"
    assert "full strength" in out["summary"]


def test_limits_for_a_different_model_are_not_this_models(tmp_path):
    out, _ = _score(tmp_path, FULL_RUN, limits=dict(API_LIMITS, model="claude-opus-5-5"))
    assert out["outcome"] == "failed"


# --------------------------------------------------------------------------- #
# 3. The answer and the death still come first                                 #
# --------------------------------------------------------------------------- #

def test_a_wrong_answer_on_a_degraded_run_is_failed_not_degraded(tmp_path):
    # `degraded` means "it worked, below strength". A run that did not work is
    # failed, whatever it ran at.
    out, _ = _score(tmp_path, DEGRADED_RUN, answer="ladders=9\n")
    assert out["outcome"] == "failed"
    assert "ladders=9" in out["summary"]


def test_a_step_github_reports_failed_is_failed_not_degraded(tmp_path):
    out, _ = _score(tmp_path, DEGRADED_RUN, claude_outcome="failure")
    assert out["outcome"] == "failed"


def test_a_degraded_trial_warns_but_never_fails_the_job(tmp_path):
    # _score asserts exit 0; the warning is the only noise it makes.
    out, _ = _score(tmp_path, DEGRADED_RUN)
    assert out["run_url"].endswith("/actions/runs/36479502126")


# --------------------------------------------------------------------------- #
# 4. The workflow wiring                                                       #
# --------------------------------------------------------------------------- #

def test_the_outcome_contract_names_degraded():
    outputs = (_doc().get("on") or _doc().get(True))["workflow_call"]["outputs"]
    assert "degraded" in outputs["outcome"]["description"]


def test_the_limits_step_runs_after_the_trial_and_before_the_score():
    ids = [s.get("id") for s in _steps()]
    assert "limits" in ids
    assert ids.index("claude") < ids.index("limits") < ids.index("verify"), (
        "the limits are read AFTER the candidate's turn loop, so nothing the "
        "trial agent writes can land in the file the score reads"
    )


def test_the_limits_step_reads_through_claude_code_pin_and_cannot_fail_the_job():
    step = _step("limits")
    assert step.get("continue-on-error") is True
    assert "always()" in str(step.get("if") or "")
    run = str(step.get("run") or "")
    assert "claude_code_pin.py limits" in run
    assert "${{" not in run, "every value arrives through env:"


def test_the_limits_step_holds_the_same_credential_as_the_trial():
    env = _step("limits").get("env") or {}
    trial = _step("claude").get("with") or {}
    assert env.get("ANTHROPIC_API_KEY") == trial.get("anthropic_api_key")
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == trial.get("claude_code_oauth_token")


def test_the_score_reads_the_limits_file_the_step_wrote():
    env = _step("verify").get("env") or {}
    assert "model-limits.json" in str(env.get("LIMITS_FILE") or "")
    assert "runner.temp" in str(env.get("LIMITS_FILE") or ""), (
        "the file lives outside the repository the trial agent can write to"
    )
    run = str(_step("limits").get("run") or "")
    assert "LIMITS_FILE" in run or "model-limits.json" in run


def test_strength_tooling_that_breaks_is_failed_not_a_crash(tmp_path):
    # The pipeline checkout the score imports from, with the strength module
    # broken: our own tooling failing must read as "could not confirm", never
    # as a pass and never as a red job with no outputs.
    pipeline = tmp_path / "pipeline"
    (pipeline / "scripts").mkdir(parents=True)
    for script in (ROOT / "scripts").glob("*.py"):
        (pipeline / "scripts" / script.name).write_text(script.read_text())
    (pipeline / "scripts" / "claude_code_pin.py").write_text(
        "raise ImportError('the strength module is broken')\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(pipeline)
    out, _ = _score(work, FULL_RUN)
    assert out["outcome"] == "failed"
    assert "strength module is broken" in out["summary"]


def test_the_score_speaks_the_modules_status_words():
    # The score compares against literals so a broken import cannot take the
    # constants down with it; these are the literals it compares against.
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import claude_code_pin as ccp
    run = str(_step("verify")["run"])
    assert (ccp.FULL, ccp.BELOW) == ("full", "below")
    assert '"below"' in run and '"full"' in run
