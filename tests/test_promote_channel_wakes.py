"""RED-first: the commit's own unit suite starts the promotion too (DRE-6993).

On 2026-10-11 `stable` sat on commits whose own `Pipeline Tests` run on
`main` was red from 12:27 to 13:33 PT. Promote Channel run 38169771118 moved
it to `4472399a` at 12:53:25, one minute and 56 seconds after that commit's
own suite (run 38167776550) had ended `failure`. DRE-6968 built the part of
the decision that can refuse such a commit (`with_unit_suite`,
`--unit-suite`), and nothing handed it the suite's result.

This file pins the wiring: the promotion starts on both workflows, reads the
candidate's own `tests.yml` push run on every route, weighs it on both passes,
never takes a unit-suite run's conclusion for the harness's, never names a
unit-suite run as the harness run, and fails a run whose suite proves nothing.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote_channel  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
HARNESS_WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
DOC = ROOT / "docs" / "self-hosting.md"

SHA = "4472399a" + "0" * 32
HARNESS = "Integration Harness"

#: The run measured on 2026-10-11, and a green one.
RED = {"run_id": 38167776550, "status": "completed", "conclusion": "failure"}
GREEN = {"run_id": 38171950338, "status": "completed", "conclusion": "success"}

GREEN_STAMP = {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                             "state": "success"}]}
PENDING_STAMP = {"statuses": [{"context": promote_channel.STATUS_CONTEXT,
                               "state": "pending"}]}


def _flat(text) -> str:
    return " ".join(str(text or "").split())


class MainTest(unittest.TestCase):
    """`promote_channel.main` with `--woken-by`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *, stamp, unit, extra):
        statuses = self.tmp / "combined_status.json"
        statuses.write_text(json.dumps(stamp))
        record = self.tmp / "unit_suite.json"
        record.write_text(json.dumps(unit))
        out = self.tmp / "gh_output"
        out.write_text("")
        printed = io.StringIO()
        os.environ["GITHUB_OUTPUT"] = str(out)
        try:
            with contextlib.redirect_stdout(printed):
                rc = promote_channel.main(
                    ["--sha", SHA, "--statuses-file", str(statuses),
                     "--unit-suite", str(record), *extra])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
        self.assertEqual(rc, 0)
        return printed.getvalue(), out.read_text()

    def test_a_red_unit_suite_start_is_answered_as_a_red_suite(self):
        printed, written = self._run(stamp=GREEN_STAMP, unit=RED, extra=[
            "--woken-by", promote_channel.UNIT_SUITE, "--conclusion", "failure",
            "--branch", "main", "--ancestry", "ahead"])
        self.assertIn("promote=false\n", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNIT_RED}\n", written)
        self.assertIn("38167776550", printed)

    def test_the_same_failure_from_the_harness_is_a_failed_harness(self):
        _, written = self._run(stamp=GREEN_STAMP, unit=RED, extra=[
            "--woken-by", HARNESS, "--conclusion", "failure",
            "--branch", "main", "--ancestry", "ahead"])
        self.assertIn("promote=false\n", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_FAILED}\n", written)

    def test_a_green_suite_never_promotes_a_commit_the_harness_has_not_proved(self):
        _, written = self._run(stamp=PENDING_STAMP, unit=GREEN, extra=[
            "--woken-by", promote_channel.UNIT_SUITE, "--conclusion", "success",
            "--branch", "main", "--ancestry", "ahead"])
        self.assertIn("promote=false\n", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_UNPROVEN}\n", written)

    def test_a_green_suite_on_a_proved_commit_promotes(self):
        _, written = self._run(stamp=GREEN_STAMP, unit=GREEN, extra=[
            "--woken-by", promote_channel.UNIT_SUITE, "--conclusion", "success",
            "--branch", "main", "--ancestry", "ahead"])
        self.assertIn("promote=true\n", written)
        self.assertIn(f"outcome={promote_channel.OUTCOME_PROMOTING}\n", written)

    def test_without_the_flag_a_promotion_reads_as_before(self):
        printed, written = self._run(stamp=GREEN_STAMP, unit=GREEN, extra=[
            "--conclusion", "success", "--branch", "main", "--ancestry", "ahead"])
        reason = f"promoting stable to {SHA} — harness green, strictly ahead."
        self.assertEqual(printed, f"harness-passed-promoting: {reason}\n")
        self.assertEqual(written, (
            "promote=true\n"
            f"reason={reason}\n"
            "outcome=harness-passed-promoting\n"))

    def test_without_the_flag_a_failed_harness_reads_as_before(self):
        printed, written = self._run(stamp=GREEN_STAMP, unit=GREEN, extra=[
            "--conclusion", "failure", "--branch", "main", "--ancestry", "ahead"])
        reason = (f"not promoting {SHA}: the harness run failed "
                  f"(conclusion=failure). This is a red trunk, not a busy one.")
        self.assertEqual(printed, f"harness-failed: {reason}\n")
        self.assertEqual(written, (
            "promote=false\n"
            f"reason={reason}\n"
            "outcome=harness-failed\n"))

    def test_an_empty_woken_by_is_not_given(self):
        """A dispatch has no starting run, so the workflow hands over an empty
        name — which must read exactly as the flag left out."""
        _, written = self._run(stamp=GREEN_STAMP, unit=GREEN, extra=[
            "--woken-by", "", "--conclusion", "failure",
            "--branch", "main", "--ancestry", "ahead"])
        self.assertIn(f"outcome={promote_channel.OUTCOME_FAILED}\n", written)


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load(WORKFLOW.read_text())
        self.on = self.wf.get("on", self.wf.get(True))

    def test_it_starts_on_both_workflows_and_only_on_main(self):
        run = self.on["workflow_run"]
        self.assertEqual(sorted(run["workflows"]),
                         sorted([HARNESS, "Pipeline Tests"]))
        self.assertEqual(run["branches"], ["main"])
        self.assertEqual(run["types"], ["completed"])

    def test_the_unit_suite_is_named_once(self):
        tests = yaml.safe_load(TESTS_WORKFLOW.read_text())
        self.assertEqual(tests["name"], promote_channel.UNIT_SUITE)
        harness = yaml.safe_load(HARNESS_WORKFLOW.read_text())
        self.assertEqual(harness["name"], HARNESS)

    def test_a_fork_pull_request_named_main_is_turned_away(self):
        condition = _flat(self.wf["jobs"]["promote"].get("if"))
        self.assertIn("workflow_run.event", condition)
        self.assertIn("pull_request", condition)
        self.assertIn("workflow_dispatch", condition)


class StepsTest(unittest.TestCase):
    def setUp(self):
        self.steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["promote"]["steps"]

    def _step(self, *, name=None, id=None):
        for step in self.steps:
            if (name and step.get("name") == name) or (id and step.get("id") == id):
                return step
        self.fail(f"no step {name or id!r}")

    def _unit_read(self):
        found = [s for s in self.steps
                 if "actions/workflows/tests.yml/runs" in str(s.get("run", ""))]
        self.assertEqual(len(found), 1, "one step reads the unit suite's run")
        return found[0]

    def test_one_step_reads_the_candidates_own_push_run_before_the_proof(self):
        step = self._unit_read()
        run = step["run"]
        self.assertIn("gh api", run)
        for query in ("head_sha=", "branch=", "event=push"):
            self.assertIn(query, run)
        # A read that fails, or finds nothing, writes `{}`.
        self.assertIn("{}", run)
        names = [s.get("name") for s in self.steps]
        self.assertLess(names.index(step["name"]),
                        names.index("Decide on the proof"))

    def test_the_proof_saves_the_unit_suite_and_who_started_it(self):
        proof = self._step(id="proof")
        run = proof["run"]
        saved = run[run.index("ARGS=(--sha"):run.index("decide-args")]
        self.assertIn("--unit-suite", saved)
        self.assertIn("--woken-by", saved)
        self.assertIn("workflow_run.name", str(proof.get("env", {})))

    def test_a_unit_suite_start_fetches_the_records_whatever_its_conclusion(self):
        for name in ("Which commit is this run about?", "Mint bot token",
                     "Fetch the candidate's combined commit status",
                     "Compare the candidate against the current channel head"):
            with self.subTest(step=name):
                self._admits_a_red_unit_suite(self._step(name=name))
        self._admits_a_red_unit_suite(self._unit_read())

    def _admits_a_red_unit_suite(self, step):
        cond = _flat(step.get("if"))
        self.assertIn("workflow_dispatch", cond)
        self.assertIn("head_branch == 'main'", cond)
        self.assertIn(f"workflow_run.name == '{promote_channel.UNIT_SUITE}'", cond)
        # The conclusion is still asked — of the harness start only, as an
        # alternative to naming the unit suite, never on top of it.
        self.assertIn("conclusion == 'success' ||", cond)

    def test_the_harness_run_is_named_only_when_the_harness_started_it(self):
        linear = self._step(name="Write the Linear release")["env"]
        for key in ("HARNESS_RUN", "HARNESS_AT"):
            value = _flat(linear[key])
            self.assertIn(f"workflow_run.name == '{HARNESS}' &&", value, key)
            self.assertTrue(value.endswith("|| '' }}"), value)
        gate = _flat(self._step(name="Where the channel stands")["env"]["GATE_RUN"])
        self.assertIn(f"workflow_run.name == '{HARNESS}' &&", gate)
        self.assertIn("workflow_run.id", gate)
        self.assertTrue(gate.endswith("|| '' }}"), gate)

    def test_the_summary_says_what_woke_the_run(self):
        say = self._step(name="Say what happened")
        self.assertIn("Woken by", say["run"])
        self.assertIn("workflow_run.name", str(say.get("env", {})))

    def test_an_unproven_suite_fails_the_run_and_a_red_or_waiting_one_does_not(self):
        last = self.steps[-1]
        gate = _flat(last.get("if"))
        self.assertIn(f"'{promote_channel.OUTCOME_MIRROR_BLOCKED}'", gate)
        self.assertIn(f"'{promote_channel.OUTCOME_UNIT_UNPROVEN}'", gate)
        self.assertNotIn(promote_channel.OUTCOME_UNIT_RED, gate)
        self.assertNotIn(promote_channel.OUTCOME_UNIT_NOT_FINISHED, gate)
        self.assertIn("::error title=", last["run"])


class DocsTest(unittest.TestCase):
    def test_a_unit_suite_run_on_main_leaves_a_receipt(self):
        doc = DOC.read_text()
        section = doc[doc.index("### Reading a channel that did not move"):]
        section = section[:section.index("|")]
        self.assertIn("`Pipeline Tests` run on `main`", _flat(section))

    def test_the_suite_is_weighed_on_every_run(self):
        flat = _flat(DOC.read_text())
        self.assertNotIn("it is weighed once the workflow hands that run over",
                         flat)
        self.assertIn("weighed on every run", flat)

    def test_the_workflow_header_names_the_unit_suite(self):
        header = WORKFLOW.read_text().split("\nname:", 1)[0]
        self.assertIn("Pipeline Tests", header)
        self.assertIn("DRE-6993", header)


if __name__ == "__main__":
    unittest.main()
