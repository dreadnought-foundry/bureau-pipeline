"""An idle sweep costs almost nothing (the CEO, 2026-10-02 ~18:00 PT).

"The Agent Bureau demo is our sandbox so it should always run there." The demo
repo's sweep runs every fifteen minutes whether or not anything is happening,
so a pass with nothing to do must cost next to nothing. ONE cheap check
decides, before the board is read:

  * the WIP cap is 0 → idle, no request at all;
  * no card of this repo is in motion (Todo / In Progress / In Review) and none
    waits in Backlog → idle, decided by ONE request (`reconcile.IDLE_QUERY`,
    `first: 1`, ids only) — or none, when the read door answers it;
  * otherwise → the pass runs exactly as before.

Idle skips promotion and this repo's work-lane phases and says so in ONE
line. The FLEET-WIDE phases still run — the Urgent fast path, the Planning
stall watchdog, the frozen-holds repair and the planner line act on every
repo's cards, and with most sweeps paused an idle repo may be the only one
running them (the coordinator's decision, 2026-10-02 ~19:10 PT). So an idle
pass costs the check plus their reads: the one board read and the planner
line's Green Light read, three requests. The same `sweep-spend:` accounting
applies.

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
    "redispatch_standing_verdicts", "rerun_runner_lost_ci", "restart_answered_blockers",
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
            # Filtered the way Linear filters: `eq` is an exact match, and
            # `containsIgnoreCase` matches any label whose name contains the
            # needle in any case. The page is `first:` long, and says whether
            # there is more.
            def match(name):
                if "label" in v:
                    return name == v["label"]
                return v["needle"].lower() in name.lower()
            hits = [c for c in self.cards
                    if c["state"]["name"] in v["states"]
                    and any(match(n["name"]) for n in c["labels"]["nodes"])]
            first = getattr(reconcile, "IDLE_PAGE", 1)
            return {"issues": {
                "nodes": [{"id": c["id"], "labels": c["labels"]} for c in hits[:first]],
                "pageInfo": {"hasNextPage": len(hits) > first}}}
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
        # And the build-run listing the no-run watchdog asks before it stamps
        # (DRE-5743): empty, so a strand of this repo's is still stamped.
        stack.enter_context(mock.patch.object(reconcile, "_build_runs_in_flight",
                                              return_value=[]))
        stack.enter_context(mock.patch.object(linear_ops, "gql", side_effect=board.gql))
        stack.enter_context(mock.patch.object(linear_ops, "requests_made",
                                              lambda: board.requests))
        for write in ("cmd_comment", "cmd_advance", "cmd_state", "add_label", "remove_label"):
            stack.enter_context(mock.patch.object(linear_ops, write))
        reconcile.main()


def _lines(capsys, prefix):
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(prefix)]


def _lane_reads(board: TeamBoard) -> list[tuple]:
    return [q for q in board.queries if q != reconcile.IDLE_QUERY]


def test_an_idle_demo_pass_spends_three_requests_and_says_so_once(capsys):
    board = TeamBoard(*_busy_elsewhere())
    _pass(board)
    assert board.queries[0] == reconcile.IDLE_QUERY
    assert board.requests == 3  # the check, the one board read, Green Light
    assert not any('eq: "Backlog"' in q for q in board.queries)  # no promotion read
    out = capsys.readouterr().out
    idle = [ln for ln in out.splitlines() if ln.startswith("idle:")]
    assert len(idle) == 1 and DEMO in idle[0]
    assert "the fleet-wide Planning and Intake phases still run" in idle[0]
    assert "sweep-spend: sweep_idle 1 request(s)" in out
    assert "sweep-spend: total 3 request(s) over 3 phase(s)" in out
    assert "promotion:" not in out
    assert "planner line:" in out and "urgent-fast-path:" in out


def test_an_idle_pass_runs_the_fleet_phases_and_skips_the_repo_ones():
    board = TeamBoard(*_busy_elsewhere())
    # The re-review watcher runs on an idle pass too (the PR #687 critic's
    # item 3): the stall watchdog, which is fleet-wide and runs, leaves this
    # repo's Planning epics with children to it (`_with_the_critics`).
    fleet = ("flag_stranded", "advance_urgent_intake", "repair_frozen_planning_holds",
             "serve_planner_line", "rereview_watch_scope")
    repo_scoped = ("promote_ready", "move_hand_built_to_review", "report_break_glass")
    with contextlib.ExitStack() as stack:
        spies = {name: stack.enter_context(mock.patch.object(
            reconcile, name, wraps=getattr(reconcile, name))) for name in fleet}
        skipped = {name: stack.enter_context(mock.patch.object(
            reconcile, name, side_effect=AssertionError(name))) for name in repo_scoped}
        _pass(board)
    for name, spy in spies.items():
        assert spy.call_count == 1, name
    for name, never in skipped.items():
        assert never.call_count == 0, name


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


def test_a_wip_cap_of_zero_is_idle_and_the_check_asks_nothing(monkeypatch, capsys):
    monkeypatch.setattr(reconcile, "MAX_WIP", 0)
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-90", "In Progress", labels=(f"repo:{DEMO}",)))
    _pass(board)
    assert reconcile.IDLE_QUERY not in board.queries
    assert board.requests == 2  # only the fleet phases' board and Green Light reads
    out = capsys.readouterr().out
    assert "idle: agent-bureau-demo — the WIP cap is 0" in out
    assert "sweep-spend: sweep_idle" not in out
    assert "sweep-spend: total 2 request(s)" in out
    assert "promotion:" not in out


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
        door.decline_fleet("lane-not-held")
        _pass(board)
        lanes = door.asked("/board")[0]["query"]["lanes"]
    assert lanes == "Todo,In Progress,In Review,Hand-work,Backlog"
    # The check asked the door, not Linear. Linear answered only the fleet
    # phases' reads: the lanes this door declines at `scope=fleet` — it serves
    # the fleet to no repo while the console's PIPELINE_READ_UNROUTED is off
    # (DRE-5848) — Planning and Intake and the planner line's Green Light, and
    # the NO-ROUTE watchdog's Todo and In Progress, which the door cannot serve
    # for cards no repo owns (the PR #687 critic's item 2).
    assert reconcile.IDLE_QUERY not in board.queries
    assert board.requests == 3
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


def test_the_idle_check_is_one_small_page_that_fails_open():
    """One request, a bounded page, ids and label names only. The label match
    is case-insensitive and owner-blind, as routing's is (`card_repo`): the
    query asks for any label CONTAINING the slug, and the sweep keeps the
    cards whose repo is exactly this one."""
    q = " ".join(reconcile.IDLE_QUERY.split())
    assert "issues(first: $first," in q
    assert "labels: {name: {containsIgnoreCase: $needle}}" in q
    assert "nodes { id labels { nodes { name } } }" in q
    assert "pageInfo { hasNextPage }" in q
    assert 1 < reconcile.IDLE_PAGE <= 100
    # Hand-work counts as in motion (DRE-5322): a person's card there is owed
    # the move to review and the idle alarm, which an idle pass skips.
    assert reconcile.IDLE_LANES == ("Todo", "In Progress", "In Review", "Hand-work", "Backlog")


# ── The label match is routing's, not an exact string (PR #687 critic, item 1) ──
# Routing (`card_repo`, `validate_card._repo_label_slugs`) lowercases the label
# and drops an owner, so `repo:dreadnought-foundry/agent-bureau-demo` and
# `repo:Agent-Bureau-Demo` both route here. An exact `eq` on `repo:<slug>` read
# such a repo as idle and skipped its promotion — the confident empty.


def test_an_owner_qualified_repo_label_is_not_idle():
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-94", "Todo",
                           labels=(f"repo:dreadnought-foundry/{DEMO}",)))
    assert _busy(board)


def test_a_capitalized_repo_label_waiting_in_backlog_is_not_idle():
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-95", "Backlog", labels=("repo:Agent-Bureau-Demo",)))
    assert _busy(board)


def test_a_longer_slug_that_contains_this_one_is_another_repos_work(monkeypatch):
    """`agent-bureau` is a substring of `agent-bureau-demo`: the needle finds
    the demo's card, and the sweep's own repo test turns it away."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    board = TeamBoard(*_busy_elsewhere(),
                      card("DRE-96", "Todo", labels=(f"repo:{DEMO}",)))
    assert not _busy(board)


def test_a_full_page_of_near_misses_is_not_proof_of_idle(monkeypatch):
    """Every card on the page is another repo's, and Linear says there is
    more: this repo's card could be on the next page. Unknown is not idle."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    page = getattr(reconcile, "IDLE_PAGE", 1)
    board = TeamBoard(*[card(f"DRE-{200 + n}", "Todo", labels=(f"repo:{DEMO}",))
                        for n in range(page + 1)])
    assert _busy(board)
    assert len(board.queries) == 1  # still one request: it fails open, it does not page


def test_an_idle_pass_still_hands_the_watcher_this_repos_planning_epic():
    """The PR #687 critic's item 3. A Planning epic with children is the stall
    watchdog's to SKIP (`_with_the_critics`) and the re-review watcher's to
    watch. The watchdog is fleet-wide and runs on an idle pass, so the watcher
    must run too, or that epic is watched by nobody."""
    epic = card("DRE-97", "Planning", labels=(f"repo:{DEMO}",), children=True)
    board = TeamBoard(*_busy_elsewhere(), epic)
    with mock.patch.object(reconcile.rereview_watch, "report") as report:
        _pass(board)
    assert report.call_count == 1
    watched = report.call_args[0][0]
    assert "DRE-97" in watched
