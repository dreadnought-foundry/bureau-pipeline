"""The build, fix, review and verify agents spend the planner's bucket too.

WHY (Stage 2 design, fix #12). Since DRE-5589 the planner reads Linear on its
own OAuth token: the same Agent-Bureau user, metered apart from the fleet key
at 5,000 an hour against 2,500. Every other agent workflow still read the
fleet key alone, so the fleet key's hour carried every build, fix, review and
verification on top of the sweeps, the gates, the relay and the console. The
busiest hour measured before this change: agent tasks 275-330, agent fix
60-90, QA 56, plus most of an untraceable 600-950 — against a 2,500 limit.

WHAT THIS PINS, read off the workflow files themselves, for exactly the four
workflows below — plan.yml's pattern copied, not reinvented
(tests/test_planner_linear_key.py pins the original):

* every step that hands a process a Linear key, the agent's own step
  included, hands it `secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY`;
* beside it, `LINEAR_API_KEY_FALLBACK` is always the fleet key, because `||`
  falls back only on an EMPTY secret, and a published token that later dies
  still holds a value — `linear_ops.gql` steps onto the fallback on a 401 and
  nothing else (tests/test_linear_ops_key_fallback.py);
* the secret is declared OPTIONAL, so a stub that never passes it — every
  explicit-secret consumer stub until this change is on `stable` — runs
  exactly as before, on the fleet key;
* each job that holds the key names its identity and its key's home, so the
  `linear-budget:` line says `budget: planner-oauth` while the token is what
  is being spent;
* and NOTHING ELSE reads the planner's token. The gates, the medic, the sweeps
  and the release workflows stay on the fleet key: the planner's bucket is for
  the agents, and a gate that quietly moved onto it would be one more spender
  on a bucket sized for them.

The token is never refreshed here. The console is its only refresher
(DRE-2532), so no file this change touches may name the token endpoint or the
OAuth app's credentials — checked below, not promised.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

PRIMARY_EXPR = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
FALLBACK_EXPR = "${{ secrets.LINEAR_API_KEY }}"
HOME = "planner-oauth"

# The four agent workflows this change moves, and the job in each that holds
# the key. A floor of key-holding steps per workflow, so a renamed key cannot
# make this pass by finding nothing (counted 2026-10-02: 10, 7, 4, 5).
AGENT_WORKFLOWS = {
    "agent-task.yml": ("execute", 10),
    "agent-fix.yml": ("fix", 7),
    "qa-review.yml": ("review", 4),
    "verify.yml": ("verify", 5),
}

# Every workflow that may read the planner's token. plan.yml is the original.
PLANNER_KEY_READERS = {"plan.yml", *AGENT_WORKFLOWS}


def _doc(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # YAML 1.1 reads a bare `on:` key as boolean True.
    return doc.get("on") or doc.get(True) or {}


def _key_steps(doc: dict) -> list[tuple[str, str, dict]]:
    """(job, step name, env) for every step that hands a process a Linear key."""
    out = []
    for job_id, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            env = step.get("env") or {}
            if "LINEAR_API_KEY" in env:
                out.append((job_id, step.get("name") or step.get("uses", "?"), env))
    return out


def _evaluate(expr: str, secrets: dict) -> str:
    """GitHub's `a || b` over the secrets context: an unset secret reads as
    the empty string, and `||` returns the first operand that is not falsy,
    else the last. Modelled rather than trusted, as in
    tests/test_planner_linear_key.py."""
    m = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expr.strip())
    assert m, f"not a single expression: {expr!r}"
    value = ""
    for operand in (o.strip() for o in m.group(1).split("||")):
        assert operand.startswith("secrets."), operand
        value = secrets.get(operand[len("secrets."):], "")
        if value:
            return value
    return value


def _code_lines(name: str) -> list[str]:
    return [line for line in (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]


# ── the wiring ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_every_step_that_holds_a_linear_key_holds_the_planners_first(name):
    _job, floor = AGENT_WORKFLOWS[name]
    steps = _key_steps(_doc(name))
    assert len(steps) >= floor, (name, len(steps))
    wrong = [(j, s, e["LINEAR_API_KEY"]) for j, s, e in steps
             if e["LINEAR_API_KEY"] != PRIMARY_EXPR]
    assert not wrong, f"{name}: steps still on the fleet key alone: {wrong}"


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_every_such_step_carries_the_fleet_key_as_its_fallback(name):
    missing = [(j, s) for j, s, e in _key_steps(_doc(name))
               if e.get("LINEAR_API_KEY_FALLBACK") != FALLBACK_EXPR]
    assert not missing, f"{name}: steps with no fleet fallback for a dead token: {missing}"


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_the_agents_own_step_holds_the_planners_key_and_the_fallback(name):
    """The agent reaches Linear by running linear_ops.py from Bash, so its
    process is the one that spends the most — it must hold both keys too.
    qa-review's critic holds no key at all, on purpose (DRE-2052 + DRE-2696,
    tests/test_agent_linear_key.py), and stays that way."""
    for job in (_doc(name).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if not str(step.get("uses") or "").startswith("anthropics/claude-code-action"):
                continue
            env = step.get("env") or {}
            if name == "qa-review.yml":
                assert "LINEAR_API_KEY" not in env, step.get("name")
                assert "LINEAR_API_KEY_FALLBACK" not in env, step.get("name")
                continue
            assert env.get("LINEAR_API_KEY") == PRIMARY_EXPR, (name, step.get("name"))
            assert env.get("LINEAR_API_KEY_FALLBACK") == FALLBACK_EXPR, (name, step.get("name"))


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_no_step_reads_the_planner_key_under_any_other_name(name):
    """The planner key reaches a process only as the primary; anywhere else it
    would be a copy nothing falls back from."""
    uses = sum(line.count("secrets.LINEAR_PLANNER_KEY") for line in _code_lines(name))
    assert uses == len(_key_steps(_doc(name))), (name, uses)


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_the_planner_key_is_declared_optional_and_the_fleet_key_stays_required(name):
    """Optional, so a caller that does not pass it is unchanged: the hazard is
    an explicit-secret stub that passes a secret the workflow on its channel
    does not declare yet — that fails at startup — so the stubs follow this
    change onto `stable`, never the other way round."""
    secrets = _on(_doc(name))["workflow_call"]["secrets"]
    assert secrets["LINEAR_PLANNER_KEY"] == {"required": False}
    assert secrets["LINEAR_API_KEY"]["required"] is True


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_the_job_that_holds_the_key_names_its_home(name):
    doc = _doc(name)
    job_id, _floor = AGENT_WORKFLOWS[name]
    assert {j for j, _s, _e in _key_steps(doc)} == {job_id}
    env = doc["jobs"][job_id].get("env") or {}
    assert env.get("LINEAR_KEY_HOME") == HOME, name
    # Whose bucket is pinned by tests/test_linear_identity_declared.py; here
    # only that the job says one at all.
    assert env.get("LINEAR_IDENTITY"), name


# ── absent means today ──────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_with_the_planner_key_absent_every_step_resolves_to_the_fleet_key(name):
    for secrets in ({"LINEAR_API_KEY": "fleet-key"},
                    {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": ""}):
        for job, step, env in _key_steps(_doc(name)):
            assert _evaluate(env["LINEAR_API_KEY"], secrets) == "fleet-key", (job, step)
            assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets) == "fleet-key"


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_with_the_planner_key_published_the_primary_is_the_token(name):
    secrets = {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": "Bearer tok"}
    for job, step, env in _key_steps(_doc(name)):
        assert _evaluate(env["LINEAR_API_KEY"], secrets) == "Bearer tok", (job, step)
        assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets) == "fleet-key"


# ── the fence ───────────────────────────────────────────────────────────────
def test_no_other_workflow_reads_the_planners_token():
    """Gates, medic, sweeps, relays and releases stay on the fleet key. A new
    reader is a decision about who spends the agents' bucket, and it is made
    here, by editing PLANNER_KEY_READERS — not by a quiet copy of a line."""
    readers = {path.name for path in WORKFLOWS.glob("*.yml")
               if any("LINEAR_PLANNER_KEY" in line for line in _code_lines(path.name))}
    assert readers == PLANNER_KEY_READERS, sorted(readers ^ PLANNER_KEY_READERS)


# ── the agents never refresh ────────────────────────────────────────────────
# Spelled in pieces so this file does not trip its own check.
_FORBIDDEN = (
    re.compile("oauth" + "/token", re.I),
    re.compile("client" + r"[\s_-]?" + "id", re.I),
    re.compile("client" + r"[\s_-]?" + "secret", re.I),
)

CHANGED = (
    *(f".github/workflows/{name}" for name in sorted(AGENT_WORKFLOWS)),
    "agents.yaml",
    "config/linear-identities.json",
    "tests/test_agent_planner_linear_key.py",
)


def test_nothing_this_change_touches_names_the_token_endpoint_or_the_apps_credentials():
    hits = []
    for rel in CHANGED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in _FORBIDDEN:
            hits.extend(f"{rel}: {m.group(0)!r}" for m in pattern.finditer(text))
    assert not hits, hits
