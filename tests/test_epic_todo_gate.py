"""RED-first tests: no writer in the pipeline puts an epic in Todo (DRE-5316).

THE BUG (live). The pipeline moved epic DRE-3621 from In Progress to Todo at
21:43 PT on 2026-09-12, and it sat there seventeen days with four children
waiting. Todo is where a card waits for a build run, and nothing builds an
epic: its children promote from In Progress, and an epic in Todo is a plan
nobody is carrying out.

FIX UNDER TEST — one rule, `scripts/epic_todo_gate.py`, called at the three
places a card's lane is written: `linear_ops.guarded_state_write` (every lane
change of an existing card ends there) and the two creation seams,
`_create_card` and `create_card`. When the target lane is Todo and the card is
an epic, nothing is written and the refusal names the right move.

Epic-ness is `mid_epic.is_epic` over the shape stamp, the `[EPIC]` title and
the children — the composition `reconcile.card_is_epic` calls, and never the
`agent:planner` label (DRE-3038/DRE-3044). Approval is read off the board: the
lane the epic was in before Todo, In Progress meaning approved.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_todo_gate.py -v
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import epic_todo_gate as gate  # noqa: E402
import linear_ops  # noqa: E402
import planning_shape  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

MODULE = ROOT / "scripts" / "epic_todo_gate.py"


def _stamp(shape: str) -> str:
    return planning_shape.shape_comment(shape, "classified at Planning's front door")


def _verdict(name: str) -> str:
    return routing_verdict.verdict_comment(name, "buildable unattended in one PR")


EPIC_TITLE = "[EPIC] bureau-pipeline: the medic wakes only on a conclusion that can need it"
ONE_OFF_TITLE = "bureau-pipeline: the medic reads the conclusion before it wakes"


# ===========================================================================
# 1. What an epic is — the one helper, never the label
# ===========================================================================

# (name, title, has_children, comment_bodies, labels, expected)
FIXTURES = [
    ("planner-owned one-off, no stamp", ONE_OFF_TITLE, False, [],
     ["agent:planner", "repo:bureau-pipeline"], False),
    ("planner-owned one-off, one-off stamp", ONE_OFF_TITLE, False,
     [_stamp("one-off")], ["agent:planner", "repo:bureau-pipeline"], False),
    ("plain FLEET card", ONE_OFF_TITLE, False, [_verdict("FLEET")],
     ["agent:engineer", "repo:bureau-pipeline"], False),
    ("[EPIC] title", EPIC_TITLE, False, [], ["repo:bureau-pipeline"], True),
    ("a card with a child", ONE_OFF_TITLE, True, [], ["repo:bureau-pipeline"], True),
    ("epic stamp", ONE_OFF_TITLE, False, [_stamp("epic")],
     ["agent:planner", "repo:bureau-pipeline"], True),
    ("roll-up stamp", ONE_OFF_TITLE, False, [_stamp("roll-up")],
     ["agent:planner", "repo:bureau-pipeline"], True),
]


def _sweep_card(title, has_children, labels):
    return {
        "title": title,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": "kid-1"}] if has_children else []},
    }


class TestWhatAnEpicIs:
    @pytest.mark.parametrize("name,title,kids,bodies,labels,expected", FIXTURES,
                             ids=[f[0] for f in FIXTURES])
    def test_the_answer(self, name, title, kids, bodies, labels, expected):
        assert gate.is_epic_card(title, kids, bodies) is expected, name

    @pytest.mark.parametrize("name,title,kids,bodies,labels,expected", FIXTURES,
                             ids=[f[0] for f in FIXTURES])
    def test_it_equals_the_sweeps_answer(self, name, title, kids, bodies, labels,
                                         expected):
        """One answer to "is this an epic" across the write layer and the sweep:
        the module cannot import `reconcile` (circular), so this holds them
        equal instead."""
        card = _sweep_card(title, kids, labels)
        assert gate.is_epic_card(title, kids, bodies) is reconcile.card_is_epic(
            card, bodies), name

    def test_it_takes_no_labels(self):
        import inspect

        params = list(inspect.signature(gate.is_epic_card).parameters)
        assert params == ["title", "has_children", "comment_bodies"]


# ===========================================================================
# 2. The refusal — which move, and where the card is now
# ===========================================================================


class TestApproval:
    def test_only_in_progress_is_approved(self):
        assert gate.approved("In Progress") is True
        for lane in ("Green Light", "Planning", "Backlog", "Todo", "Intake", None, ""):
            assert gate.approved(lane) is False, lane

    def test_carry_lane(self):
        assert gate.carry_lane("In Progress") == "In Progress"
        for lane in ("Green Light", "Planning", "Backlog", None):
            assert gate.carry_lane(lane) == "Planning", lane


class TestRefusal:
    def _refuse(self, **kw):
        args = dict(identifier="DRE-3621", target_state="Todo", title=EPIC_TITLE,
                    has_children=True, comment_bodies=[],
                    lane_before_todo="In Progress")
        args.update(kw)
        return gate.refusal(**args)

    def test_a_non_epic_moving_to_todo_is_not_refused(self):
        assert self._refuse(title=ONE_OFF_TITLE, has_children=False) is None

    def test_a_planner_owned_one_off_moving_to_todo_is_not_refused(self):
        assert self._refuse(title=ONE_OFF_TITLE, has_children=False,
                            comment_bodies=[_stamp("one-off")]) is None

    @pytest.mark.parametrize("lane", ["In Progress", "Planning", "Green Light",
                                      "Backlog", "In Review", "Done"])
    def test_an_epic_moving_anywhere_else_is_not_refused(self, lane):
        assert self._refuse(target_state=lane) is None

    def test_an_approved_epic_is_told_in_progress(self):
        body = self._refuse(lane_before_todo="In Progress")
        assert body.startswith(f"🚫 {gate.TAG}: DRE-3621 → In Progress")
        assert body.splitlines()[0] == "🚫 epic-not-todo: DRE-3621 → In Progress"
        assert "never dispatched" in body
        assert "children promote" in body

    @pytest.mark.parametrize("lane", ["Green Light", "Planning", "Backlog", None])
    def test_an_unapproved_epic_is_told_green_light(self, lane):
        body = self._refuse(lane_before_todo=lane)
        assert body.splitlines()[0] == "🚫 epic-not-todo: DRE-3621 → Green Light"
        assert "never dispatched" in body
        assert "approval is the move to In Progress" in body

    @pytest.mark.parametrize("lane", ["In Progress", "Green Light", None])
    def test_at_the_seam_the_card_stays_where_it_is(self, lane):
        body = self._refuse(lane_before_todo=lane, carried_to=None)
        assert "stays where it is" in body
        assert "carried" not in body

    def test_an_unapproved_epic_carried_to_planning_is_told_so(self):
        body = self._refuse(lane_before_todo="Green Light", carried_to="Planning")
        assert body.splitlines()[0] == "🚫 epic-not-todo: DRE-3621 → Green Light"
        assert "carried to Planning" in body
        assert "both critics" in body
        assert "stays where it is" not in body

    def test_an_approved_epic_carried_to_in_progress_is_told_so(self):
        body = self._refuse(lane_before_todo="In Progress", carried_to="In Progress")
        assert "carried to In Progress" in body
        assert "stays where it is" not in body

    def test_the_tag_is_the_contract(self):
        assert gate.TAG == "epic-not-todo"


# ===========================================================================
# 3. The lane before Todo — the newest entry into Todo, newest-first
# ===========================================================================


class _History:
    def __init__(self, nodes=None, *, raises=None, answer=None):
        self.nodes = nodes or []
        self.raises = raises
        self.answer = answer
        self.queries = []

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        self.queries.append(q)
        if self.raises:
            raise self.raises
        if self.answer is not None:
            return self.answer
        m = re.search(r"history\((first|last):\s*(\d+)", q)
        assert m and m.group(1) == "first", (
            "history(first: N) is the NEWEST n, newest first; last: is the oldest"
        )
        return {"issue": {"history": {"nodes": self.nodes[: int(m.group(2))]}}}


def _entry(frm, to):
    return {"createdAt": "2026-09-12T21:43:00.000Z",
            "fromState": {"name": frm} if frm else None,
            "toState": {"name": to} if to else None}


class TestLaneBeforeTodo:
    def test_the_newest_entry_into_todo_decides(self):
        # Newest first: the epic was approved, then pushed into Todo; an older
        # trip Green Light → Todo is history and must not be what is read.
        ops = _History([
            _entry(None, None),  # a label change: no state on either side
            _entry("In Progress", "Todo"),
            _entry("Green Light", "In Progress"),
            _entry("Green Light", "Todo"),
            _entry("Planning", "Green Light"),
        ])
        assert gate.lane_before_todo(ops, "DRE-3621") == "In Progress"
        assert "history(first:" in ops.queries[0]

    def test_a_green_light_to_todo_drag_is_not_approval(self):
        ops = _History([_entry("Green Light", "Todo"), _entry("Planning", "Green Light")])
        assert gate.lane_before_todo(ops, "DRE-1") == "Green Light"
        assert gate.carry_lane(gate.lane_before_todo(ops, "DRE-1")) == "Planning"

    def test_an_unreadable_history_is_none_and_carries_to_planning(self):
        for ops in (_History(raises=linear_ops.LinearError("boom")),
                    _History(answer={"issue": None}),
                    _History(answer={}),
                    _History([])):
            assert gate.lane_before_todo(ops, "DRE-1") is None
        assert gate.carry_lane(None) == "Planning"


# ===========================================================================
# 4. The seam — guarded_state_write, driven through a stubbed gql
# ===========================================================================


class _Board:
    """A Linear double for one card: reads, the state write, the history the
    read-back needs, the comment thread and the comment write."""

    STATES = {
        "Intake": ("st-intake", "triage"),
        "Planning": ("st-planning", "backlog"),
        "Green Light": ("st-greenlight", "backlog"),
        "Backlog": ("st-backlog", "backlog"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "In Review": ("st-inreview", "started"),
        "Done": ("st-done", "completed"),
    }

    def __init__(self, current, *, title=EPIC_TITLE, children=False, labels=(),
                 comments=()):
        self.current = current
        self.title = title
        self.children = children
        self.labels = list(labels)
        self.comments = list(comments)  # oldest → newest
        self.updates = []
        self.created = []
        self.posted = []
        self.history = []  # newest first

    def _node(self, name):
        sid, stype = self.STATES[name]
        return {"id": sid, "name": name, "type": stype}

    def _name_for(self, sid):
        return next(n for n, (i, _t) in self.STATES.items() if i == sid)

    def comment(self, body):
        self.comments.append(body)

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": {
                "id": "card-uuid", "identifier": "DRE-3621", "title": self.title,
                "team": {"id": "team-1"}, "state": self._node(self.current),
                "labels": {"nodes": [{"name": n} for n in self.labels]},
                "children": {"nodes": [{"id": "kid"}] if self.children else []},
            }}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": i, "name": n, "type": t} for n, (i, t) in self.STATES.items()]}}
        if "issueUpdate" in q:
            sid = v["input"]["stateId"]
            self.updates.append(sid)
            self.history.insert(0, {
                "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "fromState": self._node(self.current),
                "toState": self._node(self._name_for(sid)),
            })
            self.current = self._name_for(sid)
            return {"issueUpdate": {"success": True}}
        if "history" in q:
            n = int(re.search(r"history\(first:\s*(\d+)", q).group(1))
            return {"issue": {"history": {"nodes": self.history[:n]}}}
        if "comments(" in q:
            nodes = [{"body": b, "createdAt": "2026-09-30T08:00:00.000Z",
                      "user": {"id": "fleet"}} for b in reversed(self.comments)]
            return {"viewer": {"id": "fleet"}, "issue": {"comments": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": nodes}}}
        if "commentCreate" in q:
            body = v["input"]["body"]
            self.posted.append(body)
            self.comments.append(body)
            return {"commentCreate": {"success": True}}
        if "issueCreate" in q:
            self.created.append(v["input"])
            return {"issueCreate": {"success": True, "issue": {
                "id": "new-uuid", "identifier": "DRE-9999",
                "url": "https://linear.app/x/DRE-9999"}}}
        if "teams(" in q:
            return {"teams": {"nodes": [{"id": "team-1"}]}}
        raise AssertionError(f"unexpected gql query: {q}")


def _run(fn, board, *args, **kw):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=board.gql), \
            redirect_stdout(buf):
        result = fn(*args, **kw)
    return result, buf.getvalue()


def _write(board, lane):
    """The seam itself, exactly as `cmd_state` calls it."""
    sid, stype = board.STATES[lane]
    issue = {"id": "card-uuid", "state": board._node(board.current)}
    return _run(linear_ops.guarded_state_write, board, "DRE-3621", issue, sid,
                stype, lane)


TODO = _Board.STATES["Todo"][0]


class TestTheSeam:
    def test_an_epic_is_refused_at_todo_once_across_two_attempts(self):
        board = _Board("In Progress", children=True, labels=["agent:planner"])
        ok, out = _write(board, "Todo")
        assert ok is False
        assert board.updates == [], "an epic must never be written into Todo"
        assert board.current == "In Progress"
        assert "epic-not-todo" in out

        # Another writer's receipt lands between the two attempts: the
        # dedupe is keyed on the card and the move, not on the newest comment.
        board.comment("🔁 some-other-receipt: DRE-3621 re-checked by the sweep")
        ok, _ = _write(board, "Todo")
        assert ok is False
        assert board.updates == []

        refusals = [b for b in board.posted if "epic-not-todo" in b]
        assert len(refusals) == 1, board.posted
        assert refusals[0].splitlines()[0] == "🚫 epic-not-todo: DRE-3621 → In Progress"
        assert "stays where it is" in refusals[0]

    def test_an_unapproved_epic_is_told_green_light(self):
        board = _Board("Green Light", title=EPIC_TITLE)
        ok, _ = _write(board, "Todo")
        assert ok is False and board.updates == []
        assert board.posted[0].splitlines()[0] == "🚫 epic-not-todo: DRE-3621 → Green Light"

    def test_a_stamped_epic_is_refused_whatever_its_title(self):
        board = _Board("In Progress", title=ONE_OFF_TITLE, comments=[_stamp("epic")])
        ok, _ = _write(board, "Todo")
        assert ok is False and board.updates == []

    def test_cmd_state_refuses_through_the_seam(self):
        board = _Board("In Progress", children=True)
        _, out = _run(linear_ops.cmd_state, board, "DRE-3621", "Todo")
        assert board.updates == []
        assert "DRE-3621 → Todo" not in out

    def test_the_building_card_reroute_cannot_put_an_epic_in_todo(self):
        """An unmarked Backlog park of an In Progress card is re-queued to
        Todo (DRE-1885). On an epic that reroute is the write this card
        refuses."""
        board = _Board("In Progress", children=True)
        _run(linear_ops.cmd_state, board, "DRE-3621", "Backlog")
        assert board.updates == []
        assert board.current == "In Progress"

    def test_cmd_advance_refuses_through_the_seam(self):
        board = _Board("Green Light", children=True)
        _run(linear_ops.cmd_advance, board, "DRE-3621", "Todo", "Green Light,Backlog")
        assert board.updates == []

    def test_a_fleet_card_still_writes_todo(self):
        board = _Board("Backlog", title=ONE_OFF_TITLE, labels=["agent:engineer"],
                       comments=[_verdict("FLEET")])
        ok, _ = _write(board, "Todo")
        assert ok is True
        assert board.updates == [TODO]
        assert board.posted == []

    def test_a_planner_owned_one_off_still_writes_todo(self):
        board = _Board("Backlog", title=ONE_OFF_TITLE, labels=["agent:planner"],
                       comments=[_stamp("one-off"), _verdict("FLEET")])
        ok, _ = _write(board, "Todo")
        assert ok is True
        assert board.updates == [TODO]
        assert board.posted == []

    @pytest.mark.parametrize("lane", ["In Progress", "Planning"])
    def test_an_epic_still_moves_to_other_lanes(self, lane):
        board = _Board("Green Light", children=True)
        ok, _ = _write(board, lane)
        assert ok is True
        assert board.updates == [_Board.STATES[lane][0]]
        assert board.posted == []


# ===========================================================================
# 5. The creation seams — a new card is an epic only by its title
# ===========================================================================


class TestCreation:
    def _create(self, board, title, lane):
        with patch.object(linear_ops, "_team_label_ids", return_value=[]):
            return _run(linear_ops._create_card, board, "team-1", title, "body",
                        ["repo:bureau-pipeline"], [], lane=lane)

    def test_create_card_refuses_an_epic_title_in_todo(self):
        board = _Board("Todo")
        with pytest.raises(linear_ops.LinearError) as exc:
            self._create(board, EPIC_TITLE, "Todo")
        assert "epic-not-todo" in str(exc.value)
        assert board.created == []

    def test_create_card_still_creates_a_plain_card_in_todo(self):
        board = _Board("Todo")
        self._create(board, ONE_OFF_TITLE, "Todo")
        assert len(board.created) == 1
        assert board.created[0]["stateId"] == TODO

    def test_create_card_still_creates_an_epic_elsewhere(self):
        board = _Board("Todo")
        self._create(board, EPIC_TITLE, "Planning")
        assert len(board.created) == 1

    def test_the_public_create_card_refuses_an_epic_title_in_todo(self):
        board = _Board("Todo")
        with patch.object(linear_ops, "_team_label_ids", return_value=[]), \
                pytest.raises(linear_ops.LinearError) as exc:
            _run(linear_ops.create_card, board, EPIC_TITLE, "body",
                 repo_slug="bureau-pipeline", lane="Todo")
        assert "epic-not-todo" in str(exc.value)
        assert board.created == []

    def test_the_public_create_card_still_creates_a_plain_card_in_todo(self):
        board = _Board("Todo")
        with patch.object(linear_ops, "_team_label_ids", return_value=[]):
            _run(linear_ops.create_card, board, ONE_OFF_TITLE, "body",
                 repo_slug="bureau-pipeline", lane="Todo")
        assert len(board.created) == 1


# ===========================================================================
# 6. The one posting site
# ===========================================================================


class _Poster:
    def __init__(self, comments=()):
        self.comments = list(comments)
        self.posted = []

    def count_comments(self, identifier, needle, **_kw):
        return sum(1 for b in self.comments if needle in b)

    def cmd_comment(self, identifier, body, *flags):
        self.posted.append(body)
        self.comments.append(body)
        return None


class TestPostRefusal:
    def _body(self, lane="In Progress", ident="DRE-3621"):
        return gate.refusal(ident, "Todo", EPIC_TITLE, True, [], lane)

    def test_posts_once_per_card_and_move(self):
        ops = _Poster()
        assert gate.post_refusal(ops, "DRE-3621", self._body()) is True
        ops.comments.append("unrelated receipt")
        assert gate.post_refusal(ops, "DRE-3621", self._body()) is False
        assert len(ops.posted) == 1

    def test_a_different_move_is_still_told(self):
        ops = _Poster()
        gate.post_refusal(ops, "DRE-3621", self._body("In Progress"))
        assert gate.post_refusal(ops, "DRE-3621", self._body("Green Light")) is True
        assert len(ops.posted) == 2

    def test_a_carried_body_on_the_same_move_is_not_posted_twice(self):
        ops = _Poster()
        gate.post_refusal(ops, "DRE-3621", self._body("Green Light"))
        carried = gate.refusal("DRE-3621", "Todo", EPIC_TITLE, True, [],
                               "Green Light", carried_to="Planning")
        assert gate.post_refusal(ops, "DRE-3621", carried) is False

    def test_a_failed_post_does_not_raise(self):
        class _Broken(_Poster):
            def cmd_comment(self, *a, **k):
                raise linear_ops.LinearError("comment write failed")

        assert gate.post_refusal(_Broken(), "DRE-3621", self._body()) is False


# ===========================================================================
# 7. The read-only reading for the proof
# ===========================================================================


class _ReadLayer:
    """Only reads answer; every write raises."""

    def __init__(self, *, title, lane, children=False, bodies=(), history=()):
        self.issue = {"identifier": "DRE-3621", "title": title,
                      "state": {"name": lane},
                      "children": {"nodes": [{"id": "k"}] if children else []}}
        self.bodies = list(bodies)
        self.history = list(history)
        self.writes = []

    def get_issue(self, identifier, *, fresh=False):
        return self.issue

    def comment_bodies(self, identifier):
        return list(self.bodies)

    def gql(self, query, variables=None):
        q = " ".join(query.split())
        if "mutation" in q:
            self.writes.append(q)
            raise AssertionError("explain wrote")
        return {"issue": {"history": {"nodes": self.history}}}

    def _refuse(self, name):
        def _w(*a, **k):
            self.writes.append(name)
            raise AssertionError(f"explain called {name}")
        return _w

    def __getattr__(self, name):
        if name in ("cmd_state", "cmd_advance", "guarded_state_write", "_set_state",
                    "cmd_comment", "create_card", "_create_card", "count_comments"):
            return self._refuse(name)
        raise AttributeError(name)


class TestExplain:
    def test_an_epic_in_todo_reads_refused(self, capsys):
        ops = _ReadLayer(title=EPIC_TITLE, lane="Todo", children=True,
                         history=[_entry("Green Light", "Todo")])
        gate.explain(ops, "DRE-3621")
        out = capsys.readouterr().out
        assert out.splitlines()[0].startswith("refused")
        assert "🚫 epic-not-todo: DRE-3621 → Green Light" in out
        assert ops.writes == []

    def test_an_approved_epic_elsewhere_reads_refused_toward_in_progress(self, capsys):
        ops = _ReadLayer(title=EPIC_TITLE, lane="In Progress", children=True)
        gate.explain(ops, "DRE-3621", "Todo")
        out = capsys.readouterr().out
        assert out.startswith("refused")
        assert "→ In Progress" in out
        assert ops.writes == []

    def test_a_one_off_reads_allowed(self, capsys):
        ops = _ReadLayer(title=ONE_OFF_TITLE, lane="Backlog",
                         bodies=[_stamp("one-off")])
        gate.explain(ops, "DRE-5316")
        out = capsys.readouterr().out
        assert out.startswith("allowed")
        assert ops.writes == []

    def test_an_epic_toward_another_lane_reads_allowed(self, capsys):
        ops = _ReadLayer(title=EPIC_TITLE, lane="Green Light", children=True)
        gate.explain(ops, "DRE-3621", "In Progress")
        assert capsys.readouterr().out.startswith("allowed")


# ===========================================================================
# 8. The module is not a writer
# ===========================================================================


class TestTheModuleWritesNothing:
    def test_it_calls_no_seam_function(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                called.add(f.attr if isinstance(f, ast.Attribute)
                           else getattr(f, "id", None))
        seam = set(ready_lane_writers.SEAM_PRIMITIVES) | {
            "cmd_state", "cmd_advance", "cmd_unpark", "create_card", "_create_card"}
        assert not (called & seam), called & seam

    def test_the_seam_check_finds_no_write_in_it(self):
        problems = ready_lane_writers.seam_problems(str(ROOT))
        assert not [p for p in problems if "epic_todo_gate" in p], problems

    def test_it_imports_without_a_linear_key(self):
        env = {k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"}
        r = subprocess.run(
            [sys.executable, "-c", "import epic_todo_gate"],
            cwd=str(ROOT / "scripts"), env=env, capture_output=True, text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr

    def test_it_does_not_import_linear_ops_or_reconcile_at_the_top(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                top.add(node.module)
        assert "linear_ops" not in top and "reconcile" not in top
        assert {"mid_epic", "routing_verdict"} <= top
