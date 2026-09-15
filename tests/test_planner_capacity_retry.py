"""RED-first: a planner step refused for capacity re-runs on the next rung IN THE SAME JOB (DRE-3970).

The classifier and the groomer already make the same call again on Opus when
Fable refuses for capacity. The four planner steps in plan.yml are
claude-code-action steps, and until this file they had two ways to reach Opus
after a refusal:

  * the CLI's own `--fallback-model`, which the CLI documents for a model that
    is "overloaded or unavailable" — nothing says it fires on "You've hit your
    monthly spend limit", and the compiled CLI cannot be read to find out;
  * the DRE-3824 death marker, which moves the NEXT attempt, after the medic
    reruns the whole job.

Neither is a guarantee inside the run. So each planner step now has the
classifier's guarantee, as steps:

  1. the original step carries `continue-on-error`, so a refusal does not end
     the job before anything can read it;
  2. `<site> — out of capacity?` reads the step's execution record with the
     one detector (`model_fallback.capacity_refusal`) and names the next rung;
  3. a fresh token is minted (the positional rule, DRE-3486's
     tests/test_plan_token_remint.py);
  4. `<site> — on the next rung` is the SAME step — same prompt, same
     arguments — on that rung;
  5. `<site> — finished?` picks the attempt that counts, posts a receipt naming
     the model that ANSWERED (read from the record, not the request), and fails
     the job exactly where the original step used to when neither attempt
     succeeded (the step that was already `continue-on-error` keeps that).

A long genuine failure — turn cap, many turns, real spend — is not out of
capacity, gets no second run, and fails the job as before.
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
WF = ROOT / ".github" / "workflows" / "plan.yml"
SCRIPT = ROOT / "scripts" / "planner_capacity.py"
sys.path.insert(0, str(ROOT / "scripts"))

FABLE51 = "claude-fable-5-1"
OPUS = "claude-opus-5"
SONNET = "claude-sonnet-4-6"
SPEND_LIMIT = ("You've hit your monthly spend limit. Switch to another model to "
               "continue.")
MODEL_ACTION = "anthropics/claude-code-action"

# (site name, original id, cap id, re-mint id, retry id, done id, original mint id,
#  the original already continue-on-error)
SITES = [
    ("Plan epic", "claude", "plan_cap", "app_plan_retry", "claude_retry",
     "plan_done", "app_plan", False),
    ("Re-plan after send-back", "replan", "replan_cap", "app_replan_retry",
     "replan_retry", "replan_done", "app_replan", False),
    ("Re-plan after the second critic sent it back", "postreplan",
     "postreplan_cap", "app_post_retry", "postreplan_retry", "postreplan_done",
     "app_post", True),
    ("Wave route — write the wave plan", "wave", "wave_cap", "app_wave_retry",
     "wave_retry", "wave_done", "app_wave", False),
]
SITE_IDS = [s[1] for s in SITES]


def _refusal(model=FABLE51):
    return [{"type": "system", "subtype": "init", "model": model},
            {"type": "result", "subtype": "success", "is_error": True,
             "num_turns": 1, "total_cost_usd": 0, "duration_ms": 646,
             "result": SPEND_LIMIT}]


def _long_failure():
    return [{"type": "result", "subtype": "success", "is_error": True,
             "num_turns": 64, "total_cost_usd": 7.9, "duration_ms": 1_900_000,
             "result": "API Error: 500 — after reading the standard that quotes "
                       + SPEND_LIMIT,
             "modelUsage": {FABLE51: {"outputTokens": 41000}}}]


def _turn_cap():
    return [{"type": "result", "subtype": "error_max_turns", "is_error": True,
             "num_turns": 141, "total_cost_usd": 18.2,
             "result": "Reached maximum number of turns (140)"}]


def _success(answered):
    return [{"type": "result", "subtype": "success", "is_error": False,
             "num_turns": 40, "total_cost_usd": 4.1, "result": "planned",
             "modelUsage": {answered: {"outputTokens": 30000},
                            "claude-haiku-4-5-20251001": {"outputTokens": 90}}}]


def _write(tmp: Path, name: str, payload) -> str:
    path = tmp / name
    path.write_text(json.dumps(payload))
    return str(path)


def _cli(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, env=env)


def _outputs(path: Path) -> dict:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line)


# --------------------------------------------------------------------------- #
# the decision: out of capacity, or not                                        #
# --------------------------------------------------------------------------- #

def test_a_spend_limit_refusal_retries_on_the_next_rung(tmp_path):
    out = tmp_path / "out"
    proc = _cli("decide", _write(tmp_path, "x.json", _refusal()), "--model", FABLE51,
                "--github-output", out)
    assert proc.returncode == 0, proc.stderr
    got = _outputs(out)
    assert got["retry"] == "true"
    assert got["model"] == OPUS
    assert got["because"] == "monthly spend limit"


@pytest.mark.parametrize("payload", [_long_failure(), _turn_cap()],
                         ids=["long-genuine-failure", "turn-cap"])
def test_a_genuine_failure_gets_no_retry(tmp_path, payload):
    out = tmp_path / "out"
    proc = _cli("decide", _write(tmp_path, "x.json", payload), "--model", FABLE51,
                "--github-output", out)
    assert proc.returncode == 0, proc.stderr
    assert _outputs(out)["retry"] == "false"
    assert _outputs(out)["model"] == ""


def test_a_credential_failure_gets_no_retry(tmp_path):
    payload = [{"type": "result", "subtype": "success", "is_error": True,
                "num_turns": 1, "total_cost_usd": 0, "duration_ms": 300,
                "result": "Invalid API key · Please run /login"}]
    out = tmp_path / "out"
    _cli("decide", _write(tmp_path, "x.json", payload), "--model", FABLE51,
         "--github-output", out)
    assert _outputs(out)["retry"] == "false"


def test_the_last_rung_has_nowhere_to_fall(tmp_path):
    out = tmp_path / "out"
    _cli("decide", _write(tmp_path, "x.json", _refusal(SONNET)), "--model", SONNET,
         "--github-output", out)
    assert _outputs(out)["retry"] == "false"


def test_a_missing_record_gets_no_retry(tmp_path):
    out = tmp_path / "out"
    proc = _cli("decide", tmp_path / "absent.json", "--model", FABLE51,
                "--github-output", out)
    assert proc.returncode == 0
    assert _outputs(out)["retry"] == "false"


# --------------------------------------------------------------------------- #
# the finish: which attempt counts, the receipt, and the exit                  #
# --------------------------------------------------------------------------- #

def _finish(tmp_path, *, first, second="skipped", first_exec="", second_exec="",
            second_model="", because="", required=True):
    out = tmp_path / "finish-out"
    args = ["finish", "--first-outcome", first, "--second-outcome", second,
            "--first-execution-file", first_exec,
            "--second-execution-file", second_exec,
            "--asked", FABLE51, "--second-model", second_model,
            "--because", because, "--github-output", out]
    if required:
        args.append("--required")
    proc = _cli(*args)
    return proc, _outputs(out)


def test_fable_refused_and_opus_planned_is_a_success_with_a_degraded_receipt(tmp_path):
    proc, got = _finish(
        tmp_path, first="failure", second="success",
        first_exec=_write(tmp_path, "a.json", _refusal()),
        second_exec=_write(tmp_path, "b.json", _success(OPUS)),
        second_model=OPUS, because="monthly spend limit")
    assert proc.returncode == 0, proc.stderr
    assert got["outcome"] == "success"
    assert got["model"] == OPUS
    assert got["execution_file"].endswith("b.json")
    receipt = got["receipt"]
    assert receipt.startswith("DEGRADED"), receipt
    assert f"{FABLE51} (asked) / {OPUS} (answered)" in receipt
    assert "out of capacity (monthly spend limit)" in receipt
    assert "model-error:" not in receipt
    assert "planner agent starting" not in receipt, "the DRE-3824 --since needle"


def test_both_rungs_refused_fails_required_and_names_the_second(tmp_path):
    proc, got = _finish(
        tmp_path, first="failure", second="failure",
        first_exec=_write(tmp_path, "a.json", _refusal()),
        second_exec=_write(tmp_path, "b.json", _refusal(OPUS)),
        second_model=OPUS, because="monthly spend limit")
    assert proc.returncode == 1
    assert got["outcome"] == "failure"
    assert got["model"] == OPUS, "the death marker names the model that died last"


def test_a_genuine_failure_with_no_retry_still_fails_the_job(tmp_path):
    proc, got = _finish(tmp_path, first="failure",
                        first_exec=_write(tmp_path, "a.json", _long_failure()))
    assert proc.returncode == 1
    assert got["outcome"] == "failure"
    assert got["model"] == FABLE51
    assert got["receipt"] == ""


def test_the_step_that_was_already_continue_on_error_never_fails_here(tmp_path):
    proc, got = _finish(tmp_path, first="failure",
                        first_exec=_write(tmp_path, "a.json", _long_failure()),
                        required=False)
    assert proc.returncode == 0
    assert got["outcome"] == "failure"


def test_a_clean_run_on_fable_posts_no_receipt(tmp_path):
    proc, got = _finish(tmp_path, first="success",
                        first_exec=_write(tmp_path, "a.json", _success(FABLE51)))
    assert proc.returncode == 0
    assert got["outcome"] == "success"
    assert got["receipt"] == ""


def test_a_silent_cli_fallback_is_named_by_the_model_that_answered(tmp_path):
    # Gap 2: the CLI's own --fallback-model fired inside the step, so the
    # heartbeat said Fable while Opus planned. The record's modelUsage says who.
    payload = [{"type": "result", "subtype": "success", "is_error": False,
                "num_turns": 40, "total_cost_usd": 4.1,
                "modelUsage": {FABLE51: {"outputTokens": 0},
                               OPUS: {"outputTokens": 28000}}}]
    proc, got = _finish(tmp_path, first="success",
                        first_exec=_write(tmp_path, "a.json", payload))
    assert proc.returncode == 0
    assert got["receipt"].startswith("DEGRADED"), got
    assert f"{FABLE51} (asked) / {OPUS} (answered)" in got["receipt"]


# --------------------------------------------------------------------------- #
# the wiring, at every planner site                                            #
# --------------------------------------------------------------------------- #

def _steps() -> list[dict]:
    return yaml.safe_load(WF.read_text())["jobs"]["plan"]["steps"]


def _index(step_id: str) -> int:
    for i, step in enumerate(_steps()):
        if step.get("id") == step_id:
            return i
    raise AssertionError(f"plan.yml has no step with id {step_id!r}")


def _by_id(step_id: str) -> dict:
    return _steps()[_index(step_id)]


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_five_steps_sit_in_order_right_after_the_planner_step(site):
    name, orig, cap, mint, retry, done, _mint0, _coe = site
    at = _index(orig)
    assert [_steps()[at + k].get("id") for k in range(5)] == [orig, cap, mint, retry, done]


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_planner_step_does_not_end_the_job_before_it_is_read(site):
    assert _by_id(site[1]).get("continue-on-error") is True


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_capacity_read_runs_only_on_a_failed_attempt(site):
    name, orig, cap, *_ = site
    step = _by_id(cap)
    assert f"steps.{orig}.outcome == 'failure'" in step["if"]
    assert "!cancelled()" in step["if"]
    assert "planner_capacity.py decide" in step["run"]
    assert "${{" not in step["run"], "substitutions go in env: (DRE-3484)"


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_retry_is_the_same_step_on_the_next_rung(site):
    name, orig, cap, mint, retry, done, mint0, _coe = site
    a, b = _by_id(orig), _by_id(retry)
    assert b["uses"] == a["uses"]
    assert b["if"] == f"({a['if']}) && steps.{cap}.outputs.retry == 'true'", (
        "gated on the original's route too (tests/test_planning_route.py)")
    assert b.get("continue-on-error") is True
    assert b["env"] == a["env"]
    for key in a["with"]:
        if key in ("github_token", "claude_args"):
            continue
        assert b["with"][key] == a["with"][key], f"{retry}.with.{key} drifted from {orig}"
    assert a["with"]["github_token"] == f"${{{{ steps.{mint0}.outputs.token }}}}"
    assert b["with"]["github_token"] == f"${{{{ steps.{mint}.outputs.token }}}}"
    expected = (a["with"]["claude_args"]
                .replace("--model ${{ steps.model.outputs.model }}",
                         f"--model ${{{{ steps.{cap}.outputs.model }}}}")
                .replace("${{ steps.model.outputs.fallback_arg }}\n", ""))
    assert b["with"]["claude_args"] == expected


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_retry_reads_a_token_minted_after_the_failed_attempt(site):
    name, orig, cap, mint, retry, *_ = site
    step = _by_id(mint)
    assert step["uses"] == _by_id("app_plan")["uses"]
    assert step["with"] == _by_id("app_plan")["with"]
    assert step["if"] == _by_id(retry)["if"], "minted exactly when it is spent"


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_the_finish_decides_and_posts_the_receipt(site):
    name, orig, cap, mint, retry, done, _mint0, already_coe = site
    step = _by_id(done)
    assert f"steps.{orig}.outcome == 'success'" in step["if"]
    assert f"steps.{orig}.outcome == 'failure'" in step["if"]
    assert "planner_capacity.py finish" in step["run"]
    assert "${{" not in step["run"], "substitutions go in env: (DRE-3484)"
    env = step["env"]
    assert env["FIRST_OUTCOME"] == f"${{{{ steps.{orig}.outcome }}}}"
    assert env["SECOND_OUTCOME"] == f"${{{{ steps.{retry}.outcome }}}}"
    assert env["SECOND_MODEL"] == f"${{{{ steps.{cap}.outputs.model }}}}"
    assert env["BECAUSE"] == f"${{{{ steps.{cap}.outputs.because }}}}"
    assert "🧠 model-attempt:" in step["run"]
    if already_coe:
        assert step.get("continue-on-error") is True
        assert "--required" not in step["run"]
    else:
        assert not step.get("continue-on-error")
        assert "--required" in step["run"]


def test_the_death_step_reads_the_attempt_that_counted():
    env = next(s for s in _steps() if s.get("name") == "Record is_error death")["env"]
    assert env["MODEL_USED"] == ("${{ steps.plan_done.outputs.model || "
                                 "steps.model.outputs.model }}")
    assert env["EXEC_FILE_USED"] == ("${{ steps.plan_done.outputs.execution_file || "
                                     "steps.claude.outputs.execution_file }}")


def test_the_second_critic_decision_reads_the_re_plan_that_counted():
    uses = [s for s in _steps()
            if "REPLAN_OUTCOME" in (s.get("env") or {})]
    assert uses, "the send-back step still reads the re-plan outcome"
    for step in uses:
        assert step["env"]["REPLAN_OUTCOME"] == (
            "${{ steps.postreplan_done.outputs.outcome || steps.postreplan.outcome }}")


# --------------------------------------------------------------------------- #
# the two run blocks, executed                                                 #
# --------------------------------------------------------------------------- #

def _bash(step: dict, tmp: Path, env_extra: dict):
    root = tmp / ".bureau-pipeline" / "scripts"
    if not root.exists():
        shutil.copytree(ROOT / "scripts", root)
        shutil.copytree(ROOT / "config", tmp / ".bureau-pipeline" / "config")
        (root / "linear_ops.py").write_text(
            "import json,os,sys\n"
            "open(os.environ['STUB_LINEAR_LOG'],'a').write(json.dumps(sys.argv[1:])+'\\n')\n")
    out = tmp / "gh_output"
    env = dict(os.environ)
    env.update({"GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(tmp),
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
                "GITHUB_RUN_ID": "34924370626",
                "LINEAR_API_KEY": "stub", "STUB_LINEAR_LOG": str(tmp / "linear.log")})
    for key, value in (step.get("env") or {}).items():
        env.setdefault(key, "")
    env.update(env_extra)
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp, env=env,
                          capture_output=True, text=True)
    return proc, _outputs(out)


def test_the_plan_epic_steps_run_end_to_end_on_a_refusal(tmp_path):
    first = _write(tmp_path, "first.json", _refusal())
    proc, cap = _bash(_by_id("plan_cap"), tmp_path, {
        "EXEC_FILE": first, "MODEL": FABLE51, "EPIC": "DRE-3949"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert cap["retry"] == "true" and cap["model"] == OPUS

    second = _write(tmp_path, "second.json", _success(OPUS))
    (tmp_path / "gh_output").unlink()
    proc, done = _bash(_by_id("plan_done"), tmp_path, {
        "FIRST_OUTCOME": "failure", "SECOND_OUTCOME": "success",
        "FIRST_EXEC": first, "SECOND_EXEC": second, "ASKED": FABLE51,
        "SECOND_MODEL": OPUS, "BECAUSE": cap["because"], "EPIC": "DRE-3949"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert done["model"] == OPUS and done["outcome"] == "success"
    posted = [json.loads(l) for l in (tmp_path / "linear.log").read_text().splitlines()]
    assert len(posted) == 1
    assert posted[0][0] == "comment" and posted[0][1] == "DRE-3949"
    body = posted[0][2]
    assert body.startswith("🧠 model-attempt: DEGRADED")
    assert "/actions/runs/34924370626" in body, "dedupe_dispatch reads the run id"


def test_the_plan_epic_finish_fails_the_job_on_a_genuine_failure(tmp_path):
    first = _write(tmp_path, "first.json", _long_failure())
    proc, cap = _bash(_by_id("plan_cap"), tmp_path, {
        "EXEC_FILE": first, "MODEL": FABLE51, "EPIC": "DRE-3949"})
    assert cap["retry"] == "false"
    (tmp_path / "gh_output").unlink()
    proc, done = _bash(_by_id("plan_done"), tmp_path, {
        "FIRST_OUTCOME": "failure", "SECOND_OUTCOME": "skipped",
        "FIRST_EXEC": first, "SECOND_EXEC": "", "ASKED": FABLE51,
        "SECOND_MODEL": "", "BECAUSE": "", "EPIC": "DRE-3949"})
    assert proc.returncode != 0
    assert not (tmp_path / "linear.log").exists(), "no receipt for a run nobody fell from"


@pytest.mark.parametrize("site", SITES, ids=SITE_IDS)
def test_every_capacity_retry_step_carries_its_planner_steps_route(site):
    """The re-run steps belong to their planner step's route and no other.

    tests/test_wave_plan_wiring.py caught `wave_cap` and `wave_done` gated only
    on the wave step's outcome; nothing pinned the same for the plan epic and
    the two re-plans, so dropping the route from any of their four steps would
    have passed. A step off its route runs for a card on another one — a
    finished? step that fails the job, or a re-run nobody's route asked for."""
    name, orig, cap, mint, retry, done, _mint0, _coe = site
    route = _by_id(orig)["if"]
    assert route, f"{orig} is gated on a route"
    for step_id in (cap, mint, retry, done):
        gate = str(_by_id(step_id).get("if") or "")
        assert f"({route})" in gate, (
            f"{step_id} is not gated on {orig}'s route `{route}`: `{gate}`")
