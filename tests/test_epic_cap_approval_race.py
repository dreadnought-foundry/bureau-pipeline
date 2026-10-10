"""An approval cannot start an epic past the cap by a race (DRE-6493).

On an approval the relay sends two runs at once: a full-pass sweep and
`plan.yml`, whose activate route asks `epic_cap.py decide` whether the epic
starts or waits in line. Nothing ordered the two. On 2026-10-05 the sweep
won: Portico sweep 37392493131 promoted DRE-5467, DRE-5468 and DRE-5469 to
Todo at 17:10:37 PT, and `plan.yml` run 37392495772 then saw a child in Todo
and answered `start — rule 1` at 17:12:49 PT, with five epics waiting and
18–20 of 15 in motion. Rule 1 reads a child out of Backlog as "this epic has
run before", and the sweep had just made that true.

The cap decision is now made before any child can be promoted: the sweep
holds the children of an In Progress epic that has never run until a start is
on record for its current approval — the activate route's `▶️ Epic activated`
note, or the sweep's own `▶️ epic-started:` receipt. A queued epic goes back
to Green Light with its children still in Backlog. An epic with a child
already out of Backlog has run, so nothing changes for it, and rule 1 still
starts it.

These tests drive the real `reconcile.promote_ready` and the real
`epic_cap.decision` over one board, in the order the two runs met on 10-05.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_cap_approval_race.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import epic_cap  # noqa: E402
import plan_critic  # noqa: E402
import reconcile  # noqa: E402
from test_reconcile_promotion import (  # noqa: E402
    FLEET_VERDICT,
    _backlog_card,
    _planner_output,
)

EPIC = "DRE-3019"  # the fixture's epic, whose children `_planner_output` writes
WORK = ("DRE-3026", "DRE-3027", "DRE-3028")
#: 17:09 PT on 2026-10-05: the CEO moves the epic to In Progress.
APPROVED_AT = "2026-10-06T00:09:00.000Z"
#: 17:13 PT: the activate route's note, after the cap answered `start`.
ACTIVATED_AT = "2026-10-06T00:13:14.000Z"
#: Before the cap shipped (2026-09-28): an epic genuinely running already.
PRE_CAP_APPROVAL = "2026-09-20T16:00:00.000Z"

ACTIVATED_NOTE = (
    "▶️ Epic activated (3 children) — the dependency gate will flow them to "
    "build in order. Nothing else to do."
)


def _pipeline(body: str, at: str | None = None) -> dict:
    """A comment on the epic as `linear_ops.comment_records` reports it."""
    return {"body": body, "authored_by_pipeline": True, "created_at": at}


def _passed_thread() -> list[dict]:
    """The epic's thread once both critics passed the plan: what the CEO
    approved. The second critic's PASS releases the children (DRE-3059) — the
    gate this card adds is the one still standing after it."""
    return [
        _pipeline(plan_critic.cycle_marker(EPIC)),
        _pipeline(plan_critic.marker(plan_critic.STAGE_POST, 1, plan_critic.PASS)),
    ]


def _fleet(others_in_motion: int, waiting: int = 0, cap: int = 15) -> dict:
    """The fleet `epic_cap.fleet_state` reads, built by hand: `others` epics
    in motion besides the asked one, and `waiting` epics in line ahead of it
    (approved earlier, same priority)."""
    in_motion = [
        {"identifier": f"DRE-{1000 + n}", "state": {"name": epic_cap.IN_PROGRESS}}
        for n in range(others_in_motion)
    ]
    line = [
        {"identifier": f"DRE-{2000 + n}", "priority": 3,
         "createdAt": "2026-09-01T00:00:00.000Z",
         "state": {"name": epic_cap.GREEN_LIGHT},
         "history": {"nodes": [{"createdAt": f"2026-10-0{1 + n}T00:00:00.000Z",
                                "toState": {"name": epic_cap.IN_PROGRESS}}]}}
        for n in range(waiting)
    ]
    return {"cap": cap, "count_rollup_parents": False,
            "in_motion": in_motion, "waiting": epic_cap.queue_order(line)}


class _Approval:
    """One epic on one board, read by both runs.

    The sweep is `reconcile.promote_ready` with every gate this card does not
    touch held open — the same seams `tests/test_reconcile_promotion.py`
    patches — and the epic's record (its lane and its children's lanes) read
    off this board, so what the sweep moves is what the decision then reads.
    The decision is `epic_cap.decision` over that record, which is what
    `epic_cap.py decide` asks after its two reads.
    """

    def __init__(self, *, green_light=APPROVED_AT, thread=None,
                 lanes=None, minutes_since_approval=2):
        records = {c["identifier"]: c for c in _planner_output()}
        self.cards = {i: _backlog_card(records[i], [FLEET_VERDICT]) for i in WORK}
        self.lanes = dict(lanes or {i: "Backlog" for i in WORK})
        self.epic_lane = epic_cap.IN_PROGRESS
        self.green_light = green_light
        self.thread = list(_passed_thread() if thread is None else thread)
        self.minutes = minutes_since_approval
        self.posted: list[tuple[str, str]] = []
        self.whole_reads = 0

    # --- the board ------------------------------------------------------- #
    def record(self) -> dict:
        """The epic as Linear answers it: the sweep's `epic_records` and
        `decide`'s `read_epic` both carry the children's lanes."""
        return {
            "identifier": EPIC,
            "title": f"[EPIC] bureau-pipeline: {EPIC}",
            "priority": 3,
            "createdAt": "2026-09-01T00:00:00.000Z",
            "state": {"name": self.epic_lane},
            "history": {"nodes": [{"createdAt": self.green_light,
                                   "toState": {"name": epic_cap.IN_PROGRESS}}]},
            "children": {"nodes": [
                {"identifier": i, "createdAt": "2026-07-01T00:00:00.000Z",
                 "state": {"name": lane}}
                for i, lane in self.lanes.items()
            ]},
        }

    def comment_records(self, epic: str, *, whole_thread: bool = False) -> list[dict]:
        """Linear's read: the fifty newest outside a pass, unless asked for
        the whole thread."""
        if whole_thread:
            self.whole_reads += 1
            return list(self.thread)
        return list(self.thread[-reconcile.linear_ops.COMMENT_WINDOW:])

    def in_backlog(self) -> list[dict]:
        cards = []
        for ident, lane in self.lanes.items():
            if lane != "Backlog":
                continue
            card = self.cards[ident]
            card["parent"] = {"identifier": EPIC, "state": {"name": self.epic_lane}}
            cards.append(card)
        return cards

    # --- the two runs ---------------------------------------------------- #
    def sweep(self) -> int:
        def advance(ident, to_state, from_states):
            self.lanes[ident] = to_state

        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=self.in_backlog()
        ), patch.object(
            reconcile, "epic_records",
            side_effect=lambda ids: {EPIC: self.record()} if EPIC in set(ids) else {},
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light", return_value=self.green_light
        ), patch.object(
            reconcile.linear_ops, "comment_records", side_effect=self.comment_records
        ), patch.object(
            reconcile, "age_minutes", return_value=self.minutes
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=lambda i, label: None
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb),
        ):
            return reconcile.promote_ready(active_count=0)

    def decide(self, fleet: dict, capsys) -> tuple[str, str]:
        """The plan run's question, and the rule line it printed."""
        capsys.readouterr()
        answer = epic_cap.decision(fleet, EPIC, self.record())
        return answer, capsys.readouterr().err

    def activate(self) -> None:
        """`plan.yml`'s activate step on `start`: the note, then its own
        `reconcile.py --promote-only`."""
        self.thread.append(_pipeline(ACTIVATED_NOTE, ACTIVATED_AT))

    def queue(self) -> None:
        """`plan.yml`'s queued branch: back to Green Light to wait in line."""
        self.epic_lane = epic_cap.GREEN_LIGHT


@pytest.fixture(autouse=True)
def _clear_failures():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _rule(err: str) -> int:
    found = re.search(r"epic-cap: \w+ — rule (\d)", err)
    assert found, err
    return int(found.group(1))


# --------------------------------------------------------------------------- #
# AC1 — the 10-05 order, at the cap                                            #
# --------------------------------------------------------------------------- #

class TestTheTenOhFiveOrderAtTheCap:
    """The sweep reaches the epic first, then the plan run decides."""

    def test_the_sweep_first_promotes_nothing(self):
        board = _Approval()
        assert board.sweep() == 0
        assert [board.lanes[i] for i in WORK] == ["Backlog"] * 3

    def test_then_the_decision_queues_the_epic_and_its_children_stay_in_backlog(self, capsys):
        board = _Approval()
        board.sweep()
        # 15 of 15 in motion besides this one, five waiting: the 10-05 fleet.
        answer, err = board.decide(_fleet(15, waiting=5), capsys)
        assert answer == "queue", err
        assert _rule(err) == 2, err
        board.queue()
        # The next sweep finds the epic back in Green Light, not active.
        assert board.sweep() == 0
        assert [board.lanes[i] for i in WORK] == ["Backlog"] * 3

    def test_the_sweep_says_why_it_held_them(self, capsys):
        board = _Approval()
        board.sweep()
        out = capsys.readouterr().out
        assert f"{WORK[0]} is not being promoted" in out
        assert epic_cap.UNDECIDED_TAG in out
        assert EPIC in out

    def test_the_rule_one_the_race_produced_is_what_the_hold_prevents(self, capsys):
        """Non-vacuity: the same decision over a board where the children did
        get out of Backlog is exactly the 10-05 answer."""
        board = _Approval(lanes={WORK[0]: "Todo", WORK[1]: "Todo", WORK[2]: "Backlog"})
        answer, err = board.decide(_fleet(15, waiting=5), capsys)
        assert (answer, _rule(err)) == ("start", 1)


# --------------------------------------------------------------------------- #
# AC2 — the cap not reached: the epic starts as today                          #
# --------------------------------------------------------------------------- #

class TestUnderTheCapTheEpicStartsAsToday:

    def test_start_then_the_children_promote(self, capsys):
        board = _Approval()
        board.sweep()  # the sweep still arrives first
        answer, err = board.decide(_fleet(9), capsys)
        assert (answer, _rule(err)) == ("start", 4), err
        board.activate()
        promoted = board.sweep()  # the activate step's own --promote-only
        assert promoted == 3
        assert [board.lanes[i] for i in WORK] == ["Todo"] * 3

    def test_a_start_the_sweep_made_from_the_line_releases_them_too(self):
        """A waiting epic the sweep started carries `▶️ epic-started:` — the
        cap's own decision, made by the sweep."""
        board = _Approval()
        board.thread.append(_pipeline(
            epic_cap.started_receipt(1, 15, 15), "2026-10-06T00:30:00.000Z"))
        assert board.sweep() == 3


# --------------------------------------------------------------------------- #
# AC3 — an epic genuinely running before the cap shipped                       #
# --------------------------------------------------------------------------- #

class TestAnEpicRunningBeforeTheCap:
    """Approved 2026-09-20, before the cap; its activation note long gone
    from the thread the sweep reads. One child Done, one In Progress, one
    in Backlog and unblocked."""

    def _board(self):
        return _Approval(
            green_light=PRE_CAP_APPROVAL, minutes_since_approval=60 * 24 * 19,
            lanes={WORK[0]: "Done", WORK[1]: "In Progress", WORK[2]: "Backlog"},
        )

    def test_its_backlog_child_still_promotes(self):
        board = self._board()
        assert board.sweep() == 1
        assert board.lanes[WORK[2]] == "Todo"
        assert not [b for _, b in board.posted if "epic-cap-undecided" in b]

    def test_rule_one_still_starts_it_at_the_cap(self, capsys):
        board = self._board()
        answer, err = board.decide(_fleet(15, waiting=5), capsys)
        assert (answer, _rule(err)) == ("start", 1), err


# --------------------------------------------------------------------------- #
# what counts as a start on record                                             #
# --------------------------------------------------------------------------- #

class TestWhatRecordsTheStart:

    def test_a_note_from_an_earlier_approval_is_not_this_ones(self):
        """Re-approved after a queue: the note must follow THIS approval."""
        board = _Approval(thread=_passed_thread() + [
            _pipeline(ACTIVATED_NOTE, "2026-10-01T00:00:00.000Z")])
        assert board.sweep() == 0

    def test_a_note_nobody_in_the_pipeline_wrote_is_not_a_start(self):
        board = _Approval()
        board.thread.append({"body": ACTIVATED_NOTE, "authored_by_pipeline": False,
                             "created_at": ACTIVATED_AT})
        assert board.sweep() == 0

    def test_an_unreadable_thread_abstains_rather_than_freezes(self):
        """As `reconcile.epic_thread` does: a failed read is not a missing
        start, and holding on it would freeze every epic's children."""
        record = _Approval().record()
        assert epic_cap.promotion_refusal(WORK[0], EPIC, record, None, APPROVED_AT) is None
        assert epic_cap.promotion_refusal(
            WORK[0], EPIC, record, _passed_thread(), APPROVED_AT) is not None

    def test_an_unread_epic_record_abstains_the_epic_gate_holds_it(self):
        assert epic_cap.promotion_refusal(
            WORK[0], EPIC, None, _passed_thread(), APPROVED_AT) is None

    def test_an_unknown_green_light_takes_any_pipeline_start(self):
        record = _Approval().record()
        thread = _passed_thread() + [_pipeline(ACTIVATED_NOTE, ACTIVATED_AT)]
        assert epic_cap.promotion_refusal(WORK[0], EPIC, record, thread, None) is None
        assert epic_cap.promotion_refusal(
            WORK[0], EPIC, record, _passed_thread(), None) is not None

    def test_the_activate_route_writes_the_note_the_gate_reads(self):
        """The note is spelled in `plan.yml` and read here: one string."""
        workflow = (ROOT / ".github" / "workflows" / "plan.yml").read_text(encoding="utf-8")
        assert f'"{epic_cap.ACTIVATED_NOTE} ($KIDS children)' in workflow
        assert ACTIVATED_NOTE.startswith(epic_cap.ACTIVATED_NOTE)


# --------------------------------------------------------------------------- #
# what the card hears                                                          #
# --------------------------------------------------------------------------- #

class TestTheHoldIsSaidOnTheCardOnlyWhenOverdue:

    def test_within_the_decision_window_it_is_logged_not_posted(self):
        board = _Approval(minutes_since_approval=2)
        assert board.sweep() == 0
        assert board.posted == []

    def test_past_the_window_it_is_posted_once_per_child(self):
        board = _Approval(minutes_since_approval=reconcile.POST_CRITIC_GRACE_MINUTES + 1)
        board.sweep()
        board.sweep()
        posted = [b for i, b in board.posted if i == WORK[0]]
        assert len(posted) == 1, posted
        assert posted[0].startswith(f"⏸️ {epic_cap.UNDECIDED_TAG}:")
        assert EPIC in posted[0]
        # The way out is the act the relay reads on an In Progress epic.
        import review_rerun
        assert review_rerun.RERUN_REVIEW_ACT in posted[0]


# --------------------------------------------------------------------------- #
# the start is read off the whole thread, never the fifty-comment window       #
# --------------------------------------------------------------------------- #

def _markers_after_the_start(n: int) -> list[dict]:
    return [_pipeline(f"🔎 plan marker {k}", "2026-10-06T01:00:00.000Z") for k in range(n)]


class TestTheStartIsReadOffTheWholeThread:
    """An activated epic waiting on an upstream epic moves no child, and
    markers pile up on top of its one start note. Once fifty follow it the
    note leaves the window; the hold must still see it (DRE-5639)."""

    @pytest.fixture(autouse=True)
    def _second_critic_passed(self):
        """The second critic's PASS is older than the start, so it leaves the
        window too. Its gate is not this card's — held open, as the sweep's
        other gates are, so the hold under test is the only one standing."""
        with patch.object(reconcile.plan_critic, "promotion_refusal", return_value=None):
            yield

    def _board(self, after: int) -> _Approval:
        board = _Approval()
        board.activate()
        board.thread.extend(_markers_after_the_start(after))
        return board

    def test_a_start_past_the_window_still_releases_the_children(self):
        board = self._board(reconcile.linear_ops.COMMENT_WINDOW + 10)
        # Non-vacuity: the window the sweep's first read gets has no start.
        window = board.comment_records(EPIC)
        assert not epic_cap.start_on_record(window, APPROVED_AT)
        assert board.sweep() == 3
        assert [board.lanes[i] for i in WORK] == ["Todo"] * 3
        assert not [b for _, b in board.posted if epic_cap.UNDECIDED_TAG in b]

    def test_the_whole_thread_is_read_once_per_epic_per_sweep(self):
        board = self._board(reconcile.linear_ops.COMMENT_WINDOW + 10)
        board.sweep()
        assert board.whole_reads == 1

    def test_a_thread_inside_the_window_is_not_read_again(self):
        board = self._board(5)
        assert board.sweep() == 3
        assert board.whole_reads == 0

    def test_an_unreadable_whole_thread_abstains(self):
        window = _passed_thread() + _markers_after_the_start(
            reconcile.linear_ops.COMMENT_WINDOW)

        def fails(epic, **_kw):
            raise RuntimeError("Linear is down")

        with patch.object(reconcile.linear_ops, "comment_records", side_effect=fails):
            assert reconcile.whole_epic_thread(EPIC, window) is None
        assert reconcile.whole_epic_thread(EPIC, None) is None
