"""RED-first: a held epic goes on to Planning when its blocker relation is
removed, not only when the blocker reaches Done (DRE-9223).

On 10-19 the planner held DRE-7695 in Queued on its formal blocker DRE-7670
(`⏸️ epic-waits: … It is planned once every one is Done …`). On 10-23 an
operator removed that relation — DRE-7670 was a proof card, and an open proof
never holds work — and the card had no open blocker after that. The return
pass (`reconcile.return_waiting_epics`) skipped it every sweep, because an
epic with no `blockedBy` relation at all read as one that was never waiting:
it sat in Queued for twelve days until the operator sweep re-planned it by
hand on 11-04.

These tests hold:

  1. the replay: a hold posted on DRE-7670, then the relation removed with
     DRE-7670 still open, moves DRE-7695 to Planning on the next pass, and the
     receipt says the relation was removed;
  2. the control: the same hold with DRE-7670 still attached and still open
     keeps it held;
  3. the hold is the epic's standing one: an epic with no relation and no
     hold, or a hold the epic has since left Queued past, is left where it is,
     and a thread that cannot be read leaves it too;
  4. the receipt says which way the hold cleared — a blocker Done, a blocker
     canceled or duplicate, or a relation removed — and a hold on two
     blockers, one removed and one Done, names both ways.

The release itself reads the live relations, never the hold comment: the
comment only says which epic is held and which blockers it named.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_wait_relation_removed.py -v
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

import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

QUEUE_FROM = ",".join(lane_contract.QUEUE_LANES)

#: The replay's two cards: the held epic and the proof it was held on.
EPIC, PROOF = "DRE-7695", "DRE-7670"

#: When the hold was posted and the epic moved to Queued (10-19), and a later
#: moment the epic's history may move it on at.
HELD_AT, QUEUED_AT, LATER = (
    "2026-10-19T17:02:10.000Z", "2026-10-19T17:02:12.000Z", "2026-10-21T09:00:00.000Z")


def _hold(epic=EPIC, waits_on=f"{PROOF} (In Progress)"):
    """The `⏸️ epic-waits:` receipt exactly as plan.yml posts it."""
    detail = (
        f"⏸️ epic-waits: {epic} waits in Queued on its formal blocker(s) {waits_on}. "
        "It is planned once every one is Done, an In Progress epic that is built "
        "out, or the proof of an epic that is Done or built out — nothing is asked "
        "of anyone until then.")
    return {"body": pipeline_act.receipt("epic-waits-on-blocker", detail),
            "createdAt": HELD_AT}


def _record(*blocked_by, history=None, more=False):
    """The epic as the pass's epic record carries it (`EPIC_RECORD_GQL`)."""
    return {
        "id": f"uuid-{EPIC}",
        "identifier": EPIC,
        "description": "**Repo:** agent-bureau\nan epic still to be planned",
        "state": {"name": reconcile.QUEUE_LANE},
        "children": {"nodes": []},
        "history": {"nodes": history if history is not None else [
            {"createdAt": QUEUED_AT, "toState": {"name": "Queued"}},
            {"createdAt": "2026-10-19T16:50:00.000Z", "toState": {"name": "Planning"}},
        ]},
        "comments": {"nodes": [{"id": "c1"}], "pageInfo": {"hasNextPage": False}},
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"identifier": i, "state": {"name": s}}}
                for i, s in blocked_by
            ],
            "pageInfo": {"hasNextPage": more, "endCursor": None},
        },
    }


class _Linear:
    """Linear as the pass reads it: the batched epic record, and one ordinary
    (non-proof) card's title and parent for any blocker the strict rule asks
    about. Anything else is a read the pass must not make."""

    def __init__(self, record):
        self.record = record

    def gql(self, query, variables=None):
        v = variables or {}
        if "$numbers" in query:
            return {"issues": {"nodes": [self.record],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if "parent { identifier state" in query:
            return {"issue": {"identifier": v["id"], "title": "an ordinary card",
                              "state": {"name": "In Review"}, "parent": None}}
        raise reconcile.linear_ops.LinearError(
            f"unexpected query {' '.join(query.split())[:80]}")


def _pass(record, thread):
    """One run of the return pass; `thread` is the epic's comments, oldest
    first, or an exception the thread read raises. Returns (advance, comment)."""
    linear = _Linear(record)
    timeline = patch.object(
        reconcile.linear_ops, "comment_timeline",
        side_effect=thread if isinstance(thread, Exception) else (lambda *_a, **_k: thread))
    reconcile.reset_sweep_cards()
    with patch.object(reconcile.linear_ops, "gql", side_effect=linear.gql), \
        timeline, \
        patch.object(reconcile.linear_ops, "cmd_advance", return_value=True) as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        reconcile.return_waiting_epics({EPIC})
    reconcile.reset_sweep_cards()
    return advance, comment


def _first_line(comment):
    who, receipt = comment.call_args.args
    assert who == EPIC
    return receipt.splitlines()[0]


# --------------------------------------------------------------------------
# 1 + 2: the replay and its control
# --------------------------------------------------------------------------
def test_dre_7695_replay_a_removed_relation_returns_the_held_epic_to_planning():
    """Held on DRE-7670 on 10-19, the relation removed on 10-23 with DRE-7670
    still open: the record reads no `blockedBy` relation at all, and the next
    pass sends the epic on to Planning, exactly as a blocker reaching Done
    does."""
    advance, comment = _pass(_record(), [_hold()])
    advance.assert_called_once_with(EPIC, "Planning", QUEUE_FROM)
    comment.assert_called_once()
    line = _first_line(comment)
    assert line.startswith(f"🧹 Auto-advanced {reconcile.QUEUE_LANE} → Planning:"), line
    assert PROOF in line and "removed" in line, line
    assert "and no blocker still holds it" in line, line
    assert "dispatches the planner" in comment.call_args.args[1]


def test_the_same_hold_with_the_blocker_still_attached_and_open_keeps_it_held():
    """The control: DRE-7670 is still a `blockedBy` relation and still open,
    so the live relation holds it — whatever the hold comment says."""
    advance, comment = _pass(_record((PROOF, "In Review")), [_hold()])
    advance.assert_not_called()
    comment.assert_not_called()


# --------------------------------------------------------------------------
# 3: only a standing hold is released
# --------------------------------------------------------------------------
def test_an_epic_with_no_relation_and_no_hold_is_left_where_it_is():
    """Nothing blocks it and nothing held it: a roll-up child waiting for its
    parent's activation is the split's to send (DRE-6591)."""
    thread = [{"body": "a person's note", "createdAt": HELD_AT}]
    advance, comment = _pass(_record(), thread)
    advance.assert_not_called()
    comment.assert_not_called()


def test_a_comment_that_only_quotes_the_hold_is_not_the_hold():
    """Anchored at the start, like every marker the pipeline reads."""
    quoted = {"body": f"Why did it say ⏸️ epic-waits: {EPIC} waits in Queued?",
              "createdAt": HELD_AT}
    advance, comment = _pass(_record(), [quoted])
    advance.assert_not_called()
    comment.assert_not_called()


def test_a_hold_the_epic_has_since_left_queued_past_does_not_stand():
    """Held, returned to Planning, then parked in Queued by a person with no
    blocker: the old hold is not what keeps it there, so it stays."""
    history = [
        {"createdAt": "2026-10-22T10:00:00.000Z", "toState": {"name": "Queued"}},
        {"createdAt": LATER, "toState": {"name": "Planning"}},
        {"createdAt": QUEUED_AT, "toState": {"name": "Queued"}},
    ]
    advance, comment = _pass(_record(history=history), [_hold()])
    advance.assert_not_called()
    comment.assert_not_called()


def test_a_hold_with_moves_that_are_not_lane_moves_still_stands():
    """A label or a title edit after the hold is not a lane move."""
    history = [
        {"createdAt": LATER, "toState": None},
        {"createdAt": QUEUED_AT, "toState": {"name": "Queued"}},
    ]
    advance, comment = _pass(_record(history=history), [_hold()])
    advance.assert_called_once_with(EPIC, "Planning", QUEUE_FROM)


def test_an_unreadable_thread_leaves_a_relationless_epic_in_queued(capsys):
    """No relation and a thread that cannot be read: whether it is held is
    unknown, so it is left this sweep and the log names it."""
    advance, comment = _pass(
        _record(), reconcile.linear_ops.LinearError("The read operation timed out"))
    advance.assert_not_called()
    comment.assert_not_called()
    assert EPIC in capsys.readouterr().out


def test_half_a_relation_page_releases_nothing():
    """Relations not read to the end decide nothing (DRE-5379), hold or not."""
    with patch.object(reconcile, "complete_inverse_relations"):
        advance, comment = _pass(_record(more=True), [_hold()])
    advance.assert_not_called()
    comment.assert_not_called()


# --------------------------------------------------------------------------
# 4: the receipt says which way the hold cleared
# --------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["Done", "Canceled", "Duplicate"])
def test_a_closed_blocker_is_named_by_how_it_closed(state):
    advance, comment = _pass(_record((PROOF, state)), [_hold()])
    advance.assert_called_once_with(EPIC, "Planning", QUEUE_FROM)
    line = _first_line(comment)
    assert f"blocker {PROOF} is {state}" in line, line
    assert "removed" not in line, line


def test_one_blocker_removed_and_one_done_names_both_ways():
    """Held on two blockers: DRE-7670's relation removed, DRE-7600 Done."""
    hold = _hold(waits_on=f"DRE-7600 (In Progress), {PROOF} (In Progress)")
    advance, comment = _pass(_record(("DRE-7600", "Done")), [hold])
    advance.assert_called_once_with(EPIC, "Planning", QUEUE_FROM)
    line = _first_line(comment)
    assert "blocker DRE-7600 is Done" in line, line
    assert PROOF in line and "removed" in line, line


def test_a_closed_blocker_releases_even_when_the_thread_cannot_be_read():
    """The thread only names a removed relation; a release the live
    relations already prove is never held back by it."""
    advance, comment = _pass(
        _record((PROOF, "Done")), reconcile.linear_ops.LinearError("timed out"))
    advance.assert_called_once_with(EPIC, "Planning", QUEUE_FROM)
    assert f"blocker {PROOF} is Done" in _first_line(comment)
