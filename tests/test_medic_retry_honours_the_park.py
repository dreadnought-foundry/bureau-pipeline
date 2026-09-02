"""The medic's one retry does not re-dispatch a card the pipeline just parked
(DRE-2954).

Origin — DRE-2937, 2026-09-01. Two mechanisms, each correct on its own,
contradicting each other inside a minute:

  15:50  attempt 1 of run 33568177277 starts
  16:12  it dies at 151 turns and $16.79 with no PR. `turn-exhaustion-requeue`
         1/2 → the card goes back to Todo
  16:13  the requeued run starts
  16:36  it dies at 151 turns and $16.47 with no PR. The turn cap is spent, so
         the card is parked in Backlog with `needs-human`: "this card does not
         fit inside one run; split it"
  16:37  `Pipeline Medic` re-runs 33568177277 as attempt 2. The card goes back
         to In Progress and a THIRD ~$16 build starts on a card the pipeline
         itself declared unbuildable thirty seconds earlier
  16:48  an operator kills it by hand

The park says stop, the medic's retry says go, and nothing joined them. The
retry was also the wrong kind of retry for the failure class: a turn-cap death
is deterministic on the card's SIZE, so re-running the same run with the same
parameters cannot succeed — the rule DRE-1921 wrote for a rate-limited critic
("do not retry into the same wall"), one class along.

What this file pins:

  * a failed run whose card is PARKED is not retried, and one receipt names the
    park (the `needs-human` label, or Backlog plus a `held-for-human` receipt
    newer than the run);
  * a TURN-EXHAUSTION death is never retried, whatever the card's state — the
    death class is read from the run's own execution record, through the
    predicate DRE-2312/DRE-2931 already exposes, never re-derived here;
  * a genuinely transient failure — an infra error, `num_turns: 0`, no
    execution record at all — is still retried exactly once, which is the
    existing behaviour and the thing this card must not regress;
  * the DRE-2937 trio, as a fixture built from the receipts `dead_run.decide()`
    actually writes: two turn-cap deaths then a park, and the medic declines;
  * the decision is written as a receipt through `pipeline_act.py`, like every
    other act.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

import yaml

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import check_act_receipts  # noqa: E402
import check_agent_result  # noqa: E402
import dead_run  # noqa: E402
import medic_retry  # noqa: E402
import pipeline_act  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "medic.yml"
)

# DRE-2937's own numbers. The run started at 15:50 PT; the park landed at 16:36.
RUN_STARTED_AT = "2026-09-01T22:50:00.000Z"
FIRST_DEATH_AT = "2026-09-01T23:12:00.000Z"
PARKED_AT = "2026-09-01T23:36:00.000Z"
RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/33568177277"

# The turn-exhausted execution record the 16:12 and 16:36 runs each wrote:
# `num_turns` at the cap the action reports. This is the shape acceptance
# criterion 2 names — `num_turns >= MAX_TURNS`.
TURN_EXHAUSTED = {
    "is_error": True,
    "subtype": "error_max_turns",
    "num_turns": 151,
    "total_cost_usd": 16.79,
    "duration_ms": 1_320_000,
    "result": "Reached maximum number of turns (150)",
}


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def _dre2937_receipts() -> list:
    """The two receipts DRE-2937's runs actually left, composed by the module
    that writes them.

    Restating the wording here would let the two halves drift apart — the same
    discipline `tests/test_medic_linear_rate_limit.py` applies to the Linear
    client's error line. The park marker this card matches on is dead_run's own
    hold sentence, so the fixture has to BE that sentence.
    """
    first = dead_run.decide(
        0,
        turn_exhaustion=True,
        turn_facts="the 151-turn cap after 151 turns and $16.79",
        run_url=RUN_URL,
    )
    second = dead_run.decide(
        1,
        turn_exhaustion=True,
        turn_facts="the 151-turn cap after 151 turns and $16.47",
        run_url=RUN_URL,
    )
    return [
        {"body": first.comments[0], "created_at": FIRST_DEATH_AT, "action": first.action},
        {"body": second.comments[0], "created_at": PARKED_AT, "action": second.action},
    ]


# ── 1. the card is parked: no retry, and the receipt names the park ──────────
class ParkedCardIsNotRetriedTest(unittest.TestCase):
    def test_the_hold_label_alone_parks_the_card(self):
        reason = medic_retry.park_reason(
            state="In Progress",
            labels=["repo:agent-bureau", dead_run.HOLD_LABEL],
            receipts=[],
            run_started_at=RUN_STARTED_AT,
        )
        self.assertIn(dead_run.HOLD_LABEL, reason)

    def test_backlog_plus_a_hold_receipt_newer_than_the_run_parks_the_card(self):
        reason = medic_retry.park_reason(
            state=dead_run.PARK_STATE,
            labels=[],
            receipts=_dre2937_receipts(),
            run_started_at=RUN_STARTED_AT,
        )
        self.assertTrue(reason, "a park landed after the run started and was missed")
        self.assertIn(PARKED_AT, reason)

    def test_a_hold_receipt_older_than_the_run_is_not_this_runs_park(self):
        """A park from a previous life of the card says nothing about this run.
        The rule is 'newer than the run', and it is load-bearing rather than
        decorative — an old receipt must not freeze every later retry."""
        stale = [
            dict(r, created_at="2026-08-30T10:00:00.000Z")
            for r in _dre2937_receipts()
        ]
        self.assertEqual(
            "",
            medic_retry.park_reason(
                state=dead_run.PARK_STATE,
                labels=[],
                receipts=stale,
                run_started_at=RUN_STARTED_AT,
            ),
        )

    def test_an_ordinary_working_card_is_not_parked(self):
        self.assertEqual(
            "",
            medic_retry.park_reason(
                state="In Progress",
                labels=["repo:agent-bureau", "agent:engineer"],
                receipts=[{"body": "⏳ 3/5 implementation green",
                           "created_at": PARKED_AT}],
                run_started_at=RUN_STARTED_AT,
            ),
        )

    def test_a_parked_card_declines_the_retry(self):
        decision = medic_retry.decide(
            parked_because="it carries the 'needs-human' label"
        )
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_PARKED, decision.rule)

    def test_one_receipt_names_the_park_and_the_rule_applied(self):
        decision = medic_retry.decide(
            parked_because=(
                f"it was parked in {dead_run.PARK_STATE} at {PARKED_AT} by the "
                f"turn cap"
            )
        )
        body = medic_retry.declined_comment(decision, run_url=RUN_URL)
        self.assertIn("not retried", body)
        self.assertIn(PARKED_AT, body)
        self.assertIn(dead_run.PARK_STATE, body)
        self.assertIn(medic_retry.DECLINED_TAG, body)
        self.assertIn(RUN_URL, body)


# ── 2. a turn-cap death is never retried, whatever the card says ─────────────
class TurnExhaustionIsNeverRetriedTest(unittest.TestCase):
    def test_the_fixture_really_is_num_turns_at_the_cap(self):
        """The premise, asserted rather than assumed: this record's `num_turns`
        has reached the cap the action itself names."""
        cap = int(
            check_agent_result._TURN_CAP_NUMBER.search(
                TURN_EXHAUSTED["result"]
            ).group(1)
        )
        self.assertGreaterEqual(TURN_EXHAUSTED["num_turns"], cap)

    def test_the_shared_classifier_is_what_answers(self):
        """DRE-2312/DRE-2931 already expose the class — this card must read it,
        not re-derive an is_error test of its own (which is exactly how
        DRE-2695's turn-exhausted run was reported as a model death)."""
        self.assertTrue(check_agent_result.is_turn_exhaustion(TURN_EXHAUSTED))
        self.assertTrue(medic_retry.is_turn_exhaustion(TURN_EXHAUSTED))

    def test_a_turn_cap_death_declines_the_retry(self):
        decision = medic_retry.decide(execution=TURN_EXHAUSTED)
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_TURN_EXHAUSTION, decision.rule)

    def test_it_declines_regardless_of_the_cards_state(self):
        for parked in ("", "it carries the 'needs-human' label"):
            with self.subTest(parked=bool(parked)):
                decision = medic_retry.decide(
                    parked_because=parked, execution=TURN_EXHAUSTED
                )
                self.assertEqual(medic_retry.DECLINE, decision.action)

    def test_the_death_class_is_read_from_the_runs_own_log(self):
        """The medic holds no execution file — it holds the failed run's log,
        which carries the gate's own printout of that record. Reconstruct it
        from there rather than guessing from prose."""
        execution = medic_retry.execution_from_log(
            _fixture("agent-task-turn-exhaustion-2026-09-01.log")
        )
        self.assertIsNotNone(execution, "the gate's own failure detail was missed")
        self.assertEqual("error_max_turns", execution.get("subtype"))
        self.assertTrue(medic_retry.is_turn_exhaustion(execution))
        self.assertEqual(
            medic_retry.RULE_TURN_EXHAUSTION,
            medic_retry.decide(execution=execution).rule,
        )

    def test_a_turn_receipt_on_the_card_is_the_second_witness(self):
        """`gh run view --log-failed` can come back empty (a 403, an outage),
        and an unreadable log used to mean `normal` — i.e. retry. The card's own
        turn-exhaustion receipt from this run says the same thing."""
        receipts = _dre2937_receipts()
        witness = medic_retry.turn_receipt(receipts, run_started_at=RUN_STARTED_AT)
        self.assertIn(dead_run.TURN_TAG, witness)
        decision = medic_retry.decide(turn_receipt=witness)
        self.assertEqual(medic_retry.RULE_TURN_EXHAUSTION, decision.rule)

    def test_a_turn_receipt_older_than_the_run_is_not_this_runs_death(self):
        stale = [
            dict(r, created_at="2026-08-30T10:00:00.000Z")
            for r in _dre2937_receipts()
        ]
        self.assertEqual(
            "", medic_retry.turn_receipt(stale, run_started_at=RUN_STARTED_AT)
        )

    def test_the_receipt_names_the_turn_cap_as_the_rule(self):
        body = medic_retry.declined_comment(
            medic_retry.decide(execution=TURN_EXHAUSTED), run_url=RUN_URL
        )
        self.assertIn("not retried", body)
        self.assertIn("151", body)
        self.assertIn(medic_retry.DECLINED_TAG, body)


# ── 3. a genuinely transient failure is still retried exactly once ───────────
class TransientFailureIsStillRetriedTest(unittest.TestCase):
    def test_an_infra_flake_with_no_execution_record_is_retried(self):
        log = _fixture("agent-task-infra-flake.log")
        self.assertIsNone(medic_retry.execution_from_log(log))
        decision = medic_retry.decide(
            execution=medic_retry.execution_from_log(log)
        )
        self.assertEqual(medic_retry.RETRY, decision.action)
        self.assertEqual(medic_retry.RULE_NONE, decision.rule)

    def test_a_zero_turn_pre_agent_death_is_retried(self):
        """DRE-2931's shape: the run died before the agent, so it took no turn
        and spent nothing. That is transient, and the medic's one retry is
        exactly right for it."""
        pre_agent = {
            "is_error": True,
            "num_turns": 0,
            "total_cost_usd": 0,
            "duration_ms": 420,
        }
        self.assertFalse(check_agent_result.is_turn_exhaustion(pre_agent))
        self.assertEqual(
            medic_retry.RETRY, medic_retry.decide(execution=pre_agent).action
        )

    def test_an_api_death_is_retried(self):
        api_death = {
            "is_error": True,
            "subtype": "error",
            "num_turns": 1,
            "total_cost_usd": 0,
            "duration_ms": 380,
            "result": "API Error: 401 authentication_error",
        }
        self.assertTrue(check_agent_result.is_api_death(api_death))
        self.assertEqual(
            medic_retry.RETRY, medic_retry.decide(execution=api_death).action
        )

    def test_the_workflow_still_reruns_at_most_once(self):
        """ONCE, still keyed off GitHub's own `run_attempt` — the vendor's
        counter, not a marker we could double-post. This card narrows WHEN the
        one retry fires; it must not change how many there are."""
        jobs = _medic()["jobs"]
        rerunners = [
            name for name, job in jobs.items()
            if "gh run rerun" in yaml.safe_dump(job)
        ]
        self.assertEqual(["retry"], rerunners)
        self.assertIn("run_attempt == 1", jobs["retry"]["if"])
        self.assertIn("run_attempt >= 2", jobs["diagnose"]["if"])

    def test_no_card_at_all_still_retries(self):
        """Most watched workflows carry no card — the scheduled sweep, the test
        suite, the release gate. Their flakes must keep their one retry."""
        self.assertIsNone(medic_retry.card_from_branch("main"))
        self.assertEqual(
            medic_retry.RETRY, medic_retry.decide().action
        )

    def test_a_retry_decision_writes_no_receipt(self):
        with self.assertRaises(ValueError):
            medic_retry.declined_comment(medic_retry.decide(), run_url=RUN_URL)


# ── 4. the DRE-2937 trio, from the receipts the pipeline actually writes ─────
class Dre2937TrioTest(unittest.TestCase):
    def setUp(self):
        self.receipts = _dre2937_receipts()

    def test_the_fixture_is_two_turn_cap_deaths_then_a_park(self):
        self.assertEqual(["requeue", "hold"], [r["action"] for r in self.receipts])
        self.assertEqual(
            2, dead_run.count_of([r["body"] for r in self.receipts], dead_run.TURN_TAG)
        )
        self.assertIn("held-for-human", self.receipts[1]["body"])

    def test_the_park_marker_is_dead_runs_own_wording(self):
        """The marker this card matches on is not a string invented here — it is
        the sentence `dead_run.decide()` writes when a cap is spent. Pinned so a
        reword of that receipt fails here rather than silently un-parking every
        held card as far as the medic is concerned."""
        self.assertIn(medic_retry.HELD_RECEIPT_MARK, self.receipts[1]["body"])

    def test_the_medic_declines_the_third_run(self):
        reason = medic_retry.park_reason(
            state=dead_run.PARK_STATE,
            labels=[dead_run.HOLD_LABEL],
            receipts=self.receipts,
            run_started_at=RUN_STARTED_AT,
        )
        decision = medic_retry.decide(parked_because=reason)
        self.assertEqual(medic_retry.DECLINE, decision.action)
        self.assertEqual(medic_retry.RULE_PARKED, decision.rule)

    def test_the_cli_prints_the_decision_the_workflow_gates_on(self):
        facts = {
            "state": dead_run.PARK_STATE,
            "labels": [dead_run.HOLD_LABEL],
            "comments": self.receipts,
        }
        buf = io.StringIO()
        with mock.patch.object(medic_retry, "card_facts", return_value=facts):
            with contextlib.redirect_stdout(buf):
                rc = medic_retry.main([
                    "decide",
                    "--branch", "agent/DRE-2937-cold-cache",
                    "--log", os.path.join(FIXTURES, "agent-task-infra-flake.log"),
                    "--run-started-at", RUN_STARTED_AT,
                ])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("retry=false", out)
        self.assertIn(f"rule={medic_retry.RULE_PARKED}", out)
        self.assertIn("card=DRE-2937", out)
        self.assertIn("detail=", out)
        # One line per key: a newline in `detail` would write a stray
        # $GITHUB_OUTPUT key.
        self.assertEqual(4, len([ln for ln in out.splitlines() if ln.strip()]))

    def test_the_cli_retries_a_flake_on_an_unparked_card(self):
        facts = {"state": "In Progress", "labels": [], "comments": []}
        buf = io.StringIO()
        with mock.patch.object(medic_retry, "card_facts", return_value=facts):
            with contextlib.redirect_stdout(buf):
                rc = medic_retry.main([
                    "decide",
                    "--branch", "agent/DRE-2937-cold-cache",
                    "--log", os.path.join(FIXTURES, "agent-task-infra-flake.log"),
                    "--run-started-at", RUN_STARTED_AT,
                ])
        self.assertEqual(0, rc)
        self.assertIn("retry=true", buf.getvalue())

    def test_an_unreadable_card_does_not_freeze_the_retry(self):
        """Fail-open on the Linear read, deliberately: a retry into a dead
        Linear costs a cheap pre-agent death (DRE-2931), while a Linear blip
        that disabled every retry would be a new stall. The turn-cap rule below
        it reads the run's own log and still holds."""
        buf = io.StringIO()
        with mock.patch.object(
            medic_retry, "card_facts", side_effect=RuntimeError("linear down")
        ):
            with contextlib.redirect_stdout(buf):
                rc = medic_retry.main([
                    "decide",
                    "--branch", "agent/DRE-2937-cold-cache",
                    "--log", os.path.join(FIXTURES, "agent-task-infra-flake.log"),
                    "--run-started-at", RUN_STARTED_AT,
                ])
        self.assertEqual(0, rc)
        self.assertIn("retry=true", buf.getvalue())

    def test_an_unreadable_card_still_declines_a_turn_cap_death(self):
        buf = io.StringIO()
        with mock.patch.object(
            medic_retry, "card_facts", side_effect=RuntimeError("linear down")
        ):
            with contextlib.redirect_stdout(buf):
                medic_retry.main([
                    "decide",
                    "--branch", "agent/DRE-2937-cold-cache",
                    "--log", os.path.join(
                        FIXTURES, "agent-task-turn-exhaustion-2026-09-01.log"
                    ),
                    "--run-started-at", RUN_STARTED_AT,
                ])
        self.assertIn("retry=false", buf.getvalue())
        self.assertIn(f"rule={medic_retry.RULE_TURN_EXHAUSTION}", buf.getvalue())


# ── 5. the decision is an act, composed through the one writer ───────────────
class TheDecisionIsAnActTest(unittest.TestCase):
    def test_the_act_is_declared_in_the_registry(self):
        self.assertIn(medic_retry.DECLINED_ACT, pipeline_act.acts())
        self.assertEqual(
            medic_retry.DECLINED_TAG, pipeline_act.tag(medic_retry.DECLINED_ACT)
        )
        self.assertEqual("refusal", pipeline_act.kind(medic_retry.DECLINED_ACT))
        self.assertEqual("unchanged", pipeline_act.state(medic_retry.DECLINED_ACT))

    def test_the_registry_still_binds_to_the_code(self):
        self.assertEqual([], pipeline_act.problems())

    def test_every_receipt_still_composes_through_the_one_writer(self):
        self.assertEqual([], check_act_receipts.problems())

    def test_the_act_name_is_spelled_once(self):
        """The composer passes the act as a literal so the emission guard can
        read WHICH act it wraps; this pins that literal to the constant every
        other reader uses, so the two cannot drift apart."""
        with open(
            os.path.join(os.path.dirname(__file__), "..", "scripts", "medic_retry.py"),
            encoding="utf-8",
        ) as f:
            source = f.read()
        self.assertIn(f'receipt("{medic_retry.DECLINED_ACT}"', source)

    def test_the_guard_sees_this_act_composed(self):
        composed = {s.composed_as for s in check_act_receipts.sites()}
        self.assertIn(medic_retry.DECLINED_ACT, composed)

    def test_the_workflow_reaches_the_composer(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("medic_retry.py post", src)


# ── 6. medic.yml: the gate, and the job that says why ────────────────────────
def _medic() -> dict:
    with open(WORKFLOW, encoding="utf-8") as f:
        return yaml.safe_load(f)


class MedicWiringTest(unittest.TestCase):
    def setUp(self):
        self.jobs = _medic()["jobs"]

    def test_classify_publishes_the_retry_decision(self):
        outputs = self.jobs["classify"]["outputs"]
        for key in ("retry", "rule", "card", "detail"):
            self.assertIn(key, outputs, f"classify does not publish {key!r}")
        body = yaml.safe_dump(self.jobs["classify"])
        self.assertIn("medic_retry.py", body)
        self.assertIn("LINEAR_API_KEY", body)

    def test_the_gate_cannot_take_the_whole_medic_down(self):
        """Every medic job `needs: classify`, so a gate step that exits
        non-zero would skip the three back-offs and the diagnosis agent as
        well — one new single point of failure in front of four working ones.
        No answer must degrade to the pre-DRE-2954 behaviour, not to silence."""
        step = next(s for s in self.jobs["classify"]["steps"] if s.get("id") == "r")
        self.assertIn('|| OUT=""', step["run"])
        self.assertIn("::warning::", step["run"])

    def test_no_job_reruns_without_honouring_the_decision(self):
        for name, job in self.jobs.items():
            if "gh run rerun" in yaml.safe_dump(job):
                self.assertIn(
                    "needs.classify.outputs.retry != 'false'",
                    job.get("if", ""),
                    f"{name} reruns without asking whether the card is parked",
                )

    def test_the_declined_job_posts_one_receipt_and_stops(self):
        job = self.jobs["retry_declined"]
        self.assertEqual("classify", job.get("needs"))
        self.assertIn("needs.classify.outputs.retry == 'false'", job["if"])
        body = yaml.safe_dump(job)
        self.assertIn("medic_retry.py post", body)
        # It refuses the retry — it must not take one, nor summon a diagnosis
        # agent, nor go red (a red medic run is an operator email for a
        # decision the pipeline made on purpose).
        self.assertNotIn("gh run rerun", body)
        self.assertNotIn("claude-code-action", body)
        self.assertNotIn("exit 1", body)

    def test_the_declined_job_posts_once_per_failed_run(self):
        self.assertIn("run_attempt == 1", self.jobs["retry_declined"]["if"])


if __name__ == "__main__":
    unittest.main()
