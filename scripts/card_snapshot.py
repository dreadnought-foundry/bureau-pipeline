#!/usr/bin/env python3
"""One read of the card for every pre-agent step of agent-task.yml (Stage 2 fix #10).

Before a build agent starts, `agent-task.yml` used to read the same card from
Linear eleven times inside a minute: the card-validation gate (the card, its
fields, its comments), the duplicate-dispatch guard (its comments, its lane),
the turn budget (its labels), the spoken-thread renderer (its comments and who
the key is) and the In Progress move (four reads around its write).

This takes ONE snapshot at the top of the job and every pre-agent step reads
it (`load`):

  * `BUREAU_READ=on`: the read door's `/cards?ids=<card>&comments=all` — no
    Linear request at all. Door first, with the same fallback rules as every
    other reader: an UNKNOWN, a closed or failing door, a card the door does
    not hold, or a comment thread the door cannot vouch for falls back to the
    one Linear read below. Never half an answer.
  * otherwise: ONE combined Linear read — the card, its newest comment window
    (`linear_ops.COMMENT_WINDOW_GQL`, the window every one of those readers
    took), who the key is, and the team's workflow states (so the In Progress
    move, a held card since BP-3, looks no lane up).
  * `shadow`: the door is read first, compared (`read-door-diff:`), and Linear's
    answer is the snapshot.

`linear-hold` is a fallback here, not a skip, and that is deliberate: skipping
the snapshot does not spare the held bucket anything — every step would then
make its own reads, eleven instead of one.

WHAT STAYS LIVE: the duplicate-dispatch guard's read of the card's lane (the
whole question there is whether it moved while the dispatch sat queued), and
the In Progress move's DRE-2316 pre-write re-read and DRE-1877 read-back. The
snapshot decides nothing that is written without one of those.

A snapshot that could not be taken writes no file and exits 0: every step then
reads Linear exactly as it did before this module existed. A dispatch is never
blocked by it. The snapshot is handed ONLY to the pre-agent steps — never to
the agent, which must see the card as it is.

    card_snapshot.py take <CARD> --out <path>
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bureau_read  # noqa: E402

# `linear_ops` is imported where it is used, never at module load: `load` is
# called by steps whose tests stand a fake `linear_ops` in for the real one,
# and reading the snapshot needs nothing from it.

ENV = "BUREAU_CARD_SNAPSHOT"
SCHEMA = "card-snapshot/1"

#: The one combined Linear read. The comment window is THE window
#: (`COMMENT_WINDOW_GQL`), so a step reading the snapshot sees exactly the
#: fifty newest comments its own read would have.
QUERY = """query($id: String!, $teamKey: String) { viewer { id }
  issue(id: $id) {
    id identifier title description updatedAt
    team { id key } state { id name type }
    labels { nodes { name } } children(first: 1) { nodes { id } }
    %s }
  workflowStates(filter: {team: {key: {eq: $teamKey}}}) { nodes { id name type } } }"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _door_mode() -> str:
    if (os.environ.get(bureau_read.MODE_ENV) or "").strip().lower() in ("", "off"):
        return "off"
    return bureau_read.mode()


def _from_linear(identifier: str) -> dict:
    import linear_ops

    data = linear_ops.gql(QUERY % linear_ops.COMMENT_WINDOW_GQL,
                          {"id": identifier, "teamKey": linear_ops._team_key(identifier)})
    issue = data.get("issue")
    if not isinstance(issue, dict) or not issue.get("identifier"):
        raise linear_ops.LinearError(f"{identifier}: no such card")
    comments = issue.pop("comments", None)
    return {
        "schema": SCHEMA, "source": "linear", "taken_at": _now(),
        "identifier": str(issue["identifier"]).upper(),
        "issue": issue,
        "comments": linear_ops.window_nodes(comments),
        "comments_partial": linear_ops.window_is_partial(comments),
        "viewer": ((data.get("viewer") or {}).get("id")),
        "workflowStates": ((data.get("workflowStates") or {}).get("nodes")),
    }


def _from_door(identifier: str) -> tuple[dict | None, object]:
    """The door's snapshot, or `(None, why)`."""
    import linear_ops

    try:
        read = bureau_read.cards([identifier], max_age=bureau_read.DISPATCH_CARDS_MAX_AGE,
                                 comments="all", relations=False)
    except bureau_read.ReadUnknown as e:
        return None, e.reason
    node = dict(read.nodes[0])
    comments = node.pop("comments", None) or {}
    nodes = linear_ops.window_nodes(comments)
    whole = (comments.get("pageInfo") or {}).get("hasNextPage") is False
    if not whole and len(nodes) < linear_ops.COMMENT_WINDOW:
        # Fewer than the newest fifty, and the door does not say that is all:
        # a Linear read could see comments this cannot. Never half an answer.
        return None, "comments-partial"
    # The newest fifty — the window each step's own Linear read would have
    # taken — so a step decides on the same comments whichever source served.
    window = nodes[-linear_ops.COMMENT_WINDOW:]
    return {
        "schema": SCHEMA, "source": "door", "taken_at": _now(), "as_of": read.as_of,
        "identifier": str(node.get("identifier") or "").upper(),
        "issue": node,
        "comments": window,
        "comments_partial": len(window) < len(nodes) or not whole,
        "viewer": read.viewer_id,
        "workflowStates": None,
    }, read


def take(identifier: str, out: str) -> dict | None:
    identifier = identifier.strip().upper()
    mode = _door_mode()
    door, why = (None, None)
    if mode in ("on", "shadow") and bureau_read.enabled():
        door, why = _from_door(identifier)
        if door is None:
            print(f"read-door: {identifier} snapshot unknown ({why}) — one Linear read instead")
    snap = door if (mode == "on" and door is not None) else None
    if snap is None:
        try:
            snap = _from_linear(identifier)
        except Exception as e:  # noqa: BLE001 — a snapshot never blocks a dispatch
            print(f"card-snapshot: no snapshot of {identifier} ({type(e).__name__}: {e}) — "
                  "every step reads Linear itself, as before", file=sys.stderr)
            return None
        if mode == "shadow" and door is not None:
            linear_node = {**snap["issue"], "comments": {"nodes": list(reversed(snap["comments"]))}}
            door_node = {**door["issue"], "comments": {"nodes": list(reversed(door["comments"]))}}
            bureau_read.report_diffs(
                "card-snapshot",
                bureau_read.compare([door_node], [linear_node], door_as_of=door.get("as_of"),
                                    fields=("lane", "labels", "title", "description",
                                            "children", "comments")),
                1)
    tmp = f"{out}.tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
    os.replace(tmp, out)
    print(f"card-snapshot: {identifier} from {snap['source']} — "
          f"{len(snap['comments'])} comment(s), lane {((snap['issue'].get('state') or {}).get('name'))!r}")
    return snap


def load(identifier: str) -> dict | None:
    """The snapshot this step was handed, for `identifier`, or None — in which
    case the caller reads Linear exactly as it always did."""
    path = os.environ.get(ENV)
    if not path:
        return None
    try:
        with open(path) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        return None
    if str(snap.get("identifier") or "").upper() != str(identifier or "").strip().upper():
        return None
    if not isinstance(snap.get("issue"), dict) or not isinstance(snap.get("comments"), list):
        return None
    return snap


def workflow_states(team_key: str | None) -> list[dict] | None:
    """The team's workflow states the snapshot was read with, for a held
    card's lane move (`linear_ops.state_id_and_type`) — or None. Only for the
    team the snapshot's card is on; a door snapshot carries none."""
    path = os.environ.get(ENV)
    if not path or not team_key:
        return None
    try:
        with open(path) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        return None
    ident = str(snap.get("identifier") or "")
    if ident.partition("-")[0].upper() != str(team_key).upper():
        return None
    states = snap.get("workflowStates")
    if not isinstance(states, list) or not all(
            isinstance(s, dict) and s.get("id") and s.get("name") and s.get("type")
            for s in states):
        return None
    return [dict(s) for s in states] or None


def label_names(snap: dict) -> list[str]:
    return [(n.get("name") or "") for n in
            ((snap["issue"].get("labels") or {}).get("nodes") or [])]


def comment_bodies(snap: dict) -> list[str]:
    """Oldest→newest, exactly as `linear_ops.comment_bodies` returns them."""
    return [c.get("body") or "" for c in snap["comments"]]


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "take" and argv[2] == "--out":
        take(argv[1], argv[3])
        return 0
    print("usage: card_snapshot.py take <CARD> --out <path>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
