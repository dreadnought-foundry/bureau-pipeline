"""RED-first tests for DRE-3897 — one real agent run on a candidate model.

THE PROBLEM. `model-drift.yml` (DRE-2236) discovers a new model and opens a
card; a human then edits `config/models.yaml`. Nothing in between ever RUNS the
candidate. The CEO rejected "auto without a test run" for exactly that reason: a
model that ships a breaking API change would otherwise reach every build agent
untested. Opus 5 is the worked example — it turned thinking on by default, so a
tight `max_tokens` truncated answers, and it returned 400 when thinking was
disabled above `high` effort. Neither fault is visible in a catalog listing, and
both would kill a bounded, tool-using Claude Code turn loop. So that loop is the
test, and this file is what pins it.

WHAT `model-trial.yml` IS.

  * `workflow_call` ONLY. It never runs under its own name, so
    `scripts/check_workflow_watchers.py` exempts it as reusable-only and it
    needs no entry in `self-medic.yml` — asserted here against the live
    checker rather than described.
  * ONE bounded task on the model it is handed, through
    `anthropics/claude-code-action` at the sha every other workflow pins
    (DRE-3416) and after the shared install-and-assert step (DRE-3414). NEVER a
    raw Messages API call: a subscription OAuth token answers 429 to every one
    of those, at any load (DRE-3074).
  * A CHECKABLE answer. The agent reads `config/models.yaml`, counts the
    ladders it declares, and writes `ladders=<n>` to `trial-answer.txt`. The
    job recomputes the count with PyYAML and compares.
  * DATA, not a verdict on the run. A failed trial must leave the job GREEN and
    say so in its outputs — the caller (`model-adoption.yml`, DRE-3903) is what
    acts on it. A trial that failed the calling workflow would be a model
    outage taking the adoption rail down with it.

WHY HALF OF THIS FILE EXECUTES THE SCORING SCRIPT INSTEAD OF GREPPING IT. "The
outcome is `passed` only when the file matches AND the classification is
`none`" is a claim about behaviour, and a YAML parse can see none of it. The
`run:` block out of the verify step is extracted and run under bash against a
synthetic config, a synthetic answer file and a synthetic execution record —
the same discipline `tests/test_claude_install_asserted.py` applies to the
shared install action.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_trial_workflow.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TRIAL = WORKFLOWS / "model-trial.yml"
AGENT_TASK = WORKFLOWS / "agent-task.yml"
MEDIC_STUB = WORKFLOWS / "self-medic.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_watchers as watchers  # noqa: E402

# The vendor step and its pin. One sha across the repo (DRE-3416): a floating
# major is what killed every Claude-running job in the fleet for 72 minutes.
VENDOR_ACTION = "anthropics/claude-code-action"
VENDOR_PIN = "9c5ddab2e6d17b83ea679153b31f1d5f023cf636"

# The shared install-and-assert step (DRE-3414). The path resolves against the
# CALLER's workspace, which is why it is the `.bureau-pipeline/` form.
SHARED_STEP_USES = "./.bureau-pipeline/.github/actions/install-claude-code"
SHARED_STEP_ID = "install_claude"
EXECUTABLE_EXPR = "${{ steps.install_claude.outputs.executable }}"

# The three claude_args bounds the card fixes.
MAX_TURNS = "--max-turns 10"
MODEL_ARG = "--model ${{ inputs.model }}"
ALLOWED_TOOLS = '--allowedTools "Read,Glob,Grep,Write"'

# The contract shared with the adoption workflow card (DRE-3903).
CONTRACT_OUTPUTS = ("outcome", "run_url", "summary")

ANSWER_FILE = "trial-answer.txt"
CONFIG_FILE = "config/models.yaml"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _doc(path: Path = TRIAL) -> dict:
    return yaml.safe_load(path.read_text())


def _text() -> str:
    return TRIAL.read_text()


def _on(doc: dict) -> dict:
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _jobs(doc: dict) -> dict:
    return doc.get("jobs") or {}


def _steps(doc: dict) -> list:
    return [s for job in _jobs(doc).values() for s in (job or {}).get("steps") or []]


def _step_by_id(doc: dict, step_id: str) -> dict:
    for step in _steps(doc):
        if isinstance(step, dict) and step.get("id") == step_id:
            return step
    raise AssertionError(f"no step with id {step_id!r} in {TRIAL.name}")


def _model_step(doc: dict) -> dict:
    for step in _steps(doc):
        if VENDOR_ACTION in str((step or {}).get("uses", "")):
            return step
    raise AssertionError(f"{TRIAL.name} runs no {VENDOR_ACTION} step")


def _agent_task_model_step() -> dict:
    for job in (_doc(AGENT_TASK).get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if step.get("id") == "claude":
                return step
    raise AssertionError("agent-task.yml has no `Implement card` step")


# --------------------------------------------------------------------------- #
# 1. The trigger and the contract                                              #
# --------------------------------------------------------------------------- #

def test_the_workflow_exists():
    assert TRIAL.is_file(), f"{TRIAL} is missing"


def test_it_triggers_on_workflow_call_only():
    # No schedule and no dispatch: it runs when, and only when, the adoption
    # workflow calls it. That is also what makes it watcher-exempt below.
    assert set(_on(_doc())) == {"workflow_call"}


def test_it_declares_the_model_input():
    spec = (_on(_doc())["workflow_call"].get("inputs") or {}).get("model")
    assert spec is not None, "the candidate model id arrives as an input"
    assert spec.get("type") == "string"
    assert spec.get("required") is True


def test_it_declares_pipeline_ref_like_every_other_reusable():
    # NOT in the card's contract block, and not optional either: every
    # workflow_call file here must declare it (DRE-2026/DRE-2689), and
    # scripts/check_pipeline_ref.py is red without it.
    spec = (_on(_doc())["workflow_call"].get("inputs") or {}).get("pipeline_ref")
    assert spec is not None
    assert spec.get("type") == "string"
    assert spec.get("required") is True
    assert "default" not in spec


def test_it_declares_the_three_contract_outputs():
    outputs = _on(_doc())["workflow_call"].get("outputs") or {}
    assert set(CONTRACT_OUTPUTS) <= set(outputs), sorted(outputs)


def test_every_contract_output_is_wired_to_a_job_that_produces_it():
    doc = _doc()
    outputs = _on(doc)["workflow_call"]["outputs"]
    job_id, job = next(iter(_jobs(doc).items()))
    produced = job.get("outputs") or {}
    for name in CONTRACT_OUTPUTS:
        value = str(outputs[name].get("value") or "")
        assert f"jobs.{job_id}.outputs.{name}" in value, (
            f"workflow output {name!r} is {value!r} — a declared output no job "
            f"fills is a contract the caller reads as empty"
        )
        assert name in produced, f"job {job_id} does not produce {name!r}"


def test_both_credential_secrets_are_optional():
    secrets = _on(_doc())["workflow_call"].get("secrets") or {}
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert name in secrets, f"{name} must be declared for `secrets: inherit`"
        assert (secrets[name] or {}).get("required") is False, (
            f"{name} must be optional — a repo runs one auth mode, never both"
        )


def test_permissions_grant_contents_read_and_nothing_more():
    assert _doc().get("permissions") == {"contents": "read"}, (
        "nothing is pushed and no card text is read: contents: read is the "
        "whole permission set"
    )


# --------------------------------------------------------------------------- #
# 2. The watcher check exempts it, so self-medic.yml needs no entry            #
# --------------------------------------------------------------------------- #

def test_the_watcher_checker_calls_it_reusable_only():
    assert watchers.is_reusable_only(_on(_doc())) is True
    assert watchers.runs_on_default_branch(_on(_doc())) is False


def test_the_live_watcher_check_reports_no_violation_for_it():
    violations, stats = watchers.check_dir(WORKFLOWS)
    assert stats["workflows"] > 0, "the checker went vacuous"
    assert [v for v in violations if TRIAL.name in v] == []


def test_the_medic_watch_list_needs_no_entry_for_it():
    watched = _on(yaml.safe_load(MEDIC_STUB.read_text()))["workflow_run"]["workflows"]
    assert _doc().get("name") not in watched, (
        "a workflow_call-only file never runs under its own name — watching it "
        "would be a dangling entry"
    )


# --------------------------------------------------------------------------- #
# 3. The model step: the pin, the shared install, the bounds                   #
# --------------------------------------------------------------------------- #

def test_the_model_runs_through_the_pinned_vendor_action():
    uses = str(_model_step(_doc())["uses"])
    assert uses.startswith(f"{VENDOR_ACTION}@{VENDOR_PIN}"), uses


def test_it_never_calls_the_messages_api_directly():
    # DRE-3074: the subscription OAuth token answers 429 to every raw
    # /v1/messages request, at any load. Every model call in this pipeline goes
    # through claude-code-action for that reason.
    assert "api.anthropic.com" not in _text()
    assert "v1/messages" not in _text()


def test_the_shared_install_step_precedes_the_model_step():
    doc = _doc()
    steps = _steps(doc)
    shared = [i for i, s in enumerate(steps)
              if str(s.get("uses", "")).strip() == SHARED_STEP_USES]
    assert shared, f"no {SHARED_STEP_USES} step"
    assert steps[shared[0]].get("id") == SHARED_STEP_ID
    first_model = min(i for i, s in enumerate(steps)
                      if VENDOR_ACTION in str(s.get("uses", "")))
    assert shared[0] < first_model, "Claude Code is installed after it is run"


def test_the_model_step_runs_the_binary_that_was_proved():
    with_ = _model_step(_doc()).get("with") or {}
    assert with_.get("path_to_claude_code_executable") == EXECUTABLE_EXPR


def test_claude_args_carry_the_cards_three_bounds():
    args = (_model_step(_doc()).get("with") or {}).get("claude_args") or ""
    for expected in (MAX_TURNS, MODEL_ARG, ALLOWED_TOOLS):
        assert expected in args, f"claude_args is missing {expected!r}: {args!r}"


def test_no_literal_model_id_is_pinned_after_the_model_flag():
    # tests/test_model_config.py::test_no_workflow_hardcodes_a_model_id sweeps
    # the whole directory; this is the same assertion stated where the reader
    # of this file will look for it. The id arrives as an input.
    hits = re.findall(r"--model\s+(claude-[a-z0-9.-]+)", _text())
    assert hits == [], hits


def test_the_tool_set_grants_no_shell_and_no_network():
    args = (_model_step(_doc()).get("with") or {}).get("claude_args") or ""
    tools = re.search(r'--allowedTools\s+"([^"]*)"', args).group(1).split(",")
    assert sorted(t.strip() for t in tools) == ["Glob", "Grep", "Read", "Write"], tools


def test_credentials_follow_agent_tasks_implement_card_step():
    mine = _model_step(_doc()).get("with") or {}
    theirs = _agent_task_model_step().get("with") or {}
    for key in ("anthropic_api_key", "claude_code_oauth_token", "allowed_bots"):
        assert mine.get(key) == theirs.get(key), (
            f"{key} must be copied from agent-task.yml verbatim — "
            f"{mine.get(key)!r} != {theirs.get(key)!r}"
        )


# --------------------------------------------------------------------------- #
# 4. The prompt: fixed, checkable, and free of interpolation                   #
# --------------------------------------------------------------------------- #

def _prompt() -> str:
    return str((_model_step(_doc()).get("with") or {}).get("prompt") or "")


def test_the_prompt_interpolates_nothing_at_all():
    # No card text, no PR text, no run context: the task is FIXED, so there is
    # no untrusted string for an injection to ride in on. `${{` anywhere in the
    # block is the thing to fail on, not a denylist of field names.
    assert "${{" not in _prompt(), (
        "the trial prompt must be a fixed string — interpolating anything into "
        "it reopens the DRE-1989 prompt-injection surface"
    )


def test_the_prompt_states_the_checkable_task():
    prompt = _prompt()
    assert CONFIG_FILE in prompt
    assert "ladders" in prompt
    assert ANSWER_FILE in prompt
    assert "ladders=" in prompt, "the answer's exact shape must be stated"


def test_the_prompt_asks_for_nothing_that_is_pushed():
    prompt = _prompt().lower()
    assert "pull request" in prompt or "push" in prompt, (
        "the prompt must say out loud that nothing is pushed"
    )


# --------------------------------------------------------------------------- #
# 5. A failed trial is data: green job, outputs either way                     #
# --------------------------------------------------------------------------- #

def test_the_model_step_cannot_fail_the_job():
    assert _model_step(_doc()).get("continue-on-error") is True, (
        "a model that dies is the ANSWER this workflow exists to produce, not "
        "a failure of the job that asked"
    )


def test_the_verify_step_runs_even_after_a_dead_trial():
    assert "always()" in str(_step_by_id(_doc(), "verify").get("if") or "")


def test_the_verify_step_compares_and_classifies():
    run = str(_step_by_id(_doc(), "verify").get("run") or "")
    assert "check_agent_result.py classify" in run, (
        "the death class comes from the ONE shared predicate (DRE-2312), never "
        "from a test of its own"
    )
    assert "yaml" in run, "the true ladder count is computed with PyYAML"


# --------------------------------------------------------------------------- #
# 6. The scoring script, EXECUTED                                              #
# --------------------------------------------------------------------------- #

# Two ladders, deliberately not three: a synthetic config proves the count is
# READ rather than remembered from config/models.yaml.
SYNTHETIC_CONFIG = """\
kinds:
  workhorse: {ladder: workhorse}
ladders:
  workhorse:
    - model: a-model
    - model: b-model
  advisory:
    - model: c-model
default_ladder: workhorse
"""
SYNTHETIC_LADDERS = 2

CLEAN_RUN = {"is_error": False, "subtype": "success", "num_turns": 7,
             "duration_ms": 42000, "total_cost_usd": 0.11}
TURN_CAP_RUN = {"is_error": True, "subtype": "error_max_turns", "num_turns": 10,
                "duration_ms": 91000}
API_DEATH_RUN = {"is_error": True, "subtype": "success", "num_turns": 1,
                 "duration_ms": 400,
                 "result": "Invalid model id", "api_error_status": 400}


def _score(tmp_path: Path, *, answer: str | None, execution: dict | None,
           model: str = "candidate-model", config: str = SYNTHETIC_CONFIG,
           claude_outcome: str = "success"):
    """Run the verify step's real `run:` block. Returns (proc, outputs)."""
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / CONFIG_FILE).write_text(config)
    # The pipeline checkout the script reads its shared predicates out of.
    link = tmp_path / ".bureau-pipeline"
    if not link.exists():
        link.symlink_to(ROOT)
    if answer is not None:
        (tmp_path / ANSWER_FILE).write_text(answer)
    exec_file = tmp_path / "execution.json"
    if execution is not None:
        exec_file.write_text(json.dumps(execution))

    script = tmp_path / "verify.sh"
    script.write_text(str(_step_by_id(_doc(), "verify")["run"]))
    out = tmp_path / "github_output"
    out.touch()
    step_summary = tmp_path / "step_summary"
    step_summary.touch()

    env = dict(os.environ)
    env.update({
        "MODEL": model,
        "CLAUDE_OUTCOME": claude_outcome,
        "CLAUDE_EXECUTION_FILE": str(exec_file),
        "ANSWER_FILE": ANSWER_FILE,
        "CONFIG_FILE": CONFIG_FILE,
        "BUREAU_SERVER_URL": "https://github.com",
        "BUREAU_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
        "BUREAU_RUN_ID": "12345",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(step_summary),
        "RUNNER_TEMP": str(tmp_path),
    })
    proc = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True)
    outputs = dict(
        line.split("=", 1)
        for line in out.read_text().splitlines() if "=" in line
    )
    return proc, outputs


def test_a_matching_answer_on_a_clean_run_passes(tmp_path):
    proc, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                       execution=CLEAN_RUN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "passed"
    assert out["summary"].startswith("candidate-model: passed — completed,")
    assert "7 turns" in out["summary"]
    assert "42s" in out["summary"]


def test_the_run_url_is_this_run(tmp_path):
    _, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                    execution=CLEAN_RUN)
    assert out["run_url"] == (
        "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/12345"
    )


def test_a_wrong_count_fails_and_names_the_mismatch(tmp_path):
    proc, out = _score(tmp_path, answer="ladders=9\n", execution=CLEAN_RUN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"
    assert "ladders=9" in out["summary"]
    assert f"ladders={SYNTHETIC_LADDERS}" in out["summary"]


def test_a_missing_answer_file_fails(tmp_path):
    proc, out = _score(tmp_path, answer=None, execution=CLEAN_RUN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"
    assert ANSWER_FILE in out["summary"]


def test_an_answer_that_is_not_the_one_line_fails(tmp_path):
    # The line is the contract. Prose around the right number is not it.
    proc, out = _score(tmp_path,
                       answer=f"There are {SYNTHETIC_LADDERS} ladders.\n",
                       execution=CLEAN_RUN)
    assert out["outcome"] == "failed"


def test_turn_exhaustion_fails_even_with_the_right_answer(tmp_path):
    # The whole point of the card: a model that cannot finish a 10-turn loop is
    # not adoptable, whatever landed on disk before it died.
    proc, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                       execution=TURN_CAP_RUN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"
    assert "turn_exhaustion" in out["summary"]


def test_an_api_death_fails_and_is_named(tmp_path):
    proc, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                       execution=API_DEATH_RUN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"
    assert "api_death" in out["summary"]


def test_no_execution_record_at_all_fails(tmp_path):
    # `classify` answers `none` both for "it did not die" and for "there is no
    # result record to say so". Absence is not health: an adoption gate must
    # not certify a candidate on evidence it never saw.
    proc, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                       execution=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"
    assert "no execution record" in out["summary"]


def test_a_trial_step_github_reports_as_failed_fails(tmp_path):
    # `continue-on-error` keeps the job green, so GitHub's own `outcome` for
    # the step is the only record that it went red (DRE-2931). A right-looking
    # answer file does not overrule it.
    proc, out = _score(tmp_path, answer=f"ladders={SYNTHETIC_LADDERS}\n",
                       execution=CLEAN_RUN, claude_outcome="failure")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"


def test_an_unreadable_config_fails_closed(tmp_path):
    # Our own tooling breaking must never read as "the candidate passed".
    proc, out = _score(tmp_path, answer="ladders=2\n", execution=CLEAN_RUN,
                       config="ladders: [this is not a mapping\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "failed"


@pytest.mark.parametrize("execution,answer", [
    (CLEAN_RUN, f"ladders={SYNTHETIC_LADDERS}\n"),
    (TURN_CAP_RUN, None),
    (API_DEATH_RUN, "ladders=1\n"),
    (None, None),
])
def test_every_path_exits_zero_and_emits_all_three_outputs(tmp_path, execution, answer):
    # A failed trial must not fail the calling workflow: the outcome is data.
    proc, out = _score(tmp_path, answer=answer, execution=execution)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert set(CONTRACT_OUTPUTS) <= set(out), sorted(out)
    assert out["outcome"] in ("passed", "failed")
    assert "\n" not in out["summary"]


def test_the_live_config_count_is_what_the_agent_is_asked_for(tmp_path):
    # The task is only checkable if the real file answers it: the live
    # config/models.yaml must declare countable top-level ladders.
    live = yaml.safe_load((ROOT / CONFIG_FILE).read_text())
    assert isinstance(live.get("ladders"), dict) and live["ladders"], live.keys()
    proc, out = _score(tmp_path, answer=f"ladders={len(live['ladders'])}\n",
                       execution=CLEAN_RUN,
                       config=(ROOT / CONFIG_FILE).read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["outcome"] == "passed"
