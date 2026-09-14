#!/usr/bin/env python3
"""Take the real board and write it as a fixture, in the shape the sweep reads.

    python3 scripts/board_snapshot.py take --out tests/fixtures/board-snapshot-2026-09-12.json

WHY (DRE-3638). The CI ceiling on the reconcile sweep's Linear spend passes at
30 while a live pass costs 65 on bureau-pipeline and 92 on agent-bureau. The
ceiling is not wrong about its own fixture — `tests/test_sweep_request_cuts.py`
hand-builds 5 epics and 260 Backlog cards and mocks 18 of the sweep's phases
out entirely — so it measures a sweep nobody runs. A hand-built board answers
only the questions its author thought of; the real one carries the shapes
nobody would have invented: an epic with thirty children, a card whose comment
window is exhausted, a relation page that fills.

So this reads the REAL board ONCE — every DRE card in the lanes the sweep
reads — scrubs it, and writes it in the shape the sweep's own queries return
it, for the replay test (a sibling card) to run every phase over.

## What it costs, and what it may not do

One paged read through `linear_ops.gql_paged`, pages of 100, over
`LANES`. On the 2026-09-12 board that is 409 cards in 5 requests; the ceiling
is `REQUEST_CEILING` and a run that reaches it says so and exits non-zero
rather than passing quietly. The `linear-budget:` line comes from
`linear_ops`'s own atexit reporter — the ONE writer of that line, so the
number this run spent is counted the same way every other pipeline process
counts it and `check_linear_budget.py` reads it unchanged.

READ-ONLY, enforced at the seam rather than by convention: every query this
script sends passes `assert_read_only` first, so a mutation cannot leave here
whoever calls it.

## The scrub (a pure function, `scrub_card`)

  * **Comment bodies are cut to their first 200 characters.** Every marker
    this pipeline reads is anchored at the START of a body — the routing
    verdict, the run receipts, the critic's markers, the park receipt — so the
    prefix is the part the sweep reads, and the rest is what makes a board
    snapshot a megabyte per hundred cards.
  * **Descriptions are kept whole.** The sweep reads growth records,
    wave-commitment blocks and `Blocked by:` lines ANYWHERE in a description;
    a truncated one would change what the replay sees. (Linear's list api
    already truncates a description at 500 characters — that truncation is
    part of what the sweep itself sees, and is left exactly as it arrives.)
  * **A user is its `id` and nothing else.** The id is opaque and it is the
    authorship credential `comment_records` reads; the display name and the
    email are neither, and are not written.
  * **A card carries exactly `CARD_KEYS`.** The snapshot is built key by key,
    so a field Linear adds, or one a future query selects by accident, cannot
    ride along into a committed file.

## The contract (shared with the replay-test sibling)

    {"taken_at": <ISO-8601 UTC>, "team": "DRE",
     "lanes": [<lane names read>], "cards": [<card>, …]}

Each card: `id`, `identifier`, `title`, `description`, `createdAt`,
`updatedAt`, `state {name}`, `labels {nodes [{name}]}`,
`parent {identifier, state {name}} | null`,
`children {nodes [{id, identifier, createdAt, state {name}}]}`,
`comments {pageInfo {hasNextPage, endCursor}, nodes [{body, createdAt,
user {id} | null}]}` (NEWEST FIRST, exactly as Linear answers a `first: 50`
window), `relations {pageInfo {hasNextPage}, nodes [{type, issue
{identifier}, relatedIssue {identifier}}]}`, `inverseRelations {nodes [{type,
issue {identifier, state {name}}}]}`, `history {nodes [{createdAt, toState
{name}}]}`.

Exit codes: 0 the snapshot was taken and is within both ceilings · 1 it was
taken and written but a ceiling was crossed (named on stderr) · 2 the board
could not be read, and no file is written — an unreadable board is not an
empty board (DRE-2034).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402

# `reconcile.SWEPT_LANES` is the fact this script must not re-spell: the sweep
# decides which lanes it reads, and a second list here would drift the day a
# lane is added to one of the four sets that union into it. reconcile reads the
# SWEEP's own argument `REPO` at import and raises without it, deliberately
# (DRE-3042) — this script has no repo, it reads one team's board, so the
# variable is defaulted for the import and never used.
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
import reconcile  # noqa: E402

TEAM = "DRE"

#: The lanes read: the sweep's own union, plus `Backlog` — where the promotion
#: gate's candidates live and which `SWEPT_LANES` does not include, because the
#: sweep buys them in a second query (`reconcile.backlog_children`). `Intake`
#: and `Planning` are already in `SWEPT_LANES` and named again here because the
#: contract names them; `dict.fromkeys` keeps the order and drops the repeats.
LANES = tuple(dict.fromkeys(reconcile.SWEPT_LANES + ("Backlog", "Intake", "Planning")))

#: Cards per page. Linear's own maximum for this query and what both of the
#: sweep's board reads ask for.
PAGE = 100

#: The sub-connection depths, each one the depth a sweep read asks for, so the
#: snapshot cannot hold more of a card than the sweep can see:
#:   children  — `mid_epic._EPIC_QUERY`, the epic's green-light read;
#:   relations — `merge_sweep_gate.RELATION_PAGE`, the dependents read;
#:   inverse   — `reconcile.backlog_children`, the dependency gate's read;
#:   history   — `mid_epic._EPIC_QUERY` / `wave_commitment._WAVE_QUERY`.
#: The comment window is not here: it is `linear_ops.COMMENT_WINDOW_GQL`, the
#: ONE definition of which fifty comments and which way round (DRE-3250).
CHILD_PAGE = 250
RELATION_PAGE = 50
INVERSE_PAGE = 20
HISTORY_PAGE = 50

#: A comment body is cut to this many characters. Every marker this pipeline
#: reads is anchored at the start of a body.
COMMENT_BODY_CHARS = 200

#: What taking the snapshot may cost in Linear requests. The card's number: a
#: snapshot is a fixture-refresh a person runs, not a sweep, but it draws on
#: the same fleet-wide hourly quota and must stay a rounding error against it.
REQUEST_CEILING = 20

#: How big the committed fixture may be. A file over this stops being a fixture
#: and starts being a checkout cost every clone of this repo pays.
MAX_BYTES = 4 * 1024 * 1024

#: Exactly the keys a snapshot card carries. Written key by key by
#: `scrub_card`; named here so the shape has one statement.
CARD_KEYS = (
    "id", "identifier", "title", "description", "createdAt", "updatedAt",
    "state", "labels", "parent", "children", "comments", "relations",
    "inverseRelations", "history",
)

CARD_QUERY = """query($states: [String!]!, $after: String) {
     issues(first: %d, after: $after, filter: {
       team: {key: {eq: "%s"}},
       state: {name: {in: $states}}
     }) { nodes {
       id identifier title description createdAt updatedAt
       state { name }
       labels { nodes { name } }
       parent { identifier state { name } }
       children(first: %d) { nodes { id identifier createdAt state { name } } }
       %s
       relations(first: %d) {
         pageInfo { hasNextPage }
         nodes { type issue { identifier } relatedIssue { identifier } }
       }
       inverseRelations(first: %d) { nodes {
         type issue { identifier state { name } }
       } }
       history(last: %d) { nodes { createdAt toState { name } } }
     } pageInfo { hasNextPage endCursor } } }""" % (
    PAGE, TEAM, CHILD_PAGE, linear_ops.COMMENT_WINDOW_GQL,
    RELATION_PAGE, INVERSE_PAGE, HISTORY_PAGE,
)


class SnapshotError(RuntimeError):
    """This script tried to do something a read-only snapshot may not do."""


# --------------------------------------------------------------------------- #
# the seam: read-only, and counted                                             #
# --------------------------------------------------------------------------- #


def assert_read_only(query: str) -> None:
    """Refuse anything that is not a query.

    At the seam rather than only in a test: "read-only" is a promise about
    what this process does to the board, and a promise a test makes holds only
    for the path the test walks.
    """
    if (query or "").lstrip().lower().startswith("mutation"):
        raise SnapshotError(
            "board_snapshot is read-only and was asked to send a mutation — "
            "refused, no request sent"
        )


@contextlib.contextmanager
def _counted_seam():
    """Wrap `linear_ops.gql` for the duration of the read: every query is
    checked and recorded, and the list of them is the request count.

    Counted here because the ceiling is a count of REQUESTS, and inferring one
    from the number of cards returned would be a number about the board rather
    than about what was spent. The real `gql` is captured at entry, so a caller
    that has already stubbed the seam (a test) is the one wrapped.
    """
    real = linear_ops.gql
    sent: list[str] = []

    def counted(query, variables=None):
        assert_read_only(query)
        sent.append(query)
        return real(query, variables)

    linear_ops.gql = counted
    try:
        yield sent
    finally:
        linear_ops.gql = real


# --------------------------------------------------------------------------- #
# the scrub — pure functions over what the read returned                       #
# --------------------------------------------------------------------------- #


def _named(node) -> dict | None:
    """`{name}` off a node Linear may answer null for."""
    return None if node is None else {"name": node.get("name")}


def _identified(node) -> dict | None:
    """`{identifier}` off a node Linear may answer null for."""
    return None if node is None else {"identifier": node.get("identifier")}


def scrub_comment(node: dict) -> dict:
    """One comment: its first `COMMENT_BODY_CHARS` characters, when it was
    posted, and WHO by as an opaque id — no display name, no email."""
    user = node.get("user")
    return {
        "body": (node.get("body") or "")[:COMMENT_BODY_CHARS],
        "createdAt": node.get("createdAt"),
        "user": None if user is None else {"id": user.get("id")},
    }


def scrub_card(card: dict) -> dict:
    """One card, in the shape the sweep's reads return it and carrying exactly
    `CARD_KEYS`. Pure: the card it is given is not modified.

    Built key by key on purpose. A copy-then-delete scrub keeps whatever it was
    not told about, and the thing a committed fixture must never quietly gain
    is a field nobody reviewed.
    """
    comments = card.get("comments") or {}
    window = comments.get("pageInfo") or {}
    relations = card.get("relations") or {}
    parent = card.get("parent")
    return {
        "id": card.get("id"),
        "identifier": card.get("identifier"),
        "title": card.get("title"),
        # whole, never cut: the sweep reads growth records, wave-commitment
        # blocks and blocker lines anywhere in a description.
        "description": card.get("description"),
        "createdAt": card.get("createdAt"),
        "updatedAt": card.get("updatedAt"),
        "state": _named(card.get("state")),
        "labels": {
            "nodes": [{"name": n.get("name")}
                      for n in (card.get("labels") or {}).get("nodes") or []],
        },
        "parent": None if parent is None else {
            "identifier": parent.get("identifier"),
            "state": _named(parent.get("state")),
        },
        "children": {
            "nodes": [
                {"id": n.get("id"), "identifier": n.get("identifier"),
                 "createdAt": n.get("createdAt"), "state": _named(n.get("state"))}
                for n in (card.get("children") or {}).get("nodes") or []
            ],
        },
        # NEWEST FIRST, exactly as Linear answers a `first: 50` window, with the
        # window's own pageInfo: `linear_ops.window_is_partial` reads
        # `hasNextPage` to decide the window is not the thread, and the replay
        # must see the same partial windows the sweep does.
        "comments": {
            "pageInfo": {
                "hasNextPage": bool(window.get("hasNextPage")),
                "endCursor": window.get("endCursor"),
            },
            "nodes": [scrub_comment(n) for n in comments.get("nodes") or []],
        },
        "relations": {
            # the merge-sweep gate reads this to know it did not see them all
            "pageInfo": {
                "hasNextPage": bool((relations.get("pageInfo") or {}).get("hasNextPage")),
            },
            "nodes": [
                {"type": n.get("type"), "issue": _identified(n.get("issue")),
                 "relatedIssue": _identified(n.get("relatedIssue"))}
                for n in relations.get("nodes") or []
            ],
        },
        "inverseRelations": {
            "nodes": [
                {"type": n.get("type"), "issue": None if n.get("issue") is None else {
                    "identifier": n["issue"].get("identifier"),
                    "state": _named(n["issue"].get("state")),
                }}
                for n in (card.get("inverseRelations") or {}).get("nodes") or []
            ],
        },
        "history": {
            "nodes": [
                {"createdAt": n.get("createdAt"), "toState": _named(n.get("toState"))}
                for n in (card.get("history") or {}).get("nodes") or []
            ],
        },
    }


# --------------------------------------------------------------------------- #
# taking it                                                                    #
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_board(lanes: tuple[str, ...] = LANES) -> tuple[list[dict], int]:
    """Every card in `lanes`, and what reading them cost in requests.

    ONE paged read. `gql_paged` refuses a query that cannot paginate, which is
    what keeps this from silently becoming the first hundred rows the way the
    sweep's own reads once were (DRE-2681).
    """
    with _counted_seam() as sent:
        cards = linear_ops.gql_paged(CARD_QUERY, {"states": list(lanes)})
    return cards, len(sent)


def build(cards: list[dict], lanes: tuple[str, ...] = LANES) -> dict:
    """The snapshot around a board already read: scrubbed cards, plus the
    provenance that says what this file is a picture of and when."""
    return {
        "taken_at": _now(),
        "team": TEAM,
        "lanes": list(lanes),
        "cards": [scrub_card(card) for card in cards],
    }


def take(lanes: tuple[str, ...] = LANES) -> dict:
    """The snapshot: the board as it is now, read and scrubbed."""
    cards, _requests = read_board(lanes)
    return build(cards, lanes)


def render(snapshot: dict) -> str:
    """The file's bytes: the provenance keys, then ONE CARD PER LINE.

    A reviewer can still read a card out of the file, and a diff still shows
    which card changed, but the file is as many lines as the board has cards.
    Indented, the 2026-09-13 snapshot was 71,423 lines, past the critic's
    ``pr_size_strategy.OVERSIZED_LINES``, so the pull request that committed it
    could not be reviewed at all. ``json.dumps`` escapes newlines inside
    strings, so a card never spills onto a second line."""
    lines = ["{"]
    for key, value in snapshot.items():
        if key != "cards":
            lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    cards = [json.dumps(card, ensure_ascii=False) for card in snapshot.get("cards", [])]
    lines.append('  "cards": [')
    lines.extend(f"    {card}," for card in cards[:-1])
    lines.extend(f"    {card}" for card in cards[-1:])
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def cmd_take(out: str) -> int:
    try:
        cards, requests = read_board()
    except linear_ops.LinearError as e:
        # An unreadable board is not an empty board (DRE-2034): no file is
        # written, because a written one would be indistinguishable from a
        # snapshot of a board that really was empty.
        print(f"could not read the board: {e}", file=sys.stderr)
        return 2
    snapshot = build(cards)
    path = Path(out)
    text = render(snapshot)
    path.write_text(text, encoding="utf-8")
    size = path.stat().st_size
    print(
        f"board-snapshot: {len(snapshot['cards'])} card(s) from "
        f"{len(snapshot['lanes'])} lane(s) — {', '.join(snapshot['lanes'])} — "
        f"in {requests} request(s), {size / 1024 / 1024:.2f} MB → {path}"
    )
    # The `linear-budget:` line itself is printed by linear_ops' atexit
    # reporter, the ONE writer of it — never composed here.
    problems = []
    if requests >= REQUEST_CEILING:
        problems.append(
            f"the read cost {requests} requests, at or over the ceiling of "
            f"{REQUEST_CEILING}"
        )
    if size >= MAX_BYTES:
        problems.append(
            f"the file is {size} bytes, at or over the {MAX_BYTES}-byte ceiling"
        )
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    taker = sub.add_parser("take", help="read the live board and write the snapshot")
    taker.add_argument("--out", required=True, help="where to write the JSON")
    args = parser.parse_args(argv)
    return cmd_take(args.out)


if __name__ == "__main__":
    sys.exit(main())
