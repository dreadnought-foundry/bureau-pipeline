"""RED-first: the CEO's console-signed answer reaches the agents that act on
it (DRE-3785).

`scripts/spoken_thread.py` says who each comment on a card is from and checks
every console answer receipt. Reading it reaches nobody by itself. These tests
pin where it is read:

  1. **THE BUILD AGENT.** `agent-task.yml`'s `Assemble agent context` step
     APPENDS what people said on the card to `.bureau-pipeline/agent-context.md`
     — the file the prompt tells the agent to read first. Before this the
     build agent was handed the card's description and no comment at all, so
     "the CEO answers, moves the card to Todo, and a fresh run reads his
     guidance" had nothing behind it.
  2. **THE PLANNER.** `plan.yml`'s `Assemble planner context` does the same,
     for the epic a re-plan reads.
  3. **THE PLAN CRITICS.** Their `Thread:` line hands them the attributed
     thread, not `dump-comments`' bare bodies, which say nothing about who
     wrote what.
  4. **NO NEW EXPRESSION.** The card id and the Linear key reach the script
     through the step's `env:`; the new lines carry no `${{ }}`, so they add
     nothing to the compiled expression GitHub caps (DRE-3484).
  5. **THE STEP CANNOT DIE.** The planner step's own script is RUN with no card
     and no key, and exits 0 with the SPOKEN STATUS line UNKNOWN.
  6. **THE WORDS.** The engineer brief and the untrusted-content standard say
     which label is the CEO's and that nothing else is.

Run: cd bureau-pipeline && python3 -m pytest tests/test_spoken_thread_wiring.py -v
"""
from __future__ import annotations

import os
import re
import subprocess  # nosec B404 — fixed-arg bash call against a temp dir
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import spoken_thread  # noqa: E402
from test_ledger_context_wiring import fake_checkout  # noqa: E402

CONTEXT = ".bureau-pipeline/agent-context.md"
PEOPLE = 'spoken_thread.py people "$CARD"'
THREAD = "spoken_thread.py thread"


def _step(workflow: str, name: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if step.get("name") == name:
                return step
    raise AssertionError(f"{workflow} carries no {name!r} step")


def _logical(script: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", script)
    return [line.strip() for line in joined.splitlines() if line.strip()]


def _people_line(script: str) -> str:
    hits = [line for line in _logical(script) if PEOPLE in line]
    assert len(hits) == 1, f"expected one `{PEOPLE}` command, found {len(hits)}"
    return hits[0]


CONTEXT_STEPS = (("agent-task.yml", "Assemble agent context",
                  "assemble_context.py assemble"),
                 ("plan.yml", "Assemble planner context",
                  "assemble_context.py assemble planner"))


def test_each_context_step_appends_what_people_said_after_the_standards():
    for workflow, name, assemble in CONTEXT_STEPS:
        script = _step(workflow, name)["run"]
        line = _people_line(script)
        assert f">> {CONTEXT}" in line, (
            f"{workflow}: what people said must be APPENDED to {CONTEXT}")
        assert not re.search(r"(?<!>)>\s*" + re.escape(CONTEXT), line), (
            f"{workflow}: a single `>` truncates the standards just assembled")
        assert script.index(assemble) < script.index(PEOPLE), (
            f"{workflow}: the people block must come after the standards")


def test_each_context_step_hands_the_script_its_card_and_key_through_env():
    for workflow, name, _ in CONTEXT_STEPS:
        step = _step(workflow, name)
        env = step.get("env") or {}
        assert env.get("CARD") == "${{ github.event.client_payload.identifier }}", (
            f"{workflow}: {name} must name the card in env")
        assert env.get("LINEAR_API_KEY") == "${{ secrets.LINEAR_API_KEY }}", (
            f"{workflow}: {name} must carry the fleet's Linear key in env")
        assert "${{" not in _people_line(step["run"]), (
            f"{workflow}: the new line must not add to the compiled expression")


def test_each_context_step_echoes_the_spoken_status_into_the_log():
    for workflow, name, _ in CONTEXT_STEPS:
        lines = _logical(_step(workflow, name)["run"])
        assert any(spoken_thread.STATUS.rstrip(":") in line and "grep" in line
                   for line in lines), (
            f"{workflow}: the SPOKEN STATUS line must reach the step log")


def test_the_plan_critics_read_the_attributed_thread():
    text = (WORKFLOWS / "plan.yml").read_text(encoding="utf-8")
    threads = re.findall(r"^\s*Thread: (.*)$", text, re.M)
    assert len(threads) == 3, threads
    for line in threads:
        assert THREAD in line, f"a critic still reads bare bodies: {line}"
        assert "dump-comments" not in line


def test_the_planner_step_cannot_die_with_no_card_and_no_key():
    script = _step("plan.yml", "Assemble planner context")["run"]
    env = {k: v for k, v in os.environ.items()
           if k not in ("CARD", "LINEAR_API_KEY")}
    with tempfile.TemporaryDirectory() as tmp:
        fake_checkout(tmp)
        proc = subprocess.run(  # nosec B603 B607 — fixed args, temp cwd
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail",
             "-c", script], cwd=tmp, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        blob = Path(tmp, CONTEXT).read_text(encoding="utf-8")
    assert f"{spoken_thread.STATUS} UNKNOWN" in blob
    assert f"{spoken_thread.STATUS} UNKNOWN" in proc.stdout
    assert "===== BEGIN briefs/planner.md =====" in blob, "the append truncated"


def test_the_engineer_brief_says_where_the_ceos_answer_is():
    brief = (ROOT / "briefs" / "engineer.md").read_text(encoding="utf-8")
    assert spoken_thread.PEOPLE_HEADING.lstrip("# ") in brief
    assert "the CEO, via the console" in brief


def test_the_untrusted_content_standard_names_the_one_label_that_is_the_ceo():
    text = (ROOT / "standards" / "untrusted-content.md").read_text(encoding="utf-8")
    assert "the CEO, via the console" in text
