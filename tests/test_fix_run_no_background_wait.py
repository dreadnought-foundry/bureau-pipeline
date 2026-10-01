"""A fix run never ends its turn on a background task (DRE-5271).

THE INCIDENT (2026-09-29, PT). Fix runs on four pull requests made their edits
and pushed nothing, and each went to a person as "Fix attempt N pushed no new
commit". None of them died: every one ended `is_error: false`. The agent made
its edits and started the full test suite. The suite outran the 600-second
command limit, Claude Code moved it to the background, and the agent ended its
turn "to wait for the notification". In a headless `claude-code-action` run,
ending the turn ends the session. No notification arrives, the edits were never
committed, and they died with the runner.

    run 36637466321 (#564)  "Full serial suite still running (~17 min).
                             I'll commit and push once it reports."
    run 36654315141 (#571)  "Waiting for the full suite to finish before I
                             update the PR body and push."
    run 36648432869 (portico #790), 36653891976 (portico #790),
    run 36636092095 (#568), 36629912273 (#566) — the same ending.

bureau-pipeline's suite is 12,918 tests and takes 17 to 20 minutes serially,
so "run the local checks until green" was an instruction to start exactly the
command that would be backgrounded.

A second variant, run 36640595665 (#569): the agent waited in the foreground
for two full-suite runs, its push got HTTP 401 when the 60-minute token
expired, and it then trusted `briefs/engineer.md`'s promise that "the workflow
re-mints a fresh token ... and delivers your branch". Only agent-task.yml does
that; agent-fix.yml has no delivery step (that half is DRE-4911).

What is pinned here, three ways:

  1. The Fix step's prompt carries the three rules — commit and push before any
     long check, never the whole suite locally, never end a turn while a
     background task is running — on the PARSED prompt, not a grep of the file.
  2. The Fix step turns Claude Code's background tasks off
     (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), and the comment beside it
     cites where that setting was checked.
  3. The engineer brief's delivery sentence names only workflows that actually
     carry a delivery step — a `push_rescue.py` step — so it cannot promise a
     fix agent a delivery nothing performs.

Run: python3 -m pytest tests/test_fix_run_no_background_wait.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
FIX = WF_DIR / "agent-fix.yml"
BRIEF = ROOT / "briefs" / "engineer.md"

MODEL_STEP = "Fix"

# The setting and the source it was checked against. The name is the one the
# vendor documents; it was also read out of the Claude Code 2.1.284 binary the
# install step pins, where the Bash tool computes `canAutoBackground` as false
# whenever it is set.
DISABLE_BACKGROUND = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
DOCS_SOURCE = "code.claude.com/docs/en/env-vars"

# The three rules, phrased loosely enough to survive a rewording and tightly
# enough that a removal cannot hide. Each is matched within one sentence.
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
# The reason has to be IN the prompt, or the rule reorders itself the moment
# another instruction reads more urgent.
TURN_ENDS_SESSION_RE = re.compile(r"(?i)\bending the turn ends the session\b")
LOSES_WORK_RE = re.compile(r"(?i)\bfinished edit with no push\b[^.]{0,60}\bloses\b")

# The step 4 heading the rules belong to — the one that used to send the
# agent to the full suite.
LOCAL_CHECKS_RE = re.compile(r"(?m)^\s*4\. Run the repo's local checks")
PRE_PUSH_GATE_RE = re.compile(r"(?m)^\s*4b\. PRE-PUSH GATE")

# What a delivery step IS, mechanically: a step whose script runs
# push_rescue.py, the re-mint-then-push that agent-task.yml performs after its
# agent ends (DRE-3043).
DELIVERY_SCRIPT = "push_rescue.py"
# A sentence in the brief that promises that delivery.
DELIVERY_CLAIM_RE = re.compile(r"(?i)\bre-mints\b|\bdelivers your branch\b")
WORKFLOW_NAME_RE = re.compile(r"\b[\w-]+\.yml\b")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for job in (workflow.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def fix_step() -> dict:
    found = [s for s in steps(load(FIX)) if s.get("name") == MODEL_STEP]
    assert len(found) == 1, f"expected exactly one {MODEL_STEP!r} step in {FIX.name}"
    return found[0]


def fix_prompt() -> str:
    return fix_step()["with"]["prompt"]


def flat(text: str) -> str:
    """Collapse the prompt's hard wraps so a sentence reads as one line."""
    return re.sub(r"\s+", " ", text)


def fix_step_comment_block() -> str:
    """The raw text of the Fix step up to its prompt — the step's own comments."""
    raw = FIX.read_text(encoding="utf-8")
    start = raw.index(f"      - name: {MODEL_STEP}\n")
    end = raw.index("          prompt: |", start)
    return raw[start:end]


def has_delivery_step(workflow_file: str) -> bool:
    path = WF_DIR / workflow_file
    if not path.exists():
        return False
    return any(DELIVERY_SCRIPT in (s.get("run") or "") for s in steps(load(path)))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat(text)) if s.strip()]


def delivery_claims(brief_text: str) -> list[str]:
    return [s for s in sentences(brief_text) if DELIVERY_CLAIM_RE.search(s)]


def undelivered_names(brief_text: str) -> list[str]:
    """Every workflow a delivery sentence names that has no delivery step."""
    bad = []
    for claim in delivery_claims(brief_text):
        for name in WORKFLOW_NAME_RE.findall(claim):
            if not has_delivery_step(name):
                bad.append(name)
    return bad


class TheFixPromptCarriesTheThreeRules(unittest.TestCase):

    def setUp(self):
        self.prompt = flat(fix_prompt())

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
        raw = fix_prompt()
        step4 = LOCAL_CHECKS_RE.search(raw)
        gate = PRE_PUSH_GATE_RE.search(raw)
        self.assertIsNotNone(step4, "step 4 (local checks) not found in the Fix prompt")
        self.assertIsNotNone(gate, "step 4b (pre-push gate) not found in the Fix prompt")
        body = flat(raw[step4.start():gate.start()])
        for rule in (PUSH_FIRST_RE, NO_FULL_SUITE_RE, TOUCHED_FILES_RE, NEVER_END_TURN_RE):
            self.assertRegex(body, rule)

    def test_the_old_unbounded_instruction_is_gone(self):
        # "Run the repo's local checks ... until green" and nothing else is
        # the sentence every 2026-09-29 run followed into the full suite.
        raw = fix_prompt()
        step4 = LOCAL_CHECKS_RE.search(raw)
        gate = PRE_PUSH_GATE_RE.search(raw)
        body = flat(raw[step4.start():gate.start()])
        self.assertNotRegex(body, r"^4\. Run the repo's local checks \(see \.github/bureau/overrides\.md\) until green\.\s*$")


class BackgroundTasksAreOffInTheFixStep(unittest.TestCase):

    def test_the_fix_step_disables_background_tasks(self):
        env = fix_step().get("env") or {}
        self.assertEqual(str(env.get(DISABLE_BACKGROUND)), "1")

    def test_the_comment_beside_the_step_records_the_check_and_its_source(self):
        block = fix_step_comment_block()
        self.assertIn(DISABLE_BACKGROUND, block)
        self.assertIn(DOCS_SOURCE, block)
        self.assertIn("DRE-5271", block)

    def test_the_prompt_tells_the_agent_a_long_command_is_killed_not_backgrounded(self):
        self.assertRegex(flat(fix_prompt()), r"(?i)background tasks are off\b")


class TheBriefPromisesDeliveryOnlyWhereItHappens(unittest.TestCase):

    def test_the_detector_knows_which_workflows_deliver(self):
        # Non-vacuous: agent-task.yml has the rescue, and agent-fix.yml, as of
        # this card, does not (DRE-4911 adds it).
        self.assertTrue(has_delivery_step("agent-task.yml"))
        self.assertFalse(has_delivery_step("agent-fix.yml"))

    def test_the_check_catches_a_brief_that_promises_it_for_agent_fix(self):
        promise = (
            "agent-fix.yml re-mints a fresh token after you finish and "
            "delivers your branch if you could not."
        )
        self.assertEqual(undelivered_names(promise), ["agent-fix.yml"])

    def test_the_check_catches_the_unscoped_sentence_that_misled_run_36640595665(self):
        # The sentence as it stood names no workflow at all, so it reads as
        # true for every run — the brief must name who delivers.
        unscoped = "The workflow re-mints a fresh token after you finish and delivers your branch if you could not."
        claims = delivery_claims(unscoped)
        self.assertEqual(len(claims), 1)
        self.assertEqual(WORKFLOW_NAME_RE.findall(claims[0]), [])

    def test_every_delivery_sentence_names_its_workflow(self):
        text = BRIEF.read_text(encoding="utf-8")
        claims = delivery_claims(text)
        self.assertTrue(claims, "the brief's delivery sentence is gone — DRE-3043's rescue is still real")
        for claim in claims:
            self.assertTrue(
                WORKFLOW_NAME_RE.search(claim),
                f"a delivery sentence names no workflow, so it reads as true for every run: {claim!r}",
            )

    def test_no_delivery_sentence_names_a_workflow_without_a_delivery_step(self):
        text = BRIEF.read_text(encoding="utf-8")
        self.assertEqual(undelivered_names(text), [])

    def test_the_brief_says_a_fix_run_has_no_delivery_while_it_has_none(self):
        text = flat(BRIEF.read_text(encoding="utf-8"))
        if has_delivery_step("agent-fix.yml"):
            self.skipTest("agent-fix.yml delivers now (DRE-4911); nothing to warn about")
        self.assertRegex(text, r"(?i)agent-fix\.yml[^.]{0,80}\bno\b[^.]{0,40}\bstep\b")


if __name__ == "__main__":
    unittest.main()
