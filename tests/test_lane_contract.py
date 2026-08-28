"""The lane contract is DATA, and everything else reads it (DRE-2726).

A contract written in English is not enforcement. `config/lane-contract.json`
declares, per lane, the entrance condition, the exit condition, the permitted
writers and the evidence that justifies occupancy — plus, per clause, the
`enforced_from` phase that says whether the harness may assert it yet.

These tests pin the three consumers named on the card:

  * the FILE itself — every live lane carries all four clauses, and every
    clause carries a phase the contract's own phase order knows;
  * the GUARD (`lane_scope.py`) — it derives its flow, its off-flow lanes and
    its rename aliases FROM the contract instead of carrying a second copy;
  * the SWEEP (`reconcile.py`) — its staleness windows come from the same file,
    so a lane's stall budget cannot drift from the lane's own declaration.

The lane cleanup is pinned here too: `In Design Review` and `In QA` are no
longer lanes. They are `retiring` entries, which is a different thing — the
board still carries them until the workspace apply archives them, and the
conformance rules in test_lane_contract_conformance.py are what make that
transition finish rather than linger.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402
import lane_scope  # noqa: E402
import reconcile  # noqa: E402


CONTRACT = os.path.join(
    os.path.dirname(__file__), "..", "config", "lane-contract.json"
)

# The board after the cleanup this card ships: thirteen states become eleven
# lanes. Written out ONCE, here, as the thing the tests below compare against —
# every other list in the pipeline is derived from the contract file.
LIVE_LANES = (
    "Intake",
    "Planning",
    "Green Light",
    "Backlog",
    "Todo",
    "In Progress",
    "In Review",
    "Done",
    "Triage",
    "Canceled",
    "Duplicate",
)

CLAUSE_KINDS = ("entrance", "exit", "writers", "evidence")


class TestTheFileExists:
    def test_the_contract_is_json_the_stdlib_can_read(self):
        # JSON, not YAML, on purpose: linear_ops.py imports lane_scope.py and
        # runs on every product repo's agent job with no pip install. A PyYAML
        # import there would be a new runtime dependency on the hot path.
        with open(CONTRACT, encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc["lanes"], "the contract declares no lanes"

    def test_every_live_lane_declares_all_four_clauses(self):
        for lane in lane_contract.lanes(status="live"):
            missing = [k for k in CLAUSE_KINDS if k not in lane["clauses"]]
            assert not missing, f"{lane['name']} is missing clause(s) {missing}"

    def test_every_clause_carries_an_enforced_from_phase(self):
        # The amendment's load-bearing field. Without it the harness either
        # fails red on the whole board for three phases, or silently asserts
        # only the subset that exists.
        order = lane_contract.phase_order()
        for clause in lane_contract.clauses():
            assert clause.enforced_from, f"{clause.id} carries no enforced_from"
            assert clause.enforced_from in order, (
                f"{clause.id} is enforced_from {clause.enforced_from!r}, which "
                f"is not one of the contract's phases {order}"
            )

    def test_every_clause_says_what_it_requires_in_english(self):
        for clause in lane_contract.clauses():
            assert clause.text.strip(), f"{clause.id} has no text"

    def test_the_current_phase_is_one_of_the_declared_phases(self):
        assert lane_contract.current_phase() in lane_contract.phase_order()

    def test_a_phase_at_or_before_the_current_one_has_shipped(self):
        order = lane_contract.phase_order()
        current = order.index(lane_contract.current_phase())
        for i, phase in enumerate(order):
            assert lane_contract.phase_has_shipped(phase) is (i <= current)


class TestLaneCleanup:
    def test_in_design_review_is_not_a_lane(self):
        assert "In Design Review" not in lane_contract.lane_names(status="live")

    def test_in_qa_is_not_a_lane(self):
        assert "In QA" not in lane_contract.lane_names(status="live")

    def test_both_are_recorded_as_retiring_with_the_reason_and_the_board_step(self):
        retiring = {l["name"]: l for l in lane_contract.lanes(status="retiring")}
        assert set(retiring) == {"In Design Review", "In QA"}
        for lane in retiring.values():
            assert lane["retired_by"] == "DRE-2726"
            assert lane["reason"].strip()
            # The board half is somebody else's repo (agent-bureau's
            # config/linear-workspace.json). The contract names the step so the
            # transition is a scheduled act, not a hope.
            assert lane["board_action"].strip()

    def test_the_eleven_live_lanes_are_exactly_the_board_after_cleanup(self):
        assert set(lane_contract.lane_names(status="live")) == set(LIVE_LANES)

    def test_the_merged_lane_keeps_the_longer_stall_window(self):
        # In QA had 120 minutes, In Review 60. The fold keeps 120 — a shorter
        # window on the merged lane would re-nudge reviews that are simply
        # still running.
        assert lane_contract.stale_minutes()["In Review"] == 120


class TestTheGuardReadsTheFile:
    """`lane_scope.py` is the guard's copy of the rules — it must not be one."""

    def test_the_flow_is_the_contracts_flow_in_the_contracts_order(self):
        flow = tuple(
            l["name"]
            for l in lane_contract.lanes(status="live")
            if l["segment"] in ("planning", "work")
        )
        assert lane_scope.LANE_FLOW == flow

    def test_off_flow_lanes_and_their_reasons_come_from_the_contract(self):
        expected = {
            l["name"]: l["off_flow_reason"]
            for l in lane_contract.lanes(status="live")
            if l["segment"] == "off-flow"
        }
        assert lane_scope.OFF_FLOW == expected

    def test_the_rename_aliases_come_from_the_contract(self):
        assert lane_scope.LANE_ALIASES == lane_contract.aliases()

    def test_planning_exit_is_named_by_the_contract(self):
        assert lane_scope.PLANNING_EXIT_FROM == lane_contract.planning_exit()[0]
        assert lane_scope.PLANNING_EXIT_TO == lane_contract.planning_exit()[1]

    def test_the_guard_carries_no_second_copy_of_the_lane_names(self):
        # The whole point of the card: the document and the enforcement are one
        # object. A lane name spelled out in the guard's source is a second
        # copy, and a second copy drifts.
        source = open(
            os.path.join(os.path.dirname(__file__), "..", "scripts", "lane_scope.py"),
            encoding="utf-8",
        ).read()
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        # The docstring is prose ABOUT the boundary and may name it; the code
        # below must not enumerate lanes.
        body = code.split('"""', 2)[-1]
        for name in ("Intake", "Backlog", "Todo", "In Progress", "Done", "Triage"):
            assert f'"{name}"' not in body, (
                f"lane_scope.py still hardcodes {name!r} — it must read the "
                "contract file instead"
            )


class TestTheSweepReadsTheFile:
    def test_stale_windows_come_from_the_contract(self):
        assert reconcile.STALE_MINUTES == lane_contract.stale_minutes()

    def test_the_sweep_no_longer_knows_the_folded_lane(self):
        assert "In QA" not in reconcile.STALE_MINUTES
        assert "In QA" not in reconcile.SWEEP_STATES

    def test_every_swept_state_is_a_live_lane(self):
        for state in reconcile.SWEEP_STATES:
            assert state in lane_contract.lane_names(status="live")


class TestLoaderRefusesToGuess:
    def test_an_unknown_lane_raises_rather_than_defaulting(self):
        with pytest.raises(lane_contract.UnknownLane):
            lane_contract.lane("In Design Review", status="live")

    def test_a_contract_with_a_clause_missing_enforced_from_is_rejected(self, tmp_path):
        doc = json.loads(open(CONTRACT, encoding="utf-8").read())
        doc["lanes"][0]["clauses"]["entrance"].pop("enforced_from")
        broken = tmp_path / "lane-contract.json"
        broken.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(lane_contract.ContractError):
            lane_contract.load(str(broken))
