#!/usr/bin/env python3
"""WHICH wall a run hit — throttled, capped or revoked (stdlib only, no I/O).

DRE-4129. A run that dies on the Claude account's wall ends `is_error: true`,
one turn, $0 — and that is the SAME corpse shape for four different things:

  * the account was RATE-LIMITED (`throttled`) — it clears in minutes and
    names a `retry-after`;
  * the account is OUT OF USAGE OR SPEND (`capped`) — it clears at a stated
    reset, hours away, and no other model on the same subscription helps;
  * the credential was REVOKED (`revoked`) — nothing resets it, a new
    credential is the only remedy;
  * the service was briefly unreachable (`None`) — a 529 clears by itself in
    seconds and names no reset at all.

Today all four are reported as "an API/model death" and all four arm a model
swap that cannot help. The explanation was never missing:
`anthropics/claude-code-action@v1` redacts its own stdout but writes the raw
result record to the execution output file, and `execution_result.py` already
lifts a whitelist of diagnostic fields out of it. Nobody read them to answer
WHICH WALL. This module is that one reader, and nothing else.

THE DISCIPLINE, which is what makes it safe to add:

  * **It answers positively or not at all.** A cause is only ever returned on
    a signature that is actually PRESENT, never inferred from the absence of
    another one. A record the module cannot read answers `None`, and `None`
    leaves every caller behaving exactly as it does today.
  * **It reads the RESULT RECORD ONLY, through the one audited whitelist**
    (`execution_result._DIAGNOSTIC_FIELDS`). Every field access below goes
    through `_whitelisted()`, so the transcript, `env`, and the bare `error`
    field the action also writes are structurally out of reach — the same
    rule execution_result's printer already lives by, for the same reason
    (that record carries file contents, command output and the whole
    environment).
  * **The turn-cap veto comes FIRST, and positively** (DRE-3499). The
    capacity sentences are ordinary English an agent writes about itself
    after merely READING the standard that quotes them: on 2026-09-07 the
    medic marked epic DRE-3257 `kind=claude` for a review that ran 51 turns
    against a 48-turn ceiling and ended `"subtype": "success"`. The evidence
    is the action's own — `check_agent_result._turn_cap_evidence` over the
    whitelisted fields, and `dead_run.turn_cap_in_text` over the text they
    render to.
  * **Every list is read where it lives.** The capacity sentences are
    `model_fallback.CAPACITY_SIGNATURES` (DRE-3970/3978), the transient
    overload exclusion is `dead_run._CAPACITY_NOT_A_LIMIT_DEATH`, the stated
    reset is resolved by `dead_run.limit_reset`, and the quote's ceiling is
    `execution_result._VALUE_CAP`. A second copy of any of them is how two
    readers of the same payload quietly stop agreeing.

The three answer strings are a CONTRACT four sibling cards depend on, which
is why this card is first: a rule the pieces share is split out once so they
cite a declared answer instead of each inventing one.

ORDER OF THE THREE. They are positive, near-disjoint signatures, so the order
rarely matters — except in the one case where it decides a real reading. The
subscription's usage wall arrives AS a 429: "You've hit your limit · resets
8:30pm (UTC)" with `api_error_status: 429`. Reported as `throttled` that says
"clears in minutes, retry" about a window that resets tonight, and the retry
walks straight back into it. So the SPEND/USAGE sentences are read before the
status, and `revoked` — the narrowest test of the three, a 401/403 that also
carries an authentication signature — is read before both.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The turn-cap evidence as the action itself writes it (DRE-3499), and the
# clock that already resolves "resets 8:30pm (UTC)". Import-safe: neither
# module does I/O at import.
import check_agent_result  # noqa: E402 — after the path insert, by design
import dead_run  # noqa: E402 — after the path insert, by design
import execution_result  # noqa: E402 — after the path insert, by design
import model_fallback  # noqa: E402 — after the path insert, by design

# The three answers. Sibling cards cite these strings, not their spelling.
THROTTLED = "throttled"
CAPPED = "capped"
REVOKED = "revoked"

# The ONE field read by name rather than swept for text — a status is a code,
# and `429` found loose in a body is somebody quoting a number. It is a member
# of the shared whitelist, never a second list.
_STATUS_FIELD = "api_error_status"
# Where a provider error BODY lands on the record, best first. Both are
# whitelisted; `result` carries the provider's error string on the crash path.
_BODY_FIELDS = ("result", "errors")

# throttled — the ACCOUNT was rate-limited. The status, the body's own
# sentence, or the API's error type.
_THROTTLED_STATUSES = ("429",)
_THROTTLED_SIGNATURES = (
    "would exceed your account's rate limit",
    "rate_limit_error",
)

# capped — the account is out of usage or spend. The capacity list
# model_fallback already owns, minus the rate limit (that is `throttled`
# above, and a rate limit clears in minutes) and minus the transient overload
# dead_run already refuses to treat as a wall. Derived, never retyped: a new
# vendor sentence is one edit, in the file that owns it.
_NOT_CAPPED = ("rate_limit_error",) + dead_run._CAPACITY_NOT_A_LIMIT_DEATH
_CAPPED_SIGNATURES = tuple(
    sig for sig in model_fallback.CAPACITY_SIGNATURES if sig not in _NOT_CAPPED
)

# revoked — the credential is dead. BOTH halves are required: a 401/403 that
# carries an authentication signature. A bare 401 is not enough (a proxy can
# answer one), and the words alone are not enough (an agent can write them).
_REVOKED_STATUSES = ("401", "403")
_REVOKED_SIGNATURES = ("invalid_grant", "revoked", "authentication_error")

# `retry-after: 60`, `"retry-after": "60"`, `Retry-After = 60` — the header as
# the action records it, wherever in the whitelisted fields it lands. Seconds:
# the only form the Anthropic API sends.
_RETRY_AFTER = re.compile(r"retry[-_ ]?after[\"']?\s*[:=]\s*[\"']?(\d+)", re.I)


def _whitelisted(record: dict | None) -> dict:
    """The record, cut down to the whitelisted diagnostic fields.

    THE ONE PLACE THIS MODULE TOUCHES THE RECORD. Everything below reads this
    projection, so "reads only the whitelist" is a property of the code's
    shape rather than a claim about it — including the readers this module
    delegates to, which are handed the projection and never the record.
    """
    if not isinstance(record, dict):
        return {}
    fields = {}
    for field in execution_result._DIAGNOSTIC_FIELDS:
        value = record.get(field)
        if value is None or value == "" or value == [] or value == {}:
            continue
        fields[field] = value
    return fields


def _render(value: object) -> str:
    """A whitelisted value as text. Uncapped on purpose — the cap belongs to
    what a human reads (`quote`), and a signature cut in half by it would be
    a wall the module stopped recognising."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _diagnostic_text(record: dict | None) -> str:
    """The whitelisted fields as the one text the signatures are read from.

    Rendered `field: value` per line, which is the shape
    `execution_result.print_failure_detail` already writes into the run log —
    so `dead_run.turn_cap_in_text`, which was written to read exactly that
    log, reads this the same way.
    """
    fields = _whitelisted(record)
    return "\n".join(f"{name}: {_render(value)}" for name, value in fields.items())


def _status(fields: dict) -> str:
    """`api_error_status` as a string: the action writes it as a number on
    some payloads and a string on others."""
    return str(fields.get(_STATUS_FIELD) or "").strip()


def cause(record: dict | None) -> str | None:
    """THROTTLED, CAPPED, REVOKED — or None, which is every other death.

    `record` is a final result record as `execution_result.load_execution`
    returns one. Only its whitelisted diagnostic fields are read.

    None is the answer for everything this module cannot POSITIVELY name,
    `overloaded_error` included: a 529 is the service being busy, it clears by
    itself in seconds and it names no reset, which is exactly why
    `dead_run._CAPACITY_NOT_A_LIMIT_DEATH` already refuses to treat it as a
    wall. A caller that gets None behaves exactly as it does today.
    """
    fields = _whitelisted(record)
    if not fields:
        return None
    text = _diagnostic_text(record)
    # THE VETO, FIRST AND POSITIVE (DRE-3499). A run that hit the turn ceiling
    # did real work and did not meet an account wall, whatever words its own
    # closing message carries. Both readers are the ones that already own this
    # evidence, handed the whitelisted projection.
    if check_agent_result._turn_cap_evidence(fields) or dead_run.turn_cap_in_text(text):
        return None
    lowered = text.lower()
    status = _status(fields)
    if status in _REVOKED_STATUSES and any(
        sig in lowered for sig in _REVOKED_SIGNATURES
    ):
        return REVOKED
    if any(sig in lowered for sig in _CAPPED_SIGNATURES):
        return CAPPED
    if status in _THROTTLED_STATUSES or any(
        sig in lowered for sig in _THROTTLED_SIGNATURES
    ):
        return THROTTLED
    return None


def _retry_after(text: str) -> int | None:
    """The `retry-after` window in seconds, or None when nothing states one."""
    found = _RETRY_AFTER.search(text)
    return int(found.group(1)) if found else None


def reset(record: dict | None, now: datetime) -> datetime | None:
    """When the wall comes down, UTC — or None when the record does not say.

    `throttled` names a `retry-after` and clears that many seconds from now.
    `capped` states its own reset — `resets 8:30pm (UTC)` — resolved by
    `dead_run.limit_reset`, which already answers "today, or tomorrow when
    today's has passed" and is the answer the recovery sweep waits on.
    `revoked` has no reset at all: a new credential is the only remedy, and a
    sweep handed a time would wait for a window that never comes.

    A naive `now` is read as UTC. Callers pass `datetime.now(UTC)`; reading a
    naive one as the runner's local time is how a reset lands hours out.
    """
    found = cause(record)
    if found is None or found == REVOKED:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    text = _diagnostic_text(record)
    if found == THROTTLED:
        seconds = _retry_after(text)
        return now.astimezone(UTC) + timedelta(seconds=seconds) if seconds else None
    return dead_run.limit_reset(text, "claude", now)


def quote(record: dict | None) -> str:
    """The provider's own error body, capped — or "" when there is none.

    The ceiling is `execution_result._VALUE_CAP`, read from the constant the
    failure printer already caps every value at: the first few hundred
    characters always carry the error class and the rest just buries the log.
    Transcript text is structurally unreachable here — `_whitelisted` is the
    only way in, and the whitelist is the audited one.
    """
    fields = _whitelisted(record)
    for name in _BODY_FIELDS:
        if name in fields:
            return _render(fields[name])[: execution_result._VALUE_CAP]
    return ""
