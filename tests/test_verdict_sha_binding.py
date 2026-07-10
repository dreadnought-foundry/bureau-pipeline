"""Verdict↔commit binding tests (DRE-1990, migrated by DRE-1992).

Origin (2026-07-09): a QA verdict was a free-floating string on the PR
conversation — it named no commit. Code pushed AFTER a genuine APPROVE
merged without re-review because the gate had nothing to compare (this bit
PRs #13 and #25 historically). The fix binds every verdict to the exact
commit it reviewed:

  • qa-review.yml / verify.yml embed the reviewed head SHA on the verdict
    line — `VERDICT: <X> @<full-40-hex-sha>`;
  • the merge gate honors a verdict only while the PR's CURRENT headRefOid
    equals that embedded SHA. Missing SHA (pre-DRE-1990 legacy verdicts,
    or the neutral could-not-run status) or a stale SHA is treated as NO
    verdict — fail-closed, the gate waits for a fresh review;
  • reconcile.has_verdict() applies the same binding, so the In QA
    re-nudge re-triggers qa-review (produces a fresh, bound verdict)
    instead of merge-gate (which would ignore the stale verdict forever).

MIGRATION (DRE-1992): the gate-side cases originally executed the DRE-1990
shell blocks extracted verbatim from merge-gate.yml. That decision now
lives in scripts/merge_gate.py, so the same numbered cases run against its
LIVE functions (evaluate_critic / evaluate_verifier — `None` is the old
GATE_FALLTHROUGH). End-to-end parity with the pre-extraction shell —
including these very cases — is proved by
tests/test_merge_gate_decision_table.py against the frozen ba4305d fixture.

STILL LIVE-EXTRACTED HERE: the producer composition lines from
qa-review.yml / verify.yml (unchanged by the extraction) are pulled out of
those workflows at test time and executed verbatim, so producer format
stays proved against consumer expectation — a copied string would keep
passing after a drift; the extracted line cannot.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
VERIFY = ROOT / ".github" / "workflows" / "verify.yml"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "test/test")
os.environ.setdefault("REPO_SLUG", "test")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402

# Full 40-hex SHAs, as GitHub's headRefOid returns them.
SHA_REVIEWED = "aa11" * 10  # the commit the critic actually reviewed
SHA_NEWER = "bb22" * 10  # a commit pushed AFTER the verdict landed

QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"  # authors PRs — must never count

CRITIC_NEUTRAL = (
    "🔎 QA Critic could not run (infra error) — re-review needed, "
    "this is NOT a code rejection."
)
VERIFIER_NEUTRAL = (
    "🧪 QA Verifier could not run (infra error) — re-verify needed, "
    "this is NOT a feature rejection."
)


def critic_line(verdict, sha=None):
    """A QA Critic verdict comment's FIRST line (what the gate reads)."""
    base = f"🔎 QA Critic — VERDICT: {verdict}"
    return f"{base} @{sha}" if sha else base


def verifier_line(verdict, sha=None):
    base = f"🧪 QA Verifier — VERDICT: {verdict}"
    return f"{base} @{sha}" if sha else base


def run_shell(script):
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            f"extracted producer logic errored (rc={proc.returncode}): {proc.stderr}"
        )
    return proc.stdout


class CriticShaBindingTest(unittest.TestCase):
    """Condition 2: the QA Critic verdict must be bound to the current head.
    `None` from evaluate_critic = the old block's GATE_FALLTHROUGH (this
    verdict counts as APPROVE-for-this-head)."""

    def gate(self, verdict_first_line, head):
        return merge_gate.evaluate_critic(verdict_first_line, head)

    def test_01_exact_sha_approve_passes(self):
        self.assertIsNone(self.gate(critic_line("APPROVE", SHA_REVIEWED), SHA_REVIEWED))

    def test_02_stale_sha_approve_is_no_verdict(self):
        # THE hole DRE-1990 closed: code pushed after a genuine APPROVE.
        # The verdict names the old commit — it must count as NO verdict.
        got = self.gate(critic_line("APPROVE", SHA_REVIEWED), SHA_NEWER)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "wait")
        self.assertIn("NO verdict", got.reason)

    def test_03_missing_sha_legacy_approve_is_no_verdict(self):
        # Cutover: every verdict posted before DRE-1990 has no @sha and is
        # treated as stale — one re-review nudge produces a bound verdict.
        got = self.gate(critic_line("APPROVE"), SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertIn("NO verdict", got.reason)

    def test_04_request_changes_with_matching_sha_still_holds(self):
        got = self.gate(critic_line("REQUEST_CHANGES", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "hold")
        self.assertIn("not APPROVE", got.reason)

    def test_05_request_changes_with_stale_sha_still_no_merge(self):
        got = self.gate(critic_line("REQUEST_CHANGES", SHA_REVIEWED), SHA_NEWER)
        self.assertIsNotNone(got)

    def test_06_neutral_could_not_run_is_no_verdict(self):
        # The crash-path comment has no VERDICT line and no @sha: it must
        # keep superseding a stale APPROVE and hold the merge.
        self.assertIsNotNone(self.gate(CRITIC_NEUTRAL, SHA_REVIEWED))

    def test_07_empty_verdict_waits(self):
        got = self.gate("", SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertIn("no critic verdict yet", got.reason)

    def test_08_short_sha_does_not_bind(self):
        # Only a full 40-hex SHA binds; an abbreviated one is not a match
        # for the current head and must not slip through as one.
        self.assertIsNotNone(
            self.gate(critic_line("APPROVE", SHA_REVIEWED[:7]), SHA_REVIEWED)
        )


class CriticAuthorshipCompositionTest(unittest.TestCase):
    """DRE-1990 composed with #57: the authorship selection runs FIRST, so a
    forged verdict never reaches the SHA check at all — even with the
    correct SHA embedded."""

    def latest_counted(self, comments):
        body = merge_gate.latest_verdict_comment(
            comments, QA_LOGIN, merge_gate.CRITIC_MARKER
        )
        # decide() evaluates the FIRST LINE (the old `head -1`); "" models
        # the no-counted-verdict fall-through exactly as the gate sees it.
        return merge_gate.first_line(body)

    def comment(self, login, body):
        kind = "Bot" if login.endswith("[bot]") else "User"
        return {"user": {"login": login, "type": kind}, "body": body}

    def test_09_forged_author_with_correct_sha_is_invisible(self):
        verdict = self.latest_counted(
            [self.comment(WORKER_LOGIN, critic_line("APPROVE", SHA_REVIEWED))]
        )
        got = merge_gate.evaluate_critic(verdict, SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertIn("no critic verdict yet", got.reason)

    def test_10_forged_fresh_approve_cannot_refresh_real_stale_one(self):
        # Real APPROVE for the old commit, then the worker bot forges an
        # APPROVE naming the NEW head: the counted verdict is still the
        # real (stale) one, and the SHA check demotes it to no-verdict.
        verdict = self.latest_counted(
            [
                self.comment(QA_LOGIN, critic_line("APPROVE", SHA_REVIEWED)),
                self.comment(WORKER_LOGIN, critic_line("APPROVE", SHA_NEWER)),
            ]
        )
        got = merge_gate.evaluate_critic(verdict, SHA_NEWER)
        self.assertIsNotNone(got)
        self.assertIn("NO verdict", got.reason)


class VerifierShaBindingTest(unittest.TestCase):
    """Condition 3: the QA Verifier read. Deliberate asymmetry — an ABSENT
    verdict falls through (scope-gated stage, may simply not have run), but
    a PRESENT verdict proves the PR is in Verifier scope, so a missing or
    stale SHA must HOLD (fail-closed), never fall through as 'absent'."""

    def gate(self, vverdict_first_line, head):
        """Returns (decision-or-None, note) — None = fall through."""
        return merge_gate.evaluate_verifier(vverdict_first_line, head)

    def test_11_absent_verifier_verdict_is_not_a_gate(self):
        got, note = self.gate("", SHA_REVIEWED)
        self.assertIsNone(got)
        self.assertIn("not a gate", note)

    def test_12_pass_with_exact_sha_passes(self):
        got, _ = self.gate(verifier_line("PASS", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNone(got)

    def test_13_pass_with_stale_sha_holds(self):
        # NOT treated as absent — that would fail OPEN and merge code the
        # verifier never ran.
        got, _ = self.gate(verifier_line("PASS", SHA_REVIEWED), SHA_NEWER)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "hold")

    def test_14_pass_with_missing_sha_holds(self):
        got, _ = self.gate(verifier_line("PASS"), SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "hold")

    def test_15_fail_with_exact_sha_holds(self):
        got, _ = self.gate(verifier_line("FAIL", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertIn("not PASS", got.reason)

    def test_16_neutral_could_not_run_holds(self):
        got, _ = self.gate(VERIFIER_NEUTRAL, SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "hold")


class ProducerFormatTest(unittest.TestCase):
    """Producer↔consumer contract: run the LIVE composition lines from
    qa-review.yml / verify.yml (extracted verbatim, hardcoded /tmp paths and
    all) and prove the comment they produce is exactly what the gate accepts
    for the same head and rejects for a different one."""

    QA_FILES = ("/tmp/qa-verdict.md", "/tmp/qa-comment.md")
    VERIFY_FILES = ("/tmp/verify-verdict.md", "/tmp/verify-comment.md")

    @classmethod
    def setUpClass(cls):
        if shutil.which("bash") is None:
            raise AssertionError("bash is required to run these tests")

    def tearDown(self):
        for path in self.QA_FILES + self.VERIFY_FILES:
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def extract_compose_line(path, prefix):
        text = path.read_text()
        lines = [
            ln.strip() for ln in text.splitlines() if ln.strip().startswith(prefix)
        ]
        if len(lines) != 1:
            raise AssertionError(
                f"expected exactly one composition line starting {prefix!r} "
                f"in {path.name}, found {len(lines)}"
            )
        return lines[0]

    def compose(self, workflow, prefix, verdict_file, comment_file, first_line):
        line = self.extract_compose_line(workflow, prefix)
        Path(verdict_file).write_text(f"{first_line}\n\n## Summary\nEvidence.\n")
        run_shell(
            "set -euo pipefail\n"
            f"REVIEWED_SHA={shlex.quote(SHA_REVIEWED)}\n" + line + "\n"
        )
        return Path(comment_file).read_text().splitlines()[0]

    def test_17_qa_review_compose_feeds_gate_exact_sha(self):
        posted = self.compose(
            QA_REVIEW, '{ echo "🔎 QA Critic — ', *self.QA_FILES,
            first_line="VERDICT: APPROVE",
        )
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        self.assertIsNone(merge_gate.evaluate_critic(posted, SHA_REVIEWED))

    def test_18_qa_review_compose_is_stale_for_a_newer_head(self):
        posted = self.compose(
            QA_REVIEW, '{ echo "🔎 QA Critic — ', *self.QA_FILES,
            first_line="VERDICT: APPROVE",
        )
        got = merge_gate.evaluate_critic(posted, SHA_NEWER)
        self.assertIsNotNone(got)
        self.assertIn("NO verdict", got.reason)

    def test_19_qa_review_request_changes_binds_sha_too(self):
        posted = self.compose(
            QA_REVIEW, '{ echo "🔎 QA Critic — ', *self.QA_FILES,
            first_line="VERDICT: REQUEST_CHANGES",
        )
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        got = merge_gate.evaluate_critic(posted, SHA_REVIEWED)
        self.assertIsNotNone(got)
        self.assertIn("not APPROVE", got.reason)

    def test_20_verify_compose_feeds_gate_exact_sha(self):
        posted = self.compose(
            VERIFY, '{ echo "🧪 QA Verifier — ', *self.VERIFY_FILES,
            first_line="VERDICT: PASS",
        )
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        got, _ = merge_gate.evaluate_verifier(posted, SHA_REVIEWED)
        self.assertIsNone(got)

    def test_21_verify_compose_is_stale_for_a_newer_head(self):
        posted = self.compose(
            VERIFY, '{ echo "🧪 QA Verifier — ', *self.VERIFY_FILES,
            first_line="VERDICT: PASS",
        )
        got, _ = merge_gate.evaluate_verifier(posted, SHA_NEWER)
        self.assertIsNotNone(got)


class VerifierSkipNonblockingTest(unittest.TestCase):
    """DRE-1991: briefs/verifier.md promises a `VERDICT: SKIP` "never blocks
    the merge" (the card was out of Verifier scope on inspection, or a pure
    environment fault unrelated to the diff blocked the run). The gate must
    honor that: a qa-bot-authored SKIP bound to the CURRENT head falls
    through exactly like an ABSENT verifier verdict — critic APPROVE + green
    CI still decide. The DRE-1990 fail-closed asymmetry is unchanged: a SKIP
    whose SHA is missing or stale proves the PR is in Verifier scope but the
    status is not about this commit, so it HOLDS, same as PASS/FAIL.

    Full decision table (one test per row):

      verdict line          | head       | expected
      ----------------------+------------+---------------
      PASS  @head           | head       | merge-eligible
      FAIL  @head           | head       | hold
      SKIP  @head           | head       | merge-eligible   ← THE DRE-1991 row
      SKIP  @stale          | newer head | hold
      SKIP  (no sha)        | head       | hold
      (absent)              | head       | merge-eligible
    """

    def gate(self, vverdict_first_line, head):
        return merge_gate.evaluate_verifier(vverdict_first_line, head)

    def test_31_table_pass_at_head_is_merge_eligible(self):
        got, _ = self.gate(verifier_line("PASS", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNone(got)

    def test_32_table_fail_at_head_holds(self):
        got, _ = self.gate(verifier_line("FAIL", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNotNone(got)

    def test_33_table_skip_at_head_is_merge_eligible(self):
        # THE row DRE-1991 fixed: the brief promises SKIP never blocks, but
        # the gate treated everything non-PASS as a hold — a SKIP'd PR waited
        # forever for a verifier PASS that would never come.
        got, note = self.gate(verifier_line("SKIP", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIsNone(got)
        self.assertIn("SKIP", note)

    def test_34_table_skip_stale_sha_holds(self):
        # A present verdict proves Verifier scope; a stale one says nothing
        # about the CURRENT head. Treating it as "skip → merge" would fail
        # OPEN for code pushed after the skip — hold for a fresh verify.
        got, _ = self.gate(verifier_line("SKIP", SHA_REVIEWED), SHA_NEWER)
        self.assertIsNotNone(got)

    def test_35_table_skip_missing_sha_holds(self):
        # Pre-DRE-1990 format / unbound status: same fail-closed rule as an
        # unbound PASS — hold until a fresh, bound status lands.
        got, _ = self.gate(verifier_line("SKIP"), SHA_REVIEWED)
        self.assertIsNotNone(got)

    def test_36_table_absent_verdict_is_merge_eligible(self):
        got, _ = self.gate("", SHA_REVIEWED)
        self.assertIsNone(got)

    def test_37_skip_with_reason_text_on_the_line_still_falls_through(self):
        # The brief asks for "SKIP + one reason line"; agents sometimes put
        # the reason on the verdict line itself. The compose step appends
        # @sha to whatever head -1 yields, so the gate must match SKIP with
        # trailing prose before the @sha.
        line = (
            "🧪 QA Verifier — VERDICT: SKIP — single-system doc change, "
            f"nothing to run @{SHA_REVIEWED}"
        )
        got, _ = self.gate(line, SHA_REVIEWED)
        self.assertIsNone(got)


class VerifierSkipProducerTest(unittest.TestCase):
    """DRE-1991 producer↔consumer: a SKIP written to /tmp/verify-verdict.md
    is a REAL verdict (check_critic_result.py accepts any VERDICT: line), so
    verify.yml composes it through the SAME @sha-stamped line as PASS/FAIL.
    Prove the live composition output falls through the gate for the head it
    ran on and holds for a newer head. Uses ProducerFormatTest's live
    extraction helpers (not a subclass — that would re-run its tests here)."""

    VERIFY_FILES = ProducerFormatTest.VERIFY_FILES

    def tearDown(self):
        for path in self.VERIFY_FILES:
            Path(path).unlink(missing_ok=True)

    def compose_skip(self):
        line = ProducerFormatTest.extract_compose_line(
            VERIFY, '{ echo "🧪 QA Verifier — '
        )
        verdict_file, comment_file = self.VERIFY_FILES
        Path(verdict_file).write_text(
            "VERDICT: SKIP\n\n## Summary\nOut of scope on inspection — "
            "single-system change, nothing to run.\n"
        )
        run_shell(
            "set -euo pipefail\n"
            f"REVIEWED_SHA={shlex.quote(SHA_REVIEWED)}\n" + line + "\n"
        )
        return Path(comment_file).read_text().splitlines()[0]

    def test_38_verify_compose_skip_feeds_gate_same_head(self):
        posted = self.compose_skip()
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        got, note = merge_gate.evaluate_verifier(posted, SHA_REVIEWED)
        self.assertIsNone(got)
        self.assertIn("SKIP", note)

    def test_39_verify_compose_skip_is_stale_for_a_newer_head(self):
        posted = self.compose_skip()
        got, _ = merge_gate.evaluate_verifier(posted, SHA_NEWER)
        self.assertIsNotNone(got)


class ReconcileHasVerdictTest(unittest.TestCase):
    """reconcile.has_verdict() must apply the same binding: only a verdict
    bound to the PR's CURRENT head counts. Otherwise the In QA re-nudge
    keeps kicking merge-gate (which ignores the stale verdict, fail-closed)
    and a fresh review is never requested — a wedged card."""

    def pr(self, head, bodies):
        return {
            "headRefOid": head,
            "comments": [{"body": b} for b in bodies],
        }

    def test_22_bound_fresh_verdict_counts(self):
        pr = self.pr(SHA_REVIEWED, [critic_line("APPROVE", SHA_REVIEWED)])
        self.assertTrue(reconcile.has_verdict(pr))

    def test_23_stale_bound_verdict_does_not_count(self):
        # → reconcile re-triggers qa-review, not merge-gate.
        pr = self.pr(SHA_NEWER, [critic_line("APPROVE", SHA_REVIEWED)])
        self.assertFalse(reconcile.has_verdict(pr))

    def test_24_legacy_unbound_verdict_does_not_count(self):
        # Pre-DRE-1990 verdicts: reconcile is the automatic re-review nudge.
        pr = self.pr(SHA_REVIEWED, [critic_line("APPROVE")])
        self.assertFalse(reconcile.has_verdict(pr))

    def test_25_no_comments_is_no_verdict(self):
        self.assertFalse(reconcile.has_verdict(self.pr(SHA_REVIEWED, [])))

    def test_26_latest_critic_comment_governs(self):
        # Fresh APPROVE superseded by a neutral could-not-run status: the
        # latest QA Critic comment carries no binding → no verdict.
        pr = self.pr(
            SHA_REVIEWED,
            [critic_line("APPROVE", SHA_REVIEWED), CRITIC_NEUTRAL],
        )
        self.assertFalse(reconcile.has_verdict(pr))

    def test_27_request_changes_bound_to_head_counts(self):
        # A fresh REQUEST_CHANGES is a real verdict (merge-gate holds on it;
        # agent-fix acts on it) — reconcile must not re-request a review.
        pr = self.pr(SHA_REVIEWED, [critic_line("REQUEST_CHANGES", SHA_REVIEWED)])
        self.assertTrue(reconcile.has_verdict(pr))

    def test_28_pr_for_fetches_head_oid(self):
        # has_verdict compares against headRefOid — pr_for must request it,
        # or every verdict silently reads as stale and reconcile churns
        # qa-review re-runs forever.
        import inspect

        self.assertIn("headRefOid", inspect.getsource(reconcile.pr_for))


class BindingShapeTest(unittest.TestCase):
    """The gate and both producers must keep the binding — catches a
    refactor that deletes a piece outright and names the failure plainly."""

    def test_29_gate_enforces_full_sha_binding_on_both_reads(self):
        # Behavioral shape guard on the LIVE module: a verdict bound to a
        # different commit must never fall through, on either read.
        self.assertIsNotNone(
            merge_gate.evaluate_critic(critic_line("APPROVE", SHA_REVIEWED), SHA_NEWER)
        )
        decision, _ = merge_gate.evaluate_verifier(
            verifier_line("PASS", SHA_REVIEWED), SHA_NEWER
        )
        self.assertIsNotNone(decision)
        # And only a FULL 40-hex SHA binds at all.
        self.assertEqual(
            merge_gate.verdict_sha(critic_line("APPROVE", SHA_REVIEWED[:12])), None
        )
        self.assertEqual(
            merge_gate.verdict_sha(critic_line("APPROVE", SHA_REVIEWED)), SHA_REVIEWED
        )

    def test_30_producers_embed_the_reviewed_sha(self):
        for path, marker in (
            (QA_REVIEW, "🔎 QA Critic — "),
            (VERIFY, "🧪 QA Verifier — "),
        ):
            text = path.read_text()
            self.assertRegex(
                text,
                re.escape(marker) + r"[^\n]*@\$\{REVIEWED_SHA\}",
                f"{path.name} no longer embeds the reviewed SHA in its verdict line",
            )


if __name__ == "__main__":
    unittest.main()
