"""A spent merge-gate budget re-runs a canceled job instead of asking (DRE-9191).

At REVIEW_NUDGE_CAP the review lane parks the card in Green Light with a
`review-nudge-cap` question: "Is the red check holding the merge to be fixed,
or is the change dropped?" On 2026-11-03 agent-bureau #4663 (DRE-9005), head
e911378, had the critic's APPROVE, and the only checks not green were a
canceled shard, the summary job red because of it, and a Verify run that
finished without posting a verdict. There was nothing to fix and nothing for
the CEO to decide; re-running the runs was enough. The row was false.

So, before the cap asks, the sweep classifies the head's non-green checks.
When every one belongs to a re-runnable run, it re-runs each once with
`gh run rerun <id> --failed`, posts one `review-nudge-cap-rerun @<head>` note
on the card, and writes no hold, no question, no blocker and no park. Once per
head: with the note standing and every run it re-ran finished, a check still
not green sends the question as today, and its Finding names each job and says
it was re-run once at the cap.

The replay fixture is agent-bureau #4663 at 8:49 PM PT on 2026-11-03, built
from the values the card records; the values it does not record are chosen
and marked so. The sweep is the real `main()`, over a board that remembers
what each pass wrote, the way tests/test_review_lane_nudge_cap.py drives it.

Run: cd bureau-pipeline && python3 -m pytest tests/test_review_cap_canceled_rerun.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import canceled_check  # noqa: E402
import console_escalation  # noqa: E402
import reconcile  # noqa: E402
import review_cap_question  # noqa: E402

# --------------------------------------------------------------------------- #
# the replay fixture: agent-bureau #4663 at the cap                            #
# --------------------------------------------------------------------------- #

IDENT = "DRE-9005"
NUMBER = 4663
BRANCH = "agent/DRE-9005-activity-lines"  # chosen
HEAD = "e911378" + "0" * 33  # the card records the prefix; the padding is chosen
CI_RUN = 41708617138
VERIFY_RUN = 41708617694
#: 8:49 PM PT on 2026-11-03 (PST, UTC-8).
CLOCK = datetime(2026, 11, 4, 4, 49, tzinfo=timezone.utc)
#: The three gate-nudge receipts (times chosen; the first is 6.3 h before the clock).
NUDGES = ("2026-11-03T22:31:00Z", "2026-11-04T00:45:00Z", "2026-11-04T02:55:00Z")
SHARD_3_LOG = (
    "2026-11-03T22:08:40.0000000Z ============ test session starts ============\n"
    "2026-11-03T22:09:00.0000000Z ##[error]The operation was canceled.\n"
)
SUMMARY_LOG = (
    "2026-11-03T22:09:19.0000000Z backend shards concluded 'cancelled'\n"
    "2026-11-03T22:09:19.5000000Z ##[error]Process completed with exit code 1.\n"
)
SHARD_2_FAILED_LOG = (
    "2026-11-03T22:07:10.0000000Z ============ short test summary info ============\n"
    "2026-11-03T22:07:10.1000000Z FAILED console/backend/tests/test_activity.py"
    "::test_lines - AssertionError\n"
    "2026-11-03T22:07:10.2000000Z ##[error]Process completed with exit code 1.\n"
)


def _job_url(run_id, job_id):
    return (f"https://github.com/dreadnought-foundry/agent-bureau/actions/runs/"
            f"{run_id}/job/{job_id}")


def _check(job_id, name, conclusion, completed_at, run_id=CI_RUN,
           status="completed"):
    return {"id": job_id, "name": name, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "completed_at": completed_at if status == "completed" else None,
            "details_url": _job_url(run_id, job_id)}


def _checks():
    """The head's check runs at the cap, as the card records them."""
    return [
        _check(9001, "Console backend shard 3", "cancelled", "2026-11-03T22:09:00Z"),
        _check(9002, "Console backend (pytest)", "failure", "2026-11-03T22:09:20Z"),
        _check(9003, "Console backend shard 1", "success", "2026-11-03T22:05:00Z"),
        _check(9004, "Console backend shard 2", "success", "2026-11-03T22:06:00Z"),
        _check(9005, "verify / verify", "failure", "2026-11-03T22:20:00Z",
               run_id=VERIFY_RUN),
    ]


def _logs():
    return {9001: SHARD_3_LOG, 9002: SUMMARY_LOG}


def _run(run_id, status="completed", conclusion="failure", attempt=3):
    return {"id": run_id, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "run_attempt": attempt, "head_sha": HEAD}


def _runs():
    return {CI_RUN: _run(CI_RUN), VERIFY_RUN: _run(VERIFY_RUN)}


def _listing(status="completed", conclusion="failure", attempt=3):
    """`gh run list --workflow verify.yml --commit <head> --json …`."""
    return [{"databaseId": VERIFY_RUN, "status": status,
             "conclusion": conclusion if status == "completed" else None,
             "attempt": attempt, "createdAt": "2026-11-03T22:12:00Z"}]


def _qa(body):
    return {"author": {"login": reconcile.QA_BOT_LOGIN}, "body": body,
            "createdAt": "2026-11-03T22:15:00Z"}


def _worker(body, at="2026-11-04T04:00:00Z"):
    return {"author": {"login": reconcile.WORKER_BOT_LOGIN}, "body": body,
            "createdAt": at}


def _pr(*extra):
    return {
        "number": NUMBER,
        "headRefName": BRANCH,
        "state": "OPEN",
        "headRefOid": HEAD,
        "isDraft": False,
        "comments": [_qa(f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}\n\nok"),
                     *extra],
    }


class _GitHub:
    """The reads the cap makes, answered from the fixture, and every write."""

    def __init__(self, checks=None, logs=None, runs=None, listing=None):
        self.checks = _checks() if checks is None else checks
        self.logs = _logs() if logs is None else logs
        self.runs = _runs() if runs is None else runs
        self.listing = _listing() if listing is None else listing
        self.dispatched: list[tuple] = []
        self.refuse: set = set()

    def gh(self, *args, **_kw):
        joined = " ".join(str(a) for a in args)
        if f"commits/{HEAD}/check-runs" in joined:
            return json.dumps([{"total_count": len(self.checks),
                                "check_runs": self.checks}])
        if "/annotations" in joined:
            return "[]"
        if "/actions/jobs/" in joined and joined.endswith("/logs"):
            job = int(joined.rsplit("/", 2)[-2])
            return self.logs.get(job, "")
        if args[:2] == ("run", "list") and "verify.yml" in args:
            return json.dumps(self.listing) if self.listing is not None else ""
        if args and args[0] == "api" and "/actions/runs/" in joined:
            run_id = int(joined.rstrip("/").rsplit("/", 1)[-1])
            run = self.runs.get(run_id)
            return json.dumps(run) if run else ""
        return ""

    def dispatch(self, *args):
        if args[:2] == ("run", "rerun") and args[2] in self.refuse:
            raise reconcile.ReconcileWriteError(f"gh {' '.join(args)} failed rc=1")
        self.dispatched.append(args)


def _reruns(*ids):
    return [("run", "rerun", str(i), "--failed", "--repo", reconcile.REPO)
            for i in ids]


class _Board:
    """The card that remembers what each sweep wrote (oldest -> newest)."""

    def __init__(self, extra=()):
        self.comments = [
            {"body": f"🧹 Reconcile: {reconcile.GATE_NUDGE_KEY} @{HEAD} ({n}/3) — "
                     "critic APPROVE, verifier none, and the gate has not merged "
                     "it; merge gate re-triggered.", "createdAt": at}
            for n, at in enumerate(NUDGES, start=1)
        ] + list(extra)
        self.labels: list[str] = []
        self.state = reconcile.REVIEW_LANE
        self.pr_notes: list[str] = []
        self.moves: list[tuple] = []

    def card(self):
        return {
            "identifier": IDENT,
            "description": "**Repo:** agent-bureau\nwork",
            "state": {"name": self.state},
            "labels": {"nodes": [{"name": n} for n in self.labels]},
            "updatedAt": "2026-11-04T02:55:00Z",
            # The API's order — newest first; window_nodes reverses it.
            "comments": {"nodes": list(reversed(self.comments))},
        }

    def sweep(self, pr, github):
        """One full pass; returns what it posted on the card."""
        posted: list[str] = []

        def comment(ident, body, *_flags):
            assert ident == IDENT
            posted.append(body)
            self.comments.append({"body": body,
                                  "createdAt": CLOCK.isoformat().replace("+00:00", "Z")})

        def label(ident, name):
            assert ident == IDENT
            if name not in self.labels:
                self.labels.append(name)

        def advance(ident, to, from_csv, *_flags, **_kw):
            self.moves.append((to, from_csv))
            if self.state in from_csv.split(","):
                self.state = to

        def pr_note(number, body):
            self.pr_notes.append(body)
            pr["comments"].append(_worker(body))
            return True

        def age(ts):
            if ts == NUDGES[0]:
                return 378  # 6.3 h before the clock
            return 999

        mocks = {
            "unstick_conflicts": MagicMock(),
            "retrigger_dead_heads": MagicMock(),
            "check_dependabot_capacity": MagicMock(),
            "fix_approved_but_red": MagicMock(),
            "close_finished_epics": MagicMock(),
            "promote_ready": MagicMock(return_value=0),
            "age_minutes": MagicMock(side_effect=age),
            "pr_for": MagicMock(return_value=pr),
            "redispatch": MagicMock(return_value=True),
            "active_cards": MagicMock(return_value=[self.card()]),
            "flag_stranded": MagicMock(return_value=set()),
            "_nudge": MagicMock(return_value=True),
            "gh": MagicMock(side_effect=github.gh),
            "gh_dispatch": MagicMock(side_effect=github.dispatch),
            "report_fix_concurrency": MagicMock(),
            "_post_pr_note": MagicMock(side_effect=pr_note),
        }
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "cmd_comment", side_effect=comment
        ), patch.object(
            reconcile.linear_ops, "add_label", side_effect=label
        ), patch.object(
            reconcile.linear_ops, "cmd_advance", side_effect=advance
        ), patch.object(reconcile.linear_ops, "cmd_state") as cmd_state:
            reconcile.main()
        cmd_state.assert_not_called()
        self.nudge = mocks["_nudge"]
        return posted


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    # The quiet read path: every Actions read goes through the patched gh().
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _notes(bodies):
    return [b for b in bodies if f"{reconcile.REVIEW_NUDGE_CAP_RERUN_KEY} @{HEAD}" in b]


def _questions(bodies):
    return [b for b in bodies if f"🚨 {reconcile.REVIEW_NUDGE_CAP_KEY} PR #{NUMBER}" in b]


def _finding(question):
    found = console_escalation.parse(question)
    assert found is not None, question
    return found.finding


def _first_pass_note():
    """The note replay test 1's first pass leaves, as the card would carry it."""
    board, github = _Board(), _GitHub()
    posted = board.sweep(_pr(), github)
    notes = _notes(posted)
    assert len(notes) == 1, posted
    return notes[0]


# --------------------------------------------------------------------------- #
# the key                                                                      #
# --------------------------------------------------------------------------- #


class TestTheKey:
    def test_the_key_is_its_own(self):
        assert reconcile.REVIEW_NUDGE_CAP_RERUN_KEY == "review-nudge-cap-rerun"

    def test_it_matches_none_of_the_review_lane_keys(self):
        note = f"{reconcile.REVIEW_NUDGE_CAP_RERUN_KEY} @{HEAD}"
        for key in (f"{reconcile.REVIEW_NUDGE_KEY} @{HEAD} (",
                    f"{reconcile.GATE_NUDGE_KEY} @{HEAD} (",
                    f"{reconcile.REVIEW_NUDGE_CAP_KEY} @{HEAD}",
                    f"{reconcile.REVIEW_NUDGE_DRAFT_KEY} @{HEAD}"):
            assert key not in note, key

    def test_a_note_spends_none_of_the_nudge_budget(self):
        board = _Board(extra=[{"body": f"🧹 Reconcile: "
                                       f"{reconcile.REVIEW_NUDGE_CAP_RERUN_KEY} @{HEAD} — x",
                               "createdAt": "2026-11-04T04:49:00Z"}])
        assert reconcile.review_nudges_spent(
            board.card(), reconcile.GATE_NUDGE_KEY, HEAD) == 3


# --------------------------------------------------------------------------- #
# replay test 1: #4663 at the cap re-runs, and asks nobody                     #
# --------------------------------------------------------------------------- #


class TestReplayOneRerunsAtTheCap:
    def test_two_reruns_one_note_and_no_park(self):
        board, github = _Board(), _GitHub()
        posted = board.sweep(_pr(), github)

        assert github.dispatched == _reruns(CI_RUN, VERIFY_RUN)
        assert len(posted) == 1, posted
        note = posted[0]
        assert note.startswith(
            f"🧹 Reconcile: {reconcile.REVIEW_NUDGE_CAP_RERUN_KEY} @{HEAD} — ")
        for name in ("Console backend shard 3", "Console backend (pytest)"):
            assert name in note
        assert f"run {CI_RUN}" in note
        assert f"Verify run {VERIFY_RUN}" in note

        assert reconcile.HOLD_LABEL not in board.labels
        assert _questions(posted) == []
        assert not [b for b in posted if b.startswith("🔒 hold:")]
        assert board.pr_notes == []
        assert board.moves == []
        assert board.state == reconcile.REVIEW_LANE
        board.nudge.assert_not_called()
        assert reconcile._write_failures == []

    def test_a_second_pass_with_both_runs_going_writes_nothing(self):
        board, github = _Board(), _GitHub()
        pr = _pr()
        board.sweep(pr, github)
        assert len(_notes(c["body"] for c in board.comments)) == 1

        going = _GitHub(
            checks=[
                _check(9001, "Console backend shard 3", None, None, status="in_progress"),
                _check(9002, "Console backend (pytest)", None, None, status="queued"),
                _check(9003, "Console backend shard 1", "success", "2026-11-03T22:05:00Z"),
                _check(9004, "Console backend shard 2", "success", "2026-11-03T22:06:00Z"),
                _check(9005, "verify / verify", None, None, run_id=VERIFY_RUN,
                       status="in_progress"),
            ],
            runs={CI_RUN: _run(CI_RUN, status="in_progress", attempt=4),
                  VERIFY_RUN: _run(VERIFY_RUN, status="in_progress", attempt=4)},
            listing=_listing(status="in_progress", attempt=4),
        )
        posted = board.sweep(pr, going)
        assert posted == []
        assert going.dispatched == []
        assert board.labels == []
        assert board.pr_notes == []
        assert board.moves == []

    def test_one_run_still_going_writes_nothing(self):
        board = _Board(extra=[{"body": _first_pass_note(),
                               "createdAt": "2026-11-04T04:49:00Z"}])
        github = _GitHub(runs={CI_RUN: _run(CI_RUN, attempt=4),
                               VERIFY_RUN: _run(VERIFY_RUN, status="in_progress",
                                                attempt=4)})
        posted = board.sweep(_pr(), github)
        assert posted == [] and github.dispatched == []
        assert board.pr_notes == [] and board.moves == []


class _GitHubOnce(_GitHub):
    """GitHub refuses a second re-run of a run already re-run and going."""

    def dispatch(self, *args):
        if args[:2] == ("run", "rerun") and args in self.dispatched:
            raise reconcile.ReconcileWriteError(
                f"gh {' '.join(args)} failed rc=1: run is already running")
        super().dispatch(*args)


VERIFY_CANCELED_LOG = (
    "2026-11-03T22:19:59.0000000Z ##[error]The operation was canceled.\n"
)


def _verify_canceled_checks():
    """The fixture, with the Verify run's own job the one that was canceled."""
    checks = _checks()
    checks[4] = _check(9005, "verify / verify", "cancelled",
                       "2026-11-03T22:20:00Z", run_id=VERIFY_RUN)
    return checks


def _verify_canceled_github(**over):
    runs = {CI_RUN: _run(CI_RUN),
            VERIFY_RUN: _run(VERIFY_RUN, conclusion="cancelled")}
    kw = dict(checks=_verify_canceled_checks(), logs={**_logs(), 9005: VERIFY_CANCELED_LOG},
              runs=runs, listing=_listing(conclusion="cancelled"))
    kw.update(over)
    return _GitHubOnce(**kw)


class TestACanceledVerifyJob:
    """The Verify run is both a run holding a canceled check and the Verify
    run: it is re-run once, and named once, as the Verify run."""

    def test_each_run_is_re_run_once_and_noted_once(self):
        board, github = _Board(), _verify_canceled_github()
        posted = board.sweep(_pr(), github)

        assert github.dispatched == _reruns(CI_RUN, VERIFY_RUN)
        assert reconcile._write_failures == []
        notes = _notes(posted)
        assert len(notes) == 1, posted
        note = notes[0]
        assert note.count(str(VERIFY_RUN)) == 1, note
        assert f"the Verify run {VERIFY_RUN}" in note
        assert f"(run {VERIFY_RUN})" not in note
        assert f"(run {CI_RUN})" in note
        assert _questions(posted) == []
        assert board.moves == [] and board.pr_notes == []

    def test_the_second_pass_reads_both_runs_back(self):
        board, github = _Board(), _verify_canceled_github()
        pr = _pr()
        board.sweep(pr, github)
        assert reconcile._cap_rerun_noted(board.card(), HEAD) == ([CI_RUN], VERIFY_RUN)

        still = _verify_canceled_github(
            runs={CI_RUN: _run(CI_RUN, attempt=4),
                  VERIFY_RUN: _run(VERIFY_RUN, conclusion="cancelled", attempt=4)},
            listing=_listing(conclusion="cancelled", attempt=4))
        posted = board.sweep(pr, still)
        assert still.dispatched == []
        assert reconcile._write_failures == []
        questions = _questions(posted)
        assert len(questions) == 1, posted
        finding = _finding(questions[0])
        for name in ("Console backend shard 3", "Console backend (pytest)",
                     f"the Verify run {VERIFY_RUN}"):
            assert name in finding, finding
        assert finding.count(str(VERIFY_RUN)) == 1, finding


# --------------------------------------------------------------------------- #
# replay test 2: a failed test in a log is red, and the question goes          #
# --------------------------------------------------------------------------- #


class TestReplayTwoAFailedTestAsks:
    def test_a_failed_shard_reruns_nothing_and_parks(self):
        checks = _checks()
        checks[3] = _check(9004, "Console backend shard 2", "failure",
                           "2026-11-03T22:07:11Z")
        logs = {**_logs(), 9004: SHARD_2_FAILED_LOG}
        board, github = _Board(), _GitHub(checks=checks, logs=logs)
        posted = board.sweep(_pr(), github)

        assert github.dispatched == []
        assert _notes(posted) == []
        assert reconcile.HOLD_LABEL in board.labels
        assert len(_questions(posted)) == 1, posted
        assert "re-run" not in _finding(_questions(posted)[0])
        assert len(board.pr_notes) == 1
        assert board.state == "Green Light"


# --------------------------------------------------------------------------- #
# replay test 3: re-run once, still not green, and the question names them     #
# --------------------------------------------------------------------------- #


class TestReplayThreeStillRedAfterTheRerunAsks:
    def test_the_question_names_each_job_re_run_once(self):
        board = _Board(extra=[{"body": _first_pass_note(),
                               "createdAt": "2026-11-04T04:49:00Z"}])
        github = _GitHub(runs={CI_RUN: _run(CI_RUN, attempt=4),
                               VERIFY_RUN: _run(VERIFY_RUN, attempt=4)},
                         listing=_listing(attempt=4))
        posted = board.sweep(_pr(), github)

        assert github.dispatched == []
        assert _notes(posted) == []
        assert reconcile.HOLD_LABEL in board.labels
        questions = _questions(posted)
        assert len(questions) == 1, posted
        finding = _finding(questions[0])
        for name in ("Console backend shard 3", "Console backend (pytest)",
                     f"Verify run {VERIFY_RUN}"):
            assert name in finding, finding
        assert "re-run once at the cap" in finding, finding
        assert len(board.pr_notes) == 1
        assert _finding(board.pr_notes[0]) == finding
        assert board.state == "Green Light"


# --------------------------------------------------------------------------- #
# the cases that stay red, and the guards                                      #
# --------------------------------------------------------------------------- #


def _asked(board, github, posted):
    assert github.dispatched == []
    assert _notes(posted) == []
    assert len(_questions(posted)) == 1, posted
    assert board.state == "Green Light"


class TestWhatStaysRed:
    def test_an_unreadable_log_asks(self):
        board, github = _Board(), _GitHub(logs={9002: SUMMARY_LOG})
        _asked(board, github, board.sweep(_pr(), github))

    def test_a_timed_out_check_asks(self):
        checks = _checks()
        checks[2] = _check(9003, "Console backend shard 1", "timed_out",
                           "2026-11-03T22:09:30Z")
        board, github = _Board(), _GitHub(checks=checks)
        _asked(board, github, board.sweep(_pr(), github))

    def test_a_red_check_outside_the_verify_run_asks(self):
        checks = _checks() + [_check(9100, "lint", "failure", "2026-11-03T22:03:00Z",
                                     run_id=41708617000)]
        board, github = _Board(), _GitHub(checks=checks)
        _asked(board, github, board.sweep(_pr(), github))

    def test_a_verifier_fail_at_the_head_asks(self):
        board, github = _Board(), _GitHub()
        pr = _pr(_qa(f"🧪 QA Verifier — VERDICT: FAIL @{HEAD}\n\nno"))
        _asked(board, github, board.sweep(pr, github))

    def test_a_verify_run_with_no_listing_asks(self):
        board, github = _Board(), _GitHub(listing=[])
        _asked(board, github, board.sweep(_pr(), github))

    def test_a_proof_record_is_left_to_the_operator(self):
        board, github = _Board(), _GitHub()
        pr = _pr()
        pr["headRefName"] = "agent/DRE-9005-proof-record"
        posted = board.sweep(pr, github)
        assert github.dispatched == []
        assert _notes(posted) == []
        assert len(_questions(posted)) == 1, posted
        assert board.state == reconcile.REVIEW_LANE  # held for the operator

    def test_the_review_budget_is_not_this_path(self):
        # No critic verdict bound to the head: the review's budget, not the
        # gate's, and the question goes as today.
        board = _Board()
        for c in board.comments:
            c["body"] = c["body"].replace(reconcile.GATE_NUDGE_KEY,
                                          reconcile.REVIEW_NUDGE_KEY)
        github = _GitHub()
        pr = _pr()
        pr["comments"] = []
        _asked(board, github, board.sweep(pr, github))


class TestTheGuards:
    def test_a_verify_run_still_going_writes_nothing(self):
        board, github = _Board(), _GitHub(listing=_listing(status="in_progress"))
        posted = board.sweep(_pr(), github)
        assert posted == [] and github.dispatched == []
        assert board.moves == [] and board.pr_notes == []

    def test_a_ci_run_still_going_writes_nothing(self):
        runs = _runs()
        runs[CI_RUN] = _run(CI_RUN, status="in_progress")
        board, github = _Board(), _GitHub(runs=runs)
        posted = board.sweep(_pr(), github)
        assert posted == [] and github.dispatched == []
        assert board.moves == [] and board.pr_notes == []

    def _held(self, hours_ago):
        # Read against the sweep's own clock, which is the wall clock.
        now = datetime.now(timezone.utc)
        at = (now - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
        return _worker(canceled_check.held_receipt(
            NUMBER, HEAD, CI_RUN, "a job hung on its runner", now), at=at)

    def test_a_fresh_held_receipt_writes_nothing(self):
        board, github = _Board(), _GitHub()
        posted = board.sweep(_pr(self._held(1)), github)
        assert posted == [] and github.dispatched == []
        assert board.moves == [] and board.pr_notes == []

    def test_an_expired_held_receipt_asks(self):
        board, github = _Board(), _GitHub()
        posted = board.sweep(_pr(self._held(canceled_check.HELD_HOURS + 1)), github)
        _asked(board, github, posted)

    def test_a_refused_rerun_is_recorded_and_the_other_is_noted(self):
        board, github = _Board(), _GitHub()
        github.refuse = {str(VERIFY_RUN)}
        before = len(board.comments)
        with pytest.raises(SystemExit):  # a refused write turns the sweep red
            board.sweep(_pr(), github)
        posted = [c["body"] for c in board.comments[before:]]
        assert github.dispatched == _reruns(CI_RUN)
        assert any(str(VERIFY_RUN) in f for f in reconcile._write_failures)
        notes = _notes(posted)
        assert len(notes) == 1
        assert f"run {CI_RUN}" in notes[0] and str(VERIFY_RUN) not in notes[0]
        assert board.moves == [] and board.pr_notes == []

    def test_every_rerun_refused_asks_as_today(self):
        board, github = _Board(), _GitHub()
        github.refuse = {str(CI_RUN), str(VERIFY_RUN)}
        before = len(board.comments)
        with pytest.raises(SystemExit):  # a refused write turns the sweep red
            board.sweep(_pr(), github)
        posted = [c["body"] for c in board.comments[before:]]
        assert github.dispatched == []
        assert len(reconcile._write_failures) == 2
        assert _notes(posted) == []
        assert len(_questions(posted)) == 1
        assert "re-run" not in _finding(_questions(posted)[0])
        assert board.state == "Green Light"


# --------------------------------------------------------------------------- #
# the Finding line                                                             #
# --------------------------------------------------------------------------- #


def _evidence(**over):
    base = dict(card=IDENT, pr_number=NUMBER, head=HEAD,
                tag=review_cap_question.GATE_NUDGE_KEY, critic="APPROVE",
                verifier="none", spent=3, hours=6.3, gate_note_line=None, cap=3)
    base.update(over)
    return base


class TestTheFinding:
    def test_no_rerun_reads_as_before(self):
        assert review_cap_question.compose(**_evidence()) == \
            review_cap_question.compose(**_evidence(rerun_jobs=()))

    def test_the_jobs_are_named_and_re_run_once(self):
        jobs = ("Console backend shard 3", "Console backend (pytest)",
                f"the Verify run {VERIFY_RUN}")
        for text in (review_cap_question.compose(**_evidence(rerun_jobs=jobs)),
                     review_cap_question.pr_blocker(**_evidence(rerun_jobs=jobs))):
            finding = _finding(text)
            for job in jobs:
                assert job in finding
            assert "re-run once at the cap" in finding
            assert console_escalation.problems(text) == []


# --------------------------------------------------------------------------- #
# the registry                                                                 #
# --------------------------------------------------------------------------- #


def test_the_note_is_declared_unconverted():
    registry = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
    rows = [r for r in registry["unconverted"]
            if r["file"] == "scripts/reconcile.py"
            and "REVIEW_NUDGE_CAP_RERUN_KEY" in r["anchor"]]
    assert len(rows) == 1
    assert rows[0]["kind"] == "undeclared-act"
    source = (ROOT / "scripts" / "reconcile.py").read_text()
    assert rows[0]["anchor"] in source
