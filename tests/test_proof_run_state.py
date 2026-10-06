"""Is the run on this proof card alive, dead, never started, or somebody
else's? (DRE-5922)

`scripts/proof_run_state.py` reads the card's thread and three GitHub facts
and answers one of seven states. Every state is pinned here by name, over
fixture threads and recorded reads, with no network:

  * an empty thread is `none`;
  * a remote branch, an open family pull request, and a fresh `⏳` line each
    alone are `someone-else`, with the sign named;
  * a receipt plus an `in_progress` run is `running`, and so is a receipt
    younger than thirty minutes nothing has followed yet;
  * a 31-minute-old receipt with no `🧠` line is `never-started`;
  * a `completed` run with no `⏳ 5/5` after the newest receipt is `dead`,
    naming id, conclusion and PT time — and still `dead` when the record pull
    request is open, with `record_pr` set;
  * a `⏳ 5/5` after the newest receipt is `finished`;
  * a raising read is `unknown`;
  * a comment by anyone but the pipeline's own key is never a receipt or a
    heartbeat, and `dispatches` is only the first-run budget.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import proof_run_state  # noqa: E402

REPO = "dreadnought-foundry/portico"
CARD = "DRE-5412"
VIEWER = "pipeline-key-user"
PERSON = "a-person"
NOW = datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc)   # 10:00 PT

RECORD_PRS = (f"repos/{REPO}/pulls?head=dreadnought-foundry:agent/{CARD}-"
              "proof-record&state=all&per_page=100")
OPEN_PRS = f"repos/{REPO}/pulls?state=open&per_page=100"
BRANCHES = f"repos/{REPO}/git/matching-refs/heads/agent/{CARD}-"
RUN = "35712345678"
RUN_PATH = f"repos/{REPO}/actions/runs/{RUN}"


def _at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace(
        "+00:00", "Z")


def comment(body: str, minutes_ago: float, user: str | None = VIEWER) -> dict:
    return {"body": body, "createdAt": _at(minutes_ago),
            "user": {"id": user} if user else None}


def receipt(minutes_ago: float, reason="first proof run: the siblings are "
            "released", count="dispatch 1 of 2", at="09:20 PT",
            user=VIEWER) -> dict:
    return comment(f"🔬 proof-run: dispatched a proof run at {at} — {reason} "
                   f"({count})", minutes_ago, user)


def attempt(minutes_ago: float, run=RUN, user=VIEWER) -> dict:
    return comment(
        "🧠 model-attempt: claude-opus-5-5 — engineer agent starting "
        "(turns=400, fresh start). Run: https://github.com/" + REPO +
        f"/actions/runs/{run}", minutes_ago, user)


def heartbeat(n: int, minutes_ago: float, label="plan", user=VIEWER) -> dict:
    return comment(f"⏳ {n}/5 {label}", minutes_ago, user)


class Reads:
    """A recorded GitHub; an unrecorded path is a failed read."""

    def __init__(self, table: dict | None = None):
        self.table = {RECORD_PRS: [], OPEN_PRS: [], BRANCHES: []}
        self.table.update(table or {})
        self.asked: list[str] = []

    def __call__(self, path: str):
        self.asked.append(path)
        if path not in self.table:
            raise RuntimeError(f"gh api {path}: HTTP 500 (not recorded)")
        value = self.table[path]
        if isinstance(value, Exception):
            raise value
        return value


def run_answer(status: str, conclusion: str | None = None,
               updated="2026-10-06T16:14:00Z") -> dict:
    return {"id": int(RUN), "status": status, "conclusion": conclusion,
            "updated_at": updated}


def record_pr(state="open", merged_at=None) -> dict:
    return {"number": 760, "state": state, "merged_at": merged_at,
            "html_url": f"https://github.com/{REPO}/pull/760",
            "head": {"ref": f"agent/{CARD}-proof-record"}}


def read_state(comments, read=None, viewer=VIEWER):
    return proof_run_state.reading(REPO, CARD, comments, viewer,
                                   read=read or Reads(), now=NOW)


class Nobody(unittest.TestCase):

    def test_an_empty_thread_is_none(self):
        got = read_state([])
        self.assertEqual(got.state, "none")
        self.assertEqual(got.dispatches, 0)
        self.assertEqual(got.receipts, [])
        self.assertIsNone(got.run_id)
        self.assertIsNone(got.record_pr)
        self.assertEqual(len(got.lines), 1)
        self.assertTrue(got.lines[0].startswith("none — "), got.lines[0])

    def test_an_old_heartbeat_is_not_a_sign(self):
        got = read_state([heartbeat(3, 25 * 60), attempt(25 * 60)])
        self.assertEqual(got.state, "none")

    def test_a_branch_of_another_card_is_not_in_the_family(self):
        """`agent/DRE-5412-` never matches `agent/DRE-54120-…`; the open-PR
        list is filtered by the same family prefix."""
        read = Reads({OPEN_PRS: [{"number": 9, "head": {
            "ref": "agent/DRE-54120-other"}}]})
        self.assertEqual(read_state([], read).state, "none")


class SomeoneElse(unittest.TestCase):
    """No receipt, but one sign that somebody is on it — each alone."""

    def test_a_remote_branch_alone(self):
        read = Reads({BRANCHES: [{"ref": f"refs/heads/agent/{CARD}-hygiene-proof"}]})
        got = read_state([], read)
        self.assertEqual(got.state, "someone-else")
        self.assertEqual(got.lines, [
            f"someone-else — branch agent/{CARD}-hygiene-proof exists on the "
            "remote and no proof-run receipt names it"])

    def test_an_open_family_pull_request_alone(self):
        read = Reads({OPEN_PRS: [{"number": 761, "head": {
            "ref": f"agent/{CARD}-by-hand"}}]})
        got = read_state([], read)
        self.assertEqual(got.state, "someone-else")
        self.assertEqual(len(got.lines), 1)
        self.assertIn("pull request #761", got.lines[0])
        self.assertIn(f"agent/{CARD}-by-hand", got.lines[0])

    def test_a_fresh_heartbeat_alone(self):
        got = read_state([heartbeat(2, 90)])
        self.assertEqual(got.state, "someone-else")
        self.assertEqual(len(got.lines), 1)
        self.assertIn("⏳", got.lines[0])
        self.assertIn("08:30 PT", got.lines[0])

    def test_a_fresh_model_attempt_alone_names_its_run(self):
        got = read_state([attempt(60)])
        self.assertEqual(got.state, "someone-else")
        self.assertIn("🧠 model-attempt", got.lines[0])
        self.assertEqual(got.run_id, RUN)

    def test_a_persons_heartbeat_is_not_a_sign(self):
        got = read_state([heartbeat(2, 90, user=PERSON)])
        self.assertEqual(got.state, "none")


class Running(unittest.TestCase):

    def test_a_receipt_plus_an_in_progress_run(self):
        read = Reads({RUN_PATH: run_answer("in_progress")})
        got = read_state([receipt(50), attempt(45), heartbeat(1, 40)], read)
        self.assertEqual(got.state, "running")
        self.assertEqual(got.run_id, RUN)
        self.assertIn(RUN, got.lines[0])
        self.assertIn("in_progress", got.lines[0])

    def test_a_queued_run(self):
        read = Reads({RUN_PATH: run_answer("queued")})
        self.assertEqual(read_state([receipt(50), attempt(45)], read).state,
                         "running")

    def test_a_young_receipt_nothing_has_followed_is_a_queued_run(self):
        got = read_state([receipt(12)])
        self.assertEqual(got.state, "running")
        self.assertIsNone(got.run_id)
        self.assertIn("12 minutes", got.lines[0])


class NeverStarted(unittest.TestCase):

    def test_a_31_minute_old_receipt_with_no_model_attempt(self):
        got = read_state([receipt(31)])
        self.assertEqual(got.state, "never-started")
        self.assertIn("31 minutes", got.lines[0])

    def test_an_attempt_before_the_newest_receipt_does_not_count(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure")})
        got = read_state([receipt(300), attempt(290), receipt(40,
                          reason="second dispatch: the first did not finish",
                          count="dispatch 2 of 2")], read)
        self.assertEqual(got.state, "never-started")

    def test_a_persons_model_attempt_does_not_start_it(self):
        got = read_state([receipt(40), attempt(35, user=PERSON)])
        self.assertEqual(got.state, "never-started")


class Dead(unittest.TestCase):

    def test_a_completed_run_with_no_five_of_five(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure")})
        got = read_state([receipt(120), attempt(110), heartbeat(1, 100),
                          heartbeat(3, 60, "green")], read)
        self.assertEqual(got.state, "dead")
        self.assertEqual(got.run_id, RUN)
        self.assertIsNone(got.record_pr)
        self.assertEqual(got.lines, [
            f"dead — run {RUN} ended failure at 09:14 PT with no record"])

    def test_still_dead_when_the_record_pull_request_is_open(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure"),
                      RECORD_PRS: [record_pr()]})
        got = read_state([receipt(120), attempt(110)], read)
        self.assertEqual(got.state, "dead")
        self.assertEqual(got.record_pr["number"], 760)
        self.assertEqual(got.record_pr["state"], "open")
        self.assertEqual(got.lines, [
            f"dead — run {RUN} ended failure at 09:14 PT with no record; the "
            "record pull request #760 is open"])

    def test_the_newest_attempt_names_the_run(self):
        read = Reads({RUN_PATH: run_answer("completed", "cancelled"),
                      f"repos/{REPO}/actions/runs/111": run_answer("completed",
                                                                   "failure")})
        got = read_state([receipt(120), attempt(110, run="111"), attempt(90)],
                         read)
        self.assertEqual(got.state, "dead")
        self.assertEqual(got.run_id, RUN)
        self.assertIn("ended cancelled", got.lines[0])

    def test_a_merged_record_is_named_as_merged(self):
        read = Reads({RUN_PATH: run_answer("completed", "success"),
                      RECORD_PRS: [record_pr("closed",
                                             "2026-10-05T10:00:00Z")]})
        got = read_state([receipt(120), attempt(110)], read)
        self.assertEqual(got.state, "dead")
        self.assertEqual(got.record_pr["state"], "merged")

    def test_a_closed_unmerged_record_is_no_record(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure"),
                      RECORD_PRS: [record_pr("closed")]})
        self.assertIsNone(read_state([receipt(120), attempt(110)],
                                     read).record_pr)


class Finished(unittest.TestCase):

    def test_a_five_of_five_after_the_newest_receipt(self):
        read = Reads({RUN_PATH: run_answer("completed", "success"),
                      RECORD_PRS: [record_pr()]})
        got = read_state([receipt(120), attempt(110),
                          heartbeat(5, 20, "PR opened")], read)
        self.assertEqual(got.state, "finished")
        self.assertEqual(got.record_pr["number"], 760)
        self.assertTrue(got.lines[0].startswith("finished — "))

    def test_a_five_of_five_before_the_newest_receipt_is_not_finished(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure")})
        got = read_state([receipt(300), attempt(290), heartbeat(5, 280),
                          receipt(120, reason="re-run after the critic's "
                                  "findings at 07:00 PT", count="re-run 1 of 2"),
                          attempt(110)], read)
        self.assertEqual(got.state, "dead")

    def test_a_persons_five_of_five_does_not_finish_it(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure")})
        got = read_state([receipt(120), attempt(110),
                          heartbeat(5, 20, user=PERSON)], read)
        self.assertEqual(got.state, "dead")


class Unknown(unittest.TestCase):

    def test_a_raising_run_read(self):
        read = Reads({RUN_PATH: RuntimeError("HTTP 502: Bad Gateway")})
        got = read_state([receipt(120), attempt(110)], read)
        self.assertEqual(got.state, "unknown")
        self.assertIn("HTTP 502", got.lines[0])

    def test_a_raising_branch_read_is_never_none(self):
        read = Reads({BRANCHES: RuntimeError("HTTP 500")})
        self.assertEqual(read_state([], read).state, "unknown")

    def test_a_raising_record_read(self):
        read = Reads({RECORD_PRS: RuntimeError("HTTP 500")})
        self.assertEqual(read_state([receipt(12)], read).state, "unknown")

    def test_no_viewer_cannot_tell_the_pipeline_from_a_person(self):
        got = read_state([receipt(12)], viewer=None)
        self.assertEqual(got.state, "unknown")


class Receipts(unittest.TestCase):

    def test_a_receipt_by_another_user_is_not_counted(self):
        got = read_state([receipt(12, user=PERSON)])
        self.assertEqual(got.receipts, [])
        self.assertEqual(got.dispatches, 0)
        self.assertEqual(got.state, "none")

    def test_dispatches_is_only_the_first_run_budget(self):
        read = Reads({RUN_PATH: run_answer("in_progress")})
        got = read_state([
            receipt(600, reason="first proof run: the siblings are released",
                    count="dispatch 1 of 2", at="2026-10-05 23:00 PT"),
            receipt(300, reason="re-run after the critic's findings at "
                    "2026-10-06 04:00 PT", count="re-run 1 of 2",
                    at="2026-10-06 05:00 PT"),
            receipt(60, reason="re-run after the CEO's answer at 2026-10-06 "
                    "08:50 PT", count="after the CEO's answer",
                    at="2026-10-06 09:00 PT"),
            attempt(55),
        ], read)
        self.assertEqual(got.dispatches, 1)
        self.assertEqual(got.receipts, [
            proof_run_state.Receipt(
                "first proof run: the siblings are released",
                "dispatch 1 of 2", "2026-10-05 23:00 PT"),
            proof_run_state.Receipt(
                "re-run after the critic's findings at 2026-10-06 04:00 PT",
                "re-run 1 of 2", "2026-10-06 05:00 PT"),
            proof_run_state.Receipt(
                "re-run after the CEO's answer at 2026-10-06 08:50 PT",
                "after the CEO's answer", "2026-10-06 09:00 PT"),
        ])
        self.assertEqual(got.state, "running")

    def test_second_dispatch_counts_against_the_budget(self):
        got = read_state([
            receipt(600), receipt(40, reason="second dispatch: the first "
                                  "never started", count="dispatch 2 of 2")])
        self.assertEqual(got.dispatches, 2)

    def test_a_line_that_is_not_the_receipt_shape_is_not_a_receipt(self):
        got = read_state([comment("🔬 proof-run: held — the release is not "
                                  "live yet", 12)])
        self.assertEqual(got.receipts, [])


class Cli(unittest.TestCase):

    def _run(self, argv, read, comments, viewer=VIEWER):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proof_run_state.main(
                argv, read=read, thread=lambda card: (comments, viewer),
                now=NOW)
        return code, out.getvalue().splitlines()

    def test_dead_prints_its_line_and_exits_0(self):
        read = Reads({RUN_PATH: run_answer("completed", "failure"),
                      RECORD_PRS: [record_pr()]})
        code, printed = self._run(["check", "--repo", REPO, "--card", CARD],
                                  read, [receipt(120), attempt(110)])
        self.assertEqual(code, 0)
        self.assertEqual(printed, [
            f"proof-run-state: dead — run {RUN} ended failure at 09:14 PT "
            "with no record; the record pull request #760 is open"])

    def test_unknown_exits_2(self):
        read = Reads({RUN_PATH: RuntimeError("HTTP 502")})
        code, printed = self._run(["check", "--repo", REPO, "--card", CARD],
                                  read, [receipt(120), attempt(110)])
        self.assertEqual(code, 2)
        self.assertTrue(printed[0].startswith("proof-run-state: unknown — "))

    def test_an_unreadable_thread_is_unknown(self):
        def broken(card):
            raise RuntimeError("Linear: 503")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proof_run_state.main(
                ["check", "--repo", REPO, "--card", CARD], read=Reads(),
                thread=broken, now=NOW)
        self.assertEqual(code, 2)
        self.assertIn("Linear: 503", out.getvalue())


if __name__ == "__main__":
    unittest.main()
