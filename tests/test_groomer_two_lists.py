"""The proposal is two lists, Planning and Cancel, drawn from the same twenty
(DRE-4727).

The CEO's decision on DRE-4669 (2026-09-23): when the groomer looks at the 20
oldest cards it also decides whether each one still applies, and the ones that
were replaced, superseded or already done go on a list he can agree with or
not. What this file holds:

  * **the set arithmetic** — the morning's `capacity` cards are Planning plus
    Cancel together, walked in the rules' order; a card outside the set waits
    its turn whatever its description or the read says about it;
  * **the record** — every Cancel row carries a `position` restarting at 1 and
    a non-empty one-line `reason`, the one the drain later writes on the card;
  * **the table** — `CANCEL_HEADING` and `CANCEL_COLUMNS` are the wire contract
    the console's reader (DRE-4682 in agent-bureau) is written against, and
    the section is ABSENT when there is nothing to cancel;
  * **the id binds both lists**, and never a reason's text;
  * **an empty proposal is one with neither list.**

`tests/fixtures/groom_two_lists_render.md` is this repo's copy of the wire
contract; its twin is the console's own fixture.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_two_lists.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import groom_judgement  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, NOW, card, judged, ranked  # noqa: E402
from test_groomer_retry import FakeOps  # noqa: E402

TWO_LISTS_RENDER = ROOT / "tests" / "fixtures" / "groom_two_lists_render.md"
CONSOLE_TWIN = "console/backend/tests/fixtures/groom-proposal-two-lists/proposal.md"

SUPERSEDED = ("DRE-3", "DRE-8", "DRE-12")
LIKELY_DONE = ("DRE-5", "DRE-15")


def lane(count: int = 25, *, superseded=SUPERSEDED + ("DRE-22",)) -> list:
    """`count` cards, DRE-1 the oldest and each one a day newer than the last,
    so the rules' order is the identifier order."""
    return [card(f"DRE-{n}", days=100 - n,
                 description=("Superseded by: DRE-900"
                              if f"DRE-{n}" in superseded else ""))
            for n in range(1, count + 1)]


def answer_for(cards, *, likely_done=LIKELY_DONE + ("DRE-24",),
               unranked=()) -> str:
    lines = []
    for c in cards:
        cid = c["identifier"]
        if cid in unranked:
            continue
        if cid in likely_done:
            lines.append(f"{cid} | likely-done | the work already happened | "
                         f"pull request {cid[4:]}0 merged it")
        else:
            lines.append(ranked([cid]))
    return "\n".join(lines)


def two_lists(**kwargs):
    cards = lane()
    return groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                           judgement=judged(cards, answer_for(cards)), **kwargs)


def ids(rows) -> list:
    return [row["identifier"] for row in sorted(rows, key=lambda r: r["position"])]


# --------------------------------------------------------------------------
# the set arithmetic
# --------------------------------------------------------------------------
def test_the_twenty_are_fifteen_for_planning_and_five_for_cancel():
    proposal = two_lists()
    assert len(proposal["outcomes"]["now"]) == 15
    assert len(proposal["outcomes"]["dead"]) == 5
    # Cancel positions 1–5, in the rules' order.
    assert ids(proposal["outcomes"]["dead"]) == [
        "DRE-3", "DRE-5", "DRE-8", "DRE-12", "DRE-15"]
    assert [r["position"] for r in proposal["outcomes"]["dead"]] == [1, 2, 3, 4, 5]
    later = {r["identifier"] for r in proposal["outcomes"]["not-now"]}
    assert "DRE-21" in later, "the 21st card is past the morning's twenty"


def test_the_planning_list_is_numbered_from_one_in_the_rules_order():
    proposal = two_lists()
    planning = sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])
    assert [r["position"] for r in planning] == list(range(1, 16))
    assert [r["identifier"] for r in planning] == [
        f"DRE-{n}" for n in range(1, 21)
        if f"DRE-{n}" not in SUPERSEDED + LIKELY_DONE]


def test_a_card_to_cancel_outside_the_twenty_waits_its_turn():
    """DRE-22 says it was superseded and the read called DRE-24 done, and
    neither is in the morning's twenty: they are `not-now` with a cycle, not
    cancelled early and not shown as dead."""
    proposal = two_lists()
    later = {r["identifier"]: r for r in proposal["outcomes"]["not-now"]}
    for cid in ("DRE-22", "DRE-24"):
        assert cid in later
        assert later[cid]["reconsidered_in"] == 13
        assert cid not in {r["identifier"] for r in proposal["outcomes"]["dead"]}
    seq = {r["identifier"]: r for r in proposal["sequence"]}
    assert seq["DRE-22"]["outcome"] == "not-now"
    assert seq["DRE-3"]["outcome"] == "dead"
    assert groomer.CANCEL_HEADING in groomer.render_proposal(proposal)
    body = groomer.render_proposal(proposal).split(groomer.CANCEL_HEADING)[1]
    body = body.split("\n## ", 1)[0]
    assert "DRE-22" not in body and "DRE-24" not in body


def test_with_no_judgement_and_no_superseded_line_the_twenty_are_all_planning():
    cards = lane(superseded=())
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW)
    assert len(proposal["outcomes"]["now"]) == 20
    assert proposal["outcomes"]["dead"] == []


def test_an_unranked_card_is_skipped_and_the_next_card_takes_its_slot():
    cards = lane()
    proposal = groomer.propose(
        cards, cycles=CYCLES, capacity=20, now=NOW,
        judgement=judged(cards, answer_for(cards, unranked=("DRE-2",))))
    placed = ({r["identifier"] for r in proposal["outcomes"]["now"]}
              | {r["identifier"] for r in proposal["outcomes"]["dead"]})
    assert "DRE-2" not in placed
    assert "DRE-21" in placed
    assert len(placed) == 20
    assert proposal["judgement"]["unranked"] == ["DRE-2"]


def test_an_epic_is_never_split_between_the_set_and_the_rest():
    cards = [card(f"DRE-{n}", days=100 - n) for n in range(1, 4)]
    cards += [card(f"DRE-{n}", days=50, parent="DRE-800",
                   description=("Superseded by: DRE-900" if n == 10 else ""))
              for n in (10, 11)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=4, now=NOW)
    placed = ({r["identifier"] for r in proposal["outcomes"]["now"]}
              | {r["identifier"] for r in proposal["outcomes"]["dead"]})
    # Three cards and a two-card epic do not fit in four: the epic waits whole,
    # the card it would cancel included.
    assert placed == {"DRE-1", "DRE-2", "DRE-3"}
    later = {r["identifier"] for r in proposal["outcomes"]["not-now"]}
    assert {"DRE-10", "DRE-11"} <= later


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------
def test_every_cancel_row_carries_a_position_and_a_one_line_reason():
    proposal = two_lists()
    dead = {r["identifier"]: r for r in proposal["outcomes"]["dead"]}
    for row in dead.values():
        assert isinstance(row["position"], int)
        assert isinstance(row["reason"], str) and row["reason"].strip()
        for key in ("identifier", "title", "repo", "superseded_by", "source"):
            assert key in row
    assert dead["DRE-3"]["reason"] == "superseded by DRE-900"
    assert dead["DRE-3"]["source"] == groomer.DEAD_FROM_LINE
    assert dead["DRE-5"]["reason"] == "pull request 50 merged it"
    assert dead["DRE-5"]["source"] == groomer.DEAD_FROM_JUDGEMENT


def test_a_refused_evidence_is_the_withheld_sentence_never_an_empty_reason():
    cards = [card("DRE-1", days=3), card("DRE-2", days=2)]
    answer = "\n".join([
        "DRE-1 | likely-done | a merge already did it | see scripts/groomer.py",
        ranked(["DRE-2"]),
    ])
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=judged(cards, answer))
    row = proposal["outcomes"]["dead"][0]
    assert row["identifier"] == "DRE-1"
    assert row["reason"] == groomer.WITHHELD_REASON
    assert "DRE-1" in proposal["judgement"]["withheld"]


def test_the_dead_outcome_says_the_drain_cancels_it_once_the_ceo_agrees():
    text = groomer.OUTCOMES["dead"]
    assert "never cancelled here" not in text
    assert "the operator executes" not in text
    assert "drain" in text and "reason" in text


# --------------------------------------------------------------------------
# the table — the wire contract DRE-4682 reads
# --------------------------------------------------------------------------
def test_the_cancel_heading_and_columns_are_the_contracted_literals():
    assert groomer.CANCEL_HEADING == "## Cancel, with reasons"
    assert groomer.CANCEL_COLUMNS == "| # | Card | Pri | Repo | Epic | Title | Reason |"


def cancel_rows(text: str) -> list:
    """The console's reader, as DRE-4682 specifies it: the section under
    `CANCEL_HEADING` up to the next `## `, rows matched by the batch table's
    regex, cells split on an UNESCAPED pipe, the reason the seventh cell."""
    body = text.split(groomer.CANCEL_HEADING + "\n", 1)[1]
    body = body.split("\n## ", 1)[0]
    rows = []
    for line in body.splitlines():
        if not re.match(r"^\|\s*(\d+)\s*\|\s*(DRE-\d+)\s*\|", line.strip()):
            continue
        cells = [c.strip() for c in
                 re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        rows.append({"position": int(cells[0]), "identifier": cells[1],
                     "cells": cells,
                     "reason": cells[6].replace("\\|", "|")})
    return rows


def test_the_cancel_list_renders_as_a_table_under_the_contracted_heading():
    proposal = two_lists()
    text = groomer.render_proposal(proposal)
    assert "## Recommended dead" not in text
    assert text.count(groomer.CANCEL_HEADING) == 1
    body = text.split(groomer.CANCEL_HEADING + "\n", 1)[1].split("\n## ", 1)[0]
    table = [line for line in body.splitlines() if line.startswith("|")]
    assert table[0] == groomer.CANCEL_COLUMNS
    assert table[1] == "| -- | -- | -- | -- | -- | -- | -- |"
    rows = cancel_rows(text)
    assert [(r["position"], r["identifier"]) for r in rows] == [
        (1, "DRE-3"), (2, "DRE-5"), (3, "DRE-8"), (4, "DRE-12"), (5, "DRE-15")]
    for row, record in zip(rows, sorted(proposal["outcomes"]["dead"],
                                        key=lambda r: r["position"])):
        assert len(row["cells"]) == 7
        assert row["reason"] == record["reason"]


def test_the_first_six_cells_are_the_batch_tables_first_six():
    cards = [card("DRE-1", days=9, priority=2, parent="DRE-800",
                  description="Superseded by: DRE-900",
                  title="replace the old intake sweep"),
             card("DRE-2", days=5)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW)
    [row] = cancel_rows(groomer.render_proposal(proposal))
    assert row["cells"][:6] == ["1", "DRE-1", "High", "portico", "DRE-800",
                                "replace the old intake sweep"]


def test_a_reason_carrying_a_pipe_is_escaped_whole_and_round_trips():
    reason = ("merged in pull request 274 | the CEO agreed on DRE-4669, and "
              "this line runs well past the ninety characters a Why cell is cut at")
    cards = [card("DRE-1", days=3), card("DRE-2", days=2)]
    verdicts = {"DRE-1": groom_judgement.Verdict("likely-done", "done", reason),
                "DRE-2": groom_judgement.Verdict("now", "wanted")}
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW,
                               judgement=verdicts)
    text = groomer.render_proposal(proposal)
    assert "pull request 274 \\| the CEO" in text
    [row] = cancel_rows(text)
    assert row["reason"] == reason, "the reason is never truncated"


def test_an_empty_cancel_list_renders_no_cancel_section_at_all():
    cards = [card(f"DRE-{n}", days=10 - n) for n in range(1, 5)]
    text = groomer.render_proposal(
        groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW))
    assert "## Cancel" not in text, (
        "DRE-4682 reads an absent section as 'no cancellation proposed'"
    )
    assert groomer.CANCEL_COLUMNS not in text
    assert "Recommended dead" not in text


def test_the_page_names_both_lists_and_what_approval_does():
    proposal = two_lists()
    text = groomer.render_proposal(proposal)
    lead = text.splitlines()[2]
    assert lead.startswith("20 cards of 25 in Intake are proposed for cycle 12")
    assert "15 for Planning and 5 for Cancel" in lead
    assert groomer._LANE_LINE.search(text).group(1) == "Intake"
    approve = next(l for l in text.splitlines() if l.startswith("**To approve:**"))
    assert "Planning list to Planning" in approve
    assert "Cancel list to Canceled" in approve
    assert f"{groomer.EXCLUDE_TAG}: {proposal['id']} DRE-N" in text
    assert "either list" in text


def test_the_batch_table_still_round_trips_through_the_drains_reader():
    proposal = two_lists()
    got = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert [r["identifier"] for r in got["batch"]] == ids(proposal["outcomes"]["now"])
    assert not {r["identifier"] for r in got["batch"]} & set(SUPERSEDED + LIKELY_DONE)


# --------------------------------------------------------------------------
# the id binds both lists
# --------------------------------------------------------------------------
def _with_cancel(proposal, rows):
    return {**proposal, "outcomes": {**proposal["outcomes"], "dead": rows}}


def test_the_id_changes_when_a_cancel_row_is_added_removed_or_reordered():
    proposal = two_lists()
    base = groomer.proposal_id(proposal)
    dead = proposal["outcomes"]["dead"]
    removed = _with_cancel(proposal, dead[:-1])
    added = _with_cancel(proposal, dead + [{**dead[0], "identifier": "DRE-99",
                                            "position": 6}])
    swapped = [{**dead[1], "position": 1}, {**dead[0], "position": 2}] + dead[2:]
    reordered = _with_cancel(proposal, swapped)
    ids_ = {base, groomer.proposal_id(removed), groomer.proposal_id(added),
            groomer.proposal_id(reordered)}
    assert len(ids_) == 4


def test_the_id_does_not_change_when_only_a_reason_reads_differently():
    proposal = two_lists()
    reworded = _with_cancel(proposal, [{**r, "reason": r["reason"] + " — reworded"}
                                       for r in proposal["outcomes"]["dead"]])
    assert groomer.proposal_id(reworded) == groomer.proposal_id(proposal)
    assert proposal["id"] == groomer.proposal_id(proposal)


# --------------------------------------------------------------------------
# an empty proposal is one with neither list
# --------------------------------------------------------------------------
def test_a_proposal_with_only_a_cancel_row_is_posted():
    cards = [card("DRE-1", description="Superseded by: DRE-900")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, now=NOW)
    assert proposal["outcomes"]["now"] == []
    assert len(proposal["outcomes"]["dead"]) == 1
    ops = FakeOps()
    assert groomer.post_proposal(ops, "DRE-2840", proposal) is True
    assert len(ops.posted) == 1


def test_a_proposal_with_neither_list_is_still_refused():
    proposal = groomer.propose([], cycles=CYCLES, capacity=5, now=NOW)
    ops = FakeOps()
    assert groomer.post_proposal(ops, "DRE-2840", proposal) is False
    assert ops.posted == []


# --------------------------------------------------------------------------
# the committed copy of the wire contract
# --------------------------------------------------------------------------
def fixture_proposal() -> dict:
    """Three Planning rows and two Cancel rows, one reason carrying a pipe."""
    cards = [card("DRE-4101", days=40, title="retire the nightly sweep's age-out"),
             card("DRE-4102", days=39, description="Superseded by: DRE-4250",
                  title="first cut of the intake census"),
             card("DRE-4103", days=38, priority=2, repo="agent-bureau",
                  title="show the proposal's two lists on the console"),
             card("DRE-4104", days=37, title="write the cancel reason on the card"),
             card("DRE-4105", days=36, repo="agent-bureau",
                  title="a probe of the old board cutover")]
    verdicts = {
        "DRE-4101": groom_judgement.Verdict("now", "the pile is only drained "
                                                   "oldest first if nothing ages out"),
        "DRE-4103": groom_judgement.Verdict("now", "the CEO reads both lists there"),
        "DRE-4104": groom_judgement.Verdict("now", "the drain needs somewhere "
                                                   "to put the reason"),
        "DRE-4105": groom_judgement.Verdict(
            "likely-done", "the cutover already ran",
            "the cutover ran on 2026-09-07 | the probe is the proof"),
    }
    return groomer.propose(cards, cycles=CYCLES, capacity=20, now=NOW,
                           judgement=verdicts)


def test_the_two_lists_fixture_is_the_render_byte_for_byte():
    text = TWO_LISTS_RENDER.read_text(encoding="utf-8")
    header, sep, body = text.partition("-->\n\n")
    assert sep, "the fixture opens with its header comment"
    assert "DRE-4682" in header and CONSOLE_TWIN in header, (
        "the fixture names the console's twin, so a reader of either repo "
        "knows where the other half lives"
    )
    proposal = fixture_proposal()
    assert body == groomer.proposal_comment(proposal), (
        "the wire contract moved — the console's reader (DRE-4682) is written "
        "against this page"
    )
    rows = cancel_rows(body)
    assert [r["position"] for r in rows] == [1, 2]
    assert body.count("\\|") == 1
    assert rows[1]["reason"] == "the cutover ran on 2026-09-07 | the probe is the proof"
    assert len(groomer.parse_proposal_comment(body)["batch"]) == 3


# --------------------------------------------------------------------------
# the brief and the doc say the same
# --------------------------------------------------------------------------
def test_the_brief_describes_likely_done_as_the_cancel_recommendation():
    brief = (ROOT / "briefs" / "groomer.md").read_text(encoding="utf-8")
    prompt = groom_judgement.brief_prompt()
    assert "Cancel list" in prompt
    for thing in ("superseding card", "merged pull request", "decision"):
        assert thing in prompt, f"the brief never asks the evidence to name {thing!r}"
    assert "written onto the card" in prompt
    assert prompt in brief


def test_the_doc_says_the_drain_cancels_an_agreed_card_and_names_the_reader():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    outcomes = doc.split("## The outcomes, and what each one owes the reader", 1)[1]
    outcomes = outcomes.split("\n## ", 1)[0]
    assert "DRE-4682" in outcomes
    assert groomer.CANCEL_HEADING in outcomes
    assert "the drain cancels" in outcomes.lower()
    assert "never cancelled here" not in outcomes
