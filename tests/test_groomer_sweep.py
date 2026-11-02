"""RED-first: the groomer sweeps all of Intake for dead cards each morning,
and the Cancel list holds no Planning slot (DRE-7973, epic DRE-6648).

Until this card the Cancel list was drawn from the same twenty as the
Planning list (DRE-4727): a card's own `Superseded by:` line or the ranked
read's `likely-done` put it on the Cancel list only when the walk placed it
in the batch cycles, every Cancel row held one of the twenty slots, and the
check before posting read the Planning list and ten spares. What this file
holds:

  * every source, from the whole lane — a `Superseded by:` row, a ranked-read
    row and a card only the check's sweep read all go on the Cancel list
    wherever they stand, each with its proof, on no other list, and each in
    `proposal["sweep"]["outside_batch"]`;
  * a Cancel row holds no slot: twenty-five Planning-ready cards and three
    Cancel rows make a Planning list of twenty and a Cancel list of three;
  * the refusals are asked of every row, inside the batch or not, and a
    refused row is said under `## Cancels refused`;
  * `## What the check read` says how many cards the sweep read, and a
    morning whose sweep found nothing renders its Cancel section exactly as
    before (`tests/fixtures/groom-sweep-quiet-cancel.md`, written by the
    groomer as it stood on `main` before this card);
  * the words — the doc and the brief no longer say the Cancel list is drawn
    from the morning's twenty.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_sweep.py -q
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groom_context  # noqa: E402
import groom_verify  # noqa: E402
import groomer  # noqa: E402

from test_groom_readiness import QUESTION, _facts  # noqa: E402
from test_groom_verify import (  # noqa: E402
    FLEET, OWNERS, SUPERSEDED, FakeGh, FakeLinear, comment, owner_token)
from test_groomer import CYCLES, NOW, card, judged, ranked  # noqa: E402
from test_groomer_cancel_proof import (  # noqa: E402
    _answer, _lane as _proof_lane, _linear as _proof_linear)
from test_groomer_two_lists import cancel_rows  # noqa: E402

QUIET_CANCEL = ROOT / "tests" / "fixtures" / "groom-sweep-quiet-cancel.md"

JUDGED = "DRE-308"   # the ranked read's likely-done, in the third cycle
LINE = "DRE-311"     # its own `Superseded by:` line, and a readiness rule
SWEPT = "DRE-318"    # on `rewrite`, and a comment declares it superseded
POINTER = "pull request 274 merged it"
TECHNICAL = "scripts/groomer.py:1610 returns `WITHHELD_REASON`"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    for owner in FLEET:
        monkeypatch.setenv(groom_context.token_env(owner), owner_token(owner))


def lane() -> list[dict]:
    """Twenty cards, DRE-301 the newest, so the rules' order is the
    identifier order. At a capacity of three the cycles are 12, 13, 14 …
    three cards each, and DRE-308 is the second card of the third."""
    cards = [card(f"DRE-{300 + n}", days=n) for n in range(1, 21)]
    by_id = {c["identifier"]: c for c in cards}
    by_id[LINE]["description"] = ("Superseded by: DRE-4700\n\n" + QUESTION)
    return cards


def answer(cards, likely=None) -> str:
    likely = likely or {JUDGED: POINTER}
    return "\n".join(
        f"{c['identifier']} | likely-done | the work already happened | "
        f"{likely[c['identifier']]}" if c["identifier"] in likely
        else ranked([c["identifier"]]) for c in cards)


def shaping(cards, *, capacity=3, likely=None, readiness=True) -> dict:
    return dict(cycles=CYCLES, capacity=capacity, now=NOW,
                judgement=judged(cards, answer(cards, likely)),
                **({"readiness": _facts()} if readiness else {}))


def ids(rows) -> list[str]:
    return [r["identifier"] for r in sorted(rows or [],
                                            key=lambda r: r.get("position") or 0)]


def lists_holding(proposal: dict, identifier: str) -> list[str]:
    return sorted(name for name, rows in proposal["outcomes"].items()
                  if identifier in {r["identifier"] for r in rows})


def wide() -> dict:
    """The three sources at once, the way `verify_proposal` builds a morning
    — proposed, checked with the sweep on, proposed again — with DRE-318
    standing on the Rewrite first list when the check reads it."""
    cards = lane()
    shape = shaping(cards)
    first = groomer.propose(cards, **shape)
    outcomes = first["outcomes"]
    row = next(r for r in outcomes["not-now"] if r["identifier"] == SWEPT)
    outcomes["not-now"].remove(row)
    outcomes["rewrite"] = [{**row, "position": 1}]
    linear = FakeLinear({SWEPT: {"comments": [comment(SUPERSEDED)]}},
                        lanes={"DRE-4700": "Done"})
    result = groom_verify.check(first, lops=linear, run=FakeGh(),
                                owners=OWNERS, sweep=True)
    assert SWEPT in result["swept"] and SWEPT in result["cancel"]
    final = groomer.propose(cards, verified=result, **shape)
    groom_verify.settle(final, result)
    return final


# --------------------------------------------------------------------------
# every source, from the whole lane
# --------------------------------------------------------------------------
def test_every_source_puts_its_card_on_the_cancel_list_wherever_it_stands():
    final = wide()
    dead = {r["identifier"]: r for r in final["outcomes"]["dead"]}
    assert set(dead) == {JUDGED, LINE, SWEPT}
    assert dead[JUDGED]["source"] == groomer.DEAD_FROM_JUDGEMENT
    assert dead[LINE]["source"] == groomer.DEAD_FROM_LINE
    assert dead[SWEPT]["source"] == groomer.DEAD_FROM_CHECK
    # Each with its proof item, as today.
    assert dead[JUDGED]["proof"] == [{"source": groomer.PROOF_FROM_JUDGEMENT,
                                      "quote": POINTER}]
    assert dead[LINE]["proof"] == [{"source": groomer.PROOF_FROM_LINE,
                                    "quote": "Superseded by: DRE-4700"}]
    assert [p["source"] for p in dead[SWEPT]["proof"]] == [
        groomer.PROOF_FROM_CHECK]
    assert "Do not build from this card." in dead[SWEPT]["proof"][0]["quote"]
    # Positions in the rules' order, from 1.
    assert ids(final["outcomes"]["dead"]) == [JUDGED, LINE, SWEPT]
    for identifier in (JUDGED, LINE, SWEPT):
        assert lists_holding(final, identifier) == ["dead"], identifier
    groomer.assert_disjoint(final)


def test_every_cancel_outside_the_batch_is_in_the_sweep_record():
    final = wide()
    sweep = final["sweep"]
    assert sweep["outside_batch"] == [JUDGED, LINE, SWEPT]
    assert sweep["read"] == len(final["verification"]["swept"]) > 0
    assert sweep["to_cancel"] == 1
    assert sweep["unread"] == 0
    assert final["verification"]["swept_to_cancel"] == [SWEPT]


def test_the_judged_card_sits_in_the_third_cycle_of_the_old_walk():
    """The fixture is what the card says: under DRE-4727's walk, every
    Cancel row holding a slot, DRE-308 would have been planned into the
    third cycle — 14 — and left `not-now` rather than cancelled."""
    final = wide()
    slotted = groomer.cycle_plan(final["sequence"], CYCLES, 3)
    assert {r["identifier"]: r["cycle"] for r in slotted}[JUDGED] == 14
    assert final["batch"]["cycles"] == [12]


def test_a_superseded_line_card_a_readiness_rule_trips_is_cancelled_not_unready():
    final = wide()
    assert LINE not in ids(final["outcomes"].get("not-ready"))
    assert LINE in ids(final["outcomes"]["dead"])


def test_a_cancel_row_carries_no_cycle_in_the_sequence():
    final = wide()
    seq = {r["identifier"]: r for r in final["sequence"]}
    for identifier in (JUDGED, LINE, SWEPT):
        assert seq[identifier]["outcome"] == "dead"
        assert seq[identifier]["cycle"] is None


def test_verify_proposal_reads_every_card_on_offer():
    cards = lane()
    linear = FakeLinear({"DRE-319": {"comments": [comment(SUPERSEDED)]}},
                        lanes={"DRE-4700": "Done"})
    final = groomer.verify_proposal(cards, shaping(cards), lops=linear,
                                    run=FakeGh(), owners=OWNERS)
    swept = final["verification"]["swept"]
    assert "DRE-319" in swept and "DRE-320" in swept
    assert "DRE-319" in ids(final["outcomes"]["dead"])
    assert final["verification"]["swept_to_cancel"] == ["DRE-319"]
    assert final["sweep"]["read"] == len(swept)
    assert final["sweep"]["to_cancel"] == 1
    assert "DRE-319" in final["sweep"]["outside_batch"]


def test_a_failed_check_still_sweeps_and_says_every_swept_card_unread():
    class Broken(FakeLinear):
        def gql(self, query, variables=None):
            raise RuntimeError("linear is down")

    cards = lane()

    def boom(*a, **k):
        raise RuntimeError("the check fell over")

    real = groom_verify.check
    groom_verify.check = boom
    try:
        final = groomer.verify_proposal(cards, shaping(cards), lops=Broken(),
                                        run=FakeGh(), owners=OWNERS)
    finally:
        groom_verify.check = real
    swept = final["verification"]["swept"]
    assert swept, "a failed check says what the sweep would have read"
    assert final["sweep"]["read"] == len(swept)
    assert final["sweep"]["unread"] == len(swept)
    assert final["sweep"]["to_cancel"] == 0


# --------------------------------------------------------------------------
# a Cancel row holds no slot
# --------------------------------------------------------------------------
def test_twenty_five_ready_cards_and_three_cancels_make_twenty_and_three():
    cards = [card(f"DRE-{400 + n}", days=n,
                  description=("Superseded by: DRE-900"
                               if n in (3, 10, 15) else ""))
             for n in range(1, 29)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW)
    assert len(proposal["outcomes"]["now"]) == 20
    assert ids(proposal["outcomes"]["dead"]) == ["DRE-403", "DRE-410",
                                                 "DRE-415"]
    assert proposal["batch"] == {"cycles": [12], "cards": 20}
    planning = ids(proposal["outcomes"]["now"])
    assert planning == [f"DRE-{400 + n}" for n in range(1, 24)
                        if n not in (3, 10, 15)]
    # All three were inside the batch cycles under DRE-4727's walk too.
    assert proposal.get("sweep", {"outside_batch": []})["outside_batch"] == []


# --------------------------------------------------------------------------
# the refusals are unchanged and asked of every row
# --------------------------------------------------------------------------
def refused_lane() -> tuple[list[dict], dict]:
    """DRE-315, an epic with an open child the read calls likely-done, and
    DRE-316, a likely-done whose pointer the plain-English guard withholds —
    both far outside a batch of three."""
    cards = [card(f"DRE-{300 + n}", days=n) for n in range(1, 21)]
    cards[14]["children"] = {"nodes": [
        {"identifier": "DRE-9151", "state": {"name": "In Progress"}}]}
    likely = {"DRE-315": POINTER, "DRE-316": TECHNICAL}
    return cards, shaping(cards, likely=likely, readiness=False)


def test_an_epic_with_an_open_child_outside_the_batch_is_refused_and_said():
    cards, shape = refused_lane()
    proposal = groomer.propose(cards, **shape)
    assert "DRE-315" not in ids(proposal["outcomes"]["dead"])
    assert "DRE-315" in ids(proposal["outcomes"]["not-now"]), (
        "a refused row keeps the place the order gave it")
    refused = {r["identifier"]: r for r in proposal["cancels_refused"]}
    assert refused["DRE-315"]["refusal"] == groomer.cancel_refusal(
        {"identifier": "DRE-315", "open_children": 1})
    assert refused["DRE-315"]["source"] == groomer.DEAD_FROM_JUDGEMENT
    text = groomer.render_proposal(proposal)
    section = text.split("## Cancels refused\n", 1)[1].split("\n## ", 1)[0]
    assert "- DRE-315: DRE-315 is an epic with 1 open child" in section


def test_a_withheld_pointer_outside_the_batch_is_no_cancel():
    cards, shape = refused_lane()
    proposal = groomer.propose(cards, **shape)
    assert "DRE-316" not in ids(proposal["outcomes"]["dead"])
    assert "DRE-316" in ids(proposal["outcomes"]["not-now"])
    refused = {r["identifier"]: r for r in proposal["cancels_refused"]}
    assert refused["DRE-316"]["refusal"] == groomer.NO_CANCEL_PROOF
    assert groomer.cancel_proof_refusal(
        {"identifier": "DRE-316", "source": groomer.DEAD_FROM_JUDGEMENT,
         "proof": [], "children": 0}) == groomer.NO_CANCEL_PROOF
    assert proposal["outcomes"]["dead"] == []


def test_a_refused_row_inside_the_batch_keeps_its_planning_slot():
    cards, shape = refused_lane()
    shape = {**shape, "capacity": 20}
    proposal = groomer.propose(cards, **shape)
    assert {"DRE-315", "DRE-316"} <= set(ids(proposal["outcomes"]["now"]))
    assert [r["identifier"] for r in proposal["cancels_refused"]] == [
        "DRE-315", "DRE-316"]


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------
def page_scenario() -> dict:
    """Rules only, at a capacity of three: DRE-301..303 planned, ten spares,
    and DRE-314..320 swept. DRE-318 is declared superseded on its thread,
    Linear will not answer for DRE-319, and DRE-317 — in agent-bureau —
    carries its own `Superseded by:` line."""
    cards = [card(f"DRE-{300 + n}", days=n) for n in range(1, 21)]
    cards[16] = card("DRE-317", days=17, repo="agent-bureau",
                     description="Superseded by: DRE-4700")
    linear = FakeLinear({"DRE-318": {"comments": [comment(SUPERSEDED)]}},
                        lanes={"DRE-4700": "Done"}, fail={"DRE-319"})
    return groomer.verify_proposal(
        cards, dict(cycles=CYCLES, capacity=3, now=NOW), lops=linear,
        run=FakeGh(), owners=OWNERS)


def check_section(text: str) -> list[str]:
    body = text.split("## What the check read\n", 1)[1].split("\n## ", 1)[0]
    return body.strip().splitlines()


def test_what_the_check_read_carries_the_swept_line():
    final = page_scenario()
    swept = final["verification"]["swept"]
    assert len(swept) == 6, swept
    lines = check_section(groomer.render_proposal(final))
    assert ("- Swept: 6 more cards in Intake were read for dead-card "
            "evidence — 1 moved to Cancel, 1 could not be read.") in lines
    # The opening says how many it read: the lists, the spares and the
    # swept cards in Intake.
    assert "6 more in Intake swept for dead-card evidence" in lines[0]
    assert final["sweep"] == {"read": 6, "to_cancel": 1, "unread": 1,
                              "outside_batch": ["DRE-317", "DRE-318"]}


def test_the_cancel_table_parses_back_through_the_drains_reader():
    final = page_scenario()
    record = groomer.parse_proposal_comment(groomer.proposal_comment(final))
    dead = sorted(final["outcomes"]["dead"], key=lambda r: r["position"])
    assert [(r["identifier"], r["position"], r["repo"], r["reason"])
            for r in record["cancel"]] == [
        (r["identifier"], r["position"], r["repo"], r["reason"]) for r in dead]
    assert [r["identifier"] for r in record["cancel"]] == ["DRE-317",
                                                           "DRE-318"]
    assert [r["repo"] for r in record["cancel"]] == ["agent-bureau", "portico"]
    assert len(record["batch"]) == 3
    for row in cancel_rows(groomer.render_proposal(final)):
        assert len(row["cells"]) == 7


def quiet() -> dict:
    """A morning whose sweep reads two Not ready cards and finds nothing:
    the proof file's ten-card lane — a `Superseded by:` row, a ranked-read
    row and a check row, all three in the batch — and DRE-211 and DRE-212,
    each holding an open question."""
    cards = _proof_lane() + [
        card(f"DRE-{200 + n}", days=n, description=QUESTION) for n in (11, 12)]
    return groomer.verify_proposal(
        cards, dict(cycles=CYCLES, capacity=20, now=NOW,
                    judgement=judged(cards, _answer(cards)),
                    readiness=_facts()),
        lops=_proof_linear(), run=FakeGh(), owners=OWNERS)


def cancel_section(text: str) -> str:
    at = text.index(groomer.CANCEL_HEADING)
    rest = text[at:]
    end = rest.find("\n## ", 1)
    return rest if end < 0 else rest[:end + 1]


def test_a_sweep_that_found_nothing_renders_the_cancel_section_as_before():
    final = quiet()
    assert final["verification"]["swept"] == ["DRE-211", "DRE-212"]
    assert final["sweep"] == {"read": 2, "to_cancel": 0, "unread": 0,
                              "outside_batch": []}
    stored = QUIET_CANCEL.read_text(encoding="utf-8")
    assert cancel_section(groomer.render_proposal(final)) == stored
    lines = check_section(groomer.render_proposal(final))
    assert ("- Swept: 2 more cards in Intake were read for dead-card "
            "evidence — 0 moved to Cancel, 0 could not be read.") in lines


def test_a_record_written_before_the_sweep_renders_no_swept_line():
    final = quiet()
    old = copy.deepcopy(final)
    old.pop("sweep")
    text = groomer.render_proposal(old)
    assert "- Swept:" not in text
    assert "swept for dead-card evidence" not in text


# --------------------------------------------------------------------------
# the words
# --------------------------------------------------------------------------
def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_doc_no_longer_draws_the_cancel_list_from_the_twenty():
    doc = _flat((ROOT / "docs" / "groomer.md").read_text(encoding="utf-8"))
    for old in ("drawn from the same twenty",
                "The morning's twenty are Planning plus Cancel together",
                "It is one of the morning's twenty",
                "the one Cancel row not drawn from the morning's twenty",
                "The groomer does not turn it on yet"):
        assert old not in doc, old
    assert "DRE-7973" in doc and "DRE-6648" in doc


def test_the_brief_no_longer_says_among_the_mornings_twenty():
    brief = _flat((ROOT / "briefs" / "groomer.md").read_text(encoding="utf-8"))
    assert "among the morning's twenty" not in brief
    assert "DRE-4727" in brief and "DRE-6648" in brief


def test_the_module_says_the_new_rule():
    doc = _flat(groomer.__doc__)
    assert "drawn from the same twenty" not in doc
    assert "DRE-6648" in doc and "DRE-4727" in doc
    propose_doc = _flat(groomer.propose.__doc__)
    assert "DRE-6648" in propose_doc and "DRE-4727" in propose_doc
    source = _flat((ROOT / "scripts" / "groomer.py").read_text(encoding="utf-8"))
    assert "the Cancel list is drawn from the same twenty as the Planning list" \
        not in source
