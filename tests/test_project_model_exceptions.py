"""DRE-6634 — `model_fallback.py select --repo` leads with that project's
exception, and review separation still holds.

DRE-6633 made `projects.<slug>.<kind>: <model>` a validated key of a project's
choices. This file proves selection honors it: the walk for a project and a
kind is `effective_ladders(CONFIG, slug)[<that kind's ladder>]` — the exception
first, then the kind's ladder with it removed — and the critic's build/review
separation is applied to that walk exactly as it is to the fleet ladder.

THE DOCUMENT AND THE TREE (DRE-6989)
-----------------------------------
Since DRE-6939 the selector runs on the home or on the defaults in the code,
never on the choices in a tree's `config/models.yaml`. So the scenario is a
home document: the frozen one (`model_fixture.fixture_home()`, DRE-6972) with
`with_project(doc, "atlas", "workhorse", <a build rung below the top>)` and
nothing for `portico`, handed to a `model_fixture.fixture_tree` — today's
`scripts/` on the FROZEN model files (DRE-6859) — through `home_env`. The
console's saves change the home, so a rule proved on today's choices went red
when the CEO moved a role or led a group with another model (DRE-6863). The
code-side facts the scenarios lean on — `review_separation`, `effort`,
`ungrouped` — are read off the frozen `models.yaml`. The probe is stubbed
(`BUREAU_FAKE_AVAILABLE`), so nothing reaches the network, and every model is
available.

Expected ids are read off the frozen document's groups, and the role each
scenario runs off its `agents:` map, never typed:
`test_the_fixture_holds_the_scenario` pins the shape the names below rely on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_project_model_exceptions.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home  # noqa: E402
from model_fixture import fixture_home, home_env, ladder, with_project  # noqa: E402

FIXTURE = model_fixture.fixture_config()
HOME = fixture_home()
WORK = ladder(HOME, "workhorse")
# The build group's top: what a repo with no exception runs.
TOP = WORK[0]
# The exception: a build rung below the top that also tops the Reviewers, so
# the same id drives the review-separation scenarios.
EXCEPTION = ladder(HOME, "advisory")[0]
# The document every scenario on the module's tree runs on.
DOC = with_project(HOME, "atlas", "workhorse", EXCEPTION)

# The roles the scenarios run, read off the frozen document's `agents:` and
# the frozen `ungrouped:` and `review_separation` (code-side facts).
BUILDER = next(r for r, k in HOME["agents"].items()
               if k == "workhorse" and r not in (FIXTURE.get("session_agents") or []))
REVIEWER = FIXTURE["review_separation"]["roles"][0]
NO_GROUP = FIXTURE["ungrouped"][0]

# How every note on a document the reader accepted ends.
READ = model_home.status_clause({"state": model_home.READ})


def select(tree: Path, *args: str, tmp: Path, doc: dict = DOC) -> tuple[str, str]:
    """`(model, note)` from the real CLI in `tree`, on `doc`."""
    note = tmp / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         *args, "--explain-file", str(note)],
        capture_output=True, text=True, env=env(tree, doc), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return proc.stdout.strip(), note.read_text().rstrip("\n")


def env(tree: Path, doc: dict = DOC, available: dict | None = None) -> dict:
    """`doc` as the tree's home, every model available unless `available`
    says otherwise — no network."""
    return home_env(tree, doc, BUREAU_FAKE_AVAILABLE=available or {})


def py(tree: Path, code: str, doc: dict = DOC):
    """Run `code` in `tree`'s scripts dir on `doc`; it prints one JSON value."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\n"
         f"sys.path.insert(0, {str(tree / 'scripts')!r})\n"
         "import model_fallback as mf\n"
         "probe = lambda m: True\n" + code],
        capture_output=True, text=True, env=env(tree, doc), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    return model_fixture.fixture_tree(tmp_path_factory.mktemp("dre6634"))


# --------------------------------------------------------------------------- #
# The tree really reads the exception                                          #
# --------------------------------------------------------------------------- #

def test_the_tree_reads_the_document_with_its_project_entry(tree):
    """A refused document leaves the selector on the defaults in the code,
    with no atlas entry, and every scenario below would pass for the wrong
    reason."""
    got = py(tree, "mf.use_home()\n"
                   "print(json.dumps([mf.HOME_STATUS, mf.CONFIG['projects']]))")
    assert got == [model_home.READ, DOC["projects"]]
    assert DOC["projects"]["atlas"] == {"workhorse": EXCEPTION}


def test_the_fixture_holds_the_scenario():
    assert ladder(DOC, "workhorse") == WORK
    assert EXCEPTION in WORK and EXCEPTION != TOP
    assert EXCEPTION in {r["built_on"] for r in FIXTURE["review_separation"]["rules"]}
    assert DOC["agents"].get(BUILDER) == "workhorse"
    assert NO_GROUP not in DOC["agents"]
    assert "portico" not in DOC["projects"]


# --------------------------------------------------------------------------- #
# The exception leads the walk; no entry is the fleet ladder                   #
# --------------------------------------------------------------------------- #

def test_a_builder_on_atlas_runs_the_exception(tree, tmp_path):
    model, note = select(tree, BUILDER, "--repo", "atlas", tmp=tmp_path)
    assert model == EXCEPTION
    assert note.endswith(
        f" — project atlas: exception {EXCEPTION} leads the workhorse walk{READ}")


def test_an_exception_leading_the_walk_is_not_a_degradation(tree, tmp_path):
    _, note = select(tree, BUILDER, "--repo", "atlas", tmp=tmp_path)
    assert not note.startswith("DEGRADED")
    assert "top of the ladder, nothing above it was skipped" in note
    assert len(note.splitlines()) == 1


def test_a_builder_on_portico_runs_the_fleet_top(tree, tmp_path):
    model, note = select(tree, BUILDER, "--repo", "portico", tmp=tmp_path)
    assert model == ladder(DOC, "workhorse")[0] == TOP
    assert note.endswith(f" — project portico: no exception, fleet ladder{READ}")


def test_a_rung_skipped_below_the_exception_is_reported_as_today(tree, tmp_path):
    """The exception does not shield the walk from the probe: unavailable, it
    is skipped and named, and the run is DEGRADED exactly as on the fleet."""
    note = tmp_path / "n.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--explain-file", str(note)],
        capture_output=True, text=True, cwd=tree,
        env=env(tree, available={EXCEPTION: False}),
    )
    assert proc.returncode == 0, proc.stderr
    walk = [EXCEPTION] + [m for m in ladder(DOC, "workhorse") if m != EXCEPTION]
    assert proc.stdout.strip() == walk[1]
    text = note.read_text().strip()
    assert text.startswith("DEGRADED ")
    assert f"skipped {EXCEPTION} (" in text
    assert text.endswith(
        f" — project atlas: exception {EXCEPTION} leads the workhorse walk{READ}")


# --------------------------------------------------------------------------- #
# Review separation holds on the project walk                                  #
# --------------------------------------------------------------------------- #

def test_a_reviewer_on_atlas_built_on_the_exception_skips_it(tree, tmp_path):
    rule = {r["built_on"]: r["reviewers_use"]
            for r in FIXTURE["review_separation"]["rules"]}
    model, note = select(tree, REVIEWER, "--repo", "atlas", "--built-on",
                         EXCEPTION, tmp=tmp_path)
    assert model == rule[EXCEPTION] != EXCEPTION
    assert f"this build ran on {EXCEPTION}, so the reviewer does not" in note
    assert note.endswith(f" — project atlas: no exception, fleet ladder{READ}")


def test_a_reviewer_on_portico_built_on_the_fleet_top_runs_the_advisory_top(tree, tmp_path):
    model, note = select(tree, REVIEWER, "--repo", "portico", "--built-on",
                         TOP, tmp=tmp_path)
    assert model == ladder(DOC, "advisory")[0]
    assert note.endswith(f" — project portico: no exception, fleet ladder{READ}")


def test_separation_is_applied_to_an_advisory_exception_too(tmp_path):
    """The project walk is what separation narrows: an advisory exception on
    the Reviewers' top still loses that rung to a build on it."""
    doc = with_project(HOME, "atlas", "advisory", EXCEPTION)
    tree = model_fixture.fixture_tree(tmp_path / "t")
    model, note = select(tree, REVIEWER, "--repo", "atlas", "--built-on",
                         EXCEPTION, tmp=tmp_path, doc=doc)
    assert model != EXCEPTION
    assert f"this build ran on {EXCEPTION}" in note
    assert note.endswith(
        f" — project atlas: exception {EXCEPTION} leads the advisory walk{READ}")


def test_a_reviewers_model_for_atlas_leads_the_critic_on_the_owner_name(tmp_path):
    """DRE-6989: a document that gives atlas a Reviewers model of its own makes
    `select critic --repo EveryBite/atlas` lead with it — the model below the
    fleet's Reviewers top, so the fleet walk alone would not."""
    chosen = ladder(HOME, "advisory")[1]
    doc = with_project(HOME, "atlas", "advisory", chosen)
    assert ladder(doc, "advisory")[0] != chosen
    tree = model_fixture.fixture_tree(tmp_path / "t")
    model, note = select(tree, "critic", "--repo", "EveryBite/atlas",
                         tmp=tmp_path, doc=doc)
    assert model == chosen
    assert note.endswith(
        f" — project atlas: exception {chosen} leads the advisory walk{READ}")
    assert py(tree, "print(json.dumps(mf.ladder_for('critic', 'atlas')))", doc) == \
        [chosen] + [m for m in ladder(doc, "advisory") if m != chosen]


def test_an_exception_off_its_kinds_ladder_leads_the_walk(tmp_path):
    """DRE-6785: an exception may name any priced model. The Planners' last
    model is in their group only, and atlas's builders walk it first, then the
    Builders' group — through the CLI, `ladder_for` and `--fallback-file`."""
    off = ladder(HOME, "judgement")[-1]
    doc = with_project(HOME, "atlas", "workhorse", off)
    tree = model_fixture.fixture_tree(tmp_path / "t")
    fleet = ladder(doc, "workhorse")
    assert off not in fleet
    model, note = select(tree, "engineer", "--repo", "atlas", tmp=tmp_path, doc=doc)
    assert model == off
    assert note.endswith(
        f" — project atlas: exception {off} leads the workhorse walk{READ}")
    assert py(tree, "print(json.dumps(mf.ladder_for('engineer', 'atlas')))", doc) == \
        [off] + fleet
    assert py(tree, "print(json.dumps(mf.fallback_for('engineer', "
                    f"{off!r}, repo='atlas')))", doc) == fleet[0]


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
    assert model == ladder(DOC, "workhorse")[0]
    assert note.endswith(f" — project unknown (nowhere): fleet ladder{READ}")


@pytest.mark.parametrize("args", [["--repo", ""], ["--repo", "   "], []],
                         ids=["empty", "whitespace", "no-flag"])
def test_no_project_walks_the_fleet_ladder(tree, tmp_path, args):
    model, note = select(tree, BUILDER, *args, tmp=tmp_path)
    assert model == ladder(DOC, "workhorse")[0]
    assert note.endswith(f" — project none: fleet ladder{READ}")


def test_a_role_in_no_group_keeps_the_default_ladder_on_every_project(tree, tmp_path):
    # The defaults' Builders are the default walk, on a home as in the code.
    assert NO_GROUP in FIXTURE["ungrouped"]
    assert NO_GROUP not in DOC["agents"]
    default_top = ladder(DOC, "workhorse")[0]
    for repo in ("atlas", "EveryBite/atlas", "portico"):
        model, note = select(tree, NO_GROUP, "--repo", repo, tmp=tmp_path)
        assert model == default_top, repo
        assert note.endswith(f": no exception, fleet ladder{READ}"), note


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
    top = ladder(DOC, "workhorse")[0]
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
    fleet = ladder(DOC, "workhorse")
    assert got[0] == [EXCEPTION] + [m for m in fleet if m != EXCEPTION]
    assert got[1] == got[2] == got[3] == fleet


def test_fallback_for_reads_the_rung_below_on_the_project_walk(tree):
    got = py(tree, f"print(json.dumps([mf.fallback_for({BUILDER!r}, {EXCEPTION!r}, "
                   f"repo='atlas'), mf.fallback_for({BUILDER!r}, {EXCEPTION!r})]))")
    fleet = ladder(DOC, "workhorse")
    walk = [EXCEPTION] + [m for m in fleet if m != EXCEPTION]
    assert got[0] == walk[1] == TOP
    assert got[1] == fleet[fleet.index(EXCEPTION) + 1]


def test_the_fallback_file_agrees_with_the_chosen_walk(tree, tmp_path):
    fallback = tmp_path / "fallback.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--fallback-file", str(fallback)],
        capture_output=True, text=True, env=env(tree), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == EXCEPTION
    assert fallback.read_text().strip() == TOP


def test_effort_is_per_model_and_unchanged(tree, tmp_path):
    effort = tmp_path / "effort.txt"
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         BUILDER, "--repo", "atlas", "--effort-file", str(effort)],
        capture_output=True, text=True, env=env(tree), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    want = FIXTURE.get("effort", {}).get(EXCEPTION) or ""
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
