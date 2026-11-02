#!/usr/bin/env python3
"""The promotion gate's refusals, read back as a clock (DRE-4207).

`reconcile.promote_ready` refuses or holds a Queued card, prints why, and —
for the refusals — posts the reason on the card ONCE (`_surface_once`). Nothing
read that refusal again, so a card refused for the same reason on sweep after
sweep for five days read, from outside, exactly like a card waiting its turn:
the queue holds both, and the only detector was a person reading a sweep log
(DRE-4198). This module is the pure half of the fix. It never touches Linear and
imports neither `reconcile` nor `linear_ops`; the wiring card (DRE-4210) builds
the records and only calls it. Its lines name the queue as `lane_contract`
writes it (`QUEUE_LANE`, DRE-6749), never a lane literal.

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
  `🚨 promotion-stalled:` receipt on the card and a red run. The epic cap's
  hold (`epic-cap-undecided`, DRE-6618) is one: the sweep asks the cap itself
  only for an epic a blocker epic released, so the hold on any other epic's
  children stands until a person asks again, and the record names the epic.
* `HELD_TAGS` — a FACT about the card after the pass, never a log line:
  `needs-human` (left in Queued wearing the label), `agent-blocker` (left with
  an open `🛑 Agent blocked` marker), `stale-verdict`, `live-recheck`. A person
  was already told once; these are counted for the idle-board alarm and never
  clocked per card. `first_seen` is the receipt the hold left where one exists,
  and None for a label a person applied by hand.
* Everything else is a declared wait — a `blockedBy` relation, the WIP
  budget, a PARKED card, `wave-not-green-lit`, `prose-blocker-no-relation` and
  the rest — and the caller builds no record for it: each already says what
  it waits for, and that thing is scheduled.

## An epic that is not In Progress (DRE-8815)

`epic-not-active` is a declared wait, with one exception, and the exception
is clocked. On 2026-11-01 epic DRE-8698's plan run died in a shell step after
posting the plan, nothing moved the epic on, and its nine children waited
five hours behind their ordinary `🧾 promotion-wait: epic-not-active —`
receipts with nothing else said. So the full sweep builds ONE `EpicWait` per
epic in Planning — never one per child — and the record is clocked only when
the epic's newest plan run is dead or was never found (`run_dead`). Every
other lane is never clocked: Green Light is the CEO's approval and the epic
cap's queue, Triage the operator's, and the rest owe nothing of the planner.
No child is clocked under the tag (`EPIC_CLOCKED_TAGS`, never
`CLOCKED_TAGS`); the children are counted into the epic's record.

The clock is the later of two recorded times: the oldest child receipt and
the newest plan run's `updatedAt` (`epic_first_seen`), so the medic's one
re-run restarts it from the attempt's end. No child receipt, no date —
unknown is never stale, as below.

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
# The queue's names (DRE-6749). Import-safe too: it reads the contract file and
# nothing else.
import lane_contract  # noqa: E402

#: `promotion_wait.EPIC_NOT_ACTIVE`, spelled for the reason below;
#: `tests/test_epic_not_active_clock.py` holds the two the same.
EPIC_NOT_ACTIVE_TAG = "epic-not-active"

#: Refusals the gate surfaces on a card and no sweep can clear on its own.
#: The last is `epic_cap.UNDECIDED_TAG`, spelled because this module imports
#: neither `reconcile` nor `linear_ops`, and `epic_cap` imports the second;
#: `tests/test_promotion_stall.py` holds the two the same.
CLOCKED_TAGS = ("routing-no-verdict", "mid-epic-no-verdict",
                "plan-critic-post-unread", "plan-critic-post-sent-back",
                "plan-critic-post-died", "epic-cap-undecided")

#: Clocked only as an epic's record, under the rule in the docstring, and
#: kept out of CLOCKED_TAGS so `clocked()` never clocks a child under it.
EPIC_CLOCKED_TAGS = (EPIC_NOT_ACTIVE_TAG,)

#: The one lane an epic its children wait on is clocked in.
EPIC_CLOCKED_LANE = "Planning"
#: How many of the plan stub's newest runs are searched for the epic's.
PLAN_RUNS_PAGE = 50
#: A finished plan run that says Planning is working, not dead.
LIVE_CONCLUSIONS = ("success", "neutral", "skipped")

#: Holds counted for the idle-board alarm and never clocked per card. The first
#: is the hold label itself, named through its one definition: this tuple reads
#: the label and applies nothing, and a literal here would read to
#: `hold.py check` as a card created already held.
HELD_TAGS = (dead_run.HOLD_LABEL, "agent-blocker", "stale-verdict", "live-recheck")

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
    epic: str | None = None  # the epic the card waits on, where the refusal is the epic's (DRE-6618)


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


def _for_epic(refused: Refused) -> str:
    """` for its epic DRE-N`, or nothing for a record that names no epic."""
    return f" for its epic {refused.epic}" if refused.epic else ""


def notice(refused: Refused, age_minutes: float, active: int, cap: int) -> str:
    """The `🚨 promotion-stalled:` receipt body for a card `stalled` named.

    Names the receipt it is clocked from by its time rather than claiming an
    unbroken duration: a refusal that cleared and recurred is still clocked
    from its first receipt, and "first refused at" stays literally true. A
    record carrying an epic names it: that refusal is lifted on the epic."""
    then = _when(refused.first_seen)
    since = (f"first refused at {dead_run.pacific(then)}, {_hours(age_minutes)} hours ago"
             if then else f"refused for {_hours(age_minutes)} hours")
    return (
        f"{STALL_MARK} {refused.identifier} is still refused promotion as "
        f"{refused.tag}{_for_epic(refused)} — {since}.\n\n"
        "This is not a card waiting its turn: the refusal holds whatever the "
        f"WIP ({active}/{cap} on this sweep), no sweep will clear it on its own, "
        "and a person must act. The refusal receipt already on this card says "
        "what fixes it."
    )


def ledger_line(refused: Refused, age_minutes: float) -> str:
    """The one-line entry the sweep appends to its red-run ledger."""
    return (
        f"{LEDGER_STALL_OPENER}{refused.identifier}: refused promotion as "
        f"{refused.tag}{_for_epic(refused)} for {_hours(age_minutes)}h, which no "
        "sweep will clear — "
        "a person must act"
    )


class PlanRun(NamedTuple):
    """The newest plan run whose jobs name the epic, as `gh run list` read it."""

    id: str
    status: str
    conclusion: str
    updated_at: str | None   # when GitHub last touched the run — its end, once completed


class EpicWait(NamedTuple):
    """One epic its Queued children wait on, and what the record reads (DRE-8815)."""

    epic: str
    lane: str
    children: int            # its children standing in the queue, every repo's
    receipt_at: str | None   # the oldest `epic-not-active` wait receipt among them; None = unknown
    run: PlanRun | None      # None = no plan run among the newest PLAN_RUNS_PAGE names it


def oldest(times) -> str | None:
    """The earliest readable time among `times`, as written, or None."""
    dated = [(at, iso) for iso in times if (at := _when(iso)) is not None]
    return min(dated)[1] if dated else None


def run_dead(run: PlanRun | None) -> bool:
    """Is the epic's newest plan run dead, or absent? A run still going — any
    status but `completed`, the in-flight ones and any GitHub adds — is not:
    its own timeout ends it. Neither is one that finished well."""
    if run is None:
        return True
    if run.status != "completed":
        return False
    return run.conclusion not in LIVE_CONCLUSIONS


def epic_first_seen(wait: EpicWait) -> str | None:
    """The record's date: the later of the oldest child receipt and the run's
    end. None when no child carries the receipt — undated, never stale."""
    receipt = _when(wait.receipt_at)
    if receipt is None:
        return None
    ended = _when(wait.run.updated_at) if wait.run is not None else None
    return wait.run.updated_at if ended is not None and ended > receipt else wait.receipt_at


def receipt_aged(wait: EpicWait, now: str) -> bool:
    """The free gate, asked before anything is read for the epic: in Planning,
    and its oldest child receipt has stood STALL_MINUTES. The record's own date
    is never earlier, so an epic this answers False for cannot be stale."""
    if wait.lane != EPIC_CLOCKED_LANE:
        return False
    age = _age(wait.receipt_at, now)
    return age is not None and age >= STALL_MINUTES


def epic_stalled(wait: EpicWait, now: str) -> float | None:
    """The record's age in minutes once it has stood STALL_MINUTES, else None.
    None out of Planning, for a plan run that is not dead, and undated."""
    if wait.lane != EPIC_CLOCKED_LANE or not run_dead(wait.run):
        return None
    age = _age(epic_first_seen(wait), now)
    if age is None or age < STALL_MINUTES:
        return None
    return age


def epic_once_key(wait: EpicWait) -> str:
    """The receipt's once-only key, the head of its first line: the epic and
    the run, so a later dead run on the same epic is said again."""
    token = f"run {wait.run.id}" if wait.run is not None else "no-run"
    return f"{STALL_MARK} {wait.epic} [{token}]"


def _children(count: int) -> str:
    return f"{count} child" if count == 1 else f"{count} children"


def _waits(count: int) -> str:
    return "waits" if count == 1 else "wait"


def epic_notice(wait: EpicWait, now: str) -> str:
    """The `🚨 promotion-stalled:` receipt body posted on the EPIC."""
    kids = (f"{_children(wait.children)} {_waits(wait.children)} on it in "
            f"{lane_contract.QUEUE_LANE}")
    run = wait.run
    if run is None:
        head = f"has stood in {wait.lane} with no plan run found — {kids}."
        receipt = _when(wait.receipt_at)
        since = (f" Its children's oldest wait receipt was posted at "
                 f"{dead_run.pacific(receipt)}, {_hours(_age(wait.receipt_at, now))} "
                 "hours ago." if receipt is not None else "")
        detail = (f"No plan run for it was found among the newest {PLAN_RUNS_PAGE} "
                  "runs of the plan workflow: the run never started, or its dispatch "
                  f"never arrived.{since} No sweep or recovery will start one: a "
                  "person must plan it again or send the epic on.")
    else:
        head = f"has stood in {wait.lane} with its plan run dead — {kids}."
        ended = _when(run.updated_at)
        when = (f" at {dead_run.pacific(ended)}, {_hours(_age(run.updated_at, now))} "
                "hours ago" if ended is not None else "")
        detail = (f"Its newest plan run, {run.id}, ended {run.conclusion or 'unknown'}"
                  f"{when}. No sweep or recovery will start another: a person must "
                  "re-run it or send the epic on.")
    return f"{epic_once_key(wait)} {head}\n\n{detail}"


def epic_ledger_line(wait: EpicWait, age_minutes: float) -> str:
    """The epic's one-line entry on the red-run ledger, naming the epic where
    `standing_defect_alert` reads a card off it."""
    run = (f"its newest plan run {wait.run.id} ended {wait.run.conclusion or 'unknown'}"
           if wait.run is not None
           else f"no plan run found among the newest {PLAN_RUNS_PAGE}")
    return (
        f"{LEDGER_STALL_OPENER}{wait.epic}: {_children(wait.children)} "
        f"{_waits(wait.children)} on it in {lane_contract.QUEUE_LANE} while it "
        f"stands in {wait.lane} with {run}, for {_hours(age_minutes)}h, which no "
        "sweep will clear — a person must act"
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
        f"{lane_contract.QUEUE_LANE} stand refused or held, lowest {lowest} — no "
        "sweep will promote them, a person must act"
    )
    dated = [(age, r) for r in refused
             if (age := _age(r.first_seen, now)) is not None]
    if not dated:
        return line, None
    age, oldest = max(dated, key=lambda pair: pair[0])
    if age < IDLE_BOARD_MINUTES:
        return line, None
    entry = (
        f"{LEDGER_IDLE_OPENER}{cap}: {cards} {noun} in {lane_contract.QUEUE_LANE} "
        "stand refused or held with nothing dispatched; the oldest receipt, on "
        f"{oldest.identifier}, is {_hours(age)}h old — a person must act"
    )
    return line, entry
