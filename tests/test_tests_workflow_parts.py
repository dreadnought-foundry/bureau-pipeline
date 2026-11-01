"""RED-first tests for DRE-8017 — the console-change walks and the suite-green
job leave Pipeline Tests, with the scripts that decided when they ran.

The walks (`tests/*_keeps_the_suite_green.py`, through
`tests/console_change_walk.py`) copied the tree, applied a console save the
way the old pull-request save route did and ran the model tests inside the copy, once
per case. They ran in jobs of their own (DRE-6909), one walk to a job
(DRE-6935), across shards (DRE-7907), only when a change could reach them
(DRE-7908); the suite-green job (DRE-6873) ran the change walk's cases in four
parts. Since DRE-6977 a save on Settings › Agents & models writes the one home
and opens no pull request, so the route those jobs proved carries nothing. On
run 40265301227 (main, 2026-10-26 03:41 UTC) the walk jobs still took 132
runner-minutes, against 107 for the unit parts that run every other test.

Three things hold from here on:

  * NO WALK JOB IS LEFT. `tests.yml` has no `walk-list`, `walks` or
    `suite-green` job, and no step runs `walk_reach.py`,
    `suite_green_reach.py` or `unit_test_parts.py run-walks`.
  * THE REQUIRED CHECK READS THE PARTS ALONE. `unit-result` keeps the name
    branch protection requires, `scripts unit tests`, and needs `unit` and
    nothing else.
  * A WALK'S NAME MEANS NOTHING. `unit_test_parts.py` has no `walks` or
    `run-walks` command, and a file named `*_keeps_the_suite_green.py` is an
    ordinary test file that runs in exactly one part.

Run: cd bureau-pipeline && python3 -m pytest tests/test_tests_workflow_parts.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import unit_test_parts as parts  # noqa: E402

SCRIPT = ROOT / "scripts" / "unit_test_parts.py"
TESTS_WF = ROOT / ".github" / "workflows" / "tests.yml"

#: The name branch protection requires on `main`.
REQUIRED_CHECK = "scripts unit tests"

#: The numbered parts' job.
PARTS_JOB = "unit"

#: The jobs this card removed.
GONE_JOBS = ("walk-list", "walks", "suite-green")

#: What no step may run any more.
GONE_RUNS = ("walk_reach.py", "suite_green_reach.py", "unit_test_parts.py run-walks")

#: The paths this card deleted.
DELETED = (
    "scripts/walk_reach.py",
    "scripts/suite_green_reach.py",
    "tests/console_change_walk.py",
    "tests/test_console_change_keeps_the_suite_green.py",
    "tests/test_console_agent_move_keeps_the_suite_green.py",
    "tests/test_console_every_default_keeps_the_suite_green.py",
    "tests/test_console_project_model_keeps_the_suite_green.py",
    "tests/test_walk_reach.py",
    "tests/test_walks_time_budget.py",
    "tests/test_suite_green_job.py",
    "tests/test_suite_green_time_budget.py",
)


def _jobs() -> dict:
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]


def _aggregate() -> tuple[str, dict]:
    named = [(key, job) for key, job in _jobs().items()
             if job.get("name") == REQUIRED_CHECK]
    assert len(named) == 1, named
    return named[0]


def _part_count() -> int:
    return len(_jobs()[PARTS_JOB]["strategy"]["matrix"]["part"])


def _tree(tmp_path: Path, names: list[str]) -> Path:
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    pass\n")
    return tmp_path


def _list(root: Path, part: int, of: int, manifests: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "list", "--root", str(root),
         "--part", str(part), "--of", str(of),
         "--manifest", str(manifests / f"part-{part}.txt")],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.split()


# ---------------------------------------------------------------------------
# 1. No walk job is left.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", DELETED)
def test_the_deleted_path_is_gone(path):
    assert not (ROOT / path).exists(), path


def test_tests_yml_has_no_walk_or_suite_green_job():
    jobs = _jobs()
    left = [key for key in GONE_JOBS if key in jobs]
    assert left == [], f"tests.yml still has the job(s) {left}"
    for key, job in jobs.items():
        name = str(job.get("name", ""))
        assert not name.startswith("scripts unit tests (walk"), (key, name)
        assert not name.startswith("console change keeps the suite green"), (key, name)


def test_no_step_runs_a_walk_or_its_reach_script():
    for key, job in _jobs().items():
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            for gone in GONE_RUNS:
                assert gone not in run, (key, step.get("name"), gone)
    text = TESTS_WF.read_text(encoding="utf-8")
    for name in ("BUREAU_WALK_SHARD", "BUREAU_SUITE_GREEN", "needs.walk-list",
                 "needs.walks"):
        assert name not in text, name


def test_the_durations_refresh_says_why_it_mints_first_in_its_own_words():
    # The route it pointed to is gone: the console's pull-request save route
    # carried nothing since DRE-6977, and DRE-8021 deleted its workflow.
    assert not list(TESTS_WF.parent.glob("model-config-*.yml"))
    assert "model-config-" not in TESTS_WF.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. The required check reads the parts alone.
# ---------------------------------------------------------------------------


def test_the_required_check_keeps_its_name_and_needs_exactly_the_parts():
    key, job = _aggregate()
    assert key == "unit-result"
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert needs == [PARTS_JOB], needs
    assert "always()" in str(job.get("if", "")), (
        "without always() GitHub skips this job when a part fails, and a "
        "skipped required check counts as passing")


def _final_step() -> dict:
    _, job = _aggregate()
    return next(s for s in job["steps"] if s.get("name") == "Every part passed")


def test_the_last_step_reads_the_parts_result_and_nothing_else():
    step = _final_step()
    assert "always()" in str(step.get("if", ""))
    assert step.get("env") == {"PARTS": f"${{{{ needs.{PARTS_JOB}.result }}}}"}, (
        step.get("env"))


@pytest.mark.parametrize("result,code", [
    ("success", 0), ("failure", 1), ("cancelled", 1), ("skipped", 1), ("", 1)])
def test_the_last_step_is_green_only_when_every_part_succeeded(result, code, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("WALKS")}
    env.update(PARTS=result, GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))
    proc = subprocess.run(["bash", "-e", "-c", _final_step()["run"]],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == code, (result, proc.stdout, proc.stderr)
    assert "walk" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 3. A walk's name means nothing to the parts script.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["walks", "run-walks"])
def test_the_walk_commands_are_unknown(command):
    proc = subprocess.run([sys.executable, str(SCRIPT), command],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode != 0, proc.stdout
    assert "invalid choice" in proc.stderr, proc.stderr


def test_the_script_keeps_no_walk_machinery():
    for name in ("WALK", "is_walk", "walks", "walk_cases", "walk_shards",
                 "shard_count", "projected_shard_seconds", "run_walks",
                 "shard_line", "WALK_SHARD_TARGET_SECONDS",
                 "WALK_JOB_FIXED_SECONDS", "WALKS_SENTENCE"):
        assert not hasattr(parts, name), name


def test_list_puts_every_test_file_of_the_tree_in_exactly_one_part(tmp_path):
    of = _part_count()
    manifests = tmp_path / "ran"
    where: dict[str, list[int]] = {}
    for part in range(1, of + 1):
        for f in _list(ROOT, part, of, manifests):
            where.setdefault(f, []).append(part)
    every = {p.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("test_*.py")}
    missing = sorted(every - set(where))
    twice = sorted(f for f, hits in where.items() if len(hits) > 1)
    assert missing == [] and twice == [], (missing, twice)
    assert parts.check(ROOT, manifests) == []


def test_a_file_named_like_a_walk_is_an_ordinary_test_file(tmp_path):
    named = "tests/test_x_keeps_the_suite_green.py"
    root = _tree(tmp_path / "repo",
                 [f"tests/test_{i:02d}.py" for i in range(6)] + [named])
    assert named in parts.discover(root)
    manifests = tmp_path / "ran"
    handed = [_list(root, part, 3, manifests) for part in (1, 2, 3)]
    assert sum(f == named for files in handed for f in files) == 1, handed
    assert parts.check(root, manifests) == []
    # And a part that left it out is caught, as for any test file.
    for path in manifests.glob("*.txt"):
        path.write_text("\n".join(line for line in path.read_text().splitlines()
                                  if line != named) + "\n")
    assert parts.check(root, manifests) == [f"{named} is in no part — it did not run"]
