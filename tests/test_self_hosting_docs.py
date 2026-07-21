"""Self-hosting go-live record (DRE-2033 — the Option A acceptance smoke).

docs/self-hosting.md is the durable record of the day this repo went on its
own dispatch rail (DRE-1929 Option A). These tests pin the load-bearing facts
so the record can't silently rot:

  * the doc exists and names the go-live date and DRE-1929;
  * the v1 release sha it records matches what the tag actually pointed at
    when cut (pinned statically — CI checkouts are shallow and tagless, so
    the doc is the record, not a live `git rev-parse`);
  * the README section the doc points readers at still exists under the
    heading the doc names (a rename there must update the doc too).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "self-hosting.md"

# The commit the operator cut v1 at (verified via `git rev-parse v1^{commit}`
# on go-live day). The doc records this as a historical fact.
V1_SHA = "7ff9374"
GO_LIVE_DATE = "2026-07-11"
README_SECTION = "Release channel: pinning, canary, promotion"


class TestSelfHostingDoc(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC.relative_to(ROOT)}")
        self.text = DOC.read_text()

    def test_records_go_live_date_and_decision_card(self):
        self.assertIn(GO_LIVE_DATE, self.text)
        self.assertIn("DRE-1929", self.text)
        self.assertIn("adr-bureau-pipeline-self-host", self.text)

    def test_records_v1_release_sha(self):
        self.assertIn(V1_SHA, self.text)

    def test_names_the_canary_channels(self):
        # Both @main riders must be named: the fleet is on tags, these two
        # soak every merge before promotion.
        self.assertIn("agent-bureau", self.text)
        self.assertIn("@main", self.text)

    def test_points_at_a_readme_section_that_exists(self):
        self.assertIn(README_SECTION, self.text)
        readme = (ROOT / "README.md").read_text()
        self.assertIn(
            f"## {README_SECTION}",
            readme,
            "docs/self-hosting.md points at a README heading that no longer "
            "exists — update the doc's pointer alongside the rename",
        )


class TestHarnessProvesPromotion(unittest.TestCase):
    """DRE-2103: the ADR line grows a third clause — agents author, human
    promotes, HARNESS PROVES. Both operator-facing docs must carry the
    clause and the exact pre-tag command (the pipeline_ref dispatch is how
    a candidate sha earns its green stamp before the tag exists)."""

    PRE_TAG_COMMAND = "gh workflow run harness.yml"

    def setUp(self):
        self.doc = DOC.read_text()
        self.readme = (ROOT / "README.md").read_text()

    def test_both_docs_carry_the_harness_proves_clause(self):
        for text in (self.doc, self.readme):
            self.assertIn("harness proves", text)

    def test_both_docs_document_the_exact_pre_tag_command(self):
        for text in (self.doc, self.readme):
            self.assertIn(self.PRE_TAG_COMMAND, text)
            self.assertIn("pipeline_ref=", text)

    def test_readme_names_the_release_gate_workflow(self):
        # The enforcement half: a v* tag with no green stamp goes loudly
        # red via release-gate.yml — the README promotion section must say
        # so, or the gate reads as an unexplained surprise.
        self.assertIn("release-gate.yml", self.readme)


if __name__ == "__main__":
    unittest.main()
