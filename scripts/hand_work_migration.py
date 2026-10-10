#!/usr/bin/env python3
"""The hand-built migration: every automatically applied `hand-built` handled
once, dry run first (DRE-6230, epic DRE-6219).

The CEO's rule of 2026-10-07 is that `hand-built` is his mark, applied on his
words, and nothing automatic applies it. Until DRE-6226/6227/6228 the pipeline
did — the sweep stamped it on every WORKBENCH and OPERATOR card it carried to
Hand-work, the record and retired-repo writers set it at creation — so on
2026-10-09 67 open cards carried it, 49 of them applied by the pipeline's own
identities. With the writers stopped, this script takes each of those off once.
The operator decided on 2026-10-08 that no pipeline-applied `hand-built`
survives it, so there is no switch that keeps an automatic card out.

This file held DRE-5323's one-time move of person cards out of Todo; it is
rewritten for this migration rather than a second script written beside it.
`docs/hand-work-migration.md` is the record of that first run.

## Origin first

The actor is the one named on the history entry that added `hand-built` (the
newest, if it was added more than once); when no entry added it, the label was
set at creation — Linear writes no history row for that — and the actor is the
card's `creator`. A history Linear says it did not return whole, with no entry
in it, could still hold one, so it is not read as "set at creation".

  * **automatic** — the actor is one of the non-human identities
    `config/linear-identities.json` declares (read off that file, never
    listed here), or an account named by `--include-actor NAME`; or the card is
    named by `--include DRE-N`.
  * **kept** — any other actor is a person's hand: listed, untouched.
  * **could not tell** — no actor at all: listed, untouched.

## Then the class, in order, on every automatic card

  1. **epic** (`epic_todo_gate.is_epic_card`) — loses `hand-built`, gains
     `operator-step`, stays in its lane.
  2. **proof** (`proof_and_demo.is_proof`) — loses `hand-built` and nothing
     else: it keeps `no-code`, and the proof dispatch takes it from Hand-work.
  3. **operator step** — carries `no-code` or `needs-human`, or its title routes
     OPERATOR by `routing_verdict.title_verdict`, or opens `[OPERATOR]` /
     `OPERATOR:`, or it is named by `--operator-step DRE-N` — loses
     `hand-built`, gains `operator-step`.
  4. **code** — loses `hand-built`, then by lane. In Hand-work or Backlog with
     a critic pass on record it is not re-planned: every live verdict is retired
     with a `🪦 verdict-retired` note naming this migration as the retirer, the
     `operator-step` a retired verdict put on comes off first (the planning
     exit's `lifted_marks` rule), it is stamped FLEET, and a Hand-work
     card moves to Backlog, where the sweep's promotion carries it to Todo. In
     Hand-work or Backlog with no pass it moves to Planning, whose exit writes it
     a fresh verdict. Anywhere else it only loses the label.

A critic pass is read with `plan_critic`'s own record reader over the fleet's
own comments: for a parentless card, the newest one-off round on the card is a
PASS; for a child, the parent epic's second critic released the plan
(`plan_critic.post_release`).

Every card changed carries one `🧳 hand-built-migration:` comment saying what
changed and the rule it followed. This script never writes Todo.

It refuses to run, before any write, when Linear (or the identity declaration)
cannot be read. No card id appears in its code: selection is by rule.

## The operator-backlog pass (DRE-6429)

Operator cards filed before DRE-6428 sit in Backlog wearing `needs-human` +
`no-code` with no hold stamp, so `hold.reason_of` reads them `manual` and the
sweep (DRE-6427) leaves them alone by design. `operator-backlog` converts them
once. It reads every Backlog card wearing both labels that is not an epic and
not a proof, whose hold is `manual` or already `operator-step`, and lists each
under one of four headings, with its repo, title and reason:

  * **unblocked** — `manual`, every blocker terminal, no parent or one In
    Progress: marked `operator-step`, stamped, stamped OPERATOR when it carries
    no live verdict, lifted (`blockers-terminal`) and moved Backlog → Hand-work.
  * **waiting** — `manual`, with an open blocker or a parent not In Progress:
    marked `operator-step` and stamped, nothing else. The sweep lifts it later.
  * **due** — already `operator-step` and nothing left to wait on: lifted and
    moved, as the sweep would.
  * **not touched** — already `operator-step` and still waiting, or a verdict
    that routes anywhere but Hand-work (a FLEET card is a person's to read).

Dry run by default; `--apply` writes. A card moved since the read is refused
untouched, and the move itself is conditional on Backlog. A second run finds
nothing new to write: the moved cards have left Backlog and the waiting ones
read `operator-step`.

CLI:

    python3 scripts/hand_work_migration.py census
    python3 scripts/hand_work_migration.py run                # dry run, the default
    python3 scripts/hand_work_migration.py run --apply
    python3 scripts/hand_work_migration.py run [--apply] [--include DRE-N …]
        [--include-actor NAME …] [--operator-step DRE-N …]
    python3 scripts/hand_work_migration.py operator-backlog   # dry run, the default
    python3 scripts/hand_work_migration.py operator-backlog --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_linear_identities  # noqa: E402 — the declared non-human identities
import dead_run  # noqa: E402 — `HOLD_LABEL`, the needs-human label
import epic_todo_gate  # noqa: E402 — the one epic test (DRE-5316)
import hold  # noqa: E402 — the hold's stamp, lift and reason (DRE-6173)
import linear_ops  # noqa: E402
import plan_critic  # noqa: E402 — the critic's own record reader
import proof_and_demo  # noqa: E402 — the one proof test
import prose_blockers  # noqa: E402 — the sweep's own blocker gate
import routing_verdict  # noqa: E402

HAND_BUILT = routing_verdict.HAND_BUILT_LABEL
OPERATOR_STEP = routing_verdict.OPERATOR_STEP_LABEL
NO_CODE = linear_ops.NO_CODE_LABEL
NEEDS_HUMAN = dead_run.HOLD_LABEL

HAND_WORK = "Hand-work"
BACKLOG = "Backlog"
PLANNING = "Planning"
CLOSED = ("Done", "Canceled", "Duplicate")
#: The lanes a code card is moved out of; anywhere else it only loses the label.
RETURNABLE = (HAND_WORK, BACKLOG)

FLEET = "FLEET"
OPERATOR_VERDICT = "OPERATOR"
FLEET_IDENTITY = "fleet"

MARK = "🧳"
TAG = "hand-built-migration"
#: The line every migration comment opens with.
OPENER = f"{MARK} {TAG}:"
#: The opening of the FLEET restamp's `why`.
WHY_OPENER = "hand-built migration:"

#: The CEO's words, 2026-10-07 (`standards/card-quality.md`).
CEO_RULE = ("Build it and let everyone know when i say built it, I'm saying this "
            "is a hand built card.")

# The classes, in the order they are tried.
EPIC = "epic"
PROOF = "proof"
OPERATOR = "operator step"
CODE = "code"
CLASSES = (EPIC, PROOF, OPERATOR, CODE)

# The actions, in the order the census prints them.
SWAP = "swap"        # loses hand-built, gains operator-step, stays in its lane
REMOVE = "remove"    # loses hand-built and nothing else
RESTAMP = "restamp"  # loses hand-built, verdicts retired, FLEET stamped
REPLAN = "replan"    # loses hand-built, moves to Planning
KEEP = "keep"        # a person's hand — untouched
UNTOLD = "untold"    # no actor — untouched
ACTIONS = (SWAP, REMOVE, RESTAMP, REPLAN, KEEP, UNTOLD)
WRITING = (SWAP, REMOVE, RESTAMP, REPLAN)
ACTION_TITLES = {
    SWAP: f"label swapped for {OPERATOR_STEP}",
    REMOVE: "label removed",
    RESTAMP: f"restamped {FLEET}",
    REPLAN: f"moved to {PLANNING}",
    KEEP: "kept — applied by hand",
    UNTOLD: "could not tell — left alone",
}

#: Anchored at the start of the title, never a substring search.
_OPERATOR_TITLE = re.compile(r"^\s*(?:\[OPERATOR\]|OPERATOR:)", re.IGNORECASE)
_CARD_ID = re.compile(r"^DRE-\d+$")

#: Every open card carrying the mark, in any lane, with what the rules read.
#: Paginated: `gql_paged` refuses a query that cannot be. The history and the
#: comments carry `pageInfo` so a window Linear did not return whole is known.
POPULATION_QUERY = """query($label: String!, $after: String) {
  issues(first: 10, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    labels: {name: {eqIgnoreCase: $label}},
    state: {name: {nin: %s}}
  }) {
    nodes {
      id identifier title createdAt
      state { name }
      creator { name }
      labels { nodes { name } }
      parent { identifier state { name } }
      children(first: 1) { nodes { id } }
      history(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes { createdAt actor { name } addedLabels { name } }
      }
      comments(first: %d) {
        pageInfo { hasNextPage endCursor }
        nodes { body createdAt user { id name } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}""" % (json.dumps(list(CLOSED)), linear_ops.COMMENT_WINDOW)

#: One card's whole thread, with who wrote each comment, a hundred at a time
#: toward the oldest (Linear hands comments back newest first).
THREAD_QUERY = """query($id: String!, $after: String) { issue(id: $id) {
  comments(first: 100, after: $after) { pageInfo { hasNextPage endCursor }
    nodes { body createdAt user { name } } } } }"""

#: The lane a card is in now — re-read before any write to it.
LANE_QUERY = """query($id: String!) { issue(id: $id) { state { name } } }"""

#: Bound at import so a test's stand-in for the write layer need not carry it.
LinearError = linear_ops.LinearError
_window_nodes = linear_ops.window_nodes
_window_is_partial = linear_ops.window_is_partial


class Unreadable(Exception):
    """Linear could not be read, so nothing is decided and nothing is written."""


# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def non_human_actors(doc: dict | None = None) -> frozenset:
    """The display names of every identity the declaration says is not a
    person — read off `config/linear-identities.json`, never listed here."""
    try:
        rows = check_linear_identities.identities(doc or check_linear_identities.load())
        return frozenset(row["display_name"] for row in rows)
    except (check_linear_identities.IdentityError, KeyError, TypeError) as e:
        raise Unreadable(f"could not read the identity declaration ({e})") from e


def fleet_actor(doc: dict | None = None) -> str:
    """The fleet's display name: the one author whose critic records count."""
    try:
        rows = check_linear_identities.identities(doc or check_linear_identities.load())
        return next(row["display_name"] for row in rows if row.get("name") == FLEET_IDENTITY)
    except (check_linear_identities.IdentityError, KeyError, TypeError,
            StopIteration) as e:
        raise Unreadable(f"the identity declaration names no fleet user ({e})") from e


def read_population(ops) -> list[dict]:
    """Every open card carrying the mark, or `Unreadable` — never an empty
    list for a failure."""
    try:
        cards = ops.gql_paged(POPULATION_QUERY, {"label": HAND_BUILT})
    except Exception as e:  # noqa: BLE001 — LinearError, a network error, anything
        raise Unreadable(f"could not read the cards carrying {HAND_BUILT} ({e})") from e
    return [c for c in cards if _lane(c) not in CLOSED]


def read_thread(ops, identifier: str) -> list[dict]:
    """`identifier`'s whole thread, oldest→newest, or `Unreadable`."""
    nodes, after, seen = [], None, set()
    try:
        while True:
            data = ops.gql(THREAD_QUERY, {"id": identifier, "after": after})
            conn = ((data.get("issue") or {}).get("comments")) or {}
            nodes += list(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
            after = info.get("endCursor")
            if not info.get("hasNextPage") or not after or after in seen:
                break
            seen.add(after)
    except Exception as e:  # noqa: BLE001
        raise Unreadable(f"could not read {identifier}'s comments ({e})") from e
    return list(reversed(nodes))


def current_lane(ops, identifier: str) -> str | None:
    data = ops.gql(LANE_QUERY, {"id": identifier})
    return (((data.get("issue") or {}).get("state")) or {}).get("name")


def _lane(card: dict) -> str:
    return ((card.get("state") or {}).get("name")) or ""


def _labels(card: dict) -> list[str]:
    return [(n.get("name") or "") for n in ((card.get("labels") or {}).get("nodes") or [])]


def _has_children(card: dict) -> bool:
    return bool(((card.get("children") or {}).get("nodes")) or [])


def _parent(card: dict) -> str | None:
    return (card.get("parent") or {}).get("identifier")


def _when(iso: str | None) -> datetime:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def thread_of(ops, card: dict) -> list[dict]:
    """The card's comments oldest→newest: the window, or the whole thread when
    the window is not all of it — a verdict past the window is still live."""
    if _window_is_partial(card.get("comments")):
        return read_thread(ops, card["identifier"])
    return _window_nodes(card.get("comments"))


# --------------------------------------------------------------------------- #
# origin                                                                       #
# --------------------------------------------------------------------------- #


def origin(card: dict) -> tuple[str | None, str | None, str]:
    """`(actor, how, why)`: who applied the mark and how it was read —
    `history` or `creation` — or `(None, None, why it could not be told)`."""
    history = card.get("history") or {}
    adds = [n for n in (history.get("nodes") or [])
            if any(_same(lbl.get("name"), HAND_BUILT) for lbl in (n.get("addedLabels") or []))]
    if adds:
        newest = max(adds, key=lambda n: _when(n.get("createdAt")))
        actor = ((newest.get("actor") or {}).get("name") or "").strip()
        if actor:
            return actor, "history", ""
        return None, None, "the history entry that added it names no actor"
    if (history.get("pageInfo") or {}).get("hasNextPage"):
        return None, None, ("no entry in the history Linear returned added it, and "
                            "that history is not the whole of it")
    creator = ((card.get("creator") or {}).get("name") or "").strip()
    if creator:
        return creator, "creation", ""
    return None, None, "no history entry added it and the card names no creator"


def _origin_text(row: dict) -> str:
    if row["actor"] is None:
        return "no actor"
    how = "history entry" if row["origin"] == "history" else "set at creation"
    return f"applied by {row['actor']} ({how})"


# --------------------------------------------------------------------------- #
# class                                                                        #
# --------------------------------------------------------------------------- #


def classify(card: dict, bodies: list[str], operator_steps=()) -> str:
    """One of `CLASSES`, tried in order."""
    title = card.get("title") or ""
    if epic_todo_gate.is_epic_card(title, _has_children(card), bodies):
        return EPIC
    if proof_and_demo.is_proof(title):
        return PROOF
    labels = {label.casefold() for label in _labels(card)}
    if (card.get("identifier") in operator_steps
            or NO_CODE.casefold() in labels or NEEDS_HUMAN.casefold() in labels
            or routing_verdict.title_verdict(title) == OPERATOR_VERDICT
            or _OPERATOR_TITLE.match(title)):
        return OPERATOR
    return CODE


def _records(nodes: list[dict], fleet: str) -> list[dict]:
    """A thread as `plan_critic` reads it: a record counts only when the fleet
    wrote it — by name, so the answer is the same whichever key runs this."""
    return [{"body": n.get("body") or "",
             "authored_by_pipeline": _same(((n.get("user") or {}).get("name")), fleet),
             "created_at": n.get("createdAt")}
            for n in nodes]


def critic_pass(card: dict, nodes: list[dict], parent_nodes: list[dict] | None,
                fleet: str) -> str | None:
    """What critic pass this card has on record, in words, or None."""
    parent = _parent(card)
    if parent:
        state, _ = plan_critic.post_release(_records(parent_nodes or [], fleet), parent)
        if state == plan_critic.POST_RELEASED:
            return f"the second critic released the plan of its epic {parent}"
        return None
    rounds = [r for r in plan_critic.parse_markers(_records(nodes, fleet))
              if r["stage"] == plan_critic.STAGE_ONE_OFF]
    if rounds and rounds[-1]["result"] == plan_critic.PASS:
        return f"the one-off critic passed it (round {rounds[-1]['round']})"
    return None


# --------------------------------------------------------------------------- #
# the census                                                                   #
# --------------------------------------------------------------------------- #


def census(cards: list[dict], ops, *, include=(), include_actors=(),
           operator_steps=(), identities: dict | None = None) -> list[dict]:
    """One row per card: its origin, class and what will happen to it.

    `ops` is the read layer, asked for a thread the window does not hold and
    for a parent epic's thread. Raises `Unreadable` when one cannot be read —
    every read happens here, before `run` writes anything."""
    automatic = {name.casefold() for name in non_human_actors(identities)}
    automatic |= {name.strip().casefold() for name in include_actors}
    fleet = fleet_actor(identities)
    parents: dict[str, list[dict]] = {}
    rows = []
    for card in cards:
        ident = card.get("identifier")
        lane = _lane(card)
        actor, how, untold = origin(card)
        if ident in include:
            is_auto = True
        elif actor is None:
            is_auto = None
        else:
            is_auto = actor.casefold() in automatic
        row = {
            "identifier": ident,
            "title": card.get("title") or "",
            "lane": lane,
            "labels": _labels(card),
            "parent": _parent(card),
            "actor": actor,
            "origin": how,
            "automatic": is_auto,
            "class": None,
            "action": KEEP if is_auto is False else UNTOLD,
            "move_to": None,
            "passed": None,
            "nodes": [],
        }
        if is_auto is False:
            row["summary"] = f"kept — applied by hand by {actor}"
            rows.append(row)
            continue
        if is_auto is None:
            row["summary"] = f"could not tell — left alone ({untold})"
            rows.append(row)
            continue
        nodes = thread_of(ops, card)
        row["nodes"] = nodes
        cls = classify(card, [n.get("body") or "" for n in nodes], operator_steps)
        row["class"] = cls
        if cls in (EPIC, OPERATOR):
            row["action"] = SWAP
            row["summary"] = (f"an {cls}: loses {HAND_BUILT}, gains {OPERATOR_STEP}, "
                              f"stays in {lane}")
        elif cls == PROOF:
            row["action"] = REMOVE
            row["summary"] = (f"a proof: loses {HAND_BUILT} and nothing else, stays "
                              f"in {lane}")
        elif lane not in RETURNABLE:
            row["action"] = REMOVE
            row["summary"] = f"a code card in {lane}: loses {HAND_BUILT} only"
        else:
            parent = row["parent"]
            if parent and parent not in parents:
                parents[parent] = read_thread(ops, parent)
            row["passed"] = critic_pass(card, nodes, parents.get(parent), fleet)
            if row["passed"]:
                row["action"] = RESTAMP
                row["move_to"] = BACKLOG if lane == HAND_WORK else None
                row["summary"] = (
                    f"a code card with a critic pass ({row['passed']}): loses "
                    f"{HAND_BUILT}, its live verdicts retired, stamped {FLEET}"
                    + (f", {HAND_WORK} → {BACKLOG}" if row["move_to"] else
                       f", stays in {lane}"))
            else:
                row["action"] = REPLAN
                row["move_to"] = PLANNING
                row["summary"] = (f"a code card with no critic pass: loses {HAND_BUILT}, "
                                  f"{lane} → {PLANNING}")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# the run                                                                      #
# --------------------------------------------------------------------------- #

_RULES = {
    EPIC: (f"an epic is never built from a lane, so it carries the operator's "
           f"marker, `{OPERATOR_STEP}`, in place of the CEO's — the operator's "
           "decision of 2026-10-08. It stays in its lane."),
    PROOF: ("a proof keeps `no-code` and its lane; the proof dispatch takes it "
            f"from {HAND_WORK}, and nothing about it needs the CEO's mark."),
    OPERATOR: (f"an operator step carries the operator's marker, `{OPERATOR_STEP}`, "
               "in place of the CEO's, and stays in its lane."),
    REMOVE: ("a code card in this lane only loses the mark: Intake classifies it, "
             "and a card with a pull request is already underway."),
    RESTAMP: ("a code card with a critic pass on record is not re-planned: its old "
              f"verdict is retired on the record and it is stamped {FLEET}, so the "
              f"sweep's promotion carries it from {BACKLOG} to the fleet on the "
              f"gates every {FLEET} card passes."),
    REPLAN: (f"a code card with no critic pass goes back to {PLANNING}, whose exit "
             "writes it a fresh routing verdict."),
}


def _rule(row: dict) -> str:
    if row["class"] == CODE:
        return _RULES[row["action"]]
    return _RULES[row["class"]]


def fleet_why(row: dict) -> str:
    return (f"{WHY_OPENER} a code card with a critic pass on record — "
            f"{row['passed']} — so it is not re-planned; the `{HAND_BUILT}` the "
            "pipeline applied came off, and the fleet builds it.")


def _pacific(iso: str | None, unknown: str) -> str:
    """`iso` as a Pacific time a person reads, or `unknown`."""
    at = _when(iso)
    return unknown if at == datetime.min.replace(tzinfo=UTC) else dead_run.pacific(at)


def retirement_note(retired: tuple, lifted: tuple, row: dict, now: datetime) -> str:
    """The note that retires `retired` before the FLEET restamp. It opens the
    way the planning exit's note does and names each verdict by fingerprint,
    so `routing_verdict.verdicts_on` reads it the same — but the words are this
    migration's: the card never went back to Planning, and the verdict written
    next is the migration's `FLEET`, not Planning's."""
    pairs = routing_verdict.retired_pairs(retired)
    names = list(dict.fromkeys(name for name, _ in pairs))
    lines = [
        f"{routing_verdict.RETIRED_MARK} {routing_verdict.RETIRED_TAG}: "
        + " and ".join(f"**{name}**" for name in names)
        + " — retired by the one-time hand-built migration, so "
        + ("that verdict no longer routes" if len(pairs) == 1 else
           "those verdicts no longer route")
        + " the card.",
        "",
    ]
    for (name, print_), node in zip(pairs, retired):
        written = _pacific(node.get("createdAt"), "at a time Linear did not report")
        lines.append(f"- **{name}**, written {written} — `retired:{print_}`")
    took_off = (" " + ", ".join(f"`{m}`" for m in lifted) + " the old verdict put on "
                "came off before this note.") if lifted else ""
    lines += [
        "",
        f"Retired on {dead_run.pacific(now)} by the hand-built migration, not by "
        f"Planning: this card did not go back to {PLANNING}. It is a code card with "
        f"a critic pass on record — {row['passed']} — so the migration stamps "
        f"{FLEET} next, and that is the card's one live verdict. The old decision "
        f"stays on the card as the record.{took_off}",
    ]
    return "\n".join(lines)


def migration_note(row: dict, done: list[str]) -> str:
    """The comment a changed card carries, opening with `OPENER`."""
    return (
        f"{OPENER} {'; '.join(done)}.\n\n"
        f"**What it read:** `{HAND_BUILT}` was put on this card automatically — "
        f"{_origin_text(row)} — and the card is {'an' if row['class'] in (EPIC, OPERATOR) else 'a'} "
        f"{row['class']}{' card' if row['class'] == CODE else ''} in {row['lane']}.\n\n"
        f"**The rule it followed:** {_rule(row)}\n\n"
        f"The CEO's rule of 2026-10-07: \"{CEO_RULE}\" The mark is his alone, so "
        "the one-time hand-built migration takes off every one the pipeline "
        "applied."
    )


def lifted(row: dict, retired: tuple) -> tuple:
    """The marks a retired verdict put on that the `FLEET` stamp does not —
    `routing_verdict.lifted_marks`, the planning exit's own rule — and that the
    card carries. `hand-built` is not among them: it already came off."""
    names = tuple(dict.fromkeys(n for n, _ in routing_verdict.retired_pairs(retired)))
    carried = {label.casefold() for label in row["labels"]}
    return tuple(m for m in routing_verdict.lifted_marks(names, FLEET)
                 if m != HAND_BUILT and m.casefold() in carried)


def _apply_one(ops, row: dict, now: datetime, done: list[str]) -> bool:
    """Every write for one card, in order, each recorded in `done` as it
    lands — so a write that raises still leaves what came before it said.
    True when a write was refused."""
    ident = row["identifier"]
    ops.remove_label(ident, HAND_BUILT)
    done.append(f"label removed (`{HAND_BUILT}`)")
    if row["action"] == SWAP:
        ops.add_label(ident, OPERATOR_STEP)
        done.append(f"label added (`{OPERATOR_STEP}`)")
    if row["action"] == RESTAMP:
        retired = routing_verdict.retiring(row["nodes"], None)
        if retired:
            # Labels FIRST, then the note, as the planning exit does: a run
            # that died between them would leave the note standing and the old
            # verdict's `operator-step` on a card the sweep then reads as a
            # person's — one nothing builds.
            took_off = lifted(row, retired)
            for label in took_off:
                ops.remove_label(ident, label)
                done.append(f"label removed (`{label}`)")
            retirement = retirement_note(retired, took_off, row, now)
            ops.cmd_comment(ident, retirement)
        if routing_verdict.stamp_card(ident, FLEET, fleet_why(row), title=row["title"]) != 0:
            return True
        names = ", ".join(dict.fromkeys(n for n, _ in routing_verdict.retired_pairs(retired)))
        done.append(f"verdict restamped ({FLEET}"
                    + (f", after retiring {names}" if names else "") + ")")
    if row["move_to"] == BACKLOG:
        if ops.cmd_state(ident, BACKLOG, expect=(HAND_WORK,)) is False:
            return True
        done.append(f"moved to {BACKLOG}")
    elif row["move_to"] == PLANNING:
        if ops.cmd_state(ident, PLANNING, expect=(row["lane"],)) is False:
            return True
        done.append(f"moved to {PLANNING}")
    return False


def run(ops, rows: list[dict], *, apply: bool = False,
        now: datetime | None = None) -> dict:
    """Make every row's writes. Writes nothing unless `apply`.

    Each card's lane is re-read first, and a card that moved since the census
    is refused untouched. The move itself is also conditional (`expect`), so a
    card that moves between the re-read and the write is refused by the write
    layer, not dragged back. The comment follows the writes that happened.

    A card whose write raised is listed under `failed`, and printed with the
    writes that had already landed: it no longer carries `hand-built` once the first one
    has, so a re-run would not find it again."""
    now = now or datetime.now(UTC)
    changed, refused, failed = [], [], []
    for row in rows:
        if row["action"] not in WRITING or not apply:
            continue
        ident = row["identifier"]
        done: list[str] = []
        try:
            lane = current_lane(ops, ident)
            if lane != row["lane"]:
                print(f"refused {ident}: it was in {row['lane']} when read and is in "
                      f"{lane or 'an unreadable lane'} now — left untouched")
                refused.append(ident)
                continue
            was_refused = _apply_one(ops, row, now, done)
            ops.cmd_comment(ident, migration_note(row, done))
        except LinearError as e:
            written = (f" — already written: {'; '.join(done)}; it no longer carries "
                       f"`{HAND_BUILT}`, so a re-run will not find it" if done else "")
            print(f"FAILED {ident}: {e}{written}", file=sys.stderr)
            failed.append(ident)
            continue
        changed.append(ident)
        if was_refused:
            print(f"refused {ident} part-way: {'; '.join(done)} — the rest was refused")
            refused.append(ident)
    return {"applied": apply, "changed": changed, "refused": refused, "failed": failed}


# --------------------------------------------------------------------------- #
# the operator-backlog pass (DRE-6429)                                         #
# --------------------------------------------------------------------------- #

#: The writer the stamp and the lift line name.
WRITER = "hand_work_migration.py"
#: The hold a person's label with no live stamp reads (`hold.reason_of`).
MANUAL = "manual"
#: The lane a parent epic must be in for its child to move — the sweep's
#: `reconcile.EPIC_ACTIVE_STATES`.
EPIC_ACTIVE = "In Progress"

# The four headings, in the order they print.
UNBLOCKED = "unblocked"
WAITING = "waiting"
DUE = "due"
UNTOUCHED = "not touched"
SECTIONS = (UNBLOCKED, WAITING, DUE, UNTOUCHED)

#: Every Backlog card wearing the hold, with what the rules read. The relations
#: carry `pageInfo` so a card with more blockers than one page is read to the end.
OPERATOR_BACKLOG_QUERY = """query($label: String!, $after: String) {
  issues(first: 10, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    labels: {name: {eqIgnoreCase: $label}},
    state: {name: {eq: "%s"}}
  }) {
    nodes {
      id identifier title
      state { name }
      labels { nodes { name } }
      parent { identifier state { name } }
      children(first: 1) { nodes { id } }
      inverseRelations(first: 50) {
        pageInfo { hasNextPage endCursor }
        nodes { type issue { identifier state { name } } }
      }
      comments(first: %d) {
        pageInfo { hasNextPage endCursor }
        nodes { body createdAt user { id name } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}""" % (BACKLOG, linear_ops.COMMENT_WINDOW)

#: The rest of one card's relations, a hundred at a time.
RELATIONS_QUERY = """query($id: String!, $after: String) { issue(id: $id) {
  inverseRelations(first: 100, after: $after) { pageInfo { hasNextPage endCursor }
    nodes { type issue { identifier state { name } } } } } }"""


def read_operator_backlog(ops) -> list[dict]:
    """Every Backlog card carrying the hold, or `Unreadable`."""
    try:
        cards = ops.gql_paged(OPERATOR_BACKLOG_QUERY, {"label": NEEDS_HUMAN})
    except Exception as e:  # noqa: BLE001 — LinearError, a network error, anything
        raise Unreadable(f"could not read the {BACKLOG} cards carrying {NEEDS_HUMAN} "
                         f"({e})") from e
    return [c for c in cards if _lane(c) == BACKLOG]


def read_relations(ops, card: dict) -> dict:
    """The card with its relations read to the end, or `Unreadable` — a
    blocker past the first page is still a blocker."""
    ident = card.get("identifier")
    page = card.get("inverseRelations") or {}
    nodes = list(page.get("nodes") or [])
    info = page.get("pageInfo") or {}
    seen: set = set()
    try:
        while info.get("hasNextPage"):
            after = info.get("endCursor")
            if not after or after in seen:
                raise Unreadable(f"{ident}'s relations go on past a page Linear gave "
                                 "no fresh cursor for")
            seen.add(after)
            data = ops.gql(RELATIONS_QUERY, {"id": ident, "after": after})
            conn = ((data or {}).get("issue") or {}).get("inverseRelations") or {}
            nodes += list(conn.get("nodes") or [])
            info = conn.get("pageInfo") or {}
    except Unreadable:
        raise
    except Exception as e:  # noqa: BLE001
        raise Unreadable(f"could not read {ident}'s relations ({e})") from e
    return {**card, "inverseRelations": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}


def _repo(labels: list[str]) -> str:
    slugs = [label.split(":", 1)[1] for label in labels
             if label.casefold().startswith("repo:")]
    return ", ".join(slugs) or "no repo"


def _waits(card: dict) -> list[str]:
    """What the card still waits on, in words; empty when nothing. The
    blockers are `prose_blockers.relation_blockers`, the sweep's own gate."""
    waits = []
    states = prose_blockers.blocker_states(card)
    open_ = sorted(prose_blockers.relation_blockers(card))
    if open_:
        waits.append("blocked by " + ", ".join(f"{i} ({states[i]})" for i in open_))
    parent = card.get("parent")
    if parent and _lane(parent) != EPIC_ACTIVE:
        waits.append(f"its epic {parent.get('identifier')} is in "
                     f"{_lane(parent) or 'an unread lane'}, not {EPIC_ACTIVE}")
    return waits


def operator_census(cards: list[dict], ops) -> list[dict]:
    """One row per selected card, under one of `SECTIONS`. Every read happens
    here — a thread past the window, the relations past the first page — so
    an unreadable card raises `Unreadable` before anything is written."""
    rows = []
    for card in cards:
        labels = _labels(card)
        carried = {label.casefold() for label in labels}
        if NEEDS_HUMAN.casefold() not in carried or NO_CODE.casefold() not in carried:
            continue
        title = card.get("title") or ""
        nodes = thread_of(ops, card)
        bodies = [n.get("body") or "" for n in nodes]
        if (epic_todo_gate.is_epic_card(title, _has_children(card), bodies)
                or proof_and_demo.is_proof(title)):
            continue
        reason = hold.reason_of(labels, bodies)
        if reason not in (MANUAL, hold.OPERATOR_STEP_REASON):
            continue
        row = {
            "identifier": card.get("identifier"),
            "title": title,
            "repo": _repo(labels),
            "labels": labels,
            "stamped": reason == hold.OPERATOR_STEP_REASON,
            "verdict": None,
            "section": UNTOUCHED,
            "summary": "",
        }
        rows.append(row)
        try:
            row["verdict"] = routing_verdict.verdict_on(bodies)
        except routing_verdict.ConflictingVerdicts as e:
            row["summary"] = f"{e} — a person reads it"
            continue
        verdict = row["verdict"]
        lands = routing_verdict.destination(OPERATOR_VERDICT)
        if verdict and routing_verdict.destination(verdict) != lands:
            row["summary"] = (f"its routing verdict is {verdict}, which sends it to "
                              f"{routing_verdict.destination(verdict)}, not {lands} — "
                              "the hold and the verdict disagree, and a person reads that")
            continue
        waits = _waits(read_relations(ops, card))
        restamp = "" if verdict else f", stamped {OPERATOR_VERDICT}"
        if row["stamped"] and waits:
            row["summary"] = (f"held {hold.OPERATOR_STEP_REASON} and still waiting — "
                              f"{'; '.join(waits)}; the sweep lifts it when that clears")
        elif row["stamped"]:
            row["section"] = DUE
            row["summary"] = (f"held {hold.OPERATOR_STEP_REASON} and nothing left to wait "
                              f"on: lifted{restamp}, {BACKLOG} → {HAND_WORK}")
        elif waits:
            row["section"] = WAITING
            row["summary"] = (f"held {MANUAL}, waiting — {'; '.join(waits)}: marked "
                              f"{OPERATOR_STEP} and stamped, so the sweep lifts it later")
        else:
            row["section"] = UNBLOCKED
            row["summary"] = (f"held {MANUAL}, nothing left to wait on: marked "
                              f"{OPERATOR_STEP}, stamped{restamp}, lifted, "
                              f"{BACKLOG} → {HAND_WORK}")
    return rows


def operator_why(row: dict) -> str:
    return (f"operator-backlog pass: a `{NEEDS_HUMAN}` + `{NO_CODE}` card filed before "
            "its hold was stamped, with no routing verdict and nothing left to wait "
            f"on — an operator step, so it goes to {HAND_WORK}.")


def _convert_one(ops, row: dict, done: list[str]) -> bool:
    """Every write for one card of the operator-backlog pass, in order, each
    recorded in `done` as it lands. True when a write was refused.

    The marker, then the hold's stamp — so the card reads `operator-step`
    before anything else happens to it, and a run that dies here leaves a
    hold the sweep lifts. A waiting card stops there. Otherwise the OPERATOR
    verdict when it carries none, the lift, and last the move, conditional
    on the lane it was read in."""
    ident = row["identifier"]
    if OPERATOR_STEP.casefold() not in {label.casefold() for label in row["labels"]}:
        ops.add_label(ident, OPERATOR_STEP)
        done.append(f"label added (`{OPERATOR_STEP}`)")
    if not row["stamped"]:
        hold.apply(ident, hold.OPERATOR_STEP_REASON, None, WRITER)
        done.append(f"hold stamped ({hold.OPERATOR_STEP_REASON})")
    if row["section"] == WAITING:
        return False
    if row["verdict"] is None:
        if routing_verdict.stamp_card(ident, OPERATOR_VERDICT, operator_why(row),
                                      title=row["title"]) != 0:
            return True
        done.append(f"verdict stamped ({OPERATOR_VERDICT})")
    hold.lift(ident, hold.BLOCKERS_TERMINAL, WRITER, reason=hold.OPERATOR_STEP_REASON)
    done.append(f"hold lifted ({hold.BLOCKERS_TERMINAL})")
    if ops.cmd_state(ident, HAND_WORK, expect=(BACKLOG,)) is False:
        return True
    done.append(f"moved to {HAND_WORK}")
    return False


def convert(ops, rows: list[dict], *, apply: bool = False) -> dict:
    """Make every listed card's writes. Writes nothing unless `apply`; never
    writes a card under `not touched`. Each card's lane is re-read first, and
    one that left Backlog since the read is refused untouched."""
    changed, refused, failed = [], [], []
    for row in rows:
        if row["section"] == UNTOUCHED or not apply:
            continue
        ident = row["identifier"]
        done: list[str] = []
        try:
            lane = current_lane(ops, ident)
            if lane != BACKLOG:
                print(f"refused {ident}: it was in {BACKLOG} when read and is in "
                      f"{lane or 'an unreadable lane'} now — left untouched")
                refused.append(ident)
                continue
            was_refused = _convert_one(ops, row, done)
        except (LinearError, ValueError) as e:
            written = f" — already written: {'; '.join(done)}" if done else ""
            print(f"FAILED {ident}: {e}{written}", file=sys.stderr)
            failed.append(ident)
            continue
        changed.append(ident)
        if was_refused:
            print(f"refused {ident} part-way: {'; '.join(done) or 'nothing written'} "
                  "— the rest was refused")
            refused.append(ident)
    return {"applied": apply, "changed": changed, "refused": refused, "failed": failed}


def render_operator_backlog(rows: list[dict]) -> str:
    lines = []
    for section in SECTIONS:
        group = [r for r in rows if r["section"] == section]
        lines.append(f"{section} ({len(group)}):")
        if not group:
            lines.append("  none")
        for r in group:
            lines.append(f"  {r['identifier']:<10} {r['repo']:<16} {r['title'][:60]}\n"
                         f"             → {r['summary']}")
    counts = ", ".join(f"{s} {sum(r['section'] == s for r in rows)}" for s in SECTIONS)
    lines.append(f"\n{len(rows)} card(s): {counts}")
    return "\n".join(lines)


def operator_backlog(apply: bool) -> int:
    try:
        rows = operator_census(read_operator_backlog(linear_ops), linear_ops)
    except Unreadable as e:
        print(f"ERROR: {e} — refusing to run; nothing was written.", file=sys.stderr)
        return 2
    print(render_operator_backlog(rows))
    result = convert(linear_ops, rows, apply=apply)
    if not apply:
        print("\ndry run — nothing was written. Re-run with --apply.")
        return 0
    print(f"\nchanged {len(result['changed'])} card(s): "
          f"{', '.join(result['changed']) or 'none'}")
    if result["refused"]:
        print(f"refused (moved since the read, or a write refused): "
              f"{', '.join(result['refused'])}")
    if result["failed"]:
        print(f"FAILED: {', '.join(result['failed'])}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #


def render(rows: list[dict]) -> str:
    lines = []
    for action in ACTIONS:
        group = [r for r in rows if r["action"] == action]
        if not group:
            continue
        lines.append(f"{ACTION_TITLES[action]} ({len(group)}):")
        for r in group:
            lines.append(
                f"  {r['identifier']:<10} {r['title'][:60]}\n"
                f"             lane: {r['lane']}; origin: {_origin_text(r)}; "
                f"class: {r['class'] or 'not read'}\n"
                f"             → {r['summary']}"
            )
    counts = ", ".join(f"{ACTION_TITLES[a]} {sum(r['action'] == a for r in rows)}"
                       for a in ACTIONS)
    lines.append(f"\n{len(rows)} card(s): {counts}")
    return "\n".join(lines)


def _ids(values) -> list[str]:
    return [v.strip().upper() for v in (values or [])]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    switches = argparse.ArgumentParser(add_help=False)
    switches.add_argument("--include", nargs="+", action="extend", default=[],
                          metavar="DRE-N", help="treat this card as automatic")
    switches.add_argument("--include-actor", nargs="+", action="extend", default=[],
                          metavar="NAME", help="treat every card this account marked as automatic")
    switches.add_argument("--operator-step", nargs="+", action="extend", default=[],
                          metavar="DRE-N", help="class this card as an operator step")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("census", parents=[switches],
                   help="every card with its origin, class and action")
    p_run = sub.add_parser("run", parents=[switches],
                           help="make the writes (needs --apply to write)")
    p_run.add_argument("--apply", action="store_true")
    p_ops = sub.add_parser("operator-backlog",
                           help="convert the manual operator holds in Backlog")
    p_ops.add_argument("--apply", action="store_true",
                       help="make the writes; without it, a dry run")
    args = parser.parse_args(argv)
    if args.command == "operator-backlog":
        return operator_backlog(args.apply)

    include, operator_steps = _ids(args.include), _ids(args.operator_step)
    bad = [i for i in include + operator_steps if not _CARD_ID.match(i)]
    if bad:
        print(f"ERROR: not a card identifier: {', '.join(bad)}", file=sys.stderr)
        return 2

    try:
        cards = read_population(linear_ops)
        rows = census(cards, linear_ops, include=include,
                      include_actors=args.include_actor, operator_steps=operator_steps)
    except Unreadable as e:
        print(f"ERROR: {e} — refusing to run; nothing was written.", file=sys.stderr)
        return 2

    print(render(rows))
    present = {c.get("identifier") for c in cards}
    for ident in dict.fromkeys(include + operator_steps):
        if ident not in present:
            print(f"{ident}: not among the open cards carrying {HAND_BUILT} — nothing to do")

    if args.command == "census":
        return 0
    result = run(linear_ops, rows, apply=args.apply)
    if not args.apply:
        print("\ndry run — nothing was written. Re-run with --apply.")
        return 0
    print(f"\nchanged {len(result['changed'])} card(s): "
          f"{', '.join(result['changed']) or 'none'}")
    if result["refused"]:
        print(f"refused (moved since the read, or a write refused): "
              f"{', '.join(result['refused'])}")
    if result["failed"]:
        print(f"FAILED: {', '.join(result['failed'])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
