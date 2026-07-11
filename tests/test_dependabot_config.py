"""RED-first tests for DRE-2039 — bureau-pipeline's own dependabot.yml.

Live extraction over .github/dependabot.yml (same pattern as the other
config-pinning suites — no copied fixtures): weekly cadence, minor+patch
GROUPED per ecosystem (pip, github-actions) so routine bumps arrive as one
gate-mergeable PR each, majors falling out as separate single-dependency
PRs the merge gate routes to a human (test_merge_gate_dependabot.py).

Also pins the pip MANIFEST wiring: Dependabot can only bump pins that
exist, and a bumped pin only means something if CI installs from it — so
requirements-dev.txt must exist with exact pins and tests.yml must install
from it (a pin nobody installs is a vacuous update).
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".github" / "dependabot.yml"
REQUIREMENTS = ROOT / "requirements-dev.txt"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

MERGEABLE_UPDATE_TYPES = {"minor", "patch"}


def updates_by_ecosystem():
    doc = yaml.safe_load(CONFIG.read_text())
    assert doc.get("version") == 2, "dependabot.yml must be version 2"
    return {u["package-ecosystem"]: u for u in doc.get("updates", [])}


class DependabotConfigTest(unittest.TestCase):
    def test_config_exists_and_parses(self):
        self.assertTrue(CONFIG.exists(), f"{CONFIG} missing")
        self.assertIsInstance(yaml.safe_load(CONFIG.read_text()), dict)

    def test_both_ecosystems_update_weekly(self):
        updates = updates_by_ecosystem()
        for eco in ("pip", "github-actions"):
            self.assertIn(eco, updates, f"no {eco} update entry")
            self.assertEqual(
                updates[eco].get("schedule", {}).get("interval"), "weekly",
                f"{eco}: cadence must be weekly",
            )

    def test_each_ecosystem_groups_minor_and_patch_only(self):
        """One group per ecosystem, covering every dependency, holding
        EXACTLY minor+patch — so majors are excluded from the group and
        arrive as separate individual PRs (the human-merge lane)."""
        updates = updates_by_ecosystem()
        for eco in ("pip", "github-actions"):
            groups = updates[eco].get("groups") or {}
            self.assertEqual(
                len(groups), 1,
                f"{eco}: expected exactly one minor+patch group, got {groups}",
            )
            (name, group), = groups.items()
            self.assertIn("minor-patch", name,
                          f"{eco}: group name {name!r} should say what it holds")
            self.assertEqual(group.get("patterns"), ["*"],
                             f"{eco}: the group must cover every dependency")
            self.assertEqual(
                set(group.get("update-types") or []), MERGEABLE_UPDATE_TYPES,
                f"{eco}: group must hold exactly minor+patch (majors separate)",
            )

    def test_each_ecosystem_caps_open_prs_at_five(self):
        """DRE-2049 (live: agent-bureau's first sweep opened 27 PRs at once):
        every ecosystem bounds its open PRs so a weekly sweep arrives as a
        reviewable set, not a flood — dependabot holds the rest back until
        slots free up."""
        updates = updates_by_ecosystem()
        for eco in ("pip", "github-actions"):
            self.assertEqual(
                updates[eco].get("open-pull-requests-limit"), 5,
                f"{eco}: open-pull-requests-limit must cap the sweep at 5",
            )

    def test_stable_only_default_is_documented(self):
        # Dependabot's default (no prereleases unless already on one) is the
        # behavior we rely on — the file must say so where the next editor
        # will see it.
        self.assertIn("stable", CONFIG.read_text().lower())


class PipManifestWiringTest(unittest.TestCase):
    """The pip ecosystem needs a real manifest, and CI must consume it."""

    def test_requirements_dev_exists_with_exact_pins(self):
        self.assertTrue(REQUIREMENTS.exists(), f"{REQUIREMENTS} missing")
        pins = {
            m.group(1).lower(): m.group(2)
            for m in re.finditer(
                r"^([A-Za-z0-9._-]+)==([^\s#]+)", REQUIREMENTS.read_text(), re.M
            )
        }
        for pkg in ("pytest", "pyyaml"):
            self.assertIn(pkg, pins, f"{pkg} must be pinned (==) so "
                          "Dependabot has a version to bump")

    def test_ci_installs_from_the_manifest(self):
        text = TESTS_WORKFLOW.read_text()
        self.assertIn(
            "-r requirements-dev.txt", text,
            "tests.yml must install the suite's deps from requirements-dev.txt "
            "— a pin CI ignores makes every Dependabot pip PR vacuous",
        )


if __name__ == "__main__":
    unittest.main()
