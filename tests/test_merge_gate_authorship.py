"""RED-first tests for merge-gate verdict AUTHORSHIP (DRE-1987).

Origin (2026-07-09): merge-gate.yml merged on any comment containing
"QA Critic" + "VERDICT: APPROVE" regardless of author, so the engineer bot
(or anyone able to comment) could forge approval on its own PR. The fix
filters both verdict reads — the QA Critic verdict and the QA Verifier
verdict — by comment author: only comments whose .user.login equals the
qa-bot App's login (derived in the workflow from the minted token's
app-slug + "[bot]", exported as QA_LOGIN) count at all.

These tests run the EXACT jq filter expressions from merge-gate.yml —
extracted from the workflow file at test time, not copied — against mock
GitHub comment JSON, with QA_LOGIN set in the subprocess environment just
as the workflow exports it. Extracting from the live file means a future
diff that weakens either filter (e.g. drops the
`(.user.login == env.QA_LOGIN) and` clause) turns THIS test red; a copied
string would keep passing against the stale expression.

RED-first proof (2026-07-09, run before committing): with
`(.user.login == env.QA_LOGIN) and ` removed from both filters in
merge-gate.yml, 8 of these tests fail — cases 2, 3, 5, 7, 8, 10, 12 (all
the forged/ignored-author cases, a superset of the critic's required
2/3/7/8/12) plus the filter-shape guard; restoring the clause turns all
green. The numbered cases match the critic's enumeration on PR #57.
"""

import json
import os
import re
import shutil
import subprocess
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "merge-gate.yml"
)

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


def extract_filter(marker):
    """Pull the exact --jq expression for one verdict read out of the
    workflow file. Exactly one expression must mention the marker."""
    with open(WORKFLOW) as f:
        text = f.read()
    exprs = [e for e in re.findall(r"--jq '([^']*)'", text) if marker in e]
    if len(exprs) != 1:
        raise AssertionError(
            f"expected exactly one --jq expression containing {marker!r} "
            f"in merge-gate.yml, found {len(exprs)}"
        )
    return exprs[0]


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
    """Base: run a verdict filter exactly as the workflow does — jq
    subprocess, QA_LOGIN in the environment (the workflow exports it)."""

    marker = None  # set by subclasses

    @classmethod
    def setUpClass(cls):
        if shutil.which("jq") is None:
            raise AssertionError("jq is required to run these tests")
        if cls.marker is not None:
            cls.expr = extract_filter(cls.marker)

    def latest_verdict(self, comments):
        """Returns the body of the latest COUNTED verdict, or None —
        mirroring the workflow's `"" == no verdict` fall-through."""
        proc = subprocess.run(
            ["jq", self.expr],
            input=json.dumps(comments),
            capture_output=True,
            text=True,
            env={**os.environ, "QA_LOGIN": QA_LOGIN},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)


class CriticVerdictAuthorshipTest(AuthorshipFilterTest):
    """Condition 2: the QA Critic verdict read (merge-gate.yml VERDICT=)."""

    marker = "QA Critic"

    def test_01_qa_bot_approve_counts(self):
        got = self.latest_verdict([comment(QA_LOGIN, QA_APPROVE)])
        self.assertEqual(got, QA_APPROVE)

    def test_02_engineer_bot_forged_approve_is_invisible(self):
        # THE hole this card closes: the PR-authoring worker bot posts the
        # magic words on its own PR. Must not count as any verdict at all.
        got = self.latest_verdict([comment(WORKER_LOGIN, QA_APPROVE)])
        self.assertIsNone(got)

    def test_03_human_approve_is_invisible(self):
        got = self.latest_verdict(
            [comment("some-human", 'QA Critic — VERDICT: APPROVE — lgtm!')]
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


class VerifierVerdictAuthorshipTest(AuthorshipFilterTest):
    """Condition 3: the QA Verifier verdict read (merge-gate.yml VVERDICT=).
    An ABSENT verifier verdict is not a gate (scope-gated stage), so a
    forged verdict being INVISIBLE (None) means 'verifier did not run'."""

    marker = "QA Verifier"

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
        # "not PASS — holding" branch fires. (This is why verify.yml now
        # posts its verdict with the qa-bot token, atomically in DRE-1987 —
        # a worker-bot-authored FAIL would be invisible, see case 12.)
        got = self.latest_verdict([comment(QA_LOGIN, V_FAIL)])
        self.assertEqual(got, V_FAIL)

    def test_12_worker_bot_fail_is_invisible(self):
        # The OLD verify.yml posted with the worker App token; under the
        # authorship filter that author is ignored — hence the token switch.
        got = self.latest_verdict([comment(WORKER_LOGIN, V_FAIL)])
        self.assertIsNone(got)

    def test_13_qa_bot_neutral_could_not_run_counts_and_holds(self):
        # Contains "QA Verifier" but no "VERDICT: PASS": visible, latest,
        # supersedes any stale PASS, and holds the merge.
        got = self.latest_verdict(
            [comment(QA_LOGIN, V_PASS), comment(QA_LOGIN, V_NEUTRAL)]
        )
        self.assertEqual(got, V_NEUTRAL)


class FilterShapeTest(unittest.TestCase):
    """Both verdict reads must exist and be author-guarded — catches a
    refactor that deletes a read outright (the extraction would fail
    inside the case tests too, but this names the failure plainly)."""

    def test_both_filters_present_and_author_guarded(self):
        for marker in ("QA Critic", "QA Verifier"):
            expr = extract_filter(marker)
            self.assertIn(
                "env.QA_LOGIN",
                expr,
                f"the {marker} verdict read lost its authorship guard",
            )


if __name__ == "__main__":
    unittest.main()
