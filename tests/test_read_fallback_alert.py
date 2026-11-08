"""A full sweep pass that fell back to Linear is a console alert (DRE-8798).

Before this card a read that fell back to Linear was one `read-fallback:` line
in a log nobody reads (DRE-8794, DRE-8796). The full sweep now hands
`bureau_read.fallbacks()` to `read_fallback_alert.settle` at the end of its own
pass, which raises one WARN console alert per repo naming the readers that fell
back, and resolves it on a full pass that did not. What these pin, each against
the failure it exists to stop:

* one raise per repo, at WARN, with no fallback card, its body naming each
  distinct `(reader, read, reason)` and its count;
* a pass with no fallback raises nothing and resolves the repo's alert;
* a door that is not `on` makes no notice call at all;
* a raise the notice door cannot take is `lost`, files no Linear card and
  fails nothing;
* exactly one `read-fallback-alert:` line per pass, naming the outcome and
  the groups;
* the full sweep calls it, with a board read that really fell back; the
  `--promote-only` gate, a card-scoped pass and an idle pass never do; and
  nothing it raises moves the exit.

No test here reaches the console, GitHub or Linear: the notice door's wire is
replaced at the module boundary, the read door is `on` with no URL (so every
door read is UNKNOWN), and the sweep runs over a fake board.
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
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402
import read_fallback_alert  # noqa: E402
import reconcile  # noqa: E402
import standing_defect_alert  # noqa: E402

REPO = "bureau-pipeline"
KEY = f"read-fallback:{REPO}"
SWEEP = "Reconcile / reconcile / reconcile.py"
BUILD = "Agent Task / build / reconcile.py"
#: Three fallbacks in two distinct groups: the board read twice, cards once.
THREE = [
    (SWEEP, "board", "unavailable"),
    (BUILD, "cards", "timeout"),
    (SWEEP, "board", "unavailable"),
]


class Door:
    """The notice client, as `settle` sees it: every raise and resolve."""

    def __init__(self, *, outcome="raised", resolved="resolved"):
        self.raised: list[dict] = []
        self.resolved: list[str] = []
        self.outcome = outcome
        self.resolve_outcome = resolved

    def raise_notice(self, notice, **kw):
        self.raised.append({"notice": notice, **kw})
        return self.outcome

    def resolve_notice(self, notice, **kw):
        self.resolved.append(notice)
        return self.resolve_outcome


@pytest.fixture
def door(monkeypatch):
    d = Door()
    monkeypatch.setattr(pipeline_notice, "raise_notice", d.raise_notice)
    monkeypatch.setattr(pipeline_notice, "resolve_notice", d.resolve_notice)
    return d


def _said(capsys) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines()
            if line.startswith(read_fallback_alert.TAG)]


# ── the contract's constants ────────────────────────────────────────────────


def test_the_contract_constants():
    assert read_fallback_alert.TAG == "read-fallback-alert:"
    assert read_fallback_alert.NOTICE_PREFIX == "read-fallback"
    assert read_fallback_alert.SEVERITY == "WARN"
    assert read_fallback_alert.notice_key(REPO) == KEY


def test_the_notice_key_is_one_the_door_admits():
    pipeline_notice._check_key(read_fallback_alert.notice_key("agent-bureau"))


# ── settle: a pass that fell back raises, once, naming the readers ──────────


def test_three_fallbacks_in_two_groups_raise_once_at_warn(door, capsys):
    line = read_fallback_alert.settle(THREE, REPO, "on")
    assert len(door.raised) == 1
    raised = door.raised[0]
    assert raised["notice"] == KEY
    assert raised["severity"] == "WARN"
    assert raised["fallback"] is None
    assert REPO in raised["title"] and "3" in raised["title"]
    body_lines = [b for b in raised["body"].splitlines() if "read=" in b]
    assert len(body_lines) == 2
    assert any(f'reader="{SWEEP}"' in b and "read=board" in b and "reason=unavailable" in b
               and "×2" in b for b in body_lines)
    assert any(f'reader="{BUILD}"' in b and "read=cards" in b and "reason=timeout" in b
               and "×1" in b for b in body_lines)
    assert door.resolved == []
    said = _said(capsys)
    assert said == [line]
    assert line.startswith("read-fallback-alert: raised ")
    assert f"repo={REPO}" in line
    assert f'reader="{SWEEP}" read=board reason=unavailable ×2' in line
    assert f'reader="{BUILD}" read=cards reason=timeout ×1' in line


@pytest.mark.parametrize("outcome", ["repeat", "escalated"])
def test_the_doors_outcome_is_the_lines(door, capsys, outcome):
    door.outcome = outcome
    line = read_fallback_alert.settle(THREE[:1], REPO, "on")
    assert line.startswith(f"read-fallback-alert: {outcome} ")
    assert _said(capsys) == [line]


def test_the_groups_keep_first_seen_order(door):
    line = read_fallback_alert.settle(THREE, REPO, "on")
    assert line.index("read=board") < line.index("read=cards")


# ── settle: a pass that did not fall back resolves ──────────────────────────


@pytest.mark.parametrize("outcome", ["resolved", "not-open", "resolve-refused"])
def test_no_fallback_in_mode_on_resolves_and_raises_nothing(door, capsys, outcome):
    door.resolve_outcome = outcome
    line = read_fallback_alert.settle([], REPO, "on")
    assert door.raised == []
    assert door.resolved == [KEY]
    assert _said(capsys) == [line]
    assert line.startswith(f"read-fallback-alert: {outcome} ")
    assert f"repo={REPO}" in line


# ── settle: a door that is not on is not asked ──────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_a_door_not_on_makes_no_notice_call(door, capsys, mode):
    line = read_fallback_alert.settle([], REPO, mode)
    assert door.raised == [] and door.resolved == []
    assert _said(capsys) == [line]
    assert line.startswith(f"read-fallback-alert: door {mode} ")
    assert f"repo={REPO}" in line


# ── settle: a raise the door cannot take is lost, and costs nothing ─────────


@pytest.mark.parametrize("missed", [
    pipeline_notice.DoorMissed("http-503", "the door refused"),
    pipeline_notice.DoorMissed("timeout", "no answer in 8s"),
])
def test_a_raise_the_door_cannot_take_is_lost_and_files_no_card(monkeypatch, capsys, missed):
    """Through the real client: the wire refuses or times out, and no Linear
    card is filed — that would spend Linear while the door is down."""
    def _send(payload):
        raise missed
    monkeypatch.setattr(pipeline_notice, "_send", _send)
    create = mock.MagicMock(side_effect=AssertionError("a Linear card was filed"))
    monkeypatch.setattr(linear_ops, "create_card", create)
    line = read_fallback_alert.settle(THREE, REPO, "on")
    create.assert_not_called()
    assert line.startswith("read-fallback-alert: lost ")
    assert 'read=board reason=unavailable ×2' in line
    assert _said(capsys) == [line]


# ── the sweep: one call, at the end of the full pass ────────────────────────


#: Every backstop the full sweep runs that is not under test.
_STOOD_DOWN = (
    "drain_retiring_lanes", "unstick_conflicts", "refresh_stale_merge_refs",
    "retrigger_dead_heads", "flag_no_checks_prs", "flag_unowned_prs",
    "fix_approved_but_red", "retry_dead_fix_runs", "redispatch_standing_verdicts",
    "recover_limit_deaths", "restart_answered_blockers", "card_dependabot_prs",
    "review_dependabot_prs", "recover_crashed_reviews",
    "report_fleet_reviewer_outage", "check_dependabot_capacity",
    "report_intake_depth", "repair_frozen_planning_holds", "close_finished_epics",
    "report_break_glass", "report_fix_concurrency", "report_evicted_fix_runs",
    "rerun_runner_lost_ci", "resync_desynced_heads", "flag_unlanded_work",
)


@pytest.fixture
def board(monkeypatch):
    """An empty fake board behind a read door that is `on` with no URL: every
    door read is UNKNOWN, so the work-lane board read falls back to Linear —
    `_fetch_active_cards`, answered here — and is named by the client."""
    for name in ("BUREAU_READ_URL", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.reset_for_tests()
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(reconcile, "REPO_SLUG", REPO)
    ledgers = (reconcile._write_failures, reconcile._read_failures,
               reconcile._stale_defects, reconcile._card_skips, reconcile._degraded)
    for ledger in ledgers:
        ledger.clear()
    stubs = {name: mock.MagicMock() for name in _STOOD_DOWN}
    stubs.update({
        "flag_stranded": mock.MagicMock(return_value=set()),
        "report_epic_growth": mock.MagicMock(return_value=[]),
        "_fetch_active_cards": mock.MagicMock(return_value=[]),
        "backlog_children": mock.MagicMock(return_value=[]),
        "merged_card_scope": mock.MagicMock(return_value=None),
        "sweep_idle": mock.MagicMock(return_value=None),
    })
    for name, stub in stubs.items():
        monkeypatch.setattr(reconcile, name, stub)
    lops = reconcile.linear_ops
    monkeypatch.setattr(lops, "open_pass", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_comment", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_advance", mock.MagicMock())
    monkeypatch.setattr(lops, "cmd_state", mock.MagicMock())
    monkeypatch.setattr(lops, "comment_bodies", mock.MagicMock(return_value=[]))
    monkeypatch.setattr(lops, "count_comments", mock.MagicMock(return_value=0))
    offline = mock.MagicMock(side_effect=lops.LinearError("offline in this test"))
    monkeypatch.setattr(lops, "gql", offline)
    monkeypatch.setattr(lops, "gql_paged", offline)
    monkeypatch.setattr(reconcile.subprocess, "run", mock.MagicMock(
        side_effect=lambda argv, **kw: mock.Mock(
            returncode=0, stdout="[]" if argv[:3] == ["gh", "pr", "list"] else "",
            stderr="")))
    monkeypatch.setattr(reconcile.time, "sleep", lambda s: None)
    yield
    for ledger in ledgers:
        ledger.clear()
    bureau_read.reset_for_tests()


@pytest.fixture
def notice_door(monkeypatch):
    """The notice door's wire: every raise and resolve the sweep makes,
    through the real client, is recorded here."""
    sent: list[dict] = []

    def _send(payload):
        sent.append(payload)
        return "raised" if payload["action"] == "raise" else "resolved"

    monkeypatch.setattr(pipeline_notice, "_send", _send)
    monkeypatch.setattr(standing_defect_alert, "settle", mock.MagicMock(return_value=[]))
    return sent


def _exit_of(**kw) -> str | None:
    try:
        reconcile.main(**kw)
    except SystemExit as e:
        return str(e.code)
    return None


def _fallback_sends(notice_door) -> list[dict]:
    return [p for p in notice_door if p["notice"].startswith("read-fallback:")]


def test_a_full_pass_whose_board_read_fell_back_raises_the_alert(board, notice_door, capsys):
    _exit_of()
    assert ("read-fallback:" in capsys.readouterr().err)  # the client named it
    sends = _fallback_sends(notice_door)
    assert [(p["action"], p["notice"]) for p in sends] == [("raise", KEY)]
    assert sends[0]["severity"] == "WARN"
    assert "read=board reason=unavailable" in sends[0]["body"]
    assert any(read == "board" for _reader, read, _reason in bureau_read.fallbacks())


def test_a_full_pass_prints_one_alert_line_naming_the_board_read(board, notice_door, capsys):
    _exit_of()
    said = _said(capsys)
    assert len(said) == 1
    assert said[0].startswith("read-fallback-alert: raised ")
    assert "read=board reason=unavailable" in said[0]


def test_the_full_pass_hands_settle_its_fallbacks_and_mode_once(board, notice_door,
                                                               monkeypatch):
    seen: list = []
    monkeypatch.setattr(read_fallback_alert, "settle",
                        lambda fallbacks, repo, mode: seen.append((list(fallbacks), repo, mode)))
    _exit_of()
    assert len(seen) == 1
    fallbacks, repo, mode = seen[0]
    assert repo == REPO and mode == "on"
    assert fallbacks == bureau_read.fallbacks() and fallbacks


def test_a_promote_only_run_makes_no_notice_call(board, notice_door, monkeypatch):
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of(promote_only=True)
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()


def _merged_scope():
    return reconcile.MergeScope(card="DRE-41", dependents=["DRE-42"],
                                parent="DRE-40", parent_state="In Progress")


@pytest.mark.parametrize("kw", [{"promote_only": True}, {"close_only": True}],
                         ids=["promote-only", "close-only"])
def test_a_card_scoped_pass_makes_no_notice_call(board, notice_door, monkeypatch, kw):
    monkeypatch.setattr(reconcile, "merged_card_scope",
                        mock.MagicMock(return_value=_merged_scope()))
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of(**kw)
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()


def test_an_idle_pass_neither_raises_nor_resolves(board, notice_door, monkeypatch, capsys):
    """An idle pass skips the board read, so its fallbacks are not a full
    pass's: settling there would resolve what the next full pass raises."""
    monkeypatch.setattr(reconcile, "sweep_idle", mock.MagicMock(
        return_value="no card of this repo is in motion or waiting to be promoted"))
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of()
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()
    said = _said(capsys)
    assert len(said) == 1 and "idle" in said[0]


def test_a_settle_that_raises_prints_one_line_and_changes_no_exit(board, notice_door,
                                                                 monkeypatch, capsys):
    before = _exit_of()
    capsys.readouterr()
    reconcile._stale_defects.clear()
    bureau_read.reset_for_tests()
    monkeypatch.setattr(read_fallback_alert, "settle",
                        mock.MagicMock(side_effect=RuntimeError("the door exploded")))
    assert _exit_of() == before
    said = _said(capsys)
    assert len(said) == 1 and "the door exploded" in said[0]


def test_a_lost_raise_changes_no_exit(board, notice_door, monkeypatch, capsys):
    before = _exit_of()
    reconcile._stale_defects.clear()
    bureau_read.reset_for_tests()

    def _refused(payload):
        raise pipeline_notice.DoorMissed("http-503", "the door refused")
    monkeypatch.setattr(pipeline_notice, "_send", _refused)
    create = mock.MagicMock(side_effect=AssertionError("a Linear card was filed"))
    monkeypatch.setattr(linear_ops, "create_card", create)
    capsys.readouterr()
    assert _exit_of() == before
    create.assert_not_called()
    said = _said(capsys)
    assert len(said) == 1 and said[0].startswith("read-fallback-alert: lost ")


# ── proof-reach keeps the line ──────────────────────────────────────────────


def test_proof_reach_declares_the_line_held_to_the_tag():
    import json
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    entries = [e for e in doc["log_lines"] if e["tag"] == read_fallback_alert.TAG]
    assert len(entries) == 1
    assert entries[0]["printed_by"] == "scripts/read_fallback_alert.py"
    assert entries[0]["constant"] == "TAG"
