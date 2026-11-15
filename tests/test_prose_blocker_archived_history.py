"""TDD for "a prose blocker naming a card Linear archived after it closed is
history, not a defect" (DRE-9319).

Every portico Reconcile run failed from 2026-11-09 03:48 AM PT on, four an
hour, each ending with

    ERROR: DRE-3919: a prose blocker declaring DRE-3777, DRE-3949 with no
    blockedBy relation has stood for 135.3h

Epic DRE-3919's body says `**Blocked by:** DRE-3777, DRE-3949`, and the board
held both relations. Both blockers had been Done since 2026-09-29. Linear's own
auto-archive archived them at about 1:46 AM PT on 2026-11-09, and an archived
card drops out of the `inverseRelations` page the sweep reads. The line lost
its corroboration with nobody touching the card, the epic gate called it a
defect, held the epic's last child (its PROOF), and the run went red two hours
later. Linear archives every closed card in time, so this reaches every epic
and card whose declared blocker shipped long enough ago.

What is TDD'd here: before the sweep calls a prose claim a defect, it asks
Linear about each claimed card. One Linear has ARCHIVED and that is CLOSED is
history — the dependency shipped, and the relation is only hidden — so it is
dropped from the defect. Anything else is a defect exactly as before.

  1. THE REPLAY — DRE-3919's shape: two archived Done claims, an empty page.
     The epic releases its children and no stale defect is recorded.
  2. THE CONTROLS — an archived claim still open, and an unarchived claim
     with no relation, are still defects.
  3. THE CARD GATE — a Queued card whose only claim is archived and Done is
     not sent to Triage, and the live re-check a door-sourced card gets
     releases it too, so the second read cannot hold what the first released.
  4. UNKNOWN — a claim that cannot be read holds the epic with no defect
     recorded, and leaves a card neither refused nor promoted.

Run: cd bureau-pipeline && python3 -m pytest tests/test_prose_blocker_archived_history.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")

import linear_ops  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

FLEET_VERDICT = routing_verdict.verdict_comment(
    "FLEET", "the acceptance criteria are unit-testable")

# DRE-3919's own line, link-wrapped the way Linear serializes a pasted issue.
DRE_3919_LINE = (
    "**Blocked by:** [DRE-3777](https://linear.app/dreadnoughtfoundry/issue/"
    "DRE-3777/epic-portico-source-list-rebuild-part-4a-the-app-shell-and-the-"
    "library), [DRE-3949](https://linear.app/dreadnoughtfoundry/issue/DRE-3949/"
    "epic-portico-source-list-rebuild-part-4a-2-the-librarys-sheets-and)"
)
ARCHIVED_AT = "2026-11-09T09:45:35.368Z"


# The real read, kept at import: `conftest._no_archived_claims` stubs it for
# every other suite, and this one is about it.
_ARCHIVED_HISTORY = reconcile.archived_history


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    monkeypatch.setattr(reconcile, "archived_history", _ARCHIVED_HISTORY)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "portico")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._stale_defects.clear()
    reconcile._card_skips.clear()
    linear_ops._card_memo.clear()


def _issue(identifier, state="Done", archived_at=ARCHIVED_AT) -> dict:
    """`linear_ops.get_issue`'s answer: Linear answers `issue(id:)` for an
    archived card too, with its `archivedAt`."""
    return {"id": f"uuid-{identifier}", "identifier": identifier, "title": identifier,
            "team": {"id": "team"}, "state": {"id": "s", "name": state, "type": "x"},
            "labels": {"nodes": []}, "children": {"nodes": []}, "project": None,
            "archivedAt": archived_at}


def _linear(issues: dict):
    def _get_issue(identifier, *, fresh=False):
        if identifier not in issues:
            raise AssertionError(f"the sweep read {identifier}, which no claim names")
        found = issues[identifier]
        if isinstance(found, Exception):
            raise found
        return found
    return _get_issue


def _stale() -> str:
    return (datetime.now(UTC) - timedelta(
        minutes=reconcile.PROSE_DEFECT_RED_MINUTES + 5)).isoformat().replace("+00:00", "Z")


def _epic(description=DRE_3919_LINE, relations=()) -> dict:
    """DRE-3919 as the epic gate read it: the declaring line, and a page with
    no `blocks` relation, because both blockers were archived off it."""
    return {
        "identifier": "DRE-3919",
        "description": "Rewritten 2026-11-03.\n\n" + description,
        "inverseRelations": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                             "nodes": list(relations)},
    }


def _gate(epic: dict, issues: dict):
    """One epic-gate evaluation with a defect notice already 2 h+ old on the
    card — the state every portico sweep since 2026-11-09 has found."""
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
        patch.object(reconcile.linear_ops, "get_issue", _linear(issues)), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=1), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=_stale()), \
        patch.object(reconcile.linear_ops, "cmd_comment") as comment:
        held = reconcile.epic_blockers_unmet("DRE-3919")
    return held, comment


# ---------------------------------------------------------------------------
# 1. The replay
# ---------------------------------------------------------------------------
def test_replay_dre_3919_archived_done_claims_do_not_hold_the_epic(capsys):
    held, comment = _gate(_epic(), {"DRE-3777": _issue("DRE-3777"),
                                    "DRE-3949": _issue("DRE-3949")})
    assert held is False
    assert reconcile._stale_defects == []
    comment.assert_not_called()
    out = capsys.readouterr().out
    assert "held by a PROSE DEFECT" not in out
    assert "DRE-3777" in out and "DRE-3949" in out and "archived" in out


def test_replay_the_sweep_exit_is_green_with_no_stale_defect():
    """The red run was the symptom: with nothing in the ledger the pass's exit
    names no defect."""
    _gate(_epic(), {"DRE-3777": _issue("DRE-3777"), "DRE-3949": _issue("DRE-3949")})
    assert not (reconcile._write_failures or reconcile._read_failures
                or reconcile._stale_defects)


def test_a_canceled_or_duplicate_archived_claim_is_history_too():
    held, _ = _gate(_epic("**Blocked by:** DRE-3777, DRE-3949"),
                    {"DRE-3777": _issue("DRE-3777", "Canceled"),
                     "DRE-3949": _issue("DRE-3949", "Duplicate")})
    assert held is False
    assert reconcile._stale_defects == []


def test_a_live_relation_still_holds_once_the_history_is_dropped():
    """Dropping the archived claim narrows the DEFECT, never the gate: a live
    `blocks` relation on the same epic still holds its children."""
    live = {"type": "blocks", "issue": {"identifier": "DRE-4000",
                                        "state": {"name": "Queued"}}}
    epic = _epic("**Blocked by:** DRE-3777, DRE-4000", [live])
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
        patch.object(reconcile.linear_ops, "get_issue",
                     _linear({"DRE-3777": _issue("DRE-3777")})), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=1), \
        patch.object(reconcile.linear_ops, "first_comment_at", return_value=_stale()), \
        patch.object(reconcile.linear_ops, "cmd_comment"), \
        patch.object(reconcile, "_proof_blocker", return_value=(None, None)), \
        patch.object(reconcile, "card_relation_blockers",
                     return_value=({"DRE-4000": None}, set())):
        assert reconcile.epic_blockers_unmet("DRE-3919") is True
    assert reconcile._stale_defects == []


# ---------------------------------------------------------------------------
# 2. The controls
# ---------------------------------------------------------------------------
def test_an_archived_claim_that_is_still_open_is_still_a_defect():
    held, _ = _gate(_epic("**Blocked by:** DRE-3777"),
                    {"DRE-3777": _issue("DRE-3777", "In Progress")})
    assert held is True
    assert any("DRE-3919" in e and "DRE-3777" in e for e in reconcile._stale_defects)


def test_an_unarchived_claim_with_no_relation_is_still_a_defect():
    held, _ = _gate(_epic("**Blocked by:** DRE-3777"),
                    {"DRE-3777": _issue("DRE-3777", "Done", archived_at=None)})
    assert held is True
    assert any("DRE-3777" in e for e in reconcile._stale_defects)


def test_only_the_archived_claims_leave_the_defect():
    held, _ = _gate(_epic("**Blocked by:** DRE-3777, DRE-3949"),
                    {"DRE-3777": _issue("DRE-3777"),
                     "DRE-3949": _issue("DRE-3949", "Done", archived_at=None)})
    assert held is True
    assert len(reconcile._stale_defects) == 1
    assert "DRE-3949" in reconcile._stale_defects[0]
    assert "DRE-3777" not in reconcile._stale_defects[0]


def test_a_claim_linear_has_no_card_for_is_still_a_defect():
    """A typo is not unknown: Linear answered, and there is no such card."""
    held, _ = _gate(_epic("**Blocked by:** DRE-3777"),
                    {"DRE-3777": linear_ops.LinearError(
                        "linear error: [{'message': 'Entity not found: Issue'}]")})
    assert held is True
    assert any("DRE-3777" in e for e in reconcile._stale_defects)


def test_a_corroborated_epic_costs_no_read():
    relation = {"type": "blocks", "issue": {"identifier": "DRE-3777",
                                            "state": {"name": "Done"}}}
    held, _ = _gate(_epic("**Blocked by:** DRE-3777", [relation]), {})
    assert held is False


# ---------------------------------------------------------------------------
# 3. The card gate
# ---------------------------------------------------------------------------
def _card(description: str) -> dict:
    return {
        "identifier": "DRE-9152",
        "description": "**Repo:** portico\n" + description,
        "parent": {"identifier": "DRE-3919", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": [{"body": FLEET_VERDICT}]},
        "inverseRelations": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                             "nodes": []},
    }


def _sweep(card: dict, issues: dict):
    moves, comments = [], []
    with patch.object(reconcile, "backlog_children",
                      lambda only=None, **_: [dict(card)]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile.linear_ops, "get_issue", _linear(issues)), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance",
                     lambda ident, to, _from: moves.append((ident, to))), \
        patch.object(reconcile.linear_ops, "cmd_comment",
                     lambda ident, body, *f: comments.append(body)):
        promoted = reconcile.promote_ready(active_count=0)
    return promoted, moves, comments


def test_a_card_whose_only_claim_is_archived_and_done_is_not_sent_to_triage():
    promoted, moves, comments = _sweep(_card("**Blocked by:** DRE-3777"),
                                       {"DRE-3777": _issue("DRE-3777")})
    assert ("DRE-9152", "Triage") not in moves
    assert not [c for c in comments if prose_blockers.CARD_TAG in c]
    assert promoted == 1


def test_a_card_whose_claim_is_not_archived_is_still_sent_to_triage():
    _, moves, comments = _sweep(_card("**Blocked by:** DRE-3777"),
                                {"DRE-3777": _issue("DRE-3777", archived_at=None)})
    assert moves == [("DRE-9152", "Triage")]
    assert [c for c in comments if prose_blockers.CARD_TAG in c]


def _live_sweep(card: dict, issues: dict):
    """`_sweep` for a card the read door sourced: the live re-check runs, and
    it reads the card back from Linear as it stands."""
    with patch.object(reconcile, "_door_sourced", {card["identifier"]}), \
        patch.object(reconcile, "_fetch_backlog_linear",
                     lambda idents, **_: [dict(card)]):
        return _sweep(card, issues)


def _live_refusal(card: dict, issues: dict):
    with patch.object(reconcile, "_fetch_backlog_linear",
                      lambda idents, **_: [dict(card)]), \
        patch.object(reconcile.linear_ops, "get_issue", _linear(issues)):
        _, why = reconcile.live_promotion_refusal(
            card, reconcile.card_comment_bodies(card))
    return why


def test_a_door_sourced_card_whose_only_claim_is_archived_and_done_is_promoted():
    """The live re-check is the card gate's second read: a card the first
    released as history is promoted, not left standing every sweep."""
    promoted, moves, comments = _live_sweep(_card("**Blocked by:** DRE-3777"),
                                            {"DRE-3777": _issue("DRE-3777")})
    assert ("DRE-9152", "Triage") not in moves
    assert not [c for c in comments if prose_blockers.CARD_TAG in c]
    assert promoted == 1


def test_the_live_recheck_passes_an_archived_done_claim():
    assert _live_refusal(_card("**Blocked by:** DRE-3777"),
                         {"DRE-3777": _issue("DRE-3777")}) is None


def test_the_live_recheck_still_refuses_an_unarchived_claim():
    why = _live_refusal(_card("**Blocked by:** DRE-3777"),
                        {"DRE-3777": _issue("DRE-3777", archived_at=None)})
    assert why is not None and "claims a blocker" in why


def test_the_live_recheck_still_refuses_an_archived_claim_still_open():
    why = _live_refusal(_card("**Blocked by:** DRE-3777"),
                        {"DRE-3777": _issue("DRE-3777", "In Progress")})
    assert why is not None and "claims a blocker" in why


def test_the_live_recheck_holds_on_an_unreadable_claim():
    why = _live_refusal(_card("**Blocked by:** DRE-3777"),
                        {"DRE-3777": linear_ops.LinearError("linear error: HTTP 502")})
    assert why is not None and "DRE-3777" in why


def test_both_card_gate_reads_ask_the_archived_history_rule():
    """One reading of "is this claim history" on both card-gate reads, in the
    shape of `test_the_card_gate_writes_no_second_definition_of_built_out`."""
    for caller in (reconcile.promote_ready, reconcile.live_promotion_refusal):
        assert "archived_history(" in inspect.getsource(caller), caller.__name__


# ---------------------------------------------------------------------------
# 4. Unknown is neither history nor a defect
# ---------------------------------------------------------------------------
def test_an_unreadable_claim_holds_the_epic_and_records_no_defect(capsys):
    held, comment = _gate(_epic("**Blocked by:** DRE-3777"),
                          {"DRE-3777": linear_ops.LinearError("linear error: HTTP 502")})
    assert held is True
    assert reconcile._stale_defects == []
    comment.assert_not_called()
    assert "DRE-3777" in capsys.readouterr().out


def test_an_unreadable_claim_leaves_the_card_neither_refused_nor_promoted():
    promoted, moves, comments = _sweep(
        _card("**Blocked by:** DRE-3777"),
        {"DRE-3777": linear_ops.LinearError("linear error: HTTP 502")})
    assert promoted == 0
    assert moves == []
    assert not [c for c in comments if prose_blockers.CARD_TAG in c]


def test_a_rate_limit_is_the_runs_own_exit_never_one_claims():
    with pytest.raises(linear_ops.LinearRateLimited):
        _gate(_epic("**Blocked by:** DRE-3777"),
              {"DRE-3777": linear_ops.LinearRateLimited("rate limited")})


# ---------------------------------------------------------------------------
# The read: one listed site, and it selects the field the rule reads
# ---------------------------------------------------------------------------
def test_get_issue_selects_archived_at():
    sent = []

    def _gql(query, variables=None, **_):
        sent.append(query)
        return {"issue": _issue("DRE-3777")}

    with patch.object(linear_ops, "gql", _gql):
        linear_ops.get_issue("DRE-3777", fresh=True)
    assert "archivedAt" in sent[0]
