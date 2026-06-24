"""Tests for the APPROVE-flip formal-review reconciliation (DRE-1874).

THE BUG (live evidence on PRs #1830/#1827, 2026-06-24):
  The QA critic runs claude-code-action with the bureau-bot token. A
  REQUEST_CHANGES verdict makes the action submit a FORMAL "Changes requested"
  review. When the critic later flips to APPROVE, qa-review only posts a
  comment — the formal review is never updated, so GitHub keeps
  reviewDecision=CHANGES_REQUESTED and the PR stays BLOCKED (even `--admin`
  fails) until a human dismisses it. That strands approved PRs and stalls
  auto-merge.

DESIRED behavior (these tests express it; they exercise the real code path):
  On an APPROVE verdict, sync_on_approve must
    1. DISMISS the still-active CHANGES_REQUESTED review(s), and
    2. submit a fresh APPROVE review,
  so reviewDecision becomes APPROVED. It must be idempotent (a second run, with
  no outstanding changes-requested review, dismisses nothing) and never crash
  when the API is unavailable.

Run: cd bureau-pipeline && python3 -m pytest tests/test_sync_review_state.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_review_state  # noqa: E402


def _review(rid, login, state):
    return {"id": rid, "user": {"login": login}, "state": state}


def _fake_run_factory(reviews, calls, fail_writes=False):
    """subprocess.run stub: list-reviews returns `reviews`; writes are recorded.

    - `gh api repos/.../reviews ...`              -> the reviews JSON
    - `gh api -X PUT .../reviews/{id}/dismissals` -> recorded as a dismissal
    - `gh api -X POST .../reviews`                -> recorded as an approval
    `fail_writes=True` makes every write return rc=1 (simulate a transient API
    failure) to prove best-effort non-fatal behavior.
    """

    def _w():
        return SimpleNamespace(
            returncode=1 if fail_writes else 0,
            stdout="" if fail_writes else "{}",
            stderr="boom" if fail_writes else "",
        )

    def fake_run(argv, **kwargs):
        joined = " ".join(argv)
        # A dismissal write: PUT .../reviews/{id}/dismissals
        if "/dismissals" in joined:
            calls.append({"kind": "dismiss", "argv": argv})
            return _w()
        # The fresh APPROVE review write: POST .../reviews
        if "-X" in argv and "POST" in argv:
            calls.append({"kind": "approve", "argv": argv})
            return _w()
        # The read: list reviews (GET, no -X) — must come after the writes so a
        # POST .../reviews isn't mistaken for the GET.
        if argv[1] == "api" and "/reviews" in joined and "-X" not in argv:
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(reviews), stderr=""
            )
        raise AssertionError(f"unexpected gh call: {joined}")

    return fake_run


def test_stale_changes_requested_is_dismissed_and_approved():
    """The core fix: a still-active CHANGES_REQUESTED review is dismissed AND a
    fresh APPROVE review is submitted on the APPROVE flip."""
    reviews = [
        _review(111, "agent-bureau-bot[bot]", "CHANGES_REQUESTED"),
    ]
    calls = []
    with patch("subprocess.run", _fake_run_factory(reviews, calls)):
        actions = sync_review_state.sync_on_approve("o/r", "1830")

    kinds = [c["kind"] for c in calls]
    assert "dismiss" in kinds, "stale CHANGES_REQUESTED review was not dismissed"
    assert "approve" in kinds, "no fresh APPROVE review was submitted"
    # The dismissal must target the right review id.
    dismiss = next(c for c in calls if c["kind"] == "dismiss")
    assert "repos/o/r/pulls/1830/reviews/111/dismissals" in dismiss["argv"]
    assert actions == 2


def test_idempotent_when_no_outstanding_changes_requested():
    """Second run / clean PR: the bot's latest review is already DISMISSED, so
    nothing is dismissed — but the APPROVE review is still (harmlessly) sent."""
    reviews = [
        _review(111, "agent-bureau-bot[bot]", "CHANGES_REQUESTED"),
        _review(222, "agent-bureau-bot[bot]", "DISMISSED"),
    ]
    calls = []
    with patch("subprocess.run", _fake_run_factory(reviews, calls)):
        sync_review_state.sync_on_approve("o/r", "1830")

    kinds = [c["kind"] for c in calls]
    assert "dismiss" not in kinds, "dismissed an already-resolved review (not idempotent)"
    assert kinds.count("approve") == 1


def test_comment_reviews_do_not_count_as_changes_requested():
    """A later COMMENTED review must not mask an earlier CHANGES_REQUESTED — and
    must not itself be treated as something to dismiss."""
    reviews = [
        _review(111, "agent-bureau-bot[bot]", "CHANGES_REQUESTED"),
        _review(112, "agent-bureau-bot[bot]", "COMMENTED"),
    ]
    ids = sync_review_state.reviews_to_dismiss(reviews)
    assert ids == [111]


def test_already_approved_review_is_not_redismissed():
    """If the bot's latest formal review is APPROVED, there is nothing stale."""
    reviews = [
        _review(111, "agent-bureau-bot[bot]", "CHANGES_REQUESTED"),
        _review(222, "agent-bureau-bot[bot]", "APPROVED"),
    ]
    assert sync_review_state.reviews_to_dismiss(reviews) == []


def test_multiple_authors_each_evaluated_independently():
    reviews = [
        _review(1, "agent-bureau-bot[bot]", "CHANGES_REQUESTED"),
        _review(2, "someone-else", "APPROVED"),
        _review(3, "agent-bureau-qa-bot[bot]", "CHANGES_REQUESTED"),
    ]
    assert sorted(sync_review_state.reviews_to_dismiss(reviews)) == [1, 3]


def test_best_effort_when_writes_fail():
    """A transient API failure must not raise — the step exists to UNBLOCK a
    merge, so it must never wedge the job."""
    reviews = [_review(111, "agent-bureau-bot[bot]", "CHANGES_REQUESTED")]
    calls = []
    with patch("subprocess.run", _fake_run_factory(reviews, calls, fail_writes=True)):
        actions = sync_review_state.sync_on_approve("o/r", "1830")
    # Writes were attempted but reported failure -> zero counted actions, no raise.
    assert actions == 0
    assert {c["kind"] for c in calls} == {"dismiss", "approve"}


def test_main_returns_zero_even_on_api_unavailable():
    """main() always exits 0 (best-effort), and rejects missing args with 2."""
    assert sync_review_state.main(["o/r"]) == 2  # missing pr number

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="down")

    with patch("subprocess.run", fake_run):
        assert sync_review_state.main(["o/r", "1830"]) == 0
