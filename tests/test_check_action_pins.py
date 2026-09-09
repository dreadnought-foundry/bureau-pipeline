"""RED-first tests for DRE-3418 — every third-party action pinned to a SHA.

2026-09-08 15:15–16:27 PT: Anthropic's `claude-code-action` floating `v1` tag
moved to v1.0.218, whose Claude Code installer leaves no launcher, and every
Claude-running job in the fleet died for 72 minutes (DRE-3416, upstream
anthropics/claude-code-action#1817). Nothing stood between the vendor's release
and production because the reusables floated on major tags — and a floating
major gives Dependabot nothing to bump, since a patch release moves the tag
silently.

A 40-char SHA pin turns every vendor release into a Dependabot PR that the
critic and the harness prove. `scripts/check_action_pins.py` is what stops a
floating tag from coming back, so this suite pins BOTH halves:

  * the checker's behaviour, against synthetic workflows written per violation
    class (a `@vN` tag, a SHA with no version comment, a short/non-hex ref) —
    each one must be named with its file and LINE, because the operator fixing
    it needs the line and nothing else;
  * the LIVE tree — the checker exits 0 over this repo's real workflows and
    composite actions, `.github/dependabot.yml` carries the DRE-2064 house
    major-ignore shape on its `github-actions` entry, and tests.yml runs the
    checker so a floating tag cannot return unnoticed.

The `dreadnought-foundry/*` exemption is deliberate and is asserted here by
name: those are our OWN reusable workflows riding our own `@stable`/`@main`
release channel (docs/self-hosting.md), which the harness already proves.
"""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_action_pins  # noqa: E402

CHECKER = ROOT / "scripts" / "check_action_pins.py"
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
TESTS_WORKFLOW = WORKFLOWS / "tests.yml"

MAJOR_IGNORE_TYPE = "version-update:semver-major"

# A real 40-char lowercase-hex commit sha, and a version comment in the shape
# Dependabot both reads and rewrites.
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def write_workflow(tmp: Path, name: str, body: str) -> Path:
    path = tmp / name
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


class ScanTest(unittest.TestCase):
    """The line-level reader: which `uses:` lines does the checker see?"""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_floating_major_tag_is_a_violation_naming_the_line(self):
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                steps:
                  - uses: actions/checkout@v7
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("w.yml:4", violations[0])
        self.assertIn("actions/checkout@v7", violations[0])

    def test_a_sha_pin_with_a_version_comment_is_clean(self):
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7.0.1
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_sha_pin_without_a_version_comment_is_a_violation(self):
        """Dependabot reads the version comment to know what to bump FROM; a
        bare SHA is pinned but frozen, and nothing proposes its successor."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA}
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("w.yml:4", violations[0])
        self.assertIn("version comment", violations[0])

    def test_a_bare_major_in_the_comment_is_not_a_version(self):
        """`# v7` is the floating tag written down — it names no release."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/checkout@{SHA} # v7
        """)
        self.assertEqual(len(check_action_pins.check_paths([path])), 1)

    def test_a_version_comment_may_carry_trailing_prose(self):
        """The live claude-code-action pin explains itself after the version
        (DRE-3416); Dependabot reads the leading version and ignores the rest."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: anthropics/claude-code-action@{SHA} # v1.0.217 — pinned DRE-3416
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_short_or_non_hex_ref_is_a_violation(self):
        for ref in ("3d3c42e", "main", "v7.0.1", "z" * 40):
            with self.subTest(ref=ref):
                path = write_workflow(self.tmp, "w.yml", f"""
                    jobs:
                      a:
                        steps:
                          - uses: actions/checkout@{ref} # v7.0.1
                """)
                self.assertEqual(len(check_action_pins.check_paths([path])), 1)

    def test_our_own_reusable_workflows_are_exempt_by_name(self):
        """dreadnought-foundry/* rides OUR release channel (@stable/@main,
        docs/self-hosting.md) — the harness proves it, so it is not a vendor."""
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                uses: dreadnought-foundry/bureau-pipeline/.github/workflows/plan.yml@main
              b:
                uses: dreadnought-foundry/bureau-pipeline/.github/actions/x@stable
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])
        self.assertEqual(check_action_pins.EXEMPT_OWNER, "dreadnought-foundry")

    def test_local_path_references_are_not_third_party(self):
        path = write_workflow(self.tmp, "w.yml", """
            jobs:
              a:
                steps:
                  - uses: ./.github/actions/setup-python-cached
                  - uses: ./.bureau-pipeline/.github/actions/setup-node-cached
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_commented_out_and_prose_uses_lines_are_ignored(self):
        """`tdd-commit-check/action.yml` documents a caller snippet in a
        comment block, and half the reusables discuss `uses:` in prose. Neither
        is a step, and flagging them would make the checker unfixable."""
        path = write_workflow(self.tmp, "w.yml", """
            # HOW CALLERS PIN THIS:
            #       - uses: actions/checkout@v5
            jobs:
              a:
                steps:
                  # a caller must pass the same ref it pins in `uses:`
                  - name: prose mentioning uses: actions/checkout@v7
                    run: echo hi
        """)
        self.assertEqual(check_action_pins.check_paths([path]), [])

    def test_a_subpath_action_is_pinned_at_its_repository_sha(self):
        """`actions/cache/restore` is a subdirectory of the actions/cache
        repository; the pin is that repository's commit."""
        path = write_workflow(self.tmp, "w.yml", f"""
            jobs:
              a:
                steps:
                  - uses: actions/cache/restore@{SHA} # v6.1.0
                  - uses: actions/cache/save@v6
        """)
        violations = check_action_pins.check_paths([path])
        self.assertEqual(len(violations), 1, violations)
        self.assertIn("actions/cache/save@v6", violations[0])


class CliTest(unittest.TestCase):
    """The exit codes the acceptance criteria are written against."""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECKER), *args],
            capture_output=True, text=True,
        )

    def test_exits_zero_on_this_branch(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_exits_one_naming_the_line_when_a_floating_tag_comes_back(self):
        """Put a single `@vN` back and the checker fails, naming the file and
        line. This is the acceptance criterion, exercised end to end."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            write_workflow(tmp, "regressed.yml", f"""
                jobs:
                  a:
                    steps:
                      - uses: actions/checkout@{SHA} # v7.0.1
                      - uses: actions/create-github-app-token@v3
            """)
            proc = self._run(str(tmp))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("regressed.yml:5", proc.stdout)
        self.assertIn("actions/create-github-app-token@v3", proc.stdout)

    def test_a_run_that_inspects_nothing_fails_rather_than_passing(self):
        """A vacuous run is the failure mode a pin checker cannot afford: point
        it at the wrong directory and silence must not read as a green."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = self._run(tmpdir)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no third-party", proc.stdout.lower())


class LiveTreeTest(unittest.TestCase):
    """The tree itself — the acceptance criteria, read off the real files."""

    def _yaml_files(self):
        return sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml"))

    def test_no_floating_tag_survives_anywhere_we_call_an_action(self):
        """The card's grep, as a test: only `dreadnought-foundry/*` may carry a
        symbolic ref. Composite actions under .github/actions are included —
        `actions/setup-python@v7` inside setup-python-cached floats exactly the
        way a workflow-level one does, and the same tag move reaches it."""
        offenders = []
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                if not check_action_pins.SHA_RE.fullmatch(ref.ref):
                    offenders.append(f"{path.name}:{ref.line} {ref.uses}")
        self.assertEqual(offenders, [], f"floating action refs remain: {offenders}")

    def test_every_pinned_line_carries_its_version_comment(self):
        pinned = 0
        missing = []
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                pinned += 1
                if not check_action_pins.VERSION_COMMENT_RE.match(ref.comment or ""):
                    missing.append(f"{path.name}:{ref.line} {ref.uses}")
        self.assertGreater(pinned, 100, "the live scan went vacuous")
        self.assertEqual(missing, [], f"pins with no version comment: {missing}")

    def test_the_claude_code_action_pin_from_dre_3416_is_preserved(self):
        """DRE-3416's pin is v1.0.217 and stays there until DRE-3417 — this
        card resolves every OTHER action at today's tag, not this one."""
        found = set()
        for path in self._yaml_files():
            for ref in check_action_pins.references(path):
                if ref.action == "anthropics/claude-code-action":
                    found.add(ref.ref)
        self.assertEqual(found, {"9c5ddab2e6d17b83ea679153b31f1d5f023cf636"})

    def test_tests_yml_runs_the_checker(self):
        """A guard nobody runs is a guard that has already failed."""
        doc = yaml.safe_load(TESTS_WORKFLOW.read_text())
        runs = [
            step.get("run", "")
            for job in doc["jobs"].values()
            for step in (job.get("steps") or [])
        ]
        self.assertTrue(
            any("check_action_pins.py" in r for r in runs),
            "tests.yml runs no action-pin check",
        )

    def test_the_checker_is_referenced_in_the_release_channel_doc(self):
        """A change that contradicts a document updates that document: the
        release-channel doc now states how vendor actions reach the fleet."""
        doc = (ROOT / "docs" / "self-hosting.md").read_text()
        self.assertIn("check_action_pins.py", doc)
        self.assertRegex(doc, r"Dependabot proposes, the harness proves")


class DependabotMajorIgnoreTest(unittest.TestCase):
    """DRE-2064's house shape, copied onto the github-actions entry.

    Not a new policy: agent-bureau's `.github/dependabot.yml` already runs it
    and this repo's `docs/dependabot-major-rejection.md` describes the template.
    A SHA pin makes every vendor release a PR, so without this a major arrives
    weekly and burns a critic review per rung of the walk-down.
    """

    def _github_actions_entry(self):
        doc = yaml.safe_load(DEPENDABOT.read_text())
        entries = {u["package-ecosystem"]: u for u in doc["updates"]}
        self.assertIn("github-actions", entries)
        return entries["github-actions"]

    def test_majors_are_ignored_for_every_dependency(self):
        entry = self._github_actions_entry()
        ignore = entry.get("ignore")
        self.assertIsInstance(ignore, list, "github-actions entry carries no ignore:")
        self.assertIn(
            {"dependency-name": "*", "update-types": [MAJOR_IGNORE_TYPE]},
            [dict(rule) for rule in ignore],
            f"the DRE-2064 house major-ignore shape is missing: {ignore}",
        )

    def test_the_entry_stays_weekly_and_grouped_minor_plus_patch(self):
        entry = self._github_actions_entry()
        self.assertEqual(entry.get("schedule", {}).get("interval"), "weekly")
        groups = entry.get("groups") or {}
        self.assertEqual(len(groups), 1, groups)
        (group,) = groups.values()
        self.assertEqual(group.get("patterns"), ["*"])
        self.assertEqual(sorted(group.get("update-types") or []), ["minor", "patch"])


class PinLedgerTest(unittest.TestCase):
    """Every distinct action resolves to exactly ONE sha across the tree.

    Two shas for one action is the drift a grouped Dependabot bump leaves when
    it misses a file — and the missed one is the one still running last week's
    vendor code.
    """

    def test_one_sha_per_action(self):
        seen = {}
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml")):
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                seen.setdefault(ref.action, set()).add(ref.ref)
        drifted = {a: sorted(s) for a, s in seen.items() if len(s) > 1}
        self.assertEqual(drifted, {}, f"one action pinned at two shas: {drifted}")

    def test_the_version_comment_agrees_across_every_copy_of_an_action(self):
        seen = {}
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml")):
            for ref in check_action_pins.references(path):
                if ref.exempt or ref.local:
                    continue
                version = check_action_pins.VERSION_COMMENT_RE.match(ref.comment or "")
                if version:
                    seen.setdefault(ref.action, set()).add(version.group(1))
        drifted = {a: sorted(s) for a, s in seen.items() if len(s) > 1}
        self.assertEqual(drifted, {}, f"one action, two version comments: {drifted}")


if __name__ == "__main__":
    unittest.main()
