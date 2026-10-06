"""The promise nothing kept: a post-approval send-back with no round 2 (DRE-4492).

The failure this module detects is SILENCE. An epic carries a
`plan-critic: stage=post … result=SEND_BACK` record, a 🔁 receipt saying the
pipeline will run the review again, and then nothing — no round-2 record, no
tombstone, the children held under `plan-critic-post-sent-back`, and nothing on
any board saying the promise was not kept. DRE-4025 sat that way for 33 hours,
DRE-3964 for 12, DRE-4083 for 3.5, DRE-4425 for 8h43m, DRE-4396 for 21+; every
one was found by a person reading a sweep log.

The thread every case below is built from is DRE-4025's, as it stood on
2026-09-15: a cycle-start boundary, round 1's SEND_BACK record at 17:43:29Z,
the 🔁 receipt promising the re-review, and the 🤖 duplicate-skipped receipt
that is why it never ran.

One section per acceptance criterion:

  1. `overdue` on the real thread — overdue at 18:45Z, quiet at 17:50Z, and
     quiet for every state that is somebody else's business (a round 2, a
     tombstone, the bound, the Green Light lane).
  2. The credential: a record the pipeline did not write is not a record, in
     both directions — a forged SEND_BACK cannot trigger the notice and a
     forged round 2 cannot silence it.
  3. Idempotency: one notice per round, a fresh notice on a fresh cycle, and
     the log line on every sweep either way.
  4. `notice` — what it opens with, what it ends with, and the one line it
     must never be.
  5. `report` — one thread read per epic per sweep, a reader that raises
     skipping only its own epic, and what it returns.
  6. The CLI: `check` writes nothing and exits 0 either way, and `sweep`
     reports over the epics `linear_ops` names — only those it named an
     identifier for.
  7. The wiring: the sweep calls `report` inside a `_phase(` block, and the
     grace constant stays out of `reconcile.REQUIRED_ENV`.
  8. The replay (DRE-4758): `check --now` reads the lane off the epic's state
     history and the thread as it stood, so an epic that has since moved to
     Done — or whose later rounds settled the question — replays as it was.

DRE-5278 (epic DRE-5268) widens the watcher to Planning, where the second
critic's rounds now happen, and teaches it to act instead of only speaking:

  9. The lanes: Planning and In Progress are watched; Green Light and Todo are
     quiet with a line saying why.
 10. `under_review(records, epic)` — the predicate the reconcile card reads.
 11. Handed off, no review (Planning).
 12. Sent back, no re-review (Planning), including the fifteen-minute sweep.
 13. Died, no retry (Planning).
 14. In Progress: sent back, no re-review — the old-rule silence, kept.
 15. The planner line is not silence.
 16. What every notice may and may not say, and the firing classifier.
 17. `linear_ops.cmd_epics_in_flight(*flags, states=None)` and `--sight`.

Run: cd bureau-pipeline && python3 -m pytest tests/test_rereview_watch.py -v
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
# `reconcile` reads these at import (section 7 reads the sweep's own source).
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import plan_critic as pc  # noqa: E402
import planner_queue  # noqa: E402
import rereview_watch as rw  # noqa: E402
import review_rerun  # noqa: E402

EPIC = "DRE-4025"

#: When round 1 was sent back, and the two clocks the card reads it against.
SENT_BACK_AT = "2026-09-15T17:43:29Z"
LATER = "2026-09-15T18:45:00Z"      # 61.5 minutes — past the 45-minute grace
SOON = "2026-09-15T17:50:00Z"       # 6.5 minutes — the review could still be running

FINDING = (
    "DRE-4022 and DRE-4023 both edit console/backend/receipts.py with no "
    "relation between them"
)

#: What plan.yml posts after a post-approval send-back whose re-plan changed no
#: card — the receipt this module exists to hold the pipeline to.
RE_REVIEW_RECEIPT = (
    "🔁 The post-approval review found a gap and the plan has been revised to "
    "answer it — the same cards you approved, none added and none removed. The "
    f"critic's finding: {FINDING}. The review is being run again by the "
    "pipeline, on its own, as round 2 — nothing here is yours to decide."
)

#: And why it never ran on DRE-4025: the continuation was skipped as its
#: parent's duplicate (the race DRE-4573 has since closed).
DUPLICATE_SKIPPED = (
    "🤖 Duplicate dispatch skipped: an agent-plan run for this epic is already "
    "in flight. The re-review was not started. Run: "
    "https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/1"
)


def _rec(body: str, created_at: str | None = None, pipeline: bool = True) -> dict:
    """One row as `linear_ops.comment_records` returns it."""
    return {
        "body": body,
        "authored_by_pipeline": pipeline,
        "created_at": created_at,
    }


def _round(n: int = 1, result: str = pc.SEND_BACK, reason: str = FINDING,
           open_count: int | None = None) -> str:
    return pc.marker(pc.STAGE_POST, n, result, reason, open_count=open_count)


def _tombstone(run: str = "34301000001") -> str:
    return pc.death_marker(pc.STAGE_POST, run, 1, "post", "error_max_turns", 141, 140)


def thread(*extra) -> list[dict]:
    """DRE-4025's thread as it stood on 2026-09-15, plus whatever `extra` adds.

    Each `extra` is either a record dict (its own stamp and authorship) or a
    plain string, which is read as a pipeline-authored comment posted after the
    send-back.
    """
    rows = [
        _rec(pc.cycle_start_note(EPIC), "2026-09-15T17:21:02Z"),
        _rec(pc.cycle_marker(EPIC), "2026-09-15T17:21:04Z"),
        _rec(_round(1), SENT_BACK_AT),
        _rec(RE_REVIEW_RECEIPT, "2026-09-15T17:43:31Z"),
        _rec(DUPLICATE_SKIPPED, "2026-09-15T17:43:58Z"),
    ]
    for item in extra:
        rows.append(item if isinstance(item, dict)
                    else _rec(item, "2026-09-15T18:00:00Z"))
    return rows


def _overdue(records=None, lane=pc.APPROVAL_LANE, now=LATER, grace=45):
    return rw.overdue(records if records is not None else thread(),
                      EPIC, lane, now, grace)


# --- 1. The reading ----------------------------------------------------------

class TheReading(unittest.TestCase):
    """`overdue` on DRE-4025's own thread."""

    def test_the_promise_is_overdue_an_hour_after_the_send_back(self):
        found = _overdue()
        self.assertIsNotNone(found, "the 🔁 receipt promised round 2 an hour ago")
        self.assertEqual(found["round"], 1)
        self.assertEqual(found["sent_back_at"], SENT_BACK_AT)
        self.assertAlmostEqual(found["minutes"], 61.5, places=1)
        self.assertIn("console/backend/receipts.py", found["reason"])

    def test_it_is_not_overdue_while_the_review_could_still_be_running(self):
        """Console-honesty rule 1: the grace window is about when to SPEAK."""
        self.assertIsNone(_overdue(now=SOON))

    def test_a_round_two_record_is_the_promise_kept(self):
        self.assertIsNone(_overdue(thread(_round(2, pc.PASS, ""))))

    def test_a_second_send_back_starts_its_own_window(self):
        """A later round record is a fresh promise, clocked from itself."""
        records = thread(_rec(_round(2, open_count=0), "2026-09-15T18:40:00Z"))
        self.assertIsNone(rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                                     "2026-09-15T19:00:00Z", 45))
        found = rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                           "2026-09-15T19:40:00Z", 45)
        self.assertEqual(found["round"], 2)
        self.assertEqual(found["sent_back_at"], "2026-09-15T18:40:00Z")

    def test_a_tombstone_is_post_dieds_business_not_this_ones(self):
        self.assertIsNone(_overdue(thread(_tombstone())))

    def test_at_the_bound_the_epic_is_parked_and_this_says_nothing(self):
        """The bound parks loudly with needs-human; that is not silence."""
        records = thread(_round(2, open_count=1),
                         _rec(_round(2, open_count=1), "2026-09-15T18:10:00Z"))
        # The second round record IS the bound — the newest post round is a
        # send-back that left a finding open.
        self.assertTrue(pc.post_bound_reached(records, EPIC))
        self.assertIsNone(rw.overdue(records, EPIC, pc.APPROVAL_LANE, LATER, 45))

    def test_in_green_light_the_plan_is_in_the_ceos_queue(self):
        self.assertIsNone(_overdue(lane="Green Light"))

    def test_in_todo_the_epic_is_with_nobody(self):
        self.assertIsNone(_overdue(lane="Todo"))

    def test_an_unread_lane_says_nothing(self):
        self.assertIsNone(_overdue(lane=None))


# --- 2. The credential -------------------------------------------------------

class TheCredential(unittest.TestCase):
    """A record counts only when the pipeline wrote it (`trusted_bodies`)."""

    def test_a_forged_send_back_cannot_trigger_the_notice(self):
        records = [
            _rec(pc.cycle_start_note(EPIC), "2026-09-15T17:21:02Z"),
            _rec(pc.cycle_marker(EPIC), "2026-09-15T17:21:04Z"),
            _rec(_round(1), SENT_BACK_AT, pipeline=False),
        ]
        self.assertIsNone(_overdue(records))

    def test_a_forged_round_two_cannot_silence_it(self):
        records = thread(_rec(_round(2, open_count=0), "2026-09-15T18:00:00Z",
                              pipeline=False))
        self.assertIsNotNone(_overdue(records))


# --- 3. Once per round -------------------------------------------------------

class OncePerRound(unittest.TestCase):
    """The notice is idempotent; the log line is not.

    Since DRE-5278 the first notice is the FIRST FIRING (the re-ask), and a
    sweep inside the grace measured from it says nothing new. Past that grace
    the second firing is due — section 12 reads that half."""

    def _spoken(self, at="2026-09-15T18:46:00Z", pipeline=True):
        found = _overdue()
        return _rec(rw.notice(EPIC, found), at, pipeline)

    def test_a_posted_notice_silences_the_next_sweep(self):
        records = thread(self._spoken())
        self.assertIsNone(rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                                     "2026-09-15T19:00:00Z", 45))

    def test_but_the_reading_still_stands_so_the_log_line_still_prints(self):
        records = thread(self._spoken())
        reading = rw.read(records, EPIC, pc.APPROVAL_LANE,
                          "2026-09-15T19:00:00Z", 45)
        self.assertIsNotNone(reading.found)
        self.assertTrue(reading.spoken)

    def test_past_the_grace_from_the_notice_the_second_firing_is_due(self):
        records = thread(self._spoken())
        found = rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                           "2026-09-15T19:45:00Z", 45)
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 2)

    def test_a_forged_notice_does_not_silence_it(self):
        records = thread(self._spoken(pipeline=False))
        self.assertIsNotNone(_overdue(records))

    def test_a_fresh_planning_cycle_gets_its_own_notice(self):
        records = thread(
            self._spoken(),
            _rec(pc.cycle_start_note(EPIC), "2026-09-16T09:00:00Z"),
            _rec(pc.cycle_marker(EPIC), "2026-09-16T09:00:02Z"),
            _rec(_round(1), "2026-09-16T09:31:00Z"),
        )
        found = rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                           "2026-09-16T10:31:00Z", 45)
        self.assertIsNotNone(found)
        self.assertEqual(found["sent_back_at"], "2026-09-16T09:31:00Z")

    def test_a_comment_linear_gave_no_stamp_for_is_unknown_not_overdue(self):
        for stamp in (None, "", "not-a-time"):
            records = [
                _rec(pc.cycle_start_note(EPIC), "2026-09-15T17:21:02Z"),
                _rec(pc.cycle_marker(EPIC), "2026-09-15T17:21:04Z"),
                _rec(_round(1), stamp),
            ]
            with self.subTest(stamp=stamp):
                self.assertIsNone(_overdue(records))


# --- 4. What the CEO reads ---------------------------------------------------

class TheNotice(unittest.TestCase):

    def setUp(self):
        self.found = _overdue()
        self.body = rw.notice(EPIC, self.found)

    def test_it_opens_with_the_tag_the_blocker_gate_reads(self):
        self.assertTrue(
            self.body.startswith(f"🚨 {rw.REREVIEW_MISSING_TAG}: {EPIC}"),
            self.body.splitlines()[0],
        )
        self.assertEqual(rw.REREVIEW_MISSING_TAG, "plan-critic-rereview-missing")

    def test_it_names_the_round_the_time_and_the_minutes(self):
        first = self.body.splitlines()[0]
        self.assertIn("(round 1)", first)
        self.assertIn("2026-09-15 17:43 UTC", first)
        self.assertIn("61 minutes", first)

    def test_it_quotes_the_critics_reason_and_the_receipt_it_names(self):
        self.assertIn("console/backend/receipts.py", self.body)
        self.assertIn("🔁", self.body)

    def test_it_says_the_pipeline_asked_for_the_review_again(self):
        """DRE-5278: the first firing re-asks, and the notice says it did."""
        self.assertIn("asked for the review again", self.body)
        self.assertNotIn("parked in Triage", self.body)

    def test_in_progress_it_names_the_act_inside_a_sentence(self):
        """The one notice that may name the act (DRE-5275's contract): a
        person can ask too, and the act is quoted, never a line of its own."""
        self.assertIn(review_rerun.RERUN_REVIEW_ACT, self.body)

    def test_it_is_never_itself_the_re_run_act(self):
        """DRE-3286: the relay matches the WHOLE body, so a notice quoting the
        act on a line of its own would re-run the review every time it posted."""
        for line in self.body.splitlines():
            self.assertFalse(review_rerun.is_rerun_act(line), line)
        self.assertFalse(review_rerun.is_rerun_act(self.body))

    def test_the_log_line_says_what_is_missing(self):
        self.assertEqual(
            rw.log_line(EPIC, self.found),
            f"rereview-missing: {EPIC} round 1 sent back 61 min ago — "
            "no round 2 and no tombstone",
        )


# --- 5. The sweep's half -----------------------------------------------------

class _Reader:
    """A thread reader that counts its reads and can be told to fail."""

    def __init__(self, threads: dict, raises: set | None = None):
        self.threads = threads
        self.raises = raises or set()
        self.reads: list[str] = []

    def __call__(self, epic):
        self.reads.append(epic)
        if epic in self.raises:
            raise RuntimeError("Linear said no")
        return self.threads.get(epic)


def _parse_dispatch(argv) -> dict:
    """`review_rerun.py dispatch` argv as `{flag: value}`."""
    assert argv[0] == "dispatch", argv
    out = {}
    rest = list(argv[1:])
    while rest:
        flag = rest.pop(0)
        out[flag.lstrip("-").replace("-", "_")] = rest.pop(0)
    return out


class _Sweep:
    """`report` with Linear and the dispatch recorded.

    One MagicMock carries every Linear write, so `mock_calls` holds them in
    the order they were made — the label-before-state assertion reads it."""

    def __init__(self, dispatch_rc: int = 0):
        self.linear = mock.MagicMock()
        self.posted: list[tuple[str, str]] = []
        self.dispatches: list[dict] = []
        self.dispatch_rc = dispatch_rc
        self.linear.cmd_comment.side_effect = (
            lambda ident, body, *f: self.posted.append((ident, body)))

    def _dispatch(self, argv):
        self.dispatches.append(_parse_dispatch(argv))
        return self.dispatch_rc

    def __call__(self, reader, lanes, now=LATER):
        buf = io.StringIO()
        # REPO pinned here: another test module may have set it first, and
        # `report` dispatches to the sweep's own repository.
        with mock.patch.object(rw, "linear_ops", self.linear), \
                mock.patch.object(rw.review_rerun, "main",
                                  side_effect=self._dispatch), \
                mock.patch.dict(os.environ, {"REPO": REPO}), \
                redirect_stdout(buf), redirect_stderr(io.StringIO()):
            spoke = rw.report(list(lanes), reader, lanes.get, now)
        return spoke, buf.getvalue()

    def writes(self) -> list[str]:
        return [c[0] for c in self.linear.mock_calls
                if c[0] in ("add_label", "cmd_state", "cmd_comment")]


class TheReport(unittest.TestCase):

    def setUp(self):
        self.sweep = _Sweep()
        self.posted = self.sweep.posted
        self.linear = self.sweep.linear

    def _report(self, reader, lanes, now=LATER):
        return self.sweep(reader, lanes, now)

    def test_it_speaks_once_and_reads_each_thread_once(self):
        reader = _Reader({EPIC: thread(), "DRE-4083": thread()})
        lanes = {EPIC: pc.APPROVAL_LANE, "DRE-4083": pc.APPROVAL_LANE}
        spoke, out = self._report(reader, lanes)
        self.assertEqual(spoke, [EPIC, "DRE-4083"])
        self.assertEqual(sorted(reader.reads), sorted(lanes))
        self.assertEqual(len(reader.reads), 2)
        self.assertEqual(len(self.posted), 2)
        self.assertIn(f"rereview-missing: {EPIC} round 1", out)

    def test_an_epic_not_in_progress_is_never_even_read(self):
        reader = _Reader({EPIC: thread()})
        spoke, out = self._report(reader, {EPIC: "Green Light"})
        self.assertEqual(spoke, [])
        self.assertEqual(reader.reads, [])
        self.assertEqual(self.posted, [])

    def test_an_epic_in_planning_is_read(self):
        """DRE-5278: the second critic's rounds happen in Planning now."""
        reader = _Reader({EPIC: thread()})
        spoke, _ = self._report(reader, {EPIC: pc.REVIEW_LANE})
        self.assertEqual(reader.reads, [EPIC])
        self.assertEqual(spoke, [EPIC])

    def test_a_failed_dispatch_posts_no_notice_and_goes_red(self):
        """DRE-2034: no receipt on an unconfirmed dispatch."""
        sweep = _Sweep(dispatch_rc=1)
        with self.assertRaises(rw.NoticeFailed):
            sweep(_Reader({EPIC: thread()}), {EPIC: pc.APPROVAL_LANE})
        self.assertEqual(len(sweep.dispatches), 1)
        self.assertEqual(sweep.posted, [])

    def test_a_thread_reader_that_raises_skips_only_its_own_epic(self):
        reader = _Reader({EPIC: thread(), "DRE-4083": thread()},
                         raises={"DRE-4083"})
        lanes = {EPIC: pc.APPROVAL_LANE, "DRE-4083": pc.APPROVAL_LANE}
        spoke, _ = self._report(reader, lanes)
        self.assertEqual(spoke, [EPIC])

    def test_an_unreadable_thread_is_unknown_not_a_missing_re_review(self):
        """`epic_thread` answers None when Linear cannot say."""
        reader = _Reader({EPIC: None})
        spoke, _ = self._report(reader, {EPIC: pc.APPROVAL_LANE})
        self.assertEqual(spoke, [])
        self.assertEqual(self.posted, [])

    def test_the_log_line_prints_on_every_sweep_the_notice_only_once(self):
        spoken = _rec(rw.notice(EPIC, _overdue()), "2026-09-15T18:46:00Z")
        reader = _Reader({EPIC: thread(spoken)})
        spoke, out = self._report(reader, {EPIC: pc.APPROVAL_LANE},
                                  now="2026-09-15T19:00:00Z")
        self.assertEqual(spoke, [])
        self.assertEqual(self.posted, [])
        self.assertEqual(self.sweep.dispatches, [])
        self.assertIn(f"rereview-missing: {EPIC} round 1", out)

    def test_a_failed_post_is_raised_so_the_sweep_goes_red(self):
        """DRE-1254: never exit 0 on a write we claimed to make and didn't."""
        self.linear.cmd_comment.side_effect = RuntimeError("Linear 500")
        reader = _Reader({EPIC: thread()})
        with self.assertRaises(rw.NoticeFailed):
            self._report(reader, {EPIC: pc.APPROVAL_LANE})


# --- 6. The CLI --------------------------------------------------------------

class TheCli(unittest.TestCase):

    def _check(self, records, lane=pc.APPROVAL_LANE, now=LATER):
        linear = mock.MagicMock()
        linear.comment_records.return_value = records
        linear.gql.return_value = {"issue": {"state": {"name": lane}}}
        buf = io.StringIO()
        with mock.patch.object(rw, "linear_ops", linear), redirect_stdout(buf):
            code = rw.main(["check", EPIC, "--now", now])
        return code, buf.getvalue(), linear

    def test_check_prints_overdue_with_the_reason_and_writes_nothing(self):
        code, out, linear = self._check(thread())
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("overdue"), out)
        self.assertIn("console/backend/receipts.py", out)
        linear.cmd_comment.assert_not_called()

    def test_check_prints_quiet_with_the_reason_and_writes_nothing(self):
        code, out, linear = self._check(thread(), now=SOON)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("quiet"), out)
        self.assertIn("45", out)
        linear.cmd_comment.assert_not_called()

    def _sweep(self, rows, now=LATER):
        posted: list[str] = []
        linear = mock.MagicMock()
        linear.cmd_epics_in_flight.side_effect = (
            lambda *f, **kw: print(json.dumps(rows)))
        linear.comment_records.return_value = thread()
        linear.cmd_comment.side_effect = lambda i, b, *f: posted.append(i)
        buf = io.StringIO()
        with mock.patch.object(rw, "linear_ops", linear), \
                mock.patch.object(rw.review_rerun, "main", return_value=0), \
                redirect_stdout(buf):
            code = rw.main(["sweep", "--now", now])
        read = [c.args[0] for c in linear.comment_records.call_args_list]
        return code, buf.getvalue(), posted, read

    def test_sweep_runs_the_report_over_every_epic_in_flight(self):
        """The operator's seam. The epic list is `linear_ops`' own reader, so
        the lanes and the has-children test cannot drift from the critic's."""
        rows = [
            {"identifier": EPIC, "title": "e", "state": pc.APPROVAL_LANE},
            {"identifier": "DRE-4083", "title": "e", "state": "Green Light"},
        ]
        code, out, posted, _ = self._sweep(rows)
        self.assertEqual(code, 0)
        self.assertEqual(posted, [EPIC])
        self.assertIn("2 epic(s) in flight, spoke on 1", out)

    def test_sweep_drops_a_row_linear_named_no_identifier_for(self):
        """A row with no identifier is nothing to read a thread for. Carried
        into `report` it is a `None` epic, and `report` sorts the epics it is
        handed — so the whole sweep dies on it rather than skipping the row."""
        rows = [
            {"identifier": EPIC, "title": "e", "state": pc.APPROVAL_LANE},
            {"identifier": None, "title": "e", "state": pc.APPROVAL_LANE},
            {"title": "no identifier key at all", "state": pc.APPROVAL_LANE},
        ]
        code, out, posted, read = self._sweep(rows)
        self.assertEqual(code, 0)
        self.assertEqual(read, [EPIC])
        self.assertEqual(posted, [EPIC])
        self.assertIn("1 epic(s) in flight, spoke on 1", out)


# --- 7. The wiring -----------------------------------------------------------

class TheWiring(unittest.TestCase):

    @staticmethod
    def _main_source() -> str:
        import reconcile

        source = open(reconcile.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return ast.get_source_segment(source, node) or ""
        raise AssertionError("reconcile.main not found")

    def test_the_sweep_calls_report_inside_a_phase_block(self):
        body = self._main_source()
        tree = ast.parse("if 1:\n" + "\n".join(
            "    " + line for line in body.splitlines()))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            opens_a_phase = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", None) == "_phase"
                for item in node.items
            )
            if not opens_a_phase:
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "report"
                        and getattr(inner.func.value, "id", None)
                        == "rereview_watch"):
                    found.append(node)
        self.assertEqual(
            len(found), 1,
            "reconcile.main must call rereview_watch.report in exactly one "
            "_phase( block",
        )

    def test_the_grace_has_a_default_so_no_workflow_step_changes(self):
        import reconcile

        self.assertNotIn("REREVIEW_GRACE_MINUTES", reconcile.REQUIRED_ENV)
        self.assertEqual(rw.REREVIEW_GRACE_MINUTES, 45)

    def test_the_tag_rides_a_prefix_the_blocker_gate_already_reads(self):
        import reconcile

        self.assertTrue(
            rw.notice(EPIC, _overdue()).startswith(
                reconcile._AGENT_COMMENT_PREFIXES))

    def test_the_one_receipt_site_is_declared_in_the_act_registry(self):
        import check_act_receipts

        self.assertEqual(check_act_receipts.problems(), [])
        sites = [s for s in check_act_receipts.sites()
                 if s.path == "scripts/rereview_watch.py"]
        self.assertEqual(len(sites), 1, sites)


# --- 8. The replay (DRE-4758) ------------------------------------------------

#: DRE-4025 as Linear serves it TODAY, days after the instant the proof record
#: replays: the promise was eventually kept — round 2 PASSed on 2026-09-21 —
#: and the epic has been in Done since 11:50 PT that day. Neither fact was true
#: at `LATER`, and `check --now LATER` read both of them anyway: the lane check
#: refused before it reached a comment, and the thread behind it carried round
#: 2 (docs/rereview-continuation-proof-2026-09.md §2b).
ROUND_2_AT = "2026-09-21T15:05:23Z"
MOVED_TO_DONE_AT = "2026-09-21T18:50:00Z"


def settled_thread() -> list[dict]:
    """The whole thread as it stands today — round 2's PASS included."""
    return thread(_rec(_round(2, pc.PASS, ""), ROUND_2_AT))


def _move(at: str | None, frm: str | None, to: str | None) -> dict:
    """One `issue.history` node as Linear serves it."""
    return {
        "createdAt": at,
        "fromState": {"name": frm} if frm else None,
        "toState": {"name": to} if to else None,
    }


def lane_history() -> list[dict]:
    """DRE-4025's state history as Linear serves it: green-lit into In Progress
    before the send-back, moved to Done six days after it."""
    return [
        _move("2026-09-15T17:20:31Z", "Green Light", pc.APPROVAL_LANE),
        _move(MOVED_TO_DONE_AT, pc.APPROVAL_LANE, "Done"),
    ]


def _moved(at: str | None, frm: str | None, to: str | None) -> dict:
    """One lane move as `_state_history` normalises it, which is what
    `lane_at` reads."""
    return {"at": at, "from": frm, "to": to}


def lane_moves() -> list[dict]:
    """`lane_history()` after `_state_history`."""
    return [_moved(n["createdAt"],
                   (n["fromState"] or {}).get("name"),
                   (n["toState"] or {}).get("name")) for n in lane_history()]


class TheLaneAtAnInstant(unittest.TestCase):
    """`lane_at` — the lane off the epic's own history, never today's."""

    def test_it_is_where_the_last_move_before_the_instant_put_it(self):
        self.assertEqual(rw.lane_at(lane_moves(), LATER, "Done"),
                         pc.APPROVAL_LANE)

    def test_at_the_instant_of_a_move_the_move_has_happened(self):
        self.assertEqual(rw.lane_at(lane_moves(), MOVED_TO_DONE_AT, "Done"),
                         "Done")

    def test_before_the_first_move_it_is_the_lane_that_move_left(self):
        """Nothing moved the epic before this instant, so what it was moved
        OUT of next is where it was."""
        self.assertEqual(
            rw.lane_at(lane_moves(), "2026-09-15T09:00:00Z", "Done"),
            "Green Light")

    def test_an_epic_that_never_moved_is_where_it_is_today(self):
        self.assertEqual(rw.lane_at([], LATER, pc.APPROVAL_LANE),
                         pc.APPROVAL_LANE)

    def test_the_history_is_read_in_time_order_not_arrival_order(self):
        self.assertEqual(
            rw.lane_at(list(reversed(lane_moves())), LATER, "Done"),
            pc.APPROVAL_LANE)

    def test_a_move_linear_named_no_time_for_is_on_neither_side(self):
        """Unknown is unknown (console-honesty rule 2): a stamp that cannot be
        placed in time cannot be read as before or after the instant. Read as
        the newest, this would answer Canceled."""
        for stamp in (None, "", "not-a-time"):
            with self.subTest(stamp=stamp):
                moves = lane_moves() + [
                    _moved(stamp, pc.APPROVAL_LANE, "Canceled")]
                self.assertEqual(rw.lane_at(moves, LATER, "Done"),
                                 pc.APPROVAL_LANE)

    def test_an_instant_before_the_epic_had_a_lane_is_unknown(self):
        """The oldest move names no `from` — the epic was created into that
        lane. Earlier than that there is no lane to name, and `read` says
        nothing on a lane it was not given."""
        moves = [_moved("2026-09-15T17:20:31Z", None, "Intake")]
        self.assertIsNone(rw.lane_at(moves, "2026-09-14T00:00:00Z", "Done"))


class TheThreadAtAnInstant(unittest.TestCase):
    """`at_or_before` — the thread as it stood, not as it stands."""

    def test_a_comment_posted_after_the_instant_is_not_in_it(self):
        kept = rw.at_or_before(settled_thread(), LATER)
        self.assertEqual(len(kept), len(thread()))
        self.assertNotIn(ROUND_2_AT, [r["created_at"] for r in kept])

    def test_a_comment_posted_at_the_instant_is_in_it(self):
        kept = rw.at_or_before(thread(), SENT_BACK_AT)
        self.assertEqual(kept[-1]["created_at"], SENT_BACK_AT)

    def test_a_comment_linear_stamped_nothing_is_kept(self):
        """Dropping it would assert it did not exist yet, which nothing here
        knows. Unknown stays unknown, and `read` already refuses to do
        arithmetic on an unknown stamp."""
        stampless = _rec(_round(2, pc.PASS, ""), None)
        self.assertIn(stampless, rw.at_or_before(thread(stampless), LATER))


class TheHistoryRead(unittest.TestCase):
    """`_state_history` — what is kept out of Linear's history, and paging."""

    def _history(self, pages):
        linear = mock.MagicMock()
        linear.gql.side_effect = list(pages)
        with mock.patch.object(rw, "linear_ops", linear), \
                redirect_stderr(io.StringIO()):
            return rw._state_history(EPIC), linear

    def test_an_entry_that_changed_no_state_is_not_a_lane_move(self):
        """Linear's history carries every change — a label, an assignee, an
        estimate. Only the entries naming a `toState` moved the lane."""
        nodes = [
            _move("2026-09-15T17:20:31Z", "Green Light", pc.APPROVAL_LANE),
            {"createdAt": "2026-09-15T17:22:00Z", "fromState": None,
             "toState": None},
        ]
        rows, _ = self._history([
            {"issue": {"history": {"nodes": nodes,
                                   "pageInfo": {"hasNextPage": False}}}}])
        self.assertEqual(rows, [{"at": "2026-09-15T17:20:31Z",
                                 "from": "Green Light",
                                 "to": pc.APPROVAL_LANE}])

    def test_it_follows_the_pages_to_exhaustion(self):
        """A nested connection read to page one only is the DRE-2681 failure
        again: the lane would come off the OLDEST hundred entries."""
        pages = [
            {"issue": {"history": {
                "nodes": [_move("2026-09-15T17:20:31Z", "Green Light",
                                pc.APPROVAL_LANE)],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}},
            {"issue": {"history": {
                "nodes": [_move(MOVED_TO_DONE_AT, pc.APPROVAL_LANE, "Done")],
                "pageInfo": {"hasNextPage": False}}}},
        ]
        rows, linear = self._history(pages)
        self.assertEqual([r["to"] for r in rows], [pc.APPROVAL_LANE, "Done"])
        self.assertEqual(
            [c.args[1]["after"] for c in linear.gql.call_args_list],
            [None, "c1"])

    def test_a_page_claiming_another_with_no_usable_cursor_stops(self):
        """A server that keeps claiming another page must not hang the read."""
        for cursor, calls in ((None, 1), ("c1", 2)):
            page = {"issue": {"history": {
                "nodes": [_move(MOVED_TO_DONE_AT, pc.APPROVAL_LANE, "Done")],
                "pageInfo": {"hasNextPage": True, "endCursor": cursor}}}}
            with self.subTest(cursor=cursor):
                _, linear = self._history([page] * 5)
                self.assertEqual(linear.gql.call_count, calls)


class TheReplay(unittest.TestCase):
    """`check --now` against DRE-4025 as Linear serves it today.

    All three of the proof card's replays printed `quiet` on 2026-09-24. The
    tool moved the clock and read everything else as it stands, so it answered
    a question about today and called the answer a replay.
    """

    def _check(self, now=None, records=None, history=None, lane="Done"):
        linear = mock.MagicMock()
        linear.comment_records.return_value = (
            settled_thread() if records is None else records)

        def gql(query, variables=None):
            if "history" in query:
                return {"issue": {"history": {
                    "nodes": lane_history() if history is None else history,
                    "pageInfo": {"hasNextPage": False}}}}
            return {"issue": {"state": {"name": lane}}}

        linear.gql.side_effect = gql
        buf = io.StringIO()
        argv = ["check", EPIC] + (["--now", now] if now else [])
        with mock.patch.object(rw, "linear_ops", linear), redirect_stdout(buf):
            code = rw.main(argv)
        self.assertEqual(code, 0)
        linear.cmd_comment.assert_not_called()
        return buf.getvalue().strip(), linear

    def test_an_epic_that_has_since_reached_done_replays_as_it_was(self):
        """At 18:45Z DRE-4025 was In Progress with round 1 unanswered for an
        hour. It has been in Done since 2026-09-21, and that is the only thing
        `check --now` used to see."""
        out, _ = self._check(now=LATER)
        self.assertTrue(out.startswith("overdue:"), out)
        self.assertIn("round 1 sent back 61 min ago", out)
        self.assertIn("console/backend/receipts.py", out)
        self.assertNotIn("Done", out)

    def test_the_round_two_that_ran_later_does_not_settle_an_earlier_instant(self):
        """A comment created after `--now` does not change the answer: the
        reading is identical with round 2's PASS on the thread and without it."""
        with_it, _ = self._check(now=LATER)
        without_it, _ = self._check(now=LATER, records=thread())
        self.assertEqual(with_it, without_it)
        self.assertTrue(with_it.startswith("overdue:"), with_it)

    def test_an_instant_inside_the_forty_five_minute_grace_window_is_quiet(self):
        """SOON is six minutes after the send-back, so the promised review
        could still be running. Quiet for the GRACE WINDOW — not for the lane
        and not for a round 2 that had not happened yet."""
        out, _ = self._check(now=SOON)
        self.assertTrue(out.startswith("quiet:"), out)
        self.assertIn("sent back 6 min ago", out)
        self.assertIn(f"has {rw.REREVIEW_GRACE_MINUTES} minutes to run", out)
        self.assertNotIn("Done", out)

    def test_without_now_it_reads_today_and_asks_for_no_history(self):
        """The pinned fixture: bare `check` prints exactly what it printed on
        2026-09-24 (docs/rereview-continuation-proof-2026-09.md §2b), and reads
        no state history at all — there is no past to derive."""
        out, linear = self._check()
        self.assertEqual(out, (
            "quiet: DRE-4025 — the epic is in Done, not Planning or In "
            "Progress — nothing here is waiting on a review"))
        self.assertEqual(
            [c.args[0] for c in linear.gql.call_args_list
             if "history" in c.args[0]], [])


class TheClaimMatchesTheCode(unittest.TestCase):
    """The docstring promises a replay, and the sweep path is untouched."""

    @staticmethod
    def _names(fn_name: str) -> set[str]:
        source = open(rw.__file__, encoding="utf-8").read()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        raise AssertionError(f"{fn_name} not found")

    def test_the_docstring_promises_the_lane_and_the_thread_not_the_clock(self):
        claim = rw.__doc__ or ""
        self.assertIn("--now", claim)
        for word in ("lane", "thread", "state history"):
            self.assertIn(word, claim, claim)

    def test_the_flag_says_what_it_moves(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            rw.main(["check", "--help"])
        self.assertIn("lane", buf.getvalue())

    def test_check_reads_both_of_them_at_the_instant(self):
        names = self._names("_cmd_check")
        self.assertIn("at_or_before", names)
        self.assertIn("lane_at", names)

    def test_the_sweep_path_is_untouched(self):
        """`report` is what reconcile calls and what prints `rereview-missing:`.
        It reads live state and replays nothing."""
        names = self._names("report")
        for helper in ("at_or_before", "lane_at", "_state_history"):
            self.assertNotIn(helper, names)


# --- DRE-5278 fixtures -------------------------------------------------------

#: When the first critic passed the plan (the promise of the hand-off), and
#: the sweep clocks it is read against.
PRE_AT = SENT_BACK_AT
#: The watcher's own first notice, and the two sweeps after it.
NOTICE_AT = "2026-09-15T18:46:00Z"
FIFTEEN_LATER = "2026-09-15T19:01:00Z"   # 15 min after the notice
PAST_NOTICE = "2026-09-15T19:32:00Z"     # 46 min after the notice

#: What the plan route posts beside the record when it hands a passed plan to
#: the second critic. Its words are DRE-5280's; any pipeline note reads the
#: same here, because the watcher reads only records.
HAND_OFF_NOTE = (
    "📋 The first critic passed this plan. It has been handed to the second "
    "critic, which reads it before it reaches the CEO."
)

DEAD_RUN = "34301000001"
REPO = "dreadnought-foundry/bureau-pipeline"


def _pre(result: str = pc.PASS, reason: str = "") -> str:
    return pc.marker(pc.STAGE_PRE, 1, result, reason)


def handed_off_thread(*extra, pre_result: str = pc.PASS,
                      pre_pipeline: bool = True) -> list[dict]:
    """A Planning epic whose first critic passed the plan and handed it on —
    and then nothing."""
    rows = [
        _rec(pc.cycle_start_note(EPIC), "2026-09-15T17:21:02Z"),
        _rec(pc.cycle_marker(EPIC), "2026-09-15T17:21:04Z"),
        _rec(_pre(pre_result, "" if pre_result != pc.SEND_BACK else FINDING),
             PRE_AT, pre_pipeline),
        _rec(HAND_OFF_NOTE, "2026-09-15T17:43:31Z"),
    ]
    return rows + [item if isinstance(item, dict)
                   else _rec(item, "2026-09-15T18:00:00Z") for item in extra]


def died_thread(*extra) -> list[dict]:
    """A Planning epic whose second critic's review died, with no retry and
    no round behind the tombstone."""
    rows = [
        _rec(pc.cycle_start_note(EPIC), "2026-09-15T17:21:02Z"),
        _rec(pc.cycle_marker(EPIC), "2026-09-15T17:21:04Z"),
        _rec(_pre(), "2026-09-15T17:25:00Z"),
        _rec(HAND_OFF_NOTE, "2026-09-15T17:25:02Z"),
        _rec(_tombstone(DEAD_RUN), SENT_BACK_AT),
    ]
    return rows + [item if isinstance(item, dict)
                   else _rec(item, "2026-09-15T18:00:00Z") for item in extra]


def _slot(state: str, at: str, *, run: str = "36700000001",
          pipeline: bool = True, place: int | None = None) -> dict:
    """One planner-slot receipt in DRE-5176's grammar, as a thread row."""
    body = planner_queue.format_receipt(
        state, card=EPIC, run=run, repo=REPO, trigger="planning", at=at,
        place=place, of=place)
    return _rec(body, at, pipeline)


#: The three Planning silences: (name, thread builder, dispatch reason).
PLANNING_SILENCES = (
    ("handed off", handed_off_thread, review_rerun.REASON_REVIEW),
    ("sent back", thread, review_rerun.REASON_RE_REVIEW),
    ("died", died_thread, review_rerun.REASON_REVIEW_RETRY),
)


def _first_notice(build, lane=pc.REVIEW_LANE) -> dict:
    """The watcher's first notice on `build()`'s thread, as a thread row."""
    found = rw.overdue(build(), EPIC, lane, LATER, 45)
    assert found and found["firing"] == 1, found
    return _rec(rw.notice(EPIC, found), NOTICE_AT)


# --- 9. The lanes ------------------------------------------------------------

class TheLanes(unittest.TestCase):
    """DRE-4025's thread read in every lane an epic can be in."""

    def test_in_planning_it_is_overdue_past_the_grace(self):
        found = _overdue(lane=pc.REVIEW_LANE)
        self.assertIsNotNone(found)
        self.assertEqual(found["round"], 1)

    def test_in_planning_it_is_quiet_before_the_grace(self):
        self.assertIsNone(_overdue(lane=pc.REVIEW_LANE, now=SOON))

    def test_in_progress_it_is_still_overdue_past_the_grace(self):
        self.assertIsNotNone(_overdue(lane=pc.APPROVAL_LANE))

    def test_green_light_and_todo_are_quiet_with_a_line_saying_why(self):
        for lane in ("Green Light", "Todo"):
            with self.subTest(lane=lane):
                reading = rw.read(thread(), EPIC, lane, LATER, 45)
                self.assertIsNone(reading.found)
                self.assertIn(lane, reading.why)
                self.assertIn(pc.REVIEW_LANE, reading.why)


# --- 10. under_review --------------------------------------------------------

class UnderReview(unittest.TestCase):
    """"This epic is with the critics, and the watcher owns it"."""

    def test_a_first_critic_pass_is_under_review(self):
        self.assertTrue(rw.under_review(handed_off_thread(), EPIC))

    def test_a_first_critic_no_result_is_under_review(self):
        self.assertTrue(rw.under_review(
            handed_off_thread(pre_result=pc.NO_RESULT), EPIC))

    def test_a_first_critic_send_back_is_not(self):
        self.assertFalse(rw.under_review(
            handed_off_thread(pre_result=pc.SEND_BACK), EPIC))

    def test_any_second_critic_round_or_tombstone_is(self):
        self.assertTrue(rw.under_review(thread(), EPIC))
        self.assertTrue(rw.under_review(died_thread(), EPIC))

    def test_a_thread_with_no_attempt_records_is_not(self):
        self.assertFalse(rw.under_review([], EPIC))
        self.assertFalse(rw.under_review(
            [_rec(HAND_OFF_NOTE, "2026-09-15T17:43:31Z")], EPIC))

    def test_a_pass_posted_by_someone_else_is_not(self):
        self.assertFalse(rw.under_review(
            handed_off_thread(pre_pipeline=False), EPIC))

    def test_a_pass_on_an_earlier_attempt_is_not(self):
        records = handed_off_thread(
            _rec(pc.cycle_start_note(EPIC), "2026-09-15T19:00:00Z"),
            _rec(pc.cycle_marker(EPIC), "2026-09-15T19:00:02Z"))
        self.assertFalse(rw.under_review(records, EPIC))


# --- 11. Handed off, no review -----------------------------------------------

class HandedOffNoReview(unittest.TestCase):

    def test_first_sweep_asks_for_the_review_once_and_says_so(self):
        sweep = _Sweep()
        spoke, _ = sweep(_Reader({EPIC: handed_off_thread()}),
                         {EPIC: pc.REVIEW_LANE})
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatches, [{
            "epic": EPIC, "repo": REPO,
            "reason": review_rerun.REASON_REVIEW,
            "trigger_state": review_rerun.TRIGGER_STATE_REVIEW,
        }])
        self.assertEqual(len(sweep.posted), 1)
        self.assertIn("asked for the review again", sweep.posted[0][1])
        self.linear_untouched(sweep)

    def linear_untouched(self, sweep):
        sweep.linear.add_label.assert_not_called()
        sweep.linear.cmd_state.assert_not_called()

    def test_quiet_before_the_grace(self):
        self.assertIsNone(rw.overdue(handed_off_thread(), EPIC, pc.REVIEW_LANE,
                                     SOON, 45))

    def test_second_sweep_parks_label_first_with_the_record_quoted(self):
        records = handed_off_thread(_first_notice(handed_off_thread))
        sweep = _Sweep()
        spoke, _ = sweep(_Reader({EPIC: records}), {EPIC: pc.REVIEW_LANE},
                         now=PAST_NOTICE)
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatches, [])
        sweep.linear.add_label.assert_called_once_with(EPIC, "needs-human")
        sweep.linear.cmd_state.assert_called_once_with(EPIC, pc.BOUND_PARK_LANE)
        self.assertEqual(sweep.writes(),
                         ["add_label", "cmd_state", "cmd_comment"])
        note = sweep.posted[0][1]
        self.assertIn(_pre(), note)
        self.assertIn(pc.REAPPROVE_HOW, note)
        self.assertIn("parked in Triage", note)

    def test_a_first_critic_send_back_is_not_this_case(self):
        records = handed_off_thread(pre_result=pc.SEND_BACK)
        self.assertIsNone(rw.overdue(records, EPIC, pc.REVIEW_LANE, LATER, 45))


# --- 12. Sent back, no re-review (Planning) ----------------------------------

class SentBackNoReReview(unittest.TestCase):

    def test_first_firing_asks_for_the_re_review_in_planning(self):
        sweep = _Sweep()
        sweep(_Reader({EPIC: thread()}), {EPIC: pc.REVIEW_LANE})
        self.assertEqual(len(sweep.dispatches), 1)
        self.assertEqual(sweep.dispatches[0]["reason"],
                         review_rerun.REASON_RE_REVIEW)
        self.assertEqual(sweep.dispatches[0]["trigger_state"],
                         review_rerun.TRIGGER_STATE_REVIEW)
        self.assertEqual(len(sweep.posted), 1)
        self.assertIn("asked for the review again", sweep.posted[0][1])

    def test_the_same_timing_holds_for_every_planning_silence(self):
        """Fifteen minutes after the notice: nothing. Past the grace measured
        from the notice: no second dispatch, a park with needs-human."""
        for name, build, _reason in PLANNING_SILENCES:
            records = build(_first_notice(build))
            with self.subTest(silence=name, sweep="fifteen minutes later"):
                sweep = _Sweep()
                spoke, _ = sweep(_Reader({EPIC: records}),
                                 {EPIC: pc.REVIEW_LANE}, now=FIFTEEN_LATER)
                self.assertEqual(spoke, [])
                self.assertEqual(sweep.dispatches, [])
                self.assertEqual(sweep.writes(), [])
            with self.subTest(silence=name, sweep="past the grace"):
                sweep = _Sweep()
                spoke, _ = sweep(_Reader({EPIC: records}),
                                 {EPIC: pc.REVIEW_LANE}, now=PAST_NOTICE)
                self.assertEqual(spoke, [EPIC])
                self.assertEqual(sweep.dispatches, [])
                self.assertEqual(sweep.writes(),
                                 ["add_label", "cmd_state", "cmd_comment"])
                sweep.linear.add_label.assert_called_once_with(
                    EPIC, "needs-human")
                sweep.linear.cmd_state.assert_called_once_with(
                    EPIC, pc.BOUND_PARK_LANE)
                note = sweep.posted[0][1]
                self.assertIn(pc.REAPPROVE_HOW, note)
                self.assertIn("parked in Triage", note)

    def test_the_park_note_quotes_the_critics_finding_verbatim(self):
        records = thread(_first_notice(thread))
        found = rw.overdue(records, EPIC, pc.REVIEW_LANE, PAST_NOTICE, 45)
        self.assertEqual(found["firing"], 2)
        self.assertIn(FINDING, rw.notice(EPIC, found))

    def test_a_park_already_said_is_not_said_twice(self):
        """At most twice per record."""
        records = thread(_first_notice(thread))
        found = rw.overdue(records, EPIC, pc.REVIEW_LANE, PAST_NOTICE, 45)
        records.append(_rec(rw.notice(EPIC, found), "2026-09-15T19:33:00Z"))
        self.assertIsNone(rw.overdue(records, EPIC, pc.REVIEW_LANE,
                                     "2026-09-15T21:00:00Z", 45))

    def test_a_round_after_the_notice_is_a_fresh_record(self):
        records = thread(_first_notice(thread),
                         _rec(_round(2, pc.PASS, ""), "2026-09-15T19:10:00Z"))
        self.assertIsNone(rw.overdue(records, EPIC, pc.REVIEW_LANE,
                                     PAST_NOTICE, 45))

    def test_a_newest_round_of_no_result_is_quiet_with_a_line_saying_why(self):
        records = thread(_rec(_round(2, pc.NO_RESULT, ""),
                              "2026-09-15T18:00:00Z"))
        reading = rw.read(records, EPIC, pc.REVIEW_LANE, "2026-09-15T20:00:00Z",
                          45)
        self.assertIsNone(reading.found)
        self.assertIn(pc.NO_RESULT, reading.why)

    def test_a_legacy_notice_reads_as_no_firing_so_the_watcher_re_asks(self):
        """A notice from before DRE-5278 carries the tag and neither phrase.
        Read as a first firing it would park a live epic the moment this
        lands; read as nothing, the watcher asks for the review once — the
        conservative act — and parks only after its own notice."""
        legacy = _rec(
            f"🚨 {rw.REREVIEW_MISSING_TAG}: {EPIC}'s plan was sent back by the "
            "critic at 2026-09-15 17:43 UTC (round 1) and the review the "
            "pipeline promised has not run in the 62 minutes since.\n\n"
            "**To run it:** post the act.", NOTICE_AT)
        found = rw.overdue(thread(legacy), EPIC, pc.APPROVAL_LANE,
                           PAST_NOTICE, 45)
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 1)


# --- 13. Died, no retry ------------------------------------------------------

class DiedNoRetry(unittest.TestCase):

    def test_first_firing_asks_for_a_retry_in_planning(self):
        sweep = _Sweep()
        sweep(_Reader({EPIC: died_thread()}), {EPIC: pc.REVIEW_LANE})
        self.assertEqual(len(sweep.dispatches), 1)
        self.assertEqual(sweep.dispatches[0]["reason"],
                         review_rerun.REASON_REVIEW_RETRY)
        self.assertEqual(sweep.dispatches[0]["trigger_state"],
                         review_rerun.TRIGGER_STATE_REVIEW)
        self.assertIn("asked for the review again", sweep.posted[0][1])

    def test_the_park_names_the_dead_run(self):
        records = died_thread(_first_notice(died_thread))
        found = rw.overdue(records, EPIC, pc.REVIEW_LANE, PAST_NOTICE, 45)
        self.assertEqual(found["firing"], 2)
        self.assertIn(DEAD_RUN, rw.notice(EPIC, found))

    def test_a_retry_that_reached_the_planner_is_dispatched_behind_it(self):
        """The retry the review asked for itself claimed a slot: that is a
        retry dispatched behind the tombstone, not silence."""
        records = died_thread(_slot("claimed", "2026-09-15T17:50:00Z",
                                    run="36700000009"),
                              _slot("released", "2026-09-15T18:10:00Z",
                                    run="36700000009"))
        self.assertIsNone(rw.overdue(records, EPIC, pc.REVIEW_LANE, LATER, 45))

    def test_the_dead_runs_own_release_is_not_a_retry(self):
        records = died_thread(_slot("released", "2026-09-15T17:44:00Z",
                                    run=DEAD_RUN))
        found = rw.overdue(records, EPIC, pc.REVIEW_LANE, LATER, 45)
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 1)

    def test_it_asks_again_after_its_grace_whatever_a_limit_marker_says(self):
        """DRE-5640: limit_recovery leaves a Planning review death to this
        watcher, which does NOT read the Claude wall. A 🪦 limit-death marker
        whose assumed reset is hours away changes nothing: past the grace the
        watcher asks for the review again — the interaction the recovery
        sweep's line and its three-deaths bound are written against."""
        wall = _rec("🪦 limit-death: kind=claude stage=review "
                    "reset=2026-09-15T22:44:00Z run=" + DEAD_RUN + " assumed=yes",
                    "2026-09-15T17:44:00Z")
        found = rw.overdue(died_thread(wall), EPIC, pc.REVIEW_LANE, LATER, 45)
        self.assertIsNotNone(found, "the wall's reset is not a grace this reads")
        self.assertEqual(found["silence"], rw.DIED)
        self.assertEqual(found["firing"], 1)
        self.assertEqual(found["dispatch_reason"],
                         review_rerun.REASON_REVIEW_RETRY)
        self.assertIsNone(rw.overdue(died_thread(wall), EPIC, pc.REVIEW_LANE,
                                     SOON, 45),
                          "inside the grace it waits, wall or no wall")


# --- 14. In Progress: sent back, no re-review --------------------------------

class InProgressSentBack(unittest.TestCase):

    def test_first_sweep_asks_in_progress_and_names_the_act_in_a_sentence(self):
        sweep = _Sweep()
        spoke, _ = sweep(_Reader({EPIC: thread()}), {EPIC: pc.APPROVAL_LANE})
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatches, [{
            "epic": EPIC, "repo": REPO,
            "reason": review_rerun.REASON_RE_REVIEW,
            "trigger_state": review_rerun.TRIGGER_STATE_ACTIVATE,
        }])
        self.assertEqual(len(sweep.posted), 1)
        body = sweep.posted[0][1]
        self.assertIn("asked for the review again", body)
        self.assertIn(review_rerun.RERUN_REVIEW_ACT, body)
        for line in body.splitlines():
            self.assertNotEqual(line.strip(), review_rerun.RERUN_REVIEW_ACT)
        sweep.linear.add_label.assert_not_called()
        sweep.linear.cmd_state.assert_not_called()

    def test_past_the_grace_from_the_notice_it_parks_in_triage(self):
        records = thread(_first_notice(thread, pc.APPROVAL_LANE))
        sweep = _Sweep()
        spoke, _ = sweep(_Reader({EPIC: records}), {EPIC: pc.APPROVAL_LANE},
                         now=PAST_NOTICE)
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatches, [])
        self.assertEqual(sweep.writes(), ["add_label", "cmd_state", "cmd_comment"])
        sweep.linear.add_label.assert_called_once_with(EPIC, "needs-human")
        sweep.linear.cmd_state.assert_called_once_with(EPIC, pc.BOUND_PARK_LANE)
        note = sweep.posted[0][1]
        self.assertIn(FINDING, note)
        self.assertIn(pc.REAPPROVE_HOW, note)
        self.assertIn("parked in Triage", note)

    def test_no_post_round_in_progress_is_quiet_with_a_line_saying_why(self):
        reading = rw.read(handed_off_thread(), EPIC, pc.APPROVAL_LANE, LATER, 45)
        self.assertIsNone(reading.found)
        self.assertIn("activate route", reading.why)

    def test_a_newest_tombstone_in_progress_is_quiet_with_a_line_saying_why(self):
        reading = rw.read(died_thread(), EPIC, pc.APPROVAL_LANE, LATER, 45)
        self.assertIsNone(reading.found)
        self.assertIn("died", reading.why)


# --- 15. The planner line is not silence -------------------------------------

LINE_NOW = "2026-09-15T20:45:00Z"            # three hours after the line entry
LINE_ENTRY = "2026-09-15T17:45:00Z"


class ThePlannerLine(unittest.TestCase):

    def _all_silences_quiet(self, extra, now):
        for name, build, _ in PLANNING_SILENCES:
            for lane in (pc.REVIEW_LANE, pc.APPROVAL_LANE):
                if lane == pc.APPROVAL_LANE and name != "sent back":
                    continue
                with self.subTest(silence=name, lane=lane):
                    records = build(*extra)
                    reading = rw.read(records, EPIC, lane, now, 45)
                    self.assertIsNone(reading.found, reading.why)
                    sweep = _Sweep()
                    spoke, out = sweep(_Reader({EPIC: records}), {EPIC: lane},
                                       now=now)
                    self.assertEqual(spoke, [])
                    self.assertEqual(sweep.dispatches, [])
                    self.assertEqual(sweep.writes(), [])
                    yield reading, out

    def test_an_epic_waiting_in_line_inside_the_bound_is_quiet(self):
        extra = (_slot("waiting", LINE_ENTRY, place=3),)
        for reading, out in self._all_silences_quiet(extra, LINE_NOW):
            self.assertIn("waiting", reading.why)
            self.assertIn("180", reading.why)
            self.assertIn("waiting", out)
            self.assertIn("180", out)

    def test_a_dispatch_from_the_line_inside_its_grace_is_quiet_too(self):
        extra = (_slot("waiting", LINE_ENTRY, place=3),
                 _slot("dispatched", "2026-09-15T20:40:00Z"))
        for reading, _ in self._all_silences_quiet(extra, LINE_NOW):
            self.assertIn("waiting", reading.why)
            self.assertIn("180", reading.why)

    def test_past_the_lines_bound_it_is_the_stall_watchdogs(self):
        now = "2026-09-16T00:00:00Z"
        extra = (_slot("waiting", "2026-09-15T17:59:00Z", place=3),)
        for reading, _ in self._all_silences_quiet(extra, now):
            self.assertIn("361", reading.why)
            self.assertIn("stall watchdog", reading.why)

    def test_a_claim_after_the_notice_stops_the_second_firing(self):
        for name, build, _ in PLANNING_SILENCES:
            with self.subTest(silence=name):
                records = build(_first_notice(build),
                                _slot("claimed", "2026-09-15T19:22:00Z"))
                sweep = _Sweep()
                spoke, _ = sweep(_Reader({EPIC: records}),
                                 {EPIC: pc.REVIEW_LANE}, now=PAST_NOTICE)
                self.assertEqual(spoke, [])
                self.assertEqual(sweep.writes(), [])
                self.assertEqual(sweep.dispatches, [])

    def test_any_receipt_after_the_notice_stops_the_second_firing(self):
        """The card's rule is literal: no planner-slot receipt after the
        notice. A claim that has already been released is not an open claim,
        so this is the rule itself holding, not the running-review rule."""
        for name, build, _ in PLANNING_SILENCES:
            with self.subTest(silence=name):
                records = build(_first_notice(build),
                                _slot("claimed", "2026-09-15T18:50:00Z",
                                      run="36700000007"),
                                _slot("released", "2026-09-15T19:00:00Z",
                                      run="36700000007"))
                reading = rw.read(records, EPIC, pc.REVIEW_LANE, PAST_NOTICE, 45)
                self.assertTrue(reading.found is None or reading.spoken,
                                reading.why)
                sweep = _Sweep()
                spoke, _ = sweep(_Reader({EPIC: records}),
                                 {EPIC: pc.REVIEW_LANE}, now=PAST_NOTICE)
                self.assertEqual(spoke, [])
                self.assertEqual(sweep.writes(), [])
                self.assertEqual(sweep.dispatches, [])

    def _promise_lanes(self):
        """Every (silence, lane) the watcher holds a promise in: the three
        Planning silences, and the In Progress send-back."""
        for name, build, _ in PLANNING_SILENCES:
            yield name, build, pc.REVIEW_LANE
        yield "sent back", thread, pc.APPROVAL_LANE

    def test_a_claim_newer_than_the_promise_means_the_review_is_running(self):
        """No notice yet, so nothing but the open claim stands between the
        promise and a first firing. The claim is still open at LATER (the
        TTL is 115 minutes)."""
        claim_run = "36700000005"
        for name, build, lane in self._promise_lanes():
            with self.subTest(silence=name, lane=lane):
                records = build(_slot("claimed", "2026-09-15T18:00:00Z",
                                      run=claim_run))
                self.assertIsNone(rw.overdue(records, EPIC, lane, LATER, 45))
                reading = rw.read(records, EPIC, lane, LATER, 45)
                self.assertIsNone(reading.found, reading.why)
                self.assertIn(claim_run, reading.why)
                self.assertIn("the review is running", reading.why)
                sweep = _Sweep()
                spoke, _ = sweep(_Reader({EPIC: records}), {EPIC: lane})
                self.assertEqual(spoke, [])
                self.assertEqual(sweep.dispatches, [])
                self.assertEqual(sweep.writes(), [])

    def test_a_claim_older_than_the_promise_is_not_the_review(self):
        """A claim still open but made before the promise — the first
        critic's own run, say — is not the review the promise asked for. It
        sits in the thread where its stamp puts it: after the attempt's
        marker, before the promise."""
        for name, build, lane in self._promise_lanes():
            with self.subTest(silence=name, lane=lane):
                records = build()
                records.insert(2, _slot("claimed", "2026-09-15T17:30:00Z",
                                        run="36700000006"))
                found = rw.overdue(records, EPIC, lane, LATER, 45)
                self.assertIsNotNone(found)
                self.assertEqual(found["firing"], 1)

    def test_a_waiting_receipt_someone_else_posted_is_not_read(self):
        records = handed_off_thread(
            _slot("waiting", LINE_ENTRY, place=3, pipeline=False))
        found = rw.overdue(records, EPIC, pc.REVIEW_LANE, LINE_NOW, 45)
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 1)


# --- 16. What every notice may say -------------------------------------------

def _every_notice() -> list[tuple[str, str]]:
    """`(label, body)` for every notice and note this module writes."""
    out = []
    for name, build, _ in PLANNING_SILENCES:
        first = rw.overdue(build(), EPIC, pc.REVIEW_LANE, LATER, 45)
        out.append((f"{name} first", rw.notice(EPIC, first)))
        records = build(_first_notice(build))
        second = rw.overdue(records, EPIC, pc.REVIEW_LANE, PAST_NOTICE, 45)
        out.append((f"{name} park", rw.notice(EPIC, second)))
    first = rw.overdue(thread(), EPIC, pc.APPROVAL_LANE, LATER, 45)
    out.append(("in progress first", rw.notice(EPIC, first)))
    records = thread(_first_notice(thread, pc.APPROVAL_LANE))
    second = rw.overdue(records, EPIC, pc.APPROVAL_LANE, PAST_NOTICE, 45)
    out.append(("in progress park", rw.notice(EPIC, second)))
    return out


class WhatANoticeMaySay(unittest.TestCase):

    def test_no_notice_says_approve_or_green_light_outside_reapprove_how(self):
        for label, body in _every_notice():
            with self.subTest(notice=label):
                rest = body.replace(pc.REAPPROVE_HOW, "").lower()
                self.assertNotIn("post-approval", rest)
                self.assertNotIn("approve", rest)
                self.assertNotIn("green light", rest)

    def test_each_firing_carries_its_own_phrase_and_not_the_other(self):
        for label, body in _every_notice():
            with self.subTest(notice=label):
                self.assertTrue(body.startswith(
                    f"🚨 {rw.REREVIEW_MISSING_TAG}: {EPIC}"), body[:80])
                if label.endswith("first"):
                    self.assertIn("asked for the review again", body)
                    self.assertNotIn("parked in Triage", body)
                else:
                    self.assertIn("parked in Triage", body)
                    self.assertNotIn("asked for the review again", body)
                    self.assertTrue(
                        body.rstrip().endswith(pc.REAPPROVE_HOW + "."),
                        body[-200:])

    def test_only_the_in_progress_first_notice_names_the_act_itself(self):
        """A Planning first notice names no act: the act fires only on an
        epic In Progress. A park note carries it only inside REAPPROVE_HOW."""
        for label, body in _every_notice():
            with self.subTest(notice=label):
                rest = body.replace(pc.REAPPROVE_HOW, "")
                if label == "in progress first":
                    self.assertIn(review_rerun.RERUN_REVIEW_ACT, rest)
                else:
                    self.assertNotIn(review_rerun.RERUN_REVIEW_ACT, rest)
                for line in body.splitlines():
                    self.assertFalse(review_rerun.is_rerun_act(line), line)

    def test_post_approval_appears_in_neither_script(self):
        for name in ("rereview_watch.py", "linear_ops.py"):
            with self.subTest(script=name):
                text = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
                self.assertNotIn("post-approval", text.lower())


# --- 17. epics-in-flight -----------------------------------------------------

class EpicsInFlight(unittest.TestCase):
    """The keyword, the flag, and the watcher's own read."""

    def setUp(self):
        import linear_ops

        self.linear_ops = linear_ops
        self.asked: list[tuple] = []

    def _paged(self, query, variables=None, **kw):
        self.asked.append(tuple(variables["states"]))
        return [{"identifier": EPIC, "title": "e",
                 "state": {"name": pc.REVIEW_LANE},
                 "children": {"nodes": [{"id": "c"}]}}]

    def _run(self, fn):
        buf = io.StringIO()
        with mock.patch.object(self.linear_ops, "gql_paged",
                               side_effect=self._paged), redirect_stdout(buf):
            result = fn()
        return result, buf.getvalue()

    def test_bare_it_queries_exactly_in_flight(self):
        self._run(self.linear_ops.cmd_epics_in_flight)
        self.assertEqual(self.asked, [pc.IN_FLIGHT_EPIC_STATES])

    def test_the_sight_flag_queries_exactly_the_sight(self):
        self._run(lambda: self.linear_ops.cmd_epics_in_flight("--sight"))
        self.assertEqual(self.asked, [pc.SIGHT_STATES])

    def test_the_keyword_queries_exactly_what_it_names(self):
        self._run(lambda: self.linear_ops.cmd_epics_in_flight(
            states=pc.SIGHT_STATES))
        self.assertEqual(self.asked, [pc.SIGHT_STATES])

    def test_the_watcher_reads_the_sight(self):
        rows, _ = self._run(rw._epics_in_flight)
        self.assertEqual(self.asked, [pc.SIGHT_STATES])
        self.assertEqual([r["identifier"] for r in rows], [EPIC])

    def test_in_flight_itself_is_unchanged(self):
        self.assertEqual(pc.IN_FLIGHT_EPIC_STATES,
                         ("Green Light", "Todo", "In Progress"))

    def test_the_docstring_names_the_second_critic(self):
        doc = self.linear_ops.cmd_epics_in_flight.__doc__ or ""
        self.assertIn("The second critic's cross-epic sight", doc)


if __name__ == "__main__":
    unittest.main()
