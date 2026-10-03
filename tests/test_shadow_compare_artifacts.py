"""The read door's shadow comparison reports differences in the DATA, never in
the queries (DRE-5730).

Portico's first shadow pass (Reconcile run 37145213637, 2026-10-03 11:42 PT)
printed seven unexplained `read-door-diff:` lines. Every one was an artifact of
the comparison, checked against Linear and the console's database:

  * board `parent` on DRE-5485, 5535, 5538 and 5541, `linear=None`:
    `_fetch_active_cards` never selects `parent`, so the Linear side read as
    None for a field it never asked for;
  * backlog `priority` on DRE-5474, 5537 and 5542, `door=0 linear=None`:
    `backlog_children` never selects `priority` (and 0 is Linear's "No
    priority" in any case);
  * the `door-newer` class: the door serves `updatedAt` as the later of the
    card's stamp and its newest stored comment, while Linear does not move
    `updatedAt` for a comment that lands within about a minute of its last
    bump (DRE-5485: updatedAt 17:32:07.541Z, newest comment 17:32:40.075Z).

The rules pinned here: a field is compared only when BOTH nodes carry it (a
key a query never selected is absent; a field selected and null is present and
compared); priority 0 and no priority are one fact; and each side's stamp is
the later of its `updatedAt` and the newest comment in its own window.
"""
from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
from bureau_read_fakes import card  # noqa: E402

AS_OF = "2026-10-03T18:42:40.000Z"


def _without(node: dict, *keys: str) -> dict:
    """`node` as a query that never selected `keys` returns it."""
    out = copy.deepcopy(node)
    for key in keys:
        out.pop(key, None)
    return out


def _with_comment(node: dict, at: str, body: str = "a receipt") -> dict:
    out = copy.deepcopy(node)
    out["comments"]["nodes"].insert(0, {"body": body, "createdAt": at,
                                        "user": {"id": "fleet-user"}})
    return out


# ── a field one read never asked for is not compared ────────────────────────


def test_a_parent_the_linear_query_never_selected_is_not_a_difference():
    door = [card("DRE-5485", "In Progress", parent="DRE-5483", parent_lane="Intake",
                 updated="2026-10-03T17:32:40.075Z")]
    linear = [_without(card("DRE-5485", "In Progress", updated="2026-10-03T17:32:07.541Z"),
                       "parent")]
    assert bureau_read.compare(door, linear, door_as_of=AS_OF) == []


def test_a_parent_linear_selected_and_sent_as_null_is_still_compared():
    door = [card("DRE-5485", "In Progress", parent="DRE-5483", parent_lane="Intake")]
    linear = [card("DRE-5485", "In Progress")]  # selected: `parent: null`
    assert "parent" in linear[0] and linear[0]["parent"] is None
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.field, d.door, d.linear) for d in diffs] == [
        ("parent", ("DRE-5483", "Intake"), None)]
    assert not diffs[0].explained


def test_a_priority_the_linear_query_never_selected_is_not_a_difference():
    door = [card("DRE-5474", "Backlog", priority=0)]
    linear = [_without(card("DRE-5474", "Backlog"), "priority")]
    assert bureau_read.compare(door, linear, door_as_of=AS_OF) == []


def test_a_field_only_the_door_carries_is_never_compared_in_either_direction():
    door = [_without(card("DRE-1", "Todo", title="door title"), "title")]
    linear = [card("DRE-1", "Todo", title="linear title")]
    assert bureau_read.compare(door, linear, door_as_of=AS_OF) == []


# ── equivalent empties are one fact ─────────────────────────────────────────


def test_priority_zero_and_no_priority_are_the_same():
    """0 is Linear's "No priority"; a null priority says the same thing."""
    door = [card("DRE-5537", "Backlog", priority=0)]
    linear = [card("DRE-5537", "Backlog", priority=None)]
    assert bureau_read.compare(door, linear, door_as_of=AS_OF) == []
    assert bureau_read.compare(door, [card("DRE-5537", "Backlog", priority=0)],
                               door_as_of=AS_OF) == []


def test_a_real_priority_difference_is_still_reported():
    door = [card("DRE-5537", "Backlog", priority=0)]
    linear = [card("DRE-5537", "Backlog", priority=2)]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.klass, d.field) for d in diffs] == [("same-stamp-different-value", "priority")]


# ── the stamp: the later of updatedAt and the newest comment, both sides ────


def test_a_comment_after_linears_stamp_does_not_make_the_door_newer():
    """The door's stamp is its newest comment's; Linear's own `updatedAt` did
    not move for that comment. Same activity, same stamp: a real difference
    here is the shadow day's failure, not a `door-newer` one."""
    base = card("DRE-5485", "In Progress", title="door title",
                updated="2026-10-03T17:32:40.075Z")
    door = [_with_comment(base, "2026-10-03T17:32:40.075Z")]
    live = card("DRE-5485", "In Progress", title="linear title",
                updated="2026-10-03T17:32:07.541Z")
    linear = [_with_comment(live, "2026-10-03T17:32:40.075Z")]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [(d.klass, d.field) for d in diffs] == [("same-stamp-different-value", "title")]


def test_a_linear_comment_the_door_lacks_is_still_door_older():
    door = [card("DRE-5541", "Todo", title="door title", updated="2026-10-03T18:42:27.710Z")]
    live = card("DRE-5541", "Todo", title="linear title", updated="2026-10-03T18:42:27.710Z")
    linear = [_with_comment(live, "2026-10-03T18:42:29.585Z")]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert [d.klass for d in diffs if d.field == "title"] == ["door-older"]


def test_a_door_stamp_later_than_all_of_linears_activity_is_still_door_newer():
    door = [card("DRE-1", "Todo", updated="2026-10-03T18:50:00.000Z")]
    linear = [card("DRE-1", "In Review", updated="2026-10-03T18:00:00.000Z")]
    diffs = bureau_read.compare(door, linear, door_as_of=AS_OF)
    assert diffs[0].klass == "door-newer" and not diffs[0].explained


# ── the reproduction: run 37145213637's cards through Reconcile ─────────────


def _selected(query: str, node: dict) -> dict:
    """`node` cut to the top-level fields `query` actually selects — what the
    real Linear sends back for that query."""
    body = " ".join(query.split())
    return {k: copy.deepcopy(v) for k, v in node.items()
            if re.search(r"(?<![\w$])%s(?![\w:])" % re.escape(k), body)}


def test_run_37145213637_replays_with_no_unexplained_difference(monkeypatch, capsys):
    """The seven cards that printed unexplained lines, as the door served them
    and as each Linear query selects them, through Reconcile's own board and
    Backlog comparisons."""
    import reconcile

    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    repo = ("repo:portico",)
    board_door = [
        _with_comment(card("DRE-5485", "In Progress", labels=repo, parent="DRE-5483",
                           parent_lane="Intake", updated="2026-10-03T17:32:40.075Z"),
                      "2026-10-03T17:32:40.075Z"),
        _with_comment(card("DRE-5541", "Todo", labels=repo, parent="DRE-5485",
                           updated="2026-10-03T18:42:29.585Z"),
                      "2026-10-03T18:42:29.585Z"),
    ]
    board_linear = [
        _with_comment(card("DRE-5485", "In Progress", labels=repo, parent="DRE-5483",
                           parent_lane="Intake", updated="2026-10-03T17:32:07.541Z"),
                      "2026-10-03T17:32:40.075Z"),
        _with_comment(card("DRE-5541", "Todo", labels=repo, parent="DRE-5485",
                           updated="2026-10-03T18:42:27.710Z"),
                      "2026-10-03T18:42:29.585Z"),
    ]
    backlog_door = [
        card("DRE-5474", "Backlog", labels=repo, parent="DRE-5464", parent_lane="Green Light",
             priority=0, updated="2026-10-03T17:49:42.944Z"),
        _with_comment(card("DRE-5542", "Backlog", labels=repo, parent="DRE-5485",
                           priority=0, updated="2026-10-03T00:24:54.036Z"),
                      "2026-10-03T00:24:54.036Z"),
    ]
    backlog_linear = [
        card("DRE-5474", "Backlog", labels=repo, parent="DRE-5464", parent_lane="Green Light",
             priority=0, updated="2026-10-03T17:49:42.944Z"),
        _with_comment(card("DRE-5542", "Backlog", labels=repo, parent="DRE-5485",
                           priority=0, updated="2026-10-03T00:24:54.005Z"),
                      "2026-10-03T00:24:54.036Z"),
    ]

    queries = {}

    def gql_paged(query, variables=None):
        if "$states" in query:
            queries["board"] = query
            return [_selected(query, n) for n in board_linear]
        queries["backlog"] = query
        return [_selected(query, n) for n in backlog_linear]

    monkeypatch.setattr(reconcile.linear_ops, "gql_paged", gql_paged)
    monkeypatch.setattr(reconcile, "complete_inverse_relations", lambda cards: None)
    linear_board = reconcile._fetch_active_cards(reconcile.SWEPT_LANES)
    linear_backlog = reconcile.backlog_children(None, from_linear=True, stamped=True)
    # The reproduction is honest only if the queries really leave these out.
    assert all("parent" not in n for n in linear_board)
    assert all("priority" not in n for n in linear_backlog)

    door_read = bureau_read.DoorRead(nodes=board_door, as_of=AS_OF)
    reconcile._shadow_door["board"] = door_read
    reconcile._shadow_compare_board(linear_board)
    reconcile._shadow_compare_backlog(bureau_read.DoorRead(nodes=backlog_door, as_of=AS_OF),
                                      linear_backlog)
    out = capsys.readouterr().out
    assert "read-door-diff: board compared 2 card(s): 0 explained (door-older), 0 unexplained" in out
    assert "read-door-diff: backlog compared 2 card(s): 0 explained (door-older), 0 unexplained" \
        in out
