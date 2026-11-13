"""Every agent session that runs with the read door on reads through the token
broker, in every workflow, and the proof declaration names every workflow
whose log carries its read lines (DRE-9255).

A model session is never handed the runner's OIDC request variables, so a
session whose `linear_ops` reads ask the door, with no broker beside it, falls
back on every one with `reason=no-token`, in lines that reach only its
transcript. DRE-9169, DRE-9252, DRE-9253 and DRE-9255 wired the broker
(`scripts/read_token_broker.py`, DRE-9168) into the 25 such steps `main`
held — plan.yml's 13, the build jobs' 4, the checking jobs' 5 and the repair
jobs' 3, in 8 workflows. Each was found by reading the files, and this test
reads them the same way, so a session added later cannot fall back out of
sight again:

  * It reads every file under `.github/workflows/` and DISCOVERS every step
    whose `uses:` is `anthropics/claude-code-action` and whose step or job
    `env:` sets `BUREAU_READ`. Nothing here lists them.
  * Each such step's `env:` carries `BUREAU_READ_TOKEN_BROKER`,
    `BUREAU_READ_TOKEN_BROKER_SECRET` and `BUREAU_READ_LINES_FILE`, and its
    job has a `read_token_broker.py print-lines` step after its last model
    step. A failure names the workflow, the job and the step's `id:`.
  * `config/proof-reach.json`'s `agent read lines:` entry names, by workflow
    name and file, every workflow holding a discovered step, so a ninth one
    wired later fails here until the entry names it.

A model step that reads Linear with the door off is outside the set: its
reads are direct reads, not fallbacks.

The check is proved against doctored copies of a real workflow: one with the
broker variables taken off one step, one with the print step removed, and one
with a new unwired session added.

Run: python3 -m pytest tests/test_every_session_read_token_broker.py -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
DECLARATION = ROOT / "config" / "proof-reach.json"

MODEL_ACTION = "anthropics/claude-code-action"
BROKER_VARIABLES = ("BUREAU_READ_TOKEN_BROKER", "BUREAU_READ_TOKEN_BROKER_SECRET",
                    "BUREAU_READ_LINES_FILE")
PRINT_LINES = "read_token_broker.py print-lines"
START = "read_token_broker.py start"


@dataclass(frozen=True)
class Session:
    workflow: str  # the file's name under .github/workflows/
    job: str
    step_id: str

    def __str__(self) -> str:
        return f"{self.workflow} / {self.job} / {self.step_id}"


def _workflow_files(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.suffix in (".yml", ".yaml"))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _is_model(step: dict) -> bool:
    return str(step.get("uses", "")).startswith(MODEL_ACTION)


def _door_on(job: dict, step: dict) -> bool:
    return "BUREAU_READ" in (step.get("env") or {}) or "BUREAU_READ" in (job.get("env") or {})


def discover(root: Path) -> list[tuple[Session, dict, dict]]:
    """Every model step under `root` that runs with `BUREAU_READ` set."""
    found = []
    for path in _workflow_files(root):
        for job_name, job in (_load(path).get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if _is_model(step) and _door_on(job, step):
                    found.append((Session(path.name, job_name, str(step.get("id"))), job, step))
    return found


def unwired(root: Path) -> list[str]:
    """One message per discovered session that is not wired to the broker."""
    problems = []
    for session, job, step in discover(root):
        env = step.get("env") or {}
        missing = [v for v in BROKER_VARIABLES if v not in env]
        if missing:
            problems.append(f"{session}: env: does not carry {', '.join(missing)}")
        steps = job.get("steps") or []
        last_model = max(i for i, s in enumerate(steps) if _is_model(s))
        if not any(PRINT_LINES in (s.get("run") or "") for s in steps[last_model + 1:]):
            problems.append(f"{session}: no `{PRINT_LINES}` step after the job's last model step")
    return problems


def _workflow_name(path: Path) -> str:
    return re.sub(r"\s*\(reusable\)$", "", str(_load(path).get("name") or "")).strip()


def _when() -> str:
    entries = json.loads(DECLARATION.read_text(encoding="utf-8"))["log_lines"]
    (entry,) = [e for e in entries if e["tag"] == "agent read lines:"]
    return entry["when"]


# ===========================================================================
# 1. On the branch: every session that asks the door is wired
# ===========================================================================

def test_the_sessions_are_discovered_from_the_files():
    sessions = [s for s, _, _ in discover(WORKFLOWS)]
    # The 25 steps `main` held when the card was built, in 8 workflows. A
    # session added later raises these counts and must be wired to pass
    # below; the floor only proves the discovery found what was read by hand.
    assert len(sessions) >= 25, [str(s) for s in sessions]
    assert len({s.workflow for s in sessions}) >= 8
    for named in (Session("plan.yml", "plan", "claude"),
                  Session("agent-task.yml", "execute", "claude"),
                  Session("agent-fix.yml", "fix", "claude"),
                  Session("proof-task.yml", "execute", "claude"),
                  Session("verify.yml", "verify", "verifier"),
                  Session("medic.yml", "diagnose", "claude"),
                  Session("alarm-repair.yml", "repair", "repair"),
                  Session("triage-repair.yml", "repair", "repair")):
        assert named in sessions, str(named)


def test_a_model_step_with_the_door_off_is_outside_the_set():
    sessions = {str(s) for s, _, _ in discover(WORKFLOWS)}
    # qa-review.yml's critic reads Linear with no BUREAU_READ in its env.
    assert "qa-review.yml / review / critic" not in sessions


def test_every_session_that_asks_the_door_reads_through_the_broker():
    assert unwired(WORKFLOWS) == []


def test_every_workflow_holding_a_session_is_named_in_the_declaration():
    when = _when()
    for workflow in sorted({s.workflow for s, _, _ in discover(WORKFLOWS)}):
        name = _workflow_name(WORKFLOWS / workflow)
        assert f"{name} (`{workflow}`)" in when, (
            f"config/proof-reach.json's `agent read lines:` entry does not name "
            f"{name} (`{workflow}`)")


def test_the_declaration_no_longer_says_the_planning_run_s_log_alone():
    when = _when()
    assert "once the card wiring it into plan.yml lands" not in when
    for name, file in (("Agent Plan", "plan.yml"), ("Agent Task", "agent-task.yml"),
                       ("Agent Fix", "agent-fix.yml"), ("Proof Task", "proof-task.yml"),
                       ("Verify", "verify.yml"), ("Pipeline Medic", "medic.yml"),
                       ("Alarm Repair", "alarm-repair.yml"),
                       ("Triage Repair", "triage-repair.yml")):
        assert f"{name} (`{file}`)" in when, name
    assert "print-lines" in when
    assert "agent read lines: kept <k>, dropped <d>" in when
    assert "agent read lines: no file at <path>" in when


def test_proof_reach_check_passes():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "proof_reach.py"), "check"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ===========================================================================
# 2. Against doctored copies: the check fails, naming the session
# ===========================================================================

@pytest.fixture
def doctored(tmp_path):
    for path in _workflow_files(WORKFLOWS):
        shutil.copy(path, tmp_path / path.name)
    return tmp_path


def _drop_lines(path: Path, predicate) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not predicate(ln)]
    assert len(kept) < len(lines), "the doctoring changed nothing"
    path.write_text("".join(kept), encoding="utf-8")


@pytest.mark.parametrize("session", [
    Session("medic.yml", "diagnose", "claude"),
    Session("alarm-repair.yml", "repair", "repair"),
    Session("triage-repair.yml", "repair", "repair"),
    Session("verify.yml", "verify", "verifier_retry"),
], ids=str)
def test_removing_the_broker_variables_from_one_step_fails_naming_it(doctored, session):
    path = doctored / session.workflow
    doc = _load(path)
    step = next(s for s in doc["jobs"][session.job]["steps"] if s.get("id") == session.step_id)
    for variable in BROKER_VARIABLES:
        step["env"].pop(variable)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    problems = unwired(doctored)
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{session.workflow} / {session.job} / {session.step_id}:")
    for variable in BROKER_VARIABLES:
        assert variable in problems[0]


def test_removing_one_variable_by_text_edit_fails_naming_the_step(doctored):
    # The same doctoring as a person's edit to the file would make it.
    _drop_lines(doctored / "triage-repair.yml",
                lambda ln: ln.strip().startswith("BUREAU_READ_LINES_FILE:"))
    assert unwired(doctored) == [
        "triage-repair.yml / repair / repair: env: does not carry BUREAU_READ_LINES_FILE"]


def test_removing_the_print_lines_step_fails_naming_the_step(doctored):
    path = doctored / "medic.yml"
    doc = _load(path)
    steps = doc["jobs"]["diagnose"]["steps"]
    doc["jobs"]["diagnose"]["steps"] = [s for s in steps if PRINT_LINES not in (s.get("run") or "")]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == [
        f"medic.yml / diagnose / claude: no `{PRINT_LINES}` step after the job's last model step"]


def test_a_print_lines_step_before_the_last_model_step_does_not_count(doctored):
    path = doctored / "verify.yml"
    doc = _load(path)
    steps = doc["jobs"]["verify"]["steps"]
    printer = next(s for s in steps if PRINT_LINES in (s.get("run") or ""))
    steps.remove(printer)
    first_model = next(i for i, s in enumerate(steps) if _is_model(s))
    steps.insert(first_model + 1, printer)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    problems = unwired(doctored)
    assert [p.split(":")[0] for p in problems] == [
        "verify.yml / verify / verifier", "verify.yml / verify / verifier_retry"], problems


def test_a_new_session_with_the_door_on_and_no_broker_fails(doctored):
    (doctored / "new-session.yml").write_text(yaml.safe_dump({
        "name": "New Session",
        "on": {"workflow_dispatch": {}},
        "jobs": {"work": {
            "runs-on": "ubuntu-latest",
            "env": {"BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}"},
            "steps": [{"id": "agent", "uses": f"{MODEL_ACTION}@v1", "with": {"prompt": "hi"}}],
        }},
    }, sort_keys=False), encoding="utf-8")
    problems = unwired(doctored)
    assert [p.split(":")[0] for p in problems] == ["new-session.yml / work / agent"] * 3, problems


def test_a_new_session_under_a_workflow_level_door_and_no_broker_fails(doctored):
    # GitHub merges `env:` workflow → job → step, so a door opened for the
    # whole workflow is open for the session too.
    (doctored / "new-session.yml").write_text(yaml.safe_dump({
        "name": "New Session",
        "on": {"workflow_dispatch": {}},
        "env": {"BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}"},
        "jobs": {"work": {
            "runs-on": "ubuntu-latest",
            "steps": [{"id": "agent", "uses": f"{MODEL_ACTION}@v1", "with": {"prompt": "hi"}}],
        }},
    }, sort_keys=False), encoding="utf-8")
    assert "new-session.yml / work / agent" in {str(s) for s, _, _ in discover(doctored)}
    problems = unwired(doctored)
    assert [p.split(":")[0] for p in problems] == ["new-session.yml / work / agent"] * 3, problems


def test_a_new_session_with_the_variables_and_no_start_step_fails(doctored):
    (doctored / "new-session.yml").write_text(yaml.safe_dump({
        "name": "New Session",
        "on": {"workflow_dispatch": {}},
        "jobs": {"work": {
            "runs-on": "ubuntu-latest",
            "env": {"BUREAU_READ": "${{ vars.BUREAU_READ || 'off' }}"},
            "steps": [
                {"id": "agent", "uses": f"{MODEL_ACTION}@v1", "with": {"prompt": "hi"}, "env": {
                    "BUREAU_READ_TOKEN_BROKER": "${{ steps.read_broker.outputs.url }}",
                    "BUREAU_READ_TOKEN_BROKER_SECRET": "${{ steps.read_broker.outputs.secret }}",
                    "BUREAU_READ_LINES_FILE": "${{ runner.temp }}/agent-read-lines.log"}},
                {"name": "print", "if": "always()", "run":
                 "python3 .bureau-pipeline/scripts/read_token_broker.py print-lines"
                 ' --file "$RUNNER_TEMP/agent-read-lines.log"'},
            ],
        }},
    }, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == [
        f"new-session.yml / work / agent: no `{START}` step in its job"]


@pytest.mark.parametrize("session", [
    Session("medic.yml", "diagnose", "claude"),
    Session("alarm-repair.yml", "repair", "repair"),
    Session("triage-repair.yml", "repair", "repair"),
], ids=str)
def test_removing_the_start_step_fails_naming_the_step(doctored, session):
    path = doctored / session.workflow
    doc = _load(path)
    job = doc["jobs"][session.job]
    job["steps"] = [s for s in job["steps"] if START not in (s.get("run") or "")]
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == [f"{session}: no `{START}` step in its job"]


def test_a_start_step_after_the_session_does_not_count(doctored):
    path = doctored / "alarm-repair.yml"
    doc = _load(path)
    steps = doc["jobs"]["repair"]["steps"]
    starter = next(s for s in steps if START in (s.get("run") or ""))
    steps.remove(starter)
    model = next(i for i, s in enumerate(steps) if _is_model(s))
    steps.insert(model + 1, starter)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == [
        f"alarm-repair.yml / repair / repair: the job's `{START}` step does not come before it"]


def test_a_misspelled_start_step_id_in_the_values_fails_naming_the_step(doctored):
    # A one-character slip copying the block: GitHub resolves an unknown step
    # id to the empty string, and the session falls back on every read.
    path = doctored / "triage-repair.yml"
    doc = _load(path)
    step = next(s for s in doc["jobs"]["repair"]["steps"] if s.get("id") == "repair")
    for variable in ("BUREAU_READ_TOKEN_BROKER", "BUREAU_READ_TOKEN_BROKER_SECRET"):
        step["env"][variable] = step["env"][variable].replace("read_broker", "read_brokr")
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == [
        "triage-repair.yml / repair / repair: BUREAU_READ_TOKEN_BROKER is not "
        "`${{ steps.read_broker.outputs.url }}`",
        "triage-repair.yml / repair / repair: BUREAU_READ_TOKEN_BROKER_SECRET is not "
        "`${{ steps.read_broker.outputs.secret }}`"]


def test_a_new_session_in_a_wired_job_naming_a_missing_step_fails(doctored):
    # The critic's case B: a session added to plan.yml's job beside the 13
    # wired ones, its variables naming a step id that does not exist.
    path = doctored / "plan.yml"
    doc = _load(path)
    steps = doc["jobs"]["plan"]["steps"]
    last = max(i for i, s in enumerate(steps) if _is_model(s))
    steps.insert(last + 1, {"id": "new_agent", "uses": f"{MODEL_ACTION}@v1", "with": {"prompt": "hi"},
                            "env": {"BUREAU_READ": "on",
                                    "BUREAU_READ_TOKEN_BROKER": "${{ steps.read_brokr.outputs.url }}",
                                    "BUREAU_READ_TOKEN_BROKER_SECRET":
                                        "${{ steps.read_brokr.outputs.secret }}",
                                    "BUREAU_READ_LINES_FILE": "${{ runner.temp }}/agent-read-lines.log"}})
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    problems = unwired(doctored)
    assert [p.split(":")[0] for p in problems] == ["plan.yml / plan / new_agent"] * 2, problems


def test_a_new_session_with_the_door_off_is_not_held(doctored):
    (doctored / "new-session.yml").write_text(yaml.safe_dump({
        "name": "New Session",
        "on": {"workflow_dispatch": {}},
        "jobs": {"work": {
            "runs-on": "ubuntu-latest",
            "steps": [{"id": "agent", "uses": f"{MODEL_ACTION}@v1", "with": {"prompt": "hi"}}],
        }},
    }, sort_keys=False), encoding="utf-8")
    assert unwired(doctored) == []
