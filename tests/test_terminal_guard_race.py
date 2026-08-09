"""RED-first tests: the terminal guard must survive a RACE, not only a delay
(DRE-2316).

THE BUG (live, 2026-08-08, DRE-2316 on bureau-pipeline). Linear's own state
history, to the millisecond:

    22:22:17.540  In Progress -> In Review
    22:22:30.349  In Review   -> Done      (linear-sync, on the PR #137 merge)
    22:22:30.629  Done        -> Todo      (dead-run requeue)
    22:22:55.279  Todo        -> In Progress   (second agent dispatched)
    22:29:00.936  In Progress -> Backlog       (second agent's blocker park)

The DRE-1877 terminal guard in `cmd_state` is correct in LOGIC and it did not
fire: it reads the card, decides, then mutates. It read "In Review"
(non-terminal), Done landed 280 ms later, and the mutation went through against
a state that was already stale. DRE-1877 fixed this class for the SLOW case
(DRE-1803 clobbered a Done card ~9 minutes later, so the single read was still
fresh enough); check-then-act leaves it wide open for the fast one.

FIX UNDER TEST — `cmd_state` (and `cmd_advance`, the other write seam that
decides from a stale read):

  1. RE-READ the card immediately before the mutation and refuse if it has
     become terminal. This shrinks the window from "everything the caller did
     since its first read" to ONE API round trip.
  2. READ BACK our own write. Linear's issue history records the `fromState` of
     the transition we just made, so a terminal state that landed inside the
     residual window is VISIBLE afterwards — and gets restored.

Honesty note pinned by these tests: this is NOT atomic. Linear's GraphQL API
has no compare-and-set — `issueUpdate(id, input)` and `IssueUpdateInput` carry
no expected-version / if-match field (schema introspected 2026-08-09). Step 2
is a compensating write, not prevention: the card really does flap
Done -> ours -> Done. What it buys is that the finished state is the one that
SURVIVES, instead of a Done card silently sitting in Todo.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402


class _RacingLinear:
    """Linear double with a CONCURRENT WRITER built in.

    `flip_to` / `flip_after_reads` script the race deterministically: after the
    Nth issue read the card's state changes underneath us, exactly as
    linear-sync's merge->Done did 280 ms into the requeue. No sleeps, no real
    timing — the seam is the read count.
    """

    # name -> (id, type)
    STATES = {
        "Backlog": ("st-backlog", "backlog"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "In QA": ("st-inqa", "started"),
        "In Review": ("st-inreview", "started"),
        "Done": ("st-done", "completed"),
        "Canceled": ("st-canceled", "canceled"),
    }

    def __init__(self, current_state_name, *, flip_to=None, flip_after_reads=0,
                 labels=None):
        self.current = current_state_name
        self.flip_to = flip_to
        self.flip_after_reads = flip_after_reads
        self.labels = labels or []
        self.reads = 0
        self.updates = []  # stateIds passed to issueUpdate, in order
        self.history = []  # newest LAST, like Linear's history(last: n)

    # -- helpers ----------------------------------------------------------
    def _node(self, name):
        sid, stype = self.STATES[name]
        return {"id": sid, "name": name, "type": stype}

    def _name_for(self, state_id):
        for name, (sid, _t) in self.STATES.items():
            if sid == state_id:
                return name
        raise AssertionError(f"unknown stateId {state_id!r}")

    def _write(self, name):
        self.history.append(
            {
                "createdAt": "2026-08-08T22:22:30.629Z",
                "fromState": self._node(self.current),
                "toState": self._node(name),
            }
        )
        self.current = name

    def _maybe_flip(self):
        if self.flip_to and self.reads == self.flip_after_reads:
            self._write(self.flip_to)  # the concurrent writer (linear-sync)

    # -- the transport ----------------------------------------------------
    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            self.reads += 1
            issue = {
                "id": "card-uuid",
                "identifier": "DRE-2316",
                "title": "raced card",
                "team": {"id": "team-1"},
                "state": {
                    "name": self.current,
                    "type": self.STATES[self.current][1],
                },
                "labels": {"nodes": [{"name": n} for n in self.labels]},
            }
            self._maybe_flip()  # the race lands JUST AFTER this read
            return {"issue": issue}
        if "history" in q:
            return {"issue": {"history": {"nodes": list(self.history)}}}
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
            sid = v["input"].get("stateId")
            self.updates.append(sid)
            self._write(self._name_for(sid))
            return {"issueUpdate": {"success": True}}
        raise AssertionError(f"unexpected gql query: {q}")


def _run(fn, fake, *args):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            fn(*args)
    return buf.getvalue()


DONE = _RacingLinear.STATES["Done"][0]
TODO = _RacingLinear.STATES["Todo"][0]
BACKLOG = _RacingLinear.STATES["Backlog"][0]


# --------------------------------------------------------------------------
# 1. The pre-write re-read: a Done that lands after the first read is SEEN
# --------------------------------------------------------------------------
def test_done_landing_between_read_and_write_is_refused():
    """THE DRE-2316 kill shot, reproduced deterministically.

    The card reads "In Review" (non-terminal, guard passes), then linear-sync's
    merge->Done lands, then the dead-run requeue writes Todo.

    MUTATION CHECK: with only the single check-then-act read, `fake.updates`
    is [st-todo] — the finished card is sitting in Todo, which is exactly what
    dispatched a second agent onto shipped work.
    """
    fake = _RacingLinear("In Review", flip_to="Done", flip_after_reads=1)
    out = _run(linear_ops.cmd_state, fake, "DRE-2316", "Todo")
    assert fake.updates == [], (
        f"a card that went Done mid-transition must not be requeued; got {fake.updates}"
    )
    assert "refusing" in out.lower()
    assert fake.current == "Done"


def test_hold_park_losing_the_race_is_refused_too():
    """The other park caller: the dead-run HOLD cap aiming Backlog with --park.
    Same seam, same race, same answer."""
    fake = _RacingLinear("In Progress", flip_to="Done", flip_after_reads=1)
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Backlog", "--park")
    assert fake.updates == []
    assert fake.current == "Done"


def test_advance_losing_the_race_is_refused():
    """`cmd_advance` decides from a stale read as well — the agent-task PR path
    runs `advance <card> "In QA" "In Progress,Todo"`, which would happily drag a
    card that just went Done back into In QA."""
    fake = _RacingLinear("In Progress", flip_to="Done", flip_after_reads=1)
    _run(linear_ops.cmd_advance, fake, "DRE-2316", "In QA", "In Progress,Todo")
    assert fake.updates == []
    assert fake.current == "Done"


# --------------------------------------------------------------------------
# 2. The residual window: we lose anyway, and put it back
# --------------------------------------------------------------------------
def test_terminal_state_clobbered_inside_the_residual_window_is_restored():
    """The window is NARROWED, not closed: the concurrent Done lands after the
    pre-write re-read, so our write goes through and really does clobber it.

    The read-back of our own write reports `fromState = Done`, so the guard
    knows it lost and restores the terminal state. The card ends Done.
    """
    fake = _RacingLinear("In Review", flip_to="Done", flip_after_reads=2)
    out = _run(linear_ops.cmd_state, fake, "DRE-2316", "Todo")
    assert fake.updates == [TODO, DONE], (
        f"the clobbered terminal state must be restored; got {fake.updates}"
    )
    assert fake.current == "Done"
    assert "done" in out.lower()


def test_restore_also_covers_the_building_card_reroute_write():
    """DRE-1885 re-routes a building card's Backlog park to Todo — a SECOND
    write seam in the same function. It must carry the same repair, or the
    reroute becomes the way to clobber a Done card."""
    fake = _RacingLinear("In Progress", flip_to="Done", flip_after_reads=2)
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Backlog")
    assert fake.updates == [TODO, DONE]
    assert fake.current == "Done"


# --------------------------------------------------------------------------
# 3. The uncontended path is untouched (no spurious repair writes)
# --------------------------------------------------------------------------
def test_uncontended_move_writes_exactly_once():
    fake = _RacingLinear("Backlog")
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Todo")
    assert fake.updates == [TODO]


def test_uncontended_park_writes_exactly_once():
    fake = _RacingLinear("In Progress")
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Backlog", "--park")
    assert fake.updates == [BACKLOG]


def test_uncontended_reroute_still_lands_in_todo():
    """DRE-1885 unchanged when nothing races: In Progress -> Backlog becomes
    Todo, once."""
    fake = _RacingLinear("In Progress")
    out = _run(linear_ops.cmd_state, fake, "DRE-2316", "Backlog")
    assert fake.updates == [TODO]
    assert "todo" in out.lower()


def test_forward_close_is_never_repaired_away():
    """linear-sync's own merge->Done is a terminal target: no guard, no repair,
    one write. (A repair that fired here would undo every close.)"""
    fake = _RacingLinear("In Review")
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Done")
    assert fake.updates == [DONE]
    assert fake.current == "Done"


def test_advance_still_advances_when_nothing_races():
    fake = _RacingLinear("In Progress")
    _run(linear_ops.cmd_advance, fake, "DRE-2316", "In QA", "In Progress,Todo")
    assert fake.updates == [_RacingLinear.STATES["In QA"][0]]


# --------------------------------------------------------------------------
# 4. The read-back is BEST EFFORT: an unreadable history never breaks a write
# --------------------------------------------------------------------------
def test_unreadable_history_does_not_break_the_write():
    """A Linear blip on the verification read must not fail the transition we
    already made — it degrades to "narrowed window, no repair", loudly."""

    class _NoHistory(_RacingLinear):
        def gql(self, query, variables=None):
            if "history" in " ".join(query.split()):
                raise linear_ops.LinearError("boom")
            return super().gql(query, variables)

    fake = _NoHistory("Backlog")
    _run(linear_ops.cmd_state, fake, "DRE-2316", "Todo")
    assert fake.updates == [TODO]
