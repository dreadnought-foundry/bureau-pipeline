"""RED-first wiring tests for .github/workflows/harness.yml (DRE-2098).

The contract shared with the sibling cards (dependabot_flow / gate_paths
and the wiring card): workflow file harness.yml, job id `harness`,
workflow_dispatch input `pipeline_ref` defaulting to main, and a
concurrency group — two runs sharing the one sandbox repo would trample
each other, so a second dispatch must QUEUE (cancel-in-progress false:
killing a live run mid-scenario is exactly the crashed-run mess the sweep
exists to mop up).

Identity wiring: the driver acts on the sandbox with tokens minted from
THIS repo's existing App secrets, scoped to the sandbox repo. The expected
merger login is DERIVED from the qa App's own app-slug (merge-gate #57
pattern) — never a hardcoded login literal (test_qa_login_literal_roster
sweeps all workflow files for stray literals).

These tests must FAIL before harness.yml exists, and PASS after.
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
)
SANDBOX = "dreadnought-foundry/bureau-harness"


def _doc():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.name}"
    return yaml.safe_load(WORKFLOW.read_text())


def _on(doc):
    # YAML 1.1 parses the bare key `on` as boolean True.
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


def _job(doc):
    return doc["jobs"]["harness"]


def _steps(doc):
    return _job(doc).get("steps") or []


class TriggerContractTest(unittest.TestCase):
    def test_dispatch_carries_pipeline_ref_defaulting_to_main(self):
        inputs = _on(_doc()).get("workflow_dispatch", {}).get("inputs") or {}
        self.assertIn("pipeline_ref", inputs)
        self.assertEqual(inputs["pipeline_ref"].get("default"), "main")

    def test_job_id_is_harness(self):
        # The shared contract: sibling and wiring cards address this job id.
        self.assertEqual(list(_doc()["jobs"]), ["harness"])

    def test_checkout_threads_the_ref_under_test(self):
        # DRE-2103: on workflow_dispatch the ref under test is pipeline_ref
        # (defaulted to main); on pull_request the inputs context is EMPTY,
        # so without the middle clause every PR run would silently test
        # main instead of the PR's own head — the chimera class DRE-2026
        # exists to prevent.
        checkouts = [
            s for s in _steps(_doc())
            if (s.get("uses") or "").startswith("actions/checkout")
        ]
        self.assertTrue(checkouts, "no checkout step")
        self.assertEqual(
            (checkouts[0].get("with") or {}).get("ref"),
            "${{ inputs.pipeline_ref || github.event.pull_request.head.sha || 'main' }}",
            "the driver code must come from the ref under test",
        )


class ConcurrencyTest(unittest.TestCase):
    def test_single_flight_on_the_shared_sandbox(self):
        conc = _doc().get("concurrency")
        self.assertIsInstance(conc, dict, "workflow-level concurrency required")
        self.assertTrue(conc.get("group"))
        self.assertIs(
            conc.get("cancel-in-progress"), False,
            "a second run must QUEUE — cancelling mid-scenario strands "
            "sandbox state on purpose-defeating a self-cleaning suite",
        )


class IdentityWiringTest(unittest.TestCase):
    def _mints(self):
        return [
            s for s in _steps(_doc())
            if (s.get("uses") or "").startswith("actions/create-github-app-token")
        ]

    def test_worker_and_qa_tokens_minted_scoped_to_the_sandbox(self):
        mints = {(s.get("with") or {}).get("app-id"): s for s in self._mints()}
        self.assertIn("${{ secrets.BUREAU_APP_ID }}", mints)
        self.assertIn("${{ secrets.BUREAU_QA_APP_ID }}", mints)
        for step in mints.values():
            self.assertEqual(
                (step.get("with") or {}).get("repositories"), "bureau-harness",
                "mint must be scoped to the sandbox repo only",
            )
            self.assertEqual(
                (step.get("with") or {}).get("owner"), "dreadnought-foundry"
            )

    def test_qa_login_is_derived_from_the_app_slug_not_hardcoded(self):
        raw = WORKFLOW.read_text()
        self.assertNotIn(
            "agent-bureau-qa-bot", raw,
            "derive the expected merger from the minted token's app-slug "
            "(merge-gate #57) — a rename must follow automatically",
        )
        env = {}
        for step in _steps(_doc()):
            env.update(step.get("env") or {})
        self.assertIn("app-slug", env.get("HARNESS_QA_LOGIN", ""))
        self.assertIn("app-slug", env.get("HARNESS_WORKER_LOGIN", ""))

    def test_driver_is_invoked_with_the_sandbox_repo(self):
        runs = "\n".join(s.get("run") or "" for s in _steps(_doc()))
        self.assertIn("python3 -m harness", runs)
        env = {}
        for step in _steps(_doc()):
            env.update(step.get("env") or {})
        self.assertEqual(env.get("HARNESS_REPO"), SANDBOX)

    def test_qa_token_is_threaded_to_the_driver(self):
        # dependabot_flow reads check-runs on the vendor PR's head (the
        # self-skip evidence) — the qa App's token is the PROVEN reader for
        # that record (merge-gate.yml's own path); the worker App's checks
        # permission is not guaranteed.
        env = {}
        for step in _steps(_doc()):
            env.update(step.get("env") or {})
        self.assertEqual(env.get("HARNESS_QA_TOKEN"), "${{ steps.qa.outputs.token }}")


class PrGateTest(unittest.TestCase):
    """DRE-2103: the harness is load-bearing on boundary-touching PRs.

    merge_gate.py condition 1 requires EVERY check run on the head sha to
    complete green, and harness.yml is not a review workflow, so a red
    harness run holds the merge with no branch-protection change
    (tests/test_harness_gate_evidence.py proves that arm). The wiring here
    must (a) fire only on the boundary paths, (b) self-skip clean on a
    dependabot-triggered pull_request — that event gets GitHub's SEPARATE
    Dependabot secrets store, for us EMPTY, so the App-token mints would
    crash red before a single scenario ran (DRE-2047/2067; premortem Q2).
    """

    # The boundary: workflow wiring plus the dispatch/gate scripts the
    # scenarios exercise end-to-end. Everything else stays silent.
    BOUNDARY_PATHS = {
        ".github/workflows/**",
        "scripts/harness/**",
        "scripts/reconcile.py",
        "scripts/merge_gate.py",
        "scripts/dispatch_pool.py",
        "scripts/dedupe_dispatch.py",
        "scripts/should_review_pr.py",
    }

    def test_pull_request_trigger_filters_to_the_boundary_paths(self):
        pr = _on(_doc()).get("pull_request")
        self.assertIsInstance(pr, dict, "pull_request trigger required")
        self.assertEqual(set(pr.get("paths") or []), self.BOUNDARY_PATHS)

    def test_workflow_dispatch_survives_alongside_the_pr_trigger(self):
        # The release-promotion route (pipeline_ref on a candidate sha)
        # must keep working — the PR gate is additive.
        self.assertIn("workflow_dispatch", _on(_doc()))

    def test_dependabot_pull_request_self_skips_at_the_job_level(self):
        # Premortem Q1/Q2: dependabot's own opens/rebases of a
        # .github/workflows/** bump fire pull_request AS dependabot[bot],
        # which gets the empty Dependabot secrets store. The guard must sit
        # on the JOB so the token-mint steps never execute; a skipped job's
        # check run concludes `skipped` — green to the merge gate, never a
        # red crash (the pr-review.yml:52 pattern, DRE-2047).
        cond = _job(_doc()).get("if") or ""
        self.assertIn("github.event_name != 'pull_request'", cond)
        self.assertIn("github.actor != 'dependabot[bot]'", cond)

    def test_statuses_write_permission_for_the_sha_stamp(self):
        perms = _doc().get("permissions") or {}
        self.assertEqual(perms.get("contents"), "read")
        self.assertEqual(perms.get("statuses"), "write")


class ShaStampTest(unittest.TestCase):
    """DRE-2103: a run must stamp a commit status on the sha it actually
    tested. The workflow-run record cannot honestly bind a dispatch run to
    its candidate: GitHub sets the run's head_sha to the DISPATCH ref's tip,
    while pipeline_ref governs the checkout — so release-gate.yml reads
    this stamp, never the run listing."""

    def _runs(self):
        return "\n".join(s.get("run") or "" for s in _steps(_doc()))

    def test_tested_sha_is_resolved_from_the_actual_checkout(self):
        self.assertIn("git rev-parse HEAD", self._runs())

    def test_stamp_posts_the_release_gate_context(self):
        import release_gate

        raw = WORKFLOW.read_text()
        self.assertIn("/statuses/", self._runs())
        self.assertIn(
            release_gate.STATUS_CONTEXT, raw,
            "harness.yml must stamp the exact context release_gate.py reads "
            "— the string is a shared contract",
        )

    def test_stamp_runs_on_failure_too_but_never_when_cancelled(self):
        # A red dispatch run against a candidate sha must leave an honest
        # failure stamp (a later green run overwrites it — latest status
        # per context wins); a cancelled run proved nothing either way.
        stamps = [
            s for s in _steps(_doc()) if "/statuses/" in (s.get("run") or "")
        ]
        self.assertEqual(len(stamps), 1)
        self.assertEqual(stamps[0].get("if"), "success() || failure()")


class BudgetTest(unittest.TestCase):
    def test_job_timeout_budgets_all_three_scenarios(self):
        # bot_pr_flow alone budgeted 60 minutes; dependabot_flow adds a
        # reconcile-cron wait (~15 min) plus a critic run, and gate_paths
        # runs three PR legs. The job cap must cover a slow-but-honest run.
        self.assertGreaterEqual(_job(_doc()).get("timeout-minutes", 0), 90)


if __name__ == "__main__":
    unittest.main()
