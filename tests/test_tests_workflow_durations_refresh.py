"""RED-first tests for DRE-7577 — the nightly re-measures the durations record
before it falls under its 90% floor.

`tests/test_unit_test_parts.py::test_the_durations_record_covers_the_live_tree`
fails when `config/unit-test-durations.json` carries seconds for fewer than 90%
of the test files, and every pull request that adds a test file moves the tree
toward it. Nothing re-measured until somebody did it by hand, so on 2026-10-18
bureau-pipeline #1168, a docs-only change, failed on files other pull requests
had added (run 39145109282, job 117727253638: `assert 693 >= (0.9 * 771)`).

The nightly `schedule` run of `.github/workflows/tests.yml` runs every non-walk
test file with `pytest -v` across its four `scripts unit tests (part N)` jobs,
so a green nightly's logs are a whole measurement. One job reads them:

  * it runs on the `schedule` event and on no other, waits on the four parts,
    and runs nothing unless all four succeeded;
  * it asks `unit_test_parts.py due` whether fewer than 95% of the files carry
    recorded seconds — and when not, it opens nothing and writes the coverage
    it read to the job summary;
  * when due, it re-measures from this run's own four part logs
    (`unit_test_parts.py refresh`), pushes `config/unit-test-durations.json`
    and no other path to the one literal branch `bot/test-durations`, and
    opens a pull request from it, or updates the one already open — never a
    second;
  * it pushes and opens with the GitHub App token: a pull request pushed with
    the workflow's `GITHUB_TOKEN` starts no workflow, so its checks never run
    and the merge gate never wakes for it.

The shell steps are run here, in bash, against a throwaway repository and a
fake `gh`, so what is asserted is what the steps do, not how they read.

Run: cd bureau-pipeline && python3 -m pytest tests/test_tests_workflow_durations_refresh.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTS_WF = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "unit_test_parts.py"

BRANCH = "bot/test-durations"
RECORD = "config/unit-test-durations.json"

#: The gate every step after the decision carries.
DUE = "steps.due.outputs.decision == 'refresh'"


def _jobs() -> dict:
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]


def _job() -> tuple[str, dict]:
    found = [(k, j) for k, j in _jobs().items()
             if "unit_test_parts.py due" in _runs(j)]
    assert len(found) == 1, [k for k, _ in found]
    return found[0]


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


def _step(needle: str) -> dict:
    _, job = _job()
    found = [s for s in job["steps"] if needle in str(s.get("run", ""))]
    assert len(found) == 1, (needle, [s.get("name") for s in found])
    return found[0]


def _bash(step: dict, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """A step's `run`, the way GitHub runs it: `bash -e`."""
    _, job = _job()
    full = dict(os.environ, **{k: str(v) for k, v in job.get("env", {}).items()},
                **env)
    return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd, env=full,
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 1. When it runs: the nightly, and only after every part passed.
# ---------------------------------------------------------------------------


def test_the_job_runs_on_the_schedule_event_only():
    _, job = _job()
    assert " ".join(str(job.get("if", "")).split()) == \
        "github.event_name == 'schedule'", job.get("if")


def test_the_job_waits_on_the_four_parts_and_needs_them_all_green():
    # With no status function in its `if:`, GitHub adds `success()`: the job
    # is skipped unless every entry of the `unit` matrix succeeded.
    _, job = _job()
    needs = job.get("needs")
    assert "unit" in ([needs] if isinstance(needs, str) else needs), needs
    cond = str(job.get("if", ""))
    for status in ("always()", "failure()", "cancelled()", "success()"):
        assert status not in cond, cond
    assert _jobs()["unit"]["strategy"]["matrix"]["part"] == [1, 2, 3, 4]


def test_the_branch_is_the_one_literal():
    _, job = _job()
    assert job.get("env", {}).get("BRANCH") == BRANCH
    push = _step("git push")
    assert "HEAD:refs/heads/$BRANCH" in push["run"], push["run"]


def test_every_step_after_the_decision_waits_on_it():
    _, job = _job()
    steps = job["steps"]
    at = steps.index(_step("unit_test_parts.py due"))
    assert steps[at].get("id") == "due"
    after = steps[at + 1:]
    assert after, "nothing follows the decision"
    for step in after:
        assert DUE in " ".join(str(step.get("if", "")).split()), step.get("name")


# ---------------------------------------------------------------------------
# 2. The decision, run in bash against a planted tree.
# ---------------------------------------------------------------------------


def _planted_tree(tmp_path: Path, measured: int, total: int) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
    (root / "tests").mkdir()
    names = [f"tests/test_{i:02d}.py" for i in range(total)]
    for name in names:
        (root / name).write_text("def test_x():\n    pass\n")
    (root / "config").mkdir()
    (root / RECORD).write_text(json.dumps({
        "method": "planted", "provenance": {},
        "seconds": {n: 1.0 for n in names[:measured]}}, indent=2) + "\n")
    return root


def _decide(tmp_path: Path, measured: int, total: int) -> tuple[dict, str]:
    root = _planted_tree(tmp_path, measured, total)
    output, summary = tmp_path / "output", tmp_path / "summary"
    output.touch()
    summary.touch()
    proc = _bash(_step("unit_test_parts.py due"), root,
                 {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
                  "RUNNER_TEMP": str(tmp_path)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()
                   if "=" in line)
    return outputs, summary.read_text()


def test_above_the_margin_it_opens_nothing_and_says_what_it_read(tmp_path):
    outputs, summary = _decide(tmp_path, 19, 20)
    assert outputs["decision"] == "hold", outputs
    for read in ("19", "20", "95.0%"):
        assert read in summary, summary


def test_under_the_margin_it_re_measures(tmp_path):
    outputs, summary = _decide(tmp_path, 18, 20)
    assert outputs["decision"] == "refresh", outputs
    assert "18" in summary and "90.0%" in summary, summary


# ---------------------------------------------------------------------------
# 3. The measurement: this run's own four part logs, through `refresh`.
# ---------------------------------------------------------------------------


def test_the_logs_are_this_runs_four_green_parts():
    step = _step("actions/jobs/")
    text = step["run"] + json.dumps(step.get("env", {}))
    assert "github.run_id" in text
    assert "scripts unit tests (part " in step["run"]
    assert "success" in step["run"]
    assert str(step.get("env", {}).get("PARTS")) == "4"
    # The App deliberately lacks actions:read (DRE-1346); reading this run's
    # own logs is the one thing the job's token is for.
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    _, job = _job()
    assert job.get("permissions", {}).get("actions") == "read"


def test_the_record_is_rebuilt_by_the_refresh_command():
    step = _step("unit_test_parts.py refresh")
    assert "--run" in step["run"] and "--job" in step["run"]
    env = json.dumps(step.get("env", {}))
    assert "github.run_id" in env and "steps.logs.outputs.jobs" in env


# ---------------------------------------------------------------------------
# 4. The push: the record and nothing else, to the one branch, with the App.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _clone(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "config").mkdir()
    (work / RECORD).write_text("{}\n")
    (work / "README.md").write_text("x\n")
    _git(work, "add", "-A")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    return origin, work


def test_the_push_carries_the_record_and_no_other_path(tmp_path):
    origin, work = _clone(tmp_path)
    (work / RECORD).write_text('{"seconds": {}}\n')
    (work / "README.md").write_text("changed by something else\n")
    proc = _bash(_step("git push"), work, {"RUN_ID": "39145109282"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    files = _git(origin, "diff", "--name-only", "main", BRANCH).split()
    assert files == [RECORD], files
    # Run again the next night: the branch is replaced, not stacked on.
    (work / RECORD).write_text('{"seconds": {"a": 1}}\n')
    _git(work, "reset", "-q", "--soft", "origin/main")
    proc = _bash(_step("git push"), work, {"RUN_ID": "39145109283"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(origin, "rev-list", "--count", f"main..{BRANCH}").strip() == "1"


def test_the_push_refuses_a_second_staged_path(tmp_path):
    origin, work = _clone(tmp_path)
    (work / RECORD).write_text('{"seconds": {}}\n')
    (work / "README.md").write_text("staged by something else\n")
    _git(work, "add", "README.md")
    proc = _bash(_step("git push"), work, {"RUN_ID": "1"})
    assert proc.returncode != 0
    assert BRANCH not in _git(origin, "branch", "--list")


def test_the_push_and_the_pull_request_use_the_app_token():
    _, job = _job()
    mints = [s for s in job["steps"]
             if str(s.get("uses", "")).startswith("actions/create-github-app-token@")]
    assert len(mints) == 1, mints
    mint = mints[0]
    assert mint["with"]["app-id"] == "${{ secrets.BUREAU_APP_ID }}"
    assert mint["with"]["private-key"] == "${{ secrets.BUREAU_APP_PRIVATE_KEY }}"
    app = f"${{{{ steps.{mint['id']}.outputs.token }}}}"
    checkouts = [s for s in job["steps"]
                 if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1 and checkouts[0]["with"]["token"] == app, checkouts
    assert job["steps"].index(mint) < job["steps"].index(checkouts[0])
    pr = _step("gh pr create")
    assert pr["env"]["GH_TOKEN"] == app
    for step in (_step("git push"), pr, checkouts[0]):
        text = json.dumps(step)
        assert "github.token" not in text and "GITHUB_TOKEN" not in text, step


# ---------------------------------------------------------------------------
# 5. One pull request: opened once, updated after.
# ---------------------------------------------------------------------------


def _fake_gh(tmp_path: Path, open_number: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls.txt"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        f'if [ "$1 $2" = "pr list" ]; then echo "{open_number}"; fi\n')
    gh.chmod(0o755)
    return bin_dir, calls


def _open_or_update(tmp_path: Path, open_number: str) -> list[str]:
    bin_dir, calls = _fake_gh(tmp_path, open_number)
    proc = _bash(_step("gh pr create"), tmp_path,
                 {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                  "RUNNER_TEMP": str(tmp_path), "RUN_ID": "39145109282",
                  "COVERAGE": "702 of 779 test files carry recorded seconds (90.1%)",
                  "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return calls.read_text().splitlines()


def test_with_none_open_it_opens_one_from_the_branch(tmp_path):
    calls = _open_or_update(tmp_path, "")
    listed = [c for c in calls if c.startswith("pr list")]
    assert len(listed) == 1 and f"--head {BRANCH}" in listed[0], calls
    created = [c for c in calls if c.startswith("pr create")]
    assert len(created) == 1, calls
    assert f"--head {BRANCH}" in created[0] and "--base main" in created[0]


def test_with_one_open_it_updates_that_one_and_opens_none(tmp_path):
    calls = _open_or_update(tmp_path, "1190")
    assert not [c for c in calls if c.startswith("pr create")], calls
    edited = [c for c in calls if c.startswith("pr edit")]
    assert len(edited) == 1 and "1190" in edited[0], calls
