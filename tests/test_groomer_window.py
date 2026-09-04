"""Newest-first inside a 14-day window, Urgent always first (DRE-3096).

The groomer used to rank a unit by `(repo priority, created ascending,
identifier)`: Portico first, then the OLDEST card in the repo. On a 200-card
Intake that makes the first batch months-old Portico work, and a card raised
Urgent this morning waits its turn. The CEO's decision (2026-09-04): 14 days,
creation date, priority first.

The order, top to bottom, and every rule below is one test here:

  1. **Urgent opens the batch**, every repo, newest first — the
     production-issue lane.
  2. **High next**, newest first, or High means nothing.
  3. **Then the window**: created in the last `WINDOW_DAYS` days, newest first.
  4. **Repo order is a tie-break INSIDE a band** — Portico first only among
     cards of equal priority created the same day. Never the master key.
  5. **Older than the window is "not now"** — left in Intake, ungroomed, and
     reported as one line. Not aged out, not cancelled, not moved.
  6. **Two things still pull an old card forward**: a file collision with a
     batched card, and being a Linear blocker of one.
  7. **The date is the creation date**, never the last update — a stray agent
     comment must not bump a card, and the way to resurrect an old one is to
     raise its priority, which is a deliberate human act.

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

from test_groomer_population import CYCLES, card, days_ago  # noqa: E402

# A fixed clock, so an assertion about "inside the window" is about the
# window and not about the day the suite happens to run.
NOW = "2026-09-04T12:00:00.000Z"

URGENT, HIGH = 1, 2


def _ago(days: float) -> str:
    return days_ago(days, anchor=NOW)


def _order(proposal: dict, outcome: str = "now") -> list[str]:
    rows = proposal["outcomes"][outcome]
    return [r["identifier"] for r in sorted(rows, key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# the population the CEO described, top to bottom
# --------------------------------------------------------------------------
def _mixed_population():
    """One Urgent from 60 days ago, one High from 20, three inside the window
    on different days (two Portico, one agent-bureau), five older than 14."""
    return [
        card("DRE-100", repo="agent-bureau", created=_ago(60), priority=URGENT),
        card("DRE-101", repo="agent-bureau", created=_ago(20), priority=HIGH),
        card("DRE-102", repo="portico", created=_ago(9)),
        card("DRE-103", repo="portico", created=_ago(5)),
        card("DRE-104", repo="agent-bureau", created=_ago(2)),
    ] + [card(f"DRE-2{n:02d}", repo="portico", created=_ago(30 + n))
         for n in range(5)]


def test_the_batch_is_urgent_then_high_then_the_window_newest_first():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    assert _order(proposal) == [
        "DRE-100",      # Urgent, 60 days old, and still first
        "DRE-101",      # High, 20 days old — outside the window, still batched
        "DRE-104",      # then the window, newest first, whatever the repo
        "DRE-103",
        "DRE-102",
    ], "the batch is not Urgent → High → newest inside the window"


def test_the_cards_older_than_the_window_are_not_now_not_gone():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    old = {r["identifier"] for r in proposal["outcomes"]["not-now"]}
    assert old == {f"DRE-2{n:02d}" for n in range(5)}, (
        "an old card is 'not now' — it is not aged out, not cancelled and not "
        "moved; it stays in Intake ungroomed"
    )
    assert not (old & {r["identifier"] for r in proposal["outcomes"]["now"]})
    assert not (old & {r["identifier"] for r in proposal["outcomes"]["dead"]})
    # …and every card still carries exactly one outcome.
    seen = [r["identifier"] for name in groomer.OUTCOMES
            for r in proposal["outcomes"][name]]
    assert sorted(seen) == sorted(c["identifier"] for c in _mixed_population())


def test_the_old_cards_are_reported_as_one_line_with_the_way_back_in():
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    receipt = proposal["older_than_window"]
    assert receipt["cards"] == 5 and receipt["days"] == 14
    line = ("5 cards older than 14 days, not batched — raise a card's "
            "priority to High or Urgent to pull it in.")
    assert receipt["line"] == line
    assert line in groomer.render_proposal(proposal), (
        "the receipt is what the CEO reads: N cards held back, and the one "
        "deliberate act that pulls one in"
    )


def test_an_old_card_with_no_priority_is_left_out_of_the_cycle_count():
    """"Not now" here means ungroomed, not "reconsidered in cycle 14". The
    cards inside the window carry the cycle they wait for; the ones outside it
    carry no cycle at all, because nothing scheduled them."""
    proposal = groomer.propose(_mixed_population(), cycles=CYCLES, now=NOW)
    later = {r["identifier"]: r for r in proposal["outcomes"]["not-now"]}
    assert all(r["reconsidered_in"] is None for r in later.values())
    assert all(r["older_than_window"] for r in later.values())
    seq = {r["identifier"]: r for r in proposal["sequence"]}
    assert seq["DRE-200"]["cycle"] is None and seq["DRE-200"]["cycle_id"] is None


# --------------------------------------------------------------------------
# rule 4 — repo order is a tie-break, never the master key
# --------------------------------------------------------------------------
def test_portico_wins_the_tie_between_cards_created_the_same_day():
    """Two Portico cards created the same day rank ahead of an agent-bureau
    card created the same day — even when the agent-bureau card is the later
    of the three by the clock. Same priority, same day: repo decides."""
    cards = [                                        # all three on 2026-09-01
        card("DRE-1", repo="portico", created=_ago(3.0)),
        card("DRE-2", repo="portico", created=_ago(2.9)),
        card("DRE-3", repo="agent-bureau", created=_ago(2.6)),
    ]
    order = _order(groomer.propose(cards, cycles=CYCLES, now=NOW))
    assert order.index("DRE-3") == 2, (
        "REPO_PRIORITY is the last element of the key: it breaks a tie inside "
        "a band, it does not order the bands"
    )
    assert set(order[:2]) == {"DRE-1", "DRE-2"}


def test_a_newer_agent_bureau_card_outranks_an_older_portico_one():
    """The other half of the same rule: a different DAY is not a tie, so the
    newer card goes first whatever repo it is in."""
    cards = [card("DRE-1", repo="portico", created=_ago(10)),
             card("DRE-2", repo="agent-bureau", created=_ago(2))]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-2", "DRE-1"]


# --------------------------------------------------------------------------
# rule 1/2 — priority is read, and it is read from Linear
# --------------------------------------------------------------------------
def test_the_population_query_reads_the_cards_priority():
    assert "priority" in groomer.POPULATION_QUERY, (
        "the card's Linear priority was never read — Urgent cannot open the "
        "batch if the groomer does not ask for it"
    )


def test_urgent_beats_high_and_both_beat_the_freshest_card():
    cards = [card("DRE-1", created=_ago(0.5)),
             card("DRE-2", created=_ago(50), priority=HIGH),
             card("DRE-3", created=_ago(50), priority=URGENT)]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-3", "DRE-2", "DRE-1"]


def test_urgent_cards_are_newest_first_among_themselves():
    cards = [card("DRE-1", created=_ago(30), priority=URGENT),
             card("DRE-2", created=_ago(3), priority=URGENT),
             card("DRE-3", created=_ago(12), priority=URGENT)]
    assert _order(groomer.propose(cards, cycles=CYCLES, now=NOW)) == \
        ["DRE-2", "DRE-3", "DRE-1"]


def test_a_medium_priority_card_gets_no_lane_of_its_own():
    """Only Urgent and High are lanes. Medium (3) and Low (4) are ordinary
    cards, and an old Medium card is as "not now" as an unprioritised one."""
    cards = [card("DRE-1", created=_ago(40), priority=3),
             card("DRE-2", created=_ago(40), priority=4),
             card("DRE-3", created=_ago(1))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-3"]
    assert proposal["older_than_window"]["cards"] == 2


# --------------------------------------------------------------------------
# rule 7 — the date is the creation date
# --------------------------------------------------------------------------
def test_a_stray_update_does_not_pull_an_old_card_in():
    """A comment from an agent bumps `updatedAt` and nothing else. The way to
    resurrect an old card is to raise its priority — a deliberate human act."""
    touched = card("DRE-1", created=_ago(40))
    touched["updatedAt"] = NOW
    proposal = groomer.propose([touched, card("DRE-2", created=_ago(1))],
                               cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-2"]
    assert "DRE-1" in {r["identifier"] for r in proposal["outcomes"]["not-now"]}


# --------------------------------------------------------------------------
# rule 6 — what still pulls an old card forward
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


def test_an_old_card_nothing_needs_stays_out():
    """The counterweight to the two rules above: being old and uninvolved is
    the ordinary case, and it is the one that must not be pulled in."""
    cards = [card("DRE-1", repo="portico", created=_ago(45),
                  description="rewrites `Alone.tsx`"),
             card("DRE-2", repo="portico", created=_ago(2),
                  description="rewrites `Other.tsx`")]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal) == ["DRE-2"]
    assert proposal["older_than_window"]["cards"] == 1


# --------------------------------------------------------------------------
# the epic is still the unit
# --------------------------------------------------------------------------
def test_one_urgent_child_pulls_its_whole_epic_into_the_batch():
    """An epic's band is the highest priority and the newest creation among the
    epic and its children — the unit moves together or not at all."""
    cards = [card("DRE-900", created=_ago(90), title="[EPIC] Forms"),
             card("DRE-901", parent="DRE-900", created=_ago(90)),
             card("DRE-902", parent="DRE-900", created=_ago(88), priority=URGENT),
             card("DRE-1", created=_ago(1))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert _order(proposal)[:3] == ["DRE-900", "DRE-901", "DRE-902"], (
        "one Urgent child pulls its epic's unit to the front of the batch"
    )
    assert proposal["older_than_window"]["cards"] == 0


def test_an_epic_is_in_the_window_if_any_of_it_is():
    cards = [card("DRE-900", parent=None, created=_ago(90), title="[EPIC] Forms"),
             card("DRE-901", parent="DRE-900", created=_ago(90)),
             card("DRE-902", parent="DRE-900", created=_ago(3))]
    proposal = groomer.propose(cards, cycles=CYCLES, now=NOW)
    assert set(_order(proposal)) == {"DRE-900", "DRE-901", "DRE-902"}
    assert proposal["outcomes"]["not-now"] == []


# --------------------------------------------------------------------------
# the window is 14 days, and it widens without a code change
# --------------------------------------------------------------------------
def test_the_default_window_is_fourteen_days():
    assert groomer.WINDOW_DAYS == 14


def _twenty_day_population():
    """Dated off the REAL clock, because the CLI path below has no `now` to
    pass — the flag is what is under test, not the fixed clock."""
    return [card("DRE-1", created=days_ago(20)), card("DRE-2", created=days_ago(2))]


def test_the_default_window_leaves_a_twenty_day_old_card_out():
    proposal = groomer.propose(_twenty_day_population(), cycles=CYCLES)
    assert _order(proposal) == ["DRE-2"]
    assert proposal["older_than_window"] == {
        "days": 14, "cards": 1,
        "line": ("1 card older than 14 days, not batched — raise a card's "
                 "priority to High or Urgent to pull it in."),
    }


def test_a_thirty_day_window_pulls_the_same_card_in():
    """The drain of the old Backlog runs at 14; the steady state widens to 30
    without a code change."""
    proposal = groomer.propose(_twenty_day_population(), cycles=CYCLES,
                               window_days=30)
    assert _order(proposal) == ["DRE-2", "DRE-1"]
    assert proposal["older_than_window"]["cards"] == 0
    assert proposal["window_days"] == 30


# --------------------------------------------------------------------------
# the flag, on the command the operator and the workflow actually run
# --------------------------------------------------------------------------
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
    ops = FakeOps(_twenty_day_population())
    real = groomer.linear_ops
    groomer.linear_ops = ops
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "proposal.json")
        try:
            assert groomer.main(["propose", "--lane", "Intake", "--out", out,
                                 *argv]) == 0
        finally:
            groomer.linear_ops = real
        return json.loads(Path(out).read_text(encoding="utf-8"))


def test_the_cli_defaults_to_the_fourteen_day_window():
    proposal = _cli_proposal()
    assert proposal["window_days"] == 14
    assert [r["identifier"] for r in proposal["outcomes"]["now"]] == ["DRE-2"]


def test_the_cli_takes_window_days():
    proposal = _cli_proposal("--window-days", "30")
    assert proposal["window_days"] == 30
    assert {r["identifier"] for r in proposal["outcomes"]["now"]} == \
        {"DRE-1", "DRE-2"}


# --------------------------------------------------------------------------
# the rules, where a human reads them
# --------------------------------------------------------------------------
def test_the_order_is_documented_in_the_order_it_is_applied():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    for phrase in ("Urgent", "High", "14", "--window-days", "creation date"):
        assert phrase in doc, f"docs/groomer.md does not state {phrase!r}"
    assert doc.index("Urgent") < doc.index("--window-days"), (
        "the rules are documented in the order they are applied"
    )
