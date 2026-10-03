"""RED-first tests for DRE-5632 — the fix agent answers a What's new finding in
the pull request body and pushes one empty commit.

THE DEAD END THIS CLOSES. Once DRE-5512 makes a missing or malformed
`What's new:` line a blocking critic finding, the fix for that finding lives in
the pull request BODY, and a body edit pushes nothing. The critic re-runs only
on `opened`, `reopened` and `synchronize`; a `REQUEST_CHANGES` verdict is bound
to the head SHA; `agent-fix.yml`'s Report step reads "no new commit" as no
progress; and the fixing agent's own step 4b forbids a push whose diff did not
move ("a byte-identical resubmission burns a whole review round"). So every
such pull request would park in Triage with `needs-human` over a line of text.

THE REMEDY, AND WHY IT IS ONLY A PROMPT CHANGE. The fix for a line-only finding
is the line in the body followed by ONE empty commit on the same branch. The
push is a `synchronize`, so the critic re-reviews the new head everywhere it
already runs — no change to `pr-review.yml`, any fleet stub, the Report step,
the handoff kinds or the act registry. `scripts/check_tdd_commits.py`
classifies a commit by its paths and an empty commit has none, so it is neither
a test nor an implementation commit and the branch's order is unchanged.

The contract shared with DRE-5508, DRE-5510, DRE-5512 and the proof DRE-5633:
the line opens `What's new:`; its grammar is `standards/whats-new.md`; the empty
commit's subject opens `fix(<card>): What's new line` — the string the proof
looks for on the pull request's commit list.

The workflow is read through `step_shell.workflow_source` (the DRE-5222 shape),
so the test reads the same text YAML would after any `run:` moves into scripts.

Run: python3 -m pytest tests/test_whats_new_fix_resubmission.py -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_tdd_commits  # noqa: E402
import step_shell  # noqa: E402

WORKFLOW = ".github/workflows/agent-fix.yml"
FIX_JOB = "fix"

LINE_LABEL = "What's new:"
STANDARD = "standards/whats-new.md"

# The numbered instructions the exception and the commit form belong to.
STEP_4B_RE = re.compile(r"(?m)^\s*4b\. ")
STEP_5_RE = re.compile(r"(?m)^\s*5\. ")
STEP_6_RE = re.compile(r"(?m)^\s*6\. ")

# The card expression the prompt already uses for its ordinary fix commit.
CARD_EXPR = r"\$\{\{\s*steps\.pr\.outputs\.card\s*\}\}"


def workflow() -> dict:
    return yaml.safe_load(step_shell.workflow_source(WORKFLOW, root=ROOT))


def fix_prompt() -> str:
    """The `prompt:` of the ONE step in the fix job that carries one."""
    steps = workflow()["jobs"][FIX_JOB]["steps"]
    found = [s for s in steps if "prompt" in (s.get("with") or {})]
    assert len(found) == 1, (
        f"expected exactly one step carrying `prompt:` in the {FIX_JOB!r} job, "
        f"found {[s.get('name') for s in found]}"
    )
    return found[0]["with"]["prompt"]


def flat(text: str) -> str:
    """Collapse the prompt's hard wraps so a sentence reads as one line."""
    return re.sub(r"\s+", " ", text)


def section(prompt: str, start: re.Pattern, end: re.Pattern) -> str:
    begin = start.search(prompt)
    assert begin is not None, f"{start.pattern!r} not found in the fix prompt"
    stop = end.search(prompt, begin.end())
    assert stop is not None, f"{end.pattern!r} not found after {start.pattern!r}"
    return prompt[begin.start():stop.start()]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat(text)) if s.strip()]


class Step4bNamesTheWhatsNewException(unittest.TestCase):
    """Step 4b's moved-diff rule gains one exception for a line-only finding."""

    def setUp(self):
        self.step = section(fix_prompt(), STEP_4B_RE, STEP_5_RE)
        self.text = flat(self.step)

    def test_the_exception_names_the_line_and_its_standard(self):
        self.assertIn(LINE_LABEL, self.text)
        self.assertIn(STANDARD, self.text)

    def exception(self) -> str:
        """Step 4b from its first mention of the line on — the exception's own
        text, so the step's older `gh pr edit` for unmet criteria cannot
        satisfy an assertion about the What's new remedy."""
        at = self.text.find(LINE_LABEL)
        self.assertNotEqual(at, -1, f"step 4b never names {LINE_LABEL!r}")
        return self.text[at:]

    def test_the_fix_is_a_body_edit_followed_by_one_empty_commit(self):
        tail = self.exception()
        self.assertRegex(
            tail,
            r"gh pr edit [^\n]{0,80}--body-file\b",
            "step 4b must send a What's new fix to the body through gh pr edit",
        )
        self.assertRegex(
            tail,
            r"(?i)\b(exactly )?one empty commit\b",
            "step 4b must say the body edit is followed by ONE empty commit",
        )
        self.assertLess(
            tail.find("--body-file"),
            re.search(r"(?i)\bone empty commit\b", tail).start(),
            "the body edit comes first, then the empty commit",
        )

    def test_the_exception_says_it_is_not_a_byte_identical_resubmission(self):
        hits = [
            s for s in sentences(self.step)
            if re.search(r"(?i)\bnot a byte-identical resubmission\b", s)
        ]
        self.assertTrue(
            hits,
            "step 4b must say the body edit plus one empty commit is NOT a "
            "byte-identical resubmission",
        )

    def test_the_exception_is_scoped_to_what_s_new_findings(self):
        # The exception must say WHEN it applies — every blocking finding is
        # about the line — or it reads as a license to push empty commits.
        self.assertRegex(
            self.text,
            r"(?i)\bevery blocking finding\b[^.]{0,80}What's new:",
        )

    def test_the_moved_diff_rule_itself_still_stands(self):
        # An exception, not a repeal: the general rule is still there.
        self.assertRegex(
            self.text,
            r"(?i)\bA byte-identical resubmission burns a whole review round\b",
        )


class Step5CarriesTheEmptyCommitForm(unittest.TestCase):
    """Step 5 names the commit the proof (DRE-5633) looks for."""

    def setUp(self):
        self.step = section(fix_prompt(), STEP_5_RE, STEP_6_RE)
        self.text = flat(self.step)

    def test_the_empty_commit_subject_opens_fix_card_whats_new_line(self):
        self.assertRegex(
            self.text,
            r"git commit --allow-empty -m \"fix\(" + CARD_EXPR + r"\): What's new line\b",
        )

    def test_it_is_pushed_to_the_same_branch(self):
        hits = [
            s for s in sentences(self.step)
            if "--allow-empty" in s or re.search(r"(?i)\bempty commit\b", s)
        ]
        self.assertTrue(hits, "step 5 must carry the empty-commit form")
        self.assertTrue(
            any(re.search(r"(?i)\bsame branch\b", s) for s in hits),
            f"the empty commit must be pushed to the SAME branch; read: {hits}",
        )

    def test_the_ordinary_fix_commit_is_unchanged(self):
        # The existing subject is what tests/test_stranded_fix_scenario.py and
        # the loop's readers already know; the new form sits beside it.
        self.assertRegex(
            self.text,
            r"fix\(" + CARD_EXPR + r"\): address review findings",
        )


class AnEmptyCommitLeavesTheTddAnswerUnchanged(unittest.TestCase):
    """An empty commit has no paths, so it is neither a test nor an
    implementation commit: appending one never changes check_commits."""

    @staticmethod
    def commit(paths, subject="a commit"):
        return {"sha": "f" * 40, "subject": subject, "paths": list(paths)}

    def empty(self):
        return self.commit([], "fix(DRE-5632): What's new line (attempt 1)")

    def assert_unchanged(self, commits):
        before = check_tdd_commits.check_commits(commits)
        after = check_tdd_commits.check_commits(commits + [self.empty()])
        self.assertEqual(before, after)
        return after

    def test_a_passing_test_then_code_branch_still_passes(self):
        ok, _ = self.assert_unchanged([
            self.commit(["tests/test_x.py"], "test: red"),
            self.commit(["scripts/x.py"], "feat: green"),
        ])
        self.assertTrue(ok)

    def test_an_ops_only_branch_stays_exempt(self):
        ok, reason = self.assert_unchanged([
            self.commit(["tests/test_whats_new_fix_resubmission.py"], "test: red"),
            self.commit([".github/workflows/agent-fix.yml"], "feat: prompt"),
        ])
        self.assertTrue(ok)
        self.assertIn("exempt", reason)

    def test_the_empty_commit_has_no_category(self):
        self.assertEqual(check_tdd_commits.commit_categories(self.empty()), set())


if __name__ == "__main__":
    unittest.main()
