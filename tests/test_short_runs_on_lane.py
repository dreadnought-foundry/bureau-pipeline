"""The short-job runner lane (DRE-3887).

On 2026-09-13 twenty-two agent-bureau Merge Gate runs sat queued from 16:40 PT,
about an hour. All three light runners were busy with long Claude jobs — Agent
Task execute, Verify, QA Review — while the three heavy runners sat idle. The
cause was one expression: every job in `merge-gate.yml` and `linear-sync.yml`
read `vars.BUREAU_RUNS_ON`, the SAME variable that routes the 20-minute agent
and critic jobs, so a 30-second gate queued behind them for a slot.

The lane this file pins:

* Every job of the two bookkeeping reusables reads
  `BUREAU_SHORT_RUNS_ON || BUREAU_RUNS_ON || '["ubuntu-latest"]'`. That is
  today's expression with ONE variable in front of it, so a repo that has not
  set the new variable renders byte-identically to what it renders today — the
  change is inert until a repo opts in, and `test_an_unset_short_variable_
  renders_todays_runner` below is the proof, computed rather than asserted.
* No long Claude job reads the short variable. `agent-task`, `qa-review`,
  `verify`, `plan` and `medic` are the jobs whose 20 minutes the gates were
  queueing behind; routing THEM to the short pool would recreate the incident
  with the lanes swapped.

The scope is exactly these two files. Another sub-minute job that would fit the
lane is a follow-up card, not a tidy-up here — which is why the job list below
is pinned by name rather than discovered.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runs_on_switchable import (  # noqa: E402
    SHORT_LANE,
    SHORT_SWITCHABLE,
    SWITCHABLE,
    _load,
    _runner_jobs,
)

SHORT_VAR = "BUREAU_SHORT_RUNS_ON"

# Every job of the two files, named. The card's first criterion is "a test pins
# that list", so a job added to either file without a runner decision fails
# here rather than inheriting one.
SHORT_JOBS = {
    "merge-gate.yml": {"resolve", "evaluate"},
    "linear-sync.yml": {"card-done", "conflict-sweep"},
}

# The long Claude jobs the card names — none of them may read the short
# variable. `agent-fix.yml`, `groomer.yml`, `plan.yml`'s siblings and the rest
# of the fleet are covered by the sweep below; these five are pinned by name
# because they are the ones the incident was about.
LONG_CLAUDE = ["agent-task.yml", "qa-review.yml", "verify.yml", "plan.yml", "medic.yml"]

_EXPR = re.compile(r"^\$\{\{ fromJSON\((?P<chain>.+?)\) \}\}$")


def render_runs_on(expr: str, variables: dict[str, str]) -> list[str]:
    """Evaluate a `${{ fromJSON(a || b || '<json>') }}` runs-on the way GitHub
    does: `||` yields the first operand that is not falsy, an unset repository
    variable is the empty string (falsy), and `fromJSON` parses the winner.

    Deliberately not a general expression engine — it understands exactly the
    one shape these workflows use, so an expression of any other shape fails
    the parse rather than being silently approximated.
    """
    match = _EXPR.match(expr)
    assert match, f"not a fromJSON runs-on expression: {expr!r}"
    for operand in (o.strip() for o in match.group("chain").split("||")):
        if operand.startswith("vars."):
            value = variables.get(operand[len("vars.") :], "")
        else:
            assert operand.startswith("'") and operand.endswith("'"), operand
            value = operand[1:-1]
        if value:
            return json.loads(value)
    raise AssertionError(f"every operand of {expr!r} was falsy")


def _jobs(name: str) -> dict[str, str]:
    return dict(_runner_jobs(_load(WORKFLOWS / name)))


# --------------------------------------------------------------------------
# the short lane reads the new expression
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_the_short_lane_is_the_job_list_the_card_names(name: str) -> None:
    assert set(_jobs(name)) == SHORT_JOBS[name], (
        f"{name}'s runner jobs moved; the short lane is pinned by name so a "
        f"new job cannot join or leave it unnoticed"
    )


@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_every_short_job_reads_the_short_variable_first(name: str) -> None:
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if runs_on != SHORT_SWITCHABLE
    }
    assert not wrong, (
        f"{name}: every job must read {SHORT_SWITCHABLE} so a repo can route "
        f"its sub-minute jobs off the long-job runners. Wrong: {wrong}"
    )


def test_the_short_lane_is_exactly_two_files() -> None:
    """The scope the CEO signed on 2026-09-14: merge-gate and linear-sync, and
    nothing else. Widening the lane is a decision, not a refactor."""
    assert SHORT_LANE == set(SHORT_JOBS)
    mentions = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if SHORT_VAR in path.read_text()
    }
    assert mentions == SHORT_LANE, (
        f"only {sorted(SHORT_LANE)} may name {SHORT_VAR}; found {sorted(mentions)}"
    )


# --------------------------------------------------------------------------
# no long Claude job reads it
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", LONG_CLAUDE)
def test_long_claude_jobs_never_read_the_short_variable(name: str) -> None:
    path = WORKFLOWS / name
    assert SHORT_VAR not in path.read_text(), (
        f"{name} runs Claude for minutes at a time; routing it to the short "
        f"pool recreates DRE-3875 with the lanes swapped"
    )
    wrong = {
        job_id: runs_on
        for job_id, runs_on in _jobs(name).items()
        if runs_on != SWITCHABLE
    }
    assert not wrong, f"{name} must keep reading {SWITCHABLE}. Wrong: {wrong}"


# --------------------------------------------------------------------------
# the fallback is exactly today's behaviour
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(SHORT_JOBS))
def test_an_unset_short_variable_renders_todays_runner(name: str) -> None:
    """The third acceptance criterion, computed: with `BUREAU_SHORT_RUNS_ON`
    unset, every short job renders what the old expression renders — for a repo
    that sets nothing AND for a repo already on the pool."""
    for variables in (
        {},
        {"BUREAU_RUNS_ON": '["self-hosted", "macos", "bureau"]'},
    ):
        today = render_runs_on(SWITCHABLE, variables)
        for job_id, runs_on in _jobs(name).items():
            assert render_runs_on(runs_on, variables) == today, (
                f"{name}:{job_id} changed where it runs for a repo that has "
                f"not set {SHORT_VAR} (vars={variables})"
            )


def test_the_short_variable_wins_when_a_repo_sets_it() -> None:
    """…and the lane is not decoration: set it and the short jobs move, while
    the long jobs stay exactly where they were."""
    variables = {
        "BUREAU_SHORT_RUNS_ON": '["self-hosted", "macos", "bureau-short"]',
        "BUREAU_RUNS_ON": '["self-hosted", "macos", "bureau"]',
    }
    assert render_runs_on(SHORT_SWITCHABLE, variables) == [
        "self-hosted",
        "macos",
        "bureau-short",
    ]
    assert render_runs_on(SWITCHABLE, variables) == ["self-hosted", "macos", "bureau"]


def test_the_default_is_still_the_hosted_runner() -> None:
    assert render_runs_on(SHORT_SWITCHABLE, {}) == ["ubuntu-latest"]
