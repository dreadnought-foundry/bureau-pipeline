"""Regression pin: a fix run that can't move the PR forward escalates to the
human instead of stalling the card silently in In QA.

Origin (2026-06-28): DeltaSolv PRs #64 (DRE-1848) and #74 (DRE-1825) each sat
~20h in "In QA" and had to be hand-merged. Both ended with the branch's LATEST
QA Critic verdict still REQUEST_CHANGES and NO new commit to re-review:

  * #64 — the fix agent DISPUTED the critic's blocking finding (wrote
    /tmp/fix-blocker.txt) and, per its instructions, pushed nothing.
  * #74 — the fix agent reported "Fix attempt 2 pushed" but the branch head
    SHA never advanced (a phantom push), so CI + the critic never re-ran.

In both cases merge-gate behaved CORRECTLY ("latest verdict is not APPROVE —
holding") — the gap was upstream: agent-fix only posted an advisory comment and
left the card in In QA with a stale REQUEST_CHANGES the critic would never lift
on its own. Nothing surfaced it to the CEO, so it stalled invisibly.

The fix: both the dispute path and the no-new-commit path must park the card in
Plan Review (the "needs you" lane) + stamp needs-human, exactly as
agent-task.yml escalates. These tests pin the Report step's shell to that shape.
"""

import os
import re
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)


def report_step() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"name:\s*Report\b(.*?)(?:\n      - name:|\Z)", src, re.S)
    if not m:
        raise AssertionError("'Report' step not found in agent-fix.yml")
    return m.group(1)


class FixDisputeEscalatesTest(unittest.TestCase):
    def test_report_step_parks_in_plan_review(self):
        # A disputed or no-progress fix must route the card to the human queue.
        self.assertIn('"Plan Review"', report_step())

    def test_report_step_stamps_needs_human(self):
        self.assertIn("needs-human", report_step())

    def test_dispute_branch_no_longer_only_narrates(self):
        # The fix-blocker branch must DO something with the card state, not just
        # post a "needs a human look" comment that leaves it stuck in In QA.
        step = report_step()
        m = re.search(
            r"if \[ -f /tmp/fix-blocker\.txt \]; then(.*?)\n\s*else\b", step, re.S
        )
        self.assertIsNotNone(m, "fix-blocker branch not found in Report step")
        self.assertIn("Plan Review", m.group(1) + step)

    def test_detects_phantom_push_by_comparing_head_sha(self):
        # The success branch must verify the head SHA actually advanced before
        # claiming "review re-running" — a no-op push re-triggers nothing.
        step = report_step()
        self.assertIn("POST_SHA", step)
        self.assertIn("PRE_SHA", step)

    def test_resolve_step_exposes_head_sha_output(self):
        # The pre-run head SHA must be captured as a step output for the compare.
        src = open(WORKFLOW).read()
        self.assertIn('echo "head_sha=$HEAD_SHA"', src)
        self.assertIn("steps.pr.outputs.head_sha", src)


if __name__ == "__main__":
    unittest.main()
