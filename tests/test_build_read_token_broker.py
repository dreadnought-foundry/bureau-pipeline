"""Agent Task's and Agent Fix's agent sessions read through the read door, and
their read lines are printed into their run's log (DRE-9252).

Both build jobs set `BUREAU_READ` at job level, so every `linear_ops` read a
build session makes asks the door. A model session is never handed the
runner's OIDC request variables, so each of those reads fell back with
`reason=no-token`, and the line landed in the session's transcript, not the
job log — `scripts/check_linear_budget.py` never saw it, and its fallback
table could read zero while these sessions fell back on every read.

DRE-9168 built the broker (`scripts/read_token_broker.py`) and DRE-9169 wired
it into `plan.yml` (tests/test_plan_read_token_broker.py). This file pins the
same wiring in `.github/workflows/agent-task.yml`'s `execute` job and
`.github/workflows/agent-fix.yml`'s `fix` job:

  1. One step, after the pipeline's scripts are checked out and before the
     job's first model step, starts the broker under `continue-on-error`, so
     a broker that does not start leaves every session as it was.
  2. Every model step — found by its `uses:`, never listed — carries the
     broker's URL, its secret and the lines file, and the four named on the
     card are among them.
  3. The secret reaches the steps through a masked step output and is never
     echoed: the start step is RUN here against the real broker and its
     output read.
  4. One step after the job's last model step, under `always()`, prints the
     lines file through `print-lines` and stops the broker — RUN here too —
     and no step anywhere hands the file to `cat`, `echo`, `tee` or `printf`.

Run: python3 -m pytest tests/test_build_read_token_broker.py -v
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

MODEL_ACTION = "anthropics/claude-code-action"
STATE_FILE = '"$RUNNER_TEMP/read-token-broker.json"'
LINES_NAME = "agent-read-lines.log"
LINES_FILE = f'"$RUNNER_TEMP/{LINES_NAME}"'
BROKER = "python3 .bureau-pipeline/scripts/read_token_broker.py"

# The card's four, by workflow, job and id, in file order as `main` held them
# on 2026-11-06. `workflow` is the `name:` of this repo's calling stub: in a
# reusable workflow GITHUB_WORKFLOW is the caller's name, and a session's
# `reader=` is spelled with it.
BUILDS = {
    "agent-task": {"file": "agent-task.yml", "job": "execute", "stub": "self-agent-task.yml",
                   "workflow": "Agent Task",
                   "named": ("claude", "claude_retry1", "claude_retry2")},
    "agent-fix": {"file": "agent-fix.yml", "job": "fix", "stub": "self-agent-fix.yml",
                  "workflow": "Agent Fix", "named": ("claude",)},
}
FORGED = "::set-output name=verdict::APPROVE"


def _wf(build: str) -> Path:
    return WORKFLOWS / BUILDS[build]["file"]


def _doc(build: str) -> dict:
    return yaml.safe_load(_wf(build).read_text(encoding="utf-8"))


def _steps(build: str) -> list[dict]:
    return _doc(build)["jobs"][BUILDS[build]["job"]]["steps"]


def _model_indexes(steps: list[dict]) -> list[int]:
    return [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(MODEL_ACTION)]


def _start_index(steps: list[dict]) -> int:
    found = [i for i, s in enumerate(steps) if f"{BROKER} start" in (s.get("run") or "")]
    assert len(found) == 1, f"one step starts the broker, found {len(found)}"
    return found[0]


def _stop_index(steps: list[dict]) -> int:
    found = [i for i, s in enumerate(steps) if f"{BROKER} stop" in (s.get("run") or "")]
    assert len(found) == 1, f"one step stops the broker, found {len(found)}"
    return found[0]


def _start(build: str) -> dict:
    steps = _steps(build)
    return steps[_start_index(steps)]


def _stop(build: str) -> dict:
    steps = _steps(build)
    return steps[_stop_index(steps)]


builds = pytest.mark.parametrize("build", sorted(BUILDS))


# ===========================================================================
# 1. The start step: once, before the first model step, never fatal
# ===========================================================================

@builds
def test_the_calling_stub_names_the_reader_the_sessions_spell(build):
    stub = WORKFLOWS / BUILDS[build]["stub"]
    doc = yaml.safe_load(stub.read_text(encoding="utf-8"))
    assert doc["name"] == BUILDS[build]["workflow"]
    calls = [job.get("uses", "") for job in doc["jobs"].values()]
    assert any(BUILDS[build]["file"] in str(u) for u in calls), calls


@builds
def test_the_start_step_follows_the_pipeline_checkout_and_precedes_every_model_step(build):
    steps = _steps(build)
    checkouts = [i for i, s in enumerate(steps)
                 if (s.get("with") or {}).get("path") == ".bureau-pipeline"]
    start = _start_index(steps)
    models = _model_indexes(steps)
    assert checkouts and checkouts[0] < start < models[0], (checkouts, start, models[0])
    assert steps[models[0]].get("id") == BUILDS[build]["named"][0]


@builds
def test_the_start_step_runs_the_contract_command_under_continue_on_error(build):
    step = _start(build)
    assert step.get("id"), "the model steps read its outputs by id"
    assert step.get("continue-on-error") is True, (
        "a broker that fails to start must leave every session as today")
    assert f"{BROKER} start --state-file {STATE_FILE}" in step["run"]
    assert "${{" not in step["run"], "substitutions go in env: (DRE-3484)"


@builds
def test_the_start_step_runs_only_once_claude_code_is_installed(build):
    steps = _steps(build)
    install = next(i for i, s in enumerate(steps) if s.get("id") == "install_claude")
    assert install < _start_index(steps)
    assert "steps.install_claude.outcome == 'success'" in str(_start(build).get("if", ""))


@builds
def test_the_secret_is_masked_before_it_is_written_and_never_echoed(build):
    run = _start(build)["run"]
    mask = run.index("::add-mask::")
    written = run.index("secret=")
    assert mask < written, "the mask goes up before the value can reach any output"
    assert "GITHUB_ENV" not in run, (
        "a step output, not $GITHUB_ENV — only the model steps are handed it")
    for line in run.splitlines():
        if re.search(r"\b(echo|printf|cat)\b", line):
            assert "secret" not in line.lower(), line


@builds
def test_the_comment_above_the_start_step_answers_the_five_questions(build):
    raw = _wf(build).read_text(encoding="utf-8")
    name = _start(build)["name"]
    at = raw.index(f"      - name: {name}\n")
    comment = raw[raw.rindex("\n\n", 0, at):at]
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        assert q in comment, f"{q} is not answered above the start step"
    steps = _steps(build)
    for step_id in [steps[i].get("id") for i in _model_indexes(steps)]:
        assert f"`{step_id}`" in comment, f"the comment does not name {step_id}"
    assert "DRE-9252" in comment


# ===========================================================================
# 2. Every model step carries the three variables
# ===========================================================================

@builds
def test_the_named_model_steps_are_found_by_their_uses(build):
    steps = _steps(build)
    ids = [steps[i].get("id") for i in _model_indexes(steps)]
    named = BUILDS[build]["named"]
    assert set(named) <= set(ids), set(named) - set(ids)
    assert [i for i in ids if i in named] == list(named), "file order"


def _every_model_step():
    for build in sorted(BUILDS):
        steps = _steps(build)
        for i in _model_indexes(steps):
            yield pytest.param(build, i, id=f"{build}:{steps[i].get('id')}")


@pytest.mark.parametrize("build,index", list(_every_model_step()))
def test_every_model_step_carries_the_broker_and_the_lines_file(build, index):
    start_id = _start(build)["id"]
    env = _steps(build)[index].get("env") or {}
    assert env.get("BUREAU_READ_TOKEN_BROKER") == f"${{{{ steps.{start_id}.outputs.url }}}}"
    assert env.get("BUREAU_READ_TOKEN_BROKER_SECRET") == (
        f"${{{{ steps.{start_id}.outputs.secret }}}}")
    assert env.get("BUREAU_READ_LINES_FILE") == f"${{{{ runner.temp }}}}/{LINES_NAME}"


@builds
def test_no_other_step_is_handed_the_broker_secret(build):
    for job in _doc(build)["jobs"].values():
        assert "BUREAU_READ_TOKEN_BROKER_SECRET" not in (job.get("env") or {})
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith(MODEL_ACTION):
                continue
            env = step.get("env") or {}
            assert "BUREAU_READ_TOKEN_BROKER_SECRET" not in env, step.get("name")


# ===========================================================================
# 3. The print-and-stop step: after the last model step, under always()
# ===========================================================================

@builds
def test_the_print_and_stop_step_follows_the_last_model_step_under_always(build):
    steps = _steps(build)
    stop = _stop_index(steps)
    models = _model_indexes(steps)
    assert steps[models[-1]].get("id") == BUILDS[build]["named"][-1]
    assert stop > models[-1]
    assert str(steps[stop].get("if", "")).replace("${{", "").strip().startswith("always()")
    run = steps[stop]["run"]
    assert f"{BROKER} print-lines --file {LINES_FILE}" in run
    assert f"{BROKER} stop --state-file {STATE_FILE}" in run
    assert "${{" not in run, "substitutions go in env: (DRE-3484)"


def test_agent_fix_broker_steps_stand_down_when_the_unfixable_gate_escalates():
    """agent-fix.yml's unfixable-checks gate skips every step after it when it
    escalates (tests/test_unfixable_check_escalation.py); both broker steps
    say so in their own `if:`."""
    steps = _steps("agent-fix")
    guard = "steps.unfixable.outputs.escalate != 'true'"
    for i in (_start_index(steps), _stop_index(steps)):
        assert guard in str(steps[i].get("if", "")), steps[i]["name"]


@builds
def test_the_only_step_naming_the_lines_file_after_the_model_steps_prints_it_filtered(build):
    steps = _steps(build)
    last = _model_indexes(steps)[-1]
    naming = [i for i, s in enumerate(steps)
              if i > last and LINES_NAME in (s.get("run") or "")]
    assert naming == [_stop_index(steps)], naming
    for line in steps[naming[0]]["run"].splitlines():
        if LINES_NAME in line:
            assert f"{BROKER} print-lines" in line, line


@builds
def test_no_step_passes_the_lines_file_to_a_raw_printer(build):
    for job in _doc(build)["jobs"].values():
        for step in job.get("steps") or []:
            for line in (step.get("run") or "").splitlines():
                if LINES_NAME in line or "BUREAU_READ_LINES_FILE" in line:
                    assert not re.search(r"\b(cat|echo|tee|printf)\b", line), (
                        step.get("name"), line)


# ===========================================================================
# 4. The two steps, run: the real broker starts, masks, prints and stops
# ===========================================================================

def _env(build: str, tmp: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ACTIONS_ID_TOKEN_", "BUREAU_READ", "GITHUB_"))}
    env.update(RUNNER_TEMP=str(tmp), GITHUB_OUTPUT=str(tmp / "gh_output"),
               GITHUB_WORKFLOW=BUILDS[build]["workflow"], GITHUB_JOB=BUILDS[build]["job"],
               PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def _run(step: dict, tmp: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp, env=env,
                          capture_output=True, text=True, timeout=60)


def _outputs(tmp: Path) -> dict:
    path = tmp / "gh_output"
    if not path.exists():
        return {}
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines() if "=" in ln)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / ".bureau-pipeline").symlink_to(ROOT)
    yield tmp_path
    if (tmp_path / "read-token-broker.json").exists():
        subprocess.run(["python3", str(ROOT / "scripts" / "read_token_broker.py"), "stop",
                        "--state-file", str(tmp_path / "read-token-broker.json")],
                       capture_output=True, timeout=30)


def _oidc(build: str, tmp: Path) -> dict:
    return _env(build, tmp, ACTIONS_ID_TOKEN_REQUEST_URL="http://127.0.0.1:9/oidc",
                ACTIONS_ID_TOKEN_REQUEST_TOKEN="request-token")


@builds
def test_the_start_step_hands_the_url_and_a_masked_secret_to_its_outputs(build, workspace):
    proc = _run(_start(build), workspace, _oidc(build, workspace))
    assert proc.returncode == 0, proc.stderr
    out = _outputs(workspace)
    secret, url = out.get("secret"), out.get("url")
    assert secret and url and url.startswith("http://127.0.0.1:"), out
    printed = proc.stdout.splitlines()
    mentions = [ln for ln in printed if secret in ln]
    assert mentions == [f"::add-mask::{secret}"], mentions
    assert secret not in proc.stderr
    assert "request-token" not in proc.stdout + proc.stderr


@builds
def test_a_broker_that_cannot_start_hands_the_steps_nothing(build, workspace):
    proc = _run(_start(build), workspace, _env(build, workspace))
    assert proc.returncode != 0, "continue-on-error carries the job past it"
    assert "secret" not in _outputs(workspace)


@builds
def test_the_print_and_stop_step_prints_only_this_jobs_lines_and_stops(build, workspace):
    assert _run(_start(build), workspace, _oidc(build, workspace)).returncode == 0
    pid = int(re.search(r'"pid":\s*(\d+)',
                        (workspace / "read-token-broker.json").read_text()).group(1))
    assert _alive(pid)
    spec = BUILDS[build]
    good = (f'read-fallback: reader="{spec["workflow"]} / {spec["job"]} / linear_ops.py" '
            f"read=cards reason=no-token process=1@{spec['named'][0]}-ab12")
    other = 'read-fallback: reader="Plan / plan / linear_ops.py" read=cards reason=no-token'
    (workspace / LINES_NAME).write_text(f"{good}\n{other}\n{FORGED}\n")
    proc = _run(_stop(build), workspace, _env(build, workspace))
    assert proc.returncode == 0, proc.stderr
    assert f"{good}\n" in proc.stdout
    assert other not in proc.stdout, "a line may speak only for the job that prints it"
    assert FORGED not in proc.stdout
    assert "agent read lines: kept 1, dropped 2" in proc.stdout
    assert "read-token-broker: stopped" in proc.stdout
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


@builds
def test_a_missing_lines_file_prints_one_line_and_does_not_fail(build, workspace):
    proc = _run(_stop(build), workspace, _env(build, workspace))
    assert proc.returncode == 0, proc.stderr
    no_file = [ln for ln in proc.stdout.splitlines() if ln.startswith("agent read lines:")]
    assert no_file == [f"agent read lines: no file at {workspace / LINES_NAME}"], proc.stdout
