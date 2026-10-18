"""DRE-6873 — the suite-green harness runs in a CI job of its own, narrowed to
the runs that can reach the model tests and whole every night.

`tests/test_console_change_keeps_the_suite_green.py` (DRE-6865) runs every test
that can read the model files once per console change it derives — about ten
minutes of pytest a case. In the unit parts, where `BUREAU_SUITE_GREEN_RUN` is
unset, those cases skip, so before this card they ran nowhere in CI.
`tests.yml`'s `console change keeps the suite green` job runs them, split
over as many runners as `tests/test_suite_green_time_budget.py` computes from
the case count, the recorded seconds a case and the job's clock (DRE-7511).

NARROWED PER CHANGE, WHOLE EVERY NIGHT (the CEO's CI decision of 2026-09-24,
`standards/engineering.md`). The job's first step asks
`scripts/suite_green_reach.py` whether this run's changed files can reach the
model tests, by the harness's own rule (`model_fixture.model_reading_tests`'
six names and rung ids), and every later step is gated on the answer — never an
`on: paths:` filter, which would create no check run at all. Anything the
script or the step cannot read answers `run`, and a schedule run has no range
to compare, so the nightly runs every case.

Two halves, both read here:

  * the script, against planted trees and planted lists;
  * the job, off the workflow text — and the reach step's shell, run with a
    stubbed `gh`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_suite_green_job.py -v
"""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import console_change_walk as walk  # noqa: E402
import model_fixture  # noqa: E402
import suite_green_reach as reach  # noqa: E402
import test_suite_green_time_budget as budget  # noqa: E402

TESTS_WF = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "suite_green_reach.py"
HARNESS = "tests/test_console_change_keeps_the_suite_green.py"

JOB_NAME = "console change keeps the suite green"
RUNS_ON = ("${{ fromJSON(vars.BUREAU_CI_RUNS_ON || vars.BUREAU_RUNS_ON "
           "|| '[\"ubuntu-latest\"]') }}")
PYTHON_ACTION = "./.github/actions/setup-python-cached"
GATE = "steps.reach.outputs.run == 'true'"

#: A rung id only the planted tree holds, so a planted file naming it names
#: nothing else the rule reads.
PLANTED_ID = "claude-planted-7-1"
PLANTED_MODELS = f"""\
default_ladder: builders
ladders:
  builders:
    - {PLANTED_ID}
    - model: claude-planted-other-2
      effort: high
kinds:
  builder:
    ladder: builders
"""


# --------------------------------------------------------------------------- #
# The script, against planted trees                                            #
# --------------------------------------------------------------------------- #

def _tree(tmp_path: Path, files: dict | None = None,
          models: str | None = PLANTED_MODELS) -> Path:
    tree = tmp_path / "tree"
    (tree / "config").mkdir(parents=True)
    if models is not None:
        (tree / "config" / "models.yaml").write_text(models)
    for rel, text in (files or {}).items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
    return tree


def _list(tmp_path: Path, *paths: str) -> Path:
    changed = tmp_path / "changed.txt"
    changed.write_text("".join(f"{p}\n" for p in paths))
    return changed


def _decide(tmp_path, paths, files=None, models=PLANTED_MODELS):
    tree = _tree(tmp_path, files, models)
    return reach.decide(_list(tmp_path, *paths), tree)


def test_a_docs_only_list_skips(tmp_path):
    answer, _ = _decide(tmp_path, ["docs/a.md", "README.md"],
                        files={"docs/a.md": "models.yaml", "README.md": "agents.yaml"})
    assert answer == "skip"


@pytest.mark.parametrize("path", [
    "config/models.yaml",
    "config/anything-else.json",
    "agents.yaml",
    "tests/model_fixture.py",
    "tests/fixtures/model-config-frozen/models.yaml",
    ".github/workflows/tests.yml",
])
def test_a_path_that_reaches_outright_runs_naming_it(tmp_path, path):
    # Planted empty, so only the path itself can make it reach.
    assert _decide(tmp_path, ["docs/a.md", path], files={path: ""}) == ("run", path)


def test_a_test_file_naming_only_a_rung_id_runs_naming_it(tmp_path):
    rel = "tests/test_some_selector.py"
    source = f'def test_it():\n    assert pick() == "{PLANTED_ID}"\n'
    assert not any(name in source for name in reach.MODEL_FILE_NAMES)
    assert _decide(tmp_path, ["README.md", rel], files={rel: source}) == ("run", rel)


def test_the_same_test_file_without_the_id_skips(tmp_path):
    """The id is what made the file above reach: the same file without it
    does not."""
    rel = "tests/test_some_selector.py"
    source = 'def test_it():\n    assert pick() == "claude-unplanted-0"\n'
    assert _decide(tmp_path, [rel], files={rel: source})[0] == "skip"


@pytest.mark.parametrize("name", model_fixture.MODEL_FILE_NAMES)
def test_a_source_file_naming_one_of_the_six_names_runs(tmp_path, name):
    rel = "scripts/some_helper.py"
    source = f'# reads {name}\nVALUE = 1\n'
    assert _decide(tmp_path, [rel], files={rel: source}) == ("run", rel)


def test_a_scripts_file_naming_model_fallback_runs(tmp_path):
    rel = "scripts/route.py"
    source = "import model_fallback\n"
    assert _decide(tmp_path, ["docs/a.md", rel], files={rel: source}) == ("run", rel)


def test_a_scripts_file_naming_none_of_the_six_and_no_id_skips(tmp_path):
    rel = "scripts/release_notes.py"
    source = "import json\n\ndef render(notes):\n    return json.dumps(notes)\n"
    assert _decide(tmp_path, [rel], files={rel: source})[0] == "skip"


def test_a_source_file_outside_tests_and_scripts_skips_whatever_it_names(tmp_path):
    rel = "console/backend/models.py"
    assert _decide(tmp_path, [rel], files={rel: "open('models.yaml')\n"})[0] == "skip"


def test_a_deleted_py_runs_naming_it(tmp_path):
    rel = "tests/test_gone.py"
    assert _decide(tmp_path, ["README.md", rel]) == ("run", rel)


def test_a_deleted_file_that_is_not_python_skips(tmp_path):
    assert _decide(tmp_path, ["docs/gone.md"])[0] == "skip"


def test_the_first_reaching_path_is_named(tmp_path):
    first, second = "scripts/b.py", "config/models.yaml"
    answer = _decide(tmp_path, ["docs/a.md", first, second],
                     files={first: "import sync_model_config\n"})
    assert answer == ("run", first)


def test_a_missing_list_runs(tmp_path):
    answer, why = reach.decide(tmp_path / "no-such-list.txt", _tree(tmp_path))
    assert answer == "run", why


def test_an_empty_list_runs(tmp_path):
    answer, why = reach.decide(_list(tmp_path), _tree(tmp_path))
    assert answer == "run", why


@pytest.mark.parametrize("body", [
    b"README.md\n\ndocs/a.md\n",       # a blank line inside the list
    b"README.md\n  docs/a.md\n",       # a path with padding round it
    b"README.md\n/etc/passwd\n",       # not a path in the repository
    b"README.md\ndocs/\xff.md\n",      # not UTF-8
])
def test_an_unreadable_line_runs(tmp_path, body):
    changed = tmp_path / "changed.txt"
    changed.write_bytes(body)
    answer, why = reach.decide(changed, _tree(tmp_path))
    assert answer == "run", why


def test_an_unparseable_models_yaml_runs(tmp_path):
    answer, why = _decide(tmp_path, ["docs/a.md"], models="ladders: [unclosed\n")
    assert answer == "run", why


def test_a_missing_models_yaml_runs(tmp_path):
    answer, why = _decide(tmp_path, ["docs/a.md"], models=None)
    assert answer == "run", why


def test_a_models_yaml_whose_ladders_cannot_be_read_runs(tmp_path):
    answer, why = _decide(tmp_path, ["docs/a.md"],
                          models="ladders:\n  builders:\n    - effort: high\n")
    assert answer == "run", why


def test_the_six_names_are_the_ones_model_fixture_reads():
    assert reach.MODEL_FILE_NAMES == model_fixture.MODEL_FILE_NAMES


def test_the_rule_is_model_reading_tests_on_the_real_tree():
    """Every test file `model_fixture.model_reading_tests` derives reaches, and
    no other `tests/test_*.py` does but the walks — the same rule, so a test
    that joins the derived set reaches the moment it is added. The derived set
    leaves the walks out because a walk never runs a walk; a change to one
    still reaches here, and the harness itself is one."""
    derived = {p.relative_to(ROOT).as_posix()
               for p in model_fixture.model_reading_tests(ROOT)}
    assert derived, "the derived set is empty — the comparison went vacuous"
    tokens = reach.tokens(ROOT)
    every = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py"))
    reaching = {rel for rel in every if reach.reaches(rel, ROOT, tokens)}
    assert HARNESS in reaching and walk.is_walk(HARNESS)
    assert {rel for rel in reaching if not walk.is_walk(rel)} == derived & set(every)


def test_the_cli_prints_the_answer_on_its_first_line_and_exits_0(tmp_path):
    tree = _tree(tmp_path)
    for paths, want in ((["docs/a.md"], "skip"), (["agents.yaml"], "run")):
        changed = _list(tmp_path, *paths)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "decide", "--changed", str(changed),
             "--tree", str(tree)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines()[0] == want, proc.stdout
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "decide", "--changed", str(tmp_path / "nope"),
         "--tree", str(tree)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == "run", proc.stdout


# --------------------------------------------------------------------------- #
# The job, read off the workflow                                               #
# --------------------------------------------------------------------------- #

def _jobs() -> dict:
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]


def _job() -> tuple[str, dict]:
    found = [(key, job) for key, job in _jobs().items()
             if str(job.get("name", "")).startswith(JOB_NAME)]
    assert len(found) == 1, (
        f"exactly one job in tests.yml must be named {JOB_NAME!r}; "
        f"found {[k for k, _ in found]}")
    return found[0]


def _steps() -> list[dict]:
    return _job()[1]["steps"]


def _reach_index() -> int:
    ids = [i for i, step in enumerate(_steps()) if step.get("id") == "reach"]
    assert len(ids) == 1, "the job must carry exactly one step with id `reach`"
    return ids[0]


def _pytest_step() -> dict:
    found = [s for s in _steps() if HARNESS in str(s.get("run", ""))]
    assert len(found) == 1, "exactly one step runs the harness"
    return found[0]


def _harness_constant(name: str) -> str:
    tree = ast.parse((ROOT / HARNESS).read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{HARNESS} declares no {name}")


def _part_count() -> int:
    """The part count `tests/test_suite_green_time_budget.py` computes from the
    tree's cases, the recorded seconds a case and the job's clock (DRE-7511)."""
    need = budget.parts_needed(len(budget.harness.CASES), budget.CASE_SECONDS,
                               budget.limit_seconds())
    assert need is not None and need >= 1, need
    return need


def test_the_job_is_named_and_its_parts_report_as_part_n():
    _, job = _job()
    parts = job["strategy"]["matrix"]["part"]
    count = _part_count()
    assert parts == list(range(1, count + 1)), (parts, count)
    assert job["strategy"].get("fail-fast") is False
    names = [str(job["name"]).replace("${{ matrix.part }}", str(n)) for n in parts]
    assert names == [f"{JOB_NAME} (part {n})" for n in range(1, count + 1)]
    # The harness's skip reason sends the reader to this job by name.
    assert f'"{JOB_NAME}"' in _harness_constant("SKIP_REASON")


def test_the_pytest_step_sets_the_three_names_with_the_part_values():
    step = _pytest_step()
    assert "python3 -m pytest tests/test_console_change_keeps_the_suite_green.py -v" in step["run"]
    env = step["env"]
    parts = _job()[1]["strategy"]["matrix"]["part"]
    assert env[_harness_constant("RUN_ENV")] == "1"
    assert env[_harness_constant("PART_ENV")] == "${{ matrix.part }}"
    assert str(env[_harness_constant("OF_ENV")]) == str(_part_count()) == str(len(parts))


def test_the_three_names_are_set_in_this_step_and_nowhere_else_in_the_workflow():
    names = [_harness_constant(n) for n in ("RUN_ENV", "PART_ENV", "OF_ENV")]
    key, _ = _job()
    for job_key, job in _jobs().items():
        for name in names:
            assert name not in yaml.safe_dump(job.get("env") or {}), (job_key, name)
        for step in job.get("steps") or []:
            if job_key == key and step is not None and HARNESS in str(step.get("run", "")):
                continue
            assert not set(names) & set(step.get("env") or {}), (job_key, step.get("name"))
    doc = yaml.safe_load(TESTS_WF.read_text())
    assert not set(names) & set(doc.get("env") or {})


def test_the_job_reads_the_build_runner_chain_and_the_shared_python_action():
    _, job = _job()
    assert job["runs-on"] == RUNS_ON
    installs = [s for s in _steps() if s.get("uses") == PYTHON_ACTION]
    assert len(installs) == 1 and str(installs[0].get("name", "")).strip()
    assert _steps().index(installs[0]) < _steps().index(_pytest_step())


def test_the_job_has_no_if_needs_nothing_and_nothing_needs_it():
    key, job = _job()
    assert "if" not in job, "a schedule run must run this job in full"
    assert "needs" not in job
    for other, spec in _jobs().items():
        needs = spec.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        assert key not in needs, f"{other} needs {key}"
    assert key not in _jobs()["unit-result"]["needs"]


def test_the_job_declares_a_timeout():
    timeout = _job()[1].get("timeout-minutes")
    assert isinstance(timeout, int) and timeout > 0, timeout


def test_every_step_after_the_reach_step_is_gated_on_its_output():
    steps = _steps()
    i = _reach_index()
    after = steps[i + 1:]
    assert after, "nothing follows the reach step"
    for step in after:
        assert step.get("if") == GATE, step.get("name")
    # Nothing before it but the checkout it reads the tree from.
    assert [str(s.get("uses", "")).split("@")[0] for s in steps[:i]] == ["actions/checkout"]


def _reach_step() -> dict:
    return _steps()[_reach_index()]


def test_the_reach_steps_else_branch_writes_run_true():
    shell = _reach_step()["run"]
    assert "scripts/suite_green_reach.py decide" in shell
    head, _, tail = shell.rpartition("\nelse\n")
    assert head, "the reach step's shell has no `else` branch"
    branch = tail.split("\nfi", 1)[0]
    assert 'echo "run=true" >> "$GITHUB_OUTPUT"' in branch
    assert "could not read" in branch


def _run_reach(tmp_path: Path, gh_body: str, base: str = "base-sha") -> tuple[str, str]:
    """The reach step's shell, run in the repository with `gh` stubbed by
    `gh_body`. Returns what it wrote to `$GITHUB_OUTPUT` and its stdout."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> '{tmp_path}/gh-calls'\n{gh_body}\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "github-output"
    out.write_text("")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               GITHUB_OUTPUT=str(out), RUNNER_TEMP=str(runner_temp),
               GITHUB_REPOSITORY="dreadnought-foundry/bureau-pipeline",
               BASE=base, HEAD="head-sha")
    # GitHub's own `run:` shell for bash.
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                           _reach_step()["run"]], cwd=ROOT,
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out.read_text(), proc.stdout


def test_the_reach_step_runs_when_the_compare_fails(tmp_path):
    output, stdout = _run_reach(tmp_path, "exit 1")
    assert output.splitlines() == ["run=true"]
    assert "could not read" in stdout


def test_a_schedule_run_has_no_range_and_runs(tmp_path):
    # A schedule event has no `github.event.before`, so BASE is empty.
    output, _ = _run_reach(tmp_path, "printf 'docs/a.md\\n'", base="")
    assert output.splitlines() == ["run=true"]


def test_the_reach_step_skips_a_docs_only_compare(tmp_path):
    output, stdout = _run_reach(tmp_path, "printf 'docs/a.md\\nREADME.md\\n'")
    assert output.splitlines() == ["run=false"], stdout
    calls = (tmp_path / "gh-calls").read_text()
    assert "compare/base-sha...head-sha" in calls


def test_the_reach_step_runs_a_compare_that_reaches(tmp_path):
    output, stdout = _run_reach(tmp_path, "printf 'docs/a.md\\nconfig/models.yaml\\n'")
    assert output.splitlines() == ["run=true"]
    assert "config/models.yaml" in stdout
