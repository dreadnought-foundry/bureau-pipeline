"""RED-first tests for DRE-4338 — every Claude run keeps its own usage reading.

Every headless Claude run emits a `rate_limit_event` stream message carrying
the subscription windows it is drawing on, and `anthropics/claude-code-action`
writes that message — with every other message of the run — into
`$RUNNER_TEMP/claude-execution-output.json`, which is destroyed with the
runner. `scripts/usage_reading.py` reads ONLY that one message type out of the
file, writes a small JSON document shaped as the Record's `body_raw`, and the
workflows upload it as a run artifact.

WHAT THIS FILE PINS, one assertion per criterion on the card:

  * **the extraction** — the windows come out exactly as reported (fractions,
    never scaled), both the `unifiedWindows` shape and the flat single-window
    shape the pinned SDK declares, the LAST event is the reading and every
    event is kept;
  * **absence is a reading, zero is not** — no event, no file and a corrupt
    file each say `reported: false` with a named reason, never a utilization
    of 0, and the CLI exits 0 every time;
  * **the security shape** — the execution file holds every tool result of the
    run and several repos are public. Only entries whose `type` is
    `rate_limit_event` are read; a sentinel planted in every other message
    never reaches the written file, stdout or the step summary. This is the
    same discipline tests/test_execution_failure_detail.py holds the gates to;
  * **the Record shape** — `delivery_id` fits the contract's segment rule and
    tells attempts and read times apart, the fact-key parts are positive ints
    (what `record_contract.fact_key` accepts), and nothing in the body is
    named `received_at` (that is the relay's clock, not a run's);
  * **the wiring** — each of the three per-card workflows really carries the
    emit step after its last Claude step and the pinned upload beside it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "usage_reading.py"
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(SCRIPTS))

import usage_reading as ur  # noqa: E402

# Anything from the transcript that reaches the artifact, stdout or the job
# summary is a leak. Real logs carry file contents and command output here;
# the sentinel stands in for all of it.
SENTINEL = "SECRET-SENTINEL-DO-NOT-KEEP"

# The message as Claude Code 2.1.278 emitted it on 2026-09-19 (the shape the
# card's finding was verified on): three windows under `unifiedWindows`.
UNIFIED_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "overageStatus": "rejected",
        "overageDisabledReason": "overage_not_provisioned",
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.42, "resetsAt": 1789866000},
            "seven_day": {"utilization": 0.63, "resetsAt": 1790175600},
            "seven_day_overage_included": {"utilization": 0.0, "resetsAt": 1790175600},
        },
    },
    "uuid": "0d1e5a2c-7b3f-4c8e-9a1d-2f3b4c5d6e7f",
    "session_id": "sess-abc",
}

# The shape `@anthropic-ai/claude-agent-sdk` 0.3.263 DECLARES for
# `SDKRateLimitInfo` — the SDK the pinned action bundles: one window, flat.
FLAT_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed_warning",
        "rateLimitType": "seven_day",
        "utilization": 0.91,
        "resetsAt": 1790175600,
    },
    "uuid": "1e2f3a4b-5c6d-4e7f-8a9b-0c1d2e3f4a5b",
    "session_id": "sess-abc",
}

RUN_ENV = {
    "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
    "GITHUB_RUN_ID": "35413054438",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW": "Agent Task",
    "GITHUB_JOB": "execute",
    "GITHUB_EVENT_NAME": "repository_dispatch",
    "GITHUB_REF_NAME": "main",
    "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
    "GITHUB_SERVER_URL": "https://github.com",
    "CLAUDE_AUTH_MODE": "subscription",
}

READ_AT = datetime(2026, 9, 19, 23, 10, 4, 123000, tzinfo=timezone.utc)

# The Record contract's own rule for an S3 key segment
# (agent-bureau `console/backend/record_contract.py:_SEGMENT`), restated here
# because this repo cannot import that one. If it changes there, this changes.
SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _transcript(*events, planted=SENTINEL):
    """A realistic claude-execution-output.json: every non-rate-limit message
    is loaded with the sentinel, including a result record and a tool result
    that carry a `rate_limit_info` key of their own — none may be read."""
    return [
        {"type": "system", "subtype": "init", "session_id": "sess-abc",
         "cwd": f"/home/runner/{planted}", "tools": ["Bash"]},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": f"Reading the config: {planted}"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": False,
             "content": f"ANTHROPIC_API_KEY={planted}",
             "rate_limit_info": {"utilization": 0.99, "note": planted}}]}},
        *events,
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 24, "total_cost_usd": 1.23,
         "result": f"Done. {planted}",
         "rate_limit_info": {"utilization": 0.77, "note": planted},
         "env": {"ANTHROPIC_API_KEY": planted}},
    ]


def _write(tmp, payload, name="claude-execution-output.json"):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        if isinstance(payload, str):
            f.write(payload)
        else:
            json.dump(payload, f, indent=2)
    return path


def _reading(events, env=RUN_ENV, card=None, read_at=READ_AT, reason=None):
    return ur.reading(events, run=ur.run_identity(env), repo=env.get("GITHUB_REPOSITORY"),
                      read_at=read_at, card=card, reason=reason)


# --------------------------------------------------------------------------- #
# The extraction                                                               #
# --------------------------------------------------------------------------- #

class UnifiedWindows(unittest.TestCase):
    def setUp(self):
        self.reading = _reading([UNIFIED_EVENT])

    def test_it_is_reported(self):
        self.assertTrue(self.reading["reported"])
        self.assertIsNone(self.reading["not_reported_reason"])
        self.assertEqual(self.reading["rate_limit_events_seen"], 1)

    def test_one_row_per_window_as_reported(self):
        rows = {w["window"]: w for w in self.reading["windows"]}
        self.assertEqual(set(rows), {"five_hour", "seven_day", "seven_day_overage_included"})
        self.assertEqual(rows["five_hour"]["utilization"], 0.42)   # a fraction, never scaled
        self.assertEqual(rows["seven_day"]["utilization"], 0.63)
        self.assertEqual(rows["seven_day_overage_included"]["utilization"], 0.0)
        self.assertEqual(rows["five_hour"]["resets_at"], 1789866000)
        self.assertEqual(rows["five_hour"]["resets_at_iso"], "2026-09-20T01:00:00Z")
        self.assertEqual(rows["seven_day"]["resets_at_iso"], "2026-09-23T15:00:00Z")

    def test_status_type_and_overage(self):
        self.assertEqual(self.reading["status"], "allowed")
        self.assertEqual(self.reading["rate_limit_type"], "five_hour")
        self.assertEqual(self.reading["overage"]["status"], "rejected")
        self.assertEqual(self.reading["overage"]["disabled_reason"], "overage_not_provisioned")
        self.assertIs(self.reading["overage"]["is_using_overage"], False)

    def test_the_raw_event_is_kept_whole(self):
        raw = self.reading["raw"]["rate_limit_events"]
        self.assertEqual(raw, [UNIFIED_EVENT])
        # key order preserved, nothing dropped — uuid and session_id ride along
        self.assertEqual(list(raw[0]), list(UNIFIED_EVENT))
        self.assertEqual(raw[0]["uuid"], UNIFIED_EVENT["uuid"])


class FlatSingleWindow(unittest.TestCase):
    def test_the_sdk_declared_shape_yields_one_row_named_by_its_type(self):
        reading = _reading([FLAT_EVENT])
        self.assertTrue(reading["reported"])
        self.assertEqual(len(reading["windows"]), 1)
        row = reading["windows"][0]
        self.assertEqual(row["window"], "seven_day")
        self.assertEqual(row["utilization"], 0.91)
        self.assertEqual(row["resets_at"], 1790175600)
        self.assertEqual(row["resets_at_iso"], "2026-09-23T15:00:00Z")
        self.assertEqual(reading["status"], "allowed_warning")

    def test_a_status_only_event_is_reported_with_no_rows(self):
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
        reading = _reading([event])
        self.assertTrue(reading["reported"])
        self.assertEqual(reading["windows"], [])
        self.assertEqual(reading["status"], "allowed")


class SeveralEvents(unittest.TestCase):
    def test_the_last_event_is_the_reading_and_every_event_is_kept(self):
        reading = _reading([FLAT_EVENT, UNIFIED_EVENT])
        self.assertEqual(reading["rate_limit_events_seen"], 2)
        self.assertEqual(reading["raw"]["rate_limit_events"], [FLAT_EVENT, UNIFIED_EVENT])
        self.assertEqual({w["window"] for w in reading["windows"]},
                         {"five_hour", "seven_day", "seven_day_overage_included"})
        self.assertEqual(reading["status"], "allowed")


# --------------------------------------------------------------------------- #
# Absence is a reading; zero is not                                            #
# --------------------------------------------------------------------------- #

def _no_utilization_anywhere(obj):
    if isinstance(obj, dict):
        return all(k != "utilization" and _no_utilization_anywhere(v) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_no_utilization_anywhere(v) for v in obj)
    return True


class NotReported(unittest.TestCase):
    def test_a_run_that_emitted_no_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, _transcript())
            events, reason = ur.load_rate_limit_events(path)
        self.assertEqual(events, [])
        self.assertEqual(reason, ur.REASON_NO_EVENT)
        reading = _reading(events, reason=reason)
        self.assertFalse(reading["reported"])
        self.assertEqual(reading["not_reported_reason"], ur.REASON_NO_EVENT)
        self.assertEqual(reading["windows"], [])
        self.assertTrue(_no_utilization_anywhere(reading))

    def test_a_missing_file(self):
        events, reason = ur.load_rate_limit_events("/nonexistent/claude-execution-output.json")
        self.assertEqual(events, [])
        self.assertEqual(reason, ur.REASON_NO_FILE)
        self.assertTrue(_no_utilization_anywhere(_reading(events, reason=reason)))

    def test_a_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "{not json")
            events, reason = ur.load_rate_limit_events(path)
        self.assertEqual(events, [])
        self.assertEqual(reason, ur.REASON_NOT_JSON)

    def test_a_single_result_object_is_not_a_message_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, {"type": "result", "is_error": False,
                                "rate_limit_info": {"utilization": 0.5}})
            events, reason = ur.load_rate_limit_events(path)
        self.assertEqual(events, [])
        self.assertEqual(reason, ur.REASON_NOT_A_LIST)

    def test_the_cli_exits_clean_in_every_absent_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            for payload in (_transcript(), "{not json", None):
                exec_file = os.path.join(tmp, "missing.json") if payload is None \
                    else _write(tmp, payload)
                out = os.path.join(tmp, "reading.json")
                proc = _emit(exec_file, out, tmp)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                with open(out) as f:
                    reading = json.load(f)
                self.assertFalse(reading["reported"])
                self.assertIn("not reported", proc.stdout)


# --------------------------------------------------------------------------- #
# The security shape                                                           #
# --------------------------------------------------------------------------- #

class OnlyRateLimitEventsAreRead(unittest.TestCase):
    def test_only_entries_of_that_type_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, _transcript(UNIFIED_EVENT))
            events, reason = ur.load_rate_limit_events(path)
        self.assertEqual(events, [UNIFIED_EVENT])
        self.assertIsNone(reason)

    def test_a_non_dict_entry_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, ["garbage", 42, None, UNIFIED_EVENT])
            events, _ = ur.load_rate_limit_events(path)
        self.assertEqual(events, [UNIFIED_EVENT])


def _emit(exec_file, out, tmp, card=None, env=RUN_ENV):
    summary = os.path.join(tmp, "summary.md")
    gh_out = os.path.join(tmp, "github-output")
    args = [sys.executable, str(SCRIPT), "emit", "--execution-file", exec_file,
            "--out", out, "--github-output", gh_out, "--step-summary", summary]
    if card:
        args += ["--card", card]
    full = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "CLAUDE_"))}
    full.update(env)
    proc = subprocess.run(args, capture_output=True, text=True, env=full, cwd=str(ROOT))
    proc.summary = open(summary).read() if os.path.exists(summary) else ""
    proc.gh_out = open(gh_out).read() if os.path.exists(gh_out) else ""
    return proc


class TheSentinelNeverLeaves(unittest.TestCase):
    def test_nothing_from_the_transcript_reaches_file_stdout_or_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            exec_file = _write(tmp, _transcript(UNIFIED_EVENT))
            out = os.path.join(tmp, "reading.json")
            proc = _emit(exec_file, out, tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                written = f.read()
        self.assertNotIn(SENTINEL, written)
        self.assertNotIn(SENTINEL, proc.stdout)
        self.assertNotIn(SENTINEL, proc.stderr)
        self.assertNotIn(SENTINEL, proc.summary)
        # ...and the reading itself did come through
        self.assertTrue(json.loads(written)["reported"])
        self.assertIn("path=", proc.gh_out)
        self.assertIn("reported=true", proc.gh_out)


# --------------------------------------------------------------------------- #
# The Record shape                                                             #
# --------------------------------------------------------------------------- #

class DeliveryId(unittest.TestCase):
    def test_it_fits_the_contracts_segment_rule(self):
        self.assertIsNotNone(SEGMENT.fullmatch(_reading([UNIFIED_EVENT])["delivery_id"]))

    def test_it_is_deterministic_for_identical_inputs(self):
        self.assertEqual(_reading([UNIFIED_EVENT])["delivery_id"],
                         _reading([UNIFIED_EVENT])["delivery_id"])

    def test_another_attempt_is_another_delivery(self):
        env = dict(RUN_ENV, GITHUB_RUN_ATTEMPT="2")
        self.assertNotEqual(_reading([UNIFIED_EVENT])["delivery_id"],
                            _reading([UNIFIED_EVENT], env=env)["delivery_id"])

    def test_another_read_time_is_another_delivery(self):
        later = READ_AT.replace(second=5)
        self.assertNotEqual(_reading([UNIFIED_EVENT])["delivery_id"],
                            _reading([UNIFIED_EVENT], read_at=later)["delivery_id"])

    def test_a_run_outside_actions_still_gets_a_valid_id(self):
        reading = _reading([UNIFIED_EVENT], env={})
        self.assertIsNotNone(SEGMENT.fullmatch(reading["delivery_id"]))
        self.assertIsNone(reading["run"]["id"])


class FactKeyParts(unittest.TestCase):
    def test_the_key_parts_are_positive_ints_never_bools(self):
        reading = _reading([UNIFIED_EVENT])
        for value in (reading["run"]["id"], reading["run"]["attempt"], reading["read_at_epoch_ms"]):
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, bool)
            self.assertGreaterEqual(value, 1)
        self.assertEqual(reading["run"]["id"], 35413054438)
        self.assertEqual(reading["run"]["attempt"], 1)
        self.assertEqual(reading["read_at_epoch_ms"], 1789859404123)
        self.assertEqual(reading["record"]["fact_key_parts"],
                         ["run.id", "run.attempt", "read_at_epoch_ms"])

    def test_read_at_is_iso_8601_utc_and_nothing_is_named_received_at(self):
        reading = _reading([UNIFIED_EVENT])
        self.assertEqual(reading["read_at"], "2026-09-19T23:10:04.123Z")
        self.assertNotIn("received_at", json.dumps(reading))
        self.assertEqual(reading["schema"], ur.SCHEMA)
        self.assertEqual(reading["repo"], "dreadnought-foundry/portico")

    def test_the_body_declares_no_source_or_event(self):
        record = _reading([UNIFIED_EVENT])["record"]
        self.assertEqual(record["role"], "body_raw")
        self.assertNotIn("source", record)
        self.assertNotIn("event", record)


class RunIdentity(unittest.TestCase):
    def test_the_card_comes_from_the_agent_branch(self):
        env = dict(RUN_ENV, GITHUB_HEAD_REF="agent/DRE-4338-claude-usage-reading")
        self.assertEqual(_reading([UNIFIED_EVENT], env=env)["card"], "DRE-4338")

    def test_an_explicit_card_wins(self):
        env = dict(RUN_ENV, GITHUB_HEAD_REF="agent/DRE-1-x")
        self.assertEqual(_reading([UNIFIED_EVENT], env=env, card="DRE-4338")["card"], "DRE-4338")

    def test_any_other_ref_gives_null(self):
        for ref in ("main", "fix/DRE-12-thing", "dependabot/npm/foo", "repair/DRE-3-x"):
            env = dict(RUN_ENV, GITHUB_HEAD_REF=ref)
            self.assertIsNone(_reading([UNIFIED_EVENT], env=env)["card"], ref)

    def test_the_account_is_never_attributed_and_the_auth_mode_is_carried(self):
        reading = _reading([UNIFIED_EVENT])
        self.assertIs(reading["account"]["attributed"], False)
        self.assertEqual(reading["run"]["auth_mode"], "subscription")
        self.assertIsNone(_reading([UNIFIED_EVENT], env={})["run"]["auth_mode"])

    def test_the_run_url_names_the_attempt(self):
        run = _reading([UNIFIED_EVENT])["run"]
        self.assertEqual(run["url"],
                         "https://github.com/dreadnought-foundry/portico/actions/runs/35413054438/attempts/1")
        self.assertEqual(run["workflow"], "Agent Task")
        self.assertEqual(run["job"], "execute")


# --------------------------------------------------------------------------- #
# The summary line                                                             #
# --------------------------------------------------------------------------- #

class SummaryLine(unittest.TestCase):
    def test_it_names_each_window_and_its_reset_in_pacific_time(self):
        line = ur.summary_line(_reading([UNIFIED_EVENT]))
        self.assertIn("five_hour 42%", line)
        self.assertIn("seven_day 63%", line)
        self.assertIn("2026-09-19 18:00 PT", line)   # 1789866000 = 01:00Z = 18:00 PDT
        self.assertIn("2026-09-23 08:00 PT", line)   # 1790175600 = 15:00Z = 08:00 PDT
        self.assertIn("status allowed", line)
        self.assertIn("account unattributed", line)
        self.assertNotIn("Z", line.split("PT")[0].split("resets")[-1])

    def test_it_says_not_reported(self):
        line = ur.summary_line(_reading([], reason=ur.REASON_NO_EVENT))
        self.assertTrue(line.startswith("Claude usage: not reported"), line)
        self.assertIn(ur.REASON_NO_EVENT, line)


# --------------------------------------------------------------------------- #
# The wiring                                                                   #
# --------------------------------------------------------------------------- #

CLAUDE_ACTION = "anthropics/claude-code-action@"
UPLOAD = re.compile(r"^actions/upload-artifact@([0-9a-f]{40})\b")

# Each per-card workflow, its job, and the gate its emit step must carry so a
# run that never reached a model uploads nothing.
WIRED = {
    "agent-task.yml": ("execute", ("steps.gate.outputs.bounced != 'true'",
                                   "steps.dedupe.outputs.skip != 'true'")),
    "qa-review.yml": ("review", ("steps.decide.outputs.review == 'true'",
                                 "steps.size.outputs.strategy != 'oversized'")),
    "agent-fix.yml": ("fix", ("steps.pr.outputs.go == 'true'",
                              "steps.unfixable.outputs.escalate != 'true'")),
}


def _job_steps(name, job):
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    return doc["jobs"][job]["steps"]


class Wiring(unittest.TestCase):
    def _emit_and_upload(self, name):
        job, _ = WIRED[name]
        steps = _job_steps(name, job)
        emit = [i for i, s in enumerate(steps)
                if "usage_reading.py emit" in (s.get("run") or "")]
        self.assertEqual(len(emit), 1, f"{name}: exactly one emit step")
        i = emit[0]
        self.assertLess(i + 1, len(steps), f"{name}: the upload follows the emit step")
        return steps, i

    def test_the_emit_step_follows_the_last_claude_step(self):
        for name in WIRED:
            steps, i = self._emit_and_upload(name)
            last_claude = max(j for j, s in enumerate(steps)
                              if str(s.get("uses", "")).startswith(CLAUDE_ACTION))
            self.assertGreater(i, last_claude, name)

    def test_the_emit_step_never_fails_the_job_and_runs_on_every_outcome(self):
        for name, (_, gate) in WIRED.items():
            steps, i = self._emit_and_upload(name)
            step = steps[i]
            self.assertIs(step.get("continue-on-error"), True, name)
            self.assertIn("always()", str(step.get("if")), name)
            for conjunct in gate:
                self.assertIn(conjunct, str(step.get("if")), f"{name}: {conjunct}")
            self.assertNotIn("${{", step["run"], f"{name}: substitutions go in env:")

    def test_the_upload_is_pinned_named_per_attempt_and_kept_90_days(self):
        shas = set()
        for name in WIRED:
            steps, i = self._emit_and_upload(name)
            upload = steps[i + 1]
            found = UPLOAD.match(str(upload.get("uses", "")))
            self.assertIsNotNone(found, f"{name}: upload-artifact at a 40-char sha")
            shas.add(found.group(1))
            self.assertIn("github.run_attempt", str(upload["with"]["name"]), name)
            self.assertEqual(upload["with"]["retention-days"], 90, name)
            self.assertIn("always()", str(upload.get("if")), name)
            self.assertIs(upload.get("continue-on-error"), True, name)
        self.assertEqual(len(shas), 1, "one upload-artifact pin across the three")
        agent_task = (WORKFLOWS / "agent-task.yml").read_text()
        self.assertIn(f"actions/upload-artifact@{shas.pop()}", agent_task)


if __name__ == "__main__":
    unittest.main()
