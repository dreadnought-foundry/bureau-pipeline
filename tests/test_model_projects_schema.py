"""DRE-6633 — config/models.yaml grows the five keys the console's Settings ›
Agents & models page reads, and the per-project exception the selector will
honor, and `policy_errors` refuses a malformed one in plain English.

WHAT CHANGES, AND WHAT DOES NOT
-------------------------------
The file gains `kinds.<kind>.label` / `kinds.<kind>.purpose`, `ungrouped`,
`about` and `projects`. It shipped with `projects: {}`, so NOTHING any run
selected changed: `SelectionIsUnchangedTest` pins every role's pick, read off
the ladders rather than hardcoded, and every repo the console has given no
exception still walks the fleet ladders.

THE OWN-KIND READING (pinned here so this card and DRE-6635 agree)
------------------------------------------------------------------
`projects.<slug>.<kind>: <model>` puts one of that KIND'S OWN rungs first. It
never adds a model to a kind: a model joins a ladder only through a reviewed
pull request to config/models.yaml (availability is not permission,
2026-08-09). So `claude-sonnet-4-6` — on the judgement ladder only since
2026-09-16 — is refused as a workhorse exception with ONE reason naming where
it sits, and admitted as a judgement exception.

An admissible-by-membership exception is then held to rules 4, 4b, 7 and 8 on
the walk it makes, each finding prefixed `projects.<slug>.<kind>: ` — the
prefix DRE-6635's renderer filters on.

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

import model_fallback as mf  # noqa: E402
import sync_model_config  # noqa: E402

CONFIG_PATH = ROOT / "config" / "models.yaml"
REPO_MAP_PATH = ROOT / "config" / "repo-map.json"
README = ROOT / "config" / "README.md"

OPUS55 = "claude-opus-5-5"
OPUS5 = "claude-opus-5"
SONNET55 = "claude-sonnet-5-5"
SONNET5 = "claude-sonnet-5"
SONNET46 = "claude-sonnet-4-6"
FABLE51 = "claude-fable-5-1"

INTERACTIVE = ["designer", "ux-ui-engineer", "fullstack-engineer", "mobile",
               "product-lead"]


def committed() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def slugs() -> set[str]:
    return set(json.loads(REPO_MAP_PATH.read_text()))


def with_projects(projects) -> dict:
    cfg = committed()
    cfg["projects"] = projects
    return cfg


def errors(cfg, repo_slugs=None) -> list[str]:
    return mf.policy_errors(cfg, repo_slugs=slugs() if repo_slugs is None else repo_slugs)


def ladder(cfg, kind) -> list[str]:
    return [r["model"] for r in cfg["ladders"][cfg["kinds"][kind]["ladder"]]]


# --------------------------------------------------------------------------- #
# The committed data                                                           #
# --------------------------------------------------------------------------- #

class TestCommittedData:

    def test_every_kind_carries_its_label_and_purpose(self):
        kinds = committed()["kinds"]
        assert {k: (v["label"], v["purpose"]) for k, v in kinds.items()} == {
            "workhorse": ("Builders", "Do the work on every card."),
            "advisory": ("Reviewers", "Judge a pull request, a plan or a failed run."),
            "judgement": ("Planners", "Turn an epic into the cards the fleet builds."),
        }

    def test_ungrouped_is_the_five_interactive_agents(self):
        cfg = committed()
        assert cfg["ungrouped"] == INTERACTIVE
        assert not set(cfg["ungrouped"]) & set(cfg["agents"])

    def test_about_has_one_line_for_every_agent_and_every_ungrouped_name(self):
        cfg = committed()
        assert sorted(cfg["about"]) == sorted(list(cfg["agents"]) + cfg["ungrouped"])
        for name, line in cfg["about"].items():
            assert isinstance(line, str) and line.strip(), name
            assert "\n" not in line.strip(), name
        assert cfg["about"]["engineer"] == "Builds a card and opens the PR"
        assert cfg["about"]["designer"] == "Designs screens and systems"

    def test_every_project_exception_names_a_declared_repo(self):
        # The console writes exceptions here (DRE-6624); each one is a repo
        # the repo map declares and a kind the file declares.
        cfg = committed()
        for slug, entry in cfg["projects"].items():
            assert slug in slugs(), slug
            assert set(entry) <= set(cfg["kinds"]), slug

    def test_the_committed_config_validates_clean_with_the_real_repo_map(self):
        assert mf.policy_errors(committed()) == []
        assert errors(committed()) == []

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
            "workhorse": {"label": "Builders", "purpose": "Do the work on every card."},
            "advisory": {"label": "Reviewers",
                         "purpose": "Judge a pull request, a plan or a failed run."},
            "judgement": {"label": "Planners",
                          "purpose": "Turn an epic into the cards the fleet builds."},
        }
        assert cfg["ungrouped"] == INTERACTIVE
        assert cfg["about"] == committed()["about"]
        assert cfg["projects"] == committed()["projects"]

    def test_a_kind_with_no_label_or_purpose_gets_the_defaults(self):
        raw = committed()
        for spec in raw["kinds"].values():
            spec.pop("label")
            spec.pop("purpose")
        for key in ("ungrouped", "about", "projects"):
            raw.pop(key)
        cfg = mf._normalize_config(raw)
        assert cfg["groups"]["workhorse"] == {"label": "Workhorse", "purpose": ""}
        assert cfg["groups"]["judgement"] == {"label": "Judgement", "purpose": ""}
        assert (cfg["ungrouped"], cfg["about"], cfg["projects"]) == ([], {}, {})
        assert errors(raw) == []

    def test_projects_normalize_to_slug_kind_model(self):
        cfg = mf._normalize_config(with_projects({"atlas": {"workhorse": SONNET55}}))
        assert cfg["projects"] == {"atlas": {"workhorse": SONNET55}}

    def test_the_degrade_mirror_gives_the_same_four_keys(self):
        mirror = mf._normalize_config(mf._FALLBACK_MODEL_CONFIG)
        for key in ("groups", "ungrouped", "about", "projects"):
            assert mirror[key] == mf.CONFIG[key], key

    def test_the_generator_loads_the_four_keys(self):
        cfg = sync_model_config.load_config()
        for key in ("groups", "ungrouped", "about", "projects"):
            assert cfg[key] == mf.CONFIG[key], key

    def test_the_mirror_writes_them_after_review_separation_in_order(self):
        cfg = sync_model_config.load_config()
        literal = sync_model_config.render_literal(cfg)
        at = [literal.index(f'\n    "{key}": ') for key in
              ("review_separation", "groups", "ungrouped", "about", "projects")]
        assert at == sorted(at)
        cfg["projects"] = {}
        assert '\n    "projects": {},' in sync_model_config.render_literal(cfg)


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
        cfg = with_projects({"atlas": {"workhorse": SONNET55}})
        fleet = mf._normalize_config(cfg)["ladders"]
        walks = mf.effective_ladders(cfg, "atlas")
        assert walks["workhorse"] == [SONNET55, OPUS55, OPUS5, SONNET5]
        assert walks["advisory"] == fleet["advisory"]
        assert walks["judgement"] == fleet["judgement"]

    def test_effective_ladders_with_no_entry_is_the_fleet(self):
        cfg = with_projects({"atlas": {"workhorse": SONNET55}})
        fleet = mf._normalize_config(cfg)["ladders"]
        assert mf.effective_ladders(cfg, None) == fleet
        assert mf.effective_ladders(cfg, "portico") == fleet
        assert mf.effective_ladders(mf.CONFIG, "atlas") == mf.LADDERS

    def test_effective_ladders_reads_the_normalized_shape_too(self):
        cfg = mf._normalize_config(with_projects({"atlas": {"judgement": SONNET46}}))
        assert mf.effective_ladders(cfg, "atlas")["judgement"] == [
            SONNET46, FABLE51, OPUS55]

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
        cfg = committed()
        cfg["kinds"]["workhorse"]["label"] = 3
        self._only(errors(cfg), "kinds.workhorse.label: ")

    def test_a_non_string_purpose_is_refused(self):
        cfg = committed()
        cfg["kinds"]["advisory"]["purpose"] = ["two", "lines"]
        self._only(errors(cfg), "kinds.advisory.purpose: ")

    @pytest.mark.parametrize("value", ["designer", {"designer": 1}, ["designer", 7]])
    def test_ungrouped_must_be_a_list_of_strings(self, value):
        cfg = committed()
        cfg["ungrouped"] = value
        self._only(errors(cfg), "ungrouped")

    def test_about_must_be_a_map(self):
        cfg = committed()
        cfg["about"] = ["engineer"]
        self._only(errors(cfg), "about: ")

    def test_an_about_line_must_be_a_string(self):
        cfg = committed()
        cfg["about"]["engineer"] = {"line": "x"}
        self._only(errors(cfg), "about.engineer: ")

    @pytest.mark.parametrize("projects, prefix", [
        (["atlas"], "projects: "),
        ({"atlas": "claude-sonnet-5-5"}, "projects.atlas: "),
        ({"atlas": {"workhorse": 5}}, "projects.atlas.workhorse: "),
    ])
    def test_projects_must_be_a_map_of_maps_of_strings(self, projects, prefix):
        self._only(errors(with_projects(projects)), prefix)

    def test_a_project_that_is_not_in_the_repo_map_is_refused(self):
        found = errors(with_projects({"nowhere": {"workhorse": SONNET55}}))
        assert len(found) == 1, found
        assert found[0].startswith("projects.nowhere: ")
        assert "config/repo-map.json" in found[0]

    def test_an_unreadable_repo_map_refuses_every_project_entry(self, monkeypatch,
                                                                 tmp_path):
        monkeypatch.setattr(mf, "_REPO_MAP_PATH", tmp_path / "gone.json")
        found = mf.policy_errors(with_projects({
            "atlas": {"workhorse": SONNET55},
            "portico": {"judgement": SONNET46},
        }))
        assert sorted(e.split(":")[0] for e in found) == [
            "projects.atlas", "projects.portico"]
        assert all("config/repo-map.json" in e for e in found)
        # …and an empty `projects` never needs the map at all.
        assert mf.policy_errors(with_projects({})) == []

    def test_a_kind_that_is_not_declared_is_refused(self):
        found = errors(with_projects({"atlas": {"builders": SONNET55}}))
        assert len(found) == 1, found
        assert found[0].startswith("projects.atlas.builders: ")

    def test_an_about_name_that_is_no_agent_is_refused(self):
        cfg = committed()
        cfg["about"]["ghost"] = "Haunts the build"
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith("about.ghost: ")

    def test_a_name_in_both_agents_and_ungrouped_is_refused(self):
        cfg = committed()
        cfg["ungrouped"] = cfg["ungrouped"] + ["engineer"]
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith("ungrouped.engineer: ")
        assert "agents" in found[0]


# --------------------------------------------------------------------------- #
# Validation — the own-kind membership rule                                    #
# --------------------------------------------------------------------------- #

class TestOwnKindMembership:
    """The cross-ladder row DRE-6635's test reads the same way."""

    def test_the_rows_preconditions_hold_on_the_committed_ladders(self):
        cfg = committed()
        assert SONNET46 in ladder(cfg, "judgement")
        assert FABLE51 in ladder(cfg, "judgement")
        for model in (SONNET46, FABLE51):
            assert model not in ladder(cfg, "workhorse")
            assert model not in ladder(cfg, "advisory")

    def test_sonnet_4_6_is_refused_as_a_workhorse_exception_with_one_reason(self):
        found = errors(with_projects({"atlas": {"workhorse": SONNET46}}))
        assert found == [
            "projects.atlas.workhorse: claude-sonnet-4-6 is not on the workhorse "
            "ladder (it sits on the judgement ladder) — an exception reorders a "
            "kind's own ladder and never adds a model to it; a model joins a "
            "ladder only through a reviewed pull request to config/models.yaml"
        ]

    def test_fable_is_refused_as_a_workhorse_exception_with_one_reason(self):
        found = errors(with_projects({"atlas": {"workhorse": FABLE51}}))
        assert len(found) == 1, found
        assert found[0].startswith(
            "projects.atlas.workhorse: claude-fable-5-1 is not on the workhorse "
            "ladder (it sits on the judgement ladder)")

    def test_the_same_model_on_its_own_kinds_ladder_is_admitted(self):
        assert errors(with_projects({"atlas": {"judgement": SONNET46}})) == []

    def test_a_model_on_no_ladder_says_so(self):
        found = errors(with_projects({"atlas": {"advisory": "claude-opus-4-8"}}))
        assert len(found) == 1, found
        assert "(it is on no ladder)" in found[0]
        assert found[0].startswith("projects.atlas.advisory: ")


# --------------------------------------------------------------------------- #
# Validation — rules 4, 4b, 7, 8 on the walk an exception makes                #
# --------------------------------------------------------------------------- #

class TestExceptionsRunThePolicy:

    def test_a_workhorse_reorder_is_admissible(self):
        assert errors(with_projects({"atlas": {"workhorse": SONNET55}})) == []

    def test_opus_on_top_of_the_judgement_walk_is_refused(self):
        found = errors(with_projects({"atlas": {"judgement": OPUS55}}))
        assert found, "a build model may not top the judgement walk"
        assert all(e.startswith("projects.atlas.judgement: ") for e in found), found
        assert any("rule 4 " in e and "tops the judgement ladder" in e
                   for e in found), found
        assert any("rule 4b " in e and FABLE51 in e for e in found), found

    def test_opus_on_top_of_the_advisory_walk_is_refused(self):
        found = errors(with_projects({"atlas": {"advisory": OPUS55}}))
        assert found
        assert all(e.startswith("projects.atlas.advisory: ") for e in found), found
        assert any("rule 8 " in e and "BARE overlap" in e for e in found), found
        assert any("rule 4 " in e and "tops the advisory ladder" in e
                   for e in found), found

    def test_the_fleets_own_findings_are_not_repeated_per_project(self):
        # A finding the fleet config already carries is the fleet's, not the
        # exception's: the per-project re-run reports only what the walk adds.
        cfg = with_projects({"atlas": {"workhorse": SONNET55}})
        excluded = cfg["excluded"][0]["model"]
        cfg["ladders"]["judgement"].append({"model": excluded, "reason": "test"})
        found = errors(cfg)
        assert any(e.startswith(f"ladders.judgement: {excluded} is excluded")
                   for e in found), found
        assert not any(e.startswith("projects.") for e in found), found

    def test_every_exception_is_judged_on_its_own(self):
        found = errors(with_projects({"atlas": {
            "workhorse": SONNET55, "judgement": SONNET46, "advisory": OPUS55}}))
        assert found
        assert all(e.startswith("projects.atlas.advisory: ") for e in found), found


# --------------------------------------------------------------------------- #
# Rule 10 — judgement is the planner's kind                                    #
# --------------------------------------------------------------------------- #

class TestRuleTen:

    def test_the_judgement_roles_are_the_planner(self):
        assert mf.JUDGEMENT_ROLES == ("planner",)

    def test_engineer_on_judgement_is_refused_in_plain_english(self):
        cfg = committed()
        cfg["agents"]["engineer"] = "judgement"
        assert errors(cfg) == [
            "agents.engineer: 'judgement' is the planner's kind — it admits a "
            "role that runs once per epic, and engineer runs per card; volume on "
            "the strongest model is the 2026-08-09 incident. A build or fix role "
            "is 'workhorse', a reviewer is 'advisory'"
        ]

    @pytest.mark.parametrize("role", ["fixer", "medic"])
    def test_any_other_role_on_judgement_is_refused(self, role):
        cfg = committed()
        cfg["agents"][role] = "judgement"
        found = errors(cfg)
        assert len(found) == 1, found
        assert found[0].startswith(f"agents.{role}: 'judgement' is the planner's kind")

    def test_the_critic_on_judgement_carries_both_reasons(self):
        cfg = committed()
        cfg["agents"]["critic"] = "judgement"
        found = errors(cfg)
        assert any(e.startswith("agents.critic: must be 'advisory'") for e in found), found
        assert any(e.startswith("agents.critic: 'judgement' is the planner's kind")
                   for e in found), found

    @pytest.mark.parametrize("kind", ["advisory", "workhorse"])
    def test_moving_the_planner_down_is_admissible(self, kind):
        cfg = committed()
        cfg["agents"]["planner"] = kind
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
        cfg = committed()
        assert len(cfg["agents"]) == 13
        for role, kind in cfg["agents"].items():
            mf.clear_availability_cache()
            assert mf.select(role, probe=lambda m: True) == ladder(cfg, kind)[0], role

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
