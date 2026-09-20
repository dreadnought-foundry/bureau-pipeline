"""The one death-cause receipt every model-running workflow writes (DRE-4340).

On 2026-09-19 the first fleet-wide count of what actually kills runs could not
attribute 69 of 1,310 failures — $97 of model work — to any cause at all,
because the workflow they died in wrote nothing down. The classifier existed;
nobody called it.

These tests hold `scripts/death_receipt.py` to the four promises the count
needs:

  * ONE SPELLING OF THE CAUSE. Every name the receipt can write is the
    constant `check_agent_result` already owns, or one declared here because
    nothing else names it (`setup`, `cancelled`, `unknown`). A second spelling
    is how two readers of the same corpse quietly stop agreeing.
  * NEVER BLANK, NEVER A GUESS. A run the module cannot classify is recorded
    `unknown` WITH the reason it could not be — not omitted, not guessed.
  * A DEATH BEFORE THE MODEL IS A SETUP DEATH. A run that died with no turn
    taken and nothing spent is not a model death, or the wasted-work figures
    are inflated by runs that wasted nothing (DRE-2931).
  * IT CANNOT MASK A FAILURE. `emit` returns 0 whatever happens to it, so the
    receipt step never changes the job's own conclusion.

Run: cd bureau-pipeline && python3 -m pytest tests/test_death_receipt.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_agent_result  # noqa: E402 — the death classes the receipt cites
import death_cause  # noqa: E402 — the wall the receipt records under it
import death_receipt  # noqa: E402
import execution_result  # noqa: E402 — the value cap the quote is held to
import usage_reading  # noqa: E402 — the run identity both receipts share

NOW = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)

RUN = {
    "id": 35413054438,
    "attempt": 1,
    "workflow": "Agent Task",
    "job": "execute",
    "event_name": "repository_dispatch",
    "ref": "agent/DRE-4340-death-cause-receipt",
    "sha": "0" * 40,
    "url": "https://github.com/dreadnought-foundry/portico/actions/runs/35413054438/attempts/1",
    "auth_mode": "subscription",
}

# The turn ceiling — the fleet's most expensive cause ($1,559 over 22 days) and
# the one this receipt makes countable per card. claude-code-action's own name
# for it, as check_agent_result reads it.
TURN_CAP = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 150,
    "total_cost_usd": 17.44,
    "duration_ms": 3_600_000,
    "result": "Reached maximum number of turns (150)",
}

# The subscription's usage wall, which arrives AS a 429 (death_cause reads the
# spend sentence before the status, so this is `capped`, not `throttled`).
USAGE_WALL = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 429,
    "result": "You've hit your limit · resets 8:30pm (UTC)",
}

# A clean run. Nothing died; the receipt still gets written, because a corpus
# that only holds deaths cannot say what share of runs die.
CLEAN = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 42,
    "total_cost_usd": 3.21,
    "duration_ms": 900_000,
}


def _receipt(execution, **kwargs):
    kwargs.setdefault("run", RUN)
    kwargs.setdefault("repo", "dreadnought-foundry/portico")
    kwargs.setdefault("read_at", NOW)
    return death_receipt.receipt(execution, **kwargs)


# --------------------------------------------------------------------------
# One spelling of the cause
# --------------------------------------------------------------------------

class TestVocabulary:
    def test_the_death_classes_are_check_agent_results_own_constants(self):
        # Not equal strings that happen to match — the same objects, read from
        # the module that owns them. A second spelling is the defect.
        assert death_receipt.CAUSE_NONE is check_agent_result.DEATH_NONE
        assert death_receipt.CAUSE_TURN_EXHAUSTION is check_agent_result.DEATH_TURN_EXHAUSTION
        assert death_receipt.CAUSE_API is check_agent_result.DEATH_API
        assert death_receipt.CAUSE_CREDENTIAL_EXPIRY is check_agent_result.DEATH_CREDENTIAL_EXPIRY

    def test_the_three_it_declares_itself_are_the_three_nothing_else_names(self):
        assert death_receipt.CAUSE_SETUP == "setup"
        assert death_receipt.CAUSE_CANCELLED == "cancelled"
        assert death_receipt.CAUSE_UNKNOWN == "unknown"

    def test_every_cause_it_can_write_is_in_the_declared_set(self):
        assert death_receipt.CAUSE_SETUP in death_receipt.CAUSES
        assert death_receipt.CAUSE_UNKNOWN in death_receipt.CAUSES
        for name in (check_agent_result.DEATH_NONE,
                     check_agent_result.DEATH_TURN_EXHAUSTION,
                     check_agent_result.DEATH_API,
                     check_agent_result.DEATH_CREDENTIAL_EXPIRY):
            assert name in death_receipt.CAUSES

    def test_the_wall_names_are_death_causes_own(self):
        doc = _receipt(USAGE_WALL, job_status="failure")
        assert doc["wall"]["cause"] == death_cause.CAPPED


# --------------------------------------------------------------------------
# What each shape is recorded as
# --------------------------------------------------------------------------

class TestClassification:
    def test_a_turn_cap_death_is_recorded_as_one_with_its_spend(self):
        doc = _receipt(TURN_CAP, job_status="failure", step_outcome="failure")
        assert doc["died"] is True
        assert doc["cause"] == death_receipt.CAUSE_TURN_EXHAUSTION
        assert doc["phase"] == death_receipt.PHASE_MODEL
        assert doc["model_started"] is True
        # The whole point of counting it per card: the money is on the receipt.
        assert doc["spend"]["total_cost_usd"] == 17.44
        assert doc["spend"]["num_turns"] == 150
        # A turn-cap death is NOT an account wall, whatever its closing message
        # says — death_cause's veto, not re-derived here.
        assert doc["wall"]["cause"] is None

    def test_a_usage_wall_is_an_api_death_carrying_the_wall_and_the_quote(self):
        doc = _receipt(USAGE_WALL, job_status="failure", step_outcome="failure")
        assert doc["cause"] == death_receipt.CAUSE_API
        assert doc["wall"]["cause"] == death_cause.CAPPED
        assert "hit your limit" in doc["quote"]
        # capped states its own reset; throttled would say "clears in minutes"
        # about a window that resets tonight (death_cause's ordering note).
        assert doc["wall"]["resets_at"]
        assert len(doc["quote"]) <= execution_result._VALUE_CAP

    def test_a_clean_run_writes_a_receipt_that_says_nothing_died(self):
        doc = _receipt(CLEAN, job_status="success", step_outcome="success")
        assert doc["died"] is False
        assert doc["cause"] == death_receipt.CAUSE_NONE
        assert doc["cause_reason"]
        assert doc["spend"]["num_turns"] == 42

    def test_a_cancelled_run_is_not_a_death(self):
        # DRE-2074: the job timeout and an external cancel both land here, and
        # counting either as a death is how three live builds were killed.
        doc = _receipt(None, job_status="cancelled", step_outcome="cancelled")
        assert doc["died"] is False
        assert doc["cause"] == death_receipt.CAUSE_CANCELLED


class TestSetupDeath:
    """A run that dies before the model starts wasted no model work."""

    def test_a_step_github_reports_as_skipped_is_a_setup_death(self):
        doc = _receipt(None, job_status="failure", step_outcome="skipped")
        assert doc["died"] is True
        assert doc["cause"] == death_receipt.CAUSE_SETUP
        assert doc["phase"] == death_receipt.PHASE_SETUP
        assert doc["model_started"] is False
        assert "model" in doc["cause_reason"]

    def test_a_run_with_no_execution_record_at_all_is_a_setup_death(self):
        # The shape a pre-agent failure leaves behind: a Linear write refused
        # at `Card → In Progress`, a context assembly that blew up.
        doc = _receipt(None, job_status="failure", step_outcome="")
        assert doc["cause"] == death_receipt.CAUSE_SETUP
        assert doc["phase"] == death_receipt.PHASE_SETUP

    def test_a_model_step_that_ran_is_a_model_death_even_with_no_record(self):
        # The groomer's judged read and the planner's classifier call a model
        # through planning_classify and write no execution record at all.
        # Without GitHub's own outcome for the step, every one of their deaths
        # would be filed as a setup death that wasted nothing.
        doc = _receipt(None, job_status="failure", step_outcome="failure")
        assert doc["model_started"] is True
        assert doc["phase"] == death_receipt.PHASE_MODEL
        assert doc["cause"] == death_receipt.CAUSE_UNKNOWN

    def test_skipped_still_beats_a_step_outcome_that_ran(self):
        # `skipped` is GitHub saying the step never ran and nothing overrides
        # it — the order check_agent_result.agent_started already sets.
        doc = _receipt(None, job_status="failure", step_outcome="skipped")
        assert doc["model_started"] is False

    def test_a_setup_death_never_reports_model_spend(self):
        doc = _receipt(None, job_status="failure", step_outcome="skipped")
        assert doc["spend"] == {}

    def test_one_turn_at_400ms_is_a_MODEL_death_not_a_setup_one(self):
        # The boundary check_agent_result.agent_started draws: one turn means
        # the model WAS called and refused. Read the other way, every transport
        # death in the fleet would be filed as "wasted nothing".
        outage = {"type": "result", "subtype": "error_during_execution",
                  "is_error": True, "num_turns": 1, "total_cost_usd": 0,
                  "duration_ms": 400, "api_error_status": 401,
                  "result": "authentication_error: invalid_grant"}
        doc = _receipt(outage, job_status="failure", step_outcome="failure")
        assert doc["phase"] == death_receipt.PHASE_MODEL
        assert doc["model_started"] is True
        assert doc["cause"] == death_receipt.CAUSE_API
        assert doc["wall"]["cause"] == death_cause.REVOKED


class TestUnknown:
    """Never a guess and never a blank — `unknown` WITH the reason."""

    def test_a_failure_after_a_clean_model_run_is_unknown_with_a_reason(self):
        # The 69 unattributed failures' shape: the model ran and finished, and
        # something after it failed the job.
        doc = _receipt(CLEAN, job_status="failure", step_outcome="success")
        assert doc["died"] is True
        assert doc["cause"] == death_receipt.CAUSE_UNKNOWN
        assert doc["cause_reason"].strip()
        assert doc["cause_reason"] != death_receipt.CAUSE_UNKNOWN

    def test_the_reason_names_which_evidence_was_missing(self):
        no_record = _receipt(None, job_status="failure", step_outcome="failure")
        clean_record = _receipt(CLEAN, job_status="failure", step_outcome="success")
        # Two different absences, two different sentences — a reader must be
        # able to tell "nothing was written down" from "the record says it was
        # fine" without opening the run.
        assert no_record["cause_reason"] != clean_record["cause_reason"]

    def test_every_receipt_carries_a_cause_and_a_reason(self):
        for kwargs in (
            {"job_status": "success", "step_outcome": "success"},
            {"job_status": "failure", "step_outcome": "failure"},
            {"job_status": "failure", "step_outcome": "skipped"},
            {"job_status": "cancelled", "step_outcome": "cancelled"},
            {"job_status": "", "step_outcome": ""},
        ):
            for execution in (None, CLEAN, TURN_CAP, USAGE_WALL, {}, "not a dict"):
                doc = _receipt(execution, **kwargs)
                assert doc["cause"] in death_receipt.CAUSES, (execution, kwargs)
                assert doc["cause_reason"].strip(), (execution, kwargs)


# --------------------------------------------------------------------------
# The document's shape — the same names its sibling reading already uses
# --------------------------------------------------------------------------

class TestShape:
    def test_it_is_keyed_the_way_the_usage_reading_is(self):
        doc = _receipt(TURN_CAP, job_status="failure")
        assert doc["schema"] == death_receipt.SCHEMA
        assert doc["delivery_id"] == usage_reading.delivery_id(
            RUN, doc["read_at_epoch_ms"])
        assert doc["repo"] == "dreadnought-foundry/portico"
        assert doc["run"] == RUN
        assert doc["record"]["fact_key_parts"] == [
            "run.id", "run.attempt", "read_at_epoch_ms"]

    def test_the_card_is_read_off_the_branch_when_it_is_not_passed(self):
        doc = _receipt(TURN_CAP, job_status="failure")
        assert doc["card"] == "DRE-4340"

    def test_a_naive_read_at_is_refused(self):
        with pytest.raises(ValueError):
            _receipt(TURN_CAP, job_status="failure",
                     read_at=datetime(2026, 9, 19, 18, 0))

    def test_the_summary_line_names_the_cause_and_the_reason(self):
        line = death_receipt.summary_line(
            _receipt(TURN_CAP, job_status="failure"))
        assert death_receipt.CAUSE_TURN_EXHAUSTION in line
        assert line.strip()


# --------------------------------------------------------------------------
# It cannot mask a failure
# --------------------------------------------------------------------------

class TestEmitNeverFails:
    def _emit(self, tmp_path, execution, argv_extra=()):
        exec_file = tmp_path / "claude-execution-output.json"
        if execution is not None:
            exec_file.write_text(json.dumps(execution))
        out = tmp_path / "death-receipt.json"
        rc = death_receipt.main([
            "emit", "--execution-file", str(exec_file), "--out", str(out),
            *argv_extra,
        ])
        return rc, out

    def test_emit_returns_zero_on_a_death(self, tmp_path):
        rc, out = self._emit(tmp_path, TURN_CAP, ("--job-status", "failure"))
        assert rc == 0
        doc = json.loads(out.read_text())
        assert doc["cause"] == death_receipt.CAUSE_TURN_EXHAUSTION

    def test_emit_returns_zero_when_the_execution_file_is_missing(self, tmp_path):
        rc, out = self._emit(tmp_path, None, ("--job-status", "failure"))
        assert rc == 0
        assert json.loads(out.read_text())["cause"] == death_receipt.CAUSE_SETUP

    def test_emit_returns_zero_when_the_execution_file_is_corrupt(self, tmp_path):
        exec_file = tmp_path / "e.json"
        exec_file.write_text("{ not json")
        out = tmp_path / "r.json"
        assert death_receipt.main([
            "emit", "--execution-file", str(exec_file), "--out", str(out),
            "--job-status", "failure",
        ]) == 0
        assert json.loads(out.read_text())["cause"] in death_receipt.CAUSES

    def test_emit_returns_zero_when_it_cannot_write_the_file_at_all(self, tmp_path):
        # The receipt step must never be the reason a job goes red — not even
        # when the emitter itself is what is broken.
        exec_file = tmp_path / "e.json"
        exec_file.write_text(json.dumps(TURN_CAP))
        assert death_receipt.main([
            "emit", "--execution-file", str(exec_file),
            "--out", str(tmp_path / "nope") + "\x00bad",
            "--job-status", "failure",
        ]) == 0

    def test_the_cli_exits_zero_as_a_subprocess(self, tmp_path):
        # As the workflow actually runs it — `set -euo pipefail` is in force in
        # these run blocks, so a non-zero exit fails the step.
        exec_file = tmp_path / "e.json"
        exec_file.write_text(json.dumps(USAGE_WALL))
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "death_receipt.py"), "emit",
             "--execution-file", str(exec_file),
             "--out", str(tmp_path / "r.json"),
             "--job-status", "failure"],
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr
        assert death_receipt.CAUSE_API in done.stdout

    def test_it_writes_the_step_summary_and_the_step_output(self, tmp_path):
        exec_file = tmp_path / "e.json"
        exec_file.write_text(json.dumps(TURN_CAP))
        out = tmp_path / "r.json"
        summary = tmp_path / "summary.md"
        gh_out = tmp_path / "gh-out.txt"
        death_receipt.main([
            "emit", "--execution-file", str(exec_file), "--out", str(out),
            "--job-status", "failure",
            "--step-summary", str(summary), "--github-output", str(gh_out),
        ])
        assert death_receipt.CAUSE_TURN_EXHAUSTION in summary.read_text()
        written = gh_out.read_text()
        assert f"path={out}" in written
        assert f"cause={death_receipt.CAUSE_TURN_EXHAUSTION}" in written


# --------------------------------------------------------------------------
# It reads the whitelist, and only the whitelist
# --------------------------------------------------------------------------

def test_the_transcript_never_reaches_the_receipt(tmp_path):
    """Several repos are public and the artifact is world-readable for its 90
    days. `quote` comes through death_cause, whose only way into the record is
    execution_result's audited whitelist — a sentinel planted outside it must
    not appear anywhere in the written document."""
    sentinel = "SENTINEL-DO-NOT-PUBLISH"
    execution = dict(USAGE_WALL, transcript=sentinel, env={"TOKEN": sentinel},
                     error=sentinel)
    exec_file = tmp_path / "e.json"
    exec_file.write_text(json.dumps(execution))
    out = tmp_path / "r.json"
    summary = tmp_path / "s.md"
    death_receipt.main([
        "emit", "--execution-file", str(exec_file), "--out", str(out),
        "--job-status", "failure", "--step-summary", str(summary),
    ])
    assert sentinel not in out.read_text()
    assert sentinel not in summary.read_text()
