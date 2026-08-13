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

ONE EXCEPTION, DRE-2422: `subtype: error_max_turns` is not a crash in the
sense above. The auth death this gate was built for produced nothing — 634ms,
1 turn, $0. A turn-ceiling death is the opposite: portico PR #273 ran a full
8-minute, 41-turn, $2.05 review and was cut off by the ceiling, and a review
that far along may already have written its finished verdict. Throwing that
away costs a completed review and buys a second identical failure. So a
max-turns run may keep its verdict — but it must be a COMPLETE one (a legal
VERDICT: value plus the mandated `## Summary` section), and the run must show
real work (num_turns > 1). Every other is_error is rejected exactly as before,
so the auth-death fingerprint is untouched.

Called from qa-review.yml after each critic attempt:

    python3 check_critic_result.py <execution-json-path> <verdict-path>

Exit 0 when a real verdict exists (post it). Exit 1 on crash/no-verdict
(retry, then neutral + loud fail).
"""

from __future__ import annotations

import json
import sys


# The only crash whose verdict may still be believed (DRE-2422). Every other
# is_error stays hard-rejected — see _crash_may_keep_its_verdict.
_MAX_TURNS_SUBTYPE = "error_max_turns"

# The two decisions a critic is allowed to reach.
_LEGAL_VERDICTS = ("APPROVE", "REQUEST_CHANGES")


def _verdict_line_present(text: str) -> bool:
    for line in text.splitlines():
        if line.strip().startswith("VERDICT:"):
            return True
    return False


def _verdict_is_complete(text: str) -> bool:
    """Stricter than _verdict_line_present: did the review actually FINISH?

    Only used on the max-turns path, where "the file exists" is not enough —
    we need to tell a review that finished and then hit the ceiling from one
    that was cut off mid-thought. A finished verdict declares one of the two
    legal decisions AND carries the `## Summary` section the critic prompt
    mandates. A review still working has neither.
    """
    declared = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            if stripped.split(":", 1)[1].strip() in _LEGAL_VERDICTS:
                declared = True
            break
    if not declared:
        return False
    return any(
        line.strip().lower().startswith("## summary")
        for line in text.splitlines()
    )


def _crash_may_keep_its_verdict(execution: dict) -> bool:
    """True only for a turn-ceiling death that did real work first.

    THE DISTINCTION THIS GATE RESTS ON. Two things end `is_error: true` and
    they are opposites:

    * The auth/startup death (~634ms, 1 turn, $0, nothing written — seen
      2026-06-13 and again in the 2026-08-09 Fable fleet outage). The agent
      never ran. It has NO opinion, so anything sitting in the verdict file
      is stale or spurious and must never be read as a review. This is the
      case the gate was built for and it is untouched.

    * `error_max_turns` (portico PR #273, 2026-08-13 — 41 turns, 8 minutes,
      $2.05). The agent ran a full review and was cut off by the ceiling.
      It may well have written its finished verdict already.

    Only the second may keep its verdict, and only with real work behind it.
    num_turns > 1 is belt and braces: a one-turn run reviewed nothing no
    matter what subtype it claims.
    """
    if execution.get("subtype") != _MAX_TURNS_SUBTYPE:
        return False
    turns = execution.get("num_turns")
    return isinstance(turns, int) and turns > 1


def verdict_is_real(execution: dict | None, verdict_path: str) -> bool:
    """True iff a genuine review ran and left a usable verdict.

    A crashed execution (is_error=true) is authoritative — even a stale
    verdict file does not rescue it. The ONE exception is a turn-ceiling
    death (DRE-2422), which is a completed review cut short rather than an
    agent that never ran; it may keep its verdict, but only if that verdict
    is COMPLETE (see _verdict_is_complete), not merely present.

    Otherwise the verdict file must exist, be non-empty, and contain a
    `VERDICT:` line.
    """
    crashed = execution is not None and execution.get("is_error") is True
    if crashed and not _crash_may_keep_its_verdict(execution):
        return False
    try:
        with open(verdict_path) as f:
            text = f.read()
    except OSError:
        return False
    if not text.strip():
        return False
    if crashed:
        # Higher bar on the crash path only. Failing it lands exactly where
        # today's code lands — retry, then neutral + loud fail — so this can
        # only ever rescue a verdict, never manufacture one.
        return _verdict_is_complete(text)
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
    crashed = execution is not None and execution.get("is_error") is True
    if verdict_is_real(execution, verdict_path):
        if crashed:
            # Say so out loud: the run is red in the Actions UI but its
            # verdict counted. Anyone reading the log needs to see why.
            print(
                "critic result gate: ok — the review hit the turn ceiling "
                "(subtype=error_max_turns, num_turns="
                f"{execution.get('num_turns')}) but had already written a "
                "complete verdict, so it stands (DRE-2422). Raise "
                "--max-turns if this recurs."
            )
        else:
            print("critic result gate: ok — real verdict")
        return 0
    if crashed:
        print(
            "critic result gate: FAIL — execution result has is_error=true "
            f"(subtype={execution.get('subtype')!r})"
        )
    else:
        print("critic result gate: FAIL — no usable verdict file")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
