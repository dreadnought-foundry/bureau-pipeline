"""DRE-6834 — "Add a group" works: a group added from the console is accepted,
gets its own model, and takes agents.

THE PROBLEM. The CEO approved Settings › Agents & models on 2026-10-10 with
"+ Add a group" on it — a name, what it is for, a model: "a new empty box, and
a new column in the table". The change script had accepted `add_group` since
DRE-6624, but the policy refused any fourth group, so the save failed:

    change 1 of 1 (add_group product-design claude-opus-5-5): kinds: must
    declare exactly ['workhorse', 'advisory', 'judgement'], got [...]

THE RULE. An added group is a BUILD group. Every rule that holds for the
Builders' ladder holds for its ladder, a pull request built by a role in it is
never reviewed by the model that built it, and the critic and the verifier
still may not leave Reviewers, nor anyone but the planner join Planners.

The replay runs against the committed `config/models.yaml` in a copy of this
repository, the way `model-config-change.yml` saves it; every rule test runs on
the frozen fixture (`tests/model_fixture.py`), and every id, agent and slug is
read off the file it runs on.

Run: cd bureau-pipeline && python3 -m pytest tests/test_added_group.py -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import console_change_walk as walk  # noqa: E402
import model_choice  # noqa: E402
import model_config_change as mcc  # noqa: E402
import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import sync_model_config  # noqa: E402

TODAY = "2026-10-11"
LABEL = "Product & design"
PURPOSE = "Decide what to build and how it looks."
#: The name the CEO's design adds; a fresh one when a save already took it.
WANTED = "product-design"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _ids(rungs) -> list[str]:
    return [r["model"] if isinstance(r, dict) else r for r in rungs or []]


def _free_name(raw: dict, wanted: str = WANTED) -> str:
    """`wanted`, or `wanted-2`, `wanted-3`, … — the first name no group or
    ladder of `raw` holds."""
    taken = set(raw.get("kinds") or {}) | set(raw.get("ladders") or {})
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}-{n}"
    return name


def _add(raw: dict, model: str, group: str | None = None) -> dict:
    return {"op": "add_group", "group": _free_name(raw) if group is None else group,
            "label": LABEL,
            "purpose": PURPOSE, "model": model}


def _build_agent(raw: dict) -> str:
    """The first pipeline-run agent in the Builders group."""
    session = set(raw.get("session_agents") or [])
    return next(name for name, kind in raw["agents"].items()
                if kind == mf.WORKHORSE_KIND and name not in session)


def _session_agent(raw: dict) -> str:
    return raw["session_agents"][0]


def _slug(raw: dict) -> str:
    """A real repo slug with no exception of its own."""
    taken = set(raw.get("projects") or {})
    slugs = json.loads((ROOT / "config" / "repo-map.json").read_text())
    return next(slug for slug in slugs if slug not in taken)


def _priced_off(raw: dict, prices: dict, *ladders: str) -> list[str]:
    """Priced models on none of `ladders` and not set aside, in price order."""
    on = {m for name in ladders for m in _ids(raw["ladders"][name])}
    aside = set(_ids(raw.get("excluded"))) | set(_ids(raw.get("retired")))
    return [m for m in prices if m not in on and m not in aside]


def _change_set(*changes: dict) -> dict:
    return {"requested_by": "ceo@example.com", "changes": list(changes)}


def _apply(text: str, *changes: dict, **kwargs):
    return mcc.apply_change_set(text, _change_set(*changes), today=TODAY,
                                prices=kwargs.pop("prices", model_fixture.fixture_prices()),
                                **kwargs)


def _reasons(refusals) -> list[str]:
    return [reason for refusal in refusals for reason in refusal["reasons"]]


def _select(tree: Path, *args: str) -> str:
    """`model_fallback.py select …` in `tree`, every model available, no token."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")}
    env["BUREAU_FAKE_AVAILABLE"] = "{}"
    import subprocess

    proc = subprocess.run([sys.executable, "scripts/model_fallback.py", "select", *args],
                          cwd=tree, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING model config" not in proc.stderr, proc.stderr
    return proc.stdout.strip()


def _frozen_tree(tmp_path: Path) -> Path:
    return model_fixture.fixture_tree(tmp_path / "tree")


def _models(tree: Path) -> dict:
    return yaml.safe_load((tree / "config" / "models.yaml").read_text())


# --------------------------------------------------------------------------- #
# The replay: the CEO's own save, against the committed file                   #
# --------------------------------------------------------------------------- #

def test_the_ceos_add_group_saves_against_the_committed_file(tmp_path):
    """`add_group product-design` ("Product & design", "Decide what to build
    and how it looks.", claude-opus-5-5) — the change the CEO's save sent —
    validates, applies, regenerates and passes `sync_model_config.py --check`
    on a copy of this repository."""
    tree = walk.copy_tree(tmp_path / "tree")
    before = _models(tree)
    change = {"op": "add_group", "group": _free_name(before), "label": LABEL,
              "purpose": PURPOSE, "model": "claude-opus-5-5"}
    work = tmp_path / "save"
    work.mkdir()
    walk.save(tree, [change], work)

    after = _models(tree)
    group = change["group"]
    assert after["kinds"][group] == {"ladder": group, "label": LABEL, "purpose": PURPOSE}
    assert _ids(after["ladders"][group]) == [change["model"]]
    for kind in before["kinds"]:
        assert after["kinds"][kind] == before["kinds"][kind]
        assert after["ladders"][kind] == before["ladders"][kind]
    page = json.loads((tree / "config" / "model-choices.json").read_text())
    assert group in [g["key"] for g in page["groups"]]


def test_one_change_set_adds_a_group_moves_two_agents_and_gives_a_project_a_model(tmp_path):
    """Add a group, move a pipeline-run build agent and a session-run agent
    into it, and give one project an exception for it — one save."""
    tree = walk.copy_tree(tmp_path / "tree")
    raw = _models(tree)
    prices = yaml.safe_load((tree / "config" / "model-prices.yaml").read_text())["prices"]
    first = model_fixture.ladder(raw, mf.WORKHORSE_KIND)[0]
    aside = set(_ids(raw.get("excluded"))) | set(_ids(raw.get("retired")))
    other = next(m for m in prices if m != first and m not in aside)
    add = _add(raw, first)
    group, agent, session, slug = (add["group"], _build_agent(raw),
                                   _session_agent(raw), _slug(raw))
    work = tmp_path / "save"
    work.mkdir()
    walk.save(tree, [
        add,
        {"op": "move_agent", "agent": agent, "group": group},
        {"op": "move_agent", "agent": session, "group": group},
        {"op": "set_project_model", "project": slug, "group": group, "model": other},
    ], work)

    after = _models(tree)
    assert after["agents"][agent] == group
    assert after["agents"][session] == group
    assert session in after["session_agents"]
    assert session not in (after.get("ungrouped") or [])
    assert after["projects"][slug] == {group: other}
    registry = (tree / "agents.yaml").read_text()
    assert f"    kind: {group}\n" in registry


# --------------------------------------------------------------------------- #
# Selection                                                                    #
# --------------------------------------------------------------------------- #

def test_a_role_in_an_added_group_selects_from_its_ladder_and_its_projects_exception(tmp_path):
    tree = _frozen_tree(tmp_path)
    raw, prices = _models(tree), model_fixture.fixture_prices()
    builders = model_fixture.ladder(raw, mf.WORKHORSE_KIND)
    # Models the Builders do not lead with, so a walk of the wrong ladder shows.
    mine, theirs = [m for m in prices if m not in builders[:1]
                    and m not in _ids(raw["excluded"])][:2]
    add = _add(raw, mine)
    agent, slug = _build_agent(raw), _slug(raw)
    work = tmp_path / "save"
    work.mkdir()
    walk.save(tree, [add, {"op": "move_agent", "agent": agent, "group": add["group"]}], work)

    assert _select(tree, agent) == mine
    assert _select(tree, agent, "--repo", slug) == mine
    work = tmp_path / "save-2"
    work.mkdir()
    walk.save(tree, [{"op": "set_project_model", "project": slug,
                      "group": add["group"], "model": theirs}], work)
    assert _select(tree, agent, "--repo", slug) == theirs
    assert _select(tree, agent) == mine
    # The Builders and every other project are untouched.
    other = _build_agent(_models(tree))
    assert _select(tree, other, "--repo", slug) == builders[0]


def test_a_pull_request_built_in_an_added_group_is_not_reviewed_on_its_build_model(tmp_path):
    """The reviewer skips the model that built the pull request, when the
    builder is in an added group — and, when the build model cannot be read,
    every model the added group builds on comes off the reviewer's walk as the
    Builders' do."""
    tree = _frozen_tree(tmp_path)
    raw, prices = _models(tree), model_fixture.fixture_prices()
    builders = model_fixture.ladder(raw, mf.WORKHORSE_KIND)
    # A model no Builders rung holds, made the Reviewers' first and the added
    # group's only model: the overlap is the added group's alone.
    shared = _priced_off(raw, prices, raw["kinds"][mf.WORKHORSE_KIND]["ladder"])[0]
    add = _add(raw, shared)
    agent = _build_agent(raw)
    work = tmp_path / "save"
    work.mkdir()
    walk.save(tree, [
        {"op": "set_default_model", "group": mf.ADVISORY_KIND, "model": shared},
        add,
        {"op": "move_agent", "agent": agent, "group": add["group"]},
    ], work)
    assert _select(tree, agent) == shared
    for reviewer in mf.BACKSTOP_ROLES:
        assert _select(tree, reviewer) == shared
        assert _select(tree, reviewer, "--built-on", shared) != shared
        assert _select(tree, reviewer, "--built-on-unknown") != shared
    assert shared not in builders


# --------------------------------------------------------------------------- #
# Every Builders rule holds for an added group's ladder                        #
# --------------------------------------------------------------------------- #

FROZEN = model_fixture.fixture_config()
PRICES = model_fixture.fixture_prices()
GROUP = _free_name(FROZEN)
WITH_GROUP = mcc.edit_data(FROZEN, _add(FROZEN, model_fixture.ladder(
    FROZEN, mf.WORKHORSE_KIND)[0], GROUP), TODAY)
SLUGS = set(json.loads((ROOT / "config" / "repo-map.json").read_text()))


def _errors(cfg: dict, prices: dict | None = None) -> list[str]:
    return mf.policy_errors(cfg, PRICES if prices is None else prices, repo_slugs=SLUGS)


def _on(cfg: dict, ladder: str, model: str) -> dict:
    out = yaml.safe_load(yaml.safe_dump(cfg))
    out["ladders"][ladder].append({"model": model, "reason": "the test's own"})
    return out


def _unpriced(cfg):
    return "claude-nonesuch-9", PRICES


def _excluded(cfg):
    return _ids(cfg["excluded"])[0], PRICES


def _retired(cfg):
    model = _ids(cfg["retired"])[0]
    return model, {**PRICES, model: {"input": 1.0, "output": 2.0}}


@pytest.mark.parametrize("rule", [_unpriced, _excluded, _retired],
                         ids=["4b-no-declared-price", "7-excluded", "11-retired"])
def test_every_rule_that_refuses_a_builders_model_refuses_it_on_an_added_group(rule):
    assert _errors(WITH_GROUP) == []
    model, prices = rule(WITH_GROUP)
    builders = WITH_GROUP["kinds"][mf.WORKHORSE_KIND]["ladder"]
    on_builders = _errors(_on(WITH_GROUP, builders, model), prices)
    on_added = _errors(_on(WITH_GROUP, GROUP, model), prices)
    assert on_builders, f"{model} is not refused on the Builders' ladder"
    assert on_added == [e.replace(f"ladders.{builders}", f"ladders.{GROUP}")
                        for e in on_builders]


def test_a_model_the_pinned_claude_code_cannot_run_is_refused_for_an_added_group():
    model = model_fixture.ladder(FROZEN, mf.JUDGEMENT_KIND)[-1]
    text = model_fixture.fixture_text()
    floors, installed = {model: "9.0.0"}, "2.1.284"
    _, builders = _apply(text, {"op": "set_default_model", "group": mf.WORKHORSE_KIND,
                                "model": model}, floors=floors, installed=installed)
    _, added = _apply(text, _add(FROZEN, model), floors=floors, installed=installed)
    assert _reasons(builders) == [f"Our pinned Claude Code cannot run "
                                  f"{model_choice.model_label(model)} yet."]
    assert _reasons(added) == _reasons(builders)


@pytest.mark.parametrize("reviewer", mf.BACKSTOP_ROLES)
def test_the_critic_and_the_verifier_may_not_move_into_an_added_group(reviewer):
    moved = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
    moved["agents"][reviewer] = GROUP
    assert any(e.startswith(f"agents.{reviewer}: must be 'advisory'")
               for e in _errors(moved)), _errors(moved)
    text, _ = _apply(model_fixture.fixture_text(), _add(FROZEN, model_fixture.ladder(
        FROZEN, mf.WORKHORSE_KIND)[0], GROUP))
    _, refusals = _apply(text, {"op": "move_agent", "agent": reviewer, "group": GROUP})
    assert refusals, f"{reviewer} moved into an added group"


def test_only_the_planner_may_be_in_planners_with_an_added_group_beside_it():
    moved = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
    agent = _build_agent(moved)
    moved["agents"][agent] = mf.JUDGEMENT_KIND
    assert any(e.startswith(f"agents.{agent}: 'judgement'") for e in _errors(moved))


def test_any_agent_that_may_join_the_builders_may_join_an_added_group():
    session = set(WITH_GROUP["session_agents"])
    for name, kind in WITH_GROUP["agents"].items():
        if kind == mf.WORKHORSE_KIND:
            continue
        to_builders = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
        to_builders["agents"][name] = mf.WORKHORSE_KIND
        to_added = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
        to_added["agents"][name] = GROUP
        assert bool(_errors(to_builders)) == bool(_errors(to_added)), name
    for name in WITH_GROUP["ungrouped"]:
        assert name in session
        joined = mcc.edit_data(WITH_GROUP, {"op": "move_agent", "agent": name,
                                            "group": GROUP}, TODAY)
        assert _errors(joined) == [], name


def test_a_declared_rule_for_a_model_an_added_group_shares_with_reviewers_is_not_stale():
    """Rule 8's overlap is read off every build ladder, the added group's
    included: a rule for a model on Reviewers and the added group only
    describes a real overlap."""
    cfg = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
    builders = model_fixture.ladder(cfg, mf.WORKHORSE_KIND)
    reviewers = model_fixture.ladder(cfg, mf.ADVISORY_KIND)
    shared = _priced_off(cfg, PRICES, cfg["kinds"][mf.WORKHORSE_KIND]["ladder"])[0]
    cfg["ladders"][cfg["kinds"][mf.ADVISORY_KIND]["ladder"]].insert(
        0, {"model": shared, "reason": "the test's own"})
    cfg["ladders"][GROUP] = [{"model": shared, "reason": "the test's own"}]
    cfg["review_separation"]["rules"].append(
        {"built_on": shared, "reviewers_use": reviewers[-1], "reason": "the test's own"})
    assert shared not in builders
    assert _errors(cfg) == []


def test_an_added_group_walks_a_ladder_of_its_own_name():
    shared = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
    shared["kinds"][GROUP]["ladder"] = shared["kinds"][mf.WORKHORSE_KIND]["ladder"]
    found = _errors(shared)
    assert any(e.startswith(f"kinds.{GROUP}: ") for e in found), found


def test_the_three_groups_are_still_required():
    for kind in mf.ROLE_KINDS:
        cfg = yaml.safe_load(yaml.safe_dump(WITH_GROUP))
        del cfg["kinds"][kind]
        found = _errors(cfg)
        assert found and found[0].startswith("kinds: must declare "), (kind, found)


# --------------------------------------------------------------------------- #
# A name the console may not use, refused in one plain sentence                #
# --------------------------------------------------------------------------- #

def _refused_in_one_sentence(group: str) -> str:
    text = model_fixture.fixture_text()
    change = _add(FROZEN, model_fixture.ladder(FROZEN, mf.WORKHORSE_KIND)[0], group)
    after, refusals = _apply(text, change)
    assert after == text
    assert len(refusals) == 1 and refusals[0]["index"] == 1, refusals
    lines = mcc.plain_lines(refusals[0]["change"], refusals[0]["reasons"], FROZEN)
    assert len(lines) == 1, lines
    assert model_choice.plain_words_problems(lines[0]) == [], lines[0]
    return lines[0]


def test_a_taken_name_is_refused_in_one_plain_sentence():
    taken = next(iter(FROZEN["kinds"]))
    line = _refused_in_one_sentence(taken)
    label = FROZEN["kinds"][taken]["label"]
    assert f"the name {taken} is already taken by the {label} group" in line, line


def test_an_empty_name_is_refused_in_one_plain_sentence():
    line = _refused_in_one_sentence("")
    assert "a new group needs a name" in line, line


@pytest.mark.parametrize("name", ["Product-Design", "product_design", "product design",
                                  "product.design", "-design", "1st-group", "yes"])
def test_a_name_that_is_not_lowercase_letters_digits_and_dashes_is_refused(name):
    line = _refused_in_one_sentence(name)
    assert "lowercase letters, digits and dashes" in line, line


# --------------------------------------------------------------------------- #
# The published file                                                          #
# --------------------------------------------------------------------------- #

def _page(raw: dict, tmp_path: Path) -> dict:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    registry = mcc.registry_agents((model_fixture.FIXTURE_DIR / "agents.yaml").read_text())
    return json.loads(sync_model_config.render_choices(
        raw, sync_model_config.load_config(path), PRICES, registry))


def test_the_published_file_says_the_pipeline_takes_a_new_group(tmp_path):
    page = _page(FROZEN, tmp_path)
    assert page["add_group"] == {"allowed": True, "reason": None}
    assert [g["key"] for g in page["groups"]] == list(FROZEN["kinds"])


def test_the_published_file_carries_an_added_group_and_every_move_into_it(tmp_path):
    page = _page(WITH_GROUP, tmp_path)
    assert page["add_group"] == {"allowed": True, "reason": None}
    group = next(g for g in page["groups"] if g["key"] == GROUP)
    assert group["label"] == LABEL
    assert group["purpose"] == PURPOSE
    assert group["default_model"] == model_fixture.ladder(WITH_GROUP, GROUP)[0]
    assert group["fallbacks"] == []
    assert [c["model"] for c in group["choices"]] == list(PRICES)
    for agent in page["agents"]:
        if agent["group"] == GROUP:
            continue
        move = next((m for m in agent["moves"] if m["group"] == GROUP), None)
        assert move is not None, agent["name"]
        expected = agent["name"] not in mf.BACKSTOP_ROLES
        assert move["allowed"] is expected, (agent["name"], move)
    for reviewer in mf.BACKSTOP_ROLES:
        agent = next(a for a in page["agents"] if a["name"] == reviewer)
        move = next(m for m in agent["moves"] if m["group"] == GROUP)
        assert model_choice.plain_words_problems(move["reason"]) == [], move
