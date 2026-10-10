"""RED-first: an approved epic a blocker epic releases is asked about by the
sweep, not left for a person (DRE-6618).

THE INCIDENT. DRE-6064 (the parity check) was approved on 2026-10-07 while
blocked by the epic DRE-6063 (the backfills). On 2026-10-10 the last buildable
child of DRE-6063 finished, and agent-bureau's sweep printed `epic-gate:
DRE-6064 is **released at build-done**`. The same pass refused DRE-6066 and
DRE-6122 as `epic-cap-undecided`: no start was on record since the approval,
so the epic cap's hold (DRE-6493) kept them in Backlog. Nothing asked the cap
again. The refusal was on no stall clock, so the sweep stayed green, and it
would have stood until a person read a child card and posted the re-run act.

THE RULE. The full sweep that meets that hold asks the cap ITSELF when the
epic is past the activate route's window and a formal blocker relation is what
it waited on — the gate above has just released it. It asks once per epic per
pass, with `epic_cap.decision`, the four rules the activate route asks at
approval, and records the answer the usual way: `▶️ epic-started:` (and the
children move on that pass), or the `⏸️ epic-queued:` receipt, the label and
Green Light, where the line's own start (`start_queued_epics`) picks it up
with nobody's comment. A hold the sweep cannot settle that way stays a
refusal, and since this card it is on the stall clock.

How this differs from DRE-6591: that card's auto-advance carries a roll-up's
child epic still in BACKLOG — not yet planned — to Planning once every sibling
blocking it is Done. Here the epic was planned and approved and is already
In Progress; nothing needs planning, only the cap's decision.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap_released_epic_asks.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_critic  # noqa: E402
import promotion_stall  # noqa: E402
import reconcile  # noqa: E402
import review_rerun  # noqa: E402
import routing_verdict  # noqa: E402

EPIC, BLOCKER = "DRE-6064", "DRE-6063"
#: The two children nothing but the epic holds, and the four that wait on
#: DRE-6066 by a card-level relation.
FREE = ("DRE-6066", "DRE-6122")
TIED = ("DRE-6067", "DRE-6068", "DRE-6069", "DRE-6103")
#: The green light the refusal quoted: "no start is on record since it was
#: approved (2026-10-07T15:18:49.648Z)".
APPROVED_AT = "2026-10-07T15:18:49.648Z"
#: 03:50 PT on 2026-10-10, the pass that refused DRE-6066 and DRE-6122.
SWEPT_AT = datetime(2026, 10, 10, 10, 50, tzinfo=UTC)

FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def _blocker_child(identifier, state, *, title="a backfill", labels=()):
    """One of DRE-6063's children as `epic_cap.IN_MOTION_NODE` selects it."""
    return {
        "identifier": identifier, "title": title, "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels],
                   "pageInfo": {"hasNextPage": False}},
        "children": {"nodes": []},
    }


#: DRE-6063 on 2026-10-10: every build card Done; open only its proof and four
#: operator steps — the five the sweep's released line named.
BLOCKER_RECORD = {
    "identifier": BLOCKER,
    "title": "[EPIC] agent-bureau: the backfills",
    "priority": 2,
    "createdAt": "2026-10-01T00:00:00.000Z",
    "state": {"name": "In Progress"},
    "children": {"nodes": [
        _blocker_child("DRE-6070", "Done"),
        _blocker_child("DRE-6071", "Done"),
        _blocker_child("DRE-6113", "Backlog", title="PROOF: every backfill ran live"),
        *[_blocker_child(i, "Backlog", labels=("no-code", "operator-step"))
          for i in ("DRE-6109", "DRE-6097", "DRE-6096", "DRE-6095")],
    ], "pageInfo": {"hasNextPage": False}},
}


def _pipeline(body, at):
    return {"body": body, "authored_by_pipeline": True, "created_at": at}


class Board:
    """DRE-6064, its six children and its blocker, as the sweep reads them.

    The epic gate runs for real over Linear-shaped reads (`gql`), so the
    `released at build-done` line is the sweep's own. The cap's two reads
    (`fleet_state`, `read_epic`) are answered from the board, and every write
    lands on the board with a clock that ticks, so a later pass reads what an
    earlier one wrote.
    """

    def __init__(self, *, others_in_motion=9, waiting=0, blocker_relation=True,
                 now=SWEPT_AT):
        self.epic_lane = "In Progress"
        self.history = [{"createdAt": APPROVED_AT, "toState": {"name": "In Progress"}}]
        self.labels: set[str] = set()
        self.lanes = {i: "Backlog" for i in (*FREE, *TIED)}
        self.thread = [
            _pipeline(plan_critic.cycle_marker(EPIC), "2026-10-07T13:00:00.000Z"),
            _pipeline(plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS),
                      "2026-10-07T14:00:00.000Z"),
        ]
        self.child_comments: dict[str, list[str]] = {}
        self.others = others_in_motion
        self.waiting = waiting
        self.blocker_relation = blocker_relation
        self.now = now
        self.fleet_reads = 0
        self.writes: list[tuple] = []

    # --- the clock ------------------------------------------------------- #
    def tick(self) -> str:
        self.now += timedelta(minutes=1)
        return _iso(self.now)

    # --- Linear's answers ------------------------------------------------ #
    def record(self) -> dict:
        """`EPIC_RECORD_GQL`'s shape (the pass's epic record) — and enough of
        `epic_cap.EPIC_QUERY`'s that `read_epic` is answered from it too."""
        return {
            "id": "uuid-6064", "identifier": EPIC,
            "title": "[EPIC] agent-bureau: the parity check",
            "priority": 3, "createdAt": "2026-10-02T00:00:00.000Z",
            "description": "**Repo:** agent-bureau\nthe parity check",
            "state": {"name": self.epic_lane},
            "children": {"nodes": [
                {"identifier": i, "createdAt": "2026-10-06T00:00:00.000Z",
                 "state": {"name": lane}} for i, lane in self.lanes.items()]},
            "history": {"nodes": list(self.history)},
            "inverseRelations": {"nodes": [
                {"type": "blocks",
                 "issue": {"identifier": BLOCKER, "state": {"name": "In Progress"}}},
            ] if self.blocker_relation else []},
        }

    def gql(self, query, variables=None):
        v = variables or {}
        flat = " ".join(query.split())
        if "$numbers" in query:
            return {"issues": {"nodes": [self.record()],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "issue(id: $id)" in flat and "labels(first: 10)" in flat:
            assert v.get("id") == BLOCKER, v
            return {"issue": BLOCKER_RECORD}
        raise linear_ops.LinearError(f"Board: unexpected query {flat[:90]}")

    def fleet_state(self) -> dict:
        self.fleet_reads += 1
        in_motion = [{"identifier": f"DRE-{5000 + n}", "state": {"name": "In Progress"}}
                     for n in range(self.others)]
        if self.epic_lane == "In Progress":
            in_motion.append({"identifier": EPIC, "state": {"name": "In Progress"}})
        line = [{"identifier": f"DRE-{5900 + n}", "priority": 3,
                 "createdAt": "2026-09-01T00:00:00.000Z",
                 "state": {"name": "Green Light"},
                 "history": {"nodes": [{"createdAt": "2026-10-01T00:00:00.000Z",
                                        "toState": {"name": "In Progress"}}]}}
                for n in range(self.waiting)]
        if self.epic_lane == "Green Light" and epic_cap.QUEUED_LABEL in self.labels:
            line.append(self.record())
        return {"cap": 15, "count_rollup_parents": False,
                "in_motion": in_motion, "waiting": epic_cap.queue_order(line)}

    def read_epic(self, identifier) -> dict:
        assert identifier == EPIC
        return self.record()

    def comment_records(self, identifier, *, whole_thread=False):
        assert identifier == EPIC
        return list(self.thread)

    def children(self) -> list[dict]:
        cards = []
        for ident, lane in self.lanes.items():
            if lane != "Backlog":
                continue
            blocked = [] if ident in FREE else [("DRE-6066", self.lanes["DRE-6066"])]
            cards.append({
                "identifier": ident,
                "title": f"agent-bureau: {ident}",
                "description": "**Repo:** agent-bureau\nwork",
                "createdAt": "2026-10-06T00:00:00.000Z",
                "parent": {"identifier": EPIC, "state": {"name": self.epic_lane}},
                "labels": {"nodes": [{"name": "size:M"}]},
                "comments": {"nodes": [{"body": FLEET_VERDICT}]},
                "inverseRelations": {"nodes": [
                    {"type": "blocks", "issue": {"identifier": b, "state": {"name": s}}}
                    for b, s in blocked]},
            })
        return cards

    # --- the writes ------------------------------------------------------ #
    def advance(self, ident, to_state, from_states, *flags, **kw):
        self.writes.append(("advance", ident, to_state))
        if ident == EPIC:
            assert self.epic_lane in from_states.split(","), (self.epic_lane, from_states)
            self.epic_lane = to_state
            self.history.append({"createdAt": self.tick(), "toState": {"name": to_state}})
        else:
            self.lanes[ident] = to_state

    def comment(self, ident, body, *args, **kw):
        self.writes.append(("comment", ident, body))
        if ident == EPIC:
            self.thread.append(_pipeline(body, self.tick()))
        else:
            self.child_comments.setdefault(ident, []).append(body)

    def add_label(self, ident, label):
        self.writes.append(("label", ident, label))
        assert ident == EPIC
        self.labels.add(label)

    def count_comments(self, ident, needle, **kw):
        bodies = ([r["body"] for r in self.thread] if ident == EPIC
                  else self.child_comments.get(ident, []))
        return sum(1 for b in bodies if needle in b)

    def first_comment_at(self, ident, needle):
        """The oldest receipt carrying `needle` — the stall clock's read."""
        if any(needle in b for b in self.child_comments.get(ident, [])):
            return self.first_refused_at
        return None

    #: The stall clock ages a record against the wall clock (`promote_ready`
    #: reads `datetime.now` once a pass), so the receipt's age is set off it.
    first_refused_at = _iso(datetime.now(UTC) - timedelta(hours=3))

    # --- the passes ------------------------------------------------------ #
    def sweep(self, **kwargs) -> int:
        """One promotion pass of agent-bureau's sweep. `ask_cap=True` is the
        full sweep's (`main`); a promote-only pass leaves it off."""
        reconcile.reset_sweep_cards()
        with patch.object(reconcile, "REPO_SLUG", "agent-bureau"), \
                patch.object(reconcile, "age_minutes",
                             side_effect=lambda iso: _minutes(iso, self.now)), \
                patch.object(linear_ops, "gql", side_effect=self.gql), \
                patch.object(reconcile, "backlog_children", return_value=self.children()), \
                patch.object(linear_ops, "comment_records", side_effect=self.comment_records), \
                patch.object(epic_cap, "fleet_state", side_effect=self.fleet_state), \
                patch.object(epic_cap, "read_epic", side_effect=self.read_epic), \
                patch.object(linear_ops, "cmd_advance", side_effect=self.advance), \
                patch.object(linear_ops, "cmd_comment", side_effect=self.comment), \
                patch.object(linear_ops, "add_label", side_effect=self.add_label), \
                patch.object(linear_ops, "count_comments", side_effect=self.count_comments), \
                patch.object(linear_ops, "first_comment_at", side_effect=self.first_comment_at), \
                patch.object(routing_verdict, "lane_moves", return_value=[]):
            return reconcile.promote_ready(active_count=0, **kwargs)

    def start_from_the_line(self) -> None:
        """bureau-pipeline's sweep: `start_queued_epics`, the existing rule."""
        def get_issue(ident, **kw):
            return {"identifier": ident, "state": {"name": self.epic_lane},
                    "labels": {"nodes": [{"name": n} for n in sorted(self.labels)]}}

        with patch.object(reconcile, "REPO_SLUG", epic_cap.START_OWNER_SLUG), \
                patch.object(epic_cap, "waiting_line",
                             side_effect=lambda: self.fleet_state()["waiting"]), \
                patch.object(epic_cap, "fleet_state", side_effect=self.fleet_state), \
                patch.object(linear_ops, "get_issue", side_effect=get_issue), \
                patch.object(reconcile, "card_state", side_effect=lambda i: self.epic_lane), \
                patch.object(linear_ops, "cmd_advance", side_effect=self.advance), \
                patch.object(linear_ops, "cmd_comment", side_effect=self.comment):
            reconcile.start_queued_epics()

    # --- reading the board ----------------------------------------------- #
    def epic_receipts(self, opener: str) -> list[str]:
        return [r["body"] for r in self.thread if r["body"].lstrip().startswith(opener)]

    def promoted(self) -> list[str]:
        return sorted(i for i, lane in self.lanes.items() if lane == "Todo")


def _minutes(iso: str, now: datetime) -> float:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (now - then).total_seconds() / 60


@pytest.fixture(autouse=True)
def _fresh_pass():
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile.reset_sweep_cards()


STARTED = f"▶️ {epic_cap.STARTED_TAG}:"
QUEUED = f"⏸️ {epic_cap.QUEUED_TAG}:"


# --------------------------------------------------------------------------- #
# AC1 — the replay: 2026-10-10, room under the cap                             #
# --------------------------------------------------------------------------- #

class TestTheReplayWithRoom:
    """DRE-6064 and DRE-6063 as they stood at 03:50 PT on 2026-10-10, nine
    other epics in motion of fifteen and nobody waiting."""

    def test_the_sweep_records_a_start_and_promotes_dre_6066(self, capsys):
        board = Board()
        promoted = board.sweep(ask_cap=True)
        out = capsys.readouterr()
        # The gate's own line: the release is the sweep's, not the fixture's.
        assert f"epic-gate: {EPIC} is **released at build-done**: {BLOCKER}" in out.out
        assert "epic-cap: start — rule 4" in out.err, out.err
        started = board.epic_receipts(STARTED)
        assert len(started) == 1, board.thread
        assert promoted == 2
        assert board.promoted() == sorted(FREE)
        # The four that wait on DRE-6066 are held by their own relation.
        assert all(board.lanes[i] == "Backlog" for i in TIED)
        assert not reconcile._write_failures

    def test_the_start_is_the_usual_receipt_and_the_hold_reads_it(self):
        board = Board()
        board.sweep(ask_cap=True)
        body = board.epic_receipts(STARTED)[0]
        assert pipeline_act.read_trailer(body)["act"] == epic_cap.STARTED_ACT
        assert BLOCKER in body
        assert "10 of 15" in body  # nine others and this one
        assert epic_cap.start_on_record(board.thread, APPROVED_AT)

    def test_no_child_hears_the_hold_once_the_cap_has_answered(self):
        board = Board()
        board.sweep(ask_cap=True)
        assert not [b for bodies in board.child_comments.values() for b in bodies
                    if epic_cap.UNDECIDED_TAG in b]

    def test_the_cap_is_asked_once_per_epic_per_pass(self):
        """Two children meet the hold; one fleet read answers both."""
        board = Board()
        board.sweep(ask_cap=True)
        assert board.fleet_reads == 1

    def test_the_next_pass_asks_nothing(self):
        board = Board()
        board.sweep(ask_cap=True)
        board.sweep(ask_cap=True)
        assert board.fleet_reads == 1
        assert len(board.epic_receipts(STARTED)) == 1

    def test_today_the_same_pass_refuses_both_and_asks_nobody(self, capsys):
        """Non-vacuity, and the incident itself: without the ask — a
        promote-only pass is that today — DRE-6066 and DRE-6122 are refused
        `epic-cap-undecided` and nothing is written to the epic."""
        board = Board()
        assert board.sweep() == 0
        out = capsys.readouterr().out
        for ident in FREE:
            assert f"promotion: {ident} is not being promoted — ⏸️ " \
                   f"{epic_cap.UNDECIDED_TAG}" in out, out
        assert board.fleet_reads == 0
        assert not [w for w in board.writes if w[1] == EPIC]


# --------------------------------------------------------------------------- #
# AC2 — the cap full: queued with the usual receipt, started from the line     #
# --------------------------------------------------------------------------- #

class TestTheReplayAtTheCap:

    def test_the_epic_is_queued_with_the_queued_receipt(self, capsys):
        board = Board(others_in_motion=15, waiting=2)
        assert board.sweep(ask_cap=True) == 0
        assert "epic-cap: queue — rule 2" in capsys.readouterr().err
        queued = board.epic_receipts(QUEUED)
        assert len(queued) == 1, board.thread
        assert pipeline_act.read_trailer(queued[0])["act"] == epic_cap.QUEUED_ACT
        assert "place 3 of 3" in queued[0]
        assert epic_cap.QUEUED_LABEL in board.labels
        assert board.epic_lane == "Green Light"
        # In plan.yml's order: the label, the receipt, then the lane.
        epic_writes = [w[0] for w in board.writes if w[1] == EPIC]
        assert epic_writes == ["label", "comment", "advance"], epic_writes
        assert board.promoted() == []
        assert not reconcile._write_failures

    def test_it_starts_later_from_the_line_with_no_persons_comment(self):
        board = Board(others_in_motion=15)
        board.sweep(ask_cap=True)
        assert board.epic_lane == "Green Light"
        # A queued epic's children wait, and the sweep says nothing more.
        assert board.sweep(ask_cap=True) == 0
        assert board.fleet_reads == 1
        # An epic in motion runs out of cards: a slot opens.
        board.others = 14
        board.start_from_the_line()
        assert board.epic_lane == "In Progress"
        assert len(board.epic_receipts(STARTED)) == 1
        # agent-bureau's next pass: the start follows the newest approval.
        assert board.sweep(ask_cap=True) == 2
        assert board.promoted() == sorted(FREE)
        # Every word on the epic is the pipeline's.
        assert all(r["authored_by_pipeline"] for r in board.thread)


# --------------------------------------------------------------------------- #
# when the sweep does NOT ask                                                  #
# --------------------------------------------------------------------------- #

class TestWhenTheSweepLeavesTheHold:

    def test_inside_the_activate_routes_window_it_waits(self):
        """Approved twenty minutes ago: the activate route is asking now."""
        board = Board(now=datetime(2026, 10, 7, 15, 38, 49, tzinfo=UTC))
        assert board.sweep(ask_cap=True) == 0
        assert board.fleet_reads == 0

    def test_an_epic_that_waited_on_no_epic_is_left_to_its_activate_route(self):
        board = Board(blocker_relation=False)
        assert board.sweep(ask_cap=True) == 0
        assert board.fleet_reads == 0
        assert not [w for w in board.writes if w[1] == EPIC]

    def test_a_queued_receipt_since_the_approval_is_a_decision_on_record(self):
        board = Board()
        board.thread.append(_pipeline(
            epic_cap.queued_receipt(1, 1, 15, 15), "2026-10-07T15:20:00.000Z"))
        assert board.sweep(ask_cap=True) == 0
        assert board.fleet_reads == 0

    def test_while_its_activate_run_waits_for_a_planner_it_waits(self):
        board = Board()
        with patch.object(reconcile.planner_queue, "in_line", return_value=True):
            assert board.sweep(ask_cap=True) == 0
        assert board.fleet_reads == 0

    def test_an_unreadable_fleet_holds_and_writes_nothing(self, capsys):
        board = Board()

        def down():
            raise linear_ops.LinearError("Linear timed out")

        board.fleet_state = down
        assert board.sweep(ask_cap=True) == 0
        assert not [w for w in board.writes if w[1] == EPIC]
        assert any("Linear timed out" in f for f in reconcile._read_failures)
        # A failed read is never a bad blocker reference on the child.
        assert not [b for bodies in board.child_comments.values() for b in bodies
                    if reconcile.BAD_REF_TAG in b]

    def test_a_failed_receipt_is_a_write_failure_and_the_hold_stands(self):
        board = Board()

        def refused(ident, body, *a, **k):
            if ident == EPIC:
                raise linear_ops.LinearError("comment refused")
            board.child_comments.setdefault(ident, []).append(body)

        board.comment = refused
        assert board.sweep(ask_cap=True) == 0
        assert any("comment refused" in f for f in reconcile._write_failures)


# --------------------------------------------------------------------------- #
# AC3 — a hold nothing settles is on the stall clock                           #
# --------------------------------------------------------------------------- #

class TestTheHoldIsOnTheStallClock:

    def test_the_tag_is_clocked(self):
        assert promotion_stall.clocked(epic_cap.UNDECIDED_TAG)

    def test_a_hold_standing_past_the_bound_gets_one_stall_receipt_naming_the_epic(self):
        """The case the sweep cannot settle — an epic that waited on no epic —
        refused three hours ago and still refused on two passes."""
        board = Board(blocker_relation=False)
        board.sweep(ask_cap=True)
        board.sweep(ask_cap=True)
        for ident in FREE:
            stalled = [b for b in board.child_comments.get(ident, [])
                       if b.startswith(promotion_stall.STALL_MARK)]
            assert len(stalled) == 1, board.child_comments.get(ident)
            assert epic_cap.UNDECIDED_TAG in stalled[0]
            assert EPIC in stalled[0]
            assert pipeline_act.read_trailer(stalled[0])["act"] == promotion_stall.STALL_TAG
        assert any(EPIC in entry and epic_cap.UNDECIDED_TAG in entry
                   for entry in reconcile._stale_defects), reconcile._stale_defects

    def test_the_refusal_still_names_the_re_run_act(self):
        board = Board(blocker_relation=False)
        board.sweep(ask_cap=True)
        refusal = [b for b in board.child_comments[FREE[0]]
                   if b.startswith(f"⏸️ {epic_cap.UNDECIDED_TAG}:")]
        assert len(refusal) == 1
        assert review_rerun.RERUN_REVIEW_ACT in refusal[0]
        assert EPIC in refusal[0]

    def test_a_fresh_hold_is_not_stalled(self):
        board = Board(blocker_relation=False)
        board.first_refused_at = _iso(datetime.now(UTC) - timedelta(minutes=30))
        board.sweep(ask_cap=True)
        assert not [b for bodies in board.child_comments.values() for b in bodies
                    if b.startswith(promotion_stall.STALL_MARK)]


# --------------------------------------------------------------------------- #
# the wiring                                                                   #
# --------------------------------------------------------------------------- #

def test_the_full_sweep_asks_and_the_promote_only_pass_does_not():
    """`main` passes `ask_cap=True` on its full-sweep call alone: the
    activate route's own `--promote-only` pass and the merge path never ask."""
    source = (ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8")
    full = source.index("promote_ready(active_count=wip_count(mine), close_epics=True,")
    call = source[full:source.index(")", source.index("resolve_blockers=True", full)) + 1]
    assert "ask_cap=True" in call, call
    assert source.count("ask_cap=True") == 1
