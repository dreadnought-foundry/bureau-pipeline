"""A card waiting in the old planner line is measured by the stall clock alone
(DRE-8618, epic DRE-8539; the rule it replaces was DRE-5177's).

THE RULE THAT WENT. Under the fleet-wide planner cap (DRE-5176) a card could
sit in Planning for hours with only its `waiting` planner-slot receipt on it,
so `reconcile.flag_stalled_planning` read the line before the clock: a card in
line inside the line's bound was skipped, and one past it was parked with the
line's own reason after a `because parked` release. The same line silenced
`post_critic_hold_is_overdue` while an approved epic waited in it.

THE CAP IS GONE (`architecture/decisions/adr-planner-cap-removed.md`). Since
DRE-8617 a plan run claims no slot, and the sweep drains what the line still
holds (`tests/test_planner_queue_sweep.py`). So:

  1. a Planning card whose only recent comment is a `waiting` receipt is
     parked by the stall clock exactly as a card with nothing on it would
     be, with the stall clock's reason and no planner-slot release;
  2. a young one — a receipt posted inside the window bumped `updatedAt` —
     is left alone, by the clock;
  3. `claimed`, or no receipt: measured as before;

and a `waiting` or `claimed` receipt no longer changes when the hold notice
speaks: it is timed from the green light (a held planner is
`tests/test_planner_hold_notices.py`'s).

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_queue_escalation.py -v
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
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import lane_contract  # noqa: E402
import plan_critic  # noqa: E402
import planner_queue  # noqa: E402
import planning_escalation  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

CARD = "DRE-9001"
EPIC = "DRE-9100"
CHILD = "DRE-9101"
#: A wait the old line's bound (360 minutes) would have kept unparked.
LONG = 300


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


@pytest.fixture(autouse=True)
def _pin_valid_slugs(monkeypatch):
    monkeypatch.setattr(
        validate_card, "VALID_SLUGS", {"agent-bureau", "atlas", "bureau-pipeline"}
    )


@pytest.fixture(autouse=True)
def _pin_live_snapshot(monkeypatch):
    monkeypatch.setattr(
        reconcile,
        "live_rail_slugs",
        lambda: frozenset({"agent-bureau", "atlas", "bureau-pipeline"}),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _clean_ledgers():
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _iso(minutes_ago: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(minutes=minutes_ago))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _receipt(state: str, minutes_ago: float, *, card: str = CARD,
             trigger: str = "Planning", run: str | None = None,
             place: int | None = None, of: int | None = None) -> str:
    """A planner-slot receipt, composed by the module that owns the grammar —
    never hand-written here, so a grammar change cannot leave this test
    reading a string nothing posts."""
    return planner_queue.format_receipt(
        state, card=card, run=run or f"run-{state}-{int(minutes_ago)}",
        repo="dreadnought-foundry/agent-bureau", trigger=trigger,
        at=_iso(minutes_ago), place=place, of=of,
    )


def _waiting(minutes_ago: float, **kw) -> str:
    kw.setdefault("place", 3)
    kw.setdefault("of", 7)
    return _receipt("waiting", minutes_ago, **kw)


def _watchdog_receipt() -> str:
    return f"🚨 {reconcile.WATCHDOG_TAG}: planning has produced nothing. Observed: …"


def _card(identifier=CARD, state="Planning", labels=(), minutes_stale=180.0,
          bodies=()):
    """A card as `active_cards` returns it: comments inline, newest first,
    each dated by the receipt's own `at` so the ledger and the watchdog read
    the same clock."""
    nodes = []
    for n, body in enumerate(bodies):
        parsed = planner_queue.parse_receipt(body)
        created = parsed.at if parsed else _iso(minutes_stale)
        nodes.append({"id": f"c-{identifier}-{n}", "body": body, "createdAt": created})
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card waiting its turn for a planner",
        "description": "work",
        "createdAt": _iso(minutes_stale),
        "updatedAt": _iso(minutes_stale),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": list(reversed(nodes))},
    }


class _Board:
    """One Linear board the watchdog reads and writes — the shape
    tests/test_planning_hold_escalation.py's board has. The stall exit
    (`reconcile.escalate_out_of_planning`) is the real one, wrapped so a test
    can count its calls: this stands in for Linear, never for the exit. The
    planner's question seam (`planning_escalation.escalate`) raises if it is
    reached at all — since DRE-5286 the sweep never asks it."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.posted: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []
        self.escalations: list[tuple[str, str]] = []
        self.events: list[tuple[str, str]] = []

    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [c for c in self.cards if c["state"]["name"] in states]

    def _find(self, identifier):
        return next(c for c in self.cards if c["identifier"] == identifier)

    def cmd_comment(self, identifier, body, *rest):
        self.posted.append((identifier, body))
        self.events.append(("comment", identifier))
        self._find(identifier)["comments"]["nodes"].insert(
            0, {"id": f"new-{len(self.posted)}", "body": body, "createdAt": _iso(0)})

    def cmd_state(self, identifier, lane, *rest):
        self.states.append((identifier, lane))
        self.events.append(("state", identifier))
        self._find(identifier)["state"]["name"] = lane

    def add_label(self, identifier, label):
        self.added.append((identifier, label))

    def lane(self, identifier=CARD) -> str:
        return self._find(identifier)["state"]["name"]

    def run(self, fn):
        real = reconcile.escalate_out_of_planning

        def counted(card, reason, *rest, **kw):
            self.escalations.append((card["identifier"], reason))
            return real(card, reason, *rest, **kw)

        def no_question(*a, **kw):
            raise AssertionError(
                "the stall exit called planning_escalation.escalate")

        def no_per_card(identifier, *a, **kw):
            raise AssertionError(
                f"the sweep fetched {identifier} one card at a time — the "
                "comments come with the board read (DRE-2929)")

        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile, "_open_pr_listing", side_effect=lambda: []
        ), patch.object(
            reconcile, "escalate_out_of_planning", side_effect=counted
        ), patch.object(
            planning_escalation, "escalate", side_effect=no_question
        ), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=self.cmd_comment
        ), patch.object(
            reconcile.linear_ops, "cmd_state", side_effect=self.cmd_state
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=self.add_label
        ), patch.object(
            reconcile.linear_ops, "get_issue", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "comment_bodies", side_effect=no_per_card
        ), patch.object(
            reconcile.linear_ops, "count_comments", side_effect=no_per_card
        ):
            return fn()

    def watch(self):
        """The WHOLE watchdog: `flag_stranded` owns both passes."""
        return self.run(reconcile.flag_stranded)

    def reasons(self, identifier=CARD) -> list[str]:
        return [r for i, r in self.escalations if i == identifier]


def test_the_window_is_the_stall_clocks():
    """The number every test below is written around."""
    assert reconcile.PLANNING_MINUTES == 120


# ===========================================================================
# 1 & 2. In line: measured by the stall clock, whatever the receipt says
# ===========================================================================
class TestAWaitingCardIsMeasuredByTheStallClock:
    def test_three_hours_in_line_is_parked_with_the_stall_clocks_reason(self, capsys):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        assert board.watch() == {CARD}
        assert board.reasons() == [reconcile.stalled_planning_reason()]
        assert board.states == [(CARD, reconcile.PARKED_STATE)]
        assert "waiting for a planner slot" not in capsys.readouterr().out

    def test_a_wait_the_old_bound_allowed_is_parked_too(self):
        board = _Board(_card(minutes_stale=LONG, bodies=[_waiting(LONG)]))
        assert board.watch() == {CARD}
        (reason,) = board.reasons()
        assert reason == reconcile.stalled_planning_reason()
        assert "waiting in line for a planner" not in reason

    def test_the_park_posts_no_planner_slot_release(self):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        board.watch()
        posted = [b for i, b in board.posted if i == CARD]
        assert len(posted) == 1, "the stall-park note and nothing else"
        assert posted[0].startswith(f"🧹 {dedupe_dispatch.STALL_PARK_TAG}")
        assert all(planner_queue.parse_receipt(b) is None for b in posted)

    def test_no_planning_escalation_comment_is_posted(self):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        board.watch()
        assert not any(planning_escalation.ESCALATION_TAG in b for _, b in board.posted)

    def test_a_young_card_in_line_is_left_alone_by_the_clock(self):
        """A receipt posted inside the window bumped `updatedAt`: the clock,
        not the line, leaves it alone."""
        bodies = [_waiting(LONG + 30), _receipt("dispatched", 5)]
        assert planner_queue.in_line(bodies)
        board = _Board(_card(minutes_stale=5, bodies=bodies))
        assert board.watch() == set()
        assert board.escalations == []
        assert board.lane() == "Planning"

    def test_the_once_ever_marker_holds_on_the_next_pass(self):
        board = _Board(_card(minutes_stale=LONG + 60,
                             bodies=[_waiting(LONG + 60), _watchdog_receipt()]))
        assert board.watch() == set()
        assert board.escalations == []
        assert board.states == []


# ===========================================================================
# 3 & 4. Claimed, or no receipt at all: measured exactly as today
# ===========================================================================
class TestEverythingElseIsMeasuredAsToday:
    def test_a_claim_121_minutes_old_with_nothing_since_escalates_as_today(self):
        bodies = [_waiting(300), _receipt("claimed", 121)]
        board = _Board(_card(minutes_stale=121, bodies=bodies))
        assert board.watch() == {CARD}
        assert board.reasons() == [reconcile.stalled_planning_reason()]

    def test_a_young_claim_is_left_alone_as_today(self):
        board = _Board(_card(minutes_stale=10, bodies=[_receipt("claimed", 10)]))
        assert board.watch() == set()
        assert board.escalations == []

    def test_no_receipt_and_121_minutes_escalates_as_today(self):
        board = _Board(_card(minutes_stale=121, bodies=[]))
        assert board.watch() == {CARD}
        assert board.reasons() == [reconcile.stalled_planning_reason()]


# ===========================================================================
# The existing skips run first, for both rules
# ===========================================================================
class TestTheExistingSkipsRunFirst:
    @pytest.mark.parametrize("wait", [180, LONG + 60])
    def test_a_held_card_is_left_alone(self, wait, capsys):
        board = _Board(_card(labels=(reconcile.HOLD_LABEL,), minutes_stale=wait,
                             bodies=[_waiting(wait)]))
        assert board.watch() == set()
        assert board.escalations == []
        assert "waiting for a planner slot" not in capsys.readouterr().out

    @pytest.mark.parametrize("wait", [180, LONG + 60])
    def test_a_hand_built_card_is_left_alone(self, wait, capsys):
        board = _Board(_card(labels=(reconcile.HAND_BUILT_LABEL,),
                             minutes_stale=wait, bodies=[_waiting(wait)]))
        assert board.watch() == set()
        assert board.escalations == []
        out = capsys.readouterr().out
        assert reconcile.HAND_BUILT_LABEL in out
        assert "waiting for a planner slot" not in out

    @pytest.mark.parametrize("wait", [180, LONG + 60])
    def test_another_repos_card_is_left_alone(self, wait, capsys):
        board = _Board(_card(labels=("repo:atlas",), minutes_stale=wait,
                             bodies=[_waiting(wait)]))
        assert board.watch() == set()
        assert board.escalations == []
        assert "waiting for a planner slot" not in capsys.readouterr().out


# ===========================================================================
# The state rule 2 leaves behind: a waiting receipt on a Green Light card
# ===========================================================================
class TestAWaitingCardInGreenLight:
    """What the old line's park left behind, or a person's hand move. The watchdog reads
    Planning cards only, and the ledger the sweep's backstop dispatches from
    (`planner_queue.ledger` → `next_in_line`) reads a Green Light card for its
    open claim only — so one pass over one board, holding one fixture, pins
    both readings: nothing escalates it and nothing would dispatch at it."""

    def _green(self):
        return _card(state="Green Light", minutes_stale=LONG + 60,
                     bodies=[_waiting(LONG + 60)])

    def test_the_watchdog_does_not_read_it(self):
        board = _Board(self._green())
        with patch.object(reconcile.planner_queue, "in_line",
                          side_effect=AssertionError("read a Green Light card")):
            assert board.run(reconcile.flag_stalled_planning) == set()
        assert board.escalations == []

    def test_one_sweep_escalates_nothing_and_dispatches_nothing(self):
        board = _Board(self._green())
        assert board.watch() == set()
        assert board.escalations == []
        assert board.posted == []
        assert board.states == []
        assert board.lane() == "Green Light"
        ledger = planner_queue.ledger(board.cards)
        assert ledger.waiting == []
        assert planner_queue.next_in_line(ledger) is None

    def test_the_fixture_is_in_line_when_it_sits_in_planning(self):
        """The guard against a vacuous fixture: the same card in Planning IS
        next in line, so the Green Light answer above is the lane's."""
        card = self._green()
        card["state"]["name"] = "Planning"
        assert planner_queue.next_in_line(planner_queue.ledger([card])).card == CARD

    def test_moved_back_to_planning_with_a_newer_claim_it_is_measured_as_today(self):
        card = _card(minutes_stale=121,
                     bodies=[_waiting(LONG + 180), _receipt("claimed", 121)])
        board = _Board(card)
        assert board.watch() == {CARD}
        assert board.reasons() == [reconcile.stalled_planning_reason()]


# ===========================================================================
# 5. The post-approval hold notice no longer reads the line
# ===========================================================================
GREEN_LIT_45 = 45


def _epic_waiting() -> list[str]:
    return [_waiting(40, card=EPIC, trigger="In Progress", place=1, of=2)]


class TestTheHoldNoticeNoLongerReadsTheLine:
    UNREAD = plan_critic.POST_UNREAD_TAG

    def test_in_line_it_speaks_from_the_green_light(self, capsys):
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), _epic_waiting()) is True
        assert "waiting for a planner slot" not in capsys.readouterr().out

    def test_a_claim_no_longer_restarts_the_grace(self):
        bodies = _epic_waiting() + [_receipt("claimed", 10, card=EPIC,
                                             trigger="In Progress")]
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), bodies) is True
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(10), bodies) is False

    def test_no_receipt_answers_as_today(self):
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), ["an unrelated comment"]) is True
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), []) is True

    @pytest.mark.parametrize("tag", [plan_critic.POST_SENT_BACK_TAG,
                                     plan_critic.POST_DIED_TAG])
    @pytest.mark.parametrize("bodies", [
        None, [], ["🎟️ nothing"],
        [_waiting(40, card=EPIC)],
        [_receipt("claimed", 1, card=EPIC)],
    ])
    def test_a_decision_or_a_death_speaks_at_once(self, tag, bodies):
        assert reconcile.post_critic_hold_is_overdue(
            tag, _iso(GREEN_LIT_45), bodies) is True

    def test_two_arguments_answer_exactly_as_today(self):
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45)) is True
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(10)) is False
        assert reconcile.post_critic_hold_is_overdue(self.UNREAD, None) is True


# --- the same rule through promote_ready ------------------------------------
FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")


def _pipeline_comment(body: str) -> dict:
    return {"body": body, "authored_by_pipeline": True}


def _child() -> dict:
    return {
        "id": f"uuid-{CHILD}",
        "identifier": CHILD,
        "title": "Build piece one",
        "description": "Add it.\n\n## Acceptance criteria\n\n- [ ] it renders\n",
        "createdAt": _iso(60 * 24),  # before the green light: not an addition
        "parent": {"identifier": EPIC, "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": n} for n in
                             ("repo:bureau-pipeline", "agent:engineer")]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"nodes": []},
    }


class _Promotion:
    """`promote_ready` over one Backlog child of an In Progress epic, every
    gate not under test held open — the shape of
    tests/test_reconcile_promotion.py's board."""

    def __init__(self, epic_bodies):
        self.thread = [_pipeline_comment(plan_critic.cycle_marker(EPIC))] + [
            _pipeline_comment(b) for b in epic_bodies]
        self.posted: list[tuple[str, str]] = []
        self.advanced: list[str] = []

    def promote(self):
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), patch.object(
            reconcile, "backlog_children", return_value=[_child()]
        ), patch.object(
            reconcile, "epic_blockers_unmet", return_value=False
        ), patch.object(
            reconcile.mid_epic, "last_green_light",
            return_value=_iso(GREEN_LIT_45),
        ), patch.object(
            reconcile.linear_ops, "comment_records",
            side_effect=lambda epic: self.thread,
        ), patch.object(
            reconcile, "card_state", return_value="Done"
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=lambda *a: None
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda ident, *a: self.advanced.append(ident),
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b: self.posted.append((i, b)),
        ), patch.object(
            reconcile.linear_ops, "count_comments",
            side_effect=lambda i, needle, **kw: sum(
                1 for pi, pb in self.posted if pi == i and needle in pb),
        ):
            return reconcile.promote_ready(active_count=0)

    def unread_notices(self) -> list[str]:
        return [b for i, b in self.posted
                if i == CHILD and plan_critic.POST_UNREAD_TAG in b]


class TestThePromotionGateNoLongerWaitsForTheLine:
    def test_an_epic_in_line_holds_its_child_and_posts_the_notice_once(self, capsys):
        board = _Promotion(_epic_waiting())
        assert board.promote() == 0
        board.promote()
        assert board.advanced == []
        notices = board.unread_notices()
        assert len(notices) == 1, notices
        assert EPIC in notices[0]
        assert "waiting for a planner" not in capsys.readouterr().out


# ===========================================================================
# The lane contract says what the sweep now does
# ===========================================================================
def test_the_planning_clause_no_longer_names_the_line_or_its_bound():
    """A contract that still exempts a card waiting in the planner line
    describes a skip the sweep no longer makes."""
    text = lane_contract.lane("Planning")["clauses"]["exit"]["text"]
    assert "waiting in the planner line is not stalled" not in text
    assert "config/planner-queue.json" not in text
    assert "held by the planners' Linear allowance is not stalled" in text
    rendered = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
    assert "waiting in the planner line is not stalled" not in rendered
    assert "held by the planners' Linear allowance is not stalled" in rendered
