"""config/model-choices.json — the console's Settings › Agents & models page,
read off the gate a pull request meets (DRE-6635).

The console does not parse config/models.yaml and re-derive the policy. It
reads ONE generated file: every priced model, every group with its ladder and
the choices it allows, every agent with the moves it allows, and the
per-project exceptions. ``scripts/sync_model_config.py`` writes it beside the
two mirrors and ``--check`` fails red when it drifts
(tests/test_model_config.py covers the writer, the no-op and the drift gate).

These tests pin the content. The last two are the point of the file: every
``allowed`` and ``reason`` is recomputed here through the REAL route — a real
repo slug's exception written under ``projects``, an agent moved under
``agents``, ``policy_errors`` with the real repo map and prices — so the page
and the gate cannot disagree, and the renderer cannot drift from the gate on
its own.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHOICES = ROOT / "config" / "model-choices.json"
CONFIG = ROOT / "config" / "models.yaml"
PRICES = ROOT / "config" / "model-prices.yaml"

sys.path.insert(0, str(ROOT / "scripts"))

import model_fallback as mf  # noqa: E402
import sync_model_config  # noqa: E402

# A real slug of config/repo-map.json — the gate's own route, no probe.
REAL_SLUG = "atlas"

UNGROUPED = ["designer", "ux-ui-engineer", "fullstack-engineer", "mobile", "product-lead"]


def _choices() -> dict:
    return json.loads(CHOICES.read_text(encoding="utf-8"))


def _raw() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _price_ids() -> list[str]:
    return list(yaml.safe_load(PRICES.read_text())["prices"])


def _group(doc: dict, key: str) -> dict:
    return next(g for g in doc["groups"] if g["key"] == key)


def _choice(doc: dict, kind: str, model: str) -> dict:
    return next(c for c in _group(doc, kind)["choices"] if c["model"] == model)


def _agent(doc: dict, name: str) -> dict:
    return next(a for a in doc["agents"] if a["name"] == name)


def _move(doc: dict, name: str, kind: str) -> dict:
    return next(m for m in _agent(doc, name)["moves"] if m["group"] == kind)


# --------------------------------------------------------------------------- #
# The file and its shape                                                      #
# --------------------------------------------------------------------------- #

def test_the_file_is_committed_and_is_what_the_renderer_writes():
    text = CHOICES.read_text(encoding="utf-8")
    raw = _raw()
    rendered = sync_model_config.render_choices(
        raw, sync_model_config.load_config(), mf.declared_prices()
    )
    assert text == rendered


def test_the_file_is_two_space_json_with_a_trailing_newline():
    text = CHOICES.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"


def test_top_level_keys_in_the_contract_order():
    doc = _choices()
    assert list(doc) == ["generated_from", "models", "groups", "agents", "projects"]
    assert doc["generated_from"] == "config/models.yaml"


def test_entry_keys_in_the_contract_order():
    doc = _choices()
    for model in doc["models"]:
        assert list(model) == ["id", "label", "input", "output", "excluded"]
    for group in doc["groups"]:
        assert list(group) == [
            "key", "label", "purpose", "default_model", "fallbacks", "choices"
        ]
        for choice in group["choices"]:
            assert list(choice) == ["model", "allowed", "reason"]
    for agent in doc["agents"]:
        assert list(agent) == ["name", "label", "about", "group", "moves"]
        for move in agent["moves"]:
            assert list(move) == ["group", "allowed", "reason"]


# --------------------------------------------------------------------------- #
# models                                                                      #
# --------------------------------------------------------------------------- #

def test_models_are_exactly_the_priced_ids_in_price_file_order():
    ids = [m["id"] for m in _choices()["models"]]
    assert ids == _price_ids()
    assert ids == [
        "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5",
        "claude-haiku-5-5", "claude-sonnet-4-6", "claude-fable-5-1",
    ]


def test_an_excluded_id_with_no_price_is_never_offered():
    # `claude-fable-5` is excluded in models.yaml and has no declared price: a
    # price is never invented, so the console never offers it.
    ids = [m["id"] for m in _choices()["models"]]
    assert "claude-fable-5" not in _price_ids()
    assert "claude-fable-5" not in ids
    prices = set(_price_ids())
    assert all(model_id in prices for model_id in ids)


def test_model_labels_follow_the_one_rule():
    labels = {m["id"]: m["label"] for m in _choices()["models"]}
    assert labels == {
        "claude-opus-5-5": "Opus 5.5",
        "claude-opus-5": "Opus 5",
        "claude-sonnet-5-5": "Sonnet 5.5",
        "claude-sonnet-5": "Sonnet 5",
        "claude-haiku-5-5": "Haiku 5.5",
        "claude-sonnet-4-6": "Sonnet 4.6",
        "claude-fable-5-1": "Fable 5.1",
    }


def test_model_prices_are_the_declared_numbers():
    declared = yaml.safe_load(PRICES.read_text())["prices"]
    for model in _choices()["models"]:
        entry = declared[model["id"]]
        assert model["input"] == float(entry["input"])
        assert model["output"] == float(entry["output"])
        assert isinstance(model["input"], float) and isinstance(model["output"], float)
    assert _choices()["models"][0]["input"] == 4.0


def test_only_haiku_carries_an_excluded_reason():
    reasons = {e["model"]: e["reason"] for e in _raw()["excluded"]}
    for model in _choices()["models"]:
        if model["id"] == "claude-haiku-5-5":
            assert model["excluded"] == reasons["claude-haiku-5-5"]
            assert "DRE-6288" in model["excluded"]
        else:
            assert model["excluded"] is None, model["id"]


# --------------------------------------------------------------------------- #
# groups                                                                      #
# --------------------------------------------------------------------------- #

def test_one_group_per_kind_with_its_words_and_ladder():
    doc = _choices()
    cfg = sync_model_config.load_config()
    assert [g["key"] for g in doc["groups"]] == ["workhorse", "advisory", "judgement"]
    for group in doc["groups"]:
        kind = group["key"]
        assert group["label"] == cfg["groups"][kind]["label"]
        assert group["purpose"] == cfg["groups"][kind]["purpose"]
        ladder = cfg["ladders"][cfg["kinds"][kind]]
        assert group["default_model"] == ladder[0]
        assert group["fallbacks"] == ladder[1:]
        assert [c["model"] for c in group["choices"]] == [m["id"] for m in doc["models"]]
    builders = _group(doc, "workhorse")
    assert builders["label"] == "Builders"
    assert builders["default_model"] == "claude-opus-5-5"
    assert builders["fallbacks"] == ["claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5"]


def test_refused_and_allowed_choices():
    doc = _choices()
    for model in ("claude-fable-5-1", "claude-sonnet-4-6"):
        choice = _choice(doc, "workhorse", model)
        assert choice["allowed"] is False
        assert f"{model} is not on the workhorse ladder" in choice["reason"]
    # The cross-ladder row: Sonnet 4.6 sits only on the judgement ladder.
    assert "it sits on the judgement ladder" in _choice(
        doc, "workhorse", "claude-sonnet-4-6")["reason"]
    assert _choice(doc, "judgement", "claude-sonnet-4-6")["allowed"] is True
    assert _choice(doc, "workhorse", "claude-sonnet-5-5") == {
        "model": "claude-sonnet-5-5", "allowed": True, "reason": None,
    }


def test_a_groups_allowed_choices_are_its_own_rungs():
    doc = _choices()
    allowed = {
        g["key"]: [c["model"] for c in g["choices"] if c["allowed"]]
        for g in doc["groups"]
    }
    assert allowed == {
        "workhorse": ["claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5"],
        "advisory": ["claude-sonnet-5-5", "claude-sonnet-5"],
        "judgement": ["claude-sonnet-4-6", "claude-fable-5-1"],
    }
    for group in doc["groups"]:
        for choice in group["choices"]:
            if choice["allowed"]:
                assert choice["reason"] is None
            else:
                assert isinstance(choice["reason"], str) and choice["reason"]
                assert not choice["reason"].startswith("projects.")
    # Opus 5.5 is a reviewer rung but may not lead the reviewers (rule 4), nor
    # the planners (rules 4 and 4b).
    assert "tops the advisory ladder" in _choice(doc, "advisory", "claude-opus-5-5")["reason"]
    planners = _choice(doc, "judgement", "claude-opus-5-5")["reason"]
    assert "tops the judgement ladder" in planners and "rule 4b" in planners


def test_an_error_elsewhere_in_the_config_never_reaches_a_choice():
    raw = _raw()
    cfg = sync_model_config.load_config()
    prices = mf.declared_prices()
    broken = copy.deepcopy(raw)
    broken["about"]["nobody-at-all"] = "a line for nobody"
    assert mf.policy_errors(broken, prices)
    clean = json.loads(sync_model_config.render_choices(raw, cfg, prices))
    dirty = json.loads(sync_model_config.render_choices(broken, cfg, prices))
    assert dirty["groups"] == clean["groups"]


# --------------------------------------------------------------------------- #
# agents                                                                      #
# --------------------------------------------------------------------------- #

def test_agents_in_file_order_then_the_ungrouped():
    raw = _raw()
    doc = _choices()
    assert [a["name"] for a in doc["agents"]] == list(raw["agents"]) + UNGROUPED
    for agent in doc["agents"]:
        assert agent["about"] == raw["about"].get(agent["name"])
        assert agent["group"] == raw["agents"].get(agent["name"])
    assert _agent(doc, "ux-ui-engineer")["label"] == "Ux Ui Engineer"
    engineer = _agent(doc, "engineer")
    assert engineer["label"] == "Engineer"
    assert engineer["about"] == "Builds a card and opens the PR"
    assert engineer["group"] == "workhorse"
    assert _agent(doc, "plan-critic-pre")["label"] == "Plan Critic Pre"


def test_the_ungrouped_agents_have_no_group_and_no_moves():
    doc = _choices()
    for name in UNGROUPED:
        agent = _agent(doc, name)
        assert agent["group"] is None
        assert agent["moves"] == []
    assert _agent(doc, "designer")["about"] == "Designs screens and systems"


def test_a_grouped_agent_has_one_move_per_other_kind():
    doc = _choices()
    for agent in doc["agents"]:
        if agent["group"] is None:
            continue
        others = [k for k in ("workhorse", "advisory", "judgement") if k != agent["group"]]
        assert [m["group"] for m in agent["moves"]] == others


def test_refused_and_allowed_moves():
    doc = _choices()
    # The CEO's sample row: refused because the gate refuses it (rule 10).
    engineer = _move(doc, "engineer", "judgement")
    assert engineer["allowed"] is False
    assert "'judgement' is the planner's kind" in engineer["reason"]
    critic = _move(doc, "critic", "workhorse")
    assert critic["allowed"] is False
    assert "must be 'advisory'" in critic["reason"]
    assert _move(doc, "engineer", "advisory") == {
        "group": "advisory", "allowed": True, "reason": None,
    }
    assert _move(doc, "planner", "advisory")["allowed"] is True
    for name, kind in _raw()["agents"].items():
        if kind != "judgement" and name != "planner":
            assert _move(doc, name, "judgement")["allowed"] is False, name


def test_projects_is_empty_while_the_map_is():
    assert _raw()["projects"] == {}
    assert _choices()["projects"] == []


def test_projects_lists_each_slug_in_file_order(tmp_path):
    raw = _raw()
    raw["projects"] = {
        "portico": {"advisory": "claude-sonnet-5"},
        "atlas": {"workhorse": "claude-sonnet-5-5"},
    }
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = sync_model_config.load_config(path)
    doc = json.loads(sync_model_config.render_choices(raw, cfg, mf.declared_prices()))
    assert doc["projects"] == [
        {"slug": "portico", "exceptions": {"advisory": "claude-sonnet-5"}},
        {"slug": "atlas", "exceptions": {"workhorse": "claude-sonnet-5-5"}},
    ]


# --------------------------------------------------------------------------- #
# The page and the gate cannot disagree                                       #
# --------------------------------------------------------------------------- #

def _answer(errors: list[str], prefix: str) -> tuple[bool, str | None]:
    mine = [e[len(prefix):] for e in errors if e.startswith(prefix)]
    return (not mine, "; ".join(mine) if mine else None)


def test_every_choice_is_the_gates_answer_for_a_real_project():
    raw = _raw()
    prices = mf.declared_prices()
    slugs = mf.declared_repo_slugs()
    assert REAL_SLUG in slugs
    checked = 0
    for group in _choices()["groups"]:
        kind = group["key"]
        for choice in group["choices"]:
            trial = copy.deepcopy(raw)
            trial["projects"] = {REAL_SLUG: {kind: choice["model"]}}
            allowed, reason = _answer(
                mf.policy_errors(trial, prices, repo_slugs=slugs),
                f"projects.{REAL_SLUG}.{kind}: ",
            )
            assert (choice["allowed"], choice["reason"]) == (allowed, reason), (
                kind, choice["model"])
            checked += 1
    assert checked == 3 * len(_price_ids())


def test_every_move_is_the_gates_answer():
    raw = _raw()
    prices = mf.declared_prices()
    slugs = mf.declared_repo_slugs()
    baseline = set(mf.policy_errors(raw, prices, repo_slugs=slugs))
    checked = 0
    for agent in _choices()["agents"]:
        for move in agent["moves"]:
            trial = copy.deepcopy(raw)
            trial["agents"][agent["name"]] = move["group"]
            added = [
                e for e in mf.policy_errors(trial, prices, repo_slugs=slugs)
                if e not in baseline
            ]
            assert move["allowed"] is (not added), (agent["name"], move["group"])
            assert move["reason"] == ("; ".join(added) if added else None)
            checked += 1
    assert checked == 2 * len(raw["agents"])
