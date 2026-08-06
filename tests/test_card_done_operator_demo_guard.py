"""Merged PR must NOT auto-Done operator (`no-code`) or `DEMO:` cards.

THE BUG (six false card-closes in portico, 2026-07/08, one mechanism): the
merge→Done seam closes the card whose own agent/DRE-<n>- branch merged — right
for ordinary code cards, wrong for two classes where the MERGE is not the WORK:

  * `no-code` operator cards — their substance is live AWS work. An agent
    merged the RUNBOOK and linear-sync closed the card as if the AWS work
    happened: DRE-2242 closed TWICE while zero CloudFront key groups existed
    in any account; DRE-2241 closed with its security exposure still open;
    DRE-2218 (Operator Milestone 2) closed before any migration ran.
  * `DEMO:`-titled cards — they close only when every end-state claim in
    docs/demos/phase-N.md is a PASS. No merge event reads markdown verdicts:
    DRE-2253 and DRE-2252 closed while their reports said "NOT demonstrated"
    in those words.

Knock-on: epic DRE-2169 falsely closed via --close-epics once all children
(falsely) read Done. Guarding the CHILD transitions fixes the cascade — the
epic logic itself is deliberately untouched.

FIX UNDER TEST — one pure predicate, linear_ops.auto_done_skip_reason(),
consulted by BOTH auto-Done paths:
  (a) linear-sync.yml's card-done job, which now calls the `card-done`
      subcommand (guard inside) instead of a bare `state Done`;
  (b) reconcile's stale-sweep merged-PR backstop, which would otherwise
      re-close the very card linear-sync deliberately left open one cron
      sweep later.
A skipped card still gets the merge commented (marker-deduped between the two
paths) so the PR is visible on the card without the state lying.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/portico")
os.environ.setdefault("REPO_SLUG", "portico")
os.environ.setdefault("GH_TOKEN", "x")

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

LINEAR_SYNC = ROOT / ".github" / "workflows" / "linear-sync.yml"


# --------------------------------------------------------------------------
# the pure predicate: which cards a merge may close
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("title", "labels", "why"),
    [
        # The DRE-2242/2241/2218 class: operator cards, however titled.
        ("Operator: create CloudFront key groups", ["no-code", "agent:ops"],
         "no-code label"),
        ("Rotate the signing key", ["No-Code"], "label match is case-insensitive"),
        # The DRE-2253/2252 class: DEMO-titled cards, however labelled.
        ("DEMO: Phase 3 — folder access end-to-end", ["repo:portico"],
         "DEMO: title"),
        ("demo: phase 1", [], "DEMO: is case-insensitive"),
        ("  DEMO: Phase 2", [], "leading whitespace allowed"),
    ],
)
def test_skipped_card_shapes(title, labels, why):
    assert linear_ops.auto_done_skip_reason(title, labels) is not None, why


@pytest.mark.parametrize(
    ("title", "labels", "why"),
    [
        # Ordinary code cards keep auto-closing — the whole pipeline rides this.
        ("Add folder ACL enforcement", ["repo:portico", "agent:engineer"],
         "ordinary code card"),
        ("Update demo docs", [], "MENTIONING demos is not a DEMO: card"),
        ("Record the demo: phase 3", [], "DEMO: must START the title, not appear mid-title"),
        ("Phase 3 demo runner", ["agent:engineer"], "no colon, no DEMO: prefix"),
        ("Fix codegen", ["no-codegen"], "label must be exactly 'no-code', not a prefix"),
        ("", [], "empty title/labels"),
    ],
)
def test_ordinary_card_shapes_still_close(title, labels, why):
    assert linear_ops.auto_done_skip_reason(title, labels) is None, why


# --------------------------------------------------------------------------
# path (a): linear-sync's `card-done` subcommand
# --------------------------------------------------------------------------
def _issue(title, labels):
    return {
        "id": "card-uuid",
        "identifier": "DRE-2242",
        "title": title,
        "team": {"id": "team-1"},
        "state": {"name": "In Review", "type": "started"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_card_done(title, labels):
    """cmd_card_done against a faked card; returns (stdout, cmd_state mock,
    cmd_comment mock)."""
    buf = io.StringIO()
    with patch.object(
        linear_ops, "get_issue", return_value=_issue(title, labels)
    ), patch.object(linear_ops, "cmd_state") as state, patch.object(
        linear_ops, "cmd_comment"
    ) as comment:
        with redirect_stdout(buf):
            linear_ops.cmd_card_done("DRE-2242", "https://github.com/o/portico/pull/40")
    return buf.getvalue(), state, comment


def test_no_code_card_is_not_transitioned():
    """The DRE-2242 shape: merged runbook, operator card — state untouched.

    MUTATION CHECK: delete the auto_done_skip_reason() call (or its no-code
    arm) in cmd_card_done and `state` records ('DRE-2242', 'Done') — red here.
    """
    out, state, comment = _run_card_done(
        "Operator: create CloudFront key groups", ["no-code", "agent:ops"]
    )
    state.assert_not_called()
    # ...but the merge is still visible on the card, and honest about why.
    body = comment.call_args.args[1]
    assert "https://github.com/o/portico/pull/40" in body
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in body
    assert "NOT auto-closed" in body
    # ...and the job log says so loudly.
    assert "AUTO-DONE SKIPPED" in out


def test_demo_card_is_not_transitioned():
    """The DRE-2253/2252 shape: DEMO: card closes on evidence, not on merge."""
    out, state, comment = _run_card_done(
        "DEMO: Phase 3 — folder access end-to-end", ["repo:portico"]
    )
    state.assert_not_called()
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in comment.call_args.args[1]
    assert "AUTO-DONE SKIPPED" in out


def test_ordinary_card_still_goes_done():
    """The everyday path is byte-for-byte the old behavior: → Done, ✅ comment."""
    _, state, comment = _run_card_done(
        "Add folder ACL enforcement", ["repo:portico", "agent:engineer"]
    )
    state.assert_called_once_with("DRE-2242", "Done")
    comment.assert_called_once_with(
        "DRE-2242", "✅ Merged: https://github.com/o/portico/pull/40"
    )


def test_workflow_calls_card_done_not_bare_state_done():
    """Wiring: linear-sync.yml must route through `card-done` (guard inside).

    A bare `state "$CARD" "Done"` reappearing in the workflow bypasses the
    guard entirely — that exact line is what closed all six cards.
    """
    text = LINEAR_SYNC.read_text()
    assert re.search(r'card-done\s+"\$CARD"\s+"\$PR_URL"', text), (
        "linear-sync.yml no longer calls the guarded card-done subcommand"
    )
    assert not re.search(r'state\s+"\$CARD"\s+"Done"', text), (
        "linear-sync.yml transitions the card directly, bypassing the "
        "no-code/DEMO guard"
    )


# --------------------------------------------------------------------------
# path (b): reconcile's merged-PR backstop must not re-close what (a) skipped
# --------------------------------------------------------------------------
def _sweep_card(title, labels, identifier="DRE-2242"):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": title,
        "description": "runbook work",
        "updatedAt": "2026-07-01T00:00:00Z",  # long stale; age_minutes real
        "state": {"name": "In Review"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_merged_sweep(card, marker_already_posted=False):
    """Full-sweep main() with the card's PR already MERGED; returns the
    linear_ops cmd_state / cmd_comment mocks."""
    reconcile._write_failures.clear()
    merged_pr = {
        "number": 40,
        "headRefName": f"agent/{card['identifier']}-runbook",
        "state": "MERGED",
        "comments": [],
        "headRefOid": "a" * 40,
    }
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "promote_ready": MagicMock(return_value=0),
        "active_cards": MagicMock(return_value=[card]),
        "pr_for": MagicMock(return_value=merged_pr),
    }
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile, "REPO_SLUG", "portico"
    ), patch.object(reconcile.linear_ops, "cmd_state") as state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops,
        "count_comments",
        return_value=1 if marker_already_posted else 0,
    ):
        reconcile.main()
    return state, comment


def test_sweep_does_not_reclose_a_no_code_card():
    """Without this, the guard in (a) only DELAYS the false close: the card
    goes stale in In Review, the cron sweep sees its merged PR, and the old
    unconditional `cmd_state(ident, "Done")` closes it anyway.

    MUTATION CHECK: drop the skip branch in reconcile's merged backstop and
    `state` records ('DRE-2242', 'Done') — red here.
    """
    card = _sweep_card(
        "Operator: create CloudFront key groups",
        ["repo:portico", "no-code", "agent:ops"],
    )
    state, comment = _run_merged_sweep(card)
    state.assert_not_called()
    # linear-sync usually commented already; here it hadn't → post once.
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in comment.call_args.args[1]


def test_sweep_does_not_reclose_a_demo_card():
    card = _sweep_card("DEMO: Phase 3 — folder access", ["repo:portico"])
    state, _ = _run_merged_sweep(card)
    state.assert_not_called()


def test_sweep_posts_the_merge_note_only_once():
    """The card stays stale-with-merged-PR for as long as the operator takes;
    the marker comment (card-done's or a prior sweep's) must dedupe — one
    note ever, not one per sweep."""
    card = _sweep_card("Operator: rotate keys", ["repo:portico", "no-code"])
    _, comment = _run_merged_sweep(card, marker_already_posted=True)
    comment.assert_not_called()


def test_sweep_still_closes_an_ordinary_merged_card():
    """The backstop's day job survives: an ordinary code card whose PR merged
    while linear-sync was down still gets swept to Done."""
    card = _sweep_card(
        "Add folder ACL enforcement", ["repo:portico", "agent:engineer"]
    )
    state, comment = _run_merged_sweep(card)
    state.assert_called_once_with("DRE-2242", "Done")
    assert "already merged" in comment.call_args.args[1]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
