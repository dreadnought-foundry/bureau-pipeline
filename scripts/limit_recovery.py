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
    no webhook and the planner would never start.
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
positional. Every receipt the pipeline writes opens with a glyph (🧹 ⏳ 🧠 🪦
🚨 🤖 …), and people write in letters — so a later comment whose first
character is outside ASCII is a pipeline receipt and closes the marker, while
a person's "looking at this now" does not. The recovery's own `🔁` receipt is
one, which is what stops a card being re-dispatched every pass.

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

RECOVERY_TAG = "limit-recovery"
RECOVERY_MARK = f"🔁 {RECOVERY_TAG}:"

TERMINAL_LANES = ("Done", "Canceled", "Duplicate")
PLANNING_STAGES = ("classify", "plan")
RERUN_STAGES = ("fix", "review", "sync")
PLANNING_LANE = "Planning"
BOUNCE_LANE = "Intake"   # before Planning exit: not policed, and Planning entry re-fires the planner
BUILD_LANE = "Todo"


class RecoveryFailed(RuntimeError):
    """A re-entry that did not land. Reported, never swallowed, never receipted."""


def is_receipt(body: str) -> bool:
    """True when `body` is a pipeline receipt — it opens with a glyph."""
    stripped = (body or "").lstrip()
    return bool(stripped) and ord(stripped[0]) > 127


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


def _reenter(card: dict, marker: dict, *, rerun, move, dispatch) -> str:
    """Re-enter the stage the marker names; return what was done, for the
    receipt. Raises RecoveryFailed when the write did not go through."""
    ident = card["identifier"]
    stage = marker["stage"]
    lane = _lane(card)
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
    if stage in RERUN_STAGES:
        run_id = marker.get("run") or ""
        if not run_id.isdigit():
            raise RecoveryFailed("the marker names no GitHub run id to re-run")
        if not rerun(run_id):
            raise RecoveryFailed(f"gh run rerun {run_id} --failed did not go through")
        return f"Re-ran the original run {run_id}, failed jobs only"
    raise RecoveryFailed(f"unknown stage {stage!r} in the marker")


def receipt(marker: dict, why: str, what: str) -> str:
    """The recovery's own receipt: a pipeline glyph first (it must close the
    marker), the trigger, what was done, and the run it came back from."""
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
        lops.cmd_comment(ident, receipt(marker, why, what))
        lines.append(f"{RECOVERY_TAG}: {ident} {marker['stage']} re-entered — {why}")
    return lines
