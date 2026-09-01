#!/usr/bin/env python3
"""Which board-wide sweeps a merge can actually have changed (DRE-2930).

    python3 scripts/merge_sweep_gate.py DRE-1234   # prints 0, 1 or 2 flags

## The measurement

`linear-sync.yml` ran TWO board-wide reconcile passes on every merge —
`reconcile.py --promote-only` and `reconcile.py --close-epics` — whether or
not the merge changed anything either pass acts on. `--promote-only` walks the
whole Backlog (226 cards on the 2026-08-31 census) and every refusal costs a
Linear read; `--close-epics` reads the active board and then every active
epic's children.

**47 cards merged on 2026-09-01, so those two lines bought roughly 94 extra
full-board passes**, on top of the 4-per-hour cron and the 4-per-hour
dispatch sweeps: 8 reconcile runs in the peak hour, not 4. That day the
workspace exhausted Linear's 2,500/hour quota.

Nothing was wrong per merge. The defect is that the cost was **unbounded in
merge rate**, so the pipeline was least reliable on its best days — and a
47-merge day is a good day.

## The gate

A merge genuinely can unblock a card, and reacting to that is the whole point
of the event hook. The waste was that it reacted whether or not anything was
unblocked. So: one read of the just-merged card, and both sweeps become
conditional on something this merge actually made true.

* **`--promote-only`** — the dependency gate promotes a Backlog card when its
  `blockedBy` relations are all terminal (`prose_blockers.relation_blockers`
  is the gate, and relations are the only dependency since DRE-2676). This
  merge can only have cleared a blocker if the merged card itself reached a
  terminal state AND it `blocks` at least one card that has not. Neither
  true ⇒ no card became promotable *because of this merge*.
* **`--close-epics`** — `reconcile.close_finished_epics` closes an open epic
  whose children are all terminal. Only the merged card's OWN parent can have
  become all-terminal from this merge; a parentless card, an already-closed
  parent, or a parent with an open sibling closes nothing.

Both answers come out of ONE Linear read, so the gate costs one request and
replaces up to two full-board passes.

## What it deliberately does NOT gate on

A merge also frees a WIP slot, and `promote_ready` refuses at the cap — so a
card held back purely by the cap now waits for the cron rather than promoting
on the merge. That is a bounded delay to the next `*/15` sweep, and gating on
it would mean sweeping on nearly every merge of a busy repo, which is exactly
the unbounded cost this card removes. The cron is the backstop, as it is for
every other thing the gate declines.

The gate is also deliberately coarse in one direction: it asks whether the
merge cleared *a* blocker, not whether the unblocked card is now fully
promotable (its epic active, its other blockers met, its lane Backlog). Those
answers cost a read per dependent and belong to the sweep, which is about to
compute them anyway. Coarse here means an occasional pass that promotes
nothing — never a promotion that never happens.

## Unknowns, and why they answer differently

"We could not look" is not "there is nothing there", so an unreadable card
falls **open** and runs both sweeps: losing a promotion is worse than one
extra pass, and that is exactly today's behaviour. A **rate-limited** read is
the one unknown that falls **closed** — the quota is already gone (the
2026-09-01 exhaustion is why this file exists) and two board-wide passes
would deepen it. Classify before retrying; a retry against an exhausted limit
cannot succeed (DRE-1921, `standards/vendor-boundaries.md` Q5).

This module decides and prints. It never writes to Linear: every write on the
merge path stays in `reconcile.py`, which owns them.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402 — ONE definition of "terminal" (DRE-2676)

#: The two merge-triggered sweeps, in the order linear-sync.yml ran them.
PROMOTE = "--promote-only"
CLOSE_EPICS = "--close-epics"
ALL_SWEEPS = (PROMOTE, CLOSE_EPICS)

#: A blocker in one of these states no longer holds anything — the same set
#: the dependency gate and `close_finished_epics` clear on, read from the one
#: place that defines it rather than restated here.
TERMINAL = prose_blockers.TERMINAL

#: How deep the two pages are read. A card that FILLS either page is reported
#: as unknown (and sweeps) rather than counted clean — the same rule
#: `check_prose_blockers` applies to its own relation page.
RELATION_PAGE = 50
CHILD_PAGE = 100

QUERY = """query($id: String!) { issue(id: $id) {
     identifier state { name }
     relations(first: %d) {
       pageInfo { hasNextPage }
       nodes { type issue { identifier } relatedIssue { identifier state { name } } }
     }
     parent {
       identifier state { name }
       children(first: %d) {
         pageInfo { hasNextPage }
         nodes { identifier state { name } }
       }
     }
   } }""" % (RELATION_PAGE, CHILD_PAGE)


def _state(node) -> str:
    return ((node or {}).get("state") or {}).get("name") or "unknown"


def _dependents(card: dict) -> list[dict]:
    """The cards this one BLOCKS, off its forward `blocks` relations.

    Read as "the side that is not this card". Linear puts the card itself
    under `issue` and the other end under `relatedIssue` on a forward
    relation — verified against the live API on 2026-09-01, where DRE-2676's
    `blocks` node reads issue=DRE-2676, relatedIssue=DRE-2929 — but taking
    the other side by identifier is right under either convention, and
    getting it backwards here would make every merge look like it unblocked
    nothing: silent, permanent, and indistinguishable from the gate working.
    """
    mine = card.get("identifier")
    out = []
    for rel in ((card.get("relations") or {}).get("nodes") or []):
        if rel.get("type") != "blocks":
            continue
        sides = [rel.get("issue") or {}, rel.get("relatedIssue") or {}]
        other = [s for s in sides if s.get("identifier") and s.get("identifier") != mine]
        out.extend(other)
    return out


def unblocked_something(card: dict) -> bool:
    """Did this merge clear a blocker for a card that is still open?"""
    if (card.get("relations") or {}).get("pageInfo", {}).get("hasNextPage"):
        return True  # a page we did not finish reading is not an empty page
    if _state(card) not in TERMINAL:
        # The card never reached a terminal state — `card-done`'s no-code /
        # `DEMO:` guard refuses those (the six portico false closes), and a
        # card that is still open holds its dependents exactly as it did.
        return False
    return any(_state(dep) not in TERMINAL for dep in _dependents(card))


def finished_an_epic(card: dict) -> bool:
    """Did this merge close out the merged card's own parent epic?"""
    parent = card.get("parent")
    if not parent:
        return False
    if _state(parent) in TERMINAL:
        return False  # already closed — rediscovering that costs a board pass
    children = (parent.get("children") or {})
    if children.get("pageInfo", {}).get("hasNextPage"):
        return True  # unknown, not finished
    states = [_state(kid) for kid in (children.get("nodes") or [])]
    # The same condition `reconcile.close_finished_epics` applies: every child
    # terminal AND at least one actually Done — an epic whose every child was
    # cancelled did not ship.
    return bool(states) and all(s in TERMINAL for s in states) and "Done" in states


def sweeps(card: dict) -> list[str]:
    """The sweep flags this merge justifies, in linear-sync.yml's order."""
    chosen = []
    if unblocked_something(card):
        chosen.append(PROMOTE)
    if finished_an_epic(card):
        chosen.append(CLOSE_EPICS)
    return chosen


def decide(identifier: str) -> list[str]:
    """Read the merged card and answer. Never raises — the merge path must
    not go red because the gate could not decide; it falls back instead."""
    try:
        card = (linear_ops.gql(QUERY, {"id": identifier}) or {}).get("issue")
    except linear_ops.LinearRateLimited as e:
        # The one unknown that falls CLOSED. See the module docstring.
        print(
            f"merge-sweep gate: {identifier} unreadable — the Linear quota is "
            f"exhausted ({e}); running NO board-wide sweep, because a sweep "
            f"against an exhausted limit cannot succeed and deepens it. The "
            f"cron sweep picks this up once the quota refills.",
            file=sys.stderr,
        )
        return []
    except Exception as e:  # noqa: BLE001 — any other unknown falls OPEN
        print(
            f"merge-sweep gate: could not read {identifier} ({e}) — running "
            f"both sweeps, which is the un-gated behaviour. An unreadable card "
            f"is not a merge that changed nothing.",
            file=sys.stderr,
        )
        return list(ALL_SWEEPS)
    if not card:
        print(
            f"merge-sweep gate: Linear returned no issue for {identifier} — "
            f"running both sweeps rather than assuming the merge was inert.",
            file=sys.stderr,
        )
        return list(ALL_SWEEPS)
    chosen = sweeps(card)
    print(
        f"merge-sweep gate: {identifier} ({_state(card)}) — "
        f"{'unblocked a live card' if PROMOTE in chosen else 'unblocked nothing'}, "
        f"{'finished its epic' if CLOSE_EPICS in chosen else 'finished no epic'}"
        f" → {len(chosen)} board-wide sweep(s): {' '.join(chosen) or 'none'}",
        file=sys.stderr,
    )
    return chosen


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        # No card means the caller had no agent branch to act for; the merge
        # step already exits before here in that case, so this is belt and
        # braces — and it stays silent rather than sweeping the board.
        print("merge-sweep gate: no card given — nothing to sweep", file=sys.stderr)
        return 0
    for flag in decide(argv[1].strip()):
        print(flag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
