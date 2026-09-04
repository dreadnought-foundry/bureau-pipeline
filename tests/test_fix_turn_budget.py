"""RED-first tests for DRE-2533 — the FIX agent's turn budget.

The sibling of ``test_critic_turn_budget.py``. DRE-2422 fixed the critic's
ceiling and, in the very comment that justified it, listed the budgets it was
being compared against:

    "Every sibling already sits higher (verify 60, agent-fix 60,
     red-main-repair 100, agent-task 150)"

agent-fix's 60 was used as EVIDENCE that 40 was too low, and never itself
questioned. This file asks the question DRE-2422 did not.

THE BUG (agent-bureau PR #2063, 2026-08-19). The critic returned a valid
REQUEST_CHANGES. The fix agent started three seconds later and died:

    "subtype": "error_max_turns", "num_turns": 61,
    "total_cost_usd": 4.8174155
    ##[error]Execution failed: Reached maximum number of turns (60)

Then it happened AGAIN on the re-dispatch — 61 turns, $5.85 — before
DRE-2024's convergence halt correctly refused a third. **$10.67 of inference,
no fix, PR blocked.** Measured from the same window, the one fix run that
FINISHED took 47 turns (portico, 2026-08-18, $2.51).

So the observed distribution is 47(success), 61(ceiling), 61(ceiling)
against a ceiling of 60. By the standard DRE-2422 set — "a run that SUCCEEDS
at 41 [against a ceiling of 40] has exactly zero margin left" — a success at
47 against 60 has thirteen turns of margin. The fix agent is riding its
ceiling exactly as the critic was.

WHY THIS AGENT NEEDS AT LEAST THE ENGINEER'S BUDGET

The comparison is a job comparison, not a preference. The critic reads a diff
and speaks: 80/120. The engineer writes a card from scratch: 150. The fix
agent must do everything the engineer does AND FIRST comprehend someone
else's diff, read the failing CI, obey the critic's findings as a spec, prove
every acceptance criterion by running a check, and avoid breaking anything
already there. It is the most demanding job in the fleet and held the
second-smallest budget in it.

`--max-turns` is a CEILING, not a target. The portico run that finished in 47
turns costs 47 turns' worth whether the ceiling is 60 or 150. Raising it
changes the bill only for the runs that today produce nothing at all — and
DRE-2024's convergence halt still bounds a genuinely non-converging loop to
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

# What the agent that only WRITES the code gets (agent-task.yml). The repair
# job is strictly harder — it does the same work plus comprehension of someone
# else's diff — so this is a FLOOR for the fixer, never a target to sit under.
#
# DRE-3097 UPDATE: agent-task's ceiling is no longer a literal — a card can
# carry a `turns:<n>` label, or let its `size:` label pick a rung, and the
# workflow interpolates the chosen budget. What this file compares against is
# the DEFAULT, which is what a card carrying neither label still runs with, and
# it is read from `scripts/turn_budget.py` rather than retyped here so the two
# cannot drift apart the way agent-fix's 60 quietly did.
ENGINEER_BUDGET = turn_budget.DEFAULT_TURNS

# The engineer's WALL CLOCK (agent-task.yml, job `execute`). A turn ceiling is
# only half a budget: the job dies at whichever cap it reaches first. 150 turns
# behind a 45-minute wall converts "died at max-turns, no fix" into "cancelled
# by timeout, no fix" — the same wasted spend this card exists to stop. Paired
# with ENGINEER_BUDGET: whatever wall clock the engineer needs to spend 150
# turns, the strictly larger repair job needs at least as much.
ENGINEER_WALL_CLOCK = 120

_TURNS_RE = re.compile(r"--max-turns\s+(\d+)")

# `--max-turns ${{ steps.model.outputs.turns || 150 }}` — the DRE-3097 form.
# The literal inside it is the default the expression falls back to when the
# selecting step wrote nothing, so it is still a number this file can hold to
# a floor; what it is not is a number nobody can change per card.
_TURNS_EXPR_RE = re.compile(
    r"--max-turns\s+\$\{\{[^}]*?\|\|\s*(\d+)\s*\}\}"
)


def _claude_args(workflow: str, job: str, step_id: str) -> str:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    for step in doc["jobs"][job]["steps"]:
        if step.get("id") == step_id:
            return step["with"]["claude_args"]
    raise AssertionError(
        f"no step id={step_id!r} in {workflow} job {job!r} — if the step was "
        f"renamed, update this test rather than deleting it; the budget it "
        f"pins is what stopped DRE-2533 from recurring"
    )


def _max_turns(workflow: str, job: str, step_id: str) -> int:
    """The turn ceiling a step declares — a literal, or the default an
    interpolated per-card budget falls back to (DRE-3097)."""
    args = _claude_args(workflow, job, step_id)
    m = _TURNS_EXPR_RE.search(args) or _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no --max-turns: {args!r}"
    return int(m.group(1))


def _timeout_minutes(workflow: str, job: str) -> int:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    timeout = doc["jobs"][job].get("timeout-minutes")
    assert timeout is not None, (
        f"{workflow} job {job!r} declares no timeout-minutes — GitHub then "
        f"applies its 360-minute default, which is not a budget anyone chose"
    )
    return int(timeout)


def test_the_fix_agent_clears_the_observed_success_with_real_headroom():
    """A ceiling a successful run only just fits is already too low.

    47 turns finished; 60 gave it thirteen turns of margin. The next slightly
    larger PR falls off — which is exactly what PR #2063 did, twice.
    """
    turns = _max_turns("agent-fix.yml", "fix", "claude")
    assert turns >= OBSERVED_SUCCESS * 2, (
        f"agent-fix runs with --max-turns {turns}, but a fix that SUCCEEDED "
        f"took {OBSERVED_SUCCESS} turns. A ceiling within a few turns of the "
        f"observed success is a coin flip, and losing it costs the whole run: "
        f"error_max_turns commits nothing."
    )


def test_the_repair_agent_is_not_budgeted_below_the_agent_it_repairs():
    """The fixer does everything the engineer does, plus understanding the
    engineer's diff and the critic's findings first. Budgeting it below the
    engineer says the harder job is the cheaper one."""
    fixer = _max_turns("agent-fix.yml", "fix", "claude")
    assert fixer >= ENGINEER_BUDGET, (
        f"agent-fix runs with {fixer} turns while agent-task, which only has "
        f"to WRITE the code, runs with {ENGINEER_BUDGET}. The repair job is "
        f"strictly larger: read the verdict, read the failing CI, comprehend "
        f"someone else's diff, fix it, prove each acceptance criterion, break "
        f"nothing else."
    )


def test_the_engineer_budget_this_test_compares_against_is_still_real():
    """Pin the comparison itself. If agent-task's budget moves, this file's
    floor must move with it rather than silently comparing to a stale number —
    the drift that let agent-fix's 60 stand unexamined for months."""
    assert _max_turns("agent-task.yml", "execute", "claude") == ENGINEER_BUDGET, (
        "agent-task's DEFAULT turn budget changed; the workflow's inline "
        "fallback and turn_budget.DEFAULT_TURNS must agree, or a run whose "
        "budget step wrote nothing silently gets a different ceiling than the "
        "one this repo documents."
    )


def test_the_fix_job_has_the_wall_clock_to_actually_spend_its_turns():
    """A turn ceiling the wall clock cannot reach is not a raised budget.

    The job dies at whichever cap comes first. agent-task needs 120 minutes to
    spend 150 turns — 45 "murdered legitimately long builds mid-work" there
    (DRE-2074) at a SMALLER ceiling. Leaving agent-fix at 45 with the same 150
    turns just renames the failure: `error_max_turns, no fix, $X burned`
    becomes `cancelled by timeout, no fix, $X burned`, and the run commits
    nothing either way.
    """
    wall = _timeout_minutes("agent-fix.yml", "fix")
    assert wall >= ENGINEER_WALL_CLOCK, (
        f"agent-fix's job dies at {wall} minutes while budgeted "
        f"{_max_turns('agent-fix.yml', 'fix', 'claude')} turns; agent-task "
        f"needs {ENGINEER_WALL_CLOCK} minutes for {ENGINEER_BUDGET}. The "
        f"repair job is the larger one — a turn ceiling it has no clock to "
        f"reach is a raise on paper only."
    )


def test_the_engineer_wall_clock_this_test_compares_against_is_still_real():
    """Pin the wall-clock comparison the same way ENGINEER_BUDGET is pinned.
    If agent-task's timeout moves, this floor must move with it rather than
    silently measuring against a number that no longer exists."""
    assert _timeout_minutes("agent-task.yml", "execute") == ENGINEER_WALL_CLOCK, (
        "agent-task's job timeout changed; update ENGINEER_WALL_CLOCK here so "
        "the fixer's wall clock keeps tracking the agent it repairs."
    )
