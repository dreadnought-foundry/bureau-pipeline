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


def failure_reason(
    execution: dict | None,
    *,
    branch_exists: bool,
    pr_exists: bool = False,
    blocker_note: bool = False,
) -> str | None:
    """Why this run should fail, or None if it is acceptable."""
    if execution is not None and execution.get("is_error") is True:
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
    exec_path, branch, pr_url, blocker_file = (argv + ["", "", "", ""])[:4]
    reason = failure_reason(
        _load_execution(exec_path),
        branch_exists=bool(branch.strip()),
        pr_exists=bool(pr_url.strip()) and pr_url.strip() != "null",
        blocker_note=bool(blocker_file) and os.path.isfile(blocker_file),
    )
    if reason:
        print(f"agent result gate: FAIL — {reason}")
        return 1
    print("agent result gate: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
