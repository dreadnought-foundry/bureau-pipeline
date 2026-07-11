"""TDD for per-card isolation of unresolvable blocker references (DRE-2035).

PROBLEM: linear_ops.gql raised `SystemExit(f"linear error: …")` on ANY Linear
API error. card_state() calls it uncaught from promote_ready and
epic_blockers_unmet — and SystemExit subclasses BaseException, so the epic
gate's `except Exception` never caught it. One typo'd blocker reference (the
phrase "Blocked by" + a card id that doesn't exist) on ANY card the sweep
reads killed the ENTIRE sweep, every run — promotion, nudges, and the
dead-run machinery all down for the whole fleet until a human found the
offending card. Self-demonstrated 2026-07-11: the DRE-2035 card body itself
contained a literal example reference and killed every sweep until defanged.

FIX UNDER TEST:
  1. linear_ops raises LinearError (a RuntimeError) — never SystemExit — for
     API/reference errors; the CLI __main__ converts it to a nonzero exit
     explicitly at top level, so command-line behavior is unchanged.
  2. promote_ready isolates per-card failures: a card whose blocker reference
     can't resolve is SKIPPED with a loud ERROR line + ONE explanatory
     comment (keyed on reconcile.BAD_REF_TAG, like DEAD_TAG), and the rest
     of the sweep proceeds.
  3. epic_blockers_unmet fails SAFE (returns blocked) on a LinearError from
     a blocker-state read, instead of dying.
  4. A sweep-level summary line counts skipped-card errors.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

# The nonexistent card id, and the blocker line built HERE — never written
# out in card text, where the sweep's parser would match it (the first
# version of the DRE-2035 card body did exactly that and killed every sweep).
BAD_ID = "DRE-99999"
BAD_BLOCKER_LINE = "Blocked by: " + BAD_ID


@pytest.fixture(autouse=True)
def _pin_and_reset(monkeypatch):
    """Pin REPO_SLUG (bound at import) and reset the module-level skip
    collector so tests never see each other's skips."""
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._card_skips.clear()
    reconcile._write_failures.clear()


def _candidate(identifier: str, description_extra: str = "") -> dict:
    """A Backlog child eligible on every ground (active parent epic, no
    agent-blocker) — only blocker-reference resolution can hold it back."""
    return {
        "identifier": identifier,
        "description": "**Repo:** agent-bureau\nwork\n" + description_extra,
        "parent": {"identifier": "DRE-800", "state": {"name": "In Progress"}},
        "labels": {"nodes": [{"name": "size:M"}]},
        "comments": {"nodes": []},
        "inverseRelations": {"nodes": []},
    }


def _entity_not_found(identifier: str):
    raise linear_ops.LinearError(
        f"linear error: Entity not found: Issue - could not find {identifier}"
    )


# ---------------------------------------------------------------------------
# linear_ops: LinearError, never SystemExit
# ---------------------------------------------------------------------------
class _Resp:
    """Minimal urlopen context-manager stand-in returning a fixed payload."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_gql_api_error_raises_linear_error_not_system_exit():
    """An errors payload from Linear raises LinearError — a RuntimeError that
    ordinary `except Exception` handlers catch — never SystemExit."""
    payload = {"errors": [{"message": "Entity not found: Issue"}]}
    with patch.object(
        linear_ops.urllib.request, "urlopen", return_value=_Resp(payload)
    ):
        with pytest.raises(linear_ops.LinearError) as exc_info:
            linear_ops.gql("query { issue(id: $id) { id } }", {"id": BAD_ID})
    assert not isinstance(exc_info.value, SystemExit)
    assert isinstance(exc_info.value, RuntimeError)


def test_unknown_state_name_raises_linear_error():
    """The reference-error path (no such workflow state) is a LinearError too."""
    with patch.object(
        linear_ops, "gql", return_value={"workflowStates": {"nodes": []}}
    ):
        with pytest.raises(linear_ops.LinearError):
            linear_ops.state_id_and_type("team-1", "No Such State")


def test_module_never_raises_system_exit():
    """No `raise SystemExit` anywhere in linear_ops — the ONLY process exit
    is the explicit sys.exit in the CLI __main__ block."""
    source = (Path(linear_ops.__file__)).read_text()
    assert "raise SystemExit" not in source


# ---------------------------------------------------------------------------
# promote_ready: one bad reference skips ONE card, not the sweep
# ---------------------------------------------------------------------------
def test_bad_reference_does_not_stop_other_cards_from_promoting():
    """The headline acceptance: DRE-100 carries a blocker line naming a
    nonexistent card and is evaluated FIRST (lowest number); DRE-200 is clean.
    DRE-200 must still promote."""
    bad = _candidate("DRE-100", BAD_BLOCKER_LINE)
    good = _candidate("DRE-200")
    with patch.object(reconcile, "backlog_children", return_value=[bad, good]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_entity_not_found), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-200", "Todo", "Backlog")


def test_bad_card_gets_exactly_one_comment_across_sweeps():
    """Sweep 1 (no prior comment) posts the explanatory comment; sweep 2
    (comment already on the card) posts nothing — one comment, ever."""
    bad = _candidate("DRE-100", BAD_BLOCKER_LINE)

    def _sweep(prior_comments: int) -> list[str]:
        with patch.object(reconcile, "backlog_children", return_value=[bad]), \
            patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
            patch.object(reconcile, "card_state", side_effect=_entity_not_found), \
            patch.object(
                reconcile.linear_ops, "count_comments", return_value=prior_comments
            ), \
            patch.object(reconcile.linear_ops, "cmd_advance"), \
            patch.object(reconcile.linear_ops, "cmd_comment") as comment:
            reconcile.promote_ready(active_count=0)
        return [
            call.args[1]
            for call in comment.call_args_list
            if reconcile.BAD_REF_TAG in call.args[1]
        ]

    first = _sweep(prior_comments=0)
    assert len(first) == 1
    assert "doesn't resolve" in first[0]  # plain-English "fix the reference"
    second = _sweep(prior_comments=1)
    assert second == []


def test_skipped_card_is_never_promoted():
    """The bad card itself must be SKIPPED — never advanced on a reference
    the gate could not evaluate."""
    bad = _candidate("DRE-100", BAD_BLOCKER_LINE)
    with patch.object(reconcile, "backlog_children", return_value=[bad]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_entity_not_found), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 0
    advance.assert_not_called()


def test_sweep_summary_counts_skipped_cards(capsys):
    """A red pattern must be visible in the run log: an ERROR line per skip
    and a summary line counting them."""
    bad = _candidate("DRE-100", BAD_BLOCKER_LINE)
    with patch.object(reconcile, "backlog_children", return_value=[bad]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_entity_not_found), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance"), \
        patch.object(reconcile.linear_ops, "cmd_comment"):
        reconcile.promote_ready(active_count=0)
    captured = capsys.readouterr()
    assert "ERROR" in captured.err and "DRE-100" in captured.err
    assert "1 card(s) SKIPPED" in captured.out + captured.err


def test_comment_failure_does_not_kill_the_sweep():
    """Reporting must never block the sweep: even when posting the skip
    comment ITSELF fails, the clean sibling still promotes."""
    bad = _candidate("DRE-100", BAD_BLOCKER_LINE)
    good = _candidate("DRE-200")

    def _comment(identifier, body):
        if reconcile.BAD_REF_TAG in body:
            _entity_not_found(identifier)

    with patch.object(reconcile, "backlog_children", return_value=[bad, good]), \
        patch.object(reconcile, "epic_blockers_unmet", return_value=False), \
        patch.object(reconcile, "card_state", side_effect=_entity_not_found), \
        patch.object(reconcile.linear_ops, "count_comments", return_value=0), \
        patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
        patch.object(reconcile.linear_ops, "cmd_comment", side_effect=_comment):
        promoted = reconcile.promote_ready(active_count=0)
    assert promoted == 1
    advance.assert_called_once_with("DRE-200", "Todo", "Backlog")


# ---------------------------------------------------------------------------
# epic_blockers_unmet: a bad reference on the EPIC fails safe, not fatal
# ---------------------------------------------------------------------------
def test_epic_bad_blocker_reference_fails_safe_blocked():
    """An epic whose description-line blocker doesn't resolve reads as
    BLOCKED (children held, sweep alive) — never an escaping exception."""
    epic = {
        "identifier": "DRE-800",
        "description": "**Repo:** agent-bureau\n" + BAD_BLOCKER_LINE + "\nepic",
        "inverseRelations": {"nodes": []},
    }
    with patch.object(reconcile, "_fetch_epic_relations", return_value=epic), \
        patch.object(reconcile, "card_state", side_effect=_entity_not_found):
        assert reconcile.epic_blockers_unmet("DRE-800") is True
