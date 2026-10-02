"""RED-first: the plan critics' gate readings see the whole epic thread (DRE-5639).

WHAT HAPPENED, 2026-10-02. DRE-5281's hand-back worked on two approved epics
at 11:44 PT, and each then broke for the same reason.

  * DRE-3698. The second critic passed the plan at 11:51 PT
    (`stage=post round=1 result=PASS`). At 11:52 PT the review route parked it
    in Triage: "the first critic's last reading sent it back". The first critic
    had passed it, `stage=pre round=2 result=PASS` at 23:52 PT on 10-01, on
    the attempt the CEO approved.
  * DRE-3624. Re-approved at 11:43 PT at the second critic's bound, which
    is the one re-entry that opens a fresh attempt (DRE-4115). No attempt
    opened, and at 11:53 PT the review it was handed to was recorded as
    "round 3" of the old, spent cycle and parked again.

ONE CAUSE. `linear_ops.py dump-comments --with-authors` served the 50 NEWEST
comments, which is `comment_records`' default window. Every reader in
`plan_critic` that plan.yml feeds from it is written against the whole thread:
`pre_passed` reads across attempts on purpose, and `current_cycle`,
`opens_fresh_attempt` and `post_release` all look back to a boundary. Busy
epics (planner-slot receipts, operator holds) push the records out of the
window:

  * On DRE-3698 the window at the decision began at 07:57 PT, so the 23:52 PT
    PASS was not in it.
  * On DRE-3624 post round 1 of the parked attempt was not in it. Two failed
    rounds were visible, so the bound read as unspent and `activate-cycle`
    answered `keep`.

THE FIXTURE is both live threads, read from Linear on 2026-10-02 and cut at
the moment each decision was taken. Every record is verbatim, and every other
comment keeps its position and its author. A fake serves the thread the way
Linear does (newest first, `after:` paging toward the oldest), so the window
and the paging under test are the production query's own.

Run: cd bureau-pipeline && python3 -m pytest tests/test_plan_critic_whole_thread.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import linear_ops  # noqa: E402
import plan_critic as pc  # noqa: E402
import rereview_watch  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "dre5639_handback_threads.json"
PLAN_CRITIC = ROOT / "scripts" / "plan_critic.py"
WORKFLOW = ROOT / ".github" / "workflows" / "plan.yml"

FLEET = "user-the-fleet-key"
OPERATOR = "user-bureau-tools"

REVIEW_HELD_PASS = "Review — the second critic passed a plan the first critic held"


def _live_thread(epic: str) -> list[dict]:
    """The epic's comments as Linear stores them: oldest→newest, `user.id`."""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))[epic]
    return [{"body": r["body"], "createdAt": r["createdAt"],
             "user": {"id": FLEET if r["by"] == "pipeline" else OPERATOR}}
            for r in rows]


_ARGS = re.compile(r"comments\(([^)]*)\)")


class FakeLinear:
    """One card's thread, ordered the way Linear orders it: NEWEST FIRST.
    `first: n` is the n newest; `after:` pages on toward the older."""

    def __init__(self, thread: list[dict]):
        self.newest_first = list(reversed(thread))
        self.pages = 0

    def gql(self, query, variables=None):
        v = variables or {}
        flat = " ".join(query.split())
        found = _ARGS.search(flat)
        if not found:
            if "viewer" in flat:
                return {"viewer": {"id": FLEET}}
            raise AssertionError(f"unexpected Linear query: {query}")
        args = {}
        for pair in found.group(1).split(","):
            key, value = (p.strip() for p in pair.split(":", 1))
            args[key] = v.get(value[1:]) if value.startswith("$") else value
        start = int(args["after"]) + 1 if args.get("after") else 0
        size = int(args["first"])
        nodes = self.newest_first[start:start + size]
        self.pages += 1
        return {"viewer": {"id": FLEET}, "issue": {"comments": {
            "pageInfo": {"hasNextPage": start + size < len(self.newest_first),
                         "endCursor": str(start + len(nodes) - 1)},
            "nodes": nodes,
        }}}


def dump_with_authors(epic: str) -> list[dict]:
    """`linear_ops.py dump-comments <epic> --with-authors`, exactly as plan.yml
    runs it: outside any sweep's pass, its stdout parsed as the JSON the next
    step reads on stdin."""
    fake = FakeLinear(_live_thread(epic))
    linear_ops.reset_pass_cache()
    buf = io.StringIO()
    with mock.patch.object(linear_ops, "gql", side_effect=fake.gql), \
            contextlib.redirect_stdout(buf):
        linear_ops.cmd_dump_comments(epic, "--with-authors")
    return json.loads(buf.getvalue())


def run_plan_critic(args: list[str], thread: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PLAN_CRITIC), *args],
        input=json.dumps(thread), capture_output=True, text=True, check=True,
    )


def decide_post(epic: str, thread: list, result_line: str) -> dict:
    """`plan_critic.py decide --stage post`, the second critic's decision step,
    over `thread` — its step outputs as a dict."""
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "plan-critic-post.md"
        result.write_text(result_line + "\n", encoding="utf-8")
        out = Path(tmp) / "github-output"
        run_plan_critic(["decide", "--stage", "post", "--epic", epic,
                         "--result-file", str(result),
                         "--github-output", str(out)], thread)
        outputs = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith(" "):
                key, value = line.split("=", 1)
                outputs.setdefault(key, value)
        return outputs


def opened_attempt(epic: str) -> list[dict]:
    """The two comments the route step's `open_attempt` posts, as the
    pipeline's own records."""
    return [
        {"body": pc.cycle_start_note(epic), "authored_by_pipeline": True},
        {"body": pc.cycle_marker(epic), "authored_by_pipeline": True},
    ]


class TheDumpTheCriticsReadIsTheWholeThread(unittest.TestCase):

    def test_with_authors_reads_past_the_fifty_comment_window(self):
        live = _live_thread("DRE-3624")
        self.assertGreater(len(live), linear_ops.COMMENT_WINDOW)
        rows = dump_with_authors("DRE-3624")
        self.assertEqual(len(rows), len(live),
                         "dump-comments --with-authors served a window, not the thread")
        self.assertEqual([r["body"] for r in rows], [c["body"] for c in live])
        self.assertEqual([r["authored_by_pipeline"] for r in rows],
                         [c["user"]["id"] == FLEET for c in live])

    def test_the_bare_dump_and_comment_records_keep_their_window(self):
        """The fix is scoped to the credential dump. The model-fallback
        selector's bare dump and `comment_records`' default stay the window
        they always were (DRE-3250)."""
        fake = FakeLinear(_live_thread("DRE-3624"))
        linear_ops.reset_pass_cache()
        buf = io.StringIO()
        with mock.patch.object(linear_ops, "gql", side_effect=fake.gql), \
                contextlib.redirect_stdout(buf):
            linear_ops.cmd_dump_comments("DRE-3624")
            bare = json.loads(buf.getvalue())
            records = linear_ops.comment_records("DRE-3624")
        self.assertEqual(len(bare), linear_ops.COMMENT_WINDOW)
        self.assertEqual(len(records), linear_ops.COMMENT_WINDOW)


class DRE3698TheFirstCriticsPassCounts(unittest.TestCase):
    """The hand-back opened a fresh attempt, the second critic passed it, and
    the first critic's PASS from the approved attempt must carry it to Green
    Light."""

    def test_the_first_critics_pass_was_outside_the_window_the_gate_read(self):
        live = _live_thread("DRE-3698")
        window = live[-linear_ops.COMMENT_WINDOW:]
        pre_pass = "plan-critic: stage=pre round=2 result=PASS collisions=0"
        self.assertIn(pre_pass, [c["body"] for c in live])
        self.assertNotIn(pre_pass, [c["body"] for c in window],
                         "the fixture no longer reproduces the 11:52 PT park")

    def test_the_second_critics_pass_goes_to_green_light(self):
        thread = dump_with_authors("DRE-3698")
        out = decide_post("DRE-3698", thread, pc.result_line(pc.PASS))
        self.assertEqual(out["result"], pc.PASS)
        self.assertEqual(out["round"], "1", "the missed review is round 1 of its attempt")
        self.assertEqual(out["pre_passed"], "true",
                         "the first critic passed the plan the CEO approved")


class DRE3624TheMissedReviewGetsItsOwnBudget(unittest.TestCase):
    """The re-approval at 11:43 PT was a person's ask at the second critic's
    bound, so the activate route opens a fresh attempt before it hands the
    epic back. The missed review is then round 1, and a pass reaches Green
    Light on the first critic's PASS from 11:07 PT on 10-01."""

    def test_the_re_approval_at_the_bound_opens_a_fresh_attempt(self):
        thread = dump_with_authors("DRE-3624")
        answer = run_plan_critic(["activate-cycle", "--epic", "DRE-3624",
                                  "--reason", ""], thread).stdout.strip()
        self.assertEqual(answer, "open")

    def test_the_review_it_is_handed_to_is_round_one_not_round_three(self):
        thread = dump_with_authors("DRE-3624") + opened_attempt("DRE-3624")
        out = decide_post("DRE-3624", thread, pc.result_line(
            pc.SEND_BACK, "a finding on the settled plan"))
        self.assertEqual(out["round"], "1")
        self.assertEqual(out["bound"], "false",
                         "the missed review was charged the old attempt's spent rounds")

    def test_a_pass_on_the_fresh_attempt_goes_to_green_light(self):
        thread = dump_with_authors("DRE-3624") + opened_attempt("DRE-3624")
        out = decide_post("DRE-3624", thread, pc.result_line(pc.PASS))
        self.assertEqual(out["result"], pc.PASS)
        self.assertEqual(out["pre_passed"], "true")


class AReApprovalBelowTheBoundKeepsItsAttempt(unittest.TestCase):
    """Unchanged by DRE-5639 (DRE-4115): a person re-approving after ONE
    send-back is asking for round 2, judged on whether the revision answered
    round 1. Only an approval at the bound refunds the budget."""

    def test_one_send_back_then_a_re_approval_keeps_the_attempt(self):
        thread = [
            {"body": pc.cycle_marker("DRE-9001"), "authored_by_pipeline": True},
            {"body": "plan-critic: stage=pre round=1 result=PASS collisions=0",
             "authored_by_pipeline": True},
            {"body": "plan-critic: stage=post round=1 result=SEND_BACK "
                     "collisions=0 — a gap", "authored_by_pipeline": True},
        ]
        self.assertFalse(pc.opens_fresh_attempt(thread, "DRE-9001", ""))


class TheHeldParkSaysWhatIsTrue(unittest.TestCase):
    """The review route's park when the second critic passes a plan the first
    critic has not passed. It never goes to Green Light, and its sentence
    names the first critic's actual record."""

    def test_a_first_critic_send_back_is_named_as_one(self):
        thread = [{"body": "plan-critic: stage=pre round=2 result=SEND_BACK "
                           "collisions=0 — a gap", "authored_by_pipeline": True}]
        note = pc.held_park_note(thread)
        self.assertIn("the first critic's last reading sent it back", note)
        self.assertIn("needs-human", note)
        self.assertIn(pc.REAPPROVE_HOW, note)
        self.assertNotIn("Green Light passed", note)

    def test_no_first_critic_record_is_never_called_a_send_back(self):
        for thread in ([], [{"body": "plan-critic: stage=pre round=1 result=PASS "
                                     "collisions=0",
                             "authored_by_pipeline": False}]):
            with self.subTest(thread=thread):
                note = pc.held_park_note(thread)
                self.assertNotIn("sent it back", note)
                self.assertIn("nothing on this epic records a reading by the "
                              "first critic", note)
                self.assertIn(pc.REAPPROVE_HOW, note)

    def test_an_unreadable_thread_is_named_unreadable_not_unrecorded(self):
        """`None` is a thread nobody could read (console-honesty rule 2): the
        note must not tell the operator the first critic never looked."""
        note = pc.held_park_note(None)
        self.assertIn("could not be read", note)
        self.assertNotIn("nothing on this epic records", note)
        self.assertNotIn("sent it back", note)
        self.assertIn(pc.REAPPROVE_HOW, note)

    def test_a_pass_on_record_claims_nothing_it_cannot_back(self):
        thread = [{"body": "plan-critic: stage=pre round=1 result=PASS "
                           "collisions=0", "authored_by_pipeline": True}]
        note = pc.held_park_note(thread)
        self.assertIn("result=PASS", note)
        self.assertIn("could not be confirmed", note)
        self.assertNotIn("sent it back", note)
        self.assertNotIn("nothing on this epic records", note)

    def test_an_unknown_result_word_is_named_as_not_a_pass(self):
        with mock.patch.object(pc, "pre_word", return_value="SOMETHING_NEW"):
            note = pc.held_park_note([])
        self.assertIn("says SOMETHING_NEW, which is not a pass", note)
        self.assertNotIn("sent it back", note)

    def test_the_cli_names_empty_or_invalid_stdin_unreadable(self):
        """plan.yml writes `: > "$THREAD"` when the dump fails, so an empty
        stdin is the failed read, and must not print the no-record line."""
        for stdin in ("", "not json", '{"not": "a list"}'):
            with self.subTest(stdin=stdin):
                done = subprocess.run(
                    [sys.executable, str(PLAN_CRITIC), "held-park"],
                    input=stdin, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("could not be read", done.stdout)
                self.assertNotIn("nothing on this epic records", done.stdout)

    def test_the_cli_reads_an_empty_list_as_read_and_unrecorded(self):
        """`[]` is a thread that WAS read and holds no first-critic record —
        the line between it and an unreadable thread."""
        printed = run_plan_critic(["held-park"], []).stdout
        self.assertIn("nothing on this epic records a reading by the first "
                      "critic", printed)
        self.assertNotIn("could not be read", printed)

    def test_the_park_step_reads_its_sentence_through_the_module(self):
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        found = [s for job in steps["jobs"].values()
                 for s in job.get("steps") or [] if s.get("name") == REVIEW_HELD_PASS]
        self.assertEqual(len(found), 1)
        run = found[0]["run"]
        self.assertIn("dump-comments", run)
        self.assertIn("--with-authors", run)
        self.assertIn("plan_critic.py held-park", run)
        self.assertNotIn("the first critic's last reading sent it back", run,
                         "the sentence is the module's, chosen by the record")
        self.assertNotIn('"Green Light"', run)

    def test_the_cli_prints_the_note(self):
        thread = [{"body": "plan-critic: stage=pre round=1 result=SEND_BACK "
                           "collisions=0 — a gap", "authored_by_pipeline": True}]
        printed = run_plan_critic(["held-park"], thread).stdout.strip()
        self.assertEqual(printed, pc.held_park_note(thread))


class TheReReviewWatchersOwnCliReadsTheWholeThread(unittest.TestCase):
    """In a sweep the watcher reads inside reconcile's pass, which pages the
    whole thread. Its `check` replay and `sweep` CLI run outside a pass, and
    must ask the live sweep's question over the same thread."""

    def test_check_reads_the_whole_thread(self):
        seen = []

        def records(epic, **kw):
            seen.append(kw)
            return []

        with mock.patch.object(linear_ops, "comment_records", side_effect=records), \
                mock.patch.object(rereview_watch, "_lane", return_value="Planning"), \
                contextlib.redirect_stdout(io.StringIO()):
            rereview_watch.main(["check", "DRE-3698"])
        self.assertEqual(seen, [{"whole_thread": True}])

    def test_sweep_reads_the_whole_thread(self):
        seen = []

        def records(epic, **kw):
            seen.append(kw)
            return []

        with mock.patch.object(linear_ops, "comment_records", side_effect=records), \
                mock.patch.object(rereview_watch, "_epics_in_flight",
                                  return_value=[{"identifier": "DRE-3698",
                                                 "state": "Planning"}]), \
                contextlib.redirect_stdout(io.StringIO()):
            rereview_watch.main(["sweep"])
        self.assertEqual(seen, [{"whole_thread": True}])


if __name__ == "__main__":
    unittest.main()
