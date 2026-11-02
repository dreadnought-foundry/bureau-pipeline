"""The reconcile sweep drains the old planner line, one way (DRE-8618, epic DRE-8539).

THE CAP IS GONE. Since DRE-8617 a plan run reads the planners' Linear
allowance and claims no slot, so nothing joins the line any more. What the
line still holds is started at once, so no card is stranded when the line
stops being served (`architecture/decisions/adr-planner-cap-removed.md`).

THE PHASE UNDER TEST — `reconcile.serve_planner_line`, full sweeps only, until
DRE-8622 removes it:

  1. every card the ledger finds waiting — `waiting`, or a `dispatched` past
     its grace — recorded against THIS repo is fired through `plan_run.fire`
     on the plan event with its recorded trigger and reason, never
     `sent_by_run`, whatever the cap file says and however many claims are
     open;
  2. a confirmed dispatch is followed by ONE `released … · because
     cap-removed` receipt, which takes the card out of the line, so a second
     pass fires nothing;
  3. nothing is counted: no claim is released (expired or run-gone), no run
     is read on GitHub, and the cap is never asked.

Every receipt here is composed by `planner_queue.format_receipt`, the module
that owns the grammar, so a grammar change cannot leave this suite reading a
string nothing posts.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_queue_sweep.py -v
"""
from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import planner_queue  # noqa: E402
import reconcile  # noqa: E402

THIS = "dreadnought-foundry/agent-bureau"
OTHER = "dreadnought-foundry/portico"
CFG = planner_queue.load()
# The line's bound through its one reader — only planner_queue spells those
# two keys (test_planner_queue.TheCap). The cap is the four slots these
# fixtures were written against (`_four_running`), pinned through the same
# reader by `_pin`: the committed number is TheCap's contract alone, and
# DRE-5326 dropped it to two on 2026-09-30 without changing a rule here.
CAP = 4
TTL = CFG["claim_ttl_minutes"]
#: The cap's own reader, before `_pin` stands it in for the importing suites.
REAL_CAP = planner_queue.cap
GRACE = CFG["dispatched_grace_minutes"]


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", THIS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.delenv("MERGED_CARD", raising=False)
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
    # The cap's reader is pinned for the suites that import this fixture
    # (`test_planner_line_order`); the drain itself never asks it for the
    # number in force, and
    # `test_six_cards_waiting_and_a_cap_of_three_are_all_started_in_one_pass`
    # makes that ask raise to prove so.
    monkeypatch.setattr(planner_queue, "cap", lambda cfg=None, environ=None: CAP)
    ledgers = (reconcile._write_failures, reconcile._read_failures,
               reconcile._stale_defects)
    for ledger in ledgers:
        ledger.clear()
    reconcile.reset_sweep_cards()
    yield
    for ledger in ledgers:
        ledger.clear()


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _iso(minutes_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(minutes=minutes_ago))
            .isoformat().replace("+00:00", "Z"))


def _r(state: str, minutes_ago: float, *, run: str, repo: str = THIS,
       trigger: str = "Planning", reason: str | None = None,
       from_run: str | None = None, because: str | None = None) -> tuple:
    """One receipt as `(minutes ago, fields)`; the card fills in its own id."""
    return (minutes_ago, dict(state=state, run=run, repo=repo, trigger=trigger,
                              reason=reason, from_run=from_run, because=because))


def _card(ident: str, lane: str, *receipts) -> dict:
    """A card as the sweep's board read returns it: the comment window inline,
    NEWEST FIRST, each comment dated by its receipt's own time — and, like the
    real read, no comment id."""
    nodes = []
    for minutes_ago, f in receipts:
        at = _iso(minutes_ago)
        body = planner_queue.format_receipt(
            f["state"], card=ident, run=f["run"], repo=f["repo"],
            trigger=f["trigger"], at=at, reason=f["reason"],
            from_run=f["from_run"], because=f["because"],
            place=1 if f["state"] == "waiting" else None,
            of=1 if f["state"] == "waiting" else None)
        nodes.append({"body": body, "createdAt": at})
    nodes.sort(key=lambda n: n["createdAt"], reverse=True)
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": f"card {ident}",
        "description": "work",
        "updatedAt": _iso(1),
        "state": {"name": lane},
        "labels": {"nodes": [{"name": "agent:planner"}]},
        "children": {"nodes": []},
        "comments": {"nodes": nodes},
    }


def _claimed(ident: str, run: str, *, lane: str = "Planning", minutes_ago=10.0,
             repo: str = THIS) -> dict:
    return _card(ident, lane, _r("claimed", minutes_ago, run=run, repo=repo))


def _waiting(ident: str, minutes_ago: float, *, reason: str | None = None,
             repo: str = THIS, trigger: str = "Planning",
             lane: str = "Planning") -> dict:
    return _card(ident, lane, _r("waiting", minutes_ago, run=f"w-{ident}",
                                 repo=repo, trigger=trigger, reason=reason))


def _four_running(first_run: int = 101) -> list:
    return [_claimed(f"DRE-10{n}", str(first_run + n),
                     lane="In Progress" if n % 2 else "Planning")
            for n in range(4)]


def _three_waiting(first_reason: str | None = "re-run") -> list:
    return [_waiting("DRE-201", 30.0, reason=first_reason),
            _waiting("DRE-202", 20.0),
            _waiting("DRE-203", 10.0)]


class Board:
    """The sweep's board read, behind the `active_cards` seam, recording the
    lanes every call asked for."""

    def __init__(self, cards):
        self.cards = list(cards)
        self.calls: list[tuple] = []

    def active_cards(self, states=None):
        states = tuple(states or reconcile.SWEEP_STATES)
        self.calls.append(states)
        return [c for c in self.cards if c["state"]["name"] in states]

    def outside_calls(self) -> list:
        return [s for s in self.calls if not set(s) <= set(reconcile.SWEPT_LANES)]


class World:
    """Linear's writes, GitHub's run statuses and the dispatch, all recorded."""

    def __init__(self, board: Board, runs: dict, fire_result=(True, "")):
        self.board = board
        self.runs = dict(runs)
        self.fire_result = fire_result
        self.posts: list[tuple] = []
        self.fires: list[tuple] = []
        self.gh_reads: list[tuple] = []

    def cmd_comment(self, ident, body, *a, **kw):
        self.posts.append((ident, body))
        return None

    def gh_actions_read(self, *args):
        self.gh_reads.append(args)
        path = next(a for a in args if "/actions/runs/" in a)
        run = path.rsplit("/", 1)[1]
        return self.runs.get(run)

    def fire(self, card, repo, **kw):
        self.fires.append((card, repo, kw))
        return self.fire_result

    def receipts(self, state: str | None = None) -> list:
        out = []
        for ident, body in self.posts:
            r = planner_queue.parse_receipt(body)
            if r is not None and (state is None or r.state == state):
                out.append((ident, r))
        return out

    def gh_runs_read(self) -> list:
        return [next(a for a in args if "/actions/runs/" in a).rsplit("/", 1)[1]
                for args in self.gh_reads]


@contextlib.contextmanager
def _world(cards, runs=None, fire_result=(True, "")):
    board = Board(cards)
    world = World(board, runs or {}, fire_result)
    with mock.patch.object(reconcile, "active_cards", side_effect=board.active_cards), \
            mock.patch.object(linear_ops, "cmd_comment", side_effect=world.cmd_comment), \
            mock.patch.object(linear_ops, "cmd_state") as state, \
            mock.patch.object(linear_ops, "cmd_advance") as advance, \
            mock.patch.object(reconcile, "gh_actions_read",
                              side_effect=world.gh_actions_read), \
            mock.patch.object(reconcile.plan_run, "fire", side_effect=world.fire), \
            mock.patch.object(reconcile, "escalate_out_of_planning") as escalate:
        world.moves = (state, advance, escalate)
        yield world


def _serve(cards, runs=None, fire_result=(True, "")) -> World:
    with _world(cards, runs, fire_result) as world:
        reconcile.serve_planner_line()
    return world


# The full sweep, every other phase stood down: the backstops and the phases
# that would reach GitHub or read more of the board than this suite fakes.
_STOOD_DOWN = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "flag_unlanded_work", "flag_stranded_fixes", "fix_approved_but_red",
    "retry_dead_fix_runs", "redispatch_standing_verdicts", "recover_limit_deaths",
    "rerun_runner_lost_ci", "resync_desynced_heads", "restart_answered_blockers", "card_dependabot_prs", "review_dependabot_prs",
    "recover_crashed_reviews", "report_fleet_reviewer_outage",
    "check_dependabot_capacity", "settle_repair_cards", "report_intake_depth",
    "close_finished_epics", "promote_ready", "move_hand_built_to_review",
    "report_fix_concurrency", "report_evicted_fix_runs",
)


def _main(cards, runs=None, fire_result=(True, ""), **main_kw):
    """One `reconcile.main(**main_kw)` pass over `cards`. Returns the world,
    the phase spy, the report_break_glass stub (a phase that runs AFTER this
    one), and whether the pass exited red."""
    red = False
    with _world(cards, runs, fire_result) as world, contextlib.ExitStack() as stack:
        for name in _STOOD_DOWN:
            stack.enter_context(mock.patch.object(reconcile, name))
        stack.enter_context(mock.patch.object(reconcile, "flag_stranded",
                                              return_value=set()))
        stack.enter_context(mock.patch.object(reconcile, "repair_frozen_planning_holds",
                                              return_value=set()))
        stack.enter_context(mock.patch.object(reconcile, "report_epic_growth",
                                              return_value=[]))
        stack.enter_context(mock.patch.object(reconcile.rereview_watch, "report"))
        later = stack.enter_context(mock.patch.object(reconcile, "report_break_glass"))
        spy = stack.enter_context(mock.patch.object(
            reconcile, "serve_planner_line",
            side_effect=reconcile.serve_planner_line))
        try:
            reconcile.main(**main_kw)
        except SystemExit:
            red = True
    return world, spy, later, red


def _six_waiting() -> list:
    """Six cards waiting in the old line, each with its own trigger and reason."""
    return [
        _waiting("DRE-201", 60.0, reason="re-run"),
        _waiting("DRE-202", 50.0),
        _waiting("DRE-203", 40.0, reason="approved", trigger="in progress",
                 lane="In Progress"),
        _waiting("DRE-204", 30.0, reason="re-review"),
        _waiting("DRE-205", 20.0, trigger="Triage"),
        _waiting("DRE-206", 10.0, reason="review-retry"),
    ]


def _next_pass(cards, world) -> None:
    """Put every receipt `world` posted onto its card, newest first, as the
    next pass's board read returns it."""
    for ident, body in world.posts:
        card = next(c for c in cards if c["identifier"] == ident)
        card["comments"]["nodes"].insert(0, {"body": body, "createdAt": _iso(0)})


# --------------------------------------------------------------------------- #
# 1 + 2 — every waiting card started at once, then released                    #
# --------------------------------------------------------------------------- #


def test_six_cards_waiting_and_a_cap_of_three_are_all_started_in_one_pass(monkeypatch):
    # The committed cap file says three, read through its one reader.
    assert REAL_CAP(planner_queue.load(), environ={}) == 3

    def no_number_in_force(cfg=None, environ=None):
        # `load` checks the file's own number against its ceiling with an
        # empty environment; only the number IN FORCE is the cap being asked.
        if environ is None:
            raise AssertionError("the drain asked the cap")
        return REAL_CAP(cfg, environ=environ)

    monkeypatch.setattr(planner_queue, "cap", no_number_in_force)
    cards = _six_waiting()
    world, spy, _, red = _main(cards)

    assert spy.call_count == 1
    assert not red
    fired = [(c["identifier"], repo, kw) for c, repo, kw in world.fires]
    assert fired == [
        ("DRE-203", THIS, {"trigger_state": "in progress", "reason": "approved",
                           "event": reconcile.plan_run.PLAN_EVENT}),
        ("DRE-201", THIS, {"trigger_state": "Planning", "reason": "re-run",
                           "event": reconcile.plan_run.PLAN_EVENT}),
        ("DRE-202", THIS, {"trigger_state": "Planning", "reason": None,
                           "event": reconcile.plan_run.PLAN_EVENT}),
        ("DRE-204", THIS, {"trigger_state": "Planning", "reason": "re-review",
                           "event": reconcile.plan_run.PLAN_EVENT}),
        ("DRE-205", THIS, {"trigger_state": "Triage", "reason": None,
                           "event": reconcile.plan_run.PLAN_EVENT}),
        ("DRE-206", THIS, {"trigger_state": "Planning", "reason": "review-retry",
                           "event": reconcile.plan_run.PLAN_EVENT}),
    ], "the line's own order: the approval first, then arrival"
    for (ident, _, _), (record, _, _) in zip(fired, world.fires):
        assert record is next(c for c in cards if c["identifier"] == ident), (
            "fire must get the board read's own card record")
    released = world.receipts("released")
    assert sorted((i, r.because, r.trigger, r.repo) for i, r in released) == [
        ("DRE-201", "cap-removed", "Planning", THIS),
        ("DRE-202", "cap-removed", "Planning", THIS),
        ("DRE-203", "cap-removed", "in progress", THIS),
        ("DRE-204", "cap-removed", "Planning", THIS),
        ("DRE-205", "cap-removed", "Triage", THIS),
        ("DRE-206", "cap-removed", "Planning", THIS),
    ], "one release per card"
    assert len(world.posts) == 6, "a release and nothing else"
    assert world.receipts("dispatched") == []

    _next_pass(cards, world)
    for card in cards:
        assert not planner_queue.in_line(card["comments"]["nodes"])
    second, _, _, red = _main(cards)
    assert second.fires == [] and second.posts == []
    assert not red


def test_a_waiting_receipt_with_no_reason_is_fired_with_reason_none():
    world = _serve([_waiting("DRE-202", 20.0)])
    (record, _, kw), = world.fires
    assert record["identifier"] == "DRE-202"
    assert kw == {"trigger_state": "Planning", "reason": None,
                  "event": reconcile.plan_run.PLAN_EVENT}
    assert "sent_by_run" not in kw


def test_an_activate_route_card_is_refired_on_the_activate_route():
    cards = [_waiting("DRE-301", 5.0, trigger="in progress", lane="In Progress",
                      reason="approved")]
    world = _serve(cards)
    assert [(c["identifier"], kw) for c, _, kw in world.fires] == [
        ("DRE-301", {"trigger_state": "in progress", "reason": "approved",
                     "event": reconcile.plan_run.PLAN_EVENT})]


def test_the_drain_returns_the_cards_it_started():
    with _world(_three_waiting()):
        assert reconcile.serve_planner_line() == ["DRE-201", "DRE-202", "DRE-203"]


# --------------------------------------------------------------------------- #
# 3 — nothing is counted                                                       #
# --------------------------------------------------------------------------- #


def test_open_claims_neither_block_the_drain_nor_are_released():
    """Four claims open — the old cap full — and every waiting card starts.
    No run is read on GitHub and no claim is released, however its run
    stands: the run-gone check went with the count."""
    cards = _four_running() + _three_waiting()
    world = _serve(cards, {"101": "completed", "102": "in_progress"})
    assert [c["identifier"] for c, _, _ in world.fires] == [
        "DRE-201", "DRE-202", "DRE-203"]
    assert world.gh_reads == []
    assert {i for i, _ in world.receipts("released")} == {
        "DRE-201", "DRE-202", "DRE-203"}


def test_an_expired_claim_is_not_released_any_more():
    cards = [_claimed("DRE-900", "900", repo=OTHER, minutes_ago=TTL + 5),
             _claimed("DRE-901", "901", minutes_ago=TTL + 5)]
    world = _serve(cards, {"901": "completed"})
    assert world.posts == []
    assert world.gh_reads == []


def test_a_lost_dispatch_past_the_grace_is_started():
    card = _card("DRE-600", "Planning",
                 _r("waiting", 40.0, run="w-600", reason="re-run"),
                 _r("dispatched", GRACE + 5, run="fin-1", reason="re-run"))
    world = _serve([card])
    assert [(c["identifier"], kw) for c, _, kw in world.fires] == [
        ("DRE-600", {"trigger_state": "Planning", "reason": "re-run",
                     "event": reconcile.plan_run.PLAN_EVENT})]
    assert [(i, r.because) for i, r in world.receipts("released")] == [
        ("DRE-600", "cap-removed")]


def test_a_dispatch_inside_its_grace_is_left_for_the_next_pass():
    """A `dispatched` inside the grace is a dispatch on its way, not a card
    waiting: the drain leaves it, and starts it once the grace has passed."""
    card = _card("DRE-610", "Planning",
                 _r("waiting", 40.0, run="w-610"),
                 _r("dispatched", 0.2, run="fin-2"))
    world = _serve([card])
    assert world.fires == [] and world.posts == []


def test_another_repos_waiting_card_is_never_dispatched(capsys):
    world = _serve([_waiting("DRE-910", 30.0, repo=OTHER)])
    assert world.fires == []
    assert world.posts == []
    out = capsys.readouterr().out
    assert any("DRE-910" in line and OTHER in line for line in out.splitlines())


def test_a_green_light_waiting_card_is_not_dispatched():
    world = _serve([_waiting("DRE-530", 15.0, lane="Green Light")])
    assert world.fires == [] and world.posts == []


def test_a_released_card_is_not_in_line_any_more():
    """The release the drain posts is about the CARD, as a park's is
    (`planner_queue.CARD_RELEASES`), though it closes no claim: a sweep's
    run never held one."""
    world = _serve([_waiting("DRE-701", 30.0)])
    (ident, body), = world.posts
    receipt = planner_queue.parse_receipt(body)
    assert (ident, receipt.state, receipt.because) == ("DRE-701", "released",
                                                       "cap-removed")
    nodes = [{"body": body, "createdAt": _iso(0)}] + _waiting(
        "DRE-701", 30.0)["comments"]["nodes"]
    assert not planner_queue.in_line(nodes)
    assert planner_queue.ledger([_card_with(nodes, "DRE-701")]).waiting == []


def _card_with(nodes: list, ident: str) -> dict:
    card = _waiting(ident, 30.0)
    card["comments"] = {"nodes": nodes}
    return card


# --------------------------------------------------------------------------- #
# failure                                                                      #
# --------------------------------------------------------------------------- #


def test_a_failed_dispatch_posts_no_receipt_and_goes_on_the_write_ledger():
    cards = _three_waiting()
    world, _, _, red = _main(cards, fire_result=(False, "dispatch refused rc=1"))
    assert len(world.fires) == 1, "a failed dispatch ends the drain for the pass"
    assert world.posts == []
    assert "dispatch refused rc=1" in reconcile._write_failures
    assert red


def test_a_release_that_does_not_post_goes_on_the_write_ledger():
    with _world(_three_waiting()[:1]) as world, mock.patch.object(
            linear_ops, "cmd_comment",
            side_effect=linear_ops.LinearError("refused")):
        assert reconcile.serve_planner_line() == ["DRE-201"]
    assert len(world.fires) == 1
    assert any("DRE-201" in f and "cap-removed" in f
               for f in reconcile._write_failures)


def test_a_malformed_cap_file_is_loud_and_the_rest_of_the_sweep_runs(
        tmp_path, monkeypatch, capsys):
    bad = tmp_path / "planner-queue.json"
    bad.write_text("{not json")
    monkeypatch.setenv(planner_queue.CONFIG_ENV, str(bad))
    world, spy, later, red = _main(_three_waiting())
    assert spy.call_count == 1
    assert world.fires == [] and world.posts == []
    errors = [line for line in capsys.readouterr().out.splitlines()
              if line.startswith("::error::")]
    assert errors and str(bad) in errors[0]
    assert any(str(bad) in f for f in reconcile._read_failures)
    later.assert_called_once()
    assert red


# --------------------------------------------------------------------------- #
# full sweeps only                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["promote_only", "close_only"])
def test_the_event_paths_never_read_or_serve_the_line(mode, capsys):
    cards = _four_running() + _three_waiting()
    world, spy, _, _ = _main(cards, {"101": "completed"}, **{mode: True})
    spy.assert_not_called()
    assert world.board.outside_calls() == []
    assert world.fires == [] and world.posts == []
    assert "serve_planner_line" not in capsys.readouterr().out


def test_the_phase_runs_after_the_stranded_watchdog():
    order = []
    with _world(_three_waiting()), contextlib.ExitStack() as stack:
        for name in _STOOD_DOWN:
            stack.enter_context(mock.patch.object(reconcile, name))
        stack.enter_context(mock.patch.object(
            reconcile, "flag_stranded",
            side_effect=lambda: order.append("flag_stranded") or set()))
        stack.enter_context(mock.patch.object(reconcile, "repair_frozen_planning_holds",
                                              return_value=set()))
        stack.enter_context(mock.patch.object(reconcile, "report_epic_growth",
                                              return_value=[]))
        stack.enter_context(mock.patch.object(reconcile.rereview_watch, "report"))
        stack.enter_context(mock.patch.object(reconcile, "report_break_glass"))
        stack.enter_context(mock.patch.object(
            reconcile, "serve_planner_line",
            side_effect=lambda *a, **k: order.append("serve_planner_line")))
        with contextlib.suppress(SystemExit):
            reconcile.main()
    assert order == ["flag_stranded", "serve_planner_line"]


def test_a_card_escalated_this_pass_is_not_served(capsys):
    cards = _three_waiting()
    with _world(cards) as world:
        reconcile.serve_planner_line(skip={"DRE-201"})
    assert [c["identifier"] for c, _, _ in world.fires] == ["DRE-202", "DRE-203"]
    assert any("DRE-201" in line for line in capsys.readouterr().out.splitlines())
