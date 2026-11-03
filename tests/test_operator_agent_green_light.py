"""RED-first tests: the operator agent's `select` reads Green Light and hands
each row it may take to the model as a `green-light` row (DRE-9050, epic
DRE-8738).

`scripts/operator_agent.py`'s `select` read the cards waiting on a person in
`Intake`, `Queued` and `Hand-work`. It now reads `Green Light` in the same
board read. Each `Green Light` card is read by `operator_green_light.reading`
(DRE-9048): a row it does not take prints one `left in Green Light` line and
gets no row, and a row it takes is `group: "green-light"`, with at most
`GREEN_LIGHT_SLOTS` of the model's slots, taken as an hourly ring of its own.
`decide` passes that group to `operator_decide.decide` (DRE-9047).

This card copies no live thread. Every row is built from a sibling's fixture,
read by its record id: DRE-9046's
`tests/fixtures/operator-agent-triggers-2026-11-03.json` (the `Hand-work`
card), DRE-9047's `tests/fixtures/operator-decide-green-light-cards.json` and
DRE-9048's `tests/fixtures/operator-green-light-cards.json`. Three of the
Green Light records are built on the same live card, so the board here gives
each its own card number; the reading of a row does not depend on it. Every
other card is synthetic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_operator_agent_green_light.py -v
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import board_snapshot  # noqa: E402
import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import groom_verify_agent  # noqa: E402
import linear_ops  # noqa: E402
import operator_agent  # noqa: E402
import operator_decide  # noqa: E402
import operator_green_light  # noqa: E402
import operator_rules  # noqa: E402
import pipeline_act  # noqa: E402
import spoken_thread  # noqa: E402

TRIGGERS = ROOT / "tests" / "fixtures" / "operator-agent-triggers-2026-11-03.json"
DECIDE = ROOT / "tests" / "fixtures" / "operator-decide-green-light-cards.json"
GREEN = ROOT / "tests" / "fixtures" / "operator-green-light-cards.json"
REPLAY = ROOT / "tests" / "fixtures" / "operator-agent-board-2026-11-01.json"

PIPELINE_USER = "user-pipeline"
GL, WAITING = "green-light", "waiting"
LANE = "Green Light"

#: DRE-9046's pass: 2026-11-03 11:00 UTC, ten minutes after DRE-8835's
#: promotion receipt, so its `Hand-work` card is triggered.
HOUR = "2026-11-03T11:00:00Z"
NEXT_HOUR = "2026-11-03T12:00:00Z"
NOW = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
ENTRY_CARD = "DRE-8835"

#: gl-stopped-run is DRE-2498, and keeps its number. r1-ceo and r3-growth are
#: built on the same card, so each gets one of its own here.
STOPPED_CARD = "DRE-2498"
NUMBERS = {"gl-stopped-run": STOPPED_CARD, "r1-ceo": "DRE-92001", "r3-growth": "DRE-92002"}

#: DRE-9047's two quotes from gl-stopped-run's own thread: what stopped.
STOPPED = "the critic produced no result"
STOPPED_PARK = "This card was about to go to the build queue"
SANDBOX_TITLE = "SANDBOX FIXTURE (proof DRE-9054): the operator agent reads a stopped run"


@pytest.fixture(autouse=True)
def _console_key(monkeypatch):
    """The console's key is the TEST key, never fetched, so no reading reaches
    the network for it — as in `tests/test_operator_green_light.py`."""
    monkeypatch.setattr(spoken_thread, "_VERIFIER", console_receipt.Verifier(
        key_loader=lambda: console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)))


# --------------------------------------------------------------------------- #
# the fixtures, read by record id                                              #
# --------------------------------------------------------------------------- #


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _merge(card: dict, delta: dict) -> dict:
    """`delta` over `card`, as DRE-9048's test builds a record: a title
    replaces the base's, labels are added, and a comment replaces the base's
    comment of the same `createdAt` or is added beside them, newest first."""
    card = copy.deepcopy(card)
    if "title" in delta:
        card["title"] = delta["title"]
    for label in (delta.get("labels") or {}).get("nodes") or []:
        card["labels"]["nodes"].append(copy.deepcopy(label))
    added = (delta.get("comments") or {}).get("nodes") or []
    if added:
        at = {node["createdAt"]: node for node in card["comments"]["nodes"]}
        at.update({node["createdAt"]: copy.deepcopy(node) for node in added})
        card["comments"]["nodes"] = sorted(at.values(), key=lambda n: n["createdAt"],
                                           reverse=True)
    return card


def green(record_id: str) -> dict:
    """A Green Light record's board card: a DRE-9047 record as it is, or a
    DRE-9048 record built over its base."""
    decided = _json(DECIDE)
    if record_id in decided:
        return copy.deepcopy(decided[record_id]["card"])
    record = _json(GREEN)[record_id]
    return _merge(green(record["base"]["record"]), record["card"])


def entry_card() -> dict:
    """DRE-9046's `Hand-work` card, cut after its promotion receipt."""
    return copy.deepcopy(next(r["card"] for r in _json(TRIGGERS)
                              if r["card"]["identifier"] == ENTRY_CARD))


def thread(card: dict) -> list:
    """The card's comments as `linear_ops.comment_records(whole_thread=True)`
    rows, oldest to newest, read with the pipeline's own key."""
    return [{"body": n.get("body") or "", "created_at": n.get("createdAt"),
             "authored_by_pipeline": (n.get("user") or {}).get("id") == PIPELINE_USER}
            for n in linear_ops.window_nodes(card.get("comments"))]


def numbered(card: dict, ident: str) -> dict:
    card = copy.deepcopy(card)
    card["identifier"], card["id"] = ident, f"id-{ident}"
    return card


def pushed(card: dict, body: str, at: str, author: str | None = PIPELINE_USER) -> dict:
    card = copy.deepcopy(card)
    card["comments"]["nodes"].insert(0, {"body": body, "createdAt": at,
                                         "user": {"id": author} if author else None})
    return card


def _synthetic(ident, lane="Hand-work", title=None, labels=("repo:agent-bureau",),
               description="A step a person takes.", comments=()):
    return {
        "id": f"id-{ident}", "identifier": ident,
        "title": title if title is not None else f"agent-bureau: synthetic card {ident}",
        "description": description, "state": {"name": lane},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "inverseRelations": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False},
                     "nodes": list(reversed(list(comments)))},
    }


def stopped_run(ident: str) -> dict:
    """A synthetic `Green Light` row `reading` takes: a run that stopped."""
    return _synthetic(ident, lane=LANE, title=f"agent-bureau: a run that stopped ({ident})",
                      description="The build run stopped before it opened a pull request.")


# --------------------------------------------------------------------------- #
# the stub board                                                               #
# --------------------------------------------------------------------------- #


class Board:
    """Board cards, their threads off their own comments, and every read
    counted. `viewer` is what `linear_ops.viewer_id()` answers."""

    def __init__(self, viewer: str | None = PIPELINE_USER):
        self.cards: list[dict] = []
        self.viewer = viewer
        self.board_reads: list[tuple] = []
        self.thread_reads: list[str] = []
        self.history_reads: list[str] = []
        self.viewer_reads = 0
        self.histories: dict[str, list] = {}

    def add(self, card: dict) -> dict:
        self.cards.append(card)
        return card

    def install(self, monkeypatch, *, threads: bool = True):
        def read_board(lanes):
            self.board_reads.append(tuple(lanes))
            return [c for c in self.cards if c["state"]["name"] in lanes], 1

        def comment_records(ident, *, whole_thread=False):
            assert whole_thread
            self.thread_reads.append(ident)
            return thread(next(c for c in self.cards if c["identifier"] == ident))

        def lane_history(ident, first=50):
            self.history_reads.append(ident)
            return list(self.histories.get(ident, []))

        def viewer_id():
            self.viewer_reads += 1
            return self.viewer

        monkeypatch.setattr(board_snapshot, "read_board", read_board)
        if threads:
            monkeypatch.setattr(linear_ops, "comment_records", comment_records)
            monkeypatch.setattr(linear_ops, "viewer_id", viewer_id)
        monkeypatch.setattr(linear_ops, "lane_history", lane_history)
        return self


def _select(tmp_path, limit=None, hour=HOUR):
    out = tmp_path / "cards.json"
    argv = ["select", "--out", str(out), "--cards-dir", str(tmp_path / "cards"),
            "--hour", hour]
    if limit is not None:
        argv += ["--limit", str(limit)]
    assert operator_agent.main(argv) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _answers(tmp_path, *answers) -> Path:
    answers_dir = tmp_path / "answers"
    answers_dir.mkdir(exist_ok=True)
    for answer in answers:
        (answers_dir / f"{answer['card']}.json").write_text(json.dumps(answer),
                                                             encoding="utf-8")
    return answers_dir


def _decide(tmp_path, rows=None):
    cards = tmp_path / "cards.json"
    if rows is not None:
        cards.write_text(json.dumps(rows), encoding="utf-8")
    out = tmp_path / "decisions.json"
    answers = _answers(tmp_path)
    argv = ["decide", "--cards", str(cards), "--cards-dir", str(tmp_path / "cards"),
            "--answers", str(answers), "--out", str(out)]
    code = operator_agent.main(argv)
    return code, json.loads(out.read_text(encoding="utf-8"))


def _by_card(rows) -> dict:
    return {row["card"]: row for row in rows}


def _marked(rows, group=None) -> set:
    return {r["card"] for r in rows if r["for_model"] and (group is None or r["group"] == group)}


def _tag_lines(text) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(operator_decide.LOG_TAG)]


def _left_line(ident: str, why: str) -> str:
    return f"operator-agent: {ident} left in Green Light — {why}"


def _answer(card: str, outcome: str, step: str = "re-run the review the critic never finished",
            **extra) -> dict:
    answer = {"card": card, "outcome": outcome, "step": step,
              "reason": "the model read the row", "evidence": [], "answer": ""}
    answer.update(extra)
    return answer


def _repair(card: str = STOPPED_CARD) -> dict:
    return _answer(card, "repair", evidence=[STOPPED, STOPPED_PARK])


def _posted(decision: operator_decide.Decision) -> str:
    """The receipt the claim posts for `decision`, with the act registry's
    trailer, as DRE-9048's fixture receipts are written."""
    return pipeline_act.receipt("operator-agent", operator_decide.receipt_text(decision, NOW))


def _row_of(card: dict) -> dict:
    labels = [n["name"] for n in card["labels"]["nodes"]]
    return {"card": card["identifier"], "lane": card["state"]["name"],
            "title": card["title"], "description": card["description"], "labels": labels,
            "repo": operator_agent.repo_of(card, operator_agent._repo_map())}


def _moving_receipt(card: dict, outcome: str) -> str:
    """A `repair` or `answer` receipt over the card as it stands now."""
    text = "\n\n".join([card["title"], card["description"]]
                       + [r["body"] for r in thread(card)])
    answer = _repair(card["identifier"]) if outcome == "repair" else _answer(
        card["identifier"], "answer", step="Is the critic's no-result a crash?",
        evidence=[STOPPED], answer="Yes: a no-result is a crash, not a rejection.")
    decision = operator_decide.decide(_row_of(card), answer, text, group=GL)
    assert decision is not None and decision.outcome == outcome
    return _posted(decision)


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


def test_the_contracts_names():
    assert operator_agent.GREEN_LIGHT_SLOTS == 5
    assert operator_agent.SANDBOX_OPENER == "SANDBOX FIXTURE (proof DRE-"
    assert operator_agent.GREEN_LIGHT == LANE


def test_the_sandbox_opener_is_the_groomers():
    assert operator_agent.SANDBOX_OPENER == groom_verify_agent.SANDBOX_FIXTURE_TITLE_OPENER


def test_select_does_not_import_the_groomer():
    source = Path(operator_agent.__file__).read_text(encoding="utf-8")
    assert "groom_verify_agent" not in source.split('"""', 2)[2]


def test_the_board_read_is_one_read_of_the_three_lanes_and_green_light(tmp_path, monkeypatch):
    board = Board().install(monkeypatch)
    board.add(_synthetic("DRE-9001"))
    board.add(stopped_run("DRE-9301"))
    _select(tmp_path)
    assert board.board_reads == [("Intake", "Queued", "Hand-work", "Green Light")]


# --------------------------------------------------------------------------- #
# which Green Light rows                                                       #
# --------------------------------------------------------------------------- #


def _fixture_board(monkeypatch) -> Board:
    board = Board()
    board.add(entry_card())
    board.add(green("r2-plan"))
    board.add(numbered(green("r3-growth"), NUMBERS["r3-growth"]))
    board.add(numbered(green("gl-stopped-run"), NUMBERS["gl-stopped-run"]))
    board.add(numbered(green("r1-ceo"), NUMBERS["r1-ceo"]))
    return board.install(monkeypatch)


def test_over_the_sibling_fixtures_only_the_stopped_run_is_taken(tmp_path, monkeypatch, capsys):
    _fixture_board(monkeypatch)
    rows = _by_card(_select(tmp_path))
    out = capsys.readouterr().out
    assert set(rows) == {ENTRY_CARD, STOPPED_CARD}
    row = rows[STOPPED_CARD]
    assert (row["group"], row["action"], row["for_model"], row["lane"]) == (
        GL, "decide", True, LANE)
    assert rows[ENTRY_CARD]["group"] == WAITING and rows[ENTRY_CARD]["for_model"] is True
    left = [line for line in _tag_lines(out) if " left in Green Light — " in line]
    assert sorted(left) == sorted([
        _left_line("DRE-6589", operator_green_light.PLAN_WHY),
        _left_line(NUMBERS["r3-growth"], operator_green_light.GROWTH_WHY),
        _left_line(NUMBERS["r1-ceo"], operator_green_light.SENT_WHY),
    ])
    files = {p.stem for p in (tmp_path / "cards").glob("*.md")}
    assert files == {ENTRY_CARD, STOPPED_CARD}


def test_a_taken_row_carries_the_contracts_keys_and_its_group(tmp_path, monkeypatch):
    _fixture_board(monkeypatch)
    rows = _select(tmp_path)
    for row in rows:
        assert set(row) == {"card", "repo", "lane", "title", "labels", "description",
                            "fingerprint", "rule", "for_model", "action", "receipt",
                            "trigger", "group"}
    row = _by_card(rows)[STOPPED_CARD]
    assert row["fingerprint"] == operator_decide.fingerprint(green("gl-stopped-run"))
    assert row["repo"] == "dreadnought-foundry/portico"
    assert row["receipt"] is None and row["trigger"] is None


def test_the_green_light_card_file_is_fenced_and_names_its_lane(tmp_path, monkeypatch):
    _fixture_board(monkeypatch)
    _select(tmp_path)
    text = (tmp_path / "cards" / f"{STOPPED_CARD}.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == operator_agent.BEGIN and lines[-1] == operator_agent.END
    assert "Lane: Green Light" in lines
    assert STOPPED in text


def test_the_repo_rule_and_the_three_kinds_apply_in_green_light(tmp_path, monkeypatch):
    board = Board()
    board.add(stopped_run("DRE-9301"))
    board.add(_synthetic("DRE-9302", lane=LANE, labels=("repo:nowhere-at-all",)))
    board.add(_synthetic("DRE-9303", lane=LANE, title="PROOF: the epic's proof"))
    board.add(_synthetic("DRE-9304", lane=LANE, labels=("repo:agent-bureau", "hand-built")))
    board.install(monkeypatch)
    assert {r["card"] for r in _select(tmp_path)} == {"DRE-9301"}
    assert board.thread_reads == ["DRE-9301"]


def test_a_green_light_card_on_the_leave_alone_list_is_left_alone(tmp_path, monkeypatch,
                                                                     capsys):
    rules = operator_rules.load_rules(operator_agent.RULES_PATH)
    rules = {**rules, "leave_alone": [{"card": "DRE-9301", "why": "the switch card"}]}
    monkeypatch.setattr(operator_rules, "load_rules", lambda *a, **k: rules)
    board = Board()
    board.add(stopped_run("DRE-9301"))
    board.install(monkeypatch)
    assert _select(tmp_path) == []
    assert "operator-agent: DRE-9301 left alone — the switch card" in capsys.readouterr().out
    assert board.thread_reads == []


# --------------------------------------------------------------------------- #
# a standing receipt                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("outcome", ["repair", "answer"])
def test_a_moving_receipt_over_the_fingerprint_is_spent(tmp_path, monkeypatch, outcome):
    card = green("gl-stopped-run")
    card = pushed(card, _moving_receipt(card, outcome), "2026-11-03T10:00:00.000Z")
    board = Board()
    board.add(card)
    board.install(monkeypatch)
    (row,) = _select(tmp_path)
    assert (row["group"], row["action"], row["for_model"]) == (GL, "spent", False)
    assert row["receipt"].splitlines()[0].startswith(f"🛠️ operator-agent: {outcome} — ")
    assert not row["receipt"].splitlines()[-1].startswith("📎")
    assert board.history_reads == []


def test_a_spent_row_is_rebuilt_from_its_receipt_and_decide_hands_nothing_new(
        tmp_path, monkeypatch, capsys):
    card = green("gl-stopped-run")
    card = pushed(card, _moving_receipt(card, "repair"), "2026-11-03T10:00:00.000Z")
    board = Board()
    board.add(card)
    board.install(monkeypatch)
    _select(tmp_path)
    # An answer on disk that says something else is never read for it.
    _answers(tmp_path, _answer(STOPPED_CARD, "answer", step="a new question",
                               evidence=[STOPPED], answer="a new answer"))
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0
    (decision,) = decisions
    assert (decision["outcome"], decision["action"], decision["group"]) == ("repair", "spent", GL)
    assert decision["step"] == "re-run the review the critic never finished"
    assert decision["evidence"] == [STOPPED, STOPPED_PARK]
    (line,) = _tag_lines(capsys.readouterr().out)
    assert line.startswith(f"operator-agent: {STOPPED_CARD} → repair — ")
    assert line.endswith(" · spent")


def test_the_same_row_with_a_changed_description_is_decide_again(tmp_path, monkeypatch):
    card = green("gl-stopped-run")
    card = pushed(card, _moving_receipt(card, "repair"), "2026-11-03T10:00:00.000Z")
    card["description"] += "\n\nThe critic's run was re-started by hand."
    board = Board()
    board.add(card)
    board.install(monkeypatch)
    (row,) = _select(tmp_path)
    assert (row["group"], row["action"], row["for_model"], row["receipt"]) == (
        GL, "decide", True, None)


def test_a_receipt_written_in_another_lane_is_decide(tmp_path, monkeypatch):
    card = green("gl-stopped-run")
    receipt = operator_decide.Decision(
        card=STOPPED_CARD, repo="", lane="Hand-work", outcome="studio",
        reason="the step needs production access", step="deploy the stack",
        act="", deploy=False, evidence=[], host_cannot=["no deploy"], finding="",
        question="", recommendation="", choices=None, held="",
        fingerprint=operator_decide.fingerprint(card))
    card = pushed(card, _posted(receipt), "2026-11-03T10:00:00.000Z")
    board = Board()
    board.add(card)
    board.install(monkeypatch)
    (row,) = _select(tmp_path)
    assert (row["group"], row["action"], row["for_model"]) == (GL, "decide", True)
    assert board.history_reads == []


def _keep_pass(tmp_path, monkeypatch, record_id, n):
    board = Board()
    board.add(numbered(green(record_id), STOPPED_CARD))
    board.add(_synthetic("DRE-9001"))
    board.install(monkeypatch)
    pass_dir = tmp_path / f"pass-{n}"
    pass_dir.mkdir()
    return _by_card(_select(pass_dir, limit=1))


def test_the_second_pass_over_a_genuine_decision(tmp_path, monkeypatch, capsys):
    kept = operator_green_light.KEPT_WHY
    assert kept == "the agent read it and left it with the CEO"

    rows = _keep_pass(tmp_path, monkeypatch, "r1-keep", 1)
    assert STOPPED_CARD not in rows
    assert _tag_lines(capsys.readouterr().out).count(_left_line(STOPPED_CARD, kept)) == 1
    # It takes no slot: the one slot goes to the waiting card.
    assert _marked(rows.values()) == {"DRE-9001"}

    rows = _keep_pass(tmp_path, monkeypatch, "r1-keep-newer", 2)
    row = rows[STOPPED_CARD]
    assert (row["group"], row["action"], row["for_model"]) == (GL, "decide", True)
    assert _left_line(STOPPED_CARD, kept) not in capsys.readouterr().out

    rows = _keep_pass(tmp_path, monkeypatch, "r1-rekept", 3)
    assert STOPPED_CARD not in rows
    assert _tag_lines(capsys.readouterr().out).count(_left_line(STOPPED_CARD, kept)) == 1
    assert _marked(rows.values()) == {"DRE-9001"}


# --------------------------------------------------------------------------- #
# the order of the model's slots                                               #
# --------------------------------------------------------------------------- #


GREEN_ROWS = [f"DRE-{9300 + n}" for n in range(8)]


def _crowded_board(monkeypatch) -> Board:
    board = Board()
    board.add(entry_card())
    for ident in GREEN_ROWS:
        board.add(stopped_run(ident))
    for n in range(30):
        board.add(_synthetic(f"DRE-{9400 + n}"))
    return board.install(monkeypatch)


def _ring(rows, hour: str, slots: int) -> set:
    """The window of the green-light ring the hour reads, by its own
    arithmetic: sorted by card number, from `(h × slots) mod n`."""
    n = len(rows)
    h = operator_agent.hours_since_epoch(operator_agent._when(hour))
    start = (h * slots) % n
    return {rows[(start + i) % n] for i in range(slots)}


def test_the_triggered_row_then_five_green_light_rows_then_the_waiting_ring(
        tmp_path, monkeypatch):
    _crowded_board(monkeypatch)
    rows = _select(tmp_path / "one", limit=20)
    by = _by_card(rows)
    assert by[ENTRY_CARD]["trigger"] is not None and by[ENTRY_CARD]["for_model"] is True
    first = _marked(rows, GL)
    assert first == _ring(GREEN_ROWS, HOUR, 5) and len(first) == 5
    assert len(_marked(rows, WAITING) - {ENTRY_CARD}) == 14
    assert len(_marked(rows)) == 20
    for row in rows:
        assert row["action"] == ("decide" if row["for_model"] else "unread")

    second = _marked(_select(tmp_path / "two", limit=20, hour=NEXT_HOUR), GL)
    assert len(second) == 5
    assert set(GREEN_ROWS) - first <= second
    assert len(second & first) == 2
    assert second == _ring(GREEN_ROWS, NEXT_HOUR, 5)


def test_with_two_slots_the_triggered_row_and_the_first_of_the_green_light_ring(
        tmp_path, monkeypatch):
    _crowded_board(monkeypatch)
    rows = _select(tmp_path, limit=2)
    assert _marked(rows) == {ENTRY_CARD} | _ring(GREEN_ROWS, HOUR, 1)
    assert len(_marked(rows, GL)) == 1


def test_green_light_never_takes_the_routed_rows_slots(tmp_path, monkeypatch):
    board = Board()
    for n in range(3):
        board.add(_synthetic(f"DRE-{100 + n}",
                             description="The operator makes the IAM change in the console."))
    for ident in GREEN_ROWS:
        board.add(stopped_run(ident))
    board.install(monkeypatch)
    rows = _select(tmp_path, limit=4)
    assert _marked(rows, WAITING) == {"DRE-100", "DRE-101", "DRE-102"}
    assert len(_marked(rows, GL)) == 1


def test_mark_holds_green_light_to_its_slots_and_the_waiting_ring_keeps_the_rest():
    hour = operator_agent._when(HOUR)
    rows = ([{"card": f"DRE-{n}", "action": "decide", "group": GL, "rule": None,
              "trigger": None, "for_model": True} for n in range(1, 21)]
            + [{"card": f"DRE-{100 + n}", "action": "decide", "group": WAITING,
                "rule": None, "trigger": None, "for_model": True} for n in range(20)])
    operator_agent.mark(rows, 20, hour)
    assert len(_marked(rows, GL)) == operator_agent.GREEN_LIGHT_SLOTS
    assert len(_marked(rows, WAITING)) == 15


# --------------------------------------------------------------------------- #
# a sandbox fixture                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["Hand-work", "Queued"])
def test_a_sandbox_fixture_outside_green_light_is_left_alone(tmp_path, monkeypatch, capsys,
                                                             lane):
    board = Board()
    board.add(_synthetic("DRE-9501", lane=lane, title=SANDBOX_TITLE))
    board.install(monkeypatch)
    assert _select(tmp_path) == []
    assert _tag_lines(capsys.readouterr().out) == [
        "operator-agent: DRE-9501 left alone — a sandbox fixture, read only in Green Light"]
    assert board.thread_reads == []


def test_a_sandbox_fixture_in_green_light_is_read_like_any_row(tmp_path, monkeypatch, capsys):
    board = Board()
    card = stopped_run("DRE-9501")
    card["title"] = SANDBOX_TITLE
    board.add(card)
    board.install(monkeypatch)
    (row,) = _select(tmp_path)
    assert (row["card"], row["group"], row["action"]) == ("DRE-9501", GL, "decide")
    assert "sandbox fixture" not in capsys.readouterr().out
    assert board.thread_reads == ["DRE-9501"]


# --------------------------------------------------------------------------- #
# decide                                                                       #
# --------------------------------------------------------------------------- #


def _stopped_row(tmp_path, monkeypatch) -> list:
    board = Board()
    board.add(green("gl-stopped-run"))
    board.install(monkeypatch)
    return _select(tmp_path)


def test_decide_builds_a_repair_decision_for_a_green_light_row(tmp_path, monkeypatch, capsys):
    _stopped_row(tmp_path, monkeypatch)
    _answers(tmp_path, _repair())
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0
    (decision,) = decisions
    assert (decision["outcome"], decision["action"], decision["group"]) == (
        "repair", "decide", GL)
    assert decision["evidence"] == [STOPPED, STOPPED_PARK]
    assert decision["lane"] == LANE
    (line,) = _tag_lines(capsys.readouterr().out)
    assert line == (f"operator-agent: {STOPPED_CARD} → repair — the model read the row · step: "
                    "re-run the review the critic never finished")


def test_decide_builds_no_decision_for_a_do_answer_on_a_green_light_row(
        tmp_path, monkeypatch, capsys):
    _stopped_row(tmp_path, monkeypatch)
    _answers(tmp_path, _answer(STOPPED_CARD, "do"))
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0 and decisions == []
    assert _tag_lines(capsys.readouterr().out) == [
        operator_decide.unread_line(STOPPED_CARD, operator_decide.UNREAD_NO_ANSWER)]


def test_decide_builds_a_keep_decision_for_an_act_the_ceo_keeps(tmp_path, monkeypatch, capsys):
    record = next(r for r in _json(REPLAY) if r["card"] == "DRE-8173")
    card = operator_rules.fixture_card(record)
    match = operator_rules.rule_outcome(card["title"], card["description"])
    assert match is not None and match.outcome == operator_rules.CEO
    row = {"card": card["card"], "repo": "dreadnought-foundry/agent-bureau", "lane": LANE,
           "title": card["title"], "labels": card["labels"],
           "description": card["description"],
           "fingerprint": operator_decide.fingerprint(card), "rule": None,
           "for_model": True, "action": "decide", "receipt": None, "trigger": None,
           "group": GL}
    (tmp_path / "cards").mkdir()
    _answers(tmp_path, _answer("DRE-8173", "repair", step="settle the kept act",
                               evidence=["nothing at all"]))
    code, decisions = _decide(tmp_path, [row])
    assert code == 0
    (decision,) = decisions
    assert (decision["outcome"], decision["group"]) == ("keep", GL)
    assert decision["act"] == match.act
    (line,) = _tag_lines(capsys.readouterr().out)
    assert line == ("operator-agent: DRE-8173 → keep — a genuine decision — it stays with "
                    "the CEO · step: settle the kept act")


def test_decide_passes_the_waiting_group_for_a_waiting_row(tmp_path, monkeypatch):
    board = Board()
    board.add(_synthetic("DRE-9001"))
    board.install(monkeypatch)
    (row,) = _select(tmp_path)
    assert row["group"] == WAITING
    _answers(tmp_path, _answer("DRE-9001", "do", step="run the step"))
    code, decisions = _decide(tmp_path)
    assert code == 0
    (decision,) = decisions
    assert (decision["outcome"], decision["group"]) == ("do", WAITING)


def test_a_waiting_row_never_gets_a_green_light_outcome(tmp_path, monkeypatch, capsys):
    board = Board()
    board.add(_synthetic("DRE-9001"))
    board.install(monkeypatch)
    _select(tmp_path)
    _answers(tmp_path, _answer("DRE-9001", "repair", evidence=["A step a person takes."]))
    capsys.readouterr()
    code, decisions = _decide(tmp_path)
    assert code == 0 and decisions == []


# --------------------------------------------------------------------------- #
# the reads                                                                    #
# --------------------------------------------------------------------------- #


class Linear:
    """Linear's API under the real `comment_records`: the thread query
    answered from the board's cards, and every request kept."""

    def __init__(self, board: Board):
        self.board = board
        self.sent: list[str] = []

    def gql(self, query, variables=None, **kwargs):
        self.sent.append(query)
        if query.strip() == "query { viewer { id } }":
            return {"viewer": {"id": PIPELINE_USER}}
        assert "comments(" in query, query
        ident = (variables or {})["id"]
        card = next(c for c in self.board.cards if c["identifier"] == ident)
        return {"viewer": {"id": PIPELINE_USER}, "issue": {"comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": list(card["comments"]["nodes"])}}}

    def viewer_queries(self) -> int:
        return sum(1 for q in self.sent if q.strip() == "query { viewer { id } }")


def _viewer_queries(tmp_path, monkeypatch, with_green: bool) -> tuple[int, list]:
    monkeypatch.delenv("BUREAU_READ_CARD_MAX_AGE", raising=False)
    linear_ops._reset_budget_state()
    board = Board()
    board.add(entry_card())
    board.add(_synthetic("DRE-9001"))
    if with_green:
        board.add(green("gl-stopped-run"))
        board.add(stopped_run("DRE-9301"))
    board.install(monkeypatch, threads=False)
    linear = Linear(board)
    monkeypatch.setattr(linear_ops, "gql", linear.gql)
    rows = _select(tmp_path / ("green" if with_green else "empty"))
    return linear.viewer_queries(), rows


def test_the_green_light_rows_cost_no_viewer_read(tmp_path, monkeypatch):
    empty, rows = _viewer_queries(tmp_path, monkeypatch, with_green=False)
    assert {r["group"] for r in rows} == {WAITING}
    held, rows = _viewer_queries(tmp_path, monkeypatch, with_green=True)
    assert _marked(rows, GL) == {STOPPED_CARD, "DRE-9301"}
    assert held == empty


def test_the_green_light_rows_cost_no_lane_history_read(tmp_path, monkeypatch):
    hand = _synthetic("DRE-9001")
    ceo = operator_decide.Decision(
        card="DRE-9001", repo="", lane="Hand-work", outcome="ceo",
        reason="a kept act", step="choose the plan", act="business-choice", deploy=False,
        evidence=[], host_cannot=[], finding="", question="", recommendation="",
        choices={"question": "q?"}, held="", fingerprint=operator_decide.fingerprint(hand))
    board = Board()
    board.add(pushed(hand, operator_decide.receipt_text(ceo, NOW), "2026-11-03T09:00:00.000Z"))
    board.add(green("gl-stopped-run"))
    board.add(numbered(green("r1-keep-newer"), "DRE-92003"))
    board.add(numbered(green("r1-ceo"), "DRE-92001"))
    board.install(monkeypatch)
    rows = _by_card(_select(tmp_path))
    assert rows["DRE-9001"]["action"] == "apply-only"
    assert _marked(rows.values(), GL) == {STOPPED_CARD, "DRE-92003"}
    # Part 1 reads the lane history of its `ceo` card, and only that.
    assert board.history_reads == ["DRE-9001"]
    # The viewer is asked once for every Green Light row of the pass.
    assert board.viewer_reads == 1


def test_no_viewer_leaves_every_green_light_row_out(tmp_path, monkeypatch, capsys):
    def board_with(viewer, green_rows):
        board = Board(viewer=viewer)
        board.add(entry_card())
        for n in range(3):
            board.add(_synthetic(f"DRE-{9400 + n}"))
        if green_rows:
            board.add(green("gl-stopped-run"))
            board.add(stopped_run("DRE-9301"))
        return board.install(monkeypatch)

    board_with(PIPELINE_USER, False)
    plain = _select(tmp_path / "plain")
    capsys.readouterr()
    board_with(None, True)
    rows = _select(tmp_path / "none")
    out = capsys.readouterr().out
    assert rows == plain
    left = [line for line in _tag_lines(out) if " left in Green Light — " in line]
    assert sorted(left) == sorted(_left_line(ident, operator_green_light.NO_VIEWER)
                                  for ident in (STOPPED_CARD, "DRE-9301"))


def test_one_thread_read_per_card_whether_or_not_it_is_taken(tmp_path, monkeypatch):
    board = _fixture_board(monkeypatch)
    _select(tmp_path)
    idents = [c["identifier"] for c in board.cards]
    assert sorted(board.thread_reads) == sorted(idents)
    assert len(board.thread_reads) == len(set(board.thread_reads))
    assert board.viewer_reads == 1


# --------------------------------------------------------------------------- #
# the docstring, and the words                                                 #
# --------------------------------------------------------------------------- #


def test_the_docstring_says_what_select_reads_in_green_light():
    doc = operator_agent.__doc__
    flat = " ".join(doc.split())
    for name in ("operator_green_light.reading", "GREEN_LIGHT_SLOTS", "SANDBOX_OPENER",
                 "GREEN_LIGHT_GROUP", "WAITING_GROUP", "left in Green Light",
                 "keep_stands", "a sandbox fixture, read only in Green Light"):
        assert name in flat, name
    reads = flat.split("## The reads, named", 1)[1].split("## ", 1)[0].lower()
    assert "one whole-thread read per `green light` card" in reads
    assert "no lane-history or viewer read" in reads


def test_nothing_added_names_a_person():
    source = Path(operator_agent.__file__).read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", source)
    assert "sign-in" not in source.lower() and "sign in" not in source.lower()
