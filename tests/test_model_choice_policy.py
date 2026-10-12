"""DRE-6785 — the model policy cautions instead of refusing: any priced model may
lead a group or a project, and review separation is derived, never refused.

THE DECISION (the CEO, 2026-10-10 17:54 PT, looking at the Planners' dropdown
with five of seven models grayed out): "It shouldn't gray out these models. I
should be able to set it to anything I want. Fix this."

So an explicit choice of any model with a declared price is admissible for any
group and for any project, and the promise that a pull request is never
reviewed by the model that built it is kept where the reviewer's model is
selected — on the walk that project's reviewers really make — rather than by
refusing the choice. Nothing automatic changes: the probe only walks down,
discovery adopts nothing onto a build or planning ladder, an adoption never
strands a project's choice, and a model a project names stays a known model.

THE DOCUMENT AND THE TREE (DRE-6989)
-----------------------------------
Since DRE-6939 the selector runs on the home or on the defaults in the code,
never on the choices in a tree's `config/models.yaml`. So every scenario is a
home document: the frozen one (`model_fixture.fixture_home()`, DRE-6972)
edited with the shared `lead_with` and `with_project`, judged by
`model_home.home_errors` against the frozen prices and the frozen code-side
facts, and handed to a `model_fixture.fixture_tree` through `home_env` or
`fixture_module(..., doc=...)`. A console save changes the home, so a scenario
read off today's choices would go red on a legitimate save (DRE-6866); every
model id below is read off the frozen document's groups or the frozen
`models.yaml`, never typed. What is a fact of the code — `review_separation`,
`retired`, `excluded`, the old file's schema and the adoption that edits it —
is still proved on the frozen `models.yaml` through `policy_errors`. Only the
`todays_` tests read the committed file, and they check what holds for any
saved state. The probe is stubbed (`BUREAU_FAKE_AVAILABLE`, or a lambda
in-process): no network.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_choice_policy.py -q
"""

from __future__ import annotations

import copy
import hashlib
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

import model_adoption as ma  # noqa: E402
import model_adoption_actions as maa  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home  # noqa: E402
from model_fixture import (  # noqa: E402
    fixture_home, fixture_module, fixture_tree, home_env, ladder, lead_with,
    with_project,
)

CONFIG_PATH = ROOT / "config" / "models.yaml"
REPO_MAP_PATH = ROOT / "config" / "repo-map.json"

# Each id is named for the model it is in the frozen document, and read off the
# place in its groups the scenarios rely on.
HOME = fixture_home()
FROZEN = model_fixture.fixture_config()
# The Builders' top, which is also the Reviewers' last model.
OPUS55 = ladder(HOME, "workhorse")[0]
OPUS5 = ladder(HOME, "workhorse")[1]
# The Reviewers' top two, both Builders' models too.
SONNET55 = ladder(HOME, "advisory")[0]
SONNET5 = ladder(HOME, "advisory")[1]
# The Planners' top, and their last model, which is in no other group.
FABLE51 = ladder(HOME, "judgement")[0]
SONNET46 = ladder(HOME, "judgement")[-1]
# The priced model the frozen file sets aside under `excluded`.
HAIKU55 = next(e["model"] for e in FROZEN["excluded"] if "haiku" in e["model"])

REVIEWERS = ("critic", "verifier")

# How every note on a document the reader accepted ends.
READ = model_home.status_clause({"state": model_home.READ})


def unseen(family: str, version: int) -> str:
    """A model id no frozen file names, in the vendor's spelling of the ids it
    does name."""
    return f"{OPUS55.split('-')[0]}-{family}-{version}"


def successor(model: str) -> str:
    """The same-family id one minor version above `model`."""
    head, _, minor = model.rpartition("-")
    return f"{head}-{int(minor) + 1}"


# Priced nowhere, and of no family the fixture runs.
UNPRICED = unseen("nova", 9)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def committed() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def frozen() -> dict:
    return model_fixture.fixture_config()


def prices() -> dict:
    return model_fixture.fixture_prices()


def slugs() -> set[str]:
    return set(json.loads(REPO_MAP_PATH.read_text()))


def errors(cfg: dict, with_prices: dict | None = None) -> list[str]:
    """`policy_errors` on a copy of the frozen `models.yaml` — the code-side
    facts and the old file's schema."""
    return mf.policy_errors(cfg, prices() if with_prices is None else with_prices,
                            repo_slugs=slugs())


def ladder_name(cfg: dict, kind: str) -> str:
    return cfg["kinds"][kind]["ladder"]


def code_side(excluded: list[str] | None = None) -> dict:
    """The frozen code-side facts a home is judged by, `excluded` replaced
    when given."""
    norm = mf._normalize_config(frozen())
    return {
        "excluded": list(norm["excluded"] if excluded is None else excluded),
        "retired": list(norm["retired"]),
        "session_agents": list(norm.get("session_agents") or []),
        "agents": model_home._registry_names(model_fixture.FIXTURE_DIR / "agents.yaml"),
    }


def refusals(doc: dict, side: dict | None = None) -> list[str]:
    """`model_home.home_errors` for `doc` on the frozen prices and facts: what
    every writer of the home checks before it writes."""
    return model_home.home_errors(doc, prices(), side or code_side(), slugs())


def as_config(doc: dict, raw: dict | None = None) -> dict:
    """The config the selector runs on with `doc` read: the code-side facts of
    `raw` (the frozen `models.yaml` by default) and the document's choices."""
    return {**mf._normalize_config(raw or frozen()), **mf._home_choices(doc)}


def env(tree: Path, doc: dict | None, available: dict | None = None) -> dict:
    """`doc` as `tree`'s home (no home when None), the probe stubbed."""
    fake = {"BUREAU_FAKE_AVAILABLE": json.dumps(available or {})}
    if doc is None:
        return {**os.environ, **fake}
    return home_env(tree, doc, **fake)


def cli(tree: Path, doc: dict, role: str, *args: str, available: dict | None = None):
    """`(model, note)` from the real CLI in `tree`, on `doc`."""
    note = tree / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         role, *args, "--explain-file", str(note)],
        capture_output=True, text=True, env=env(tree, doc, available), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return proc.stdout.strip(), note.read_text().strip()


def py(tree: Path, doc: dict, code: str):
    """Run `code` against `tree`'s model_fallback on `doc`; it prints one JSON
    value."""
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


def decision(tree: Path, doc: dict, role: str, **kwargs) -> dict:
    """`select_with_reasons(role, **kwargs)` in `tree` on `doc`, plus its
    note. An `unavailable` list is the models the probe reports down."""
    down = kwargs.pop("unavailable", [])
    return py(tree, doc, (
        f"down = {down!r}\n"
        f"d = mf.select_with_reasons({role!r}, probe=lambda m: m not in down, "
        f"**{kwargs!r})\n"
        "d['note'] = mf.selection_note(d)\n"
        "print(json.dumps(d))"
    ))


# --------------------------------------------------------------------------- #
# Five choices, each admitted and each leading its walk                        #
# --------------------------------------------------------------------------- #

CHOICES = {
    "planners-opus55": (lambda: lead_with(HOME, "judgement", OPUS55),
                        "planner", None, OPUS55),
    "builders-fable51": (lambda: lead_with(HOME, "workhorse", FABLE51),
                         "engineer", None, FABLE51),
    # DRE-6989: the Builders led with the Reviewers' first model.
    "builders-sonnet55": (lambda: lead_with(HOME, "workhorse", SONNET55),
                          "engineer", None, SONNET55),
    "reviewers-opus55": (lambda: lead_with(HOME, "advisory", OPUS55),
                         "critic", None, OPUS55),
    "atlas-builders-sonnet46": (
        lambda: with_project(HOME, "atlas", "workhorse", SONNET46),
        "engineer", "atlas", SONNET46),
}


@pytest.mark.parametrize("name", list(CHOICES))
def test_a_choice_is_admitted_and_leads_its_walk(name, tmp_path):
    build, role, repo, chosen = CHOICES[name]
    doc = build()
    assert refusals(doc) == []
    tree = fixture_tree(tmp_path)
    repo_args = ("--repo", repo) if repo else ()
    model, note = cli(tree, doc, role, *repo_args)
    assert model == chosen
    assert not note.startswith("DEGRADED"), note
    assert note.endswith(READ), note
    assert py(tree, doc, f"print(json.dumps(mf.select({role!r}, repo={repo!r}, "
                         "probe=probe)))") == chosen


def test_the_builders_led_with_the_reviewers_first_model_select_it_for_the_engineer(tmp_path):
    """DRE-6989: the frozen defaults lead the Builders with another model, so
    only the document can make the engineer print the Reviewers' first."""
    first = ladder(HOME, "advisory")[0]
    builders_top = ladder(HOME, "workhorse")[0]
    assert builders_top != first
    doc = lead_with(HOME, "workhorse", first)
    tree = fixture_tree(tmp_path)
    on_doc, note = cli(tree, doc, "engineer")
    on_defaults, _ = cli(tree, None, "engineer")
    assert on_doc == first
    assert note.endswith(READ), note
    assert on_defaults == builders_top


def test_the_reviewers_choice_needs_no_declared_rule():
    doc = lead_with(HOME, "advisory", OPUS55)
    rules = {r["built_on"] for r in FROZEN["review_separation"]["rules"]}
    assert OPUS55 not in rules
    assert OPUS55 in ladder(doc, "workhorse")
    assert refusals(doc) == []


def test_a_project_exception_off_its_ladder_walks_it_first_then_the_ladder(tmp_path):
    doc = with_project(HOME, "atlas", "workhorse", SONNET46)
    assert SONNET46 not in ladder(doc, "workhorse")
    want = [SONNET46, *ladder(doc, "workhorse")]
    tree = fixture_tree(tmp_path)
    fm = fixture_module(tree, doc=doc)
    assert fm.effective_ladders(fm.CONFIG, "atlas")["workhorse"] == want
    got = py(tree, doc, "print(json.dumps([mf.ladder_for('engineer', 'atlas'), "
                        f"mf.fallback_for('engineer', {SONNET46!r}, 'atlas'), "
                        "mf.select_with_reasons('engineer', repo='atlas', "
                        "probe=probe)['exception']]))")
    assert got == [want, OPUS55, SONNET46]


# --------------------------------------------------------------------------- #
# Four separations, derived with no declared rule                              #
# --------------------------------------------------------------------------- #

SEPARATIONS = {
    # (a) the fleet's Reviewers lead with the fleet's build model.
    "a-fleet-reviewers-opus55": (
        lambda: lead_with(HOME, "advisory", OPUS55), None, OPUS55, SONNET55),
    # (b) atlas builds on the model the fleet's reviewers lead with.
    "b-atlas-builds-sonnet55": (
        lambda: with_project(HOME, "atlas", "workhorse", SONNET55),
        "atlas", SONNET55, OPUS55),
    # (c) atlas's reviewers lead with the model atlas builds on.
    "c-atlas-reviewers-opus55": (
        lambda: with_project(HOME, "atlas", "advisory", OPUS55),
        "atlas", OPUS55, SONNET55),
    # (d) atlas builds AND reviews on the same off-group model.
    "d-atlas-both-sonnet46": (
        lambda: with_project(with_project(HOME, "atlas", "workhorse", SONNET46),
                             "atlas", "advisory", SONNET46),
        "atlas", SONNET46, SONNET55),
}


@pytest.mark.parametrize("name", list(SEPARATIONS))
def test_a_reviewer_is_separated_from_the_build_model(name, tmp_path):
    build, repo, built_on, reviewed_on = SEPARATIONS[name]
    doc = build()
    assert refusals(doc) == []
    tree = fixture_tree(tmp_path)
    fm = fixture_module(tree, doc=doc)
    assert fm.reviewers_for(fm.CONFIG, built_on, repo)[0] == reviewed_on
    assert built_on not in fm.reviewers_for(fm.CONFIG, built_on, repo)
    repo_args = ("--repo", repo) if repo else ()
    for role in REVIEWERS:
        model, note = cli(tree, doc, role, *repo_args, "--built-on", built_on)
        assert model == reviewed_on, (role, note)
        assert f"this build ran on {built_on}, so the reviewer does not" in note
        got = decision(tree, doc, role, repo=repo, built_on=built_on)
        assert got["model"] == reviewed_on, role
        assert got["separated"] == [built_on]
        assert not got["degraded"]
        assert built_on in got["note"]


def test_reviewers_for_reads_the_raw_and_the_normalized_shape():
    # Only atlas carries an entry, and portico must be a project with none here.
    cfg = frozen()
    cfg["projects"] = {"atlas": {"advisory": OPUS55}}
    norm = mf._normalize_config(cfg)
    assert mf.reviewers_for(cfg, OPUS55, "atlas") == \
        mf.reviewers_for(norm, OPUS55, "atlas") == [SONNET55, SONNET5]
    # No slug, or one with no entry, is the fleet's walk.
    fleet = ladder(cfg, "advisory")
    assert mf.reviewers_for(cfg, None) == fleet
    assert mf.reviewers_for(cfg, OPUS5, "portico") == fleet
    assert mf.reviewers_for(cfg, SONNET55, "nowhere") == [OPUS55, SONNET5]


# --------------------------------------------------------------------------- #
# The unknown build model, on the frozen document and on the Reviewers → Opus 5.5 one #
# --------------------------------------------------------------------------- #

def test_an_unknown_build_still_lands_on_opus55_on_the_frozen_document(tmp_path):
    tree = fixture_tree(tmp_path)
    for role in REVIEWERS:
        assert decision(tree, HOME, role, built_on_unknown=True)["model"] == OPUS55, role


def test_the_unknown_case_on_reviewers_opus55_keeps_the_last_rung_and_says_so(tmp_path):
    doc = lead_with(HOME, "advisory", OPUS55)
    assert ladder(doc, "advisory") == [OPUS55, SONNET55, SONNET5]
    tree = fixture_tree(tmp_path)
    for role in REVIEWERS:
        got = decision(tree, doc, role, built_on_unknown=True)
        assert got["model"] == SONNET5, role
        assert sorted(got["separated"]) == sorted([OPUS55, SONNET55])
        assert (f"; {SONNET5}, the walk's last rung, also builds for the fleet "
                "and reviews this one because nothing else was left") in got["note"]
        assert len(got["note"].splitlines()) == 1
        model, note = cli(tree, doc, role, "--built-on-unknown")
        assert model == SONNET5
        # Read off the card, the same document reviews an Opus 5.5 build on Sonnet 5.5.
        assert decision(tree, doc, role, built_on=OPUS55)["model"] == SONNET55
        assert cli(tree, doc, role, "--built-on", OPUS55)[0] == SONNET55


def test_the_unknown_case_reads_the_projects_build_walk(tmp_path):
    """A project whose Builders walk adds a model its reviewers lead with: the
    unknown case takes it off, which the fleet's build walk alone would not."""
    doc = with_project(with_project(HOME, "atlas", "workhorse", SONNET46),
                       "atlas", "advisory", SONNET46)
    tree = fixture_tree(tmp_path)
    on_atlas = decision(tree, doc, "critic", repo="atlas", built_on_unknown=True)
    assert SONNET46 in on_atlas["separated"]
    assert on_atlas["model"] == OPUS55
    assert "also builds for atlas" in on_atlas["note"]


# --------------------------------------------------------------------------- #
# What is still refused                                                        #
# --------------------------------------------------------------------------- #

def test_an_unpriced_exception_is_refused_in_plain_words():
    doc = with_project(HOME, "atlas", "workhorse", UNPRICED)
    assert refusals(doc) == [
        f"{UNPRICED} has no declared price, so it cannot be chosen."
    ]


def test_an_unpriced_model_leading_a_group_is_refused_with_the_same_sentence():
    for kind in ("workhorse", "advisory", "judgement"):
        assert refusals(lead_with(HOME, kind, UNPRICED)) == [
            f"{UNPRICED} has no declared price, so it cannot be chosen."
        ], kind


def test_an_unpriced_rung_is_refused_with_the_same_sentence():
    for kind in ("workhorse", "advisory", "judgement"):
        cfg = frozen()
        cfg["ladders"][ladder_name(cfg, kind)].append(
            {"model": UNPRICED, "reason": "priced nowhere"})
        assert errors(cfg) == [
            f"ladders.{ladder_name(cfg, kind)}: {UNPRICED} has no declared "
            "price, so it cannot be chosen."
        ], kind


def test_the_unpriced_sentence_carries_no_engineering_words():
    sentence = mf._unpriced(UNPRICED)
    assert sentence == f"{UNPRICED} has no declared price, so it cannot be chosen."
    for word in ("rule", "ladder", "rung", "kind", ".yaml", "config/"):
        assert word not in sentence


def test_an_excluded_model_leading_a_group_is_still_refused():
    found = refusals(lead_with(HOME, "workhorse", HAIKU55))
    assert found == [f"{HAIKU55} is excluded, so it cannot be chosen."], found


@pytest.mark.parametrize("edit, needle", [
    (lambda c: c["review_separation"]["rules"].append(
        {"built_on": OPUS5, "reviewers_use": SONNET55, "reason": "stale"}), "STALE"),
    (lambda c: c["review_separation"]["rules"][0].update(
        reviewers_use=c["review_separation"]["rules"][0]["built_on"]),
     "sends the reviewer back"),
    (lambda c: c["review_separation"]["rules"][0].update(reviewers_use=SONNET46),
     "is not on the advisory ladder"),
], ids=["stale", "back-to-build", "off-advisory"])
def test_a_malformed_declared_rule_is_still_refused(edit, needle):
    cfg = frozen()
    edit(cfg)
    found = errors(cfg)
    assert any(needle in e for e in found), found


def test_an_added_kind_on_the_builders_ladder_is_refused_in_rule_1s_words():
    """DRE-6834, in the place of the pin that any fourth kind is refused: an
    added group is admitted on a ladder of its own name, and one that would
    walk the Builders' ladder is refused, as sharing a ladder always was."""
    cfg = frozen()
    cfg["kinds"]["designers"] = {"ladder": ladder_name(cfg, "workhorse")}
    found = errors(cfg)
    assert found and all(e.startswith("kinds") for e in found), found
    assert any(e.startswith("kinds.designers: ") for e in found), found
    cfg["kinds"]["designers"] = {"ladder": "designers"}
    cfg["ladders"]["designers"] = [{"model": ladder(cfg, "workhorse")[0], "reason": "own"}]
    assert errors(cfg) == []


def test_todays_config_still_passes_with_no_rung_moved():
    assert errors(committed(), mf.declared_prices()) == []
    assert mf.policy_errors(committed()) == []


# --------------------------------------------------------------------------- #
# Retired ids are never named                                                  #
# --------------------------------------------------------------------------- #

def test_a_retired_id_on_a_ladder_is_refused_in_the_ladder_form():
    cfg = frozen()
    retired = cfg["retired"][0]["model"]
    cfg["ladders"][ladder_name(cfg, "judgement")].append(
        {"model": retired, "reason": "still here"})
    found = [e for e in errors(cfg, {**prices(), retired: {"input": 1.0, "output": 2.0}})
             if e.startswith("retired:")]
    assert found == [
        f"retired: {retired} is retired and is still named by ladders.judgement — "
        "a retired model is never selectable, so nothing was written. Remove it "
        "from that ladder, or do not retire it."
    ]


def test_todays_config_names_no_retired_id():
    assert not [e for e in errors(committed(), mf.declared_prices())
                if e.startswith("retired:")]


# --------------------------------------------------------------------------- #
# The adoption's write gate, on a non-rung exception (DRE-6633 unchanged)      #
# --------------------------------------------------------------------------- #

def _adopt(candidate: str, replaces: str, *ladders: str) -> dict:
    """A Decision of rule `adopt` replacing `replaces` on each of `ladders`, in
    the record shape `model_adoption._decision` builds."""
    entry = {"id": candidate, "display_name": None}
    price = {"input": 1.0, "output": 5.0}
    return json.loads(json.dumps(ma._decision(
        entry, ma.family(candidate), None, ma.RULE_ADOPT,
        [{"ladder": name, "model": replaces, "created_at": None, "price": price}
         for name in ladders],
        price, "a same-family successor at no higher price")))


def _config_text(projects: dict) -> str:
    """The frozen models.yaml TEXT with its `projects:` block replaced —
    `apply_text` edits text, never a re-dump."""
    lines = model_fixture.fixture_text().splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("projects:"))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() and not lines[i][0].isspace())
    block = ["projects:\n"] + [
        line for slug, entry in projects.items()
        for line in [f"  {slug}:\n"] + [f"    {k}: {m}\n" for k, m in entry.items()]
    ]
    return "".join(lines[:start] + block + ["\n"] + lines[end:])


def test_an_adoption_never_strands_a_non_rung_exception():
    candidate = successor(SONNET46)
    text = _config_text({"atlas": {"workhorse": SONNET46}})
    assert errors(yaml.safe_load(text)) == []
    result = maa.apply_text(text, _adopt(candidate, SONNET46, "judgement"),
                            "2026-10-11")
    after = yaml.safe_load(result)
    assert after["projects"]["atlas"]["workhorse"] == SONNET46
    assert ladder(after, "judgement")[-1] == candidate
    assert SONNET46 in [r["model"] for r in after["retired"]]
    with_candidate = {**prices(), candidate: {"input": 1.0, "output": 5.0}}
    assert errors(after, with_candidate) == [
        f"retired: {SONNET46} is retired and is still named by "
        "projects.atlas.workhorse — a retired model is never selectable, so "
        "nothing was written. The operator changes or clears that choice on "
        f"Settings › Agents & models, and the next daily run retires {SONNET46}."
    ]


def test_an_adoption_still_moves_an_exception_on_its_own_kinds_rung():
    candidate = successor(SONNET55)
    text = _config_text({"atlas": {"workhorse": SONNET55}})
    # Sonnet 5.5 is a rung of both the Builders' and the Reviewers' ladder,
    # so a same-family successor replaces it on both, as the classifier says.
    result = maa.apply_text(
        text, _adopt(candidate, SONNET55, "workhorse", "advisory"), "2026-10-11")
    after = yaml.safe_load(result)
    assert after["projects"]["atlas"]["workhorse"] == candidate
    # The exception moved with its rung, so nothing refuses it and nothing is
    # left naming a retired id. (The `effort` entry an adoption leaves behind
    # is the adoption's own concern, and untouched by this card.)
    found = errors(after, {**prices(), candidate: {"input": 2.0, "output": 10.0}})
    assert not [e for e in found if "projects." in e or e.startswith("retired:")], found


# --------------------------------------------------------------------------- #
# A model only a project names is still a known model                          #
# --------------------------------------------------------------------------- #

def _haiku_for_atlas() -> tuple[dict, dict]:
    """atlas's builders on the model the frozen file sets aside, the set-aside
    lifted: the document, and a copy of the frozen `models.yaml` with no such
    entry under `excluded`."""
    raw = frozen()
    raw["excluded"] = [e for e in raw["excluded"] if e["model"] != HAIKU55]
    return with_project(HOME, "atlas", "workhorse", HAIKU55), raw


def test_a_model_only_a_project_names_validates():
    doc, raw = _haiku_for_atlas()
    assert not any(HAIKU55 in group["models"] for group in doc["groups"].values())
    assert refusals(doc, code_side(mf._normalize_config(raw)["excluded"])) == []
    # Set aside, it is refused as it always was.
    assert refusals(doc) == [f"{HAIKU55} is excluded, so it cannot be chosen."]


def test_the_adoption_never_asks_about_a_model_a_project_chose():
    cfg = as_config(*_haiku_for_atlas())
    assert HAIKU55 in ma._known_ids(cfg)
    catalog = [
        {"id": HAIKU55, "display_name": "Claude Haiku 5.5",
         "created_at": "2026-10-01T00:00:00Z"},
        {"id": f"{HAIKU55}-20261001", "display_name": "Claude Haiku 5.5",
         "created_at": "2026-10-01T00:00:00Z"},
    ]
    found = ma.classify_catalog(catalog, cfg, ma.load_prices())
    assert not [d for d in found if d["candidate"].startswith(HAIKU55)], found


def test_a_build_on_a_model_only_a_project_names_is_read_off_its_marker(tmp_path):
    """The Planners give up their last model and atlas's builders take it, so
    it is in no group of the document: a build on it is still read off its
    marker, and atlas's critic reviews it on the Reviewers' top. (A process
    that read a home knows the models the defaults in the code name; one that
    neither they nor the code-side facts name is DRE-6992's.)"""
    doc = copy.deepcopy(HOME)
    doc["groups"]["judgement"]["models"].remove(SONNET46)
    doc = with_project(doc, "atlas", "workhorse", SONNET46)
    assert not any(SONNET46 in group["models"] for group in doc["groups"].values())
    assert refusals(doc) == []
    tree = fixture_tree(tmp_path)
    got = py(tree, doc, (
        f"m = {SONNET46!r}\n"
        "print(json.dumps([m in mf.KNOWN_MODELS, "
        "mf.last_attempt_model([mf.attempt_marker(m)]), "
        "mf.select('critic', repo='atlas', built_on=m, probe=probe), "
        "mf.select_with_reasons('critic', repo='atlas', built_on=m, "
        "probe=probe)['built_on_unknown'], mf.ladder_for('engineer', 'atlas')[0]]))"
    ))
    assert got == [True, SONNET46, ladder(doc, "advisory")[0], False, SONNET46]


def test_todays_known_sets_carry_every_project_id():
    assert mf._project_ids(mf.CONFIG) <= mf.KNOWN_MODELS
    assert mf._project_ids(committed()) <= ma._known_ids(committed())


# --------------------------------------------------------------------------- #
# Nothing automatic changes                                                    #
# --------------------------------------------------------------------------- #

def test_the_automatic_paths_never_climb():
    everything_up = {m: True for m in mf.declared_prices()}
    for role in mf.AGENT_KINDS:
        mf.clear_availability_cache()
        got = mf.select(role, probe=lambda m: everything_up.get(m, True))
        assert got in mf.ladder_for(role), role
        assert got == mf.ladder_for(role)[0], role


def test_discover_writes_no_file(tmp_path):
    tree = fixture_tree(tmp_path)

    def digest() -> dict:
        return {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(tree.rglob("*")) if p.is_file()
                and "__pycache__" not in p.parts}

    before = digest()
    subprocess.run([sys.executable, str(tree / "scripts" / "model_fallback.py"),
                    "discover"], capture_output=True, text=True, env=env(tree, None), cwd=tree)
    assert digest() == before


def test_a_new_family_is_never_adopted():
    candidate = unseen("corvid", 1)
    catalog = [{"id": candidate, "display_name": "Claude Corvid 1",
                "created_at": "2026-11-10T00:00:00Z"}]
    cheap = {**ma.load_prices(), candidate: {"input": 0.1, "output": 0.5}}
    for cfg in (frozen(), as_config(lead_with(HOME, "workhorse", FABLE51)),
                as_config(*_haiku_for_atlas())):
        found = ma.classify_catalog(catalog, copy.deepcopy(cfg), cheap)
        assert found and all(d["rule"] != ma.RULE_ADOPT for d in found), found
