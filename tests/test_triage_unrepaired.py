"""One console alert lists every Triage card the repair could not fix (DRE-8649).

`scripts/triage_unrepaired.py` reads the Triage lane once and each card's
whole thread, lists the cards whose newest pipeline-authored `🩹
triage-repair:` receipt is `unfinished` or `unrepairable` — or whose only
receipt is a `started` older than `TRIAGE_REPAIR_RUNNING_HOURS` — and settles
ONE console alert, `triage_repair:fleet:unrepaired`, the way the hold-deadline
alert settles (`hold_deadline.settle`): raise on a list, resolve on a complete
empty read, and call the door not at all on a short empty read.

Every scenario runs over `TriageRepairBoard` — the board the repair's own
tests use — with the real receipt bodies `triage_repair` writes, and the door
stubbed at `pipeline_notice._send`, so the client's own outcome words and its
own `pipeline-notice:` line are the real ones.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import hold  # noqa: E402
import hygiene_triage  # noqa: E402
import linear_ops  # noqa: E402
import notice_writers  # noqa: E402
import pipeline_act  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_reach  # noqa: E402
import triage_repair  # noqa: E402
import triage_unrepaired  # noqa: E402
from triage_repair_fakes import FLEET_USER, PERSON_USER, Comment, TriageRepairBoard  # noqa: E402

KEY = "triage_repair:fleet:unrepaired"
KIND = "pipeline_notice:triage_repair:fleet:unrepaired"
TITLE = "Triage cards the repair could not fix"
SHORT = "Some Triage cards could not be read on this run, so this list may be short."

#: 2026-11-01 14:00 PT (PST, the day DST ends).
NOW = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)
H = timedelta(hours=1)


def _at(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _receipt(body: str) -> str:
    """A receipt as `triage_repair._post` writes it: the body, then the act's
    trailer."""
    return pipeline_act.receipt("triage-repair", body)


def _place(board: TriageRepairBoard, ident: str, *, arrived: datetime,
           comments=(), title: str | None = None) -> None:
    """A card in Triage that arrived at `arrived`, carrying `comments` —
    `(body, posted)` or `(body, posted, user)` — oldest first."""
    card = board.add(ident, title=title or f"the card {ident}")
    card.history = [("Triage", _at(arrived))]
    card.comments = []
    for entry in comments:
        body, posted, user = (*entry, FLEET_USER)[:3]
        card.comments.append(Comment(body, _at(posted), user))


UNREPAIRABLE_WHY = "a red check the fix loop cannot fix holds it"
UNFINISHED_WHAT = "the planner wrote no revised card"
PARK_NOTE = ("🛑 Parked in Triage — the plan critic's bound: three rounds sent the "
             "card back.\n\nWhat the critic found so far:\n1. the files line is wrong")


def _stamp(reason: str, at: str | None, by: str) -> str:
    return hold.stamp_line(reason, at, by)


SHA = "a" * 40


def five_card_board() -> TriageRepairBoard:
    """The acceptance board: one `unrepairable`, one `unfinished`, one
    `rewritten`, one three-hour `started` and one one-hour `started`."""
    board = TriageRepairBoard()
    _place(board, "DRE-101", arrived=NOW - 30 * H, title="a card held on a red check",
           comments=[
               (_stamp("unfixable-check", SHA, ".github/workflows/agent-fix.yml"),
                NOW - 30 * H),
               (_receipt(triage_repair.started_body("the card", bound=False)),
                NOW - 29 * H),
               (_receipt(triage_repair.unrepairable_body(UNREPAIRABLE_WHY)),
                NOW - 29 * H + timedelta(minutes=1)),
           ])
    _place(board, "DRE-102", arrived=NOW - 20 * H, title="a card the planner left half-done",
           comments=[
               (PARK_NOTE, NOW - 20 * H),
               (_receipt(triage_repair.started_body("the card")), NOW - 19 * H),
               (_receipt(triage_repair.unfinished_body(UNFINISHED_WHAT, [])),
                NOW - 18 * H),
           ])
    _place(board, "DRE-103", arrived=NOW - 40 * H, title="a card rewritten and parked again",
           comments=[
               (_receipt(triage_repair.started_body("the card")), NOW - 50 * H),
               (_receipt(triage_repair.rewritten_body(["f"], "1. fixed")), NOW - 49 * H),
               (PARK_NOTE, NOW - 40 * H),
           ])
    _place(board, "DRE-104", arrived=NOW - 3 * H - timedelta(minutes=5),
           title="a card whose repair died",
           comments=[
               (PARK_NOTE, NOW - 3 * H - timedelta(minutes=5)),
               (_receipt(triage_repair.started_body("the card")), NOW - 3 * H),
           ])
    _place(board, "DRE-105", arrived=NOW - 1 * H - timedelta(minutes=5),
           title="a card being repaired now",
           comments=[
               (PARK_NOTE, NOW - 1 * H - timedelta(minutes=5)),
               (_receipt(triage_repair.started_body("the card")), NOW - 1 * H),
           ])
    return board


# ── the door and Linear ─────────────────────────────────────────────────────


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


@pytest.fixture
def filed(monkeypatch):
    """Every card the code under test tried to create or find: a fallback
    card would pass through one of these."""
    calls: list = []
    for name in ("create_card", "find_open", "find_open_prefix"):
        monkeypatch.setattr(linear_ops, name,
                            lambda *a, _n=name, **k: calls.append((_n, a, k)))
    return calls


def tagged(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("triage-unrepaired:")]


def closing(out: str) -> str:
    [line] = [ln for ln in tagged(out) if "card(s) listed" in ln]
    return line


def run(board: TriageRepairBoard, now: datetime = NOW) -> int:
    with board.patched():
        return triage_unrepaired.run(now=now)


# ── the contract ─────────────────────────────────────────────────────────────


class TestTheContract:
    def test_the_key_the_kind_the_title_and_the_tag(self):
        assert triage_unrepaired.NOTICE == KEY
        assert triage_unrepaired.KIND == KIND
        assert triage_unrepaired.TITLE == TITLE
        assert triage_unrepaired.SEVERITY == "WARN"
        assert triage_unrepaired.TAG == "triage-unrepaired:"
        assert triage_unrepaired.SHORT_READ_LINE == SHORT

    def test_the_title_carries_no_count_and_no_date(self):
        assert not re.search(r"\d", TITLE)

    def test_it_reads_the_receipt_opener_and_outcome_words_dre_8647_writes(self):
        assert triage_repair.RECEIPT_KEY == "🩹 triage-repair:"
        assert set(triage_unrepaired.LISTED_OUTCOMES) == {"unfinished", "unrepairable"}
        assert triage_unrepaired.RUNNING_HOURS == hygiene_triage.TRIAGE_REPAIR_RUNNING_HOURS


def dre_8606_card_rule(kind: str) -> str | None:
    """DRE-8606's card rule: the last `:`-separated segment of an alert's
    kind is its card when it matches `^DRE-\\d+$`."""
    last = kind.split(":")[-1]
    return last if re.fullmatch(r"DRE-\d+", last) else None


class TestItsKindNamesNoCard:
    def test_the_card_rule_reads_a_card_off_a_kind_that_names_one(self):
        assert dre_8606_card_rule("pipeline_notice:hold:fleet:DRE-12") == "DRE-12"

    def test_the_card_rule_reads_no_card_off_this_alert(self):
        assert dre_8606_card_rule(triage_unrepaired.KIND) is None
        assert dre_8606_card_rule(KIND) is None


# ── one run over the acceptance board ────────────────────────────────────────


class TestOneRun:
    def test_it_raises_the_one_key_at_warn_listing_three_oldest_first(
            self, door, filed, capsys):
        d = door("raised")
        board = five_card_board()
        assert run(board) == 0
        [payload] = d.payloads
        assert payload["action"] == "raise"
        assert payload["notice"] == KEY
        assert payload["severity"] == "WARN"
        assert payload["title"] == TITLE
        lines = payload["body"].splitlines()
        assert [ln.split(" — ", 1)[0] for ln in lines] == ["DRE-101", "DRE-102", "DRE-104"]
        assert filed == []

    def test_each_line_carries_its_four_facts(self, door, capsys):
        d = door("raised")
        run(five_card_board())
        first, second, third = d.payloads[0]["body"].splitlines()
        # the card's identifier and title
        assert first.startswith("DRE-101 — a card held on a red check — ")
        assert second.startswith("DRE-102 — a card the planner left half-done — ")
        assert third.startswith("DRE-104 — a card whose repair died — ")
        # why it was parked: the hold reason, else the park note's first line
        assert "parked: held unfixable-check" in first
        assert ("parked: 🛑 Parked in Triage — the plan critic's bound: three rounds "
                "sent the card back.") in second
        # what the repair tried: its newest receipt's first line
        assert f"repair: 🩹 triage-repair: unrepairable — {UNREPAIRABLE_WHY}" in first
        assert f"repair: 🩹 triage-repair: unfinished — {UNFINISHED_WHAT}." in second
        assert "repair: 🩹 triage-repair: started — this card was parked" in third
        # how long it has been in Triage
        assert first.endswith("in Triage 30 hours, since 2026-10-31 09:00 PT")
        assert second.endswith("in Triage 20 hours, since 2026-10-31 19:00 PT")
        assert third.endswith("in Triage 3 hours, since 2026-11-01 10:55 PT")

    def test_it_prints_one_line_per_card_then_the_closing_line(self, door, capsys):
        door("raised")
        run(five_card_board())
        out = capsys.readouterr().out
        assert tagged(out) == [
            "triage-unrepaired: DRE-101 unrepairable in Triage since 2026-10-31 09:00 PT",
            "triage-unrepaired: DRE-102 unfinished in Triage since 2026-10-31 19:00 PT",
            "triage-unrepaired: DRE-104 started in Triage since 2026-11-01 10:55 PT",
            "triage-unrepaired: 3 card(s) listed · complete=yes · outcome=raised",
        ]

    def test_the_clients_own_line_follows_the_closing_line(self, door, capsys):
        door("raised")
        run(five_card_board())
        lines = capsys.readouterr().out.splitlines()
        at = lines.index("triage-unrepaired: 3 card(s) listed · complete=yes · outcome=raised")
        assert lines[at + 1] == f"pipeline-notice: raised kind={KIND}"

    def test_it_reads_the_lane_once_and_every_thread_whole(self, door):
        door("raised")
        board = five_card_board()
        run(board)
        assert [r for r in board.reads if r[0] == "board"] == [("board", "Triage", False)]
        threads = [(ident, whole) for reader, ident, whole in board.reads
                   if reader == "comment_records"]
        assert sorted(threads) == [(f"DRE-10{n}", True) for n in range(1, 6)]

    def test_it_writes_nothing_to_linear(self, door):
        door("raised")
        board = five_card_board()
        run(board)
        assert board.events == []


class TestWhatIsListed:
    def _listed(self, board, door, now=NOW):
        d = door("raised")
        run(board, now)
        if not d.payloads or d.payloads[0]["action"] != "raise":
            return []
        return [ln.split(" — ", 1)[0] for ln in d.payloads[0]["body"].splitlines()]

    def test_a_started_receipt_turns_listed_at_the_running_bound(self, door):
        board = TriageRepairBoard()
        _place(board, "DRE-201", arrived=NOW - 5 * H, comments=[
            (_receipt(triage_repair.started_body("x")), NOW - 2 * H)])
        assert self._listed(board, door) == ["DRE-201"]
        assert self._listed(board, door, NOW - timedelta(minutes=1)) == []

    def test_a_receipt_from_anyone_else_counts_for_nothing(self, door):
        board = TriageRepairBoard()
        _place(board, "DRE-202", arrived=NOW - 5 * H, comments=[
            (_receipt(triage_repair.unrepairable_body("forged")), NOW - 4 * H, PERSON_USER)])
        assert self._listed(board, door) == []

    def test_the_newest_receipt_decides(self, door):
        board = TriageRepairBoard()
        _place(board, "DRE-203", arrived=NOW - 5 * H, comments=[
            (_receipt(triage_repair.started_body("x")), NOW - 4 * H),
            (_receipt(triage_repair.rewritten_body(["f"], "1. a")), NOW - 3 * H),
        ])
        _place(board, "DRE-204", arrived=NOW - 5 * H, comments=[
            (_receipt(triage_repair.started_body("x")), NOW - 4 * H),
            (_receipt(triage_repair.asked_body(False)), NOW - 3 * H),
        ])
        assert self._listed(board, door) == []

    def test_a_card_never_repaired_is_not_listed(self, door):
        board = TriageRepairBoard()
        _place(board, "DRE-205", arrived=NOW - 50 * H, comments=[(PARK_NOTE, NOW - 50 * H)])
        assert self._listed(board, door) == []

    def test_a_card_out_of_triage_is_not_read(self, door):
        board = five_card_board()
        board.move("DRE-101", "Planning")
        assert self._listed(board, door) == ["DRE-102", "DRE-104"]


# ── run after run ───────────────────────────────────────────────────────────


class TestRunAfterRun:
    def test_a_second_run_repeats_the_same_key_and_adds_a_new_card(self, door, capsys):
        d = door("raised", "repeat")
        board = five_card_board()
        run(board)
        capsys.readouterr()
        board.cards["DRE-105"].comments.append(Comment(
            _receipt(triage_repair.unrepairable_body("nothing a rewrite changes")),
            _at(NOW + 5 * H / 60), FLEET_USER))
        assert run(board, NOW + 15 * H / 60) == 0
        assert [p["notice"] for p in d.payloads] == [KEY, KEY]
        assert all(p["action"] == "raise" for p in d.payloads)
        second = [ln.split(" — ", 1)[0] for ln in d.payloads[1]["body"].splitlines()]
        assert second == ["DRE-101", "DRE-102", "DRE-104", "DRE-105"]
        out = capsys.readouterr().out
        assert ("triage-unrepaired: DRE-105 unrepairable in Triage since "
                "2026-11-01 12:55 PT") in tagged(out)
        assert closing(out) == ("triage-unrepaired: 4 card(s) listed · complete=yes · "
                                "outcome=repeat")
        assert f"pipeline-notice: repeat kind={KIND}" in out.splitlines()


class TestNothingListed:
    def _clean_board(self):
        board = TriageRepairBoard()
        _place(board, "DRE-301", arrived=NOW - 5 * H, comments=[(PARK_NOTE, NOW - 5 * H)])
        return board

    @pytest.mark.parametrize("answer", ["resolved", "not-open"])
    def test_a_complete_run_resolves_the_key(self, door, capsys, answer):
        d = door(answer)
        assert run(self._clean_board()) == 0
        assert d.payloads == [{"action": "resolve", "notice": KEY}]
        out = capsys.readouterr().out
        assert closing(out) == (f"triage-unrepaired: 0 card(s) listed · complete=yes · "
                                f"outcome={answer}")
        assert f"pipeline-notice: {answer} kind={KIND}" in out.splitlines()

    def test_an_empty_triage_lane_is_a_complete_read(self, door, capsys):
        d = door("not-open")
        run(TriageRepairBoard())
        assert d.payloads == [{"action": "resolve", "notice": KEY}]

    def test_an_incomplete_run_calls_no_door(self, door, capsys, monkeypatch):
        d = door()
        for name in ("raise_notice", "resolve_notice"):
            monkeypatch.setattr(pipeline_notice, name,
                                lambda *a, **k: pytest.fail("the door was called"))
        board = self._clean_board()
        _place(board, "DRE-302", arrived=NOW - 5 * H)
        _unreadable(board, "DRE-302")
        assert run(board) == 0
        assert d.payloads == []
        out = capsys.readouterr().out
        assert closing(out) == ("triage-unrepaired: 0 card(s) listed · complete=no · "
                                "outcome=unread")
        assert not [ln for ln in out.splitlines() if ln.startswith("pipeline-notice:")]

    def test_a_board_that_cannot_be_read_calls_no_door(self, door, capsys, monkeypatch):
        d = door()
        board = self._clean_board()

        def down(*a, **k):
            raise linear_ops.LinearError("Linear is down")
        monkeypatch.setattr(board, "gql_paged", down)
        assert run(board) == 1
        assert d.payloads == []
        assert closing(capsys.readouterr().out) == (
            "triage-unrepaired: 0 card(s) listed · complete=no · outcome=unread")


def _unreadable(board: TriageRepairBoard, ident: str) -> None:
    """`ident`'s thread read fails; every other card's reads as before."""
    real = board.comment_records

    def records(identifier, *, whole_thread=False):
        if identifier == ident:
            raise linear_ops.LinearError("the thread read timed out")
        return real(identifier, whole_thread=whole_thread)
    board.comment_records = records


class TestAShortRead:
    def test_a_short_read_that_lists_a_card_raises_ending_in_the_short_line(
            self, door, capsys):
        d = door("raised")
        board = five_card_board()
        _unreadable(board, "DRE-105")
        run(board)
        [payload] = d.payloads
        assert payload["action"] == "raise"
        lines = payload["body"].splitlines()
        assert lines[-1] == SHORT
        assert [ln.split(" — ", 1)[0] for ln in lines[:-1]] == ["DRE-101", "DRE-102", "DRE-104"]
        assert closing(capsys.readouterr().out) == (
            "triage-unrepaired: 3 card(s) listed · complete=no · outcome=raised")

    def test_a_complete_read_carries_no_short_line(self, door):
        d = door("raised")
        run(five_card_board())
        assert SHORT not in d.payloads[0]["body"]


# ── the door down ────────────────────────────────────────────────────────────


class TestTheDoorDown:
    def test_an_unreachable_door_is_lost_and_files_nothing(self, door, filed, capsys):
        door(pipeline_notice.DoorMissed("unreachable", "connection refused"))
        board = five_card_board()
        assert run(board) == 0
        assert filed == []
        assert board.events == []
        out = capsys.readouterr().out
        assert closing(out) == ("triage-unrepaired: 3 card(s) listed · complete=yes · "
                                "outcome=lost")
        assert f"pipeline-notice: lost unreachable kind={KIND}" in out.splitlines()

    def test_the_raise_is_handed_no_fallback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pipeline_notice, "raise_notice",
                            lambda key, **kw: calls.append((key, kw)) or "raised")
        run(five_card_board())
        [(key, kw)] = calls
        assert key == KEY
        assert kw.get("fallback") is None

    def test_a_refused_resolve_is_named(self, door, capsys):
        door(pipeline_notice.DoorMissed("http-503", "down"))
        board = TriageRepairBoard()
        run(board)
        assert closing(capsys.readouterr().out).endswith("outcome=resolve-refused")

    def test_a_client_that_throws_never_throws_out_of_the_run(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise RuntimeError("the client itself broke")
        monkeypatch.setattr(pipeline_notice, "raise_notice", boom)
        assert run(five_card_board()) == 0
        assert closing(capsys.readouterr().out).endswith("outcome=lost")


# ── the declarations ─────────────────────────────────────────────────────────


class TestTheDeclarations:
    def test_the_notice_writers_row(self):
        rows = {r["id"]: r for r in notice_writers.load()}
        assert rows["triage-unrepaired"] == {
            "id": "triage-unrepaired",
            "repo": "bureau-pipeline",
            "file": "scripts/triage_unrepaired.py",
            "title": TITLE,
            "disposition": "notice",
            "notice_key": KEY,
            "reason": ("a new notice (DRE-8609) — closed by the writer on the first "
                       "complete Triage Repair run that lists no card; it files no "
                       "fallback card"),
        }

    def test_notice_writers_check_passes(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "notice_writers.py"),
                               "check"], capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr

    def test_notice_writers_reads_it_as_repointed(self, capsys):
        notice_writers.report(None)
        assert "notice-writers: triage-unrepaired repointed" in capsys.readouterr().out

    def test_proof_reach_declares_the_log_tag(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
        [row] = [e for e in doc["log_lines"] if e["tag"] == "triage-unrepaired:"]
        assert row["printed_by"] == "scripts/triage_unrepaired.py"
        assert row["constant"] == "TAG"
        assert proof_reach._log_line_problems(ROOT, row) == []
