"""The 10-30 failure, replayed and repaired with no person (DRE-8636, epic DRE-8606).

From 10-30 10:46 AM PDT the scheduled Reconcile went red, run after run, on one
prose blocker: DRE-7305's description said `**Blocked by:** DRE-7300` and the
board held no relation behind it. On 10-31 at about 5:35 PM PT a person
reworded the sentence, and the sweep went green. This replays that day with the
pieces the epic built, and nobody touches the card:

1. the full sweep goes red on the epic's prose blocker, and raises the
   standing-defect alert (DRE-8632);
2. two hours later the Alarm Repair selects that alert (DRE-8633);
3. it claims the attempt, and applies the agent's `add-relation` decision —
   the relation DRE-7300 blocks DRE-7305, and its `repaired` receipt;
4. the next full sweep reads that relation, is green, and resolves the alert.

THE BOARD. One board, built in code — `tests/test_no_real_board_in_repo.py`
forbids a committed real one. It is `ReplayBoard`, a subclass of
`tests/alarm_repair_fakes.py`'s `AlarmRepairBoard`, rather than the sweep's
`FakeLinear`. The repair's board already answers every read and write
`alarm_repair` makes, including the relation mutation the real
`linear_ops._add_blocked_by` sends, and it keeps each card's relations as
state; the subclass adds the sweep's reads off that same state: its Queued
read and its batched epic record (both `gql_paged`, so `backlog_children`,
`epic_records` and the epic gate run as written), and the comment counts the
prose-defect report asks. So the relation `apply` writes is the relation the
second sweep's epic gate reads, with nothing in between.

THE SWEEP is `reconcile.main()`, run whole, with the backstops the epic gate
does not reach stood down the way `tests/test_standing_defect_alert.py` stands
them down (that tuple is imported, so the two suites drive one sweep shape),
GitHub answering empty, and the notice door's wire recorded. The notice
client, `standing_defect_alert.settle`, the promotion gate, the epic gate and
`_report_epic_prose_defect` are the real ones.

WHO WROTE WHAT. Every write the board takes is attributed by the call stack
that made it: the first frame in `scripts/reconcile.py`, `scripts/alarm_repair.py`
or `scripts/hold.py`. A write none of the three made would be a person's. The
test's own setup builds the cards' state directly, which is not a write the
board records.

Run: cd bureau-pipeline && python3 -m pytest tests/test_alarm_repair_replay.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import alarm_repair  # noqa: E402
import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import pipeline_notice  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
from alarm_repair_fakes import FLEET_USER, AlarmRepairBoard, Comment, iso  # noqa: E402
from test_standing_defect_alert import _STOOD_DOWN  # noqa: E402

REPO = "bureau-pipeline"
EPIC = "DRE-7305"
BLOCKER = "DRE-7300"
BLOCKER_EPIC = "DRE-7299"
CHILD = "DRE-7310"
NOTICE = f"standing-defect:{REPO}:{EPIC}"
KIND = f"pipeline_notice:{NOTICE}"
DESCRIPTION = (f"**Blocked by:** {BLOCKER}\n\n"
               "The epic as its author wrote it on 10-30. Nobody rewords it in this replay.\n")
RED_EXIT = ("reconcile: 0 write / 0 read failure(s), 1 unfixed card defect(s) — "
            "see ERROR lines above")

#: The files whose frames count as a pipeline seam, and the writer each is.
SEAMS = {
    str(ROOT / "scripts" / "reconcile.py"): "sweep",
    str(ROOT / "scripts" / "alarm_repair.py"): "alarm_repair",
    str(ROOT / "scripts" / "hold.py"): "hold",
}


def _writer() -> str | None:
    """The pipeline seam on the current call stack, innermost first — or None,
    which is a write no pipeline code made."""
    frame = sys._getframe(1)
    while frame is not None:
        found = SEAMS.get(frame.f_code.co_filename)
        if found:
            return found
        frame = frame.f_back
    return None


class _AttributedEvents(list):
    """`AlarmRepairBoard.events`, with the writer of each recorded beside it."""

    def __init__(self) -> None:
        super().__init__()
        self.writers: list[str | None] = []

    def append(self, event) -> None:
        super().append(event)
        self.writers.append(_writer())


class ReplayBoard(AlarmRepairBoard):
    """The repair's board, answering the sweep's reads off the same cards."""

    def __init__(self) -> None:
        super().__init__()
        self.events = _AttributedEvents()
        #: Every write the board refused because nothing in the replay may make it.
        self.refused: list[str] = []
        self.created: dict[str, str] = {}

    # --- the sweep's reads -------------------------------------------------

    def _window(self, card) -> dict:
        return {"pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"body": c.body, "createdAt": c.created_at,
                           "user": {"id": c.user}} for c in card.comments]}

    def _queued_node(self, card) -> dict:
        """A Queued card as `backlog_children`' query selects it."""
        node = self._node(card)
        parent = self.cards.get(card.parent) if card.parent else None
        node.update({
            "createdAt": self.created.get(card.identifier, iso(datetime.now(UTC))),
            "priority": 0,
            "parent": ({"identifier": parent.identifier,
                        "state": {"name": parent.lane}} if parent else None),
            "children": {"nodes": [{"id": f"id-{c}"} for c in card.children[:1]]},
            "comments": self._window(card),
        })
        return node

    def _epic_node(self, card) -> dict:
        """An epic as the pass's batched record (`EPIC_RECORD_GQL`) selects it."""
        node = self._node(card)
        node.update({
            "children": {"nodes": [{"identifier": c, "createdAt": iso(datetime.now(UTC)),
                                    "state": {"name": self.cards[c].lane}}
                                   for c in card.children if c in self.cards]},
            "history": {"nodes": []},
            "comments": {"nodes": [{"id": f"{card.identifier}-c{n}"}
                                   for n in range(len(card.comments))],
                         "pageInfo": {"hasNextPage": False, "endCursor": None}},
        })
        return node

    def gql_paged(self, query, variables=None, **kw):
        variables = variables or {}
        numbers = variables.get("numbers")

        def wanted(card) -> bool:
            return numbers is None or int(card.identifier.split("-")[1]) in numbers

        if query == reconcile._EPIC_RECORDS_QUERY:
            self.reads.append(("epic_records", ",".join(map(str, numbers or []))))
            return [self._epic_node(c) for c in self.cards.values() if wanted(c)]
        if reconcile.QUEUE_STATE_FILTER in query and "parent { identifier state" in query:
            self.reads.append(("backlog_children", ""))
            return [self._queued_node(c) for c in self.cards.values()
                    if c.lane in reconcile.QUEUE_LANES and wanted(c)]
        raise linear_ops.LinearError("offline in this replay: a read the board does not serve")

    def gql(self, query, variables=None, **kw):
        if not query.lstrip().startswith("mutation") and not (variables or {}).get("id"):
            raise linear_ops.LinearError("offline in this replay: a read the board does not serve")
        if query.lstrip().startswith("mutation") and "issueRelationCreate" not in query:
            self.refused.append(" ".join(query.split())[:80])
            raise linear_ops.LinearError("a mutation nothing in this replay may make")
        return super().gql(query, variables, **kw)

    def comment_bodies(self, identifier, *, whole_thread=False):
        return self.bodies(identifier)

    def count_comments(self, identifier, needle, *, since=None):
        return sum(1 for body in self.bodies(identifier) if needle in body)

    def first_comment_at(self, identifier, needle):
        return next((c.created_at for c in self._card(identifier).comments
                     if needle in c.body), None)

    # --- writes nothing here may make, said as such -------------------------

    def _refuse(self, what: str):
        self.refused.append(what)
        raise linear_ops.LinearError(f"{what}: a write nothing in this replay may make")

    def cmd_state(self, identifier, state_name, *flags, **kw):
        self._refuse(f"move {identifier} to {state_name}")

    def cmd_advance(self, identifier, state_name, *flags, **kw):
        self._refuse(f"advance {identifier} to {state_name}")

    def set_description(self, identifier, body):
        self._refuse(f"set {identifier}'s description")

    @contextlib.contextmanager
    def patched(self):
        with super().patched(), contextlib.ExitStack() as stack:
            for name in ("gql_paged", "comment_bodies", "count_comments",
                         "first_comment_at", "cmd_advance"):
                stack.enter_context(mock.patch.object(
                    linear_ops, name, side_effect=getattr(self, name)))
            yield self


class Door:
    """The notice door's wire, and the console behind it: a raise opens the
    alert the open-alerts read then serves, stamped with the clock of the
    sweep that raised it; a resolve closes it."""

    def __init__(self, board: ReplayBoard) -> None:
        self.board = board
        self.sent: list[dict] = []
        self.clock: datetime = datetime.now(UTC)

    def _send(self, payload: dict) -> str:
        self.sent.append(payload)
        kind = f"pipeline_notice:{payload['notice']}"
        open_rows = [row for row in self.board.alerts if row["kind"] == kind]
        if payload["action"] == "raise":
            if open_rows:
                return "repeat"
            self.board.alert(kind, raised_at=iso(self.clock),
                             severity=payload["severity"], title=payload["title"])
            return "raised"
        if not open_rows:
            return "not-open"
        self.board.alerts = [row for row in self.board.alerts if row["kind"] != kind]
        return "resolved"

    def standing(self) -> list[tuple[str, str]]:
        return [(p["action"], p["notice"]) for p in self.sent
                if p["notice"].startswith("standing-defect:")]


_LEDGERS = ("_write_failures", "_read_failures", "_stale_defects", "_card_skips",
            "_degraded", "_ledger_unread")


def _clear_ledgers() -> None:
    """One process is one pass in production; here two passes share one."""
    for name in _LEDGERS:
        getattr(reconcile, name).clear()


@pytest.fixture
def replay(monkeypatch):
    """The 10-30 board, its door, and the sweep's surroundings."""
    now = datetime.now(UTC)
    board = ReplayBoard()
    board.add(BLOCKER_EPIC, lane="Done", title="[EPIC] the work DRE-7300 proves",
              children=[BLOCKER])
    board.add(BLOCKER, lane="In Review", parent=BLOCKER_EPIC,
              title="PROOF: the work of DRE-7299 is live")
    board.add(EPIC, lane="In Progress", title="[EPIC] the 10-30 epic",
              description=DESCRIPTION, children=[CHILD])
    # The sweep's own `🚨 epic-prose-defect:` comment, first posted 3 hours
    # before this replay's now: past PROSE_DEFECT_RED_MINUTES.
    board.cards[EPIC].comments.append(Comment(
        prose_blockers.epic_refusal(EPIC, {BLOCKER}), iso(now - timedelta(hours=3)),
        FLEET_USER))
    board.add(CHILD, lane="Queued", parent=EPIC, title="a child of the 10-30 epic",
              description="The child's work.\n")
    board.cards[CHILD].comments.append(Comment(
        routing_verdict.verdict_comment("FLEET", "the acceptance criteria are unit-testable"),
        iso(now - timedelta(hours=20)), FLEET_USER))
    board.created[CHILD] = iso(now - timedelta(hours=24))
    assert timedelta(hours=3) > timedelta(minutes=reconcile.PROSE_DEFECT_RED_MINUTES)

    door = Door(board)
    door.clock = now
    monkeypatch.setattr(pipeline_notice, "_send", door._send)
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", REPO)
    for name in _STOOD_DOWN:
        monkeypatch.setattr(reconcile, name, mock.MagicMock())
    monkeypatch.setattr(reconcile, "flag_stranded", mock.MagicMock(return_value=set()))
    monkeypatch.setattr(reconcile, "report_epic_growth", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(reconcile, "active_cards", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(reconcile, "merged_card_scope", mock.MagicMock(return_value=None))
    monkeypatch.setattr(reconcile.subprocess, "run", mock.MagicMock(
        side_effect=lambda argv, **kw: mock.Mock(
            returncode=0, stdout="[]" if argv[:3] == ["gh", "pr", "list"] else "",
            stderr="")))
    monkeypatch.setattr(reconcile.time, "sleep", lambda s: None)
    _clear_ledgers()
    with board.patched():
        yield board, door, now
    _clear_ledgers()


def _sweep() -> tuple[str | None, str]:
    """One full `reconcile.main()`: its exit text (None is green) and its
    whole output, stdout, stderr and the exit line, as the run log holds it."""
    _clear_ledgers()
    out, err = io.StringIO(), io.StringIO()
    exit_text = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            reconcile.main()
        except SystemExit as e:
            exit_text = str(e.code)
    log = out.getvalue() + err.getvalue() + (f"{exit_text}\n" if exit_text else "")
    return exit_text, log


def _epic_gate(log: str) -> list[str]:
    return [line for line in log.splitlines() if line.startswith(f"epic-gate: {EPIC}")]


def test_the_10_30_failure_goes_red_is_repaired_and_goes_green_with_no_person(
        replay, monkeypatch, tmp_path):
    board, door, now = replay

    # ── 1. the first sweep: red on the epic's prose blocker ──────────────────
    exit_text, log = _sweep()
    assert exit_text == RED_EXIT, log
    assert door.standing() == [("raise", NOTICE)], door.sent
    assert medic_classify.classify("Reconcile", log) == "standing_defect"
    gate = _epic_gate(log)
    assert gate and all(prose_blockers.CARD_TAG in line for line in gate), gate

    # ── 2. two hours later: the repair selects the alert ─────────────────────
    later = now + timedelta(hours=2, minutes=5)
    monkeypatch.setattr(alarm_repair, "_now", lambda: later)
    served = bureau_read.open_alerts()["alerts"]
    assert served == [{"kind": KIND, "severity": "WARN",
                       "title": f"{EPIC} is keeping the {REPO} sweep red",
                       "raised_at": iso(later - timedelta(hours=2, minutes=5)),
                       "card": EPIC}]
    raised = served[0]["raised_at"]
    selected = tmp_path / "selected.json"
    assert alarm_repair.main(["select", "--out", str(selected)]) == 0
    rows = json.loads(selected.read_text(encoding="utf-8"))
    assert [(r["card"], r["action"]) for r in rows] == [(EPIC, "repair")], rows
    assert {"prose-blocker", "standing-defect"} <= set(rows[0]["classes"])
    assert rows[0]["undeclared"] == [BLOCKER]

    # ── 3. the repair: claim, the agent's decision, apply ────────────────────
    assert alarm_repair.main(["claim", EPIC, "--alert", KIND, "--raised", raised]) == 0
    (tmp_path / "decision.json").write_text(json.dumps({
        "action": "add-relation", "relations": [BLOCKER],
        "why": f"The epic's description declares {BLOCKER} a blocker and the board "
               "holds no relation for it; the relation is the dependency it declares.",
    }), encoding="utf-8")
    assert alarm_repair.main(["apply", EPIC, "--alert", KIND, "--raised", raised,
                              "--dir", str(tmp_path)]) == 0
    assert board.relations(EPIC) == [BLOCKER]
    assert board.newest(EPIC).splitlines()[0] == (
        f"🔩 alarm-repair: repaired · alert={KIND} · raised={raised} · move=pipeline")

    # ── 4. the second sweep: green, and the alert resolved ───────────────────
    exit_text, log = _sweep()
    assert exit_text is None, log
    assert door.standing() == [("raise", NOTICE), ("resolve", NOTICE)], door.sent
    assert board.alerts == []
    gate = _epic_gate(log)
    assert gate, log
    assert not any(prose_blockers.CARD_TAG in line for line in gate), gate
    assert any(f"formal blockedBy relation on {BLOCKER}" in line for line in gate), gate

    # ── what holds across the whole run ──────────────────────────────────────
    assert board.description(EPIC) == DESCRIPTION
    assert board.refused == []
    writes = list(zip(board.events, board.events.writers))
    assert writes, "the replay made no write at all"
    assert {writer for _, writer in writes} <= {"sweep", "alarm_repair", "hold"}, writes
    assert (("relation", EPIC, BLOCKER), "alarm_repair") in writes, writes
    assert all(c.user == FLEET_USER for card in board.cards.values() for c in card.comments)
