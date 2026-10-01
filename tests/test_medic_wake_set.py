"""RED-first (DRE-3625): the medic's wake set is declared once — `failure` and
`timed_out` — and the reusable medic reads it.

Before this card `.github/workflows/medic.yml` held the rule as the literal
`github.event.workflow_run.conclusion == 'failure'` in nine job `if:` sites,
pinned by nothing — and one short: a run that concluded `timed_out` never
woke the medic at all. The operator's decision (DRE-3530, 2026-09-10): wake on
`failure` and `timed_out`; stay silent on `cancelled`, `success` and `skipped`.

`scripts/medic_wake.py` declares the set and renders the one GitHub Actions
expression that reads it; the workflow carries the rendered text. A workflow
cannot import Python, so this file is what holds the two to each other:

  1. the declaration — `WAKE_ON` is exactly the operator's two conclusions,
     and `SILENT_ON` gives each conclusion it leaves out a reason;
  2. the rendering — `expression()` is the contract string the stub cards
     (DRE-3626, DRE-3627) write identically;
  3. the workflow, live-extracted — every job `if:` that reads
     `workflow_run.conclusion` reads it only through `expression()`, and
     `classify`'s `if:` is exactly it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_medic_wake_set.py -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import medic_wake  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "medic.yml"
SCRIPT = ROOT / "scripts" / "medic_wake.py"

# The contract shared with DRE-3626 and DRE-3627, written out rather than
# derived — a rendering that drifted would otherwise pass against itself.
CONTRACT = (
    "(github.event.workflow_run.conclusion == 'failure' || "
    "github.event.workflow_run.conclusion == 'timed_out')"
)


def _gates() -> dict:
    """Every job's `if:` that reads the woken run's conclusion, by job name."""
    jobs = yaml.safe_load(WORKFLOW.read_text("utf-8"))["jobs"]
    return {
        name: str(job["if"])
        for name, job in jobs.items()
        if "workflow_run.conclusion" in str(job.get("if", ""))
    }


# ── 1. the declaration ───────────────────────────────────────────────────────
class WakeSetTest(unittest.TestCase):
    def test_wake_on_is_failure_and_timed_out(self):
        self.assertEqual(medic_wake.WAKE_ON, ("failure", "timed_out"))

    def test_silent_on_names_the_three_quiet_conclusions_with_reasons(self):
        for conclusion in ("success", "skipped", "cancelled"):
            self.assertIn(conclusion, medic_wake.SILENT_ON)
            reason = medic_wake.SILENT_ON[conclusion]
            self.assertIsInstance(reason, str)
            self.assertTrue(reason.strip(), f"{conclusion} has no reason")

    def test_no_silent_conclusion_wakes_the_medic(self):
        for conclusion in ("success", "skipped", "cancelled"):
            self.assertNotIn(conclusion, medic_wake.WAKE_ON)
        self.assertFalse(set(medic_wake.WAKE_ON) & set(medic_wake.SILENT_ON))

    def test_the_docstring_carries_the_operator_s_cancelled_decision(self):
        doc = medic_wake.__doc__ or ""
        self.assertIn("cancelled", doc)
        self.assertIn("DRE-3530", doc)
        self.assertIn("2026-09-10", doc)


# ── 2. the rendering ─────────────────────────────────────────────────────────
class ExpressionTest(unittest.TestCase):
    def test_expression_is_the_contract(self):
        self.assertEqual(medic_wake.expression(), CONTRACT)

    def test_expression_is_rendered_from_wake_on(self):
        # One comparison per member, so the declaration is what it reads.
        for conclusion in medic_wake.WAKE_ON:
            self.assertIn(
                f"github.event.workflow_run.conclusion == '{conclusion}'",
                medic_wake.expression(),
            )
        self.assertEqual(
            medic_wake.expression().count("||"), len(medic_wake.WAKE_ON) - 1
        )

    def test_the_script_prints_the_expression(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(out.strip(), CONTRACT)


# ── 3. the workflow reads it ─────────────────────────────────────────────────
class MedicReadsTheWakeSetTest(unittest.TestCase):
    def test_the_gates_are_found(self):
        # A vacuity guard: the nine sites of 2268dc5, at least.
        self.assertGreaterEqual(len(_gates()), 9, sorted(_gates()))

    def test_every_conclusion_gate_carries_the_expression(self):
        for name, gate in _gates().items():
            with self.subTest(job=name):
                self.assertIn(medic_wake.expression(), gate)

    def test_no_gate_reads_the_conclusion_outside_the_expression(self):
        for name, gate in _gates().items():
            with self.subTest(job=name):
                rest = gate.replace(medic_wake.expression(), "")
                self.assertNotIn("workflow_run.conclusion", rest)

    def test_classify_gate_is_exactly_the_expression(self):
        jobs = yaml.safe_load(WORKFLOW.read_text("utf-8"))["jobs"]
        self.assertEqual(jobs["classify"]["if"], medic_wake.expression())

    def test_the_header_names_the_timeout(self):
        head = WORKFLOW.read_text("utf-8").splitlines()[0]
        self.assertIn("fails or times out", head)


if __name__ == "__main__":
    unittest.main()
