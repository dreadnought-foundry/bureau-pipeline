"""Within a band, work already in flight goes first (DRE-6664).

The CEO's decision of 2026-10-10 on DRE-6643: inside one priority band a card
that serves an epic already In Progress goes ahead of the rest, and the rest
stay newest first. "Serves" is read off the board, never off text — the card's
parent epic is In Progress, or the card has a Linear `blocks` relation to a
card whose parent epic is In Progress. A unit (an epic with its children, or
one parentless card) is in flight when any member serves.

What this file pins, each a way the rule could quietly stop holding:

  * the key reorders inside a band and never across one;
  * a `related` relation is not a `blocks` one;
  * collisions and `blockedBy` stay constraints on top, and the page names the
    pair when the key could not be honoured;
  * the population read carries the fields the rule reads, shaped exactly as
    the query returns them, at a page Linear's per-request limit never meets.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_in_flight_first.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer_population import CYCLES, card  # noqa: E402

DOC = ROOT / "docs" / "groomer.md"
SOURCE = ROOT / "scripts" / "groomer.py"

EPIC = "DRE-50"


def _with_parent(c: dict, epic: str = EPIC, state: str = "In Progress") -> dict:
    """`c` under `epic`, the parent selected as `POPULATION_QUERY` selects it."""
    c["parent"] = {"identifier": epic, "title": f"[EPIC] {epic}",
                   "state": {"name": state}}
    return c


def _blocks(c: dict, target: str, *, epic: str = EPIC,
            epic_state: str = "In Progress", kind: str = "blocks",
            target_state: str = "Todo", archived: str | None = None) -> dict:
    """`c` carries a `kind` relation to `target`, a child of `epic`."""
    c.setdefault("relations", {"nodes": []})["nodes"].append({
        "type": kind,
        "relatedIssue": {"identifier": target,
                         "state": {"name": target_state},
                         "archivedAt": archived,
                         "parent": {"identifier": epic,
                                    "state": {"name": epic_state}}},
    })
    return c


def _order(cards: list[dict], **kw) -> list[str]:
    return [r["identifier"] for r in groomer.sequence(cards, **kw)]


def _band_of_four(epic_state: str) -> list[dict]:
    return [
        _with_parent(card("DRE-100", created="2026-09-01T00:00:00.000Z"),
                     state=epic_state),
        card("DRE-101", created="2026-09-10T00:00:00.000Z"),
        card("DRE-102", created="2026-09-11T00:00:00.000Z"),
        card("DRE-103", created="2026-09-12T00:00:00.000Z"),
    ]


# --------------------------------------------------------------------------
# the key, inside one band
# --------------------------------------------------------------------------
def test_an_older_card_serving_an_epic_in_progress_opens_its_band():
    assert _order(_band_of_four("In Progress")) == [
        "DRE-100", "DRE-103", "DRE-102", "DRE-101"]


def test_with_the_epic_in_backlog_the_band_is_newest_first_unchanged():
    assert _order(_band_of_four("Backlog")) == [
        "DRE-103", "DRE-102", "DRE-101", "DRE-100"]


def test_a_blocks_relation_to_a_child_of_an_epic_in_progress_is_in_flight():
    cards = [
        _blocks(card("DRE-110", created="2026-09-01T00:00:00.000Z"), "DRE-60"),
        card("DRE-111", created="2026-09-10T00:00:00.000Z"),
    ]
    assert groomer.serves_in_flight(cards[0]) == EPIC
    assert _order(cards) == ["DRE-110", "DRE-111"]


def test_a_related_relation_to_the_same_card_is_not():
    cards = [
        _blocks(card("DRE-110", created="2026-09-01T00:00:00.000Z"), "DRE-60",
                kind="related"),
        card("DRE-111", created="2026-09-10T00:00:00.000Z"),
    ]
    assert groomer.serves_in_flight(cards[0]) is None
    assert _order(cards) == ["DRE-111", "DRE-110"]


def test_a_blocks_relation_to_an_archived_or_closed_card_serves_nothing():
    archived = _blocks(card("DRE-112"), "DRE-60",
                       archived="2026-10-01T00:00:00.000Z")
    closed = _blocks(card("DRE-113"), "DRE-61", target_state="Done")
    assert groomer.serves_in_flight(archived) is None
    assert groomer.serves_in_flight(closed) is None


def test_the_in_flight_states_are_exactly_in_progress():
    assert groomer.IN_FLIGHT_EPIC_STATES_FOR_ORDER == ("In Progress",)


def test_the_key_never_moves_a_card_across_bands():
    cards = [
        card("DRE-120", created="2026-09-01T00:00:00.000Z",
             priority=groomer.URGENT),
        _with_parent(card("DRE-121", created="2026-09-10T00:00:00.000Z",
                          priority=groomer.HIGH)),
    ]
    assert groomer.serves_in_flight(cards[1]) == EPIC
    assert _order(cards) == ["DRE-120", "DRE-121"]


# --------------------------------------------------------------------------
# the unit carries the answer
# --------------------------------------------------------------------------
def test_each_unit_says_whether_it_is_in_flight_and_what_it_serves():
    cards = [
        # A parentless card serving an epic through a blocks relation, one
        # serving nothing, and an epic in Backlog.
        _blocks(card("DRE-131"), "DRE-70", epic="DRE-77"),
        card("DRE-132"),
        _with_parent(card("DRE-133"), epic="DRE-88", state="Backlog"),
    ]
    by_key = {u["key"]: u for u in groomer.units(cards)}
    assert by_key["DRE-131"]["in_flight"] is True
    assert by_key["DRE-131"]["serves"] == "DRE-77"
    assert by_key["DRE-132"]["in_flight"] is False
    assert by_key["DRE-132"]["serves"] is None
    assert by_key["DRE-88"]["in_flight"] is False
    assert by_key["DRE-88"]["serves"] is None


def test_a_unit_serves_the_first_epic_found_by_card_identifier_order():
    # One epic in the lane with two children, each serving a different epic
    # through a blocks relation: the lower identifier's answer is the unit's.
    a = _blocks(_with_parent(card("DRE-141"), epic="DRE-140", state="Backlog"),
                "DRE-71", epic="DRE-91")
    b = _blocks(_with_parent(card("DRE-142"), epic="DRE-140", state="Backlog"),
                "DRE-72", epic="DRE-92")
    (unit,) = groomer.units([b, a])
    assert unit["in_flight"] is True and unit["serves"] == "DRE-91"


# --------------------------------------------------------------------------
# the constraints stay on top, and the page says when they won
# --------------------------------------------------------------------------
def test_an_in_flight_card_blocked_by_an_earlier_card_still_follows_it():
    flying = _with_parent(card("DRE-150", created="2026-09-01T00:00:00.000Z"))
    flying["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-151",
                                     "state": {"name": "Intake"}}}]}
    cards = [flying,
             card("DRE-151", created="2026-09-05T00:00:00.000Z"),
             card("DRE-152", created="2026-09-10T00:00:00.000Z")]
    assert _order(cards) == ["DRE-151", "DRE-150", "DRE-152"]
    proposal = groomer.propose(cards, cycles=CYCLES)
    named = [c for c in proposal["unhonoured_constraints"]
             if c.get("before") == "DRE-151" and c.get("after") == "DRE-150"]
    assert named, (
        "the in-flight unit was held back by a constraint and the page did "
        f"not name the pair: {proposal['unhonoured_constraints']}")
    assert "DRE-151" in groomer.render_proposal(proposal).split(
        "## Collisions, and what the order does about them")[1]


def test_an_in_flight_card_colliding_with_a_newer_card_follows_it():
    flying = _with_parent(card("DRE-160", created="2026-09-01T00:00:00.000Z",
                               description="edits `ledger.py`"))
    cards = [flying,
             card("DRE-161", created="2026-09-05T00:00:00.000Z",
                  description="also edits `scripts/ledger.py`"),
             card("DRE-162", created="2026-09-10T00:00:00.000Z")]
    assert _order(cards) == ["DRE-161", "DRE-160", "DRE-162"]
    proposal = groomer.propose(cards, cycles=CYCLES)
    assert any(c.get("before") == "DRE-161" and c.get("after") == "DRE-160"
               for c in proposal["unhonoured_constraints"])


def test_an_honoured_key_names_nothing():
    proposal = groomer.propose(_band_of_four("In Progress"), cycles=CYCLES)
    assert proposal["unhonoured_constraints"] == []


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------
def test_a_rules_only_row_placed_by_the_key_says_so_in_its_why_cell():
    proposal = groomer.propose(_band_of_four("In Progress"), cycles=CYCLES)
    row = next(r for r in proposal["outcomes"]["now"]
               if r["identifier"] == "DRE-100")
    assert "in flight — serves DRE-50 (In Progress)" in row["reason"]
    others = [r for r in proposal["outcomes"]["now"]
              if r["identifier"] != "DRE-100"]
    assert others and all("in flight" not in r["reason"] for r in others)
    text = groomer.render_proposal(proposal)
    line = next(ln for ln in text.splitlines() if "| DRE-100 |" in ln)
    assert "in flight — serves DRE-50 (In Progress)" in line


def test_the_batch_paragraph_names_the_rule_when_it_placed_a_card():
    text = groomer.render_proposal(
        groomer.propose(_band_of_four("In Progress"), cycles=CYCLES))
    paragraph = text.split("## The batch, in order")[1].split("| # |")[0]
    assert "Within a band, a card serving an epic In Progress goes first" \
        in paragraph


def test_the_batch_paragraph_is_unchanged_when_nothing_is_in_flight():
    text = groomer.render_proposal(
        groomer.propose(_band_of_four("Backlog"), cycles=CYCLES))
    assert "In Progress goes first" not in text


# --------------------------------------------------------------------------
# the population read
# --------------------------------------------------------------------------
CONTRACT_FIELDS = (
    "parent { identifier title state { name } }",
    "relations(first: 20) { nodes { type relatedIssue { identifier state "
    "{ name } archivedAt parent { identifier state { name } } } } }",
    "inverseRelations(first: 20) { nodes { type issue { identifier state "
    "{ name } archivedAt parent { identifier state { name } } } } }",
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_population_query_carries_the_contract_fields_on_every_row():
    flat = _flat(groomer.POPULATION_QUERY)
    for field in CONTRACT_FIELDS:
        assert field in flat, f"POPULATION_QUERY lacks `{field}`"


def test_serves_in_flight_reads_a_row_shaped_exactly_as_the_query_returns_it():
    row = {
        "identifier": "DRE-170", "title": "a thing", "description": "",
        "createdAt": "2026-09-01T00:00:00.000Z", "priority": 0,
        "state": {"name": "Intake"},
        "labels": {"nodes": [{"name": "repo:portico"}]},
        "parent": None, "project": None, "cycle": None,
        "relations": {"nodes": [
            {"type": "related", "relatedIssue": {
                "identifier": "DRE-171", "state": {"name": "Todo"},
                "archivedAt": None,
                "parent": {"identifier": "DRE-99", "state": {"name": "In Progress"}}}},
            {"type": "blocks", "relatedIssue": {
                "identifier": "DRE-172", "state": {"name": "Todo"},
                "archivedAt": None,
                "parent": {"identifier": "DRE-98", "state": {"name": "In Progress"}}}},
        ]},
        "inverseRelations": {"nodes": [
            {"type": "blocks", "issue": {
                "identifier": "DRE-173", "state": {"name": "Todo"},
                "archivedAt": None,
                "parent": {"identifier": "DRE-97", "state": {"name": "In Progress"}}}},
        ]},
        "children": {"nodes": []},
    }
    assert groomer.serves_in_flight(row) == "DRE-98", (
        "only a `blocks` relation FROM this card serves; being blocked by a "
        "child of an epic in flight is not serving it")
    row["parent"] = {"identifier": "DRE-96", "title": "[EPIC] x",
                     "state": {"name": "In Progress"}}
    assert groomer.serves_in_flight(row) == "DRE-96", "the parent is read first"


def test_the_page_is_at_most_25_while_the_query_names_both_relation_lists():
    assert groomer.POPULATION_PAGE == 25
    q = groomer.POPULATION_QUERY
    if " relations(" in q or "inverseRelations(" in q:
        assert groomer.POPULATION_PAGE <= 25, (
            "the widened query costs more per row; the page was measured at 25")
    assert f"issues(first: {groomer.POPULATION_PAGE}, after: $after" in q


def test_the_comment_above_the_page_carries_the_measured_complexity_and_date():
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    at = next(i for i, ln in enumerate(lines)
              if ln.startswith("POPULATION_PAGE ="))
    comment = []
    for ln in reversed(lines[:at]):
        if not ln.startswith("#"):
            break
        comment.append(ln)
    block = " ".join(reversed(comment))
    assert re.search(r"X-Complexity[^0-9]{0,40}\d+", block), block
    assert re.search(r"2026-\d\d-\d\d", block), block


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def test_the_doc_states_the_step_with_the_decision_and_readiness_outranks_it():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## The order, applied top to bottom")[1].split("\n## ")[0]
    flat = _flat(section)
    assert "DRE-6643" in flat and "2026-10-10" in flat
    assert "In Progress" in flat and "goes first" in flat
    assert re.search(r"[Rr]eadiness (outranks|wins)", flat)
