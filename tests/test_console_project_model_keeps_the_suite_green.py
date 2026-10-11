"""DRE-6858 — a project's own model saved from the console merges with a green
suite.

Settings › Agents & models gives a project a model of its own: Save changes
dispatches `model-config-change.yml`, which applies the change set to
config/models.yaml (`scripts/model_config_change.py apply`), regenerates the
mirrors (`scripts/sync_model_config.py`) and opens a pull request carrying
nothing else. The first real one, bureau-pipeline #993 ("Run
agent-bureau-demo's Builders group on claude-sonnet-5-5 first"), was red in
eighteen tests across six files: every one assumed the committed `projects` map
was empty. A pull request that carries a settings change must merge on the
suite as it stands, so this file does what that workflow does, in a COPY of the
tree, and then runs the model tests inside the copy.

THE TREE
--------
Each case copies the repository (everything but `.git` and the pipeline's own
`.bureau-pipeline` checkout) into a temp directory — the seam
`tests/test_project_model_exceptions.py` uses, widened to `tests/` so the suite
runs against the copy's config. The scripts read their config off their own
file's path, so the copy is the only way to run the real tools on a file the
repo does not ship.

The copy starts from NO exceptions: its `projects` block is reset to
`projects: {}` and the mirrors regenerated before the first save. The committed
file carries whatever the CEO last saved — #993 itself, once it merged — and a
case that re-applies a save already in the tree is refused as a no-op before
the suite ever runs.

The three cases run their suites side by side: each is the whole model suite,
and one after another they would cost three times the wall clock.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_project_model_keeps_the_suite_green.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from model_config_text import without_projects  # noqa: E402

#: The six files #993 turned red, named by the card.
NAMED = (
    "tests/test_model_config.py",
    "tests/test_model_choices.py",
    "tests/test_model_projects_schema.py",
    "tests/test_project_model_exceptions.py",
    "tests/test_model_config_change.py",
    "tests/test_model_adoption_actions.py",
)

#: Not copied: the repository's history, and the pipeline checkout a workflow
#: run puts beside the tree (it is not this repository's code).
SKIPPED = {".git", ".bureau-pipeline", ".pytest_cache"}


def _set(project: str, group: str, model: str) -> dict:
    return {"op": "set_project_model", "project": project, "group": group,
            "model": model}


def _clear(project: str, group: str) -> dict:
    return {"op": "clear_project_model", "project": project, "group": group}


#: Each case is a list of SAVES, applied in order, and the `projects` map the
#: committed file must hold once they are.
CASES = {
    # The exact change set of bureau-pipeline #993.
    "pr-993": (
        [[_set("agent-bureau-demo", "workhorse", "claude-sonnet-5-5")]],
        {"agent-bureau-demo": {"workhorse": "claude-sonnet-5-5"}},
    ),
    # One project's builders and another project's reviewers.
    "two-projects-two-groups": (
        [[_set("agent-bureau-demo", "workhorse", "claude-sonnet-5-5"),
          _set("portico", "advisory", "claude-sonnet-5")]],
        {"agent-bureau-demo": {"workhorse": "claude-sonnet-5-5"},
         "portico": {"advisory": "claude-sonnet-5"}},
    ),
    # Set in one save, cleared in the next.
    "set-then-cleared": (
        [[_set("agent-bureau-demo", "workhorse", "claude-sonnet-5-5")],
         [_clear("agent-bureau-demo", "workhorse")]],
        {},
    ),
}


def copy_tree(base: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    base.mkdir(parents=True)
    for entry in ROOT.iterdir():
        if entry.name in SKIPPED:
            continue
        if entry.is_dir():
            shutil.copytree(entry, base / entry.name, ignore=ignore)
        else:
            shutil.copy2(entry, base / entry.name)
    return base


def reset_projects(tree: Path) -> None:
    """Empty the copy's `projects` block and regenerate its mirrors."""
    config = tree / "config" / "models.yaml"
    config.write_text(without_projects(config.read_text()))
    proc = _run(tree, "scripts/sync_model_config.py")
    assert proc.returncode == 0, (proc.stdout, proc.stderr)


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=tree, env=env)


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
        proc = _run(tree, *args)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)


def suite_files(tree: Path) -> list[str]:
    """The six named files plus every other `tests/test_model_*.py`."""
    files = list(NAMED)
    for path in sorted((tree / "tests").glob("test_model_*.py")):
        rel = path.relative_to(tree).as_posix()
        if rel not in files:
            files.append(rel)
    return files


def run_case(name: str, base: Path) -> dict:
    saves, _ = CASES[name]
    tree = copy_tree(base / "tree")
    reset_projects(tree)
    for i, changes in enumerate(saves):
        work = base / f"save-{i}"
        work.mkdir()
        save(tree, changes, work)
    proc = _run(tree, "-m", "pytest", *suite_files(tree), "-q", "-rf",
                "-p", "no:cacheprovider")
    return {
        "projects": yaml.safe_load(
            (tree / "config" / "models.yaml").read_text())["projects"],
        "choices": json.loads(
            (tree / "config" / "model-choices.json").read_text())["projects"],
        "returncode": proc.returncode,
        "failed": re.findall(r"^FAILED (\S+)", proc.stdout, flags=re.M),
        "tail": proc.stdout[-4000:] + proc.stderr[-2000:],
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("dre6858")
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        futures = {name: pool.submit(run_case, name, base / name)
                   for name in CASES}
        return {name: future.result() for name, future in futures.items()}


def test_the_suite_covers_the_six_files_and_every_model_test():
    files = suite_files(ROOT)
    assert files[:len(NAMED)] == list(NAMED)
    for rel in NAMED:
        assert (ROOT / rel).is_file(), rel
    assert len(files) > len(NAMED)
    assert all(re.fullmatch(r"tests/test_(model_\w+|project_model_exceptions)\.py", f)
               for f in files), files


@pytest.mark.parametrize("name", list(CASES))
def test_the_saved_change_is_in_the_tree(results, name):
    """A save that never landed would leave the suite green for the wrong
    reason."""
    want = CASES[name][1]
    got = results[name]
    assert got["projects"] == want
    assert got["choices"] == [{"slug": slug, "exceptions": exceptions}
                              for slug, exceptions in want.items()]


@pytest.mark.parametrize("name", list(CASES))
def test_every_model_test_passes_after_the_save(results, name):
    got = results[name]
    assert got["failed"] == [], "\n".join(got["failed"]) + "\n\n" + got["tail"]
    assert got["returncode"] == 0, got["tail"]
