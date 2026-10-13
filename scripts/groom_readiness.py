#!/usr/bin/env python3
"""Readiness rules as data — what keeps a card off the Planning list (DRE-6665).

The groomer's batch was the first twenty cards of the rules' order, and
nothing in the rules asked whether a card was ready to be planned: its ranked
read's "wait for X" changed nothing, a file collision was checked only against
other Intake cards, a child went without its epic, and an open "decide before
building" section was not read at all. On 2026-10-10 a person removed five
such cards from proposal `a4f198411b02` by hand.

This module is the rule set the groomer applies before the batch is cut, with
no model call. It is pure: `evaluate(card, board)` reads one card against
facts the run read off the board that morning and returns every rule the card
trips, as `{"rule", "names", "reason", "condition"}` rendered from
`config/groom-readiness.json`. A card that trips any rule takes no slot and
is listed on the proposal's "Not ready" list with its reason and condition.
Nothing is remembered: the facts are read again every morning, so a card
returns to the Planning list by itself on the first morning its condition
holds.

## The five rules evaluated here, by id

1. `read-waits-on-open-card` — the ranked read's `depends on` line names an
   open card. A named card with no child and no `[EPIC]` title holds until it
   is Done; a named epic holds until it is built out or Done, read exactly as
   the sweep's epic gate reads a blocker (`reconcile._not_built_out`,
   DRE-6407). The card's own parent never holds it — that is DRE-6664's
   in-flight case.
2. `file-named-by-in-flight-card` — a file the card names in backticks is
   named by a card in Planning, Todo, In Progress or In Review.
3. `child-of-epic-not-in-progress` — the card's parent epic has not started.
4. `undecided-question` — a `decide before building` section with a question
   still beneath it, and no `## Decisions from the CEO` block.
5. `roll-up-parent` — a child of the card is an epic of its own.

The sixth record, the parked repo, is applied by `groomer.held_repo_rows`
before anything here runs (DRE-3403).

## How the named cards are read

In two steps (`read_named`, `read_epics`), and neither aliases
`epic_cap.IN_MOTION_NODE` over a list. The light read asks `NAMED_NODE` for at
most `NAMED_PAGE` ids a request; the epic read asks `IN_MOTION_NODE` once for
each named card the light read shows In Progress and an epic. A read that
fails — `lops.gql` raises, or Linear returns no issue — leaves that card
`unread`, and an unread card holds every card naming it. Nothing is retried in
the run; the next morning reads again.

Run: python3 scripts/groom_readiness.py check
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answer_into_card  # noqa: E402 — the CEO's decisions block, one heading
import epic_cap  # noqa: E402 — buildable(), counts_against_cap(), IN_MOTION_NODE
import groomer  # noqa: E402 — file references, repo labels, the boilerplate rule
import mid_epic  # noqa: E402 — the one reading of "is this card an epic"
from lane_contract import QUEUE_LANES  # noqa: E402 — the queue, read under either name (DRE-6742)

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "groom-readiness.json"

#: The `applied_by` of a rule evaluated here.
APPLIED_HERE = "groom_readiness"

#: The open lanes a card named on a `depends on` line holds the card from.
#: Done, Canceled and Duplicate clear it (`standards/card-quality.md`:
#: "Canceled clears the blocker without claiming delivery"). The queue is read
#: under every name it carries, so a card the moved writers file in Queued
#: holds its dependents as one in the old lane did (DRE-6742).
OPEN_STATES = ("Planning", "Green Light", *QUEUE_LANES, "Todo", "In Progress",
               "In Review")

#: The lanes whose cards own the files they name, for rule 2. Each is read
#: once a morning with `groomer.read_population` (`groomer._readiness`).
IN_FLIGHT_LANES = ("Planning", "Todo", "In Progress", "In Review")

#: A parent epic in one of these has not started, so its child waits (rule 3).
#: Intake is the lane itself: an epic in the same population is a member of
#: the same unit, and the epic card is what the rule leaves in the walk.
PARENT_NOT_STARTED = ("Intake", "Planning", "Green Light", *QUEUE_LANES, "Todo",
                      "Triage")

#: Rule 4 reads a `##` or `###` heading whose text contains this, any case.
DECIDE = "decide before building"

# The light read's page and node. Linear prices a request on the nodes it COULD
# return (`epic_cap.py` records the 5,000-node yardstick). One alias is the
# issue, its state, a one-wide children connection and its one probe — about
# four nodes — so fifty aliases are about two hundred, far under the yardstick,
# and the number of distinct ids a morning's `depends on` lines name is not
# bounded, so the page is what bounds a request. `IN_MOTION_NODE` is NOT
# aliased over a list: Linear prices it at up to 1,200 nodes, which is why
# `epic_cap.IN_MOTION_PAGE` is four, and why the epic read below is one
# request per epic. `tests/test_groom_readiness.py` recomputes the alias
# weight off the selection's own `first:`.
NAMED_PAGE = 50
NAMED_NODE = "identifier title state { name } children(first: 1) { nodes { id } }"

#: The epic read: one single-issue request per named epic In Progress, the
#: shape `reconcile._BLOCKER_EPIC_QUERY` reads a blocker epic with.
EPIC_QUERY = """query($id: String!) { issue(id: $id) {%s
         } }""" % epic_cap.IN_MOTION_NODE

#: Why a named card is unread when Linear answered without it.
NO_ISSUE = "Linear returned no issue for it"

_CARD_REF = re.compile(r"\b(DRE-\d+)\b")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(\S.*?)[ \t]*$")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
#: The placeholders a template may carry (`config/groom-readiness.json`).
PLACEHOLDERS = ("names", "state", "files", "why", "question", "repo")


# --------------------------------------------------------------------------- #
# the data file                                                                #
# --------------------------------------------------------------------------- #

def load(path: str | Path | None = None) -> list[dict]:
    """The rule records, in order."""
    with open(path or RULES_PATH, encoding="utf-8") as fh:
        return list(json.load(fh)["rules"])


def check(path: str | Path | None = None) -> list[str]:
    """Every way the data file and the evaluators disagree, or `[]`."""
    try:
        rules = load(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"cannot read the readiness rules at {path or RULES_PATH}: {e}"]
    found = []
    ids = [r.get("id") for r in rules]
    for dup in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        found.append(f"rule id {dup} is recorded twice")
    here = [r.get("id") for r in rules if r.get("applied_by") == APPLIED_HERE]
    for rule in here:
        if rule not in EVALUATORS:
            found.append(f"rule {rule} is applied_by {APPLIED_HERE} but has no "
                         f"evaluator in scripts/groom_readiness.py")
    for rule in EVALUATORS:
        if rule not in here:
            found.append(f"evaluator {rule} has no record applied_by "
                         f"{APPLIED_HERE} in config/groom-readiness.json")
    for r in rules:
        applier = r.get("applied_by")
        if applier != APPLIED_HERE and not callable(getattr(groomer, str(applier),
                                                            None)):
            found.append(f"rule {r.get('id')} is applied_by {applier}, which "
                         f"is neither this module nor a function in groomer.py")
        for key in ("reason", "condition"):
            if not str(r.get(key) or "").strip():
                found.append(f"rule {r.get('id')} has no {key}")
        for key, text in r.items():
            if key in ("id", "applied_by") or not isinstance(text, str):
                continue
            for name in _PLACEHOLDER.findall(text):
                if name not in PLACEHOLDERS:
                    found.append(f"rule {r.get('id')}'s {key} carries an "
                                 f"unknown placeholder {{{name}}}")
    return found


def _say(record: dict, key: str, **values) -> str:
    return record[key].format(**{name: values.get(name, "")
                                 for name in PLACEHOLDERS})


def _row(record: dict, names: list[str], said: list[tuple[str, str]]) -> dict:
    """One rule tripped: every clause's reason joined with `; `, every
    condition with ` and `, each said once."""
    return {"rule": record["id"], "names": names,
            "reason": "; ".join(dict.fromkeys(r for r, _ in said)),
            "condition": " and ".join(dict.fromkeys(c for _, c in said))}


# --------------------------------------------------------------------------- #
# reading the board                                                            #
# --------------------------------------------------------------------------- #

def depends_on(verdict) -> str | None:
    """The `depends on` labelled reason of a verdict, or None.

    The parser gives labelled reasons to a `now` line only
    (`groom_judgement._verdict`), so on a live morning this is the `now`
    verdict's line; read off the verdict as it stands, so a replay that adds
    the line reads it the same way."""
    if verdict is None:
        return None
    return (getattr(verdict, "reasons", None) or {}).get("depends on")


def _named_on(card: dict, verdicts: dict | None) -> list[str]:
    """The card ids `card`'s `depends on` line names, less its own parent and
    itself, in the order written."""
    text = depends_on((verdicts or {}).get(card.get("identifier")))
    skip = {card.get("identifier"), (card.get("parent") or {}).get("identifier")}
    return [i for i in dict.fromkeys(_CARD_REF.findall(text or ""))
            if i not in skip]


def named_ids(cards: list[dict], verdicts: dict | None) -> list[str]:
    """Every distinct id named on the population's `depends on` lines, less
    each card's own parent — what the light read asks for."""
    found = {i for c in cards for i in _named_on(c, verdicts)}
    return sorted(found, key=groomer._card_sort_key)


def depends_on_missing(verdicts: dict | None) -> int:
    """How many `now` verdicts carry no `depends on` line at all — what rule 1
    could not see. A diagnostic of the read, not a measure of the rule."""
    return sum(1 for v in (verdicts or {}).values()
               if getattr(v, "outcome", None) == "now" and
               "depends on" not in (getattr(v, "reasons", None) or {}))


def named_query(count: int) -> str:
    """The light read for `count` ids: one alias each, every alias selecting
    exactly `NAMED_NODE`, the ids as variables."""
    params = ", ".join(f"$i{n}: String!" for n in range(count))
    aliases = " ".join(f"n{n}: issue(id: $i{n}) {{ {NAMED_NODE} }}"
                       for n in range(count))
    return f"query({params}) {{ {aliases} }}"


def _why(e: BaseException) -> str:
    return " ".join((str(e) or type(e).__name__).split())


def read_named(lops, ids: list[str]) -> dict:
    """`{id: light record}` for every id, `NAMED_PAGE` a request — or
    `{id: {"unread": why}}` when its request raised or Linear returned no
    issue for it. One attempt each; nothing is retried in the run."""
    out: dict = {}
    ids = list(ids)
    for start in range(0, len(ids), NAMED_PAGE):
        chunk = ids[start:start + NAMED_PAGE]
        variables = {f"i{n}": identifier for n, identifier in enumerate(chunk)}
        try:
            data = lops.gql(named_query(len(chunk)), variables) or {}
        except Exception as e:  # noqa: BLE001 — an unread card holds, never guesses
            for identifier in chunk:
                out[identifier] = {"unread": _why(e)}
            continue
        for n, identifier in enumerate(chunk):
            node = data.get(f"n{n}")
            out[identifier] = node if isinstance(node, dict) else {"unread": NO_ISSUE}
    return out


def _is_epic(record: dict) -> bool:
    return mid_epic.is_epic(record.get("title") or "",
                            has_children=bool(epic_cap._children(record)))


def _state(record: dict) -> str:
    return ((record or {}).get("state") or {}).get("name") or ""


def read_epics(lops, named: dict) -> dict:
    """`{id: IN_MOTION_NODE record}` for each named card the light read shows
    In Progress and an epic, one request each — or `{"unread": why}`. A named
    epic in any other state costs nothing: one that is not In Progress is not
    built out, and a Done one needs no read."""
    out: dict = {}
    for identifier, record in named.items():
        if "unread" in record or _state(record) != epic_cap.IN_PROGRESS \
                or not _is_epic(record):
            continue
        try:
            issue = (lops.gql(EPIC_QUERY, {"id": identifier}) or {}).get("issue")
        except Exception as e:  # noqa: BLE001 — an unread epic holds, never guesses
            out[identifier] = {"unread": _why(e)}
            continue
        out[identifier] = issue if isinstance(issue, dict) else {"unread": NO_ISSUE}
    return out


def _lane_rows(cards: list[dict]) -> list[dict]:
    return [{"identifier": c.get("identifier"), "repo": groomer.repo_of(c),
             "files": sorted(groomer.file_references(c.get("description")))}
            for c in cards or []]


def facts(*, lanes: dict | None = None, named: dict | None = None,
          epics: dict | None = None) -> dict:
    """The morning's board facts — `propose`'s `readiness` argument: each
    in-flight lane's file map, the named cards' light reads and the named
    epics' records. JSON, so a proposal record or a fixture can carry it."""
    return {"lanes": {lane: _lane_rows((lanes or {}).get(lane) or [])
                      for lane in IN_FLIGHT_LANES},
            "named": dict(named or {}), "epics": dict(epics or {})}


def read(lops, cards: list[dict], verdicts: dict | None, *,
         lanes: dict) -> dict:
    """Read the named cards for `cards`' `depends on` lines and return
    `facts(...)` with `lanes` — the in-flight lanes, already read."""
    named = read_named(lops, named_ids(cards, verdicts))
    epics = read_epics(lops, named)
    return facts(lanes=lanes, named=named, epics=epics)


def board(readiness: dict, *, population=(), verdicts: dict | None = None,
          threshold: int | None = None) -> dict:
    """What `evaluate` reads: the facts, the verdicts, and the files cited so
    often across the five lanes that they are reference, not ownership —
    `collision_report`'s boilerplate rule, over Intake and the four lanes."""
    threshold = groomer.BOILERPLATE_THRESHOLD if threshold is None else threshold
    counts: Counter = Counter()
    for c in population:
        counts.update(groomer.file_references(c.get("description")))
    for rows in (readiness.get("lanes") or {}).values():
        for row in rows:
            counts.update(set(row.get("files") or ()))
    return {**readiness, "verdicts": verdicts or {}, "rules": load(),
            "boilerplate": {name for name, n in counts.items() if n > threshold}}


# --------------------------------------------------------------------------- #
# the rules                                                                    #
# --------------------------------------------------------------------------- #

def built_out(record: dict) -> bool:
    """Is this In Progress epic built out — `reconcile._not_built_out`
    answering None (DRE-6407)? In Progress, with children, and
    `epic_cap.counts_against_cap(record, count_rollup_parents=True)` false: no
    child `buildable()`, no open child epic, the children page read to the
    end."""
    return (_state(record) == epic_cap.IN_PROGRESS
            and bool(epic_cap._children(record))
            and not epic_cap.counts_against_cap(record,
                                                count_rollup_parents=True))


def _waits_on_open_card(card: dict, board: dict, record: dict) -> dict | None:
    names, said = [], []
    for named in _named_on(card, board.get("verdicts")):
        light = (board.get("named") or {}).get(named)
        if light is None or "unread" in light:
            why = (light or {}).get("unread") or "it was not read"
            said.append((_say(record, "unread_reason", names=named, why=why),
                         _say(record, "epic_condition", names=named)))
            names.append(named)
            continue
        state = _state(light)
        if state not in OPEN_STATES:
            continue
        if not _is_epic(light):
            said.append((_say(record, "reason", names=named, state=state),
                         _say(record, "condition", names=named)))
            names.append(named)
            continue
        if state == epic_cap.IN_PROGRESS:
            epic = (board.get("epics") or {}).get(named)
            if epic is None or "unread" in epic:
                why = (epic or {}).get("unread") or "its children were not read"
                said.append((_say(record, "unread_reason", names=named, why=why),
                             _say(record, "epic_condition", names=named)))
                names.append(named)
                continue
            if built_out(epic):
                continue
        said.append((_say(record, "epic_reason", names=named, state=state),
                     _say(record, "epic_condition", names=named)))
        names.append(named)
    return _row(record, names, said) if names else None


def _file_named_in_flight(card: dict, board: dict, record: dict) -> dict | None:
    mine = groomer.file_references(card.get("description")) - \
        set(board.get("boilerplate") or ())
    if not mine:
        return None
    repo = groomer.repo_of(card)
    names, said = [], []
    for lane in IN_FLIGHT_LANES:
        for other in (board.get("lanes") or {}).get(lane) or []:
            # Same repo only, as `collision_report` reads a collision: a
            # shared basename in two repositories is two different files.
            if other.get("identifier") == card.get("identifier") \
                    or other.get("repo") != repo:
                continue
            shared = sorted(mine & set(other.get("files") or ()))
            if not shared:
                continue
            files = ", ".join(f"`{f}`" for f in shared)
            said.append((_say(record, "reason", names=other["identifier"],
                              state=lane, files=files),
                         _say(record, "condition", names=other["identifier"])))
            names.append(other["identifier"])
    return _row(record, names, said) if names else None


def _child_of_epic_not_started(card: dict, board: dict, record: dict) -> dict | None:
    parent = card.get("parent") or {}
    state = _state(parent)
    if not parent.get("identifier") or state not in PARENT_NOT_STARTED:
        return None
    named = parent["identifier"]
    return _row(record, [named], [(_say(record, "reason", names=named, state=state),
                                   _say(record, "condition", names=named))])


def open_question(description: str | None) -> dict | None:
    """The first open `decide before building` question, or None.

    `{"heading", "item", "more"}`: a `##` or `###` heading whose text carries
    `DECIDE`, any case, with at least one list item beneath it before the next
    heading of its level or higher. None whatever the headings say when the
    card carries the CEO's `## Decisions from the CEO` block
    (`answer_into_card.HEADING`), read the way that module reads it."""
    lines = (description or "").splitlines()
    if any(line.rstrip() == answer_into_card.HEADING for line in lines):
        return None
    for start, line in enumerate(lines):
        heading = _HEADING.match(line)
        if not heading or len(heading.group(1)) not in (2, 3) \
                or DECIDE not in heading.group(2).lower():
            continue
        level, items = len(heading.group(1)), []
        for below in lines[start + 1:]:
            ends = _HEADING.match(below)
            if ends and len(ends.group(1)) <= level:
                break
            item = _ITEM.match(below)
            if item:
                items.append(" ".join(item.group(1).split()))
        if items:
            return {"heading": " ".join(heading.group(2).split()),
                    "item": items[0], "more": len(items) - 1}
    return None


def _undecided_question(card: dict, board: dict, record: dict) -> dict | None:
    found = open_question(card.get("description"))
    if found is None:
        return None
    # Cut as a table cell is cut (`groomer._cell`); the page escapes the pipes
    # and defangs the whole cell when it renders the row.
    question = f'"{groomer._trim(found["item"])}"'
    if found["more"]:
        question += f" (+{found['more']} more)"
    question += f' under "{groomer._trim(found["heading"])}"'
    return _row(record, [], [(_say(record, "reason", question=question),
                              _say(record, "condition"))])


def _roll_up_parent(card: dict, board: dict, record: dict) -> dict | None:
    epics = [c.get("identifier") for c in
             ((card.get("children") or {}).get("nodes") or [])
             if (c.get("title") or "").lstrip().lower().startswith("[epic]")]
    epics = sorted((e for e in epics if e), key=groomer._card_sort_key)
    if not epics:
        return None
    return _row(record, epics, [(_say(record, "reason", names=", ".join(epics)),
                                 _say(record, "condition"))])


#: The five evaluated rules, by the id their record carries.
EVALUATORS = {
    "read-waits-on-open-card": _waits_on_open_card,
    "file-named-by-in-flight-card": _file_named_in_flight,
    "child-of-epic-not-in-progress": _child_of_epic_not_started,
    "undecided-question": _undecided_question,
    "roll-up-parent": _roll_up_parent,
}


def evaluate(card: dict, board: dict) -> list[dict]:
    """Every rule `card` trips, in the data file's order —
    `[{"rule", "names", "reason", "condition"}]`, `[]` when it is ready."""
    out = []
    for record in board.get("rules") or load():
        rule = EVALUATORS.get(record.get("id"))
        if record.get("applied_by") != APPLIED_HERE or rule is None:
            continue
        row = rule(card, board, record)
        if row is not None:
            out.append(row)
    return out


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="the data file and the evaluators "
                                           "agree on the rule ids")
    p_check.add_argument("--rules", default=None,
                         help="the data file to check (default %s)" % RULES_PATH)
    args = parser.parse_args(argv)
    problems = check(args.rules)
    for problem in problems:
        print(f"groom-readiness: {problem}")
    if problems:
        return 1
    rules = load(args.rules)
    print(f"groom-readiness: {len(rules)} rule(s), {len(EVALUATORS)} "
          f"evaluated here — the data file and the evaluators agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
