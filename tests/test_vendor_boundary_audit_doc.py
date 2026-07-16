"""Vendor-boundary backfill audit (DRE-2110, child of DRE-2073).

The vendor-boundaries standard (DRE-2105) was distilled from incidents found
LIVE, one at a time. DRE-2110 sweeps the whole existing surface once, on
paper: docs/vendor-boundary-audit-2026-07.md applies the five-question
premortem checklist to every dispatch/trigger/gate surface that existed at
audit time and files every gap as a Linear card.

These tests pin the audit's contract so the record can't silently rot:

  * the doc exists, names the standard and the epic it files under;
  * every workflow file that existed at audit time is covered (the list is
    pinned STATICALLY, like test_self_hosting_docs pins the v1 sha — this is
    a dated backfill record; NEW surfaces are covered by the standard's
    critic gate, not by editing a July audit);
  * every audited surface section answers all five checklist questions
    explicitly and carries an explicit verdict;
  * every gap verdict is actionable: it references a filed DRE card id or a
    ready-to-file draft.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "vendor-boundary-audit-2026-07.md"

# The complete .github/workflows/ roster on audit day (2026-07-16). Pinned
# statically: the audit is a dated record of THIS surface, not a living
# registry of future workflows.
WORKFLOWS_AT_AUDIT = [
    "agent-fix.yml",
    "agent-task.yml",
    "linear-sync.yml",
    "medic.yml",
    "merge-gate.yml",
    "plan.yml",
    "pr-review.yml",
    "qa-review.yml",
    "reconcile.yml",
    "red-main-repair.yml",
    "self-agent-fix.yml",
    "self-agent-task.yml",
    "self-linear-sync.yml",
    "self-medic.yml",
    "self-merge-gate.yml",
    "self-plan.yml",
    "self-reconcile.yml",
    "self-red-main-repair.yml",
    "tests.yml",
    "verify.yml",
]

# Each audited surface gets its own `## ` section; the token must appear in
# the section heading. This is the card's floor: every reusable workflow's
# trigger surface, the dispatch/gate scripts, the self-* stub family, and
# the @dependabot command surface.
SURFACE_HEADING_TOKENS = [
    "agent-task.yml",
    "qa-review.yml",
    "verify.yml",
    "merge-gate.yml",
    "agent-fix.yml",
    "plan.yml",
    "medic.yml",
    "linear-sync.yml",
    "reconcile.yml",
    "red-main-repair.yml",
    "pr-review.yml",
    "tests.yml",
    "self-",  # the stub family section (each stub also named in the body)
    "dispatch_pool.py",
    "dedupe_dispatch.py",
    "@dependabot",
]

QUESTION_MARKERS = ["**Q1", "**Q2", "**Q3", "**Q4", "**Q5"]
VERDICT_RE = re.compile(r"\*\*Verdict:\*\*\s*(.+)", re.IGNORECASE)
CARD_ID_RE = re.compile(r"DRE-\d{3,}")


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) for every `## ` section of the doc."""
    parts = re.split(r"^## +(.+)$", text, flags=re.MULTILINE)
    # parts = [preamble, h1, b1, h2, b2, ...]
    return list(zip(parts[1::2], parts[2::2]))


class TestAuditDocExists(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            DOC.is_file(),
            f"missing {DOC.relative_to(ROOT)} — the DRE-2110 backfill audit",
        )
        self.text = DOC.read_text(encoding="utf-8")

    def test_names_the_standard_and_the_epic(self):
        self.assertIn("vendor-boundaries.md", self.text)
        self.assertIn("DRE-2105", self.text)
        self.assertIn("DRE-2073", self.text)
        self.assertIn("DRE-2110", self.text)

    def test_dated_as_the_july_backfill(self):
        self.assertIn("2026-07", self.text)


class TestEveryWorkflowCovered(unittest.TestCase):
    """Every workflow file that existed at audit time must be named in the
    doc — the card says enumerate from the code, and this is that roster."""

    def setUp(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC.relative_to(ROOT)}")
        self.text = DOC.read_text(encoding="utf-8")

    def test_pinned_roster_matches_the_repo_no_omissions(self):
        # Guard the PIN itself: if the audit-day roster in this test ever
        # disagrees with what the audit commit actually shipped, the pin is
        # wrong, not the doc. (Runs against the same checkout, so at audit
        # time these are equal by construction; later additions are allowed
        # to drift — the doc is dated.)
        for wf in WORKFLOWS_AT_AUDIT:
            self.assertTrue(
                (ROOT / ".github" / "workflows" / wf).is_file(),
                f"pinned workflow {wf} does not exist in the repo",
            )

    def test_every_audit_day_workflow_is_named(self):
        for wf in WORKFLOWS_AT_AUDIT:
            self.assertIn(
                wf, self.text,
                f"audit doc never mentions {wf} — every trigger surface "
                "must be covered",
            )


class TestEverySurfaceAnswersTheChecklist(unittest.TestCase):
    """Each surface section must answer Q1–Q5 explicitly and end in an
    explicit verdict — 'covered' or 'gap', never silence."""

    def setUp(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC.relative_to(ROOT)}")
        self.text = DOC.read_text(encoding="utf-8")
        self.sections = sections(self.text)

    def find(self, token: str) -> tuple[str, str]:
        for heading, body in self.sections:
            if token in heading:
                return heading, body
        self.fail(f"no `## ` section heading contains {token!r}")

    def test_each_surface_has_a_section_with_all_five_answers(self):
        for token in SURFACE_HEADING_TOKENS:
            heading, body = self.find(token)
            for q in QUESTION_MARKERS:
                self.assertIn(
                    q, body,
                    f"section {heading!r} does not answer {q}…** — all five "
                    "checklist questions must be answered explicitly",
                )
            self.assertRegex(
                body, VERDICT_RE,
                f"section {heading!r} has no explicit **Verdict:** line",
            )

    def test_every_verdict_is_covered_or_gap(self):
        verdicts = VERDICT_RE.findall(self.text)
        self.assertGreaterEqual(len(verdicts), len(SURFACE_HEADING_TOKENS))
        for v in verdicts:
            self.assertTrue(
                "covered" in v.lower() or "gap" in v.lower(),
                f"verdict line {v!r} must say covered or gap",
            )

    def test_every_gap_is_filed_or_drafted(self):
        for heading, body in self.sections:
            for v in VERDICT_RE.findall(body):
                if "gap" not in v.lower():
                    continue
                self.assertTrue(
                    CARD_ID_RE.search(v) or "draft" in v.lower(),
                    f"gap verdict in section {heading!r} references neither "
                    f"a filed DRE card id nor a ready-to-file draft: {v!r}",
                )

    def test_each_self_stub_is_named_in_the_stub_section(self):
        _, body = self.find("self-")
        for wf in WORKFLOWS_AT_AUDIT:
            if wf.startswith("self-"):
                self.assertIn(
                    wf, body,
                    f"the stub-family section must cover {wf} individually",
                )


if __name__ == "__main__":
    unittest.main()
