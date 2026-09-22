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

Run: cd bureau-pipeline && python3 -m pytest tests/test_rereview_watch.py -v
"""

from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
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
    """The notice is idempotent; the log line is not."""

    def _spoken(self, at="2026-09-15T18:46:00Z", pipeline=True):
        found = _overdue()
        return _rec(rw.notice(EPIC, found), at, pipeline)

    def test_a_posted_notice_silences_the_next_sweep(self):
        records = thread(self._spoken())
        self.assertIsNone(rw.overdue(records, EPIC, pc.APPROVAL_LANE,
                                     "2026-09-15T19:45:00Z", 45))

    def test_but_the_reading_still_stands_so_the_log_line_still_prints(self):
        records = thread(self._spoken())
        reading = rw.read(records, EPIC, pc.APPROVAL_LANE,
                          "2026-09-15T19:45:00Z", 45)
        self.assertIsNotNone(reading.found)
        self.assertTrue(reading.spoken)

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

    def test_it_ends_with_the_one_sentence_every_refusal_uses(self):
        self.assertTrue(
            self.body.rstrip().endswith(pc.REAPPROVE_HOW + "."),
            self.body[-400:],
        )
        self.assertIn("**To run it:** ", self.body)

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


class TheReport(unittest.TestCase):

    def setUp(self):
        self.posted: list[tuple[str, str]] = []
        self.linear = mock.MagicMock()
        self.linear.cmd_comment.side_effect = (
            lambda ident, body, *f: self.posted.append((ident, body)))

    def _report(self, reader, lanes, now=LATER):
        with mock.patch.object(rw, "linear_ops", self.linear):
            buf = io.StringIO()
            with redirect_stdout(buf):
                spoke = rw.report(list(lanes), reader, lanes.get, now)
        return spoke, buf.getvalue()

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
                                  now="2026-09-15T19:45:00Z")
        self.assertEqual(spoke, [])
        self.assertEqual(self.posted, [])
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
        linear.cmd_epics_in_flight.side_effect = lambda: print(json.dumps(rows))
        linear.comment_records.return_value = thread()
        linear.cmd_comment.side_effect = lambda i, b, *f: posted.append(i)
        buf = io.StringIO()
        with mock.patch.object(rw, "linear_ops", linear), redirect_stdout(buf):
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


if __name__ == "__main__":
    unittest.main()
