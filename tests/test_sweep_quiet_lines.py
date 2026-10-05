"""RED-first tests: four sweep phases each say so when they find nothing (DRE-5519).

THE GAP. The DRE-3636 proof read a production sweep's log for each of its
eight fleet-wide phases (`docs/sandbox-seat-proof-2026-09.md` §3.2) and found
four that print nothing when idle — `drain_retiring_lanes`,
`recover_limit_deaths`, `repair_frozen_planning_holds` and
`carry_epics_out_of_todo` — so a reader of the log cannot tell "ran and found
nothing" from "never ran". The other phases already print one quiet line, in
one shape: the phase's own prefix, `nothing to …`, and what it read —
`fleet-reviewer-outage: nothing to report — 0 could-not-run in the last 30 min`.

FIX UNDER TEST. Each of the four prints exactly that one line when it acts on
nothing, and no such line when it acts.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sweep_quiet_lines.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402

RETIRING = {
    "name": "In Escrow",
    "status": "retiring",
    "retired_by": "DRE-9001",
    "replaced_by": "In Review",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_ACCOUNT", raising=False)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _card(ident, state, *, labels=(), bodies=(), children=(), title="a card"):
    return {
        "id": f"uuid-{ident}",
        "identifier": ident,
        "title": title,
        "description": "work",
        "updatedAt": "2026-10-01T00:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in labels]},
        "children": {"nodes": [{"id": c} for c in children]},
        "comments": {"nodes": [{"body": b, "createdAt": "2026-10-01T00:00:00Z"}
                               for b in bodies]},
    }


def _board(cards):
    """`active_cards` filtering by lane, as the real one does."""
    return lambda states=reconcile.SWEEP_STATES: [
        c for c in cards if c["state"]["name"] in states
    ]


def _lines(out: str, prefix: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(f"{prefix}: ")]


# ===========================================================================
# drain_retiring_lanes
# ===========================================================================
class TestDrain:
    def test_no_retiring_lane_prints_one_quiet_line(self, capsys):
        with patch.object(reconcile.lane_contract, "lanes", MagicMock(return_value=())), \
             patch.object(reconcile, "active_cards") as cards:
            reconcile.drain_retiring_lanes()
        cards.assert_not_called()  # still inert: no Linear read for nothing
        assert _lines(capsys.readouterr().out, "drain") == [
            "drain: nothing to move — the lane contract names no retiring lane"
        ]

    def test_an_empty_retiring_lane_prints_one_quiet_line(self, capsys):
        with patch.object(reconcile.lane_contract, "lanes",
                          MagicMock(return_value=(RETIRING,))), \
             patch.object(reconcile, "active_cards", MagicMock(return_value=[])):
            reconcile.drain_retiring_lanes()
        assert _lines(capsys.readouterr().out, "drain") == [
            "drain: nothing to move — 0 card(s) in the retiring lane(s) In Escrow"
        ]

    def test_a_drain_that_moves_prints_no_quiet_line(self, capsys):
        stranded = _card("DRE-9999", "In Escrow")
        with patch.object(reconcile.lane_contract, "lanes",
                          MagicMock(return_value=(RETIRING,))), \
             patch.object(reconcile, "active_cards", MagicMock(return_value=[stranded])), \
             patch.object(reconcile.linear_ops, "cmd_advance"), \
             patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.drain_retiring_lanes()
        out = capsys.readouterr().out
        assert "nothing to" not in out, out
        assert _lines(out, "drain") == [
            "drain: moved 1 card(s) out of retiring lane(s)"
        ]


# ===========================================================================
# recover_limit_deaths
# ===========================================================================
class TestLimitRecovery:
    def test_an_empty_board_prints_one_quiet_line(self, capsys):
        with patch.object(reconcile, "active_cards", MagicMock(return_value=[])):
            reconcile.recover_limit_deaths()
        assert _lines(capsys.readouterr().out, "limit-recovery") == [
            "limit-recovery: nothing to re-enter — 0 card(s) read, none with a "
            "limit death to recover"
        ]

    def test_cards_with_no_limit_death_still_print_one_quiet_line(self, capsys):
        board = [_card("DRE-1", "In Progress", labels=("repo:agent-bureau",)),
                 _card("DRE-2", "Planning")]
        with patch.object(reconcile, "active_cards", MagicMock(return_value=board)):
            reconcile.recover_limit_deaths()
        assert _lines(capsys.readouterr().out, "limit-recovery") == [
            "limit-recovery: nothing to re-enter — 2 card(s) read, none with a "
            "limit death to recover"
        ]

    def test_a_recovery_that_acts_prints_no_quiet_line(self, capsys):
        acted = ["limit-recovery: DRE-1 build re-entered — the window reset"]
        with patch.object(reconcile, "active_cards", MagicMock(return_value=[])), \
             patch.object(reconcile.limit_recovery, "recover", return_value=acted):
            reconcile.recover_limit_deaths()
        out = capsys.readouterr().out
        assert "nothing to" not in out, out
        assert _lines(out, "limit-recovery") == acted


# ===========================================================================
# repair_frozen_planning_holds
# ===========================================================================
def _frozen(ident="DRE-2415"):
    return _card(ident, "Planning", labels=(reconcile.HOLD_LABEL,),
                 bodies=[f"{reconcile.WATCHDOG_TAG}: stalled in Planning"])


class TestPlanningRepair:
    def test_an_empty_board_prints_one_quiet_line(self, capsys):
        with patch.object(reconcile, "active_cards", _board([])), \
             patch.object(reconcile, "_open_pr_listing") as prs:
            assert reconcile.repair_frozen_planning_holds() == set()
        prs.assert_not_called()  # no candidate, no GitHub read
        assert _lines(capsys.readouterr().out, "planning-repair") == [
            "planning-repair: nothing to repair — 0 frozen card(s) among the "
            "0 card(s) in Planning"
        ]

    def test_unfrozen_planning_cards_print_one_quiet_line(self, capsys):
        board = [_card("DRE-1", "Planning"), _card("DRE-2", "Planning")]
        with patch.object(reconcile, "active_cards", _board(board)):
            assert reconcile.repair_frozen_planning_holds() == set()
        assert _lines(capsys.readouterr().out, "planning-repair") == [
            "planning-repair: nothing to repair — 0 frozen card(s) among the "
            "2 card(s) in Planning"
        ]

    def test_a_repair_that_acts_prints_no_quiet_line(self, capsys):
        with patch.object(reconcile, "active_cards", _board([_frozen()])), \
             patch.object(reconcile, "_open_pr_listing", return_value=[]), \
             patch.object(reconcile, "escalate_out_of_planning", return_value=True), \
             patch.object(reconcile.linear_ops, "remove_label"):
            assert reconcile.repair_frozen_planning_holds() == {"DRE-2415"}
        out = capsys.readouterr().out
        assert "nothing to" not in out, out
        assert len(_lines(out, "planning-repair")) == 1
        assert "DRE-2415 was frozen in Planning" in out


# ===========================================================================
# carry_epics_out_of_todo
# ===========================================================================
class TestEpicCarry:
    def test_an_empty_board_prints_one_quiet_line(self, capsys):
        with patch.object(reconcile, "active_cards", _board([])):
            reconcile.carry_epics_out_of_todo()
        assert _lines(capsys.readouterr().out, "epic-not-todo") == [
            "epic-not-todo: nothing to carry — no epic among the 0 card(s) in Todo"
        ]

    def test_a_todo_lane_of_one_offs_prints_one_quiet_line(self, capsys):
        board = [_card("DRE-1", "Todo", labels=("repo:agent-bureau",)),
                 _card("DRE-2", "Todo", labels=("repo:agent-bureau",)),
                 _card("DRE-3", "In Progress", children=("kid",))]
        with patch.object(reconcile, "active_cards", _board(board)), \
             patch.object(reconcile.linear_ops, "cmd_state") as state:
            reconcile.carry_epics_out_of_todo()
        state.assert_not_called()
        assert _lines(capsys.readouterr().out, "epic-not-todo") == [
            "epic-not-todo: nothing to carry — no epic among the 2 card(s) in Todo"
        ]

    def test_a_carry_that_acts_prints_no_quiet_line(self, capsys):
        epic = _card("DRE-3621", "Todo", labels=("repo:agent-bureau",),
                     children=("kid",))
        with patch.object(reconcile, "active_cards", _board([epic])), \
             patch.object(reconcile.epic_todo_gate, "lane_before_todo",
                          return_value="Backlog"), \
             patch.object(reconcile.epic_todo_gate, "refusal", return_value=None), \
             patch.object(reconcile.linear_ops, "cmd_state", return_value=True):
            reconcile.carry_epics_out_of_todo()
        out = capsys.readouterr().out
        assert "nothing to" not in out, out
        assert _lines(out, "epic-not-todo") == [
            "epic-not-todo: DRE-3621 found in Todo (before: Backlog) — carried to Planning"
        ]
