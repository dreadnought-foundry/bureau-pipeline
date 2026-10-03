#!/usr/bin/env python3
"""One read of a card per job, for the steps that judge its pull request (Stage 2 #14).

The critic (`qa-review.yml`) and the verifier (`verify.yml`) each read the
card they are judging twice from Linear: its description (the **Design:**
line; the verifier's scope reads the labels too) and its comment thread (the
build's `model-attempt:` heartbeat that keeps a model from reviewing its own
build, DRE-3880). This takes ONE snapshot at the start of the job and every
step reads that file.

## Where the snapshot comes from

* `BUREAU_READ` `on`, on a run the door accepts: the console's read door,
  `/cards?ids=<card>&comments=all&relations=0`. Only a provably whole thread is
  taken (`bureau_read.cards` says `thread-incomplete` otherwise): a thread
  missing the heartbeat fails the model separation closed where Linear would
  have found it.
* Anything else — `off` (the default), a door that cannot give the whole
  answer, a closed door, any error — ONE Linear read: the description, the
  labels and the same fifty-newest comment window `dump-comments` read
  (`linear_ops.COMMENT_WINDOW_GQL`, the one literal). That is the two reads
  this replaces, in one request.
* A `pull_request` or `pull_request_target` run never asks the door: the door
  refuses those tokens by design (S7), and `bureau_read` will not mint one.
  Nearly every review and verify is one, so for them this is the one Linear
  read and nothing else. Only a `workflow_dispatch` re-review or re-verify can
  be served by the door.
* `UNKNOWN reason=linear-hold` in `on`: no Linear call at all (Stage 2 item
  34) and no snapshot — the steps degrade exactly as on an unreadable card.
* `shadow`: the door first, then the one Linear read; the difference is logged
  as `read-door-diff:` lines and Linear's answer is the one written.

Same rules as Reconcile's door reads (BP-2): Linear on UNKNOWN, a closed door
or any error; skip on a Linear hold; never half an answer.

## What a step reads, and what it gets on a failed read

A read that fails writes NO file, and removes one an earlier job left on a
self-hosted runner. Each reader then sees exactly what its own failed read
used to give it:

* `description <file>` — the raw description, as `linear_ops.py description`
  printed it (empty for none). No file: exit 1, nothing printed → the visual-QA
  stage reads no **Design:** line and skips, as before.
* `thread <file>` — the comment bodies as a JSON array, oldest → newest, as
  `linear_ops.py dump-comments` printed them. No file: exit 1 → the select step
  passes `--built-on-unknown`, which fails CLOSED, as before.
* `verify_scope.py --snapshot <file>` — description and labels, with
  `read_card`'s rule that an empty description is no card signal. No file:
  no card signal, as before.

## Why the steps that judge a pull request, and not the build

agent-task's pre-agent steps are BP-4's (fix #10); they may take this same
snapshot. Nothing here writes to Linear: every write still goes through
`linear_ops`, which re-reads what it must.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402

COMMENT_WINDOW = 50  # linear_ops.COMMENT_WINDOW — the shadow compares like with like


def _linear_query() -> str:
    import linear_ops

    return ("query($id: String!) { issue(id: $id) {"
            " identifier updatedAt description labels { nodes { name } }"
            f" {linear_ops.COMMENT_WINDOW_GQL} }} }}")


def _snapshot(node: dict, source: str) -> dict:
    """The facts the readers use, out of a Linear-shaped node. Comments arrive
    NEWEST first from both Linear and the door; reversed ONCE, here."""
    comments = list(((node.get("comments") or {}).get("nodes")) or [])
    return {
        "identifier": node.get("identifier"),
        "source": source,
        "description": node.get("description") or "",
        "labels": [(n or {}).get("name") or ""
                   for n in ((node.get("labels") or {}).get("nodes") or [])],
        "comments": [(c or {}).get("body") or "" for c in reversed(comments)],
    }


def _from_linear(identifier: str) -> dict | None:
    """The one Linear read: the node, or None when Linear holds no such card.
    Raises whatever `linear_ops.gql` raises."""
    import linear_ops

    return (linear_ops.gql(_linear_query(), {"id": identifier}) or {}).get("issue")


def _door(identifier: str) -> bureau_read.DoorRead:
    return bureau_read.cards([identifier], max_age=bureau_read.REVIEW_CARD_MAX_AGE,
                             comments="all", relations=False)


def _shadow_compare(door: bureau_read.DoorRead | None, linear_node: dict | None) -> None:
    if door is None:
        return
    node = dict(door.nodes[0])
    # Linear's answer is the fifty-newest window; the door's is every comment.
    # Like with like, or every thread past fifty is a "difference".
    comments = dict(node.get("comments") or {})
    comments["nodes"] = list(comments.get("nodes") or [])[:COMMENT_WINDOW]
    node["comments"] = comments
    diffs = bureau_read.compare([node], [linear_node] if linear_node else [],
                                door_as_of=door.as_of,
                                fields=("description", "labels", "comments"))
    bureau_read.report_diffs("card", diffs, 1)


def take(identifier: str) -> dict | None:
    """The card's snapshot, or None when it could not be read (or must not be:
    a Linear hold). Never half of one."""
    mode = bureau_read.mode()
    shadow_door: bureau_read.DoorRead | None = None
    if mode in ("on", "shadow"):
        try:
            read = _door(identifier)
        except bureau_read.ReadUnknown as e:
            if e.skip and mode == "on":
                bureau_read.note_skip(f"the card read for {identifier}", e.reason)
                return None
            print(f"read-door: card {identifier} not served ({e}) — reading Linear",
                  file=sys.stderr)
            if mode == "shadow":
                print(f"read-door-diff: card door-unknown ({e.reason}) — nothing compared")
        else:
            if mode == "on":
                return _snapshot(read.nodes[0], "door")
            shadow_door = read
    node = _from_linear(identifier)
    if mode == "shadow":
        _shadow_compare(shadow_door, node)
    if not node:
        return None
    return _snapshot(node, "linear")


def write(path, snap: dict | None) -> None:
    """Write the snapshot, or — for None — make sure no file is there: a
    snapshot an earlier job left on a self-hosted runner is not this card's."""
    path = Path(path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if snap is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(snap))
        tmp.replace(path)


def load(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or "comments" not in data or "description" not in data:
        return None
    return data


def description_of(path) -> str | None:
    snap = load(path)
    return None if snap is None else (snap.get("description") or "")


def thread_json(path) -> str | None:
    snap = load(path)
    return None if snap is None else json.dumps(list(snap.get("comments") or []))


USAGE = """usage:
  card_snapshot.py take <DRE-N> <file>    read the card once; exit 1 = no snapshot
  card_snapshot.py description <file>     the raw description (exit 1: none)
  card_snapshot.py thread <file>          the comment bodies, oldest first (exit 1: none)"""


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "take":
        identifier, path = argv[1].strip().upper(), argv[2]
        try:
            snap = take(identifier)
        except Exception as e:  # noqa: BLE001 — an unreadable card is no snapshot
            write(path, None)
            print(f"card-snapshot: could not read {identifier} ({e}) — no snapshot; "
                  "every step that reads it degrades as on an unreadable card",
                  file=sys.stderr)
            return 1
        write(path, snap)
        if snap is None:
            print(f"card-snapshot: no snapshot of {identifier}", file=sys.stderr)
            return 1
        print(f"card-snapshot: {identifier} from {snap['source']} "
              f"({len(snap['comments'])} comment(s))", file=sys.stderr)
        return 0
    if len(argv) == 2 and argv[0] in ("description", "thread"):
        out = description_of(argv[1]) if argv[0] == "description" else thread_json(argv[1])
        if out is None:
            return 1
        sys.stdout.write(out)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
