"""RED-first tests for DRE-2282 — a crashed critic must not park a PR
forever: bounded per-head re-review, then escalate loudly.

THE BUG (live, 2026-08-07): portico's critic token stopped authenticating,
so every review run crashed at startup — the critic retries twice internally
then deliberately fails its job "for medic visibility", and the medic
(correctly, DRE-1921) skips a crashed critic. Eight PRs (#128, #141, #144,
#145, #149, #150, #151, #152) sat with fully green CI, a FAILURE review
check, and no verdict — nothing retried them, nothing reported them, and
they moved only when a human hand-dispatched qa-review.yml. #128 compounded
it: an APPROVE bound to an older sha, correctly ignored by the merge gate,
with nothing anywhere saying so.

FIX UNDER TEST — reconcile.recover_crashed_reviews(), a full-sweep backstop
generalising the DRE-2071 dependabot shape to agent PRs:
  1. An open, non-draft, non-DIRTY agent/* PR whose head has a crashed
     review check (completed failure/timed_out/cancelled, review-named) and
     no sha-bound verdict gets ONE re-dispatch of the review stub, receipted
     per head sha by a worker-bot PR comment (the DEPENDABOT_RECEIPT_CAP
     pattern). A new commit changes the sha and re-arms the budget.
  2. Cap spent = a real outage: ONE plain-English report on the linked card
     (the flag_no_checks_prs shape) naming the PR, saying the reviewer is
     down and that this is NOT a code rejection. Once per head, never once
     per sweep.
  3. A PR whose newest verdict is bound to an OLDER sha than its head, with
     no review running or dispatched, is reported the same way — the merge
     gate rightly ignores stale verdicts, so the stall is otherwise
     invisible.
  4. A review genuinely in flight (a non-completed review check at the head,
     or a workflow_dispatch review run queued/in_progress) is left alone —
     the review stub is cancel-in-progress per PR, so dispatching would
     cancel it (the DRE-2032 watchdog-kills-its-patient class).
  5. Report-only escalation: nothing on this path merges a PR or dispatches
     a build agent (no `pr merge`, no repository_dispatch, no fix/task
     stub) — only the review stub is ever dispatched.

Run: cd bureau-pipeline && python3 -m pytest tests/ -v
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import reconcile  # noqa: E402

SHA = "a" * 40
OLD_SHA = "b" * 40

TAG = getattr(reconcile, "CRASHED_REVIEW_DISPATCH_TAG", "crashed-review-redispatch")

CRASHED = '[["completed", "failure"]]'
REVIEW_RUNNING = '[["in_progress", ""]]'
NO_REVIEW_CHECKS = "[]"


def _paged(rows: str) -> str:
    """The jq'd `[status, conclusion(, name)]` rows a test names, served as
    the `gh api --paginate --slurp` pages the head read asks for (DRE-6532).
    A row with no name is the run-attributed `call / review`. An empty
    answer stays empty: the read failed."""
    if not rows:
        return rows
    runs = [{"status": r[0], "conclusion": r[1] or None,
             "name": r[2] if len(r) > 2 else "call / review"}
            for r in json.loads(rows)]
    return json.dumps([{"total_count": len(runs), "check_runs": runs}])


@pytest.fixture(autouse=True)
def _product_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", "dreadnought-foundry/atlas")
    monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _pr(number=141, sha=SHA, branch="agent/DRE-2290-widget", comments=(),
        mstate="CLEAN", draft=False):
    return {
        "number": number,
        "headRefName": branch,
        "headRefOid": sha,
        "mergeStateStatus": mstate,
        "isDraft": draft,
        "comments": list(comments),
    }


def _verdict(sha):
    """A genuine qa-bot verdict comment, sha-bound (DRE-1990 shape)."""
    return {
        "author": {"login": "agent-bureau-qa-bot"},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}\n\nLooks solid.",
    }


def _receipt(sha=SHA, author="agent-bureau-bot"):
    """The sweep's own per-sha re-dispatch receipt (worker-bot authored)."""
    return {
        "author": {"login": author},
        "body": f"🔁 {TAG} @{sha}: review re-dispatched after a crash (DRE-2282)",
    }


def _run_factory(state):
    """subprocess.run stub covering exactly the gh calls this backstop makes:
    pr list (the scan), the per-sha check-runs read, run list (the
    workflow_dispatch in-flight probe), workflow run (the dispatch), and
    pr comment (the receipt). Merge/repository-dispatch calls are recorded
    so the never-merge guardrail can assert on them."""

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        state["gh_calls"].append(tuple(argv[1:]))
        if argv[1] == "pr" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["prs"]), stderr=""
            )
        if argv[1] == "pr" and argv[2] == "view" and \
                argv[argv.index("--json") + 1] != "comments":
            # The alert's read of a pull request the listing does not hold
            # (DRE-8690), or a refused read when the test names none.
            view = state["views"].get(int(argv[3]))
            if view is None:
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="HTTP 502: Bad Gateway")
            return SimpleNamespace(returncode=0, stdout=json.dumps(view), stderr="")
        if argv[1] == "pr" and argv[2] == "view":
            # The full-thread read (DRE-7510): a thread the test names, or a
            # refused read when it names None.
            thread = state["threads"].get(int(argv[3]))
            if thread is None:
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="HTTP 502: Bad Gateway")
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"comments": thread}), stderr=""
            )
        if argv[1] == "api" and any("/check-runs" in a for a in argv[2:]):
            return SimpleNamespace(returncode=0, stdout=_paged(state["checks"]),
                                   stderr="")
        if argv[1] == "run" and argv[2] == "list":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps(state["runs"]), stderr=""
            )
        if argv[1] == "workflow" and argv[2] == "run":
            state["dispatches"].append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "pr" and argv[2] == "comment":
            state["receipts"].append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "pr" and argv[2] == "merge":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[1] == "api" and argv[2].endswith("/dispatches"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh call: {argv}")

    return fake_run


def _sweep(prs, checks=CRASHED, runs=(), card_bodies=(), threads=None, alerts=(),
           views=None, cards=()):
    """Run recover_crashed_reviews once. `checks` is the jq'd review-check
    payload for every head; `runs` the review runs every `gh run list`
    answers (the workflow_dispatch probe and the per-head listing alike);
    `card_bodies` the linked card's existing Linear comments; `threads` the
    whole comment thread `gh pr view` answers per PR number (None = refused);
    `alerts` the open console alerts the door serves, and `views` the pull
    request `gh pr view` answers for an alert whose PR is not listed
    (DRE-8690). `cards` is the pass's board read (DRE-9273), served both to
    a read of it and as the snapshot an earlier phase left behind."""
    state = {
        "prs": prs,
        "checks": checks,
        "runs": list(runs),
        "threads": dict(threads or {}),
        "views": dict(views or {}),
        "gh_calls": [],
        "dispatches": [],
        "receipts": [],
        "reports": [],
        "raised": [],
        "resolved": [],
    }
    door = {"schema": "bureau-read/1", "read_at": "2026-11-01T21:30:00Z",
            "alerts": [dict(a) for a in alerts]}
    with patch.object(
        reconcile.pipeline_notice, "raise_notice",
        side_effect=lambda notice, **kw: state["raised"].append({"notice": notice, **kw})
        or "raised",
    ), patch.object(
        reconcile.pipeline_notice, "resolve_notice",
        side_effect=lambda notice, **kw: state["resolved"].append(notice) or "resolved",
    ), patch.object(
        reconcile.bureau_read, "open_alerts", side_effect=lambda: door,
    ), patch.object(
        reconcile.subprocess, "run", side_effect=_run_factory(state)
    ), patch.object(
        reconcile.linear_ops, "comment_bodies",
        side_effect=lambda ident: list(card_bodies),
    ), patch.object(
        reconcile.linear_ops, "cmd_comment",
        side_effect=lambda ident, body: state["reports"].append((ident, body)),
    ), patch.object(
        reconcile, "active_cards", side_effect=lambda *_a, **_k: list(cards),
    ), patch.object(
        reconcile, "_swept_cards", list(cards),
    ):
        reconcile.recover_crashed_reviews()
    return state


# --------------------------------------------------------------------------
# 1. The bounded retry: one re-dispatch per head
# --------------------------------------------------------------------------
def test_crashed_review_no_verdict_redispatches_once():
    """ACCEPTANCE: a crashed review with no verdict at the head and nothing
    in flight gets exactly one re-dispatch of the review stub, with a
    sha-bound worker-bot receipt so the next sweep can count it."""
    state = _sweep([_pr()])
    assert len(state["dispatches"]) == 1, (
        "a crashed review must earn one automatic re-dispatch"
    )
    argv = state["dispatches"][0]
    assert "qa-review.yml" in argv, "the re-dispatch targets the review stub"
    assert "pr_number=141" in argv
    assert len(state["receipts"]) == 1, "the re-dispatch must post its receipt"
    body = state["receipts"][0][state["receipts"][0].index("--body") + 1]
    assert TAG in body and SHA in body, (
        "the receipt carries the tag + full head sha — that pair is what "
        "the per-head cap counts"
    )
    assert "NOT a code rejection" in body, (
        "DRE-1916 discipline: a crashed review is never framed as a rejection"
    )
    assert state["reports"] == [], "below the cap there is nothing to escalate"


def test_second_sweep_does_not_redispatch_again():
    """ACCEPTANCE: the cap is per head — with one receipt already at this
    sha and the retry also crashed, a second sweep must NOT dispatch."""
    state = _sweep([_pr(comments=[_receipt()])])
    assert state["dispatches"] == [], (
        "one re-dispatch per head sha is the whole budget — a second sweep "
        "must never dispatch again (the DRE-1921 loop cost the App quota)"
    )
    assert state["receipts"] == []


def test_new_commit_resets_the_per_head_count():
    """ACCEPTANCE: receipts bound to a superseded sha are dead — a new
    commit re-arms a fresh budget, exactly like a dependabot rebase."""
    state = _sweep([_pr(comments=[_receipt(sha=OLD_SHA)])])
    assert len(state["dispatches"]) == 1, (
        "a new head sha must re-arm the re-dispatch budget"
    )


def test_forged_receipts_do_not_count_toward_the_cap():
    """DRE-1998 discipline: only worker-bot receipts count — a forger must
    not be able to exhaust the budget and freeze the head."""
    state = _sweep([_pr(comments=[_receipt(author="mallory")])])
    assert len(state["dispatches"]) == 1


def test_retry_cap_is_one():
    assert reconcile.CRASHED_REVIEW_RETRY_CAP == 1, (
        "the card grants ONE automatic re-dispatch per head, then escalation"
    )


def test_many_crashed_prs_are_paced_per_sweep_oldest_first():
    """Eight portico PRs crashed at once — an unpaced sweep would burst
    eight critic runs into the LLM quota (the DRE-2049 lesson). Dispatches
    are paced per sweep, oldest PR first; the tail waits for the next sweep."""
    state = _sweep([_pr(number=n) for n in (152, 151, 150, 149, 145)])
    dispatched = [
        int(a.removeprefix("pr_number="))
        for argv in state["dispatches"] for a in argv
        if a.startswith("pr_number=")
    ]
    assert dispatched == [145, 149, 150], (
        f"at most {reconcile.CRASHED_REVIEW_SWEEP_CAP} paced dispatches per "
        f"sweep, oldest first — got {dispatched}"
    )


# --------------------------------------------------------------------------
# 2. Escalation when the cap is spent
# --------------------------------------------------------------------------
def test_cap_spent_reports_reviewer_down_in_plain_english():
    """ACCEPTANCE: cap exhausted = a real outage. Exactly one plain-English
    report on the linked card naming the PR, saying the reviewer is down
    and that this is NOT a code rejection."""
    state = _sweep([_pr(comments=[_receipt()])])
    assert len(state["reports"]) == 1
    ident, body = state["reports"][0]
    assert ident == "DRE-2290", "the report lands on the PR's linked card"
    assert "#141" in body
    assert "NOT a code rejection" in body
    assert "review" in body.lower() and "down" in body.lower(), (
        "the report must say plainly that the reviewer is down"
    )
    assert reconcile.REVIEWER_DOWN_TAG in body


def test_reviewer_down_report_fires_once_not_once_per_sweep():
    """ACCEPTANCE: the report is idempotent per head — repeated sweeps over
    the same stalled head must not re-post it."""
    posted = []
    for _ in range(3):
        state = _sweep(
            [_pr(comments=[_receipt()])],
            card_bodies=[b for _, b in posted],
        )
        posted.extend(state["reports"])
    assert len(posted) == 1, (
        f"one report per head, not one per sweep — got {len(posted)}"
    )


def test_reviewer_down_report_is_per_head_a_new_commit_re_arms():
    """A NEW head that also caps out is a new fact and is reported again —
    the marker binds the head sha, not the PR alone."""
    old_report = f"🚨 {reconcile.REVIEWER_DOWN_TAG} PR #141 @{OLD_SHA}: …"
    state = _sweep(
        [_pr(comments=[_receipt()])],
        card_bodies=[old_report],
    )
    assert len(state["reports"]) == 1


# --------------------------------------------------------------------------
# 2b. DRE-8690: a review that died on the Claude limit waits for limit
#     recovery, and every other review at the cap is a console alert
# --------------------------------------------------------------------------
LIMIT_RUN = "41349671195"
CRASHED_RUN = {"databaseId": int(LIMIT_RUN), "status": "completed",
               "conclusion": "failure", "createdAt": "2026-11-01T20:05:00Z"}


def _limit_marker(stage="review", kind="claude", run=LIMIT_RUN):
    """The medic's marker, as it wrote it on 11-01."""
    return (f"🪦 limit-death: kind={kind} stage={stage} "
            f"reset=2026-11-02T01:00:00Z run={run}")


def test_a_claude_review_death_at_the_cap_is_left_to_limit_recovery(capsys):
    """ACCEPTANCE: a live `kind=claude stage=review` marker naming the
    crashed review run at this head means wait — no `🚨 reviewer-down`, no
    alert, and one non-ERROR line naming the run. MUTATION CHECK: drop the
    marker check and the report closes the marker, which is what switched the
    recovery off on 11-01."""
    state = _sweep([_pr(comments=[_receipt()])], runs=[CRASHED_RUN],
                   card_bodies=[_limit_marker()])
    assert state["reports"] == []
    assert state["raised"] == []
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if LIMIT_RUN in line]
    assert len(lines) == 1, captured.out
    assert "limit-recovery owns it" in lines[0]
    assert "ERROR" not in lines[0]
    assert "reviewer is down" not in captured.err


@pytest.mark.parametrize("bodies", [
    [_limit_marker(run="99999999999")],                     # another run's death
    [_limit_marker(stage="build")],                         # not a review death
    [_limit_marker(kind="linear")],                         # not the Claude wall
    [_limit_marker(), "🚨 reviewer-down PR #9 @x: closed"],  # the marker is closed
])
def test_any_other_marker_still_reports_the_reviewer_down(bodies):
    state = _sweep([_pr(comments=[_receipt()])], runs=[CRASHED_RUN], card_bodies=bodies)
    assert len(state["raised"]) == 1
    assert len(state["reports"]) == 1


def test_the_cap_reports_the_card_and_raises_its_alert():
    """ACCEPTANCE: the `🚨 reviewer-down` comment exactly as today, plus the
    card's WARN alert naming the PR, the crashed run and the operator's
    re-run command."""
    state = _sweep([_pr(comments=[_receipt()])], runs=[CRASHED_RUN])
    assert len(state["reports"]) == 1
    assert state["reports"][0][1].startswith(f"🚨 {reconcile.REVIEWER_DOWN_TAG} PR #141 @{SHA}:")
    assert len(state["raised"]) == 1
    raised = state["raised"][0]
    assert raised["notice"] == "reviewer-down:atlas:DRE-2290"
    assert raised["severity"] == "WARN"
    assert raised["fallback"] is None
    assert raised["title"].startswith(f"DRE-2290: PR #141 @{SHA[:8]} —")
    body = raised["body"]
    assert "https://github.com/dreadnought-foundry/atlas/pull/141" in body
    assert f"https://github.com/dreadnought-foundry/atlas/actions/runs/{LIMIT_RUN}" in body
    assert f"gh run rerun {LIMIT_RUN} --failed --repo dreadnought-foundry/atlas" in body


def test_a_reported_head_raises_its_alert_on_every_pass():
    """The comment is once per head; the alert is raised each pass, and a
    repeat raise is free — so a sweep after the report still names the card."""
    first = _sweep([_pr(comments=[_receipt()])], runs=[CRASHED_RUN])
    again = _sweep([_pr(comments=[_receipt()])], runs=[CRASHED_RUN],
                   card_bodies=[b for _, b in first["reports"]])
    assert again["reports"] == []
    assert [r["notice"] for r in again["raised"]] == ["reviewer-down:atlas:DRE-2290"]


def _open_alert(card="DRE-2290", pr=141, head=SHA):
    import crashed_review_alert
    return {"kind": f"pipeline_notice:reviewer-down:atlas:{card}", "severity": "WARN",
            "title": crashed_review_alert.title(
                crashed_review_alert.Capped(card, pr, head, LIMIT_RUN)),
            "raised_at": "2026-11-01T20:18:00Z", "card": card}


def test_a_head_skipped_for_a_dispatch_in_flight_keeps_its_alert():
    """ACCEPTANCE: skipped is not recovered. The head is off this pass's
    capped list only because a review dispatch is in flight; resolving it
    would restart the Alarm Repair's two-hour clock on the next raise."""
    state = _sweep([_pr(comments=[_receipt()])],
                   runs=[{"status": "in_progress"}], alerts=[_open_alert()])
    assert state["raised"] == []
    assert state["resolved"] == []


def test_a_head_with_a_verdict_resolves_its_alert():
    state = _sweep([_pr(comments=[_receipt(), _verdict(SHA)])], alerts=[_open_alert()])
    assert state["resolved"] == ["reviewer-down:atlas:DRE-2290"]


def test_a_head_that_moved_resolves_its_alert():
    state = _sweep([_pr(sha=OLD_SHA)], checks=NO_REVIEW_CHECKS, alerts=[_open_alert()])
    assert state["resolved"] == ["reviewer-down:atlas:DRE-2290"]


def test_a_pr_gone_from_the_listing_is_read_once_and_resolved_when_merged():
    state = _sweep([], alerts=[_open_alert()],
                   views={141: {"number": 141, "state": "MERGED", "headRefOid": SHA,
                                "headRefName": "agent/DRE-2290-widget", "comments": []}})
    assert any(c[:3] == ("pr", "view", "141") for c in state["gh_calls"])
    assert state["resolved"] == ["reviewer-down:atlas:DRE-2290"]


# --------------------------------------------------------------------------
# 3. The stale-verdict stall is reported
# --------------------------------------------------------------------------
def test_stale_verdict_with_nothing_running_is_reported():
    """ACCEPTANCE: a PR whose newest verdict binds an OLDER sha than its
    head, with no review running or dispatched, is invisible the same way
    #128 was (APPROVE at bc233d4a, head two commits on) — say so."""
    state = _sweep(
        [_pr(number=128, branch="agent/DRE-2280-relay", comments=[_verdict(OLD_SHA)])],
        checks=NO_REVIEW_CHECKS,
    )
    assert state["dispatches"] == [], "the stale-verdict path is report-only"
    assert len(state["reports"]) == 1
    ident, body = state["reports"][0]
    assert ident == "DRE-2280"
    assert "#128" in body
    assert "older commit" in body
    assert "NOT a code rejection" in body
    assert reconcile.STALE_VERDICT_TAG in body


def test_stale_verdict_report_fires_once_per_head():
    posted = []
    for _ in range(3):
        state = _sweep(
            [_pr(number=128, comments=[_verdict(OLD_SHA)])],
            checks=NO_REVIEW_CHECKS,
            card_bodies=[b for _, b in posted],
        )
        posted.extend(state["reports"])
    assert len(posted) == 1


def test_stale_verdict_not_reported_while_dispatch_run_in_flight():
    """"With nothing running" is load-bearing: a live workflow_dispatch
    review run may be about to post the fresh verdict."""
    state = _sweep(
        [_pr(comments=[_verdict(OLD_SHA)])],
        checks=NO_REVIEW_CHECKS,
        runs=[{"status": "in_progress"}],
    )
    assert state["reports"] == []


def test_verdict_bound_to_head_settles_everything():
    """Control: a current-head verdict means the PR is healthy — no
    dispatch, no report, even with a crashed check run still at the head
    (the retry that produced the verdict left one behind)."""
    state = _sweep([_pr(comments=[_receipt(), _verdict(SHA)])])
    assert state["dispatches"] == []
    assert state["reports"] == []


# --------------------------------------------------------------------------
# 3b. DRE-9273: the stale-verdict notice re-tests before it is written, says
#     which re-test failed, and is lifted on a later pass once it clears
# --------------------------------------------------------------------------
NEW_SHA = "c" * 40
NOTICE_CARD = "DRE-2280"


def _card(ident=NOTICE_CARD, state="In Review", labels=(), bodies=()):
    """One card of the pass's board read, its comment window newest-first as
    the API answers it."""
    return {
        "identifier": ident,
        "title": "a card",
        "description": "",
        "updatedAt": "2026-11-12T10:00:00Z",
        "state": {"name": state},
        "labels": {"nodes": [{"name": n} for n in ("repo:atlas", *labels)]},
        "comments": {"nodes": [{"body": b} for b in reversed(list(bodies))]},
    }


def _nudge(sha=SHA, n=1):
    """The In Review nudge's review re-trigger receipt for `sha`."""
    return (f"🧹 Reconcile: {reconcile.REVIEW_NUDGE_KEY} @{sha} "
            f"({n}/{reconcile.REVIEW_NUDGE_CAP}) — no critic verdict bound to "
            "this head after 2h; review re-triggered.")


def _notice(number=128, sha=SHA):
    return (f"🚨 {reconcile.STALE_VERDICT_TAG} PR #{number} @{sha}: open PR "
            f"#{number} has its newest review verdict bound to an older commit.")


def _stale(sha=SHA, comments=None):
    return _pr(number=128, sha=sha, branch="agent/DRE-2280-relay",
               comments=[_verdict(OLD_SHA)] if comments is None else comments)


def _retest_line(body):
    lines = [line for line in body.splitlines() if line.startswith("Re-tested before posting:")]
    assert len(lines) == 1, body
    return lines[0]


def test_an_in_review_card_with_budget_left_gets_no_notice(capsys):
    """ACCEPTANCE: the In Review nudge re-reviews a head with no bound verdict
    by itself, so the notice saying nothing will is held back, and the pass
    says so in one line naming the PR, the head and the budget spent."""
    bodies = [_nudge(n=1)]
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, card_bodies=bodies,
                   cards=[_card(bodies=bodies)])
    assert state["reports"] == []
    assert state["dispatches"] == [], "the stale-verdict path is report-only"
    lines = [line for line in capsys.readouterr().out.splitlines()
             if line.startswith("stale-verdict:")]
    assert len(lines) == 1, lines
    assert "PR #128" in lines[0]
    assert SHA[:8] in lines[0]
    assert f"1/{reconcile.REVIEW_NUDGE_CAP}" in lines[0]
    assert reconcile.REVIEW_NUDGE_CAP == 3


def test_held_back_at_two_re_triggers_and_written_naming_three_of_three_at_three():
    """The cap is REVIEW_NUDGE_CAP, read and not copied: two spent leaves the
    nudge one more, three spent means it asks a person instead."""
    two = [_nudge(n=1), _nudge(n=2)]
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, card_bodies=two,
                   cards=[_card(bodies=two)])
    assert state["reports"] == []
    three = [*two, _nudge(n=3)]
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, card_bodies=three,
                   cards=[_card(bodies=three)])
    assert len(state["reports"]) == 1
    assert "3/3" in _retest_line(state["reports"][0][1])
    assert state["dispatches"] == []


def test_receipts_for_another_head_spend_nothing_here():
    """The budget is per head: an older head's receipts leave this one's
    untouched, so the notice is still held back."""
    other = [_nudge(OLD_SHA, n) for n in (1, 2, 3)]
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, card_bodies=other,
                   cards=[_card(bodies=other)])
    assert state["reports"] == []


def test_a_card_outside_in_review_gets_the_notice_naming_its_lane():
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS,
                   cards=[_card(state="In Progress")])
    assert len(state["reports"]) == 1
    ident, body = state["reports"][0]
    assert ident == NOTICE_CARD
    assert "In Progress" in _retest_line(body)
    assert "has its newest review verdict bound to" in body, "the act's anchor stays"
    assert state["dispatches"] == []


def test_a_held_card_gets_the_notice_naming_the_hold_label():
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS,
                   cards=[_card(labels=(reconcile.HOLD_LABEL,))])
    assert len(state["reports"]) == 1
    assert reconcile.HOLD_LABEL in _retest_line(state["reports"][0][1])


def test_a_card_missing_from_the_board_read_gets_the_notice_saying_so():
    """The nudge loop walks the board read and nothing else, so a card it
    does not hold is not re-reviewed by the sweep."""
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, cards=())
    assert len(state["reports"]) == 1
    assert "board read" in _retest_line(state["reports"][0][1])


def test_a_cap_of_zero_writes_the_notice_on_the_first_pass(monkeypatch):
    monkeypatch.setattr(reconcile, "REVIEW_NUDGE_CAP", 0)
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, cards=[_card()])
    assert len(state["reports"]) == 1
    assert "0/0" in _retest_line(state["reports"][0][1])


def test_a_notice_whose_head_moved_on_is_lifted_naming_the_new_head():
    """ACCEPTANCE: the notice for the old head stands until a later pass sees
    the open PR at a new head; one lift, under its own key, naming it."""
    bodies = [_notice(sha=OLD_SHA)]
    state = _sweep([_stale(sha=NEW_SHA, comments=[_verdict(OLD_SHA)])],
                   checks=NO_REVIEW_CHECKS, card_bodies=bodies,
                   cards=[_card(bodies=bodies)])
    assert len(state["reports"]) == 1, state["reports"]
    ident, body = state["reports"][0]
    assert ident == NOTICE_CARD
    assert f"{reconcile.STALE_VERDICT_LIFT_KEY} PR #128 @{OLD_SHA}:" in body
    assert NEW_SHA[:8] in body
    assert "stale-verdict" not in body, "a lift never repeats the notice's marker"
    assert "has its newest review verdict bound to" not in body
    assert state["dispatches"] == []


def test_a_notice_whose_verdict_caught_up_is_lifted_naming_the_verdict():
    bodies = [_notice(sha=SHA)]
    state = _sweep([_stale(comments=[_verdict(OLD_SHA), _verdict(SHA)])],
                   checks=NO_REVIEW_CHECKS, card_bodies=bodies,
                   cards=[_card(bodies=bodies)])
    assert len(state["reports"]) == 1, state["reports"]
    _, body = state["reports"][0]
    assert body.startswith("🧹")
    assert f"{reconcile.STALE_VERDICT_LIFT_KEY} PR #128 @{SHA}:" in body
    assert "APPROVE" in body and SHA[:8] in body
    assert "stale-verdict" not in body
    assert state["dispatches"] == []


def test_a_notice_still_true_is_not_lifted():
    bodies = [_notice(sha=SHA)]
    state = _sweep([_stale()], checks=NO_REVIEW_CHECKS, card_bodies=bodies,
                   cards=[_card(state="In Progress", bodies=bodies)])
    assert state["reports"] == []


def test_a_lifted_notice_is_not_lifted_again_on_the_next_pass():
    posted = [_notice(sha=SHA)]
    for _ in range(2):
        state = _sweep([_stale(comments=[_verdict(SHA)])], checks=NO_REVIEW_CHECKS,
                       card_bodies=posted, cards=[_card(bodies=posted)])
        posted = [*posted, *(b for _, b in state["reports"])]
    lifts = [b for b in posted if reconcile.STALE_VERDICT_LIFT_KEY in b]
    assert len(lifts) == 1, posted


def test_a_lift_counts_as_no_second_notice():
    """The once-per-head check, and routing_verdict's substring reader, still
    count one notice on a card that carries a lift."""
    posted = [_notice(sha=SHA)]
    state = _sweep([_stale(comments=[_verdict(SHA)])], checks=NO_REVIEW_CHECKS,
                   card_bodies=posted, cards=[_card(bodies=posted)])
    posted += [b for _, b in state["reports"]]
    assert sum(f"{reconcile.STALE_VERDICT_TAG} PR #128 @{SHA}:" in b for b in posted) == 1
    assert sum("stale-verdict" in b for b in posted) == 1


def test_a_lift_moves_no_card_and_changes_no_label():
    bodies = [_notice(sha=OLD_SHA)]
    with patch.object(reconcile.linear_ops, "cmd_state") as cmd_state, \
            patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
            patch.object(reconcile.linear_ops, "add_label") as add_label, \
            patch.object(reconcile.linear_ops, "remove_label") as remove_label:
        state = _sweep([_stale(sha=NEW_SHA)], checks=NO_REVIEW_CHECKS,
                       card_bodies=bodies, cards=[_card(bodies=bodies)])
    assert any(reconcile.STALE_VERDICT_LIFT_KEY in b for _, b in state["reports"])
    for write in (cmd_state, advance, add_label, remove_label):
        write.assert_not_called()
    assert state["dispatches"] == []


def test_the_lift_ships_as_one_undeclared_act_row_and_the_guard_passes():
    """Console-first: the lift is an `unconverted` `undeclared-act` row until
    agent-bureau gives it a registered act, and the receipt guard passes."""
    import check_act_receipts

    root = Path(__file__).resolve().parent.parent
    doc = json.loads((root / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
    rows = [r for r in doc["unconverted"] if r.get("file") == "scripts/reconcile.py"
            and "STALE_VERDICT_LIFT_KEY" in (r.get("anchor") or "")]
    assert [r["kind"] for r in rows] == ["undeclared-act"], rows
    assert check_act_receipts.problems() == []


def test_a_card_outside_the_board_read_is_not_re_checked():
    """Its merge, close or lane move is the visible change; nothing here reads
    a card the pass does not already hold."""
    bodies = [_notice(sha=OLD_SHA)]
    state = _sweep([_stale(sha=NEW_SHA, comments=[_verdict(NEW_SHA)])],
                   checks=NO_REVIEW_CHECKS, card_bodies=bodies, cards=())
    assert state["reports"] == []


# --------------------------------------------------------------------------
# 4. A review genuinely in progress is left alone
# --------------------------------------------------------------------------
def test_review_check_running_at_head_is_left_alone():
    """ACCEPTANCE: qa-review is cancel-in-progress per PR — dispatching
    over a live review would cancel it (DRE-2032's class)."""
    state = _sweep([_pr()], checks=REVIEW_RUNNING)
    assert state["dispatches"] == []
    assert state["reports"] == []


def test_workflow_dispatch_run_in_flight_defers_everything():
    """A crashed head whose retry run is still in flight is attempt 2 IN
    PROGRESS — no dispatch (would cancel it) and no premature escalation."""
    state = _sweep(
        [_pr(comments=[_receipt()])],
        runs=[{"status": "in_progress"}],
    )
    assert state["dispatches"] == []
    assert state["reports"] == []


# --------------------------------------------------------------------------
# 5. Report-only guardrails: never merge, never start a build agent
# --------------------------------------------------------------------------
def test_never_merges_and_never_dispatches_a_build_agent():
    """ACCEPTANCE: nothing on this path merges a PR or dispatches an
    agent-execute / fix / task run — the only workflow ever dispatched is
    the review stub."""
    for scenario in (
        _sweep([_pr()]),                          # the re-dispatch path
        _sweep([_pr(comments=[_receipt()])]),     # the escalation path
        _sweep([_pr(comments=[_verdict(OLD_SHA)])], checks=NO_REVIEW_CHECKS),
    ):
        for call in scenario["gh_calls"]:
            assert call[:2] != ("pr", "merge"), "this path must never merge"
            assert not (call[0] == "api" and call[1].endswith("/dispatches")), (
                "this path must never fire repository_dispatch (agent-execute)"
            )
        for argv in scenario["dispatches"]:
            assert "qa-review.yml" in argv, (
                f"only the review stub may be dispatched — got {argv}"
            )
            assert not any("agent" in a for a in argv if a.endswith(".yml")), (
                f"no build/fix agent workflow on this path — got {argv}"
            )


# --------------------------------------------------------------------------
# Exclusions & read discipline
# --------------------------------------------------------------------------
def test_non_agent_branches_are_excluded():
    state = _sweep([_pr(branch="dependabot/pip/pytest-9.2")])
    assert state["dispatches"] == [] and state["reports"] == []


def test_draft_prs_are_excluded():
    state = _sweep([_pr(draft=True)])
    assert state["dispatches"] == [] and state["reports"] == []


def test_dirty_prs_are_excluded():
    """unstick_conflicts owns conflicted PRs — a review of an unmergeable
    head would be wasted and the fix push re-arms a fresh sha anyway."""
    state = _sweep([_pr(mstate="DIRTY")])
    assert state["dispatches"] == [] and state["reports"] == []


def test_unreadable_check_runs_skip_the_pr():
    """DRE-2034 read discipline: a 403 parsed as emptiness is not 'no
    crashed review' — act on nothing rather than on fabricated data."""
    state = _sweep([_pr()], checks="")
    assert state["dispatches"] == [] and state["reports"] == []


def test_green_review_checks_with_no_verdict_do_nothing():
    """A completed-success review with no verdict at all is not this
    backstop's case (the In QA nudge owns it) — never dispatch or report."""
    state = _sweep([_pr()], checks='[["completed", "success"]]')
    assert state["dispatches"] == [] and state["reports"] == []


def test_agent_branch_without_card_ref_does_not_crash():
    state = _sweep([_pr(branch="agent/experiment", comments=[_receipt()])])
    assert state["reports"] == []


# --------------------------------------------------------------------------
# DRE-7510: the whole thread, and the whole bot pool
# --------------------------------------------------------------------------
# PR #1134, 2026-10-18: `gh pr list --json comments` answers a pull request's
# FIRST 100 comments and no more. Every comment from #101 on — three verdicts
# at the head and every re-dispatch receipt — was invisible to the sweep, so
# it read "no verdict at this head" and "no receipt", and dispatched again.
GH_LIST_COMMENT_PAGE = 100


def _filler(n=GH_LIST_COMMENT_PAGE):
    """The first page of a long thread: an APPROVE bound to an OLDER sha, then
    a person's comments — nothing that binds the current head."""
    return [_verdict(OLD_SHA)] + [
        {"author": {"login": "sid"}, "body": f"note {i}"} for i in range(n - 1)
    ]


def _request_changes(sha):
    return {
        "author": {"login": "agent-bureau-qa-bot"},
        "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{sha}\n\nOne finding.",
    }


def _no_writes(state):
    calls = state["gh_calls"]
    assert state["dispatches"] == [], "no `gh workflow run`"
    assert not any(c[:2] == ("run", "rerun") for c in calls), "no `gh run rerun`"
    assert state["receipts"] == [], "no `gh pr comment`"


def test_a_verdict_past_comment_100_settles_the_head():
    """#1134's shape: the listing's 100 comments carry no verdict at the head,
    the head-bound review check is at failure (what REQUEST_CHANGES writes),
    and the full thread's comment #101 is the qa-bot's REQUEST_CHANGES at the
    head. That verdict settles it — no dispatch, no re-run, no receipt."""
    first = _filler()
    state = _sweep(
        [_pr(comments=first)],
        checks='[["completed", "failure", "QA critic review"]]',
        threads={141: first + [_request_changes(SHA)]},
    )
    _no_writes(state)
    assert any(c[:2] == ("pr", "view") for c in state["gh_calls"]), (
        "a 100-comment listing is gh's page — the whole thread must be read"
    )


def test_a_receipt_past_comment_100_still_counts():
    """The same listing, and a full thread whose #101 is the sweep's own
    re-dispatch receipt at this head: the one re-dispatch is spent, so the
    sweep takes the cap-spent path instead of dispatching a second time."""
    first = _filler()
    with patch.object(
        reconcile, "_report_crashed_review_cap",
        wraps=reconcile._report_crashed_review_cap,
    ) as cap:
        state = _sweep(
            [_pr(comments=first)],
            threads={141: first + [_receipt()]},
        )
    _no_writes(state)
    cap.assert_called_once()
    assert cap.call_args.args[1] == 1, "the receipt at #101 is the one counted"


def test_an_unreadable_full_thread_means_no_action():
    """DRE-2034: a 100-comment listing whose full read fails may be hiding a
    verdict or a receipt — never act on it, and say so on the fail-loudly
    rail naming the pull request."""
    state = _sweep([_pr(comments=_filler())], threads={141: None})
    _no_writes(state)
    assert state["reports"] == []
    assert any("#141" in f for f in reconcile._write_failures), (
        f"the failed thread read names the PR — got {reconcile._write_failures}"
    )


def test_no_extra_read_below_100_comments():
    """A thread shorter than gh's page is already whole: no `gh pr view`."""
    state = _sweep([
        _pr(number=141, comments=_filler(GH_LIST_COMMENT_PAGE - 1)),
        _pr(number=142, comments=[_receipt()]),
        _pr(number=143),
    ])
    assert not any(c[:2] == ("pr", "view") for c in state["gh_calls"])
    assert len(state["dispatches"]) == 2, "141 and 143 still get their one retry"


@pytest.mark.parametrize("author", [
    "agent-bureau-bot-2", "agent-bureau-bot-4", "agent-bureau-bot-3[bot]",
])
def test_a_pool_siblings_receipt_counts_toward_the_cap(author):
    """#981's shape: a receipt written by a dispatch-pool sibling is the
    fleet's own and spends the head's one re-dispatch."""
    state = _sweep([_pr(comments=[_receipt(author=author)])])
    assert state["dispatches"] == [], (
        f"a {author} receipt at this head must stop a second dispatch"
    )
    assert state["receipts"] == []


@pytest.mark.parametrize("author", [
    "mallory", "agent-bureau-qa-bot", "agent-bureau-bot-5", "agent-bureau-bot-2x",
])
def test_no_one_outside_the_pool_counts(author):
    """DRE-1998: a receipt by anyone outside the four pool slots — a person,
    the qa-bot, a look-alike login — neither spends nor saves the budget."""
    state = _sweep([_pr(comments=[_receipt(author=author)])])
    assert len(state["dispatches"]) == 1


def test_the_pool_is_exactly_the_four_dispatch_slots():
    assert set(reconcile.WORKER_BOT_POOL_LOGINS) == {
        "agent-bureau-bot", "agent-bureau-bot-2",
        "agent-bureau-bot-3", "agent-bureau-bot-4",
    }


def _hold(sha=SHA, at="2026-10-18T10:00:00Z"):
    return {
        "author": {"login": "agent-bureau-bot"},
        "createdAt": at,
        "body": f"🛑 {reconcile.reviewer_environment.HOLD_TAG} @{sha}: held",
    }


def _timed_receipt(author, at="2026-10-18T10:30:00Z"):
    return {**_receipt(author=author), "createdAt": at}


def test_a_pool_siblings_redispatch_newer_than_the_hold_spends_it():
    """`_hold_state`: a re-dispatch receipt newer than the hold means the
    hold was released and spent — whichever pool slot posted it."""
    pr = _pr(comments=[_hold(), _timed_receipt("agent-bureau-bot-2")])
    assert reconcile._hold_state(pr, None) == ("", "")


def test_a_forged_redispatch_newer_than_the_hold_does_not_spend_it():
    pr = _pr(comments=[_hold(), _timed_receipt("agent-bureau-bot-5")])
    held = reconcile._hold_state(pr, None)
    assert held.held_at == "2026-10-18T10:00:00Z" and held.release == ""


# --------------------------------------------------------------------------
# Wiring: runs on every full sweep, never in the event-hook modes
# --------------------------------------------------------------------------
def _main_mocks():
    return [
        mock.patch.object(reconcile, name) for name in (
            "unstick_conflicts", "retrigger_dead_heads", "flag_no_checks_prs",
            "fix_approved_but_red", "retry_dead_fix_runs",
            "review_dependabot_prs", "card_dependabot_prs", "check_dependabot_capacity",
            "promote_ready",
        )
    ] + [
        mock.patch.object(reconcile, "flag_stranded", return_value=set()),
        mock.patch.object(reconcile, "active_cards", return_value=[]),
        mock.patch.object(reconcile, "backlog_children", return_value=[]),
    ]


def test_full_sweep_runs_the_recovery():
    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        rec = stack.enter_context(
            mock.patch.object(reconcile, "recover_crashed_reviews")
        )
        for m in _main_mocks():
            stack.enter_context(m)
        reconcile.main()
    rec.assert_called_once_with()


def test_conflicts_only_mode_does_not_run_it():
    with mock.patch.object(reconcile, "unstick_conflicts"), \
         mock.patch.object(reconcile, "recover_crashed_reviews") as rec:
        reconcile.main(conflicts_only=True)
    rec.assert_not_called()


def test_promote_only_mode_does_not_run_it():
    reconcile._write_failures.clear()
    with contextlib.ExitStack() as stack:
        rec = stack.enter_context(
            mock.patch.object(reconcile, "recover_crashed_reviews")
        )
        for m in _main_mocks():
            stack.enter_context(m)
        reconcile.main(promote_only=True)
    rec.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
