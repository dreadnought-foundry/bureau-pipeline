"""DRE-2465, end to end: the notice the operator reads must match the run.

portico PR #297, 2026-08-15. Four critic executions, all `is_error: false`,
117 turns, $12.40 billed. The pull request was told, four times:

    The adversarial reviewer crashed twice (startup/auth failure, no
    inference). No findings were produced.

That sentence is hardcoded in an `else` branch gated only on `REAL != true`,
so it is what a PR gets for EVERY way a verdict can go missing. The operator
believed it — the notice had been literally true five times in the preceding
72 hours — rotated a healthy token twice, and lost a day.

These tests execute the REAL run block out of qa-review.yml, fed by the REAL
gate script, exactly as the workflow wires them: an execution file plus the
step outputs the gate writes from it. Nothing about the message is asserted
by grepping YAML — the shell either produces the right words or it does not.

Two rules bound the rewording and both are load-bearing for merges:
  * the comment must keep `QA Critic`, or merge-gate stops treating it as the
    latest verdict and a stale APPROVE survives;
  * it must never contain `VERDICT: APPROVE`, or the neutral status IS a
    merge credential.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
GATE = ROOT / "scripts" / "check_critic_result.py"

# The run the operator was told never happened (attempt 2's retry).
RAN_NO_VERDICT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 25,
    "total_cost_usd": 3.3887,
    "duration_ms": 203000,
    "permission_denials_count": 0,
    "result": "I reviewed the diff.",
}

# The death the current wording was written for, and still describes.
AUTH_DEATH = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 634,
    "num_turns": 1,
    "total_cost_usd": 0,
    "result": "API Error: 401 authentication_error",
}


def _post_step_run() -> str:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    for step in doc["jobs"]["review"]["steps"]:
        if step.get("id") == "post":
            return step["run"]
    raise AssertionError("qa-review.yml has no step with id 'post'")


def _resolve_expressions(run: str) -> str:
    for expr, value in {
        "github.repository": "dreadnought-foundry/portico",
        "github.run_id": "31891083751",
    }.items():
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    return run


def gate_outputs(td: Path, execution: dict | None, verdict_text=None) -> dict:
    """Run the real gate the way the workflow does, and read its outputs."""
    exec_path = td / "claude-execution-output.json"
    verdict_path = td / "qa-verdict.md"
    if execution is not None:
        exec_path.write_text(json.dumps(execution))
    if verdict_text is not None:
        verdict_path.write_text(verdict_text)
    out_path = td / "github_output"
    out_path.touch()
    subprocess.run(
        [sys.executable, str(GATE), str(exec_path), str(verdict_path),
         "--github-output", str(out_path)],
        capture_output=True, text=True, check=False,
    )
    parsed = {}
    for line in out_path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return parsed


def run_post(td: Path, gate2: dict, *, gate1: dict | None = None,
             real: str = "false") -> tuple[subprocess.CompletedProcess, str]:
    """Execute the post step's shell with the gate's own outputs as env."""
    bin_dir = td / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\nexit 0\n")
    gh.chmod(0o755)

    run = _resolve_expressions(_post_step_run())
    assert "${{" not in run, "an unresolved GitHub expression reached the shell"
    run = run.replace("/tmp/", str(td) + "/")
    script = td / "post.sh"
    script.write_text("set -euo pipefail\n" + run)

    gate1 = gate1 if gate1 is not None else gate2
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update({
        "CARD": "", "REAL": real, "PR": "297",
        "REVIEWED_SHA": "a" * 40, "CONTENT_ID": "",
        "MODEL_ID": "claude-sonnet-5", "MODEL_WHY": "advisory ladder top",
        # Exactly what the workflow interpolates from the two gate steps.
        "A1_OUTCOME": gate1.get("outcome", ""),
        "A1_TURNS": gate1.get("turns", ""),
        "A1_COST": gate1.get("cost", ""),
        "A2_OUTCOME": gate2.get("outcome", ""),
        "A2_TURNS": gate2.get("turns", ""),
        "A2_COST": gate2.get("cost", ""),
    })
    proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                          capture_output=True, text=True)
    comment = (td / "qa-comment.md")
    return proc, comment.read_text() if comment.exists() else ""


def comment_for(execution, **kwargs) -> str:
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        gate2 = gate_outputs(td, execution)
        proc, body = run_post(td, gate2, **kwargs)
        assert proc.returncode == 0, proc.stderr
        return body


class SuccessShapedFailureGetsItsOwnMessageTest(unittest.TestCase):
    """is_error: false + no verdict file — the #297 case, end to end."""

    def test_it_does_not_claim_a_crash_or_an_auth_failure(self):
        body = comment_for(RAN_NO_VERDICT)
        for false_claim in ("crashed", "startup/auth failure", "no inference"):
            self.assertNotIn(false_claim, body,
                             f"the notice still says {false_claim!r} about a "
                             f"run that completed 25 turns for $3.39")

    def test_it_states_the_turns_and_the_cost(self):
        body = comment_for(RAN_NO_VERDICT)
        self.assertIn("25", body)
        self.assertIn("3.39", body)

    def test_it_says_out_loud_that_this_is_not_an_auth_failure(self):
        # The operator rotated a healthy token twice. The notice has to stop
        # him doing it a third time.
        body = comment_for(RAN_NO_VERDICT).lower()
        self.assertIn("not an authentication", body)

    def test_both_completed_attempts_are_accounted_for(self):
        # #297 spent $12.40 across four executions and the operator was told
        # about none of them. One attempt's numbers is not the whole bill.
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            first = {"outcome": "completed_no_verdict", "turns": "29",
                     "cost": "4.21"}
            second = {"outcome": "completed_no_verdict", "turns": "42",
                      "cost": "3.35"}
            proc, body = run_post(td, second, gate1=first)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for number in ("29", "4.21", "42", "3.35"):
                self.assertIn(number, body,
                              f"attempt numbers {number} went unreported")

    def test_a_completed_first_attempt_counts_even_if_the_retry_did_not(self):
        # One clean execution is enough to disprove "it never ran".
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            ran = gate_outputs(td, RAN_NO_VERDICT)
            (td / "claude-execution-output.json").unlink()
            (td / "github_output").unlink()
            crashed = gate_outputs(td, AUTH_DEATH)
            proc, body = run_post(td, crashed, gate1=ran)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("startup/auth failure", body)
            self.assertIn("25", body)


class TheCrashWordingIsUntouchedTest(unittest.TestCase):
    """DRE-2282 quotes this sentence as evidence of a genuine auth death. It
    must still be exactly what a genuine auth death produces."""

    def test_a_real_crash_keeps_the_current_wording(self):
        body = comment_for(AUTH_DEATH)
        self.assertIn("could not run (infra error)", body)
        self.assertIn("crashed twice (startup/auth failure, no inference)",
                      body)

    def test_no_execution_record_at_all_keeps_the_crash_wording(self):
        # We cannot prove it ran, so we must not say it did.
        body = comment_for(None)
        self.assertIn("crashed twice (startup/auth failure, no inference)",
                      body)


class MergeGateContractTest(unittest.TestCase):
    """The two rules the rewording may not break, pinned on EVERY branch."""

    def _bodies(self) -> list[str]:
        return [comment_for(RAN_NO_VERDICT), comment_for(AUTH_DEATH),
                comment_for(None)]

    def test_every_neutral_comment_carries_the_qa_critic_marker(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertIn("QA Critic", body)
                self.assertIn("QA Critic", body.splitlines()[0])

    def test_no_neutral_comment_can_be_read_as_an_approval(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertNotIn("VERDICT: APPROVE", body)

    def test_every_neutral_comment_still_says_it_is_not_a_rejection(self):
        # merge-gate holds; the fix loop must not be triggered by this.
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertIn("not a code rejection", body.lower())
                self.assertIn("not a request for changes", body.lower())


class TheNumbersAreDataNotShellTest(unittest.TestCase):
    """The gate's outputs reach a double-quoted shell string, same as the
    model note (tests/test_qa_review_model_note.py). They are numbers by
    construction; this proves nothing executes if that ever stops being
    true."""

    def test_a_hostile_turn_count_is_never_executed(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            marker = td / "PWNED"
            gate2 = {"outcome": "completed_no_verdict",
                     "turns": f"$(touch {marker})`touch {marker}`",
                     "cost": "3.39"}
            proc, body = run_post(td, gate2)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists(),
                             "a gate output was executed as shell")
            self.assertIn("QA Critic", body)


class BothGateStepsAreWiredTest(unittest.TestCase):
    """The attempt-1 and retry gate steps are deliberate duplicates. A change
    to one that misses the other is how the retry stops reporting."""

    def test_both_gate_steps_pass_github_output_to_the_script(self):
        doc = yaml.safe_load(QA_REVIEW.read_text())
        steps = {s.get("id"): s for s in doc["jobs"]["review"]["steps"]}
        for step_id in ("gate1", "gate2"):
            run = steps[step_id]["run"]
            self.assertIn("check_critic_result.py", run)
            self.assertIn("--github-output", run,
                          f"{step_id} does not ask the gate for its outcome")

    def test_the_post_step_reads_both_attempts(self):
        doc = yaml.safe_load(QA_REVIEW.read_text())
        steps = {s.get("id"): s for s in doc["jobs"]["review"]["steps"]}
        env = steps["post"]["env"]
        self.assertIn("gate1.outputs.outcome", env["A1_OUTCOME"])
        self.assertIn("gate2.outputs.outcome", env["A2_OUTCOME"])


if __name__ == "__main__":
    unittest.main()
