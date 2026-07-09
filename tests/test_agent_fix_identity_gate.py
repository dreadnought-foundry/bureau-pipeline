"""Regression pin: DRE-1988 — agent-fix must only respond to qa-bot identity.

Before this fix anyone who could comment on a PR could trigger the fix agent
by including "VERDICT: REQUEST_CHANGES" in their comment body. Three locks
now gate on identity; this suite pins each one so a revert turns CI red.
"""
import os
import re
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)


def workflow_src() -> str:
    return open(WORKFLOW).read()


class TriggerGateTest(unittest.TestCase):
    def test_job_if_requires_qa_bot_authorship(self):
        # The job-level `if` must check comment author before the body phrase.
        # Without this check any commenter can wake the fix agent.
        src = workflow_src()
        self.assertIn(
            "github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'",
            src,
        )

    def test_job_if_author_check_precedes_body_check(self):
        # Author check must appear in the same `if` block as VERDICT phrase,
        # and before it, so the `&&` short-circuits correctly.
        src = workflow_src()
        author_pos = src.find("github.event.comment.user.login == 'agent-bureau-qa-bot[bot]'")
        body_pos = src.find("contains(github.event.comment.body, 'VERDICT: REQUEST_CHANGES')")
        self.assertGreater(author_pos, 0, "author check not found in job if")
        self.assertGreater(body_pos, 0, "body check not found in job if")
        self.assertLess(author_pos, body_pos, "author check must precede body check")


class VerdictFilterTest(unittest.TestCase):
    """The Resolve step must filter jq results to qa-bot-authored comments only."""

    def _resolve_step(self) -> str:
        src = workflow_src()
        m = re.search(
            r"name:\s*Resolve PR.*?((?:\n      - name:|\Z))",
            src, re.S
        )
        if not m:
            raise AssertionError("Resolve step not found in agent-fix.yml")
        return m.group(0)

    def test_verdict_jq_filters_by_qa_bot_author(self):
        step = self._resolve_step()
        self.assertIn('.user.login == "agent-bureau-qa-bot[bot]"', step)

    def test_verdict_idx_jq_filters_by_qa_bot_author(self):
        # The LAST_VERDICT_IDX lookup must also be author-filtered so the
        # outstanding-vs-pushed comparison can't be fooled by a planted comment.
        step = self._resolve_step()
        # Both the VERDICT and LAST_VERDICT_IDX jq calls must include the filter.
        occurrences = step.count('.user.login == "agent-bureau-qa-bot[bot]"')
        self.assertGreaterEqual(occurrences, 2, "expected at least 2 author-filtered jq calls in Resolve step")


class SpecFetchTest(unittest.TestCase):
    """The Fetch Critic Verdict step must exist and filter by qa-bot author."""

    def _fetch_step(self) -> str:
        src = workflow_src()
        m = re.search(
            r"name:\s*Fetch critic verdict.*?((?:\n      - name:|\Z))",
            src, re.S
        )
        if not m:
            raise AssertionError(
                "'Fetch critic verdict' step not found in agent-fix.yml — "
                "DRE-1988 spec pre-fetch step was removed"
            )
        return m.group(0)

    def test_fetch_step_exists(self):
        # The step must exist; its absence means the agent reads the raw
        # PR comment thread where planted specs could be injected.
        self._fetch_step()  # raises if missing

    def test_fetch_step_filters_by_qa_bot_author(self):
        step = self._fetch_step()
        self.assertIn('agent-bureau-qa-bot[bot]', step)

    def test_agent_prompt_reads_prefetched_file_not_raw_comments(self):
        src = workflow_src()
        # The Fix step's prompt must reference the pre-fetched file, not
        # `gh pr view ... --comments` which would expose the raw thread.
        self.assertIn("critic-verdict.md", src)
        # And it must NOT instruct the agent to read comments directly.
        # The old instruction was "Read the latest QA Critic comment on the PR".
        self.assertNotIn(
            "Read the latest QA Critic comment on the PR",
            src,
        )


class AllowedBotsTest(unittest.TestCase):
    def test_allowed_bots_is_not_wildcard(self):
        src = workflow_src()
        # The wildcard that let any bot actor through must be gone.
        self.assertNotIn('allowed_bots: "*"', src)

    def test_allowed_bots_names_qa_bot(self):
        src = workflow_src()
        self.assertIn("agent-bureau-qa-bot", src)

    def test_allowed_bots_names_github_actions(self):
        # github-actions is the actor of machine workflow_dispatch legs
        # (merge-gate conflict repair, reconciler stuck-PR retry).
        # Omitting it would kill every conflict-repair run fleet-wide.
        src = workflow_src()
        self.assertIn("github-actions", src)


if __name__ == "__main__":
    unittest.main()
