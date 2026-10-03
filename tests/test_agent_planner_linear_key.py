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
  included, hands it the planner's token ONLY when the calling repo has opted
  in — `vars.LINEAR_AGENT_BUCKET == 'planner' && secrets.LINEAR_PLANNER_KEY
  || secrets.LINEAR_API_KEY` — and the fleet key otherwise (Stage 2 review
  item 29, finding H5b). Without the opt-in, a repo whose stub passes
  `secrets: inherit` (portico, the demo) would start spending the planner's
  bucket the moment `stable` advanced past this change, with nobody deciding;
  the planner's own plan.yml needs no opt-in and keeps its expression;
* beside it, `LINEAR_API_KEY_FALLBACK` is always the fleet key, because `||`
  falls back only on an EMPTY secret, and a published token that later dies
  still holds a value — `linear_ops.gql` steps onto the fallback on a 401 and
  nothing else (tests/test_linear_ops_key_fallback.py);
* the secret is declared OPTIONAL, so a stub that never passes it — every
  explicit-secret consumer stub until this change is on `stable` — runs
  exactly as before, on the fleet key;
* each job that holds the key names its identity and its key's home, so the
  `linear-budget:` line says `budget: planner-oauth` while the token is what
  is being spent — and the home follows the same opt-in, so a repo that has
  not opted in never names it;
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

OPT_IN = "vars.LINEAR_AGENT_BUCKET == 'planner'"
PRIMARY_EXPR = (
    "${{ " + OPT_IN + " && secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
)
FALLBACK_EXPR = "${{ secrets.LINEAR_API_KEY }}"
HOME = "planner-oauth"
HOME_EXPR = "${{ " + OPT_IN + " && 'planner-oauth' || '' }}"

# The four agent workflows this change moves, and the job in each that holds
# the key. A floor of key-holding steps per workflow, so a renamed key cannot
# make this pass by finding nothing (counted 2026-10-02: 10, 7, 4, 5). The
# critic and the verifier each hold one fewer since Stage 2 fix #14: their two
# card reads became one snapshot step, and the steps that read the snapshot
# hold no key (tests/test_review_card_snapshot_wiring.py).
AGENT_WORKFLOWS = {
    "agent-task.yml": ("execute", 10),
    "agent-fix.yml": ("fix", 7),
    "qa-review.yml": ("review", 3),
    "verify.yml": ("verify", 4),
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


def _evaluate(expr: str, secrets: dict, variables: dict | None = None):
    """GitHub's expression semantics for the subset these lines use, modeled
    rather than trusted (as in tests/test_planner_linear_key.py), from the
    Actions expression reference:

    * an unset secret or variable reads as the empty string;
    * `&&` binds tighter than `||`, and both return an OPERAND, not a boolean:
      `&&` the first falsy operand else the last, `||` the first truthy
      operand else the last — the documented `cond && a || b` idiom, including
      its documented caveat that a falsy `a` falls through to `b` (which is
      exactly what sends an opted-in repo with no published token to the
      fleet key);
    * `==` between strings IGNORES CASE, so `PLANNER` opts in too.
    """
    variables = variables or {}
    m = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expr.strip())
    assert m, f"not a single expression: {expr!r}"

    def atom(text: str):
        text = text.strip()
        cmp = re.fullmatch(r"(.+?)\s*==\s*(.+)", text)
        if cmp:
            left, right = atom(cmp.group(1)), atom(cmp.group(2))
            return str(left).lower() == str(right).lower()
        lit = re.fullmatch(r"'([^']*)'", text)
        if lit:
            return lit.group(1)
        if text.startswith("secrets."):
            return secrets.get(text[len("secrets."):], "")
        if text.startswith("vars."):
            return variables.get(text[len("vars."):], "")
        raise AssertionError(f"operand this model does not know: {text!r}")

    def conj(text: str):
        value = None
        for part in text.split("&&"):
            value = atom(part)
            if not value:
                return value
        return value

    value = None
    for part in m.group(1).split("||"):
        value = conj(part)
        if value:
            return value
    return value


def test_the_model_reads_the_documented_ternary_idiom_as_github_does():
    """The evaluator itself, on the documented example's shape, so a wrong
    model cannot make the wiring tests below pass."""
    expr = "${{ vars.X == 'main' && 'a' || 'b' }}"
    assert _evaluate(expr, {}, {"X": "main"}) == "a"
    assert _evaluate(expr, {}, {"X": "MAIN"}) == "a"
    assert _evaluate(expr, {}, {"X": "other"}) == "b"
    assert _evaluate(expr, {}, {}) == "b"
    # The caveat: a falsy middle operand falls through.
    assert _evaluate("${{ vars.X == 'main' && '' || 'b' }}", {}, {"X": "main"}) == "b"


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
    # The home follows the opt-in: `planner-oauth` only where the repo chose
    # the planner's bucket, and empty — which linear_ops never prints, because
    # it is not a declared home — everywhere else.
    assert env.get("LINEAR_KEY_HOME") == HOME_EXPR, name
    # Whose bucket is pinned by tests/test_linear_identity_declared.py; here
    # only that the job says one at all.
    assert env.get("LINEAR_IDENTITY"), name


# ── fleet unless the repo opts in ───────────────────────────────────────────
PUBLISHED = {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": "Bearer tok"}
UNPUBLISHED = ({"LINEAR_API_KEY": "fleet-key"},
               {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": ""})


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_planner_token_needs_repo_opt_in(name):
    """Stage 2 review P7 / H5b: a published token is NOT enough. A repo whose
    stub passes `secrets: inherit` holds the token the moment the console
    publishes it, so without this gate it would flip onto the planner's bucket
    when `stable` advanced past this change — nobody deciding, nothing on the
    board looking different. The variable is the decision."""
    env_job = _doc(name)["jobs"][AGENT_WORKFLOWS[name][0]].get("env") or {}
    for variables in ({}, {"LINEAR_AGENT_BUCKET": ""},
                      {"LINEAR_AGENT_BUCKET": "fleet"},
                      {"LINEAR_AGENT_BUCKET": "planner-oauth"}):
        for job, step, env in _key_steps(_doc(name)):
            assert _evaluate(env["LINEAR_API_KEY"], PUBLISHED, variables) == "fleet-key", (
                variables, job, step)
        assert _evaluate(env_job["LINEAR_KEY_HOME"], PUBLISHED, variables) == "", variables


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
@pytest.mark.parametrize("word", ["planner", "PLANNER", "Planner"])
def test_an_opted_in_repo_with_the_token_published_spends_the_token(name, word):
    """`==` on strings ignores case in GitHub expressions, so every casing of
    `planner` is the same decision — said here so nobody is surprised."""
    variables = {"LINEAR_AGENT_BUCKET": word}
    env_job = _doc(name)["jobs"][AGENT_WORKFLOWS[name][0]].get("env") or {}
    for job, step, env in _key_steps(_doc(name)):
        assert _evaluate(env["LINEAR_API_KEY"], PUBLISHED, variables) == "Bearer tok", (job, step)
        assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], PUBLISHED, variables) == "fleet-key"
    assert _evaluate(env_job["LINEAR_KEY_HOME"], PUBLISHED, variables) == HOME


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_an_opted_in_repo_with_no_token_published_stays_on_the_fleet_key(name):
    variables = {"LINEAR_AGENT_BUCKET": "planner"}
    for secrets in UNPUBLISHED:
        for job, step, env in _key_steps(_doc(name)):
            assert _evaluate(env["LINEAR_API_KEY"], secrets, variables) == "fleet-key", (job, step)
            assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets, variables) == "fleet-key"


@pytest.mark.parametrize("name", sorted(AGENT_WORKFLOWS))
def test_the_opt_in_is_the_callers_variable_and_read_nowhere_else(name):
    """`vars` in a reusable workflow resolves from the CALLER's repository —
    the same reading `vars.CLAUDE_AUTH_MODE` relies on. Every line that names the variable is one of the key lines or the job's
    home, and nothing else in the file reads it."""
    uses = sum(line.count("vars.LINEAR_AGENT_BUCKET") for line in _code_lines(name))
    assert uses == len(_key_steps(_doc(name))) + 1, (name, uses)


# ── a token known to be dead is never sent (review item 30) ─────────────────
EXPIRES_EXPR = "${{ vars.LINEAR_PLANNER_KEY_EXPIRES_AT }}"

# Every job in the fleet that can hold the planner's token: the four agent
# jobs, and the planner's own two — the gate lives in linear_ops, which every
# one of them runs, and a planner sending a dead token is the same 401 storm.
TOKEN_JOBS = {
    **{name: (job,) for name, (job, _floor) in AGENT_WORKFLOWS.items()},
    "plan.yml": ("plan", "publish"),
}


@pytest.mark.parametrize("name", sorted(TOKEN_JOBS))
def test_every_job_that_can_hold_the_token_is_told_when_it_expires(name):
    """There is no clock in a GitHub expression, so the comparison happens in
    linear_ops (tests/test_linear_ops_key_fallback.py); the job's only part is
    handing it the published expiry. Job level, like the identity: every
    step, the agent's own included, inherits it."""
    doc = _doc(name)
    for job_id in TOKEN_JOBS[name]:
        env = doc["jobs"][job_id].get("env") or {}
        assert env.get("LINEAR_PLANNER_KEY_EXPIRES_AT") == EXPIRES_EXPR, (name, job_id)
    holders = {j for j, _s, _e in _key_steps(doc)}
    assert holders <= set(TOKEN_JOBS[name]), (name, holders)


def test_the_expiry_is_read_by_exactly_the_jobs_that_can_hold_the_token():
    readers = {path.name for path in WORKFLOWS.glob("*.yml")
               if any("LINEAR_PLANNER_KEY_EXPIRES_AT" in line
                      for line in _code_lines(path.name))}
    assert readers == set(TOKEN_JOBS), sorted(readers ^ set(TOKEN_JOBS))


# ── the fence ───────────────────────────────────────────────────────────────
def test_no_other_workflow_reads_the_planners_token():
    """Gates, medic, sweeps, relays and releases stay on the fleet key. A new
    reader is a decision about who spends the agents' bucket, and it is made
    here, by editing PLANNER_KEY_READERS — not by a quiet copy of a line."""
    readers = {path.name for path in WORKFLOWS.glob("*.yml")
               if any("LINEAR_PLANNER_KEY" in line for line in _code_lines(path.name))}
    assert readers == PLANNER_KEY_READERS, sorted(readers ^ PLANNER_KEY_READERS)


def test_only_the_four_agent_workflows_ask_for_the_opt_in():
    """The planner's own plan.yml has spent its bucket since DRE-5589 without
    asking; the opt-in is for the agents that joined it, and only them."""
    askers = {path.name for path in WORKFLOWS.glob("*.yml")
              if any("LINEAR_AGENT_BUCKET" in line for line in _code_lines(path.name))}
    assert askers == set(AGENT_WORKFLOWS), sorted(askers ^ set(AGENT_WORKFLOWS))


# ── the agents never refresh ────────────────────────────────────────────────
# Spelled in pieces so this file does not trip its own check.
_FORBIDDEN = (
    re.compile("oauth" + "/token", re.I),
    re.compile("client" + r"[\s_-]?" + "id", re.I),
    re.compile("client" + r"[\s_-]?" + "secret", re.I),
)

CHANGED = (
    *(f".github/workflows/{name}" for name in sorted(AGENT_WORKFLOWS)),
    ".github/workflows/plan.yml",
    "agents.yaml",
    "config/linear-identities.json",
    "scripts/linear_ops.py",
    "tests/test_agent_planner_linear_key.py",
    "tests/test_linear_ops_key_fallback.py",
)


def test_nothing_this_change_touches_names_the_token_endpoint_or_the_apps_credentials():
    hits = []
    for rel in CHANGED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in _FORBIDDEN:
            hits.extend(f"{rel}: {m.group(0)!r}" for m in pattern.finditer(text))
    assert not hits, hits
