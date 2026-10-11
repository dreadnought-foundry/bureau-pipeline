"""DRE-6899 — changing a group's default model from the console merges with a
green suite.

Settings › Agents & models has a Default model dropdown on each group. Picking
another model there sends `set_default_model`: the pipeline moves that model to
the front of the group's ladder, regenerates the mirrors and opens a pull
request. Measured on `main` after #1004, every such pull request was red — 40
tests for Builders on claude-sonnet-5-5, 11 for Reviewers on claude-sonnet-5,
26 for Planners on claude-sonnet-4-6 — because each read the committed
`config/models.yaml` (or a file generated from it) and expected today's first
model of a group. A save from the console must merge on the suite as it
stands, so this file does what `model-config-change.yml` does, in a COPY of the
tree, and then runs every test that reads the model files inside the copy —
the third slice of DRE-6853, beside DRE-6858's project harness and DRE-6889's
agent-move harness.

THE TREE
--------
Each case copies the repository (everything but `.git` and the pipeline's own
`.bureau-pipeline` checkout) into a temp directory and saves its change set on
the committed file as it stands: a group's default is one model per group, so
there is no earlier state to reset to, and whichever default the CEO last
saved is the one the next save moves away from.

THE CASES
---------
Read from the committed files, never written here, so a group or a priced
model added later is walked the day it lands:

  * for each group in `kinds`, each model with a declared price that
    `model_config_change.py apply --check` accepts as that group's default —
    one save each (the group's current default is refused as a no-op, and a
    model the policy refuses, Fable on a build ladder for one, never opens a
    pull request);
  * one save that changes every group's default at once — the first
    combination of accepted models, group by group, that `apply` accepts as
    one change set.

A group can have no model to move to: with Sonnet 4.6 saved as the Builders'
default, every other rung of the Planners' ladder is a build model, which the
policy refuses at its top. Such a group is not dropped quietly — the walk
covers every group `apply --check` accepts a model for, and the test that
asks the CLI about every group and every priced model proves the rest accept
none.

THE SUITE RUN INSIDE THE COPY
-----------------------------
The ten files the card names, then every other `tests/test_model_*.py`,
`tests/test_session_*.py` and `tests/test_console_*.py`. Three of those — this
file, DRE-6858's and DRE-6889's — are copy-and-run harnesses: inside the copy
their cheap tests run, and the two tests each that build the copies are
deselected, since nesting them would run the whole walk again per case.

The cases run side by side, as many at once as the machine has cores.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_default_model_keeps_the_suite_green.py -q
"""

from __future__ import annotations

import itertools
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
sys.path.insert(0, str(ROOT / "scripts"))

import model_config_change as mcc  # noqa: E402
from model_fallback import declared_prices  # noqa: E402

MODELS = ROOT / "config" / "models.yaml"
SELF = "tests/test_console_default_model_keeps_the_suite_green.py"
PROJECT_HARNESS = "tests/test_console_project_model_keeps_the_suite_green.py"
AGENT_HARNESS = "tests/test_console_agent_move_keeps_the_suite_green.py"

#: The files the card names: the ones a console default-model change turned red.
NAMED = (
    "tests/test_model_policy.py",
    "tests/test_model_fallback.py",
    "tests/test_model_config.py",
    "tests/test_model_config_change.py",
    "tests/test_model_adoption_actions.py",
    "tests/test_model_effort.py",
    "tests/test_project_model_exceptions.py",
    "tests/test_model_choices.py",
    "tests/test_model_catalog.py",
    "tests/test_model_config_change_workflow.py",
)

#: The tests that copy the tree and run the suite again — deselected inside
#: a copy, so no case runs the whole walk a second time.
NESTED = (
    f"{PROJECT_HARNESS}::test_the_saved_change_is_in_the_tree",
    f"{PROJECT_HARNESS}::test_every_model_test_passes_after_the_save",
    f"{AGENT_HARNESS}::test_the_saved_change_is_in_the_tree",
    f"{AGENT_HARNESS}::test_every_test_passes_after_the_save",
    f"{SELF}::test_the_saved_change_is_in_the_tree",
    f"{SELF}::test_every_test_passes_after_the_save",
)

#: Not copied: the repository's history, and the pipeline checkout a workflow
#: run puts beside the tree (it is not this repository's code).
SKIPPED = {".git", ".bureau-pipeline", ".pytest_cache"}

TODAY = "2026-10-11"
REQUESTER = "ceo@example.com"


def _default(group: str, model: str) -> dict:
    return {"op": "set_default_model", "group": group, "model": model}


def _accepts(text: str, changes: list[dict]) -> bool:
    _, refusals = mcc.apply_change_set(
        text, {"requested_by": REQUESTER, "changes": changes}, today=TODAY)
    return refusals == []


def accepted_defaults(text: str) -> dict[str, list[str]]:
    """Group → every priced model `apply` accepts as its default, in the order
    config/model-prices.yaml declares them."""
    raw = yaml.safe_load(text)
    priced = list(declared_prices())
    return {group: [model for model in priced
                    if _accepts(text, [_default(group, model)])]
            for group in raw["kinds"]}


def cases(text: str) -> dict[str, list[dict]]:
    """Case name → the one change set it saves."""
    accepted = accepted_defaults(text)
    out = {f"{group}-default-{model}": [_default(group, model)]
           for group, models in accepted.items() for model in models}
    offered = {group: models for group, models in accepted.items() if models}
    for combo in itertools.product(*offered.values()) if offered else ():
        changes = [_default(group, model) for group, model in zip(offered, combo)]
        if _accepts(text, changes):
            out["every-group-at-once"] = changes
            break
    return out


CASES = cases(MODELS.read_text())


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


def _run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run([sys.executable, *args], capture_output=True,
                          text=True, cwd=tree, env=env)


def _change_set(changes: list[dict], tmp: Path) -> Path:
    path = tmp / "change-set.json"
    path.write_text(json.dumps({"requested_by": REQUESTER, "changes": changes}))
    return path


def save(tree: Path, changes: list[dict], tmp: Path) -> None:
    """What `model-config-change.yml` does with one Save changes."""
    change_set = _change_set(changes, tmp)
    for args in (
        ("scripts/model_config_change.py", "apply", "--changes", str(change_set)),
        ("scripts/sync_model_config.py",),
        ("scripts/sync_model_config.py", "--check"),
    ):
        proc = _run(tree, *args)
        assert proc.returncode == 0, (args, proc.stdout, proc.stderr)


def suite_files(tree: Path) -> list[str]:
    """The named files plus every other `tests/test_model_*.py`,
    `tests/test_session_*.py` and `tests/test_console_*.py`."""
    files = list(NAMED)
    for pattern in ("test_model_*.py", "test_session_*.py", "test_console_*.py"):
        for path in sorted((tree / "tests").glob(pattern)):
            rel = path.relative_to(tree).as_posix()
            if rel not in files:
                files.append(rel)
    return files


def run_case(name: str, base: Path) -> dict:
    tree = copy_tree(base / "tree")
    before = yaml.safe_load((tree / "config" / "models.yaml").read_text())
    work = base / "save"
    work.mkdir()
    save(tree, CASES[name], work)
    deselect = [arg for nodeid in NESTED for arg in ("--deselect", nodeid)]
    proc = _run(tree, "-m", "pytest", *suite_files(tree), *deselect, "-q", "-rf",
                "-p", "no:cacheprovider")
    return {
        "before": before,
        "models": yaml.safe_load((tree / "config" / "models.yaml").read_text()),
        "choices": json.loads((tree / "config" / "model-choices.json").read_text()),
        "returncode": proc.returncode,
        # A subtest's failure prints as `SUBFAILED(<params>) <nodeid>`.
        "failed": re.findall(r"^(?:SUB)?FAILED(?:\(.*?\))? (tests/\S+)", proc.stdout,
                             flags=re.M),
        "tail": proc.stdout[-4000:] + proc.stderr[-2000:],
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("dre6899")
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        futures = {name: pool.submit(run_case, name, base / name)
                   for name in CASES}
        return {name: future.result() for name, future in futures.items()}


def test_the_suite_covers_the_named_files_and_every_model_session_and_console_test():
    files = suite_files(ROOT)
    assert files[:len(NAMED)] == list(NAMED)
    for rel in NAMED:
        assert (ROOT / rel).is_file(), rel
    assert SELF in files
    for pattern in ("test_model_*.py", "test_session_*.py", "test_console_*.py"):
        for path in (ROOT / "tests").glob(pattern):
            assert path.relative_to(ROOT).as_posix() in files, path


def test_only_the_tests_that_copy_the_tree_are_deselected():
    """A deselected test is one no case runs; only the nested walks may be."""
    for nodeid in NESTED:
        rel, name = nodeid.split("::")
        assert rel in (SELF, PROJECT_HARNESS, AGENT_HARNESS), nodeid
        assert re.search(rf"^def {name}\(results\b", (ROOT / rel).read_text(),
                         flags=re.M), nodeid


def test_the_walk_covers_every_group_the_file_holds():
    """A group the walk skips is a dropdown nobody proved — read straight off
    the file, without the case builder's help."""
    text = MODELS.read_text()
    raw = yaml.safe_load(text)
    assert len(raw["kinds"]) >= 3, raw["kinds"]
    walked = {changes[0]["group"] for changes in CASES.values() if len(changes) == 1}
    # A group left out is one the console offers no other default for — and
    # that is asked of the policy model by model, not taken from the walk.
    for group in set(raw["kinds"]) - walked:
        for model in declared_prices():
            assert not _accepts(text, [_default(group, model)]), (group, model)
    assert walked, "no group has a default the console can save"
    at_once = CASES["every-group-at-once"]
    assert [c["group"] for c in at_once] == [g for g in raw["kinds"] if g in walked]
    assert all(c["op"] == "set_default_model" for c in at_once)


def test_every_case_is_a_model_apply_check_accepts_and_no_accepted_one_is_skipped(tmp_path):
    """The CLI the workflow calls is the judge: for every group and every
    priced model, `apply --check` exits 0 exactly when the walk saves it."""
    raw = yaml.safe_load(MODELS.read_text())
    singles = {(changes[0]["group"], changes[0]["model"])
               for changes in CASES.values() if len(changes) == 1}
    pairs = [(group, model) for group in raw["kinds"] for model in declared_prices()]

    def check(pair, tmp):
        tmp.mkdir()
        proc = _run(ROOT, "scripts/model_config_change.py", "apply", "--check",
                    "--today", TODAY, "--changes",
                    str(_change_set([_default(*pair)], tmp)))
        assert proc.returncode in (0, 2), (pair, proc.stdout, proc.stderr)
        return proc.returncode == 0

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        verdicts = dict(zip(pairs, pool.map(
            check, pairs, [tmp_path / str(i) for i in range(len(pairs))])))
    assert singles == {pair for pair, ok in verdicts.items() if ok}
    for group in raw["kinds"]:
        current = raw["ladders"][raw["kinds"][group]["ladder"]][0]["model"]
        assert (group, current) not in singles, (group, current)


def test_the_cases_follow_the_default_the_file_holds():
    """The walk is the file's: once the CEO's save has merged, the model he
    picked is the no-op and the default he moved away from is a case."""
    text = MODELS.read_text()
    raw = yaml.safe_load(text)
    for group, spec in raw["kinds"].items():
        current = raw["ladders"][spec["ladder"]][0]["model"]
        name, changes = next(((n, c) for n, c in CASES.items()
                              if len(c) == 1 and c[0]["group"] == group),
                             (None, None))
        if changes is None:
            continue
        saved, refusals = mcc.apply_change_set(
            text, {"requested_by": REQUESTER, "changes": changes}, today=TODAY)
        assert refusals == []
        got = cases(saved)
        assert name not in got, (group, sorted(got))
        assert f"{group}-default-{current}" in got, (group, sorted(got))


@pytest.mark.parametrize("name", list(CASES))
def test_the_saved_change_is_in_the_tree(results, name):
    """A save that never landed would leave the suite green for the wrong
    reason."""
    got = results[name]
    before, models, choices = got["before"], got["models"], got["choices"]
    published = {group["key"]: group for group in choices["groups"]}
    for change in CASES[name]:
        group, model = change["group"], change["model"]
        ladder = models["kinds"][group]["ladder"]
        rungs = [rung["model"] for rung in models["ladders"][ladder]]
        old = [rung["model"] for rung in before["ladders"][ladder]]
        assert rungs[0] == model, rungs
        assert rungs[1:] == [m for m in old if m != model], (old, rungs)
        assert published[group]["default_model"] == model, published[group]


@pytest.mark.parametrize("name", list(CASES))
def test_every_test_passes_after_the_save(results, name):
    got = results[name]
    assert got["failed"] == [], "\n".join(got["failed"]) + "\n\n" + got["tail"]
    assert got["returncode"] == 0, got["tail"]
