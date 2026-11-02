"""The fix agent starts with the parts catalog (DRE-8735, rule 7 of DRE-8708).

`agent-fix.yml`'s `Assemble fix context` step appends what
`design_parts.py catalog` (DRE-8717) prints to `.bureau-pipeline/agent-context.md`,
read at the root of the pull request's own checkout, for every fix run whatever
the card's role: a fix for one finding can write UI code on any card.

The step's block runs here as written, against the fixture repos DRE-8717
ships in `tests/fixtures/design-parts/`, with only `assemble_context.py`
stubbed. The step never fails on the catalog: with the script missing, the
context carries a `PARTS CATALOG: UNKNOWN — …` line in its place.

Run: python3 -m pytest tests/test_fix_parts_catalog_wiring.py -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import design_parts  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "design-parts")
STEP = "Assemble fix context"

#: What the stubbed assembler writes, so the catalog is seen to follow it.
ASSEMBLED = "# assembled fix context (stub)"


def step() -> dict:
    with open(WORKFLOW, encoding="utf-8") as fh:
        steps = yaml.safe_load(fh)["jobs"]["fix"]["steps"]
    found = [s for s in steps if s.get("name") == STEP]
    assert len(found) == 1, f"expected one {STEP!r} step in agent-fix.yml"
    return found[0]


def run_step(fixture: str, *, catalog_script: bool = True):
    """Run the step's block at the root of a copy of `fixture`. Returns
    (proc, the assembled context)."""
    run = step()["run"]
    assert "${{" not in run, "the step's block carries an expression the harness cannot fill"
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        shutil.copytree(os.path.join(FIXTURES, fixture), repo)
        scripts = os.path.join(repo, ".bureau-pipeline", "scripts")
        os.makedirs(scripts)
        with open(os.path.join(scripts, "assemble_context.py"), "w", encoding="utf-8") as fh:
            fh.write(f"print({ASSEMBLED!r})\n")
        if catalog_script:
            shutil.copy(os.path.join(SCRIPTS, "design_parts.py"), scripts)
        # GitHub's own bash for a `run:` block.
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
                              cwd=repo, capture_output=True, text=True, timeout=60)
        path = os.path.join(repo, ".bureau-pipeline", "agent-context.md")
        context = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        return proc, context


def _cli(fixture: str) -> list:
    """What the catalog prints for `fixture`, run as its own CLI."""
    done = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "design_parts.py"), "catalog",
         "--repo-root", os.path.join(FIXTURES, fixture)],
        capture_output=True, text=True, timeout=60, check=True)
    return done.stdout.splitlines()


class TheDeclaredRepoTest(unittest.TestCase):

    def setUp(self):
        self.proc, self.context = run_step("declared")
        self.log = self.proc.stdout + self.proc.stderr

    def test_the_step_succeeds(self):
        self.assertEqual(0, self.proc.returncode, self.log)

    def test_the_catalog_follows_the_assembled_context(self):
        lines = self.context.splitlines()
        self.assertEqual(ASSEMBLED, lines[0])
        first = next(i for i, line in enumerate(lines) if line.startswith("PARTS CATALOG:"))
        self.assertEqual(
            "PARTS CATALOG: 6 part(s) from .github/bureau/design-system.json",
            lines[first])
        self.assertEqual(_cli("declared"), lines[first:first + 7])

    def test_every_part_is_listed_with_where_it_ships(self):
        for name, ships_at in (
                ("Badge", "web/src/components/atoms/Badge.vue"),
                ("Button", "web/src/components/atoms/Button.tsx"),
                ("Card", "web/src/components/atoms/Card/index.tsx"),
                ("SearchField", "web/src/components/molecules/SearchField.jsx"),
                ("Header", "web/src/components/organisms/Header.svelte"),
                ("PageShell", "web/src/components/templates/PageShell.tsx")):
            self.assertRegex(self.context,
                             rf"(?m)^- {name} \(\w+\) — ships at {ships_at}$")

    def test_the_log_carries_the_status_line_and_the_echoed_first_line(self):
        self.assertIn(
            f"{design_parts.TAG} 6 part(s) from .github/bureau/design-system.json",
            self.proc.stderr)
        self.assertIn(
            "PARTS CATALOG: 6 part(s) from .github/bureau/design-system.json\n",
            self.proc.stdout)


class TheRepoWithNoDesignSystemTest(unittest.TestCase):

    def test_the_context_carries_the_one_none_line(self):
        proc, context = run_step("bare")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        found = [line for line in context.splitlines() if line.startswith("PARTS CATALOG:")]
        self.assertEqual(1, len(found), context)
        self.assertTrue(found[0].startswith("PARTS CATALOG: none — "), found)
        self.assertEqual(_cli("bare"), found)


class TheMissingScriptTest(unittest.TestCase):

    def test_the_step_succeeds_and_says_unknown(self):
        proc, context = run_step("declared", catalog_script=False)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        lines = context.splitlines()
        self.assertEqual(ASSEMBLED, lines[0])
        found = [line for line in lines if line.startswith("PARTS CATALOG:")]
        self.assertEqual(1, len(found), context)
        self.assertTrue(found[0].startswith("PARTS CATALOG: UNKNOWN — "), found)
        self.assertIn(found[0], proc.stdout)


class EveryFixRunGetsItTest(unittest.TestCase):

    def test_the_step_is_gated_as_before_and_on_no_role(self):
        gate = step().get("if", "")
        self.assertEqual(
            "steps.pr.outputs.go == 'true' && steps.unfixable.outputs.escalate != 'true'",
            gate)
        self.assertIn("design_parts.py catalog", step()["run"])
        self.assertNotIn("--repo-root", step()["run"])


if __name__ == "__main__":
    unittest.main()
