"""The CEO's "build it" and "put it into planning", as the standard carries them (DRE-6219).

The CEO, 2026-10-07: "build it" means a hand-built card. The rule lived only in
one repo's session memory, so sessions rooted anywhere else never read it. The
shared standards reach every repo — headless agents through workflow context
injection, interactive sessions through the `dreadnought-standards` plugin
generated from this repo's `main` — so the rule lands ONCE, in
`standards/card-quality.md`, directly after the routing verdicts that explain
the `hand-built` mark.

Each assertion is content-shaped on purpose: a document is the only consumer of
this change, and a rule nothing reads back is a rule that gets edited away.
Where the section names a lane, the lane is checked against
`config/lane-contract.json`, so the text cannot drift to a lane the board does
not carry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARD = ROOT / "standards" / "card-quality.md"
LANE_CONTRACT = ROOT / "config" / "lane-contract.json"

HEADING = "## The CEO's words, and where the card goes (DRE-6219)"
BEFORE = "## Routing verdicts (DRE-2724)"
AFTER = "## Epics"

# The lanes the section names, each of which the lane contract must carry.
LANES = ("Intake", "Planning", "Hand-work", "In Review")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _loose(needle: str) -> re.Pattern:
    """`needle` with every run of whitespace made elastic — the standard
    hard-wraps at 80 columns and a phrase falling across a line break is not a
    document that stopped saying it."""
    return re.compile(r"\s+".join(re.escape(w) for w in needle.split()), re.I)


def _headings() -> list[str]:
    return [line for line in _read(STANDARD).splitlines() if line.startswith("## ")]


def _section() -> str:
    """The section, heading to the next `## `."""
    body = _read(STANDARD)
    at = body.find(HEADING)
    assert at >= 0, f"standards/card-quality.md carries no {HEADING!r} heading"
    rest = body[at:]
    end = re.compile(r"^## ", re.M).search(rest, 1)
    return rest[: end.start()] if end else rest


def _table_rows() -> list[str]:
    return [
        line
        for line in _section().splitlines()
        if line.startswith("|") and not re.match(r"^\|\s*--", line)
    ]


def _row(phrase: str) -> str:
    rows = [r for r in _table_rows()[1:] if f'"{phrase}"' in r.lower()]
    assert len(rows) == 1, (
        f'the table has {len(rows)} rows for "{phrase}" — it must have exactly one'
    )
    return rows[0]


def _contract_lanes() -> set[str]:
    contract = json.loads(_read(LANE_CONTRACT))
    return {lane["name"] for lane in contract["lanes"]}


def test_heading_exists_exactly_once():
    assert _headings().count(HEADING) == 1


def test_section_sits_directly_after_routing_verdicts_and_before_epics():
    headings = _headings()
    at = headings.index(HEADING)
    assert headings[at - 1] == BEFORE, (
        f"{HEADING!r} must come directly after {BEFORE!r}, which explains the "
        f"`hand-built` mark; it follows {headings[at - 1]!r}"
    )
    assert headings[at + 1] == AFTER, (
        f"{HEADING!r} must come directly before {AFTER!r}; it precedes "
        f"{headings[at + 1]!r}"
    )


def test_table_has_two_rows_one_per_phrase():
    rows = _table_rows()
    assert rows, "the section carries no table"
    header = rows[0]
    for column in ("The CEO says", "The card", "Who builds it"):
        assert column in header, f"the table header lacks {column!r}"
    assert len(rows[1:]) == 2, f"the table has {len(rows[1:])} rows, not two"
    _row("build it")
    _row("put it into planning")


def test_build_it_card_is_filed_in_intake_carrying_hand_built():
    row = _row("build it")
    assert "**Intake**" in row, 'the "build it" row does not file the card in Intake'
    assert "`hand-built`" in row, 'the "build it" row does not carry `hand-built`'
    assert _loose("never put in Hand-work by hand").search(row), (
        'the "build it" row does not say the card is never put in Hand-work by hand'
    )
    # The sweep is the one writer named into Hand-work.
    assert _loose("the sweep is the writer that carries a card there").search(row)
    assert "In Review" in row and _loose("the merge closes it").search(row), (
        'the "build it" row does not carry the card to In Review on its pull '
        "request and to Done on its merge"
    )


def test_put_it_into_planning_card_goes_through_intake_first():
    row = _row("put it into planning")
    assert "**Intake**" in row and "**Planning**" in row
    assert _loose("as a second write").search(row), (
        'the "put it into planning" row does not keep the Intake step in the history'
    )


def test_anything_else_is_intake_first():
    assert _loose("Anything else he files follows the normal rule, which is Intake first").search(
        _section()
    )


def test_states_why_a_hand_filed_card_is_safe_from_the_fleet():
    section = _section()
    # No dispatch from Intake.
    assert _loose("Nothing dispatches a card from Intake").search(section)
    # The groomer's `hand-built` exclusion.
    assert "groom_verify_agent.exclusion" in section and "DRE-5306" in section
    # The sweep's `hand-built` skip.
    assert "DRE-2524" in section and _loose("stranded watchdog").search(section)


def test_names_the_urgent_fast_path_as_the_route_out_of_intake():
    """`reconcile.advance_urgent_intake` reads no `hand-built` label, so an
    Urgent hand-built card IS planned and can be routed FLEET. The section must
    say so and must not promise the card is never planned."""
    section = _section()
    assert "advance_urgent_intake" in section, (
        "the section does not name the Urgent fast path that can move a "
        "hand-built Intake card to Planning"
    )
    assert _loose("never filed at Urgent or raised to it").search(section)
    assert _loose("move it back to Intake").search(section)
    assert _loose("no code checks it").search(section)
    assert not _loose("never planned, routed or promoted").search(section), (
        "the section promises a hand-built card is never planned, but the "
        "Urgent fast path plans it"
    )
    # The groomer's batch can list the card; verification is what drops it.
    assert _loose("verification drops it from the batch").search(section)
    assert not _loose("The groomer never proposes").search(section)


def test_states_build_conduct_is_guidance_nothing_checks():
    section = _section()
    assert _loose("is guidance to the session").search(section)
    assert _loose("Nothing in the pipeline checks it").search(section)


def test_every_lane_named_is_one_the_contract_carries():
    section = _section()
    carried = _contract_lanes()
    for lane in LANES:
        assert lane in section, f"the section does not name {lane!r}"
        assert lane in carried, f"{lane!r} is not a lane in config/lane-contract.json"
