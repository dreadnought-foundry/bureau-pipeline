"""Event-driven promotion resolves a card's repo by LABEL, not the stamp (DRE-1879).

When linear-sync closes a card on a merge it immediately runs reconcile's
`--promote-only` gate so a now-unblocked sibling jumps to Todo right then, rather
than waiting on the ~hourly cron sweep. That promotion only acts on THIS repo's
cards, and it decided "this repo" with `card_repo_slug`, which reads ONLY the
deprecated `**Repo:** <slug>` description stamp.

Cards created the modern way carry a `repo:<slug>` LABEL and NO stamp
(DRE-1699/DRE-1697 — the label is the canonical repo signal). So a label-only
dependent returned None from the resolver, never matched REPO_SLUG, and was
silently skipped: its blocker merged → went Done, but it never promoted. That is
exactly what stranded DeltaSolv's DRE-1811 (label `repo:deltasolv`, no stamp)
after DRE-1803 merged — the chain only moved when the operator promoted by hand.

These tests pin the LABEL-first `card_repo` resolver and prove the gate now
promotes a label-only dependent on a merge.

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
os.environ.setdefault("REPO", "DeltaSolv/deltasolv")
os.environ.setdefault("REPO_SLUG", "deltasolv")

import reconcile  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_repo_slug(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "deltasolv")


# --------------------------------------------------------------------------
# card_repo — the label-first resolver
# --------------------------------------------------------------------------
def test_card_repo_reads_the_label_with_no_stamp():
    """The modern card: a repo:<slug> label, no **Repo:** stamp."""
    card = {
        "description": "Wire the GraphQL client.\n\n**Blocked by:** DRE-1803",
        "labels": {"nodes": [{"name": "agent:frontend"}, {"name": "repo:deltasolv"}]},
    }
    assert reconcile.card_repo(card) == "deltasolv"


def test_card_repo_label_strips_owner_prefix():
    card = {"description": "", "labels": {"nodes": [{"name": "repo:DeltaSolv/deltasolv"}]}}
    assert reconcile.card_repo(card) == "deltasolv"


def test_card_repo_falls_back_to_legacy_stamp():
    """An old card with the stamp and no label still resolves (back-compat)."""
    card = {"description": "**Repo:** agent-bureau\nwork", "labels": {"nodes": []}}
    assert reconcile.card_repo(card) == "agent-bureau"


def test_card_repo_label_wins_over_stamp():
    card = {
        "description": "**Repo:** agent-bureau\nwork",
        "labels": {"nodes": [{"name": "repo:deltasolv"}]},
    }
    assert reconcile.card_repo(card) == "deltasolv"


def test_card_repo_none_when_neither_present():
    assert reconcile.card_repo({"description": "work", "labels": {"nodes": []}}) is None


# --------------------------------------------------------------------------
# promote_ready — a LABEL-only dependent is promoted on merge (the DRE-1811 case)
# --------------------------------------------------------------------------
def _label_only_dependent():
    """The DRE-1811 shape: Backlog, active parent epic, repo by LABEL only (no
    **Repo:** stamp), and its sole blocker (DRE-1803) already Done."""
    return {
        "identifier": "DRE-1811",
        "description": "Offline-queue scaffold.\n\n**Blocked by:** DRE-1803",
        "parent": {"identifier": "DRE-1781", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "agent:frontend"}, {"name": "repo:deltasolv"}]},
        "comments": {"nodes": []},
        # DRE-1803 is Done, so it is NOT a live blocker (blockers_of filters Done).
        "inverseRelations": {"nodes": []},
    }


def test_label_only_dependent_is_promoted_on_merge():
    """The fix: a label-only card whose blocker is Done IS promoted to Todo.

    MUTATION CHECK: swap card_repo back to the stamp-only card_repo_slug and this
    card resolves to None != 'deltasolv' → skipped → promoted == 0 → fails. So the
    label-first resolver is load-bearing here.
    """
    reconcile._write_failures.clear()
    card = _label_only_dependent()
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile, "card_state", return_value="Done"), patch.object(
        reconcile.linear_ops, "cmd_advance"
    ) as advance, patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-1811", "Todo", "Backlog")


def test_other_repos_dependent_is_not_promoted_here():
    """Scope guard: a label-only card for a DIFFERENT repo is left alone (this
    merge event belongs to deltasolv, not atlas)."""
    reconcile._write_failures.clear()
    card = _label_only_dependent()
    card["labels"]["nodes"] = [{"name": "repo:atlas"}]
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_promotion_respects_max_wip():
    """DRE-1879 keeps the WIP cap: at cap, the merge event promotes nothing."""
    reconcile._write_failures.clear()
    card = _label_only_dependent()
    with patch.object(reconcile, "backlog_children", return_value=[card]), patch.object(
        reconcile, "epic_blockers_unmet", return_value=False
    ), patch.object(reconcile.linear_ops, "cmd_advance") as advance, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ):
        promoted = reconcile.promote_ready(active_count=reconcile.MAX_WIP)
    assert promoted == 0
    advance.assert_not_called()
