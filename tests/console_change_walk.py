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

Run: cd bureau-pipeline && python3 -m pytest tests/test_tests_workflow_parts.py -q
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

#: A walk's file name — the same pattern `scripts/unit_test_parts.py` finds
#: the walks' job's files by.
WALK = "*_keeps_the_suite_green.py"

#: Not copied: the repository's history, and the pipeline checkout a workflow
#: run puts beside the tree (it is not this repository's code).
SKIPPED = {".git", ".bureau-pipeline", ".pytest_cache"}

#: A failed test, or a failed subtest, on pytest's `-rf` summary.
FAILED = re.compile(r"^(?:FAILED|SUBFAILED(?:\(.*?\)|\[.*?\])?) (\S+::\S+)", re.M)


def is_walk(rel: str) -> bool:
    return fnmatch.fnmatch(Path(rel).name, WALK)


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


def run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    """`python3 <args>` inside the copy."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=tree, env=env)


def regenerate(tree: Path) -> None:
    """Regenerate the copy's mirrors of `config/models.yaml`."""
    proc = run(tree, "scripts/sync_model_config.py")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def save(tree: Path, changes: list[dict], tmp: Path) -> None:
    """What `model-config-change.yml` does with one Save changes."""
    change_set = tmp / "change-set.json"
    change_set.write_text(json.dumps(
        {"requested_by": "ceo@example.com", "changes": changes}))
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
    failed test and subtest, and the end of its output."""
    _refuse_walks(files)
    proc = run(tree, "-m", "pytest", *files, "-q", "-rf",
               "-p", "no:cacheprovider")
    return {
        "returncode": proc.returncode,
        "failed": list(dict.fromkeys(FAILED.findall(proc.stdout))),
        "tail": proc.stdout[-4000:] + proc.stderr[-2000:],
    }


def run_all(names, run_case: Callable[[str, Path], dict], base: Path) -> dict:
    """`run_case(name, base / name)` for every case, side by side, as many at
    once as the machine has cores."""
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        futures = {name: pool.submit(run_case, name, base / name)
                   for name in names}
        return {name: future.result() for name, future in futures.items()}
