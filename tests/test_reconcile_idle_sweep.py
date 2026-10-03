"""An idle sweep costs almost nothing (the CEO, 2026-10-02 ~18:00 PT).

"The Agent Bureau demo is our sandbox so it should always run there." The demo
repo's sweep runs every fifteen minutes whether or not anything is happening,
so a pass with nothing to do must cost next to nothing. ONE cheap check
decides, before the board is read:

  * the WIP cap is 0 → idle, no request at all;
  * no card of this repo is in motion (Todo / In Progress / In Review) and none
    waits in Backlog → idle, ONE request (`reconcile.IDLE_QUERY`, `first: 1`,
    ids only) — or none, when the read door answers it;
  * otherwise → the pass runs exactly as before.

Idle skips the board read, promotion and every phase that reads the board,
and says so in ONE line. The same `sweep-spend:` accounting applies.

The repo here is demo-shaped: `agent-bureau-demo`, on a team board busy with
OTHER repos' cards — which must not make the demo's pass pay for them.
Every real `subprocess.run` is refused, so nothing here reaches GitHub; the
Linear fake refuses any query but the ones named, so a phase that slips past
the idle skip fails the test by name.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau-demo")
os.environ.setdefault("REPO_SLUG", "agent-bureau-demo")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402
import validate_card  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

DEMO = "agent-bureau-demo"

#: The backstops that read GitHub. Stood down: this file is about Linear's
#: cost, and none of them reads the board before GitHub answers them.
GITHUB_BACKSTOPS = (
    "unstick_conflicts", "refresh_stale_merge_refs", "retrigger_dead_heads",
    "flag_no_checks_prs", "flag_unowned_prs", "flag_unlanded_work",
    "flag_stranded_fixes", "fix_approved_but_red", "retry_dead_fix_runs",
    "redispatch_standing_verdicts", "restart_answered_blockers",
    "review_dependabot_prs", "recover_crashed_reviews", "check_dependabot_capacity",
    "report_fix_concurrency", "report_evicted_fix_runs",
)


class TeamBoard:
    """Linear for the whole team: other repos busy, the demo as configured.
    Answers the idle check by its label and lanes, the way Linear filters."""

    def __init__(self, *cards):
        self.cards = list(cards)
        self.queries: list[str] = []

    @property
    def requests(self) -> int:
        return len(self.queries)

    def gql(self, query, variables=None):
        self.queries.append(query)
        v = variables or {}
        if query == reconcile.IDLE_QUERY:
            hits = [c for c in self.cards
                    if c["state"]["name"] in v["states"]
                    and v["label"] in {n["name"] for n in c["labels"]["nodes"]}]
            return {"issues": {"nodes": [{"id": c["id"]} for c in hits[:1]]}}
        q = " ".join(query.split())
        if "state: {name: {in: $states}}" in q:
            nodes = [c for c in self.cards if c["state"]["name"] in v["states"]]
            return {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        if 'state: {name: {eq: "Backlog"}}' in q:
            nodes = [c for c in self.cards if c["state"]["name"] == "Backlog"]
            return {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        raise AssertionError(f"a Linear read on an idle pass: {q[:160]}")


def _busy_elsewhere():
    return [card(f"DRE-{n}", lane, labels=("repo:portico",))
            for n, lane in ((1, "Todo"), (2, "In Progress"), (3, "In Review"),
                            (4, "Backlog"), (5, "Planning"), (6, "Intake"))]


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "MERGED_CARD",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", DEMO)
    monkeypatch.setattr(reconcile, "MAX_WIP", 3)
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {DEMO, "portico"})
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _refuse_subprocess(*a, **k):
    raise AssertionError(f"a real subprocess on an idle pass: {a[0] if a else k}")


def _pass(board: TeamBoard, *, stub=()):
    with contextlib.ExitStack() as stack:
        for name in GITHUB_BACKSTOPS + tuple(stub):
            stack.enter_context(mock.patch.object(reconcile, name))
        stack.enter_context(mock.patch.object(reconcile.subprocess, "run", _refuse_subprocess))
        # The GitHub listings the PR-side backstops read first: empty, so they
        # run for real and find no pull request to act on.
        stack.enter_context(mock.patch.object(reconcile, "_dependabot_pr_listing",
                                              return_value=[]))
        stack.enter_context(mock.patch.object(reconcile, "_open_pr_listing", return_value=[]))
        stack.enter_context(mock.patch.object(linear_ops, "gql", side_effect=board.gql))
        stack.enter_context(mock.patch.object(linear_ops, "requests_made",
                                              lambda: board.requests))
        for write in ("cmd_comment", "cmd_advance", "cmd_state", "add_label", "remove_label"):
            stack.enter_context(mock.patch.object(linear_ops, write))
        reconcile.main()


def _lines(capsys, prefix):
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(prefix)]


def test_an_idle_demo_pass_spends_one_request_and_says_so_once(capsys):
    board = TeamBoard(*_busy_elsewhere())
    _pass(board)
    assert board.queries == [reconcile.IDLE_QUERY]
    out = capsys.readouterr().out
    idle = [ln for ln in out.splitlines() if ln.startswith("idle:")]
    assert len(idle) == 1 and DEMO in idle[0]
    assert "sweep-spend: sweep_idle 1 request(s)" in out
    assert "sweep-spend: total 1 request(s) over 1 phase(s)" in out
    assert "promotion:" not in out


def test_a_demo_card_in_motion_runs_the_pass_as_before(capsys):
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-90", "In Progress", labels=(f"repo:{DEMO}",)))
    stubs = ("recover_limit_deaths", "card_dependabot_prs", "report_fleet_reviewer_outage",
             "settle_repair_cards", "flag_stranded", "report_intake_depth",
             "advance_urgent_intake", "repair_frozen_planning_holds", "serve_planner_line",
             "release_groom_queue", "move_hand_built_to_review", "carry_epics_out_of_todo",
             "report_break_glass", "report_epic_growth", "pr_for", "agent_run_alive",
             "redeliver_rescued_work")
    with mock.patch.object(reconcile, "rereview_watch_scope",
                           return_value=(set(), lambda e: None)):
        _pass(board, stub=stubs)
    out = capsys.readouterr().out
    assert not [ln for ln in out.splitlines() if ln.startswith("idle:")]
    assert board.queries[0] == reconcile.IDLE_QUERY
    assert any("state: {name: {in: $states}}" in q and q != reconcile.IDLE_QUERY
               for q in board.queries)  # the board read happened
    assert "promotion: WIP cap 3" in out


def _busy(board) -> bool:
    """True when the idle check says this pass has work."""
    with mock.patch.object(linear_ops, "gql", side_effect=board.gql):
        return reconcile.sweep_idle() is None


def test_a_demo_card_waiting_in_backlog_is_not_idle():
    board = TeamBoard(*_busy_elsewhere(), card("DRE-91", "Backlog", labels=(f"repo:{DEMO}",)))
    assert _busy(board)
    assert board.queries == [reconcile.IDLE_QUERY]


def test_a_demo_card_in_planning_does_not_make_the_pass_busy():
    """Planning is not motion: the planner moves it, and the board phases that
    watch Planning are fleet-wide — every other repo's sweep runs them."""
    board = TeamBoard(*_busy_elsewhere(), card("DRE-92", "Planning", labels=(f"repo:{DEMO}",)))
    assert not _busy(board)


def test_a_wip_cap_of_zero_is_idle_with_no_request_at_all(monkeypatch, capsys):
    monkeypatch.setattr(reconcile, "MAX_WIP", 0)
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-90", "In Progress", labels=(f"repo:{DEMO}",)))
    _pass(board)
    assert board.queries == []
    out = capsys.readouterr().out
    assert "idle: agent-bureau-demo — the WIP cap is 0" in out
    assert "sweep-spend: total 0 request(s)" in out


def test_an_unreadable_check_runs_the_full_pass():
    def broken(query, variables=None):
        raise linear_ops.LinearError("linear error from x: 502")
    with mock.patch.object(linear_ops, "gql", side_effect=broken):
        assert reconcile.sweep_idle() is None


def test_with_the_door_on_the_idle_check_costs_no_linear_request(monkeypatch, capsys):
    board = TeamBoard(*_busy_elsewhere())
    with FakeIssuer() as issuer, FakeDoor({}, repository=f"dreadnought-foundry/{DEMO}") as door:
        for key, value in door_env(door_url=door.url, issuer=issuer,
                                   repository=f"dreadnought-foundry/{DEMO}").items():
            monkeypatch.setenv(key, value)
        _pass(board)
        lanes = door.asked("/board")[0]["query"]["lanes"]
    assert board.queries == []
    assert lanes == "Todo,In Progress,In Review,Backlog"
    assert "idle: agent-bureau-demo — no card of this repo is in motion" in capsys.readouterr().out


def test_with_the_door_on_a_held_backlog_card_is_not_waiting(monkeypatch):
    held = card("DRE-93", "Backlog", labels=(f"repo:{DEMO}", "needs-human"))
    with FakeIssuer() as issuer, FakeDoor({"DRE-93": held},
                                          repository=f"dreadnought-foundry/{DEMO}") as door:
        for key, value in door_env(door_url=door.url, issuer=issuer,
                                   repository=f"dreadnought-foundry/{DEMO}").items():
            monkeypatch.setenv(key, value)
        assert reconcile.sweep_idle() is not None


def test_an_idle_pass_still_files_the_card_a_new_dependabot_pr_needs(monkeypatch):
    """An open pull request is work whatever the board says: the PR-side
    backstops still run on an idle pass, and filing a card reads no board."""
    board = TeamBoard(*_busy_elsewhere())
    pr = {"number": 7, "url": "https://github.com/x/pull/7", "title": "Bump x",
          "body": "", "state": "OPEN", "headRefName": "dependabot/pip/x",
          "author": {"login": "app/dependabot", "is_bot": True}}
    monkeypatch.setattr(reconcile, "is_dependabot_pr", lambda p: True)
    created = []
    with mock.patch.object(linear_ops, "find_by_pr_url", return_value=None), \
            mock.patch.object(linear_ops, "create_card",
                              side_effect=lambda *a, **k: created.append(a) or
                              {"identifier": "DRE-99"}), \
            mock.patch.object(reconcile, "_stamp_dependabot_pr"), \
            mock.patch.object(linear_ops, "gql", side_effect=board.gql), \
            mock.patch.object(reconcile, "_dependabot_pr_listing", return_value=[pr]):
        reconcile.reset_sweep_cards()
        reconcile._idle_pass.append("no card in motion")
        reconcile.card_dependabot_prs()
    assert len(created) == 1
    assert board.queries == []  # no board read


def test_the_idle_check_is_the_smallest_question():
    q = " ".join(reconcile.IDLE_QUERY.split())
    assert "issues(first: 1," in q
    assert "nodes { id }" in q
    assert "labels: {name: {eq: $label}}" in q
    assert reconcile.IDLE_LANES == ("Todo", "In Progress", "In Review", "Backlog")
