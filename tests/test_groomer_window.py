"""Urgent, High, then the oldest Intake cards first — and nothing is hidden
for its age (DRE-4725).

The groomer used to batch Urgent, High, then the last 14 days newest first, and
leave every older unprioritised card out of the batch for ever. Measured on
2026-09-23 that was 74 of the 117 Intake cards older than 14 days, out of 254
in the lane, the oldest 83 days old. The CEO's decision on DRE-4669
(2026-09-23) reverses both halves: "work through the old Intake pile, 20 cards
at a time, oldest first, until the pile is gone."

The order, top to bottom, and every rule below is one test here:

  1. **Urgent opens the batch**, every repo, oldest first.
  2. **High next**, oldest first.
  3. **Then everything else, OLDEST creation day first.** No window: a card
     is never left out for being old.
  4. **Repo order is a tie-break inside a day** — Portico first only among
     cards of equal priority created the same day. Never the master key.
  5. **A unit's age is its OLDEST card**, so an old epic is not sent to the
     back by one new child.
  6. **Collisions and blockers are ordering constraints**: the older card of
     a colliding pair, and a blocker, go first — and an Urgent or High card
     that waits on an older unprioritised one pulls it forward with it.
  7. **The read decides neither membership nor order.** The model's `now`
     order does not reorder the batch and `not-now` does not remove a card;
     only a card the read declined (`unranked`) comes out, and the next card
     in order takes its slot.
  8. **The date is the creation date**, never the last update.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_window.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

from test_groomer import judged, ranked  # noqa: E402
from test_groomer_population import CYCLES, card, days_ago  # noqa: E402

# A fixed clock, so an assertion about a card's age is about the age and not
# about the day the suite happens to run.
NOW = "2026-09-04T12:00:00.000Z"

URGENT, HIGH = 1, 2

OLD_SENTENCE = "not batched — raise a card's priority"


def _ago(days: float) -> str:
    return days_ago(days, anchor=NOW)


def _order(proposal: dict, outcome: str = "now") -> list[str]:
    rows = proposal["outcomes"][outcome]
    return [r["identifier"] for r in sorted(rows, key=lambda r: r["position"])]


def _sequence(proposal: dict) -> list[str]:
    return [r["identifier"] for r in proposal["sequence"]
            if r["position"] is not None]


def _batch(proposal: dict) -> set[str]:
    return {r["identifier"] for r in proposal["outcomes"]["now"]}


def _blocked_by(blocked: dict, blocker: str) -> dict:
    blocked["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": blocker,
                                     "state": {"name": "Intake"}}}]}
    return blocked


# --------------------------------------------------------------------------
# the population the CEO described, top to bottom
# --------------------------------------------------------------------------
def _mixed_population():
    """One Urgent from 60 days ago, one High from 20, three unprioritised
    cards from the last fortnight (two Portico, one agent-bureau), and five
    unprioritised cards 30 to 34 days old."""
    return [
        card("DRE-100", repo="agent-bureau", created=_ago(60), priority=URGENT),
        card("DRE-101", repo="agent-bureau", created=_ago(20), priority=HIGH),
        card("DRE-102", repo="portico", created=_ago(9)),
        card("DRE-103", repo="portico", created=_ago(5)),
        card("DRE-104", repo="agent-bureau", created=_ago(2)),
    ] + [card(f"DRE-2{n:02d}", repo="portico", created=_ago(30 + n))
         for n in range(5)]


def test_the_batch_is_urgent_then_high_then_oldest_first():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    assert _order(proposal) == [
        "DRE-100",      # Urgent
        "DRE-101",      # High
        "DRE-204",      # then everything else, OLDEST first, whatever the repo
        "DRE-203",
        "DRE-202",
        "DRE-201",
        "DRE-200",
        "DRE-102",
        "DRE-103",
        "DRE-104",
    ], "the batch is not Urgent → High → oldest first"


def test_an_83_day_old_card_with_no_priority_precedes_a_3_day_old_one():
    cards = [card("DRE-1", created=_ago(3)), card("DRE-2", created=_ago(83))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW,
                               judgement=None)
    assert _order(proposal) == ["DRE-2", "DRE-1"], (
        "the oldest card in the pile goes first — it is what the batch is for"
    )


def test_urgent_beats_high_and_both_beat_the_oldest_card():
    cards = [card("DRE-1", created=_ago(0.5)),
             card("DRE-2", created=_ago(50), priority=HIGH),
             card("DRE-3", created=_ago(50), priority=URGENT),
             card("DRE-4", created=_ago(80))]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-3", "DRE-2", "DRE-4", "DRE-1"]


def test_urgent_cards_are_oldest_first_among_themselves():
    cards = [card("DRE-1", created=_ago(30), priority=URGENT),
             card("DRE-2", created=_ago(3), priority=URGENT),
             card("DRE-3", created=_ago(12), priority=URGENT)]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-1", "DRE-3", "DRE-2"]


def test_high_cards_are_oldest_first_among_themselves():
    cards = [card("DRE-1", created=_ago(4), priority=HIGH),
             card("DRE-2", created=_ago(40), priority=HIGH),
             card("DRE-3", created=_ago(1))]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-2", "DRE-1", "DRE-3"]


def test_a_medium_priority_card_gets_no_lane_of_its_own():
    """Only Urgent and High are lanes. Medium (3) and Low (4) are ordinary
    cards: ordered by age with the unprioritised ones, and batched."""
    cards = [card("DRE-1", created=_ago(40), priority=3),
             card("DRE-2", created=_ago(20), priority=4),
             card("DRE-3", created=_ago(1)),
             card("DRE-4", created=_ago(30))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-4", "DRE-2", "DRE-3"]
    assert proposal["older_than_window"]["cards"] == 0


# --------------------------------------------------------------------------
# no hiding — the window excludes nothing
# --------------------------------------------------------------------------
def test_a_card_older_than_fourteen_days_with_no_priority_is_in_the_batch():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    assert {f"DRE-2{n:02d}" for n in range(5)} <= _batch(proposal)
    assert proposal["outcomes"]["not-now"] == [], (
        "ten cards and a capacity of twenty: nothing is left out, whatever "
        "its age"
    )


def test_an_old_card_nothing_needs_is_in_the_batch_by_age():
    """What used to be the counterweight to the pull-forward rules — old and
    uninvolved stays out — is now the ordinary case the batch exists for."""
    cards = [card("DRE-1", repo="portico", created=_ago(45),
                  description="rewrites `Alone.tsx`"),
             card("DRE-2", repo="portico", created=_ago(2),
                  description="rewrites `Other.tsx`")]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-2"]
    assert proposal["older_than_window"]["cards"] == 0
    assert proposal["collisions"]["pairs"] == [], "the fixture collided"


def _populations():
    yield _mixed_population()
    yield [card("DRE-1", created=_ago(83)), card("DRE-2", created=_ago(3))]
    yield [card(f"DRE-{n}", created=_ago(n * 7)) for n in range(1, 40)]
    yield [card("DRE-900", created=_ago(90), title="[EPIC] Forms"),
           card("DRE-901", parent="DRE-900", created=_ago(90)),
           card("DRE-902", parent="DRE-900", created=_ago(2))]


def test_older_than_window_is_zero_for_every_population():
    for cards in _populations():
        for window in (1, 14, 30):
            proposal = groomer.propose(cards, cycles=CYCLES, now=NOW,
                                       window_days=window)
            assert proposal["older_than_window"]["cards"] == 0
            assert all(r["older_than_window"] is False
                       for r in proposal["outcomes"]["not-now"])


def test_the_page_no_longer_says_not_batched_raise_a_priority():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    assert OLD_SENTENCE not in groomer.render_proposal(proposal)
    assert not hasattr(groomer, "older_than_window_line"), (
        "the one-line receipt of hidden cards is gone with the hiding"
    )


def test_a_card_past_the_capacity_is_not_now_with_the_cycle_it_waits_for():
    """Outside the batch is `not-now` with the cycle it is projected into —
    for an 83-day-old card as for a 3-day-old one."""
    cards = [card(f"DRE-{n}", created=_ago(90 - n)) for n in range(1, 6)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-2"]
    later = {r["identifier"]: r for r in proposal["outcomes"]["not-now"]}
    assert set(later) == {"DRE-3", "DRE-4", "DRE-5"}
    for row in later.values():
        assert row["reconsidered_in"] is not None
        assert row["older_than_window"] is False
        assert row["trigger"] == f"when cycle {row['reconsidered_in']} opens"
        assert "window" not in row["reason"]


# --------------------------------------------------------------------------
# repo order is a tie-break inside a day, never the master key
# --------------------------------------------------------------------------
def test_portico_wins_the_tie_between_cards_created_the_same_day():
    """Two Portico cards created the same day rank ahead of an agent-bureau
    card created the same day — even when the agent-bureau card is the
    earlier of the three by the clock. Same priority, same day: repo decides."""
    cards = [                                        # all three on 2026-09-01
        card("DRE-1", repo="agent-bureau", created=_ago(3.0)),
        card("DRE-2", repo="portico", created=_ago(2.9)),
        card("DRE-3", repo="portico", created=_ago(2.6)),
    ]
    order = _order(groomer.propose(cards, cycles=CYCLES, now=NOW))
    assert order.index("DRE-1") == 2, (
        "REPO_PRIORITY breaks a tie inside a day, it does not order the days"
    )
    assert order[:2] == ["DRE-2", "DRE-3"], "the timestamp orders within repo"


def test_an_older_agent_bureau_card_outranks_a_newer_portico_one():
    """The other half of the same rule: a different DAY is not a tie, so the
    older card goes first whatever repo it is in."""
    cards = [card("DRE-1", repo="portico", created=_ago(2)),
             card("DRE-2", repo="agent-bureau", created=_ago(10))]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-2", "DRE-1"]


def test_the_population_query_reads_the_cards_priority():
    assert "priority" in groomer.POPULATION_QUERY, (
        "the card's Linear priority was never read — Urgent cannot open the "
        "batch if the groomer does not ask for it"
    )


# --------------------------------------------------------------------------
# the date is the creation date
# --------------------------------------------------------------------------
def test_a_stray_update_does_not_move_a_card():
    """A comment from an agent bumps `updatedAt` and nothing else: an old card
    touched this morning is still ordered by the day it was created."""
    touched = card("DRE-1", created=_ago(40))
    touched["updatedAt"] = NOW
    proposal = groomer.propose(
        [card("DRE-2", created=_ago(1)), touched,
         card("DRE-3", created=_ago(20))], cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-3", "DRE-2"]


# --------------------------------------------------------------------------
# the epic is still the unit, and its age is its OLDEST card
# --------------------------------------------------------------------------
def test_a_units_ordering_age_is_its_oldest_card():
    """One new child does not send an old epic to the back of the pile."""
    cards = [card("DRE-901", parent="DRE-900", created=_ago(60)),
             card("DRE-902", parent="DRE-900", created=_ago(1)),
             card("DRE-1", created=_ago(30))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-901", "DRE-902", "DRE-1"], (
        "the epic's unit is 60 days old by its oldest child, so it goes "
        "ahead of a 30-day-old card"
    )


def test_one_urgent_child_pulls_its_whole_epic_into_the_batch():
    """An epic's band is the highest priority among the epic and its
    children — the unit moves together or not at all."""
    cards = [card("DRE-900", created=_ago(90), title="[EPIC] Forms"),
             card("DRE-901", parent="DRE-900", created=_ago(90)),
             card("DRE-902", parent="DRE-900", created=_ago(88), priority=URGENT),
             card("DRE-1", created=_ago(100))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-900", "DRE-901", "DRE-902", "DRE-1"], (
        "one Urgent child pulls its epic's unit to the front of the batch"
    )
    assert proposal["older_than_window"]["cards"] == 0


# --------------------------------------------------------------------------
# collisions and blockers — ordering constraints, never a membership filter
# --------------------------------------------------------------------------
def test_an_old_card_colliding_on_a_file_is_pulled_into_the_batch():
    """Existing behaviour, re-asserted under the new key: the collision is
    ordered BEFORE the batched card it collides with, and the pair is reported
    with the file that caused it."""
    cards = [
        card("DRE-1", repo="portico", created=_ago(45),
             description="rewrites `Thread.tsx`"),
        card("DRE-2", repo="portico", created=_ago(2),
             description="also rewrites `rails/CommentsRail/Thread.tsx`"),
    ]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-2"], (
        "a 45-day-old card that collides with a batched card is pulled "
        "forward, not left behind to conflict with it later"
    )
    assert proposal["older_than_window"]["cards"] == 0
    pair = proposal["collisions"]["pairs"][0]
    assert (pair["before"], pair["after"]) == ("DRE-1", "DRE-2")
    assert pair["files"] == ["Thread.tsx"]
    assert "Thread.tsx" in groomer.render_proposal(proposal)


def test_an_old_blocker_of_a_batched_card_is_pulled_into_the_batch():
    blocked = card("DRE-2", repo="portico", created=_ago(2))
    blocked["inverseRelations"] = {"nodes": [
        {"type": "blocks", "issue": {"identifier": "DRE-1",
                                     "state": {"name": "Intake"}}}]}
    cards = [card("DRE-1", repo="portico", created=_ago(80)), blocked]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-1", "DRE-2"]


def test_a_high_card_colliding_with_an_older_card_pulls_it_ahead_of_itself():
    """Where oldest-first and the collision rule differ: a High card that
    names the same file as an older unprioritised card. The older card goes
    first, pulled forward past every other card in the pile, the High card
    follows it — both inside a batch of two — and the pair is reported with
    the file that caused it."""
    cards = [card("DRE-1", created=_ago(40),
                  description="rewrites `Thread.tsx`"),
             card("DRE-2", created=_ago(3), priority=HIGH,
                  description="also rewrites `Thread.tsx`")] + \
        [card(f"DRE-{n}", created=_ago(50 + n)) for n in range(10, 15)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW)
    assert _order(proposal) == ["DRE-1", "DRE-2"]
    pair = proposal["collisions"]["pairs"][0]
    assert (pair["before"], pair["after"]) == ("DRE-1", "DRE-2")
    assert "Thread.tsx" in pair["why"]
    assert "Thread.tsx" in groomer.render_proposal(proposal)


def test_an_urgent_card_blocked_by_an_older_card_pulls_its_blocker_into_the_batch():
    """The blocker is unprioritised and younger than the rest of the pile; it
    is pulled forward by the Urgent card it holds, not left behind the pile
    with the Urgent card waiting behind it."""
    urgent = _blocked_by(card("DRE-5", created=_ago(1), priority=URGENT),
                         "DRE-6")
    cards = [urgent, card("DRE-6", created=_ago(20))] + \
        [card(f"DRE-{n}", created=_ago(60 + n)) for n in range(10, 15)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW)
    assert _order(proposal) == ["DRE-6", "DRE-5"]


def test_a_blocker_of_a_collider_is_pulled_forward_too():
    """The pull is transitive: a card that blocks a card that collides with
    a High card goes ahead of both."""
    cards = [_blocked_by(card("DRE-1", created=_ago(30),
                              description="rewrites `Form.tsx`"), "DRE-3"),
             card("DRE-2", created=_ago(2), priority=HIGH,
                  description="also rewrites `Form.tsx`"),
             card("DRE-3", created=_ago(10))] + \
        [card(f"DRE-{n}", created=_ago(60 + n)) for n in range(10, 15)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, now=NOW)
    assert _order(proposal) == ["DRE-3", "DRE-1", "DRE-2"]


# --------------------------------------------------------------------------
# with a judgement: the read decides neither membership nor order
# --------------------------------------------------------------------------
def _judged(cards, answer, **kwargs):
    return groomer.propose(cards, cycles=CYCLES, now=NOW,
                           judgement=judged(cards, answer), **kwargs)


def test_the_models_now_order_does_not_reorder_the_batch():
    cards = [card("DRE-1", created=_ago(10)), card("DRE-2", created=_ago(5)),
             card("DRE-3", created=_ago(1))]
    proposal = _judged(cards, ranked(["DRE-3", "DRE-2", "DRE-1"]))
    assert _order(proposal) == ["DRE-1", "DRE-2", "DRE-3"], (
        "the rules' order is the order; the ranked read does not reorder it"
    )
    rules = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == _order(rules)
    groomer.assert_disjoint(proposal)


def test_a_not_now_verdict_does_not_take_a_card_out_of_the_batch():
    cards = [card("DRE-1", created=_ago(30)), card("DRE-2", created=_ago(2))]
    answer = "\n".join([
        ranked(["DRE-2"]),
        "DRE-1 | not-now | it can wait | when the console lands"])
    proposal = _judged(cards, answer)
    assert _order(proposal) == ["DRE-1", "DRE-2"]
    assert proposal["outcomes"]["not-now"] == []
    groomer.assert_disjoint(proposal)


def test_an_unranked_card_is_skipped_and_the_next_in_order_fills_its_slot():
    cards = [card("DRE-1", created=_ago(30)), card("DRE-2", created=_ago(20)),
             card("DRE-3", created=_ago(10)), card("DRE-4", created=_ago(1))]
    # DRE-2 is left out of the answer, which is the read declining it; the
    # model's `now` order is the reverse of the rules' and decides nothing.
    proposal = _judged(cards, ranked(["DRE-4", "DRE-3", "DRE-1"]), capacity=2)
    assert proposal["judgement"]["unranked"] == ["DRE-2"]
    assert _order(proposal) == ["DRE-1", "DRE-3"], (
        "the declined card comes out and the next card in order takes the "
        "slot — the batch is still two cards"
    )
    later = {r["identifier"]: r for r in proposal["outcomes"]["not-now"]}
    assert later["DRE-2"]["reconsidered_in"] is None
    assert later["DRE-2"]["trigger"] is None
    assert later["DRE-4"]["reconsidered_in"] is not None
    assert "Could not rank" in groomer.render_proposal(proposal)
    groomer.assert_disjoint(proposal)


def test_dre_3737s_fixture_batches_all_four_oldest_first():
    """DRE-3737's own fixture, with the outcome the CEO decided.

    DRE-3737 (epic DRE-3149) would have kept a card the model ranked
    `not-now` out of the batch even when it collides on a file with a card the
    model picked — "the collision rule orders cards WITHIN the set the model
    picked". Its premise was that the model's `now` set is what the batch is
    made of. The CEO's decision on DRE-4669 (2026-09-23) removed that premise:
    the batch is the oldest cards by the rules, and `now`/`not-now` decide
    neither membership nor order. So the expected outcome here is the
    OPPOSITE of DRE-3737's: C, the older card that names A's file, is in the
    batch ahead of A, and D, the oldest card of all, opens it.

    A is DRE-11 (3 days), B DRE-12 (5 days), both ranked `now`; C is DRE-13
    (40 days) and D DRE-14 (60 days), both ranked `not-now`.
    """
    cards = [card("DRE-11", created=_ago(3), description="edits `Thread.tsx`"),
             card("DRE-12", created=_ago(5)),
             card("DRE-13", created=_ago(40),
                  description="also edits `Thread.tsx`"),
             card("DRE-14", created=_ago(60))]
    answer = "\n".join([
        ranked(["DRE-11", "DRE-12"]),
        "DRE-C | not-now | it can wait | when the forms work lands",
        "DRE-D | not-now | it can wait | when the forms work lands"])
    proposal = _judged(cards, answer, capacity=20)
    order = _order(proposal)
    assert set(order) == {"DRE-11", "DRE-12", "DRE-13", "DRE-14"}
    assert order[0] == "DRE-14"
    assert order.index("DRE-13") < order.index("DRE-11")
    assert _batch(proposal) == {"DRE-11", "DRE-12", "DRE-13", "DRE-14"}
    pair = proposal["collisions"]["pairs"][0]
    assert (pair["before"], pair["after"]) == ("DRE-13", "DRE-11")
    groomer.assert_disjoint(proposal)


# --------------------------------------------------------------------------
# the window is accepted, and is a receipt of nothing
# --------------------------------------------------------------------------
def test_window_days_is_accepted_and_changes_nothing():
    cards = [card("DRE-1", created=_ago(20)), card("DRE-2", created=_ago(2)),
             card("DRE-3", created=_ago(70))]
    base = groomer.propose(cards, cycles=CYCLES, now=NOW)
    for window in (1, 30, 90):
        wide = groomer.propose(cards, cycles=CYCLES, now=NOW,
                               window_days=window)
        assert _order(wide) == _order(base) == ["DRE-3", "DRE-1", "DRE-2"]
        assert wide["id"] == base["id"]
        assert wide["window_days"] == window
        assert wide["older_than_window"]["cards"] == 0


class FakeOps:
    """The two reads `propose` makes, and nothing else — the CLI is what is
    under test, so the parsers stay real (same shape as
    tests/test_groomer_retry.py)."""

    def __init__(self, cards):
        self.cards = cards

    def gql_paged(self, query, variables=None, *, connection="issues"):
        return list(self.cards)

    def gql(self, query, variables=None):
        return {"cycles": {"nodes": [dict(c, completedAt=None) for c in CYCLES]}}

    def __getattr__(self, name):
        import linear_ops
        return getattr(linear_ops, name)


def _cli_proposal(*argv) -> dict:
    ops = FakeOps([card("DRE-1", created=days_ago(20)),
                   card("DRE-2", created=days_ago(2))])
    real = groomer.linear_ops
    groomer.linear_ops = ops
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "proposal.json")
        try:
            assert groomer.main(["propose", "--lane", "Intake", "--out", out,
                                 "--no-judgement", *argv]) == 0
        finally:
            groomer.linear_ops = real
        return json.loads(Path(out).read_text(encoding="utf-8"))


def test_the_cli_still_takes_window_days_and_it_hides_nothing():
    for argv in ((), ("--window-days", "14"), ("--window-days", "30")):
        proposal = _cli_proposal(*argv)
        assert [r["identifier"] for r in proposal["outcomes"]["now"]] == \
            ["DRE-1", "DRE-2"]
        assert proposal["older_than_window"]["cards"] == 0


# --------------------------------------------------------------------------
# the rules, where a human and the model read them
# --------------------------------------------------------------------------
def _doc_section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_the_doc_states_urgent_high_then_oldest_first_with_no_window():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    order = _doc_section(doc, "## The order, applied top to bottom")
    assert order.index("Urgent") < order.index("High") < \
        order.index("oldest first"), "the order is documented as applied"
    for stale in ("newest first", "Then the window", "not batched"):
        assert stale not in order, f"the order section still says {stale!r}"
    assert "no window" in order
    assert "pulls the older card forward" in order
    assert "never filters on the model's picks" in order
    outcomes = _doc_section(doc, "## The outcomes, and what each one owes")
    assert "older than the window" not in outcomes


def test_the_brief_no_longer_says_the_now_lines_fill_the_batch():
    brief = (ROOT / "briefs" / "groomer.md").read_text(encoding="utf-8")
    assert "is the order the batch is filled in" not in brief
    assert "hands the ordering back to the rules" not in brief
    assert "oldest first" in brief
    # The four outcomes and the line format are unchanged.
    for outcome in ("`now`", "`not-now`", "`likely-done`", "`unranked`"):
        assert outcome in brief
    assert "<card id> | <outcome> | <reason> | <trigger or evidence>" in brief
