"""RED-first: the unlanded-work watchdog leaves a proof's record branch to the
proof dispatcher (DRE-8438).

THE GAP: `_flag_one_unlanded_branch` reports any `agent/DRE-*` branch that has
commits and never had a pull request (DRE-2682). Once the record pull request
opens only when the record can merge, a waiting proof's record branch,
`agent/DRE-<n>-proof-record`, is often exactly that — so the waiting proof
would get an "unlanded" comment and, past its due date, a late-list entry under
`UNLANDED_BRANCH_WAITS_ON`, on top of its own `🔬 proof-waiting` hold.

FIX UNDER TEST: a proof-record branch (`proof_record.proof_record_branch`)
answers None before its pull requests are read. The proof dispatcher owns that
branch, and the card's hold says what it waits on. Every other branch is judged
exactly as before.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unlanded_proof_record_branch.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("REPO_SLUG", "test")
os.environ.setdefault("GH_TOKEN", "x")

import hold_deadline  # noqa: E402
import reconcile  # noqa: E402

RECORD = "agent/DRE-123-proof-record"
FIX = "agent/DRE-123-fix-thing"
SHA = "d" * 40


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """This sweep owns `test`; the read door is off; the ledgers start clean."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "test")
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/test")
    monkeypatch.setattr(reconcile, "_default_branch", None)
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _iso(hours_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(hours=hours_ago))
            .strftime("%Y-%m-%dT%H:%M:%S.000Z"))


class World:
    """GitHub and Linear as the watchdog reads them: the branch has three
    commits of its own, its last one idle far past `UNLANDED_MINUTES` and past
    the late-hold due date, and no pull request was ever opened for it."""

    def __init__(self):
        self.reads: list[tuple] = []
        self.comments: list[tuple[str, str]] = []

    def gh(self, *args):
        self.reads.append(args)
        path = args[1] if args[0] == "api" else ""
        if path == f"repos/{reconcile.REPO}":
            return "main"
        if "/commits/" in path and path.endswith("/pulls"):
            return "[]"
        if "/compare/" in path:
            return json.dumps({"ahead": 3, "last": _iso(400.0)})
        return ""

    def patches(self):
        return (
            mock.patch.object(reconcile, "gh", side_effect=self.gh),
            mock.patch.object(reconcile, "gh_read", side_effect=self.gh),
            mock.patch.object(reconcile, "card_state", return_value="In Review"),
            mock.patch.object(reconcile.linear_ops, "comment_bodies", return_value=[]),
            mock.patch.object(reconcile.linear_ops, "cmd_comment",
                              side_effect=lambda i, b: self.comments.append((i, b))),
        )

    def flag(self, name):
        p = self.patches()
        with p[0], p[1], p[2], p[3], p[4]:
            return reconcile._flag_one_unlanded_branch(
                {"name": name, "sha": SHA}, set(), {}, [])

    def pull_reads(self):
        return [r for r in self.reads
                if r[0] == "api" and "/commits/" in r[1] and r[1].endswith("/pulls")]


# --------------------------------------------------------------------------- #
# one branch                                                                   #
# --------------------------------------------------------------------------- #

def test_a_proof_record_branch_answers_none_without_reading_its_pull_requests():
    """The acceptance case: commits, no pull request ever, idle past
    `UNLANDED_MINUTES` — and still None, with no read of its pull requests and
    no comment. On main it is reported."""
    world = World()
    assert world.flag(RECORD) is None
    assert world.pull_reads() == [], "its pull requests are never read"
    assert world.comments == [], "a waiting proof gets no unlanded comment"


def test_a_proof_record_branch_is_never_listed_late():
    """No `LateHold` waiting on `UNLANDED_BRANCH_WAITS_ON` — the branch is past
    the due date on its last commit's clock, and still nothing is late."""
    found = World().flag(RECORD)
    assert not isinstance(found, hold_deadline.LateHold)
    assert found != reconcile.UNLANDED_UNREAD


def test_any_other_branch_of_the_same_card_is_still_reported():
    """`agent/DRE-123-fix-thing` in the same state is reported, as today: the
    comment, and the late-list entry waiting on a pull request from it."""
    world = World()
    found = world.flag(FIX)
    assert world.pull_reads(), "its pull requests are read, as today"
    assert isinstance(found, hold_deadline.LateHold)
    assert found.ident == "DRE-123"
    assert found.waits_on == reconcile.UNLANDED_BRANCH_WAITS_ON.format(branch=FIX)
    assert [i for i, _ in world.comments] == ["DRE-123"]
    assert f"branch {FIX}:" in world.comments[0][1]


@pytest.mark.parametrize("name", [
    "agent/DRE-123-proof-record-2",
    "agent/DRE-123-proof-recorder",
    "agent/DRE-123-not-a-proof-record",
])
def test_only_the_exact_record_branch_shape_is_left_alone(name):
    """The rule is `proof_record.proof_record_branch`'s, never a substring:
    a branch that merely mentions the words is judged as any other."""
    world = World()
    assert isinstance(world.flag(name), hold_deadline.LateHold)
    assert world.comments


# --------------------------------------------------------------------------- #
# the whole pass                                                               #
# --------------------------------------------------------------------------- #

def test_a_pass_lists_the_fix_branch_late_and_never_the_record_branch():
    """Through `flag_unlanded_work`: both branches stand PR-less on one card;
    only the fix branch is commented on and handed to `settle`."""
    world = World()
    settle = mock.Mock()
    branches = [{"name": RECORD, "sha": SHA}, {"name": FIX, "sha": "e" * 40}]
    p = world.patches()
    with p[0], p[1], p[2], p[3], p[4], \
            mock.patch.object(reconcile, "pr_head_refs", return_value=set()), \
            mock.patch.object(reconcile, "card_branches", return_value=branches), \
            mock.patch.object(reconcile, "_flag_hand_built_idle"), \
            mock.patch.object(hold_deadline, "settle", settle):
        reconcile.flag_unlanded_work()
    settle.assert_called_once()
    late = settle.call_args.args[2]
    assert [h.waits_on for h in late] == [
        reconcile.UNLANDED_BRANCH_WAITS_ON.format(branch=FIX)]
    assert settle.call_args.kwargs["complete"] is True
    assert all(f"branch {RECORD}:" not in b for _, b in world.comments)
    assert any(f"branch {FIX}:" in b for _, b in world.comments)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
