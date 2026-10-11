"""DRE-6935 — every default model the console offers, and every move between
two groups it allows, merges with a green suite.

At 10:47 PT on 2026-10-11 the CEO set the Planners' default model to Opus 5.5
on Settings › Agents & models, and the pull request the save opened was red.
DRE-6853's rewrites had taken every pinned choice out of the model tests, but
DRE-6785 was built before them and brought pins back: a new file,
`tests/test_model_choice_policy.py`, tested rules against the committed
`config/models.yaml`, and three other tests still compared one group's leading
model with another's. Every one of the fifteen default changes the page
offered turned at least one of them red. DRE-6865's walk changes each group's
default once, to its first offered choice; this one changes it to every choice
the page offers.

THE CASES
---------
Read from the tree's `config/model-choices.json`, never typed, so a choice the
page newly offers is walked the day it lands:

  * for each group, every choice marked `allowed` that is not the group's
    `default_model` — one `set_default_model` save each — less a model the
    tree's `config/models.yaml` lists under `excluded`;
  * for each (from-group, to-group) pair an agent in a group may make, the
    first agent in the file allowed to make it — one `move_agent` save each.
    A session-run agent is in no group; its moves are
    `tests/test_console_agent_move_keeps_the_suite_green.py`'s.

SET ASIDE
---------
Since DRE-6786 the choices file offers a model we set aside (one under
`excluded`) to every group, with a caution, but `model_config_change.py
apply` still refuses to put it on a ladder, so its save opens no pull request
and has no suite to turn red. Those choices are `set_aside`, read off the same
two files: the coverage test counts them beside the walked cases, and
`test_a_set_aside_choice_is_refused_so_it_opens_no_pull_request` fails the day
the change script accepts one — the day the walk must start saving it.

THE SUITE RUN INSIDE THE COPY
-----------------------------
The files the card names, then every other `tests/test_model_*.py`,
`tests/test_project_model_*.py` and `tests/test_session_*.py` — less every
walk (DRE-6909). The copy, the save and the inner run are
`tests/console_change_walk.py`'s, shared by every walk, and the cases run side
by side in the walks' own job (`scripts/unit_test_parts.py run-walks`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_every_default_keeps_the_suite_green.py -q
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

CHOICES = ROOT / "config" / "model-choices.json"
MODELS = ROOT / "config" / "models.yaml"

#: The files the card names: the ones a default change turned red.
NAMED = (
    "tests/test_model_choice_policy.py",
    "tests/test_model_config.py",
    "tests/test_model_adoption.py",
    "tests/test_session_agents.py",
    "tests/test_project_model_exceptions.py",
)

#: What the inner run collects beyond the named files, walks left out.
PATTERNS = ("test_model_*.py", "test_project_model_*.py", "test_session_*.py")


def offered_defaults(choices: dict) -> list[tuple[str, str]]:
    """(group, model) for every `allowed` choice of every group that is not
    its `default_model`, in the file's order."""
    return [(group["key"], choice["model"])
            for group in choices["groups"]
            for choice in group["choices"]
            if choice["allowed"] and choice["model"] != group["default_model"]]


def set_aside(models: dict) -> set[str]:
    """The models `config/models.yaml` lists under `excluded`."""
    return {entry["model"] for entry in models.get("excluded") or []}


def allowed_moves(choices: dict) -> dict:
    """(from-group, to-group) → the first agent in a group the file lets make
    that move, in the file's order."""
    pairs: dict = {}
    for agent in choices["agents"]:
        if agent.get("group") is None:
            continue
        for move in agent.get("moves") or []:
            if move.get("allowed"):
                pairs.setdefault((agent["group"], move["group"]), agent["name"])
    return pairs


def cases(choices: dict, models: dict) -> dict:
    """Case name → the one change its save carries."""
    out = {}
    for group, model in offered_defaults(choices):
        if model in set_aside(models):
            continue
        out[f"default-{group}-{model}"] = {
            "op": "set_default_model", "group": group, "model": model}
    for (frm, to), agent in allowed_moves(choices).items():
        out[f"move-{agent}-{frm}-to-{to}"] = {
            "op": "move_agent", "agent": agent, "group": to}
    return out


CASES = cases(json.loads(CHOICES.read_text()), yaml.safe_load(MODELS.read_text()))


def suite_files(tree: Path) -> list[str]:
    """The named files plus every other `tests/test_model_*.py`,
    `tests/test_project_model_*.py` and `tests/test_session_*.py` — no walk."""
    return walk.inner_files(tree, NAMED, PATTERNS)


def run_case(name: str, base: Path) -> dict:
    tree = walk.copy_tree(base / "tree")
    walk.apply_saves(tree, [[CASES[name]]], base)
    return {
        "models": yaml.safe_load((tree / "config" / "models.yaml").read_text()),
        **walk.run_suite(tree, suite_files(tree)),
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    return walk.run_all(CASES, run_case, tmp_path_factory.mktemp("dre6935"))


def test_the_walk_covers_every_offered_default_and_every_allowed_move():
    """Counted straight off the file, without the case builder's help: a walk
    that covers fewer cases than the page offers fails here."""
    choices = json.loads(CHOICES.read_text())
    aside = {e["model"] for e in yaml.safe_load(MODELS.read_text())["excluded"]}
    defaults = {(group["key"], choice["model"])
                for group in choices["groups"] for choice in group["choices"]
                if choice["allowed"] and choice["model"] != group["default_model"]}
    pairs = {(agent["group"], move["group"])
             for agent in choices["agents"] if agent.get("group")
             for move in agent.get("moves") or [] if move.get("allowed")}
    assert len(defaults) >= len(choices["groups"]), defaults
    assert pairs, "the choices file allows no move between two groups"
    walked_defaults = {(c["group"], c["model"]) for c in CASES.values()
                       if c["op"] == "set_default_model"}
    home = {agent["name"]: agent.get("group") for agent in choices["agents"]}
    walked_pairs = {(home[c["agent"]], c["group"]) for c in CASES.values()
                    if c["op"] == "move_agent"}
    skipped = {(g, m) for g, m in defaults if m in aside}
    assert walked_defaults == defaults - skipped, sorted(defaults ^ walked_defaults)
    assert walked_pairs == pairs, sorted(pairs ^ walked_pairs)
    assert len(CASES) == len(defaults) - len(skipped) + len(pairs)


def test_the_cases_read_a_choice_the_file_newly_offers():
    """The walk is the file's: a choice allowed later is walked the day it
    lands, and the default itself never is."""
    choices = json.loads(CHOICES.read_text())
    models = yaml.safe_load(MODELS.read_text())
    group = choices["groups"][0]
    refused = next((c for c in group["choices"] if not c["allowed"]), None)
    if refused is None:
        refused = {"model": "claude-newcomer-1", "allowed": False, "reason": "x"}
        group["choices"].append(refused)
    assert f"default-{group['key']}-{refused['model']}" not in cases(choices, models)
    refused["allowed"], refused["reason"] = True, None
    got = cases(choices, models)
    assert got[f"default-{group['key']}-{refused['model']}"] == {
        "op": "set_default_model", "group": group["key"], "model": refused["model"]}
    assert f"default-{group['key']}-{group['default_model']}" not in got


def test_a_set_aside_choice_is_refused_so_it_opens_no_pull_request():
    """Every offered default the walk leaves out is one the change script
    refuses, and every one it walks is accepted — so the walk skips nothing a
    save could turn into a pull request."""
    choices = json.loads(CHOICES.read_text())
    text = MODELS.read_text()
    aside = set_aside(yaml.safe_load(text))

    def refusals(group: str, model: str) -> list:
        return mcc.apply_change_set(
            text, {"requested_by": "ceo@example.com", "changes": [
                {"op": "set_default_model", "group": group, "model": model}]},
            today="2026-10-11")[1]

    for group, model in offered_defaults(choices):
        found = refusals(group, model)
        if model in aside:
            assert found and any(f"{model} is excluded" in reason
                                 for r in found for reason in r["reasons"]), (
                f"the change script now accepts {model} for {group}: walk it")
        else:
            assert found == [], (group, model, found)


def test_the_suite_covers_the_named_files_and_every_model_and_session_test():
    files = suite_files(ROOT)
    assert files[:len(NAMED)] == list(NAMED)
    for rel in NAMED:
        assert (ROOT / rel).is_file(), rel
    for pattern in PATTERNS:
        for path in (ROOT / "tests").glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            assert (rel in files) != walk.is_walk(rel), rel
    assert Path(__file__).resolve().relative_to(ROOT).as_posix() not in files
    assert not [rel for rel in files if walk.is_walk(rel)], files


@pytest.mark.parametrize("name", list(CASES))
def test_the_saved_change_is_in_the_tree(results, name):
    """A save that never landed would leave the suite green for the wrong
    reason."""
    change, models = CASES[name], results[name]["models"]
    if change["op"] == "set_default_model":
        ladder = models["ladders"][models["kinds"][change["group"]]["ladder"]]
        first = ladder[0]["model"] if isinstance(ladder[0], dict) else ladder[0]
        assert first == change["model"], ladder
    else:
        assert models["agents"][change["agent"]] == change["group"]


@pytest.mark.parametrize("name", list(CASES))
def test_every_test_passes_after_the_save(results, name):
    got = results[name]
    assert got["failed"] == [], "\n".join(got["failed"]) + "\n\n" + got["tail"]
    assert got["returncode"] == 0, got["tail"]
