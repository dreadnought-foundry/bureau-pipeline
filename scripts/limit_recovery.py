#!/usr/bin/env python3
"""Bring a limit death back when the world changes (DRE-3171, stdlib only).

A limit death (dead_run.py, class `limit`) leaves ONE marker on the card and
moves nothing: the run hit the Claude account's usage limit or Linear's request
budget, which was never the card's fault, and retrying into the same wall is
what the medic used to do — once, immediately, spending an attempt, after which
nothing ever came back to the card. The CEO's instruction, verbatim: "Once I
release [the account] it should automatically pick that up."

This is the other half. Once per pass the reconcile sweep hands `recover()`
the board it already read; for every card whose NEWEST `🪦 limit-death:` marker
is the latest pipeline receipt on it, the stage that died is re-entered when
EITHER

  * the marker's reset time has passed, or
  * the active account differs from the one the marker recorded — the seam
    DRE-3170 fills; `None` means only the clock can trigger, and a marker that
    recorded no account cannot be "switched from", or
  * the marker is a Linear limit with no reset time at all. Linear's quota is
    hourly and its reset header is rarely in the error text; the sweep running
    this has just READ the board through that same quota, which is the
    evidence it refilled. A Claude limit with no reset time waits for the
    account switch, because nothing here has evidence about it.

## Re-entry is the stage's own front door

  * `classify` / `plan` — the card re-enters Planning. The relay's planner
    trigger is Planning ENTRY (agent-bureau `cloud/relay`: "card enters
    Planning → agent-plan"), so a card already sitting in Planning — which is
    where plan.yml's failure paths leave it — is bounced out through Intake,
    the lane before Planning exit that nothing polices, and straight back.
    Two writes, said out loud on the receipt, because a same-lane write fires
    no webhook and the planner would never start. A card in Intake, Backlog or
    Triage simply enters Planning. A card in a WORK lane is different: plan.yml's
    ACTIVATE route runs the second critic against a CEO-approved epic that is
    already In Progress, and dragging that back to Planning would undo the
    approval and fire a plan-mode run — so there the ORIGINAL run is re-run,
    which keeps its own trigger.

## Nothing waits without a clock, a switch, or a person being told

The nudge loop leaves a limit-parked card alone, which is right while a
trigger can still fire and wrong forever if none can. A marker with no reset
time and no recorded account (an API `rate_limit_error` carries no `resets …`;
so does a reset in a zone the parser does not read), or a PR-stage marker
naming no run to re-run, gets ONE `⚠️ limit-recovery:` receipt saying what a
person does. That receipt opens with a glyph, so it CLOSES the marker: the card
is back on the sweep's ordinary clock rather than hidden from it, and the sweep
is not red on every pass for a card nobody was told about.
  * `build` — In Progress → Todo, the sweep's own requeue move, which the relay
    dispatches. A card already in Todo (DRE-3062 died at `Card → In Progress`
    and never left it) gets the sweep's own re-dispatch instead.
  * `fix` / `review` / `sync` — the ORIGINAL GitHub run the marker names is
    re-run, failed jobs only. Never a workflow_dispatch: the rerun keeps the
    run's own event, PR and head, which is what the fix loop, the critic and
    linear-sync all read.

## The writes are injected, and why

The lane contract (DRE-2859, `ready_lane_writers.py`) attributes a lane write
to the ACTOR that runs the module, and the actor here is the sweep:
`reconcile.py` is a declared writer of Todo, this module is not and should not
be. So `move`, `dispatch` and `rerun` arrive from the caller — the sweep passes
`linear_ops.cmd_state`, its own `redispatch` and `gh run rerun` — and the only
seam this module touches itself is the comment writer, for the receipt. The
same shape makes the whole decision testable without Linear or GitHub.

## What a "newer receipt" is

The board read carries comment bodies in order and no timestamps, so newer is
positional. A later comment closes the marker when the PIPELINE wrote it, and
without an authorship read (one request per card, against the very quota this
module exists to spare) that is read off the body itself, two ways: the
`📎 pipeline-act:` trailer every composed act ends with, or an opening glyph
from RECEIPT_GLYPHS — the finite set the pipeline's card writers open with
(🧹 the sweep, 🧠 ⏳ the run's heartbeats, 🤖 the report step, 🪦 🚨 🛑 🙋 the
dead-run, hold, blocker and escalation receipts, and this module's own 🔁 and
⚠️). It used to be "any non-ASCII first character", and the critic on #279
showed what that does: a person's "👍 approved, go ahead" read as the
pipeline's own receipt, closed the marker, and the sweep silently stopped
bringing the card back — the stall this module exists to end. So the set is
enumerated as data, a glyph people reach for (👍 🎉 👀 ✅ ❌ 🙏) is not in it, and
a receipt the pipeline opens with a glyph not listed here simply does not close
the marker — the safe direction, because the recovery's own receipt always does.

Bounded by `wip_room` per pass; skips terminal cards (Done / Canceled /
Duplicate) and cards held for a human (`needs-human`), which the sweep leaves
alone everywhere else too. A write that does not land is reported on an
`ERROR:` line — the sweep adds it to its write ledger and goes red, like every
other write — and no receipt is posted for it, so the next pass retries.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dead_run  # noqa: E402 — the marker's one definition
import pipeline_act  # noqa: E402 — the trailer is the one claim of pipeline authorship

RECOVERY_TAG = "limit-recovery"
RECOVERY_MARK = f"🔁 {RECOVERY_TAG}:"
# The hand-off: one receipt, a glyph first so it closes the marker.
HANDOFF_MARK = f"⚠️ {RECOVERY_TAG}:"

TERMINAL_LANES = ("Done", "Canceled", "Duplicate")
PLANNING_STAGES = ("classify", "plan")
RERUN_STAGES = ("fix", "review", "sync")
PLANNING_LANE = "Planning"
BOUNCE_LANE = "Intake"   # before Planning exit: not policed, and Planning entry re-fires the planner
# The lanes a planning-stage card may ENTER Planning from. Anything else is a
# card past Planning exit (Green Light, Todo, In Progress, In Review), and a
# plan death there is re-run in place — never re-planned.
REPLAN_FROM = ("Intake", "Backlog", "Triage")
BUILD_LANE = "Todo"


class RecoveryFailed(RuntimeError):
    """A re-entry that did not land. Reported, never swallowed, never receipted."""


# The glyphs the pipeline's card writers open a receipt with — the finite
# set, as data (see the module docstring for why "any non-ASCII character"
# was the wrong proxy). Kept to the writers a limit-parked card can actually
# hear from: the sweep, the run itself, the report step, the medic and the
# dead-run/hold/blocker/escalation receipts. ✅ and ❌ are deliberately absent:
# people reach for them, and the one pipeline ✅ (card-done) lands on a card
# that is already Done and skipped here.
RECEIPT_GLYPHS = (
    "🧹", "🧠", "⏳", "🤖", "🪦", "🚨", "🛑", "🙋", "🧯", "🔌", "♻️", "🩺",
    "🔧", "🔀", "⚡", "🔓", "🚫", "🏁",
    RECOVERY_MARK[:1], HANDOFF_MARK[:2],
)


def is_receipt(body: str) -> bool:
    """True when `body` is something the PIPELINE wrote on the card: it
    carries the act trailer, or opens with one of RECEIPT_GLYPHS."""
    text = body or ""
    if pipeline_act.read_trailer(text) is not None:
        return True
    stripped = text.lstrip()
    return any(stripped.startswith(glyph) for glyph in RECEIPT_GLYPHS)


def waiting(bodies) -> dict | None:
    """The newest limit-death marker on the card, or None when a later
    pipeline receipt has superseded it (or there never was one)."""
    newest = None
    for body in bodies or []:
        parsed = dead_run.parse_limit_marker(body)
        if parsed is not None:
            newest = parsed
        elif is_receipt(body):
            newest = None
    return newest


def trigger(marker: dict, now: datetime, active_account: str | None) -> str | None:
    """Why the card may come back now — one plain sentence — or None."""
    recorded = marker.get("account")
    if recorded and active_account and recorded != active_account:
        return f"account switched {recorded} → {active_account}"
    reset = marker.get("reset")
    if reset is not None:
        return f"window reset at {dead_run.pacific(reset)}" if now >= reset else None
    if marker.get("kind") == "linear":
        return ("Linear's quota answered this sweep's own board read, so it has "
                "refilled")
    return None


def _until(marker: dict) -> str:
    reset = marker.get("reset")
    if reset is not None:
        return f"until {dead_run.pacific(reset)}"
    if marker.get("account"):
        return f"until the account switches away from {marker['account']}"
    return "for an account switch, and the marker recorded no account"


def _bodies(card: dict) -> list[str]:
    return [n.get("body") or "" for n in (card.get("comments") or {}).get("nodes", [])]


def _held(card: dict) -> bool:
    return any(
        (lbl.get("name") or "").lower() == dead_run.HOLD_LABEL
        for lbl in (card.get("labels") or {}).get("nodes", [])
    )


def _lane(card: dict) -> str:
    return (card.get("state") or {}).get("name") or ""


def _needs_rerun(card: dict, marker: dict) -> bool:
    """Whether re-entry means re-running the original run: every PR stage,
    and a planning stage whose card is already past Planning exit."""
    stage = marker.get("stage")
    if stage in RERUN_STAGES:
        return True
    return stage in PLANNING_STAGES and _lane(card) not in (PLANNING_LANE, *REPLAN_FROM)


def handoff_reason(card: dict, marker: dict) -> str | None:
    """Why nothing here can ever bring this card back — the ONE sentence a
    person is told — or None when a trigger and a re-entry both exist."""
    stage = marker.get("stage")
    if stage not in PLANNING_STAGES and stage != "build" and stage not in RERUN_STAGES:
        return f"the marker names a stage this sweep does not know ({stage!r})"
    if _needs_rerun(card, marker) and not (marker.get("run") or "").isdigit():
        return (f"the marker names no GitHub run to re-run, so the failed {stage} "
                f"run has to be re-run by hand (Actions → Re-run failed jobs), or "
                f"the branch pushed to start it fresh")
    if (marker.get("reset") is None and marker.get("kind") != "linear"
            and not marker.get("account")):
        return ("the marker names no reset time and no account, so nothing here "
                "can tell when the wall comes down. The card is back on the "
                "sweep's ordinary clock; if the account is still limited, park it "
                "in Backlog until the account is released, then return it to Todo")
    return None


def handoff_receipt(marker: dict, reason: str) -> str:
    return (
        f"{HANDOFF_MARK} cannot bring this card back on its own — {reason}. "
        f"(Limit death: {marker.get('kind')} limit in the {marker.get('stage')} "
        f"stage, run {marker.get('run') or 'unknown'}.)"
    )


def _reenter(card: dict, marker: dict, *, rerun, move, dispatch) -> str:
    """Re-enter the stage the marker names; return what was done, for the
    receipt. Raises RecoveryFailed when the write did not go through."""
    ident = card["identifier"]
    stage = marker["stage"]
    lane = _lane(card)
    if _needs_rerun(card, marker):
        run_id = marker.get("run") or ""
        if not run_id.isdigit():  # handoff_reason() answers this first; belt and braces
            raise RecoveryFailed("the marker names no GitHub run id to re-run")
        if not rerun(run_id):
            raise RecoveryFailed(f"gh run rerun {run_id} --failed did not go through")
        kept = (" in place, because a card past Planning exit is never re-planned"
                if stage in PLANNING_STAGES else "")
        return f"Re-ran the original run {run_id}, failed jobs only{kept}"
    if stage in PLANNING_STAGES:
        if lane == PLANNING_LANE:
            move(ident, BOUNCE_LANE)
            move(ident, PLANNING_LANE)
            return (f"Bounced {PLANNING_LANE} → {BOUNCE_LANE} → {PLANNING_LANE}, "
                    f"because entering {PLANNING_LANE} is what starts the planner")
        move(ident, PLANNING_LANE)
        return f"Moved {lane} → {PLANNING_LANE}, which starts the planner"
    if stage == "build":
        if lane == BUILD_LANE:
            if not dispatch(card):
                raise RecoveryFailed("the re-dispatch did not go through")
            return f"Re-dispatched from {BUILD_LANE} (a same-lane write fires no webhook)"
        move(ident, BUILD_LANE)
        return f"Moved {lane} → {BUILD_LANE}, which dispatches a fresh run"
    raise RecoveryFailed(f"unknown stage {stage!r} in the marker")


def recovery_receipt(marker: dict, why: str, what: str) -> str:
    """The recovery's own receipt: a pipeline glyph first (it must close the
    marker), the trigger, what was done, and the run it came back from.

    Not composed through pipeline_act.receipt() YET: a registry row is a
    console-first change (DRE-3091), so this and the hand-off below are
    declared in config/pipeline-acts.json's `unconverted` block until the
    console knows the act — never named `receipt`, so the receipt guard
    cannot mistake it for the one writer."""
    return (
        f"{RECOVERY_MARK} re-entered {marker['stage']} — {why}. {what}. The "
        f"limit that stopped run {marker.get('run') or 'unknown'} is no longer "
        f"in the way, so this is the same work carrying on, not a new attempt."
    )


def recover(lops, now: datetime, active_account: str | None, wip_room: int, *,
            rerun, move, dispatch, cards) -> list[str]:
    """One pass. Returns the lines the sweep prints; `ERROR:` lines are the
    writes that did not land, which the sweep adds to its ledger.

    `lops` posts the receipt (`cmd_comment`). `rerun(run_id) -> bool`,
    `move(identifier, lane)` and `dispatch(card) -> bool` are the sweep's own
    writes, injected — see the module docstring for why.
    """
    lines: list[str] = []
    room = wip_room
    for card in cards or []:
        ident = card["identifier"]
        if _lane(card) in TERMINAL_LANES or _held(card):
            continue
        marker = waiting(_bodies(card))
        if marker is None:
            continue
        reason = handoff_reason(card, marker)
        if reason is not None:
            # Told once, and the receipt closes the marker — no WIP spent, no
            # ERROR line, and the card is the sweep's again next pass.
            lops.cmd_comment(ident, handoff_receipt(marker, reason))
            lines.append(f"{RECOVERY_TAG}: {ident} handed to a human — {reason.split('.')[0]}")
            continue
        why = trigger(marker, now, active_account)
        if why is None:
            lines.append(f"{RECOVERY_TAG}: {ident} is waiting {_until(marker)} "
                         f"({marker['kind']} limit, {marker['stage']} stage)")
            continue
        if room <= 0:
            lines.append(f"{RECOVERY_TAG}: {ident} is ready ({why}) but the WIP room "
                         f"is spent this pass — next sweep")
            continue
        try:
            what = _reenter(card, marker, rerun=rerun, move=move, dispatch=dispatch)
        except Exception as exc:  # noqa: BLE001 — one card's failure never costs the next its turn
            lines.append(f"ERROR: {RECOVERY_TAG} {ident}: {marker['stage']} re-entry "
                         f"did not land: {exc}")
            continue
        room -= 1
        lops.cmd_comment(ident, recovery_receipt(marker, why, what))
        lines.append(f"{RECOVERY_TAG}: {ident} {marker['stage']} re-entered — {why}")
    return lines
