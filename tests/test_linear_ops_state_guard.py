"""RED-first: the read-back repair only ever repairs its OWN write (DRE-5142).

THE BUG (live, 2026-09-28, epic DRE-5034). A person reopened the epic Done →
Planning twice, and both times the planner's own run closed it again inside a
minute: 08:28:22 → 08:29:22 PT and 15:16:17 → 15:16:59 PT. A third reopen,
routed Done → Intake → Planning precisely to dodge it, was closed the same way
at 17:53:31 PT. While the epic read Done none of its eleven unbuilt children
could start.

plan.yml's plan route runs `linear_ops.py state <epic> Planning` on a card that
is ALREADY in Planning. `guarded_state_write` wrote it anyway and then ran the
DRE-2316 read-back, which asked for `history(last: 10)` believing that to be
the ten NEWEST entries. It is the ten OLDEST. On a card with more than ten
history entries the repair never saw its own write, took the newest entry
inside that old window whose `toState` was Planning — the person's reopen,
`fromState` Done — and "restored" Done.

WHAT LINEAR ACTUALLY DOES, recorded rather than assumed. The fixture
`fixtures/dre-5034-history-2026-09-29.json` is DRE-5034's real history, read
on 2026-09-29: `history(first: 12)` and `history(last: 12)` asked in ONE query,
and `history(first: 100)` paged to the end. `first:` answers NEWEST FIRST;
`last:` answers the OLDEST n, ascending — the same shape DRE-3250 measured for
`comments`. The fake below serves a history that way and is itself pinned
against the recording, so every test here runs against Linear's order, not
against the order the code imagined.

THE FIX UNDER TEST:
  * a write to the lane the card is already in writes nothing and repairs
    nothing;
  * the read-back reads the NEWEST entries (`history(first: n)`);
  * the read-back only accepts an entry created at or after the instant just
    before its own mutation — an older entry is somebody else's.

Run: cd bureau-pipeline && python3 -m pytest tests/test_linear_ops_state_guard.py -v
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import linear_ops  # noqa: E402

RECORDING = json.loads(
    (ROOT / "tests" / "fixtures" / "dre-5034-history-2026-09-29.json").read_text()
)
#: DRE-5034's whole history, newest first — the order Linear answered it in.
HISTORY = RECORDING["history_first_100"]

# DRE-5034's own lanes, ids as Linear recorded them.
PLANNING = "4980c9ea-1676-4fce-aa69-b3407ac424dd"
DONE = "2b6c0bcb-382d-401c-8701-728a1f361f96"
INTAKE = "b1020ed8-97bf-40d4-a3ac-d36adf4b8abf"
STATES = {
    "Intake": (INTAKE, "unstarted"),
    "Planning": (PLANNING, "unstarted"),
    "Todo": ("st-todo", "unstarted"),
    "In Progress": ("st-inprogress", "started"),
    "Done": (DONE, "completed"),
}


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _move(entry: dict) -> tuple:
    return (
        (entry.get("fromState") or {}).get("name"),
        (entry.get("toState") or {}).get("name"),
    )


def _history_until(iso: str) -> list[dict]:
    """DRE-5034's history as it stood at `iso` (inclusive), newest first."""
    return [copy.deepcopy(e) for e in HISTORY if _at(e["createdAt"]) <= _at(iso)]


def _shifted(entries: list[dict], newest_at: datetime) -> list[dict]:
    """`entries` moved in time so the newest lands at `newest_at`."""
    delta = newest_at - _at(entries[0]["createdAt"])
    return [
        {**e, "createdAt": _iso(_at(e["createdAt"]) + delta)} for e in entries
    ]


def _window(history: list[dict], query: str) -> list[dict]:
    """Serve a `history(first|last: n)` window the way Linear does."""
    m = re.search(r"history\((first|last):\s*(\d+)", query)
    assert m, f"a history read must bound its window: {query}"
    n = int(m.group(2))
    if m.group(1) == "first":
        return list(history[:n])
    return list(reversed(history[-n:]))


class _Linear:
    """A card with a real history, served in Linear's order.

    `history` is kept NEWEST FIRST. A write prepends its own entry stamped with
    the moment it lands; a write that changes nothing records nothing.
    """

    def __init__(self, current: str, history: list[dict] | None = None):
        self.current = current
        self.history = list(history or [])
        self.updates: list[str] = []
        self.history_queries: list[str] = []

    def _node(self, name):
        sid, stype = STATES[name]
        return {"id": sid, "name": name, "type": stype}

    def _name_for(self, state_id):
        for name, (sid, _t) in STATES.items():
            if sid == state_id:
                return name
        raise AssertionError(f"unknown stateId {state_id!r}")

    def gql(self, query, variables=None):
        v = variables or {}
        q = " ".join(query.split())
        if "issueUpdate" in q:
            sid = v["input"]["stateId"]
            self.updates.append(sid)
            name = self._name_for(sid)
            if name != self.current:
                self.history.insert(0, {
                    "createdAt": _iso(datetime.now(UTC)),
                    "fromState": self._node(self.current),
                    "toState": self._node(name),
                })
            self.current = name
            return {"issueUpdate": {"success": True}}
        if "history(" in q:
            self.history_queries.append(q)
            return {"issue": {"history": {"nodes": _window(self.history, q)}}}
        if "issue(id: $id) { id identifier title team" in q:
            return {"issue": {
                "id": "card-uuid", "identifier": "DRE-5034",
                "title": "[EPIC] the morning briefing v2", "team": {"id": "team-1"},
                "state": self._node(self.current),
                "labels": {"nodes": []}, "children": {"nodes": [{"id": "kid"}]},
            }}
        if "workflowStates" in q:
            return {"workflowStates": {"nodes": [
                {"id": sid, "name": name, "type": stype}
                for name, (sid, stype) in STATES.items()
            ]}}
        raise AssertionError(f"unexpected gql query: {q}")


def _run(fn, fake, *args):
    buf = io.StringIO()
    with patch.object(linear_ops, "gql", side_effect=fake.gql):
        with redirect_stdout(buf):
            result = fn(*args)
    return result, buf.getvalue()


# --------------------------------------------------------------------------
# 0. The fake answers in Linear's order — pinned against the recording
# --------------------------------------------------------------------------
def test_recorded_first_window_is_the_newest_entries_newest_first():
    """`history(first: 12)` on DRE-5034 answered the twelve NEWEST entries,
    newest first. The fake must reproduce that byte for byte, or nothing below
    proves anything about Linear."""
    assert _window(HISTORY, "history(first: 12)") == RECORDING["history_first_12"]
    stamps = [e["createdAt"] for e in RECORDING["history_first_12"]]
    assert stamps == sorted(stamps, reverse=True)
    assert stamps[0] == max(e["createdAt"] for e in HISTORY)


def test_recorded_last_window_is_the_oldest_entries_oldest_first():
    """`history(last: 12)` answered the twelve OLDEST, ascending — the window
    the old read-back took for the newest. It ends on the person's 08:28 PT
    reopen and the repair's 08:29 PT close that "restored" from it."""
    assert _window(HISTORY, "history(last: 12)") == RECORDING["history_last_12"]
    stamps = [e["createdAt"] for e in RECORDING["history_last_12"]]
    assert stamps == sorted(stamps)
    assert stamps[0] == min(e["createdAt"] for e in HISTORY)
    assert _move(RECORDING["history_last_12"][-1]) == ("Planning", "Done")
    assert _move(RECORDING["history_last_12"][-3]) == ("Done", "Planning")


# --------------------------------------------------------------------------
# 1. The read-back reads the newest entries, and only its own
# --------------------------------------------------------------------------
# DRE-5034 at 17:52:47 PT on 2026-09-28: the person's Intake → Planning is the
# newest entry, the 08:28 and 15:16 PT Done → Planning reopens are older.
AT_THIRD_REOPEN = "2026-09-29T00:52:47.316Z"


def test_dre_5034_shaped_history_is_not_read_as_a_clobbered_done():
    history = _history_until(AT_THIRD_REOPEN)
    assert len(history) > 10
    assert _move(history[0]) == ("Intake", "Planning")
    assert any(_move(e) == ("Done", "Planning") for e in history[1:])

    fake = _Linear("Planning", history)
    # The planner's run starts its write half a minute after the reopen.
    since = _at(AT_THIRD_REOPEN) + timedelta(seconds=43)
    got, _ = _run(linear_ops._clobbered_terminal_state, fake,
                  "DRE-5034", PLANNING, since)
    assert got is None, (
        f"the repair picked up a person's reopen as its own write: {got}"
    )


def test_newest_move_into_the_lane_is_read_even_when_it_is_the_newest_of_many():
    """Even taking the person's Intake → Planning as the write under
    verification, its `fromState` is Intake — nothing terminal to restore. The
    old window could not see this entry at all."""
    fake = _Linear("Planning", _history_until(AT_THIRD_REOPEN))
    since = _at(AT_THIRD_REOPEN) - timedelta(seconds=1)
    got, _ = _run(linear_ops._clobbered_terminal_state, fake,
                  "DRE-5034", PLANNING, since)
    assert got is None


def test_the_read_back_asks_for_the_newest_entries():
    fake = _Linear("Planning", _history_until(AT_THIRD_REOPEN))
    _run(linear_ops._clobbered_terminal_state, fake, "DRE-5034", PLANNING,
         datetime.now(UTC))
    assert fake.history_queries, "the read-back never read the history"
    for q in fake.history_queries:
        assert re.search(r"history\(first:", q), (
            f"`last:` is the OLDEST entries on Linear, not the newest: {q}"
        )


def test_a_matching_entry_that_predates_the_write_is_not_ours():
    """The only move into the lane is a person's Done → Planning 40 seconds
    before the write started. Nothing at or after that instant: nothing this
    write did, nothing to repair."""
    now = datetime.now(UTC)
    reopen = {
        "createdAt": _iso(now - timedelta(seconds=40)),
        "fromState": {"id": DONE, "name": "Done", "type": "completed"},
        "toState": {"id": PLANNING, "name": "Planning", "type": "unstarted"},
    }
    fake = _Linear("Planning", [reopen])
    got, _ = _run(linear_ops._clobbered_terminal_state, fake,
                  "DRE-5034", PLANNING, now)
    assert got is None


def test_an_entry_created_after_the_write_started_is_still_repaired():
    """The repair's real job survives on a long history: a Done that raced our
    write shows up as OUR entry, newest, `fromState` Done — and is returned."""
    now = datetime.now(UTC)
    ours = {
        "createdAt": _iso(now + timedelta(milliseconds=300)),
        "fromState": {"id": DONE, "name": "Done", "type": "completed"},
        "toState": {"id": PLANNING, "name": "Planning", "type": "unstarted"},
    }
    history = [ours] + _shifted(_history_until(AT_THIRD_REOPEN),
                                now - timedelta(minutes=5))
    fake = _Linear("Planning", history)
    got, _ = _run(linear_ops._clobbered_terminal_state, fake,
                  "DRE-5034", PLANNING, now)
    assert got and got["id"] == DONE


# --------------------------------------------------------------------------
# 2. A write to the lane the card is already in is a no-op
# --------------------------------------------------------------------------
# DRE-5034 at 15:16:18 PT: the person's 15:16:17 Done → Planning is the newest
# lane move — the second reopen the planner's run closed at 15:16:59.
AT_SECOND_REOPEN = "2026-09-28T22:16:18.720Z"


def test_dre_5034_replay_state_planning_on_a_reopened_card_writes_nothing():
    history = _history_until(AT_SECOND_REOPEN)
    reopen_at = datetime.now(UTC) - timedelta(seconds=40)
    history = _shifted(history, reopen_at + timedelta(seconds=1))
    assert _move(history[1]) == ("Done", "Planning")
    assert len(history) > 10

    fake = _Linear("Planning", history)
    _run(linear_ops.cmd_state, fake, "DRE-5034", "Planning")
    assert fake.updates == [], (
        f"a person's reopen was closed again by a no-op write: {fake.updates}"
    )
    assert fake.current == "Planning"


def test_pre_write_read_in_the_target_lane_sends_no_mutation():
    fake = _Linear("Todo")
    ok, out = _run(linear_ops.guarded_state_write, fake, "DRE-5034",
                   {"id": "card-uuid", "state": fake._node("Todo")},
                   STATES["Todo"][0], "unstarted", "Todo")
    assert ok is True
    assert fake.updates == []
    assert fake.history_queries == [], "a write that did not happen has nothing to read back"
    assert "already" in out.lower()


def test_terminal_target_already_reached_sends_no_mutation():
    """A terminal target skips the pre-write read, so the no-op check uses the
    issue already in hand: closing a Done card writes nothing."""
    fake = _Linear("Done")
    _run(linear_ops.cmd_state, fake, "DRE-5034", "Done")
    assert fake.updates == []
    assert fake.current == "Done"


def test_a_real_move_still_writes_once():
    fake = _Linear("Intake", _history_until(AT_THIRD_REOPEN))
    _run(linear_ops.cmd_state, fake, "DRE-5034", "Planning")
    assert fake.updates == [PLANNING]
    assert fake.current == "Planning"


def test_a_real_close_still_writes_once():
    fake = _Linear("Planning")
    _run(linear_ops.cmd_state, fake, "DRE-5034", "Done")
    assert fake.updates == [DONE]
