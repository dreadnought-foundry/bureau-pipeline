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

# Comment fixtures in the REST issues/comments shape the workflow now hands to
# fix_dead_run.py (oldest-first, `{"user": {"login"}, "body"}`).
WORKER = "agent-bureau-bot[bot]"
MARKER = f"⚡ {fix_dead_run.OUTAGE_TAG}: the fix run died with an API/model error."
PUSH_C = "🔧 Fix attempt 1 pushed — CI and critic review re-running."


def _c(login, body):
    return {"user": {"login": login}, "body": body}


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


class ConsecutiveDeathsTest(unittest.TestCase):
    """DRE-2018 review finding: the retry cap must count only CONSECUTIVE
    deaths since the last successful push, not every death marker the PR has
    ever carried. A recovered outage episode weeks ago must not pre-exhaust
    the cap for a fresh one."""

    def count(self, comments):
        return fix_dead_run.consecutive_prior_deaths(comments)

    def test_counts_all_deaths_within_one_episode(self):
        # No push between them — all deaths are in a row.
        self.assertEqual(self.count([_c(WORKER, MARKER), _c(WORKER, MARKER)]), 2)

    def test_push_resets_the_consecutive_run(self):
        # The critic's cross-episode scenario: [marker, marker, push, marker]
        # → only the post-push death counts (1), NOT the cumulative 3.
        comments = [
            _c(WORKER, MARKER), _c(WORKER, MARKER),
            _c(WORKER, PUSH_C), _c(WORKER, MARKER),
        ]
        self.assertEqual(self.count(comments), 1)

    def test_conflict_push_marker_also_resets(self):
        # Both push markers carry the same "pushed — CI and critic review
        # re-running" substring, so a conflict-round push clears the run too.
        conflict_push = "🔀 Conflict resolution round 2 pushed — CI and " \
                        "critic review re-running."
        comments = [_c(WORKER, MARKER), _c(WORKER, conflict_push),
                    _c(WORKER, MARKER)]
        self.assertEqual(self.count(comments), 1)

    def test_ignores_non_worker_bot_markers(self):
        # DRE-1995: anyone can plant the marker; only worker-bot deaths count.
        self.assertEqual(
            self.count([_c("attacker[bot]", MARKER), _c(WORKER, MARKER)]), 1
        )

    def test_forged_push_marker_does_not_reset(self):
        # A push marker from a non-worker identity must not clear the run —
        # otherwise an attacker could reset the cap and force endless retries.
        comments = [_c(WORKER, MARKER), _c("attacker[bot]", PUSH_C),
                    _c(WORKER, MARKER)]
        self.assertEqual(self.count(comments), 2)

    def test_hold_comment_neither_counts_nor_resets(self):
        # The hold comment (🛑, no OUTAGE_TAG marker) is worker-bot authored
        # but is neither a death marker nor a push — it is transparent here.
        hold = "🛑 The AI service failed 3 fix runs in a row on this PR."
        self.assertEqual(self.count([_c(WORKER, MARKER), _c(WORKER, hold)]), 1)

    def test_empty_or_none(self):
        self.assertEqual(self.count([]), 0)
        self.assertEqual(self.count(None), 0)


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

    def _run_comments(self, payload, comments, extra=()):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            with open(exec_path, "w") as f:
                json.dump(payload, f)
            comments_path = os.path.join(td, "comments.json")
            with open(comments_path, "w") as f:
                json.dump(comments, f)
            return subprocess.run(
                [sys.executable,
                 os.path.join(os.path.dirname(__file__), "..", "scripts",
                              "fix_dead_run.py"),
                 "decide", exec_path,
                 "--comments-json", comments_path, *extra],
                capture_output=True, text=True,
            )

    def test_cli_comments_json_counts_only_post_push_deaths(self):
        # Cumulative would be 3 (→ hold); consecutive since the push is 1
        # (→ retry). The --comments-json path must derive the consecutive count.
        comments = [
            _c(WORKER, MARKER), _c(WORKER, MARKER),
            _c(WORKER, PUSH_C), _c(WORKER, MARKER),
        ]
        p = self._run_comments(DIED, comments)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self._action_and_body(p)[0], "retry")

    def test_cli_comments_json_holds_at_consecutive_cap(self):
        # A push, then RETRY_CAP deaths with no push between → hold.
        comments = [_c(WORKER, PUSH_C)] + [
            _c(WORKER, MARKER) for _ in range(fix_dead_run.RETRY_CAP)
        ]
        p = self._run_comments(DIED, comments)
        self.assertEqual(self._action_and_body(p)[0], "hold")

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

    def test_death_count_hands_full_comment_list_to_the_script(self):
        # DRE-2018 review: the retry cap must be scoped to CONSECUTIVE deaths
        # since the last push, which requires the push markers and comment
        # authorship — so the Report step hands the full comment list to
        # fix_dead_run.py via --comments-json instead of a cumulative jq count.
        # (The worker-bot filter + push-marker reset now live in the script,
        # unit-tested by ConsecutiveDeathsTest; DRE-1995 discipline preserved.)
        step = report_step()
        self.assertIn("--comments-json", step)
        # The old cumulative count — every marker ever posted — must be gone.
        self.assertNotIn(
            f'contains("{fix_dead_run.OUTAGE_TAG}"))] | length', step
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
