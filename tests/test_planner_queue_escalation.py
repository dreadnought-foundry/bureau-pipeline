"""A card waiting in the planner line is not a dead planner (DRE-5177, epic DRE-5167).

THE TRAP. `reconcile.flag_stalled_planning` escalates any Planning card whose
`updatedAt` is `PLANNING_MINUTES` (120) old, saying "planning has produced
nothing". Once the fleet-wide planner cap is live (DRE-5176) a card can sit in
Planning for hours with only its `waiting` planner-slot receipt on it — every
fifth card of a groomer batch — and that rule would park each one in the CEO's
queue as a failure. The same cap puts an approved epic whose activate run was
not admitted `In Progress` with a `waiting` receipt and no second-critic round,
and `post_critic_hold_is_overdue` would stamp "re-run the review" on every
child thirty minutes after the approval.

THE RULE UNDER TEST — the watchdog reads the line before it reads the clock:

  1. in line (`waiting`, or `dispatched` inside the grace) and inside the
     bound: skipped, with one log line naming the card and the word waiting;
  2. in line and past the line's bound (360 minutes): escalated once, through
     the same seam, with the line's own reason;
  3. `claimed`: measured as today;
  4. no receipt: measured as today;

and the hold notice speaks only once the epic's review has started — while the
epic is in line it is logged, and once it is claimed the grace runs from the
claim.

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
BOUND = 360  # the line's bound, read through planner_queue.waiting_max() below


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
    tests/test_planning_hold_escalation.py's board has. `escalate()` is the
    real one, wrapped so a test can count its calls: this stands in for
    Linear, never for the escalation."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.posted: list[tuple[str, str]] = []
        self.states: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []
        self.escalations: list[tuple[str, str]] = []

    def active_cards(self, states=reconcile.SWEEP_STATES):
        return [c for c in self.cards if c["state"]["name"] in states]

    def _find(self, identifier):
        return next(c for c in self.cards if c["identifier"] == identifier)

    def cmd_comment(self, identifier, body, *rest):
        self.posted.append((identifier, body))
        self._find(identifier)["comments"]["nodes"].insert(
            0, {"id": f"new-{len(self.posted)}", "body": body, "createdAt": _iso(0)})

    def cmd_state(self, identifier, lane, *rest):
        self.states.append((identifier, lane))
        self._find(identifier)["state"]["name"] = lane

    def add_label(self, identifier, label):
        self.added.append((identifier, label))

    def lane(self, identifier=CARD) -> str:
        return self._find(identifier)["state"]["name"]

    def run(self, fn):
        real = planning_escalation.escalate

        def counted(linear_ops, identifier, reason, **kw):
            self.escalations.append((identifier, reason))
            return real(linear_ops, identifier, reason, **kw)

        def no_per_card(identifier, *a, **kw):
            raise AssertionError(
                f"the sweep fetched {identifier} one card at a time — the "
                "comments come with the board read (DRE-2929)")

        with patch.object(
            reconcile, "active_cards", side_effect=self.active_cards
        ), patch.object(
            reconcile, "_open_pr_listing", side_effect=lambda: []
        ), patch.object(
            reconcile.planning_escalation, "escalate", side_effect=counted
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


def test_the_bound_is_the_config_files():
    """The numbers every test below is written around, read through the one
    reader the card is allowed to use."""
    assert planner_queue.waiting_max() == BOUND
    assert reconcile.PLANNING_MINUTES == 120


# ===========================================================================
# 1. In line and inside the bound: skipped, whatever updatedAt says
# ===========================================================================
class TestAWaitingCardIsNotAStrand:
    def test_three_hours_in_line_is_not_escalated(self, capsys):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        assert board.watch() == set()
        assert board.escalations == []
        assert board.posted == []
        assert board.states == []
        assert board.lane() == "Planning"
        out = capsys.readouterr().out
        line = next(l for l in out.splitlines() if CARD in l and "waiting" in l)
        assert "not a strand" in line
        assert "place 3 of 7" in line, "the place is read off its receipt"
        assert "180 minutes" in line, "the wait is named"

    def test_no_planning_escalation_comment_is_posted(self):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        board.watch()
        assert not any(planning_escalation.ESCALATION_TAG in b for _, b in board.posted)

    def test_the_skip_is_logged_once_per_card_per_sweep(self, capsys):
        board = _Board(_card(minutes_stale=180, bodies=[_waiting(180)]))
        board.watch()
        out = capsys.readouterr().out
        hits = [l for l in out.splitlines()
                if CARD in l and "waiting for a planner slot" in l]
        assert len(hits) == 1, out

    def test_359_minutes_is_still_inside_the_bound(self):
        board = _Board(_card(minutes_stale=BOUND - 1, bodies=[_waiting(BOUND - 1)]))
        assert board.watch() == set()
        assert board.escalations == []
        assert board.lane() == "Planning"


# ===========================================================================
# 2. In line and past the bound: escalated once, with the line's own reason
# ===========================================================================
class TestAWaitPastTheBoundIsEscalated:
    def test_361_minutes_is_escalated_once_with_the_lines_reason(self):
        board = _Board(_card(minutes_stale=BOUND + 1, bodies=[_waiting(BOUND + 1)]))
        assert board.watch() == {CARD}
        reasons = board.reasons()
        assert len(reasons) == 1, reasons
        reason = reasons[0]
        assert "about 6 hours" in reason, reason
        assert "waiting in line for a planner" in reason
        assert reason.endswith(
            "Sending it back through Planning gives it a fresh place in line.")
        assert "planning has produced nothing" not in reason
        assert planning_escalation.refusal(reason) is None
        assert board.states == [(CARD, reconcile.ESCALATED_STATE)]
        assert reconcile.ESCALATED_STATE == "Green Light"

    def test_the_whole_reason_reaches_the_card(self):
        board = _Board(_card(minutes_stale=BOUND + 1, bodies=[_waiting(BOUND + 1)]))
        board.watch()
        posted = [b for i, b in board.posted if i == CARD]
        assert len(posted) == 1
        assert "waiting in line for a planner" in posted[0]
        assert planning_escalation.NOT_PLAIN_ENGLISH not in posted[0]

    def test_the_once_ever_marker_holds_on_the_next_pass(self):
        """The WATCHDOG_TAG comment is present: the card is not escalated
        again, however long it has waited."""
        board = _Board(_card(minutes_stale=BOUND + 60,
                             bodies=[_waiting(BOUND + 60), _watchdog_receipt()]))
        assert board.watch() == set()
        assert board.escalations == []
        assert board.states == []

    def test_a_dispatch_inside_the_grace_measures_from_the_line_entry(self):
        """Dispatched five minutes ago — in line by the foundation card's rule —
        and its line entry is past the bound. The dispatch bumped updatedAt,
        which is exactly why the line's clock and not the stall clock decides."""
        bodies = [_waiting(BOUND + 30), _receipt("dispatched", 5)]
        assert planner_queue.in_line(bodies)
        board = _Board(_card(minutes_stale=5, bodies=bodies))
        assert board.watch() == {CARD}
        (reason,) = board.reasons()
        assert "waiting in line for a planner" in reason
        assert board.states == [(CARD, reconcile.ESCALATED_STATE)]


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
    @pytest.mark.parametrize("wait", [180, BOUND + 60])
    def test_a_held_card_is_left_alone(self, wait, capsys):
        board = _Board(_card(labels=(reconcile.HOLD_LABEL,), minutes_stale=wait,
                             bodies=[_waiting(wait)]))
        assert board.watch() == set()
        assert board.escalations == []
        assert "waiting for a planner slot" not in capsys.readouterr().out

    @pytest.mark.parametrize("wait", [180, BOUND + 60])
    def test_a_hand_built_card_is_left_alone(self, wait, capsys):
        board = _Board(_card(labels=(reconcile.HAND_BUILT_LABEL,),
                             minutes_stale=wait, bodies=[_waiting(wait)]))
        assert board.watch() == set()
        assert board.escalations == []
        out = capsys.readouterr().out
        assert reconcile.HAND_BUILT_LABEL in out
        assert "waiting for a planner slot" not in out

    @pytest.mark.parametrize("wait", [180, BOUND + 60])
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
    """What rule 2 leaves behind, or a person's hand move. The watchdog reads
    Planning cards only, and the ledger the sweep's backstop dispatches from
    (`planner_queue.ledger` → `next_in_line`) reads a Green Light card for its
    open claim only — so one pass over one board, holding one fixture, pins
    both readings: nothing escalates it and nothing would dispatch at it."""

    def _green(self):
        return _card(state="Green Light", minutes_stale=BOUND + 60,
                     bodies=[_waiting(BOUND + 60)])

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
                     bodies=[_waiting(BOUND + 180), _receipt("claimed", 121)])
        board = _Board(card)
        assert board.watch() == {CARD}
        assert board.reasons() == [reconcile.stalled_planning_reason()]


# ===========================================================================
# 5. The post-approval hold notice waits for the line too
# ===========================================================================
GREEN_LIT_45 = 45


def _epic_waiting() -> list[str]:
    return [_waiting(40, card=EPIC, trigger="In Progress", place=1, of=2)]


class TestTheHoldNoticeReadsTheLine:
    UNREAD = plan_critic.POST_UNREAD_TAG

    def test_in_line_it_is_logged_not_posted(self, capsys):
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), _epic_waiting()) is False
        out = capsys.readouterr().out
        line = next(l for l in out.splitlines() if EPIC in l)
        assert "waiting" in line

    def test_claimed_ten_minutes_ago_is_inside_the_grace(self):
        bodies = _epic_waiting() + [_receipt("claimed", 10, card=EPIC,
                                             trigger="In Progress")]
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), bodies) is False

    def test_claimed_31_minutes_ago_speaks(self):
        bodies = _epic_waiting() + [_receipt("claimed", 31, card=EPIC,
                                             trigger="In Progress")]
        assert reconcile.post_critic_hold_is_overdue(
            self.UNREAD, _iso(GREEN_LIT_45), bodies) is True

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


class TestThePromotionGateWaitsForTheLine:
    def test_an_epic_in_line_holds_its_child_and_posts_nothing(self, capsys):
        board = _Promotion(_epic_waiting())
        assert board.promote() == 0
        assert board.advanced == []
        assert board.unread_notices() == []
        out = capsys.readouterr().out
        assert any(EPIC in l and "waiting for a planner" in l
                   for l in out.splitlines()), out

    def test_a_claim_31_minutes_old_posts_the_notice_once(self):
        board = _Promotion(_epic_waiting() + [
            _receipt("claimed", 31, card=EPIC, trigger="In Progress")])
        assert board.promote() == 0
        board.promote()
        assert board.advanced == []
        notices = board.unread_notices()
        assert len(notices) == 1, notices
        assert EPIC in notices[0]


# ===========================================================================
# The lane contract says what the sweep now does
# ===========================================================================
def test_the_planning_clause_names_the_line_and_its_bound():
    """A contract that still describes every long wait as a stall describes a
    stall the sweep no longer raises, and misses the one it now does."""
    text = lane_contract.lane("Planning")["clauses"]["exit"]["text"]
    assert "waiting in the planner line is not stalled" in text
    assert "line order" in text
    assert "config/planner-queue.json" in text
    assert "Green Light" in text
    rendered = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
    assert "waiting in the planner line is not stalled" in rendered
