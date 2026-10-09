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
     with the planning exit's own note, it is stamped FLEET, and a Hand-work
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

CLI:

    python3 scripts/hand_work_migration.py census
    python3 scripts/hand_work_migration.py run                # dry run, the default
    python3 scripts/hand_work_migration.py run --apply
    python3 scripts/hand_work_migration.py run [--apply] [--include DRE-N …]
        [--include-actor NAME …] [--operator-step DRE-N …]
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
import linear_ops  # noqa: E402
import plan_critic  # noqa: E402 — the critic's own record reader
import proof_and_demo  # noqa: E402 — the one proof test
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


def _apply_one(ops, row: dict, now: datetime) -> tuple[list[str], bool]:
    """Every write for one card, in order. `(what changed, refused)`."""
    ident = row["identifier"]
    done = []
    ops.remove_label(ident, HAND_BUILT)
    done.append(f"label removed (`{HAND_BUILT}`)")
    if row["action"] == SWAP:
        ops.add_label(ident, OPERATOR_STEP)
        done.append(f"label added (`{OPERATOR_STEP}`)")
    if row["action"] == RESTAMP:
        retired = routing_verdict.retiring(row["nodes"], None)
        if retired:
            retirement = routing_verdict.retirement_comment(retired, None, now=now)
            ops.cmd_comment(ident, retirement)
        if routing_verdict.stamp_card(ident, FLEET, fleet_why(row), title=row["title"]) != 0:
            return done, True
        names = ", ".join(dict.fromkeys(n for n, _ in routing_verdict.retired_pairs(retired)))
        done.append(f"verdict restamped ({FLEET}"
                    + (f", after retiring {names}" if names else "") + ")")
    if row["move_to"] == BACKLOG:
        if ops.cmd_state(ident, BACKLOG, expect=(HAND_WORK,)) is False:
            return done, True
        done.append(f"moved to {BACKLOG}")
    elif row["move_to"] == PLANNING:
        if ops.cmd_state(ident, PLANNING, expect=(row["lane"],)) is False:
            return done, True
        done.append(f"moved to {PLANNING}")
    return done, False


def run(ops, rows: list[dict], *, apply: bool = False,
        now: datetime | None = None) -> dict:
    """Make every row's writes. Writes nothing unless `apply`.

    Each card's lane is re-read first, and a card that moved since the census
    is refused untouched. The move itself is also conditional (`expect`), so a
    card that moves between the re-read and the write is refused by the write
    layer, not dragged back. The comment follows the writes that happened."""
    now = now or datetime.now(UTC)
    changed, refused, failed = [], [], []
    for row in rows:
        if row["action"] not in WRITING or not apply:
            continue
        ident = row["identifier"]
        try:
            lane = current_lane(ops, ident)
            if lane != row["lane"]:
                print(f"refused {ident}: it was in {row['lane']} when read and is in "
                      f"{lane or 'an unreadable lane'} now — left untouched")
                refused.append(ident)
                continue
            done, was_refused = _apply_one(ops, row, now)
            ops.cmd_comment(ident, migration_note(row, done))
        except LinearError as e:
            print(f"FAILED {ident}: {e}", file=sys.stderr)
            failed.append(ident)
            continue
        changed.append(ident)
        if was_refused:
            print(f"refused {ident} part-way: {'; '.join(done)} — the rest was refused")
            refused.append(ident)
    return {"applied": apply, "changed": changed, "refused": refused, "failed": failed}


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
    args = parser.parse_args(argv)

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
