"""Console-honesty standard — badges derive from what actually happened
(DRE-2107, child of DRE-2073).

DRE-2023's watchdog read In-Progress-no-PR staleness as death while three
builds were actually alive, then killed the runs it was counting — state
inferred from adjacent signals instead of fetched from the source of truth.
The standard codifies that lesson for every console/UI surface that renders
pipeline state, and rides the same assemble_context.py rail as every other
standard (DRE-1646) so it reaches the roles that build and review console
work.

These tests pin the two halves:

  1. The STANDARD — standards/console-honesty.md exists and carries the
     three rules (derive-from-truth, explicit stale/absent rendering, the
     mandatory stale-data test) plus the critic lens that checks them on
     console cards.
  2. The RAIL — assemble_context.py injects it for engineer/frontend/critic
     (the exact role lists are pinned in test_assemble_context.py; this
     suite only asserts the standard is on the rail at all, mirroring
     test_vendor_boundaries_wiring.py).
"""

import os
import sys
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
STANDARD = os.path.join(REPO, "standards", "console-honesty.md")
README = os.path.join(REPO, "standards", "README.md")
sys.path.insert(0, os.path.join(REPO, "scripts"))


def body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach the console-building/reviewing
    roles via assemble_context.py — the DRE-1646 single-source rail."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/console-honesty.md must exist",
        )

    def test_console_roles_receive_the_standard(self):
        import assemble_context as ac

        for role in ("engineer", "frontend", "critic"):
            self.assertIn(
                "console-honesty.md",
                ac.standards_for(role),
                f"{role} must receive the console-honesty standard — it "
                "builds or reviews console surfaces that render pipeline "
                "state",
            )

    def test_readme_lists_the_standard(self):
        with open(README, encoding="utf-8") as f:
            self.assertIn(
                "console-honesty.md", f.read(),
                "standards/README.md must list console-honesty.md",
            )


class DeriveFromTruthTest(unittest.TestCase):
    """Rule 1: every badge/button/status derives from what ACTUALLY
    happened — live state fetched from the source of truth, never inference
    from adjacent signals."""

    def test_source_of_truth_named(self):
        text = body()
        self.assertIn("source of truth", text)
        self.assertIn("adjacent signals", text)

    def test_the_critic_crash_example(self):
        # The canonical confusion the rule exists to prevent: a crashed
        # review rendered as a rejection.
        text = body()
        self.assertIn("review didn't run", text)
        self.assertIn("critic rejected", text)

    def test_the_dre_2023_origin(self):
        # The seed incident: staleness read as death, the watchdog killing
        # the live runs it was counting.
        self.assertIn("DRE-2023", body())


class StaleAbsentRenderingTest(unittest.TestCase):
    """Rule 2: every state element defines its stale-data and absent-data
    rendering explicitly — unknown shown as unknown, never the last known
    value, never a ghost row."""

    def test_stale_and_absent_defined_explicitly(self):
        text = body()
        self.assertIn("stale", text.lower())
        self.assertIn("absent", text.lower())

    def test_unknown_is_shown_as_unknown(self):
        text = body()
        self.assertIn("unknown", text.lower())
        self.assertIn("last known value", text)

    def test_no_ghost_rows(self):
        self.assertIn("ghost row", body())


class MandatoryStaleDataTestTest(unittest.TestCase):
    """Rule 3: every state element ships with a stale/absent-data test; a
    console card without one is incomplete."""

    def test_stale_data_test_is_mandatory(self):
        text = body()
        self.assertIn("test", text.lower())
        self.assertIn("incomplete", text)


class CriticLensTest(unittest.TestCase):
    """On console cards the critic explicitly checks the three rules — the
    standard must say so."""

    def test_critic_lens_exists(self):
        text = body().lower()
        self.assertIn("critic", text)
        self.assertIn("three rules", text)


if __name__ == "__main__":
    unittest.main()
