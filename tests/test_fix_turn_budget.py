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
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

# The high-water mark for a fix run that actually FINISHED (portico,
# 2026-08-18, 47 turns / $2.51). The ceiling must clear this with real room,
# not by thirteen turns.
OBSERVED_SUCCESS = 47

# What the agent that only WRITES the code gets (agent-task.yml). The repair
# job is strictly harder — it does the same work plus comprehension of someone
# else's diff — so this is a FLOOR for the fixer, never a target to sit under.
ENGINEER_BUDGET = 150

_TURNS_RE = re.compile(r"--max-turns\s+(\d+)")


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
    args = _claude_args(workflow, job, step_id)
    m = _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no --max-turns: {args!r}"
    return int(m.group(1))


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
        "agent-task's turn budget changed; update ENGINEER_BUDGET here so the "
        "fixer's floor keeps tracking the agent it repairs."
    )
