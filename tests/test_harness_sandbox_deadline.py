"""RED-first tests for the harness's dead-sandbox fail-fast (DRE-3076).

WHAT HAPPENED (2026-09-03, 20:07–21:01 PT). Main's harness run `52e9ecc4` sat
on *Run harness scenarios* from 20:07 with nothing happening in the sandbox
after 20:14. The sandbox's OWN reconcile sweep had died at 20:27 —
`Linear API returned 400 … rate limited: 2500 requests/hour exhausted` — and
the scenario that depended on it simply kept waiting. Its only exit was the
job's `timeout-minutes: 180`: no promotion until 23:07. An operator's assistant
killed it by hand at 21:01; the queued run for the newest main then passed in
eleven minutes and `stable` caught up 50 commits.

A proving run that cannot tell "the sandbox is slow" from "the sandbox is dead"
holds the release channel hostage for the length of its timeout, on exactly the
nights the channel is busiest.

WHAT IS PINNED HERE:

  * **A deadline per wait**, separate from the job timeout. Every wait a
    scenario takes carries it — the check is that no scenario reaches the bare
    `wait_until`, because a wait added later that forgot the deadline is the
    same three hours back.
  * **The deadline is a LIVENESS checkpoint, not a shorter cap.** A healthy
    sandbox that is merely slow keeps its full budget: the critic's own job
    clock is 65 minutes and `test_harness_wiring` pins the verdict wait to it,
    so a hard 10-minute cap would report FAIL on a healthy pipeline (run
    33274348041, one card earlier). At each deadline the driver asks a
    different question — is the sandbox alive? — and only a FAILED sandbox run
    ends the wait.
  * **The cause is quoted**, from the sandbox's own most recent sweep / gate /
    linear-sync run, so the receipt can say *harness blocked by sandbox* rather
    than *harness failed*.
  * **Blocked is not "the commit is bad".** The driver exits on a distinct
    code, stops running further scenarios, and writes a receipt line that
    `promote_channel.py` reads as "not proven yet".

The fixture sandbox is the real 2026-09-01 rate-limited sweep log
(`tests/fixtures/reconcile-linear-ratelimited-2026-09-01.log`), the same
condition that blocked run 52e9ecc4.
"""

import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_channel  # noqa: E402
from harness import framework, sandbox_health  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SWEEP_LOG = FIXTURES / "reconcile-linear-ratelimited-2026-09-01.log"
SCENARIO_DIR = ROOT / "scripts" / "harness" / "scenarios"
WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
SANDBOX = "dreadnought-foundry/bureau-harness"

# The quote every assertion below wants to see reach the operator — the
# sandbox's own words, not the harness's guess about them.
RATE_LIMIT_QUOTE = "rate limited: 2500 requests/hour exhausted"


def _run(path, conclusion, name=None, updated="2026-09-03T20:27:11Z", run_id=91):
    """An Actions workflow-run record, REST-shaped."""
    return {
        "id": run_id,
        "name": name or path.removesuffix(".yml").replace("-", " ").title(),
        "path": f".github/workflows/{path}",
        "status": "completed",
        "conclusion": conclusion,
        "updated_at": updated,
        "html_url": f"https://github.com/{SANDBOX}/actions/runs/{run_id}",
    }


class FakeSandbox:
    """A sandbox whose machinery runs and logs are whatever the test says.

    Mirrors the two GitHub client calls the probe makes, and counts them, so a
    probe that hammers the API on every poll is visible.
    """

    def __init__(self, runs, logs=None, fail_with=None):
        self.runs = runs
        self.logs = logs or {}
        self.fail_with = fail_with
        self.run_calls = 0
        self.log_calls = 0

    def list_workflow_runs(self, repo, per_page=50):
        self.run_calls += 1
        if self.fail_with:
            raise self.fail_with
        return list(self.runs)

    def run_log_text(self, repo, run_id):
        self.log_calls += 1
        return self.logs.get(run_id)


# ── the probe: what the sandbox's last machinery run says ──────────────────
class SandboxHealthTest(unittest.TestCase):
    def test_a_failed_sweep_is_reported_with_its_own_words(self):
        gh = FakeSandbox(
            [_run("reconcile.yml", "failure", name="Reconcile (reusable)")],
            logs={91: SWEEP_LOG.read_text()},
        )
        failure = sandbox_health.latest_failure(gh, SANDBOX)
        self.assertIsNotNone(failure, "a failed sweep must be reported")
        self.assertIn(RATE_LIMIT_QUOTE, failure.text)
        # Named as the self-healing quota condition the medic already knows,
        # never re-derived here.
        self.assertEqual(failure.classification, "linear_ratelimited")
        quote = failure.quote()
        self.assertTrue(
            quote.startswith(promote_channel.BLOCKED_MARKER),
            f"the quote must carry the receipt marker: {quote!r}",
        )
        self.assertIn("Reconcile", quote)
        self.assertIn("2026-09-03T20:27:11Z", quote)
        self.assertIn(RATE_LIMIT_QUOTE, quote)

    def test_a_healthy_sandbox_reports_nothing(self):
        gh = FakeSandbox([
            _run("reconcile.yml", "success"),
            _run("merge-gate.yml", "success", run_id=92),
            _run("linear-sync.yml", "skipped", run_id=93),
        ])
        self.assertIsNone(sandbox_health.latest_failure(gh, SANDBOX))

    def test_only_the_machinery_workflows_count(self):
        """A failing product-CI run in the sandbox is not a dead sandbox — the
        scenarios are ABOUT red checks, and gate_paths deliberately drives some.
        Only the sweep, the gate and the linear-sync say the machinery itself
        has stopped."""
        gh = FakeSandbox([
            _run("ci.yml", "failure", name="CI"),
            _run("tests.yml", "failure", name="Tests", run_id=92),
        ])
        self.assertIsNone(sandbox_health.latest_failure(gh, SANDBOX))

    def test_the_gate_and_linear_sync_count_too(self):
        for path in ("merge-gate.yml", "linear-sync.yml"):
            with self.subTest(path=path):
                gh = FakeSandbox([_run(path, "failure")], logs={91: "boom"})
                self.assertIsNotNone(sandbox_health.latest_failure(gh, SANDBOX))

    def test_only_the_most_recent_run_of_a_workflow_is_read(self):
        """A sweep that failed and then RECOVERED is not a dead sandbox."""
        gh = FakeSandbox([
            _run("reconcile.yml", "success", updated="2026-09-03T20:42:00Z"),
            _run("reconcile.yml", "failure", updated="2026-09-03T20:27:11Z",
                 run_id=90),
        ])
        self.assertIsNone(sandbox_health.latest_failure(gh, SANDBOX))

    def test_an_unreadable_sandbox_is_unknown_never_dead(self):
        """Never raise on unknown data — a read failure must not invent a
        block, or a GitHub blip would fail every harness run."""
        gh = FakeSandbox([], fail_with=RuntimeError("GitHub API 403: forbidden"))
        notes = []
        self.assertIsNone(
            sandbox_health.latest_failure(gh, SANDBOX, log=notes.append)
        )
        self.assertTrue(notes, "an unreadable probe must say so")

    def test_a_cancelled_run_is_not_a_dead_sandbox(self):
        gh = FakeSandbox([_run("reconcile.yml", "cancelled")])
        self.assertIsNone(sandbox_health.latest_failure(gh, SANDBOX))

    def test_an_unreadable_log_still_reports_the_failure(self):
        """The run listing is already evidence. A log we cannot read costs the
        quote, not the fail-fast."""
        gh = FakeSandbox([_run("reconcile.yml", "failure")], logs={})
        failure = sandbox_health.latest_failure(gh, SANDBOX)
        self.assertIsNotNone(failure)
        self.assertIn(
            "https://github.com/dreadnought-foundry/bureau-harness/actions/runs/91",
            failure.quote(),
        )


class ErrorSummaryTest(unittest.TestCase):
    def test_the_generic_exit_line_is_never_the_quote(self):
        """`Process completed with exit code 75` is what the operator already
        knew. The line above it is the one worth three hours."""
        summary = sandbox_health.error_summary(SWEEP_LOG.read_text())
        self.assertIn(RATE_LIMIT_QUOTE, summary)
        self.assertNotIn("Process completed with exit code", summary)

    def test_the_log_timestamp_and_job_columns_are_stripped(self):
        summary = sandbox_health.error_summary(SWEEP_LOG.read_text())
        self.assertFalse(summary.startswith("sweep\t"))
        self.assertNotRegex(summary, r"^\d{4}-\d\d-\d\dT")

    def test_a_log_with_nothing_but_the_generic_line_falls_back_to_it(self):
        summary = sandbox_health.error_summary(
            "2026-09-03T20:27:11Z ##[error]Process completed with exit code 1"
        )
        self.assertIn("exit code 1", summary)


class ReceiptLineTest(unittest.TestCase):
    """The receipt crosses a shell and GitHub's 140-char status description —
    and its text comes from a LOG, which is not ours to trust."""

    def test_the_line_is_clamped_to_the_status_description_limit(self):
        line = sandbox_health.receipt_line(
            promote_channel.BLOCKED_MARKER + " sandbox Reconcile failed: " + "x" * 500
        )
        self.assertLessEqual(len(line), sandbox_health.RECEIPT_LIMIT)
        self.assertTrue(line.startswith(promote_channel.BLOCKED_MARKER))

    def test_the_clamp_keeps_the_cause_not_only_the_preamble(self):
        """The 2026-09-03 quote, clamped. A tail clamp kept the timestamp and
        threw away the rate limit — the only part anyone reads it for."""
        line = sandbox_health.receipt_line(
            f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile (reusable) "
            "failure at 2026-09-03T20:27:11Z (linear_ratelimited): reconcile: "
            "Linear API returned 400 from https://api.linear.app/graphql: "
            f"{RATE_LIMIT_QUOTE}"
        )
        self.assertLessEqual(len(line), sandbox_health.RECEIPT_LIMIT)
        self.assertTrue(line.startswith(promote_channel.BLOCKED_MARKER))
        self.assertIn("rate limited", line)

    def test_the_raw_payload_dump_is_not_what_gets_quoted(self):
        """`linear_ops` appends the API's body AFTER naming the condition. The
        sentence is the receipt; the JSON is for the medic's classifier."""
        summary = sandbox_health.error_summary(SWEEP_LOG.read_text())
        self.assertIn(RATE_LIMIT_QUOTE, summary)
        self.assertNotIn("body:", summary)
        self.assertNotIn("Rate limit exceeded. Only", summary)

    def test_newlines_and_control_characters_cannot_ride_into_the_receipt(self):
        """A `blocked_reason=` line in $GITHUB_OUTPUT is a key=value file: a
        newline in the value is a second key, and the value is log text."""
        line = sandbox_health.receipt_line(
            "harness blocked: boom\nblocked=false\n::set-output name=x::y"
        )
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)


# ── the deadline: every wait has one, and it asks a liveness question ──────
class Clock:
    """A fake clock that only advances when the code under test sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class WaitDeadlineTest(unittest.TestCase):
    def _ctx(self, probe, deadline=framework.WAIT_DEADLINE_SECONDS):
        clock = Clock()
        ctx = framework.HarnessContext(
            gh=None, repo=SANDBOX, run_id="gha-1-1",
            clock=clock, sleep=clock.sleep, log=lambda *a: None,
            wait_deadline=deadline, sandbox_probe=probe,
        )
        return ctx, clock

    def test_a_dead_sandbox_ends_the_wait_within_a_minute_of_the_deadline(self):
        """Acceptance criterion 1, in one assertion."""
        cause = f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed"
        ctx, clock = self._ctx(lambda description, elapsed: cause)
        with self.assertRaises(framework.SandboxBlocked) as caught:
            ctx.wait("a verdict that will never come", lambda: None,
                     timeout=ctx.verdict_timeout)
        self.assertLessEqual(
            clock.now, framework.WAIT_DEADLINE_SECONDS + 60,
            "the run must end within a minute of the deadline, not at the "
            "job timeout",
        )
        self.assertIn(cause, str(caught.exception))
        self.assertEqual(caught.exception.cause, cause)

    def test_a_slow_but_healthy_sandbox_keeps_its_full_budget(self):
        """The deadline is a liveness checkpoint, NOT a shorter cap. The
        critic's own job clock is 65 minutes; a hard 10-minute cap would report
        FAIL on a healthy pipeline (run 33274348041)."""
        ctx, clock = self._ctx(lambda description, elapsed: None)
        with self.assertRaises(framework.HarnessTimeout):
            ctx.wait("a slow but honest verdict", lambda: None,
                     timeout=ctx.verdict_timeout)
        self.assertGreaterEqual(clock.now, ctx.verdict_timeout)

    def test_the_probe_is_asked_again_at_every_deadline_not_only_the_first(self):
        """A sandbox that dies at minute 30 of a 70-minute wait must not be
        waited out to minute 70."""
        asked = []

        def probe(description, elapsed):
            asked.append(elapsed)
            return "harness blocked: died late" if len(asked) >= 3 else None

        ctx, clock = self._ctx(probe)
        with self.assertRaises(framework.SandboxBlocked):
            ctx.wait("a verdict", lambda: None, timeout=ctx.verdict_timeout)
        self.assertGreaterEqual(len(asked), 3)
        self.assertLess(clock.now, ctx.verdict_timeout)

    def test_a_wait_that_expires_asks_the_probe_before_reporting_a_timeout(self):
        """"When a wait expires, the harness reads the sandbox's most recent
        run" — a deadline longer than the wait must not skip the read."""
        ctx, clock = self._ctx(
            lambda description, elapsed: "harness blocked: sandbox is down",
            deadline=10_000.0,
        )
        with self.assertRaises(framework.SandboxBlocked):
            ctx.wait("a merge", lambda: None, timeout=120.0)

    def test_a_satisfied_wait_never_probes(self):
        ctx, _ = self._ctx(lambda description, elapsed: self.fail("probed"))
        self.assertEqual(ctx.wait("now", lambda: "ok", timeout=60.0), "ok")

    def test_the_deadline_can_be_switched_off(self):
        """The operator escape hatch: 0 minutes = no liveness probe at all."""
        ctx, _ = self._ctx(lambda d, e: self.fail("probed"), deadline=0)
        with self.assertRaises(framework.HarnessTimeout):
            ctx.wait("a verdict", lambda: None, timeout=60.0)


class FixtureSandboxTest(unittest.TestCase):
    """The card's own test, end to end: a fixture sandbox whose SWEEP has
    failed — the scenario fails within the deadline with the sweep's error
    quoted. Everything real except GitHub: the shipped probe, the shipped
    wait, the shipped phase runner, and the actual 2026-09-01 sweep log."""

    def setUp(self):
        self.gh = FakeSandbox(
            [_run("reconcile.yml", "failure", name="Reconcile (reusable)")],
            logs={91: SWEEP_LOG.read_text()},
        )
        self.clock = Clock()
        self.ctx = framework.HarnessContext(
            gh=self.gh, repo=SANDBOX, run_id="gha-1-1",
            clock=self.clock, sleep=self.clock.sleep, log=lambda *a: None,
            sandbox_probe=sandbox_health.probe(
                (self.gh,), SANDBOX, log=lambda *a: None
            ),
        )

        class WaitsForever(framework.Scenario):
            name = "waits_forever"

            def verify(inner, ctx):
                ctx.wait("a critic verdict that will never come",
                         lambda: None, timeout=ctx.verdict_timeout)

        self.result = framework.run_scenario(WaitsForever(), self.ctx)

    def test_the_scenario_fails_with_the_sweeps_own_error_quoted(self):
        self.assertFalse(self.result.ok)
        self.assertEqual(self.result.failed_phase, "verify")
        self.assertIn(RATE_LIMIT_QUOTE, self.result.blocked)
        self.assertIn("Reconcile", self.result.blocked)

    def test_it_fails_inside_the_deadline_not_the_job_timeout(self):
        self.assertLessEqual(
            self.clock.now, framework.WAIT_DEADLINE_SECONDS + 60,
            "the whole card: 10 minutes, not the job's 180",
        )

    def test_the_probe_reads_the_sandbox_once_not_on_every_poll(self):
        """Twenty polls per deadline; the liveness read happens at the
        deadline, so a healthy long wait costs a handful of calls, not a
        thousand."""
        self.assertEqual(self.gh.run_calls, 1)
        self.assertEqual(self.gh.log_calls, 1)


class EveryWaitCarriesTheDeadlineTest(unittest.TestCase):
    """The card's title: *each scenario wait has its own deadline*. Pinned as
    a property of the code rather than of today's call sites, because the wait
    somebody adds next year is the one that costs the three hours."""

    def _scenario_sources(self):
        return sorted(p for p in SCENARIO_DIR.glob("*.py")
                      if p.name != "__init__.py")

    def test_no_scenario_calls_the_bare_wait_helper(self):
        offenders = []
        for path in self._scenario_sources():
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"(?<![.\w])wait_until\s*\(", line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "scenario waits must go through ctx.wait(), which carries the "
            "sandbox deadline; a bare wait_until() is a wait that can hang "
            "for the whole job timeout",
        )

    def test_the_context_offers_the_wait_the_scenarios_use(self):
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x")
        self.assertTrue(callable(getattr(ctx, "wait", None)))
        self.assertGreater(ctx.wait_deadline, 0)


# ── a blocked run stops, says why, and is not a verdict on the commit ──────
class BlockedRunTest(unittest.TestCase):
    def _blocking_scenario(self, cause):
        class Blocked(framework.Scenario):
            name = "blocked_probe"

            def exercise(self, ctx):
                raise framework.SandboxBlocked(f"{cause} — waited 600s",
                                               cause=cause)

        return Blocked()

    def test_run_scenario_records_the_block_distinctly_from_a_failure(self):
        cause = f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile failed"
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x",
                                       log=lambda *a: None)
        result = framework.run_scenario(self._blocking_scenario(cause), ctx)
        self.assertFalse(result.ok)
        self.assertEqual(result.blocked, cause)
        self.assertEqual(result.failed_phase, "exercise")

    def test_an_ordinary_failure_is_not_marked_blocked(self):
        class Broken(framework.Scenario):
            name = "broken"

            def verify(self, ctx):
                raise framework.ScenarioFailure("the gate merged nothing")

        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x",
                                       log=lambda *a: None)
        result = framework.run_scenario(Broken(), ctx)
        self.assertFalse(result.ok)
        self.assertIsNone(result.blocked)


class DriverTest(unittest.TestCase):
    """`python3 -m harness` on a dead sandbox: stop, say why, exit distinctly."""

    def _main(self, scenarios, env, out_path):
        from harness import __main__ as driver

        environ = {
            "HARNESS_WORKER_TOKEN": "t",
            "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
            "HARNESS_RUN_ID": "gha-1-1",
            "GITHUB_OUTPUT": str(out_path),
            **env,
        }
        buf = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(driver, "discover", return_value=scenarios), \
                mock.patch.object(driver, "GitHub", lambda *a, **k: object()), \
                mock.patch.object(sandbox_health, "probe",
                                  return_value=self.probe), \
                mock.patch("sys.stdout", buf):
            code = driver.main(["--repo", SANDBOX])
        return code, buf.getvalue()

    def setUp(self):
        self.cause = (f"{promote_channel.BLOCKED_MARKER} sandbox Reconcile "
                      f"failed at 2026-09-03T20:27:11Z: {RATE_LIMIT_QUOTE}")
        self.ran = []
        blocked_cause = self.cause

        class First(framework.Scenario):
            name = "aaa_blocked"

            def exercise(inner, ctx):
                raise framework.SandboxBlocked(blocked_cause,
                                               cause=blocked_cause)

        ran = self.ran

        class Second(framework.Scenario):
            name = "zzz_after"

            def exercise(inner, ctx):
                ran.append(inner.name)

        self.scenarios = {"aaa_blocked": First(), "zzz_after": Second()}
        self.probe = lambda description, elapsed: blocked_cause

    def test_a_blocked_scenario_stops_the_run_immediately(self):
        out = Path(self.enterContext(_tmpdir())) / "out.txt"
        code, printed = self._main(self.scenarios, {}, out)
        self.assertEqual(
            self.ran, [],
            "a dead sandbox must not be re-proved by every later scenario — "
            "that is the three hours, one deadline at a time",
        )
        self.assertEqual(code, framework.BLOCKED_EXIT)
        self.assertIn(RATE_LIMIT_QUOTE, printed)

    def test_the_receipt_names_the_block_for_the_stamp_step(self):
        out = Path(self.enterContext(_tmpdir())) / "out.txt"
        self._main(self.scenarios, {}, out)
        written = out.read_text()
        self.assertIn("blocked=true", written)
        reason = [ln for ln in written.splitlines()
                  if ln.startswith("blocked_reason=")]
        self.assertEqual(len(reason), 1, written)
        self.assertTrue(
            reason[0].removeprefix("blocked_reason=").startswith(
                promote_channel.BLOCKED_MARKER
            ),
            reason[0],
        )
        self.assertLessEqual(
            len(reason[0].removeprefix("blocked_reason=")),
            sandbox_health.RECEIPT_LIMIT,
        )

    def test_the_deadline_input_reaches_the_context(self):
        seen = {}
        real_ctx = framework.HarnessContext

        def capture(**kwargs):
            seen.update(kwargs)
            return real_ctx(**kwargs)

        out = Path(self.enterContext(_tmpdir())) / "out.txt"
        with mock.patch.object(framework, "HarnessContext", capture):
            self._main({"zzz_after": self.scenarios["zzz_after"]},
                       {"HARNESS_WAIT_DEADLINE_MINUTES": "4"}, out)
        self.assertEqual(seen.get("wait_deadline"), 240.0)

    def test_no_receipt_is_written_when_nothing_was_blocked(self):
        out = Path(self.enterContext(_tmpdir())) / "out.txt"
        code, _ = self._main({"zzz_after": self.scenarios["zzz_after"]}, {}, out)
        self.assertEqual(code, 0)
        self.assertNotIn("blocked=true", out.read_text() if out.exists() else "")


def _tmpdir():
    import tempfile

    return tempfile.TemporaryDirectory()


# ── the wiring: the workflow input, and the receipt the stamp writes ───────
def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _job():
    return _doc()["jobs"]["harness"]


def _steps():
    return _job().get("steps") or []


def _step(predicate):
    for step in _steps():
        if predicate(step):
            return step
    return None


class WorkflowWiringTest(unittest.TestCase):
    def test_the_deadline_is_a_dispatch_input(self):
        on = _doc().get("on", _doc().get(True)) or {}
        inputs = (on.get("workflow_dispatch") or {}).get("inputs") or {}
        self.assertIn(
            "wait_deadline_minutes", inputs,
            "the per-wait deadline must be an operator input, separate from "
            "the job timeout",
        )
        self.assertEqual(
            str(inputs["wait_deadline_minutes"].get("default")),
            str(int(framework.WAIT_DEADLINE_SECONDS // 60)),
            "the workflow default and the driver default are one number",
        )

    def test_the_input_reaches_the_driver(self):
        step = _step(lambda s: "python3 -m harness" in (s.get("run") or ""))
        self.assertIsNotNone(step, "no scenario-running step")
        self.assertEqual(
            (step.get("env") or {}).get("HARNESS_WAIT_DEADLINE_MINUTES"),
            "${{ inputs.wait_deadline_minutes }}",
            "an input the driver never reads is not a deadline",
        )

    def test_the_deadline_is_far_below_the_job_timeout(self):
        """The whole point: a stuck wait must die on its own deadline, never
        on the job's three-hour ceiling."""
        cap = _job().get("timeout-minutes", 0) * 60
        self.assertLess(framework.WAIT_DEADLINE_SECONDS * 2, cap)

    def test_the_stamp_quotes_the_block_in_its_description(self):
        step = _step(lambda s: "/statuses/" in (s.get("run") or ""))
        self.assertIsNotNone(step, "no stamp step")
        env = step.get("env") or {}
        self.assertEqual(
            env.get("BLOCKED_REASON"),
            "${{ steps.scenarios.outputs.blocked_reason }}",
            "the stamp must carry the driver's blocked receipt",
        )
        self.assertIn(
            "BLOCKED_REASON", step.get("run") or "",
            "the receipt must reach the status description the promote "
            "workflow reads",
        )

    def test_the_scenario_step_is_addressable(self):
        step = _step(lambda s: "python3 -m harness" in (s.get("run") or ""))
        self.assertEqual(step.get("id"), "scenarios")


if __name__ == "__main__":
    unittest.main()
