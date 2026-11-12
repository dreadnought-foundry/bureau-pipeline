"""A full sweep pass that fell back to Linear is a console alert (DRE-8798),
counted over every step of the sweep job (DRE-9251).

Before DRE-8798 a read that fell back to Linear was one `read-fallback:` line
in a log nobody reads (DRE-8794, DRE-8796). `read_fallback_alert.settle`
raises one WARN console alert per repo naming the readers that fell back, and
resolves it on a full pass that did not. Until DRE-9251 the sweep settled it
from its own process's fallbacks alone, so run 41697018402 resolved the alert
in its Sweep step while two `Proof overdue` fallbacks printed further down the
same log. Now every door-reading step of the job copies its read lines to one
lines file, `reconcile.py` only writes whether this pass may settle, and the
job's last step settles from both. What these pin, each against the failure
it exists to stop:

* one raise per repo, at WARN, with no fallback card, its body naming each
  distinct `(reader, read, reason)` and its count;
* a pass with no fallback raises nothing and resolves the repo's alert;
* a door that is not `on` makes no notice call at all;
* a raise the notice door cannot take is `lost`, files no Linear card and
  fails nothing;
* exactly one `read-fallback-alert:` line per pass, naming the outcome, the
  groups and the processes it read;
* the full sweep makes no notice call for this alert and prints no line: it
  writes the settle file at its end, `idle (<why>)` on an idle pass, and the
  `--promote-only` gate and a card-scoped pass write none;
* `read_fallback_alert.py settle` raises on another step's fallbacks, never
  resolves on a missing lines file, settles nothing without a settle file that
  says it may, and — as its own process — prints its line before its own
  `read-door: served` exit line and never writes the lines file it read.

No test here reaches the console, GitHub or Linear: the notice door's wire is
replaced at the module boundary, the read door is `on` with no URL (so every
door read is UNKNOWN), and the sweep runs over a fake board.
"""
from __future__ import annotations

import json
import os
import subprocess
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


@pytest.fixture
def settle_file(monkeypatch, tmp_path):
    """`$RUNNER_TEMP`, as the job sets it, and the settle file the pass writes."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    return tmp_path / read_fallback_alert.SETTLE_FILE


def test_a_full_pass_whose_board_read_fell_back_makes_no_notice_call(board, notice_door,
                                                                    settle_file, capsys):
    """The Settle step settles; the sweep's own process never calls the door
    for this alert, whatever it fell back on."""
    _exit_of()
    assert "read-fallback:" in capsys.readouterr().err  # the client named it
    assert any(read == "board" for _reader, read, _reason in bureau_read.fallbacks())
    assert _fallback_sends(notice_door) == []


def test_a_full_pass_prints_no_alert_line(board, notice_door, settle_file, capsys):
    _exit_of()
    assert _said(capsys) == []


def test_a_full_pass_never_calls_settle(board, notice_door, settle_file, monkeypatch):
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of()
    settle.assert_not_called()


def test_a_full_pass_that_reached_its_end_writes_that_it_may_settle(board, notice_door,
                                                                    settle_file):
    _exit_of()
    assert json.loads(settle_file.read_text()) == {"settle": True}
    assert read_fallback_alert.settle_refusal(str(settle_file)) is None


def test_a_full_pass_with_no_runner_temp_writes_nothing(board, notice_door, monkeypatch,
                                                        tmp_path):
    monkeypatch.delenv("RUNNER_TEMP", raising=False)
    monkeypatch.chdir(tmp_path)
    _exit_of()
    assert list(tmp_path.iterdir()) == []


def test_a_promote_only_run_makes_no_notice_call(board, notice_door, settle_file, monkeypatch):
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of(promote_only=True)
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()
    assert not settle_file.exists()


def _merged_scope():
    return reconcile.MergeScope(card="DRE-41", dependents=["DRE-42"],
                                parent="DRE-40", parent_state="In Progress")


@pytest.mark.parametrize("kw", [{"promote_only": True}, {"close_only": True}],
                         ids=["promote-only", "close-only"])
def test_a_card_scoped_pass_makes_no_notice_call(board, notice_door, settle_file,
                                                monkeypatch, kw):
    monkeypatch.setattr(reconcile, "merged_card_scope",
                        mock.MagicMock(return_value=_merged_scope()))
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of(**kw)
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()
    assert not settle_file.exists()


IDLE_WHY = "no card of this repo is in motion or waiting to be promoted"


def test_an_idle_pass_writes_why_it_may_not_settle(board, notice_door, settle_file,
                                                   monkeypatch, capsys):
    """An idle pass skips the board read, so its reads are not a full pass's:
    settling there would resolve what the next full pass raises."""
    monkeypatch.setattr(reconcile, "sweep_idle", mock.MagicMock(return_value=IDLE_WHY))
    settle = mock.MagicMock()
    monkeypatch.setattr(read_fallback_alert, "settle", settle)
    _exit_of()
    assert _fallback_sends(notice_door) == []
    settle.assert_not_called()
    assert _said(capsys) == []
    assert json.loads(settle_file.read_text()) == {"settle": False,
                                                   "why": f"idle ({IDLE_WHY})"}
    assert read_fallback_alert.settle_refusal(str(settle_file)) == f"idle ({IDLE_WHY})"


def test_a_settle_file_that_cannot_be_written_changes_no_exit(board, notice_door,
                                                              settle_file, monkeypatch,
                                                              tmp_path):
    before = _exit_of()
    reconcile._stale_defects.clear()
    bureau_read.reset_for_tests()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "no" / "such" / "directory"))
    assert _exit_of() == before


# ── `read_fallback_alert.py settle`: the job's last step ────────────────────

OVERDUE = "Reconcile / sweep / proof_overdue_sweep.py"
SERVED_ON = "read-door: served {n} unknown 0 unavailable 1 (mode on) 40ms"


def _fallback_line(reader, read, reason):
    return f'read-fallback: reader="{reader}" read={read} reason={reason}'


@pytest.fixture
def job(monkeypatch, tmp_path):
    """The Settle step's world: the door `on`, a lines file and a settle file
    in `$RUNNER_TEMP`, and a fresh read-door state for this process."""
    for name in ("BUREAU_READ_URL", "GITHUB_WORKFLOW", "GITHUB_JOB",
                 bureau_read.LINES_FILE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.reset_for_tests()

    class Job:
        lines = tmp_path / read_fallback_alert.LINES_FILE
        settle = tmp_path / read_fallback_alert.SETTLE_FILE

        def write_lines(self, *lines):
            self.lines.write_text("".join(f"{ln}\n" for ln in lines))

        def may_settle(self):
            self.settle.write_text(json.dumps({"settle": True}))

        def argv(self):
            return ["settle", "--lines-file", str(self.lines),
                    "--settle-file", str(self.settle), "--repo", REPO]

    yield Job()
    bureau_read.reset_for_tests()


#: What the Sweep step and one `Proof overdue` process copy to the lines file
#: when only the latter fell back — run 41697018402's shape.
OVERDUE_RUN = (
    "read-door: mode on (BUREAU_READ=on, read once for this run)",
    "linear-budget: 12 request(s)",
    SERVED_ON.format(n=9),
    "read-door: mode on (BUREAU_READ=on, read once for this run)",
    _fallback_line(OVERDUE, "cards", "unavailable"),
    _fallback_line(OVERDUE, "dependents", "timeout"),
    SERVED_ON.format(n=0),
)


def test_another_steps_fallbacks_raise_the_alert(door, job, capsys):
    job.write_lines(*OVERDUE_RUN)
    job.may_settle()
    assert read_fallback_alert.main(job.argv()) == 0
    assert door.resolved == []
    assert len(door.raised) == 1
    raised = door.raised[0]
    assert raised["notice"] == KEY and raised["severity"] == "WARN"
    assert f'reader="{OVERDUE}" read=cards reason=unavailable ×1' in raised["body"]
    assert f'reader="{OVERDUE}" read=dependents reason=timeout ×1' in raised["body"]
    (line,) = _said(capsys)
    assert line.startswith(
        f"read-fallback-alert: raised repo={REPO} 2 fallback(s) from 2 process(es) — ")
    assert f'reader="{OVERDUE}" read=cards reason=unavailable ×1' in line
    assert "resolved" not in line


def test_no_fallback_in_three_processes_resolves(door, job, capsys):
    job.write_lines(*(SERVED_ON.format(n=n) for n in (3, 0, 1)))
    job.may_settle()
    assert read_fallback_alert.main(job.argv()) == 0
    assert door.raised == [] and door.resolved == [KEY]
    assert _said(capsys) == [
        f"read-fallback-alert: resolved repo={REPO} 0 fallback(s) from 3 process(es)"]


@pytest.mark.parametrize("settle_text,why", [
    (None, "no settle file"),
    (json.dumps({"settle": False, "why": f"idle ({IDLE_WHY})"}), f"idle ({IDLE_WHY})"),
    ("{not json", "settle file unreadable"),
    (json.dumps({"why": "nothing said"}), "nothing said"),
], ids=["missing", "idle", "malformed", "no-verdict"])
def test_no_settle_file_or_one_that_says_not_settles_nothing(door, job, capsys,
                                                             settle_text, why):
    job.write_lines(*OVERDUE_RUN)
    if settle_text is not None:
        job.settle.write_text(settle_text)
    assert read_fallback_alert.main(job.argv()) == 0
    assert door.raised == [] and door.resolved == []
    (line,) = _said(capsys)
    assert line.startswith(f"read-fallback-alert: not settled this pass — {why}")


@pytest.mark.parametrize("make", ["missing", "a-directory"])
def test_a_lines_file_it_cannot_read_never_resolves(door, job, capsys, make):
    job.may_settle()
    if make == "a-directory":
        job.lines.mkdir()
    assert read_fallback_alert.main(job.argv()) == 0
    assert door.raised == [] and door.resolved == []
    assert _said(capsys) == ["read-fallback-alert: not settled this pass — no lines file"]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_the_door_line_is_unchanged_when_the_door_is_not_on(door, job, capsys,
                                                            monkeypatch, mode):
    monkeypatch.setenv("BUREAU_READ", mode)
    job.write_lines(*OVERDUE_RUN)
    job.may_settle()
    assert read_fallback_alert.main(job.argv()) == 0
    assert door.raised == [] and door.resolved == []
    assert _said(capsys) == [f"read-fallback-alert: door {mode} repo={REPO} — "
                             "no fallback is noted; no notice call"]


def test_a_settle_that_raises_says_so_and_exits_zero(door, job, capsys, monkeypatch):
    job.write_lines(*OVERDUE_RUN)
    job.may_settle()
    monkeypatch.setattr(pipeline_notice, "raise_notice",
                        mock.MagicMock(side_effect=RuntimeError("the door exploded")))
    assert read_fallback_alert.main(job.argv()) == 0
    (line,) = _said(capsys)
    assert line == ("read-fallback-alert: not settled this pass — "
                    "RuntimeError: the door exploded")


def test_reading_the_lines_counts_fallbacks_and_served_processes(tmp_path):
    path = tmp_path / "lines.log"
    path.write_text("\n".join([
        *OVERDUE_RUN,
        _fallback_line("Reconcile / sweep / reconcile.py", "board", "unavailable"),
        "read-fallback: something this client never prints",
        "linear-read: site=x",
        "  read-door: served 4 (indented, not a line that opens with it)",
    ]) + "\n")
    fallbacks, processes = read_fallback_alert.read_lines(str(path))
    assert processes == 2
    assert fallbacks == [
        (OVERDUE, "cards", "unavailable"),
        (OVERDUE, "dependents", "timeout"),
        ("Reconcile / sweep / reconcile.py", "board", "unavailable"),
        ("?", "?", "unparsed"),
    ]


def test_the_cli_names_the_files_the_workflow_writes():
    assert read_fallback_alert.LINES_FILE == "sweep-read-lines.log"
    assert read_fallback_alert.SETTLE_FILE == "read-fallback-settle.json"


# ── the Settle process, as its own process ──────────────────────────────────

_SETTLE_PROCESS = r'''
import json, runpy, sys
scripts, record = sys.argv[1], sys.argv[2]
sys.path.insert(0, scripts)
import pipeline_notice

def _send(payload):
    with open(record, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
    return "raised" if payload["action"] == "raise" else "resolved"

pipeline_notice._send = _send
sys.argv = [scripts + "/read_fallback_alert.py", *sys.argv[3:]]
runpy.run_path(scripts + "/read_fallback_alert.py", run_name="__main__")
'''


@pytest.mark.parametrize("lines_env", [False, True],
                         ids=["step-env", "lines-file-env-set-anyway"])
def test_the_settle_process_prints_its_line_before_its_exit_line(tmp_path, lines_env):
    lines = tmp_path / read_fallback_alert.LINES_FILE
    lines.write_text("".join(f"{ln}\n" for ln in OVERDUE_RUN))
    before = lines.read_bytes()
    settle = tmp_path / read_fallback_alert.SETTLE_FILE
    settle.write_text(json.dumps({"settle": True}))
    record = tmp_path / "sent.jsonl"
    env = dict(os.environ, BUREAU_READ="on")
    for name in ("BUREAU_READ_URL", "GITHUB_WORKFLOW", "GITHUB_JOB", "PYTHONUNBUFFERED",
                 bureau_read.LINES_FILE_ENV):
        env.pop(name, None)
    if lines_env:
        env[bureau_read.LINES_FILE_ENV] = str(lines)
    proc = subprocess.run(
        [sys.executable, "-c", _SETTLE_PROCESS, str(ROOT / "scripts"), str(record),
         "settle", "--lines-file", str(lines), "--settle-file", str(settle),
         "--repo", REPO],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stdout
    out = proc.stdout.splitlines()
    alert = [i for i, ln in enumerate(out) if ln.startswith(read_fallback_alert.TAG)]
    served = [i for i, ln in enumerate(out) if ln.startswith("read-door: served")]
    mode = [i for i, ln in enumerate(out) if ln.startswith("read-door: mode on")]
    assert len(alert) == 1 and len(served) == 1 and len(mode) == 1, proc.stdout
    assert mode[0] < alert[0] < served[0], proc.stdout
    assert out[alert[0]].startswith(
        f"read-fallback-alert: raised repo={REPO} 2 fallback(s) from 2 process(es) — ")
    assert out[served[0]].startswith("read-door: served 0 ")
    assert lines.read_bytes() == before
    sent = [json.loads(ln) for ln in record.read_text().splitlines()]
    assert [(p["action"], p["notice"]) for p in sent] == [("raise", KEY)]


# ── proof-reach keeps the line ──────────────────────────────────────────────


def _proof_reach_entry() -> dict:
    doc = json.loads((ROOT / "config" / "proof-reach.json").read_text())
    entries = [e for e in doc["log_lines"] if e["tag"] == read_fallback_alert.TAG]
    assert len(entries) == 1
    return entries[0]


def test_proof_reach_declares_the_line_held_to_the_tag():
    entry = _proof_reach_entry()
    assert entry["printed_by"] == "scripts/read_fallback_alert.py"
    assert entry["constant"] == "TAG"


def test_proof_reach_names_the_settle_step_and_what_p_counts():
    when = _proof_reach_entry()["when"]
    assert "`Settle the read-fallback alert`" in when
    assert "from <p> process(es)" in when
    assert "check-ref" in when
    assert "Settle process" in when
    assert "sweep step's log" not in when
