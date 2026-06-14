#!/usr/bin/env python3
"""Gate on the agent's execution result (DRE-1346 Fix 1, stdlib only).

The Claude execution result JSON can end {"subtype": "success",
"is_error": true} — a usage-limit or API death mid-run that the workflow
previously reported as success, hiding the dead card behind a green
conclusion until a staleness sweep noticed.

Called from agent-task.yml after the agent step:

    python3 check_agent_result.py <execution-json-path> <branch> <pr-url> <blocker-file>

Exit 1 (fail the job, loudly) when:
  - the execution result has is_error == true, OR
  - there is no agent branch, no PR, and no blocker note (silent death).
Exit 0 otherwise. An honest blocker note is working-as-designed; absence
of the result file alone is not failure (action versions move it) when
the run left real evidence (branch or PR).
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
    ignore_is_error: bool = False,
) -> str | None:
    """Why this run should fail, or None if it is acceptable.

    `ignore_is_error` (DRE-1354): an is_error death is now handled by the Report
    step's model-fallback requeue (it switches model + counts toward the hold
    cap), so the agent-task gate no longer hard-fails on it — a hard fail would
    trigger the medic to re-run the job on the SAME model, bypassing the cap
    (the DRE-1300 18×-loop bug). The gate still fails on a no-evidence silent
    death so a truly lost run stays loud.
    """
    if not ignore_is_error and is_error_death(execution):
        return "execution result has is_error=true"
    if not branch_exists and not pr_exists and not blocker_note:
        return "no agent branch, no PR, and no blocker note"
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
    exec_path, branch, pr_url, blocker_file = (argv + ["", "", "", ""])[:4]
    reason = failure_reason(
        _load_execution(exec_path),
        branch_exists=bool(branch.strip()),
        pr_exists=bool(pr_url.strip()) and pr_url.strip() != "null",
        blocker_note=bool(blocker_file) and os.path.isfile(blocker_file),
        ignore_is_error=ignore_is_error,
    )
    if reason:
        print(f"agent result gate: FAIL — {reason}")
        return 1
    print("agent result gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
