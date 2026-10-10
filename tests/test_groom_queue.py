"""RED-first: an approved groom batch goes into the planner line instead of
being refused, and the reconcile sweep releases it (DRE-5435).

THE MORNING. On 2026-10-01 the CEO approved groom batch 37b77a9f795d (20
cards, all for Planning) at 06:29, 06:30 and 07:07 PT, and was answered
`groom-drain-refused: … planner slots: 0 free of 2` each time: DRE-5326's
`NoFreeSlot` left the batch approvable, so the only way forward was to keep
approving until a slot happened to be free at that moment. The operator fed
the batch into Planning by hand, two cards every 15 minutes.

THE RULE. One approval moves the whole batch into the line: the drain moves
what the free slots take and QUEUES the rest — each card stays in Intake and
gains the `groom-queued` label, and ONE `🧺 groom-queued: <id>` record on the
proposal card lists them in place order (`tests/test_groomer_drain_slots.py`).
This suite is the other half, `reconcile.release_groom_queue`, the planner
line backstop's (DRE-5178) next phase:

  * per free slot `serve_planner_line` has left, ONE queued card moves to
    Planning and loses its label — oldest batch first, place order within a
    batch — and one `🧺 groom-released` row per pass says what moved and what
    left the lane;
  * a queued card that has left Intake is dropped with no state write, its
    place not held; one whose label a person removed is never released;
  * only the bureau-pipeline sweep releases (`epic_cap.START_OWNER_SLUG`),
    on full sweeps only, right after `serve_planner_line`;
  * the groomer never proposes a queued card again;
  * waiting in the groom queue is never escalated to the CEO: a queued card
    is in Intake, which no Planning watchdog and no planner line reads;
  * a stalled queue — a free slot, nothing released, nothing changed for the
    planner line's own bound — is ONE `no-code` card created in Triage, never
    a second while the first is open.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_queue.py -v
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import epic_cap  # noqa: E402
import groomer  # noqa: E402
import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import planner_queue  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402

from test_planner_queue_sweep import Board, _claimed, _waiting  # noqa: E402

STANDING = "DRE-4541"
OLDER, NEWER = "01d0ba7c4e11", "fe3e7ba7c422"
THIS = "dreadnought-foundry/bureau-pipeline"


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", THIS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", epic_cap.START_OWNER_SLUG)
    monkeypatch.setenv(reconcile.GROOM_CARD_ENV, STANDING)
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    ledgers = (reconcile._write_failures, reconcile._read_failures,
               reconcile._stale_defects)
    for ledger in ledgers:
        ledger.clear()
    reconcile.reset_sweep_cards()
    yield
    for ledger in ledgers:
        ledger.clear()


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _iso(minutes_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(minutes=minutes_ago))
            .isoformat().replace("+00:00", "Z"))


def _card(ident: str, *, lane: str = "Intake", queued: bool = True,
          days_old: float = 30.0) -> dict:
    """A card as the sweep's board read returns it, labels included."""
    labels = [{"name": "repo:portico"}, {"name": "agent:engineer"}]
    if queued:
        labels.append({"name": groomer.QUEUED_LABEL})
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"card {ident}",
        "description": "work",
        "priority": 3,
        "createdAt": _iso(days_old * 1440),
        "updatedAt": _iso(days_old * 1440),
        "state": {"name": lane},
        "labels": {"nodes": labels},
        "children": {"nodes": []},
        "comments": {"nodes": []},
    }


def _queued(pid: str, idents, *, minutes_ago: float = 60.0) -> dict:
    """The drain's own queued record, as the standing card's thread carries it."""
    return {"body": groomer.queued_record(
                pid, [{"identifier": i, "repo": "portico"} for i in idents]),
            "authored_by_pipeline": True, "created_at": _iso(minutes_ago)}


def _released(pid: str, *, released=(), left=(), unqueued=(),
              minutes_ago: float = 30.0) -> dict:
    return {"body": groomer.released_record([
                {"id": pid, "released": list(released), "left": list(left),
                 "unqueued": list(unqueued)}]),
            "authored_by_pipeline": True, "created_at": _iso(minutes_ago)}


#: The freshness bound (DRE-6640), read off the sweep and never restated:
#: every time below that depends on it is built from it.
BOUND = reconcile.GROOM_LABEL_FRESH_MINUTES
INSIDE = BOUND / 2          # a queued record younger than the bound
PAST = BOUND * 2            # one older than it


class Queue:
    """Linear behind the release: the standing card's thread, the board read,
    and every write the phase makes, recorded in order. `direct` is what a
    direct read of one card answers for its label (DRE-6640): True labelled,
    False not; a card it does not name was never expected to be read."""

    def __init__(self, thread, cards, *, open_card=None, fail_state=(),
                 direct=None):
        self.thread = list(thread)
        self.board = Board(cards)
        self.open_card = open_card
        self.fail_state = set(fail_state)
        self.direct = dict(direct or {})
        self.writes: list[tuple] = []
        self.thread_reads: list[tuple] = []
        self.direct_reads: list[tuple] = []

    def comment_records(self, identifier, *, whole_thread=False):
        self.thread_reads.append((identifier, whole_thread))
        return list(self.thread)

    def get_issue(self, identifier, *, fresh=False):
        self.direct_reads.append((identifier, fresh))
        assert identifier in self.direct, f"{identifier} was read directly"
        card = _card(identifier, queued=self.direct[identifier])
        return {"id": card["id"], "identifier": identifier,
                "state": card["state"], "labels": card["labels"]}

    def cmd_state(self, identifier, state_name, *flags):
        if identifier in self.fail_state:
            raise linear_ops.LinearError(f"{identifier}: Linear said no")
        self.writes.append(("state", identifier, state_name))

    def remove_label(self, identifier, label_name):
        self.writes.append(("unlabel", identifier, label_name))

    def cmd_comment(self, identifier, body, *flags):
        self.writes.append(("comment", identifier, body))

    def find_open(self, title):
        self.writes.append(("find_open", title))
        return self.open_card

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane=linear_ops.CREATE_LANE):
        self.writes.append(("create", title, description, repo_slug,
                            tuple(labels), lane))
        return {"identifier": "DRE-9999", "url": "https://linear.app/x"}

    # -- reading back -------------------------------------------------------
    def of(self, kind: str) -> list:
        return [w[1:] for w in self.writes if w[0] == kind]

    def rows(self) -> list:
        posts = [body for ident, body in self.of("comment") if ident == STANDING]
        assert len(posts) <= 1, f"{len(posts)} groom-released comments in a pass"
        return groomer.parse_released_record(posts[0]) if posts else []


@contextlib.contextmanager
def _linear(queue: Queue):
    with mock.patch.object(reconcile, "active_cards",
                           side_effect=queue.board.active_cards), \
            mock.patch.object(linear_ops, "comment_records",
                              side_effect=queue.comment_records), \
            mock.patch.object(linear_ops, "cmd_state",
                              side_effect=queue.cmd_state), \
            mock.patch.object(linear_ops, "cmd_advance") as advance, \
            mock.patch.object(linear_ops, "remove_label",
                              side_effect=queue.remove_label), \
            mock.patch.object(linear_ops, "add_label") as add, \
            mock.patch.object(linear_ops, "cmd_comment",
                              side_effect=queue.cmd_comment), \
            mock.patch.object(linear_ops, "find_open",
                              side_effect=queue.find_open), \
            mock.patch.object(linear_ops, "get_issue",
                              side_effect=queue.get_issue), \
            mock.patch.object(linear_ops, "create_card",
                              side_effect=queue.create_card):
        yield
    assert not advance.called and not add.called


def _release(queue: Queue, free: int):
    with _linear(queue):
        return reconcile.release_groom_queue(free)


# --------------------------------------------------------------------------- #
# the release                                                                  #
# --------------------------------------------------------------------------- #


def test_one_card_per_free_slot_oldest_batch_first():
    queue = Queue(
        [_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=120),
         _queued(NEWER, ["DRE-21", "DRE-22"], minutes_ago=60)],
        [_card(i) for i in ("DRE-11", "DRE-12", "DRE-21", "DRE-22")])
    released = _release(queue, 3)
    assert released == ["DRE-11", "DRE-12", "DRE-21"]
    assert queue.of("state") == [(i, "Planning") for i in released]
    assert queue.of("unlabel") == [(i, groomer.QUEUED_LABEL) for i in released]
    assert queue.rows() == [
        {"id": OLDER, "released": ["DRE-11", "DRE-12"], "left": [],
         "unqueued": []},
        {"id": NEWER, "released": ["DRE-21"], "left": [], "unqueued": []}]
    # One read of the standing card's WHOLE thread — the proposal card
    # outgrows the fifty-comment window. (Each released card's own window is
    # read once too, for its groom-moved note — DRE-3326.)
    assert [r for r in queue.thread_reads if r[0] == STANDING] == [
        (STANDING, True)]


def test_place_order_within_a_batch_not_card_number():
    queue = Queue([_queued(OLDER, ["DRE-30", "DRE-10", "DRE-20"])],
                  [_card(i) for i in ("DRE-10", "DRE-20", "DRE-30")])
    assert _release(queue, 2) == ["DRE-30", "DRE-10"]


def test_the_destination_is_a_module_constant_the_writer_check_reads():
    assert reconcile.GROOM_RELEASE_TO == "Planning"
    found = [w for w in ready_lane_writers.writes()
             if w.where.startswith("scripts/reconcile.py:")
             and w.expression == "GROOM_RELEASE_TO"]
    assert [(w.writer, w.lane) for w in found] == [("reconcile.py", "Planning")]
    assert "reconcile.py" in lane_contract.lane_writers("Planning")


def test_a_released_card_is_never_released_twice():
    queue = Queue([_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=120),
                   _released(OLDER, released=["DRE-11"])],
                  [_card("DRE-11"), _card("DRE-12")])
    assert _release(queue, 2) == ["DRE-12"]


def test_no_free_slot_releases_nothing_and_writes_nothing():
    queue = Queue([_queued(OLDER, ["DRE-11"])], [_card("DRE-11")])
    assert _release(queue, 0) == []
    assert queue.writes == []


def test_an_empty_queue_reads_one_thread_and_writes_nothing():
    queue = Queue([], [_card("DRE-11", queued=False)])
    assert _release(queue, 2) == []
    assert queue.writes == []


def test_a_card_that_left_intake_is_dropped_with_no_state_write():
    """In Planning by hand (label still on it): the label comes off, no
    state write. Canceled, off the board read: nothing is written to it.
    Neither holds its place — the next card takes the slot."""
    queue = Queue([_queued(OLDER, ["DRE-11", "DRE-12", "DRE-13"])],
                  [_card("DRE-11", lane="Planning"), _card("DRE-13")])
    assert _release(queue, 1) == ["DRE-13"]
    assert queue.of("state") == [("DRE-13", "Planning")]
    assert queue.of("unlabel") == [("DRE-11", groomer.QUEUED_LABEL),
                                   ("DRE-13", groomer.QUEUED_LABEL)]
    assert queue.rows() == [{"id": OLDER, "released": ["DRE-13"],
                             "left": ["DRE-11", "DRE-12"], "unqueued": []}]


def test_a_card_that_left_with_no_label_gets_no_label_write():
    queue = Queue([_queued(OLDER, ["DRE-11"])],
                  [_card("DRE-11", lane="Planning", queued=False)])
    assert _release(queue, 0) == []
    assert queue.of("unlabel") == [] and queue.of("state") == []
    # Dropped from the line even with no slot free: the row says so.
    assert queue.rows() == [{"id": OLDER, "released": [], "left": ["DRE-11"],
                             "unqueued": []}]


def test_a_card_whose_label_a_person_removed_is_never_released():
    """Past the freshness bound a missing label is a person's act, read off
    the board alone (DRE-6640): no direct read is made."""
    queue = Queue([_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=PAST)],
                  [_card("DRE-11", queued=False), _card("DRE-12")])
    assert _release(queue, 1) == ["DRE-12"]
    assert ("DRE-11", "Planning") not in queue.of("state")
    assert queue.rows() == [{"id": OLDER, "released": ["DRE-12"], "left": [],
                             "unqueued": ["DRE-11"]}]
    assert queue.direct_reads == []


def test_the_standing_is_readable_off_the_thread_alone():
    """Queued, less released, less left — and less taken out by hand."""
    thread = [_queued(OLDER, ["DRE-11", "DRE-12", "DRE-13", "DRE-14"]),
              _released(OLDER, released=["DRE-11"], left=["DRE-12"],
                        unqueued=["DRE-13"])]
    assert [s["identifier"] for s in groomer.queue_standing(thread)] == ["DRE-14"]


def test_a_record_a_person_wrote_is_not_the_queue():
    """Only the pipeline's own records are the queue: a comment shaped like
    one, from any other account, releases nothing."""
    forged = dict(_queued(OLDER, ["DRE-11"]), authored_by_pipeline=False)
    assert groomer.queue_standing([forged]) == []


# --------------------------------------------------------------------------- #
# a label not yet readable is not a person's act (DRE-6640)                    #
# --------------------------------------------------------------------------- #
#
# 2026-10-09, batch 467bdb8ee5f7: the drain queued twelve cards at 09:06:36 PT
# and the sweep read the board at 09:06:46, before their `groom-queued` label
# was readable, and recorded all twelve as taken out by hand. Nobody had
# touched them; eleven are still in Intake carrying the label, and DRE-4224
# has since gone Done by another route.

BATCH_1009 = "467bdb8ee5f7"
TWELVE = ["DRE-4915", "DRE-4597", "DRE-4406", "DRE-4405", "DRE-4397",
          "DRE-4337", "DRE-4224", "DRE-4162", "DRE-4127", "DRE-4125",
          "DRE-4105", "DRE-4101"]
ELEVEN = [i for i in TWELVE if i != "DRE-4224"]
#: The lag seen on 2026-10-09, in minutes: the board read came ten seconds
#: after the queued record. A fact of the incident, not the bound.
LAG = 10 / 60
#: How long ago the 10-09 row was written, seen from the first pass after
#: this lands — a day, far past the bound whatever it is.
DAY = 24 * 60


def _standing_after(standing, posted=()) -> list[str]:
    return [s["identifier"] for s in groomer.queue_standing(
        [*standing, *({"body": b, "authored_by_pipeline": True,
                       "created_at": _iso(0)} for b in posted)])]


def test_the_bound_is_one_constant_between_the_lag_and_the_sweep_interval():
    workflow = (ROOT / ".github" / "workflows" / "self-reconcile.yml").read_text(
        encoding="utf-8")
    interval = int(re.search(r'cron:\s*"\*/(\d+) ', workflow).group(1))
    assert LAG < BOUND <= interval
    # Written once, as a module constant, with the comment that says why.
    source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = [n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name)
                        and t.id == "GROOM_LABEL_FRESH_MINUTES"
                        for t in n.targets)]
    assert len(assigned) == 1
    above = source.splitlines()[assigned[0].lineno - 2]
    assert above.startswith("#:"), above
    assert source.count("GROOM_LABEL_FRESH_MINUTES =") == 1


_WORDS = dict(enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen".split()))


def _bound_literals(text: str) -> list[str]:
    digits = re.escape(f"{BOUND:g}")
    found = re.findall(rf"\b{digits}(?:\.0+)?\s*-?\s*min", text, re.I)
    found += re.findall(rf"minutes(?:_ago)?\s*=\s*{digits}(?:\.0+)?\b", text)
    return found


def test_no_literal_number_of_minutes_for_the_bound_in_the_tests_or_the_doc():
    tests = Path(__file__).read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    assert _bound_literals(tests) == [] and _bound_literals(doc) == []
    word = _WORDS.get(BOUND)
    queue_section = doc.split("**Every card past the free slots is queued")[1] \
        .split("### What the drain wrote down")[0]
    assert "GROOM_LABEL_FRESH_MINUTES" in queue_section
    if word:
        assert not re.search(rf"\b{word}[ -]minute", tests, re.I)
        assert not re.search(rf"\b{word}[ -]minute", queue_section, re.I)


def test_inside_the_bound_a_missing_label_is_read_directly_and_released():
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=INSIDE)],
                  [_card("DRE-11", queued=False)], direct={"DRE-11": True})
    assert _release(queue, 1) == ["DRE-11"]
    assert queue.direct_reads == [("DRE-11", True)]
    assert queue.of("state") == [("DRE-11", "Planning")]
    assert queue.rows() == [{"id": OLDER, "released": ["DRE-11"], "left": [],
                             "unqueued": []}]


def test_the_same_card_inside_the_bound_is_held_not_taken_out():
    """`test_a_card_whose_label_a_person_removed_is_never_released`, with its
    queued record inside the bound and the direct read showing no label
    either: the card keeps its place and nothing is written about it."""
    queue = Queue([_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=INSIDE)],
                  [_card("DRE-11", queued=False), _card("DRE-12")],
                  direct={"DRE-11": False})
    assert _release(queue, 1) == ["DRE-12"]
    assert ("DRE-11", "Planning") not in queue.of("state")
    assert queue.rows() == [{"id": OLDER, "released": ["DRE-12"], "left": [],
                             "unqueued": []}]
    assert queue.direct_reads == [("DRE-11", True)]
    posted = [b for i, b in queue.of("comment") if i == STANDING]
    assert _standing_after(queue.thread, posted) == ["DRE-11"]


def test_inside_the_bound_with_a_label_in_neither_read_nothing_is_written():
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=INSIDE)],
                  [_card("DRE-11", queued=False)], direct={"DRE-11": False})
    assert _release(queue, 2) == []
    assert queue.writes == []


def test_the_direct_read_is_made_only_for_an_unlabelled_card_inside_the_bound():
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=PAST),
                   _queued(NEWER, ["DRE-21", "DRE-22"], minutes_ago=INSIDE)],
                  [_card("DRE-11", queued=False), _card("DRE-21"),
                   _card("DRE-22", queued=False)],
                  direct={"DRE-22": True})
    assert _release(queue, 3) == ["DRE-21", "DRE-22"]
    assert queue.direct_reads == [("DRE-22", True)]
    assert queue.rows() == [
        {"id": OLDER, "released": [], "left": [], "unqueued": ["DRE-11"]},
        {"id": NEWER, "released": ["DRE-21", "DRE-22"], "left": [],
         "unqueued": []}]


def test_the_age_is_the_newest_queued_record_naming_the_card():
    """A batch queued again (its first drain died before the drained record)
    is as young as its newest record."""
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=PAST),
                   _queued(OLDER, ["DRE-11"], minutes_ago=INSIDE)],
                  [_card("DRE-11", queued=False)], direct={"DRE-11": False})
    assert _release(queue, 1) == []
    assert queue.writes == [] and queue.direct_reads == [("DRE-11", True)]


@pytest.mark.parametrize("shown", [True, False])
def test_the_2026_10_09_batch_replayed(shown):
    """Twelve queued, a sweep ten seconds later whose board read shows no
    label, two free slots."""
    queue = Queue([_queued(BATCH_1009, TWELVE, minutes_ago=LAG)],
                  [_card(i, queued=False) for i in TWELVE],
                  direct={i: shown for i in TWELVE})
    released = _release(queue, 2)
    posted = [b for i, b in queue.of("comment") if i == STANDING]
    assert "taken out by hand" not in "".join(posted)
    if shown:
        assert released == TWELVE[:2]
        assert queue.of("state") == [(i, "Planning") for i in TWELVE[:2]]
        assert queue.rows() == [{"id": BATCH_1009, "released": TWELVE[:2],
                                 "left": [], "unqueued": []}]
        assert _standing_after(queue.thread, posted) == TWELVE[2:]
    else:
        assert released == [] and queue.writes == []
        assert _standing_after(queue.thread) == TWELVE


# --------------------------------------------------------------------------- #
# a wrongly dropped card is put back in the line (DRE-6640)                    #
# --------------------------------------------------------------------------- #


def _incident_thread() -> list[dict]:
    return [_queued(BATCH_1009, TWELVE, minutes_ago=DAY + LAG),
            _released(BATCH_1009, unqueued=TWELVE, minutes_ago=DAY)]


def _incident_board() -> list[dict]:
    return [_card(i, lane="Done" if i == "DRE-4224" else "Intake")
            for i in TWELVE]


def _repair(thread, cards, free=0) -> tuple[Queue, list[str]]:
    queue = Queue(thread, cards)
    _release(queue, free)
    return queue, [b for i, b in queue.of("comment") if i == STANDING]


def test_the_eleven_are_put_back_by_one_record_and_only_once():
    thread = _incident_thread()
    queue, posted = _repair(thread, _incident_board())
    assert len(posted) == 1 and queue.writes == [("comment", STANDING, posted[0])]
    record = groomer.parse_queued_record(posted[0])
    assert posted[0].startswith(f"{groomer.MARK} {groomer.QUEUED_TAG}: {BATCH_1009}\n")
    assert record["id"] == BATCH_1009
    assert [c["identifier"] for c in record["cards"]] == ELEVEN
    # Their original places: DRE-4224's seventh is not taken by another.
    assert [c["place"] for c in record["cards"]] == [
        TWELVE.index(i) + 1 for i in ELEVEN]
    # The receipt: the row it corrects, by its date, and what was wrong.
    row_day = thread[1]["created_at"][:10]
    assert row_day in posted[0]
    assert f"{groomer.MARK} {groomer.RELEASED_TAG}" in posted[0]
    assert "taken out by hand" in posted[0]
    assert "nobody" in posted[0].lower()
    # The queue reads the cards again, by its one grammar.
    assert _standing_after(thread, posted) == ELEVEN
    # A second pass over the thread with that record on it writes nothing.
    again = [*thread, {"body": posted[0], "authored_by_pipeline": True,
                       "created_at": _iso(INSIDE)}]
    queue, more = _repair(again, _incident_board())
    assert more == [] and queue.writes == []


def test_a_card_that_left_intake_or_lost_its_label_is_not_put_back():
    thread = [_queued(OLDER, ["DRE-11", "DRE-12", "DRE-13"], minutes_ago=DAY),
              _released(OLDER, unqueued=["DRE-11", "DRE-12", "DRE-13"],
                        minutes_ago=DAY)]
    queue, posted = _repair(thread, [_card("DRE-11", queued=False),
                                     _card("DRE-12", lane="Planning"),
                                     _card("DRE-13")])
    record = groomer.parse_queued_record(posted[0])
    assert [c["identifier"] for c in record["cards"]] == ["DRE-13"]
    assert queue.direct_reads == []


def test_a_card_released_by_a_row_is_not_put_back():
    thread = [_queued(OLDER, ["DRE-11"], minutes_ago=DAY),
              _released(OLDER, released=["DRE-11"], minutes_ago=DAY)]
    queue, posted = _repair(thread, [_card("DRE-11")])
    assert posted == [] and queue.writes == []


def _dropped_then(record: dict) -> list[dict]:
    return [_queued(OLDER, ["DRE-11"], minutes_ago=DAY),
            _released(OLDER, unqueued=["DRE-11"], minutes_ago=DAY),
            record]


def test_a_pipeline_record_after_the_row_naming_the_card_means_done():
    queue, posted = _repair(
        _dropped_then(_queued(OLDER, ["DRE-11"], minutes_ago=INSIDE)),
        [_card("DRE-11")])
    assert posted == [] and queue.writes == []


def test_a_record_a_person_wrote_after_the_row_does_not_count():
    forged = dict(_queued(OLDER, ["DRE-11"], minutes_ago=INSIDE),
                  authored_by_pipeline=False)
    _queue, posted = _repair(_dropped_then(forged), [_card("DRE-11")])
    assert len(posted) == 1
    assert [c["identifier"] for c in
            groomer.parse_queued_record(posted[0])["cards"]] == ["DRE-11"]


def test_a_record_written_before_the_row_does_not_count():
    """A batch queued twice before the row named its card: both records are
    older than the row, so neither is the repair."""
    thread = [_queued(OLDER, ["DRE-11"], minutes_ago=DAY + 1),
              _queued(OLDER, ["DRE-11"], minutes_ago=DAY),
              _released(OLDER, unqueued=["DRE-11"], minutes_ago=DAY)]
    _queue, posted = _repair(thread, [_card("DRE-11")])
    assert len(posted) == 1


def test_one_record_per_batch_in_place_order():
    thread = [_queued(OLDER, ["DRE-13", "DRE-11"], minutes_ago=DAY),
              _queued(NEWER, ["DRE-21"], minutes_ago=DAY),
              _released(OLDER, unqueued=["DRE-11", "DRE-13"], minutes_ago=DAY),
              _released(NEWER, unqueued=["DRE-21"], minutes_ago=DAY)]
    _queue, posted = _repair(thread, [_card(i) for i in
                                      ("DRE-11", "DRE-13", "DRE-21")])
    records = [groomer.parse_queued_record(b) for b in posted]
    assert [(r["id"], [c["identifier"] for c in r["cards"]]) for r in records] \
        == [(OLDER, ["DRE-13", "DRE-11"]), (NEWER, ["DRE-21"])]


def test_a_repaired_card_is_released_on_a_later_pass_like_any_queued_card():
    thread = [_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=DAY),
              _released(OLDER, unqueued=["DRE-11", "DRE-12"], minutes_ago=DAY)]
    _queue, posted = _repair(thread, [_card("DRE-11"), _card("DRE-12")])
    later = [*thread, {"body": posted[0], "authored_by_pipeline": True,
                       "created_at": _iso(PAST)}]
    queue = Queue(later, [_card("DRE-11"), _card("DRE-12")])
    assert _release(queue, 1) == ["DRE-11"]
    notes = [groomer.parse_moved_note(b) for i, b in queue.of("comment")
             if i == "DRE-11"]
    assert len(notes) == 1 and notes[0]["id"] == OLDER
    assert queue.of("state") == [("DRE-11", "Planning")]
    assert queue.of("unlabel") == [("DRE-11", groomer.QUEUED_LABEL)]
    assert queue.rows() == [{"id": OLDER, "released": ["DRE-11"], "left": [],
                             "unqueued": []}]


def test_a_repaired_card_is_protected_by_the_bound_on_the_pass_that_releases_it():
    """Its record is fresh: a board read that has not caught up with the
    label reads it directly rather than taking it out again."""
    thread = [_queued(OLDER, ["DRE-11"], minutes_ago=DAY),
              _released(OLDER, unqueued=["DRE-11"], minutes_ago=DAY)]
    _queue, posted = _repair(thread, [_card("DRE-11")])
    later = [*thread, {"body": posted[0], "authored_by_pipeline": True,
                       "created_at": _iso(INSIDE)}]
    queue = Queue(later, [_card("DRE-11", queued=False)],
                  direct={"DRE-11": True})
    assert _release(queue, 1) == ["DRE-11"]
    assert "taken out by hand" not in "".join(b for _i, b in queue.of("comment"))


def test_only_the_bureau_pipeline_sweep_releases(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    queue = Queue([_queued(OLDER, ["DRE-11"])], [_card("DRE-11")])
    assert _release(queue, 2) == []
    assert queue.writes == [] and queue.thread_reads == []


def test_a_failed_release_is_a_write_failure_and_stops_the_pass():
    queue = Queue([_queued(OLDER, ["DRE-11", "DRE-12"])],
                  [_card("DRE-11"), _card("DRE-12")], fail_state={"DRE-11"})
    assert _release(queue, 2) == []
    assert queue.of("state") == [] and queue.of("unlabel") == []
    assert any("DRE-11" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------- #
# its place in the sweep                                                       #
# --------------------------------------------------------------------------- #


def _main_source() -> ast.FunctionDef:
    tree = ast.parse((ROOT / "scripts" / "reconcile.py").read_text("utf-8"))
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")


def test_main_releases_right_after_serving_the_line_on_full_sweeps_only():
    main = _main_source()
    full = next(n for n in ast.walk(main) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "not promote_only"
                and "serve_planner_line(" in ast.unparse(n))
    calls = [ast.unparse(n.func) for n in ast.walk(full)
             if isinstance(n, ast.Call)]
    assert "serve_planner_line" in calls and "release_groom_queue" in calls
    lines = {ast.unparse(n.func): n.lineno for n in ast.walk(full)
             if isinstance(n, ast.Call)
             and ast.unparse(n.func) in ("serve_planner_line",
                                         "release_groom_queue")}
    assert lines["serve_planner_line"] < lines["release_groom_queue"]
    # And nowhere else in the pass.
    everywhere = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) == "release_groom_queue"]
    assert len(everywhere) == 1


def test_serve_planner_line_says_how_many_slots_it_left():
    """The cap less running, dispatched and waiting — `free_planner_slots`'s
    arithmetic, after the line has been served."""
    from test_planner_queue_sweep import _world
    with mock.patch.object(planner_queue, "cap", lambda cfg=None, environ=None: 2):
        with _world([]):
            assert reconcile.serve_planner_line() == 2
        reconcile.reset_sweep_cards()
        with _world([_claimed("DRE-101", "9001")], runs={"9001": "in_progress"}):
            assert reconcile.serve_planner_line() == 1
        reconcile.reset_sweep_cards()
        # Another repo's card waiting: not served here, and owed a slot.
        with _world([_claimed("DRE-101", "9001"),
                     _waiting("DRE-201", 5.0,
                              repo="dreadnought-foundry/portico")],
                    runs={"9001": "in_progress"}):
            assert reconcile.serve_planner_line() == 0


# --------------------------------------------------------------------------- #
# never proposed again, never escalated                                        #
# --------------------------------------------------------------------------- #


def test_the_groomer_never_proposes_a_queued_card_again(monkeypatch, capsys):
    from test_groom_verify import LANE, _args, _patch_build
    queued = _card("DRE-999", queued=True, days_old=1)
    queued.update(parent=None, project=None, cycle=None,
                  inverseRelations={"nodes": []})
    _patch_build(monkeypatch, [*LANE, queued])
    proposal = groomer._build(_args(capacity=20))
    named = json.dumps(proposal)
    assert "DRE-999" not in named
    assert "DRE-101" in named
    err = capsys.readouterr().err
    assert "1 card" in err and groomer.QUEUED_LABEL in err


def test_waiting_in_the_queue_is_never_escalated_and_gets_no_slot_receipt():
    """A queued card is in Intake. The Planning watchdog and the planner line
    read neither Intake nor its label, so a month in the queue escalates
    nothing and posts no planner-slot receipt."""
    assert "Intake" not in planner_queue.LEDGER_LANES
    queued = _card("DRE-11", days_old=30.0)
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=30 * 1440)], [queued])
    with _linear(queue), \
            mock.patch.object(reconcile, "escalate_out_of_planning") as escalate, \
            mock.patch.object(reconcile.plan_run, "fire") as fire, \
            mock.patch.object(reconcile.routing_verdict, "is_parked",
                              return_value=False):
        assert reconcile.flag_stalled_planning() == set()
        reconcile.serve_planner_line()
    assert not escalate.called and not fire.called
    assert queue.writes == []


# --------------------------------------------------------------------------- #
# a stalled queue is one Triage card                                           #
# --------------------------------------------------------------------------- #


def _stalled(**kw) -> Queue:
    bound = planner_queue.waiting_max()
    return Queue([_queued(OLDER, ["DRE-11", "DRE-12"], minutes_ago=bound + 60),
                  _released(OLDER, released=["DRE-10"], minutes_ago=bound + 30)],
                 [_card("DRE-11"), _card("DRE-12")],
                 fail_state={"DRE-11", "DRE-12"}, **kw)


def test_a_stalled_queue_files_one_no_code_card_in_triage():
    queue = _stalled()
    _release(queue, 1)
    created = queue.of("create")
    assert len(created) == 1
    title, body, slug, labels, lane = created[0]
    assert title == reconcile.GROOM_STALL_TITLE
    assert slug == "bureau-pipeline"
    assert labels == (linear_ops.NO_CODE_LABEL,)
    assert lane == reconcile.GROOM_STALL_LANE == "Triage"
    assert not [lbl for lbl in labels if lbl.startswith("agent:")]
    # The whole notice is in the body: which batch, how many, how long, and
    # what the last release wrote — so no comment is posted.
    assert OLDER in body and "2 cards" in body
    assert "DRE-10" in body
    assert queue.of("find_open") == [(reconcile.GROOM_STALL_TITLE,)]
    # Nothing on the standing card. The card the pass tried carries its
    # groom-moved note, written before the lane write that failed (DRE-3326).
    assert [c for c in queue.of("comment") if c[0] == STANDING] == []


def test_never_a_second_card_while_the_first_is_open():
    queue = _stalled(open_card="DRE-7777")
    _release(queue, 1)
    assert queue.of("create") == []


def test_no_card_when_the_queue_changed_inside_the_bound():
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=30)],
                  [_card("DRE-11")], fail_state={"DRE-11"})
    _release(queue, 1)
    assert queue.of("create") == [] and queue.of("find_open") == []


def test_no_card_when_there_was_no_free_slot():
    queue = _stalled()
    _release(queue, 0)
    assert queue.of("create") == [] and queue.of("find_open") == []


def test_the_bound_is_the_planner_lines_own(monkeypatch):
    """Read from `planner_queue.waiting_max()`, never restated."""
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=90)],
                  [_card("DRE-11")], fail_state={"DRE-11"})
    _release(queue, 1)
    assert queue.of("create") == []
    monkeypatch.setattr(planner_queue, "waiting_max", lambda: 60)
    queue = Queue([_queued(OLDER, ["DRE-11"], minutes_ago=90)],
                  [_card("DRE-11")], fail_state={"DRE-11"})
    _release(queue, 1)
    assert len(queue.of("create")) == 1


# --------------------------------------------------------------------------- #
# the declarations                                                             #
# --------------------------------------------------------------------------- #


def _registry() -> dict:
    return json.loads((ROOT / "config" / "pipeline-acts.json").read_text("utf-8"))


@pytest.mark.parametrize("path, anchor", [
    ("scripts/groomer.py", "queued_record("),
    ("scripts/reconcile.py", "released_record("),
    ("scripts/reconcile.py", "requeued_record("),
])
def test_the_two_new_comment_sites_are_declared_not_an_act(path, anchor):
    registry = _registry()
    rows = [r for r in registry["unconverted"]
            if r.get("file") == path and anchor in r.get("anchor", "")]
    assert len(rows) == 1, f"{path}: {anchor} is not declared"
    assert rows[0]["kind"] == "not-an-act"
    assert not [a for a in json.dumps(registry["acts"]).split('"')
                if anchor.rstrip("(") in a]


def test_the_act_receipt_guard_passes():
    done = subprocess.run([sys.executable, "scripts/check_act_receipts.py"],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_lane_contract_says_where_the_queue_goes():
    contract = json.loads((ROOT / "config" / "lane-contract.json")
                          .read_text("utf-8"))
    lanes = {lane["name"]: lane for lane in contract["lanes"]}
    intake_exit = lanes["Intake"]["clauses"]["exit"]["text"]
    triage_entrance = lanes["Triage"]["clauses"]["entrance"]["text"]
    assert "groom-queued" in intake_exit
    assert "groom queue" in triage_entrance


def test_the_groomer_doc_names_the_queue():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    assert f"`{groomer.MARK} {groomer.QUEUED_TAG}: <id>`" in doc
    assert f"`{groomer.MARK} {groomer.RELEASED_TAG}: <id>" in doc
    assert "Nine refusals" in doc and "Ten refusals" not in doc
    assert "NoFreeSlot" not in doc


def test_the_groomer_doc_names_the_bound_and_the_repair():
    doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")
    queue_section = doc.split("**Every card past the free slots is queued")[1] \
        .split("### What the drain wrote down")[0]
    assert "`GROOM_LABEL_FRESH_MINUTES`" in queue_section
    assert "taken out by hand" in queue_section
    assert "put back" in queue_section
    assert "DRE-6640" in queue_section
