"""Pre-submit gate: the build agent verifies the card's acceptance criteria
before it opens a PR, and the fixer before it pushes (DRE-2487).

The problem this pins shut
--------------------------
Classifying the 125 REQUEST_CHANGES verdicts on portico between 2026-07-29 and
2026-08-18: 58 of them — 40-46% — rejected the PR for something the AUTHOR
could have caught by re-reading its own card. Unmet acceptance criteria,
partial delivery, claims asserted but never verified, and (portico#316) a
byte-identical diff resubmitted from a closed PR against a card whose checklist
explicitly demanded tests and a write guard. Every one costs a full fix round:
a fixer dispatch on the workhorse model, a critic re-review, a CI + QA pair,
and a median 14 minutes of turnaround (p90: 2 hours).

So both authoring prompts carry a mandatory pre-submit walk: BEFORE
``gh pr create`` (agent-task.yml) and BEFORE the fix push (agent-fix.yml), the
agent re-reads the acceptance criteria and, per item, verifies the diff
satisfies it **by running a check** — a test, a grep, a command whose output it
reads — never by recall. Anything it cannot satisfy is finished before
submitting or declared in the PR body under ``## Unmet criteria`` with a
reason. The fixer additionally proves its diff moved off the rejected head sha.

Why a test and not just prompt text: a prompt is the one part of this pipeline
with no compiler and no runtime error. A step that "disappears" in a later
whitespace-shuffling edit of a 50-line prompt block fails SILENTLY and the only
symptom is a slow drift back to 40% avoidable rejections. This file is the
model-config discipline (``tests/test_model_config.py``) applied to prompt
text: the structural pins are on the PARSED workflow (the action input, not a
grep of the file), they pin ORDER (gate before submit) as well as presence, and
the ``## Unmet criteria`` heading is pinned byte-identical in both prompts
because it is the contract a later critic-side card reads.

Scope is deliberately the two AUTHORING prompts named by the card. plan.yml,
medic.yml, qa-review.yml, verify.yml and red-main-repair.yml are out of scope:
the first three author no code and the last two are their own cards.
"""

import os
import re
import unittest

import yaml

REPO = os.path.join(os.path.dirname(__file__), "..")
WF_DIR = os.path.join(REPO, ".github", "workflows")

TASK_WF = "agent-task.yml"
FIX_WF = "agent-fix.yml"
AUTHORING_WORKFLOWS = (TASK_WF, FIX_WF)

# THE CONTRACT STRING. A later critic-side card greps PR bodies for this exact
# heading, so it is written identically in both prompts — same case, same
# level, same words. Change it here and in both prompts in one commit, never
# in one place.
UNMET_HEADING = "## Unmet criteria"

# Heading-shaped lines mentioning "unmet" — used to prove there is no near-miss
# spelling ("## Unmet Criteria", "### unmet criteria", "## Unmet AC") anywhere
# in either prompt. A near miss is worse than a typo: the contract silently
# matches nothing.
UNMET_HEADING_SHAPED = re.compile(r"(?im)^\s*#{1,6}[^\n]*unmet[^\n]*$")

# The semantic pins. Each is one idea the card requires, phrased loosely enough
# that a rewording of the prompt survives but a REMOVAL cannot.
CRITERIA_RE = re.compile(r"(?i)acceptance criteria")
PER_ITEM_RE = re.compile(r"(?i)\b(each|every|one by one|item by item)\b")
BY_CHECK_RE = re.compile(r"(?i)\b(test|grep|command)\b")
NOT_RECALL_RE = re.compile(r"(?i)\brecall\b")
REASON_RE = re.compile(r"(?i)\breason\b")

# The submit action each prompt's gate must precede.
TASK_SUBMIT_RE = re.compile(r"gh pr create")
FIX_SUBMIT_RE = re.compile(r"(?i)push to the SAME branch")

# The fixer's extra obligation: the diff must actually differ from the head the
# critic rejected. That sha is FETCHED at dispatch (steps.pr.outputs.head_sha,
# the head the verdict was bound to) — a prompt that told the agent to "check
# the diff changed" without giving it the rejected sha would be asking for a
# guess.
REJECTED_HEAD_EXPR = "${{ steps.pr.outputs.head_sha }}"
DIFF_RE = re.compile(r"git diff")


def workflow(name: str) -> dict:
    with open(os.path.join(WF_DIR, name)) as f:
        return yaml.safe_load(f)


def agent_prompt(name: str) -> str:
    """The `prompt:` input of the workflow's claude-code-action step, read from
    the PARSED yaml — the string the agent actually receives, not a grep of the
    file. Exactly one such step exists in each authoring workflow."""
    prompts = []
    for job in workflow(name)["jobs"].values():
        for step in job.get("steps") or []:
            uses = step.get("uses") or ""
            if "anthropics/claude-code-action" in uses:
                prompts.append((step.get("with") or {}).get("prompt"))
    assert len(prompts) == 1, f"{name}: expected one agent step, got {len(prompts)}"
    assert prompts[0], f"{name}: agent step has no prompt"
    return prompts[0]


def norm(text: str) -> str:
    """Whitespace-collapsed. The prompts are hard-wrapped at ~70 columns, so
    every semantic pin below has to read ACROSS a line break — matching the raw
    string would make a reflow of the same words look like a removal."""
    return re.sub(r"\s+", " ", text)


def numbered_steps(prompt: str) -> list:
    """The prompt's mandatory ordered list, as [(label, body)] in written
    order. Both prompts number their process ("1.", "2.", and the house's
    inserted-step form "1b.", "2b.") — the gate has to BE one of these items,
    not a sentence buried in surrounding prose that an agent can read as
    optional colour."""
    items = []
    marker = re.compile(r"(?m)^\s*(\d+[a-z]?)\.\s")
    hits = list(marker.finditer(prompt))
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(prompt)
        items.append((hit.group(1), prompt[hit.end() : end]))
    return items


def gate_index(items: list) -> int:
    """Index of the numbered step that IS the pre-submit gate: it names the
    acceptance criteria, walks them per item, and forbids recall."""
    for i, (_, body) in enumerate(items):
        flat = norm(body)
        if (
            CRITERIA_RE.search(flat)
            and PER_ITEM_RE.search(flat)
            and NOT_RECALL_RE.search(flat)
        ):
            return i
    return -1


def match_index(items: list, pattern) -> int:
    for i, (_, body) in enumerate(items):
        if pattern.search(norm(body)):
            return i
    return -1


def gate_body(items: list, workflow_name: str) -> str:
    """The gate step's body, or a failure that says which prompt lost it —
    indexing with a -1 would silently assert against the LAST step instead."""
    idx = gate_index(items)
    assert idx >= 0, (
        f"{workflow_name}: no numbered step re-reads the acceptance criteria "
        "item by item and forbids recall — the pre-submit gate is gone"
    )
    return items[idx][1]


class UnmetCriteriaContractTest(unittest.TestCase):
    """`## Unmet criteria` is one string in two prompts (card AC 4)."""

    def test_both_prompts_carry_the_exact_heading(self):
        for wf in AUTHORING_WORKFLOWS:
            with self.subTest(workflow=wf):
                self.assertIn(
                    UNMET_HEADING,
                    agent_prompt(wf),
                    f"{wf}: the agent prompt must name the exact heading "
                    f"{UNMET_HEADING!r} — it is the contract a later "
                    "critic-side card reads out of the PR body",
                )

    def test_no_near_miss_spelling_of_the_heading(self):
        for wf in AUTHORING_WORKFLOWS:
            for line in UNMET_HEADING_SHAPED.findall(agent_prompt(wf)):
                with self.subTest(workflow=wf, line=line):
                    self.assertEqual(
                        line.strip(),
                        UNMET_HEADING,
                        f"{wf}: {line.strip()!r} is a near miss of "
                        f"{UNMET_HEADING!r} — one spelling, or the contract "
                        "matches nothing",
                    )

    def test_the_heading_is_byte_identical_across_the_two_prompts(self):
        # Not "both contain a heading" — both contain the SAME bytes. Compare
        # what each prompt actually wrote rather than trusting the constant.
        written = set()
        for wf in AUTHORING_WORKFLOWS:
            written |= {ln.strip() for ln in UNMET_HEADING_SHAPED.findall(agent_prompt(wf))}
        self.assertEqual(
            written,
            {UNMET_HEADING},
            "agent-task.yml and agent-fix.yml disagree on the unmet-criteria "
            f"heading: {sorted(written)}",
        )


class BuildAgentPreSubmitGateTest(unittest.TestCase):
    """agent-task.yml: verify per criterion, by running checks, before
    `gh pr create` (card AC 1)."""

    def setUp(self):
        self.prompt = agent_prompt(TASK_WF)
        self.items = numbered_steps(self.prompt)

    def test_the_gate_is_a_numbered_mandatory_step(self):
        self.assertGreaterEqual(
            gate_index(self.items),
            0,
            "agent-task.yml's prompt has no numbered pre-submit step that "
            "re-reads the acceptance criteria item by item and forbids recall",
        )

    def test_the_gate_demands_an_executed_check_not_recall(self):
        body = norm(gate_body(self.items, TASK_WF))
        self.assertRegex(
            body,
            BY_CHECK_RE,
            "the gate must name the FORM of proof (a test, a grep, a command) "
            "— 'verify each criterion' without that reads as re-reading the "
            "diff, which is what portico#316 did",
        )
        self.assertRegex(body, NOT_RECALL_RE)

    def test_the_gate_precedes_opening_the_pr(self):
        gate = gate_index(self.items)
        submit = match_index(self.items, TASK_SUBMIT_RE)
        self.assertGreaterEqual(submit, 0, "no `gh pr create` step found")
        self.assertLess(
            gate,
            submit,
            "the verification walk must come BEFORE `gh pr create` — a gate "
            "after the PR exists is a fix round, which is the cost this card "
            "removes",
        )

    def test_unmet_items_are_completed_or_declared_with_a_reason(self):
        body = gate_body(self.items, TASK_WF)
        self.assertIn(UNMET_HEADING, body)
        self.assertRegex(
            norm(body),
            REASON_RE,
            "an unmet criterion listed with no reason tells the critic "
            "nothing — the heading carries the why",
        )


class FixAgentPrePushGateTest(unittest.TestCase):
    """agent-fix.yml: the same walk before pushing, plus proof the diff moved
    off the rejected head (card AC 2)."""

    def setUp(self):
        self.prompt = agent_prompt(FIX_WF)
        self.items = numbered_steps(self.prompt)

    def test_the_gate_is_a_numbered_mandatory_step(self):
        self.assertGreaterEqual(
            gate_index(self.items),
            0,
            "agent-fix.yml's prompt has no numbered pre-push step that "
            "re-reads the acceptance criteria item by item and forbids recall",
        )

    def test_the_gate_demands_an_executed_check_not_recall(self):
        body = norm(gate_body(self.items, FIX_WF))
        self.assertRegex(body, BY_CHECK_RE)
        self.assertRegex(body, NOT_RECALL_RE)

    def test_the_gate_precedes_the_push(self):
        gate = gate_index(self.items)
        submit = match_index(self.items, FIX_SUBMIT_RE)
        self.assertGreaterEqual(submit, 0, "no commit-and-push step found")
        self.assertLess(
            gate,
            submit,
            "the walk must happen before the push — the push is what "
            "re-triggers CI and the critic",
        )

    def test_the_gate_compares_against_the_rejected_head_sha(self):
        body = gate_body(self.items, FIX_WF)
        self.assertRegex(
            norm(body),
            DIFF_RE,
            "confirming the diff CHANGED means running a diff, not "
            "remembering that you edited something",
        )
        self.assertIn(
            REJECTED_HEAD_EXPR,
            body,
            "the rejected head sha must be interpolated from "
            f"{REJECTED_HEAD_EXPR} — the agent cannot be asked to guess which "
            "commit the critic rejected",
        )

    def test_an_unchanged_diff_must_not_be_pushed(self):
        body = norm(gate_body(self.items, FIX_WF))
        self.assertRegex(
            body,
            re.compile(r"(?i)do not push|don't push"),
            "portico#316 resubmitted a byte-identical diff: an empty diff "
            "against the rejected head must stop the push outright",
        )

    def test_the_rejected_head_sha_output_exists_upstream(self):
        # The interpolation above is only real if the step output it names is
        # actually produced — a typo'd expression renders as the empty string
        # and the instruction quietly becomes `git diff ..HEAD`.
        body = open(os.path.join(WF_DIR, FIX_WF)).read()
        self.assertRegex(
            body,
            re.compile(r'head_sha=\$HEAD_SHA" >> "\$GITHUB_OUTPUT"'),
            "agent-fix.yml must still publish steps.pr.outputs.head_sha",
        )


class GateSurvivesAPromptRewriteTest(unittest.TestCase):
    """The pins above are structural, but the cheapest way to lose this gate is
    a prompt edit that keeps the words and drops the obligation. These are the
    two properties an edit must not silently break."""

    def test_both_prompts_keep_the_criteria_walk(self):
        # The card's discipline in one assertion per prompt: every authoring
        # prompt names the acceptance criteria inside its own gate step.
        for wf in AUTHORING_WORKFLOWS:
            with self.subTest(workflow=wf):
                items = numbered_steps(agent_prompt(wf))
                self.assertRegex(norm(gate_body(items, wf)), CRITERIA_RE)

    def test_the_gate_is_not_optional_language(self):
        # "consider verifying" / "if time permits" defeats the whole thing.
        soft = re.compile(r"(?i)\b(if you can|if time|consider verifying|optionally)\b")
        for wf in AUTHORING_WORKFLOWS:
            with self.subTest(workflow=wf):
                items = numbered_steps(agent_prompt(wf))
                self.assertNotRegex(norm(gate_body(items, wf)), soft)


if __name__ == "__main__":
    unittest.main()
