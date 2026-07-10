"""RED-first regression tests for verdict↔commit binding (DRE-1990).

Origin (2026-07-09): a QA verdict was a free-floating string on the PR
conversation — it named no commit. Code pushed AFTER a genuine APPROVE
merged without re-review because the gate had nothing to compare (this bit
PRs #13 and #25 historically). The fix binds every verdict to the exact
commit it reviewed:

  • qa-review.yml / verify.yml embed the reviewed head SHA on the verdict
    line — `VERDICT: <X> @<full-40-hex-sha>`;
  • merge-gate.yml honors a verdict only while the PR's CURRENT headRefOid
    equals that embedded SHA. Missing SHA (pre-DRE-1990 legacy verdicts,
    or the neutral could-not-run status) or a stale SHA is treated as NO
    verdict — fail-closed, the gate waits for a fresh review;
  • reconcile.has_verdict() applies the same binding, so the In QA
    re-nudge re-triggers qa-review (produces a fresh, bound verdict)
    instead of merge-gate (which would ignore the stale verdict forever).

Pattern follows tests/test_merge_gate_authorship.py (#57): the tests
EXECUTE THE LIVE LOGIC extracted from the workflow files at test time —
never a copy. The DRE-1990 marker blocks in merge-gate.yml are run
verbatim in a bash harness; the producer composition lines in
qa-review.yml / verify.yml are run verbatim so producer format is proved
against consumer expectation; and the #57 authorship jq filter is composed
in front (a forged author with the CORRECT SHA must still be invisible).
A future diff that weakens or deletes the binding changes the extracted
text and turns these tests red; a copied string would keep passing.

RED-first proof: see the PR body — with the SHA checks inside the
DRE-1990 critic block removed from merge-gate.yml, the stale-SHA and
missing-SHA cases below fall through to merge and fail; restoring the
block turns them green.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MERGE_GATE = ROOT / ".github" / "workflows" / "merge-gate.yml"
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
VERIFY = ROOT / ".github" / "workflows" / "verify.yml"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "test/test")
os.environ.setdefault("REPO_SLUG", "test")

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

# Markers bracketing the two decision blocks in merge-gate.yml. The tests
# execute the text BETWEEN each pair verbatim.
CRITIC_BEGIN = "# ── DRE-1990 critic-verdict SHA binding"
CRITIC_END = "# ── DRE-1990 end critic binding"
VERIFIER_BEGIN = "# ── DRE-1990 verifier-verdict SHA binding"
VERIFIER_END = "# ── DRE-1990 end verifier binding"


def critic_line(verdict, sha=None):
    """A QA Critic verdict comment's FIRST line (what merge-gate reads)."""
    base = f"🔎 QA Critic — VERDICT: {verdict}"
    return f"{base} @{sha}" if sha else base


def verifier_line(verdict, sha=None):
    base = f"🧪 QA Verifier — VERDICT: {verdict}"
    return f"{base} @{sha}" if sha else base


def extract_block(path, begin, end):
    """Pull the shell between two marker comments out of a workflow file,
    stripped of its YAML indent, ready to execute. Fails loudly if the
    markers (and so the binding logic) were removed or renamed."""
    text = path.read_text()
    m = re.search(re.escape(begin) + r"[^\n]*\n(.*?)^\s*" + re.escape(end), text, re.S | re.M)
    if not m:
        raise AssertionError(
            f"marker block {begin!r} not found in {path.name} — "
            "the DRE-1990 verdict-SHA binding was removed or renamed"
        )
    lines = m.group(1).splitlines()
    pad = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    block = "\n".join(ln[pad:] for ln in lines)
    if "${{" in block:
        raise AssertionError(
            "the DRE-1990 block must stay pure shell (no ${{ }} expressions) "
            "so tests can execute it verbatim"
        )
    return block


def extract_jq_filter(marker):
    """The #57 authorship filter for one verdict read — extracted live,
    same approach as tests/test_merge_gate_authorship.py."""
    text = MERGE_GATE.read_text()
    exprs = [e for e in re.findall(r"--jq '([^']*)'", text) if marker in e]
    if len(exprs) != 1:
        raise AssertionError(
            f"expected exactly one --jq expression containing {marker!r} "
            f"in merge-gate.yml, found {len(exprs)}"
        )
    return exprs[0]


def run_shell(script):
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"extracted gate logic errored (rc={proc.returncode}): {proc.stderr}"
        )
    return proc.stdout


class GateBlockTest(unittest.TestCase):
    """Base: execute a DRE-1990 decision block exactly as merge-gate does —
    same shell options, variables preset as the workflow leaves them.
    GATE_FALLTHROUGH in the output means the block let evaluation continue
    (for the critic: this verdict counts as APPROVE-for-this-head)."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("bash") is None or shutil.which("jq") is None:
            raise AssertionError("bash and jq are required to run these tests")

    def run_block(self, block, variables):
        script = "set -euo pipefail\n"
        for name, value in variables.items():
            script += f"{name}={shlex.quote(value)}\n"
        script += block + "\necho GATE_FALLTHROUGH\n"
        return run_shell(script)


class CriticShaBindingTest(GateBlockTest):
    """Condition 2: the QA Critic verdict must be bound to the current head."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.block = extract_block(MERGE_GATE, CRITIC_BEGIN, CRITIC_END)

    def gate(self, verdict_first_line, head):
        return self.run_block(
            self.block, {"VERDICT": verdict_first_line, "SHA": head}
        )

    def test_01_exact_sha_approve_passes(self):
        out = self.gate(critic_line("APPROVE", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIn("GATE_FALLTHROUGH", out)

    def test_02_stale_sha_approve_is_no_verdict(self):
        # THE hole this card closes: code pushed after a genuine APPROVE.
        # The verdict names the old commit — it must count as NO verdict.
        out = self.gate(critic_line("APPROVE", SHA_REVIEWED), SHA_NEWER)
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("NO verdict", out)

    def test_03_missing_sha_legacy_approve_is_no_verdict(self):
        # Cutover: every verdict posted before DRE-1990 has no @sha and is
        # treated as stale — one re-review nudge produces a bound verdict.
        out = self.gate(critic_line("APPROVE"), SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("NO verdict", out)

    def test_04_request_changes_with_matching_sha_still_holds(self):
        out = self.gate(
            critic_line("REQUEST_CHANGES", SHA_REVIEWED), SHA_REVIEWED
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("not APPROVE", out)

    def test_05_request_changes_with_stale_sha_still_no_merge(self):
        out = self.gate(critic_line("REQUEST_CHANGES", SHA_REVIEWED), SHA_NEWER)
        self.assertNotIn("GATE_FALLTHROUGH", out)

    def test_06_neutral_could_not_run_is_no_verdict(self):
        # The crash-path comment has no VERDICT line and no @sha: it must
        # keep superseding a stale APPROVE and hold the merge.
        out = self.gate(CRITIC_NEUTRAL, SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)

    def test_07_empty_verdict_waits(self):
        out = self.gate("", SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("no critic verdict yet", out)

    def test_08_short_sha_does_not_bind(self):
        # Only a full 40-hex SHA binds; an abbreviated one is not a match
        # for the current head and must not slip through as one.
        out = self.gate(
            critic_line("APPROVE", SHA_REVIEWED[:7]), SHA_REVIEWED
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)


class CriticAuthorshipCompositionTest(GateBlockTest):
    """DRE-1990 composed with #57: the authorship jq filter runs FIRST, so a
    forged verdict never reaches the SHA check at all — even with the
    correct SHA embedded."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.block = extract_block(MERGE_GATE, CRITIC_BEGIN, CRITIC_END)
        cls.jq_expr = extract_jq_filter("QA Critic")

    def latest_counted(self, comments):
        proc = subprocess.run(
            ["jq", self.jq_expr],
            input=json.dumps(comments),
            capture_output=True,
            text=True,
            env={**os.environ, "QA_LOGIN": QA_LOGIN},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        # merge-gate pipes the body through `head -1`; "" models jq's
        # null → empty fall-through exactly as the workflow sees it.
        return body.splitlines()[0] if body else ""

    def comment(self, login, body):
        kind = "Bot" if login.endswith("[bot]") else "User"
        return {"user": {"login": login, "type": kind}, "body": body}

    def test_09_forged_author_with_correct_sha_is_invisible(self):
        verdict = self.latest_counted(
            [self.comment(WORKER_LOGIN, critic_line("APPROVE", SHA_REVIEWED))]
        )
        out = self.run_block(
            self.block, {"VERDICT": verdict, "SHA": SHA_REVIEWED}
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("no critic verdict yet", out)

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
        out = self.run_block(self.block, {"VERDICT": verdict, "SHA": SHA_NEWER})
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("NO verdict", out)


class VerifierShaBindingTest(GateBlockTest):
    """Condition 3: the QA Verifier read. Deliberate asymmetry — an ABSENT
    verdict falls through (scope-gated stage, may simply not have run), but
    a PRESENT verdict proves the PR is in Verifier scope, so a missing or
    stale SHA must HOLD (fail-closed), never fall through as 'absent'."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.block = extract_block(MERGE_GATE, VERIFIER_BEGIN, VERIFIER_END)

    def gate(self, vverdict_first_line, head):
        return self.run_block(
            self.block, {"VVERDICT": vverdict_first_line, "SHA": head}
        )

    def test_11_absent_verifier_verdict_is_not_a_gate(self):
        out = self.gate("", SHA_REVIEWED)
        self.assertIn("GATE_FALLTHROUGH", out)
        self.assertIn("not a gate", out)

    def test_12_pass_with_exact_sha_passes(self):
        out = self.gate(verifier_line("PASS", SHA_REVIEWED), SHA_REVIEWED)
        self.assertIn("GATE_FALLTHROUGH", out)

    def test_13_pass_with_stale_sha_holds(self):
        # NOT treated as absent — that would fail OPEN and merge code the
        # verifier never ran.
        out = self.gate(verifier_line("PASS", SHA_REVIEWED), SHA_NEWER)
        self.assertNotIn("GATE_FALLTHROUGH", out)

    def test_14_pass_with_missing_sha_holds(self):
        out = self.gate(verifier_line("PASS"), SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)

    def test_15_fail_with_exact_sha_holds(self):
        out = self.gate(verifier_line("FAIL", SHA_REVIEWED), SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("not PASS", out)

    def test_16_neutral_could_not_run_holds(self):
        out = self.gate(VERIFIER_NEUTRAL, SHA_REVIEWED)
        self.assertNotIn("GATE_FALLTHROUGH", out)


class ProducerFormatTest(GateBlockTest):
    """Producer↔consumer contract: run the LIVE composition lines from
    qa-review.yml / verify.yml (extracted verbatim, hardcoded /tmp paths and
    all) and prove the comment they produce is exactly what the merge-gate
    blocks accept for the same head and reject for a different one."""

    QA_FILES = ("/tmp/qa-verdict.md", "/tmp/qa-comment.md")
    VERIFY_FILES = ("/tmp/verify-verdict.md", "/tmp/verify-comment.md")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.critic_block = extract_block(MERGE_GATE, CRITIC_BEGIN, CRITIC_END)
        cls.verifier_block = extract_block(
            MERGE_GATE, VERIFIER_BEGIN, VERIFIER_END
        )

    def tearDown(self):
        for path in self.QA_FILES + self.VERIFY_FILES:
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def extract_compose_line(path, prefix):
        text = path.read_text()
        lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip().startswith(prefix)
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
        out = self.run_block(
            self.critic_block, {"VERDICT": posted, "SHA": SHA_REVIEWED}
        )
        self.assertIn("GATE_FALLTHROUGH", out)

    def test_18_qa_review_compose_is_stale_for_a_newer_head(self):
        posted = self.compose(
            QA_REVIEW, '{ echo "🔎 QA Critic — ', *self.QA_FILES,
            first_line="VERDICT: APPROVE",
        )
        out = self.run_block(
            self.critic_block, {"VERDICT": posted, "SHA": SHA_NEWER}
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("NO verdict", out)

    def test_19_qa_review_request_changes_binds_sha_too(self):
        posted = self.compose(
            QA_REVIEW, '{ echo "🔎 QA Critic — ', *self.QA_FILES,
            first_line="VERDICT: REQUEST_CHANGES",
        )
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        out = self.run_block(
            self.critic_block, {"VERDICT": posted, "SHA": SHA_REVIEWED}
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)
        self.assertIn("not APPROVE", out)

    def test_20_verify_compose_feeds_gate_exact_sha(self):
        posted = self.compose(
            VERIFY, '{ echo "🧪 QA Verifier — ', *self.VERIFY_FILES,
            first_line="VERDICT: PASS",
        )
        self.assertIn(f"@{SHA_REVIEWED}", posted)
        out = self.run_block(
            self.verifier_block, {"VVERDICT": posted, "SHA": SHA_REVIEWED}
        )
        self.assertIn("GATE_FALLTHROUGH", out)

    def test_21_verify_compose_is_stale_for_a_newer_head(self):
        posted = self.compose(
            VERIFY, '{ echo "🧪 QA Verifier — ', *self.VERIFY_FILES,
            first_line="VERDICT: PASS",
        )
        out = self.run_block(
            self.verifier_block, {"VVERDICT": posted, "SHA": SHA_NEWER}
        )
        self.assertNotIn("GATE_FALLTHROUGH", out)


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
    """Both gate blocks and both producers must keep the binding — catches a
    refactor that deletes a piece outright and names the failure plainly."""

    def test_29_gate_blocks_present_and_compare_shas(self):
        critic = extract_block(MERGE_GATE, CRITIC_BEGIN, CRITIC_END)
        verifier = extract_block(MERGE_GATE, VERIFIER_BEGIN, VERIFIER_END)
        self.assertIn('"$V_SHA" != "$SHA"', critic)
        self.assertIn('"$VV_SHA" != "$SHA"', verifier)

    def test_30_producers_embed_the_reviewed_sha(self):
        for path, marker in ((QA_REVIEW, "🔎 QA Critic — "), (VERIFY, "🧪 QA Verifier — ")):
            text = path.read_text()
            self.assertRegex(
                text,
                re.escape(marker) + r'[^\n]*@\$\{REVIEWED_SHA\}',
                f"{path.name} no longer embeds the reviewed SHA in its verdict line",
            )


if __name__ == "__main__":
    unittest.main()
