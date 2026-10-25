"""A waiting proof row re-checks itself on a schedule (DRE-7951).

A record row reading `Not observed. waiting for <event>` once held its card
until a person posted `🔬 proof-observed` — unless the row carried a
`[not-before=<T>]` stamp (DRE-7316). DRE-6678's nine rows waited for "the
morning of October 11" that way, and nothing alarmed or lifted the hold.

Pinned here, through the dispatcher's real sweep with injected reads:

  * every waiting hold is time-only: an unstamped row, and one waiting for the
    operator's press, is read as stamped `WAITING_RECHECK_HOURS` after the
    pass that posts the hold, and the `proof-dispatch:` line says when it
    lifts;
  * a prose waiting hold already standing lifts itself that long after it was
    posted, under the pipeline's own `🔬 proof-observed`;
  * a lift over rows nobody stamped dispatches a `re-check` — once per record
    head — that spends neither the re-run budget nor the timed one;
  * re-checks have their own budget, `RECHECK_BUDGET` per record pull request
    across every head, and then one hold for the operator, which alarms;
  * a spent re-run budget never strands a waiting row;
  * timed re-run 2 of 2 says it is the last (`LAST_TIMED_REASON`);
  * DRE-6678 replayed: three passes on the evening of 2026-10-10 dispatch
    nothing, and the first pass at the lift time dispatches one `re-check`,
    with no person's `🔬 proof-observed` anywhere in the thread.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_record  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
import spoken_thread  # noqa: E402
from test_proof_dispatch import (  # noqa: E402
    HEAD, MET_ROW, NOT_MET_ROW, NOW, PERSON, REPO, SHA7, SENT_BACK_FIXTURES,
    SHA7_6678, STAMP, VIEWER, WAITING_ROW, Board, Harness, _declined,
    _lifts, _review, comment, fake_voices, gate_rerun_receipt, hold, lane_card,
    receipt, record, record_md, rerun_receipt, state, time_hold, verdict,
)

CARD = "DRE-5930"
RECHECK = f"re-check of the waiting rows at {SHA7}"
TWELVE = timedelta(hours=12)
PRESS = "set the sandbox project's Builders to Sonnet 5.5"
PRESS_ROW = ("The sandbox project's Builders run on Sonnet 5.5",
             f"Not observed. waiting for the operator's press: {PRESS}")
STAMPED_ROW = ("Seven days of arrivals show only the CEO's decisions in Green Light",
               f"Not observed. waiting for the window that closes 2026-10-17 18:59 PT "
               f"[not-before={STAMP}]: the seven days' arrivals, counted")
#: 2026-10-18 02:45 UTC, 19:45 PT: after `STAMP`.
AFTER_STAMP = datetime(2026, 10, 18, 2, 45, tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def node(body: str, when: datetime, user: str = VIEWER) -> dict:
    return {"body": body, "createdAt": _iso(when), "user": {"id": user}}


def _thread(*extra) -> list:
    """A first run that opened the record, then whatever `extra` adds."""
    nodes = [receipt(3000), comment("⏳ 5/5 PR opened", 2500), *extra]
    return sorted(nodes, key=lambda c: c["createdAt"])


def recheck_receipt(minutes_ago: float, sha7: str = SHA7) -> dict:
    return receipt(minutes_ago, reason=f"re-check of the waiting rows at {sha7}",
                   count="re-check")


def stamped_hold(minutes_ago: float) -> dict:
    """The time-only hold `_waiting` posts for `STAMPED_ROW`."""
    return hold(proof_record._cut(STAMPED_ROW[0], 70), f"not-before={STAMP}",
                minutes_ago)


def timed_receipt(minutes_ago: float, n: int) -> dict:
    return receipt(minutes_ago, reason=f"timed re-run after not-before 2026-10-16 "
                                       f"19:30 PT at {SHA7}",
                   count=f"timed re-run {n} of 2")


def _pass(monkeypatch, *, thread, now, text, pr=None, run="finished", live=True):
    board = Board(review=[_review(window=thread)], threads={CARD: thread})
    h = Harness(monkeypatch, board, records={CARD: pr or _declined()},
                texts={900: text},
                states={CARD: state(run, dispatches=1,
                                    record={"number": 900, "state": "open"})})
    tally = h.sweep(live=live, now=now)
    return h, tally


def _said(capsys, ident: str = CARD) -> list:
    return [line for line in capsys.readouterr().out.splitlines() if ident in line]


def _hold_time(observed: str, needs: str) -> datetime | None:
    return proof_record.time_only(linear_ops.proof_waiting_line(observed, needs))


def _receipt_line(h) -> str:
    return h.posted[-1][1].split("\n", 1)[0]


def _pt(when: datetime) -> str:
    return spoken_thread.pacific_label(when.isoformat())


# --------------------------------------------------------------------------- #
# the contract shared with the sibling cards                                   #
# --------------------------------------------------------------------------- #


def test_the_shared_constants():
    assert proof_dispatch.WAITING_RECHECK_HOURS == 12
    assert proof_dispatch.RECHECK_REASON == "re-check of the waiting rows at {sha7}"
    assert proof_dispatch.RECHECK_COUNT == "re-check"
    assert proof_dispatch.LAST_TIMED_REASON == (
        "last timed re-run after not-before {at} at {sha7}")


def test_the_receipt_reader_parses_a_re_check():
    body = (f"🔬 proof-run: dispatched a proof run at 2026-10-11 07:22 PT — "
            f"{RECHECK} (re-check)")
    assert proof_run_state.receipt(body) == proof_run_state.Receipt(
        RECHECK, "re-check", "2026-10-11 07:22 PT")


# --------------------------------------------------------------------------- #
# 1. every waiting hold lifts itself                                           #
# --------------------------------------------------------------------------- #


def test_an_unstamped_waiting_row_is_held_until_twelve_hours_after_the_pass(
        monkeypatch, capsys):
    h, tally = _pass(monkeypatch, thread=_thread(), now=NOW,
                     text=record_md(MET_ROW, WAITING_ROW))
    lines = _said(capsys)
    assert h.fired == [] and h.posted == [] and tally.held == 1
    ((ident, observed, needs),) = h.holds
    assert ident == CARD
    assert observed == proof_record._cut(WAITING_ROW[0], 70)
    # The needs half is the one clause and nothing else, so it reads time-only.
    assert needs.startswith(linear_ops.NOT_BEFORE) and " " not in needs
    assert _hold_time(observed, needs) == NOW + TWELVE
    assert any(line.endswith(f"— lifts at {_pt(NOW + TWELVE)}") for line in lines), lines


def test_the_hold_is_posted_once_and_the_next_pass_says_when_it_lifts(
        monkeypatch, capsys):
    text = record_md(MET_ROW, WAITING_ROW)
    h, _ = _pass(monkeypatch, thread=_thread(), now=NOW, text=text)
    ((_, observed, needs),) = h.holds
    capsys.readouterr()
    held = _thread(node(linear_ops.proof_waiting_line(observed, needs), NOW))
    later = NOW + timedelta(hours=3)
    h, tally = _pass(monkeypatch, thread=held, now=later, text=text)
    lines = _said(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and tally.held == 1
    assert any(line.endswith(f"— lifts at {_pt(NOW + TWELVE)}") for line in lines), lines


def test_a_standing_prose_waiting_hold_lifts_itself_twelve_hours_after_its_post(
        monkeypatch, capsys):
    """The hold `_waiting` posted before this change: prose, naming the event.
    It lifts itself `WAITING_RECHECK_HOURS` after it was posted, under the
    pipeline's own `🔬 proof-observed`, and a re-check is dispatched."""
    text = record_md(MET_ROW, WAITING_ROW)
    posted = NOW - timedelta(minutes=60)
    prose = (proof_record._cut(WAITING_ROW[0], 70),
             "the 2026-10-14 release: the tag moves")
    thread = _thread(node(linear_ops.proof_waiting_line(*prose), posted))

    h, tally = _pass(monkeypatch, thread=thread, now=posted + TWELVE - timedelta(hours=1),
                     text=text)
    lines = _said(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and tally.held == 1
    assert any(f"re-run: held by {linear_ops.proof_waiting_line(*prose)} — lifts at "
               f"{_pt(posted + TWELVE)}" in line for line in lines), lines

    h, tally = _pass(monkeypatch, thread=thread, now=posted + TWELVE, text=text)
    (note,) = _lifts(h)
    assert proof_dispatch.LIFTED_BY in note
    assert f"(read at {_pt(posted + TWELVE)})" in note
    assert h.fired == [(CARD, REPO, RECHECK, "proof-execute")]
    assert h.holds == [] and tally.dispatched == 1
    assert _receipt_line(h).endswith(f" — {RECHECK} (re-check)")
    # The note is the pipeline's, and the one hold reader reads it as the discharge.
    after = thread + [node(note, posted + TWELVE)]
    assert proof_record.open_holds(fake_voices(after, VIEWER, card=CARD)) == []


def test_a_row_waiting_for_the_operators_press_gets_a_time_hold_and_a_re_check(
        monkeypatch, capsys):
    text = record_md(MET_ROW, PRESS_ROW)
    h, tally = _pass(monkeypatch, thread=_thread(), now=NOW, text=text)
    ((_, observed, needs),) = h.holds
    assert _hold_time(observed, needs) == NOW + TWELVE
    assert not needs.startswith(proof_dispatch.OPERATOR_PRESS)
    # …so the sweep reads it as an event's wait, never the operator's.
    line = linear_ops.proof_waiting_line(observed, needs)
    clock, _, why_not = reconcile._held_for_operator(lane_card(
        CARD, state="In Review", window=[node(line, NOW - timedelta(hours=30))]))
    assert clock is None and "waits on an event" in why_not

    held = _thread(node(line, NOW))
    capsys.readouterr()
    h, tally = _pass(monkeypatch, thread=held, now=NOW + TWELVE, text=text)
    assert len(_lifts(h)) == 1
    assert [f[2] for f in h.fired] == [RECHECK]
    assert _receipt_line(h).endswith("(re-check)")


def test_the_operators_press_is_no_longer_held_for_the_operator():
    assert proof_dispatch.OPERATOR_PRESS not in proof_dispatch.OPERATOR_NEEDS


# --------------------------------------------------------------------------- #
# 2. a re-check spends no budget, and there is one per record head             #
# --------------------------------------------------------------------------- #


def test_re_check_and_timed_receipts_never_count_toward_the_re_run_budget(
        monkeypatch, capsys):
    spent = (recheck_receipt(1800, sha7=HEAD[:6] + "f"), comment("⏳ 5/5 amended", 1700),
             timed_receipt(1500, 1), comment("⏳ 5/5 amended", 1400),
             timed_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    assert proof_run_state.receipt(spent[0]["body"]).count == "re-check"
    h, _ = _pass(monkeypatch, thread=_thread(*spent), now=NOW,
                 text=record_md(MET_ROW, NOT_MET_ROW))
    assert [f[2] for f in h.fired] == [f"re-run after the gate's decline at {SHA7}"]
    assert _receipt_line(h).endswith("(re-run 1 of 2)")


def test_a_second_re_check_at_the_same_head_is_refused_and_the_rows_held_again(
        monkeypatch, capsys):
    """A head that already had its re-check, its rows still unstamped: the
    time lift dispatches nothing, and the rows are held again."""
    text = record_md(MET_ROW, WAITING_ROW)
    observed = proof_record._cut(WAITING_ROW[0], 70)
    thread = _thread(recheck_receipt(400), comment("⏳ 5/5 re-checked", 380),
                     time_hold("2026-10-06T08:00-07:00", 60, observed=observed))
    h, tally = _pass(monkeypatch, thread=thread, now=NOW, text=text)
    lines = _said(capsys)
    assert len(_lifts(h)) == 1
    assert h.fired == [] and tally.dispatched == 0 and tally.held == 1
    ((_, again, needs),) = h.holds
    assert again == observed and _hold_time(again, needs) == NOW + TWELVE
    assert any("re-check" in line and f"lifts at {_pt(NOW + TWELVE)}" in line
               for line in lines), lines


def test_a_head_that_had_its_re_check_takes_the_timed_path_once_stamped(
        monkeypatch, capsys):
    text = record_md(MET_ROW, STAMPED_ROW)
    thread = _thread(recheck_receipt(400), comment("⏳ 5/5 re-checked", 380),
                     stamped_hold(60))
    h, _ = _pass(monkeypatch, thread=thread, now=AFTER_STAMP, text=text)
    assert [f[2] for f in h.fired] == [
        f"timed re-run after not-before 2026-10-17 19:30 PT at {SHA7}"]
    assert _receipt_line(h).endswith("(timed re-run 1 of 2)")


def test_a_re_check_spends_neither_budget_with_both_spent(monkeypatch, capsys):
    """Both re-runs and both timed re-runs spent: a lift over unstamped rows
    still dispatches its re-check, and the `re-run n of 2` count is unchanged."""
    spent = (gate_rerun_receipt(2000, 1), comment("⏳ 5/5 amended", 1900),
             rerun_receipt(1600, 2), comment("⏳ 5/5 amended", 1500),
             timed_receipt(1200, 1), comment("⏳ 5/5 amended", 1100),
             timed_receipt(800, 2), comment("⏳ 5/5 amended", 700))
    observed = proof_record._cut(WAITING_ROW[0], 70)
    thread = _thread(*spent, time_hold("2026-10-06T08:00-07:00", 60, observed=observed))
    h, tally = _pass(monkeypatch, thread=thread, now=NOW,
                     text=record_md(MET_ROW, WAITING_ROW))
    assert [f[2] for f in h.fired] == [RECHECK]
    assert _receipt_line(h).endswith(f" — {RECHECK} (re-check)")
    assert tally.dispatched == 1


# --------------------------------------------------------------------------- #
# 2b. re-checks have their own budget, across heads                            #
# --------------------------------------------------------------------------- #
#
# A re-check amends the record, so the record's head moves, and "one per head"
# alone would re-check a row whose event is days away twice a day until it
# came — each a full proof run, and nothing alarming. `RECHECK_BUDGET` per
# record pull request, then one hold for the operator.

RECHECK_HELD = (proof_dispatch.RECHECK_EXHAUSTED_OBSERVED,
                proof_dispatch.RECHECK_EXHAUSTED_NEEDS)


def _rechecked(n: int) -> tuple:
    """`n` re-checks, each at an earlier head of its own, each followed by
    the amend that moved the head on."""
    nodes = []
    for i in range(n):
        nodes += [recheck_receipt(1800 - 300 * i, sha7=f"{i + 1}" * 7),
                  comment("⏳ 5/5 re-checked", 1750 - 300 * i)]
    return tuple(nodes)


def _lifted_waiting(row) -> dict:
    return time_hold("2026-10-06T08:00-07:00", 60,
                     observed=proof_record._cut(row[0], 70))


def test_the_re_check_budget():
    assert proof_dispatch.RECHECK_BUDGET == 3
    assert proof_dispatch.RECHECK_EXHAUSTED_NEEDS in proof_dispatch.OPERATOR_NEEDS


@pytest.mark.parametrize("row", [WAITING_ROW, PRESS_ROW], ids=["event", "press"])
def test_a_record_that_spent_its_re_checks_is_held_for_the_operator(
        monkeypatch, capsys, row):
    budget = proof_dispatch.RECHECK_BUDGET
    thread = _thread(*_rechecked(budget), _lifted_waiting(row))
    h, tally = _pass(monkeypatch, thread=thread, now=NOW, text=record_md(MET_ROW, row))
    lines = _said(capsys)
    assert len(_lifts(h)) == 1
    assert h.fired == [] and tally.dispatched == 0 and tally.held == 1
    assert h.holds == [(CARD, *RECHECK_HELD)]
    assert any(f"re-check budget — {budget} re-checks on #900" in line
               and "held for an operator" in line for line in lines), lines


def test_one_re_check_short_of_the_budget_still_re_checks(monkeypatch, capsys):
    thread = _thread(*_rechecked(proof_dispatch.RECHECK_BUDGET - 1),
                     _lifted_waiting(WAITING_ROW))
    h, tally = _pass(monkeypatch, thread=thread, now=NOW,
                     text=record_md(MET_ROW, WAITING_ROW))
    assert [f[2] for f in h.fired] == [RECHECK]
    assert h.holds == [] and tally.dispatched == 1


def test_re_checks_before_the_record_opened_spend_none_of_it(monkeypatch, capsys):
    thread = _thread(*_rechecked(proof_dispatch.RECHECK_BUDGET),
                     _lifted_waiting(WAITING_ROW))
    h, tally = _pass(monkeypatch, thread=thread, now=NOW, pr=_declined(opened=200),
                     text=record_md(MET_ROW, WAITING_ROW))
    assert [f[2] for f in h.fired] == [RECHECK]
    assert tally.dispatched == 1


def test_the_spent_re_check_hold_stands_and_fires_nothing_after(monkeypatch, capsys):
    """The next pass, twelve hours on: the operator's hold is no time hold,
    so nothing lifts it and nothing is posted again."""
    text = record_md(MET_ROW, WAITING_ROW)
    thread = _thread(*_rechecked(proof_dispatch.RECHECK_BUDGET),
                     _lifted_waiting(WAITING_ROW))
    h, _ = _pass(monkeypatch, thread=thread, now=NOW, text=text)
    (note,) = _lifts(h)
    after = thread + [node(note, NOW),
                      node(linear_ops.proof_waiting_line(*RECHECK_HELD), NOW)]
    capsys.readouterr()
    h, tally = _pass(monkeypatch, thread=after, now=NOW + TWELVE, text=text)
    lines = _said(capsys)
    assert h.fired == [] and h.posted == [] and h.holds == [] and tally.held == 1
    assert any(f"re-run: held by {linear_ops.proof_waiting_line(*RECHECK_HELD)}"
               in line for line in lines), lines


def test_an_unpressed_press_alarms_through_the_spent_re_check_hold():
    """The press's own hold waits for a time now; the spent re-checks' hold
    is the operator's, so the sweep's held-for-operator read alarms on it."""
    line = linear_ops.proof_waiting_line(*RECHECK_HELD)
    held = node(line, NOW - timedelta(hours=30))
    clock, kind, why_not = reconcile._held_for_operator(
        lane_card(CARD, state="In Review", window=[held]))
    assert clock is held, why_not
    assert kind == "operator's-reading hold"


# --------------------------------------------------------------------------- #
# 3. a spent re-run budget never strands a waiting row                         #
# --------------------------------------------------------------------------- #

RERUN_HELD = (proof_dispatch.RERUN_EXHAUSTED_OBSERVED,
              f"{proof_dispatch.RERUN_EXHAUSTED_NEEDS} — the record at "
              f"{HEAD[:6]}f names no fix")


def test_both_re_runs_spent_and_a_stamped_row_past_its_time_gets_its_timed_re_run(
        monkeypatch, capsys):
    """The budget's own hold still stands from an earlier head; the waiting
    row's time hold beside it lifts, and the timed re-run goes."""
    spent = (rerun_receipt(1600, 1), comment("⏳ 5/5 amended", 1500),
             rerun_receipt(1200, 2), comment("⏳ 5/5 amended", 1100),
             hold(*RERUN_HELD, 1000))
    thread = _thread(*spent, stamped_hold(60))
    pr = record(verdict("REQUEST_CHANGES", 100))
    h, tally = _pass(monkeypatch, thread=thread, now=AFTER_STAMP, pr=pr,
                     text=record_md(MET_ROW, STAMPED_ROW))
    lines = _said(capsys)
    assert len(_lifts(h)) == 1, lines
    assert [f[2] for f in h.fired] == [
        f"timed re-run after not-before 2026-10-17 19:30 PT at {SHA7}"], lines
    assert _receipt_line(h).endswith("(timed re-run 1 of 2)")
    receipts = [proof_run_state.receipt(c["body"]) for c in thread]
    assert sum(1 for r in receipts if r and r.count.startswith("re-run")) == 2
    assert tally.dispatched == 1


# --------------------------------------------------------------------------- #
# 4. the last timed re-run says it is the last                                 #
# --------------------------------------------------------------------------- #


def test_timed_re_run_two_of_two_is_dispatched_with_the_last_reason(monkeypatch, capsys):
    thread = _thread(timed_receipt(800, 1), comment("⏳ 5/5 amended", 700),
                     stamped_hold(60))
    h, _ = _pass(monkeypatch, thread=thread, now=AFTER_STAMP,
                 text=record_md(MET_ROW, STAMPED_ROW))
    last = f"last timed re-run after not-before 2026-10-17 19:30 PT at {SHA7}"
    assert [f[2] for f in h.fired] == [last]
    assert _receipt_line(h).endswith(f" — {last} (timed re-run 2 of 2)")
    parsed = proof_run_state.receipt(h.posted[-1][1])
    assert parsed.reason == last and parsed.count == "timed re-run 2 of 2"


def test_timed_re_run_one_of_two_keeps_its_reason(monkeypatch, capsys):
    h, _ = _pass(monkeypatch, thread=_thread(stamped_hold(60)), now=AFTER_STAMP,
                 text=record_md(MET_ROW, STAMPED_ROW))
    assert [f[2] for f in h.fired] == [
        f"timed re-run after not-before 2026-10-17 19:30 PT at {SHA7}"]


# --------------------------------------------------------------------------- #
# DRE-6678 replayed: no person's note, no re-run spent before the event        #
# --------------------------------------------------------------------------- #


def _replay_6678() -> tuple:
    doc = json.loads((SENT_BACK_FIXTURES / "dre-6678.json").read_text(encoding="utf-8"))
    thread = [node(n["body"], datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")),
                   VIEWER if n["by_pipeline"] else PERSON)
              for n in doc["thread"]]
    text = (SENT_BACK_FIXTURES / f"record-{SHA7_6678}.md").read_text(encoding="utf-8")
    return doc, thread, text


def _replayed_pass(monkeypatch, doc, thread, text, now):
    ident = doc["card"]
    board = Board(review=[lane_card(ident, state=doc["lane"], window=thread)],
                  threads={ident: list(thread)})
    h = Harness(monkeypatch, board, records={ident: doc["pr"]},
                texts={doc["pr"]["number"]: text},
                states={ident: state("finished", dispatches=1,
                                     record={"number": 981, "state": "open"})})
    tally = h.sweep(now=now)
    # What the pass wrote joins the thread the next pass reads.
    for _, observed, needs in h.holds:
        thread.append(node(linear_ops.proof_waiting_line(observed, needs), now))
    for _, body in h.posted:
        thread.append(node(body, now))
    return h, tally


def test_dre_6678_replayed_waits_the_evening_and_re_checks_at_the_lift_time(
        monkeypatch, capsys):
    doc, thread, text = _replay_6678()
    start = datetime.fromisoformat(doc["as_of"].replace("Z", "+00:00"))
    lift = None
    for minutes in (0, 30, 60):     # 19:22, 19:52 and 20:22 PT on 2026-10-10
        now = start + timedelta(minutes=minutes)
        assert _pt(now).startswith("2026-10-10 ")
        h, tally = _replayed_pass(monkeypatch, doc, thread, text, now)
        lines = _said(capsys, doc["card"])
        assert h.fired == [] and tally.dispatched == 0 and tally.held == 1, lines
        assert not any(proof_run_state.receipt(b) for _, b in h.posted)
        if minutes == 0:
            ((_, observed, needs),) = h.holds
            lift = _hold_time(observed, needs)
            assert lift == start + TWELVE
        else:
            assert h.holds == [] and h.posted == []
        assert any(f"lifts at {_pt(lift)}" in line for line in lines), lines

    h, tally = _replayed_pass(monkeypatch, doc, thread, text, lift - timedelta(minutes=1))
    assert h.fired == [] and h.posted == []
    capsys.readouterr()

    h, tally = _replayed_pass(monkeypatch, doc, thread, text, lift)
    recheck = f"re-check of the waiting rows at {SHA7_6678}"
    assert [(f[0], f[2]) for f in h.fired] == [("DRE-6678", recheck)]
    assert tally.dispatched == 1
    assert len(_lifts(h)) == 1
    assert _receipt_line(h).endswith(f" — {recheck} (re-check)")
    # No re-run was spent, and no person posted a `🔬 proof-observed`.
    counts = [r.count for c in thread if (r := proof_run_state.receipt(c["body"]))]
    assert not any(c.startswith("re-run") for c in counts), counts
    assert not any(c["user"]["id"] == PERSON
                   and c["body"].startswith(proof_record.OBSERVED_MARK) for c in thread)
