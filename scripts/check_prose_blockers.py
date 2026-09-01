#!/usr/bin/env python3
"""The prose-blocker survey: how many dependencies live only in prose (DRE-2676).

    python3 scripts/check_prose_blockers.py          # measure the live board

The plan this card shipped turns on ONE measurement, taken 2026-08-31:

    293 live DRE cards · 35 carrying a prose blocker declaration ·
    44 declarations · 44 corroborated by a formal `blocks` relation ·
    0 prose-only

Zero prose-only is what made the strict gate safe to ship without a
behaviour-change window: prose ⊆ relations everywhere, so a relations-only gate
returns exactly what the old one returned, and a defect refusal routes nobody.

**A number that lives only in a card description is true on the day it was
written.** So this recomputes it on demand, in the `make check-channel-fleet`
spirit — ask the command, never trust the figure written into prose
(`adr-one-writer-per-fact`, DRE-2605, the rule that replaced this repo's
hand-counted channel roster).

## Why a full read per card

Linear's LIST api truncates `description` at 500 characters, which is exactly
why this card's original step 1 said the count could not be established. So the
roster is read as a list (identifiers only, where truncation cannot bite) and
each card is then read IN FULL. That is one request per live card — slow, and
the reason this is a command somebody runs rather than something the sweep does
every fifteen minutes.

It also means this check sees MORE than the gate does: `reconcile.
backlog_children` reads its candidates through the list api, so a declaration
past the 500th character of a body is invisible to the sweep and visible here.
That asymmetry is the point of surveying rather than trusting the gate's own
view — a defect the gate cannot see is exactly the one nobody would otherwise
find. It is also why a finding here is reported rather than acted on.

## Scope, stated because provenance is part of a measurement

Live cards only. Done/Canceled/Duplicate are never promotion candidates, so
they are not scanned, and this measures TODAY's board — it cannot prove no
prose-only card ever existed. `inverseRelations` is read one page deep; a card
that FILLS the page is reported as truncated rather than counted clean, because
"we did not look" and "there is nothing there" are different answers.

Exit codes: 0 a clean board · 1 at least one prose-only claim (each one named)
· 2 the board could not be read — which is never rendered as a clean result
(DRE-2034).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402

# How many relations are read per card. The maximum observed on any card on
# 2026-08-31 was 7, so this does not bind today — and `Survey.truncated` says so
# rather than assuming it, because the day it binds is the day a card silently
# reads as prose-only.
RELATION_PAGE = 20


@dataclass(frozen=True)
class Survey:
    """What the board says right now. Every field is counted, never recalled."""

    live: int
    declaring: tuple           # cards carrying >= 1 declaration
    claims: tuple              # (card, claimed, corroborated) per reference
    truncated: tuple           # cards whose relation page filled

    @property
    def references(self) -> int:
        return len(self.claims)

    @property
    def corroborated(self) -> int:
        return sum(1 for _, _, ok in self.claims if ok)

    @property
    def prose_only(self) -> tuple:
        return tuple((card, claimed) for card, claimed, ok in self.claims if not ok)


def survey(cards) -> Survey:
    """Count `cards` with the SAME functions the gate reads them with.

    Deliberately `prose_blockers`, not a second parser: a survey with its own
    grammar would report a defect the sweep does not see, or miss one it does,
    and either way the number would stop being about the pipeline.
    """
    declaring: list = []
    claims: list = []
    truncated: list = []
    for card in cards:
        relations = prose_blockers.relation_ids(card)
        found = sorted(prose_blockers.prose_claims(card))
        if not found:
            continue
        declaring.append(card["identifier"])
        for claimed in found:
            claims.append((card["identifier"], claimed, claimed in relations))
        if len(((card.get("inverseRelations") or {}).get("nodes") or [])) >= RELATION_PAGE:
            truncated.append(card["identifier"])
    return Survey(
        live=len(list(cards)),
        declaring=tuple(declaring),
        claims=tuple(claims),
        truncated=tuple(truncated),
    )


# --------------------------------------------------------------------------- #
# reading the board                                                            #
# --------------------------------------------------------------------------- #


def live_identifiers() -> list:
    """Every DRE card that is not Done/Canceled/Duplicate, by identifier."""
    return [
        node["identifier"]
        for node in linear_ops.gql_paged(
            """query($after: String) {
                 issues(first: 100, after: $after, filter: {
                   team: {key: {eq: "DRE"}},
                   state: {type: {nin: ["completed", "canceled"]}}
                 }) { nodes { identifier }
                      pageInfo { hasNextPage endCursor } } }"""
        )
    ]


def read_card(identifier: str) -> dict:
    """One card, IN FULL — the untruncated description and its relations."""
    return linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             identifier description
             parent { identifier }
             inverseRelations(first: %d) { nodes {
               type issue { identifier state { name } }
             } } } }""" % RELATION_PAGE,
        {"id": identifier},
    )["issue"]


def live_cards() -> list:
    """The roster, then a full read of each card on it."""
    identifiers = live_identifiers()
    print(f"reading {len(identifiers)} live card(s) in full…", file=sys.stderr)
    return [read_card(identifier) for identifier in identifiers]


# --------------------------------------------------------------------------- #
# the report                                                                   #
# --------------------------------------------------------------------------- #


def render(result: Survey) -> str:
    lines = [
        "prose-blocker survey — computed now, from the live board",
        "",
        f"  live DRE cards (not Done/Canceled/Duplicate) ... {result.live}",
        f"  cards carrying a prose blocker declaration ..... {len(result.declaring)}",
        f"  prose blocker references in total .............. {result.references}",
        f"  — corroborated by a formal blocks relation ..... {result.corroborated}",
        f"  — PROSE-ONLY, no matching relation ............. {len(result.prose_only)}",
    ]
    if result.prose_only:
        lines += [
            "",
            "Each of these is a defect in the CARD, not a dependency. The sweep "
            "refuses to promote it, says so once, and moves it to "
            f"{prose_blockers.DEFECT_LANE}:",
        ]
        lines += [f"  [FAIL] {card} declares {claimed} and no relation holds it"
                  for card, claimed in result.prose_only]
    if result.truncated:
        lines += [
            "",
            f"  NOT MEASURED: {', '.join(result.truncated)} filled the "
            f"{RELATION_PAGE}-relation page, so a corroborating relation may "
            "exist that this read never saw.",
        ]
    return "\n".join(lines)


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    try:
        cards = live_cards()
    except linear_ops.LinearError as e:
        # An unreadable board is not a clean board (DRE-2034). Neither the green
        # of a measured zero nor the red of a finding — its own answer.
        print(f"could not read the board: {e}", file=sys.stderr)
        return 2
    result = survey(cards)
    print(render(result))
    return 1 if result.prose_only else 0


if __name__ == "__main__":
    sys.exit(main())
