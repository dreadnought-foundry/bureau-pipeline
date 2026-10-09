"""A spent review budget parks the card in Green Light with a question (DRE-6181).

At REVIEW_NUDGE_CAP the sweep used to label the card `needs-human`, leave it
In Review and post a bare `🚨 review-nudge-cap` notice — a hold a person found
only by scanning Linear. `hand_review_nudge_to_person` now writes, in order:

  1. the hold — `hold.apply(ident, "review-cap-spent", <head>, "reconcile.py")`,
     the label and its `🔒 hold:` stamp bound to the head;
  2. the question — `review_cap_question.compose(...)` over the evidence the
     sweep already holds, in the one Green Light format (DRE-3893);
  3. the pull request blocker — `review_cap_question.pr_blocker(...)` over the
     same evidence, through `_post_pr_note`, so a person's Operator decision
     has a blocker to answer (`fix_context.operator_decision`);
  4. the park — `linear_ops.cmd_advance(ident, "Green Light", REVIEW_LANE)`.

Once per head: the key on a worker-bot comment of the pull request, with the
label on the card, means nothing is written a second time.

WHAT THIS PINS, one section per acceptance criterion of the card:

  1. The order of the writes, and a second sweep over the same head writing
     nothing; the read door's live lane gating the first write; a blocker
     that did not post parking nothing; and a park that stopped after its
     hold — the run died, or the blocker did not post — finished by the next
     sweep, which writes only what is missing.
  2. The real `fix_context` readers over a REST thread holding the posted
     blocker and a person's decision: PROCEED with it, SKIP `no-blocker`
     without it.
  3. The question is `compose`'s return value over the evidence gathered —
     tag, head, verdicts, count, age, the gate's hold note when it declined —
     and, unpatched, a body `console_escalation` reads.
  4. REVIEW_NUDGE_CAP=0 parks on the first stale sweep with `cap=0`.
  5. The lane contract declares the park, and the two new comment sites are
     declared `not-an-act`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_review_cap_green_light.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import console_escalation  # noqa: E402
import fix_context  # noqa: E402
import green_light_rows  # noqa: E402
import hold  # noqa: E402
import lane_contract  # noqa: E402
import reconcile  # noqa: E402
import review_cap_question  # noqa: E402
from test_review_lane_nudge_cap import _Board, _pr  # noqa: E402

HEAD = "a" * 40
IDENT = "DRE-5231"
NUMBER = 42
UNIT = "reconcile.py#hand_review_nudge_to_person"
GATE_NOTE = (f"⏸️ Merge gate: declined @{HEAD} — required check tests "
             "concluded failure")
STAMP = f"🔒 hold: reason=review-cap-spent at={HEAD} lifts=new-head by=reconcile.py"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(reconcile, "REPO_SLUG", "agent-bureau")
    monkeypatch.setattr(reconcile, "_door_sourced", set())
    reconcile._write_failures.clear()
    yield
    reconcile._write_failures.clear()


def _receipt(tag, n):
    return {"body": f"🧹 Reconcile: {tag} @{HEAD} ({n}/3) — re-triggered.",
            "createdAt": "2026-09-30T00:00:00Z"}


def _card(tag=reconcile.GATE_NUDGE_KEY, labels=(), extra=(), spent=3):
    """An In Review card carrying `spent` receipts of `tag` on HEAD."""
    nodes = [_receipt(tag, n) for n in range(1, spent + 1)] + list(extra)
    return {
        "identifier": IDENT,
        "state": {"name": reconcile.REVIEW_LANE},
        "labels": {"nodes": [{"name": n} for n in labels]},
        # The API's order — newest first; window_nodes reverses it.
        "comments": {"nodes": list(reversed(nodes))},
    }


def _qa(body):
    return {"author": {"login": reconcile.QA_BOT_LOGIN}, "body": body}


def _worker(body):
    return {"author": {"login": reconcile.WORKER_BOT_LOGIN}, "body": body}


def _pr_with(*comments):
    return {"number": NUMBER, "headRefOid": HEAD, "comments": list(comments)}


class _Writes:
    """Every write the park can make, in one ordered log."""

    def __init__(self):
        self.log: list = []

    def run(self, card, pr, tag=reconcile.GATE_NUDGE_KEY,
            critic="APPROVE", verifier="FAIL"):
        log = self.log
        with patch.object(reconcile.linear_ops, "add_label",
                          side_effect=lambda i, n: log.append(("label", i, n))), \
             patch.object(reconcile.linear_ops, "cmd_comment",
                          side_effect=lambda i, b, *f: log.append(("comment", i, b))), \
             patch.object(reconcile.linear_ops, "cmd_advance",
                          side_effect=lambda i, to, frm, *f, **k:
                          log.append(("advance", i, to, frm))), \
             patch.object(reconcile.linear_ops, "cmd_state",
                          side_effect=lambda *a, **k: log.append(("state",) + a)), \
             patch.object(reconcile, "_post_pr_note",
                          side_effect=lambda n, b: log.append(("pr", n, b)) or True), \
             patch.object(reconcile, "age_minutes", return_value=375):
            reconcile.hand_review_nudge_to_person(card, pr, tag, critic, verifier)
        return log


def _evidence(**over):
    base = dict(card=IDENT, pr_number=NUMBER, head=HEAD,
                tag=reconcile.GATE_NUDGE_KEY, critic="APPROVE", verifier="FAIL",
                spent=3, hours=6.25, gate_note_line=GATE_NOTE, cap=3)
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# 1. the order of the writes, once per head                                    #
# --------------------------------------------------------------------------- #


class TestTheParkWritesInOrder:
    def test_hold_stamp_question_blocker_then_the_move(self):
        log = _Writes().run(_card(), _pr_with(_qa(GATE_NOTE)))
        assert [entry[0] for entry in log] == [
            "label", "comment", "comment", "pr", "advance"], log
        assert log[0] == ("label", IDENT, reconcile.HOLD_LABEL)
        assert log[1] == ("comment", IDENT, STAMP)
        assert log[2] == ("comment", IDENT,
                          review_cap_question.compose(**_evidence()))
        assert log[3] == ("pr", NUMBER,
                          review_cap_question.pr_blocker(**_evidence()))
        assert log[4] == ("advance", IDENT, "Green Light", reconcile.REVIEW_LANE)

    def test_the_stamp_is_the_live_hold_the_readers_see(self):
        log = _Writes().run(_card(), _pr_with())
        bodies = [entry[2] for entry in log if entry[0] == "comment"]
        stamp = hold.read_stamp(bodies)
        assert stamp["reason"] == "review-cap-spent"
        assert stamp["at"] == HEAD
        assert hold.reason_of([reconcile.HOLD_LABEL], bodies) == "review-cap-spent"

    def test_a_second_sweep_over_the_same_head_writes_nothing(self):
        first = _Writes().run(_card(), _pr_with(_qa(GATE_NOTE)))
        bodies = [entry[2] for entry in first if entry[0] == "comment"]
        blocker = next(entry[2] for entry in first if entry[0] == "pr")
        again = _card(
            labels=(reconcile.HOLD_LABEL,),
            extra=[{"body": b, "createdAt": "2026-09-30T01:00:00Z"} for b in bodies])
        again["state"] = {"name": "Green Light"}
        log = _Writes().run(again, _pr_with(_qa(GATE_NOTE), _worker(blocker)))
        assert log == []

    def test_held_and_asked_but_still_in_review_only_moves(self):
        # The run died between the blocker and the move: everything is said,
        # and the park is the one write left.
        first = _Writes().run(_card(), _pr_with(_qa(GATE_NOTE)))
        bodies = [entry[2] for entry in first if entry[0] == "comment"]
        blocker = next(entry[2] for entry in first if entry[0] == "pr")
        stopped = _card(
            labels=(reconcile.HOLD_LABEL,),
            extra=[{"body": b, "createdAt": "2026-09-30T01:00:00Z"} for b in bodies])
        log = _Writes().run(stopped, _pr_with(_qa(GATE_NOTE), _worker(blocker)))
        assert log == [("advance", IDENT, "Green Light", reconcile.REVIEW_LANE)]

    def test_a_blocker_that_did_not_post_does_not_park(self):
        # `_post_pr_note` records a failed post and answers False. Parked
        # anyway, the card would leave the review lane with no blocker for a
        # person's Operator decision to answer, and no sweep would retry it.
        log: list = []
        with patch.object(reconcile.linear_ops, "add_label",
                          side_effect=lambda i, n: log.append(("label", i, n))), \
             patch.object(reconcile.linear_ops, "cmd_comment",
                          side_effect=lambda i, b, *f: log.append(("comment", i, b))), \
             patch.object(reconcile.linear_ops, "cmd_advance") as advance, \
             patch.object(reconcile, "_post_pr_note",
                          side_effect=lambda n, b: log.append(("pr", n, b)) and False), \
             patch.object(reconcile, "age_minutes", return_value=375):
            reconcile.hand_review_nudge_to_person(
                _card(), _pr_with(_qa(GATE_NOTE)), reconcile.GATE_NUDGE_KEY,
                "APPROVE", "FAIL")
        assert [entry[0] for entry in log] == [
            "label", "comment", "comment", "pr"], log
        advance.assert_not_called()

    def test_a_key_quoted_by_a_person_is_not_the_blocker(self):
        # Only the worker bot's own comment counts as asked; a person quoting
        # the key does not silence the blocker.
        quoted = {"author": {"login": "someone"},
                  "body": f"see review-nudge-cap @{HEAD}"}
        log = _Writes().run(_card(), _pr_with(quoted))
        assert [entry[0] for entry in log].count("pr") == 1

    def test_a_lifted_label_with_the_head_unmoved_re_holds_and_re_parks(self):
        first = _Writes().run(_card(), _pr_with())
        bodies = [entry[2] for entry in first if entry[0] == "comment"]
        blocker = next(entry[2] for entry in first if entry[0] == "pr")
        # A person took the label off and moved the card back; nothing pushed.
        back = _card(extra=[{"body": b, "createdAt": "2026-09-30T01:00:00Z"}
                            for b in bodies])
        log = _Writes().run(back, _pr_with(_worker(blocker)))
        assert [entry[0] for entry in log] == [
            "label", "comment", "advance"], log
        assert log[1] == ("comment", IDENT, STAMP)
        assert log[2] == ("advance", IDENT, "Green Light", reconcile.REVIEW_LANE)

    def test_the_door_reading_a_card_that_left_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(reconcile, "_door_sourced", {IDENT})
        live = {"state": {"name": "Green Light"}, "labels": {"nodes": []}}
        with patch.object(reconcile.linear_ops, "get_issue", return_value=live):
            log = _Writes().run(_card(), _pr_with())
        assert log == []

    def test_the_door_reading_a_card_still_in_review_parks_it(self, monkeypatch):
        monkeypatch.setattr(reconcile, "_door_sourced", {IDENT})
        live = {"state": {"name": reconcile.REVIEW_LANE}, "labels": {"nodes": []}}
        with patch.object(reconcile.linear_ops, "get_issue", return_value=live):
            log = _Writes().run(_card(), _pr_with())
        assert [entry[0] for entry in log] == [
            "label", "comment", "comment", "pr", "advance"], log


class TestAnUnfinishedParkIsFinished:
    """The whole sweep, twice: the first stops part-way after the hold, and
    the next — which skips every other held card — finishes the park and
    repeats nothing the first one wrote."""

    def _spent_board(self):
        board = _Board()
        for n in range(1, 4):
            board.comments.append(_receipt(reconcile.GATE_NUDGE_KEY, n)["body"])
        return board, _pr(critic="APPROVE", verifier="FAIL")

    def _held_in_review(self, board):
        assert board.state == reconcile.REVIEW_LANE
        assert reconcile.held(board.card())
        assert reconcile.review_cap_park_unfinished(board.card()) == HEAD

    def _parked_once(self, board):
        assert board.state == "Green Light"
        assert [b for b in board.comments if b.startswith("🔒 hold:")] == [STAMP]
        assert len([b for b in board.comments
                    if b.startswith(f"🚨 review-nudge-cap PR #{NUMBER} @{HEAD}:")]) == 1
        assert len(board.pr_notes) == 1
        assert board.pr_notes[0] == review_cap_question.pr_blocker(
            # _Board's age_minutes answers 999.
            **_evidence(gate_note_line=None, hours=999 / 60))

    def test_a_run_that_died_right_after_the_hold_is_finished(self):
        board, pr = self._spent_board()
        board.die_on_comment = "🚨 review-nudge-cap"
        with pytest.raises(RuntimeError):
            board.sweep(pr)
        self._held_in_review(board)
        assert board.pr_notes == []
        board.die_on_comment = None
        nudge, cmd_state, posted = board.sweep(pr)
        nudge.assert_not_called()
        cmd_state.assert_not_called()
        # The question only: the hold it died after is not written twice.
        assert len(posted) == 1, posted
        assert posted[0].startswith(f"🚨 review-nudge-cap PR #{NUMBER} @{HEAD}:")
        self._parked_once(board)

    def test_a_blocker_that_did_not_post_is_posted_and_parked_next_sweep(self):
        board, pr = self._spent_board()
        board.refuse_pr_notes = 1
        board.sweep(pr)
        self._held_in_review(board)
        assert board.pr_notes == []
        _, _, posted = board.sweep(pr)
        assert posted == []  # the hold and the question are not repeated
        self._parked_once(board)

    def test_a_run_that_died_at_the_blocker_is_finished(self):
        board, pr = self._spent_board()
        board.die_on_pr_note = True
        with pytest.raises(RuntimeError):
            board.sweep(pr)
        self._held_in_review(board)
        board.die_on_pr_note = False
        board.sweep(pr)
        self._parked_once(board)

    def test_a_parked_card_is_not_touched_again(self):
        board, pr = self._spent_board()
        board.sweep(pr)
        self._parked_once(board)
        before = (list(board.comments), list(board.pr_notes), board.state)
        _, _, posted = board.sweep(pr)
        assert posted == []
        assert (board.comments, board.pr_notes, board.state) == before

    def test_a_new_head_leaves_the_unfinished_park_to_the_holds_lane(self):
        board, pr = self._spent_board()
        board.refuse_pr_notes = 1
        board.sweep(pr)
        self._held_in_review(board)
        nudge, _, posted = board.sweep(_pr(head="c" * 40, critic="APPROVE",
                                          verifier="FAIL"))
        nudge.assert_not_called()
        assert posted == [] and board.pr_notes == []
        assert board.state == reconcile.REVIEW_LANE

    def test_a_persons_hold_in_review_is_not_parked(self):
        board, pr = self._spent_board()
        board.labels.append(reconcile.HOLD_LABEL)  # no stamp: a person's hold
        assert reconcile.review_cap_park_unfinished(board.card()) is None
        nudge, _, posted = board.sweep(pr)
        nudge.assert_not_called()
        assert posted == [] and board.pr_notes == []
        assert board.state == reconcile.REVIEW_LANE


# --------------------------------------------------------------------------- #
# 2. the way back the question names is the one both restarts read             #
# --------------------------------------------------------------------------- #


def _rest(login, kind, body):
    return {"user": {"login": login, "type": kind}, "body": body}


class TestAnOperatorDecisionAnswersTheBlocker:
    def _thread(self, with_blocker):
        log = _Writes().run(_card(), _pr_with())
        blocker = next(entry[2] for entry in log if entry[0] == "pr")
        thread = [_rest("someone", "User", "looking at this now")]
        if with_blocker:
            thread.append(_rest(reconcile.WORKER_REST_LOGIN, "Bot", blocker))
        thread.append(_rest("someone", "User",
                            "**Operator decision** — fix the red tests check"))
        return thread

    def test_the_decision_after_the_blocker_is_read_and_proceeds(self):
        thread = self._thread(with_blocker=True)
        decision = fix_context.operator_decision(thread, reconcile.WORKER_REST_LOGIN)
        assert decision is thread[-1]
        verdict, _ = fix_context.decision_trigger(thread, reconcile.WORKER_REST_LOGIN)
        assert verdict == fix_context.TRIGGER_PROCEED

    def test_without_the_blocker_the_decision_steers_nothing(self):
        thread = self._thread(with_blocker=False)
        assert fix_context.decision_trigger(thread, reconcile.WORKER_REST_LOGIN) == (
            fix_context.TRIGGER_SKIP, fix_context.SKIP_NO_BLOCKER)


# --------------------------------------------------------------------------- #
# 3. the question is the composer's, over the evidence the sweep gathered      #
# --------------------------------------------------------------------------- #


class TestTheQuestionIsComposed:
    def _composed(self, card, pr, **kw):
        compose = MagicMock(return_value="THE QUESTION")
        blocker = MagicMock(return_value="THE BLOCKER")
        with patch.object(reconcile.review_cap_question, "compose", compose), \
             patch.object(reconcile.review_cap_question, "pr_blocker", blocker):
            log = _Writes().run(card, pr, **kw)
        return compose, blocker, log

    def test_the_gate_budget_passes_the_evidence_and_the_hold_note(self):
        older = f"⏸️ Merge gate: declined @{'b' * 40} — an older hold"
        compose, blocker, log = self._composed(
            _card(), _pr_with(_qa(older), _qa(GATE_NOTE + "\n\nNot merged.")))
        compose.assert_called_once_with(**_evidence())
        blocker.assert_called_once_with(**_evidence())
        assert ("comment", IDENT, "THE QUESTION") in log
        assert ("pr", NUMBER, "THE BLOCKER") in log

    def test_the_review_budget_passes_no_gate_note(self):
        compose, _, _ = self._composed(
            _card(tag=reconcile.REVIEW_NUDGE_KEY), _pr_with(_qa(GATE_NOTE)),
            tag=reconcile.REVIEW_NUDGE_KEY, critic="none", verifier="none")
        compose.assert_called_once_with(**_evidence(
            tag=reconcile.REVIEW_NUDGE_KEY, critic="none", verifier="none",
            gate_note_line=None))

    def test_a_gate_note_forged_by_a_person_is_not_read(self):
        forged = {"author": {"login": "someone"}, "body": GATE_NOTE}
        compose, _, _ = self._composed(_card(), _pr_with(forged))
        assert compose.call_args.kwargs["gate_note_line"] is None

    def test_unpatched_the_question_reads_as_the_green_light_format(self):
        log = _Writes().run(_card(), _pr_with(_qa(GATE_NOTE)))
        question = log[2][2]
        assert console_escalation.problems(question) == []
        assert console_escalation.parse(question) is not None
        assert question.startswith(f"🚨 review-nudge-cap PR #{NUMBER} @{HEAD}:")
        assert question.rstrip().endswith(f"review-nudge-cap @{HEAD}")


# --------------------------------------------------------------------------- #
# 4. a cap of 0 parks on the first stale sweep                                 #
# --------------------------------------------------------------------------- #


class TestTheCapIsOffAtZero:
    def test_parks_on_the_first_stale_sweep_with_cap_zero(self, monkeypatch):
        monkeypatch.setattr(reconcile, "REVIEW_NUDGE_CAP", 0)
        board = _Board()
        compose = MagicMock(wraps=review_cap_question.compose)
        with patch.object(reconcile.review_cap_question, "compose", compose):
            nudge, _, _ = board.sweep(_pr(critic="APPROVE", verifier="FAIL"))
        nudge.assert_not_called()
        assert board.state == "Green Light"
        assert reconcile.HOLD_LABEL in board.labels
        assert compose.call_args.kwargs["cap"] == 0
        assert compose.call_args.kwargs["spent"] == 0


# --------------------------------------------------------------------------- #
# 5. the contract and the act registry declare the park                        #
# --------------------------------------------------------------------------- #


class TestTheLaneContractDeclaresThePark:
    def _green_light(self):
        return lane_contract.lane(green_light_rows.lane_name())["clauses"]

    def test_the_sweep_is_a_green_light_writer(self):
        assert "reconcile.py" in self._green_light()["writers"]["who"]

    def test_the_arrival_record_names_the_park(self):
        record = next(r for r in green_light_rows.arrivals() if r.get("where") == UNIT)
        assert record["kind"] == "agent-escalation"
        assert record["writer"] == "reconcile.py"
        assert record["card"] == "DRE-6172"
        assert "review-nudge-cap" in record["evidence"]

    def test_the_discovery_finds_the_park_and_the_rows_check_is_green(self):
        units = {unit for _, unit in green_light_rows.green_light_writes(str(ROOT))}
        assert UNIT in units
        assert green_light_rows.problems() == []


class TestTheTwoNewCommentSitesAreDeclared:
    def _ours(self):
        doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
        return [e for e in doc["unconverted"]
                if e.get("file") == "scripts/reconcile.py"
                and e.get("anchor") in ("cmd_comment(ident, review_question)",
                                        "_post_pr_note(number, review_blocker)")]

    def test_both_are_not_an_act(self):
        ours = self._ours()
        assert len(ours) == 2, ours
        assert all(e["kind"] == "not-an-act" for e in ours), ours

    def test_the_registry_check_is_green(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_act_receipts.py")],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
