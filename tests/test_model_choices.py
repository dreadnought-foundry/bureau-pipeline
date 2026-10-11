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

A test here reads the committed file for what must hold in ANY state the
console can save, and derives every expected value from that file: a group's
``default_model`` is the first rung of its ladder as written, an agent's
``group`` its ``agents:`` entry as written, ``projects`` the committed map's
slugs in file order. A rule of the renderer is proved on the frozen fixture
(``tests/model_fixture.py``) instead. Neither shape names a rung id, pairs an
agent with a group, lists agents or expects an empty map (DRE-6861).
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

import claude_code_pin as ccp  # noqa: E402
import model_choice  # noqa: E402
import model_config_change as mcc  # noqa: E402
import model_fallback as mf  # noqa: E402
import sync_model_config  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import model_fixture  # noqa: E402

# A real slug of config/repo-map.json — the gate's own route, no probe.
REAL_SLUG = "atlas"


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


def _rungs(raw: dict, kind: str) -> list[str]:
    """The rung ids of `kind`'s ladder in the raw config, as written."""
    return model_fixture.ladder(raw, kind)


def _fixture_choices(tmp_path, raw: dict | None = None) -> tuple[dict, dict]:
    """`(raw, page)`: the frozen fixture's config — or `raw`, a config built
    from it — and the page the renderer writes for it, against the fixture's
    prices and its registry."""
    raw = model_fixture.fixture_config() if raw is None else raw
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    registry = mcc.registry_agents(
        (model_fixture.FIXTURE_DIR / "agents.yaml").read_text())
    page = sync_model_config.render_choices(
        raw, sync_model_config.load_config(path), model_fixture.fixture_prices(),
        registry)
    return raw, json.loads(page)


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
            # `caution` is the fourth key (DRE-6786): a reader of the old shape
            # still reads the first three.
            assert list(choice) == ["model", "allowed", "reason", "caution"]
    for agent in doc["agents"]:
        assert list(agent) == ["name", "label", "about", "group", "runs_in", "moves"]
        for move in agent["moves"]:
            assert list(move) == ["group", "allowed", "reason"]


# --------------------------------------------------------------------------- #
# models                                                                      #
# --------------------------------------------------------------------------- #

def test_models_are_exactly_the_priced_ids_in_price_file_order():
    ids = [m["id"] for m in _choices()["models"]]
    assert ids == _price_ids()


def test_an_excluded_id_with_no_price_is_never_offered():
    # `claude-fable-5` is excluded in models.yaml and has no declared price: a
    # price is never invented, so the console never offers it.
    ids = [m["id"] for m in _choices()["models"]]
    assert "claude-fable-5" not in _price_ids()
    assert "claude-fable-5" not in ids
    prices = set(_price_ids())
    assert all(model_id in prices for model_id in ids)


def test_model_labels_follow_the_one_rule():
    # The rule, on ids no ladder names: `claude-<family>-<major>[-<minor>]` is
    # `<Family> <major>[.<minor>]`, and an id of any other shape is its own.
    # It lives in scripts/model_choice.py since DRE-6786, beside the words the
    # change-writer shares, and the generator imports it from there.
    label = model_choice.model_label
    assert sync_model_config.model_label is label
    assert label("claude-haiku-5-5") == "Haiku 5.5"
    assert label("claude-fable-5") == "Fable 5"
    assert label("claude-opus-4-8") == "Opus 4.8"
    assert label("claude-opus-10") == "Opus 10"
    assert label("not-a-claude-id") == "not-a-claude-id"
    # And every model the page offers carries it.
    for model in _choices()["models"]:
        assert model["label"] == label(model["id"]), model["id"]
        assert model["label"] != model["id"], model["id"]


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
    # Each group's leader and fallbacks are its ladder AS WRITTEN — whichever
    # model the console last put first (DRE-6861).
    doc = _choices()
    raw = _raw()
    cfg = sync_model_config.load_config()
    assert [g["key"] for g in doc["groups"]] == list(raw["kinds"])
    for group in doc["groups"]:
        kind = group["key"]
        assert group["label"] == cfg["groups"][kind]["label"]
        assert group["purpose"] == cfg["groups"][kind]["purpose"]
        assert group["default_model"] == _rungs(raw, kind)[0]
        assert group["fallbacks"] == _rungs(raw, kind)[1:]
        assert [c["model"] for c in group["choices"]] == [m["id"] for m in doc["models"]]


def test_every_priced_model_is_allowed_everywhere_with_its_caution(tmp_path):
    # DRE-6785 (the CEO, 2026-10-10): any priced model may lead any group, so
    # the own-kind rule, rule 4 and rule 4b refuse nothing now. DRE-6786: a
    # model the file excludes is allowed too — the probe is the change the
    # console would make, which lifts the exclusion — and it carries the
    # set-aside caution instead. A rule of the renderer, on the frozen fixture.
    raw, doc = _fixture_choices(tmp_path)
    prices = model_fixture.fixture_prices()
    excluded = {e["model"] for e in raw["excluded"]} & set(prices)
    assert excluded, "the fixture excludes a priced model"
    for group in doc["groups"]:
        for choice in group["choices"]:
            model = choice["model"]
            assert choice == {
                "model": model, "allowed": True, "reason": None,
                "caution": model_choice.caution_for(raw, prices, group["key"], model),
            }, (group["key"], model)
            if model in excluded:
                assert "set aside" in choice["caution"], (group["key"], model)


def test_a_groups_leader_is_always_allowed_and_a_reason_never_names_a_project():
    # Any state of the file the console can save.
    doc = _choices()
    for group in doc["groups"]:
        allowed = [c["model"] for c in group["choices"] if c["allowed"]]
        assert group["default_model"] in allowed, group["key"]
        for choice in group["choices"]:
            if choice["allowed"]:
                assert choice["reason"] is None
            else:
                assert isinstance(choice["reason"], str) and choice["reason"]
                assert not choice["reason"].startswith("projects.")
                assert choice["caution"] is None



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
    # The committed file in any state the page can save (DRE-6889): its own
    # `agents`, then its own `ungrouped`, each in file order. Which agents those
    # are, and in which groups, is the console's choice (DRE-6861).
    raw = _raw()
    doc = _choices()
    assert [a["name"] for a in doc["agents"]] == list(raw["agents"]) + raw["ungrouped"]
    for agent in doc["agents"]:
        assert agent["about"] == raw["about"].get(agent["name"])
        assert agent["group"] == raw["agents"].get(agent["name"])
        # The one label rule: the name, its hyphens spaces, each word capitalized.
        assert agent["label"] == agent["name"].replace("-", " ").title()


def test_every_runs_in_is_what_session_agents_says():
    # DRE-6833: `session` for a name under `session_agents`, `pipeline` for
    # every other — wherever the console last put either.
    session = set(_raw()["session_agents"])
    for agent in _choices()["agents"]:
        expected = "session" if agent["name"] in session else "pipeline"
        assert agent["runs_in"] == expected, agent["name"]


def test_the_ungrouped_agents_have_no_group_and_no_moves():
    # Three moves each since DRE-6833: they are session agents, and a session
    # agent may join any group. The test keeps its name; its claim about
    # moves inverted with the card. Since DRE-6889 it reads the session agents
    # off the file, wherever the CEO last put them: one in no group may join
    # every group, one in a group every other group.
    raw = _raw()
    doc = _choices()
    assert raw["session_agents"]
    for name in raw["session_agents"]:
        agent = _agent(doc, name)
        assert agent["group"] == raw["agents"].get(name)
        assert (agent["group"] is None) is (name in raw["ungrouped"])
        assert agent["runs_in"] == "session"
        assert [m["group"] for m in agent["moves"]] == [
            k for k in raw["kinds"] if k != agent["group"]]
        assert all(m["allowed"] and m["reason"] is None for m in agent["moves"])


def test_a_grouped_agent_has_one_move_per_other_kind():
    doc = _choices()
    kinds = list(_raw()["kinds"])
    for agent in doc["agents"]:
        if agent["group"] is None:
            continue
        others = [k for k in kinds if k != agent["group"]]
        assert [m["group"] for m in agent["moves"]] == others


def test_refused_and_allowed_moves():
    # The CEO's sample rows, on whatever the file holds: a pipeline-run role
    # outside the planners' kind is refused it (rule 10), a merge-gating
    # reviewer is refused every move, and each refusal is the change script's
    # plain line naming the group by its label. A session-run agent is exempt,
    # and the CEO may have put one anywhere.
    raw = _raw()
    doc = _choices()
    groups = sync_model_config.load_config()["groups"]
    planners = groups[mf.JUDGEMENT_KIND]["label"]
    reviewers = groups[mf.ADVISORY_KIND]["label"]
    owners = " and ".join(mf.JUDGEMENT_ROLES)
    session = set(raw["session_agents"])
    for agent in doc["agents"]:
        name = agent["name"]
        for move in agent["moves"]:
            if move["allowed"]:
                assert move["reason"] is None, (name, move["group"])
            else:
                assert move["reason"].startswith(f"Move {name} from the "), move
        if name in session:
            continue
        if name in mf.BACKSTOP_ROLES:
            for move in agent["moves"]:
                assert move["allowed"] is False, (name, move["group"])
                assert f"{name} must stay in the {reviewers} group" in move["reason"]
        elif name not in mf.JUDGEMENT_ROLES and agent["group"] != mf.JUDGEMENT_KIND:
            move = _move(doc, name, mf.JUDGEMENT_KIND)
            assert move["allowed"] is False, name
            assert f"only the {owners} may be in the {planners} group" in move["reason"]


def test_projects_mirrors_the_map_in_file_order():
    # Holds for any state of the live map: the console writes exceptions here.
    projects = _raw()["projects"]
    assert _choices()["projects"] == [
        {"slug": slug, "exceptions": exceptions}
        for slug, exceptions in projects.items()
    ]
    slugs = mf.declared_repo_slugs()
    for slug in projects:
        assert slug in slugs, slug


def test_projects_lists_each_slug_in_file_order(tmp_path):
    # A rule of the renderer, on a config built from the frozen fixture: two
    # exceptions, each a rung of its own kind's ladder, out of slug order.
    raw = model_fixture.fixture_config()
    reviewers = _rungs(raw, mf.ADVISORY_KIND)[1]
    builders = _rungs(raw, mf.WORKHORSE_KIND)[-1]
    raw["projects"] = {
        "portico": {mf.ADVISORY_KIND: reviewers},
        "atlas": {mf.WORKHORSE_KIND: builders},
    }
    _, doc = _fixture_choices(tmp_path, raw)
    assert doc["projects"] == [
        {"slug": "portico", "exceptions": {mf.ADVISORY_KIND: reviewers}},
        {"slug": "atlas", "exceptions": {mf.WORKHORSE_KIND: builders}},
    ]


# --------------------------------------------------------------------------- #
# The page and the gate cannot disagree                                       #
# --------------------------------------------------------------------------- #

def _answer(errors: list[str], prefix: str) -> tuple[bool, str | None]:
    mine = [e[len(prefix):] for e in errors if e.startswith(prefix)]
    return (not mine, "; ".join(mine) if mine else None)


def _check_choices(raw: dict, doc: dict, prices: dict) -> int:
    """Every choice on `doc` is the gate's answer for the change the console
    makes for it (DRE-6786): the model lifted from `excluded` and written as a
    real project's exception of `raw`. A model the pinned Claude Code cannot
    run is refused before the policy is asked. An allowed choice carries
    `caution_for`'s sentence and a refused one none. The number checked."""
    slugs = mf.declared_repo_slugs()
    assert REAL_SLUG in slugs
    floors = ccp.model_floors(ROOT)
    installed = ccp.installed_claude_code(ROOT) if floors else None
    checked = 0
    for group in doc["groups"]:
        kind = group["key"]
        for choice in group["choices"]:
            model = choice["model"]
            trial = copy.deepcopy(raw)
            trial["excluded"] = [
                e for e in trial.get("excluded") or []
                if (e.get("model") if isinstance(e, dict) else e) != model]
            trial["projects"] = {REAL_SLUG: {kind: model}}
            floor = floors.get(model)
            if floor and ccp.version_key(installed) < ccp.version_key(floor):
                allowed, reason = False, (
                    f"Our pinned Claude Code cannot run {model_choice.model_label(model)} yet.")
            else:
                allowed, reason = _answer(
                    mf.policy_errors(trial, prices, repo_slugs=slugs),
                    f"projects.{REAL_SLUG}.{kind}: ",
                )
            caution = (model_choice.caution_for(raw, prices, kind, model)
                       if allowed else None)
            assert (choice["allowed"], choice["reason"], choice["caution"]) == (
                allowed, reason, caution), (kind, model)
            checked += 1
    assert checked == len(raw["kinds"]) * len(prices)
    return checked


def _check_moves(raw: dict, doc: dict, prices: dict, registry: set[str]) -> int:
    """Every agent's move on `doc`, grouped or not, probed through the change
    script's own edit (`edit_data`) on `raw` and judged as the change script
    judges it: its pre-flight checks, then the findings `policy_errors` ADDS.
    The published reason is the plain line the change script prints for those
    findings (DRE-6833). The number checked."""
    slugs = mf.declared_repo_slugs()
    baseline = set(mf.policy_errors(raw, prices, repo_slugs=slugs))
    checked = 0
    for agent in doc["agents"]:
        for move in agent["moves"]:
            change = {"op": "move_agent", "agent": agent["name"], "group": move["group"]}
            added = mcc._problems(change, raw, prices, registry) or [
                e for e in mf.policy_errors(mcc.edit_data(raw, change, ""), prices,
                                            repo_slugs=slugs)
                if e not in baseline
            ]
            assert move["allowed"] is (not added), (agent["name"], move["group"])
            assert move["reason"] == (
                " ".join(mcc.plain_lines(change, added, raw)) if added else None)
            checked += 1
    groups = len(raw["kinds"])
    assert checked == (groups - 1) * len(raw["agents"]) + groups * len(raw["ungrouped"])
    return checked


def test_every_choice_is_the_gates_answer_for_a_real_project():
    _check_choices(_raw(), _choices(), mf.declared_prices())


def test_every_move_is_the_gates_answer():
    registry = mcc.registry_agents((ROOT / "agents.yaml").read_text())
    _check_moves(_raw(), _choices(), mf.declared_prices(), registry)


def test_the_renderer_writes_the_gates_answer_for_every_choice_and_move(tmp_path):
    # The same two checks on the page the renderer writes for the frozen
    # fixture: the committed page is checked above, and this is what fails
    # when the RENDERER disagrees with the gate, whatever the committed file
    # holds (DRE-6861).
    raw, doc = _fixture_choices(tmp_path)
    prices = model_fixture.fixture_prices()
    registry = mcc.registry_agents(
        (model_fixture.FIXTURE_DIR / "agents.yaml").read_text())
    assert _check_choices(raw, doc, prices)
    assert _check_moves(raw, doc, prices, registry)
    # Both answers occur among the moves, and both a caution and none among the
    # choices — every choice is allowed since DRE-6786 — so a renderer that
    # allowed every move, or cautioned every choice or none, could not pass.
    assert {m["allowed"] for a in doc["agents"] for m in a["moves"]} == {True, False}
    cautions = {c["caution"] is None for g in doc["groups"] for c in g["choices"]}
    assert cautions == {True, False}
