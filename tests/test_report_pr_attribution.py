"""Regression pin: the agent-task report step must never claim a PR it
didn't verify belongs to this card.

Origin (2026-06-12, DRE-1343): the agent died without pushing a branch.
The report step's lookup — `gh pr list --head "$(git branch -r | grep -o
"agent/${CARD}..." ...)"` — got an EMPTY head from the grep, and `gh pr
list --head ""` returns the repo's most recent open PR. The step commented
"PR opened: .../pull/1366" (another card's PR) and advanced the card to
In QA, masking a dead run that the dead-run requeue path would otherwise
have caught immediately.

The fix lives in shell inside agent-task.yml: resolve BRANCH first, only
query gh when BRANCH is non-empty, and require the found PR's head to be
that branch. These tests pin the script text of the report step.
"""

import os
import re
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)


def report_step() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"- name: Report result to Linear(.*)\Z", src, re.S)
    if not m:
        raise AssertionError("report step not found in agent-task.yml")
    return m.group(1)


class ReportPrAttributionTest(unittest.TestCase):
    def test_branch_resolved_separately_and_guarded(self):
        step = report_step()
        self.assertIn('BRANCH=$(', step)
        self.assertIn('if [ -n "$BRANCH" ]; then', step)

    def test_no_inline_command_substitution_in_head_filter(self):
        # The original bug: --head "$(git branch -r | grep ...)" — an empty
        # substitution silently matches every PR. The filter must use the
        # pre-resolved, guarded $BRANCH variable.
        self.assertNotIn('--head "$(', report_step())

    def test_empty_branch_cannot_reach_pr_claim(self):
        # PR_URL must only be derived inside the non-empty-branch guard.
        step = report_step()
        guard = step.find('if [ -n "$BRANCH" ]; then')
        claim = step.find("PR_URL=$(gh pr list")
        self.assertGreater(claim, guard, "PR lookup must be inside the BRANCH guard")


if __name__ == "__main__":
    unittest.main()
