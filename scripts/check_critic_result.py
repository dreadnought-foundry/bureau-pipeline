#!/usr/bin/env python3
"""Gate on the QA critic's execution result + verdict (stdlib only).

Mirror of DRE-1346 Fix 1 (check_agent_result.py) for the critic side.

The QA critic runs claude-code-action then is expected to write a verdict to
/tmp/qa-verdict.md whose first non-blank line is `VERDICT: APPROVE` or
`VERDICT: REQUEST_CHANGES`. When that step CRASHES, the Claude execution
result ends {"is_error": true} (auth/startup death — observed ~340ms / 1 turn
/ $0 inference, 2026-06-13) and/or no verdict file is written. qa-review.yml
previously fail-closed and posted a REQUEST_CHANGES verdict with NO real
findings — a false reject that churned good PRs (#1441/#1442) into the fix
loop and spawned duplicate-PR cycles (DRE-1330/1332).

A crash must NEVER yield a real verdict. This gate decides whether a GENUINE
review ran: a real verdict requires is_error != true AND a verdict file that
exists, is non-empty, and declares a VERDICT: line. Anything else means the
review did not really run — the workflow must retry once, then post a NEUTRAL
status (not REQUEST_CHANGES) and fail loudly (medic-visible).

Called from qa-review.yml after each critic attempt:

    python3 check_critic_result.py <execution-json-path> <verdict-path>

Exit 0 when a real verdict exists (post it). Exit 1 on crash/no-verdict
(retry, then neutral + loud fail).
"""

from __future__ import annotations

import json
import sys


def _verdict_line_present(text: str) -> bool:
    for line in text.splitlines():
        if line.strip().startswith("VERDICT:"):
            return True
    return False


def verdict_is_real(execution: dict | None, verdict_path: str) -> bool:
    """True iff a genuine review ran and left a usable verdict.

    A crashed execution (is_error=true) is authoritative — even a stale
    verdict file does not rescue it. Otherwise the verdict file must exist,
    be non-empty, and contain a `VERDICT:` line.
    """
    if execution is not None and execution.get("is_error") is True:
        return False
    try:
        with open(verdict_path) as f:
            text = f.read()
    except OSError:
        return False
    if not text.strip():
        return False
    return _verdict_line_present(text)


def _load_execution(path: str) -> dict | None:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    # The action writes either a single result object or a message list
    # ending with the result record (matches check_agent_result.py).
    if isinstance(data, list):
        for entry in reversed(data):
            if isinstance(entry, dict) and "is_error" in entry:
                return entry
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str]) -> int:
    exec_path, verdict_path = (argv + ["", ""])[:2]
    execution = _load_execution(exec_path)
    if verdict_is_real(execution, verdict_path):
        print("critic result gate: ok — real verdict")
        return 0
    if execution is not None and execution.get("is_error") is True:
        print("critic result gate: FAIL — execution result has is_error=true")
    else:
        print("critic result gate: FAIL — no usable verdict file")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
