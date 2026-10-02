"""The planner spends its own Linear bucket when it has one (DRE-5589).

THE NUMBERS. The planner cap is 2 (`config/planner-queue.json`, DRE-5326)
because planners and sweeps share one Linear key's 2,500 requests an hour. On
2026-10-01 at 21:41 PT a Linear OAuth app authorized as the Agent-Bureau user
was measured on its own 5,000-an-hour bucket while still writing as
Agent-Bureau: at one instant the key read 351/2500 and the token 4998/5000,
and 61 requests on the token moved the key by only 6. DRE-5587 makes the
console publish that token, already `Bearer <access token>`, as the repo
Actions secret `LINEAR_PLANNER_KEY`.

WHAT THIS PINS, read off the workflow files themselves:

* every step in plan.yml that hands a Linear key to a process hands it
  `secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY` — the planner's
  bucket when the console has published one, the fleet key exactly as before
  when it has not;
* beside it, `LINEAR_API_KEY_FALLBACK` is always the fleet key, because `||`
  falls back only on an EMPTY secret: a published token that later dies still
  holds a value, and `linear_ops.gql` needs the fleet key in hand to recover
  from the 401 (tests/test_linear_ops_key_fallback.py);
* the secret is declared optional, so a stub that never passes it changes
  nothing, and this repo's own stub passes it by `secrets: inherit`;
* each job says which home its key comes from, so the `linear-budget:` line
  can name the bucket it spent.

And the planner never refreshes the token. The console is the only refresher
(DRE-2532): one refresh anywhere else spends the console's link and stops the
chain until somebody re-authorizes. So no file this card touches may name the
token endpoint or the OAuth app's own credentials — checked below, not
promised.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PLAN = WORKFLOWS / "plan.yml"
SELF_PLAN = WORKFLOWS / "self-plan.yml"
IDENTITIES = ROOT / "config" / "linear-identities.json"

PRIMARY_EXPR = "${{ secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY }}"
FALLBACK_EXPR = "${{ secrets.LINEAR_API_KEY }}"
HOME = "planner-oauth"


def _plan() -> dict:
    return yaml.safe_load(PLAN.read_text(encoding="utf-8"))


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
    """GitHub's `a || b` over the secrets context, the part this file uses.

    An unset secret reads as the empty string, and `||` returns the first
    operand that is not falsy — else the last. That is the whole of the
    semantics the planner's fallback rests on, so it is modelled here rather
    than trusted.
    """
    m = re.fullmatch(r"\$\{\{\s*(.+?)\s*\}\}", expr.strip())
    assert m, f"not a single expression: {expr!r}"
    operands = [o.strip() for o in m.group(1).split("||")]
    value = ""
    for operand in operands:
        assert operand.startswith("secrets."), operand
        value = secrets.get(operand[len("secrets."):], "")
        if value:
            return value
    return value


# ── the wiring ──────────────────────────────────────────────────────────────
def test_every_step_that_holds_a_linear_key_holds_the_planners_first():
    steps = _key_steps(_plan())
    # Seventy today across `plan` and `publish`. A floor, so a renamed
    # key cannot make this pass by finding nothing.
    assert len(steps) >= 60, len(steps)
    wrong = [(j, s, e["LINEAR_API_KEY"]) for j, s, e in steps
             if e["LINEAR_API_KEY"] != PRIMARY_EXPR]
    assert not wrong, f"steps still on the fleet key alone: {wrong[:5]}"


def test_every_such_step_carries_the_fleet_key_as_its_fallback():
    missing = [(j, s) for j, s, e in _key_steps(_plan())
               if e.get("LINEAR_API_KEY_FALLBACK") != FALLBACK_EXPR]
    assert not missing, f"steps with no fleet fallback for a dead token: {missing[:5]}"


def test_no_step_reads_the_planner_key_under_any_other_name():
    """The planner key reaches a process only as the primary; anywhere else it
    would be a copy nothing falls back from."""
    code = [line for line in PLAN.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]
    uses = sum(line.count("secrets.LINEAR_PLANNER_KEY") for line in code)
    assert uses == len(_key_steps(_plan())), uses


def test_the_planner_key_is_declared_optional_and_the_fleet_key_stays_required():
    secrets = _on(_plan())["workflow_call"]["secrets"]
    assert secrets["LINEAR_PLANNER_KEY"] == {"required": False}
    assert secrets["LINEAR_API_KEY"]["required"] is True


def test_every_job_that_holds_the_key_names_its_home():
    doc = _plan()
    jobs = {j for j, _s, _e in _key_steps(doc)}
    assert jobs == {"plan", "publish"}, jobs
    for job_id in jobs:
        env = doc["jobs"][job_id].get("env") or {}
        assert env.get("LINEAR_IDENTITY") == "fleet", job_id
        assert env.get("LINEAR_KEY_HOME") == HOME, job_id


def test_this_repos_stub_passes_every_secret_through():
    stub = yaml.safe_load(SELF_PLAN.read_text(encoding="utf-8"))
    call = stub["jobs"]["call"]
    assert call["uses"].startswith("dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@")
    assert call.get("secrets") == "inherit"


# ── absent means today ──────────────────────────────────────────────────────
def test_with_the_planner_key_absent_every_step_resolves_to_the_fleet_key():
    for secrets in ({"LINEAR_API_KEY": "fleet-key"},
                    {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": ""}):
        for job, step, env in _key_steps(_plan()):
            assert _evaluate(env["LINEAR_API_KEY"], secrets) == "fleet-key", (job, step)
            assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets) == "fleet-key"


def test_with_the_planner_key_published_the_primary_is_the_token():
    secrets = {"LINEAR_API_KEY": "fleet-key", "LINEAR_PLANNER_KEY": "Bearer tok"}
    for job, step, env in _key_steps(_plan()):
        assert _evaluate(env["LINEAR_API_KEY"], secrets) == "Bearer tok", (job, step)
        assert _evaluate(env["LINEAR_API_KEY_FALLBACK"], secrets) == "fleet-key"


# ── the declaration ─────────────────────────────────────────────────────────
def test_the_fleet_identity_declares_the_planner_home():
    rows = {r["name"]: r for r in json.loads(IDENTITIES.read_text())["identities"]}
    homes = {h["name"]: h for h in rows["fleet"].get("homes") or []}
    assert HOME in homes
    home = homes[HOME]
    assert home["env"] == "LINEAR_PLANNER_KEY"
    assert "bureau/linear-oauth-planner/agent-bureau-token" in home["lives_in"]
    assert "LINEAR_PLANNER_KEY" in home["lives_in"]


# ── the planner never refreshes ─────────────────────────────────────────────
# Spelled in pieces so this file does not trip its own check.
_FORBIDDEN = (
    re.compile("oauth" + "/token", re.I),
    re.compile("client" + r"[\s_-]?" + "id", re.I),
    re.compile("client" + r"[\s_-]?" + "secret", re.I),
)

CHANGED = (
    ".github/workflows/plan.yml",
    ".github/workflows/self-plan.yml",
    "config/linear-identities.json",
    "config/README.md",
    "scripts/linear_ops.py",
    "scripts/check_linear_budget.py",
    "scripts/check_linear_identities.py",
    "tests/test_planner_linear_key.py",
    "tests/test_linear_ops_key_fallback.py",
)


def test_nothing_this_card_touches_names_the_token_endpoint_or_the_apps_credentials():
    hits = []
    for rel in CHANGED:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in _FORBIDDEN:
            for m in pattern.finditer(text):
                hits.append(f"{rel}: {m.group(0)!r}")
    assert not hits, hits
