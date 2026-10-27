"""The one copy of a console-change walk's machinery (DRE-6909).

A WALK is a `tests/*_keeps_the_suite_green.py` file: it copies the tree, does
what `model-config-change.yml` does with a Save changes from the console, and
runs the model tests inside the copy, once per case — DRE-6858's
`tests/test_console_project_model_keeps_the_suite_green.py` and DRE-6889's
`tests/test_console_agent_move_keeps_the_suite_green.py` were the first two.

Each of them used to carry its own copy of the tree copy, the apply, the
regenerate and the inner run, and the agent-move walk's inner run collected
`tests/test_console_*.py` — the other walk and itself — in every one of its
twenty copies. On 2026-10-11 that put `scripts unit tests (part 1)` past its
30-minute limit with no test failed (run 38142539090). So they live here,
once, and the inner file list refuses a walk: `inner_files` leaves every walk
out of a pattern and raises on one named outright, and `run_suite` raises on
one handed to it however it got there. A walk never runs a walk.

The walks themselves run in a job of their own, `tests.yml`'s `walks`
(`scripts/unit_test_parts.py run-walks`), never in a numbered part.

A WALK RUNS IN SHARDS (DRE-7907). On 2026-10-24 the every-default walk took
60m07s in one job (job 120989216560), and `unit-result` waited on it. So
each walk's cases are spread across several jobs: `walks --json` gives each
walk a shard count, and `shard` picks the cases one shard runs by
`BUREAU_WALK_SHARD` of `BUREAU_WALK_SHARDS` — index modulo the count equal to
the shard minus one, the change walk's `selected` rule. It is the one reader
of those two names: a walk asks it for its shard and never reads them itself,
and `case_names` reads a walk's case names off either shape of `CASES`. An
inner run never sees them, so a copy never runs a shard of anything.

A HUNG CASE FAILS BY NAME (DRE-8152). On 2026-10-27 the project-model walk's
shard 5 of 8 printed nothing after its case-free tests until the job's
20-minute clock canceled it (job 122184991649, run 40502923604): an inner
suite left children that never exited, `run` waited on them with no limit,
and the cancel threw away the output that would have named the test. Now
`run_all` gives a shard's cases one deadline, `CASE_LIMIT_SECONDS` from when
they start, and `run` starts every subprocess in a process group of its own.
The limit is the walks job's (`case_limit`): with no shard named, as in
`suite-green`'s hour of the change walk's cases, there is none.
At the deadline the group is killed — the children it left too, which would
otherwise hold its pipes open — and the case's lookup in the results raises
`CaseOverLimit`, naming the walk, the shard and the case, with the end of
its output. The inner run is `-v` and unbuffered, so that output ends on the
inner test that hung. `tests/test_walks_time_budget.py` holds the limit
under the walks job's clock, and `run-walks` keeps a walk limit of its own
as the backstop for a hang outside any subprocess.

Run: cd bureau-pipeline && python3 -m pytest tests/test_tests_workflow_parts.py -q
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

#: How long a shard's cases have, in seconds, from when they start (DRE-8152):
#: twice `scripts/unit_test_parts.py`'s 450-second shard target, the 2x
#: run-to-run spread `tests.yml` documents, so an honest slow shard is never
#: cut off. `tests/test_walks_time_budget.py` holds it, and the walk limit
#: above it, under the walks job's `timeout-minutes`.
CASE_LIMIT_SECONDS = 900

#: How long past the deadline `run_all` waits for a case whose thread is not
#: in a subprocess it can stop, before it reports that case and stops waiting.
CASE_GRACE_SECONDS = 30

#: How much of a stopped case's output its failure carries.
OVER_LIMIT_TAIL = 3000

#: A walk's file name — the same pattern `scripts/unit_test_parts.py` finds
#: the walks' job's files by.
WALK = "*_keeps_the_suite_green.py"

#: The shard a walk's job runs, and how many there are (DRE-7907): both or
#: neither. `tests.yml`'s `walks` step sets them and nothing else does.
SHARD_ENV = "BUREAU_WALK_SHARD"
SHARDS_ENV = "BUREAU_WALK_SHARDS"

#: Not copied: the repository's history, and the pipeline checkout a workflow
#: run puts beside the tree (it is not this repository's code).
SKIPPED = {".git", ".bureau-pipeline", ".pytest_cache"}

#: A failed test, or a failed subtest, on pytest's `-rf` summary.
FAILED = re.compile(r"^(?:FAILED|SUBFAILED(?:\(.*?\)|\[.*?\])?) (\S+::\S+)", re.M)


def is_walk(rel: str) -> bool:
    return fnmatch.fnmatch(Path(rel).name, WALK)


def case_names(cases) -> list[str]:
    """A walk's case names, in order: the keys of a dict `CASES`, or the
    first of each `(name, change set)` pair of the change walk's list."""
    if isinstance(cases, dict):
        return list(cases)
    return [name for name, _ in cases]


def shard(names, env=None) -> list[str]:
    """The names shard n of m runs: index modulo m equal to n - 1. Neither
    variable set, every name; one without the other, or a shard outside
    1..m, is refused."""
    env = os.environ if env is None else env
    n, m = env.get(SHARD_ENV), env.get(SHARDS_ENV)
    if n is None and m is None:
        return list(names)
    if n is None or m is None:
        raise ValueError(f"{SHARD_ENV} and {SHARDS_ENV} are set together or "
                         f"not at all")
    n, m = int(n), int(m)
    if not 1 <= n <= m:
        raise ValueError(f"{SHARD_ENV}={n} is not a shard of {SHARDS_ENV}={m}")
    return [name for i, name in enumerate(names) if i % m == n - 1]


def copy_tree(base: Path, root: Path = ROOT) -> Path:
    """`root` copied into `base`, less what `SKIPPED` names and bytecode."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    base.mkdir(parents=True)
    for entry in root.iterdir():
        if entry.name in SKIPPED:
            continue
        if entry.is_dir():
            shutil.copytree(entry, base / entry.name, ignore=ignore)
        else:
            shutil.copy2(entry, base / entry.name)
    return base


class CaseOverLimit(Exception):
    """A case still running at its shard's deadline, stopped (DRE-8152)."""


#: The deadline of the case this thread runs, set by `run_all`.
_case = threading.local()


def _stop(proc: subprocess.Popen) -> tuple[str, str]:
    """Kill `proc`'s process group and read what it printed."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        return proc.communicate(timeout=CASE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # A child that left the group still holds the pipes: give them up.
        proc.kill()
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()
        proc.wait()
        return "", ""


def run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    """`python3 <args>` inside the copy, with no shard named, in a process
    group of its own — stopped, with `CaseOverLimit`, at this case's
    deadline."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    for name in ("GITHUB_STEP_SUMMARY", SHARD_ENV, SHARDS_ENV):
        env.pop(name, None)
    deadline = getattr(_case, "deadline", None)
    proc = subprocess.Popen([sys.executable, *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=tree,
                            env=env, start_new_session=True)
    try:
        out, err = proc.communicate(
            timeout=None if deadline is None
            else max(deadline - time.monotonic(), 0))
    except subprocess.TimeoutExpired:
        out, err = _stop(proc)
        said = (err + out)[-OVER_LIMIT_TAIL:]
        raise CaseOverLimit(
            f"`python3 {' '.join(args)}` was still running and was stopped; "
            f"the end of its output:\n{said}") from None
    except BaseException:
        _stop(proc)
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def regenerate(tree: Path) -> None:
    """Regenerate the copy's mirrors of `config/models.yaml`."""
    proc = run(tree, "scripts/sync_model_config.py")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def save(tree: Path, changes: list[dict], tmp: Path,
         requested_by: str = "ceo@example.com") -> None:
    """What `model-config-change.yml` does with one Save changes."""
    change_set = tmp / "change-set.json"
    change_set.write_text(json.dumps(
        {"requested_by": requested_by, "changes": changes}))
    for args in (
        ("scripts/model_config_change.py", "apply", "--changes", str(change_set)),
        ("scripts/sync_model_config.py",),
        ("scripts/sync_model_config.py", "--check"),
    ):
        proc = run(tree, *args)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)


def apply_saves(tree: Path, saves: list[list[dict]], base: Path) -> None:
    """Every save of a case, in order, each with a work directory of its own."""
    for i, changes in enumerate(saves):
        work = base / f"save-{i}"
        work.mkdir()
        save(tree, changes, work)


def _refuse_walks(files) -> None:
    walks = [rel for rel in files if is_walk(rel)]
    if walks:
        raise ValueError(
            f"a walk never runs a walk, and {walks} would — the walks run in "
            f"their own job (scripts/unit_test_parts.py run-walks)")


def inner_files(tree: Path, named, patterns) -> list[str]:
    """The named files, then every other `tests/<pattern>` file, in pattern
    order — leaving out every walk. A walk named outright is refused."""
    _refuse_walks(named)
    files = list(named)
    for pattern in patterns:
        for path in sorted((tree / "tests").glob(pattern)):
            rel = path.relative_to(tree).as_posix()
            if rel not in files and not is_walk(rel):
                files.append(rel)
    return files


def run_suite(tree: Path, files: list[str]) -> dict:
    """pytest on `files` inside the copy: its exit code, the node ids of every
    failed test and subtest, and the end of its output. `-v`, so a run
    stopped at its deadline ends on the test it was in."""
    _refuse_walks(files)
    proc = run(tree, "-m", "pytest", *files, "-v", "-rf",
               "-p", "no:cacheprovider")
    return {
        "returncode": proc.returncode,
        "failed": list(dict.fromkeys(FAILED.findall(proc.stdout))),
        "tail": proc.stdout[-4000:] + proc.stderr[-2000:],
    }


class Results(dict):
    """Each case's result by name. A case that ran past the deadline is not
    in it: looking it up raises `CaseOverLimit`, so that case's tests fail
    by its name and every other case's stand on their own."""

    def __init__(self, done: dict, over: dict[str, str]):
        super().__init__(done)
        self.over = over

    def __getitem__(self, name):
        if name in self.over:
            raise CaseOverLimit(self.over[name])
        return super().__getitem__(name)


def _where(run_case: Callable) -> str:
    """`walk <file>, shard n of m` for the walk `run_case` belongs to."""
    module = sys.modules.get(getattr(run_case, "__module__", ""), None)
    path = Path(getattr(module, "__file__", None) or "<unknown walk>")
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = "/".join(path.parts[-2:])
    n, m = os.environ.get(SHARD_ENV), os.environ.get(SHARDS_ENV)
    return f"walk {rel}, " + (f"shard {n} of {m}" if n and m
                              else "every case (no shard named)")


def case_limit(env=None) -> float | None:
    """The walks job's case limit: `CASE_LIMIT_SECONDS` when its shard is
    named, and None — no limit — when it is not. `suite-green` runs the
    change walk's cases through this helper with no shard named, for up to
    an hour on its own 90-minute clock."""
    env = os.environ if env is None else env
    named = env.get(SHARD_ENV) is not None and env.get(SHARDS_ENV) is not None
    return CASE_LIMIT_SECONDS if named else None


def run_all(names, run_case: Callable[[str, Path], dict], base: Path,
            limit: float | None = None) -> Results:
    """`run_case(name, base / name)` for every case, side by side, as many at
    once as the machine has cores — every case done within `limit` seconds
    of the first starting (`case_limit()` unless a test names one). A case
    past it is stopped and named in the results (`Results`)."""
    limit = case_limit() if limit is None else limit
    deadline = None if limit is None else time.monotonic() + limit
    where = _where(run_case)

    def over(name: str, why: str) -> str:
        return f"{where}: case {name} ran past its {limit:g} s limit — {why}"

    def one(name: str):
        _case.deadline = deadline
        try:
            return run_case(name, base / name)
        except CaseOverLimit as exc:
            return CaseOverLimit(over(name, str(exc)))
        finally:
            _case.deadline = None

    pool = ThreadPoolExecutor(max_workers=os.cpu_count() or 2)
    futures = {name: pool.submit(one, name) for name in names}
    wait(futures.values(),
         timeout=None if limit is None else limit + CASE_GRACE_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    done, late = {}, {}
    for name, future in futures.items():
        if not future.done():
            late[name] = over(name, "it was still running outside any "
                                    "subprocess the walk could stop")
        elif future.cancelled():
            late[name] = over(name, "it never started")
        elif isinstance(future.result(), CaseOverLimit):
            late[name] = str(future.result())
        else:
            done[name] = future.result()
    return Results(done, late)
