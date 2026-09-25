"""No build or fix job carries a literal turn count (DRE-4364).

THE BUG. `agent-fix.yml` chose its ceiling with a number typed into the YAML
and never read `config/turn-budgets.json` at all. A card could ask for more
turns and the fix loop would still run it at the literal: DRE-3926 carried
`turns:400` and DRE-4028 carried `turns:250`, and both died in the fix loop at
150 because the label the card carried reached the build job and nothing else.
The live case that closed into this card is portico PR #676 — a fix run that
finished its work at 151 turns, cost $18.21, and was failed by the ceiling.

`agent-task.yml` had the other half of the same fault. It selected through
`scripts/turn_budget.py`, then wrote `${{ steps.model.outputs.turns || 150 }}`
in three places: the literal was unreachable (the agent step is skipped
whenever the selecting step is, and `select` never exits non-zero), so it could
only ever mask a bug in the step that writes the output — with a number.

THIS FILE IS THE GUARD, not the fix. The fix is two edits in two workflows, and
an edit is undone by the next edit. What keeps the criterion true is a test
that reads both files and refuses a number: after this, a literal turn ceiling
in a build or fix job cannot merge without somebody deleting this file, which
is a visible act rather than a quiet one.

WHY THE OTHER SIX WORKFLOWS ARE NOT READ HERE. `plan.yml`, `verify.yml`,
`qa-review.yml`, `medic.yml`, `red-main-repair.yml` and `model-trial.yml` carry
their own literal ceilings for the planner, verifier, critic, medic, repair and
trial jobs. None of those is a build or fix job, none appears in the 110
turn-cap deaths this epic was cut from, and each was sized by its own card
(DRE-2422, DRE-2533, DRE-3450). Widening this list is a decision somebody makes
on evidence, not a tidy-up.

READ OFF THE PARSED YAML, never the raw text. Both files DISCUSS `--max-turns`
in their comments — the block above `agent-fix.yml`'s `claude_args` is most of
this card's reasoning — and a guard that could not tell a comment from a flag
would have to be written around, which is how a guard stops being read.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"

#: The two workflows that run a MODEL over a card's own work — the build job
#: and the fix job. The epic's criterion is read as these two.
BUILD_AND_FIX = ("agent-task.yml", "agent-fix.yml")

#: The ONE form a ceiling may take in those two files: the output of the step
#: that ran `turn_budget.py select`. No fallback, because a fallback is a
#: literal wearing a `||`.
SELECTED = "${{ steps.model.outputs.turns }}"

#: Everything to the end of the line after the flag. Not `\S+` — the expression
#: above contains spaces, and a regex that stopped at the first one would read
#: every compliant ceiling as the token `${{` and pass on nothing.
_MAX_TURNS_RE = re.compile(r"--max-turns\b[ \t]*([^\n]*)")

#: What the flag may never be followed by, stated as its own question so the
#: failure names the fault rather than a diff of two long strings.
_LITERAL_RE = re.compile(r"--max-turns\s+\d")

#: How many flags this file expects to find at all. Three agent steps in
#: agent-task.yml (first attempt plus two retries) and one in agent-fix.yml.
#: A guard that silently found nothing would pass forever.
EXPECTED_FLAGS = 4


def _strings(node):
    """Every string value in a parsed YAML document, depth-first.

    The whole document rather than `with.claude_args` alone: a ceiling can be
    written in a `run:` block or assembled into an env var, and a guard that
    only looked where today's flag sits would be a guard against today.
    """
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _ceilings(workflow: str) -> list[str]:
    """Every `--max-turns` argument in `workflow`, as written."""
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text())
    found: list[str] = []
    for text in _strings(doc):
        for m in _MAX_TURNS_RE.finditer(text):
            found.append(m.group(1).strip())
    return found


def _flag_lines(workflow: str) -> list[str]:
    """The flag and its argument, one entry per occurrence, for the messages."""
    return [f"--max-turns {arg}" for arg in _ceilings(workflow)]


def test_no_build_or_fix_job_carries_a_literal_turn_count():
    """The criterion, stated as the test that keeps it true."""
    for workflow in BUILD_AND_FIX:
        for arg in _ceilings(workflow):
            assert not _LITERAL_RE.match(f"--max-turns {arg}"), (
                f"{workflow} declares `--max-turns {arg}` — a number typed "
                f"into the YAML. A card that carries a 'turns:' label cannot "
                f"reach it, which is how DRE-3926 (turns:400) and DRE-4028 "
                f"(turns:250) both died in the fix loop at 150. The ceiling "
                f"comes from the step that ran turn_budget.py select: "
                f"`--max-turns {SELECTED}`."
            )


def test_every_ceiling_is_the_selected_budget_with_no_fallback():
    """`|| 400` is the same literal wearing an operator.

    It cannot fire on any path a run can take — every agent step carries the
    same `if` as the step that writes `turns`, and `turn_budget.py select`
    never exits non-zero — so the only thing it can do is convert a bug in the
    selecting step into a silently different ceiling.
    """
    for workflow in BUILD_AND_FIX:
        for arg in _ceilings(workflow):
            assert arg == SELECTED, (
                f"{workflow} declares `--max-turns {arg}`; the only permitted "
                f"form is `--max-turns {SELECTED}`. A `||` fallback here can "
                f"never be reached by a correct run and can only mask a broken "
                f"one — with the literal this card exists to delete."
            )


def test_the_guard_actually_reads_the_flags_it_claims_to():
    """A guard that finds nothing passes forever.

    Pin the count so a renamed step, a restructured `claude_args` or a regex
    that quietly stopped matching shows up as a failure here rather than as a
    green run proving nothing.
    """
    found = [line for wf in BUILD_AND_FIX for line in _flag_lines(wf)]
    assert len(found) >= EXPECTED_FLAGS, (
        f"expected at least {EXPECTED_FLAGS} `--max-turns` flags across "
        f"{', '.join(BUILD_AND_FIX)} (three agent steps in agent-task.yml, one "
        f"in agent-fix.yml) and found {len(found)}: {found}. Either a model "
        f"step lost its ceiling or this guard stopped reading them."
    )


def test_the_comment_prose_is_not_what_is_being_read():
    """The files argue about `--max-turns` in prose; the guard reads YAML.

    `agent-task.yml`'s budget comment and `agent-fix.yml`'s block above
    `claude_args` both write the flag inside a comment. If this guard ever
    starts reading raw text it will fail on the reasoning rather than on a
    ceiling, and the next author will route around it.
    """
    for workflow in BUILD_AND_FIX:
        raw = (WORKFLOWS / workflow).read_text()
        in_comments = sum(
            line.count("--max-turns") for line in raw.splitlines()
            if line.lstrip().startswith("#")
        )
        if not in_comments:
            continue
        assert len(_ceilings(workflow)) == raw.count("--max-turns") - in_comments, (
            f"{workflow} writes `--max-turns` {in_comments} time(s) in a "
            f"comment and {raw.count('--max-turns')} time(s) in all, but the "
            f"guard read {len(_ceilings(workflow))} ceiling(s). It must read "
            f"the parsed document: a guard that failed on the reasoning above "
            f"a flag is a guard the next author deletes."
        )
