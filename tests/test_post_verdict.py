"""DRE-5384: qa-review.yml's `Post verdict or neutral status` runs scripts/post_verdict.sh.

The step's shell moved out of the workflow into its own file, mechanically
(`scripts/step_shell.py move`, DRE-5380), and the suites that walk its
branches read it through `step_shell` (DRE-5382). This file pins the move
itself: the step delegates by the one line the contract allows, it keeps
every input it had, the script opens the way a `run:` block runs, its header
says what it does before what happened to it — and the file runs as a file.

The end-to-end case runs `bash scripts/post_verdict.sh` by path, with no
rewriting, so it reads and writes the fixed `/tmp/qa-*.md` paths the
workflow uses. Whatever stood at those paths before the test is put back.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
SCRIPT = ROOT / "scripts" / "post_verdict.sh"
STEP = "Post verdict or neutral status"
DELEGATION = 'bash "$PIPELINE_DIR/scripts/post_verdict.sh"'

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import step_shell  # noqa: E402
from test_verdict_evidence_scenario import APPROVE, _gate, _gh_stub  # noqa: E402

#: The step's inputs on `main` before the move, every one of which the
#: script still reads from its environment.
ENV_KEYS = (
    "GH_TOKEN", "LINEAR_API_KEY", "PR", "CARD", "REPO", "REVIEWED_SHA",
    "CONTENT_ID", "REAL", "A1_OUTCOME", "A1_TURNS", "A1_COST", "A2_OUTCOME",
    "A2_TURNS", "A2_COST", "A1_CAUSE_TEXT", "A2_CAUSE_TEXT", "A1_EVIDENCE",
    "A2_EVIDENCE", "BODY_READ_AT", "MODEL_ID", "MODEL_WHY", "STRATEGY",
    "OVERSIZE_MESSAGE", "REFUTED", "INSTALL_FAILED", "A1_ELAPSED",
    "A2_ELAPSED", "BACKOFF_OUTCOME",
)

#: The fixed paths the script reads and writes.
TMP_FILES = ("qa-verdict.md", "qa-comment.md", "qa-linear.md", "qa-evidence-hold.md")


def _step() -> dict:
    """The step as the workflow file itself spells it — never read through."""
    doc = yaml.safe_load(QA_REVIEW.read_text())
    found = [
        step for step in doc["jobs"]["review"]["steps"]
        if isinstance(step, dict) and step.get("name") == STEP
    ]
    if len(found) != 1:
        raise AssertionError(f"qa-review.yml has {len(found)} steps named {STEP!r}")
    return found[0]


class TheStepDelegatesTest(unittest.TestCase):
    def test_run_is_the_delegation_line(self):
        run = _step()["run"]
        self.assertEqual(run.strip(), DELEGATION)
        self.assertEqual(step_shell.delegated_script(run), "scripts/post_verdict.sh")

    def test_it_keeps_every_input(self):
        env = _step().get("env") or {}
        self.assertEqual([k for k in ENV_KEYS if k not in env], [])

    def test_it_keeps_its_id_and_condition(self):
        step = _step()
        self.assertEqual(step.get("id"), "post")
        self.assertEqual(step.get("if"), "always() && !cancelled() && steps.decide.outputs.review == 'true'")


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


class TheFileRunsAsAFileTest(unittest.TestCase):
    """One approved verdict, through the real gates, posted by the file itself."""

    def setUp(self):
        saved = {}
        for name in TMP_FILES:
            path = Path("/tmp") / name
            saved[name] = path.read_bytes() if path.exists() else None
            path.unlink(missing_ok=True)

        def restore():
            for name, data in saved.items():
                path = Path("/tmp") / name
                path.unlink(missing_ok=True)
                if data is not None:
                    path.write_bytes(data)

        self.addCleanup(restore)

    def test_an_approve_posts_bound_to_the_reviewed_head(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            outputs = _gate(td, APPROVE)
            self.assertEqual(outputs["real"], "true")
            Path("/tmp/qa-verdict.md").write_text(APPROVE)
            bin_dir = _gh_stub(td)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env.update({
                "PIPELINE_DIR": str(ROOT),
                "CARD": "", "PR": "2247", "REPO": "dreadnought-foundry/agent-bureau",
                "REAL": outputs["real"],
                "REVIEWED_SHA": "f" * 40, "CONTENT_ID": "e" * 64,
                "MODEL_ID": "claude-opus-5", "MODEL_WHY": "advisory ladder top",
                "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
                "A1_OUTCOME": outputs.get("outcome", ""),
                "A1_TURNS": outputs.get("turns", ""),
                "A1_COST": outputs.get("cost", ""),
                "A1_EVIDENCE": outputs.get("evidence", ""),
            })
            proc = subprocess.run(
                ["bash", str(SCRIPT)], cwd=td, env=env,
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        comment = Path("/tmp/qa-comment.md").read_text()
        self.assertEqual(
            comment.splitlines()[0],
            f"🔎 QA Critic — VERDICT: APPROVE @{'f' * 40} content:{'e' * 64}",
        )
        self.assertIn("It does what the card asked.", comment)


if __name__ == "__main__":
    unittest.main()
