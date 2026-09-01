"""TDD for the re-runnable prose-blocker survey (DRE-2676).

The card's plan turned on a MEASUREMENT: 293 live DRE cards, 35 carrying a
prose blocker declaration, 44 declarations, 44 corroborated by a formal
`blocks` relation, **0 prose-only**. That number is what made the strict gate
safe to ship — and a number that lives only in a card description is a number
that is true on the day it was written.

So it is computed, never remembered, in the `make check-channel-fleet` spirit:
ask the command rather than trusting a figure written into prose. The house
rule it serves (`adr-one-writer-per-fact`) is the same one that replaced this
repo's hand-counted channel roster.

The counting is a pure function over cards the check has already fetched, so
this module drives it offline; the live read is a thin shell over
`linear_ops.gql_paged` and is exercised through a stubbed pager.

Run: cd bureau-pipeline && python3 -m pytest tests/test_check_prose_blockers.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import check_prose_blockers as check  # noqa: E402


def _relation(identifier: str, state: str = "In Progress") -> dict:
    return {"type": "blocks", "issue": {"identifier": identifier, "state": {"name": state}}}


def _card(identifier, description="work", relations=(), parent=None) -> dict:
    return {
        "identifier": identifier,
        "description": description,
        "parent": {"identifier": parent} if parent else None,
        "inverseRelations": {"nodes": list(relations)},
    }


# ---------------------------------------------------------------------------
# the counting, offline
# ---------------------------------------------------------------------------
def test_a_board_with_no_declarations_at_all():
    survey = check.survey([_card("DRE-1"), _card("DRE-2")])
    assert survey.live == 2
    assert survey.declaring == ()
    assert survey.references == 0
    assert survey.prose_only == ()


def test_a_corroborated_declaration_counts_as_corroborated():
    survey = check.survey([
        _card("DRE-1", "**Blocked by:** DRE-9", [_relation("DRE-9")]),
        _card("DRE-2"),
    ])
    assert survey.live == 2
    assert survey.declaring == ("DRE-1",)
    assert survey.references == 1
    assert survey.corroborated == 1
    assert survey.prose_only == ()


def test_a_prose_only_declaration_is_named_not_just_counted():
    """The whole value of the check is that it says WHICH card, so somebody can
    go and fix it — a bare count is a number nobody can act on."""
    survey = check.survey([_card("DRE-1", "**Blocked by:** DRE-9")])
    assert survey.references == 1
    assert survey.corroborated == 0
    assert survey.prose_only == (("DRE-1", "DRE-9"),)


def test_a_terminal_relation_still_corroborates():
    """The gate subtracts every `blocks` relation, terminal or not, so this
    survey has to count the same set — a check that disagreed with the gate it
    reports on would be measuring a different board."""
    survey = check.survey([
        _card("DRE-1", "**Blocked by:** DRE-9", [_relation("DRE-9", "Done")]),
    ])
    assert survey.corroborated == 1
    assert survey.prose_only == ()


def test_one_card_can_carry_several_references():
    survey = check.survey([
        _card("DRE-1", "**Blocked by:** DRE-9, DRE-8", [_relation("DRE-9")]),
    ])
    assert survey.declaring == ("DRE-1",)
    assert survey.references == 2
    assert survey.corroborated == 1
    assert survey.prose_only == (("DRE-1", "DRE-8"),)


def test_a_mention_is_not_a_declaration():
    """The survey reads the SAME anchored parser the gate does (DRE-2922), so
    the DRE-2492 sentences count as nothing here too. A survey with its own
    grammar would report a defect the sweep does not see."""
    survey = check.survey([
        _card("DRE-1", "Both depend on DRE-2494 only - neither depends on the other."),
        _card("DRE-2", "DRE-2496 lands first. B3 is formally blocked by it."),
    ])
    assert survey.declaring == ()
    assert survey.references == 0


def test_the_parent_epic_is_never_counted_as_a_claim():
    """The gate excludes it, so the survey must not report it as a defect."""
    survey = check.survey([
        _card("DRE-2", "**Blocked by:** DRE-1", parent="DRE-1"),
    ])
    assert survey.references == 0
    assert survey.prose_only == ()


def test_a_full_relation_page_is_reported_rather_than_assumed_complete():
    """`inverseRelations` is fetched a page at a time. A card that FILLS the
    page may have relations the survey never saw, and calling it prose-only
    would be a confident wrong answer — the honesty rule the console standard
    states for stale data, one system over."""
    relations = [_relation(f"DRE-{n}") for n in range(check.RELATION_PAGE)]
    survey = check.survey([_card("DRE-1", "**Blocked by:** DRE-9", relations)])
    assert survey.truncated == ("DRE-1",)


def test_a_short_relation_page_is_not_reported_as_truncated():
    survey = check.survey([_card("DRE-1", "**Blocked by:** DRE-9", [_relation("DRE-9")])])
    assert survey.truncated == ()


# ---------------------------------------------------------------------------
# what it prints, and what it exits
# ---------------------------------------------------------------------------
def test_the_report_states_every_measure_the_card_asked_for():
    survey = check.survey([
        _card("DRE-1", "**Blocked by:** DRE-9", [_relation("DRE-9")]),
        _card("DRE-2"),
    ])
    report = check.render(survey)
    assert "live" in report.lower()
    assert "2" in report and "1" in report
    assert "prose-only" in report.lower()


def test_a_clean_board_exits_zero():
    cards = [_card("DRE-1", "**Blocked by:** DRE-9", [_relation("DRE-9")])]
    with patch.object(check, "live_cards", return_value=cards):
        assert check.main([]) == 0


def test_a_prose_only_card_exits_one_and_names_it(capsys):
    """It has teeth: a prose-only card is a defect the sweep will route to
    Triage, and the check is how anyone asks whether the board holds one."""
    with patch.object(check, "live_cards", return_value=[_card("DRE-1", "**Blocked by:** DRE-9")]):
        assert check.main([]) == 1
    assert "DRE-1" in capsys.readouterr().out


def test_an_unreadable_board_is_not_an_empty_board(capsys):
    """DRE-2034's rule: a read that failed must never render as a clean result.
    Exit 2 — neither the green of a measured board nor the red of a finding."""
    def _boom(*args, **kwargs):
        raise check.linear_ops.LinearError("linear error: rate limited")

    with patch.object(check, "live_cards", side_effect=_boom):
        assert check.main([]) == 2
    assert "rate limited" in capsys.readouterr().err


def test_the_roster_read_skips_terminal_cards():
    """Scope is LIVE cards. Done/Canceled/Duplicate are never promotion
    candidates, so they are not scanned — and saying so is part of the
    measurement's provenance, not an optimisation."""
    seen: dict = {}

    def _paged(query, variables=None):
        seen["query"] = query
        return [{"identifier": "DRE-1"}, {"identifier": "DRE-2"}]

    with patch.object(check.linear_ops, "gql_paged", _paged):
        assert check.live_identifiers() == ["DRE-1", "DRE-2"]
    assert "completed" in seen["query"] and "canceled" in seen["query"]


def test_each_card_is_read_IN_FULL_never_off_the_list():
    """The measurement the whole plan rests on is only possible this way:
    Linear's LIST api truncates `description` at 500 characters, which is
    exactly why the card's original step 1 said the count could not be
    established. A survey that read bodies off the roster would under-count and
    report a clean board it never looked at."""
    reads: list = []

    def _read(identifier):
        reads.append(identifier)
        return _card(identifier, "**Blocked by:** DRE-9")

    with patch.object(check, "live_identifiers", return_value=["DRE-1", "DRE-2"]), \
        patch.object(check, "read_card", _read):
        cards = check.live_cards()
    assert reads == ["DRE-1", "DRE-2"]
    assert [c["identifier"] for c in cards] == ["DRE-1", "DRE-2"]


def test_the_full_read_asks_for_the_body_the_relations_and_the_parent():
    seen: dict = {}

    def _gql(query, variables=None):
        seen["query"] = query
        return {"issue": _card("DRE-1")}

    with patch.object(check.linear_ops, "gql", _gql):
        assert check.read_card("DRE-1")["identifier"] == "DRE-1"
    query = seen["query"]
    assert "description" in query
    assert "inverseRelations" in query
    assert "parent" in query
