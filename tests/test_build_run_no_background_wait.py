"""A build run never ends its turn on a background task (DRE-6959).

THE INCIDENT (2026-10-11, run 38164304144, card DRE-6950). The build agent
committed its failing test, made the change in its working tree, started the
web suite in the background and ended its turn: "I'll be notified when the
suite exits". In a headless `claude-code-action` run, ending the turn ends the
session. The session closed as `success` at 102 of 400 turns, the push rescue
delivered the one commit that existed, and the change itself died with the
runner.

That made agent-task.yml's build step the fourth model step to end its turn on
a running background command — DRE-5271 fixed agent-fix.yml's "Fix" step,
DRE-5564 the planner, DRE-6523 the repair agent. This file is
tests/test_repair_run_no_background_wait.py pointed at the three build steps
(`Implement card` and its two rate-limit retries), with the rule regexes
copied unchanged from tests/test_fix_run_no_background_wait.py:

  1. Every build step turns Claude Code's background tasks off
     (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`) in `env:`, and the comment
     beside it cites where that setting was checked — on the PARSED workflow.
  2. Every build prompt's step 3 carries the rules — commit and push before
     any long check, never the whole suite locally, only the touched test
     files under a timeout, never end a turn while a background task is
     running, and the reasons — and "all must pass locally before you push"
     is gone.
  3. briefs/engineer.md no longer tells the agent the local checks must be
     green before it pushes.

Run: python3 -m pytest tests/test_build_run_no_background_wait.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / ".github" / "workflows" / "agent-task.yml"
BRIEF = ROOT / "briefs" / "engineer.md"

# The three build steps: attempt 1 and the two rate-limit retries, whose
# `with:` blocks tests/test_agent_task_rate_limit_retry.py holds identical.
MODEL_STEPS = ("Implement card", "Implement card (retry 1)", "Implement card (retry 2)")

DISABLE_BACKGROUND = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
DOCS_SOURCE = "code.claude.com/docs/en/env-vars"
CARD = "DRE-6959"

# The same rule regexes the fix test holds the Fix prompt to, so the three
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

RULES = (PUSH_FIRST_RE, NO_FULL_SUITE_RE, TOUCHED_FILES_RE, NEVER_END_TURN_RE,
         TURN_ENDS_SESSION_RE, LOSES_WORK_RE, BACKGROUND_OFF_RE)

# Step 3 is where the prompt sent the agent to the whole suite; step 4 is the
# pre-submit gate.
LOCAL_CHECKS_RE = re.compile(r"(?m)^\s*3\. Run the local checks")
PRE_SUBMIT_RE = re.compile(r"(?m)^\s*4\. PRE-SUBMIT GATE")
OLD_SENTENCE = "all must pass locally before you push"

OLD_BRIEF_CLAUSE = "must be green before you push"
NEW_BRIEF_CLAUSE = ("the local check commands, run narrowed to what your change "
                    "can reach as your workflow prompt says")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for job in (workflow.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def build_step(name: str, workflow: dict | None = None) -> dict:
    found = [s for s in steps(workflow or load(TASK)) if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one {name!r} step in {TASK.name}"
    return found[0]


def build_prompt(name: str, workflow: dict | None = None) -> str:
    return build_step(name, workflow)["with"]["prompt"]


def flat(text: str) -> str:
    """Collapse the prompt's hard wraps so a sentence reads as one line."""
    return re.sub(r"\s+", " ", text)


def step_comment_block(name: str, raw: str | None = None) -> str:
    """The raw text of one build step up to its prompt — the step's own comments."""
    raw = TASK.read_text(encoding="utf-8") if raw is None else raw
    start = raw.index(f"      - name: {name}\n")
    end = raw.index("          prompt: |", start)
    return raw[start:end]


def step_3(raw: str) -> str:
    start = LOCAL_CHECKS_RE.search(raw)
    end = PRE_SUBMIT_RE.search(raw)
    assert start is not None, "step 3 (local checks) not found in the build prompt"
    assert end is not None, "step 4 (pre-submit gate) not found in the build prompt"
    return flat(raw[start.start():end.start()])


def rules_missing_from(body: str) -> list[str]:
    """The rules `body` does not carry — empty when all seven do."""
    return [rule.pattern for rule in RULES if not rule.search(body)]


def missing_rules(prompt: str) -> list[str]:
    """The rules step 3 of `prompt` does not carry."""
    return rules_missing_from(step_3(prompt))


class BackgroundTasksAreOffInEveryBuildStep(unittest.TestCase):

    def setUp(self):
        self.workflow = load(TASK)

    def test_every_build_step_disables_background_tasks_in_env(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                step = build_step(name, self.workflow)
                env = step.get("env") or {}
                self.assertEqual(str(env.get(DISABLE_BACKGROUND)), "1")
                # In `env:`, never in `with:` — the action passes its inputs,
                # not the step's environment, through `with:`.
                self.assertNotIn(DISABLE_BACKGROUND, step.get("with") or {})

    def test_the_comment_beside_each_step_records_the_check_and_its_source(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                block = step_comment_block(name)
                self.assertIn(DISABLE_BACKGROUND, block)
                self.assertIn(DOCS_SOURCE, block)
                self.assertIn(CARD, block)

    def test_the_comment_block_ends_at_its_own_steps_prompt(self):
        # Non-vacuous: attempt 1's block must not reach into retry 1's, or a
        # comment on one step would vouch for another.
        block = step_comment_block("Implement card")
        self.assertNotIn("Implement card (retry 1)", block)

    def test_the_check_goes_red_when_one_step_loses_the_key(self):
        stripped = TASK.read_text(encoding="utf-8")
        marker = "      - name: Implement card (retry 2)\n"
        head, tail = stripped.split(marker, 1)
        tail = tail.replace(f'          {DISABLE_BACKGROUND}: "1"\n', "", 1)
        workflow = yaml.safe_load(head + marker + tail)
        env = build_step("Implement card (retry 2)", workflow).get("env") or {}
        self.assertNotIn(DISABLE_BACKGROUND, env)
        self.assertEqual(
            str((build_step("Implement card", workflow).get("env") or {}).get(DISABLE_BACKGROUND)),
            "1")


class EveryBuildPromptCarriesTheRules(unittest.TestCase):

    def setUp(self):
        self.workflow = load(TASK)

    def test_every_rule_sits_in_step_3_of_every_build_prompt(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                self.assertEqual(missing_rules(build_prompt(name, self.workflow)), [])

    def test_each_rule_holds_on_the_whole_flattened_prompt(self):
        for name in MODEL_STEPS:
            prompt = flat(build_prompt(name, self.workflow))
            for rule in RULES:
                with self.subTest(step=name, rule=rule.pattern):
                    self.assertRegex(prompt, rule)

    def test_the_old_unbounded_sentence_is_gone(self):
        # "all must pass locally before you push" is the sentence the
        # 2026-10-11 run followed into a background web suite.
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                self.assertNotIn(OLD_SENTENCE, flat(build_prompt(name, self.workflow)))

    def test_the_check_goes_red_when_any_one_rule_is_removed(self):
        prompt = build_prompt("Implement card", self.workflow)
        body = step_3(prompt)
        for rule in RULES:
            with self.subTest(rule=rule.pattern):
                match = rule.search(body)
                self.assertIsNotNone(match)
                cut = body[:match.start()] + body[match.end():]
                self.assertIn(rule.pattern, rules_missing_from(cut))

    def test_steps_4_4b_and_5_keep_their_numbering(self):
        for name in MODEL_STEPS:
            raw = build_prompt(name, self.workflow)
            with self.subTest(step=name):
                self.assertRegex(raw, r"(?m)^\s*4\. PRE-SUBMIT GATE")
                self.assertRegex(raw, r"(?m)^\s*4b\. COMMIT-ORDER SELF-CHECK")
                self.assertRegex(raw, r"(?m)^\s*5\. Push the branch and open a PR")


class TheEngineerBriefNoLongerGatesThePushOnTheLocalChecks(unittest.TestCase):

    def test_the_brief_drops_must_be_green_before_you_push(self):
        self.assertNotIn(OLD_BRIEF_CLAUSE, flat(BRIEF.read_text(encoding="utf-8")))

    def test_the_brief_sends_the_agent_to_its_workflow_prompt_for_the_narrowing(self):
        self.assertIn(NEW_BRIEF_CLAUSE, flat(BRIEF.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
