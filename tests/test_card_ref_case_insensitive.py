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
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def card_extraction_line(workflow: str) -> str:
    """The literal `CARD=$(...)` line from a workflow file."""
    text = (WORKFLOWS / workflow).read_text()
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if re.match(r"\s*CARD=\$\(", ln)
    ]
    assert len(lines) == 1, (
        f"expected exactly one CARD=$(...) extraction in {workflow}, "
        f"found {len(lines)}"
    )
    return lines[0]


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


if __name__ == "__main__":
    unittest.main()
