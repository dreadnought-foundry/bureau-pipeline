"""A card the read could not rank is never in the proposed batch (DRE-3544).

Judged proposal `f673bfefa340` (2026-09-08, 260 cards, 18 proposed) carried
DRE-3020 at position 32 with its printed reason the literal string "could not
rank — needs a person", and the same comment listed it under "Could not rank —
needs a person". A drain of that batch would have moved a card the model had
explicitly declined to place. Batch 2 did not repeat it — by one sequence
position, not by a guard (`docs/groomer-judged-batch.md`).

So the two lists are made disjoint where the batch is built, and the write seam
refuses a proposal where they are not:

  * the rules rank a card, the read declines it, and the batch comes out
    without it — the card is `not-now`, and the "Could not rank" section names
    it;
  * a declined card names no trigger and no cycle: what is owed is a person,
    not a deferral somebody scheduled;
  * the batch table a DRAIN parses never carries it;
  * `proposed ∩ unranked = ∅` is asserted at write time, so a future change
    that lets one through crashes the run rather than proposing it.

Run: cd bureau-pipeline && python3 -m pytest \
    tests/test_groomer_unranked_never_batched.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_context  # noqa: E402
import groom_judgement  # noqa: E402
import groomer  # noqa: E402

NOW = "2026-09-05T12:00:00Z"
BASE = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
PACK = groom_context.pack(now=NOW)
CYCLES = [
    {"number": 12, "id": "cyc-12", "startsAt": "2026-09-07T07:00:00.000Z",
     "endsAt": "2026-09-21T07:00:00.000Z"},
    {"number": 13, "id": "cyc-13", "startsAt": "2026-09-21T07:00:00.000Z",
     "endsAt": "2026-10-05T07:00:00.000Z"},
]
# The sentence the model's declined cards carry, and the one printed in the
# batch table of `f673bfefa340`. Named here so the fixture is the incident.
DECLINED = "could not rank — needs a person"


def ago(days: float) -> str:
    return (BASE - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def card(identifier, *, repo="portico", parent=None, days=1, description="",
         title=None, priority=0):
    return {
        "identifier": identifier,
        "title": title or f"{identifier} does a thing",
        "description": description,
        "createdAt": ago(days),
        "priority": priority,
        "state": {"name": "Intake"},
        "labels": {"nodes": [{"name": f"repo:{repo}"}, {"name": "agent:engineer"}]},
        "parent": {"identifier": parent, "title": f"[EPIC] {parent}"} if parent else None,
        "project": None,
        "cycle": None,
        "inverseRelations": {"nodes": []},
    }


class Canned:
    """The call seam, answering one canned answer and reaching no model."""

    def __init__(self, answer=""):
        self.answer, self.calls = answer, 0

    def __call__(self, model, prompt, *, max_tokens=None, timeout_seconds=None):
        self.calls += 1
        import planning_classify
        return planning_classify.Answer(text=self.answer, model="test-model",
                                        truncated=False)


def judged(cards, answer):
    rows = groom_judgement.census(cards, now=NOW)
    return groom_judgement.run(rows, PACK, call=Canned(answer),
                               model="test-model")


def ranked(order, outcome="now", reason="the model wanted it"):
    return "\n".join(" | ".join([cid, outcome, reason]) for cid in order)


def batch_ids(proposal) -> list[str]:
    return [row["identifier"] for row in
            sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]


def later_rows(proposal) -> dict:
    return {row["identifier"]: row for row in proposal["outcomes"]["not-now"]}


def population():
    """The incident in three cards: the rules would batch all of them, oldest
    first (DRE-4725) — DRE-1, DRE-3020, DRE-2."""
    return [card("DRE-1", days=3), card("DRE-3020", days=2), card("DRE-2")]


# --------------------------------------------------------------------------
# the rules rank it, the read declines it, and the batch comes out without it
# --------------------------------------------------------------------------
def test_the_rules_would_have_batched_the_card_the_read_declines():
    """The fixture is the incident, not a card the window was going to drop.

    Without this the test below could pass because the rules never wanted the
    card — which proves nothing about the guard.
    """
    cards = population()
    rules_only = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW)
    assert "DRE-3020" in batch_ids(rules_only), (
        "the fixture must be a card the rules put IN the batch, the way "
        "DRE-3020 was at position 32 of f673bfefa340"
    )


def test_a_card_the_read_declined_is_not_in_the_proposed_batch():
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    assert proposal["judgement"]["unranked"] == ["DRE-3020"]
    assert "DRE-3020" not in batch_ids(proposal), (
        "a drain moves the batch; a card the read explicitly declined to "
        "place must not be in it"
    )
    assert batch_ids(proposal) == ["DRE-1", "DRE-2"]
    assert proposal["batch"]["cards"] == 2, (
        "the batch count is what is in the batch, not what was in it before "
        "the declined card came out"
    )
    assert "DRE-3020" in later_rows(proposal), (
        "the card is still reported — it is not in the batch and it has not "
        "vanished from the population"
    )


def test_the_two_lists_are_disjoint_on_every_row_of_the_proposal():
    cards = [card(f"DRE-{n:02d}", days=n % 4 + 1) for n in range(12)]
    declined = ["DRE-03", "DRE-07", "DRE-11"]
    answer = ranked([c["identifier"] for c in cards
                     if c["identifier"] not in declined])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, answer))
    unranked = set(proposal["judgement"]["unranked"])
    assert unranked == set(declined)
    assert unranked & set(batch_ids(proposal)) == set()
    # and the same fact in the row-level outcome every other reader uses
    for row in proposal["sequence"]:
        if row["identifier"] in unranked:
            assert row["outcome"] != "now", f"{row['identifier']} is in the batch"
            assert row["cycle"] is None, (
                f"{row['identifier']} was declined and still carries a cycle"
            )


def test_a_declined_child_comes_out_while_its_epic_stays():
    """An epic is one unit for ORDER. It is not a way into the batch for a
    card the read refused to place."""
    cards = [card("DRE-1", days=1, parent="DRE-900"),
             card("DRE-3020", days=2, parent="DRE-900"),
             card("DRE-2", days=3)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    assert "DRE-3020" not in batch_ids(proposal)
    assert "DRE-1" in batch_ids(proposal)


# --------------------------------------------------------------------------
# what is owed is a person, not a cycle
# --------------------------------------------------------------------------
def test_a_declined_card_names_no_trigger_and_no_cycle():
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    row = later_rows(proposal)["DRE-3020"]
    assert row["reason"] == DECLINED
    assert row["judged"] is False
    assert row["trigger"] is None, (
        "a trigger would file it as a deferral somebody scheduled; what "
        "brings it back is a person"
    )
    assert row["reconsidered_in"] is None
    assert row["older_than_window"] is False, (
        "it is inside the window — the read declined it, the clock did not"
    )
    assert proposal["deprioritised"] == [], (
        "a declined card is not a repo waiting for a cycle"
    )


def test_the_declined_card_is_not_listed_under_not_now_and_when_to_come_back():
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    text = groomer.render_proposal(proposal)
    section = text.split("## Not now — and when to come back")
    if len(section) > 1:
        body = section[1].split("\n## ")[0]
        assert "DRE-3020" not in body, (
            "a refusal that renders as a deferral is a refusal nobody reads"
        )
    assert "## Could not rank — needs a person" in text
    needs_person = text.split("## Could not rank — needs a person")[1]
    assert "DRE-3020" in needs_person.split("\n## ")[0]


# --------------------------------------------------------------------------
# the batch a DRAIN reads, and the assertion at write time
# --------------------------------------------------------------------------
def test_the_batch_table_the_drain_parses_never_carries_it():
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    record = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    moved = [row["identifier"] for row in record["batch"]]
    assert moved == ["DRE-1", "DRE-2"], (
        "the drain moves what the batch table says; it never says DRE-3020"
    )
    table = groomer.proposal_comment(proposal).split(
        "## The batch, in order")[1].split("\n## ")[0]
    assert "DRE-3020" not in table
    assert DECLINED not in table, (
        "no batch row may print the sentence that says nobody could place it"
    )


def test_a_proposal_that_proposes_an_unranked_card_is_refused_at_write_time():
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, ranked(["DRE-1", "DRE-2"])))
    groomer.assert_disjoint(proposal)  # the real one passes

    # the mutation: the declined card put back into the batch by hand
    declined = later_rows(proposal)["DRE-3020"]
    proposal["outcomes"]["now"].append({**declined, "position": 99, "cycle": 12,
                                        "cycle_id": "cyc-12", "unit": "DRE-3020",
                                        "epic": None, "band": 2,
                                        "projected": False})
    with pytest.raises(groomer.ProposalContradiction) as caught:
        groomer.assert_disjoint(proposal)
    assert "DRE-3020" in str(caught.value)
    with pytest.raises(groomer.ProposalContradiction):
        groomer.proposal_comment(proposal)


# --------------------------------------------------------------------------
# a read that placed nothing declined nothing
# --------------------------------------------------------------------------
def test_a_read_that_answered_nothing_still_proposes_the_rules_batch():
    """`problem` is set on exactly the paths where NO card was ranked, and
    every card is `unranked` by default there. The read refused nothing, so
    the proposal is the rules' — emptying the batch would take the groomer
    down with the model."""
    cards = population()
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                               judgement=judged(cards, "I'd rather not."))
    assert proposal["judgement"]["problem"]
    assert proposal["judgement"]["unranked"] == ["DRE-1", "DRE-2", "DRE-3020"]
    assert batch_ids(proposal) == ["DRE-1", "DRE-3020", "DRE-2"], (
        "a read that placed nothing falls back to the rules, exactly as it "
        "did before the read existed"
    )
    groomer.assert_disjoint(proposal)
    text = groomer.proposal_comment(proposal)
    assert proposal["judgement"]["problem"] in text
    assert "## Could not rank — needs a person" not in text, (
        "the whole population under a heading that says nobody could place "
        "it, listing the very cards in the batch above, is the contradiction "
        "this card is about — the problem line says what happened instead"
    )


def test_a_failed_read_still_names_a_trigger_on_every_deferred_card():
    cards = [card(f"DRE-{n}", days=n) for n in range(1, 6)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=2, now=NOW,
                               judgement=judged(cards, "I'd rather not."))
    later = later_rows(proposal)
    assert later, "capacity 2 over five cards defers three of them"
    for identifier, row in later.items():
        assert row["trigger"], (
            f"{identifier} was deferred by the RULES — the read declined "
            f"nothing, so it still names what brings it back"
        )


# --------------------------------------------------------------------------
# the rules-only path is untouched
# --------------------------------------------------------------------------
def test_no_judgement_batches_exactly_what_it_always_did():
    cards = population()
    rules_only = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW)
    assert batch_ids(rules_only) == ["DRE-1", "DRE-3020", "DRE-2"]
    assert rules_only["judgement"]["enabled"] is False
    groomer.assert_disjoint(rules_only)
