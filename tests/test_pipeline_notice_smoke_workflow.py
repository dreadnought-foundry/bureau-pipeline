"""The notice door's live smoke, read from its workflow file (DRE-7451).

`.github/workflows/pipeline-notice-smoke.yml` is the one live check of the
console's notice door that does not wait for something in the fleet to break.
The operator dispatches it once for the proof, and its log must read
`resolved` or `not-open`, then `raised`, `repeat`, `resolved`. What this pins:

* it runs only when somebody dispatches it — never on a push, a pull request
  or a schedule, because every run raises and clears a real console alert;
* it holds exactly `id-token: write` (the door's credential) and
  `contents: read` (its own checkout), and nothing else;
* it is pointed at the door by `BUREAU_READ_URL`, the variable the read door's
  client already reads;
* it runs the client four times — resolve, raise, raise, resolve — on
  `smoke:pipeline_notice` at WARN, with no fallback card and no Linear call,
  so a door it cannot reach prints `lost` and fails the run rather than
  filing anything.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "pipeline-notice-smoke.yml"
KEY = "smoke:pipeline_notice"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(doc: dict):
    # PyYAML reads a bare `on:` key as the boolean True.
    return doc.get("on", doc.get(True))


def _steps(doc: dict) -> list[dict]:
    return [step for job in doc["jobs"].values() for step in job.get("steps", [])]


def _calls(doc: dict) -> list[str]:
    """Every `pipeline_notice.py` invocation in the run scripts, in order,
    with shell line continuations joined."""
    calls = []
    for step in _steps(doc):
        script = (step.get("run") or "").replace("\\\n", " ")
        for line in script.splitlines():
            if "scripts/pipeline_notice.py" in line:
                calls.append(" ".join(line.split()))
    return calls


def test_it_is_triggered_by_workflow_dispatch_alone():
    triggers = _triggers(_doc())
    assert isinstance(triggers, dict)
    assert list(triggers) == ["workflow_dispatch"]


def test_it_grants_exactly_id_token_write_and_contents_read():
    doc = _doc()
    assert doc["permissions"] == {"id-token": "write", "contents": "read"}
    for job in doc["jobs"].values():
        assert "permissions" not in job, "a job-level grant would widen the workflow's"


def test_it_carries_bureau_read_url():
    doc = _doc()
    envs = [doc.get("env") or {}] + [job.get("env") or {} for job in doc["jobs"].values()]
    assert any(env.get("BUREAU_READ_URL") == "${{ vars.BUREAU_READ_URL }}" for env in envs)


def test_it_checks_out_the_ref_it_was_dispatched_on():
    checkouts = [s for s in _steps(_doc())
                 if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == "${{ github.ref }}"


def test_it_runs_resolve_raise_raise_resolve_on_the_smoke_key_at_warn():
    calls = _calls(_doc())
    verbs = [re.search(r"pipeline_notice\.py (\w+)", c).group(1) for c in calls]
    assert verbs == ["resolve", "raise", "raise", "resolve"]
    for call in calls:
        assert f"--notice {KEY}" in call
    for call in calls[1:3]:
        assert "--severity WARN" in call


def test_it_files_no_card_on_any_path():
    text = WORKFLOW.read_text()
    assert "--fallback" not in text
    assert "linear_ops.py" not in text
    assert "LINEAR_API_KEY" not in text
    assert "secrets." not in text


def test_each_outcome_is_checked():
    """The run is green only when the door answered the four outcomes the
    proof reads: a raise the door could not take already exits non-zero."""
    scripts = [s.get("run") or "" for s in _steps(_doc())]
    joined = "\n".join(scripts)
    for outcome in ("resolved", "not-open", "raised", "repeat"):
        assert f"pipeline-notice: {outcome} kind=pipeline_notice:{KEY}" in joined
