"""RED-first: a limit death whose marker Linear refuses is still brought back
(DRE-5837).

Epic DRE-3624's planner run died on the Claude usage limit on 2026-10-02. The
medic classified it, composed the `🪦 limit-death:` marker, and Linear refused
the write. Nothing else recorded the death, so `limit_recovery` — which reads
only that marker — had nothing to bring back. The medic printed a warning
asking for a by-hand re-entry, and the card sat for two days until a person
diagnosed it.

What must hold:

  1. The marker write is RETRIED before it is given up on.
  2. A write that still fails leaves the death on the run's OWN record — a
     `limit-death-record` artifact the sweep reads over GitHub, with no Linear
     in the way — and the medic says so.
  3. The sweep finds that record, writes the marker once Linear answers, and
     limit recovery re-enters the stage after the limit resets. Pinned end to
     end below with a fake Linear that refuses the medic's marker write.
  4. The medic's line names the card and says whether recovery is automatic
     or needs a person, and never asks for a by-hand re-entry when it is
     automatic.

Run: cd bureau-pipeline && python3 -m pytest tests/test_limit_death_without_marker.py -v
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import limit_death_record  # noqa: E402
import limit_recovery  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402

MEDIC = ROOT / ".github" / "workflows" / "medic.yml"

CARD = "DRE-3624"
RUN = "36600000001"
DIED_AT = "2026-10-02T20:31:00Z"
# Already past on the sweep's real clock: the end-to-end test below runs
# `recover_limit_deaths()` as the sweep does, at `datetime.now(UTC)`.
RESET = datetime(2026, 10, 3, 2, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _pin_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/agent-bureau")
    monkeypatch.setattr(limit_death_record, "MARKER_WAITS", (0, 0))
    reconcile._write_failures.clear()
    reconcile.reset_sweep_cards()


def marker(kind="claude", stage="plan", reset=RESET, run=RUN, account=None) -> str:
    """The marker exactly as the medic composes it — never hand-typed."""
    return dead_run.LimitDeath(kind=kind, stage=stage, reset=reset,
                               run_id=run, account=account).marker()


def card(ident=CARD, lane="Planning", nodes=(), labels=("repo:agent-bureau",)):
    """`nodes` oldest→newest as the card reads; stored newest-first, the way
    Linear answers a comment window (DRE-3250)."""
    return {
        "id": f"id-{ident}",
        "identifier": ident,
        "title": f"[EPIC] {ident}",
        "description": "work",
        "updatedAt": "2026-10-02T10:00:00Z",
        "state": {"name": lane},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [dict(n) for n in reversed(list(nodes))]},
    }


def node(body, at):
    return {"body": body, "createdAt": at}


def record(**over):
    rec = limit_death_record.make_record(CARD, RUN, DIED_AT, marker())
    rec.update(over)
    return rec


class Refusing:
    """A fake Linear comment writer that refuses the first `refusals` writes."""

    def __init__(self, refusals=99, answer=None):
        self.calls: list[tuple[str, str]] = []
        self.refusals = refusals
        self.answer = answer

    def __call__(self, ident, body):
        self.calls.append((ident, body))
        if len(self.calls) <= self.refusals:
            raise linear_ops.LinearError("Linear API returned 400: refused")
        return self.answer


# ── 1. the write is retried ──────────────────────────────────────────────────
def test_a_refused_marker_write_is_retried_and_a_later_attempt_lands():
    comment = Refusing(refusals=2)
    assert limit_death_record.post_marker(CARD, marker(), comment=comment, log=lambda *_: None)
    assert len(comment.calls) == 3


def test_a_write_refused_on_every_attempt_is_given_up_on_after_the_cap():
    comment = Refusing()
    assert not limit_death_record.post_marker(CARD, marker(), comment=comment, log=lambda *_: None)
    assert len(comment.calls) == limit_death_record.MARKER_ATTEMPTS >= 2


def test_a_full_card_is_a_refused_write_too():
    """`cmd_comment` answers the comment-cap condition rather than raising
    (DRE-3343) — the marker did not land, so it is not reported as written."""
    comment = Refusing(refusals=0, answer=linear_ops.COMMENT_CAP_CONDITION)
    assert not limit_death_record.post_marker(CARD, marker(), comment=comment, log=lambda *_: None)


# ── 2. the medic keeps the death on its own run ──────────────────────────────
def test_post_writes_the_record_when_linear_refuses(monkeypatch, tmp_path, capsys):
    comment = Refusing()
    monkeypatch.setattr(linear_ops, "cmd_comment", comment)
    path = tmp_path / "rec" / limit_death_record.RECORD_FILE
    rc = limit_death_record.main(["post", CARD, marker(), "--run-id", RUN,
                                  "--died-at", DIED_AT, "--record", str(path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "marker=refused" in out.out.splitlines()
    saved = json.loads(path.read_text())
    assert saved["card"] == CARD and saved["run"] == RUN and saved["died_at"] == DIED_AT
    assert dead_run.parse_limit_marker(saved["marker"])["run"] == RUN
    assert len(comment.calls) == limit_death_record.MARKER_ATTEMPTS


def test_post_writes_no_record_when_the_marker_lands(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(linear_ops, "cmd_comment", Refusing(refusals=0))
    path = tmp_path / "rec" / limit_death_record.RECORD_FILE
    limit_death_record.main(["post", CARD, marker(), "--run-id", RUN,
                             "--died-at", DIED_AT, "--record", str(path)])
    out = capsys.readouterr()
    assert "marker=written" in out.out.splitlines()
    assert not path.exists()
    assert CARD in out.err and "automatic" in out.err


def test_the_record_round_trips_and_a_forged_one_is_refused():
    rec = record()
    assert limit_death_record.read_record(json.dumps(rec)) == rec
    assert limit_death_record.read_record("not json") is None
    assert limit_death_record.read_record(json.dumps(record(marker="hello"))) is None
    assert limit_death_record.read_record(json.dumps(record(card="DRE-1; rm -rf"))) is None
    assert limit_death_record.read_record(json.dumps(record(run="999"))) is None, (
        "the record's run must be the run its marker names")


# ── 4. the medic's line ──────────────────────────────────────────────────────
@pytest.mark.parametrize("outcome", ["written", "recorded"])
def test_an_automatic_recovery_is_said_to_be_automatic(outcome):
    line = limit_death_record.medic_line(CARD, outcome, dead_run.parse_limit_marker(marker()))
    assert CARD in line
    assert "automatic" in line
    assert "by hand" not in line and "needs a person" not in line


def test_a_recorded_death_says_where_the_record_is():
    line = limit_death_record.medic_line(CARD, "recorded", dead_run.parse_limit_marker(marker()))
    assert line.startswith("::warning")
    assert limit_death_record.RECORD_NAME in line


def test_a_lost_record_needs_a_person_and_says_so():
    line = limit_death_record.medic_line(CARD, "lost", dead_run.parse_limit_marker(marker()))
    assert line.startswith("::warning") and CARD in line
    assert "needs a person" in line and "automatic" not in line


def test_a_marker_nothing_can_bring_back_needs_a_person_even_when_written():
    """A Claude limit with no reset time and no account: the sweep hands it to a
    person (`limit_recovery.handoff_reason`), so the medic must not call it
    automatic."""
    m = dead_run.parse_limit_marker(marker(reset=None))
    line = limit_death_record.medic_line(CARD, "written", m)
    assert "needs a person" in line and "automatic" not in line


def test_a_held_card_needs_a_person():
    m = dead_run.parse_limit_marker(marker(stage="build"))
    line = limit_death_record.medic_line(CARD, "written", m, labels=["needs-human"])
    assert "needs a person" in line and "needs-human" in line


def test_say_reads_the_record_and_names_the_card(tmp_path, capsys):
    path = tmp_path / limit_death_record.RECORD_FILE
    path.write_text(json.dumps(record()))
    limit_death_record.main(["say", CARD, "--outcome", "recorded", "--record", str(path)])
    out = capsys.readouterr().out
    assert CARD in out and "automatic" in out and "by hand" not in out


def test_say_with_no_record_needs_a_person(tmp_path, capsys):
    limit_death_record.main(["say", CARD, "--outcome", "lost",
                             "--record", str(tmp_path / "missing.json")])
    out = capsys.readouterr().out
    assert CARD in out and "needs a person" in out


# ── 3. the sweep finds the record ────────────────────────────────────────────
class Sweep:
    def __init__(self, refuse=False):
        self.comments: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.lops = MagicMock()
        self.refuse = refuse

        def comment(ident, body):
            if self.refuse:
                raise linear_ops.LinearError("refused")
            self.comments.append((ident, body))

        self.lops.cmd_comment.side_effect = comment

    def backfill(self, cards, records):
        return limit_death_record.backfill(self.lops, cards, records,
                                           delete=self.deleted.append)


def test_a_card_with_no_marker_gets_the_marker_from_the_record():
    s = Sweep()
    board = [card(nodes=[node("🧠 model-attempt: planner", "2026-10-02T20:00:00Z")])]
    lines = s.backfill(board, [record(artifact="77")])
    assert len(s.comments) == 1
    ident, body = s.comments[0]
    assert ident == CARD
    assert dead_run.parse_limit_marker(body)["run"] == RUN
    assert "refused" in body, "the late marker says why it is late"
    assert s.deleted == ["77"]
    assert limit_recovery.waiting(limit_recovery._bodies(board[0]))["run"] == RUN, (
        "the marker is on the card THIS pass, so recovery need not wait a sweep")
    assert any(CARD in line for line in lines)


def test_a_card_that_already_carries_the_runs_marker_is_left_alone():
    s = Sweep()
    board = [card(nodes=[node(marker(), "2026-10-02T21:00:00Z")])]
    s.backfill(board, [record(artifact="77")])
    assert s.comments == []
    assert s.deleted == ["77"]


@pytest.mark.parametrize("receipt", [
    "⏳ 1/5 plan — continuing", "🧠 model-attempt: planner",
    f"{limit_recovery.RECOVERY_MARK} re-entered plan", f"{limit_recovery.HANDOFF_MARK} x",
])
def test_a_stage_that_ran_again_since_the_death_is_not_re_marked(receipt):
    s = Sweep()
    board = [card(nodes=[node(receipt, "2026-10-03T09:00:00Z")])]
    s.backfill(board, [record(artifact="77")])
    assert s.comments == []
    assert s.deleted == ["77"]


def test_a_sweep_nudge_after_the_death_does_not_cancel_the_record():
    """The sweep's own notes are not evidence the stage re-ran: DRE-3624 sat
    for two days, and a nudge in that time must not hide the death again."""
    s = Sweep()
    board = [card(nodes=[node("🧹 stalled in Planning", "2026-10-03T09:00:00Z")])]
    s.backfill(board, [record(artifact="77")])
    assert len(s.comments) == 1


def test_a_receipt_from_before_the_death_does_not_count():
    s = Sweep()
    board = [card(nodes=[node("⏳ 1/5 plan", "2026-10-02T20:00:00Z")])]
    s.backfill(board, [record(artifact="77")])
    assert len(s.comments) == 1


def test_a_card_off_the_board_keeps_its_record():
    s = Sweep()
    s.backfill([card(ident="DRE-1")], [record(artifact="77")])
    assert s.comments == [] and s.deleted == []


def test_a_refused_backfill_is_an_error_and_the_record_is_kept():
    s = Sweep(refuse=True)
    board = [card()]
    lines = s.backfill(board, [record(artifact="77")])
    assert s.deleted == []
    assert any(line.startswith("ERROR:") and CARD in line for line in lines)
    assert limit_recovery.waiting(limit_recovery._bodies(board[0])) is None


def test_a_failed_delete_never_costs_the_marker():
    s = Sweep()

    def delete(_):
        raise RuntimeError("403")

    lines = limit_death_record.backfill(s.lops, [card()], [record(artifact="77")], delete=delete)
    assert len(s.comments) == 1
    assert not any(line.startswith("ERROR:") for line in lines)


def test_records_come_off_the_listing_and_expired_ones_are_skipped():
    listing = json.dumps({"artifacts": [
        {"id": 77, "name": limit_death_record.RECORD_NAME, "expired": False,
         "workflow_run": {"id": 555}},
        {"id": 78, "name": limit_death_record.RECORD_NAME, "expired": True,
         "workflow_run": {"id": 556}},
        {"id": 79, "name": limit_death_record.RECORD_NAME, "expired": False,
         "workflow_run": {"id": 557}},
    ]})
    fetched = []

    def download(run_id):
        fetched.append(run_id)
        return json.dumps(record()) if run_id == "555" else None

    records = limit_death_record.records_from_listing(listing, download)
    assert fetched == ["555", "557"]
    assert [r["artifact"] for r in records] == ["77"]


# ── the medic workflow ───────────────────────────────────────────────────────
def _classify_steps():
    return yaml.safe_load(MEDIC.read_text())["jobs"]["classify"]["steps"]


def test_the_medic_keeps_a_refused_marker_on_its_own_run():
    steps = _classify_steps()
    ids = [s.get("id") for s in steps]
    limit_at = ids.index("limit")
    upload = steps[ids.index("record")]
    assert ids.index("record") > limit_at
    assert "upload-artifact" in upload["uses"]
    assert upload["if"] == "steps.limit.outputs.marker == 'refused'"
    assert upload["with"]["name"] == limit_death_record.RECORD_NAME
    assert upload["with"]["path"].endswith(limit_death_record.RECORD_FILE)
    assert upload.get("continue-on-error") is True, (
        "every medic job needs classify; an upload must never take it down")
    limit = steps[limit_at]["run"]
    assert "limit_death_record.py post" in limit
    assert "by hand" not in limit


def test_the_medic_says_who_brings_the_card_back_after_the_upload():
    steps = _classify_steps()
    ids = [s.get("id") for s in steps]
    say = next(s for s in steps[ids.index("record") + 1:]
               if "limit_death_record.py say" in (s.get("run") or ""))
    assert say["if"] == "steps.limit.outputs.marker == 'refused'"
    assert "steps.record.outcome" in json.dumps(say.get("env") or {})


# ── 3, end to end: a fake Linear refuses the medic, the sweep brings it back ─
def test_a_refused_marker_is_brought_back_by_the_sweep_after_the_reset(monkeypatch, tmp_path):
    # The medic: Linear refuses every attempt, so the death goes to the record.
    medic_linear = Refusing()
    monkeypatch.setattr(linear_ops, "cmd_comment", medic_linear)
    saved = tmp_path / "medic" / limit_death_record.RECORD_FILE
    assert limit_death_record.main(["post", CARD, marker(), "--run-id", RUN,
                                    "--died-at", DIED_AT, "--record", str(saved)]) == 0
    assert len(medic_linear.calls) == limit_death_record.MARKER_ATTEMPTS
    uploaded = saved.read_text()

    # The sweep: the card carries no marker; GitHub holds the run's record.
    stuck = card(lane="Planning", nodes=[node("🧠 model-attempt: planner", "2026-10-02T20:00:00Z")])
    deleted = []

    def actions_read(args):
        if args[0] == "api" and "actions/artifacts?name=" in args[1]:
            return json.dumps({"artifacts": [{"id": 77, "name": limit_death_record.RECORD_NAME,
                                              "expired": False, "workflow_run": {"id": 999}}]}), None
        if args[:3] == ("run", "download", "999"):
            target = Path(args[args.index("-D") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / limit_death_record.RECORD_FILE).write_text(uploaded)
            return "", None
        raise AssertionError(f"unexpected Actions read {args}")

    def gh_dispatch(*args):
        if args[:3] == ("api", "-X", "DELETE"):
            deleted.append(args[3])
            return None
        raise AssertionError(f"unexpected write {args}")

    monkeypatch.setenv("GH_DISPATCH_TOKEN", "t")
    sweep_comments = []
    with patch.object(reconcile, "active_cards", return_value=[stuck]), \
         patch.object(reconcile, "_actions_read", side_effect=actions_read), \
         patch.object(reconcile, "gh_dispatch", side_effect=gh_dispatch), \
         patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=lambda i, b: sweep_comments.append((i, b))):
        reconcile.recover_limit_deaths()

    assert [c.args[:2] for c in cmd_state.call_args_list] == [
        (CARD, "Intake"), (CARD, "Planning")], "re-entered through Planning's front door"
    assert dead_run.parse_limit_marker(sweep_comments[0][1])["run"] == RUN
    assert sweep_comments[-1][1].startswith(limit_recovery.RECOVERY_MARK)
    assert deleted == ["repos/dreadnought-foundry/agent-bureau/actions/artifacts/77"]
    assert reconcile._write_failures == []


def test_without_the_actions_token_the_sweep_reads_no_records(monkeypatch):
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    with patch.object(reconcile, "active_cards", return_value=[card()]), \
         patch.object(reconcile, "_actions_read") as read, \
         patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.recover_limit_deaths()
    read.assert_not_called()


def test_an_unreadable_listing_degrades_and_recovery_still_runs(monkeypatch, capsys):
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "t")
    ran = []
    with patch.object(reconcile, "active_cards", return_value=[card()]), \
         patch.object(reconcile, "_actions_read", return_value=(None, "rc=1: 403")), \
         patch.object(reconcile.limit_recovery, "recover",
                      side_effect=lambda *a, **k: ran.append(1) or []):
        reconcile.recover_limit_deaths()
    assert ran == [1]
    assert "DEGRADED" in capsys.readouterr().out
    assert reconcile._write_failures == []
