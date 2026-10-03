"""The reusable Linear Sync workflow wires the read door (Stage 2 BP-7, #15 and item 38).

The same wiring `reconcile.yml` carries (BP-2,
`tests/test_reconcile_workflow_read_door.py`), on the step that runs the
merge-sweep gate and the scoped passes:

* the mode comes from the CALLER's repository variable, `off` when unset;
* the door's address and audience come from variables; the scripts' ref is
  handed to the client so it refuses a token that names another ref;
* the reusable declares NO `permissions:` block — the stub grants
  `id-token: write`, and any block here would silently take it away (S23);
* a `pipeline_ref` check runs before the step.

One deliberate difference from reconcile.yml: the check here is
`continue-on-error`. The step it guards is the one that moves the merged card
to Done (GitHub's merge is the ground truth, DRE-2027), and a refused ref must
never cost a card its Done. The client refuses the door on its own for the
same mismatch (`bureau_read._token`, `pipeline-ref-mismatch`), so the gate and
the passes fall back to Linear; the check's job here is the loud annotation.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "linear-sync.yml"
STUB = ROOT / ".github" / "workflows" / "self-linear-sync.yml"
STEP = "Card → Done"
CHECK = "Refuse a pipeline_ref the stub did not call"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps() -> list[dict]:
    return _doc()["jobs"]["card-done"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_the_merge_step_reads_the_mode_from_the_callers_variable_default_off():
    assert _step(STEP)["env"]["BUREAU_READ"] == "${{ vars.BUREAU_READ || 'off' }}"


def test_the_merge_step_is_told_where_the_door_is_and_which_ref_its_scripts_are():
    env = _step(STEP)["env"]
    assert env["BUREAU_READ_URL"] == "${{ vars.BUREAU_READ_URL }}"
    assert env["BUREAU_READ_AUDIENCE"] == "${{ vars.BUREAU_READ_AUDIENCE }}"
    assert env["BUREAU_PIPELINE_REF"] == "${{ inputs.pipeline_ref }}"


def test_the_reusable_declares_no_permissions_block():
    doc = _doc()
    assert "permissions" not in doc
    for name, job in doc["jobs"].items():
        assert "permissions" not in job, f"job {name} declares permissions"


def test_this_repos_own_stub_grants_the_id_token():
    stub = yaml.safe_load(STUB.read_text())
    assert (stub.get("permissions") or {}).get("id-token") == "write"


def test_the_ref_check_runs_before_the_merge_step_and_never_blocks_the_done():
    names = [s.get("name") for s in _steps()]
    check = _step(CHECK)
    assert names.index(CHECK) < names.index(STEP)
    assert 'bureau_read.py check-ref "$PIPELINE_REF"' in check["run"]
    assert "${{" not in check["run"]  # through env, never interpolated
    assert check["env"]["PIPELINE_REF"] == "${{ inputs.pipeline_ref }}"
    assert check["env"]["BUREAU_READ"] == "${{ vars.BUREAU_READ || 'off' }}"
    assert check["env"]["BUREAU_READ_AUDIENCE"] == "${{ vars.BUREAU_READ_AUDIENCE }}"
    # The step after it moves the card to Done: a refused ref must not stop it.
    assert check.get("continue-on-error") is True
    assert "if" not in _step(STEP)


def test_the_done_itself_is_untouched():
    run = _step(STEP)["run"]
    assert 'python3 .bureau-pipeline/scripts/linear_ops.py card-done "$CARD" "$PR_URL"' in run
    # The gate and the passes are the only readers the door wiring is for.
    assert 'SWEEPS=$(python3 .bureau-pipeline/scripts/merge_sweep_gate.py "$CARD")' in run


def test_the_conflict_sweep_reads_no_board_and_is_not_wired():
    """`--conflicts-only` reads GitHub only (`unstick_conflicts`): nothing in
    it asks Linear for a card, so there is nothing for the door to serve."""
    steps = _doc()["jobs"]["conflict-sweep"]["steps"]
    for step in steps:
        assert "BUREAU_READ" not in (step.get("env") or {})
