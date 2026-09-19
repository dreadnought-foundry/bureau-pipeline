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
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402

CONFIG = ROOT / ".github" / "dependabot.yml"
REQUIREMENTS = ROOT / "requirements-dev.txt"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
PLAYBOOK = ROOT / "docs" / "dependabot-major-rejection.md"

MERGEABLE_UPDATE_TYPES = {"minor", "patch"}
MAJOR_IGNORE_TYPE = "version-update:semver-major"
ALL_IGNORE_TYPES = {
    "version-update:semver-major",
    "version-update:semver-minor",
    "version-update:semver-patch",
}

WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"

# Deliberate vendor pins Dependabot must not propose past (DRE-4336) — the
# SECOND purpose an `ignore` stanza serves in this repo, beside rejecting
# majors. One row per hold: the dependency, the ecosystem entry it lives under,
# the sha the tree is held at, the card that placed the hold and the card that
# lifts it. The sha is here to answer "does the hold still stand?" off the live
# tree; the guards that ENFORCE the pin are tests/test_check_action_pins.py and
# its two siblings, and this file never replaces them.
HELD_PINS = {
    "anthropics/claude-code-action": {
        "ecosystem": "github-actions",
        "held_sha": "9c5ddab2e6d17b83ea679153b31f1d5f023cf636",  # v1.0.217
        "held_by": "DRE-3416",
        "lifted_by": "DRE-3417",
    },
}


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
        # Since DRE-2589 the install is the shared action's job, so the wiring
        # to assert is the manifest handed to it rather than a `-r` on a run
        # line. The invariant is unchanged: whatever installs must install
        # THIS file — a pin CI ignores makes every Dependabot pip PR vacuous.
        import yaml

        doc = yaml.safe_load(TESTS_WORKFLOW.read_text())
        callers = [
            step
            for job in doc["jobs"].values()
            for step in job.get("steps") or []
            if "setup-python-cached" in str(step.get("uses", ""))
        ]
        self.assertTrue(
            callers,
            "tests.yml no longer sets python up through the shared action, so "
            "nothing here guarantees the suite runs against the pinned manifest",
        )
        named = [
            c for c in callers
            if "requirements-dev.txt" in str((c.get("with") or {}).get("requirements", ""))
        ]
        self.assertTrue(
            named,
            f"no step installs from {REQUIREMENTS.name} — the suite would run "
            f"against whatever PyPI served this morning, so a Dependabot bump "
            f"of these pins would exercise nothing",
        )


def assert_ignore_rules_reject_majors_per_dependency(testcase, eco, update):
    """The shape the MAJOR-REJECT template must show (DRE-2118):
    per-dependency, majors only. Pasted as written, anything broader would
    silently swallow the minor/patch stream the grouped auto-merge lane
    depends on.

    This is no longer the only shape an ignore rule may take in the live
    config: a deliberate vendor pin is held by a rule with NO `update-types`,
    which is exactly the swallow-everything behaviour described above, wanted
    on purpose for one named dependency (DRE-4336). `HeldVendorPinTest` owns
    that shape and keeps it to the dependencies declared in `HELD_PINS`."""
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


def _ignores_every_update_type(rule):
    """True when a rule stops minors and patches as well as majors.

    Dependabot's documented semantics: an ignore rule with neither
    `update-types` nor `versions` ignores every update of that dependency.
    Listing all three update types says the same thing the long way. A
    `versions` key narrows the rule to a range, which is not a hold."""
    if "versions" in rule:
        return False
    if "update-types" not in rule:
        return True
    return set(rule["update-types"]) == ALL_IGNORE_TYPES


def _live_shas(action):
    """Every sha the live tree pins `action` at, read the way the pin guard
    reads it (scripts/check_action_pins.py)."""
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml"))
    return {
        ref.ref
        for path in paths
        for ref in check_action_pins.references(path)
        if ref.action == action
    }


class HeldVendorPinTest(unittest.TestCase):
    """DRE-4336 — a deliberate vendor pin is held in config, not by hoping.

    bureau-pipeline#452 (2026-09-19): the weekly github-actions sweep proposed
    `anthropics/claude-code-action` v1.0.217 -> v1.0.226 inside the grouped
    minor/patch PR. v1.0.217 is pinned ON PURPOSE (DRE-3416: the floating tag
    moved to v1.0.218 on 2026-09-08 and every Claude-running job in the fleet
    died for 72 minutes); lifting it is DRE-3417's job, with a live critic run
    as proof. Four guard tests went red by design, the critic answered
    REQUEST_CHANGES, and nothing in the fleet fixes a `dependabot/*` branch —
    so the PR could neither merge nor go away, and a plain close re-files it
    every week, dragging the safe half of each sweep down with it.

    The durable answer is the one docs/dependabot-major-rejection.md already
    names for a major: an `ignore` rule in `.github/dependabot.yml`. For a hold
    it carries NO `update-types`, because the bump being refused IS a patch.

    Asked in both directions so the rule cannot outlive its reason: while the
    tree is held at the recorded sha the rule must be there, and once the pin
    moves (DRE-3417) the rule must be gone — a leftover hold would freeze the
    action at whatever DRE-3417 chose, silently, for good.
    """

    def _rules_for(self, dependency, ecosystem):
        update = updates_by_ecosystem()[ecosystem]
        return [
            dict(rule) for rule in update.get("ignore") or []
            if rule.get("dependency-name") == dependency
        ]

    def test_the_live_scan_is_not_vacuous(self):
        for dependency in HELD_PINS:
            self.assertTrue(
                _live_shas(dependency),
                f"no `uses:` of {dependency} found — every test in this class "
                "would pass on an empty tree",
            )

    def test_a_held_pin_is_ignored_for_every_update_type_while_it_stands(self):
        for dependency, hold in HELD_PINS.items():
            if _live_shas(dependency) != {hold["held_sha"]}:
                continue  # the pin has moved; the next test owns that case
            rules = self._rules_for(dependency, hold["ecosystem"])
            self.assertEqual(
                len(rules), 1,
                f"{dependency} is held at {hold['held_sha'][:8]} by "
                f"{hold['held_by']}, but .github/dependabot.yml carries "
                f"{len(rules)} ignore rule(s) naming it — Dependabot will "
                "propose past the pin in every weekly sweep (bureau-pipeline"
                "#452) and the grouped PR can never merge",
            )
            self.assertTrue(
                _ignores_every_update_type(rules[0]),
                f"the hold on {dependency} must ignore EVERY update type — "
                f"the bump it refuses is a patch — but the rule is {rules[0]}",
            )

    def test_the_hold_is_removed_with_the_pin_it_protects(self):
        for dependency, hold in HELD_PINS.items():
            if _live_shas(dependency) == {hold["held_sha"]}:
                continue
            self.assertEqual(
                self._rules_for(dependency, hold["ecosystem"]), [],
                f"{dependency} no longer sits at {hold['held_sha'][:8]}, so "
                f"{hold['lifted_by']} has moved the pin — delete its ignore "
                "rule from .github/dependabot.yml and its HELD_PINS row in "
                "the same PR, or Dependabot never proposes this action again",
            )

    def test_every_live_ignore_rule_is_a_major_reject_or_a_declared_hold(self):
        """The two purposes, and no third. A rule without `update-types` on a
        dependency nobody declared here is the silent swallow DRE-2118 warned
        about; a bare rule with no `dependency-name` ignores the ecosystem."""
        for eco, update in updates_by_ecosystem().items():
            for rule in update.get("ignore") or []:
                name = rule.get("dependency-name")
                self.assertTrue(name, f"{eco}: ignore rule names no dependency: {rule}")
                if rule.get("update-types") == [MAJOR_IGNORE_TYPE]:
                    continue
                self.assertIn(
                    name, HELD_PINS,
                    f"{eco}: ignore rule for {name!r} is neither majors-only "
                    "nor a hold declared in HELD_PINS",
                )
                self.assertEqual(HELD_PINS[name]["ecosystem"], eco)
                self.assertTrue(_ignores_every_update_type(rule), rule)

    def test_the_house_major_ignore_survives_beside_the_hold(self):
        ignore = updates_by_ecosystem()["github-actions"].get("ignore") or []
        self.assertIn(
            {"dependency-name": "*", "update-types": [MAJOR_IGNORE_TYPE]},
            [dict(rule) for rule in ignore],
            "adding a hold must not displace the DRE-2064 major ignore",
        )

    def test_the_config_and_the_playbook_both_name_the_hold(self):
        """The stanza is only reversible if the next editor can find out why
        it is there and which card takes it away."""
        config = CONFIG.read_text()
        playbook = PLAYBOOK.read_text()
        for dependency, hold in HELD_PINS.items():
            if _live_shas(dependency) != {hold["held_sha"]}:
                continue
            for where, text in ((".github/dependabot.yml", config),
                                ("docs/dependabot-major-rejection.md", playbook)):
                for needle in (dependency, hold["held_by"], hold["lifted_by"]):
                    self.assertIn(needle, text, f"{where} never mentions {needle}")


if __name__ == "__main__":
    unittest.main()
