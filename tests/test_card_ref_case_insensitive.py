"""Lockstep tests: workflow card-ref extraction is case-insensitive and
normalizes to uppercase (DRE-2003).

should_review_pr.py's docstring promises its `DRE-<n>` pattern stays in
lockstep with the extraction sites in linear-sync.yml and merge-gate.yml.
On 2026-07-09 a lowercase `ops/dre-N-...` branch proved the Python gate was
case-sensitive while the workflow-level contains() was not — a silent review
bypass — and the same case-sensitive `grep -oE 'DRE-[0-9]+'` in linear-sync
means a lowercase branch also never closes its card on merge.

These tests EXECUTE the actual `CARD=$(...)` extraction line lifted from each
workflow's run block (old-shell-parity style, like the merge-gate decision
tests), so they fail on the real shipped shell, not a re-implementation:

  • lowercase `dre-123` in a branch/title  → extracts, normalized to DRE-123
  • uppercase `DRE-9`                      → still extracts (no regression)
  • no card ref                            → still empty (no over-match)

DRE-2016 extends the same lockstep coverage to the four remaining
extraction sites the DRE-2003 audit found: agent-fix, qa-review, medic,
verify. Those feed Linear comments/context (not the merge decision), so a
case-sensitive miss only breaks card-linking — but the contract is the same.

DRE-4179 adds a SECOND site in qa-review.yml: the PR-side lane write. It is
back in merge-gate's class of consequence — its $CARD is MOVED, not merely
commented on — so it reads an ANCHORED `^agent/DRE-<n>-` where the first site
reads the card id anywhere in the ref. Both are covered; `EXTRACTION_SITES`
below is what fails when a third appears with no class of its own.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def card_extraction_lines(workflow: str) -> list:
    """Every literal `CARD=$(...)` line in a workflow file — the ones that
    extract from branch/title text. linear-sync.yml carries a second, fenced
    `CARD=$(...)` since DRE-3665 (the dependabot join, which delegates to
    `dependabot_card.py` and extracts from no shell text itself); it is pinned
    by tests/test_dependabot_card.py and is not an extraction site in the
    DRE-2003 sense, so it is dropped here."""
    text = (WORKFLOWS / workflow).read_text()
    return [
        ln.strip()
        for ln in text.splitlines()
        if re.match(r"\s*CARD=\$\(", ln) and "dependabot_card.py" not in ln
    ]


# How many extraction sites each workflow has, and therefore how many classes
# below cover it. A count, not a list of shapes: the thing worth failing on is
# a site nobody wrote a case-insensitivity test for, and that is exactly what a
# number catches. qa-review.yml has TWO since DRE-4179 — the Resolve PR step's
# (unanchored, off `$BRANCH`, for logging the card and fetching its context)
# and the PR-side lane write's (anchored `^agent/DRE-<n>-`, off `$HEAD_REF`,
# because that one MOVES a card and an unanchored match would move the wrong
# one). Both are DRE-2003 sites; both are covered.
EXTRACTION_SITES = {
    "merge-gate.yml": 1,
    "linear-sync.yml": 1,
    "agent-fix.yml": 1,
    "qa-review.yml": 2,
    "medic.yml": 1,
    "verify.yml": 1,
}


def card_extraction_line(workflow: str, *, reads: str = "") -> str:
    """The workflow's one extraction line, or the one reading `$<reads>`."""
    lines = card_extraction_lines(workflow)
    if reads:
        lines = [ln for ln in lines if f"${reads}" in ln]
    assert len(lines) == 1, (
        f"expected exactly one CARD=$(...) extraction in {workflow}"
        f"{f' reading ${reads}' if reads else ''}, found {len(lines)}"
    )
    return lines[0]


class EveryExtractionSiteIsCoveredTest(unittest.TestCase):
    """The guard the single-line assertion used to be. A new `CARD=$(...)` in
    any of these workflows fails here until a class below covers it — an
    uncovered site is how DRE-2003's silent review bypass shipped."""

    def test_the_site_count_matches_the_classes_below(self):
        for workflow, expected in EXTRACTION_SITES.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(len(card_extraction_lines(workflow)), expected)


def run_extraction(line: str, env_setup: str) -> str:
    """Execute the extracted shell line under the workflow's shell options
    and return the resulting $CARD."""
    script = f'set -euo pipefail; {env_setup}; {line}; printf "%s" "$CARD"'
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class MergeGateExtractionTest(unittest.TestCase):
    """merge-gate.yml parses $BRANCH; its $CARD drives the Linear
    'In Review' advance + merge comment."""

    def setUp(self):
        self.line = card_extraction_line("merge-gate.yml")

    def _card(self, branch: str) -> str:
        return run_extraction(self.line, f'BRANCH="{branch}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


class LinearSyncExtractionTest(unittest.TestCase):
    """linear-sync.yml parses $HEAD_REF (only — DRE-2027 dropped $PR_TITLE
    from extraction: titles are prose, not provenance); its $CARD is the card
    closed on merge (card → Done). Case-insensitivity now applies to the
    card's own agent branch — the one shape still allowed to auto-Done; the
    Done-gate cases themselves live in tests/test_linear_sync_done_gate.py."""

    def setUp(self):
        self.line = card_extraction_line("linear-sync.yml")

    def _card(self, head_ref: str) -> str:
        return run_extraction(self.line, f'HEAD_REF="{head_ref}"')

    def test_lowercase_agent_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


class AgentFixExtractionTest(unittest.TestCase):
    """agent-fix.yml parses $BRANCH; its $CARD threads the card ref into
    the fixing agent's context and Linear attempt comments."""

    def setUp(self):
        self.line = card_extraction_line("agent-fix.yml")

    def _card(self, branch: str) -> str:
        return run_extraction(self.line, f'BRANCH="{branch}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


class QaReviewExtractionTest(unittest.TestCase):
    """qa-review.yml parses $BRANCH; its $CARD gives the critic the card
    context and addresses the verdict's Linear comment."""

    def setUp(self):
        self.line = card_extraction_line("qa-review.yml", reads="BRANCH")

    def _card(self, branch: str) -> str:
        return run_extraction(self.line, f'BRANCH="{branch}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


class QaReviewLaneWriteExtractionTest(unittest.TestCase):
    """qa-review.yml's second site (DRE-4179): the PR-side lane write parses
    $HEAD_REF and its $CARD is MOVED, so this one is anchored. The lane cases
    live in tests/test_pr_side_in_review.py; what is asserted here is the
    DRE-2003 contract — a lowercase `agent/dre-N-` head must move its card,
    not be silently skipped."""

    def setUp(self):
        self.line = card_extraction_line("qa-review.yml", reads="HEAD_REF")

    def _card(self, head_ref: str) -> str:
        return run_extraction(self.line, f'HEAD_REF="{head_ref}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")

    def test_a_card_named_later_in_the_ref_stays_empty(self):
        """The anchor, in the lockstep file too: this site differs from the one
        above by design and a copy of that regex here would be the defect."""
        self.assertEqual(self._card("bot/rename-agent/DRE-123-x"), "")


class MedicExtractionTest(unittest.TestCase):
    """medic.yml parses $HEAD_BRANCH; its $CARD addresses the
    reviewer-down note posted to the card."""

    def setUp(self):
        self.line = card_extraction_line("medic.yml")

    def _card(self, head_branch: str) -> str:
        return run_extraction(self.line, f'HEAD_BRANCH="{head_branch}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


class VerifyExtractionTest(unittest.TestCase):
    """verify.yml parses $BRANCH; its $CARD gives the Verifier the card
    context and addresses the PASS/FAIL Linear comment."""

    def setUp(self):
        self.line = card_extraction_line("verify.yml")

    def _card(self, branch: str) -> str:
        return run_extraction(self.line, f'BRANCH="{branch}"')

    def test_lowercase_branch_extracts_uppercase_card(self):
        self.assertEqual(self._card("agent/dre-123-x"), "DRE-123")

    def test_uppercase_branch_still_extracts(self):
        self.assertEqual(self._card("agent/DRE-9-x"), "DRE-9")

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(self._card("chore/deps"), "")


if __name__ == "__main__":
    unittest.main()
