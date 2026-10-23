#!/usr/bin/env python3
"""Was a build's API-error death a WAIT rather than a strike? (DRE-7795)

Today `report_agent_result.sh` sends every `api_death`
(`check_agent_result.classify_death`) to `dead_run.py decide --is-error`: one
of the card's three strikes is spent and a `model-error:` marker switches the
model. Two of those deaths are nobody's fault and a fresh run is the remedy:

  * `rotation` — our own renewal revoked the run's Claude token under it
    (DRE-5856), and a fresh job is handed the renewed one;
  * `vendor-transient` — the vendor answered a momentary 529 `overloaded_error`
    or a 500/502/503/504, which clears by itself.

This module is the one reader that names them, and nothing else. It answers
POSITIVELY or not at all, the `death_cause.py` discipline: a kind is returned
only on a signature that is present, and None — every other death, a record
it cannot read included — leaves the death decided exactly as today, a strike.
DRE-7800 turns a kind into a marker instead of a strike, DRE-7801 caps
repeated waits and DRE-7802 wires the report script to ask; until then nothing
calls this module and no death changes.

ONE READER OF EACH FACT. The renewal 401 is `credential_rotation.in_execution`,
the turn-cap veto is `check_agent_result._turn_cap_evidence`, the capacity wall
is `model_fallback.capacity_refusal`, and the result record is read only
through `execution_result._DIAGNOSTIC_FIELDS`, never the transcript. None of
them is copied here: a copy written to keep this module stdlib-only is a
second reader that drifts from the first, and importing `model_fallback` is
the price of having one reader of a capacity wall. Each is imported inside the
function that needs it.

WHERE THIS DEPARTS FROM THE EPIC, on purpose. The epic says a 401 inside a
renewal window stays on the DRE-5856 path; this reader instead reads the
renewal 401 by why the token died, not by the clock — `in_execution` keys on
the 401's wording (`revoked`, or the CLI retrying a 401 and then dying on one
that does not say `expired`), so a 401 inside a renewal window that carries
neither shape reads None and stays a strike, and a `revoked` 401 outside one
reads `rotation` — and it makes that 401 a wait rather than today's strike and
model switch, because the wait's re-entry on the sweep's next pass gives the
card the same fresh run on the renewed token that DRE-5856's re-run does,
without spending a strike (DRE-7805 makes the medic read the marker as a wait,
so the card is brought back once, not twice).

CLI:
    python3 vendor_wait.py kind <execution-file>
prints `rotation`, `vendor-transient` or `none`, and exits 0 either way — an
unreadable file prints `none`.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROTATION = "rotation"
VENDOR_TRANSIENT = "vendor-transient"
# The contract: DRE-7800 writes these as the marker's `kind=`, DRE-7801 counts
# them and agent-bureau's DRE-7796 reads them off the marker.
KINDS = (ROTATION, VENDOR_TRANSIENT)
NONE = "none"

# The vendor's momentary answers. A status is a code, read by name off the
# result record — `529` found loose in a body is somebody quoting a number.
_STATUS_FIELD = "api_error_status"
_TRANSIENT_STATUSES = ("529", "500", "502", "503", "504")
_OVERLOADED = "overloaded_error"


def _result_record(execution) -> dict | None:
    """The result record — the last message that says whether it errored, as
    `execution_result.load_execution` picks it; the action writes either the
    whole message list or the record alone."""
    if isinstance(execution, dict):
        return execution if "is_error" in execution else None
    if isinstance(execution, list):
        for message in reversed(execution):
            if isinstance(message, dict) and "is_error" in message:
                return message
    return None


def _whitelisted(record: dict) -> dict:
    """The record cut down to the audited diagnostic fields — the only view of
    it the vendor reading below ever sees."""
    import execution_result

    fields = {}
    for field in execution_result._DIAGNOSTIC_FIELDS:
        value = record.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue
        fields[field] = value
    return fields


def _text(fields: dict) -> str:
    """The whitelisted fields as one `field: value` text, the shape
    `execution_result.print_failure_detail` writes."""
    lines = []
    for name, value in fields.items():
        rendered = value if isinstance(value, str) else json.dumps(value, default=str)
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)


def _rotation(execution) -> bool:
    """Our own renewal revoked the token — DRE-5856's reader, unchanged."""
    import credential_rotation

    return credential_rotation.in_execution(execution)


def _vendor_transient(record: dict) -> bool:
    """A 529/5xx or `overloaded_error` on a run that neither hit the turn
    ceiling nor met a capacity wall."""
    import check_agent_result
    import model_fallback

    fields = _whitelisted(record)
    text = _text(fields)
    status = str(fields.get(_STATUS_FIELD) or "").strip()
    if status not in _TRANSIENT_STATUSES and _OVERLOADED not in text.lower():
        return False
    # A run that hit the turn ceiling did real work, whatever its last call
    # answered (DRE-3499).
    if check_agent_result._turn_cap_evidence(fields):
        return False
    # The overload is the one capacity signature that is the vendor being
    # busy; every other one is the account's wall, which a wait cannot clear.
    wall = model_fallback.capacity_refusal(text)
    return wall is None or wall == _OVERLOADED


def kind(execution) -> str | None:
    """`rotation`, `vendor-transient` — or None, which is every other death.

    `execution` is the execution output as the action writes it: the message
    list, or the result record alone. Only a run that died (`is_error: true`)
    is ever a wait.
    """
    record = _result_record(execution)
    if record is None or record.get("is_error") not in (True, "true"):
        return None
    if _rotation(execution):
        return ROTATION
    if _vendor_transient(record):
        return VENDOR_TRANSIENT
    return None


def from_file(path: str) -> str | None:
    """`kind` over an execution file; None when there is none to read."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return kind(data)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "kind":
        print("usage: vendor_wait.py kind <execution-file>", file=sys.stderr)
        return 2
    print(from_file(args[1]) or NONE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
