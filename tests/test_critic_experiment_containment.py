"""DRE-3226: the critic's own experiments cannot delete the pipeline it runs on.

bureau-pipeline PR #279, 2026-09-05 15:46 PT (head `b78e519`). The QA critic
was doing what its charter asks — reverting files to `main` to prove the PR's
tests actually bite — and the experiment (a `git stash -u`, a `git clean -x`,
a checkout of a path that contains the nested clone) took
`.bureau-pipeline/` with it, because that checkout sat INSIDE the pull
request's own working tree. The post-review step then ran
`python3 .bureau-pipeline/scripts/check_critic_result.py` against a file that
no longer existed and the review died. The verdict comment read `QA Critic
could not run (infra error) — re-review needed`; the sweep re-dispatched once
and the second run approved, so nothing was lost that time. The class recurs
on any PR the critic experiments on.

Three things are pinned here, one per acceptance criterion:

  * the checkout the workflow RUNS FROM is outside `$GITHUB_WORKSPACE`, proved
    by executing the real relocation step and then running the very command
    that caused the incident (`git clean -xdf`) inside the working tree;
  * the post-review step, handed a checkout that is gone, SAYS SO in the
    verdict it posts instead of crashing;
  * the critic's brief names the two commands that are off limits in the repo
    root, each with its reason, and that brief reaches the critic.

Nothing here is asserted by grepping YAML alone: the relocation step and the
post step are executed as shell, the way the runner executes them.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
CRITIC_BRIEF = ROOT / "briefs" / "critic.md"

#: The checkout `actions/checkout` plants, and the ONE path a `uses: ./…`
#: local action can be resolved from (Actions resolves it under the workspace
#: and admits no expression in `uses:`), so the name still appears in the file.
IN_TREE = ".bureau-pipeline"


def _doc() -> dict:
    return yaml.safe_load(QA_REVIEW.read_text())


def _steps() -> list[dict]:
    return _doc()["jobs"]["review"]["steps"]


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"qa-review.yml has no step with id {step_id!r}")


def _index(predicate) -> int:
    for i, step in enumerate(_steps()):
        if predicate(step):
            return i
    raise AssertionError("no step matched")


def _relocation_step() -> dict:
    """The one step that exports PIPELINE_DIR for every later step."""
    found = [s for s in _steps()
             if "PIPELINE_DIR=" in (s.get("run") or "")
             and "GITHUB_ENV" in (s.get("run") or "")]
    assert len(found) == 1, (
        "exactly one step may export PIPELINE_DIR to $GITHUB_ENV — the card "
        f"asks for one absolute path exported once, found {len(found)}"
    )
    return found[0]


def _fake_checkout(root: Path) -> None:
    """What `actions/checkout` leaves behind, in the shape the steps read."""
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check_critic_result.py").write_text("print('gate')\n")
    (root / "scripts" / "linear_ops.py").write_text("print('linear')\n")
    actions = root / ".github" / "actions" / "install-claude-code"
    actions.mkdir(parents=True)
    (actions / "action.yml").write_text("name: install\nruns:\n  using: node20\n")


class PipelineCheckoutLivesOutsideTheWorkspaceTest(unittest.TestCase):
    """Criterion 1: the nested checkout is outside $GITHUB_WORKSPACE."""

    def test_the_relocation_step_puts_the_checkout_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            workspace = td / "work" / "portico" / "portico"
            runner_temp = td / "work" / "_temp"
            workspace.mkdir(parents=True)
            runner_temp.mkdir(parents=True)
            _fake_checkout(workspace / IN_TREE)

            env_file = td / "github_env"
            env_file.touch()
            script = td / "relocate.sh"
            script.write_text("set -euo pipefail\n" + _relocation_step()["run"])
            env = dict(os.environ)
            env.update({
                "GITHUB_WORKSPACE": str(workspace),
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_ENV": str(env_file),
                "GITHUB_OUTPUT": str(td / "github_output"),
            })
            proc = subprocess.run(["bash", str(script)], cwd=workspace, env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            exported = dict(
                line.split("=", 1)
                for line in env_file.read_text().splitlines() if "=" in line
            )
            self.assertIn("PIPELINE_DIR", exported,
                          "the step must export PIPELINE_DIR for later steps")
            pipeline_dir = Path(exported["PIPELINE_DIR"])
            self.assertTrue(pipeline_dir.is_absolute(),
                            f"PIPELINE_DIR must be absolute, got {pipeline_dir}")
            self.assertFalse(
                str(pipeline_dir).startswith(str(workspace) + os.sep)
                or pipeline_dir == workspace,
                f"{pipeline_dir} is inside the pull request's working tree "
                f"({workspace}) — a git command in the repo root can delete it",
            )
            self.assertTrue(
                (pipeline_dir / "scripts" / "check_critic_result.py").exists(),
                "the post-review gate's own script did not survive the move",
            )

    def test_the_incident_command_no_longer_takes_the_checkout(self):
        # The incident, reproduced: `git clean -xdf` in the repo root is one
        # of the three commands that removed the checkout on PR #279.
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            workspace = td / "work" / "portico" / "portico"
            runner_temp = td / "work" / "_temp"
            workspace.mkdir(parents=True)
            runner_temp.mkdir(parents=True)
            _fake_checkout(workspace / IN_TREE)
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

            env_file = td / "github_env"
            env_file.touch()
            script = td / "relocate.sh"
            script.write_text("set -euo pipefail\n" + _relocation_step()["run"])
            env = dict(os.environ)
            env.update({
                "GITHUB_WORKSPACE": str(workspace),
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_ENV": str(env_file),
                "GITHUB_OUTPUT": str(td / "github_output"),
            })
            proc = subprocess.run(["bash", str(script)], cwd=workspace, env=env,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            pipeline_dir = Path(dict(
                line.split("=", 1)
                for line in env_file.read_text().splitlines() if "=" in line
            )["PIPELINE_DIR"])

            subprocess.run(["git", "clean", "-xdf"], cwd=workspace, check=True,
                           capture_output=True)
            self.assertTrue(
                (pipeline_dir / "scripts" / "check_critic_result.py").exists(),
                "a `git clean -xdf` in the PR's working tree still deletes the "
                "script the post-review step runs — DRE-3226 is not fixed",
            )

    def test_no_step_runs_a_pipeline_script_from_inside_the_working_tree(self):
        offenders = []
        for i, step in enumerate(_steps()):
            run = step.get("run") or ""
            # The comments in this file quote the old path when they explain
            # the move; only executable lines count.
            for line in run.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r"(?<![\w/$])\.bureau-pipeline/scripts/", line):
                    offenders.append(
                        f"step {i} ({step.get('name') or step.get('id')}): {line.strip()}")
        self.assertEqual(
            offenders, [],
            "every pipeline script must be run out of $PIPELINE_DIR, which is "
            "outside the PR's working tree:\n" + "\n".join(offenders),
        )

    def test_nothing_after_the_last_agent_step_touches_the_in_tree_copy(self):
        # Everything the critic's experiments could reach must already have
        # been read by the time the agent runs. The gates, the verdict and the
        # check run are all downstream of it and may depend on nothing there.
        last_agent = max(
            i for i, s in enumerate(_steps())
            if str(s.get("uses") or "").startswith("anthropics/claude-code-action@")
        )
        offenders = []
        for i, step in enumerate(_steps()[last_agent + 1:], start=last_agent + 1):
            body = yaml.safe_dump(step)
            for line in body.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                if IN_TREE in line:
                    offenders.append(
                        f"step {i} ({step.get('name') or step.get('id')}): {line.strip()}")
        self.assertEqual(
            offenders, [],
            "a post-review step still reads the checkout inside the PR's "
            "working tree:\n" + "\n".join(offenders),
        )

    def test_the_relocation_happens_before_the_first_script_use(self):
        relocation = _relocation_step()
        order = _steps()
        reloc_i = order.index(relocation)
        checkout_i = _index(
            lambda s: str(s.get("uses") or "").startswith("actions/checkout")
            and (s.get("with") or {}).get("repository")
            == "dreadnought-foundry/bureau-pipeline"
        )
        self.assertLess(checkout_i, reloc_i,
                        "the checkout must happen before it is moved")
        uses = [
            i for i, s in enumerate(order)
            if i != reloc_i
            and any("$PIPELINE_DIR" in line
                    for line in (s.get("run") or "").splitlines()
                    if not line.lstrip().startswith("#"))
        ]
        self.assertTrue(uses, "nothing reads $PIPELINE_DIR")
        self.assertLess(reloc_i, min(uses),
                        "PIPELINE_DIR is used before it is exported")


class PostStepSurvivesAMissingCheckoutTest(unittest.TestCase):
    """Criterion 2: the post-review step says the checkout was missing."""

    def _run_post(self, pipeline_dir: str):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            bin_dir = td / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                f'printf "gh %s\\n" "$*" >> {td / "gh.log"}\n'
                "exit 0\n"
            )
            gh.chmod(0o755)

            run = _step("post")["run"]
            for expr, value in {
                "github.repository": "dreadnought-foundry/bureau-pipeline",
                "github.run_id": "17252880151",
            }.items():
                run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
            self.assertNotIn("${{", run,
                             "an unresolved GitHub expression reached the shell")
            run = run.replace("/tmp/", str(td) + "/")
            script = td / "post.sh"
            script.write_text("set -euo pipefail\n" + run)

            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env.update({
                "CARD": "DRE-3226", "REAL": "false", "PR": "279",
                "REVIEWED_SHA": "b" * 40, "CONTENT_ID": "",
                "MODEL_ID": "claude-opus-5", "MODEL_WHY": "advisory ladder top",
                # The gates ran against a checkout that was not there, so they
                # recorded nothing — exactly the state PR #279 was in.
                "A1_OUTCOME": "", "A1_TURNS": "", "A1_COST": "",
                "A2_OUTCOME": "", "A2_TURNS": "", "A2_COST": "",
                "PIPELINE_DIR": pipeline_dir,
            })
            proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                                  capture_output=True, text=True)
            comment = td / "qa-comment.md"
            body = comment.read_text() if comment.exists() else ""
            log = (td / "gh.log").read_text() if (td / "gh.log").exists() else ""
            return proc, body, log

    def setUp(self):
        with tempfile.TemporaryDirectory() as raw:
            gone = str(Path(raw) / "bureau-pipeline")
        self.proc, self.body, self.log = self._run_post(gone)

    def test_the_step_does_not_crash(self):
        self.assertEqual(self.proc.returncode, 0,
                         f"the post-review step died: {self.proc.stderr}")

    def test_the_verdict_says_the_checkout_was_missing(self):
        low = self.body.lower()
        self.assertIn("checkout", low,
                      f"the comment never names the missing checkout: {self.body}")
        self.assertIn("missing", low, self.body)

    def test_it_does_not_blame_a_credential_or_claim_a_crash(self):
        # The wrong cause costs an operator a day of credential-hunting
        # (DRE-2465/DRE-2924). Nothing here was a credential.
        low = self.body.lower()
        for false_claim in ("startup/auth failure", "no inference",
                            "crashed twice"):
            self.assertNotIn(false_claim, low, self.body)

    def test_it_still_reaches_the_pull_request(self):
        self.assertIn("pr comment", self.log,
                      "the neutral status was composed and never posted")

    def test_merge_gate_reads_it_as_the_latest_word_and_not_as_approval(self):
        self.assertIn("QA Critic", self.body.splitlines()[0])
        self.assertNotIn("VERDICT:", self.body)


class CriticBriefNamesTheForbiddenCommandsTest(unittest.TestCase):
    """Criterion 3: the two commands and the reason, one sentence each."""

    def setUp(self):
        self.brief = CRITIC_BRIEF.read_text()

    def _sentence_with(self, needle: str) -> str:
        for sentence in re.split(r"(?<=[.;:])\s+", self.brief.replace("\n", " ")):
            if needle in sentence:
                return sentence
        raise AssertionError(
            f"briefs/critic.md never names {needle!r} — the critic cannot obey "
            f"a rule it was not given"
        )

    def test_git_stash_u_is_named_with_its_reason(self):
        sentence = self._sentence_with("git stash -u")
        self.assertRegex(
            sentence, r"(?i)untracked|checkout|pipeline",
            f"the rule gives no reason, so it reads as arbitrary: {sentence!r}",
        )

    def test_git_clean_x_is_named_with_its_reason(self):
        sentence = self._sentence_with("git clean -x")
        self.assertRegex(
            sentence, r"(?i)untracked|checkout|pipeline",
            f"the rule gives no reason, so it reads as arbitrary: {sentence!r}",
        )

    def test_experiments_are_confined_to_the_reviewed_repository(self):
        self.assertRegex(
            self.brief,
            r"(?is)experiment.{0,160}?(inside|within).{0,60}?(repo|repository|"
            r"working tree)",
            "the brief must say experiments run inside the PR's repo only",
        )

    def test_the_brief_reaches_the_critic(self):
        # A rule in a file nobody injects is a rule nobody follows: the
        # critic's context is assembled by the same script the workflow runs.
        # assemble_context.py reads its inputs from the pipeline checkout it
        # is run beside, so the harness stands one up the way the workflow
        # does — pointed at THIS tree, not at the one already on disk.
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / IN_TREE).symlink_to(ROOT)
            out = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "assemble_context.py"),
                 "assemble", "critic"],
                capture_output=True, text=True, check=True, cwd=td,
            ).stdout
        self.assertIn("git stash -u", out)
        self.assertIn("git clean -x", out)


if __name__ == "__main__":
    unittest.main()
