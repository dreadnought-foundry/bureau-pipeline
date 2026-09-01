#!/usr/bin/env python3
"""A dependency is a `blockedBy` relation, or it does not exist (DRE-2676).

Prose that claims a dependency the board does not hold is a **defect in the
card**, not a dependency.

## Why the gate stopped reading prose

The sweep honoured two sources for one question — the formal `blocks` relation
and a `**Blocked by:**` line in the body — and only one of them is a thing the
system can see. Linear models dependencies natively, renders them in the UI,
and no sentence can create one by accident; a sentence, meanwhile, is invisible
to the console, to auto-close, and to every consumer that is not this parser.
Two answers to one question is the drift, and DRE-2670 is what it costs: epic
DRE-2492 froze five of its own children for five days on the sentence *"neither
depends on the other"*, across ~480 consecutive GREEN sweeps.

## The measurement that made the strict version the safe one

The board was read card by card on **2026-08-31** — a full read per card, not
the list API, whose `description` truncates at 500 characters:

    293 live DRE cards · 35 carrying a prose blocker declaration ·
    44 declarations · 44 corroborated by a formal `blocks` relation ·
    0 prose-only

Three cards carry a relation the prose does not mention, so relations are
already the richer and more current source. Prose ⊆ relations everywhere, which
means a relations-only gate returns exactly what the old gate returned — the
behavioural change is a no-op against live data, and a refusal that routes
offending cards to Triage would, on day one, route zero. That is why this ships
strict rather than through a log-only window. `scripts/check_prose_blockers.py`
recomputes those numbers on demand; they are never remembered.

## Why the parser is DEMOTED and not deleted

Deleting it would make a false sentence permanently invisible: a card that says
"Blocked by DRE-N" with no relation would promote with nobody told, and the
prose would rot into a lie that reads authoritative to every human who opens
the card. So `blocker_prose`'s anchored grammar stays, and its job changes from
GATE to DETECTOR. Its anchoring matters more in this role, not less — a false
positive here routes a well-written card to Triage, so the DRE-2492 sentences
remain must-not-match fixtures in `blocker_prose.NOT_DECLARING`.

## The split

    relation_blockers(card)  -> the non-terminal `blocks` relations. THE GATE.
    prose_claims(card)       -> the declaring lines' ids. EVIDENCE ONLY.
    undeclared_claims(card)  -> prose_claims minus EVERY `blocks` relation,
                                terminal or not.

`undeclared_claims` subtracts terminal relations too, deliberately: a card
whose declared dependency has already shipped is documenting history, not
claiming something the board denies.

## The two refusals, and why they escalate differently

A CARD gets `card_refusal()` and is moved to **Triage** — `CLAUDE.md` defines
that lane as exactly this, *"something about the CARD is wrong and it cannot
proceed as written"*. Not Green Light: a malformed sentence is a mechanical
fix, not a CEO decision, and Green Light is the queue that costs CEO time.

An EPIC gets `epic_refusal()` and is **not moved at all**. `reconcile.
advance_unblocked_epics` moves an epic to Triage to *trigger the planner*, and
a planning run cannot fix a sentence; filing a separate Triage card is banned
outright (`CLAUDE.md`: *"Do not file notifications as Triage cards"*). So the
epic's escalation is time: past `reconcile.PROSE_DEFECT_RED_MINUTES` the sweep
run goes red through its existing exit-1 ledger and medic picks it up.

Both notices OPEN with their own tag, because `reconcile._surface_once` reads
the tag off the notice rather than being told one — pair a notice with the
wrong tag and two refusals silence each other.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocker_prose  # noqa: E402

# A blocker is met when it is terminal, and only then. Read inline off the
# relation, so the gate never fetches a state it was already handed.
TERMINAL = ("Done", "Canceled", "Duplicate")

# The idempotency keys the two refusals are surfaced under. NEITHER MAY CONTAIN
# THE OTHER: both are counted with `tag in body`, so one nesting inside the
# other would make each invisible to whichever reader found the other first —
# the same rule `pipeline_act` enforces on the act registry, for the same
# reason. `tests/test_prose_blocker_defect.py` pins both directions.
CARD_TAG = "prose-blocker-no-relation"
EPIC_TAG = "epic-prose-defect"

# The three phrases a declaration may open with, named in both notices so the
# author can see what the detector reads (`blocker_prose.BLOCKER_LINE` is the
# grammar; these are how a human recognises it).
DECLARING_PHRASES = ("Blocked by", "Depends on", "Serialize after")

# Where a defective CARD goes, and where its author sends it back from. Backlog,
# not Todo: Todo dispatches an agent run, and the card's real blockers may still
# be unmet — Backlog is the lane the dependency gate re-evaluates it in.
DEFECT_LANE = "Triage"
RETURN_LANE = "Backlog"


def _blocks_relations(card: dict) -> list:
    """Every `blocks` inverse relation on the card, terminal or not."""
    return [
        rel for rel in ((card.get("inverseRelations") or {}).get("nodes") or [])
        if rel.get("type") == "blocks"
    ]


def relation_ids(card: dict) -> set:
    """Every card the board records as blocking this one, in ANY state.

    The corroboration set. A dependency that has already shipped is still a
    dependency the card's prose is entitled to name.
    """
    return {
        ((rel.get("issue") or {}).get("identifier"))
        for rel in _blocks_relations(card)
    } - {None}


def relation_blockers(card: dict) -> set:
    """THE GATE: the blockers that are not yet met.

    Non-terminal `blocks` relations, and nothing else. Prose cannot add to this
    set, which is the whole of DRE-2676.
    """
    return {
        identifier
        for identifier, state in blocker_states(card).items()
        if state not in TERMINAL
    }


def blocker_states(card: dict) -> dict:
    """`{identifier: state}` for every `blocks` relation, terminal or not.

    The relation carries its blocker's state inline, so the refusal line can
    name the state without a second read per blocker per sweep.
    """
    out: dict = {}
    for rel in _blocks_relations(card):
        issue = rel.get("issue") or {}
        identifier = issue.get("identifier")
        if identifier:
            out[identifier] = (issue.get("state") or {}).get("name") or "unknown"
    return out


def prose_claims(card: dict) -> set:
    """EVIDENCE ONLY: every card id the body DECLARES as a blocker.

    Read with the one anchored grammar (`blocker_prose`, DRE-2922), so the
    detector, the producer that materialises a declaration into a relation at
    creation, and the groomer cannot disagree about what a declaration is.

    A card's own id and its PARENT EPIC's id are never claims. An epic only
    closes when its children finish, so an epic ref on a blocker line would
    deadlock the card forever (DRE-1207, DRE-1216, DRE-1233) — and under this
    card it would be worse than a deadlock: a defect refusal for a relation
    nobody would ever be right to set.
    """
    parent = (card.get("parent") or {}).get("identifier")
    return {
        ref for ref in blocker_prose.blocker_ids(card.get("description"))
        if ref not in (card.get("identifier"), parent)
    }


def undeclared_claims(card: dict) -> set:
    """The defect: what the prose claims and the board does not hold.

    Empty on all 293 live cards as of 2026-08-31 — including all 44 corroborated
    declarations, which stay exactly as they are.
    """
    return prose_claims(card) - relation_ids(card)


# --------------------------------------------------------------------------- #
# the notices                                                                  #
# --------------------------------------------------------------------------- #


def _claims(claims) -> str:
    return ", ".join(sorted(claims))


def _both_fixes(claims) -> str:
    named = _claims(claims)
    phrases = " / ".join(f'"{p}"' for p in DECLARING_PHRASES)
    return (
        "**Either fix works, and one of them is enough:**\n\n"
        f"1. **Make the claim true** — add the Linear `blockedBy` relation to "
        f"{named}. The dependency gate reads relations, so the card will then "
        "wait for it properly, and the console and the auto-close gates will "
        "see the dependency too.\n"
        f"2. **Make the line ordinary prose** — reword it so it does not OPEN "
        f"with {phrases}. That is what a declaration looks like to the "
        "detector; a sentence that merely mentions a card is not one."
    )


def card_refusal(identifier: str, claims) -> str:
    """Why the sweep is not promoting `identifier`, and what to do about it."""
    named = _claims(claims)
    return (
        f"🚨 {CARD_TAG}: {identifier} declares a dependency on {named} in its "
        "description, and the board holds no `blockedBy` relation for it — so "
        "that sentence is a defect in this card, not a dependency. The card is "
        f"not being promoted, and has been moved to **{DEFECT_LANE}**.\n\n"
        "**A dependency is a Linear `blockedBy` relation.** Prose is "
        "documentation: people read it, and nothing in the pipeline can act on "
        "it. A line that claims a dependency the board does not hold reads "
        "authoritative to every human who opens this card and is invisible to "
        "every gate — so it is worth fixing rather than leaving to rot.\n\n"
        f"{_both_fixes(claims)}\n\n"
        f"**Then move the card back to `{RETURN_LANE}`, not `Todo`.** "
        f"`{RETURN_LANE}` is where the dependency gate re-evaluates it — its "
        "real blockers may still be unmet — while `Todo` dispatches an agent "
        "run at it immediately."
    )


def epic_refusal(identifier: str, claims) -> str:
    """The same defect on an EPIC, which is not moved anywhere."""
    named = _claims(claims)
    return (
        f"🚨 {EPIC_TAG}: this epic's description declares a dependency on "
        f"{named} and the board holds no `blockedBy` relation for it. Until "
        "that is resolved the epic's children are not promoted — the sweep "
        "will not release work under an epic that says something about itself "
        "the board contradicts.\n\n"
        "**A dependency is a Linear `blockedBy` relation.** This epic's own "
        "prose is the only thing claiming one.\n\n"
        f"{_both_fixes(claims)}\n\n"
        "**The epic has not been moved.** Its lane is where the planner's work "
        "is tracked, and a planning run cannot fix a sentence. What happens "
        "instead is that the reconcile run goes red while this stands, so it "
        "reaches the medic rather than printing into a green log nobody reads."
    )


def refusal_tag(refusal: str | None) -> str | None:
    """The idempotency tag `refusal` is surfaced under, or None if it is not
    one of this module's notices.

    Read off the notice by its own module — `_surface_once` is handed a tag and
    never infers one, and this is the function that keeps the pairing honest.
    """
    first = ((refusal or "").splitlines() or [""])[0]
    for tag in (CARD_TAG, EPIC_TAG):
        if first.startswith(f"🚨 {tag}:"):
            return tag
    return None
