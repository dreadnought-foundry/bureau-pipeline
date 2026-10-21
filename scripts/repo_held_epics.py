#!/usr/bin/env python3
"""An approved epic in a repo held at max_wip 0 is told so once (DRE-7754).

When a repo's WIP cap is 0 its sweep goes idle in one line, and nothing
reaches the board. On 2026-10-19 that is how DRE-7600 sat approved in
agent-bureau-demo, its children in Queued looking like cards waiting their
turn, with no notice to anyone (epic DRE-7671). This module ends the silence
in two passes; the reconcile wiring card (DRE-7755) calls them.

`hold_pass` runs on a repo held at max_wip 0. It reads this repo's In Progress
epics, then their Queued children, and every read comes before any write. Each
Queued child that waits on the cap is held `wip-cap-held` through `hold.apply`,
and the epic gets one `repo-held` receipt, unless one already stands: a
receipt created after the epic's newest green light and after the newest
`cap-restored` lift line on any of its Queued children.

`lift_pass` runs on a repo whose cap is above 0. It reads this repo's Queued
cards wearing the hold and lifts each one whose live stamp reason is
`wip-cap-held`. Every other hold is left alone.

Two words, never written where the other belongs: the act's tag is
`repo-held` (the receipt, `LOG_PREFIX`, the console's `ACTS` entry, DRE-7751)
and the hold reason is `wip-cap-held`, lifted on `cap-restored`. A hold line
carrying an act tag would be read as a receipt (`docs/holds.md`).

Which children wait on the cap: every Queued child of this repo except one
carrying a live PARKED routing verdict, which is deliberately not built and
which a restored cap would not move. A child blocked by an unfinished sibling
waits on the cap as well, and the lift hands it back to the dependency gate.

Unknown is never a write. An epic whose green light cannot be read, or one of
whose threads cannot be read whole, gets neither a hold nor a receipt, and the
pass says why in one line. A child wearing the label with no live stamp reads
`manual`, the shape of a person's hold, so it is never re-stamped: the receipt
lists it as a person's and the pass names it.

Import-safe: no I/O at import, and no REPO or LINEAR_API_KEY read.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hold  # noqa: E402 — the one writer of the hold
import lane_contract  # noqa: E402 — the Queued lane and its alias
import linear_ops  # noqa: E402 — import-safe: no key read at import
import mid_epic  # noqa: E402 — the green light
import pipeline_act  # noqa: E402 — the one receipt writer
import routing_verdict  # noqa: E402 — PARKED

#: Every line either pass returns opens with this (config/proof-reach.json).
LOG_PREFIX = "repo-held:"

#: The act and its tag (config/pipeline-acts.json), and the receipt's first
#: line prefix.
ACT = "repo-held-notice"
TAG = "repo-held"
RECEIPT_PREFIX = f"⏸️ {TAG}:"

#: The hold (config/holds.json): the reason, its lift kind and the writer.
HOLD_REASON = "wip-cap-held"
LIFT_KIND = "cap-restored"
WRITER = "scripts/repo_held_epics.py"

#: How a child wearing the label with no live stamp is listed. `reason_of`
#: reads it `manual`, exactly as a hold a person put on by hand.
UNSTAMPED = "manual — needs-human with no hold stamp; a person lifts it"

_EPIC_LANES = list(mid_epic.EPIC_ACTIVE_LANES)
_QUEUE_LANES = list(lane_contract.QUEUE_LANES)

#: This repo's approved epics. The label is matched the way routing matches
#: it (`reconcile.IDLE_QUERY`): any label CONTAINING the slug, case-blind, and
#: `_repo_of` keeps the cards whose slug is exactly this repo.
EPICS_QUERY = """query($needle: String!, $states: [String!]!, $after: String) {
  issues(first: 25, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    labels: {name: {containsIgnoreCase: $needle}},
    state: {name: {in: $states}}
  }) { nodes {
    id identifier state { name } labels { nodes { name } }
    children(first: 1) { nodes { id } }
    history(first: 50) { nodes { createdAt toState { name } } }
    %s
  } pageInfo { hasNextPage endCursor } } }""" % linear_ops.COMMENT_WINDOW_GQL

#: Their Queued children, every epic in one read.
CHILDREN_QUERY = """query($ids: [ID!], $states: [String!]!, $after: String) {
  issues(first: 50, after: $after, filter: {
    parent: {id: {in: $ids}},
    state: {name: {in: $states}}
  }) { nodes {
    id identifier state { name } labels { nodes { name } }
    parent { id identifier }
    %s
  } pageInfo { hasNextPage endCursor } } }""" % linear_ops.COMMENT_WINDOW_GQL

#: This repo's Queued cards wearing the hold.
HELD_QUERY = """query($needle: String!, $states: [String!]!, $after: String) {
  issues(first: 50, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    and: [
      {labels: {name: {containsIgnoreCase: $needle}}},
      {labels: {name: {eqIgnoreCase: %s}}}
    ],
    state: {name: {in: $states}}
  }) { nodes {
    id identifier state { name } labels { nodes { name } }
    %s
  } pageInfo { hasNextPage endCursor } } }""" % (json.dumps(hold.HOLD_LABEL),
                                                 linear_ops.COMMENT_WINDOW_GQL)

_LIFTED = f"{hold.LIFT_PREFIX} reason={HOLD_REASON} because={LIFT_KIND} "


class _Unreadable(Exception):
    """A thread that could not be read whole; `what` names it."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


# --------------------------------------------------------------------------- #
# reads                                                                        #
# --------------------------------------------------------------------------- #


def _paged(gql, query: str, variables: dict) -> list:
    """Every node of one read. One request unless Linear says more lie past
    the page, which no held repo's board comes near."""
    nodes: list = []
    after = None
    seen: set = set()
    while True:
        conn = (gql(query, {**variables, "after": after}) or {}).get("issues") or {}
        nodes += conn.get("nodes") or []
        info = conn.get("pageInfo") or {}
        after = info.get("endCursor")
        if not info.get("hasNextPage") or not after or after in seen:
            return nodes
        seen.add(after)


def _repo_of(card: dict) -> str | None:
    """The card's `repo:` slug, read as routing reads it: lowercased, owner
    dropped (`reconcile.card_repo`)."""
    for label in linear_ops._label_names(card):
        name = label.lower()
        if name.startswith("repo:"):
            return name[len("repo:"):].rsplit("/", 1)[-1] or None
    return None


def _thread(card: dict) -> list:
    """The card's comments oldest→newest, `[{"body", "createdAt"}]`: its
    window, or the whole thread when the window is not all of it. Raises
    `_Unreadable` when the whole read does not answer, or still holds no
    more than the window did."""
    comments = card.get("comments")
    window = [{"body": n.get("body") or "", "createdAt": n.get("createdAt") or ""}
              for n in linear_ops.window_nodes(comments)]
    if not linear_ops.window_is_partial(comments):
        return window
    identifier = card.get("identifier")
    try:
        whole = linear_ops.comment_timeline(identifier, whole_thread=True)
    except Exception as exc:  # noqa: BLE001 — an unread thread is unknown
        print(f"{LOG_PREFIX} could not read {identifier} whole ({exc})", file=sys.stderr)
        raise _Unreadable(f"comments of {identifier}") from exc
    if len(whole) <= len(window):
        raise _Unreadable(f"comments of {identifier}")
    return whole


def _bodies(thread: list) -> list:
    return [c["body"] for c in thread]


def _first_line(body: str) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _ts(iso: str | None) -> datetime | None:
    try:
        return mid_epic._ts(iso) if iso else None
    except (TypeError, ValueError):
        return None


def _newest(thread: list, prefix: str) -> datetime | None:
    stamps = [_ts(c["createdAt"]) for c in thread if _first_line(c["body"]).startswith(prefix)]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _number(identifier: str) -> tuple:
    digits = re.findall(r"\d+", identifier or "")
    return (int(digits[-1]) if digits else 0, identifier or "")


# --------------------------------------------------------------------------- #
# hold_pass                                                                    #
# --------------------------------------------------------------------------- #


def _listing(child: dict) -> str:
    if child["hold"] is None:
        return f"held ({HOLD_REASON})"
    return f"held ({child['hold']})"


def _detail(epic: str, slug: str, holds, waiting: list) -> str:
    """The receipt's body: what is held, the stub's words, what lifts it."""
    where = holds.stub_path or holds.cap_source
    if holds.stub_path and holds.stub_comment:
        said = (f"The cap is set in `{holds.stub_path}`, and the stub says why:\n\n"
                f"> {holds.stub_comment}")
    elif holds.stub_path:
        said = f"The cap is set in `{holds.stub_path}`, which says nothing more about it."
    else:
        said = f"The cap is read from {holds.cap_source}."
    rows = "\n".join(f"- {c['identifier']} — {_listing(c)}" for c in waiting)
    return (
        f"{RECEIPT_PREFIX} {epic} is approved, and {slug} builds nothing — its WIP cap is 0.\n\n"
        f"{said}\n\n"
        f"Every Queued child of {epic} waits on the cap:\n\n{rows}\n\n"
        f"What lifts it: restoring `max_wip` above 0 in `{where}`. The next sweep "
        f"then lifts every `{HOLD_REASON}` hold listed here, and each child waits "
        "on the dependency gate as before. A child held for another reason keeps "
        "that hold."
    )


def _hold(child: str) -> None:
    """Hold one Queued child for the cap: the label, then the stamp."""
    hold.apply(child, reason=HOLD_REASON, at=None, by=WRITER)


def _post_receipt(epic: str, detail: str) -> None:
    body = pipeline_act.receipt("repo-held-notice", detail)
    linear_ops.cmd_comment(epic, body)


def _plan_epic(epic: dict, children: list) -> dict:
    """What to write for one epic, read off threads already in hand or read
    whole now. Raises `_Unreadable`."""
    queued = [{"card": child, "thread": _thread(child)} for child in children]
    waiting = []
    for entry in queued:
        bodies = _bodies(entry["thread"])
        if routing_verdict.is_parked(bodies):
            continue
        labels = linear_ops._label_names(entry["card"])
        stamp = hold.read_stamp(bodies)
        if hold.reason_of(labels, bodies) is None:
            state = None
        elif stamp is None:
            state = UNSTAMPED
        else:
            state = stamp["reason"]
        waiting.append({"identifier": entry["card"]["identifier"], "hold": state})
    if not waiting:
        return {"waiting": []}
    # The receipt stands when it is newer than the green light and than every
    # `cap-restored` lift on a Queued child: a re-approval, or children lifted
    # and held again, is a hold the earlier receipt no longer describes.
    green = _ts(mid_epic.green_light_from((epic.get("history") or {}).get("nodes")))
    lifted = [t for t in (_newest(e["thread"], _LIFTED) for e in queued) if t is not None]
    receipt = _newest(_thread(epic), RECEIPT_PREFIX)
    stands = (receipt is not None and green is not None and receipt > green
              and all(receipt > t for t in lifted))
    return {"waiting": waiting, "stands": stands}


def hold_pass(slug: str, holds, *, gql=linear_ops.gql) -> list[str]:
    """Hold the waiting children of this repo's approved epics, and tell each
    epic once. `holds` is `sweep_holds.read`'s record; a repo it does not hold
    at max_wip 0 is not this pass's, and nothing is read.

    One read when no epic of this repo is In Progress, two when one is, and
    one whole-thread read per partial window. A failed read raises
    `linear_ops.LinearError` before anything is written; a failed write
    raises out of the pass too, so the pass that half-wrote a hold goes red."""
    if not holds.max_wip_held:
        return []
    found = _paged(gql, EPICS_QUERY, {"needle": slug, "states": _EPIC_LANES})
    # An epic is a card with children here, never its title: an approved
    # epic with no child has nothing waiting on the cap, and an In Progress
    # one-off is a build, not an approval.
    epics = [e for e in found
             if _repo_of(e) == slug and (e.get("children") or {}).get("nodes")]
    if not epics:
        return [f"{LOG_PREFIX} no approved epic of {slug} is In Progress; nothing written"]
    epics.sort(key=lambda e: _number(e.get("identifier")))

    lines: list[str] = []
    known = []
    for epic in epics:
        if mid_epic.green_light_from((epic.get("history") or {}).get("nodes")) is None:
            lines.append(f"{LOG_PREFIX} {epic['identifier']} — green light unreadable; "
                         "nothing written")
        else:
            known.append(epic)
    if not known:
        return lines

    found = _paged(gql, CHILDREN_QUERY, {"ids": [e["id"] for e in known],
                                         "states": _QUEUE_LANES})
    by_epic: dict = {}
    for child in found:
        parent = (child.get("parent") or {}).get("identifier")
        if _repo_of(child) == slug and parent:
            by_epic.setdefault(parent, []).append(child)

    plans = []
    for epic in known:
        name = epic["identifier"]
        children = sorted(by_epic.get(name, []), key=lambda c: _number(c.get("identifier")))
        try:
            plan = _plan_epic(epic, children)
        except _Unreadable as unread:
            lines.append(f"{LOG_PREFIX} {name} — {unread.what} unreadable; nothing written")
            continue
        if not plan["waiting"]:
            lines.append(f"{LOG_PREFIX} {name} — no Queued child waits on the cap; "
                         "nothing written")
            continue
        plans.append((name, plan))

    # Every read is done. Now the writes: the holds first, so the receipt
    # describes holds that stand, then the receipt.
    for name, plan in plans:
        held = []
        for child in plan["waiting"]:
            if child["hold"] is None:
                _hold(child["identifier"])
                child["hold"] = HOLD_REASON
                held.append(child["identifier"])
            elif child["hold"] == UNSTAMPED:
                lines.append(f"{LOG_PREFIX} {name} — {child['identifier']} wears "
                             "needs-human with no hold stamp; left for a person")
        ids = ", ".join(held) or "none"
        if plan["stands"]:
            lines.append(f"{LOG_PREFIX} {name} — notice already stands; held {ids}")
            continue
        _post_receipt(name, _detail(name, slug, holds, plan["waiting"]))
        lines.append(f"{LOG_PREFIX} {name} — notice posted; held {ids}")
    return lines


# --------------------------------------------------------------------------- #
# lift_pass                                                                    #
# --------------------------------------------------------------------------- #


def lift_pass(slug: str, cap: int, *, gql=linear_ops.gql) -> list[str]:
    """Lift every `wip-cap-held` hold on this repo's Queued cards, now that
    the cap is above 0. Every other hold, and a label with no live stamp, is
    left alone. One read, plus one whole-thread read per partial window."""
    if cap <= 0:
        return []
    found = _paged(gql, HELD_QUERY, {"needle": slug, "states": _QUEUE_LANES})
    cards = sorted((c for c in found if _repo_of(c) == slug),
                   key=lambda c: _number(c.get("identifier")))
    lines: list[str] = []
    due = []
    for card in cards:
        try:
            bodies = _bodies(_thread(card))
        except _Unreadable as unread:
            lines.append(f"{LOG_PREFIX} {card['identifier']} — {unread.what} "
                         "unreadable; nothing written")
            continue
        if hold.reason_of(linear_ops._label_names(card), bodies) != HOLD_REASON:
            continue
        if (hold.read_stamp(bodies) or {}).get("reason") == HOLD_REASON:
            due.append(card["identifier"])
    for identifier in due:
        hold.lift(identifier, because=LIFT_KIND, by=WRITER, reason=HOLD_REASON)
        lines.append(f"{LOG_PREFIX} lifted {identifier} ({LIFT_KIND}, max_wip {cap})")
    return lines
