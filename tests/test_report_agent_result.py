"""`Report result to Linear` runs as a file: scripts/report_agent_result.sh (DRE-5223).

DRE-3488 moves the step's 26,000-character `run:` block into a script, and
the step keeps one line that calls it. The suites DRE-5221 pointed at
`step_shell` prove the behavior survives the move; this file proves the move
itself. The step delegates and keeps every value it hands the script, the
script is shaped the way `step_shell` says a moved script is, its header
reads current behavior first and incident history second, and the file
runs end to end by its own path. That last part matters because no other
test calls the file directly.

The harness copies the one in tests/test_turn_budget_scenario.py:
`linear_ops.py` records rather than writes, `card_pr.py` answers what the
test picks, `git` answers nothing, and every other script in the checkout is
the real one.

Run: python3 -m pytest tests/test_report_agent_result.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-task.yml")
SCRIPT = os.path.join(ROOT, "scripts", "report_agent_result.sh")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import step_shell  # noqa: E402

STEP = "Report result to Linear"
DELEGATION = "bash .bureau-pipeline/scripts/report_agent_result.sh"

#: The step's `env:` on `main` at 9fe61d5, as the card lists it.
ENV_KEYS = (
    "LINEAR_API_KEY", "GH_TOKEN", "CARD", "RESUME_BRANCH", "RESUME_SHA",
    "RESUME_SNAPSHOT", "MODEL_USED", "CLAUDE_OUTCOME", "DEDUPE_OUTCOME",
    "MODEL_OUTCOME", "CTX_OUTCOME", "SANITIZE_OUTCOME", "INPROGRESS_OUTCOME",
    "PRE_AGENT_LOG", "RESCUE_PUSH_STATUS", "RESCUE_PATCH", "RESCUE_ARTIFACT",
    "RESCUE_PUSHED", "RESCUE_ERROR", "GH_DISPATCH_TOKEN", "BUREAU_SERVER_URL",
    "BUREAU_REPOSITORY", "BUREAU_RUN_ID", "CLAUDE_EXECUTION_FILE",
    "RESCUE_LOCAL_WORK",
)

#: The 27 card references the block carried on `main`; `verify` requires
#: every one of them in the script.
REFERENCES = (
    "DRE-1286", "DRE-1300", "DRE-1343", "DRE-1354", "DRE-1403", "DRE-1655",
    "DRE-1885", "DRE-2032", "DRE-2034", "DRE-2070", "DRE-2074", "DRE-2312",
    "DRE-2316", "DRE-2695", "DRE-2727", "DRE-2776", "DRE-2911", "DRE-2923",
    "DRE-2931", "DRE-3043", "DRE-3097", "DRE-3098", "DRE-3165", "DRE-3262",
    "DRE-4366", "DRE-4368", "DRE-4370",
)

CARD = "DRE-5223"
REPO = "dreadnought-foundry/bureau-pipeline"
RUN_ID = "34000000001"
PR_URL = f"https://github.com/{REPO}/pull/700"


def step() -> dict:
    for entry in yaml.safe_load(open(WORKFLOW))["jobs"]["execute"]["steps"]:
        if entry.get("name") == STEP:
            return entry
    raise AssertionError(f"{STEP!r} is gone from agent-task.yml")


def script_text() -> str:
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


class TheStepDelegates(unittest.TestCase):

    def test_the_run_is_the_delegation_line(self):
        """Put the inline block back and this goes red."""
        self.assertEqual(DELEGATION, step()["run"].strip())
        self.assertEqual("scripts/report_agent_result.sh",
                         step_shell.delegated_script(step()["run"]))

    def test_the_env_still_carries_every_value_the_script_reads(self):
        missing = [k for k in ENV_KEYS if k not in (step().get("env") or {})]
        self.assertEqual([], missing)

    def test_the_step_keeps_its_condition(self):
        self.assertIn("steps.dedupe.outputs.skip != 'true'", step()["if"])

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
        would reach bash as literal text."""
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

LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["LINEAR_STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"op": sys.argv[1], "args": sys.argv[2:]}) + "\\n")
if sys.argv[1] == "count-comments":
    print("0")
if sys.argv[1] == "dump-comments":
    print("[]")
'''

CARD_PR_STUB = '''#!/usr/bin/env python3
import os
print("OPEN\\t" + os.environ["CARD_PR_STUB_URL"])
'''

FINISHED = {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": 120, "total_cost_usd": 9.40, "result": "PR opened"}


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


def _checkout(td: str) -> None:
    base = os.path.join(td, ".bureau-pipeline")
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(base, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
    _executable(os.path.join(base, "scripts", "linear_ops.py"), LINEAR_STUB)
    _executable(os.path.join(base, "scripts", "card_pr.py"), CARD_PR_STUB)


def run_pr_opened(tc, command: list[str]):
    """Run `command` from a runner-shaped directory; return `(proc, journal)`."""
    td = tempfile.mkdtemp()
    tc.addCleanup(shutil.rmtree, td, ignore_errors=True)
    _checkout(td)
    binary = os.path.join(td, "bin")
    os.makedirs(binary)
    _executable(os.path.join(binary, "git"), "#!/bin/sh\nexit 0\n")
    exec_file = os.path.join(td, "claude-execution-output.json")
    with open(exec_file, "w", encoding="utf-8") as fh:
        json.dump(FINISHED, fh)
    log = os.path.join(td, "linear.jsonl")
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(
        PATH=binary + os.pathsep + os.environ["PATH"],
        RUNNER_TEMP=td,
        LINEAR_API_KEY="test-key",
        GH_TOKEN="test",
        CARD=CARD,
        RESUME_SNAPSHOT=os.path.join(td, "resume-branches.json"),
        MODEL_USED="claude-opus-5",
        CLAUDE_OUTCOME="success",
        DEDUPE_OUTCOME="success",
        MODEL_OUTCOME="success",
        CTX_OUTCOME="success",
        SANITIZE_OUTCOME="success",
        INPROGRESS_OUTCOME="success",
        PRE_AGENT_LOG=os.path.join(td, "preagent.log"),
        RESCUE_ARTIFACT=f"rescue-{CARD}.patch",
        BUREAU_SERVER_URL="https://github.com",
        BUREAU_REPOSITORY=REPO,
        BUREAU_RUN_ID=RUN_ID,
        CLAUDE_EXECUTION_FILE=exec_file,
        RESCUE_LOCAL_WORK="false",
        LINEAR_STUB_LOG=log,
        CARD_PR_STUB_URL=PR_URL,
    )
    proc = subprocess.run(command, cwd=td, env=env,
                          capture_output=True, text=True)
    journal = []
    if os.path.exists(log):
        journal = [json.loads(line) for line in
                   open(log, encoding="utf-8").read().splitlines()]
    return proc, journal


class PrOpenedRunsAsAFile(unittest.TestCase):
    """The "PR opened" path, driven through the file in this repo."""

    def _assert_pr_opened(self, proc, journal):
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        posted = [e["args"][1] for e in journal if e["op"] == "comment"]
        self.assertEqual(
            [f"🤖 PR opened: {PR_URL} — CI + critic review running. "
             f"Run: https://github.com/{REPO}/actions/runs/{RUN_ID}"],
            posted)
        moved = [e["args"] for e in journal if e["op"] in ("advance", "state")]
        self.assertEqual([[CARD, "In Review", "In Progress,Todo"]], moved)

    def test_the_script_runs_by_its_path(self):
        self._assert_pr_opened(*run_pr_opened(self, ["bash", SCRIPT]))

    def test_the_step_line_runs_the_same_script(self):
        """The step's own `run:` executed from the runner's working
        directory, where the pipeline checkout sits at .bureau-pipeline."""
        self._assert_pr_opened(*run_pr_opened(self, ["bash", "-c", step()["run"]]))


if __name__ == "__main__":
    unittest.main()
