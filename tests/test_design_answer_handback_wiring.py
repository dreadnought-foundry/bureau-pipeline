"""DRE-8720 (DRE-8708 rule 4c): a skipped build hands the CEO's design answer back to the critic.

When the critic's verdict carries a design question, the card waits in Green
Light. The CEO answers from the console and the card returns to In Progress,
and that move dispatches `agent-task.yml`. Its duplicate-dispatch guard
(`dedupe_dispatch.decide`) then skips the build, because the card's pull
request is open — and before this card nothing else ran, so the critic's
verdict stood at the same head and nobody told it he had answered.

The step `Hand an answered design question back to the critic` closes that.
It runs `design_answer.py handback` (DRE-8722), which dispatches one
re-review when a verified answer follows the question and writes nothing
otherwise. It runs only on the open-PR skip: the merged, live-run and
past-building skips are not the answer coming back.

These tests pin the step's place, condition and env, evaluate its condition
against the guard's real reasons, and execute its block as written with the
hand-back stubbed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import dedupe_dispatch  # noqa: E402
import step_shell  # noqa: E402

NAME = "Hand an answered design question back to the critic"
CONDITION = ("steps.gate.outputs.bounced != 'true' && steps.dedupe.outputs.skip == 'true' "
             "&& startsWith(steps.dedupe.outputs.reason, 'open agent PR #')")
CARD = "DRE-77"
REPO = "dreadnought-foundry/portico"

_EXPR = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


def _steps() -> list[dict]:
    doc = yaml.safe_load(step_shell.workflow_source(WORKFLOW))
    return doc["jobs"]["execute"]["steps"]


def _step() -> dict:
    return next(s for s in _steps() if s.get("name") == NAME)


# --- the condition, evaluated -------------------------------------------------

_COMPARE = re.compile(r"^(?P<ref>[\w.]+)\s*(?P<op>==|!=)\s*'(?P<value>[^']*)'$")
_STARTS = re.compile(r"^startsWith\(\s*(?P<ref>[\w.]+)\s*,\s*'(?P<value>[^']*)'\s*\)$")


def holds(condition: str, outputs: dict) -> bool:
    """The step's `if:` against step outputs, as Actions reads the three forms
    it uses — `==`, `!=` and `startsWith`, which Actions compares
    case-insensitively. An output never written reads as ''."""
    for term in (t.strip() for t in condition.split("&&")):
        compare, starts = _COMPARE.match(term), _STARTS.match(term)
        if compare:
            got = outputs.get(compare["ref"], "")
            ok = (got == compare["value"]) == (compare["op"] == "==")
        elif starts:
            got = outputs.get(starts["ref"], "")
            ok = got.casefold().startswith(starts["value"].casefold())
        else:
            raise AssertionError(f"a condition term this reader does not know: {term!r}")
        if not ok:
            return False
    return True


def _guard(prs=(), lane="In Progress", heartbeat_run="", run_status=""):
    bodies = ([f"🧠 model-attempt: claude-opus-5-5 — engineer agent starting · "
               f"run https://github.com/{REPO}/actions/runs/{heartbeat_run}"]
              if heartbeat_run else [])
    return dedupe_dispatch.decide(CARD, "999", bodies, lambda _rid: run_status,
                                  list(prs), lane)


def _pr(state: str) -> dict:
    return {"number": 412, "headRefName": f"agent/{CARD}-parts", "state": state}


GUARD_CASES = {
    "open-pr": dict(prs=[_pr("OPEN")]),
    "merged-pr": dict(prs=[_pr("MERGED")]),
    "live-run": dict(heartbeat_run="123", run_status="in_progress"),
    "in-review": dict(lane="In Review"),
}


def _outputs(decision, bounced="false") -> dict:
    return {"steps.gate.outputs.bounced": bounced,
            "steps.dedupe.outputs.skip": "true" if decision.skip else "false",
            "steps.dedupe.outputs.reason": decision.reason}


# --- the step's shape -----------------------------------------------------------

def test_the_step_sits_directly_after_the_duplicate_dispatch_guard():
    steps = _steps()
    at = next(i for i, s in enumerate(steps) if s.get("id") == "dedupe")
    assert steps[at + 1].get("name") == NAME


def test_its_condition_is_exactly_the_open_pr_skip():
    assert _step()["if"] == CONDITION


def test_a_failing_handback_never_fails_the_job():
    assert _step().get("continue-on-error") is True


def test_its_env_is_the_card_the_linear_keys_the_repo_and_the_workflow_token():
    step, guard = _step(), next(s for s in _steps() if s.get("id") == "dedupe")
    env = step["env"]
    assert set(env) == {"CARD", "LINEAR_API_KEY", "LINEAR_API_KEY_FALLBACK",
                        "BUREAU_CARD_SNAPSHOT", "REPO", "GH_TOKEN"}
    assert env["CARD"] == "${{ github.event.client_payload.identifier }}"
    assert env["LINEAR_API_KEY"] == guard["env"]["LINEAR_API_KEY"]
    assert env["LINEAR_API_KEY_FALLBACK"] == guard["env"]["LINEAR_API_KEY_FALLBACK"]
    # DRE-8900: the thread off the pre-agent card snapshot, the guard's own.
    assert env["BUREAU_CARD_SNAPSHOT"] == guard["env"]["BUREAU_CARD_SNAPSHOT"]
    assert env["REPO"] == "${{ github.repository }}"
    # The App token holds no Actions permission (DRE-1254); the dispatch
    # rides the workflow's own token, which the stub grants `actions: write`.
    assert env["GH_TOKEN"] == "${{ github.token }}"


def test_bureau_pipelines_own_stub_grants_the_dispatch():
    stub = yaml.safe_load((ROOT / ".github" / "workflows" / "self-agent-task.yml").read_text())
    assert stub["permissions"].get("actions") == "write"


# --- the guard's four reasons, and which of them runs the step ------------------

def test_only_the_open_pr_reason_opens_with_open_agent_pr():
    reasons = {case: _guard(**kw) for case, kw in GUARD_CASES.items()}
    assert all(d.skip for d in reasons.values()), reasons
    opening = {case for case, d in reasons.items()
               if d.reason.startswith("open agent PR #")}
    assert opening == {"open-pr"}, {c: d.reason for c, d in reasons.items()}


@pytest.mark.parametrize("case,runs", [
    ("open-pr", True), ("merged-pr", False), ("live-run", False), ("in-review", False),
])
def test_the_step_runs_on_the_open_pr_skip_alone(case, runs):
    assert holds(_step()["if"], _outputs(_guard(**GUARD_CASES[case]))) is runs


def test_an_open_pr_with_a_live_run_still_reads_the_open_pr_reason():
    decision = _guard(prs=[_pr("OPEN")], heartbeat_run="123", run_status="in_progress")
    assert holds(_step()["if"], _outputs(decision))


def test_the_step_does_not_run_when_the_build_proceeds_or_the_gate_bounced():
    proceeding = _guard()
    assert not proceeding.skip
    assert not holds(_step()["if"], _outputs(proceeding))
    assert not holds(_step()["if"], _outputs(_guard(**GUARD_CASES["open-pr"]),
                                             bounced="true"))


# --- the block, executed ----------------------------------------------------------

_STUB = '''import json, os, sys
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "gh_token": os.environ.get("GH_TOKEN", "")}) + "\\n")
print("design-answer: stubbed", file=sys.stderr)
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''


def _run_block(tmp_path: Path, exit_code: int = 0):
    pipeline = tmp_path / "pipeline" / "scripts"
    pipeline.mkdir(parents=True)
    (pipeline / "design_answer.py").write_text(_STUB)
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bureau-pipeline").symlink_to(tmp_path / "pipeline")
    values = {
        "github.event.client_payload.identifier": CARD,
        "github.repository": REPO,
        "github.token": "ghs_workflow_token",
    }
    step = _step()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "STUB_LOG": str(tmp_path / "calls.jsonl"), "STUB_EXIT": str(exit_code)}
    for key, raw in step["env"].items():
        env[key] = _EXPR.sub(lambda m: values.get(m.group(1), "lin_api_test"), raw)
    script = step_shell.step_shell(step)
    assert "${{" not in script, "an unresolved expression would reach the shell"
    proc = subprocess.run(  # nosec B603 B607 — this repo's own step, fixed argv
        ["bash", "-e", "-c", script], cwd=work, env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )
    log = tmp_path / "calls.jsonl"
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return proc, calls


def test_the_block_hands_the_card_back_on_the_workflow_token(tmp_path):
    proc, calls = _run_block(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert calls == [{"argv": ["handback", CARD, "--repo", REPO],
                      "gh_token": "ghs_workflow_token"}]
    assert "design-answer:" in proc.stderr


def test_a_failing_handback_is_the_steps_failure_alone(tmp_path):
    # The step itself goes red; continue-on-error (pinned above) keeps the job
    # green, and nothing after it reads this step.
    proc, calls = _run_block(tmp_path, exit_code=1)
    assert proc.returncode != 0
    assert len(calls) == 1
    later = json.dumps(_steps()[_steps().index(_step()) + 1:])
    assert "steps.handback" not in later and NAME not in later
