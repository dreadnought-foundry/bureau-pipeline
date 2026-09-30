"""RED-first tests: the sweep carries an epic it finds in Todo out of Todo (DRE-5347).

THE GAP. DRE-5316 stops the pipeline's own writers putting an epic in Todo, but
a person can still drag one there — in Linear, or from the console's epic move
menu. Nothing comes for it once it is there: the relay dispatches nothing for
an epic in Todo, and the promoter and the nudge loop skip epics. That is how
DRE-3621 spent seventeen days in Todo.

FIX UNDER TEST — `reconcile.carry_epics_out_of_todo()`, a phase of every full
sweep. For each Todo card `epic_todo_gate.is_epic_card` calls an epic (never
the `agent:planner` label), it reads `epic_todo_gate.lane_before_todo`, writes
In Progress when `epic_todo_gate.approved` and Planning otherwise, and posts
the refusal through `epic_todo_gate.post_refusal` with `carried_to` set to the
lane it just wrote. It runs before `promote_ready` fetches its candidates, so a
child's parent lane is read after the carry, never before it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_epic_carry.py -v
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import epic_todo_gate as gate  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402

RECONCILE = ROOT / "scripts" / "reconcile.py"
EPIC = "DRE-3621"


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so the carry recognises
    this suite's agent-bureau cards whatever the collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _card(identifier=EPIC, *, title="bureau-pipeline: the medic wakes on a conclusion",
          children=("child-1",), labels=("repo:agent-bureau", "agent:planner"),
          state="Todo"):
    """A card in the shape `_fetch_active_cards` reads: `children(first: 1)`
    and the comment window inline. The default is an epic by its children,
    wearing `agent:planner` like every card that has been through Planning."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "the plan",
        "updatedAt": "2026-09-30T00:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": c} for c in children]},
        "comments": {"nodes": []},
    }


def _board(cards):
    """`active_cards` filtering by lane as the real one does."""

    def active_cards(states=reconcile.SWEEP_STATES):
        return [c for c in cards if c["state"]["name"] in states]

    return active_cards


class _Linear:
    """The write layer the carry calls, recording every write. Comments posted
    are kept, so a second sweep sees the first one's refusal the way the real
    `count_comments` would."""

    def __init__(self, before: dict, *, unreadable=(), failing_writes=()):
        self.before = before
        self.unreadable = set(unreadable)
        self.failing_writes = set(failing_writes)
        self.states: list[tuple] = []
        self.comments: list[tuple] = []

    def gql(self, query, variables=None):
        ident = (variables or {}).get("id")
        if ident in self.unreadable:
            raise RuntimeError("Linear answered 500")
        lane = self.before.get(ident)
        nodes = [{"fromState": {"name": "Backlog"}, "toState": {"name": "Planning"}}]
        if lane is not None:
            # Newest first, the order `history(first: n)` answers in.
            nodes = [{"fromState": {"name": lane}, "toState": {"name": "Todo"}}] + nodes
        return {"issue": {"history": {"nodes": nodes}}}

    def cmd_state(self, identifier, lane, *flags):
        if identifier in self.failing_writes:
            raise RuntimeError("Linear refused the write")
        self.states.append((identifier, lane))

    def cmd_comment(self, identifier, body):
        self.comments.append((identifier, body))

    def count_comments(self, identifier, prefix):
        return sum(
            1 for ident, body in self.comments
            if ident == identifier and body.startswith(prefix)
        )


def _sweep(cards, lin: _Linear):
    reconcile._write_failures.clear()
    with mock.patch.object(
        reconcile, "active_cards", side_effect=_board(cards)
    ), mock.patch.object(
        reconcile.linear_ops, "gql", side_effect=lin.gql
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_state", side_effect=lin.cmd_state
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment", side_effect=lin.cmd_comment
    ), mock.patch.object(
        reconcile.linear_ops, "count_comments", side_effect=lin.count_comments
    ):
        reconcile.carry_epics_out_of_todo()
    failures = list(reconcile._write_failures)
    reconcile._write_failures.clear()
    return failures


# --------------------------------------------------------------------------
# The carry
# --------------------------------------------------------------------------
def test_an_approved_epic_in_todo_is_carried_back_to_in_progress():
    lin = _Linear({EPIC: "In Progress"})
    assert _sweep([_card()], lin) == []
    assert lin.states == [(EPIC, "In Progress")]
    assert len(lin.comments) == 1
    ident, body = lin.comments[0]
    assert ident == EPIC
    assert body.splitlines()[0] == gate.opener(EPIC, "In Progress")
    assert "It was carried to In Progress." in body


def test_an_epic_dragged_from_green_light_is_carried_to_planning():
    """A Green Light to Todo drag is not approval (DRE-5316), so the plan is
    re-planned — and the comment names Green Light as where approval happens
    and Planning as where the card now is."""
    lin = _Linear({EPIC: "Green Light"})
    assert _sweep([_card()], lin) == []
    assert lin.states == [(EPIC, "Planning")]
    assert len(lin.comments) == 1
    body = lin.comments[0][1]
    assert body.splitlines()[0] == gate.opener(EPIC, "Green Light")
    assert "approval in Green Light" in body
    assert "It was carried to Planning" in body
    assert "carried to In Progress" not in body


def test_an_unreadable_history_reads_as_not_approved():
    lin = _Linear({EPIC: "In Progress"}, unreadable={EPIC})
    assert _sweep([_card()], lin) == []
    assert lin.states == [(EPIC, "Planning")]
    assert len(lin.comments) == 1
    body = lin.comments[0][1]
    assert body.splitlines()[0] == gate.opener(EPIC, None)
    assert "approval in Green Light" in body
    assert "It was carried to Planning" in body


def test_an_epic_by_title_alone_is_carried():
    lin = _Linear({EPIC: "In Progress"})
    _sweep([_card(title="[EPIC] bureau-pipeline: a plan", children=())], lin)
    assert lin.states == [(EPIC, "In Progress")]


def test_a_one_off_wearing_agent_planner_is_left_alone():
    """The label says the planner owns the card, and every classified one-off
    wears it (DRE-3038/DRE-3044). A one-off in Todo is waiting for its run."""
    lin = _Linear({"DRE-5400": "Backlog"})
    one_off = _card("DRE-5400", title="bureau-pipeline: a small fix", children=())
    assert _sweep([one_off], lin) == []
    assert lin.states == []
    assert lin.comments == []


def test_a_second_sweep_posts_no_second_comment():
    """The epic is dragged back to Todo after the first carry: the second
    sweep carries it again, and the refusal already on the card is not
    repeated."""
    lin = _Linear({EPIC: "In Progress"})
    _sweep([_card()], lin)
    _sweep([_card()], lin)
    assert lin.states == [(EPIC, "In Progress"), (EPIC, "In Progress")]
    assert len(lin.comments) == 1


def test_an_epic_in_another_lane_is_not_read():
    lin = _Linear({EPIC: "Green Light"})
    _sweep([_card(state="In Progress"), _card("DRE-3622", state="Planning")], lin)
    assert lin.states == []
    assert lin.comments == []


def test_a_card_handed_over_outside_todo_is_never_written():
    """The carry writes on the strength of the lane its read reports, so it
    asks that lane itself: a read that hands it an In Progress epic — a stub
    that ignores the lane asked for, or a snapshot gone stale — moves nothing."""
    lin = _Linear({EPIC: "Green Light"})
    reconcile._write_failures.clear()
    with mock.patch.object(
        reconcile, "active_cards", return_value=[_card(state="In Progress")]
    ), mock.patch.object(
        reconcile.linear_ops, "gql", side_effect=lin.gql
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_state", side_effect=lin.cmd_state
    ), mock.patch.object(
        reconcile.linear_ops, "cmd_comment", side_effect=lin.cmd_comment
    ), mock.patch.object(
        reconcile.linear_ops, "count_comments", side_effect=lin.count_comments
    ):
        reconcile.carry_epics_out_of_todo()
    assert lin.states == []
    assert lin.comments == []


def test_another_repos_epic_is_left_to_its_own_sweep():
    lin = _Linear({EPIC: "In Progress"})
    _sweep([_card(labels=("repo:portico", "agent:planner"))], lin)
    assert lin.states == []
    assert lin.comments == []


def test_a_write_that_fails_posts_no_refusal_and_the_sweep_goes_on():
    """The refusal names the lane the card is in; a write that did not land
    must not be described as one that did. The failure is recorded, so the
    sweep ends red, and the next epic is still carried."""
    lin = _Linear({EPIC: "In Progress", "DRE-3700": "Green Light"},
                  failing_writes={EPIC})
    failures = _sweep([_card(), _card("DRE-3700")], lin)
    assert len(failures) == 1 and EPIC in failures[0]
    assert lin.states == [("DRE-3700", "Planning")]
    assert [ident for ident, _ in lin.comments] == ["DRE-3700"]


# --------------------------------------------------------------------------
# The contract: DRE-5316's rule, read — never restated
# --------------------------------------------------------------------------
def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(RECONCILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"reconcile.py defines no {name}()")


def test_the_carry_reads_the_gate_and_defines_no_second_copy():
    carry = _function("carry_epics_out_of_todo")
    called = {
        node.func.attr
        for node in ast.walk(carry)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "epic_todo_gate"
    }
    assert {"is_epic_card", "lane_before_todo", "approved", "post_refusal"} <= called
    tree = ast.parse(RECONCILE.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not defined & {"is_epic_card", "lane_before_todo", "approved", "post_refusal"}


def test_the_two_destinations_are_literals_the_lane_check_reads():
    carry = _function("carry_epics_out_of_todo")
    lanes = {
        node.args[1].value
        for node in ast.walk(carry)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cmd_state"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    }
    assert lanes == {"In Progress", "Planning"}
    first, last = carry.lineno, carry.end_lineno
    found = {
        w.lane for w in ready_lane_writers.writes()
        if w.writer == "reconcile.py"
        and w.where.startswith("scripts/reconcile.py:")
        and first <= int(w.where.rsplit(":", 1)[1]) <= last
    }
    assert found == {"In Progress", "Planning"}
    assert ready_lane_writers.writer_problems() == []


# --------------------------------------------------------------------------
# The order: the carry runs before the promoter reads its candidates
# --------------------------------------------------------------------------
_FULL_SWEEP_PHASES = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "flag_unlanded_work", "flag_stranded_fixes", "fix_approved_but_red",
    "retry_dead_fix_runs", "redispatch_standing_verdicts", "recover_limit_deaths",
    "restart_answered_blockers", "card_dependabot_prs", "review_dependabot_prs",
    "recover_crashed_reviews", "report_fleet_reviewer_outage",
    "check_dependabot_capacity", "settle_repair_cards", "report_intake_depth",
    "advance_urgent_intake", "serve_planner_line", "close_finished_epics",
    "move_hand_built_to_review", "report_break_glass", "rereview_watch",
    "report_fix_concurrency", "report_evicted_fix_runs",
)


def _recorded_sweep(**kwargs) -> list[str]:
    order: list[str] = []
    stubs = {name: mock.MagicMock() for name in _FULL_SWEEP_PHASES}
    stubs.update(
        flag_stranded=mock.MagicMock(return_value=set()),
        repair_frozen_planning_holds=mock.MagicMock(return_value=set()),
        active_cards=mock.MagicMock(return_value=[]),
        report_epic_growth=mock.MagicMock(return_value=[]),
        merged_card_scope=mock.MagicMock(return_value=None),
        carry_epics_out_of_todo=mock.MagicMock(
            side_effect=lambda: order.append("carry")),
        backlog_children=mock.MagicMock(
            side_effect=lambda *a, **k: order.append("candidates") or []),
    )
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    with mock.patch.multiple(reconcile, **stubs):
        try:
            reconcile.main(**kwargs)
        except SystemExit:  # a red sweep still ran every phase
            pass
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    return order


def test_the_carry_runs_before_the_promoter_fetches_its_candidates():
    order = _recorded_sweep()
    assert "carry" in order and "candidates" in order
    assert order.index("carry") < order.index("candidates")


def test_the_promotion_only_pass_does_not_carry():
    """`--promote-only` runs on every merge in the fleet and is the dependency
    gate alone; the carry is a full-sweep phase."""
    order = _recorded_sweep(promote_only=True)
    assert "carry" not in order


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
