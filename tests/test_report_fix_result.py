"""agent-fix.yml's `Report` runs as a file: scripts/report_fix_result.sh (DRE-5225).

DRE-3488 moves the step's 22,799-character `run:` block into a script, and
the step keeps one line that calls it. The suites DRE-5221 and DRE-5222
pointed at `step_shell` prove the behavior survives the move; this file
proves the move itself. The step delegates and keeps every value it hands
the script, the script is shaped the way `step_shell` says a moved script
is, its header reads current behavior first and incident history second,
and the file runs end to end by its own path. That last part matters
because no other test calls the file directly.

The harness is tests/test_act_emission_scenario.py's, imported rather than
copied: `gh` records what it is asked to post and answers `pr view` with a
pushed head, `linear_ops.py` exits 0, this run's handoff is opened by the
real `fix_handoff.py`, and every other script in the checkout is the real
one.

Run: python3 -m pytest tests/test_report_fix_result.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
SCRIPT = os.path.join(ROOT, "scripts", "report_fix_result.sh")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pipeline_act  # noqa: E402
import step_shell  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    RECEIPTS,
    _checkout,
    _gh_stub,
    _open_handoff,
    _report_env,
    answers,
)

STEP = "Report"
DELEGATION = "bash .bureau-pipeline/scripts/report_fix_result.sh"

#: The step's `env:` on `main` at 9fe61d5, as the card lists it, and the
#: eight DRE-6350 wired for the Push rescue routes DRE-6351 reads.
ENV_KEYS = (
    "GH_TOKEN", "LINEAR_API_KEY", "CARD", "PRE_SHA", "DISPATCH_TOKEN",
    "CLASSIFICATION", "REPO", "PR", "ATTEMPT", "MODE", "EXEC_FILE", "RUN_URL",
    "RESCUE_LOCAL_WORK", "RESCUE_PUSHED", "RESCUE_PATCH", "RESCUE_ARTIFACT",
    "RESCUE_PUSH_STATUS", "RESCUE_ERROR", "RESCUE_TARGET_BRANCH", "RUN_ID",
)

#: The 21 card references the block carried on `main`; `verify` requires
#: every one of them in the script.
REFERENCES = (
    "DRE-1254", "DRE-1995", "DRE-2018", "DRE-2056", "DRE-2199", "DRE-2312",
    "DRE-2399", "DRE-2409", "DRE-2696", "DRE-2722", "DRE-2776", "DRE-2813",
    "DRE-2817", "DRE-2826", "DRE-3084", "DRE-3951", "DRE-4139", "DRE-4183",
    "DRE-4460", "DRE-4486", "DRE-4849",
)


def step() -> dict:
    for entry in yaml.safe_load(open(WORKFLOW))["jobs"]["fix"]["steps"]:
        if entry.get("name") == STEP:
            return entry
    raise AssertionError(f"{STEP!r} is gone from agent-fix.yml")


def script_text() -> str:
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


class TheStepDelegates(unittest.TestCase):

    def test_the_run_is_the_delegation_line(self):
        """Put the inline block back and this goes red."""
        self.assertEqual(DELEGATION, step()["run"].strip())
        self.assertEqual("scripts/report_fix_result.sh",
                         step_shell.delegated_script(step()["run"]))

    def test_the_env_still_carries_every_value_the_script_reads(self):
        missing = [k for k in ENV_KEYS if k not in (step().get("env") or {})]
        self.assertEqual([], missing)

    def test_the_step_keeps_its_condition(self):
        self.assertEqual(
            "steps.pr.outputs.go == 'true' && "
            "steps.unfixable.outputs.escalate != 'true' && always()",
            step()["if"])

    def test_the_step_sets_no_shell(self):
        """The script's `set -e` matches the runner's `bash -e {0}` only
        while the step sets no `shell:` of its own."""
        self.assertNotIn("shell", step())


class TheScriptFile(unittest.TestCase):

    def test_it_opens_with_the_two_required_lines(self):
        lines = script_text().splitlines()
        self.assertEqual(["#!/usr/bin/env bash", "set -e"], lines[:2])

    def test_it_carries_no_actions_expression(self):
        """Actions never substitutes into a file, so an expression here
        would reach bash as literal text. The body's own `${VAR:-…}`
        parameter expansions are bash, moved verbatim, and stay."""
        self.assertNotIn("${{", script_text())

    def test_the_header_reads_present_tense_first_and_history_second(self):
        lines = script_text().splitlines()
        self.assertIn("# What this script does", lines)
        self.assertIn("# Incident history", lines)
        self.assertLess(lines.index("# What this script does"),
                        lines.index("# Incident history"))

    def test_the_header_sits_between_line_two_and_the_first_code_line(self):
        lines = script_text().splitlines()
        first_code = next(i for i, line in enumerate(lines[2:], start=2)
                          if line.strip() and not line.lstrip().startswith("#"))
        header = "\n".join(lines[2:first_code])
        self.assertIn("What this script does", header)
        self.assertIn("Incident history", header)

    def test_the_header_names_every_env_input(self):
        lines = script_text().splitlines()
        start = lines.index("# What this script does")
        end = lines.index("# Incident history")
        section = "\n".join(lines[start:end])
        missing = [k for k in ENV_KEYS if k not in section]
        self.assertEqual([], missing)

    def test_every_card_reference_is_kept(self):
        text = script_text()
        missing = [ref for ref in REFERENCES if ref not in text]
        self.assertEqual([], missing)


# --------------------------------------------------------------------------- #
# the file runs as a file                                                      #
# --------------------------------------------------------------------------- #

def run_pushed_round(td: str, command: list[str]):
    """Run `command` from a runner-shaped directory for a plain pushed fix
    round; return `(proc, posted comments)`."""
    _checkout(td)
    log = os.path.join(td, "comments.jsonl")
    binary = _gh_stub(td, log)
    for path in RECEIPTS:
        if os.path.exists(path):
            os.remove(path)
    _open_handoff(td, blocked=False)
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(PATH=binary + os.pathsep + os.environ["PATH"],
               CLASSIFICATION="", DISPATCH_TOKEN="test",
               **_report_env(td, "fix"))
    proc = subprocess.run(command, cwd=td, env=env,
                          capture_output=True, text=True)
    posted = [json.loads(line) for line in
              (open(log).read().splitlines() if os.path.exists(log) else [])]
    return proc, posted


class APushedRoundRunsAsAFile(unittest.TestCase):
    """The "fix attempt pushed" path, driven through the file in this repo."""

    def tearDown(self):
        for path in RECEIPTS:
            if os.path.exists(path):
                os.remove(path)

    def _assert_receipt(self, td, proc, posted):
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertEqual(
            ["🔧 Fix attempt 2 pushed — CI and critic review re-running."
             f"\n\n{pipeline_act.trailer('fix-attempt-landed')}"
             f"\n\n{answers(td)}\n"],
            [p["body"] for p in posted])

    def test_the_script_runs_by_its_path(self):
        with tempfile.TemporaryDirectory() as td:
            proc, posted = run_pushed_round(td, ["bash", SCRIPT])
            self._assert_receipt(td, proc, posted)

    def test_the_step_line_runs_the_same_script(self):
        """The step's own `run:` executed from the runner's working
        directory, where the pipeline checkout sits at .bureau-pipeline."""
        with tempfile.TemporaryDirectory() as td:
            proc, posted = run_pushed_round(td, ["bash", "-c", step()["run"]])
            self._assert_receipt(td, proc, posted)


if __name__ == "__main__":
    unittest.main()
