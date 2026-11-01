#!/usr/bin/env python3
"""Why a Queued card whose blockers are all finished has not moved (DRE-8646).

THE HOLE. `reconcile.promote_ready` refuses a Queued card at several exits
that only log: the WIP budget, a hand-applied hold, an epic that is not
active, the epic gate, and the epic cap's queue. Each is right, and each
leaves the card saying nothing. On 2026-11-01 DRE-7909 — an OPERATOR card,
both blockers Done — had sat in Queued for seven days, and every pass of the
sweep printed `is held for a human ('needs-human' label, reason=manual)` and
wrote nothing on the card.

So this module is two things, both pure — no Linear call, no I/O:

  * `admits_manual_hold` — the one predicate both hold lines in
    `promote_ready` ask (the board read's and the live re-check's): a
    person's card under a hand-applied hold goes on through the gates and is
    carried to Hand-work, where a person works from, with its label kept;
  * the vocabulary of the comment the sweep posts once per card per reason
    when one of those five exits holds a card whose blockers are all
    terminal — the opener, the reasons, the once-only key and the text.

`SAYS_WHY` is every opener a Queued card's thread may carry as the sweep's
answer for why it has not moved: this module's, the hold stamp, the agent
blocker's marker, and the ten refusals that already post once. The PROOF card
of epic DRE-8609 reads it by these exact strings.

The opener and the reasons are declared in `config/pipeline-acts.json`'s
`unconverted` block as a report, not an act: the comment moves nothing,
holds nothing new and hands the card to nobody — it says what an existing
gate is already doing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_cap  # noqa: E402
import hold  # noqa: E402
import mid_epic  # noqa: E402
import plan_critic  # noqa: E402
import prose_blockers  # noqa: E402
import routing_verdict  # noqa: E402

#: The comment's opener. The first line is
#: `🧾 promotion-wait: <reason> — <card id> <plain sentence>`.
OPENER = "🧾 promotion-wait"

WIP_CAP = "wip-cap"
HELD = "held"
EPIC_NOT_ACTIVE = "epic-not-active"
EPIC_GATE = "epic-gate"
EPIC_CAP_QUEUED = "epic-cap-queued"

#: Every reason the comment may name, in the order `promote_ready` meets them
#: at the budget exit and after it.
REASONS = (WIP_CAP, HELD, EPIC_NOT_ACTIVE, EPIC_GATE, EPIC_CAP_QUEUED)

# `reconcile` owns these two, and importing it here would be a cycle — it
# imports this module. `tests/test_promotion_wait.py` pins both to the
# constants' own values.
_BAD_REF = "unresolvable-blocker-reference"  # reconcile.BAD_REF_TAG
_AGENT_BLOCKED = "🛑 Agent blocked"  # reconcile.BLOCKER_MARKER

#: The refusals that already post once, each by its own tag. A card one of
#: them speaks for is not told again.
ALREADY_SAID = (
    routing_verdict.NO_VERDICT_TAG,
    routing_verdict.NOT_FLEET_TAG,
    routing_verdict.STALE_VERDICT_TAG,
    mid_epic.NO_VERDICT_TAG,
    epic_cap.UNDECIDED_TAG,
    plan_critic.POST_UNREAD_TAG,
    plan_critic.POST_SENT_BACK_TAG,
    plan_critic.POST_DIED_TAG,
    prose_blockers.CARD_TAG,
    _BAD_REF,
)

#: Every string a Queued card's thread may open a comment with as the sweep's
#: answer for why the card has not moved.
SAYS_WHY = (OPENER, hold.STAMP_PREFIX, _AGENT_BLOCKED) + ALREADY_SAID


def key(reason: str) -> str:
    """The once-only key: the comment's own first-line head for `reason`.
    Counted on the card's thread, so the pair is `(card, reason)`."""
    if reason not in REASONS:
        raise ValueError(f"{reason!r} is not a promotion-wait reason: {', '.join(REASONS)}")
    return f"{OPENER}: {reason} —"


def _verdict(bodies) -> str | None:
    """The card's one verdict, or None — two verdicts are none to act on."""
    try:
        return routing_verdict.verdict_on(bodies)
    except Exception:  # noqa: BLE001 — ConflictingVerdicts: not one to read
        return None


def admits_manual_hold(labels, bodies) -> bool:
    """Does a hold line let this held card go on through the gates?

    True only for a hold a person applied by hand — `needs-human` with no
    live stamp, `hold.reason_of` answering `manual` — on a card whose verdict
    is a person's (OPERATOR or WORKBENCH). Such a card is carried to
    Hand-work, the lane a person works from, which dispatches nothing. A
    FLEET card, a stamped hold, a card with no verdict or with two answer
    False, and the hold line stands as it did."""
    if hold.reason_of(labels, bodies) != "manual":
        return False
    verdict = _verdict(bodies)
    return verdict is not None and routing_verdict.is_person_verdict(verdict)


def blockers_terminal(card: dict) -> bool:
    """Is every `blocks` relation the candidates query returned terminal?
    Read off the relations in hand, never a second read. A card with no
    blocker at all answers True."""
    return all(state in prose_blockers.TERMINAL
               for state in prose_blockers.blocker_states(card).values())


def budget_reason(labels, bodies, epic_admits: bool) -> str | None:
    """Which reason a card held at the WIP budget exit is told, off the facts
    in hand: a hand-applied hold first, then an epic that does not admit the
    card, then the budget. None for a card under a stamped hold — its stamp
    already says why — and for a card the budget is not what waits on: one
    with no verdict, or one routed somewhere no run is dispatched, whose own
    refusal is said on the first pass with room."""
    reason = hold.reason_of(labels, bodies)
    if reason == "manual":
        return HELD
    if reason is not None:
        return None
    if not epic_admits:
        return EPIC_NOT_ACTIVE
    verdict = _verdict(bodies)
    if verdict is None or not routing_verdict.is_promotable(verdict):
        return None
    return WIP_CAP


def notice(reason: str, card: str, *, epic: str | None = None,
           epic_lane: str | None = None, active: int | None = None,
           cap: int | None = None) -> str:
    """The comment for `reason`: its first line the contract's, then what the
    card waits on and what releases it, in plain words."""
    head = key(reason)
    label = f"`{hold.HOLD_LABEL}`"
    if reason == WIP_CAP:
        first = f"{card} is ready to build and waits for a free build slot."
        waits = (f"the fleet's work-in-progress cap — {active} of {cap} build "
                 "slots are taken.")
        releases = ("a build in flight finishing, which frees a slot. The next "
                    "sweep with room promotes it, Urgent cards first, then by "
                    "card number.")
    elif reason == HELD:
        first = (f"{card} is held by a {label} label a person applied by hand, "
                 "with no hold stamp.")
        waits = (f"that {label} label. Every blocker is finished, and the sweep "
                 "never moves a card a person has held.")
        releases = (f"a person removing the {label} label. The next sweep then "
                    "reads the card again.")
    elif reason == EPIC_NOT_ACTIVE:
        first = f"{card} waits on its epic {epic}, which is in {epic_lane}, not In Progress."
        waits = (f"epic {epic}'s approval. A child moves only once its epic is "
                 "In Progress.")
        releases = (f"epic {epic} moving to In Progress, its plan approved. The "
                    "next sweep then reads the card again.")
    elif reason == EPIC_GATE:
        first = f"{card} waits on its epic {epic}, which is not releasing its children."
        waits = (f"epic {epic}'s own blockers — an epic it is blocked by that "
                 "has not shipped, or a defect on the epic itself. The sweep "
                 "log's epic-gate line names which.")
        releases = (f"whatever holds epic {epic} clearing. The epic gate then "
                    "releases its children on the next sweep.")
    else:  # EPIC_CAP_QUEUED — key() refused anything else above
        first = f"{card} waits on its epic {epic}, which the epic cap has queued in line."
        waits = (f"a place under the epic cap for epic {epic}, which waits its "
                 "turn in Green Light.")
        releases = (f"an epic ahead of it finishing, which starts epic {epic}. "
                    "Its children move on the next sweep after that.")
    return f"{head} {first}\n\nWhat it waits on: {waits}\nWhat releases it: {releases}"
