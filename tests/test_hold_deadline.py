"""Every hold kind gets a declared due date, and one writer says when one is late
(DRE-7964).

The pipeline's three hold kinds — `proof-waiting`, `hand-work-overdue` and
`unlanded-work-watchdog` — are `kind: hold` acts with `cadence_s: null`, so
nothing said when one was overdue and they sat for weeks. `config/
hold-deadlines.json` declares each one's chase and due date, and
`scripts/hold_deadline.py` reads it: `phase()` names where a hold stands and
`settle()` is the one writer of the `hold-late:<repo>:<tag>` console alert.

What this file proves:

* the shipped file passes `check` against the shipped act registry — this test
  is the CI gate that turns a later change to either file red;
* `check` names each of the six problems the card lists;
* `phase()` at its boundaries for each of the three tags;
* `settle()`'s five paths — a raise, a raise on a short read, a resolve, no
  door call, a door failure — each printing one `hold-deadline:` line and
  none of them filing a Linear card.

Run: cd bureau-pipeline && python3 -m pytest tests/test_hold_deadline.py -v
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hold_deadline  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402

CONFIG = ROOT / "config" / "hold-deadlines.json"
ACTS = ROOT / "config" / "pipeline-acts.json"
HAND_WORK_AGE = ROOT / "config" / "hand-work-age.json"

T0 = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)  # 09:00 PT


def shipped() -> tuple[dict, dict, dict]:
    return (json.loads(CONFIG.read_text()), json.loads(ACTS.read_text()),
            json.loads(HAND_WORK_AGE.read_text()))


def problems_with(mutate) -> list[str]:
    doc, acts, age = shipped()
    doc, acts, age = copy.deepcopy(doc), copy.deepcopy(acts), copy.deepcopy(age)
    mutate(doc, acts, age)
    return hold_deadline.problems(doc, acts, age)


# ── the shipped file ─────────────────────────────────────────────────────────


class TestShippedFile:
    def test_check_passes_on_the_shipped_files(self, capsys):
        assert hold_deadline.main(["check"]) == 0
        assert "0 problem(s)" in capsys.readouterr().out

    def test_check_is_the_default_command(self):
        assert hold_deadline.main([]) == 0

    def test_exactly_the_three_tags_with_the_contracts_numbers(self):
        doc = json.loads(CONFIG.read_text())
        assert tuple(sorted(doc)) == tuple(sorted(hold_deadline.TAGS))
        assert hold_deadline.TAGS == (
            "proof-waiting", "hand-work-overdue", "unlanded-work-watchdog")
        numbers = {tag: (row["chase_after_hours"], row["due_hours"])
                   for tag, row in doc.items()}
        assert numbers == {
            "proof-waiting": (24, 72),
            "hand-work-overdue": (24, 72),
            "unlanded-work-watchdog": (None, 24),
        }

    def test_every_row_carries_its_sentences(self):
        for tag, row in json.loads(CONFIG.read_text()).items():
            for field in ("chase", "clock", "why"):
                assert isinstance(row[field], str) and row[field].strip(), (tag, field)

    def test_the_proof_waiting_chase_names_the_hand_work_run_and_the_re_check(self):
        chase = json.loads(CONFIG.read_text())["proof-waiting"]["chase"]
        assert "DRE-7967" in chase and "Hand-work" in chase
        assert "DRE-7951" in chase and "In Review" in chase

    def test_the_act_registry_keeps_its_holds_null(self):
        acts = json.loads(ACTS.read_text())["acts"]
        for act in acts:
            if act["tag"] in hold_deadline.TAGS:
                assert act["kind"] == "hold"
                assert act["cadence_s"] is None


# ── check names each problem ─────────────────────────────────────────────────


class TestCheckProblems:
    def test_the_shipped_files_have_no_problem(self):
        assert hold_deadline.problems(*shipped()) == []

    def test_an_unknown_tag(self):
        def add(doc, acts, age):
            doc["no-such-act"] = dict(doc["proof-waiting"])
        found = problems_with(add)
        assert any("no-such-act" in p and "not a kind: hold act" in p for p in found), found

    def test_a_missing_tag(self):
        found = problems_with(lambda doc, acts, age: doc.pop("unlanded-work-watchdog"))
        assert any("unlanded-work-watchdog" in p and "missing" in p for p in found), found

    def test_a_tag_whose_act_is_not_a_hold(self):
        def unhold(doc, acts, age):
            for act in acts["acts"]:
                if act["tag"] == "proof-waiting":
                    act["kind"] = "progress"
        found = problems_with(unhold)
        assert any("proof-waiting" in p and "kind: progress" in p for p in found), found

    def test_a_hold_act_that_is_not_one_of_the_three(self):
        def add(doc, acts, age):
            doc["stranded-watchdog"] = dict(doc["proof-waiting"])
        found = problems_with(add)
        assert any("stranded-watchdog" in p for p in found), found

    @pytest.mark.parametrize("chase", [72, 80])
    def test_a_chase_at_or_past_the_due(self, chase):
        def late_chase(doc, acts, age):
            doc["proof-waiting"]["chase_after_hours"] = chase
        found = problems_with(late_chase)
        assert any("proof-waiting" in p and "chase_after_hours" in p
                   and "due_hours" in p for p in found), found

    @pytest.mark.parametrize("field,value", [
        ("due_hours", 0), ("due_hours", -5), ("chase_after_hours", 0),
        ("chase_after_hours", -1), ("due_hours", None), ("due_hours", True),
        ("due_hours", "72"),
    ])
    def test_a_zero_negative_or_non_number(self, field, value):
        def bad(doc, acts, age):
            doc["unlanded-work-watchdog"][field] = value
        found = problems_with(bad)
        assert any("unlanded-work-watchdog" in p and field in p
                   and "positive number" in p for p in found), found

    def test_a_hand_work_chase_that_differs_from_hand_work_age(self):
        found = problems_with(lambda doc, acts, age: age.update(hours=12))
        assert any("hand-work-overdue" in p and "hand-work-age.json" in p
                   for p in found), found

    def test_a_hand_work_chase_of_null_differs_too(self):
        def null(doc, acts, age):
            doc["hand-work-overdue"]["chase_after_hours"] = None
        found = problems_with(null)
        assert any("hand-work-overdue" in p and "hand-work-age.json" in p
                   for p in found), found

    def test_a_missing_sentence(self):
        found = problems_with(lambda doc, acts, age: doc["proof-waiting"].pop("clock"))
        assert any("proof-waiting" in p and "clock" in p for p in found), found

    def test_check_exits_1_and_prints_each_problem(self, tmp_path, capsys):
        doc, _, _ = shipped()
        doc.pop("hand-work-overdue")
        bad = tmp_path / "hold-deadlines.json"
        bad.write_text(json.dumps(doc))
        assert hold_deadline.main(["check", "--config", str(bad)]) == 1
        out = capsys.readouterr().out
        assert "hold-deadline:" in out and "hand-work-overdue" in out
        assert "1 problem(s)" in out

    def test_an_unreadable_file_fails(self, tmp_path, capsys):
        bad = tmp_path / "hold-deadlines.json"
        bad.write_text("{not json")
        assert hold_deadline.main(["check", "--config", str(bad)]) == 1
        assert "unreadable" in capsys.readouterr().out


# ── phase at its boundaries ──────────────────────────────────────────────────


H = timedelta(hours=1)
S = timedelta(seconds=1)


class TestPhase:
    @pytest.mark.parametrize("tag", ["proof-waiting", "hand-work-overdue"])
    def test_a_chased_hold(self, tag):
        assert hold_deadline.chase_at(tag, T0) == T0 + 24 * H
        assert hold_deadline.due_at(tag, T0) == T0 + 72 * H
        assert hold_deadline.phase(tag, T0, T0) == hold_deadline.WAITING
        assert hold_deadline.phase(tag, T0, T0 + 24 * H - S) == "waiting"
        assert hold_deadline.phase(tag, T0, T0 + 24 * H) == "chase"
        assert hold_deadline.phase(tag, T0, T0 + 72 * H - S) == hold_deadline.CHASE
        assert hold_deadline.phase(tag, T0, T0 + 72 * H) == "late"
        assert hold_deadline.phase(tag, T0, T0 + 500 * H) == hold_deadline.LATE

    def test_a_hold_with_no_chase_waits_until_it_is_late(self):
        tag = "unlanded-work-watchdog"
        assert hold_deadline.chase_at(tag, T0) is None
        assert hold_deadline.due_at(tag, T0) == T0 + 24 * H
        for at in (T0, T0 + 12 * H, T0 + 24 * H - S):
            assert hold_deadline.phase(tag, T0, at) == "waiting"
        assert hold_deadline.phase(tag, T0, T0 + 24 * H) == "late"

    def test_an_unknown_tag_is_refused(self):
        with pytest.raises(ValueError):
            hold_deadline.phase("stranded-watchdog", T0, T0)


# ── settle, the one writer ───────────────────────────────────────────────────


class Door:
    """The console door, stubbed at `pipeline_notice._send`: every payload it
    is handed, and the outcomes it answers in turn."""

    def __init__(self, *answers):
        self.payloads: list[dict] = []
        self.answers = list(answers)

    def __call__(self, payload):
        self.payloads.append(payload)
        answer = self.answers.pop(0) if self.answers else "raised"
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def door(monkeypatch):
    def install(*answers):
        stub = Door(*answers)
        monkeypatch.setattr(pipeline_notice, "_send", stub)
        return stub
    return install


@pytest.fixture(autouse=True)
def no_linear(monkeypatch):
    """Nothing here may file a Linear card: every route to one blows up."""
    def refuse(*a, **k):
        raise AssertionError("a Linear write was attempted")
    for name in ("create_card", "find_open", "find_open_prefix", "cmd_comment"):
        monkeypatch.setattr(linear_ops, name, refuse)


def hold_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("hold-deadline:")]


NOW = T0 + 100 * H
OLD = hold_deadline.LateHold("DRE-11", T0, "the operator's press: open the console")
NEW = hold_deadline.LateHold("DRE-12", T0 + 10 * H, "an observation of the next release")


class TestSettle:
    def test_a_late_list_raises_the_alert_with_no_fallback(self, door, capsys):
        d = door("raised")
        outcome = hold_deadline.settle("proof-waiting", "agent-bureau", [NEW, OLD],
                                       complete=True, now=NOW)
        assert outcome == "raised"
        [payload] = d.payloads
        assert payload["action"] == "raise"
        assert payload["notice"] == "hold-late:agent-bureau:proof-waiting"
        assert payload["severity"] == "WARN"
        assert payload["title"] == "2 proof-waiting hold(s) past their due date in agent-bureau"
        assert payload["title"] == hold_deadline.TITLE.format(
            n=2, tag="proof-waiting", repo="agent-bureau")
        lines = payload["body"].splitlines()
        assert lines == [
            "DRE-11 — since 2026-10-20 09:00 PT, due 2026-10-23 09:00 PT, "
            "28 hours late, waits on the operator's press: open the console",
            "DRE-12 — since 2026-10-20 19:00 PT, due 2026-10-23 19:00 PT, "
            "18 hours late, waits on an observation of the next release",
        ]
        assert hold_lines(capsys.readouterr().out) == [
            "hold-deadline: proof-waiting repo=agent-bureau late=2 "
            "cards=DRE-11,DRE-12 complete=yes outcome=raised"]

    def test_the_raise_is_handed_no_fallback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pipeline_notice, "raise_notice",
                            lambda key, **kw: calls.append((key, kw)) or "raised")
        hold_deadline.settle("hand-work-overdue", "bureau-pipeline", [OLD],
                             complete=True, now=NOW)
        [(key, kw)] = calls
        assert key == "hold-late:bureau-pipeline:hand-work-overdue"
        assert kw.get("fallback") is None
        assert kw["severity"] == "WARN"

    def test_a_short_read_with_late_holds_still_raises_with_the_unread_line(
            self, door, capsys):
        d = door("raised")
        outcome = hold_deadline.settle("hand-work-overdue", "portico", [OLD],
                                       complete=False, now=NOW)
        assert outcome == "raised"
        [payload] = d.payloads
        assert payload["action"] == "raise"
        assert payload["title"] == "1 hand-work-overdue hold(s) past their due date in portico"
        lines = payload["body"].splitlines()
        assert lines[0].startswith("DRE-11 — ")
        assert lines[-1] == hold_deadline.UNREAD_LINE
        assert hold_deadline.UNREAD_LINE == (
            "Some holds of this kind could not be read on this pass, "
            "so this list may be short.")
        assert hold_lines(capsys.readouterr().out) == [
            "hold-deadline: hand-work-overdue repo=portico late=1 "
            "cards=DRE-11 complete=no outcome=raised"]

    def test_a_complete_read_with_nothing_late_resolves(self, door, capsys):
        d = door("resolved")
        outcome = hold_deadline.settle("unlanded-work-watchdog", "agent-bureau", [],
                                       complete=True, now=NOW)
        assert outcome == "resolved"
        assert d.payloads == [{"action": "resolve",
                               "notice": "hold-late:agent-bureau:unlanded-work-watchdog"}]
        assert hold_lines(capsys.readouterr().out) == [
            "hold-deadline: unlanded-work-watchdog repo=agent-bureau late=0 "
            "cards=none complete=yes outcome=resolved"]

    def test_a_short_read_with_nothing_late_calls_no_door(self, door, monkeypatch, capsys):
        d = door()
        for name in ("raise_notice", "resolve_notice"):
            monkeypatch.setattr(pipeline_notice, name,
                                lambda *a, **k: pytest.fail("the door was called"))
        outcome = hold_deadline.settle("proof-waiting", "agent-bureau", [],
                                       complete=False, now=NOW)
        assert outcome == hold_deadline.UNREAD == "unread"
        assert d.payloads == []
        assert hold_lines(capsys.readouterr().out) == [
            "hold-deadline: proof-waiting repo=agent-bureau late=0 "
            "cards=none complete=no outcome=unread"]

    def test_a_repeat_sends_the_newest_list(self, door, capsys):
        d = door("raised", "repeat", "escalated")
        key = "hold-late:agent-bureau:proof-waiting"
        first = hold_deadline.settle("proof-waiting", "agent-bureau", [OLD],
                                     complete=True, now=NOW)
        second = hold_deadline.settle("proof-waiting", "agent-bureau", [OLD, NEW],
                                      complete=True, now=NOW + H)
        third = hold_deadline.settle("proof-waiting", "agent-bureau", [NEW],
                                     complete=False, now=NOW + 2 * H)
        assert (first, second, third) == ("raised", "repeat", "escalated")
        assert [p["notice"] for p in d.payloads] == [key] * 3
        assert [p["title"] for p in d.payloads] == [
            "1 proof-waiting hold(s) past their due date in agent-bureau",
            "2 proof-waiting hold(s) past their due date in agent-bureau",
            "1 proof-waiting hold(s) past their due date in agent-bureau",
        ]
        assert d.payloads[0]["body"] != d.payloads[1]["body"]
        assert "29 hours late" in d.payloads[1]["body"]
        assert len(d.payloads[1]["body"].splitlines()) == 2
        assert d.payloads[2]["body"].splitlines()[0].startswith("DRE-12 — ")
        assert d.payloads[2]["body"].splitlines()[-1] == hold_deadline.UNREAD_LINE
        outcomes = [ln.rsplit("outcome=", 1)[1]
                    for ln in hold_lines(capsys.readouterr().out)]
        assert outcomes == ["raised", "repeat", "escalated"]

    def test_an_unreachable_door_is_lost_and_files_nothing(self, door, capsys):
        door(pipeline_notice.DoorMissed("unreachable", "connection refused"))
        outcome = hold_deadline.settle("proof-waiting", "agent-bureau", [OLD],
                                       complete=True, now=NOW)
        assert outcome == "lost"
        assert hold_lines(capsys.readouterr().out) == [
            "hold-deadline: proof-waiting repo=agent-bureau late=1 "
            "cards=DRE-11 complete=yes outcome=lost"]

    def test_a_refused_resolve_is_named_and_not_raised(self, door, capsys):
        door(pipeline_notice.DoorMissed("http-503", "down"))
        outcome = hold_deadline.settle("proof-waiting", "agent-bureau", [],
                                       complete=True, now=NOW)
        assert outcome == "resolve-refused"
        assert len(hold_lines(capsys.readouterr().out)) == 1

    @pytest.mark.parametrize("late", [[OLD], []])
    def test_a_door_that_throws_never_throws_out_of_settle(self, monkeypatch, capsys, late):
        def boom(*a, **k):
            raise RuntimeError("the client itself broke")
        monkeypatch.setattr(pipeline_notice, "raise_notice", boom)
        monkeypatch.setattr(pipeline_notice, "resolve_notice", boom)
        outcome = hold_deadline.settle("proof-waiting", "agent-bureau", late,
                                       complete=True, now=NOW)
        assert outcome == "lost"
        [line] = hold_lines(capsys.readouterr().out)
        assert line.endswith("complete=yes outcome=lost")

    def test_the_log_tag(self):
        assert hold_deadline.TAG == "hold-deadline:"
