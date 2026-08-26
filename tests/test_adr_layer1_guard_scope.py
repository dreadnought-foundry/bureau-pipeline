"""ADR pin for Layer 1's scope (DRE-2754).

`architecture/decisions/adr-layer-1-guard-scope.md` is the decision record the
card's first acceptance criterion asks for: the boundary chosen, the reason, the
two options rejected and why, the one hazard it carries, and the plan.yml
ordering that produced the collision. These tests pin the load-bearing facts so
the record cannot rot into a summary of a decision nobody can reconstruct:

  * it names its own card and every card in the collision (DRE-2725 the guard,
    DRE-2719 Planning-produces-children, DRE-2737 break-glass);
  * it states the boundary — Planning exit — and the three lanes upstream of it;
  * BOTH rejected options are recorded WITH their reason, not just listed;
  * the hazard is written down: the boundary is only as good as the definition
    of where verdicts are produced;
  * it states what does NOT change, with the addresses (`linear_ops.py:686`,
    `plan.yml:255`, the epic move) so the next reader does not re-derive them;
  * the code the rule lives in is named AND exists — an ADR pointing at a file
    that was never written is design fiction.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "architecture" / "decisions" / "adr-layer-1-guard-scope.md"


class TestAdrLayer1GuardScope(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ADR.is_file(), f"missing {ADR.relative_to(ROOT)}")
        self.text = ADR.read_text()

    def test_names_its_card_and_the_colliding_cards(self):
        for card in ("DRE-2754", "DRE-2725", "DRE-2719", "DRE-2737"):
            self.assertIn(card, self.text)

    def test_states_the_boundary_by_name(self):
        self.assertIn("Planning exit", self.text)

    def test_names_the_three_unpoliced_lanes(self):
        for lane in ("Intake", "Planning", "Green Light"):
            self.assertIn(lane, self.text)

    def test_records_the_rejected_option_of_a_pending_marker_in_intake(self):
        # Option 1: children into Intake with a pending marker. Rejected
        # because it dilutes the one surface the CEO actually reads.
        self.assertRegex(self.text, r"(?i)option 1")
        self.assertRegex(self.text, r"(?i)pending marker")
        self.assertRegex(self.text, r"(?i)rejected")

    def test_records_the_rejected_option_of_a_holding_lane(self):
        # Option 2: a Planning-scoped holding lane. Rejected, kept as the
        # fallback — the record must carry the fallback status too.
        self.assertRegex(self.text, r"(?i)option 2")
        self.assertRegex(self.text, r"(?i)holding lane")
        self.assertRegex(self.text, r"(?i)fallback")

    def test_says_why_this_is_not_an_exception_list(self):
        self.assertRegex(self.text, r"(?i)exception list")
        self.assertRegex(self.text, r"(?i)derived")

    def test_records_the_hazard(self):
        self.assertRegex(self.text, r"(?i)hazard")
        self.assertRegex(self.text, r"(?i)pre-verdict lanes")

    def test_states_the_plan_yml_ordering_with_addresses(self):
        # The card's last criterion: state the sequence so the next reader does
        # not have to rediscover it.
        self.assertIn("plan.yml:255", self.text)
        self.assertRegex(self.text, r"plan\.yml:3\d\d")

    def test_states_what_does_not_change(self):
        self.assertIn("linear_ops.py:686", self.text)
        self.assertRegex(self.text, r"(?i)backlog")

    def test_names_the_module_the_rule_lives_in_and_it_exists(self):
        self.assertIn("scripts/lane_scope.py", self.text)
        self.assertTrue((ROOT / "scripts" / "lane_scope.py").is_file())

    def test_addresses_it_cites_in_this_repo_are_real(self):
        # Every `<file>:<line>` the ADR cites for a file in THIS repo must
        # point at a line that exists — DRE-2725 spent a review round on line
        # numbers that had drifted.
        for path, line in re.findall(r"`?([\w./-]+\.(?:py|yml)):(\d+)`?", self.text):
            target = ROOT / path if (ROOT / path).exists() else ROOT / "scripts" / path
            if not target.is_file():
                target = ROOT / ".github" / "workflows" / path
            self.assertTrue(target.is_file(), f"ADR cites missing file {path}")
            self.assertLessEqual(
                int(line),
                len(target.read_text().splitlines()),
                f"ADR cites {path}:{line}, past end of file",
            )
