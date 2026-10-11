"""DRE-6785 — the model policy cautions instead of refusing: any priced model may
lead a group or a project, and review separation is derived, never refused.

THE DECISION (the CEO, 2026-10-10 17:54 PT, looking at the Planners' dropdown
with five of seven models grayed out): "It shouldn't gray out these models. I
should be able to set it to anything I want. Fix this."

So an explicit choice of any model with a declared price is admissible for any
group and for any project, and the promise that a pull request is never
reviewed by the model that built it is kept where the reviewer's model is
selected — on the walk that project's reviewers really make — rather than by
refusing the config. Nothing automatic changes: the probe only walks down,
discovery adopts nothing onto a build or planning ladder, an adoption never
strands a project's choice, and a model a project names stays a known model.

THE TREE
--------
Every scenario is an edit of the FROZEN `models.yaml`, judged against the frozen
`model-prices.yaml` (`tests/model_fixture.py`), in a tree of today's `scripts/`
on those files (`model_fixture.fixture_tree`) — the pattern
tests/test_project_model_exceptions.py uses. `model_fallback` reads its config
off its own path, so the tree is the only way to drive the real selector and
CLI on a config the repo does not ship. The probe is stubbed
(`BUREAU_FAKE_AVAILABLE`, or a lambda in-process): no network.

WHAT IS NEVER READ OFF THE COMMITTED FILE (DRE-6935)
----------------------------------------------------
The console rewrites `config/models.yaml` on every save, and this file first
tested its rules against that file as it stood on 2026-10-10: every one of the
fifteen default changes the page offered turned it red. So every model id a
scenario names is read off the fixture's ladders by the part it plays —
`BUILD_TOP` leads the Builders and is the Reviewers' last rung, and so on —
and `test_the_fixture_holds_the_scenario` says what each must be. Only the
tests named `test_todays_*`, and the one that never climbs, read the committed
file, and each checks what must hold for any state the console can save.

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

CONFIG_PATH = ROOT / "config" / "models.yaml"
REPO_MAP_PATH = ROOT / "config" / "repo-map.json"

REVIEWERS = ("critic", "verifier")

# --------------------------------------------------------------------------- #
# The models every scenario names, each read off the frozen ladders by the     #
# part it plays (`test_the_fixture_holds_the_scenario` says what that is)      #
# --------------------------------------------------------------------------- #

_FX = model_fixture.fixture_config()
BUILD = model_fixture.ladder(_FX, "workhorse")
REVIEW = model_fixture.ladder(_FX, "advisory")
PLAN = model_fixture.ladder(_FX, "judgement")
_RULES = {r["built_on"]: r["reviewers_use"] for r in _FX["review_separation"]["rules"]}

#: Leads the Builders, and is the Reviewers' last rung.
BUILD_TOP = BUILD[0]
#: A Builders rung on no Reviewers rung.
BUILD_ONLY = next(m for m in BUILD if m not in REVIEW)
#: Leads the Reviewers, and is a Builders rung with a declared rule.
REVIEW_TOP = REVIEW[0]
#: The Reviewers' second rung.
REVIEW_NEXT = REVIEW[1]
#: Leads the Planners, on no Builders rung.
PLAN_TOP = PLAN[0]
#: The Planners' last rung, on neither the Builders' nor the Reviewers' ladder.
PLAN_LAST = PLAN[-1]
#: The frozen prices, for the adoption's reader.
FROZEN_PRICES = model_fixture.FIXTURE_DIR / "model-prices.yaml"
#: An excluded model with a declared price.
EXCLUDED = next(e["model"] for e in _FX["excluded"]
                if e["model"] in model_fixture.fixture_prices())


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def committed() -> dict:
    """The committed file, for what must hold in any state the console saves."""
    return yaml.safe_load(CONFIG_PATH.read_text())


def frozen() -> dict:
    """The frozen `models.yaml`, parsed afresh — a test may edit its copy."""
    return model_fixture.fixture_config()


def prices() -> dict:
    return model_fixture.fixture_prices()


def slugs() -> set[str]:
    return set(json.loads(REPO_MAP_PATH.read_text()))


def errors(cfg: dict, with_prices: dict | None = None) -> list[str]:
    return mf.policy_errors(cfg, prices() if with_prices is None else with_prices,
                            repo_slugs=slugs())


def ladder_name(cfg: dict, kind: str) -> str:
    return cfg["kinds"][kind]["ladder"]


def ladder(cfg: dict, kind: str) -> list[str]:
    return [r["model"] for r in cfg["ladders"][ladder_name(cfg, kind)]]


def lead_with(cfg: dict, kind: str, model: str) -> dict:
    """`cfg` with `model` moved (or added) to the top of `kind`'s ladder."""
    name = ladder_name(cfg, kind)
    rungs = [r for r in cfg["ladders"][name] if r["model"] != model]
    cfg["ladders"][name] = [{"model": model, "reason": "the CEO's choice"}] + rungs
    return cfg


def with_project(cfg: dict, slug: str, entry: dict) -> dict:
    projects = dict(cfg.get("projects") or {})
    projects[slug] = dict(entry)
    cfg["projects"] = projects
    return cfg


def make_tree(base: Path, cfg: dict) -> Path:
    """Today's scripts on the frozen files, `cfg` as its models.yaml."""
    model_fixture.fixture_tree(base / "tree")
    (base / "tree" / "config" / "models.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False))
    return base / "tree"


def env(available: dict | None = None) -> dict:
    out = dict(os.environ)
    out["BUREAU_FAKE_AVAILABLE"] = json.dumps(available or {})
    return out


def cli(tree: Path, role: str, *args: str, available: dict | None = None):
    """`(model, note)` from the real CLI in `tree`."""
    note = tree / "note.txt"
    if note.exists():
        note.unlink()
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select",
         role, *args, "--explain-file", str(note)],
        capture_output=True, text=True, env=env(available), cwd=tree,
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return proc.stdout.strip(), note.read_text().strip()


def py(tree: Path, code: str):
    """Run `code` against `tree`'s model_fallback; it prints one JSON value."""
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


def decision(tree: Path, role: str, **kwargs) -> dict:
    """`select_with_reasons(role, **kwargs)` in `tree`, plus its note. An
    `unavailable` list is the models the probe reports down."""
    down = kwargs.pop("unavailable", [])
    return py(tree, (
        f"down = {down!r}\n"
        f"d = mf.select_with_reasons({role!r}, probe=lambda m: m not in down, "
        f"**{kwargs!r})\n"
        "d['note'] = mf.selection_note(d)\n"
        "print(json.dumps(d))"
    ))


# --------------------------------------------------------------------------- #
# Four choices, each admitted and each leading its walk                        #
# --------------------------------------------------------------------------- #

def test_the_fixture_holds_the_scenario():
    """Each name above plays the part its comment says, or every scenario
    below would pass for the wrong reason."""
    assert BUILD_TOP in REVIEW and BUILD_TOP != REVIEW_TOP
    assert BUILD_TOP not in _RULES
    assert BUILD_ONLY not in REVIEW
    assert REVIEW_TOP in BUILD and REVIEW_TOP in _RULES
    assert set(REVIEW) <= set(BUILD), "every Reviewers rung also builds"
    assert PLAN_TOP not in BUILD
    assert PLAN_LAST not in BUILD + REVIEW and PLAN_LAST != PLAN_TOP
    assert EXCLUDED not in BUILD + REVIEW + PLAN
    assert len({BUILD_TOP, BUILD_ONLY, REVIEW_TOP, REVIEW_NEXT, PLAN_TOP,
                PLAN_LAST, EXCLUDED}) == 7
    assert errors(frozen()) == []


CHOICES = {
    "planners-build-top": (lambda: lead_with(frozen(), "judgement", BUILD_TOP),
                           "planner", None, BUILD_TOP),
    "builders-planners-top": (lambda: lead_with(frozen(), "workhorse", PLAN_TOP),
                              "engineer", None, PLAN_TOP),
    "reviewers-build-top": (lambda: lead_with(frozen(), "advisory", BUILD_TOP),
                            "critic", None, BUILD_TOP),
    "atlas-builders-off-ladder": (
        lambda: with_project(frozen(), "atlas", {"workhorse": PLAN_LAST}),
        "engineer", "atlas", PLAN_LAST),
}


@pytest.mark.parametrize("name", list(CHOICES))
def test_a_choice_is_admitted_and_leads_its_walk(name, tmp_path):
    build, role, repo, chosen = CHOICES[name]
    cfg = build()
    assert errors(cfg) == []
    tree = make_tree(tmp_path, cfg)
    repo_args = ("--repo", repo) if repo else ()
    model, note = cli(tree, role, *repo_args)
    assert model == chosen
    assert not note.startswith("DEGRADED"), note
    assert py(tree, f"print(json.dumps(mf.select({role!r}, repo={repo!r}, "
                    "probe=probe)))") == chosen


def test_the_reviewers_choice_needs_no_declared_rule():
    cfg = lead_with(frozen(), "advisory", BUILD_TOP)
    rules = {r["built_on"] for r in cfg["review_separation"]["rules"]}
    assert BUILD_TOP not in rules
    assert BUILD_TOP in ladder(cfg, "workhorse")
    assert errors(cfg) == []


def test_a_project_exception_off_its_ladder_walks_it_first_then_the_ladder(tmp_path):
    cfg = with_project(frozen(), "atlas", {"workhorse": PLAN_LAST})
    assert PLAN_LAST not in ladder(cfg, "workhorse")
    want = [PLAN_LAST, *BUILD]
    assert mf.effective_ladders(cfg, "atlas")["workhorse"] == want
    tree = make_tree(tmp_path, cfg)
    got = py(tree, "print(json.dumps([mf.ladder_for('engineer', 'atlas'), "
                   f"mf.fallback_for('engineer', {PLAN_LAST!r}, 'atlas'), "
                   "mf.select_with_reasons('engineer', repo='atlas', "
                   "probe=probe)['exception']]))")
    assert got == [want, BUILD_TOP, PLAN_LAST]


# --------------------------------------------------------------------------- #
# Four separations, derived with no declared rule                              #
# --------------------------------------------------------------------------- #

SEPARATIONS = {
    # (a) the fleet's Reviewers lead with the fleet's build model.
    "a-fleet-reviewers-build-top": (
        lambda: lead_with(frozen(), "advisory", BUILD_TOP), None, BUILD_TOP,
        REVIEW_TOP),
    # (b) atlas builds on the model the fleet's reviewers lead with, which the
    # declared rule sends to its own substitute.
    "b-atlas-builds-review-top": (
        lambda: with_project(frozen(), "atlas", {"workhorse": REVIEW_TOP}),
        "atlas", REVIEW_TOP, _RULES[REVIEW_TOP]),
    # (c) atlas's reviewers lead with the model atlas builds on.
    "c-atlas-reviewers-build-top": (
        lambda: with_project(frozen(), "atlas", {"advisory": BUILD_TOP}),
        "atlas", BUILD_TOP, REVIEW_TOP),
    # (d) atlas builds AND reviews on the same off-ladder model.
    "d-atlas-both-off-ladder": (
        lambda: with_project(frozen(), "atlas",
                             {"workhorse": PLAN_LAST, "advisory": PLAN_LAST}),
        "atlas", PLAN_LAST, REVIEW_TOP),
}


@pytest.mark.parametrize("name", list(SEPARATIONS))
def test_a_reviewer_is_separated_from_the_build_model(name, tmp_path):
    build, repo, built_on, reviewed_on = SEPARATIONS[name]
    cfg = build()
    assert errors(cfg) == []
    assert mf.reviewers_for(cfg, built_on, repo)[0] == reviewed_on
    assert built_on not in mf.reviewers_for(cfg, built_on, repo)
    tree = make_tree(tmp_path, cfg)
    repo_args = ("--repo", repo) if repo else ()
    for role in REVIEWERS:
        model, note = cli(tree, role, *repo_args, "--built-on", built_on)
        assert model == reviewed_on, (role, note)
        assert f"this build ran on {built_on}, so the reviewer does not" in note
        got = decision(tree, role, repo=repo, built_on=built_on)
        assert got["model"] == reviewed_on, role
        assert got["separated"] == [built_on]
        assert not got["degraded"]
        assert built_on in got["note"]


def test_reviewers_for_reads_the_raw_and_the_normalized_shape():
    # Only atlas carries an entry, and portico must be a project with none.
    cfg = frozen()
    cfg["projects"] = {"atlas": {"advisory": BUILD_TOP}}
    norm = mf._normalize_config(cfg)
    # atlas walks its choice, then the fleet's Reviewers, less the build model.
    want = [m for m in REVIEW if m != BUILD_TOP]
    assert len(want) == 2
    assert mf.reviewers_for(cfg, BUILD_TOP, "atlas") == \
        mf.reviewers_for(norm, BUILD_TOP, "atlas") == want
    # No slug, or one with no entry, is the fleet's walk.
    fleet = ladder(cfg, "advisory")
    assert mf.reviewers_for(cfg, None) == fleet
    assert mf.reviewers_for(cfg, BUILD_ONLY, "portico") == fleet
    # A build on a model with a declared rule leads with the rule's substitute.
    substitute = _RULES[REVIEW_TOP]
    assert mf.reviewers_for(cfg, REVIEW_TOP, "nowhere") == [
        substitute, *[m for m in fleet if m not in (REVIEW_TOP, substitute)]]


# --------------------------------------------------------------------------- #
# The unknown build model, on the frozen file and with the Reviewers led by    #
# the build model                                                              #
# --------------------------------------------------------------------------- #

def test_an_unknown_build_lands_on_the_reviewers_last_rung_on_the_frozen_config(tmp_path):
    # Every Reviewers rung also builds, so none is left, and the walk keeps
    # its last.
    tree = make_tree(tmp_path, frozen())
    for role in REVIEWERS:
        got = decision(tree, role, built_on_unknown=True)
        assert got["model"] == REVIEW[-1], role
        assert cli(tree, role, "--built-on-unknown")[0] == REVIEW[-1], role


def test_the_unknown_case_on_reviewers_led_by_the_build_top_keeps_the_last_rung_and_says_so(
        tmp_path):
    cfg = lead_with(frozen(), "advisory", BUILD_TOP)
    walk = ladder(cfg, "advisory")
    assert walk == [BUILD_TOP, *[m for m in REVIEW if m != BUILD_TOP]]
    last = walk[-1]
    tree = make_tree(tmp_path, cfg)
    for role in REVIEWERS:
        got = decision(tree, role, built_on_unknown=True)
        assert got["model"] == last, role
        assert sorted(got["separated"]) == sorted(walk[:-1])
        assert (f"; {last}, the walk's last rung, also builds for the fleet "
                "and reviews this one because nothing else was left") in got["note"]
        assert len(got["note"].splitlines()) == 1
        model, note = cli(tree, role, "--built-on-unknown")
        assert model == last
        # The same file reviews a build on its top rung on the rung below it.
        assert decision(tree, role, built_on=BUILD_TOP)["model"] == walk[1]
        assert cli(tree, role, "--built-on", BUILD_TOP)[0] == walk[1]


def test_the_unknown_case_reads_the_projects_build_walk(tmp_path):
    """A project whose Builders walk adds a model its reviewers lead with: the
    unknown case takes it off, which the fleet's build walk alone would not."""
    cfg = with_project(frozen(), "atlas",
                       {"workhorse": PLAN_LAST, "advisory": PLAN_LAST})
    tree = make_tree(tmp_path, cfg)
    on_atlas = decision(tree, "critic", repo="atlas", built_on_unknown=True)
    assert PLAN_LAST in on_atlas["separated"]
    assert on_atlas["model"] == REVIEW[-1]
    assert "also builds for atlas" in on_atlas["note"]


# --------------------------------------------------------------------------- #
# What is still refused                                                        #
# --------------------------------------------------------------------------- #

def test_an_unpriced_exception_is_refused_in_plain_words():
    cfg = with_project(frozen(), "atlas", {"workhorse": "claude-nova-9"})
    assert errors(cfg) == [
        "projects.atlas.workhorse: claude-nova-9 has no declared price, so it "
        "cannot be chosen."
    ]


def test_an_unpriced_rung_is_refused_with_the_same_sentence():
    for kind in ("workhorse", "advisory", "judgement"):
        cfg = frozen()
        cfg["ladders"][ladder_name(cfg, kind)].append(
            {"model": "claude-nova-9", "reason": "priced nowhere"})
        assert errors(cfg) == [
            f"ladders.{ladder_name(cfg, kind)}: claude-nova-9 has no declared "
            "price, so it cannot be chosen."
        ], kind


def test_the_unpriced_sentence_carries_no_engineering_words():
    sentence = mf._unpriced("claude-nova-9")
    assert sentence == "claude-nova-9 has no declared price, so it cannot be chosen."
    for word in ("rule", "ladder", "rung", "kind", ".yaml", "config/"):
        assert word not in sentence


def test_an_excluded_model_on_a_ladder_is_still_refused():
    cfg = lead_with(frozen(), "workhorse", EXCLUDED)
    found = errors(cfg)
    assert any(e.startswith(f"ladders.workhorse: {EXCLUDED} is excluded")
               for e in found), found


@pytest.mark.parametrize("edit, needle", [
    (lambda c: c["review_separation"]["rules"].append(
        {"built_on": BUILD_ONLY, "reviewers_use": REVIEW_TOP, "reason": "stale"}),
     "STALE"),
    (lambda c: c["review_separation"]["rules"][0].update(
        reviewers_use=c["review_separation"]["rules"][0]["built_on"]),
     "sends the reviewer back"),
    (lambda c: c["review_separation"]["rules"][0].update(reviewers_use=PLAN_LAST),
     "is not on the advisory ladder"),
], ids=["stale", "back-to-build", "off-advisory"])
def test_a_malformed_declared_rule_is_still_refused(edit, needle):
    cfg = frozen()
    edit(cfg)
    found = errors(cfg)
    assert any(needle in e for e in found), found


def test_a_fourth_kind_is_still_refused_in_rule_1s_words():
    cfg = frozen()
    cfg["kinds"]["designers"] = {"ladder": ladder_name(cfg, "workhorse")}
    found = errors(cfg)
    assert len(found) == 1 and found[0].startswith("kinds: must declare exactly ")


def test_todays_config_still_passes_with_no_rung_moved():
    assert mf.policy_errors(committed(), mf.declared_prices(),
                            repo_slugs=slugs()) == []
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
    found = mf.policy_errors(committed(), mf.declared_prices(), repo_slugs=slugs())
    assert not [e for e in found if e.startswith("retired:")]


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


def _successor(model: str) -> str:
    """An id the catalog could publish next in `model`'s family."""
    return f"claude-{ma.family(model)}-99"


def test_an_adoption_never_strands_a_non_rung_exception():
    # The Planners' last rung, which no Builders rung is: atlas names it as a
    # Builders choice off that group's ladder, and its successor replaces it
    # on the Planners' ladder only.
    candidate = _successor(PLAN_LAST)
    text = _config_text({"atlas": {"workhorse": PLAN_LAST}})
    assert errors(yaml.safe_load(text)) == []
    result = maa.apply_text(text, _adopt(candidate, PLAN_LAST, "judgement"),
                            "2026-10-11")
    after = yaml.safe_load(result)
    assert after["projects"]["atlas"]["workhorse"] == PLAN_LAST
    assert ladder(after, "judgement")[-1] == candidate
    assert PLAN_LAST in [r["model"] for r in after["retired"]]
    with_candidate = {**prices(), candidate: {"input": 1.0, "output": 5.0}}
    assert errors(after, with_candidate) == [
        f"retired: {PLAN_LAST} is retired and is still named by "
        "projects.atlas.workhorse — a retired model is never selectable, so "
        "nothing was written. The operator changes or clears that choice on "
        "Settings › Agents & models, and the next daily run retires "
        f"{PLAN_LAST}."
    ]


def test_an_adoption_still_moves_an_exception_on_its_own_kinds_rung():
    candidate = _successor(REVIEW_TOP)
    text = _config_text({"atlas": {"workhorse": REVIEW_TOP}})
    # The Reviewers' top is a rung of both the Builders' and the Reviewers'
    # ladder, so a same-family successor replaces it on both, as the
    # classifier says.
    result = maa.apply_text(
        text, _adopt(candidate, REVIEW_TOP, "workhorse", "advisory"), "2026-10-11")
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

def _excluded_for_atlas() -> dict:
    cfg = frozen()
    cfg["excluded"] = [e for e in cfg["excluded"] if e["model"] != EXCLUDED]
    return with_project(cfg, "atlas", {"workhorse": EXCLUDED})


def test_a_model_only_a_project_names_validates():
    cfg = _excluded_for_atlas()
    assert not any(EXCLUDED in ids for ids in mf._normalize_config(cfg)["ladders"].values())
    assert errors(cfg) == []


def test_the_adoption_never_asks_about_a_model_a_project_chose():
    cfg = _excluded_for_atlas()
    assert EXCLUDED in ma._known_ids(cfg)
    catalog = [
        {"id": EXCLUDED, "display_name": "The excluded model",
         "created_at": "2026-10-01T00:00:00Z"},
        {"id": f"{EXCLUDED}-20261001", "display_name": "The excluded model",
         "created_at": "2026-10-01T00:00:00Z"},
    ]
    found = ma.classify_catalog(catalog, cfg, ma.load_prices(FROZEN_PRICES))
    assert not [d for d in found if d["candidate"].startswith(EXCLUDED)], found


def test_a_build_on_a_model_only_a_project_names_is_read_off_its_marker(tmp_path):
    tree = make_tree(tmp_path, _excluded_for_atlas())
    got = py(tree, (
        f"m = {EXCLUDED!r}\n"
        "print(json.dumps([m in mf.KNOWN_MODELS, "
        "mf.last_attempt_model([mf.attempt_marker(m)]), "
        "mf.select('critic', repo='atlas', built_on=m, probe=probe), "
        "mf.select_with_reasons('critic', repo='atlas', built_on=m, "
        "probe=probe)['built_on_unknown']]))"
    ))
    assert got == [True, EXCLUDED, ladder(_excluded_for_atlas(), "advisory")[0], False]


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
    tree = make_tree(tmp_path, frozen())

    def digest() -> dict:
        return {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(tree.rglob("*")) if p.is_file()
                and "__pycache__" not in p.parts}

    before = digest()
    subprocess.run([sys.executable, str(tree / "scripts" / "model_fallback.py"),
                    "discover"], capture_output=True, text=True, env=env(), cwd=tree)
    assert digest() == before


def test_a_new_family_is_never_adopted():
    catalog = [{"id": "claude-corvid-1", "display_name": "Claude Corvid 1",
                "created_at": "2026-11-10T00:00:00Z"}]
    cheap = {**ma.load_prices(FROZEN_PRICES),
             "claude-corvid-1": {"input": 0.1, "output": 0.5}}
    for cfg in (frozen(), lead_with(frozen(), "workhorse", PLAN_TOP),
                _excluded_for_atlas()):
        found = ma.classify_catalog(catalog, copy.deepcopy(cfg), cheap)
        assert found and all(d["rule"] != ma.RULE_ADOPT for d in found), found
