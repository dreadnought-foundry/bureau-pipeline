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

import unittest
from pathlib import Path

import yaml

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

    def test_checkout_threads_the_pipeline_ref_under_test(self):
        checkouts = [
            s for s in _steps(_doc())
            if (s.get("uses") or "").startswith("actions/checkout")
        ]
        self.assertTrue(checkouts, "no checkout step")
        self.assertEqual(
            (checkouts[0].get("with") or {}).get("ref"),
            "${{ inputs.pipeline_ref || 'main' }}",
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


if __name__ == "__main__":
    unittest.main()
