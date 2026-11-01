"""The proof dispatcher asks the operator nothing about a record's rows (DRE-7953).

Since DRE-7952 a record whose every unmet row is a gap — a cause row or a
history row — merges on its approved record and closes its PROOF card with one
fix card. So the dispatcher stops asking about those rows: under the gate's
decline it neither holds the card nor asks a question, refuses the re-run that
would read the same thing, and leaves the record to the gate.

And a record whose two re-runs are spent on rows the build really fails —
every unmet row a plain `Not met.` naming no fix — files one fix card through
`proof_gap.file_fix_card(kind="defect")` and waits for it to ship. Its hold
reads `— needs <DRE-n> shipped`, an event wait the sweep never alarms; once
the card is Done, merged and live, DRE-7740's re-arm gives one more run.

The critic's spent trigger (`RERUN_EXHAUSTED_*`) is unchanged and alarms.

A fix already filed against the proof — a blocker related after its first
`🔬 proof-run` receipt, not Canceled or a duplicate — is held on and never
filed again (DRE-8251), replayed off agent-bureau's DRE-8162 and its record
#4242: pipeline lines, relation rows, card states, descriptions written here,
pull request rows and times — no person's name or email.

Every fixture is `tests/test_proof_dispatch.py`'s, imported: the board, the
harness, its recorded writes and its filer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import proof_cause_park  # noqa: E402
import proof_dispatch  # noqa: E402
import proof_gap  # noqa: E402
import proof_record  # noqa: E402
import proof_run_state  # noqa: E402
import reconcile  # noqa: E402
import test_proof_dispatch as tpd  # noqa: E402 — the fixtures, imported
import test_proof_dispatch_blocker_rearm as rearm  # noqa: E402 — the relations board

SHA7 = tpd.SHA7
IDENT = "DRE-5930"
CARD = tpd.DEFECT_CARD

#: Two cause rows and a history row: every unmet row a gap (DRE-7952).
GAPS = tpd.record_md(tpd.MET_ROW, tpd.CAUSE_ROW, tpd.GONE_ROW, tpd.HISTORY_ROW)
LEFT = (f"re-run: every unmet row of the record at {SHA7} is a gap it closes "
        "with — left to the gate, never re-run")

#: Two plain `Not met.` rows naming no fix.
PLAIN_ROW = ("The banner names the release", "Not met. the banner still reads v40")
PLAIN = tpd.record_md(tpd.MET_ROW, tpd.NOT_MET_ROW, PLAIN_ROW)
#: The hold a spent budget on a failing build posts.
SHIPPED = (tpd.GATE_HELD[0], f"{CARD} shipped")


def _pass(monkeypatch, *, thread, text, pr=None, live=True, filed=None,
          fixes=None, release="ready", prs=None, run="finished"):
    card = tpd._review(IDENT, window=thread)
    board = tpd.Board(review=[card], threads={IDENT: thread}, fixes=fixes)
    moves = tpd._Moves(monkeypatch)
    h = tpd.Harness(monkeypatch, board, records={IDENT: pr or tpd._declined()},
                    texts={900: text}, filed=filed, release=release, prs=prs,
                    states={IDENT: tpd.state(run, dispatches=1,
                                             record={"number": 900, "state": "open"})})
    tally = h.sweep(live=live)
    return h, tally, moves


def _lines(capsys) -> list:
    return tpd._about(tpd._lines(capsys), IDENT)


# --------------------------------------------------------------------------- #
# 1. a record whose unmet rows are all gaps is left to the gate                #
# --------------------------------------------------------------------------- #


def test_gaps_two_cause_rows_and_a_history_row_get_no_hold_no_question_no_dispatch(
        monkeypatch, capsys):
    h, tally, moves = _pass(monkeypatch, thread=tpd._sent_back(), text=GAPS)
    lines = _lines(capsys)
    assert h.fired == [] and h.holds == [] and h.posted == [] and moves.log == []
    assert h.filings == []
    assert not any(b.startswith("🙋") for _, b in h.posted)
    assert any(line.endswith(LEFT) for line in lines), lines
    assert tally.dispatched == 0 and tally.failures == []
    assert h.text_reads == [(900, tpd.HEAD)]


def test_gaps_the_old_cause_and_history_holds_are_read_past_and_nothing_is_posted(
        monkeypatch, capsys):
    """A thread the old park and history hold were posted on: neither holds
    the reading, and nothing new is asked on top of them."""
    rows = proof_cause_park.rows_of(GAPS)
    parked = tpd._sent_back(tpd.hold(*proof_cause_park.hold(rows), 80),
                            tpd.comment(proof_cause_park.question(rows), 79),
                            tpd.hold(*tpd.HISTORY_HOLD, 78))
    h, tally, moves = _pass(monkeypatch, thread=parked, text=GAPS)
    lines = _lines(capsys)
    assert h.fired == [] and h.holds == [] and h.posted == [] and moves.log == []
    assert any(line.endswith(LEFT) for line in lines), lines


@pytest.mark.parametrize("text", [
    tpd.record_md(tpd.MET_ROW, tpd.CAUSE_ROW),
    tpd.record_md(tpd.MET_ROW, tpd.HISTORY_ROW),
], ids=["cause-only", "history-only"])
def test_gaps_one_kind_alone_is_left_to_the_gate_too(monkeypatch, capsys, text):
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(), text=text)
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert any(line.endswith(LEFT) for line in _lines(capsys))


def test_gaps_the_dry_run_would_hold_nothing_and_ask_nothing(monkeypatch, capsys):
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(), text=GAPS, live=False)
    lines = _lines(capsys)
    assert h.fired == [] and h.holds == [] and h.posted == []
    assert not any("would: hold" in line or "would: ask" in line for line in lines), lines
    assert any(line.endswith(LEFT) for line in lines), lines


def test_gaps_beside_a_plain_unmet_row_is_re_run_as_before(monkeypatch, capsys):
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(),
                    text=tpd.record_md(tpd.CAUSE_ROW, tpd.HISTORY_ROW, tpd.NOT_MET_ROW))
    assert h.fired == [(IDENT, tpd.REPO, tpd.GATE_RERUN, "proof-execute")]
    assert h.holds == []


def test_gaps_the_critics_findings_are_answered_by_a_re_run(monkeypatch, capsys):
    """Only the gate merges a gap record, and only on the critic's APPROVE:
    a record the critic sent back is never left to the gate, where nothing
    would answer it. Its findings get the re-run, on the critic's budget."""
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(), text=GAPS,
                    pr=tpd._declined(approve=tpd.verdict("REQUEST_CHANGES", 100)))
    assert h.fired == [(IDENT, tpd.REPO, tpd.RERUN, "proof-execute")]
    assert h.holds == [] and not any(b.startswith("🙋") for _, b in h.posted)


def test_gaps_no_operator_wording_is_left_for_the_rows():
    """`HISTORY_NEEDS` is gone, and no wording the dispatcher holds for the
    operator opens the cause park's: those rows are the gate's now. The
    opening stays a constant, for reading the holds posted before."""
    assert not hasattr(proof_dispatch, "HISTORY_NEEDS")
    assert not any(needs.startswith(proof_cause_park.CAUSE_HOLD_OPENING)
                   for needs in proof_dispatch.OPERATOR_NEEDS)
    assert proof_cause_park.CAUSE_HOLD_OPENING == "the operator's decision on"
    assert not hasattr(proof_dispatch, "park_caused")


# --------------------------------------------------------------------------- #
# 3. a spent budget on a build that does not work files a fix card            #
# --------------------------------------------------------------------------- #


def test_defect_a_spent_budget_on_two_plain_rows_files_one_fix_card(monkeypatch, capsys):
    h, tally, moves = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT), text=PLAIN)
    lines = _lines(capsys)
    assert h.fired == [] and moves.log == []
    assert h.filings == [(IDENT, tpd.EPIC, f"https://github.com/{tpd.REPO}/pull/900",
                          [(tpd.NOT_MET_ROW[0], proof_gap.DEFECT),
                           (PLAIN_ROW[0], proof_gap.DEFECT)],
                          tpd.SLUG, proof_gap.DEFECT)]
    assert h.holds == [(IDENT, *SHIPPED)]
    assert h.order.index(("file", IDENT)) < h.order.index(("hold", IDENT))
    assert tally.held == 1 and tally.failures == []
    assert any(CARD in line and "never another" in line for line in lines), lines


def test_defect_the_hold_is_an_event_wait_the_sweep_never_alarms():
    line = linear_ops.proof_waiting_line(*SHIPPED)
    assert line.endswith(f"— needs {CARD} shipped")
    needs = line.partition(" — needs ")[2]
    assert not needs.startswith(proof_dispatch.OPERATOR_NEEDS)
    card = tpd.lane_card(IDENT, state="In Review", window=[tpd.hold(*SHIPPED, 60 * 30)])
    clock, _, why_not = reconcile._held_for_operator(card)
    assert clock is None
    assert why_not == f"its hold waits on an event ({CARD} shipped), not the operator"


def test_defect_the_next_pass_files_nothing_and_posts_nothing(monkeypatch, capsys):
    thread = tpd._sent_back(*tpd.SPENT, tpd.hold(*SHIPPED, 600))
    h, tally, _ = _pass(monkeypatch, thread=thread, text=PLAIN,
                        fixes={CARD: tpd.fix_card(CARD, state_name="In Progress")})
    lines = _lines(capsys)
    assert h.fired == [] and h.holds == [] and h.posted == [] and h.filings == []
    assert tally.held == 1
    # The card named on the hold is read beside the `fix:` cells.
    assert ("fix_card", CARD) in h.board.reads
    assert any(f"the fix {CARD} is In Progress" in line for line in lines), lines


def test_defect_a_card_already_filed_on_the_thread_is_held_on_not_filed_again(
        monkeypatch, capsys):
    """A receipt posted on an earlier pass whose hold did not post: the card
    is read off the receipt and the hold names it."""
    filed = tpd.comment(f"{proof_gap.RECEIPT_MARK} {CARD} — the defect in the build "
                        "its proof found is filed in Intake", 600)
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT, filed), text=PLAIN)
    assert h.filings == []
    assert h.holds == [(IDENT, *SHIPPED)]


def test_defect_with_that_card_shipped_the_next_pass_dispatches_one_re_armed_run(
        monkeypatch, capsys):
    thread = tpd._sent_back(*tpd.SPENT, tpd.hold(*SHIPPED, 600))
    h, tally, _ = _pass(monkeypatch, thread=thread, text=PLAIN,
                        fixes={CARD: tpd.fix_card(CARD)})
    reason = f"re-run after {CARD} shipped"
    assert h.fired == [(IDENT, tpd.REPO, reason, "proof-execute")]
    assert tally.dispatched == 1 and h.holds == [] and h.filings == []
    parsed = proof_run_state.receipt(h.posted[0][1])
    assert (parsed.reason, parsed.count) == (reason, "re-armed")
    assert [[m.card for m in merges] for _, merges in h.release_calls] == [[CARD]]


@pytest.mark.parametrize("case", ["not-done", "unmerged", "release-waiting"])
def test_defect_a_card_not_yet_shipped_re_arms_nothing(monkeypatch, capsys, case):
    fixes = {CARD: tpd.fix_card(CARD, state_name="In Review" if case == "not-done"
                                else "Done")}
    prs = ({CARD: {"number": 4001, "state": "OPEN", "headRefName": f"agent/{CARD}-x"}}
           if case == "unmerged" else None)
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT, tpd.hold(*SHIPPED, 600)),
                    text=PLAIN, fixes=fixes, prs=prs,
                    release="waiting" if case == "release-waiting" else "ready")
    assert h.fired == [] and h.holds == [] and h.filings == []


def test_defect_after_the_re_armed_run_is_spent_the_operator_is_alarmed(
        monkeypatch, capsys):
    """The fix shipped and the build still fails its rows: the card re-armed
    its one run, so the budget's hold for an operator's reading comes back."""
    thread = tpd._sent_back(*tpd.SPENT, tpd.hold(*SHIPPED, 600),
                            tpd.rearmed_receipt(200, f"re-run after {CARD} shipped"),
                            tpd.comment("⏳ 5/5 amended", 150))
    h, _, _ = _pass(monkeypatch, thread=thread, text=PLAIN,
                    fixes={CARD: tpd.fix_card(CARD)})
    assert h.fired == [] and h.filings == []
    [(_, observed, needs)] = h.holds
    assert observed == tpd.GATE_HELD[0] and needs.startswith(tpd.GATE_HELD[1])
    assert needs.startswith(proof_dispatch.OPERATOR_NEEDS)


def test_defect_a_filing_that_fails_is_a_failure_and_posts_no_hold(monkeypatch, capsys):
    h, tally, _ = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT), text=PLAIN,
                        filed=linear_ops.LinearError("Linear: 503"))
    lines = _lines(capsys)
    assert h.fired == [] and h.holds == []
    assert len(tally.failures) == 1 and "503" in tally.failures[0]
    assert any("503" in line for line in lines), lines


def test_defect_the_dry_run_files_nothing_and_writes_nothing(monkeypatch, capsys):
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT), text=PLAIN, live=False)
    lines = _lines(capsys)
    assert h.filings == [] and h.holds == [] and h.posted == []
    assert any(line.startswith(f"would: file a fix card for {IDENT}") for line in lines), lines


@pytest.mark.parametrize("beside", [tpd.FIX_ROW, tpd.CAUSE_ROW, tpd.HISTORY_ROW],
                         ids=["names-a-fix", "cause", "history"])
def test_defect_a_row_that_is_not_plain_files_nothing(monkeypatch, capsys, beside):
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(*tpd.SPENT),
                    text=tpd.record_md(tpd.MET_ROW, tpd.NOT_MET_ROW, beside),
                    fixes={tpd.FIX: tpd.fix_card(state_name="In Progress")})
    assert h.filings == []
    [(_, observed, needs)] = h.holds
    assert observed == tpd.GATE_HELD[0] and needs.startswith(tpd.GATE_HELD[1])


def test_defect_the_critics_spent_trigger_is_unchanged_and_alarms(monkeypatch, capsys):
    critic = tpd._declined(approve=tpd.verdict("REQUEST_CHANGES", 100))
    spent = (tpd.rerun_receipt(1500, 1), tpd.comment("⏳ 5/5 amended", 1400),
             tpd.rerun_receipt(800, 2), tpd.comment("⏳ 5/5 amended", 700))
    h, _, _ = _pass(monkeypatch, thread=tpd._sent_back(*spent), text=PLAIN, pr=critic)
    assert h.filings == [] and h.fired == []
    [(_, observed, needs)] = h.holds
    assert (observed, needs.split(" — ")[0]) == tpd.RERUN_HELD
    assert needs.startswith(proof_dispatch.OPERATOR_NEEDS)


def test_defect_the_shipped_wording_is_an_event_wording():
    assert proof_dispatch.FIX_SHIPPED_NEEDS in proof_dispatch.EVENT_NEEDS
    assert proof_dispatch.FIX_SHIPPED_NEEDS not in proof_dispatch.OPERATOR_NEEDS
    assert proof_dispatch.FIX_SHIPPED_NEEDS.format(card=CARD) == SHIPPED[1]
    assert proof_record.time_only(linear_ops.proof_waiting_line(*SHIPPED)) is None


# --------------------------------------------------------------------------- #
# 4. a fix already filed against the proof is held on, never filed again      #
#    (DRE-8251) — agent-bureau's DRE-8162, record #4242, replayed              #
# --------------------------------------------------------------------------- #

D8162 = "DRE-8162"
PR_8162 = 4242
HEAD_8162 = "4e5d6c7" + "0" * 33
FIX_8215 = "DRE-8215"
#: The pass that filed DRE-8230 instead: 10-27 7:07 PM PT.
NOW_8162 = rearm._ts("2026-10-28T02:07:00Z")
#: The first proof run, which opened the record, and the two re-runs that
#: spent the budget — the newest at 01:35:31Z, after DRE-8215 was related.
THREAD_8162 = [
    rearm.run_receipt("2026-10-27T23:22:58Z", "2026-10-27 16:22 PT", "first proof run",
                      "dispatch 1 of 2"),
    rearm.at("⏳ 5/5 PR opened", "2026-10-27T23:58:00Z"),
    rearm.run_receipt("2026-10-28T00:46:24Z", "2026-10-27 17:46 PT",
                      "re-run after the gate's decline at 1a2b3c4", "re-run 1 of 2"),
    rearm.at("⏳ 5/5 amended", "2026-10-28T01:12:00Z"),
    rearm.run_receipt("2026-10-28T01:35:31Z", "2026-10-27 18:35 PT",
                      "re-run after the gate's decline at 5d6e7f8", "re-run 2 of 2"),
    rearm.at("⏳ 5/5 amended", "2026-10-28T01:54:00Z"),
]
#: The one row the record fails, a plain `Not met.` naming no fix.
SPEED_ROW = ("The Desk's first paint lands under two seconds", "Not met. 3.4 s at 18:20 PT")
TEXT_8162 = rearm.record_text(D8162, tpd.MET_ROW, SPEED_ROW)


def blocker(ident: str, stamp: str, state_name: str, description: str) -> dict:
    """A `blocks` relation on the proof, with its time and the blocking card's
    state, as `LinearReads.blocking` reads it."""
    return {"type": "blocks", "createdAt": stamp,
            "issue": {"identifier": ident, "state": {"name": state_name},
                      "description": description}}


#: The epic's build cards, related at 11:44 AM PT, Done, naming no record.
BUILD_8162 = [blocker(f"DRE-{n}", "2026-10-27T18:44:00Z", "Done",
                      f"agent-bureau: build part {n - 8158} of the epic DRE-8158.")
              for n in (8159, 8160, 8161)]
#: The fix filed for the failing speed row, related before the newest receipt.
FIXED_8215 = blocker(FIX_8215, "2026-10-28T01:03:44Z", "Planning",
                     f"The proof {D8162} fails its speed row in agent-bureau #{PR_8162}.")


class _Filer(rearm.Fleet):
    """One agent-bureau pass whose fix-card filer is recorded (DRE-7953)."""

    def __init__(self, monkeypatch, board, **kw):
        super().__init__(monkeypatch, board, **kw)
        monkeypatch.setattr(proof_gap, "file_fix_card", self.file_fix)


def _pass_8162(monkeypatch, *, relations=None, thread=None, cards=None, now=NOW_8162,
               live=True):
    thread = THREAD_8162 if thread is None else thread
    relations = BUILD_8162 + [FIXED_8215] if relations is None else relations
    board = rearm.Relations(review=[rearm._review(D8162, thread)], threads={D8162: thread},
                            fixes=cards or {}, relations={D8162: relations})
    pr = rearm.record_pr(D8162, PR_8162, HEAD_8162, opened="2026-10-27T23:58:00Z",
                         approved="2026-10-28T01:58:00Z", declined="2026-10-28T02:00:00Z")
    h = _Filer(monkeypatch, board, records={D8162: pr}, texts={PR_8162: TEXT_8162},
               prs={(rearm.AB_REPO, FIX_8215): rearm.merged(
                   4248, "7e95e69b0" + "0" * 31, FIX_8215, "2026-10-28T15:02:00Z")},
               states={D8162: tpd.state("finished", dispatches=1,
                                        record={"number": PR_8162, "state": "open"})})
    tally = h.sweep(now=now, live=live)
    return h, tally


def test_dre_8162_replayed_the_fix_already_filed_is_held_on_and_nothing_is_filed(
        monkeypatch, capsys):
    """On `main` at 098f1a5e this pass filed a second card (DRE-8230) and held
    on it. DRE-8215 was related after the first proof run, so it is a fix for
    this record: the hold names it, and nothing is filed or receipted."""
    h, tally = _pass_8162(monkeypatch)
    lines = tpd._about(tpd._lines(capsys), D8162)
    assert h.filings == [], lines
    assert h.holds == [(D8162, rearm.GATE_HELD[0], f"{FIX_8215} shipped")]
    assert linear_ops.proof_waiting_line(*h.holds[0][1:]).endswith(
        f"— needs {FIX_8215} shipped")
    assert h.posted == [] and h.fired == []
    assert not any(proof_gap.RECEIPT_MARK in body for _, body in h.posted)
    assert tally.held == 1 and tally.failures == []
    assert any(FIX_8215 in line and "filed nothing" in line for line in lines), lines


def test_dre_8162_the_build_cards_are_never_the_fix(monkeypatch, capsys):
    """The epic's build cards were related before the first proof run: never
    held on, beside DRE-8215 or without it — without it the pass files, as
    before."""
    h, _ = _pass_8162(monkeypatch)
    [(_, _, needs)] = h.holds
    assert not any(f"DRE-{n}" in needs for n in (8159, 8160, 8161))
    capsys.readouterr()

    h, tally = _pass_8162(monkeypatch, relations=BUILD_8162)
    assert [f[0] for f in h.filings] == [D8162]
    assert h.holds == [(D8162, rearm.GATE_HELD[0], f"{tpd.DEFECT_CARD} shipped")]
    assert tally.failures == []


@pytest.mark.parametrize("gone", ["Canceled", "Duplicate"])
def test_dre_8162_a_blocker_canceled_or_duplicate_is_never_the_fix(
        monkeypatch, capsys, gone):
    h, _ = _pass_8162(monkeypatch,
                      relations=BUILD_8162 + [{**FIXED_8215, "issue": {
                          **FIXED_8215["issue"], "state": {"name": gone}}}])
    assert [f[0] for f in h.filings] == [D8162]
    assert h.holds == [(D8162, rearm.GATE_HELD[0], f"{tpd.DEFECT_CARD} shipped")]


def test_dre_8162_two_fixes_filed_hold_on_the_newest_and_the_line_names_both(
        monkeypatch, capsys):
    later = blocker("DRE-8217", "2026-10-28T01:20:00Z", "Intake",
                    f"A second look at {D8162}'s speed row.")
    h, _ = _pass_8162(monkeypatch, relations=BUILD_8162 + [later, FIXED_8215])
    lines = tpd._about(tpd._lines(capsys), D8162)
    assert h.filings == []
    assert h.holds == [(D8162, rearm.GATE_HELD[0], "DRE-8217 shipped")]
    assert any(FIX_8215 in line and "DRE-8217" in line and "filed nothing" in line
               for line in lines), lines


def test_dre_8162_relations_not_read_file_nothing_and_hold_nothing(monkeypatch, capsys):
    h, tally = _pass_8162(monkeypatch, relations=RuntimeError("Linear: 503"))
    lines = tpd._about(tpd._lines(capsys), D8162)
    assert h.filings == [] and h.holds == [] and h.posted == [] and h.fired == []
    assert len(tally.failures) == 1 and "503" in tally.failures[0]
    assert any("relations could not be read" in line and "503" in line
               and "nothing filed" in line for line in lines), lines


def test_dre_8162_the_dry_run_would_hold_on_the_fix_and_writes_nothing(monkeypatch, capsys):
    h, _ = _pass_8162(monkeypatch, live=False)
    lines = tpd._lines(capsys)
    assert h.filings == [] and h.holds == [] and h.posted == []
    assert not any(line.startswith("would: file a fix card") for line in lines), lines
    assert any(FIX_8215 in line and "filed nothing" in line for line in lines), lines


def test_dre_8162_with_dre_8215_shipped_the_next_pass_dispatches_one_re_armed_run(
        monkeypatch, capsys):
    """The hold the replay posts, then 10-28 after v1.6.641: DRE-8215 Done,
    #4248 merged at 7e95e69b0 and its release ready — one re-armed run."""
    h, _ = _pass_8162(monkeypatch)
    [(_, observed, needs)] = h.holds
    held = rearm.at(linear_ops.proof_waiting_line(observed, needs), "2026-10-28T02:07:30Z")
    capsys.readouterr()

    h, tally = _pass_8162(monkeypatch, thread=THREAD_8162 + [held],
                          cards={FIX_8215: rearm.fix(FIX_8215, rearm.AB)},
                          now=rearm._ts("2026-10-28T16:10:00Z"))
    reason = f"re-run after {FIX_8215} shipped"
    assert h.fired == [(D8162, rearm.AB_REPO, reason, "proof-execute")]
    assert tally.dispatched == 1 and h.holds == [] and h.filings == []
    assert (rearm.AB_REPO, FIX_8215) in h.pr_reads
    assert h.release_calls == [(rearm.AB_REPO, [FIX_8215])]


def test_the_proofs_relations_are_read_with_each_blockers_state(monkeypatch):
    """A Canceled or Duplicate blocker is never the fix, so the relations read
    carries each blocking card's state (DRE-8251)."""
    queries: list = []

    def gql(query, variables=None):
        queries.append(query)
        return {"issue": {"inverseRelations": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": BUILD_8162 + [FIXED_8215]}}}

    monkeypatch.setattr(linear_ops, "gql", gql)
    assert proof_dispatch.LinearReads().blocking(D8162) == BUILD_8162 + [FIXED_8215]
    [query] = queries
    assert "issue { identifier state { name } }" in " ".join(query.split())
