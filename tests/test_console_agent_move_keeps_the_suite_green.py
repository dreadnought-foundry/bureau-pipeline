"""DRE-6889 — moving a session-run agent into a group, or adding an agent, from
the console merges with a green suite.

On 2026-10-10 the CEO dragged Designer into Builders on Settings › Agents &
models. DRE-6833 made the pipeline accept that move, but the pull request a
save opens was still red: twenty tests across eight files read the committed
`config/models.yaml` and expected the five session-run agents to be in no
group, in that order, and nobody else. A save from the console must merge on
the suite as it stands, so this file does what `model-config-change.yml` does,
in a COPY of the tree, and then runs every test that reads the model files
inside the copy — the second slice of DRE-6853, beside DRE-6858's
`tests/test_console_project_model_keeps_the_suite_green.py`.

THE TREE
--------
Each case copies the repository (everything but `.git` and the pipeline's own
`.bureau-pipeline` checkout) into a temp directory. The copy starts where the
file stood before any save from the page: every session-run agent in no group
(`with_session_agents_ungrouped`) and no project exceptions (`projects: {}`),
mirrors regenerated. The committed file carries whatever the CEO last saved,
and a case that re-applies a move already in the tree is refused as a no-op
before the suite ever runs.

THE CASES
---------
Read from the copy's starting file, never written here, so an agent or a group
added later is walked the day it lands:

  * every session-run agent into every group, one save each;
  * all of them into Builders (the group on the default ladder) in one save;
  * an agent added; an agent added and moved into Builders in one save;
  * a session-run agent moved, then moved again in a second save;
  * a session-run agent moved, then a project's own model saved — what
    DRE-6858's file does inside a tree that already holds a moved agent.

THE SUITE RUN INSIDE THE COPY
-----------------------------
The seven files the card names, then every other `tests/test_model_*.py`,
`tests/test_session_*.py` and `tests/test_console_*.py` — less every walk
(DRE-6909). A walk never runs a walk: on 2026-10-11 this list held DRE-6858's
walk and this file, and twenty copies of the tree each ran both again, which
put `scripts unit tests (part 1)` past its 30-minute limit. What DRE-6858's
walk proves inside such a tree is the last case above. The copy, the save and
the inner run are `tests/console_change_walk.py`'s, shared by every walk.

The cases run side by side, as many at once as the machine has cores, in the
walks' own job (`scripts/unit_test_parts.py run-walks`), never in a numbered
part.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_agent_move_keeps_the_suite_green.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import console_change_walk as walk  # noqa: E402
import model_config_change as mcc  # noqa: E402
from model_config_text import (  # noqa: E402
    fresh_agent_name,
    with_session_agents_ungrouped,
    without_projects,
)

MODELS = ROOT / "config" / "models.yaml"
PROJECT_HARNESS = "tests/test_console_project_model_keeps_the_suite_green.py"

#: The files the card names: the ones the console's agent moves turned red.
#: DRE-6889 named an eighth, DRE-6858's walk, which a walk never runs
#: (DRE-6909).
NAMED = (
    "tests/test_session_agents.py",
    "tests/test_model_choices.py",
    "tests/test_model_projects_schema.py",
    "tests/test_model_config_change.py",
    "tests/test_model_policy.py",
    "tests/test_model_config.py",
    "tests/test_project_model_exceptions.py",
)

#: What the inner run collects beyond the named files, walks left out.
PATTERNS = ("test_model_*.py", "test_session_*.py", "test_console_*.py")

#: The exact change set of bureau-pipeline #993 (DRE-6858's first case).
PR_993 = {"op": "set_project_model", "project": "agent-bureau-demo",
          "group": "workhorse", "model": "claude-sonnet-5-5"}


def _move(agent: str, group: str) -> dict:
    return {"op": "move_agent", "agent": agent, "group": group}


def _add(agent: str) -> dict:
    return {"op": "add_agent", "agent": agent, "about": "Answers questions from our data"}


def start_text(text: str) -> str:
    """The copy's starting file: no session-run agent in a group, no project
    exceptions."""
    return without_projects(with_session_agents_ungrouped(text))


def builders(raw: dict) -> str:
    """The group the default ladder backs — Builders."""
    return next(kind for kind, spec in raw["kinds"].items()
                if spec["ladder"] == raw["default_ladder"])


def cases(text: str) -> dict:
    """Case name → (saves, where each agent ends up, projects). A save is one
    change set; an agent's place is its group, or None for no group."""
    raw = yaml.safe_load(start_text(text))
    session, groups = raw["session_agents"], list(raw["kinds"])
    first, home = session[0], builders(raw)
    other = next(group for group in groups if group != home)
    added = fresh_agent_name(raw)
    out = {}
    for agent in session:
        for group in groups:
            out[f"{agent}-into-{group}"] = ([[_move(agent, group)]], {agent: group}, {})
    out["all-into-builders"] = ([[_move(agent, home) for agent in session]],
                                dict.fromkeys(session, home), {})
    out["added"] = ([[_add(added)]], {added: None}, {})
    out["added-and-moved"] = ([[_add(added), _move(added, home)]], {added: home}, {})
    out["moved-twice"] = ([[_move(first, home)], [_move(first, other)]],
                          {first: other}, {})
    out["moved-then-a-project-model"] = (
        [[_move(first, home)], [PR_993]], {first: home},
        {PR_993["project"]: {PR_993["group"]: PR_993["model"]}})
    return out


CASES = cases(MODELS.read_text())


def reset(tree: Path) -> None:
    """Put the copy where the file stood before any save, and regenerate its
    mirrors."""
    config = tree / "config" / "models.yaml"
    config.write_text(start_text(config.read_text()))
    walk.regenerate(tree)


def suite_files(tree: Path) -> list[str]:
    """The named files plus every other `tests/test_model_*.py`,
    `tests/test_session_*.py` and `tests/test_console_*.py` — no walk."""
    return walk.inner_files(tree, NAMED, PATTERNS)


def run_case(name: str, base: Path) -> dict:
    tree = walk.copy_tree(base / "tree")
    reset(tree)
    walk.apply_saves(tree, CASES[name][0], base)
    return {
        "models": yaml.safe_load((tree / "config" / "models.yaml").read_text()),
        "choices": json.loads((tree / "config" / "model-choices.json").read_text()),
        **walk.run_suite(tree, suite_files(tree)),
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    return walk.run_all(CASES, run_case, tmp_path_factory.mktemp("dre6889"))


def test_the_suite_covers_the_named_files_and_every_model_session_and_console_test():
    files = suite_files(ROOT)
    assert files[:len(NAMED)] == list(NAMED)
    for rel in NAMED:
        assert (ROOT / rel).is_file(), rel
    for pattern in PATTERNS:
        for path in (ROOT / "tests").glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            assert (rel in files) != walk.is_walk(rel), rel


def test_the_inner_run_holds_no_walk():
    """DRE-6909: this list held DRE-6858's walk and this file on 2026-10-11."""
    files = suite_files(ROOT)
    assert PROJECT_HARNESS not in files
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() not in files
    assert not [rel for rel in files if walk.is_walk(rel)], files


def test_the_walk_covers_every_session_agent_and_every_group():
    """A pair the walk skips is a move nobody proved — read straight off the
    file, without the case builder's help."""
    raw = yaml.safe_load(MODELS.read_text())
    pairs = {(agent, group) for agent in raw["session_agents"] for group in raw["kinds"]}
    assert len(pairs) >= 5 * 3, pairs
    walked = {(agent, group) for saves, _, _ in CASES.values() if len(saves) == 1
              and len(saves[0]) == 1 and saves[0][0]["op"] == "move_agent"
              for agent, group in [(saves[0][0]["agent"], saves[0][0]["group"])]}
    assert pairs <= walked, sorted(pairs - walked)


def test_the_cases_read_an_agent_and_a_group_added_to_the_file():
    """The walk is the file's, so a newcomer is walked the day it lands."""
    text = MODELS.read_text()
    text = text.replace("\nsession_agents:\n", "\nsession_agents:\n  - newcomer\n", 1)
    text = text.replace("\nungrouped:\n", "\nungrouped:\n  - newcomer\n", 1)
    text = text.replace("\nkinds:\n", "\nkinds:\n  extra:\n    ladder: workhorse\n"
                        "    label: Extra\n    purpose: A group added later.\n", 1)
    raw = yaml.safe_load(text)
    assert "newcomer" in raw["session_agents"] and "extra" in raw["kinds"]
    got = cases(text)
    for group in raw["kinds"]:
        assert f"newcomer-into-{group}" in got, group
    for agent in raw["session_agents"]:
        assert f"{agent}-into-extra" in got, agent


def test_the_cases_start_from_no_session_agent_in_a_group():
    """A tree the CEO has already saved a move into is walked from the same
    start, so `designer-into-workhorse` is never refused as a no-op."""
    raw = yaml.safe_load(MODELS.read_text())
    agent, home = raw["session_agents"][0], builders(raw)
    moved, refusals = mcc.apply_change_set(
        start_text(MODELS.read_text()), {"requested_by": "ceo@example.com", "changes": [_move(agent, home)]},
        today="2026-10-11")
    assert refusals == []
    start = yaml.safe_load(start_text(moved))
    assert agent not in start["agents"]
    assert agent in yaml.safe_load(moved)["agents"]
    assert start["ungrouped"] == raw["session_agents"]
    assert cases(moved) == cases(MODELS.read_text())


def test_the_added_agent_is_a_name_the_file_does_not_hold():
    raw = yaml.safe_load(start_text(MODELS.read_text()))
    added = CASES["added"][0][0][0]["agent"]
    assert added not in raw["agents"] and added not in raw["ungrouped"]
    assert added not in raw["session_agents"] and added not in raw["about"]


@pytest.mark.parametrize("name", list(CASES))
def test_the_saved_change_is_in_the_tree(results, name):
    """A save that never landed would leave the suite green for the wrong
    reason."""
    _, places, projects = CASES[name]
    got = results[name]
    models, choices = got["models"], got["choices"]
    published = {agent["name"]: agent for agent in choices["agents"]}
    for agent, group in places.items():
        assert agent in models["session_agents"], agent
        if group is None:
            assert agent in models["ungrouped"] and agent not in models["agents"]
        else:
            assert models["agents"][agent] == group and agent not in models["ungrouped"]
        assert published[agent]["group"] == group, published[agent]
        assert published[agent]["runs_in"] == "session", published[agent]
    assert (models.get("projects") or {}) == projects


@pytest.mark.parametrize("name", list(CASES))
def test_every_test_passes_after_the_save(results, name):
    got = results[name]
    assert got["failed"] == [], "\n".join(got["failed"]) + "\n\n" + got["tail"]
    assert got["returncode"] == 0, got["tail"]
