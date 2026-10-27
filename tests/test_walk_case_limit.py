"""DRE-8152 — a walk case that hangs fails by name, before the job's clock.

On 2026-10-27 `main`'s Pipeline Tests run 40502923604 (the merge of #1275,
which changed only `config/unit-test-durations.json`) lost its job `scripts
unit tests (walk tests/test_console_project_model_keeps_the_suite_green.py
shard 5 of 8)`, job 122184991649. The shard printed `4 case(s) selected of
31`, passed its four case-free tests by 12:20:53Z, then printed nothing until
the job's 20-minute `timeout-minutes` canceled it at 12:40:43Z. The run
concluded `cancelled`, so Promote Channel refused to move `stable` and
Red-Main Repair skipped. The 27 push runs on `main` before it ran that shard,
the same four `*-advisory` cases, in 3 to 6 minutes.

What hung: at cleanup the runner terminated ten orphaned `python3`
processes — pids 2114 and 2115 (`run-walks` and the walk's pytest), 2140 to
2143, and 5027, 5082, 5083 and 5092. Each case makes five subprocesses in
order (the reset's regenerate, the apply, the regenerate, its `--check`,
the inner pytest), and the runner's four cores ran the four cases side by
side, so 2140 to 2143 are the four inner pytest runs, started together; the
four later pids are children those inner suites started and never got back.
The helper's `run` was `subprocess.run(capture_output=True)` with no
timeout, the inner run printed `-q` dots into memory, and nothing above it
had a clock shorter than the job's — so a hung child held its case, its
case held the shard, and the cancel threw away the only output that could
have named the test.

So, two limits, both under the job's clock:

  * A CASE LIMIT (`tests/console_change_walk.py`'s `CASE_LIMIT_SECONDS`).
    Every subprocess a case runs starts in a process group of its own, and
    every case shares one deadline from when the walk's cases start. Past it
    the whole group is killed — the children it left too, which would
    otherwise hold its output pipe open and block the read forever — and
    that case's tests fail with the walk, the shard and the case named, and
    the last of its output. The inner run is `-v` and unbuffered, so that
    output ends on the inner test that hung. The limit is the walks job's:
    it holds when that job's shard is named, and a run with none named —
    `suite-green`'s hour of the change walk's cases — keeps no limit.
  * A WALK LIMIT (`scripts/unit_test_parts.py`'s `WALK_LIMIT_SECONDS`), the
    backstop for a hang outside any subprocess: `run-walks` stops the walk's
    pytest and names the walk, the shard and the cases it selected.

`tests/test_walks_time_budget.py` holds both under the walks job's
`timeout-minutes`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_walk_case_limit.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import console_change_walk as walk  # noqa: E402
import unit_test_parts as parts  # noqa: E402

SCRIPT = ROOT / "scripts" / "unit_test_parts.py"

#: Long enough that a test waiting on it out is a test that failed.
FOREVER = 120

#: A child that starts a grandchild holding its output open, says so, and
#: sleeps — the shape of the hang on job 122184991649.
HANG = (
    "import subprocess, sys, time\n"
    f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep({FOREVER})'])\n"
    "print('started a child that will not exit', flush=True)\n"
    f"time.sleep({FOREVER})\n"
)


def _env(**more) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GITHUB_STEP_SUMMARY", walk.SHARD_ENV, walk.SHARDS_ENV)}
    env.update(more)
    return env


def _walk_source(cases: dict[str, str], limit: int) -> str:
    """A planted walk: each case runs its own `python3 -c <code>` through the
    helper, the cases share `limit` seconds, one test per case."""
    return textwrap.dedent(f"""\
        import sys
        import pytest
        sys.path.insert(0, {str(ROOT / 'tests')!r})
        import console_change_walk as walk
        CASES = {cases!r}
        SHARD = walk.shard(walk.case_names(CASES))
        def run_case(name, base):
            base.mkdir(parents=True)
            proc = walk.run(base, "-c", CASES[name])
            return {{"returncode": proc.returncode}}
        @pytest.fixture(scope="module")
        def results(tmp_path_factory):
            return walk.run_all(SHARD, run_case, tmp_path_factory.mktemp("w"),
                                limit={limit})
        @pytest.mark.parametrize("name", SHARD)
        def test_the_case_passes(results, name):
            assert results[name]["returncode"] == 0
        """)


def _plant(root: Path, rel: str, source: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    (root / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    return path


def test_a_case_past_its_limit_fails_naming_the_walk_the_shard_and_the_case(tmp_path):
    """The whole path the walks job runs: `run-walks --only` as shard 1 of
    1. The hung case fails by name and the quick one passes, long before
    the hung one's child would have let go."""
    rel = "tests/test_hangs_keeps_the_suite_green.py"
    root = tmp_path / "repo"
    _plant(root, rel, _walk_source({"quick": "pass", "stuck": HANG}, limit=3))
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "run-walks", "--root", str(root),
         "--only", rel],
        capture_output=True, text=True, cwd=root, timeout=FOREVER,
        env=_env(**{walk.SHARD_ENV: "1", walk.SHARDS_ENV: "1"}))
    took = time.monotonic() - start
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert took < FOREVER / 2, f"the hung case held the walk {took:.0f} s"
    assert "test_the_case_passes[quick] PASSED" in out, out
    assert "test_the_case_passes[stuck] FAILED" in out, out
    said = [ln for ln in out.splitlines() if "ran past its 3 s limit" in ln]
    assert said, out
    assert any(f"walk {rel}" in ln and "shard 1 of 1" in ln
               and "case stuck" in ln for ln in said), said
    assert not any("case quick" in ln for ln in said), said
    assert "started a child that will not exit" in out, (
        "the hung case's own output is in the log")


def test_a_hung_child_and_the_child_it_left_are_both_stopped(tmp_path):
    """`subprocess.run(timeout=...)` kills only the child, then reads its
    pipes until they close — and the grandchild holds them open. The case
    limit kills the child's whole process group."""
    def run_case(name, base):
        base.mkdir(parents=True)
        return {"proc": walk.run(base, "-c", HANG)}

    start = time.monotonic()
    got = walk.run_all(["stuck"], run_case, tmp_path, limit=2)
    took = time.monotonic() - start
    assert took < FOREVER / 2, f"run_all waited {took:.0f} s on the grandchild"
    with pytest.raises(walk.CaseOverLimit) as over:
        got["stuck"]
    said = str(over.value)
    assert "case stuck" in said and "ran past its 2 s limit" in said, said
    assert "started a child that will not exit" in said, said


def test_a_case_inside_its_limit_comes_back_as_it_always_did(tmp_path):
    def run_case(name, base):
        base.mkdir(parents=True)
        proc = walk.run(base, "-c", f"print({name!r})")
        return {"out": proc.stdout.strip(), "returncode": proc.returncode}

    got = walk.run_all(["a", "b"], run_case, tmp_path, limit=60)
    assert dict(got) == {"a": {"out": "a", "returncode": 0},
                         "b": {"out": "b", "returncode": 0}}
    assert got["a"]["out"] == "a"


def test_the_case_limit_is_the_walks_jobs_alone():
    """The limit is the walks job's, so it reads that job's shard, as
    `shard` does. `suite-green` runs the change walk's cases through the
    same helper with no shard named, for 23 to 67 minutes on a 90-minute
    clock (run 40491497458), and must not be cut off at 15."""
    shard = {walk.SHARD_ENV: "5", walk.SHARDS_ENV: "8"}
    assert walk.case_limit(shard) == walk.CASE_LIMIT_SECONDS
    assert walk.case_limit({}) is None


def test_a_shard_named_puts_its_cases_under_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(walk, "CASE_LIMIT_SECONDS", 2)
    monkeypatch.setenv(walk.SHARD_ENV, "1")
    monkeypatch.setenv(walk.SHARDS_ENV, "1")

    def run_case(name, base):
        base.mkdir(parents=True)
        return {"proc": walk.run(base, "-c", f"import time; time.sleep({FOREVER})")}

    start = time.monotonic()
    got = walk.run_all(["stuck"], run_case, tmp_path)
    assert time.monotonic() - start < FOREVER / 2
    with pytest.raises(walk.CaseOverLimit, match="shard 1 of 1: case stuck ran past its 2 s limit"):
        got["stuck"]


def test_no_shard_named_leaves_a_long_case_to_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(walk, "CASE_LIMIT_SECONDS", 1)
    monkeypatch.delenv(walk.SHARD_ENV, raising=False)
    monkeypatch.delenv(walk.SHARDS_ENV, raising=False)

    def run_case(name, base):
        base.mkdir(parents=True)
        proc = walk.run(base, "-c", "import time; time.sleep(3); print('done')")
        return {"out": proc.stdout.strip()}

    assert walk.run_all(["long"], run_case, tmp_path)["long"] == {"out": "done"}


def test_a_case_still_queued_at_the_limit_fails_by_name_too(tmp_path, monkeypatch):
    """One worker, two hung cases: the second never gets a turn before the
    shared deadline, and still fails by name rather than running on past
    it."""
    monkeypatch.setattr(walk.os, "cpu_count", lambda: 1)

    def run_case(name, base):
        base.mkdir(parents=True)
        return {"proc": walk.run(base, "-c", f"import time; time.sleep({FOREVER})")}

    start = time.monotonic()
    got = walk.run_all(["first", "second"], run_case, tmp_path, limit=2)
    assert time.monotonic() - start < FOREVER / 2
    for name in ("first", "second"):
        with pytest.raises(walk.CaseOverLimit, match=f"case {name} ran past"):
            got[name]


def test_the_hung_inner_test_is_named_in_the_failure(tmp_path):
    """The inner run is `-v` and unbuffered, so the last line it printed
    before the kill is the inner test that hung."""
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_slow.py").write_text(
        "import time\n"
        "def test_quick():\n    pass\n"
        f"def test_hangs():\n    time.sleep({FOREVER})\n")

    def run_case(name, base):
        return walk.run_suite(tree, ["tests/test_slow.py"])

    got = walk.run_all(["inner"], run_case, tmp_path / "base", limit=8)
    with pytest.raises(walk.CaseOverLimit) as over:
        got["inner"]
    said = str(over.value)
    assert "tests/test_slow.py::test_quick PASSED" in said, said
    assert said.rstrip().endswith("tests/test_slow.py::test_hangs"), said


def test_a_walk_hung_outside_any_subprocess_is_stopped_by_run_walks(tmp_path, capfd, monkeypatch):
    """The backstop: a walk whose own process hangs is stopped at the walk
    limit, red, with the walk, the shard and the cases it selected named."""
    rel = "tests/test_sleeps_keeps_the_suite_green.py"
    root = tmp_path / "repo"
    _plant(root, rel, textwrap.dedent(f"""\
        import sys, time
        import pytest
        sys.path.insert(0, {str(ROOT / 'tests')!r})
        import console_change_walk as walk
        CASES = {{"a": 1, "b": 1, "c": 1}}
        SHARD = walk.shard(walk.case_names(CASES))
        @pytest.mark.parametrize("name", SHARD)
        def test_the_case(name):
            time.sleep({FOREVER})
        """))
    monkeypatch.setenv(walk.SHARD_ENV, "2")
    monkeypatch.setenv(walk.SHARDS_ENV, "3")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    start = time.monotonic()
    code = parts.run_walks(root, rel, limit=3)
    took = time.monotonic() - start
    out = capfd.readouterr().out
    assert code == 1, out
    assert took < FOREVER / 2, f"run-walks waited {took:.0f} s"
    said = [ln for ln in out.splitlines() if "ran past its 3 s limit" in ln]
    assert len(said) == 1, out
    assert f"walk {rel}" in said[0] and "shard 2 of 3" in said[0], said
    assert said[0].endswith("the case(s) this shard selected: b"), said
    assert f"walk {rel} ... failed" in out, out
