"""The critic's TDD check compares against the pull request's base (DRE-5896).

`briefs/critic.md` told the critic to run
`check_tdd_commits.py origin/main HEAD`. That is right only where pull
requests target `main`. A repo whose pipeline branch is something else —
EveryBite/atlas lands on `sid/main` — would have every commit on its base that
`main` lacks counted as part of the pull request, and false TDD findings
reported against it.

The base is already read by the review run: `qa-review.yml` names
`baseRefName` in the job's one pull-request record (`PR_RECORD_FIELDS`) and
reads it through `scripts/read_once.py`. The brief points the critic at that
same read, so no second way of finding the base exists.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CRITIC_BRIEF = ROOT / "briefs" / "critic.md"
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import step_shell  # noqa: E402

#: Any invocation of the TDD check, with its two arguments.
_TDD_CALL = re.compile(r"check_tdd_commits\.py\s+(\S+)\s+(\S+)")


class CriticTddCheckUsesTheBaseBranch(unittest.TestCase):

    def setUp(self):
        self.brief = CRITIC_BRIEF.read_text()

    def _calls(self) -> list[tuple[str, str]]:
        calls = _TDD_CALL.findall(self.brief)
        self.assertTrue(
            calls,
            "briefs/critic.md no longer shows the critic how to run "
            "check_tdd_commits.py — the example command is the instruction",
        )
        return calls

    def test_the_brief_never_compares_against_a_literal_main(self):
        for base, _ in self._calls():
            self.assertNotRegex(
                base, r"^[\"']?origin/main[\"']?$",
                "the critic's TDD check names `main` literally; a repo whose "
                "pull requests target another branch gets false findings",
            )

    def test_the_example_compares_against_the_base_variable(self):
        bases = {base for base, _ in self._calls()}
        self.assertTrue(
            any(re.fullmatch(r"\"?origin/\$\{?BASE\}?\"?", b) for b in bases),
            f"the example command must compare against origin/$BASE, got {bases}",
        )

    def test_the_brief_names_the_base_branch(self):
        self.assertRegex(self.brief, r"(?i)base branch")

    def test_base_is_read_the_way_the_review_run_reads_it(self):
        # The same command the workflow's own steps use: the job's one record
        # of the pull request, its `baseRefName` field.
        read = re.search(
            r'BASE=\$\(python3 "\$PIPELINE_DIR"/scripts/read_once\.py pr \S+ '
            r'--fields "\$PR_RECORD_FIELDS" --field baseRefName\)',
            self.brief,
        )
        self.assertIsNotNone(
            read,
            "the brief must read the base through read_once.py's pull-request "
            "record, the one the review run already holds",
        )

    def test_the_workflow_still_reads_base_that_way(self):
        # If the review run stopped carrying baseRefName in its record, the
        # brief's command would ask for a field the record does not hold.
        doc = yaml.safe_load(step_shell.workflow_source(QA_REVIEW))
        fields = doc["jobs"]["review"]["env"]["PR_RECORD_FIELDS"].split(",")
        self.assertIn("baseRefName", fields)
        self.assertIn(
            '--fields "$PR_RECORD_FIELDS" --field baseRefName',
            QA_REVIEW.read_text(),
        )


if __name__ == "__main__":
    unittest.main()
