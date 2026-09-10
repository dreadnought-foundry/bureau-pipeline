"""RED-first tests for the `agent_task_parses` rehearsal (DRE-3486).

WHAT HAPPENED (2026-09-09). `agent-task.yml` — the one workflow that builds
every card in the fleet — became unparseable to GitHub: a `run:` block
carrying `${{ }}` compiles into ONE expression, and that step's script had
reached ~23,260 characters against a hard 21,000 ceiling (DRE-3484). The
integration harness ran on that exact commit at 22:21 PT, **passed**, and
`promote-channel` advanced `stable` onto it. From 06:15 PT every
`agent-execute` dispatch in every repo on the channel was refused before a
job started: three hours and twenty minutes with no build lane.

The harness did nothing wrong — it reported on what it covers. Its sandbox
installs four stubs (`ci`, `qa-review`, `merge-gate`, `reconcile`) and not
`agent-task`, so the workflow that builds every card was the one workflow the
pre-production gate never installed, never parsed and never fired.

WHAT IS PINNED HERE:

  * **The rehearsal FIRES the workflow.** GitHub validates a called workflow
    only at DISPATCH: a file can be valid YAML, pass every linter, and still
    be rejected at the moment it is used. So the rehearsal sends a real
    `agent-execute` `repository_dispatch` at the sandbox and reads what
    GitHub did with it.
  * **A started job is the proof.** `jobs > 0` on the run, and
    `referenced_workflows` naming bureau-pipeline's own `agent-task.yml` with
    a resolved commit — the caller/callee contract holding.
  * **The 2026-09-09 signature is named, not timed out.** A run that
    concludes with `total_count: 0` jobs and `{"billable": {}}` timing fails
    the rehearsal with the words the outage deserves, inside ONE wait budget
    rather than at the 70-minute verdict deadline.
  * **The harness still fits its job cap** with the rehearsal added.
  * **`docs/harness.md` states the intended set.** The absence that caused
    this was invisible because nothing ever wrote down which workflows the
    sandbox covers.
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from harness import framework, github_api  # noqa: E402
from harness.__main__ import select_names  # noqa: E402
from harness.scenarios import discover  # noqa: E402
from harness.scenarios import agent_task_parses as atp  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
DOC = ROOT / "docs" / "harness.md"

# The commit the harness run is proving — harness.yml's `tested` step.
TESTED_SHA = "c0ffee1c0ffee1c0ffee1c0ffee1c0ffee1c0ffe"

# The callee the sandbox stub must reach, WITHOUT the ref.
CALLEE = "dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml"

# ...and the way the LIVE endpoint spells it: the ref is attached to `path`,
# and carried again as `ref`. Sandbox run 34414467787 (2026-09-09, PR #332's
# own proving run) returned exactly this, and the rehearsal — comparing
# against the bare `CALLEE` the REST schema's example shows — rejected a
# correctly wired stub as "does not call this repo's reusable workflow". The
# fixture defaults to the live shape so that cannot recur silently.
CALLEE_AT_REF = f"{CALLEE}@main"


def _run_record(run_id=771, status="in_progress", conclusion=None, sha=TESTED_SHA,
                referenced=True, display_title="agent-execute",
                callee_path=CALLEE_AT_REF):
    """An Actions workflow-run record, REST-shaped.

    `event` is the TRIGGER (`repository_dispatch`), never the dispatch type;
    GitHub keeps the type only as `display_title`. Run 34411760958 in the
    sandbox (2026-09-09 15:20 PT) is the shape copied here.
    """
    record = {
        "id": run_id,
        "name": "Agent Task",
        "path": ".github/workflows/agent-task.yml",
        "event": "repository_dispatch",
        "display_title": display_title,
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-09-09T22:21:04Z",
        "updated_at": "2026-09-09T22:21:41Z",
        "html_url": f"https://github.com/{SANDBOX}/actions/runs/{run_id}",
        "referenced_workflows": [],
    }
    if referenced:
        record["referenced_workflows"] = [
            {"path": callee_path, "sha": sha, "ref": "refs/heads/main"}
        ]
    return record


class FakeSandbox:
    """The sandbox as the rehearsal sees it: a dispatch endpoint, a run
    listing, a run record, a job count and a timing record.

    `job_counts` is consumed one poll at a time, so a test can say "no job
    yet, then a job" or "no job, ever, and then the run concluded".
    """

    def __init__(self, runs_before=(), appears=None, records=(), job_counts=(),
                 timing=None, dispatch_error=None, runs_error=None):
        self.runs_before = list(runs_before)
        self.appears = appears  # the run record the dispatch produces
        self.records = list(records)  # successive get_workflow_run answers
        self.job_counts = list(job_counts)
        self.timing = timing if timing is not None else {"billable": {}}
        self.dispatch_error = dispatch_error
        self.runs_error = runs_error
        self.dispatched = []
        self.cancelled = []
        self.listed_events = []  # every `event=` the rehearsal asked for
        self.job_calls = 0
        self.timing_calls = 0

    # ── the calls the rehearsal makes ────────────────────────────────────
    def repository_dispatch(self, repo, event_type, client_payload):
        if self.dispatch_error:
            raise self.dispatch_error
        self.dispatched.append((repo, event_type, dict(client_payload)))

    def list_workflow_runs_for(self, repo, workflow_file, event=None, per_page=30):
        if self.runs_error:
            raise self.runs_error
        self.listed_events.append(event)
        if not self.dispatched or self.appears is None:
            runs = list(self.runs_before)
        else:
            runs = [self.appears] + list(self.runs_before)
        # GitHub's `event=` filter matches the run's TRIGGER event, exactly;
        # `event=agent-execute` returns an empty page for a run whose event
        # is `repository_dispatch`, which is how attempt 3 on PR #332 timed
        # out with its own run in plain sight (2026-09-09 15:20 PT).
        if event is not None:
            runs = [run for run in runs if run.get("event") == event]
        return runs

    def get_workflow_run(self, repo, run_id):
        if len(self.records) > 1:
            return self.records.pop(0)
        return self.records[0] if self.records else self.appears

    def list_run_jobs(self, repo, run_id):
        self.job_calls += 1
        total = self.job_counts.pop(0) if len(self.job_counts) > 1 else (
            self.job_counts[0] if self.job_counts else 0
        )
        return {
            "total_count": total,
            "jobs": [{"id": 1, "name": "execute"}] * total,
        }

    def run_timing(self, repo, run_id):
        self.timing_calls += 1
        return self.timing

    def cancel_workflow_run(self, repo, run_id):
        self.cancelled.append(run_id)

    # ── cleanup's targeted tidy-up ───────────────────────────────────────
    def list_open_prs(self, repo):
        return []

    def delete_ref(self, repo, branch):
        return False

    def matching_refs(self, repo, prefix):
        return []


def _ctx(gh, **kw):
    """A context whose clock never really sleeps."""
    elapsed = {"t": 0.0}

    def clock():
        return elapsed["t"]

    def sleep(seconds):
        elapsed["t"] += seconds

    ctx = framework.HarnessContext(
        gh=gh,
        repo=SANDBOX,
        run_id="main-20260909-abcdef",
        namespace="main",
        qa_login="agent-bureau-qa-bot[bot]",
        tested_sha=kw.pop("tested_sha", TESTED_SHA),
        poll_interval=1.0,
        clock=clock,
        sleep=sleep,
        log=lambda *a, **k: None,
        **kw,
    )
    ctx.state["elapsed"] = elapsed
    return ctx


class FindingTheRunTest(unittest.TestCase):
    """A `repository_dispatch` answers 204 with no run id, so the rehearsal
    has to find its run afterwards — and attempt 3 on PR #332 (2026-09-09
    15:20 PT) never did: it asked GitHub for `event=agent-execute`, the
    dispatch TYPE, while the run it had fired listed under the trigger event
    `repository_dispatch` with the type only in its title. Ten minutes of
    empty pages, then a timeout, with the run in plain sight."""

    def test_it_lists_runs_by_the_trigger_event_github_records(self):
        started = _run_record()
        gh = FakeSandbox(appears=started, records=[started], job_counts=[1])
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        scenario.verify(ctx)
        self.assertEqual(ctx.state["run_id"], started["id"])
        self.assertTrue(gh.listed_events, "the rehearsal never listed runs")
        self.assertEqual(
            set(gh.listed_events), {"repository_dispatch"},
            f"the listing must ask for the trigger event GitHub records, "
            f"never the dispatch type; asked for {sorted(map(str, gh.listed_events))}",
        )

    def test_a_dispatch_of_another_type_is_not_mistaken_for_ours(self):
        """A run of the same workflow that a different dispatch type produced
        (a second `types:` entry on the stub, say) lists under the same
        trigger event; the title is what tells them apart."""
        other = _run_record(run_id=772, display_title="agent-plan")
        gh = FakeSandbox(appears=other, records=[other], job_counts=[1])
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        with self.assertRaises(framework.HarnessTimeout):
            scenario.verify(ctx)
        self.assertNotIn("run_id", ctx.state)

    def test_a_record_without_a_title_is_still_ours(self):
        """The listing is already scoped to the trigger event; a missing
        field is not evidence of another type."""
        self.assertTrue(atp.is_our_dispatch({"id": 1, "event": "repository_dispatch"}))
        self.assertTrue(atp.is_our_dispatch(_run_record()))
        self.assertFalse(atp.is_our_dispatch(_run_record(display_title="agent-plan")))
        self.assertFalse(atp.is_our_dispatch(None))


class RefusalTest(unittest.TestCase):
    """The 2026-09-09 signature: a run that concluded having started nothing."""

    def _refused(self):
        refused = _run_record(status="completed", conclusion="failure")
        gh = FakeSandbox(
            appears=refused,
            records=[refused],
            job_counts=[0],
            timing={"billable": {}},
        )
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        return scenario, ctx, gh

    def test_a_zero_job_refusal_fails_with_the_named_reason(self):
        scenario, ctx, _gh = self._refused()
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.verify(ctx)
        self.assertIn(
            atp.REFUSED_MESSAGE, str(caught.exception),
            "a zero-job refusal must be reported with the outage's own words, "
            "not as a timeout",
        )

    def test_the_refusal_quotes_the_run_and_the_empty_billing(self):
        """The receipt has to be actionable: which run, and the corroborating
        evidence that GitHub billed no runner time at all."""
        scenario, ctx, _gh = self._refused()
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.verify(ctx)
        message = str(caught.exception)
        self.assertIn("771", message)
        self.assertRegex(message, r"billed no runner time|billable")

    def test_the_fast_fail_fires_inside_one_wait_budget(self):
        """Not at the 70-minute verdict deadline (DRE-3486): the run has
        already concluded, so nothing more can happen and waiting longer only
        buys a worse message."""
        scenario, ctx, _gh = self._refused()
        with self.assertRaises(framework.ScenarioFailure):
            scenario.verify(ctx)
        spent = ctx.state["elapsed"]["t"]
        self.assertLessEqual(
            spent, atp.start_budget(ctx),
            "the rehearsal must fail inside one wait budget",
        )
        self.assertLess(
            spent, ctx.verdict_timeout,
            f"failing at {spent:.0f}s means the refusal was reported as a "
            f"timeout, which is what the harness already did on 2026-09-09",
        )

    def test_a_job_that_starts_late_still_passes(self):
        """A slow sandbox is not a refusal: no job on the first poll, a job on
        the second, and the rehearsal passes."""
        pending = _run_record(status="queued")
        started = _run_record(status="in_progress")
        gh = FakeSandbox(
            appears=pending,
            records=[pending, started, started],
            job_counts=[0, 1, 1],
        )
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        scenario.verify(ctx)  # must not raise
        self.assertGreaterEqual(gh.job_calls, 2)


class StartedJobTest(unittest.TestCase):
    def _pass(self, **kw):
        started = _run_record(status="in_progress", **kw)
        gh = FakeSandbox(appears=started, records=[started], job_counts=[1])
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        return scenario, ctx, gh

    def test_a_started_job_at_the_commit_under_test_passes(self):
        scenario, ctx, _gh = self._pass()
        scenario.verify(ctx)
        self.assertEqual(ctx.state["jobs"], 1)
        self.assertEqual(ctx.state["parsed_sha"], TESTED_SHA)
        self.assertTrue(
            ctx.state["covers_commit_under_test"],
            "referenced_workflows named the commit under test — the rehearsal "
            "must record that it did",
        )

    def test_it_dispatches_agent_execute_at_the_sandbox(self):
        _scenario, _ctx, gh = self._pass()
        self.assertEqual(len(gh.dispatched), 1)
        repo, event, payload = gh.dispatched[0]
        self.assertEqual(repo, SANDBOX)
        self.assertEqual(
            event, "agent-execute",
            "the rehearsal must fire the same event the relay fires — a "
            "different one proves a workflow nothing in the fleet uses",
        )
        # The relay's payload shape: the stub keys its concurrency group off
        # `identifier`, and agent-task.yml reads title/description/url.
        for key in ("identifier", "title", "description", "url"):
            self.assertIn(key, payload)
        self.assertTrue(
            payload["identifier"].startswith("harness-"),
            "the seeded card id must sit inside the sweepable harness "
            f"namespace, got {payload['identifier']!r}",
        )

    def test_the_ref_attached_to_the_path_does_not_hide_the_callee(self):
        """The live endpoint returns `path` with the ref attached. Sandbox run
        34414467787 answered `…/agent-task.yml@main` and the rehearsal — which
        compared against the bare path the REST schema's example shows —
        reported the correctly wired stub as one that "does not call this
        repo's reusable workflow". A false alarm on EVERY run, from the check
        whose whole job is to be believed when it goes red."""
        scenario, ctx, _gh = self._pass(callee_path=CALLEE_AT_REF)
        scenario.verify(ctx)  # must not raise
        self.assertEqual(ctx.state["parsed_sha"], TESTED_SHA)
        self.assertTrue(ctx.state["covers_commit_under_test"])

    def test_a_path_with_no_ref_is_still_the_callee(self):
        """The schema's own example spells it bare, so both must match."""
        scenario, ctx, _gh = self._pass(callee_path=CALLEE)
        scenario.verify(ctx)  # must not raise
        self.assertEqual(ctx.state["parsed_sha"], TESTED_SHA)

    def test_another_workflow_in_this_repo_is_not_the_callee(self):
        """Stripping the ref must not loosen the match to a prefix: a stub
        calling bureau-pipeline's `ci.yml` proves nothing about agent-task.yml,
        and neither does one calling a fork's look-alike."""
        for impostor in (
            "dreadnought-foundry/bureau-pipeline/.github/workflows/ci.yml@main",
            f"{CALLEE}.disabled",
            f"someone-else/bureau-pipeline/.github/workflows/agent-task.yml@main",
        ):
            with self.subTest(path=impostor):
                scenario, ctx, _gh = self._pass(callee_path=impostor)
                with self.assertRaises(framework.ScenarioFailure) as caught:
                    scenario.verify(ctx)
                self.assertIn(CALLEE, str(caught.exception))

    def test_callee_path_survives_a_malformed_entry(self):
        self.assertEqual(atp.callee_path({"path": CALLEE_AT_REF}), CALLEE)
        self.assertEqual(atp.callee_path({"path": CALLEE}), CALLEE)
        self.assertEqual(atp.callee_path({"path": None}), "")
        self.assertEqual(atp.callee_path({}), "")
        self.assertEqual(atp.callee_path(None), "")
        self.assertIsNone(atp.callee_reference([None, "junk", {}]))

    def test_a_run_that_never_reached_bureau_pipeline_fails(self):
        """`jobs > 0` alone would pass for a stub calling something else
        entirely. The callee is half of what a dispatch proves."""
        scenario, ctx, _gh = self._pass(referenced=False)
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.verify(ctx)
        self.assertIn(CALLEE, str(caught.exception))

    def test_a_reference_without_a_resolved_commit_fails(self):
        started = _run_record(status="in_progress", sha="")
        gh = FakeSandbox(appears=started, records=[started], job_counts=[1])
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        with self.assertRaises(framework.ScenarioFailure):
            scenario.verify(ctx)

    def test_another_commit_is_recorded_rather_than_asserted(self):
        """The sandbox's stubs ride `@main` on purpose and GitHub resolves that
        ref AT DISPATCH, while harness.yml queues runs behind one another
        (`cancel-in-progress: false`). So the commit GitHub compiled is often
        not the commit this run is stamping, and failing on that would turn
        every boundary PR — and every merge-burst push — red on the pipeline's
        own design. The gap is RECORDED instead, never silently dropped."""
        scenario, ctx, _gh = self._pass(sha="0ddba11" + "0" * 33)
        scenario.verify(ctx)
        self.assertFalse(ctx.state["covers_commit_under_test"])
        self.assertIn(TESTED_SHA[:12], ctx.state["coverage_note"])
        self.assertIn("0ddba11", ctx.state["coverage_note"])


class MissingStubTest(unittest.TestCase):
    def test_a_sandbox_without_the_stub_says_so(self):
        """The operator's half of DRE-3486 installs the stub by hand. Until it
        is there the rehearsal cannot fire, and it must say THAT rather than
        report a workflow that does not parse."""
        gh = FakeSandbox(
            runs_error=github_api.GitHubError(404, "Not Found"),
        )
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.setup(ctx)
        message = str(caught.exception).lower()
        self.assertIn("agent-task.yml", message)
        self.assertIn("stub", message)


class CleanupTest(unittest.TestCase):
    def test_cleanup_cancels_the_run_it_started(self):
        """The rehearsal does not wait for the agent to build anything, so it
        must not leave a build agent running in the sandbox either."""
        started = _run_record(status="in_progress")
        gh = FakeSandbox(appears=started, records=[started], job_counts=[1])
        ctx = _ctx(gh)
        scenario = atp.SCENARIO
        scenario.setup(ctx)
        scenario.exercise(ctx)
        scenario.verify(ctx)
        scenario.cleanup(ctx)
        self.assertEqual(gh.cancelled, [771])

    def test_cleanup_survives_a_run_that_was_never_found(self):
        gh = FakeSandbox()
        ctx = _ctx(gh)
        atp.SCENARIO.cleanup(ctx)  # must not raise
        self.assertEqual(gh.cancelled, [])


class RegistrationTest(unittest.TestCase):
    def test_the_rehearsal_is_discovered_with_the_others(self):
        available = discover()
        self.assertIn("agent_task_parses", available)

    def test_it_runs_in_the_default_sweep(self):
        """Opt-in would leave the channel exactly as uncovered as it was on
        2026-09-09 — the gate that advances `stable` is the default sweep."""
        available = discover()
        default = select_names(available, [])
        self.assertIn("agent_task_parses", default)
        for sibling in ("bot_pr_flow", "dependabot_flow", "gate_paths",
                        "lane_contract"):
            self.assertIn(sibling, default)

    def test_it_does_not_spend_a_build_agent_run(self):
        self.assertFalse(
            atp.SCENARIO.requires_agent,
            "the rehearsal asserts that a job STARTED; it never waits for the "
            "agent to build anything, so it is not an opt-in agent scenario",
        )


class WiringTest(unittest.TestCase):
    def _job(self):
        return yaml.safe_load(WORKFLOW.read_text())["jobs"]["harness"]

    def _scenario_step(self):
        for step in self._job()["steps"]:
            if step.get("id") == "scenarios":
                return step
        self.fail("harness.yml has no `scenarios` step")

    def test_the_workflow_hands_the_driver_the_commit_under_test(self):
        env = self._scenario_step().get("env") or {}
        self.assertIn(
            "HARNESS_TESTED_SHA", env,
            "the rehearsal compares referenced_workflows against the commit "
            "the run is stamping — the workflow is the only place that knows it",
        )
        self.assertIn("steps.tested.outputs.sha", env["HARNESS_TESTED_SHA"])

    def test_the_job_cap_still_holds_with_the_rehearsal_added(self):
        """Acceptance: the harness still completes inside its existing
        deadline. The cap has to hold the shape of a bad day — one wasted
        verdict wait, gate_paths' longest chain — PLUS this rehearsal's own
        budget, which is bounded by one sandbox wait budget."""
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x")
        needed = (
            2 * ctx.verdict_timeout + ctx.merge_timeout + atp.start_budget(ctx)
        ) / 60
        cap = self._job().get("timeout-minutes", 0)
        self.assertGreaterEqual(
            cap, needed,
            f"timeout-minutes: {cap} no longer holds a bad day plus the "
            f"agent_task_parses rehearsal ({needed:.0f} min)",
        )

    def test_the_rehearsals_budget_is_one_sandbox_wait_budget(self):
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x")
        self.assertLessEqual(atp.start_budget(ctx), ctx.wait_deadline)
        # Switching the liveness check off must not remove the bound.
        off = framework.HarnessContext(
            gh=None, repo=SANDBOX, run_id="x", wait_deadline=0
        )
        self.assertGreater(atp.start_budget(off), 0)
        self.assertLess(atp.start_budget(off), off.verdict_timeout)


class DocTest(unittest.TestCase):
    """docs/harness.md states the INTENDED set — the absence that caused
    2026-09-09 was invisible because nothing ever wrote it down."""

    def _doc(self):
        self.assertTrue(DOC.exists(), "docs/harness.md must exist")
        return DOC.read_text()

    def test_the_doc_lists_every_workflow_the_sandbox_covers(self):
        text = self._doc()
        for stub in ("ci.yml", "qa-review.yml", "merge-gate.yml",
                     "reconcile.yml", "agent-task.yml"):
            self.assertIn(
                stub, text,
                f"docs/harness.md must name {stub} — the list is what a reader "
                "compares against .github/workflows/",
            )

    def test_every_pipeline_workflow_the_doc_names_exists_here(self):
        """A list that names a workflow this repo does not have is the same
        rot from the other direction."""
        names = set(re.findall(r"`([a-z0-9-]+\.yml)`", self._doc()))
        # `ci.yml` is the sandbox's own product CI and has no counterpart here.
        here = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
        missing = sorted(
            n for n in names
            if n not in here and n.removeprefix("self-") not in here
            and n != "ci.yml"
        )
        self.assertEqual(
            missing, [],
            f"docs/harness.md names workflows that do not exist here: {missing}",
        )

    def test_the_doc_names_every_scenario_the_driver_discovers(self):
        text = self._doc()
        for name in sorted(discover()):
            self.assertIn(
                name, text,
                f"docs/harness.md must name the `{name}` scenario — a coverage "
                "list that lags the driver is how a gap stays invisible",
            )


if __name__ == "__main__":
    unittest.main()
