"""Two guards on workflow `run:` blocks: an interpolated block stays under GitHub's expression ceiling (DRE-3484), and no block exceeds 8,000 characters (DRE-3488).

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

WHAT THE EXPRESSION BUDGET IS NOT. The DRE-3484 budget is not a style rule
about long scripts. A `run:` block with NO `${{ }}` in it is never compiled as
an expression, so GitHub's 21,000 ceiling never reaches it — which is why the
fix for `Report result to Linear` moved its five substitutions to `env:` and did
not touch the script. So the budget test measures only the blocks GitHub's
ceiling can actually reach, and the remedy it names is `env:`, never deletion.
The length of every block, interpolated or not, is a separate rule: the
DRE-3488 run-block ceiling below.

THE BUDGET is 18,000, three thousand below GitHub's 21,000. When this guard
landed, `agent-fix.yml` carried blocks at 17,550 and 16,694. Both were under the
budget but had less room than `agent-task.yml` had a week before, so they were
named here as the next occurrence. That is history now. DRE-3488 moved the
Resolve step's shell into `scripts/resolve_fix_pr.sh` with its seven
substitutions in `env:` (DRE-5224), and the Report step into
`scripts/report_fix_result.sh` (DRE-5225), so neither one interpolates anymore.

THE RUN-BLOCK CEILING (DRE-3488). No `run:` block in `.github/workflows/*.yml`
may be longer than 8,000 characters, whether or not it interpolates, and there
is no exceptions table. The expression budget above only stops a block at the
point where GitHub would refuse it, which let the shell inside the workflows
grow a few hundred characters at a time. On `main` at db6adf6 (2026-10-01)
there were 324 `run:` blocks and five were over 8,000: 28,856, 26,127, 22,799,
22,418 and 17,921. The merge gate's block had grown 2,733 characters in a
single day. DRE-3488's five move cards (DRE-5223, DRE-5224, DRE-5225, DRE-5383
and DRE-5384) took those five shells out into `scripts/<name>.sh` behind a
one-line delegation step. When this ceiling landed, measured at bureau-pipeline
d07a8b0 (2026-10-02), there were 338 blocks, none over the line, and the largest
was 7,083 (`linear-sync.yml`, `Card → Done`). The largest interpolated block
compiled to 4,038 (`agent-fix.yml`, `Escalate checks the loop structurally
cannot fix`).

The remedy for a block over the ceiling is to move it, not to shorten it:
`python3 scripts/step_shell.py move` puts the shell in `scripts/<name>.sh`
behind the delegation line that `scripts/step_shell.py` defines, and any
`${{ }}` inputs are passed through `env:`. Deleting history comments to get
under the number throws away the record of why the shell looks the way it
does, and the ceiling exists to keep that record somewhere better.

The DRE-3484 budget stays as the backstop for interpolated blocks. The ceiling
is tighter for every block today, but the budget measures what GitHub actually
counts and does not depend on this number staying where it is.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import step_shell  # noqa: E402

# GitHub's hard ceiling on a single compiled expression.
GITHUB_MAX_EXPRESSION = 21_000

# What we allow ourselves, leaving 3,000 characters of headroom. PR #327 added
# ~4,000 characters in one go; a budget without room for one careless PR is not
# a budget.
BUDGET = 18_000

# The most raw characters any `run:` block may carry, interpolated or not
# (DRE-3488). Above it the shell belongs in `scripts/<name>.sh`.
RUN_BLOCK_CEILING = 8_000

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


def run_blocks_over_ceiling(
    blocks: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, str, int]]:
    """``(file, job, step, size)`` for every block longer than the ceiling.

    The size is the raw script length, with no brace doubling: the ceiling is
    about how much shell lives in a workflow, not about what GitHub compiles.
    """
    return [
        (path, job, step, len(script))
        for path, job, step, script in blocks
        if len(script) > RUN_BLOCK_CEILING
    ]


def move_via(workflow: str) -> str | None:
    """The ``--via`` a ``step_shell.py move`` in ``workflow`` needs, or None.

    Read from step_shell's own move table, never restated here: a workflow
    whose moves there carry ``via="pipeline-dir"`` (``qa-review.yml``, whose
    pipeline checkout moves out of the working tree) needs that flag, because
    without it ``move`` writes the ``.bureau-pipeline`` line, and that path
    does not exist when the step runs.
    """
    vias = {m.via for m in step_shell.REHEARSAL if m.workflow == workflow and m.via}
    assert len(vias) <= 1, f"{workflow}: step_shell's move table disagrees with itself: {vias}"
    return vias.pop() if vias else None


def ceiling_failure_message(path: str, job: str, step: str, size: int) -> str:
    """What the ceiling test prints for one block over the line, remedy included."""
    via = move_via(path)
    via_flag = f" --via {via}" if via else ""
    line = step_shell._LINE[via].format(name="<name>")
    return (
        f"{path} job '{job}' step '{step}': this run block is {size:,} "
        f"characters, over the {RUN_BLOCK_CEILING:,}-character ceiling for a run "
        f"block (DRE-3488).\n"
        f"FIX: move the shell into scripts/<name>.sh with\n"
        f"    python3 scripts/step_shell.py move --root . --workflow {path} "
        f"--step '{step}' --script <name>{via_flag} [--env NAME=EXPR ...]\n"
        f"which leaves the delegation line `{line}` in its place, and pass any "
        f"${{{{ }}}} inputs through the step's `env:`. Do not delete history "
        f"comments to get under the number: they are the record of why the shell "
        f"looks the way it does, and they move with it."
    )


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


@pytest.mark.parametrize(
    "path,job,step,script",
    [pytest.param(*b, id=f"{b[0]}::{b[1]}::{b[2]}") for b in run_blocks()],
)
def test_no_run_block_exceeds_the_ceiling(
    path: str, job: str, step: str, script: str
) -> None:
    over = run_blocks_over_ceiling([(path, job, step, script)])
    assert not over, ceiling_failure_message(path, job, step, len(script))


def test_the_printed_remedy_carries_via_pipeline_dir_only_for_qa_review() -> None:
    """The command the ceiling prints must produce a delegation line that runs.

    `qa-review.yml` moves its pipeline checkout out of the working tree, so a
    move there without `--via pipeline-dir` writes a `.bureau-pipeline` path
    that does not exist when the step runs, and the review breaks fleet-wide.
    """
    qa = ceiling_failure_message("qa-review.yml", "review", "Some step", 8_001)
    assert "--script <name> --via pipeline-dir " in qa
    assert 'bash "$PIPELINE_DIR/scripts/<name>.sh"' in qa
    assert ".bureau-pipeline" not in qa

    other = ceiling_failure_message("linear-sync.yml", "card-done", "Card → Done", 8_001)
    assert "--via" not in other
    assert "bash .bureau-pipeline/scripts/<name>.sh" in other
    assert "$PIPELINE_DIR" not in other

    for message in (qa, other):
        assert "8,001 characters" in message
        assert "python3 scripts/step_shell.py move" in message
        assert "Do not delete history comments" in message


def test_the_ceiling_reports_one_character_over_and_measures_real_blocks() -> None:
    """Pin the ceiling's arithmetic, so the guard cannot go vacuous."""
    over = "x" * (RUN_BLOCK_CEILING + 1)
    at = "x" * RUN_BLOCK_CEILING
    assert len(over) == 8_001 and len(at) == 8_000
    reported = run_blocks_over_ceiling(
        [
            ("synthetic.yml", "job", "over", over),
            ("synthetic.yml", "job", "at", at),
        ]
    )
    assert reported == [("synthetic.yml", "job", "over", 8_001)]
    blocks = run_blocks()
    assert len(blocks) > 20, f"only {len(blocks)} run blocks found — is the glob right?"


def test_the_measurement_counts_braces_the_way_format_escapes_them() -> None:
    """Pin the arithmetic itself, so a refactor cannot quietly make it lenient."""
    assert compiled_expression_length("abc") == 3
    assert compiled_expression_length("${VAR}") == 8  # 6 chars + 2 braces
    assert compiled_expression_length("{}") == 4


def test_the_budget_leaves_real_headroom_under_githubs_ceiling() -> None:
    """A budget set at the ceiling is not a guard, it is a coin flip."""
    assert GITHUB_MAX_EXPRESSION - BUDGET >= 3_000
