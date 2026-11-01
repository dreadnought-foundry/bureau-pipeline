"""DRE-6834 — "Add a group" works: a group added from the console is accepted,
gets its own model, and takes agents.

THE PROBLEM. The CEO approved Settings › Agents & models on 2026-10-10 with
"+ Add a group" on it — a name, what it is for, a model: "a new empty box, and
a new column in the table". The old pull-request save route had accepted
`add_group` since DRE-6624, but the policy refused any fourth group, so the
save failed:

    change 1 of 1 (add_group product-design claude-opus-5-5): kinds: must
    declare exactly ['workhorse', 'advisory', 'judgement'], got [...]

THE RULE. An added group is a BUILD group. Every rule that holds for the
Builders' models holds for its models, a pull request built by a role in it is
never reviewed by the model that built it, and the critic and the verifier
still may not leave Reviewers, nor anyone but the planner join Planners.

Since DRE-6977 a save writes the home and opens no pull request, and DRE-8021
deleted the route that did. So every test here runs on the frozen home
document (`model_fixture.fixture_home()`) with the group added as a save
writes it: a rule test judges it with `model_home.home_errors` on the frozen
prices and code-side facts — the review-separation rule, a code-side fact,
with `policy_errors` on the config the selector runs on with it read — and a
selection test hands it to a tree built with `fixture_tree(...,
regenerate=False)`. Every id, agent and slug is read off the document or the
frozen files.

Run: cd bureau-pipeline && python3 -m pytest tests/test_added_group.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home  # noqa: E402

LABEL = "Product & design"
PURPOSE = "Decide what to build and how it looks."
#: The name the CEO's design adds; a fresh one when a save already took it.
WANTED = "product-design"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _free_name(raw: dict, wanted: str = WANTED) -> str:
    """`wanted`, or `wanted-2`, `wanted-3`, … — the first name no group of
    `raw` holds."""
    taken = set(raw.get("kinds") or {}) | set(raw.get("ladders") or {})
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}-{n}"
    return name


def _select(tree: Path, doc: dict, *args: str) -> str:
    """`model_fallback.py select …` in `tree` on the home document `doc`,
    every model available, no token."""
    env = model_fixture.home_env(tree, doc, BUREAU_FAKE_AVAILABLE="{}")
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(key, None)
    import subprocess

    proc = subprocess.run([sys.executable, "scripts/model_fallback.py", "select", *args],
                          cwd=tree, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING model config" not in proc.stderr, proc.stderr
    assert "model-home: read" in proc.stderr, proc.stderr
    return proc.stdout.strip()


def _frozen_tree(tmp_path: Path) -> Path:
    return model_fixture.fixture_tree(tmp_path / "tree", regenerate=False)


# --------------------------------------------------------------------------- #
# Selection                                                                    #
# --------------------------------------------------------------------------- #

def _doc_models(doc: dict) -> list[str]:
    """Every model the document's groups hold, first seen first."""
    return list(dict.fromkeys(m for kind in doc["groups"]
                              for m in model_fixture.ladder(doc, kind)))


def _doc_build_agent(doc: dict) -> str:
    """The first agent the document puts in the Builders group."""
    return next(name for name, group in doc["agents"].items()
                if group == mf.WORKHORSE_KIND)


def _doc_slug(doc: dict) -> str:
    """A real repo slug the document gives no exception of its own."""
    taken = set(doc.get("projects") or {})
    slugs = json.loads((ROOT / "config" / "repo-map.json").read_text())
    return next(slug for slug in slugs if slug not in taken)


def _with_group(doc: dict, model: str) -> tuple[dict, str]:
    """`doc` with a group added on `model` alone — its label, purpose and
    models, as a save of "+ Add a group" writes it — and the group's name."""
    group = _free_name({"kinds": doc["groups"]})
    new = json.loads(json.dumps(doc))
    new["groups"][group] = {"label": LABEL, "purpose": PURPOSE, "models": [model]}
    return new, group


def test_a_role_in_an_added_group_selects_from_its_ladder_and_its_projects_exception(tmp_path):
    tree = _frozen_tree(tmp_path)
    home = model_fixture.fixture_home()
    builders = model_fixture.ladder(home, mf.WORKHORSE_KIND)
    # Models the Builders do not lead with, so a walk of the wrong ladder shows.
    mine, theirs = [m for m in _doc_models(home) if m not in builders[:1]][:2]
    agent, slug = _doc_build_agent(home), _doc_slug(home)
    doc, group = _with_group(home, mine)
    doc = model_fixture.move_agent(doc, agent, group)

    assert _select(tree, doc, agent) == mine
    assert _select(tree, doc, agent, "--repo", slug) == mine
    doc = model_fixture.with_project(doc, slug, group, theirs)
    assert _select(tree, doc, agent, "--repo", slug) == theirs
    assert _select(tree, doc, agent) == mine
    # The Builders and every other project are untouched.
    other = _doc_build_agent(doc)
    assert other != agent
    assert _select(tree, doc, other, "--repo", slug) == builders[0]


def test_a_pull_request_built_in_an_added_group_is_not_reviewed_on_its_build_model(tmp_path):
    """The reviewer skips the model that built the pull request, when the
    builder is in an added group — and, when the build model cannot be read,
    every model the added group builds on comes off the reviewer's walk as the
    Builders' do."""
    tree = _frozen_tree(tmp_path)
    home = model_fixture.fixture_home()
    builders = model_fixture.ladder(home, mf.WORKHORSE_KIND)
    # A model no Builders rung holds, made the Reviewers' first and the added
    # group's only model: the overlap is the added group's alone.
    shared = next(m for m in _doc_models(home) if m not in builders)
    agent = _doc_build_agent(home)
    doc = model_fixture.lead_with(home, mf.ADVISORY_KIND, shared)
    doc, group = _with_group(doc, shared)
    doc = model_fixture.move_agent(doc, agent, group)
    assert _select(tree, doc, agent) == shared
    for reviewer in mf.BACKSTOP_ROLES:
        assert _select(tree, doc, reviewer) == shared
        assert _select(tree, doc, reviewer, "--built-on", shared) != shared
        assert _select(tree, doc, reviewer, "--built-on-unknown") != shared
    assert shared not in builders


# --------------------------------------------------------------------------- #
# Every Builders rule holds for an added group's models                        #
# --------------------------------------------------------------------------- #

HOME = model_fixture.fixture_home()
WITH_GROUP, GROUP = _with_group(HOME, model_fixture.ladder(HOME, mf.WORKHORSE_KIND)[0])
PRICES = model_fixture.fixture_prices()
SLUGS = set(json.loads((ROOT / "config" / "repo-map.json").read_text()))


def _frozen_side() -> dict:
    """The frozen code-side facts a home is judged by: `excluded`, `retired`
    and `session_agents` of the frozen `models.yaml`, and the names in the
    frozen `agents.yaml`."""
    frozen = mf._normalize_config(model_fixture.fixture_config())
    return {
        "excluded": list(frozen["excluded"]),
        "retired": list(frozen["retired"]),
        "session_agents": list(frozen.get("session_agents") or []),
        "agents": model_home._registry_names(model_fixture.FIXTURE_DIR / "agents.yaml"),
    }


def _refusals(doc: dict, prices: dict | None = None) -> list[str]:
    """`model_home.home_errors` on `doc`, on the frozen prices and facts: what
    every writer of the home checks before it writes."""
    return model_home.home_errors(doc, PRICES if prices is None else prices,
                                  _frozen_side(), SLUGS)


def _on(doc: dict, group: str, model: str) -> dict:
    """`doc` with `model` last in `group`."""
    new = json.loads(json.dumps(doc))
    new["groups"][group]["models"].append(model)
    return new


def _as_config(doc: dict) -> dict:
    """The config the selector runs on with `doc` read: the frozen file's
    code-side facts and the document's choices."""
    return {**mf._normalize_config(model_fixture.fixture_config()),
            **mf._home_choices(doc)}


def _unpriced():
    return "claude-nonesuch-9", PRICES


def _excluded():
    return _frozen_side()["excluded"][0], PRICES


def _retired():
    model = _frozen_side()["retired"][0]
    return model, {**PRICES, model: {"input": 1.0, "output": 2.0}}


@pytest.mark.parametrize("rule", [_unpriced, _excluded, _retired],
                         ids=["no-declared-price", "excluded", "retired"])
def test_every_rule_that_refuses_a_builders_model_refuses_it_on_an_added_group(rule):
    assert _refusals(WITH_GROUP) == []
    model, prices = rule()
    on_builders = _refusals(_on(WITH_GROUP, mf.WORKHORSE_KIND, model), prices)
    on_added = _refusals(_on(WITH_GROUP, GROUP, model), prices)
    assert on_builders, f"{model} is not refused in the Builders group"
    assert on_added == on_builders


@pytest.mark.parametrize("reviewer", mf.BACKSTOP_ROLES)
def test_the_critic_and_the_verifier_may_not_move_into_an_added_group(reviewer):
    found = _refusals(model_fixture.move_agent(WITH_GROUP, reviewer, GROUP))
    assert any(e.startswith(f"The {reviewer} must be in the {mf.ADVISORY_KIND} group")
               for e in found), found


def test_only_the_planner_may_be_in_planners_with_an_added_group_beside_it():
    agent = _doc_build_agent(WITH_GROUP)
    found = _refusals(model_fixture.move_agent(WITH_GROUP, agent, mf.JUDGEMENT_KIND))
    owners = " and ".join(mf.JUDGEMENT_ROLES)
    assert (f"Only {owners} and the session agents may be in the "
            f"{mf.JUDGEMENT_KIND} group, and {agent} is in it.") in found, found


def test_any_agent_that_may_join_the_builders_may_join_an_added_group():
    for name, group in WITH_GROUP["agents"].items():
        if group == mf.WORKHORSE_KIND:
            continue
        to_builders = _refusals(model_fixture.move_agent(WITH_GROUP, name, mf.WORKHORSE_KIND))
        to_added = _refusals(model_fixture.move_agent(WITH_GROUP, name, GROUP))
        assert bool(to_builders) == bool(to_added), name
    session = _frozen_side()["session_agents"]
    assert session
    for name in session:
        assert name not in WITH_GROUP["agents"], name
        assert _refusals(model_fixture.move_agent(WITH_GROUP, name, GROUP)) == [], name


def test_a_declared_rule_for_a_model_an_added_group_shares_with_reviewers_is_not_stale():
    """Rule 8's overlap is read off every build group, the added group's
    included: a rule for a model the Reviewers share with the added group
    alone describes a real overlap."""
    builders = model_fixture.ladder(HOME, mf.WORKHORSE_KIND)
    reviewers = model_fixture.ladder(HOME, mf.ADVISORY_KIND)
    shared = next(m for m in _doc_models(HOME) if m not in builders)
    doc = model_fixture.lead_with(HOME, mf.ADVISORY_KIND, shared)
    with_group, _ = _with_group(doc, shared)
    rule = {shared: reviewers[-1]}
    for d in (doc, with_group):
        assert _refusals(d) == []
    cfg = _as_config(with_group)
    cfg["review_separation"]["rules"].update(rule)
    assert mf.policy_errors(cfg, PRICES, repo_slugs=SLUGS) == []
    # The positive control: with no added group the rule names an overlap
    # that is not there.
    cfg = _as_config(doc)
    cfg["review_separation"]["rules"].update(rule)
    assert mf.policy_errors(cfg, PRICES, repo_slugs=SLUGS)


def test_the_three_groups_are_still_required():
    for kind in mf.ROLE_KINDS:
        doc = json.loads(json.dumps(WITH_GROUP))
        del doc["groups"][kind]
        found = _refusals(doc)
        assert f"The {kind} group is missing, or has no model." in found, (kind, found)


# --------------------------------------------------------------------------- #
# A name a group may not have                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["", "Product-Design", "product_design", "product design",
                                  "product.design", "-design", "1st-group"])
def test_a_name_that_is_not_lowercase_letters_digits_and_dashes_is_refused(name):
    doc = json.loads(json.dumps(WITH_GROUP))
    doc["groups"][name] = doc["groups"].pop(GROUP)
    found = _refusals(doc)
    assert any(e.startswith(f"The group {name} is not a group name: ")
               and "lowercase letters, digits and dashes" in e for e in found), found
