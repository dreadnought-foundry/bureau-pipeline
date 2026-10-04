#!/usr/bin/env python3
"""The read door's client: the pipeline reads the board from OUR database (#5).

Stage 2 moves the pipeline's board reads off Linear and onto the console's
record of it. Reconcile is the first reader (~640 Linear requests an hour, most
of them the same board re-read); the console serves the same rows from the
table the Linear feed already keeps current. This module is the one door the
pipeline reads it through. Stdlib only — it runs inside every reusable workflow.

## What a caller gets, and what it never gets

`board()`, `cards()`, `dependents()` and `workflow_states()` each return a
`DoorRead` whose `nodes` are byte-compatible with what the same reader takes
from Linear's GraphQL — the field paths are declared below and pinned against
the live GraphQL selections by `tests/test_read_door_shape_parity.py`.

Every one of them either returns the WHOLE answer or raises `ReadUnknown`.
There is no partial answer: a missing card, a missing field, a lane the door
did not affirm, an age past the caller's limit — each is UNKNOWN, and the
caller falls back to Linear for that read. "The door could not say" is never
allowed to read as "there is nothing there" (the confident empty that once let
an empty In Progress lane promote twelve cards at once, Stage 2 review H2).

## When the door is not used

* `BUREAU_READ` is `off` (the default). Read ONCE per process, printed, and
  never re-read: a run keeps the mode it started in (Stage 2 item 35).
* After ONE failure — network, timeout, 5xx, 401/403, 429, a malformed answer —
  the door is not asked again for the rest of the run: every later read goes
  straight to Linear (design §3). One slow door must not cost a sweep eight
  seconds per read.
* No OIDC token: the stub did not grant `id-token: write`. That is a fallback,
  never a red run.
* A `pull_request` or `pull_request_target` run (`GITHUB_EVENT_NAME`): the door
  refuses those tokens by design (its check 7, review S7), so the client never
  mints one or asks (BP-8). Nearly every QA Review, Verify and Linear Sync run
  is one; they read Linear exactly as before.

## The one UNKNOWN that is not a fallback

`reason=linear-hold` means the console's Linear key is rate-limited — the same
bucket the fallback would spend. `ReadUnknown.skip` is True for it (and for a
429 while this process already knows its own key is held): the caller SKIPS the
phase this pass, says so in one line, and makes no Linear call for it (Stage 2
review H1). Falling back there would deepen the hold that caused it, fleet-wide.

## The contract the console door (agent-bureau AB-1) must match

Base: `$BUREAU_READ_URL` + `/api/v1/pipeline/<endpoint>`. https only, except a
loopback host (the cross-repo harness).

Headers on every request:
  `Authorization: Bearer <GitHub Actions OIDC JWT>` (audience
  `$BUREAU_READ_AUDIENCE`, default `DEFAULT_AUDIENCE` — the door's
  `PIPELINE_READ_AUDIENCE` default too), `X-Bureau-Max-Age: <seconds>`
  (always), `X-Bureau-Relations-Max-Age: <seconds>` (when `relations=1`),
  `Accept: application/json`. A FRESH token every request: the door claims
  each `jti` once (replay, S3).

Endpoints (query values comma-joined, URL-encoded; `relations` always SAID,
because the door serves relations when it is left out):
  GET /board?lanes=<L1,L2>&scope=repo|fleet&comments=50&relations=0|1
  GET /cards?ids=<DRE-1,DRE-2>&comments=50|all&relations=0|1
      — `all`: every stored comment, and only a provably whole thread is FRESH
        to this client (`thread-incomplete` otherwise)
  GET /cards/<id>/dependents?lanes=<L>&comments=50&relations=1
      — the cards <id> blocks, in the lanes asked, as board nodes
  GET /workflow-states
  GET /planning-order
      — the planner line's order (DRE-5807), outside the envelope below:
        `{"order": ["DRE-…", …], "set_at", "set_by", "read_at"}`. Read
        whatever the mode, and a failure never stops the door for the run
        (`planning_order()` says why).

Envelope (HTTP 200 whenever the caller is authenticated):
  {"schema": "bureau-read/1", "verdict": "FRESH"|"UNKNOWN",
   "freshness": {"as_of", "age_seconds", "basis", "relations_as_of", "reason"},
   "caller": {"repository", "slug", "tenant", "scope"},
   "viewer": {"id"},
   "lanes": [<every lane served>]          # /board only — see below
   "issues": {"nodes": [...]} | null,
   "workflowStates": {"nodes": [...]}       # /workflow-states only
  }
A FRESH /board must ECHO the lanes it is affirming in `lanes`, equal as a set
to the lanes asked for: a door that does not hold a lane says so (UNKNOWN
`lane-not-held`) rather than answering with an empty one. A FRESH /cards must
carry every id asked for, and no other. `UNKNOWN` carries `issues: null` and a
machine reason in `freshness.reason` (`stale`, `relations-stale`,
`reread-pending`, `linear-hold`, `missing-field`, `lane-not-held`).

Statuses: 200; 401/403 (refused — the client stops using the door this run);
404 on /cards or /dependents (a card outside the tenant — UNKNOWN for that one
read, the door stays in use); 429 (throttled — stops this run, on purpose: a
sweep that waits out `Retry-After` holds its runner, and its reads fall back to
Linear); 503 (closed or the database is down — stops this run). Any other
status stops this run too.
"""
from __future__ import annotations

import atexit
import base64
import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

SCHEMA = "bureau-read/1"
PATH_PREFIX = "/api/v1/pipeline"
#: The one audience both sides default to: this client asks GitHub for it, and
#: the door's `PIPELINE_READ_AUDIENCE` defaults to it (agent-bureau's
#: `github_oidc.DEFAULT_AUDIENCE`). Overridable on both, together.
DEFAULT_AUDIENCE = "https://app.agent-bureau.com/pipeline-read"

MODE_ENV = "BUREAU_READ"
URL_ENV = "BUREAU_READ_URL"
AUDIENCE_ENV = "BUREAU_READ_AUDIENCE"
#: The ref the reusable checked its scripts out at (`inputs.pipeline_ref`). When
#: it is set, the client refuses the door unless the OIDC token's
#: `job_workflow_ref` names the same ref (Stage 2 item 38, review M1).
PIPELINE_REF_ENV = "BUREAU_PIPELINE_REF"
MODES = ("off", "shadow", "on")

# ── Per-reader freshness limits (Stage 2 item 39, review L2) ────────────────
# Written down once, here, and REQUIRED on every call: the client refuses to
# send a request without one, because a door asked for "whatever you have"
# answers FRESH about data of any age.
#: The board a sweep decides on: two minutes.
BOARD_MAX_AGE = 120
#: A card an agent run is about to be dispatched at: one minute (BP-4).
DISPATCH_CARDS_MAX_AGE = 60
#: The workflow-state map a lane move turns a lane name into an id with (BP-3):
#: an hour. A state's id never changes, and a renamed lane is a name the door
#: does not hold yet — a miss, which the caller asks Linear for. Every move
#: still re-reads its card live before it writes (DRE-2316).
WORKFLOW_STATES_MAX_AGE = 3600
#: Relations come from the console's 15-minute poll (no relation webhook
#: exists), so twenty minutes is one missed poll. Not the safety on its own:
#: every promotion re-reads its blockers live before it writes (item 32).
RELATIONS_MAX_AGE = 1200
#: The card a critic or verifier is about to judge (BP-8, fix #14): its
#: **Design:** line, labels and the build's model heartbeat. Two minutes, the
#: board's bound: nothing here writes, and every one of those facts was set
#: long before the pull request it is reviewing.
REVIEW_CARD_MAX_AGE = 120

#: The events whose OIDC token the door refuses by design (its check 7, review
#: S7): a private repo that sends write tokens to fork pull requests would
#: otherwise hand a fork a door token. Asking from one buys nothing but a
#: refusal the door counts toward its refused-and-never-served alarm (M2), so
#: the client never asks: no token is minted, nothing is sent, and the run
#: reads Linear as it always did. A QA Review, a Verify and a Linear Sync run
#: on `pull_request` — almost every one of them.
EVENT_ENV = "GITHUB_EVENT_NAME"
REFUSED_EVENTS = frozenset({"pull_request", "pull_request_target"})

#: Timeouts (item 40, review M4): one attempt, connect within a second, the
#: whole exchange within eight. Module constants so the tests can shrink them.
CONNECT_TIMEOUT = 1.0
TOTAL_TIMEOUT = 8.0

LINEAR_HOLD = "linear-hold"

# ── The node shape (design §3, byte-compatible with Linear's GraphQL) ───────
# A nested dict of the field PATHS a node must carry: `None` is a leaf, a dict
# is a selection. `nodes` under a connection means "each node has this shape".
# `tests/test_read_door_shape_parity.py` derives the same trees from the
# GraphQL text reconcile.py actually sends and fails if they drift apart.
_COMMENTS = {
    "pageInfo": {"hasNextPage": None, "endCursor": None},
    "nodes": {"body": None, "createdAt": None, "user": {"id": None}},
}
_INVERSE_RELATIONS = {
    "pageInfo": {"hasNextPage": None, "endCursor": None},
    "nodes": {"type": None, "issue": {"identifier": None, "state": {"name": None}}},
}
#: Every field `_fetch_active_cards` and `backlog_children` select, as one
#: shape: the door serves one card one way whichever reader asked.
CARD_FIELDS = {
    "id": None,
    "identifier": None,
    "title": None,
    "description": None,
    "createdAt": None,
    "updatedAt": None,
    "priority": None,
    "state": {"name": None},
    "labels": {"nodes": {"name": None}},
    "parent": {"identifier": None, "state": {"name": None}},
    "children": {"nodes": {"id": None}},
    "comments": _COMMENTS,
}
#: Added to CARD_FIELDS when a read asks for `relations=1`.
RELATION_FIELDS = {"inverseRelations": _INVERSE_RELATIONS}
WORKFLOW_STATE_FIELDS = {"id": None, "name": None, "type": None}

#: Fields whose VALUE may be null (Linear sends null for them too). Every other
#: leaf must be present and non-null; a null where Linear never sends one is
#: the "state: null" the dependency gate would crash on (review C2).
_NULLABLE = frozenset({
    "description", "parent", "user", "endCursor", "priority", "relatedIssue",
})


class ReadUnknown(Exception):
    """The door could not give the WHOLE answer. Never carries partial data.

    `reason` is the machine reason (the door's `freshness.reason`, or the
    client's own: `unavailable`, `timeout`, `refused`, `throttled`,
    `malformed`, `not-found`, `no-token`, `pipeline-ref-mismatch`, …).
    """

    def __init__(self, reason: str, detail: str = "", *, status: int | None = None,
                 unavailable: bool = False, linear_held: bool = False):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.status = status
        self.unavailable = unavailable
        self._linear_held = linear_held

    @property
    def skip(self) -> bool:
        """True when falling back to Linear would spend a held bucket: the
        caller skips the phase this pass instead (item 34)."""
        if self.reason == LINEAR_HOLD:
            return True
        return self.status == 429 and self._linear_held


@dataclass
class DoorRead:
    """One whole answer: the nodes, and how fresh the door says they are."""

    nodes: list
    as_of: str | None = None
    age_seconds: float | None = None
    relations_as_of: str | None = None
    viewer_id: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


# ── Process state: one process is one run ──────────────────────────────────
_state: dict = {}


def reset_for_tests() -> None:
    """A fresh run: mode unread, counters zero, door not disabled, no token."""
    _state.clear()
    _state.update(
        mode=None,
        served=0,
        unknown=0,
        unavailable=0,
        skipped=0,
        door_ms=0.0,
        disabled=None,  # the reason the door stopped being asked, or None
        reported=False,
    )


reset_for_tests()


def _say(line: str) -> None:
    """Telemetry goes to stderr, the way `linear-budget:` does: some workflows
    read a script's stdout through `$(...)`, and the sweep's step tees both."""
    print(line, file=sys.stderr, flush=True)


def mode() -> str:
    """`off` / `shadow` / `on`, read from `BUREAU_READ` ONCE and printed.

    A value outside the three is `off`, said out loud: a typo in a repository
    variable must never be read as "on".
    """
    if _state["mode"] is not None:
        return _state["mode"]
    raw = os.environ.get(MODE_ENV, "")
    value = raw.strip().lower() or "off"
    if value not in MODES:
        _say(f"read-door: {MODE_ENV}={raw!r} is not one of {'/'.join(MODES)} — "
             "reading as 'off'")
        value = "off"
    _state["mode"] = value
    _say(f"read-door: mode {value} ({MODE_ENV}={raw or 'unset'}, read once for this run)")
    return value


def enabled() -> bool:
    """Should a reader ask the door at all right now?"""
    return mode() != "off" and _state["disabled"] is None


def disabled_reason() -> str | None:
    return _state["disabled"]


def note_skip(what: str, reason: str) -> None:
    """The one line a skipped phase prints (item 34), counted for the exit line."""
    _state["skipped"] += 1
    _say(f"read-door: skipped {what} this pass — the door says {reason}; "
         "no Linear fallback (it would spend the held bucket)")


def exit_line() -> str:
    """`read-door: served <n> unknown <u> unavailable <x> (mode <m>) <ms>ms`."""
    return (
        f"read-door: served {_state['served']} unknown {_state['unknown']} "
        f"unavailable {_state['unavailable']} (mode {_state['mode'] or 'off'}) "
        f"{int(round(_state['door_ms']))}ms"
    )


def _report_at_exit() -> None:
    if _state["reported"] or (_state["mode"] in (None, "off")):
        return
    _state["reported"] = True
    try:
        _say(exit_line())
        if _state["skipped"]:
            _say(f"read-door: skipped {_state['skipped']} phase(s) on {LINEAR_HOLD}")
    except Exception:  # pragma: no cover — telemetry never fails an exit
        pass


atexit.register(_report_at_exit)


def _disable(reason: str, detail: str) -> None:
    if _state["disabled"] is None:
        _state["disabled"] = reason
        _say(f"read-door: unavailable ({reason}: {detail}) — every later read this "
             "run goes to Linear")


def _linear_key_held() -> bool:
    """Has THIS process already been refused by Linear's rate limit?

    Late by design: at the first door read of a pass nothing has called Linear
    yet, so a 429 there falls back. A door that knows its key is held says
    `linear-hold` itself, which is the signal that is never late.
    """
    lops = sys.modules.get("linear_ops")
    budget = getattr(lops, "_budget", None)
    return isinstance(budget, dict) and budget.get("refused_after") is not None


# ── The OIDC token ──────────────────────────────────────────────────────────


def _b64url_json(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def token_claims(token: str) -> dict:
    """The JWT's payload, UNVERIFIED. It came from the runner; the door is
    what verifies it. Read here only for `exp` and `job_workflow_ref`."""
    try:
        return _b64url_json(token.split(".")[1])
    except Exception:  # noqa: BLE001 — an unreadable token has no claims
        return {}


def normalize_ref(ref: str) -> str:
    """`refs/tags/stable` → `stable`, `refs/heads/main` → `main`, a sha as is."""
    ref = (ref or "").strip()
    for prefix in ("refs/tags/", "refs/heads/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def called_ref(claims: dict) -> str | None:
    """The ref in `job_workflow_ref` (`owner/repo/.github/workflows/x.yml@ref`)."""
    jwr = claims.get("job_workflow_ref") or ""
    if "@" not in jwr:
        return None
    return normalize_ref(jwr.rsplit("@", 1)[1])


def pipeline_ref_problem(claims: dict, pipeline_ref: str | None) -> str | None:
    """Why the token's called ref and the scripts' ref disagree, or None.

    The door's identity is the reusable workflow's path and ref; the scripts
    that run come from `inputs.pipeline_ref`. Unequal, a run would present
    `@stable`'s identity while executing another ref's code (review M1)."""
    if not pipeline_ref:
        return None
    called = called_ref(claims)
    if called is None:
        return "the OIDC token carries no job_workflow_ref"
    if called != normalize_ref(pipeline_ref):
        return (f"the reusable was called at {called!r} but its scripts were "
                f"checked out at {normalize_ref(pipeline_ref)!r}")
    return None


def _mint() -> str:
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise ReadUnknown("no-token", "no ACTIONS_ID_TOKEN_REQUEST_URL — the calling "
                          "stub does not grant id-token: write", unavailable=True)
    audience = os.environ.get(AUDIENCE_ENV) or DEFAULT_AUDIENCE
    joined = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{joined}audience={urllib.parse.quote(audience, safe='')}",
        headers={"Authorization": f"Bearer {request_token}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TOTAL_TIMEOUT) as resp:  # nosec B310
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()[:200]
        raise ReadUnknown("no-token", f"the runner's OIDC endpoint answered "
                          f"{e.code}: {body or 'no body'}", unavailable=True) from e
    except Exception as e:  # noqa: BLE001 — any failure to mint is a fallback
        raise ReadUnknown("no-token", f"minting failed: {e}", unavailable=True) from e
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise ReadUnknown("no-token", "the OIDC endpoint returned no token value",
                          unavailable=True)
    return value


def _token() -> str:
    """A FRESH token for one request. The door claims every `jti` once (its
    replay store, S3), so a token sent twice is refused `replayed` — even after
    a 400, 403 or 429 on the first use. GitHub's token endpoint is local to
    the job and cheap; minting per request is the only way that cannot
    collide with the door's rule."""
    token = _mint()
    problem = pipeline_ref_problem(token_claims(token), os.environ.get(PIPELINE_REF_ENV))
    if problem:
        raise ReadUnknown("pipeline-ref-mismatch", problem, unavailable=True)
    return token


# ── The request ─────────────────────────────────────────────────────────────


def _base() -> urllib.parse.SplitResult:
    raw = (os.environ.get(URL_ENV) or "").strip()
    if not raw:
        raise ReadUnknown("unavailable", f"{URL_ENV} is not set", unavailable=True)
    parts = urllib.parse.urlsplit(raw)
    loopback = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        # The token is a bearer credential: never sent in clear text, except
        # to a door on this machine (the cross-repo harness).
        raise ReadUnknown("unavailable", f"{URL_ENV} must be https (got "
                          f"{parts.scheme}://{parts.hostname})", unavailable=True)
    return parts


def _http_get(path: str, query: dict, headers: dict) -> tuple[int, bytes]:
    """One GET, one attempt: connect within CONNECT_TIMEOUT, everything within
    TOTAL_TIMEOUT. `urlopen`'s single socket timeout cannot say both."""
    base = _base()
    target = (base.path.rstrip("/") + PATH_PREFIX + path
              + ("?" + urllib.parse.urlencode(query) if query else ""))
    if base.scheme == "https":
        conn = http.client.HTTPSConnection(
            base.hostname, base.port or 443, timeout=CONNECT_TIMEOUT,
            context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(base.hostname, base.port or 80,
                                          timeout=CONNECT_TIMEOUT)
    outcome: dict = {}

    def exchange() -> None:
        try:
            conn.connect()  # bounded by CONNECT_TIMEOUT
            conn.sock.settimeout(TOTAL_TIMEOUT)
            conn.request("GET", target, headers=headers)
            resp = conn.getresponse()
            outcome["answer"] = (resp.status, resp.read())
        except BaseException as e:  # noqa: BLE001 — handed to the caller below
            outcome["error"] = e

    # The TOTAL bound is the join, not a socket timeout: a socket timeout
    # bounds each recv, and a door dripping a byte a second would never trip
    # it. A thread still waiting past the deadline is abandoned (daemon) and
    # ends on its own socket timeout; the run has already moved to Linear.
    worker = threading.Thread(target=exchange, daemon=True, name="bureau-read")
    worker.start()
    worker.join(TOTAL_TIMEOUT)
    if worker.is_alive():
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best effort
            pass
        raise TimeoutError(f"no complete answer within {TOTAL_TIMEOUT:g}s")
    conn.close()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["answer"]


def _require_max_age(name: str, value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"bureau_read: {name} must be a positive number of seconds "
                         f"(a per-reader constant, item 39) — got {value!r}")
    return value


def _get(endpoint: str, query: dict, *, max_age: int,
         relations_max_age: int | None = None, not_found_ok: bool = False) -> dict:
    """The envelope for one read, or ReadUnknown. Counts and times everything."""
    _require_max_age("max_age", max_age)
    if relations_max_age is not None:
        _require_max_age("relations_max_age", relations_max_age)
    if mode() == "off":
        raise ReadUnknown("off", f"{MODE_ENV} is off", unavailable=True)
    if _state["disabled"] is not None:
        raise ReadUnknown("unavailable", f"the door stopped answering earlier this "
                          f"run ({_state['disabled']})", unavailable=True)
    event = os.environ.get(EVENT_ENV, "")
    if event in REFUSED_EVENTS:
        _state["unavailable"] += 1
        _disable("event-refused", f"a {event} run's token is refused by the door")
        raise ReadUnknown("event-refused", f"a {event} run's token is refused by the door",
                          unavailable=True)
    started = time.monotonic()
    try:
        try:
            _base()  # refused BEFORE a token is minted for it
            token = _token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "X-Bureau-Max-Age": str(max_age),
            }
            if relations_max_age is not None:
                headers["X-Bureau-Relations-Max-Age"] = str(relations_max_age)
            status, body = _http_get(endpoint, query, headers)
        except ReadUnknown as e:
            _state["unavailable"] += 1
            _disable(e.reason, e.detail)
            raise
        except Exception as e:  # noqa: BLE001 — network, timeout, TLS: all fallback
            reason = "timeout" if isinstance(e, TimeoutError) else "unavailable"
            _state["unavailable"] += 1
            _disable(reason, f"{type(e).__name__}: {e}")
            raise ReadUnknown(reason, f"{type(e).__name__}: {e}",
                              unavailable=True) from e
        if status == 404 and not_found_ok:
            _state["unknown"] += 1
            raise ReadUnknown("not-found", f"{endpoint}: 404", status=404)
        if status != 200:
            reason = {401: "refused", 403: "refused", 429: "throttled",
                      503: "closed"}.get(status, "unavailable")
            text = body.decode("utf-8", "replace")[:200]
            held = _linear_key_held() or LINEAR_HOLD in text
            _state["unavailable"] += 1
            _disable(reason, f"HTTP {status} {text}")
            raise ReadUnknown(reason, f"HTTP {status} {text}", status=status,
                              unavailable=True, linear_held=held)
        try:
            envelope = json.loads(body.decode("utf-8"))
        except ValueError as e:
            _state["unavailable"] += 1
            _disable("malformed", "the answer is not JSON")
            raise ReadUnknown("malformed", "the answer is not JSON",
                              unavailable=True) from e
        return envelope
    finally:
        _state["door_ms"] += (time.monotonic() - started) * 1000.0


def _malformed(detail: str) -> ReadUnknown:
    """A door answering outside its contract is a door bug: UNKNOWN, and not
    asked again this run."""
    _state["unavailable"] += 1
    _disable("malformed", detail)
    return ReadUnknown("malformed", detail, unavailable=True)


def _missing_paths(value, shape, path: str = "") -> list[str]:
    """Every field path `shape` names that `value` does not carry."""
    if shape is None:
        return []
    if not isinstance(value, dict):
        return [path or "<node>"]
    out = []
    for key, sub in shape.items():
        here = f"{path}.{key}" if path else key
        if key not in value:
            out.append(here)
            continue
        child = value[key]
        if child is None:
            if key not in _NULLABLE:
                out.append(f"{here} (null)")
            continue
        if key == "nodes" and isinstance(sub, dict):
            if not isinstance(child, list):
                out.append(f"{here} (not a list)")
                continue
            for i, item in enumerate(child):
                out.extend(_missing_paths(item, sub, f"{here}[{i}]"))
        elif isinstance(sub, dict):
            out.extend(_missing_paths(child, sub, here))
    return out


def _accept(envelope: dict, *, max_age: int, shape: dict,
            relations: bool, nodes_key: str = "issues") -> DoorRead:
    """Validate a 200 envelope whole, or raise. Never returns part of one."""
    if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA:
        raise _malformed(f"schema is not {SCHEMA!r}")
    verdict = envelope.get("verdict")
    fresh = envelope.get("freshness") or {}
    if verdict == "UNKNOWN":
        reason = (fresh.get("reason") or "unknown") if isinstance(fresh, dict) else "unknown"
        _state["unknown"] += 1
        raise ReadUnknown(str(reason), "the door answered UNKNOWN")
    if verdict != "FRESH":
        raise _malformed(f"verdict {verdict!r}")
    if not isinstance(fresh, dict) or not fresh.get("as_of"):
        raise _malformed("FRESH without freshness.as_of")
    age = fresh.get("age_seconds")
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        raise _malformed("FRESH without a numeric freshness.age_seconds")
    if age > max_age:
        # The door should have said UNKNOWN; the client holds the line anyway.
        _state["unknown"] += 1
        raise ReadUnknown("stale", f"age {age}s is past this reader's {max_age}s")
    if relations and not fresh.get("relations_as_of"):
        raise _malformed("relations asked for and no freshness.relations_as_of")
    caller = envelope.get("caller") or {}
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository and isinstance(caller, dict) and caller.get("repository") and (
            str(caller["repository"]).lower() != repository.lower()):
        raise _malformed(f"answered for {caller.get('repository')!r}, not {repository!r}")
    container = envelope.get(nodes_key)
    if not isinstance(container, dict) or not isinstance(container.get("nodes"), list):
        raise _malformed(f"FRESH without {nodes_key}.nodes")
    nodes = container["nodes"]
    for node in nodes:
        missing = _missing_paths(node, shape)
        if missing:
            ident = node.get("identifier") if isinstance(node, dict) else None
            raise _malformed(f"{ident or 'a node'} lacks {', '.join(missing[:5])}")
    return DoorRead(
        nodes=nodes,
        as_of=fresh.get("as_of"),
        age_seconds=float(age),
        relations_as_of=fresh.get("relations_as_of"),
        viewer_id=((envelope.get("viewer") or {}).get("id")
                   if isinstance(envelope.get("viewer"), dict) else None),
        raw=envelope,
    )


def _card_shape(relations: bool) -> dict:
    return {**CARD_FIELDS, **RELATION_FIELDS} if relations else dict(CARD_FIELDS)


# ── The four reads ──────────────────────────────────────────────────────────


def board(lanes, *, scope: str = "repo", max_age: int, comments: int = 50,
          relations: bool = False, relations_max_age: int | None = None) -> DoorRead:
    """Every card in `lanes`, for this repo's tenant (`scope=repo`) or the
    fleet (`scope=fleet`, steward repos only). Whole, or ReadUnknown."""
    wanted = [str(lane) for lane in lanes]
    if not wanted:
        raise ValueError("bureau_read.board: no lanes asked for")
    if scope not in ("repo", "fleet"):
        raise ValueError(f"bureau_read.board: scope {scope!r}")
    query = {"lanes": ",".join(wanted), "scope": scope, "comments": str(comments),
             "relations": "1" if relations else "0"}
    if relations:
        relations_max_age = relations_max_age or RELATIONS_MAX_AGE
    envelope = _get("/board", query, max_age=max_age,
                    relations_max_age=relations_max_age if relations else None)
    read = _accept(envelope, max_age=max_age, shape=_card_shape(relations),
                   relations=relations)
    echoed = envelope.get("lanes")
    if not isinstance(echoed, list) or set(echoed) != set(wanted):
        raise _malformed(f"FRESH /board affirmed lanes {echoed!r}, asked {wanted!r} — "
                         "an unaffirmed lane is never read as an empty one")
    for node in read.nodes:
        lane = (node.get("state") or {}).get("name")
        if lane not in wanted:
            raise _malformed(f"{node.get('identifier')} is in {lane!r}, not a lane asked for")
    _state["served"] += 1
    return read


def cards(ids, *, max_age: int, comments: int | str = 50, relations: bool = True,
          relations_max_age: int | None = None) -> DoorRead:
    """Exactly the cards named — every one of them, or ReadUnknown.

    `comments="all"` asks for every stored comment, and then the WHOLE thread
    is the answer: a node whose `comments.pageInfo.hasNextPage` is true (the
    door cannot prove it holds the whole thread) is half an answer, so the
    read is UNKNOWN `thread-incomplete`. The door stays in use: one card's
    thread is not the door failing.
    """
    if comments != "all" and (not isinstance(comments, int) or isinstance(comments, bool)):
        raise ValueError(f"bureau_read.cards: comments must be a count or 'all', got {comments!r}")
    wanted = [str(i).strip().upper() for i in ids if str(i).strip()]
    if not wanted:
        return DoorRead(nodes=[])
    query = {"ids": ",".join(wanted), "comments": str(comments),
             "relations": "1" if relations else "0"}
    if relations:
        relations_max_age = relations_max_age or RELATIONS_MAX_AGE
    envelope = _get("/cards", query, max_age=max_age,
                    relations_max_age=relations_max_age if relations else None,
                    not_found_ok=True)
    read = _accept(envelope, max_age=max_age, shape=_card_shape(relations),
                   relations=relations)
    got = [str(n.get("identifier") or "").upper() for n in read.nodes]
    if sorted(got) != sorted(set(wanted)):
        raise _malformed(f"/cards answered {sorted(got)} for {sorted(set(wanted))}")
    if comments == "all":
        partial = [n.get("identifier") for n in read.nodes
                   if ((n.get("comments") or {}).get("pageInfo") or {}).get("hasNextPage")
                   is not False]
        if partial:
            _state["unknown"] += 1
            raise ReadUnknown("thread-incomplete",
                              f"the door cannot prove it holds every comment on "
                              f"{', '.join(sorted(map(str, partial)))}")
    _state["served"] += 1
    return read


def dependents(identifier: str, *, max_age: int, lanes=None, comments: int = 50,
               relations_max_age: int | None = None) -> DoorRead:
    """The cards `identifier` blocks — every card whose relations name it as a
    `blocks` — in `lanes` (every lane the door holds when None), each a board
    node with its relations. Whole, or ReadUnknown: a candidate whose relations
    the door cannot read makes the list unprovable, and the door says so."""
    ident = str(identifier).strip().upper()
    query = {"comments": str(comments), "relations": "1"}
    wanted = [str(lane) for lane in lanes] if lanes is not None else None
    if wanted:
        query = {"lanes": ",".join(wanted), **query}
    envelope = _get(f"/cards/{urllib.parse.quote(ident, safe='')}/dependents", query,
                    max_age=max_age,
                    relations_max_age=relations_max_age or RELATIONS_MAX_AGE,
                    not_found_ok=True)
    read = _accept(envelope, max_age=max_age, shape=_card_shape(True), relations=True)
    for node in read.nodes:
        lane = (node.get("state") or {}).get("name")
        if wanted and lane not in wanted:
            raise _malformed(f"/dependents of {ident} answered {node.get('identifier')} "
                             f"in {lane!r}, not a lane asked for")
        if not any(rel.get("type") == "blocks"
                   and (rel.get("issue") or {}).get("identifier") == ident
                   for rel in (node.get("inverseRelations") or {}).get("nodes") or []):
            raise _malformed(f"/dependents of {ident} answered {node.get('identifier')}, "
                             "whose relations do not name it")
    _state["served"] += 1
    return read


def workflow_states(*, max_age: int) -> DoorRead:
    """The team's workflow states (`id name type`), for BP-3's state cache."""
    envelope = _get("/workflow-states", {}, max_age=max_age)
    read = _accept(envelope, max_age=max_age, shape=WORKFLOW_STATE_FIELDS,
                   relations=False, nodes_key="workflowStates")
    if not read.nodes:
        raise _malformed("/workflow-states answered no states")
    _state["served"] += 1
    return read


# ── The planner line's order (DRE-5807) ─────────────────────────────────────
#: `GET /planning-order` → `{"order": ["DRE-…", …], "set_at", "set_by",
#: "read_at"}` — the order the CEO set by dragging the Overview's In line
#: cards (agent-bureau DRE-5782). An empty order means arrival order.
PLANNING_ORDER_PATH = "/planning-order"


@dataclass
class PlanningOrder:
    """The order the CEO set for the planner line: identifiers, first first."""

    order: list
    set_at: str | None = None
    set_by: str | None = None
    read_at: str | None = None


def _text_or_none(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def planning_order() -> PlanningOrder:
    """The planner line's order, or ReadUnknown. Never part of one.

    Not one of the board reads above, and it does not follow two of their
    rules, on purpose:

    * It is asked whatever `BUREAU_READ` says. The mode is the board reads'
      rollout switch — off, then shadow against Linear, then on — and the
      order has no Linear copy to shadow or fall back to. The door's address
      (`BUREAU_READ_URL`) and an OIDC token are what it needs; without either
      it is ReadUnknown, and the planner line runs in arrival order.
    * A failure here never stops the door for the run's other reads. The
      endpoint is new, so a door that predates it answers 404, and the board
      reads must not pay for that. It is asked once per run, so one slow
      answer costs one wait. It still honors the rules that protect the door:
      a door that already stopped answering this run is not asked again, and
      a `pull_request` run never asks (the door refuses those tokens, S7).

    No freshness header: the order is the console's own record, not a copy of
    Linear's. Nothing here touches the run's counters or prints anything: the
    caller (`planner_queue.planning_order`) says the one line.
    """
    if _state["disabled"] is not None:
        raise ReadUnknown("unavailable", f"the door stopped answering earlier this "
                          f"run ({_state['disabled']})", unavailable=True)
    event = os.environ.get(EVENT_ENV, "")
    if event in REFUSED_EVENTS:
        raise ReadUnknown("event-refused", f"a {event} run's token is refused by the door",
                          unavailable=True)
    try:
        _base()  # refused BEFORE a token is minted for it
        token = _token()
        status, body = _http_get(PLANNING_ORDER_PATH, {}, {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
    except ReadUnknown:
        raise
    except Exception as e:  # noqa: BLE001 — network, timeout, TLS: all ReadUnknown
        reason = "timeout" if isinstance(e, TimeoutError) else "unavailable"
        raise ReadUnknown(reason, f"{type(e).__name__}: {e}", unavailable=True) from e
    if status != 200:
        reason = {401: "refused", 403: "refused", 404: "not-found", 429: "throttled",
                  503: "closed"}.get(status, "unavailable")
        text = body.decode("utf-8", "replace")[:200]
        raise ReadUnknown(reason, f"HTTP {status} {text}", status=status, unavailable=True)
    try:
        answer = json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise ReadUnknown("malformed", "the answer is not JSON", unavailable=True) from e
    order = answer.get("order") if isinstance(answer, dict) else None
    if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
        raise ReadUnknown("malformed", "the answer carries no `order` list of identifiers",
                          unavailable=True)
    wanted = [i.strip().upper() for i in order if i.strip()]
    return PlanningOrder(
        order=list(dict.fromkeys(wanted)),
        set_at=_text_or_none(answer.get("set_at")),
        set_by=_text_or_none(answer.get("set_by")),
        read_at=_text_or_none(answer.get("read_at")),
    )


# ── The shadow comparison (item 36, review M6) ──────────────────────────────
#: The two classes that decide the shadow bar. `door-older` is EXPLAINED: the
#: door's snapshot is older than the Linear read taken after it. Every other
#: class is a FAILURE of the shadow day, `same-stamp-different-value` above all.
DOOR_OLDER = "door-older"
SAME_STAMP = "same-stamp-different-value"
DOOR_NEWER = "door-newer"
EXPLAINED = frozenset({DOOR_OLDER})


def _stamp(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _effective_stamp(node: dict):
    """The later of the node's `updatedAt` and the newest comment in its own
    window — the stamp both sides are classified by (DRE-5730).

    The door serves `updatedAt` as exactly this (AB-1's contract). Linear's
    own `updatedAt` moves for a comment, but not for one that lands within
    about a minute of its last bump (2026-10-03: 62 of 100 active cards had a
    newest comment up to 49 s after `updatedAt`). Compared raw, the door read
    FIRST would look newer than Linear read after it — `door-newer`, a
    failure — on a card where nothing differs but the arithmetic. Applied to
    both sides, the same activity is the same stamp. None when the node has
    no `updatedAt` (unstamped stays unstamped)."""
    stamp = _stamp(node.get("updatedAt"))
    if stamp is None:
        return None
    for comment in ((node.get("comments") or {}).get("nodes") or []):
        at = _stamp((comment or {}).get("createdAt"))
        if at is not None and at > stamp:
            stamp = at
    return stamp


def _normal(node: dict) -> dict:
    """The facts a decision reads, normalized: labels as a set, comments in
    order by (createdAt, body, author), relations as a set.

    ONLY the fields the node carries (DRE-5730). A key absent from a node is
    a field its query never selected — Reconcile's board read has no `parent`,
    its Backlog read no `priority` — and comparing it would report the query,
    not the data. A field that was selected and came back null is present,
    and IS compared. The door's own nodes are always whole: the client refuses
    a door answer missing any field (`missing-field`) before it gets here."""
    out = {}
    if "state" in node:
        out["lane"] = (node.get("state") or {}).get("name")
    if "labels" in node:
        out["labels"] = sorted({(lbl or {}).get("name") or "" for lbl in
                                ((node.get("labels") or {}).get("nodes") or [])})
    if "title" in node:
        out["title"] = node.get("title")
    if "description" in node:
        # The door serves "" where Linear may send null: one fact, not two.
        out["description"] = node.get("description") or ""
    if "priority" in node:
        # 0 is Linear's "No priority"; null says the same: one fact, not two.
        out["priority"] = node.get("priority") or 0
    if "parent" in node:
        parent = node.get("parent")
        out["parent"] = ((parent.get("identifier"), (parent.get("state") or {}).get("name"))
                         if parent else None)
    if "children" in node:
        out["children"] = bool((node.get("children") or {}).get("nodes"))
    if "comments" in node:
        out["comments"] = [
            (c.get("createdAt"), c.get("body"), (c.get("user") or {}).get("id"))
            for c in ((node.get("comments") or {}).get("nodes") or [])
        ]
    rels = node.get("inverseRelations")
    if rels is not None:
        out["relations"] = sorted(
            (r.get("type"), (r.get("issue") or {}).get("identifier"),
             ((r.get("issue") or {}).get("state") or {}).get("name"))
            for r in (rels.get("nodes") or []))
        out["relations_partial"] = bool((rels.get("pageInfo") or {}).get("hasNextPage"))
    return out


@dataclass
class Diff:
    klass: str
    identifier: str
    field: str
    door: object
    linear: object

    @property
    def explained(self) -> bool:
        return self.klass in EXPLAINED


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= 80 else f"{text[:77]}..."


def compare(door_nodes, linear_nodes, *, door_as_of: str | None,
            fields: tuple | None = None) -> list[Diff]:
    """Classify every difference between the door's answer and Linear's.

    The door is read FIRST, so it is never the newer of the two. Per card,
    by each side's `_effective_stamp` (`updatedAt` or a later comment):
    a difference with the door's stamp earlier than Linear's is
    `door-older`; with the same stamp it is `same-stamp-different-value`; a
    door stamp later than Linear's is `door-newer` (a failure: impossible when
    the door was read first). Only fields BOTH nodes carry are compared
    (`_normal`). A card only Linear holds is `door-older` when it
    changed after the door's `as_of`, else a same-stamp failure (the door
    should have held it). A card only the door holds left the lanes after the
    door's snapshot, which the later Linear read proves — `door-older`.
    Relation differences are `door-older`: relations ride the console's poll,
    and the shadow cannot prove relation freshness (review R10) — the live
    re-check before every promotion is what guards them.
    Comments that Linear holds and the door does not, all newer than the door's
    `as_of`, are `door-older`.
    """
    door = {str(n.get("identifier")): n for n in door_nodes}
    linear = {str(n.get("identifier")): n for n in linear_nodes}
    as_of = _stamp(door_as_of)
    out: list[Diff] = []
    for ident in sorted(set(door) | set(linear)):
        d, l_ = door.get(ident), linear.get(ident)
        if d is None:
            changed = _stamp(l_.get("updatedAt"))
            klass = DOOR_OLDER if (changed and as_of and changed > as_of) else SAME_STAMP
            out.append(Diff(klass, ident, "presence", None, (l_.get("state") or {}).get("name")))
            continue
        if l_ is None:
            out.append(Diff(DOOR_OLDER, ident, "presence",
                            (d.get("state") or {}).get("name"), None))
            continue
        nd, nl = _normal(d), _normal(l_)
        ds, ls = _effective_stamp(d), _effective_stamp(l_)
        for key in sorted(set(nd) | set(nl)):
            if fields is not None and key not in fields:
                continue
            if key not in nd or key not in nl or nd[key] == nl[key]:
                continue
            if key in ("relations", "relations_partial"):
                klass = DOOR_OLDER
            elif key == "comments" and _comments_explained(nd[key], nl[key], as_of):
                klass = DOOR_OLDER
            elif ds is None or ls is None:
                klass = SAME_STAMP
            elif ds < ls:
                klass = DOOR_OLDER
            elif ds == ls:
                klass = SAME_STAMP
            else:
                klass = DOOR_NEWER
            out.append(Diff(klass, ident, key, nd[key], nl[key]))
    return out


def _comments_explained(door_c: list, linear_c: list, as_of) -> bool:
    """Linear's window holds comments the door's does not, and every one of
    them was posted after the door's `as_of`."""
    if as_of is None:
        return False
    extra = [c for c in linear_c if c not in door_c]
    missing = [c for c in door_c if c not in linear_c]
    if not extra:
        return False
    newer = all((_stamp(c[0]) or as_of) > as_of for c in extra)
    # A window is newest-fifty: the comments the door has and Linear does not
    # are the oldest ones pushed out of Linear's window by the new ones.
    return newer and len(missing) <= len(extra)


def report_diffs(read: str, diffs: list[Diff], compared: int) -> int:
    """Print the `read-door-diff:` lines; return the unexplained count."""
    for diff in diffs:
        print(f"read-door-diff: {diff.klass} {read} {diff.identifier} {diff.field}: "
              f"door={_short(diff.door)} linear={_short(diff.linear)}")
    unexplained = sum(1 for d in diffs if not d.explained)
    print(f"read-door-diff: {read} compared {compared} card(s): "
          f"{len(diffs) - unexplained} explained ({DOOR_OLDER}), "
          f"{unexplained} unexplained")
    return unexplained


# ── CLI: the reusable's pipeline_ref refusal (item 38) ──────────────────────


def check_ref(pipeline_ref: str) -> int:
    """Exit 1 when the OIDC token's called ref is not `pipeline_ref`.

    Only when the door could be used: in mode `off`, or with no token to read,
    there is no identity to protect and the check says so and passes — a
    fleet-wide red on a ref-normalization slip is the one outcome worse than
    no check.
    """
    if mode() == "off":
        print(f"read-door: pipeline_ref check skipped — {MODE_ENV} is off")
        return 0
    try:
        token = _mint()
    except ReadUnknown as e:
        print(f"read-door: pipeline_ref check skipped — {e}; the door is not used "
              "without a token")
        return 0
    problem = pipeline_ref_problem(token_claims(token), pipeline_ref)
    if problem:
        print(f"::error::read-door: refusing to run — {problem}. Pass the ref the "
              "stub pins in `uses:` as `pipeline_ref` (DRE-2689).")
        return 1
    print(f"read-door: pipeline_ref {normalize_ref(pipeline_ref)!r} matches the called ref")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "check-ref":
        return check_ref(argv[2])
    print("usage: bureau_read.py check-ref <pipeline_ref>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
