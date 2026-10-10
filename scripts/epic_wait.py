#!/usr/bin/env python3
"""An epic whose blocker epic is still open waits in Backlog (DRE-6671).

The second epic of an observation-gated seam is "planned in detail only when
the first is Done" (`standards/card-quality.md`, the DRE-3164 / DRE-3245
shape). DRE-6591 closed one door to planning it early: a roll-up's activation
leaves a child an open sibling blocks in Backlog for the sweep's auto-advance
(`reconcile.advance_unblocked_epics`, DRE-6407). The Planning entrance itself
stayed open. On 2026-10-10
the planner on DRE-6589 (blocked by DRE-6587, In Progress, none of its seven
children Done) could only escalate "wait for the reading"; the CEO agreed, the
console's answer press put the card back in Planning — every decision outcome
lands there — and the relay dispatched a second planner run that could only
ask the same question again. Each loop cost a planner run and a Green Light
row, and nothing read the epic's `blockedBy` relations before the planner slot
was claimed.

## `hold` — the gate `plan.yml` runs before the planner slot

It reads the card and its `blockedBy` relations live, in one request (the
relations `prose_blockers.relation_ids` reads, each carrying its blocker's lane
inline), and HOLDS the card only when all of these are true:

  * the card is in Planning — the entrance this guards — or already in Backlog,
    where a previous run of this gate put it. Any other lane (an approval In
    Progress, a review) is not the Planning entrance, and passes;
  * the card is itself an epic (`mid_epic.is_epic`: the shape stamp, `[EPIC]`
    in the title, or children);
  * at least one blocker is an epic whose lane is not in
    `prose_blockers.TERMINAL` (Done, Canceled, Duplicate). A blocker that is a
    card and not an epic holds nothing here — the card gate reads card-level
    blockers. The card's own parent epic holds nothing either: an epic stays
    In Progress until its children finish, so a parent named as a blocker
    would hold its child forever (`standards/engineering.md`).

A blocker whose lane cannot be read — or a relation the board could not name
at all — HOLDS the card and says so: planning an epic early costs a planner
run nobody needed, and waiting costs nothing the release below does not repair
on its next read.
A card that could not be read at all passes with a `::warning::`: nothing is
known about its blockers, not even that it has any, and a hold on no evidence
would strand a card nothing is coming for.

When it holds it writes `held=true` first, then, in this order:

  1. ONE receipt on the card, `⏸️ epic-waits:`, composed through
     `pipeline_act.receipt()` — every open blocker epic and its lane, that the
     card waits in Backlog, and that the sweep carries it on once every
     blocker epic is closed. A receipt already on the card for the same set
     of blockers since it was last carried on is not posted again; a
     different set is a new receipt;
  2. `linear_ops.cmd_advance(<card>, "Backlog", "Planning")` — only from
     Planning. A card already in Backlog is not moved again.

`plan.yml` then skips every later step on `held`, so no planner slot is
claimed and no model is called. When nothing holds, the gate prints why and
answers `held=false`, and the run continues unchanged.

The step exits 0 on every path: a write that fails after the decision is a
`::warning::` and the card is still held — this run must not plan it.

## The release — the sweep reads the hold again on every pass

The auto-advance `reconcile.advance_unblocked_epics` is an EDGE: it runs only
when the sweep itself closes a blocker epic Done, and only for a dependent
still in Backlog at that moment. A blocker Canceled or made a Duplicate, one a
person closed, one closed between this gate's read and its move, or one whose
lane this gate could not read, would each leave the held card in Backlog with
nothing coming for it — and the lane contract says an epic is never promoted
out of Backlog, so nothing would say so either.

So the release is a LEVEL check (`reconcile.release_waiting_epics`, run off
the Backlog read promotion already makes): every Backlog epic whose newest word
from either half is this gate's receipt (`waiting`) is read again live, and
`still_waits` answers with the hold's own rule — the same `open_blockers`, so
the release is exactly the hold's inverse. Nothing open: the sweep carries it
on through the auto-advance's own move and note, whose opening `RELEASED` ends
the wait on the record. Still open, or a read that failed: it waits, and the
next pass reads it again.

CLI:

    python3 scripts/epic_wait.py hold DRE-N [--github-output FILE]
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mid_epic  # noqa: E402 — ONE answer to "is this an epic" (DRE-3038)
import pipeline_act  # noqa: E402 — the one receipt writer (DRE-2825)
import prose_blockers  # noqa: E402 — TERMINAL, the one meaning of "met"
import routing_verdict  # noqa: E402 — ONE tolerant read of the shape stamp

# The act this gate's receipt is, and its live key — declared in
# `config/pipeline-acts.json`, learned by the console first (DRE-6672).
ACT = "epic-waits-on-blocker"
TAG = "epic-waits"
MARK = "⏸️"
OPENING = f"{MARK} {TAG}:"

# The entrance this guards, and where the card waits.
ENTRANCE = "Planning"
WAIT_LANE = "Backlog"

# What a blocker whose lane could not be read is called on the receipt.
UNREAD = "could not be read"

# The opening of the auto-advance's note (`reconcile._auto_advance_epic`) —
# the record that a wait ended. A hold receipt older than it is spent.
RELEASED = "🧹 Auto-advanced Backlog → Triage:"

# One card, its parent, and its `blocks` inverse relations with each blocker's
# lane, title and first child inline — so deciding costs one request, plus a
# shape-stamp read only for an open blocker its title and children do not
# already call an epic.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
  identifier title state { name }
  parent { identifier }
  children(first: 1) { nodes { id } }
  inverseRelations(first: 100) {
    nodes { type issue {
      identifier title state { name }
      children(first: 1) { nodes { id } }
    } }
    pageInfo { hasNextPage }
  }
} }"""

_ID = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


def _linear_ops():
    import linear_ops

    return linear_ops


def _has_children(issue: dict) -> bool:
    return bool(((issue.get("children") or {}).get("nodes")) or [])


def _shape(lops, identifier: str) -> tuple:
    """The card's shape stamp and its comment bodies — `(None, [])` when the
    thread cannot be read, which is "nothing has classified this"."""
    try:
        bodies = list(lops.comment_bodies(identifier, whole_thread=True))
    except Exception as exc:  # noqa: BLE001 — an unreadable stamp is no stamp
        print(f"epic-wait: {identifier}'s comments could not be read ({exc})")
        return None, []
    return routing_verdict.shape_of(bodies), bodies


def _lane(issue: dict) -> str | None:
    return ((issue or {}).get("state") or {}).get("name") or None


def open_blockers(lops, card: dict) -> list:
    """`[(identifier, lane or None)]` — every blocker epic of `card` that is not
    terminal, plus every blocker whose lane could not be read (lane None).
    A blocker that is a card and not an epic, a terminal one, and the card's
    own parent are not in it."""
    parent = (card.get("parent") or {}).get("identifier")
    out = []
    for rel in ((card.get("inverseRelations") or {}).get("nodes") or []):
        if rel.get("type") != "blocks":
            continue
        issue = rel.get("issue") or {}
        identifier = issue.get("identifier")
        if not identifier:
            out.append(("a blocker the board could not name", None))
            continue
        if identifier == parent:
            print(f"epic-wait: {identifier} is this card's parent epic — "
                  "a parent named as a blocker holds nothing")
            continue
        lane = _lane(issue)
        if lane in prose_blockers.TERMINAL:
            continue
        epic = mid_epic.is_epic(issue.get("title"), _has_children(issue))
        if not epic:
            epic = mid_epic.is_epic(issue.get("title"), _has_children(issue),
                                    _shape(lops, identifier)[0])
        if not epic:
            print(f"epic-wait: blocker {identifier} is a card, not an epic — "
                  "it holds nothing here")
            continue
        out.append((identifier, lane))
    return out


def named(waits: list) -> str:
    """Every blocker in `waits` with its lane, as the receipt names them."""
    return ", ".join(f"{ident} ({lane or UNREAD})" for ident, lane in waits)


def receipt_detail(card: str, waits: list) -> str:
    """The receipt's body; its first line names every blocker and its lane."""
    return (
        f"{OPENING} {card} waits in {WAIT_LANE} — blocker epic(s) still open: "
        f"{named(waits)}.\n\n"
        "An epic is planned only once every epic it is blocked by is Done: "
        "the second epic of a seam is planned on what the first one observed "
        "(`standards/card-quality.md`). So this planning run claimed no "
        "planner slot and called no model.\n\n"
        f"It waits in {WAIT_LANE}. The sweep reads its blockers again on every "
        "pass, and once every blocker epic is closed — Done, Canceled or "
        "Duplicate — its auto-advance carries it on to be planned; nobody "
        "needs to move it. A blocker whose lane could not be read holds it "
        "too, until the next pass reads it."
    )


def _blocker_set(line: str) -> frozenset:
    _, _, listed = line.partition("still open:")
    return frozenset(_ID.findall(listed))


def _first_line(body: str) -> str:
    lines = (body or "").lstrip().splitlines()
    return lines[0] if lines else ""


def _since_released(bodies: list) -> list:
    """The bodies after the newest note that carried the card on, oldest first."""
    out: list = []
    for body in bodies:
        if _first_line(body).startswith(RELEASED):
            out = []
        else:
            out.append(body)
    return out


def already_said(bodies: list, waits: list) -> bool:
    """Does the card already carry a receipt for exactly these blockers, since
    it was last carried on?"""
    wanted = frozenset(ident for ident, _ in waits if _ID.fullmatch(ident))
    for body in _since_released(bodies):
        first = _first_line(body)
        if first.startswith(OPENING) and _blocker_set(first) == wanted:
            return True
    return False


def waiting(bodies: list) -> bool:
    """Is the card held by this gate and not carried on since? `bodies` is
    oldest first; a hold receipt newer than the newest `RELEASED` note is a
    wait still standing."""
    return any(_first_line(b).startswith(OPENING) for b in _since_released(bodies))


def still_waits(lops, card: str) -> list | None:
    """The open blocker epics that still hold `card`, read live — `[]` when
    nothing does and the card may be carried on, `None` when the card or its
    whole relation page could not be read, which is never a release.

    The hold's own rule (`open_blockers`), so a blocker Canceled or Duplicate
    releases exactly as one Done does, and a blocker whose lane could not be
    read keeps the card waiting until a read that can. A read that raises
    raises: the caller says so and asks again next pass."""
    issue = (lops.gql(CARD_QUERY, {"id": card}) or {}).get("issue")
    if not issue:
        return None
    if (((issue.get("inverseRelations") or {}).get("pageInfo") or {})
            .get("hasNextPage")):
        print(f"epic-wait: {card}'s relation page is full — the blockers past "
              "it were not read, so it is not released on half a page")
        return None
    return open_blockers(lops, issue)


def _answer(github_output: str | None, held: bool) -> None:
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"held={'true' if held else 'false'}\n")


def hold(lops, card: str, *, github_output: str | None = None) -> int:
    """Hold `card` in Backlog while a blocker epic is still open. Returns
    0 on every path; the decision is `held=` in `github_output`."""
    try:
        issue = (lops.gql(CARD_QUERY, {"id": card}) or {}).get("issue")
    except Exception as exc:  # noqa: BLE001 — fail open, loudly
        issue, why = None, str(exc)
    else:
        why = "Linear returned no such card"
    if not issue:
        print(f"::warning::epic-wait: {card} could not be read ({why}) — "
              "nothing is known about its blockers, so the run continues")
        _answer(github_output, False)
        return 0

    lane = _lane(issue)
    if lane not in (ENTRANCE, WAIT_LANE):
        print(f"epic-wait: {card} is in {lane!r}, not at the {ENTRANCE} "
              "entrance — nothing to hold, the run continues")
        _answer(github_output, False)
        return 0

    shape, bodies = _shape(lops, card)
    if not mid_epic.is_epic(issue.get("title"), _has_children(issue), shape):
        print(f"epic-wait: {card} is not an epic — the gate holds only an "
              "epic, the run continues")
        _answer(github_output, False)
        return 0

    if (((issue.get("inverseRelations") or {}).get("pageInfo") or {})
            .get("hasNextPage")):
        print(f"epic-wait: {card}'s relation page is full — the blockers past "
              "it were not read")
    waits = open_blockers(lops, issue)
    if not waits:
        print(f"epic-wait: {card} is an epic with no open blocker epic — "
              "the run continues")
        _answer(github_output, False)
        return 0

    _answer(github_output, True)
    print(f"epic-wait: {card} HELD — blocker epic(s) still open: {named(waits)}")
    try:
        if already_said(bodies, waits):
            print(f"epic-wait: {card} — receipt for these blockers already on the card")
        else:
            lops.cmd_comment(card, pipeline_act.receipt(
                "epic-waits-on-blocker", receipt_detail(card, waits)))
            print(f"epic-wait: {card} — receipt posted")
        if lane == ENTRANCE:
            lops.cmd_advance(card, WAIT_LANE, ENTRANCE)
        else:
            print(f"epic-wait: {card} is already in {WAIT_LANE} — nothing moved")
    except Exception as exc:  # noqa: BLE001 — the hold stands; say so
        print(f"::warning::epic-wait: {card} is held, but a write failed ({exc}) "
              "— this run still plans nothing")
    return 0


def _cmd_hold(args) -> int:
    return hold(_linear_ops(), args.card, github_output=args.github_output)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    h = sub.add_parser("hold", help="hold an epic whose blocker epic is open")
    h.add_argument("card")
    h.add_argument("--github-output", default=None,
                   help="the step's $GITHUB_OUTPUT, where held=true|false goes")
    h.set_defaults(fn=_cmd_hold)
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_usage(sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
