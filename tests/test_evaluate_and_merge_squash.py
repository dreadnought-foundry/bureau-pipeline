"""The merge gate squashes a proof record and merge-commits everything else (DRE-8171),
squashing only where the repository allows squash (DRE-8195).

A proof-record branch, `agent/DRE-<n>-proof-record`, can carry a commit whose
message or diff names a person. Merged with a merge commit, every commit on
the branch lands on `main` for good; squashed, only the one squash commit does.
So scripts/evaluate_and_merge.sh picks its merge flag off the head branch,
through the one reader that already decides what a record branch is —
`proof_record.proof_record_branch` — and every other branch keeps `--merge`,
because the TDD split commits must stay visible on `main`. A record squashes
only where the repository's own `allow_squash_merge` is true: GitHub refuses a
squash where it is false (agent-bureau), so there the record merges with
`--merge`, and a setting it cannot read keeps the squash.

These runs drive the file by path against the stub `gh` of
tests/test_merge_gate_one_read.py, which records every argv, and read the
merge call(s) off that log: the first try, and the one retry GitHub's own
server error earns (DRE-6242), carry the same flag.

Run: python3 -m pytest tests/test_evaluate_and_merge_squash.py -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import proof_record  # noqa: E402
from test_evaluate_and_merge import header  # noqa: E402
from test_merge_gate_one_read import (  # noqa: E402
    ALL_MET, AUTHOR_APP_X, CI_RUN_DONE, DEPENDABOT_BRANCH, GREEN_CI, HEAD,
    PATCH_COMMIT, PR, RECORD_FILES, REPO, SERVER_ERROR, green_fixture,
    run_gate, view,
)

RECORD_BRANCH = "agent/DRE-8171-proof-record"
CARD_BRANCH = "agent/DRE-8171-squash-proof-records"
REPAIR_BRANCH = "repair/main-red-1a2b3c4"
TRAILING = ["--delete-branch", "--match-head-commit", HEAD]


def merge_argv(flag: str) -> list:
    return ["pr", "merge", str(PR), flag, *TRAILING]


def record_fixture(**over) -> dict:
    fx = green_fixture(headRefName=RECORD_BRANCH, files=RECORD_FILES)
    fx["record"] = ALL_MET
    fx.update(over)
    return fx


def card_fixture(branch: str, **over) -> dict:
    fx = green_fixture(headRefName=branch)
    fx.update(over)
    return fx


def dependabot_fixture(**over) -> dict:
    fx = {"view": view(headRefName=DEPENDABOT_BRANCH, author=AUTHOR_APP_X),
          "check_runs": GREEN_CI, "workflow_runs": [CI_RUN_DONE],
          "pr_commits": [PATCH_COMMIT]}
    fx.update(over)
    return fx


class TheBranchesThisSuiteRunsTest(unittest.TestCase):
    """The fixtures are what they say: the reader calls only the first a
    record branch, so a change to the reader cannot quietly empty a case."""

    def test_only_the_record_branch_is_a_record_branch(self):
        self.assertTrue(proof_record.proof_record_branch(RECORD_BRANCH))
        for branch in (CARD_BRANCH, REPAIR_BRANCH, DEPENDABOT_BRANCH,
                       "agent/DRE-8171-proof-record-v2"):
            with self.subTest(branch=branch):
                self.assertFalse(proof_record.proof_record_branch(branch))


class AProofRecordIsSquashedTest(unittest.TestCase):
    def test_the_merge_is_a_squash_pinned_to_the_evaluated_head(self):
        run = run_gate(record_fixture(), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(run.merges, [merge_argv("--squash")], run.explain())

    def test_the_retry_after_githubs_server_error_squashes_too(self):
        run = run_gate(record_fixture(merge_errors=[SERVER_ERROR]), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("merged PR", run.proc.stdout, run.explain())
        self.assertEqual(run.merges, [merge_argv("--squash")] * 2, run.explain())


def setting_reads(run) -> list:
    return [c for c in run.calls
            if c[:2] == ["api", f"repos/{REPO}"] and ".allow_squash_merge" in c]


class TheRepositorysSettingPicksTheRecordsMethodTest(unittest.TestCase):
    """A proof record squashes only where the repository allows squash
    (DRE-8195). agent-bureau has `allow_squash_merge: false`, and GitHub
    refuses a squash there, so its record merges with a merge commit."""

    def test_a_record_where_squash_is_not_allowed_merges_with_a_merge_commit(self):
        run = run_gate(record_fixture(allow_squash_merge=False), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(run.merges, [merge_argv("--merge")], run.explain())
        self.assertIn("allow_squash_merge: false", run.proc.stdout, run.explain())
        self.assertIn("merge method: --merge", run.proc.stdout, run.explain())

    def test_the_retry_where_squash_is_not_allowed_is_a_merge_commit_too(self):
        run = run_gate(record_fixture(allow_squash_merge=False,
                                      merge_errors=[SERVER_ERROR]), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.merges, [merge_argv("--merge")] * 2, run.explain())

    def test_a_record_where_squash_is_allowed_still_squashes(self):
        run = run_gate(record_fixture(allow_squash_merge=True), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.merges, [merge_argv("--squash")], run.explain())
        self.assertIn("allow_squash_merge: true", run.proc.stdout, run.explain())
        self.assertIn("merge method: --squash", run.proc.stdout, run.explain())

    def test_the_setting_is_read_once(self):
        run = run_gate(record_fixture(allow_squash_merge=False,
                                      merge_errors=[SERVER_ERROR]), linear_stub=True)
        self.assertEqual(len(setting_reads(run)), 1, run.explain())

    def test_an_unreadable_setting_keeps_the_squash(self):
        run = run_gate(record_fixture(repo_read_fails=True), linear_stub=True)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(run.merges, [merge_argv("--squash")], run.explain())
        self.assertEqual(len(setting_reads(run)), 1, run.explain())
        self.assertIn("could not be read", run.proc.stdout, run.explain())

    def test_a_refusal_stays_loud(self):
        run = run_gate(record_fixture(
            allow_squash_merge=False,
            merge_error="GraphQL: Pull request is not mergeable (mergePullRequest)"),
            linear_stub=True)
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertIn("real failure", run.proc.stdout, run.explain())

    def test_every_other_branch_keeps_its_merge_commit_either_way(self):
        for allowed in (True, False):
            for name, fixture in EveryOtherBranchKeepsItsMergeCommitTest.CASES.items():
                with self.subTest(allow_squash_merge=allowed, branch=name):
                    run = run_gate(fixture(allow_squash_merge=allowed), linear_stub=True)
                    self.assertEqual(run.decision(), "merge", run.explain())
                    self.assertEqual(run.merges, [merge_argv("--merge")], run.explain())


class EveryOtherBranchKeepsItsMergeCommitTest(unittest.TestCase):
    """The TDD split commits stay visible on `main`: `--merge`, never
    `--squash`, with the same trailing flags."""

    CASES = {
        "agent-card-branch": lambda **over: card_fixture(CARD_BRANCH, **over),
        "repair-branch": lambda **over: card_fixture(REPAIR_BRANCH, **over),
        "dependabot-branch": dependabot_fixture,
    }

    def test_the_merge_is_a_merge_commit(self):
        for name, fixture in self.CASES.items():
            with self.subTest(branch=name):
                run = run_gate(fixture(), linear_stub=True)
                self.assertEqual(run.decision(), "merge", run.explain())
                self.assertEqual(run.merges, [merge_argv("--merge")], run.explain())

    def test_the_retry_after_githubs_server_error_is_a_merge_commit_too(self):
        for name, fixture in self.CASES.items():
            with self.subTest(branch=name):
                run = run_gate(fixture(merge_errors=[SERVER_ERROR]), linear_stub=True)
                self.assertEqual(run.proc.returncode, 0, run.explain())
                self.assertEqual(run.merges, [merge_argv("--merge")] * 2, run.explain())


class TheScriptAsksTheOneReaderTest(unittest.TestCase):
    """The branch rule is not restated in the shell: one `MERGE_METHOD`,
    decided through `proof_record.proof_record_branch`, used by both calls."""

    def test_both_merge_calls_use_the_one_variable(self):
        text = (ROOT / "scripts" / "evaluate_and_merge.sh").read_text()
        calls = [ln for ln in text.splitlines()
                 if re.search(r"\bgh pr merge\b", ln) and not ln.lstrip().startswith("#")]
        self.assertEqual(len(calls), 2, calls)
        for call in calls:
            with self.subTest(call=call):
                self.assertIn('"$MERGE_METHOD" --delete-branch --match-head-commit "$SHA"', call)
        self.assertIn("proof_record.proof_record_branch", text)
        self.assertNotRegex(text, r"proof-record\)|\*-proof-record")


class TheHeaderStatesTheRuleTest(unittest.TestCase):
    def test_what_it_does_names_the_squash_rule_and_its_reader(self):
        what, history = "\n".join(header()).split("Incident history", 1)
        paragraphs = [p for p in re.split(r"\n#\s*\n", what) if "--squash" in p]
        self.assertEqual(len(paragraphs), 1, what)
        paragraph = paragraphs[0]
        self.assertIn("proof_record.proof_record_branch", paragraph)
        self.assertIn("--merge", paragraph)
        self.assertIn("MERGE_METHOD", paragraph)
        self.assertRegex(history, r"DRE-8171(?!\d)")

    def test_what_it_does_says_the_method_follows_the_repositorys_setting(self):
        what, history = "\n".join(header()).split("Incident history", 1)
        paragraph = next(p for p in re.split(r"\n#\s*\n", what) if "--squash" in p)
        self.assertIn("allow_squash_merge", paragraph)
        # The why: GitHub refuses a squash where the setting is off, and the
        # name hazard a squash closes is a public repository's.
        self.assertRegex(paragraph, r"refuses")
        self.assertRegex(paragraph, r"public")
        self.assertRegex(history, r"DRE-8195(?!\d)")


if __name__ == "__main__":
    unittest.main()
