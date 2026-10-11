"""RED-first tests for the `proof_task_parses` rehearsal (DRE-6045).

THE GAP. `agent_task_parses` (DRE-3486) fires `agent-task.yml` at the sandbox
because GitHub validates a CALLED workflow only at dispatch — the 2026-09-09
outage was a workflow that passed every parse test here and was refused the
moment it was used. `proof-task.yml`, the proof run on a PROOF card
(DRE-5924), sat outside that cover: `docs/harness.md` said no sandbox stub was
installed and no rehearsal fired it, so a commit that broke the proof
workflow could still advance `stable`.

The stub is now installed — bureau-harness PR #3551 under DRE-6034, fired once
by hand as sandbox run 37872931948 — and this rehearsal fires it on every
default sweep, exactly as `agent_task_parses` fires its sibling.

WHAT IS PINNED HERE:

  * **The rehearsal FIRES the workflow**, with the sweep's own proof payload
    (`plan_run.payload(card, reason=…)` under `plan_run.PROOF_EVENT`), key for
    key.
  * **A started job is the proof**: `jobs > 0`, and `referenced_workflows`
    naming bureau-pipeline's own `proof-task.yml` with a resolved commit.
  * **A refusal is named, not timed out** — inside ONE wait budget.
  * **A missing stub is a missing stub**, naming DRE-6034, never a parse
    failure.
  * **It sits in the default sweep**, and the harness still fits its job cap.
  * **`docs/harness.md` and the README no longer call it uncovered.**
"""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_run  # noqa: E402
from harness import framework, github_api  # noqa: E402
from harness.__main__ import select_names  # noqa: E402
from harness.agent_scenario import CARD_URL  # noqa: E402
from harness.scenarios import discover  # noqa: E402

try:  # RED until the scenario exists; every test below then fails, not errors
    from harness.scenarios import proof_task_parses as ptp  # noqa: E402
except ImportError:  # pragma: no cover — only before the scenario lands
    ptp = None

from harness.scenarios import agent_task_parses as atp  # noqa: E402

SANDBOX = "dreadnought-foundry/bureau-harness"
WORKFLOW = ROOT / ".github" / "workflows" / "harness.yml"
DOC = ROOT / "docs" / "harness.md"
README = ROOT / "scripts" / "harness" / "README.md"

# The commit the harness run is proving — harness.yml's `tested` step.
TESTED_SHA = "c0ffee1c0ffee1c0ffee1c0ffee1c0ffee1c0ffe"

# The callee the sandbox stub must reach, WITHOUT the ref.
CALLEE = "dreadnought-foundry/bureau-pipeline/.github/workflows/proof-task.yml"

# The way the live endpoint spells it: sandbox run 37872931948 (2026-10-08
# 19:05 PT, the operator's hand-fired check of DRE-6034's stub) answered this
# path, with `ref` `refs/heads/main` and a resolved `sha` beside it.
CALLEE_AT_REF = f"{CALLEE}@main"

# The seven keys the sweep's proof dispatch carries (proof_dispatch.py →
# plan_run.payload(card, reason=…)).
PROOF_KEYS = {"card_id", "identifier", "title", "description", "labels", "url",
              "reason"}


def _scenario(test):
    if ptp is None:
        test.fail("scripts/harness/scenarios/proof_task_parses.py does not exist")
    return ptp.SCENARIO


def _run_record(run_id=881, status="in_progress", conclusion=None, sha=TESTED_SHA,
                referenced=True, display_title="proof-execute",
                callee_path=CALLEE_AT_REF):
    """An Actions workflow-run record, REST-shaped, after run 37872931948."""
    record = {
        "id": run_id,
        "name": "Proof Task",
        "path": ".github/workflows/proof-task.yml",
        "event": "repository_dispatch",
        "display_title": display_title,
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-10-09T02:05:04Z",
        "updated_at": "2026-10-09T02:05:41Z",
        "html_url": f"https://github.com/{SANDBOX}/actions/runs/{run_id}",
        "referenced_workflows": [],
    }
    if referenced:
        record["referenced_workflows"] = [
            {"path": callee_path, "sha": sha, "ref": "refs/heads/main"}
        ]
    return record


class FakeSandbox:
    """The sandbox as the rehearsal sees it — the same fake
    `tests/test_harness_agent_task_parses.py` drives its sibling with, plus a
    record of WHICH workflow file each listing asked for."""

    def __init__(self, runs_before=(), appears=None, records=(), job_counts=(),
                 timing=None, dispatch_error=None, runs_error=None):
        self.runs_before = list(runs_before)
        self.appears = appears
        self.records = list(records)
        self.job_counts = list(job_counts)
        self.timing = timing if timing is not None else {"billable": {}}
        self.dispatch_error = dispatch_error
        self.runs_error = runs_error
        self.dispatched = []
        self.cancelled = []
        self.listed_events = []
        self.listed_files = []
        self.job_calls = 0

    def repository_dispatch(self, repo, event_type, client_payload):
        if self.dispatch_error:
            raise self.dispatch_error
        self.dispatched.append((repo, event_type, dict(client_payload)))

    def list_workflow_runs_for(self, repo, workflow_file, event=None, per_page=30):
        if self.runs_error:
            raise self.runs_error
        self.listed_events.append(event)
        self.listed_files.append(workflow_file)
        if not self.dispatched or self.appears is None:
            runs = list(self.runs_before)
        else:
            runs = [self.appears] + list(self.runs_before)
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
        return {"total_count": total, "jobs": [{"id": 1, "name": "call"}] * total}

    def run_timing(self, repo, run_id):
        return self.timing

    def cancel_workflow_run(self, repo, run_id):
        self.cancelled.append(run_id)

    def list_open_prs(self, repo):
        return []

    def delete_ref(self, repo, branch):
        return False

    def matching_refs(self, repo, prefix):
        return []


def _ctx(gh, **kw):
    """A context whose clock never really sleeps, and whose log is kept."""
    elapsed = {"t": 0.0}
    lines = []

    def clock():
        return elapsed["t"]

    def sleep(seconds):
        elapsed["t"] += seconds

    ctx = framework.HarnessContext(
        gh=gh,
        repo=SANDBOX,
        run_id="main-20261011-abcdef",
        namespace="main",
        qa_login="agent-bureau-qa-bot[bot]",
        tested_sha=kw.pop("tested_sha", TESTED_SHA),
        poll_interval=1.0,
        clock=clock,
        sleep=sleep,
        log=lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
        **kw,
    )
    ctx.state["elapsed"] = elapsed
    ctx.state["lines"] = lines
    return ctx


def _fired(test, record=None, job_counts=(1,), **kw):
    record = record or _run_record()
    gh = FakeSandbox(appears=record, records=[record], job_counts=list(job_counts))
    ctx = _ctx(gh, **kw)
    scenario = _scenario(test)
    scenario.setup(ctx)
    scenario.exercise(ctx)
    return scenario, ctx, gh


class RegistrationTest(unittest.TestCase):
    def test_the_rehearsal_is_discovered(self):
        self.assertIn("proof_task_parses", discover())

    def test_it_runs_in_the_default_sweep(self):
        """The gate that advances `stable` is the default sweep; opt-in would
        leave the proof workflow exactly as uncovered as the doc said it was."""
        self.assertIn("proof_task_parses", select_names(discover(), []))

    def test_it_does_not_spend_a_build_agent_run(self):
        self.assertFalse(_scenario(self).requires_agent)

    def test_it_is_named_for_its_module(self):
        self.assertEqual(_scenario(self).name, "proof_task_parses")

    def test_it_is_a_copy_not_a_subclass(self):
        """DRE-3101 died at the turn cap refactoring exactly this module: the
        rehearsal is a COPY of agent_task_parses, nothing lifted or shared."""
        scenario = _scenario(self)
        self.assertNotIsInstance(scenario, type(atp.SCENARIO))


class ConstantsTest(unittest.TestCase):
    """The contract DRE-6034's stub and DRE-6044's PROOF card both read."""

    def test_the_contract_constants(self):
        _scenario(self)
        self.assertEqual(ptp.DISPATCH_EVENT, "proof-execute")
        self.assertEqual(ptp.DISPATCH_EVENT, plan_run.PROOF_EVENT)
        self.assertEqual(ptp.TRIGGER_EVENT, "repository_dispatch")
        self.assertEqual(ptp.WORKFLOW_FILE, "proof-task.yml")
        self.assertEqual(ptp.CALLEE_PATH, CALLEE)

    def test_the_wait_budget_is_one_sandbox_budget(self):
        _scenario(self)
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x")
        self.assertLessEqual(ptp.start_budget(ctx), ctx.wait_deadline)
        off = framework.HarnessContext(
            gh=None, repo=SANDBOX, run_id="x", wait_deadline=0
        )
        self.assertGreater(ptp.start_budget(off), 0)
        self.assertLess(ptp.start_budget(off), off.verdict_timeout)


class DispatchTest(unittest.TestCase):
    def test_it_fires_proof_execute_at_the_sandbox(self):
        _s, _c, gh = _fired(self)
        self.assertEqual(len(gh.dispatched), 1)
        repo, event, _payload = gh.dispatched[0]
        self.assertEqual(repo, SANDBOX)
        self.assertEqual(event, "proof-execute")

    def test_the_payload_is_the_sweep_s_proof_payload_key_for_key(self):
        """proof_dispatch.py sends `plan_run.payload(card, reason=…)`; a
        thinner or fatter payload exercises a shape nothing in the fleet
        sends."""
        _s, ctx, gh = _fired(self)
        _repo, _event, payload = gh.dispatched[0]
        swept = plan_run.payload(
            {"id": "x", "identifier": "x", "title": "x", "description": "x",
             "labels": {"nodes": [{"name": "agent:ops"}]}},
            reason="x",
        )
        self.assertEqual(set(payload), set(swept))
        self.assertEqual(set(payload), PROOF_KEYS)
        identifier = ctx.state["identifier"]
        self.assertEqual(payload["identifier"], identifier)
        self.assertEqual(payload["card_id"], identifier)
        self.assertTrue(identifier.startswith("harness-"), identifier)
        self.assertEqual(payload["labels"], ["agent:ops"])
        self.assertEqual(payload["url"], CARD_URL)
        reason = payload["reason"]
        self.assertIsInstance(reason, str)
        self.assertNotIn("\n", reason)
        self.assertIn("harness", reason)

    def test_it_lists_its_own_workflow_by_the_trigger_event(self):
        scenario, ctx, gh = _fired(self)
        scenario.verify(ctx)
        self.assertEqual(set(gh.listed_events), {"repository_dispatch"})
        self.assertEqual(set(gh.listed_files), {"proof-task.yml"})

    def test_a_dispatch_of_another_type_is_not_mistaken_for_ours(self):
        other = _run_record(run_id=882, display_title="agent-execute")
        scenario, ctx, _gh = _fired(self, record=other)
        with self.assertRaises(framework.HarnessTimeout):
            scenario.verify(ctx)
        self.assertNotIn("run_id", ctx.state)


class StartedJobTest(unittest.TestCase):
    def test_a_started_job_at_the_commit_under_test_passes(self):
        scenario, ctx, _gh = _fired(self)
        scenario.verify(ctx)
        self.assertEqual(ctx.state["jobs"], 1)
        self.assertEqual(ctx.state["parsed_sha"], TESTED_SHA)
        self.assertTrue(ctx.state["covers_commit_under_test"])

    def test_it_prints_the_lines_the_proof_card_reads(self):
        """DRE-6044's PROOF card reads these lines off the harness run."""
        scenario, ctx, _gh = _fired(self)
        scenario.verify(ctx)
        scenario.cleanup(ctx)
        log = "\n".join(ctx.state["lines"])
        url = f"https://github.com/{SANDBOX}/actions/runs/881"
        self.assertIn(f"[proof_task_parses] dispatch produced run 881 ({url})", log)
        self.assertIn(
            "[proof_task_parses] 1 job(s) started on run 881 — "
            "proof-task.yml compiled", log,
        )
        self.assertIn(
            f"[proof_task_parses] proof-task.yml parses at {TESTED_SHA[:12]}", log
        )
        self.assertIn("[proof_task_parses] cleanup: cancelled run 881", log)

    def test_the_callee_is_compared_before_the_at(self):
        for spelled in (CALLEE_AT_REF, CALLEE):
            with self.subTest(path=spelled):
                scenario, ctx, _gh = _fired(self, _run_record(callee_path=spelled))
                scenario.verify(ctx)  # must not raise
                self.assertEqual(ctx.state["parsed_sha"], TESTED_SHA)
        _scenario(self)
        self.assertEqual(ptp.callee_path({"path": CALLEE_AT_REF}), CALLEE)
        self.assertEqual(ptp.callee_path(None), "")

    def test_another_workflow_is_not_the_callee(self):
        """Above all, the sibling: a stub that calls agent-task.yml proves
        nothing about proof-task.yml."""
        for impostor in (
            "dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@main",
            f"{CALLEE}.disabled",
            "someone-else/bureau-pipeline/.github/workflows/proof-task.yml@main",
        ):
            with self.subTest(path=impostor):
                scenario, ctx, _gh = _fired(self, _run_record(callee_path=impostor))
                with self.assertRaises(framework.ScenarioFailure) as caught:
                    scenario.verify(ctx)
                self.assertIn(CALLEE, str(caught.exception))

    def test_a_reference_without_a_resolved_sha_fails(self):
        scenario, ctx, _gh = _fired(self, _run_record(sha=""))
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.verify(ctx)
        self.assertIn("proof-task.yml", str(caught.exception))

    def test_another_commit_is_recorded_rather_than_asserted(self):
        scenario, ctx, _gh = _fired(self, _run_record(sha="0ddba11" + "0" * 33))
        scenario.verify(ctx)  # must not raise
        self.assertFalse(ctx.state["covers_commit_under_test"])
        self.assertIn(TESTED_SHA[:12], ctx.state["coverage_note"])
        self.assertIn("0ddba11", ctx.state["coverage_note"])
        self.assertIn("proof-task.yml", ctx.state["coverage_note"])


class RefusalTest(unittest.TestCase):
    def _refused(self):
        refused = _run_record(status="completed", conclusion="failure")
        return _fired(self, refused, job_counts=(0,))

    def test_a_zero_job_run_fails_with_the_refusal_message(self):
        scenario, ctx, _gh = self._refused()
        with self.assertRaises(framework.ScenarioFailure) as caught:
            scenario.verify(ctx)
        message = str(caught.exception)
        self.assertIn(ptp.REFUSED_MESSAGE, message)
        self.assertIn("proof-task.yml was refused before any job started", message)
        self.assertIn("881", message)

    def test_the_refusal_fires_inside_one_wait_budget(self):
        scenario, ctx, _gh = self._refused()
        with self.assertRaises(framework.ScenarioFailure):
            scenario.verify(ctx)
        spent = ctx.state["elapsed"]["t"]
        self.assertLessEqual(spent, ptp.start_budget(ctx))
        self.assertLess(spent, ctx.verdict_timeout)


class MissingStubTest(unittest.TestCase):
    def test_a_404_is_a_missing_stub_naming_dre_6034(self):
        gh = FakeSandbox(runs_error=github_api.GitHubError(404, "Not Found"))
        ctx = _ctx(gh)
        with self.assertRaises(framework.ScenarioFailure) as caught:
            _scenario(self).setup(ctx)
        message = str(caught.exception)
        self.assertIn("bureau-harness has no proof-task.yml stub", message)
        self.assertIn("DRE-6034", message)
        self.assertIn("MISSING STUB", message)

    def test_another_error_is_not_dressed_as_a_missing_stub(self):
        gh = FakeSandbox(runs_error=github_api.GitHubError(500, "boom"))
        ctx = _ctx(gh)
        with self.assertRaises(github_api.GitHubError):
            _scenario(self).setup(ctx)


class CleanupTest(unittest.TestCase):
    def test_cleanup_cancels_the_run_it_started(self):
        scenario, ctx, gh = _fired(self)
        scenario.verify(ctx)
        scenario.cleanup(ctx)
        self.assertEqual(gh.cancelled, [881])

    def test_cleanup_survives_a_run_that_was_never_found(self):
        gh = FakeSandbox()
        ctx = _ctx(gh)
        _scenario(self).cleanup(ctx)  # must not raise
        self.assertEqual(gh.cancelled, [])


class JobCapTest(unittest.TestCase):
    def test_the_job_cap_holds_with_both_rehearsals(self):
        """The bad day `test_harness_agent_task_parses` sizes the cap for, plus
        ONE more sandbox budget for this rehearsal."""
        _scenario(self)
        job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["harness"]
        ctx = framework.HarnessContext(gh=None, repo=SANDBOX, run_id="x")
        needed = (
            2 * ctx.verdict_timeout + ctx.merge_timeout
            + atp.start_budget(ctx) + ptp.start_budget(ctx)
        ) / 60
        self.assertGreaterEqual(job.get("timeout-minutes", 0), needed)


class DocTest(unittest.TestCase):
    def _stub_row(self):
        for line in DOC.read_text().splitlines():
            if line.startswith("| `proof-task.yml` |"):
                return line
        self.fail("docs/harness.md has no `proof-task.yml` stub row")

    def test_the_stub_row_names_its_rehearsal(self):
        row = self._stub_row()
        self.assertTrue(row.rstrip().endswith("| `proof_task_parses` |"), row)
        self.assertIn("DRE-5924", row)
        self.assertNotIn("would call", row)

    def test_the_doc_no_longer_calls_it_uncovered(self):
        text = DOC.read_text()
        self.assertNotIn("No sandbox stub is installed", text)
        self.assertNotIn("no card yet", text)

    def test_the_rehearsals_table_lists_it_in_the_default_sweep(self):
        rows = [line for line in DOC.read_text().splitlines()
                if line.startswith("| `proof_task_parses` |")]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("| yes |", rows[0])
        self.assertIn("proof-execute", rows[0])

    def test_the_readme_lists_the_stub_as_a_prerequisite(self):
        text = README.read_text()
        self.assertIn("**`proof-task.yml` stub**", text)
        self.assertIn("repository_dispatch: [proof-execute]", text)
        bullet = text[text.index("**`proof-task.yml` stub**"):]
        bullet = bullet[:bullet.index("\n* ")]
        for word in ("DRE-6034", "proof_task_parses", "MISSING STUB"):
            self.assertIn(word, bullet)


if __name__ == "__main__":
    unittest.main()
