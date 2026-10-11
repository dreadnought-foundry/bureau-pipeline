"""DRE-6865 — every change the console can save keeps every test that can read
the model files green.

Settings › Agents & models saves a CHANGE SET: `model-config-change.yml` runs
`scripts/model_config_change.py apply` on it, regenerates the mirrors with
`scripts/sync_model_config.py` and opens a pull request carrying nothing else.
Until the epic this closes, a legitimate save could turn that pull request red:
tests read the committed `config/models.yaml` and pinned today's choices. A
Planners default change turned five tests in `tests/test_plan_model_death_swing.py`
red, and nothing ran that file — it imports the selector and names no model
file. The two walks before this one (DRE-6858, DRE-6889) each proved one slice
of the page; this one proves every operation, against every test that can read
the model files.

THE TREE
--------
The tree the cases are read from is the tree they are applied to: the repo
root, or the tree `BUREAU_SUITE_GREEN_TREE` names. Each case copies it
(everything but `.git` and the caches — `tests/console_change_walk.py`'s copy,
shared by every walk), saves one change set into the copy the way the workflow
does (`apply`, then `sync_model_config.py` and `--check`), and runs inside the
copy every file `model_fixture.model_reading_tests(copy)` names — less the
walks, which a walk never runs (DRE-6909). No list of files lives here.

THE CASES
---------
DERIVED from the tree, never typed, so this file pins no choice and never
names one the tree does not hold (`cases`):

  * a project's own model — the first slug of `config/repo-map.json` given the
    Builders group's second rung — set, and set then cleared in one change set;
  * each group's default changed to the first `allowed` choice of
    `config/model-choices.json` that is not its `default_model`;
  * for each (from-group, to-group) pair with an `allowed` move in
    `agents[].moves`, the first such agent moved (an agent in no group moves
    from `ungrouped`);
  * an agent added under a name the file does not hold;
  * a group added under a name the file does not hold, on the Builders
    group's default model (DRE-6834).

Builders is the group the default ladder backs, and the added agent's name is
the first free one — both read off the tree's `config/models.yaml`.

`REFUSED_BY_POLICY` names each operation of `model_config_change.OPS` the
policy refuses today, with the test that proves it. The coverage test fails
when an operation has neither a case nor an entry there, or an allowed pair has
no case — so a new operation, or a move DRE-6781 newly allows, fails here until
the same pull request walks it.

TWO SPEEDS
----------
Each case is about ten minutes of pytest on a runner, so the file runs at two
speeds, chosen by the environment:

  * `BUREAU_SUITE_GREEN_RUN` unset — how `scripts/unit_test_parts.py
    run-walks` runs it — the coverage, derivation and case-listing tests run
    and every per-case test skips with the reason `SKIP_REASON`;
  * `BUREAU_SUITE_GREEN_RUN=1` — every case runs, each in its own copy, side by
    side through the helper's thread pool. `BUREAU_SUITE_GREEN_PART=<n>` with
    `BUREAU_SUITE_GREEN_OF=<m>` (both or neither) keeps the cases whose index
    modulo m is n - 1, so a job can split them across runners (DRE-6873).

The inner pytest runs with none of these four set, so this file's heavy cases
could never nest even if a walk were collected. Each case prints
`suite-green: <case> ran <n> files in <seconds>s`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_change_keeps_the_suite_green.py -v -rs
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import console_change_walk as walk  # noqa: E402
import model_fixture  # noqa: E402
from model_config_text import fresh_agent_name  # noqa: E402

#: The whole of the contract with DRE-6873's job.
TREE_ENV = "BUREAU_SUITE_GREEN_TREE"
RUN_ENV = "BUREAU_SUITE_GREEN_RUN"
PART_ENV = "BUREAU_SUITE_GREEN_PART"
OF_ENV = "BUREAU_SUITE_GREEN_OF"
SWITCHES = (TREE_ENV, RUN_ENV, PART_ENV, OF_ENV)

SKIP_REASON = 'runs in the "console change keeps the suite green" job'
REQUESTED_BY = "suite-green harness"
ABOUT = "Answers questions from our data"

#: Each operation the policy refuses today, and the test that proves it. Empty
#: since DRE-6834: `add_group` was the one, and the policy takes a group now.
REFUSED_BY_POLICY: dict[str, str] = {}

#: What the added group's case calls it, and the name it asks for.
GROUP_LABEL = "Product & design"
GROUP_PURPOSE = "Decide what to build and how it looks."
GROUP_NAME = "product-design"

_TEST_ID = re.compile(r"(tests/\S+\.py)::(\w+)")


# --------------------------------------------------------------------------- #
# Reading the tree                                                             #
# --------------------------------------------------------------------------- #

def tree_root() -> Path:
    """The tree `BUREAU_SUITE_GREEN_TREE` names, or the repo root."""
    named = os.environ.get(TREE_ENV)
    return Path(named).resolve() if named else ROOT


def ops(tree: Path) -> dict:
    """`OPS` of the tree's `scripts/model_config_change.py`, read without
    running the module."""
    source = (tree / "scripts" / "model_config_change.py").read_text()
    for node in ast.parse(source).body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "OPS" for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{tree}/scripts/model_config_change.py declares no OPS")


def _choices(tree: Path) -> dict:
    return json.loads((tree / "config" / "model-choices.json").read_text())


def _models(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text()) or {}


def builders(tree: Path) -> str:
    """The group the default ladder backs — Builders."""
    raw = _models(tree)
    return next(kind for kind, spec in raw["kinds"].items()
                if spec["ladder"] == raw["default_ladder"])


def allowed_pairs(choices: dict) -> dict:
    """(from-group, to-group) → the first agent the choices file lets make that
    move, in the file's order. An agent in no group moves from None."""
    pairs: dict = {}
    for agent in choices["agents"]:
        for move in agent.get("moves") or []:
            if move.get("allowed"):
                pairs.setdefault((agent.get("group"), move["group"]), agent["name"])
    return pairs


def _change_set(*changes: dict) -> dict:
    return {"requested_by": REQUESTED_BY, "changes": list(changes)}


def fresh_group_name(raw: dict, wanted: str = GROUP_NAME) -> str:
    """`wanted`, or `wanted-2`, `wanted-3`, … — the first name no group or
    ladder of `raw` holds."""
    taken = set(raw.get("kinds") or {}) | set(raw.get("ladders") or {})
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}-{n}"
    return name


def cases(tree: Path) -> list[tuple[str, dict]]:
    """Every case, name and change set, in a fixed order — read off the tree's
    `config/model-choices.json`, `config/repo-map.json`, `config/models.yaml`
    and `scripts/model_config_change.py`."""
    tree = Path(tree)
    choices = _choices(tree)
    groups = {group["key"]: group for group in choices["groups"]}
    out: list[tuple[str, dict]] = []

    home = builders(tree)
    rung = groups[home]["fallbacks"][0]
    taken = {p["slug"]: p.get("exceptions") or {} for p in choices.get("projects") or []}
    slugs = json.loads((tree / "config" / "repo-map.json").read_text())
    slug = next(s for s in slugs if taken.get(s, {}).get(home) != rung)
    given = {"op": "set_project_model", "project": slug, "group": home, "model": rung}
    out.append(("project-model-set", _change_set(given)))
    out.append(("project-model-set-then-cleared", _change_set(
        given, {"op": "clear_project_model", "project": slug, "group": home})))

    for key, group in groups.items():
        other = next((c["model"] for c in group["choices"]
                      if c["allowed"] and c["model"] != group["default_model"]), None)
        if other is not None:
            out.append((f"default-{key}", _change_set(
                {"op": "set_default_model", "group": key, "model": other})))

    for (frm, to), agent in allowed_pairs(choices).items():
        out.append((f"move-{frm or 'ungrouped'}-to-{to}", _change_set(
            {"op": "move_agent", "agent": agent, "group": to})))

    out.append(("agent-added", _change_set(
        {"op": "add_agent", "agent": fresh_agent_name(_models(tree)), "about": ABOUT})))
    out.append(("group-added", _change_set(
        {"op": "add_group", "group": fresh_group_name(_models(tree)),
         "label": GROUP_LABEL, "purpose": GROUP_PURPOSE,
         "model": groups[home]["default_model"]})))
    return out


def uncovered(tree: Path, case_list) -> list[str]:
    """Every operation of the tree's `OPS` with neither a case nor a
    `REFUSED_BY_POLICY` entry, and every allowed (from-group, to-group) pair of
    its choices file with no case."""
    choices = _choices(tree)
    home_of = {agent["name"]: agent.get("group") for agent in choices["agents"]}
    changes = [c for _, change_set in case_list for c in change_set["changes"]]
    exercised = {c["op"] for c in changes}
    moved = {(home_of.get(c["agent"]), c["group"]) for c in changes if c["op"] == "move_agent"}
    problems = [f"operation {op!r} has no case and no REFUSED_BY_POLICY entry"
                for op in ops(tree) if op not in exercised and op not in REFUSED_BY_POLICY]
    problems += [f"the allowed move {frm or 'ungrouped'} → {to} ({agent}) has no case"
                 for (frm, to), agent in allowed_pairs(choices).items()
                 if (frm, to) not in moved]
    return problems


def selected(case_list, env=None) -> list[tuple[str, dict]]:
    """The cases part n of m runs: index modulo m equal to n - 1. Neither
    variable set, every case; one without the other is refused."""
    env = os.environ if env is None else env
    part, of = env.get(PART_ENV), env.get(OF_ENV)
    if part is None and of is None:
        return list(case_list)
    if part is None or of is None:
        raise ValueError(f"{PART_ENV} and {OF_ENV} are set together or not at all")
    n, m = int(part), int(of)
    if not 1 <= n <= m:
        raise ValueError(f"{PART_ENV}={n} is not a part of {OF_ENV}={m}")
    return [case for i, case in enumerate(case_list) if i % m == n - 1]


def suite_files(tree: Path) -> list[str]:
    """What runs inside a changed copy: `model_fixture.model_reading_tests`,
    repo-relative, less every walk."""
    tree = Path(tree).resolve()
    files = [path.relative_to(tree).as_posix()
             for path in model_fixture.model_reading_tests(tree)]
    return [rel for rel in files if not walk.is_walk(rel)]


TREE = tree_root()
CASES = cases(TREE)
SELECTED = selected(CASES)


# --------------------------------------------------------------------------- #
# The per-case run                                                             #
# --------------------------------------------------------------------------- #

def run_case(name: str, base: Path) -> dict:
    change_set = dict(SELECTED)[name]
    try:
        tree = walk.copy_tree(base / "tree", TREE)
        work = base / "save"
        work.mkdir()
        walk.save(tree, change_set["changes"], work,
                  requested_by=change_set["requested_by"])
    except AssertionError as exc:
        return {"error": f"the save itself failed: {exc}", "files": 0, "seconds": 0.0}
    files = suite_files(tree)
    started = time.monotonic()
    got = walk.run_suite(tree, files)
    return {**got, "files": len(files), "seconds": time.monotonic() - started}


def heavy(env=None) -> bool:
    """Whether the per-case runs are asked for: `BUREAU_SUITE_GREEN_RUN=1`."""
    env = os.environ if env is None else env
    return env.get(RUN_ENV) == "1"


def run_selected(names, base: Path, run=run_case) -> dict:
    """`run` for every case, side by side, with none of the switches in the
    environment the inner runs inherit — a copy never runs these cases."""
    saved = {name: os.environ.pop(name) for name in SWITCHES if name in os.environ}
    try:
        return walk.run_all(names, run, base)
    finally:
        os.environ.update(saved)


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    if not heavy():
        pytest.skip(SKIP_REASON)
    return run_selected([name for name, _ in SELECTED],
                        tmp_path_factory.mktemp("dre6865"))


@pytest.mark.parametrize("name", [name for name, _ in SELECTED])
def test_every_model_reading_test_passes_after_the_change(results, name, capsys):
    got = results[name]
    with capsys.disabled():
        print(f"\nsuite-green: {name} ran {got['files']} files in {got['seconds']:.0f}s",
              flush=True)
    assert "error" not in got, got.get("error")
    assert got["files"] > 0, "the inner run held no file"
    assert got["failed"] == [], "\n".join(got["failed"]) + "\n\n" + got["tail"]
    assert got["returncode"] == 0, got["tail"]


# --------------------------------------------------------------------------- #
# The cheap tests: they run at both speeds                                     #
# --------------------------------------------------------------------------- #

def _plant(tmp_path: Path, tree: Path = TREE) -> Path:
    """The files `cases` and `uncovered` read, copied out of `tree`."""
    for rel in ("config/model-choices.json", "config/repo-map.json",
                "config/models.yaml", "scripts/model_config_change.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((tree / rel).read_text())
    return tmp_path


def _allow_a_new_pair(planted: Path) -> tuple:
    """Allow, in the planted choices file, one move whose (from, to) pair no
    agent may make today. Returns the pair."""
    path = planted / "config" / "model-choices.json"
    choices = json.loads(path.read_text())
    pairs = allowed_pairs(choices)
    for agent in choices["agents"]:
        for move in agent.get("moves") or []:
            pair = (agent.get("group"), move["group"])
            if not move.get("allowed") and pair not in pairs:
                move["allowed"], move["reason"] = True, None
                path.write_text(json.dumps(choices, indent=2))
                return pair
    pytest.fail("every refused move shares its pair with an allowed one — "
                "no pair is left to plant")


def test_every_operation_and_every_allowed_move_has_a_case():
    assert uncovered(TREE, CASES) == []
    accepted = ops(TREE)
    for op, reason in REFUSED_BY_POLICY.items():
        assert op in accepted, f"REFUSED_BY_POLICY names {op!r}, which OPS no longer holds"
        found = _TEST_ID.search(reason)
        assert found, f"the reason for {op!r} names no test: {reason}"
        path, test = found.groups()
        assert f"def {test}(" in (TREE / path).read_text(), reason


def test_the_coverage_test_fails_on_an_operation_with_no_case(tmp_path):
    planted = _plant(tmp_path)
    script = planted / "scripts" / "model_config_change.py"
    script.write_text(script.read_text().replace(
        "OPS = {\n", 'OPS = {\n    "rename_group": ("group", "label"),\n', 1))
    assert "rename_group" in ops(planted)
    assert uncovered(planted, CASES) == [
        "operation 'rename_group' has no case and no REFUSED_BY_POLICY entry"]


def test_the_coverage_test_fails_on_a_newly_allowed_move_with_no_case(tmp_path):
    planted = _plant(tmp_path)
    frm, to = _allow_a_new_pair(planted)
    problems = uncovered(planted, CASES)
    assert len(problems) == 1 and f"{frm or 'ungrouped'} → {to}" in problems[0], problems


def test_the_cases_are_read_from_the_tree_the_variable_names(tmp_path, monkeypatch):
    """A copy whose choices file allows one move the tree's does not yields
    exactly one case more — the move — and nothing else changes."""
    planted = _plant(tmp_path)
    frm, to = _allow_a_new_pair(planted)
    monkeypatch.setenv(TREE_ENV, str(planted))
    assert tree_root() == planted.resolve()
    before, after = dict(CASES), dict(cases(tree_root()))
    added = sorted(set(after) - set(before))
    assert added == [f"move-{frm or 'ungrouped'}-to-{to}"], added
    assert set(before) <= set(after)
    assert {name: after[name] for name in before} == before
    assert uncovered(planted, list(after.items())) == []
    monkeypatch.delenv(TREE_ENV)
    assert tree_root() == ROOT


def test_the_cases_cover_every_kind_of_save_and_name_only_what_the_tree_holds():
    names = [name for name, _ in CASES]
    assert len(names) == len(set(names)), names
    choices = _choices(TREE)
    groups = {group["key"]: group for group in choices["groups"]}
    agents = {agent["name"] for agent in choices["agents"]}
    slugs = set(json.loads((TREE / "config" / "repo-map.json").read_text()))
    raw = _models(TREE)
    kinds = {}
    for name, change_set in CASES:
        assert change_set == {"requested_by": REQUESTED_BY,
                              "changes": change_set["changes"]}, name
        kinds.setdefault(tuple(c["op"] for c in change_set["changes"]), []).append(name)
        for change in change_set["changes"]:
            group = groups.get(change.get("group"))
            if change["op"] == "add_group":
                assert group is None and change["group"] not in raw["ladders"], name
                assert choices["add_group"]["allowed"], name
                assert change["model"] in {m["id"] for m in choices["models"]}, name
                continue
            if "group" in change:
                assert group is not None, (name, change)
            if "model" in change:
                offered = {c["model"] for c in group["choices"] if c["allowed"]}
                assert change["model"] in offered, (name, change)
            if "project" in change:
                assert change["project"] in slugs, (name, change)
            if change["op"] == "move_agent":
                assert change["agent"] in agents, (name, change)
            if change["op"] == "add_agent":
                assert change["agent"] not in agents | set(raw.get("about") or {}), name
    assert len(kinds[("set_project_model",)]) == 1
    assert len(kinds[("set_project_model", "clear_project_model")]) == 1
    assert len(kinds[("add_agent",)]) == 1
    defaults = {cs["changes"][0]["group"] for name, cs in CASES
                if cs["changes"][0]["op"] == "set_default_model"}
    assert defaults == {key for key, group in groups.items()
                        if any(c["allowed"] and c["model"] != group["default_model"]
                               for c in group["choices"])}
    assert len(kinds[("move_agent",)]) == len(allowed_pairs(choices))


def test_every_case_is_a_change_set_the_tree_accepts():
    """A refused case — a no-op, a model the tree does not price — would fail
    the save in the heavy run; here it fails in seconds."""
    mcc = model_fixture.fixture_module(TREE, "model_config_change")
    text = (TREE / "config" / "models.yaml").read_text()
    for name, change_set in CASES:
        _, refusals = mcc.apply_change_set(text, change_set, today="2026-10-11")
        assert refusals == [], (name, refusals)


def test_the_parts_cover_every_case_exactly_once():
    for of in range(1, len(CASES) + 2):
        runs = [selected(CASES, {PART_ENV: str(n), OF_ENV: str(of)})
                for n in range(1, of + 1)]
        names = [name for run in runs for name, _ in run]
        assert sorted(names) == sorted(name for name, _ in CASES), of
        assert len(names) == len(set(names)), of
    assert selected(CASES, {}) == CASES
    for env in ({PART_ENV: "1"}, {OF_ENV: "2"}, {PART_ENV: "3", OF_ENV: "2"},
                {PART_ENV: "0", OF_ENV: "2"}):
        with pytest.raises(ValueError):
            selected(CASES, env)


def test_the_inner_run_is_the_model_reading_tests_and_no_walk():
    files = suite_files(ROOT)
    every = [path.relative_to(ROOT).as_posix()
             for path in model_fixture.model_reading_tests(ROOT)]
    assert files == [rel for rel in every if not walk.is_walk(rel)]
    assert not [rel for rel in files if walk.is_walk(rel)], files
    # The file a Planners default change turned red while nothing ran it.
    assert "tests/test_plan_model_death_swing.py" in files
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() not in files


def test_the_heavy_cases_run_only_when_asked_and_the_inner_runs_see_no_switch(
        tmp_path, monkeypatch):
    assert not heavy({}) and not heavy({RUN_ENV: "0"}) and heavy({RUN_ENV: "1"})
    for name, value in ((TREE_ENV, str(ROOT)), (RUN_ENV, "1"),
                        (PART_ENV, "1"), (OF_ENV, "1")):
        monkeypatch.setenv(name, value)
    seen = run_selected(["a", "b"], tmp_path,
                        run=lambda name, base: {k: os.environ.get(k) for k in SWITCHES})
    assert seen == {"a": dict.fromkeys(SWITCHES), "b": dict.fromkeys(SWITCHES)}
    assert os.environ[RUN_ENV] == "1" and os.environ[TREE_ENV] == str(ROOT)
