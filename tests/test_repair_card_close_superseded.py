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
    when the board refused a write;
  * a card listing a failing test that an earlier Canceled repair card of the
    same repo also lists gets that flaky test's fix card filed BEFORE the
    cancel, named in the comment — the step the sweep's `settle` runs before
    its own cancel (DRE-6870), which a card this close cancels never reaches;
  * the real `_CloseOps` reads and writes Linear through the shapes and the
    signatures `linear_ops` actually has.

Run: python3 -m pytest tests/test_repair_card_close_superseded.py -v
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import repair_card  # noqa: E402

CARD = "DRE-9216"
WORKFLOW = "Pipeline Tests"
REPO = "bureau-pipeline"
FAILED_SHA = "19d0cb1af205" + "1" * 28
FAILED_RUN_URL = ("https://github.com/dreadnought-foundry/bureau-pipeline/"
                  "actions/runs/41945000001")
FILED = "2026-11-05T01:22:00.000Z"
NOW = datetime(2026, 11, 5, 1, 30, tzinfo=UTC)
FLAKY_TEST = "tests/test_repair_history.py::GatherTest::test_paged"
EARLIER = "DRE-9100"
FIX_CARD = "DRE-9300"
FIX_TITLE = repair_card.fix_card_title(REPO, FLAKY_TEST)
GREEN_RUN = "41945382062"
GREEN_URL = ("https://github.com/dreadnought-foundry/bureau-pipeline/actions/"
             f"runs/{GREEN_RUN}")
# The record says 4:59 PM PT on 2026-11-04 — PST since 2026-11-01, so 00:59Z
# on the 5th.
GREEN_AT = "2026-11-05T00:59:12Z"
GREEN_PT = "2026-11-04 16:59 PT"


def card_body(tests=()) -> str:
    """A repair card's body as `open` writes it — the shipped writer."""
    return repair_card.card_body(
        workflow_name=WORKFLOW, run_url=FAILED_RUN_URL, head_sha=FAILED_SHA,
        repo_slug=REPO, attempt=1, failing_tests=list(tests))


def earlier_card(*, state="Canceled", filed=NOW - timedelta(days=3)) -> dict:
    """An earlier repair card of this repo listing `FLAKY_TEST`, in the shape
    `repair_card_history` answers."""
    return {"identifier": EARLIER, "description": card_body([FLAKY_TEST]),
            "state": state,
            "createdAt": filed.strftime("%Y-%m-%dT%H:%M:%S.000Z")}


class FakeBoard:
    """The Linear seam `close_superseded` uses, recorded — in ONE order, so a
    test can say the fix card was filed before the cancel."""

    def __init__(self, state="In Progress", *, state_error=None,
                 cancel_answer=True, cancel_error=None, comment_error=None,
                 tests=(), history=(), history_error=None, open_fix=None,
                 create_error=None):
        self.state = state
        self.state_error = state_error
        self.cancel_answer = cancel_answer
        self.cancel_error = cancel_error
        self.comment_error = comment_error
        self.description = card_body(tests)
        self.history = history
        self.history_error = history_error
        self.open_fix = open_fix
        self.create_error = create_error
        self.canceled: list[tuple] = []
        self.comments: list[tuple] = []
        self.history_reads: list[tuple] = []
        self.created: list[dict] = []
        self.order: list[str] = []

    def read_card(self, identifier):
        if self.state_error:
            raise self.state_error
        return {"state": self.state, "description": self.description,
                "created_at": FILED}

    def repair_card_history(self, repo_slug, since):
        self.history_reads.append((repo_slug, since))
        if self.history_error:
            raise self.history_error
        return self.history

    def find_open(self, title):
        return self.open_fix

    def create_card(self, title, description, *, repo_slug, labels=(),
                    lane="Planning"):
        if self.create_error:
            raise self.create_error
        self.order.append("create")
        self.created.append({"title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels),
                             "lane": lane})
        return {"identifier": FIX_CARD}

    def cancel(self, identifier, lane):
        if self.cancel_error:
            raise self.cancel_error
        self.order.append("cancel")
        self.canceled.append((identifier, lane))
        return self.cancel_answer

    def cmd_comment(self, identifier, body):
        if self.comment_error:
            raise self.comment_error
        self.comments.append((identifier, body))


def close(board, **overrides):
    said: list[str] = []
    kwargs = dict(card=CARD, green_run=GREEN_RUN, green_url=GREEN_URL,
                  green_at=GREEN_AT, workflow_name=WORKFLOW, repo_slug=REPO,
                  ops=board, log=said.append, now=NOW)
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
                    "--workflow-name", WORKFLOW, "--repo", REPO,
                    "--history-file", path])

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


class TheFlakyTestsFixCardTest(unittest.TestCase):
    """The second green cancel of a card naming the same failing test files
    that test's fix card first (DRE-6870) — on this close exactly as on the
    sweep's. A card this close cancels is never read by `settle` again, so a
    close that skipped the step would file no fix card at all, and a test
    that heals on its own is the likeliest thing to end superseded."""

    def second_time(self, **kw):
        return FakeBoard(tests=[FLAKY_TEST], history=[earlier_card()], **kw)

    def test_exactly_one_fix_card_is_filed_and_named_in_the_comment(self):
        board = self.second_time()
        result, said = close(board)
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertEqual(len(board.created), 1, said)
        made = board.created[0]
        self.assertEqual(made["title"], FIX_TITLE)
        self.assertEqual(made["repo_slug"], REPO)
        self.assertEqual(made["lane"], linear_ops.CREATE_LANE)
        self.assertEqual(set(made["labels"]),
                         set(repair_card.fix_card_labels(REPO)))
        # The fix card names both failures — the earlier card and this one.
        self.assertIn(EARLIER, made["description"])
        self.assertIn(CARD, made["description"])
        self.assertIn(FAILED_SHA, made["description"])
        self.assertEqual(len(board.comments), 1)
        body = board.comments[0][1]
        self.assertTrue(body.startswith(repair_card.SETTLE_MARKER))
        self.assertIn(FIX_CARD, body)
        self.assertIn(FLAKY_TEST, body)
        self.assertIn(GREEN_RUN, body)
        self.assertTrue(any(FIX_CARD in line for line in said), said)

    def test_the_fix_card_is_filed_before_the_cancel(self):
        # A run that dies between the two finds the fix card by its title
        # next time and files nothing twice — the sweep's own ordering.
        board = self.second_time()
        close(board)
        self.assertEqual(board.order, ["create", "cancel"])

    def test_the_read_is_this_repos_last_14_days(self):
        board = self.second_time()
        close(board)
        self.assertEqual(board.history_reads, [
            (REPO, NOW - timedelta(days=repair_card.FLAKY_WINDOW_DAYS))])

    def test_no_earlier_card_files_no_fix_card(self):
        board = FakeBoard(tests=[FLAKY_TEST], history=[])
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertEqual(board.created, [])
        self.assertEqual(board.canceled, [(CARD, "In Progress")])
        self.assertNotIn("flaky test to fix", board.comments[0][1])

    def test_an_earlier_card_outside_the_window_files_no_fix_card(self):
        board = FakeBoard(tests=[FLAKY_TEST], history=[
            earlier_card(filed=NOW - timedelta(days=15))])
        close(board)
        self.assertEqual(board.created, [])

    def test_a_card_listing_no_test_reads_no_history(self):
        board = FakeBoard(history=[earlier_card()])
        close(board)
        self.assertEqual(board.history_reads, [])
        self.assertEqual(board.created, [])

    def test_a_fix_card_already_open_is_named_and_not_filed_again(self):
        board = self.second_time(open_fix="DRE-9299")
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertEqual(board.created, [])
        self.assertIn("DRE-9299", board.comments[0][1])

    def test_unreadable_earlier_cards_close_nothing(self):
        # The sweep reads this as UNKNOWN and touches nothing; so does this
        # close, and `settle` takes the card — and the question — next pass.
        for history, error in ((None, None),
                               ((), RuntimeError("Linear 503"))):
            with self.subTest(history=history, error=error):
                board = FakeBoard(tests=[FLAKY_TEST], history=history,
                                  history_error=error)
                result, said = close(board)
                self.assertEqual(result, repair_card.CLOSE_FAILED)
                self.assertEqual(board.canceled, [])
                self.assertEqual(board.comments, [])
                self.assertTrue(any("could not be read" in line
                                    for line in said), said)

    def test_a_refused_fix_card_cancels_nothing(self):
        board = self.second_time(create_error=RuntimeError("refused"))
        result, _ = close(board)
        self.assertEqual(result, repair_card.CLOSE_FAILED)
        self.assertEqual(board.canceled, [])
        self.assertEqual(board.comments, [])


class TheRealLinearSeamTest(unittest.TestCase):
    """`_CloseOps` itself, with only `linear_ops`' network calls stubbed — the
    query it sends, the shape it reads back, and the calls it writes with."""

    def drive(self, *, state="In Progress", tests=(), history=(),
              open_fix=None, moved=True):
        sent: dict = {"gql": [], "state": [], "comment": [], "created": [],
                      "history": []}
        issue = {"identifier": CARD, "state": {"name": state},
                 "description": card_body(tests), "createdAt": FILED}

        def gql(query, variables=None, **kw):
            sent["gql"].append((query, variables))
            return {"issue": issue}

        def cmd_state(identifier, state_name, *flags, **kw):
            sent["state"].append((identifier, state_name, flags, kw))
            return moved

        def cmd_comment(identifier, body, *flags):
            sent["comment"].append((identifier, body))

        def history_read(prefix, *, repo_slug, since):
            sent["history"].append((prefix, repo_slug, since))
            return list(history)

        def create_card(title, description, *, repo_slug, labels=(),
                        lane=linear_ops.CREATE_LANE):
            sent["created"].append(title)
            return {"identifier": FIX_CARD}

        with mock.patch.multiple(
                linear_ops, gql=gql, cmd_state=cmd_state,
                cmd_comment=cmd_comment,
                repo_cards_by_title_prefix=history_read,
                find_open=lambda title: open_fix, create_card=create_card):
            result, said = close(repair_card._CloseOps())
        return result, sent, said

    def test_the_read_asks_for_the_lane_the_body_and_the_filing_time(self):
        result, sent, _ = self.drive()
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertEqual(len(sent["gql"]), 1)
        query, variables = sent["gql"][0]
        self.assertEqual(variables, {"id": CARD})
        for field in ("state { name }", "description", "createdAt"):
            self.assertIn(field, query)

    def test_the_cancel_is_conditional_on_the_lane_it_read(self):
        _, sent, _ = self.drive(state="Todo")
        self.assertEqual(sent["state"],
                         [(CARD, "Canceled", (), {"expect": ("Todo",)})])
        self.assertEqual(len(sent["comment"]), 1)
        self.assertEqual(sent["comment"][0][0], CARD)

    def test_a_cancel_the_guard_refused_posts_nothing(self):
        result, sent, _ = self.drive(moved=False)
        self.assertEqual(result, repair_card.CLOSE_ALREADY_FINISHED)
        self.assertEqual(sent["comment"], [])

    def test_a_finished_card_is_neither_moved_nor_said(self):
        result, sent, _ = self.drive(state="Done")
        self.assertEqual(result, repair_card.CLOSE_ALREADY_FINISHED)
        self.assertEqual(sent["state"], [])
        self.assertEqual(sent["comment"], [])

    def test_the_flaky_step_reads_and_files_through_linear_ops(self):
        result, sent, _ = self.drive(tests=[FLAKY_TEST],
                                     history=[earlier_card()])
        self.assertEqual(result, repair_card.CLOSED_SUPERSEDED)
        self.assertEqual(
            sent["history"],
            [(repair_card.TITLE_ANCHOR, REPO,
              NOW - timedelta(days=repair_card.FLAKY_WINDOW_DAYS))])
        self.assertEqual(sent["created"], [FIX_TITLE])
        self.assertIn(FIX_CARD, sent["comment"][0][1])

    def test_the_stubbed_calls_match_linear_ops_real_signatures(self):
        # The stubs above stand in for these; a rename or a dropped keyword
        # here would leave every test green and the real close broken.
        self.assertIn("expect", inspect.signature(
            linear_ops.cmd_state).parameters)
        for name, params in (
                ("repo_cards_by_title_prefix", {"repo_slug", "since"}),
                ("create_card", {"repo_slug", "labels", "lane"})):
            with self.subTest(name=name):
                self.assertLessEqual(params, set(inspect.signature(
                    getattr(linear_ops, name)).parameters))


def _ops_calls(*functions) -> set:
    """Every `ops.<name>(…)` call made inside the named `repair_card`
    functions."""
    with open(os.path.join(SCRIPTS, "repair_card.py")) as fh:
        tree = ast.parse(fh.read())
    return {
        n.func.attr
        for fn in tree.body
        if isinstance(fn, ast.FunctionDef) and fn.name in functions
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "ops"
    }


class TheCloseSeamCarriesEveryCallTest(unittest.TestCase):

    def test_close_ops_has_every_method_the_close_calls(self):
        calls = _ops_calls("close_superseded", "_flaky_fix")
        self.assertLessEqual(
            {"read_card", "cancel", "cmd_comment", "repair_card_history",
             "find_open", "create_card"}, calls)
        for name in sorted(calls):
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(repair_card._CloseOps, name, None)),
                    f"repair_card._CloseOps has no {name}")


if __name__ == "__main__":
    unittest.main()
