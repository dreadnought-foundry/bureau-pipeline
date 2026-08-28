#!/usr/bin/env python3
"""Structural label repair, parents first (DRE-2681).

A deterministic sweep over the WHOLE Backlog — every row, not the first
hundred — that repairs the one thing inheritance can settle without judgment:
a card carrying no `initiative:<x>` label when its parent carries one.

Why parents first. A pass that walks the card list top to bottom repairs a
child whose parent is already labelled and reports the rest as unrepairable —
including children whose parent sits further down the same list and is itself
repairable. On the 2026-08-23 census, 66 of the Backlog's cards carried no
`initiative:*` label: 26 could inherit from a parent and 40 could not. Resolving
parents first means a parent repaired in THIS pass can supply the value to its
own children in the same run.

Why the report separates two failures. 26 of those 40 have no parent at all;
the other 14 sit under six unlabelled parents — and four of those six are
themselves Done or Canceled, so "fix the parent first" is not available. Those
are different asks for whoever reads the report, so they are different lines.

What a missing `initiative:*` label actually costs is narrow, and this pass is
scoped to it: `validate_card.infer_repo` step 2a uses it as the first route to a
repo for a card carrying no `repo:` label, and
`missing(..., require_initiative=True)` refuses to CREATE a child without it.
Promotion is unaffected — `reconcile.py` never reads the label.

What it will NOT do (D1, approved 2026-08-23): repair anything needing
judgment. An unknown `repo:<slug>` is REPORTED, never rewritten — picking the
right repo for a card whose label is wrong is a decision, not an inheritance.

CLI:
  report            read-only. Prints the report, writes nothing. (default)
  repair            applies the planned labels, then prints the same report.

Both print the same proof line: whether this run repaired a card BEYOND the
100th row of the Backlog. A run that touched only the first 100 rows has not
proven the pagination fix and says so itself, in those words.

Auth: LINEAR_API_KEY env var (shared with linear_ops.py).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402
import validate_card  # noqa: E402

INITIATIVE_PREFIX = "initiative:"
REPO_PREFIX = "repo:"

# A parent in one of these states cannot be labelled first — it is finished.
TERMINAL_STATES = frozenset({"Done", "Canceled", "Duplicate"})

# The gap kinds the report keeps apart. NO_PARENT and PARENT_UNLABELLED are
# both "nobody to inherit from" but ask different things of the reader:
# one needs a decision, the other needs the parent labelled.
NO_PARENT = "no-parent"
PARENT_UNLABELLED = "parent-unlabelled"
PARENT_TERMINAL = "parent-terminal"
UNKNOWN_REPO = "unknown-repo"

# The row the pagination fix exists for. A repair at or below it proves nothing
# about pages the promoter could never see.
FIRST_PAGE = 100


def labels_of(node: dict | None) -> list[str]:
    """A card's (or parent's) label names, lowercased. `[]` for a missing node,
    so an absent parent reads as unlabelled rather than raising."""
    return [
        (n.get("name") or "").lower()
        for n in ((node or {}).get("labels") or {}).get("nodes", [])
    ]


def initiative_of(labels: list[str]) -> str | None:
    """The first non-empty `initiative:<x>` label, or None."""
    for l in labels:
        if l.startswith(INITIATIVE_PREFIX) and l.split(":", 1)[1].strip():
            return l
    return None


def _number(card: dict) -> int:
    """The card's numeric part, for a stable tie-break. Non-numeric ids sort
    last rather than crashing the pass."""
    try:
        return int(card["identifier"].split("-")[1])
    except (IndexError, ValueError):
        return 1 << 31


def parents_first(cards: list[dict]) -> list[dict]:
    """`cards` reordered so a card's parent — when the parent is in the set —
    always comes first. Cards whose parent is absent (most parents are epics
    outside the Backlog) are roots. Stable by card number otherwise.

    Marks a card visited BEFORE recursing, so a parent cycle terminates with an
    odd order instead of hanging the pass.
    """
    by_id = {c["identifier"]: c for c in cards}
    ordered: list[dict] = []
    seen: set[str] = set()

    def emit(card: dict) -> None:
        ident = card["identifier"]
        if ident in seen:
            return
        seen.add(ident)
        parent = (card.get("parent") or {}).get("identifier")
        if parent in by_id and parent not in seen:
            emit(by_id[parent])
        ordered.append(card)

    for card in sorted(cards, key=_number):
        emit(card)
    return ordered


def plan_repairs(
    cards: list[dict], *, valid_slugs: set[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """(repairs, gaps) for `cards`, which arrive in CENSUS order (the order the
    paginated fetch returned them — that order is what the row numbers mean).

    A repair is `{identifier, label, source, row}`; a gap is
    `{identifier, kind, detail, row}`. Neither writes anything.
    """
    valid = validate_card.VALID_SLUGS if valid_slugs is None else valid_slugs
    rows = {c["identifier"]: i + 1 for i, c in enumerate(cards)}
    # identifier -> the initiative label it HAS or WILL have after this pass.
    # Seeded as the pass runs, which is the whole reason it runs parents-first.
    resolved: dict[str, str] = {}
    repairs: list[dict] = []
    gaps: list[dict] = []

    for card in parents_first(cards):
        ident = card["identifier"]
        row = rows[ident]
        labels = labels_of(card)

        # Slug validity: reported, never repaired (D1 — a wrong repo label is a
        # decision). Checked for every card, labelled or not.
        for slug in validate_card.unknown_repo_slugs(labels, valid):
            gaps.append({
                "identifier": ident, "row": row, "kind": UNKNOWN_REPO,
                "detail": (
                    f"carries repo:{slug}, which is not on the routing map — a "
                    "human must say which repo this card belongs to"
                ),
            })

        own = initiative_of(labels)
        if own:
            resolved[ident] = own
            continue

        parent = card.get("parent")
        if not parent:
            gaps.append({
                "identifier": ident, "row": row, "kind": NO_PARENT,
                "detail": "has no parent to inherit an initiative from",
            })
            continue

        pid = parent["identifier"]
        # A parent repaired earlier in THIS pass wins over its read-time labels.
        value = resolved.get(pid) or initiative_of(labels_of(parent))
        if value:
            repairs.append({
                "identifier": ident, "row": row, "label": value, "source": pid,
            })
            resolved[ident] = value
            continue

        state = (parent.get("state") or {}).get("name") or "unknown"
        if state in TERMINAL_STATES:
            gaps.append({
                "identifier": ident, "row": row, "kind": PARENT_TERMINAL,
                "detail": (
                    f"its parent {pid} is {state}, so it cannot be fixed first — "
                    "this card needs the label set directly"
                ),
            })
        else:
            gaps.append({
                "identifier": ident, "row": row, "kind": PARENT_UNLABELLED,
                "detail": (
                    f"its parent {pid} carries no initiative: label — label the "
                    "parent and the next run inherits it"
                ),
            })
    return repairs, gaps


def proof_line(cards: list[dict], repairs: list[dict]) -> str:
    """The one line the acceptance criterion is read against.

    A run that repaired a card beyond row 100 proves the promoter now sees past
    the first page. A run that did not says so — it is not evidence, and
    recording it as evidence is how a phase gets signed off on a page it never
    left.
    """
    beyond = [r for r in repairs if r["row"] > FIRST_PAGE]
    if beyond:
        named = ", ".join(f"{r['identifier']} (row {r['row']})" for r in beyond[:5])
        return (
            f"beyond row {FIRST_PAGE}: PROVEN — {len(beyond)} card(s) repaired "
            f"past the first page: {named}"
        )
    return (
        f"beyond row {FIRST_PAGE}: NOT PROVEN — this run repaired nothing past "
        f"row {FIRST_PAGE} of the {len(cards)} row(s) it read, so it is not "
        "evidence that the promoter sees past the first page"
    )


def format_report(cards: list[dict], repairs: list[dict], gaps: list[dict]) -> str:
    """The run's own report: what it read, what it repaired, what it could not,
    and whether it proved anything about the pages past the first."""
    lines = [
        f"structural repair: {len(cards)} Backlog card(s) read "
        "(pagination followed to exhaustion)",
        f"repaired {len(repairs)}:",
    ]
    lines += [
        f"  {r['identifier']} (row {r['row']}): {r['label']} inherited from {r['source']}"
        for r in sorted(repairs, key=lambda r: r["row"])
    ] or ["  (none)"]
    lines.append(f"not repairable {len(gaps)}:")
    lines += [
        f"  {g['identifier']} (row {g['row']}): {g['detail']}"
        for g in sorted(gaps, key=lambda g: (g["kind"], g["row"]))
    ] or ["  (none)"]
    lines.append(proof_line(cards, repairs))
    return "\n".join(lines)


def fetch_backlog() -> list[dict]:
    """Every Backlog card with its parent's state AND labels — the parent's
    labels inline, so inheritance costs no extra read per card. Paginated to
    exhaustion: a repair pass that stops at row 100 cannot repair the rows this
    card exists for."""
    return linear_ops.gql_paged(
        """query($after: String) {
           issues(first: 100, after: $after, filter: {
             team: {key: {eq: "DRE"}},
             state: {name: {eq: "Backlog"}}
           }) { nodes {
             identifier
             state { name }
             labels { nodes { name } }
             parent {
               identifier state { name } labels { nodes { name } }
             }
           } pageInfo { hasNextPage endCursor } } }"""
    )


def apply_repairs(repairs: list[dict]) -> int:
    """Write the planned labels. Returns how many actually landed.

    One 🔧 comment per card, in the Todo gate's own words — the repair has to be
    visible on the card, not only in a run log nobody keeps. A write failure is
    reported and the pass continues: one bad card must not strand the rest.
    """
    applied = 0
    for r in repairs:
        try:
            linear_ops.add_label(r["identifier"], r["label"])
            linear_ops.cmd_comment(
                r["identifier"],
                f"🔧 Structural repair: added {r['label']}, inherited from "
                f"{r['source']}.",
            )
        except Exception as e:  # noqa: BLE001 — one card's write, never the pass
            print(f"ERROR: {r['identifier']} repair failed: {e}", file=sys.stderr)
            continue
        applied += 1
    return applied


def run(apply: bool = False) -> str:
    cards = fetch_backlog()
    repairs, gaps = plan_repairs(cards)
    if apply:
        applied = apply_repairs(repairs)
        if applied != len(repairs):
            print(
                f"WARNING: {len(repairs) - applied} planned repair(s) did not "
                "land — see ERROR lines above",
                file=sys.stderr,
            )
    report = format_report(cards, repairs, gaps)
    print(report)
    return report


if __name__ == "__main__":
    cmd = (sys.argv[1:] or ["report"])[0]
    if cmd not in ("report", "repair"):
        sys.exit(f"usage: structural_repair.py [report|repair] (got {cmd!r})")
    run(apply=cmd == "repair")
