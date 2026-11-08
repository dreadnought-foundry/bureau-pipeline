"""RED-first: no workflow names a context GitHub does not provide at its key
(DRE-8913).

`parts-count.yml` set `COUNT_JSON: ${{ runner.temp }}/parts-count.json` on its
`count` job's own `env:`. The `runner` context does not exist there — the
runner has not been chosen yet — so GitHub refused the whole file: every push
run on `main` died in seconds with no jobs, and the one-time count never ran.
The file was valid YAML, so `Workflow YAML parses` and the wiring tests passed
it, and the first reader that knew the rule was GitHub, after the merge.

`scripts/check_workflow_contexts.py` reads every workflow file against
GitHub's "Context availability" table. What this file holds shut:

  * the broken line is refused, naming the file, the job, the key and the
    context, and the CLI exits 1 on it;
  * the repaired `parts-count.yml`, and the whole live tree, pass — and the
    live tree with the broken line put back is exactly one finding;
  * `runner`, `steps` and `job` are refused at every job-level key but
    `steps`, `outputs` and `environment.url`, and `matrix` in a job's `if:`;
  * `inputs` at any job-level key, and `matrix` in a job's `env`, `runs-on`,
    `concurrency` and `name`, are never refused — 15 working workflows use
    them;
  * contexts are read inside `${{ }}` and in a bare `if:`, never inside a
    quoted string, a property name or a function name;
  * `tests.yml` runs it in `unit-result`, beside `Workflow YAML parses`.

Every fixture is written by the test itself.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_workflow_contexts.py -q
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PARTS_COUNT = WORKFLOWS / "parts-count.yml"
TESTS_WORKFLOW = WORKFLOWS / "tests.yml"
SCRIPT = ROOT / "scripts" / "check_workflow_contexts.py"
sys.path.insert(0, str(ROOT / "scripts"))

import check_workflow_contexts as cwc  # noqa: E402

TABLE_URL = ("https://docs.github.com/en/actions/reference/workflows-and-actions/"
             "contexts#context-availability")
BROKEN_LINE = "      COUNT_JSON: ${{ runner.temp }}/parts-count.json"


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _found(findings) -> list:
    return [(Path(f.file).name, f.job, f.key, f.context) for f in findings]


def _job_workflow(tmp_path: Path, job: dict, name: str = "fixture.yml") -> Path:
    doc = {"name": "Fixture", "on": {"workflow_dispatch": {}},
           "jobs": {"one": {"runs-on": "ubuntu-latest", **job,
                            "steps": job.get("steps", [{"run": "true"}])}}}
    path = tmp_path / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the defect, and its repair
# --------------------------------------------------------------------------
BROKEN = f"""\
name: Parts Count
on:
  workflow_dispatch:
jobs:
  count:
    runs-on: ubuntu-latest
    env:
      LINEAR_IDENTITY: fleet
{BROKEN_LINE}
    steps:
      - name: Count the copies of shared parts
        run: echo "$COUNT_JSON"
"""


def test_the_broken_line_is_refused_naming_file_job_key_and_context(tmp_path):
    path = _write(tmp_path / "workflows", "parts-count.yml", BROKEN)
    assert _found(cwc.check_file(path)) == [("parts-count.yml", "count", "env", "runner")]


def test_the_cli_exits_1_on_the_broken_line_and_names_it(tmp_path):
    _write(tmp_path / "workflows", "parts-count.yml", BROKEN)
    done = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "workflows")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1, done.stdout + done.stderr
    out = done.stdout + done.stderr
    for word in ("parts-count.yml", "count", "env", "runner"):
        assert word in out, (word, out)


def test_the_repaired_parts_count_passes():
    assert cwc.check_file(PARTS_COUNT) == []


def test_the_live_tree_passes_and_every_file_is_read():
    findings, stats = cwc.check_dir(WORKFLOWS)
    assert findings == []
    files = [p for p in WORKFLOWS.iterdir() if p.suffix in (".yml", ".yaml")]
    assert stats["files"] == len(files) and stats["files"] > 40
    assert stats["expressions"] > 1000, "the check read almost nothing"


def test_the_live_tree_with_the_broken_line_back_is_exactly_one_finding(tmp_path):
    copy = tmp_path / "workflows"
    shutil.copytree(WORKFLOWS, copy)
    text = PARTS_COUNT.read_text(encoding="utf-8")
    anchor = "      LINEAR_IDENTITY: fleet\n"
    assert text.count(anchor) == 1
    (copy / "parts-count.yml").write_text(
        text.replace(anchor, anchor + BROKEN_LINE + "\n"), encoding="utf-8")
    findings, _ = cwc.check_dir(copy)
    assert _found(findings) == [("parts-count.yml", "count", "env", "runner")]


def test_the_cli_passes_the_live_tree():
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------
# what is refused
# --------------------------------------------------------------------------
JOB_LEVEL_REFUSALS = {
    "env": lambda e: {"env": {"X": e}},
    "runs-on": lambda e: {"runs-on": e},
    "if": lambda e: {"if": e},
    "concurrency": lambda e: {"concurrency": {"group": e}},
    "name": lambda e: {"name": e},
    "timeout-minutes": lambda e: {"timeout-minutes": e},
    "strategy": lambda e: {"strategy": {"matrix": {"a": [e]}}},
}


@pytest.mark.parametrize("context", ["runner", "steps", "job"])
@pytest.mark.parametrize("key", sorted(JOB_LEVEL_REFUSALS))
def test_runner_steps_and_job_are_refused_at_job_level_keys(tmp_path, key, context):
    path = _job_workflow(tmp_path, JOB_LEVEL_REFUSALS[key](f"${{{{ {context}.x }}}}"))
    assert _found(cwc.check_file(path)) == [("fixture.yml", "one", key, context)]


@pytest.mark.parametrize("value", ["matrix.os == 'linux'", "${{ matrix.os == 'linux' }}"])
def test_matrix_is_refused_in_a_job_if(tmp_path, value):
    path = _job_workflow(tmp_path, {"if": value})
    assert _found(cwc.check_file(path)) == [("fixture.yml", "one", "if", "matrix")]


def test_a_bare_job_if_is_read_as_an_expression(tmp_path):
    path = _job_workflow(tmp_path, {"if": "runner.os == 'Linux' && github.ref != ''"})
    assert _found(cwc.check_file(path)) == [("fixture.yml", "one", "if", "runner")]


def test_a_key_the_table_has_no_row_for_provides_no_context(tmp_path):
    path = _job_workflow(tmp_path, {"steps": [{"uses": "${{ matrix.action }}"}]})
    assert _found(cwc.check_file(path)) == [("fixture.yml", "one", "steps.uses", "matrix")]


def test_a_workflow_level_key_is_read_too(tmp_path):
    path = _write(tmp_path / "workflows", "top.yml", """\
        name: Top
        on: workflow_dispatch
        env:
          X: ${{ matrix.a }}
        jobs:
          one:
            runs-on: ubuntu-latest
            steps:
              - run: "true"
        """)
    assert _found(cwc.check_file(path)) == [("top.yml", "", "env", "matrix")]


def test_a_yaml_extension_is_read(tmp_path):
    _job_workflow(tmp_path, {"env": {"X": "${{ steps.a.outputs.b }}"}}, name="other.yaml")
    findings, stats = cwc.check_dir(tmp_path / "workflows")
    assert stats["files"] == 1
    assert _found(findings) == [("other.yaml", "one", "env", "steps")]


def test_a_file_that_is_not_yaml_fails_the_cli(tmp_path):
    _write(tmp_path / "workflows", "bad.yml", "jobs: [unclosed\n")
    done = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "workflows")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert "bad.yml" in done.stdout + done.stderr


# --------------------------------------------------------------------------
# what is never refused — the first draft's false refusals
# --------------------------------------------------------------------------
def test_inputs_and_matrix_where_working_workflows_use_them_pass(tmp_path):
    path = _write(tmp_path / "workflows", "reusable.yml", """\
        name: Reusable
        on:
          workflow_call:
            inputs:
              go:
                type: boolean
            outputs:
              done:
                value: ${{ jobs.one.outputs.done }}
        jobs:
          one:
            if: inputs.go && github.event_name != 'push'
            name: Leg ${{ matrix.leg }} for ${{ inputs.go }}
            runs-on: ${{ matrix.os }}
            concurrency:
              group: one-${{ matrix.leg }}-${{ inputs.go }}
            env:
              LEG: ${{ matrix.leg }}
              GO: ${{ inputs.go }}
            strategy:
              matrix:
                leg: ${{ fromJSON(inputs.legs || '[1]') }}
                os: [ubuntu-latest]
            outputs:
              done: ${{ steps.s.outputs.done }}
            environment:
              name: ${{ matrix.leg }}
              url: ${{ steps.s.outputs.url }}
            steps:
              - id: s
                if: ${{ runner.os == 'Linux' && job.status == 'success' }}
                env:
                  TEMP: ${{ runner.temp }}/x.json
                with:
                  path: ${{ steps.a.outputs.b }}
                run: echo "${{ matrix.leg }} $TEMP"
          two:
            if: ${{ inputs.go }}
            needs: one
            runs-on: ubuntu-latest
            steps:
              - run: "true"
        """)
    assert cwc.check_file(path) == []


@pytest.mark.parametrize("row", sorted(r for r in cwc.ALLOWED if r.startswith("jobs.")))
def test_inputs_is_provided_at_every_job_level_row(row):
    assert "inputs" in cwc.ALLOWED[row], row


@pytest.mark.parametrize("row", ["env", "runs-on", "concurrency", "name"])
def test_matrix_is_provided_at_job_env_runs_on_concurrency_and_name(row):
    assert "matrix" in cwc.ALLOWED[f"jobs.<job_id>.{row}"]


def test_a_quoted_string_is_not_read():
    assert cwc.contexts_named("github.ref == 'runner.temp' || 'it''s steps.a'") == {"github"}


def test_a_property_name_is_not_read():
    assert cwc.contexts_named("github.event.inputs.runner || needs.a.outputs.steps") == {
        "github", "needs"}


def test_a_function_name_is_not_read():
    assert cwc.contexts_named("runner(github.ref) && fromJSON(needs.a.outputs.b)") == {
        "github", "needs"}


def test_an_expression_in_a_string_is_found_between_its_braces():
    assert cwc.expressions("a ${{ runner.temp }} b ${{ 'x}}' && steps.a }}", bare=False) == [
        " runner.temp ", " 'x}}' && steps.a "]
    assert cwc.expressions("runner.os == 'Linux'", bare=True) == ["runner.os == 'Linux'"]
    assert cwc.expressions("plain text", bare=False) == []


# --------------------------------------------------------------------------
# the table is data, with its source beside it
# --------------------------------------------------------------------------
def test_the_table_carries_its_url():
    assert cwc.TABLE_URL == TABLE_URL
    source = SCRIPT.read_text(encoding="utf-8")
    url, table = source.index(TABLE_URL), source.index("\nALLOWED = {")
    assert url < table and source.count("\n", url, table) <= 5, "the URL sits beside the table"


def test_the_table_is_transcribed_for_the_keys_the_card_names():
    a = cwc.ALLOWED
    assert a["jobs.<job_id>.env"] == {"github", "needs", "strategy", "matrix", "vars",
                                      "secrets", "inputs"}
    assert a["jobs.<job_id>.if"] == {"github", "needs", "vars", "inputs"}
    assert a["jobs.<job_id>.strategy"] == {"github", "needs", "vars", "inputs"}
    for row in ("jobs.<job_id>.steps.run", "jobs.<job_id>.outputs.<output_id>",
                "jobs.<job_id>.environment.url"):
        assert {"runner", "steps", "job"} <= a[row], row


def test_it_needs_no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the check opened a socket")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    findings, _ = cwc.check_dir(WORKFLOWS)
    assert findings == []


# --------------------------------------------------------------------------
# every pull request is read by it
# --------------------------------------------------------------------------
def test_tests_yml_runs_it_beside_workflow_yaml_parses():
    steps = yaml.safe_load(TESTS_WORKFLOW.read_text(encoding="utf-8"))["jobs"][
        "unit-result"]["steps"]
    names = [s.get("name") for s in steps]
    parses = names.index("Workflow YAML parses")
    runs = [i for i, s in enumerate(steps)
            if "python3 scripts/check_workflow_contexts.py" in str(s.get("run", ""))]
    assert runs == [parses + 1], names
    assert "if" not in steps[runs[0]] and "continue-on-error" not in steps[runs[0]]
