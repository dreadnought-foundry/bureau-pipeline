"""DRE-6634 — `model_fallback.py select --repo` leads with that project's
exception, and review separation still holds.

DRE-6633 made `projects.<slug>.<kind>: <model>` a validated key of
config/models.yaml. This file proves selection honors it: the walk for a project
and a kind is `effective_ladders(CONFIG, slug)[<that kind's ladder>]` — the
exception first, then the kind's ladder with it removed — and the critic's
build/review separation is applied to that walk exactly as it is to the fleet
ladder.

THE TREE
--------
Every scenario runs against a COPY of `scripts/` and `config/` in a temp
directory whose models.yaml is the committed one plus
`projects: {atlas: {workhorse: claude-sonnet-5-5}}` and nothing for `portico`.
`model_fallback` reads its config off its own file's path, so the copy is the
only way to drive the real CLI on a config the repo does not ship. The probe is
stubbed (`BUREAU_FAKE_AVAILABLE`), so nothing reaches the network, and every
model is available.

Expected ids are read off the tree's ladders, never typed: the card's own
numbers (`claude-opus-5-5`, `claude-sonnet-5-5`) are what those reads are today,
and `test_the_card_s_numbers_are_what_the_ladders_say` pins that they are.

Run: cd bureau-pipeline && python3 -m pytest tests/test_project_model_exceptions.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
from model_config_text import config_with_session_agents_ungrouped  # noqa: E402

OPUS55 = "claude-opus-5-5"
SONNET55 = "claude-sonnet-5-5"
PROJECTS = {"atlas": {"workhorse": SONNET55}}


def make_tree(base: Path, projects: dict) -> Path:
    """A copy of scripts/ and config/ whose models.yaml carries `projects`,
    with every session-run agent in no group wherever the CEO last put them
    (DRE-6889) — the start the role-in-no-group case below needs."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "scripts", base / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "config", base / "config", ignore=ignore)
    cfg = yaml.safe_load(config_with_session_agents_ungrouped())
    cfg["projects"] = projects
    (base / "config" / "models.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return base


def tree_config(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text())


def ladder(cfg: dict, kind: str) -> list[str]:
    return [r["model"] for r in cfg["ladders"][cfg["kinds"][kind]["ladder"]]]


def env() -> dict:
    out = dict(os.environ)
    out["BUREAU_FAKE_AVAILABLE"] = "{}"  # every model available, no network
    return out


def select(tree: Path, *args: str, tmp: Path) -> tuple[str, str]:
    """`(model, note)` from the real CLI in `tree`."""
    note = tmp / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         *args, "--explain-file", str(note)],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n")


def py(tree: Path, code: str):
    """Run `code` in `tree`'s scripts dir; it prints one JSON value."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         f"sys.path.insert(0, {str(tree / 'scripts')!r})\n"
         "import model_fallback as mf\n"
         "probe = lambda m: True\n" + code],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    return make_tree(tmp_path_factory.mktemp("dre6634"), PROJECTS)


# --------------------------------------------------------------------------- #
# The tree really carries the exception                                        #
# --------------------------------------------------------------------------- #

def test_the_tree_s_config_loads_with_its_project_entry(tree):
    """A refused config falls back to the generated literal with `projects: {}`
    and every scenario below would pass for the wrong reason."""
    assert py(tree, "print(json.dumps(mf.CONFIG['projects']))") == PROJECTS


def test_the_card_s_numbers_are_what_the_ladders_say(tree):
    cfg = tree_config(tree)
    assert ladder(cfg, "workhorse")[0] == OPUS55
    assert ladder(cfg, "advisory")[0] == SONNET55
    assert SONNET55 in ladder(cfg, "workhorse")


# --------------------------------------------------------------------------- #
# The exception leads the walk; no entry is the fleet ladder                   #
# --------------------------------------------------------------------------- #

def test_engineer_on_atlas_runs_the_exception(tree, tmp_path):
    model, note = select(tree, "engineer", "--repo", "atlas", tmp=tmp_path)
    assert model == SONNET55
    assert note.endswith(
        f" — project atlas: exception {SONNET55} leads the workhorse walk")


def test_an_exception_leading_the_walk_is_not_a_degradation(tree, tmp_path):
    _, note = select(tree, "engineer", "--repo", "atlas", tmp=tmp_path)
    assert not note.startswith("DEGRADED")
    assert "top of the ladder, nothing above it was skipped" in note
    assert len(note.splitlines()) == 1


def test_engineer_on_portico_runs_the_fleet_top(tree, tmp_path):
    model, note = select(tree, "engineer", "--repo", "portico", tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0] == OPUS55
    assert note.endswith(" — project portico: no exception, fleet ladder")


def test_a_rung_skipped_below_the_exception_is_reported_as_today(tree, tmp_path):
    """The exception does not shield the walk from the probe: unavailable, it
    is skipped and named, and the run is DEGRADED exactly as on the fleet."""
    note = tmp_path / "n.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         "engineer", "--repo", "atlas", "--explain-file", str(note)],
        capture_output=True, text=True, cwd=tree,
        env=dict(env(), BUREAU_FAKE_AVAILABLE=json.dumps({SONNET55: False})),
    )
    assert proc.returncode == 0, proc.stderr
    walk = [SONNET55] + [m for m in ladder(tree_config(tree), "workhorse")
                         if m != SONNET55]
    assert proc.stdout.strip() == walk[1]
    text = note.read_text().strip()
    assert text.startswith("DEGRADED ")
    assert f"skipped {SONNET55} (" in text
    assert text.endswith(
        f" — project atlas: exception {SONNET55} leads the workhorse walk")


# --------------------------------------------------------------------------- #
# Review separation holds on the project walk                                  #
# --------------------------------------------------------------------------- #

def test_critic_on_atlas_built_on_sonnet55_skips_sonnet55(tree, tmp_path):
    cfg = tree_config(tree)
    rule = {r["built_on"]: r["reviewers_use"]
            for r in cfg["review_separation"]["rules"]}
    model, note = select(tree, "critic", "--repo", "atlas", "--built-on",
                         SONNET55, tmp=tmp_path)
    assert model == rule[SONNET55] == OPUS55
    assert f"this build ran on {SONNET55}, so the reviewer does not" in note
    assert note.endswith(" — project atlas: no exception, fleet ladder")


def test_critic_on_portico_built_on_opus55_runs_the_advisory_top(tree, tmp_path):
    model, note = select(tree, "critic", "--repo", "portico", "--built-on",
                         OPUS55, tmp=tmp_path)
    assert model == ladder(tree_config(tree), "advisory")[0] == SONNET55
    assert note.endswith(" — project portico: no exception, fleet ladder")


def test_separation_is_applied_to_an_advisory_exception_too(tmp_path):
    """The project walk is what separation narrows: an advisory exception on
    Sonnet 5.5 still loses that rung to a Sonnet-5.5 build."""
    tree = make_tree(tmp_path / "t", {"atlas": {"advisory": SONNET55}})
    model, note = select(tree, "critic", "--repo", "atlas", "--built-on",
                         SONNET55, tmp=tmp_path)
    assert model != SONNET55
    assert f"this build ran on {SONNET55}" in note
    assert note.endswith(
        f" — project atlas: exception {SONNET55} leads the advisory walk")


# --------------------------------------------------------------------------- #
# The two spellings, and everything that is not a project                      #
# --------------------------------------------------------------------------- #

def test_owner_name_resolves_like_the_slug(tree, tmp_path):
    by_slug = select(tree, "engineer", "--repo", "atlas", tmp=tmp_path)
    by_full = select(tree, "engineer", "--repo", "EveryBite/atlas", tmp=tmp_path)
    assert by_full == by_slug
    assert by_full[0] == SONNET55


def test_owner_name_is_matched_case_insensitively(tree):
    assert py(tree, "print(json.dumps(mf.resolve_repo_slug('everybite/ATLAS')))") \
        == "atlas"


def test_resolve_repo_slug_reads_both_spellings_and_nothing_else(tree):
    got = py(tree, "print(json.dumps([mf.resolve_repo_slug(v) for v in "
                   "['atlas', 'EveryBite/atlas', ' atlas ', 'nowhere', '', "
                   "'   ', None, 'EveryBite/nowhere']]))")
    assert got == ["atlas", "atlas", "atlas", None, None, None, None, None]


def test_an_unknown_value_walks_the_fleet_ladder(tree, tmp_path):
    model, note = select(tree, "engineer", "--repo", "nowhere", tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0]
    assert note.endswith(" — project unknown (nowhere): fleet ladder")


@pytest.mark.parametrize("args", [["--repo", ""], ["--repo", "   "], []],
                         ids=["empty", "whitespace", "no-flag"])
def test_no_project_walks_the_fleet_ladder(tree, tmp_path, args):
    model, note = select(tree, "engineer", *args, tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0]
    assert note.endswith(" — project none: fleet ladder")


def test_a_role_in_no_group_keeps_the_default_ladder_on_every_project(tree, tmp_path):
    cfg = tree_config(tree)
    assert "designer" in cfg["ungrouped"]
    assert "designer" not in cfg["agents"]
    default_top = [r["model"] for r in cfg["ladders"][cfg["default_ladder"]]][0]
    for repo in ("atlas", "EveryBite/atlas", "portico"):
        model, note = select(tree, "designer", "--repo", repo, tmp=tmp_path)
        assert model == default_top, repo
        assert note.endswith(": no exception, fleet ladder"), note


# --------------------------------------------------------------------------- #
# The Python surface                                                           #
# --------------------------------------------------------------------------- #

def test_select_with_reasons_names_the_project_and_the_exception(tree):
    got = py(tree, """
out = {}
for repo in ('atlas', 'portico', 'nowhere', '', None):
    d = mf.select_with_reasons('engineer', repo=repo, probe=probe)
    out[str(repo)] = [d['project'], d['exception'], d['model']]
print(json.dumps(out))
""")
    top = ladder(tree_config(tree), "workhorse")[0]
    assert got == {
        "atlas": ["atlas", SONNET55, SONNET55],
        "portico": ["portico", None, top],
        "nowhere": [None, None, top],
        "": [None, None, top],
        "None": [None, None, top],
    }


def test_ladder_for_is_the_project_walk(tree):
    got = py(tree, "print(json.dumps([mf.ladder_for('engineer', 'atlas'), "
                   "mf.ladder_for('engineer'), mf.ladder_for('engineer', 'portico'), "
                   "mf.ladder_for('designer', 'atlas')]))")
    fleet = ladder(tree_config(tree), "workhorse")
    assert got[0] == [SONNET55] + [m for m in fleet if m != SONNET55]
    assert got[1] == got[2] == got[3] == fleet


def test_fallback_for_reads_the_rung_below_on_the_project_walk(tree):
    got = py(tree, "print(json.dumps([mf.fallback_for('engineer', 'claude-sonnet-5-5', "
                   "repo='atlas'), mf.fallback_for('engineer', 'claude-sonnet-5-5')]))")
    fleet = ladder(tree_config(tree), "workhorse")
    walk = [SONNET55] + [m for m in fleet if m != SONNET55]
    assert got[0] == walk[1] == OPUS55
    assert got[1] == fleet[fleet.index(SONNET55) + 1]


def test_the_fallback_file_agrees_with_the_chosen_walk(tree, tmp_path):
    fallback = tmp_path / "fallback.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         "engineer", "--repo", "atlas", "--fallback-file", str(fallback)],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == SONNET55
    assert fallback.read_text().strip() == OPUS55


def test_effort_is_per_model_and_unchanged(tree, tmp_path):
    effort = tmp_path / "effort.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         "engineer", "--repo", "atlas", "--effort-file", str(effort)],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    want = tree_config(tree).get("effort", {}).get(SONNET55) or ""
    assert effort.read_text().strip() == want


def test_an_unknown_value_cannot_put_a_quote_in_the_note(tree):
    """The note rides a shell string (tests/test_qa_review_model_note.py)."""
    note = py(tree, "print(json.dumps(mf.selection_note(mf.select_with_reasons("
                    "'engineer', repo='a\"b`c$d\\nx', probe=probe))))")
    assert len(note.splitlines()) == 1
    for ch in '"`$':
        assert ch not in note
    assert " — project unknown (" in note


# --------------------------------------------------------------------------- #
# The committed config: only a project the console named changes anything      #
# --------------------------------------------------------------------------- #

def test_every_role_on_every_repo_selects_its_walk_top_on_the_committed_config():
    # Holds for any exceptions the console has written: a repo with none
    # selects the fleet pick, a repo with one selects the top of its walk.
    repo_map = json.loads((ROOT / "config" / "repo-map.json").read_text())
    projects = mf.CONFIG["projects"]
    probe = lambda m: True  # noqa: E731
    assert mf.AGENT_KINDS
    for role, kind in mf.AGENT_KINDS.items():
        name = mf.CONFIG["kinds"][kind]
        fleet = mf.select(role, probe=probe)
        for slug in repo_map:
            mf.clear_availability_cache()
            walk = mf.effective_ladders(mf.CONFIG, slug)[name]
            if kind not in projects.get(slug, {}):
                assert walk == mf.LADDERS[name], (role, slug)
                assert walk[0] == fleet, (role, slug)
            assert mf.select(role, repo=slug, probe=probe) == walk[0], (role, slug)
