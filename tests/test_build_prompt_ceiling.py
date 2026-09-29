"""The build prompt names the run's turn ceiling and its decide-by turn (DRE-4370).

The build agent was never told its turn budget. Its `⏳ n/5` markers existed and
nothing acted on them, and the voluntary hand-back (exit 8) had no deadline — an
agent could sprawl to the last turn and die with nothing written down. So:

  * the `Select model` step asks `turn_budget.py decide-by` for the checkpoint
    turn (DRE-4361 owns the command) and writes it as `decide_by`;
  * every copy of the build prompt states the ceiling and that turn, both read
    from the step outputs rather than typed into the prose, and says the
    decision — continue, or hand back with a split proposal — is made by then
    and not later;
  * the `⏳ 1/5` heartbeat carries the decision, in the two markers the
    contract names;
  * exit 8 asks for a split proposal in a stated shape, and the hand-back
    comment the Report step posts says that is what it is.

Read from the PARSED workflow, like tests/test_presubmit_commit_order.py: the
string the agent receives, not a grep of the file.

Run: cd bureau-pipeline && python3 -m pytest tests/test_build_prompt_ceiling.py -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import planner_score  # noqa: E402

WF = ROOT / ".github" / "workflows" / "agent-task.yml"

TURNS_EXPR = "${{ steps.model.outputs.turns }}"
DECIDE_BY_EXPR = "${{ steps.model.outputs.decide_by }}"
CONTINUING = "⏳ 1/5 plan — continuing within"
HANDING_BACK = "⏳ 1/5 plan — handing back"
HEADER_SUFFIX = "(the agent's split proposal, written at its decide-by turn)"


def _doc() -> dict:
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _steps() -> list:
    return [s for job in _doc()["jobs"].values() for s in job.get("steps") or []]


def _norm(text: str) -> str:
    """Whitespace-collapsed — the prompt is hard-wrapped, and a reflow of the
    same words must not read as a removal."""
    return re.sub(r"\s+", " ", text or "")


def _select_model() -> dict:
    found = [s for s in _steps() if s.get("id") == "model"]
    assert len(found) == 1, "agent-task.yml carries exactly one `model` step"
    assert found[0].get("name") == "Select model"
    return found[0]


def _prompts() -> list:
    prompts = [
        (s.get("with") or {}).get("prompt") or ""
        for s in _steps()
        if "anthropics/claude-code-action" in (s.get("uses") or "")
    ]
    assert len(prompts) == 3, (
        f"expected the three attempt copies of the build prompt, found {len(prompts)}"
    )
    return prompts


def _exit_8(prompt: str) -> str:
    """Item 8 of the process block, whitespace-collapsed."""
    text = _norm(prompt)
    start = text.index("8. HAND BACK")
    return text[start:]


# --------------------------------------------------------------------------- #
# the Select model step                                                        #
# --------------------------------------------------------------------------- #

def test_the_select_model_step_asks_turn_budget_for_the_decide_by_turn():
    run = _select_model()["run"]
    assert re.search(
        r"DECIDE_BY=\$\(python3 \.bureau-pipeline/scripts/turn_budget\.py "
        r"decide-by \"\$TURNS\"\)",
        run,
    ), "the step must derive the checkpoint from the run's own ceiling"


def test_the_select_model_step_writes_decide_by_as_an_output():
    run = _select_model()["run"]
    assert re.search(r'echo "decide_by=\$DECIDE_BY" >> "\$GITHUB_OUTPUT"', run)
    assert run.index("decide-by") > run.index("turn_budget.py select"), (
        "decide-by reads TURNS, so it runs after TURNS is selected"
    )


# --------------------------------------------------------------------------- #
# the three prompt copies                                                      #
# --------------------------------------------------------------------------- #

def test_every_prompt_copy_names_the_ceiling_and_the_decide_by_turn():
    for prompt in _prompts():
        assert TURNS_EXPR in prompt, "the ceiling comes from the step output"
        assert DECIDE_BY_EXPR in prompt, "the decide-by turn comes from the step output"
        assert "split proposal" in prompt


def test_the_ceiling_paragraph_comes_before_the_process_block():
    for prompt in _prompts():
        text = _norm(prompt)
        process = text.index("Process (mandatory):")
        assert -1 < text.find(f"ceiling of {TURNS_EXPR} turns") < process
        assert -1 < text.find(f"By turn {DECIDE_BY_EXPR}") < process


def test_the_decision_is_made_by_that_turn_and_not_later():
    for prompt in _prompts():
        text = _norm(prompt)
        assert "a run that reaches the ceiling is killed with its work unshipped" in text
        assert "you must have decided one of two things" in text
        assert "After that turn you do not hand back" in text


def test_the_heartbeat_carries_the_decision_in_the_contract_markers():
    for prompt in _prompts():
        text = _norm(prompt)
        assert f"{CONTINUING} {TURNS_EXPR} turns" in text
        assert HANDING_BACK in text


def test_exit_8_asks_for_a_split_proposal_in_the_stated_shape():
    for prompt in _prompts():
        item = _exit_8(prompt)
        assert "split proposal" in item
        assert "/tmp/agent-handback.txt" in item
        for part in ("a title", "the files it edits",
                     "the sibling it does not depend on"):
            assert part in item, f"exit 8 no longer asks for {part!r}"


def test_the_prompt_copies_are_still_identical():
    assert len(set(_prompts())) == 1


# --------------------------------------------------------------------------- #
# the hand-back comment                                                        #
# --------------------------------------------------------------------------- #

def test_the_hand_back_comment_header_names_the_split_proposal():
    report = next(s for s in _steps() if s.get("name") == "Report result to Linear")
    headers = [
        line for line in report["run"].splitlines()
        if planner_score.HANDBACK_RECEIPT_PREFIX in line
    ]
    assert len(headers) == 1, "one hand-back header in the Report step"
    header = headers[0]
    assert HEADER_SUFFIX in header
    # The prefix the classifier, the scorer and the split ledger all key on
    # still opens the comment.
    assert f'echo "{planner_score.HANDBACK_RECEIPT_PREFIX}' in header
