"""RED-first tests: the sweep closes a medic stop the card has already moved on from (DRE-9283).

The week ending 2026-11-07 counted 170 medic stops standing still for 1,488.3
hours (DRE-9269), every one read as waiting on the operator. The stop is the
medic's diagnosis, `run-failure-diagnosed`, a `hold` nothing in the registry
discharged. The medic had already acted on most of them — re-ran 8, sent 6 to
Planning, named the fix loop for 107 — and a merge or a sweep had carried 154
of them on. The stop outlived the recovery it was recording.

`scripts/medic_discharge.py` holds the decision, pure, over a card's thread,
and `reconcile.discharge_medic_stops` does the reads and posts one
`medic-stop-discharged` receipt per stop it closes, whose trailer says
`discharges: run-failure-diagnosed`. The rules, first that applies decides:

  1. a `medic-stop-discharged` receipt with the same `run=` — nothing written;
  2. `step=ask-person` — keeps waiting, whatever happens to the card after;
  3. a first line ending ` moved=no` — keeps waiting;
  4. `step=requeue-once` — `because=re-ran`;
  5. `step=send-to-planning` — `because=sent-to-planning`;
  6. `step=fix-path` — only on a carrier after the receipt: `merged`, then
     `new-head`, then `sweep-dispatched`.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "ghs_app")

import linear_ops  # noqa: E402
import medic_discharge  # noqa: E402
import medic_next_step  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402

CARD = "DRE-9259"
SLUG = "bureau-pipeline"
REPO = f"dreadnought-foundry/{SLUG}"
RUN = "19283746"

#: The real board read, kept before `conftest.py` stubs it for every test.
_REAL_CARDS = reconcile.medic_discharge_cards

BEFORE = "2026-11-10T17:00:00.000Z"
RECEIPT_AT = "2026-11-10T18:00:00.000Z"
AFTER = "2026-11-10T19:30:00.000Z"
LATER = "2026-11-10T20:00:00.000Z"

DIAGNOSIS = pipeline_act.receipt(
    "run-failure-diagnosed",
    "## What failed\n\nThe build run failed twice; the medic read it as bad code.")


def _receipt(step, *, cls=None, run=RUN, moved=True):
    cls = cls or {"requeue-once": "transient-infrastructure",
                  "send-to-planning": "bad-card",
                  "fix-path": "bad-code",
                  "ask-person": "platform-fault"}[step]
    body = medic_next_step.receipt(
        medic_next_step.NextStep(cls, step, "a reason"),
        run_url=f"https://github.com/{REPO}/actions/runs/{run}",
        finding="The run failed.", moved=moved, lane="In Progress",
        moved_to="Green Light" if step == "ask-person" else None)
    return pipeline_act.receipt("medic-next-step", body)


def _c(body, at):
    return {"body": body, "createdAt": at}


def _thread(step, *, moved=True, run=RUN, extra=()):
    return [_c(DIAGNOSIS, BEFORE), _c(_receipt(step, moved=moved, run=run), RECEIPT_AT),
            *extra]


def _stop(thread, card=CARD):
    stops = medic_discharge.stops(card, thread)
    assert len(stops) == 1, stops
    return stops[0]


def _pr(state="OPEN", merged_at=None, committed=BEFORE, comments=(), number=77):
    return {"number": number, "state": state, "mergedAt": merged_at,
            "url": f"https://github.com/{REPO}/pull/{number}",
            "headRefName": f"agent/{CARD}-x",
            "commits": [{"oid": "a" * 40, "committedDate": "2026-11-09T10:00:00Z"},
                        {"oid": "b" * 40, "committedDate": committed}],
            "comments": list(comments)}


def _decide(thread, pr=None):
    return medic_discharge.decide(_stop(thread), thread, pr=pr)


def _composed(found):
    """The comment the sweep posts for `found`."""
    return pipeline_act.receipt(medic_discharge.ACT, medic_discharge.body(found))


def _trailered(act, at, text="the sweep acted"):
    return _c(pipeline_act.receipt(act, text), at)


# --------------------------------------------------------------------------- #
# 1. the decision                                                              #
# --------------------------------------------------------------------------- #


class TestTheMedicsOwnStep:
    def test_requeue_once_discharges_as_re_ran(self):
        found = _decide(_thread("requeue-once"))
        assert found is not None and found.because == "re-ran"

    def test_send_to_planning_discharges_as_sent_to_planning(self):
        found = _decide(_thread("send-to-planning"))
        assert found is not None and found.because == "sent-to-planning"

    @pytest.mark.parametrize("step", ["requeue-once", "send-to-planning"])
    def test_a_step_that_did_not_land_keeps_waiting(self, step):
        thread = _thread(step, moved=False)
        assert medic_next_step.parse_receipt(thread[1]["body"])["moved"] is False
        assert _decide(thread) is None

    def test_neither_reads_the_pull_request(self):
        for step in ("requeue-once", "send-to-planning"):
            thread = _thread(step)
            assert not medic_discharge.needs_pull_request(_stop(thread), thread)


class TestAskPerson:
    def test_ask_person_never_discharges_even_after_a_merge(self):
        """Rule 2 comes before any moved or carrier test: the receipt has no
        ` moved=no`, and the card's pull request merged after it."""
        thread = _thread("ask-person")
        assert not thread[1]["body"].split("\n", 1)[0].endswith(" moved=no")
        assert medic_next_step.parse_receipt(thread[1]["body"])["moved"] is True
        pr = _pr(state="MERGED", merged_at=AFTER, committed=AFTER,
                 comments=[_trailered("fix-loop-restarted", AFTER)])
        assert _decide(thread, pr) is None

    def test_ask_person_is_refused_by_its_own_rule(self, monkeypatch):
        """Rule 2 is what refuses it: with the step's own rule out of the way,
        the same merge would close the stop as it closes a fix-path one."""
        thread = _thread("ask-person")
        pr = _pr(state="MERGED", merged_at=AFTER)
        monkeypatch.setattr(medic_discharge, "ASK_PERSON", "no-such-step")
        assert _decide(thread, pr).because == "merged"

    def test_ask_person_reads_no_pull_request(self):
        thread = _thread("ask-person")
        assert not medic_discharge.needs_pull_request(_stop(thread), thread)


class TestNoConclusion:
    def test_a_diagnosis_with_no_receipt_is_not_a_stop(self):
        assert pipeline_act.read_trailer(DIAGNOSIS)["act"] == "run-failure-diagnosed"
        assert medic_discharge.stops(CARD, [_c(DIAGNOSIS, BEFORE)]) == []

    def test_a_step_the_reader_does_not_know_keeps_waiting(self):
        body = (f"🩺 medic-next-step: class=bad-code step=some-new-step run={RUN}\n\n"
                "A step this reader was never taught.")
        thread = [_c(DIAGNOSIS, BEFORE), _c(body, RECEIPT_AT)]
        assert _decide(thread, _pr(state="MERGED", merged_at=AFTER)) is None

    def test_a_receipt_quoted_past_the_first_line_is_not_a_stop(self):
        quoted = "Somebody wrote:\n" + _receipt("requeue-once")
        assert medic_discharge.stops(CARD, [_c(quoted, RECEIPT_AT)]) == []


class TestFixPath:
    def test_parse_receipt_reads_moved_for_every_fix_path(self):
        thread = _thread("fix-path", moved=False)
        assert medic_next_step.parse_receipt(thread[1]["body"])["moved"] is True

    def test_a_fix_path_with_no_carrier_keeps_waiting(self):
        """`moved: True` alone never closes a fix-path stop."""
        thread = _thread("fix-path")
        assert medic_discharge.needs_pull_request(_stop(thread), thread)
        assert _decide(thread, _pr()) is None
        assert _decide(thread, None) is None

    def test_merged_after_the_receipt(self):
        found = _decide(_thread("fix-path"), _pr(state="MERGED", merged_at=AFTER))
        assert found is not None and found.because == "merged"
        assert found.pr_number == 77 and found.at == AFTER

    def test_merged_before_the_receipt_writes_nothing(self):
        assert _decide(_thread("fix-path"), _pr(state="MERGED", merged_at=BEFORE)) is None

    def test_a_new_head_after_the_receipt(self):
        found = _decide(_thread("fix-path"), _pr(committed=AFTER))
        assert found is not None and found.because == "new-head"
        assert found.pr_number == 77 and found.at == AFTER

    def test_a_head_from_before_the_receipt_writes_nothing(self):
        assert _decide(_thread("fix-path"), _pr(committed=BEFORE)) is None

    def test_the_newest_commit_is_the_last_one_listed(self):
        """`gh pr view --json commits` lists oldest first, so the last is the
        head. A list in the other order reads its oldest commit as the head:
        the stop keeps waiting rather than being closed on a commit that
        came before the receipt."""
        pr = _pr(committed=AFTER)
        assert _decide(_thread("fix-path"), pr).because == "new-head"
        pr["commits"].reverse()
        assert _decide(_thread("fix-path"), pr) is None

    def test_a_new_head_on_a_closed_pull_request_is_not_a_carrier(self):
        assert _decide(_thread("fix-path"), _pr(state="CLOSED", committed=AFTER)) is None

    @pytest.mark.parametrize("act", ["fix-loop-restarted", "merge-ref-refreshed"])
    def test_a_sweep_dispatch_on_the_card_after_the_receipt(self, act):
        thread = _thread("fix-path", extra=[_trailered(act, AFTER)])
        found = _decide(thread, _pr())
        assert found is not None and found.because == "sweep-dispatched"
        assert found.act == act

    def test_a_sweep_dispatch_on_the_pull_request_after_the_receipt(self):
        pr = _pr(comments=[_trailered("merge-ref-refreshed", AFTER)])
        found = _decide(_thread("fix-path"), pr)
        assert found is not None and found.because == "sweep-dispatched"

    def test_a_sweep_dispatch_before_the_receipt_writes_nothing(self):
        thread = _thread("fix-path")
        thread.insert(1, _trailered("fix-loop-restarted", BEFORE))
        pr = _pr(comments=[_trailered("merge-ref-refreshed", BEFORE)])
        assert _decide(thread, pr) is None

    @pytest.mark.parametrize("act", ["fix-attempt-landed", "medic-next-step",
                                     "inherited-red-main-returned"])
    def test_an_act_that_is_not_a_sweep_dispatch_is_not_a_carrier(self, act):
        thread = _thread("fix-path", extra=[_trailered(act, AFTER)])
        assert _decide(thread, _pr()) is None

    def test_merged_names_the_condition_before_new_head_and_sweep(self):
        thread = _thread("fix-path", extra=[_trailered("fix-loop-restarted", AFTER)])
        pr = _pr(state="MERGED", merged_at=LATER, committed=AFTER)
        assert _decide(thread, pr).because == "merged"

    def test_new_head_names_the_condition_before_sweep(self):
        thread = _thread("fix-path", extra=[_trailered("fix-loop-restarted", AFTER)])
        assert _decide(thread, _pr(committed=LATER)).because == "new-head"


class TestTheSweepDispatchSet:
    def test_the_set_is_read_off_the_registry(self):
        names = medic_discharge.sweep_dispatch_acts()
        expected = {r["name"] for r in pipeline_act.rows()
                    if r["state"] == "dispatched"
                    and r["emits"]["file"] in ("scripts/reconcile.py",
                                               "scripts/stale_merge_ref.py")}
        assert names == expected
        assert {"fix-loop-restarted", "merge-ref-refreshed"} <= names
        assert "fix-attempt-landed" not in names

    def test_it_follows_the_registry_rather_than_a_list(self):
        rows = [{"name": "made-up-dispatch", "state": "dispatched",
                 "emits": {"file": "scripts/reconcile.py"}},
                {"name": "made-up-hold", "state": "held",
                 "emits": {"file": "scripts/reconcile.py"}}]
        assert medic_discharge.sweep_dispatch_acts(rows) == {"made-up-dispatch"}

    def test_no_act_name_is_listed_in_the_module(self):
        source = (ROOT / "scripts" / "medic_discharge.py").read_text(encoding="utf-8")
        for name in ("fix-loop-restarted", "merge-ref-refreshed",
                     "review-retried-after-crash", "epic-queue-started"):
            assert name not in source


class TestAlreadyDischarged:
    def _discharged(self, run=RUN):
        stop = medic_discharge.Stop(CARD, run, "requeue-once",
                                    "transient-infrastructure", True, RECEIPT_AT)
        body = _composed(medic_discharge.Discharge(stop, "re-ran"))
        return _c(body, AFTER)

    def test_a_run_with_a_discharge_is_not_discharged_again(self):
        thread = _thread("requeue-once", extra=[self._discharged()])
        assert _decide(thread) is None

    def test_rule_one_comes_first_for_a_fix_path(self):
        thread = _thread("fix-path", extra=[self._discharged()])
        assert not medic_discharge.needs_pull_request(_stop(thread), thread)
        assert _decide(thread, _pr(state="MERGED", merged_at=AFTER)) is None

    def test_another_runs_discharge_does_not_count(self):
        thread = _thread("requeue-once", extra=[self._discharged(run="555")])
        assert _decide(thread).because == "re-ran"

    def test_two_receipts_for_two_runs_are_two_stops(self):
        thread = [*_thread("requeue-once", run="1"),
                  _c(_receipt("send-to-planning", run="2"), AFTER)]
        assert [s.run for s in medic_discharge.stops(CARD, thread)] == ["1", "2"]

    def test_a_repeated_run_is_one_stop(self):
        thread = [*_thread("requeue-once"), _c(_receipt("requeue-once"), AFTER)]
        stops = medic_discharge.stops(CARD, thread)
        assert len(stops) == 1 and stops[0].created_at == RECEIPT_AT


# --------------------------------------------------------------------------- #
# 2. the receipt                                                               #
# --------------------------------------------------------------------------- #


class TestTheReceipt:
    @pytest.mark.parametrize("step,pr,because", [
        ("requeue-once", None, "re-ran"),
        ("send-to-planning", None, "sent-to-planning"),
        ("fix-path", _pr(state="MERGED", merged_at=AFTER), "merged"),
        ("fix-path", _pr(committed=AFTER), "new-head"),
        ("fix-path", _pr(comments=[_trailered("fix-loop-restarted", AFTER)]),
         "sweep-dispatched"),
    ])
    def test_the_first_line_and_the_trailer(self, step, pr, because):
        found = _decide(_thread(step), pr)
        body = _composed(found)
        first = body.split("\n", 1)[0]
        assert first == f"🩺 medic-stop-discharged: because={because} step={step} run={RUN}"
        assert body.rstrip("\n").splitlines()[-1] == (
            "📎 pipeline-act: medic-stop-discharged · kind: recovery · "
            "state: unchanged · next: reconcile.py · discharges: run-failure-diagnosed · "
            "subscriber: reconcile.yml · tag: medic-stop-discharged")
        trailer = pipeline_act.read_trailer(body)
        assert trailer["discharges"] == "run-failure-diagnosed"
        # One plain-English sentence between the two.
        middle = body.split("\n\n")[1]
        assert middle.strip() and "\n" not in middle.strip()

    def test_merged_and_new_head_name_the_pull_request_and_the_time_in_pt(self):
        for pr in (_pr(state="MERGED", merged_at=AFTER), _pr(committed=AFTER)):
            body = _composed(_decide(_thread("fix-path"), pr))
            assert "#77" in body
            # 19:30 UTC on 2026-11-10 is 11:30 AM PT.
            assert "11:30 AM PT" in body

    def test_the_receipt_is_not_a_medic_receipt(self):
        body = _composed(_decide(_thread("requeue-once")))
        assert medic_next_step.parse_receipt(body) is None
        assert medic_discharge.stops(CARD, [_c(body, AFTER)]) == []


class TestTheRegistryRow:
    def test_the_row(self):
        row = pipeline_act.record("medic-stop-discharged")
        assert row["tag"] == "medic-stop-discharged"
        assert row["adopted"] is True
        assert (row["kind"], row["state"], row["next_actor"]) == (
            "recovery", "unchanged", "reconcile.py")
        assert row["discharges"] == "run-failure-diagnosed"
        assert row["subscriber"] == "reconcile.yml"
        assert row["cadence_s"] is None and row["cadence_why"].strip()
        assert row["emits"]["file"] == "scripts/medic_discharge.py"
        assert medic_discharge.ACT == medic_discharge.RECEIPT_TAG == "medic-stop-discharged"

    def test_the_registry_checks_clean(self):
        assert pipeline_act.problems() == []

    def test_the_doc_carries_the_row(self):
        doc = (ROOT / "docs" / "pipeline-acts.md").read_text(encoding="utf-8")
        assert "medic-stop-discharged" in doc
        assert "discharges: run-failure-diagnosed" in doc or \
            "`run-failure-diagnosed`" in doc


# --------------------------------------------------------------------------- #
# 3. the sweep                                                                 #
# --------------------------------------------------------------------------- #


class _Board:
    """This repo's cards for one read, every comment write logged."""

    def __init__(self):
        self.cards: dict = {}
        self.posts: list = []
        self.queries: list = []
        self.lanes_asked: list = []
        self.whole_reads: list = []
        self.fail_comment = False

    def add(self, ident, thread, *, lane="In Review", labels=(f"repo:{SLUG}",),
            partial=False, seen_in=None, seen_thread=None):
        """`seen_in` is the lane the pass's board read, made at the top of
        the pass, caught the card in before it moved to `lane`; `seen_thread`
        the window it held then."""
        self.cards[ident] = {"lane": lane, "labels": list(labels),
                             "thread": list(thread), "partial": partial,
                             "seen_in": seen_in or lane,
                             "seen_thread": seen_thread}

    def _node(self, ident, *, seen=False):
        card = self.cards[ident]
        thread = card["seen_thread"] if seen and card["seen_thread"] is not None \
            else card["thread"]
        return {"id": f"id-{ident}", "identifier": ident,
                "state": {"name": card["seen_in" if seen else "lane"]},
                "labels": {"nodes": [{"name": n} for n in card["labels"]]},
                "comments": {"pageInfo": {"hasNextPage": card["partial"]},
                             "nodes": list(reversed(thread))}}

    def gql(self, query, variables=None, **_kw):
        """The queue and Done read: Linear applies the lanes it is asked and
        the completion window; every Done card here completed inside it."""
        self.queries.append((query, dict(variables or {})))
        if "issues(" in query:
            asked = [*variables["queue"], variables["done"]]
            return {"issues": {"nodes": [self._node(i) for i, c in self.cards.items()
                                         if c["lane"] in asked],
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        raise AssertionError(f"unexpected Linear query: {query[:80]}")

    def active_cards(self, states=()):
        """The pass's board read: every repo's cards, in the lanes asked, as
        that read saw them at the top of the pass."""
        self.lanes_asked.append(tuple(states))
        return [self._node(i, seen=True) for i, c in self.cards.items()
                if c["seen_in"] in states]

    def cmd_comment(self, ident, body, *flags):
        if self.fail_comment:
            raise linear_ops.LinearError("the board refused the comment")
        self.cards[ident]["thread"].append(_c(body, LATER))
        self.posts.append((ident, body))


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(reconcile, "medic_discharge_cards", _REAL_CARDS)
    monkeypatch.setattr(reconcile, "REPO", REPO)
    monkeypatch.setattr(reconcile, "REPO_SLUG", SLUG)
    for ledger in (reconcile._write_failures, reconcile._read_failures, reconcile._degraded):
        ledger.clear()
    b = _Board()
    monkeypatch.setattr(linear_ops, "gql", b.gql)
    monkeypatch.setattr(linear_ops, "cmd_comment", b.cmd_comment)
    monkeypatch.setattr(reconcile, "active_cards", b.active_cards)
    monkeypatch.setattr(reconcile, "_idle_pass", [])

    def no_whole_thread(identifier, whole_thread=False, **_kw):
        b.whole_reads.append(identifier)
        raise linear_ops.LinearError(f"{identifier}: the whole thread would not read")
    monkeypatch.setattr(linear_ops, "comment_timeline", no_whole_thread)
    b.prs = {}

    def pr_for(ident):
        found = b.prs.get(ident)
        if isinstance(found, Exception):
            raise found
        return found
    monkeypatch.setattr(reconcile, "pr_for", pr_for)

    def gh_read(*args):
        number = int(args[2])
        for pr in b.prs.values():
            if isinstance(pr, dict) and pr["number"] == number:
                return json.dumps({"mergedAt": pr["mergedAt"], "commits": pr["commits"]})
        raise reconcile.ReconcileReadError(f"no PR #{number}")
    monkeypatch.setattr(reconcile, "gh_read", gh_read)
    yield b
    for ledger in (reconcile._write_failures, reconcile._read_failures, reconcile._degraded):
        ledger.clear()


def _bare(pr):
    """What `pr_for` returns: no `mergedAt`, no `commits`."""
    return {k: v for k, v in pr.items() if k not in ("mergedAt", "commits")}


class TestTheSweep:
    def test_it_posts_one_receipt_per_closed_stop(self, board):
        board.add("DRE-1", _thread("requeue-once"))
        board.add("DRE-2", _thread("send-to-planning"), lane="Planning")
        board.add("DRE-3", _thread("ask-person"), lane="Green Light")
        reconcile.discharge_medic_stops()
        firsts = {i: b.split("\n", 1)[0] for i, b in board.posts}
        assert firsts == {
            "DRE-1": f"🩺 medic-stop-discharged: because=re-ran step=requeue-once run={RUN}",
            "DRE-2": f"🩺 medic-stop-discharged: because=sent-to-planning "
                     f"step=send-to-planning run={RUN}",
        }
        assert reconcile._write_failures == reconcile._read_failures == []

    def test_a_diagnosis_with_no_receipt_gets_nothing_written(self, board):
        board.add("DRE-1", [_c(DIAGNOSIS, BEFORE)])
        board.add("DRE-2", [_c(DIAGNOSIS, BEFORE)], lane="Done")
        board.prs["DRE-1"] = _pr(state="MERGED", merged_at=AFTER)
        reconcile.discharge_medic_stops()
        assert board.posts == []
        assert reconcile._write_failures == reconcile._read_failures == []

    def test_a_second_pass_writes_nothing_more(self, board):
        board.add("DRE-1", _thread("requeue-once"))
        reconcile.discharge_medic_stops()
        reconcile.discharge_medic_stops()
        assert len(board.posts) == 1

    def test_the_read_is_scoped_to_the_repo_and_the_lanes(self, board):
        board.add("DRE-1", _thread("requeue-once"), labels=("repo:agent-bureau",))
        board.add("DRE-2", _thread("requeue-once"), lane="Done",
                  labels=("repo:agent-bureau",))
        board.add("DRE-3", _thread("requeue-once"), lane="Green Light")
        reconcile.discharge_medic_stops()
        assert board.posts == []
        assert board.lanes_asked == [("In Progress", "In Review", "Planning")]
        assert set(reconcile.BOARD_READ_LANES) >= set(board.lanes_asked[0]), (
            "the lanes asked of the pass's board read are in it, at no request")
        issues = [(q, v) for q, v in board.queries if "issues(" in q]
        assert len(issues) == 1, "one Linear read of its own: Queued and Done"
        query, variables = issues[0]
        assert variables["needle"] == SLUG
        assert (variables["queue"], variables["done"]) == (
            list(reconcile.QUEUE_LANES), "Done")
        assert "{state: {name: {in: $queue}}}" in query
        assert "completedAt: {gt: $since}" in query
        since = medic_discharge._ts(variables["since"])
        age = reconcile.datetime.now(reconcile.UTC) - since
        assert abs(age.total_seconds() - 24 * 3600) < 120

    def test_a_done_card_merged_after_the_receipt_is_discharged(self, board):
        board.add("DRE-1", _thread("fix-path"), lane="Done")
        board.prs["DRE-1"] = _pr(state="MERGED", merged_at=AFTER)
        reconcile.discharge_medic_stops()
        assert [b.split("\n", 1)[0] for _, b in board.posts] == [
            f"🩺 medic-stop-discharged: because=merged step=fix-path run={RUN}"]

    def test_a_queued_card_is_read(self, board):
        board.add("DRE-1", _thread("requeue-once"), lane="Queued")
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-1"]

    @pytest.mark.parametrize("lane", reconcile.QUEUE_LANES)
    def test_the_queue_is_read_under_every_name_it_carries(self, board, lane):
        """The read does not lean on `drain_retiring_lanes` having moved a
        card out of a retiring alias earlier in the pass."""
        board.add("DRE-1", _thread("requeue-once"), lane=lane)
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-1"]

    @pytest.mark.parametrize("now_in", ["Done", "Queued"])
    def test_a_card_that_moved_during_the_pass_is_decided_once(self, board, now_in):
        """The pass's board read caught the card in a board lane at the top
        of the pass; it moved — its pull request merged, or it left Planning
        — before this read, so it is in both reads, as two windows."""
        board.add("DRE-1", _thread("requeue-once"), lane=now_in, seen_in="In Review")
        reconcile.discharge_medic_stops()
        assert [b.split("\n", 1)[0] for _, b in board.posts] == [
            f"🩺 medic-stop-discharged: because=re-ran step=requeue-once run={RUN}"]

    def test_the_fresh_read_of_a_card_in_both_is_the_one_decided(self, board):
        """The board read's window predates a discharge another pass posted;
        the card's own read holds it, so nothing more is written."""
        discharged = _composed(medic_discharge.Discharge(
            medic_discharge.Stop("DRE-1", RUN, "requeue-once",
                                 "transient-infrastructure", True, RECEIPT_AT), "re-ran"))
        board.add("DRE-1", _thread("requeue-once", extra=[_c(discharged, AFTER)]),
                  lane="Done", seen_in="In Review", seen_thread=_thread("requeue-once"))
        reconcile.discharge_medic_stops()
        assert board.posts == []

    def test_an_idle_pass_reads_only_its_own_read(self, board, monkeypatch):
        monkeypatch.setattr(reconcile, "_idle_pass", ["nothing in motion"])
        board.add("DRE-1", _thread("requeue-once"))
        board.add("DRE-2", _thread("requeue-once"), lane="Done")
        reconcile.discharge_medic_stops()
        assert board.lanes_asked == []
        assert [i for i, _ in board.posts] == ["DRE-2"]

    def test_a_partial_window_with_no_receipt_is_not_read_whole(self, board):
        board.add("DRE-1", [_c(DIAGNOSIS, BEFORE)], partial=True)
        reconcile.discharge_medic_stops()
        assert board.whole_reads == []
        assert board.posts == [] and reconcile._read_failures == []

    def test_a_fix_path_reads_its_pull_request(self, board):
        board.add("DRE-1", _thread("fix-path"))
        board.prs["DRE-1"] = _pr(state="MERGED", merged_at=AFTER)
        reconcile.discharge_medic_stops()
        assert [b.split("\n", 1)[0] for _, b in board.posts] == [
            f"🩺 medic-stop-discharged: because=merged step=fix-path run={RUN}"]

    def test_pr_for_is_what_names_the_pull_request(self, board, monkeypatch):
        board.add("DRE-1", _thread("fix-path"))
        full = _pr(committed=AFTER)
        board.prs["DRE-1"] = full
        monkeypatch.setattr(reconcile, "pr_for", lambda ident: _bare(full))
        reconcile.discharge_medic_stops()
        assert "because=new-head" in board.posts[0][1].split("\n", 1)[0]

    def test_a_fix_path_with_no_pull_request_keeps_waiting(self, board):
        board.add("DRE-1", _thread("fix-path"))
        reconcile.discharge_medic_stops()
        assert board.posts == []
        assert reconcile._read_failures == []

    def test_a_card_waiting_out_a_limit_is_left_alone(self, board):
        """A discharge is a pipeline receipt, and a pipeline receipt after a
        `🪦 limit-death:` marker supersedes it — so the stop waits until the
        limit recovery has spoken."""
        marker = (f"🪦 limit-death: kind=claude stage=build "
                  f"reset=2026-11-10T21:00:00Z run={RUN}")
        board.add("DRE-1", _thread("requeue-once", extra=[_c(marker, AFTER)]))
        reconcile.discharge_medic_stops()
        assert board.posts == []


class TestTheCap:
    def test_the_cap_is_ten(self):
        assert reconcile.MEDIC_DISCHARGE_SWEEP_CAP == 10

    def test_at_most_the_cap_oldest_receipt_first(self, board, monkeypatch):
        monkeypatch.setattr(reconcile, "MEDIC_DISCHARGE_SWEEP_CAP", 2)
        stamps = ["2026-11-10T18:00:03.000Z", "2026-11-10T18:00:01.000Z",
                  "2026-11-10T18:00:04.000Z", "2026-11-10T18:00:02.000Z"]
        for n, at in enumerate(stamps, start=1):
            board.add(f"DRE-{n}", [_c(_receipt("requeue-once", run=str(n)), at)])
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-2", "DRE-4"]
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-2", "DRE-4", "DRE-1", "DRE-3"]

    def test_a_stop_that_keeps_waiting_spends_none_of_the_cap(self, board, monkeypatch):
        monkeypatch.setattr(reconcile, "MEDIC_DISCHARGE_SWEEP_CAP", 1)
        board.add("DRE-1", [_c(_receipt("ask-person", run="1"), BEFORE)])
        board.add("DRE-2", [_c(_receipt("requeue-once", run="2"), RECEIPT_AT)])
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-2"]


class TestFailures:
    def test_an_unreadable_thread_is_a_read_failure(self, board):
        board.add("DRE-1", _thread("requeue-once"), partial=True)
        board.add("DRE-2", _thread("requeue-once"))
        reconcile.discharge_medic_stops()
        assert board.whole_reads == ["DRE-1"]
        assert [i for i, _ in board.posts] == ["DRE-2"]
        assert any("DRE-1" in f for f in reconcile._read_failures)

    def test_a_partial_window_with_a_receipt_is_decided_on_the_whole_thread(
            self, board, monkeypatch):
        older = _composed(medic_discharge.Discharge(
            medic_discharge.Stop("DRE-1", RUN, "requeue-once",
                                 "transient-infrastructure", True, RECEIPT_AT), "re-ran"))
        board.add("DRE-1", [_c(_receipt("requeue-once"), RECEIPT_AT)], partial=True)
        whole = [_c(_receipt("requeue-once"), RECEIPT_AT), _c(older, AFTER),
                 _c("a person said something", LATER)]
        monkeypatch.setattr(linear_ops, "comment_timeline",
                            lambda identifier, whole_thread=False, **_k: whole)
        reconcile.discharge_medic_stops()
        assert board.posts == [], "the whole thread holds this run's discharge"

    def test_a_rate_limited_board_read_degrades(self, board, monkeypatch):
        def limited(*_a, **_k):
            raise linear_ops.LinearRateLimited("RATELIMITED")
        monkeypatch.setattr(linear_ops, "gql", limited)
        reconcile.discharge_medic_stops()
        assert reconcile._read_failures == [] and reconcile._write_failures == []
        assert any("medic-discharge" in d for d in reconcile._degraded)

    def test_another_board_read_failure_is_a_read_failure(self, board, monkeypatch):
        def broken(*_a, **_k):
            raise linear_ops.LinearError("HTTP 400")
        monkeypatch.setattr(linear_ops, "gql", broken)
        reconcile.discharge_medic_stops()
        assert reconcile._read_failures and reconcile._degraded == []

    def test_a_rate_limited_pull_request_read_degrades(self, board):
        board.add("DRE-1", _thread("fix-path"))
        board.add("DRE-2", _thread("requeue-once"))
        board.prs["DRE-1"] = reconcile.ReconcileRateLimited("API rate limit exceeded")
        reconcile.discharge_medic_stops()
        assert [i for i, _ in board.posts] == ["DRE-2"]
        assert reconcile._read_failures == []
        assert any("DRE-1" in d for d in reconcile._degraded)

    def test_an_unreadable_pull_request_is_a_read_failure(self, board):
        board.add("DRE-1", _thread("fix-path"))
        board.prs["DRE-1"] = reconcile.ReconcileReadError("HTTP 502")
        reconcile.discharge_medic_stops()
        assert board.posts == []
        assert any("DRE-1" in f for f in reconcile._read_failures)

    def test_a_failed_post_is_a_write_failure(self, board):
        board.add("DRE-1", _thread("requeue-once"))
        board.fail_comment = True
        reconcile.discharge_medic_stops()
        assert reconcile._write_failures


class TestWhereItRuns:
    def _backstops(self):
        source = inspect.getsource(reconcile.main)
        return re.findall(r"^\s+([a-z_]+),$", source.split("for backstop in (", 1)[1]
                          .split("):", 1)[0], re.M)

    def test_it_is_in_the_backstop_list_beside_the_inherited_red_main_return(self):
        """Beside the return, after the refresh that follows it — the three
        are pinned in a row (DRE-8849) — so a `merge-ref-refreshed` receipt
        posted on this pass is already on the thread it reads."""
        names = self._backstops()
        assert "discharge_medic_stops" in names
        i = names.index("discharge_medic_stops")
        assert names[i - 1] == "refresh_stale_merge_refs"
        assert names[i - 2] == "return_inherited_red_main_holds"

    def test_the_event_driven_passes_do_not_run_it(self, monkeypatch):
        called = []
        monkeypatch.setattr(reconcile, "discharge_medic_stops", lambda: called.append(1))
        monkeypatch.setattr(reconcile, "unstick_conflicts", lambda: None)
        reconcile.main(conflicts_only=True)
        assert called == []

    def test_it_posts_through_the_act_writer(self):
        source = inspect.getsource(reconcile.discharge_medic_stops)
        assert ("pipeline_act.receipt(medic_discharge.ACT, "
                "medic_discharge.body(found))") in source

    def test_the_module_does_no_io(self):
        module = (ROOT / "scripts" / "medic_discharge.py").read_text(encoding="utf-8")
        for io in ("linear_ops", "subprocess", "urllib", "gh_read", "open("):
            assert io not in module, io
