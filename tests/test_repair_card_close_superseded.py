"""A repair run that finds main already green closes the card it filed (DRE-9217).

The Red-Main Repair loop files its card ("Red main repaired — <check> failed
at <sha>") into `In Progress` before its agent starts. When the agent then
finds main already green it writes `/tmp/repair-superseded.txt`, the Report
step verifies the claim against a later green run (DRE-5700) and ends the job
green — and until this card nothing closed the card it had filed. The reconcile
sweep's `settle` pass would, eventually; in the meantime the card sat In
Progress with no work behind it.

The replay, 2026-11-04 PT: DRE-9216 ("Red main repaired — Pipeline Tests failed
at 19d0cb1af205") was filed at 17:22. Its repair run 41948055432 ended
superseded, because Pipeline Tests run 41945382062 had passed on main
8261eb54f at 16:59. The card stayed In Progress until an operator sweep
canceled it at 17:29.

What these tests pin:

  * `close_superseded` moves the card to Canceled, then posts ONE comment
    naming the green run, its link and its time in PT. The comment opens
    with `SETTLE_MARKER`, so the sweep's `settle` never comments a second time;
  * a card already Done, Canceled or Duplicate is left alone, and the log
    says so with its state;
  * a card whose state cannot be read, or whose cancel is refused or raises,
    gets no comment — a comment claiming a cancel that did not land is the
    DRE-1254 shape;
  * the green run's time comes off the run listing the Report step already
    gathered — when it completed (`updated_at`), else when it was created;
  * the `close-superseded` subcommand drives all of it, and exits 1 only
    when the board refused a write.

Run: python3 -m pytest tests/test_repair_card_close_superseded.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import repair_card  # noqa: E402

CARD = "DRE-9216"
WORKFLOW = "Pipeline Tests"
GREEN_RUN = "41945382062"
GREEN_URL = ("https://github.com/dreadnought-foundry/bureau-pipeline/actions/"
             f"runs/{GREEN_RUN}")
# The record says 4:59 PM PT on 2026-11-04 — PST since 2026-11-01, so 00:59Z
# on the 5th.
GREEN_AT = "2026-11-05T00:59:12Z"
GREEN_PT = "2026-11-04 16:59 PT"


class FakeBoard:
    """The Linear seam `close_superseded` uses, recorded."""

    def __init__(self, state="In Progress", *, state_error=None,
                 cancel_answer=True, cancel_error=None, comment_error=None):
        self.state = state
        self.state_error = state_error
        self.cancel_answer = cancel_answer
        self.cancel_error = cancel_error
        self.comment_error = comment_error
        self.canceled: list[tuple] = []
        self.comments: list[tuple] = []

    def card_state(self, identifier):
        if self.state_error:
            raise self.state_error
        return self.state

    def cancel(self, identifier, lane):
        if self.cancel_error:
            raise self.cancel_error
        self.canceled.append((identifier, lane))
        return self.cancel_answer

    def cmd_comment(self, identifier, body):
        if self.comment_error:
            raise self.comment_error
        self.comments.append((identifier, body))


def close(board, **overrides):
    said: list[str] = []
    kwargs = dict(card=CARD, green_run=GREEN_RUN, green_url=GREEN_URL,
                  green_at=GREEN_AT, workflow_name=WORKFLOW, ops=board,
                  log=said.append)
    kwargs.update(overrides)
    return repair_card.close_superseded(**kwargs), said


class ClosesTheCardItFiledTest(unittest.TestCase):

    def test_an_in_progress_card_is_canceled(self):
        board = FakeBoard()
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        # The cancel is conditional on the lane the decision read, so a card
        # moved in between is not dragged.
        self.assertEqual(board.canceled, [(CARD, "In Progress")])

    def test_one_comment_names_the_green_run_and_its_pt_time(self):
        board = FakeBoard()
        close(board)
        self.assertEqual(len(board.comments), 1)
        identifier, body = board.comments[0]
        self.assertEqual(identifier, CARD)
        self.assertIn(GREEN_RUN, body)
        self.assertIn(GREEN_URL, body)
        self.assertIn(GREEN_PT, body)
        self.assertIn(WORKFLOW, body)

    def test_the_comment_carries_the_settle_marker(self):
        # `settle` reads this marker as "already said" — one comment per card.
        board = FakeBoard()
        close(board)
        self.assertTrue(board.comments[0][1].startswith(repair_card.SETTLE_MARKER))

    def test_the_run_log_says_the_card_was_canceled(self):
        _, said = close(FakeBoard())
        self.assertTrue(any(CARD in line and "Canceled" in line and GREEN_RUN in line
                            for line in said), said)

    def test_a_green_time_nobody_answered_still_closes(self):
        board = FakeBoard()
        result, _ = close(board, green_at="")
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertNotIn(GREEN_PT, board.comments[0][1])
        self.assertIn("did not answer", board.comments[0][1])


class AlreadyMovedIsLeftAloneTest(unittest.TestCase):

    def assert_left_alone(self, state):
        board = FakeBoard(state=state)
        result, said = close(board)
        self.assertEqual(result, repair_card.CLOSE_ALREADY_FINISHED)
        self.assertEqual(board.canceled, [])
        self.assertEqual(board.comments, [])
        self.assertTrue(any(CARD in line and "left alone" in line and state in line
                            for line in said), said)

    def test_done_is_left_alone(self):
        self.assert_left_alone("Done")

    def test_canceled_is_left_alone(self):
        self.assert_left_alone("Canceled")

    def test_duplicate_is_left_alone(self):
        self.assert_left_alone("Duplicate")


class NothingIsClaimedThatDidNotLandTest(unittest.TestCase):

    def test_an_unreadable_card_is_not_written(self):
        board = FakeBoard(state_error=RuntimeError("Linear 503"))
        result, said = close(board)
        self.assertEqual(result, repair_card.CLOSE_FAILED)
        self.assertEqual(board.canceled, [])
        self.assertEqual(board.comments, [])
        self.assertTrue(any("Linear 503" in line for line in said), said)

    def test_a_cancel_that_raises_gets_no_comment(self):
        board = FakeBoard(cancel_error=RuntimeError("refused"))
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSE_FAILED)
        self.assertEqual(board.comments, [])

    def test_a_cancel_the_guard_refused_gets_no_comment(self):
        # cmd_state answers False when the card left the lane the decision read.
        board = FakeBoard(cancel_answer=False)
        result, said = close(board)
        self.assertEqual(result, repair_card.CLOSE_ALREADY_FINISHED)
        self.assertEqual(board.comments, [])
        self.assertTrue(any("left alone" in line for line in said), said)

    def test_a_comment_that_fails_is_a_failure(self):
        board = FakeBoard(comment_error=RuntimeError("refused"))
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSE_FAILED)
        self.assertEqual(board.canceled, [(CARD, "In Progress")])


class GreenRunTimeTest(unittest.TestCase):

    def history(self, **run):
        row = {"run_id": int(GREEN_RUN), "head_sha": "8261eb54f" + "0" * 31,
               "status": "completed", "conclusion": "success", "event": "push",
               "created_at": "2026-11-05T00:41:02Z"}
        row.update(run)
        return {"current": {}, "prior": [], "branch_runs": [row]}

    def test_the_completion_time_is_read_first(self):
        self.assertEqual(repair_card.green_run_time(
            self.history(updated_at=GREEN_AT), GREEN_RUN), GREEN_AT)

    def test_the_creation_time_when_no_completion_time(self):
        self.assertEqual(repair_card.green_run_time(self.history(), GREEN_RUN),
                         "2026-11-05T00:41:02Z")

    def test_a_run_not_in_the_listing_has_no_time(self):
        self.assertEqual(repair_card.green_run_time(self.history(), "1"), "")

    def test_an_unreadable_history_has_no_time(self):
        self.assertEqual(repair_card.green_run_time(None, GREEN_RUN), "")


class SubcommandTest(unittest.TestCase):

    def run_cli(self, board, history):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "history.json")
            with open(path, "w") as fh:
                json.dump(history, fh)
            with mock.patch.object(repair_card, "_CloseOps", lambda: board):
                return repair_card.main([
                    "close-superseded", "--card", CARD,
                    "--green-run", GREEN_RUN, "--green-url", GREEN_URL,
                    "--workflow-name", WORKFLOW, "--history-file", path])

    HISTORY = {"current": {}, "prior": [], "branch_runs": [
        {"run_id": int(GREEN_RUN), "head_sha": "8" * 40, "status": "completed",
         "conclusion": "success", "event": "push",
         "created_at": "2026-11-05T00:41:02Z", "updated_at": GREEN_AT}]}

    def test_the_subcommand_closes_with_the_listed_time(self):
        board = FakeBoard()
        self.assertEqual(self.run_cli(board, self.HISTORY), 0)
        self.assertEqual(board.canceled, [(CARD, "In Progress")])
        self.assertIn(GREEN_PT, board.comments[0][1])

    def test_already_finished_exits_zero(self):
        board = FakeBoard(state="Done")
        self.assertEqual(self.run_cli(board, self.HISTORY), 0)
        self.assertEqual(board.canceled, [])

    def test_a_refused_write_exits_one(self):
        board = FakeBoard(cancel_error=RuntimeError("refused"))
        self.assertEqual(self.run_cli(board, self.HISTORY), 1)


if __name__ == "__main__":
    unittest.main()
