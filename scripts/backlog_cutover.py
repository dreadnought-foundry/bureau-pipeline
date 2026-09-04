#!/usr/bin/env python3
"""The cutover — the whole legacy Backlog moves to Intake, and nobody is exempt
(DRE-2687).

Every Backlog card was created before the front door existed, and none of them
carries a routing verdict. They are not work: they are a list of things somebody
once wanted. **They move to Intake and are re-planned before they are work
again.**

## There is no allowlist

The 2026-08-23 decision (D4) exempted four classes from the move — cards inside
`promote_ready()`'s reach, `needs-human` cards, operator / `no-code` cards, and
the paused DeltaSolv set. The CEO withdrew it on 2026-08-26, and the reason is
mechanical rather than a change of mind: DRE-2725's guard says a card with no
verdict cannot rest in any lane, and an exempt card is precisely a card with no
verdict sitting in Backlog. The guard would have bounced all of them to Intake
on its first sweep whatever this script said. There were only ever two
consistent positions — exempt the legacy cards from the guard PERMANENTLY,
which is the two-population board DRE-2728 exists to prevent, or move
everything.

So the exemption is replaced by an ORDERING, not removed and forgotten:

  1. **The promoter-reach cards go first.** They were exempted because they are
     live work and stranding them costs real time. That concern is right and
     sequencing is the fix — stranded for hours, not parked forever, and no
     second population is created to achieve it. The run records which they
     were.
  2. **Then newest-first through the rest** (D2, approved 2026-08-23). The
     newest cards are fresh enough in the operator's memory that a wrong
     verdict is spotted instantly; a two-month-old card gives nobody that. The
     stated cost is accepted: the newest cards are also the best-formed, so the
     first batch flatters the classifier and the hard cases are met last.

## Grandfathering is not an exemption

A card with an OPEN pull request, or a run receipt whose clock is still
running, is justified in its lane by evidence and finishes under the old rules.
That distinction is drawn on the evidence, never on a list of ids —
`card_ids_in_code()` and its test exist so that stays true. If such a card comes
back for rework it goes to Intake like anything else.

An unreadable pull-request lookup is NOT "no pull request" (DRE-2034): the run
refuses rather than yanking a card out from under an open PR.

## Backlog is empty on cutover day

Not nearly empty. Empty. It refills only with verdict-carrying cards at the rate
Planning produces them, so **expect it to look alarming for about a week** — say
so in advance rather than explaining it afterwards. Occupancy is recorded
immediately before and immediately after the run, on the card, because "the
board looks wrong" a week later needs a number to compare against.

Bounded and on demand, like the groomer: it writes nothing without `--apply`,
and `--limit` bounds a single run.

## Rehearsing it on one card

`--limit N` bounds a run but cannot NAME one: it takes whichever real cards the
plan puts first, so a throwaway probe dropped into Backlog could not be moved
alone and the move-in path could not be watched before the real run.
`--only DRE-N [DRE-M …]` restricts the population to the cards it names, through
this same path — the same in-flight test, the same reason posted before the
move, the same from-lane guard. A named card that is not in Backlog is reported
as such and skipped; the run never reaches into whatever lane it is actually in.
The occupancy record for such a run says plainly that it was a rehearsal on
named cards, so nothing can read it as the cutover's.

CLI:

    python3 scripts/backlog_cutover.py census
    python3 scripts/backlog_cutover.py plan   [--limit N] [--only DRE-N …] [--out plan.json]
    python3 scripts/backlog_cutover.py run    [--apply] [--limit N] [--only DRE-N …] [--record CARD]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_pr  # noqa: E402 — ONE anchored "does this head ref belong to this card"
import linear_ops  # noqa: E402
import routing_verdict  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
REPO_MAP_PATH = os.path.join(ROOT, "config", "repo-map.json")

#: The one transition this script makes, and the only one it may make.
CUTOVER_FROM = "Backlog"
CUTOVER_TO = "Intake"

MARK = "🚚"
CUTOVER_TAG = "backlog-cutover"

#: The two headlines an occupancy record can open with, and they are mutually
#: exclusive on purpose (DRE-3034). Anything asking whether the cutover has run
#: reads the record, so a rehearsal on a named handful must not open with the
#: cutover's sentence — and must say, in the same words, that it has not.
CUTOVER_HEADLINE = f"{MARK} {CUTOVER_TAG}: the cutover ran."
REHEARSAL_HEADLINE = f"{MARK} {CUTOVER_TAG}: a REHEARSAL ran — NOT the cutover."

#: Parent-epic states that count as ACTIVATED, i.e. states in which the promoter
#: would look at a child at all. This is `reconcile.EPIC_ACTIVE_STATES`, and
#: tests/test_backlog_cutover.py pins the two together rather than importing the
#: sweep (which requires the sweep's environment to import at all). If the
#: promoter's rule changes, that test fails instead of this script quietly
#: disagreeing with the promoter it is named after.
EPIC_ACTIVE_STATES = ("Todo", "In Progress")

#: Proof-of-life prefixes — the receipts an agent run posts. Mirrors
#: `reconcile._LIFE_PREFIXES`, pinned by the same test. The sweep's own 🪦/🧹/🚨
#: receipts are deliberately NOT here: they would make every card the sweep has
#: ever touched look alive, which is every legacy card in the population.
LIFE_PREFIXES = ("⏳", "🧠")

#: How fresh a run receipt has to be to mean "a run is still going". One build's
#: honest ceiling, the same number the contract gives In Progress. Older than
#: this it is history — almost every legacy card was dispatched once, and
#: reading that as in-flight would re-create the exemption by accident.
IN_FLIGHT_MINUTES = 60

#: How many pull requests to read per repo. The fleet's open-PR count is in the
#: tens; a truncated page here would read as "no open PR" on a live card.
PR_PAGE = "200"

_CARD_ID = re.compile(r"\bDRE-\d+\b")


class CutoverUnreadable(Exception):
    """A fact the run needs could not be read, so the run does not act.

    Never downgraded to a default: "could not tell" and "there is no pull
    request" are different facts, and only one of them makes it safe to move a
    card (DRE-2034, console-honesty rule 1).
    """


# --------------------------------------------------------------------------- #
# reading the population                                                       #
# --------------------------------------------------------------------------- #

# Paginated, and the query declares $after / selects pageInfo because
# `gql_paged` refuses one that cannot paginate. The sweep's unpaginated read
# made its world the first 100 rows of a 226-card Backlog (DRE-2681); a cutover
# that moves page one has moved nothing and left a second population behind.
POPULATION_QUERY = """query($lane: String!, $after: String) {
  issues(first: 100, after: $after, filter: {
    team: {key: {eq: "DRE"}},
    state: {name: {eq: $lane}}
  }) {
    nodes {
      id identifier title createdAt
      state { name }
      labels { nodes { name } }
      parent { identifier state { name } }
      comments(last: 50) { nodes { body createdAt } }
    }
    pageInfo { hasNextPage endCursor }
  }
}"""


def read_population(lops, lane: str = CUTOVER_FROM) -> list[dict]:
    """Every card in `lane` — all of them, not the first page."""
    return lops.gql_paged(POPULATION_QUERY, {"lane": lane})


def occupancy(lops, lanes: tuple = (CUTOVER_FROM, CUTOVER_TO)) -> dict:
    """How many cards each lane holds right now, read live."""
    return {lane: len(read_population(lops, lane)) for lane in lanes}


def repo_map() -> dict:
    with open(REPO_MAP_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# no allowlist — asserted against this file's own source                       #
# --------------------------------------------------------------------------- #


def card_ids_in_code(source: str) -> list:
    """Card identifiers appearing in the CODE of `source`, in order.

    An exemption list is a list of card ids, so this is what "no allowlist"
    looks like as a check. Comments are invisible to the parser and docstrings
    are skipped on purpose: the DRE references that RECORD the decisions are
    prose, and prose is not a rule the code follows.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Unparseable is not clean — scan the raw text rather than report none.
        return _dedupe(_CARD_ID.findall(source))
    skip = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None) or []
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                skip.add(id(first.value))
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ):
            found += _CARD_ID.findall(node.value)
    return _dedupe(found)


def _dedupe(items) -> list:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --------------------------------------------------------------------------- #
# the ordering: promoter reach first, then newest first                        #
# --------------------------------------------------------------------------- #


def _labels(card: dict) -> list:
    return [n["name"].lower() for n in (card.get("labels") or {}).get("nodes", [])]


def _comments(card: dict) -> list:
    return list((card.get("comments") or {}).get("nodes", []))


def in_promoter_reach(card: dict) -> bool:
    """Would `promote_ready()` look at this card at all?

    Read off live state — the epic's own lane, and the card's own verdict —
    never off a list of ids. Two gates, because there are two ways a card can
    have been approved (DRE-2735): a CHILD inherits its epic's activation, and a
    PARENTLESS card's verdict IS its approval. An `agent:planner` card is an
    epic, and the sweep never promotes one.

    Note that a held card is still in reach: `needs-human` is why it is stuck,
    not a reason the promoter never looks. Five of the seven cards measured on
    2026-08-23 were held by the phantom-blocker defect and were counted.
    """
    labels = _labels(card)
    if "agent:planner" in labels:
        return False
    parent = card.get("parent")
    if parent:
        return (parent.get("state") or {}).get("name") in EPIC_ACTIVE_STATES
    bodies = [c.get("body") or "" for c in _comments(card)]
    verdict = routing_verdict.verdict_on(bodies)
    return bool(verdict and routing_verdict.is_promotable(verdict))


def _created(card: dict) -> str:
    return card.get("createdAt") or ""


def _card_number(identifier: str) -> int:
    try:
        return int(identifier.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _newest_first(cards: list) -> list:
    """Newest first, with the card number as a deterministic tie-break — two
    cards created in the same second must not order differently per run."""
    return sorted(
        cards,
        key=lambda c: (_created(c), _card_number(c["identifier"])),
        reverse=True,
    )


# --------------------------------------------------------------------------- #
# in-flight, on evidence                                                       #
# --------------------------------------------------------------------------- #


def _age_minutes(iso: str, now: str | None = None) -> float:
    """Minutes since `iso`. `now` makes the age testable without freezing the
    clock. (reconcile.age_minutes, spelled here so this script imports without
    the sweep's environment.)"""
    then = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    at = (
        datetime.fromisoformat(now.replace("Z", "+00:00"))
        if now
        else datetime.now(UTC)
    )
    return (at - then).total_seconds() / 60


def in_flight_reason(
    card: dict, *, open_pr_refs=(), now: str | None = None
) -> str | None:
    """Why this card is in flight, or None. Evidence only.

    An in-flight card finishes and closes normally under the old rules. If it
    comes BACK for rework it goes to Intake like anything else — which is a
    property of where it re-enters, not of an exemption recorded here.
    """
    identifier = card["identifier"]
    for ref in open_pr_refs or ():
        if card_pr.matches_card(ref, identifier):
            return (
                f"an open pull request on `{ref}` — a pull request justifies "
                "the lane by evidence, so this card finishes under the old rules"
            )
    for comment in _comments(card):
        body = (comment.get("body") or "").lstrip()
        if not body.startswith(LIFE_PREFIXES):
            continue
        try:
            age = _age_minutes(comment.get("createdAt") or "", now)
        except ValueError:
            continue
        if age < IN_FLIGHT_MINUTES:
            return (
                f"a run receipt {age:.0f} minutes old — a run is still going, "
                "and its receipt justifies the lane by evidence"
            )
    return None


def open_pr_refs(slugs=None, run=None) -> set:
    """Every OPEN pull request head ref across the fleet's repos.

    One call per repo rather than one per card: the population is in the
    hundreds and the repo list is in the single digits. A read that fails
    raises — an unreadable answer must never reach `in_flight_reason` as an
    empty set, which would read as "no card has a pull request".
    """
    run = run or card_pr._gh_json
    mapping = repo_map()
    refs: set = set()
    for slug in slugs if slugs is not None else tuple(mapping):
        repo = mapping.get(slug)
        if not repo:
            raise CutoverUnreadable(
                f"{slug!r} is not in config/repo-map.json — cannot ask GitHub "
                "whether its cards have open pull requests"
            )
        cmd = [
            "pr", "list", "--repo", repo, "--state", "open",
            "--limit", PR_PAGE, "--json", "headRefName",
        ]
        try:
            rows = json.loads(run(cmd) or "[]")
        except Exception as e:  # PrLookupError, ValueError, anything gh does
            raise CutoverUnreadable(
                f"could not read {repo}'s open pull requests ({e}) — refusing "
                "to move cards on an unreadable answer"
            ) from e
        refs.update((row.get("headRefName") or "") for row in rows)
    return {ref for ref in refs if ref}


# --------------------------------------------------------------------------- #
# the plan, and the run                                                        #
# --------------------------------------------------------------------------- #


def only_ids(values) -> list:
    """The `--only` card ids, normalised, in the order they were named.

    Case and stray whitespace are the operator's to get wrong at 2am, and a
    card named twice is one card. A value that is not a card identifier at all
    is refused loudly rather than reported as "not in Backlog" — that phrasing
    is a claim about the board, and a typo must never make one.
    """
    out: list = []
    for value in values or ():
        wanted = (value or "").strip().upper()
        if not _CARD_ID.fullmatch(wanted):
            raise ValueError(f"{value!r} is not a card identifier")
        if wanted not in out:
            out.append(wanted)
    return out


def plan(
    cards: list, *, open_pr_refs=(), now: str | None = None, only=None
) -> dict:
    """The whole population, ordered, with one outcome per card.

    Completeness is asserted rather than assumed: every card comes out either in
    `move` or in `in_flight`, and `population` is what went in.

    `only` restricts the population to the cards it names — the whole run on a
    named handful, down this same path, so the cutover can be rehearsed on a
    probe before it is run on the board (DRE-3034). Nothing else changes: a
    named card is still tested for being in flight, still moved with its reason
    posted first, still guarded on the lane it was read in. A named card that is
    not in the population is reported in `not_in_backlog` and never touched.
    """
    named = only_ids(only) if only is not None else None
    not_in_backlog: list = []
    if named is not None:
        by_id = {c["identifier"]: c for c in cards}
        cards = [by_id[i] for i in named if i in by_id]
        not_in_backlog = [i for i in named if i not in by_id]

    movable, held = [], []
    for card in cards:
        why = in_flight_reason(card, open_pr_refs=open_pr_refs, now=now)
        if why:
            held.append({"identifier": card["identifier"], "why": why})
        else:
            movable.append(card)
    reach = [c for c in movable if in_promoter_reach(c)]
    rest = [c for c in movable if not in_promoter_reach(c)]
    ordered = _newest_first(reach) + _newest_first(rest)
    return {
        "population": len(cards),
        "move": ordered,
        "batch_one": [c["identifier"] for c in _newest_first(reach)],
        "in_flight": held,
        "only": named,
        "not_in_backlog": not_in_backlog,
    }


def move_note(card: dict, position: int, total: int, batch_one: bool) -> str:
    """What the moved card carries, in the CEO's language rather than a diff."""
    why_first = (
        "\n\n**This card is in batch one** because live work is stranded for "
        "hours, not parked forever — the cards the promoter was already "
        "looking at are classified first."
        if batch_one
        else ""
    )
    return (
        f"{MARK} {CUTOVER_TAG}: moved {CUTOVER_FROM} → {CUTOVER_TO} "
        f"({position} of {total}).\n\n"
        "**Why:** this card was created before the front door existed and "
        "carries no routing verdict, so nothing can say who should build it or "
        "whether it is still wanted. Everything unstarted moves to Intake and "
        "is re-planned before it is work again. There is no exemption list — a "
        f"second population with no gate is the problem being removed.{why_first}"
        "\n\n**What happens next:** the groomer sequences Intake into batches "
        "the CEO approves, and the batch that is approved goes to Planning. "
        "Nothing here changes what the card says."
    )


def run(lops, plan_: dict, *, apply: bool = False, limit: int | None = None) -> dict:
    """Move the planned cards. Writes nothing unless `apply`.

    The reason is posted BEFORE the move, so a move that fails still leaves the
    reason on the card. `cmd_advance` is guarded on the from-lane, so a card
    that left Backlog between the read and the write is not dragged back.
    """
    targets = plan_["move"][:limit] if limit else plan_["move"]
    batch_one = set(plan_["batch_one"])
    total = len(plan_["move"])
    moved = []
    for position, card in enumerate(targets, start=1):
        identifier = card["identifier"]
        first = identifier in batch_one
        if not apply:
            continue
        lops.cmd_comment(identifier, move_note(card, position, total, first))
        lops.cmd_advance(identifier, CUTOVER_TO, CUTOVER_FROM)
        moved.append({"identifier": identifier, "batch_one": first})
    return {
        "applied": apply,
        "moved": moved,
        "planned": [c["identifier"] for c in targets],
        "batch_one": plan_["batch_one"],
        "in_flight": plan_["in_flight"],
        "only": plan_.get("only"),
        "not_in_backlog": plan_.get("not_in_backlog") or [],
    }


def _lane_table(before: dict, after: dict) -> list:
    lines = ["| Lane | Before | After |", "| -- | -- | -- |"]
    for lane in sorted(set(before) | set(after)):
        lines.append(f"| {lane} | {before.get(lane, '—')} | {after.get(lane, '—')} |")
    return lines


def _left_alone(result: dict) -> str:
    return (
        "**Left alone, on evidence:** "
        + (
            "; ".join(
                f"{row['identifier']} — {row['why']}" for row in result["in_flight"]
            )
            or "none"
        )
        + "."
    )


def record_note(before: dict, after: dict, result: dict) -> str:
    """The occupancy record: what the board held immediately before and
    immediately after. A week of an alarming-looking board needs a number to
    compare against, written down before anyone asks.

    An `--only` run gets a DIFFERENT record (DRE-3034), because the numbers
    either side of a rehearsal on two named cards look nothing like the
    cutover's and must never be read as them.
    """
    if result.get("only"):
        return _rehearsal_note(before, after, result)
    lines = [CUTOVER_HEADLINE, ""]
    lines += _lane_table(before, after)
    lines += [
        "",
        f"**Moved:** {len(result['moved'])} card(s).",
        f"**Batch one (inside the promoter's reach, classified first):** "
        f"{', '.join(result['batch_one']) or 'none'}.",
        "",
        _left_alone(result),
        "",
        "Backlog is empty rather than nearly empty, and it refills only with "
        "verdict-carrying cards at the rate Planning produces them. Expect the "
        "board to look alarming for about a week.",
    ]
    return "\n".join(lines)


def _rehearsal_note(before: dict, after: dict, result: dict) -> str:
    """The record for an `--only` run, which cannot be mistaken for the
    cutover's: it names the cards it was rehearsed on and says in so many words
    that the cutover has not run."""
    only = result["only"]
    skipped = result.get("not_in_backlog") or []
    lines = [
        REHEARSAL_HEADLINE,
        "",
        f"**Rehearsed on {len(only)} named card(s):** {', '.join(only)}.",
        "",
    ]
    lines += _lane_table(before, after)
    lines += [
        "",
        f"**Moved:** {len(result['moved'])} card(s)"
        + (
            ": " + ", ".join(row["identifier"] for row in result["moved"])
            if result["moved"]
            else ""
        )
        + ".",
        f"**Named but not in {CUTOVER_FROM}, so not touched:** "
        f"{', '.join(skipped) or 'none'}.",
        "",
        _left_alone(result),
        "",
        f"This was a rehearsal on cards named by hand, to watch one card take "
        f"the {CUTOVER_FROM} → {CUTOVER_TO} path before the real run. **The "
        f"cutover has not run**, {CUTOVER_FROM} above is still the whole legacy "
        f"population, and these counts are not the cutover's occupancy record.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _render(plan_: dict) -> str:
    lines = []
    if plan_.get("only"):
        lines.append(f"only:       {', '.join(plan_['only'])} (a rehearsal)")
    lines += [
        f"population: {plan_['population']} card(s) in {CUTOVER_FROM}",
        f"moving:     {len(plan_['move'])}",
        f"batch one:  {', '.join(plan_['batch_one']) or 'none'}",
        f"in flight:  {len(plan_['in_flight'])}",
    ]
    for identifier in plan_.get("not_in_backlog") or ():
        lines.append(f"  - {identifier}: not in {CUTOVER_FROM} — skipped")
    for row in plan_["in_flight"]:
        lines.append(f"  - {row['identifier']}: {row['why']}")
    lines.append("")
    for position, card in enumerate(plan_["move"], start=1):
        flag = "*" if card["identifier"] in set(plan_["batch_one"]) else " "
        lines.append(
            f"{position:>4}{flag} {card['identifier']:<10} {(card.get('title') or '')[:70]}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("census", help="what the lanes hold right now")

    only_help = "restrict the population to these cards — a rehearsal, not the cutover"

    p_plan = sub.add_parser("plan", help="the ordered move list; writes nothing")
    p_plan.add_argument("--limit", type=int)
    p_plan.add_argument("--only", nargs="+", metavar="CARD", help=only_help)
    p_plan.add_argument("--out", help="write the plan as JSON")

    p_run = sub.add_parser("run", help="move the cards (needs --apply to write)")
    p_run.add_argument("--apply", action="store_true")
    p_run.add_argument("--limit", type=int)
    p_run.add_argument("--only", nargs="+", metavar="CARD", help=only_help)
    p_run.add_argument("--record", help="post the occupancy record to this card")

    args = parser.parse_args(argv)

    # Validated before anything is read or written: a typo must stop the run,
    # not become a card the record says was not in Backlog.
    try:
        only = only_ids(getattr(args, "only", None) or ())
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.command == "census":
        for lane, count in occupancy(linear_ops).items():
            print(f"{lane}: {count}")
        return 0

    before = occupancy(linear_ops)
    cards = read_population(linear_ops)
    try:
        refs = open_pr_refs()
    except CutoverUnreadable as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    plan_ = plan(cards, open_pr_refs=refs, only=only or None)

    if args.command == "plan":
        if args.limit:
            plan_["move"] = plan_["move"][: args.limit]
        print(_render(plan_))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(
                    {**plan_, "move": [c["identifier"] for c in plan_["move"]]},
                    fh,
                    indent=2,
                )
            print(f"wrote {args.out}")
        return 0

    result = run(linear_ops, plan_, apply=args.apply, limit=args.limit)
    if not args.apply:
        print(_render(plan_))
        print("\ndry run — nothing was written. Re-run with --apply.")
        return 0
    after = occupancy(linear_ops)
    note = record_note(before, after, result)
    print(note)
    if args.record:
        linear_ops.cmd_comment(args.record, note)
        print(f"recorded on {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
