"""Release-channel ref threading tests (DRE-2026).

Origin: the reusable workflows re-checkout dreadnought-foundry/bureau-pipeline
internally with NO ref (audit found 13 sites across 9 reusable workflows).
A product-repo stub pinned to `...@vN` therefore pinned only the top-level
YAML; every script/brief/standard those workflows execute still floated
@main — the README's "point one repo's stub at a branch ref" canary
procedure tested a chimera.

Contract under test (enforced live by scripts/check_pipeline_ref.py, which
runs as a Pipeline Tests step and whose functions these tests exercise):

  * EVERY `actions/checkout` of dreadnought-foundry/bureau-pipeline carries
    ref: ${{ inputs.pipeline_ref || 'main' }} — verbatim, so the value a
    stub passes reaches every internal checkout, and an empty `inputs`
    context (workflow_dispatch / repository_dispatch / schedule) still
    lands on main exactly like the old ref-less checkout.
  * EVERY reusable workflow (workflow_call trigger) declares the
    `pipeline_ref` input as type: string, default: main — so a stub that
    omits it stays byte-identical to today's rolling @main channel.

Live-extraction pattern (like tests/test_merge_gate_authorship.py): the
Live* tests parse the ACTUAL workflow files, so a future diff that adds a
ref-less internal checkout, drops the input, or forgets to thread it into
a new checkout turns this suite red. The synthetic tests pin each violation
class the checker must catch, so the checker itself can't rot.
"""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_pipeline_ref as cpr  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"

# The 9 reusable workflows at the time of DRE-2026. New reusable workflows
# are covered automatically by the directory sweep; this floor guards
# against the sweep going vacuous (e.g. a path typo finding zero files).
KNOWN_REUSABLE = {
    "agent-fix.yml",
    "agent-task.yml",
    "linear-sync.yml",
    "medic.yml",
    "merge-gate.yml",
    "plan.yml",
    "qa-review.yml",
    "reconcile.yml",
    "verify.yml",
}
# Internal checkout sites counted in the DRE-2026 audit (13). More is fine
# (new sites are swept automatically); fewer means sites vanished or the
# extractor broke — either way a human should look.
KNOWN_CHECKOUT_FLOOR = 13


def wf(text):
    return yaml.safe_load(text)


class LiveWorkflowsTest(unittest.TestCase):
    """Parse the real .github/workflows/*.yml — the shipping contract."""

    @classmethod
    def setUpClass(cls):
        cls.violations, cls.stats = cpr.check_dir(WORKFLOWS)
        cls.docs = {
            p.name: yaml.safe_load(p.read_text())
            for p in sorted(WORKFLOWS.glob("*.yml"))
        }

    def test_no_violations_in_live_workflows(self):
        """THE gate: every internal checkout threads pipeline_ref and every
        reusable workflow declares it. (RED before DRE-2026: 13 ref-less
        checkouts + 9 missing inputs.)"""
        self.assertEqual(
            self.violations, [],
            "\n".join(["DRE-2026 threading violations:"] + self.violations),
        )

    def test_extraction_is_not_vacuous(self):
        """The sweep actually saw the fleet: all known reusable workflows
        and at least the audited 13 internal checkout sites."""
        live_reusable = {
            name for name, doc in self.docs.items() if cpr.is_reusable(doc)
        }
        self.assertTrue(
            KNOWN_REUSABLE <= live_reusable,
            f"missing reusable workflows: {KNOWN_REUSABLE - live_reusable}",
        )
        self.assertGreaterEqual(
            self.stats["internal_checkouts"], KNOWN_CHECKOUT_FLOOR
        )

    def test_every_reusable_workflow_declares_pipeline_ref(self):
        """Explicit per-workflow enumeration (readable failure per file)."""
        for name, doc in self.docs.items():
            if not cpr.is_reusable(doc):
                continue
            with self.subTest(workflow=name):
                call = cpr._on_block(doc)["workflow_call"] or {}
                spec = (call.get("inputs") or {}).get("pipeline_ref")
                self.assertIsNotNone(
                    spec, f"{name}: no pipeline_ref workflow_call input"
                )
                self.assertEqual(spec.get("type"), "string", name)
                self.assertEqual(spec.get("default"), "main", name)

    def test_every_internal_checkout_threads_the_input(self):
        """Explicit per-site enumeration (readable failure per site)."""
        for name, doc in self.docs.items():
            for job_id, i, step in cpr.internal_checkouts(doc):
                with self.subTest(workflow=name, job=job_id, step=i):
                    self.assertEqual(
                        (step.get("with") or {}).get("ref"),
                        cpr.REQUIRED_REF_EXPR,
                        f"{name} jobs.{job_id}.steps[{i}]",
                    )

    def test_checker_cli_passes_on_live_tree(self):
        """The exact entrypoint Pipeline Tests runs."""
        self.assertEqual(cpr.main(["check_pipeline_ref.py", str(WORKFLOWS)]), 0)


class SyntheticViolationsTest(unittest.TestCase):
    """Each violation class the checker MUST catch — pins the checker."""

    def check(self, text):
        return cpr.check_workflow(wf(text), "synthetic.yml")

    def test_refless_internal_checkout_flagged(self):
        v = self.check(
            """
            on: {repository_dispatch: {types: [x]}}
            jobs:
              j:
                steps:
                  - uses: actions/checkout@v5
                    with:
                      repository: dreadnought-foundry/bureau-pipeline
                      path: .bureau-pipeline
            """
        )
        self.assertEqual(len(v), 1)
        self.assertIn("has no ref", v[0])

    def test_literal_ref_flagged(self):
        """A hardcoded ref (even 'main') breaks the channel: the stub's
        pipeline_ref value would never reach this checkout."""
        v = self.check(
            """
            on: {workflow_call: {inputs: {pipeline_ref: {type: string, default: main}}}}
            jobs:
              j:
                steps:
                  - uses: actions/checkout@v5
                    with:
                      repository: dreadnought-foundry/bureau-pipeline
                      ref: main
            """
        )
        self.assertEqual(len(v), 1)
        self.assertIn("must thread the input verbatim", v[0])

    def test_missing_input_flagged(self):
        v = self.check("on: {workflow_call: {secrets: {K: {required: false}}}}\njobs: {}")
        self.assertEqual(len(v), 1)
        self.assertIn("lacks the 'pipeline_ref' input", v[0])

    def test_wrong_default_flagged(self):
        v = self.check(
            "on: {workflow_call: {inputs: {pipeline_ref: {type: string, default: v1}}}}\njobs: {}"
        )
        self.assertEqual(len(v), 1)
        self.assertIn("must default to 'main'", v[0])

    def test_wrong_type_flagged(self):
        v = self.check(
            "on: {workflow_call: {inputs: {pipeline_ref: {type: boolean, default: main}}}}\njobs: {}"
        )
        self.assertEqual(len(v), 1)
        self.assertIn("must be type: string", v[0])

    def test_correct_workflow_clean(self):
        v = self.check(
            """
            on:
              workflow_call:
                inputs:
                  pipeline_ref: {type: string, default: main}
            jobs:
              j:
                steps:
                  - uses: actions/checkout@v5
                  - uses: actions/checkout@v5
                    with:
                      repository: dreadnought-foundry/bureau-pipeline
                      ref: ${{ inputs.pipeline_ref || 'main' }}
                      path: .bureau-pipeline
            """
        )
        self.assertEqual(v, [])

    def test_bare_and_foreign_checkouts_ignored(self):
        """Caller-repo checkouts and other repositories are out of scope."""
        v = self.check(
            """
            on: {repository_dispatch: {types: [x]}}
            jobs:
              j:
                steps:
                  - uses: actions/checkout@v5
                  - uses: actions/checkout@v5
                    with: {repository: someorg/other-repo}
            """
        )
        self.assertEqual(v, [])

    def test_on_parsed_as_boolean_true_key(self):
        """yaml.safe_load turns bare `on:` into the boolean True key; the
        checker must still see workflow_call behind it."""
        doc = yaml.safe_load(
            "on:\n  workflow_call:\n    secrets: {}\njobs: {}"
        )
        self.assertNotIn("on", doc)  # proves the YAML-1.1 quirk is in play
        self.assertIn(True, doc)
        self.assertTrue(cpr.is_reusable(doc))
        v = cpr.check_workflow(doc, "synthetic.yml")
        self.assertEqual(len(v), 1)
        self.assertIn("lacks the 'pipeline_ref' input", v[0])

    def test_on_as_list_is_not_reusable(self):
        doc = yaml.safe_load("on: [push, pull_request]\njobs: {}")
        self.assertFalse(cpr.is_reusable(doc))
        self.assertEqual(cpr.check_workflow(doc, "synthetic.yml"), [])


class CheckerCliTest(unittest.TestCase):
    def test_vacuous_directory_fails(self):
        """Zero internal checkouts = the checker is looking at the wrong
        tree; that must be a failure, not a silent pass."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            Path(d, "empty.yml").write_text("on: [push]\njobs: {}\n")
            self.assertEqual(cpr.main(["check_pipeline_ref.py", d]), 1)


if __name__ == "__main__":
    unittest.main()
