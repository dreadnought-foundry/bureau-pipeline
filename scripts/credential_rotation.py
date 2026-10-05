#!/usr/bin/env python3
"""A run killed by our own Claude token renewal (DRE-5856, stdlib only).

The console renews the Claude token chain about every eight hours and rewrites
`CLAUDE_CODE_OAUTH_TOKEN` in every repo, and every renewal revokes the access
token before it (console/backend/claude_oauth_token.py). A run in flight at
that moment dies on its next call with `API Error: 401 OAuth access token has
been revoked`. Nothing about the card, the model or the account caused it, and
a re-run fixes it: GitHub hands a fresh job the secret as it stands now, which
is the renewed token. The job that died cannot fix it for itself — it was
handed its secrets when it started.

On 2026-10-05 planner run 37328397948 (epic DRE-5852) died that way at
07:56:52 PT, two seconds after a renewal, with five-hour utilization at 0.06.
The medic recorded it as a usage-limit death, the review route's re-check
parked the epic in Triage with `needs-human`, and the medic then declined its
one retry because of that label. A person diagnosed it and moved the epic back.

THE TWO SHAPES, both read off the execution file the action writes:

  * the run's RESULT died on a 401 that says `revoked` — `api_error_status:
    401` or `API Error: 401` in `result`, with `revoked` in `result`;
  * the CLI RETRIED a call on a 401 — a `system`/`api_retry` event with
    `error_status: 401` or `error: "authentication_failed"` — and the run
    then died.

Read POSITIVELY, the same discipline as `death_cause.py`: the words alone are
not enough (an agent can write "revoked" about anything, so only the result
record and the CLI's own retry events are read, never an assistant turn), a
401 alone is not enough (an EXPIRED token is the chain not renewing, the
opposite fact), and a run whose last word is a capacity wall
(`model_fallback.CAPACITY_SIGNATURES`) is that wall, whatever it retried on
earlier — re-running into a usage limit is what DRE-3171 stopped.

THE LINE IN THE LOG. The medic classifies off `gh run view --log-failed`, and
the action redacts the transcript out of the log, so the 401 never reaches it.
`death_receipt.py emit` — the receipt step every model-running workflow ends
with — prints `log_line()` when the record shows a rotation, and `in_log`
reads it back only where a PRINTED line sits: at the start of a line, or right
after the `job<TAB>step<TAB>timestamp ` prefix the log puts on every line. The
same anchoring as `medic_retry._LOG_CARD`: GitHub's echo of a step's script
carries a color code before the words, and prose quoting the mark mid-line is
not the line. `dead_run.limit_kind` vetoes the Claude wall on it.

CLI:
    python3 credential_rotation.py check <execution-file>
prints `rotation=true` and exits 0 for a rotation; prints `rotation=false` and
exits 1 for anything else, an unreadable file included — so a workflow can
write `if python3 … check "$FILE"; then`.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# What a capacity wall says (DRE-3970/3978), read where it lives. Import-safe:
# model_fallback does no I/O and imports nothing of ours.
import model_fallback  # noqa: E402 — after the path insert, by design

ROTATION_TAG = "credential-rotation"
LOG_MARK = f"{ROTATION_TAG}:"

_STATUS = "401"
_REVOKED = "revoked"
_RETRY_SUBTYPE = "api_retry"
_RETRY_ERROR = "authentication_failed"
_API_401 = re.compile(r"API Error:\s*401\b", re.I)
_LOG_LINE = re.compile(
    r"(?m)^(?:[^\t\n]*\t[^\t\n]*\t\S+ )?" + re.escape(LOG_MARK) + " "
)


def _messages(data) -> list[dict]:
    """The execution output as a list of messages: the action writes either
    the whole message list or the result record alone."""
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    return [data] if isinstance(data, dict) else []


def _final(messages: list[dict]) -> dict | None:
    """The result record — the last message that says whether it errored,
    the same one `execution_result.load_execution` returns."""
    for message in reversed(messages):
        if "is_error" in message:
            return message
    return None


def _retried_on_401(message: dict) -> bool:
    if message.get("type") != "system" or message.get("subtype") != _RETRY_SUBTYPE:
        return False
    return (str(message.get("error_status") or "").strip() == _STATUS
            or str(message.get("error") or "").strip() == _RETRY_ERROR)


def in_execution(data) -> bool:
    """Did this run die because its Claude token was revoked under it?

    `data` is the execution output as the action writes it. True only for a
    run that DIED (`is_error: true`) and either died on a 401 saying
    `revoked`, or retried a call on a 401 first; never when its last word is
    a capacity wall.
    """
    messages = _messages(data)
    final = _final(messages)
    if not final or final.get("is_error") not in (True, "true"):
        return False
    result = str(final.get("result") or "")
    lowered = result.lower()
    if any(sig in lowered for sig in model_fallback.CAPACITY_SIGNATURES):
        return False
    status = str(final.get("api_error_status") or "").strip()
    if (status == _STATUS or _API_401.search(result)) and _REVOKED in lowered:
        return True
    return any(_retried_on_401(m) for m in messages)


def from_file(path: str) -> bool:
    """`in_execution` over an execution file; False when there is none to read."""
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return in_execution(data)


def log_line() -> str:
    """The one line the receipt step prints for a rotation."""
    return (
        f"{LOG_MARK} the run's Claude token was revoked under it (a 401 that "
        "says revoked) — the console's renewal of the token chain does that to "
        "the token before it, so a re-run on the renewed token is the remedy, "
        "not a wait for a usage limit"
    )


def in_log(text: str) -> bool:
    """Does a failed run's log carry the rotation line where only a printed
    line can sit?"""
    return bool(_LOG_LINE.search(text or ""))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "check":
        print("usage: credential_rotation.py check <execution-file>", file=sys.stderr)
        return 2
    rotated = from_file(args[1])
    print(f"rotation={'true' if rotated else 'false'}")
    return 0 if rotated else 1


if __name__ == "__main__":
    sys.exit(main())
