"""RED-first: a revised plan's review is not a duplicate of the run that asked for it (DRE-4573).

THE INCIDENT (DRE-4467, 2026-09-21, the round-robin epic). The CEO approved it
at 20:03 PT. The second critic sent the plan back at 20:08 PT, round 1 of 2,
and the planner revised it. At 20:13:43 PT that same run asked for round 2
(`review_rerun.py dispatch --reason re-review`). The dispatch started run
35682382846 at 20:13:50 PT, and the planner's duplicate guard skipped it:

    🤖 Duplicate dispatch skipped: a planner run for DRE-4467 was already in
    flight when this dispatch arrived (run 35681764276)

Run 35681764276 was the run that SENT the request, still finishing its last
steps (it ended 20:13:55 PT). Round 2 never ran, the sweep held every ready
child with `plan-critic-post-unread`, and nothing asked again. An approved epic
built nothing, with no error anywhere.

It happens every time, not by bad luck: a re-review or review-retry is always
sent by a planner run on the same card, before that run ends, so
`in_flight_when_dispatched` always finds the sender.

THE FIX UNDER TEST. The request carries the sender's run id
(`client_payload.sent_by_run`), `plan.yml` hands it to the gate as
`SENT_BY_RUN`, and `plan_decide` drops that ONE run before deciding. Any other
planner run in flight on the card is still a duplicate, exactly as DRE-3409
decided.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_gate_sender_is_not_a_duplicate.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dedupe_dispatch  # noqa: E402
import linear_ops  # noqa: E402
import plan_run  # noqa: E402
import review_rerun as rr  # noqa: E402

# `gh api repos/dreadnought-foundry/agent-bureau/actions/runs/<id>` on the two
# runs, 2026-09-21.
SENDER = {"id": "35681764276", "created_at": "2026-09-22T03:03:47Z",
          "updated_at": "2026-09-22T03:13:55Z", "status": "completed"}
REVIEW_RUN_ID = "35682382846"
REVIEW_CREATED = "2026-09-22T03:13:50Z"
OTHER = {"id": "35682000001", "created_at": "2026-09-22T03:10:00Z",
         "updated_at": "2026-09-22T03:20:00Z", "status": "completed"}

CARD = {
    "id": "e5858663-ca90-4cc6-b47d-977fce94e89f",
    "identifier": "DRE-4467",
    "title": "[EPIC] The round robin spreads the work",
    "description": "",
    "labels": {"nodes": [{"name": "agent:planner"}, {"name": "repo:agent-bureau"}]},
}


def _in_flight(runs):
    return dedupe_dispatch.in_flight_when_dispatched(runs, REVIEW_RUN_ID,
                                                     REVIEW_CREATED)


# --- the incident, replayed -------------------------------------------------

def test_the_incident_run_really_was_in_flight_when_round_two_arrived():
    # The premise: without the sender id, the guard finds the sender.
    assert [r["id"] for r in _in_flight([SENDER])] == [SENDER["id"]]


def test_round_two_proceeds_when_its_only_sibling_is_the_run_that_asked_for_it():
    d = dedupe_dispatch.plan_decide("DRE-4467", "in progress", "In Progress",
                                    _in_flight([SENDER]),
                                    sent_by_run=SENDER["id"])
    assert d.skip is False, d.reason


def test_another_planner_run_in_flight_is_still_a_duplicate():
    d = dedupe_dispatch.plan_decide("DRE-4467", "in progress", "In Progress",
                                    _in_flight([SENDER, OTHER]),
                                    sent_by_run=SENDER["id"])
    assert d.skip is True
    assert OTHER["id"] in d.reason
    assert SENDER["id"] not in d.reason, "the sender is not the duplicate"


def test_a_sender_id_naming_some_other_run_excuses_nothing():
    d = dedupe_dispatch.plan_decide("DRE-4467", "in progress", "In Progress",
                                    _in_flight([SENDER]), sent_by_run="999")
    assert d.skip is True


def test_a_dispatch_with_no_sender_behaves_exactly_as_before():
    d = dedupe_dispatch.plan_decide("DRE-4467", "in progress", "In Progress",
                                    _in_flight([SENDER]))
    assert d.skip is True
    assert SENDER["id"] in d.reason


def test_the_sender_does_not_excuse_a_lane_that_has_moved_on():
    d = dedupe_dispatch.plan_decide("DRE-4467", "in progress", "Green Light",
                                    _in_flight([SENDER]),
                                    sent_by_run=SENDER["id"])
    assert d.skip is True


# --- the request carries its sender -----------------------------------------

class _FiredDispatch:
    """`plan_run.fire`'s gh call, with the payload it wrote kept."""

    def __init__(self):
        self.sent = None

    def __call__(self, argv, **kwargs):
        self.sent = json.loads(Path(argv[argv.index("--input") + 1]).read_text())
        return mock.Mock(returncode=0, stderr="", stdout="")


def _fire(**kwargs):
    gh = _FiredDispatch()
    with mock.patch.object(plan_run.subprocess, "run", gh):
        ok, err = plan_run.fire(CARD, "dreadnought-foundry/agent-bureau", **kwargs)
    assert ok, err
    return gh.sent["client_payload"]


def test_fire_carries_the_sender_when_given():
    payload = _fire(trigger_state=rr.TRIGGER_STATE_ACTIVATE,
                    reason=rr.REASON_RE_REVIEW, sent_by_run="35681764276")
    assert payload["sent_by_run"] == "35681764276"


def test_fire_without_a_sender_sends_no_such_key():
    assert "sent_by_run" not in _fire()
    assert "sent_by_run" not in _fire(trigger_state=rr.TRIGGER_STATE_ACTIVATE,
                                      reason=rr.REASON_REVIEW_RETRY)


def _dispatch_from_a_run(monkeypatch, run_id):
    if run_id is None:
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    gh = _FiredDispatch()
    with mock.patch.object(linear_ops, "gql", return_value={"issue": CARD}), \
            mock.patch.object(plan_run.subprocess, "run", gh):
        rc = rr.main(["dispatch", "--epic", "DRE-4467", "--repo", "o/n",
                      "--reason", rr.REASON_RE_REVIEW])
    assert rc == 0
    return gh.sent["client_payload"]


def test_review_rerun_dispatch_names_the_run_it_is_sent_from(monkeypatch):
    payload = _dispatch_from_a_run(monkeypatch, "35681764276")
    assert payload["sent_by_run"] == "35681764276"


def test_review_rerun_dispatch_from_a_terminal_names_no_sender(monkeypatch):
    # An operator re-sending the review by hand is not inside a planner run.
    assert "sent_by_run" not in _dispatch_from_a_run(monkeypatch, None)


# --- the gate reads it, end to end ------------------------------------------

@pytest.fixture()
def _gate_env(tmp_path, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", REVIEW_RUN_ID)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dreadnought-foundry/agent-bureau")
    monkeypatch.setenv("TRIGGER_STATE", "in progress")
    return out


def test_cmd_plan_gate_lets_round_two_through_with_the_sender_named(_gate_env, monkeypatch):
    monkeypatch.setenv("SENT_BY_RUN", SENDER["id"])
    with mock.patch.object(dedupe_dispatch, "_plan_siblings", return_value=[SENDER]), \
            mock.patch.object(dedupe_dispatch, "_current_lane", return_value="In Progress"), \
            mock.patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-4467")
    assert "skip=false" in _gate_env.read_text()
    receipt.assert_not_called()


def test_cmd_plan_gate_without_the_sender_still_skips(_gate_env, monkeypatch):
    monkeypatch.delenv("SENT_BY_RUN", raising=False)
    with mock.patch.object(dedupe_dispatch, "_plan_siblings", return_value=[SENDER]), \
            mock.patch.object(dedupe_dispatch, "_current_lane", return_value="In Progress"), \
            mock.patch.object(dedupe_dispatch.linear_ops, "cmd_comment"):
        dedupe_dispatch.cmd_plan_gate("DRE-4467")
    assert "skip=true" in _gate_env.read_text()


def test_plan_yml_hands_the_sender_to_the_gate():
    wf = yaml.safe_load((ROOT / ".github/workflows/plan.yml").read_text())
    steps = [s for job in wf["jobs"].values() for s in job.get("steps", [])]
    gate = [s for s in steps if "dedupe_dispatch.py plan-gate" in (s.get("run") or "")]
    assert gate, "plan.yml no longer runs the plan gate"
    for step in gate:
        assert step.get("env", {}).get("SENT_BY_RUN") == \
            "${{ github.event.client_payload.sent_by_run }}"


# --- the plan route's own ask for the second critic, in Planning (DRE-5277) --
#
# Under DRE-5268 the second critic reads the plan while the epic still sits in
# Planning, and the plan run asks for that review itself once the first critic
# has passed the plan: `review_rerun.py dispatch --reason review
# --trigger-state planning`, sent from inside the run that is still finishing.
# The guard already admits it — the sender is dropped (DRE-4573) and the lane
# it was fired for is the lane the epic is in — so these pin that rather than
# change it.

def test_the_plan_runs_own_review_ask_is_admitted_while_the_epic_is_in_planning():
    d = dedupe_dispatch.plan_decide("DRE-4467", rr.TRIGGER_STATE_REVIEW,
                                    "Planning", _in_flight([SENDER]),
                                    sent_by_run=SENDER["id"])
    assert d.skip is False, d.reason


def test_a_second_identical_ask_while_a_review_run_is_in_flight_is_a_duplicate():
    # The first ask's review run is OTHER, still going when the second ask was
    # created. Both asks name the same sender; the review run is not it.
    d = dedupe_dispatch.plan_decide("DRE-4467", rr.TRIGGER_STATE_REVIEW,
                                    "Planning", _in_flight([SENDER, OTHER]),
                                    sent_by_run=SENDER["id"])
    assert d.skip is True
    assert OTHER["id"] in d.reason


def test_the_review_ask_is_admitted_end_to_end_through_the_gate(monkeypatch, tmp_path):
    """The payload `review_rerun.py dispatch` really writes, read by the gate
    exactly as `plan.yml` hands it over (TRIGGER_STATE, SENT_BY_RUN)."""
    monkeypatch.setenv("GITHUB_RUN_ID", SENDER["id"])
    gh = _FiredDispatch()
    with mock.patch.object(linear_ops, "gql", return_value={"issue": CARD}), \
            mock.patch.object(plan_run.subprocess, "run", gh):
        rc = rr.main(["dispatch", "--epic", "DRE-4467", "--repo", "o/n",
                      "--reason", rr.REASON_REVIEW,
                      "--trigger-state", rr.TRIGGER_STATE_REVIEW])
    assert rc == 0
    sent = gh.sent["client_payload"]
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", REVIEW_RUN_ID)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("TRIGGER_STATE", sent["trigger_state"])
    monkeypatch.setenv("SENT_BY_RUN", sent["sent_by_run"])
    with mock.patch.object(dedupe_dispatch, "_plan_siblings", return_value=[SENDER]), \
            mock.patch.object(dedupe_dispatch, "_current_lane", return_value="Planning"), \
            mock.patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-4467")
    assert "skip=false" in out.read_text()
    receipt.assert_not_called()


# --- a parked epic stays parked (DRE-5277) ----------------------------------
#
# When an `agent:planner` card ENTERS Triage the relay dispatches a plan run
# with `trigger_state: triage`, and the route reads that as plan mode: back to
# Planning and a full re-plan. A park written by the sweep (DRE-5278, DRE-5286)
# has no in-flight run to cover it, so without this rule the park is undone
# within seconds. These drive the gate with a synthetic `triage` payload.

TRIAGE = "triage"
STALL_NOTE = "🛑 Planning stalled — parked for a person. (planning-stall-park)"


def test_the_stall_park_tag_is_the_declared_contract():
    assert dedupe_dispatch.STALL_PARK_TAG == "planning-stall-park"


def test_a_triage_dispatch_on_a_card_carrying_needs_human_is_skipped():
    d = dedupe_dispatch.plan_decide("DRE-4467", TRIAGE, "Triage", [],
                                    labels=["agent:planner", "needs-human"],
                                    newest_comment="")
    assert d.skip is True
    assert "Planning" in d.reason, "the reason says how it re-enters"


def test_a_triage_dispatch_whose_newest_comment_is_the_stall_park_is_skipped():
    d = dedupe_dispatch.plan_decide("DRE-4467", TRIAGE, "Triage", [],
                                    labels=["agent:planner"],
                                    newest_comment=STALL_NOTE)
    assert d.skip is True
    assert "Planning" in d.reason


def test_a_triage_dispatch_on_a_card_with_neither_proceeds():
    d = dedupe_dispatch.plan_decide("DRE-4467", TRIAGE, "Triage", [],
                                    labels=["agent:planner"],
                                    newest_comment="a person's comment")
    assert d.skip is False, d.reason


def test_the_lane_word_is_matched_however_the_relay_cases_it():
    d = dedupe_dispatch.plan_decide("DRE-4467", "Triage", "Triage", [],
                                    labels=["needs-human"])
    assert d.skip is True


def test_the_rule_never_touches_a_planning_dispatch():
    # A person moving a parked epic from Triage to Planning is how it re-enters.
    for state in ("planning", "Planning"):
        d = dedupe_dispatch.plan_decide("DRE-4467", state, "Planning", [],
                                        labels=["needs-human"],
                                        newest_comment=STALL_NOTE)
        assert d.skip is False, d.reason


def test_an_older_stall_park_comment_is_not_the_newest():
    # The rule reads the NEWEST comment only; the caller hands it that one.
    d = dedupe_dispatch.plan_decide("DRE-4467", TRIAGE, "Triage", [],
                                    labels=[], newest_comment="later comment")
    assert d.skip is False


class _Reader:
    """A stubbed Linear reader that records every call made of it."""

    def __init__(self, answer=None, error=None):
        self.calls = []
        self.answer = answer
        self.error = error

    def __call__(self, identifier):
        self.calls.append(identifier)
        if self.error:
            raise self.error
        return self.answer


def _gate_with_park_reads(_gate_env, monkeypatch, trigger, labels, newest):
    monkeypatch.setenv("TRIGGER_STATE", trigger)
    monkeypatch.delenv("SENT_BY_RUN", raising=False)
    with mock.patch.object(dedupe_dispatch, "_plan_siblings", return_value=[]), \
            mock.patch.object(dedupe_dispatch, "_current_lane",
                              return_value="Triage" if trigger == TRIAGE else "Planning"), \
            mock.patch.object(dedupe_dispatch, "_card_labels", labels), \
            mock.patch.object(dedupe_dispatch, "_newest_comment", newest), \
            mock.patch.object(dedupe_dispatch.linear_ops, "cmd_comment") as receipt:
        dedupe_dispatch.cmd_plan_gate("DRE-4467")
    return _gate_env.read_text(), receipt


def test_cmd_plan_gate_makes_no_park_read_for_a_planning_dispatch(_gate_env, monkeypatch):
    labels, newest = _Reader(["needs-human"]), _Reader(STALL_NOTE)
    written, _receipt = _gate_with_park_reads(_gate_env, monkeypatch, "planning",
                                              labels, newest)
    assert "skip=false" in written
    assert labels.calls == [] and newest.calls == []


def test_cmd_plan_gate_makes_no_park_read_for_an_in_progress_dispatch(_gate_env, monkeypatch):
    labels, newest = _Reader(["needs-human"]), _Reader(STALL_NOTE)
    _gate_with_park_reads(_gate_env, monkeypatch, "in progress", labels, newest)
    assert labels.calls == [] and newest.calls == []


def test_cmd_plan_gate_skips_a_triage_dispatch_on_a_parked_card(_gate_env, monkeypatch):
    labels, newest = _Reader(["needs-human"]), _Reader("")
    written, receipt = _gate_with_park_reads(_gate_env, monkeypatch, TRIAGE,
                                             labels, newest)
    assert "skip=true" in written
    assert labels.calls == ["DRE-4467"]
    receipt.assert_called_once()


def test_cmd_plan_gate_skips_a_triage_dispatch_after_a_stall_park(_gate_env, monkeypatch):
    labels, newest = _Reader([]), _Reader(STALL_NOTE)
    written, _receipt = _gate_with_park_reads(_gate_env, monkeypatch, TRIAGE,
                                              labels, newest)
    assert "skip=true" in written
    assert newest.calls == ["DRE-4467"]


def test_cmd_plan_gate_fails_open_on_an_unreadable_card(_gate_env, monkeypatch):
    labels = _Reader(error=RuntimeError("Linear 500"))
    newest = _Reader(error=RuntimeError("Linear 500"))
    written, receipt = _gate_with_park_reads(_gate_env, monkeypatch, TRIAGE,
                                             labels, newest)
    assert "skip=false" in written
    receipt.assert_not_called()


def test_the_park_readers_read_labels_and_the_newest_comment(monkeypatch):
    issue = {"labels": {"nodes": [{"name": "needs-human"}, {"name": "agent:planner"}]},
             "state": {"name": "Triage"}}
    with mock.patch.object(dedupe_dispatch.linear_ops, "get_issue",
                           return_value=issue), \
            mock.patch.object(dedupe_dispatch.linear_ops, "comment_bodies",
                              return_value=["old", STALL_NOTE]):
        assert dedupe_dispatch._card_labels("DRE-4467") == ["needs-human", "agent:planner"]
        assert dedupe_dispatch._newest_comment("DRE-4467") == STALL_NOTE
    with mock.patch.object(dedupe_dispatch.linear_ops, "comment_bodies",
                           return_value=[]):
        assert dedupe_dispatch._newest_comment("DRE-4467") == ""
