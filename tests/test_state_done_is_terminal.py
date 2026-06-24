"""`cmd_state` never reopens a finished card (DRE-1877).

A merged PR moves its Linear card to Done, and that Done is ground truth. The
bug this pins: in DeltaSolv, linear-sync set DRE-1803 → Done on the PR #16
merge, then a *concurrent duplicate run* of the same card hit the dead-run cap
and its HOLD path ran `linear_ops.py state DRE-1803 Backlog` ~9 minutes later —
unconditionally dragging the already-Done, `needs-human`-labelled card back into
Backlog. The Done card then looked unfinished, its dependent DRE-1811 never
promoted, and epic E0.4 stalled "waiting for the next card."

The fix guards the shared `cmd_state` seam (every park — hold, requeue, blocker —
routes through it): once a card is in a terminal lifecycle bucket
(completed/canceled), an automated NON-terminal transition is refused. Forward
and terminal moves (→ Done, → Canceled, Done→Done) are untouched. The presence
of the `needs-human` label is irrelevant — closing/keeping-closed is gated on
the card being finished, never on a label.
"""

import io
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402


class _FakeLinear:
    """Minimal Linear double for cmd_state: a card in `current` state-type and a
    team whose workflow states include the ones the test moves between. Records
    every issueUpdate stateId so a test can assert whether the move happened."""

    # name -> (id, type) for the team's workflow states
    STATES = {
        "Backlog": ("st-backlog", "backlog"),
        "Todo": ("st-todo", "unstarted"),
        "In Progress": ("st-inprogress", "started"),
        "Done": ("st-done", "completed"),
        "Canceled": ("st-canceled", "canceled"),
    }

    def __init__(self, current_state_name):
        cur_id, cur_type = self.STATES[current_state_name]
        self._issue = {
            "id": "card-uuid",
            "identifier": "DRE-1803",
            "title": "merged card",
            "team": {"id": "team-1"},
            "state": {"name": current_state_name, "type": cur_type},
        }
        self.updates = []  # stateIds passed to issueUpdate

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": self._issue}
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


def _run_state(fake, target):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            linear_ops.cmd_state("DRE-1803", target)
    return buf.getvalue()


def test_done_card_is_not_dragged_back_to_backlog():
    """The exact DRE-1803 failure: a Done card asked to go Backlog stays Done.

    This is the load-bearing assertion. MUTATION CHECK: drop the terminal guard
    in cmd_state and `fake.updates` becomes [st-backlog] — i.e. the card WAS
    reopened — and this fails. So the test cannot pass without the fix.
    """
    fake = _FakeLinear("Done")
    out = _run_state(fake, "Backlog")
    assert fake.updates == [], f"a finished card must not be reopened, got {fake.updates}"
    assert "refusing" in out.lower()


def test_done_card_is_not_requeued_to_todo():
    """The dead-run *requeue* path (state Todo) must also leave a Done card alone."""
    fake = _FakeLinear("Done")
    _run_state(fake, "Todo")
    assert fake.updates == []


def test_canceled_card_is_not_reopened():
    """Canceled is terminal too — an automated park must not revive it."""
    fake = _FakeLinear("Canceled")
    _run_state(fake, "Backlog")
    assert fake.updates == []


def test_setting_done_on_a_merge_still_works():
    """Forward close is untouched: linear-sync's own `state Done` must still fire
    (the guard refuses only un-completing, never completing)."""
    fake = _FakeLinear("In Progress")
    _run_state(fake, "Done")
    assert fake.updates == [_FakeLinear.STATES["Done"][0]]


def test_done_to_done_is_idempotent_and_allowed():
    """Re-asserting Done on an already-Done card is a terminal→terminal move and
    is allowed (idempotent re-close from reconcile's 'already merged' sweep)."""
    fake = _FakeLinear("Done")
    _run_state(fake, "Done")
    assert fake.updates == [_FakeLinear.STATES["Done"][0]]


def test_normal_working_transitions_still_move():
    """Backlog → Todo (the everyday promotion) is unaffected by the guard."""
    fake = _FakeLinear("Backlog")
    _run_state(fake, "Todo")
    assert fake.updates == [_FakeLinear.STATES["Todo"][0]]
