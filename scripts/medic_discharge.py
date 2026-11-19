#!/usr/bin/env python3
"""The sweep closes a medic stop the card has already moved on from (DRE-9283).

The stop is the medic's diagnosis, the `run-failure-diagnosed` act: a `hold`
whose trailer says `next: operator`, and which nothing in the registry
discharged. The week ending 2026-11-07 counted 170 of them standing still for
1,488.3 hours (DRE-9269), and none needed the CEO. The medic had already acted
on most — it re-ran 8, sent 6 to Planning and named the fix loop for 107 — and
a merge or a sweep had carried 154 of them on. The stop outlived the recovery
it was recording.

This module is the decision, pure: it reads a card's thread and, for a
`fix-path` stop, the card's pull request, and answers whether the stop is over
and why. `reconcile.discharge_medic_stops` does the reads, decides here, and
posts the receipt this module composes, whose trailer reads `discharges:
run-failure-diagnosed` — the registry's grammar for "this act ended that one",
the one the fix loop's restart already uses for a disputed fix attempt. The
stop wears no label and has no lift condition, so nothing about holds changes.

A stop is one `medic-next-step` receipt (`medic_next_step.parse_receipt` on
the comment's first line), named by its `run=`. The first rule that applies
decides it:

  1. the thread holds a `medic-stop-discharged` receipt with the same `run=`
     — nothing is written;
  2. `step=ask-person` keeps waiting, whatever happens to the card after: a
     question for a person is not a recovery;
  3. a first line ending ` moved=no` keeps waiting — the step did not land;
  4. `step=requeue-once` discharges, `because=re-ran`;
  5. `step=send-to-planning` discharges, `because=sent-to-planning`;
  6. `step=fix-path` discharges only on a carrier after the receipt, the
     first found naming it: the pull request merged (`merged`), took a new
     newest commit while open (`new-head`), or a comment on the card or the
     pull request carries the trailer of a `dispatched` act the sweep emits
     (`sweep-dispatched`).

`fix-path` needs a carrier because `medic_next_step.receipt` never writes
` moved=no` on it, so `parse_receipt` reads `moved: True` for every one,
although the medic moved nothing. A diagnosis with no receipt after it is the
report's "no step", and is never a candidate: the pass starts from receipts.

The sweep reads a card's thread whole only when its comment window carries a
receipt and is not the whole thread. A discharge, and every carrier, comes
after the receipt it answers, so a window that holds the receipt holds them
too: nothing is ever discharged twice. What a window with no receipt costs is
a stop older than the card's newest fifty comments, which stays as it is.

Import-safe and I/O-free: no file, network or Linear read at import or in any
function here, so every case is a unit test over a fixture thread.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medic_next_step  # noqa: E402 — the one reader of the medic's receipt
import pipeline_act  # noqa: E402 — the trailer's reader, and the registry's rows

#: The act and its tag (config/pipeline-acts.json). Written as a literal: the
#: act registry reads `X_TAG = "..."` constants straight off the emitter file.
ACT = "medic-stop-discharged"
RECEIPT_TAG = "medic-stop-discharged"
RECEIPT_MARK = f"🩺 {RECEIPT_TAG}:"
#: The act this one ends — its trailer's `discharges:` field.
DISCHARGES = "run-failure-diagnosed"

#: The phrase config/pipeline-acts.json pins as this emitter's anchor. Named
#: once and used once, in `body`.
ANCHOR_PHRASE = "so this stop is not waiting on the operator"

#: Every line the sweep prints for this path opens with this.
LOG_PREFIX = "medic-discharge:"

#: The conditions, as the receipt's `because=` writes them.
RE_RAN = "re-ran"
SENT_TO_PLANNING = "sent-to-planning"
MERGED = "merged"
NEW_HEAD = "new-head"
SWEEP_DISPATCHED = "sweep-dispatched"

#: The medic's steps this module reads (`medic_next_step.STEPS`).
ASK_PERSON = "ask-person"
FIX_PATH = "fix-path"
#: The steps whose own move ends the stop, and the condition each names.
OWN_STEP = {"requeue-once": RE_RAN, "send-to-planning": SENT_TO_PLANNING}

#: The files whose `dispatched` acts are the sweep's. The act names are read
#: off the registry (`sweep_dispatch_acts`), never listed here.
SWEEP_EMITTERS = ("scripts/reconcile.py", "scripts/stale_merge_ref.py")
DISPATCHED = "dispatched"

#: The lanes a card the sweep reads sits in. `BOARD_LANES` are in the
#: sweep's own board read, so they cost the pass nothing; the queue is not,
#: and it is read here with the closed lane, for the cards completed inside
#: the window — a merged `fix-path` card is there.
LANES = ("In Progress", "Queued", "In Review", "Planning")
BOARD_LANES = ("In Progress", "In Review", "Planning")
QUEUE_LANE = "Queued"
DONE_LANE = "Done"
DONE_WINDOW = timedelta(hours=24)

#: This repo's `QUEUE_LANE` cards, and its `DONE_LANE` cards completed since
#: `$since`, in one paged read. The label is matched the way
#: `repo_held_epics` matches it — any label containing the slug, case-blind —
#: and the caller keeps the cards whose `repo:` slug is exactly this repo.
CARDS_QUERY = """query($needle: String!, $queue: String!, $done: String!,
                       $since: DateTimeOrDuration!, $after: String) {
  issues(first: 100, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    labels: {name: {containsIgnoreCase: $needle}},
    or: [
      {state: {name: {eq: $queue}}},
      {and: [{state: {name: {eq: $done}}}, {completedAt: {gt: $since}}]}
    ]
  }) { nodes {
    id identifier state { name } labels { nodes { name } }
    %s
  } pageInfo { hasNextPage endCursor } } }"""

_DISCHARGED_LINE = re.compile(
    rf"^{re.escape(RECEIPT_MARK)} because=(\S+) step=(\S+) run=(\S+)$")
_PT = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Stop:
    """One medic stop: the `medic-next-step` receipt that names it."""

    card: str
    run: str
    step: str
    cls: str
    moved: bool
    created_at: str


@dataclass(frozen=True)
class Discharge:
    """A stop that is over, and the condition that ended it. `pr_number` and
    `at` are the pull request and the moment for `merged` and `new-head`;
    `act` is the sweep's act for `sweep-dispatched`."""

    stop: Stop
    because: str
    pr_number: int | None = None
    at: str | None = None
    act: str | None = None


def query_variables(slug: str, now: datetime) -> dict:
    """The variables `CARDS_QUERY` is asked with, for `slug` at `now`."""
    since = (now - DONE_WINDOW).astimezone(timezone.utc)
    return {"needle": slug, "queue": QUEUE_LANE, "done": DONE_LANE,
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ")}


# --------------------------------------------------------------------------- #
# the thread                                                                   #
# --------------------------------------------------------------------------- #


def _ts(iso: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _after(iso: str | None, stop: Stop) -> bool:
    """Is `iso` strictly later than the stop's receipt? An unreadable time on
    either side is not later: an unread moment carries nothing."""
    moment, receipt_at = _ts(iso), _ts(stop.created_at)
    return moment is not None and receipt_at is not None and moment > receipt_at


def stops(card: str, thread) -> list[Stop]:
    """Every stop on `thread` (`[{"body", "createdAt"}]`, oldest first),
    oldest first: each comment whose first line `parse_receipt` reads, once
    per `run=` — a repeated run is the stop its first receipt named."""
    out: list[Stop] = []
    seen: set = set()
    for comment in thread or []:
        parsed = medic_next_step.parse_receipt(comment.get("body"))
        if parsed is None or parsed["run"] in seen:
            continue
        seen.add(parsed["run"])
        out.append(Stop(card, parsed["run"], parsed["step"], parsed["class"],
                        parsed["moved"], comment.get("createdAt") or ""))
    return out


def discharged_runs(thread) -> set:
    """The `run=` of every `medic-stop-discharged` receipt on `thread`."""
    runs = set()
    for comment in thread or []:
        first = (comment.get("body") or "").split("\n", 1)[0].strip()
        found = _DISCHARGED_LINE.match(first)
        if found:
            runs.add(found.group(3))
    return runs


def needs_pull_request(stop: Stop, thread) -> bool:
    """Does deciding `stop` read the card's pull request? Only a `fix-path`
    stop rule 6 is reached for — every other one is decided off the thread."""
    return (stop.run not in discharged_runs(thread) and stop.step == FIX_PATH
            and stop.moved)


def sweep_dispatch_acts(rows=None) -> frozenset:
    """The act names whose row has `state: dispatched` and is emitted from
    one of `SWEEP_EMITTERS` — read off the registry (`pipeline_act.rows()`)
    unless `rows` is handed in."""
    rows = pipeline_act.rows() if rows is None else rows
    return frozenset(r["name"] for r in rows
                     if r.get("state") == DISPATCHED
                     and (r.get("emits") or {}).get("file") in SWEEP_EMITTERS)


# --------------------------------------------------------------------------- #
# the decision                                                                 #
# --------------------------------------------------------------------------- #


def decide(stop: Stop, thread, *, pr: dict | None = None,
           dispatch_acts=None) -> Discharge | None:
    """The discharge `stop` owes, or None while it keeps waiting. `thread`
    is the card's; `pr` is its pull request as `reconcile.pr_for` reads it
    with `mergedAt` and `commits` beside, or None when it has none."""
    if stop.run in discharged_runs(thread):
        return None                                        # rule 1
    if stop.step == ASK_PERSON:
        return None                                        # rule 2
    if not stop.moved:
        return None                                        # rule 3
    if stop.step in OWN_STEP:
        return Discharge(stop, OWN_STEP[stop.step])        # rules 4 and 5
    if stop.step not in medic_next_step.STEPS:
        return None             # a step this reader was never taught waits
    return _carrier(stop, thread, pr, dispatch_acts)       # rule 6: fix-path


def _carrier(stop: Stop, thread, pr: dict | None, dispatch_acts) -> Discharge | None:
    pr = pr or {}
    number = pr.get("number")
    state = (pr.get("state") or "").upper()
    if state == "MERGED" and _after(pr.get("mergedAt"), stop):
        return Discharge(stop, MERGED, pr_number=number, at=pr.get("mergedAt"))
    commits = pr.get("commits") or []
    newest = (commits[-1] or {}).get("committedDate") if commits else None
    if state == "OPEN" and _after(newest, stop):
        return Discharge(stop, NEW_HEAD, pr_number=number, at=newest)
    acts = sweep_dispatch_acts() if dispatch_acts is None else dispatch_acts
    for comment in [*(thread or []), *(pr.get("comments") or [])]:
        if not _after(comment.get("createdAt"), stop):
            continue
        trailer = pipeline_act.read_trailer(comment.get("body") or "")
        if trailer and trailer.get("act") in acts:
            return Discharge(stop, SWEEP_DISPATCHED, act=trailer["act"])
    return None


# --------------------------------------------------------------------------- #
# the receipt                                                                  #
# --------------------------------------------------------------------------- #


def _pt(iso: str | None) -> str:
    moment = _ts(iso)
    if moment is None:
        return "an unread time"
    local = moment.astimezone(_PT)
    hour = local.hour % 12 or 12
    return (f"{local:%Y-%m-%d} {hour}:{local.minute:02d} "
            f"{'AM' if local.hour < 12 else 'PM'} PT")


def _what(found: Discharge) -> str:
    if found.because == RE_RAN:
        return "the medic re-ran the failed jobs once"
    if found.because == SENT_TO_PLANNING:
        return "the medic sent the card back to Planning"
    if found.because == MERGED:
        return f"pull request #{found.pr_number} merged at {_pt(found.at)}"
    if found.because == NEW_HEAD:
        return (f"pull request #{found.pr_number} took a new commit at "
                f"{_pt(found.at)}")
    return f"the sweep dispatched {found.act} on the card"


def body(found: Discharge) -> str:
    """The receipt's body before its trailer: the contract line, then one
    plain-English sentence naming the condition. The sweep composes the
    comment as `pipeline_act.receipt(ACT, body(found))`, which appends the
    trailer whose `discharges:` reads `DISCHARGES`."""
    stop = found.stop
    first = (f"{RECEIPT_MARK} because={found.because} step={stop.step} "
             f"run={stop.run}")
    sentence = (f"The medic's stop for run {stop.run} is over: "
                f"{_what(found)} after the medic's {stop.step} receipt, "
                f"{ANCHOR_PHRASE}.")
    return f"{first}\n\n{sentence}"
