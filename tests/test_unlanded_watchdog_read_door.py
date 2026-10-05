"""The unlanded-work watchdog reads its cards' lanes through the read door (DRE-5847).

`flag_unlanded_work` used to ask Linear for the lane of every stale,
PR-less branch's card, one `card_state` request per branch per full sweep —
10-14 requests a pass in agent-bureau, about 100 an hour across the fleet —
although our database already holds every card's lane.

What is pinned here, against a REAL HTTP fake door (`bureau_read_fakes.py`):

  * in `BUREAU_READ=on` the pass makes ONE `/cards` read for every branch's
    card, and no Linear lane read at all;
  * the door's lane is the one the watchdog decides on;
  * inside a pass, the door's comment window answers the once-only check, so
    it needs no Linear read either;
  * a door that cannot answer whole (UNKNOWN, a card it does not hold, a door
    that is down) sends those cards to Linear exactly as before, and the sweep
    log says how many;
  * `linear-hold` skips the phase rather than spend the held bucket;
  * with the door off — and in `shadow` — nothing changes: no door request,
    one Linear lane read per stale branch, no new log line.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import bureau_read  # noqa: E402
import reconcile  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

A = ("agent/DRE-701-first", "DRE-701")
B = ("agent/DRE-702-second", "DRE-702")
C = ("agent/DRE-703-third", "DRE-703")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_PIPELINE_REF",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    reconcile._write_failures.clear()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()
    yield
    reconcile._write_failures.clear()
    bureau_read.reset_for_tests()
    reconcile.reset_sweep_cards()


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def _fake_gh(branches: dict[str, str], pr_refs=()):
    """GitHub as the watchdog reads it: every branch is stale, ahead and PR-less
    unless its name is in `pr_refs`."""
    def gh(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps([{"headRefName": r} for r in pr_refs])
        path = args[1]
        if path == f"repos/{reconcile.REPO}":
            return "main"
        if path.endswith("/branches"):
            return "\n".join(json.dumps({"name": n, "sha": s}) for n, s in branches.items())
        if path.endswith("/pulls"):
            return "[]"
        if "/compare/" in path:
            return json.dumps({"ahead": 2, "last": _iso(reconcile.UNLANDED_MINUTES + 30)})
        return ""
    return gh


@contextlib.contextmanager
def door_at(monkeypatch, *door_cards, mode="on"):
    world = {c["identifier"]: c for c in door_cards}
    with FakeIssuer() as issuer, FakeDoor(world) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        yield door


def sweep(branches, *, linear_lanes=None, pr_refs=()):
    """One `flag_unlanded_work()` pass. Linear's lane read is `card_state`;
    any other Linear query fails the test. Returns (comments, card_state mock)."""
    linear_lanes = linear_lanes or {}
    comments: list[tuple[str, str]] = []
    fake = _fake_gh(dict(branches), pr_refs)

    def no_linear(query, variables=None):
        raise AssertionError(f"unexpected Linear query: {' '.join(query.split())[:120]}")

    with mock.patch.object(reconcile, "gh", side_effect=fake), \
            mock.patch.object(reconcile, "gh_read", side_effect=fake), \
            mock.patch.object(reconcile, "active_cards", return_value=[]), \
            mock.patch.object(reconcile, "card_state",
                              side_effect=lambda i: linear_lanes.get(i, "In Progress")) as live, \
            mock.patch.object(reconcile.linear_ops, "gql", side_effect=no_linear), \
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
            mock.patch.object(reconcile.linear_ops, "cmd_comment",
                              side_effect=lambda ident, body: comments.append((ident, body))):
        reconcile.flag_unlanded_work()
    return comments, live


def _posted(comments) -> list[str]:
    return sorted(ident for ident, _ in comments)


# ── on: one door read, no Linear lane read ──────────────────────────────────


def test_one_door_cards_read_for_every_branch_and_no_linear_lane_read(monkeypatch):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40, C[0]: "c" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress"), card(B[1], "In Progress"),
                 card(C[1], "In Review")) as door:
        comments, live = sweep(branches)
    asked = door.asked("/cards")
    assert len(asked) == 1, asked
    assert sorted(asked[0]["query"]["ids"].split(",")) == [A[1], B[1], C[1]]
    assert len(door.requests) == 1  # nothing else asked of the door either
    live.assert_not_called()
    assert _posted(comments) == [A[1], B[1], C[1]]


def test_the_door_lane_is_the_one_the_watchdog_decides_on(monkeypatch):
    """Linear (the stub) says every card is In Progress; the door says one is
    Done and one waits in Todo. Only the door's In Progress card is reported."""
    branches = {A[0]: "a" * 40, B[0]: "b" * 40, C[0]: "c" * 40}
    with door_at(monkeypatch, card(A[1], "Done"), card(B[1], "Todo"),
                 card(C[1], "In Progress")):
        comments, live = sweep(branches)
    live.assert_not_called()
    assert _posted(comments) == [C[1]]


def test_a_branch_with_a_pull_request_is_not_asked_about(monkeypatch):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress"), card(B[1], "In Progress")) as door:
        comments, _ = sweep(branches, pr_refs=[B[0]])
    assert door.asked("/cards")[0]["query"]["ids"] == A[1]
    assert _posted(comments) == [A[1]]


def test_two_branches_of_one_card_ask_for_it_once(monkeypatch):
    branches = {A[0]: "a" * 40, "agent/DRE-701-second-attempt": "d" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress")) as door:
        comments, live = sweep(branches)
    assert [r["query"]["ids"] for r in door.asked("/cards")] == [A[1]]
    live.assert_not_called()
    assert _posted(comments) == [A[1], A[1]]


def test_no_candidate_branch_asks_the_door_nothing(monkeypatch):
    with door_at(monkeypatch) as door:
        comments, live = sweep({})
    assert door.requests == []
    live.assert_not_called()
    assert comments == []


def test_the_door_comment_window_answers_the_once_only_check_without_linear(monkeypatch):
    """Inside a pass the door's comment window goes into the pass cache, so
    `comment_bodies` finds A's earlier notice without a Linear read: A stays
    quiet, and B — whose window holds no notice — is reported, still with no
    Linear read. Drop the seeding and every branch asks Linear for its thread."""
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    warned = f"🚨 {reconcile.UNLANDED_TAG} branch {A[0]}: the branch carries work"
    queries: list[str] = []
    comments: list[tuple[str, str]] = []
    fake = _fake_gh(branches)

    def no_linear(query, variables=None):
        queries.append(" ".join(query.split())[:120])
        raise AssertionError("unexpected Linear query")

    reconcile.linear_ops.open_pass()
    try:
        with door_at(monkeypatch, card(A[1], "In Progress", comments=[warned]),
                     card(B[1], "In Progress", comments=["an unrelated note"])), \
                mock.patch.object(reconcile, "gh", side_effect=fake), \
                mock.patch.object(reconcile, "gh_read", side_effect=fake), \
                mock.patch.object(reconcile, "active_cards", return_value=[]), \
                mock.patch.object(reconcile, "card_state") as live, \
                mock.patch.object(reconcile.linear_ops, "gql", side_effect=no_linear), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment",
                                  side_effect=lambda ident, body: comments.append((ident, body))):
            reconcile.flag_unlanded_work()
    finally:
        reconcile.linear_ops.reset_pass_cache()
    assert queries == []
    assert reconcile._write_failures == []
    live.assert_not_called()
    assert _posted(comments) == [B[1]]


def test_the_sweep_log_says_the_door_answered_and_linear_read_none(monkeypatch, capsys):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress"), card(B[1], "In Progress")):
        sweep(branches)
    out = capsys.readouterr().out
    assert "unlanded: card lanes — 2 from the door in one read, 0 read from Linear" in out


# ── the door cannot answer: only those cards go to Linear ──────────────────


def test_a_door_unknown_sends_the_cards_to_linear_and_says_how_many(monkeypatch, capsys):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress"), card(B[1], "In Progress")) as door:
        door.routes["/cards"] = door.unknown("stale")
        comments, live = sweep(branches, linear_lanes={A[1]: "Done"})
    assert sorted(c.args[0] for c in live.call_args_list) == [A[1], B[1]]
    assert _posted(comments) == [B[1]]  # Linear's lane decided: A is Done
    out = capsys.readouterr().out
    assert "unlanded: card lanes — 0 from the door in one read, 2 read from Linear" in out


def test_a_card_the_door_does_not_hold_falls_back_to_linear(monkeypatch):
    """`/cards` is all-or-nothing: a card outside the door's tenant makes the
    whole read UNKNOWN (`not-found`), and the cards are read from Linear."""
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "In Progress")):  # B is not stored
        comments, live = sweep(branches)
    assert sorted(c.args[0] for c in live.call_args_list) == [A[1], B[1]]
    assert _posted(comments) == [A[1], B[1]]


def test_only_the_stale_branches_unknown_cards_are_read_from_linear(monkeypatch, capsys):
    """The fallback is lazy: a card the door could not answer is read from
    Linear only when its branch reaches the lane check, as it was before."""
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}

    def fake(*args):  # A's last commit is a minute old — still moving
        if args[0] == "api" and "/compare/" in args[1]:
            minutes = 1 if args[1].endswith(A[0]) else reconcile.UNLANDED_MINUTES + 30
            return json.dumps({"ahead": 2, "last": _iso(minutes)})
        return _fake_gh(branches)(*args)

    with door_at(monkeypatch) as door:
        door.routes["/cards"] = door.unknown("stale")
        with mock.patch.object(reconcile, "gh", side_effect=fake), \
                mock.patch.object(reconcile, "gh_read", side_effect=fake), \
                mock.patch.object(reconcile, "active_cards", return_value=[]), \
                mock.patch.object(reconcile, "card_state", return_value="In Progress") as live, \
                mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment"):
            reconcile.flag_unlanded_work()
    assert [c.args[0] for c in live.call_args_list] == [B[1]]
    assert "0 from the door in one read, 1 read from Linear" in capsys.readouterr().out


def test_an_unavailable_door_behaves_exactly_as_today(monkeypatch):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "Done"), card(B[1], "Done")) as door:
        door.routes["/cards"] = (503, {"error": "closed"})
        comments, live = sweep(branches)
    assert sorted(c.args[0] for c in live.call_args_list) == [A[1], B[1]]
    assert _posted(comments) == [A[1], B[1]]  # Linear's In Progress decided


def test_linear_hold_skips_the_phase_and_makes_no_linear_lane_read(monkeypatch):
    """`linear-hold`: falling back would spend the held bucket, so the phase is
    skipped (BoardHeld, one line from its phase) and the hold stands for the
    rest of the pass."""
    branches = {A[0]: "a" * 40}
    calls: list[str] = []
    with door_at(monkeypatch, card(A[1], "In Progress")) as door:
        door.routes["/cards"] = door.unknown(bureau_read.LINEAR_HOLD)
        fake = _fake_gh(branches)
        with mock.patch.object(reconcile, "gh", side_effect=fake), \
                mock.patch.object(reconcile, "gh_read", side_effect=fake), \
                mock.patch.object(reconcile, "active_cards", return_value=[]), \
                mock.patch.object(reconcile, "card_state", side_effect=calls.append), \
                mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]), \
                mock.patch.object(reconcile.linear_ops, "cmd_comment") as posted:
            with pytest.raises(reconcile.BoardHeld):
                reconcile.flag_unlanded_work()
    assert calls == []
    posted.assert_not_called()
    assert reconcile._door_hold == [bureau_read.LINEAR_HOLD]


# ── off and shadow: exactly as today ────────────────────────────────────────


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_read_each_lane_from_linear_as_today(monkeypatch, capsys, mode):
    branches = {A[0]: "a" * 40, B[0]: "b" * 40}
    with door_at(monkeypatch, card(A[1], "Done"), card(B[1], "Done"), mode=mode) as door:
        comments, live = sweep(branches)
    assert door.requests == []
    assert sorted(c.args[0] for c in live.call_args_list) == [A[1], B[1]]
    assert _posted(comments) == [A[1], B[1]]
    assert "card lanes" not in capsys.readouterr().out


def test_with_no_door_configured_nothing_changes(capsys):
    comments, live = sweep({A[0]: "a" * 40})
    assert [c.args[0] for c in live.call_args_list] == [A[1]]
    assert _posted(comments) == [A[1]]
    assert "card lanes" not in capsys.readouterr().out
