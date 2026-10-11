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
Every scenario is an edit of a COPY of today's `config/models.yaml`, judged
against today's `config/model-prices.yaml`, in a copied tree of `scripts/` and
`config/` — the pattern tests/test_project_model_exceptions.py uses.
`model_fallback` reads its config off its own path, so the copy is the only way
to drive the real selector and CLI on a config the repo does not ship. The
probe is stubbed (`BUREAU_FAKE_AVAILABLE`, or a lambda in-process): no network.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_choice_policy.py -q
"""

from __future__ import annotations

import copy
import hashlib
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

import model_adoption as ma  # noqa: E402
import model_adoption_actions as maa  # noqa: E402
import model_fallback as mf  # noqa: E402

CONFIG_PATH = ROOT / "config" / "models.yaml"
REPO_MAP_PATH = ROOT / "config" / "repo-map.json"

OPUS55 = "claude-opus-5-5"
OPUS5 = "claude-opus-5"
SONNET55 = "claude-sonnet-5-5"
SONNET5 = "claude-sonnet-5"
SONNET46 = "claude-sonnet-4-6"
FABLE51 = "claude-fable-5-1"
HAIKU55 = "claude-haiku-5-5"

REVIEWERS = ("critic", "verifier")


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def committed() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def prices() -> dict:
    return mf.declared_prices()


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
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "scripts", base / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "config", base / "config", ignore=ignore)
    (base / "config" / "models.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return base


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

CHOICES = {
    "planners-opus55": (lambda: lead_with(committed(), "judgement", OPUS55),
                        "planner", None, OPUS55),
    "builders-fable51": (lambda: lead_with(committed(), "workhorse", FABLE51),
                         "engineer", None, FABLE51),
    "reviewers-opus55": (lambda: lead_with(committed(), "advisory", OPUS55),
                         "critic", None, OPUS55),
    "atlas-builders-sonnet46": (
        lambda: with_project(committed(), "atlas", {"workhorse": SONNET46}),
        "engineer", "atlas", SONNET46),
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
    cfg = lead_with(committed(), "advisory", OPUS55)
    rules = {r["built_on"] for r in cfg["review_separation"]["rules"]}
    assert OPUS55 not in rules
    assert OPUS55 in ladder(cfg, "workhorse")
    assert errors(cfg) == []


def test_a_project_exception_off_its_ladder_walks_it_first_then_the_ladder(tmp_path):
    cfg = with_project(committed(), "atlas", {"workhorse": SONNET46})
    assert SONNET46 not in ladder(cfg, "workhorse")
    want = [SONNET46, OPUS55, OPUS5, SONNET55, SONNET5]
    assert mf.effective_ladders(cfg, "atlas")["workhorse"] == want
    tree = make_tree(tmp_path, cfg)
    got = py(tree, "print(json.dumps([mf.ladder_for('engineer', 'atlas'), "
                   "mf.fallback_for('engineer', 'claude-sonnet-4-6', 'atlas'), "
                   "mf.select_with_reasons('engineer', repo='atlas', "
                   "probe=probe)['exception']]))")
    assert got == [want, OPUS55, SONNET46]


# --------------------------------------------------------------------------- #
# Four separations, derived with no declared rule                              #
# --------------------------------------------------------------------------- #

SEPARATIONS = {
    # (a) the fleet's Reviewers lead with the fleet's build model.
    "a-fleet-reviewers-opus55": (
        lambda: lead_with(committed(), "advisory", OPUS55), None, OPUS55, SONNET55),
    # (b) atlas builds on the model the fleet's reviewers lead with.
    "b-atlas-builds-sonnet55": (
        lambda: with_project(committed(), "atlas", {"workhorse": SONNET55}),
        "atlas", SONNET55, OPUS55),
    # (c) atlas's reviewers lead with the model atlas builds on.
    "c-atlas-reviewers-opus55": (
        lambda: with_project(committed(), "atlas", {"advisory": OPUS55}),
        "atlas", OPUS55, SONNET55),
    # (d) atlas builds AND reviews on the same off-ladder model.
    "d-atlas-both-sonnet46": (
        lambda: with_project(committed(), "atlas",
                             {"workhorse": SONNET46, "advisory": SONNET46}),
        "atlas", SONNET46, SONNET55),
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
    cfg = with_project(committed(), "atlas", {"advisory": OPUS55})
    norm = mf._normalize_config(cfg)
    assert mf.reviewers_for(cfg, OPUS55, "atlas") == \
        mf.reviewers_for(norm, OPUS55, "atlas") == [SONNET55, SONNET5]
    # No slug, or one with no entry, is the fleet's walk.
    fleet = ladder(cfg, "advisory")
    assert mf.reviewers_for(cfg, None) == fleet
    assert mf.reviewers_for(cfg, OPUS5, "portico") == fleet
    assert mf.reviewers_for(cfg, SONNET55, "nowhere") == [OPUS55, SONNET5]


# --------------------------------------------------------------------------- #
# The unknown build model, on today's file and on the Reviewers → Opus 5.5 one  #
# --------------------------------------------------------------------------- #

def test_an_unknown_build_still_lands_on_opus55_on_todays_config():
    for role in REVIEWERS:
        mf.clear_availability_cache()
        assert mf.select(role, probe=lambda m: True, built_on_unknown=True) == OPUS55


def test_the_unknown_case_on_reviewers_opus55_keeps_the_last_rung_and_says_so(tmp_path):
    cfg = lead_with(committed(), "advisory", OPUS55)
    assert ladder(cfg, "advisory") == [OPUS55, SONNET55, SONNET5]
    tree = make_tree(tmp_path, cfg)
    for role in REVIEWERS:
        got = decision(tree, role, built_on_unknown=True)
        assert got["model"] == SONNET5, role
        assert sorted(got["separated"]) == sorted([OPUS55, SONNET55])
        assert (f"; {SONNET5}, the walk's last rung, also builds for the fleet "
                "and reviews this one because nothing else was left") in got["note"]
        assert len(got["note"].splitlines()) == 1
        model, note = cli(tree, role, "--built-on-unknown")
        assert model == SONNET5
        # Read off the card, the same file reviews an Opus 5.5 build on Sonnet 5.5.
        assert decision(tree, role, built_on=OPUS55)["model"] == SONNET55
        assert cli(tree, role, "--built-on", OPUS55)[0] == SONNET55


def test_the_unknown_case_reads_the_projects_build_walk(tmp_path):
    """A project whose Builders walk adds a model its reviewers lead with: the
    unknown case takes it off, which the fleet's build walk alone would not."""
    cfg = with_project(committed(), "atlas",
                       {"workhorse": SONNET46, "advisory": SONNET46})
    tree = make_tree(tmp_path, cfg)
    on_atlas = decision(tree, "critic", repo="atlas", built_on_unknown=True)
    assert SONNET46 in on_atlas["separated"]
    assert on_atlas["model"] == OPUS55
    assert "also builds for atlas" in on_atlas["note"]


# --------------------------------------------------------------------------- #
# What is still refused                                                        #
# --------------------------------------------------------------------------- #

def test_an_unpriced_exception_is_refused_in_plain_words():
    cfg = with_project(committed(), "atlas", {"workhorse": "claude-nova-9"})
    assert errors(cfg) == [
        "projects.atlas.workhorse: claude-nova-9 has no declared price, so it "
        "cannot be chosen."
    ]


def test_an_unpriced_rung_is_refused_with_the_same_sentence():
    for kind in ("workhorse", "advisory", "judgement"):
        cfg = committed()
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
    cfg = lead_with(committed(), "workhorse", HAIKU55)
    found = errors(cfg)
    assert any(e.startswith(f"ladders.workhorse: {HAIKU55} is excluded")
               for e in found), found


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
    cfg = committed()
    edit(cfg)
    found = errors(cfg)
    assert any(needle in e for e in found), found


def test_a_fourth_kind_is_still_refused_in_rule_1s_words():
    cfg = committed()
    cfg["kinds"]["designers"] = {"ladder": ladder_name(cfg, "workhorse")}
    found = errors(cfg)
    assert len(found) == 1 and found[0].startswith("kinds: must declare exactly ")


def test_todays_config_still_passes_with_no_rung_moved():
    assert errors(committed()) == []
    assert mf.policy_errors(committed()) == []


# --------------------------------------------------------------------------- #
# Retired ids are never named                                                  #
# --------------------------------------------------------------------------- #

def test_a_retired_id_on_a_ladder_is_refused_in_the_ladder_form():
    cfg = committed()
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
    assert not [e for e in errors(committed()) if e.startswith("retired:")]


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
    """Today's config/models.yaml TEXT with its `projects:` block replaced —
    `apply_text` edits text, never a re-dump."""
    lines = CONFIG_PATH.read_text().splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("projects:"))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() and not lines[i][0].isspace())
    block = ["projects:\n"] + [
        line for slug, entry in projects.items()
        for line in [f"  {slug}:\n"] + [f"    {k}: {m}\n" for k, m in entry.items()]
    ]
    return "".join(lines[:start] + block + ["\n"] + lines[end:])


def test_an_adoption_never_strands_a_non_rung_exception():
    candidate = "claude-sonnet-4-7"
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
        "retired: claude-sonnet-4-6 is retired and is still named by "
        "projects.atlas.workhorse — a retired model is never selectable, so "
        "nothing was written. The operator changes or clears that choice on "
        "Settings › Agents & models, and the next daily run retires "
        "claude-sonnet-4-6."
    ]


def test_an_adoption_still_moves_an_exception_on_its_own_kinds_rung():
    candidate = "claude-sonnet-5-6"
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

def _haiku_for_atlas() -> dict:
    cfg = committed()
    cfg["excluded"] = [e for e in cfg["excluded"] if e["model"] != HAIKU55]
    return with_project(cfg, "atlas", {"workhorse": HAIKU55})


def test_a_model_only_a_project_names_validates():
    cfg = _haiku_for_atlas()
    assert not any(HAIKU55 in ids for ids in mf._normalize_config(cfg)["ladders"].values())
    assert errors(cfg) == []


def test_the_adoption_never_asks_about_a_model_a_project_chose():
    cfg = _haiku_for_atlas()
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
    tree = make_tree(tmp_path, _haiku_for_atlas())
    got = py(tree, (
        "print(json.dumps(['claude-haiku-5-5' in mf.KNOWN_MODELS, "
        "mf.last_attempt_model([mf.attempt_marker('claude-haiku-5-5')]), "
        "mf.select('critic', repo='atlas', built_on='claude-haiku-5-5', probe=probe), "
        "mf.select_with_reasons('critic', repo='atlas', built_on='claude-haiku-5-5', "
        "probe=probe)['built_on_unknown']]))"
    ))
    assert got == [True, HAIKU55, ladder(_haiku_for_atlas(), "advisory")[0], False]


def test_todays_known_sets_carry_every_project_id():
    assert mf._project_ids(mf.CONFIG) <= mf.KNOWN_MODELS
    assert mf._project_ids(committed()) <= ma._known_ids(committed())


# --------------------------------------------------------------------------- #
# Nothing automatic changes                                                    #
# --------------------------------------------------------------------------- #

def test_the_automatic_paths_never_climb():
    everything_up = {m: True for m in prices()}
    for role in mf.AGENT_KINDS:
        mf.clear_availability_cache()
        got = mf.select(role, probe=lambda m: everything_up.get(m, True))
        assert got in mf.ladder_for(role), role
        assert got == mf.ladder_for(role)[0], role


def test_discover_writes_no_file(tmp_path):
    tree = make_tree(tmp_path, committed())

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
    cheap = {**ma.load_prices(), "claude-corvid-1": {"input": 0.1, "output": 0.5}}
    for cfg in (committed(), lead_with(committed(), "workhorse", FABLE51),
                _haiku_for_atlas()):
        found = ma.classify_catalog(catalog, copy.deepcopy(cfg), cheap)
        assert found and all(d["rule"] != ma.RULE_ADOPT for d in found), found
