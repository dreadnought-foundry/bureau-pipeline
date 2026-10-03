#!/usr/bin/env python3
"""The one-time move of a person's work out of Todo into Hand-work (DRE-5323,
epic DRE-5240).

Todo is the build button: a card that enters it makes the relay dispatch a run.
Until DRE-5322 the sweep also carried a WORKBENCH or OPERATOR card there, marked
`hand-built`, so a person's card could wait in Todo for days beside the fleet's
builds and Todo read as a stuck queue. The sweep now carries those cards to
`Hand-work`. This script moves the ones that were already in Todo when it
changed.

## Four classes, by rule, read live, in this order

  * **epic** — `epic_todo_gate.is_epic_card`, the pipeline's one epic test (the
    shape stamp, an `[EPIC]` title, or any child). `agent:planner` is never
    read: every classified one-off wears it. The script writes nothing to an
    epic: the sweep's carry (DRE-5347) moves an epic it finds in Todo, and the
    census names the lane it will carry it to, `epic_todo_gate.carry_lane` of
    the lane the epic was in before Todo. One writer of that rule, not two.
  * **finished** — an open card whose comments carry linear-sync's merge
    receipt (`✅ Merged:`) or `linear_ops.MERGED_NOT_CLOSED_MARKER`. Never
    closed here: Done is ground truth and a close is a person's call. The census
    quotes the receipt, and the operator closes the card by hand or returns it
    with a reason.
  * **person** — carries `hand-built` or `no-code`. Moved to Hand-work through
    the guarded write layer, with a `🧳 hand-work-migration:` comment.
  * **build** — none of the above: a card a run is dispatched or queued for.
    Left alone.

A card whose labels or comments could not be read is `unclassified` and left
alone.

## Skipped, with the reason printed

  * a person card with a dispatched run receipt (`⏳`, `🧠`) newer than its
    `hand-built` / `no-code` mark — a run that started after the mark is a run
    nobody should have to explain away by moving the card under it;
  * with `--only`, a named card that is not in Todo.

It refuses to run at all, before any write, when Linear cannot be read. No card
id appears in its code: selection is by rule.

CLI:

    python3 scripts/hand_work_migration.py census
    python3 scripts/hand_work_migration.py run                # dry run, the default
    python3 scripts/hand_work_migration.py run --apply
    python3 scripts/hand_work_migration.py run --only DRE-N   # one card, dry or --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_todo_gate  # noqa: E402 — the one epic test and the carry rule (DRE-5316)
import linear_ops  # noqa: E402
import routing_verdict  # noqa: E402

TODO = "Todo"
HAND_WORK = "Hand-work"

MARK = "🧳"
TAG = "hand-work-migration"
#: The line every migration comment opens with. The proof card reads it to tell
#: a migrated card from a sweep-moved one (`🧹 Auto-promoted Backlog →`) and from
#: a carried epic (`🚫 epic-not-todo:`).
OPENER = f"{MARK} {TAG}:"

EPIC = "epic"
FINISHED = "finished"
PERSON = "person"
BUILD = "build"
UNCLASSIFIED = "unclassified"
CLASSES = (EPIC, FINISHED, PERSON, BUILD)

#: The marks that say a person builds this card — the marks the two verdicts
#: whose actor is a person declare, read off the vocabulary.
PERSON_MARKS = tuple(sorted({
    mark.lower()
    for name in routing_verdict.verdicts()
    if not routing_verdict.is_promotable(name)
    for mark in routing_verdict.marks(name)
}))

#: linear-sync's merge receipt (`linear_ops.cmd_card_done`), and the marker it
#: posts on a card it deliberately leaves open.
MERGED_RECEIPT = "✅ Merged:"
FINISHED_MARKERS = (MERGED_RECEIPT, linear_ops.MERGED_NOT_CLOSED_MARKER)

#: A dispatched run's proof-of-life receipts (`reconcile._LIFE_PREFIXES`).
LIFE_PREFIXES = ("⏳", "🧠")

_CARD_ID = re.compile(r"^DRE-\d+$")

#: Todo, every card, with what the four rules read: labels, parent, whether it
#: has children, its newest comments, and its newest history (for the time its
#: mark was added). Paginated: `gql_paged` refuses a query that cannot be.
POPULATION_QUERY = """query($lane: String!, $after: String) {
  issues(first: 25, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    state: {name: {eq: $lane}}
  }) {
    nodes {
      id identifier title createdAt
      state { name }
      labels { nodes { name } }
      parent { identifier state { name } }
      children(first: 1) { nodes { id } }
      history(first: 50) { nodes { createdAt addedLabels { name } } }
      %s
    }
    pageInfo { hasNextPage endCursor }
  }
}""" % linear_ops.COMMENT_WINDOW_GQL


class Unreadable(Exception):
    """Linear could not be read, so nothing is decided and nothing is written."""


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def read_todo(lops) -> list[dict]:
    """Every card in Todo, or `Unreadable` — never an empty list for a failure."""
    try:
        return lops.gql_paged(POPULATION_QUERY, {"lane": TODO})
    except Exception as e:  # noqa: BLE001 — LinearError, a network error, anything
        raise Unreadable(f"could not read Todo from Linear ({e})") from e


def _labels(card: dict) -> list[str] | None:
    nodes = (card.get("labels") or {}).get("nodes")
    if nodes is None:
        return None
    return [(n.get("name") or "") for n in nodes]


#: The one place the API's newest-first comment order is reversed (DRE-3250),
#: bound at import: it is a pure helper, and the write layer passed to `run` is
#: a separate argument.
_window_nodes = linear_ops.window_nodes


def _comments(card: dict) -> list[dict] | None:
    if card.get("comments") is None:
        return None
    return _window_nodes(card.get("comments"))


def _bodies(card: dict) -> list[str]:
    return [c.get("body") or "" for c in (_comments(card) or [])]


def _has_children(card: dict) -> bool:
    return bool(((card.get("children") or {}).get("nodes")) or [])


def _when(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# the four classes                                                             #
# --------------------------------------------------------------------------- #


def finished_receipt(card: dict) -> str | None:
    """The merge receipt line this card carries, quoted, or None."""
    for body in reversed(_bodies(card)):
        for line in body.splitlines():
            if any(marker in line for marker in FINISHED_MARKERS):
                return line.strip()
    return None


def classify(card: dict) -> str:
    """One of the four classes, by the precedence above — or `unclassified`."""
    labels, comments = _labels(card), _comments(card)
    if labels is None or comments is None or not card.get("identifier"):
        return UNCLASSIFIED
    if epic_todo_gate.is_epic_card(card.get("title") or "", _has_children(card),
                                   _bodies(card)):
        return EPIC
    if finished_receipt(card) is not None:
        return FINISHED
    if any(label.lower() in PERSON_MARKS for label in labels):
        return PERSON
    return BUILD


def mark_added_at(card: dict) -> datetime | None:
    """When the card's person mark was added: the newest history entry adding
    `hand-built` or `no-code`. A mark set when the card was created, or added
    beyond the history window, has no entry — the card's creation is then the
    earliest it can have been, which is the conservative answer for the skip
    rule below (any run receipt counts as newer)."""
    newest = None
    for node in ((card.get("history") or {}).get("nodes") or []):
        added = [(lbl.get("name") or "").lower() for lbl in (node.get("addedLabels") or [])]
        if any(mark in added for mark in PERSON_MARKS):
            at = _when(node.get("createdAt"))
            if at is not None and (newest is None or at > newest):
                newest = at
    return newest or _when(card.get("createdAt"))


def run_after_mark(card: dict) -> str | None:
    """Why this person card must not move — a dispatched run receipt newer than
    its mark — or None."""
    mark = mark_added_at(card)
    for comment in reversed(_comments(card) or []):
        body = (comment.get("body") or "").lstrip()
        if not body.startswith(LIFE_PREFIXES):
            continue
        at = _when(comment.get("createdAt"))
        if at is None or mark is None or at > mark:
            return (
                f"a dispatched run receipt at {comment.get('createdAt')} is newer "
                f"than its person mark ({mark.isoformat() if mark else 'unreadable'})"
                " — a run started after the card was marked, so it is not moved"
            )
    return None


def census(cards: list[dict], ops=None) -> list[dict]:
    """One row per Todo card: its class and what will happen to it.

    `ops` is the read layer, asked only for an epic's lane before Todo (its
    history), so the row can name where the sweep will carry it."""
    rows = []
    for card in cards:
        cls = classify(card)
        labels = _labels(card) or []
        row = {
            "identifier": card.get("identifier"),
            "title": card.get("title") or "",
            "class": cls,
            "labels": labels,
            "parent": (card.get("parent") or {}).get("identifier"),
            "verdict": _verdict(card),
            "action": "",
            "skip": None,
        }
        if cls == EPIC:
            before = epic_todo_gate.lane_before_todo(ops, row["identifier"]) if ops else None
            lane = epic_todo_gate.carry_lane(before)
            row["carry_to"] = lane
            row["action"] = (f"left alone — the sweep's epic carry moves it to "
                             f"{lane} (it was {before or 'unknown'} before Todo)")
        elif cls == FINISHED:
            row["receipt"] = finished_receipt(card)
            row["action"] = ("finished, never closed — left alone; the operator closes "
                             f"it Done by hand or returns it with a reason. Receipt: "
                             f"{row['receipt']}")
        elif cls == PERSON:
            row["skip"] = run_after_mark(card)
            row["action"] = (f"skipped — {row['skip']}" if row["skip"]
                             else f"moves to {HAND_WORK}")
        elif cls == BUILD:
            row["action"] = "left alone — a build card"
        else:
            row["action"] = "left alone — its labels or comments could not be read"
        rows.append(row)
    return rows


def _verdict(card: dict) -> str | None:
    try:
        return routing_verdict.verdict_on(_bodies(card))
    except routing_verdict.ConflictingVerdicts:
        return "CONFLICTING"


# --------------------------------------------------------------------------- #
# the run                                                                      #
# --------------------------------------------------------------------------- #


def migration_note(row: dict) -> str:
    """The comment a moved card carries, opening with `OPENER`."""
    marks = ", ".join(f"`{m}`" for m in row["labels"] if m.lower() in PERSON_MARKS)
    return (
        f"{OPENER} moved {TODO} → {HAND_WORK} by the one-time Hand-work "
        "migration.\n\n"
        f"**Why:** this card is a person's work — it carries {marks} — and "
        f"{HAND_WORK} is now the lane a person's work waits in. {TODO} holds only "
        "what the fleet builds: a card entering it makes the relay dispatch a "
        "build. Nothing about the card changed except its lane, and nothing was "
        "dispatched."
    )


def run(ops, rows: list[dict], *, apply: bool = False) -> dict:
    """Move every person card not skipped. Writes nothing unless `apply`.

    The move is CONDITIONAL on the card still being in Todo (`expect`): a card
    that left Todo since the read is refused by the write layer, not dragged
    back. The comment follows a move that happened, and only then."""
    moved, refused = [], []
    for row in rows:
        if row["class"] != PERSON or row.get("skip"):
            continue
        if not apply:
            continue
        ident = row["identifier"]
        if ops.cmd_state(ident, HAND_WORK, expect=(TODO,)) is False:
            refused.append(ident)
            continue
        ops.cmd_comment(ident, migration_note(row))
        moved.append(ident)
    return {"applied": apply, "moved": moved, "refused": refused}


def restrict(cards: list[dict], only: list[str]) -> tuple[list[dict], list[str]]:
    """The cards named by `--only`, and the named ids that are not in Todo."""
    by_id = {c.get("identifier"): c for c in cards}
    return [by_id[i] for i in only if i in by_id], [i for i in only if i not in by_id]


def render(rows: list[dict]) -> str:
    lines = []
    for cls in CLASSES + (UNCLASSIFIED,):
        group = [r for r in rows if r["class"] == cls]
        if not group:
            continue
        lines.append(f"{cls} ({len(group)}):")
        for r in group:
            lines.append(
                f"  {r['identifier']:<10} {r['title'][:60]}\n"
                f"             labels: {', '.join(r['labels']) or 'none'}; "
                f"verdict: {r['verdict'] or 'none'}; parent: {r['parent'] or 'none'}\n"
                f"             → {r['action']}"
            )
    return "\n".join(lines) or "Todo is empty."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("census", help="Todo, each card with its class and what will happen")
    p_run = sub.add_parser("run", help="move the person cards (needs --apply to write)")
    p_run.add_argument("--apply", action="store_true")
    p_run.add_argument("--only", nargs="+", metavar="CARD")
    args = parser.parse_args(argv)

    only = [o.strip().upper() for o in (getattr(args, "only", None) or [])]
    bad = [o for o in only if not _CARD_ID.match(o)]
    if bad:
        print(f"ERROR: not a card identifier: {', '.join(bad)}", file=sys.stderr)
        return 2

    try:
        cards = read_todo(linear_ops)
    except Unreadable as e:
        print(f"ERROR: {e} — refusing to run; nothing was written.", file=sys.stderr)
        return 2

    missing: list[str] = []
    if only:
        cards, missing = restrict(cards, only)
    rows = census(cards, linear_ops)
    print(render(rows))
    for ident in missing:
        print(f"skipped {ident}: not in {TODO} — the migration only moves cards out of {TODO}")

    if args.command == "census":
        return 0
    result = run(linear_ops, rows, apply=args.apply)
    if not args.apply:
        print("\ndry run — nothing was written. Re-run with --apply.")
        return 0
    print(f"\nmoved {len(result['moved'])} card(s) to {HAND_WORK}: "
          f"{', '.join(result['moved']) or 'none'}")
    if result["refused"]:
        print(f"refused by the write layer (left Todo since the read): "
              f"{', '.join(result['refused'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
