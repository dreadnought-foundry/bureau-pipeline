"""A proof run never ends its turn on a background task (DRE-8258).

THE INCIDENT (2026-10-28, agent-bureau run 40746950893, card DRE-8162). The
proof agent started the card's roughly five-minute
`scripts/desk_speed.py` run in the background and ended its turn: "Both runs
are still going. I'll pick up when they finish." In a headless
`claude-code-action` run the session ends at the final message. The runner
terminated the orphaned process, the speed run never finished, and the record
was never written.

That made proof-task.yml's agent step the fifth model step to end its turn on
a running background command — DRE-5271 fixed agent-fix.yml's "Fix" step,
DRE-5564 the planner, DRE-6523 the repair agent, DRE-6959 the build. This file
is tests/test_build_run_no_background_wait.py pointed at the three proof
steps (`Implement proof card` and its two rate-limit retries):

  1. Every proof step turns Claude Code's background tasks off
     (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`) in `env:`, never `with:`, and
     the comment beside it cites the card and where the setting was checked —
     on the PARSED workflow.
  2. Every proof prompt's item 2 carries the paragraph — background tasks are
     off, the session ends at the final message, a command runs in the
     foreground to completion before the record is written, a long command
     gets a Bash `timeout` up to 600 seconds, and one that cannot finish in
     that is a `Not observed.` row, never a background task.
  3. The per-command limit is not raised: `BASH_MAX_TIMEOUT_MS` appears
     nowhere in proof-task.yml (agent-fix.yml says why).

Run: python3 -m pytest tests/test_proof_run_no_background_wait.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / ".github" / "workflows" / "proof-task.yml"

# The three proof steps: attempt 1 and the two rate-limit retries, whose
# `with:` and `env:` blocks tests/test_proof_task_workflow.py holds identical.
MODEL_STEPS = ("Implement proof card", "Implement proof card (retry 1)",
               "Implement proof card (retry 2)")

DISABLE_BACKGROUND = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
RAISED_LIMIT = "BASH_MAX_TIMEOUT_MS"
DOCS_SOURCE = "code.claude.com/docs/en/env-vars"
CARD = "DRE-8258"
# Attempt 1's comment names the incident; the retries point back to it.
INCIDENT = ("40746950893", "DRE-8162", "fifth")

# Item 2 observes and writes the record; item 3 pushes it. The paragraph sits
# between them because the items' numbers are pinned by other tests.
STEP_2 = "2. Observe every acceptance criterion as the read-only identities"
STEP_3 = "3. Push the branch with GH_TOKEN"

BACKGROUND_OFF_RE = re.compile(r"(?i)\bbackground tasks are off\b")
NEVER_END_TURN_RE = re.compile(
    r"(?i)\bnever end your turn\b[^.]{0,40}\bbackground task\b"
)
SESSION_ENDS_RE = re.compile(r"(?i)\byour session ends at your final message\b")
NO_NOTIFICATION_RE = re.compile(r"(?i)\bno notification ever arrives\b")
FOREGROUND_RE = re.compile(
    r"(?i)\bin the foreground, to completion\b[^.]{0,40}"
    r"\bwrite the record only from its finished output\b"
)
BASH_TIMEOUT_RE = re.compile(r"(?i)\bBash `timeout`[^.]{0,80}\b600-second ceiling\b")
NEVER_BACKGROUNDED_RE = re.compile(
    r"(?i)\bcannot finish within 600 seconds is never backgrounded\b"
)
OVERRUN_ROW_RE = re.compile(
    r"Not observed\. cause: out-of-reach — <the command did not finish in 600 seconds"
)

RULES = (BACKGROUND_OFF_RE, NEVER_END_TURN_RE, SESSION_ENDS_RE, NO_NOTIFICATION_RE,
         FOREGROUND_RE, BASH_TIMEOUT_RE, NEVER_BACKGROUNDED_RE, OVERRUN_ROW_RE)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict) -> list[dict]:
    out: list[dict] = []
    for job in (workflow.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def proof_step(name: str, workflow: dict | None = None) -> dict:
    found = [s for s in steps(workflow or load(PROOF)) if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one {name!r} step in {PROOF.name}"
    return found[0]


def proof_prompt(name: str, workflow: dict | None = None) -> str:
    return proof_step(name, workflow)["with"]["prompt"]


def flat(text: str) -> str:
    """Collapse the prompt's hard wraps so a sentence reads as one line."""
    return re.sub(r"\s+", " ", text)


def step_comment_block(name: str, raw: str | None = None) -> str:
    """The raw text of one proof step up to its prompt — the step's own comments."""
    raw = PROOF.read_text(encoding="utf-8") if raw is None else raw
    start = raw.index(f"      - name: {name}\n")
    end = raw.index("          prompt: |", start)
    return raw[start:end]


def step_2(prompt: str) -> str:
    start, end = prompt.find(STEP_2), prompt.find(STEP_3)
    assert 0 <= start < end, f"the prompt has no {STEP_2!r} before {STEP_3!r}"
    return flat(prompt[start:end])


def rules_missing_from(body: str) -> list[str]:
    """The rules `body` does not carry — empty when all of them do."""
    return [rule.pattern for rule in RULES if not rule.search(body)]


class BackgroundTasksAreOffInEveryProofStep(unittest.TestCase):

    def setUp(self):
        self.workflow = load(PROOF)

    def test_every_proof_step_disables_background_tasks_in_env(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                step = proof_step(name, self.workflow)
                env = step.get("env") or {}
                self.assertEqual(str(env.get(DISABLE_BACKGROUND)), "1")
                # In `env:`, never in `with:` — the action passes its inputs,
                # not the step's environment, through `with:`.
                self.assertNotIn(DISABLE_BACKGROUND, step.get("with") or {})

    def test_the_comment_beside_each_step_records_the_card_and_its_source(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                block = step_comment_block(name)
                self.assertIn(DISABLE_BACKGROUND, block)
                self.assertIn(DOCS_SOURCE, block)
                self.assertIn(CARD, block)

    def test_attempt_1s_comment_names_the_incident_as_the_fifth(self):
        block = step_comment_block("Implement proof card")
        for fact in INCIDENT:
            with self.subTest(fact=fact):
                self.assertIn(fact, block)

    def test_the_comment_block_ends_at_its_own_steps_prompt(self):
        # Non-vacuous: attempt 1's block must not reach into retry 1's, or a
        # comment on one step would vouch for another.
        block = step_comment_block("Implement proof card")
        self.assertNotIn("Implement proof card (retry 1)", block)

    def test_the_check_goes_red_when_one_step_loses_the_key(self):
        raw = PROOF.read_text(encoding="utf-8")
        marker = "      - name: Implement proof card (retry 2)\n"
        head, tail = raw.split(marker, 1)
        tail = tail.replace(f'          {DISABLE_BACKGROUND}: "1"\n', "", 1)
        workflow = yaml.safe_load(head + marker + tail)
        env = proof_step("Implement proof card (retry 2)", workflow).get("env") or {}
        self.assertNotIn(DISABLE_BACKGROUND, env)
        self.assertEqual(
            str((proof_step("Implement proof card", workflow).get("env") or {})
                .get(DISABLE_BACKGROUND)),
            "1")

    def test_the_per_command_limit_is_not_raised(self):
        # agent-fix.yml, "THE PER-COMMAND LIMIT IS NOT RAISED, on purpose".
        self.assertNotIn(RAISED_LIMIT, PROOF.read_text(encoding="utf-8"))


class EveryProofPromptCarriesTheParagraph(unittest.TestCase):

    def setUp(self):
        self.workflow = load(PROOF)

    def test_every_rule_sits_in_item_2_of_every_proof_prompt(self):
        for name in MODEL_STEPS:
            with self.subTest(step=name):
                self.assertEqual(
                    rules_missing_from(step_2(proof_prompt(name, self.workflow))), [])

    def test_the_three_prompt_copies_are_identical(self):
        first = proof_prompt(MODEL_STEPS[0], self.workflow)
        for name in MODEL_STEPS[1:]:
            with self.subTest(step=name):
                self.assertEqual(first, proof_prompt(name, self.workflow))

    def test_the_check_goes_red_when_any_one_rule_is_removed(self):
        body = step_2(proof_prompt("Implement proof card", self.workflow))
        for rule in RULES:
            with self.subTest(rule=rule.pattern):
                match = rule.search(body)
                self.assertIsNotNone(match)
                cut = body[:match.start()] + body[match.end():]
                self.assertIn(rule.pattern, rules_missing_from(cut))

    def test_the_items_keep_their_numbering(self):
        for name in MODEL_STEPS:
            raw = proof_prompt(name, self.workflow)
            with self.subTest(step=name):
                for item in (STEP_2, STEP_3, "4. A criterion only the CEO's own press",
                             "5. A row you cannot observe as worded",
                             "6. Last, post the marker"):
                    self.assertIn(item, raw)


if __name__ == "__main__":
    unittest.main()
