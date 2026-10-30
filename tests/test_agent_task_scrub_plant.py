"""A trusted workflow step plants the throwaway probe in the build's working log (DRE-8467).

The agent-log scrub (DRE-4268) has a by-value pass no stored working log has
ever exercised: nothing a build agent does puts a registered secret's value in
its transcript, and an agent rightly refuses a card that asks it to `echo` one
— card text is untrusted, and a card that wanted to leak a real secret would
read the same. DRE-7513 put the throwaway `BUREAU_PLANT_PROBE` in the agent's
shell; this is the trusted route that puts it in the transcript instead.

One step in agent-task.yml's `execute` job, after the agent steps and before
`Keep the run's working log`, gated on the repository variable
`BUREAU_PLANT_PROBE_APPEND` being `true` and on the probe not being empty. It
appends ONE record, `"type": "scrub-plant"`, to the execution file the upload
step scrubs, holding the probe's value (the by-value plant) and a fixed
made-up Anthropic-shaped key that is no registered secret (the by-shape plant).

Pinned here:

  * the step's wiring — where it sits, its gate, the file it writes, the one
    secret it reads, and the comment that says it is for one sandbox run;
  * what it writes — its own shell, run as Actions runs it: one plant record
    with both strings when the variable is `true` and the probe is set, none
    when the variable is unset or the probe is empty, and neither value
    printed;
  * what the scrub makes of it — the planted file, run through
    `upload_agent_log`'s own scrub call exactly as the upload step makes it:
    the probe replaced by its by-value marker, the key by its by-shape marker,
    and a summary whose `by_value` counts `BUREAU_PLANT_PROBE`.

Run: python3 -m pytest tests/test_agent_task_scrub_plant.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / ".github" / "workflows" / "agent-task.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import upload_agent_log  # noqa: E402
from secret_shapes import SHAPES  # noqa: E402

PLANT_SCRIPT = "plant_scrub_probe.py"
UPLOAD_SCRIPT = "upload_agent_log.py"
WORKING_LOG = "Keep the run's working log"
AGENT_ACTION = "anthropics/claude-code-action"

PROBE = "BUREAU_PLANT_PROBE"
PROBE_REF = "${{ secrets.BUREAU_PLANT_PROBE }}"
SWITCH = "BUREAU_PLANT_PROBE_APPEND"
SWITCH_REF = "${{ vars.BUREAU_PLANT_PROBE_APPEND }}"
PLANT_IF = "always() && vars.BUREAU_PLANT_PROBE_APPEND == 'true'"

#: A probe value the test owns. Long enough for the scrub's by-value pass
#: (`scrub_agent_log.MIN_VALUE_LENGTH`), shaped like nothing in SHAPES.
PROBE_VALUE = "plant-probe-DRE8467-throwaway-value"

SECRET_RE = re.compile(r"\bsecrets\.([A-Za-z0-9_]+)")
EXPR_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def load() -> dict:
    return yaml.safe_load(TASK.read_text(encoding="utf-8"))


def execute_steps(doc: dict) -> list[dict]:
    return doc["jobs"]["execute"].get("steps") or []


def index_of(steps: list[dict], match) -> list[int]:
    return [i for i, s in enumerate(steps) if match(s)]


def plant_step(doc: dict) -> dict:
    found = [s for s in execute_steps(doc) if PLANT_SCRIPT in str(s.get("run") or "")]
    assert len(found) == 1, f"expected exactly one {PLANT_SCRIPT} step in job execute"
    return found[0]


def upload_step(doc: dict) -> dict:
    found = [s for s in execute_steps(doc) if UPLOAD_SCRIPT in str(s.get("run") or "")]
    assert len(found) == 1, f"expected exactly one {UPLOAD_SCRIPT} step in job execute"
    return found[0]


def comment_above(name: str) -> str:
    """The comment block directly above the step called `name` in the raw file."""
    lines = TASK.read_text(encoding="utf-8").splitlines()
    at = lines.index(f"      - name: {name}")
    block = []
    for line in reversed(lines[:at]):
        if not line.strip().startswith("#"):
            break
        block.append(line.strip().lstrip("#").strip())
    return " ".join(reversed(block))


# --------------------------------------------------------------------------- #
# The wiring                                                                   #
# --------------------------------------------------------------------------- #

class TestThePlantStepIsWired:

    def setup_method(self):
        self.doc = load()
        self.steps = execute_steps(self.doc)
        self.step = plant_step(self.doc)

    def test_it_sits_after_every_agent_step_and_before_the_working_log(self):
        at = self.steps.index(self.step)
        agents = index_of(self.steps, lambda s: str(s.get("uses") or "").startswith(AGENT_ACTION + "@"))
        upload = index_of(self.steps, lambda s: UPLOAD_SCRIPT in str(s.get("run") or ""))
        assert agents and at > max(agents)
        assert upload and at < upload[0]

    def test_it_runs_only_when_the_variable_is_true(self):
        # `always()` like the working log it feeds: a dead agent's log is the
        # one kept, so the plant must reach it too. Without the variable the
        # step is skipped and the job does today's work.
        assert self.step.get("if") == PLANT_IF

    def test_it_never_changes_the_jobs_conclusion(self):
        assert self.step.get("continue-on-error") is True

    def test_it_reads_the_probe_and_the_switch_from_env(self):
        env = self.step.get("env") or {}
        assert env.get(PROBE) == PROBE_REF
        assert env.get(SWITCH) == SWITCH_REF

    def test_it_reads_no_other_secret(self):
        read = set()
        for value in (self.step.get("env") or {}).values():
            read.update(SECRET_RE.findall(str(value)))
        assert read == {PROBE}

    def test_every_expression_is_in_env_never_in_the_script(self):
        assert "${{" not in str(self.step.get("run"))

    def test_it_writes_the_file_the_upload_step_scrubs(self):
        mine = (self.step.get("env") or {}).get("CLAUDE_EXECUTION_FILE")
        theirs = (upload_step(self.doc).get("env") or {}).get("CLAUDE_EXECUTION_FILE")
        assert mine and mine == theirs
        fallback = 'EXEC_FILE="${CLAUDE_EXECUTION_FILE:-${RUNNER_TEMP:-/tmp}/claude-execution-output.json}"'
        assert fallback in str(self.step.get("run"))
        assert fallback in str(upload_step(self.doc).get("run"))

    def test_the_variable_is_read_by_this_step_alone(self):
        raw = TASK.read_text(encoding="utf-8")
        assert raw.count(f"vars.{SWITCH}") == 2  # the `if:` and the env line
        for step in self.steps:
            if step is self.step:
                continue
            assert SWITCH not in json.dumps(step), step.get("name")

    def test_its_comment_names_the_cards_and_the_one_sandbox_run(self):
        text = comment_above(self.step["name"])
        assert "DRE-5357" in text and "DRE-7513" in text
        assert "sandbox" in text
        assert "single run" in text
        assert "removed" in text


# --------------------------------------------------------------------------- #
# What the step writes                                                         #
# --------------------------------------------------------------------------- #

#: A short transcript in the action's shape: the message list ending with the
#: result record.
TRANSCRIPT = [
    {"type": "system", "subtype": "init", "session_id": "s-1"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the card."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3},
]


def run_plant_step(tmp_path: Path, *, switch: str | None, probe: str | None,
                   transcript=TRANSCRIPT) -> tuple[subprocess.CompletedProcess, Path]:
    """The plant step's own shell, as Actions runs it (`bash -e`), with its
    `env:` rendered. Answers the process and the execution file."""
    step = plant_step(load())
    exec_file = tmp_path / "runner-temp" / "claude-execution-output.json"
    exec_file.parent.mkdir(parents=True)
    if transcript is not None:
        exec_file.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(ROOT)
    rendered = {
        "steps.claude_result.outputs.execution_file": str(exec_file),
        "vars.BUREAU_PLANT_PROBE_APPEND": switch or "",
        "secrets.BUREAU_PLANT_PROBE": probe or "",
    }
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "RUNNER_TEMP": str(exec_file.parent)}
    for name, value in (step.get("env") or {}).items():
        env[name] = EXPR_RE.sub(lambda m: rendered[m.group(1)], str(value))
    if switch is None:
        env.pop(SWITCH, None)
    proc = subprocess.run(  # nosec B603 B607 — this repo's own step, fixed argv
        ["bash", "-e", "-c", step["run"]], cwd=work, env=env,
        capture_output=True, text=True, timeout=60, check=False)
    return proc, exec_file


def plant_records(exec_file: Path) -> list[dict]:
    data = json.loads(exec_file.read_text(encoding="utf-8"))
    return [r for r in data if isinstance(r, dict) and r.get("type") == "scrub-plant"]


def key_shaped(record: dict) -> list[str]:
    return [v for v in record.values()
            if isinstance(v, str) and SHAPES["anthropic-key"].fullmatch(v)]


class TestWhatTheStepWrites:

    def test_switch_on_and_probe_set_appends_one_record_with_both_plants(self, tmp_path):
        proc, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        records = plant_records(exec_file)
        assert len(records) == 1
        assert PROBE_VALUE in records[0].values()
        shaped = key_shaped(records[0])
        assert len(shaped) == 1
        assert shaped[0] != PROBE_VALUE

    def test_the_transcript_is_kept_and_the_record_is_appended_last(self, tmp_path):
        _, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE)
        data = json.loads(exec_file.read_text(encoding="utf-8"))
        assert data[:-1] == TRANSCRIPT
        assert data[-1]["type"] == "scrub-plant"

    def test_the_step_prints_neither_value(self, tmp_path):
        proc, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE)
        shaped = key_shaped(plant_records(exec_file)[0])[0]
        printed = proc.stdout + proc.stderr
        assert PROBE_VALUE not in printed
        assert shaped not in printed
        assert not SHAPES["anthropic-key"].search(printed)

    @pytest.mark.parametrize("switch", [None, "", "false", "TRUE", "1"])
    def test_the_switch_not_true_plants_nothing(self, tmp_path, switch):
        proc, exec_file = run_plant_step(tmp_path, switch=switch, probe=PROBE_VALUE)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert plant_records(exec_file) == []
        assert json.loads(exec_file.read_text(encoding="utf-8")) == TRANSCRIPT

    def test_an_empty_probe_plants_nothing(self, tmp_path):
        proc, exec_file = run_plant_step(tmp_path, switch="true", probe="")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert plant_records(exec_file) == []
        assert json.loads(exec_file.read_text(encoding="utf-8")) == TRANSCRIPT

    def test_no_execution_file_creates_none(self, tmp_path):
        # A bounced or duplicate card never ran a model: there is no log to
        # keep, and the plant must not invent one for the upload step to find.
        proc, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE,
                                         transcript=None)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not exec_file.exists()

    def test_a_file_that_is_not_the_message_list_is_left_alone(self, tmp_path):
        proc, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE,
                                         transcript={"type": "result", "is_error": False})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert json.loads(exec_file.read_text(encoding="utf-8")) == {
            "type": "result", "is_error": False}


# --------------------------------------------------------------------------- #
# What the scrub makes of it                                                   #
# --------------------------------------------------------------------------- #

def scrub_as_the_upload_step_does(tmp_path: Path, exec_file: Path) -> tuple[str, dict]:
    """The upload step's scrub, by its own code path: every `SCRUB_` variable
    the step's env names (the probe given the planted value, the rest empty
    as on a repo that does not hold them), through `secrets_from_env` and
    `run_scrub`. Answers the scrubbed text and the scrub's summary."""
    env = {}
    for name, value in (upload_step(load()).get("env") or {}).items():
        if name.startswith("SCRUB_"):
            env[name] = PROBE_VALUE if value == PROBE_REF else ""
    secrets = upload_agent_log.secrets_from_env("SCRUB_", env)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        scrubbed = upload_agent_log.run_scrub(
            exec_file, tmp_path / "agent-log", secrets, upload_agent_log.SCRUB)
    line = out.getvalue().strip()
    assert line.startswith("scrub: "), line
    return scrubbed.read_text(encoding="utf-8"), json.loads(line[len("scrub: "):])


class TestTheScrubRedactsBothPlants:

    def test_both_plants_come_out_as_their_markers(self, tmp_path):
        _, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE)
        shaped = key_shaped(plant_records(exec_file)[0])[0]
        text, _ = scrub_as_the_upload_step_does(tmp_path, exec_file)
        assert PROBE_VALUE not in text
        assert shaped not in text
        [record] = plant_records_from_text(text)
        assert f"«redacted:{PROBE}»" in record.values()
        assert "«redacted-shape:anthropic-key»" in record.values()

    def test_the_summary_counts_the_probe_by_value_and_the_key_by_shape(self, tmp_path):
        _, exec_file = run_plant_step(tmp_path, switch="true", probe=PROBE_VALUE)
        _, summary = scrub_as_the_upload_step_does(tmp_path, exec_file)
        assert summary["by_value"], "an empty by_value is the failure this card exists to rule out"
        assert summary["by_value"].get(PROBE, 0) >= 1
        assert summary["by_shape"].get("anthropic-key", 0) >= 1

    def test_without_the_plant_by_value_stays_empty(self, tmp_path):
        # Non-vacuous: the same scrub over the same transcript, unplanted,
        # reports the `by_value: {}` that reads exactly like success.
        _, exec_file = run_plant_step(tmp_path, switch=None, probe=PROBE_VALUE)
        _, summary = scrub_as_the_upload_step_does(tmp_path, exec_file)
        assert summary["by_value"] == {}


def plant_records_from_text(text: str) -> list[dict]:
    data = json.loads(text)
    return [r for r in data if isinstance(r, dict) and r.get("type") == "scrub-plant"]
