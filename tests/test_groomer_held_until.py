"""RED-first: a held card waits for what it was held for, and a Move to Cancel
is carried as the decision it is (DRE-7826).

DRE-7824 gave the groomer its memory, and read two of the console's controls
wrong. Every hold was "rank it last", whatever it was held until; and a Move to
Cancel was read as Don't do, which keeps the card in Intake for ever. Now
`_build` asks `groom_memory.standing` where each remembered card stands this
morning — the states of the cards a hold names read in one Linear request,
today the Pacific date — and `propose` acts on the answer:

  * a card held until a named card is off both lists until that card is
    `Done`, and on offer — said as back on offer — on the first proposal after;
  * a card held until a date is off both lists until that Pacific date;
  * a card moved to Cancel is on the next proposal's Cancel list, whatever
    its place in the order and with no Planning slot, the person's decision
    as its proof — so approving that proposal cancels it;
  * a hold with no condition still ranks last, and a proposal with no stored
    decision renders as it did before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_held_until.py -q
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import groom_judgement  # noqa: E402
import groom_verify  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, NOW, card  # noqa: E402
from test_groom_verify import (LANE, FakeGh, FakeLinear, OWNERS,  # noqa: E402
                               _args, cancel, planning)
from test_groomer_cancel_drain import FakeOps  # noqa: E402
from test_groomer_memory import (CURRENT, EARLIER, EARLIER_ANSWERS,  # noqa: E402
                                 Threads, door, row, said, section)

NO_DECISION_RENDER = ROOT / "tests" / "fixtures" / "groom_no_decision_render.md"
BACK_ON_OFFER = "Back on offer — the hold you set is met:"
SAY_MORE = "**To say more than yes:**"
ROW_PID = "50e396c78523"          # `row()`'s proposal id
# `row()`'s time, 2026-09-22 02:49 UTC, is the evening of 2026-09-21 in PT.
ROW_DAY = "2026-09-21"
DRAIN_CARD = "DRE-2683"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
class States:
    """`linear_ops.gql` as the state read sees it: one aliased request, each
    alias one card's `state { name }`. A card missing from `states` comes back
    as no issue; `fail` makes the whole request raise."""

    def __init__(self, states=None, *, fail=False):
        self.states = dict(states or {})
        self.fail = fail
        self.asked: list[list[str]] = []

    def gql(self, query, variables=None):
        ids = [v for _, v in sorted((variables or {}).items())]
        self.asked.append(ids)
        if self.fail:
            raise RuntimeError("linear answered 503")
        out = {}
        for name, identifier in (variables or {}).items():
            alias = "n" + name[1:]
            state = self.states.get(identifier)
            out[alias] = ({"identifier": identifier, "state": {"name": state}}
                          if state else None)
        return out


def build(monkeypatch, cards=LANE, *, rows=(), threads=None, states=None,
          now=NOW, capacity=3, **kw):
    """`_build` over `cards`, the door answering `rows`, the threads `threads`
    and the state read `states` (a `States`, or a `{card: state}` map)."""
    ops = Threads(threads)
    reader = states if isinstance(states, States) else States(states)
    monkeypatch.setattr(groomer, "_now", lambda: now)
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: list(cards))
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groomer.linear_ops, "comment_records",
                        ops.comment_records)
    monkeypatch.setattr(groomer.linear_ops, "gql", reader.gql)
    monkeypatch.setattr(bureau_read, "groom_removals", door(list(rows)))
    built = groomer._build(_args(card=CURRENT, capacity=capacity, **kw))
    built["_states_asked"] = reader.asked
    return built


def cancel_ids(proposal):
    return [r["identifier"] for r in cancel(proposal)]


def everywhere(proposal):
    return (set(planning(proposal)) | set(cancel_ids(proposal))
            | {r["identifier"] for r in proposal["sequence"]})


def earlier(proposal):
    return section(groomer.render_proposal(proposal), EARLIER_ANSWERS)


def recorded_reason(day=ROW_DAY, pid=ROW_PID, actor="Sid"):
    return (f"You moved this card to Cancel on {day} (proposal {pid}); "
            f"the console's record — Move to Cancel recorded by {actor} on "
            f"{day}, proposal {pid}")


def epic(identifier, *, days, child_state):
    c = card(identifier, days=days, title=f"[EPIC] {identifier}")
    c["children"] = {"nodes": [{"identifier": "DRE-990",
                                "state": {"name": child_state}}]}
    return c


# --------------------------------------------------------------------------
# held until a named card
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state, lane", [
    ("In Review", "In Review"), ("Canceled", "Canceled"),
    ("Duplicate", "Duplicate"), (None, "state not read")])
def test_a_card_held_until_another_card_is_off_both_lists_until_it_is_done(
        monkeypatch, state, lane):
    before = build(monkeypatch)
    assert "DRE-102" in planning(before), "the fixture never offered DRE-102"
    after = build(monkeypatch, rows=[row("DRE-102", "hold", until_card="DRE-9")],
                  states={"DRE-9": state} if state else {})
    assert "DRE-102" not in everywhere(after)
    assert planning(after) == ["DRE-101", "DRE-103", "DRE-104"]
    assert "held_before" not in after, "a conditional hold is not ranked last"
    [held] = after["held_until"]
    assert set(held) == {"identifier", "hold_until_card", "hold_until_date",
                         "why"}
    assert held["identifier"] == "DRE-102"
    assert held["hold_until_card"] == "DRE-9"
    assert held["hold_until_date"] is None
    assert "DRE-9" in held["why"]
    assert "back_on_offer" not in after
    page = earlier(after)
    named = [ln for ln in page.splitlines() if "DRE-102" in ln]
    assert len(named) == 1, page
    assert f"Held until DRE-9 ({lane})" in named[0], named[0]


def test_an_open_hold_card_is_said_as_not_offered_until_it_is_done(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "hold", until_card="DRE-9")],
                  states={"DRE-9": "In Review"})
    assert ("Held until DRE-9 (In Review) — not offered until it is Done: "
            "DRE-102") in earlier(after)


@pytest.mark.parametrize("state", ["Canceled", "Duplicate", None])
def test_a_hold_that_cannot_be_met_says_why(monkeypatch, state):
    after = build(monkeypatch, rows=[row("DRE-102", "hold", until_card="DRE-9")],
                  states={"DRE-9": state} if state else {})
    line = [ln for ln in earlier(after).splitlines() if "DRE-102" in ln][0]
    assert after["held_until"][0]["why"] in line, line
    assert "not offered until it is Done" not in line, line


def test_a_card_whose_hold_card_is_done_is_back_on_offer_and_not_ranked_last(
        monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "hold", until_card="DRE-9")],
                  states={"DRE-9": "Done"})
    assert planning(after) == ["DRE-101", "DRE-102", "DRE-103"]
    assert "held_before" not in after
    assert not any(r.get("held") for r in after["outcomes"]["now"])
    assert "held_until" not in after
    assert after["back_on_offer"] == [{"identifier": "DRE-102",
                                       "why": "DRE-9 is Done"}]
    lines = [ln for ln in groomer.render_proposal(after).splitlines()
             if ln.startswith(BACK_ON_OFFER)]
    assert lines == [f"{BACK_ON_OFFER} DRE-102 (DRE-9 is Done)."]


def test_a_hold_off_a_thread_marker_is_honored_the_same_way(monkeypatch):
    thread = [said(groomer.HOLD_TAG, "DRE-102", "until DRE-9")]
    held = build(monkeypatch, threads={EARLIER: thread},
                 states={"DRE-9": "In Progress"})
    assert "DRE-102" not in everywhere(held)
    back = build(monkeypatch, threads={EARLIER: thread},
                 states={"DRE-9": "Done"})
    assert "DRE-102" in planning(back)


def test_the_hold_cards_are_read_in_one_request(monkeypatch):
    rows = [row("DRE-102", "hold", until_card="DRE-9"),
            row("DRE-103", "hold", until_card="DRE-8", ident=2),
            row("DRE-104", "hold", until_card="DRE-9", ident=3)]
    after = build(monkeypatch, rows=rows,
                  states={"DRE-9": "Done", "DRE-8": "Todo"})
    assert len(after["_states_asked"]) == 1, after["_states_asked"]
    assert sorted(after["_states_asked"][0]) == ["DRE-8", "DRE-9"]
    assert "DRE-103" not in everywhere(after)
    assert {"DRE-102", "DRE-104"} <= set(planning(after))


def test_no_request_when_no_hold_names_a_card(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "hold"),
                                     row("DRE-103", "hold", ident=2,
                                         until_date="2027-01-01")])
    assert after["_states_asked"] == []


def test_a_state_read_that_fails_keeps_every_hold(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "hold", until_card="DRE-9")],
                  states=States({"DRE-9": "Done"}, fail=True))
    assert "DRE-102" not in everywhere(after)
    assert "Held until DRE-9 (state not read)" in earlier(after)


def test_cards_held_until_one_card_share_its_line(monkeypatch):
    rows = [row("DRE-102", "hold", until_card="DRE-9"),
            row("DRE-104", "hold", until_card="DRE-9", ident=2)]
    after = build(monkeypatch, rows=rows, states={"DRE-9": "In Review"})
    assert ("Held until DRE-9 (In Review) — not offered until it is Done: "
            "DRE-102, DRE-104.") in earlier(after)


# --------------------------------------------------------------------------
# held until a date
# --------------------------------------------------------------------------
@pytest.mark.parametrize("now", [
    "2026-09-05T12:00:00Z",      # 05:00 PT the day before
    "2026-09-06T06:30:00Z",      # 23:30 PT the day before
])
def test_a_card_held_until_a_date_is_off_both_lists_the_day_before(
        monkeypatch, now):
    after = build(monkeypatch, now=now,
                  rows=[row("DRE-102", "hold", until_date="2026-09-06")])
    assert "DRE-102" not in everywhere(after)
    [held] = after["held_until"]
    assert held["hold_until_date"] == "2026-09-06"
    assert held["hold_until_card"] is None
    assert ("Held until 2026-09-06 — not offered before then: DRE-102."
            in earlier(after))


@pytest.mark.parametrize("now", [
    "2026-09-06T07:00:00Z",      # 00:00 PT on the day
    "2026-09-06T20:00:00Z",      # 13:00 PT on the day
])
def test_a_card_held_until_a_date_is_back_on_offer_on_that_date(
        monkeypatch, now):
    after = build(monkeypatch, now=now,
                  rows=[row("DRE-102", "hold", until_date="2026-09-06")])
    assert "DRE-102" in planning(after)
    assert "held_before" not in after and "held_until" not in after
    assert after["back_on_offer"] == [{"identifier": "DRE-102",
                                       "why": "2026-09-06 has come"}]
    assert (f"{BACK_ON_OFFER} DRE-102 (2026-09-06 has come)."
            in groomer.render_proposal(after).splitlines())


# --------------------------------------------------------------------------
# a hold with no condition, and a morning with no decision
# --------------------------------------------------------------------------
def test_a_hold_with_no_condition_still_ranks_last(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "hold")])
    assert after["held_before"] == ["DRE-102"]
    assert "DRE-102" not in planning(after)
    assert "DRE-102" in {r["identifier"] for r in after["sequence"]}
    assert "held_until" not in after and "back_on_offer" not in after
    assert ("Held on an earlier proposal, so ranked after every card you have "
            "not held: DRE-102.") in earlier(after)


def test_a_proposal_with_no_stored_decision_renders_as_before(monkeypatch):
    """`groom_no_decision_render.md` is this page as `main` rendered it before
    this card — written by that groomer, not this one, and re-written by
    DRE-7973, whose Cancel row holds no Planning slot. The one difference is
    the sentence this card adds to the `To say more than yes` paragraph, named
    the console's controls."""
    lane = LANE + [card("DRE-130", days=2, description="Superseded by: DRE-90",
                        title="an old | thing")]
    built = build(monkeypatch, lane, capacity=4)
    for key in ("held_until", "back_on_offer", "hold_lanes"):
        assert key not in built
    page = groomer.render_proposal(built)
    before = NO_DECISION_RENDER.read_text(encoding="utf-8")
    old = next(ln for ln in before.splitlines() if ln.startswith(SAY_MORE))
    assert page == before.replace(old, f"{old} {groomer.CONSOLE_CONTROLS}"), (
        "a proposal with no stored decision no longer renders as it did")


def test_the_say_more_paragraph_names_the_consoles_three_controls():
    proposal = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW)
    line = next(ln for ln in groomer.render_proposal(proposal).splitlines()
                if ln.startswith(SAY_MORE))
    for control, marker in (("Not doing", groomer.EXCLUDE_TAG),
                            ("Move to Cancel", "move to Cancel"),
                            ("Hold until", groomer.HOLD_TAG)):
        assert control in line and marker in line, (control, line)
    assert line.index("Move to Cancel") < line.index("Cancel list", line.index(
        "Move to Cancel"))


# --------------------------------------------------------------------------
# Move to Cancel
# --------------------------------------------------------------------------
def test_a_cancel_is_the_contract_string():
    assert groomer.DEAD_FROM_RECORD == "console-record"
    assert groom_verify.RECORDED_CANCEL_SOURCE == groomer.DEAD_FROM_RECORD
    assert groomer.DEAD_FROM_RECORD not in groomer.UNATTRIBUTED_SOURCES


def test_a_recorded_cancel_outside_the_morning_is_on_the_cancel_list(
        monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-112", "cancel")])
    assert cancel_ids(after) == ["DRE-112"]
    assert planning(after) == ["DRE-101", "DRE-102", "DRE-103"]
    assert "excluded_before" not in after


def test_a_recorded_cancel_inside_the_morning_takes_no_slot(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-102", "cancel")])
    assert cancel_ids(after) == ["DRE-102"]
    assert planning(after) == ["DRE-101", "DRE-103", "DRE-104"]
    [seq] = [r for r in after["sequence"] if r["identifier"] == "DRE-102"]
    assert seq["outcome"] == "dead" and seq["cycle"] is None
    assert seq["source"] == groomer.DEAD_FROM_RECORD


def test_the_recorded_cancel_row_carries_the_decision_as_its_proof(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-112", "cancel")])
    [dead] = cancel(after)
    assert dead["source"] == groomer.DEAD_FROM_RECORD
    assert dead["proof"] == [{
        "source": "the console's record",
        "quote": f"Move to Cancel recorded by Sid on {ROW_DAY}, "
                 f"proposal {ROW_PID}"}]
    assert dead["reason"] == recorded_reason()
    assert dead["judged"] is False
    assert dead["evidence"] == (f"You moved this card to Cancel on {ROW_DAY} "
                                f"(proposal {ROW_PID})")
    assert groomer.cancel_proof_refusal(dead) is None
    assert groomer.cancel_refusal(dead) is None


def test_a_cancel_off_a_thread_marker_names_no_actor_it_does_not_have(
        monkeypatch):
    thread = [said(groomer.EXCLUDE_TAG, "DRE-112", "move to Cancel",
                   at="2026-09-04T18:00:00Z", pid="a4f198411b02")]
    after = build(monkeypatch, threads={EARLIER: thread})
    [dead] = cancel(after)
    assert dead["reason"] == recorded_reason("2026-09-04", "a4f198411b02",
                                             actor="you")


def test_the_cancel_row_reads_back_byte_for_byte_with_a_pipe_in_the_title(
        monkeypatch):
    lane = [c if c["identifier"] != "DRE-112"
            else card("DRE-112", days=12, title="split | the reader")
            for c in LANE]
    after = build(monkeypatch, lane, rows=[row("DRE-112", "cancel")])
    record = groomer.parse_proposal_comment(groomer.proposal_comment(after))
    assert record["cancel"] == [{"identifier": "DRE-112", "position": 1,
                                 "repo": "portico",
                                 "reason": recorded_reason()}]


def test_approving_the_proposal_cancels_the_card_with_that_reason(monkeypatch):
    after = build(monkeypatch, rows=[row("DRE-112", "cancel")])
    ops = FakeOps(comments=[
        {"body": groomer.proposal_comment(after), "authored_by_pipeline": True},
        {"body": groomer.approval_comment(after["id"]),
         "authored_by_pipeline": False}])
    groomer.drain(ops, card=DRAIN_CARD)
    assert ("DRE-112", "Canceled") in ops.state_writes
    assert ("DRE-112", groomer.cancelled_note(after["id"], recorded_reason())) \
        in ops.written
    assert ("DRE-112", "Planning") not in ops.state_writes


def test_an_epic_with_an_open_child_is_refused_and_named_as_your_move(
        monkeypatch):
    lane = [epic("DRE-112", days=12, child_state="In Progress")
            if c["identifier"] == "DRE-112" else c for c in LANE]
    after = build(monkeypatch, lane, rows=[row("DRE-112", "cancel")])
    assert "DRE-112" not in cancel_ids(after)
    assert [r["identifier"] for r in after["cancels_refused"]] == ["DRE-112"]
    assert after["cancels_refused"][0]["source"] == groomer.DEAD_FROM_RECORD
    page = groomer.render_proposal(after)
    refused = section(page, "## Cancels refused")
    line = [ln for ln in refused.splitlines() if "DRE-112" in ln]
    assert line and ("proposed for Cancel by your Move to Cancel "
                     "(`console-record`)") in line[0], refused


def test_an_epic_whose_children_are_all_closed_is_cancelled(monkeypatch):
    lane = [epic("DRE-112", days=12, child_state="Done")
            if c["identifier"] == "DRE-112" else c for c in LANE]
    after = build(monkeypatch, lane, rows=[row("DRE-112", "cancel")])
    assert cancel_ids(after) == ["DRE-112"]
    assert after["cancels_refused"] == []


def test_a_recorded_cancel_for_a_card_not_on_offer_is_not_shown(monkeypatch):
    lane = [c for c in LANE if c["identifier"] != "DRE-112"]
    gone = build(monkeypatch, lane, rows=[row("DRE-112", "cancel")])
    assert cancel_ids(gone) == []
    held_repo = [card("DRE-112", repo="atlas", days=12) if
                 c["identifier"] == "DRE-112" else c for c in LANE]
    held = build(monkeypatch, held_repo, rows=[row("DRE-112", "cancel")],
                 hold_repo=["atlas"])
    assert cancel_ids(held) == []


def test_a_ranked_read_verdict_does_not_change_the_recorded_reason():
    judgement = {"DRE-112": groom_judgement.Verdict(
        "likely-done", "the model thinks so", "superseded by DRE-150")}
    cancelled = [{"identifier": "DRE-112", "actor_name": "Sid",
                  "recorded_at": "2026-09-22T02:49:23Z",
                  "proposal_id": ROW_PID}]
    proposal = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW,
                               judgement=judgement, recorded_cancel=cancelled)
    [dead] = cancel(proposal)
    assert dead["source"] == groomer.DEAD_FROM_RECORD
    assert dead["reason"] == recorded_reason()
    assert dead["judged"] is False


def test_a_recorded_cancel_wins_over_a_superseded_by_line():
    lane = [card("DRE-112", days=12, description="Superseded by: DRE-150")
            if c["identifier"] == "DRE-112" else c for c in LANE]
    cancelled = [{"identifier": "DRE-112", "actor_name": "Sid",
                  "recorded_at": "2026-09-22T02:49:23Z",
                  "proposal_id": ROW_PID}]
    proposal = groomer.propose(lane, cycles=CYCLES, capacity=3, now=NOW,
                               recorded_cancel=cancelled)
    [dead] = cancel(proposal)
    assert dead["source"] == groomer.DEAD_FROM_RECORD
    assert dead["reason"] == recorded_reason()


# --------------------------------------------------------------------------
# the check before posting leaves a person's decision alone
# --------------------------------------------------------------------------
def _recorded(title):
    lane = [card("DRE-102", days=2, title=title)
            if c["identifier"] == "DRE-102" else c for c in LANE]
    cancelled = [{"identifier": "DRE-102", "actor_name": "Sid",
                  "recorded_at": "2026-09-22T02:49:23Z",
                  "proposal_id": ROW_PID}]
    return lane, cancelled


@pytest.mark.parametrize("title", ["a plain card",
                                   "superseded by DRE-150, see DRE-151"])
def test_the_check_writes_no_row_and_no_keep_for_a_recorded_cancel(title):
    lane, cancelled = _recorded(title)
    proposal = groomer.propose(lane, cycles=CYCLES, capacity=3, now=NOW,
                               recorded_cancel=cancelled)
    # A reason that happens to name a card is the case that could be kept.
    proposal["outcomes"]["dead"][0]["reason"] += " — DRE-150 is in Intake"
    result = groom_verify.check(proposal,
                                lops=FakeLinear(lanes={"DRE-150": "Intake"}),
                                run=FakeGh(), owners=OWNERS)
    assert "DRE-102" not in {r["identifier"] for r in result["cards"]}
    assert "DRE-102" not in result["keep"]
    assert "DRE-102" not in result["cancel"]


def test_a_verified_proposal_keeps_the_recorded_cancel_on_the_cancel_list():
    lane, cancelled = _recorded("superseded by DRE-150")
    final = groomer.verify_proposal(
        lane, dict(cycles=CYCLES, capacity=3, now=NOW,
                   recorded_cancel=cancelled),
        lops=FakeLinear(lanes={"DRE-150": "Intake"}), run=FakeGh(),
        owners=OWNERS)
    assert cancel_ids(final) == ["DRE-102"]
    assert cancel(final)[0]["reason"] == recorded_reason()
    assert "DRE-102" not in final["verification"]["cancels_rejected"]
    assert "DRE-102" not in {r["identifier"]
                             for r in final["verification"]["cards"]}


# --------------------------------------------------------------------------
# the doc and the module say it
# --------------------------------------------------------------------------
def test_the_doc_carries_the_memory_section():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    heading = re.search(r"^#{2,3} The memory\b.*$", doc, re.M)
    assert heading, "docs/groomer.md has no memory section"
    body = doc[heading.end():]
    body = re.split(r"^#{1,3} ", body, maxsplit=1, flags=re.M)[0]
    for needle in ("groom_memory.standing_cards", "read_memory",
                   "rotation", "not-doing", "cancel", "hold", "put-back",
                   "fallback", "Hold until", "Move to Cancel",
                   groomer.DEAD_FROM_RECORD):
        assert needle in body, f"the memory section never says {needle!r}"


def test_the_module_says_the_recorded_cancel_is_placed_wherever_it_stands():
    """Since DRE-7973 every Cancel row is; the recorded one was first."""
    doc = groomer.__doc__
    para = doc.split("**Proposes.**", 1)[1]
    para = para.split("\n\n", 1)[0]
    assert "Move to Cancel" in para, para
    assert "wherever it" in para, para
