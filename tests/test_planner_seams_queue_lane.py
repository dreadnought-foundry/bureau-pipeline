"""The planner's seams name the queue through the contract (DRE-6746).

Six scripts on the planner's side of the board named `Backlog` as a literal.
The queue is `lane_contract`'s: read under every name it carries
(`QUEUE_LANES`) and written as the planning exit's destination (`QUEUE_LANE`),
which is `Backlog` until DRE-6742 moves the exit and `Queued` after (DRE-6741).

Two things are pinned here, over each script's own source:

  1. No string constant in it contains `Backlog` — read with
     `lane_contract._scan_python`, the AST reader the contract's own vocabulary
     check uses, so a docstring counts and a `#` comment recording an incident
     does not.
  2. Every script that reads or writes the queue imports `QUEUE_LANE` or
     `QUEUE_LANES` from `lane_contract` rather than spelling a lane.

Each script's behavior under both names is pinned beside its other tests.

Run: cd bureau-pipeline && python3 -m pytest tests/test_planner_seams_queue_lane.py -v
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import lane_contract  # noqa: E402

SEAMS = (
    "plan_child_verdicts.py",
    "epic_split.py",
    "mid_epic.py",
    "structural_repair.py",
    "validate_card.py",
    "split_ledger.py",
)

# The seams that read or write the queue. `validate_card.py` only described it
# in its docstring, and now names no lane for it at all.
QUEUE_SEAMS = tuple(name for name in SEAMS if name != "validate_card.py")


@pytest.mark.parametrize("name", SEAMS)
def test_no_string_constant_names_backlog(name):
    assert lane_contract._scan_python(str(SCRIPTS / name), ["Backlog"]) == set()


@pytest.mark.parametrize("name", QUEUE_SEAMS)
def test_the_queue_is_imported_from_the_contract(name):
    tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "lane_contract"
        for alias in node.names
    }
    assert imported & {"QUEUE_LANE", "QUEUE_LANES"}, (name, imported)


def test_the_scan_sees_a_docstring(tmp_path):
    """The reader counts a docstring and skips a comment — the line the card
    draws between what a script says and the incident a comment records."""
    path = tmp_path / "probe.py"
    path.write_text('"""Waits in Backlog."""\n', encoding="utf-8")
    assert lane_contract._scan_python(str(path), ["Backlog"]) == {"Backlog"}
    path.write_text("# DRE-1: sat in Backlog for a week\n", encoding="utf-8")
    assert lane_contract._scan_python(str(path), ["Backlog"]) == set()
