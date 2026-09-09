"""RED-first guard: a `run:` block carrying `${{ }}` stays under GitHub's expression ceiling (DRE-3484).

THE OUTAGE (live, 2026-09-09 06:15 PT — 09:35 PT). Every `agent-execute`
dispatch in the fleet was refused by GitHub before a job started:

    Invalid workflow file: .github/workflows/agent-task.yml#L21
    error parsing called workflow
    "dreadnought-foundry/bureau-pipeline/.github/workflows/agent-task.yml@stable"
    (source tag with sha:a1bf0625) : (Line: 718, Col: 14):
    Exceeded max expression length 21000

No card could be built in any repo on the channel for three hours and twenty
minutes. The critic, the fix agent, the merge gate, reconcile and the release
train were untouched — they live in files under the ceiling — so PRs already in
flight went on merging while nothing new could start, which is exactly why it
took a morning to notice.

WHY IT HAPPENS. A `run:` block that contains `${{ }}` is not a string to GitHub.
It compiles the WHOLE script into one `format()` expression — the entire script
becomes the format string, with every literal `{` and `}` doubled to escape it —
and a single expression may not exceed 21,000 characters. The script's own
length is therefore the expression's length. `agent-task.yml`'s last step,
`Report result to Linear`, grew for months a few hundred characters at a time
and crossed the line in one PR:

    2ed1b0b4  the channel the last good build used   19,256   under
    3b3c143e  first commit of PR #327 (DRE-3262)     22,203   over
    a1bf0625  the channel this morning               23,260   over

NOTHING CAUGHT IT, TWICE OVER. GitHub validates a called workflow only at
dispatch, in production — the file is valid YAML and every ordinary linter
passes it. And `bureau-harness`, the gate that proves a commit before
`promote-channel` advances `stable` onto it, installs four stubs (`ci`,
`qa-review`, `merge-gate`, `reconcile`) and does not install `agent-task.yml` at
all: zero references in that repo under any spelling. The one workflow that
builds every card is the one workflow the pre-production gate never parses. That
second gap is its own card; this file closes the first.

WHAT THIS GUARD IS NOT. It is not a style rule about long scripts. A `run:`
block with NO `${{ }}` in it is never compiled as an expression and has no
ceiling at any length — which is why the fix for `Report result to Linear` moved
its five substitutions to `env:` and did not touch the script. So the guard
measures only the blocks the ceiling can actually reach, and the remedy it names
is `env:`, never deletion.

THE BUDGET is 18,000, three thousand below GitHub's 21,000. At landing
`agent-fix.yml` carries blocks at 17,550 and 16,694 — under the budget, but with
less room than `agent-task.yml` had a week ago. They are the next occurrence,
and this guard is what will stop it: the commit that pushes either past 18,000
fails here rather than in the fleet.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

# GitHub's hard ceiling on a single compiled expression.
GITHUB_MAX_EXPRESSION = 21_000

# What we allow ourselves, leaving 3,000 characters of headroom. PR #327 added
# ~4,000 characters in one go; a budget without room for one careless PR is not
# a budget.
BUDGET = 18_000

WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"


def compiled_expression_length(script: str) -> int:
    """Characters GitHub counts when it compiles ``script`` into ``format()``.

    The script becomes the format string, so its own length counts, and every
    literal brace is doubled to escape it — a shell script full of ``${VAR}``
    pays twice for each one. The YAML block scalar has already stripped the
    file's indentation by the time the parser hands us the string, so what we
    measure here is what GitHub measures.
    """
    return len(script) + script.count("{") + script.count("}")


def run_blocks() -> list[tuple[str, str, str, str]]:
    """``(file, job, step, script)`` for every ``run:`` in every workflow."""
    found: list[tuple[str, str, str, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        for job_name, job in (doc.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                script = step.get("run")
                if isinstance(script, str):
                    name = step.get("name") or f"step {index}"
                    found.append((path.name, job_name, name, script))
    return found


def test_there_are_run_blocks_to_measure() -> None:
    """A guard that silently measures nothing prints OK forever."""
    blocks = run_blocks()
    assert len(blocks) > 20, f"only {len(blocks)} run blocks found — is the glob right?"
    assert any(
        "${{" in script for *_, script in blocks
    ), "no interpolated run block found — the guard would be vacuous"


@pytest.mark.parametrize(
    "path,job,step,script",
    [pytest.param(*b, id=f"{b[0]}::{b[2]}") for b in run_blocks() if "${{" in b[3]],
)
def test_an_interpolated_run_block_stays_within_budget(
    path: str, job: str, step: str, script: str
) -> None:
    size = compiled_expression_length(script)
    assert size <= BUDGET, (
        f"{path} job '{job}' step '{step}': this run block contains ${{{{ }}}} and "
        f"compiles to a {size:,}-character expression, over our {BUDGET:,} budget "
        f"(GitHub refuses the whole file above {GITHUB_MAX_EXPRESSION:,}, and refuses "
        f"it at DISPATCH — every consumer repo breaks at once, with no job and no log).\n"
        f"FIX: move the ${{{{ }}}} substitutions out of the script into the step's "
        f"`env:` and read them as shell variables. A run block with no substitutions "
        f"is never compiled as an expression, so the ceiling stops applying at any "
        f"length. Do not delete script to get under the number."
    )


def test_the_measurement_counts_braces_the_way_format_escapes_them() -> None:
    """Pin the arithmetic itself, so a refactor cannot quietly make it lenient."""
    assert compiled_expression_length("abc") == 3
    assert compiled_expression_length("${VAR}") == 8  # 6 chars + 2 braces
    assert compiled_expression_length("{}") == 4


def test_the_budget_leaves_real_headroom_under_githubs_ceiling() -> None:
    """A budget set at the ceiling is not a guard, it is a coin flip."""
    assert GITHUB_MAX_EXPRESSION - BUDGET >= 3_000
