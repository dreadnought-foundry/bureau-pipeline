"""DRE-2776: an escalation is not a broken card — it parks in Green Light.

DRE-2722 moved every "a person is needed" park to `Triage` on the strength of
its own title. The criteria it was actually accepted against said something
narrower: Triage takes the UNROUTABLE and the HELD card, and `Green Light`
keeps what the lane it renamed held — which included the escalate-by-exception
question. Operator decision, 2026-08-27: escalations belong in `Green Light`,
the CEO's "needs you" queue. A real decision sitting in a lane people scan as a
defect list is how Triage rotted the first time (17 machine-created cards, zero
transitions, 2026-08-24).

The split this file pins, in one sentence: **a card parked because a human must
DECIDE goes to `Green Light`; a card parked because it went WRONG stays in
`Triage`.** So the engineer's escalate-by-exception path moves; the fix loop's
non-convergence park, the unfixable-check hold and the red-main repair card —
each of them a card that went wrong — do not, and stay pinned next door in
`test_green_light_rename.py`.

Four surfaces have to agree or the rule is only true in one of them: the shell
that actually moves the card, the prompt that tells the agent where its
question lands, the brief it reads when deciding to escalate, and the standard
that brief inherits. The review of this change found the standard updated and
the other three untouched — which is the whole reason this file exists.
"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: Where a card goes when a human owes it a DECISION, and where it goes when it
#: is simply broken. Named apart because the whole card is the distinction.
DECISION_LANE = "Green Light"
BROKEN_CARD_LANE = "Triage"

AGENT_TASK = os.path.join(".github", "workflows", "agent-task.yml")


def read(rel: str) -> str:
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def report_step() -> str:
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)",
        read(AGENT_TASK),
        re.S,
    )
    assert m, "'Report result to Linear' step not found in agent-task.yml"
    return m.group(1)


def escalation_branch() -> str:
    """The escalate-by-exception branch — the shell that actually runs when an
    agent stops to ask the CEO a question."""
    m = re.search(
        r"elif \[ -f /tmp/agent-escalation\.txt \](.*?)\n\s*elif \[ -f /tmp/agent-blocker\.txt \]",
        report_step(),
        re.S,
    )
    assert m, "escalation branch not found in agent-task.yml"
    return m.group(1)


def escalate_prompt_item() -> str:
    """Step 6 of the build agent's own prompt — what the agent is TOLD happens
    to its question."""
    m = re.search(r"\n +6\. ESCALATE(.*?)\n +7\. ", read(AGENT_TASK), re.S)
    assert m, "the ESCALATE prompt item was not found in agent-task.yml"
    return m.group(1)


def brief_escalation_section() -> str:
    """`briefs/engineer.md`'s "How to escalate" — the operational instructions
    an agent follows at escalation time, which is not the same document as the
    standard below."""
    m = re.search(r"### How to escalate(.*?)\n## ", read("briefs/engineer.md"), re.S)
    assert m, "'How to escalate' section not found in briefs/engineer.md"
    return m.group(1)


class TheShellThatMovesTheCardTest(unittest.TestCase):
    """Half one: the live code path. Prose can say anything; this is the line
    that hands Linear a lane name."""

    def test_the_escalation_branch_advances_to_the_decision_lane(self):
        self.assertIn(f'"{DECISION_LANE}"', escalation_branch())

    def test_the_broken_card_lane_is_gone_from_the_branch_entirely(self):
        # Not just "does not advance to Triage": the literal must be absent
        # from the branch, comment included. The retired rule was restated in
        # the inline comment beside the command, which is exactly where the
        # next reader re-derives it from.
        self.assertNotIn(BROKEN_CARD_LANE, escalation_branch())

    def test_the_branch_still_posts_the_question_before_moving_the_card(self):
        # Moving the card without the question is a silent park: the CEO sees a
        # card appear in their queue with nothing to answer.
        branch = escalation_branch()
        self.assertLess(
            branch.index("linear_ops.py comment"),
            branch.index(f'"{DECISION_LANE}"'),
        )

    def test_the_prompt_names_the_same_lane_the_branch_uses(self):
        # A prompt that names a different lane than the shell is a lie the
        # agent repeats to the CEO in its own escalation note.
        item = escalate_prompt_item()
        self.assertIn(DECISION_LANE, item)
        self.assertNotIn(BROKEN_CARD_LANE, item)


class TheInstructionsTheAgentReadsTest(unittest.TestCase):
    """Half two: the documents. The brief is what an agent consults at the
    moment it decides to escalate — the standard it inherits is one hop
    further away, and the two disagreeing is how this bug shipped."""

    def test_the_brief_routes_escalations_to_the_decision_lane(self):
        self.assertIn(DECISION_LANE, brief_escalation_section())

    def test_the_brief_does_not_route_escalations_to_the_broken_card_lane(self):
        self.assertNotIn(BROKEN_CARD_LANE, brief_escalation_section())

    def test_the_standard_routes_escalations_to_the_decision_lane(self):
        self.assertIn(
            f"parks the card in the **`{DECISION_LANE}`** lane",
            read("standards/card-quality.md"),
        )

    def test_the_readme_describes_the_same_route(self):
        m = re.search(
            r"stops and\s+asks only by exception(.*?)\n\n", read("README.md"), re.S
        )
        assert m, "the README's escalate-by-exception paragraph was not found"
        self.assertIn(DECISION_LANE, m.group(1))
        self.assertNotIn(BROKEN_CARD_LANE, m.group(1))


class HumanParkGateTest(unittest.TestCase):
    """The dispatch gate (DRE-2024) asks exactly one question: does a human owe
    this card an action before automation may act again? Both queues answer
    yes, so both have to gate. Recognising only one of them is the DeltaSolv
    PR #120 loop — an identical doomed fix run every sweep, forever."""

    def _parked(self, lane: str) -> bool:
        payload = {"issue": {"state": {"name": lane}, "labels": {"nodes": []}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            return reconcile.card_parked_for_human("DRE-2009")

    def test_a_card_awaiting_a_decision_is_human_parked(self):
        self.assertTrue(self._parked(DECISION_LANE))

    def test_a_broken_card_is_still_human_parked(self):
        self.assertTrue(self._parked(BROKEN_CARD_LANE))

    def test_a_working_lane_is_not_human_parked(self):
        self.assertFalse(self._parked("In QA"))

    def test_both_queues_are_declared_in_one_place(self):
        # One tuple, so a third human queue is onboarded by editing a list and
        # not by finding every `== PARKED_STATE` in the sweep.
        self.assertEqual(
            (BROKEN_CARD_LANE, DECISION_LANE), reconcile.PARKED_STATES
        )


if __name__ == "__main__":
    unittest.main()
