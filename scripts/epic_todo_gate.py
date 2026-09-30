#!/usr/bin/env python3
"""An epic is never put in Todo (DRE-5316) — the rule, written once.

Todo is where a card waits for a build run, and nothing builds an epic: its
children promote from In Progress. The pipeline moved epic DRE-3621 from In
Progress to Todo at 21:43 PT on 2026-09-12 and it sat there seventeen days with
four children waiting. Every lane change of an existing card ends in
`linear_ops.guarded_state_write`, and every new card is created through
`_create_card` or `create_card`; those three call `refusal()` here, and on an
answer nothing is written.

## What an epic is

`mid_epic.is_epic` over the shape stamp, the `[EPIC]` title and the children —
the exact composition `reconcile.card_is_epic` calls, imported rather than
restated. `agent:planner` is NOT read: it says the planner owns a card, the
relay requires it before it will plan any card, and every classified one-off
wears it (DRE-3038/DRE-3044). Reading it here would refuse every promoted
one-off at the lane it is promoted into. This module cannot import
`reconcile`, which imports `linear_ops` at the top, so a test holds the two
answers equal instead.

## Approved, read off the board

An epic the CEO approved is In Progress; approval is that move and only that.
So the rule reads the lane the epic was in before Todo: at the write seam, the
card's current lane; for a card already sitting in Todo, the `fromState` of the
newest history entry into Todo (`lane_before_todo`). In Progress means
approved; anything else — Green Light included, and a history Linear cannot
return — means not approved. A drag from Green Light to Todo is deliberately
NOT approval: Green Light also holds escalations and parked plans no critic
passed, and the plan-critic's own PASS record is not read for the same reason.

## The right move

An approved epic stays in, or returns to, In Progress, where its children
promote. An unapproved one needs the CEO's approval in Green Light, and
approval is the move to In Progress. A mover that finds an epic already in Todo
carries it to `carry_lane()`: In Progress when approved, Planning when not —
never straight to Green Light, whose only road in is through both critics.

The refusal is posted from ONE site, `post_refusal`, once per card and move.

CLI (read-only — it calls no write function):

    python3 scripts/epic_todo_gate.py explain DRE-N [lane]   # lane defaults to Todo
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mid_epic  # noqa: E402 — ONE answer to "is this an epic" (DRE-3038)
import routing_verdict  # noqa: E402 — ONE tolerant read of the shape stamp

TAG = "epic-not-todo"

TODO = "Todo"
IN_PROGRESS = "In Progress"
GREEN_LIGHT = "Green Light"
PLANNING = "Planning"

#: How many of the card's newest history entries `lane_before_todo` reads. The
#: history holds every field change, not only lane moves, so the window is wide
#: enough that the entry into Todo is in it on any card that is sitting there.
HISTORY_WINDOW = 50


def _same_lane(a: str | None, b: str) -> bool:
    return (a or "").strip().lower() == b.lower()


def is_epic_card(title: str, has_children: bool, comment_bodies) -> bool:
    """Is this card an epic? The shape stamp first, then the `[EPIC]` title or
    any child. It takes no labels, so it cannot read `agent:planner`."""
    return mid_epic.is_epic(
        title, has_children, routing_verdict.shape_of(comment_bodies)
    )


def approved(lane_before_todo: str | None) -> bool:
    """An epic the CEO approved was In Progress before Todo. Nothing else is
    approval."""
    return _same_lane(lane_before_todo, IN_PROGRESS)


def carry_lane(lane_before_todo: str | None) -> str:
    """Where a mover carries an epic it finds in Todo. The movers branch on this
    and write each lane as a literal, so the lane-writer check reads both."""
    return IN_PROGRESS if approved(lane_before_todo) else PLANNING


def _move(lane_before_todo: str | None) -> str:
    return IN_PROGRESS if approved(lane_before_todo) else GREEN_LIGHT


def opener(identifier: str, lane_before_todo: str | None) -> str:
    """The refusal's first line — and its dedupe key: the card and the move."""
    return f"🚫 {TAG}: {identifier} → {_move(lane_before_todo)}"


def refusal(identifier: str, target_state: str, title: str, has_children: bool,
            comment_bodies, lane_before_todo: str | None,
            carried_to: str | None = None) -> str | None:
    """The refusal body for a write of `target_state`, or None when it may go.

    None unless the target is Todo and the card is an epic. `carried_to` is the
    lane a mover wrote (None at the seam, where nothing moves), and the body says
    where the card is now — so it never names a lane the card is not in.
    """
    if not _same_lane(target_state, TODO):
        return None
    if not is_epic_card(title, has_children, comment_bodies):
        return None
    if approved(lane_before_todo):
        why = (
            "This epic is already approved: the CEO moved it to In Progress, and "
            "In Progress is where its children promote."
        )
    else:
        why = (
            "This epic is not approved yet. It needs the CEO's approval in Green "
            "Light, and approval is the move to In Progress."
        )
    if carried_to is None:
        where = "Nothing was written: the card stays where it is."
    elif _same_lane(carried_to, PLANNING):
        where = (
            "It was carried to Planning, where both critics read its plan before "
            "it reaches Green Light for the CEO."
        )
    else:
        where = f"It was carried to {carried_to}."
    return (
        f"{opener(identifier, lane_before_todo)}\n\n"
        "An epic is never dispatched: Todo is where a card waits for a build "
        f"run, and nothing builds an epic. {why}\n\n{where}"
    )


def lane_before_todo(ops, identifier: str) -> str | None:
    """The lane the card was in before it entered Todo: the `fromState` of the
    NEWEST history entry whose `toState` is Todo. None when Linear cannot
    answer, or the window holds no such entry — both read as not approved.

    `history(first: n)` is the n NEWEST entries, newest first; `last: n` is the
    OLDEST (recorded on DRE-5034, DRE-5142).
    """
    try:
        data = ops.gql(
            """query($id: String!) { issue(id: $id) {
                 history(first: %d) { nodes {
                   fromState { name } toState { name }
                 } } } }""" % HISTORY_WINDOW,
            {"id": identifier},
        )
        nodes = (((data or {}).get("issue") or {}).get("history") or {}).get("nodes") or []
        for node in nodes:
            if _same_lane((node.get("toState") or {}).get("name"), TODO):
                return (node.get("fromState") or {}).get("name") or None
    except Exception as exc:  # noqa: BLE001 — unreadable is "not approved"
        print(f"{identifier}: could not read its history ({exc})", file=sys.stderr)
    return None


def post_refusal(ops, identifier: str, body: str) -> bool:
    """Post the refusal on the card — the ONE site that does. `ops` is the write
    layer, passed in so this module never imports it at the top.

    Posts only when no comment already carries this refusal's opener: keyed on
    the card and the move, not on the newest comment, so a receipt another
    writer posts in between cannot make a writer that retries every sweep post
    it again. Never raises: the refusal is that nothing was written, and a
    comment that cannot be posted must not turn it into a crash.
    """
    first = body.splitlines()[0]
    try:
        if ops.count_comments(identifier, first) != 0:
            return False
        return ops.cmd_comment(identifier, body) is None
    except Exception as exc:  # noqa: BLE001 — reporting never blocks the refusal
        print(f"{identifier}: could not post the {TAG} refusal ({exc})", file=sys.stderr)
        return False


def decision(ops, identifier: str, lane: str = TODO) -> str | None:
    """The refusal the write layer would give a write of `lane`, read through
    the read functions only. For a card already in Todo the lane before it is
    read off its history, the way the movers read it."""
    issue = ops.get_issue(identifier, fresh=True)
    current = (issue.get("state") or {}).get("name")
    before = lane_before_todo(ops, identifier) if _same_lane(current, TODO) else current
    kids = bool(((issue.get("children") or {}).get("nodes")) or [])
    return refusal(identifier, lane, issue.get("title") or "", kids,
                   ops.comment_bodies(identifier), before)


def explain(ops, identifier: str, lane: str = TODO) -> None:
    """Print `refused` and the body, or `allowed`. Writes nothing."""
    body = decision(ops, identifier, lane)
    if body is None:
        print(f"allowed: a write of {identifier} to {lane!r} is not refused")
    else:
        print(f"refused: a write of {identifier} to {lane!r}\n\n{body}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "explain" or len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    import linear_ops

    explain(linear_ops, argv[1], argv[2] if len(argv) == 3 else TODO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
