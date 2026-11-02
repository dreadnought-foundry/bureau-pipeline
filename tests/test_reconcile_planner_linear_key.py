"""The Reconcile sweep spends the planner's OAuth bucket, not the fleet key (DRE-8716).

WHY. On 2026-11-01 the fleet key (Agent-Bureau, 2,500 requests an hour) ran
down to about 850 remaining. Counted from every run's own `linear-calls:`
lines across the ten repos in config/repo-map.json, 2:00-4:00 PM PT: 4,037
fleet-key requests, and Reconcile alone sent 3,200 of them (79%). The next
spenders were Agent Task (263, 7%), Linear Sync (141) and QA Review (128).
Over the same two hours the planner's OAuth bucket (the same Agent-Bureau
user, metered apart at 5,000 an hour, DRE-5589) never read below 4,296 of
5,000, and it sat at about 4,990 for most of them. So the sweep moves onto
that bucket — plan.yml's shape, copied:

* every step in reconcile.yml that hands a process a Linear key hands it the
  planner's token first and the fleet key when no token is published, and
  `LINEAR_API_KEY_FALLBACK` is always the fleet key, because `||` falls back
  only on an EMPTY secret and `linear_ops.gql` steps onto the fallback on a
  401 and nothing else (tests/test_linear_ops_key_fallback.py);
* the token is declared OPTIONAL, so a stub that passes no secrets of its own
  — or a repo the console has not published it to — runs on the fleet key
  exactly as before;
* the job names the token's home, so the next runs' `linear-budget:` and
  `linear-calls:` lines say `budget: planner-oauth` — and it is handed the
  token's published expiry, so a token known to be dead is never sent;
* the identity still comes from the CALLER (DRE-3630): bureau-harness runs
  this file on the sandbox key and has no planner token, so its lines keep
  saying `budget: sandbox`.

ONE DIFFERENCE FROM plan.yml, on purpose: an escape hatch. A repo whose
variable `LINEAR_SWEEP_BUCKET` is `fleet` (any casing) puts its sweep back on
the fleet key with no code change and no wait for `stable`, should the
shared bucket ever run short. Unset — every repo today — means the planner's
bucket. It is the agents' `LINEAR_AGENT_BUCKET` turned around: the agents
moved only where a repo opted in, and no repo ever did; the sweep is the
spender the CEO asked to move now (DRE-8716), so it moves unless a repo
opts out.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RECONCILE = WORKFLOWS / "reconcile.yml"

sys.path.insert(0, str(ROOT / "scripts"))
import linear_ops  # noqa: E402

OPT_OUT = "vars.LINEAR_SWEEP_BUCKET != 'fleet'"
PRIMARY_EXPR = "${{ " + OPT_OUT + " && secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
FALLBACK_EXPR = "${{ secrets.LINEAR_API_KEY }}"
HOME = "planner-oauth"
HOME_EXPR = "${{ " + OPT_OUT + " && 'planner-oauth' || '' }}"
EXPIRES_EXPR = "${{ vars.LINEAR_PLANNER_KEY_EXPIRES_AT }}"

JOB = "sweep"
# Counted 2026-11-01: Sweep, Dispatch proof runs, Close proof records that
# cannot merge, Proof overdue, Reply to the CEO's Green Light comments, Read
# the switches. A floor, so a renamed key cannot make this pass by finding
# nothing.
KEY_STEP_FLOOR = 6


def _doc() -> dict:
    return yaml.safe_load(RECONCILE.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    # YAML 1.1 reads a bare `on:` key as boolean True.
    return doc.get("on") or doc.get(True) or {}


def _key_steps(doc: dict) -> list[tuple[str, str, dict]]:
    out = []
    for job_id, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            env = step.get("env") or {}
            if "LINEAR_API_KEY" in env:
                out.append((job_id, step.get("name") or step.get("uses", "?"), env))
    return out


def _code_lines() -> list[str]:
    return [line for line in RECONCILE.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]


def _evaluate(expr: str, secrets: dict, variables: dict | None = None):
    """GitHub's expression semantics for the subset these lines use, modeled
    rather than trusted (tests/test_agent_planner_linear_key.py's model, with
    `!=` added), from the Actions expression reference:

    * an unset secret or variable reads as the empty string;
    * `&&` binds tighter than `||`, and both return an OPERAND: `&&` the first
      falsy operand else the last, `||` the first truthy operand else the last;
    * `==` and `!=` between strings IGNORE CASE.
    """
    variables = variables or {}
    m = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expr.strip())
    assert m, f"not a single expression: {expr!r}"

    def atom(text: str):
        text = text.strip()
        cmp = re.fullmatch(r"(.+?)\s*(==|!=)\s*(.+)", text)
        if cmp:
            left, right = atom(cmp.group(1)), atom(cmp.group(3))
            same = str(left).lower() == str(right).lower()
            return same if cmp.group(2) == "==" else not same
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
    """The evaluator itself, so a wrong model cannot make the tests below pass."""
    expr = "${{ vars.X != 'off' && 'a' || 'b' }}"
    assert _evaluate(expr, {}, {}) == "a"
    assert _evaluate(expr, {}, {"X": "on"}) == "a"
    assert _evaluate(expr, {}, {"X": "off"}) == "b"
    assert _evaluate(expr, {}, {"X": "OFF"}) == "b"
    # The documented caveat: a falsy middle operand falls through.
    assert _evaluate("${{ vars.X != 'off' && '' || 'b' }}", {}, {}) == "b"


# ── the wiring ──────────────────────────────────────────────────────────────
def test_every_step_that_holds_a_linear_key_holds_the_planners_first():
    steps = _key_steps(_doc())
    assert len(steps) >= KEY_STEP_FLOOR, len(steps)
    wrong = [(j, s, e["LINEAR_API_KEY"]) for j, s, e in steps
             if e["LINEAR_API_KEY"] != PRIMARY_EXPR]
    assert not wrong, f"reconcile.yml steps still on the fleet key alone: {wrong}"


def test_every_such_step_carries_the_fleet_key_as_its_fallback():
    missing = [(j, s) for j, s, e in _key_steps(_doc())
               if e.get("LINEAR_API_KEY_FALLBACK") != FALLBACK_EXPR]
    assert not missing, f"steps with no fleet fallback for a dead token: {missing}"


def test_the_planner_key_is_declared_optional_and_the_fleet_key_stays_required():
    secrets = _on(_doc())["workflow_call"]["secrets"]
    assert secrets["LINEAR_PLANNER_KEY"] == {"required": False}
    assert secrets["LINEAR_API_KEY"]["required"] is True


def test_the_sweep_job_names_its_home_its_expiry_and_its_callers_identity():
    doc = _doc()
    assert {j for j, _s, _e in _key_steps(doc)} == {JOB}
    env = doc["jobs"][JOB].get("env") or {}
    assert env.get("LINEAR_KEY_HOME") == HOME_EXPR
    assert env.get("LINEAR_PLANNER_KEY_EXPIRES_AT") == EXPIRES_EXPR
    # DRE-3630: never a literal — the sandbox's stub passes its own word.
    assert env.get("LINEAR_IDENTITY") == "${{ inputs.linear_identity }}"


def test_the_planner_key_reaches_a_process_only_as_the_primary():
    lines = _code_lines()
    uses = sum(line.count("secrets.LINEAR_PLANNER_KEY") for line in lines)
    assert uses == len(_key_steps(_doc())), uses


def test_the_opt_out_is_read_only_by_the_key_lines_and_the_home():
    uses = sum(line.count("vars.LINEAR_SWEEP_BUCKET") for line in _code_lines())
    assert uses == len(_key_steps(_doc())) + 1, uses


# ── what each repo gets ─────────────────────────────────────────────────────
PUBLISHED = {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": "Bearer tok"}
UNPUBLISHED = ({"LINEAR_API_KEY": "fleet-key"},
               {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": ""})


@pytest.mark.parametrize("variables", [{}, {"LINEAR_SWEEP_BUCKET": ""},
                                       {"LINEAR_SWEEP_BUCKET": "planner"}])
def test_with_the_token_published_the_sweep_spends_the_token(variables):
    env_job = _doc()["jobs"][JOB]["env"]
    for job, step, env in _key_steps(_doc()):
        assert _evaluate(env["LINEAR_API_KEY"], PUBLISHED, variables) == "Bearer tok", (job, step)
        assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], PUBLISHED, variables) == "fleet-key"
    assert _evaluate(env_job["LINEAR_KEY_HOME"], PUBLISHED, variables) == HOME


@pytest.mark.parametrize("secrets", UNPUBLISHED)
def test_with_no_token_published_the_sweep_stays_on_the_fleet_key(secrets):
    for job, step, env in _key_steps(_doc()):
        assert _evaluate(env["LINEAR_API_KEY"], secrets, {}) == "fleet-key", (job, step)
        assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets, {}) == "fleet-key"


@pytest.mark.parametrize("word", ["fleet", "FLEET", "Fleet"])
def test_a_repo_that_opts_out_sweeps_on_the_fleet_key_and_names_no_home(word):
    variables = {"LINEAR_SWEEP_BUCKET": word}
    env_job = _doc()["jobs"][JOB]["env"]
    for job, step, env in _key_steps(_doc()):
        assert _evaluate(env["LINEAR_API_KEY"], PUBLISHED, variables) == "fleet-key", (job, step)
    assert _evaluate(env_job["LINEAR_KEY_HOME"], PUBLISHED, variables) == ""


# ── what the budget line will say ───────────────────────────────────────────
def _bucket(monkeypatch, *, identity, primary, fallback, home):
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, identity)
    monkeypatch.setenv("LINEAR_API_KEY", primary)
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, fallback)
    monkeypatch.setenv(linear_ops.HOME_ENV, home)
    return linear_ops.spending_bucket()


def test_a_production_sweep_on_the_token_says_planner_oauth(monkeypatch):
    assert _bucket(monkeypatch, identity="fleet", primary="Bearer tok",
                   fallback="fleet-key", home=HOME) == HOME


def test_a_production_sweep_with_no_token_says_fleet(monkeypatch):
    assert _bucket(monkeypatch, identity="fleet", primary="fleet-key",
                   fallback="fleet-key", home=HOME) == "fleet"


def test_the_sandbox_sweep_still_says_sandbox(monkeypatch):
    """bureau-harness: no planner token, its LINEAR_API_KEY is the sandbox
    key, so primary and fallback are the same key and the line names the
    caller's identity — the home is never borrowed by another identity."""
    assert _bucket(monkeypatch, identity="sandbox", primary="sandbox-key",
                   fallback="sandbox-key", home=HOME) == "sandbox"
    assert _bucket(monkeypatch, identity="sandbox", primary="Bearer tok",
                   fallback="sandbox-key", home=HOME) == "sandbox"


# ── the declaration says so ─────────────────────────────────────────────────
def test_the_planner_oauth_home_names_the_sweep_as_a_reader():
    import json
    doc = json.loads((ROOT / "config" / "linear-identities.json").read_text(encoding="utf-8"))
    fleet = next(i for i in doc["identities"] if i["name"] == "fleet")
    home = next(h for h in fleet["homes"] if h["name"] == HOME)
    assert "reconcile.yml" in home["lives_in"]
    assert "LINEAR_SWEEP_BUCKET" in home["lives_in"]
