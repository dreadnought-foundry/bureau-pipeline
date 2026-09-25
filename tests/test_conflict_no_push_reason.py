"""A conflict-resolution run that pushes nothing says why on the PR (DRE-4849).

The live fault (2026-09-24). The conflict agent — agent-fix.yml in conflict
mode — ended "success" with no push, repeatedly: portico #699 and #701 twice
each, #687 twice. Each run spent about 30 turns and about $0.60 on a trivial
conflict (one list line in `mcp_tools/index.ts`, one test assertion both sides
had fixed) that the operator later resolved in minutes.

Nothing recorded why. The action's output is hidden (`show_full_output` off)
and the agent-log upload failed on its role, so every "pushed no new commit"
escalation reached a person with no reason attached, and the person redid the
diagnosis from scratch.

The answer, and what this file pins:

  * the conflict-mode prompt tells the agent to write a one-paragraph reason
    file — keyed to this run through the DRE-3951 handoff, like the blocker and
    the refutation — before it ends without a push;
  * when the run pushed nothing, the escalation comment quotes that reason
    verbatim, and with no reason file it says the agent left none and names the
    run log.

The scenarios EXECUTE the real Report step (the harness in
tests/test_fix_answers_its_own_pr.py), with `gh` and `linear_ops.py` stubbed
and the real pipeline scripts in the checkout.

Run: python3 -m pytest tests/test_conflict_no_push_reason.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fix_budget  # noqa: E402
import fix_handoff  # noqa: E402
from test_fix_answers_its_own_pr import (  # noqa: E402
    CARD, PR, REPO, SHA, FixRun, _write, step_named, substitute,
)

RUN_URL = f"https://github.com/{REPO}/actions/runs/35120044871"

# The reason the portico #699 run would have left, had it been asked. Two
# lines, backticks and a command substitution: quoted VERBATIM means none of
# that is reflowed, dropped or evaluated by the shell that posts it.
REASON = (
    "Conflicted: one list line in `mcp_tools/index.ts` and one assertion in "
    "`index.test.ts` that both sides had fixed.\n"
    "Tried: merged origin/main, kept both list entries, took main's assertion; "
    "`npm test` went red after the merge on $(whoami), so I did not push."
)

NO_REASON = "the agent left no reason — its run log is"


class NoPushRun(FixRun):
    """A fix run whose branch did not move: `gh pr view` answers the head the
    run was dispatched on, and no execution file exists, so fix_dead_run reads
    it as a run that worked and pushed nothing (escalate — not an outage)."""

    def _bash(self, run: str, env: dict):
        script = os.path.join(self.td, "step.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("set -eo pipefail\n" + run)
        return subprocess.run(
            ["bash", script], cwd=self.td,
            env=dict(
                os.environ,
                PATH=self.bin + os.pathsep + os.environ["PATH"],
                RUNNER_TEMP=self.base,
                GH_LOG=self.log,
                GH_POST_SHA=self.sha,
                GH_TOKEN="test",
                DISPATCH_TOKEN="workflow-token",
                LINEAR_API_KEY="test-key",
                GITHUB_OUTPUT=self.output,
                **env,
            ),
            capture_output=True, text=True,
        )

    def report_step(self, mode: str = "conflict"):
        run = substitute(step_named("Report")["run"], {})
        run = run.replace("/tmp/", self.legacy + "/")
        return self._bash(run, {
            "REPO": self.repo, "PR": self.pr, "CARD": self.card,
            "PRE_SHA": self.sha, "ATTEMPT": "2", "MODE": mode,
            "EXEC_FILE": os.path.join(self.td, "exec.json"),
            "RUN_URL": RUN_URL,
        })


class NoPushCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="conflict-no-push-")
        self.addCleanup(shutil.rmtree, self.td, True)
        self.run = NoPushRun(self.td)
        self.paths = self.run.open_handoff()
        self.run.stamp_verdict(body="")

    def only_comment(self, proc) -> str:
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        posted = self.run.posted()
        self.assertEqual(len(posted), 1, posted)
        return posted[0]


class TheEscalationQuotesTheAgentsReason(NoPushCase):
    """AC1: a conflict-mode run that ends with no push and a reason file posts
    that reason in its escalation comment."""

    def test_the_reason_is_quoted_verbatim(self):
        _write(self.paths["reason"], REASON)
        body = self.only_comment(self.run.report_step())
        self.assertIn("pushed no new commit", body)
        for line in REASON.splitlines():
            self.assertIn(line, body, "a line of the reason was not quoted")
        self.assertNotIn(NO_REASON, body)

    def test_the_comment_still_parks_for_a_human(self):
        # The reason is added to the escalation; it does not replace it.
        _write(self.paths["reason"], REASON)
        self.only_comment(self.run.report_step())
        notes = self.run.card_notes()
        self.assertIn(["add-label", CARD, "needs-human"], notes)

    def test_a_reason_from_another_run_is_never_quoted(self):
        # DRE-3951's rule holds for the new file too: a reason that appears at
        # another run's keyed path mid-run cannot be attributed to this one.
        other = fix_handoff.handoff_dir(self.run.base, REPO, "9999", "f" * 40)
        os.makedirs(other, exist_ok=True)
        _write(os.path.join(other, "fix-reason.txt"), "another PR's reason")
        proc = self.run.report_step()
        self.assertNotEqual(proc.returncode, 0, "a refusal is loud")
        self.assertEqual(self.run.posted(), [])
        self.assertNotIn("another PR's reason", proc.stdout + proc.stderr)


class NoReasonFileSaysSo(NoPushCase):
    """AC2: with no reason file the comment names the run log and says no
    reason was left."""

    def test_the_comment_names_the_run_log(self):
        body = self.only_comment(self.run.report_step())
        self.assertIn("pushed no new commit", body)
        self.assertIn(NO_REASON, body)
        self.assertIn(RUN_URL, body)

    def test_an_empty_reason_file_is_no_reason(self):
        _write(self.paths["reason"], "  \n")
        body = self.only_comment(self.run.report_step())
        self.assertIn(NO_REASON, body)
        self.assertIn(RUN_URL, body)

    def test_the_previous_jobs_reason_is_cleared_not_quoted(self):
        # What the last job on the mini left under the shared path: cleared
        # when this job opens its handoff, so it reads as no reason at all.
        stale = _write(os.path.join(self.run.legacy, "fix-reason.txt"),
                       "a stale reason from yesterday")
        when = time.time() - 3600
        os.utime(stale, (when, when))
        self.assertEqual(self.run.open_handoff_step().returncode, 0)
        self.run.stamp_verdict(body="")
        self.assertFalse(os.path.exists(stale))
        body = self.only_comment(self.run.report_step())
        self.assertNotIn("a stale reason", body)
        self.assertIn(NO_REASON, body)


class FixModeIsUnchanged(NoPushCase):
    """Scope: the reason file is the conflict agent's. A fix-mode run that
    pushes nothing already has the blocker file, and its escalation is not
    rewritten here."""

    def test_fix_mode_does_not_claim_a_missing_reason(self):
        body = self.only_comment(self.run.report_step(mode="fix"))
        self.assertIn("pushed no new commit", body)
        self.assertNotIn(NO_REASON, body)


class TheComposer(unittest.TestCase):
    """fix_budget.no_push_body — the one place the wording lives."""

    def test_conflict_with_a_reason(self):
        body = fix_budget.no_push_body("conflict", 3, SHA, REASON, RUN_URL)
        self.assertTrue(body.startswith(
            f"🛑 Fix attempt 3 pushed no new commit (branch still at `{SHA[:8]}`)"
        ), body)
        for line in REASON.splitlines():
            self.assertIn(line, body)
        self.assertNotIn(NO_REASON, body)

    def test_conflict_without_a_reason(self):
        for missing in ("", "   \n\n"):
            body = fix_budget.no_push_body("conflict", 1, SHA, missing, RUN_URL)
            self.assertIn(f"{NO_REASON} {RUN_URL}", body)

    def test_fix_mode_keeps_todays_wording(self):
        body = fix_budget.no_push_body("fix", 2, SHA, "", RUN_URL)
        self.assertEqual(
            body,
            f"🛑 Fix attempt 2 pushed no new commit (branch still at "
            f"`{SHA[:8]}`) — the reviewer will not re-run and the last verdict "
            "stands. Escalating to a human rather than leaving this PR stuck.",
        )

    def test_the_cli_reads_the_reason_file(self):
        reason_file = os.path.join(tempfile.mkdtemp(), "reason.txt")
        self.addCleanup(shutil.rmtree, os.path.dirname(reason_file), True)
        _write(reason_file, REASON)
        out = os.path.join(os.path.dirname(reason_file), "body.md")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "fix_budget.py"), "no-push",
             "--mode", "conflict", "--attempt", "4", "--head", SHA,
             "--reason-file", reason_file, "--run-url", RUN_URL, "--out", out],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = open(out, encoding="utf-8").read()
        self.assertIn(REASON.splitlines()[1], body)

    def test_the_cli_treats_an_absent_file_as_no_reason(self):
        out = os.path.join(tempfile.mkdtemp(), "body.md")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "fix_budget.py"), "no-push",
             "--mode", "conflict", "--attempt", "1", "--head", SHA,
             "--reason-file", out + ".missing", "--run-url", RUN_URL,
             "--out", out],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"{NO_REASON} {RUN_URL}", open(out, encoding="utf-8").read())


class ThePromptAsksForTheReason(unittest.TestCase):
    """AC3, read off the workflow: the conflict-mode prompt tells the agent to
    write the reason file before ending without a push."""

    def setUp(self):
        self.prompt = step_named("Fix")["with"]["prompt"]

    def test_the_handoff_publishes_a_reason_path(self):
        self.assertIn("reason", fix_handoff.KINDS)

    def test_the_prompt_names_this_runs_reason_path(self):
        self.assertIn("${{ steps.handoff.outputs.reason }}", self.prompt)

    def test_the_instruction_is_in_the_conflict_step(self):
        # Step 2b is the merge-conflict step; the instruction lives there, and
        # says to write the file BEFORE ending without a push.
        start = self.prompt.index("2b. Check for a merge conflict")
        end = self.prompt.index("\n3. ", start)
        step = " ".join(self.prompt[start:end].split())
        self.assertIn("${{ steps.handoff.outputs.reason }}", step)
        self.assertRegex(step, r"(?i)before you end")
        self.assertRegex(step, r"(?i)without (a )?push")
        for what in ("what conflicted", "what you tried", "why you did not push"):
            self.assertIn(what, step)


if __name__ == "__main__":
    unittest.main()
