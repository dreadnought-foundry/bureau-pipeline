"""Regression pin: DRE-5123 — a scheduled Claude step must admit the qa-bot.

The first scheduled groomer morning after DRE-4972 wired the verify step in
(groomer run 36426267250, `event=schedule`) posted its proposal on time, but
all 29 verify legs came back `unverified — agent step failed`. Every leg's
Claude step refused to start:

    Workflow initiated by non-human actor: agent-bureau-qa-bot ...
    Add bot to allowed_bots list

GitHub gives a `schedule` run the actor who last changed the workflow on the
default branch. Here that is `agent-bureau-qa-bot[bot]`, because it merges
every pipeline PR — so every scheduled run of this repo initiates as the
qa-bot, and a `claude-code-action` step whose `allowed_bots` omits it aborts
before the model is ever called. That is the fifth lockout of the DRE-2020 /
DRE-2037 / DRE-2039 / DRE-2053 class (standards/vendor-boundaries.md, Q1).

This suite reads the LIVE workflow YAML, finds every workflow with a
`schedule:` trigger, follows its jobs' `uses:` into the reusable workflows of
this repo (a scheduled caller's actor is the reusable's actor), and pins that
every Claude step reached that way lists `agent-bureau-qa-bot`. Discovered,
never listed: a scheduled workflow added tomorrow is checked the day it lands.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

QA_BOT = "agent-bureau-qa-bot"
CLAUDE_ACTION = "anthropics/claude-code-action@"

# A job-level `uses:` naming one of THIS repo's workflows: the local form
# (`./.github/workflows/x.yml`) or the self-hosting form every `self-*` stub
# uses (`dreadnought-foundry/bureau-pipeline/.github/workflows/x.yml@main`).
_LOCAL_REUSABLE = re.compile(
    r"^(?:\./|dreadnought-foundry/bureau-pipeline/)\.github/workflows/"
    r"(?P<name>[^@/]+\.ya?ml)(?:@.+)?$"
)


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text()) or {}


def _on(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, dict):
        return on
    if isinstance(on, list):
        return {event: None for event in on}
    if isinstance(on, str):
        return {on: None}
    return {}


def workflow_names() -> list[str]:
    names = sorted(p.name for p in WORKFLOWS.glob("*.y*ml"))
    assert names, f"no workflow files under {WORKFLOWS}"
    return names


def scheduled_workflows() -> list[str]:
    return [n for n in workflow_names() if "schedule" in _on(_load(n))]


def called_workflows(name: str) -> list[str]:
    """This repo's reusable workflows that `name`'s jobs call directly."""
    called = []
    for job in (_load(name).get("jobs") or {}).values():
        match = _LOCAL_REUSABLE.match(str((job or {}).get("uses") or ""))
        if match and (WORKFLOWS / match["name"]).is_file():
            called.append(match["name"])
    return called


def reachable_from(name: str) -> list[str]:
    """`name` plus every reusable of this repo it reaches, transitively."""
    seen, queue = [], [name]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.append(current)
        queue.extend(called_workflows(current))
    return seen


def claude_steps(name: str) -> list[tuple[str, dict]]:
    """(job/step label, step) for every claude-code-action step in `name`."""
    steps = []
    for job_id, job in (_load(name).get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if str(step.get("uses") or "").startswith(CLAUDE_ACTION):
                label = step.get("id") or step.get("name") or "?"
                steps.append((f"{name}:{job_id}/{label}", step))
    return steps


def allowed_bots(step: dict) -> list[str]:
    raw = str((step.get("with") or {}).get("allowed_bots") or "")
    return [token.strip() for token in raw.strip("\"'").split(",") if token.strip()]


def scheduled_claude_steps() -> list[tuple[str, str, dict]]:
    """(scheduled workflow, step label, step) for every Claude step a
    `schedule:` trigger can reach."""
    found = []
    for scheduled in scheduled_workflows():
        for name in reachable_from(scheduled):
            for label, step in claude_steps(name):
                found.append((scheduled, label, step))
    return found


class DiscoveryIsNotVacuousTest(unittest.TestCase):
    """The pin below proves nothing if the walk finds no Claude step."""

    def test_the_groomer_schedule_reaches_the_verify_agent(self):
        self.assertIn("self-groomer.yml", scheduled_workflows())
        self.assertIn("groomer.yml", reachable_from("self-groomer.yml"))
        labels = [label for _, label, _ in scheduled_claude_steps()]
        self.assertIn("groomer.yml:verify/claude", labels)

    def test_the_self_hosting_uses_form_resolves(self):
        match = _LOCAL_REUSABLE.match(
            "dreadnought-foundry/bureau-pipeline/.github/workflows/groomer.yml@main")
        self.assertEqual(match["name"], "groomer.yml")
        self.assertEqual(
            _LOCAL_REUSABLE.match("./.github/workflows/reconcile.yml")["name"],
            "reconcile.yml")
        self.assertIsNone(_LOCAL_REUSABLE.match("actions/checkout@v4"))


class ScheduledClaudeStepsAdmitTheQaBotTest(unittest.TestCase):
    """A `schedule` run initiates as whoever last changed the workflow on the
    default branch — in this repo, the qa-bot that merges every PR."""

    def test_every_scheduled_claude_step_lists_the_qa_bot(self):
        failures = [
            f"{label} (scheduled by {scheduled}): allowed_bots={allowed_bots(step)}"
            for scheduled, label, step in scheduled_claude_steps()
            if QA_BOT not in allowed_bots(step) and "*" not in allowed_bots(step)
        ]
        self.assertEqual(
            failures, [],
            f"every Claude step a `schedule:` trigger reaches must list {QA_BOT} "
            "in allowed_bots — a scheduled run's actor is the last merger of "
            "the workflow, the qa-bot, and the step aborts without it "
            "(DRE-5123):\n" + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
