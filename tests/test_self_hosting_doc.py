"""Self-hosting go-live record (DRE-2033 — the Option A acceptance smoke).

docs/self-hosting.md is the durable record that bureau-pipeline became a
dispatch target for its own cards (DRE-1929 Option A, live 2026-07-11).
These tests pin the facts the doc must carry — above all the v1 tag sha,
which is verifiable in-repo (`git rev-parse v1^{commit}`) and must never
drift from what the doc claims.
"""

import unittest
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "self-hosting.md"

# The v1 release the fleet consumes (annotated tag → this commit).
V1_SHA = "7ff9374"


class SelfHostingDoc(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOC.is_file(), f"missing {DOC}")
        self.text = DOC.read_text()

    def test_records_go_live_date_and_decision(self):
        self.assertIn("2026-07-11", self.text)
        self.assertIn("DRE-1929", self.text)
        self.assertIn("adr-bureau-pipeline-self-host", self.text)

    def test_records_v1_tag_sha(self):
        self.assertIn(V1_SHA, self.text)

    def test_records_canary_channel_and_human_gate(self):
        self.assertIn("@main", self.text)
        self.assertIn("canary", self.text.lower())
        self.assertIn("promot", self.text.lower())  # promotion/promotes

    def test_points_at_readme_release_channel_section(self):
        self.assertIn("README", self.text)
        self.assertIn("Release channel", self.text)


if __name__ == "__main__":
    unittest.main()
