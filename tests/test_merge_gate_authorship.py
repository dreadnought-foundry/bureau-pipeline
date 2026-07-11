"""Merge-gate verdict AUTHORSHIP tests (DRE-1987, migrated by DRE-1992).

Origin (2026-07-09): merge-gate.yml merged on any comment containing
"QA Critic" + "VERDICT: APPROVE" regardless of author, so the engineer bot
(or anyone able to comment) could forge approval on its own PR. The fix
(#57) filters both verdict reads — the QA Critic verdict and the QA
Verifier verdict — by comment author: only comments whose .user.login
equals the qa-bot App's login (derived in the workflow from the minted
token's app-slug + "[bot]", exported as QA_LOGIN) count at all.

MIGRATION (DRE-1992): these cases originally executed the exact jq filter
expressions extracted from merge-gate.yml at test time. The decision moved
into scripts/merge_gate.py, so the same numbered cases (the critic's
enumeration on PR #57) now run against merge_gate.latest_verdict_comment —
the function the gate's decide() uses for both verdict reads. What kept the
old extraction honest (a diff weakening the live filter turns tests red)
still holds: these tests exercise the LIVE selection function, and
tests/test_merge_gate_decision_table.py proves decide() end-to-end against
the frozen pre-extraction shell, forged-author rows included, while
tests/test_merge_gate_wiring.py pins the workflow to pass the app-slug-
derived login into the script.

Selection is now also ANCHORED (DRE-1992 scope note 2026-07-09): a comment
counts only if its first line OPENS with the marker — merely quoting or
mentioning "QA Critic" in prose is invisible. The delta rows in the
decision table document the exact behavior change.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

# The login the workflow derives at runtime: steps.qa.outputs.app-slug +
# "[bot]". Empirically agent-bureau-qa-bot[bot] since bureau-pipeline #51.
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"  # authors PRs — must never count

QA_APPROVE = "🔎 QA Critic — VERDICT: APPROVE"
QA_REJECT = "🔎 QA Critic — VERDICT: REQUEST_CHANGES"
V_PASS = "🧪 QA Verifier — VERDICT: PASS"
V_FAIL = "🧪 QA Verifier — VERDICT: FAIL"
V_NEUTRAL = (
    "🧪 QA Verifier could not run (infra error) — re-verify needed, "
    "this is NOT a feature rejection."
)


def comment(login, body):
    """A GitHub issue-comment shaped like the REST API returns it.
    login=None models a deleted account (null user). App identities carry
    the reserved "[bot]" suffix and type Bot; anything else is a User."""
    if login is None:
        user = None
    else:
        kind = "Bot" if login.endswith("[bot]") else "User"
        user = {"login": login, "type": kind}
    return {"user": user, "body": body}


class AuthorshipFilterTest(unittest.TestCase):
    """Base: run one verdict read exactly as decide() does — the live
    selection function, the workflow-derived QA_LOGIN."""

    marker = None  # set by subclasses

    def latest_verdict(self, comments):
        """Returns the body of the latest COUNTED verdict, or None —
        mirroring the gate's no-verdict fall-through."""
        return merge_gate.latest_verdict_comment(comments, QA_LOGIN, self.marker)


class CriticVerdictAuthorshipTest(AuthorshipFilterTest):
    """Condition 2: the QA Critic verdict read."""

    marker = merge_gate.CRITIC_MARKER

    def test_01_qa_bot_approve_counts(self):
        got = self.latest_verdict([comment(QA_LOGIN, QA_APPROVE)])
        self.assertEqual(got, QA_APPROVE)

    def test_02_engineer_bot_forged_approve_is_invisible(self):
        # THE hole DRE-1987 closed: the PR-authoring worker bot posts the
        # magic words on its own PR. Must not count as any verdict at all.
        got = self.latest_verdict([comment(WORKER_LOGIN, QA_APPROVE)])
        self.assertIsNone(got)

    def test_03_human_approve_is_invisible(self):
        got = self.latest_verdict(
            [comment("some-human", "QA Critic — VERDICT: APPROVE — lgtm!")]
        )
        self.assertIsNone(got)

    def test_04_qa_bot_request_changes_counts(self):
        got = self.latest_verdict([comment(QA_LOGIN, QA_REJECT)])
        self.assertEqual(got, QA_REJECT)

    def test_05_forged_approve_cannot_override_real_request_changes(self):
        # Real rejection, then a forged approval (and human noise) after it:
        # the latest COUNTED verdict must still be the real REQUEST_CHANGES.
        got = self.latest_verdict(
            [
                comment(QA_LOGIN, QA_REJECT),
                comment(WORKER_LOGIN, QA_APPROVE),
                comment("some-human", "QA Critic — VERDICT: APPROVE"),
            ]
        )
        self.assertEqual(got, QA_REJECT)

    def test_06_real_reverdict_approve_wins(self):
        got = self.latest_verdict(
            [comment(QA_LOGIN, QA_REJECT), comment(QA_LOGIN, QA_APPROVE)]
        )
        self.assertEqual(got, QA_APPROVE)

    def test_10_deleted_null_author_never_counts(self):
        got = self.latest_verdict([comment(None, QA_APPROVE)])
        self.assertIsNone(got)

    def test_14_quoted_verdict_is_invisible_even_from_qa_bot(self):
        # DRE-1992 scope note: a comment merely QUOTING a verdict must not
        # count — even when the qa-bot itself posts the quote.
        got = self.latest_verdict([comment(QA_LOGIN, f"> {QA_APPROVE}")])
        self.assertIsNone(got)

    def test_15_marker_mention_in_prose_is_invisible(self):
        got = self.latest_verdict(
            [comment(QA_LOGIN, "Waiting on the QA Critic to re-run.")]
        )
        self.assertIsNone(got)


class VerifierVerdictAuthorshipTest(AuthorshipFilterTest):
    """Condition 3: the QA Verifier verdict read. An ABSENT verifier verdict
    is not a gate (scope-gated stage), so a forged verdict being INVISIBLE
    (None) means 'verifier did not run'."""

    marker = merge_gate.VERIFIER_MARKER

    def test_07_engineer_bot_forged_pass_is_invisible(self):
        got = self.latest_verdict([comment(WORKER_LOGIN, V_PASS)])
        self.assertIsNone(got)

    def test_08_forged_pass_cannot_mask_real_fail(self):
        got = self.latest_verdict(
            [comment(QA_LOGIN, V_FAIL), comment(WORKER_LOGIN, V_PASS)]
        )
        self.assertEqual(got, V_FAIL)

    def test_09_qa_bot_pass_counts(self):
        got = self.latest_verdict([comment(QA_LOGIN, V_PASS)])
        self.assertEqual(got, V_PASS)

    def test_11_qa_bot_fail_counts_and_holds(self):
        # NOT fail-open: a real FAIL must be visible so the gate's
        # "not PASS — holding" branch fires. (This is why verify.yml posts
        # its verdict with the qa-bot token, atomically in DRE-1987 — a
        # worker-bot-authored FAIL would be invisible, see case 12.)
        got = self.latest_verdict([comment(QA_LOGIN, V_FAIL)])
        self.assertEqual(got, V_FAIL)

    def test_12_worker_bot_fail_is_invisible(self):
        # The OLD verify.yml posted with the worker App token; under the
        # authorship filter that author is ignored — hence the token switch.
        got = self.latest_verdict([comment(WORKER_LOGIN, V_FAIL)])
        self.assertIsNone(got)

    def test_13_qa_bot_neutral_could_not_run_counts_and_holds(self):
        # Opens with "🧪 QA Verifier" but carries no structured verdict:
        # visible, latest, supersedes any stale PASS, and holds the merge.
        got = self.latest_verdict(
            [comment(QA_LOGIN, V_PASS), comment(QA_LOGIN, V_NEUTRAL)]
        )
        self.assertEqual(got, V_NEUTRAL)


class FilterShapeTest(unittest.TestCase):
    """Both verdict reads must stay author-guarded inside decide() itself —
    catches a refactor that drops the login filter from one read (the case
    tests above hit the shared function; this exercises the composition).
    The workflow side (QA_LOGIN derived from the minted token's app-slug)
    is pinned by tests/test_merge_gate_wiring.py."""

    HEAD = "aa11" * 10
    GREEN = [{"name": "unit", "status": "completed", "conclusion": "success"}]

    def test_decide_ignores_forged_critic_verdict(self):
        decision = merge_gate.decide(
            self.HEAD, QA_LOGIN, self.GREEN,
            [comment(WORKER_LOGIN, f"{QA_APPROVE} @{self.HEAD}")],
            compare_status="ahead",
        )
        self.assertEqual(decision.action, "wait")
        self.assertIn("no critic verdict yet", decision.reason)

    def test_decide_ignores_forged_verifier_fail(self):
        decision = merge_gate.decide(
            self.HEAD, QA_LOGIN, self.GREEN,
            [
                comment(QA_LOGIN, f"{QA_APPROVE} @{self.HEAD}"),
                comment(WORKER_LOGIN, f"{V_FAIL} @{self.HEAD}"),
            ],
            compare_status="ahead",
        )
        self.assertEqual(decision.action, "merge")


if __name__ == "__main__":
    unittest.main()
