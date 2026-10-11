"""TDD for EPIC-LEVEL dependency enforcement in the reconcile sweep (DRE-1772).

The card-level gate (promote_ready) already holds a Backlog child until its
OWN blockers are Done. But nothing enforced dependencies between EPICS: an epic
B that is `blocked-by` epic A could be moved In Progress (plan approved) and its
children would promote and start building even though A — the prerequisite
epic — had not shipped. And when A finally finished, nothing pulled B into the
pipeline; an operator had to notice and hand-promote it.

This module TDDs two additive behaviors:

  1. EPIC-LEVEL GATE — before promoting an epic's children, check that EPIC's
     own `blocked-by` relations; if ANY blocker epic is not Done, promote NONE
     of that epic's children this sweep, regardless of the epic's own state.
     Composes with (does not replace) the card-level gate, MAX_WIP, and the
     DRE-1585 unresolved-agent-blocker guard. Fails SAFE: missing/ambiguous
     relation data → treat as blocked (do not promote).

  2. AUTO-ADVANCE — when a blocker epic reaches Done, for each epic blocked-by
     it: if ALL its blocker epics are now Done AND it is still in Backlog, move
     it to Planning, whose arrival dispatches the planner (DRE-6653 — Triage
     is the broken-card lane now). NEVER to In Progress — the Green Light
     approval gate is preserved. Idempotent: never re-advance an epic already
     past Backlog; never thrash an operator-parked epic. Fails SAFE.

  3. THE RETURN PASS (DRE-6653) — the full sweep's backstop for the hook:
     `return_waiting_epics` reads every Backlog epic of this repo's formal
     `blockedBy` relations and returns one to Planning once every blocker is
     terminal, whoever closed it and however.

Reconcile governs promotion for EVERY product repo, so a bug here breaks
everyone — hence test-first.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

# Since DRE-3385 a Backlog card carrying NO routing verdict is refused
# promotion outright, so every candidate below carries one — a fixture held
# back for the missing verdict would say nothing about the gate under test.
FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")



@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress"):
    """A Backlog child eligible on every existing ground (active parent epic,
    no formal card blockers, no agent-blocker) — so ONLY the new epic-level
    gate can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork",
        "parent": {"identifier": parent, "state": {"name": parent_state}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"nodes": []},
    }


# ---------------------------------------------------------------------------
# Behavior 1: epic-level gate — epic_blockers_unmet(epic_identifier)
# ---------------------------------------------------------------------------
def _in_progress_blocker_with_a_card_left(identifier="DRE-700"):
    """Epic A's single-issue read (DRE-6407): In Progress with a build card
    still open in Backlog — not built out, so it holds under either rule. Said
    explicitly, so a hold never passes on an unstubbed read that failed."""
    return {"issue": {
        "identifier": identifier,
        "title": f"[EPIC] {identifier}",
        "state": {"name": "In Progress"},
        "children": {
            "nodes": [{
                "identifier": "DRE-701", "title": "a build card",
                "state": {"name": "Backlog"},
                "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                "children": {"nodes": []},
            }],
            "pageInfo": {"hasNextPage": False},
        },
    }}


def test_epic_with_unmet_blocker_epic_reports_unmet():
    """Epic B blocked-by epic A (A is In Progress, a build card still open) →
    B's blockers are unmet. (Relation read via the epic's inverseRelations,
    like cards.)"""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {
            "nodes": [{"type": "blocks", "issue": {"identifier": "DRE-700", "state": {"name": "In Progress"}}}]
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b), \
        patch.object(reconcile.linear_ops, "gql",
                     return_value=_in_progress_blocker_with_a_card_left()):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


def test_epic_with_all_blocker_epics_done_reports_clear():
    """Epic B blocked-by epic A, A Done → B's blockers are met (False)."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {
            "nodes": [{"type": "blocks", "issue": {"identifier": "DRE-700", "state": {"name": "Done"}}}]
        },
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b):
        assert reconcile.epic_blockers_unmet("DRE-800") is False


def test_epic_with_no_blockers_reports_clear():
    """An epic with no blocker relations and no blocked-by lines is clear."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b):
        assert reconcile.epic_blockers_unmet("DRE-800") is False


def test_epic_blocked_by_description_line_alone_reports_unmet():
    """A 'Blocked by: DRE-700' line on the EPIC that NO relation corroborates
    still holds the epic's children — but since DRE-2676 it holds them because
    the sentence is a defect the board contradicts, not because prose is a
    dependency (tests/test_prose_blocker_defect.py owns that behaviour)."""
    epic_b = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\nBlocked by: DRE-700\nepic B",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic_b), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=None), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


def test_epic_relations_unreadable_fails_safe_unmet():
    """Fail SAFE: if the epic's relation data can't be read, treat as blocked."""
    with patch.object(reconcile, "_fetch_epic_relations", return_value=None):
        assert reconcile.epic_blockers_unmet("DRE-800") is True


# ---------------------------------------------------------------------------
# Behavior 1 wired into promote_ready: B blocked-by A, A not Done →
# B's Backlog cards NOT promoted even though B is In Progress.
# ---------------------------------------------------------------------------
def test_promote_ready_skips_children_of_blocked_epic():
    reconcile._write_failures.clear()
    child = _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress")
    with patch.object(reconcile, "backlog_children", return_value=[child]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=True
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_promote_ready_promotes_children_of_unblocked_epic():
    """Control: epic's own blockers are all Done → children promote as before."""
    reconcile._write_failures.clear()
    child = _child(identifier="DRE-900", parent="DRE-800", parent_state="In Progress")
    with patch.object(reconcile, "backlog_children", return_value=[child]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value="Done"), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-900", "Todo", "Backlog")


def test_promote_ready_evaluates_each_epic_once_per_sweep():
    """Two children of the SAME blocked epic → the epic gate is consulted but
    neither child promotes (and the gate is not spammed per-child)."""
    reconcile._write_failures.clear()
    kids = [
        _child(identifier="DRE-900", parent="DRE-800"),
        _child(identifier="DRE-901", parent="DRE-800"),
    ]
    with patch.object(reconcile, "backlog_children", return_value=kids), patch.object(
        reconcile, "epic_blockers_unmet", return_value=True
    ) as gate, patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()
    assert gate.call_count == 1  # consulted once per epic, not once per child


# ---------------------------------------------------------------------------
# Behavior 2: auto-advance — advance_unblocked_epics(done_epic)
# ---------------------------------------------------------------------------
def _forward_blocks(done_epic, *blocked_epics):
    """gql shape for the epic that just went Done: its forward `relations`
    naming the epics it blocks."""
    return {
        "issue": {
            "relations": {
                "nodes": [
                    {"type": "blocks", "issue": {"identifier": b}} for b in blocked_epics
                ]
            }
        }
    }


def test_done_blocker_epic_advances_dependent_backlog_to_planning():
    """A→Done, B blocked-by only A, B in Backlog → B moves Backlog→Planning.

    This is the only path: since the wave route was retired (DRE-4700) no
    dependent epic takes any other."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_called_once_with("DRE-800", "Planning", "Backlog")
    comment.assert_called_once()
    receipt = comment.call_args.args[1]
    assert receipt.startswith("🧹 Auto-advanced Backlog → Planning:"), receipt
    assert "DRE-700 is Done" in receipt
    assert "dispatches the planner" in receipt
    assert "a person still approves the plan" in receipt
    assert "Triage" not in receipt


def test_advance_never_moves_epic_to_in_progress():
    """The Green Light approval gate is preserved: target is Planning, NOT
    In Progress and not Triage — assert the literal transition target."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    _, args, _ = advance.mock_calls[0]
    assert args[1] == "Planning"
    assert "In Progress" not in args
    assert "Triage" not in args


def test_advance_holds_epic_with_another_unmet_blocker():
    """B blocked-by A AND C; A Done but C not Done → B stays in Backlog."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Backlog"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=True), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_is_idempotent_for_already_advanced_epic():
    """B already past Backlog (In Progress / Triage) → never re-advanced; the
    operator-parked / already-running epic is never thrashed."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="In Progress"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_does_not_revive_canceled_epic():
    """A dropped (Canceled) dependent epic is never pulled back into the flow."""
    with patch.object(reconcile.linear_ops, "gql", return_value=_forward_blocks("DRE-700", "DRE-800")), \
        patch.object(reconcile, "card_state", return_value="Canceled"), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


def test_advance_fails_safe_when_relations_unreadable():
    """If the just-Done epic's forward relations can't be read, advance nothing."""
    with patch.object(reconcile.linear_ops, "gql", return_value={"issue": None}), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance:
        reconcile.advance_unblocked_epics("DRE-700")
    advance.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 2 wiring: close_finished_epics auto-advances the chain on merge,
# and the full sweep is the backstop.
# ---------------------------------------------------------------------------
def test_close_finished_epics_advances_chain_when_an_epic_closes():
    """When close_finished_epics closes an epic, it advances the epics that
    epic was blocking (merge-time hook)."""
    kids = {"issue": {"children": {"nodes": [{"state": {"name": "Done"}}, {"state": {"name": "Done"}}]}}}
    with patch.object(reconcile.linear_ops, "gql", return_value=kids), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-700"})
    advance_chain.assert_called_once_with("DRE-700")


def test_close_finished_epics_no_advance_when_epic_stays_open():
    """No close → no chain advance."""
    kids = {"issue": {"children": {"nodes": [{"state": {"name": "Done"}}, {"state": {"name": "In Progress"}}]}}}
    with patch.object(reconcile.linear_ops, "gql", return_value=kids), \
        patch.object(reconcile.linear_ops, "cmd_state"), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "advance_unblocked_epics") as advance_chain:
        reconcile.close_finished_epics({"DRE-700"})
    advance_chain.assert_not_called()


# ---------------------------------------------------------------------------
# Behavior 3: the return pass — return_waiting_epics(epics), DRE-6653.
# The hook above fires only when the sweep itself closes a blocker EPIC. A
# blocker that is an ordinary card, an epic a person closed by hand, or a
# Canceled blocker never fires it, so the full sweep reads every Backlog epic
# of this repo's formal blockedBy relations and returns it to Planning once
# every blocker is terminal.
# ---------------------------------------------------------------------------
#: The DRE-6585 roll-up: DRE-6589 waits in Backlog on its sibling DRE-6587.
ROLLUP_PARENT, ROLLUP_BLOCKER, ROLLUP_WAITER = "DRE-6585", "DRE-6587", "DRE-6589"


def _backlog_epic(identifier, *blocked_by, state="Backlog", more=False):
    """An epic as the pass's epic record carries it (`EPIC_RECORD_GQL`)."""
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nan epic still to be planned",
        "state": {"name": state},
        "children": {"nodes": []},
        "history": {"nodes": []},
        "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
                for i, s in blocked_by
            ],
            "pageInfo": {"hasNextPage": more, "endCursor": None},
        },
    }


class _EpicRecords:
    """Linear as the pass reads it: the batched epic record and nothing else.
    An epic not handed in is one Linear did not answer for — an unreadable read."""

    def __init__(self, *records):
        self.records = {r["identifier"]: r for r in records}
        self.reads = 0

    def gql(self, query, variables=None):
        v = variables or {}
        if "$numbers" not in query:
            raise reconcile.linear_ops.LinearError(
                f"unexpected query {' '.join(query.split())[:80]}")
        self.reads += 1
        wanted = {int(n) for n in v.get("numbers") or ()}
        return {"issues": {
            "nodes": [r for i, r in self.records.items() if int(i.split("-")[1]) in wanted],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}


def _return_pass(linear, epics, advance_effect=None):
    """One run of the pass over `epics`; returns (advance, comment) recorders."""
    reconcile.reset_sweep_cards()
    with patch.object(reconcile.linear_ops, "gql", side_effect=linear.gql), \
        patch.object(reconcile.linear_ops, "cmd_advance",
                     side_effect=advance_effect) as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.return_waiting_epics(set(epics))
    reconcile.reset_sweep_cards()
    return advance, comment


def test_dre_6585_replay_a_hand_closed_sibling_returns_the_waiter_to_planning():
    """The DRE-6585 roll-up, replayed: a person closes DRE-6587 by hand, so no
    `_close_epic_if_finished` runs and no hook fires. DRE-6589 waits in Backlog
    on DRE-6587 alone, and the full sweep's pass returns it to Planning, naming
    the blocker that released it."""
    linear = _EpicRecords(_backlog_epic(ROLLUP_WAITER, (ROLLUP_BLOCKER, "Done")))
    with patch.object(reconcile, "_close_epic_if_finished") as close:
        advance, comment = _return_pass(linear, {ROLLUP_WAITER})
    close.assert_not_called()
    advance.assert_called_once_with(ROLLUP_WAITER, "Planning", "Backlog")
    comment.assert_called_once()
    who, receipt = comment.call_args.args
    assert who == ROLLUP_WAITER
    assert receipt.startswith("🧹 Auto-advanced Backlog → Planning:"), receipt
    first_line = receipt.splitlines()[0]
    assert ROLLUP_BLOCKER in first_line
    assert "dispatches the planner" in receipt
    assert "Triage" not in receipt
    assert linear.reads == 1  # one relation read for the pass, not one per epic


def test_the_pass_releases_on_an_ordinary_card_blocker_done():
    """The blocker need not be an epic: an ordinary card Done releases too."""
    linear = _EpicRecords(_backlog_epic("DRE-800", ("DRE-701", "Done")))
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_called_once_with("DRE-800", "Planning", "Backlog")
    assert "DRE-701" in comment.call_args.args[1].splitlines()[0]


def test_the_pass_releases_on_a_canceled_blocker():
    """Canceled is terminal (`prose_blockers.TERMINAL`): it holds nothing."""
    linear = _EpicRecords(_backlog_epic(
        "DRE-800", ("DRE-700", "Canceled"), ("DRE-701", "Duplicate")))
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_called_once_with("DRE-800", "Planning", "Backlog")
    first_line = comment.call_args.args[1].splitlines()[0]
    assert "DRE-700" in first_line and "DRE-701" in first_line


def test_the_pass_holds_an_epic_with_an_open_blocker():
    """One blocker Done and one still In Progress: the strict rule holds it."""
    linear = _EpicRecords(_backlog_epic(
        "DRE-800", ("DRE-700", "Done"), ("DRE-701", "In Progress")))
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_not_called()
    comment.assert_not_called()


def test_the_pass_leaves_an_epic_with_no_relation():
    """Nothing blocks it, so it was never waiting: a roll-up child waiting for
    its parent's activation is the split's to send (DRE-6591)."""
    linear = _EpicRecords(_backlog_epic("DRE-800"))
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_not_called()
    comment.assert_not_called()


def test_the_pass_leaves_an_epic_past_backlog():
    """An epic the record reads in another lane is not the pass's to move."""
    linear = _EpicRecords(_backlog_epic("DRE-800", ("DRE-700", "Done"), state="Planning"))
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_not_called()
    comment.assert_not_called()


def test_the_pass_leaves_an_epic_it_cannot_read(capsys):
    """An unreadable read advances nothing and says which epic."""
    linear = _EpicRecords()  # Linear answers for no epic at all
    advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_not_called()
    comment.assert_not_called()
    assert "DRE-800" in capsys.readouterr().out


def test_the_pass_holds_on_half_a_relation_page():
    """Relations not read to the end decide nothing (DRE-5379)."""
    linear = _EpicRecords(_backlog_epic("DRE-800", ("DRE-700", "Done"), more=True))
    with patch.object(reconcile, "complete_inverse_relations"):
        advance, comment = _return_pass(linear, {"DRE-800"})
    advance.assert_not_called()
    comment.assert_not_called()


def test_the_pass_isolates_a_failure_to_one_epic(capsys):
    """One epic whose move fails is logged and skipped; the rest are swept."""
    linear = _EpicRecords(
        _backlog_epic("DRE-800", ("DRE-700", "Done")),
        _backlog_epic("DRE-801", ("DRE-700", "Done")),
    )

    def fail_first(identifier, *_):
        if identifier == "DRE-800":
            raise reconcile.linear_ops.LinearError("The read operation timed out")

    advance, comment = _return_pass(linear, {"DRE-800", "DRE-801"}, fail_first)
    assert [c.args[0] for c in advance.call_args_list] == ["DRE-800", "DRE-801"]
    assert [c.args[0] for c in comment.call_args_list] == ["DRE-801"]
    err = capsys.readouterr().err
    assert "DRE-800" in err and "timed out" in err


def test_the_pass_does_not_return_an_epic_the_hook_already_returned():
    """The hook and the pass in one sweep: one move, one receipt."""
    linear = _EpicRecords(_backlog_epic("DRE-800", ("DRE-700", "Done")))
    reconcile.reset_sweep_cards()
    with patch.object(reconcile.linear_ops, "gql", side_effect=linear.gql), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile._return_epic_to_planning("DRE-800", "blocker DRE-700 is Done")
        reconcile.return_waiting_epics({"DRE-800"})
    reconcile.reset_sweep_cards()
    advance.assert_called_once_with("DRE-800", "Planning", "Backlog")
    comment.assert_called_once()


# ---------------------------------------------------------------------------
# Behavior 3 wiring: its own phase of the full sweep, handed the Backlog epics
# off the promotion phase's read — and never on the merge path.
# ---------------------------------------------------------------------------
def _sweep_phase_mocks(backlog_epics):
    def promote(*_, **kwargs):
        if kwargs.get("close_epics"):
            reconcile._backlog_epics.update(backlog_epics)
        return 0

    from unittest.mock import MagicMock
    return {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "active_cards": MagicMock(return_value=[]),
        "close_finished_epics": MagicMock(return_value=set()),
        "promote_ready": MagicMock(side_effect=promote),
        "merged_card_scope": MagicMock(return_value=None),
        "return_waiting_epics": MagicMock(),
    }


def test_the_full_sweep_runs_the_pass_over_the_promotion_reads_backlog_epics():
    mocks = _sweep_phase_mocks({"DRE-6589"})
    with patch.multiple(reconcile, **mocks):
        try:
            reconcile.main()
        except SystemExit:  # a red sweep still ran every phase
            pass
    mocks["return_waiting_epics"].assert_called_once_with({"DRE-6589"})


def test_the_close_only_path_runs_no_pass():
    mocks = _sweep_phase_mocks({"DRE-6589"})
    with patch.multiple(reconcile, **mocks):
        reconcile.main(close_only=True)
    mocks["return_waiting_epics"].assert_not_called()


def test_the_promotion_read_hands_its_backlog_epics_to_the_pass():
    """`promote_ready(close_epics=True)` records the epics it read in Backlog —
    the same set `closable_epics(cards, BACKLOG_LANE)` hands the close — less
    the ones it closed."""
    epic = {**_child(identifier="DRE-800", parent=None),
            "parent": None, "children": {"nodes": [{"id": "kid"}]}}
    closed = {**_child(identifier="DRE-801", parent=None),
              "parent": None, "children": {"nodes": [{"id": "kid"}]}}
    reconcile.reset_sweep_cards()
    with patch.object(reconcile, "backlog_children", return_value=[epic, closed]), \
        patch.object(reconcile, "close_finished_epics", return_value={"DRE-801"}), \
        patch.object(reconcile, "epic_records"), \
        patch.object(reconcile, "_door_epic_threads"), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0, close_epics=True)
    assert reconcile._backlog_epics == {"DRE-800"}
    reconcile.reset_sweep_cards()
    assert reconcile._backlog_epics == set()
