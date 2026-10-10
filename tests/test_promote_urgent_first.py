"""The sweep gives a build slot to an Urgent card before older ones (DRE-6567).

`promote_ready` sorted its candidates by card number and by nothing else, so an
Urgent fix filed today waited behind every older ready card. On 2026-10-09 at
21:52 PT the scheduled sweep printed `WIP at cap (12/12)` and `31 candidate(s)
not considered this sweep, lowest-numbered DRE-2828`; DRE-6560 — Urgent, the
fix several of the twelve cards in flight were waiting on — was the
highest-numbered of the 31, and an operator moved it to Todo by hand.

What this module pins down:

  1. The order is (Urgent first, then card number) and nothing else: Urgent
     cards ascending by number, then the rest ascending by number, a card with
     no `priority` among the rest.
  2. Every gate still holds an Urgent card: blocked, held, or under an epic
     that is not releasing its children, and the next card in order goes.
  3. At the cap, ONE line per sweep names every Urgent card of this repo the
     cap held back, and says the gates were not asked.
  4. The budget-spent line still names the lowest-NUMBERED unconsidered card
     when an Urgent card stands first in the order.
  5. The Backlog read selects `priority`, in the same request.

Run: cd bureau-pipeline && python3 -m pytest tests/test_promote_urgent_first.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

ROUTING_FLEET = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")

URGENT_LINE = "promotion: Urgent card(s) held at the cap, gates not yet asked: "

#: The `priority` key is left off the card entirely — what a read that never
#: selected the field returns.
NO_FIELD = object()


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _card(identifier, priority=NO_FIELD, *, labels=("repo:agent-bureau",),
          relations=(), parent=None):
    """A Backlog card eligible on every ground but the one under test —
    parentless by default, so its FLEET verdict is its approval and no epic
    read is asked."""
    card = {
        "identifier": identifier,
        "description": "work",
        "createdAt": "2026-10-01T00:00:00.000Z",
        "parent": {"identifier": parent[0], "state": {"name": parent[1]}} if parent else None,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [{"body": ROUTING_FLEET}]},
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
                for i, s in relations
            ]
        },
    }
    if priority is not NO_FIELD:
        card["priority"] = priority
    return card


def _sweep(cards, active_count, *, epic_unmet=()):
    """Run promote_ready over `cards`; the identifiers advanced, in order."""
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()
    with patch.object(reconcile, "backlog_children", return_value=cards), patch.object(
        reconcile, "epic_blockers_unmet", side_effect=lambda epic: epic in epic_unmet
    ), patch.object(reconcile, "epic_records", return_value={}), patch.object(
        reconcile, "card_state", return_value="Done"
    ), patch.object(
        reconcile.mid_epic, "last_green_light", return_value=None
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ), patch.object(reconcile.linear_ops, "count_comments", return_value=0):
        reconcile.promote_ready(active_count=active_count)
    return [c.args[0] for c in advance.call_args_list]


def _urgent_lines(out):
    return [ln for ln in out.splitlines() if ln.startswith(URGENT_LINE)]


# --------------------------------------------------------------------------
# The order
# --------------------------------------------------------------------------
def test_replay_2026_10_09_the_urgent_card_numbered_highest_takes_the_one_slot():
    """A full cap, 31 candidates of this repo with the Urgent one numbered
    highest, and one slot freeing: the Urgent card is the one promoted."""
    older = [_card(f"DRE-{2828 + n}", 3) for n in range(30)]
    cards = [*older, _card("DRE-6560", reconcile.URGENT_PRIORITY)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP - 1) == ["DRE-6560"]


def test_two_urgent_cards_the_lower_numbered_goes_first():
    cards = [_card("DRE-100", 2), _card("DRE-500", 1), _card("DRE-400", 1)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP - 1) == ["DRE-400"]


def test_with_no_urgent_card_the_lowest_numbered_goes_as_before():
    cards = [_card("DRE-300", 2), _card("DRE-100", 3), _card("DRE-200", 0)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP - 1) == ["DRE-100"]


def test_the_order_is_urgent_first_then_card_number_and_nothing_else(monkeypatch):
    """Room for all of them, so the advance order IS the promotion order.
    Priority 2, 3 and 4, 0 and a card with no field at all are one class —
    the rest — ordered by number alone."""
    monkeypatch.setattr(reconcile, "MAX_WIP", 50)
    cards = [
        _card("DRE-20", 0),
        _card("DRE-30", 1),
        _card("DRE-7"),  # no `priority` field
        _card("DRE-5", 2),
        _card("DRE-10", 1),
        _card("DRE-9", None),
        _card("DRE-6", 4),
    ]
    assert _sweep(cards, active_count=0) == [
        "DRE-10", "DRE-30", "DRE-5", "DRE-6", "DRE-7", "DRE-9", "DRE-20",
    ]


# --------------------------------------------------------------------------
# Every gate still holds an Urgent card
# --------------------------------------------------------------------------
@pytest.mark.parametrize("urgent, epic_unmet", [
    (_card("DRE-900", 1, relations=[("DRE-899", "In Review")]), ()),
    (_card("DRE-900", 1, labels=("repo:agent-bureau", reconcile.HOLD_LABEL)), ()),
    (_card("DRE-900", 1, parent=("DRE-2801", "In Progress")), ("DRE-2801",)),
    (_card("DRE-900", 1, parent=("DRE-2801", "Green Light")), ()),
], ids=["blocked", "held", "epic-not-releasing", "epic-not-active"])
def test_a_gated_urgent_card_is_still_not_promoted_and_the_next_one_is(urgent, epic_unmet):
    cards = [_card("DRE-101", 3), _card("DRE-102", 3), urgent]
    assert _sweep(cards, active_count=reconcile.MAX_WIP - 1,
                  epic_unmet=epic_unmet) == ["DRE-101"]


# --------------------------------------------------------------------------
# The at-cap line
# --------------------------------------------------------------------------
def test_at_the_cap_one_line_names_the_waiting_urgent_card(capsys):
    cards = [_card("DRE-2828", 3), _card("DRE-6560", 1)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP) == []
    assert _urgent_lines(capsys.readouterr().out) == [URGENT_LINE + "DRE-6560"]


def test_at_the_cap_two_waiting_urgent_cards_are_named_in_one_line(capsys):
    cards = [_card("DRE-2828", 3), _card("DRE-6561", 1), _card("DRE-6560", 1)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP) == []
    assert _urgent_lines(capsys.readouterr().out) == [URGENT_LINE + "DRE-6560, DRE-6561"]


def test_at_the_cap_with_no_urgent_card_waiting_the_line_is_absent(capsys):
    cards = [_card("DRE-2828", 3), _card("DRE-2829"), _card("DRE-2830", 2)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP) == []
    out = capsys.readouterr().out
    assert _urgent_lines(out) == []
    assert "Urgent" not in out


def test_the_line_names_a_blocked_urgent_card_because_the_cap_asks_no_gate(capsys):
    """At the cap the loop stops a slot-taking card before the blocker gate
    runs, so a blocked Urgent card is named — the line says the gates were not
    asked, and that is exactly true."""
    blocked = _card("DRE-6560", 1, relations=[("DRE-6559", "In Progress")])
    assert _sweep([_card("DRE-2828", 3), blocked], active_count=reconcile.MAX_WIP) == []
    out = capsys.readouterr().out
    assert _urgent_lines(out) == [URGENT_LINE + "DRE-6560"]
    assert "DRE-6559" not in out  # the blocker gate never ran


def test_an_urgent_card_promoted_this_sweep_is_not_named_but_the_next_one_held_is(capsys):
    cards = [_card("DRE-2828", 3), _card("DRE-6560", 1), _card("DRE-6561", 1)]
    assert _sweep(cards, active_count=reconcile.MAX_WIP - 1) == ["DRE-6560"]
    assert _urgent_lines(capsys.readouterr().out) == [URGENT_LINE + "DRE-6561"]


def test_another_repos_urgent_card_is_not_named(capsys):
    theirs = _card("DRE-6560", 1, labels=("repo:deltasolv",))
    assert _sweep([_card("DRE-2828", 3), theirs], active_count=reconcile.MAX_WIP) == []
    assert _urgent_lines(capsys.readouterr().out) == []


# --------------------------------------------------------------------------
# The budget-spent line keeps telling the truth
# --------------------------------------------------------------------------
def test_budget_spent_line_names_the_lowest_numbered_even_with_urgent_first(capsys):
    cards = [_card("DRE-6560", 1), _card("DRE-2828", 3), _card("DRE-2900")]
    assert _sweep(cards, active_count=reconcile.MAX_WIP) == []
    budget = [ln for ln in capsys.readouterr().out.splitlines()
              if "WIP budget spent" in ln]
    assert len(budget) == 1, budget
    assert "3 candidate(s) not considered" in budget[0]
    assert budget[0].endswith("lowest-numbered DRE-2828")


# --------------------------------------------------------------------------
# The field
# --------------------------------------------------------------------------
def test_the_backlog_read_selects_priority_in_its_one_request():
    seen = []

    def gql_paged(query, variables=None):
        seen.append(query)
        return []

    with patch.object(reconcile.linear_ops, "gql_paged", side_effect=gql_paged):
        assert reconcile.backlog_children(from_linear=True) == []
    assert len(seen) == 1
    nodes = seen[0].split("nodes {", 1)[1]
    assert re.search(r"\bpriority\b", nodes.split("parent {", 1)[0]), seen[0]
