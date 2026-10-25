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
import functools
import importlib
import json
import os
import re
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


#: The two names a walk's shard reaches it by (DRE-7907). Only the helper
#: reads them; a walk that names one is picking its own cases.
SHARD_NAMES = ("BUREAU_WALK_SHARD", "BUREAU_WALK_SHARDS")


def _helper_problems(walk: str, source: str) -> list[str]:
    """Every way the walk `source` carries machinery the helper owns."""
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    found = []
    if HELPER not in imported:
        found.append(f"{walk} does not use tests/{HELPER}.py")
    own = sorted(imported & OWN_COPY_MODULES)
    if own:
        found.append(f"{walk} carries its own tree-copy or inner-run code: {own}")
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    if defined & {"copy_tree", "_run", "save"}:
        found.append(f"{walk} defines "
                     f"{sorted(defined & {'copy_tree', '_run', 'save'})} — "
                     f"use the helper's")
    named = [name for name in SHARD_NAMES
             if re.search(rf"\b{name}\b", source)]
    if named:
        found.append(f"{walk} names {named} — only tests/{HELPER}.py reads a "
                     f"walk's shard")
    picks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "shard"]
    if not picks:
        found.append(f"{walk} never asks the helper for its shard "
                     f"(`walk.shard(...)`), so it runs every case in every "
                     f"shard")
    return found


@pytest.mark.parametrize("walk", _walks())
def test_every_walk_uses_the_one_shared_helper(walk):
    """The copy, the apply, the regenerate, the inner run and the pick of a
    shard's cases live once. A walk that imports `shutil`, `subprocess` or a
    thread pool of its own is carrying its own copy of them, and one that
    names a shard variable is picking its own cases (DRE-7907)."""
    found = _helper_problems(walk, (ROOT / walk).read_text(encoding="utf-8"))
    assert found == [], "\n".join(found)


@pytest.mark.parametrize("name", SHARD_NAMES)
def test_a_walk_that_names_a_shard_variable_fails_the_helper_check(name):
    walk = _walks()[0]
    source = (ROOT / walk).read_text(encoding="utf-8")
    assert _helper_problems(walk, source) == []
    planted = source + f"\nMINE = os.environ.get({name!r})\n"
    found = _helper_problems(walk, planted)
    assert len(found) == 1 and name in found[0], found


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
    of = len(_jobs()[PARTS_JOB]["strategy"]["matrix"]["part"])
    measured = parts.load_weights(parts.DURATIONS)
    for part in range(1, of + 1):
        handed = parts.part_files(ROOT, part, of, measured)
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
    for _, job in (_walks_job(), _walk_list_job()):
        text = yaml.safe_dump(job)
        assert "keeps_the_suite_green" not in text, (
            "the walks' jobs must find the walks by their name pattern, never "
            "list them")


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
    # Plus exactly three names: the walk the matrix entry was handed
    # (DRE-7016) and its shard (DRE-7907), and they reach the shell through
    # the environment, never the `run:`.
    assert step.get("env") == dict(
        unit_env, WALK="${{ matrix.walk }}",
        BUREAU_WALK_SHARD="${{ matrix.shard }}",
        BUREAU_WALK_SHARDS="${{ matrix.shards }}"), step.get("env")
    assert "${{" not in step["run"], step["run"]


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


# ---------------------------------------------------------------------------
# 4. One walk, one job (DRE-6935).
# ---------------------------------------------------------------------------
# On run 38178110152 the four walks shared one 45-minute job and it was
# canceled twice at its limit: the agent-move walk 17m45s, the change walk
# 0m12s, the every-default walk 20m42s, and the project walk cut off 6m25s in.
# The every-default walk grows with every model the page offers, so the walks
# run one to a job, the way the suite runs in parts, and the job list is read
# off the tree by a job before them — a walk added later needs no edit.


def _walk_list_job() -> tuple[str, dict]:
    found = [(key, job) for key, job in _jobs().items()
             if "unit_test_parts.py walks --json" in
             "\n".join(str(s.get("run", "")) for s in job.get("steps", []))]
    assert len(found) == 1, (
        f"exactly one job in tests.yml must list the walks "
        f"(`unit_test_parts.py walks --json`); found {[k for k, _ in found]}")
    return found[0]


def test_each_walk_shard_runs_in_a_job_of_its_own():
    """DRE-7907: one matrix entry per (walk, shard), built from the list job's
    output, and a red shard names its walk and its shard on the checks."""
    key, job = _walks_job()
    list_key, list_job = _walk_list_job()
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert list_key in needs, needs
    strategy = job.get("strategy") or {}
    assert strategy.get("fail-fast") is False, (
        "one red shard must not cancel the others: a canceled shard hides its "
        "own failures behind somebody else's")
    matrix = strategy.get("matrix") or {}
    assert list(matrix) == ["include"], matrix
    assert str(matrix["include"]).replace(" ", "") == (
        f"${{{{fromJSON(needs.{list_key}.outputs.walks)}}}}"), matrix
    name = job.get("name", "")
    assert "${{ matrix.walk }}" in name, name
    assert "shard ${{ matrix.shard }} of ${{ matrix.shards }}" in name, name
    outputs = list_job.get("outputs") or {}
    assert "walks" in outputs, outputs
    assert "if" not in list_job, "the walks are listed on every event"


def test_each_walk_job_runs_only_the_walk_and_the_shard_it_was_handed():
    _, job = _walks_job()
    step = next(s for s in job["steps"] if "run-walks" in str(s.get("run", "")))
    env = step.get("env") or {}
    assert env.get("WALK") == "${{ matrix.walk }}", env
    assert env.get("BUREAU_WALK_SHARD") == "${{ matrix.shard }}", env
    assert env.get("BUREAU_WALK_SHARDS") == "${{ matrix.shards }}", env
    assert '--only "$WALK"' in step["run"], step["run"]
    assert "${{" not in step["run"], step["run"]


def test_the_shard_names_are_set_in_the_walks_step_and_nowhere_else():
    """The two names reach a walk from the `walks` step's `env:` alone — no
    other job, step or line of tests.yml carries either."""
    text = TESTS_WF.read_text(encoding="utf-8")
    hits = re.findall(r"^.*\bBUREAU_WALK_SHARDS?\b.*$", text, re.M)
    assert [h.strip() for h in hits] == [
        "BUREAU_WALK_SHARD: ${{ matrix.shard }}",
        "BUREAU_WALK_SHARDS: ${{ matrix.shards }}",
    ], hits
    _, job = _walks_job()
    step = next(s for s in job["steps"] if "run-walks" in str(s.get("run", "")))
    assert set(SHARD_NAMES) <= set(step.get("env") or {})


def test_the_walk_list_reads_the_runner_chain_on_a_short_clock_of_its_own():
    """Like every other job here it runs where `BUREAU_CI_RUNS_ON` says, and
    listing files takes seconds, so its limit is minutes, not the walks'."""
    _, list_job = _walk_list_job()
    assert list_job.get("runs-on") == _jobs()[PARTS_JOB]["runs-on"], list_job.get("runs-on")
    assert 0 < int(list_job.get("timeout-minutes", 0)) <= 10, list_job.get("timeout-minutes")
    assert "if" not in list_job


def test_a_list_job_that_failed_leaves_the_walks_skipped_and_that_is_red():
    """When the list job fails, GitHub skips every walk, and `needs.walks.
    result` reads `skipped` — which must fail the required check, never pass
    it as a skipped job would pass the merge gate."""
    final = next(s for s in _aggregate()["steps"] if s.get("name") == "Every part passed")
    for walks, code in (("skipped", 1), ("failure", 1), ("cancelled", 1), ("success", 0)):
        proc = subprocess.run(
            ["bash", "-e", "-c", final["run"]], capture_output=True, text=True,
            env=dict(os.environ, PARTS="success", WALKS=walks))
        assert proc.returncode == code, (walks, proc.stdout, proc.stderr)


def test_the_walk_clock_is_over_twice_the_slowest_projected_shard():
    """The cap is a single shard's now (DRE-7907), held from each walk's CI
    seconds in `config/unit-test-durations.json` and its shard count."""
    _, job = _walks_job()
    measured = parts.load_weights(parts.DURATIONS)
    shards = _shard_counts(_tree_shards())
    projected = {rel: parts.projected_shard_seconds(
        measured[rel], len(parts.walk_cases(ROOT, rel)), shards[rel])
        for rel in parts.walks(ROOT)}
    slowest = max(projected.values())
    assert int(job["timeout-minutes"]) * 60 >= 2 * slowest, (
        job["timeout-minutes"], projected)


def _walk_source(cases: list[str], marks: Path | None = None) -> str:
    """A walk with the dict walks' shape: `CASES`, a `results` fixture that
    runs only the shard's cases through the helper, and one per-case test.
    Each case it runs leaves a file named for it in `marks`."""
    return (
        "import sys\n"
        "from pathlib import Path\n"
        "import pytest\n"
        f"sys.path.insert(0, {str(ROOT / 'tests')!r})\n"
        "import console_change_walk as walk\n"
        f"CASES = {dict.fromkeys(cases, 1)!r}\n"
        "SHARD = walk.shard(walk.case_names(CASES))\n"
        f"MARKS = {str(marks) if marks else ''!r}\n"
        "def run_case(name, base):\n"
        "    if MARKS:\n"
        "        Path(MARKS, name).write_text('ran')\n"
        "    return {'ok': True}\n"
        "@pytest.fixture(scope='module')\n"
        "def results(tmp_path_factory):\n"
        "    return walk.run_all(SHARD, run_case, tmp_path_factory.mktemp('w'))\n"
        "@pytest.mark.parametrize('name', SHARD)\n"
        "def test_the_case_passes(results, name):\n"
        "    assert results[name]['ok']\n")


def _record(tmp_path: Path, seconds: dict[str, float]) -> Path:
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"method": "planted", "provenance": {},
                                "seconds": seconds}))
    return path


def _shard_counts(entries: list[dict]) -> dict[str, int]:
    return {e["walk"]: e["shards"] for e in entries}


@functools.cache
def _tree_shards() -> tuple[dict, ...]:
    """`walk_shards` on this tree, read once: it imports every walk."""
    return tuple(parts.walk_shards(ROOT))


def test_walks_json_prints_one_entry_per_walk_shard_and_is_red_with_none(tmp_path):
    """DRE-7907: one object per (walk, shard), each carrying the walk, its
    shard number and its shard count, and as many entries as the walks'
    shard counts add up to."""
    one = "tests/test_one_keeps_the_suite_green.py"
    two = "tests/test_two_keeps_the_suite_green.py"
    root = _tree(tmp_path / "some", ["tests/test_a.py"])
    _tree(root, [one], _walk_source([f"c{i}" for i in range(10)]))
    _tree(root, [two], _walk_source(["a", "b", "c"]))
    # Ten cases over an hour need shards; three cases over 20 s fit in one.
    record = _record(tmp_path, {one: 3600.0, two: 20.0})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "walks", "--json", "--root", str(root),
         "--record", str(record)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "\n" not in proc.stdout.strip(), "a job output is one line"
    entries = json.loads(proc.stdout)
    counts = _shard_counts(entries)
    want = parts.shard_count(3600.0, 10)
    assert counts == {one: want, two: 1} and want > 1, counts
    assert entries == (
        [{"walk": one, "shard": n, "shards": want} for n in range(1, want + 1)]
        + [{"walk": two, "shard": 1, "shards": 1}]), entries
    assert len(entries) == sum(counts.values())
    none = _tree(tmp_path / "none", ["tests/test_a.py"])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "walks", "--json", "--root", str(none)],
        capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout == "", "no list at all, so the matrix is never empty"
    assert parts.WALK in proc.stderr, "the message names the pattern it looked for"


def test_walks_json_on_the_tree_is_every_shard_of_every_walk():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "walks", "--json"],
        capture_output=True, text=True, check=True)
    entries = json.loads(proc.stdout)
    assert entries == parts.walk_shards(ROOT)
    counts = _shard_counts(entries)
    assert sorted(counts) == parts.walks(ROOT)
    assert len(entries) == sum(counts.values())
    for rel, n in counts.items():
        assert [e["shard"] for e in entries if e["walk"] == rel] == list(
            range(1, n + 1)), rel


def test_the_walk_list_installs_what_walks_json_needs():
    """`walks --json` reads each walk's cases, so it imports the walks — and
    they import PyYAML and pytest. The list job installs them the way the
    walks' own job does, before it lists."""
    _, list_job = _walk_list_job()
    _, job = _walks_job()
    steps = list_job["steps"]
    install = next(i for i, s in enumerate(steps)
                   if "setup-python-cached" in str(s.get("uses", "")))
    listing = next(i for i, s in enumerate(steps)
                   if "walks --json" in str(s.get("run", "")))
    assert install < listing, steps
    walks_install = next(s for s in job["steps"]
                         if "setup-python-cached" in str(s.get("uses", "")))
    assert steps[install] == walks_install, steps[install]
    assert "python3 scripts/unit_test_parts.py walks --json" in steps[listing]["run"]


#: A test body that leaves a mark when pytest collects it, so a test can see
#: whether a file was run at all.
MARKS = ("import pathlib\n"
         "pathlib.Path(__file__).with_suffix('.ran').write_text('ran')\n")


def test_run_walks_only_runs_that_walk_alone(tmp_path):
    root = _tree(tmp_path, ["tests/test_a.py",
                            "tests/test_green_keeps_the_suite_green.py"])
    _tree(root, ["tests/test_red_keeps_the_suite_green.py"],
          MARKS + "def test_x():\n    assert False\n")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root),
         "--only", "tests/test_green_keeps_the_suite_green.py"],
        capture_output=True, text=True, cwd=root, env=_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 walk(s): 1 passed" in proc.stdout
    assert "test_red_keeps_the_suite_green.py" not in proc.stdout
    assert not (root / "tests" / "test_red_keeps_the_suite_green.ran").exists(), (
        "the failing walk ran")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root),
         "--only", "tests/test_red_keeps_the_suite_green.py"],
        capture_output=True, text=True, cwd=root, env=_env())
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 walk(s): 0 passed, 1 failed" in proc.stdout


def test_run_walks_refuses_a_walk_it_cannot_find(tmp_path):
    """A typo, or a file that is not a walk, would run nothing and pass — or
    run a file that is not a walk on the walks' clock. Neither starts pytest."""
    root = _tree(tmp_path, ["tests/test_one_keeps_the_suite_green.py"])
    _tree(root, ["tests/test_x.py"], MARKS + "def test_x():\n    pass\n")
    for named in ("tests/test_x.py", "tests/test_gone_keeps_the_suite_green.py"):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run-walks", "--root", str(root),
             "--only", named],
            capture_output=True, text=True, cwd=root, env=_env())
        assert proc.returncode != 0, named
        assert named in proc.stderr, proc.stderr
        assert "running walk" not in proc.stdout, proc.stdout
        assert not (root / "tests" / "test_x.ran").exists(), named


# ---------------------------------------------------------------------------
# 5. Each walk runs across shards (DRE-7907).
# ---------------------------------------------------------------------------
# On 2026-10-24 the every-default walk took 60m07s in one job (job
# 120989216560, run 40133214810), and `unit-result`, and the channel promotion
# behind it, waited on it. Each walk now runs its cases across as many jobs as
# its CI seconds need: `walks --json` hands the matrix one entry per (walk,
# shard), and the helper picks a shard's cases by two names alone.


def _helper():
    return _module(f"tests/{HELPER}.py")


def _shard_env(n: int, of: int) -> dict:
    return {SHARD_NAMES[0]: str(n), SHARD_NAMES[1]: str(of)}


def _cover(names: list[str], of: int, pick=None) -> list[str]:
    """Every problem with `of` shards of `names` taken together: a name no
    shard picked, a name two did, a name none of them should hold."""
    pick = pick or _helper().shard
    picked = [name for n in range(1, of + 1) for name in pick(names, _shard_env(n, of))]
    found = [f"{name} is in no shard of {of}" for name in names if name not in picked]
    found += [f"{name} is in {picked.count(name)} shards of {of}"
              for name in dict.fromkeys(picked) if picked.count(name) > 1]
    found += [f"{name} is not a case" for name in picked if name not in names]
    return found


@pytest.mark.parametrize("walk", parts.walks(ROOT))
def test_every_walks_shards_pick_every_case_exactly_once(walk):
    """At the count `walks --json` gives the walk, at a larger planted count,
    and at one shard more than it has cases."""
    names = _helper().case_names(_module(walk).CASES)
    assert names and len(names) == len(set(names)), names
    assert parts.walk_cases(ROOT, walk) == names
    of = _shard_counts(_tree_shards())[walk]
    for count in (of, of + 3, len(names) + 1):
        assert _cover(names, count) == [], count
    assert _helper().shard(names, {}) == names


def test_the_cover_check_fails_on_a_case_picked_twice_or_by_no_shard():
    names = ["a", "b", "c"]
    assert _cover(names, 2) == []
    twice = _cover(names, 2, pick=lambda names, env: list(names))
    assert any("in 2 shards" in p for p in twice), twice
    none = _cover(names, 2, pick=lambda names, env: [])
    assert len(none) == 3 and all("in no shard" in p for p in none), none
    first_only = _cover(names, 2, pick=lambda names, env: names[:1])
    assert any("b is in no shard" in p for p in first_only), first_only


def test_the_shard_is_picked_by_index_modulo_the_count():
    names = ["a", "b", "c", "d", "e"]
    shard = _helper().shard
    assert shard(names, _shard_env(1, 2)) == ["a", "c", "e"]
    assert shard(names, _shard_env(2, 2)) == ["b", "d"]
    assert shard(names, _shard_env(2, 3)) == ["b", "e"]
    assert shard(names, _shard_env(1, 1)) == names


@pytest.mark.parametrize("env", [
    {SHARD_NAMES[0]: "1"},
    {SHARD_NAMES[1]: "2"},
    _shard_env(0, 2),
    _shard_env(3, 2),
], ids=["shard-alone", "count-alone", "shard-0", "shard-over-count"])
def test_a_bad_shard_pair_raises(env):
    with pytest.raises(ValueError):
        _helper().shard(["a", "b", "c"], env)


def test_the_helper_reads_case_names_off_either_shape():
    helper = _helper()
    assert helper.case_names({"x": 1, "y": 2}) == ["x", "y"]
    assert helper.case_names([("x", {}), ("y", {})]) == ["x", "y"]


def test_an_inner_run_of_the_helper_sees_no_shard_name(tmp_path, monkeypatch):
    """A copy never runs a shard of a walk: the names stop at the walk."""
    for name, value in zip(SHARD_NAMES, ("2", "3")):
        monkeypatch.setenv(name, value)
    probe = "import json, os; print(json.dumps([os.environ.get(n) for n in %r]))"
    proc = _helper().run(tmp_path, "-c", probe % (SHARD_NAMES,))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [None, None]
    assert os.environ[SHARD_NAMES[0]] == "2"


def _dict_walks() -> list[str]:
    return [rel for rel in _walks() if isinstance(_module(rel).CASES, dict)]


def _collected_cases(walk: str, env: dict) -> set[str]:
    """The case names pytest generates per-case tests for in `walk` under
    `env`, read off `--collect-only`."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", walk],
        capture_output=True, text=True, cwd=ROOT,
        env={**{k: v for k, v in _env().items() if k not in SHARD_NAMES}, **env})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return set(re.findall(r"^\S+::\w+\[(.+)\]$", proc.stdout, re.M))


@pytest.mark.parametrize("walk", _dict_walks())
def test_a_dict_walk_generates_per_case_tests_for_its_shard_alone(walk):
    names = _helper().case_names(_module(walk).CASES)
    assert _collected_cases(walk, {}) == set(names)
    env = _shard_env(2, 3)
    want = _helper().shard(names, env)
    assert want and len(want) < len(names)
    assert _collected_cases(walk, env) == set(want)


def test_run_walks_names_the_shard_and_counts_its_cases(tmp_path):
    """A planted walk with three cases, run as shard n of 3: its `results`
    fixture runs one case, the line names the walk, the shard and that one
    case, the step summary carries the same line, and the three shards'
    counts add up to three."""
    rel = "tests/test_three_keeps_the_suite_green.py"
    marks = tmp_path / "marks"
    root = _tree(tmp_path / "repo", ["tests/test_a.py"])
    _tree(root, [rel], _walk_source(["a", "b", "c"], marks))
    counted, ran = 0, []
    for n in (1, 2, 3):
        marks.mkdir()
        summary = tmp_path / f"summary-{n}.md"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "run-walks", "--root", str(root),
             "--only", rel],
            capture_output=True, text=True, cwd=root,
            env=dict(_env(), GITHUB_STEP_SUMMARY=str(summary), **_shard_env(n, 3)))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        lines = [ln for ln in proc.stdout.splitlines() if f"shard {n} of 3" in ln]
        assert len(lines) == 1 and rel in lines[0], proc.stdout
        found = re.search(r"(\d+) case\(s\) selected", lines[0])
        assert found, lines[0]
        counted += int(found.group(1))
        assert lines[0] in summary.read_text(), summary.read_text()
        here = sorted(p.name for p in marks.iterdir())
        assert len(here) == int(found.group(1)) == 1, here
        ran += here
        for p in marks.iterdir():
            p.unlink()
        marks.rmdir()
    assert counted == 3
    assert sorted(ran) == ["a", "b", "c"]


def test_run_walks_with_no_shard_runs_every_case_as_one_shard(tmp_path):
    rel = "tests/test_three_keeps_the_suite_green.py"
    marks = tmp_path / "marks"
    marks.mkdir()
    root = _tree(tmp_path / "repo", ["tests/test_a.py"])
    _tree(root, [rel], _walk_source(["a", "b", "c"], marks))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root), "--only", rel],
        capture_output=True, text=True, cwd=root,
        env={k: v for k, v in _env().items() if k not in SHARD_NAMES})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"shard 1 of 1 of walk {rel}: 3 case(s) selected" in proc.stdout
    assert sorted(p.name for p in marks.iterdir()) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# 6. The walks run only when a change can reach them (DRE-7908).
# ---------------------------------------------------------------------------
# On 2026-10-24 PR #1119 merged one proof record and its run on main
# (40133214810) spent 46 to 60 minutes on walks nothing in it could turn red,
# while the channel promotion refused `unit-suite-not-finished`. `walk-list`
# now asks `scripts/walk_reach.py` once, and every walk step is gated on the
# answer. No document that exists is named here: a sample is
# `docs/no-such-proof.md`.

REACH = "scripts/walk_reach.py"
GATE = "needs.walk-list.outputs.run == 'true'"
SKIP_LINE = "skipped: no relevant change"


def _reach_step(list_job: dict) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(list_job["steps"])
             if REACH in str(s.get("run", ""))]
    assert len(found) == 1, f"exactly one walk-list step must ask {REACH}"
    return found[0]


def test_the_walk_list_holds_what_the_decision_needs():
    """Its own token reads earlier runs (the App lacks `actions: read`,
    DRE-1346), the history is real for the ancestry check, and the walks'
    imports are installed before the decision imports them."""
    key, list_job = _walk_list_job()
    assert key == "walk-list"
    assert list_job.get("permissions") == {"contents": "read", "actions": "read"}
    checkout = next(s for s in list_job["steps"]
                    if "actions/checkout" in str(s.get("uses", "")))
    assert (checkout.get("with") or {}).get("fetch-depth") == 0, checkout
    install = next(i for i, s in enumerate(list_job["steps"])
                   if "setup-python-cached" in str(s.get("uses", "")))
    i, step = _reach_step(list_job)
    assert install < i
    assert list_job["steps"][install]["with"]["requirements"].strip() == "requirements-dev.txt"
    assert "if" not in step
    assert step["env"].get("GH_TOKEN") == "${{ github.token }}"
    outputs = list_job.get("outputs") or {}
    sid = step.get("id")
    assert outputs.get("run") == f"${{{{ steps.{sid}.outputs.run }}}}", outputs
    assert outputs.get("reason") == f"${{{{ steps.{sid}.outputs.reason }}}}", outputs
    assert "${{" not in step["run"], "inputs reach the shell through env alone"


def test_the_push_base_is_never_event_before():
    """Push A goes red, push B changes a doc: against `event.before` B would
    skip and move `stable` past A. The base is the last green main commit."""
    _, list_job = _walk_list_job()
    assert "event.before" not in yaml.safe_dump(list_job)
    _, step = _reach_step(list_job)
    assert "walk_reach.py base" in step["run"]
    assert "actions/workflows/tests.yml/runs" in step["run"]


def test_the_walk_list_is_still_red_with_no_walk():
    _, list_job = _walk_list_job()
    listing = next(s for s in list_job["steps"]
                   if "walks --json" in str(s.get("run", "")))
    assert "if" not in listing and "||" not in listing["run"], listing


def _stub_bin(tmp_path: Path, runs: str | None, changed: list[str] | None,
              ancestor: str = "") -> tuple[Path, Path]:
    """`gh` and `git` stubs on a PATH of their own: `gh` serves the run list
    and the compare's file list (or fails where one is None) and logs every
    call; `git merge-base --is-ancestor` holds only for `ancestor`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    (tmp_path / "runs.json").write_text(runs or "")
    (tmp_path / "changed.txt").write_text("".join(f"{c}\n" for c in changed or []))
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$*\" >> {str(log)!r}\n"
        "case \"$*\" in\n"
        f"  *actions/workflows/*) {'cat ' + repr(str(tmp_path / 'runs.json')) if runs is not None else 'exit 1'} ;;\n"
        f"  *compare/*) {'cat ' + repr(str(tmp_path / 'changed.txt')) if changed is not None else 'exit 1'} ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n")
    git = bin_dir / "git"
    git.write_text(
        "#!/usr/bin/env bash\n"
        f"[ \"$1 $2 $3\" = \"merge-base --is-ancestor {ancestor}\" ]\n")
    for path in (gh, git):
        path.chmod(0o755)
    (bin_dir / "python3").symlink_to(sys.executable)
    return bin_dir, log


def _run_reach(tmp_path: Path, event: str, runs=None, changed=None,
               ancestor: str = "", head: str = "e" * 40) -> tuple[dict, str, str]:
    _, list_job = _walk_list_job()
    _, step = _reach_step(list_job)
    bin_dir, log = _stub_bin(tmp_path, runs, changed, ancestor)
    out = tmp_path / "output.txt"
    out.write_text("")
    env = dict(_env(), **{k: str(v) for k, v in step["env"].items()
                          if "${{" not in str(v)})
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out),
               RUNNER_TEMP=str(tmp_path), GITHUB_REPOSITORY="test/test",
               GH_TOKEN="test", EVENT=event, HEAD=head,
               PR_BASE="f" * 40 if event == "pull_request" else "")
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], capture_output=True,
                          text=True, cwd=ROOT, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return got, proc.stdout, log.read_text() if log.exists() else ""


def test_the_walk_list_step_reads_the_env_it_is_run_with_here():
    _, step = _reach_step(_walk_list_job()[1])
    for name in ("EVENT", "HEAD", "PR_BASE", "GH_TOKEN"):
        assert name in step["env"], step["env"]
    assert step["env"]["EVENT"] == "${{ github.event_name }}"
    assert step["env"]["PR_BASE"] == "${{ github.event.pull_request.base.sha }}"


def test_a_schedule_run_runs_every_walk_and_asks_nothing(tmp_path):
    got, stdout, calls = _run_reach(tmp_path, "schedule")
    assert got["run"] == "true", got
    assert "schedule" in got["reason"], got
    assert calls == "", "a schedule run has no range, so nothing is fetched"


@pytest.mark.parametrize("event", ["workflow_dispatch", "merge_group"])
def test_any_other_event_runs_every_walk(tmp_path, event):
    got, _, _ = _run_reach(tmp_path, event)
    assert got["run"] == "true" and event in got["reason"], got


def test_a_pull_request_of_one_absent_doc_skips_and_says_why(tmp_path):
    got, stdout, calls = _run_reach(tmp_path, "pull_request", changed=["docs/no-such-proof.md"])
    assert got["run"] == "false", (got, stdout)
    assert got["reason"], got
    assert f"compare/{'f' * 40}...{'e' * 40}" in calls, calls


def test_a_pull_request_touching_the_workflow_runs_and_names_it(tmp_path):
    got, stdout, _ = _run_reach(tmp_path, "pull_request",
                                changed=[".github/workflows/tests.yml", "docs/no-such-proof.md"])
    assert got["run"] == "true" and ".github/workflows/tests.yml" in got["reason"], got
    assert ".github/workflows/tests.yml" in stdout


def test_a_compare_that_fails_runs_every_walk(tmp_path):
    got, _, _ = _run_reach(tmp_path, "pull_request", changed=None)
    assert got["run"] == "true" and got["reason"], got


def _runs_body(*runs: tuple[str, str]) -> str:
    return json.dumps({"total_count": len(runs), "workflow_runs": [
        {"head_sha": sha, "conclusion": conclusion, "status": "completed",
         "event": "push", "head_branch": "main",
         "created_at": f"2026-10-24T2{i}:00:00Z"} for i, (sha, conclusion) in
        enumerate(reversed(runs))][::-1]})


def test_a_push_compares_from_the_last_green_commit_on_main(tmp_path):
    green, red = "a" * 40, "b" * 40
    got, _, calls = _run_reach(tmp_path, "push",
                               runs=_runs_body((red, "failure"), (green, "success")),
                               changed=["docs/no-such-proof.md"], ancestor=green)
    assert f"compare/{green}...{'e' * 40}" in calls, calls
    assert got["run"] == "false", got


@pytest.mark.parametrize("case", ["lookup-fails", "no-success", "not-ancestor"])
def test_a_push_with_no_usable_base_runs_every_walk(tmp_path, case):
    green = "a" * 40
    runs = None if case == "lookup-fails" else _runs_body(
        (green, "failure" if case == "no-success" else "success"))
    got, _, calls = _run_reach(tmp_path, "push", runs=runs,
                               changed=["docs/no-such-proof.md"], ancestor="")
    assert got["run"] == "true" and got["reason"], got
    assert "compare/" not in calls, calls


def _walks_skip_step(job: dict) -> dict:
    ungated = [s for s in job["steps"] if "if" not in s]
    assert len(ungated) == 1, (
        f"exactly one walks step runs whatever walk-list answered; found {ungated}")
    return ungated[0]


def test_every_step_that_installs_or_runs_a_walk_is_gated_and_the_job_is_not():
    _, job = _walks_job()
    assert "if" not in job, "a skipped required job counts as passing"
    skip = _walks_skip_step(job)
    for step in job["steps"]:
        if step is not skip:
            assert step.get("if") == GATE, step
    for needle in ("actions/checkout", "setup-python-cached", "run-walks"):
        assert any(needle in str(s.get("uses", "")) + str(s.get("run", ""))
                   for s in job["steps"] if s is not skip), needle
    assert "run-walks" not in str(skip.get("run", ""))


def _run_skip_step(tmp_path: Path, run: str) -> tuple[subprocess.CompletedProcess, str]:
    _, job = _walks_job()
    step = _walks_skip_step(job)
    summary = tmp_path / "summary.md"
    summary.write_text("")
    env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary), RUN=run,
               REASON="none of the 1 changed file(s) reach a walk")
    assert step["env"]["RUN"] == "${{ needs.walk-list.outputs.run }}"
    assert step["env"]["REASON"] == "${{ needs.walk-list.outputs.reason }}"
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], capture_output=True,
                          text=True, env=env)
    return proc, summary.read_text()


def test_a_skipped_walk_says_so_in_its_log_and_its_summary(tmp_path):
    proc, summary = _run_skip_step(tmp_path, "false")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = f"{SKIP_LINE} — none of the 1 changed file(s) reach a walk"
    assert line in proc.stdout and line in summary, (proc.stdout, summary)


def test_a_running_walk_prints_no_skip_line(tmp_path):
    proc, summary = _run_skip_step(tmp_path, "true")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert SKIP_LINE not in proc.stdout + summary


@pytest.mark.parametrize("run", ["", "maybe"])
def test_a_walk_list_answer_that_is_neither_is_red(tmp_path, run):
    """Every walk step is gated on `'true'`, so an answer that is neither
    would leave the shard a silent green."""
    proc, _ = _run_skip_step(tmp_path, run)
    assert proc.returncode != 0, proc.stdout


def test_the_required_check_needs_the_walk_list_and_says_why_the_walks_were_skipped(tmp_path):
    job = _aggregate()
    needs = job.get("needs")
    assert "walk-list" in needs and "walks" in needs, needs
    final = next(s for s in job["steps"] if s.get("name") == "Every part passed")
    assert final["env"]["WALKS_RUN"] == "${{ needs.walk-list.outputs.run }}"
    assert final["env"]["WALKS_REASON"] == "${{ needs.walk-list.outputs.reason }}"
    summary = tmp_path / "summary.md"
    for walks, run, code, says in (
            ("success", "false", 0, True), ("success", "true", 0, False),
            ("skipped", "", 1, False), ("failure", "true", 1, False)):
        proc = subprocess.run(
            ["bash", "-e", "-c", final["run"]], capture_output=True, text=True,
            env=dict(os.environ, PARTS="success", WALKS=walks, WALKS_RUN=run,
                     WALKS_REASON="none of the 1 changed file(s) reach a walk",
                     GITHUB_STEP_SUMMARY=str(summary)))
        assert proc.returncode == code, (walks, run, proc.stdout, proc.stderr)
        assert (f"{SKIP_LINE} — none of the 1 changed file(s) reach a walk"
                in proc.stdout) is says, (walks, run, proc.stdout)
