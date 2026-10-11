"""RED-first tests for DRE-6909 — the console-change walks run as their own
job, and a walk never runs a walk.

bureau-pipeline #1006 (DRE-6899) finished red on 2026-10-11 with no failing
test: `scripts unit tests (part 1)` was canceled at its 30-minute limit (run
38142539090, 13:49:22Z to 14:19:41Z), while parts 2, 3 and 4 took 20 to 24
minutes. The day before, the four parts took 11 to 16. What grew overnight was
the "walks" — `tests/*_keeps_the_suite_green.py`, each of which copies the
tree, applies a console save and runs the model tests in the copy, once per
case. Part 1 held `test_console_agent_move_keeps_the_suite_green.py` and part
2 `test_console_project_model_keeps_the_suite_green.py`, and the agent-move
walk's inner run collected `tests/test_console_*.py` — the other walk and
itself — in every one of its twenty copies.

Three things have to hold from here on:

  * A WALK NEVER RUNS A WALK. Every walk's inner file list leaves out every
    `*_keeps_the_suite_green.py` file, and the copy, the apply, the regenerate
    and the inner run live once, in `tests/console_change_walk.py`.
  * THE WALKS RUN AS THEIR OWN JOB. `tests.yml` runs them in a job of their
    own with its own clock, the numbered parts collect none of them, and the
    job carrying the required name fails when that job did not succeed.
  * THE LIMIT IS WATCHED. The walks' job prints how long each walk took, and a
    walk added later is picked up by that job — by its name alone — and by no
    part.

Run: cd bureau-pipeline && python3 -m pytest tests/test_tests_workflow_parts.py -v
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import unit_test_parts as parts  # noqa: E402

SCRIPT = ROOT / "scripts" / "unit_test_parts.py"
TESTS_WF = ROOT / ".github" / "workflows" / "tests.yml"
HELPER = "console_change_walk"

#: The name branch protection requires on `main`.
REQUIRED_CHECK = "scripts unit tests"

#: The numbered parts' job.
PARTS_JOB = "unit"

#: The walks on `main` when this card was cut. More may be added; these two
#: must never stop being walks.
KNOWN_WALKS = (
    "tests/test_console_agent_move_keeps_the_suite_green.py",
    "tests/test_console_project_model_keeps_the_suite_green.py",
)

#: What a walk must not carry of its own: the tree copy, the subprocess run
#: and the thread pool are the helper's.
OWN_COPY_MODULES = {"shutil", "subprocess", "concurrent.futures"}


def _walks() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix()
                  for p in (ROOT / "tests").rglob("*_keeps_the_suite_green.py"))


def _module(rel: str):
    return importlib.import_module(Path(rel).stem)


def _tree(tmp_path: Path, names: list[str], body: str = "def test_x():\n    pass\n") -> Path:
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return tmp_path


def _env() -> dict:
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    return env


def _jobs() -> dict:
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]


def _walks_job() -> tuple[str, dict]:
    found = [(key, job) for key, job in _jobs().items()
             if "unit_test_parts.py run-walks" in
             "\n".join(str(s.get("run", "")) for s in job.get("steps", []))]
    assert len(found) == 1, (
        f"exactly one job in tests.yml must run the walks "
        f"(`unit_test_parts.py run-walks`); found {[k for k, _ in found]}")
    return found[0]


def _aggregate() -> dict:
    named = [job for job in _jobs().values() if job.get("name") == REQUIRED_CHECK]
    assert len(named) == 1, named
    return named[0]


# ---------------------------------------------------------------------------
# 1. A walk never runs a walk.
# ---------------------------------------------------------------------------


def test_the_known_walks_are_walks():
    assert set(KNOWN_WALKS) <= set(_walks()), _walks()
    assert parts.walks(ROOT) == _walks()
    for rel in _walks():
        assert parts.is_walk(rel), rel
    assert not parts.is_walk("tests/test_console_receipt.py")


@pytest.mark.parametrize("walk", _walks())
def test_no_walk_runs_a_walk(walk):
    """The file list each walk's inner run hands pytest, read from the walk
    itself: on 2026-10-11 the agent-move walk's held both walks."""
    inner = _module(walk).suite_files(ROOT)
    assert inner, walk
    nested = [rel for rel in inner if rel.endswith("_keeps_the_suite_green.py")]
    assert nested == [], f"{walk}'s inner run collects the walks {nested}"


@pytest.mark.parametrize("walk", _walks())
def test_every_walk_uses_the_one_shared_helper(walk):
    """The copy, the apply, the regenerate and the inner run live once. A walk
    that imports `shutil`, `subprocess` or a thread pool of its own is carrying
    its own copy of them."""
    tree = ast.parse((ROOT / walk).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert HELPER in imported, f"{walk} does not use tests/{HELPER}.py"
    own = sorted(imported & OWN_COPY_MODULES)
    assert own == [], f"{walk} carries its own tree-copy or inner-run code: {own}"
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    assert not defined & {"copy_tree", "_run", "save"}, (
        f"{walk} defines {sorted(defined & {'copy_tree', '_run', 'save'})} — "
        f"use the helper's")


def test_the_helper_leaves_every_walk_out_of_an_inner_run(tmp_path):
    walk = _module(f"tests/{HELPER}.py")
    tree = _tree(tmp_path, [
        "tests/test_model_a.py",
        "tests/test_console_b.py",
        "tests/test_console_c_keeps_the_suite_green.py",
        "tests/test_model_d_keeps_the_suite_green.py",
    ])
    files = walk.inner_files(tree, ("tests/test_model_a.py",),
                             ("test_model_*.py", "test_console_*.py"))
    assert files == ["tests/test_model_a.py", "tests/test_console_b.py"]


def test_the_helper_refuses_a_walk_named_outright(tmp_path):
    walk = _module(f"tests/{HELPER}.py")
    tree = _tree(tmp_path, ["tests/test_x_keeps_the_suite_green.py"])
    with pytest.raises(ValueError, match="walk"):
        walk.inner_files(tree, ("tests/test_x_keeps_the_suite_green.py",), ())
    with pytest.raises(ValueError, match="walk"):
        walk.run_suite(tree, ["tests/test_x_keeps_the_suite_green.py"])


def test_the_helper_reports_a_failing_inner_run(tmp_path):
    """The walks must still go red: a failing test inside the copy comes back
    by node id, a subtest's too."""
    walk = _module(f"tests/{HELPER}.py")
    tree = _tree(tmp_path, ["tests/test_red.py"],
                 "def test_pins_a_choice():\n    assert 'a' == 'b'\n")
    got = walk.run_suite(tree, ["tests/test_red.py"])
    assert got["returncode"] != 0
    assert got["failed"] == ["tests/test_red.py::test_pins_a_choice"]


# ---------------------------------------------------------------------------
# 2. The walks run as their own job, and no part collects them.
# ---------------------------------------------------------------------------


def test_no_numbered_part_collects_a_walk():
    for part in range(1, len(_jobs()[PARTS_JOB]["strategy"]["matrix"]["part"]) + 1):
        handed = parts.part_files(ROOT, part, len(
            _jobs()[PARTS_JOB]["strategy"]["matrix"]["part"]),
            parts.load_weights(parts.DURATIONS))
        assert [f for f in handed if parts.is_walk(f)] == [], part


def test_the_parts_and_the_walks_job_between_them_run_every_test_file():
    every = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").rglob("*.py")
                   if p.name.startswith("test_") and "__pycache__" not in p.parts)
    split = set(parts.discover(ROOT))
    walks = set(parts.walks(ROOT))
    assert not split & walks
    assert split | walks >= set(every)


def test_the_walks_run_in_a_job_of_their_own_with_a_declared_clock():
    key, job = _walks_job()
    assert key != PARTS_JOB
    assert job.get("name") != REQUIRED_CHECK
    assert int(job.get("timeout-minutes", 0)) > 0, (
        "the walks' job declares no timeout-minutes — GitHub's default is "
        "six hours, which is not a budget anyone chose")
    assert "if" not in job, "the walks run on every event, the nightly included"


def test_the_walks_job_names_no_walk_so_a_new_one_needs_no_workflow_edit():
    _, job = _walks_job()
    text = yaml.safe_dump(job)
    assert "keeps_the_suite_green" not in text, (
        "the walks' job must find the walks by their name pattern, never list "
        "them")


def test_the_required_check_waits_on_the_walks_and_fails_when_they_fail():
    key, _ = _walks_job()
    job = _aggregate()
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert PARTS_JOB in needs and key in needs, needs
    assert "always()" in str(job.get("if", ""))
    final = next(s for s in job["steps"] if s.get("name") == "Every part passed")
    assert "always()" in str(final.get("if", ""))
    text = yaml.safe_dump(final)
    assert f"needs.{key}.result" in text, (
        "the last step must read the walks' result — a red walk has to fail "
        "the pull request")
    assert f"needs.{PARTS_JOB}.result" in text


def test_the_walks_job_starts_from_the_parts_environment():
    """The walks import the same modules the parts do; they get the same
    environment, so a walk does not pass or fail on which job ran it."""
    _, job = _walks_job()
    unit_env = next(s for s in _jobs()[PARTS_JOB]["steps"]
                    if s.get("name") == "Unit tests")["env"]
    step = next(s for s in job["steps"]
                if "run-walks" in str(s.get("run", "")))
    assert step.get("env") == unit_env


# ---------------------------------------------------------------------------
# 3. The limit is watched.
# ---------------------------------------------------------------------------


def test_a_new_walk_is_picked_up_by_the_walks_job_and_by_no_part(tmp_path):
    root = _tree(tmp_path, [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_console_c.py",
        "tests/test_console_moved_twice_keeps_the_suite_green.py",
    ])
    new = "tests/test_console_moved_twice_keeps_the_suite_green.py"
    assert parts.walks(root) == [new]
    assert new not in parts.discover(root)
    for part in (1, 2):
        assert new not in parts.part_files(root, part, 2, {})
    listed = subprocess.run(
        [sys.executable, str(SCRIPT), "walks", "--root", str(root)],
        capture_output=True, text=True, check=True).stdout.split()
    assert listed == [new]


def test_the_check_names_a_walk_a_part_ran(tmp_path):
    root = _tree(tmp_path / "repo", [
        "tests/test_a.py", "tests/test_b.py",
        "tests/test_x_keeps_the_suite_green.py"])
    ran = tmp_path / "ran"
    ran.mkdir()
    (ran / "part-1.txt").write_text(parts.manifest(
        1, 1, ["tests/test_a.py", "tests/test_b.py",
               "tests/test_x_keeps_the_suite_green.py"]))
    problems = parts.check(root, ran)
    assert any("tests/test_x_keeps_the_suite_green.py" in p and "walks" in p
               for p in problems), problems


def test_run_walks_prints_how_long_each_walk_took_and_fails_on_a_red_one(tmp_path):
    root = _tree(tmp_path, ["tests/test_a.py"])
    _tree(root, ["tests/test_green_keeps_the_suite_green.py"])
    _tree(root, ["tests/test_red_keeps_the_suite_green.py"],
          "def test_x():\n    assert False\n")
    summary = tmp_path / "summary.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root)],
        capture_output=True, text=True, cwd=root,
        env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    for name, verdict in (("green", "passed"), ("red", "failed")):
        line = next(ln for ln in proc.stdout.splitlines()
                    if ln.startswith(f"walk tests/test_{name}_keeps_the_suite_green.py"))
        assert verdict in line and "took" in line, line
    assert "test_green_keeps_the_suite_green.py" in summary.read_text()


def test_run_walks_passes_when_every_walk_passes(tmp_path):
    root = _tree(tmp_path, ["tests/test_a.py",
                            "tests/test_one_keeps_the_suite_green.py"])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root)],
        capture_output=True, text=True, cwd=root, env=_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 walk(s)" in proc.stdout


def test_run_walks_with_no_walk_is_red(tmp_path):
    """A pattern that matches nothing would make the job a silent green."""
    root = _tree(tmp_path, ["tests/test_a.py"])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root)],
        capture_output=True, text=True, cwd=root, env=_env())
    assert proc.returncode == 1, proc.stdout + proc.stderr
