"""linear-sync Done gate: only a card's OWN agent branch closes it (DRE-2027).

The hole (DRE-727 class, audit epic 6): linear-sync.yml derived the card to
move to Done by grepping `DRE-[0-9]+` out of the merged PR's head branch AND
its title, first match wins. A PR whose title says "part of DRE-99" — or any
hand-named branch that merely mentions a card — could auto-Done a card (even
an epic) that was never built, and the cloud era has no auto-revert.

The gate this file pins: the Done transition fires ONLY for the card whose
OWN agent branch was merged — head ref anchored `agent/DRE-<n>-...`, with the
delimiter REQUIRED after the number (so a DRE-142 reference can never act for
DRE-1428; same delimiter rule DRE-2025 lands in agent-task.yml). Card
references in PR titles/bodies never transition anything. Hand-named branches
(`ops/...`) auto-Done NOTHING — the operator closes those cards by hand,
which was already the real behavior for operator work.

Live-extraction style (pattern: tests/test_card_ref_case_insensitive.py,
old-shell-parity like the merge-gate decision tests): each case EXECUTES the
actual `CARD=$(...)` line lifted from linear-sync.yml, so a diff that weakens
the shipped shell turns these red — no re-implementation, no copied fixture.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINEAR_SYNC = ROOT / ".github" / "workflows" / "linear-sync.yml"


def card_extraction_line() -> str:
    """The literal `CARD=$(...)` line from linear-sync.yml."""
    lines = [
        ln.strip()
        for ln in LINEAR_SYNC.read_text().splitlines()
        if re.match(r"\s*CARD=\$\(", ln)
    ]
    assert len(lines) == 1, (
        "expected exactly one CARD=$(...) extraction in linear-sync.yml, "
        f"found {len(lines)}"
    )
    return lines[0]


def extracted_card(head_ref: str, title: str) -> str:
    """Run the extracted shell line exactly as the workflow would (same
    variables in scope) and return the resulting $CARD ('' = Done nothing:
    the workflow's empty-CARD branch exits 0 before any Linear write)."""
    line = card_extraction_line()
    script = (
        "set -euo pipefail; "
        f'HEAD_REF="{head_ref}"; PR_TITLE="{title}"; '
        f'{line}; printf "%s" "$CARD"'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class OwnAgentBranchClosesItsCardTest(unittest.TestCase):
    """The one path that MAY auto-Done: the card's own agent branch."""

    def test_merged_agent_branch_dones_its_own_card(self):
        self.assertEqual(
            extracted_card("agent/DRE-100-fix-thing", "fix(DRE-100): thing"),
            "DRE-100",
        )

    def test_lowercase_agent_branch_normalizes_to_uppercase(self):
        # DRE-2003's guarantee survives the gate: card refs are matched
        # case-insensitively and normalized to uppercase.
        self.assertEqual(
            extracted_card("agent/dre-123-x", "some title"), "DRE-123"
        )

    def test_title_reference_to_another_card_is_inert(self):
        # THE DRE-727 hole: a merged agent/DRE-100 PR titled "part of
        # DRE-99" must Done DRE-100 only — DRE-99 is untouched.
        self.assertEqual(
            extracted_card("agent/DRE-100-x", "part of DRE-99"), "DRE-100"
        )


class ReferencesNeverTransitionTest(unittest.TestCase):
    """Card MENTIONS — titles, hand-named branches, prefix collisions —
    must never move any card."""

    def test_title_only_reference_dones_nothing(self):
        # No agent branch merged → the "part of DRE-99" title is prose,
        # not provenance. Auto-Done'ing DRE-99 here is the shipped bug.
        self.assertEqual(extracted_card("chore/tidy-things", "part of DRE-99"), "")

    def test_ops_branch_dones_nothing(self):
        # Hand-named branches (ops/...) carry no agent provenance: the
        # operator closes those cards by hand — today's real behavior,
        # now the documented one.
        self.assertEqual(
            extracted_card("ops/DRE-101-y", "ops(DRE-101): y"), ""
        )

    def test_agent_prefix_must_be_anchored(self):
        # `agent/` embedded mid-ref is not an agent branch.
        self.assertEqual(
            extracted_card("wip/agent/DRE-100-x", "anything"), ""
        )

    def test_delimiter_required_after_card_number(self):
        # Same delimiter rule as DRE-2025 in agent-task.yml: without a
        # trailing delimiter the number is unterminated — a bare
        # `agent/DRE-142` is not the well-formed `agent/DRE-142-<slug>`
        # shape agent-task creates, so it asserts nothing.
        self.assertEqual(extracted_card("agent/DRE-142", "anything"), "")

    def test_longer_card_number_is_its_own_card_not_a_prefix(self):
        # DRE-142 ≠ DRE-1428: the merged branch's full number wins.
        self.assertEqual(
            extracted_card("agent/DRE-1428-fix", "re: DRE-142"), "DRE-1428"
        )

    def test_no_card_ref_stays_empty(self):
        self.assertEqual(extracted_card("chore/deps", "bump deps"), "")


class ExtractionInputShapeTest(unittest.TestCase):
    """The extraction may consume ONLY the head ref. If $PR_TITLE (or any
    other PR text) re-enters the CARD line, title references regain the
    power to transition cards — red here before it ships."""

    def test_card_line_reads_only_head_ref(self):
        line = card_extraction_line()
        self.assertIn("$HEAD_REF", line)
        self.assertNotIn("PR_TITLE", line)
        self.assertNotIn("PR_BODY", line)


if __name__ == "__main__":
    unittest.main()
