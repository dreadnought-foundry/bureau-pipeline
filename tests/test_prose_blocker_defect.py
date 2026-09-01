"""TDD for "a blocker is a Linear relation or it does not exist" (DRE-2676).

The sweep read dependencies out of TWO sources — the formal `blockedBy`
relation and a `**Blocked by:**` line in the card's body — and honoured both.
That is one question with two answers, and the prose half is the one no gate,
no console and no auto-close can see. It froze five cards for five days once
already (DRE-2670, epic DRE-2492), and the remedy then was to ANCHOR the
grammar so a mention is not a declaration.

This card ends the second answer. On 2026-08-31 the live board was measured
card by card — 293 live DRE cards, 35 carrying a prose blocker declaration, 44
declarations in total, **44 of them corroborated by a formal `blocks` relation
and 0 prose-only**. Three cards carry a relation the prose does not mention.
The relations are already the richer and more current source, so a
relations-only gate returns exactly what the sweep returns today, and the
strict version ships with no behaviour-change window.

What is TDD'd here:

  1. THE SPLIT — `relation_blockers()` is the gate (non-terminal `blocks`
     relations) and `prose_claims()` is evidence only. `unmet` is computed from
     relations alone, so prose can no longer ADD a blocker, and the gate no
     longer fetches a prose reference's state.

  2. PROSE THAT DISAGREES WITH THE BOARD IS A DEFECT — a claim with no `blocks`
     relation of any state refuses the card out loud, comments ONCE, and moves
     it to Triage (the broken-card lane: something about the CARD is wrong).
     Not Green Light — a malformed sentence is a mechanical fix, not a CEO
     decision.

  3. PROSE THAT AGREES WITH THE BOARD IS INERT — all 44 declarations live
     today. Nothing is refused and no description is rewritten.

  4. THE EPIC GATE FOLLOWS THE SAME RULE, with a different escalation: it
     refuses and comments, it does NOT move the epic's lane (Triage triggers a
     planner run, and a planning run cannot fix a sentence), and it turns the
     sweep run red once the defect is more than two hours old.

Reconcile governs promotion for EVERY product repo, so a bug here breaks
everyone — hence test-first.

Run: cd bureau-pipeline && python3 -m pytest tests/test_prose_blocker_defect.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises these agent-bureau cards regardless of collection order, and
    empty the module-level ledgers so tests never see each other's entries."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile._card_skips.clear()


def _relation(identifier: str, state: str = "In Progress") -> dict:
    return {"type": "blocks", "issue": {"identifier": identifier, "state": {"name": state}}}


def _card(identifier="DRE-900", description="work", relations=(), parent="DRE-800",
          parent_state="In Progress") -> dict:
    """A Backlog child eligible on every other ground — so only the dependency
    gate and the new prose-defect refusal can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\n" + description,
        "parent": {"identifier": parent, "state": {"name": parent_state}} if parent else None,
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": list(relations)},
    }


def _minutes_ago(minutes: float) -> str:
    """An ISO timestamp `minutes` in the past — the shape Linear stamps a
    comment's `createdAt` with, which is the sweep's clock for how long a
    defect has stood unfixed."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat().replace(
        "+00:00", "Z"
    )


def _no_state_fetch(identifier):
    raise AssertionError(
        f"the gate fetched {identifier}'s state — a blocker is a relation, and "
        "a relation carries its blocker's state inline"
    )


# ---------------------------------------------------------------------------
# 1. The split: relation_blockers is the gate, prose_claims is evidence
# ---------------------------------------------------------------------------
def test_relation_blockers_are_the_non_terminal_blocks_relations():
    card = _card(relations=[
        _relation("DRE-700", "In Progress"),
        _relation("DRE-701", "Done"),
        _relation("DRE-702", "Canceled"),
        _relation("DRE-703", "Duplicate"),
        {"type": "related", "issue": {"identifier": "DRE-704", "state": {"name": "Todo"}}},
    ])
    assert prose_blockers.relation_blockers(card) == {"DRE-700"}


def test_relation_blockers_ignore_the_description_entirely():
    """The gate cannot be added to from prose. This is the whole card."""
    card = _card(description="**Blocked by:** DRE-700")
    assert prose_blockers.relation_blockers(card) == set()


def test_prose_claims_reads_declarations_and_nothing_else():
    card = _card(
        identifier="DRE-900",
        description=(
            "**Blocked by:** DRE-700, DRE-900\n"
            "Serialize after: DRE-800\n"
            "Note that DRE-11 depends on nothing here, and neither depends on the other.\n"
        ),
        parent="DRE-800",
    )
    # The card's own id and its parent epic's id are never claims: an epic only
    # closes when its children finish, so an epic ref would deadlock forever.
    assert prose_blockers.prose_claims(card) == {"DRE-700"}


def test_prose_claims_never_reads_a_mention_as_a_declaration():
    """The DRE-2492 sentences, verbatim. The anchoring DRE-2670 paid five days
    for does not weaken because the gate stopped honouring prose — this parser
    is now the DEFECT DETECTOR, and a false positive here would route a
    well-written card to Triage."""
    card = _card(description=(
        "Wave 2 sequencing: DRE-2496 **lands first.** B3 is formally blocked by "
        "it, so it cannot start before the rail exists.\n"
        "\n"
        "Both depend on DRE-2494 only - neither depends on the other, and neither "
        "blocks anything in wave 4.\n"
    ))
    assert prose_blockers.prose_claims(card) == set()
    assert prose_blockers.undeclared_claims(card) == set()


def test_prose_that_agrees_with_the_board_is_inert():
    """All 44 declarations on the board today. The line stays as human
    documentation and changes nothing."""
    card = _card(description="**Blocked by:** DRE-700", relations=[_relation("DRE-700")])
    assert prose_blockers.undeclared_claims(card) == set()


def test_a_terminal_relation_still_corroborates_the_prose():
    """`undeclared` subtracts EVERY `blocks` relation, terminal or not. A card
    whose declared dependency has shipped is documenting history, not claiming
    a dependency the board does not hold."""
    card = _card(description="**Blocked by:** DRE-700",
                 relations=[_relation("DRE-700", "Done")])
    assert prose_blockers.relation_blockers(card) == set()
    assert prose_blockers.undeclared_claims(card) == set()


def test_a_claim_with_no_relation_of_any_state_is_undeclared():
    card = _card(description="**Blocked by:** DRE-700",
                 relations=[_relation("DRE-701", "Done")])
    assert prose_blockers.undeclared_claims(card) == {"DRE-700"}


# ---------------------------------------------------------------------------
# 1 wired into promote_ready: prose can no longer hold a card
# ---------------------------------------------------------------------------
def test_a_card_whose_only_unmet_blocker_is_prose_declared_now_promotes():
    """The behaviour change, at the gate.

    The card declares `**Blocked by:** DRE-700` in its body and the board holds
    the matching relation, already Done — so nothing blocks it. `card_state` is
    patched to EXPLODE: the old gate resolved every prose reference by fetching
    its live state, and this asserts the new one never asks. It promotes.
    """
    card = _card(description="**Blocked by:** DRE-700",
                 relations=[_relation("DRE-700", "Done")])
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_no_state_fetch), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-900", "Todo", "Backlog")


def test_a_live_relation_still_holds_the_card():
    """Control: the gate is not disarmed, it is narrowed."""
    card = _card(relations=[_relation("DRE-700", "In Review")])
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_no_state_fetch), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_the_refusal_line_names_the_blocker_its_state_and_its_source(capsys):
    """DRE-2918's rule, kept: the exit that holds a card SPEAKS, and it names
    the blocker, the state it is in, and where the dependency came from."""
    card = _card(relations=[_relation("DRE-700", "In Review")])
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0)
    out = capsys.readouterr().out
    assert "DRE-900" in out and "DRE-700" in out
    assert "In Review" in out                      # its state
    assert "formal blockedBy relation" in out      # its source


# ---------------------------------------------------------------------------
# 2. Prose that disagrees with the board refuses, speaks, and routes
# ---------------------------------------------------------------------------
def _defective(identifier="DRE-900", claimed="DRE-700") -> dict:
    return _card(identifier=identifier, description=f"**Blocked by:** {claimed}")


def test_an_undeclared_prose_claim_refuses_promotion(capsys):
    card = _defective()
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    assert ("DRE-900", "Todo", "Backlog") not in [c.args for c in advance.mock_calls]
    out = capsys.readouterr().out
    assert prose_blockers.CARD_TAG in out       # the named line
    assert "DRE-900" in out and "DRE-700" in out


def test_the_refusal_comment_names_both_fixes_and_the_return_lane():
    card = _defective()
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.promote_ready(active_count=0)
    posted = [c.args[1] for c in comment.call_args_list if prose_blockers.CARD_TAG in c.args[1]]
    assert len(posted) == 1
    body = posted[0]
    assert "DRE-700" in body
    assert "blockedBy" in body                       # recovery 1: add the relation
    assert "Blocked by" in body and "Depends on" in body and "Serialize after" in body
    assert "Backlog" in body                         # the return lane…
    assert "Todo" in body                            # …named against the wrong one


def test_a_defective_card_is_moved_to_triage():
    """Triage, not Green Light: something about the CARD is wrong and it cannot
    proceed as written. A malformed sentence is a mechanical fix, not a CEO
    decision, and Green Light is the queue that costs CEO time."""
    card = _defective()
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0)
    advance.assert_called_once_with("DRE-900", "Triage", "Backlog")


def test_a_defective_card_never_reaches_green_light():
    card = _defective()
    with patch.object(reconcile, "backlog_children", return_value=[card]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0)
    assert all("Green Light" not in call.args for call in advance.mock_calls)


def test_no_card_description_is_rewritten():
    """The 44 corroborated prose lines stay as human documentation, and the
    defective one is not silently repaired either — the pipeline never edits a
    card's body to fix a dependency it disagrees with."""
    cards = [
        _defective("DRE-900"),
        _card("DRE-901", description="**Blocked by:** DRE-700",
              relations=[_relation("DRE-700", "Done")]),
    ]
    with patch.object(reconcile, "backlog_children", return_value=cards), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile.linear_ops, "set_description") as rewrite:
        reconcile.promote_ready(active_count=0)
    rewrite.assert_not_called()


def test_one_defective_card_does_not_stop_its_siblings():
    """Per-card isolation, unchanged (DRE-2035): the defect is this card's."""
    cards = [_defective("DRE-900"), _card("DRE-901")]
    with patch.object(reconcile, "backlog_children", return_value=cards), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    assert ("DRE-901", "Todo", "Backlog") in [c.args for c in advance.mock_calls]


def test_a_failed_triage_move_goes_in_the_ledger_and_the_sweep_continues():
    """The move is a WRITE inside the sweep, so it rides the existing shape:
    the failure fails the run red for medic and never blocks the rest."""
    cards = [_defective("DRE-900"), _card("DRE-901")]

    def _advance(identifier, to_state, from_states):
        if to_state == "Triage":
            raise linear_ops.LinearError("linear error: rate limited")

    with patch.object(reconcile, "backlog_children", return_value=cards), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance", side_effect=_advance), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1                                    # the sibling still ships
    assert any("DRE-900" in failure for failure in reconcile._write_failures)


# ---------------------------------------------------------------------------
# 2, the idempotency half: DRE-2723's dead lane is not recreated
# ---------------------------------------------------------------------------
class _Board:
    """A Linear stand-in that REMEMBERS: the lane a card is in, and the
    comments posted on it. Two sweeps against a mock that forgets would prove
    nothing about repetition, which is the whole question here."""

    def __init__(self, cards):
        self.cards = list(cards)
        self.lane = {card["identifier"]: "Backlog" for card in self.cards}
        self.comments: dict = {card["identifier"]: [] for card in self.cards}
        self.moves: list = []

    def backlog_children(self):
        return [c for c in self.cards if self.lane[c["identifier"]] == "Backlog"]

    def cmd_advance(self, identifier, to_state, from_states_csv):
        # The real write is guarded on the from-lane; a stand-in that ignored
        # that would hide exactly the double-move this test is looking for.
        if self.lane.get(identifier) not in from_states_csv.split(","):
            return
        self.lane[identifier] = to_state
        self.moves.append((identifier, to_state))

    def cmd_comment(self, identifier, body, *flags):
        self.comments.setdefault(identifier, []).append(body)

    def count_comments(self, identifier, needle, since=None):
        return sum(1 for body in self.comments.get(identifier, []) if needle in body)

    def sweep(self):
        with patch.object(reconcile, "backlog_children", self.backlog_children), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile.linear_ops, "count_comments", self.count_comments), \
            patch.object(reconcile.linear_ops, "cmd_advance", self.cmd_advance), \
            patch.object(reconcile.linear_ops, "cmd_comment", self.cmd_comment):
            return reconcile.promote_ready(active_count=0)


def test_two_sweeps_produce_exactly_one_comment_and_one_state_change():
    """The DRE-2723 rule: Triage must not become a lane the sweep re-flags and
    re-comments on every fifteen minutes."""
    board = _Board([_defective("DRE-900")])
    board.sweep()
    board.sweep()
    assert board.moves == [("DRE-900", "Triage")]
    posted = [b for b in board.comments["DRE-900"] if prose_blockers.CARD_TAG in b]
    assert len(posted) == 1


# ---------------------------------------------------------------------------
# 4. The epic gate follows the same rule, with a different escalation
# ---------------------------------------------------------------------------
def _epic(description="epic B", relations=()) -> dict:
    return {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\n" + description,
        "inverseRelations": {"nodes": list(relations)},
    }


def test_an_epic_held_only_by_prose_is_a_defect_not_a_dependency(capsys):
    """It still refuses — an epic that says something false about itself is not
    a healthy epic — but it refuses as a DEFECT, and it says so."""
    with patch.object(reconcile, "_fetch_epic_relations",
                      return_value=_epic("**Blocked by:** DRE-700")), \
        patch.object(reconcile, "card_state", side_effect=_no_state_fetch), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=None), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        assert reconcile.epic_blockers_unmet("DRE-800") is True
    out = capsys.readouterr().out
    assert "DRE-800" in out and "DRE-700" in out
    assert "prose" in out.lower() and "no formal" in out.lower()


def test_an_epic_whose_prose_agrees_with_the_board_is_not_held():
    """Corroborated prose plus a Done relation: the dependency is met, the
    sentence is documentation, and the epic's children promote."""
    epic = _epic("**Blocked by:** DRE-700", [_relation("DRE-700", "Done")])
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
        patch.object(reconcile, "card_state", side_effect=_no_state_fetch), \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        assert reconcile.epic_blockers_unmet("DRE-800") is False
    comment.assert_not_called()


def test_an_epic_prose_defect_comments_once_across_sweeps():
    epic = _epic("**Blocked by:** DRE-700")
    prior = {"count": 0}

    def _count(identifier, needle, since=None):
        return prior["count"] if needle == prose_blockers.EPIC_TAG else 0

    def _sweep():
        with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
            patch.object(reconcile.linear_ops, "count_comments", _count), \
            patch.object(reconcile.linear_ops, "first_comment_at", return_value=None), \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.epic_blockers_unmet("DRE-800")
            return [c.args[1] for c in comment.call_args_list]

    first = _sweep()
    assert len(first) == 1
    assert prose_blockers.EPIC_TAG in first[0]
    prior["count"] = 1
    assert _sweep() == []


def test_an_epic_prose_defect_never_moves_the_epics_lane():
    """`advance_unblocked_epics` moves an epic to Triage to TRIGGER THE PLANNER,
    and a planning run cannot fix a sentence. Filing a separate Triage card is
    banned outright, so the epic stays exactly where it is."""
    with patch.object(reconcile, "_fetch_epic_relations",
                      return_value=_epic("**Blocked by:** DRE-700")), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=None), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_state") as state:
        reconcile.epic_blockers_unmet("DRE-800")
    advance.assert_not_called()
    state.assert_not_called()


def test_a_fresh_epic_prose_defect_does_not_turn_the_run_red():
    """The sweep that FINDS the defect reports it and stays green: a comment
    posted seconds ago has not been ignored yet."""
    fresh = _minutes_ago(5)
    with patch.object(reconcile, "_fetch_epic_relations",
                      return_value=_epic("**Blocked by:** DRE-700")), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=1), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=fresh), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.epic_blockers_unmet("DRE-800")
    assert reconcile._stale_defects == []


def test_an_epic_prose_defect_older_than_two_hours_turns_the_run_red():
    """The escalation the epic gets INSTEAD of a lane move: past the window the
    run exits 1 through the existing ledger, so medic picks it up."""
    stale = _minutes_ago(reconcile.PROSE_DEFECT_RED_MINUTES + 5)
    with patch.object(reconcile, "_fetch_epic_relations",
                      return_value=_epic("**Blocked by:** DRE-700")), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=1), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=stale), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.epic_blockers_unmet("DRE-800")
    assert any("DRE-800" in entry for entry in reconcile._stale_defects)


def test_a_stale_defect_exits_the_sweep_red():
    """The ledger has to be WIRED, not merely filled: a list nothing reads is
    the silent-accretion failure this pipeline is named after."""
    reconcile._stale_defects.append("DRE-800: prose blocker with no relation")
    with patch.object(reconcile, "active_cards", return_value=[]), \
        patch.object(reconcile, "promote_ready", return_value=0), \
        patch.object(reconcile, "repo_epics", return_value=set()):
        with pytest.raises(SystemExit) as exit_info:
            reconcile.main(promote_only=True)
    assert "DRE-800" in str(exit_info.value) or "defect" in str(exit_info.value)


def test_a_reporting_failure_on_the_epic_defect_never_kills_the_sweep():
    """Reporting must never block the sweep — the epic still reads as held."""
    def _boom(*args, **kwargs):
        raise linear_ops.LinearError("linear error: rate limited")

    with patch.object(reconcile, "_fetch_epic_relations",
                      return_value=_epic("**Blocked by:** DRE-700")), \
        patch.object(reconcile.linear_ops, "count_comments", side_effect=_boom), \
        patch.object(reconcile.linear_ops, "first_comment_at", side_effect=_boom), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


# ---------------------------------------------------------------------------
# The notices, read on their own
# ---------------------------------------------------------------------------
def test_each_refusal_opens_with_its_own_tag():
    """`_surface_once` reads the tag off the notice; pair a notice with the
    wrong tag and two refusals silence each other."""
    card = prose_blockers.card_refusal("DRE-900", {"DRE-700"})
    epic = prose_blockers.epic_refusal("DRE-800", {"DRE-700"})
    assert prose_blockers.refusal_tag(card) == prose_blockers.CARD_TAG
    assert prose_blockers.refusal_tag(epic) == prose_blockers.EPIC_TAG
    assert prose_blockers.refusal_tag("something else entirely") is None


def test_the_two_tags_never_contain_one_another():
    """Both are counted with `tag in body`, so one nesting inside the other
    would make each invisible to whichever reader found the other first."""
    assert prose_blockers.CARD_TAG not in prose_blockers.EPIC_TAG
    assert prose_blockers.EPIC_TAG not in prose_blockers.CARD_TAG
    assert prose_blockers.CARD_TAG not in prose_blockers.epic_refusal("DRE-800", {"DRE-7"})
    assert prose_blockers.EPIC_TAG not in prose_blockers.card_refusal("DRE-900", {"DRE-7"})


def test_a_refusal_names_every_claim_it_refuses():
    body = prose_blockers.card_refusal("DRE-900", {"DRE-700", "DRE-701"})
    assert "DRE-700" in body and "DRE-701" in body


def test_the_epic_notice_states_that_the_epic_is_not_being_moved():
    body = prose_blockers.epic_refusal("DRE-800", {"DRE-700"})
    assert "Triage" not in body or "not" in body.lower()
    assert "blockedBy" in body


# ---------------------------------------------------------------------------
# The documents that stop the defect being re-authored
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_card_standard_points_at_the_relation_not_the_sentence():
    text = _read(ROOT / "standards" / "card-quality.md")
    assert "`blockedBy` relation" in text
    assert "Triage" in text
    # The claim that must be gone: that the body line is what the gate reads.
    assert "a body line the console parses into the\n  dependency gate" not in text


def test_the_planner_brief_points_at_the_relation_not_the_sentence():
    text = _read(ROOT / "briefs" / "planner.md")
    assert "blockedBy" in text
    assert "Triage" in text


def test_both_documents_name_the_defect_by_the_name_the_sweep_prints():
    """A reader who meets the refusal on a card can find the rule that produced
    it. Two vocabularies for one rule is the drift this card exists to end."""
    for path in (ROOT / "standards" / "card-quality.md", ROOT / "briefs" / "planner.md"):
        assert prose_blockers.CARD_TAG in _read(path), path
