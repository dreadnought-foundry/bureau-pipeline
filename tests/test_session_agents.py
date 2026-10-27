"""DRE-6833 — an agent the pipeline does not run can be put in a group, and
the selector never mistakes it for a pipeline role.

THE INCIDENT. On 2026-10-10 the CEO dragged Designer into Builders on Settings ›
Agents & models, and the save was refused: Designer has no entry in the agent
registry (agents.yaml), because it runs in a person's session rather than a
pipeline workflow.

THE FIX, as this file pins it:

  * The code names the agents that run in a person's session under
    `session_agents`. Such an agent may join any group of the home, or none.
  * A group never makes it a pipeline role: no agents.yaml entry, not in
    `model_fallback.AGENT_KINDS`, and the selector answers it exactly as it
    answers an unknown name, from the YAML and from the defaults in the code
    alike.
  * Rule 10 (only the planner is in the Planners) exempts session-run agents
    and nobody else, and a name in neither agents.yaml, the session agents nor
    the home's `added_agents` is refused.

WHERE A TEST STANDS (DRE-6988, DRE-8021). Since DRE-6977 a save writes the home
and opens no pull request, and DRE-8021 deleted the route that did, with its
tests. So every test here runs on the frozen home document
(`model_fixture.fixture_home()`): a rule test judges it with
`model_home.home_errors` on the frozen prices and code-side facts, and a
selection test puts the agent in its group with `model_fixture.move_agent` and
hands the document to a tree built with `fixture_tree(..., regenerate=False)`
through `home_env` — it never writes the tree's `models.yaml`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_session_agents.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import model_fallback as mf  # noqa: E402
import model_fixture  # noqa: E402
import model_home  # noqa: E402
from model_fixture import fixture_home, home_env, move_agent  # noqa: E402

#: Where every case starts (DRE-6935, DRE-6988): the frozen home document
#: (tests/model_fixture.py), every session-run agent in no group. The
#: session-run agents are a code-side fact of the frozen models.yaml; the
#: groups are the document's.
_FROZEN_HOME = fixture_home()
FROZEN_SESSION = list(model_fixture.fixture_config()["session_agents"])
FROZEN_GROUPS = list(_FROZEN_HOME["groups"])
SLUGS = set(json.loads((ROOT / "config" / "repo-map.json").read_text()))


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _home_env(tree: Path, doc: dict) -> dict:
    """`doc` as the tree's home (`model_fixture.home_env`), every model
    available, no network."""
    env = home_env(tree, doc, BUREAU_FAKE_AVAILABLE="{}")
    env.pop("GITHUB_STEP_SUMMARY", None)
    return env


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


def _refusals(doc: dict) -> list[str]:
    """`model_home.home_errors` on `doc`, on the frozen prices and facts: what
    every writer of the home checks before it writes."""
    return model_home.home_errors(doc, model_fixture.fixture_prices(),
                                  _frozen_side(), SLUGS)


def _fresh_name(wanted: str = "data-analyst") -> str:
    """`wanted`, or `wanted-2`, … — a name the frozen files and document hold
    nowhere."""
    side = _frozen_side()
    taken = (set(side["agents"] or ()) | set(side["session_agents"])
             | set(_FROZEN_HOME["agents"]) | set(_FROZEN_HOME.get("added_agents") or {}))
    name, n = wanted, 1
    while name in taken:
        n += 1
        name = f"{wanted}-{n}"
    return name


# --------------------------------------------------------------------------- #
# 1. The home's rules on a session agent                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("agent", FROZEN_SESSION)
def test_a_session_agent_may_be_in_any_group_of_the_home_or_in_none(agent):
    assert agent not in _FROZEN_HOME["agents"]
    assert _refusals(_FROZEN_HOME) == []
    for group in FROZEN_GROUPS:
        assert _refusals(move_agent(_FROZEN_HOME, agent, group)) == [], group


def test_rule_10_exempts_the_session_agents_and_nobody_else():
    owners = " and ".join(mf.JUDGEMENT_ROLES)
    builder = next(name for name, group in _FROZEN_HOME["agents"].items()
                   if group == mf.WORKHORSE_KIND)
    found = _refusals(move_agent(_FROZEN_HOME, builder, mf.JUDGEMENT_KIND))
    assert (f"Only {owners} and the session agents may be in the "
            f"{mf.JUDGEMENT_KIND} group, and {builder} is in it.") in found, found
    session = FROZEN_SESSION[0]
    assert _refusals(move_agent(_FROZEN_HOME, session, mf.JUDGEMENT_KIND)) == []


def test_an_added_agent_joins_a_group_and_a_name_in_neither_list_is_refused():
    new = _fresh_name()
    doc = move_agent(_FROZEN_HOME, new, mf.WORKHORSE_KIND)
    assert (f"{new} is not an agent: it is in neither agents.yaml, the session "
            "agents nor added_agents.") in _refusals(doc)
    doc["added_agents"] = {**doc.get("added_agents", {}), new: "Answers questions from our data"}
    assert _refusals(doc) == []


# --------------------------------------------------------------------------- #
# 2. Every session agent into every group, and the selector never notices      #
# --------------------------------------------------------------------------- #

def _select(tree: Path, role: str, env: dict) -> tuple[str, str]:
    """`(model, note)` from the real `model_fallback.py select` in `tree`."""
    proc = subprocess.run(
        [sys.executable, str(tree / "scripts" / "model_fallback.py"), "select", role],
        capture_output=True, text=True, env=env, cwd=tree)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    assert "model-home: read" in proc.stderr, proc.stderr
    note = next(line for line in proc.stderr.splitlines()
                if line.startswith("model-policy:"))
    return proc.stdout.strip(), note


def _reads(tree: Path, role: str, env: dict) -> dict:
    """What the tree's `model_fallback` answers for `role`, having read the
    home `env` names."""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(tree / 'scripts')!r})\n"
        "import model_fallback as mf\n"
        "assert mf.use_home() == 'read', mf.HOME_STATUS\n"
        f"role = {role!r}\n"
        "print(json.dumps({'kind': mf.kind_for(role), 'ladder': mf.ladder_for(role),\n"
        "    'default': mf.LADDER, 'judgement': mf.LADDERS[mf.KINDS['judgement']],\n"
        "    'in_agent_kinds': role in mf.AGENT_KINDS,\n"
        "    'session': mf.CONFIG.get('session_agents'),\n"
        "    'agents': mf.CONFIG['agents']}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, cwd=tree)
    assert proc.returncode == 0, proc.stderr
    assert "REFUSING" not in proc.stderr, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_answered_as_unknown(tree: Path, env: dict, agent: str, group: str, read: str):
    reads = _reads(tree, agent, env)
    # The home really carries the move — a refused or unread document would
    # make every assertion below pass for the wrong reason.
    assert reads["agents"].get(agent) == group, (read, reads)
    assert agent in reads["session"], read
    assert reads["in_agent_kinds"] is False, read
    assert reads["kind"] == "workhorse", read
    assert reads["ladder"] == reads["default"], read
    assert reads["ladder"] != reads["judgement"], read
    model, note = _select(tree, agent, env)
    unknown_model, unknown_note = _select(tree, "nosuchagent", env)
    assert model == reads["default"][0], read
    assert (model, note) == (unknown_model,
                             unknown_note.replace("nosuchagent", agent)), read
    # The planner's TOP may lead the builders since 2026-10-10 (DRE-6785);
    # what a session agent never reaches is a Fable on the planner's ladder.
    fables = [m for m in reads["judgement"] if m.startswith("claude-fable")]
    assert model not in fables, read


@pytest.mark.parametrize("agent", FROZEN_SESSION)
@pytest.mark.parametrize("group", FROZEN_GROUPS)
def test_a_session_agent_joins_any_group_and_selects_like_an_unknown_name(
        tmp_path, agent, group):
    # On the frozen document, where the Planners' leading model is on no
    # Builders rung, so selecting by the group would show (DRE-6935).
    assert agent not in _FROZEN_HOME["agents"]
    doc = move_agent(_FROZEN_HOME, agent, group)
    tree = model_fixture.fixture_tree(tmp_path / "tree", regenerate=False)
    env = _home_env(tree, doc)
    _assert_answered_as_unknown(tree, env, agent, group, "with the YAML")

    # The degrade mirror: take the YAML away so `_load_config` falls back to
    # the defaults in the code for the code-side facts too — which agents run
    # in a person's session among them.
    (tree / "config" / "models.yaml").unlink()
    _assert_answered_as_unknown(tree, env, agent, group, "from the mirror")


def test_the_mirror_carries_session_agents_beside_ungrouped():
    defaults = mf.code_defaults()
    assert defaults["session_agents"] == yaml.safe_load(CONFIG.read_text())["session_agents"]
    keys = list(defaults)
    assert keys.index("session_agents") == keys.index("ungrouped") + 1
