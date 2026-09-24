"""The FIX agent's turn budget — selected, never typed (DRE-2533, DRE-4364).

WHERE THIS FILE STARTED. DRE-2422 fixed the critic's ceiling and, in the very
comment that justified it, listed the budgets it was being compared against:

    "Every sibling already sits higher (verify 60, agent-fix 60,
     red-main-repair 100, agent-task 150)"

agent-fix's 60 was used as EVIDENCE that 40 was too low and never itself
questioned. This file asked the question DRE-2422 did not, and pinned the
answer as a number.

THE NUMBER WAS THE REMAINING FAULT (DRE-4364). A ceiling typed into the YAML is
a ceiling no card can move: `turn_budget.py` reads a `turns:<n>` label off the
card and `agent-fix.yml` never called it, so DRE-3926 (`turns:400`) and
DRE-4028 (`turns:250`) both died in the fix loop at 150 carrying the label that
would have saved them. The live case that closed into DRE-4364 is portico PR
#676: a fix run that finished its work at 151 turns, cost $18.21, and was
failed by the ceiling.

So what this file pins changed shape. It no longer holds a number in the
workflow to a floor — there is no number in the workflow. It holds that the
fixer's ceiling IS the budget the `Select model` step selected, that the step
selects one on every path a run can take, and that the module's own default
still clears the evidence below. `tests/test_no_literal_turn_ceiling.py` is the
guard against a literal coming back; this file is the guard against the ceiling
coming from somewhere other than the card.

THE EVIDENCE, kept because it is why any of this exists. agent-bureau PR #2063
(2026-08-19): the critic returned a valid REQUEST_CHANGES, the fix agent
started three seconds later and died:

    "subtype": "error_max_turns", "num_turns": 61,
    "total_cost_usd": 4.8174155
    ##[error]Execution failed: Reached maximum number of turns (60)

Then it happened AGAIN on the re-dispatch — 61 turns, $5.85 — before DRE-2024's
convergence halt correctly refused a third. **$10.67 of inference, no fix, PR
blocked.** Measured from the same window, the one fix run that FINISHED took 47
turns (portico, 2026-08-18, $2.51): thirteen turns of margin under that
ceiling.

WHY THIS AGENT NEEDS AT LEAST THE ENGINEER'S BUDGET. The comparison is a job
comparison, not a preference. The critic reads a diff and speaks. The engineer
writes a card from scratch. The fix agent must do everything the engineer does
AND FIRST comprehend someone else's diff, read the failing CI, obey the
critic's findings as a spec, prove every acceptance criterion by running a
check, and avoid breaking anything already there. It is the most demanding job
in the fleet and held the second-smallest budget in it. Since DRE-4361 both
take the same rung, and since this card both take it from the same module.

`--max-turns` is a CEILING, not a target. The portico run that finished in 47
turns costs 47 turns' worth whatever number the selector answers. A higher
ceiling changes the bill only for the runs that today produce nothing at all —
and DRE-2024's convergence halt still bounds a genuinely non-converging loop to
two attempts per head sha, so this cannot become an unbounded burn.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
sys.path.insert(0, str(REPO / "scripts"))

import turn_budget  # noqa: E402

# The high-water mark for a fix run that actually FINISHED (portico,
# 2026-08-18, 47 turns / $2.51). The ceiling must clear this with real room,
# not by thirteen turns.
OBSERVED_SUCCESS = 47

# What the agent that only WRITES the code gets. Both workflows now select
# through `turn_budget.py`, so the comparison is against the module rather than
# against a number retyped in two YAML files — which is exactly how agent-fix's
# 60 came to sit unexamined beside agent-task's 150 for months.
ENGINEER_BUDGET = turn_budget.DEFAULT_TURNS

# The engineer's WALL CLOCK (agent-task.yml, job `execute`). A turn ceiling is
# only half a budget: the job dies at whichever cap it reaches first. A big
# ceiling behind a 45-minute wall converts "died at max-turns, no fix" into
# "cancelled by timeout, no fix" — the same wasted spend this card exists to
# stop. Paired with ENGINEER_BUDGET: whatever wall clock the engineer needs to
# spend its turns, the strictly larger repair job needs at least as much.
ENGINEER_WALL_CLOCK = 120

# The one form a ceiling may take in either of these two workflows: the output
# of the step that ran `turn_budget.py select`. No `|| <n>` fallback — the
# agent step carries the same `if` as the selecting step and `select` never
# exits non-zero, so a fallback can only mask a bug, with a literal.
SELECTED_TURNS = "${{ steps.model.outputs.turns }}"

_TURNS_RE = re.compile(r"--max-turns\b[ \t]*([^\n]*)")


def _steps(workflow: str, job: str) -> list[dict]:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    return doc["jobs"][job]["steps"]


def _step(workflow: str, job: str, step_id: str) -> dict:
    for step in _steps(workflow, job):
        if step.get("id") == step_id:
            return step
    raise AssertionError(
        f"no step id={step_id!r} in {workflow} job {job!r} — if the step was "
        f"renamed, update this test rather than deleting it; the budget it "
        f"pins is what stopped DRE-2533 from recurring"
    )


def _claude_args(workflow: str, job: str, step_id: str) -> str:
    return _step(workflow, job, step_id)["with"]["claude_args"]


def _max_turns(workflow: str, job: str, step_id: str) -> str:
    """The turn ceiling a step declares, exactly as written."""
    args = _claude_args(workflow, job, step_id)
    m = _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no --max-turns: {args!r}"
    return m.group(1).strip()


def _timeout_minutes(workflow: str, job: str) -> int:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    timeout = doc["jobs"][job].get("timeout-minutes")
    assert timeout is not None, (
        f"{workflow} job {job!r} declares no timeout-minutes — GitHub then "
        f"applies its 360-minute default, which is not a budget anyone chose"
    )
    return int(timeout)


def _select_model_run() -> str:
    """agent-fix.yml's `Select model` step, as a shell script."""
    return _step("agent-fix.yml", "fix", "model")["run"]


def test_the_fix_agent_takes_its_ceiling_from_the_selected_budget():
    """The fixer's ceiling is the card's own rung, not a number in the YAML.

    DRE-3926 carried `turns:400` and DRE-4028 carried `turns:250`; both died in
    the fix loop at 150, because the label reached the build job and nothing
    else read it.
    """
    assert _max_turns("agent-fix.yml", "fix", "claude") == SELECTED_TURNS, (
        f"agent-fix's Fix step must declare `--max-turns {SELECTED_TURNS}`. A "
        f"literal there is a ceiling no card can move, and the fix loop is "
        f"where the two labelled cards died."
    )


def test_the_select_model_step_selects_the_budget():
    """The output the Fix step reads has to be written by somebody.

    Same module, same subcommand and same `--explain-file` shape as
    agent-task.yml: a run must not be able to read the card one way for its
    model and another for its budget.
    """
    run = _select_model_run()
    assert "turn_budget.py select" in run, (
        "agent-fix.yml's Select model step does not call `turn_budget.py "
        "select`; nothing else writes the ceiling the Fix step interpolates."
    )
    assert re.search(r'echo\s+"turns=', run), (
        "the Select model step never writes `turns` to GITHUB_OUTPUT — the "
        "Fix step's `--max-turns` would interpolate to empty."
    )
    assert re.search(r'echo\s+"turns_why=', run), (
        "the Select model step never writes `turns_why`; the reason a run got "
        "the ceiling it got is what the park receipt and the step summary read."
    )
    assert "GITHUB_STEP_SUMMARY" in run, (
        "the chosen budget and its reason must reach the step summary, the "
        "same way the selected model does — a budget nobody can see is a "
        "budget nobody can question."
    )


def test_the_budget_is_selected_from_the_pr_card():
    """The ceiling is a property of the CARD, so the card id is the input.

    `steps.pr.outputs.card` is derived from the branch name by the Resolve PR
    step. Passing anything else — the PR number, a constant — would select a
    budget for something other than the work being repaired.
    """
    step = _step("agent-fix.yml", "fix", "model")
    env = step.get("env") or {}
    assert any(
        "steps.pr.outputs.card" in str(value) for value in env.values()
    ), (
        "the Select model step does not receive `steps.pr.outputs.card` — the "
        "budget must be selected for the card the PR is repairing. Pass it "
        "through env, never by interpolating into the script (DRE-1996: the "
        "branch this id is derived from is agent-chosen text)."
    )
    assert "LINEAR_API_KEY" in env, (
        "the budget is a `turns:`/`size:` label on the card, so this step "
        "reads Linear — agent-task.yml's Select model step declares the same "
        "secret for the same reason. Without it every card silently gets the "
        "default."
    )


def test_a_branch_with_no_card_id_still_gets_a_ceiling():
    """`repair/*` branches carry no DRE-N, and still run the fixer.

    `select --labels ""` is the module's answer for "no card": it returns the
    default rather than hitting Linear for an empty identifier.
    """
    run = _select_model_run()
    assert '--labels ""' in run, (
        "agent-fix.yml's Select model step has no empty-card path. A repair/* "
        "branch carries no card id, and `select ''` would send an empty "
        "identifier to Linear instead of answering the default."
    )


def test_the_conflict_round_gets_a_ceiling_too():
    """Conflict mode exits this step early — and still runs the agent.

    The Fix step carries the same `if` as this one, so a conflict round runs
    `claude_args` with whatever `turns` holds. If the only write sits after the
    early exit, that round interpolates an empty ceiling and the action gets a
    bare `--max-turns`.
    """
    run = _select_model_run()
    turns_write = run.find('echo "turns=')
    early_exit = run.find("exit 0")
    assert turns_write >= 0, "the Select model step never writes `turns`"
    assert early_exit >= 0, (
        "agent-fix.yml's Select model step no longer has an early exit — if "
        "the conflict-mode branch was restructured, re-read this test: what it "
        "protects is that EVERY path through the step writes a ceiling."
    )
    assert turns_write < early_exit, (
        "the budget is written after the conflict-mode early exit, so a "
        "conflict round reaches the Fix step with `turns` unset and the action "
        "gets a bare `--max-turns`. Select the budget before the exit."
    )


def test_the_engineer_is_budgeted_the_same_way_the_fixer_is():
    """Both jobs read one module, so neither can drift from the other.

    This is the test that would have caught agent-fix's 60 when agent-task
    moved: the two ceilings are now the same expression, evaluated per card.
    """
    for step_id in ("claude", "claude_retry1", "claude_retry2"):
        assert _max_turns("agent-task.yml", "execute", step_id) == SELECTED_TURNS, (
            f"agent-task's {step_id} step must declare `--max-turns "
            f"{SELECTED_TURNS}` with no `||` fallback. The fallback cannot "
            f"fire on any path a run takes — the agent step is skipped "
            f"whenever the selecting step is, and `select` never exits "
            f"non-zero — so it can only mask a bug, with the literal this "
            f"card deleted."
        )


def test_the_default_the_fleet_runs_with_clears_the_observed_success():
    """A ceiling a successful run only just fits is already too low.

    47 turns finished; 60 gave it thirteen turns of margin, and the next
    slightly larger PR fell off — which is exactly what PR #2063 did, twice.
    With the number gone from the YAML, the thing to hold to this floor is the
    default the module hands a card that asks for nothing.
    """
    default = turn_budget.default_budget()
    assert default >= OBSERVED_SUCCESS * 2, (
        f"a fix run with no 'turns:' label would get {default} turns, but a "
        f"fix that SUCCEEDED took {OBSERVED_SUCCESS}. A ceiling within a few "
        f"turns of the observed success is a coin flip, and losing it costs "
        f"the whole run: error_max_turns commits nothing."
    )
    assert default == ENGINEER_BUDGET, (
        "config/turn-budgets.json and turn_budget.DEFAULT_TURNS disagree; the "
        "module's degrade path must stay byte-equivalent to the file, or a "
        "run that cannot read its config gets a surprise budget."
    )


def test_the_fix_job_has_the_wall_clock_to_actually_spend_its_turns():
    """A turn ceiling the wall clock cannot reach is not a raised budget.

    The job dies at whichever cap comes first. agent-task needs 120 minutes for
    its budget — 45 "murdered legitimately long builds mid-work" there
    (DRE-2074) at a SMALLER ceiling. Leaving agent-fix at 45 just renames the
    failure: `error_max_turns, no fix, $X burned` becomes `cancelled by
    timeout, no fix, $X burned`, and the run commits nothing either way.
    """
    wall = _timeout_minutes("agent-fix.yml", "fix")
    assert wall >= ENGINEER_WALL_CLOCK, (
        f"agent-fix's job dies at {wall} minutes while budgeted up to "
        f"{ENGINEER_BUDGET} turns; agent-task gets {ENGINEER_WALL_CLOCK} "
        f"minutes for the same. The repair job is the larger one — a turn "
        f"ceiling it has no clock to reach is a raise on paper only."
    )


def test_the_engineer_wall_clock_this_test_compares_against_is_still_real():
    """Pin the wall-clock comparison the same way ENGINEER_BUDGET is pinned.
    If agent-task's timeout moves, this floor must move with it rather than
    silently measuring against a number that no longer exists."""
    assert _timeout_minutes("agent-task.yml", "execute") == ENGINEER_WALL_CLOCK, (
        "agent-task's job timeout changed; update ENGINEER_WALL_CLOCK here so "
        "the fixer's wall clock keeps tracking the agent it repairs."
    )
