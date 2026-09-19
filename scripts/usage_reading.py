#!/usr/bin/env python3
"""Keep each Claude run's own subscription-usage reading (DRE-4338, stdlib only).

Every headless Claude run emits a stream message

    {"type": "rate_limit_event", "rate_limit_info": {...}, "uuid": ..., "session_id": ...}

carrying the subscription windows the run is drawing on — per window a
`utilization` (a fraction, 0–1) and a `resetsAt` (epoch seconds), plus the
`status`, the `rateLimitType` and the overage fields. Two shapes exist and both
are read: the `unifiedWindows` map (five_hour / seven_day /
seven_day_overage_included, as Claude Code 2.1.278 emitted it on 2026-09-19)
and the flat single-window shape `@anthropic-ai/claude-agent-sdk` 0.3.263 —
the SDK the pinned action bundles — declares for `SDKRateLimitInfo`
(`status`, `resetsAt`, `rateLimitType`, `utilization`). The SDK says the event
is "emitted when rate limit info changes", so a run may emit several or none.

`anthropics/claude-code-action` (pinned sha 9c5ddab, base-action/src/
run-claude-sdk.ts:191) pushes every message into an array and writes it to
`$RUNNER_TEMP/claude-execution-output.json`; its `sanitizeSdkOutput()` prints
only `system/init` and `result` to the job log. The reading is therefore on the
runner's disk on every run and is destroyed with the runner. This module reads
it back out and the workflows upload it as a run artifact — no contract change,
no secret, nothing to deploy, and retrievable for 90 days so the Record can
BACKFILL from these once its ingestion lands.

WHAT IT READS, AND ONLY THAT. The execution file holds every assistant turn
and every tool result of the run — file contents, command output, whatever a
hostile PR contrived to get echoed — and several repos are public. This module
keeps entries whose `type` is exactly `rate_limit_event` and nothing else; a
`result` record or a `tool_result` that happens to carry a `rate_limit_info`
key is not one. tests/test_usage_reading.py plants a sentinel in every other
message and asserts it never reaches the written file, stdout or the step
summary — the same discipline execution_result.py holds the gates to.

THE SHAPE IS THE RECORD'S `body_raw`, NOT ITS ENVELOPE. The Record
(agent-bureau `config/record-contract.json`) wraps each delivery in an envelope
whose `received_at`, `relay_version` and `tenant_id` are facts about the
RELAY'S receipt; a run cannot fill them honestly, so it does not try. What the
run does know sits at the body's top level under the envelope's own names, so
the ingester copies rather than derives: `delivery_id` (fits the contract's
segment rule `[A-Za-z0-9_-]{1,128}`) and `repo`. `read_at` is the emitter's
clock and is deliberately NOT named `received_at`. `run.id`, `run.attempt` and
`read_at_epoch_ms` are positive ints — what the contract's `fact_key` accepts
for a dotted key part — so a reading is an EVENT keyed on all three: one per
run, and two readings never collapse into one on a redelivery.

WHAT THIS MODULE DOES NOT DECIDE. The `source` and `event` the Record files
these under, the fact key as declared in the contract, the storage shape and
the ingestion path are the Record card's (database architect). `RECORD_SOURCES`
is not edited here and no source name is declared here; `record_coverage.py`
refuses a second source by design until that card says which one the coverage
facts belong to (DRE-4288). A run cannot POST to the Record either: product
repos hold no AWS credentials at run time. And the ACCOUNT is unattributed:
it is whichever `CLAUDE_CODE_OAUTH_TOKEN` the repo held, the run's own record
does not say, and this module does not guess — `account.attributed` is always
false and attribution is the Record card's problem too.

Absent is a reading; zero is not. A run that emitted no event, a missing file
and a corrupt file each produce `reported: false` with a named reason and NO
`utilization` anywhere. The `emit` command never fails the job: a reading that
could not be taken is a line in the log, not a red build.

Usage (from a workflow step, after the last Claude step):

    python3 usage_reading.py emit --execution-file <path> --out <path> \
        [--card DRE-N] [--github-output "$GITHUB_OUTPUT"] \
        [--step-summary "$GITHUB_STEP_SUMMARY"]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

#: The body's own version tag. NOT the Record envelope's `record.v1` — that
#: is the wrapper's schema; this names the document inside `body_raw`.
SCHEMA = "claude-usage-reading.v1"

#: The one message type this module reads.
RATE_LIMIT_EVENT = "rate_limit_event"

#: The named reasons a reading is `reported: false`. Strings, not an enum: they
#: cross into the JSON file and the step summary.
REASON_NO_FILE = "no execution file"
REASON_NOT_JSON = "execution file is not JSON"
REASON_NOT_A_LIST = "execution file is not a message list"
REASON_NO_EVENT = "no rate_limit_event in this run"

#: The Record contract's own rule for an S3 key segment (agent-bureau
#: `console/backend/record_contract.py:_SEGMENT`). Restated because this repo
#: cannot import that one; `delivery_id` is built to fit it and the test pins
#: that it does.
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,128}")

#: A card id read out of the branch the fleet's gates already match on
#: (`agent/DRE-<n>-<slug>`). Only that prefix: `fix/*`, `repair/*` and
#: `dependabot/*` carry no card the way the merge gate reads it, and a guess
#: here would attribute a reading to work it was not spent on.
_CARD_IN_REF = re.compile(r"(?:^|/)agent/(DRE-\d+)(?:-|$)")

_PACIFIC = "America/Los_Angeles"


# --------------------------------------------------------------------------- #
# Reading the execution file                                                   #
# --------------------------------------------------------------------------- #

def load_rate_limit_events(path: str) -> tuple[list[dict], str | None]:
    """Every `rate_limit_event` message in the execution file, in order.

    Returns `(events, reason)`: `reason` is None when at least one event was
    read, else one of the REASON_* strings naming why there is none — a file
    that could not be read, one that is not the action's message list, or a
    run that simply emitted no event. Only entries that are dicts with
    `type == "rate_limit_event"` are kept; everything else in the file is
    never looked at again.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError:
        return [], REASON_NO_FILE
    except ValueError:
        return [], REASON_NOT_JSON
    if not isinstance(data, list):
        return [], REASON_NOT_A_LIST
    events = [entry for entry in data
              if isinstance(entry, dict) and entry.get("type") == RATE_LIMIT_EVENT]
    return events, (None if events else REASON_NO_EVENT)


# --------------------------------------------------------------------------- #
# Building the reading                                                         #
# --------------------------------------------------------------------------- #

def _positive_int(value: object) -> int | None:
    """An int >= 1, or None. Bools are refused by name (a bool is an int in
    Python) — the same rule the contract's fact_key applies."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number >= 1 else None
    return None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _iso_utc(seconds: object) -> str | None:
    """Epoch SECONDS as ISO-8601 UTC with a trailing Z, or None when the value
    is not a number this can place on a calendar. Deliberately no unit guess:
    the reported value travels beside it untouched."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    try:
        at = datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_identity(env: dict | None = None) -> dict:
    """The run's own identity, read from the runner's standard environment.

    Every value is None outside GitHub Actions; nothing is invented. The URL
    names the ATTEMPT, because a re-run is another delivery of another reading.
    """
    env = os.environ if env is None else env
    run_id = _positive_int(env.get("GITHUB_RUN_ID"))
    attempt = _positive_int(env.get("GITHUB_RUN_ATTEMPT"))
    repo = _text(env.get("GITHUB_REPOSITORY"))
    server = _text(env.get("GITHUB_SERVER_URL"))
    url = None
    if run_id and attempt and repo and server:
        url = f"{server}/{repo}/actions/runs/{run_id}/attempts/{attempt}"
    return {
        "id": run_id,
        "attempt": attempt,
        "workflow": _text(env.get("GITHUB_WORKFLOW")),
        "job": _text(env.get("GITHUB_JOB")),
        "event_name": _text(env.get("GITHUB_EVENT_NAME")),
        # The PR head on a pull_request run, else the ref the run was made on.
        "ref": _text(env.get("GITHUB_HEAD_REF")) or _text(env.get("GITHUB_REF_NAME")),
        "sha": _text(env.get("GITHUB_SHA")),
        "url": url,
        # `vars.CLAUDE_AUTH_MODE` decides whether the run used
        # CLAUDE_CODE_OAUTH_TOKEN (`subscription`) or ANTHROPIC_API_KEY. Not a
        # secret, and the one fact that says whether this is a subscription
        # reading at all.
        "auth_mode": _text(env.get("CLAUDE_AUTH_MODE")),
    }


def card_from_ref(ref: object) -> str | None:
    """`DRE-<n>` out of an `agent/DRE-<n>-<slug>` ref, else None."""
    if not isinstance(ref, str):
        return None
    found = _CARD_IN_REF.search(ref)
    return found.group(1) if found else None


def delivery_id(run: dict, read_at_epoch_ms: int) -> str:
    """`gha-<run_id>-<attempt>-<read_at_epoch_ms>`: run id + attempt + read
    time, so two readings are two deliveries and the same reading is the same
    one. `unknown` stands in outside Actions so the id still fits the segment
    rule; it never stands in for a real run."""
    run_id = run.get("id") or "unknown"
    attempt = run.get("attempt") or "unknown"
    value = f"gha-{run_id}-{attempt}-{read_at_epoch_ms}"
    if not _SEGMENT.fullmatch(value):  # pragma: no cover — ints and a fixed prefix
        raise ValueError(f"delivery_id {value!r} does not fit the Record's segment rule")
    return value


def _windows(info: dict) -> list[dict]:
    """One row per window, utilization and resetsAt exactly as reported.

    `unifiedWindows` when the message carries it; else the flat single-window
    shape, named by its `rateLimitType`; else no rows (a status-only event is
    still a reading).
    """
    unified = info.get("unifiedWindows")
    rows = []
    if isinstance(unified, dict):
        for name, window in unified.items():
            if not isinstance(window, dict):
                continue
            rows.append({
                "window": str(name),
                "utilization": window.get("utilization"),
                "resets_at": window.get("resetsAt"),
                "resets_at_iso": _iso_utc(window.get("resetsAt")),
            })
        return rows
    if "utilization" in info or "resetsAt" in info:
        rows.append({
            "window": _text(info.get("rateLimitType")) or "unspecified",
            "utilization": info.get("utilization"),
            "resets_at": info.get("resetsAt"),
            "resets_at_iso": _iso_utc(info.get("resetsAt")),
        })
    return rows


def reading(events: list[dict], *, run: dict, repo: str | None,
            read_at: datetime, card: str | None = None,
            reason: str | None = None) -> dict:
    """The document the artifact holds — the Record's `body_raw` for one run.

    `events` are the run's `rate_limit_event` messages in order; the rows are
    derived from the LAST one (the run's final state) and every event is kept
    whole under `raw`. `reason` names why there are none, when there are none.
    `read_at` must be timezone-aware; it is written as ISO-8601 UTC and as
    epoch milliseconds, and the latter is a fact-key part.
    """
    if read_at.tzinfo is None or read_at.utcoffset() is None:
        raise ValueError("read_at must be timezone-aware")
    read_at = read_at.astimezone(timezone.utc)
    epoch_ms = int(read_at.timestamp() * 1000)
    reported = bool(events)
    if not reported and reason is None:
        reason = REASON_NO_EVENT
    last = events[-1].get("rate_limit_info") if reported else None
    info = last if isinstance(last, dict) else {}
    return {
        "schema": SCHEMA,
        "delivery_id": delivery_id(run, epoch_ms),
        "repo": repo,
        "read_at": read_at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{read_at.microsecond // 1000:03d}Z",
        "read_at_epoch_ms": epoch_ms,
        "run": run,
        "card": card if card else card_from_ref(run.get("ref")),
        "account": {
            "attributed": False,
            "note": ("whichever CLAUDE_CODE_OAUTH_TOKEN the repo held when this "
                     "run started; the run's own record does not name it, and "
                     "attribution is the Record card's"),
        },
        "reported": reported,
        "not_reported_reason": None if reported else reason,
        "rate_limit_events_seen": len(events),
        "windows": _windows(info) if reported else [],
        "status": _text(info.get("status")),
        "rate_limit_type": _text(info.get("rateLimitType")),
        "overage": {
            "status": _text(info.get("overageStatus")),
            "disabled_reason": _text(info.get("overageDisabledReason")),
            "is_using_overage": info.get("isUsingOverage") if isinstance(info.get("isUsingOverage"), bool) else None,
            "resets_at": info.get("overageResetsAt"),
            "resets_at_iso": _iso_utc(info.get("overageResetsAt")),
        },
        # Every rate_limit_event as the action wrote it — key order preserved,
        # no field dropped. Not "as the CLI sent it": the action already
        # parsed and re-serialised the stream once (execution-file.ts).
        "raw": {"rate_limit_events": events},
        "record": {
            "role": "body_raw",
            "fact_key_parts": ["run.id", "run.attempt", "read_at_epoch_ms"],
            "declared_by": ("the Record card names the source and the event "
                            "(config/record-contract.json); this emitter declares neither"),
        },
    }


# --------------------------------------------------------------------------- #
# The human line                                                               #
# --------------------------------------------------------------------------- #

def _pacific(seconds: object) -> str | None:
    """`YYYY-MM-DD HH:MM PT` for a human, or None. GitHub answers in UTC and
    the CEO reads these to tell 'just now' from 'overnight'; a bare `01:00Z`
    reads as tomorrow. Falls back to UTC, labelled, where tz data is absent."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    try:
        at = datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    try:
        from zoneinfo import ZoneInfo
        return at.astimezone(ZoneInfo(_PACIFIC)).strftime("%Y-%m-%d %H:%M PT")
    except Exception:  # no tz database on this host — say so rather than guess
        return at.strftime("%Y-%m-%d %H:%M UTC")


def _percent(utilization: object) -> str:
    if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
        return "n/a"
    if 0 <= utilization <= 1:
        return f"{round(utilization * 100)}%"
    return f"{utilization} (as reported)"


def summary_line(doc: dict) -> str:
    """One line for the job summary and the log."""
    if not doc.get("reported"):
        return f"Claude usage: not reported by this run ({doc.get('not_reported_reason')})"
    parts = []
    for row in doc.get("windows") or []:
        reset = _pacific(row.get("resets_at"))
        text = f"{row['window']} {_percent(row.get('utilization'))}"
        if reset:
            text += f" (resets {reset})"
        parts.append(text)
    if not parts:
        parts.append("no window reported")
    parts.append(f"status {doc.get('status') or 'unknown'}")
    if doc.get("overage", {}).get("is_using_overage"):
        parts.append("using overage")
    parts.append("account unattributed")
    return "Claude usage: " + " · ".join(parts)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _append(path: str | None, line: str) -> None:
    if not path:
        return
    with open(path, "a") as f:
        f.write(line + "\n")


def emit(args: argparse.Namespace) -> int:
    events, reason = load_rate_limit_events(args.execution_file)
    doc = reading(
        events,
        run=run_identity(),
        repo=_text(os.environ.get("GITHUB_REPOSITORY")),
        read_at=datetime.now(timezone.utc),
        card=args.card or None,
        reason=reason,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        # No sort_keys: the raw block keeps the action's key order.
        json.dump(doc, f, indent=1)
        f.write("\n")
    line = summary_line(doc)
    print(line)
    print(f"usage reading written: {args.out} (delivery {doc['delivery_id']})")
    _append(args.step_summary, line)
    _append(args.github_output, f"path={args.out}")
    _append(args.github_output, f"reported={'true' if doc['reported'] else 'false'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("emit", help="write the run's reading and say it in one line")
    e.add_argument("--execution-file", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--card", default="")
    e.add_argument("--github-output", default="")
    e.add_argument("--step-summary", default="")
    args = parser.parse_args(argv)
    try:
        return emit(args)
    except Exception as exc:  # never a red build: a reading not taken is a log line
        print(f"usage reading: skipped ({type(exc).__name__}: {exc})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
