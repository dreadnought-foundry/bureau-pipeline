"""Layer 1's scope is a boundary at Planning exit, not a list of lanes (DRE-2754).

The seam this closes: `plan.yml` creates an epic's children into `Backlog`
(`:255`) and only afterwards moves the epic to Green Light (`:360`). So children
exist in a guarded lane while their epic is still pre-approval — and DRE-2725's
Layer 1 rule ("a verdict, and if the card has a parent epic, that epic is In
Progress") is satisfied by neither clause at that moment. The guard would bounce
the planner's own output to Intake, then to Triage on the second strike.

The operator's decision (2026-08-26) is option 3, restated as a boundary:
**the guard polices lanes downstream of where verdicts are produced.** Verdicts
are written at Planning exit, so `Intake`, `Planning` and `Green Light` sit
before any verdict can exist and there is nothing there to check.

These tests pin that as a DERIVED rule rather than an exception list:

  * the three pre-exit lanes are unpoliced and the five lanes DRE-2725's
    original matrix covers are still policed — the matrix extension the
    amendment on DRE-2725 requires;
  * the reproduction shape — children in `Backlog` while their epic is in
    `Planning` / `Green Light` — is not policed, and becomes policed the moment
    the epic passes Planning exit;
  * a lane inserted into the flow is classified by its POSITION, with no edit
    to the rule (add one before Planning exit → unpoliced; after → policed);
  * a lane the contract has never heard of raises rather than being guessed
    onto either side of the boundary.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_scope  # noqa: E402


# --- the flow, and where Planning exit sits in it ----------------------------


class TestFlowOrder:
    def test_planning_segment_precedes_the_work_segment(self):
        pos = {lane: i for i, lane in enumerate(lane_scope.LANE_FLOW)}
        assert pos["Intake"] < pos["Planning"] < pos["Green Light"]
        assert pos["Green Light"] < pos["Backlog"] < pos["Todo"]
        assert pos["Todo"] < pos["In Progress"] < pos["In Review"] < pos["Done"]

    def test_planning_exit_is_named_and_sits_between_green_light_and_backlog(self):
        # The hazard on the card: the boundary is only as good as the
        # definition of "where verdicts are produced". It is pinned to a
        # transition with a name, not to a description that drifts.
        assert lane_scope.PLANNING_EXIT_FROM == "Green Light"
        assert lane_scope.PLANNING_EXIT_TO == "Backlog"
        pos = lane_scope.LANE_FLOW.index
        assert pos(lane_scope.PLANNING_EXIT_TO) == pos(lane_scope.PLANNING_EXIT_FROM) + 1

    def test_the_retired_name_is_todays_board_name_for_green_light(self):
        # Wave 1.5 §5's rename has landed in code (DRE-2722) but not on the
        # board, which is a manual click waiting on the relay. Until it lands
        # the live board answers with the retired name, so both names must land
        # on the same side of the boundary.
        assert lane_scope.canonical_lane("Plan Review") == "Green Light"  # lane-rename-shim
        assert lane_scope.is_policed("Plan Review") is False  # lane-rename-shim


# --- the matrix, extended (DRE-2725's amendment) -----------------------------


class TestPolicedLanes:
    @pytest.mark.parametrize("lane", ["Intake", "Planning", "Green Light"])
    def test_pre_exit_lanes_are_not_policed(self, lane):
        assert lane_scope.is_policed(lane) is False

    @pytest.mark.parametrize(
        "lane", ["Backlog", "Todo", "In Progress", "In Review", "Done"]
    )
    def test_the_original_five_lanes_are_still_policed(self, lane):
        # DRE-2725's matrix, unchanged. Narrowing the scope must not quietly
        # unguard the lanes the wave exists to guard.
        assert lane_scope.is_policed(lane) is True

    def test_triage_is_not_policed(self):
        # Triage is where a three-times-returned card lands. Policing it would
        # bounce the guard's own destination straight back to Intake.
        assert lane_scope.is_policed("Triage") is False
        assert "Triage" in lane_scope.OFF_FLOW

    @pytest.mark.parametrize("lane", ["Canceled", "Duplicate"])
    def test_terminal_off_flow_lanes_are_not_policed(self, lane):
        assert lane_scope.is_policed(lane) is False

    def test_policed_and_unpoliced_partition_the_contract(self):
        policed = set(lane_scope.policed_lanes())
        unpoliced = set(lane_scope.unpoliced_lanes())
        assert policed.isdisjoint(unpoliced)
        assert policed | unpoliced == set(lane_scope.LANE_FLOW) | set(lane_scope.OFF_FLOW)

    def test_policed_lanes_are_derived_from_the_flow_not_enumerated(self):
        exit_at = lane_scope.LANE_FLOW.index(lane_scope.PLANNING_EXIT_TO)
        assert lane_scope.policed_lanes() == lane_scope.LANE_FLOW[exit_at:]


# --- the reproduction: an epic's children, created pre-approval ---------------


class TestPlannerChildrenAreNotBounced:
    # The third name is the board's CURRENT one for the second — a card sitting
    # there today must classify the same way.
    @pytest.mark.parametrize(
        "epic_lane", ["Planning", "Green Light", "Plan Review"]  # lane-rename-shim
    )
    def test_children_in_backlog_are_unpoliced_while_the_epic_is_pre_approval(
        self, epic_lane
    ):
        # Exactly the shape plan.yml produces: sub-issues created into Backlog
        # at :255 while the epic is still in Planning, then moved to Green
        # Light at :360. No verdict can exist for either card yet.
        assert (
            lane_scope.is_policed("Backlog", parent_epic_lane=epic_lane) is False
        )

    def test_children_become_policed_once_the_epic_passes_planning_exit(self):
        # Approval moves the epic to In Progress (DRE-2725 narrows
        # EPIC_ACTIVE_STATES to In Progress alone) and the second critic's
        # verdicts exist by then — so the guard applies from that moment.
        assert lane_scope.is_policed("Backlog", parent_epic_lane="In Progress") is True
        assert lane_scope.is_policed("Todo", parent_epic_lane="In Progress") is True

    def test_a_parentless_card_in_backlog_is_policed(self):
        # A one-off leaves Planning with its structural check done, so the
        # epic clause has nothing to say and Backlog's own rule applies.
        assert lane_scope.is_policed("Backlog") is True
        assert lane_scope.is_policed("Backlog", parent_epic_lane=None) is True

    def test_the_epic_clause_cannot_police_an_unpoliced_lane(self):
        # An active epic does not drag its children's pre-exit lanes into
        # scope — the card's own lane decides first.
        assert lane_scope.is_policed("Intake", parent_epic_lane="In Progress") is False


# --- one rule as the board changes -------------------------------------------


class TestBoundaryHoldsWhenTheBoardChanges:
    def test_a_lane_added_before_planning_exit_is_unpoliced_with_no_rule_edit(
        self, monkeypatch
    ):
        flow = lane_scope.LANE_FLOW
        widened = flow[: flow.index("Green Light")] + ("Plan Critique",) + flow[flow.index("Green Light") :]
        monkeypatch.setattr(lane_scope, "LANE_FLOW", widened)
        assert lane_scope.is_policed("Plan Critique") is False

    def test_a_lane_added_after_planning_exit_is_policed_with_no_rule_edit(
        self, monkeypatch
    ):
        flow = lane_scope.LANE_FLOW
        widened = flow + ("Released",)
        monkeypatch.setattr(lane_scope, "LANE_FLOW", widened)
        assert lane_scope.is_policed("Released") is True

    def test_an_unknown_lane_raises_rather_than_landing_on_a_side(self):
        # Console-honesty rule 1: never infer. A lane added to the board but
        # not to the contract must fail loudly, not default to unpoliced (the
        # guard silently stops working) or policed (it eats a new flow).
        with pytest.raises(lane_scope.UnknownLane):
            lane_scope.is_policed("Somewhere New")

    def test_an_unknown_parent_epic_lane_raises_too(self):
        with pytest.raises(lane_scope.UnknownLane):
            lane_scope.is_policed("Backlog", parent_epic_lane="Somewhere New")


# --- the rule names its boundary, and never describes it ---------------------


class TestTheRuleNamesPlanningExit:
    def test_module_names_planning_exit(self):
        src = lane_scope.__file__.replace(".pyc", ".py")
        with open(src) as f:
            text = f.read()
        assert "Planning exit" in text

    def test_module_never_defines_scope_as_pre_verdict_lanes(self):
        # The card's hazard clause: "pre-verdict lanes" is a description, and
        # descriptions drift. The boundary is named after the transition.
        src = lane_scope.__file__.replace(".pyc", ".py")
        with open(src) as f:
            text = f.read()
        assert "pre-verdict lanes" not in text.lower()
