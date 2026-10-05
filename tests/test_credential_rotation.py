"""RED-first: a run killed by our own Claude token renewal is re-run, not
recorded as a usage-limit death and parked for a person (DRE-5856).

On 2026-10-05 planner run 37328397948 (epic DRE-5852) ran 19 turns, spent
$2.41, and ended `API Error: 401 OAuth access token has been revoked` at
07:56:52 PT. Its own rate-limit events that minute said five-hour utilization
0.06 and seven-day 0.02 — nowhere near a wall. Two seconds earlier the console
had renewed the Claude chain and rewritten `CLAUDE_CODE_OAUTH_TOKEN` in every
repo, and every renewal revokes the access token before it. The medic then
wrote `🪦 limit-death: kind=claude stage=plan reset=unknown`, the re-check of
the revised plan parked the epic in Triage with `needs-human`, and the medic
declined its one retry because of that label. A person diagnosed it and moved
the epic back by hand.

Two readers were wrong, and the failed log shows both:

  * The medic's limit classifier matched "monthly spend limit" — in a COMMENT
    of plan.yml's own `Select model` script, which GitHub echoes into every
    planner run's log. The 401 itself never reaches the log: the action
    redacts the transcript.
  * Nothing between the 401 and the park asked whether the re-plan had been
    killed by a credential renewal rather than by anything in the plan.

What this file pins:

  1. **The reading.** `credential_rotation.in_execution` answers True for the
     execution record's two shapes — a result that died on a 401 saying
     "revoked", and an `api_retry` event with `error_status: 401` or
     `error: "authentication_failed"` on a run that then died — and False for
     a usage-limit death, an expired token, a run that recovered, and a run
     that did not die.
  2. **The line in the log.** `death_receipt.py emit`, every model-running
     workflow's receipt step, prints the rotation line when the record shows
     one, and `credential_rotation.in_log` reads it back only where a printed
     line sits — never off GitHub's echo of a script, never out of prose.
  3. **Never a limit death.** The incident's own failed log, with the line
     the receipt step now prints, is not a limit death: `limit_kind` answers
     None and `decide` writes no `🪦 limit-death:` marker.
  4. **Re-run once.** With no marker the medic's limit gate stays shut, its
     retry gate answers RETRY, and the retry job is the once-only attempt-1
     rerun (`gh run rerun --failed`), which starts a fresh job on the
     renewed token.
  5. **A real limit is still one.** A 429 usage-limit death prints no
     rotation line and still classifies `kind=claude`, with its reset.

The plan.yml half — the re-check that no longer parks a rotation — is walked
in tests/test_plan_critic_scenario.py, against the real step script.

Run: cd bureau-pipeline && python3 -m pytest tests/test_credential_rotation.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import credential_rotation  # noqa: E402
import dead_run  # noqa: E402
import death_receipt  # noqa: E402
import medic_retry  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
#: The incident run's execution record, as the action writes it: the rate-limit
#: events at 0.06 / 0.02, the CLI's retry on the 401, and the result.
ROTATION = FIXTURES / "credential-rotation-37328397948.json"
#: The incident run's own failed log, trimmed to the lines that decided it:
#: GitHub's echo of the `Select model` script (the "monthly spend limit"
#: comment the medic matched), the redacted result record, and the death
#: receipt's summary line.
ROTATION_LOG = FIXTURES / "credential-rotation-37328397948.log"
#: A real usage-limit death: 429, utilization at the limit, a stated reset.
USAGE_LIMIT = FIXTURES / "usage-limit-death.json"

RUN = "37328397948"
DIED_AT = "2026-10-05T14:56:59Z"
#: The prefix `gh run view --log-failed` puts on every line of this run.
GH_PREFIX = "call / bureau-card: DRE-5852\tUNKNOWN STEP\t2026-10-05T14:56:58.4319587Z "
MEDIC = ROOT / ".github" / "workflows" / "medic.yml"


def _messages(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def _result(**fields) -> dict:
    base = {"type": "result", "subtype": "success", "is_error": True,
            "num_turns": 19, "total_cost_usd": 2.41}
    base.update(fields)
    return base


# --------------------------------------------------------------------------
# 1. the reading, off the execution record
# --------------------------------------------------------------------------

def test_the_incident_run_reads_as_a_credential_rotation():
    assert credential_rotation.in_execution(_messages(ROTATION)) is True


def test_the_cli_says_so_with_exit_zero(capsys):
    assert credential_rotation.main(["check", str(ROTATION)]) == 0
    assert capsys.readouterr().out.strip() == "rotation=true"


def test_a_401_that_says_revoked_is_one_without_any_retry_event():
    record = _result(result="API Error: 401 OAuth access token has been revoked")
    assert credential_rotation.in_execution([record]) is True
    # ...and with the status only in the field, as the action records it.
    record = _result(api_error_status=401,
                     result="Failed to authenticate. OAuth access token has been revoked.")
    assert credential_rotation.in_execution([record]) is True


@pytest.mark.parametrize("event", [
    {"type": "system", "subtype": "api_retry", "error_status": 401},
    {"type": "system", "subtype": "api_retry", "error_status": "401"},
    {"type": "system", "subtype": "api_retry", "error": "authentication_failed"},
])
def test_an_api_retry_on_a_401_is_one_on_a_run_that_then_died(event):
    final = _result(result="API Error: 401 Please run /login")
    assert credential_rotation.in_execution([event, final]) is True


def test_a_single_result_object_is_read_too():
    """The action writes either the whole message list or the result alone."""
    record = _result(result="API Error: 401 OAuth access token has been revoked")
    assert credential_rotation.in_execution(record) is True


def test_a_usage_limit_death_is_not_one():
    assert credential_rotation.in_execution(_messages(USAGE_LIMIT)) is False
    assert credential_rotation.main(["check", str(USAGE_LIMIT)]) == 1


def test_a_usage_limit_wins_over_an_earlier_401_retry():
    """A wall is a wall: the run's last word is the reading, and a re-run
    into a usage limit is exactly what DRE-3171 stopped."""
    retry = {"type": "system", "subtype": "api_retry", "error_status": 401}
    final = _result(api_error_status=429, num_turns=1, total_cost_usd=0,
                    result="You've hit your limit · resets 8:30pm (UTC)")
    assert credential_rotation.in_execution([retry, final]) is False


def test_an_expired_token_is_not_a_rotation():
    """Expiry is the chain NOT renewing — the opposite fact. Only "revoked"
    is what a renewal does to the token before it."""
    record = _result(api_error_status=401,
                     result="Failed to authenticate. API Error: 401 OAuth access token has expired.")
    assert credential_rotation.in_execution([record]) is False


def test_an_expired_token_after_a_401_retry_is_not_a_rotation():
    """The real CLI retries the 401 before it gives up, so an expired-token
    death carries the retry event too — and it is still expiry."""
    retry = {"type": "system", "subtype": "api_retry", "error_status": 401,
             "error": "authentication_failed"}
    record = _result(api_error_status=401,
                     result="API Error: 401 OAuth access token has expired.")
    assert credential_rotation.in_execution([retry, record]) is False


def test_a_401_retry_then_a_death_on_something_else_is_not_one():
    """A brief 401 the run got past is not what killed it: the final result
    must itself be the 401."""
    retry = {"type": "system", "subtype": "api_retry", "error_status": 401,
             "error": "authentication_failed"}
    max_turns = _result(subtype="error_max_turns", result="max turns reached")
    assert credential_rotation.in_execution([retry, max_turns]) is False
    no_result = _result(result="")
    assert credential_rotation.in_execution([retry, no_result]) is False


def test_a_401_retry_the_run_recovered_from_is_not_one():
    retry = {"type": "system", "subtype": "api_retry", "error_status": 401}
    final = _result(is_error=False, result="done")
    assert credential_rotation.in_execution([retry, final]) is False


def test_words_alone_are_not_one():
    """An agent can write "revoked" about anything; the 401 must be there."""
    record = _result(result="the token was revoked, so I stopped")
    assert credential_rotation.in_execution([record]) is False
    # ...and an assistant turn quoting the API error is not the result.
    quoted = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "API Error: 401 OAuth access token has been revoked"}]}}
    assert credential_rotation.in_execution([quoted, _result(result="boom")]) is False


@pytest.mark.parametrize("path", ["", "/nonexistent/claude-execution-output.json"])
def test_no_record_is_no_rotation(path, capsys):
    assert credential_rotation.from_file(path) is False
    assert credential_rotation.main(["check", path]) == 1
    assert capsys.readouterr().out.strip().endswith("rotation=false")


# --------------------------------------------------------------------------
# 2. the line in the log
# --------------------------------------------------------------------------

def _receipt_stdout(tmp_path, execution: Path, capsys) -> str:
    """What the receipt step prints into the job log for this record."""
    exec_file = tmp_path / "claude-execution-output.json"
    exec_file.write_text(execution.read_text(encoding="utf-8"), encoding="utf-8")
    assert death_receipt.main([
        "emit", "--execution-file", str(exec_file),
        "--out", str(tmp_path / "death-receipt.json"),
        "--job-status", "failure", "--model-ran", "true",
    ]) == 0
    return capsys.readouterr().out


def _as_failed_log(stdout: str) -> str:
    """Printed lines as `gh run view --log-failed` hands them to the medic."""
    return "".join(f"{GH_PREFIX}{line}\n" for line in stdout.splitlines())


def test_the_receipt_step_prints_the_rotation_line(tmp_path, capsys):
    out = _receipt_stdout(tmp_path, ROTATION, capsys)
    assert credential_rotation.log_line() in out.splitlines()
    assert credential_rotation.in_log(_as_failed_log(out)) is True


def test_the_receipt_step_prints_no_rotation_line_for_a_usage_limit(tmp_path, capsys):
    out = _receipt_stdout(tmp_path, USAGE_LIMIT, capsys)
    assert credential_rotation.LOG_MARK not in out
    assert credential_rotation.in_log(_as_failed_log(out)) is False


def test_the_line_counts_bare_and_after_the_log_prefix():
    line = credential_rotation.log_line()
    assert credential_rotation.in_log(line) is True
    assert credential_rotation.in_log(f"{GH_PREFIX}{line}") is True


@pytest.mark.parametrize("text", [
    # GitHub's echo of a step's script carries the color code before the words.
    f"{GH_PREFIX}^[[36;1m# {credential_rotation.LOG_MARK} quoted in a comment^[[0m",
    f"{GH_PREFIX}\x1b[36;1mecho \"{credential_rotation.LOG_MARK} x\"\x1b[0m",
    # Prose that mentions the mark mid-line is not the line.
    f"{GH_PREFIX}the card says {credential_rotation.LOG_MARK} happened once",
    f"see `{credential_rotation.LOG_MARK}` in the receipt",
])
def test_the_line_never_counts_from_an_echo_or_from_prose(text):
    assert credential_rotation.in_log(text) is False


# --------------------------------------------------------------------------
# 3. never a limit death
# --------------------------------------------------------------------------

def _incident_log(tmp_path, capsys) -> str:
    """The incident's failed log as the medic now receives it: the real
    trimmed log, plus what the receipt step prints for the real record."""
    return (ROTATION_LOG.read_text(encoding="utf-8")
            + _as_failed_log(_receipt_stdout(tmp_path, ROTATION, capsys)))


def test_the_incident_log_was_read_as_a_limit_death_without_the_line():
    """The defect, pinned so the veto below is not vacuous: the echoed
    "monthly spend limit" comment alone made this log a Claude limit death."""
    assert dead_run.limit_kind(ROTATION_LOG.read_text(encoding="utf-8")) == "claude"


def test_the_incident_log_is_not_a_limit_death(tmp_path, capsys):
    assert dead_run.limit_kind(_incident_log(tmp_path, capsys)) is None


def test_decide_writes_no_limit_death_marker_for_it(tmp_path, capsys):
    log = tmp_path / "medic-log.txt"
    log.write_text(_incident_log(tmp_path, capsys), encoding="utf-8")
    rc = dead_run.main([
        "decide", "0", "--limit-log", str(log),
        "--workflow", "Agent Plan (reusable)", "--run-id", RUN, "--now", DIED_AT,
        "--failed-step", "Re-check the revised plan — review mode",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines()[0] != "limit"
    assert dead_run.LIMIT_TAG not in out


def test_a_linear_wall_in_the_same_log_is_still_linear(tmp_path, capsys):
    """The veto is about the Claude credential; Linear refusing a request is
    another vendor's wall and a renewed Claude token does not explain it."""
    text = (_incident_log(tmp_path, capsys)
            + f"{GH_PREFIX}LinearRateLimited: rate limited: 2500 requests/hour exhausted\n")
    assert dead_run.limit_kind(text) == "linear"


# --------------------------------------------------------------------------
# 4. re-run once, on the current token
# --------------------------------------------------------------------------

def test_the_medic_retries_it_once(tmp_path, capsys):
    """No marker, so the medic's limit output is false; nothing parked the
    card, so the retry gate answers RETRY; and the retry job is the one
    attempt-1 rerun of the failed jobs — a fresh job, which GitHub hands the
    secret as it stands now: the renewed token."""
    log = tmp_path / "medic-log.txt"
    log.write_text(_incident_log(tmp_path, capsys), encoding="utf-8")
    dead_run.main([
        "decide", "0", "--limit-log", str(log),
        "--workflow", "Agent Plan (reusable)", "--run-id", RUN, "--now", DIED_AT,
    ])
    assert capsys.readouterr().out.splitlines()[0] != "limit"
    assert medic_retry.decide(execution=json.loads(ROTATION.read_text())[-1]).retry is True

    retry = yaml.safe_load(MEDIC.read_text(encoding="utf-8"))["jobs"]["retry"]
    gate = " ".join(retry["if"].split())
    assert "github.event.workflow_run.run_attempt == 1" in gate
    assert "needs.classify.outputs.limit != 'true'" in gate
    (step,) = retry["steps"]
    assert "gh run rerun" in step["run"] and "--failed" in step["run"]


# --------------------------------------------------------------------------
# 5. a real usage limit is still one
# --------------------------------------------------------------------------

def test_a_real_usage_limit_death_is_still_a_limit_death(tmp_path, capsys):
    record = json.loads(USAGE_LIMIT.read_text())[-1]
    text = (_as_failed_log(_receipt_stdout(tmp_path, USAGE_LIMIT, capsys))
            + "".join(f"{GH_PREFIX}  {k}: {v}\n" for k, v in record.items()))
    assert dead_run.limit_kind(text) == "claude"
    reset = dead_run.limit_reset(text, "claude", datetime(2026, 10, 5, 15, 0, tzinfo=UTC))
    assert reset == datetime(2026, 10, 5, 20, 30, tzinfo=UTC)
