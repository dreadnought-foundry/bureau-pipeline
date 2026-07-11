"""RED-first tests for the agent-fix model-death guard (DRE-2018).

Origin (2026-07-10, DeltaSolv token outage): when the model died mid-fix-run
(execution result {"is_error": true} — API outage, exhausted subscription),
agent-fix's no-progress guard posted "🛑 Fix attempt N pushed no new commit"
and parked the card needs-human/Plan Review — an escalation that blames the
fix agent and misleads the CEO's queue. The medic path (agent-task) already
distinguishes model-death ("agent died with API/model error") and requeues;
the fix path did not.

fix_dead_run.py owns the call for the no-progress guard, from the execution
result JSON (the same result-JSON contract check_agent_result.py reads):

  - is_error death → "retry": post the fix-run-model-death marker comment
    (the reconcile sweep re-dispatches the fix agent on it — nothing
    event-driven ever re-fires agent-fix once the qa-bot's REQUEST_CHANGES
    trigger is consumed), burn NO fix-attempt budget, do NOT park the card.
  - after RETRY_CAP straight deaths (the medic's cap pattern) → "hold":
    park for a human with honest outage wording, not agent-failure wording.
  - genuine ran-but-pushed-nothing → "escalate": today's behavior, unchanged.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fix_dead_run  # noqa: E402

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)

DIED = {"subtype": "success", "is_error": True}
RAN = {"subtype": "success", "is_error": False}


def decision(execution, prior_deaths=0, **kw):
    return fix_dead_run.decide(execution, prior_deaths, **kw)


class DecideTest(unittest.TestCase):
    def test_is_error_death_retries_even_with_success_subtype(self):
        # The DRE-1346 shape: {"subtype": "success", "is_error": true}.
        self.assertEqual(decision(DIED).action, "retry")

    def test_ran_but_pushed_nothing_escalates(self):
        # The model RAN fine and still produced nothing re-reviewable —
        # today's park-for-human escalation stands.
        d = decision(RAN)
        self.assertEqual(d.action, "escalate")
        self.assertEqual(d.comment, "")

    def test_missing_execution_result_escalates(self):
        # No result file = no PROOF of an outage; fail safe to today's path.
        self.assertEqual(decision(None).action, "escalate")

    def test_retry_comment_carries_marker_and_outage_wording(self):
        c = decision(DIED).comment
        self.assertIn(fix_dead_run.OUTAGE_TAG, c)
        self.assertIn("AI service", c)
        self.assertIn("retr", c)  # "will retry"/"re-dispatches" phrasing

    def test_retry_comment_never_matches_budget_or_routing_markers(self):
        # The retry comment is worker-bot authored on the PR, so it must not
        # collide with any marker other reads count or route on:
        #   "🔧 Fix attempt"   — the 3-attempt review-fix budget counter
        #   "🔀 Conflict resolution" — the 5-round conflict budget counter
        #   "pushed — CI and critic review re-running" — the push marker that
        #       flips fix-vs-conflict routing (LAST_PUSH_IDX)
        #   leading 🛑 — fix_context.py's blocker prefix (an outage is not an
        #       unanswered blocker; the next fix run must not stop on it)
        c = decision(DIED, run_url="https://runs/1").comment
        self.assertNotIn("🔧 Fix attempt", c)
        self.assertNotIn("🔀 Conflict resolution", c)
        self.assertNotIn("pushed — CI and critic review re-running", c)
        self.assertNotIn("QA Critic", c)
        self.assertFalse(c.startswith("🛑"))

    def test_cap_holds_after_retries(self):
        # Medic's cap pattern: deaths 1..RETRY_CAP retry; the next one holds.
        self.assertEqual(decision(DIED, fix_dead_run.RETRY_CAP).action, "hold")

    def test_under_cap_still_retries(self):
        self.assertEqual(
            decision(DIED, fix_dead_run.RETRY_CAP - 1).action, "retry"
        )

    def test_cap_never_reaches_escalate_wording(self):
        # The hold must read as an OUTAGE, not as the agent failing.
        c = decision(DIED, fix_dead_run.RETRY_CAP).comment
        self.assertIn("AI service", c)
        self.assertNotIn("pushed no new commit", c)

    def test_hold_comment_omits_the_marker(self):
        # If the hold comment carried the marker it would (a) count itself
        # into the next DEATHS read and (b) be the newest worker-bot comment
        # the reconcile sweep re-dispatches on — un-holding the hold.
        c = decision(DIED, fix_dead_run.RETRY_CAP).comment
        self.assertNotIn(fix_dead_run.OUTAGE_TAG, c)

    def test_hold_comment_is_blocker_prefixed(self):
        # A held PR IS an unanswered blocker: opening with 🛑 lets
        # fix_context.py show it to the next fix run as awaiting a human.
        c = decision(DIED, fix_dead_run.RETRY_CAP).comment
        self.assertTrue(c.startswith("🛑"))

    def test_run_url_lands_in_the_comment(self):
        c = decision(DIED, run_url="https://runs/42").comment
        self.assertIn("https://runs/42", c)


class CliTest(unittest.TestCase):
    def _run(self, payload, prior="0", extra=()):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            if payload is not None:
                with open(exec_path, "w") as f:
                    json.dump(payload, f)
            return subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(__file__), "..", "scripts",
                              "fix_dead_run.py"),
                 "decide", exec_path, prior, *extra],
                capture_output=True, text=True,
            )

    def _action_and_body(self, proc):
        lines = proc.stdout.split("\n")
        return lines[0], "\n".join(lines[2:])

    def test_cli_retry_on_is_error(self):
        p = self._run(DIED)
        self.assertEqual(p.returncode, 0)
        action, body = self._action_and_body(p)
        self.assertEqual(action, "retry")
        self.assertIn(fix_dead_run.OUTAGE_TAG, body)

    def test_cli_escalate_when_model_ran(self):
        p = self._run(RAN)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self._action_and_body(p)[0], "escalate")

    def test_cli_escalate_on_missing_result_file(self):
        p = self._run(None)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self._action_and_body(p)[0], "escalate")

    def test_cli_hold_at_cap(self):
        p = self._run(DIED, prior=str(fix_dead_run.RETRY_CAP))
        self.assertEqual(self._action_and_body(p)[0], "hold")

    def test_cli_threads_run_url(self):
        p = self._run(DIED, extra=("--run-url", "https://runs/7"))
        self.assertIn("https://runs/7", self._action_and_body(p)[1])

    def test_cli_result_list_shape(self):
        # The action can write a message list ending with the result record
        # (the same tolerance check_agent_result._load_execution has).
        p = self._run([{"type": "message"}, {"subtype": "x", "is_error": True}])
        self.assertEqual(self._action_and_body(p)[0], "retry")


def report_step() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"name:\s*Report\b(.*?)(?:\n      - name:|\Z)", src, re.S)
    if not m:
        raise AssertionError("'Report' step not found in agent-fix.yml")
    return m.group(1)


class WorkflowWiringTest(unittest.TestCase):
    """agent-fix.yml must actually consult the decision — shape pins in the
    house pattern of test_fix_dispute_escalates_to_human.py."""

    def test_fix_step_exposes_execution_file(self):
        # The Report step can only read is_error if the claude step has an id
        # and its execution_file output is consumed.
        src = open(WORKFLOW).read()
        self.assertIn("id: claude", src)
        self.assertIn("steps.claude.outputs.execution_file", report_step())

    def test_report_consults_fix_dead_run(self):
        self.assertIn("fix_dead_run.py", report_step())

    def test_death_count_reads_worker_bot_comments_only(self):
        # DRE-1995 discipline: anyone can comment on a PR; only worker-bot
        # authored marker comments may count toward the retry cap.
        step = report_step()
        m = re.search(
            r"select\(\.user\.login == \"agent-bureau-bot\[bot\]\"\)[^\n]*"
            + re.escape(f'contains("{fix_dead_run.OUTAGE_TAG}")'),
            step,
        )
        self.assertIsNotNone(
            m, "death counter must filter to worker-bot + marker in one jq"
        )

    def test_retry_branch_never_parks(self):
        # The whole point: an outage must not park the card or stamp
        # needs-human (park_for_human does both).
        step = report_step()
        m = re.search(
            r"if \[ \"\$ACTION\" = \"retry\" \]; then(.*?)\n\s*elif", step, re.S
        )
        self.assertIsNotNone(m, "retry branch not found in Report step")
        self.assertNotIn("park_for_human", m.group(1))
        self.assertNotIn("needs-human", m.group(1))

    def test_genuine_no_progress_keeps_todays_escalation(self):
        # Acceptance: ran-but-pushed-nothing behavior unchanged.
        step = report_step()
        self.assertIn("pushed no new commit", step)
        self.assertIn("park_for_human", step)


if __name__ == "__main__":
    unittest.main()
