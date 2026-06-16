"""TDD for the dependency-gate's unresolved-agent-blocker guard (DRE-1585).

PROBLEM: when the engineer agent hits a genuine, DETERMINISTIC blocker it posts
a `🛑 Agent blocked` comment and parks the card back in Backlog ON PURPOSE — a
Todo return would redispatch the next agent into the identical wall. But the
dependency gate (promote_ready) only looks at FORMAL blockers (blocks relations
+ "Blocked by:" lines). When those happen to be Done and the parent epic is
active, the next reconcile sweep re-promoted the card anyway. Real incident:
DRE-1572 looped Backlog→Todo→In Progress→Backlog FIVE times, burning five
engineer runs.

FIX UNDER TEST: reconcile.has_unresolved_blocker(card) + a guard in
promote_ready — before promoting a Backlog card, skip it if its latest decisive
comment is the engineer's `🛑 Agent blocked` marker with no human reply after
it. The gate's own "🧹 Auto-promoted" receipt is a machine marker, so it can
never clear the blocker and re-arm the loop; a human comment after the marker
does resolve it.

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

BLOCKER = (
    "🛑 Agent blocked: the upstream `/v2/widgets` endpoint does not exist — "
    "parked in Backlog until the blocker is resolved. Run: https://x"
)


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    """reconcile.REPO_SLUG is bound at import; pin it so promote_ready
    recognises this test's agent-bureau cards regardless of collection order
    (the same test-isolation hazard guarded in test_human_hold.py)."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")


def _candidate(comments):
    """A Backlog child with an active parent epic, no formal blockers, and the
    given comment bodies (oldest→newest) — eligible on formal grounds, so only
    the new blocker guard can hold it back."""
    return {
        "identifier": "DRE-1572",
        "description": "**Repo:** agent-bureau\nwork",
        "parent": {"identifier": "DRE-1268", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": [{"body": b} for b in comments]},
        "inverseRelations": {"nodes": []},
    }


# --------------------------------------------------------------------------
# has_unresolved_blocker — the detector
# --------------------------------------------------------------------------
def test_blocker_is_latest_comment_is_unresolved():
    card = _candidate(["🤖 PR opened: https://x", BLOCKER])
    assert reconcile.has_unresolved_blocker(card) is True


def test_human_reply_after_blocker_resolves_it():
    """A plain-English human comment after the marker clears it."""
    card = _candidate([BLOCKER, "Created the endpoint, you can proceed now."])
    assert reconcile.has_unresolved_blocker(card) is False


def test_gate_receipt_after_blocker_does_not_resolve():
    """The gate's own machine receipt must NOT count as a human resolution —
    otherwise the very act of (wrongly) promoting would clear the guard and
    re-arm the five-run loop."""
    card = _candidate(
        [BLOCKER, "🧹 Auto-promoted Backlog → Todo: parent epic active and all blockers Done."]
    )
    assert reconcile.has_unresolved_blocker(card) is True


def test_no_blocker_comment_is_not_blocked():
    card = _candidate(["🤖 PR opened: https://x"])
    assert reconcile.has_unresolved_blocker(card) is False


def test_missing_comments_key_is_not_blocked():
    """Hand-built fixtures without a comments key are treated as unblocked."""
    card = {"identifier": "DRE-1", "labels": {"nodes": []}}
    assert reconcile.has_unresolved_blocker(card) is False


# --------------------------------------------------------------------------
# promote_ready — the gate honours the guard
# --------------------------------------------------------------------------
def test_promote_ready_skips_card_with_unresolved_blocker():
    """The loop reproducer: formal blockers Done, epic active — but an
    unresolved 🛑 Agent blocked is the latest comment. Must NOT promote."""
    reconcile._write_failures.clear()
    card = _candidate(["🤖 PR opened: https://x", BLOCKER])
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_promote_ready_promotes_card_with_resolved_blocker():
    """Control A: a human resolved the blocker — the card IS promoted."""
    reconcile._write_failures.clear()
    card = _candidate([BLOCKER, "Fixed upstream, go ahead."])
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-1572", "Todo", "Backlog")


def test_promote_ready_promotes_card_without_blocker():
    """Control B: a card that never blocked is promoted as before."""
    reconcile._write_failures.clear()
    card = _candidate(["🤖 PR opened: https://x"])
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-1572", "Todo", "Backlog")
