"""RED-first tests for the critic-result gate (mirror of DRE-1346 Fix 1).

Origin (2026-06-13): the QA critic runs claude-code-action then posts a
plain-English verdict. When that step CRASHES — the execution result ends
{"is_error": true} (auth/startup death, ~340ms / 1 turn / $0 inference) —
or writes NO verdict file, qa-review.yml previously FAIL-CLOSED and posted a
REQUEST_CHANGES verdict with no real findings. That false reject churned good
PRs into the fix loop and spawned duplicate-PR cycles (PRs #1441/#1442,
DRE-1330/1332, 2026-06-13).

The fix mirrors DRE-1346: a critic CRASH must NOT yield a real verdict. The
gate parses the execution output + the verdict artifact; is_error OR a
missing/empty/malformed verdict means the review did NOT really run, so the
workflow retries once and, if still dead, posts a NEUTRAL status (not
REQUEST_CHANGES) and fails the job loudly (medic-visible). A real verdict —
APPROVE *or* REQUEST_CHANGES with findings — only ever comes from a genuine
review (is_error=false AND a valid VERDICT: line written).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_critic_result  # noqa: E402


def real(execution=None, verdict_text=None):
    """True iff a genuine review ran. verdict_text=None means no file."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "qa-verdict.md")
        if verdict_text is not None:
            with open(path, "w") as f:
                f.write(verdict_text)
        return check_critic_result.verdict_is_real(execution, path)


class VerdictIsRealTest(unittest.TestCase):
    # --- crash / no-verdict conditions: NOT a real review ---------------

    def test_is_error_true_is_not_real_even_with_verdict_present(self):
        # A crashed run that somehow left a stale verdict file is still a
        # crash — the execution result is authoritative.
        self.assertFalse(
            real({"subtype": "success", "is_error": True},
                 "VERDICT: APPROVE\nlooks fine")
        )

    def test_missing_verdict_file_is_not_real(self):
        self.assertFalse(real({"is_error": False}, verdict_text=None))

    def test_empty_verdict_file_is_not_real(self):
        self.assertFalse(real({"is_error": False}, verdict_text=""))

    def test_whitespace_only_verdict_is_not_real(self):
        self.assertFalse(real({"is_error": False}, verdict_text="   \n  \n"))

    def test_verdict_without_verdict_line_is_not_real(self):
        # Truncated / malformed output that never declared a verdict.
        self.assertFalse(
            real({"is_error": False}, "the code seems okay to me overall")
        )

    def test_missing_execution_file_with_no_verdict_is_not_real(self):
        self.assertFalse(real(None, verdict_text=None))

    # --- genuine reviews: REAL, must pass through unchanged -------------

    def test_clean_approve_is_real(self):
        self.assertTrue(
            real({"is_error": False},
                 "VERDICT: APPROVE\nThe change does what the card asks.")
        )

    def test_clean_request_changes_is_real(self):
        # The critical guard: a genuine rejection must NOT be downgraded to
        # neutral. is_error=false + valid VERDICT line == real review.
        self.assertTrue(
            real({"is_error": False},
                 "VERDICT: REQUEST_CHANGES\nTests are vacuous.\n"
                 "## For the fixing agent\nfoo.py:10 assert is tautological")
        )

    def test_missing_execution_file_but_valid_verdict_is_real(self):
        # Action versions move the execution file around; if a valid verdict
        # was actually written, the review ran. Absence of the result file
        # alone must not nuke a genuine verdict.
        self.assertTrue(real(None, "VERDICT: APPROVE\nfine"))

    def test_verdict_line_not_required_on_first_line(self):
        # Tolerate a leading blank line before the VERDICT marker.
        self.assertTrue(
            real({"is_error": False}, "\nVERDICT: APPROVE\nfine")
        )


class CliTest(unittest.TestCase):
    """CLI: exit 0 == real verdict (post it); exit 1 == crash/no-verdict
    (caller must retry, then neutral+fail)."""

    def _run(self, payload, verdict_text=None):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            verdict_path = os.path.join(td, "qa-verdict.md")
            if payload is not None:
                with open(exec_path, "w") as f:
                    json.dump(payload, f)
            if verdict_text is not None:
                with open(verdict_path, "w") as f:
                    f.write(verdict_text)
            return subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(__file__), "..", "scripts",
                              "check_critic_result.py"),
                 exec_path, verdict_path],
                capture_output=True, text=True,
            )

    def test_cli_exit_1_on_is_error(self):
        p = self._run({"is_error": True}, "VERDICT: APPROVE\nok")
        self.assertEqual(p.returncode, 1)
        self.assertIn("is_error", p.stdout + p.stderr)

    def test_cli_exit_1_on_missing_verdict(self):
        p = self._run({"is_error": False}, verdict_text=None)
        self.assertEqual(p.returncode, 1)

    def test_cli_exit_0_on_real_approve(self):
        p = self._run({"is_error": False}, "VERDICT: APPROVE\nok")
        self.assertEqual(p.returncode, 0)

    def test_cli_exit_0_on_real_request_changes(self):
        p = self._run({"is_error": False}, "VERDICT: REQUEST_CHANGES\nbad")
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
