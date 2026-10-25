"""RED-first: the groomer's week, counted and posted (DRE-7970).

The only scoring instrument was `groomer_score.py`, which grades the ranked
read against one hand audit and ran once, by hand. Nothing counted what a
person took off a proposal, or why: the parent epic's baseline (28 of 191
cards removed over the 13 proposals of 2026-09-26 to 2026-10-10, 7 of 10
Cancel rows agreed) was counted by hand from the console's database.

`scripts/groom_scorecard.py score` reads the standing groom card's whole
thread and the console's record of decisions, and counts one Pacific week,
Sunday to Saturday:

  * `proposed` — the Planning and Cancel rows of the week's answered
    proposals (a drain, a decline or any per-card decision names it);
  * `removed` — those rows a person took off, once per proposal and card,
    with the reason each was taken off for;
  * `cancel` — what became of every Cancel row: agreed, kept or unanswered.

`--post` writes it once per week on the standing card, as one comment that
opens `📊 groom-scorecard: <start>..<end>` and carries a fenced JSON block.
Every run prints one `groom-scorecard:` line on stderr.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groom_scorecard.py -q
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import bureau_read  # noqa: E402
import groomer  # noqa: E402
import groom_scorecard  # noqa: E402

WEEK = json.loads(
    (ROOT / "tests" / "fixtures" / "groom-scorecard-week.json").read_text())
CARD = WEEK["standing_card"]                     # DRE-4541
ENDING = WEEK["week_ending"]                     # 2026-10-17, a Saturday

TAGS = {"excluded": groomer.EXCLUDE_TAG, "held": groomer.HOLD_TAG,
        "added": groomer.ADD_TAG,
        "rewrite-accepted": groomer.REWRITE_ACCEPT_TAG}

LINE = re.compile(
    r"^groom-scorecard: week=\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2} "
    r"proposals=\d+ answered=\d+ proposed=\d+ removed=\d+ "
    r"share=(?:\d\.\d{4}|none) cancel_rows=\d+ agreed=\d+ kept=\d+ "
    r"unanswered=\d+ source=(?:record|fallback)$")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def said(tag, pid, card_id=None, tail=None, *, at="2026-10-13T16:00:00Z",
         by_pipeline=False):
    """A marker typed on the thread, as `comment_records` returns it."""
    named = f" {card_id}" if card_id else ""
    rest = f" — {tail}" if tail else ""
    return {"body": f"{groomer.MARK} {tag}: {pid}{named}{rest}",
            "authored_by_pipeline": by_pipeline, "created_at": at}


def week_thread():
    """The fixture's week as the thread carries it: its proposals and drained
    records copied whole, and each marker's first line as a person's comment
    (the shape `tests/test_groomer_memory.py` rebuilds them in)."""
    markers = [said(TAGS[m["kind"]], m["proposal_id"], m["card"], m["tail"],
                    at=m["created_at"]) for m in WEEK["markers"][CARD]]
    return sorted([dict(r) for r in WEEK["thread"]] + markers,
                  key=lambda r: r["created_at"])


class Ops:
    """`linear_ops`: one thread per card, and every comment it is asked to
    write is kept and lands on the thread."""

    def __init__(self, threads):
        self.threads = {k: [dict(r) for r in v] for k, v in threads.items()}
        self.posted: list[tuple[str, str]] = []

    def comment_records(self, identifier, *, whole_thread=False):
        assert whole_thread, "the scorecard reads the WHOLE thread"
        return [dict(r) for r in self.threads.get(identifier, [])]

    def cmd_comment(self, identifier, body, *flags):
        self.posted.append((identifier, body))
        self.threads.setdefault(identifier, []).append(
            {"body": body, "authored_by_pipeline": True,
             "created_at": "2026-10-18T15:00:00Z"})
        return None


def door(rows=None, reasons=None):
    rows = WEEK["door"]["decisions"] if rows is None else rows
    reasons = WEEK["door"]["reasons"] if reasons is None else reasons

    def groom_removals(since=None):
        return {"schema": bureau_read.SCHEMA, "reasons": list(reasons),
                "decisions": [dict(r) for r in rows]}
    return groom_removals


def door_down(reason="unavailable", detail="BUREAU_READ_URL is not set"):
    def groom_removals(since=None):
        raise bureau_read.ReadUnknown(reason, detail, unavailable=True)
    return groom_removals


def row(pid, card_id, decision, *, reason=None, comment=None,
        at="2026-10-13T16:00:00Z", standing=CARD, ident=1):
    return {"id": ident, "recorded_at": at, "standing_card": standing,
            "proposal_id": pid, "card": card_id, "decision": decision,
            "reason": reason, "hold_until_card": None, "hold_until_date": None,
            "actor_name": "Sid", "comment": comment}


def proposal(pid, planning, cancel=(), *, at="2026-10-13T13:00:00Z"):
    """A proposal comment `groomer.parse_proposal_comment` reads back."""
    w = [f"{groomer.MARK} {groomer.PROPOSAL_TAG}: {pid}", "",
         f"# Groom proposal `{pid}` — cycle 15", "",
         "## The batch, in order", "",
         "| # | Card | Pri | Repo | Epic | Title |",
         "| -- | -- | -- | -- | -- | -- |"]
    w += [f"| {n} | {c} | High | bureau-pipeline | — | a card |"
          for n, c in enumerate(planning, 1)]
    if cancel:
        w += ["", groomer.CANCEL_HEADING, "", groomer.CANCEL_COLUMNS,
              "| -- | -- | -- | -- | -- | -- | -- |"]
        w += [f"| {n} | {c} | Low | bureau-pipeline | — | a card | done |"
              for n, c in enumerate(cancel, 1)]
    assert groomer.parse_proposal_comment("\n".join(w))["id"] == pid
    return {"body": "\n".join(w) + "\n", "authored_by_pipeline": True,
            "created_at": at}


def drained(pid, outcomes, *, at="2026-10-13T18:00:00Z"):
    """A `groom-drained` record, written by `groomer.drained_record` itself."""
    rows = [{"identifier": c, "outcome": o, "why": f"proposal `{pid}`"}
            for c, o in outcomes.items()]
    body = groomer.drained_record({
        "proposal": pid, "rows": rows, "refused": [], "to": "Planning",
        "from": "Intake", "cycle": 15,
        "moved": [c for c, o in outcomes.items() if o == "moved"]})
    return {"body": body, "authored_by_pipeline": True, "created_at": at}


def run(monkeypatch, capsys, records, *, removals=None, argv=(),
        ending=ENDING):
    ops = Ops({CARD: records})
    monkeypatch.setattr(groom_scorecard, "linear_ops", ops)
    monkeypatch.setattr(bureau_read, "groom_removals", removals or door())
    code = groom_scorecard.main(["score", "--card", CARD,
                                 "--week-ending", ending, *argv])
    out, err = capsys.readouterr()
    return code, ops, out, err


def score(records, *, removals=None, ending=ENDING):
    answer, problem = None, None
    try:
        answer = (removals or door())()
    except bureau_read.ReadUnknown as e:
        problem = f"{e.reason}: {e.detail}"
    return groom_scorecard.score(records, standing_card=CARD,
                                 week_ending=ending, door=answer,
                                 door_problem=problem)


def tagged(err):
    return [line for line in err.splitlines()
            if line.startswith(groom_scorecard.LINE_TAG)]


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------
def test_the_contract_constants():
    assert groom_scorecard.MARK == "📊"
    assert groom_scorecard.SCORECARD_TAG == "groom-scorecard"
    assert groom_scorecard.LINE_TAG == "groom-scorecard:"
    assert groom_scorecard.SCHEMA == "groom-scorecard/1"


def test_the_fixture_week_scores_the_counts_stated_by_hand():
    """The week of 2026-10-11 on DRE-4541, counted by hand off the thread.

    Seven proposals were posted. `ab09ece4ddee` (Sunday) has no drain, no
    decline and no per-card decision — unanswered. The other six were
    drained. Their rows, Planning + Cancel:
      06e997f2ba4c 15 + 3 · 3381b8d562a8 12 + 3 · d025c5a5baf1 4 + 1 ·
      3fa8dd194048 17 + 1 · 60bab694a603 5 + 0 · 51e3653fb40a 2 + 3   = 66.
    Removed, all Planning rows: DRE-7000 and DRE-3589 on 06e997f2ba4c
    (`groom-excluded — move to Cancel`; DRE-7000 also a door `cancel` row
    with no reason), and DRE-2503 on 3381b8d562a8 (its marker AND its
    door row, `waits-on-other-work`) — three, 3/66 = 0.0455.
    Cancel rows: 06e997f2ba4c DRE-6894, DRE-7019, DRE-3386 · 3381b8d562a8
    DRE-3333, DRE-7060, DRE-4054 · d025c5a5baf1 DRE-5199 · 3fa8dd194048
    DRE-7273 · 51e3653fb40a DRE-7377, DRE-7085, DRE-7076 — every one
    `cancelled` on its drain, 11 agreed; ab09ece4ddee's DRE-6894, DRE-3386,
    DRE-3333 have no drain, 3 unanswered. Nothing kept.
    """
    result = score(week_thread())
    assert result == {
        "schema": "groom-scorecard/1",
        "week_start": "2026-10-11", "week_end": "2026-10-17",
        "standing_card": "DRE-4541",
        "proposals": [
            {"id": "ab09ece4ddee", "posted_at": "2026-10-11T13:13:54.437Z",
             "planning": 14, "cancel": 3, "answered": False},
            {"id": "06e997f2ba4c", "posted_at": "2026-10-12T13:32:44.655Z",
             "planning": 15, "cancel": 3, "answered": True},
            {"id": "3381b8d562a8", "posted_at": "2026-10-13T13:21:44.090Z",
             "planning": 12, "cancel": 3, "answered": True},
            {"id": "d025c5a5baf1", "posted_at": "2026-10-14T13:11:18.754Z",
             "planning": 4, "cancel": 1, "answered": True},
            {"id": "3fa8dd194048", "posted_at": "2026-10-15T13:13:05.222Z",
             "planning": 17, "cancel": 1, "answered": True},
            {"id": "60bab694a603", "posted_at": "2026-10-16T13:12:43.855Z",
             "planning": 5, "cancel": 0, "answered": True},
            {"id": "51e3653fb40a", "posted_at": "2026-10-17T13:10:14.691Z",
             "planning": 2, "cancel": 3, "answered": True},
        ],
        "proposed": 66, "removed": 3, "removed_share": 0.0455,
        "removed_by_reason": [
            {"reason": None, "label": "move to Cancel", "count": 2},
            {"reason": "waits-on-other-work",
             "label": "Not ready: waits on other work", "count": 1},
        ],
        "cancel": {"rows": 14, "agreed": 11, "kept": 0, "unanswered": 3},
        "memory_source": "record", "door_problem": None,
    }


def test_an_unanswered_proposal_is_listed_and_counted_nowhere_else():
    pid, other = "a1a1a1a1a1a1", "b2b2b2b2b2b2"
    records = [proposal(pid, ["DRE-1", "DRE-2"], ["DRE-3", "DRE-4"]),
               proposal(other, ["DRE-5"], ["DRE-6"]),
               said(groomer.EXCLUDE_TAG, other, "DRE-5", "Already done")]
    result = score(records, removals=door(rows=[]))
    assert [(p["id"], p["answered"]) for p in result["proposals"]] == [
        (pid, False), (other, True)]
    assert result["proposed"] == 2                      # DRE-5 and DRE-6 only
    assert result["removed"] == 1                       # DRE-5
    # Both Cancel lists have no drain: DRE-3, DRE-4 and DRE-6.
    assert result["cancel"] == {"rows": 3, "agreed": 0, "kept": 0,
                                "unanswered": 3}


def test_a_proposal_counts_as_answered_by_a_drain_a_decline_or_a_door_row():
    drained_pid, declined_pid, door_pid, silent = (
        "c3c3c3c3c3c3", "d4d4d4d4d4d4", "e5e5e5e5e5e5", "f6f6f6f6f6f6")
    records = [proposal(drained_pid, ["DRE-1"]),
               proposal(declined_pid, ["DRE-2"]),
               proposal(door_pid, ["DRE-3"]),
               proposal(silent, ["DRE-4"]),
               drained(drained_pid, {"DRE-1": "moved"}),
               said(groomer.DECLINE_TAG, declined_pid, tail="not this week")]
    result = score(records, removals=door(rows=[
        row(door_pid, "DRE-3", "put-back")]))
    answered = {p["id"]: p["answered"] for p in result["proposals"]}
    assert answered == {drained_pid: True, declined_pid: True,
                        door_pid: True, silent: False}
    assert result["proposed"] == 3 and result["removed"] == 0


def test_a_decision_the_pipeline_wrote_answers_nothing():
    pid = "a7a7a7a7a7a7"
    records = [proposal(pid, ["DRE-1"]),
               said(groomer.EXCLUDE_TAG, pid, "DRE-1", "nope",
                    by_pipeline=True),
               said(groomer.DECLINE_TAG, pid, tail="nope", by_pipeline=True)]
    result = score(records, removals=door(rows=[]))
    assert result["proposals"][0]["answered"] is False
    assert result["proposed"] == 0 and result["removed"] == 0


def test_a_row_and_its_mirrored_marker_count_once():
    pid = "a8a8a8a8a8a8"
    records = [proposal(pid, ["DRE-1", "DRE-2"]),
               said(groomer.EXCLUDE_TAG, pid, "DRE-1",
                    "Not ready: waits on other work",
                    at="2026-10-13T16:00:01Z")]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-1", "not-doing", reason="waits-on-other-work",
            at="2026-10-13T16:00:00Z")]))
    assert result["removed"] == 1
    assert result["removed_by_reason"] == [
        {"reason": "waits-on-other-work",
         "label": "Not ready: waits on other work", "count": 1}]


def test_a_put_back_row_and_an_added_marker_are_no_removal():
    pid = "a9a9a9a9a9a9"
    records = [proposal(pid, ["DRE-1", "DRE-2", "DRE-3"]),
               said(groomer.EXCLUDE_TAG, pid, "DRE-2", "later",
                    at="2026-10-13T15:00:00Z"),
               said(groomer.ADD_TAG, pid, "DRE-2", at="2026-10-13T15:30:00Z"),
               said(groomer.ADD_TAG, pid, "DRE-9")]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-1", "put-back")]))
    assert result["proposed"] == 3
    assert result["removed"] == 0
    assert result["removed_by_reason"] == []
    assert result["removed_share"] == 0.0


def test_a_removal_with_no_reason_anywhere_is_no_reason_recorded():
    pid = "b1b1b1b1b1b1"
    records = [proposal(pid, ["DRE-1", "DRE-2", "DRE-3"], ["DRE-4"]),
               said(groomer.HOLD_TAG, pid, "DRE-1"),
               said(groomer.EXCLUDE_TAG, pid, "DRE-4")]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-2", "hold"),
        row(pid, "DRE-3", "not-doing", comment="The CEO's own words")]))
    assert result["removed"] == 4
    # DRE-1 (a hold marker with no tail), DRE-2 (a door hold with no reason
    # and no comment) and DRE-4 (a Cancel row excluded with no tail); DRE-3
    # is taken off for its row's comment.
    assert result["removed_by_reason"] == [
        {"reason": None, "label": "no reason recorded", "count": 3},
        {"reason": None, "label": "The CEO's own words", "count": 1}]


def test_a_door_label_comes_before_the_comment_and_the_marker_tail():
    pid = "b2b2b2b2b2b3"
    records = [proposal(pid, ["DRE-1", "DRE-2"]),
               said(groomer.EXCLUDE_TAG, pid, "DRE-1", "my own words"),
               said(groomer.EXCLUDE_TAG, pid, "DRE-2", "my other words")]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-1", "not-doing", reason="already-done",
            comment="a comment"),
        row(pid, "DRE-2", "not-doing", comment="a comment")]))
    assert result["removed_by_reason"] == [
        {"reason": "already-done", "label": "Already done", "count": 1},
        {"reason": None, "label": "a comment", "count": 1}]


def test_the_cancel_list_removal_rules():
    """A Cancel row is taken off by a `not-doing` or `hold`, or by an
    exclusion or hold marker — and a `cancel` decision on one is agreement,
    not a removal."""
    pid = "b3b3b3b3b3b3"
    records = [proposal(pid, [], ["DRE-1", "DRE-2", "DRE-3", "DRE-4"]),
               said(groomer.HOLD_TAG, pid, "DRE-1", "not yet"),
               drained(pid, {"DRE-1": "held back", "DRE-2": "held back",
                             "DRE-3": "cancelled", "DRE-4": "cancelled"})]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-2", "not-doing", reason="already-done"),
        row(pid, "DRE-3", "cancel")]))
    assert result["proposed"] == 4
    assert result["removed"] == 2                        # DRE-1 and DRE-2
    assert result["cancel"] == {"rows": 4, "agreed": 2, "kept": 2,
                                "unanswered": 0}


def test_a_planning_row_cancel_decision_is_a_removal():
    pid = "b4b4b4b4b4b4"
    records = [proposal(pid, ["DRE-1"]), drained(pid, {"DRE-1": "held back"})]
    result = score(records, removals=door(rows=[row(pid, "DRE-1", "cancel")]))
    assert result["removed"] == 1


def test_a_door_row_on_another_standing_card_is_not_read():
    pid = "b5b5b5b5b5b5"
    records = [proposal(pid, ["DRE-1"])]
    result = score(records, removals=door(rows=[
        row(pid, "DRE-1", "not-doing", standing="DRE-4488")]))
    assert result["proposals"][0]["answered"] is False
    assert result["removed"] == 0


def test_the_week_is_seven_pacific_days_sunday_to_saturday():
    inside = [proposal("c1c1c1c1c1c1", ["DRE-1"], at="2026-10-11T07:00:00Z"),
              proposal("c2c2c2c2c2c2", ["DRE-2"], at="2026-10-18T06:59:00Z")]
    outside = [proposal("c3c3c3c3c3c3", ["DRE-3"], at="2026-10-11T06:59:00Z"),
               proposal("c4c4c4c4c4c4", ["DRE-4"], at="2026-10-18T07:00:00Z")]
    result = score(inside + outside, removals=door(rows=[]))
    assert (result["week_start"], result["week_end"]) == (
        "2026-10-11", "2026-10-17")
    assert [p["id"] for p in result["proposals"]] == [
        "c1c1c1c1c1c1", "c2c2c2c2c2c2"]


def test_no_answered_proposal_has_no_share():
    result = score([proposal("c5c5c5c5c5c5", ["DRE-1"])],
                   removals=door(rows=[]))
    assert result["proposed"] == 0 and result["removed_share"] is None


def test_the_default_week_ends_the_saturday_before_today_pacific():
    # Sunday 2026-10-18 08:00 PT
    assert groom_scorecard.default_week_ending(
        datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)) == "2026-10-17"
    # Saturday 2026-10-24 23:30 PT is still Saturday: the one before it.
    assert groom_scorecard.default_week_ending(
        datetime(2026, 10, 25, 6, 30, tzinfo=timezone.utc)) == "2026-10-17"
    # Wednesday 2026-10-21
    assert groom_scorecard.default_week_ending(
        datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)) == "2026-10-17"


def test_a_week_ending_that_is_not_a_saturday_is_refused(capsys):
    with pytest.raises(SystemExit):
        groom_scorecard.main(["score", "--card", CARD,
                              "--week-ending", "2026-10-18"])


# --------------------------------------------------------------------------
# the drained record, read in the grammar `groomer.drained_record` writes
# --------------------------------------------------------------------------
def test_the_drained_reader_is_pinned_against_drained_record():
    pid = "d1d1d1d1d1d1"
    outcomes = {"DRE-1": "moved", "DRE-2": "held back", "DRE-3": "cancelled",
                "DRE-4": "already gone", "DRE-5": "added", "DRE-6": "refused"}
    assert groom_scorecard.drained_rows(drained(pid, outcomes)["body"]) == {
        "proposal": pid, "rows": outcomes}


def test_the_drained_reader_reads_every_real_record_in_the_fixture():
    bodies = [r["body"] for r in WEEK["thread"]
              if groomer.decision_match(groomer.DRAINED_TAG, r["body"])]
    assert len(bodies) == 6
    read = [groom_scorecard.drained_rows(b) for b in bodies]
    first = read[0]
    assert first["proposal"] == "06e997f2ba4c"
    assert first["rows"]["DRE-6933"] == "moved"
    assert first["rows"]["DRE-7000"] == "held back"
    assert first["rows"]["DRE-6894"] == "cancelled"
    assert groom_scorecard.drained_rows("not a record") is None


# --------------------------------------------------------------------------
# the console's record could not be read
# --------------------------------------------------------------------------
def test_with_the_door_down_the_score_comes_from_the_markers_alone(
        monkeypatch, capsys):
    code, ops, out, err = run(monkeypatch, capsys, week_thread(),
                              removals=door_down(), argv=["--post"])
    assert code == 0
    [(_, body)] = ops.posted
    result = groom_scorecard.parse_comment(body)
    assert result["memory_source"] == "fallback"
    assert result["door_problem"] == "unavailable: BUREAU_READ_URL is not set"
    # The same three removals, read off the markers: DRE-2503's reason is its
    # marker's own words now, with no key to it.
    assert result["removed"] == 3
    assert result["removed_by_reason"] == [
        {"reason": None, "label": "move to Cancel", "count": 2},
        {"reason": None, "label": "Not ready: waits on other work",
         "count": 1}]
    lines = body.splitlines()
    unread = [l for l in lines if l.startswith(groom_scorecard.DOOR_UNREAD)]
    assert len(unread) == 1
    assert "unavailable: BUREAU_READ_URL is not set" in unread[0]
    assert tagged(err)[0].endswith("source=fallback")


def test_with_the_door_read_the_comment_says_nothing_of_it(monkeypatch,
                                                         capsys):
    code, ops, out, err = run(monkeypatch, capsys, week_thread(),
                              argv=["--post"])
    [(_, body)] = ops.posted
    assert groom_scorecard.DOOR_UNREAD not in body


# --------------------------------------------------------------------------
# posting
# --------------------------------------------------------------------------
def test_post_writes_one_comment_once_per_week(monkeypatch, capsys):
    records = week_thread()
    code, ops, out, err = run(monkeypatch, capsys, records, argv=["--post"])
    assert code == 0
    [(card, body)] = ops.posted
    assert card == CARD
    assert body.splitlines()[0] == "📊 groom-scorecard: 2026-10-11..2026-10-17"
    assert groom_scorecard.parse_comment(body) == score(week_thread())
    fenced = re.findall(r"^```json\n(.*?)\n```$", body, re.S | re.M)
    assert len(fenced) == 1 and json.loads(fenced[0]) == score(week_thread())

    # The second run reads the comment the first one wrote, and writes nothing.
    ops2 = Ops(ops.threads)
    monkeypatch.setattr(groom_scorecard, "linear_ops", ops2)
    assert groom_scorecard.main(["score", "--card", CARD, "--week-ending",
                                 ENDING, "--post"]) == 0
    out2, err2 = capsys.readouterr()
    assert ops2.posted == []
    assert "groom-scorecard: already posted 2026-10-11..2026-10-17" in out2
    assert len(tagged(err2)) == 1


def test_another_weeks_scorecard_does_not_stop_this_one(monkeypatch, capsys):
    earlier = {"body": "📊 groom-scorecard: 2026-10-04..2026-10-10\n",
               "authored_by_pipeline": True,
               "created_at": "2026-10-11T15:00:00Z"}
    code, ops, out, err = run(monkeypatch, capsys, [earlier, *week_thread()],
                              argv=["--post"])
    assert len(ops.posted) == 1


def test_without_post_nothing_is_written(monkeypatch, capsys, tmp_path):
    out_file = tmp_path / "score.json"
    code, ops, out, err = run(monkeypatch, capsys, week_thread(),
                              argv=["--out", str(out_file)])
    assert code == 0 and ops.posted == []
    assert json.loads(out_file.read_text()) == score(week_thread())


def test_no_decision_reader_reads_the_scorecard_as_a_decision():
    body = groom_scorecard.render_comment(score(week_thread()))
    for tag in (*groomer.DECISION_TAGS, groomer.DRAINED_TAG):
        assert groomer.decision_match(tag, body) is None
    for tag in groomer.REPO_TAGS:
        assert groomer.repo_switch_match(tag, body) is None
    assert groomer.parse_proposal_comment(body) is None
    record = {"body": body, "authored_by_pipeline": False,
              "created_at": "2026-10-18T15:00:00Z"}
    assert groomer.per_card_markers([record], CARD) == []
    assert groomer.read_decisions([record])["drained"] == []


def test_an_approved_proposal_nobody_drained_is_still_unanswered(monkeypatch,
                                                                 capsys):
    """The definition is the card's: a drain, a decline or a per-card
    decision answers a proposal. An approval the drain never acted on
    answers nothing, and the comment says it was approved."""
    pid = "e1e1e1e1e1e1"
    records = [proposal(pid, ["DRE-1"]), said(groomer.APPROVAL_TAG, pid)]
    code, ops, out, err = run(monkeypatch, capsys, records,
                              removals=door(rows=[]))
    assert "proposals=1 answered=0 proposed=0" in tagged(err)[0]
    assert f"`{pid}`" in out and "approved, never drained" in out


def test_the_comment_says_the_week_in_plain_words():
    body = groom_scorecard.render_comment(score(week_thread()))
    prose = body.split("```json")[0]
    assert "66 cards" in prose and "removed 3" in prose
    assert "move to Cancel" in prose
    assert "ab09ece4ddee" in prose                      # the unanswered one


# --------------------------------------------------------------------------
# the one line
# --------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [[], ["--post"]])
def test_every_run_prints_exactly_one_line_in_the_grammar(monkeypatch, capsys,
                                                         argv):
    code, ops, out, err = run(monkeypatch, capsys, week_thread(), argv=argv)
    [line] = tagged(err)
    assert LINE.match(line), line
    assert line == ("groom-scorecard: week=2026-10-11..2026-10-17 proposals=7 "
                    "answered=6 proposed=66 removed=3 share=0.0455 "
                    "cancel_rows=14 agreed=11 kept=0 unanswered=3 "
                    "source=record")
    assert tagged(out) == []


def test_an_empty_week_prints_share_none(monkeypatch, capsys):
    code, ops, out, err = run(monkeypatch, capsys, [], argv=[])
    [line] = tagged(err)
    assert LINE.match(line) and " share=none " in line
