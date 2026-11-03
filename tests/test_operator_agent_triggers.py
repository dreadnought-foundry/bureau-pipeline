"""RED-first tests: the operator agent reads a card first when it just entered
Hand-work, or just got the hand-work-overdue alarm (DRE-9046, epic DRE-8738).

`scripts/operator_agent.py`'s `select` reads the cards waiting on a person in
an hourly ring, so a card that just arrived could wait three hours for its
turn. Two events now put a card first: the sweep's `🧹 Auto-promoted <queue>
→ Hand-work:` receipt, and the `🚨 hand-work-overdue:` alarm. Both are read
off the whole thread `select` already reads, so the triggers add no Linear
read of any kind.

The board here is a stub, as in `tests/test_operator_agent.py`. The fixture,
`tests/fixtures/operator-agent-triggers-2026-11-03.json`, holds two live cards
in the board read's shape, each cut after the comment it serves: DRE-8835
after its promotion receipt, DRE-8637 after its overdue alarm. Every other
card is synthetic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_agent_triggers.py -v
"""
from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import board_snapshot  # noqa: E402
import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import operator_agent  # noqa: E402
import operator_decide  # noqa: E402
import operator_rules  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "operator-agent-triggers-2026-11-03.json"
RECORD_KEYS = {"source", "copied", "cut_after", "withheld", "card"}
#: The fields `board_snapshot.CARD_QUERY` selects on a card.
CARD_KEYS = {"id", "identifier", "title", "description", "createdAt", "updatedAt", "state",
             "labels", "parent", "children", "comments", "relations", "inverseRelations",
             "history"}

#: The fixture's pass: 2026-11-03 11:00 UTC, ten minutes after DRE-8835's
#: promotion receipt and 1 h 55 min after DRE-8637's alarm.
HOUR = "2026-11-03T11:00:00Z"
NOW = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
ENTRY_CARD, ENTRY_AT = "DRE-8835", "2026-11-03T10:50:10.567Z"
ALARM_CARD, ALARM_AT = "DRE-8637", "2026-11-03T09:04:56.951Z"

PROMOTED = ("🧹 Auto-promoted Queued → Hand-work: routed **OPERATOR** — Not code — a deploy. "
            "operator, your turn in Hand-work — a person builds this; nothing was dispatched.")
ALARM = ("🚨 hand-work-overdue: {card} has waited in Hand-work 24 hours, past its 24 — the "
         "operator's to look at.")
ROUTED = "The operator makes the IAM change in the console."
HOST_QUOTE = "In this child the studio user can do everything but deploy."
#: A claim of the agent's over the card as it once read: it names no
#: fingerprint, so the card is `decide` again, as with no comment at all.
CLAIM = "🛠️ operator-agent: do-claim — a claim over the card as it once read · 02:40 PT"


# --------------------------------------------------------------------------- #
# the stub board                                                               #
# --------------------------------------------------------------------------- #


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ago(minutes: float) -> str:
    return _iso(NOW - timedelta(minutes=minutes))


def _record(body, pipeline=True, at=None):
    return {"body": body, "authored_by_pipeline": pipeline, "created_at": at or _ago(30)}


def _board_card(ident, lane="Hand-work", title=None, labels=("repo:agent-bureau",),
                description="A step a person takes.", blocked_by=()):
    return {
        "id": f"id-{ident}", "identifier": ident,
        "title": title if title is not None else f"agent-bureau: synthetic card {ident}",
        "description": description, "state": {"name": lane},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"type": "blocks", "issue": {"identifier": b, "state": {"name": "Queued"}}}
            for b in blocked_by]},
    }


class Board:
    """Cards and their threads, with every read counted. The lane history and
    the viewer read raise: the triggers make neither."""

    def __init__(self):
        self.cards: list[dict] = []
        self.threads: dict[str, list] = {}
        self.thread_reads: list[str] = []

    def add(self, ident, thread=(), **card):
        self.cards.append(_board_card(ident, **card))
        self.threads[ident] = list(thread)
        return self.cards[-1]

    def add_fixture(self):
        for record in _fixture():
            card = record["card"]
            self.cards.append(card)
            self.threads[card["identifier"]] = [
                {"body": n["body"], "created_at": n["createdAt"],
                 "authored_by_pipeline": (n.get("user") or {}).get("id") == "user-pipeline"}
                for n in linear_ops.window_nodes(card["comments"])]
        return self

    def install(self, monkeypatch):
        def read_board(lanes):
            return [c for c in self.cards if c["state"]["name"] in lanes], 1

        def comment_records(ident, *, whole_thread=False):
            assert whole_thread
            self.thread_reads.append(ident)
            return list(self.threads.get(ident, []))

        def refused(name):
            def read(*args, **kwargs):
                raise AssertionError(f"select made a {name} read")
            return read

        monkeypatch.setattr(board_snapshot, "read_board", read_board)
        monkeypatch.setattr(linear_ops, "comment_records", comment_records)
        monkeypatch.setattr(linear_ops, "lane_history", refused("lane-history"))
        monkeypatch.setattr(linear_ops, "viewer_id", refused("viewer"))
        monkeypatch.setattr(linear_ops, "gql", refused("raw Linear"))
        return self


def _fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _select(tmp_path, limit=None, hour=HOUR):
    out = tmp_path / "cards.json"
    argv = ["select", "--out", str(out), "--cards-dir", str(tmp_path / "cards"),
            "--hour", hour]
    if limit is not None:
        argv += ["--limit", str(limit)]
    assert operator_agent.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _decide(tmp_path):
    out = tmp_path / "decisions.json"
    answers = tmp_path / "answers"
    answers.mkdir(exist_ok=True)
    argv = ["decide", "--cards", str(tmp_path / "cards.json"),
            "--cards-dir", str(tmp_path / "cards"), "--answers", str(answers),
            "--out", str(out)]
    code = operator_agent.main(argv)
    return code, json.loads(out.read_text(encoding="utf-8"))


def _by_card(rows) -> dict:
    return {row["card"]: row for row in rows}


def _marked(rows) -> set:
    return {row["card"] for row in rows if row["for_model"]}


def _tag_lines(text) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(operator_decide.LOG_TAG)]


def _decision(ident, outcome, fingerprint):
    extra = {"studio": {"host_cannot": [HOST_QUOTE]}}
    fields = dict(card=ident, repo="", lane="Hand-work", outcome=outcome,
                  reason="the model read the card", step="do the step the card names",
                  act="", deploy=False, evidence=[], host_cannot=[], finding="",
                  question="", recommendation="", choices=None, held="",
                  fingerprint=fingerprint)
    fields.update(extra.get(outcome, {}))
    return operator_decide.Decision(**fields)


def _studio_receipt(board_card, at):
    """A pipeline receipt of the agent's, `studio`, over the card as it stands."""
    decision = _decision(board_card["identifier"], "studio",
                         operator_decide.fingerprint(board_card))
    return _record(operator_decide.receipt_text(decision, NOW), at=at)


def _crowded(board, routed=2, ring=4):
    """Routed cards that take the slots first, and ring cards around them, all
    numbered below the fixture's cards."""
    for n in range(routed):
        board.add(f"DRE-{100 + n}", description=ROUTED)
    for n in range(ring):
        board.add(f"DRE-{200 + n}")
    return board


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contracts_names_and_hours():
    assert operator_agent.TRIGGER_HOURS == 2
    assert operator_agent.TRIGGER_ENTRY == "hand-work entry"
    assert operator_agent.TRIGGER_ALARM == "hand-work-overdue alarm"


def test_every_row_carries_a_trigger_key(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    board.add("DRE-9001")
    board.add("DRE-9002", thread=[_record(PROMOTED, at=_ago(20))])
    rows = _by_card(_select(tmp_path))
    assert rows["DRE-9001"]["trigger"] is None
    assert rows["DRE-9002"]["trigger"] == {
        "kind": "hand-work entry", "at": (NOW - timedelta(minutes=20)).isoformat()}


# --------------------------------------------------------------------------- #
# the fixture's two cards are read first                                       #
# --------------------------------------------------------------------------- #


def test_both_fixture_cards_are_read_first_where_the_ring_would_not_reach_them(
        tmp_path, monkeypatch, capsys):
    board = _crowded(Board().add_fixture()).install(monkeypatch)
    rows = _select(tmp_path, limit=2)
    by_card = _by_card(rows)

    # Without the triggers the routed cards take both slots, and the ring none.
    plain = [dict(row, trigger=None, action="decide") if row["action"] == "unread" else
             dict(row, trigger=None) for row in rows]
    operator_agent.mark(plain, 2, NOW)
    assert _marked(plain) == {"DRE-100", "DRE-101"}

    assert _marked(rows) == {ENTRY_CARD, ALARM_CARD}
    for ident in (ENTRY_CARD, ALARM_CARD):
        assert by_card[ident]["action"] == "decide" and by_card[ident]["for_model"] is True
    assert by_card[ENTRY_CARD]["trigger"]["kind"] == "hand-work entry"
    assert by_card[ALARM_CARD]["trigger"]["kind"] == "hand-work-overdue alarm"
    for ident in ("DRE-100", "DRE-101", "DRE-200"):
        assert by_card[ident]["action"] == "unread" and by_card[ident]["trigger"] is None

    capsys.readouterr()
    code, _decisions = _decide(tmp_path)
    assert code == 0
    lines = _tag_lines(capsys.readouterr().out)
    entry = "operator-agent: DRE-8835 triggered — hand-work entry at 2026-11-03 02:50 PT · decide"
    alarm = ("operator-agent: DRE-8637 triggered — hand-work-overdue alarm at "
             "2026-11-03 01:04 PT · decide")
    assert entry in lines and alarm in lines
    assert entry.endswith(dead_run.pacific(
        datetime.fromisoformat(ENTRY_AT.replace("Z", "+00:00"))) + " · decide")
    # Each trigger line follows its card's own line.
    for ident, line in ((ENTRY_CARD, entry), (ALARM_CARD, alarm)):
        at = lines.index(line)
        assert lines[at - 1].startswith(f"operator-agent: {ident} ")
        assert "triggered" not in lines[at - 1]
    assert sum("triggered" in line for line in lines) == 2


def test_the_fixture_alarm_card_names_its_alarm_not_its_day_old_entry(tmp_path, monkeypatch):
    Board().add_fixture().install(monkeypatch)
    row = _by_card(_select(tmp_path))[ALARM_CARD]
    thread = [n["body"] for n in _fixture()[1]["card"]["comments"]["nodes"]]
    assert any(body.startswith("🧹 Auto-promoted Queued → Hand-work:") for body in thread)
    assert row["trigger"] == {"kind": "hand-work-overdue alarm",
                              "at": datetime.fromisoformat(
                                  ALARM_AT.replace("Z", "+00:00")).isoformat()}


def test_both_triggers_pending_name_the_newer(tmp_path, monkeypatch, capsys):
    board = Board().install(monkeypatch)
    board.add("DRE-9001", thread=[_record(PROMOTED, at=_ago(50)),
                                  _record(ALARM.format(card="DRE-9001"), at=_ago(10))])
    board.add("DRE-9002", thread=[_record(ALARM.format(card="DRE-9002"), at=_ago(50)),
                                  _record(PROMOTED, at=_ago(10))])
    rows = _by_card(_select(tmp_path))
    assert rows["DRE-9001"]["trigger"]["kind"] == "hand-work-overdue alarm"
    assert rows["DRE-9002"]["trigger"]["kind"] == "hand-work entry"
    capsys.readouterr()
    _decide(tmp_path)
    lines = [line for line in _tag_lines(capsys.readouterr().out) if "triggered" in line]
    assert lines == [
        f"operator-agent: DRE-9001 triggered — hand-work-overdue alarm at "
        f"{dead_run.pacific(NOW - timedelta(minutes=10))} · decide",
        f"operator-agent: DRE-9002 triggered — hand-work entry at "
        f"{dead_run.pacific(NOW - timedelta(minutes=10))} · decide",
    ]


def test_the_other_queue_names_promotion_receipt_is_an_entry(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    for n, name in enumerate(operator_agent.lane_contract.QUEUE_LANES):
        board.add(f"DRE-900{n}", thread=[_record(
            f"🧹 Auto-promoted {name} → Hand-work: routed **OPERATOR**", at=_ago(15))])
    rows = _select(tmp_path)
    assert len(rows) == len(operator_agent.lane_contract.QUEUE_LANES) >= 1
    for row in rows:
        assert row["trigger"]["kind"] == "hand-work entry", row["card"]


# --------------------------------------------------------------------------- #
# not pending: the ring reads the card exactly as before                       #
# --------------------------------------------------------------------------- #


def _ring_board(monkeypatch, thread) -> Board:
    board = _crowded(Board(), routed=0, ring=6).install(monkeypatch)
    board.add("DRE-9001", thread=thread)
    return board


@pytest.mark.parametrize("hour", ["2026-11-03T11:00:00Z", "2026-11-03T11:30:00Z",
                                  "2026-11-03T12:00:00Z"])
def test_a_trigger_older_than_the_agents_own_comment_is_not_pending(tmp_path, monkeypatch,
                                                                    hour):
    _ring_board(monkeypatch, [])
    before = _select(tmp_path, limit=2, hour=hour)
    triggers = [_record(PROMOTED, at=_ago(40)),
                _record(ALARM.format(card="DRE-9001"), at=_ago(30))]

    _ring_board(monkeypatch, [_record(CLAIM, at=_ago(50))] + triggers)
    row = _by_card(_select(tmp_path, limit=2, hour=hour))["DRE-9001"]
    assert row["trigger"]["kind"] == "hand-work-overdue alarm" and row["for_model"] is True

    _ring_board(monkeypatch, triggers + [_record(CLAIM, at=_ago(20))])
    rows = _select(tmp_path, limit=2, hour=hour)
    assert _by_card(rows)["DRE-9001"]["trigger"] is None
    assert [(r["card"], r["action"], r["for_model"]) for r in rows] == [
        (r["card"], r["action"], r["for_model"]) for r in before]


@pytest.mark.parametrize("hour", ["2026-11-03T11:00:00Z", "2026-11-03T12:00:00Z",
                                  "2026-11-03T13:00:00Z"])
def test_a_trigger_more_than_two_hours_old_is_left_to_the_ring(tmp_path, monkeypatch, hour):
    _ring_board(monkeypatch, [])
    before = _select(tmp_path, limit=2, hour=hour)
    hour_at = datetime.fromisoformat(hour.replace("Z", "+00:00"))
    stale = _iso(hour_at - timedelta(hours=operator_agent.TRIGGER_HOURS, minutes=1))
    _ring_board(monkeypatch, [_record(PROMOTED, at=stale),
                              _record(ALARM.format(card="DRE-9001"), at=stale)])
    rows = _select(tmp_path, limit=2, hour=hour)
    assert _by_card(rows)["DRE-9001"]["trigger"] is None
    assert [(r["card"], r["action"], r["for_model"]) for r in rows] == [
        (r["card"], r["action"], r["for_model"]) for r in before]


def test_a_trigger_exactly_two_hours_old_is_still_pending(tmp_path, monkeypatch):
    edge = _iso(NOW - timedelta(hours=operator_agent.TRIGGER_HOURS))
    _ring_board(monkeypatch, [_record(PROMOTED, at=edge)])
    row = _by_card(_select(tmp_path, limit=2))["DRE-9001"]
    assert row["trigger"]["kind"] == "hand-work entry" and row["for_model"] is True


def test_a_comment_a_person_wrote_is_no_trigger_and_cancels_none(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    board.add("DRE-9001", thread=[_record(PROMOTED, pipeline=False, at=_ago(10)),
                                  _record(ALARM.format(card="DRE-9001"), pipeline=False,
                                          at=_ago(10))])
    board.add("DRE-9002", thread=[_record(PROMOTED, at=_ago(30)),
                                  _record(CLAIM, pipeline=False, at=_ago(10))])
    rows = _by_card(_select(tmp_path))
    assert rows["DRE-9001"]["trigger"] is None
    assert rows["DRE-9002"]["trigger"]["kind"] == "hand-work entry"


def test_a_card_outside_hand_work_is_never_triggered(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    board.add("DRE-9001", lane="Queued", labels=("repo:agent-bureau", "operator-step"),
              thread=[_record(PROMOTED, at=_ago(10)),
                      _record(ALARM.format(card="DRE-9001"), at=_ago(5))])
    row = _select(tmp_path)[0]
    assert row["lane"] == "Queued" and row["trigger"] is None


# --------------------------------------------------------------------------- #
# a standing receipt keeps its action                                          #
# --------------------------------------------------------------------------- #


def _spent_board(monkeypatch, alarmed: bool) -> Board:
    board = Board().install(monkeypatch)
    card = board.add("DRE-9001", blocked_by=(operator_rules.load_rules()["studio"]["blocker"],))
    thread = [_studio_receipt(card, at=_ago(60))]
    if alarmed:
        thread.append(_record(ALARM.format(card="DRE-9001"), at=_ago(15)))
    board.threads["DRE-9001"] = thread
    return board


def test_a_triggered_spent_card_keeps_spent_and_decide_writes_nothing_new(
        tmp_path, monkeypatch, capsys):
    _spent_board(monkeypatch, alarmed=False)
    _select(tmp_path)
    _code, untriggered = _decide(tmp_path)

    _spent_board(monkeypatch, alarmed=True)
    row = _select(tmp_path)[0]
    assert row["trigger"]["kind"] == "hand-work-overdue alarm"
    assert row["action"] == "spent" and row["for_model"] is False
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0
    assert decisions == untriggered
    assert [d["action"] for d in decisions] == ["spent"]
    lines = _tag_lines(capsys.readouterr().out)
    assert lines[0].startswith("operator-agent: DRE-9001 → studio — ")
    assert lines[1] == (f"operator-agent: DRE-9001 triggered — hand-work-overdue alarm at "
                        f"{dead_run.pacific(NOW - timedelta(minutes=15))} · spent")
    assert len(lines) == 2


# --------------------------------------------------------------------------- #
# more triggers than the limit                                                 #
# --------------------------------------------------------------------------- #


def test_more_pending_triggers_than_the_limit_read_the_oldest_first(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    ages = {"DRE-9001": 20, "DRE-9002": 90, "DRE-9003": 50, "DRE-9004": 70}
    for ident, minutes in ages.items():
        board.add(ident, thread=[_record(PROMOTED, at=_ago(minutes))])
    board.add("DRE-100", description=ROUTED)
    rows = _by_card(_select(tmp_path, limit=2))
    assert _marked(rows.values()) == {"DRE-9002", "DRE-9004"}
    for ident in ("DRE-9001", "DRE-9003", "DRE-100"):
        assert rows[ident]["action"] == "unread" and rows[ident]["for_model"] is False
    for ident in ages:
        assert rows[ident]["trigger"]["kind"] == "hand-work entry"

    # The next pass, an hour on: the two read got the agent's receipt, and the
    # other two are still pending, and read now.
    for ident in ("DRE-9002", "DRE-9004"):
        card = next(c for c in board.cards if c["identifier"] == ident)
        board.threads[ident].append(_studio_receipt(card, at=_ago(-5)))
    nxt = _by_card(_select(tmp_path, limit=2, hour="2026-11-03T12:00:00Z"))
    assert nxt["DRE-9001"]["trigger"] is not None and nxt["DRE-9003"]["trigger"] is not None
    assert _marked(nxt.values()) == {"DRE-9001", "DRE-9003"}
    assert nxt["DRE-9002"]["trigger"] is None and nxt["DRE-9004"]["trigger"] is None


# --------------------------------------------------------------------------- #
# the triggers make no Linear read                                             #
# --------------------------------------------------------------------------- #


def test_the_triggers_make_no_read_and_one_thread_read_per_card(tmp_path, monkeypatch):
    board = _crowded(Board().add_fixture(), routed=1, ring=2).install(monkeypatch)
    board.add("DRE-9001", thread=[_record("A person's note, no receipt of any kind.",
                                          pipeline=False, at=_ago(5))])
    rows = _by_card(_select(tmp_path, limit=3))
    assert rows["DRE-9001"]["lane"] == "Hand-work" and rows["DRE-9001"]["trigger"] is None
    assert sorted(board.thread_reads) == sorted(c["identifier"] for c in board.cards)
    assert len(board.thread_reads) == len(set(board.thread_reads)) == len(rows)


def test_the_module_reads_no_lane_moves_and_no_viewer():
    source = Path(operator_agent.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]
    assert "lane_moves" not in code and "viewer_id" not in code


def test_the_docstring_names_the_entries_the_trigger_covers_and_the_read_it_does_not_make():
    doc = operator_agent.__doc__
    assert "routing_verdict.lane_moves" in doc
    assert "🧹 Auto-promoted" in doc and "→ Hand-work:" in doc
    assert "🚨 hand-work-overdue:" in doc
    assert "hourly ring" in doc
    assert "TRIGGER_HOURS" in doc
    flat = " ".join(doc.split()).lower()
    assert "the triggers add no read" in flat


# --------------------------------------------------------------------------- #
# the fixture is excerpts, never copies (DRE-3918)                             #
# --------------------------------------------------------------------------- #


def test_the_fixture_holds_the_two_cards_in_the_board_reads_shape():
    records = _fixture()
    assert [r["card"]["identifier"] for r in records] == [ENTRY_CARD, ALARM_CARD]
    for record in records:
        assert set(record) == RECORD_KEYS
        card = record["card"]
        assert set(card) == CARD_KEYS
        assert card["state"]["name"] == "Hand-work"
        assert record["source"] == ("https://linear.app/dreadnoughtfoundry/issue/"
                                    + card["identifier"])
        assert record["copied"] == "2026-11-03"
        assert record["withheld"] and all(isinstance(w, str) for w in record["withheld"])
        newest = linear_ops.window_nodes(card["comments"])[-1]
        assert record["cut_after"] == {"created_at": newest["createdAt"],
                                       "opens": record["cut_after"]["opens"]}
        assert newest["body"].startswith(record["cut_after"]["opens"])
        for node in card["comments"]["nodes"]:
            assert node["user"]["id"] in ("user-pipeline", "user-person")
    assert records[0]["cut_after"]["created_at"] == ENTRY_AT
    assert records[1]["cut_after"]["created_at"] == ALARM_AT


def test_the_fixture_names_no_address_link_or_account_number():
    text = FIXTURE.read_text(encoding="utf-8")
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    assert emails <= {"person@example.com"}
    for url in re.findall(r"https?://[^\s)\"\]>]+", text):
        assert url.startswith(("https://github.com/dreadnought-foundry/",
                               "https://linear.app/dreadnoughtfoundry/")), url
    assert not re.search(r"\d{12}", text)


def test_the_fixture_names_the_ceo_and_no_one_else():
    text = FIXTURE.read_text(encoding="utf-8")
    assert not re.search(r"\bSid\b|Conklin", text)
