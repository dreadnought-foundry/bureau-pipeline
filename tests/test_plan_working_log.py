"""plan.yml names the model step whose transcript its working log is (DRE-5350).

`plan.yml` is one job that runs many model steps and keeps ONE working log.
Every one of those steps uses `anthropics/claude-code-action`, and the action
writes every step's transcript to one fixed path —
`$RUNNER_TEMP/claude-execution-output.json`, `EXECUTION_FILENAME` in its
`base-action/src/execution-file.ts`, with no step id in the name. So the file
the upload keeps is always the LAST model step's transcript, whatever
expression reads it. What was missing is WHICH step that is, and the effort
that step was given: the steps take their effort from several different
model-selection steps, so no single `effort_arg` is true for the job.

The `kept` step resolves it. Its `env:` carries every model step's outcome and
the `effort_arg` its own `claude_args` reads; its body walks the steps from the
end and names the last one that ran. A step that succeeded hands its effort to
the upload. A step that failed is named, but stamps no effort: a step that dies
before the agent starts writes no file, and the one on disk is an earlier
step's — `outcome` cannot tell the two failures apart, and the contract says
never a guessed value.

WHAT THIS FILE PINS:

  * THE WIRING, read off the workflow rather than restated. The model steps
    and the selection step each one reads are collected from the file, so a
    card that adds, moves or removes a model step goes red here with the one
    change it owes named.
  * THE BEHAVIOUR of the step's `run:` body, executed under `bash -e` with a
    fixed environment.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / ".github" / "workflows" / "plan.yml"

JOB = "plan"
KEPT_ID = "kept"
UPLOAD_CALL = "upload_agent_log.py"
ACTION = "anthropics/claude-code-action"

#: The `effort_arg` a model step's own `claude_args` reads.
EFFORT_READ = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.effort_arg")


def _steps():
    doc = yaml.safe_load(PLAN.read_text())
    return doc["jobs"][JOB]["steps"]


def _model_steps(steps):
    """(id, selection step) for every model step, in workflow order."""
    found = []
    for step in steps:
        if not str(step.get("uses") or "").startswith(ACTION):
            continue
        args = str((step.get("with") or {}).get("claude_args") or "")
        sels = EFFORT_READ.findall(args)
        found.append((step.get("id"), sels))
    return found


def _kept_step(steps):
    hits = [s for s in steps if s.get("id") == KEPT_ID]
    return hits[0] if len(hits) == 1 else None


def _upload_step(steps):
    hits = [s for s in steps if UPLOAD_CALL in str(s.get("run") or "")]
    return hits[0] if len(hits) == 1 else None


def _index(steps, step):
    return next(i for i, s in enumerate(steps) if s is step)


class WiringTest(unittest.TestCase):
    """The `kept` step names exactly the model steps the workflow holds."""

    @classmethod
    def setUpClass(cls):
        cls.steps = _steps()
        cls.models = _model_steps(cls.steps)
        cls.kept = _kept_step(cls.steps)

    def _kept(self):
        self.assertIsNotNone(
            self.kept,
            f"plan.yml [{JOB}] must carry exactly one step `id: {KEPT_ID}` — "
            f"`Which model step's transcript the run kept` — before "
            f"`Keep the run's working log`",
        )
        return self.kept

    def test_the_discovery_is_not_vacuous(self):
        self.assertGreater(len(self.models), 1)
        for sid, _ in self.models:
            self.assertTrue(sid, "every model step needs an `id:` to be named")

    def test_every_model_step_reads_exactly_one_effort(self):
        for sid, sels in self.models:
            with self.subTest(step=sid):
                self.assertEqual(
                    len(sels), 1,
                    f"model step `{sid}` must read exactly one "
                    f"`steps.<sel>.outputs.effort_arg` in its `claude_args`; "
                    f"found {sels}",
                )

    def test_every_model_steps_outcome_is_carried(self):
        env = self._kept().get("env") or {}
        want = {f"OUTCOME_{sid}": f"${{{{ steps.{sid}.outcome }}}}"
                for sid, _ in self.models}
        have = {k: v for k, v in env.items() if k.startswith("OUTCOME_")}
        for key in sorted(set(want) - set(have)):
            self.fail(f"the `{KEPT_ID}` step is missing model step "
                      f"`{key[len('OUTCOME_'):]}`: add its "
                      f"`OUTCOME_<id>`/`EFFORT_<id>` pair and its place in "
                      f"`ORDER`")
        for key in sorted(set(have) - set(want)):
            self.fail(f"the `{KEPT_ID}` step names `{key[len('OUTCOME_'):]}`, "
                      f"which is not a model step: remove its "
                      f"`OUTCOME_<id>`/`EFFORT_<id>` pair and its place in "
                      f"`ORDER`")
        for key, value in want.items():
            with self.subTest(env=key):
                self.assertEqual(str(have[key]).strip(), value)

    def test_every_model_steps_own_effort_is_carried(self):
        env = self._kept().get("env") or {}
        want = {f"EFFORT_{sid}": f"${{{{ steps.{sels[0]}.outputs.effort_arg }}}}"
                for sid, sels in self.models if len(sels) == 1}
        have = {k: v for k, v in env.items() if k.startswith("EFFORT_")}
        for key in sorted(set(want) - set(have)):
            self.fail(f"the `{KEPT_ID}` step is missing model step "
                      f"`{key[len('EFFORT_'):]}`: add its "
                      f"`OUTCOME_<id>`/`EFFORT_<id>` pair and its place in "
                      f"`ORDER`")
        for key in sorted(set(have) - set(want)):
            self.fail(f"the `{KEPT_ID}` step names `{key[len('EFFORT_'):]}`, "
                      f"which is not a model step: remove its "
                      f"`OUTCOME_<id>`/`EFFORT_<id>` pair and its place in "
                      f"`ORDER`")
        for key, value in want.items():
            with self.subTest(env=key):
                self.assertEqual(
                    str(have[key]).strip(), value,
                    f"`{key}` must read the selection step its model step's "
                    f"own `claude_args` reads",
                )

    def test_order_names_the_model_steps_in_workflow_order(self):
        env = self._kept().get("env") or {}
        self.assertEqual(
            str(env.get("ORDER") or "").split(),
            [sid for sid, _ in self.models],
            f"`ORDER` on the `{KEPT_ID}` step must list every model step id, "
            f"in workflow order — add or remove the step's place in it",
        )

    def test_the_step_sits_after_the_last_model_step_and_before_the_upload(self):
        kept = self._kept()
        upload = _upload_step(self.steps)
        self.assertIsNotNone(upload)
        last_model = max(i for i, s in enumerate(self.steps)
                         if str(s.get("uses") or "").startswith(ACTION))
        at = _index(self.steps, kept)
        self.assertGreater(at, last_model)
        self.assertLess(at, _index(self.steps, upload))

    def test_the_step_runs_however_the_run_ended(self):
        self.assertIn("always()", str(self._kept().get("if") or ""))

    def test_the_step_interpolates_nothing_inside_its_run_body(self):
        """DRE-3484: every `${{ }}` lives in `env:`."""
        self.assertNotIn("${{", str(self._kept().get("run") or ""))

    def test_the_upload_is_handed_the_kept_steps_effort(self):
        upload = _upload_step(self.steps)
        self.assertIsNotNone(upload)
        env = upload.get("env") or {}
        self.assertEqual(
            str(env.get("EFFORT_ARG") or "").strip(),
            f"${{{{ steps.{KEPT_ID}.outputs.effort_arg }}}}",
        )


class BodyTest(unittest.TestCase):
    """The `kept` step's `run:` body, executed under a fixed environment."""

    @classmethod
    def setUpClass(cls):
        steps = _steps()
        cls.ids = [sid for sid, _ in _model_steps(steps)]
        kept = _kept_step(steps)
        cls.body = str(kept.get("run") or "") if kept else None

    def setUp(self):
        if self.body is None:
            self.fail(f"plan.yml [{JOB}] has no `{KEPT_ID}` step to run")

    def _run(self, outcomes, efforts=None, order=None):
        """Run the body; returns (outputs dict, output lines, summary)."""
        efforts = efforts or {}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output"
            summary = Path(tmp) / "summary"
            out.touch()
            summary.touch()
            env = {"PATH": os.environ["PATH"],
                   "GITHUB_OUTPUT": str(out),
                   "GITHUB_STEP_SUMMARY": str(summary),
                   "ORDER": " ".join(order or self.ids)}
            for sid in order or self.ids:
                env[f"OUTCOME_{sid}"] = outcomes.get(sid, "skipped")
                env[f"EFFORT_{sid}"] = efforts.get(sid, "")
            result = subprocess.run(["bash", "-e", "-c", self.body],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [ln for ln in out.read_text().splitlines() if ln]
            outputs = dict(ln.split("=", 1) for ln in lines)
            return outputs, lines, summary.read_text()

    def test_the_last_step_that_ran_wins(self):
        first, middle, last = self.ids[0], self.ids[1], self.ids[-1]
        outputs, _, _ = self._run(
            {first: "success", middle: "success"},
            {first: "--effort low", middle: "--effort high",
             last: "--effort max"})
        self.assertEqual(outputs.get("step_id"), middle)

    def test_a_succeeded_step_carries_its_own_effort(self):
        a, b = self.ids[0], self.ids[1]
        outputs, _, summary = self._run(
            {a: "success", b: "success"},
            {a: "--effort low", b: "--effort xhigh"})
        self.assertEqual(outputs.get("step_id"), b)
        self.assertEqual(outputs.get("effort_arg"), "--effort xhigh")
        self.assertEqual(outputs.get("certain"), "true")
        self.assertIn(f"kept model step: {b} --effort xhigh", summary)

    def test_a_failed_step_is_named_with_no_effort(self):
        for outcome in ("failure", "cancelled"):
            with self.subTest(outcome=outcome):
                a, b = self.ids[0], self.ids[1]
                outputs, _, summary = self._run(
                    {a: "success", b: outcome},
                    {a: "--effort low", b: "--effort high"})
                self.assertEqual(outputs.get("step_id"), b)
                self.assertEqual(outputs.get("certain"), "false")
                self.assertNotIn("effort_arg", outputs,
                                 "a failed step's transcript ownership cannot "
                                 "be confirmed, so no effort is stamped")
                self.assertIn(
                    f"kept model step: {b} (failed — transcript ownership "
                    f"unconfirmed, no effort stamped)", summary)

    def test_a_later_step_with_no_effort_never_borrows_an_earlier_ones(self):
        a, b = self.ids[0], self.ids[1]
        outputs, _, summary = self._run(
            {a: "success", b: "success"},
            {a: "--effort high", b: ""})
        self.assertEqual(outputs.get("step_id"), b)
        self.assertEqual(outputs.get("effort_arg"), "")
        self.assertEqual(outputs.get("certain"), "true")
        self.assertIn(f"kept model step: {b} (no effort)", summary)

    def test_an_empty_outcome_counts_as_not_run(self):
        a, b = self.ids[0], self.ids[1]
        outputs, _, _ = self._run({a: "success", b: ""},
                                  {a: "--effort low", b: "--effort max"})
        self.assertEqual(outputs.get("step_id"), a)
        self.assertEqual(outputs.get("effort_arg"), "--effort low")

    def test_no_model_step_ran_writes_nothing(self):
        outputs, lines, summary = self._run(
            {}, {sid: "--effort high" for sid in self.ids})
        self.assertEqual(lines, [])
        self.assertEqual(summary.strip(), "")

    def test_the_step_writes_one_summary_line_naming_the_winner(self):
        last = self.ids[-1]
        _, _, summary = self._run({last: "success"}, {last: "--effort medium"})
        self.assertEqual(summary.strip().splitlines(),
                         [f"kept model step: {last} --effort medium"])

    # ---- the two runs the card names, by their real step ids ----------------

    def _need(self, *ids):
        missing = [i for i in ids if i not in self.ids]
        if missing:
            self.skipTest(f"plan.yml no longer holds {missing}")

    def test_a_healthy_plan_route_run_names_the_first_critic(self):
        self._need("claude", "prea")
        outputs, _, _ = self._run(
            {"claude": "success", "prea": "success"},
            {"claude": "--effort high", "prea": "--effort xhigh"})
        self.assertEqual(outputs.get("step_id"), "prea")
        self.assertEqual(outputs.get("effort_arg"), "--effort xhigh")

    def test_a_run_that_dies_in_posta_names_posta_with_no_effort(self):
        self._need("posta")
        outputs, _, _ = self._run(
            {"posta": "failure"}, {"posta": "--effort high"})
        self.assertEqual(outputs.get("step_id"), "posta")
        self.assertNotIn("effort_arg", outputs)


if __name__ == "__main__":
    unittest.main()
