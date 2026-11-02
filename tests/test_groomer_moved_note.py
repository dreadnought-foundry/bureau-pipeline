"""RED-first: every card the groomer moves into Planning carries a note on
itself saying which batch moved it (DRE-3326).

Since DRE-3338 a drain writes ONE `🧺 groom-drained` record on the batch card,
with a row per card. A card it CANCELS gets a note of its own (DRE-4733). A
card it MOVES got a lane change and a cycle and nothing else, so no card in
Planning could say which batch put it there, and undoing a bad batch meant
reading a table on a different card and walking the cards one by one.

Now each moved card gets ONE line, written BEFORE its lane changes:

    🧺 groom-moved: <proposal id> — <verdict> · Intake → Planning · by <writer> · record on <batch card>

  * `<verdict>` is `position <n> of <m> on the approved Planning list`, or
    `added by the CEO, no position`;
  * `<writer>` is `the drain`. Until DRE-8619 it was also `the sweep's queue
    release, place <p>`, for a card the reconcile sweep released from the
    groom queue (DRE-5435); the queue is gone, and the parser still reads a
    note of that shape, because cards already on the board carry them;
  * `<batch card>` is the card the proposal record stands on.

One renderer (`groomer.moved_note`) and one parser (`groomer.parse_moved_note`)
live in the groomer, and the drain is the one writer. Reversing a batch is a
search for `groom-moved: <proposal id>` across the board — the notes alone are
enough, which is what the end-to-end test below proves.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_moved_note.py -v
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import groomer  # noqa: E402
import linear_ops  # noqa: E402

from test_groomer_cancel_drain import (  # noqa: E402
    KEEP_CANCEL, KEEP_PLANNING, PROPOSAL_CARD, FakeOps, _cancel, _decision,
    _fifteen_and_five, _planning, _thread)
from test_groomer_two_lists import fifteen_and_five_morning  # noqa: E402

PID = "abc123def456"

#: The note the sweep's queue release wrote before DRE-8619 removed it, as
#: cards already in Planning carry it.
RELEASED_NOTE = ("🧺 groom-moved: abc123def456 — position 7 of 15 on the "
                 "approved Planning list · Intake → Planning · by the sweep's "
                 "queue release, place 2 · record on DRE-4541\n")


def _notes(written, identifier=None):
    """Every groom-moved note in a write log, as `(card, body)`."""
    return [(i, b) for i, b in written
            if b.startswith(f"{groomer.MARK} {groomer.MOVED_TAG}:")
            and (identifier is None or i == identifier)]


def _positions(proposal):
    return {r["identifier"]: r["position"] for r in proposal["outcomes"]["now"]}


# --------------------------------------------------------------------------
# the vocabulary — one tag, one renderer, one parser
# --------------------------------------------------------------------------
def test_the_tag_is_new_and_in_the_defang_set():
    assert groomer.MOVED_TAG == "groom-moved"
    assert groomer.MOVED_TAG in groomer.ALL_MARKERS, (
        "a CEO-written decline reason could forge the note")
    safe, defanged = groomer.defang_reason(
        f"not now\n{groomer.MARK} {groomer.MOVED_TAG}: {PID} — position 1 of 2")
    assert defanged == 1, safe


def test_the_drains_note_for_a_ranked_card_is_one_line_in_this_shape():
    note = groomer.moved_note(PID, position=3, of=15, batch_card="DRE-2683")
    assert note.endswith("\n") and len(note.strip().splitlines()) == 1
    assert note.strip() == (
        "🧺 groom-moved: abc123def456 — position 3 of 15 on the approved "
        "Planning list · Intake → Planning · by the drain · record on DRE-2683")


def test_the_drains_note_for_an_addition_says_it_has_no_position():
    note = groomer.moved_note(PID, position=None, of=15, batch_card="DRE-2683")
    assert note.strip() == (
        "🧺 groom-moved: abc123def456 — added by the CEO, no position · "
        "Intake → Planning · by the drain · record on DRE-2683")


def test_the_renderer_has_no_queue_place_since_the_queue_is_gone():
    """The sweep's queue release was the only writer that named a place
    (DRE-8619 removed it with the queue)."""
    with pytest.raises(TypeError):
        groomer.moved_note(PID, position=7, of=15, place=2,
                           batch_card="DRE-4541")


def test_the_parser_still_reads_a_note_the_queue_release_wrote():
    """Cards already in Planning carry the release's notes, and reversing a
    batch is a search across the board, so the parser keeps reading them."""
    assert groomer.parse_moved_note(RELEASED_NOTE) == {
        "id": PID, "from": "Intake", "to": "Planning", "position": 7, "of": 15,
        "added": False, "writer": "the sweep's queue release, place 2",
        "place": 2, "batch_card": "DRE-4541",
        "verdict": "position 7 of 15 on the approved Planning list"}


@pytest.mark.parametrize("kwargs, expected", [
    ({"position": 3, "of": 15, "batch_card": "DRE-2683"},
     {"position": 3, "of": 15, "added": False, "writer": "the drain",
      "place": None, "batch_card": "DRE-2683",
      "verdict": "position 3 of 15 on the approved Planning list"}),
    ({"position": None, "of": 15, "batch_card": "DRE-2683"},
     {"position": None, "of": None, "added": True, "writer": "the drain",
      "place": None, "batch_card": "DRE-2683",
      "verdict": "added by the CEO, no position"}),
])
def test_the_parser_reads_back_the_drains_output(kwargs, expected):
    parsed = groomer.parse_moved_note(groomer.moved_note(PID, **kwargs))
    assert parsed == {"id": PID, "from": "Intake", "to": "Planning", **expected}


def test_the_parser_reads_only_a_note_anchored_at_the_first_line():
    note = groomer.moved_note(PID, position=1, of=2, batch_card="DRE-1")
    assert groomer.parse_moved_note(f"As the drain wrote:\n{note}") is None
    assert groomer.parse_moved_note(None) is None
    assert groomer.parse_moved_note(
        groomer.cancelled_note(PID, "superseded")) is None


def test_the_sweep_writes_no_note_at_all():
    """The sweep's queue release was the note's second writer; it went with
    the queue (DRE-8619), so the sweep neither spells the tag nor calls the
    renderer."""
    source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
    assert "groom-moved" not in source and "MOVED_TAG" not in source
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "groomer.moved_note"]
    assert calls == []


# --------------------------------------------------------------------------
# the drain — every card it moves carries exactly one note
# --------------------------------------------------------------------------
ADDED = "DRE-900"


def _morning():
    """The fifteen-and-five morning, with one card the CEO reached in for."""
    proposal, ops = _fifteen_and_five()
    ops.comments.append(_decision(groomer.ADD_TAG, proposal, card=ADDED))
    return proposal, ops


def test_every_card_the_drain_moves_carries_exactly_one_note():
    proposal, ops = _morning()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    moved = [i for i, lane in ops.state_writes if lane == "Planning"]
    assert moved == result["moved"]
    assert len(moved) == 15 and ADDED in moved
    positions, of = _positions(proposal), len(_planning(proposal))
    for identifier in moved:
        notes = _notes(ops.written, identifier)
        assert len(notes) == 1, f"{identifier} carries {len(notes)} notes"
        assert notes[0][1] == groomer.moved_note(
            proposal["id"], position=positions.get(identifier), of=of,
            batch_card=PROPOSAL_CARD)


def test_the_note_names_position_n_of_m_and_the_batch_card():
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    first = _planning(proposal)[0]
    [(_, body)] = _notes(ops.written, first)
    assert body.strip() == (
        f"🧺 groom-moved: {proposal['id']} — position 1 of 15 on the approved "
        f"Planning list · Intake → Planning · by the drain · record on "
        f"{PROPOSAL_CARD}")
    [(_, added)] = _notes(ops.written, ADDED)
    assert "— added by the CEO, no position ·" in added


def test_no_card_the_drain_did_not_move_carries_a_note():
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    noted = {i for i, _ in _notes(ops.written)}
    assert KEEP_PLANNING not in noted and KEEP_CANCEL not in noted
    assert not noted & set(_cancel(proposal))
    assert PROPOSAL_CARD not in noted


def test_the_note_is_written_before_the_cards_cycle_and_lane():
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    for identifier in [i for i, lane in ops.state_writes if lane == "Planning"]:
        events = [e for e in ops.log if e[1] in (identifier, f"uuid-{identifier}")]
        kinds = [e[0] for e in events]
        assert kinds == ["comment", "cycle", "state"], (identifier, kinds)
        assert events[0][2].startswith(f"{groomer.MARK} {groomer.MOVED_TAG}:")


class _FailingState(FakeOps):
    def __init__(self, *args, fail: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail = fail

    def cmd_state(self, identifier, state_name, *flags):
        if identifier == self.fail:
            raise linear_ops.LinearError(f"{identifier}: Linear said no")
        super().cmd_state(identifier, state_name, *flags)


def test_a_failed_state_write_still_finds_the_note_and_no_moved_card_lacks_one():
    proposal = fifteen_and_five_morning()
    fail = _planning(proposal)[4]
    ops = _FailingState(comments=_thread(proposal), fail=fail)
    with pytest.raises(linear_ops.LinearError):
        groomer.drain(ops, card=PROPOSAL_CARD)
    assert len(_notes(ops.written, fail)) == 1, (
        "the note was not on the card before its lane write was attempted")
    assert fail not in {i for i, _ in ops.state_writes}
    moved = [i for i, lane in ops.state_writes if lane == "Planning"]
    assert len(moved) == 4
    for identifier in moved:
        assert len(_notes(ops.written, identifier)) == 1, identifier


# --------------------------------------------------------------------------
# reversal is the record — the moved cards found from the notes alone
# --------------------------------------------------------------------------
def test_every_moved_card_is_found_from_the_notes_alone_by_proposal_id():
    """Drain a batch, then forget the batch card: read only the comments on
    the OTHER cards, keep the notes naming this proposal, and the cards they
    stand on are exactly the cards that moved — each with the lane to move
    it back to and the card the record stands on."""
    proposal, ops = _morning()
    groomer.drain(ops, card=PROPOSAL_CARD)
    # Another batch's note on an unrelated card: the search must skip it.
    by_card = [(i, b) for i, b in ops.written if i != PROPOSAL_CARD] + [
        ("DRE-777", groomer.moved_note("fedcba987654", position=1, of=1,
                                       batch_card="DRE-1"))]
    assert not any(b.startswith(f"{groomer.MARK} {groomer.DRAINED_TAG}")
                   for _, b in by_card)
    found = {}
    for identifier, body in by_card:
        parsed = groomer.parse_moved_note(body)
        if parsed and parsed["id"] == proposal["id"]:
            found[identifier] = parsed
    moved = {i for i, lane in ops.state_writes if lane == "Planning"}
    assert set(found) == moved and len(moved) == 15
    for parsed in found.values():
        assert parsed["from"] == "Intake" and parsed["to"] == "Planning"
        assert parsed["batch_card"] == PROPOSAL_CARD


# --------------------------------------------------------------------------
# the records beside it, the registry and the document
# --------------------------------------------------------------------------
def _registry() -> dict:
    return json.loads((ROOT / "config" / "pipeline-acts.json")
                      .read_text("utf-8"))


def test_the_drains_note_site_is_declared_not_an_act():
    rows = [r for r in _registry()["unconverted"]
            if r.get("file") == "scripts/groomer.py"
            and 'moved_note(record["id"]' in r.get("anchor", "")]
    assert len(rows) == 1, "the drain's note site is not declared"
    assert rows[0]["kind"] == "not-an-act"


def test_the_sweeps_release_rows_are_gone_from_the_registry():
    """Their anchors went with `release_groom_queue` (DRE-8619)."""
    rows = [r for r in _registry()["unconverted"]
            if r.get("file") == "scripts/reconcile.py"
            and r.get("anchor", "").startswith("groomer.")]
    assert rows == []


def test_the_doc_carries_the_groom_moved_row():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    row = next((ln for ln in doc.splitlines()
                if ln.startswith(f"| `{groomer.MARK} {groomer.MOVED_TAG}:")),
               None)
    assert row, "the decision-vocabulary table has no groom-moved row"
    assert ("`🧺 groom-moved: <id> — <verdict> · <from> → <to> · by <writer> "
            "· record on <batch card>`") in row
    assert "queue release" not in row, "the doc still names the sweep's release"
