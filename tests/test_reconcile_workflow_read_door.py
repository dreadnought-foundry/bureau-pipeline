"""The reusable Reconcile workflow wires the read door (Stage 2 BP-2, items 6 and 38).

* The mode comes from the CALLER's repository variable, `off` when unset, so
  every repo stays on Linear until someone flips it — per repo, in the order
  design §6 sets (portico → deltasolv → agent-bureau → atlas).
* The door's address and audience come from variables too; the scripts' ref
  is handed to the client so it can refuse a token that names another ref.
* The reusable declares NO `permissions:` block, top level or job: a called
  workflow's permissions can only be downgraded, and any block silently sets
  every unnamed permission — `id-token` included — to none. The stub grants
  `id-token: write`; this file must not take it away (review S23).
* Before the sweep, the reusable refuses to run when `inputs.pipeline_ref` is
  not the ref the stub called it at (review M1, item 38) — whenever the door
  could be used. In mode `off`, or with no token, there is no identity to
  protect, and the check says so and passes.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps() -> list[dict]:
    return _doc()["jobs"]["sweep"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_the_sweep_reads_the_mode_from_the_callers_variable_default_off():
    env = _step("Sweep")["env"]
    assert env["BUREAU_READ"] == "${{ vars.BUREAU_READ || 'off' }}"


def test_the_sweep_is_told_where_the_door_is_and_which_ref_its_scripts_are():
    env = _step("Sweep")["env"]
    assert env["BUREAU_READ_URL"] == "${{ vars.BUREAU_READ_URL }}"
    assert env["BUREAU_READ_AUDIENCE"] == "${{ vars.BUREAU_READ_AUDIENCE }}"
    assert env["BUREAU_PIPELINE_REF"] == "${{ inputs.pipeline_ref }}"


def test_the_reusable_declares_no_permissions_block():
    doc = _doc()
    assert "permissions" not in doc
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, f"job {name} declares permissions"


def test_the_reusable_refuses_a_pipeline_ref_it_was_not_called_at():
    names = [s.get("name") for s in _steps()]
    check = _step("Refuse a pipeline_ref the stub did not call")
    assert names.index(check["name"]) < names.index("Sweep")
    # Through `env`, never interpolated into the shell line.
    assert 'bureau_read.py check-ref "$PIPELINE_REF"' in check["run"]
    assert check["env"]["PIPELINE_REF"] == "${{ inputs.pipeline_ref }}"
    assert "${{" not in check["run"]
    assert check["env"]["BUREAU_READ"] == "${{ vars.BUREAU_READ || 'off' }}"
    assert check["env"]["BUREAU_READ_AUDIENCE"] == "${{ vars.BUREAU_READ_AUDIENCE }}"
    assert "continue-on-error" not in check


def test_the_step_summary_carries_the_read_door_lines():
    assert "grep '^read-door' sweep.log" in _step("Sweep")["run"]
