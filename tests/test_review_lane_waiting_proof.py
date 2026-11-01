"""RED-first: the review lane's dead-run requeue leaves a waiting proof in its
lane (DRE-8438).

THE GAP: in the sweep's nudge loop the arm `state == REVIEW_LANE and not
is_open` parks an `In Review` card with no open pull request, idle past
`STALE_MINUTES["In Review"]`, in `Queued` under a `dead-run-requeue` receipt
(DRE-2034, DRE-7108). Only `held(card)` reads a hold there, and it reads the
`needs-human` label, never a `🔬 proof-waiting` comment. Today a `PROOF:` card
is kept off that arm by `hand_built` alone — a rule about who starts a proof's
run, which says nothing of what the proof waits on — and the sweep logs it as
`hand-built:`. Once the proof run and the sweep close a record pull request
while its proof waits, that one rule is all that stands between every waiting
proof in `In Review` and a requeue about two hours later.

FIX UNDER TEST — `waiting_proof_off_pr(card, pr)`, a guard ahead of that arm
and of the hand-built skip, for an `In Review` card with no open pull request.
It answers the hold's first line, cut to 120, when the card is a proof's, `pr`
is CLOSED and unmerged on a proof-record branch, and the card carries an open
`🔬 proof-waiting` hold — read the way `_proof_wait_clock` reads them: the
board read's comment window, or the whole thread once when the window is
partial. The sweep then prints one `proof-waiting:` line and writes nothing. A
thread that cannot be read raises `ReconcileReadError`: nothing is done to the
card and the pass ends red. Every other card is judged exactly as before — a
proof card the guard does not answer for is still left alone as hand-built,
and any other card is still requeued.

Run: cd bureau-pipeline && python3 -m pytest tests/test_review_lane_waiting_proof.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import proof_record  # noqa: E402
import reconcile  # noqa: E402

IDENT = "DRE-123"
RECORD = f"agent/{IDENT}-proof-record"
HOLD = ("🔬 proof-waiting: the nightly on main has not run since the record "
        "was written — needs the 02:10 PT nightly to run green on main")
OBSERVED = "🔬 proof-observed: the nightly ran green on main at 02:10 PT"


@pytest.fixture(autouse=True)
def _clean_failure_state(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()
    yield
    reconcile._write_failures.clear()
    reconcile._read_failures.clear()


def _iso(hours_ago: float) -> str:
    return ((datetime.now(UTC) - timedelta(hours=hours_ago))
            .strftime("%Y-%m-%dT%H:%M:%S.000Z"))


def _node(body: str, hours_ago: float) -> dict:
    """A comment as Linear returns it — a person's, so a `proof-observed`
    discharges, as `open_holds_at` reads it."""
    return {"body": body, "createdAt": _iso(hours_ago), "user": {"id": "someone"}}


def _card(*, title=f"PROOF: {IDENT} proves the nightly", nodes=None,
          partial=False) -> dict:
    """The replay's card: In Review, idle three hours, its newest comment an
    open hold. `nodes` are oldest first; the window is newest first."""
    nodes = [_node(HOLD, 3.0)] if nodes is None else nodes
    return {
        "identifier": IDENT,
        "title": title,
        "description": "**Repo:** agent-bureau\nproof",
        "state": {"name": "In Review"},
        "labels": {"nodes": []},
        "updatedAt": _iso(3.0),
        "comments": {"nodes": list(reversed(nodes)),
                     "pageInfo": {"hasNextPage": partial}},
    }


def _pr(*, head=RECORD, state="CLOSED", number=77) -> dict:
    """The newest pull request `pr_for` finds for the card."""
    return {"number": number, "headRefName": head, "state": state,
            "comments": [], "headRefOid": "a" * 40, "baseRefName": "main",
            "isDraft": False}


class Sweep:
    """One `main()` pass over the one card, with every other phase stubbed —
    the same stubs `test_review_lane_requeue_cap` runs the arm under."""

    def __init__(self, card, pr, *, thread=None, dead=0):
        self.card, self.pr, self.thread, self.dead = card, pr, thread, dead
        self.thread_reads: list[str] = []
        self.exit = None

    def _thread_and_viewer(self, ident, *_needs, whole=False):
        self.thread_reads.append(ident)
        if isinstance(self.thread, Exception):
            raise self.thread
        return list(self.thread or []), "viewer"

    def run(self, capsys):
        mocks = {
            "unstick_conflicts": MagicMock(),
            "retrigger_dead_heads": MagicMock(),
            "check_dependabot_capacity": MagicMock(),
            "fix_approved_but_red": MagicMock(),
            "close_finished_epics": MagicMock(),
            "promote_ready": MagicMock(return_value=0),
            "redispatch": MagicMock(return_value=True),
            "active_cards": MagicMock(return_value=[self.card]),
            "flag_stranded": MagicMock(return_value=set()),
            "alarm_held_proofs": MagicMock(),
            "pr_for": MagicMock(return_value=self.pr),
        }
        with patch.multiple(reconcile, **mocks), patch.object(
            reconcile.linear_ops, "count_comments", return_value=self.dead
        ) as self.count_comments, patch.object(
            reconcile.linear_ops, "add_label"
        ) as self.add_label, patch.object(
            reconcile.linear_ops, "cmd_state"
        ) as self.cmd_state, patch.object(
            reconcile.linear_ops, "cmd_advance"
        ) as self.cmd_advance, patch.object(
            reconcile.linear_ops, "cmd_comment"
        ) as self.cmd_comment, patch.object(
            reconcile.linear_ops, "_thread_and_viewer",
            side_effect=self._thread_and_viewer,
        ):
            try:
                reconcile.main()
            except SystemExit as e:
                self.exit = e
        self.out = capsys.readouterr().out
        return self

    @property
    def dead_counted(self) -> bool:
        return any(reconcile.DEAD_TAG in c.args for c in self.count_comments.call_args_list)

    @property
    def bodies(self) -> list[str]:
        return [c.args[1] for c in self.cmd_comment.call_args_list]

    def assert_left_alone(self):
        self.cmd_state.assert_not_called()
        self.cmd_advance.assert_not_called()
        self.add_label.assert_not_called()
        assert self.bodies == [], "a waiting proof gets no comment"
        assert not self.dead_counted, "no dead run is counted"

    def assert_requeued(self):
        self.cmd_state.assert_called_once_with(IDENT, reconcile.QUEUE_LANE, "--park")
        assert any(f"🪦 {reconcile.DEAD_TAG}:" in b for b in self.bodies), (
            "the requeue carries the dead-run receipt, as today")
        assert "proof-waiting:" not in self.out

    def assert_left_as_hand_built(self):
        """A proof card the guard does not answer for: left alone by the
        hand-built skip, as today — the guard never claims it."""
        self.assert_left_alone()
        assert f"hand-built: {IDENT} in In Review with no PR" in self.out
        assert "proof-waiting:" not in self.out
        assert self.exit is None, self.exit


# --------------------------------------------------------------------------- #
# the replay                                                                   #
# --------------------------------------------------------------------------- #

def test_the_replay_a_waiting_proof_keeps_its_lane(capsys):
    """The acceptance replay: a `PROOF:` card in `In Review`, idle three
    hours, its newest pull request closed and unmerged on its record branch,
    its newest comment an open hold. On main it is requeued to `Queued`."""
    sweep = Sweep(_card(), _pr()).run(capsys)
    sweep.assert_left_alone()
    assert sweep.exit is None, sweep.exit
    line = (f"proof-waiting: {IDENT} in In Review with its record pull request "
            f"#77 closed — left to the proof dispatcher; held by "
            f"{proof_record._cut(HOLD, 120)}")
    assert line in sweep.out
    assert "hand-built:" not in sweep.out, "the guard, not the hand-built rule, keeps it"


def test_the_guard_holds_on_its_own_without_the_hand_built_rule(capsys):
    """The waiting proof keeps its lane because of its hold, whatever
    `hand_built` answers: with that rule out of the way the card is still not
    requeued, and no dead run is counted."""
    with patch.object(reconcile, "hand_built", return_value=False):
        sweep = Sweep(_card(), _pr(), dead=reconcile.REQUEUE_CAP).run(capsys)
    sweep.assert_left_alone()
    assert f"proof-waiting: {IDENT}" in sweep.out


def test_a_partial_window_is_judged_on_the_whole_thread_read_once(capsys):
    """The board read's window holds only newer comments: the hold lies
    beyond it, so the whole thread is read — once — and the hold found."""
    newer = _node("⏳ heartbeat", 1.0)
    card = _card(nodes=[newer], partial=True)
    sweep = Sweep(card, _pr(), thread=[_node(HOLD, 3.0), newer]).run(capsys)
    sweep.assert_left_alone()
    assert sweep.thread_reads == [IDENT]


# --------------------------------------------------------------------------- #
# judged exactly as before                                                     #
# --------------------------------------------------------------------------- #

def test_a_card_not_titled_as_a_proof_is_requeued_as_today(capsys):
    """The guard's whole effect on any other card is none: the same closed
    record-shaped pull request and the same hold on a card that is not a
    proof's, and it is requeued under the dead-run receipt."""
    Sweep(_card(title=f"{IDENT} fixes a thing"), _pr()).run(capsys).assert_requeued()


def test_a_discharged_hold_is_judged_as_today(capsys):
    """A later `🔬 proof-observed` discharges the hold: the guard does not
    answer, and the card is judged as today — the hand-built skip."""
    card = _card(nodes=[_node(HOLD, 4.0), _node(OBSERVED, 3.0)])
    Sweep(card, _pr()).run(capsys).assert_left_as_hand_built()


def test_a_discharged_hold_without_the_hand_built_rule_is_requeued(capsys):
    """And with the hand-built rule out of the way, the dead-run arm
    requeues it — the guard never answers for a proof that waits on
    nothing."""
    card = _card(nodes=[_node(HOLD, 4.0), _node(OBSERVED, 3.0)])
    with patch.object(reconcile, "hand_built", return_value=False):
        Sweep(card, _pr()).run(capsys).assert_requeued()


def test_a_closed_pull_request_on_another_branch_is_judged_as_today(capsys):
    pr = _pr(head=f"agent/{IDENT}-proof-nightly")
    Sweep(_card(), pr).run(capsys).assert_left_as_hand_built()


def test_another_branch_without_the_hand_built_rule_is_requeued(capsys):
    pr = _pr(head=f"agent/{IDENT}-proof-nightly")
    with patch.object(reconcile, "hand_built", return_value=False):
        Sweep(_card(), pr).run(capsys).assert_requeued()


def test_a_proof_with_no_pull_request_at_all_is_judged_as_today(capsys):
    """No record pull request to have been closed: the guard does not answer."""
    Sweep(_card(), None).run(capsys).assert_left_as_hand_built()


# --------------------------------------------------------------------------- #
# an unread thread                                                             #
# --------------------------------------------------------------------------- #

def test_an_unread_thread_moves_nothing_and_ends_the_pass_red(capsys):
    """DRE-2034's rule: an unread answer is not an empty one. The card is
    neither moved nor commented, and the run exits red."""
    card = _card(nodes=[_node("⏳ heartbeat", 1.0)], partial=True)
    sweep = Sweep(card, _pr(), thread=RuntimeError("HTTP 502")).run(capsys)
    sweep.assert_left_alone()
    assert sweep.exit is not None, "the pass ends red"
    assert any(IDENT in f for f in reconcile._read_failures)


# --------------------------------------------------------------------------- #
# the guard on its own                                                         #
# --------------------------------------------------------------------------- #

def test_the_guard_answers_the_holds_first_line_cut_to_120():
    long_hold = "🔬 proof-waiting: " + "the record waits on a long thing " * 8
    card = _card(nodes=[_node(long_hold + "\nmore detail", 3.0)])
    line = reconcile.waiting_proof_off_pr(card, _pr())
    assert line == proof_record._cut(long_hold, 120)
    assert len(line) <= 120


@pytest.mark.parametrize("pr", [None, _pr(state="OPEN"), _pr(state="MERGED")],
                         ids=["no-pr", "open", "merged"])
def test_the_guard_answers_none_for_a_pull_request_that_is_not_closed(pr):
    """Judged before the thread: a partial window buys no read."""
    with patch.object(reconcile.linear_ops, "_thread_and_viewer") as thread:
        assert reconcile.waiting_proof_off_pr(_card(partial=True), pr) is None
    thread.assert_not_called()


def test_the_guard_reads_no_thread_for_a_card_that_is_not_a_proof():
    """The title is judged first: a partial window on a non-proof card buys
    no thread read."""
    card = _card(title=f"{IDENT} fixes a thing", partial=True)
    with patch.object(reconcile.linear_ops, "_thread_and_viewer") as thread:
        assert reconcile.waiting_proof_off_pr(card, _pr()) is None
    thread.assert_not_called()


def test_the_guard_raises_the_read_error_on_an_unread_thread():
    card = _card(nodes=[], partial=True)
    with patch.object(reconcile.linear_ops, "_thread_and_viewer",
                      side_effect=RuntimeError("HTTP 502")):
        with pytest.raises(reconcile.ReconcileReadError):
            reconcile.waiting_proof_off_pr(card, _pr())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
