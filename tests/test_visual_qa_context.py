"""Tests for the critic-facing visual-QA context block (DRE-1481).

The block is the prose injected into BOTH critic prompts. Its wording carries
the safety contract, so we pin it:

  * SKIP and harness-DEGRADED blocks must explicitly tell the critic NOT to
    block — no false visual finding when there is no usable render.
  * The RUN block must hand the critic each (design, render) pair and say a
    material mismatch IS blocking — the "catch" path.
  * DEGRADED must SAY SO ON THE VERDICT (DRE-3248). It used to read "this is
    infrastructure flakiness … NOT a defect", so five deliberately-broken
    screens produced a verdict with nothing on it about the visual check —
    a skip wearing green. Not blocking is unchanged; being silent is not.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import visual_qa_context as vqc  # noqa: E402

BOARD_DESIGN = "console/design/images/screens/desktop/kanban-planning-board.png"


class SkipBlockTest(unittest.TestCase):
    def test_skip_says_do_not_block(self):
        block = vqc.build_context(
            {"run": False, "reason": "no **Design:** ref on the card/PR"},
            render_status="ok", render_note="", rendered={},
        )
        self.assertIn("skipped", block)
        self.assertIn("Do NOT", block)
        self.assertNotIn("blocking finding", block)


class DegradedBlockTest(unittest.TestCase):
    def _degraded(self, note="board screenshot failed"):
        return vqc.build_context(
            {"run": True, "screens": ["board"], "designs": [BOARD_DESIGN],
             "reason": "comparing 1 screen(s) against the design"},
            render_status="degraded", render_note=note, rendered={},
        )

    def test_harness_failure_is_non_fatal_note_not_a_block(self):
        block = self._degraded()
        self.assertIn("could not produce", block)
        self.assertIn("Do NOT", block)
        self.assertIn("board screenshot failed", block)
        # A degraded run must NOT instruct the critic that a mismatch is blocking
        # — there is no render to compare.
        self.assertNotIn("IS a blocking finding", block)

    def test_degraded_puts_one_plain_line_on_the_verdict(self):
        # DRE-3248 acceptance: when the harness fails, the verdict body carries
        # a "Visual check did not run" line. That line IS the fix — five red
        # screens went uncompared and the verdict said nothing.
        block = self._degraded()
        self.assertIn("Visual check did not run: board screenshot failed", block)
        self.assertIn("Summary", block)

    def test_degraded_no_longer_reads_as_expected_or_as_no_defect(self):
        # The old wording told the critic this was "infrastructure flakiness",
        # "expected" and "NOT a defect". A degraded gate must never read green.
        block = self._degraded()
        for phrase in ("infrastructure flakiness", "expected", "NOT a defect"):
            self.assertNotIn(phrase, block)

    def test_degraded_still_does_not_block(self):
        # Explicitly unchanged by DRE-3248: it stops being silent, it does not
        # start blocking.
        block = self._degraded()
        self.assertIn("do NOT block", block)

    def test_degraded_with_no_note_still_says_it_did_not_run(self):
        block = self._degraded(note="")
        self.assertIn("Visual check did not run:", block)
        # …and the line is never left dangling with nothing after the colon.
        line = next(l for l in block.splitlines() if "Visual check did not run:" in l)
        self.assertTrue(line.split("Visual check did not run:", 1)[1].strip())

    def test_the_skip_block_keeps_its_own_wording(self):
        # A SKIP is a PR with no design comparison to make — that IS expected,
        # and it must not start claiming a check failed.
        block = vqc.build_context(
            {"run": False, "reason": "no **Design:** ref on the card/PR"},
            render_status="ok", render_note="", rendered={},
        )
        self.assertIn("NOT a defect", block)
        self.assertNotIn("Visual check did not run", block)


class RunBlockTest(unittest.TestCase):
    def test_run_hands_critic_both_paths_and_makes_mismatch_blocking(self):
        block = vqc.build_context(
            {"run": True, "screens": ["board"], "designs": [BOARD_DESIGN],
             "reason": "comparing 1 screen(s) against the design"},
            render_status="ok", render_note="",
            rendered={"board": "console/design/images/_renders/board.png"},
        )
        self.assertIn(BOARD_DESIGN, block)                                   # design
        self.assertIn("console/design/images/_renders/board.png", block)    # render
        self.assertIn("widths", block)
        self.assertIn("blocking finding", block)
        # Tells the critic to ignore sub-pixel noise (avoid false blocks).
        self.assertIn("antialiasing", block.lower())

    def test_run_default_render_path_when_not_supplied(self):
        block = vqc.build_context(
            {"run": True, "screens": ["agents"],
             "designs": ["console/design/images/screens/desktop/agents-configuration.png"]},
            render_status="ok", render_note="", rendered={},
        )
        self.assertIn("console/design/images/_renders/agents.png", block)


class CliTest(unittest.TestCase):
    def _run(self, plan, status="ok", note="", rendered=None):
        with tempfile.TemporaryDirectory() as td:
            pf = os.path.join(td, "plan.json")
            with open(pf, "w") as f:
                json.dump(plan, f)
            cmd = [sys.executable,
                   os.path.join(os.path.dirname(__file__), "..", "scripts",
                                "visual_qa_context.py"),
                   "--plan-file", pf, "--render-status", status, "--render-note", note]
            if rendered:
                cmd += ["--rendered", *rendered]
            return subprocess.run(cmd, capture_output=True, text=True)

    def test_cli_skip(self):
        p = self._run({"run": False, "reason": "no design ref"})
        self.assertEqual(p.returncode, 0)
        self.assertIn("skipped", p.stdout)

    def test_cli_run(self):
        p = self._run(
            {"run": True, "screens": ["board"], "designs": [BOARD_DESIGN]},
            rendered=["board=console/design/images/_renders/board.png"],
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn(BOARD_DESIGN, p.stdout)
        self.assertIn("blocking finding", p.stdout)

    def test_cli_degraded_carries_the_verdict_line(self):
        p = self._run(
            {"run": True, "screens": ["board"], "designs": [BOARD_DESIGN]},
            status="degraded", note="screenshot harness exited 1 (see logs)",
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn(
            "Visual check did not run: screenshot harness exited 1 (see logs)",
            p.stdout,
        )

    def test_cli_missing_plan_file_degrades_to_skip(self):
        p = subprocess.run(
            [sys.executable,
             os.path.join(os.path.dirname(__file__), "..", "scripts", "visual_qa_context.py"),
             "--plan-file", "/nonexistent/plan.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn("skipped", p.stdout)


if __name__ == "__main__":
    unittest.main()
