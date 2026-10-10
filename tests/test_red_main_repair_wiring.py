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
    can't-confidently-fix path escalates to Triage.

The self-host stub (self-red-main-repair.yml) puts THIS repo's own main on
the repair rail, and the medic watches the stage (a repair run's failure is
diagnosed by the medic — repair never watches itself).
"""

import os
import subprocess
import sys
import tempfile
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

    def test_decide_is_told_which_workflow_went_red(self):
        # The sandbox-block marker is only infra when the HARNESS printed it
        # (run 34258403698), so the classifier needs the workflow's name — and
        # it travels by env like every other untrusted event field (DRE-1996).
        body = src(REUSABLE)
        self.assertIn("--workflow-name \"$WF_NAME\"", body)
        step = [
            s for s in doc(REUSABLE)["jobs"]["repair"]["steps"]
            if "red_main_repair.py decide" in (s.get("run") or "")
        ]
        self.assertTrue(step, "no decide step runs red_main_repair.py decide")
        self.assertEqual(
            (step[0].get("env") or {}).get("WF_NAME"),
            "${{ github.event.workflow_run.name }}",
        )

    def test_agent_is_gated_on_the_decision(self):
        self.assertIn("steps.decide.outputs.go == 'true'", src(REUSABLE))

    def test_budget_exhaustion_raises_a_deduplicated_triage_card(self):
        body = src(REUSABLE)
        self.assertIn("steps.decide.outputs.escalate == 'true'", body)
        self.assertIn("linear_ops.py", body)
        self.assertIn("find-open", body)

    def test_uncertain_agent_parks_in_triage(self):
        body = src(REUSABLE)
        self.assertIn("/tmp/repair-escalation.txt", body)
        self.assertIn("Triage", body)


class RepeatedTimeoutHistoryTest(unittest.TestCase):
    """DRE-4674: `decide` is given a memory, and the fetch that fills it.

    Portico's main failed the same `infra — typecheck & test` step on four
    commits in a row, each retried once, every time on
    `The action 'Test' has timed out after 12 minutes` — and every run read as
    infrastructure. The history is FETCHED (never assumed), it is gathered
    before the decision that reads it, and what the decision learns reaches
    the agent and the human.
    """

    @staticmethod
    def _steps():
        return doc(REUSABLE)["jobs"]["repair"]["steps"]

    @staticmethod
    def _index(steps, needle):
        for i, step in enumerate(steps):
            if needle in (step.get("run") or ""):
                return i
        return -1

    def test_the_history_is_gathered_before_the_decision(self):
        steps = self._steps()
        gather = self._index(steps, "repair_history.py")
        decide = self._index(steps, "red_main_repair.py decide")
        self.assertGreater(gather, -1, "no step gathers the repair history")
        self.assertGreater(decide, -1, "no decide step")
        self.assertLess(gather, decide,
                        "the history must be fetched before decide reads it")

    def test_the_history_is_fetched_from_this_runs_own_workflow(self):
        # The comparison is "the same workflow file", so the listing is keyed
        # on the event's own workflow id and the event's own path — never a
        # literal, which would read one fleet repo's runs for all of them.
        steps = self._steps()
        gather = steps[self._index(steps, "repair_history.py")]
        env = gather.get("env") or {}
        self.assertEqual(env.get("WORKFLOW_ID"),
                         "${{ github.event.workflow_run.workflow_id }}")
        self.assertEqual(env.get("RUN_ATTEMPT"),
                         "${{ github.event.workflow_run.run_attempt }}")
        self.assertEqual(env.get("WORKFLOW_PATH"),
                         "${{ github.event.workflow_run.path }}")
        self.assertEqual(env.get("DEFAULT_BRANCH"),
                         "${{ github.event.repository.default_branch }}")
        # DRE-1996: every untrusted event field travels by env, never
        # interpolated into the script line.
        self.assertNotIn("github.event", gather.get("run") or "")

    def test_the_history_knows_when_this_run_was_created(self):
        # DRE-5069: "a later run on the branch" is read against this run's own
        # created_at, from the event — a re-run keeps it, so attempt 2 of a
        # failed run is still ordered where the commit was.
        steps = self._steps()
        gather = steps[self._index(steps, "repair_history.py")]
        env = gather.get("env") or {}
        self.assertEqual(env.get("RUN_CREATED_AT"),
                         "${{ github.event.workflow_run.created_at }}")
        self.assertIn('--run-created-at "$RUN_CREATED_AT"',
                      gather.get("run") or "")

    def test_decide_is_handed_the_history_file(self):
        body = src(REUSABLE)
        self.assertIn("--history-file /tmp/repair-history.json", body)
        self.assertIn("--out /tmp/repair-history.json", body)

    def test_the_agent_is_told_which_step_which_clock_which_commits(self):
        # A repair dispatched at a repeated timeout starts at the named step
        # with the named limit, instead of re-deriving what the decision knew.
        body = src(REUSABLE)
        for output in ("timeout_job", "timeout_step", "timeout_limit",
                       "timeout_commits"):
            self.assertIn(f"steps.decide.outputs.{output}", body,
                          f"the decision's {output} reaches nothing")
        prompt = [
            s for s in self._steps()
            if "claude-code-action" in (s.get("uses") or "")
        ][0]["with"]["prompt"]
        for output in ("timeout_job", "timeout_step", "timeout_limit",
                       "timeout_commits"):
            self.assertIn(f"steps.decide.outputs.{output}", prompt)

    def test_the_budget_exhausted_card_names_the_step_and_the_clock(self):
        # The human who picks this up is told which step and which clock, not
        # just "main is red" — that card is the whole handover.
        steps = self._steps()
        card = steps[self._index(steps, "linear_ops.py")]
        env = card.get("env") or {}
        self.assertEqual(env.get("TIMEOUT_STEP"),
                         "${{ steps.decide.outputs.timeout_step }}")
        self.assertEqual(env.get("TIMEOUT_JOB"),
                         "${{ steps.decide.outputs.timeout_job }}")
        self.assertEqual(env.get("TIMEOUT_LIMIT"),
                         "${{ steps.decide.outputs.timeout_limit }}")
        self.assertEqual(env.get("TIMEOUT_COMMITS"),
                         "${{ steps.decide.outputs.timeout_commits }}")
        # Read in the script, in either shell form (`$X` / `${X:-default}`) —
        # an env var declared and never read is a card that says nothing.
        run = card.get("run") or ""
        for var in ("TIMEOUT_STEP", "TIMEOUT_JOB", "TIMEOUT_LIMIT",
                    "TIMEOUT_COMMITS"):
            self.assertRegex(run, r"\$\{?" + var)


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


class ReportFinishesTheRepairTest(unittest.TestCase):
    """DRE-6525: a pushed branch with no pull request is opened by the Report
    step itself, before the step fails for the medic."""

    ERROR_LINE = ("echo \"::error::repair agent produced neither a PR nor an "
                  "escalation — failing for medic visibility\"")

    @staticmethod
    def _report():
        steps = doc(REUSABLE)["jobs"]["repair"]["steps"]
        found = [s for s in steps if s.get("name") == "Report"]
        assert len(found) == 1, "exactly one Report step"
        return found[0]

    def test_the_step_runs_the_finish_helper_on_the_branch_and_the_run(self):
        run = self._report()["run"]
        self.assertIn("repair_finish.py finish", run)
        self.assertIn('--branch "$BRANCH"', run)
        self.assertIn('--failed-run-url "$RUN_URL"', run)

    def test_the_agents_own_pull_request_is_still_looked_for_first(self):
        run = self._report()["run"]
        self.assertIn("card_pr.py find", run)
        self.assertLess(run.index("card_pr.py find"),
                        run.index("repair_finish.py finish"))
        self.assertLess(run.index("/tmp/repair-escalation.txt"),
                        run.index("repair_finish.py finish"))

    def test_the_fall_through_still_fails_for_the_medic(self):
        run = self._report()["run"]
        self.assertIn(self.ERROR_LINE, run)
        after = run[run.index(self.ERROR_LINE):]
        self.assertIn("exit 1", after)
        self.assertLess(run.index("repair_finish.py finish"),
                        run.index(self.ERROR_LINE))

    def test_the_step_condition_is_unchanged(self):
        self.assertEqual(self._report().get("if"),
                         "always() && steps.decide.outputs.go == 'true'")

    def test_the_card_url_and_default_branch_reach_the_step_by_env(self):
        env = self._report().get("env") or {}
        self.assertEqual(env.get("CARD_URL"), "${{ steps.card.outputs.card_url }}")
        self.assertEqual(env.get("DEFAULT_BRANCH"),
                         "${{ github.event.repository.default_branch }}")
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.worker.outputs.token }}")


class FinishUnlandedWiringTest(unittest.TestCase):
    """DRE-6526: the workflow hands `decide` the comparison of each record
    branch with the default branch, and acts on `finish-unlanded` by opening
    the pull request through the Report step's own helper."""

    REASON = "steps.decide.outputs.reason == 'finish-unlanded'"

    @staticmethod
    def _steps():
        return doc(REUSABLE)["jobs"]["repair"]["steps"]

    @classmethod
    def _index(cls, needle, key="run"):
        for i, step in enumerate(cls._steps()):
            if needle in (step.get(key) or ""):
                return i
        return -1

    @classmethod
    def _gather(cls):
        return cls._steps()[cls._index("/tmp/repair-compares.json")]

    @classmethod
    def finish_step(cls):
        found = [s for s in cls._steps() if s.get("if") == cls.REASON]
        assert len(found) == 1, "exactly one step acts on finish-unlanded"
        return found[0]

    def test_the_comparisons_are_gathered_between_the_records_and_the_decision(self):
        records = self._index("git/matching-refs/heads/repair/")
        gather = self._index("/tmp/repair-compares.json")
        decide = self._index("red_main_repair.py decide")
        self.assertGreater(records, -1)
        self.assertGreater(gather, -1, "no step writes /tmp/repair-compares.json")
        self.assertNotEqual(gather, decide)
        self.assertLess(records, gather)
        self.assertLess(gather, decide)

    def test_the_gather_matches_refs_through_record_refs(self):
        run = self._gather()["run"]
        self.assertIn("red_main_repair.py record-refs", run)
        self.assertIn("--refs-file /tmp/repair-refs.json", run)
        self.assertIn('--head-sha "$HEAD_SHA"', run)
        self.assertIn("/compare/", run)
        self.assertIn("FETCH-FAILED", run)
        # The regex lives once, in the script.
        self.assertNotIn("DRE-[0-9]", run)

    def test_the_gather_runs_on_the_boot_token_with_event_fields_in_env(self):
        step = self._gather()
        env = step.get("env") or {}
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.app.outputs.token }}")
        self.assertEqual(env.get("HEAD_SHA"),
                         "${{ github.event.workflow_run.head_sha }}")
        self.assertEqual(env.get("DEFAULT_BRANCH"),
                         "${{ github.event.repository.default_branch }}")
        self.assertNotIn("${{", step["run"])

    def test_the_decision_reads_the_comparisons(self):
        run = self._steps()[self._index("red_main_repair.py decide")]["run"]
        self.assertIn("--compares-file /tmp/repair-compares.json", run)

    def test_the_finish_step_runs_the_helper_on_the_decided_branch(self):
        step = self.finish_step()
        run = step["run"]
        self.assertIn("repair_finish.py finish", run)
        for flag in ('--repo "$GITHUB_REPOSITORY"', '--branch "$BRANCH"',
                     '--default-branch "$DEFAULT_BRANCH"',
                     '--failed-run-url "$RUN_URL"',
                     '--workflow-name "$WF_NAME"'):
            self.assertIn(flag, run)
        # The card step did not run; the helper names the card off the branch.
        self.assertNotIn("--card-url", run)
        # DRE-1996: values ride env, never interpolated into the script.
        self.assertNotIn("${{", run)
        env = step.get("env") or {}
        self.assertEqual(env.get("BRANCH"), "${{ steps.decide.outputs.branch }}")
        self.assertEqual(env.get("GH_TOKEN"), "${{ steps.app.outputs.token }}")
        self.assertEqual(env.get("LINEAR_API_KEY"),
                         "${{ secrets.LINEAR_API_KEY }}")
        self.assertEqual(env.get("RUN_URL"),
                         "${{ github.event.workflow_run.html_url }}")
        self.assertEqual(env.get("WF_NAME"),
                         "${{ github.event.workflow_run.name }}")
        self.assertEqual(env.get("DEFAULT_BRANCH"),
                         "${{ github.event.repository.default_branch }}")

    def test_the_finish_step_follows_the_superseded_report(self):
        superseded = self._index("steps.decide.outputs.reason == 'superseded'",
                                 key="if")
        finish = self._index(self.REASON, key="if")
        self.assertGreater(superseded, -1)
        self.assertEqual(finish, superseded + 1)
        self.assertEqual(
            self.finish_step()["name"],
            "Finish — open the pull request the agent pushed and left")

    def test_the_agent_the_card_the_pool_and_the_report_skip_it(self):
        # finish-unlanded is go=false, and each of these is gated on go.
        by_id = {s.get("id"): s for s in self._steps() if s.get("id")}
        for step_id in ("card", "claude", "pool", "worker"):
            self.assertIn("steps.decide.outputs.go == 'true'",
                          by_id[step_id].get("if") or "", step_id)
        report = [s for s in self._steps() if s.get("name") == "Report"][0]
        self.assertIn("steps.decide.outputs.go == 'true'", report.get("if"))

    def test_the_concurrency_comment_names_the_ending(self):
        self.assertIn("finish-unlanded", src(REUSABLE).split("concurrency:")[0])


_FINISH_STUB = """\
import os, sys
open(os.environ["CALLS"], "a").write("repair_finish " + " ".join(sys.argv[1:]) + "\\n")
code = int(os.environ["FAKE_FINISH_RC"])
finish = os.environ["FAKE_FINISH"]
url = os.environ.get("FAKE_FINISH_URL", "") if code == 0 else ""
sys.stdout.write(f"finish={finish}\\npr_url={url}\\ncard=DRE-6511\\n")
sys.exit(code)
"""

BRANCH = "repair/DRE-6511-bcb36adf6037"
PR_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/pull/883"


class FinishStepShellTest(unittest.TestCase):
    """The finish step's `run:` body, executed as GitHub executes it
    (`bash -e`), with the helper stubbed."""

    def run_step(self, *, rc, finish):
        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import step_shell

        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, ".bureau-pipeline", "scripts")
            os.makedirs(scripts)
            with open(os.path.join(scripts, "repair_finish.py"), "w") as fh:
                fh.write(_FINISH_STUB)
            calls = os.path.join(tmp, "calls.txt")
            summary = os.path.join(tmp, "summary.md")
            open(calls, "w").close()
            open(summary, "w").close()
            env = {
                **os.environ,
                "CALLS": calls,
                "FAKE_FINISH_RC": str(rc),
                "FAKE_FINISH": finish,
                "FAKE_FINISH_URL": PR_URL,
                "BRANCH": BRANCH,
                "RUN_URL": "https://github.com/x/y/actions/runs/38003951327",
                "WF_NAME": "Pipeline Tests",
                "DEFAULT_BRANCH": "main",
                "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
                "GITHUB_STEP_SUMMARY": summary,
            }
            body = step_shell.step_shell(FinishUnlandedWiringTest.finish_step())
            done = subprocess.run(["bash", "-e", "-c", body], cwd=tmp, env=env,
                                  capture_output=True, text=True, check=False)
            with open(calls) as fh, open(summary) as sh:
                return done, fh.read(), sh.read()

    def test_exit_0_ends_the_step_green_with_the_url(self):
        done, calls, summary = self.run_step(rc=0, finish="opened")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn(PR_URL, done.stdout)
        self.assertIn(PR_URL, summary)
        self.assertNotIn("::error::", done.stdout)
        self.assertIn(f"--branch {BRANCH}", calls)
        self.assertNotIn("--card-url", calls)

    def test_already_open_ends_green_with_one_call_and_no_second_create(self):
        done, calls, summary = self.run_step(rc=0, finish="already-open")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn(PR_URL, done.stdout)
        self.assertIn("already-open", done.stdout)
        self.assertEqual(calls.count("repair_finish finish"), 1)
        self.assertNotIn("gh pr create", done.stdout + done.stderr)

    def test_exit_2_fails_the_step_for_the_medic(self):
        done, calls, _ = self.run_step(rc=2, finish="unreadable")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(
            f"::error::finish-unlanded: the pull request could not be opened "
            f"for {BRANCH} (finish=unreadable)", done.stdout)
        self.assertIn("repair_finish finish", calls)


class AlreadyOpenIsSafeToRepeatTest(unittest.TestCase):
    """The helper's half of a repeat: a pull request already on the branch is
    `already-open`, and nothing is created."""

    def test_a_second_finish_creates_nothing(self):
        sys.path.insert(0, os.path.join(REPO, "scripts"))
        import repair_finish

        created = []

        class Ops:
            def branch_ref(self, repo, branch):
                return {"ref": f"refs/heads/{branch}"}

            def compare(self, repo, base, head):
                return {"status": "ahead", "ahead_by": 1,
                        "commits": [{"sha": "c" * 40,
                                     "commit": {"message": "fix"}}]}

            def pulls_for_head(self, repo, branch):
                return [{"number": 883, "url": PR_URL, "state": "OPEN"}]

            def create_pr(self, *a, **kw):
                created.append(kw)
                return "unexpected"

            def cmd_comment(self, *a):
                created.append(a)

        result = repair_finish.finish(
            repo="dreadnought-foundry/bureau-pipeline", branch=BRANCH,
            default_branch="main", failed_run_url="u",
            workflow_name="Pipeline Tests", ops=Ops())
        self.assertEqual(result["finish"], "already-open")
        self.assertEqual(result["pr_url"], PR_URL)
        self.assertEqual(created, [])


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
