"""RED-first tests for DRE-3450 — the epic planner's turn ceiling.

THE INCIDENT (epic DRE-3419, 2026-09-08). The `Plan epic` step ran to a
CLEAN finish and the workflow killed the run anyway:

    "subtype": "success", "is_error": false, "num_turns": 85
    ##[error]Claude reported a successful result after 85 turns,
             exceeding the configured maximum of 80

The plan comment and nine children had already landed. Nothing about the
model, the credentials or the plan failed — the ceiling was simply below
where this epic's planning actually finishes, so `claude-code-action`
turned a success into a failed job. The first attempt and the automatic
retry died on the same number, because a ceiling is DETERMINISTIC: re-run
the same planner over the same epic and it walks into the same wall.

WHAT THIS FILE PINS

1. The epic-planning ceiling clears the observed run with real headroom.
   85 turns is not "an occasional overflow" of 80 — it is over the top of
   it, and the run that produced it was a SUCCESS, so the distribution is
   already past where the ceiling sat.

2. Every OTHER agent step in plan.yml keeps the ceiling it had. This card
   raises exactly one number; the table below is the whole set, so a
   sibling ceiling that moves with it fails here rather than shipping
   unnoticed.

3. A planner run that finishes UNDER the ceiling is still a success —
   `subtype: success, is_error: false` records no death of any kind. The
   raise must not turn the healthy path into an error path.

`--max-turns` is a CEILING, not a target. Runs that finish in 40 turns
cost exactly what they cost today; raising it changes the bill only for
runs that would otherwise have died having already done the work.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "plan.yml"
sys.path.insert(0, str(REPO / "scripts"))

import check_agent_result as car  # noqa: E402

ACTION = "anthropics/claude-code-action"

# What DRE-3419's successful epic plan actually took, and the ceiling it was
# run against. Both are read off the run's own result record — see the
# module docstring.
DRE_3419_TURNS = 85
DRE_3419_CEILING = 80

# The step that plans an epic, and the ceiling this card sets on it.
EPIC_STEP = "claude"
EPIC_CEILING = 120

# Every agent step plan.yml can run, and the ceiling each one carries.
# `posta` is deliberately absent: its ceiling has been an EXPRESSION since
# DRE-3241 (sized per plan) and is asserted separately below.
#
# This table is the "everything else is unchanged" assertion for DRE-3450.
# Adding a step, or moving one of these numbers, is meant to fail here.
CEILINGS = {
    "oocritic": 20,      # Pre-approval critic — the one-off exit
    EPIC_STEP: EPIC_CEILING,  # Plan epic
    "prea": 40,          # First critic — round 1
    "replan": 60,        # Re-plan after send-back
    "preb": 40,          # First critic — round 2
    "postreplan": 60,    # Re-plan after the second critic sent it back
    "wave": 80,          # Wave route — write the wave plan
}

# The one step whose ceiling is chosen at run time rather than written here.
SIZED_STEP = "posta"
SIZED_EXPRESSION = "steps.postturns.outputs.max_turns"

_TURNS_RE = re.compile(r"--max-turns\s+(\S+)")


def _agent_steps() -> dict[str, dict]:
    """Every claude-code-action step in plan.yml, keyed by step id."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = {}
    for job in doc["jobs"].values():
        for step in job.get("steps") or []:
            if str(step.get("uses") or "").split("@")[0] == ACTION:
                steps[step.get("id")] = step
    return steps


def _turns_arg(step_id: str) -> str:
    steps = _agent_steps()
    assert step_id in steps, (
        f"no claude-code-action step id={step_id!r} in plan.yml — if the step "
        f"was renamed, update this test rather than deleting it; the ceiling "
        f"it pins is what stopped DRE-3450 from recurring"
    )
    args = str((steps[step_id].get("with") or {}).get("claude_args") or "")
    m = _TURNS_RE.search(args)
    assert m, f"plan.yml:{step_id} declares no --max-turns: {args!r}"
    return m.group(1)


def _ceiling(step_id: str) -> int:
    raw = _turns_arg(step_id)
    assert raw.isdigit(), (
        f"plan.yml:{step_id} takes its ceiling from {raw!r} rather than a "
        f"literal — this test can no longer tell what the step runs with"
    )
    return int(raw)


class TestEpicPlanningCeiling:
    def test_the_epic_planner_clears_the_run_that_died(self):
        """DRE-3419 finished its work at 85 turns and was failed for it.

        The ceiling must sit above where epic planning actually finishes,
        not on top of it — 80 was already below a SUCCESSFUL run.
        """
        ceiling = _ceiling(EPIC_STEP)
        assert ceiling == EPIC_CEILING, (
            f"the epic-planning step runs with --max-turns {ceiling}; "
            f"DRE-3450 sets it to {EPIC_CEILING} because DRE-3419's plan "
            f"completed successfully at {DRE_3419_TURNS} turns against a "
            f"ceiling of {DRE_3419_CEILING} and the job was failed anyway"
        )
        assert ceiling > DRE_3419_TURNS, (
            f"a ceiling of {ceiling} does not clear the {DRE_3419_TURNS} "
            f"turns DRE-3419 needed to finish"
        )

    def test_the_ceiling_leaves_headroom_rather_than_matching_the_incident(self):
        """Setting the ceiling to 85 would fail the next epic that is one
        step larger. The raise has to buy margin, not exactly one run."""
        ceiling = _ceiling(EPIC_STEP)
        assert ceiling >= DRE_3419_TURNS * 1.25, (
            f"ceiling {ceiling} sits within 25% of the observed "
            f"{DRE_3419_TURNS}-turn run; a budget riding the top of the "
            f"distribution is the bug DRE-3450 is fixing"
        )

    def test_it_is_still_a_ceiling(self):
        """Unbounded is not the fix. The cap exists so a looping planner
        cannot drain the account (the 2026-08-09 fleet-down lesson)."""
        assert 0 < _ceiling(EPIC_STEP) <= 200


class TestEveryOtherPlannerCeilingIsUnchanged:
    @pytest.mark.parametrize("step_id", sorted(CEILINGS))
    def test_each_step_carries_its_declared_ceiling(self, step_id):
        assert _ceiling(step_id) == CEILINGS[step_id], (
            f"plan.yml:{step_id} runs with --max-turns {_ceiling(step_id)}, "
            f"not the {CEILINGS[step_id]} this table declares. DRE-3450 "
            f"raises the epic planner's ceiling and NOTHING else"
        )

    def test_the_table_names_every_agent_step_in_the_workflow(self):
        """A ceiling added without a line here would be 'unchanged' by
        default, which is exactly what this table exists to prevent."""
        found = set(_agent_steps())
        assert found == set(CEILINGS) | {SIZED_STEP}, (
            f"plan.yml's agent steps are {sorted(found)}; this test knows "
            f"{sorted(set(CEILINGS) | {SIZED_STEP})}. Add the new step and "
            f"the ceiling it should carry"
        )

    def test_the_post_approval_review_is_still_sized_at_run_time(self):
        """DRE-3241 made this one an expression on purpose — it is sized
        from the plan it has to read. It must not become a literal here."""
        raw = _turns_arg(SIZED_STEP)
        args = str(
            (_agent_steps()[SIZED_STEP].get("with") or {}).get("claude_args")
        )
        assert not raw.isdigit(), (
            f"plan.yml:{SIZED_STEP} now carries a literal ceiling {raw!r}; "
            f"its budget is chosen per plan (DRE-3241)"
        )
        assert SIZED_EXPRESSION in args, (
            f"plan.yml:{SIZED_STEP} no longer reads its ceiling from "
            f"{SIZED_EXPRESSION}: {args!r}"
        )


class TestSuccessUnderTheCeilingIsStillSuccess:
    """The raise must not disturb the healthy path.

    `claude-code-action` is what refused DRE-3419's result; on our side the
    record of a run that finished under the ceiling has to keep reading as
    'no death', or the plan run would be reported failed for a different
    reason after the ceiling stopped doing it.
    """

    @pytest.mark.parametrize("turns", [1, 40, DRE_3419_TURNS, EPIC_CEILING])
    def test_a_successful_result_records_no_death(self, turns):
        execution = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": turns,
        }
        assert car.classify_death(execution) == car.DEATH_NONE
        assert not car.is_error_death(execution)
        assert not car.is_turn_exhaustion(execution)

    def test_exhausting_the_raised_ceiling_is_still_turn_exhaustion(self):
        """The other half: at 121 turns the run really did run out, and it
        must classify as the budget death it is — not as an API outage."""
        execution = {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "num_turns": EPIC_CEILING + 1,
        }
        assert car.classify_death(execution) == car.DEATH_TURN_EXHAUSTION


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
