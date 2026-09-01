"""RED-first tests: a platform fault is not a strike against the card (DRE-2931).

On 2026-09-01 DRE-2911 spent two of its three dead-run strikes on runs that
never reached a model. The Linear workspace key was at its 2,500/hour ceiling,
so every call returned HTTP 400; attempts 2 and 3 died at the `Card → In
Progress` state write, ~20 seconds in, having spent $0 and taken zero turns.
Both were recorded as `agent died with API/model error (is_error)` with a
`model-error: claude-opus-5` marker, and the third one parked the card in
Backlog with the `needs-human` label — "a human must split/fix the card".

Three separate wrongs in that record, and this file pins all three plus the
park that half-applied:

  1. **The strike.** A run that fails BEFORE the agent starts consumed no
     attempt at the work, so it must not spend the budget that decides whether
     a card needs splitting. The discriminator is on disk: no execution result
     at all, or one reporting `num_turns: 0`. GitHub's own step outcome is
     better still — a `skipped` agent step is the platform saying the agent
     never ran.
  2. **The marker.** `model-error:` exists so the DRE-1354 ladder tries a
     DIFFERENT model next time. No model was called, so the marker sends the
     next attempt down a path that cannot help and tells a human the model
     failed when the quota did.
  3. **The class.** A Linear `RATELIMITED` failure is a WAIT, not a failure of
     the card (DRE-2923's operator decision: a quota wait is UNKNOWN, not
     FAILED). It counts nothing, writes no marker, and parks nothing.
  4. **The park.** DRE-2911's park half-applied — the `needs-human` label write
     landed and the state write to Backlog did not, so the card sat In Progress
     for seven and a half hours while its own comment said it was in Backlog.
     Nothing retried, because from the pipeline's side the park was done. A
     park writes BOTH or it writes NEITHER and says so.

The turn-cap death (attempt 1: 151 turns, $18.37) is the control case — it is
the one strike DRE-2911 legitimately earned, and it must still be counted,
still name its turn count and its dollar cost, and never be described as "hung
or lost".
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import check_agent_result  # noqa: E402
import dead_run  # noqa: E402
import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import model_fallback  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")


def workflow(name: str) -> str:
    return open(os.path.join(WORKFLOWS, name)).read()


# ── the DRE-2911 run trio, from the receipts ────────────────────────────────
# Attempt 1 (run 33468806067): a real turn-cap death. 20 minutes, 151 turns
# against a 150 cap, $18.37. The ONE strike the card actually earned.
TURN_CAP_DEATH = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "duration_ms": 1_200_000,
    "num_turns": 151,
    "total_cost_usd": 18.37,
    "terminal_reason": "max_turns",
    "result": "Reached maximum number of turns (150)",
}

# Attempts 2 and 3: the run died at `Card → In Progress` on a Linear HTTP 400.
# claude-code-action never ran, so there is NO execution result at all.
PRE_AGENT = None

# The other shape the card names: an execution record that exists but reports
# zero turns — the action started and the agent never took a step.
ZERO_TURNS = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 900,
    "num_turns": 0,
    "total_cost_usd": 0,
}

# A genuine transport/auth death (DRE-2365's positive outage signature): the
# model WAS called and answered 401. One turn, so the agent DID start — this
# still counts, and this is the line the new class must not cross.
API_DEATH = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 412,
    "num_turns": 1,
    "total_cost_usd": 0,
    "api_error_status": 401,
    "result": "Invalid bearer token",
}

# What the Linear client logs when the workspace quota is exhausted — the real
# shape, composed by linear_ops._api_error (DRE-2923). medic_classify requires
# the host and the fingerprint on the SAME line, so this is one line.
RATELIMIT_LOG = (
    "Traceback (most recent call last):\n"
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql: rate limited: 2500 requests/hour "
    'exhausted — body: \'{"errors":[{"extensions":{"code":"RATELIMITED"}}]}\'\n'
)

# The same step failing for a reason that is NOT a quota exhaustion.
PLAIN_FAILURE_LOG = (
    "linear_ops.LinearError: Linear API returned 500 from "
    "https://api.linear.app/graphql: 'internal error'\n"
)


class AgentStartedTest(unittest.TestCase):
    """The discriminator: did this run consume an attempt at the work?"""

    def test_no_execution_result_at_all_means_the_agent_never_started(self):
        self.assertFalse(check_agent_result.agent_started(PRE_AGENT))

    def test_zero_turns_means_the_agent_never_started(self):
        self.assertFalse(check_agent_result.agent_started(ZERO_TURNS))

    def test_a_skipped_agent_step_means_the_agent_never_started(self):
        # GitHub's own outcome for a step a prior failure prevented from
        # running. The strongest possible evidence, and it is free.
        self.assertFalse(
            check_agent_result.agent_started(
                TURN_CAP_DEATH, claude_outcome="skipped"
            )
        )

    def test_a_turn_cap_death_started(self):
        self.assertTrue(check_agent_result.agent_started(TURN_CAP_DEATH))

    def test_a_one_turn_api_death_started(self):
        # DRE-2365's outage signature: the model WAS reached and refused. This
        # is a death of the run, it counts, and the new class must not eat it.
        self.assertTrue(check_agent_result.agent_started(API_DEATH))

    def test_a_pushed_branch_proves_the_agent_started(self):
        # No execution file, but a branch with commits on it: the agent ran.
        # Absence of the result file alone is not proof of absence — some
        # action versions move it (check_agent_result's own docstring).
        self.assertTrue(
            check_agent_result.agent_started(
                None, branch_exists=True, claude_outcome="failure"
            )
        )

    def test_a_result_record_without_a_turn_count_started(self):
        # The DRE-1346 legacy shape carries no num_turns. A result record at
        # all means the action produced one, so the agent ran.
        self.assertTrue(
            check_agent_result.agent_started({"subtype": "success",
                                              "is_error": True})
        )


class StartedCliTest(unittest.TestCase):
    """The form the workflow calls — one predicate, no inline shell test."""

    def _started(self, payload, *extra):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "out.json")
            if payload is not None:
                with open(path, "w") as f:
                    json.dump(payload, f)
            p = subprocess.run(
                [sys.executable,
                 os.path.join(SCRIPTS, "check_agent_result.py"),
                 "started", path, *extra],
                capture_output=True, text=True,
            )
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_cli_says_no_for_a_missing_execution_file(self):
        self.assertEqual(self._started(None), "no")

    def test_cli_says_no_for_zero_turns(self):
        self.assertEqual(self._started(ZERO_TURNS), "no")

    def test_cli_says_no_for_a_skipped_agent_step(self):
        self.assertEqual(
            self._started(TURN_CAP_DEATH, "--claude-outcome", "skipped"), "no"
        )

    def test_cli_says_yes_for_a_run_that_worked(self):
        self.assertEqual(self._started(TURN_CAP_DEATH), "yes")
        self.assertEqual(self._started(API_DEATH), "yes")

    def test_cli_says_yes_when_a_branch_exists(self):
        self.assertEqual(
            self._started(None, "--branch", "agent/DRE-2911-x"), "yes"
        )


class GateDoesNotFailOnASkippedAgentStepTest(unittest.TestCase):
    """The result gate: no branch and no PR is EXPECTED when the agent never
    ran, so it is not a silent death and must not go red into the medic."""

    def test_skipped_agent_step_is_not_a_silent_death(self):
        self.assertIsNone(
            check_agent_result.failure_reason(
                None, branch_exists=False, claude_outcome="skipped"
            )
        )

    def test_a_real_silent_death_still_fails_the_gate(self):
        self.assertEqual(
            check_agent_result.failure_reason(
                {"is_error": False, "num_turns": 40},
                branch_exists=False, claude_outcome="success",
            ),
            "no agent branch, no PR, no blocker note, and no escalation note",
        )


class PreAgentDecisionTest(unittest.TestCase):
    """dead_run.decide: a run that never reached the agent."""

    def decide(self, **kw):
        return dead_run.decide(0, pre_agent=True, **kw)

    def test_action_is_an_infrastructure_fault_not_a_death(self):
        self.assertEqual(self.decide().action, "infra")

    def test_it_spends_no_dead_run_strike(self):
        # The count is substring-based, so the receipt must not carry the tag
        # at all — this is AC 1, in one assertion.
        body = self.decide(run_url="https://runs/2").comments[0]
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertNotIn(dead_run.TURN_TAG, body)

    def test_it_writes_no_model_error_marker(self):
        # AC 3: `model-error:` only when a model was actually called. Even
        # asked to record one, a pre-agent fault refuses.
        body = self.decide(is_error=True, error_model="claude-opus-5").comments[0]
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)
        self.assertNotIn("claude-opus-5", body)
        self.assertIsNone(model_fallback.last_error_model([body]))

    def test_it_never_claims_a_model_or_api_error(self):
        body = self.decide(is_error=True, error_model="claude-opus-5").comments[0]
        for claim in ("API/model error", "is_error", "AI service"):
            self.assertNotIn(claim, body)

    def test_it_names_the_step_that_failed(self):
        body = self.decide(failed_step="Card → In Progress").comments[0]
        self.assertIn("Card → In Progress", body)

    def test_it_degrades_without_a_step_name(self):
        body = self.decide().comments[0]
        self.assertNotIn("None", body)
        self.assertIn("before the agent", body)

    def test_it_says_the_fault_is_the_runs_not_the_cards(self):
        body = self.decide(failed_step="Card → In Progress").comments[0]
        self.assertIn("not", body.lower())
        self.assertIn("card", body.lower())

    def test_it_never_parks_even_at_the_cap(self):
        # AC 4/6: the cap is irrelevant to a fault the card did not cause.
        for prior in (0, dead_run.REQUEUE_CAP, dead_run.REQUEUE_CAP + 5):
            d = dead_run.decide(prior, pre_agent=True)
            self.assertEqual(d.action, "infra", f"prior={prior}")
            self.assertNotIn(dead_run.HOLD_LABEL, d.comments[0])

    def test_a_cancelled_run_still_wins(self):
        # DRE-2074's answer is unchanged and still the most specific.
        self.assertEqual(
            dead_run.decide(0, pre_agent=True, cancelled=True).action, "defer"
        )

    def test_it_wins_over_the_turn_and_api_classes(self):
        # A run that never started cannot have exhausted turns or killed a
        # model, whatever else the caller passes.
        self.assertEqual(
            dead_run.decide(0, pre_agent=True, turn_exhaustion=True,
                            turn_facts="the 150-turn cap").action,
            "infra",
        )
        self.assertEqual(
            dead_run.decide(0, pre_agent=True, is_error=True,
                            error_model="claude-opus-5").action,
            "infra",
        )


class RateLimitedDecisionTest(unittest.TestCase):
    """AC 4: a Linear RATELIMITED failure is its own class — a WAIT."""

    def decide(self, **kw):
        return dead_run.decide(0, pre_agent=True, rate_limited=True, **kw)

    def test_it_is_reported_as_an_infrastructure_wait(self):
        d = self.decide(failed_step="Card → In Progress")
        self.assertEqual(d.action, "infra")
        body = d.comments[0]
        self.assertIn("quota", body.lower())
        self.assertIn("Linear", body)

    def test_it_counts_no_strike_and_writes_no_marker(self):
        body = self.decide(is_error=True, error_model="claude-opus-5").comments[0]
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertNotIn(dead_run.TURN_TAG, body)
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)

    def test_it_does_not_park_the_card(self):
        for prior in (0, dead_run.REQUEUE_CAP + 3):
            d = dead_run.decide(prior, pre_agent=True, rate_limited=True)
            self.assertEqual(d.action, "infra")
            self.assertNotIn(dead_run.HOLD_LABEL, d.comments[0])
            self.assertNotIn("Backlog", d.comments[0])

    def test_it_does_not_blame_the_card_or_ask_for_a_split(self):
        body = self.decide().comments[0]
        self.assertNotIn("split", body.lower())
        self.assertNotIn("needs-human", body)

    def test_the_wait_reads_differently_from_a_plain_infra_fault(self):
        # Two different facts with two different next actions (the
        # console-honesty rule): "wait, it refills" is not "a step broke".
        self.assertNotEqual(
            self.decide().comments[0],
            dead_run.decide(0, pre_agent=True).comments[0],
        )

    def test_the_rate_limit_fingerprint_is_the_shared_one(self):
        # The classifier the workflow uses is medic_classify's, not a second
        # copy of the same regex (DRE-2923 owns that string).
        self.assertTrue(medic_classify.is_linear_rate_limited(RATELIMIT_LOG))
        self.assertFalse(medic_classify.is_linear_rate_limited(PLAIN_FAILURE_LOG))
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", RATELIMIT_LOG),
            "linear_ratelimited",
        )


class TurnCapDeathStillCountsTest(unittest.TestCase):
    """AC 2: the one strike DRE-2911 earned, told correctly."""

    def facts(self):
        return check_agent_result.turn_exhaustion_facts(TURN_CAP_DEATH)

    def test_the_receipt_carries_the_turn_count_and_the_dollar_cost(self):
        body = dead_run.decide(
            0, turn_exhaustion=True, turn_facts=self.facts()
        ).comments[0]
        self.assertIn("151 turns", body)
        self.assertIn("$18.37", body)
        self.assertIn("150-turn cap", body)

    def test_it_is_described_as_turn_exhaustion_not_as_hung_or_lost(self):
        body = dead_run.decide(
            0, turn_exhaustion=True, turn_facts=self.facts()
        ).comments[0]
        self.assertIn("ran out of steps", body)
        self.assertNotIn("hung", body)
        self.assertNotIn("lost", body)
        self.assertNotIn("API/model error", body)

    def test_it_still_spends_its_own_budget(self):
        # It ran. It counted. Removing the strike is not the fix.
        self.assertIn(
            dead_run.TURN_TAG,
            dead_run.decide(0, turn_exhaustion=True,
                            turn_facts=self.facts()).comments[0],
        )

    def test_a_turn_cap_death_is_never_a_pre_agent_fault(self):
        self.assertTrue(check_agent_result.agent_started(TURN_CAP_DEATH))
        self.assertEqual(
            check_agent_result.classify_death(TURN_CAP_DEATH),
            check_agent_result.DEATH_TURN_EXHAUSTION,
        )


class Dre2911TrioTest(unittest.TestCase):
    """AC 6: the three runs, driven end to end through the real decision, with
    the counts read back the way linear_ops.count_comments reads them."""

    def _thread(self):
        """The card's comment thread after the three DRE-2911 runs."""
        bodies = []
        # Attempt 1 — the turn-cap death. Its own budget, its own tag.
        bodies.append(
            dead_run.decide(
                dead_run.count_of(bodies, dead_run.TURN_TAG),
                turn_exhaustion=True,
                turn_facts=check_agent_result.turn_exhaustion_facts(
                    TURN_CAP_DEATH
                ),
                run_url="https://runs/33468806067",
            ).comments[0]
        )
        # Attempts 2 and 3 — ~20 seconds, $0, no execution result at all,
        # because the Linear quota was exhausted at `Card → In Progress`.
        actions = []
        for _ in range(2):
            d = dead_run.decide(
                dead_run.count_of(bodies, dead_run.DEAD_TAG),
                pre_agent=True,
                rate_limited=True,
                failed_step="Card → In Progress",
            )
            actions.append(d.action)
            bodies.append(d.comments[0])
        return bodies, actions

    def test_the_turn_cap_death_counts_once(self):
        bodies, _ = self._thread()
        self.assertEqual(dead_run.count_of(bodies, dead_run.TURN_TAG), 1)

    def test_the_two_platform_faults_count_zero(self):
        bodies, _ = self._thread()
        self.assertEqual(dead_run.count_of(bodies, dead_run.DEAD_TAG), 0)

    def test_the_card_is_not_parked(self):
        _, actions = self._thread()
        self.assertEqual(actions, ["infra", "infra"])
        self.assertNotIn("hold", actions)

    def test_no_receipt_names_a_model_as_having_failed(self):
        bodies, _ = self._thread()
        self.assertIsNone(model_fallback.last_error_model(bodies))
        for body in bodies:
            self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)

    def test_the_counts_agree_with_the_real_comment_counter(self):
        # count_of must be what linear_ops.count_comments does, or the number
        # this file asserts is not the number the pipeline reads.
        bodies, _ = self._thread()
        records = [{"body": b} for b in bodies]
        self.assertEqual(
            len([r for r in records if dead_run.DEAD_TAG in r["body"]]), 0
        )
        self.assertEqual(
            len([r for r in records if dead_run.TURN_TAG in r["body"]]), 1
        )

    def test_the_trio_leaves_a_full_death_budget_behind(self):
        # The point of the whole card: after DRE-2911's night, the next real
        # death is strike 1 of 3, not strike 4 of 3.
        bodies, _ = self._thread()
        d = dead_run.decide(
            dead_run.count_of(bodies, dead_run.DEAD_TAG),
            is_error=True, error_model="claude-opus-5",
        )
        self.assertEqual(d.action, "requeue")
        self.assertIn(f"dead run 1/{dead_run.REQUEUE_CAP + 1}", d.comments[0])


class AtomicParkTest(unittest.TestCase):
    """AC 5: a park writes both the label and the state, or neither."""

    def _recorder(self, fail_state=False, fail_label=False, fail_read=False):
        calls = []

        def read_state():
            calls.append(("read", None))
            if fail_read:
                raise linear_ops.LinearError("read failed")
            return "In Progress"

        def write_state(name):
            calls.append(("state", name))
            if fail_state:
                raise linear_ops.LinearError("state write failed")

        def add_label(name):
            calls.append(("label", name))
            if fail_label:
                raise linear_ops.LinearError("label write failed")

        return calls, read_state, write_state, add_label

    def park(self, **kw):
        calls, read_state, write_state, add_label = self._recorder(**kw)
        ok = dead_run.park(
            "DRE-2911",
            read_state=read_state,
            write_state=write_state,
            add_label=add_label,
            log=lambda *a, **k: None,
        )
        return ok, calls

    def test_a_clean_park_writes_the_state_then_the_label(self):
        ok, calls = self.park()
        self.assertTrue(ok)
        self.assertIn(("state", dead_run.PARK_STATE), calls)
        self.assertIn(("label", dead_run.HOLD_LABEL), calls)
        # State FIRST: the label is the marker that stops the sweep, and a
        # label with no state is exactly DRE-2911's seven-and-a-half hours.
        self.assertLess(
            calls.index(("state", dead_run.PARK_STATE)),
            calls.index(("label", dead_run.HOLD_LABEL)),
        )

    def test_a_failing_state_write_writes_no_label_at_all(self):
        # The acceptance criterion, in one test: DRE-2911's park half-applied
        # the other way round and the card sat In Progress claiming Backlog.
        ok, calls = self.park(fail_state=True)
        self.assertFalse(ok)
        self.assertEqual([c for c in calls if c[0] == "label"], [])

    def test_a_failing_state_write_is_retried(self):
        ok, calls = self.park(fail_state=True)
        self.assertFalse(ok)
        self.assertEqual(
            len([c for c in calls if c[0] == "state"]), dead_run.PARK_ATTEMPTS
        )

    def test_a_failing_label_write_rolls_the_state_back(self):
        # The other half of "both or neither": the card must not be left in
        # Backlog without the label, where the promotion gate can pick it up.
        ok, calls = self.park(fail_label=True)
        self.assertFalse(ok)
        self.assertEqual(calls[-1], ("state", "In Progress"))

    def test_an_unreadable_current_state_writes_nothing(self):
        ok, calls = self.park(fail_read=True)
        self.assertFalse(ok)
        self.assertEqual([c for c in calls if c[0] in ("state", "label")], [])

    def test_the_unlanded_note_says_the_park_did_not_happen(self):
        note = dead_run.park_unlanded_comment(run_url="https://runs/9")
        self.assertIn("not", note.lower())
        self.assertIn("Backlog", note)
        self.assertIn(dead_run.HOLD_LABEL, note)
        self.assertIn("https://runs/9", note)

    def test_the_unlanded_note_still_records_the_death(self):
        # The strike was real — the park failing does not un-count it, or the
        # card walks back in with a budget it already spent.
        self.assertIn(dead_run.DEAD_TAG, dead_run.park_unlanded_comment())

    def test_the_park_cli_exits_nonzero_when_it_writes_nothing(self):
        p = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "dead_run.py"), "park",
             "DRE-2911"],
            capture_output=True, text=True,
            env=dict(os.environ, LINEAR_API_KEY=""),
        )
        self.assertNotEqual(p.returncode, 0)


class NothingIsSilentlyUnparkedTest(unittest.TestCase):
    """AC 7: a card already parked on an inflated count stays parked."""

    def test_dead_run_has_no_path_that_clears_the_hold_label(self):
        source = open(os.path.join(SCRIPTS, "dead_run.py")).read()
        self.assertNotIn("remove_label", source)
        self.assertNotIn("remove-label", source)

    def test_the_report_step_never_clears_the_hold_label_or_unparks(self):
        step = report_step("agent-task.yml")
        self.assertNotIn("remove-label", step)
        self.assertNotIn("unpark", step)

    def test_the_reset_marker_is_still_the_only_way_to_refill_the_budget(self):
        # `linear_ops.py unpark` remains a HUMAN act, and this change adds no
        # second door to it.
        self.assertEqual(dead_run.RESET_TAG, "dead-run-budget-reset")
        self.assertIn(dead_run.RESET_TAG, report_step("agent-task.yml"))


def report_step(name: str) -> str:
    src = workflow(name)
    m = re.search(
        r"name:\s*Report result to Linear\b(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    assert m is not None, f"Report step not found in {name}"
    return m.group(1)


def gate_step(name: str) -> str:
    src = workflow(name)
    m = re.search(
        r"name:\s*Gate on agent result\b(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    assert m is not None, f"gate step not found in {name}"
    return m.group(1)


class WorkflowWiringTest(unittest.TestCase):
    """The gate has to actually classify before it counts."""

    def test_the_report_step_asks_whether_the_agent_started(self):
        step = report_step("agent-task.yml")
        self.assertIn("check_agent_result.py started", step)
        self.assertIn("--pre-agent", step)

    def test_the_started_check_reads_githubs_own_step_outcome(self):
        step = report_step("agent-task.yml")
        self.assertIn("--claude-outcome", step)

    def test_the_report_step_names_the_failing_step_from_step_outcomes(self):
        step = report_step("agent-task.yml")
        self.assertIn("--failed-step", step)
        self.assertIn("Card → In Progress", step)
        # Derived from GitHub's own outcomes, not inferred from elapsed time.
        self.assertIn("steps.inprogress.outcome", workflow("agent-task.yml"))

    def test_the_report_step_classifies_the_rate_limit_through_medic_classify(self):
        step = report_step("agent-task.yml")
        self.assertIn("medic_classify.py", step)
        self.assertIn("linear_ratelimited", step)
        self.assertIn("--rate-limited", step)

    def test_the_pre_agent_branch_never_reaches_the_model_error_flags(self):
        step = report_step("agent-task.yml")
        # The `--is-error --error-model` pair must sit behind BOTH the
        # api_death test and the agent-started test.
        m = re.search(r"--is-error --error-model", step)
        self.assertIsNotNone(m)
        before = step[: m.start()]
        self.assertIn('"$AGENT_STARTED" = "no"', before)
        self.assertIn("api_death", before)

    def test_the_pre_agent_branch_counts_no_comments(self):
        # It must not even ASK Linear for the count: under a quota exhaustion
        # that call fails too, and the answer is irrelevant anyway.
        step = report_step("agent-task.yml")
        m = re.search(r'if \[ "\$AGENT_STARTED" = "no" \]; then(.*?)\n            elif',
                      step, re.S)
        self.assertIsNotNone(m, "pre-agent branch not found")
        self.assertNotIn("count-comments", m.group(1))

    def test_the_hold_branch_parks_atomically(self):
        step = report_step("agent-task.yml")
        self.assertIn("dead_run.py park", step)
        # And no longer writes the label and the state as two independent
        # `|| true` calls, either of which can land without the other.
        self.assertNotIn("add-label \"$CARD\" needs-human", step)

    def test_the_gate_step_does_not_fail_on_a_skipped_agent_step(self):
        # A red gate summons the medic to re-run a job whose agent never even
        # started — into the same exhausted quota (the DRE-1921 loop).
        self.assertIn("skipped", check_agent_result.failure_reason.__doc__ or "")

    def test_the_pre_agent_fault_is_reported_against_the_run(self):
        step = report_step("agent-task.yml")
        self.assertIn("::warning", step)
        self.assertIn('"$ACTION" = "infra"', step)

    def test_the_in_progress_step_captures_its_own_output(self):
        src = workflow("agent-task.yml")
        self.assertIn("PRE_AGENT_LOG", src)
        self.assertIn("id: inprogress", src)


if __name__ == "__main__":
    unittest.main()
