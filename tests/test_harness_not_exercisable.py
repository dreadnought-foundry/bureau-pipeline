"""RED-first tests for NOT EXERCISABLE — a live fixture the harness cannot
conjure is absent, so the scenario asserted nothing (DRE-4841).

WHAT HAPPENED (2026-09-25). Main's harness run 36081827143 went red on one
line:

    dependabot_flow: FAIL at setup
      - setup: ScenarioFailure: no open genuine Dependabot PR in the sandbox
        — one cannot be conjured by API. Regenerate: comment
        `@dependabot recreate` on the newest closed Dependabot PR …

Every other scenario passed. Nothing about the commit was implicated: the
sandbox's standing fixture — bureau-harness #1, a pytest 7→9 major bump open
since 2026-07-21 — had been closed by the operator 19 minutes earlier, under
the house rule that majors are never auto-filed (DRE-2064) and alongside the
card that brings the sandbox's own `dependabot.yml` to that shape (DRE-4829).
The fixture persisted only BECAUSE it was a major the gate parked for a human;
once majors are ignored, a minor/patch bump is the arm the gate auto-merges,
so no Dependabot PR persists between runs and the fixture cannot come back.

So the harness was reporting the absence of a vendor artifact as a verdict on
the commit, on a run that gates `stable` and every `v*` tag — and its own
remedy asked a human to re-file the pull request their house rule forbids.

WHAT IS PINNED HERE:

  * **A third outcome, distinct from both.** `ScenarioFailure` says the
    pipeline is broken; `SandboxBlocked` says the sandbox is a corpse and
    stops the run. `ScenarioNotExercisable` says neither happened — the live
    fixture is not there, so nothing was proven either way about the clauses
    that need it.
  * **Never a PASS.** The summary must say NOT EXERCISABLE and name what went
    unproven. A green run that checked nothing, silently, is the failure mode
    the harness exists to prevent (`lane_contract`'s "unknown is never a
    pass").
  * **Not a verdict on the commit.** Exit 0, so `stable` keeps moving, and the
    run carries a warning annotation plus a step-summary section so the gap is
    visible on the run page rather than buried in the log.
  * **The run continues.** Unlike a dead sandbox, one missing fixture says
    nothing about the next scenario's, so later scenarios still run.
  * **The escape hatch cannot swallow a real failure** — a fixture that IS
    present and misbehaving still fails. That clause lives beside the scenario
    it guards, in `tests/test_harness_dependabot_flow.py`.
"""

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from harness import framework  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
REASON = "no open genuine Dependabot PR — the live vendor path went unobserved"


class _Recording(framework.Scenario):
    """A scenario whose phases record themselves, one of them opting out."""

    def __init__(self, name, at=None, fail_cleanup=False):
        self.name = name
        self.calls = []
        self.at = at
        self.fail_cleanup = fail_cleanup

    def _phase(self, phase):
        self.calls.append(phase)
        if self.at == phase:
            raise framework.ScenarioNotExercisable(REASON)

    def setup(self, ctx):
        self._phase("setup")

    def exercise(self, ctx):
        self._phase("exercise")

    def verify(self, ctx):
        self._phase("verify")

    def cleanup(self, ctx):
        self.calls.append("cleanup")
        if self.fail_cleanup:
            raise RuntimeError("boom-cleanup")


def _ctx():
    return framework.HarnessContext(gh=None, repo=SANDBOX, run_id="t-1",
                                    log=lambda *a: None)


class ResultShapeTest(unittest.TestCase):
    """`run_scenario` records the opt-out apart from a failure."""

    def test_the_remaining_phases_are_skipped_but_cleanup_still_runs(self):
        s = _Recording("unexercisable", at="setup")
        result = framework.run_scenario(s, _ctx())
        self.assertEqual(s.calls, ["setup", "cleanup"])
        self.assertEqual(result.not_exercisable, REASON)

    def test_it_is_not_a_failure(self):
        # The whole point: the scenario found nothing wrong with the commit —
        # it never got to look, and saying "FAIL" here holds `stable` and every
        # v* tag on a vendor artifact nobody can conjure.
        result = framework.run_scenario(_Recording("u", at="setup"), _ctx())
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.errors, [])
        self.assertIsNone(result.failed_phase)

    def test_it_is_not_a_sandbox_block(self):
        # A dead sandbox stops the whole run; a missing fixture says nothing
        # about the next scenario's, so the two must not be conflated.
        result = framework.run_scenario(_Recording("u", at="setup"), _ctx())
        self.assertIsNone(result.blocked)

    def test_the_note_names_the_phase_that_opted_out(self):
        result = framework.run_scenario(_Recording("u", at="verify"), _ctx())
        self.assertTrue(
            any("verify" in note and REASON in note for note in result.notes),
            result.notes,
        )

    def test_a_cleanup_failure_still_fails_the_scenario(self):
        # Cleanup proves the sandbox is usable for the next run. Opting out of
        # the assertions must not buy an exemption from leaving it clean.
        s = _Recording("u", at="setup", fail_cleanup=True)
        result = framework.run_scenario(s, _ctx())
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_phase, "cleanup")

    def test_a_scenario_that_ran_normally_carries_no_such_record(self):
        result = framework.run_scenario(_Recording("ok"), _ctx())
        self.assertTrue(result.ok)
        self.assertIsNone(result.not_exercisable)


class DriverTest(unittest.TestCase):
    """`python3 -m harness` with one scenario that could not be exercised."""

    def _main(self, scenarios, out_path, summary_path):
        from harness import __main__ as driver

        environ = {
            "HARNESS_WORKER_TOKEN": "t",
            "HARNESS_QA_LOGIN": "agent-bureau-qa-bot[bot]",
            "HARNESS_RUN_ID": "gha-1-1",
            "GITHUB_OUTPUT": str(out_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
        }
        buf = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), \
                mock.patch.object(driver, "discover", return_value=scenarios), \
                mock.patch.object(driver, "GitHub", lambda *a, **k: object()), \
                mock.patch("sys.stdout", buf):
            code = driver.main(["--repo", SANDBOX])
        return code, buf.getvalue()

    def setUp(self):
        self.ran = []
        ran = self.ran

        class After(framework.Scenario):
            name = "zzz_after"

            def exercise(inner, ctx):
                ran.append(inner.name)

        self.scenarios = {
            "aaa_unexercisable": _Recording("aaa_unexercisable", at="setup"),
            "zzz_after": After(),
        }
        tmp = self.enterContext(_tmpdir())
        self.out = Path(tmp) / "out.txt"
        self.summary = Path(tmp) / "summary.md"

    def test_the_later_scenarios_still_run(self):
        self._main(self.scenarios, self.out, self.summary)
        self.assertEqual(
            self.ran, ["zzz_after"],
            "a missing fixture is not a dead sandbox — it says nothing about "
            "whether the next scenario can run",
        )

    def test_the_run_is_not_red(self):
        code, _ = self._main(self.scenarios, self.out, self.summary)
        self.assertEqual(code, 0)

    def test_the_summary_says_not_exercisable_and_never_pass(self):
        _, printed = self._main(self.scenarios, self.out, self.summary)
        lines = [ln.strip() for ln in printed.splitlines()]
        line = [ln for ln in lines if ln.startswith("aaa_unexercisable:")]
        self.assertEqual(len(line), 1, printed)
        self.assertIn("NOT EXERCISABLE", line[0])
        self.assertNotIn("PASS", line[0])
        # and what went unproven is printed, not just the status word
        self.assertIn(REASON, printed)

    def test_a_passing_scenario_is_still_reported_as_a_pass(self):
        _, printed = self._main(self.scenarios, self.out, self.summary)
        self.assertIn("zzz_after: PASS", printed)

    def test_the_gap_is_annotated_on_the_run_page(self):
        # Buried in a 60-minute log, an unproven clause is invisible. A warning
        # annotation and a step-summary section are the loud, non-red channels
        # the run page already renders.
        _, printed = self._main(self.scenarios, self.out, self.summary)
        self.assertIn("::warning::", printed)
        self.assertIn("aaa_unexercisable", printed.split("::warning::")[1])
        written = self.summary.read_text()
        self.assertIn("aaa_unexercisable", written)
        self.assertIn(REASON, written)

    def test_it_is_not_reported_as_a_sandbox_block(self):
        # `blocked=true` tells promote_channel "not proven, re-prove next run"
        # and the stamp step turns the whole run's description into the block.
        # A missing fixture is a permanent, named gap — not that.
        self._main(self.scenarios, self.out, self.summary)
        written = self.out.read_text() if self.out.exists() else ""
        self.assertNotIn("blocked=true", written)

    def test_nothing_is_written_when_every_scenario_was_exercised(self):
        code, printed = self._main(
            {"zzz_after": self.scenarios["zzz_after"]}, self.out, self.summary
        )
        self.assertEqual(code, 0)
        self.assertNotIn("::warning::", printed)
        self.assertNotIn("NOT EXERCISABLE", printed)
        self.assertEqual(
            self.summary.read_text() if self.summary.exists() else "", ""
        )


class ReleaseTrainParseTest(unittest.TestCase):
    """The one consumer of the summary block must not read the new status as
    a failing scenario (`release_train.harness_failures`)."""

    def test_a_not_exercisable_line_is_not_a_named_failure(self):
        import release_train

        log = (
            "== harness summary ==\n"
            "  agent_task_parses: PASS\n"
            "  dependabot_flow: NOT EXERCISABLE — no open genuine "
            "Dependabot PR\n"
            "  gate_paths: FAIL at verify\n"
        )
        self.assertEqual(
            release_train.harness_failures(log),
            (("gate_paths", "FAIL at verify"),),
        )


def _tmpdir():
    import tempfile

    return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
