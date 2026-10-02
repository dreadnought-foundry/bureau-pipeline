"""merge-gate.yml's `Evaluate and merge` runs scripts/evaluate_and_merge.sh (DRE-5383).

The step's 500-line `run: |` block moved into a file of its own, through
`scripts/step_shell.py move` (DRE-5380), and its comments were reorganized
into a header: what the script does today, then the incident history that
got it there. The behavior proof is the suites DRE-5222 and DRE-5381 pointed
at `step_shell`; this file proves the move itself:

  - the step's whole `run:` is the delegation line, so putting an inline
    block back fails here;
  - the step's `env:` still carries every input the script reads;
  - the script opens with the two lines the move contract fixes, carries no
    expression for Actions to fill in, and its header says what it does
    before it says how it got there;
  - the file runs AS A FILE, by path, end to end — one draft pull request,
    with the stubs tests/test_merge_gate_draft_scenario.py already uses.

Run: python3 -m pytest tests/test_evaluate_and_merge.py -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
SCRIPT = ROOT / "scripts" / "evaluate_and_merge.sh"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import step_shell  # noqa: E402
from test_merge_gate_draft_scenario import (  # noqa: E402
    BRANCH, DRAFT_REFUSAL, GH_STUB, HEAD, HUMAN_MARK, PR, QA_LOGIN, REPO,
    WORKER_LOGIN,
)

DELEGATION = "bash .bureau-pipeline/scripts/evaluate_and_merge.sh"
ENV_KEYS = ("GH_TOKEN", "LINEAR_API_KEY", "PR", "REPO_FULL", "WORKFLOW_TOKEN", "QA_LOGIN")

# Every card the block carried on `main` at db6adf6, 2026-10-01. `step_shell
# verify` checks the references against whatever `main` carries; this list
# keeps the snapshot the card was written against.
REFERENCES = (
    "DRE-1927", "DRE-1990", "DRE-1992", "DRE-1994", "DRE-2037", "DRE-2039",
    "DRE-2056", "DRE-2117", "DRE-2121", "DRE-2340", "DRE-2416", "DRE-2426",
    "DRE-2508", "DRE-2681", "DRE-2777", "DRE-2810", "DRE-3130", "DRE-3138",
    "DRE-3467", "DRE-3879", "DRE-4103", "DRE-4139", "DRE-4341", "DRE-4407",
    "DRE-4486", "DRE-4912", "DRE-5045", "DRE-5070", "DRE-5228", "DRE-5230",
)


def the_step() -> dict:
    """The step as the workflow file spells it — never read through."""
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"]["steps"]
    found = [s for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(found) == 1, "expected exactly one 'Evaluate and merge' step"
    return found[0]


def header() -> list[str]:
    """The script's lines from line 3 up to its first code line."""
    lines = SCRIPT.read_text().splitlines()
    first_code = step_shell.code_lines(SCRIPT.read_text())[0]
    return lines[2:lines.index(first_code)]


class TheStepDelegatesTest(unittest.TestCase):
    def test_the_run_is_exactly_the_delegation_line(self):
        run = the_step()["run"]
        self.assertEqual(run.strip(), DELEGATION, "the step runs an inline block again")
        self.assertEqual(step_shell.delegated_script(run), "scripts/evaluate_and_merge.sh")

    def test_the_env_carries_every_input_the_script_reads(self):
        env = the_step().get("env") or {}
        for key in ENV_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, env)


class TheScriptFileTest(unittest.TestCase):
    def test_it_opens_with_the_shebang_and_set_e(self):
        self.assertEqual(SCRIPT.read_text().splitlines()[:2], ["#!/usr/bin/env bash", "set -e"])

    def test_it_carries_no_expression_for_actions_to_fill_in(self):
        """Actions fills in `${{ }}` only inside the workflow file; in a
        script it would reach bash as a bad substitution."""
        self.assertNotIn("${{", SCRIPT.read_text())

    def test_the_body_keeps_its_own_strict_mode(self):
        self.assertEqual(step_shell.code_lines(SCRIPT.read_text())[0], "set -euo pipefail")

    def test_the_header_says_what_it_does_before_its_history(self):
        text = "\n".join(header())
        self.assertIn("What this script does", text)
        self.assertIn("Incident history", text)
        self.assertLess(text.index("What this script does"), text.index("Incident history"))

    def test_the_header_is_comments_only(self):
        lines = header()
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(not line.strip() or line.startswith("#"), line)

    def test_the_header_names_each_env_input(self):
        what = "\n".join(header()).split("Incident history")[0]
        for key in ENV_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, what)

    def test_every_history_reference_survives_in_the_header(self):
        history = "\n".join(header()).split("Incident history", 1)[1]
        for ref in REFERENCES:
            with self.subTest(ref=ref):
                self.assertRegex(history, re.escape(ref) + r"(?!\d)")


class TheFileRunsAsAFileTest(unittest.TestCase):
    """`bash scripts/evaluate_and_merge.sh`, by path, against a draft PR — the
    scenario of tests/test_merge_gate_draft_scenario.py (DRE-3467), with its
    stub `gh`. The gate decides `human`, leaves one note and never merges."""

    @classmethod
    def setUpClass(cls):
        fixture = {
            "pr": {
                "headRefName": BRANCH, "state": "OPEN", "mergeStateStatus": "DRAFT",
                "isDraft": True, "headRefOid": HEAD, "baseRefName": "main",
            },
            "check_runs": [
                {"name": "scripts unit tests", "status": "completed",
                 "conclusion": "success", "check_suite": {"id": 1}},
            ],
            "compare": {"status": "ahead", "files": [{"filename": "a.py"}]},
            "pr_commits": [{"sha": HEAD}],
            "author": WORKER_LOGIN,
        }
        seed = [{"id": 1, "user": {"login": QA_LOGIN},
                 "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"}]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "bin").mkdir()
            stub = td / "bin" / "gh"
            stub.write_text(GH_STUB)
            stub.chmod(0o755)  # nosec B103 — a test stub on PATH
            os.symlink(ROOT, td / ".bureau-pipeline")
            (td / "fixture.json").write_text(json.dumps(fixture))
            (td / "comments.json").write_text(json.dumps(seed))
            (td / "gh.log").write_text("")
            cls.proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own script
                ["bash", str(SCRIPT)],
                cwd=td, capture_output=True, text=True,
                env={
                    **os.environ,
                    "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "PR": str(PR),
                    "GH_TOKEN": "qa-token",
                    "LINEAR_API_KEY": "test-key",
                    "REPO_FULL": REPO,
                    "WORKFLOW_TOKEN": "workflow-token",
                    "QA_LOGIN": QA_LOGIN,
                    "FIXTURE": str(td / "fixture.json"),
                    "COMMENTS": str(td / "comments.json"),
                    "GH_LOG": str(td / "gh.log"),
                    "MERGE_ERROR": DRAFT_REFUSAL,
                },
            )
            cls.calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
            cls.comments = json.loads((td / "comments.json").read_text())

    def explain(self) -> str:
        return f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}"

    def test_it_exits_clean(self):
        self.assertEqual(self.proc.returncode, 0, self.explain())

    def test_it_reached_the_decision_and_held_for_a_person(self):
        self.assertIn("decision=human", self.proc.stdout, self.explain())

    def test_it_never_calls_the_merge_command(self):
        self.assertEqual([c for c in self.calls if c[:2] == ["pr", "merge"]], [], self.explain())

    def test_it_leaves_one_note_on_the_pull_request(self):
        notes = [c["body"] for c in self.comments if HUMAN_MARK in c["body"]]
        self.assertEqual(len(notes), 1, self.comments)


if __name__ == "__main__":
    unittest.main()
