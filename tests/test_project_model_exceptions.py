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
Every scenario runs against `model_fixture.fixture_tree` — today's `scripts/`
on the FROZEN model files (DRE-6859) — whose models.yaml then carries
`projects: {atlas: {workhorse: <a build rung below the top>}}` and nothing for
`portico`. The console rewrites the committed file on every save, so a rule
proved on it went red when the CEO moved a role or led a group with another
model (DRE-6863). `model_fallback` reads its config off its own file's path, so
the tree is the only way to drive the real CLI on a config the repo does not
ship. The probe is stubbed (`BUREAU_FAKE_AVAILABLE`), so nothing reaches the
network, and every model is available.

Expected ids are read off the frozen ladders, and the role each scenario runs
off the frozen `agents:` map, never typed: `test_the_fixture_holds_the_scenario`
pins the shape the names below rely on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_project_model_exceptions.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
from model_fixture import ladder  # noqa: E402

FIXTURE = model_fixture.fixture_config()
WORK = ladder(FIXTURE, "workhorse")
# The build ladder's top: what a repo with no exception runs.
TOP = WORK[0]
# The exception: a build rung below the top that also tops the advisory ladder,
# so the same id drives the review-separation scenarios.
EXCEPTION = ladder(FIXTURE, "advisory")[0]
PROJECTS = {"atlas": {"workhorse": EXCEPTION}}

# The roles the scenarios run, read off the frozen `agents:` and `ungrouped:`.
BUILDER = next(r for r, k in FIXTURE["agents"].items()
               if k == "workhorse" and r not in (FIXTURE.get("session_agents") or []))
REVIEWER = FIXTURE["review_separation"]["roles"][0]
NO_GROUP = FIXTURE["ungrouped"][0]


def make_tree(base: Path, projects: dict) -> Path:
    """The frozen fixture tree at `base`, its models.yaml carrying `projects`."""
    model_fixture.fixture_tree(base)
    cfg = model_fixture.fixture_config()
    cfg["projects"] = projects
    (base / "config" / "models.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return base


def tree_config(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text())


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


def test_the_fixture_holds_the_scenario(tree):
    cfg = tree_config(tree)
    assert ladder(cfg, "workhorse") == WORK
    assert EXCEPTION in WORK and EXCEPTION != TOP
    assert EXCEPTION in {r["built_on"] for r in cfg["review_separation"]["rules"]}
    assert BUILDER in cfg["agents"] and cfg["agents"][BUILDER] == "workhorse"
    assert NO_GROUP not in cfg["agents"]


# --------------------------------------------------------------------------- #
# The exception leads the walk; no entry is the fleet ladder                   #
# --------------------------------------------------------------------------- #

def test_a_builder_on_atlas_runs_the_exception(tree, tmp_path):
    model, note = select(tree, BUILDER, "--repo", "atlas", tmp=tmp_path)
    assert model == EXCEPTION
    assert note.endswith(
        f" — project atlas: exception {EXCEPTION} leads the workhorse walk")


def test_an_exception_leading_the_walk_is_not_a_degradation(tree, tmp_path):
    _, note = select(tree, BUILDER, "--repo", "atlas", tmp=tmp_path)
    assert not note.startswith("DEGRADED")
    assert "top of the ladder, nothing above it was skipped" in note
    assert len(note.splitlines()) == 1


def test_a_builder_on_portico_runs_the_fleet_top(tree, tmp_path):
    model, note = select(tree, BUILDER, "--repo", "portico", tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0] == TOP
    assert note.endswith(" — project portico: no exception, fleet ladder")


def test_a_rung_skipped_below_the_exception_is_reported_as_today(tree, tmp_path):
    """The exception does not shield the walk from the probe: unavailable, it
    is skipped and named, and the run is DEGRADED exactly as on the fleet."""
    note = tmp_path / "n.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--explain-file", str(note)],
        capture_output=True, text=True, cwd=tree,
        env=dict(env(), BUREAU_FAKE_AVAILABLE=json.dumps({EXCEPTION: False})),
    )
    assert proc.returncode == 0, proc.stderr
    walk = [EXCEPTION] + [m for m in ladder(tree_config(tree), "workhorse")
                         if m != EXCEPTION]
    assert proc.stdout.strip() == walk[1]
    text = note.read_text().strip()
    assert text.startswith("DEGRADED ")
    assert f"skipped {EXCEPTION} (" in text
    assert text.endswith(
        f" — project atlas: exception {EXCEPTION} leads the workhorse walk")


# --------------------------------------------------------------------------- #
# Review separation holds on the project walk                                  #
# --------------------------------------------------------------------------- #

def test_a_reviewer_on_atlas_built_on_the_exception_skips_it(tree, tmp_path):
    cfg = tree_config(tree)
    rule = {r["built_on"]: r["reviewers_use"]
            for r in cfg["review_separation"]["rules"]}
    model, note = select(tree, REVIEWER, "--repo", "atlas", "--built-on",
                         EXCEPTION, tmp=tmp_path)
    assert model == rule[EXCEPTION] != EXCEPTION
    assert f"this build ran on {EXCEPTION}, so the reviewer does not" in note
    assert note.endswith(" — project atlas: no exception, fleet ladder")


def test_a_reviewer_on_portico_built_on_the_fleet_top_runs_the_advisory_top(tree, tmp_path):
    model, note = select(tree, REVIEWER, "--repo", "portico", "--built-on",
                         TOP, tmp=tmp_path)
    assert model == ladder(tree_config(tree), "advisory")[0]
    assert note.endswith(" — project portico: no exception, fleet ladder")


def test_separation_is_applied_to_an_advisory_exception_too(tmp_path):
    """The project walk is what separation narrows: an advisory exception on
    Sonnet 5.5 still loses that rung to a Sonnet-5.5 build."""
    tree = make_tree(tmp_path / "t", {"atlas": {"advisory": EXCEPTION}})
    model, note = select(tree, REVIEWER, "--repo", "atlas", "--built-on",
                         EXCEPTION, tmp=tmp_path)
    assert model != EXCEPTION
    assert f"this build ran on {EXCEPTION}" in note
    assert note.endswith(
        f" — project atlas: exception {EXCEPTION} leads the advisory walk")


def test_an_exception_off_its_kinds_ladder_leads_the_walk(tmp_path):
    """DRE-6785: an exception may name any priced model. Sonnet 4.6 is on the
    judgement ladder only, and atlas's builders walk it first, then the
    Builders' ladder — through the CLI, `ladder_for` and `--fallback-file`."""
    sonnet46 = "claude-sonnet-4-6"
    tree = make_tree(tmp_path / "t", {"atlas": {"workhorse": sonnet46}})
    fleet = ladder(tree_config(tree), "workhorse")
    assert sonnet46 not in fleet
    model, note = select(tree, "engineer", "--repo", "atlas", tmp=tmp_path)
    assert model == sonnet46
    assert note.endswith(
        f" — project atlas: exception {sonnet46} leads the workhorse walk")
    assert py(tree, "print(json.dumps(mf.ladder_for('engineer', 'atlas')))") == \
        [sonnet46] + fleet
    assert py(tree, "print(json.dumps(mf.fallback_for('engineer', "
                    f"{sonnet46!r}, repo='atlas')))") == fleet[0]


# --------------------------------------------------------------------------- #
# The two spellings, and everything that is not a project                      #
# --------------------------------------------------------------------------- #

def test_owner_name_resolves_like_the_slug(tree, tmp_path):
    by_slug = select(tree, BUILDER, "--repo", "atlas", tmp=tmp_path)
    by_full = select(tree, BUILDER, "--repo", "EveryBite/atlas", tmp=tmp_path)
    assert by_full == by_slug
    assert by_full[0] == EXCEPTION


def test_owner_name_is_matched_case_insensitively(tree):
    assert py(tree, "print(json.dumps(mf.resolve_repo_slug('everybite/ATLAS')))") \
        == "atlas"


def test_resolve_repo_slug_reads_both_spellings_and_nothing_else(tree):
    got = py(tree, "print(json.dumps([mf.resolve_repo_slug(v) for v in "
                   "['atlas', 'EveryBite/atlas', ' atlas ', 'nowhere', '', "
                   "'   ', None, 'EveryBite/nowhere']]))")
    assert got == ["atlas", "atlas", "atlas", None, None, None, None, None]


def test_an_unknown_value_walks_the_fleet_ladder(tree, tmp_path):
    model, note = select(tree, BUILDER, "--repo", "nowhere", tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0]
    assert note.endswith(" — project unknown (nowhere): fleet ladder")


@pytest.mark.parametrize("args", [["--repo", ""], ["--repo", "   "], []],
                         ids=["empty", "whitespace", "no-flag"])
def test_no_project_walks_the_fleet_ladder(tree, tmp_path, args):
    model, note = select(tree, BUILDER, *args, tmp=tmp_path)
    assert model == ladder(tree_config(tree), "workhorse")[0]
    assert note.endswith(" — project none: fleet ladder")


def test_a_role_in_no_group_keeps_the_default_ladder_on_every_project(tree, tmp_path):
    cfg = tree_config(tree)
    assert NO_GROUP in cfg["ungrouped"]
    assert NO_GROUP not in cfg["agents"]
    default_top = [r["model"] for r in cfg["ladders"][cfg["default_ladder"]]][0]
    for repo in ("atlas", "EveryBite/atlas", "portico"):
        model, note = select(tree, NO_GROUP, "--repo", repo, tmp=tmp_path)
        assert model == default_top, repo
        assert note.endswith(": no exception, fleet ladder"), note


# --------------------------------------------------------------------------- #
# The Python surface                                                           #
# --------------------------------------------------------------------------- #

def test_select_with_reasons_names_the_project_and_the_exception(tree):
    got = py(tree, f"""
out = {{}}
for repo in ('atlas', 'portico', 'nowhere', '', None):
    d = mf.select_with_reasons({BUILDER!r}, repo=repo, probe=probe)
    out[str(repo)] = [d['project'], d['exception'], d['model']]
print(json.dumps(out))
""")
    top = ladder(tree_config(tree), "workhorse")[0]
    assert got == {
        "atlas": ["atlas", EXCEPTION, EXCEPTION],
        "portico": ["portico", None, top],
        "nowhere": [None, None, top],
        "": [None, None, top],
        "None": [None, None, top],
    }


def test_ladder_for_is_the_project_walk(tree):
    got = py(tree, f"print(json.dumps([mf.ladder_for({BUILDER!r}, 'atlas'), "
                   f"mf.ladder_for({BUILDER!r}), mf.ladder_for({BUILDER!r}, 'portico'), "
                   f"mf.ladder_for({NO_GROUP!r}, 'atlas')]))")
    fleet = ladder(tree_config(tree), "workhorse")
    assert got[0] == [EXCEPTION] + [m for m in fleet if m != EXCEPTION]
    assert got[1] == got[2] == got[3] == fleet


def test_fallback_for_reads_the_rung_below_on_the_project_walk(tree):
    got = py(tree, f"print(json.dumps([mf.fallback_for({BUILDER!r}, {EXCEPTION!r}, "
                   f"repo='atlas'), mf.fallback_for({BUILDER!r}, {EXCEPTION!r})]))")
    fleet = ladder(tree_config(tree), "workhorse")
    walk = [EXCEPTION] + [m for m in fleet if m != EXCEPTION]
    assert got[0] == walk[1] == TOP
    assert got[1] == fleet[fleet.index(EXCEPTION) + 1]


def test_the_fallback_file_agrees_with_the_chosen_walk(tree, tmp_path):
    fallback = tmp_path / "fallback.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--fallback-file", str(fallback)],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == EXCEPTION
    assert fallback.read_text().strip() == TOP


def test_effort_is_per_model_and_unchanged(tree, tmp_path):
    effort = tmp_path / "effort.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--effort-file", str(effort)],
        capture_output=True, text=True, env=env(), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    want = tree_config(tree).get("effort", {}).get(EXCEPTION) or ""
    assert effort.read_text().strip() == want


def test_an_unknown_value_cannot_put_a_quote_in_the_note(tree):
    """The note rides a shell string (tests/test_qa_review_model_note.py)."""
    note = py(tree, "print(json.dumps(mf.selection_note(mf.select_with_reasons("
                    f"{BUILDER!r}, repo='a\"b`c$d\\nx', probe=probe))))")
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
