"""A red-main repair run never ends its turn on a background task (DRE-6523).

THE INCIDENT (2026-10-09). The repair agent for DRE-6511 committed and pushed
its fix, started the whole test suite with `run_in_background`, said "I'll open
the PR once it reports", and ended its turn. In a headless
`claude-code-action` run, ending the turn ends the session. No pull request
was opened and `main` stayed red for 75 minutes.

That made the "Repair agent" step the third model step to end its turn on a
running background command — DRE-5271 fixed agent-fix.yml's "Fix" step,
DRE-5564 the planner — and DRE-6520 is the parent that gives the repair step
the same two guards the Fix step carries. This file is
tests/test_fix_run_no_background_wait.py's first two classes pointed at
red-main-repair.yml:

  1. The Repair agent prompt carries the rules — commit and push before any
     long check, never the whole suite locally, only the touched test files
     under a timeout, never end a turn while a background task is running,
     and the reasons — on the PARSED prompt, not a grep of the file.
  2. The Repair agent step turns Claude Code's background tasks off
     (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), and the comment beside it
     cites where that setting was checked.

The fix test's third class (the engineer brief's delivery sentence) is not
copied: red-main-repair.yml has no push-rescue step and the brief claims none
for it.

Run: python3 -m pytest tests/test_repair_run_no_background_wait.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / ".github" / "workflows" / "red-main-repair.yml"

MODEL_STEP = "Repair agent"

DISABLE_BACKGROUND = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
DOCS_SOURCE = "code.claude.com/docs/en/env-vars"
PARENT_CARD = "DRE-6520"

# The same rule regexes the fix test holds the Fix prompt to, so the two
# prompts cannot drift apart on what the rules say.
PUSH_FIRST_RE = re.compile(
    r"(?i)\bcommit and push\b[^.]{0,40}\bbefore\b[^.]{0,40}\bcheck\b[^.]{0,60}\bminutes\b"
)
NO_FULL_SUITE_RE = re.compile(
    r"(?i)\b(do not|don't|never)\b[^.]{0,20}\brun\b[^.]{0,20}\b(whole|full)\b[^.]{0,20}"
    r"\bsuite\b[^.]{0,20}\blocally\b[^.]{0,40}\bCI runs it\b"
)
TOUCHED_FILES_RE = re.compile(
    r"(?i)\bonly the test files\b[^.]{0,40}\bchange touches\b[^.]{0,40}\beach under a timeout\b"
)
NEVER_END_TURN_RE = re.compile(
    r"(?i)\bnever end your turn\b[^.]{0,40}\bbackground task\b"
)
TURN_ENDS_SESSION_RE = re.compile(r"(?i)\bending the turn ends the session\b")
LOSES_WORK_RE = re.compile(r"(?i)\bfinished edit with no push\b[^.]{0,60}\bloses\b")
BACKGROUND_OFF_RE = re.compile(r"(?i)\bbackground tasks are off\b")

# Step 4 is where the prompt used to send the agent to the full suite; step 5
# is the push and the pull request.
LOCAL_CHECKS_RE = re.compile(r"(?m)^\s*4\. Run the repo's local checks")
OPEN_PR_RE = re.compile(r"(?m)^\s*5\. Push the branch and open a PR")
OLD_STEP_4 = "Run the repo's local checks (see .github/bureau/overrides.md) until green."


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for job in (workflow.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def repair_step() -> dict:
    found = [s for s in steps(load(REPAIR)) if s.get("name") == MODEL_STEP]
    assert len(found) == 1, f"expected exactly one {MODEL_STEP!r} step in {REPAIR.name}"
    return found[0]


def repair_prompt() -> str:
    return repair_step()["with"]["prompt"]


def flat(text: str) -> str:
    """Collapse the prompt's hard wraps so a sentence reads as one line."""
    return re.sub(r"\s+", " ", text)


def repair_step_comment_block() -> str:
    """The raw text of the Repair agent step up to its prompt — the step's own comments."""
    raw = REPAIR.read_text(encoding="utf-8")
    start = raw.index(f"      - name: {MODEL_STEP}\n")
    end = raw.index("          prompt: |", start)
    return raw[start:end]


def step_4(raw: str) -> str:
    start = LOCAL_CHECKS_RE.search(raw)
    end = OPEN_PR_RE.search(raw)
    assert start is not None, "step 4 (local checks) not found in the Repair agent prompt"
    assert end is not None, "step 5 (push and open a PR) not found in the Repair agent prompt"
    return flat(raw[start.start():end.start()])


class TheRepairPromptCarriesTheRules(unittest.TestCase):

    def setUp(self):
        self.prompt = flat(repair_prompt())

    def test_commit_and_push_before_any_long_check(self):
        self.assertRegex(self.prompt, PUSH_FIRST_RE)

    def test_the_whole_suite_is_never_run_locally_because_ci_runs_it(self):
        self.assertRegex(self.prompt, NO_FULL_SUITE_RE)

    def test_only_the_touched_test_files_run_each_under_a_timeout(self):
        self.assertRegex(self.prompt, TOUCHED_FILES_RE)

    def test_never_end_a_turn_while_a_background_task_runs(self):
        self.assertRegex(self.prompt, NEVER_END_TURN_RE)

    def test_the_prompt_says_why(self):
        self.assertRegex(self.prompt, TURN_ENDS_SESSION_RE)
        self.assertRegex(self.prompt, LOSES_WORK_RE)

    def test_the_rules_sit_in_step_4_where_the_suite_used_to_be_sent(self):
        body = step_4(repair_prompt())
        for rule in (PUSH_FIRST_RE, NO_FULL_SUITE_RE, TOUCHED_FILES_RE, NEVER_END_TURN_RE,
                     TURN_ENDS_SESSION_RE, LOSES_WORK_RE, BACKGROUND_OFF_RE):
            self.assertRegex(body, rule)

    def test_the_old_unbounded_instruction_is_gone(self):
        # "Run the repo's local checks ... until green." and nothing else is
        # the sentence the 2026-10-09 run followed into the full suite.
        self.assertNotIn(OLD_STEP_4, self.prompt)

    def test_step_5_still_opens_the_fix_red_main_pull_request(self):
        raw = repair_prompt()
        start = OPEN_PR_RE.search(raw)
        self.assertIsNotNone(start)
        step5 = flat(raw[start.start():])
        self.assertIn("gh pr create", step5)
        self.assertIn('"fix(red-main): <summary>"', step5)
        self.assertRegex(step5, r"(?i)failed run URL")
        self.assertRegex(step5, r"(?i)card link")


class BackgroundTasksAreOffInTheRepairStep(unittest.TestCase):

    def test_the_repair_step_disables_background_tasks(self):
        env = repair_step().get("env") or {}
        self.assertEqual(str(env.get(DISABLE_BACKGROUND)), "1")

    def test_the_comment_beside_the_step_records_the_check_and_its_source(self):
        block = repair_step_comment_block()
        self.assertIn(DISABLE_BACKGROUND, block)
        self.assertIn(DOCS_SOURCE, block)
        self.assertIn(PARENT_CARD, block)

    def test_the_prompt_tells_the_agent_a_long_command_is_killed_not_backgrounded(self):
        self.assertRegex(flat(repair_prompt()), BACKGROUND_OFF_RE)


if __name__ == "__main__":
    unittest.main()
