"""RED-first tests: the sweep brings a limit death back when the world changes
(DRE-3171).

A limit death (tests/test_limit_death_is_its_own_class.py) leaves ONE marker on
the card and moves nothing. This is the other half: once per pass the reconcile
sweep reads every card whose NEWEST `🪦 limit-death:` marker is the latest
pipeline receipt on it, and re-enters the stage that died when EITHER the
marker's reset time has passed OR the active account differs from the one the
marker recorded. The CEO's instruction, verbatim: "Once I release [the account]
it should automatically pick that up."

Re-entry is the stage's own front door, never a fresh run of something else:

  * `classify` / `plan` — the card re-enters Planning. The relay's planner
    trigger is Planning ENTRY (agent-bureau's relay: "card enters Planning →
    agent-plan"), so a card already sitting in Planning is bounced out through
    Intake — the lane before Planning exit, which nothing polices — and back.
  * `build` — In Progress → Todo, the sweep's own requeue move; a card already
    in Todo gets the sweep's own re-dispatch, because a same-lane write fires
    no webhook.
  * `fix` / `review` / `sync` — the ORIGINAL GitHub run named in the marker is
    re-run, failed jobs only. Never a workflow_dispatch: the rerun keeps the
    run's own event, PR and head.

The lane writes are INJECTED rather than made here (DRE-2859): the lane
contract attributes a write to the actor that runs the module, and the actor
is the sweep — `reconcile.py` is a declared writer of Todo, this module is
not. The second half of this file pins that seam: the sweep calls recovery
once per full pass, survives an exception inside it, hands it only this repo's
cards, and leaves a limit-parked card alone in its own nudge loop — the loop
that would otherwise spend a strike on an In Progress card sixty minutes after
a death that was never the card's.

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_recovery.py -v
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import limit_recovery  # noqa: E402
import reconcile  # noqa: E402

RUN = "33912345678"
RESET = datetime(2026, 9, 5, 20, 30, tzinfo=UTC)          # 13:30 PT
BEFORE = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)          # the window is still shut
AFTER = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)           # the window has reset


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    reconcile._write_failures.clear()
    reconcile.reset_sweep_cards()


def marker(kind="claude", stage="build", reset=RESET, run=RUN, account=None) -> str:
    """The marker as dead_run writes it — never hand-typed here, so a change to
    the shape reaches both halves of the mechanism or neither."""
    return dead_run.decide(
        0, limit=dead_run.LimitDeath(kind=kind, stage=stage, reset=reset,
                                     run_id=run, account=account),
    ).comments[0]


def card(ident="DRE-3062", lane="In Progress", bodies=(), labels=("repo:agent-bureau",)):
    return {
        "id": f"id-{ident}",
        "identifier": ident,
        "title": f"{ident} title",
        "description": "work",
        "updatedAt": "2026-09-05T10:00:00Z",
        "state": {"name": lane},
        "labels": {"nodes": [{"name": name} for name in labels]},
        "comments": {"nodes": [{"body": b} for b in bodies]},
    }


class Seams:
    """Every write the recovery can make, recorded and injectable."""

    def __init__(self, *, rerun_ok=True, dispatch_ok=True, move_raises=None):
        self.comments: list[tuple[str, str]] = []
        self.moves: list[tuple[str, str]] = []
        self.reruns: list[str] = []
        self.dispatched: list[str] = []
        self._rerun_ok = rerun_ok
        self._dispatch_ok = dispatch_ok
        self._move_raises = move_raises
        self.lops = MagicMock()
        self.lops.cmd_comment.side_effect = lambda ident, body: self.comments.append((ident, body))

    def rerun(self, run_id: str) -> bool:
        self.reruns.append(run_id)
        return self._rerun_ok

    def move(self, ident: str, lane: str) -> None:
        if self._move_raises:
            raise self._move_raises
        self.moves.append((ident, lane))

    def dispatch(self, c: dict) -> bool:
        self.dispatched.append(c["identifier"])
        return self._dispatch_ok

    def recover(self, cards, *, now=AFTER, active_account=None, wip_room=8):
        return limit_recovery.recover(
            self.lops, now, active_account, wip_room,
            rerun=self.rerun, move=self.move, dispatch=self.dispatch, cards=cards,
        )


# --------------------------------------------------------------------------
# the two triggers
# --------------------------------------------------------------------------
def test_reset_passed_reenters_build_from_in_progress():
    s = Seams()
    lines = s.recover([card(bodies=[marker()])])
    assert s.moves == [("DRE-3062", "Todo")]
    assert s.dispatched == [] and s.reruns == []
    assert len(s.comments) == 1
    ident, body = s.comments[0]
    assert ident == "DRE-3062"
    assert body.startswith(limit_recovery.RECOVERY_MARK)
    assert "window reset at 2026-09-05 13:30 PT" in body
    assert any("DRE-3062" in line for line in lines)


def test_reset_not_passed_waits():
    s = Seams()
    lines = s.recover([card(bodies=[marker()])], now=BEFORE)
    assert s.moves == [] and s.comments == []
    assert any("DRE-3062" in line and "13:30 PT" in line for line in lines), (
        "a card that is waiting says until when, in Pacific time"
    )


def test_account_switch_triggers_before_the_reset():
    s = Seams()
    s.recover([card(bodies=[marker(account="main")])], now=BEFORE, active_account="work")
    assert s.moves == [("DRE-3062", "Todo")]
    assert "account switched main → work" in s.comments[0][1]


def test_the_same_account_is_not_a_switch():
    s = Seams()
    s.recover([card(bodies=[marker(account="main")])], now=BEFORE, active_account="main")
    assert s.moves == [] and s.comments == []


def test_no_recorded_account_means_only_the_clock_can_trigger():
    s = Seams()
    s.recover([card(bodies=[marker()])], now=BEFORE, active_account="work")
    assert s.moves == [] and s.comments == []


def test_active_account_none_means_only_the_clock_can_trigger():
    s = Seams()
    s.recover([card(bodies=[marker(account="main")])], now=BEFORE, active_account=None)
    assert s.moves == [] and s.comments == []


def test_linear_with_unknown_reset_recovers_on_the_next_pass():
    """Linear's quota is hourly and its reset header is rarely in the text. The
    sweep that runs this recovery has just READ the board through the same
    quota — that is the evidence it refilled, and the receipt says so."""
    s = Seams()
    s.recover([card(bodies=[marker(kind="linear", stage="sync", reset=None)])], now=BEFORE)
    assert s.reruns == [RUN]
    assert "quota" in s.comments[0][1].lower()


def test_claude_with_unknown_reset_but_a_recorded_account_waits_for_the_switch():
    s = Seams()
    lines = s.recover([card(bodies=[marker(reset=None, account="main")])], now=AFTER, active_account="main")
    assert s.moves == [] and s.comments == []
    assert any("DRE-3062" in line and "main" in line for line in lines), (
        "a card waiting on a switch says which account it is waiting to leave"
    )
    s.recover([card(bodies=[marker(reset=None, account="main")])], now=AFTER, active_account="work")
    assert s.moves == [("DRE-3062", "Todo")]


def test_a_marker_nothing_can_trigger_is_handed_to_a_human_once():
    """Nothing waits without a clock, a switch, or a person being told. A
    Claude marker with no reset time and no recorded account has none of the
    first two — an API `rate_limit_error` carries no `resets …`, and so does a
    reset in a zone the parser does not read — so it gets ONE receipt saying
    what a human does, and that receipt closes the marker: the card is back on
    the sweep's ordinary clock instead of hidden from it forever."""
    s = Seams()
    lines = s.recover([card(bodies=[marker(reset=None)])], now=AFTER, active_account=None)
    assert s.moves == [] and s.reruns == [] and s.dispatched == []
    assert len(s.comments) == 1
    ident, body = s.comments[0]
    assert ident == "DRE-3062"
    assert body.startswith(limit_recovery.HANDOFF_MARK)
    assert limit_recovery.is_receipt(body), "the hand-off must close the marker"
    assert not any(line.startswith("ERROR:") for line in lines)
    again = Seams()
    again.recover([card(bodies=[marker(reset=None), body])], now=AFTER)
    assert again.comments == [] and again.moves == []


def test_a_handed_off_card_is_ordinary_to_the_sweep_again():
    handoff = Seams()
    handoff.recover([card(bodies=[marker(reset=None)])])
    assert limit_recovery.waiting([marker(reset=None), handoff.comments[0][1]]) is None


def test_a_planning_stage_marker_on_a_working_card_reruns_the_run_never_replans():
    """plan.yml's ACTIVATE route runs the second critic against a CEO-approved
    epic that is already In Progress. A limit death there must not drag the
    epic back to Planning — that undoes the approval and fires a plan-mode
    run. The original run is re-run instead; it keeps its own trigger."""
    for lane in ("Todo", "In Progress", "In Review", "Green Light"):
        s = Seams()
        s.recover([card(ident="DRE-3162", lane=lane, bodies=[marker(stage="plan")])])
        assert s.moves == [], lane
        assert s.reruns == [RUN], lane


# --------------------------------------------------------------------------
# re-entry is the stage's own front door
# --------------------------------------------------------------------------
def test_build_from_todo_is_a_redispatch_not_a_same_lane_write():
    s = Seams()
    s.recover([card(lane="Todo", bodies=[marker()])])
    assert s.dispatched == ["DRE-3062"]
    assert s.moves == []
    assert "re-dispatched" in s.comments[0][1].lower()


def test_build_from_backlog_moves_to_todo():
    s = Seams()
    s.recover([card(lane="Backlog", bodies=[marker()])])
    assert s.moves == [("DRE-3062", "Todo")]


@pytest.mark.parametrize("stage", ["plan", "classify"])
def test_planning_stage_from_planning_bounces_out_through_intake_and_back(stage):
    s = Seams()
    s.recover([card(ident="DRE-3162", lane="Planning", labels=(), bodies=[marker(stage=stage)])])
    assert s.moves == [("DRE-3162", "Intake"), ("DRE-3162", "Planning")]
    assert s.dispatched == []
    assert "Planning" in s.comments[0][1]


@pytest.mark.parametrize("lane", ["Backlog", "Intake", "Triage"])
def test_planning_stage_from_before_planning_exit_enters_planning_once(lane):
    s = Seams()
    s.recover([card(ident="DRE-3162", lane=lane, bodies=[marker(stage="plan")])])
    assert s.moves == [("DRE-3162", "Planning")]


@pytest.mark.parametrize("stage", ["fix", "review", "sync"])
def test_pr_stages_rerun_the_original_run(stage):
    s = Seams()
    s.recover([card(lane="In Review", bodies=[marker(stage=stage, run=RUN)])])
    assert s.reruns == [RUN]
    assert s.moves == [] and s.dispatched == []
    assert RUN in s.comments[0][1]


def test_a_rerun_with_no_run_id_is_handed_to_a_human_once_not_errored_every_pass():
    """An `ERROR:` here would go into the sweep's write ledger on EVERY pass —
    a permanently red sweep and a medic woken every fifteen minutes for a card
    nobody is told about. The honest answer is one receipt naming what a human
    does, which closes the marker."""
    s = Seams()
    lines = s.recover([card(lane="In Review", bodies=[marker(stage="review", run="unknown")])])
    assert s.reruns == []
    assert len(s.comments) == 1 and s.comments[0][1].startswith(limit_recovery.HANDOFF_MARK)
    assert "re-run" in s.comments[0][1].lower()
    assert not any(line.startswith("ERROR:") for line in lines)
    again = Seams()
    again.recover([card(lane="In Review", bodies=[marker(stage="review", run="unknown"), s.comments[0][1]])])
    assert again.comments == [] and again.reruns == []


# --------------------------------------------------------------------------
# bounds and skips
# --------------------------------------------------------------------------
def test_wip_room_bounds_a_pass():
    cards = [card(ident=f"DRE-{n}", bodies=[marker()]) for n in (1, 2, 3)]
    s = Seams()
    s.recover(cards, wip_room=2)
    assert [m[0] for m in s.moves] == ["DRE-1", "DRE-2"]
    assert len(s.comments) == 2
    s = Seams()
    s.recover(cards, wip_room=0)
    assert s.moves == [] and s.comments == []


@pytest.mark.parametrize("lane", ["Done", "Canceled", "Duplicate"])
def test_terminal_cards_are_skipped(lane):
    s = Seams()
    s.recover([card(lane=lane, bodies=[marker()])])
    assert s.moves == [] and s.comments == []


def test_a_card_held_for_a_human_is_skipped():
    s = Seams()
    s.recover([card(bodies=[marker()], labels=("repo:agent-bureau", dead_run.HOLD_LABEL))])
    assert s.moves == [] and s.comments == []


def test_a_newer_pipeline_receipt_means_the_card_already_moved_on():
    s = Seams()
    s.recover([card(bodies=[marker(), "🧹 Reconcile: card sat in Todo with no run — re-dispatched."])])
    assert s.moves == [] and s.comments == []


def test_the_recoverys_own_receipt_counts_so_nothing_dispatches_twice():
    s = Seams()
    s.recover([card(bodies=[marker()])])
    receipt = s.comments[0][1]
    again = Seams()
    again.recover([card(bodies=[marker(), receipt])])
    assert again.moves == [] and again.comments == []


def test_a_human_comment_after_the_marker_does_not_cancel_recovery():
    s = Seams()
    s.recover([card(bodies=[marker(), "Looking at this now — Sid"])])
    assert s.moves == [("DRE-3062", "Todo")]


@pytest.mark.parametrize(
    "note",
    ["👍 approved, go ahead", "🎉 nice work", "👀", "✅ looks right to me",
     "❌ not this one", "🙏 thanks", "😀", "✨ shiny", "❤️"],
)
def test_a_human_comment_that_opens_with_an_emoji_is_not_a_receipt(note):
    """The critic's finding on #279: 'any non-ASCII first character' read a
    thumbs-up as the pipeline's own receipt, closed the marker, and silently
    stopped bringing the card back — the exact stall this module exists to
    end. A receipt is a glyph the PIPELINE opens with, or the act trailer."""
    assert not limit_recovery.is_receipt(note)
    s = Seams()
    s.recover([card(bodies=[marker(), note])])
    assert s.moves == [("DRE-3062", "Todo")], note


@pytest.mark.parametrize(
    "receipt",
    ["🧹 Reconcile: card sat in Todo with no run — re-dispatched.",
     "🧠 model-attempt: claude-opus-5 — engineer agent starting.",
     "⏳ 2/5 failing tests written",
     "🤖 PR opened: https://github.com/o/r/pull/1",
     "🪦 dead-run-requeue: agent died — requeued to Todo (dead run 1/3).",
     "🚨 held-for-human (dead-run-requeue cap reached): parked.",
     "🛑 Agent blocked: needs a decision — parked in Backlog.",
     "🙋 The agent paused for a decision before building.",
     "🧯 infrastructure fault before the agent started.",
     "🔌 The code reviewer was temporarily unavailable.",
     "♻️ dead-run-budget-reset: un-parked by a human.",
     "🩺 medic-retry-declined: not retried — parked.",
     f"{limit_recovery.RECOVERY_MARK} re-entered build — window reset.",
     f"{limit_recovery.HANDOFF_MARK} cannot bring this card back on its own."],
)
def test_every_pipeline_receipt_closes_the_marker(receipt):
    assert limit_recovery.is_receipt(receipt)
    s = Seams()
    s.recover([card(bodies=[marker(), receipt])])
    assert s.moves == [] and s.comments == [], receipt


def test_the_act_trailer_alone_makes_a_receipt():
    """Every act composed through pipeline_act.receipt() ends with the
    `📎 pipeline-act:` trailer, whatever glyph it opens with — the one
    machine-readable claim of pipeline authorship a comment can carry."""
    import pipeline_act

    body = pipeline_act.receipt("card-stranded", "no agent run has started. Observed: parked.")
    assert limit_recovery.is_receipt(body)
    assert limit_recovery.is_receipt("plain words first\n\n" + pipeline_act.trailer("card-stranded"))


def test_the_newest_marker_wins():
    s = Seams()
    old = marker(reset=datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
    new = marker(reset=datetime(2026, 9, 6, 20, 30, tzinfo=UTC))
    s.recover([card(bodies=[old, "🧹 Reconcile: re-dispatched", new])])
    assert s.moves == [] and s.comments == []


def test_a_card_without_a_marker_is_not_touched():
    s = Seams()
    s.recover([card(bodies=["🪦 dead-run-requeue: agent died — requeued to Todo (dead run 1/3)."])])
    assert s.moves == [] and s.comments == []


def test_a_failed_write_is_reported_and_no_receipt_is_posted():
    s = Seams(move_raises=RuntimeError("Linear refused the write"))
    lines = s.recover([card(ident="DRE-1", bodies=[marker()]),
                       card(ident="DRE-2", lane="Todo", bodies=[marker()])])
    assert [ident for ident, _ in s.comments] == ["DRE-2"]
    assert any(line.startswith("ERROR:") and "DRE-1" in line for line in lines)
    assert s.dispatched == ["DRE-2"], "one card's failure never costs the next its turn"


def test_a_failed_rerun_is_reported_and_no_receipt_is_posted():
    s = Seams(rerun_ok=False)
    lines = s.recover([card(lane="In Review", bodies=[marker(stage="review")])])
    assert s.comments == []
    assert any(line.startswith("ERROR:") for line in lines)


def test_waiting_reads_the_newest_marker_or_nothing():
    assert limit_recovery.waiting([]) is None
    assert limit_recovery.waiting([marker()])["run"] == RUN
    assert limit_recovery.waiting([marker(), "🧹 Reconcile: re-dispatched."]) is None
    assert limit_recovery.waiting([marker(), "a human reply"])["run"] == RUN


# --------------------------------------------------------------------------
# the sweep's side of the seam
# --------------------------------------------------------------------------
def test_the_sweep_runs_recovery_as_a_backstop():
    tree = ast.parse(inspect.getsource(reconcile.main))
    named = {
        elt.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        for elt in node.elts
        if isinstance(elt, ast.Name)
    }
    assert "recover_limit_deaths" in named
    assert "retry_dead_fix_runs" in named, "the backstops tuple is the one read"


def test_an_exception_inside_recovery_never_aborts_the_sweep(capsys):
    with patch.object(reconcile, "active_cards", return_value=[]), patch.object(
        reconcile.limit_recovery, "recover", side_effect=RuntimeError("boom")
    ):
        reconcile.recover_limit_deaths()
    assert "limit-recovery" in capsys.readouterr().err


def test_the_wrapper_hands_recovery_this_repos_cards_and_the_seams(monkeypatch):
    monkeypatch.delenv("CLAUDE_ACCOUNT", raising=False)
    mine = card(ident="DRE-1")
    theirs = card(ident="DRE-2", labels=("repo:portico",))
    unlabelled = card(ident="DRE-3", lane="Planning", labels=())
    seen = {}

    def fake_recover(lops, now, active_account, wip_room, *, rerun, move, dispatch, cards):
        seen.update(lops=lops, now=now, active_account=active_account,
                    wip_room=wip_room, rerun=rerun, move=move, dispatch=dispatch,
                    cards=cards)
        return ["ERROR: limit-recovery DRE-1: something did not land"]

    with patch.object(reconcile, "active_cards", return_value=[mine, theirs, unlabelled]), \
         patch.object(reconcile.limit_recovery, "recover", side_effect=fake_recover), \
         patch.object(reconcile, "gh_dispatch") as gh_dispatch, \
         patch.object(reconcile.linear_ops, "cmd_state") as cmd_state:
        reconcile.recover_limit_deaths()
        seen["rerun"]("123")
        seen["move"]("DRE-1", "Todo")
    assert seen["lops"] is reconcile.linear_ops
    assert seen["now"].tzinfo is not None
    assert seen["active_account"] is None
    assert [c["identifier"] for c in seen["cards"]] == ["DRE-1", "DRE-3"], (
        "this repo's cards plus the unlabelled Planning card; never another repo's"
    )
    assert seen["wip_room"] == reconcile.MAX_WIP - 1
    assert seen["dispatch"] is reconcile.redispatch
    gh_dispatch.assert_called_once_with(
        "run", "rerun", "123", "--failed", "--repo", "dreadnought-foundry/agent-bureau"
    )
    cmd_state.assert_called_once_with("DRE-1", "Todo")
    assert any("did not land" in f for f in reconcile._write_failures), (
        "a write the recovery could not make turns the sweep red, like every write"
    )


def test_the_wrapper_reads_the_active_account_from_the_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_ACCOUNT", "work")
    seen = {}

    def fake_recover(lops, now, active_account, wip_room, **kw):
        seen["active_account"] = active_account
        return []

    with patch.object(reconcile, "active_cards", return_value=[]), patch.object(
        reconcile.limit_recovery, "recover", side_effect=fake_recover
    ):
        reconcile.recover_limit_deaths()
    assert seen["active_account"] == "work"


def _full_sweep_mocks(cards):
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "close_finished_epics": MagicMock(),
        "promote_ready": MagicMock(return_value=0),
        "age_minutes": MagicMock(return_value=999),
        "pr_for": MagicMock(return_value=None),
        "flag_stranded": MagicMock(return_value=set()),
        "agent_run_alive": MagicMock(return_value=False),
        "active_cards": MagicMock(return_value=cards),
    }


def test_the_nudge_loop_leaves_a_limit_parked_card_alone():
    """MUTATION CHECK: drop the limit-marker consult from main()'s nudge loop
    and this In Progress card is requeued to Todo with a dead-run strike sixty
    minutes after a death that was never the card's — straight back into the
    wall it died on."""
    parked = card(bodies=[marker(reset=datetime(2026, 12, 1, tzinfo=UTC))])
    with patch.multiple(reconcile, **_full_sweep_mocks([parked])), patch.object(
        reconcile.linear_ops, "count_comments"
    ) as count, patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, patch.object(reconcile.linear_ops, "add_label") as add_label:
        reconcile.main()
    cmd_state.assert_not_called()
    count.assert_not_called()
    add_label.assert_not_called()
    assert all(dead_run.DEAD_TAG not in c.args[1] for c in cmd_comment.call_args_list)


def test_the_nudge_loop_resumes_once_a_newer_receipt_follows_the_marker():
    """The control: the skip is keyed on the marker being the NEWEST receipt.
    After the recovery's own receipt the card is ordinary again."""
    receipt = f"{limit_recovery.RECOVERY_MARK} re-entered build — window reset at 2026-09-05 13:30 PT."
    ordinary = card(bodies=[marker(), receipt])
    with patch.multiple(reconcile, **_full_sweep_mocks([ordinary])), patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ), patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ), patch.object(reconcile.linear_ops, "add_label"):
        reconcile.main()
    cmd_state.assert_called_once_with("DRE-3062", "Todo")


def test_a_full_sweep_reenters_a_reset_card_exactly_once():
    """The recovery moves the card, and the nudge loop — reading the same
    cached board — does not move it a second time in the same pass."""
    ready = card(bodies=[marker(reset=datetime(2026, 1, 1, tzinfo=UTC))])
    with patch.multiple(reconcile, **_full_sweep_mocks([ready])), patch.object(
        reconcile.linear_ops, "count_comments", return_value=0
    ) as count, patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as cmd_comment, patch.object(reconcile.linear_ops, "add_label"):
        reconcile.main()
    cmd_state.assert_called_once_with("DRE-3062", "Todo")
    count.assert_not_called()
    bodies = [c.args[1] for c in cmd_comment.call_args_list]
    assert len(bodies) == 1 and bodies[0].startswith(limit_recovery.RECOVERY_MARK)
    assert reconcile._write_failures == []
