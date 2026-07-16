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
PLAYBOOK = ROOT / "docs" / "dependabot-major-rejection.md"

MERGEABLE_UPDATE_TYPES = {"minor", "patch"}
MAJOR_IGNORE_TYPE = "version-update:semver-major"


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


def assert_ignore_rules_reject_majors_per_dependency(testcase, eco, update):
    """The one shape an ignore rule may take here (DRE-2118): per-dependency,
    majors only. Anything broader would silently swallow the minor/patch
    stream the grouped auto-merge lane depends on."""
    for rule in update.get("ignore") or []:
        testcase.assertIn(
            "dependency-name", rule,
            f"{eco}: every ignore rule must name ONE dependency — a bare "
            "rule ignores the whole ecosystem",
        )
        testcase.assertEqual(
            rule.get("update-types"), [MAJOR_IGNORE_TYPE],
            f"{eco}: ignore rule for {rule.get('dependency-name')!r} must "
            "reject majors ONLY — without update-types Dependabot stops "
            "proposing minors and patches too",
        )


class MajorRejectionTest(unittest.TestCase):
    """DRE-2118 — the config-ignore rejection path for parked majors.

    The merge gate parks every Dependabot major for a human, but both
    `@dependabot ignore*` commands are booby-trapped (DRE-2064 walk-down,
    DRE-2062 grouped-PR refusal) and plain closing re-files weekly. The
    durable rejection is a config `ignore` stanza. These tests pin the
    operator playbook and the config template that carries the pattern.
    """

    def test_playbook_doc_exists(self):
        self.assertTrue(
            PLAYBOOK.is_file(),
            f"missing {PLAYBOOK.relative_to(ROOT)} — the operator playbook "
            "for rejecting a Dependabot major",
        )

    def test_playbook_names_the_one_safe_path_in_order(self):
        text = PLAYBOOK.read_text()
        self.assertIn(".github/dependabot.yml", text)
        self.assertIn(MAJOR_IGNORE_TYPE, text,
                      "the playbook must show the exact ignore stanza shape")
        step1 = text.find("Step 1")
        step2 = text.find("Step 2")
        self.assertGreaterEqual(step1, 0, "playbook lost its Step 1")
        self.assertGreater(step2, step1,
                           "config ignore stanza (Step 1) must come BEFORE "
                           "closing the PR (Step 2)")

    def test_playbook_forbids_the_comment_commands(self):
        text = PLAYBOOK.read_text()
        self.assertIn("@dependabot ignore", text,
                      "the do-not-use commands must be named explicitly")
        # The live incidents are the argument — the doc cites both.
        self.assertIn("DRE-2064", text)  # the ignore-command walk-down
        self.assertIn("DRE-2062", text)  # ignore refuses grouped PRs

    def test_config_points_at_the_playbook(self):
        self.assertIn(
            "docs/dependabot-major-rejection.md", CONFIG.read_text(),
            "dependabot.yml must point the next editor at the playbook",
        )

    def test_commented_template_splices_into_a_valid_ignore_stanza(self):
        """The template in dependabot.yml must be copy-paste correct: strip
        the comment markers between its sentinel lines and the result must
        parse as exactly the per-dependency major-only shape. Mangle the
        template's indentation and this goes red."""
        lines = CONFIG.read_text().splitlines()
        starts = [i for i, l in enumerate(lines)
                  if "begin major-reject template" in l]
        ends = [i for i, l in enumerate(lines)
                if "end major-reject template" in l]
        self.assertEqual(len(starts), 1,
                         "dependabot.yml must carry exactly one sentinel-"
                         "marked major-reject template")
        self.assertEqual(len(ends), 1)
        body = [l.replace("# ", "", 1)
                for l in lines[starts[0] + 1:ends[0]]]
        self.assertTrue(body, "template between the sentinels is empty")
        indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
        stanza = yaml.safe_load("\n".join(l[indent:] for l in body))
        self.assertIsInstance(stanza, dict)
        self.assertIn("ignore", stanza)
        assert_ignore_rules_reject_majors_per_dependency(
            self, "template", stanza)
        self.assertTrue(stanza["ignore"],
                        "template must show at least one example rule")


if __name__ == "__main__":
    unittest.main()
