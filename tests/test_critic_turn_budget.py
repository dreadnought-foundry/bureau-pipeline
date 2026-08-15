"""RED-first tests for DRE-2422 — the critic's turn budget.

THE BUG (portico PR #273, 2026-08-13). The QA critic ran on Sonnet 5 with
`--max-turns 40`. Both the first attempt and the retry ended identically:

    "subtype": "error_max_turns", "is_error": true, "num_turns": 41,
    "total_cost_usd": 2.0498711
    ##[error]Execution failed: Reached maximum number of turns (40)

$4.05 of inference, no verdict, PR blocked. The model choice was correct —
Sonnet 5 is top of the ladder for an advisory role, and the 2026-08-09 Fable
hazard is closed. The CEILING was the problem: 40 turns was sized for an
older, terser model. Sampling portico's critic runs from that same hour:

    12, 18, 19, 31, 41(success), 41(error_max_turns), 41(error_max_turns)

A run that SUCCEEDS at 41 has exactly zero margin left. The ceiling was not
"generous with an occasional overflow" — the critic was riding it, and any
PR above about 1k changed lines fell off. #273 is 7 files / +1184.

TWO THINGS THIS FILE PINS:

1. The budget is high enough to clear the observed distribution with real
   headroom. Every sibling agent already sits higher: verify 60, agent-fix
   60, red-main-repair 100, agent-task 150. The critic — the one agent that
   must read an ENTIRE diff before it may speak — had the smallest budget of
   any of them.

2. The retry budget is STRICTLY HIGHER than the first attempt's. This is the
   deeper bug and the more durable assertion: #273's retry re-ran the same
   agent, on the same diff, with the same 40 turns, and died at 41 again.
   `error_max_turns` is DETERMINISTIC — a retry that changes no input cannot
   change the outcome. It can only re-spend the money. Any retry that exists
   to recover from exhausting a resource must come back with more of it.

`--max-turns` is a CEILING, not a target. Runs that finish in 12 or 19 turns
cost exactly what they cost today; raising it changes the bill only for runs
that would otherwise have died producing nothing.

DRE-2466 UPDATE. The ceiling is no longer a literal in the YAML: the workflow
sizes the PR first and the ceiling comes from `scripts/pr_size_strategy.py`,
so a file-list review of a 17k-line diff gets the turns that work needs while
a two-file change keeps today's budget. Every assertion below now runs
against EVERY strategy in that table rather than against one literal — the
properties DRE-2422 pinned are strictly harder to break, not easier.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
sys.path.insert(0, str(REPO / "scripts"))

import pr_size_strategy as pss  # noqa: E402

# The size step's outputs, in the order pr_size_strategy.turn_budget()
# returns them. The workflow interpolates these into claude_args.
_TURN_OUTPUTS = ("max_turns", "retry_max_turns")

# The observed high-water mark for a critic run that actually finished.
OBSERVED_CEILING_HIT = 41

# What verify.yml and agent-fix.yml already give their agents.
SIBLING_FLOOR = 60


def _claude_args(workflow: str, job: str, step_id: str) -> str:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    for step in doc["jobs"][job]["steps"]:
        if step.get("id") == step_id:
            return step["with"]["claude_args"]
    raise AssertionError(
        f"no step id={step_id!r} in {workflow} job {job!r} — if the step was "
        f"renamed, update this test rather than deleting it; the budget it "
        f"pins is what stopped DRE-2422 from recurring"
    )


# `--max-turns 80` (a literal) or `--max-turns ${{ steps.size.outputs.X }}`
# (DRE-2466 — the budget the size step selected for this PR).
_TURNS_RE = re.compile(
    r"--max-turns\s+(?:(\d+)|\$\{\{\s*steps\.size\.outputs\.(\w+)\s*\}\})"
)


def _max_turns(workflow: str, job: str, step_id: str, strategy: str) -> int:
    """The ceiling this step actually runs with, on the given strategy.

    A literal is taken as written. An expression is RESOLVED through the
    same table the workflow reads at run time, so these assertions keep
    pinning effective values rather than the presence of a placeholder —
    an indirection that hid the real number would retire the guard.
    """
    args = _claude_args(workflow, job, step_id)
    m = _TURNS_RE.search(args)
    assert m, f"{workflow}:{step_id} declares no --max-turns: {args!r}"
    if m.group(1):
        return int(m.group(1))
    name = m.group(2)
    assert name in _TURN_OUTPUTS, (
        f"{workflow}:{step_id} takes its ceiling from an unknown size-step "
        f"output {name!r} — this test can no longer tell what the critic "
        f"actually runs with"
    )
    return pss.turn_budget(strategy)[_TURN_OUTPUTS.index(name)]


@pytest.fixture(params=sorted(pss.TURN_BUDGET))
def strategy(request) -> str:
    """Every review strategy the workflow can select (DRE-2466)."""
    return request.param


@pytest.fixture
def attempt1(strategy) -> int:
    return _max_turns("qa-review.yml", "review", "critic", strategy)


@pytest.fixture
def retry(strategy) -> int:
    return _max_turns("qa-review.yml", "review", "critic_retry", strategy)


class TestCriticTurnBudget:
    def test_first_attempt_clears_the_observed_ceiling(self, attempt1):
        """40 was not merely tight — a SUCCESSFUL run used all 41. The budget
        must sit clearly above where the critic actually runs, not on top of
        it."""
        assert attempt1 > OBSERVED_CEILING_HIT, (
            f"the critic's turn budget is {attempt1}; runs were observed "
            f"finishing at {OBSERVED_CEILING_HIT} turns and dying at the "
            f"same number. A ceiling at the top of the distribution blocks "
            f"every large PR (portico #273, DRE-2422)."
        )

    def test_first_attempt_is_at_least_as_generous_as_its_siblings(
        self, attempt1
    ):
        """The critic must read the WHOLE diff before it may speak — the
        prompt forbids rationing findings across rounds. It should not be
        the most constrained agent in the fleet.

        The floor is verify.yml's and agent-fix.yml's 60, asserted as a
        literal rather than read out of those files on purpose: this test
        must not fail because a workflow it does not own moved a step.
        """
        assert attempt1 >= SIBLING_FLOOR, (
            f"critic budget {attempt1} is below the {SIBLING_FLOOR} that "
            f"verify.yml and agent-fix.yml already give their agents; the "
            f"one agent that must read the entire diff should not be the "
            f"most constrained one"
        )

    def test_retry_budget_is_strictly_higher_than_the_first_attempt(
        self, attempt1, retry
    ):
        """THE regression guard for this class.

        `error_max_turns` is deterministic. Re-running the same review over
        the same diff with the same ceiling produces the same death — which
        is exactly what portico #273 did, twice, for $4.05. A retry whose
        only job is to recover from running out of a resource MUST return
        with more of that resource, or it is just a second invoice.
        """
        assert retry > attempt1, (
            f"the critic retry gets {retry} turns and the first attempt "
            f"{attempt1}. A retry that changes nothing cannot succeed "
            f"against a deterministic max-turns failure (DRE-2422)."
        )

    def test_both_attempts_still_declare_a_ceiling(self, attempt1, retry):
        """Unbounded is not the fix. A runaway critic is its own outage —
        the ceiling exists so a looping agent cannot drain the account
        (the 2026-08-09 fleet-down lesson)."""
        for name, value in (("attempt 1", attempt1), ("retry", retry)):
            assert 0 < value <= 200, (
                f"critic {name} budget {value} is not a sane ceiling"
            )

    def test_the_two_attempts_stay_otherwise_in_sync(self):
        """qa-review.yml carries the attempt-1 and retry `with:` blocks as
        deliberate duplicates — GitHub Actions has no YAML anchors, so the
        file says in a comment that they MUST be kept in sync. The turn
        budget is now the ONE intended difference; anything else drifting
        apart is the bug that comment warns about."""
        a1 = _claude_args("qa-review.yml", "review", "critic")
        rt = _claude_args("qa-review.yml", "review", "critic_retry")
        strip = lambda s: _TURNS_RE.sub("--max-turns N", s)  # noqa: E731
        assert strip(a1) == strip(rt), (
            "the critic attempt-1 and retry claude_args have drifted apart "
            "beyond their turn budget — the file's own comment requires "
            "them kept in sync (no YAML anchors available)"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
