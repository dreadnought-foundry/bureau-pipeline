"""RED-first tests for DRE-7577 — the nightly re-measures the durations record
before it falls under its 90% floor.

`tests/test_unit_test_parts.py::test_the_durations_record_covers_the_live_tree`
fails when `config/unit-test-durations.json` carries seconds for fewer than 90%
of the test files, and every pull request that adds a test file moves the tree
toward it. Nothing re-measured until somebody did it by hand, so on 2026-10-18
bureau-pipeline #1168, a docs-only change, failed on files other pull requests
had added (run 39145109282, job 117727253638: `assert 693 >= (0.9 * 771)`).

The nightly `schedule` run of `.github/workflows/tests.yml` runs every non-walk
test file with `pytest -v` across its five `scripts unit tests (part N)` jobs,
so a green nightly's logs are a whole measurement. One job reads them:

  * it runs on the `schedule` event and on no other, waits on the five parts,
    and runs only when each of them succeeded or failed — and a failed part
    is measured only when `unit_test_parts.py coverage-only` says it is red
    on the durations-coverage tests and on nothing else (DRE-7726: under the
    floor, the part running those tests is red on the floor itself, so a
    refresh that needed every part green could never run again);
  * it asks `unit_test_parts.py due` whether fewer than 95% of the files carry
    recorded seconds — and when not, it opens nothing and writes the coverage
    it read to the job summary;
  * when due, it re-measures from this run's own five part logs
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

import pytest
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
# 1. When it runs: the nightly, once every part finished.
# ---------------------------------------------------------------------------


#: The job's whole `if:` (DRE-7726): the nightly only, after the parts
#: finished — succeeded, or failed, which a part red only on the
#: durations-coverage tests does. `always()` lets the job run past a failed
#: part; the result test keeps out a canceled or skipped `unit`.
GATE = ("always() && github.event_name == 'schedule' && "
        "(needs.unit.result == 'success' || needs.unit.result == 'failure')")


def _gate(event: str, unit: str) -> bool:
    """The job's `if:`, evaluated for one event and one `unit` result. The
    expression uses only `&&`, `||`, `==`, parentheses, `always()` and two
    contexts, so it is rewritten into Python and evaluated as written."""
    _, job = _job()
    cond = " ".join(str(job.get("if", "")).split())
    assert cond == GATE, cond
    py = (cond.replace("&&", " and ").replace("||", " or ")
          .replace("always()", "True")
          .replace("github.event_name", "event")
          .replace("needs.unit.result", "unit"))
    return bool(eval(py, {"__builtins__": {}}, {"event": event, "unit": unit}))


def test_the_job_runs_on_the_schedule_event_only():
    # Rewritten for DRE-7726: the event test is unchanged, and now sits
    # beside `always()` and the `unit` result.
    for event in ("push", "pull_request", "workflow_dispatch"):
        for unit in ("success", "failure"):
            assert not _gate(event, unit), (event, unit)
    assert _gate("schedule", "success")


def test_the_job_waits_on_the_five_parts_and_runs_when_they_finished():
    # Rewritten for DRE-7726 (was ..._and_needs_them_all_green): a failed
    # `unit` no longer skips the job — the fetch step decides whether its red
    # parts are red only on the durations-coverage tests. A canceled or
    # skipped `unit` still runs nothing.
    _, job = _job()
    needs = job.get("needs")
    assert "unit" in ([needs] if isinstance(needs, str) else needs), needs
    assert _gate("schedule", "success") and _gate("schedule", "failure")
    for unit in ("cancelled", "skipped"):
        assert not _gate("schedule", unit), unit
    cond = str(job.get("if", ""))
    for status in ("failure()", "cancelled()", "success()"):
        assert status not in cond, cond
    # Five since DRE-8122: part 4 of four took 24 to 29 minutes against its
    # 30-minute cap and was canceled at it on five pull-request runs.
    assert _jobs()["unit"]["strategy"]["matrix"]["part"] == [1, 2, 3, 4, 5]


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
# 3. The measurement: this run's own five part logs, through `refresh`.
# ---------------------------------------------------------------------------


def test_the_logs_are_this_runs_five_finished_parts():
    # Rewritten for DRE-7726 (was test_the_logs_are_this_runs_four_green_parts):
    # a part that concluded `failure` is kept beside the green ones, every
    # part is still required (five since DRE-8122), and the red ones go
    # through `coverage-only` before anything is measured.
    step = _step("actions/jobs/")
    text = step["run"] + json.dumps(step.get("env", {}))
    assert "github.run_id" in text
    assert "scripts unit tests (part " in step["run"]
    assert '.conclusion == "success" or .conclusion == "failure"' in step["run"]
    assert str(step.get("env", {}).get("PARTS")) == "5"
    assert "unit_test_parts.py coverage-only" in step["run"]
    _, job = _job()
    steps = job["steps"]
    assert steps.index(step) < steps.index(_step("unit_test_parts.py refresh"))
    # The App deliberately lacks actions:read (DRE-1346); reading this run's
    # own logs is the one thing the job's token is for.
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    _, job = _job()
    assert job.get("permissions", {}).get("actions") == "read"


def test_the_fetch_expects_as_many_parts_as_unit_runs():
    """`PARTS` is the one other place the parts are counted (DRE-8122). Left
    behind when `matrix.part` grows, the fetch finds more finished parts than
    it expects and refuses every nightly; left behind when it shrinks, it
    never finds enough. Either way the record stops being re-measured."""
    step = _step("actions/jobs/")
    matrix = _jobs()["unit"]["strategy"]["matrix"]["part"]
    assert str(step.get("env", {}).get("PARTS")) == str(len(matrix)), (
        f"the fetch step expects {step.get('env', {}).get('PARTS')} finished "
        f"parts, but `unit` runs {len(matrix)} ({matrix})")


def test_the_record_is_rebuilt_by_the_refresh_command():
    step = _step("unit_test_parts.py refresh")
    assert "--run" in step["run"] and "--job" in step["run"]
    env = json.dumps(step.get("env", {}))
    assert "github.run_id" in env and "steps.logs.outputs.jobs" in env


def test_the_refresh_is_told_which_parts_were_red():
    step = _step("unit_test_parts.py refresh")
    assert "--coverage-failed-job" in step["run"]
    assert step["env"]["FAILED"] == "${{ steps.logs.outputs.failed }}"


def _measure_tree(tmp_path: Path) -> Path:
    """A throwaway repository holding this tree's `unit_test_parts.py`, three
    test files and a record that has seconds for one of them — so the
    script's own `ROOT` and record are the throwaway ones, never this tree's."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / SCRIPT.name)
    for name in ("test_alpha.py", "test_beta.py", "test_gamma.py"):
        (repo / "tests").mkdir(exist_ok=True)
        (repo / "tests" / name).write_text("def test_x():\n    pass\n")
    (repo / "config").mkdir()
    (repo / RECORD).write_text(json.dumps({
        "method": "CI run 1, jobs 1 and 2: the suite in four parts",
        "provenance": {}, "seconds": {"tests/test_alpha.py": 9.9}}) + "\n")
    _git(tmp_path, "init", "-q", str(repo))
    return repo


@pytest.mark.parametrize("failed", ["", "702"])
def test_the_re_measure_step_runs_the_refresh_it_names(tmp_path, failed):
    """The step's `run`, in bash, over two part logs: the argv it builds must
    be one `refresh` accepts. Run 40310480396 died here on 2026-10-26 with
    `unrecognized arguments:` naming every log after the first — the step put
    a `--job` between the logs, and the `logs` positional takes one run."""
    repo = _measure_tree(tmp_path)
    logs = tmp_path / "part-logs"
    logs.mkdir()
    (logs / "job-701.log").write_text(
        "2026-10-19T10:50:00.0000000Z tests/test_alpha.py::test_a PASSED [  0%]\n"
        "2026-10-19T10:50:02.0000000Z tests/test_alpha.py::test_b PASSED [ 50%]\n"
        "2026-10-19T10:50:05.5000000Z tests/test_beta.py::test_c PASSED [100%]\n")
    (logs / "job-702.log").write_text(
        "2026-10-19T10:51:00.0000000Z tests/test_gamma.py::test_d PASSED [  0%]\n"
        "2026-10-19T10:51:04.2500000Z tests/test_gamma.py::test_e PASSED [100%]\n")
    proc = _bash(_step("unit_test_parts.py refresh"), repo, {
        "RUNNER_TEMP": str(tmp_path), "RUN_ID": "40310480396",
        "JOBS": "701 702", "FAILED": failed, "HEAD_SHA": "6f66363b"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = json.loads((repo / RECORD).read_text())
    assert record["seconds"] == {"tests/test_alpha.py": 2.0,
                                 "tests/test_beta.py": 3.5,
                                 "tests/test_gamma.py": 4.25}, record
    assert "40310480396" in record["method"], record["method"]
    assert "701 and 702" in record["method"], record["method"]
    if failed:
        assert "job 702 red only on the durations-coverage tests" in \
            record["method"], record["method"]


# The fetch step, run in bash over a fake `gh` that serves this run's jobs
# through the real `jq` and each job's log from a file.

FAKE_GH = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
path = next(a for a in args if a.startswith("repos/"))
with open(os.environ["GH_CALLS"], "a") as fh:
    fh.write(path + "\\n")
if path.endswith("/logs"):
    # gh refuses a body holding terminal escape sequences unless told to
    # allow them, and every Actions log holds them in its step headers
    # (scripts/reconcile.py's `_read_job_log` says the same).
    if "--allow-escape-sequences" not in args:
        sys.stderr.write("the response contains terminal escape sequences; "
                         "pass --allow-escape-sequences to output it anyway\\n")
        sys.exit(1)
    job = path.split("/")[-2]
    with open(os.path.join(os.environ["FAKE_LOGS"], job + ".log")) as fh:
        sys.stdout.write(fh.read())
    sys.exit(0)
with open(os.environ["FAKE_JOBS"]) as fh:
    data = fh.read()
sys.exit(subprocess.run(["jq", "-r", args[args.index("--jq") + 1]],
                        input=data, text=True).returncode)
"""

COVERAGE_FAILED = [
    "tests/test_unit_test_parts.py::test_the_durations_record_covers_the_live_tree",
    "tests/test_unit_test_parts.py::test_the_coverage_check_fails_under_ninety_percent",
    "tests/test_unit_test_parts.py::test_the_coverage_check_fails_without_the_shared_method[None]",
]


def _log(failed: list[str], summary: bool = True) -> str:
    lines = ["tests/test_alpha.py::test_a PASSED [  0%]"]
    lines += [f"FAILED {node} - AssertionError: under 90%" for node in failed]
    if summary:
        lines.append(f"===== {len(failed) or 1} {'failed' if failed else 'passed'}"
                     f", 10 passed in 61.00s (0:01:01) =====")
    return "".join(f"2026-10-20T10:41:{i:02d}.0000000Z {line}\n"
                   for i, line in enumerate(lines))


def _fetch(tmp_path: Path, parts: dict) -> tuple[subprocess.CompletedProcess,
                                                 dict, list[str]]:
    """Run the fetch step over `parts` — `{job id: (conclusion, log)}`."""
    bin_dir, logs = tmp_path / "bin", tmp_path / "logs"
    bin_dir.mkdir()
    logs.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    jobs = [{"id": job, "name": f"scripts unit tests (part {n})",
             "conclusion": conclusion}
            for n, (job, (conclusion, _)) in enumerate(parts.items(), 1)]
    jobs.append({"id": 1, "name": "scripts unit tests", "conclusion": "failure"})
    (tmp_path / "jobs.json").write_text(json.dumps({"jobs": jobs}))
    for job, (_, text) in parts.items():
        (logs / f"{job}.log").write_text(text)
    output, calls = tmp_path / "output", tmp_path / "gh-calls.txt"
    output.touch()
    calls.touch()
    proc = _bash(_step("actions/jobs/"), ROOT, {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
        "RUN_ID": "39400000000", "PARTS": str(_step("actions/jobs/")["env"]["PARTS"]),
        "GH_CALLS": str(calls),
        "FAKE_JOBS": str(tmp_path / "jobs.json"), "FAKE_LOGS": str(logs)})
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()
                   if "=" in line)
    return proc, outputs, calls.read_text().splitlines()


def test_a_part_red_only_on_the_coverage_tests_is_measured(tmp_path):
    proc, outputs, _ = _fetch(tmp_path, {
        701: ("success", _log([])), 702: ("failure", _log(COVERAGE_FAILED)),
        703: ("success", _log([])), 704: ("success", _log([])),
        705: ("success", _log([]))})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["jobs"].split() == ["701", "702", "703", "704", "705"], outputs
    assert outputs["failed"].split() == ["702"], outputs
    for job in (701, 702, 703, 704, 705):
        assert (tmp_path / "part-logs" / f"job-{job}.log").is_file()


def test_five_green_parts_name_no_red_part(tmp_path):
    proc, outputs, _ = _fetch(tmp_path, {
        job: ("success", _log([])) for job in (701, 702, 703, 704, 705)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["failed"] == "", outputs


def test_a_part_red_on_another_test_ends_the_job_before_measuring(tmp_path):
    other = "tests/test_merge_gate.py::test_the_gate_holds"
    proc, outputs, _ = _fetch(tmp_path, {
        701: ("success", _log([])),
        702: ("failure", _log(COVERAGE_FAILED + [other])),
        703: ("success", _log([])), 704: ("success", _log([])),
        705: ("success", _log([]))})
    assert proc.returncode != 0
    errors = [line for line in proc.stdout.splitlines()
              if line.startswith("::error::")]
    assert len(errors) == 1 and other in errors[0], proc.stdout
    assert "jobs" not in outputs, outputs


def test_a_red_part_with_no_summary_line_ends_the_job(tmp_path):
    proc, outputs, _ = _fetch(tmp_path, {
        701: ("success", _log([])), 702: ("failure", _log([], summary=False)),
        703: ("success", _log([])), 704: ("success", _log([])),
        705: ("success", _log([]))})
    assert proc.returncode != 0
    assert "::error::" in proc.stdout, proc.stdout
    assert "jobs" not in outputs, outputs


def test_a_canceled_part_is_not_a_whole_measurement(tmp_path):
    proc, outputs, calls = _fetch(tmp_path, {
        701: ("success", _log([])), 702: ("cancelled", _log([])),
        703: ("success", _log([])), 704: ("failure", _log(COVERAGE_FAILED)),
        705: ("success", _log([]))})
    assert proc.returncode != 0
    assert "found 4" in proc.stdout and "not 5" in proc.stdout, proc.stdout
    assert not [c for c in calls if c.endswith("/logs")], calls
    assert "jobs" not in outputs, outputs


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
