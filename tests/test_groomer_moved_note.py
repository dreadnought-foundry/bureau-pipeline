"""RED-first: every card the groomer moves into Planning carries a note on
itself saying which batch moved it (DRE-3326).

Since DRE-3338 a drain writes ONE `🧺 groom-drained` record on the batch card,
with a row per card. A card it CANCELS gets a note of its own (DRE-4733). A
card it MOVES got a lane change and a cycle and nothing else — and so did a
card the reconcile sweep released from the groom queue (DRE-5435). So no card
in Planning could say which batch put it there, and undoing a bad batch meant
reading a table on a different card and walking the cards one by one.

Now each moved card gets ONE line, written BEFORE its lane changes:

    🧺 groom-moved: <proposal id> — <verdict> · Intake → Planning · by <writer> · record on <batch card>

  * `<verdict>` is `position <n> of <m> on the approved Planning list`, or
    `added by the CEO, no position`;
  * `<writer>` is `the drain`, or `the sweep's queue release, place <p>`;
  * `<batch card>` is the card the proposal record stands on.

One renderer (`groomer.moved_note`) and one parser (`groomer.parse_moved_note`)
live in the groomer, and both writers call the renderer. Reversing a batch is a
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
import reconcile  # noqa: E402

from test_groomer_cancel_drain import (  # noqa: E402
    KEEP_CANCEL, KEEP_PLANNING, PROPOSAL_CARD, FakeOps, _cancel, _decision,
    _fifteen_and_five, _planning, _thread)
from test_groomer_two_lists import two_lists  # noqa: E402
from test_groom_queue import (  # noqa: E402
    STANDING, Queue, _card, _pin, _release)

PID = "abc123def456"

_pin = _pin  # the queue suite's autouse fixture, applied here too


@pytest.fixture(autouse=True)
def _label_writes(monkeypatch):
    """A queued card's one write is its label; the drain fixture has none."""
    def add_label(self, identifier, label_name):
        self.log.append(("label", identifier, label_name))
    monkeypatch.setattr(FakeOps, "add_label", add_label, raising=False)


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


def test_the_releases_note_names_the_sweep_and_its_queue_place():
    note = groomer.moved_note(PID, position=7, of=15, place=2,
                              batch_card="DRE-4541")
    assert note.strip() == (
        "🧺 groom-moved: abc123def456 — position 7 of 15 on the approved "
        "Planning list · Intake → Planning · by the sweep's queue release, "
        "place 2 · record on DRE-4541")


@pytest.mark.parametrize("kwargs, expected", [
    ({"position": 3, "of": 15, "batch_card": "DRE-2683"},
     {"position": 3, "of": 15, "added": False, "writer": "the drain",
      "place": None, "batch_card": "DRE-2683",
      "verdict": "position 3 of 15 on the approved Planning list"}),
    ({"position": None, "of": 15, "batch_card": "DRE-2683"},
     {"position": None, "of": None, "added": True, "writer": "the drain",
      "place": None, "batch_card": "DRE-2683",
      "verdict": "added by the CEO, no position"}),
    ({"position": 7, "of": 15, "place": 2, "batch_card": "DRE-4541"},
     {"position": 7, "of": 15, "added": False,
      "writer": "the sweep's queue release, place 2", "place": 2,
      "batch_card": "DRE-4541",
      "verdict": "position 7 of 15 on the approved Planning list"}),
    ({"position": None, "of": 15, "place": 4, "batch_card": "DRE-4541"},
     {"position": None, "of": None, "added": True,
      "writer": "the sweep's queue release, place 4", "place": 4,
      "batch_card": "DRE-4541", "verdict": "added by the CEO, no position"}),
])
def test_the_parser_reads_back_both_writers_output(kwargs, expected):
    parsed = groomer.parse_moved_note(groomer.moved_note(PID, **kwargs))
    assert parsed == {"id": PID, "from": "Intake", "to": "Planning", **expected}


def test_the_parser_reads_only_a_note_anchored_at_the_first_line():
    note = groomer.moved_note(PID, position=1, of=2, batch_card="DRE-1")
    assert groomer.parse_moved_note(f"As the drain wrote:\n{note}") is None
    assert groomer.parse_moved_note(None) is None
    assert groomer.parse_moved_note(
        groomer.cancelled_note(PID, "superseded")) is None


def test_reconcile_renders_no_note_of_its_own():
    """Both writers call the ONE renderer: the sweep never spells the tag."""
    source = (ROOT / "scripts" / "reconcile.py").read_text("utf-8")
    assert "groom-moved" not in source and "MOVED_TAG" not in source
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "groomer.moved_note"]
    assert len(calls) == 1


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


def test_a_queued_card_gets_no_note_until_it_is_released(monkeypatch):
    monkeypatch.setattr(groomer, "free_planner_slots", lambda lops: (3, 3))
    proposal, ops = _morning()
    result = groomer.drain(ops, card=PROPOSAL_CARD)
    assert result["queued"], "nothing queued, so this proves nothing"
    noted = {i for i, _ in _notes(ops.written)}
    assert noted == set(result["moved"]) and len(noted) == 3
    assert not noted & set(result["queued"])


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
    proposal = two_lists()
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
# the sweep's queue release — the same note, its place, its position
# --------------------------------------------------------------------------
def _drained_then_queued(monkeypatch, *, free=2, added=None):
    """Drain the fifteen-card batch on the standing card with `free` slots,
    and hand back the thread the sweep reads afterwards: the proposal, the
    approval and every record the drain wrote on the card."""
    monkeypatch.setattr(groomer, "free_planner_slots", lambda lops: (free, 3))
    proposal = two_lists()
    comments = _thread(proposal)
    if added:
        comments.append(_decision(groomer.ADD_TAG, proposal, card=added))
    ops = FakeOps(comments=comments)
    result = groomer.drain(ops, card=STANDING)
    thread = comments + [{"body": b, "authored_by_pipeline": True,
                          "created_at": "2026-10-06T12:00:00Z"}
                         for i, b in ops.written if i == STANDING]
    return proposal, result, thread


def test_a_released_card_carries_the_note_naming_the_sweep_and_its_place(
        monkeypatch):
    proposal, result, thread = _drained_then_queued(monkeypatch)
    queued = result["queued"]
    queue = Queue(thread, [_card(i) for i in queued])
    released = _release(queue, 3)
    assert released == queued[:3]
    positions, of = _positions(proposal), len(_planning(proposal))
    for place, identifier in enumerate(released, 1):
        notes = _notes(queue.of("comment"), identifier)
        assert len(notes) == 1, f"{identifier} carries {len(notes)} notes"
        assert notes[0][1] == groomer.moved_note(
            proposal["id"], position=positions[identifier], of=of,
            place=place, batch_card=STANDING)
        parsed = groomer.parse_moved_note(notes[0][1])
        assert parsed["position"] == positions[identifier] == place + 2
        assert parsed["writer"] == f"the sweep's queue release, place {place}"
    assert {i for i, _ in _notes(queue.of("comment"))} == set(released)


def test_a_released_addition_is_noted_as_added_with_no_position(monkeypatch):
    proposal, result, thread = _drained_then_queued(monkeypatch, free=15,
                                                    added=ADDED)
    assert result["queued"] == [ADDED]
    queue = Queue(thread, [_card(ADDED)])
    assert _release(queue, 1) == [ADDED]
    [(_, body)] = _notes(queue.of("comment"), ADDED)
    assert body == groomer.moved_note(proposal["id"], position=None, of=15,
                                      place=1, batch_card=STANDING)


def test_the_release_writes_the_note_before_the_lane():
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"}])
    queue = Queue([{"body": queued, "authored_by_pipeline": True,
                    "created_at": "2026-10-06T12:00:00Z"}], [_card("DRE-11")])
    assert _release(queue, 1) == ["DRE-11"]
    on_card = [w for w in queue.writes if w[1] == "DRE-11"]
    assert [w[0] for w in on_card][:2] == ["comment", "state"]
    assert on_card[0][2].startswith(f"{groomer.MARK} {groomer.MOVED_TAG}: {PID}")


def test_a_release_with_no_proposal_on_the_thread_still_writes_a_note():
    """The position cannot be read, and the note says so rather than guessing
    — never `added by the CEO` for a card nobody can say was added."""
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"}])
    queue = Queue([{"body": queued, "authored_by_pipeline": True,
                    "created_at": "2026-10-06T12:00:00Z"}], [_card("DRE-11")])
    _release(queue, 1)
    [(_, body)] = _notes(queue.of("comment"), "DRE-11")
    parsed = groomer.parse_moved_note(body)
    assert parsed["id"] == PID and parsed["position"] is None
    assert not parsed["added"] and "added by the CEO" not in body
    assert parsed["writer"] == "the sweep's queue release, place 1"


def test_a_failed_release_state_write_still_leaves_the_note():
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"}])
    queue = Queue([{"body": queued, "authored_by_pipeline": True,
                    "created_at": "2026-10-06T12:00:00Z"}],
                  [_card("DRE-11")], fail_state={"DRE-11"})
    assert _release(queue, 1) == []
    assert len(_notes(queue.of("comment"), "DRE-11")) == 1
    assert queue.of("state") == []


class _Cards(Queue):
    """The queue fake, with each card's own thread served back: the standing
    card's is the queue's, any other card's is what the pass wrote on it."""

    def comment_records(self, identifier, *, whole_thread=False):
        if identifier == STANDING:
            return super().comment_records(identifier, whole_thread=whole_thread)
        self.thread_reads.append((identifier, whole_thread))
        return [{"body": b, "authored_by_pipeline": True}
                for i, b in self.of("comment") if i == identifier]


def test_a_stalled_release_retried_pass_after_pass_writes_one_note():
    """The note posts, the lane write fails, the next pass tries again: the
    card already carries its note, so the retry writes no second one."""
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"}])
    queue = _Cards([{"body": queued, "authored_by_pipeline": True,
                     "created_at": "2026-10-06T12:00:00Z"}],
                   [_card("DRE-11")], fail_state={"DRE-11"})
    for _ in range(3):
        reconcile.reset_sweep_cards()
        assert _release(queue, 1) == []
    assert len(_notes(queue.of("comment"), "DRE-11")) == 1
    queue.fail_state.clear()
    reconcile.reset_sweep_cards()
    assert _release(queue, 1) == ["DRE-11"]
    assert len(_notes(queue.of("comment"), "DRE-11")) == 1
    assert queue.of("state") == [("DRE-11", "Planning")]


def test_another_batchs_note_or_a_persons_copy_does_not_count():
    """Only the pipeline's note for THIS batch stands in for the write."""
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"}])

    class _Seeded(Queue):
        def comment_records(self, identifier, *, whole_thread=False):
            if identifier == STANDING:
                return super().comment_records(identifier,
                                               whole_thread=whole_thread)
            return [{"body": groomer.moved_note("fedcba987654", position=1,
                                                of=1, batch_card="DRE-1"),
                     "authored_by_pipeline": True},
                    {"body": groomer.moved_note(PID, position=1, of=1,
                                                batch_card=STANDING),
                     "authored_by_pipeline": False}]

    queue = _Seeded([{"body": queued, "authored_by_pipeline": True,
                      "created_at": "2026-10-06T12:00:00Z"}], [_card("DRE-11")])
    assert _release(queue, 1) == ["DRE-11"]
    assert len(_notes(queue.of("comment"), "DRE-11")) == 1


def test_a_note_that_will_not_post_moves_nothing_and_stops_the_pass():
    """A card is never in Planning without its note: a note Linear refused is
    a write failure, the card stays queued, and the pass releases no more."""
    queued = groomer.queued_record(PID, [{"identifier": "DRE-11",
                                          "repo": "portico"},
                                         {"identifier": "DRE-12",
                                          "repo": "portico"}])

    class _NoNotes(Queue):
        def cmd_comment(self, identifier, body, *flags):
            if body.startswith(f"{groomer.MARK} {groomer.MOVED_TAG}:"):
                raise linear_ops.LinearError(f"{identifier}: Linear said no")
            super().cmd_comment(identifier, body, *flags)

    queue = _NoNotes([{"body": queued, "authored_by_pipeline": True,
                       "created_at": "2026-10-06T12:00:00Z"}],
                     [_card("DRE-11"), _card("DRE-12")])
    assert _release(queue, 2) == []
    assert queue.of("state") == [] and queue.of("unlabel") == []
    assert any("DRE-11" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------
# the records beside it, the registry and the document
# --------------------------------------------------------------------------
def _registry() -> dict:
    return json.loads((ROOT / "config" / "pipeline-acts.json")
                      .read_text("utf-8"))


@pytest.mark.parametrize("path, anchor", [
    ("scripts/groomer.py", 'moved_note(record["id"]'),
    ("scripts/reconcile.py", "groomer.moved_note("),
])
def test_both_note_sites_are_declared_not_an_act(path, anchor):
    rows = [r for r in _registry()["unconverted"]
            if r.get("file") == path and anchor in r.get("anchor", "")]
    assert len(rows) == 1, f"{path} {anchor!r} is not declared"
    assert rows[0]["kind"] == "not-an-act"


def test_the_doc_carries_the_groom_moved_row():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    row = next((ln for ln in doc.splitlines()
                if ln.startswith(f"| `{groomer.MARK} {groomer.MOVED_TAG}:")),
               None)
    assert row, "the decision-vocabulary table has no groom-moved row"
    assert ("`🧺 groom-moved: <id> — <verdict> · <from> → <to> · by <writer> "
            "· record on <batch card>`") in row
