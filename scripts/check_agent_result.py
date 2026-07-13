#!/usr/bin/env python3
"""Gate on the agent's execution result (DRE-1346 Fix 1, stdlib only).

The Claude execution result JSON can end {"subtype": "success",
"is_error": true} — a usage-limit or API death mid-run that the workflow
previously reported as success, hiding the dead card behind a green
conclusion until a staleness sweep noticed.

Called from agent-task.yml after the agent step:

    python3 check_agent_result.py <execution-json-path> <branch> <pr-url> \
        <blocker-file> [--escalation-file <path>]

Exit 1 (fail the job, loudly) when:
  - the execution result has is_error == true, OR
  - there is no agent branch, no PR, no blocker note, and no escalation note
    (silent death).
Exit 0 otherwise. An honest blocker note OR an honest escalation note (the
agent intentionally stopped to ask the CEO a decision — DRE-1655) is
working-as-designed; absence of the result file alone is not failure (action
versions move it) when the run left real evidence (branch, PR, or note).
"""

from __future__ import annotations

import json
import os
import sys


def is_error_death(execution: dict | None) -> bool:
    """True when the execution result is a mid-run API/model death
    ({"is_error": true}). The single source of truth for is_error detection,
    reused by agent-task's Report step to route the death through the
    model-fallback requeue path (DRE-1354)."""
    return execution is not None and execution.get("is_error") is True


def failure_reason(
    execution: dict | None,
    *,
    branch_exists: bool,
    pr_exists: bool = False,
    blocker_note: bool = False,
    escalation_note: bool = False,
    ignore_is_error: bool = False,
    claude_outcome: str = "",
) -> str | None:
    """Why this run should fail, or None if it is acceptable.

    `ignore_is_error` (DRE-1354): an is_error death is now handled by the Report
    step's model-fallback requeue (it switches model + counts toward the hold
    cap), so the agent-task gate no longer hard-fails on it — a hard fail would
    trigger the medic to re-run the job on the SAME model, bypassing the cap
    (the DRE-1300 18×-loop bug). The gate still fails on a no-evidence silent
    death so a truly lost run stays loud.

    `claude_outcome` (DRE-2074): the agent step's Actions outcome. "cancelled"
    means the agent was KILLED (job timeout / external cancel) while still
    working — no evidence is expected, so it is not a silent death and must
    not fail the gate (a red gate summons the medic to re-run a healthy-but-
    slow card). It waives ONLY the silent-death reason: an is_error record is
    affirmative evidence of a model death and still fails without the ignore
    flag. The reconcile sweep owns the requeue off the run's real conclusion.
    """
    if not ignore_is_error and is_error_death(execution):
        return "execution result has is_error=true"
    if claude_outcome == "cancelled":
        return None
    if (
        not branch_exists
        and not pr_exists
        and not blocker_note
        and not escalation_note
    ):
        return "no agent branch, no PR, no blocker note, and no escalation note"
    return None


def _load_execution(path: str) -> dict | None:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    # The action writes either a single result object or a message list
    # ending with the result record.
    if isinstance(data, list):
        for entry in reversed(data):
            if isinstance(entry, dict) and "is_error" in entry:
                return entry
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str]) -> int:
    # Optional trailing --ignore-is-error flag (DRE-1354): the Report step owns
    # the is_error→model-fallback requeue, so the gate should not hard-fail on it
    # (a hard fail re-runs the job on the same model via the medic).
    ignore_is_error = "--ignore-is-error" in argv
    argv = [a for a in argv if a != "--ignore-is-error"]
    # Optional --escalation-file <path> (DRE-1655): the agent intentionally
    # stopped to ask the CEO a decision. Like a blocker note, it is an honest,
    # designed outcome — not a silent death — so its presence keeps the gate green.
    escalation_file = ""
    if "--escalation-file" in argv:
        i = argv.index("--escalation-file")
        escalation_file = (argv[i + 1] if i + 1 < len(argv) else "")
        del argv[i : i + 2]
    # Optional --claude-outcome <outcome> (DRE-2074): the agent step's Actions
    # outcome. "cancelled" = the run was killed externally mid-build, not a
    # silent death — the gate stays green and reconcile owns the follow-up.
    claude_outcome = ""
    if "--claude-outcome" in argv:
        i = argv.index("--claude-outcome")
        claude_outcome = (argv[i + 1] if i + 1 < len(argv) else "")
        del argv[i : i + 2]
    exec_path, branch, pr_url, blocker_file = (argv + ["", "", "", ""])[:4]

    def _has_note(path: str) -> bool:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0

    reason = failure_reason(
        _load_execution(exec_path),
        branch_exists=bool(branch.strip()),
        pr_exists=bool(pr_url.strip()) and pr_url.strip() != "null",
        blocker_note=bool(blocker_file) and os.path.isfile(blocker_file),
        escalation_note=_has_note(escalation_file),
        ignore_is_error=ignore_is_error,
        claude_outcome=claude_outcome,
    )
    if reason:
        print(f"agent result gate: FAIL — {reason}")
        return 1
    print("agent result gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
