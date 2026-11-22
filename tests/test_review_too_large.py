"""RED-first tests for DRE-9328: a pull request the critic refused as too large
is its own outcome, never a crashed review, and never turns the sweep red.

THE INCIDENT (DeltaSolv PR #10, DRE-9278). 80 files and 84,730 changed lines,
84,526 of them one design file. On 2026-11-12 the critic posted "this pull
request is too large to review ... this is NOT a code rejection", and the
head-bound review check read `Review skipped — pull request too large`. The
sweep's crashed-review pass read the red check as a crash: it re-ran the
review once (the size did not change, so the re-run refused again), and from
then on every pass printed `ERROR: crashed-review: PR #10 head 3b11dec6 — the
review crashed and 1 re-dispatch(es) crashed too (cap 1); the reviewer is down
and a human must fix it`, raised the reviewer-down alert, and the DeltaSolv
sweep failed 399 runs in a row until a person merged the pull request.

What these tests hold, one acceptance criterion each:

  * the DRE-9278 replay prints the size outcome under its own name, with the
    pull request, its file and line counts and the limit, and no
    `crashed-review` line;
  * it raises no crashed-review alert and makes no re-dispatch;
  * a real crash — no size refusal on the head — is still `crashed-review`,
    and so is a forged or stale refusal (the refusal is read off the
    head-bound check AND the critic's own notice, never the wording alone);
  * with the size refusal as the only finding, the sweep's red ledgers stay
    empty on every pass, so its exit is 0;
  * the card is asked once per head, in the Green Light format, not once per
    pass, and a new head asks again.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "DeltaSolv/deltasolv")
os.environ.setdefault("REPO_SLUG", "deltasolv")
os.environ.setdefault("GH_TOKEN", "x")

import console_escalation  # noqa: E402
import crashed_review_alert  # noqa: E402
import pr_size_strategy  # noqa: E402
import publish_review_check as prc  # noqa: E402
import reconcile  # noqa: E402

REPO = "DeltaSolv/deltasolv"
SHA = "3b11dec6" + "4f" * 16
NEW_SHA = "9c0ffee1" + "2d" * 16
CARD = "DRE-9278"
BRANCH = f"agent/{CARD}-live-frames"
SIZE = {"files": 80, "lines": 84_730}
SIZE_PHRASE = "80 files, 84,730 changed lines"
#: The crashed review run at the head, as `gh run list --commit` answers it.
HEAD_RUNS = [{"databaseId": 44012345678, "status": "completed",
              "conclusion": "failure", "createdAt": "2026-11-12T20:05:00Z"}]


def _too_large_notice(at="2026-11-12T20:04:00Z") -> dict:
    """The critic's size refusal, as post_verdict.sh posts it: the message
    pr_size_strategy composes, verbatim, as the qa-bot."""
    return {"author": {"login": "agent-bureau-qa-bot"}, "createdAt": at,
            "body": pr_size_strategy.oversize_message(SIZE)}


def _redispatch_receipt(sha=SHA) -> dict:
    """The sweep's one re-dispatch, spent on the first pass after the refusal."""
    return {"author": {"login": "agent-bureau-bot"},
            "createdAt": "2026-11-12T20:20:00Z",
            "body": f"🔁 {reconcile.CRASHED_REVIEW_DISPATCH_TAG} @{sha}: the review "
                    "run for this head crashed"}


def _head_check(sha=SHA, *, too_large=SIZE_PHRASE) -> dict:
    """The head-bound review check publish_review_check.py writes."""
    _conclusion, title, summary = prc.decide(False, "", too_large=too_large)
    return {"name": prc.CHECK_NAME, "status": "completed", "conclusion": "failure",
            "head_sha": sha, "output": {"title": f"{title} @{sha[:8]}",
                                        "summary": summary}}


def _run_check() -> dict:
    """The run-attributed `call / review`, red beside the head-bound one."""
    return {"name": "call / review", "status": "completed", "conclusion": "failure"}


def _pr(sha=SHA, comments=None) -> dict:
    if comments is None:
        comments = [_too_large_notice(), _redispatch_receipt(sha),
                    _too_large_notice("2026-11-12T20:31:00Z")]
    return {"number": 10, "headRefName": BRANCH, "headRefOid": sha,
            "mergeStateStatus": "BLOCKED", "isDraft": False, "comments": comments}


@pytest.fixture(autouse=True)
def _product_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "deltasolv")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()
    reconcile._pr_listing = None
    yield
    for ledger in (reconcile._write_failures, reconcile._read_failures,
                   reconcile._stale_defects):
        ledger.clear()
    reconcile._pr_listing = None


class Sweep:
    """`recover_crashed_reviews`, pass after pass, against one stubbed world.

    The card's comments persist across passes — what one pass posts, the next
    one reads — and every dispatch, re-run, PR comment, card comment, lane
    move and console alert is recorded, never made."""

    def __init__(self, prs, checks, *, card_bodies=(), alerts=(), moves=(),
                 comment_errors=()):
        self.prs = prs
        self.checks = checks
        self.card_bodies = list(card_bodies)
        self.alerts = [dict(a) for a in alerts]
        self.dispatches: list = []
        self.reruns: list = []
        self.pr_comments: list = []
        self.card_comments: list = []
        self.advances: list = []
        self.raised: list = []
        self.resolved: list = []
        #: What each lane move answers, in order — True, False, or an
        #: exception to raise; past the end, every move is written.
        self.moves = list(moves)
        #: What each card comment raises, in order — None posts it.
        self.comment_errors = list(comment_errors)

    def _gh(self, argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        args = list(argv[1:])
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["pr", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.prs), stderr="")
        if args[0] == "api" and any("/check-runs" in a for a in args[1:]):
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                [{"total_count": len(self.checks), "check_runs": self.checks}]))
        if args[:2] == ["run", "list"]:
            rows = HEAD_RUNS if "--commit" in args else []
            return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
        if args[:2] == ["run", "rerun"]:
            self.reruns.append(args)
            return ok
        if args[:2] == ["workflow", "run"]:
            self.dispatches.append(args)
            return ok
        if args[:2] == ["pr", "comment"]:
            self.pr_comments.append(args)
            return ok
        raise AssertionError(f"unexpected gh call: {argv}")

    def _comment(self, ident, body):
        error = self.comment_errors.pop(0) if self.comment_errors else None
        if error is not None:
            raise error
        self.card_comments.append((ident, body))
        self.card_bodies.append(body)

    def _advance(self, ident, to_state, from_csv, *flags, held=False):
        answer = self.moves.pop(0) if self.moves else True
        if isinstance(answer, BaseException):
            raise answer
        if answer:
            self.advances.append((ident, to_state, from_csv))
        return answer

    def run(self):
        """One pass. Returns what it printed, stdout and stderr together."""
        reconcile._pr_listing = None
        door = {"schema": "bureau-read/1", "read_at": "2026-11-22T19:11:00Z",
                "alerts": self.alerts}
        with patch.object(reconcile.subprocess, "run", side_effect=self._gh), \
             patch.object(reconcile.pipeline_notice, "raise_notice",
                          side_effect=lambda n, **kw: self.raised.append(n) or "raised"), \
             patch.object(reconcile.pipeline_notice, "resolve_notice",
                          side_effect=lambda n, **kw: self.resolved.append(n) or "resolved"), \
             patch.object(reconcile.bureau_read, "open_alerts", side_effect=lambda: door), \
             patch.object(reconcile.linear_ops, "comment_bodies",
                          side_effect=lambda ident: list(self.card_bodies)), \
             patch.object(reconcile.linear_ops, "cmd_comment", side_effect=self._comment), \
             patch.object(reconcile.linear_ops, "cmd_advance", side_effect=self._advance), \
             patch.object(reconcile, "_swept_cards", []):
            reconcile.recover_crashed_reviews()


def _replay(capsys, **kw) -> tuple[Sweep, str]:
    sweep = Sweep([_pr()], [_head_check(), _run_check()], **kw)
    sweep.run()
    captured = capsys.readouterr()
    return sweep, captured.out + captured.err


def _red_ledgers() -> list[str]:
    """Everything `main` reads to decide a red exit."""
    return [*reconcile._write_failures, *reconcile._read_failures,
            *reconcile._stale_defects]


# --------------------------------------------------------------------------
# Acceptance 1: the DRE-9278 replay prints the size outcome, never a crash
# --------------------------------------------------------------------------
def test_the_dre_9278_replay_prints_the_size_outcome_and_no_crashed_review_line(capsys):
    _sweep, out = _replay(capsys)
    assert "crashed-review" not in out, out
    assert "reviewer is down" not in out
    line = next((ln for ln in out.splitlines() if ln.startswith("review-too-large:")), "")
    assert line, out
    assert "PR #10" in line and SHA[:8] in line
    assert SIZE_PHRASE in line
    assert "200 files / 20,000 changed lines" in line
    assert "split" in line and "a person's review" in line


def test_the_size_outcome_is_not_an_error_line(capsys):
    _sweep, out = _replay(capsys)
    assert not any(ln.startswith("ERROR:") for ln in out.splitlines()), out


# --------------------------------------------------------------------------
# Acceptance 2: no crashed-review alert, no re-dispatch
# --------------------------------------------------------------------------
def test_the_replay_raises_no_crashed_review_alert_and_makes_no_redispatch(capsys):
    sweep, _out = _replay(capsys)
    assert sweep.raised == []
    assert sweep.dispatches == [] and sweep.reruns == []
    assert sweep.pr_comments == [], "no re-dispatch receipt either"


def test_the_first_pass_after_the_refusal_does_not_spend_the_retry(capsys):
    """Before DRE-9328 the first pass re-ran the review, and the re-run
    refused again: a re-run cannot change the size."""
    sweep = Sweep([_pr(comments=[_too_large_notice()])], [_head_check(), _run_check()])
    sweep.run()
    out = capsys.readouterr()
    assert sweep.dispatches == [] and sweep.reruns == [] and sweep.pr_comments == []
    assert "crashed-review" not in out.out + out.err


def test_a_standing_reviewer_down_alert_for_the_refused_head_resolves(capsys):
    """The alert the old misreading raised says the reviewer is down; the
    refusal on the same head is positive evidence it is not."""
    stuck = crashed_review_alert.Capped(CARD, 10, SHA, "44012345678")
    alert = {"kind": f"pipeline_notice:reviewer-down:deltasolv:{CARD}",
             "severity": "WARN", "title": crashed_review_alert.title(stuck)}
    sweep, out = _replay(capsys, alerts=[alert])
    assert sweep.resolved == [f"reviewer-down:deltasolv:{CARD}"], out
    assert sweep.raised == []
    assert (f"crashed-review-alert: resolved card={CARD} — the critic refused "
            f"PR #10 for size at {SHA[:8]}, not a crash") in out, out


# --------------------------------------------------------------------------
# Acceptance 3: a real crash is still a crash, exactly as today
# --------------------------------------------------------------------------
def _crash_check(sha=SHA) -> dict:
    _conclusion, title, summary = prc.decide(False, "")
    return {"name": prc.CHECK_NAME, "status": "completed", "conclusion": "failure",
            "head_sha": sha, "output": {"title": f"{title} @{sha[:8]}",
                                        "summary": summary}}


def _assert_crash_path(sweep: Sweep, out: str) -> None:
    assert (f"ERROR: crashed-review: PR #10 head {SHA[:8]} — the review crashed "
            "and 1 re-dispatch(es) crashed too (cap 1); the reviewer is down and "
            "a human must fix it") in out
    assert "review-too-large" not in out
    assert sweep.raised == [f"reviewer-down:deltasolv:{CARD}"]
    assert any("reviewer-down PR #10 @" in body for _c, body in sweep.card_comments)


def test_a_real_crash_with_no_size_refusal_is_still_crashed_review(capsys):
    sweep = Sweep([_pr(comments=[_redispatch_receipt()])], [_crash_check(), _run_check()])
    sweep.run()
    captured = capsys.readouterr()
    _assert_crash_path(sweep, captured.out + captured.err)


def test_a_refusal_on_an_earlier_head_does_not_excuse_a_crash_on_this_one(capsys):
    """The critic's notice names no head; the head-bound check does. A head
    pushed under the limit whose review then crashed is a crash."""
    comments = [_too_large_notice(), _redispatch_receipt()]
    sweep = Sweep([_pr(comments=comments)], [_crash_check(), _run_check()])
    sweep.run()
    captured = capsys.readouterr()
    _assert_crash_path(sweep, captured.out + captured.err)


def test_a_too_large_check_with_no_critic_notice_is_not_trusted(capsys):
    """A check anybody could name `QA critic review` is not enough on its
    own: the critic's own notice, authored by the qa-bot, must say it too."""
    forged = dict(_too_large_notice(), author={"login": "someone-else"})
    sweep = Sweep([_pr(comments=[forged, _redispatch_receipt()])],
                  [_head_check(), _run_check()])
    sweep.run()
    captured = capsys.readouterr()
    _assert_crash_path(sweep, captured.out + captured.err)


# --------------------------------------------------------------------------
# Acceptance 4: the sweep's exit is 0 on the second and every later pass
# --------------------------------------------------------------------------
def test_a_size_refusal_alone_leaves_every_red_ledger_empty_on_every_pass(capsys):
    sweep = Sweep([_pr()], [_head_check(), _run_check()])
    for n in range(1, 4):
        sweep.run()
        assert _red_ledgers() == [], f"pass {n}: {_red_ledgers()}"
    out = capsys.readouterr()
    assert "ERROR:" not in out.out + out.err


# --------------------------------------------------------------------------
# Acceptance 5: the card is asked once per head, in Green Light
# --------------------------------------------------------------------------
def test_the_card_is_asked_once_per_head_not_once_per_pass(capsys):
    sweep = Sweep([_pr()], [_head_check(), _run_check()])
    for _ in range(3):
        sweep.run()
    assert len(sweep.card_comments) == 1, sweep.card_comments
    ident, body = sweep.card_comments[0]
    assert ident == CARD
    assert f"review-too-large PR #10 @{SHA}:" in body
    assert sweep.advances == [(CARD, "Green Light", reconcile.REVIEW_LANE)]


def test_the_question_is_in_the_green_light_format_and_names_what_moves_it(capsys):
    sweep, _out = _replay(capsys)
    _ident, body = sweep.card_comments[0]
    assert console_escalation.problems(body) == [], body
    asked = console_escalation.parse(body)
    assert asked is not None
    assert SIZE_PHRASE in asked.finding
    assert "200 files / 20,000 changed lines" in asked.finding
    assert "not a code rejection" in asked.finding
    assert "split" in asked.question and "person" in asked.question
    assert "reviewer is down" not in body and "crashed" not in body


def test_a_new_head_still_refused_is_asked_again(capsys):
    sweep = Sweep([_pr()], [_head_check(), _run_check()])
    sweep.run()
    sweep.prs = [_pr(sha=NEW_SHA, comments=[_too_large_notice()])]
    sweep.checks = [_head_check(NEW_SHA), _run_check()]
    sweep.run()
    keys = [body for _c, body in sweep.card_comments]
    assert len(keys) == 2
    assert f"@{SHA}:" in keys[0] and f"@{NEW_SHA}:" in keys[1]


# --------------------------------------------------------------------------
# The park is written in an order a later pass can finish
# --------------------------------------------------------------------------
def test_a_move_that_raises_asks_nothing_and_the_next_pass_parks_it(capsys):
    """Linear failed the move: nothing is written, so no marker says the card
    was asked, and the next pass makes the whole park — never a question left
    on a card that stayed In Review, with every later pass reading it as done."""
    sweep = Sweep([_pr()], [_head_check(), _run_check()],
                  moves=[reconcile.linear_ops.LinearError("Linear 500 on the move")])
    sweep.run()
    first = capsys.readouterr()
    assert sweep.card_comments == [] and sweep.advances == []
    assert _red_ledgers() == [], "a size refusal never turns the sweep red"
    assert "ERROR:" not in first.out + first.err
    assert "the next pass tries again" in first.out, first.out
    sweep.run()
    assert sweep.advances == [(CARD, "Green Light", reconcile.REVIEW_LANE)]
    assert len(sweep.card_comments) == 1, sweep.card_comments
    assert f"review-too-large PR #10 @{SHA}:" in sweep.card_comments[0][1]
    sweep.run()
    assert len(sweep.card_comments) == 1 and len(sweep.advances) == 1


def test_a_card_somebody_already_moved_is_left_where_it_is_and_not_asked(capsys):
    """The move is out of the review lane only. A card a person already moved
    gets no question: it is not waiting in Green Light, and the question says
    it is."""
    sweep = Sweep([_pr()], [_head_check(), _run_check()], moves=[False])
    sweep.run()
    out = capsys.readouterr().out
    assert sweep.card_comments == [] and sweep.advances == []
    assert (f"review-too-large: PR #10 head {SHA[:8]} — {CARD} is not in "
            f"{reconcile.REVIEW_LANE}; left where it is, nothing asked") in out, out
    assert _red_ledgers() == []


def test_a_question_that_does_not_post_after_the_move_is_a_write_failure(capsys):
    """The card is in Green Light with no question on it, and no later pass
    can tell: that half-written park is a person's to finish, so it is said
    once, on the ledger that turns the sweep red."""
    sweep = Sweep([_pr()], [_head_check(), _run_check()],
                  comment_errors=[reconcile.linear_ops.LinearError("Linear 500")])
    sweep.run()
    err = capsys.readouterr().err
    assert sweep.advances == [(CARD, "Green Light", reconcile.REVIEW_LANE)]
    assert sweep.card_comments == []
    assert len(reconcile._write_failures) == 1, reconcile._write_failures
    assert reconcile._write_failures[0].startswith(
        f"review-too-large: {CARD} (PR #10) was moved to Green Light, and its "
        "question did not post"), reconcile._write_failures
    assert "ERROR: review-too-large:" in err


def test_a_pull_request_with_no_card_id_is_reported_and_nothing_is_written(capsys):
    pr = dict(_pr(), headRefName="agent/live-frames")
    sweep = Sweep([pr], [_head_check(), _run_check()])
    sweep.run()
    out = capsys.readouterr().out
    assert sweep.card_comments == [] and sweep.advances == []
    assert (f"WARNING: review-too-large: PR #10 (agent/live-frames) carries "
            "no card id — nowhere to ask") in out, out
    assert "review-too-large: PR #10 head" in out
    assert _red_ledgers() == []


# --------------------------------------------------------------------------
# The one wording: the readers key on the writers' own constants
# --------------------------------------------------------------------------
def test_the_check_title_the_sweep_reads_is_the_one_the_publisher_writes():
    _conclusion, title, _summary = prc.decide(False, "", too_large=SIZE_PHRASE)
    assert title == prc.TOO_LARGE_TITLE


def test_the_critic_notice_the_sweep_reads_opens_with_the_strategys_opener():
    assert pr_size_strategy.oversize_message(SIZE).startswith(
        pr_size_strategy.OVERSIZE_OPENER)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
