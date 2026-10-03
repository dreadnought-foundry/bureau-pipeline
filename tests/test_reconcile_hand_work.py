"""RED-first: the sweep carries a WORKBENCH or OPERATOR card to Hand-work, and
watches it there (DRE-5322, epic DRE-5240).

Todo is the build button: a card that enters it makes the relay dispatch a run.
Since DRE-3385 the sweep has carried a person's card (WORKBENCH, OPERATOR) into
Todo marked `hand-built`, so Todo meant both "about to build" and "waiting on a
person, for days". DRE-5321 pointed those two verdicts at `Hand-work`; this card
makes the promoter go where the verdict says.

WHAT IS UNDER TEST:
  * `promote_ready` advances to `routing_verdict.destination(verdict)`: a
    WORKBENCH card to Hand-work, marks first, one advance, the receipt
    `🧹 Auto-promoted Backlog → Hand-work: …`, no WIP slot; OPERATOR also
    `no-code`; FLEET still to Todo, spending a slot.
  * Hand-work is watched, never nudged: in `WATCHDOG_LANES` and
    `HAND_BUILT_REVIEW_LANES`, not in `SWEEP_STATES`. A hand-built card there
    with no pull request is left alone by the nudge loop and by
    `flag_stranded` — three of these facts are silence, so each is pinned.
  * `move_hand_built_to_review` carries a Hand-work card with an open pull
    request to In Review once; `_flag_hand_built_idle` still raises on one idle
    past `HAND_IDLE_MINUTES` with neither branch nor pull request.
  * The idle check counts a card in Hand-work as in motion, so a repo whose
    only work is a person's card is not skipped (an idle pass skips the two
    phases that card is owed).
  * The act registry and the ready-lane writer check still pass.

Run: cd bureau-pipeline && python3 -m pytest tests/test_reconcile_hand_work.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_act  # noqa: E402
import ready_lane_writers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402
import validate_card  # noqa: E402

# The harnesses are IMPORTED, not copied: each is the one statement of how its
# phase is driven offline, and a second copy here would drift from it.
import test_hand_built_not_stranded as stranded  # noqa: E402
import test_hand_built_open_pr_to_review as review  # noqa: E402
import test_operator_card_promotion as promo  # noqa: E402

HAND_WORK = "Hand-work"
HAND_BUILT = "hand-built"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """The pins the imported harnesses' own autouse fixtures apply in their
    modules: this sweep owns portico, portico is on the rail, and the canonical
    snapshot is never fetched."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    monkeypatch.setattr(validate_card, "VALID_SLUGS", {"portico", "atlas", "bureau-pipeline"})
    monkeypatch.setattr(reconcile, "live_rail_slugs",
                        lambda: frozenset({"portico", "atlas", "bureau-pipeline"}),
                        raising=False)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


# --------------------------------------------------------------------------
# 1: the promoter goes where the verdict says
# --------------------------------------------------------------------------
class TestThePromoterReadsTheDestination:
    def test_a_workbench_card_is_advanced_to_hand_work_once(self):
        board = promo._Board(promo._card(comments=[promo.WORKBENCH]))
        assert board.promote() == 1
        advances = [(k, i, w) for k, i, w in board.writes if k == "advance"]
        assert advances == [("advance", "DRE-3385", HAND_WORK)]

    def test_the_advance_is_cmd_advance_to_hand_work_from_backlog(self):
        """The exact call: `_Board` records only the destination, so the gate
        is driven here with the same seams and a spy that keeps all three
        arguments."""
        calls = []
        card = promo._card(comments=[promo.WORKBENCH])
        with mock.patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                mock.patch.object(reconcile, "backlog_children", return_value=[card]), \
                mock.patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
                mock.patch.object(reconcile.mid_epic, "last_green_light",
                                  return_value=promo.GREEN_LIGHT), \
                mock.patch.object(reconcile, "epic_thread", return_value=[]), \
                mock.patch.object(reconcile.plan_critic, "promotion_refusal",
                                  return_value=None), \
                mock.patch.object(reconcile, "card_state", return_value="Done"), \
                mock.patch.object(reconcile.linear_ops, "cmd_advance",
                                  side_effect=lambda *a: calls.append(a)), \
                mock.patch.object(reconcile.linear_ops, "add_label"), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment"), \
                mock.patch.object(reconcile.linear_ops, "count_comments", return_value=0):
            assert reconcile.promote_ready(active_count=0) == 1
        assert calls == [("DRE-3385", HAND_WORK, "Backlog")]

    def test_hand_built_is_stamped_before_the_state_write(self):
        board = promo._Board(promo._card(comments=[promo.WORKBENCH]))
        board.promote()
        kinds = [(k, w) for k, i, w in board.writes if i == "DRE-3385"]
        assert kinds == [("label", HAND_BUILT), ("advance", HAND_WORK)]

    def test_an_operator_card_also_carries_no_code(self):
        board = promo._Board(promo._card(comments=[promo.OPERATOR]))
        board.promote()
        kinds = [(k, w) for k, i, w in board.writes if i == "DRE-3385"]
        assert kinds == [("label", HAND_BUILT), ("label", linear_ops.NO_CODE_LABEL),
                         ("advance", HAND_WORK)]

    def test_the_receipt_names_hand_work(self):
        board = promo._Board(promo._card(comments=[promo.WORKBENCH]))
        board.promote()
        receipt = board.receipt_for("DRE-3385")
        assert receipt.startswith(f"🧹 Auto-promoted Backlog → {HAND_WORK}: ")
        assert "→ Todo" not in receipt

    def test_a_persons_card_spends_no_wip_slot(self, capsys):
        """At the cap, with one run in flight, the person's card still leaves."""
        with mock.patch.object(reconcile, "MAX_WIP", 1):
            board = promo._Board(promo._card(comments=[promo.WORKBENCH]))
            assert board.promote(active_count=1) == 1
        assert board.lane_of("DRE-3385") == HAND_WORK
        assert "budget spent" not in capsys.readouterr().out

    def test_fleet_still_goes_to_todo_and_spends_a_slot(self):
        with mock.patch.object(reconcile, "MAX_WIP", 1):
            board = promo._Board(
                promo._card(identifier="DRE-1", comments=[promo.FLEET]),
                promo._card(identifier="DRE-2", comments=[promo.FLEET]),
            )
            assert board.promote(active_count=0) == 1
        assert board.lane_of("DRE-1") == "Todo"
        assert board.lane_of("DRE-2") == "Backlog"
        assert board.receipt_for("DRE-1").startswith("🧹 Auto-promoted Backlog → Todo: ")

    def test_the_lane_is_never_spelled_in_the_promoter(self):
        import inspect
        source = inspect.getsource(reconcile.promote_ready)
        assert 'cmd_advance(card["identifier"], "Todo"' not in source
        assert "Auto-promoted Backlog → Todo" not in source
        assert "routing_verdict.destination(verdict)" in source


# --------------------------------------------------------------------------
# 2: watched, never nudged
# --------------------------------------------------------------------------
class TestTheLaneSets:
    def test_hand_work_has_no_stall_window_so_the_nudge_loop_never_sees_it(self):
        assert HAND_WORK not in lane_contract.stale_minutes()
        assert HAND_WORK not in reconcile.SWEEP_STATES

    def test_the_watchdog_lanes(self):
        assert reconcile.WATCHDOG_LANES == ("Todo", HAND_WORK, "In Progress")

    def test_the_review_move_reads_hand_work(self):
        assert HAND_WORK in reconcile.HAND_BUILT_REVIEW_LANES
        assert HAND_WORK in reconcile.SWEPT_LANES

    def test_a_card_in_hand_work_does_not_count_against_wip(self):
        card = stranded._card(state=HAND_WORK)
        assert not reconcile.counts_against_wip(card)


class TestSilence:
    def test_the_nudge_loop_leaves_a_hand_built_card_in_hand_work_alone(self):
        s = stranded._run_sweep([stranded._card(state=HAND_WORK)])
        s.redispatch.assert_not_called()
        s.cmd_state.assert_not_called()
        s.cmd_advance.assert_not_called()
        s.add_label.assert_not_called()

    def test_flag_stranded_reports_nothing_for_it(self):
        flagged, comment, add_label = stranded._run_watchdog(
            [stranded._card(state=HAND_WORK)], bodies=[])
        assert flagged == set()
        comment.assert_not_called()
        add_label.assert_not_called()


# --------------------------------------------------------------------------
# 3: what the lane is still owed
# --------------------------------------------------------------------------
class TestWhatTheLaneIsOwed:
    def test_an_open_pull_request_carries_it_to_in_review_once(self):
        first = review._run([review._pr()], [review._card(state=HAND_WORK)])
        first.cmd_advance.assert_called_once()
        ident, to_lane, from_lanes = first.cmd_advance.call_args.args
        assert (ident, to_lane) == (review.CARD, reconcile.REVIEW_LANE)
        assert HAND_WORK in [lane.strip() for lane in from_lanes.split(",")]
        first.cmd_comment.assert_called_once()
        said = first.cmd_comment.call_args.args[1]
        again = review._run([review._pr()], [review._card(state=HAND_WORK)], bodies=[said])
        again.cmd_advance.assert_not_called()
        again.cmd_comment.assert_not_called()

    def test_idle_with_no_branch_and_no_pull_request_raises_the_alarm(self):
        card = stranded._card(state=HAND_WORK,
                              minutes_stale=reconcile.HAND_IDLE_MINUTES + 60)
        posted = []
        with mock.patch.object(reconcile, "active_cards",
                               side_effect=lambda states=None: [
                                   c for c in [card]
                                   if c["state"]["name"] in (states or ())]), \
                mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment",
                                  side_effect=lambda i, b: posted.append((i, b))):
            reconcile._flag_hand_built_idle([], set())
        assert len(posted) == 1
        ident, body = posted[0]
        assert ident == card["identifier"]
        assert f"{reconcile.UNLANDED_TAG} no branch:" in body
        assert HAND_WORK in body


# --------------------------------------------------------------------------
# 4: a repo whose only work is a person's card is not idle
# --------------------------------------------------------------------------
class TestTheIdleCheck:
    def test_hand_work_is_in_motion(self):
        assert HAND_WORK in reconcile.IN_MOTION_LANES
        assert HAND_WORK in reconcile.IDLE_LANES

    def test_the_linear_check_asks_about_hand_work(self):
        asked = []

        def gql(query, variables=None):
            asked.append(variables)
            return {"issues": {"nodes": [{"id": "x", "labels": {"nodes": [
                {"name": "repo:portico"}]}}], "pageInfo": {"hasNextPage": False}}}
        with mock.patch.object(reconcile, "MAX_WIP", 3), \
                mock.patch.object(reconcile.bureau_read, "mode", return_value="off"), \
                mock.patch.object(reconcile.linear_ops, "gql", side_effect=gql):
            assert reconcile.sweep_idle() is None
        assert HAND_WORK in asked[0]["states"]


# --------------------------------------------------------------------------
# 5: the registries still agree
# --------------------------------------------------------------------------
class TestTheRegistries:
    def test_the_act_registry_anchors_the_new_receipt(self):
        assert pipeline_act.main(["check"]) == 0

    def test_reconcile_may_write_both_lanes_it_promotes_into(self):
        for lane in ("Todo", HAND_WORK):
            assert "reconcile.py" in lane_contract.lane_writers(lane)

    def test_the_ready_lane_writer_check_passes(self):
        """`writer_problems`, the part of `check` that reads this repository:
        the promoter's destination is read through `routing_verdict.destination`
        and attributed to reconcile.py, a permitted writer of both lanes. (The
        CLI's other half asks agent-bureau's workspace file about `operator`,
        which is not on disk in this checkout.)"""
        assert HAND_WORK in ready_lane_writers.ready_lanes()
        problems = ready_lane_writers.writer_problems()
        assert problems == [], "\n".join(problems)
        promoted = {w.lane for w in ready_lane_writers.writes()
                    if w.writer == "reconcile.py" and "destination" in w.expression}
        assert {"Todo", HAND_WORK} <= promoted, promoted
