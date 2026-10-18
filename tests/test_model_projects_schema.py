"""DRE-6633 — config/models.yaml grows the five keys the console's Settings ›
Agents & models page reads, and the per-project exception the selector will
honor, and `policy_errors` refuses a malformed one in plain English.

WHAT CHANGES, AND WHAT DOES NOT
-------------------------------
The file gains `kinds.<kind>.label` / `kinds.<kind>.purpose`, `ungrouped`,
`about` and `projects`. A repo the console has given no exception walks the
fleet ladders, and every role's pick is read off the ladders rather than
hardcoded (`TestSelectionIsUnchanged`).

WHAT IS READ FROM WHERE (DRE-6863, DRE-6988)
--------------------------------------------
The console changes a choice on every save, so a RULE here is proved on the
frozen files (tests/model_fixture.py), and every model id and role it names is
read off the frozen home document through `ladder`, never typed. A project's
own model is a choice the home holds (DRE-6939), so a project rule is a
document built with `with_project`, judged by `model_home.home_errors` on the
frozen code-side facts and prices, and a walk it makes is read off a fixture
tree's selector that has read it (`fixture_module(..., doc=...)`). A rule about
the rest of the frozen `models.yaml` — its keys' shapes, rule 10 — is still
`policy_errors` on a copy of it. What holds for any saved state — the
committed `projects` block validates with the committed repo map, `ungrouped`
and `agents` are disjoint and every name in them has an `about` line — keeps
reading the committed file and derives what it expects from it.

ANY PRICED MODEL (DRE-6785, the CEO's decision of 2026-10-10)
-------------------------------------------------------------
`projects.<slug>.<kind>: <model>` puts that model first. It was the own-kind
reading until 2026-10-10 — the model had to be a rung of that kind's own
ladder — and the CEO lifted it: "I should be able to set it to anything I
want." So Sonnet 4.6 and Fable 5.1 are admitted as workhorse
exceptions, and so is a priced model on no ladder; a model with no declared
price is refused with one plain sentence.

An admitted exception is then held to `excluded` and `retired` like any model
a document names, in the one plain sentence a writer prints.

RULE 10 — `judgement` is the planner's kind. Nothing enforced the "ONE role,
deliberately" comment, so `agents.engineer: judgement` validated clean and
would have put Fable 5.1 on every build card.

Run: cd bureau-pipeline && python3 -m pytest tests/test_model_projects_schema.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home  # noqa: E402
from model_config_text import with_session_agents_ungrouped  # noqa: E402
from model_fixture import fixture_home, ladder, with_project  # noqa: E402

CONFIG_PATH = ROOT / "config" / "models.yaml"
REPO_MAP_PATH = ROOT / "config" / "repo-map.json"
README = ROOT / "config" / "README.md"

# The frozen document's groups, and the rungs the rules below are about, read
# off them by their place — never typed. `TestTheFixtureHoldsTheRows` pins the
# shape each name relies on. The frozen models.yaml holds the code-side facts.
FIXTURE = model_fixture.fixture_config()
HOME = fixture_home()
FIXTURE_PRICES = mf.declared_prices(model_fixture.FIXTURE_DIR / "model-prices.yaml")
WORK = ladder(HOME, "workhorse")
ADVICE = ladder(HOME, "advisory")
PLAN = ladder(HOME, "judgement")
# The build ladder's top, which every other ladder degrades onto.
BUILD_TOP = WORK[0]
# A build rung below the top: the workhorse exception the scenarios set.
REORDER = WORK[2]
# The judgement ladder's top and last rung, both on no other ladder.
PLAN_TOP = PLAN[0]
PLAN_LAST = PLAN[-1]

# A model id with no declared price in the frozen files.
UNPRICED = "claude-nova"

# The roles the rules move, read off the frozen document's `agents`.
_KINDS = HOME["agents"]
BUILD_ROLES = [r for r, k in _KINDS.items() if k == "workhorse"]
PLANNER = next(r for r, k in _KINDS.items() if k == "judgement")
BOUND = FIXTURE["review_separation"]["roles"][0]
UNBOUND_ADVISORY = next(r for r, k in _KINDS.items()
                        if k == "advisory" and r not in FIXTURE["review_separation"]["roles"])


def committed() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def fixture() -> dict:
    return model_fixture.fixture_config()


def slugs() -> set[str]:
    return set(json.loads(REPO_MAP_PATH.read_text()))


def with_projects(projects) -> dict:
    cfg = fixture()
    cfg["projects"] = projects
    return cfg


def errors(cfg, repo_slugs=None) -> list[str]:
    """`policy_errors` on a config built from the frozen copy, priced by it."""
    return mf.policy_errors(cfg, prices=FIXTURE_PRICES,
                            repo_slugs=slugs() if repo_slugs is None else repo_slugs)


def _ids(entries) -> list[str]:
    return [e["model"] if isinstance(e, dict) else e for e in entries or []]


def frozen_side(**changes) -> dict:
    """The code-side facts of the frozen files, as a reader holds them, with
    `changes` laid over them."""
    registry = yaml.safe_load((model_fixture.FIXTURE_DIR / "agents.yaml").read_text())
    side = {
        "excluded": _ids(FIXTURE["excluded"]),
        "retired": _ids(FIXTURE["retired"]),
        "session_agents": list(FIXTURE["session_agents"]),
        "agents": [a["name"] for a in registry["agents"]],
    }
    return {**side, **changes}


_THE_REPO_MAP = object()


def home_errors(doc, repo_slugs=_THE_REPO_MAP, side=None) -> list[str]:
    """`model_home.home_errors` on a document, judged by the frozen code-side
    facts and prices and the repo map (`slugs()` unless `repo_slugs` says
    otherwise — None is a map that could not be read)."""
    return model_home.home_errors(doc, FIXTURE_PRICES,
                                  frozen_side() if side is None else side,
                                  slugs() if repo_slugs is _THE_REPO_MAP else repo_slugs)


def excluded_sentence(model: str) -> str:
    return f"{model} is excluded, so it cannot be chosen."


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """Today's scripts on the frozen model files, shared by the walk tests."""
    return model_fixture.fixture_tree(tmp_path_factory.mktemp("frozen") / "tree")


def selector(tree, doc):
    """The tree's selector, having read `doc` as its home."""
    return model_fixture.fixture_module(tree, doc=doc)


class TestTheFixtureHoldsTheRows:
    """What each name above relies on, true of the frozen copy."""

    def test_the_names_sit_where_the_rows_need_them(self):
        assert len(WORK) == 4 and len(PLAN) == 3
        assert BUILD_TOP in PLAN[1:] and BUILD_TOP in ADVICE[1:]
        assert BUILD_TOP not in {r["built_on"] for r in FIXTURE["review_separation"]["rules"]}
        assert REORDER != BUILD_TOP
        for model in (PLAN_TOP, PLAN_LAST):
            assert model not in WORK and model not in ADVICE
        assert len(BUILD_ROLES) >= 2


# --------------------------------------------------------------------------- #
# The committed data                                                           #
# --------------------------------------------------------------------------- #

class TestCommittedData:

    def test_every_kind_carries_its_label_and_purpose(self):
        # The words are the console's to change; that each group has them is not.
        for kind, spec in committed()["kinds"].items():
            assert isinstance(spec["label"], str) and spec["label"].strip(), kind
            assert isinstance(spec["purpose"], str) and spec["purpose"].strip(), kind

    def test_every_session_agent_is_in_a_group_or_in_none_and_only_they_are_in_none(self):
        # DRE-6889: the CEO moves these agents into groups and adds new ones,
        # so the claim is the one that holds for any saved state — every
        # session-run agent is in a group or in none, and only they are in none.
        cfg = committed()
        assert cfg["session_agents"]
        for name in cfg["session_agents"]:
            assert name in cfg["agents"] or name in cfg["ungrouped"], name
        assert set(cfg["ungrouped"]) <= set(cfg["session_agents"])
        assert not set(cfg["ungrouped"]) & set(cfg["agents"])

    def test_about_has_one_line_for_every_agent_and_every_ungrouped_name(self):
        cfg = committed()
        assert sorted(cfg["about"]) == sorted(list(cfg["agents"]) + cfg["ungrouped"])
        for name, line in cfg["about"].items():
            assert isinstance(line, str) and line.strip(), name
            assert "\n" not in line.strip(), name

    def test_every_project_exception_names_a_declared_repo(self):
        # The console writes exceptions here (DRE-6624); each one is a repo
        # the repo map declares and a kind the file declares.
        cfg = committed()
        for slug, entry in cfg["projects"].items():
            assert slug in slugs(), slug
            assert set(entry) <= set(cfg["kinds"]), slug

    def test_the_committed_config_validates_clean_with_the_real_repo_map(self):
        assert mf.policy_errors(committed()) == []
        assert mf.policy_errors(committed(), repo_slugs=slugs()) == []

    def test_no_pipeline_act_is_declared_for_this_card(self):
        acts = (ROOT / "config" / "pipeline-acts.json").read_text()
        assert "DRE-6633" not in acts


# --------------------------------------------------------------------------- #
# The normalized shape — the contract the siblings read                        #
# --------------------------------------------------------------------------- #

class TestNormalizedShape:

    def test_the_loaded_config_carries_the_four_keys(self):
        cfg = mf.CONFIG
        assert cfg["groups"] == {
            kind: {"label": spec["label"], "purpose": spec["purpose"]}
            for kind, spec in committed()["kinds"].items()}
        assert cfg["ungrouped"] == committed()["ungrouped"]
        assert cfg["about"] == committed()["about"]
        assert cfg["projects"] == committed()["projects"]

    def test_a_kind_with_no_label_or_purpose_gets_the_defaults(self):
        # From a file with no session-run agent in a group: with
        # `session_agents` popped, one the CEO grouped would be a stranger.
        raw = yaml.safe_load(with_session_agents_ungrouped(model_fixture.fixture_text()))
        for spec in raw["kinds"].values():
            spec.pop("label")
            spec.pop("purpose")
        for key in ("ungrouped", "session_agents", "about", "projects"):
            raw.pop(key)
        cfg = mf._normalize_config(raw)
        assert cfg["groups"]["workhorse"] == {"label": "Workhorse", "purpose": ""}
        assert cfg["groups"]["judgement"] == {"label": "Judgement", "purpose": ""}
        assert (cfg["ungrouped"], cfg["about"], cfg["projects"]) == ([], {}, {})
        assert cfg["session_agents"] == []
        assert errors(raw) == []

    def test_projects_normalize_to_slug_kind_model(self):
        cfg = mf._normalize_config(with_projects({"atlas": {"workhorse": REORDER}}))
        assert cfg["projects"] == {"atlas": {"workhorse": REORDER}}

    def test_the_degrade_mirror_gives_the_same_four_keys(self):
        mirror = mf._normalize_config(mf._FALLBACK_MODEL_CONFIG)
        for key in ("groups", "ungrouped", "about", "projects"):
            assert mirror[key] == mf.CONFIG[key], key


# --------------------------------------------------------------------------- #
# The three helpers                                                            #
# --------------------------------------------------------------------------- #

class TestHelpers:

    def test_project_walk_puts_the_exception_first(self):
        assert mf.project_walk(["a", "b", "c"], "b") == ["b", "a", "c"]
        assert mf.project_walk(["a", "b", "c"], "a") == ["a", "b", "c"]

    def test_project_walk_without_an_exception_is_the_ladder(self):
        rungs = ["a", "b", "c"]
        assert mf.project_walk(rungs, None) == rungs
        assert mf.project_walk(rungs, None) is not rungs

    def test_effective_ladders_for_the_workhorse_case(self):
        cfg = with_projects({"atlas": {"workhorse": REORDER}})
        fleet = mf._normalize_config(cfg)["ladders"]
        walks = mf.effective_ladders(cfg, "atlas")
        # The exception first, then every fleet rung in the fleet's order.
        assert walks["workhorse"] == [WORK[2], WORK[0], WORK[1], WORK[3]]
        assert walks["advisory"] == fleet["advisory"]
        assert walks["judgement"] == fleet["judgement"]

    def test_effective_ladders_with_no_entry_is_the_fleet(self):
        cfg = with_projects({"atlas": {"workhorse": REORDER}})
        fleet = mf._normalize_config(cfg)["ladders"]
        assert mf.effective_ladders(cfg, None) == fleet
        assert mf.effective_ladders(cfg, "portico") == fleet
        # Atlas with no entry, on configs this test builds — never the
        # committed file, which carries whatever the console last saved
        # (DRE-6883): an empty map, and the normalized shape naming another
        # project.
        assert mf.effective_ladders(with_projects({}), "atlas") == fleet
        norm = mf._normalize_config(with_projects({"portico": {"advisory": ADVICE[1]}}))
        assert mf.effective_ladders(norm, "atlas") == fleet

    def test_effective_ladders_reads_the_normalized_shape_too(self):
        cfg = mf._normalize_config(with_projects({"atlas": {"judgement": PLAN_LAST}}))
        assert mf.effective_ladders(cfg, "atlas")["judgement"] == [
            PLAN[2], PLAN[0], PLAN[1]]

    def test_declared_repo_slugs_are_the_repo_map_keys(self):
        assert mf.declared_repo_slugs() == slugs()

    def test_an_unreadable_repo_map_is_none_and_says_so(self, tmp_path, capsys):
        assert mf.declared_repo_slugs(tmp_path / "missing.json") is None
        assert "repo-map.json" in capsys.readouterr().err
        bad = tmp_path / "repo-map.json"
        bad.write_text("{ not json")
        assert mf.declared_repo_slugs(bad) is None


# --------------------------------------------------------------------------- #
# Validation — malformed keys                                                  #
# --------------------------------------------------------------------------- #

class TestMalformedKeys:

    @staticmethod
    def _only(found, prefix):
        assert found, "expected a refusal"
        assert all(e.startswith(prefix) for e in found), found

    def test_a_non_string_label_is_refused(self):
        cfg = fixture()
        cfg["kinds"]["workhorse"]["label"] = 3
        self._only(errors(cfg), "kinds.workhorse.label: ")

    def test_a_non_string_purpose_is_refused(self):
        cfg = fixture()
        cfg["kinds"]["advisory"]["purpose"] = ["two", "lines"]
        self._only(errors(cfg), "kinds.advisory.purpose: ")

    @pytest.mark.parametrize("value", ["designer", {"designer": 1}, ["designer", 7]])
    def test_ungrouped_must_be_a_list_of_strings(self, value):
        cfg = fixture()
        cfg["ungrouped"] = value
        self._only(errors(cfg), "ungrouped")

    def test_about_must_be_a_map(self):
        cfg = fixture()
        cfg["about"] = [BUILD_ROLES[0]]
        self._only(errors(cfg), "about: ")

    def test_an_about_line_must_be_a_string(self):
        cfg = fixture()
        cfg["about"][BUILD_ROLES[0]] = {"line": "x"}
        self._only(errors(cfg), f"about.{BUILD_ROLES[0]}: ")

    @pytest.mark.parametrize("projects", [
        ["atlas"],
        {"atlas": REORDER},
        {"atlas": {"workhorse": 5}},
    ])
    def test_projects_must_be_a_map_of_maps_of_strings(self, projects):
        doc = fixture_home()
        doc["projects"] = projects
        assert ("projects must map each project slug to the model each named "
                "group tries first there.") in home_errors(doc)

    def test_a_project_that_is_not_in_the_repo_map_is_refused(self):
        found = home_errors(with_project(HOME, "nowhere", "workhorse", REORDER))
        assert found == ["nowhere is not a project in config/repo-map.json, so its "
                         "choices cannot be used."]

    def test_an_unreadable_repo_map_refuses_every_project_entry(self, tmp_path):
        unread = mf.declared_repo_slugs(tmp_path / "gone.json")
        assert unread is None
        doc = fixture_home()
        doc["projects"] = {}
        doc = with_project(doc, "atlas", "workhorse", REORDER)
        doc = with_project(doc, "portico", "judgement", PLAN_LAST)
        assert home_errors(doc, repo_slugs=unread) == [
            f"{slug} is not a project in config/repo-map.json, so its choices "
            "cannot be used." for slug in ("atlas", "portico")]
        # …and a document with no exception never needs the map at all.
        doc["projects"] = {}
        assert home_errors(doc, repo_slugs=unread) == []

    def test_a_kind_that_is_not_declared_is_refused(self):
        found = home_errors(with_project(HOME, "atlas", "builders", REORDER))
        assert found == ["Project atlas names the group builders, which the "
                         "document does not declare."]

    def test_an_about_name_that_is_no_agent_is_refused(self):
        cfg = fixture()
        cfg["about"]["ghost"] = "Haunts the build"
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith("about.ghost: ")

    def test_a_name_in_both_agents_and_ungrouped_is_refused(self):
        cfg = fixture()
        cfg["ungrouped"] = cfg["ungrouped"] + [BUILD_ROLES[0]]
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith(f"ungrouped.{BUILD_ROLES[0]}: ")
        assert "agents" in found[0]


# --------------------------------------------------------------------------- #
# Validation — any priced model (the own-kind rule lifted, DRE-6785)           #
# --------------------------------------------------------------------------- #

class TestOwnKindMembership:
    """The cross-ladder row DRE-6635's test reads the same way — admitted
    since 2026-10-10."""

    def test_the_rows_preconditions_hold_on_the_frozen_ladders(self):
        for model in (PLAN_LAST, PLAN_TOP):
            assert model in ladder(HOME, "judgement")
            assert model not in ladder(HOME, "workhorse")
            assert model not in ladder(HOME, "advisory")

    def test_a_judgement_only_rung_is_admitted_as_a_workhorse_exception(self, tree):
        doc = with_project(HOME, "atlas", "workhorse", PLAN_LAST)
        assert home_errors(doc) == []
        sel = selector(tree, doc)
        assert sel.ladder_for(BUILD_ROLES[0], repo="atlas") == [PLAN_LAST] + WORK
        assert sel.ladder_for(BUILD_ROLES[0]) == WORK

    def test_the_judgement_top_is_admitted_as_a_workhorse_exception(self, tree):
        doc = with_project(HOME, "atlas", "workhorse", PLAN_TOP)
        assert home_errors(doc) == []
        assert selector(tree, doc).ladder_for(BUILD_ROLES[0], repo="atlas")[0] == PLAN_TOP

    def test_the_same_model_on_its_own_kinds_ladder_is_admitted(self):
        assert home_errors(with_project(HOME, "atlas", "judgement", PLAN_LAST)) == []

    def test_a_priced_model_on_no_ladder_is_admitted(self):
        # The frozen files' set-aside priced model, derived — the committed
        # file loses it the moment a save makes it a default (DRE-6935).
        side = frozen_side()
        aside = [m for m in side["excluded"] if m in FIXTURE_PRICES]
        assert aside, "the frozen copy sets aside no priced model"
        model = aside[0]
        doc = with_project(HOME, "atlas", "advisory", model)
        for group in doc["groups"]:
            assert model not in ladder(doc, group)
        released = dict(side, excluded=[m for m in side["excluded"] if m != model])
        assert home_errors(doc, side=released) == []
        # …and while this release still sets it aside, the document is refused.
        assert home_errors(doc) == [excluded_sentence(model)]

    def test_a_model_with_no_declared_price_says_so_and_only_that(self):
        assert UNPRICED not in FIXTURE_PRICES
        found = home_errors(with_project(HOME, "atlas", "advisory", UNPRICED))
        assert found == [f"{UNPRICED} has no declared price, so it cannot be chosen."]


# --------------------------------------------------------------------------- #
# Validation — the walk an exception makes                                     #
# --------------------------------------------------------------------------- #

class TestExceptionsRunThePolicy:

    def test_a_workhorse_reorder_is_admissible(self):
        assert home_errors(with_project(HOME, "atlas", "workhorse", REORDER)) == []

    def test_a_build_rung_on_top_of_the_judgement_walk_is_admitted(self, tree):
        # Refused by rules 4 and 4b until 2026-10-10 (DRE-6785).
        doc = with_project(HOME, "atlas", "judgement", BUILD_TOP)
        assert home_errors(doc) == []
        assert selector(tree, doc).ladder_for(PLANNER, repo="atlas")[0] == BUILD_TOP

    def test_a_build_rung_on_top_of_the_advisory_walk_is_admitted(self, tree):
        # Refused as a bare overlap until 2026-10-10: the reviewer now skips
        # the build model at selection time instead (DRE-6785).
        doc = with_project(HOME, "atlas", "advisory", BUILD_TOP)
        assert home_errors(doc) == []
        sel = selector(tree, doc)
        assert sel.ladder_for(UNBOUND_ADVISORY, repo="atlas")[0] == BUILD_TOP
        assert sel.reviewers_for(sel.CONFIG, BUILD_TOP, "atlas")[0] != BUILD_TOP

    def test_an_excluded_model_as_an_exception_is_refused(self):
        excluded = frozen_side()["excluded"][0]
        found = home_errors(with_project(HOME, "atlas", "advisory", excluded))
        assert found == [excluded_sentence(excluded)]

    def test_the_fleets_own_findings_are_not_repeated_per_project(self):
        # A finding the fleet's groups already carry is the fleet's, not the
        # exception's: it is said once, and the project adds nothing.
        excluded = frozen_side()["excluded"][0]
        doc = with_project(HOME, "atlas", "workhorse", REORDER)
        doc["groups"]["judgement"]["models"].append(excluded)
        assert home_errors(doc) == [excluded_sentence(excluded)]

    def test_every_exception_is_judged_on_its_own(self):
        excluded = frozen_side()["excluded"][0]
        doc = with_project(HOME, "atlas", "workhorse", REORDER)
        doc = with_project(doc, "atlas", "judgement", PLAN_LAST)
        doc = with_project(doc, "atlas", "advisory", excluded)
        assert home_errors(doc) == [excluded_sentence(excluded)]


# --------------------------------------------------------------------------- #
# Rule 10 — judgement is the planner's kind                                    #
# --------------------------------------------------------------------------- #

class TestRuleTen:

    def test_the_judgement_roles_are_the_planner(self):
        assert mf.JUDGEMENT_ROLES == ("planner",)

    def test_a_build_role_on_judgement_is_refused_in_plain_english(self):
        role = BUILD_ROLES[0]
        cfg = fixture()
        cfg["agents"][role] = "judgement"
        assert errors(cfg) == [
            f"agents.{role}: 'judgement' is the planner's kind — it admits a "
            f"role that runs once per epic, and {role} runs per card; volume on "
            "the strongest model is the 2026-08-09 incident. A build or fix role "
            "is 'workhorse', a reviewer is 'advisory'"
        ]

    # Another build role, and an advisory role the review fence does not bind.
    @pytest.mark.parametrize("role", [BUILD_ROLES[-1], UNBOUND_ADVISORY])
    def test_any_other_role_on_judgement_is_refused(self, role):
        cfg = fixture()
        cfg["agents"][role] = "judgement"
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith(f"agents.{role}: 'judgement' is the planner's kind")

    def test_a_bound_reviewer_on_judgement_carries_both_reasons(self):
        cfg = fixture()
        cfg["agents"][BOUND] = "judgement"
        found = errors(cfg)
        assert any(e.startswith(f"agents.{BOUND}: must be 'advisory'") for e in found), found
        assert any(e.startswith(f"agents.{BOUND}: 'judgement' is the planner's kind")
                   for e in found), found

    @pytest.mark.parametrize("kind", ["advisory", "workhorse"])
    def test_moving_the_planner_down_is_admissible(self, kind):
        cfg = fixture()
        cfg["agents"][PLANNER] = kind
        assert errors(cfg) == []


# --------------------------------------------------------------------------- #
# Nothing any run selects changes                                              #
# --------------------------------------------------------------------------- #

class TestSelectionIsUnchanged:

    def setup_method(self):
        mf.clear_availability_cache()

    def teardown_method(self):
        mf.clear_availability_cache()

    def test_every_role_selects_the_top_of_its_kinds_ladder(self):
        # Every pipeline-run role, read off the file. A session-run agent the
        # CEO put in a group selects as an unknown name — the default ladder's
        # top — whatever its group (DRE-6833).
        cfg = committed()
        session = set(cfg["session_agents"])
        roles = {r: k for r, k in cfg["agents"].items() if r not in session}
        assert roles
        for role, kind in roles.items():
            mf.clear_availability_cache()
            assert mf.select(role, probe=lambda m: True) == ladder(cfg, kind)[0], role
        default_top = [r["model"] for r in cfg["ladders"][cfg["default_ladder"]]][0]
        for role in session & set(cfg["agents"]):
            mf.clear_availability_cache()
            assert mf.select(role, probe=lambda m: True) == default_top, role

    def test_no_repo_without_an_exception_walks_anything_but_the_fleet_ladders(self):
        for slug in sorted(slugs() - set(mf.CONFIG["projects"])):
            assert mf.effective_ladders(mf.CONFIG, slug) == mf.LADDERS, slug


# --------------------------------------------------------------------------- #
# The record                                                                   #
# --------------------------------------------------------------------------- #

class TestDocumented:

    def test_the_readme_documents_the_keys_the_walk_and_rule_10(self):
        text = README.read_text()
        start = text.index("# `models.yaml` — the ONE model config")
        section = " ".join(text[start:text.index("\n---\n", start)].split())
        for needle in ("`label`", "`purpose`", "`ungrouped`", "`about`", "`projects`",
                       "own ladder", "`JUDGEMENT_ROLES`", "rule 10",
                       "config/repo-map.json"):
            assert needle in section, needle

    def test_the_yaml_shows_the_projects_shape_in_a_comment(self):
        lines = CONFIG_PATH.read_text().splitlines()
        at = next(i for i, l in enumerate(lines) if l.startswith("projects:"))
        above = " ".join(l for l in lines[max(0, at - 30):at] if l.lstrip().startswith("#"))
        assert "atlas:" in above and "workhorse:" in above
