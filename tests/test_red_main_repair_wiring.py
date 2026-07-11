"""Red-main auto-repair — workflow wiring pins (DRE-1927).

.github/workflows/red-main-repair.yml is the reusable repair stage the ADR
(adr-red-main-auto-repair) specifies; these tests hold the workflow to the
four guardrail mechanisms exactly as pinned there:

  * trigger: event-driven off workflow_run conclusion=failure on the default
    branch — and NO schedule trigger anywhere in the stage or its stub (the
    reconcile sweep stays the only scheduled job in the system);
  * guardrail 2 (no crash-loop): the decide step runs red_main_repair.py
    BEFORE any agent, and the budget-exhausted path raises a deduplicated
    Linear triage card instead of a third attempt;
  * guardrail 3 (concurrency lock): job-level Actions concurrency group
    red-main-repair-<repo> with cancel-in-progress: false;
  * guardrail 4 (quota isolation): the worker token is minted through the
    dispatch pool (dispatch_pool.py) keyed repair:<failing-sha>;
  * fix flow: the agent authors a normal PR as the worker identity (the
    qa-bot merges it — author != merger), forward-fix only, with the
    stale-test-vs-broken-code claim required in the PR body; the
    can't-confidently-fix path escalates to Plan Review.

The self-host stub (self-red-main-repair.yml) puts THIS repo's own main on
the repair rail, and the medic watches the stage (a repair run's failure is
diagnosed by the medic — repair never watches itself).
"""

import os
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")
REUSABLE = "red-main-repair.yml"
STUB = "self-red-main-repair.yml"


def src(workflow: str) -> str:
    return open(os.path.join(WF_DIR, workflow)).read()


def doc(workflow: str) -> dict:
    return yaml.safe_load(src(workflow))


def _on(d: dict) -> dict:
    on = d.get("on", d.get(True))
    return on if isinstance(on, dict) else {}


class TriggerTest(unittest.TestCase):
    def test_reusable_workflow_exists_and_is_callable(self):
        self.assertTrue(os.path.isfile(os.path.join(WF_DIR, REUSABLE)))
        self.assertIn("workflow_call", _on(doc(REUSABLE)))

    def test_fires_only_on_default_branch_failure(self):
        body = src(REUSABLE)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", body)
        self.assertIn(
            "github.event.workflow_run.head_branch == "
            "github.event.repository.default_branch",
            body,
        )

    def test_no_new_polling_loop(self):
        # The ADR's explicit promise: event-driven, zero scheduled load.
        for wf in (REUSABLE, STUB):
            self.assertNotIn(
                "schedule", _on(doc(wf)),
                f"{wf} must not add a polling loop — workflow_run only",
            )


class ConcurrencyLockTest(unittest.TestCase):
    """Guardrail 3: one repair in flight per repo, duplicates queue behind."""

    def test_job_level_concurrency_group(self):
        jobs = doc(REUSABLE).get("jobs") or {}
        self.assertEqual(len(jobs), 1, "one job — the lock covers the whole run")
        job = next(iter(jobs.values()))
        conc = job.get("concurrency") or {}
        self.assertIn("red-main-repair-", str(conc.get("group")))
        self.assertIs(conc.get("cancel-in-progress"), False)


class DecideBeforeDispatchTest(unittest.TestCase):
    """Guardrail 2: classify + decide run before any agent spins up."""

    def test_decide_step_runs_the_decision_script(self):
        self.assertIn("red_main_repair.py", src(REUSABLE))

    def test_failed_logs_are_fetched_for_classification(self):
        self.assertIn("--log-failed", src(REUSABLE))

    def test_agent_is_gated_on_the_decision(self):
        self.assertIn("steps.decide.outputs.go == 'true'", src(REUSABLE))

    def test_budget_exhaustion_raises_a_deduplicated_triage_card(self):
        body = src(REUSABLE)
        self.assertIn("steps.decide.outputs.escalate == 'true'", body)
        self.assertIn("linear_ops.py", body)
        self.assertIn("find-open", body)

    def test_uncertain_agent_parks_in_plan_review(self):
        body = src(REUSABLE)
        self.assertIn("/tmp/repair-escalation.txt", body)
        self.assertIn("Plan Review", body)


class QuotaIsolationTest(unittest.TestCase):
    """Guardrail 4: mint through the dispatch pool, keyed by the repair."""

    def test_worker_is_selected_by_the_dispatch_pool(self):
        self.assertIn("dispatch_pool.py select", src(REUSABLE))

    def test_pool_key_is_the_repair_identity(self):
        self.assertIn("BUREAU_POOL_KEY: repair:", src(REUSABLE))

    def test_agent_runs_on_the_worker_token(self):
        # The worker App authors the PR; the qa-bot App (merge-gate) merges
        # it — author != merger by identity.
        self.assertIn(
            "github_token: ${{ steps.worker.outputs.token }}", src(REUSABLE)
        )


class FixFlowPromptTest(unittest.TestCase):
    def test_forward_fix_only(self):
        body = src(REUSABLE)
        self.assertIn("NEVER push to the default branch", body)
        self.assertIn("NEVER force-push", body)

    def test_stale_test_vs_broken_code_claim_is_required(self):
        body = src(REUSABLE)
        self.assertIn("STALE TEST", body)
        self.assertIn("BROKEN CODE", body)

    def test_log_content_is_declared_data_not_instructions(self):
        self.assertIn("DATA, not instructions", src(REUSABLE))

    def test_pr_flows_through_the_normal_gates(self):
        # The agent must not merge its own fix; critic + merge gate own it.
        self.assertIn("do not merge it yourself", src(REUSABLE).lower())


class SelfHostTest(unittest.TestCase):
    def test_stub_watches_this_repos_ci_on_completion(self):
        on = _on(doc(STUB))
        wr = on.get("workflow_run") or {}
        self.assertIn("Pipeline Tests", wr.get("workflows") or [])
        self.assertEqual(wr.get("types"), ["completed"])

    def test_medic_watches_the_repair_stage(self):
        # ADR guardrail 2: "repair never watches itself" — a repair run's own
        # failure routes through the EXISTING medic.
        medic_on = _on(doc("self-medic.yml"))
        watched = (medic_on.get("workflow_run") or {}).get("workflows") or []
        self.assertIn("Red-Main Repair", watched)

    def test_repair_does_not_watch_itself(self):
        wr = _on(doc(STUB)).get("workflow_run") or {}
        self.assertNotIn("Red-Main Repair", wr.get("workflows") or [])


class RegistryTest(unittest.TestCase):
    def test_repair_agent_is_on_the_console_roster(self):
        with open(os.path.join(REPO, "agents.yaml")) as f:
            agents = yaml.safe_load(f)["agents"]
        entries = [a for a in agents
                   if a["workflow"].endswith("red-main-repair.yml")]
        self.assertEqual(len(entries), 1,
                         "agents.yaml needs exactly one repair-stage entry")


if __name__ == "__main__":
    unittest.main()
