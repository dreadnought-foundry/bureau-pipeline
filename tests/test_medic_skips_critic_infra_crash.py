"""Regression pin (DRE-1921, the #1 quota-burn fix): the medic must NOT loop on
a QA critic that crashed on INFRASTRUCTURE.

Origin (2026-06-28): when the QA critic *job* crashed on a GitHub rate-limit
(`API rate limit exceeded for installation ID …`), qa-review.yml posted the
neutral "QA Critic could not run (infra error)" comment and FAILED the job
loudly "for medic visibility". The medic then treated that FAILURE as retryable:

  retry → `gh run rerun --failed` → critic hits the SAME rate-limit → crashes
  again (deepening the limit) → fails loudly → diagnose → diagnosis agent burns
  more GitHub quota.

Six PRs looped on this and burned the bot's GitHub quota twice.

The fix has two halves, both pinned here:
  1. A classifier (scripts/medic_classify.py) that distinguishes a critic
     INFRA-CRASH (no verdict — reviewer was DOWN) from a normal failure, off the
     run name + its failed-step logs.
  2. medic.yml gating: on a critic infra-crash the medic neither reruns nor
     diagnoses — it BACKS OFF (posts one plain-English "reviewer down (infra)"
     note) and lets a later natural trigger re-review.

A genuine REQUEST_CHANGES verdict is UNAFFECTED: the critic ran (no crash), the
qa-review job ends green, and the fix routes through agent-fix.yml — never the
medic. These tests assert the crash path pauses/escalates while a real verdict
still flows.
"""

import os
import re
import sys
import unittest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import medic_classify  # noqa: E402

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "medic.yml"
)
QA_REVIEW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "qa-review.yml"
)


# ── Classifier: infra-crash vs real failure ──────────────────────────────────
class ClassifierTest(unittest.TestCase):
    def test_rate_limit_in_qa_review_is_infra_crash(self):
        log = (
            "Run anthropics/claude-code-action@v1\n"
            "Error: API rate limit exceeded for installation ID 12345678.\n"
        )
        self.assertTrue(
            medic_classify.is_critic_infra_crash("QA Review (reusable)", log)
        )

    def test_neutral_marker_is_infra_crash(self):
        # qa-review posts/echoes this exact phrase when the critic crashed.
        log = "🔎 QA Critic could not run (infra error) — re-review needed"
        self.assertTrue(
            medic_classify.is_critic_infra_crash("QA Review (reusable)", log)
        )

    def test_secondary_rate_limit_is_infra_crash(self):
        log = "You have exceeded a secondary rate limit. Please wait a few minutes"
        self.assertTrue(
            medic_classify.is_critic_infra_crash("QA Review (reusable)", log)
        )

    def test_genuine_test_failure_is_not_infra_crash(self):
        # A normal red CI/critic run (real findings, assertion failures) must
        # still flow to the medic's retry/diagnose — do NOT regress that.
        log = (
            "FAILED tests/test_foo.py::test_bar - AssertionError: 3 != 4\n"
            "1 failed, 12 passed\n"
        )
        self.assertFalse(
            medic_classify.is_critic_infra_crash("QA Review (reusable)", log)
        )

    def test_request_changes_verdict_is_not_infra_crash(self):
        # A real REQUEST_CHANGES verdict is the critic WORKING, not a crash. It
        # never even reaches the medic, but the classifier must not mistake it.
        log = (
            "🔎 QA Critic — VERDICT: REQUEST_CHANGES\n"
            "## For the fixing agent\nsrc/x.py:10 missing null check\n"
        )
        self.assertFalse(
            medic_classify.is_critic_infra_crash("QA Review (reusable)", log)
        )

    def test_rate_limit_outside_qa_review_is_not_critic_crash(self):
        # A rate-limit in some OTHER workflow (e.g. agent-task, verify) is a
        # real failure the medic should still retry/diagnose once — only the
        # *critic* infra-crash gets the back-off treatment.
        log = "Error: API rate limit exceeded for installation ID 12345678."
        self.assertFalse(
            medic_classify.is_critic_infra_crash("Agent Task (reusable)", log)
        )

    def test_cli_prints_true_on_crash(self):
        import io
        import contextlib

        buf = io.StringIO()
        path = os.path.join(os.path.dirname(__file__), "_medic_log.tmp")
        with open(path, "w") as f:
            f.write("API rate limit exceeded for installation ID 1")
        try:
            with contextlib.redirect_stdout(buf):
                rc = medic_classify.main(["QA Review (reusable)", path])
        finally:
            os.remove(path)
        self.assertEqual(rc, 0)
        self.assertIn("infra_crash=true", buf.getvalue())


# ── medic.yml wiring: back off, do not rerun/diagnose on a critic infra-crash ─
def _medic_src() -> str:
    return open(WORKFLOW).read()


class MedicWiringTest(unittest.TestCase):
    def test_retry_job_skips_on_infra_crash(self):
        # The retry job's `if:` must exclude a classified infra-crash.
        src = _medic_src()
        m = re.search(r"\n  retry:\n(.*?)\n  \w+:", src, re.S)
        self.assertIsNotNone(m, "retry job not found")
        self.assertIn("infra_crash != 'true'", m.group(1))

    def test_diagnose_job_skips_on_infra_crash(self):
        src = _medic_src()
        m = re.search(r"\n  diagnose:\n(.*?)\Z", src, re.S)
        self.assertIsNotNone(m, "diagnose job not found")
        self.assertIn("infra_crash != 'true'", m.group(1))

    def test_backoff_job_runs_only_on_infra_crash(self):
        # A dedicated back-off path fires ONLY on the infra-crash and does NOT
        # rerun (it must not call `gh run rerun`).
        src = _medic_src()
        m = re.search(r"\n  backoff:\n(.*?)\n  \w+:", src, re.S)
        self.assertIsNotNone(m, "backoff job not found")
        body = m.group(1)
        self.assertIn("infra_crash == 'true'", body)
        self.assertNotIn("gh run rerun", body)

    def test_classify_job_exists_and_feeds_the_gates(self):
        # A single classify job must produce the infra_crash output the gates read.
        src = _medic_src()
        self.assertIn("classify:", src)
        self.assertIn("medic_classify.py", src)
        self.assertIn("infra_crash: ${{ steps.c.outputs.infra_crash }}", src)
        # Each acting job must depend on classify so the gate value is available.
        for job in ("retry:", "diagnose:", "backoff:"):
            block = re.search(rf"\n  {job}\n(.*?)\n    runs-on", src, re.S)
            self.assertIsNotNone(block, f"{job} block not found")
            self.assertIn("needs: classify", block.group(1))

    def test_qa_review_still_posts_the_neutral_marker(self):
        # The classifier keys off qa-review's marker phrase — if that string
        # ever drifts, this catches it so the two stay in lockstep.
        self.assertIn(
            medic_classify.CRITIC_NEUTRAL_MARKER, open(QA_REVIEW).read()
        )


if __name__ == "__main__":
    unittest.main()
