"""RED-first tests: a card whose build run is still QUEUED is not dispatched
again, and a card whose work already shipped is not rebuilt (DRE-4830).

THE INCIDENT (portico, 2026-09-24; the receipts are quoted below verbatim).
Seven MCP cards were promoted to Todo at 14:59 PT and dispatched as Agent Task
runs. The mini was saturated — 78 jobs waiting at 17:00 — so every one of those
runs sat `queued`, and a queued run has posted no `🧠 model-attempt` heartbeat,
because agent-task posts that at "Card → In Progress" once it has a runner.

Sixteen minutes later the reconcile sweep found each card still in Todo with no
pull request, past the lane contract's 15-minute Todo stall window, and read
"no run receipt" as "no run":

    DRE-4518  2026-09-24T21:59:34Z  🧹 Auto-promoted Backlog → Todo
    DRE-4518  2026-09-24T22:15:58Z  🧹 Reconcile: card sat in Todo with no
                                       run — re-dispatched.
    DRE-4519  2026-09-24T22:15:49Z  (the same receipt)
    DRE-4526  2026-09-24T22:00:24Z  🧹 Auto-promoted Backlog → Todo
    DRE-4526  2026-09-24T22:29:19Z  (the same receipt, the next sweep)

That receipt is `reconcile._TODO_REDISPATCH_NOTE`, written by exactly one
place: main()'s nudge loop, the `state == "Todo" and not is_open` branch. It is
the writer, named from the receipt rather than from a reading of the code.

Then DRE-4518's duplicate started at 17:44 PT — twenty minutes AFTER its own
PR #700 merged at 17:24 — and the duplicate-dispatch guard let it through,
because the guard gates on an OPEN agent PR and nothing else. It rebuilt the
same three tools from scratch onto the merged branch, which is the stranded
commit DRE-4828 was filed for.

FIX UNDER TEST, in three parts:

  1. agent-task.yml names its job after the card
     (`bureau-card: <DRE-N>`), exactly as plan.yml has since DRE-3223. The job
     name is the only place a card identifier survives into the Actions API for
     a run that has not started, so it is the only way to see a queued run.
  2. reconcile.build_run_refusal(): the card's Agent Task runs that GitHub says
     have not finished — `queued`, `waiting`, `pending` and `requested` counted
     identically to `in_progress` (stranded_fix.IN_FLIGHT_STATUSES) — refuse the
     Todo re-dispatch. A card with no run at all still dispatches.
  3. dedupe_dispatch.decide(): a MERGED agent PR, or a card already in the
     review lane or Done, skips the dispatch as an OPEN PR does today.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import reconcile  # noqa: E402
import stranded_fix  # noqa: E402

# The incident's own numbers, so a failure reads as the thing that happened.
CARD = "DRE-4518"
QUEUED_RUN = "36064854391"
DUP_RUN = "36066445856"


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


# --------------------------------------------------------------------------
# 1. the queued run is visible at all: the job name carries the card
# --------------------------------------------------------------------------
def _agent_task_job():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "agent-task.yml").read_text())
    return doc["jobs"]["execute"]


def test_agent_task_job_is_named_after_the_card():
    """The ONE place a card id survives into the Actions API before the run
    starts. Without it a queued run cannot be attributed to a card at all and
    part 2 below has nothing to read (plan.yml's DRE-3223 convention)."""
    name = str(_agent_task_job().get("name") or "")
    assert dedupe_dispatch.PLAN_JOB_MARKER in name, (
        "agent-task's job must carry the `bureau-card:` marker, the same one "
        "dedupe_dispatch.job_names_this_card matches"
    )
    assert "client_payload.identifier" in name, (
        "the marker must name THIS card, off the dispatch payload"
    )


def test_the_job_name_matcher_is_the_shared_one():
    """reconcile must not grow its own parse of the job name — two readers of
    one convention is how they come to disagree (stranded_fix's note)."""
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    assert "dedupe_dispatch.job_names_this_card" in source or (
        "dedupe_dispatch.sibling_on_this_card" in source
    ), "reconcile must attribute a run through dedupe_dispatch's matcher"


def test_in_flight_statuses_come_from_the_one_declaration():
    """`queued` must count exactly as `in_progress` does, and the set that
    says so is declared once (stranded_fix, DRE-4486)."""
    assert reconcile.IN_FLIGHT_STATUSES is stranded_fix.IN_FLIGHT_STATUSES
    for status in ("queued", "waiting", "pending", "in_progress"):
        assert status in reconcile.IN_FLIGHT_STATUSES
    assert "completed" not in reconcile.IN_FLIGHT_STATUSES


# --------------------------------------------------------------------------
# 2. reconcile.build_run_refusal(): a queued run refuses the re-dispatch
# --------------------------------------------------------------------------
def _actions_reader(runs, jobs=None, fail=None):
    """A double for reconcile._actions_read: answers the run listing with
    `runs` and any run's jobs with `jobs[run_id]`. `fail` makes the LISTING
    unreadable, the way the App token's 403 does."""
    import json

    jobs = jobs or {}

    def read(args):
        if args[0] == "run" and args[1] == "list":
            if fail:
                return None, fail
            return json.dumps(runs), None
        if args[0] == "api" and args[1].endswith("/jobs"):
            run_id = args[1].split("/runs/")[1].split("/")[0]
            names = jobs.get(run_id, [])
            return json.dumps({"jobs": [{"name": n} for n in names]}), None
        raise AssertionError(f"unexpected Actions read: {args}")

    return read


def _run(run_id, status):
    return {"databaseId": int(run_id), "status": status}


@pytest.mark.parametrize("status", sorted(stranded_fix.IN_FLIGHT_STATUSES))
def test_an_unfinished_run_for_the_card_refuses_the_redispatch(status):
    """The incident: the run exists, GitHub says it has not finished, and the
    card has said nothing because the run has no runner yet."""
    reader = _actions_reader(
        [_run(QUEUED_RUN, status)],
        {QUEUED_RUN: [f"call / bureau-card: {CARD}"]},
    )
    with patch.object(reconcile, "_actions_read", side_effect=reader):
        refusal = reconcile.build_run_refusal(CARD)
    assert refusal, f"a {status} Agent Task run must refuse the re-dispatch"
    assert QUEUED_RUN in refusal, "the refusal must name the run it found"


def test_a_completed_run_does_not_refuse():
    """A concluded run with no PR is the REAL requeue case and must still
    re-dispatch — otherwise the fix strands every dead run."""
    reader = _actions_reader(
        [_run(QUEUED_RUN, "completed")],
        {QUEUED_RUN: [f"call / bureau-card: {CARD}"]},
    )
    with patch.object(reconcile, "_actions_read", side_effect=reader):
        assert reconcile.build_run_refusal(CARD) == ""


def test_no_run_at_all_does_not_refuse():
    """ACCEPTANCE: a card with no run — queued, pending or in progress — is
    still dispatched, so the fix cannot strand work."""
    with patch.object(reconcile, "_actions_read", side_effect=_actions_reader([])):
        assert reconcile.build_run_refusal(CARD) == ""


def test_another_cards_queued_run_does_not_refuse():
    """Every card's build runs come off ONE shared workflow, so the listing
    arrives mixed; a sibling card's queued run must not hold this card."""
    reader = _actions_reader(
        [_run(QUEUED_RUN, "queued")],
        {QUEUED_RUN: ["call / bureau-card: DRE-4519"]},
    )
    with patch.object(reconcile, "_actions_read", side_effect=reader):
        assert reconcile.build_run_refusal(CARD) == ""


def test_near_miss_identifier_does_not_refuse():
    """The DRE-1034 vs DRE-10345 anchor, inherited from the shared matcher:
    DRE-451 must not be held by DRE-4518's run."""
    reader = _actions_reader(
        [_run(QUEUED_RUN, "queued")],
        {QUEUED_RUN: [f"call / bureau-card: {CARD}"]},
    )
    with patch.object(reconcile, "_actions_read", side_effect=reader):
        assert reconcile.build_run_refusal("DRE-451") == ""


def test_unreadable_listing_refuses_and_takes_the_sweep_red():
    """Every Actions read in this file fails CLOSED (gh_actions_read's
    contract): an unreadable listing is not an empty one, so the sweep defers
    the dispatch for fifteen minutes and exits red for the medic."""
    reader = _actions_reader([], fail="rc=1: HTTP 403: Resource not accessible")
    with patch.object(reconcile, "_actions_read", side_effect=reader), patch.object(
        reconcile, "workflow_on_default_branch", return_value=True
    ):
        refusal = reconcile.build_run_refusal(CARD)
    assert refusal, "an unreadable listing must not read as 'no run'"
    assert reconcile._read_failures, "the failure must take the sweep red"


def test_absent_build_stub_does_not_refuse():
    """A repo with no build stub has nothing in flight, and the sweep stays
    green — the DRE-4378 adjudication, so a missing stub cannot make every
    sweep red forever."""
    reader = _actions_reader([], fail="rc=1: HTTP 404: workflow not found")
    with patch.object(reconcile, "_actions_read", side_effect=reader), patch.object(
        reconcile, "workflow_on_default_branch", return_value=False
    ):
        assert reconcile.build_run_refusal(CARD) == ""
    assert not reconcile._read_failures


def test_the_listing_is_read_once_per_sweep():
    """One listing, however many Todo cards ask: the question is about the
    workflow, not the card, and a per-card read would be a request per card
    per sweep (the DRE-2929 class)."""
    listings = []

    def counting(args):
        if args[0] == "run":
            listings.append(args)
            return "[]", None
        raise AssertionError(f"unexpected Actions read: {args}")

    with patch.object(reconcile, "_actions_read", side_effect=counting):
        reconcile.build_run_refusal(CARD)
        reconcile.build_run_refusal("DRE-4519")
    assert len(listings) == 1


def test_build_workflow_resolves_the_stub_not_the_reusable(monkeypatch):
    """Runs only ever exist under the STUB's filename; in this repo
    agent-task.yml IS the reusable (workflow_call only), so a guard watching it
    reads permanently idle — review_workflow()/fix_workflow()'s lesson."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "bureau-pipeline")
    assert reconcile.build_workflow() == "self-agent-task.yml"
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    assert reconcile.build_workflow() == "agent-task.yml"


# --------------------------------------------------------------------------
# 2b. the sweep branch that did it: Todo + no PR
# --------------------------------------------------------------------------
def _todo_card():
    return {
        "id": "uuid-4518",
        "identifier": CARD,
        "title": "portico: MCP read tools for forms and threads",
        "description": "**Repo:** portico\nwork",
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "agent:engineer"}]},
        "updatedAt": "2026-09-24T21:59:34Z",
    }


def _sweep_mocks(extra=None):
    m = {
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
        "age_minutes": MagicMock(return_value=999),  # past the Todo window
        "pr_for": MagicMock(return_value=None),  # no PR — the redispatch case
        "flag_stranded": MagicMock(return_value=set()),
    }
    if extra:
        m.update(extra)
    return m


def test_sweep_does_not_redispatch_a_card_whose_run_is_queued():
    """MUTATION CHECK, and the incident itself: delete the refusal consult in
    main()'s Todo branch and this re-dispatches DRE-4518 at 15:15 exactly as
    it did — the receipt, the duplicate, the second build onto a merged
    branch."""
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "redispatch": MagicMock(return_value=True),
        "build_run_refusal": MagicMock(
            return_value=f"Agent Task run {QUEUED_RUN} for {CARD} is queued"),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment:
        reconcile.main()
    mocks["redispatch"].assert_not_called()
    cmd_state.assert_not_called()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert not any(reconcile._TODO_REDISPATCH_NOTE in b for b in bodies), (
        "a card with a live run must not get the re-dispatch receipt"
    )


def test_sweep_still_redispatches_a_card_with_no_run():
    """ACCEPTANCE, the other direction: nothing in flight → the card is still
    dispatched and still gets its receipt."""
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "redispatch": MagicMock(return_value=True),
        "build_run_refusal": MagicMock(return_value=""),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.main()
    mocks["redispatch"].assert_called_once()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert any(reconcile._TODO_REDISPATCH_NOTE in b for b in bodies)


def test_sweep_asks_before_dispatching_not_after():
    """The refusal is consulted on the way IN. A card held by a queued run
    must cost no dispatch at all — not a dispatch whose receipt is withheld."""
    calls = []
    mocks = _sweep_mocks({
        "active_cards": MagicMock(return_value=[_todo_card()]),
        "redispatch": MagicMock(side_effect=lambda c: calls.append(c) or True),
        "build_run_refusal": MagicMock(return_value="run 1 is queued"),
    })
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "cmd_state"
    ), patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    assert calls == []


# --------------------------------------------------------------------------
# 3. the duplicate-dispatch gate: shipped work is not rebuilt
# --------------------------------------------------------------------------
OPEN_PR = {"number": 700, "headRefName": f"agent/{CARD}-mcp-read-tools",
           "state": "OPEN"}
MERGED_PR = {**OPEN_PR, "state": "MERGED"}
CLOSED_PR = {**OPEN_PR, "state": "CLOSED"}


def _no_status_calls(run_id):
    raise AssertionError(f"run_status must not be consulted (asked {run_id})")


def test_merged_agent_pr_skips():
    """THE REBUILD: run 36066445856 started at 17:44 PT, after PR #700 had
    merged at 17:24, and pushed a from-scratch second build of the same three
    tools onto the merged branch (DRE-4828). Today only an OPEN PR stops it."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [MERGED_PR])
    assert d.skip is True
    assert "#700" in d.reason
    assert "merged" in d.reason.lower()


def test_open_agent_pr_still_skips():
    """The condition that already worked keeps working."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [OPEN_PR])
    assert d.skip is True
    assert "#700" in d.reason


def test_closed_unmerged_pr_still_proceeds():
    """An abandoned attempt leaves the card rebuildable — the case reconcile's
    review-lane requeue is built on (DRE-2034). Closed is not shipped."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [CLOSED_PR])
    assert d.skip is False


@pytest.mark.parametrize("lane", ["Done", "In Review"])
def test_a_card_whose_work_shipped_skips(lane):
    """The card's own lane says the work is in review or done; a build run
    then has nothing to build and everything to overwrite."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [],
                               card_lane=lane)
    assert d.skip is True
    assert lane in d.reason


@pytest.mark.parametrize("lane", ["Todo", "In Progress", "Backlog"])
def test_a_card_still_being_built_proceeds(lane):
    """ACCEPTANCE, the no-strand direction: no run, no PR, and a lane before
    review — the card is still dispatched."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [],
                               card_lane=lane)
    assert d.skip is False


def test_unreadable_lane_proceeds():
    """FAIL-OPEN, as everything in this guard does: a Linear blip must never
    strand a healthy card."""
    d = dedupe_dispatch.decide(CARD, DUP_RUN, [], _no_status_calls, [],
                               card_lane="")
    assert d.skip is False


def test_shipped_lanes_are_sliced_from_the_contract():
    """Not an enumerated list: a lane's position in the flow is part of what
    the lane IS (DRE-2726), and the board is renamed in the contract file."""
    import lane_scope

    assert dedupe_dispatch.SHIPPED_LANES == lane_scope.LANE_FLOW[
        lane_scope.LANE_FLOW.index("In Review"):
    ]
    assert "Todo" not in dedupe_dispatch.SHIPPED_LANES


def test_the_gate_reads_merged_prs_at_all():
    """`gh pr list` defaults to open-only — the DRE-2316 blindness. The gate's
    own read must ask for every state, or the MERGED condition above can never
    fire in production however right decide() is."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    with patch.object(dedupe_dispatch.subprocess, "run", side_effect=fake_run):
        dedupe_dispatch._card_prs(CARD)
    assert calls, "the gate must read the card's pull requests"
    listed = [a for a in calls if a[1:3] == ["pr", "list"]]
    assert listed, f"expected a `gh pr list`, got {calls}"
    assert "--state" in listed[0] and "all" in listed[0], (
        "the PR read must ask for every state so a MERGED PR is visible"
    )
    assert "state" in listed[0][listed[0].index("--json") + 1], (
        "the PR read must select `state` — decide() branches on it"
    )


def test_cmd_gate_skips_a_merged_pr_end_to_end(tmp_path, monkeypatch):
    """The wiring, not just the decision: the CLI reads the lane and the PR
    list, decides, and writes skip=true with a machine-marked receipt."""
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", DUP_RUN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "dreadnought-foundry/portico")
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies", return_value=[]
    ), patch.object(
        dedupe_dispatch, "_card_prs", return_value=[MERGED_PR]
    ), patch.object(
        dedupe_dispatch, "_current_lane", return_value="Done"
    ), patch.object(
        dedupe_dispatch.linear_ops, "cmd_comment"
    ) as receipt:
        dedupe_dispatch.cmd_gate(CARD)
    assert "skip=true" in out.read_text()
    receipt.assert_called_once()
    assert receipt.call_args[0][1].startswith("🤖")


def test_cmd_gate_fails_open_when_the_lane_is_unreadable(tmp_path, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", DUP_RUN)
    with patch.object(
        dedupe_dispatch.linear_ops, "comment_bodies", return_value=[]
    ), patch.object(
        dedupe_dispatch, "_card_prs", return_value=[]
    ), patch.object(
        dedupe_dispatch, "_current_lane", side_effect=RuntimeError("linear 500")
    ):
        dedupe_dispatch.cmd_gate(CARD)
    assert "skip=false" in out.read_text()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
