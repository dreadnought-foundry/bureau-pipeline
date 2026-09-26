"""RED-first: the drain executes both lists the CEO agreed (DRE-4733).

Since DRE-4727 a proposal is two lists drawn from the same twenty: the Planning
list, and the Cancel list with a one-line reason on every row. The drain moved
the first and ignored the second, so a card the CEO agreed should be cancelled
stayed in Intake until somebody cancelled it by hand. Now:

  * an agreed Planning card moves to `Planning`, on the cycle the record named,
    exactly as before;
  * an agreed Cancel card gets its reason written on it as
    `🧺 groom-cancelled: <proposal id> — <reason>`, and THEN moves to
    `Canceled` — never `Done`, and never onto a cycle;
  * a card the CEO excluded, on either list, stays in Intake with no write;
  * a card that has already left Intake since the proposal is an `already
    gone` row naming the lane it is in now, and the rest of the batch still
    moves. That used to refuse the WHOLE batch — on 2026-09-16 two cards that
    had moved on (DRE-3453 Done, DRE-3127 Canceled) cost the CEO a second
    approval of a batch that was otherwise right.

The summary line is a wire contract with the console (DRE-4682 in
agent-bureau), which reads the four clauses it always has plus one optional
`cancelled:` clause before `refused:`. `already gone` is a row, never a clause.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_cancel_drain.py -v
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402
import lane_contract  # noqa: E402
import ready_lane_writers  # noqa: E402

from test_groomer_approval_gate import FakeOps as _GateOps  # noqa: E402
from test_groomer import CYCLES, NOW, card  # noqa: E402
from test_groomer_two_lists import TWO_LISTS_RENDER, two_lists  # noqa: E402

PROPOSAL_CARD = "DRE-2683"

#: The console's reader of the drained record's summary line — DRE-4682 in
#: agent-bureau parses exactly this grammar: today's four clauses in today's
#: order and spelling, plus the `cancelled:` clause before `refused:`, and
#: nothing else. Mirrored here so a change to the line is a change to its
#: reader, made on purpose.
DRE_4682_SUMMARY = re.compile(
    r"^moved: \d+ · held back: \d+ · added: \d+ · cancelled: \d+ · "
    r"refused: \d+ → Planning at .+$")

#: The Pacific time the drain stamps on its summary line (`dead_run.pacific`).
_PT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} PT$")

#: Every summary line any test in this file made the drain write, checked
#: against the reader's grammar once the module's tests have run.
_SUMMARIES: list[str] = []


class FakeOps(_GateOps):
    """The approval-gate fixture, plus ONE log of every write in the order the
    drain made it — the order is part of the contract: a cancelled card's
    reason is on it before its lane changes, so a crash between the two
    leaves a card in Intake carrying a note that names the batch."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log: list[tuple] = []

    def gql(self, query, variables=None):
        if "cycles(" not in query:
            self.log.append(("cycle", (variables or {}).get("id")))
        return super().gql(query, variables)

    def cmd_state(self, identifier, state_name, *flags):
        self.log.append(("state", identifier, state_name))
        super().cmd_state(identifier, state_name, *flags)

    def cmd_comment(self, identifier, body, *flags):
        self.log.append(("comment", identifier, body))
        super().cmd_comment(identifier, body, *flags)
        if body.startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}:"):
            _SUMMARIES.extend(ln for ln in body.splitlines()
                              if ln.startswith("moved: "))


# --------------------------------------------------------------------------
# fixtures — the fifteen-and-five morning DRE-4727 proposes
# --------------------------------------------------------------------------
def _record(proposal):
    return {"body": groomer.proposal_comment(proposal),
            "authored_by_pipeline": True}


def _decision(tag, proposal, *, card=None, reason=None, by_pipeline=False):
    return {"body": groomer.decision_comment(
                tag, groomer.proposal_id(proposal), card=card, reason=reason),
            "authored_by_pipeline": by_pipeline}


def _thread(proposal, *decisions):
    return [_record(proposal), _decision(groomer.APPROVAL_TAG, proposal),
            *decisions]


def _ids(rows):
    return [r["identifier"] for r in sorted(rows, key=lambda r: r["position"])]


def _planning(proposal):
    return _ids(proposal["outcomes"]["now"])


def _cancel(proposal):
    return _ids(proposal["outcomes"]["dead"])


def _reasons(proposal):
    return {r["identifier"]: r["reason"] for r in proposal["outcomes"]["dead"]}


def _note(proposal, identifier):
    return (f"{groomer.MARK} {groomer.CANCELLED_TAG}: {proposal['id']} — "
            f"{_reasons(proposal)[identifier]}")


def _row(body, identifier):
    return next((ln for ln in body.splitlines()
                 if re.match(rf"^\|[^|]*\|\s*{identifier}\s*\|", ln.strip())), None)


def _outcome(body, identifier):
    row = _row(body, identifier)
    assert row, f"{identifier} has no row in the drain's record:\n{body}"
    return [c.strip() for c in row.strip().strip("|").split("|")][2]


def _drained(ops):
    records = [b for t, b in ops.written
               if t == PROPOSAL_CARD
               and b.startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}:")]
    assert len(records) == 1, f"the drain wrote {len(records)} drained records"
    return records[0]


def _summary(body):
    return next(ln for ln in body.splitlines() if ln.startswith("moved: "))


#: The two the CEO said no to, one on each list.
KEEP_PLANNING, KEEP_CANCEL = "DRE-2", "DRE-8"


def _fifteen_and_five(**lanes):
    proposal = two_lists()
    assert len(_planning(proposal)) == 15 and len(_cancel(proposal)) == 5, (
        "the fixture must be the fifteen-and-five morning for this to prove "
        "anything")
    ops = FakeOps(comments=_thread(
        proposal,
        _decision(groomer.EXCLUDE_TAG, proposal, card=KEEP_PLANNING,
                  reason="design first"),
        _decision(groomer.EXCLUDE_TAG, proposal, card=KEEP_CANCEL,
                  reason="still wanted")), lanes=lanes)
    return proposal, ops


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------
def test_both_destinations_are_module_constants():
    """Module constants, because `ready_lane_writers.py` attributes a write
    by reading constants and parameter defaults — never a local."""
    assert groomer.DRAIN_TO == "Planning"
    assert groomer.CANCEL_TO == "Canceled"
    assert groomer.NEVER_WRITES == ("Duplicate", "Done")


def test_the_cancelled_note_is_one_line_in_its_own_marker():
    assert groomer.CANCELLED_TAG == "groom-cancelled"
    assert groomer.CANCELLED_TAG in groomer.ALL_MARKERS, (
        "the defang set does not know the new marker")
    note = groomer.cancelled_note("abc123def456", "superseded by DRE-900")
    assert note.strip() == \
        "🧺 groom-cancelled: abc123def456 — superseded by DRE-900"
    assert len(note.strip().splitlines()) == 1


def test_the_six_outcomes():
    assert groomer.DRAIN_OUTCOMES == (
        "moved", "held back", "added", "cancelled", "refused", "already gone")


# --------------------------------------------------------------------------
# reading the Cancel table back
# --------------------------------------------------------------------------
def test_the_cancel_table_is_read_back_from_the_two_lists_fixture():
    """DRE-4727's committed copy of the wire contract, read — never edited —
    with every reason intact, the escaped pipe included."""
    text = TWO_LISTS_RENDER.read_text(encoding="utf-8")
    _, sep, body = text.partition("-->\n\n")
    assert sep, "the fixture opens with its header comment"
    record = groomer.parse_proposal_comment(body)
    assert record["id"] == "3734d5abc032"
    assert [r["identifier"] for r in record["batch"]] == [
        "DRE-4103", "DRE-4101", "DRE-4104"]
    assert record["cancel"] == [
        {"identifier": "DRE-4102", "position": 1, "repo": "portico",
         "reason": "superseded by DRE-4250"},
        {"identifier": "DRE-4105", "position": 2, "repo": "agent-bureau",
         "reason": "the cutover ran on 2026-09-07 | the probe is the proof"},
    ]


def test_a_proposal_with_no_cancel_section_reads_an_empty_cancel_list():
    cards = [card(f"DRE-{n:03d}") for n in range(6)]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=3, batch_cycles=1)
    assert groomer.CANCEL_HEADING not in groomer.proposal_comment(proposal)
    record = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert record["cancel"] == []


def test_the_cancel_table_round_trips_from_the_render():
    proposal = two_lists()
    record = groomer.parse_proposal_comment(groomer.proposal_comment(proposal))
    assert [r["identifier"] for r in record["cancel"]] == _cancel(proposal)
    assert [r["position"] for r in record["cancel"]] == [1, 2, 3, 4, 5]
    assert {r["identifier"]: r["reason"] for r in record["cancel"]} == \
        _reasons(proposal)


def test_a_title_carrying_a_pipe_does_not_move_the_reason():
    """The reader splits on an UNESCAPED pipe, so an escaped one in a title
    stays in the title and the reason is still the seventh cell."""
    body = "\n".join([
        f"{groomer.MARK} {groomer.PROPOSAL_TAG}: abc123def456", "",
        "# Groom proposal `abc123def456` — cycle 12", "",
        groomer.CANCEL_HEADING, "", groomer.CANCEL_COLUMNS,
        "| -- | -- | -- | -- | -- | -- | -- |",
        r"| 1 | DRE-7 | — | portico | — | a \| b | done in PR 9 \| twice |", ""])
    record = groomer.parse_proposal_comment(body)
    assert record["cancel"] == [{"identifier": "DRE-7", "position": 1,
                                 "repo": "portico",
                                 "reason": "done in PR 9 | twice"}]


# --------------------------------------------------------------------------
# the fifteen-and-five morning, end to end
# --------------------------------------------------------------------------
def test_the_agreed_cards_move_to_planning_and_canceled_and_nothing_to_done():
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    planning = [i for i in _planning(proposal) if i != KEEP_PLANNING]
    cancel = [i for i in _cancel(proposal) if i != KEEP_CANCEL]
    assert len(planning) == 14 and len(cancel) == 4
    assert ops.state_writes == ([(i, "Planning") for i in planning]
                                + [(i, "Canceled") for i in cancel])
    assert "Done" not in {lane for _, lane in ops.state_writes}
    notes = [(t, b) for t, b in ops.written if t != PROPOSAL_CARD]
    assert notes == [(i, groomer.cancelled_note(proposal["id"],
                                                _reasons(proposal)[i]))
                     for i in cancel]
    for i in cancel:
        assert notes[cancel.index(i)][1].strip() == _note(proposal, i)


def test_the_excluded_card_on_either_list_gets_no_write_of_any_kind():
    proposal, ops = _fifteen_and_five()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    for kept in (KEEP_PLANNING, KEEP_CANCEL):
        assert not [e for e in ops.log if e[1] == kept or
                    e[1] == f"uuid-{kept}"], f"the drain wrote on {kept}"
    assert sorted(result["held_back"]) == sorted([KEEP_PLANNING, KEEP_CANCEL])
    body = _drained(ops)
    assert _outcome(body, KEEP_PLANNING) == "held back"
    assert _outcome(body, KEEP_CANCEL) == "held back"
    assert "still wanted" in _row(body, KEEP_CANCEL)


def test_the_writes_happen_in_the_stated_order():
    """Planning cards first — cycle, then lane, per card, in the record's
    order — then each Cancel card: its note, then its lane, and no cycle.
    The drained record is the last write of all."""
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    expected = []
    for i in _planning(proposal):
        if i != KEEP_PLANNING:
            expected += [("cycle", f"uuid-{i}"), ("state", i, "Planning")]
    for i in _cancel(proposal):
        if i != KEEP_CANCEL:
            expected += [("comment", i, groomer.cancelled_note(
                             proposal["id"], _reasons(proposal)[i])),
                         ("state", i, "Canceled")]
    assert ops.log[:-1] == expected
    assert ops.log[-1][:2] == ("comment", PROPOSAL_CARD)
    assert ops.log[-1][2].startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}:")


def test_a_cancelled_card_is_never_given_a_cycle():
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    cycled = {variables["id"] for _, variables in ops.mutations}
    for i in _cancel(proposal):
        assert f"uuid-{i}" not in cycled, f"{i} was filed into a cycle"
    assert len(cycled) == 14
    assert sum(1 for _, lane in ops.state_writes if lane == "Canceled") == 4, (
        "no Cancel card moved, so this proves nothing about their cycles")


def test_the_summary_line_is_the_one_dre_4682_reads_byte_for_byte():
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    line = _summary(_drained(ops))
    head, sep, when = line.partition(" → Planning at ")
    assert sep, line
    assert head == ("moved: 14 · held back: 2 · added: 0 · cancelled: 4 · "
                    "refused: 0")
    assert _PT.match(when), f"only the time may vary: {when!r}"
    assert DRE_4682_SUMMARY.match(line)


def test_every_row_is_one_of_the_six_and_every_card_on_either_list_has_one():
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    body = _drained(ops)
    for i in _planning(proposal) + _cancel(proposal):
        assert _outcome(body, i) in groomer.DRAIN_OUTCOMES
        assert sum(1 for ln in body.splitlines()
                   if _row(ln, i)) == 1, f"{i} has more than one row"
    for i in _cancel(proposal):
        if i != KEEP_CANCEL:
            assert _outcome(body, i) == "cancelled"


def test_the_result_names_the_cancelled_cards():
    proposal, ops = _fifteen_and_five()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["cancelled"] == [i for i in _cancel(proposal)
                                   if i != KEEP_CANCEL]
    assert result["moved"] == [i for i in _planning(proposal)
                               if i != KEEP_PLANNING]


# --------------------------------------------------------------------------
# a card that already left — reported, never a reason to refuse the rest
# --------------------------------------------------------------------------
def test_a_card_already_gone_on_either_list_gets_its_row_and_the_rest_moves():
    """The 2026-09-16 refusal, turned round: one Planning card is Done and one
    Cancel card is already Canceled. Neither is written to, each is an
    `already gone` row naming its lane, neither is counted on any clause, and
    everything else the CEO agreed still moves."""
    gone_planning, gone_cancel = "DRE-4", "DRE-12"
    proposal, ops = _fifteen_and_five(**{gone_planning: "Done",
                                         gone_cancel: "Canceled"})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    for gone in (gone_planning, gone_cancel):
        assert not [e for e in ops.log
                    if e[1] in (gone, f"uuid-{gone}")], f"wrote on {gone}"
    body = _drained(ops)
    assert _outcome(body, gone_planning) == "already gone"
    assert "Done" in _row(body, gone_planning)
    assert _outcome(body, gone_cancel) == "already gone"
    assert "Canceled" in _row(body, gone_cancel)
    head = _summary(body).partition(" → ")[0]
    assert head == ("moved: 13 · held back: 2 · added: 0 · cancelled: 3 · "
                    "refused: 0")
    assert "already gone" not in _summary(body)
    assert len(result["moved"]) == 13 and len(result["cancelled"]) == 3
    assert not any(b.startswith(f"{groomer.MARK} {groomer.DRAIN_REFUSED_TAG}")
                   for _, b in ops.written)


def test_an_addition_that_already_left_is_already_gone_and_the_rest_moves():
    proposal = two_lists()
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card="DRE-900")),
        lanes={"DRE-900": "In Progress"})
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-900" not in {i for i, _ in ops.state_writes}
    assert result["moved"] == _planning(proposal)
    body = _drained(ops)
    assert _outcome(body, "DRE-900") == "already gone"
    assert "In Progress" in _row(body, "DRE-900")


def test_not_in_lane_is_gone():
    assert not hasattr(groomer, "NotInLane"), (
        "the whole-batch refusal for a card that moved on is still defined")


# --------------------------------------------------------------------------
# the other decisions, applied to the Cancel list
# --------------------------------------------------------------------------
def test_a_held_repo_holds_back_a_card_on_the_cancel_list():
    cards = [card("DRE-1", days=9), card("DRE-2", days=8, repo="atlas",
                                         description="Superseded by: DRE-900"),
             card("DRE-3", days=7, description="Superseded by: DRE-901")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, batch_cycles=1,
                               now=NOW)
    assert _cancel(proposal) == ["DRE-2", "DRE-3"]
    hold = {"body": f"{groomer.MARK} {groomer.REPO_HOLD_TAG}: atlas",
            "authored_by_pipeline": False}
    ops = FakeOps(comments=_thread(proposal) + [hold])
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert "DRE-2" not in {i for i, _ in ops.state_writes}
    assert ("DRE-3", "Canceled") in ops.state_writes
    body = _drained(ops)
    assert _outcome(body, "DRE-2") == "held back"
    assert "repo held: atlas" in _row(body, "DRE-2")


def test_an_addition_only_ever_reaches_the_planning_list():
    """An addition naming a card on the Cancel list is the CEO's newest word
    about that card, and it says Planning — the card is not cancelled."""
    proposal = two_lists()
    target = _cancel(proposal)[0]
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.ADD_TAG, proposal, card=target)))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert (target, "Canceled") not in ops.state_writes
    assert (target, "Planning") in ops.state_writes
    assert result["added"] == [target]
    assert not [b for t, b in ops.written if t == target]


def test_an_exclusion_naming_a_card_on_neither_list_is_refused():
    proposal = two_lists()
    ops = FakeOps(comments=_thread(
        proposal, _decision(groomer.EXCLUDE_TAG, proposal, card="DRE-900")))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert len(result["refused"]) == 1
    assert _outcome(_drained(ops), "DRE-900") == "refused"


def test_a_proposal_with_only_a_cancel_list_cancels_and_assigns_no_cycle():
    cards = [card("DRE-1", days=9, description="Superseded by: DRE-900")]
    proposal = groomer.propose(cards, cycles=CYCLES, capacity=5, batch_cycles=1,
                               now=NOW)
    assert _planning(proposal) == [] and _cancel(proposal) == ["DRE-1"]
    ops = FakeOps(comments=_thread(proposal))
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert ops.state_writes == [("DRE-1", "Canceled")]
    assert ops.mutations == []
    assert result["cancelled"] == ["DRE-1"]


# --------------------------------------------------------------------------
# the drain never closes, and never cancels the Planning list
# --------------------------------------------------------------------------
def _unread(*args, **kwargs):                   # pragma: no cover - the point
    raise AssertionError("the drain read the card before refusing")


@pytest.mark.parametrize("lane", ["Done", "Duplicate"])
def test_a_closing_destination_is_refused_before_the_card_is_read(lane):
    proposal = two_lists()
    ops = FakeOps(comments=_thread(proposal))
    ops.comment_records = _unread
    ops.get_issue = _unread
    with pytest.raises(groomer.WillNotClose):
        groomer.drain(ops, card=PROPOSAL_CARD, to=lane)
    assert ops.log == []


def test_the_planning_list_is_never_sent_to_canceled():
    """Canceled is written ONLY for a card on the agreed Cancel table, so the
    Planning list's destination can never be it."""
    proposal = two_lists()
    ops = FakeOps(comments=_thread(proposal))
    ops.comment_records = _unread
    with pytest.raises(groomer.WillNotClose):
        groomer.drain(ops, card=PROPOSAL_CARD, to=groomer.CANCEL_TO)
    assert ops.log == []


def test_will_not_cancel_is_renamed():
    assert not hasattr(groomer, "WillNotCancel")


# --------------------------------------------------------------------------
# the writer checks — the Canceled write is attributed, the note is declared
# --------------------------------------------------------------------------
def test_the_lane_contract_lets_the_groomer_write_canceled():
    assert "groomer.py" in lane_contract.lane_writers("Canceled")


def test_the_canceled_write_is_attributed_to_the_groomer():
    found = [w for w in ready_lane_writers.writes()
             if w.where.startswith("scripts/groomer.py:")]
    assert {(w.writer, w.lane) for w in found} == {
        ("groomer.py", "Planning"), ("groomer.py", "Canceled")}
    canceled = [w for w in found if w.lane == "Canceled"]
    assert [w.expression for w in canceled] == ["CANCEL_TO"], (
        "the Canceled write must name the module constant at the call site")


def test_the_canceled_write_is_its_own_call_site_naming_the_constant():
    tree = ast.parse((ROOT / "scripts" / "groomer.py").read_text("utf-8"))
    targets = [ast.unparse(node.args[1]) for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and ast.unparse(node.func).endswith("cmd_state")
               and len(node.args) > 1]
    assert targets.count("CANCEL_TO") == 1
    assert "destination" not in targets


def test_the_note_is_declared_not_an_act_beside_the_drained_record():
    registry = json.loads((ROOT / "config" / "pipeline-acts.json")
                          .read_text("utf-8"))
    rows = [r for r in registry["unconverted"]
            if r.get("file") == "scripts/groomer.py"
            and "cancelled_note(" in r.get("anchor", "")]
    assert len(rows) == 1
    assert rows[0]["kind"] == "not-an-act"
    assert not [a for a in json.dumps(registry["acts"]).split('"')
                if "cancelled_note" in a]


def test_the_act_receipt_guard_accepts_the_note():
    done = subprocess.run([sys.executable, "scripts/check_act_receipts.py"],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
def test_the_doc_carries_the_cancelled_marker_and_the_five_clause_line():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    assert f"`{groomer.MARK} {groomer.CANCELLED_TAG}: <id> — <reason>`" in doc
    assert ("moved: n · held back: n · added: n · cancelled: n · refused: n "
            "→ Planning at <time PT>") in doc
    assert "DRE-4682" in doc
    for outcome in groomer.DRAIN_OUTCOMES:
        assert f"`{outcome}`" in doc, f"the doc does not name {outcome!r}"


# --------------------------------------------------------------------------
# every summary line this file produced, against the reader's grammar
# --------------------------------------------------------------------------
def test_zz_every_summary_line_matches_the_console_readers_grammar():
    """Last in the file on purpose: it reads every summary line the tests
    above made the drain write. The regex is DRE-4682's reader, mirrored."""
    proposal, ops = _fifteen_and_five()
    groomer.drain(ops, card=PROPOSAL_CARD)
    assert _SUMMARIES
    for line in _SUMMARIES:
        assert DRE_4682_SUMMARY.match(line), line
