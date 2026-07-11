"""Guard tests for bureau-pipeline's own Dependabot config (DRE-2039).

Live-extraction over .github/dependabot.yml (no copied fixtures — same
pattern as test_worker_pool_allowed_bots.py): the policy the merge gate
enforces (minors/patches auto-merge, majors human) only holds if the config
SHAPES the PRs that way — minor+patch grouped per ecosystem so they arrive
as one gate-mergeable PR, majors left outside every group so they arrive as
individual human-merged PRs. A config edit that grouped majors in, dropped
an ecosystem, or moved off the weekly cadence would silently change what
auto-merges — these tests make that a red diff instead.

The pip ecosystem needs a manifest to watch: requirements-dev.txt pins the
CI test toolchain, and tests.yml must install from it — otherwise Dependabot
bumps a file CI never reads and the bumped PR's green proves nothing.
"""

import os
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".github" / "dependabot.yml"
MANIFEST = ROOT / "requirements-dev.txt"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

EXPECTED_ECOSYSTEMS = {"pip", "github-actions"}


def updates():
    doc = yaml.safe_load(CONFIG.read_text())
    assert doc.get("version") == 2, "dependabot.yml must be version 2"
    return doc["updates"]


class DependabotConfigTest(unittest.TestCase):
    def test_config_exists_and_parses(self):
        self.assertTrue(CONFIG.exists(), ".github/dependabot.yml is missing")
        self.assertTrue(updates(), "no updates entries")

    def test_both_ecosystems_watched_weekly(self):
        entries = {u["package-ecosystem"]: u for u in updates()}
        self.assertEqual(set(entries), EXPECTED_ECOSYSTEMS)
        for eco, entry in entries.items():
            self.assertEqual(
                entry.get("schedule", {}).get("interval"), "weekly",
                f"{eco}: cadence must be weekly (one grouped PR a week, "
                "not a daily drip)",
            )

    def test_each_ecosystem_groups_exactly_minor_and_patch(self):
        for entry in updates():
            eco = entry["package-ecosystem"]
            groups = entry.get("groups") or {}
            self.assertEqual(
                len(groups), 1,
                f"{eco}: expected exactly one minor+patch group, got {groups}",
            )
            (group,) = groups.values()
            self.assertEqual(
                sorted(group.get("update-types", [])), ["minor", "patch"],
                f"{eco}: the group must cover exactly minor+patch — majors "
                "must stay OUTSIDE every group so they arrive as individual "
                "human-merged PRs",
            )
            self.assertEqual(
                group.get("applies-to", "version-updates"), "version-updates",
                f"{eco}: the group is for version updates",
            )

    def test_pip_manifest_exists_and_pins_the_ci_toolchain(self):
        self.assertTrue(
            MANIFEST.exists(),
            "requirements-dev.txt is missing — the pip ecosystem has "
            "nothing to watch without a manifest",
        )
        text = MANIFEST.read_text()
        for pkg in ("pytest", "pyyaml"):
            self.assertRegex(
                text, rf"(?im)^{pkg}==",
                f"{pkg} must be ==-pinned — Dependabot bumps pins, "
                "not open ranges",
            )

    def test_ci_installs_from_the_watched_manifest(self):
        text = TESTS_WORKFLOW.read_text()
        self.assertIn(
            "-r requirements-dev.txt", text,
            "tests.yml must install from requirements-dev.txt, or Dependabot "
            "bumps a file CI never reads and the PR's green proves nothing",
        )
        self.assertNotIn(
            "pip -q install pytest pyyaml", text,
            "the ad-hoc latest-of-everything install must be replaced by the "
            "pinned manifest",
        )


if __name__ == "__main__":
    unittest.main()
