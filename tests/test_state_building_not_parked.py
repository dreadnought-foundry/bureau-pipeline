"""`cmd_state` never silently strands a BUILDING card in Backlog (DRE-1885).

Follow-on to DRE-1877 (which stopped a *finished* card from being dragged out of
a terminal bucket) — this guards the same seam one lifecycle state earlier, on a
card that is actively building (In Progress).

The bug this pins: DRE-1822 went Todo → In Progress at 20:08; a hold/park path
reverted it to **Backlog** at 20:13 during the Actions-budget-block + dead-run
window — and nothing re-promotes a card from Backlog, so it sat stranded ~3h
until a human re-promoted it, stalling epic E4.1. Same class as DRE-1803, on the
building card rather than the finished one.

The fix: in the shared `cmd_state` seam, an In Progress (`started`) card aimed at
a `backlog`-type state is re-routed to **Todo** (which re-dispatches via the
cascade) instead of Backlog (inert) — UNLESS the park is deliberate, signalled by
`--park` (the blocker branch + the dead-run HOLD cap, whose Backlog is intended)
or the card already carrying the `needs-human` hold label. A not-started card
dropped to Backlog (an explicit operator/CEO park) is untouched.
"""

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402


class _FakeLinear:
    """Linear double for cmd_state: a card in `current` state with `labels`, and a
    team whose workflow states are the standard lanes. Records every issueUpdate
    stateId so a test can assert what move (if any) actually happened."""

    # name -> (id, type)
    STATES = {
        "Backlog": ("st-backlog", "backlog"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "In QA": ("st-inqa", "started"),
        "Done": ("st-done", "completed"),
        "Canceled": ("st-canceled", "canceled"),
    }

    def __init__(self, current_state_name, labels=None):
        cur_id, cur_type = self.STATES[current_state_name]
        self._issue = {
            "id": "card-uuid",
            "identifier": "DRE-1822",
            "title": "building card",
            "team": {"id": "team-1"},
            "state": {"name": current_state_name, "type": cur_type},
            "labels": {"nodes": [{"name": n} for n in (labels or [])]},
        }
        self.updates = []  # stateIds passed to issueUpdate

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": self._issue}
        if "history" in q:
            # cmd_state reads its own write back to catch a terminal state it
            # clobbered inside the race window (DRE-2316). Nothing races in
            # this suite, so the history carries no transition of ours.
            return {"issue": {"history": {"nodes": []}}}
        if "workflowStates" in q:
            return {
                "workflowStates": {
                    "nodes": [
                        {"id": sid, "name": name, "type": stype}
                        for name, (sid, stype) in self.STATES.items()
                    ]
                }
            }
        if "issueUpdate" in q:
            self.updates.append(v["input"].get("stateId"))
            return {"issueUpdate": {"success": True}}
        raise AssertionError(f"unexpected gql query: {q}")


def _run_state(fake, target, *flags):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_state("DRE-1822", target, *flags)
    return buf.getvalue()


# --- the load-bearing assertion (mutation check) ---------------------------

def test_building_card_park_to_backlog_is_rerouted_to_todo():
    """The exact DRE-1822 failure: a hold/park aiming an In Progress card at
    Backlog must NOT land it in Backlog. It is re-routed to Todo (re-dispatch).

    MUTATION CHECK: delete the building-card guard in cmd_state and `fake.updates`
    becomes [st-backlog] — the card WAS stranded — and this fails. So the test
    cannot pass without the fix.
    """
    fake = _FakeLinear("In Progress")
    out = _run_state(fake, "Backlog")
    assert fake.updates == [_FakeLinear.STATES["Todo"][0]], (
        f"a building card must be re-queued to Todo, not parked in Backlog; "
        f"got {fake.updates}"
    )
    assert "todo" in out.lower()
    assert _FakeLinear.STATES["Backlog"][0] not in fake.updates


# --- legitimate Backlog parks are preserved --------------------------------

def test_explicit_park_flag_keeps_a_building_card_in_backlog():
    """The blocker branch / dead-run HOLD cap pass --park: Backlog is intended
    (Todo would redispatch into the same wall / loop past the cap)."""
    fake = _FakeLinear("In Progress")
    _run_state(fake, "Backlog", "--park")
    assert fake.updates == [_FakeLinear.STATES["Backlog"][0]]


def test_needs_human_held_card_keeps_a_building_card_in_backlog():
    """A card already stamped 'needs-human' is a deliberate human-hold — the
    Backlog park is allowed even without --park."""
    fake = _FakeLinear("In Progress", labels=["needs-human", "repo:atlas"])
    _run_state(fake, "Backlog")
    assert fake.updates == [_FakeLinear.STATES["Backlog"][0]]


def test_not_started_card_park_to_backlog_still_works():
    """An explicit operator/CEO park of a card that never STARTED (here: Todo, an
    unstarted lane) must still drop to Backlog — that is a legitimate 'drop a
    not-started card', not the mid-flight strand bug. Disabling the guard must
    NOT change this; it asserts the guard is correctly scoped to `started`."""
    fake = _FakeLinear("Todo")
    _run_state(fake, "Backlog")
    assert fake.updates == [_FakeLinear.STATES["Backlog"][0]]


def test_backlog_card_park_to_backlog_is_idempotent():
    """A Backlog → Backlog no-op park is allowed (not started → not the bug)."""
    fake = _FakeLinear("Backlog")
    _run_state(fake, "Backlog")
    assert fake.updates == [_FakeLinear.STATES["Backlog"][0]]


# --- the guard is narrowly scoped; everyday transitions untouched ----------

def test_building_card_can_still_advance_to_in_qa():
    """The reroute is Backlog-only: an In Progress → In QA advance is untouched."""
    fake = _FakeLinear("In Progress")
    _run_state(fake, "In QA")
    assert fake.updates == [_FakeLinear.STATES["In QA"][0]]


def test_building_card_can_still_go_to_todo_directly():
    """An explicit In Progress → Todo move is exactly what the reroute produces;
    asking for it directly must just work (no double-handling)."""
    fake = _FakeLinear("In Progress")
    _run_state(fake, "Todo")
    assert fake.updates == [_FakeLinear.STATES["Todo"][0]]


def test_terminal_guard_still_wins_over_building_reroute():
    """A Done card asked for Backlog is refused outright (DRE-1877), never
    re-routed to Todo — the terminal guard takes precedence."""
    fake = _FakeLinear("Done")
    out = _run_state(fake, "Backlog")
    assert fake.updates == []
    assert "refusing" in out.lower()


# --- the queue lane under either name (DRE-6744) ---------------------------


class _QueuedBoard(_FakeLinear):
    """The board with `Queued` beside `Backlog`, typed `unstarted` — the worst
    case: a reroute that read only Linear's type would let a building card
    into the queue under its new name."""

    STATES = {**_FakeLinear.STATES, "Queued": ("st-queued", "unstarted")}


def test_building_card_aimed_at_queued_is_rerouted_to_todo():
    """The reroute reads the queue lane by NAME, through `QUEUE_LANES`: a
    building card parked in `Queued` strands exactly as one parked in the lane
    it replaces."""
    fake = _QueuedBoard("In Progress")
    out = _run_state(fake, "Queued")
    assert fake.updates == [_QueuedBoard.STATES["Todo"][0]], fake.updates
    assert "instead of 'Queued'" in out


def test_held_building_card_aimed_at_queued_is_rerouted_to_todo():
    """The held move (one read) decides the same reroute on the live card."""
    fake = _QueuedBoard("In Progress")
    _run_state(fake, "Queued", "--held")
    assert fake.updates == [_QueuedBoard.STATES["Todo"][0]], fake.updates


def test_explicit_park_flag_keeps_a_building_card_in_queued():
    fake = _QueuedBoard("In Progress")
    _run_state(fake, "Queued", "--park")
    assert fake.updates == [_QueuedBoard.STATES["Queued"][0]]


def test_not_started_card_park_to_queued_still_works():
    fake = _QueuedBoard("Todo")
    _run_state(fake, "Queued")
    assert fake.updates == [_QueuedBoard.STATES["Queued"][0]]


def test_state_queued_on_a_board_without_it_falls_back_to_backlog_and_says_so():
    """`state Queued` against a board that does not carry the lane yet lands in
    the lane it replaces, and the receipt says so: `_LANE_RENAME_FALLBACKS` is
    the contract's alias, inverted."""
    fake = _FakeLinear("Todo")
    out = _run_state(fake, "Queued")
    assert fake.updates == [_FakeLinear.STATES["Backlog"][0]]
    assert "'Queued' is not on the board yet" in out
    assert "pre-rename lane 'Backlog'" in out


def test_building_card_aimed_at_queued_on_a_board_without_it_is_rerouted():
    fake = _FakeLinear("In Progress")
    _run_state(fake, "Queued")
    assert fake.updates == [_FakeLinear.STATES["Todo"][0]]


def test_rename_fallbacks_are_the_contract_aliases_inverted():
    import lane_contract

    inverted: dict = {}
    for old, new in lane_contract.aliases().items():
        inverted[new.lower()] = inverted.get(new.lower(), ()) + (old,)
    assert linear_ops._LANE_RENAME_FALLBACKS == inverted
    # Every name a reader accepts for the queue is a name a write falls back to.
    assert linear_ops._LANE_RENAME_FALLBACKS[lane_contract.QUEUE_LANES[0].lower()] \
        == lane_contract.QUEUE_LANES[1:]


def test_the_write_layer_names_no_retired_queue_lane_of_its_own():
    """No string constant in `linear_ops.py` — docstring, receipt, query —
    carries the lane `Queued` replaces: every write and every read of the queue
    lane goes through `lane_contract`. A `#` comment recording an incident is
    history and keeps its wording; the scan reads string constants only."""
    import lane_contract

    assert lane_contract._scan_python(linear_ops.__file__, ["Backlog"]) == set()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
