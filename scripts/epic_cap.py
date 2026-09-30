#!/usr/bin/env python3
"""The epic cap, as data (DRE-5134).

The CEO capped the epics in motion at 15 on 2026-09-28: 45 epics were In
Progress, a sort of them found 14 real builds and 16 close-outs, and the cap
fits the 14 builds plus one slot. `config/epic-cap.json` is the single copy of
that number, and this module is the single counting rule on the pipeline side.
The operator console reads the same file over the GitHub contents API.

Three things live here and nowhere else:

  * **the counting rule** — an epic counts iff it is In Progress, it has at
    least one child, and (unless `count_rollup_parents`) at least one child is
    not itself an epic. A roll-up holds no cards of its own and takes no slot.
  * **the order of the line** — Linear priority (Urgent, High, Medium, Low,
    none last), then approval time (the OLDEST entry into In Progress after
    the newest entry into Planning, `createdAt` as the fallback), then the
    identifier.
  * **the decision** the approval gate makes — `start` or `queue`, in four
    rules, each named on stderr when it answers.

The waiting line is every `Green Light` epic carrying `epic-queued`. The label
alone is not the line: an epic that left Green Light any other way has left
the line (`labeled_elsewhere()` finds those for the sweep).

Nothing here posts a comment or moves a card. The receipt bodies are composed
here, WITHOUT the pipeline-act trailer; the card that posts one wraps it with
`pipeline_act.receipt`.

CLI:

    python3 scripts/epic_cap.py check                  # validate the file
    python3 scripts/epic_cap.py show                   # the count and the line
    python3 scripts/epic_cap.py decide --epic DRE-N [--receipt-file PATH]

`decide` prints exactly `start` or `queue`. Exit 2: the cap file could not be
read. Exit 3: Linear could not be read — with `--receipt-file`, the unread
receipt is in the file, so the gate holds the approval instead of letting it
through unread.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import linear_ops  # noqa: E402
import mid_epic  # noqa: E402 — ONE rule for "a child is an epic"

ROOT = os.path.dirname(_HERE)
#: Resolved off this script's own directory, so a product-repo checkout finds
#: it under `.bureau-pipeline/config/` exactly as `lane_contract.py` does.
CAP_PATH = os.path.join(ROOT, "config", "epic-cap.json")

QUEUED_LABEL = "epic-queued"
QUEUED_TAG = "epic-queued"
STARTED_TAG = "epic-started"
REDISPATCHED_TAG = "epic-start-redispatched"
QUEUED_ACT = "epic-approval-queued"
STARTED_ACT = "epic-queue-started"
REDISPATCHED_ACT = "epic-start-redispatched"

#: The one repo whose periodic sweep starts waiting epics (DRE-5152).
START_OWNER_SLUG = "bureau-pipeline"

#: A child in one of these lanes is the record that the epic has already run:
#: only the activate route promotes a child out of Backlog. Read off the
#: epic's own children, never off its comment thread — `count_comments`
#: reads the fifty newest comments, and a running epic's `▶️ Epic activated`
#: comment falls out of that window within a day.
ACTIVATED_STATES = ("Todo", "In Progress", "In Review", "Done")

IN_PROGRESS = "In Progress"
GREEN_LIGHT = "Green Light"
PLANNING = "Planning"

#: Linear numbers priority 0 none, 1 Urgent, 2 High, 3 Medium, 4 Low
#: (`scripts/groomer.py` reads the same field). None ranks after Low.
PRIORITY_NAMES = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
_NO_PRIORITY_RANK = 5

HISTORY_WINDOW = 250

# Page sizes. Linear prices a request on the nodes it COULD return and refuses
# one priced too high — `reconcile.EPIC_RECORD_PAGE` records it. The yardstick
# this repo knows Linear answers is `backlog_children`: 100 cards × a
# 50-comment window = 5,000 nodes, live since DRE-2929. Both pages are sized
# under it, and `tests/test_epic_cap.py` recomputes each weight off the
# selection's own `first:`/`last:` numbers, so a connection widened later fails
# there rather than at Linear.
#
# An In Progress node can return 250 children and 250 grandchild probes: 500
# nodes, so 8 a page is 4,000.
IN_MOTION_PAGE = 8
# A waiting node can return 250 history entries: 16 a page is 4,000.
WAITING_PAGE = 16
LABELED_PAGE = 100

IN_MOTION_NODE = """
             identifier title priority createdAt state { name }
             children(first: 250) { nodes {
               identifier title children(first: 1) { nodes { id } }
             } }"""

WAITING_NODE = """
             identifier title priority createdAt state { name }
             history(last: 250) { nodes { createdAt toState { name } } }"""

# `last:` is deliberate: `history(first: n)` is the n NEWEST entries and
# `history(last: n)` the n OLDEST, ascending (DRE-5034,
# tests/fixtures/dre-5034-history-2026-09-29.json). The first approval is the
# one wanted, so the oldest end is the one read.

IN_MOTION_QUERY = """query($after: String) {
           issues(first: %d, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "In Progress"}}
           }) { nodes {%s
           } pageInfo { hasNextPage endCursor } } }""" % (IN_MOTION_PAGE, IN_MOTION_NODE)

WAITING_QUERY = """query($after: String) {
           issues(first: %d, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "Green Light"}},
             labels: {name: {eq: "%s"}}
           }) { nodes {%s
           } pageInfo { hasNextPage endCursor } } }""" % (
    WAITING_PAGE, QUEUED_LABEL, WAITING_NODE,
)

LABELED_QUERY = """query($after: String) {
           issues(first: %d, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             labels: {name: {eq: "%s"}},
             state: {name: {neq: "Green Light"}}
           }) { nodes {
             identifier state { name }
           } pageInfo { hasNextPage endCursor } } }""" % (LABELED_PAGE, QUEUED_LABEL)

EPIC_QUERY = """query($id: String!) { issue(id: $id) {
           identifier title priority createdAt state { name }
           history(last: 250) { nodes { createdAt toState { name } } }
           children(first: 250) { nodes { state { name } } }
         } }"""

_FELL_BACK = (
    "(approval time: no In Progress entry among the 250 oldest history "
    "entries; using createdAt)"
)


class EpicCapError(RuntimeError):
    """The cap file is missing or malformed. Raised, never defaulted: a cap
    nobody chose is worse than no answer, because it looks like a decision."""


# --------------------------------------------------------------------------- #
# the file                                                                     #
# --------------------------------------------------------------------------- #

def load(path: str | None = None) -> dict:
    """The cap file, validated. `cap` a positive integer and
    `count_rollup_parents` a boolean; every other key is a record no reader
    depends on."""
    path = path or CAP_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise EpicCapError(f"cannot read the epic cap at {path}: {e}") from e
    if not isinstance(doc, dict):
        raise EpicCapError(f"{path}: expected an object carrying cap and "
                           "count_rollup_parents")
    if "cap" not in doc:
        raise EpicCapError(f"{path}: the key cap is missing")
    cap = doc["cap"]
    # bool is an int in Python; `true` is not a cap anybody chose.
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise EpicCapError(f"{path}: cap must be a positive integer, not {cap!r}")
    if "count_rollup_parents" not in doc:
        raise EpicCapError(f"{path}: the key count_rollup_parents is missing")
    if not isinstance(doc["count_rollup_parents"], bool):
        raise EpicCapError(
            f"{path}: count_rollup_parents must be true or false, not "
            f"{doc['count_rollup_parents']!r}"
        )
    return doc


# --------------------------------------------------------------------------- #
# the counting rule                                                            #
# --------------------------------------------------------------------------- #

def _state(record: dict) -> str:
    return ((record or {}).get("state") or {}).get("name") or ""


def _children(record: dict) -> list:
    return (((record or {}).get("children") or {}).get("nodes")) or []


def _child_is_epic(child: dict) -> bool:
    return mid_epic.is_epic(child.get("title") or "",
                            has_children=bool(_children(child)))


def _rollup_flag(count_rollup_parents: bool | None) -> bool:
    if count_rollup_parents is None:
        return load()["count_rollup_parents"]
    return count_rollup_parents


def counts_against_cap(epic: dict, *, count_rollup_parents: bool | None = None) -> bool:
    """In Progress, at least one child, and (unless roll-up parents count) at
    least one child that is not itself an epic. The flag defaults to the
    file."""
    if _state(epic) != IN_PROGRESS:
        return False
    children = _children(epic)
    if not children:
        return False
    if _rollup_flag(count_rollup_parents):
        return True
    return any(not _child_is_epic(c) for c in children)


def in_motion(epics, *, count_rollup_parents: bool | None = None) -> list:
    """The In Progress epics that count against the cap."""
    flag = _rollup_flag(count_rollup_parents)
    return [e for e in epics if counts_against_cap(e, count_rollup_parents=flag)]


# --------------------------------------------------------------------------- #
# the order of the line                                                        #
# --------------------------------------------------------------------------- #

def _approval_entry(epic: dict) -> str | None:
    """The OLDEST entry into In Progress after the newest entry into Planning
    (after nothing, for an epic never re-planned), or None.

    The CEO moving a queued epic to In Progress again adds a NEWER entry, and
    must not send the epic to the back of its band — so the first approval is
    kept. An epic re-planned and approved again is approved anew."""
    entries = (((epic or {}).get("history") or {}).get("nodes")) or []
    # `history(last: 250)` answers ascending already; the sort is stable and
    # only guards against a window read in the other order.
    entries = sorted(entries, key=lambda n: n.get("createdAt") or "")
    approved = None
    for entry in entries:
        to = ((entry.get("toState") or {}).get("name")) or ""
        if to == PLANNING:
            approved = None
        elif to == IN_PROGRESS and approved is None and entry.get("createdAt"):
            approved = entry["createdAt"]
    return approved


def approval_time(epic: dict) -> str:
    """When this epic was approved in its current planning attempt, falling
    back to its own `createdAt` when no such entry is in the window."""
    return _approval_entry(epic) or (epic or {}).get("createdAt") or ""


def approval_fell_back(epic: dict) -> bool:
    """True when `approval_time` is the epic's `createdAt`, not an approval."""
    return _approval_entry(epic) is None


def priority_rank(priority) -> int:
    """1 Urgent … 4 Low as themselves; 0, none or anything else after Low."""
    try:
        value = int(priority or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value in PRIORITY_NAMES else _NO_PRIORITY_RANK


def _identifier_key(identifier: str):
    """DRE-99 before DRE-100: the team, then the number."""
    team, _, number = (identifier or "").rpartition("-")
    return (team, int(number)) if number.isdigit() else (identifier or "", -1)


def _order_key(epic: dict):
    return (priority_rank(epic.get("priority")), approval_time(epic),
            _identifier_key(epic.get("identifier") or ""))


def queue_order(waiting) -> list:
    """The line: Priority, then approval time oldest first, then identifier."""
    return sorted(waiting, key=_order_key)


def place(identifier: str, waiting) -> tuple[int, int]:
    """`identifier`'s 1-based place in the line, and the line's length."""
    line = queue_order(waiting)
    for k, epic in enumerate(line, start=1):
        if epic.get("identifier") == identifier:
            return k, len(line)
    raise ValueError(f"{identifier} is not in the line")


def free_slots(fleet: dict) -> int:
    return max(0, fleet["cap"] - len(fleet["in_motion"]))


# --------------------------------------------------------------------------- #
# the decision                                                                 #
# --------------------------------------------------------------------------- #

def activated_before(epic: dict) -> bool:
    """A child in one of `ACTIVATED_STATES`: this epic has already run."""
    return any(_state(c) in ACTIVATED_STATES for c in _children(epic))


def _line_with(fleet: dict, identifier: str, epic: dict) -> list:
    """The waiting line with the asked epic inserted, once."""
    record = {**(epic or {}), "identifier": identifier}
    others = [e for e in fleet["waiting"] if e.get("identifier") != identifier]
    return queue_order(others + [record])


def _others_in_motion(fleet: dict, identifier: str) -> list:
    return [e for e in fleet["in_motion"] if e.get("identifier") != identifier]


def _rule(fleet: dict, identifier: str, epic: dict) -> tuple[str, int, str]:
    """(answer, rule number, the line saying why)."""
    cap = fleet["cap"]
    if activated_before(epic):
        return "start", 1, (
            f"{identifier} has a child in {'/'.join(ACTIVATED_STATES)}, so it "
            "has run before — a running epic re-approved after an amendment, or "
            "a re-planned epic with delivered children — and an epic in motion "
            "is never queued"
        )
    others = len(_others_in_motion(fleet, identifier))
    if others >= cap:
        return "queue", 2, (
            f"{others} of {cap} epics are in motion besides {identifier}, at or "
            "above the cap"
        )
    head = _line_with(fleet, identifier, epic)[0]
    if head.get("identifier") != identifier:
        return "queue", 3, (
            f"{others} of {cap} epics are in motion besides {identifier} and a "
            f"slot is free, but {head.get('identifier')} is ahead in line and "
            "takes it"
        )
    return "start", 4, (
        f"{others} of {cap} epics are in motion besides {identifier}, a slot "
        "is free and nobody waiting is ahead of it"
    )


def decision(fleet: dict, identifier: str, epic: dict) -> str:
    """`start` or `queue`, printing on stderr which rule answered.

    1. start — the epic has already run (`activated_before`): an epic in motion
       is never frozen by its own amendment.
    2. queue — the epics in motion OTHER than this one are at or above the cap.
    3. queue — a slot is free but a waiting epic ranks ahead of this one.
    4. start — otherwise.

    Two approvals in the same minute at cap − 1 may both queue (healed by the
    sweep's next pass) or both start (healed when the next epic closes). Both
    windows are the sweep's cadence, and both are accepted."""
    answer, number, why = _rule(fleet, identifier, epic)
    print(f"epic-cap: {answer} — rule {number}: {why}", file=sys.stderr)
    return answer


def receipt_for(fleet: dict, identifier: str, epic: dict) -> str:
    """The queued receipt for `identifier`, its place computed with it in the
    line. Rule 3 names the epic ahead that takes the free slot."""
    _, number, _ = _rule(fleet, identifier, epic)
    line = _line_with(fleet, identifier, epic)
    k, n = place(identifier, line)
    ahead = line[0].get("identifier") if number == 3 else None
    return queued_receipt(k, n, len(_others_in_motion(fleet, identifier)),
                          fleet["cap"], ahead=ahead)


# --------------------------------------------------------------------------- #
# the receipts (bodies only — the posting card adds the pipeline-act trailer)  #
# --------------------------------------------------------------------------- #

_ORDER_SENTENCE = (
    "The line runs in Priority order (Urgent, High, Medium, Low, oldest "
    "approval first)."
)
_MOVE_UP = "Change its Priority to move it up the line."
_NOT_AGAIN = (
    "Moving it to In Progress again neither starts it nor changes its place."
)


def queued_receipt(place: int, total: int, in_motion: int, cap: int, *,
                   ahead: str | None = None) -> str:
    """`⏸️ epic-queued:` — approved and waiting in line. With `ahead`, the
    rule-3 wording: a slot is free and that epic takes it first."""
    if ahead:
        why = (
            f"A slot is free, and {ahead} is ahead of this epic in line — the "
            "sweep starts it first, on its next pass, then this one in its turn."
        )
    else:
        why = (
            f"{in_motion} of {cap} epics are in motion, so the sweep starts "
            "this one when one closes."
        )
    return (
        f"⏸️ {QUEUED_TAG}: approved and waiting in line — place {place} of "
        f"{total}. {why} {_ORDER_SENTENCE} {_MOVE_UP} {_NOT_AGAIN}"
    )


def unread_receipt(reason: str) -> str:
    """`⏸️ epic-queued:` — approved, but the count could not be read."""
    reason = " ".join(str(reason or "no reason given").split())[:300]
    return (
        f"⏸️ {QUEUED_TAG}: approved — but the fleet's count of epics in motion "
        f"could not be read ({reason}). The epic waits in line, and the sweep "
        "starts it on its next pass if there is room. Priority and first "
        f"approval decide its place. {_NOT_AGAIN}"
    )


def started_receipt(place_was: int, in_motion: int, cap: int) -> str:
    """`▶️ epic-started:` — the sweep started a waiting epic. `in_motion`
    counts this one."""
    return (
        f"▶️ {STARTED_TAG}: a slot opened and this epic was next in line "
        f"(place {place_was}) — the sweep started it, and {in_motion} of {cap} "
        "epics are now in motion."
    )


# --------------------------------------------------------------------------- #
# the reads                                                                    #
# --------------------------------------------------------------------------- #

def _paged(query: str) -> tuple[list, int]:
    """`linear_ops.gql_paged`, and how many requests it made.

    The count is measured, not derived from the node count: `gql_paged` stops
    on `hasNextPage`, and a page count computed from the rows would be a claim
    about Linear's paging rather than a record of it."""
    real = linear_ops.gql
    made = [0]

    def counted(q, variables=None):
        made[0] += 1
        return real(q, variables)

    linear_ops.gql = counted
    try:
        nodes = linear_ops.gql_paged(query)
    finally:
        linear_ops.gql = real
    return nodes, made[0]


def _waiting() -> tuple[list, int]:
    nodes, made = _paged(WAITING_QUERY)
    return queue_order(nodes), made


def waiting_line() -> list:
    """The waiting query alone, in queue order — one request while the line is
    short, so a pass with nobody waiting never pays for the in-motion read."""
    return _waiting()[0]


def fleet_state() -> dict:
    """The cap, the epics in motion and the line, in two paged reads.

    The sweep's `_fetch_active_cards` fetches neither priority nor the
    children's own children, so it cannot be reused. In Progress cards with
    no children come back too — there is no server-side has-children filter —
    and `counts_against_cap` drops them."""
    doc = load()
    flag = doc["count_rollup_parents"]
    issues, in_motion_requests = _paged(IN_MOTION_QUERY)
    waiting, waiting_requests = _waiting()
    print(
        f"epic-cap: the fleet read made {in_motion_requests + waiting_requests} "
        f"Linear request(s) — {in_motion_requests} for {len(issues)} In Progress "
        f"issue(s), {waiting_requests} for {len(waiting)} waiting epic(s)",
        file=sys.stderr,
    )
    return {
        "cap": doc["cap"],
        "count_rollup_parents": flag,
        "in_motion": in_motion(issues, count_rollup_parents=flag),
        "waiting": waiting,
    }


def labeled_elsewhere() -> list:
    """Every DRE issue carrying `epic-queued` whose state is not Green Light —
    labeled, and not in the line. The sweep (DRE-5152) strips the label from
    the ones outside In Progress, and confirms the activation of the In
    Progress ones it started."""
    nodes, _ = _paged(LABELED_QUERY)
    return [n for n in nodes if _state(n) != GREEN_LIGHT]


def read_epic(identifier: str) -> dict:
    """The asked epic, one single-issue read — its children's states answer
    `activated_before`."""
    issue = (linear_ops.gql(EPIC_QUERY, {"id": identifier}) or {}).get("issue")
    if not issue:
        raise linear_ops.LinearError(f"Linear returned no issue for {identifier}")
    return {**issue, "identifier": issue.get("identifier") or identifier}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _cmd_check(_args) -> int:
    doc = load()
    print(
        f"epic-cap: {CAP_PATH} ok — cap {doc['cap']}, count_rollup_parents "
        f"{str(doc['count_rollup_parents']).lower()}"
    )
    return 0


def _cmd_show(_args) -> int:
    fleet = fleet_state()
    print(f"Epics in motion: {len(fleet['in_motion'])} of {fleet['cap']}")
    for epic in fleet["in_motion"]:
        print(f"  {epic.get('identifier')}  {epic.get('title') or ''}")
    print(f"Waiting in line: {len(fleet['waiting'])}")
    for k, epic in enumerate(fleet["waiting"], start=1):
        name = PRIORITY_NAMES.get(priority_rank(epic.get("priority")), "No priority")
        line = (f"  {k}. {epic.get('identifier')}  [{name}]  approved "
                f"{approval_time(epic)}  {epic.get('title') or ''}")
        if approval_fell_back(epic):
            line += f"  {_FELL_BACK}"
        print(line)
    return 0


def _write(path: str | None, body: str) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)


def _cmd_decide(args) -> int:
    identifier = args.epic
    load()  # exit 2 before any Linear read when the cap is unreadable
    try:
        fleet = fleet_state()
        epic = read_epic(identifier)
    except (linear_ops.LinearError, OSError) as e:
        print(f"epic-cap: Linear could not be read: {e}", file=sys.stderr)
        _write(args.receipt_file, unread_receipt(str(e)))
        return 3
    answer = decision(fleet, identifier, epic)
    print(answer)
    if answer == "queue":
        _write(args.receipt_file, receipt_for(fleet, identifier, epic))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The epic cap (DRE-5134).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate config/epic-cap.json")
    sub.add_parser("show", help="the epics in motion and the line")
    decide = sub.add_parser("decide", help="start or queue one approved epic")
    decide.add_argument("--epic", required=True)
    decide.add_argument("--receipt-file")
    args = parser.parse_args(argv)
    handler = {"check": _cmd_check, "show": _cmd_show, "decide": _cmd_decide}[args.cmd]
    try:
        return handler(args)
    except EpicCapError as e:
        print(f"epic-cap: {e}", file=sys.stderr)
        return 2
    except (linear_ops.LinearError, OSError) as e:
        print(f"epic-cap: Linear could not be read: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
