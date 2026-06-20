"""RED-first tests for the agent-result gate (DRE-1346 Fix 1).

Origin (2026-06-12): the Claude execution result can end
{"subtype": "success", "is_error": true} (usage-limit/API death mid-run).
The workflow reported success, so the medic's failed-run path never fired
and dead cards sat as zombies behind a green conclusion. The gate parses
the execution output after the agent step: is_error OR (no agent branch
AND no PR AND no blocker note) fails the job loudly. An honest blocker
note is exempt — that path is working as designed.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_agent_result  # noqa: E402


NO_EVIDENCE = "no agent branch, no PR, no blocker note, and no escalation note"


def verdict(execution=None, branch=False, pr=False, blocker=False, escalation=False):
    return check_agent_result.failure_reason(
        execution,
        branch_exists=branch,
        pr_exists=pr,
        blocker_note=blocker,
        escalation_note=escalation,
    )


class FailureReasonTest(unittest.TestCase):
    def test_is_error_true_fails_even_with_success_subtype(self):
        self.assertEqual(
            verdict({"subtype": "success", "is_error": True}, branch=True, pr=True),
            "execution result has is_error=true",
        )

    def test_no_branch_no_pr_no_blocker_fails(self):
        self.assertEqual(verdict({"is_error": False}), NO_EVIDENCE)

    def test_honest_blocker_is_exempt(self):
        self.assertIsNone(verdict({"is_error": False}, blocker=True))

    def test_honest_escalation_is_exempt(self):
        # DRE-1655: the agent intentionally stopped to ask the CEO a decision —
        # an honest, designed outcome (→ Plan Review), not a silent death.
        self.assertIsNone(verdict({"is_error": False}, escalation=True))

    def test_clean_success_with_pr_passes(self):
        self.assertIsNone(verdict({"is_error": False}, branch=True, pr=True))

    def test_branch_without_pr_yet_passes_the_gate(self):
        # The wrap-up step owns PR detection/requeue; a pushed branch means
        # the agent did real work — not this gate's failure to report.
        self.assertIsNone(verdict({"is_error": False}, branch=True))

    def test_missing_or_unparseable_execution_file_does_not_fail_alone(self):
        # Action versions move the file around; absence of the result file
        # must not fail an otherwise-evidenced run (branch/PR present).
        self.assertIsNone(verdict(None, branch=True, pr=True))

    def test_missing_execution_file_with_no_evidence_still_fails(self):
        self.assertEqual(verdict(None), NO_EVIDENCE)


class IgnoreIsErrorTest(unittest.TestCase):
    """DRE-1354: the agent-task gate no longer hard-fails on is_error (the Report
    step owns that path now), but a no-evidence silent death still fails."""

    def test_is_error_ignored_when_flag_set(self):
        self.assertIsNone(
            check_agent_result.failure_reason(
                {"is_error": True}, branch_exists=True, ignore_is_error=True
            )
        )

    def test_is_error_still_fails_by_default(self):
        self.assertEqual(
            check_agent_result.failure_reason(
                {"is_error": True}, branch_exists=True
            ),
            "execution result has is_error=true",
        )

    def test_no_evidence_still_fails_even_when_ignoring_is_error(self):
        self.assertEqual(
            check_agent_result.failure_reason(
                {"is_error": True}, branch_exists=False, ignore_is_error=True
            ),
            NO_EVIDENCE,
        )

    def test_is_error_death_helper(self):
        self.assertTrue(check_agent_result.is_error_death({"is_error": True}))
        self.assertFalse(check_agent_result.is_error_death({"is_error": False}))
        self.assertFalse(check_agent_result.is_error_death(None))


class CliTest(unittest.TestCase):
    def _run(self, payload, branch="", pr="", blocker_file="", extra=()):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            if payload is not None:
                with open(exec_path, "w") as f:
                    json.dump(payload, f)
            env = {**os.environ, "PATH": os.environ["PATH"]}
            return subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(__file__), "..", "scripts",
                              "check_agent_result.py"),
                 exec_path, branch, pr, blocker_file, *extra],
                capture_output=True, text=True, env=env,
            )

    def test_cli_ignore_is_error_flag_exits_0_with_branch(self):
        p = self._run({"is_error": True}, branch="agent/DRE-1",
                      extra=("--ignore-is-error",))
        self.assertEqual(p.returncode, 0)

    def test_cli_exit_1_on_is_error(self):
        p = self._run({"is_error": True}, branch="agent/DRE-1", pr="http://pr")
        self.assertEqual(p.returncode, 1)
        self.assertIn("is_error", p.stdout + p.stderr)

    def test_cli_exit_0_on_clean_run(self):
        p = self._run({"is_error": False}, branch="agent/DRE-1", pr="http://pr")
        self.assertEqual(p.returncode, 0)

    def test_cli_exit_0_on_blocker_note(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("blocked: spec ambiguous")
            blocker = f.name
        try:
            p = self._run({"is_error": False}, blocker_file=blocker)
            self.assertEqual(p.returncode, 0)
        finally:
            os.unlink(blocker)

    def test_cli_exit_0_on_escalation_note(self):
        # DRE-1655: an escalation file (agent stopped to ask the CEO) keeps the
        # gate green with no branch/PR — like a blocker note.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("Should free-tier users see X, or only paid?")
            esc = f.name
        try:
            p = self._run({"is_error": False},
                          extra=("--escalation-file", esc))
            self.assertEqual(p.returncode, 0)
        finally:
            os.unlink(esc)

    def test_cli_empty_escalation_file_does_not_exempt(self):
        # An empty escalation file is not a real escalation — no evidence ⇒ fail.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            esc = f.name  # zero bytes
        try:
            p = self._run({"is_error": False},
                          extra=("--escalation-file", esc))
            self.assertEqual(p.returncode, 1)
        finally:
            os.unlink(esc)


if __name__ == "__main__":
    unittest.main()
