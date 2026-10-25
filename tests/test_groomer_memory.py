"""RED-first: the groomer reads its memory before the batch is cut (DRE-7824).

`_build` read ONE thread, the current standing card's, and nothing else. On
2026-09-26 that cost four cards: DRE-4054, DRE-4267, DRE-4271 and DRE-4272
were marked `groom-excluded` on DRE-4488 on 2026-09-22, and every DRE-4541
proposal from that morning offered them again, because nothing read DRE-4488.

Now, once per run and before the census, the groomer reads the console's
record of every decision (`bureau_read.groom_removals`, DRE-7821) and the
whole thread of every standing card (`groom_memory.standing_cards`,
DRE-7822), and takes the newest decision per card (`groom_memory.remembered`):

  * a `not-doing` or `cancel` keeps the card off the offer until a person
    brings it back, and the page names the reason recorded and its date;
  * a `put-back` — a door row or a `groom-added` marker — brings it back, and
    the page says so in the `Put back on offer by you:` line; an accepted
    rewrite brings it back too, and is said as one in its own line. Neither
    line names a card that was never removed;
  * a morning the door cannot be read says so on the page, with the door's
    reason, and honors the decisions written on the threads instead;
  * every `propose` run prints one `groom-memory:` line in the Groom step's
    log, which the proof run may read (`config/proof-reach.json`).

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_memory.py -q
"""
from __future__ import annotations

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

import bureau_read  # noqa: E402
import groom_memory  # noqa: E402
import groomer  # noqa: E402

from test_groomer import CYCLES, NOW, card  # noqa: E402
from test_groom_verify import LANE, _args, cancel, planning  # noqa: E402

THREADS = json.loads(
    (ROOT / "tests" / "fixtures" / "groom-memory-threads.json").read_text())

CURRENT = "DRE-4541"
EARLIER = "DRE-4488"
PID = "a4f198411b02"
REASONS = [{"key": "already-done", "label": "Already done"},
           {"key": "waits-on-other-work",
            "label": "Not ready: waits on other work"}]
EARLIER_ANSWERS = "## Your earlier answers"
PUT_BACK = "Put back on offer by you:"
REWRITTEN_BACK = "Back on offer because you accepted its rewrite:"
DOOR_DOWN = "The console's record of your decisions could not be read this morning"
UNREADABLE = "## Decisions I could not read"

TAGS = {"excluded": groomer.EXCLUDE_TAG, "held": groomer.HOLD_TAG,
        "added": groomer.ADD_TAG,
        "rewrite-accepted": groomer.REWRITE_ACCEPT_TAG}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def said(tag, card_id, tail=None, *, at="2026-10-20T16:00:00Z", pid=PID,
         by_pipeline=False):
    """A per-card marker typed on a thread, as `comment_records` returns it."""
    rest = f" — {tail}" if tail else ""
    return {"body": f"{groomer.MARK} {tag}: {pid} {card_id}{rest}",
            "authored_by_pipeline": by_pipeline, "created_at": at}


def records_from(markers):
    """A thread rebuilt from the fixture's markers — each one's first line as
    the console writes it, in the order the thread carries them."""
    return [said(TAGS[m["kind"]], m["card"], m["tail"], at=m["created_at"],
                 pid=m["proposal_id"]) for m in markers]


def row(card_id, decision="not-doing", *, at="2026-09-22T02:49:23Z",
        standing=EARLIER, reason=None, comment=None, ident=1, until_card=None,
        until_date=None):
    return {"id": ident, "recorded_at": at, "standing_card": standing,
            "proposal_id": "50e396c78523", "card": card_id,
            "decision": decision, "reason": reason,
            "hold_until_card": until_card, "hold_until_date": until_date,
            "actor_name": "Sid", "comment": comment}


class Threads:
    """`linear_ops.comment_records`, one thread per card, every read kept."""

    def __init__(self, threads=None):
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.reads: list[tuple[str, bool]] = []

    def comment_records(self, identifier, *, whole_thread=False):
        self.reads.append((identifier, whole_thread))
        return [dict(r) for r in self.threads.get(identifier, [])]


def door(rows=(), reasons=REASONS):
    def groom_removals(since=None):
        return {"schema": bureau_read.SCHEMA, "reasons": list(reasons),
                "decisions": [dict(r) for r in rows]}
    return groom_removals


def door_down(reason="unavailable", detail="BUREAU_READ_URL is not set"):
    def groom_removals(since=None):
        raise bureau_read.ReadUnknown(reason, detail, unavailable=True)
    return groom_removals


def patch(monkeypatch, cards, *, threads=None, removals=None):
    ops = Threads(threads)
    monkeypatch.setattr(groomer, "_now", lambda: NOW)
    monkeypatch.setattr(groomer, "read_population", lambda lops, l: list(cards))
    monkeypatch.setattr(groomer, "read_cycles", lambda lops: CYCLES)
    monkeypatch.setattr(groomer.linear_ops, "comment_records",
                        ops.comment_records)
    monkeypatch.setattr(bureau_read, "groom_removals", removals or door())
    return ops


def build(monkeypatch, cards=LANE, *, current=CURRENT, **kw):
    capacity = kw.pop("capacity", 3)
    patch(monkeypatch, cards, **kw)
    return groomer._build(_args(card=current, capacity=capacity))


def offered_anywhere(proposal):
    return set(planning(proposal)) | {r["identifier"] for r in cancel(proposal)}


def section(page, heading=EARLIER_ANSWERS):
    lines = page.splitlines()
    assert heading in lines, f"no `{heading}` section:\n{page}"
    out = []
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def memory_lines(err):
    return [ln for ln in err.splitlines() if ln.startswith(groomer.MEMORY_TAG)]


# --------------------------------------------------------------------------
# per_card_markers — a vouched thread as groom_memory's marker dicts
# --------------------------------------------------------------------------
def test_the_constant_is_the_tag():
    assert groomer.MEMORY_TAG == "groom-memory:"


def test_a_thread_reads_back_into_exactly_the_markers_it_was_written_from():
    """The fixture is the DRE-4488 and DRE-4541 threads' markers, copied; the
    thread rebuilt from them reads back to the same dicts, field for field."""
    for standing in (EARLIER, CURRENT):
        markers = THREADS["markers"][standing]
        records = records_from(markers)
        records.insert(1, {"body": f"{groomer.MARK} {groomer.PROPOSAL_TAG}: "
                                   f"{PID}\n\nthe page", "created_at":
                           markers[0]["created_at"],
                           "authored_by_pipeline": True})
        assert groomer.per_card_markers(records, standing) == markers


def test_only_a_decider_marker_is_a_marker():
    records = [said(groomer.EXCLUDE_TAG, "DRE-7", "mine", by_pipeline=True),
               {**said(groomer.EXCLUDE_TAG, "DRE-8"), "authored_by_pipeline": True,
                "vouched_by": groomer.VOUCHED_BY_CONSOLE},
               said(groomer.HOLD_TAG, "DRE-9", "until DRE-1")]
    got = groomer.per_card_markers(records, CURRENT)
    assert [(m["card"], m["kind"], m["tail"]) for m in got] == [
        ("DRE-8", "excluded", None), ("DRE-9", "held", "until DRE-1")]
    assert {m["standing_card"] for m in got} == {CURRENT}


def test_a_marker_naming_no_card_is_no_marker():
    records = [{"body": f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} done",
                "authored_by_pipeline": False, "created_at": NOW}]
    assert groomer.per_card_markers(records, CURRENT) == []


def test_a_marker_with_no_stamp_keeps_its_place_in_the_thread():
    """Linear names a comment's time; a fixture may not. A marker with none is
    never dropped — it takes the stamp before it, so the newer word still
    wins."""
    records = [{"body": f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} DRE-7",
                "authored_by_pipeline": False},
               {"body": f"{groomer.MARK} {groomer.ADD_TAG}: {PID} DRE-7",
                "authored_by_pipeline": False}]
    markers = groomer.per_card_markers(records, CURRENT)
    assert [m["kind"] for m in markers] == ["excluded", "added"]
    got = groom_memory.remembered([], markers, reasons=None,
                                  door_problem="unavailable")
    assert got["DRE-7"]["decision"] == "put-back"


# --------------------------------------------------------------------------
# the door's row, honored on the next proposal on another card
# --------------------------------------------------------------------------
def test_a_not_doing_row_from_an_earlier_card_keeps_the_card_off_both_lists(
        monkeypatch, capsys):
    before = build(monkeypatch)
    assert "DRE-102" in planning(before), "the fixture never offered DRE-102"
    after = build(monkeypatch, removals=door([row("DRE-102",
                                                  reason="already-done")]))
    assert "DRE-102" not in offered_anywhere(after)
    assert "DRE-102" not in {r["identifier"] for r in after["sequence"]}
    assert after["excluded_before"] == ["DRE-102"]
    assert after["excluded_reasons"] == {"DRE-102": {
        "reason_label": "Already done", "recorded_at": "2026-09-22T02:49:23Z",
        "source": "record"}}
    earlier = section(groomer.render_proposal(after))
    line = [ln for ln in earlier.splitlines() if "DRE-102" in ln
            and "groom-added" not in ln]
    assert line, earlier
    # 02:49 UTC on 2026-09-22 is the evening of 2026-09-21 in Pacific time.
    assert "Already done" in line[0] and "2026-09-21" in line[0], line[0]
    assert f"{groomer.ADD_TAG}: {after['id']} DRE-N" in earlier


def test_a_cancel_is_on_the_cancel_list_not_off_the_offer(monkeypatch):
    """Since DRE-7826 a Move to Cancel is carried as the decision it is: on
    the next Cancel list, never a Don't do that keeps the card in Intake."""
    after = build(monkeypatch, removals=door([row("DRE-102", "cancel")]))
    assert [r["identifier"] for r in cancel(after)] == ["DRE-102"]
    assert "DRE-102" not in planning(after)
    assert "excluded_before" not in after
    assert after["excluded_reasons"] == {}


def test_only_a_hold_with_no_condition_is_ranked_last(monkeypatch):
    """A hold on a card or a date waits for it (DRE-7826): off the offer
    while the condition is unmet — the card's state unread here."""
    def unread(*a, **k):
        raise RuntimeError("linear answered 503")

    monkeypatch.setattr(groomer.linear_ops, "gql", unread)
    rows = [row("DRE-101", "hold", until_card="DRE-9"),
            row("DRE-102", "hold", until_date="2027-01-01", ident=2),
            row("DRE-103", "hold", ident=3)]
    after = build(monkeypatch, removals=door(rows))
    assert after["held_before"] == ["DRE-103"]
    assert [h["identifier"] for h in after["held_until"]] == ["DRE-101",
                                                              "DRE-102"]
    assert planning(after) == ["DRE-104", "DRE-105", "DRE-106"]


def test_a_later_put_back_row_brings_it_back_and_says_why(monkeypatch):
    rows = [row("DRE-102", reason="already-done"),
            row("DRE-102", "put-back", at="2026-10-20T03:00:00Z",
                comment="Wanted after all", ident=2)]
    after = build(monkeypatch, removals=door(rows))
    assert "DRE-102" in planning(after)
    assert "excluded_before" not in after
    assert after["put_back"] == {"DRE-102": {
        "reason_label": "Wanted after all",
        "recorded_at": "2026-10-20T03:00:00Z", "source": "record"}}
    page = groomer.render_proposal(after)
    line = [ln for ln in page.splitlines() if ln.startswith(PUT_BACK)]
    assert len(line) == 1, page
    assert "DRE-102" in line[0] and "Wanted after all" in line[0]
    assert "2026-10-19" in line[0], line[0]


def test_a_groom_added_marker_newer_than_the_row_brings_it_back(monkeypatch):
    added = said(groomer.ADD_TAG, "DRE-102", at="2026-10-21T18:00:00Z")
    after = build(monkeypatch, removals=door([row("DRE-102")]),
                  threads={CURRENT: [added]})
    assert "DRE-102" in planning(after)
    assert after["put_back"]["DRE-102"] == {
        "reason_label": None, "recorded_at": "2026-10-21T18:00:00Z",
        "source": "thread"}
    line = [ln for ln in groomer.render_proposal(after).splitlines()
            if ln.startswith(PUT_BACK)]
    assert line and "DRE-102" in line[0]
    assert "no reason recorded" in line[0] and "2026-10-21" in line[0]


def test_a_rewrite_accepted_on_a_card_never_removed_is_not_put_back(monkeypatch):
    accepted = said(groomer.REWRITE_ACCEPT_TAG, "DRE-102",
                    at="2026-10-21T18:00:00Z")
    after = build(monkeypatch, threads={CURRENT: [accepted]})
    assert "DRE-102" in planning(after)
    assert after["put_back"] == {}
    assert after["rewritten_back"] == {}
    page = groomer.render_proposal(after)
    assert not [ln for ln in page.splitlines()
                if ln.startswith((PUT_BACK, REWRITTEN_BACK))], page


def test_a_rewrite_accepted_newer_than_a_removal_brings_it_back_and_says_so(
        monkeypatch):
    accepted = said(groomer.REWRITE_ACCEPT_TAG, "DRE-102",
                    at="2026-10-21T18:00:00Z")
    after = build(monkeypatch, removals=door([row("DRE-102")]),
                  threads={CURRENT: [accepted]})
    assert "DRE-102" in planning(after)
    assert "excluded_before" not in after
    assert after["put_back"] == {}
    assert after["rewritten_back"] == {"DRE-102": {
        "reason_label": None, "recorded_at": "2026-10-21T18:00:00Z",
        "source": "thread"}}
    page = groomer.render_proposal(after)
    assert not [ln for ln in page.splitlines() if ln.startswith(PUT_BACK)], page
    line = [ln for ln in page.splitlines() if ln.startswith(REWRITTEN_BACK)]
    assert len(line) == 1, page
    assert "DRE-102" in line[0] and "2026-10-21" in line[0], line[0]


def test_a_groom_added_on_a_card_never_removed_is_not_put_back(monkeypatch):
    added = said(groomer.ADD_TAG, "DRE-102", at="2026-10-21T18:00:00Z")
    after = build(monkeypatch, threads={CURRENT: [added]})
    assert "DRE-102" in planning(after)
    assert after["put_back"] == {}
    assert not [ln for ln in groomer.render_proposal(after).splitlines()
                if ln.startswith(PUT_BACK)]


def test_a_groom_added_after_an_excluded_marker_is_put_back(monkeypatch):
    thread = [said(groomer.EXCLUDE_TAG, "DRE-102", at="2026-10-20T16:00:00Z"),
              said(groomer.ADD_TAG, "DRE-102", at="2026-10-21T18:00:00Z")]
    after = build(monkeypatch, threads={EARLIER: thread})
    assert "DRE-102" in planning(after)
    assert list(after["put_back"]) == ["DRE-102"]


def test_no_put_back_line_when_no_put_back_card_is_in_the_lane(monkeypatch):
    rows = [row("DRE-999", "put-back", comment="elsewhere")]
    after = build(monkeypatch, removals=door(rows))
    assert after["put_back"] == {}
    assert not [ln for ln in groomer.render_proposal(after).splitlines()
                if ln.startswith(PUT_BACK)]


# --------------------------------------------------------------------------
# the 2026-09-22 cards, off the real threads
# --------------------------------------------------------------------------
def test_the_cards_excluded_on_dre_4488_are_not_offered_on_dre_4541(monkeypatch):
    four = ["DRE-4267", "DRE-4271", "DRE-4272"]
    lane = [card(c, days=n + 1) for n, c in enumerate(four)] + LANE[:5]
    threads = {EARLIER: records_from(THREADS["markers"][EARLIER]),
               CURRENT: records_from(THREADS["markers"][CURRENT])}
    before = build(monkeypatch, lane, capacity=20)
    assert set(four) <= set(planning(before)), "the lane never offered them"
    after = build(monkeypatch, lane, capacity=20, threads=threads,
                  removals=door([], reasons=[]))
    assert after["memory"]["source"] == "record"
    assert not set(four) & offered_anywhere(after)
    assert set(four) <= set(after["excluded_before"])
    earlier = section(groomer.render_proposal(after))
    for c in four:
        named = [ln for ln in earlier.splitlines() if c in ln]
        assert named, f"{c} is not named under {EARLIER_ANSWERS}"
        assert "no reason recorded" in named[0] and "2026-09-21" in named[0]
    for c in four:
        assert after["excluded_reasons"][c]["source"] == "history"


# --------------------------------------------------------------------------
# a morning the door cannot be read
# --------------------------------------------------------------------------
def test_a_door_that_cannot_be_read_is_said_and_the_threads_are_honored(
        monkeypatch, capsys):
    excluded = said(groomer.EXCLUDE_TAG, "DRE-102", "Already done")
    after = build(monkeypatch, removals=door_down(),
                  threads={EARLIER: [excluded]})
    err = capsys.readouterr().err
    assert "DRE-102" not in offered_anywhere(after)
    assert after["excluded_reasons"]["DRE-102"]["source"] == "fallback"
    assert after["memory"]["source"] == "fallback"
    assert after["memory"]["door_problem"] == (
        "unavailable: BUREAU_READ_URL is not set")
    page = groomer.render_proposal(after)
    line = [ln for ln in page.splitlines() if ln.startswith(DOOR_DOWN)]
    assert len(line) == 1, page
    assert "unavailable" in line[0] and "BUREAU_READ_URL is not set" in line[0]
    assert "standing cards" in line[0]
    tagged = memory_lines(err)
    assert len(tagged) == 1, err
    assert tagged[0].endswith(" door=unavailable detail=BUREAU_READ_URL is not set")
    assert " source=fallback " in tagged[0]


def test_the_door_line_is_written_when_nothing_was_excluded(monkeypatch):
    after = build(monkeypatch, removals=door_down("no-token",
                                                  "id-token: write is not granted"))
    assert "excluded_before" not in after
    line = [ln for ln in groomer.render_proposal(after).splitlines()
            if ln.startswith(DOOR_DOWN)]
    assert line and "no-token" in line[0]
    assert "id-token: write is not granted" in line[0]


def test_no_door_line_when_the_door_answered(monkeypatch):
    after = build(monkeypatch)
    assert after["memory"]["door_problem"] is None
    assert DOOR_DOWN not in groomer.render_proposal(after)


def test_the_detail_on_the_tagged_line_is_one_line(monkeypatch, capsys):
    build(monkeypatch, removals=door_down("malformed", "a  b\n\tc"))
    tagged = memory_lines(capsys.readouterr().err)
    assert tagged[0].endswith(" door=malformed detail=a b c")


# --------------------------------------------------------------------------
# what is read, and what is printed
# --------------------------------------------------------------------------
def test_every_standing_thread_is_read_whole_once_and_vouched_as_itself(
        monkeypatch):
    vouched = []
    real = groomer.vouch

    def spy(records, *, card, verifier=None):
        vouched.append(card)
        return real(records, card=card, verifier=verifier)

    monkeypatch.setattr(groomer, "vouch", spy)
    ops = patch(monkeypatch, LANE)
    groomer._build(_args(card="DRE-9999"))
    want = list(groom_memory.standing_cards("DRE-9999"))
    whole = [c for c, w in ops.reads if w]
    assert sorted(whole) == sorted(want)
    assert len(whole) == len(set(whole)), f"a thread was read twice: {whole}"
    assert sorted(vouched) == sorted(want)


def test_the_proposal_carries_the_memory_block(monkeypatch):
    rows = [row("DRE-102", reason="already-done")]
    thread = [said(groomer.EXCLUDE_TAG, "DRE-103", at="2026-10-21T00:00:00Z"),
              said(groomer.EXCLUDE_TAG, "DRE-104", at="2026-09-01T00:00:00Z")]
    after = build(monkeypatch, removals=door(rows), threads={CURRENT: thread})
    assert after["memory"] == {
        "source": "record", "door_problem": None,
        "standing_cards": list(groom_memory.standing_cards(CURRENT)),
        "counts": {"rows": 1, "history": 1, "thread": 1, "fallback": 0}}


@pytest.mark.parametrize("current", [CURRENT, "DRE-9999"])
def test_every_propose_run_prints_one_tagged_line(monkeypatch, capsys, current):
    patch(monkeypatch, LANE, removals=door([row("DRE-102")]),
          threads={current: [said(groomer.HOLD_TAG, "DRE-103")]})
    assert groomer.main(["propose", "--card", current, "--no-judgement",
                         "--no-verify", "--capacity", "3"]) == 0
    tagged = memory_lines(capsys.readouterr().err)
    assert len(tagged) == 1, tagged
    want = ",".join(groom_memory.standing_cards(current))
    assert tagged[0] == (f"groom-memory: source=record rows=1 history=0 "
                         f"thread=1 fallback=0 excluded=1 held=1 "
                         f"standing={want}")


def test_a_verified_proposal_still_prints_one_line_and_keeps_its_memory(
        monkeypatch, capsys):
    def broken(*a, **k):
        raise RuntimeError("no check today")

    monkeypatch.setattr(groomer.groom_verify, "check", broken)
    patch(monkeypatch, LANE, removals=door([row("DRE-102")]))
    built = groomer._build(_args(card=CURRENT, verify=True))
    assert len(memory_lines(capsys.readouterr().err)) == 1
    assert built["memory"]["source"] == "record"
    assert "DRE-102" not in offered_anywhere(built)


def test_the_new_lines_leave_the_id_alone():
    memory = {"source": "fallback", "door_problem": "unavailable: x",
              "standing_cards": list(groom_memory.STANDING_CARDS),
              "counts": {"rows": 0, "history": 0, "thread": 0, "fallback": 1}}
    plain = groomer.propose(LANE, cycles=CYCLES, capacity=3, now=NOW,
                            excluded=["DRE-102"])
    said_more = groomer.propose(
        LANE, cycles=CYCLES, capacity=3, now=NOW, excluded=["DRE-102"],
        memory=memory,
        excluded_reasons={"DRE-102": {"reason_label": "Already done",
                                      "recorded_at": NOW, "source": "fallback"}},
        put_back={"DRE-103": {"reason_label": None, "recorded_at": NOW,
                              "source": "fallback"}},
        rewritten_back={"DRE-104": {"reason_label": None, "recorded_at": NOW,
                                    "source": "fallback"}})
    assert said_more["id"] == plain["id"]
    assert groomer.render_proposal(said_more) != groomer.render_proposal(plain)


# --------------------------------------------------------------------------
# a marker naming no card is still listed
# --------------------------------------------------------------------------
def test_a_marker_naming_no_card_is_still_listed(monkeypatch):
    line = f"{groomer.MARK} {groomer.EXCLUDE_TAG}: {PID} the second one is done"
    thread = [{"body": f"{groomer.MARK} {groomer.PROPOSAL_TAG}: {PID}\n\npage",
               "authored_by_pipeline": True, "created_at": NOW},
              {"body": line, "authored_by_pipeline": False, "created_at": NOW}]
    after = build(monkeypatch, threads={CURRENT: thread, EARLIER: thread})
    assert after["unreadable_decisions"] == [line]
    assert line in section(groomer.render_proposal(after), UNREADABLE)


def test_the_proof_run_may_read_the_tagged_line():
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    entries = [e for e in doc["log_lines"] if e["tag"] == groomer.MEMORY_TAG]
    assert entries == [{
        "tag": "groom-memory:", "printed_by": "scripts/groomer.py",
        "constant": "MEMORY_TAG",
        "when": "every groomer propose run since DRE-7824, one line in the "
                "Groom step's log"}]
