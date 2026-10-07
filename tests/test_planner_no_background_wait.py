"""A planner never ends its run on background helpers, and a planner that
ends with no cards is quoted in its own words (DRE-5564).

THE INCIDENT (2026-10-01, 7:19–7:24 pm PT). Portico run 36955032837 planned
epic DRE-5490 on claude-fable-5-1: 14 turns, $5.17, `is_error: false`. It
started three exploration agents in the background and then ended its turn:

    "The exploration agents are still running. I'll wait for their reports
     before cutting cards, since the footprint of each card depends on what
     the real files are."

    "…Everything I need next depends on the three exploration reports
     (server, client, ADR/evidence conventions), so I'm waiting on those
     before drafting the cards."

In a headless `claude-code-action` run, ending the turn ends the run, so the
reports never arrived. No cards, no reason file — and the epic went to Green
Light under "The planner ended without creating any cards and without stating
a reason", although the planner's own last message said exactly why. It is the
planner's twin of the fix-run incident DRE-5271 pinned in
`tests/test_fix_run_no_background_wait.py`.

What is pinned here:

  1. Every model step in plan.yml turns Claude Code's background tasks off
     (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), so a helper the planner starts
     runs in the foreground and the planner holds its turn until the helper's
     report is back. The planning prompts say so, and say why.
  2. A planner that ends with no cards and no reason file is quoted: the
     escalation reads the planner's final message out of the run's execution
     file and puts it on the epic, through the same plain-English guard every
     reason passes. "No reason" is said only when there was nothing to quote.

Run: python3 -m pytest tests/test_planner_no_background_wait.py -v
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import planning_escalation  # noqa: E402
from test_planning_escalation import _Card  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"

DISABLE_BACKGROUND = "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"
DOCS_SOURCE = "code.claude.com/docs/en/env-vars"

CARD = "DRE-5490"

#: The result event's text in run 36955032837, verbatim — what the planner
#: said as its run ended.
LAST_WORDS = (
    "…Everything I need next depends on the three exploration reports "
    "(server, client, ADR/evidence conventions), so I'm waiting on those "
    "before drafting the cards."
)

#: The two prompts that plan an epic: the first attempt and its re-run on the
#: next rung. Both carry the escalation path (step 6) a no-card run takes.
PLANNING_STEPS = ("Plan epic", "Plan epic — on the next rung")

NEVER_END_TURN_RE = re.compile(
    r"(?i)\bnever end your turn\b[^.]{0,80}\bstill running\b"
)
TURN_ENDS_RUN_RE = re.compile(r"(?i)\bending your turn ends the run\b")
BACKGROUND_OFF_RE = re.compile(r"(?i)\bbackground tasks are off\b")
LAST_MESSAGE_POSTED_RE = re.compile(
    r"(?i)\blast message\b[^.]{0,80}\bposted on the epic\b"
)


def _workflow() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    out: list[dict] = []
    for job in (_workflow().get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def _model_steps() -> list[dict]:
    return [s for s in _steps() if "claude-code-action" in str(s.get("uses") or "")]


def _step(name: str) -> dict:
    found = [s for s in _steps() if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one {name!r} step in plan.yml"
    return found[0]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _execution(tmp_path: Path, events, name: str = "exec.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(events), encoding="utf-8")
    return str(path)


def _the_incident(tmp_path: Path, result: str = LAST_WORDS) -> str:
    """The shape run 36955032837 left behind: three background helpers
    started, two closing messages, and a clean result carrying the second."""
    helpers = [
        {"type": "tool_use", "name": "Agent",
         "input": {"description": f"explore {what}", "run_in_background": True}}
        for what in ("server", "client", "ADR conventions")
    ]
    return _execution(tmp_path, [
        {"type": "system", "subtype": "init", "model": "claude-fable-5-1"},
        {"type": "assistant", "message": {"content": helpers}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "The exploration agents are still running."}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": result}]}},
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 14, "total_cost_usd": 5.17, "result": result},
    ])


# ===========================================================================
# 1. Background work cannot outlive the planner's turn
# ===========================================================================
class TestBackgroundTasksAreOffInEveryPlanStep:
    def test_plan_yml_has_model_steps_to_check(self):
        # Non-vacuous: the planning steps, the critics and the re-plans.
        names = {s.get("name") for s in _model_steps()}
        assert set(PLANNING_STEPS) <= names
        assert len(names) >= 10

    @pytest.mark.parametrize("step", _model_steps(), ids=lambda s: s.get("id"))
    def test_every_model_step_disables_background_tasks(self, step):
        env = step.get("env") or {}
        assert str(env.get(DISABLE_BACKGROUND)) == "1", (
            f"{step.get('name')!r} lets its agent start background work it "
            "can end the run on"
        )

    def test_the_comment_records_the_incident_and_the_source(self):
        raw = WF.read_text(encoding="utf-8")
        start = raw.index("      - name: Plan epic\n")
        block = raw[start:raw.index("          prompt: |", start)]
        assert DISABLE_BACKGROUND in block
        assert DOCS_SOURCE in block
        assert "DRE-5564" in block
        assert "36955032837" in block


class TestThePlanningPromptsSayWhy:
    @pytest.mark.parametrize("name", PLANNING_STEPS)
    def test_never_end_the_turn_while_started_work_is_running(self, name):
        prompt = _flat(_step(name)["with"]["prompt"])
        assert NEVER_END_TURN_RE.search(prompt)
        assert TURN_ENDS_RUN_RE.search(prompt)
        assert BACKGROUND_OFF_RE.search(prompt)

    @pytest.mark.parametrize("name", PLANNING_STEPS)
    def test_the_planner_is_told_its_last_message_is_the_reason(self, name):
        prompt = _flat(_step(name)["with"]["prompt"])
        assert LAST_MESSAGE_POSTED_RE.search(prompt)


# ===========================================================================
# 2. A planner that ends with no cards is quoted in its own words
# ===========================================================================
class TestTheFinalMessageIsRead:
    def test_the_incident_yields_the_planners_last_words(self, tmp_path):
        path = _the_incident(tmp_path)
        assert planning_escalation.final_message(path) == LAST_WORDS

    def test_a_single_result_object_is_read_too(self, tmp_path):
        path = _execution(tmp_path, {"type": "result", "is_error": False,
                                     "result": "  I stopped here.  "})
        assert planning_escalation.final_message(path) == "I stopped here."

    def test_a_died_run_has_no_last_words(self, tmp_path):
        # An is_error result carries the provider's error, not the planner.
        path = _execution(tmp_path, [{"type": "result", "is_error": True,
                                      "result": "API Error: 529 overloaded"}])
        assert planning_escalation.final_message(path) is None

    @pytest.mark.parametrize("path", [None, "", "/nonexistent/exec.json"])
    def test_no_file_means_nothing_to_quote(self, path):
        assert planning_escalation.final_message(path) is None

    def test_an_empty_result_means_nothing_to_quote(self, tmp_path):
        path = _execution(tmp_path, [{"type": "result", "is_error": False,
                                      "result": "   "}])
        assert planning_escalation.final_message(path) is None

    def test_a_long_message_is_bounded(self, tmp_path):
        path = _the_incident(tmp_path, result="word " * 2000)
        text = planning_escalation.final_message(path)
        assert len(text) <= planning_escalation.FINAL_MESSAGE_CAP + 1
        assert text.endswith("…")


class TestTheEscalationQuotesIt:
    def test_the_note_quotes_the_last_words_instead_of_no_reason(self):
        note = planning_escalation.escalation_comment(CARD, None, last_words=LAST_WORDS)
        assert LAST_WORDS in note
        assert planning_escalation.NO_REASON_STATED not in note
        assert f"> {LAST_WORDS}" in note

    def test_no_reason_is_said_only_when_there_is_nothing_to_quote(self):
        note = planning_escalation.escalation_comment(CARD, None)
        assert planning_escalation.NO_REASON_STATED in note

    def test_a_stated_reason_wins_over_the_last_words(self):
        reason = "The CEO has to choose which customer this is for."
        note = planning_escalation.escalation_comment(CARD, reason, last_words=LAST_WORDS)
        assert reason in note
        assert LAST_WORDS not in note

    @pytest.mark.parametrize("leak", [
        "I stopped because scripts/reconcile.py needs promote_ready() first.",
        "VERDICT: APPROVE — nothing to plan.",
    ])
    def test_last_words_pass_the_same_plain_english_guard(self, leak):
        note = planning_escalation.escalation_comment(CARD, None, last_words=leak)
        assert leak not in note
        assert "VERDICT:" not in note
        assert "scripts/reconcile.py" not in note
        assert planning_escalation.destination() in note

    def test_a_multi_line_message_stays_inside_the_quote(self):
        words = "First I looked.\n\nThen I stopped."
        note = planning_escalation.escalation_comment(CARD, None, last_words=words)
        assert "> First I looked.\n>\n> Then I stopped." in note


class TestTheCliEndToEnd:
    def test_the_incident_parks_the_epic_quoting_the_planner(self, tmp_path):
        """The run as it happened: no reason file, the execution file the
        action wrote. The epic still parks for the CEO — and the note says
        what the planner said."""
        card = _Card()
        missing = tmp_path / "planner-escalation.txt"
        card.run(lambda: planning_escalation.main([
            "escalate", CARD, "--reason-file", str(missing),
            "--execution-file", _the_incident(tmp_path),
        ]))
        assert card.states == [(CARD, planning_escalation.destination())]
        assert len(card.posted) == 1
        assert LAST_WORDS in card.bodies()
        assert planning_escalation.NO_REASON_STATED not in card.bodies()

    def test_a_written_reason_is_still_the_one_posted(self, tmp_path):
        card = _Card()
        reason_file = tmp_path / "planner-escalation.txt"
        reason_file.write_text("This needs a decision about pricing.", encoding="utf-8")
        card.run(lambda: planning_escalation.main([
            "escalate", CARD, "--reason-file", str(reason_file),
            "--execution-file", _the_incident(tmp_path),
        ]))
        assert "This needs a decision about pricing." in card.bodies()
        assert LAST_WORDS not in card.bodies()

    def test_a_card_that_moved_on_is_told_the_last_words_too(self, tmp_path):
        card = _Card(lane="In Progress")
        card.run(lambda: planning_escalation.main([
            "escalate", CARD, "--execution-file", _the_incident(tmp_path),
        ]))
        assert card.states == []
        assert LAST_WORDS in card.bodies()


class TestPlanYmlHandsOverTheExecutionFile:
    def test_the_planner_escalation_step_passes_the_execution_file(self):
        step = _step("Planner escalation — hand-planning parks for the CEO")
        assert "--execution-file" in step["run"]
        env = step.get("env") or {}
        handed = " ".join(str(v) for v in env.values())
        # The attempt that counted — the next-rung re-run when there was one.
        assert "steps.plan_done.outputs.execution_file" in handed
        assert "steps.claude.outputs.execution_file" in handed
