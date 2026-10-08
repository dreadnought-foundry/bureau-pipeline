"""The sweep's two dead-run caps hand the card to the planner once, then hold
with a stamp (DRE-6186, epic DRE-6172).

`main()` used to park a card in Backlog with the bare `needs-human` label at
the cap, in two inline blocks — the In Progress no-PR branch and the In Review
no-PR branch. DRE-6178 gave `dead_run.decide` the planner-first answer and
`dead_run.split_tried` the one reading of whether this budget already tried
the planner. Both caps now reach ONE helper, `hand_dead_run_to_planner`, which
asks those two and acts on the answer:

  * `replan` — the decision's body (it opens `hold.DEAD_SPLIT_MARK`) is posted
    and the card advances to Planning from the lane it is in. No label.
  * `hold` — `hold.apply(…, "dead-run-cap", "none", "reconcile.py")`, the
    `--park` move to Backlog, and the lane's `🚨 held-for-human` receipt with
    its anchor phrase unchanged.

The death the sweep sees is always the silent class, so `decide` is asked with
no class flag; the earlier deaths' first lines ride on the hand-off for the
planner.

Run: python3 -m pytest tests/test_hold_sweep_dead_run.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import hold  # noqa: E402
import reconcile  # noqa: E402

CAP_STAMP = "🔒 hold: reason=dead-run-cap at=none lifts=unpark-marker by=reconcile.py"
FILES = "scripts/reconcile.py, config/holds.json"
ANCHORS = {
    "In Progress": "agent keeps dying with no PR",
    reconcile.REVIEW_LANE: f"{reconcile.REVIEW_LANE} with no PR after",
}


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._door_sourced.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._door_sourced.clear()


def _silent(n: int) -> str:
    return dead_run.decide(n - 1).comments[0]


def _credential(n: int, status: str) -> str:
    return dead_run.decide(n - 1, credential_expiry=True, push_status=status,
                           artifact=f"rescue-DRE-7100-{n}.patch").comments[0]


def _at_cap() -> list[str]:
    """The thread of a card at the cap: one requeue receipt per spent strike."""
    return [_silent(n) for n in range(1, reconcile.REQUEUE_CAP + 1)]


def _card(state="In Progress", identifier="DRE-7100", bodies=()):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "a card that keeps dying",
        "description": f"Some work.\n\n**Files:** {FILES}\n\n## Acceptance criteria\n- [ ] x",
        "updatedAt": "2026-10-01T00:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": "repo:agent-bureau"}]},
        "comments": {"nodes": [{"body": b} for b in bodies]},
    }


class _Linear:
    """Every `linear_ops` write the helper can make, on one ordered recorder,
    over a thread the posted comments join — so a count read after a write
    sees it."""

    def __init__(self, thread=(), live_lane=None):
        self.thread = list(thread)
        self.writes: list[tuple] = []
        self.state_guards: list[dict] = []
        self.live_lane = live_lane

    def patches(self, card):
        def comment(ident, body):
            self.writes.append(("comment", ident, body))
            self.thread.append(body)

        def get_issue(ident, *, fresh=False):
            return {"identifier": ident,
                    "state": {"name": self.live_lane or card["state"]["name"]},
                    "labels": card["labels"]}

        return [
            patch.object(reconcile.linear_ops, "cmd_comment", side_effect=comment),
            patch.object(reconcile.linear_ops, "add_label", side_effect=lambda i, label:
                         self.writes.append(("label", i, label))),
            patch.object(reconcile.linear_ops, "cmd_advance",
                         side_effect=lambda i, to, frm, *f, **k:
                         self.writes.append(("advance", i, to, frm))),
            patch.object(reconcile.linear_ops, "cmd_state",
                         side_effect=lambda i, to, *f, **k: (
                             self.writes.append(("state", i, to, *f)),
                             self.state_guards.append(k))[-1]),
            patch.object(reconcile.linear_ops, "get_issue", side_effect=get_issue),
            patch.object(reconcile.linear_ops, "_thread",
                         side_effect=lambda i, *a, **k: [{"body": b} for b in self.thread]),
        ]

    def run(self, card, dead, bodies=None, extra=()):
        bodies = list(self.thread) if bodies is None else bodies
        cms = self.patches(card) + list(extra)
        for cm in cms:
            cm.start()
        try:
            reconcile.hand_dead_run_to_planner(card, dead, bodies)
        finally:
            for cm in reversed(cms):
                cm.stop()

    def kinds(self):
        return [w[0] for w in self.writes]

    def comments(self):
        return [w[2] for w in self.writes if w[0] == "comment"]


# --------------------------------------------------------------------------- #
# the helper: hand off once, hold the second time                              #
# --------------------------------------------------------------------------- #


class TestTheFirstStrikeHandsTheCardToPlanning:
    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    def test_an_untried_budget_advances_to_planning_under_the_split_receipt(self, lane):
        linear = _Linear(_at_cap())
        card = _card(lane)
        linear.run(card, reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "split_tried", return_value=False)])
        assert linear.kinds() == ["comment", "advance"]
        receipt = linear.comments()[0]
        assert receipt.startswith(hold.DEAD_SPLIT_MARK)
        assert FILES in receipt, "the receipt quotes the card's **Files:** line"
        assert f"dead run {reconcile.REQUEUE_CAP + 1}/{reconcile.REQUEUE_CAP + 1}" in receipt
        assert linear.writes[1] == ("advance", "DRE-7100", "Planning", lane)

    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    def test_the_hand_off_writes_no_label_and_never_parks(self, lane):
        linear = _Linear(_at_cap())
        linear.run(_card(lane), reconcile.REQUEUE_CAP)
        assert "label" not in linear.kinds()
        assert not [w for w in linear.writes if w[0] == "state"]
        assert not any(c.startswith(hold.STAMP_PREFIX) for c in linear.comments())


class TestTheSecondStrikeHolds:
    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    def test_a_tried_budget_parks_with_the_label_the_stamp_and_the_anchor(self, lane):
        linear = _Linear(_at_cap())
        linear.run(_card(lane), reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "split_tried", return_value=True)])
        assert linear.kinds() == ["label", "comment", "state", "comment"]
        assert linear.writes[0] == ("label", "DRE-7100", reconcile.HOLD_LABEL)
        assert linear.writes[1][2] == CAP_STAMP
        assert linear.writes[2] == ("state", "DRE-7100", "Backlog", "--park")
        receipt = linear.writes[3][2]
        assert receipt.startswith("🚨 held-for-human:")
        assert ANCHORS[lane] in receipt
        assert not any(c.startswith(hold.DEAD_SPLIT_MARK) for c in linear.comments())

    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    @pytest.mark.parametrize("recorded", [False, True], ids=["hung", "recorded"])
    def test_the_same_card_dying_once_more_after_the_hand_off_holds(self, lane, recorded):
        """The scenario, on the real `split_tried`: at the cap the card is
        handed off; the planner sends it back as one piece and it dies again —
        hung, which only the sweep sees, or with a requeue receipt, which still
        counts — and the second strike is the hold."""
        linear = _Linear(_at_cap())
        card = _card(lane)

        def count():
            with linear.patches(card)[-1]:
                return reconcile.linear_ops.count_comments(
                    "DRE-7100", reconcile.DEAD_TAG, since=reconcile.RESET_TAG)

        dead = count()
        linear.run(card, dead)
        assert linear.kinds() == ["comment", "advance"]
        if recorded:
            linear.thread.append(_silent(dead + 1))
        linear.writes.clear()
        dead = count()
        assert dead == reconcile.REQUEUE_CAP + recorded, "the next death still counts"
        linear.run(card, dead)
        assert linear.kinds() == ["label", "comment", "state", "comment"]
        assert CAP_STAMP in linear.comments()
        assert ANCHORS[lane] in linear.comments()[-1]
        assert f"after {dead} requeues" in linear.comments()[-1]

    def test_a_reset_marker_makes_the_planner_tryable_once_more(self):
        handed = _at_cap() + [
            dead_run.dead_split_comment(reconcile.REQUEUE_CAP + 1, footprint=FILES)]
        linear = _Linear(handed + [f"{reconcile.RESET_TAG} — unparked"] + _at_cap())
        linear.run(_card(), reconcile.REQUEUE_CAP)
        assert linear.kinds() == ["comment", "advance"]


class TestTheReadDoorRunsFirst:
    @pytest.mark.parametrize("tried", [False, True])
    def test_a_door_card_that_left_its_lane_gets_no_write(self, tried):
        card = _card()
        reconcile._door_sourced.add(card["identifier"])
        linear = _Linear(_at_cap(), live_lane="Done")
        linear.run(card, reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "split_tried", return_value=tried)])
        assert linear.writes == []

    def test_a_door_card_still_in_its_lane_is_parked_from_lane_conditionally(self):
        card = _card()
        reconcile._door_sourced.add(card["identifier"])
        linear = _Linear(_at_cap())
        linear.run(card, reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "split_tried", return_value=True)])
        assert ("state", "DRE-7100", "Backlog", "--park") in linear.writes
        assert linear.state_guards == [{"expect": ("In Progress",), "labels_absent": ()}]


# --------------------------------------------------------------------------- #
# the decision is dead_run's, asked for the silent class                       #
# --------------------------------------------------------------------------- #


class TestTheDecisionIsDeadRuns:
    def test_decide_is_asked_with_no_class_flag_and_the_death_lines(self):
        bodies = _at_cap()
        linear = _Linear(bodies)
        real = dead_run.decide
        spy = MagicMock(side_effect=real)
        linear.run(_card(), reconcile.REQUEUE_CAP, bodies,
                   extra=[patch.object(reconcile.dead_run, "decide", spy)])
        spy.assert_called_once()
        args, kwargs = spy.call_args
        assert args == (reconcile.REQUEUE_CAP,)
        assert not kwargs.get("is_error")
        assert not kwargs.get("credential_expiry")
        assert kwargs["deaths"] == dead_run.death_lines(bodies)
        assert kwargs["split_tried"] is dead_run.split_tried(bodies) is False
        assert kwargs["footprint"] == FILES
        assert kwargs["run_url"] == ""

    def test_the_hand_off_quotes_every_earlier_death_in_order(self):
        """Two refused pushes and one silent death since the reset: the
        planner reads what each was before it cuts anything."""
        bodies = ["🪦 dead-run-requeue: an old death",
                  f"{reconcile.RESET_TAG} — unparked",
                  _credential(1, "403"), _credential(2, "400"), _silent(3)]
        linear = _Linear(bodies)
        linear.run(_card(), reconcile.REQUEUE_CAP, bodies)
        receipt = linear.comments()[0]
        quoted = [dead_run._quoted(line) for line in dead_run.death_lines(bodies)]
        assert len(quoted) == 3
        assert "HTTP 403" in quoted[0] and "HTTP 400" in quoted[1]
        assert "no PR and no blocker note" in quoted[2]
        at = [receipt.index(q) for q in quoted]
        assert at == sorted(at), "the earlier deaths are quoted oldest first"
        assert "an old death" not in receipt, "a death before the reset is not this budget's"

    def test_a_hold_answer_parks(self):
        linear = _Linear(_at_cap())
        held = dead_run.Decision("hold", ["🚨 held-for-human (decided elsewhere)"])
        linear.run(_card(), reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "decide", return_value=held)])
        assert ("state", "DRE-7100", "Backlog", "--park") in linear.writes
        assert "advance" not in linear.kinds()

    def test_a_replan_answer_advances(self):
        linear = _Linear(_at_cap())
        body = f"{hold.DEAD_SPLIT_MARK} decided elsewhere"
        replan = dead_run.Decision("replan", [body])
        linear.run(_card(reconcile.REVIEW_LANE), reconcile.REQUEUE_CAP,
                   extra=[patch.object(reconcile.dead_run, "split_tried", return_value=True),
                          patch.object(reconcile.dead_run, "decide", return_value=replan)])
        assert linear.writes == [
            ("comment", "DRE-7100", body),
            ("advance", "DRE-7100", "Planning", reconcile.REVIEW_LANE)]

    def test_nothing_else_in_reconcile_reads_the_split_mark_to_decide(self):
        tree = ast.parse((ROOT / "scripts" / "reconcile.py").read_text(encoding="utf-8"))
        marks: list[int] = []
        tried: list[str] = []

        def visit(node, scope):
            for child in ast.iter_child_nodes(node):
                inner = child.name if isinstance(child, ast.FunctionDef) else scope
                if isinstance(child, ast.Attribute) and child.attr == "DEAD_SPLIT_MARK":
                    marks.append(child.lineno)
                if isinstance(child, ast.Attribute) and child.attr == "split_tried":
                    tried.append(scope)
                visit(child, inner)

        visit(tree, "<module>")
        assert marks == [], f"reconcile.py reads hold.DEAD_SPLIT_MARK at lines {marks}"
        assert tried == ["hand_dead_run_to_planner"]
        assert "DEAD_SPLIT_MARK" not in (ROOT / "scripts" / "reconcile.py").read_text(
            encoding="utf-8").replace("hold.DEAD_SPLIT_MARK", "")


class TestTheHandOffSpendsNoBudget:
    def test_the_receipt_carries_neither_budget_tag(self):
        linear = _Linear(_at_cap())
        linear.run(_card(), reconcile.REQUEUE_CAP)
        receipt = linear.comments()[0]
        assert reconcile.DEAD_TAG not in receipt
        assert reconcile.RESET_TAG not in receipt

    def test_the_count_is_the_same_before_and_after_it(self):
        linear = _Linear(_at_cap())
        card = _card()

        def count():
            with linear.patches(card)[-1]:
                return reconcile.linear_ops.count_comments(
                    "DRE-7100", reconcile.DEAD_TAG, since=reconcile.RESET_TAG)

        before = count()
        linear.run(card, before)
        assert linear.comments()[0].startswith(hold.DEAD_SPLIT_MARK)
        assert count() == before == reconcile.REQUEUE_CAP


# --------------------------------------------------------------------------- #
# both caps in main() reach the helper                                         #
# --------------------------------------------------------------------------- #


def _sweep(card, dead):
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),
        "pr_for": MagicMock(return_value=None),
        "redispatch": MagicMock(return_value=True),
        "active_cards": MagicMock(return_value=[card]),
        "flag_stranded": MagicMock(return_value=set()),
        "agent_run_alive": MagicMock(return_value=False),
        "redeliver_rescued_work": MagicMock(return_value=False),
        "hand_dead_run_to_planner": MagicMock(),
    }
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile.linear_ops, "count_comments", return_value=dead
    ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.main()
    return mocks["hand_dead_run_to_planner"], add_label, cmd_state


class TestBothCapsReachTheHelper:
    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    def test_at_the_cap_the_branch_calls_the_helper_once(self, lane):
        card = _card(lane, bodies=_at_cap())
        helper, add_label, cmd_state = _sweep(card, reconcile.REQUEUE_CAP)
        helper.assert_called_once_with(
            card, reconcile.REQUEUE_CAP, reconcile.card_comment_bodies(card))
        add_label.assert_not_called()
        cmd_state.assert_not_called()

    @pytest.mark.parametrize("lane", ["In Progress", reconcile.REVIEW_LANE])
    @pytest.mark.parametrize("dead", range(reconcile.REQUEUE_CAP))
    def test_below_the_cap_the_branch_requeues_and_never_calls_it(self, lane, dead):
        helper, _, cmd_state = _sweep(_card(lane), dead)
        helper.assert_not_called()
        cmd_state.assert_called_once_with("DRE-7100", "Todo")


# --------------------------------------------------------------------------- #
# the registry                                                                 #
# --------------------------------------------------------------------------- #


class TestTheRegistry:
    def test_one_row_at_the_helper_and_none_at_main(self):
        rows = [r for r in hold.load()["sites"] if r["file"] == "scripts/reconcile.py"]
        assert [r["scope"] for r in rows].count("hand_dead_run_to_planner") == 1
        assert not [r for r in rows if r["scope"] == "main"]
        row = next(r for r in rows if r["scope"] == "hand_dead_run_to_planner")
        assert row["anchor"] == "held-for-human"
        assert [(e["reason"], e["lifts"]) for e in row["reasons"]] == [
            ("dead-run-cap", "unpark-marker")]

    def test_the_check_holds(self):
        assert hold.problems() == []

    def test_the_helper_is_the_one_hold_site_in_its_scope(self):
        sites = [s for s in hold.discover() if s.file == "scripts/reconcile.py"]
        assert [s.scope for s in sites].count("hand_dead_run_to_planner") == 1
        assert "main" not in [s.scope for s in sites]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
