"""RED-first: a harness run GitHub refused for a rate limit is INCONCLUSIVE,
never a red trunk (DRE-9068).

WHAT HAPPENED (2026-11-02, ~9:10 PM PT). Integration Harness run 41581663622,
on main @ dd9a33d, ended "harness-failed / red trunk". Every code scenario
passed. The two failures were `gate_paths`' cleanup and `proof_task_parses`'
exercise, and both were GitHub's `403 API rate limit exceeded for installation
ID 123249480` — the worker bot's shared installation. `stable` read a busy
quota as a red trunk and held until a by-hand promote at 9:30 PM PT (run
41584296722).

WHAT IS PINNED HERE:

  * A harness STEP fed a 403 whose body names an installation's rate limit
    waits and retries, and passes when the retry succeeds — and the same for
    a secondary limit that names no reset (GitHub asks for at least a minute)
    or that carries a far primary window beside a non-zero remaining count.
  * A refusal that outlasts the wait ends the scenario RATE-LIMITED, apart
    from a failure and apart from a sandbox block: the driver stops the run,
    exits `framework.INCONCLUSIVE_EXIT`, and writes a receipt opening with
    `promote_channel.RATE_LIMIT_MARKER`.
  * `promote_channel` reads that stamp as `harness-inconclusive` /
    rate-limited, says "re-run the harness", and never `harness-failed` —
    and the channel-hold diagnosis reads it as a re-run, not a fix.
  * A REAL failure — a 404, an assertion — is still `harness-failed`, and a
    real failure anywhere in the run outranks a rate limit elsewhere in it.
  * promote-channel.yml reads the stamp on a FAILED harness run, which is
    the only way the marker reaches the decision at all.

Run: python3 -m pytest tests/test_harness_rate_limit_inconclusive.py -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import channel_hold  # noqa: E402
import promote_channel  # noqa: E402
import red_main_repair  # noqa: E402
from harness import framework, sandbox_health  # noqa: E402
from harness import __main__ as driver  # noqa: E402
from harness.github_api import GitHub, GitHubError, RateLimited  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
SHA = "dd9a33d" + "0" * 33
PROMOTE_YML = ROOT / ".github" / "workflows" / "promote-channel.yml"

#: The refusal run 41581663622 was answered with, word for word.
INSTALLATION_REFUSAL = (
    b'{"message": "API rate limit exceeded for installation ID 123249480. '
    b'If you reach out to GitHub Support for help, please include the request '
    b'ID 6402:2134C1:CBF3F9:29A1A37:6AB1CFD7"}'
)
SECONDARY_REFUSAL = (
    b'{"message": "You have exceeded a secondary rate limit and have been '
    b'temporarily blocked from content creation. Please retry your request '
    b'again later."}'
)
NOT_FOUND = b'{"message": "Not Found"}'
NOW = 1_793_682_600.0  # 2026-11-02T21:10:00-08:00


def _refusal(status=403, body=INSTALLATION_REFUSAL, headers=None):
    return urllib.error.HTTPError(
        "https://api.github.com/repos/x", status, "refused",
        headers if headers is not None else {}, io.BytesIO(body),
    )


class ScriptedOpener:
    """Answers each request from a script, recording who asked."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.methods = []

    def __call__(self, req):
        self.methods.append(req.get_method())
        answer = self.answers.pop(0) if self.answers else (200, b"{}", {})
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _worker(answers, naps, log=None):
    """The worker client: no fallback identity, so a refusal is waited out."""
    return GitHub(
        "ghs_worker", opener=ScriptedOpener(answers), identity="worker",
        wall_clock=lambda: NOW, sleeper=naps.append,
        log=(log.append if log is not None else (lambda *_: None)),
    )


def _ctx(gh=None):
    return framework.HarnessContext(gh=gh, repo=SANDBOX, run_id="main-gha-1-1",
                                    log=lambda *_: None)


class _Commenter(framework.Scenario):
    """A harness step that makes ONE worker write — proof_task_parses'
    exercise is the same shape: one call, whose refusal used to end the run."""

    name = "commenter"

    def exercise(self, ctx):
        ctx.gh.create_comment(SANDBOX, 1, "harness probe")


# --------------------------------------------------------------------------- #
# A harness step waits out the refusal and passes                              #
# --------------------------------------------------------------------------- #

class TheStepWaitsAndRetriesTest(unittest.TestCase):

    def test_an_installation_refusal_is_waited_out_and_the_step_passes(self):
        naps = []
        gh = _worker([
            _refusal(headers={"x-ratelimit-remaining": "0",
                              "x-ratelimit-reset": str(int(NOW + 600))}),
            (201, b'{"id": 7}', {}),
        ], naps)
        result = framework.run_scenario(_Commenter(), _ctx(gh))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(naps), 1)
        self.assertGreaterEqual(naps[0], 600)
        self.assertLessEqual(naps[0], 610)

    def test_a_secondary_limit_naming_no_reset_is_waited_a_minute(self):
        # GitHub's own rule for a secondary limit with neither header: wait
        # at least a minute. Before this card the client read "no reset" as
        # "nothing to wait for" and the refusal stood on the spot.
        naps = []
        gh = _worker([_refusal(body=SECONDARY_REFUSAL), (201, b'{"id": 8}', {})], naps)
        result = framework.run_scenario(_Commenter(), _ctx(gh))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(naps), 1)
        self.assertGreaterEqual(naps[0], 60)
        self.assertLessEqual(naps[0], 65)

    def test_a_far_primary_window_beside_remaining_requests_is_not_this_refusal(self):
        # A secondary limit with no `retry-after` still carries the PRIMARY
        # window's reset, and `x-ratelimit-remaining` says that window is not
        # what refused us. Reading the reset there judged a one-minute pause
        # an hour long, past the cap, and gave up.
        naps = []
        gh = _worker([
            _refusal(body=SECONDARY_REFUSAL,
                     headers={"x-ratelimit-remaining": "3120",
                              "x-ratelimit-reset": str(int(NOW + 55 * 60))}),
            (201, b'{"id": 9}', {}),
        ], naps)
        result = framework.run_scenario(_Commenter(), _ctx(gh))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(naps), 1)
        self.assertLessEqual(naps[0], 65, "a minute, not the primary window's 55")

    def test_the_header_read_skips_a_reset_whose_window_still_has_room(self):
        from harness import github_api

        self.assertIsNone(github_api._reset_from_headers(
            {"x-ratelimit-remaining": "12", "x-ratelimit-reset": "1790000000"}, 1000.0))
        self.assertEqual(github_api._reset_from_headers(
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}, 1000.0),
            1790000000)
        # `retry-after` still wins, whatever the remaining count says.
        self.assertEqual(github_api._reset_from_headers(
            {"retry-after": "30", "x-ratelimit-remaining": "12",
             "x-ratelimit-reset": "1790000000"}, 1000.0), 1030)


# --------------------------------------------------------------------------- #
# A refusal that outlasts the wait is rate-limited, not failed                  #
# --------------------------------------------------------------------------- #

class _Far(framework.Scenario):
    """Refused by a meter that resets past the wait cap."""

    name = "far"

    def __init__(self, phase="exercise", then=None):
        self.phase, self.then = phase, then

    def _refuse(self, ctx):
        raise RateLimited(403, INSTALLATION_REFUSAL.decode(), reset=int(NOW + 3000))

    def exercise(self, ctx):
        if self.phase == "exercise":
            self._refuse(ctx)
        if self.then == "assert":
            raise framework.ScenarioFailure("the gate merged nothing")
        if self.then == "404":
            raise GitHubError(404, NOT_FOUND.decode())

    def cleanup(self, ctx):
        if self.phase == "cleanup":
            self._refuse(ctx)
        if self.then == "cleanup-404":
            raise GitHubError(404, NOT_FOUND.decode())


class TheScenarioIsRateLimitedTest(unittest.TestCase):

    def test_a_refusal_past_the_cap_ends_the_scenario_rate_limited(self):
        result = framework.run_scenario(_Far(), _ctx())
        self.assertFalse(result.ok)
        self.assertIsNone(result.blocked, "a busy quota is not a dead sandbox")
        self.assertTrue(result.rate_limited)
        self.assertIn("123249480", result.rate_limited)
        self.assertEqual(result.failed_phase, "exercise")

    def test_a_refused_cleanup_after_passing_phases_is_rate_limited(self):
        # gate_paths' cleanup on run 41581663622: every phase had passed.
        result = framework.run_scenario(_Far(phase="cleanup"), _ctx())
        self.assertFalse(result.ok)
        self.assertTrue(result.rate_limited)
        self.assertEqual(result.failed_phase, "cleanup")

    def test_a_real_failure_with_a_refused_cleanup_is_still_a_failure(self):
        result = framework.run_scenario(_Far(phase="cleanup", then="assert"), _ctx())
        self.assertFalse(result.ok)
        self.assertIsNone(result.rate_limited)

    def test_a_refusal_followed_by_a_real_cleanup_failure_is_a_failure(self):
        result = framework.run_scenario(_Far(then="cleanup-404"), _ctx())
        self.assertFalse(result.ok)
        self.assertIsNone(result.rate_limited)

    def test_a_404_is_a_failure_and_not_rate_limited(self):
        result = framework.run_scenario(_Far(phase="none", then="404"), _ctx())
        self.assertFalse(result.ok)
        self.assertIsNone(result.rate_limited)
        self.assertIsNone(result.blocked)

    def test_a_permission_403_is_a_failure_and_not_rate_limited(self):
        naps = []
        gh = _worker([_refusal(body=b'{"message": "Resource not accessible by integration"}')], naps)
        result = framework.run_scenario(_Commenter(), _ctx(gh))
        self.assertFalse(result.ok)
        self.assertIsNone(result.rate_limited)
        self.assertEqual(naps, [])


# --------------------------------------------------------------------------- #
# The driver: stop, say inconclusive, exit distinctly                           #
# --------------------------------------------------------------------------- #

def _run_driver(scenarios):
    """`python3 -m harness` over `scenarios`: (exit code, stdout, outputs)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        environ = {
            "HARNESS_WORKER_TOKEN": "t",
            "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
            "HARNESS_RUN_ID": "gha-41581663622-1",
            "HARNESS_WAIT_DEADLINE_MINUTES": "0",
            "GITHUB_OUTPUT": str(out),
        }
        buf = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(driver, "discover", return_value=scenarios), \
                mock.patch.object(driver, "GitHub", lambda *a, **k: object()), \
                mock.patch("sys.stdout", buf):
            code = driver.main(["--repo", SANDBOX])
        return code, buf.getvalue(), out.read_text() if out.exists() else ""


def _driver_scenarios(ran, first="refused"):
    """Two scenarios: the first refused for a rate limit past the cap (or
    failing a real assertion), the second recording that it ran."""

    class First(framework.Scenario):
        name = "aaa_first"

        def exercise(inner, ctx):
            if first == "refused":
                raise RateLimited(403, INSTALLATION_REFUSAL.decode(),
                                  reset=int(NOW + 3000))
            if first == "assert":
                raise framework.ScenarioFailure("the gate merged nothing")

    class Second(framework.Scenario):
        name = "zzz_second"

        def exercise(inner, ctx):
            ran.append(inner.name)
            raise RateLimited(403, INSTALLATION_REFUSAL.decode(),
                              reset=int(NOW + 3000))

    return {"aaa_first": First(), "zzz_second": Second()}


class DriverTest(unittest.TestCase):

    def _main(self, scenarios):
        return _run_driver(scenarios)

    def _scenarios(self, first="refused"):
        self.ran = []
        return _driver_scenarios(self.ran, first)

    def test_a_rate_limited_run_stops_and_exits_inconclusive(self):
        code, printed, _ = self._main(self._scenarios())
        self.assertEqual(code, framework.INCONCLUSIVE_EXIT)
        self.assertNotIn(framework.INCONCLUSIVE_EXIT, (0, 1, 2, framework.BLOCKED_EXIT))
        self.assertEqual(self.ran, [], "an exhausted meter is not re-asked by every scenario")
        self.assertIn("harness INCONCLUSIVE / rate-limited", printed)
        self.assertIn("RATE-LIMITED at exercise", printed)

    def test_the_receipt_carries_the_rate_limit_marker_for_the_stamp(self):
        _, _, written = self._main(self._scenarios())
        self.assertIn("rate_limited=true", written)
        reason = [ln for ln in written.splitlines() if ln.startswith("blocked_reason=")]
        self.assertEqual(len(reason), 1, written)
        line = reason[0].removeprefix("blocked_reason=")
        self.assertTrue(line.startswith(promote_channel.RATE_LIMIT_MARKER), line)
        self.assertLessEqual(len(line), sandbox_health.RECEIPT_LIMIT)
        self.assertIn("aaa_first", line)

    def test_a_real_failure_outranks_a_later_rate_limit(self):
        code, printed, written = self._main(self._scenarios(first="assert"))
        self.assertEqual(code, 1)
        self.assertEqual(self.ran, ["zzz_second"])
        self.assertNotIn("rate_limited=true", written)
        self.assertNotIn("blocked_reason=", written)
        self.assertNotIn("INCONCLUSIVE", printed)

    def test_red_main_repair_does_not_read_the_run_as_a_code_failure(self):
        # The driver's own `::error::` line is what Red-Main Repair reads off
        # a red harness run; the marker opens with the sandbox-block marker,
        # so no fix agent is spent on a commit nobody judged.
        _, printed, _ = self._main(self._scenarios())
        self.assertTrue(red_main_repair.is_sandbox_blocked("Integration Harness", printed))


# --------------------------------------------------------------------------- #
# The promoter: inconclusive, re-run the harness — never a red trunk            #
# --------------------------------------------------------------------------- #

def _stamp(state, description):
    return {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                          "state": state, "description": description}]}


def _receipt():
    """The stamp description run 41581663622 would have carried."""
    return sandbox_health.receipt_line(
        f"{promote_channel.RATE_LIMIT_MARKER} — gate_paths cleanup: GitHub "
        f"API 403: {INSTALLATION_REFUSAL.decode()}"
    )


class PromoterReadsInconclusiveTest(unittest.TestCase):

    def test_the_vocabulary(self):
        self.assertEqual(promote_channel.OUTCOME_INCONCLUSIVE, "harness-inconclusive")
        self.assertEqual(promote_channel.INCONCLUSIVE_RATE_LIMITED, "rate-limited")
        self.assertTrue(promote_channel.RATE_LIMIT_MARKER.startswith(
            promote_channel.BLOCKED_MARKER))

    def test_a_rate_limited_failed_run_is_inconclusive_not_red_trunk(self):
        decision = promote_channel.evaluate(
            _stamp("failure", _receipt()), SHA, ancestry="ahead",
            conclusion="failure", branch="main",
        )
        self.assertFalse(decision.promote)
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_INCONCLUSIVE)
        self.assertNotEqual(decision.outcome, promote_channel.OUTCOME_FAILED)
        self.assertTrue(decision.reason.startswith("rate-limited"), decision.reason)
        self.assertIn("re-run the harness", decision.reason)
        self.assertNotIn("red trunk, not a busy one", decision.reason)

    def test_the_driver_receipt_end_to_end_reaches_inconclusive(self):
        written = _run_driver(_driver_scenarios([]))[2]
        line = next(ln for ln in written.splitlines() if ln.startswith("blocked_reason="))
        decision = promote_channel.evaluate(
            _stamp("failure", line.removeprefix("blocked_reason=")), SHA,
            ancestry="ahead", conclusion="failure", branch="main",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_INCONCLUSIVE)

    def test_the_cli_receipt_reads_inconclusive_slash_rate_limited(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        statuses, out = Path(tmp) / "s.json", Path(tmp) / "out.txt"
        statuses.write_text(json.dumps(_stamp("failure", _receipt())))
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                mock.patch("sys.stdout", buf):
            code = promote_channel.main(
                ["--sha", SHA, "--statuses-file", str(statuses), "--ancestry", "ahead",
                 "--conclusion", "failure", "--branch", "main"])
        self.assertEqual(code, 0)
        self.assertTrue(buf.getvalue().startswith("harness-inconclusive: rate-limited"),
                        buf.getvalue())
        self.assertIn("outcome=harness-inconclusive\n", out.read_text())
        self.assertIn("promote=false\n", out.read_text())

    def test_a_real_failure_is_still_harness_failed(self):
        decision = promote_channel.evaluate(
            _stamp("failure", "integration harness: failure"), SHA,
            ancestry="ahead", conclusion="failure", branch="main",
        )
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_FAILED)

    def test_a_by_hand_promote_still_refuses_an_inconclusive_stamp(self):
        decision = promote_channel.evaluate(
            _stamp("failure", _receipt()), SHA, ancestry="ahead", manual=True,
            trunk="identical", actor="sid",
        )
        self.assertFalse(decision.promote)
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_UNPROVEN)

    def test_the_self_hosting_table_names_it(self):
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        rows = [ln for ln in doc.splitlines() if ln.startswith("| `harness-inconclusive` |")]
        self.assertEqual(len(rows), 1)
        self.assertIn("re-run", rows[0])


# --------------------------------------------------------------------------- #
# The channel-hold diagnosis reads it as a re-run                               #
# --------------------------------------------------------------------------- #

def _job(name, step="Run harness scenarios", attempt=1):
    at = datetime(2026, 11, 3, 5, 10, tzinfo=timezone.utc)
    return channel_hold.Job(name, "https://github.com/x/actions/runs/41581663622",
                            attempt, channel_hold.FAILURE, at, at, step)


def _reading(job):
    decision = channel_hold.Decision(
        promote_channel.OUTCOME_INCONCLUSIVE,
        "rate-limited — not promoting: re-run the harness", "https://run")
    return channel_hold.Reading(held=True, carried="", chain=[SHA],
                                decision=decision, failing=job)


class ChannelHoldReadsARerunTest(unittest.TestCase):

    def test_the_outcome_maps_to_a_rerun(self):
        self.assertEqual(channel_hold.OUTCOME_NEXT[promote_channel.OUTCOME_INCONCLUSIVE],
                         channel_hold.RERUN)

    def test_the_failed_harness_job_is_a_rerun_not_a_fix(self):
        step, why = channel_hold.next_step(_reading(_job(channel_hold.HARNESS_JOB)))
        self.assertEqual(step, channel_hold.RERUN, why)
        self.assertIn("rate", why)

    def test_a_failed_unit_suite_job_beside_it_is_still_a_fix(self):
        step, _ = channel_hold.next_step(
            _reading(_job("scripts unit tests (1)", step="Run tests")))
        self.assertEqual(step, channel_hold.FIX)

    def test_the_harness_job_name_is_the_workflows_own(self):
        doc = yaml.safe_load((ROOT / ".github" / "workflows" / "harness.yml").read_text())
        job = doc["jobs"][channel_hold.HARNESS_JOB]
        self.assertNotIn("name", job, "the job's id is its name only while it has no `name:`")


# --------------------------------------------------------------------------- #
# The wiring: the stamp reaches the decision on a FAILED harness run            #
# --------------------------------------------------------------------------- #

class PromoteWiringTest(unittest.TestCase):

    def _step(self, name):
        doc = yaml.safe_load(PROMOTE_YML.read_text())
        return next(s for s in doc["jobs"]["promote"]["steps"] if s.get("name") == name)

    def test_a_failed_harness_run_fetches_the_stamp(self):
        step = self._step("Fetch the candidate's combined commit status")
        cond = " ".join(str(step.get("if")).split())
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", cond)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", cond)
        self.assertIn("head_branch == 'main'", cond)

    def test_the_fetch_names_the_runs_own_head_when_resolve_did_not_run(self):
        step = self._step("Fetch the candidate's combined commit status")
        candidate = " ".join(str(step["env"]["CANDIDATE"]).split())
        self.assertIn("steps.resolve.outputs.candidate", candidate)
        self.assertIn("github.event.workflow_run.head_sha", candidate)

    def test_a_failed_run_still_mints_no_token(self):
        # Reading a stamp needs only the workflow token; the bot token that
        # can move the ref stays on the green and unit-suite routes.
        cond = " ".join(str(self._step("Mint bot token").get("if")).split())
        self.assertNotIn("conclusion == 'failure'", cond)


if __name__ == "__main__":
    unittest.main()
