"""RED-first: the sweep reads a promoted card that has not started in In
Progress (as well as Todo, until DRE-7660 deleted it), re-fires its dispatch as a build, and requeues a
dead run to Queued (DRE-7108, epic: the Todo retirement).

Until this card the `state == "Todo" and not is_open` branch of `main()`'s
nudge loop was what rescued a promoted card nobody built. Once the flip sends a
promotion to `In Progress`, the same loop's no-PR branch would read that card
as a dead run after 60 minutes and requeue it with a `🪦` death on its count.
So the loop asks one question first, of every card in
`lane_contract.START_LANES`: does it carry a start receipt (the build run's
`🧠 model-attempt` comment) newer than its newest entry into its lane?

WHAT IS UNDER TEST:
  1. NOT STARTED — a card in a start lane with no start receipt is left
     alone before `START_MINUTES`, then re-dispatched once when no run is in
     flight and none is held on the runner line, and never gains a `🪦`
     dead-run comment. A card that HAS started keeps today's In Progress path
     (liveness, rescue, dead-run count) at its 60-minute window.
  2. HELD — a card whose newest runner-line comment is `⏸️ runner-line: held`
     and younger than seven hours is alive: not re-fired, no `🪦`, named on a
     `start-window:` line. Released, dropped, or a hold eight hours old re-fire.
  3. THE RE-FIRE IS A BUILD — `plan_run.fire` is passed `agent-execute`, never
     the event the labels would pick, so a one-off wearing `agent:planner` is
     not sent to the planner.
  4. A DEAD RUN IS REQUEUED TO `Queued`, and `promote_ready` promotes it again
     on the next pass under the same gates.
  5. Under a fixture contract where FLEET's destination is `In Progress`,
     `promote_ready` writes it, counts it in the cap, and refuses exactly what
     it refuses today; WORKBENCH and OPERATOR still go to Hand-work.
  6. No bare `Todo` string constant is left in `reconcile.py` outside
     `carry_epics_out_of_todo`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_promoted_not_started.py -v
"""
from __future__ import annotations

import ast
import copy
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import planning_shape  # noqa: E402
import plan_run  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

import test_operator_card_promotion as promo  # noqa: E402

CARD = "DRE-7108"
NOW = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
START = lane_contract.START_MINUTES
#: The start lanes the sweep reads a card in: the live ones. Todo, the
#: retiring one (DRE-7129), was drained instead until DRE-7660 deleted it.
LIVE_START_LANES = tuple(
    lane for lane in lane_contract.START_LANES if lane in lane_contract.lane_names()
)
DEAD_WINDOW = reconcile.STALE_MINUTES["In Progress"]
REDISPATCH_RECEIPT = (
    "🧹 Reconcile: card was promoted and no run started — re-dispatched."
)


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _age(iso: str, now: str | None = None) -> float:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (NOW - then).total_seconds() / 60


START_RECEIPT = "🧠 model-attempt: claude-opus-5-5 — engineer agent starting"


def _held(place="place 2 of 3"):
    return f"⏸️ runner-line: held · {place} · 2026-10-17 10:00 PT"


RELEASED = "▶️ runner-line: released · 2026-10-17 11:00 PT"
DROPPED = "⏹️ runner-line: dropped · 2026-10-17 11:00 PT"


def _card(state, *, comments=(), idle=30, identifier=CARD, labels=("agent:engineer",)):
    """A card as the board read hands it to the nudge loop. `comments` is
    `(body, minutes_ago)` pairs in any order; the window is laid out the way
    the API sends it, newest first."""
    nodes = sorted(
        ({"body": b, "createdAt": _iso(m), "user": None} for b, m in comments),
        key=lambda n: n["createdAt"], reverse=True,
    )
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "portico: a card the sweep promoted",
        "description": "**Repo:** portico\nwork",
        "createdAt": _iso(24 * 60),
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": []},
        "updatedAt": _iso(idle),
        "comments": {"nodes": nodes},
    }


@pytest.fixture(autouse=True)
def _clean_sweep_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/portico")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile.reset_sweep_cards()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile.reset_sweep_cards()


class _Sweep:
    """`reconcile.main()` over a board of cards with every phase that is not
    the nudge loop stubbed, and every write recorded."""

    def __init__(self, *cards, entered=None, refusal="", alive=False, dead=0):
        self.cards = list(cards)
        # identifier → minutes since it entered its current lane
        self.entered = entered or {}
        self.refusal = refusal
        self.alive = alive
        self.dead = dead
        self.moves: list[tuple] = []
        self.comments: list[tuple[str, str]] = []
        self.fires: list[tuple[str, str | None]] = []
        self.history_reads: list[str] = []

    def _history(self, identifier, first=50):
        self.history_reads.append(identifier)
        card = next(c for c in self.cards if c["identifier"] == identifier)
        lane = card["state"]["name"]
        minutes = self.entered.get(identifier, 120)
        return [{"createdAt": _iso(minutes), "toState": {"name": lane}},
                {"createdAt": _iso(minutes + 60), "toState": {"name": "Queued"}}]

    def _fire(self, card, repo, **kw):
        self.fires.append((card["identifier"], kw.get("event")))
        return True, ""

    def run(self, capsys=None):
        mocks = {
            "unstick_conflicts": MagicMock(),
            "retrigger_dead_heads": MagicMock(),
            "check_dependabot_capacity": MagicMock(),
            "fix_approved_but_red": MagicMock(),
            "retry_dead_fix_runs": MagicMock(),
            "restart_answered_blockers": MagicMock(),
            "redispatch_standing_verdicts": MagicMock(),
            "review_dependabot_prs": MagicMock(),
            "card_dependabot_prs": MagicMock(),
            "close_finished_epics": MagicMock(),
            "promote_ready": MagicMock(return_value=0),
            "age_minutes": MagicMock(side_effect=_age),
            "pr_for": MagicMock(return_value=None),
            "flag_stranded": MagicMock(return_value=set()),
            "active_cards": MagicMock(side_effect=lambda states=reconcile.SWEEP_STATES: [
                c for c in self.cards if c["state"]["name"] in states]),
            "build_run_refusal": MagicMock(return_value=self.refusal),
            "agent_run_alive": MagicMock(return_value=self.alive),
            "redeliver_rescued_work": MagicMock(return_value=False),
        }
        # Every other phase's GitHub and Linear reads answer the way they do
        # offline — refused — without the round trip.
        refused = subprocess.CompletedProcess([], 1, "", "HTTP 401: Bad credentials")
        with patch.multiple(reconcile, **mocks), patch.object(
            subprocess, "run", return_value=refused
        ), patch.object(
            reconcile.linear_ops, "gql",
            side_effect=reconcile.linear_ops.LinearError("Linear API returned 401"),
        ), patch.object(
            reconcile.plan_run, "fire", side_effect=self._fire
        ), patch.object(
            reconcile.linear_ops, "lane_history", side_effect=self._history
        ), patch.object(
            reconcile.linear_ops, "count_comments", return_value=self.dead
        ), patch.object(
            reconcile.linear_ops, "add_label"
        ), patch.object(
            reconcile.linear_ops, "cmd_state",
            side_effect=lambda *a, **kw: self.moves.append(a) or True,
        ), patch.object(
            reconcile.linear_ops, "cmd_advance",
            side_effect=lambda *a, **kw: self.moves.append(("advance",) + a) or True,
        ), patch.object(
            reconcile.linear_ops, "cmd_comment",
            side_effect=lambda i, b, *a, **kw: self.comments.append((i, b)),
        ):
            reconcile.main()
        self.out = capsys.readouterr().out if capsys else ""
        self.mocks = mocks
        return self

    def bodies(self, identifier=CARD):
        return [b for i, b in self.comments if i == identifier]

    def deaths(self, identifier=CARD):
        return [b for b in self.bodies(identifier) if b.startswith("🪦")]

    def refired(self, identifier=CARD):
        return [f for f in self.fires if f[0] == identifier]


# --------------------------------------------------------------------------
# 1: promoted, not started — in either start lane
# --------------------------------------------------------------------------
class TestNotStarted:
    def test_the_start_lanes_are_the_contracts(self):
        assert set(lane_contract.START_LANES) == {"In Progress"}

    def test_no_start_lane_is_left_to_drain(self):
        # Todo was the retiring start lane (DRE-7129) and its entry is gone
        # (DRE-7660): every start lane is live, none is drained, and a card
        # in the deleted lane is neither watched nor moved.
        assert LIVE_START_LANES == lane_contract.START_LANES == ("In Progress",)
        assert not set(reconcile.RETIRING_LANES) & set(lane_contract.START_LANES)
        sweep = _Sweep(_card("Todo", idle=START + 15)).run()
        assert sweep.fires == []
        assert sweep.moves == []

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_left_alone_before_the_start_window(self, lane):
        sweep = _Sweep(_card(lane, idle=START - 5)).run()
        assert sweep.fires == []
        assert sweep.moves == []
        assert sweep.bodies() == []

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_redispatched_once_after_the_window_when_nothing_is_in_flight(self, lane):
        """In Progress at 30 minutes is inside its own 60-minute window: what
        acts is the start window, because nothing ever started."""
        sweep = _Sweep(_card(lane, idle=START + 15)).run()
        assert sweep.refired() == [(CARD, plan_run.EXECUTE_EVENT)]
        assert sweep.bodies() == [REDISPATCH_RECEIPT]
        assert sweep.moves == [], "a not-started card changes no lane"

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_left_alone_while_a_run_is_queued(self, lane):
        sweep = _Sweep(_card(lane, idle=START + 15),
                       refusal="Agent Task run 1 for DRE-7108 is queued").run()
        assert sweep.fires == []
        assert sweep.bodies() == []
        assert sweep.moves == []

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_never_a_dead_run_however_long_it_sits(self, lane):
        """Past the In Progress window, with no live run: the old branch
        requeued this card and counted a death. It never started."""
        sweep = _Sweep(_card(lane, idle=DEAD_WINDOW * 3)).run()
        assert sweep.deaths() == []
        assert sweep.moves == []
        assert sweep.refired() == [(CARD, plan_run.EXECUTE_EVENT)]

    def test_a_start_receipt_older_than_the_lane_entry_is_not_a_start(self):
        """A prior attempt's receipt says nothing about this trip through the
        lane: the card re-entered after it."""
        card = _card("In Progress", idle=START + 15,
                     comments=[(START_RECEIPT, 300)])
        sweep = _Sweep(card, entered={CARD: 60}).run()
        assert sweep.refired() == [(CARD, plan_run.EXECUTE_EVENT)]
        assert sweep.deaths() == []

    def test_a_card_with_no_start_receipt_costs_no_history_read(self):
        """No `🧠` at all answers the question without the lane's entry time,
        so the common case is read off the board read alone."""
        sweep = _Sweep(_card("In Progress", idle=START + 15)).run()
        assert sweep.history_reads == []

    def test_an_unreadable_lane_history_acts_on_nothing_and_goes_red(self):
        card = _card("In Progress", idle=DEAD_WINDOW * 2,
                     comments=[(START_RECEIPT, 30)])
        sweep = _Sweep(card)

        def boom(identifier, first=50):
            raise reconcile.linear_ops.LinearError("linear 500")

        sweep._history = boom
        with pytest.raises(SystemExit):
            sweep.run()
        assert sweep.fires == [] and sweep.moves == [] and sweep.bodies() == []
        assert reconcile._read_failures


class TestStartedKeepsTodaysPath:
    def _started(self, idle):
        return _card("In Progress", idle=idle,
                     comments=[(START_RECEIPT, idle + 5)])

    def test_inside_the_dead_run_window_nothing_happens(self):
        sweep = _Sweep(self._started(START + 15), entered={CARD: START + 25}).run()
        assert sweep.fires == [] and sweep.moves == [] and sweep.bodies() == []

    def test_a_live_run_is_left_alone(self):
        sweep = _Sweep(self._started(DEAD_WINDOW + 30),
                       entered={CARD: DEAD_WINDOW + 40}, alive=True).run()
        assert sweep.fires == [] and sweep.moves == [] and sweep.bodies() == []

    def test_a_dead_run_is_requeued_to_the_queue_and_counted(self):
        sweep = _Sweep(self._started(DEAD_WINDOW + 30),
                       entered={CARD: DEAD_WINDOW + 40}, dead=0).run()
        assert sweep.fires == [], "a started card is requeued, never re-fired"
        assert [m[:2] for m in sweep.moves] == [(CARD, lane_contract.QUEUE_LANE)]
        deaths = sweep.deaths()
        assert len(deaths) == 1
        assert f"Requeued to {lane_contract.QUEUE_LANE}" in deaths[0]
        assert "Todo" not in deaths[0]
        assert "(dead run 1/" in deaths[0]

    def test_the_rescue_is_asked_before_the_requeue(self):
        sweep = _Sweep(self._started(DEAD_WINDOW + 30), entered={CARD: DEAD_WINDOW + 40})
        sweep.run()
        sweep.mocks["redeliver_rescued_work"].assert_called_once()

    def test_the_requeue_is_not_rerouted_to_todo(self):
        """linear_ops' building-card reroute (DRE-1885) sends an In Progress →
        queue move to Todo unless the caller says the move is deliberate; the
        requeue is, and `promote_ready` brings the card back."""
        sweep = _Sweep(self._started(DEAD_WINDOW + 30),
                       entered={CARD: DEAD_WINDOW + 40}).run()
        assert "--park" in sweep.moves[0]

    def test_at_the_cap_the_planner_gets_it_as_today(self):
        sweep = _Sweep(self._started(DEAD_WINDOW + 30),
                       entered={CARD: DEAD_WINDOW + 40},
                       dead=reconcile.REQUEUE_CAP).run()
        assert ("advance", CARD, "Planning", "In Progress") in sweep.moves


# --------------------------------------------------------------------------
# 2: held on the runner line
# --------------------------------------------------------------------------
class TestHeldOnTheRunnerLine:
    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_a_card_held_three_hours_ago_is_alive(self, lane, capsys):
        card = _card(lane, idle=START + 15, comments=[(_held(), 180)])
        sweep = _Sweep(card).run(capsys)
        assert sweep.fires == []
        assert sweep.deaths() == []
        assert sweep.bodies() == []
        lines = [ln for ln in sweep.out.splitlines() if ln.startswith("start-window:")]
        assert lines == [
            f"start-window: {CARD} held on the runner line since "
            f"{reconcile._pt(_iso(180))}"
        ]

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    @pytest.mark.parametrize("newest", [RELEASED, DROPPED])
    def test_released_or_dropped_is_refired_once(self, lane, newest):
        card = _card(lane, idle=START + 15,
                     comments=[(_held(), 180), (newest, 60)])
        sweep = _Sweep(card).run()
        assert sweep.refired() == [(CARD, plan_run.EXECUTE_EVENT)]

    @pytest.mark.parametrize("lane", LIVE_START_LANES)
    def test_a_hold_eight_hours_old_is_refired_once(self, lane):
        card = _card(lane, idle=START + 15, comments=[(_held(), 8 * 60)])
        sweep = _Sweep(card).run()
        assert sweep.refired() == [(CARD, plan_run.EXECUTE_EVENT)]

    def test_the_newest_by_created_at_decides(self):
        """A hold posted after a release is the standing word, wherever the
        two sit in the window."""
        card = _card("In Progress", idle=START + 15,
                     comments=[(RELEASED, 240), (_held(), 120)])
        sweep = _Sweep(card).run()
        assert sweep.fires == []

    def test_the_hold_is_asked_after_the_run_listing(self):
        """Liveness reads the run listing first; a held card with a queued run
        is still left alone, and the queue is what says so."""
        card = _card("In Progress", idle=START + 15, comments=[(_held(), 180)])
        sweep = _Sweep(card, refusal="run 1 is queued").run()
        assert sweep.fires == []

    def test_several_held_cards_on_one_pass_refire_none(self, capsys):
        cards = [
            _card(lane, identifier=f"DRE-71{n}{i}", idle=START + 15,
                  comments=[(_held(f"place {n + 1} of 4"), 60 + n)])
            for n, lane in enumerate(LIVE_START_LANES)
            for i in range(2)
        ]
        sweep = _Sweep(*cards).run(capsys)
        assert sweep.fires == []
        assert sweep.comments == []
        held = [ln for ln in sweep.out.splitlines() if ln.startswith("start-window:")]
        assert len(held) == len(cards)

    def test_the_receipt_pattern_is_the_consoles(self):
        assert reconcile.RUNNER_LINE_RE.pattern == (
            r"^(⏸|▶|⏹)️?\s*runner-line:\s*(held|released|dropped)\b"
        )
        assert reconcile.RUNNER_LINE_HELD_MINUTES == 7 * 60


# --------------------------------------------------------------------------
# 3: the re-fire is a build
# --------------------------------------------------------------------------
class TestTheRefireIsABuild:
    def test_three_refires_all_send_agent_execute(self):
        one_off = planning_shape.shape_comment("one-off", "one card, one pull request")
        labelled = _card("In Progress", identifier="DRE-7203", idle=START + 15,
                         labels=("agent:planner",), comments=[(one_off, 600)])
        sweep = _Sweep(
            _card("In Progress", identifier="DRE-7201", idle=START + 15),
            _card("In Progress", identifier="DRE-7202", idle=START + 15),
            labelled,
        ).run()
        assert sorted(sweep.fires) == [
            ("DRE-7201", plan_run.EXECUTE_EVENT),
            ("DRE-7202", plan_run.EXECUTE_EVENT),
            ("DRE-7203", plan_run.EXECUTE_EVENT),
        ]
        assert all(event != plan_run.PLAN_EVENT for _, event in sweep.fires)

    def test_redispatch_names_the_event_outright(self):
        """Through the real `plan_run.fire`: the payload GitHub receives says
        `agent-execute` on a card the label rule would send to the planner."""
        import json

        sent = []

        def fake_run(argv, **kwargs):
            sent.append(json.loads(Path(argv[argv.index("--input") + 1]).read_text()))
            return MagicMock(returncode=0, stdout="", stderr="")

        card = _card("In Progress", labels=("agent:planner",))
        with patch.object(plan_run.subprocess, "run", side_effect=fake_run):
            assert reconcile.redispatch(card) is True
        assert [s["event_type"] for s in sent] == [plan_run.EXECUTE_EVENT]


# --------------------------------------------------------------------------
# 4: a dead run goes to the queue, and the next pass promotes it
# --------------------------------------------------------------------------
class TestTheRequeueIsPromotedAgain:
    def _died(self):
        return _card("In Progress", idle=DEAD_WINDOW + 30,
                     comments=[(promo.FLEET, 2000), (START_RECEIPT, DEAD_WINDOW + 35)])

    def _queued(self, card, sweep):
        """The card as the next pass's Queued read hands it over: in the lane
        the requeue wrote, carrying every comment the pass posted."""
        child = promo._card(identifier=CARD, comments=[promo.FLEET] + sweep.bodies())
        assert sweep.moves[0][1] == lane_contract.QUEUE_LANE
        return child

    def test_two_passes_requeue_then_promote(self):
        first = _Sweep(self._died(), entered={CARD: DEAD_WINDOW + 40}).run()
        assert any(f"Requeued to {lane_contract.QUEUE_LANE}" in b for b in first.deaths())
        board = promo._Board(self._queued(self._died(), first))
        assert board.promote() == 1
        assert board.lane_of(CARD) == routing_verdict.destination("FLEET")
        assert board.receipt_for(CARD).startswith(
            f"🧹 Auto-promoted {lane_contract.QUEUE_LANE} → "
            f"{routing_verdict.destination('FLEET')}: "
        )

    def test_the_second_pass_still_waits_on_the_cap(self):
        first = _Sweep(self._died(), entered={CARD: DEAD_WINDOW + 40}).run()
        with patch.object(reconcile, "MAX_WIP", 1):
            board = promo._Board(self._queued(self._died(), first))
            assert board.promote(active_count=1) == 0
        assert board.lane_of(CARD) == "Backlog"


# --------------------------------------------------------------------------
# 5: FLEET's destination is the dispatch lane
# --------------------------------------------------------------------------
@pytest.fixture
def flipped(monkeypatch):
    """A fixture contract: `build_start.lane` and FLEET's destination are both
    the dispatch lane, the shape the flip card ships."""
    contract = copy.deepcopy(lane_contract.load())
    contract["build_start"]["lane"] = contract["build_start"]["dispatch_lane"]
    assert lane_contract.build_start_problems(contract) == []
    monkeypatch.setattr(lane_contract, "START_LANE", lane_contract.start_lane(contract))
    vocab = copy.deepcopy(routing_verdict.load())
    for route in vocab["verdicts"]:
        if route["name"] == "FLEET":
            route["destination"] = lane_contract.DISPATCH_LANE
    real = routing_verdict.destination
    monkeypatch.setattr(reconcile.routing_verdict, "destination",
                        lambda name, doc=None: real(name, doc or vocab))
    assert routing_verdict.destination("FLEET") == "In Progress"
    return vocab


class TestFleetPromotesIntoTheDispatchLane:
    def test_an_unblocked_child_of_an_approved_epic_goes_to_in_progress(self, flipped):
        board = promo._Board(promo._card(identifier="DRE-1", comments=[promo.FLEET]))
        assert board.promote() == 1
        assert board.lane_of("DRE-1") == "In Progress"
        assert board.receipt_for("DRE-1").startswith(
            "🧹 Auto-promoted Queued → In Progress: ")

    def test_a_blocked_child_is_refused(self, flipped):
        blocked = promo._card(identifier="DRE-2", comments=[promo.FLEET])
        blocked["inverseRelations"] = {"nodes": [
            {"type": "blocks", "issue": {"identifier": "DRE-9", "state": {"name": "In Progress"}}}
        ]}
        board = promo._Board(blocked)
        with patch.object(reconcile, "card_state", return_value="In Progress"):
            assert board.promote() == 0
        assert board.lane_of("DRE-2") == "Backlog"

    def test_one_over_the_cap_is_refused(self, flipped):
        with patch.object(reconcile, "MAX_WIP", 1):
            board = promo._Board(
                promo._card(identifier="DRE-1", comments=[promo.FLEET]),
                promo._card(identifier="DRE-2", comments=[promo.FLEET]),
            )
            assert board.promote(active_count=0) == 1
        assert board.lane_of("DRE-1") == "In Progress"
        assert board.lane_of("DRE-2") == "Backlog"

    def test_an_unapproved_epics_child_is_refused(self, flipped):
        board = promo._Board(promo._card(identifier="DRE-3", parent_state="Green Light",
                                         comments=[promo.FLEET]))
        assert board.promote() == 0
        assert board.lane_of("DRE-3") == "Backlog"

    @pytest.mark.parametrize("verdict", [promo.WORKBENCH, promo.OPERATOR])
    def test_a_persons_card_still_goes_to_hand_work(self, flipped, verdict):
        board = promo._Board(promo._card(identifier="DRE-4", comments=[verdict]))
        assert board.promote() == 1
        assert board.lane_of("DRE-4") == "Hand-work"

    def test_the_wip_count_is_todays_for_the_same_board(self):
        """The cap counts a card the same in either start lane, so a board
        whose promoted card sits in In Progress counts what it counted when
        the card sat in Todo — before the promotion and after it."""
        before = [_card("In Progress", identifier="DRE-1"),
                  _card("In Review", identifier="DRE-2")]
        today = before + [_card("Todo", identifier="DRE-3")]
        flipped_board = before + [_card("In Progress", identifier="DRE-3")]
        with patch.object(reconcile, "repo_epics", return_value=set()):
            count = lambda b: reconcile.wip_count(reconcile.wip_base(b))  # noqa: E731
            assert count(before) == 2
            assert count(flipped_board) == count(today) == 3


# --------------------------------------------------------------------------
# 6: the module names no lane `Todo` of its own
# --------------------------------------------------------------------------
class TestNoTodoLiteral:
    def test_no_bare_todo_constant_outside_the_epic_carry(self):
        tree = ast.parse((ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8"))
        exempt = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "carry_epics_out_of_todo":
                exempt |= {id(n) for n in ast.walk(node)}
        found = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == "Todo"
            and id(node) not in exempt
        ]
        assert found == [], f"bare 'Todo' constants at lines {found}"

    def test_the_lane_tuples_read_the_start_lanes(self):
        assert reconcile.START_WINDOW_MINUTES == lane_contract.START_MINUTES
        for lane in LIVE_START_LANES:
            assert lane in reconcile.WATCHDOG_LANES
            assert lane in reconcile.INTAKE_BLOCKED_LANES
        # The deleted one is never watched (DRE-7129, DRE-7660).
        assert "Todo" not in reconcile.WATCHDOG_LANES

    def test_the_redispatch_note_names_no_lane(self):
        assert reconcile._TODO_REDISPATCH_NOTE == (
            "card was promoted and no run started — re-dispatched"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
