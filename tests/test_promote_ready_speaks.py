"""Every exit from promote_ready says which gate held the card (DRE-2918).

`promote_ready` decides NOT to promote a card in six places, and five of them
were silent. One of those five froze a card for a sweep with nothing anywhere
saying so: at 23:09:37 on 2026-08-31, sweep run 33449449288 passed over
DRE-2826 and mentioned it zero times — `grep DRE-2826` over the whole run
returns nothing. The card was held by `if unmet: continue`, because its formal
`blockedBy` relation to DRE-2825 was then In Review. Correct decision, zero
output.

The source was a FORMAL RELATION, not prose, so this is not the prose problem
(DRE-2676) — any unmet blocker of any origin silenced the card.

What this module pins down:

  1. A STRUCTURAL guard. An AST sweep walks `promote_ready` and asserts every
     `continue`/`break` in its body sits in a block that also reports, with a
     single named exemption — the repo-mismatch skip, which is legitimately
     silent because the sweep is per-repo and every repo would otherwise print
     a line for every other repo's whole Backlog. Inspection does not count: a
     future silent exit has to fail the suite.
  2. The DRE-2826 case as a fixture — a Backlog card with one non-terminal
     formal blocker produces a line naming DRE-2825, its state, and that the
     source was a relation (mirroring the epic gate's lines, which already get
     this right).
  3. The WIP-budget `break` emits ONE summary line per sweep, naming how many
     candidates went unconsidered and the lowest-numbered one — a 200-card
     Backlog must not produce 200 lines.
  4. The hold-label and epic skips speak.
  5. The agent-blocker branch still prints its existing line — asserted, not
     assumed, because this card must not regress the one exit that was right.

Run: cd bureau-pipeline && python3 -m pytest tests/test_promote_ready_speaks.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import planning_shape  # noqa: E402
import reconcile  # noqa: E402

_SOURCE = (_SCRIPTS / "reconcile.py").read_text()

# The ONE exit allowed to say nothing, named by the condition that guards it.
# Naming the exemption — rather than counting silent exits — is the point: a
# second silent exit cannot hide behind it.
_SILENT_EXEMPTION = "card_repo(card) != REPO_SLUG"


# --------------------------------------------------------------------------
# The AST guard
# --------------------------------------------------------------------------
def _promote_ready(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "promote_ready"
    )


def _reporting_helpers(source: str) -> set[str]:
    """Module-level functions whose own body prints.

    `skip_bad_reference` and `_surface_once` report on behalf of the block that
    calls them, so a call to one IS the block speaking — resolved by reading
    the helper, never by adding a second name to the exemption list.
    """
    tree = ast.parse(source)
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "print"
            for c in ast.walk(node)
        )
    }


def _statement_blocks(func: ast.AST):
    """Every statement list under `func` — its body, each `if`/`else`/`try`
    branch, each `except` handler."""
    for node in ast.walk(func):
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(v, ast.stmt) for v in value):
                yield value


def _block_speaks(block: list[ast.stmt], reporters: set[str]) -> bool:
    for stmt in block:
        for call in ast.walk(stmt):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and (call.func.id == "print" or call.func.id in reporters)):
                return True
    return False


def _exempt_linenos(func: ast.AST) -> list[int]:
    """Lines of the exits guarded by the one named silent condition."""
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.If) and ast.unparse(node.test) == _SILENT_EXEMPTION:
            out += [s.lineno for s in node.body if isinstance(s, (ast.Continue, ast.Break))]
    return out


def _silent_exits(source: str = _SOURCE) -> list[int]:
    """Lines of every `continue`/`break` in promote_ready whose block is silent
    and is not the one named exemption."""
    func = _promote_ready(source)
    reporters = _reporting_helpers(source)
    exempt = set(_exempt_linenos(func))
    silent = []
    for block in _statement_blocks(func):
        for stmt in block:
            if not isinstance(stmt, (ast.Continue, ast.Break)):
                continue
            if stmt.lineno in exempt:
                continue
            if not _block_speaks(block, reporters):
                silent.append(stmt.lineno)
    return sorted(silent)


def test_no_exit_from_promote_ready_is_silent():
    """Every continue/break but the named exemption reports why it fired."""
    assert _silent_exits() == [], (
        "silent exit(s) in promote_ready at reconcile.py line(s) "
        f"{_silent_exits()} — a card held there is passed over with nothing "
        "anywhere saying so (DRE-2918)"
    )


def test_exactly_one_exit_is_exempt_and_it_is_the_repo_mismatch_skip():
    func = _promote_ready(_SOURCE)
    assert len(_exempt_linenos(func)) == 1


def test_the_silent_exit_carries_an_inline_comment_saying_why():
    """The repo-mismatch skip states its silence on the line itself, so the
    next reader of that `continue` does not have to ask."""
    lineno = _exempt_linenos(_promote_ready(_SOURCE))[0]
    line = _SOURCE.splitlines()[lineno - 1]
    assert "#" in line, f"reconcile.py:{lineno} is silent with no comment saying why"
    assert "silent" in line.split("#", 1)[1].lower()


def test_the_guard_catches_a_newly_added_silent_exit():
    """Non-vacuity: the sweep must FAIL on a silent exit, not merely pass on
    today's code."""
    synthetic = (
        "def promote_ready(active_count):\n"
        "    for card in candidates:\n"
        "        if card_repo(card) != REPO_SLUG:\n"
        "            continue\n"
        "        if unmet:\n"
        "            continue\n"
        "        print('promoted')\n"
    )
    assert _silent_exits(synthetic) == [6]


# --------------------------------------------------------------------------
# Behavior — the fixtures behind the guard
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this module's agent-bureau cards regardless of collection
    order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _card(identifier="DRE-2826", *, labels=("repo:agent-bureau",), relations=(),
          description="work", comments=(), parent=("DRE-2800", "In Progress")):
    """A Backlog card eligible on every ground except the one under test."""
    return {
        "identifier": identifier,
        "description": description,
        "createdAt": "2026-08-01T00:00:00.000Z",
        "parent": {"identifier": parent[0], "state": {"name": parent[1]}} if parent else None,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
                for i, s in relations
            ]
        },
    }


def _sweep(cards, active_count=0, card_state="Done"):
    """Run promote_ready over `cards` with every Linear read stubbed."""
    reconcile._write_failures.clear()
    reconcile._card_skips.clear()
    with patch.object(reconcile, "backlog_children", return_value=cards), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value=card_state), patch.object(
        reconcile.mid_epic, "last_green_light", return_value=None
    ), patch.object(reconcile.linear_ops, "cmd_advance"), patch.object(
        reconcile.linear_ops, "cmd_comment"
    ), patch.object(reconcile.linear_ops, "count_comments", return_value=0):
        return reconcile.promote_ready(active_count=active_count)


def _lines_naming(captured, identifier):
    return [ln for ln in captured.out.splitlines() if identifier in ln]


def test_unmet_formal_blocker_names_the_blocker_its_state_and_the_relation(capsys):
    """The DRE-2826 case: one non-terminal formal blocker, and the sweep says
    so — naming DRE-2825, In Review, and that the source was a relation."""
    card = _card("DRE-2826", relations=[("DRE-2825", "In Review")])
    promoted = _sweep([card], card_state="In Review")
    assert promoted == 0
    held = _lines_naming(capsys.readouterr(), "DRE-2826")
    assert len(held) == 1, f"expected exactly one line, got {held}"
    assert "DRE-2825" in held[0]
    assert "In Review" in held[0]
    assert "a formal blockedBy relation" in held[0]


def test_a_prose_only_blocker_speaks_as_a_DEFECT_not_as_a_hold(capsys):
    """A blocker declared only by a description line is a different fact with a
    different fix, and the line says which one it was. Since DRE-2676 that fact
    is "this card says something the board does not hold" — a defect — so the
    line names the claim and the refusal rather than a state nothing has."""
    card = _card("DRE-2900", description="Blocked by: DRE-2899\nwork")
    promoted = _sweep([card], card_state="In Progress")
    assert promoted == 0
    held = _lines_naming(capsys.readouterr(), "DRE-2900")
    assert len(held) == 1, f"expected exactly one line, got {held}"
    assert "DRE-2899" in held[0]
    assert reconcile.prose_blockers.CARD_TAG in held[0]


def test_wip_budget_break_reports_once_naming_the_shortfall(capsys):
    """Candidates are sorted ascending by card number, so the cards cut off are
    always the newest — one summary line per SWEEP, never one per card."""
    cards = [_card(f"DRE-90{n}") for n in (0, 1, 2, 3)]
    promoted = _sweep(cards, active_count=reconcile.MAX_WIP - 1)
    assert promoted == 1
    out = capsys.readouterr().out
    budget = [ln for ln in out.splitlines() if "budget" in ln.lower()]
    assert len(budget) == 1, f"expected one budget line per sweep, got {budget}"
    assert "3" in budget[0]  # three candidates went unconsidered
    assert "DRE-901" in budget[0]  # the lowest-numbered of them
    assert "DRE-902" not in budget[0] and "DRE-903" not in budget[0]


def test_hold_label_skip_speaks(capsys):
    card = _card("DRE-2901", labels=("repo:agent-bureau", reconcile.HOLD_LABEL))
    assert _sweep([card]) == 0
    held = _lines_naming(capsys.readouterr(), "DRE-2901")
    assert len(held) == 1, f"expected exactly one line, got {held}"
    assert reconcile.HOLD_LABEL in held[0]


def test_epic_skip_speaks(capsys):
    """An epic, said the way the sweep now reads one: the planning shape stamp
    (DRE-3044), not the `agent:planner` label the fixture also wears."""
    card = _card(
        "DRE-2902",
        labels=("repo:agent-bureau", "agent:planner"),
        comments=[planning_shape.shape_comment("epic", "a decomposition")],
    )
    assert _sweep([card]) == 0
    held = _lines_naming(capsys.readouterr(), "DRE-2902")
    assert len(held) == 1, f"expected exactly one line, got {held}"


def test_repo_mismatch_skip_stays_silent(capsys):
    """The one deliberate silence: another repo's Backlog card is passed over
    without a line, because every repo would print one for every other repo's
    every card, every fifteen minutes."""
    card = _card("DRE-2903", labels=("repo:deltasolv",))
    assert _sweep([card]) == 0
    assert _lines_naming(capsys.readouterr(), "DRE-2903") == []


def test_agent_blocker_branch_still_prints_its_line(capsys):
    """Do not regress the one exit that already spoke (DRE-1585)."""
    blocked = _card(
        "DRE-2904",
        comments=["🛑 Agent blocked: the upstream endpoint does not exist. Run: https://x"],
    )
    assert _sweep([blocked]) == 0
    held = _lines_naming(capsys.readouterr(), "DRE-2904")
    assert len(held) == 1, f"expected exactly one line, got {held}"
    assert "unresolved agent-blocker" in held[0]
