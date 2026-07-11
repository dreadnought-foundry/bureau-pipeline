"""ADR pin for the red-main auto-repair agent (DRE-1925 — design before build).

architecture/decisions/adr-red-main-auto-repair.md is the sign-off artifact
the DRE-1923 build card is blocked on: the trigger, the fix flow, and a
concrete mechanism for each of the four guardrails. These tests pin the
load-bearing facts so the ADR can't silently rot or ship hollow:

  * the ADR exists, names its card (DRE-1925) and the build card it gates
    (DRE-1923), and carries an explicit CEO sign-off section;
  * the trigger is event-driven off `workflow_run` conclusion=failure on
    main — and the ADR says out loud that no new polling loop is added;
  * every one of the four guardrails is present WITH its mechanism: the
    named script / identity / lock it rides on, not just an intention;
  * the scripts the mechanisms lean on actually exist in this repo (a
    rename there must update the ADR too).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "architecture" / "decisions" / "adr-red-main-auto-repair.md"

# Scripts the guardrail mechanisms are grounded in. The ADR must name each,
# and each must exist — an ADR pointing at a deleted asset is design fiction.
GROUNDING_SCRIPTS = ("medic_classify.py", "dispatch_pool.py")


class TestAdrRedMainAutoRepair(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ADR.is_file(), f"missing {ADR.relative_to(ROOT)}")
        self.text = ADR.read_text()

    def test_names_its_card_and_the_build_card_it_gates(self):
        self.assertIn("DRE-1925", self.text)
        self.assertIn("DRE-1923", self.text)

    def test_carries_a_ceo_sign_off_section(self):
        # The whole point of design-before-build: the build card starts only
        # after the CEO signs off, and the ADR is where that gate is written.
        self.assertIn("sign-off", self.text.lower())
        self.assertIn("CEO", self.text)

    def test_trigger_is_event_driven_not_polling(self):
        self.assertIn("workflow_run", self.text)
        self.assertIn("failure", self.text)
        self.assertIn("no new polling", self.text.lower())

    def test_fix_flow_keeps_the_two_robot_identity_split(self):
        # The repair agent authors; only the qa identity merges. Enforced by
        # different GitHub Apps, so the ADR must name both identities.
        self.assertIn("agent-bureau-bot", self.text)
        self.assertIn("agent-bureau-qa-bot", self.text)

    def test_fix_flow_escalates_to_plan_review(self):
        self.assertIn("Plan Review", self.text)

    def test_guardrail_no_test_gutting_has_a_critic_mechanism(self):
        # Not just "the critic reviews it": the ADR must state the
        # stale-test-vs-broken-code distinction and the rejection verdict a
        # weakening diff earns.
        self.assertIn("stale", self.text.lower())
        self.assertIn("REQUEST_CHANGES", self.text)
        self.assertIn("weaken", self.text.lower())

    def test_guardrail_no_crash_loop_rides_the_classifier(self):
        # Bounded attempts + medic_classify back-off — the 2026-06-28
        # medic↔critic quota crash-loop is the incident this must never
        # rebuild, so the ADR names it.
        self.assertIn("medic_classify.py", self.text)
        self.assertIn("2026-06-28", self.text)
        self.assertIn("back off", self.text.lower().replace("backs off", "back off"))

    def test_guardrail_concurrency_lock_is_per_repo_and_debounced(self):
        self.assertIn("one repair in flight", self.text.lower())
        self.assertIn("debounce", self.text.lower())
        # The concurrent-clobber lesson the lock exists because of.
        self.assertIn("DRE-1803", self.text)

    def test_guardrail_quota_isolation_rides_the_dispatch_pool(self):
        self.assertIn("dispatch_pool.py", self.text)
        self.assertIn("card dispatch", self.text.lower())

    def test_grounding_scripts_are_named_and_exist(self):
        for script in GROUNDING_SCRIPTS:
            self.assertIn(script, self.text)
            self.assertTrue(
                (ROOT / "scripts" / script).is_file(),
                f"the ADR grounds a guardrail in scripts/{script}, which no "
                "longer exists — update the ADR alongside the removal",
            )


if __name__ == "__main__":
    unittest.main()
