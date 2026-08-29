"""The groomer runs on demand, and only on demand (DRE-2683, decision D5).

Approved by the operator on 2026-08-23: on demand, until the groomer's
judgement has been audited. Not on a schedule. A groomer running unattended
over two hundred cards before anyone has checked its calls is the same mistake
as trusting a critic's verdicts before comparing them to a held-back set.

So the trigger shape is part of the contract and is asserted here against the
LIVE workflow files (the pattern tests/test_self_host_stubs.py uses): a
`schedule:` added later turns this red rather than quietly starting a sweep
nobody asked for.

The rest is the wiring every workflow in this repo owes: the reusable threads
`pipeline_ref` (DRE-2026/DRE-2689), the runnable stub is watched by the medic
(DRE-2036 — an unwatched red run is a safety net that dies silently), and the
lane the drain writes into declares the groomer as one of its writers, because
the lane contract is what the harness asserts the live board against.

Run: cd bureau-pipeline && python3 -m pytest tests/test_groomer_wiring.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import groomer  # noqa: E402

PIPELINE = "dreadnought-foundry/bureau-pipeline"


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), f"missing workflow {name}"
    return yaml.safe_load(path.read_text())


def _on(doc: dict) -> dict:
    on = doc.get("on", doc.get(True))
    return on if isinstance(on, dict) else {}


class OnDemandOnlyTest(unittest.TestCase):
    def test_the_stub_is_manual_dispatch_only(self):
        on = _on(_load("self-groomer.yml"))
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn(
            "schedule", on,
            "D5: the groomer runs on demand until its judgement has been "
            "audited — a cron here is the decision being reversed silently",
        )
        self.assertEqual(
            set(on), {"workflow_dispatch"},
            "on demand means one trigger: a person dispatching it",
        )

    def test_no_workflow_in_this_repo_schedules_the_groomer(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            doc = yaml.safe_load(path.read_text())
            if not isinstance(doc, dict):
                continue
            if "schedule" not in _on(doc):
                continue
            job = next(iter((doc.get("jobs") or {}).values()), {})
            self.assertNotIn(
                "groomer.yml", str(job.get("uses") or ""),
                f"{path.name} puts the groomer on a schedule",
            )

    def test_the_stub_calls_the_reusable_at_the_qualified_ref(self):
        job = next(iter(_load("self-groomer.yml")["jobs"].values()))
        self.assertEqual(
            job.get("uses"),
            f"{PIPELINE}/.github/workflows/groomer.yml@main",
        )
        self.assertEqual(job.get("secrets"), "inherit")

    def test_the_stub_is_named_groomer_and_the_medic_watches_it(self):
        self.assertEqual(_load("self-groomer.yml").get("name"), "Groomer")
        watched = (_on(_load("self-medic.yml")).get("workflow_run") or {}).get(
            "workflows") or []
        self.assertIn(
            "Groomer", watched,
            "DRE-2036: every workflow that runs under its own name is watched, "
            "or its red runs go undiagnosed",
        )

    def test_the_reusable_threads_pipeline_ref(self):
        doc = _load("groomer.yml")
        on = _on(doc)
        self.assertIn("workflow_call", on)
        spec = (on["workflow_call"].get("inputs") or {}).get("pipeline_ref")
        self.assertIsNotNone(spec, "DRE-2689: the reusable must take pipeline_ref")
        self.assertTrue(spec.get("required"))
        self.assertNotIn("default", spec)

    def test_the_reusable_defaults_to_proposing_never_draining(self):
        """A dispatch with nothing filled in must be the read-only one. The
        drain is the step that moves cards, and it is opt-in by name."""
        inputs = (_on(_load("groomer.yml"))["workflow_call"].get("inputs") or {})
        self.assertEqual(inputs["mode"]["default"], "propose")
        stub_inputs = (_on(_load("self-groomer.yml"))["workflow_dispatch"]
                       .get("inputs") or {})
        self.assertEqual(stub_inputs["mode"]["default"], "propose")


class LaneContractTest(unittest.TestCase):
    """The drain moves a card into Planning, so the groomer is a writer of
    Planning. A writer the contract does not name is a write the harness
    cannot account for."""

    def setUp(self):
        self.contract = json.loads(
            (ROOT / "config" / "lane-contract.json").read_text())

    def _lane(self, name):
        return next(l for l in self.contract["lanes"] if l.get("name") == name)

    def test_the_groomer_is_a_declared_writer(self):
        writers = self.contract["writers"]
        self.assertIn("groomer.py", writers)
        path = writers["groomer.py"]["path"]
        self.assertTrue((ROOT / path).is_file(), f"{path} does not exist")

    def test_the_groomer_writes_the_lane_it_drains_into(self):
        who = self._lane(groomer.DRAIN_TO)["clauses"]["writers"]["who"]
        self.assertIn("groomer.py", who)

    def test_the_groomer_is_not_a_writer_of_intake(self):
        """Intake's writers are 'anything that creates a card, and nothing
        that moves one onward'. The groomer only ever moves cards OUT."""
        who = self._lane("Intake")["clauses"]["writers"]["who"]
        self.assertNotIn("groomer.py", who)

    def test_the_rendered_contract_document_is_current(self):
        import lane_contract
        doc = (ROOT / "docs" / "lane-contract.md").read_text(encoding="utf-8")
        self.assertEqual(
            doc, lane_contract.render_markdown(),
            "docs/lane-contract.md is stale — regenerate it with "
            "`python3 scripts/lane_contract.py render`",
        )


class DocumentationTest(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "groomer.md").read_text(encoding="utf-8")

    def test_the_doc_states_the_approval_gate(self):
        self.assertIn(groomer.APPROVAL_TAG, self.doc)
        self.assertIn("Intake", self.doc)

    def test_the_doc_says_why_a_cycle_is_not_sprint_planning(self):
        self.assertIn(groomer.CYCLE_IS_NOT_SPRINT_PLANNING, self.doc)

    def test_the_doc_records_the_comparison_against_the_forms_review(self):
        """Proof in production: one real batch groomed, its ordering compared
        against the collisions DRE-2649 found. Agreement and disagreement are
        both results, and both are written down."""
        proof = (ROOT / "docs" / "groomer-first-batch.md").read_text(encoding="utf-8")
        self.assertIn("DRE-2649", proof)
        for heading in ("## Agreement", "## Disagreement"):
            self.assertIn(heading, proof)


if __name__ == "__main__":                      # pragma: no cover
    unittest.main()
