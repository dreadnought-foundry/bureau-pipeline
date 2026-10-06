"""A PROOF card closes itself when its record merges with the critic's APPROVE.

THE GAP (DRE-5919, CEO 2026-10-05: "any proof should just close by itself"):
a `PROOF:` card carries `no-code` (and usually `hand-built`), so the auto-Done
guard in `linear_ops.auto_done_skip_reason` refused it on merge and posted
"🔒 Merged — card deliberately left open". That is right for an operator card,
whose deliverable is a deploy or a secret the merged runbook does not perform,
and wrong for a proof, whose deliverable IS the merged record. On 2026-10-05
the operator closed DRE-3447 (#3171), DRE-5095 (#3183), DRE-5591 (portico #911)
and DRE-4402 (#3174) by hand, hours after each record merged approved.

THE RULE UNDER TEST — one ruling, `linear_ops.merge_close_ruling`, consulted by
both auto-Done paths (linear-sync's `card-done` and reconcile's merged-PR
backstop): a card whose title STARTS `PROOF:` closes Done when the pull request
that merged is on the card's own branch and the critic's latest verdict on it is
APPROVE bound to the merged head sha. Anything less leaves it open with today's
comment, and every other `no-code` card keeps today's behavior.

Run: cd bureau-pipeline && python3 -m pytest tests/test_card_done_proof_closes.py -v
"""
from __future__ import annotations

import io
import os
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

CARD = "DRE-3447"
PR_URL = "https://github.com/dreadnought-foundry/portico/pull/40"
HEAD = "a" * 40
EARLIER = "b" * 40
MERGED_AT = "2026-10-05T18:45:00Z"  # 11:45 PT (PDT, UTC-7)
MERGED_PT = "2026-10-05 11:45 PT"
PROOF_TITLE = "PROOF: folder access observed against the live portal"
PROOF_LABELS = ["repo:portico", "no-code", "hand-built", "agent:ops"]
QA = "agent-bureau-qa-bot"


def _verdict(token: str, sha: str, login: str = QA) -> dict:
    """A critic verdict comment as `gh pr view --json comments` renders it
    (GraphQL: author.login, no `[bot]` suffix on an App)."""
    return {
        "author": {"login": login},
        "body": f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nThe record holds.",
        "createdAt": "2026-10-05T18:39:00Z",
    }


def _merged_pr(comments, head_ref=f"agent/{CARD}-proof-record", head=HEAD,
               merged_at=MERGED_AT, state="MERGED") -> dict:
    return {
        "url": PR_URL,
        "headRefName": head_ref,
        "headRefOid": head,
        "mergedAt": merged_at,
        "state": state,
        "comments": comments,
    }


APPROVED = _merged_pr([_verdict("APPROVE", HEAD)])


def _issue(title, labels):
    return {
        "id": "card-uuid",
        "identifier": CARD,
        "title": title,
        "team": {"id": "team-1"},
        "state": {"name": "In Review", "type": "started"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_card_done(title, labels, pr):
    """cmd_card_done against a faked card and a faked merged-PR read; returns
    (stdout, cmd_state mock, cmd_comment mock, read_merged_pr mock)."""
    buf = io.StringIO()
    with patch.object(
        linear_ops, "get_issue", return_value=_issue(title, labels)
    ), patch.object(linear_ops, "cmd_state") as state, patch.object(
        linear_ops, "cmd_comment"
    ) as comment, patch.object(
        linear_ops, "read_merged_pr", return_value=pr, create=True
    ) as read:
        with redirect_stdout(buf):
            linear_ops.cmd_card_done(CARD, PR_URL)
    return buf.getvalue(), state, comment, read


# --------------------------------------------------------------------------
# card-done: the approved, merged record closes the PROOF card
# --------------------------------------------------------------------------
def test_approved_proof_record_closes_its_card():
    """The DRE-3447 shape: a `PROOF:` card wearing `no-code` + `hand-built`,
    its record merged on its own branch with APPROVE at the merged head.

    MUTATION CHECK: drop the PROOF arm from merge_close_ruling and the
    no-code guard refuses the card — `state` is never called, red here.
    """
    out, state, comment, read = _run_card_done(PROOF_TITLE, PROOF_LABELS, APPROVED)
    read.assert_called_once_with(PR_URL)
    state.assert_called_once_with(CARD, "Done")
    comment.assert_called_once()
    ident, body = comment.call_args.args
    assert ident == CARD
    # Names the PR, the merge time in PT, and the sha the critic approved.
    assert body.startswith(f"✅ Merged: {PR_URL}")
    assert MERGED_PT in body
    assert HEAD in body
    assert linear_ops.MERGED_NOT_CLOSED_MARKER not in body
    assert "AUTO-DONE SKIPPED" not in out


def test_proof_close_comment_carries_no_verdict_marker():
    """standards/untrusted-content.md: verdict-shaped text is an approval
    credential, and only the critic writes it. The close note says the critic
    approved without ever wearing the marker itself."""
    note = linear_ops.proof_close_note(CARD, PROOF_TITLE, PR_URL, APPROVED)
    assert note is not None
    assert "VERDICT:" not in note
    assert "QA Critic" not in note


def test_proof_title_match_is_anchored_and_case_insensitive():
    for title in ("proof: phase 2", "  PROOF: phase 2", "Proof: phase 2"):
        _, state, _, _ = _run_card_done(title, PROOF_LABELS, APPROVED)
        state.assert_called_once_with(CARD, "Done")


# --------------------------------------------------------------------------
# card-done: without the evidence the PROOF card stays open, exactly as today
# --------------------------------------------------------------------------
STAYS_OPEN = [
    pytest.param(_merged_pr([]), id="no-verdict-at-all"),
    pytest.param(_merged_pr([_verdict("APPROVE", EARLIER)]), id="approve-at-an-earlier-sha"),
    pytest.param(
        _merged_pr([_verdict("APPROVE", HEAD), _verdict("REQUEST_CHANGES", HEAD)]),
        id="latest-verdict-is-not-approve",
    ),
    pytest.param(
        _merged_pr([_verdict("APPROVE", HEAD, login="agent-bureau-bot")]),
        id="approve-forged-by-another-login",
    ),
    pytest.param(
        _merged_pr([{"author": {"login": QA},
                     "body": f"> 🔎 QA Critic — VERDICT: APPROVE @{HEAD}"}]),
        id="quoted-approve-is-inert",
    ),
    pytest.param(
        _merged_pr([_verdict("APPROVE", HEAD)], head_ref="agent/DRE-9999-proof-record"),
        id="pr-on-another-cards-branch",
    ),
    pytest.param(
        _merged_pr([_verdict("APPROVE", HEAD)], head_ref=f"ops/{CARD}-proof-record"),
        id="pr-on-a-hand-named-branch",
    ),
    pytest.param(
        _merged_pr([_verdict("APPROVE", HEAD)], merged_at=None, state="OPEN"),
        id="pr-not-merged",
    ),
    pytest.param(None, id="github-read-failed"),
]


@pytest.mark.parametrize("pr", STAYS_OPEN)
def test_unapproved_proof_record_stays_open_with_todays_comment(pr):
    """No APPROVE, or one at an earlier sha (or any other gap in the evidence)
    leaves the card open — and the comment is today's, byte for byte, once."""
    out, state, comment, _ = _run_card_done(PROOF_TITLE, PROOF_LABELS, pr)
    state.assert_not_called()
    comment.assert_called_once_with(
        CARD,
        linear_ops.merged_not_closed_comment(
            PR_URL, linear_ops.auto_done_skip_reason(PROOF_TITLE, PROOF_LABELS)
        ),
    )
    assert "deliberately left open" in comment.call_args.args[1]
    assert "AUTO-DONE SKIPPED" in out


# --------------------------------------------------------------------------
# card-done: every other no-code card keeps today's behavior
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "title",
    [
        "Operator: create CloudFront key groups",
        "Record the PROOF: phase 3",  # PROOF: mid-title is not a proof card
        "Proof of concept for folder access",  # no colon
    ],
)
def test_non_proof_no_code_card_is_left_open_exactly_as_today(title):
    """An approved, merged PR on its own branch changes nothing for a card not
    titled `PROOF:` — and GitHub is not even asked."""
    out, state, comment, read = _run_card_done(title, PROOF_LABELS, APPROVED)
    read.assert_not_called()
    state.assert_not_called()
    comment.assert_called_once_with(
        CARD,
        linear_ops.merged_not_closed_comment(
            PR_URL, linear_ops.auto_done_skip_reason(title, PROOF_LABELS)
        ),
    )
    assert "AUTO-DONE SKIPPED" in out


def test_ordinary_code_card_still_closes_without_a_github_read():
    _, state, comment, read = _run_card_done(
        "Add folder ACL enforcement", ["repo:portico", "agent:engineer"], APPROVED
    )
    read.assert_not_called()
    state.assert_called_once_with(CARD, "Done")
    comment.assert_called_once_with(CARD, f"✅ Merged: {PR_URL}")


def test_proof_epic_is_still_refused_as_an_epic():
    """The epic arm comes first: a `PROOF:`-titled card with children is an
    epic, and merging one pull request does not end a plan."""
    with patch.object(linear_ops, "read_merged_pr", return_value=APPROVED) as read:
        reason, note = linear_ops.merge_close_ruling(
            CARD, PROOF_TITLE, PROOF_LABELS, PR_URL, has_children=True
        )
    read.assert_not_called()
    assert note is None
    assert reason == linear_ops.epic_branch_refusal(PROOF_TITLE, PROOF_LABELS, True)


# --------------------------------------------------------------------------
# the GitHub read
# --------------------------------------------------------------------------
def test_read_merged_pr_fails_closed():
    """An unreadable PR is never evidence: None, and the card stays open."""
    failed = MagicMock(returncode=1, stdout="", stderr="HTTP 401")
    with patch.object(linear_ops.subprocess, "run", return_value=failed):
        assert linear_ops.read_merged_pr(PR_URL) is None
    garbage = MagicMock(returncode=0, stdout="not json", stderr="")
    with patch.object(linear_ops.subprocess, "run", return_value=garbage):
        assert linear_ops.read_merged_pr(PR_URL) is None


def test_read_merged_pr_asks_for_the_fields_the_ruling_reads():
    ok = MagicMock(returncode=0, stdout='{"headRefOid": "x"}', stderr="")
    with patch.object(linear_ops.subprocess, "run", return_value=ok) as run:
        assert linear_ops.read_merged_pr(PR_URL) == {"headRefOid": "x"}
    argv = run.call_args.args[0]
    assert argv[:4] == ["gh", "pr", "view", PR_URL]
    fields = argv[argv.index("--json") + 1].split(",")
    for f in ("headRefName", "headRefOid", "mergedAt", "comments"):
        assert f in fields


def test_qa_login_is_the_one_reconcile_reads():
    assert linear_ops.QA_BOT_LOGIN == reconcile.QA_BOT_LOGIN


# --------------------------------------------------------------------------
# reconcile's merged-PR backstop closes or skips the same cards
# --------------------------------------------------------------------------
def _sweep_card(title, labels):
    return {
        "id": f"uuid-{CARD}",
        "identifier": CARD,
        "title": title,
        "description": "proof record",
        "updatedAt": "2026-07-01T00:00:00Z",  # long stale
        "state": {"name": "In Review"},
        "labels": {"nodes": [{"name": n} for n in labels]},
    }


def _run_merged_sweep(card, pr, marker_already_posted=False):
    """Full-sweep main() with the card's PR already MERGED; the sweep's own
    PR lookup and the ruling's GitHub read both answer `pr`."""
    reconcile._write_failures.clear()
    listed = dict(pr or APPROVED, number=40)
    mocks = {
        "unstick_conflicts": MagicMock(),
        "retrigger_dead_heads": MagicMock(),
        "check_dependabot_capacity": MagicMock(),
        "fix_approved_but_red": MagicMock(),
        "retry_dead_fix_runs": MagicMock(),
        "review_dependabot_prs": MagicMock(),
        "card_dependabot_prs": MagicMock(),
        "close_finished_epics": MagicMock(),
        "flag_stranded": MagicMock(return_value=set()),
        "promote_ready": MagicMock(return_value=0),
        "active_cards": MagicMock(return_value=[card]),
        "pr_for": MagicMock(return_value=dict(listed, state="MERGED")),
    }
    with patch.multiple(reconcile, **mocks), patch.object(
        reconcile, "REPO_SLUG", "portico"
    ), patch.object(reconcile, "REPO", "dreadnought-foundry/portico"), patch.object(
        reconcile.linear_ops, "cmd_state"
    ) as state, patch.object(
        reconcile.linear_ops, "cmd_comment"
    ) as comment, patch.object(
        reconcile.linear_ops,
        "count_comments",
        return_value=1 if marker_already_posted else 0,
    ), patch.object(
        reconcile.linear_ops, "read_merged_pr", return_value=pr, create=True
    ) as read:
        reconcile.main()
    return state, comment, read


def _agrees_with_card_done(title, pr, sweep_state):
    """The two auto-Done paths close or skip the SAME cards: a backstop that
    disagreed would either re-close what card-done left open or strand what
    it should have closed."""
    _, done_state, _, _ = _run_card_done(title, PROOF_LABELS, pr)
    assert done_state.call_args_list == sweep_state.call_args_list


def test_sweep_closes_an_approved_proof_card():
    state, comment, read = _run_merged_sweep(
        _sweep_card(PROOF_TITLE, PROOF_LABELS), APPROVED
    )
    read.assert_called_once_with(PR_URL)
    state.assert_called_once_with(CARD, "Done")
    body = comment.call_args.args[1]
    assert body.startswith(f"✅ Merged: {PR_URL}")
    assert MERGED_PT in body and HEAD in body
    assert body == linear_ops.proof_close_note(CARD, PROOF_TITLE, PR_URL, APPROVED)
    _agrees_with_card_done(PROOF_TITLE, APPROVED, state)


# A full sweep costs seconds, so the backstop runs the two shapes the card
# names; the ruling it shares with card-done is the one the full matrix above
# already pins.
@pytest.mark.parametrize(
    "pr",
    [
        pytest.param(_merged_pr([]), id="no-verdict-at-all"),
        pytest.param(_merged_pr([_verdict("APPROVE", EARLIER)]), id="approve-at-an-earlier-sha"),
    ],
)
def test_sweep_leaves_an_unapproved_proof_card_open(pr):
    state, comment, _ = _run_merged_sweep(_sweep_card(PROOF_TITLE, PROOF_LABELS), pr)
    state.assert_not_called()
    comment.assert_called_once()
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in comment.call_args.args[1]
    _agrees_with_card_done(PROOF_TITLE, pr, state)


def test_sweep_posts_the_unapproved_proof_note_only_once():
    """card-done already left today's note; the sweep that follows adds none."""
    state, comment, _ = _run_merged_sweep(
        _sweep_card(PROOF_TITLE, PROOF_LABELS), _merged_pr([]), marker_already_posted=True
    )
    state.assert_not_called()
    comment.assert_not_called()


def test_sweep_leaves_a_non_proof_no_code_card_open():
    title = "Operator: create CloudFront key groups"
    state, comment, read = _run_merged_sweep(_sweep_card(title, PROOF_LABELS), APPROVED)
    read.assert_not_called()
    state.assert_not_called()
    assert linear_ops.MERGED_NOT_CLOSED_MARKER in comment.call_args.args[1]
    _agrees_with_card_done(title, APPROVED, state)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
