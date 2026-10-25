#!/usr/bin/env python3
"""A loop neither promotion gate can see alone (DRE-3539).

The sweep holds work at two levels. The epic gate
(`reconcile.epic_blockers_unmet`, DRE-1772) holds every child of an epic that
is `blockedBy` an epic not built out (DRE-6407); the card gate
(`reconcile.card_relation_blockers`) holds a Queued card whose own `blockedBy`
blocker is open. Each is right on its own. Across the two a loop can form,
and then both print a correct "held by" line on every pass and nothing ever
releases: from 2026-09-08 about 5:40 PM PT to 2026-09-10 6:41 AM PT, epic
DRE-3421 was `blockedBy` DRE-3420, DRE-3420's lead child DRE-3433 was
`blockedBy` DRE-3428, a DRE-3421 child — ten cards held for 38 hours until a
person found the loop by hand and deleted the epic-to-epic edge.

This module is the pure half, the split `promotion_stall` runs: it never
touches Linear and imports neither `reconcile` nor `linear_ops`.
`reconcile.promote_ready` hands it the pass's Queued candidates and the pass's
epic records, already read, and prints and posts what it returns.

## The edges — exactly three

* card C -> blocker B (`CARD_EDGE`): C carries a non-terminal `blockedBy` on B,
  off C's own inverse relations (`prose_blockers.relation_blockers`).
* card C -> epic F (`EPIC_EDGE`): C's parent epic E carries a non-terminal
  `blockedBy` on F, so the epic gate holds C behind F. Off E's inverse
  relations in the pass's epic record; an epic whose record the pass did not
  read has no blockers here.
* epic F -> card K (`CHILD_EDGE`): K is a Queued candidate under F, F is In
  Progress, and `epic_cap.buildable()` counts K (not a proof, no person's
  mark), so F is not built out while K waits. Off K's own parent, labels and
  title on the candidate list.

There is deliberately no child -> own-parent edge: a child waits for its
parent's BLOCKERS, never for its parent, and modeling it the other way makes
every epic and its child a two-node loop.

A node outside those two sources — a card in any lane but Queued, an epic the
pass did not read — carries no edges. A card outside Queued is not one these
gates hold, so a loop through it is not this deadlock.

## What a cycle says

Its identifiers in order from the lowest, so the same loop reads the same way
every pass, with the epic a card waits THROUGH named between the card and the
blocker; every step with its kind; and the one relation to remove. An
epic-level edge is preferred — the one the operator deleted on 2026-09-10 —
then a card-level one, each the first met walking from the lowest identifier.
The sweep names the loop and never edits a relation: a person or the planner
decides which edge goes.
"""
from __future__ import annotations

import os
import re
import sys
from collections import deque
from datetime import UTC, datetime
from typing import Iterable, NamedTuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import epic_cap  # noqa: E402 — ONE rule for "a child the cap would count"
import mid_epic  # noqa: E402 — ONE rule for "a card is an epic"
import prose_blockers  # noqa: E402 — ONE reading of the non-terminal relations

#: What the sweep's line and the epic's comment open with.
TAG = "dependency-cycle:"

CARD_EDGE = "card"    # C's own blockedBy on B
EPIC_EDGE = "epic"    # C waits through its parent epic's blockedBy on F
CHILD_EDGE = "child"  # epic F waits for its open, buildable Queued child K

_PACIFIC = ZoneInfo("America/Los_Angeles")
_NUMBER = re.compile(r"-(\d+)$")


class Step(NamedTuple):
    """One edge of a cycle: `source` waits for `target`."""

    source: str
    target: str
    kind: str                # CARD_EDGE | EPIC_EDGE | CHILD_EDGE
    epic: str | None = None  # EPIC_EDGE: the parent epic whose relation holds the card

    def relation(self) -> str | None:
        """The `blockedBy` relation this step stands on, as `<B> blocks <A>`;
        None for a child step, which no relation makes."""
        if self.kind == CARD_EDGE:
            return f"{self.target} blocks {self.source}"
        if self.kind == EPIC_EDGE:
            return f"{self.target} blocks {self.epic}"
        return None

    def said(self) -> str:
        if self.kind == CHILD_EDGE:
            return f"{self.source} waits for its open child {self.target}"
        if self.kind == EPIC_EDGE:
            return (f"{self.source} (child of {self.epic}) waits for {self.target} "
                    f"({self.epic}'s blocker)")
        return f"{self.source} is blockedBy {self.target}"


class Cycle(NamedTuple):
    """One loop: `nodes` named in order from the lowest, `steps` in the same
    order, and `remove` the relation to delete to break it."""

    nodes: tuple[str, ...]
    steps: tuple[Step, ...]
    remove: str


def _number(identifier: str) -> tuple[float, str]:
    """Sort key for "lowest": by the card's number, then its text."""
    found = _NUMBER.search(identifier or "")
    return (int(found.group(1)) if found else float("inf"), identifier or "")


def _parent(card: dict) -> dict:
    return card.get("parent") or {}


def _is_epic(card: dict) -> bool:
    return mid_epic.is_epic(card.get("title") or "",
                            has_children=bool((card.get("children") or {}).get("nodes")),
                            shape=card.get("shape"))


def _edges(candidates: Iterable[dict], records: dict[str, dict]) -> dict[str, dict[str, Step]]:
    """`{source: {target: step}}` — the three kinds, off data the pass holds."""
    edges: dict[str, dict[str, Step]] = {}

    def add(step: Step) -> None:
        # A card held by its own relation AND through its epic on the same
        # blocker keeps the card's step: removing the epic's edge alone would
        # not release it.
        if step.source != step.target:
            edges.setdefault(step.source, {}).setdefault(step.target, step)

    for card in candidates:
        ident = card.get("identifier")
        if not ident or _is_epic(card):
            continue  # an epic in Queued is promoted by a person, never held here
        for blocker in prose_blockers.relation_blockers(card):
            add(Step(ident, blocker, CARD_EDGE))
        parent = _parent(card)
        epic = parent.get("identifier")
        record = records.get(epic) if epic else None
        if record is not None:
            for blocker in prose_blockers.relation_blockers(record):
                add(Step(ident, blocker, EPIC_EDGE, epic))
        if (epic and (parent.get("state") or {}).get("name") == epic_cap.IN_PROGRESS
                and epic_cap.buildable(card)):
            add(Step(epic, ident, CHILD_EDGE))
    return edges


def _reachable(start: str, edges: dict[str, dict[str, Step]]) -> set[str]:
    """Every node reached from `start` over one edge or more."""
    seen: set[str] = set()
    todo = list(edges.get(start, {}))
    while todo:
        node = todo.pop()
        if node not in seen:
            seen.add(node)
            todo.extend(edges.get(node, {}))
    return seen


def _shortest_loop(start: str, component: set[str],
                   edges: dict[str, dict[str, Step]]) -> list[Step]:
    """The shortest loop from `start` back to itself inside `component`,
    successors taken lowest first so the answer is the same every pass."""
    came: dict[str, Step] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for target in sorted(edges.get(node, {}), key=_number):
            if target not in component:
                continue
            step = edges[node][target]
            if target == start:
                path = [step]
                while path[0].source != start:
                    path.insert(0, came[path[0].source])
                return path
            if target not in came:
                came[target] = step
                queue.append(target)
    return []  # unreachable: `start` is in its own component only on a loop


def _remove(steps: list[Step]) -> str:
    """The first epic-level relation met from the lowest identifier, else the
    first card-level one."""
    for kind in (EPIC_EDGE, CARD_EDGE):
        for step in steps:
            if step.kind == kind:
                return step.relation() or ""
    return ""


def find_cycles(candidates: list[dict], records: dict[str, dict]) -> list[Cycle]:
    """Every loop among the pass's Queued `candidates` and epic `records`, one
    per strongly connected group, lowest identifier first.

    One per group, not every loop in it: removing the named edge either
    breaks the group or leaves a smaller loop the next pass names."""
    edges = _edges(candidates, records)
    nodes = set(edges) | {t for targets in edges.values() for t in targets}
    reach = {node: _reachable(node, edges) for node in nodes}
    covered: set[str] = set()
    cycles: list[Cycle] = []
    for node in sorted(nodes, key=_number):
        if node in covered or node not in reach[node]:
            continue
        component = {other for other in reach[node] if node in reach[other]}
        covered |= component
        steps = _shortest_loop(node, component, edges)
        named: list[str] = []
        for step in steps:
            named.append(step.source)
            if step.kind == EPIC_EDGE and step.epic not in named:
                named.append(step.epic)
        cycles.append(Cycle(tuple(named), tuple(steps), _remove(steps)))
    return cycles


def epics(cycle: Cycle) -> list[str]:
    """The epics in the loop — a child step's source and target epic, and the
    epic a card waits through — lowest first. A card-only loop has none."""
    found: set[str] = set()
    for step in cycle.steps:
        if step.kind == CHILD_EDGE:
            found.add(step.source)
        elif step.kind == EPIC_EDGE:
            found.update((step.target, step.epic))
    return sorted(found, key=_number)


def _walk(cycle: Cycle) -> str:
    return " → ".join(step.said() for step in cycle.steps)


def line(cycle: Cycle) -> str:
    """The sweep's one log line for `cycle`."""
    return (f"{TAG} {_walk(cycle)} — remove `{cycle.remove}`; neither gate "
            "releases any card in this loop, and the sweep never edits a relation")


def pacific_date(now: str) -> str:
    """The PT calendar date of the ISO time `now`, as `YYYY-MM-DD`."""
    at = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(_PACIFIC).date().isoformat()


def notice_tag(date: str) -> str:
    """The dated tag an epic's notice opens with — one comment per epic per
    PT day, found again by `count_comments` on the same tag."""
    return f"{TAG} {date}"


def notice(cycle: Cycle, date: str) -> str:
    """The comment posted on each epic in `cycle`, opening with its dated tag."""
    return (
        f"{notice_tag(date)} — these cards wait on each other, so neither the "
        "epic gate nor the card gate will ever release them:\n\n"
        f"{_walk(cycle)}\n\n"
        f"Remove `{cycle.remove}` to break the loop. The sweep never deletes or "
        "edits a relation — a person or the planner decides which edge goes. "
        "This is said once a day while the loop stands."
    )
