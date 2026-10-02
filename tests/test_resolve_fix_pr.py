"""DRE-5224: agent-fix.yml's `Resolve PR, mode, and attempt budget` runs scripts/resolve_fix_pr.sh.

The step's shell moved out of the workflow into its own file, mechanically
(`scripts/step_shell.py move`, DRE-5380), and its seven `${{ }}` expressions
became two env inputs, PR_NUMBER and REPO. The suites that walk its branches
read it through `step_shell` (DRE-5222). This file pins the move itself: the
step delegates by the one line the contract allows, it keeps every input it
had plus the two the move adds, the script opens the way a `run:` block runs,
its header says what it does before what happened to it — and the file runs
as a file.

The end-to-end case runs `bash scripts/resolve_fix_pr.sh` by path, with no
rewriting, against the `gh` stub tests/test_hand_dispatch_no_work.py uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENT_FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"
SCRIPT = ROOT / "scripts" / "resolve_fix_pr.sh"
STEP = "Resolve PR, mode, and attempt budget"
DELEGATION = "bash .bureau-pipeline/scripts/resolve_fix_pr.sh"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import step_shell  # noqa: E402
from test_hand_dispatch_no_work import ATTEMPT, GH_STUB, WORKER, rest  # noqa: E402

#: The step's inputs: the five it had on `main` before the move, then the
#: two the move adds in place of its seven expressions.
ENV_KEYS = (
    "GH_TOKEN", "WRITER_TOKEN", "WORKER_LOGIN", "EVENT_NAME", "TRIGGERING_ACTOR",
    "PR_NUMBER", "REPO",
)

#: What the two new inputs carry — the expressions the block interpolated.
NEW_ENV = {
    "PR_NUMBER": "${{ github.event.issue.number || github.event.inputs.pr_number }}",
    "REPO": "${{ github.repository }}",
}

#: Every card the block's comments named on `main`; the move keeps them all.
REFERENCES = (
    "DRE-1927", "DRE-1988", "DRE-1995", "DRE-1996", "DRE-2024", "DRE-2053",
    "DRE-2316", "DRE-2409", "DRE-2548", "DRE-2813", "DRE-2817", "DRE-3451",
    "DRE-4109", "DRE-4139", "DRE-4157", "DRE-4407", "DRE-4848",
)


def _step() -> dict:
    """The step as the workflow file itself spells it — never read through."""
    doc = yaml.safe_load(AGENT_FIX.read_text())
    found = [
        step for step in doc["jobs"]["fix"]["steps"]
        if isinstance(step, dict) and step.get("name") == STEP
    ]
    if len(found) != 1:
        raise AssertionError(f"agent-fix.yml has {len(found)} steps named {STEP!r}")
    return found[0]


class TheStepDelegatesTest(unittest.TestCase):
    def test_run_is_the_delegation_line(self):
        run = _step()["run"]
        self.assertEqual(run.strip(), DELEGATION)
        self.assertEqual(step_shell.delegated_script(run), "scripts/resolve_fix_pr.sh")

    def test_it_keeps_every_input_and_adds_the_two(self):
        env = _step().get("env") or {}
        self.assertEqual([k for k in ENV_KEYS if k not in env], [])
        for key, value in NEW_ENV.items():
            self.assertEqual(env[key], value)

    def test_it_keeps_its_id_and_condition(self):
        step = _step()
        self.assertEqual(step.get("id"), "pr")
        self.assertEqual(step.get("if"), "steps.decision.outputs.start != 'skip'")


class TheScriptFileTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), f"{SCRIPT.relative_to(ROOT)} is missing")
        self.text = SCRIPT.read_text()

    def test_it_opens_the_way_a_run_block_runs(self):
        self.assertEqual(self.text.splitlines()[:2], ["#!/usr/bin/env bash", "set -e"])

    def test_it_holds_no_expression(self):
        self.assertNotIn("${{", self.text)

    def test_what_it_does_comes_before_its_history(self):
        does = self.text.find("# What this script does")
        history = self.text.find("# Incident history")
        self.assertNotEqual(does, -1, "no 'What this script does' section")
        self.assertNotEqual(history, -1, "no 'Incident history' section")
        self.assertLess(does, history)
        first_code = step_shell.code_lines(self.text)[0]
        self.assertLess(history, self.text.find(first_code), "the header sits above the first code line")

    def test_the_header_names_every_input(self):
        header = self.text[:self.text.find(step_shell.code_lines(self.text)[0])]
        self.assertEqual([k for k in ENV_KEYS if k not in header], [])

    def test_every_card_reference_survives(self):
        self.assertEqual([ref for ref in REFERENCES if ref not in self.text], [])


class TheFileRunsAsAFileTest(unittest.TestCase):
    """A plain open agent pull request, one attempt in, resolved by the file itself."""

    def test_an_open_agent_pr_goes(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "bin").mkdir()
            stub = td / "bin" / "gh"
            stub.write_text(GH_STUB)
            stub.chmod(0o755)
            # The pipeline checkout the job takes before this step.
            (td / ".bureau-pipeline").symlink_to(ROOT)
            info = td / "pr-info.json"
            info.write_text(json.dumps({
                "state": "OPEN", "headRefName": "agent/DRE-2721-x",
                "headRefOid": "d9f2c1ab" + "0" * 32, "mergeStateStatus": "CLEAN",
            }))
            comments = td / "comments.json"
            comments.write_text(json.dumps([rest(WORKER, ATTEMPT.format(n=1))]))
            out_file = td / "step-output"
            out_file.write_text("")
            log = td / "gh-writes.jsonl"
            proc = subprocess.run(
                ["bash", str(SCRIPT)], cwd=td,
                env=dict(
                    os.environ,
                    PATH=f"{td}/bin:{os.environ['PATH']}",
                    GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                    GH_PR_INFO=str(info), GH_COMMENTS=str(comments), GH_LOG=str(log),
                    GH_TOKEN="test", WRITER_TOKEN="test-writer",
                    WORKER_LOGIN=WORKER, EVENT_NAME="issue_comment",
                    TRIGGERING_ACTOR="agent-bureau-qa-bot[bot]",
                    PR_NUMBER="199", REPO="dreadnought-foundry/bureau-pipeline",
                ),
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs = dict(
                line.partition("=")[::2]
                for line in out_file.read_text().splitlines() if "=" in line
            )
            posted = log.read_text() if log.exists() else ""
        self.assertEqual(outputs["go"], "true")
        self.assertEqual(outputs["number"], "199")
        self.assertEqual(outputs["card"], "DRE-2721")
        self.assertEqual(outputs["mode"], "fix")
        self.assertEqual(outputs["attempt"], "2")
        self.assertEqual(posted, "")


if __name__ == "__main__":
    unittest.main()
