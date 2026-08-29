"""`In QA` folds into `In Review`, and the merged lane keeps the longer window
(DRE-2726).

Both lanes meant the same thing — "a pull request is open and being checked" —
and the sweep split one job across them: `In QA` chased the critic's verdict,
`In Review` chased the merge gate. The fold puts both jobs on one lane, keyed
off the evidence rather than off which of two lanes the card happens to sit in:

  * open PR, no verdict bound to the head → re-nudge the critic
  * open PR, verdict bound → re-nudge the merge gate
  * no PR at all → the shared dead-run requeue, capped exactly as before

`STALE_MINUTES` gave the two lanes 120 and 60 minutes. The merged lane keeps
120: a shorter window would re-dispatch reviews that are simply still running.

The lane-name half is pinned by test_lane_contract.py; this file pins the
BEHAVIOUR, because a rename that silently drops the critic re-nudge would pass
a name check and strand every card whose review died.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import lane_contract  # noqa: E402
import reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The two states DRE-2726 retired; DRE-2818 deleted their contract entries once
# Linear archived them. Spelled out here because the contract no longer does.
RETIRED_LANES = ("In QA", "In Design Review")

# A retiring lane the shipped contract does NOT carry. The drain's behaviour is
# proved against this instead of against the real file: with the real file the
# retiring set is empty, so every assertion below would pass by doing nothing.
FAKE_RETIRING = {
    "name": "In Escrow",
    "status": "retiring",
    "retired_by": "DRE-9001",
    "replaced_by": "In Review",
    "reason": "a stand-in retirement, so the drain is proved and not assumed",
    "board_action": "archive the state through the workspace apply, then "
    "delete this entry",
}


@contextmanager
def _retiring(*lane_docs):
    """Run the body as if the contract declared `lane_docs` as retiring."""
    with patch.object(
        reconcile.lane_contract, "lanes", MagicMock(return_value=tuple(lane_docs))
    ):
        yield


def _stranded(state="In Escrow"):
    return {
        "identifier": "DRE-9999",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": state},
        "labels": {"nodes": []},
        "updatedAt": "2026-08-28T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _card(state="In Review"):
    return {
        "identifier": "DRE-2726",
        "description": "**Repo:** agent-bureau\nwork",
        "state": {"name": state},
        "labels": {"nodes": []},
        "updatedAt": "2026-08-28T00:00:00Z",
    }


def _sweep_mocks(extra=None, card=None):
    m = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),
        "pr_for": MagicMock(return_value=None),
        "redispatch": MagicMock(return_value=True),
        "active_cards": MagicMock(return_value=[card or _card()]),
        "flag_stranded": MagicMock(return_value=set()),
    }
    if extra:
        m.update(extra)
    return m


def _open_pr():
    return {
        "number": 42,
        "headRefName": "agent/DRE-2726-lane-contract-as-data",
        "state": "OPEN",
        "comments": [],
        "headRefOid": "b" * 40,
    }


class TestTheWindows:
    def test_the_merged_lane_keeps_the_longer_of_the_two_windows(self):
        assert reconcile.STALE_MINUTES["In Review"] == 120

    def test_the_folded_lane_is_gone_from_the_sweep(self):
        assert "In QA" not in reconcile.STALE_MINUTES
        assert "In QA" not in reconcile.SWEEP_STATES

    def test_the_sweep_still_covers_the_review_lane(self):
        assert "In Review" in reconcile.SWEEP_STATES


class TestTheMergedLaneDoesBothJobs:
    def test_open_pr_without_a_bound_verdict_re_nudges_the_critic(self):
        """The In QA job, now done on In Review. Without this the fold loses
        the critic re-nudge entirely and a dead review strands forever."""
        mocks = _sweep_mocks({
            "pr_for": MagicMock(return_value=_open_pr()),
            "_nudge": MagicMock(return_value=True),
            "verdict_bound": MagicMock(return_value=False),
        })
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "cmd_state"
        ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.main()
        mocks["_nudge"].assert_called_once_with("qa-review.yml", 42)
        cmd_state.assert_not_called()

    def test_open_pr_with_a_bound_verdict_re_nudges_the_merge_gate(self):
        mocks = _sweep_mocks({
            "pr_for": MagicMock(return_value=_open_pr()),
            "_nudge": MagicMock(return_value=True),
            "verdict_bound": MagicMock(return_value=True),
        })
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "cmd_state"
        ), patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.main()
        mocks["_nudge"].assert_called_once_with("merge-gate.yml", 42)

    def test_no_pr_requeues_under_the_shared_dead_run_cap(self):
        mocks = _sweep_mocks()
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "count_comments", return_value=1
        ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
            reconcile.linear_ops, "cmd_state"
        ) as cmd_state, patch.object(
            reconcile.linear_ops, "cmd_comment"
        ) as cmd_comment:
            reconcile.main()
        cmd_state.assert_called_once_with("DRE-2726", "Todo")
        add_label.assert_not_called()
        bodies = [c.args[1] for c in cmd_comment.call_args_list]
        assert any(reconcile.DEAD_TAG in b for b in bodies)

    def test_no_pr_at_the_cap_holds_instead_of_looping(self):
        mocks = _sweep_mocks()
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "count_comments", return_value=reconcile.REQUEUE_CAP
        ), patch.object(reconcile.linear_ops, "add_label") as add_label, patch.object(
            reconcile.linear_ops, "cmd_state"
        ) as cmd_state, patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.main()
        add_label.assert_called_once_with("DRE-2726", reconcile.HOLD_LABEL)
        cmd_state.assert_called_once_with("DRE-2726", "Backlog", "--park")


class TestTheProgressLaneAimsAtTheMergedLane:
    def test_in_progress_with_an_open_pr_advances_to_in_review(self):
        mocks = _sweep_mocks({
            "pr_for": MagicMock(return_value=_open_pr()),
            "_nudge": MagicMock(return_value=True),
        }, card=_card("In Progress"))
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "cmd_advance"
        ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.main()
        advance.assert_called_once_with("DRE-2726", "In Review", "In Progress")


class TestNoWriterStillAimsAtTheRetiredLane:
    """Every pipeline path that used to write `In QA` now writes `In Review`.

    Scanned rather than remembered: a workflow that still advances a card into
    an archived state fails the Linear call at 2am, on a card nobody is
    watching.
    """

    WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

    def test_no_workflow_writes_the_retired_lane(self):
        offenders = []
        for path in self.WORKFLOWS:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue  # incident history may name the lane it happened in
                if any(name in line for name in RETIRED_LANES):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        assert not offenders, "\n".join(offenders)

    def test_the_gate_promotes_from_the_lanes_that_still_exist(self):
        text = (ROOT / ".github" / "workflows" / "merge-gate.yml").read_text(
            encoding="utf-8"
        )
        assert '"In Review" "In Progress"' in text


class TestTheRetiringLaneDrainsItself:
    """The ordering that makes any lane retirement safe.

    The code stops writing the lane first; the sweep drains what the previous
    pipeline left there second; the board archives the state last; the contract
    entry is deleted fourth. Skip the middle step and every card sitting in the
    folded lane at merge time is stranded with nothing coming for it — the sweep
    no longer looks at that lane, and the merge gate no longer advances out of
    it.

    DRE-2818 finished step four for `In QA` and `In Design Review`, so the
    contract carries no retiring lane today. The DECISION recorded here is that
    `drain_retiring_lanes` STAYS: it is step two of a protocol the contract
    still defines (a `retiring` status with a mandatory board step, plus the
    `board.retiring_lane_is_empty` rule that is promised from Phase 3 and names
    this function as its prerequisite). What it does not get to be is code that
    reads as live while nothing proves it — so every test below injects a
    retiring lane of its own, and one pins the inert case explicitly.
    """

    def test_the_contract_carries_no_retiring_lane_today(self):
        assert lane_contract.lanes(status="retiring") == ()

    def test_the_drain_is_inert_when_nothing_is_retiring(self):
        # The explicit decision, as a test: with an empty retiring set the drain
        # reads nothing from Linear and writes nothing to it. Not "it happens to
        # do nothing" — it must not even ask.
        with patch.object(reconcile, "active_cards") as cards, patch.object(
            reconcile.linear_ops, "cmd_advance"
        ) as advance, patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.drain_retiring_lanes()
        cards.assert_not_called()
        advance.assert_not_called()
        comment.assert_not_called()

    def test_the_drain_moves_a_stranded_card_to_the_replacement(self):
        with _retiring(FAKE_RETIRING), patch.object(
            reconcile, "active_cards", MagicMock(return_value=[_stranded()])
        ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
            reconcile.linear_ops, "cmd_comment"
        ) as comment:
            reconcile.drain_retiring_lanes()
        advance.assert_called_once_with("DRE-9999", "In Review", "In Escrow")
        note = comment.call_args.args[1]
        assert "retired" in note
        # The card that retired the lane comes from the lane's own entry, never
        # from a card id frozen into the sweep.
        assert "DRE-9001" in note

    def test_the_drain_ignores_a_retiring_lane_that_names_no_replacement(self):
        # A retirement with nowhere to send its cards is not drainable, and
        # guessing a destination is worse than leaving it for a human.
        orphan = dict(FAKE_RETIRING)
        orphan.pop("replaced_by")
        with _retiring(orphan), patch.object(
            reconcile, "active_cards"
        ) as cards, patch.object(reconcile.linear_ops, "cmd_advance") as advance:
            reconcile.drain_retiring_lanes()
        cards.assert_not_called()
        advance.assert_not_called()

    def test_the_drain_is_a_no_op_when_the_lane_is_already_empty(self):
        with _retiring(FAKE_RETIRING), patch.object(
            reconcile, "active_cards", MagicMock(return_value=[])
        ), patch.object(reconcile.linear_ops, "cmd_advance") as advance:
            reconcile.drain_retiring_lanes()
        advance.assert_not_called()

    def test_the_drain_runs_on_every_sweep(self):
        # A drain nobody calls is a drain that never happens. Pin it to the
        # backstop list the full sweep actually walks.
        import inspect

        source = inspect.getsource(reconcile.main)
        assert "drain_retiring_lanes," in source

    def test_a_failed_drain_fails_the_run(self):
        with _retiring(FAKE_RETIRING), patch.object(
            reconcile, "active_cards", MagicMock(return_value=[_stranded()])
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=RuntimeError("nope")
        ):
            with pytest.raises(reconcile.ReconcileWriteError):
                reconcile.drain_retiring_lanes()


class TestTheDocumentedFlowMatches:
    def test_the_card_quality_standard_describes_the_eleven_lane_flow(self):
        text = (ROOT / "standards" / "card-quality.md").read_text(encoding="utf-8")
        assert "In QA" not in text
        assert "`Todo → In Progress → In Review → Done`" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
