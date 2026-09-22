#!/usr/bin/env python3
"""The post-approval send-back whose promised re-review never ran (DRE-4492).

The failure this module detects is SILENCE. An epic carries a
`plan-critic: stage=post … result=SEND_BACK` record, a 🔁 receipt saying the
review is being run again by the pipeline, on its own — and then nothing. No
round-2 record, no tombstone, the children held in Backlog under
`plan_critic.POST_SENT_BACK_TAG`, and nothing on any board saying the promise
was not kept. DRE-4025 sat that way for 33 hours, DRE-3964 for 12, DRE-4083
for 3.5, DRE-4425 for 8h43m, DRE-4396 for 21 and counting; every one of them
was found by a person reading a sweep log and recovered by hand with
`review_rerun.RERUN_REVIEW_ACT`.

One race that produced it is closed (DRE-4573 / PR #476: the continuation was
skipped as its own parent's duplicate). This is the detector that speaks when
any version of that race, or anything else at all, leaves the promise unkept —
because the thing being watched for is the absence of a record, and an absence
has no cause written on it.

WHAT IT DOES NOT OWN. Every credential this reads is `plan_critic`'s, imported
rather than re-spelled, so the sweep's gate and this detector can never
disagree about one epic:

  * `current_cycle_entries` — which comments belong to the planning attempt in
    front of us, keeping each comment's record so the round's own `created_at`
    survives the scoping (a re-plan opens a fresh window).
  * `trusted_bodies` / `parse_markers` — a round counts only when the pipeline
    wrote it AND the comment says nothing else. A forged SEND_BACK cannot
    trigger the notice and a forged round 2 cannot silence it.
  * `post_release` — POST_HELD is the state this watches; POST_DIED is a
    tombstone and somebody else's business.
  * `post_bound_reached` — the bound parks loudly with `needs-human`, which is
    the opposite of silence.
  * `REAPPROVE_HOW` — the one sentence every refusal ends with, so the act the
    notice names is the act the relay honours.

CLI:
  check <EPIC> [--now <ISO>]   read the live thread and lane, say `overdue …`
                               or `quiet …` with the reason. Writes nothing,
                               exits 0 either way. `--now` replays a real
                               thread as it stood at a moment.
  sweep [--now <ISO>]          run `report` over `linear_ops.py epics-in-flight`
                               for an operator. This POSTS.

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

import linear_ops  # noqa: E402
import plan_critic  # noqa: E402

#: The first word of the notice, and its idempotency key. In the
#: `dead_run.DEAD_TAG` shape every other refusal on an epic uses, and its own
#: tag rather than one of `plan_critic`'s three: "the critic found a gap" and
#: "the review that was promised never ran" are different facts with different
#: next actions, and one tag would let the first silence the second forever.
REREVIEW_MISSING_TAG = "plan-critic-rereview-missing"

#: How long the promise has to be unkept before the notice is POSTED.
#:
#: Sized from what the promise actually costs when it is kept: the continuation
#: queues behind its parent for seconds, then the review runs at up to
#: `plan_critic.POST_REVIEW_TURNS_CAP` turns at the ~9 s/turn measured on
#: DRE-3164 — about twenty minutes with setup. A retry after a death leaves a
#: tombstone FIRST, and a tombstone is `plan_critic.POST_DIED`'s business, so
#: that path never needs to fit inside this window. Forty-five minutes is
#: comfortably longer than the kept promise and short enough that the shortest
#: incident this was built for (DRE-4083, 3.5 hours) is named on its epic
#: within four sweeps.
#:
#: Beside `reconcile.POST_CRITIC_GRACE_MINUTES` in spirit, including the reason
#: it exists at all: this is a decision about when to SPEAK, never about what is
#: true. The log line prints on every sweep from the first one
#: (standards/console-honesty.md rule 1). It has a default, so it is NOT in
#: `reconcile.REQUIRED_ENV` and no workflow step declares it.
REREVIEW_GRACE_MINUTES = int(os.environ.get("REREVIEW_GRACE_MINUTES", "45"))


class NoticeFailed(Exception):
    """One or more notices could not be posted.

    Raised at the END of `report`, after every other epic has had its turn: a
    Linear write that failed must take the sweep red for the medic (DRE-1254 —
    never exit 0 on a write we claimed to make and did not), but one epic's
    failed POST must not cost the others their reading.
    """


class Reading(NamedTuple):
    """What `read` saw on one epic's thread.

    `found` is the overdue round REGARDLESS of whether the notice has been
    posted, and `spoken` says whether it has. The two are separate because the
    console-honesty rule and the idempotency rule pull in opposite directions:
    the log line speaks on every sweep, the comment once per round.
    """

    found: dict | None
    spoken: bool
    why: str


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


def _last_post_round(entries) -> tuple[int, dict, str | None] | None:
    """`(index, row, created_at)` for the NEWEST post-stage round record.

    One entry at a time through `plan_critic.parse_markers`, which is the
    credential in one call: `trusted_bodies` keeps a bystander's comment out
    and `_sole_record` keeps the pipeline's own prose out, so at most one round
    can ever come from one comment. The index is what the idempotency scan
    below counts from — a notice is only this round's if it came after it.
    """
    last = None
    for index, entry in enumerate(entries):
        rows = plan_critic.parse_markers([entry])
        if rows and rows[0]["stage"] == plan_critic.STAGE_POST:
            last = (index, rows[0], _created_at(entry))
    return last


def _already_spoken(entries, after: int) -> bool:
    """Has this pipeline already said the promise was unkept, for THIS round?

    Only the pipeline's own comments count, for the same reason every other
    record here is scoped that way: anyone with comment access on the epic
    could otherwise post the tag once and silence the detector for the life of
    the round.
    """
    for entry in entries[after + 1:]:
        for body in plan_critic.trusted_bodies([entry]):
            if body.strip().startswith(f"🚨 {REREVIEW_MISSING_TAG}:"):
                return True
    return False


def read(records, epic: str, lane: str | None, now: str | None = None,
         grace_minutes: int | None = None) -> Reading:
    """What this epic's thread says about the promise, in one pass.

    Every refusal below is somebody else's business, and each one is read off
    `plan_critic` rather than re-derived:

      * a lane that is not `plan_critic.APPROVAL_LANE`. In Green Light the plan
        is in the CEO's queue and the existing refusals already speak; anywhere
        else there is no approved plan to be re-reviewed. An UNREADABLE lane is
        unknown, and unknown says nothing.
      * anything but `POST_HELD`. POST_RELEASED is the plan passed, POST_NOT_RUN
        is `plan_critic.POST_UNREAD_TAG`'s incident, and POST_DIED is a
        tombstone — a review that died left a record, which is not silence.
      * the bound reached. That parks the epic in Green Light with
        `needs-human` and both findings, loudly, which is the opposite of the
        failure this watches for.
      * a round Linear gave no `created_at` for. There is no arithmetic to do
        on an unknown stamp, and the whole content of the notice is how long it
        has been (standards/console-honesty.md rule 2).
    """
    grace = REREVIEW_GRACE_MINUTES if grace_minutes is None else grace_minutes
    if lane != plan_critic.APPROVAL_LANE:
        return Reading(None, False, (
            f"the epic is in {lane or 'a lane Linear did not name'}, not "
            f"{plan_critic.APPROVAL_LANE} — nothing here is waiting on a "
            "re-review"
        ))
    state, detail = plan_critic.post_release(records, epic)
    if state != plan_critic.POST_HELD:
        return Reading(None, False, (
            f"the second critic's state on this plan is `{state}`, not "
            f"`{plan_critic.POST_HELD}` — no send-back is waiting on a "
            "re-review"
        ))
    if plan_critic.post_bound_reached(records, epic):
        return Reading(None, False, (
            "the plan is at the bound and parked with needs-human — a park "
            "says so on the card, which is not the silence this watches for"
        ))
    entries = plan_critic.current_cycle_entries(records, epic)
    last = _last_post_round(entries)
    if last is None:  # pragma: no cover — POST_HELD cannot be reached without one
        return Reading(None, False, "no post-approval round on this plan")
    index, row, created = last
    if not created:
        return Reading(None, False, (
            f"round {row['round']} was sent back, but Linear named no time for "
            "the comment that recorded it — how long it has been is unknown"
        ))
    try:
        minutes = _minutes_since(created, now)
    except ValueError:
        # A stamp Linear gave us and we cannot read is unknown, exactly as
        # `plan_critic.promotion_refusal` reads an unparseable green light.
        return Reading(None, False, (
            f"round {row['round']} was sent back at {created!r}, which is not "
            "a time this can read — how long it has been is unknown"
        ))
    reason = plan_critic.one_line(detail) or "no reason recorded"
    if minutes < grace:
        return Reading(None, False, (
            f"round {row['round']} was sent back {int(minutes)} min ago and "
            f"the review it promised has {grace} minutes to run"
        ))
    found = {
        "round": row["round"],
        "sent_back_at": created,
        "minutes": minutes,
        "reason": reason,
    }
    spoken = _already_spoken(entries, index)
    return Reading(found, spoken, (
        f"round {row['round']} was sent back {int(minutes)} min ago, there is "
        f"no round {row['round'] + 1} and no tombstone, and the critic's "
        f"reason was: {reason}"
    ) + (" — already said once for this round" if spoken else ""))


def overdue(records, epic: str, lane: str | None, now: str | None = None,
            grace_minutes: int | None = None) -> dict | None:
    """The round whose promised re-review is overdue AND unsaid, or None.

    `{"round": n, "sent_back_at": iso, "minutes": float, "reason": str}`. The
    once-per-round idempotency is part of the answer here: this is the question
    "is there a notice to post", and a later round record starts a fresh
    window by opening a fresh index for `_already_spoken` to count from.
    `read` is the same reading with the two halves kept apart, for the log line
    that prints either way.
    """
    reading = read(records, epic, lane, now, grace_minutes)
    return reading.found if (reading.found and not reading.spoken) else None


# --- What gets said ----------------------------------------------------------

def log_line(epic: str, found: dict) -> str:
    """The one line the sweep prints on EVERY sweep the promise is unkept."""
    return (
        f"rereview-missing: {epic} round {found['round']} sent back "
        f"{int(found['minutes'])} min ago — no round {found['round'] + 1} and "
        "no tombstone"
    )


def notice(epic: str, found: dict) -> str:
    """The CEO-facing comment, posted once per round.

    It opens with 🚨, which `reconcile._AGENT_COMMENT_PREFIXES` already reads as
    a machine comment, so the blocker gate is not confused into thinking a
    person spoke.

    It ends with `plan_critic.REAPPROVE_HOW` — the sentence every refusal ends
    with, so the act named here is the act the relay honours. Because that
    sentence embeds `review_rerun.RERUN_REVIEW_ACT` in prose, no line of this
    notice IS the act (DRE-3286: the relay matches the whole comment body, so a
    notice quoting it alone would re-run the review every time it posted).
    """
    minutes = int(found["minutes"])
    nxt = found["round"] + 1
    return (
        f"🚨 {REREVIEW_MISSING_TAG}: {epic}'s plan was sent back by the "
        f"post-approval critic at {_utc(found['sent_back_at'])} "
        f"(round {found['round']}) and the review the pipeline promised has "
        f"not run in the {minutes} minutes since — nothing is scheduled and "
        "the children are held.\n\n"
        f"The critic's reason: {found['reason']}\n\n"
        "The 🔁 receipt above this says the review is being run again by the "
        "pipeline, on its own. **It was not.** There is no round "
        f"{nxt} on this plan and no record of a review that died trying, so "
        "nothing is coming: the children stay in Backlog until somebody asks "
        "for the review.\n\n"
        f"**To run it:** {plan_critic.REAPPROVE_HOW}."
    )


# --- The sweep's half --------------------------------------------------------

def report(epics, thread_reader, lane_reader, now: str | None = None) -> list[str]:
    """Say what is overdue, once per round, over the epics handed in.

    `thread_reader(epic)` returns `linear_ops.comment_records` rows, None when
    Linear could not say, or raises; `lane_reader(epic)` returns the lane name.
    The lane is asked FIRST so an epic that is not In Progress costs no thread
    read at all, and each epic that is reads its thread exactly once.

    Returns the epics a notice was POSTED on. The log line is not that list: it
    prints on every sweep the promise is unkept, including the sweeps after the
    notice has already been posted (standards/console-honesty.md rule 1 — the
    console speaks from the first sweep, the comment waits for the window).

    A read that fails skips that epic and the sweep carries on — the same
    abstention `reconcile.epic_thread` already makes, for the same reason. A
    failed POST is collected and raised at the end, so the sweep goes red for
    the medic rather than swallowing it.
    """
    spoke: list[str] = []
    failures: list[str] = []
    for epic in sorted(epics or []):
        try:
            lane = lane_reader(epic)
            if lane != plan_critic.APPROVAL_LANE:
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
            continue
        print(log_line(epic, reading.found))
        if reading.spoken:
            continue
        try:
            linear_ops.cmd_comment(epic, notice(epic, reading.found))
        except Exception as exc:  # noqa: BLE001 — collected, raised below
            failures.append(f"{epic}: {exc}")
            print(f"ERROR: rereview-missing: could not post on {epic} ({exc})",
                  file=sys.stderr)
            continue
        spoke.append(epic)
    if failures:
        raise NoticeFailed(
            "could not post the re-review-missing notice on "
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


def _epics_in_flight() -> list[dict]:
    """`linear_ops.py epics-in-flight`, read in process.

    Its own reader, called rather than re-queried: the states are
    `plan_critic.IN_FLIGHT_EPIC_STATES` and the epic test is "has children",
    and a second copy of either here would drift from the one the post-approval
    critic's own cross-epic sight uses. It prints, so the print is captured.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        linear_ops.cmd_epics_in_flight()
    return json.loads(buf.getvalue() or "[]")


def _cmd_check(args) -> int:
    """Read one epic and say what is true. Writes nothing, exits 0 either way:
    this is a reader for a person replaying a thread, and an exit code would
    make it look like a gate."""
    reading = read(linear_ops.comment_records(args.epic), args.epic,
                   _lane(args.epic), args.now)
    if reading.found and not reading.spoken:
        print(f"overdue: {log_line(args.epic, reading.found)} — {reading.why}")
    else:
        print(f"quiet: {args.epic} — {reading.why}")
    return 0


def _cmd_sweep(args) -> int:
    rows = _epics_in_flight()
    lanes = {r.get("identifier"): r.get("state") for r in rows}
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
                   help="replay the thread as it stood at this ISO instant")
    c.set_defaults(fn=_cmd_check)

    s = sub.add_parser("sweep", help="report over every epic in flight (POSTS)")
    s.add_argument("--now", default=None)
    s.set_defaults(fn=_cmd_sweep)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
