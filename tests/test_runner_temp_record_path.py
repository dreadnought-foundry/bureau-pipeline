"""RED-first: a run's model-step record is read from the runner's own temp
folder, never from a path only GitHub's own machines have (DRE-6994).

The model action writes every model step's record to ONE file,
`claude-execution-output.json` in the runner's temp folder. A step handed no
pointer (`steps.<id>.outputs.execution_file` is set only for a step that ran)
fell back to the literal `/home/runner/work/_temp/…`, which is the temp folder
on GitHub's own Linux machines and nowhere else: on RunsOn it is
`/home/runner/_work/_temp`, on the Mac mini's container runners `/_work/_temp`.

Planning run 37654166914 (2026-10-07, RunsOn) printed `Log saved to
/home/runner/_work/_temp/claude-execution-output.json` twice, then its death
receipt fell back to GitHub's folder and said no record was written. After
DRE-6985 that printed line is what marks a real usage limit, so on those
machines a limit in a critic or re-plan step was retried into.

What this file pins:

  1. **The guard.** No file under `.github/workflows/` or `.github/actions/`
     names `/home/runner/work/_temp`; a failure names each file and line.
  2. **The planner's receipt, run for real.** The script of plan.yml's "Keep
     the run's death-cause receipt", with an empty pointer and a temp folder
     that is not GitHub's, hands `death_receipt.py emit` the record in THAT
     folder; a pointer it is handed still wins. A usage-limit record there
     prints `Run death: api_death · wall capped`, which the medic's reader
     (`dead_run.limit_kind`) reads as `"claude"` after the log prefix. An
     empty folder still prints the "no execution record" line.
  3. **The groomer's verdict** falls back to `runner.temp` in its expression.
  4. **The Report steps' scripts.** agent-task's and agent-fix's Report steps
     run `scripts/report_agent_result.sh` and `scripts/report_fix_result.sh`,
     which classify the death from the same fallback; neither names it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_runner_temp_record_path.py -v
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dead_run  # noqa: E402 — the medic's limit reader (DRE-6985)

GITHUB_ONLY_TEMP = "/home/runner/work/_temp"
GUARDED = (ROOT / ".github" / "workflows", ROOT / ".github" / "actions")
# The Report steps' scripts, moved out of agent-task.yml and agent-fix.yml
# (DRE-5223, DRE-5225), read the record the same way.
REPORT_SCRIPTS = (ROOT / "scripts" / "report_agent_result.sh",
                  ROOT / "scripts" / "report_fix_result.sh")
PLAN = ROOT / ".github" / "workflows" / "plan.yml"
GROOMER = ROOT / ".github" / "workflows" / "groomer.yml"
USAGE_LIMIT = ROOT / "tests" / "fixtures" / "usage-limit-death.json"
RECORD = "claude-execution-output.json"
# `gh run view --log-failed`'s prefix, as tests/test_credential_rotation.py
# gives it.
GH_PREFIX = "call / bureau-card: DRE-0000\tUNKNOWN STEP\t2026-10-07T16:57:14.4319587Z "

# Records every python3 call the step makes, then runs the real interpreter
# on the real script, so the receipt is the one a runner would print.
SHIM = r"""#!/bin/bash
printf '%s\n' "$*" >> "$SHIM_LOG"
exec "$REAL_PYTHON" "$@"
"""


# --------------------------------------------------------------------------
# 1. the guard
# --------------------------------------------------------------------------

def _github_only_paths() -> list[str]:
    found = []
    for root in GUARDED:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if GITHUB_ONLY_TEMP in line:
                    found.append(f"{path.relative_to(ROOT)}:{n}")
    return found


def test_no_workflow_or_action_names_githubs_own_temp_folder():
    found = _github_only_paths()
    assert not found, (
        f"{GITHUB_ONLY_TEMP} is the runner's temp folder on GitHub's own Linux "
        "machines only — read $RUNNER_TEMP (runner.temp in an expression) "
        "instead. Found at:\n  " + "\n  ".join(found))


def test_the_guard_reads_both_folders():
    for root in GUARDED:
        assert root.is_dir() and any(root.rglob("*.yml")), root


# --------------------------------------------------------------------------
# 2. the planner's receipt, run for real
# --------------------------------------------------------------------------

def _receipt_step() -> dict:
    with open(PLAN, encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    for job in plan["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == "Keep the run's death-cause receipt":
                return step
    raise AssertionError("plan.yml has no \"Keep the run's death-cause receipt\" step")


def run_receipt(tmp_path: Path, *, record: Path | None = None,
                pointer: str = "") -> dict:
    """Execute the receipt step's script as a runner whose temp folder is
    `tmp_path/_work/_temp` — not GitHub's — with `record` copied in as the
    model action leaves it. Returns the python3 calls, the printed output
    and the folder."""
    step = _receipt_step()
    assert "${{" not in step["run"], "every expression stays in env:"
    temp = tmp_path / "_work" / "_temp"
    temp.mkdir(parents=True)
    if record is not None:
        shutil.copy(record, temp / RECORD)
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(ROOT)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").write_text(SHIM)
    os.chmod(bin_dir / "python3", 0o755)
    calls = tmp_path / "calls.log"
    calls.touch()
    out = tmp_path / "github_output"
    out.touch()
    summary = tmp_path / "step_summary"
    summary.touch()
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=work, capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(temp),
            "CLAUDE_EXECUTION_FILE": pointer,
            "MODEL_RAN": "true",
            "JOB_STATUS": "failure",
            "CARD": "DRE-0000",
            "CLAUDE_AUTH_MODE": "",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "SHIM_LOG": str(calls),
            "REAL_PYTHON": sys.executable,
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return {
        "calls": [c for c in calls.read_text().splitlines() if c],
        "stdout": proc.stdout,
        "temp": temp,
    }


def _execution_file(result: dict) -> str:
    emits = [c for c in result["calls"] if "death_receipt.py emit" in c]
    assert len(emits) == 1, result["calls"]
    match = re.search(r"--execution-file (\S+)", emits[0])
    assert match, emits[0]
    return match.group(1)


def test_an_empty_pointer_reads_the_runners_own_temp_folder(tmp_path):
    result = run_receipt(tmp_path)
    assert _execution_file(result) == str(result["temp"] / RECORD)


def test_a_pointer_it_is_handed_is_used_unchanged(tmp_path):
    other = str(tmp_path / "elsewhere" / "planner-record.json")
    assert _execution_file(run_receipt(tmp_path, pointer=other)) == other


def test_a_usage_limit_in_that_folder_prints_wall_capped(tmp_path):
    out = run_receipt(tmp_path, record=USAGE_LIMIT)["stdout"]
    assert any(line.startswith("Run death: api_death · wall capped")
               for line in out.splitlines()), out


def test_the_printed_line_reads_as_a_claude_limit_after_the_log_prefix(tmp_path):
    out = run_receipt(tmp_path, record=USAGE_LIMIT)["stdout"]
    log = "".join(f"{GH_PREFIX}{line}\n" for line in out.splitlines())
    assert dead_run.limit_kind(log) == "claude"


def test_a_folder_holding_no_record_still_says_none_was_written(tmp_path):
    out = run_receipt(tmp_path)["stdout"]
    assert any(line.startswith(
        "Run death: unknown — the run died and no execution record was written")
        for line in out.splitlines()), out


# --------------------------------------------------------------------------
# 3. the groomer's verdict
# --------------------------------------------------------------------------

def _groomer_steps(name: str) -> list[dict]:
    with open(GROOMER, encoding="utf-8") as f:
        groomer = yaml.safe_load(f)
    steps = [s for job in groomer["jobs"].values() for s in job.get("steps") or []
             if s.get("name") == name]
    assert steps, f"groomer.yml has no {name!r} step"
    return steps


def test_the_groomer_verdict_falls_back_to_runner_temp():
    (step,) = _groomer_steps("Write the verdict")
    value = step["env"]["EXECUTION_FILE"]
    assert "steps.claude.outputs.execution_file" in value, value
    assert "runner.temp" in value, value
    assert GITHUB_ONLY_TEMP not in value, value


def test_every_groomer_receipt_reads_the_runners_temp_folder():
    for step in _groomer_steps("Keep the run's death-cause receipt"):
        assert f"${{RUNNER_TEMP:-/tmp}}/{RECORD}" in step["run"], step["run"]


# --------------------------------------------------------------------------
# 4. the Report steps' scripts
# --------------------------------------------------------------------------

def test_the_report_scripts_read_the_runners_temp_folder():
    for path in REPORT_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert GITHUB_ONLY_TEMP not in text, path.name
        assert f"${{RUNNER_TEMP:-/tmp}}/{RECORD}" in text, path.name
