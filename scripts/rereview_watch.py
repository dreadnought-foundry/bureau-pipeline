#!/usr/bin/env python3
"""The review the pipeline promised and nobody ran (DRE-4492, DRE-5278).

The failure this module detects is SILENCE. A plan is with the critics, the
pipeline has promised the next step — a review, a re-review, a retry — and
then nothing: no round record, no tombstone, nothing on any board saying the
promise was not kept. DRE-4025 sat that way for 33 hours, DRE-3964 for 12,
DRE-4083 for 3.5, DRE-4425 for 8h43m, DRE-4396 for 21 and counting; every one
of them was found by a person reading a sweep log and recovered by hand with
`review_rerun.RERUN_REVIEW_ACT`.

Under DRE-5268 the second critic reads a plan while its epic sits in Planning
(`plan_critic.REVIEW_LANE`), so this watcher owns every epic under review
there (`under_review`), and keeps the one In Progress silence it was built
for, because an epic approved under the old rule can still sit In Progress
with a send-back no review followed. The lane decides which silences apply:

  Planning     1. handed off, no review — the first critic passed the plan,
                  the hand-off to the second critic produced nothing.
               2. sent back, no re-review — `post_release` is POST_HELD below
                  the bound and no round followed.
               3. died, no retry — the newest record is a tombstone and no
                  retry reached the planner behind it.
  In Progress  4. sent back, no re-review — silence 2's reading, nothing else.

Each is judged past `REREVIEW_GRACE_MINUTES` from the record that made the
promise and fires at most twice per record. The FIRST firing asks for the
review itself (`review_rerun.py dispatch`, with `--reason` and
`--trigger-state` always explicit) and says so. The SECOND is judged from the
watcher's own notice — older than the grace, with no planner-slot receipt, no
round and no tombstone after it — and parks the epic: `needs-human` FIRST,
then `plan_critic.BOUND_PARK_LANE`, then a note ending with
`plan_critic.REAPPROVE_HOW`. The label goes first because the relay
dispatches a plan run the moment an `agent:planner` card enters Triage, and
the plan-gate refuses that run only when the card already carries the label.

THE PLANNER LINE IS NOT SILENCE (DRE-5167). A review asked for claims a planner
slot like any plan run; with none free it posts a `waiting` receipt and the
line's backstop (DRE-5178) dispatches it in order. An epic in line inside
the line's bound (`planner_queue.overdue`) is quiet whatever the age of its
promise, and one past
the bound belongs to the stall watchdog's line rule (DRE-5177) — one owner per
shape. Every line fact is `planner_queue`'s, read off pipeline-written
receipts only.

WHAT IT DOES NOT OWN. Every credential this reads is `plan_critic`'s, imported
rather than re-spelled, so the sweep's gate and this detector can never
disagree about one epic:

  * `current_cycle_entries` — which comments belong to the planning attempt in
    front of us, keeping each comment's record so its `created_at` survives
    the scoping (a re-plan opens a fresh window).
  * `trusted_bodies` / `parse_markers` / `parse_deaths` — a record counts only
    when the pipeline wrote it AND the comment says nothing else. A forged
    SEND_BACK cannot trigger a firing and a forged round 2 cannot silence it.
  * `post_release` — POST_HELD is the send-back this watches.
  * `post_bound_reached` — the bound parks loudly with `needs-human`, which is
    the opposite of silence.
  * `REAPPROVE_HOW` — the sentence every park ends with.

CLI:
  check <EPIC> [--now <ISO>]   say `overdue …` or `quiet …` with the reason.
                               Writes nothing, exits 0 either way. Bare, it
                               reads the live thread and lane. With `--now` it
                               REPLAYS, and a replay moves all three: the lane
                               comes off the epic's own state history at that
                               instant, the thread keeps only the comments that
                               existed by then, and the clock is that instant
                               (DRE-4758) — Planning and In Progress history
                               alike.
  sweep [--now <ISO>]          run `report` over `linear_ops.py epics-in-flight
                               --sight` for an operator. This POSTS, DISPATCHES
                               and PARKS. `--now` here is the clock only — the
                               sweep reads live state, which is the whole of
                               its job.

The reconcile sweep calls `report` directly (one call, in `main()`'s report
phase beside `report_break_glass()`).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import UTC, datetime
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import plan_critic  # noqa: E402
import planner_queue  # noqa: E402
import review_rerun  # noqa: E402

#: The first word of every notice, and its idempotency key. In the
#: `dead_run.DEAD_TAG` shape every other refusal on an epic uses, and its own
#: tag rather than one of `plan_critic`'s three: "the critic found a gap" and
#: "the review that was promised never ran" are different facts with different
#: next actions, and one tag would let the first silence the second forever.
REREVIEW_MISSING_TAG = "plan-critic-rereview-missing"

#: The words that make a notice this watcher's FIRST firing, and the words
#: that make it the SECOND. Read back off the thread to decide which firing is
#: due, so no notice of one kind may carry the other's words (the tests hold
#: every notice to that). A notice carrying the tag and neither phrase was
#: written before DRE-5278 and counts as no firing: the watcher then asks for
#: the review once, the conservative act, rather than parking at once.
FIRST_FIRING_WORDS = "asked for the review again"
SECOND_FIRING_WORDS = f"parked in {plan_critic.BOUND_PARK_LANE}"

#: The lanes this watcher reads. Planning is where both critics read a plan
#: under DRE-5268; In Progress keeps the old-rule send-back.
WATCHED_LANES = (plan_critic.REVIEW_LANE, plan_critic.APPROVAL_LANE)

#: Which silence a promise is. One word each, for the log line and the notice.
HANDED_OFF = "handed-off"
SENT_BACK = "sent-back"
DIED = "died"

#: How long a promise has to be unkept before the watcher acts, and how long
#: its own re-ask gets before the second firing.
#:
#: Sized from what the promise actually costs when it is kept: the run queues
#: for seconds, then the review runs at up to
#: `plan_critic.POST_REVIEW_TURNS_CAP` turns at the ~9 s/turn measured on
#: DRE-3164 — about twenty minutes with setup. Forty-five minutes is
#: comfortably longer than the kept promise and short enough that the shortest
#: incident this was built for (DRE-4083, 3.5 hours) is acted on within four
#: sweeps. A wait in the planner line is not measured against it at all (see
#: the module docstring).
#:
#: Beside `reconcile.POST_CRITIC_GRACE_MINUTES` in spirit, including the reason
#: it exists at all: this is a decision about when to ACT, never about what is
#: true. The log line prints on every sweep from the first one
#: (standards/console-honesty.md rule 1). It has a default, so it is NOT in
#: `reconcile.REQUIRED_ENV` and no workflow step declares it.
REREVIEW_GRACE_MINUTES = int(os.environ.get("REREVIEW_GRACE_MINUTES", "45"))


class NoticeFailed(Exception):
    """One or more firings could not be completed.

    Raised at the END of `report`, after every other epic has had its turn: a
    dispatch or a Linear write that failed must take the sweep red for the
    medic (DRE-1254 — never exit 0 on a write we claimed to make and did not),
    but one epic's failure must not cost the others their reading.
    """


class Reading(NamedTuple):
    """What `read` saw on one epic's thread.

    `found` is the unkept promise REGARDLESS of whether there is anything to
    do about it this sweep, and `spoken` says whether there is not. The two
    are separate because the console-honesty rule and the idempotency rule
    pull in opposite directions: the log line speaks on every sweep, a firing
    at most twice per record. `log` is a line the sweep prints for a QUIET
    reading that still has something to say — the planner line.
    """

    found: dict | None
    spoken: bool
    why: str
    log: str | None = None


# --- The reading -------------------------------------------------------------

def _minutes_since(iso: str, now: str | None = None) -> float:
    """Minutes between `iso` and `now` (an ISO string), or the clock."""
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    at = (datetime.fromisoformat(now.replace("Z", "+00:00"))
          if now else datetime.now(UTC))
    return (at - then).total_seconds() / 60


def _utc(iso: str) -> str:
    return (datetime.fromisoformat(iso.replace("Z", "+00:00"))
            .astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"))


def _created_at(entry) -> str | None:
    return entry.get("created_at") if isinstance(entry, dict) else None


def _body(entry) -> str:
    return (entry.get("body") or "") if isinstance(entry, dict) else (entry or "")


def _after(stamp: str | None, than: str | None) -> bool:
    """Is `stamp` strictly later than `than`? Unknown on either side is no."""
    try:
        a = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
        b = datetime.fromisoformat((than or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    a = a if a.tzinfo else a.replace(tzinfo=UTC)
    b = b if b.tzinfo else b.replace(tzinfo=UTC)
    return a > b


def under_review(records, epic: str) -> bool:
    """Is this epic with the critics, so that this watcher owns it?

    True when the current planning attempt (`plan_critic.current_cycle_entries`)
    carries a first-critic round the plan route proceeds on — its newest
    `stage=pre` round is `PASS` or `NO_RESULT` — or any second-critic
    (`stage=post`) round or tombstone. False otherwise, and False over a thread
    with no attempt records at all.

    `records` is the `dump-comments --with-authors` shape
    (`linear_ops.comment_records`, or `linear_ops.window_nodes` over a board
    read). The credential is `plan_critic`'s: a record is a comment the
    pipeline wrote that says nothing else, so a PASS posted by anyone else
    puts nothing under review.
    """
    newest_pre = None
    for entry in plan_critic.current_cycle_entries(records, epic):
        rows = plan_critic.parse_markers([entry])
        if rows:
            if rows[0]["stage"] == plan_critic.STAGE_POST:
                return True
            if rows[0]["stage"] == plan_critic.STAGE_PRE:
                newest_pre = rows[0]
            continue
        deaths = plan_critic.parse_deaths([entry])
        if deaths and deaths[0]["stage"] == plan_critic.STAGE_POST:
            return True
    return bool(newest_pre) and newest_pre["result"] in (
        plan_critic.PASS, plan_critic.NO_RESULT)


def _records(entries) -> dict:
    """The attempt's records, one pass: the newest pre round and the newest
    post-stage record (a round or a tombstone), each with its index."""
    out: dict = {"pre": None, "post": None}
    for index, entry in enumerate(entries):
        rows = plan_critic.parse_markers([entry])
        if rows:
            if rows[0]["stage"] == plan_critic.STAGE_PRE:
                out["pre"] = (index, rows[0], entry)
            elif rows[0]["stage"] == plan_critic.STAGE_POST:
                out["post"] = ("round", index, rows[0], entry)
            continue
        deaths = plan_critic.parse_deaths([entry])
        if deaths and deaths[0]["stage"] == plan_critic.STAGE_POST:
            out["post"] = ("death", index, deaths[0], entry)
    return out


def _receipt_node(entry):
    """A thread row as the node `planner_queue.parse_receipt` dates by
    Linear's own stamp (`createdAt`), not by the receipt's `at` field."""
    if isinstance(entry, dict):
        return {"body": entry.get("body") or "",
                "createdAt": entry.get("created_at")}
    return entry


def _receipt_bodies(records) -> list:
    """The planner-slot evidence this may read: pipeline-written comments
    only. A `waiting` receipt anyone else posts is not a place in line."""
    out = []
    for entry in records or []:
        if isinstance(entry, dict):
            if entry.get("authored_by_pipeline"):
                out.append(_receipt_node(entry))
        else:
            out.append(entry)
    return out


def _receipt(entry):
    return planner_queue.parse_receipt(_receipt_node(entry))


def _the_line(epic: str, bodies, now: str | None) -> Reading | None:
    """The epic in the planner line, read through `planner_queue` — or None
    when it is not in line."""
    if not planner_queue.in_line(bodies, now):
        return None
    waited = planner_queue.waited_minutes(bodies, now)
    receipts = [r for r in (planner_queue.parse_receipt(b) for b in bodies) if r]
    newest = receipts[-1] if receipts else None
    waits = [r for r in receipts if r.state == "waiting" and r.place]
    place = (f", place {waits[-1].place} of {waits[-1].of}" if waits else "")
    state = newest.state if newest else "waiting"
    minutes = ("an unknown number of" if waited is None else str(int(waited)))
    said = (f"waiting in the planner line for {minutes} min (newest receipt "
            f"`{state}`{place})")
    if planner_queue.overdue(bodies, now):
        why = (f"{said}, past the line's bound — the Planning stall "
               "watchdog's line rule (DRE-5177) owns this epic, so this "
               "watcher leaves it alone")
    else:
        why = (f"{said} — a wait inside the line's bound is not silence; the "
               "line's backstop dispatches it in order")
    return Reading(None, False, why, log=f"rereview-missing: {epic} quiet — {why}")


def _lane_refusal(lane: str | None) -> str:
    where = lane or "a lane Linear did not name"
    head = (f"the epic is in {where}, not {plan_critic.REVIEW_LANE} or "
            f"{plan_critic.APPROVAL_LANE}")
    if lane == "Green Light":
        return (head + " — both critics passed this plan and it waits for a "
                "decision, so nothing here is waiting on a review")
    if lane == "Todo":
        return (head + " — an epic in Todo is with nobody, so nothing here is "
                "waiting on a review")
    return head + " — nothing here is waiting on a review"


def _sent_back(records, epic: str, post, lane: str):
    """Silences 2 and 4: `(promise, None)` or `(None, why)`."""
    _kind, index, row, entry = post
    state, detail = plan_critic.post_release(records, epic)
    if state != plan_critic.POST_HELD:
        if row["result"] == plan_critic.NO_RESULT:
            owner = ("the review route's own re-ask (DRE-5280)"
                     if lane == plan_critic.REVIEW_LANE else
                     "the re-run act, which a person posts")
            return None, (
                f"the newest second-critic round is `{plan_critic.NO_RESULT}` "
                f"— that is not a send-back, and its owner is {owner}, not "
                "this watcher"
            )
        return None, (
            f"the second critic's state on this plan is `{state}`, not "
            f"`{plan_critic.POST_HELD}` — no send-back is waiting on a "
            "re-review"
        )
    if plan_critic.post_bound_reached(records, epic):
        return None, (
            "the plan is at the bound and parked with needs-human — a park "
            "says so on the card, which is not the silence this watches for"
        )
    trigger = (review_rerun.TRIGGER_STATE_REVIEW
               if lane == plan_critic.REVIEW_LANE
               else review_rerun.TRIGGER_STATE_ACTIVATE)
    return {
        "silence": SENT_BACK,
        "index": index,
        "since": _created_at(entry),
        "sent_back_at": _created_at(entry),
        "round": row["round"],
        "reason": plan_critic.one_line(detail) or "no reason recorded",
        "finding": row["reason"] or "no reason recorded",
        "record": _body(entry).strip(),
        "dispatch_reason": review_rerun.REASON_RE_REVIEW,
        "trigger_state": trigger,
    }, None


def _promise(records, epic: str, lane: str, entries):
    """The record that made the promise this lane watches, as
    `(promise, None)`, or `(None, why)` when there is none to watch."""
    seen = _records(entries)
    post = seen["post"]
    if lane == plan_critic.APPROVAL_LANE:
        if post is None:
            return None, (
                "no second-critic round on this attempt — an epic In Progress "
                "with no review is reviewed by the activate route on the "
                "approval itself"
            )
        if post[0] == "death":
            return None, (
                "the newest second-critic record is a review that died — In "
                "Progress the pipeline's own after-death retry is behind it, "
                "so this is not the silence read here"
            )
        return _sent_back(records, epic, post, lane)
    if not under_review(records, epic):
        return None, (
            "the plan is not with the critics: no first-critic pass and no "
            "second-critic record on this attempt — the Planning stall "
            "watchdog reads that epic, not this watcher"
        )
    if post is None:
        index, _row, entry = seen["pre"]
        return {
            "silence": HANDED_OFF,
            "index": index,
            "since": _created_at(entry),
            "round": None,
            "reason": "the first critic passed this plan",
            "finding": "",
            "record": _body(entry).strip(),
            "dispatch_reason": review_rerun.REASON_REVIEW,
            "trigger_state": review_rerun.TRIGGER_STATE_REVIEW,
        }, None
    if post[0] == "death":
        _kind, index, death, entry = post
        _state, detail = plan_critic.post_release(records, epic)
        return {
            "silence": DIED,
            "index": index,
            "since": _created_at(entry),
            "round": None,
            "reason": plan_critic.one_line(detail) or "the review died",
            "finding": "",
            "record": _body(entry).strip(),
            "dead_run": death.get("run") or "?",
            "dispatch_reason": review_rerun.REASON_REVIEW_RETRY,
            "trigger_state": review_rerun.TRIGGER_STATE_REVIEW,
        }, None
    return _sent_back(records, epic, post, lane)


def _what(promise: dict) -> str:
    """The promise in a few words, for the quiet lines."""
    if promise["silence"] == SENT_BACK:
        return f"round {promise['round']} was sent back"
    if promise["silence"] == HANDED_OFF:
        return ("the first critic passed the plan and handed it to the second "
                "critic")
    return "the second critic's review died"


def _because(promise: dict, minutes: float) -> str:
    """What is missing, for the overdue line."""
    m = int(minutes)
    if promise["silence"] == SENT_BACK:
        r = promise["round"]
        return (f"round {r} was sent back {m} min ago, there is no round "
                f"{r + 1} and no tombstone, and the critic's reason was: "
                f"{promise['reason']}")
    if promise["silence"] == HANDED_OFF:
        return (f"the first critic passed the plan {m} min ago and handed it "
                "to the second critic, and no review and no tombstone followed")
    return (f"the second critic's review died {m} min ago "
            f"({promise['reason']}), and no retry and no round followed")


def _own_notices(entries, after: int) -> list[tuple[int, str, str | None]]:
    """This watcher's own notices after the promise, as `(index, kind, at)`
    with `kind` one of `first`, `park` or `legacy`.

    `entries` is already the pipeline's own comments (`current_cycle_entries`
    keeps nothing else), so a tag anyone else posts silences nothing.
    """
    out = []
    for index in range(after + 1, len(entries)):
        text = _body(entries[index]).strip()
        if not text.startswith(f"🚨 {REREVIEW_MISSING_TAG}:"):
            continue
        if SECOND_FIRING_WORDS in text:
            kind = "park"
        elif FIRST_FIRING_WORDS in text:
            kind = "first"
        else:
            kind = "legacy"
        out.append((index, kind, _created_at(entries[index])))
    return out


def _receipts_after(entries, after: int) -> list:
    """Planner-slot receipts the pipeline posted after `entries[after]`."""
    return [r for r in (_receipt(e) for e in entries[after + 1:]) if r]


def read(records, epic: str, lane: str | None, now: str | None = None,
         grace_minutes: int | None = None) -> Reading:
    """What this epic's thread says about the promise, in one pass.

    Every refusal below is somebody else's business, and each one is read off
    `plan_critic` or `planner_queue` rather than re-derived:

      * a lane that is not Planning or In Progress. In Green Light both
        critics passed the plan; in Todo the epic is with nobody; anywhere
        else there is no plan under review. An UNREADABLE lane is unknown,
        and unknown says nothing.
      * the planner line. An epic waiting for a planner slot is quiet inside
        the line's bound and the stall watchdog's past it.
      * no promise this lane watches (see the module docstring), the bound
        reached, or a newest round of `NO_RESULT`.
      * a record Linear gave no `created_at` for. There is no arithmetic to do
        on an unknown stamp, and the whole content of a firing is how long it
        has been (standards/console-honesty.md rule 2).
      * a planner run that claimed a slot after the promise: the review is
        running. For a dead review, any receipt behind the tombstone but the
        dead run's own release: a retry reached the planner behind it.
    """
    grace = REREVIEW_GRACE_MINUTES if grace_minutes is None else grace_minutes
    if lane not in WATCHED_LANES:
        return Reading(None, False, _lane_refusal(lane))
    bodies = _receipt_bodies(records)
    line = _the_line(epic, bodies, now)
    if line is not None:
        return line
    entries = plan_critic.current_cycle_entries(records, epic)
    promise, refusal = _promise(records, epic, lane, entries)
    if promise is None:
        return Reading(None, False, refusal)
    since = promise["since"]
    if not since:
        return Reading(None, False, (
            f"{_what(promise)}, but Linear named no time for the comment that "
            "recorded it — how long it has been is unknown"
        ))
    try:
        minutes = _minutes_since(since, now)
    except ValueError:
        # A stamp Linear gave us and we cannot read is unknown, exactly as
        # `plan_critic.promotion_refusal` reads an unparseable green light.
        return Reading(None, False, (
            f"{_what(promise)} at {since!r}, which is not a time this can "
            "read — how long it has been is unknown"
        ))
    if minutes < grace:
        return Reading(None, False, (
            f"{_what(promise)} {int(minutes)} min ago and the review it "
            f"promised has {grace} minutes to run"
        ))
    claim = planner_queue.open_claim(bodies, now)
    if claim is not None and _after(claim.created_at, since):
        return Reading(None, False, (
            f"{_what(promise)}, and planner run {claim.run} claimed a slot "
            "for this epic after it — the review is running"
        ))
    index = promise["index"]
    if promise["silence"] == DIED:
        behind = [r for r in _receipts_after(entries, index)
                  if not (r.state == "released"
                          and r.run == promise["dead_run"])]
        if behind:
            return Reading(None, False, (
                f"the second critic's review died, and a retry reached the "
                f"planner behind it (`{behind[-1].state}` from run "
                f"{behind[-1].run}) — the retry the review asked for owns it"
            ))
    found = dict(promise, minutes=minutes, lane=lane, firing=1)
    why = _because(promise, minutes)
    notices = _own_notices(entries, index)
    if any(kind == "park" for _i, kind, _at in notices):
        found["firing"] = 2
        return Reading(found, True, why + " — parked once already for this "
                       "record")
    firsts = [(i, at) for i, kind, at in notices if kind == "first"]
    if not firsts:
        return Reading(found, False, why)
    asked_index, asked_at = firsts[-1]
    found.update(firing=2, asked_at=asked_at)
    try:
        asked_minutes = _minutes_since(asked_at, now) if asked_at else None
    except ValueError:
        asked_minutes = None
    if asked_minutes is None:
        return Reading(found, True, why + " — asked for the review once "
                       "already, and Linear named no readable time for that "
                       "notice, so how long it has had is unknown")
    if asked_minutes < grace:
        return Reading(found, True, why + (
            f" — asked for the review once already, {int(asked_minutes)} min "
            f"ago, and that review has {grace} minutes to run"))
    landed = _receipts_after(entries, asked_index)
    if landed:
        return Reading(found, True, why + (
            f" — asked for the review once already, and a planner-slot "
            f"receipt (`{landed[-1].state}`) landed after that, so the ask "
            "reached the planner; not parked"))
    return Reading(found, False, why + (
        f" — asked for the review once already, {int(asked_minutes)} min ago, "
        "and nothing followed"))


def overdue(records, epic: str, lane: str | None, now: str | None = None,
            grace_minutes: int | None = None) -> dict | None:
    """The unkept promise with a firing DUE this sweep, or None.

    `{"silence", "firing", "round", "since", "minutes", "reason", …}` — the
    firing (1: ask again, 2: park) is part of the answer, because this is the
    question "is there anything to do". `read` is the same reading with the
    two halves kept apart, for the log line that prints either way.
    """
    reading = read(records, epic, lane, now, grace_minutes)
    return reading.found if (reading.found and not reading.spoken) else None


# --- What gets said ----------------------------------------------------------

def log_line(epic: str, found: dict) -> str:
    """The one line the sweep prints on EVERY sweep the promise is unkept."""
    m = int(found["minutes"])
    if found["silence"] == SENT_BACK:
        line = (f"rereview-missing: {epic} round {found['round']} sent back "
                f"{m} min ago — no round {found['round'] + 1} and no tombstone")
    elif found["silence"] == HANDED_OFF:
        line = (f"rereview-missing: {epic} handed to the second critic {m} min "
                "ago — no review and no tombstone")
    else:
        line = (f"rereview-missing: {epic} review died {m} min ago — no retry "
                "and no round")
    if found.get("firing") == 2:
        line += " — asked for once already"
    return line


def _first_notice(epic: str, found: dict) -> str:
    """The FIRST firing: the pipeline asked for the review again, and says so.

    A Planning notice names no act — the act fires only on an epic In Progress
    (DRE-3287) — and the In Progress one names it inside a sentence, so no line
    of it IS the act (DRE-3286: the relay matches the whole comment body).
    """
    head = f"🚨 {REREVIEW_MISSING_TAG}: {epic}"
    at = _utc(found["since"])
    m = int(found["minutes"])
    after = ("Nothing here is yours to decide. If that review does not run "
             "either, the epic goes to an operator with needs-human.")
    if found["silence"] == HANDED_OFF:
        return (
            f"{head}'s plan passed the first critic at {at} and was handed to "
            f"the second critic, but no review has run in the {m} minutes "
            "since and no record says one died — so the pipeline "
            f"{FIRST_FIRING_WORDS}.\n\n"
            f"The first critic's record: `{found['record']}`\n\n"
            f"{after}"
        )
    if found["silence"] == DIED:
        return (
            f"{head}: the second critic's review of this plan died at {at} "
            f"and no retry has reached the planner in the {m} minutes since — "
            f"so the pipeline {FIRST_FIRING_WORDS}.\n\n"
            f"The dead review: {found['reason']}\n\n"
            f"{after}"
        )
    nxt = found["round"] + 1
    if found["lane"] == plan_critic.REVIEW_LANE:
        return (
            f"{head}'s plan was sent back by the second critic at {at} "
            f"(round {found['round']}) and no re-review has run in the {m} "
            f"minutes since — no round {nxt} and no record of a review that "
            f"died — so the pipeline {FIRST_FIRING_WORDS}.\n\n"
            f"The critic's reason: {found['reason']}\n\n"
            f"{after}"
        )
    return (
        f"{head}'s plan was sent back by the second critic at {at} "
        f"(round {found['round']}) and the review the pipeline promised has "
        f"not run in the {m} minutes since, so the pipeline "
        f"{FIRST_FIRING_WORDS}.\n\n"
        f"The critic's reason: {found['reason']}\n\n"
        "The 🔁 receipt above this said the review was being run again by "
        "the pipeline, on its own. It was not: there is no round "
        f"{nxt} on this plan and no record of a review that died trying, so "
        "the children stay held until a review runs.\n\n"
        "A person can ask too: post a comment on the epic that says exactly "
        f"{review_rerun.RERUN_REVIEW_ACT} and nothing else.\n\n"
        f"{after}"
    )


def _park_note(epic: str, found: dict) -> str:
    """The SECOND firing: the epic is parked, and the note says why and how
    back — ending with `plan_critic.REAPPROVE_HOW`, the one sentence every park
    ends with."""
    head = (f"🚨 {REREVIEW_MISSING_TAG}: {epic} is {SECOND_FIRING_WORDS} with "
            "needs-human for an operator.")
    at = _utc(found["since"])
    asked = (f" at {_utc(found['asked_at'])}" if found.get("asked_at")
             else "")
    nothing = ("Nothing has started building. A review the pipeline cannot "
               "get to run twice needs a person to look at why, not a third "
               "ask.")
    back = f"**The way back:** {plan_critic.REAPPROVE_HOW}."
    if found["silence"] == HANDED_OFF:
        return (
            f"{head} The first critic passed this plan at {at}, and the "
            "hand-off to the second critic produced no review twice: the "
            f"pipeline asked for the review once more{asked} and nothing ran."
            "\n\n"
            f"The first critic's record: `{found['record']}`\n\n"
            "Nothing has been found wrong with the plan. "
            f"{nothing}\n\n{back}"
        )
    if found["silence"] == DIED:
        return (
            f"{head} The second critic's review of this plan died at {at} "
            f"(run {found['dead_run']}), and the retry the pipeline asked "
            f"for{asked} produced no review either.\n\n"
            f"The dead review: {found['reason']}\n\n"
            "Nothing has been found wrong with the plan. "
            f"{nothing}\n\n{back}"
        )
    return (
        f"{head} The second critic sent this plan back at {at} (round "
        f"{found['round']}), and the pipeline asked for the review twice — "
        f"the second time{asked} — and nothing ran.\n\n"
        f"The critic's finding, unresolved: {found['finding']}\n\n"
        f"{nothing}\n\n{back}"
    )


def notice(epic: str, found: dict) -> str:
    """The comment for the firing `found` is due — the first (the re-ask) or
    the second (the park). One function, so the sweep posts both through one
    line.

    Every notice opens with 🚨, which `reconcile._AGENT_COMMENT_PREFIXES`
    already reads as a machine comment, so the blocker gate is not confused
    into thinking a person spoke.
    """
    if found.get("firing") == 2:
        return _park_note(epic, found)
    return _first_notice(epic, found)


# --- The sweep's half --------------------------------------------------------

def dispatch_review(epic: str, repo: str | None, reason: str,
                    trigger_state: str) -> bool:
    """`review_rerun.py dispatch` for this epic, with `--reason` and
    `--trigger-state` both explicit — never the dispatch default (DRE-5277).

    In process, through `review_rerun.main`, so the contract the workflow
    steps call is the contract this calls. True only on exit 0: a notice is
    never posted on an unconfirmed dispatch (DRE-2034).
    """
    if not repo:
        print(f"ERROR: rereview-missing: no REPO to ask for {epic}'s review in",
              file=sys.stderr)
        return False
    argv = ["dispatch", "--epic", epic, "--repo", repo, "--reason", reason,
            "--trigger-state", trigger_state]
    try:
        return review_rerun.main(argv) == 0
    except SystemExit as exc:  # argparse refusing an argument
        return exc.code == 0


def _fire(epic: str, found: dict, repo: str | None) -> None:
    """What the firing DOES before its comment. Raises on a failure, so the
    comment is never posted for an act that did not happen."""
    if found.get("firing") == 2:
        # The label BEFORE the move: the relay dispatches a plan run the
        # moment an `agent:planner` card enters Triage, and the plan-gate
        # refuses it only when the card already carries `needs-human`.
        linear_ops.add_label(epic, dead_run.HOLD_LABEL)
        linear_ops.cmd_state(epic, plan_critic.BOUND_PARK_LANE)
        return
    if not dispatch_review(epic, repo, found["dispatch_reason"],
                           found["trigger_state"]):
        raise RuntimeError(
            f"the review could not be asked for ({found['dispatch_reason']}, "
            f"trigger state {found['trigger_state']!r}) — the dispatch did "
            "not go through, so no notice was posted"
        )


def report(epics, thread_reader, lane_reader, now: str | None = None,
           repo: str | None = None) -> list[str]:
    """Act on every unkept promise, at most twice per record, over the epics
    handed in.

    `thread_reader(epic)` returns `linear_ops.comment_records` rows, None when
    Linear could not say, or raises; `lane_reader(epic)` returns the lane name.
    The lane is asked FIRST so an epic in neither watched lane costs no thread
    read at all, and each epic that is reads its thread exactly once. `repo`
    is where a re-ask is dispatched: `REPO`, the sweep's own repository, whose
    epics `reconcile` hands in, unless the caller names another.

    Returns the epics a firing was COMPLETED on. The log line is not that
    list: it prints on every sweep the promise is unkept, including the sweeps
    after a firing (standards/console-honesty.md rule 1).

    A read that fails skips that epic and the sweep carries on — the same
    abstention `reconcile.epic_thread` already makes, for the same reason. A
    failed dispatch or write is collected and raised at the end, so the sweep
    goes red for the medic rather than swallowing it.
    """
    repo = repo or os.environ.get("REPO")
    spoke: list[str] = []
    failures: list[str] = []
    for epic in sorted(epics or []):
        try:
            lane = lane_reader(epic)
            if lane not in WATCHED_LANES:
                continue
            records = thread_reader(epic)
            if records is None:
                continue
            reading = read(records, epic, lane, now)
        except Exception as exc:  # noqa: BLE001 — a read never fails the sweep
            print(f"rereview-missing: could not read {epic} ({exc}) — skipped "
                  "this sweep", file=sys.stderr)
            continue
        if not reading.found:
            if reading.log:
                print(reading.log)
            continue
        print(log_line(epic, reading.found))
        if reading.spoken:
            continue
        try:
            _fire(epic, reading.found, repo)
            linear_ops.cmd_comment(epic, notice(epic, reading.found))
        except Exception as exc:  # noqa: BLE001 — collected, raised below
            failures.append(f"{epic}: {exc}")
            print(f"ERROR: rereview-missing: could not act on {epic} ({exc})",
                  file=sys.stderr)
            continue
        spoke.append(epic)
    if failures:
        raise NoticeFailed(
            "could not complete the re-review-missing firing on "
            + "; ".join(failures)
        )
    return spoke


# --- The CLI -----------------------------------------------------------------

def _lane(identifier: str) -> str | None:
    """The card's lane, straight from Linear. The same one-field query
    `reconcile.card_state` makes — spelled here rather than imported, because
    `reconcile` imports THIS module and the cycle would be real."""
    data = linear_ops.gql(
        "query($id: String!) { issue(id: $id) { state { name } } }",
        {"id": identifier},
    ) or {}
    return ((data.get("issue") or {}).get("state") or {}).get("name")


# --- The replay (DRE-4758) ----------------------------------------------------
#
# `--now` used to move the clock and nothing else, while the lane and the whole
# comment thread were still read as they stand TODAY. That is not a replay, and
# the tool said nothing about the difference: DRE-4025 replayed `quiet` at an
# instant when it was really `overdue`, because it has been in Done since
# 2026-09-21 and `read` refuses on the lane before it reaches a comment. All
# three of the proof card's replays printed `quiet` that way
# (docs/rereview-continuation-proof-2026-09.md §2b).
#
# The decision itself is untouched — these two helpers hand `read` the past and
# it applies exactly the reading the live sweep applies.

#: One page of the epic's own history. Nested under `issue`, so
#: `linear_ops.gql_paged` (which walks a TOP-LEVEL connection) cannot serve it
#: and the cursor is followed here — unfollowed, the lane would be derived from
#: the oldest hundred entries and nothing would say so (DRE-2681, nested).
_HISTORY_QUERY = """query($id: String!, $after: String) { issue(id: $id) {
     history(first: 100, after: $after) {
       nodes { createdAt fromState { name } toState { name } }
       pageInfo { hasNextPage endCursor }
     } } }"""


def _instant(iso: str | None) -> datetime | None:
    """`iso` as an aware UTC datetime, or None when it cannot be read.

    Tolerant on purpose, unlike `_minutes_since`: a stamp that cannot be placed
    in time must leave the reading unknown rather than raise out of a replay.
    """
    try:
        at = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _state_history(identifier: str) -> list[dict]:
    """Every LANE MOVE on the card, as `{"at": iso, "from": name, "to": name}`.

    Linear's history carries every change — a label, an assignee, an estimate —
    and records `fromState`/`toState` only on the ones that moved the lane, so
    an entry naming no `toState` is dropped here rather than carried into the
    derivation as a move to nowhere.
    """
    moves: list[dict] = []
    after: str | None = None
    seen: set[str] = set()
    while True:
        data = linear_ops.gql(_HISTORY_QUERY,
                              {"id": identifier, "after": after}) or {}
        history = ((data.get("issue") or {}).get("history")) or {}
        for node in history.get("nodes") or []:
            to = ((node.get("toState") or {}).get("name"))
            if not to:
                continue
            moves.append({
                "at": node.get("createdAt"),
                "from": ((node.get("fromState") or {}).get("name")),
                "to": to,
            })
        info = history.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return moves
        after = info.get("endCursor")
        if not after or after in seen:
            print(f"rereview-missing: {identifier}'s history claims another "
                  f"page with cursor {after!r} — stopping at {len(moves)} "
                  "move(s)", file=sys.stderr)
            return moves
        seen.add(after)


def lane_at(moves, now: str, current: str | None) -> str | None:
    """The lane the epic was in at `now`, off its own history. Three readings,
    in order, and each is the only honest one for its case:

      * the NEWEST move at or before `now` — its `to` is where the epic was put
        and where it stayed until the next move.
      * no move that early, so nothing had moved it yet: the OLDEST move after
        `now` records what it was moved OUT of, which is where it was. That
        move may name no `from` at all (the epic was created into its first
        lane), and then there is no lane to name — None, which `read` treats as
        unknown and says nothing about.
      * no moves at all: an epic that never moved is where it is today.

    A move Linear named no readable time for is on neither side of `now` —
    unknown is unknown (standards/console-honesty.md rule 2), and reading it as
    the newest would answer with whatever it happens to be.
    """
    when = _instant(now)
    if when is None:
        return current
    placed: list[tuple[datetime, dict]] = []
    for move in moves or []:
        stamp = _instant(move.get("at"))
        if stamp:
            placed.append((stamp, move))
    placed.sort(key=lambda pair: pair[0])
    before = [move for stamp, move in placed if stamp <= when]
    if before:
        return before[-1].get("to")
    after = [move for stamp, move in placed if stamp > when]
    if after:
        return after[0].get("from")
    return current


def at_or_before(records, now: str) -> list[dict]:
    """The thread as it stood at `now` — every comment Linear stamped later
    dropped, so a round, a tombstone or a notice that came afterwards cannot
    answer a question asked before it.

    A comment Linear gave NO stamp for is KEPT, and that is the same rule from
    the other side: nothing here knows when it was written, and dropping it
    would assert it did not exist yet. `read` already declines to do arithmetic
    on an unknown stamp, so such a round reads as unknown in a replay exactly
    as it does live.
    """
    when = _instant(now)
    if when is None:
        return list(records or [])
    kept = []
    for record in records or []:
        stamp = _instant(_created_at(record))
        if stamp is not None and stamp > when:
            continue
        kept.append(record)
    return kept


def _epics_in_flight() -> list[dict]:
    """`linear_ops.py epics-in-flight --sight`, read in process.

    Its own reader, called rather than re-queried: the states are
    `plan_critic.SIGHT_STATES` (Planning and In Progress are both in it, and
    `report` filters on the lane it is handed) and the epic test is "has
    children", and a second copy of either here would drift from the one the
    second critic's own cross-epic sight uses. It prints, so the print is
    captured.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        linear_ops.cmd_epics_in_flight(states=plan_critic.SIGHT_STATES)
    return json.loads(buf.getvalue() or "[]")


def _cmd_check(args) -> int:
    """Read one epic and say what is true. Writes nothing, exits 0 either way:
    this is a reader for a person replaying a thread, and an exit code would
    make it look like a gate.

    With `--now` every input is taken at that instant, not just the clock
    (DRE-4758) — the lane off the epic's state history, the thread trimmed to
    what existed by then. The reading is `read` either way: the point of the
    replay is to ask the LIVE sweep's question about a past moment, so a
    second decision here would be a second answer to disagree with.
    """
    records = linear_ops.comment_records(args.epic)
    lane = _lane(args.epic)
    if args.now:
        records = at_or_before(records, args.now)
        lane = lane_at(_state_history(args.epic), args.now, lane)
    reading = read(records, args.epic, lane, args.now)
    if reading.found and not reading.spoken:
        print(f"overdue: {log_line(args.epic, reading.found)} — {reading.why}")
    else:
        print(f"quiet: {args.epic} — {reading.why}")
    return 0


def _cmd_sweep(args) -> int:
    rows = _epics_in_flight()
    # A row Linear named no identifier for is nothing this can read a thread
    # for — dropped here rather than carried into `report` as a `None` epic.
    lanes = {r.get("identifier"): r.get("state")
             for r in rows if r.get("identifier")}
    spoke = report(list(lanes), linear_ops.comment_records, lanes.get, args.now)
    print(f"rereview-missing: {len(lanes)} epic(s) in flight, "
          f"spoke on {len(spoke)}" + (f" ({', '.join(spoke)})" if spoke else ""))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="is this epic's promised re-review overdue?")
    c.add_argument("epic")
    c.add_argument("--now", default=None,
                   help="replay: the lane and the thread as they stood at "
                        "this ISO instant, read against that clock")
    c.set_defaults(fn=_cmd_check)

    s = sub.add_parser("sweep", help="report over every epic in flight (POSTS)")
    s.add_argument("--now", default=None)
    s.set_defaults(fn=_cmd_sweep)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
