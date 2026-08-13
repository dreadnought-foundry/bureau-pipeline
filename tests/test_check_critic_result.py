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


# ---------------------------------------------------------------------------
# DRE-2422 — the max-turns exception.
#
# Portico PR #273 (2026-08-13) burned $4.05 across two critic attempts and
# produced no verdict. Both attempts ended:
#
#     "subtype": "error_max_turns", "is_error": true, "num_turns": 41
#
# `error_max_turns` is a fundamentally different animal from the auth-death
# this gate was built for. The auth death (2026-06-13, and again in the
# 2026-08-09 Fable fleet outage) is a CONTENT-FREE crash: ~634ms, 1 turn,
# $0 inference, nothing written. `error_max_turns` is the opposite — a
# genuine multi-minute review that did real work and may already have
# written its finished verdict before the ceiling cut it off.
#
# Discarding that verdict throws away a completed review AND the money that
# bought it. So the gate accepts it — but ONLY under conditions a
# content-free crash can never satisfy:
#
#   * subtype must be exactly `error_max_turns`. EVERY other is_error stays
#     hard-rejected, so the auth-death fingerprint is untouched.
#   * the run must show real work (num_turns > 1).
#   * the verdict must be COMPLETE, not mid-thought: a VERDICT: line
#     declaring one of the two legal values, plus the `## Summary` section
#     the critic prompt mandates.
#
# The write is what makes this safe to decide: the critic emits the verdict
# in ONE `Write` tool call as its closing act. A tool call is atomic — it
# either happened in full or not at all — so there is no half-written
# verdict to mistake for a finished one. A review cut off mid-thought has
# no file, or a file with no VERDICT: line; either way it is rejected below
# and the workflow retries exactly as it does today.
# ---------------------------------------------------------------------------

# The real shape of the PR #273 failure (portico run 31655143148).
MAX_TURNS_CRASH = {
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 41,
    "total_cost_usd": 2.0498711,
    "duration_ms": 502_000,
}

# The auth-death fingerprint this gate exists to stop: a dead agent that
# produced nothing, in ~634ms, for $0. Observed 2026-06-13 and again in the
# 2026-08-09 Fable fleet outage.
AUTH_DEATH_CRASH = {
    "subtype": "success",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0,
    "duration_ms": 634,
}

# What a finished critic verdict actually looks like — VERDICT line, the
# CEO-facing summary, and the technical section for the fixing agent.
COMPLETE_VERDICT = """VERDICT: REQUEST_CHANGES

## Summary
The comment actions land, but resolving a comment doesn't stick — reopen the
page and the comment looks unresolved again, so people will redo the same work.

## For the fixing agent
src/comments/resolve.ts:88 — the resolve mutation never persists `resolvedAt`.
"""

COMPLETE_APPROVE = """VERDICT: APPROVE

## Summary
The change does what the card asked: comment actions work and resolving one
keeps its state after a reload.
"""


class MaxTurnsVerdictTest(unittest.TestCase):
    """A review that hit the turn ceiling AFTER finishing is a real review."""

    # --- accept: the ceiling cut off a COMPLETED review -----------------

    def test_max_turns_with_complete_request_changes_is_real(self):
        self.assertTrue(real(MAX_TURNS_CRASH, COMPLETE_VERDICT))

    def test_max_turns_with_complete_approve_is_real(self):
        self.assertTrue(real(MAX_TURNS_CRASH, COMPLETE_APPROVE))

    # --- still reject: nothing content-free may sneak through -----------

    def test_auth_death_with_complete_verdict_is_still_not_real(self):
        """THE load-bearing guard. A dead agent that somehow has a perfect
        verdict file next to it must never be read as a review — this is
        what stopped a crashed Fable critic from approving code during the
        2026-08-09 fleet outage. Widening the gate for max-turns must not
        widen it by so much as an inch here."""
        self.assertFalse(real(AUTH_DEATH_CRASH, COMPLETE_VERDICT))
        self.assertFalse(real(AUTH_DEATH_CRASH, COMPLETE_APPROVE))

    def test_other_is_error_subtypes_with_complete_verdict_are_not_real(self):
        for subtype in ("error_during_execution", "error", "success", None):
            payload = {"is_error": True, "num_turns": 30}
            if subtype is not None:
                payload["subtype"] = subtype
            with self.subTest(subtype=subtype):
                self.assertFalse(real(payload, COMPLETE_APPROVE))

    def test_max_turns_with_no_verdict_file_is_not_real(self):
        """PR #273's actual state: the ceiling hit mid-review, nothing
        written. Must still retry, exactly as today."""
        self.assertFalse(real(MAX_TURNS_CRASH, verdict_text=None))

    def test_max_turns_with_empty_verdict_is_not_real(self):
        self.assertFalse(real(MAX_TURNS_CRASH, ""))

    def test_max_turns_stopped_mid_thought_is_not_real(self):
        """Prose with no VERDICT: line — the review was still thinking."""
        self.assertFalse(
            real(MAX_TURNS_CRASH,
                 "## Notes so far\nStill checking whether the tests are real")
        )

    def test_max_turns_verdict_line_without_summary_is_not_real(self):
        """A bare VERDICT: line with none of the mandated body is not a
        finished verdict — it is a review that stopped mid-thought."""
        self.assertFalse(real(MAX_TURNS_CRASH, "VERDICT: APPROVE\n"))

    def test_max_turns_with_undeclared_verdict_value_is_not_real(self):
        """`VERDICT:` must resolve to one of the two legal decisions. A
        trailing-off or hedged marker is not a decision."""
        for line in ("VERDICT: ", "VERDICT: MAYBE", "VERDICT: APPROV"):
            with self.subTest(line=line):
                self.assertFalse(
                    real(MAX_TURNS_CRASH, f"{line}\n\n## Summary\nlooks ok")
                )

    def test_max_turns_with_no_real_work_is_not_real(self):
        """Belt and braces: a one-turn run did no review, whatever it
        labels itself."""
        self.assertFalse(
            real({"subtype": "error_max_turns", "is_error": True,
                  "num_turns": 1}, COMPLETE_APPROVE)
        )

    # --- the clean path is untouched ------------------------------------

    def test_clean_run_still_needs_only_a_verdict_line(self):
        """The max-turns exception adds a STRICTER bar on the crash path
        only. A healthy run keeps today's contract — no new way to reject a
        genuine review, which is the false-reject class DRE-1330 opened."""
        self.assertTrue(real({"is_error": False}, "VERDICT: APPROVE\nfine"))


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

    def test_cli_exit_0_on_max_turns_with_complete_verdict(self):
        """End to end: the workflow step must see exit 0 and post the
        verdict rather than burning a second $2 attempt (DRE-2422)."""
        p = self._run(MAX_TURNS_CRASH, COMPLETE_VERDICT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_cli_exit_1_on_auth_death_even_with_complete_verdict(self):
        p = self._run(AUTH_DEATH_CRASH, COMPLETE_VERDICT)
        self.assertEqual(p.returncode, 1)
        self.assertIn("is_error", p.stdout + p.stderr)

    def test_cli_exit_1_on_max_turns_with_no_verdict(self):
        p = self._run(MAX_TURNS_CRASH, verdict_text=None)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main()
