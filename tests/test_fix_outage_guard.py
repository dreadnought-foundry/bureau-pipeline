"""RED-first tests for the agent-fix outage guard (DRE-2018).

Origin (2026-07-10, DeltaSolv token-outage post-incident): when the fix
agent's model died mid-run ({"is_error": true} — API outage, exhausted
subscription), agent-fix's no-progress guard saw only "no new commit" and
posted "🛑 Fix attempt N pushed no new commit", parking the card
needs-human/Plan Review — an escalation that blames the fix agent and
misleads the CEO's queue. The agent-task path already distinguishes
model-death (check_agent_result + dead_run) and requeues; agent-fix did not.

The fix, pinned here:
  * scripts/fix_run_outage.py decides from the execution-result JSON:
      escalate — model RAN and pushed nothing → today's park behavior;
      retry    — model DIED under the outage cap → retry-friendly comment,
                 no park, no fix-attempt burned;
      hold     — outages repeated past the cap → park with a comment that
                 blames the OUTAGE, not the work.
  * agent-fix.yml's Report step routes its no-progress branch through that
    decision (worker-bot-authored outage markers only — DRE-1995).
  * reconcile.py gains the re-dispatch sweep: the latest worker-bot fix-loop
    status comment being an outage-retry marker → dispatch agent-fix.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_run_outage  # noqa: E402
import reconcile  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
IS_ERROR_FIXTURE = os.path.join(FIXTURES, "claude-execution-is-error.json")
CLEAN_FIXTURE = os.path.join(FIXTURES, "claude-execution-clean.json")
WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)

IS_ERROR_EXEC = {"subtype": "success", "is_error": True}
CLEAN_EXEC = {"subtype": "success", "is_error": False}


class DecideTest(unittest.TestCase):
    """fix_run_outage.decide(execution, prior_outages, ...) -> Decision"""

    def test_clean_run_escalates_like_today(self):
        # The model RAN and pushed nothing — genuine no-progress keeps
        # today's park-for-human behavior (the workflow's own shell).
        d = fix_run_outage.decide(CLEAN_EXEC, 0, pr_number="7")
        self.assertEqual(d.action, "escalate")

    def test_missing_execution_result_escalates_fail_safe(self):
        # Can't prove an outage → don't hide a genuine no-progress behind one.
        d = fix_run_outage.decide(None, 0, pr_number="7")
        self.assertEqual(d.action, "escalate")

    def test_is_error_death_retries_under_the_cap(self):
        for prior in range(fix_run_outage.RETRY_CAP):
            d = fix_run_outage.decide(IS_ERROR_EXEC, prior, pr_number="7")
            self.assertEqual(d.action, "retry", f"prior={prior}")

    def test_is_error_death_holds_at_the_cap(self):
        d = fix_run_outage.decide(
            IS_ERROR_EXEC, fix_run_outage.RETRY_CAP, pr_number="7"
        )
        self.assertEqual(d.action, "hold")

    def test_retry_comment_is_plain_english_and_tagged(self):
        d = fix_run_outage.decide(
            IS_ERROR_EXEC, 0, pr_number="7", run_url="https://runs/1"
        )
        self.assertIn(fix_run_outage.OUTAGE_TAG, d.pr_comment)
        self.assertIn("AI service was unavailable", d.pr_comment)
        self.assertIn("retry", d.pr_comment)
        self.assertIn("https://runs/1", d.pr_comment)
        self.assertIn("AI service was unavailable", d.card_comment)
        self.assertIn("#7", d.card_comment)
        self.assertIn("no action needed", d.card_comment.lower())

    def test_hold_comment_blames_the_outage_not_the_work(self):
        d = fix_run_outage.decide(
            IS_ERROR_EXEC, fix_run_outage.RETRY_CAP, pr_number="7"
        )
        self.assertIn("AI service was unavailable", d.pr_comment)
        self.assertIn("AI service was unavailable", d.card_comment)
        # The CEO instruction mirrors the house park pattern.
        self.assertIn("**Todo**", d.card_comment)
        self.assertIn("**Backlog**", d.card_comment)

    def test_hold_comment_does_not_carry_the_retry_tag(self):
        # reconcile's sweep re-dispatches on OUTAGE_TAG; the hold comment
        # must be invisible to it or the loop never terminates.
        d = fix_run_outage.decide(
            IS_ERROR_EXEC, fix_run_outage.RETRY_CAP, pr_number="7"
        )
        self.assertNotIn(fix_run_outage.OUTAGE_TAG, d.pr_comment)
        self.assertNotIn(fix_run_outage.OUTAGE_TAG, d.card_comment)

    def test_comments_never_collide_with_fix_loop_counter_markers(self):
        # These strings ARE agent-fix's budgets and its fix-vs-conflict
        # router; an outage comment containing one corrupts the counters.
        forbidden = (
            "🔧 Fix attempt",
            "pushed — CI and critic review re-running",
            "🔀 Conflict resolution",
            "VERDICT:",
            "QA Critic",
        )
        for prior in (0, fix_run_outage.RETRY_CAP):
            d = fix_run_outage.decide(IS_ERROR_EXEC, prior, pr_number="7")
            for marker in forbidden:
                self.assertNotIn(marker, d.pr_comment)
                self.assertNotIn(marker, d.card_comment)


class CliFixtureTest(unittest.TestCase):
    """Result-JSON fixtures both ways, end-to-end through the CLI."""

    def _run(self, exec_path, prior="0", extra=()):
        return subprocess.run(
            [
                sys.executable,
                os.path.join(
                    os.path.dirname(__file__), "..", "scripts", "fix_run_outage.py"
                ),
                "decide",
                exec_path,
                prior,
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    def test_simulated_is_error_result_prints_retry_and_writes_comments(self):
        with tempfile.TemporaryDirectory() as td:
            pr_out = os.path.join(td, "pr.md")
            card_out = os.path.join(td, "card.md")
            p = self._run(
                IS_ERROR_FIXTURE,
                extra=(
                    "--pr", "7",
                    "--run-url", "https://runs/1",
                    "--pr-comment-out", pr_out,
                    "--card-comment-out", card_out,
                ),
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "retry")
            with open(pr_out) as f:
                self.assertIn("AI service was unavailable", f.read())
            with open(card_out) as f:
                self.assertIn("no action needed", f.read().lower())

    def test_genuine_no_progress_result_prints_escalate(self):
        with tempfile.TemporaryDirectory() as td:
            pr_out = os.path.join(td, "pr.md")
            p = self._run(
                CLEAN_FIXTURE,
                extra=("--pr", "7", "--pr-comment-out", pr_out),
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "escalate")
            # No outage → no comment files: the workflow's escalate arm owns
            # the messaging, and a stale file must not leak into a comment.
            self.assertFalse(os.path.exists(pr_out))

    def test_missing_file_prints_escalate(self):
        p = self._run("/nonexistent/exec.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "escalate")

    def test_cap_reached_prints_hold(self):
        p = self._run(
            IS_ERROR_FIXTURE, prior=str(fix_run_outage.RETRY_CAP), extra=("--pr", "7")
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "hold")


def report_step() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"name:\s*Report\b(.*?)(?:\n      - name:|\Z)", src, re.S)
    if not m:
        raise AssertionError("'Report' step not found in agent-fix.yml")
    return m.group(1)


class WorkflowWiringTest(unittest.TestCase):
    """agent-fix.yml routes its no-progress branch through the guard."""

    def test_fix_step_exposes_execution_file_output(self):
        src = open(WORKFLOW).read()
        m = re.search(r"- name: Fix\n(.*?)uses: anthropics/claude-code-action", src, re.S)
        self.assertIsNotNone(m, "'Fix' step not found")
        self.assertIn("id: claude", m.group(1))
        self.assertIn("steps.claude.outputs.execution_file", report_step())

    def test_report_step_calls_the_outage_guard(self):
        self.assertIn("fix_run_outage.py", report_step())

    def test_prior_outage_count_is_worker_bot_authored_only(self):
        # DRE-1995 discipline: a forged outage marker must not burn the cap.
        step = report_step()
        self.assertRegex(
            step,
            r'select\(\.user\.login == "agent-bureau-bot\[bot\]"\)'
            r'\s*\|\s*select\(\.body \| contains\("'
            + re.escape(fix_run_outage.OUTAGE_TAG)
            + r'"\)\)',
        )

    def _case_arm(self, label: str) -> str:
        step = report_step()
        m = re.search(label + r"\)\n(.*?);;", step, re.S)
        self.assertIsNotNone(m, f"'{label})' case arm not found in Report step")
        return m.group(1)

    def test_retry_arm_neither_parks_nor_burns_an_attempt(self):
        arm = self._case_arm("retry")
        self.assertNotIn("park_for_human", arm)
        self.assertNotIn("needs-human", arm)
        self.assertNotIn("Plan Review", arm)
        self.assertNotIn("Fix attempt", arm)

    def test_hold_arm_parks_for_human(self):
        self.assertIn("park_for_human", self._case_arm("hold"))

    def test_genuine_no_progress_escalation_is_unchanged(self):
        # Acceptance: ran-but-pushed-nothing keeps today's behavior.
        step = report_step()
        self.assertIn("pushed no new commit", step)
        self.assertIn("The fix agent ran but produced no change", step)
        self.assertIn("park_for_human", step)


def _pr(comments, number=7, branch="agent/DRE-1-x", mstate="CLEAN"):
    return {
        "number": number,
        "headRefName": branch,
        "mergeStateStatus": mstate,
        "comments": comments,
    }


def _c(body, login="agent-bureau-bot"):
    return {"author": {"login": login}, "body": body}


OUTAGE_COMMENT = (
    f"⚠️ {fix_run_outage.OUTAGE_TAG}: the AI service was unavailable during "
    "this fix run — will retry."
)


def _sweep(prs, busy="[]"):
    def fake_gh(*args):
        if args[:2] == ("run", "list"):
            return busy
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        raise AssertionError(f"unexpected gh read: {args}")

    with mock.patch.object(reconcile, "gh", side_effect=fake_gh), \
         mock.patch.object(reconcile, "gh_dispatch") as dispatch:
        reconcile.retry_outage_fixes()
    return dispatch


class ReconcileOutageSweepTest(unittest.TestCase):
    """reconcile.retry_outage_fixes(): the promised automatic retry."""

    def test_dispatches_fix_agent_when_last_fix_word_is_outage(self):
        dispatch = _sweep([_pr([_c(OUTAGE_COMMENT)])])
        dispatch.assert_called_once()
        self.assertIn("pr_number=7", dispatch.call_args.args)

    def test_human_chatter_after_the_outage_does_not_suppress_the_retry(self):
        dispatch = _sweep(
            [_pr([_c(OUTAGE_COMMENT), _c("thanks, watching this", login="ceo")])]
        )
        dispatch.assert_called_once()

    def test_no_dispatch_when_a_later_fix_attempt_superseded_the_outage(self):
        dispatch = _sweep(
            [_pr([
                _c(OUTAGE_COMMENT),
                _c("🔧 Fix attempt 1 pushed — CI and critic review re-running."),
            ])]
        )
        dispatch.assert_not_called()

    def test_forged_outage_marker_is_invisible(self):
        # DRE-1995/1998 discipline: only the worker bot's own markers count.
        dispatch = _sweep([_pr([_c(OUTAGE_COMMENT, login="mallory")])])
        dispatch.assert_not_called()

    def test_no_dispatch_after_the_outage_hold(self):
        d = fix_run_outage.decide(
            IS_ERROR_EXEC, fix_run_outage.RETRY_CAP, pr_number="7"
        )
        dispatch = _sweep([_pr([_c(OUTAGE_COMMENT), _c(d.pr_comment)])])
        dispatch.assert_not_called()

    def test_dirty_prs_are_left_to_the_conflict_sweep(self):
        dispatch = _sweep([_pr([_c(OUTAGE_COMMENT)], mstate="DIRTY")])
        dispatch.assert_not_called()

    def test_non_agent_branches_are_ignored(self):
        dispatch = _sweep([_pr([_c(OUTAGE_COMMENT)], branch="feature/manual")])
        dispatch.assert_not_called()

    def test_busy_guard_skips_when_a_fix_run_is_live(self):
        dispatch = _sweep(
            [_pr([_c(OUTAGE_COMMENT)])], busy='[{"status": "in_progress"}]'
        )
        dispatch.assert_not_called()

    def test_full_sweep_runs_the_outage_backstop(self):
        with mock.patch.object(reconcile, "unstick_conflicts"), \
             mock.patch.object(reconcile, "retrigger_dead_heads"), \
             mock.patch.object(reconcile, "fix_approved_but_red"), \
             mock.patch.object(reconcile, "retry_outage_fixes") as outage, \
             mock.patch.object(reconcile, "active_cards", return_value=[]), \
             mock.patch.object(reconcile, "promote_ready"), \
             mock.patch.object(reconcile, "backlog_children", return_value=[]):
            reconcile.main()
        outage.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
