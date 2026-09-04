"""RED-first tests for DRE-3101: a pull request's harness run takes its
sandbox code from main.

Observed 2026-09-04, 10:00 PT onward. DRE-3075 gave every harness run a
namespace and taught `framework.sweep_leftovers` to collect only its own —
in `scripts/harness/`, which a `pull_request` run reads from the PR's branch.
Three PR branches cut before that merge (DRE-3091, DRE-3097, DRE-3098) still
ran the old sweep, each deleted every harness branch and closed every probe
PR in the shared sandbox, main's included, and no main proving run finished
for three hours while `stable` sat ten merges behind. The namespace rule is
only as good as the oldest branch that runs it.

So a PR run replaces its `scripts/harness/` with main's copy unless the PR
itself changes that directory (then the PR is testing the harness, and the
step says so). Two halves are pinned here:

  * **the wiring** — the step exists, runs only on `pull_request`, sits
    between the checkout and the scenarios, asks GitHub which files THIS PR
    changed (never a tree diff against main, which on an old branch is
    exactly the case that must be caught), and the workflow may read
    pull-request files;
  * **the shell** — run for real against a throwaway git repo with a fake
    `gh`: a PR that does not touch the harness ends up running main's copy,
    a PR that does keeps its own.

These tests must FAIL against harness.yml without the step, and PASS after.

Run: python3 -m pytest tests/test_harness_code_from_main.py -v
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
STEP = "Harness code from main (DRE-3101)"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _steps():
    return _doc()["jobs"]["harness"]["steps"]


def _index(fragment: str) -> int:
    for i, s in enumerate(_steps()):
        name = s.get("name") or s.get("uses") or ""
        if fragment.lower() in name.lower():
            return i
    raise AssertionError(f"no harness.yml step named like {fragment!r}")


def _step(fragment: str) -> dict:
    return _steps()[_index(fragment)]


class TheWiring(unittest.TestCase):
    def test_the_step_exists_and_runs_only_for_pull_requests(self):
        step = _step(STEP)
        self.assertEqual(step.get("if"), "github.event_name == 'pull_request'")

    def test_it_sits_between_the_checkout_and_the_scenarios(self):
        self.assertLess(_index("actions/checkout"), _index(STEP))
        self.assertLess(_index(STEP), _index("Run harness scenarios"))

    def test_it_asks_github_which_files_this_pr_changed(self):
        """A tree diff against main would flag every OLD branch as 'changed'
        — the one case this exists to catch — so the question is put to the
        pull request itself."""
        run = _step(STEP)["run"]
        self.assertIn("/files", run)
        self.assertIn("scripts/harness/", run)
        self.assertNotIn("git diff", run)

    def test_it_takes_the_harness_code_from_main_when_the_pr_leaves_it_alone(self):
        run = _step(STEP)["run"]
        self.assertIn("git checkout origin/main -- scripts/harness", run)
        self.assertIn("source=main", run)
        self.assertIn("source=pr", run)

    def test_the_workflow_may_read_pull_request_files(self):
        self.assertEqual(_doc()["permissions"].get("pull-requests"), "read")


class TheShell(unittest.TestCase):
    """The step's own shell, run against a throwaway repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = os.path.join(self.tmp, "origin")
        self.work = os.path.join(self.tmp, "work")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        self.env = env
        # origin: main carries the NEW harness; the PR branch was cut from the
        # OLD one and never touched scripts/harness/.
        self._git("init", "-q", "-b", "main", self.origin)
        harness = Path(self.origin, "scripts", "harness")
        harness.mkdir(parents=True)
        (harness / "framework.py").write_text("OLD = True  # un-namespaced sweep\n")
        Path(self.origin, "other.py").write_text("x = 1\n")
        self._git("-C", self.origin, "add", "-A")
        self._git("-C", self.origin, "commit", "-q", "-m", "old harness")
        self._git("-C", self.origin, "checkout", "-q", "-b", "agent/DRE-9999-old-branch")
        Path(self.origin, "other.py").write_text("x = 2\n")
        self._git("-C", self.origin, "commit", "-q", "-am", "the PR's change")
        self._git("-C", self.origin, "checkout", "-q", "main")
        (harness / "framework.py").write_text("NEW = True  # namespaced sweep (DRE-3075)\n")
        self._git("-C", self.origin, "commit", "-q", "-am", "new harness on main")
        # the runner's checkout: the PR head, shallow, exactly like actions/checkout
        self._git("clone", "-q", "--depth", "1", "--branch", "agent/DRE-9999-old-branch",
                  self.origin, self.work)

    def _git(self, *args):
        subprocess.run(["git", *args], check=True, env=self.env,
                       capture_output=True, text=True)

    def _fake_gh(self, files):
        body = "\n".join(files)
        Path(self.bin, "gh").write_text(
            "#!/bin/bash\n" + (f"printf '%s\\n' {' '.join(repr(f) for f in files)}\n" if files else "true\n"))
        os.chmod(Path(self.bin, "gh"), 0o755)

    def _run_step(self):
        run = _step(STEP)["run"]
        env = dict(self.env, PATH=self.bin + os.pathsep + os.environ["PATH"],
                   GH_TOKEN="test", PR_NUMBER="9999",
                   GITHUB_REPOSITORY="dreadnought-foundry/bureau-pipeline",
                   GITHUB_OUTPUT=os.path.join(self.tmp, "out"))
        Path(env["GITHUB_OUTPUT"]).write_text("")
        out = subprocess.run(["bash", "-e", "-c", run], cwd=self.work,
                             capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout, Path(env["GITHUB_OUTPUT"]).read_text()

    def test_a_pr_that_leaves_the_harness_alone_runs_mains_copy(self):
        self._fake_gh(["other.py"])
        stdout, outputs = self._run_step()
        self.assertIn("NEW = True", Path(self.work, "scripts", "harness", "framework.py").read_text(),
                      "an old branch still ran its own un-namespaced sweep")
        self.assertIn("source=main", outputs)
        self.assertIn("harness code: main", stdout)
        # the PR's own change is untouched — only scripts/harness/ is swapped
        self.assertEqual(Path(self.work, "other.py").read_text(), "x = 2\n")

    def test_a_pr_that_changes_the_harness_keeps_its_own(self):
        self._fake_gh(["scripts/harness/framework.py", "other.py"])
        stdout, outputs = self._run_step()
        self.assertIn("OLD = True", Path(self.work, "scripts", "harness", "framework.py").read_text(),
                      "a PR testing the harness must run the code it is testing")
        self.assertIn("source=pr", outputs)
        self.assertIn("running the PR's copy", stdout)


if __name__ == "__main__":
    unittest.main()
