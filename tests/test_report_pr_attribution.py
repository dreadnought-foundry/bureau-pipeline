"""Regression pin: the agent-task report step must never claim a PR it
didn't verify belongs to this card.

Origin (2026-06-12, DRE-1343): the agent died without pushing a branch.
The report step's lookup — `gh pr list --head "$(git branch -r | grep -o
"agent/${CARD}..." ...)"` — got an EMPTY head from the grep, and `gh pr
list --head ""` returns the repo's most recent open PR. The step commented
"PR opened: .../pull/1366" (another card's PR) and advanced the card to
In QA, masking a dead run that the dead-run requeue path would otherwise
have caught immediately.

The original fix lived entirely in shell: resolve BRANCH first, only query
gh when BRANCH is non-empty. DRE-2316 moved the lookup itself into the
shared predicate `scripts/card_pr.py` (because `gh pr list` defaults to
--state open, the shell could not see a PR that had just MERGED and
declared a dead run over shipped work). The attribution guarantee moved
with it and got STRONGER: instead of "don't ask when the branch is empty",
the predicate confirms every candidate PR's head ref against a \\b-anchored
match on this card's identifier — so even a lookup with no branch at all
cannot return a stranger's PR. These tests pin both halves: the workflow
asks the predicate, and the predicate refuses to mis-attribute.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import card_pr  # noqa: E402

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)


def report_step() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"- name: Report result to Linear(.*)\Z", src, re.S)
    if not m:
        raise AssertionError("report step not found in agent-task.yml")
    return m.group(1)


def report_code() -> str:
    """The report step with `#` comment lines stripped: the comments quote the
    old, broken command deliberately — that history is why the fix is there."""
    return "\n".join(
        line for line in report_step().splitlines() if not line.lstrip().startswith("#")
    )


class ReportPrAttributionTest(unittest.TestCase):
    def test_branch_resolved_separately(self):
        step = report_step()
        self.assertIn("BRANCH=$(", step)

    def test_no_inline_command_substitution_in_the_lookup(self):
        # The original bug: --head "$(git branch -r | grep ...)" — an empty
        # substitution silently matches every PR. The lookup must use the
        # pre-resolved $BRANCH variable, never an inline substitution.
        step = report_step()
        self.assertNotIn('--head "$(', step)
        self.assertNotIn('--branch "$(', step)
        self.assertIn('--branch "$BRANCH"', step)

    def test_pr_claim_goes_through_the_shared_predicate(self):
        # No raw `gh pr list` may survive in the step: it is the call that
        # both mis-attributed (DRE-1343) and went blind to merged PRs
        # (DRE-2316).
        step = report_step()
        self.assertNotIn("gh pr list", report_code())
        self.assertIn("card_pr.py", step)
        # And the claim itself must be made from what the predicate returned.
        claim = step.find("PR opened: $PR_URL")
        lookup = step.find("card_pr.py find")
        self.assertGreater(claim, lookup, "the PR claim must follow the lookup")


class PredicateRefusesMisattributionTest(unittest.TestCase):
    """The DRE-1343 guarantee, now enforced where the lookup lives."""

    STRANGER = {
        "number": 1366,
        "url": "https://github.com/o/r/pull/1366",
        "headRefName": "agent/DRE-1366-someone-elses-card",
        "state": "OPEN",
    }

    def _run(self, prs):
        def gh(_args):
            import json

            return json.dumps(prs)

        return gh

    def test_empty_branch_cannot_produce_another_cards_pr(self):
        found = card_pr.find(
            "DRE-1343", branch="", repo="o/r", run=self._run([self.STRANGER])
        )
        self.assertIsNone(found)

    def test_even_a_head_query_result_must_carry_this_card(self):
        # Defence in depth: if gh ever answered a --head query with an
        # unrelated PR, the anchored confirm still drops it.
        found = card_pr.find(
            "DRE-1343",
            branch="agent/DRE-1343-real",
            repo="o/r",
            run=self._run([self.STRANGER]),
        )
        self.assertIsNone(found)

    def test_the_cards_own_pr_is_still_found(self):
        mine = dict(self.STRANGER, number=9, headRefName="agent/DRE-1343-real")
        found = card_pr.find(
            "DRE-1343", branch="agent/DRE-1343-real", repo="o/r", run=self._run([mine])
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["number"], 9)


if __name__ == "__main__":
    unittest.main()
