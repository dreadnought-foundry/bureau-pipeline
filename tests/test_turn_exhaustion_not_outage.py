"""RED-first tests: a turn-exhausted run is not an AI-service outage (DRE-2312).

`check_agent_result.is_error_death()` was a bare `execution.get("is_error") is
True`. It never read the error subtype, so `error_max_turns` — the agent
hitting claude-code-action's 60-turn cap — was indistinguishable from a real
API failure, and everything downstream inherited the wrong story:

  * portico PR #170 (2026-08-24, run 31331769488): 16 minutes of agent time,
    `subtype: error_max_turns`, and the card was told "🛑 The AI service failed
    3 fix runs in a row … died with an API/model error each time". There was no
    outage; the conflict was three files with one hunk each.
  * portico PR #234 (2026-08-10): three consecutive runs at 61 turns,
    $4.72 / $5.07 / — , each posted as "the AI service was unavailable… No
    fix-attempt budget was used". ~$15 to repeat a run that could not finish.
  * agent-bureau run 32791846359 / DRE-2695 (2026-08-24): a BUILD run, not a
    fix run — `subtype: error_max_turns` after 36 minutes that reached "3/5
    implementation green", reported as "agent died with API/model error
    (is_error) — dead run 1/3" plus a `model-error: claude-opus-5` marker.
    agent-task.yml ran its OWN inline `(e or {}).get('is_error')` test, so
    fixing the shared predicate alone would have left that path wrong.

What this pins:

  * turn exhaustion is classified DISTINCTLY from an API/model death, and a
    genuine outage is recognised by its POSITIVE signature (sub-second
    `duration_ms`, `num_turns: 1`, `total_cost_usd: 0`) rather than by the
    absence of `error_max_turns`;
  * turn exhaustion is a FAILED ATTEMPT, not an outage: it is retried at most
    ONCE (agent runs are stochastic — DRE-2695's second attempt reached the
    same milestone ~10 minutes earlier) and the SECOND one escalates with the
    real reason;
  * no message on either path claims the AI service failed, and none claims no
    budget was used;
  * turn exhaustion consumes no `dead run N/3` strike and writes no
    `model-error:` marker, so the DRE-1354 model fallback cannot fire on it;
  * both call sites — the fix path and agent-task's dead-run requeue — classify
    through the SAME shared predicate;
  * a genuine API death still retries under the cap and still holds with the
    outage wording, unchanged.
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

import check_agent_result  # noqa: E402
import dead_run  # noqa: E402
import fix_dead_run  # noqa: E402
import reconcile  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def workflow(name: str) -> str:
    return open(os.path.join(WORKFLOWS, name)).read()


# The PR #170 / DRE-2695 shape: claude-code-action's own record of hitting the
# turn ceiling. Minutes of duration, dozens of turns, real dollars.
EXHAUSTED = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "duration_ms": 960_000,
    "num_turns": 60,
    "total_cost_usd": 4.72,
    "terminal_reason": "max_turns",
    "result": "Reached maximum number of turns (60)",
}

# The positive signature of a REAL outage (DRE-2365): a transport/auth failure
# returns sub-second, on one turn, having spent nothing. This is what the split
# asserts on — a run that spent money and took minutes did not fail to reach
# the service.
OUTAGE = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 412,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 401,
    "result": "Invalid bearer token",
}

# The DRE-1346 legacy shape the existing suites drive: no subtype detail at
# all. Absent evidence of a turn cap, it stays an API/model death.
DIED = {"subtype": "success", "is_error": True}
RAN = {"subtype": "success", "is_error": False}

# Phrasing no turn-exhaustion message may contain. The expensive failure was
# not the retry — it was sending the operator to the credential chain.
OUTAGE_WORDS = ("AI service", "outage", "unavailable", "API/model error")


def assert_no_outage_claim(case: unittest.TestCase, text: str) -> None:
    for word in OUTAGE_WORDS:
        case.assertNotIn(word, text, f"turn-exhaustion text claims {word!r}: {text}")


class ClassifyTest(unittest.TestCase):
    """check_agent_result owns the classification, for every caller."""

    def test_error_max_turns_is_turn_exhaustion(self):
        self.assertEqual(
            check_agent_result.classify_death(EXHAUSTED),
            check_agent_result.DEATH_TURN_EXHAUSTION,
        )
        self.assertTrue(check_agent_result.is_turn_exhaustion(EXHAUSTED))

    def test_error_max_turns_is_not_an_api_death(self):
        # The acceptance criterion in one line: a real error_max_turns payload
        # must not be reported as an outage.
        self.assertFalse(check_agent_result.is_api_death(EXHAUSTED))

    def test_real_outage_signature_is_an_api_death(self):
        self.assertEqual(
            check_agent_result.classify_death(OUTAGE),
            check_agent_result.DEATH_API,
        )
        self.assertTrue(check_agent_result.is_api_death(OUTAGE))
        self.assertFalse(check_agent_result.is_turn_exhaustion(OUTAGE))

    def test_legacy_is_error_without_detail_stays_an_api_death(self):
        self.assertEqual(
            check_agent_result.classify_death(DIED), check_agent_result.DEATH_API
        )

    def test_terminal_reason_alone_is_enough(self):
        # The agent-bureau job log carried `terminal_reason: max_turns`.
        self.assertTrue(
            check_agent_result.is_turn_exhaustion(
                {"is_error": True, "terminal_reason": "max_turns"}
            )
        )

    def test_result_text_alone_is_enough(self):
        # ##[error]Execution failed: Reached maximum number of turns (60)
        self.assertTrue(
            check_agent_result.is_turn_exhaustion(
                {"is_error": True,
                 "result": "Reached maximum number of turns (60)"}
            )
        )

    def test_outage_signature_is_never_read_as_turn_exhaustion(self):
        # A run that took 400ms, took one turn and spent nothing did not
        # exhaust a 60-turn budget, whatever else the record says.
        payload = dict(OUTAGE, terminal_reason="max_turns")
        self.assertEqual(
            check_agent_result.classify_death(payload),
            check_agent_result.DEATH_API,
        )

    def test_clean_run_and_missing_result_are_not_deaths(self):
        self.assertEqual(
            check_agent_result.classify_death(RAN), check_agent_result.DEATH_NONE
        )
        self.assertEqual(
            check_agent_result.classify_death(None), check_agent_result.DEATH_NONE
        )
        self.assertFalse(check_agent_result.is_turn_exhaustion(None))
        self.assertFalse(check_agent_result.is_api_death(None))

    def test_is_error_death_still_covers_both_classes(self):
        # The gate's silent-death reasoning is unchanged: both classes are
        # still deaths. Only the STORY told about them differs.
        self.assertTrue(check_agent_result.is_error_death(EXHAUSTED))
        self.assertTrue(check_agent_result.is_error_death(OUTAGE))
        self.assertFalse(check_agent_result.is_error_death(RAN))

    def test_facts_name_the_cap_and_what_the_run_spent(self):
        facts = check_agent_result.turn_exhaustion_facts(EXHAUSTED)
        self.assertIn("60-turn cap", facts)
        self.assertIn("60 turns", facts)
        self.assertIn("$4.72", facts)

    def test_facts_degrade_without_numbers(self):
        facts = check_agent_result.turn_exhaustion_facts(
            {"is_error": True, "subtype": "error_max_turns"}
        )
        self.assertIn("turn cap", facts)
        self.assertNotIn("None", facts)


class ClassifyCliTest(unittest.TestCase):
    """The one predicate the workflows call — no second inline is_error test."""

    def _classify(self, payload):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "out.json")
            if payload is not None:
                with open(path, "w") as f:
                    json.dump(payload, f)
            p = subprocess.run(
                [sys.executable,
                 os.path.join(SCRIPTS, "check_agent_result.py"),
                 "classify", path],
                capture_output=True, text=True,
            )
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_cli_classifies_turn_exhaustion(self):
        self.assertEqual(self._classify(EXHAUSTED), "turn_exhaustion")

    def test_cli_classifies_api_death(self):
        self.assertEqual(self._classify(OUTAGE), "api_death")

    def test_cli_classifies_a_live_run_and_a_missing_file(self):
        self.assertEqual(self._classify(RAN), "none")
        self.assertEqual(self._classify(None), "none")

    def test_cli_tolerates_the_message_list_shape(self):
        self.assertEqual(
            self._classify([{"type": "message"}, EXHAUSTED]), "turn_exhaustion"
        )

    def test_both_call_sites_agree_on_one_payload(self):
        # The acceptance criterion: agent-task's dead-run requeue and the
        # fix path must classify the SAME error_max_turns payload the same
        # way. The CLI is what agent-task.yml calls; fix_dead_run.decide is
        # the fix path; both must say "turn exhaustion", not "outage".
        self.assertEqual(self._classify(EXHAUSTED), "turn_exhaustion")
        self.assertEqual(
            fix_dead_run.decide(EXHAUSTED, 0).action, "retry-turns"
        )
        self.assertTrue(check_agent_result.is_turn_exhaustion(EXHAUSTED))


class FixRunTurnExhaustionTest(unittest.TestCase):
    """agent-fix's no-progress guard (fix_dead_run.decide)."""

    def test_first_exhaustion_retries_with_its_own_marker(self):
        d = fix_dead_run.decide(EXHAUSTED, 0)
        self.assertEqual(d.action, "retry-turns")
        self.assertIn(fix_dead_run.TURN_CAP_TAG, d.comment)

    def test_first_exhaustion_never_posts_the_model_death_marker(self):
        # Acceptance: a turn-exhausted fix run does not post the
        # fix-run-model-death marker (it is what the outage story hangs on,
        # and what the DRE-1354 model fallback keys off).
        self.assertNotIn(
            fix_dead_run.OUTAGE_TAG, fix_dead_run.decide(EXHAUSTED, 0).comment
        )

    def test_retry_message_says_the_agent_ran_out_of_steps(self):
        c = fix_dead_run.decide(EXHAUSTED, 0, run_url="https://runs/170").comment
        self.assertIn("ran out of steps", c)
        self.assertIn("60-turn cap", c)
        self.assertIn("$4.72", c)
        self.assertIn("https://runs/170", c)
        assert_no_outage_claim(self, c)

    def test_retry_message_does_not_claim_the_budget_was_free(self):
        # "No fix-attempt budget was used" was false and it defeated the
        # breaker: a full run WAS used.
        c = fix_dead_run.decide(EXHAUSTED, 0).comment
        self.assertNotIn("No fix-attempt budget was used", c)
        self.assertIn("full fix run", c)

    def test_retry_message_never_collides_with_routing_markers(self):
        c = fix_dead_run.decide(EXHAUSTED, 0, run_url="https://runs/1").comment
        self.assertNotIn("🔧 Fix attempt", c)
        self.assertNotIn("🔀 Conflict resolution", c)
        self.assertNotIn("pushed — CI and critic review re-running", c)
        self.assertNotIn("QA Critic", c)
        self.assertFalse(c.startswith("🛑"))

    def test_second_exhaustion_escalates_with_the_real_reason(self):
        d = fix_dead_run.decide(EXHAUSTED, 0, prior_exhaustions=1)
        self.assertEqual(d.action, "hold-turns")
        self.assertIn("ran out of steps", d.comment)
        self.assertIn("split", d.comment)
        self.assertTrue(d.comment.startswith("🛑"))
        assert_no_outage_claim(self, d.comment)

    def test_hold_omits_both_markers(self):
        # The hold must be the newest worker-bot comment WITHOUT a retry
        # marker, or the reconcile sweep re-dispatches straight through it.
        c = fix_dead_run.decide(EXHAUSTED, 0, prior_exhaustions=1).comment
        self.assertNotIn(fix_dead_run.TURN_CAP_TAG, c)
        self.assertNotIn(fix_dead_run.OUTAGE_TAG, c)

    def test_exhaustion_cap_is_one_retry(self):
        self.assertEqual(fix_dead_run.TURN_RETRY_CAP, 1)

    def test_outage_deaths_do_not_spend_the_exhaustion_budget(self):
        # Separate classes, separate counters: prior API deaths must not push
        # a first turn exhaustion straight to the hold.
        self.assertEqual(
            fix_dead_run.decide(EXHAUSTED, 2, prior_exhaustions=0).action,
            "retry-turns",
        )

    def test_pr_170_regression_two_exhaustions_one_escalation(self):
        # The shape that burned three fix runs and reported an outage: drive
        # two consecutive error_max_turns results through the real comment
        # thread and assert ONE retry, then ONE escalation with the real
        # reason — never three runs and never an outage claim.
        worker = "agent-bureau-bot[bot]"
        thread = []
        first = fix_dead_run.decide(EXHAUSTED, 0, prior_exhaustions=0)
        self.assertEqual(first.action, "retry-turns")
        thread.append({"user": {"login": worker}, "body": first.comment})

        prior = fix_dead_run.consecutive_prior_markers(
            thread, fix_dead_run.TURN_CAP_TAG
        )
        self.assertEqual(prior, 1)
        second = fix_dead_run.decide(EXHAUSTED, 0, prior_exhaustions=prior)
        self.assertEqual(second.action, "hold-turns")
        assert_no_outage_claim(self, first.comment + second.comment)
        # And the escalation ends the episode: it carries no retry marker, so
        # the sweep stops instead of dispatching a third run.
        thread.append({"user": {"login": worker}, "body": second.comment})
        self.assertEqual(
            fix_dead_run.consecutive_prior_markers(
                thread, fix_dead_run.TURN_CAP_TAG
            ),
            1,
        )

    def test_push_clears_the_exhaustion_run(self):
        worker = "agent-bureau-bot[bot]"
        thread = [
            {"user": {"login": worker},
             "body": f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps"},
            {"user": {"login": worker},
             "body": "🔧 Fix attempt 2 pushed — CI and critic review re-running."},
            {"user": {"login": worker},
             "body": f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps"},
        ]
        self.assertEqual(
            fix_dead_run.consecutive_prior_markers(
                thread, fix_dead_run.TURN_CAP_TAG
            ),
            1,
        )

    def test_forged_exhaustion_marker_is_invisible(self):
        # DRE-1995 discipline carries over to the new marker.
        thread = [
            {"user": {"login": "mallory"},
             "body": f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps"},
        ]
        self.assertEqual(
            fix_dead_run.consecutive_prior_markers(
                thread, fix_dead_run.TURN_CAP_TAG
            ),
            0,
        )

    def test_the_two_markers_are_not_substrings_of_each_other(self):
        # Counting is substring-based (dead_run.RESET_TAG's lesson): either
        # containment would make one class silently spend the other's budget.
        self.assertNotIn(fix_dead_run.OUTAGE_TAG, fix_dead_run.TURN_CAP_TAG)
        self.assertNotIn(fix_dead_run.TURN_CAP_TAG, fix_dead_run.OUTAGE_TAG)

    def test_exhaustion_marker_does_not_count_as_a_model_death(self):
        thread = [
            {"user": {"login": "agent-bureau-bot[bot]"},
             "body": f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps"},
        ]
        self.assertEqual(fix_dead_run.consecutive_prior_deaths(thread), 0)


class GenuineApiDeathUnchangedTest(unittest.TestCase):
    """The outage path must be exactly as it was — same cap, same wording."""

    def test_outage_still_retries_with_the_model_death_marker(self):
        d = fix_dead_run.decide(OUTAGE, 0)
        self.assertEqual(d.action, "retry")
        self.assertIn(fix_dead_run.OUTAGE_TAG, d.comment)
        self.assertIn("AI service", d.comment)

    def test_outage_still_holds_at_the_retry_cap(self):
        d = fix_dead_run.decide(OUTAGE, fix_dead_run.RETRY_CAP)
        self.assertEqual(d.action, "hold")
        self.assertIn("AI service", d.comment)
        self.assertTrue(d.comment.startswith("🛑"))

    def test_outage_retries_are_still_capped_at_two(self):
        self.assertEqual(fix_dead_run.RETRY_CAP, 2)
        self.assertEqual(fix_dead_run.decide(OUTAGE, 1).action, "retry")

    def test_ran_but_pushed_nothing_still_escalates(self):
        self.assertEqual(fix_dead_run.decide(RAN, 0).action, "escalate")
        self.assertEqual(fix_dead_run.decide(None, 0).action, "escalate")


class FixCliTest(unittest.TestCase):
    def _run(self, payload, comments=None, extra=()):
        with tempfile.TemporaryDirectory() as td:
            exec_path = os.path.join(td, "out.json")
            with open(exec_path, "w") as f:
                json.dump(payload, f)
            args = ["decide", exec_path]
            if comments is not None:
                cpath = os.path.join(td, "comments.json")
                with open(cpath, "w") as f:
                    json.dump(comments, f)
                args += ["--comments-json", cpath]
            p = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "fix_dead_run.py"),
                 *args, *extra],
                capture_output=True, text=True,
            )
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.split("\n")
        return lines[0], "\n".join(lines[2:])

    def _c(self, body, login="agent-bureau-bot[bot]"):
        return {"user": {"login": login}, "body": body}

    def test_cli_retries_the_first_exhaustion(self):
        action, body = self._run(EXHAUSTED, comments=[])
        self.assertEqual(action, "retry-turns")
        self.assertIn(fix_dead_run.TURN_CAP_TAG, body)
        assert_no_outage_claim(self, body)

    def test_cli_holds_the_second_exhaustion(self):
        action, body = self._run(
            EXHAUSTED,
            comments=[self._c(f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps")],
        )
        self.assertEqual(action, "hold-turns")
        assert_no_outage_claim(self, body)

    def test_cli_counts_the_two_classes_separately(self):
        # Two prior OUTAGE deaths (at the outage cap) plus a first turn
        # exhaustion: the exhaustion is retried, not held on someone else's
        # exhausted budget.
        outage = self._c(f"⚡ {fix_dead_run.OUTAGE_TAG}: the fix run died")
        action, _ = self._run(EXHAUSTED, comments=[outage, outage])
        self.assertEqual(action, "retry-turns")

    def test_cli_outage_path_unchanged(self):
        action, body = self._run(OUTAGE, comments=[])
        self.assertEqual(action, "retry")
        self.assertIn(fix_dead_run.OUTAGE_TAG, body)


class BuildRunTurnExhaustionTest(unittest.TestCase):
    """agent-task's dead-run requeue (dead_run.decide)."""

    FACTS = "the 60-turn cap after 60 turns and $4.72"

    def decide(self, prior=0, **kw):
        return dead_run.decide(
            prior, turn_exhaustion=True, turn_facts=self.FACTS, **kw
        )

    def test_first_exhaustion_requeues(self):
        d = self.decide(0)
        self.assertEqual(d.action, "requeue")
        self.assertIn(dead_run.TURN_TAG, d.comments[0])

    def test_requeue_spends_no_dead_run_strike(self):
        # Acceptance: a turn-exhausted build run does not consume a
        # `dead run N/3` strike. The count is substring-based, so the receipt
        # must not carry the dead-run tag at all.
        body = self.decide(0).comments[0]
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertNotIn("dead run", body)

    def test_requeue_records_no_model_error_marker(self):
        # Acceptance: no `model-error:` marker — a budget ceiling is not a
        # model fault, and the DRE-1354 fallback must not switch models on it.
        body = self.decide(0, error_model="claude-opus-5").comments[0]
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)
        self.assertNotIn("claude-opus-5", body)

    def test_requeue_message_says_the_agent_ran_out_of_steps(self):
        body = self.decide(0, run_url="https://runs/32791846359").comments[0]
        self.assertIn("ran out of steps", body)
        self.assertIn("60-turn cap", body)
        self.assertIn("$4.72", body)
        self.assertIn("https://runs/32791846359", body)
        assert_no_outage_claim(self, body)

    def test_second_exhaustion_holds_with_the_real_reason(self):
        d = self.decide(dead_run.TURN_REQUEUE_CAP)
        self.assertEqual(d.action, "hold")
        body = d.comments[0]
        self.assertIn("ran out of steps", body)
        self.assertIn("split", body)
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)
        assert_no_outage_claim(self, body)

    def test_exhaustion_cap_is_one_requeue(self):
        self.assertEqual(dead_run.TURN_REQUEUE_CAP, 1)

    def test_tags_are_not_substrings_of_each_other(self):
        self.assertNotIn(dead_run.DEAD_TAG, dead_run.TURN_TAG)
        self.assertNotIn(dead_run.TURN_TAG, dead_run.DEAD_TAG)
        self.assertNotIn(dead_run.RESET_TAG, dead_run.TURN_TAG)
        self.assertNotIn(dead_run.TURN_TAG, dead_run.RESET_TAG)

    def test_cancelled_still_wins(self):
        # DRE-2074: a killed run is not a death of any class.
        d = dead_run.decide(0, turn_exhaustion=True, cancelled=True)
        self.assertEqual(d.action, "defer")

    def test_api_death_path_unchanged(self):
        d = dead_run.decide(0, is_error=True, error_model="claude-opus-5")
        self.assertEqual(d.action, "requeue")
        self.assertIn(dead_run.DEAD_TAG, d.comments[0])
        self.assertIn("API/model error", d.comments[0])
        self.assertIn(
            f"{dead_run.ERROR_MARKER_PREFIX} claude-opus-5", d.comments[0]
        )

    def test_cli_turn_exhaustion_reads_the_execution_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "out.json")
            with open(path, "w") as f:
                json.dump(EXHAUSTED, f)
            p = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "dead_run.py"),
                 "decide", "0", "--turn-exhaustion", "--execution-file", path,
                 "--run-url", "https://runs/9"],
                capture_output=True, text=True,
            )
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.split("\n")
        action, body = lines[0], "\n".join(lines[2:])
        self.assertEqual(action, "requeue")
        self.assertIn(dead_run.TURN_TAG, body)
        self.assertIn("60-turn cap", body)
        assert_no_outage_claim(self, body)


class ReconcileRetriesExhaustedFixRunsTest(unittest.TestCase):
    """The promised retry has to come from somewhere: nothing event-driven
    re-fires agent-fix once the REQUEST_CHANGES trigger is consumed."""

    def _pr(self, comments):
        return {
            "number": 170,
            "headRefName": "agent/DRE-2312-x",
            "mergeStateStatus": "BLOCKED",
            "comments": [
                {"author": {"login": login}, "body": body}
                for login, body in comments
            ],
        }

    def sweep(self, prs):
        calls = []

        def gh(*args):
            if args[:2] == ("run", "list"):
                return "[]"
            if args[:2] == ("pr", "list"):
                return json.dumps(prs)
            return ""

        with mock.patch.object(reconcile, "gh", side_effect=gh), \
             mock.patch.object(reconcile, "gh_dispatch",
                               side_effect=lambda *a: calls.append(a)), \
             mock.patch.object(reconcile, "card_parked_for_human",
                               return_value=False):
            reconcile.retry_dead_fix_runs()
        return calls

    def test_dispatches_on_the_turn_exhaustion_marker(self):
        marker = ("agent-bureau-bot",
                  f"⚡ {fix_dead_run.TURN_CAP_TAG}: the fix run ran out of steps")
        calls = self.sweep([self._pr([marker])])
        self.assertEqual(len(calls), 1)
        self.assertIn("pr_number=170", " ".join(calls[0]))

    def test_no_dispatch_after_the_exhaustion_hold(self):
        # The hold carries no marker — the sweep must stop, not spend a third
        # run on a task that already proved it needs splitting.
        hold = fix_dead_run.decide(EXHAUSTED, 0, prior_exhaustions=1).comment
        marker = ("agent-bureau-bot",
                  f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps")
        self.assertEqual(
            self.sweep([self._pr([marker, ("agent-bureau-bot", hold)])]), []
        )


class WorkflowWiringTest(unittest.TestCase):
    """The workflows must actually route on the classification."""

    @staticmethod
    def emitted(shell: str) -> str:
        """The step's shell minus its `#` comment lines.

        These assertions are about what the pipeline SAYS — the comment lines
        explaining why the outage wording moved are not messages anyone is
        told, and matching them would make the pin vacuous in both directions.
        """
        return "\n".join(
            line for line in shell.splitlines() if not line.strip().startswith("#")
        )

    def report_step(self, name: str) -> str:
        src = workflow(name)
        m = re.search(
            r"name:\s*Report result to Linear\b(.*?)(?:\n      - name:|\Z)",
            src, re.S,
        ) if name == "agent-task.yml" else re.search(
            r"name:\s*Report\b(.*?)(?:\n      - name:|\Z)", src, re.S
        )
        self.assertIsNotNone(m, f"Report step not found in {name}")
        return m.group(1)

    def test_agent_task_has_no_second_inline_is_error_test(self):
        # The DRE-2695 bug: agent-task.yml ran its own
        # `(e or {}).get('is_error') is True` instead of the shared
        # classifier, so fixing the predicate alone left this path wrong.
        src = workflow("agent-task.yml")
        self.assertNotIn("get('is_error')", src)
        self.assertNotIn('get("is_error")', src)

    def test_agent_task_classifies_through_the_shared_cli(self):
        step = self.report_step("agent-task.yml")
        self.assertIn("check_agent_result.py classify", step)
        self.assertIn("turn_exhaustion", step)

    def test_agent_task_counts_the_exhaustion_tag_separately(self):
        step = self.report_step("agent-task.yml")
        self.assertIn(dead_run.TURN_TAG, step)
        self.assertIn("--turn-exhaustion", step)
        # And it still passes the death budget's own reset marker, so an
        # un-parked card gets a fresh set of attempts on both counters.
        self.assertIn(dead_run.RESET_TAG, step)

    def test_agent_task_only_stamps_model_error_on_an_api_death(self):
        step = self.report_step("agent-task.yml")
        m = re.search(r'--is-error --error-model[^\n]*', step)
        self.assertIsNotNone(m, "model-error flags not found")
        # The flag pair must sit inside an api_death branch, never in the
        # unconditional path a turn exhaustion also reaches.
        self.assertIn("api_death", step)

    def test_plan_records_model_error_only_for_an_api_death(self):
        # The planner's medic rerun swings models off this marker. A turn
        # exhaustion must not arm it.
        src = workflow("plan.yml")
        self.assertNotIn("c.is_error_death(e)", src)
        self.assertIn("check_agent_result.py classify", src)
        self.assertIn("api_death", src)

    def test_agent_fix_handles_both_exhaustion_actions(self):
        step = self.report_step("agent-fix.yml")
        self.assertIn('"$ACTION" = "retry-turns"', step)
        self.assertIn('"$ACTION" = "hold-turns"', step)

    def test_agent_fix_card_text_for_exhaustion_says_steps_not_outage(self):
        # Acceptance: the CARD message (what the operator reads in Linear)
        # states the agent ran out of steps and claims nothing about the AI
        # service. The outage wording stays on the outage branches.
        step = self.report_step("agent-fix.yml")
        for action in ("retry-turns", "hold-turns"):
            m = re.search(
                r'elif \[ "\$ACTION" = "%s" \]; then(.*?)\n              (?:elif|else)\b'
                % action,
                step, re.S,
            )
            self.assertIsNotNone(m, f"{action} branch not found")
            branch = self.emitted(m.group(1))
            self.assertIn("ran out of steps", branch)
            assert_no_outage_claim(self, branch)

    def test_agent_fix_exhaustion_retry_never_parks(self):
        step = self.report_step("agent-fix.yml")
        m = re.search(
            r'elif \[ "\$ACTION" = "retry-turns" \]; then(.*?)\n              (?:elif|else)\b',
            step, re.S,
        )
        self.assertIsNotNone(m)
        branch = self.emitted(m.group(1))
        self.assertNotIn("park_for_human", branch)
        self.assertNotIn("needs-human", branch)

    def test_agent_fix_exhaustion_hold_parks_for_a_human(self):
        step = self.report_step("agent-fix.yml")
        m = re.search(
            r'elif \[ "\$ACTION" = "hold-turns" \]; then(.*?)\n              (?:elif|else)\b',
            step, re.S,
        )
        self.assertIsNotNone(m)
        self.assertIn("park_for_human", m.group(1))


if __name__ == "__main__":
    unittest.main()
