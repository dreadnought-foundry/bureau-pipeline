"""RED-first tests for DRE-9230, the sweep half: a held verdict is named
HELD, re-asked once with its reason, and a second hold on the same head sends
the card to Triage instead of leaving it In Review with nothing scheduled.

THE INCIDENT (portico #1107, run 42016908858). The evidence gate held the
critic's verdict at 3:16 AM PT. A held verdict fails the review job exactly
as a crash does, so `recover_crashed_reviews` read it as one: it logged
`crashed-review-redispatch`, posted "the review run for this head crashed",
and re-ran the same review once — blind. It held again at 3:24 AM PT, the
retry budget was spent, and the pull request sat In Review with nothing
scheduled until an operator fixed it by hand at 3:55 AM PT.

What these tests hold, one acceptance criterion each:

  * the sweep's log line and its pull request comment say HELD, never
    crashed — and the crash path's own wording is pinned beside it, so the
    two cases stay two;
  * the re-ask is one per head, the crash budget is not spent by it, and a
    new commit re-arms it;
  * a second hold on the same head moves the card to Triage with ONE line
    naming the pull request, both runs and the held finding — and the
    replay from #1107's own two held verdicts produces exactly that move.

The critic side — the re-run review is told why — is tests/test_held_verdict.py.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/atlas")
os.environ.setdefault("REPO_SLUG", "atlas")
os.environ.setdefault("GH_TOKEN", "x")

import held_verdict  # noqa: E402
import reconcile  # noqa: E402

SHA = "a" * 40
REPO = "dreadnought-foundry/atlas"
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "held-verdict-1107.json").read_text())
RUN = FIXTURE["run"]
HOLD_1, HOLD_2 = [c for c in FIXTURE["comments"]
                  if c["author"]["login"] == "agent-bureau-qa-bot"]
#: The review check at every head: completed red, as a hold leaves it.
FAILED_CHECK = [{"status": "completed", "conclusion": "failure",
                 "name": "call / review"}]
#: The run listing at the head: the held run, completed red.
HELD_RUN = [{"databaseId": int(RUN), "status": "completed",
             "conclusion": "failure", "createdAt": "2026-11-05T11:12:00Z"}]


@pytest.fixture(autouse=True)
def _product_repo(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", "atlas")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._pr_listing = None
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    reconcile._pr_listing = None


def _pr(number=141, sha=SHA, branch="agent/DRE-2290-widget", comments=()):
    return {"number": number, "headRefName": branch, "headRefOid": sha,
            "mergeStateStatus": "BLOCKED", "isDraft": False,
            "comments": list(comments)}


def _hold(comment: dict, attempt: str, sha: str = SHA, author="agent-bureau-qa-bot") -> dict:
    """One of #1107's holds as the post step publishes it now: its body
    verbatim, then the line naming the head and the run."""
    return {
        "author": {"login": author},
        "createdAt": comment["createdAt"],
        "body": comment["body"].rstrip("\n") + "\n\n"
        + held_verdict.footer(sha, RUN, attempt) + "\n",
    }


def _reask(sha: str = SHA) -> dict:
    """The sweep's own re-ask receipt, as a worker-pool slot posts it."""
    return {
        "author": {"login": "agent-bureau-bot-2"},
        "createdAt": "2026-11-05T11:20:09Z",
        "body": held_verdict.reask_notice(sha, f"re-ran the held review run {RUN}",
                                          held_verdict.read(_hold(HOLD_1, "1", sha)["body"])),
    }


def _body(argv) -> str:
    return argv[argv.index("--body") + 1]


def _run(prs, *, moved=True, card_bodies=(), head_runs=HELD_RUN):
    """Run `recover_crashed_reviews` once against a stubbed `gh` and Linear.

    `head_runs` answers the run listing at the head (the run to re-run); the
    workflow_dispatch in-flight probe answers nothing in flight. Every re-run,
    dispatch, pull request comment, card comment and lane move is recorded,
    never made."""
    state = {"gh_calls": [], "dispatches": [], "receipts": [], "reports": [],
             "advances": [], "raised": []}

    def fake_run(argv, **kwargs):
        assert argv[0] == "gh", f"unexpected call: {argv}"
        args = list(argv[1:])
        state["gh_calls"].append(tuple(args))
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["pr", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(prs), stderr="")
        if args[0] == "api" and any("/check-runs" in a for a in args[1:]):
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                [{"total_count": len(FAILED_CHECK), "check_runs": FAILED_CHECK}]))
        if args[:2] == ["run", "list"]:
            rows = head_runs if "--commit" in args else []
            return SimpleNamespace(returncode=0, stdout=json.dumps(list(rows)), stderr="")
        if args[:2] == ["run", "rerun"]:
            return ok
        if args[:2] == ["workflow", "run"]:
            state["dispatches"].append(args)
            return ok
        if args[:2] == ["pr", "comment"]:
            state["receipts"].append(args)
            return ok
        raise AssertionError(f"unexpected gh call: {argv}")

    def advance(ident, to_state, from_csv, *flags, held=False):
        state["advances"].append((ident, to_state, from_csv))
        return moved

    door = {"schema": "bureau-read/1", "read_at": "2026-11-05T11:30:00Z", "alerts": []}
    with patch.object(reconcile.subprocess, "run", side_effect=fake_run), \
         patch.object(reconcile.pipeline_notice, "raise_notice",
                      side_effect=lambda notice, **kw: state["raised"].append(notice) or "raised"), \
         patch.object(reconcile.pipeline_notice, "resolve_notice",
                      side_effect=lambda notice, **kw: "resolved"), \
         patch.object(reconcile.bureau_read, "open_alerts", side_effect=lambda: door), \
         patch.object(reconcile.linear_ops, "comment_bodies",
                      side_effect=lambda ident: list(card_bodies)), \
         patch.object(reconcile.linear_ops, "cmd_comment",
                      side_effect=lambda ident, body: state["reports"].append((ident, body))), \
         patch.object(reconcile.linear_ops, "cmd_advance", side_effect=advance):
        reconcile.recover_crashed_reviews()
    assert reconcile._write_failures == [], reconcile._write_failures
    return state


# --------------------------------------------------------------------------
# Acceptance 2: held is named held, crashed is named crashed — both pinned
# --------------------------------------------------------------------------
def test_a_held_verdict_is_logged_as_held_not_as_a_crashed_review(capsys):
    _run([_pr(comments=[_hold(HOLD_1, "1")])])
    out = capsys.readouterr().out
    assert (
        f"held-verdict: PR #141 head {SHA[:8]} — the critic's verdict was HELD "
        f"by the evidence gate in run {RUN} attempt 1, not a crashed review: "
        "it gave no command output for the finding it named. Re-asking the "
        "review once, told why it was held"
    ) in out
    assert f"held-verdict: PR #141 — re-ran the held review run {RUN}" in out
    assert "crashed" not in out.replace("not a crashed review", "")


def test_a_held_verdicts_pull_request_comment_says_held():
    state = _run([_pr(comments=[_hold(HOLD_1, "1")])])
    assert len(state["receipts"]) == 1
    body = _body(state["receipts"][0])
    assert body == (
        f"🔁 held-verdict-reask @{SHA}: the critic's verdict on this head was "
        f"HELD by the evidence gate in run {RUN} attempt 1 — it gave no command "
        "output for the finding it named, so it was posted as a hold, not a "
        "verdict. This is not a crashed review and not a code rejection. The "
        f"reconcile sweep re-ran the held review run {RUN}, and the re-run "
        "review is told why the last one was held. One re-ask per head: if the "
        "next verdict on this head is held too, the card goes to Triage naming "
        "both runs."
    )
    assert reconcile.CRASHED_REVIEW_DISPATCH_TAG not in body


def test_a_crashed_review_keeps_its_own_wording(capsys):
    """The other case, pinned beside it: a review that crashed with no
    verdict and no hold is still logged and receipted as a crash."""
    state = _run([_pr()])
    out = capsys.readouterr().out
    assert (
        f"crashed-review: PR #141 head {SHA[:8]} — review crashed with no "
        "verdict; re-running the crashed run (1/1 for this head)"
    ) in out
    assert f"crashed-review: PR #141 — re-ran the crashed review run {RUN}" in out
    assert "held-verdict" not in out
    body = _body(state["receipts"][0])
    assert body.startswith(
        f"🔁 {reconcile.CRASHED_REVIEW_DISPATCH_TAG} @{SHA}: the review run for "
        "this head crashed (an infra failure — it never produced a verdict, so "
        f"this is NOT a code rejection), and the reconcile sweep re-ran the "
        f"crashed review run {RUN} (DRE-2282)."
    )


def test_the_re_ask_re_runs_the_held_run_itself():
    state = _run([_pr(comments=[_hold(HOLD_1, "1")])])
    reruns = [c for c in state["gh_calls"] if c[:2] == ("run", "rerun")]
    assert reruns == [("run", "rerun", RUN, "--failed", "--repo",
                       REPO)]


# --------------------------------------------------------------------------
# One re-ask per head
# --------------------------------------------------------------------------
def test_one_re_ask_per_head():
    """The re-ask spent and no second hold yet — the re-asked review is
    still to report, or crashed for real. Never a second re-ask."""
    state = _run([_pr(comments=[_hold(HOLD_1, "1"), _reask()])])
    assert not [r for r in state["receipts"] if "held-verdict-reask" in _body(r)]


def test_a_re_ask_does_not_spend_the_crash_budget():
    """The re-asked review crashed for real: that crash still gets the
    crash path's own one retry, under the crash wording."""
    state = _run([_pr(comments=[_hold(HOLD_1, "1"), _reask()])])
    assert len(state["receipts"]) == 1
    assert reconcile.CRASHED_REVIEW_DISPATCH_TAG in _body(state["receipts"][0])


def test_a_hold_on_an_older_head_is_history():
    """A new commit re-arms everything: an old head's hold neither re-asks
    nor counts toward Triage."""
    old = "c" * 40
    state = _run([_pr(comments=[_hold(HOLD_1, "1", sha=old),
                                _hold(HOLD_2, "2", sha=old)])])
    assert state["advances"] == []
    assert reconcile.CRASHED_REVIEW_DISPATCH_TAG in _body(state["receipts"][0])


def test_a_hold_somebody_else_wrote_is_invisible():
    state = _run([_pr(comments=[_hold(HOLD_1, "1", author="mallory"),
                                _hold(HOLD_2, "2", author="mallory")])])
    assert state["advances"] == []
    assert reconcile.CRASHED_REVIEW_DISPATCH_TAG in _body(state["receipts"][0])


# --------------------------------------------------------------------------
# Acceptance 3: the second hold on the same head goes to Triage
# --------------------------------------------------------------------------
def _replay_pr(*comments):
    return _pr(number=FIXTURE["number"], sha=FIXTURE["headRefOid"],
               branch=FIXTURE["headRefName"], comments=comments)


def _replay_thread(receipt):
    """#1107's thread up to 11:24:12Z, its two holds as the post step now
    publishes them, with `receipt` standing where the sweep's 11:20:09Z one
    stood."""
    sha = FIXTURE["headRefOid"]
    out = []
    for c in FIXTURE["comments"]:
        if c is HOLD_1:
            out.append(_hold(HOLD_1, "1", sha))
        elif c is HOLD_2:
            out.append(_hold(HOLD_2, "2", sha))
        elif "crashed-review-redispatch" in c["body"]:
            out.append(receipt if receipt is not None else c)
        else:
            out.append(c)
    return out


@pytest.mark.parametrize("receipt", ["re-ask", "legacy"])
def test_replay_1107_two_held_verdicts_go_to_triage(receipt, capsys):
    """THE REPLAY: #1107's own two held verdicts, on one head. Under the
    receipt this card posts, and under the `crashed-review-redispatch` one
    the sweep really posted that night, the answer is the same Triage move
    with one line naming the pull request, both runs and the held finding."""
    sha = FIXTURE["headRefOid"]
    thread = _replay_thread(_reask(sha) if receipt == "re-ask" else None)
    state = _run([_replay_pr(*thread)])

    assert state["advances"] == [("DRE-9229", "Triage", reconcile.REVIEW_LANE)]
    assert len(state["reports"]) == 1
    ident, line = state["reports"][0]
    assert ident == "DRE-9229"
    assert "\n" not in line, "the finding is one line"
    assert line == (
        f"🚦 held-verdict-twice PR #1107 @{sha}: the evidence gate held the "
        f"critic's verdict on this head twice — run {RUN} attempt 1 and run "
        f"{RUN} attempt 2 — the second time on the finding: "
        '`gh pr view 1107 --json body -q .body | grep -in "what\'s new"` '
        "printed nothing and exited 1. Moved to Triage: answer the finding, "
        "then push a new commit or re-run the review by hand."
    )
    # Nothing is scheduled behind it, and nothing calls it an outage.
    assert state["dispatches"] == []
    assert not [c for c in state["gh_calls"] if c[:2] == ("run", "rerun")]
    assert state["receipts"] == []
    assert reconcile.REVIEWER_DOWN_TAG not in line
    assert state["raised"] == []
    assert (f"held-verdict: PR #1107 head {sha[:8]} — held twice (run {RUN} "
            f"attempt 1, run {RUN} attempt 2); DRE-9229 moved to Triage") \
        in capsys.readouterr().out


def test_the_triage_move_happens_once_per_head():
    sha = FIXTURE["headRefOid"]
    first = _run([_replay_pr(*_replay_thread(None))])
    posted = [b for _, b in first["reports"]]
    again = _run([_replay_pr(*_replay_thread(None))], card_bodies=posted)
    assert again["advances"] == [] and again["reports"] == []
    assert held_verdict.twice_marker(1107, sha) in posted[0]


def test_a_card_no_longer_in_review_is_left_where_it_is():
    """Somebody already moved it: the sweep writes nothing on it."""
    state = _run([_replay_pr(*_replay_thread(None))], moved=False)
    assert len(state["advances"]) == 1
    assert state["reports"] == []
    assert state["dispatches"] == [] and state["receipts"] == []
