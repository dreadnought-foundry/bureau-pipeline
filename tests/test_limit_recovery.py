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
from datetime import UTC, datetime, timedelta
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
        # NEWEST FIRST, the order Linear answers a comment window in
        # (DRE-3250); `bodies` is written oldest→newest, as the card reads.
        "comments": {"nodes": [{"body": b} for b in reversed(list(bodies))]},
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


# --------------------------------------------------------------------------
# the hold blocks agent work, not bookkeeping (Stage 2 fix #23, BP-6)
# --------------------------------------------------------------------------
# DRE-5620, 2026-10-02: the PR merged, `card-done` in Linear Sync died on the
# fleet's exhausted Linear quota (run 37051027773, 11:58 PT), and the medic
# left `🪦 limit-death: kind=linear stage=sync reset=unknown run=37051027773`.
# The card was In Review with a leftover `needs-human` label, so this pass
# skipped it every time and the card never closed. A sync rerun starts no
# agent; it finishes bookkeeping for work that already merged. The rule is
# the medic retry gate's (`medic_retry.park_rule_applies`), applied to the
# workflow each marker stage belongs to.
DRE5620_RUN = "37051027773"
HELD = ("repo:agent-bureau", dead_run.HOLD_LABEL)


def _dre5620_marker() -> str:
    return marker(kind="linear", stage="sync", reset=None, run=DRE5620_RUN)


def test_dre5620_a_held_card_s_sync_death_is_re_run_once_the_quota_answers():
    s = Seams()
    lines = s.recover([card(ident="DRE-5620", lane="In Review",
                            bodies=[_dre5620_marker()], labels=HELD)])
    assert s.reruns == [DRE5620_RUN]
    assert s.moves == [] and s.dispatched == []
    assert len(s.comments) == 1
    ident, body = s.comments[0]
    assert ident == "DRE-5620"
    assert body.startswith(limit_recovery.RECOVERY_MARK)
    assert any("DRE-5620 sync re-entered" in line for line in lines)


def test_a_held_card_s_agent_task_death_is_still_skipped():
    """The build stage is Agent Task: new agent work a person said stop to."""
    s = Seams()
    s.recover([card(bodies=[marker(kind="linear", stage="build", reset=None)],
                    labels=HELD)])
    assert s.moves == [] and s.dispatched == [] and s.reruns == []
    assert s.comments == []


@pytest.mark.parametrize("stage", ["classify", "plan", "build", "fix", "review"])
def test_every_stage_but_sync_still_honors_the_hold(stage):
    """Fail closed, as the medic gate does: review (QA Review) is not named as
    bookkeeping, so it keeps the hold too."""
    s = Seams()
    s.recover([card(lane="In Review", bodies=[marker(kind="linear", stage=stage, reset=None)],
                    labels=HELD)])
    assert s.moves == [] and s.dispatched == [] and s.reruns == [] and s.comments == []


def test_a_held_card_with_an_unknown_stage_is_skipped_not_handed_off():
    s = Seams()
    s.recover([card(bodies=[f"🪦 limit-death: kind=linear stage=mystery reset=unknown run={RUN}"],
                    labels=HELD)])
    assert s.moves == [] and s.reruns == [] and s.comments == []


def test_the_rule_is_the_medic_gate_s_own_not_a_copy():
    import medic_retry

    assert limit_recovery.medic_retry is medic_retry
    source = inspect.getsource(limit_recovery)
    for name in medic_retry.BOOKKEEPING_WORKFLOWS:
        assert f'"{name}"' not in source, f"limit_recovery spells {name!r} itself"
    # Every stage the marker can carry maps onto the workflow dead_run says
    # it came from, and only sync is bookkeeping.
    assert [s for s in dead_run.LIMIT_STAGES
            if not limit_recovery.hold_blocks_stage(s)] == ["sync"]
    assert limit_recovery.hold_blocks_stage("mystery")
    assert limit_recovery.hold_blocks_stage("")


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
         patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, \
         patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.recover_limit_deaths()
        seen["rerun"]("123")
        seen["move"]("DRE-1", "Todo")
        seen["lops"].cmd_comment("DRE-3", "receipt")
    # The receipt is posted through linear_ops, and every card the recovery
    # wrote to is recorded for the Planning watchdog (DRE-5841).
    cmd_comment.assert_called_once_with("DRE-3", "receipt")
    assert reconcile._limit_recovered == {"DRE-1", "DRE-3"}
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


# --------------------------------------------------------------------------
# the WIP room is promotion's own (DRE-4934)
# --------------------------------------------------------------------------
def _dre_4811_board():
    """agent-bureau on 2026-09-25, in miniature: a promotion base of 3 of 12
    (two ordinary builds plus the dead card itself, in Todo), and a repo that
    holds far more than 12 active cards once epics, hand-built cards and
    automation cards are counted. Promotion said `WIP 3+0/12`; recovery said
    the room was spent."""
    dead = card(ident="DRE-4811", lane="Todo",
                bodies=[marker(kind="linear", reset=datetime(2026, 1, 1, tzinfo=UTC))])
    work = [card(ident=f"DRE-{n}") for n in (101, 102)]
    epics = [
        {**card(ident=f"DRE-{200 + n}"), "title": f"[EPIC] epic {n}"}
        for n in range(10)
    ]
    by_hand = [card(ident=f"DRE-{300 + n}", labels=("repo:agent-bureau", "hand-built"))
               for n in range(4)]
    bots = [card(ident=f"DRE-{400 + n}",
                 labels=("repo:agent-bureau", reconcile.dependabot_card.LABEL))
            for n in range(3)]
    return dead, [dead, *work, *epics, *by_hand, *bots]


def test_a_limit_death_is_redispatched_when_promotion_has_room(monkeypatch, capsys):
    """RED on `main`: recovery counted every active card of the repo — the ten
    epics and the three automation cards included — so 15 of 12 read as no
    room, and DRE-4811 sat in Todo for twenty hours printing "WIP room is
    spent" beside a promotion step reading 3 of 12."""
    monkeypatch.setattr(reconcile, "MAX_WIP", 12)
    dead, board = _dre_4811_board()
    assert reconcile.wip_count(
        [c for c in board if reconcile.card_repo(c) == "agent-bureau"]
    ) >= 12, "the fixture must be a board the wide count calls full"
    dispatched = []
    with patch.object(reconcile, "active_cards", return_value=board), \
         patch.object(reconcile, "redispatch",
                      side_effect=lambda c: dispatched.append(c["identifier"]) or True), \
         patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.recover_limit_deaths()
    out = capsys.readouterr().out
    assert "WIP room is spent" not in out, out
    assert dispatched == ["DRE-4811"]


def test_recovery_and_promotion_read_one_wip_room(monkeypatch):
    """The two paths are handed the same number: whatever promotion is told the
    base is, recovery's room is the cap minus exactly that. MUTATION CHECK:
    widen either side's base and the two disagree."""
    monkeypatch.setattr(reconcile, "MAX_WIP", 12)
    _, board = _dre_4811_board()
    seen = {}

    def fake_recover(lops, now, active_account, wip_room, **kw):
        seen["wip_room"] = wip_room
        return []

    # The board carries epics, so the sweep's epic-growth refresh has work to
    # do; it writes descriptions, and nothing here is about it.
    mocks = {**_full_sweep_mocks(board), "report_epic_growth": MagicMock(return_value=[])}
    with patch.multiple(reconcile, **mocks), \
         patch.object(reconcile.limit_recovery, "recover", side_effect=fake_recover), \
         patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
         patch.object(reconcile.linear_ops, "cmd_state"), \
         patch.object(reconcile.linear_ops, "cmd_comment"), \
         patch.object(reconcile.linear_ops, "add_label"), \
         patch.object(reconcile, "redispatch", return_value=True):
        reconcile.main()
    active_count = mocks["promote_ready"].call_args.kwargs["active_count"]
    assert active_count == 3
    assert seen["wip_room"] == reconcile.MAX_WIP - active_count


# --------------------------------------------------------------------------
# a classify or plan death in Planning is re-entered (DRE-4211)
# --------------------------------------------------------------------------
def _linear_board(board):
    """Linear's side of the sweep's ONE board read: the cards in the lanes the
    read asked for, and a record of every read made. Patched in beneath
    `active_cards`, so the lanes the recovery pass is handed are the lanes it
    asked for — a stub of `active_cards` itself would answer any question with
    the whole board and could never see the defect."""
    reads: list[tuple[str, ...]] = []

    def fetch(states):
        reads.append(tuple(states))
        return [c for c in board if c["state"]["name"] in states]

    return fetch, reads


def _drive_recovery(board):
    fetch, reads = _linear_board(board)
    with patch.object(reconcile, "_fetch_active_cards", side_effect=fetch), \
         patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, \
         patch.object(reconcile.linear_ops, "cmd_comment") as cmd_comment:
        reconcile.recover_limit_deaths()
    moves = [tuple(c.args) for c in cmd_state.call_args_list]
    receipts = [c.args[0] for c in cmd_comment.call_args_list]
    return moves, receipts, reads


def test_a_classify_death_in_planning_is_reentered_by_the_sweep():
    """RED on `main`: the pass was handed `active_cards()` — Todo, In Progress
    and In Review — so a card whose classify run died on the Linear allowance
    sat in Planning and nothing ever re-entered it (2026-09-28, 13:35 to 15:02
    PT: the planner runs that died were re-sent by hand)."""
    dead = card(ident="DRE-4678", lane="Planning",
                bodies=[marker(kind="linear", stage="classify",
                               reset=datetime(2026, 1, 1, tzinfo=UTC))])
    theirs = card(ident="DRE-4679", lane="Planning", labels=("repo:portico",),
                  bodies=[marker(kind="linear", stage="classify",
                                 reset=datetime(2026, 1, 1, tzinfo=UTC))])
    moves, receipts, _ = _drive_recovery([dead, theirs])
    assert moves == [("DRE-4678", "Intake"), ("DRE-4678", "Planning")], moves
    assert receipts == ["DRE-4678"], "another repo's Planning card is its own sweep's"
    assert reconcile._write_failures == []


def test_a_classify_death_in_intake_is_reentered_by_the_sweep():
    dead = card(ident="DRE-4680", lane="Intake",
                bodies=[marker(kind="linear", stage="classify",
                               reset=datetime(2026, 1, 1, tzinfo=UTC))])
    moves, receipts, _ = _drive_recovery([dead])
    assert moves == [("DRE-4680", "Planning")], moves
    assert receipts == ["DRE-4680"]


def test_widening_the_pass_costs_no_extra_linear_read():
    """The lanes the pass now reads are all inside SWEPT_LANES, which the
    sweep's one board read already covers — so the pass buys nothing."""
    board = [card(ident="DRE-4678", lane="Planning",
                  bodies=[marker(kind="linear", stage="plan",
                                 reset=datetime(2026, 1, 1, tzinfo=UTC))])]
    _, _, reads = _drive_recovery(board)
    assert reads == [reconcile.SWEPT_LANES], reads


def test_planning_cards_do_not_spend_the_wip_room(monkeypatch, capsys):
    """The WIP room stays promotion's own (DRE-4934) now that the pass sees
    Planning and Intake: a card waiting on a plan occupies no build slot, and
    promotion never counts one. MUTATION CHECK: count the room over the
    widened list and the six Planning cards spend it, so the dead build card
    sits in Todo printing "WIP room is spent" beside a promotion with room."""
    monkeypatch.setattr(reconcile, "MAX_WIP", 3)
    dead = card(ident="DRE-4811", lane="Todo",
                bodies=[marker(kind="linear", reset=datetime(2026, 1, 1, tzinfo=UTC))])
    working = card(ident="DRE-101")
    planning = [card(ident=f"DRE-{500 + n}", lane="Planning") for n in range(6)]
    fetch, _ = _linear_board([dead, working, *planning])
    dispatched = []
    with patch.object(reconcile, "_fetch_active_cards", side_effect=fetch), \
         patch.object(reconcile, "redispatch",
                      side_effect=lambda c: dispatched.append(c["identifier"]) or True), \
         patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.recover_limit_deaths()
    out = capsys.readouterr().out
    assert "WIP room is spent" not in out, out
    assert dispatched == ["DRE-4811"]


# --------------------------------------------------------------------------
# the assumed clock is bounded, and a dead second-critic review is the
# re-review watcher's (DRE-5640)
# --------------------------------------------------------------------------
# DRE-5455 gives every Claude marker with no stated reset an assumed one,
# five hours on. Under a cap nothing here has the wording of (a weekly cap, a
# monthly spend limit) that would bring a card back into the same wall every
# five hours for days, so three assumed-clock deaths in a day hand the card to
# a person. And a `stage=review` death on a Planning card is the second
# critic's: a re-run keeps its run id and the medic marks a run id once, so
# the re-review watcher's fresh run is its re-entry, never a re-run here.
NOW = datetime(2026, 10, 4, 22, 0, tzinfo=UTC)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assumed_marker(stage="plan", reset=None, run=RUN) -> str:
    """An assumed-clock Claude marker as dead_run writes it (DRE-5455)."""
    return dead_run.decide(
        0, limit=dead_run.LimitDeath(kind="claude", stage=stage,
                                     reset=reset or NOW + timedelta(hours=4),
                                     run_id=run, reset_assumed=True),
    ).comments[0]


def dated_card(ident="DRE-5640", lane="Planning", nodes=(), labels=()):
    """A card whose comment nodes carry `createdAt`, as the board read selects
    it (`linear_ops.COMMENT_FIELDS`). `nodes` is (body, hours_ago) oldest→newest;
    hours_ago None leaves the node with no `createdAt`."""
    c = card(ident=ident, lane=lane, labels=labels)
    built = []
    for body, hours_ago in nodes:
        node = {"body": body}
        if hours_ago is not None:
            node["createdAt"] = _iso(hours_ago)
        built.append(node)
    c["comments"] = {"nodes": list(reversed(built))}
    return c


def _reentered(stage="plan") -> str:
    return (f"{limit_recovery.RECOVERY_MARK} re-entered {stage} — window reset at "
            f"2026-10-04 10:00 PT. Bounced Planning → Intake → Planning.")


def _three_deaths(ages=(11, 6, 1), *, between=None, stage="plan", newest=None):
    """Three assumed deaths with the recovery's own 🔁 receipt after the first
    and the second — the card came back twice and died a third time."""
    first, second, third = ages
    nodes = [(assumed_marker(stage), first), (between or _reentered(), first - 0.5),
             (assumed_marker(stage), second), (_reentered(), second - 0.5),
             (newest or assumed_marker(stage), third)]
    return nodes


def test_the_contract_constants():
    assert limit_recovery.CLAUDE_ASSUMED_DEATHS_MAX == 3
    assert limit_recovery.CLAUDE_ASSUMED_SPAN_HOURS == 24
    assert limit_recovery.HANDOFF_MARK == "⚠️ limit-recovery:"
    assert limit_recovery.RECOVERY_MARK == "🔁 limit-recovery:"


def test_three_assumed_deaths_in_a_day_hand_the_card_to_a_person_once():
    s = Seams()
    c = dated_card(nodes=_three_deaths())
    lines = s.recover([c], now=NOW)
    assert s.moves == [] and s.reruns == [] and s.dispatched == []
    assert len(s.comments) == 1
    ident, body = s.comments[0]
    assert ident == "DRE-5640"
    assert body.startswith(limit_recovery.HANDOFF_MARK)
    assert limit_recovery.is_receipt(body), "the hand-off must close the marker"
    assert "three times in a day" in body
    assert "named no reset time" in body and "assumed five-hour clock" in body
    assert "not the five-hour usage window" in body
    for way_back in ("Intake then Planning", "Todo for a build", "Re-run failed jobs"):
        assert way_back in body, way_back
    assert any("DRE-5640 handed to a human" in line for line in lines)
    assert not any(line.startswith("ERROR:") for line in lines)
    again = Seams()
    again.recover([dated_card(nodes=[*_three_deaths(), (body, 0.5)])], now=NOW)
    assert again.comments == [] and again.moves == [] and again.reruns == []


def test_two_assumed_deaths_wait_and_say_the_clock_was_assumed():
    s = Seams()
    nodes = [(assumed_marker(), 6), (_reentered(), 5.5), (assumed_marker(), 1)]
    lines = s.recover([dated_card(nodes=nodes)], now=NOW)
    assert s.comments == [] and s.moves == [] and s.reruns == []
    assert any("DRE-5640 is waiting until" in line
               and "(assumed — the run named no reset) (claude limit, plan stage)" in line
               for line in lines), lines


def test_a_stated_reset_says_nothing_about_an_assumption():
    s = Seams()
    lines = s.recover([card(bodies=[marker()])], now=BEFORE)
    assert not any("assumed" in line for line in lines), lines


def test_a_death_older_than_a_day_is_not_counted():
    s = Seams()
    s.recover([dated_card(nodes=_three_deaths(ages=(25, 6, 1)))], now=NOW)
    assert s.comments == [] and s.moves == []


def test_the_count_starts_over_behind_a_handoff_receipt():
    handoff = f"{limit_recovery.HANDOFF_MARK} cannot bring this card back on its own — earlier."
    s = Seams()
    s.recover([dated_card(nodes=_three_deaths(between=handoff))], now=NOW)
    assert s.comments == [] and s.moves == []


def test_a_marker_with_no_assumed_field_is_not_counted():
    stated = marker(stage="plan", reset=NOW + timedelta(hours=4))
    assert dead_run.parse_limit_marker(stated)["assumed"] is False
    s = Seams()
    nodes = _three_deaths()
    nodes[2] = (stated, 6)
    s.recover([dated_card(nodes=nodes)], now=NOW)
    assert s.comments == [] and s.moves == []


def test_a_node_with_no_created_at_is_never_counted():
    s = Seams()
    nodes = _three_deaths()
    nodes[0] = (nodes[0][0], None)
    s.recover([dated_card(nodes=nodes)], now=NOW)
    assert s.comments == [] and s.moves == []


def test_an_assumed_reset_that_has_passed_bounces_the_card_like_a_stated_one():
    s = Seams()
    due = assumed_marker(reset=NOW - timedelta(minutes=1))
    s.recover([dated_card(nodes=[(due, 5)])], now=NOW)
    assert s.moves == [("DRE-5640", "Intake"), ("DRE-5640", "Planning")]
    assert len(s.comments) == 1
    body = s.comments[0][1]
    assert body.startswith(limit_recovery.RECOVERY_MARK)
    assert f"window reset at {dead_run.pacific(NOW - timedelta(minutes=1))}" in body


def test_handoff_reason_keeps_its_two_argument_shape():
    m = dead_run.parse_limit_marker(assumed_marker())
    assert limit_recovery.handoff_reason({}, m) is None
    assert limit_recovery.handoff_reason({}, m, assumed_deaths=2) is None
    assert "three times in a day" in limit_recovery.handoff_reason({}, m, assumed_deaths=3)


def _review_death(minutes_ago=1) -> str:
    reset = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"🪦 limit-death: kind=claude stage=review reset={reset} run=777 assumed=yes"


def test_a_review_death_on_a_planning_card_is_the_rereview_watchers():
    s = Seams()
    lines = s.recover([dated_card(nodes=[(_review_death(), 5)])], now=NOW)
    assert s.reruns == [] and s.moves == [] and s.dispatched == []
    assert s.comments == []
    assert ("limit-recovery: DRE-5640 review death (claude limit) is the re-review "
            "watcher's — it asks for the review again after its own grace and does "
            "not read the Claude wall") in lines
    assert not any("once the wall is down" in line for line in lines), lines


def test_a_review_death_on_a_planning_card_spends_no_wip_room():
    s = Seams()
    lines = s.recover([dated_card(nodes=[(_review_death(), 5)])], now=NOW, wip_room=0)
    assert not any("WIP room" in line for line in lines), lines
    assert any("re-review watcher's" in line for line in lines)


def test_a_review_death_in_review_is_rerun_as_today():
    s = Seams()
    s.recover([dated_card(lane="In Review", nodes=[(_review_death(), 5)])], now=NOW)
    assert s.reruns == ["777"]
    assert len(s.comments) == 1
    assert s.comments[0][1].startswith(limit_recovery.RECOVERY_MARK)


def test_a_third_assumed_review_death_on_a_planning_card_is_handed_off():
    s = Seams()
    nodes = _three_deaths(stage="review", newest=_review_death())
    s.recover([dated_card(nodes=nodes)], now=NOW)
    assert s.reruns == [] and s.moves == [] and s.dispatched == []
    assert len(s.comments) == 1
    body = s.comments[0][1]
    assert body.startswith(limit_recovery.HANDOFF_MARK)
    assert "three times in a day" in body
    # The way back is a fresh review, never a re-run of the dead one.
    assert "re-review watcher" in body
    import review_rerun
    assert f"`{review_rerun.RERUN_REVIEW_ACT}` on the epic" in body
    assert "Not Re-run failed jobs" in body
    for other in ("Intake then Planning", "Todo for a build"):
        assert other not in body, other


def test_a_third_assumed_review_death_in_review_keeps_the_rerun_way_back():
    s = Seams()
    nodes = _three_deaths(stage="review", newest=_review_death())
    s.recover([dated_card(lane="In Review", nodes=nodes)], now=NOW)
    assert len(s.comments) == 1
    body = s.comments[0][1]
    assert body.startswith(limit_recovery.HANDOFF_MARK)
    assert "Re-run failed jobs for a fix, review or sync run" in body
    assert "re-review watcher" not in body
