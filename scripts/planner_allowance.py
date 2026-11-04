#!/usr/bin/env python3
"""A planner reads the planners' Linear allowance before it starts (DRE-8616, epic DRE-8539).

The CEO's decision of 2026-10-31: no fixed number of planners. Before a planner
starts it reads how much of the planners' Linear allowance is left. Above
`threshold_remaining` it starts at once, however many others are running.
Below it, it waits a few minutes and reads again, then starts. A 429 or
`RATELIMITED` from Linear counts as below. `architecture/decisions/
adr-planner-cap-removed.md` says why the cap went and what was kept.

THE READ. `read()` sends ONE cheap request (`{ viewer { id } }`) through
`linear_ops.gql`, with the key the calling job holds — the planner's own OAuth
bucket, `LINEAR_PLANNER_KEY || LINEAR_API_KEY` (DRE-5589) — and reads
`remaining`, `limit` and the reset off Linear's `x-ratelimit-requests-*`
headers through `linear_ops.rate_reading()`. A `LinearRateLimited` answer is a
low reading, `refused`, with `remaining` from that answer's header when Linear
sent one and `unknown` otherwise. Any other failure — network, 5xx, no key, an
answer that does not parse — is NOT a low allowance: the planner starts at
once `because read-failed`, and the step says so with a `::warning::`. A
planner never fails or strands because of the read.

THE WAIT. `wait()` is the decision loop. The first read at or above the
threshold starts the planner and posts nothing: the fast path costs one
request. Below it, the hold begins at that read, and the run re-reads at the
next wall-clock boundary that is a multiple of `wait_minutes`, so every held
planner in the fleet reads at the same moment. At a good read it takes its
place in the held line, sleeps `min(place × stagger_seconds, wait_minutes ×
60)`, and posts `started … because allowance-ok`. At the last boundary read
that is not past `hold_max_minutes` from the hold's start — the read after
which the next boundary would be — it starts whatever the reading, `because
hold-max`, after the SAME stagger: that is exactly when the allowance is
still low, and held planners that reached the bound at one read would
otherwise all start in one second. Reading on the boundary at or before the
bound, rather than after it, is what keeps the whole wait, stagger included,
inside the receipt's `until` (`at` + `hold_max_minutes` + `wait_minutes`).

THE PLACE. The cards whose newest planner-hold receipt is a live `held`, this
run's card among them, ordered by the CEO's Planning order (read through
`bureau_read`, DRE-5807 — the one reader left of it, since the old planner
line's ledger was deleted with the cap, DRE-8622), then
the oldest `held` first. The place is the number of cards ahead, so the head
of the line sleeps 0. A door that cannot answer is arrival order, said in one
line. A held-line read that Linear refuses or that fails gives the place
`run_id mod 30`, said in one line, so a stagger still spreads the starts when
nothing can be read. Past the thirtieth place the planners share the last
start moment.

LINEAR'S REFUSAL STOPS THIS PROCESS ASKING, AND `wait` UNDOES THAT ON PURPOSE.
`linear_ops.gql` arms a stop at the first `RATELIMITED` (DRE-3202), and every
later `gql` in the process is refused unsent. `wait` is the one caller that has
already backed off, so after the injected `sleep` has returned for the next
boundary — never otherwise, never twice without a sleep between — it calls
`linear_ops.lift_rate_limit_stop` when the stop is armed. This module is that
function's only caller, and `tests/test_planner_allowance.py` fails on another.

THE RECEIPTS (one per line, posted with the job's key, never the fleet's):

  🪫 planner-hold: held · card <DRE-N> · run <id> · repo <owner/name> · remaining <n|unknown> of <limit|unknown> · next read at <ISO> · every <wait_minutes> min · until <ISO> · at <ISO>
  🪫 planner-hold: started · card <DRE-N> · run <id> · repo <owner/name> · remaining <n|unknown> of <limit|unknown> · because <allowance-ok|hold-max> · at <ISO>

A read Linear refused is never followed by a `held` post: the stop would
refuse it unsent, and Linear would refuse it anyway while the bucket stands at
0. The `held` is posted at the first read of the hold that Linear answers,
and always reports the read that BEGAN the hold, with that read's time as its
`at`. A refused post is never retried at once: the `held` is tried again at
the next answered low read, and a refused `started` is left unposted. The
`started` is tried once at every start but the fast path — after a refused
hold-max read the stop refuses it unsent, which is said as `receipt refused`.
A read that fails posts nothing. While no `held` stands the hold is invisible
to the sweep and the console; that lasts at most `hold_max_minutes` plus
`wait_minutes`, which `check` keeps below the Planning stall window.

THE LOG LINE. Every read prints one line on stdout:

  planner-allowance: <start|hold> · card <DRE-N> · remaining <n|unknown> of <limit|unknown> · threshold <t> · read <answered|refused> · receipt <held|started|deferred|refused|none> · requests <n> · at <ISO>

`read refused` is a `RATELIMITED` answer; a read that failed any other way
says `read failed`, beside its `::warning::`. `receipt deferred` means no post
was tried because the read was refused, `receipt refused` that Linear refused
the post (or a non-quota failure did, with a `::warning::`). `requests` is
`linear_ops.requests_made()` after the read and its receipt. Declared in
`config/proof-reach.json`, so a proof run may quote it.

THE TWO SEAMS THAT FAKE A READING. `PLANNER_ALLOWANCE_FAKE`, a JSON list of
`{"remaining": n, "limit": n}` consumed one per read, replaces the request
entirely — for the plan workflow's wiring test, where no Linear key is held.
`fixture_low_reading` in the config is the live proof's: a run in exactly that
`repo`, for a card whose title contains `title_marker`, sends its FIRST read
through `gql` like any other, keeps the real limit, and only then takes the
fixture's `remaining`. Every later read is real. Nowhere else does a title
change anything.

THE LINEAR BOUNDARY — the five vendor-boundary questions:

  Q1 (whose identity, whose quota). Every request goes through `linear_ops`
     with the key the plan job holds: the planners' own OAuth bucket. The fast
     path is one request; a hold is one read per boundary, two per receipt
     (`cmd_comment` reads the card, then creates), and one held-line read.
  Q2 (which secrets store). None of its own. No key is a failed read, which
     starts the planner `because read-failed`.
  Q3 (retry). `gql` retries a transient fault once, and a doubled receipt is
     harmless: `held()` reads only the newest. A rate-limited post is never
     moved onto the fleet key (`linear_ops.is_auth_refusal`).
  Q4 (limits). The held line is one `comments` read filtered to the receipt's
     tag inside the live window, paged a hundred at a time.
  Q5 (a crash). A crash leaves at most a `held` that expires at its `until`;
     the planner the job starts next is a new process, so the stop armed here
     never reaches it.

CLI (every command but `check` exits 0 on every path):
  check                     exit 0 on a valid config file; 1 naming the defect
  wait <CARD> --run-id ID --repo O/N --title TITLE [--github-output PATH]
                            start=true because=<allowance-ok|hold-max|read-failed>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TAG = "planner-allowance:"
RECEIPT_MARK = "🪫 planner-hold:"
STATES = ("held", "started")
BECAUSE = ("allowance-ok", "hold-max", "read-failed")

READ_QUERY = "query { viewer { id } }"
#: The held line: every planner-hold receipt posted inside the live window.
HELD_LINE_QUERY = """query($since: DateTimeOrDuration!, $after: String) {
  comments(first: 100, after: $after, filter: {
    body: {contains: "planner-hold:"}, createdAt: {gt: $since}
  }) { nodes { id body createdAt issue { identifier } }
       pageInfo { hasNextPage endCursor } } }"""

CONFIG_ENV = "PLANNER_ALLOWANCE_CONFIG"
FAKE_ENV = "PLANNER_ALLOWANCE_FAKE"
_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.normpath(os.path.join(_HERE, "..", "config", "planner-allowance.json"))
CONFIG_KEYS = ("threshold_remaining", "wait_minutes", "hold_max_minutes", "stagger_seconds",
               "fixture_low_reading")
FIXTURE_KEYS = ("repo", "title_marker", "remaining")
#: The fallback place is `run_id mod PLACES` when the held line cannot be read.
PLACES = 30


class PlannerAllowanceError(RuntimeError):
    """A bad config file, named with its defect."""


class ReadFailed(RuntimeError):
    """The allowance read failed for a reason that is not Linear's refusal."""


# --------------------------------------------------------------------------- #
# the config                                                                   #
# --------------------------------------------------------------------------- #


def config_path(path: str | None = None) -> str:
    return path or os.environ.get(CONFIG_ENV) or CONFIG_PATH


def _int(where: str, key: str, value, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlannerAllowanceError(f"{where}: {key} must be an integer, got {value!r}")
    if positive and value <= 0:
        raise PlannerAllowanceError(f"{where}: {key} must be greater than 0, got {value}")
    if value < 0:
        raise PlannerAllowanceError(f"{where}: {key} must not be negative, got {value}")
    return value


def _stall_window() -> int:
    import lane_contract  # noqa: PLC0415 — beside this script, read only by `load`
    return lane_contract.stale_minutes()["Planning"]


def load(path: str | None = None) -> dict:
    """The config, validated. Raises `PlannerAllowanceError` naming the path
    and the key — never a default."""
    where = config_path(path)
    try:
        with open(where, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise PlannerAllowanceError(
            f"{where}: cannot read the planner allowance config ({exc.strerror or exc})"
        ) from exc
    except ValueError as exc:
        raise PlannerAllowanceError(f"{where}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise PlannerAllowanceError(f"{where}: expected a JSON object")
    for key in CONFIG_KEYS:
        if key not in doc:
            raise PlannerAllowanceError(f"{where}: missing key {key!r}")
    extra = sorted(set(doc) - set(CONFIG_KEYS))
    if extra:
        raise PlannerAllowanceError(f"{where}: unknown key {extra[0]!r}")
    out = {
        "threshold_remaining": _int(where, "threshold_remaining", doc["threshold_remaining"]),
        "wait_minutes": _int(where, "wait_minutes", doc["wait_minutes"], positive=True),
        "hold_max_minutes": _int(where, "hold_max_minutes", doc["hold_max_minutes"]),
        "stagger_seconds": _int(where, "stagger_seconds", doc["stagger_seconds"]),
    }
    fixture = doc["fixture_low_reading"]
    if not isinstance(fixture, dict):
        raise PlannerAllowanceError(f"{where}: fixture_low_reading must be an object")
    for key in FIXTURE_KEYS:
        if key not in fixture:
            raise PlannerAllowanceError(f"{where}: fixture_low_reading is missing key {key!r}")
    extra = sorted(set(fixture) - set(FIXTURE_KEYS))
    if extra:
        raise PlannerAllowanceError(f"{where}: fixture_low_reading has unknown key {extra[0]!r}")
    for key in ("repo", "title_marker"):
        if not isinstance(fixture[key], str) or not fixture[key].strip():
            raise PlannerAllowanceError(
                f"{where}: fixture_low_reading.{key} must be a non-empty string")
    out["fixture_low_reading"] = {
        "repo": fixture["repo"], "title_marker": fixture["title_marker"],
        "remaining": _int(where, "fixture_low_reading.remaining", fixture["remaining"]),
    }
    # A held card posts nothing between its `held` and its `started` (and
    # may post no `held` at all), and its stagger can add up to `wait_minutes`
    # after the bound: the whole of it must sit inside the Planning stall
    # window, or the watchdog reads a waiting planner as a stalled card.
    stall = _stall_window()
    bound = out["hold_max_minutes"] + out["wait_minutes"]
    if bound >= stall:
        raise PlannerAllowanceError(
            f"{where}: hold_max_minutes + wait_minutes = {bound} must be below the "
            f"Planning stall window ({stall} minutes, config/lane-contract.json)")
    return out


# --------------------------------------------------------------------------- #
# the grammar                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Receipt:
    state: str
    card: str
    run: str
    repo: str
    remaining: str
    limit: str
    at: str
    next_read: str | None = None
    every: str | None = None
    until: str | None = None
    because: str | None = None
    created_at: str = ""
    comment_id: str = ""


_HEAD = re.compile(r"^\U0001faab️?\s*planner-hold:\s*(\S+)$")
_REMAINING = re.compile(r"^(\S+) of (\S+)$")
_FIELDS = ("card", "run", "repo", "remaining", "next read at", "every", "until",
           "because", "at")
_REQUIRED = {"held": ("card", "run", "repo", "remaining", "next read at", "every",
                      "until", "at"),
             "started": ("card", "run", "repo", "remaining", "because", "at")}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(text) -> datetime | None:
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _field(value) -> str:
    """One receipt field: one line, no separator inside it."""
    text = " ".join(str(value).replace("·", " ").split())
    return text or "-"


def _number(value) -> str:
    return "unknown" if value is None else str(value)


def format_receipt(state: str, *, card: str, run: str, repo: str, remaining, limit,
                   at: datetime, next_read: datetime | None = None,
                   every: int | None = None, until: datetime | None = None,
                   because: str | None = None) -> str:
    """The one place a receipt's text is composed."""
    parts = [f"{RECEIPT_MARK} {state}", f"card {_field(card)}", f"run {_field(run)}",
             f"repo {_field(repo)}", f"remaining {_number(remaining)} of {_number(limit)}"]
    if state == "held":
        parts += [f"next read at {_iso(next_read)}", f"every {every} min",
                  f"until {_iso(until)}"]
    elif state == "started":
        parts.append(f"because {_field(because)}")
    else:
        raise PlannerAllowanceError(f"unknown planner-hold state {state!r}")
    parts.append(f"at {_iso(at)}")
    return " · ".join(parts)


def parse_receipt(body) -> Receipt | None:
    """A receipt from a comment body, or from a comment node `{id, body,
    createdAt}`. Anchored at the start: a comment that QUOTES the mark is not
    a receipt. None for anything else."""
    node = body if isinstance(body, dict) else None
    text = node.get("body") if node is not None else body
    if not isinstance(text, str):
        return None
    lines = text.strip().splitlines()
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(" · ")]
    head = _HEAD.match(parts[0])
    if not head or head.group(1) not in STATES:
        return None
    state = head.group(1)
    fields: dict = {}
    for part in parts[1:]:
        for key in _FIELDS:
            if part.startswith(key + " "):
                fields.setdefault(key, part[len(key) + 1:].strip())
                break
    if not all(fields.get(k) for k in _REQUIRED[state]):
        return None
    numbers = _REMAINING.match(fields["remaining"])
    if numbers is None or _dt(fields["at"]) is None:
        return None
    if state == "held" and _dt(fields["until"]) is None:
        return None
    every = fields.get("every")
    return Receipt(
        state=state, card=fields["card"], run=fields["run"], repo=fields["repo"],
        remaining=numbers.group(1), limit=numbers.group(2), at=fields["at"],
        next_read=fields.get("next read at"),
        every=every[:-len(" min")] if every and every.endswith(" min") else every,
        until=fields.get("until"), because=fields.get("because"),
        created_at=str((node or {}).get("createdAt") or fields["at"]),
        comment_id=str((node or {}).get("id") or ""),
    )


def _newest(bodies) -> Receipt | None:
    """The newest planner-hold receipt among `bodies` — by Linear's
    `createdAt` where a node carries one, else its own `at`; a tie goes to
    the later in the list."""
    found = []
    for i, body in enumerate(bodies or ()):
        r = parse_receipt(body)
        if r is not None:
            found.append((_dt(r.created_at), i, r))
    if not found:
        return None
    return max(found, key=lambda x: (x[0], x[1]))[2]


def held(bodies, now) -> bool:
    """THE one reader for the sweep and anything else: true while the card's
    newest planner-hold receipt is `held` and `now` is before its `until`."""
    r = _newest(bodies)
    if r is None or r.state != "held":
        return False
    return _dt(now) < _dt(r.until)


# --------------------------------------------------------------------------- #
# the read                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Reading:
    remaining: int | None
    limit: int | None
    reset_ms: int | None = None
    refused: bool = False


_fake: dict = {}


def _fake_reading() -> Reading | None:
    """The next `PLANNER_ALLOWANCE_FAKE` reading, or None when it is unset.
    Consumed one per read; the last stands once the list runs out."""
    raw = (os.environ.get(FAKE_ENV) or "").strip()
    if not raw:
        return None
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows:
            raise ValueError("expected a non-empty JSON list")
        row = rows[min(_fake.get(raw, 0), len(rows) - 1)]
        _fake[raw] = _fake.get(raw, 0) + 1
        remaining, limit = row["remaining"], row.get("limit")
        if isinstance(remaining, bool) or not isinstance(remaining, int):
            raise ValueError(f"remaining must be an integer, got {remaining!r}")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ReadFailed(f"{FAKE_ENV} is not a list of readings: {exc}") from exc
    return Reading(remaining=remaining, limit=limit if isinstance(limit, int) else None)


def read(linear_ops=None) -> Reading:
    """ONE allowance read. A refused one is a `Reading(refused=True)`; any
    other failure raises `ReadFailed`."""
    fake = _fake_reading()
    if fake is not None:
        return fake
    if linear_ops is None:
        import linear_ops  # noqa: PLC0415 — `check` must run with this file alone
    before = linear_ops.requests_made()
    refused = False
    try:
        linear_ops.gql(READ_QUERY)
    except linear_ops.LinearRateLimited:
        refused = True
    except Exception as exc:  # noqa: BLE001 — every other failure is a failed read
        raise ReadFailed(" ".join(f"{type(exc).__name__}: {exc}".split())[:300]) from exc
    reading = linear_ops.rate_reading()
    if reading.get("call", 0) <= before:
        # No response arrived for this read: no header of it to report.
        reading = {}
    if not refused and reading.get("remaining") is None:
        # An answer that does not say what is left is not a low allowance.
        raise ReadFailed("Linear answered without an x-ratelimit-requests-remaining header")
    return Reading(remaining=reading.get("remaining"), limit=reading.get("limit"),
                   reset_ms=reading.get("reset_ms"), refused=refused)


# --------------------------------------------------------------------------- #
# the held line                                                                #
# --------------------------------------------------------------------------- #

ORDER_FALLBACK = ("planner allowance: the Overview's order could not be read ({why}) — "
                  "the held line runs in arrival order")


def _say(line: str) -> None:
    """The line's one-line notes go to stderr: stdout carries the log line
    and the `key=value` answers."""
    print(line, file=sys.stderr, flush=True)


def _normal_order(order) -> tuple:
    """Identifiers as the board spells them, each once, first mention wins."""
    wanted = (str(i).strip().upper() for i in (order or ()))
    return tuple(dict.fromkeys(i for i in wanted if i))


def planning_order() -> list:
    """The order the CEO set on the Overview (DRE-5807), read through the
    console's read door — or `[]`, arrival order, when it cannot be read.

    Copied from the old planner line's ledger before DRE-8622 deleted it. It
    never raises and never blocks past the door client's own
    bound, and it says exactly one line each time it is asked."""
    try:
        import bureau_read  # noqa: PLC0415 — `check` must run with this file alone
        got = bureau_read.planning_order()
    except Exception as exc:  # noqa: BLE001 — a read failure never stalls the line
        reason = getattr(exc, "reason", None) or type(exc).__name__
        detail = " ".join(str(getattr(exc, "detail", None) or exc).split())
        _say(ORDER_FALLBACK.format(why=f"{reason}: {detail}" if detail else reason))
        return []
    order = list(_normal_order(got.order))
    if order:
        _say(f"planner allowance: the Overview's order names {len(order)} card(s), set by "
             f"{got.set_by or 'an unrecorded person'} at {got.set_at or 'an unrecorded time'}"
             " — they go first; the rest follow oldest held first")
    else:
        _say("planner allowance: no order is set on the Overview — "
             "the held line runs in arrival order")
    return order


def held_line(linear_ops, now: datetime, cfg: dict) -> dict:
    """card → the `at` of its live `held`, over every card whose newest
    planner-hold receipt is a live `held`. One paged read; raises as `gql`
    does."""
    since = now - timedelta(minutes=cfg["hold_max_minutes"] + cfg["wait_minutes"])
    nodes = linear_ops.gql_paged(HELD_LINE_QUERY, {"since": _iso(since)},
                                 connection="comments")
    by_card: dict = {}
    for node in nodes:
        r = parse_receipt(node)
        if r is None:
            continue
        card = ((node.get("issue") or {}).get("identifier") or r.card).strip().upper()
        by_card.setdefault(card, []).append(node)
    line = {}
    for card, bodies in by_card.items():
        r = _newest(bodies)
        if held(bodies, now):
            line[card] = _dt(r.at)
    return line


def _fallback_place(run_id) -> int:
    text = str(run_id).strip()
    number = int(text) if text.isdigit() else sum(text.encode())
    return number % PLACES


def place(linear_ops, card: str, run_id, began: datetime, now: datetime, cfg: dict) -> int:
    """How many cards are ahead of this one in the held line."""
    try:
        line = held_line(linear_ops, now, cfg)
    except Exception as exc:  # noqa: BLE001 — refused or failed: run_id mod 30
        why = "Linear refused it" if isinstance(exc, linear_ops.LinearRateLimited) \
            else " ".join(f"{type(exc).__name__}: {exc}".split())[:200]
        spot = _fallback_place(run_id)
        _say(f"planner allowance: the held line could not be read ({why}) — "
             f"{card} takes place {spot}, run_id mod {PLACES}")
        return spot
    mine = card.strip().upper()
    line.setdefault(mine, began)
    order = planning_order() if len(line) > 1 else []
    ranked = {c: i for i, c in enumerate(order)}
    rows = sorted(line, key=lambda c: (0, ranked[c], line[c], c) if c in ranked
                  else (1, 0, line[c], c))
    return rows.index(mine)


# --------------------------------------------------------------------------- #
# the wait                                                                     #
# --------------------------------------------------------------------------- #


def _warn(message: str) -> None:
    print("::warning::" + " ".join(str(message).split()), flush=True)


def _next_boundary(at: datetime, minutes: int) -> datetime:
    step = minutes * 60
    stamp = at.timestamp()
    return datetime.fromtimestamp((int(stamp // step) + 1) * step, timezone.utc)


def _log(verb: str, card: str, reading: Reading | None, threshold, read_word: str,
         receipt: str, requests: int, at: datetime) -> None:
    remaining = reading.remaining if reading else None
    limit = reading.limit if reading else None
    print(f"{TAG} {verb} · card {card} · remaining {_number(remaining)} of "
          f"{_number(limit)} · threshold {threshold} · read {read_word} · receipt "
          f"{receipt} · requests {requests} · at {_iso(at)}", flush=True)


def _post(linear_ops, card: str, body: str) -> str:
    """`posted`, `refused` (Linear's rate limit — the stop is armed), or
    `failed` (anything else, said with a `::warning::`). Never raises. A
    rate-limited post is never retried here and never moved onto the fleet
    key (`linear_ops.is_auth_refusal`)."""
    try:
        condition = linear_ops.cmd_comment(card, body)
    except linear_ops.LinearRateLimited:
        return "refused"
    except Exception as exc:  # noqa: BLE001 — a receipt never fails the planner
        _warn(f"planner allowance for {card}: the receipt did not post — {exc}")
        return "failed"
    if condition:
        _warn(f"planner allowance for {card}: the receipt did not post — {condition}")
        return "failed"
    return "posted"


def _answer(because: str) -> dict:
    return {"start": "true", "because": because}


def _fixture(cfg: dict, repo: str, title: str) -> int | None:
    fx = cfg["fixture_low_reading"]
    if (repo or "").strip() == fx["repo"] and fx["title_marker"] in (title or ""):
        return fx["remaining"]
    return None


def wait(card, run_id, repo, title, now=None, sleep=None, *, linear_ops=None) -> dict:
    """The decision loop. Always answers `start=true`; `because` says why."""
    now = now or _utcnow
    sleep = sleep or time.sleep
    run_id = str(run_id)
    try:
        cfg = load()
    except PlannerAllowanceError as exc:
        _warn(f"planner allowance for {card}: started without a reading — {exc}")
        return _answer("read-failed")
    if linear_ops is None:
        import linear_ops  # noqa: PLC0415 — `check` must run with this file alone
    lops = linear_ops
    threshold, every = cfg["threshold_remaining"], cfg["wait_minutes"]
    fixture = _fixture(cfg, repo, title)
    began = begin = None  # the hold's start, and the reading that began it
    held_posted = stopped = False
    first = True
    while True:
        at = now()
        if stopped:
            # After the sleep to this boundary, and only then (DRE-8616).
            lops.lift_rate_limit_stop(f"planner allowance re-read for {card}")
            stopped = False
        try:
            reading = read(lops)
        except ReadFailed as exc:
            _warn(f"planner allowance for {card}: the read failed, so the planner starts "
                  f"now — {exc}")
            _log("start", card, None, threshold, "failed", "none", lops.requests_made(), at)
            return _answer("read-failed")
        if first and fixture is not None and not reading.refused:
            _say(f"planner allowance: {card} is the sandbox's ALLOWANCE FIXTURE — the real "
                 f"reading was {_number(reading.remaining)} of {_number(reading.limit)}; "
                 f"{fixture} stands in for it on this first read only")
            reading.remaining = fixture
        first = False
        read_word = "refused" if reading.refused else "answered"
        stopped = stopped or reading.refused
        low = reading.refused or reading.remaining is None or reading.remaining < threshold
        if began is None:
            if not low:
                _log("start", card, reading, threshold, read_word, "none",
                     lops.requests_made(), at)
                return _answer("allowance-ok")
            began, begin = at, reading
        until = began + timedelta(minutes=cfg["hold_max_minutes"] + every)
        nxt = _next_boundary(at, every)
        at_max = nxt > began + timedelta(minutes=cfg["hold_max_minutes"])
        receipt = "none"
        if low and not reading.refused and not held_posted:
            # The first read of the hold Linear answers: the `held`, reporting
            # the read that BEGAN the hold.
            outcome = _post(lops, card, format_receipt(
                "held", card=card, run=run_id, repo=repo, remaining=begin.remaining,
                limit=begin.limit, at=began, next_read=nxt, every=every, until=until))
            held_posted = outcome == "posted"
            stopped = stopped or outcome == "refused"
            receipt = "held" if held_posted else "refused"
        elif low and reading.refused and not held_posted:
            receipt = "deferred"
        if low and not at_max:
            _log("hold", card, reading, threshold, read_word, receipt, lops.requests_made(), at)
            sleep(max((nxt - now()).total_seconds(), 0))
            continue
        because = "hold-max" if low else "allowance-ok"
        spot = place(lops, card, run_id, began, now(), cfg)
        sleep(min(spot * cfg["stagger_seconds"], every * 60))
        outcome = _post(lops, card, format_receipt(
            "started", card=card, run=run_id, repo=repo, remaining=reading.remaining,
            limit=reading.limit, at=at, because=because))
        _log("start", card, reading, threshold, read_word,
             "started" if outcome == "posted" else "refused", lops.requests_made(), at)
        return _answer(because)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _write_outputs(path: str | None, answer: dict) -> None:
    for key, value in answer.items():
        print(f"{key}={value}")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in answer.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:
        _warn(f"planner allowance: could not write step outputs: {exc}")


def _cmd_check(args, **_) -> int:
    try:
        cfg = load()
    except PlannerAllowanceError as exc:
        print(f"planner allowance config: {exc}", file=sys.stderr)
        return 1
    print(f"planner allowance config OK: {config_path()} "
          f"(threshold_remaining={cfg['threshold_remaining']} "
          f"wait_minutes={cfg['wait_minutes']} hold_max_minutes={cfg['hold_max_minutes']} "
          f"stagger_seconds={cfg['stagger_seconds']})")
    return 0


def _cmd_wait(args, now=None, sleep=None) -> int:
    try:
        answer = wait(args.card, args.run_id, args.repo, args.title, now=now, sleep=sleep)
    except Exception as exc:  # noqa: BLE001 — a planner never fails because of the read
        _warn(f"planner allowance for {args.card}: started after an error — {exc}")
        answer = _answer("read-failed")
    _write_outputs(args.github_output, answer)
    return 0


def main(argv=None, *, now=None, sleep=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate config/planner-allowance.json")
    c.set_defaults(fn=_cmd_check)

    c = sub.add_parser("wait", help="read the allowance; hold while it is low; then start")
    c.add_argument("card")
    c.add_argument("--run-id", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--title", default="")
    c.add_argument("--github-output", default=None)
    c.set_defaults(fn=_cmd_wait)

    args = ap.parse_args(argv)
    return args.fn(args, now=now, sleep=sleep)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
