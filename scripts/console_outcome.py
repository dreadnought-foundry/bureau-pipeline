#!/usr/bin/env python3
"""The outcome door's client: the Operator Agent records a `do` card's outcome
through the console (DRE-8925, epic DRE-8738).

A `no-code` card the Operator Agent closes is closed by the console, never by
a bare move to Done: the console writes its own `Outcome:` record and moves
the card (agent-bureau DRE-8926), and that record is what the relay's lane
guard (DRE-7327) reads, so the guard never returns the close. This module is
the one client for that door. Stdlib only, shaped like `pipeline_notice.py`,
whose base URL check, OIDC mint and audience it imports rather than restates.

## The door

`POST $BUREAU_READ_URL/api/v1/pipeline/outcomes` with the JSON body
`{"card": "DRE-<n>", "outcome": "<one sentence>", "receipt_url": "<url>"}`
and `Authorization: Bearer <GitHub Actions OIDC JWT>`, minted fresh for the
call for the audience `$BUREAU_NOTICE_AUDIENCE` (default
`pipeline_notice.DEFAULT_AUDIENCE`). It answers 200 with `{"card":
"DRE-<n>", "outcome": "recorded" | "already-recorded"}`; 400, 401, 403, 409
and 503 are its refusals.

## It hands the answer back, and writes nothing else

`record` returns an `Answer` — the status, the door's outcome, the one word
the log line carries and the detail — and the caller classes it:
`operator_agent_apply.py do-close` reads 400 and 409 (`NEVER`: the card is not
`no-code`, or not in a lane the door closes from) as a close that can never
land, and every other miss as one that may land on a later pass. With no
door — no `BUREAU_READ_URL`, no OIDC token, an answer it cannot read — the
close is refused and the card waits. It never falls back to writing an
`Outcome:` comment itself: that record is the console's, and a forged one is
exactly what the lane guard exists to catch.

## The one line

Exactly one line per call on stdout, under `TAG`:

    console-outcome: <outcome> card=DRE-<n>

`<outcome>` is the door's own word on a 200 (`recorded`,
`already-recorded`), and otherwise the miss: `no-url`, `bad-url`,
`no-token`, `timeout`, `unreachable`, `http-<status>` or `malformed`. The
detail goes to stderr.

## Usage

    python3 scripts/console_outcome.py record --card DRE-<n> --outcome <sentence> \\
        --receipt-url <url>

exits 0 when the door recorded the outcome, and 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_notice  # noqa: E402

TAG = "console-outcome:"
PATH = "/api/v1/pipeline/outcomes"
#: What a 200 may say. Anything else is a malformed answer.
OUTCOMES = ("recorded", "already-recorded")
#: The refusals that say the close can never land.
NEVER = (400, 409)

#: One attempt, bounded. A module constant so the tests can shrink it.
TOTAL_TIMEOUT = 15.0


@dataclass(frozen=True)
class Answer:
    """The door's answer to one call. `status` is None when no HTTP answer
    came; `outcome` is the door's word on a well-formed 200, else None;
    `reason` is the one word the log line carries."""

    status: int | None
    outcome: str | None
    reason: str
    detail: str = ""

    @property
    def recorded(self) -> bool:
        return self.outcome in OUTCOMES

    @property
    def never(self) -> bool:
        """True when the door refused the outcome for good."""
        return self.status in NEVER


def _http_post(url: str, payload: dict, headers: dict) -> tuple[int, bytes]:
    """One POST, one attempt: the status and the body, whatever the status."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TOTAL_TIMEOUT) as resp:  # nosec B310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        finally:
            e.close()


def _ask(card: str, outcome: str, receipt_url: str) -> Answer:
    try:
        base = pipeline_notice._base()
        token = pipeline_notice._mint()
    except pipeline_notice.DoorMissed as e:
        return Answer(None, None, e.reason, e.detail)
    url = f"{base.scheme}://{base.netloc}{base.path.rstrip('/')}{PATH}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Accept": "application/json"}
    payload = {"card": card, "outcome": outcome, "receipt_url": receipt_url}
    try:
        status, raw = _http_post(url, payload, headers)
    except (TimeoutError, socket.timeout) as e:
        return Answer(None, None, "timeout", str(e))
    except Exception as e:  # noqa: BLE001 — every other transport failure
        reason = getattr(e, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return Answer(None, None, "timeout", str(e))
        return Answer(None, None, "unreachable", f"{type(e).__name__}: {e}")
    if status != 200:
        detail = raw.decode("utf-8", "replace").strip()[:300]
        return Answer(status, None, f"http-{status}", detail or "no body")
    try:
        said = json.loads(raw.decode())
    except ValueError:
        return Answer(status, None, "malformed", "the 200 was not JSON")
    word = said.get("outcome") if isinstance(said, dict) else None
    if word not in OUTCOMES:
        return Answer(status, None, "malformed", f"the 200 carried outcome {word!r}")
    if said.get("card") != card:
        return Answer(status, None, "malformed", f"the 200 named {said.get('card')!r}")
    return Answer(status, word, word)


def record(card: str, outcome: str, receipt_url: str) -> Answer:
    """Record `outcome` for `card` through the console's outcome door, citing
    the receipt comment at `receipt_url`. Prints the one line; never raises
    for a door that did not take the call."""
    answer = _ask(card, outcome, receipt_url)
    print(f"{TAG} {answer.reason} card={card}", flush=True)
    if answer.detail:
        print(f"console-outcome: {card} — {answer.reason}: {answer.detail}",
              file=sys.stderr, flush=True)
    return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record a card's outcome through the console")
    r.add_argument("--card", required=True)
    r.add_argument("--outcome", required=True)
    r.add_argument("--receipt-url", required=True)
    args = parser.parse_args(argv)
    return 0 if record(args.card, args.outcome, args.receipt_url).recorded else 1


if __name__ == "__main__":
    sys.exit(main())
