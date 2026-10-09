#!/usr/bin/env python3
"""The promotion gate's refusals, read back as a clock (DRE-4207).

`reconcile.promote_ready` refuses or holds a Backlog card, prints why, and —
for the refusals — posts the reason on the card ONCE (`_surface_once`). Nothing
read that refusal again, so a card refused for the same reason on sweep after
sweep for five days read, from outside, exactly like a card waiting its turn:
Backlog holds both, and the only detector was a person reading a sweep log
(DRE-4198). This module is the pure half of the fix. It never touches Linear and
imports neither `reconcile` nor `linear_ops`; the wiring card (DRE-4210) builds
the records and only calls it.

## The clock is the receipt's age

Never a count of sweeps remembered somewhere. "How long has this stood" is
answered by the oldest comment carrying the refusal's tag — `first_seen`, which
the caller reads and this module never decides. A refusal that cleared and
recurred is clocked from its first receipt, because the receipt is posted once,
ever; that is why `notice` names the TIME of the receipt rather than claiming
an unbroken duration. It is the rule `reconcile._report_epic_prose_defect`
already runs for an epic's prose defect, generalized.

## Clocked, held, and the declared waits

* `CLOCKED_TAGS` — the refusals no sweep can clear on its own. Each ends only
  when a person or a planner run acts, so one standing `STALL_MINUTES` earns a
  `🚨 promotion-stalled:` receipt on the card and a red run.
* `HELD_TAGS` — a FACT about the card after the pass, never a log line:
  `needs-human` (left in Backlog wearing the label), `agent-blocker` (left with
  an open `🛑 Agent blocked` marker), `stale-verdict`, `live-recheck`. A person
  was already told once; these are counted for the idle-board alarm and never
  clocked per card. `first_seen` is the receipt the hold left where one exists,
  and None for a label a person applied by hand.
* Everything else is a declared wait — a `blockedBy` relation, an epic not In
  Progress, the WIP budget, a PARKED card, `wave-not-green-lit`,
  `prose-blocker-no-relation` and the rest — and the caller builds no record
  for it: each already says what it waits for, and that thing is scheduled.

## Unknown age is never stale

A record whose `first_seen` is None — or is not a time this module can read —
is STANDING and never AGED. `stalled` says None for it; `idle_board` counts it
toward the line and never dates the entry by it.

`now` is always passed in: the sweep's clock is a fixture in tests.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The one Pacific renderer the sweep's receipts share. Import-safe: dead_run
# does no I/O at import and pulls in neither reconcile nor linear_ops.
import dead_run  # noqa: E402 — after the path insert, by design

#: Refusals the gate surfaces on a card and no sweep can clear on its own.
CLOCKED_TAGS = ("routing-no-verdict", "mid-epic-no-verdict",
                "plan-critic-post-unread", "plan-critic-post-sent-back",
                "plan-critic-post-died")

#: Holds counted for the idle-board alarm and never clocked per card.
HELD_TAGS = ("needs-human", "agent-blocker", "stale-verdict", "live-recheck")

STALL_TAG = "promotion-stalled"
STALL_MARK = "🚨 promotion-stalled:"

#: How long a clocked refusal may stand before its card is called stalled. Two
#: hours ≈ eight sweeps, the window `PROSE_DEFECT_RED_MINUTES` already runs.
STALL_MINUTES = int(os.environ.get("PROMOTION_STALL_MINUTES", "120"))

#: How long the oldest dated record on an idle board may stand before the run
#: goes red — past the 30-minute post-critic grace, so a fresh epic approval
#: whose second critic is still running does not turn the sweep red on the spot.
IDLE_BOARD_MINUTES = int(os.environ.get("IDLE_BOARD_MINUTES", "60"))

# The openers the wiring card and the proof card quote. Contract: change one
# and every reader of the red-run ledger has to change with it.
LEDGER_STALL_OPENER = "promotion-stalled "
LEDGER_IDLE_OPENER = "idle board — WIP 0/"
IDLE_LINE_OPENER = "promotion: idle board — WIP 0/"

_NUMBER = re.compile(r"-(\d+)$")


class Refused(NamedTuple):
    """One card the pass did not promote for a reason that is not a declared wait."""

    identifier: str
    tag: str                 # one of CLOCKED_TAGS or HELD_TAGS
    first_seen: str | None   # ISO time of the oldest receipt carrying the tag's needle; None = unknown


def _when(iso: str | None) -> datetime | None:
    """`iso` as an aware time, or None when it is absent or unreadable."""
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _age(first_seen: str | None, now: str) -> float | None:
    """Minutes from `first_seen` to `now`, or None when either is unknown."""
    then, at = _when(first_seen), _when(now)
    if then is None or at is None:
        return None
    return (at - then).total_seconds() / 60


def _hours(age_minutes: float) -> str:
    return f"{age_minutes / 60:.1f}"


def clocked(tag: str | None) -> bool:
    """True for exactly the CLOCKED_TAGS — never a held tag, a declared wait or None."""
    return tag in CLOCKED_TAGS


def stalled(refused: Refused, now: str) -> float | None:
    """The refusal's age in minutes once it has stood STALL_MINUTES, else None.

    None under the window, None for a held tag whatever its age, and None when
    `first_seen` is unknown — unknown is never stale."""
    if not clocked(refused.tag):
        return None
    age = _age(refused.first_seen, now)
    if age is None or age < STALL_MINUTES:
        return None
    return age


def notice(refused: Refused, age_minutes: float, active: int, cap: int) -> str:
    """The `🚨 promotion-stalled:` receipt body for a card `stalled` named.

    Names the receipt it is clocked from by its time rather than claiming an
    unbroken duration: a refusal that cleared and recurred is still clocked
    from its first receipt, and "first refused at" stays literally true."""
    then = _when(refused.first_seen)
    since = (f"first refused at {dead_run.pacific(then)}, {_hours(age_minutes)} hours ago"
             if then else f"refused for {_hours(age_minutes)} hours")
    return (
        f"{STALL_MARK} {refused.identifier} is still refused promotion as "
        f"{refused.tag} — {since}.\n\n"
        "This is not a card waiting its turn: the refusal holds whatever the "
        f"WIP ({active}/{cap} on this sweep), no sweep will clear it on its own, "
        "and a person must act. The refusal receipt already on this card says "
        "what fixes it."
    )


def ledger_line(refused: Refused, age_minutes: float) -> str:
    """The one-line entry the sweep appends to its red-run ledger."""
    return (
        f"{LEDGER_STALL_OPENER}{refused.identifier}: refused promotion as "
        f"{refused.tag} for {_hours(age_minutes)}h, which no sweep will clear — "
        "a person must act"
    )


def _number(identifier: str) -> tuple[float, str]:
    """Sort key for "lowest-numbered": by the card's number, then its text."""
    found = _NUMBER.search(identifier)
    return (int(found.group(1)) if found else float("inf"), identifier)


def idle_board(active: int, spent: int, cap: int, refused: list[Refused],
               now: str) -> tuple[str | None, str | None]:
    """The idle-board line and its red-run ledger entry, each or None.

    `active` is the WIP the sweep started with and `spent` what it dispatched
    this pass; the board is idle only when both are zero, so either one alone
    returns (None, None). `refused` is every record the pass built, clocked and
    held alike. The line is said whenever the board is idle and `refused` is
    non-empty; the entry only once the oldest KNOWN `first_seen` is
    IDLE_BOARD_MINUTES old — an undated record counts toward the line and never
    toward the entry."""
    if active + spent > 0 or not refused:
        return None, None
    cards = len({r.identifier for r in refused})
    noun = "card" if cards == 1 else "cards"
    lowest = min((r.identifier for r in refused), key=_number)
    line = (
        f"{IDLE_LINE_OPENER}{cap}: nothing was dispatched and {cards} {noun} in "
        f"Backlog stand refused or held, lowest {lowest} — no sweep will promote "
        "them, a person must act"
    )
    dated = [(age, r) for r in refused
             if (age := _age(r.first_seen, now)) is not None]
    if not dated:
        return line, None
    age, oldest = max(dated, key=lambda pair: pair[0])
    if age < IDLE_BOARD_MINUTES:
        return line, None
    entry = (
        f"{LEDGER_IDLE_OPENER}{cap}: {cards} {noun} in Backlog stand refused or "
        f"held with nothing dispatched; the oldest receipt, on {oldest.identifier}, "
        f"is {_hours(age)}h old — a person must act"
    )
    return line, entry
