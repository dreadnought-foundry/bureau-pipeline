"""RED-first: the sandbox groom proposal's wiring (DRE-8782).

`groomer.yml` takes one more input, `sandbox`, off by default. Set, a
`propose` run hands `--sandbox` to `groomer.py propose`, and a `drain`
refuses before any script runs: nobody drains a sandbox proposal. Off, the
Groom step runs exactly as before.

`self-groomer.yml`'s morning makes a second `propose` run with `sandbox` set,
in the job `sandbox`:

  * AFTER the real one — it needs the `schedule` job, and runs whatever that
    job concluded, so a real run that failed never holds the sandbox's;
  * IN THE GROOMING HOUR — keyed on the same gate's `go`, the one that reads
    the 06:00-06:59 PT clock;
  * ON THE SANDBOX GROOM CARD — the gate reads it from committed config
    (`config/groom-sandbox.json`, through `scripts/groom_sandbox.py card`),
    never a repo variable, which a proof run cannot read;
  * and its failure fails nothing of the real one's: the `schedule` job does
    not need it, and no job does.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_sandbox_wiring.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_groomer_verify_wiring import (  # noqa: E402
    GROOM_STEP, PIPELINE, _RunsSteps, _expression, _groom_env, _load, _named,
    _needs, _on)

GATE_STEP = "The sandbox groom card, from committed config"


class TheSandboxInputTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("groomer.yml")

    def test_the_reusable_takes_sandbox_off_by_default(self):
        spec = (_on(self.doc)["workflow_call"].get("inputs") or {}).get("sandbox")
        self.assertIsNotNone(spec, "the reusable takes no sandbox input")
        self.assertEqual(spec.get("type"), "string")
        self.assertEqual(spec.get("default"), "")
        self.assertIs(spec.get("required"), False)

    def test_the_groom_step_reads_it_through_env(self):
        step = _named(self.doc["jobs"]["groom"], GROOM_STEP)
        self.assertEqual(_expression((step.get("env") or {}).get("SANDBOX")),
                         "inputs.sandbox")
        self.assertNotIn("${{", step["run"])


class TheSandboxGroomsTest(_RunsSteps):
    def setUp(self):
        super().setUp()
        self.groom = _named(self.doc["jobs"]["groom"], GROOM_STEP)

    def propose_argv(self) -> list:
        return next(c["argv"] for c in self.recorded()
                    if c["script"] == "groomer.py")

    def test_a_sandbox_propose_hands_groomer_the_flag(self):
        proc = self.run_step(self.groom, _groom_env(SANDBOX="true",
                                                    CARD="DRE-8780"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.propose_argv()
        self.assertEqual(argv[0], "propose")
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--card") + 1], "DRE-8780")

    def test_without_sandbox_propose_is_as_today(self):
        for env in (_groom_env(), _groom_env(SANDBOX=""),
                    _groom_env(SANDBOX="false")):
            self.calls.unlink(missing_ok=True)
            proc = self.run_step(self.groom, env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("--sandbox", self.propose_argv())

    def test_a_drain_with_sandbox_refuses_before_any_write_and_says_why(self):
        proc = self.run_step(self.groom, _groom_env(MODE="drain",
                                                    SANDBOX="true"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.recorded(), [], "a script ran")
        self.assertFalse((self.ws / "proposal.txt").exists())
        self.assertFalse(self.output.exists() and self.output.read_text())
        self.assertIn("sandbox", proc.stderr)
        self.assertIn("never drained", proc.stderr)

    def test_a_drain_without_sandbox_still_drains(self):
        proc = self.run_step(self.groom, _groom_env(MODE="drain"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.scripts_run(), [("groomer.py", "drain")])


class TheMorningSandboxRunTest(unittest.TestCase):
    def setUp(self):
        self.doc = _load("self-groomer.yml")
        self.jobs = self.doc["jobs"]
        self.job = self.jobs.get("sandbox")
        self.assertIsNotNone(self.job, "the stub has no `sandbox` job")
        self.with_ = self.job.get("with") or {}

    def test_it_runs_after_the_real_proposal(self):
        self.assertEqual(sorted(_needs(self.job)), ["gate", "schedule"])

    def test_it_runs_in_the_grooming_hour_whatever_the_real_one_concluded(self):
        cond = _expression(self.job.get("if"))
        self.assertIn("needs.gate.outputs.go == 'true'", cond)
        self.assertIn("!cancelled()", cond,
                      "without a status function a failed real run would "
                      "skip the sandbox's")
        self.assertNotIn("needs.schedule.result", cond)

    def test_it_proposes_with_sandbox_on_the_sandbox_groom_card(self):
        self.assertEqual(self.with_.get("mode"), "propose")
        self.assertEqual(self.with_.get("sandbox"), "true")
        self.assertEqual(_expression(self.with_.get("card")),
                         "needs.gate.outputs.sandbox_card")
        self.assertNotIn("vars.", str(self.with_.get("card")),
                         "a proof run cannot read a repo variable")
        self.assertEqual(self.job.get("uses"),
                         f"{PIPELINE}/.github/workflows/groomer.yml@main")
        self.assertEqual(self.job.get("secrets"), "inherit")

    def test_the_gate_reads_the_card_from_committed_config(self):
        gate = self.jobs["gate"]
        step = _named(gate, GATE_STEP)
        self.assertIn("python3 .bureau-pipeline/scripts/groom_sandbox.py card",
                      step.get("run") or "")
        self.assertEqual(
            _expression((gate.get("outputs") or {}).get("sandbox_card")),
            f"steps.{step['id']}.outputs.card")
        self.assertTrue((ROOT / "config" / "groom-sandbox.json").is_file())

    def test_a_sandbox_failure_fails_nothing_of_the_real_run(self):
        self.assertNotIn("sandbox", _needs(self.jobs["schedule"]))
        for name, job in self.jobs.items():
            self.assertNotIn("sandbox", _needs(job),
                             f"job {name!r} waits on the sandbox run")
        gate = self.jobs["gate"]
        step = _named(gate, GATE_STEP)
        # The real proposal's gate never goes red over the sandbox's card.
        self.assertTrue(step.get("continue-on-error"))
        order = [s.get("name") for s in gate["steps"]]
        self.assertGreater(order.index(GATE_STEP),
                           order.index("Is it 06:00 PT, and is the standing card open?"))

    def test_the_real_proposal_job_is_unchanged(self):
        job = self.jobs["schedule"]
        self.assertNotIn("sandbox", job.get("with") or {})
        self.assertEqual(_expression(job.get("if")),
                         "needs.gate.outputs.go == 'true'")


if __name__ == "__main__":
    unittest.main()
