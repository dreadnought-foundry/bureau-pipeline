"""RED-first tests: a CANCELLED agent run is not a dead agent (DRE-2074).

THE BUG (live evidence, 2026-07-12, DRE-2070 on agent-bureau): agent-task's
execute job carried `timeout-minutes: 45`. A legitimately long build (DRE-2070
profiles a 7-minute vitest suite repeatedly — 45 minutes is NORMAL) hit that
timeout mid-work; GitHub cancelled the "Implement card" step, and the
`always()` Report step then read "no PR, no blocker note" as a SILENT AGENT
DEATH — counting the shared dead-run cap and requeuing/parking a card whose
run was in_progress on GitHub and had posted a ⏳ receipt six minutes earlier
(run 29209527599, hold posted 22:20:54 at the 45-minute mark, 4 kills total,
card parked needs-human). DRE-2032 fixed the reconcile side of this class;
the run's OWN report step was the remaining murderer.

FIX UNDER TEST:
  - dead_run.decide() learns the `cancelled` death class: the agent was
    KILLED (job timeout / external cancel), it did not die. Action is
    "defer" — one informational comment WITHOUT the dead-run-requeue tag
    (must not increment the shared cap), no state move, no hold label,
    regardless of the prior dead count. The reconcile sweep's authoritative
    run-status check (DRE-2032) owns the requeue once the run has actually
    CONCLUDED without a PR — dead-run handling as today, never over a live
    run.
  - check_agent_result.failure_reason() learns `claude_outcome`: a cancelled
    agent step with no evidence is NOT a silent death (a red gate would send
    the medic to re-run a healthy-but-slow card — the 21:36 re-run raced the
    park in the incident).
  - agent-task.yml: the execute job's total-runtime cap rises so normal long
    builds finish (45 murdered them); the Gate and Report steps thread
    `steps.claude.outcome` into the scripts.
  - Requeue-cap accounting for GENUINE deaths (silent / hung / is_error) is
    unchanged.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_agent_result  # noqa: E402
import dead_run  # noqa: E402

RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/29209527599"
NO_EVIDENCE = "no agent branch, no PR, no blocker note, and no escalation note"


# --------------------------------------------------------------------------
# dead_run.decide — the cancelled class defers, never counts, never holds
# --------------------------------------------------------------------------
def test_cancelled_run_defers_instead_of_requeueing():
    d = dead_run.decide(0, cancelled=True, run_url=RUN_URL)
    assert d.action == "defer"


def test_cancelled_comment_never_carries_the_dead_tag():
    """The shared cap is counted by DEAD_TAG occurrences on the card — a
    cancelled-run receipt containing it would increment the cap exactly like
    the death it exists to NOT be."""
    d = dead_run.decide(1, cancelled=True, run_url=RUN_URL)
    assert dead_run.DEAD_TAG not in d.comments[0]


def test_cancelled_run_at_the_cap_is_not_held():
    """The DRE-2070 kill shot: prior count already at/past the cap, run killed
    by the job timeout → the old code posted 'held-for-human ... 4th time'
    and parked a healthy card. A cancelled run must defer even then."""
    for prior in (2, 3):
        d = dead_run.decide(prior, cancelled=True, run_url=RUN_URL)
        assert d.action == "defer", f"prior_dead={prior} must not hold"
        assert dead_run.HOLD_LABEL not in d.comments[0]
        assert "held-for-human" not in d.comments[0]


def test_cancelled_comment_defers_to_reconcile_and_names_the_run():
    """Fail loud: the receipt must say what happened (cancelled, not dead)
    and who owns the follow-up (the reconcile sweep, off the run's REAL
    conclusion), and carry the run URL for the audit trail."""
    d = dead_run.decide(0, cancelled=True, run_url=RUN_URL)
    body = d.comments[0]
    assert "cancelled" in body.lower()
    assert "reconcile" in body.lower()
    assert RUN_URL in body


def test_cancelled_comment_is_not_proof_of_life():
    """reconcile.agent_run_alive's receipt fallback treats ⏳/🧠-prefixed
    comments as an alive agent — the cancelled receipt must not masquerade
    as one (it would suppress the eventual requeue)."""
    d = dead_run.decide(0, cancelled=True, run_url=RUN_URL)
    assert not d.comments[0].lstrip().startswith(("⏳", "🧠"))


def test_genuine_death_accounting_is_unchanged():
    """Card AC: requeue-cap accounting unchanged for real deaths."""
    below = dead_run.decide(0)
    assert below.action == "requeue"
    assert dead_run.DEAD_TAG in below.comments[0]
    at_cap = dead_run.decide(2)
    assert at_cap.action == "hold"
    assert "held-for-human" in at_cap.comments[0]


def test_cli_cancelled_flag_prints_defer():
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dead_run.py"),
         "decide", "2", "--cancelled", "--run-url", RUN_URL],
        capture_output=True, text=True,
    ).stdout
    lines = out.splitlines()
    assert lines[0] == "defer"
    assert dead_run.DEAD_TAG not in out


# --------------------------------------------------------------------------
# check_agent_result — a cancelled step with no evidence is not a silent death
# --------------------------------------------------------------------------
def test_gate_waives_silent_death_when_step_was_cancelled():
    assert check_agent_result.failure_reason(
        None, branch_exists=False, claude_outcome="cancelled"
    ) is None


def test_gate_still_fails_silent_death_on_other_outcomes():
    for outcome in ("", "success", "failure"):
        assert check_agent_result.failure_reason(
            None, branch_exists=False, claude_outcome=outcome
        ) == NO_EVIDENCE, f"outcome={outcome!r} must still fail"


def test_gate_cancelled_waives_only_the_silent_death_reason():
    """An is_error record is affirmative evidence of a model death — a
    cancellation must not mask it (the Report step's model-fallback path
    still owns it via --ignore-is-error in production)."""
    assert check_agent_result.failure_reason(
        {"is_error": True}, branch_exists=True, claude_outcome="cancelled"
    ) == "execution result has is_error=true"


def test_gate_cli_claude_outcome_cancelled_exits_zero(tmp_path):
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_agent_result.py"),
         str(tmp_path / "missing.json"), "", "", "",
         "--claude-outcome", "cancelled"],
        capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr


def test_gate_cli_without_cancelled_outcome_still_exits_one(tmp_path):
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_agent_result.py"),
         str(tmp_path / "missing.json"), "", "", "",
         "--claude-outcome", "success"],
        capture_output=True, text=True,
    )
    assert p.returncode == 1


# --------------------------------------------------------------------------
# wiring over agent-task.yml
# --------------------------------------------------------------------------
def _agent_task():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "agent-task.yml").read_text())


def _step(name):
    steps = _agent_task()["jobs"]["execute"]["steps"]
    matches = [s for s in steps if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step"
    return matches[0]


def test_execute_job_timeout_allows_long_builds():
    """45 minutes murdered normal long builds (DRE-2070 profiled a 7-minute
    vitest suite repeatedly — killed at the 45-minute mark, 4×). The job
    timeout stays the total-runtime cap, but it must comfortably exceed a
    legitimate long build."""
    timeout = _agent_task()["jobs"]["execute"]["timeout-minutes"]
    assert timeout >= 90, (
        f"execute timeout-minutes={timeout}: the 45-minute cap killed live "
        "builds mid-work (DRE-2074)"
    )


def test_report_step_threads_the_claude_outcome():
    step = _step("Report result to Linear")
    assert step["env"].get("CLAUDE_OUTCOME") == "${{ steps.claude.outcome }}", (
        "the Report step must know whether the agent step was cancelled "
        "(job timeout / external cancel) — that is not a dead agent"
    )
    assert "--cancelled" in step["run"], (
        "the dead branch must route a cancelled outcome into "
        "dead_run.py decide --cancelled"
    )


def test_report_step_defer_action_moves_no_state():
    """The defer action posts the receipt and stops — the Todo requeue must
    be reachable only for action=requeue, and hold only for action=hold."""
    run = _step("Report result to Linear")["run"]
    assert '"$ACTION" = "requeue"' in run, (
        "the Todo requeue must be gated on the explicit requeue action so "
        "a defer decision falls through to no state change"
    )


def test_gate_step_threads_the_claude_outcome():
    step = _step("Gate on agent result")
    assert step["env"].get("CLAUDE_OUTCOME") == "${{ steps.claude.outcome }}"
    assert "--claude-outcome" in step["run"], (
        "a cancelled step must not read as a silent death, or the red gate "
        "summons the medic to re-run a healthy-but-slow card"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
