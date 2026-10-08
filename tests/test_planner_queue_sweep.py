"""The reconcile sweep is the planner line's backstop (DRE-5178, epic DRE-5167).

THE GAP. The end-of-run dispatch in `plan.yml` is the planner line's fast
path, and it does not run when a runner is killed, when a job hits its
timeout (its `always()` steps are skipped), or when the finishing run cannot
mint a token for the waiting card's owner. A card can then wait in a line
with a free slot and nothing coming to serve it.

THE PHASE UNDER TEST — `reconcile.serve_planner_line`, full sweeps only:

  1. release dead claims: an open claim past the TTL `because expired`,
     fleet-wide, with no GitHub read; an open claim of THIS repo whose run
     GitHub reports `completed` `because run-gone`; an unreadable status
     keeps the claim;
  2. dispatch into free slots, earliest card in line first, this repo's
     cards only, through `plan_run.fire` with the card's recorded trigger
     and reason and never `sent_by_run` — and a `dispatched` receipt only on
     a confirmed dispatch;
  3. print the line's depth once, as a `::warning::` once the oldest card
     has waited over half the line's bound, and never escalate.

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
GRACE = CFG["dispatched_grace_minutes"]
BOUND = planner_queue.waiting_max()


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", THIS)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.delenv("MERGED_CARD", raising=False)
    monkeypatch.delenv(planner_queue.CONFIG_ENV, raising=False)
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


def _depth_lines(out: str) -> list[str]:
    return [line for line in out.splitlines()
            if line.removeprefix("::warning::").startswith("planner line: ")
            and " waiting, oldest " in line]


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


# --------------------------------------------------------------------------- #
# 1 + 2 — a run gone, a slot freed, the first card in line served              #
# --------------------------------------------------------------------------- #


def test_one_full_sweep_releases_the_gone_run_and_serves_the_first_card(capsys):
    cards = _four_running() + _three_waiting()
    runs = {"101": "completed", "102": "in_progress", "103": "in_progress",
            "104": "queued"}
    world, spy, _, red = _main(cards, runs)

    assert spy.call_count == 1
    released = world.receipts("released")
    assert [(i, r.run, r.because) for i, r in released] == [
        ("DRE-100", "101", "run-gone")]
    assert len(world.fires) == 1
    record, repo, kw = world.fires[0]
    first = next(c for c in cards if c["identifier"] == "DRE-201")
    assert record is first, "fire must get the board read's own card record"
    assert repo == THIS
    assert kw == {"trigger_state": "Planning", "reason": "re-run",
                  "event": reconcile.plan_run.PLAN_EVENT}
    assert "sent_by_run" not in kw
    dispatched = world.receipts("dispatched")
    assert [(i, r.trigger, r.reason, r.repo) for i, r in dispatched] == [
        ("DRE-201", "Planning", "re-run", THIS)]
    posted_to = {i for i, _ in world.posts}
    assert not posted_to & {"DRE-202", "DRE-203"}, "the other two stay waiting"
    assert not red
    assert _depth_lines(capsys.readouterr().out) == [
        f"planner line: 2 waiting, oldest 20 minutes, 3 running and 1 dispatched of {CAP}"]


def test_a_waiting_receipt_with_no_reason_is_fired_with_reason_none():
    cards = _four_running() + _three_waiting(first_reason=None)
    world = _serve(cards, {"101": "completed", "102": "in_progress",
                           "103": "in_progress", "104": "in_progress"})
    assert len(world.fires) == 1
    record, _, kw = world.fires[0]
    assert record["identifier"] == "DRE-201"
    assert kw == {"trigger_state": "Planning", "reason": None,
                  "event": reconcile.plan_run.PLAN_EVENT}


def test_an_activate_route_card_is_refired_on_the_activate_route():
    cards = [_waiting("DRE-301", 5.0, trigger="in progress", lane="In Progress",
                      reason="approved")]
    world = _serve(cards)
    assert [(c["identifier"], kw) for c, _, kw in world.fires] == [
        ("DRE-301", {"trigger_state": "in progress", "reason": "approved",
                     "event": reconcile.plan_run.PLAN_EVENT})]


def test_every_run_still_live_dispatches_nothing_and_prints_the_depth(capsys):
    cards = _four_running() + _three_waiting()
    world = _serve(cards, {"101": "in_progress", "102": "in_progress",
                           "103": "queued", "104": "in_progress"})
    assert world.fires == []
    assert world.posts == []
    out = capsys.readouterr().out
    assert _depth_lines(out) == [
        f"planner line: 3 waiting, oldest 30 minutes, 4 running and 0 dispatched of {CAP}"]
    for ident in ("DRE-201", "DRE-202", "DRE-203"):
        assert any(ident in line and "planner line" in line
                   for line in out.splitlines()), f"no reason printed for {ident}"


def test_an_unreadable_status_keeps_the_claim():
    cards = _four_running() + _three_waiting()
    world = _serve(cards, {"101": None, "102": "in_progress", "103": "in_progress",
                           "104": "in_progress"})
    assert world.receipts("released") == []
    assert world.fires == []


# --------------------------------------------------------------------------- #
# the handover                                                                 #
# --------------------------------------------------------------------------- #


S, C = "4001", "4002"  # the sender run and the run it handed its slot to


def _handover_card() -> dict:
    return _card("DRE-400", "Planning",
                 _r("claimed", 20.0, run=S),
                 _r("claimed", 10.0, run=C, from_run=S))


def test_a_handed_over_claim_is_one_running_card_and_released_on_nothing(capsys):
    world = _serve([_handover_card()], {S: "completed", C: "in_progress"})
    assert world.receipts("released") == []
    assert S not in world.gh_runs_read(), "S's claim is closed by the handover"
    assert _depth_lines(capsys.readouterr().out) == [
        f"planner line: 0 waiting, oldest 0 minutes, 1 running and 0 dispatched of {CAP}"]


def test_a_handed_over_claim_whose_run_is_gone_is_released_under_its_own_run():
    world = _serve([_handover_card()], {S: "completed", C: "completed"})
    assert [(i, r.run, r.because) for i, r in world.receipts("released")] == [
        ("DRE-400", C, "run-gone")]


def test_a_claim_whose_run_is_no_run_id_is_left_to_the_ttl():
    world = _serve([_claimed("DRE-410", "not-a-run")], {"not-a-run": "completed"})
    assert world.gh_reads == []
    assert world.posts == []


# --------------------------------------------------------------------------- #
# Green Light                                                                  #
# --------------------------------------------------------------------------- #


def test_a_green_light_claim_holds_its_slot():
    cards = ([_claimed(f"DRE-50{n}", f"5{n}") for n in range(3)]
             + [_claimed("DRE-510", "510", lane="Green Light")]
             + [_waiting("DRE-520", 15.0)])
    board_calls = []
    with _world(cards, {r: "in_progress" for r in ("50", "51", "52", "510")}) as world:
        reconcile.serve_planner_line()
        board_calls = world.board.outside_calls()
    assert world.fires == []
    assert board_calls == [("Green Light",)], (
        "exactly one board read for a lane outside SWEPT_LANES")


def test_a_green_light_waiting_card_is_neither_counted_nor_dispatched(capsys):
    cards = ([_claimed(f"DRE-50{n}", f"5{n}") for n in range(3)]
             + [_waiting("DRE-530", 15.0, lane="Green Light")])
    world = _serve(cards, {r: "in_progress" for r in ("50", "51", "52")})
    assert world.fires == []
    assert _depth_lines(capsys.readouterr().out) == [
        f"planner line: 0 waiting, oldest 0 minutes, 3 running and 0 dispatched of {CAP}"]


# --------------------------------------------------------------------------- #
# the reserved slot and the place in line                                      #
# --------------------------------------------------------------------------- #


def _reserved_board(dispatched_minutes_ago: float) -> list:
    reserved = _card("DRE-600", "Planning",
                     _r("waiting", 40.0, run="w-600", reason="re-run"),
                     _r("dispatched", dispatched_minutes_ago, run="fin-1",
                        reason="re-run"))
    return ([_claimed(f"DRE-61{n}", f"61{n}") for n in range(3)] + [reserved]
            + _three_waiting())


def test_a_dispatch_inside_the_grace_is_a_taken_slot(capsys):
    world = _serve(_reserved_board(0.2),
                   {r: "in_progress" for r in ("610", "611", "612")})
    assert world.fires == []
    assert world.posts == []
    assert _depth_lines(capsys.readouterr().out) == [
        f"planner line: 3 waiting, oldest 30 minutes, 3 running and 1 dispatched of {CAP}"]


def test_a_lost_dispatch_past_the_grace_is_served_first():
    world = _serve(_reserved_board(GRACE + 5),
                   {r: "in_progress" for r in ("610", "611", "612")})
    assert [(c["identifier"], kw) for c, _, kw in world.fires] == [
        ("DRE-600", {"trigger_state": "Planning", "reason": "re-run",
                     "event": reconcile.plan_run.PLAN_EVENT})]


def test_a_card_that_lost_its_slot_keeps_its_place_in_line():
    a = _card("DRE-700", "Planning",
              _r("waiting", 60.0, run="a1"),
              _r("dispatched", 55.0, run="fin-a"),
              _r("claimed", 54.0, run="a2"),
              _r("waiting", 50.0, run="a2"))
    b = _waiting("DRE-701", 58.0)
    cards = [_claimed(f"DRE-71{n}", f"71{n}") for n in range(3)] + [a, b]
    world = _serve(cards, {r: "in_progress" for r in ("710", "711", "712")})
    assert [c["identifier"] for c, _, _ in world.fires] == ["DRE-700"]


def test_a_slot_left_free_by_a_queued_claim_is_served_to_the_head():
    """DRE-6329: a claim that finds cards already waiting queues behind them
    and leaves its free slot to `next`. With no release pending to run that
    `next`, the sweep serves the head of the line — never the queued claim."""
    a = _waiting("DRE-6300", 20.0)
    b = _waiting("DRE-6301", 19.0)
    c = _card("DRE-6302", "Planning",
              _r("claimed", 18.0, run="37790062876"),
              _r("waiting", 18.0 - 1 / 60, run="37790062876"))
    cards = [_claimed(f"DRE-73{n}", f"73{n}") for n in range(3)] + [a, b, c]
    world = _serve(cards, {r: "in_progress" for r in ("730", "731", "732")})
    assert [c["identifier"] for c, _, _ in world.fires] == ["DRE-6300"]
    assert [i for i, _ in world.receipts("dispatched")] == ["DRE-6300"]
    assert world.receipts("released") == []


def _retried_card() -> dict:
    """DRE-5213's receipts (DRE-5378): claimed and admitted, released by the
    run that finished, then the automatic retry — a GitHub re-run, under the
    same run id — claimed again and was told to wait."""
    run = "36726491495"
    return _card("DRE-5213", "Planning",
                 _r("claimed", 150.0, run=run),
                 _r("released", 121.0, run=run, because="finished"),
                 _r("claimed", 120.0, run=run),
                 _r("waiting", 120.0 - 1 / 60, run=run))


def test_a_retry_whose_run_finished_is_served_before_cards_that_joined_after_it():
    """DRE-202 joined the line after DRE-5213's first claim and before its
    retry; DRE-203 after both. The retry is served first, and GitHub calling
    its run completed is not read as a dead claim."""
    cards = ([_claimed(f"DRE-72{n}", f"72{n}") for n in range(3)]
             + [_retried_card(), _waiting("DRE-202", 140.0), _waiting("DRE-203", 60.0)])
    runs = {"720": "in_progress", "721": "in_progress", "722": "in_progress",
            "36726491495": "completed"}
    world = _serve(cards, runs)
    assert [(c["identifier"], kw) for c, _, kw in world.fires] == [
        ("DRE-5213", {"trigger_state": "Planning", "reason": None,
                      "event": reconcile.plan_run.PLAN_EVENT})]
    assert [i for i, _ in world.receipts("dispatched")] == ["DRE-5213"]
    assert world.receipts("released") == []
    assert "36726491495" not in world.gh_runs_read()


def test_the_served_retry_is_dispatched_once_not_every_pass():
    cards = ([_claimed(f"DRE-72{n}", f"72{n}") for n in range(3)]
             + [_retried_card(), _waiting("DRE-202", 140.0)])
    runs = {"720": "in_progress", "721": "in_progress", "722": "in_progress"}
    first = _serve(cards, runs)
    assert [c["identifier"] for c, _, _ in first.fires] == ["DRE-5213"]
    # The next pass reads the `dispatched` receipt the first one posted.
    ident, body = first.posts[-1]
    retried = next(c for c in cards if c["identifier"] == ident)
    retried["comments"]["nodes"].insert(0, {"body": body, "createdAt": _iso(0)})
    second = _serve(cards, runs)
    assert second.fires == []


# --------------------------------------------------------------------------- #
# the depth warning                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("waited, loud", [(BOUND / 2 + 1, True), (BOUND / 2 - 1, False)])
def test_the_depth_line_warns_past_half_the_bound(capsys, waited, loud):
    cards = _four_running() + [_waiting("DRE-800", waited)]
    world = _serve(cards, {r: "in_progress" for r in ("101", "102", "103", "104")})
    lines = _depth_lines(capsys.readouterr().out)
    assert len(lines) == 1
    assert lines[0].startswith("::warning::") is loud
    assert f"oldest {int(waited)} minutes" in lines[0]
    assert world.fires == [] and world.posts == []
    for seam in world.moves:
        seam.assert_not_called()


# --------------------------------------------------------------------------- #
# other repos                                                                  #
# --------------------------------------------------------------------------- #


def test_an_expired_claim_on_another_repos_card_is_released_without_a_read():
    cards = [_claimed("DRE-900", "900", repo=OTHER, minutes_ago=TTL + 5),
             _claimed("DRE-901", "901", repo=OTHER, minutes_ago=10.0)]
    world = _serve(cards, {"901": None})
    assert [(i, r.run, r.because, r.repo) for i, r in world.receipts("released")] == [
        ("DRE-900", "900", "expired", OTHER)]
    assert world.gh_reads == [], "another repo's run is never read"


def test_another_repos_waiting_card_is_never_dispatched(capsys):
    world = _serve([_waiting("DRE-910", 30.0, repo=OTHER)])
    assert world.fires == []
    assert world.posts == []
    out = capsys.readouterr().out
    assert any("DRE-910" in line and OTHER in line for line in out.splitlines())


# --------------------------------------------------------------------------- #
# failure                                                                      #
# --------------------------------------------------------------------------- #


def test_a_failed_dispatch_posts_no_receipt_and_goes_on_the_write_ledger():
    cards = _three_waiting()
    world, _, _, red = _main(cards, fire_result=(False, "dispatch refused rc=1"))
    assert len(world.fires) == 1
    assert world.receipts("dispatched") == []
    assert "dispatch refused rc=1" in reconcile._write_failures
    assert red


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
    out = capsys.readouterr().out
    assert "serve_planner_line" not in out
    assert _depth_lines(out) == []


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
