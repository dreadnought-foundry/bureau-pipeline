"""Vendor-boundaries standard — the vendor-behavior premortem checklist
(DRE-2105, child of DRE-2073).

Every bug in the 2026-07-12 dependency-automation rollout lived at the
GitHub boundary, not in our logic: the vendor's actual behavior on actors,
secrets stores, ignore commands, and crash recovery surprised us ten times
in production while unit TDD stayed green. The standard turns those
surprises into a premortem checklist answered BEFORE building, and rides
the same assemble_context.py rail as every other standard (DRE-1646) so it
reaches the roles that plan, build, and review boundary-touching work.

These tests pin the two halves:

  1. The STANDARD — standards/vendor-boundaries.md exists, carries the five
     checklist questions, every seed incident (the ten 2026-07-12
     GitHub-boundary surprises + the June quota/medic-loop lessons), and an
     explicit critic section (an unanswered checklist question on a
     boundary-touching PR is a finding).
  2. The RAIL — assemble_context.py injects it for planner/engineer/
     frontend/devops/critic (the exact role lists are pinned in
     test_assemble_context.py; this suite only asserts the standard is on
     the rail at all, mirroring test_untrusted_content_wiring.py).
"""

import os
import sys
import unittest

REPO = os.path.join(os.path.dirname(__file__), "..")
STANDARD = os.path.join(REPO, "standards", "vendor-boundaries.md")
README = os.path.join(REPO, "standards", "README.md")
sys.path.insert(0, os.path.join(REPO, "scripts"))


def body() -> str:
    with open(STANDARD, encoding="utf-8") as f:
        return f.read()


class StandardOnTheRailTest(unittest.TestCase):
    """The standard must exist and reach the boundary roles via
    assemble_context.py — the DRE-1646 single-source rail."""

    def test_standard_file_exists(self):
        self.assertTrue(
            os.path.isfile(STANDARD),
            "standards/vendor-boundaries.md must exist",
        )

    def test_boundary_roles_receive_the_standard(self):
        import assemble_context as ac

        for role in ("planner", "engineer", "frontend", "devops", "critic"):
            self.assertIn(
                "vendor-boundaries.md",
                ac.standards_for(role),
                f"{role} must receive the vendor-boundaries standard — it "
                "plans, builds, or reviews boundary-touching work",
            )

    def test_readme_lists_the_standard(self):
        with open(README, encoding="utf-8") as f:
            self.assertIn(
                "vendor-boundaries.md", f.read(),
                "standards/README.md must list vendor-boundaries.md",
            )


class ChecklistTest(unittest.TestCase):
    """The five premortem questions from DRE-2105 — each must be asked in
    the standard, in agent-actionable terms."""

    def test_initiating_actor_question(self):
        text = body()
        self.assertIn("initiating actor", text)
        self.assertIn("allowed_bots", text)

    def test_secrets_store_question(self):
        text = body()
        self.assertIn("secrets store", text)
        self.assertIn("Dependabot", text)

    def test_vendor_retry_close_reopen_question(self):
        text = body()
        for verb in ("retry", "close", "reopen", "ignore", "rebase", "re-file"):
            self.assertIn(
                verb, text,
                f"the checklist must ask what the vendor does on {verb!r}",
            )

    def test_command_limitations_question(self):
        self.assertIn("single-dependency", body())

    def test_crash_recovery_question(self):
        text = body()
        self.assertIn("crash", text)
        self.assertIn("receipt", text)


class SeedIncidentsTest(unittest.TestCase):
    """Every documented incident the checklist is distilled from must appear
    — one line of what GitHub actually did plus the question that would have
    caught it. Pinned by the identifying card refs / dates so a rewrite
    cannot silently drop the evidence."""

    def test_the_five_allowlist_lockouts(self):
        text = body()
        # pool bots / qa-bot / dependabot / github-actions (bp) /
        # github-actions again on the fleet's stale v3 tag.
        for ref in ("DRE-2020", "DRE-2037", "DRE-2039", "DRE-2053"):
            self.assertIn(ref, text, f"lockout incident {ref} must be seeded")
        self.assertIn("github-actions", text)
        self.assertIn("v3", text)

    def test_the_empty_dependabot_secrets_store(self):
        text = body()
        self.assertIn("DRE-2047", text)
        self.assertIn("DRE-2067", text)

    def test_the_ignore_walk_down(self):
        text = body()
        self.assertIn("DRE-2064", text)
        self.assertIn("walk", text.lower())

    def test_the_multi_dep_ignore_limitation(self):
        self.assertIn("DRE-2062", body())

    def test_receipts_blocked_crashed_run_recovery_twice(self):
        text = body()
        self.assertIn("DRE-2071", text)
        self.assertIn("27", text)  # the 27 frozen agent-bureau reviews
        self.assertIn("6", text)  # the 6 hand-dispatched fleet reviews

    def test_the_june_lessons(self):
        text = body()
        self.assertIn("2026-06-28", text)
        self.assertIn("DRE-1921", text)
        self.assertIn("medic", text.lower())

    def test_rollout_date_named(self):
        self.assertIn("2026-07-12", body())


class CriticSectionTest(unittest.TestCase):
    """On a boundary-touching PR the critic walks the checklist and treats
    an unanswered question as a finding — the standard must say so."""

    def test_critic_section_exists(self):
        text = body()
        self.assertIn("critic", text.lower())
        self.assertIn("finding", text.lower())

    def test_unanswered_question_is_a_finding(self):
        self.assertIn("unanswered", body().lower())


if __name__ == "__main__":
    unittest.main()
