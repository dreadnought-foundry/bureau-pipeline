"""RED-first: a queued planner dispatch must not re-plan an epic (DRE-3409).

THE INCIDENT (DRE-3244, 2026-09-08, five `Agent Plan` runs in 42 minutes).
The mover was never upstream of the run and was never the medic: it is
`plan.yml`'s own `Route — plan or activate` step, running inside a run that
had been QUEUED for a quarter of an hour. `self-plan.yml` groups plan runs
per card (`agent-<identifier>`, `cancel-in-progress: false`), so a second
dispatch does not die — it waits, and starts as soon as the run ahead of it
finishes. The evidence, off run 34281711446's own log:

    21:38:22  Green Light → Planning   (the move nobody could explain)
    21:38:27  run 34281711446 created  (the relay, on that Planning entry)
    21:52:45  run 34279893974 completes — the epic is in Green Light
    21:52:50  run 34281711446's job finally STARTS
    21:53:00  step `Route — plan or activate`, FROM="planning"
    21:53:02  `DRE-3244 → Planning`    ← plan.yml:411, the mover

Each queued run undid the Green Light the run ahead of it had just written,
and the relay turned that Planning entry into yet another dispatch. Five
runs, three plans, three critic passes, one epic.

THE FIX UNDER TEST — `dedupe_dispatch.py plan-gate`, the build guard's own
shape (DRE-2057) reused for the planner, wired into `plan.yml` before the
route step. It refuses on either condition:

  (a) ANOTHER PLAN RUN WAS IN FLIGHT when this dispatch arrived. Read off
      GitHub, not off the clock at start time — by the time a queued run
      starts, the run it was queued behind has already finished, so
      "is one live right now" would have seen nothing.
  (b) THE LANE HAS MOVED ON. The dispatch names the lane the card entered;
      if the epic is somewhere else by the time the run gets a runner, the
      entry it was fired for has already been served.

Fail-open throughout, like the build guard: this gates a PLAN, not a merge.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_dispatch_dedupe.py -v
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402

# --- the incident, as GitHub recorded it ----------------------------------
# `gh run view <id> --json createdAt,updatedAt` on DRE-3244's five runs.
DRE_3244_RUNS = [
    {"id": "34279866400", "created_at": "2026-09-08T21:18:41Z",
     "updated_at": "2026-09-08T21:38:06Z", "status": "completed"},
    {"id": "34279893974", "created_at": "2026-09-08T21:19:00Z",
     "updated_at": "2026-09-08T21:52:45Z", "status": "completed"},
    {"id": "34281711446", "created_at": "2026-09-08T21:38:27Z",
     "updated_at": "2026-09-08T22:00:09Z", "status": "completed"},
    {"id": "34283008358", "created_at": "2026-09-08T21:53:06Z",
     "updated_at": "2026-09-08T22:09:46Z", "status": "completed"},
    {"id": "34283640273", "created_at": "2026-09-08T22:00:29Z",
     "updated_at": "2026-09-08T22:15:37Z", "status": "completed"},
]


def _run(run_id, created, updated, status="completed"):
    return {"id": run_id, "created_at": created, "updated_at": updated,
            "status": status}


# --------------------------------------------------------------------------
# (a) in_flight_when_dispatched() — "was a run already going when I arrived?"
# --------------------------------------------------------------------------
def test_the_queued_duplicate_sees_the_run_it_was_queued_behind():
    """Run 34281711446 was created at 21:38:27 while 34279893974 (21:19:00 →
    21:52:45) was still going. That overlap is the whole defect, and it is
    invisible to a liveness check at start time: 34279893974 had finished
    five seconds before 34281711446 got a runner, at 21:52:50.

    34279866400 is deliberately NOT in the answer — it ended at 21:38:06,
    twenty-one seconds before this dispatch was created. Overlap is measured,
    not assumed from being earlier."""
    live = dedupe_dispatch.in_flight_when_dispatched(
        DRE_3244_RUNS, "34281711446", "2026-09-08T21:38:27Z"
    )
    assert [r["id"] for r in live] == ["34279893974"]


@pytest.mark.parametrize(
    "own_id,own_created,expected",
    [
        # The CEO's own approval run — nothing was in flight before it.
        ("34279866400", "2026-09-08T21:18:41Z", []),
        # Every later run in the loop was dispatched on top of a live run.
        ("34279893974", "2026-09-08T21:19:00Z", ["34279866400"]),
        ("34283008358", "2026-09-08T21:53:06Z", ["34281711446"]),
        ("34283640273", "2026-09-08T22:00:29Z", ["34283008358"]),
    ],
)
def test_only_the_ceo_triggered_run_was_dispatched_into_a_quiet_card(
    own_id, own_created, expected
):
    live = dedupe_dispatch.in_flight_when_dispatched(
        DRE_3244_RUNS, own_id, own_created
    )
    assert [r["id"] for r in live] == expected


def test_a_run_that_finished_before_the_dispatch_is_not_in_flight():
    """The legitimate re-plan: the previous run is over and done before the
    new dispatch arrives."""
    prior = [_run("1", "2026-09-08T20:00:00Z", "2026-09-08T20:30:00Z")]
    assert dedupe_dispatch.in_flight_when_dispatched(
        prior, "2", "2026-09-08T20:31:00Z"
    ) == []


def test_a_run_created_after_this_one_is_never_in_flight_for_it():
    """A later duplicate is that run's own problem to refuse — otherwise both
    sides of a pair refuse each other and the epic is never planned."""
    later = [_run("2", "2026-09-08T21:00:00Z", "2026-09-08T21:30:00Z")]
    assert dedupe_dispatch.in_flight_when_dispatched(
        later, "1", "2026-09-08T20:59:00Z"
    ) == []


def test_our_own_run_never_counts():
    """A job re-run keeps the run id; the run must not refuse on itself. Here
    34279893974 IS its own overlapping sibling by the clock — created before
    itself is false, but the id check is what a re-run relies on."""
    itself = [_run("34279893974", "2026-09-08T21:19:00Z",
                   "2026-09-08T21:52:45Z")]
    assert dedupe_dispatch.in_flight_when_dispatched(
        itself, "34279893974", "2026-09-08T21:19:00Z"
    ) == []


def test_same_second_dispatches_break_the_tie_on_run_id():
    """Two dispatches in the same second (the webhook double-fire the build
    guard was written for): the LOWER run id proceeds and the higher refuses,
    so exactly one of the pair runs."""
    pair = [
        _run("100", "2026-09-08T21:00:00Z", "2026-09-08T21:20:00Z"),
        _run("101", "2026-09-08T21:00:00Z", "2026-09-08T21:25:00Z"),
    ]
    assert [r["id"] for r in dedupe_dispatch.in_flight_when_dispatched(
        pair, "101", "2026-09-08T21:00:00Z")] == ["100"]
    assert dedupe_dispatch.in_flight_when_dispatched(
        pair, "100", "2026-09-08T21:00:00Z") == []


def test_a_still_running_sibling_with_no_end_time_counts():
    """status != completed means it is still going, whatever updated_at says."""
    live = [_run("1", "2026-09-08T21:00:00Z", "2026-09-08T21:00:05Z",
                 status="in_progress")]
    assert [r["id"] for r in dedupe_dispatch.in_flight_when_dispatched(
        live, "2", "2026-09-08T21:10:00Z")] == ["1"]


def test_unparseable_timestamps_fail_open():
    """An unreadable run record must not refuse a plan — a false refusal
    strands an epic with nobody planning it."""
    junk = [_run("1", "not-a-time", "also-not-a-time", status="completed")]
    assert dedupe_dispatch.in_flight_when_dispatched(
        junk, "2", "2026-09-08T21:10:00Z") == []
    assert dedupe_dispatch.in_flight_when_dispatched(
        DRE_3244_RUNS, "2", "") == []


# --------------------------------------------------------------------------
# (b) lane_left_behind() — the dispatch names a lane the card has left
# --------------------------------------------------------------------------
def test_the_epic_moved_on_to_green_light_before_the_run_got_a_runner():
    """The observed state at 21:52:50: the dispatch was fired on a Planning
    entry, and the epic was in Green Light by the time the run started."""
    assert dedupe_dispatch.lane_left_behind("planning", "Green Light") is True


def test_a_dispatch_served_in_the_lane_it_was_fired_for_proceeds():
    assert dedupe_dispatch.lane_left_behind("planning", "Planning") is False
    assert dedupe_dispatch.lane_left_behind("in progress", "In Progress") is False


def test_a_renamed_lane_is_resolved_before_it_is_compared():
    """`Plan Review` was renamed to `Green Light` (the contract's own alias).
    A board mid-rename must not read as a card that moved."""
    assert dedupe_dispatch.lane_left_behind("plan review", "Green Light") is False


def test_an_unreadable_lane_proceeds():
    """Fail-open on both sides: no trigger lane in the payload, or no lane
    read back from Linear, means nothing to compare."""
    assert dedupe_dispatch.lane_left_behind("", "Green Light") is False
    assert dedupe_dispatch.lane_left_behind("planning", "") is False


# --------------------------------------------------------------------------
# job_names_this_card() — which card a candidate run belongs to
# --------------------------------------------------------------------------
def test_the_job_name_carries_the_card():
    """plan.yml names its job after the card (DRE-3223); through the stub the
    API reports it as `call / bureau-card: DRE-3244`."""
    assert dedupe_dispatch.job_names_this_card(
        ["call / bureau-card: DRE-3244", "call / publish"], "DRE-3244") is True


def test_a_near_miss_identifier_is_not_this_card():
    """DRE-324 must not match DRE-3244, and DRE-3244 must not match
    DRE-32440 (reconcile.pr_for's anchoring rule)."""
    names = ["call / bureau-card: DRE-3244"]
    assert dedupe_dispatch.job_names_this_card(names, "DRE-324") is False
    assert dedupe_dispatch.job_names_this_card(
        ["call / bureau-card: DRE-32440"], "DRE-3244") is False


def test_a_run_with_no_jobs_yet_is_not_claimed_for_this_card():
    assert dedupe_dispatch.job_names_this_card([], "DRE-3244") is False


# --------------------------------------------------------------------------
# plan_decide() — the refusal, and the reason a person reads
# --------------------------------------------------------------------------
def test_plan_decide_refuses_the_dispatch_that_was_queued_behind_a_run():
    d = dedupe_dispatch.plan_decide(
        "DRE-3244", "planning", "Green Light",
        [_run("34279893974", "2026-09-08T21:19:00Z", "2026-09-08T21:52:45Z")],
    )
    assert d.skip is True
    assert "34279893974" in d.reason


def test_plan_decide_refuses_a_dispatch_whose_lane_has_moved_on():
    """Even with no readable sibling run, an epic that has left the lane the
    dispatch was fired for is not re-planned — this is the criterion 'an epic
    left in Green Light by a planner run stays there'."""
    d = dedupe_dispatch.plan_decide("DRE-3244", "planning", "Green Light", [])
    assert d.skip is True
    assert "Green Light" in d.reason and "planning" in d.reason.lower()


def test_plan_decide_lets_the_first_dispatch_through():
    """The CEO's approval move: nothing in flight, and the epic is in the
    lane the dispatch names."""
    d = dedupe_dispatch.plan_decide("DRE-3244", "in progress", "In Progress", [])
    assert d.skip is False


def test_plan_decide_lets_a_normal_planning_entry_through():
    d = dedupe_dispatch.plan_decide("DRE-3409", "planning", "Planning", [])
    assert d.skip is False


def test_plan_decide_names_the_identifier_in_every_reason():
    for args in (("planning", "Green Light", []),
                 ("planning", "Planning", []),
                 ("planning", "Green Light",
                  [_run("9", "2026-09-08T21:00:00Z", "2026-09-08T21:30:00Z")])):
        d = dedupe_dispatch.plan_decide("DRE-3244", *args)
        assert "DRE-3244" in d.reason


# --------------------------------------------------------------------------
# The incident replayed end to end: how many of the five runs would plan?
# --------------------------------------------------------------------------
def test_the_incident_collapses_to_one_planner_run():
    """DRE-3244's five runs, replayed against the guard with the lane the
    board actually held when each run started. Only the CEO's own approval
    run plans; the four the loop produced are refused."""
    # (own run id, its created_at, the dispatch's trigger lane, the lane the
    #  board held when the run finally started)
    replay = [
        ("34279866400", "2026-09-08T21:18:41Z", "in progress", "In Progress"),
        ("34279893974", "2026-09-08T21:19:00Z", "planning", "Green Light"),
        ("34281711446", "2026-09-08T21:38:27Z", "planning", "Green Light"),
        ("34283008358", "2026-09-08T21:53:06Z", "planning", "Green Light"),
        ("34283640273", "2026-09-08T22:00:29Z", "planning", "Green Light"),
    ]
    planned = []
    for own_id, own_created, trigger, lane in replay:
        siblings = dedupe_dispatch.in_flight_when_dispatched(
            DRE_3244_RUNS, own_id, own_created
        )
        if not dedupe_dispatch.plan_decide("DRE-3244", trigger, lane, siblings).skip:
            planned.append(own_id)
    assert planned == ["34279866400"], (
        "exactly one Planning entry must produce exactly one planner run"
    )


# --------------------------------------------------------------------------
# cmd_plan_gate — the thin CLI: outputs, one receipt, fail-open
# --------------------------------------------------------------------------
@pytest.fixture()
def _gh_output(tmp_path, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", "34281711446")
    # Pinned, not inherited: this suite runs INSIDE Actions, and a re-run of
    # the CI job would otherwise hand every test below the re-run carve-out.
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setenv("TRIGGER_STATE", "planning")
    return out


def test_cmd_plan_gate_skip_emits_output_and_one_receipt(_gh_output):
    with patch.object(dedupe_dispatch, "_run_meta", return_value={}), \
         patch.object(dedupe_dispatch, "_current_lane", return_value="Green Light"), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=true" in _gh_output.read_text()
    receipt.assert_called_once()
    body = receipt.call_args[0][1]
    # Machine-marker prefix: reconcile's blocker gate reads any NON-machine
    # comment as a human reply (reconcile._AGENT_COMMENT_PREFIXES).
    assert body.startswith("🤖"), "skip receipt must carry a machine marker prefix"
    assert "Duplicate dispatch skipped" in body, (
        "the receipt must be the shape the build-run dedupe already uses"
    )
    assert "/actions/runs/34281711446" in body, "the receipt names its own run"


def test_cmd_plan_gate_clean_emits_skip_false_and_says_nothing(_gh_output):
    with patch.object(dedupe_dispatch, "_run_meta", return_value={}), \
         patch.object(dedupe_dispatch, "_current_lane", return_value="Planning"), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=false" in _gh_output.read_text()
    receipt.assert_not_called()


def test_cmd_plan_gate_fails_open_when_linear_is_unreadable(_gh_output):
    with patch.object(dedupe_dispatch, "_run_meta", return_value={}), \
         patch.object(dedupe_dispatch.linear_ops, "get_issue",
                      side_effect=RuntimeError("linear 500")), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment"):
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=false" in _gh_output.read_text()


def test_cmd_plan_gate_fails_open_when_github_is_unreadable(_gh_output, monkeypatch):
    """An unreadable run list must not refuse: the lane check is the backstop,
    and here it agrees the dispatch is current."""
    with patch.object(dedupe_dispatch, "_run_meta",
                      side_effect=RuntimeError("gh 502")), \
         patch.object(dedupe_dispatch, "_current_lane", return_value="Planning"), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment"):
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=false" in _gh_output.read_text()


def test_cmd_plan_gate_receipt_failure_still_refuses(_gh_output):
    with patch.object(dedupe_dispatch, "_run_meta", return_value={}), \
         patch.object(dedupe_dispatch, "_current_lane", return_value="Green Light"), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment",
                      side_effect=RuntimeError("linear 500")):
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=true" in _gh_output.read_text()


def test_cmd_plan_gate_lets_a_rerun_through(_gh_output, monkeypatch):
    """limit_recovery.py brings a plan-stage limit death back by RE-RUNNING
    the original run when the card is past Planning exit. A re-run carries the
    original dispatch's creation time and trigger lane, so both checks would
    read a world that moved on since a dispatch nobody is making again —
    refusing it would break the one recovery that has no other route."""
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with patch.object(dedupe_dispatch, "_run_meta") as meta, \
         patch.object(dedupe_dispatch, "_current_lane") as lane, \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=false" in _gh_output.read_text()
    receipt.assert_not_called()
    meta.assert_not_called()
    lane.assert_not_called()


def test_a_first_attempt_is_not_a_rerun(_gh_output, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    with patch.object(dedupe_dispatch, "_run_meta", return_value={}), \
         patch.object(dedupe_dispatch, "_current_lane", return_value="Green Light"), \
         patch.object(dedupe_dispatch.linear_ops, "cmd_comment"):
        dedupe_dispatch.cmd_plan_gate("DRE-3244")
    assert "skip=true" in _gh_output.read_text()


def test_plan_gate_is_a_cli_verb():
    assert dedupe_dispatch.main(["dedupe_dispatch.py", "plan-gate"]) == 2
    with patch.object(dedupe_dispatch, "cmd_plan_gate") as gate:
        assert dedupe_dispatch.main(
            ["dedupe_dispatch.py", "plan-gate", "DRE-3244"]) == 0
    gate.assert_called_once_with("DRE-3244")


def test_the_two_gates_share_one_receipt_writer():
    """One `cmd_comment` call site in this module, so the act registry's
    single `🤖 Duplicate dispatch skipped` declaration keeps matching exactly
    one site (check_act_receipts.py refuses a declaration matching two)."""
    source = (ROOT / "scripts" / "dedupe_dispatch.py").read_text()
    assert source.count("linear_ops.cmd_comment(") == 1


# --------------------------------------------------------------------------
# plan.yml wiring: the guard runs before the step that made the move
# --------------------------------------------------------------------------
def _plan_yaml():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "plan.yml").read_text())


def _steps():
    return _plan_yaml()["jobs"]["plan"]["steps"]


def _step(name):
    matches = [s for s in _steps() if s.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step"
    return matches[0]


def test_plan_guard_step_exists_and_calls_the_script():
    step = _step("Duplicate-dispatch guard")
    assert step.get("id") == "dedupe"
    assert "dedupe_dispatch.py plan-gate" in step["run"]
    assert "client_payload.identifier" in step["run"]
    assert "trigger_state" in str(step.get("env", {})), (
        "the guard needs the lane the dispatch was fired for"
    )


def test_plan_guard_runs_after_the_card_gate_and_before_the_route_step():
    names = [s.get("name") or "" for s in _steps()]
    gate = names.index("Card-validation gate")
    dedupe = names.index("Duplicate-dispatch guard")
    route = names.index("Route — plan or activate")
    assert gate < dedupe < route, (
        "the guard must run after the card resolves and before the one step "
        "that writes Planning"
    )


def test_the_mover_itself_honors_the_skip():
    """`Route — plan or activate` is the step that wrote `DRE-3244 →
    Planning` at 21:53:02. It must not run in a refused dispatch — that is
    the criterion 'an epic left in Green Light stays there'."""
    cond = str(_step("Route — plan or activate").get("if", ""))
    assert re.search(r"steps\.dedupe\.outputs\.skip\s*!=\s*'true'", cond)


def test_every_step_the_card_gate_guards_also_honors_the_skip():
    """The six steps keyed on the card-validation gate are the roots every
    other step in this job descends from (through steps.shape / steps.route).
    A root without the skip condition is a leak that re-plans the epic."""
    leaked = [
        s.get("name")
        for s in _steps()
        if s.get("id") != "dedupe"  # the guard cannot skip on its own answer
        and "steps.gate.outputs.bounced" in str(s.get("if", ""))
        and not re.search(r"steps\.dedupe\.outputs\.skip\s*!=\s*'true'",
                          str(s.get("if", "")))
    ]
    assert leaked == [], f"steps that would still run on a refused dispatch: {leaked}"


def test_no_step_after_the_guard_runs_unconditionally():
    """A step with no `if` at all would run in a refused dispatch."""
    names = [s.get("name") or s.get("uses") or "" for s in _steps()]
    after = _steps()[names.index("Duplicate-dispatch guard") + 1:]
    assert [s.get("name") for s in after if not s.get("if")] == []


def test_the_seed_incident_is_cited_where_the_component_is_described():
    """DRE-3244 is the seed incident and it is named in the workflow's own
    comment block, beside the guard and the route step it explains."""
    text = (ROOT / ".github" / "workflows" / "plan.yml").read_text()
    assert "DRE-3244" in text
    assert "34281711446" in text, (
        "cite the run whose log carries the move, not just the card"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
