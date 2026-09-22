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
