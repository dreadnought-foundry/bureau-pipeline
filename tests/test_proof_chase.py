"""A proof waiting on an event in Hand-work is chased once (DRE-7967).

A `🔬 proof-waiting` hold on a `PROOF:` card in `Hand-work` whose needs half
waits on an event — not a time, not a person's press — once stopped the proof
dispatcher for good: nothing looked again, so the event could happen and the
card still waited. Once such a hold has stood for the `proof-waiting` row's
`chase_after_hours` (`config/hold-deadlines.json`, DRE-7964), the dispatcher
starts ONE proof run on the card, counted `chase`, to observe whether the event
has happened.

Pinned here, through the dispatcher's real sweep with injected reads:

  * Hand-work only: a 25-hour event hold is chased, a 23-hour one is named
    held on the lane read as before; In Review is DRE-7951's re-check and
    Green Light is never chased;
  * the conditions a chase still reads (2, 3, 5 and 7) and the one it does
    not (6, the first-run budget);
  * what is not an event hold, `_OWN_NEEDS` discovered off the module;
  * once per standing hold;
  * the `chase` count, parsed as a receipt, spending no budget;
  * the three hold rows of `config/pipeline-acts.json` name
    `config/hold-deadlines.json`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import hold_deadline  # noqa: E402
import linear_ops  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_run_state  # noqa: E402
import spoken_thread  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    CAUSE_HOLD, HISTORY_HOLD, NOW, REPO, Board, Harness, _review, _sent_back,
    comment, detail, hold, lane_card, promoted, receipt, record, state, verdict,
)

CARD = "DRE-5930"
OBSERVED = "the weekly report shows the new column"
EVENT = "the weekly report of 2026-10-11"
HOUR = 60


def event_hold(hours_ago: float, *, needs: str = EVENT) -> dict:
    return hold(OBSERVED, needs, hours_ago * HOUR)


def _reason(node: dict) -> str:
    return proof_dispatch.CHASE_REASON.format(
        at=spoken_thread.pacific_label(node["createdAt"]))


def chase_receipt(hours_ago: float, of: dict) -> dict:
    return receipt(hours_ago * HOUR, reason=_reason(of), count="chase")


def _hand(nodes, *, window=None, ident=CARD) -> Board:
    """A Hand-work card whose thread is `nodes`, its window the same unless
    told otherwise."""
    nodes = [promoted(5000), *nodes]
    nodes.sort(key=lambda c: c["createdAt"])
    shown = nodes if window is None else window
    return Board(hand=[lane_card(ident, window=shown)], threads={ident: nodes})


def _lines(capsys) -> list:
    return capsys.readouterr().out.splitlines()


def _about(lines, ident=CARD) -> list:
    return [line for line in lines if ident in line]


def _chased(h) -> list:
    return [f for f in h.fired if (f[2] or "").startswith("chase of the event hold")]


# --------------------------------------------------------------------------- #
# Hand-work, the first-run branch                                              #
# --------------------------------------------------------------------------- #


def test_a_25_hour_event_hold_in_hand_work_is_chased_once(monkeypatch, capsys):
    held = event_hold(25)
    board = _hand([held])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    lines = _about(_lines(capsys))
    # Not named held off the lane read: the thread read decides.
    assert ("thread", CARD) in board.reads
    assert not any("condition 4" in line for line in lines)
    assert h.fired == [(CARD, REPO, _reason(held), "proof-execute")]
    assert tally.dispatched == 1
    assert len(h.posted) == 1
    first = h.posted[0][1].split("\n", 1)[0]
    assert first.endswith(f" — {_reason(held)} (chase)")
    parsed = proof_run_state.receipt(h.posted[0][1])
    assert parsed is not None and parsed.count == "chase"
    # The chase line, before the eligible and dispatched lines.
    at = spoken_thread.pacific_label(held["createdAt"])
    chase = (f"proof-dispatch: {CARD} — chase: the event hold of {at} has stood "
             f"25h, past its 24h — "
             f"{proof_record._cut(linear_ops.proof_waiting_line(OBSERVED, EVENT), 120)}")
    assert chase in lines
    eligible = next(i for i, line in enumerate(lines) if "eligible:" in line)
    dispatched = next(i for i, line in enumerate(lines) if "dispatched:" in line)
    assert lines.index(chase) < eligible < dispatched
    # No 🔬 proof-observed, no new hold, no lift.
    assert h.holds == []
    assert not any(proof_record.OBSERVED_STEM in body for _, body in h.posted)


def test_a_23_hour_event_hold_is_named_held_as_today(monkeypatch, capsys):
    board = _hand([event_hold(23)])
    h = Harness(monkeypatch, board)
    tally = h.sweep()
    lines = _about(_lines(capsys))
    assert h.fired == [] and tally.held == 1
    assert any("condition 4 (hold): held by" in line and EVENT in line for line in lines)
    assert ("thread", CARD) not in board.reads
    assert not any("chase:" in line for line in lines)


def test_a_late_event_hold_never_chased_gets_its_one_chase(monkeypatch, capsys):
    held = event_hold(80)
    h = Harness(monkeypatch, _hand([held]))
    h.sweep()
    assert _chased(h) == [(CARD, REPO, _reason(held), "proof-execute")]


def test_the_dry_pass_prints_the_chase_and_writes_nothing(monkeypatch, capsys):
    held = event_hold(25)
    h = Harness(monkeypatch, _hand([held]))
    h.sweep(live=False)
    lines = _lines(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == []
    assert any(line.startswith(f"proof-dispatch: {CARD} — chase: the event hold of ")
               for line in lines)
    assert f"would: dispatch {CARD} — {_reason(held)}" in lines


def test_the_chase_is_read_off_the_thread_when_the_window_shows_no_hold(
        monkeypatch, capsys):
    held = event_hold(30)
    h = Harness(monkeypatch, _hand([held], window=[promoted(5000)]))
    h.sweep()
    assert _chased(h) == [(CARD, REPO, _reason(held), "proof-execute")]


# --------------------------------------------------------------------------- #
# the conditions a chase still reads                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("epic", ["Planning", "Green Light"])
def test_condition_2_refuses_a_chase(monkeypatch, capsys, epic):
    board = _hand([event_hold(25)])
    board.details[CARD] = detail(epic_state=epic)
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 2" in line for line in _about(_lines(capsys)))


def test_condition_3_refuses_a_chase(monkeypatch, capsys):
    board = _hand([event_hold(25)])
    board.details[CARD] = detail(blockers={"DRE-5921": "Done", "DRE-5922": "In Review"})
    h = Harness(monkeypatch, board)
    h.sweep()
    assert h.fired == []
    assert any("condition 3" in line for line in _about(_lines(capsys)))


@pytest.mark.parametrize("run", ["running", "unknown", "someone-else", "finished"])
def test_condition_5_refuses_a_chase(monkeypatch, capsys, run):
    h = Harness(monkeypatch, _hand([event_hold(25)]), states={CARD: state(run)})
    h.sweep()
    assert h.fired == []
    assert any("condition 5" in line for line in _about(_lines(capsys)))


@pytest.mark.parametrize("run", ["none", "never-started", "dead"])
def test_condition_5_a_free_run_is_chased(monkeypatch, capsys, run):
    held = event_hold(25)
    h = Harness(monkeypatch, _hand([held]), states={CARD: state(run)})
    h.sweep()
    assert _chased(h) == [(CARD, REPO, _reason(held), "proof-execute")]


def test_condition_7_a_waiting_release_refuses_a_chase(monkeypatch, capsys):
    h = Harness(monkeypatch, _hand([event_hold(25)]), release="waiting")
    tally = h.sweep()
    assert h.fired == [] and tally.waiting == 1
    assert any("condition 7" in line for line in _about(_lines(capsys)))


def test_a_done_epic_is_chased_as_an_epic_in_progress(monkeypatch, capsys):
    held = event_hold(25)
    board = _hand([held])
    board.details[CARD] = detail(epic_state="Done")
    h = Harness(monkeypatch, board)
    h.sweep()
    assert _chased(h) == [(CARD, REPO, _reason(held), "proof-execute")]


def test_condition_6_is_not_read_for_a_chase(monkeypatch, capsys):
    """Two first-run receipts already on the card: a first run would be held
    on its budget, and a chase is dispatched."""
    spent = [receipt(3000), receipt(2900, reason="second dispatch — run 1 ended "
                                                 "failure with no record",
                                     count="dispatch 2 of 2")]
    held = event_hold(25)
    h = Harness(monkeypatch, _hand([*spent, held]),
                states={CARD: state("dead", dispatches=2)})
    h.sweep()
    assert _chased(h) == [(CARD, REPO, _reason(held), "proof-execute")]
    assert h.holds == []
    assert not any("condition 6" in line for line in _about(_lines(capsys)))


# --------------------------------------------------------------------------- #
# In Review and Green Light are never chased                                   #
# --------------------------------------------------------------------------- #


def _review_pass(monkeypatch, pr, *, event_holds=True):
    thread = _sent_back(event_hold(25))
    board = Board(review=[_review(window=thread)], threads={CARD: thread})
    h = Harness(monkeypatch, board, records={CARD: pr},
                states={CARD: state("finished", dispatches=1,
                                    record={"number": 900, "state": "open"})})
    if not event_holds:
        monkeypatch.setattr(proof_dispatch, "_is_event_hold", lambda line: False)
    h.sweep()
    return h


@pytest.mark.parametrize("pr", [record(verdict("REQUEST_CHANGES", 100)), record()],
                         ids=["a trigger at the head", "no trigger"])
def test_in_review_is_not_chased(monkeypatch, capsys, pr):
    h = _review_pass(monkeypatch, pr)
    lines = _about(_lines(capsys))
    assert _chased(h) == []
    assert not any("chase" in line for line in lines)
    # `rerunning` reads it exactly as it reads it with no chase at all.
    with monkeypatch.context() as again:
        plain = _review_pass(again, pr, event_holds=False)
    assert _about(_lines(capsys)) == lines
    assert plain.fired == h.fired and plain.holds == h.holds


def test_green_light_is_never_chased(monkeypatch, capsys):
    thread = [promoted(5000), event_hold(25)]
    card = lane_card(CARD, state="Green Light", window=thread)
    h = Harness(monkeypatch, Board(green=[card], threads={CARD: thread}))
    h.sweep()
    assert _chased(h) == []
    assert not any("chase" in line for line in _about(_lines(capsys)))


# --------------------------------------------------------------------------- #
# what is not an event hold                                                    #
# --------------------------------------------------------------------------- #


def _own_needs_wordings() -> dict:
    """Every `*_NEEDS` string constant of the module, its fields filled."""
    return {name: re.sub(r"\{[^{}]*\}", "DRE-8001", value)
            for name, value in vars(proof_dispatch).items()
            if name.endswith("_NEEDS") and isinstance(value, str)}


def test_the_own_needs_wordings_are_the_modules():
    names = set(_own_needs_wordings())
    assert {"EXHAUSTED_NEEDS", "RERUN_EXHAUSTED_NEEDS", "GATE_EXHAUSTED_NEEDS",
            "FIX_SHIPPED_NEEDS", "RECHECK_EXHAUSTED_NEEDS"} <= names
    assert len(proof_dispatch._OWN_NEEDS) == len(names) + 1  # and OPERATOR_PRESS


def _line(observed: str, needs: str) -> str:
    return linear_ops.proof_waiting_line(observed, needs)


NOT_EVENTS = {
    "the CEO's press": ("the CEO signs in to the console",
                        "the CEO's press: sign in once"),
    "a cause hold": CAUSE_HOLD,
    "a history hold": HISTORY_HOLD,
    "the operator's press": ("the sandbox runs on Sonnet 5.5",
                             f"{proof_dispatch.OPERATOR_PRESS}: set the Builders"),
    "a fix card to ship": ("the build fails its rows", "DRE-1234 shipped"),
    "two fix cards to ship": ("the build fails its rows", "DRE-1234, DRE-1235 shipped"),
    "a time-only hold still ahead": ("the day of jobs",
                                     "not-before=2026-10-08T08:00-07:00"),
    **{name: ("an observation the dispatcher waits on", wording)
       for name, wording in _own_needs_wordings().items()},
}


def test_an_event_hold_is_one():
    assert proof_dispatch._is_event_hold(_line(OBSERVED, EVENT))


@pytest.mark.parametrize("kind", sorted(NOT_EVENTS))
def test_not_an_event_hold(kind):
    assert not proof_dispatch._is_event_hold(_line(*NOT_EVENTS[kind]))


def test_the_cause_and_history_holds_are_the_parks():
    for kind in ("a cause hold", "a history hold"):
        assert proof_cause_park.is_cause_hold(_line(*NOT_EVENTS[kind])), kind


@pytest.mark.parametrize("kind", sorted(NOT_EVENTS))
def test_alone_it_is_never_chased(monkeypatch, capsys, kind):
    h = Harness(monkeypatch, _hand([hold(*NOT_EVENTS[kind], 25 * HOUR)]))
    h.sweep()
    assert _chased(h) == []
    assert not any("chase:" in line for line in _about(_lines(capsys)))


@pytest.mark.parametrize("order", ["older", "newer"])
@pytest.mark.parametrize("kind", sorted(NOT_EVENTS))
def test_beside_an_event_hold_it_stops_the_chase(monkeypatch, capsys, kind, order):
    other = hold(*NOT_EVENTS[kind], (30 if order == "older" else 25) * HOUR)
    event = event_hold(28 if order == "older" else 30)
    h = Harness(monkeypatch, _hand([other, event]))
    h.sweep()
    assert h.fired == []
    assert not any("chase:" in line for line in _about(_lines(capsys)))


def test_a_needs_wording_added_to_the_module_is_discovered(monkeypatch):
    added = "an operator reading the {what} the new branch wrote"
    line = _line(OBSERVED, "an operator reading the trace the new branch wrote")
    assert proof_dispatch._is_event_hold(line)
    monkeypatch.setattr(proof_dispatch, "NEWLY_ADDED_NEEDS", added, raising=False)
    monkeypatch.setattr(proof_dispatch, "_OWN_NEEDS", proof_dispatch._own_needs())
    assert not proof_dispatch._is_event_hold(line)


# --------------------------------------------------------------------------- #
# once per standing hold                                                       #
# --------------------------------------------------------------------------- #


def test_a_chased_hold_is_not_chased_again(monkeypatch, capsys):
    held = event_hold(30)
    h = Harness(monkeypatch, _hand([held, chase_receipt(5, held)]),
                states={CARD: state("dead")})
    h.sweep()
    assert h.fired == []


def test_a_chased_hold_is_not_chased_again_off_the_thread(monkeypatch, capsys):
    """The window shows only the hold; the thread shows the chase."""
    held = event_hold(30)
    h = Harness(monkeypatch, _hand([held, chase_receipt(5, held)],
                                   window=[promoted(5000), held]),
                states={CARD: state("dead")})
    h.sweep()
    assert h.fired == []
    assert any("condition 4" in line for line in _about(_lines(capsys)))


@pytest.mark.parametrize("shown", ["whole", "copy only"])
def test_a_copy_of_the_hold_buys_no_second_chase(monkeypatch, capsys, shown):
    held, copy = event_hold(60), event_hold(26)
    nodes = [held, chase_receipt(40, held), copy]
    window = None if shown == "whole" else [promoted(5000), copy]
    h = Harness(monkeypatch, _hand(nodes, window=window), states={CARD: state("dead")})
    h.sweep()
    assert h.fired == []


@pytest.mark.parametrize("count,reason", [
    ("re-check", "re-check of the waiting rows at c0ffee1"),
    ("dispatch 1 of 2", "first proof run"),
    ("chase", "chase of the event hold of 2026-10-01 08:00 PT"),
])
def test_a_receipt_newer_than_the_newest_event_hold_stops_the_chase(
        monkeypatch, capsys, count, reason):
    held = event_hold(30)
    newer = receipt(10 * HOUR, reason=reason, count=count)
    h = Harness(monkeypatch, _hand([held, newer], window=[promoted(5000), held]),
                states={CARD: state("dead")})
    h.sweep()
    assert h.fired == []


# --------------------------------------------------------------------------- #
# the count, and the budgets it spends none of                                 #
# --------------------------------------------------------------------------- #

CHASE_LINE = ("🔬 proof-run: dispatched a proof run at 09:14 PT — chase of the "
              "event hold of 08:00 PT (chase)")


def test_the_chase_receipt_parses():
    got = proof_run_state.receipt(CHASE_LINE)
    assert got == proof_run_state.Receipt("chase of the event hold of 08:00 PT",
                                          "chase", "09:14 PT")
    assert proof_dispatch.CHASE_COUNT == "chase"
    assert proof_dispatch.CHASE_REASON == "chase of the event hold of {at}"


def _read(path):
    return []


def test_a_chase_receipt_spends_no_first_run_dispatch():
    nodes = [comment(CHASE_LINE, 600)]
    got = proof_run_state.reading(REPO, CARD, nodes, "pipeline-key-user",
                                  read=_read, now=NOW)
    assert got.dispatches == 0
    assert [r.count for r in got.receipts] == ["chase"]
    # It anchors the reading as a real run.
    assert got.state == "never-started"


def test_the_first_run_after_a_chase_is_dispatch_1_of_2(monkeypatch, capsys):
    held = event_hold(30)
    nodes = [held, chase_receipt(20, held),
             comment(linear_ops.proof_observed_line("the report showed it at 09:00 PT"),
                     60, user="a-person")]
    h = Harness(monkeypatch, _hand(nodes))
    h.run_state = lambda repo, ident, comments, viewer, *, read, now: (
        proof_run_state.reading(repo, ident, comments, viewer, read=_read, now=now))
    h.sweep()
    assert h.fired == [(CARD, REPO, "first proof run", "proof-execute")]
    assert h.posted[0][1].split("\n", 1)[0].endswith("(dispatch 1 of 2)")


def test_a_chase_receipt_spends_no_rerun(monkeypatch, capsys):
    thread = _sent_back(receipt(200, reason="chase of the event hold of "
                                            "2026-10-05 08:00 PT", count="chase"))
    assert proof_run_state.receipt(thread[-1]["body"]) is not None
    board = Board(review=[_review(window=thread)], threads={CARD: thread})
    h = Harness(monkeypatch, board, records={CARD: record(verdict("REQUEST_CHANGES", 100))},
                states={CARD: state("finished", dispatches=1,
                                    record={"number": 900, "state": "open"})})
    h.sweep()
    assert [f[0] for f in h.fired] == [CARD]
    assert h.posted[0][1].split("\n", 1)[0].endswith("(re-run 1 of 2)")


def test_the_chase_count_is_none_of_the_counted_ones():
    count = proof_dispatch.CHASE_COUNT
    assert not count.startswith(("re-run", "timed re-run"))
    assert count not in (proof_dispatch.RECHECK_COUNT, proof_dispatch.REARM_COUNT,
                         proof_dispatch.RETURN_COUNT)
    assert not proof_dispatch.CHASE_REASON.startswith(proof_run_state.FIRST_RUN)


# --------------------------------------------------------------------------- #
# the three hold rows of config/pipeline-acts.json, and the doc                #
# --------------------------------------------------------------------------- #


def test_the_hold_rows_name_the_deadlines_file():
    acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    rows = {row["tag"]: row for row in acts["acts"] if row.get("tag") in hold_deadline.TAGS}
    assert set(rows) == set(hold_deadline.TAGS)
    for tag, row in rows.items():
        assert row["cadence_s"] is None, tag
        assert "config/hold-deadlines.json" in row["cadence_why"], tag
        assert "declares no bound" not in row["cadence_why"], tag


def test_the_doc_describes_the_chase():
    doc = (ROOT / "docs" / "pipeline-acts.md").read_text()
    start = doc.index("## The row that starts a proof — `🔬 proof-run` (DRE-5926)")
    end = doc.find("\n## ", start + 1)
    section = doc[start:end if end != -1 else None]
    for phrase in ("DRE-7967", "(chase)", "Hand-work", "In Review", "re-check",
                   "Green Light", "once"):
        assert phrase in section, phrase
