"""RED-first: the harness re-kicks a sandbox run GitHub left queued with no
jobs, instead of failing the whole harness half an hour later (DRE-6147).

WHAT HAPPENED (2026-10-07). PR #779 merged at 07:57 PT and the harness run
for it, 37641092524 attempt 1, started at 07:58. Scenario `gate_paths` opened
sandbox PR bureau-harness#3506 and waited for its merge gate. From about 08:07
four bureau-harness Merge Gate runs — 37642346652, 37642362278, 37642364418,
37642390202 — sat in GitHub's queue with NO JOBS CREATED, while every other
sandbox run ran normally. The liveness probe ran at 616, 1235 and 1854 s and
failed the run at 08:35 as `SandboxIdle`. `promote-channel` then refused with
"a red trunk", the channel read `state=blocked behind=4`, and merge-to-live
took an hour for a stall in GitHub's queue no code caused.

WHAT IS PINNED HERE:

  * A sandbox run `queued` with ZERO jobs for longer than
    `framework.QUEUE_STALL_SECONDS` is cancelled and re-run, one log line
    names the stuck run, the replacement and the wait, and the wait carries on
    inside the same scenario — which then passes on the replacement's result.
  * A run queued WITH jobs is waiting for a runner, not stuck, and is never
    cancelled.
  * After `framework.QUEUE_REKICK_LIMIT` re-kicks the wait fails with a reason
    naming GitHub's queue — a `SandboxBlocked`, never a `SandboxIdle` — and
    `promote_channel` refuses it as a queue stall, not a red trunk.
  * The no-run-created `SandboxIdle` path is unchanged.

Run: python3 -m pytest tests/test_harness_queue_stall.py -v
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_channel  # noqa: E402
from harness import framework, sandbox_health  # noqa: E402
from harness.github_api import GitHub  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
WALL = 1_791_384_420.0  # 2026-10-07T08:07:00-07:00, near enough
STUCK_ID = 37642346652


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class Clock:
    """Monotonic and wall time that only advance when the code sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def wall(self):
        return WALL + self.now

    def sleep(self, seconds):
        self.now += seconds


class QueuedSandbox:
    """A sandbox holding Merge Gate run 37642346652, as GitHub reported it on
    2026-10-07: `queued`, and no jobs.

    `jobs` is how many jobs the run has while queued — 0 is the stall, 1 is a
    run waiting for a runner. `replacements_stall` makes every re-run land in
    the same stall, the case the re-kick limit exists for. Every write is
    recorded, so a test can say exactly what the harness cancelled and re-ran.
    """

    def __init__(self, clock, jobs=0, replacements_stall=False, queued_at=None):
        self.clock = clock
        self.jobs = jobs
        self.replacements_stall = replacements_stall
        self.run = {
            "id": STUCK_ID,
            "name": "Merge Gate",
            "path": ".github/workflows/merge-gate.yml",
            "status": "queued",
            "conclusion": None,
            "run_attempt": 1,
            "created_at": _iso(queued_at if queued_at is not None else WALL),
            "run_started_at": _iso(queued_at if queued_at is not None else WALL),
            "html_url": f"https://github.com/{SANDBOX}/actions/runs/{STUCK_ID}",
        }
        self.cancels, self.reruns = [], []
        self.recent_calls = 0

    # ── reads ────────────────────────────────────────────────────────────
    def list_recent_runs(self, repo, per_page=50):
        self.recent_calls += 1
        return [dict(self.run)]

    def list_workflow_runs(self, repo, per_page=50):
        return [dict(self.run)] if self.run["status"] == "completed" else []

    def list_run_jobs(self, repo, run_id):
        count = self.jobs if self.run["status"] == "queued" else 1
        return {"total_count": count, "jobs": [{"id": n} for n in range(count)]}

    def get_workflow_run(self, repo, run_id):
        return dict(self.run)

    # ── writes ───────────────────────────────────────────────────────────
    def cancel_workflow_run(self, repo, run_id):
        self.cancels.append(run_id)
        if self.run["status"] == "completed":
            return False
        self.run.update(status="completed", conclusion="cancelled")
        return True

    def rerun_workflow_run(self, repo, run_id):
        self.reruns.append(run_id)
        self.run["run_attempt"] += 1
        self.run["run_started_at"] = _iso(self.clock.wall())
        self.run["conclusion"] = None
        self.run["status"] = "queued" if self.replacements_stall else "in_progress"
        if not self.replacements_stall:
            self.jobs = 1

    def replacement_ran(self):
        return self.run["run_attempt"] > 1 and self.run["status"] != "queued"


def _ctx(gh, clock, logs, probe=None, **kwargs):
    return framework.HarnessContext(
        gh=gh, repo=SANDBOX, run_id="gha-1-1",
        clock=clock, sleep=clock.sleep, wall_clock=clock.wall,
        log=logs.append, sandbox_probe=probe,
        queue_watch=sandbox_health.QueueWatch(
            gh, SANDBOX, wall_clock=clock.wall, sleep=clock.sleep,
            log=logs.append,
        ),
        **kwargs,
    )


def _waiting_scenario(poll, description="the gate merging PR #3506"):
    class WaitsOnTheGate(framework.Scenario):
        name = "gate_paths"

        def verify(self, ctx):
            ctx.wait(description, poll, timeout=ctx.verdict_timeout)

    return WaitsOnTheGate()


def _rekick_lines(logs):
    return [line for line in logs if "queue stall:" in str(line)
            and "re-ran it" in str(line)]


# ── the named constants ────────────────────────────────────────────────────
class ConstantsTest(unittest.TestCase):
    def test_the_bound_is_five_minutes_and_named(self):
        self.assertEqual(framework.QUEUE_STALL_SECONDS, 5 * 60.0)

    def test_the_rekick_limit_is_two_and_named(self):
        self.assertEqual(framework.QUEUE_REKICK_LIMIT, 2)

    def test_the_failure_marker_is_a_blocked_receipt(self):
        """It opens with the blocked marker, so every reader that already
        knows a blocked run — the stamp step, red-main repair's back-off —
        keeps treating it as one, and names GitHub's queue after it."""
        self.assertTrue(
            promote_channel.QUEUE_STALL_MARKER.startswith(
                promote_channel.BLOCKED_MARKER
            )
        )
        self.assertIn("GitHub queue stall", promote_channel.QUEUE_STALL_MARKER)


# ── what counts as stuck ───────────────────────────────────────────────────
class StuckRunTest(unittest.TestCase):
    def _watch(self, gh, clock):
        return sandbox_health.QueueWatch(
            gh, SANDBOX, wall_clock=clock.wall, sleep=clock.sleep,
            log=lambda *a: None,
        )

    def test_queued_with_no_jobs_past_the_bound_is_stuck(self):
        clock = Clock()
        gh = QueuedSandbox(clock)
        clock.now = framework.QUEUE_STALL_SECONDS + 1
        stuck = self._watch(gh, clock).stuck()
        self.assertEqual([s.run_id for s in stuck], [STUCK_ID])
        self.assertEqual(stuck[0].workflow, "Merge Gate")

    def test_queued_with_no_jobs_inside_the_bound_is_not_yet_stuck(self):
        clock = Clock()
        gh = QueuedSandbox(clock)
        clock.now = framework.QUEUE_STALL_SECONDS - 1
        self.assertEqual(self._watch(gh, clock).stuck(), [])

    def test_queued_with_jobs_is_waiting_for_a_runner_not_stuck(self):
        clock = Clock()
        gh = QueuedSandbox(clock, jobs=1)
        clock.now = 10 * framework.QUEUE_STALL_SECONDS
        self.assertEqual(self._watch(gh, clock).stuck(), [])

    def test_a_run_whose_jobs_cannot_be_read_is_unknown_never_stuck(self):
        clock = Clock()
        gh = QueuedSandbox(clock)
        clock.now = 10 * framework.QUEUE_STALL_SECONDS

        def refuse(repo, run_id):
            raise RuntimeError("GitHub API 403: Resource not accessible")

        gh.list_run_jobs = refuse
        self.assertEqual(self._watch(gh, clock).stuck(), [])

    def test_the_bound_runs_from_the_latest_attempt_not_the_first(self):
        """A replacement gets its own five minutes: the clock is the attempt's
        `run_started_at`, which a re-run resets, not `created_at`, which it
        does not."""
        clock = Clock()
        gh = QueuedSandbox(clock, queued_at=WALL - 3600)
        gh.run["run_started_at"] = _iso(WALL)
        clock.now = framework.QUEUE_STALL_SECONDS - 1
        self.assertEqual(self._watch(gh, clock).stuck(), [])


# ── acceptance criterion 1: cancel, re-run, keep waiting, pass ─────────────
class RekickedRunPassesTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gh = QueuedSandbox(self.clock)
        self.logs = []
        self.ctx = _ctx(self.gh, self.clock, self.logs)
        # The gate merges once the replacement has run.
        self.result = framework.run_scenario(
            _waiting_scenario(lambda: self.gh.replacement_ran() or None),
            self.ctx,
        )

    def test_the_stuck_run_is_cancelled_then_re_run(self):
        self.assertEqual(self.gh.cancels, [STUCK_ID])
        self.assertEqual(self.gh.reruns, [STUCK_ID])

    def test_the_scenario_passes_on_the_replacements_result(self):
        self.assertTrue(self.result.ok, self.result.errors)
        self.assertIsNone(self.result.blocked)

    def test_it_was_re_kicked_soon_after_the_bound_not_at_the_idle_verdict(self):
        self.assertLess(self.clock.now, framework.WAIT_DEADLINE_SECONDS)

    def test_one_line_names_the_stuck_run_the_replacement_and_the_wait(self):
        lines = _rekick_lines(self.logs)
        self.assertEqual(len(lines), 1, self.logs)
        line = lines[0]
        self.assertIn(str(STUCK_ID), line)
        self.assertIn("Merge Gate", line)
        self.assertIn("attempt 2", line)
        self.assertIn("the gate merging PR #3506", line)


# ── acceptance criterion 2: queued WITH jobs keeps today's rule ────────────
class QueuedWithJobsTest(unittest.TestCase):
    def test_a_run_waiting_for_a_runner_is_never_cancelled(self):
        clock = Clock()
        gh = QueuedSandbox(clock, jobs=1)
        logs = []
        ctx = _ctx(gh, clock, logs)
        with self.assertRaises(framework.HarnessTimeout) as caught:
            ctx.wait("the gate merging PR #3506", lambda: None,
                     timeout=ctx.verdict_timeout)
        self.assertNotIsInstance(caught.exception, framework.SandboxBlocked)
        self.assertEqual(gh.cancels, [])
        self.assertEqual(gh.reruns, [])
        self.assertEqual(_rekick_lines(logs), [])


# ── acceptance criterion 3: two re-kicks, then a GitHub-queue failure ──────
class RekickLimitTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.gh = QueuedSandbox(self.clock, replacements_stall=True)
        self.logs = []
        self.ctx = _ctx(self.gh, self.clock, self.logs)
        self.result = framework.run_scenario(
            _waiting_scenario(lambda: None), self.ctx
        )

    def test_it_re_kicks_exactly_the_limit_and_no_more(self):
        self.assertEqual(len(self.gh.reruns), framework.QUEUE_REKICK_LIMIT)
        self.assertEqual(len(self.gh.cancels), framework.QUEUE_REKICK_LIMIT)

    def test_one_log_line_per_re_kick(self):
        lines = _rekick_lines(self.logs)
        self.assertEqual(len(lines), framework.QUEUE_REKICK_LIMIT, self.logs)
        self.assertIn("attempt 2", lines[0])
        self.assertIn("attempt 3", lines[1])

    def test_the_scenario_fails_naming_githubs_queue_not_sandbox_idle(self):
        self.assertFalse(self.result.ok)
        self.assertEqual(self.result.failed_phase, "verify")
        self.assertIsNotNone(self.result.blocked)
        self.assertTrue(
            self.result.blocked.startswith(promote_channel.QUEUE_STALL_MARKER),
            self.result.blocked,
        )
        self.assertIn(str(STUCK_ID), self.result.blocked)
        self.assertNotIn("SandboxIdle", " ".join(self.result.errors))

    def test_the_failure_is_a_block_not_an_idle_timeout(self):
        self.assertTrue(
            issubclass(framework.SandboxQueueStall, framework.SandboxBlocked)
        )
        self.assertFalse(
            issubclass(framework.SandboxQueueStall, framework.HarnessTimeout)
        )

    def test_it_fails_well_before_the_idle_verdict_would_have(self):
        self.assertLess(
            self.clock.now,
            framework.IDLE_PROBE_LIMIT * framework.WAIT_DEADLINE_SECONDS,
        )

    def test_promote_channel_refuses_it_as_a_queue_stall_not_a_red_trunk(self):
        """The receipt goes the way every blocked receipt goes: the driver
        clamps it into the stamp's description, and the promoter reads it
        there. The run itself concluded `failure`, exactly as on 2026-10-07."""
        description = sandbox_health.receipt_line(self.result.blocked)
        combined = {"statuses": [{
            "context": promote_channel.STATUS_CONTEXT,
            "state": "failure",
            "description": description,
        }]}
        decision = promote_channel.evaluate(
            combined, "aec997e2", conclusion="failure", branch="main",
            ancestry=promote_channel.AHEAD,
        )
        self.assertFalse(decision.promote)
        self.assertEqual(decision.outcome, promote_channel.OUTCOME_QUEUE_STALL)
        self.assertIn("queue stall", decision.reason.lower())
        self.assertNotIn("red trunk", decision.reason.lower())

    def test_red_main_repair_still_backs_off_from_it(self):
        """No diff turns a GitHub queue stall green, so no fix agent is
        dispatched at the commit that met one."""
        import red_main_repair

        self.assertTrue(red_main_repair.is_sandbox_blocked(
            "Integration Harness", f"harness BLOCKED: {self.result.blocked}"
        ))


# ── acceptance criterion 4: the no-run-created SandboxIdle path ───────────
class IdlePathUnchangedTest(unittest.TestCase):
    def test_no_run_at_all_still_ends_as_sandbox_idle(self):
        """A sandbox that started nothing has nothing to re-kick: the wait
        ends exactly as DRE-3453 ends it, two probes in, with its own
        wording."""
        clock = Clock()

        class Empty:
            cancels, reruns = [], []

            def list_recent_runs(self, repo, per_page=50):
                return []

            def cancel_workflow_run(self, repo, run_id):
                self.cancels.append(run_id)

            def rerun_workflow_run(self, repo, run_id):
                self.reruns.append(run_id)

        gh = Empty()
        asked = []

        def probe(description, elapsed):
            asked.append(elapsed)
            return sandbox_health.ProbeReport(newest_run_at=WALL - 900.0)

        logs = []
        ctx = _ctx(gh, clock, logs, probe=probe)
        with self.assertRaises(framework.SandboxIdle) as caught:
            ctx.wait("a critic comment on PR #1494", lambda: None,
                     timeout=ctx.verdict_timeout)
        self.assertIn(
            "waiting for something the sandbox will not do: "
            "a critic comment on PR #1494",
            str(caught.exception),
        )
        self.assertEqual(len(asked), framework.IDLE_PROBE_LIMIT)
        self.assertLessEqual(
            clock.now,
            framework.IDLE_PROBE_LIMIT * framework.WAIT_DEADLINE_SECONDS + 60,
        )
        self.assertEqual(gh.cancels, [])
        self.assertEqual(gh.reruns, [])

    def test_a_wait_with_no_queue_watch_behaves_as_before(self):
        clock = Clock()
        ctx = framework.HarnessContext(
            gh=None, repo=SANDBOX, run_id="gha-1-1",
            clock=clock, sleep=clock.sleep, wall_clock=clock.wall,
            log=lambda *a: None,
            sandbox_probe=lambda d, e: sandbox_health.ProbeReport(
                newest_run_at=WALL - 900.0
            ),
        )
        self.assertIsNone(ctx.queue_watch)
        with self.assertRaises(framework.SandboxIdle):
            ctx.wait("a verdict", lambda: None, timeout=ctx.verdict_timeout)


# ── a re-kick GitHub refuses degrades to today's behaviour ─────────────────
class RefusedRekickTest(unittest.TestCase):
    def test_a_refused_cancel_is_logged_and_never_counted_or_retried(self):
        clock = Clock()
        gh = QueuedSandbox(clock)
        attempts = []

        def refuse(repo, run_id):
            attempts.append(run_id)
            raise RuntimeError("GitHub API 403: Resource not accessible")

        gh.cancel_workflow_run = refuse
        logs = []
        ctx = _ctx(gh, clock, logs)
        with self.assertRaises(framework.HarnessTimeout) as caught:
            ctx.wait("a merge", lambda: None, timeout=ctx.verdict_timeout)
        self.assertNotIsInstance(caught.exception, framework.SandboxBlocked)
        self.assertEqual(attempts, [STUCK_ID], "one refusal is enough to know")
        self.assertTrue(any("could not re-kick" in str(l) for l in logs), logs)


# ── the client call and the driver wiring ──────────────────────────────────
class RerunCallTest(unittest.TestCase):
    def test_rerun_posts_to_the_runs_rerun_endpoint(self):
        seen = []

        def opener(req):
            seen.append((req.get_method(), req.full_url))
            return (201, b"", {})

        GitHub("ghs_x", opener=opener).rerun_workflow_run(SANDBOX, STUCK_ID)
        self.assertEqual(seen, [(
            "POST",
            f"https://api.github.com/repos/{SANDBOX}/actions/runs/"
            f"{STUCK_ID}/rerun",
        )])


class DriverWiringTest(unittest.TestCase):
    def _main(self, env):
        from harness import __main__ as driver

        class Quiet(framework.Scenario):
            name = "quiet"

        seen = {}
        real_ctx = framework.HarnessContext

        def capture(**kwargs):
            seen.update(kwargs)
            return real_ctx(**kwargs)

        environ = {
            "HARNESS_WORKER_TOKEN": "t",
            "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
            "HARNESS_RUN_ID": "gha-1-1",
            **env,
        }
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(driver, "discover",
                                  return_value={"quiet": Quiet()}), \
                mock.patch.object(driver, "GitHub", lambda *a, **k: object()), \
                mock.patch.object(framework, "HarnessContext", capture), \
                mock.patch("sys.stdout", io.StringIO()):
            driver.main(["--repo", SANDBOX])
        return seen

    def test_every_scenario_context_carries_the_queue_watch(self):
        seen = self._main({})
        self.assertIsInstance(seen.get("queue_watch"), sandbox_health.QueueWatch)

    def test_the_operators_off_switch_turns_it_off_too(self):
        """`HARNESS_WAIT_DEADLINE_MINUTES=0` is the pre-DRE-3076 escape hatch:
        no sandbox checks at all. A re-kick is a write into the sandbox, so
        it obeys the same switch."""
        seen = self._main({"HARNESS_WAIT_DEADLINE_MINUTES": "0"})
        self.assertIsNone(seen.get("queue_watch"))


class DocsTest(unittest.TestCase):
    def test_the_receipt_table_names_the_queue_stall_outcome(self):
        text = (ROOT / "docs" / "self-hosting.md").read_text()
        self.assertIn(f"`{promote_channel.OUTCOME_QUEUE_STALL}`", text)

    def test_the_harness_readme_describes_the_re_kick(self):
        text = (ROOT / "scripts" / "harness" / "README.md").read_text()
        self.assertIn("QUEUE_STALL_SECONDS", text)
        self.assertIn("QUEUE_REKICK_LIMIT", text)


if __name__ == "__main__":
    unittest.main()
