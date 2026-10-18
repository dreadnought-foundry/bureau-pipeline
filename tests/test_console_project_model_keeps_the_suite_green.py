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

EVERY PROJECT, EVERY GROUP (DRE-6883)
-------------------------------------
The three cases above tried agent-bureau-demo and portico, and both went green
while a save for atlas, or for any project's Planners, still turned the suite
red. So the walk also covers the whole grid: every repo slug in
config/repo-map.json × every kind in config/models.yaml, each pair saved alone
into its own copy. The grid is READ from those two files, never written here,
so a project or a group added later is walked the day it is added.

Each pair's model is one the policy accepts as that project's exception for
that group — a rung of the group's own ladder below its first, for which
`model_fallback.policy_errors` raises nothing under that pair's key. Where a
group accepts more than one, the projects take them in turn (the i-th project
in sorted order takes the i-th accepted rung, wrapping), so every accepted
rung of every group is saved by some project.

The cases run their suites side by side, as many at once as the machine has
cores: each is the whole model suite, and one after another they would cost
the sum of their wall clocks. The copy, the save and the inner run are
`tests/console_change_walk.py`'s, shared by every walk, and the inner run
holds no walk (DRE-6909). The walks run in their own job
(`scripts/unit_test_parts.py run-walks`), never in a numbered part.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_project_model_keeps_the_suite_green.py -q
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import console_change_walk as walk  # noqa: E402
import model_fallback as mf  # noqa: E402
from model_config_text import config_without_projects, without_projects  # noqa: E402

REPO_MAP = ROOT / "config" / "repo-map.json"
MODELS = ROOT / "config" / "models.yaml"

#: The six files #993 turned red, named by the card.
NAMED = (
    "tests/test_model_config.py",
    "tests/test_model_choices.py",
    "tests/test_model_projects_schema.py",
    "tests/test_project_model_exceptions.py",
    "tests/test_model_config_change.py",
    "tests/test_model_adoption_actions.py",
)

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


def file_pairs() -> set[tuple[str, str]]:
    """Every (project, group) pair the two files hold, read without the grid's
    help: the repo-map's keys × the models file's kinds."""
    slugs = json.loads(REPO_MAP.read_text())
    kinds = yaml.safe_load(MODELS.read_text())["kinds"]
    return {(slug, kind) for slug in slugs for kind in kinds}


def accepted_models(raw: dict, slug: str, kind: str) -> list[str]:
    """The rungs below the first of `kind`'s own ladder that the policy
    accepts as `slug`'s exception for `kind`, in ladder order."""
    cfg = mf._normalize_config(raw)
    ladder = cfg["ladders"][cfg["kinds"][kind]]
    prefix = f"projects.{slug}.{kind}: "
    accepted = []
    for model in ladder[1:]:
        trial = dict(raw, projects={slug: {kind: model}})
        if not any(error.startswith(prefix) for error in mf.policy_errors(trial)):
            accepted.append(model)
    return accepted


def grid() -> dict[str, tuple[str, str, str | None]]:
    """Case name → (project, group, model) for every pair `file_pairs` holds.
    A pair whose group accepts no model keeps None, so it is reported, never
    dropped."""
    raw = yaml.safe_load(config_without_projects())
    slugs = sorted(json.loads(REPO_MAP.read_text()))
    pairs = {}
    for i, slug in enumerate(slugs):
        for kind in raw["kinds"]:
            accepted = accepted_models(raw, slug, kind)
            model = accepted[i % len(accepted)] if accepted else None
            pairs[f"{slug}-{kind}"] = (slug, kind, model)
    return pairs


GRID = grid()

for _name, (_slug, _kind, _model) in GRID.items():
    CASES[_name] = ([[_set(_slug, _kind, _model)]], {_slug: {_kind: _model}})


def reset_projects(tree: Path) -> None:
    """Empty the copy's `projects` block and regenerate its mirrors."""
    config = tree / "config" / "models.yaml"
    config.write_text(without_projects(config.read_text()))
    walk.regenerate(tree)


def suite_files(tree: Path) -> list[str]:
    """The six named files plus every other `tests/test_model_*.py` — no
    walk."""
    return walk.inner_files(tree, NAMED, ("test_model_*.py",))


def run_case(name: str, base: Path) -> dict:
    saves, _ = CASES[name]
    tree = walk.copy_tree(base / "tree")
    reset_projects(tree)
    walk.apply_saves(tree, saves, base)
    return {
        "projects": yaml.safe_load(
            (tree / "config" / "models.yaml").read_text())["projects"],
        "choices": json.loads(
            (tree / "config" / "model-choices.json").read_text())["projects"],
        **walk.run_suite(tree, suite_files(tree)),
    }


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    return walk.run_all(CASES, run_case, tmp_path_factory.mktemp("dre6858"))


def test_the_suite_covers_the_six_files_and_every_model_test():
    files = suite_files(ROOT)
    assert files[:len(NAMED)] == list(NAMED)
    for rel in NAMED:
        assert (ROOT / rel).is_file(), rel
    assert len(files) > len(NAMED)
    assert all(re.fullmatch(r"tests/test_(model_\w+|project_model_exceptions)\.py", f)
               for f in files), files


def test_the_walk_covers_every_project_and_every_group():
    """DRE-6883: a project or a group the walk skips is one nobody proved."""
    pairs = file_pairs()
    assert len(pairs) >= 2 * 3, pairs
    walked = {(slug, kind) for slug, kind, _ in GRID.values()}
    assert walked == pairs
    assert len(GRID) == len(pairs)
    unsaveable = [name for name, (_, _, model) in GRID.items() if model is None]
    assert unsaveable == [], (
        f"no model the policy accepts below the first rung for {unsaveable}")
    for name in GRID:
        assert name in CASES, name


def test_every_accepted_rung_of_every_group_is_saved_by_some_project():
    raw = yaml.safe_load(config_without_projects())
    saved = {(kind, model) for _, kind, model in GRID.values()}
    for slug, kind in file_pairs():
        for model in accepted_models(raw, slug, kind):
            assert (kind, model) in saved, (kind, model)


def test_the_grid_reads_a_project_added_to_the_repo_map(monkeypatch, tmp_path):
    """The grid is the files', so a new project is walked the day it lands."""
    repo_map = tmp_path / "repo-map.json"
    slugs = dict(json.loads(REPO_MAP.read_text()), newcomer="example/newcomer")
    repo_map.write_text(json.dumps(slugs))
    monkeypatch.setattr(sys.modules[__name__], "REPO_MAP", repo_map)
    monkeypatch.setattr(mf, "declared_repo_slugs", lambda path=None: set(slugs))
    kinds = yaml.safe_load(MODELS.read_text())["kinds"]
    added = {name: case for name, case in grid().items() if case[0] == "newcomer"}
    assert {case[1] for case in added.values()} == set(kinds)
    assert all(case[2] is not None for case in added.values()), added


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
