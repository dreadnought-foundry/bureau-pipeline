"""RED-first: the sweep starts the next waiting epic (DRE-5152).

THE OTHER HALF OF THE CAP. DRE-5136 leaves an approved epic waiting in
`Green Light` with the `epic-queued` label when the fleet is at its cap of
epics in motion. This card is what starts it: two new phases of
`scripts/reconcile.py`.

  * `tend_epic_queue()` keeps the label honest — off any card that left the
    line any other way — and confirms that a start the sweep made actually
    took: an In Progress epic still carrying the label past the act's cadence,
    with nothing the pipeline wrote since its `▶️ epic-started:` receipt, is
    one the relay never activated, and its own repo's sweep asks for the
    activate run once.
  * `start_queued_epics()` moves the head of the line from Green Light to
    In Progress when there is room, and posts the receipt. The move IS the
    trigger: the relay fires `plan.yml`'s activate route on it. Only the
    owner's periodic sweep runs it, and any repo's `--close-only` pass that
    closed an epic.

Every fake here is Linear as these two phases read and write it, through the
seams the card names (`epic_cap`'s three reads, `linear_ops.get_issue`,
`comment_records`, `cmd_advance`, `cmd_comment`, `remove_label`) — never the
pass's board snapshot, which these phases do not read.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap_sweep.py -v
"""
from __future__ import annotations

import contextlib
import copy
import inspect
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import epic_cap  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import plan_run  # noqa: E402
import reconcile  # noqa: E402
import review_rerun  # noqa: E402

OWNER = epic_cap.START_OWNER_SLUG
OTHER = "atlas"
SANDBOX = "bureau-harness"
GREEN_LIGHT, IN_PROGRESS = "Green Light", "In Progress"
NOW = datetime.now(UTC)


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def _cadence_minutes() -> float:
    return pipeline_act.cadence_s(epic_cap.STARTED_ACT) / 60


def _epic(ident, *, state=GREEN_LIGHT, priority=3, approved=600, queued=True,
          repo=OWNER):
    """An epic as Linear holds it: a `repo:` label, `epic-queued` while it
    waits, and one approval in its history `approved` minutes ago."""
    labels = [{"name": f"repo:{repo}"}]
    if queued:
        labels.append({"name": epic_cap.QUEUED_LABEL})
    return {
        "id": f"uuid-{ident}", "identifier": ident, "title": f"[EPIC] {ident}",
        "description": f"the plan for {ident}", "priority": priority,
        "createdAt": _iso(100_000), "state": {"name": state},
        "labels": {"nodes": labels},
        "history": {"nodes": [
            {"createdAt": _iso(approved), "toState": {"name": IN_PROGRESS}},
        ]},
        "children": {"nodes": [{"id": f"kid-{ident}"}]},
    }


def _queued(epic) -> bool:
    return any(l["name"] == epic_cap.QUEUED_LABEL for l in epic["labels"]["nodes"])


def _row(body, minutes_ago, *, pipeline=True):
    return {"body": body, "authored_by_pipeline": pipeline,
            "created_at": _iso(minutes_ago)}


def _started(minutes_ago, *, pipeline=True):
    """The `▶️ epic-started:` receipt as the start posts it."""
    body = pipeline_act.receipt(epic_cap.STARTED_ACT, epic_cap.started_receipt(1, 15, 15))
    return _row(body, minutes_ago, pipeline=pipeline)


def _redispatched(minutes_ago):
    body = pipeline_act.receipt(
        epic_cap.REDISPATCHED_ACT,
        f"▶️ {epic_cap.REDISPATCHED_TAG}: asked for the activate run again.",
    )
    return _row(body, minutes_ago)


class Board:
    """Linear, as the two phases read and write it, with every call recorded
    in order (`calls`) and every READ counted (`requests`)."""

    def __init__(self, epics=(), *, cap=15, in_motion=14, threads=None):
        self.epics = {e["identifier"]: e for e in epics}
        self.cap = cap
        self.in_motion = in_motion
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        #: What a fresh re-read says instead of the board, per identifier.
        self.reread = {}
        self.refuse_advance = set()
        self.labeled = None  # a fixed answer for labeled_elsewhere, if set
        self.calls = []
        self.requests = 0
        self.fire = mock.MagicMock(return_value=(True, ""))
        self.raise_on = {}

    def _read(self, name, *args):
        self.calls.append((name, *args))
        self.requests += 1
        if name in self.raise_on:
            raise self.raise_on[name]

    # -- the reads ----------------------------------------------------------
    def _line(self):
        return epic_cap.queue_order([
            copy.deepcopy(e) for e in self.epics.values()
            if e["state"]["name"] == GREEN_LIGHT and _queued(e)
        ])

    def labeled_elsewhere(self):
        self._read("labeled_elsewhere")
        if self.labeled is not None:
            return copy.deepcopy(self.labeled)
        return [
            {"identifier": i, "state": {"name": e["state"]["name"]}}
            for i, e in self.epics.items()
            if _queued(e) and e["state"]["name"] != GREEN_LIGHT
        ]

    def waiting_line(self):
        self._read("waiting_line")
        return self._line()

    def fleet_state(self):
        self._read("fleet_state")
        return {
            "cap": self.cap, "count_rollup_parents": False,
            "in_motion": [{"identifier": f"DRE-{9000 + n}"} for n in range(self.in_motion)],
            "waiting": self._line(),
        }

    def get_issue(self, identifier, *, fresh=False):
        self._read("get_issue", identifier, fresh)
        issue = copy.deepcopy(self.epics[identifier])
        issue.update(copy.deepcopy(self.reread.get(identifier, {})))
        return issue

    def comment_records(self, identifier, *, whole_thread=False):
        self._read("comment_records", identifier)
        return copy.deepcopy(self.threads.get(identifier, []))

    def gql(self, query, variables=None):
        """`reconcile.card_state`'s read, and `plan_run.CARD_QUERY`."""
        ident = (variables or {}).get("id")
        self._read("gql", ident)
        epic = self.epics.get(ident)
        if epic is None:
            return {"issue": None}
        return {"issue": copy.deepcopy(epic)}

    # -- the writes ---------------------------------------------------------
    def cmd_advance(self, identifier, to_state, from_states, *flags, held=False):
        self.calls.append(("cmd_advance", identifier, to_state, from_states))
        epic = self.epics[identifier]
        allowed = [s.strip() for s in from_states.split(",")]
        if identifier not in self.refuse_advance and epic["state"]["name"] in allowed:
            epic["state"] = {"name": to_state}

    def cmd_comment(self, identifier, body, *flags):
        self.calls.append(("cmd_comment", identifier, body))
        self.threads.setdefault(identifier, []).append(_row(body, 0))

    def remove_label(self, identifier, label):
        self.calls.append(("remove_label", identifier, label))
        epic = self.epics.get(identifier)
        if epic is not None:
            epic["labels"]["nodes"] = [
                l for l in epic["labels"]["nodes"] if l["name"] != label
            ]

    def add_label(self, identifier, label):
        self.calls.append(("add_label", identifier, label))

    def cmd_state(self, identifier, state, *flags, **kwargs):
        self.calls.append(("cmd_state", identifier, state))

    # -- what happened ------------------------------------------------------
    def named(self, name):
        return [c for c in self.calls if c[0] == name]

    def writes(self):
        return [c for c in self.calls if c[0] in (
            "cmd_advance", "cmd_comment", "remove_label", "add_label", "cmd_state",
        )] + [("fire",) + tuple(c.args) for c in self.fire.call_args_list]

    @contextlib.contextmanager
    def patched(self):
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            for name in ("labeled_elsewhere", "waiting_line", "fleet_state"):
                enter(mock.patch.object(epic_cap, name, getattr(self, name)))
            for name in ("get_issue", "comment_records", "gql", "cmd_advance",
                         "cmd_comment", "remove_label", "add_label", "cmd_state"):
                enter(mock.patch.object(linear_ops, name, getattr(self, name)))
            enter(mock.patch.object(linear_ops, "requests_made", lambda: self.requests))
            enter(mock.patch.object(plan_run, "fire", self.fire))
            yield self


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", OWNER)
    ledgers = (reconcile._write_failures, reconcile._read_failures,
               reconcile._stale_defects)
    for ledger in ledgers:
        ledger.clear()
    yield
    for ledger in ledgers:
        ledger.clear()


def _slug(monkeypatch, slug):
    monkeypatch.setattr(reconcile, "REPO_SLUG", slug)
    monkeypatch.setattr(reconcile, "REPO", f"dreadnought-foundry/{slug}")


# --------------------------------------------------------------------------
# A whole main() pass, every other phase stood down
# --------------------------------------------------------------------------
#: Every phase of a full pass that is not under test here. The two new phases
#: stay live, and so does the close when a test fakes its answer.
_STOOD_DOWN = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "flag_unlanded_work", "flag_stranded_fixes", "fix_approved_but_red",
    "retry_dead_fix_runs", "redispatch_standing_verdicts", "recover_limit_deaths",
    "rerun_runner_lost_ci", "resync_desynced_heads", "restart_answered_blockers", "card_dependabot_prs", "review_dependabot_prs",
    "recover_crashed_reviews", "report_fleet_reviewer_outage",
    "check_dependabot_capacity", "settle_repair_cards", "report_intake_depth",
    "advance_urgent_intake", "repair_frozen_planning_holds", "release_groom_queue",
    "carry_epics_out_of_todo", "promote_ready", "move_hand_built_to_review",
    "report_break_glass", "report_fix_concurrency", "report_evicted_fix_runs",
)


@contextlib.contextmanager
def _main_world(board, *, idle=None, closed=frozenset(), scope=None, close_spends=0):
    """`main()` with every phase but the two new ones (and a faked close)
    stood down. `closed` is what the close says it closed; `scope` is what
    `merged_card_scope` answers on the close-only path."""
    def close(epics):
        board.calls.append(("close_finished_epics", tuple(sorted(epics))))
        board.requests += close_spends
        return set(closed)

    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(board.patched())
        for name in _STOOD_DOWN:
            enter(mock.patch.object(reconcile, name))
        enter(mock.patch.object(reconcile, "close_finished_epics", side_effect=close))
        enter(mock.patch.object(reconcile, "flag_stranded", return_value=set()))
        enter(mock.patch.object(reconcile, "serve_planner_line", return_value=0))
        enter(mock.patch.object(reconcile, "report_epic_growth", return_value=[]))
        enter(mock.patch.object(reconcile, "active_cards", return_value=[]))
        enter(mock.patch.object(reconcile, "sweep_idle", return_value=idle))
        enter(mock.patch.object(reconcile, "merged_card_scope", return_value=scope))
        enter(mock.patch.object(reconcile, "rereview_watch_scope",
                                return_value=(set(), lambda epic: None)))
        enter(mock.patch.object(reconcile.rereview_watch, "report"))
        yield board


def _run_main(board, capsys, **kwargs):
    """One `main()` pass; returns `(stdout lines, red)`."""
    mode = {k: kwargs.pop(k) for k in ("close_only", "promote_only", "conflicts_only")
            if k in kwargs}
    capsys.readouterr()
    red = False
    with _main_world(board, **kwargs):
        try:
            reconcile.main(**mode)
        except SystemExit:
            red = True
    out = capsys.readouterr()
    return out.out.splitlines() + out.err.splitlines(), red


def _epic_cap_reads(board):
    return [c[0] for c in board.calls
            if c[0] in ("labeled_elsewhere", "waiting_line", "fleet_state")]


# --------------------------------------------------------------------------
# 1: where the two phases run
# --------------------------------------------------------------------------
def test_both_phases_are_top_level_functions_of_reconcile():
    assert callable(getattr(reconcile, "tend_epic_queue", None))
    assert callable(getattr(reconcile, "start_queued_epics", None))


def test_the_full_sweep_runs_them_in_order_after_the_close_and_before_the_carry():
    """Read as text, the way the other suites read `main()`: on the full
    sweep the order is close → tend → start → carry, and neither is a member
    of the backstop tuple."""
    source = inspect.getsource(reconcile.main)
    full = source[source.index("nudges = 0"):]
    close = full.index('_phase("close_finished_epics")')
    tend = full.index('_phase("tend_epic_queue")')
    start = full.index('_phase("start_queued_epics")')
    carry = full.index('_phase("carry_epics_out_of_todo")')
    assert close < tend < start < carry
    tuple_text = full[full.index("for backstop in ("):full.index("):", full.index("for backstop in ("))]
    assert "tend_epic_queue" not in tuple_text
    assert "start_queued_epics" not in tuple_text


def test_close_only_runs_the_start_and_never_the_tending():
    source = inspect.getsource(reconcile.main)
    close_only = source[source.index("if close_only:"):source.index("nudges = 0")]
    assert '_phase("start_queued_epics")' in close_only
    assert "tend_epic_queue" not in close_only
    conflicts = source[source.index("if conflicts_only:"):source.index("if close_only:")]
    assert "epic_queue" not in conflicts and "queued_epics" not in conflicts


def test_promote_only_never_reaches_either_phase(capsys):
    board = Board([_epic("DRE-101")], in_motion=0)
    _run_main(board, capsys, promote_only=True)
    assert _epic_cap_reads(board) == []
    assert board.writes() == []


def test_conflicts_only_never_reaches_either_phase(capsys):
    board = Board([_epic("DRE-101")], in_motion=0)
    _run_main(board, capsys, conflicts_only=True)
    assert _epic_cap_reads(board) == []
    assert board.writes() == []


def test_close_only_that_closed_nothing_makes_no_epic_cap_read(capsys):
    board = Board([_epic("DRE-101")], in_motion=0)
    lines, _ = _run_main(board, capsys, close_only=True, closed=set())
    assert _epic_cap_reads(board) == []
    assert board.writes() == []
    assert not any(l.startswith("epic-start:") for l in lines), lines


# --------------------------------------------------------------------------
# 2: who starts the line
# --------------------------------------------------------------------------
def test_another_repos_full_sweep_tends_but_never_starts(monkeypatch, capsys):
    _slug(monkeypatch, OTHER)
    board = Board([_epic("DRE-101")], in_motion=0)
    lines, red = _run_main(board, capsys)
    reads = _epic_cap_reads(board)
    assert "labeled_elsewhere" in reads
    assert "waiting_line" not in reads and "fleet_state" not in reads
    assert board.named("cmd_advance") == []
    assert (f"epic-start: not the owner (REPO_SLUG={OTHER}) — "
            f"{OWNER} starts the line") in lines, lines
    assert not red


def test_the_owners_full_sweep_reads_the_line(capsys):
    board = Board([_epic("DRE-101")], in_motion=0)
    _run_main(board, capsys)
    assert "waiting_line" in _epic_cap_reads(board)


def test_another_repos_close_pass_that_closed_an_epic_reads_the_line(monkeypatch, capsys):
    _slug(monkeypatch, OTHER)
    board = Board([_epic("DRE-101")], in_motion=0)
    _run_main(board, capsys, close_only=True, closed={"DRE-77"})
    reads = _epic_cap_reads(board)
    assert "waiting_line" in reads
    assert "labeled_elsewhere" not in reads, "the tending never runs on --close-only"
    assert [c[1] for c in board.named("cmd_advance")] == ["DRE-101"]


# --------------------------------------------------------------------------
# 3: the line and the slots
# --------------------------------------------------------------------------
def test_nobody_waiting_makes_no_fleet_read(capsys):
    board = Board([], in_motion=3)
    with board.patched():
        reconcile.start_queued_epics()
    assert _epic_cap_reads(board) == ["waiting_line"]
    assert "epic-start: nobody waiting" in capsys.readouterr().out


def _start(board, **kwargs):
    with board.patched():
        reconcile.start_queued_epics(**kwargs)
    return [c[1] for c in board.named("cmd_advance")]


def test_one_slot_starts_the_higher_priority_epic():
    medium = _epic("DRE-101", priority=3, approved=900)
    high = _epic("DRE-102", priority=2, approved=60)
    board = Board([medium, high], in_motion=14)
    assert _start(board) == ["DRE-102"]
    assert board.epics["DRE-102"]["state"]["name"] == IN_PROGRESS
    assert board.epics["DRE-101"]["state"]["name"] == GREEN_LIGHT


def test_one_slot_at_equal_priority_starts_the_older_approval():
    newer = _epic("DRE-101", priority=2, approved=60)
    older = _epic("DRE-102", priority=2, approved=900)
    board = Board([newer, older], in_motion=14)
    assert _start(board) == ["DRE-102"]
    assert board.epics["DRE-101"]["state"]["name"] == GREEN_LIGHT


def test_two_slots_start_both_in_order():
    first = _epic("DRE-102", priority=1, approved=60)
    second = _epic("DRE-101", priority=4, approved=900)
    board = Board([second, first], in_motion=13)
    assert _start(board) == ["DRE-102", "DRE-101"]
    assert all(e["state"]["name"] == IN_PROGRESS for e in board.epics.values())


def test_at_the_cap_nothing_is_moved_labeled_or_posted(capsys):
    board = Board([_epic("DRE-101"), _epic("DRE-102")], in_motion=15)
    assert _start(board) == []
    assert board.writes() == []
    assert "epic-start: 15 of 15 in motion, 2 waiting — nothing to start" in (
        capsys.readouterr().out)


def test_over_the_cap_no_epic_in_progress_is_ever_moved():
    """The 20 epics already In Progress when the cap shipped keep running:
    the count stays over the cap until they close, and nothing stops them."""
    running = [_epic(f"DRE-{200 + n}", state=IN_PROGRESS, queued=False) for n in range(20)]
    board = Board(running + [_epic("DRE-101"), _epic("DRE-102")], in_motion=20)
    assert _start(board) == []
    assert board.writes() == []
    assert all(e["state"]["name"] == IN_PROGRESS for e in running)


@pytest.mark.parametrize("reread", [
    {"state": {"name": "Planning"}},
    {"labels": {"nodes": [{"name": f"repo:{OWNER}"}]}},
], ids=["left-green-light", "label-gone"])
def test_a_candidate_gone_on_its_re_read_stops_the_pass(reread):
    head = _epic("DRE-102", priority=1)
    next_ = _epic("DRE-101", priority=4)
    board = Board([head, next_], in_motion=13)
    board.reread["DRE-102"] = reread
    assert _start(board) == []
    assert board.writes() == []
    assert ("get_issue", "DRE-102", True) in board.calls


def test_a_refused_advance_stops_the_pass():
    head = _epic("DRE-102", priority=1)
    next_ = _epic("DRE-101", priority=4)
    board = Board([head, next_], in_motion=13)
    board.refuse_advance.add("DRE-102")
    assert _start(board) == ["DRE-102"]
    assert board.named("cmd_comment") == []
    assert board.epics["DRE-101"]["state"]["name"] == GREEN_LIGHT


def test_a_start_is_advance_then_confirm_then_one_receipt():
    board = Board([_epic("DRE-101")], in_motion=14)
    _start(board)
    sequence = [c[0] for c in board.calls if c[1:2] == ("DRE-101",)]
    advance = sequence.index("cmd_advance")
    confirm = sequence.index("gql", advance)
    comment = sequence.index("cmd_comment")
    assert advance < confirm < comment
    assert board.named("cmd_advance") == [
        ("cmd_advance", "DRE-101", IN_PROGRESS, GREEN_LIGHT)]
    (_, ident, body), = board.named("cmd_comment")
    assert ident == "DRE-101"
    assert body.startswith(f"▶️ {epic_cap.STARTED_TAG}:")
    assert epic_cap.started_receipt(1, 15, 15) in body
    assert pipeline_act.read_trailer(body)["act"] == epic_cap.STARTED_ACT
    assert board.named("remove_label") == [], "the activate route removes the label"
    board.fire.assert_not_called()


def test_a_start_prints_its_one_line(capsys):
    board = Board([_epic("DRE-101"), _epic("DRE-102", approved=60)], in_motion=14)
    _start(board)
    assert ("epic-start: DRE-101 started (was 1 of 2 in line; 15 of 15 in motion)"
            in capsys.readouterr().out)


def test_a_failed_move_is_named_and_stops_the_pass(capsys):
    board = Board([_epic("DRE-102", priority=1), _epic("DRE-101", priority=4)],
                  in_motion=13)

    def boom(*args, **kwargs):
        board.calls.append(("cmd_advance",) + args)
        raise linear_ops.LinearError("Linear said no")

    with board.patched(), mock.patch.object(linear_ops, "cmd_advance", boom):
        reconcile.start_queued_epics()
    assert [c[1] for c in board.named("cmd_advance")] == ["DRE-102"]
    assert board.named("cmd_comment") == []
    err = capsys.readouterr().err
    assert "DRE-102" in err and "Linear said no" in err


# --------------------------------------------------------------------------
# 4: the label stays honest
# --------------------------------------------------------------------------
@pytest.mark.parametrize("slug", [OWNER, OTHER])
def test_the_label_comes_off_every_card_outside_the_line(monkeypatch, capsys, slug):
    _slug(monkeypatch, slug)
    parked = _epic("DRE-301", state="Backlog")
    canceled = _epic("DRE-302", state="Canceled")
    waiting = _epic("DRE-303")
    board = Board([parked, canceled, waiting])
    board.labeled = [
        {"identifier": "DRE-301", "state": {"name": "Backlog"}},
        {"identifier": "DRE-302", "state": {"name": "Canceled"}},
        {"identifier": "DRE-303", "state": {"name": GREEN_LIGHT}},
    ]
    with board.patched():
        reconcile.tend_epic_queue()
    assert board.named("remove_label") == [
        ("remove_label", "DRE-301", epic_cap.QUEUED_LABEL),
        ("remove_label", "DRE-302", epic_cap.QUEUED_LABEL),
    ]
    assert [w for w in board.writes() if w[0] != "remove_label"] == []
    out = capsys.readouterr().out
    assert "epic-queue: DRE-301 left the line (Backlog) — label removed" in out
    assert "epic-queue: DRE-302 left the line (Canceled) — label removed" in out


# --------------------------------------------------------------------------
# 5: the start is confirmed
# --------------------------------------------------------------------------
def _started_board(thread, *, repo=OWNER):
    return Board([_epic("DRE-401", state=IN_PROGRESS, repo=repo)],
                 threads={"DRE-401": thread})


def _tend(board):
    with board.patched():
        reconcile.tend_epic_queue()


def test_a_start_the_relay_missed_is_re_dispatched_once():
    board = _started_board([_started(_cadence_minutes() + 30)])
    _tend(board)
    board.fire.assert_called_once()
    card, repo = board.fire.call_args.args
    assert card["identifier"] == "DRE-401"
    assert card["description"] == "the plan for DRE-401"
    assert repo == reconcile.REPO
    assert board.fire.call_args.kwargs["trigger_state"] == review_rerun.TRIGGER_STATE_ACTIVATE
    assert board.fire.call_args.kwargs["trigger_state"] == "in progress"
    assert board.fire.call_args.kwargs["reason"] == "epic-start-relay-missed"
    (_, ident, body), = board.named("cmd_comment")
    assert ident == "DRE-401"
    assert body.startswith(f"▶️ {epic_cap.REDISPATCHED_TAG}:")
    assert pipeline_act.read_trailer(body)["act"] == epic_cap.REDISPATCHED_ACT
    assert board.named("remove_label") == [] and board.named("cmd_advance") == []


def test_a_newer_comment_from_a_person_does_not_count_as_taken():
    board = _started_board([
        _started(_cadence_minutes() + 30),
        _row("is this one running?", 5, pipeline=False),
    ])
    _tend(board)
    board.fire.assert_called_once()


@pytest.mark.parametrize("thread", [
    pytest.param(lambda: [_started(_cadence_minutes() - 10)], id="younger-than-the-cadence"),
    pytest.param(lambda: [_started(_cadence_minutes() + 30),
                          _row("🎟️ planner-slot: waiting — place 2 of 3", 20)],
                 id="planner-slot-waiting"),
    pytest.param(lambda: [_started(_cadence_minutes() + 30),
                          _row("Approved under the old rule — handed back to Planning", 20)],
                 id="newer-pipeline-comment"),
    pytest.param(lambda: [_started(_cadence_minutes() * 3),
                          _redispatched(_cadence_minutes() * 2)],
                 id="already-re-dispatched"),
    pytest.param(lambda: [], id="no-start-receipt"),
])
def test_a_start_that_took_or_was_already_retried_is_left_alone(thread):
    board = _started_board(thread())
    _tend(board)
    board.fire.assert_not_called()
    assert board.writes() == []


def test_another_repos_epic_is_never_confirmed_from_here(monkeypatch):
    _slug(monkeypatch, OTHER)
    board = _started_board([_started(_cadence_minutes() + 30)], repo=OWNER)
    _tend(board)
    board.fire.assert_not_called()
    assert board.writes() == []
    assert board.named("get_issue") == [("get_issue", "DRE-401", True)]
    assert board.named("comment_records") == []


def test_each_in_progress_epic_is_read_once_and_its_thread_only_in_its_own_repo(
        monkeypatch):
    mine = _epic("DRE-401", state=IN_PROGRESS, repo=OTHER)
    theirs = _epic("DRE-402", state=IN_PROGRESS, repo=OWNER)
    _slug(monkeypatch, OTHER)
    board = Board([mine, theirs], threads={
        "DRE-401": [_started(10)], "DRE-402": [_started(10)],
    })
    _tend(board)
    assert sorted(board.named("get_issue")) == [
        ("get_issue", "DRE-401", True), ("get_issue", "DRE-402", True)]
    assert board.named("comment_records") == [("comment_records", "DRE-401")]


def test_a_re_dispatch_that_did_not_go_is_a_write_failure_with_no_receipt():
    board = _started_board([_started(_cadence_minutes() + 30)])
    board.fire.return_value = (False, "redispatch DRE-401: gh api failed rc=1")
    _tend(board)
    board.fire.assert_called_once()
    assert board.named("cmd_comment") == []
    assert reconcile._write_failures == ["redispatch DRE-401: gh api failed rc=1"]


# --------------------------------------------------------------------------
# 6: failure
# --------------------------------------------------------------------------
@pytest.mark.parametrize("read", ["labeled_elsewhere", "waiting_line", "fleet_state"])
def test_a_broken_cap_file_skips_the_phase_and_the_sweep_stays_green(read, capsys):
    board = Board([_epic("DRE-101"), _epic("DRE-102", state="Backlog")], in_motion=0)
    board.raise_on[read] = epic_cap.EpicCapError("config/epic-cap.json: the key cap is missing")
    lines, red = _run_main(board, capsys)
    said = [l for l in lines if "the key cap is missing" in l]
    assert len(said) == 1, lines
    assert not red
    assert reconcile._read_failures == [] and reconcile._write_failures == []


@pytest.mark.parametrize("read", [
    "labeled_elsewhere", "waiting_line", "fleet_state", "get_issue", "comment_records",
])
def test_an_unreadable_linear_is_a_read_failure(read, capsys):
    started = _epic("DRE-401", state=IN_PROGRESS)
    board = Board([_epic("DRE-101"), started], in_motion=0,
                  threads={"DRE-401": [_started(_cadence_minutes() + 30)]})
    board.raise_on[read] = linear_ops.LinearError("Linear is down")
    _lines, red = _run_main(board, capsys)
    assert reconcile._read_failures, f"a {read} that failed was not recorded"
    assert any("Linear is down" in f for f in reconcile._read_failures)
    assert red, "a read failure turns the pass red for the medic"
    reconcile._read_failures.clear()


# --------------------------------------------------------------------------
# 7: both close-only exits start the next epic
# --------------------------------------------------------------------------
_MERGED = SimpleNamespace(card="DRE-55", dependents=[], parent="DRE-77",
                          parent_state=IN_PROGRESS)


@pytest.mark.parametrize("scope, said", [
    (_MERGED, "close-only: epic close evaluated for DRE-55's parent"),
    (None, "close-only: epic close evaluated ("),
], ids=["merged-cards-parent", "whole-board"])
def test_each_close_only_exit_starts_the_next_epic(scope, said, capsys):
    board = Board([_epic("DRE-101")], in_motion=14)
    lines, _ = _run_main(board, capsys, close_only=True, scope=scope,
                         closed={"DRE-77"}, close_spends=1)
    assert [c[1] for c in board.named("cmd_advance")] == ["DRE-101"]
    assert board.named("waiting_line") == [("waiting_line",)]
    evaluated = next(i for i, l in enumerate(lines) if l.startswith(said))
    started = next(i for i, l in enumerate(lines) if l.startswith("epic-start: DRE-101 started"))
    assert evaluated < started
    spend = [l for l in lines if l.startswith("sweep-spend: ")]
    assert any(l.startswith("sweep-spend: close_finished_epics 1 ") for l in spend), spend
    assert any(l.startswith("sweep-spend: start_queued_epics ") for l in spend), spend


@pytest.mark.parametrize("scope", [_MERGED, None], ids=["merged-cards-parent", "whole-board"])
def test_a_close_only_exit_that_closed_nothing_starts_nothing(scope, capsys):
    board = Board([_epic("DRE-101")], in_motion=14)
    lines, _ = _run_main(board, capsys, close_only=True, scope=scope, closed=set())
    assert _epic_cap_reads(board) == []
    assert board.writes() == []
    assert not any(l.startswith("sweep-spend: start_queued_epics") for l in lines)


def test_the_close_says_which_epics_it_closed():
    """`close_finished_epics` returns the set it closed, and
    `_close_epic_if_finished` says True when it closed."""
    def record(epics):
        return {e: {"children": {"nodes": [{"state": {"name": "Done"}}]}}
                if e == "DRE-77" else
                {"children": {"nodes": [{"state": {"name": "In Progress"}}]}}
                for e in epics}

    with mock.patch.object(reconcile, "epic_records", side_effect=record), \
            mock.patch.object(linear_ops, "cmd_state"), \
            mock.patch.object(linear_ops, "cmd_comment"), \
            mock.patch.object(reconcile, "advance_unblocked_epics"):
        assert reconcile.close_finished_epics({"DRE-77", "DRE-78"}) == {"DRE-77"}
        assert reconcile._close_epic_if_finished("DRE-77") is True
        assert reconcile._close_epic_if_finished("DRE-78") is False


# --------------------------------------------------------------------------
# 8: an idle pass still starts the line
# --------------------------------------------------------------------------
def test_an_idle_owner_pass_still_runs_both_phases_and_starts(capsys):
    board = Board([_epic("DRE-101")], in_motion=14)
    lines, _ = _run_main(board, capsys, idle="no card of repo:bureau-pipeline is in motion")
    assert any(l.startswith("idle: ") for l in lines), lines
    reads = _epic_cap_reads(board)
    assert "labeled_elsewhere" in reads and "waiting_line" in reads
    assert [c[1] for c in board.named("cmd_advance")] == ["DRE-101"]


# --------------------------------------------------------------------------
# 9: the stand-in every other suite runs under
# --------------------------------------------------------------------------
def test_every_other_suite_reads_an_empty_queue_for_free():
    """`tests/conftest.py` answers both reads with nobody labeled and nobody
    waiting, so the 47 suites that run a whole sweep make no request for them."""
    with mock.patch.object(linear_ops, "gql",
                           side_effect=AssertionError("a request was made")):
        assert epic_cap.labeled_elsewhere() == []
        assert epic_cap.waiting_line() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
