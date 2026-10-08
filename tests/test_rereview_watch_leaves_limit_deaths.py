"""The re-review watcher waits out a review's limit death (DRE-5842).

Under DRE-5268 the second critic reads a plan while its epic sits in Planning,
and `rereview_watch` owns every such epic. When the review dies on a Claude
limit the medic writes a `🪦 limit-death:` marker with a reset (DRE-5455), and
DRE-5640 leaves a `stage=review` death on a Planning epic to this watcher.
Before this card the watcher asked for the review again
`REREVIEW_GRACE_MINUTES` after the tombstone — into the same wall — and the
next death parked a plan that had done nothing wrong.

Three rules, applied once a promise is found and before its grace check, and
read only off the pipeline's own comments in the current attempt after the
promise's record:

  1. A marker whose reset is still ahead: quiet, waiting on the wall. Once the
     reset passes the existing rules decide, and a review death gets its first
     firing at once — a fresh run.
  2. A `🔁 limit-recovery:` receipt younger than the grace: quiet, the run the
     recovery re-entered owns it.
  3. A `⚠️ limit-recovery:` hand-off as the newest limit record: the second
     firing — the park — is due at once, and its note quotes the hand-off.

One section per acceptance criterion, then the import direction.

Run: cd bureau-pipeline && python3 -m pytest tests/test_rereview_watch_leaves_limit_deaths.py -v
"""

from __future__ import annotations

import ast
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402
import limit_recovery  # noqa: E402
import plan_critic as pc  # noqa: E402
import rereview_watch as rw  # noqa: E402
import review_rerun  # noqa: E402

EPIC = "DRE-5842"
REPO = "dreadnought-foundry/bureau-pipeline"
DEAD_RUN = "34301000001"
GRACE = rw.REREVIEW_GRACE_MINUTES

#: The sweep's clock. The tombstone is an hour old, the wall four hours ahead.
NOW = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
TOMBSTONE_AT = NOW - timedelta(minutes=60)
MARKER_AT = TOMBSTONE_AT + timedelta(minutes=1)
RESET = NOW + timedelta(hours=4)
PAST_RESET = RESET + timedelta(minutes=1)

FINDING = ("DRE-5801 and DRE-5802 both edit scripts/reconcile.py with no "
           "relation between them")


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(body: str, when: datetime | None, pipeline: bool = True) -> dict:
    """One row as `linear_ops.comment_records` returns it."""
    return {"body": body, "authored_by_pipeline": pipeline,
            "created_at": _iso(when) if when else None}


def _marker(stage: str = "review", reset: datetime | None = RESET,
            assumed: bool = True, kind: str = "claude") -> str:
    """The marker from `dead_run`'s own writer, never hand-spelled."""
    return dead_run.limit_marker(kind, stage, reset, DEAD_RUN,
                                 reset_assumed=assumed)


def _opening() -> list[dict]:
    """A fresh planning attempt whose first critic passed the plan."""
    start = NOW - timedelta(hours=2)
    return [
        _rec(pc.cycle_start_note(EPIC), start),
        _rec(pc.cycle_marker(EPIC), start + timedelta(seconds=2)),
        _rec(pc.marker(pc.STAGE_PRE, 1, pc.PASS, ""), start + timedelta(minutes=5)),
    ]


def _tombstone() -> str:
    return pc.death_marker(pc.STAGE_POST, DEAD_RUN, 1, "post",
                           "error_during_execution", 3, 140)


def died_thread(*extra) -> list[dict]:
    """First critic PASS, then the second critic's tombstone an hour old."""
    return _opening() + [_rec(_tombstone(), TOMBSTONE_AT), *extra]


def walled_thread(*extra, marker: str | None = None) -> list[dict]:
    """`died_thread` plus the medic's limit marker one minute after it."""
    return died_thread(_rec(marker or _marker(), MARKER_AT), *extra)


SENT_BACK_AT = NOW - timedelta(hours=6)


def sent_back_thread(*extra) -> list[dict]:
    """The second critic sent round 1 back; its re-plan died on the wall."""
    return _opening() + [
        _rec(pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, FINDING), SENT_BACK_AT),
        _rec(_marker(stage="plan", reset=NOW - timedelta(minutes=30)),
             SENT_BACK_AT + timedelta(minutes=5)),
        *extra,
    ]


def _recovery(minutes_old: float, stage: str = "plan") -> dict:
    marker = dead_run.parse_limit_marker(_marker(stage=stage))
    body = limit_recovery.recovery_receipt(
        marker, f"window reset at {dead_run.pacific(NOW - timedelta(minutes=30))}",
        "Bounced Planning → Intake → Planning, because entering Planning is "
        "what starts the planner")
    return _rec(body, NOW - timedelta(minutes=minutes_old))


HANDOFF_REASON = (
    "the run has died three times in a day on a Claude limit that named no "
    "reset time, each time brought back on an assumed five-hour clock, and a "
    "wall still standing after that is not the five-hour usage window")


def _handoff(when: datetime, pipeline: bool = True) -> dict:
    marker = dead_run.parse_limit_marker(_marker())
    return _rec(limit_recovery.handoff_receipt(marker, HANDOFF_REASON), when,
                pipeline)


def _read(records, now: datetime = NOW, lane: str = pc.REVIEW_LANE):
    return rw.read(records, EPIC, lane, _iso(now))


def _overdue(records, now: datetime = NOW, lane: str = pc.REVIEW_LANE):
    return rw.overdue(records, EPIC, lane, _iso(now))


class _Reader:
    def __init__(self, records):
        self.records = records

    def __call__(self, epic):
        return self.records


class _Sweep:
    """`report` with Linear and `dispatch_review` stubbed and recorded."""

    def __init__(self):
        self.linear = mock.MagicMock()
        self.posted: list[tuple[str, str]] = []
        self.dispatched: list[tuple] = []
        self.linear.cmd_comment.side_effect = (
            lambda ident, body, *f: self.posted.append((ident, body)))

    def _dispatch(self, epic, repo, reason, trigger_state):
        self.dispatched.append((epic, repo, reason, trigger_state))
        return True

    def __call__(self, records, now: datetime, lane: str = pc.REVIEW_LANE):
        buf = io.StringIO()
        with mock.patch.object(rw, "linear_ops", self.linear), \
                mock.patch.object(rw, "hold", self.linear.hold), \
                mock.patch.object(rw, "dispatch_review",
                                  side_effect=self._dispatch), \
                mock.patch.dict(os.environ, {"REPO": REPO}), \
                redirect_stdout(buf), redirect_stderr(io.StringIO()):
            spoke = rw.report([EPIC], _Reader(records), {EPIC: lane}.get,
                              _iso(now))
        return spoke, buf.getvalue()

    def writes(self) -> list[str]:
        return [c[0] for c in self.linear.mock_calls
                if c[0] in ("hold.apply", "add_label", "cmd_state", "cmd_comment")]


# --- 1. Waiting on the wall --------------------------------------------------

class WaitingOnTheWall(unittest.TestCase):

    def test_a_review_death_with_its_reset_ahead_is_quiet_and_says_until_when(self):
        reading = _read(walled_thread())
        self.assertIsNone(reading.found)
        self.assertFalse(reading.spoken)
        until = dead_run.pacific(RESET)
        self.assertIn(
            f"waiting on a claude limit death (review stage) until {until} "
            "(assumed) — asks for the review again after that", reading.why)

    def test_report_dispatches_nothing_and_posts_nothing(self):
        sweep = _Sweep()
        spoke, _out = sweep(walled_thread(), NOW)
        self.assertEqual(spoke, [])
        self.assertEqual(sweep.dispatched, [])
        self.assertEqual(sweep.writes(), [])

    def test_a_stated_reset_carries_no_assumed_word(self):
        reading = _read(walled_thread(marker=_marker(assumed=False)))
        self.assertIsNone(reading.found)
        self.assertIn(f"until {dead_run.pacific(RESET)} — asks for the review "
                      "again after that", reading.why)
        self.assertNotIn("(assumed)", reading.why)

    def test_a_re_plan_death_after_a_send_back_waits_too(self):
        """A `stage=plan` marker: the recovery re-enters that one at its
        reset, and nobody revised the plan meanwhile."""
        records = _opening() + [
            _rec(pc.marker(pc.STAGE_POST, 1, pc.SEND_BACK, FINDING), SENT_BACK_AT),
            _rec(_marker(stage="plan"), SENT_BACK_AT + timedelta(minutes=5)),
        ]
        reading = _read(records)
        self.assertIsNone(reading.found)
        self.assertIn("waiting on a claude limit death (plan stage) until",
                      reading.why)

    def test_a_marker_before_the_promises_record_waits_on_nothing(self):
        """Only the comments after the promise's record are read."""
        records = _opening() + [_rec(_marker(), NOW - timedelta(minutes=90)),
                                _rec(_tombstone(), TOMBSTONE_AT)]
        reading, plain = _read(records), _read(died_thread())
        self.assertIsNotNone(reading.found)
        self.assertEqual(reading.found["firing"], 1)
        self.assertEqual((reading.spoken, reading.why), (plain.spoken, plain.why))


# --- 2. The first firing once the wall is down -------------------------------

class PastTheReset(unittest.TestCase):

    def test_one_minute_past_the_reset_the_first_firing_is_due(self):
        found = _overdue(walled_thread(), PAST_RESET)
        self.assertIsNotNone(found)
        self.assertEqual(found["silence"], rw.DIED)
        self.assertEqual(found["firing"], 1)

    def test_report_asks_for_the_review_as_died_no_retry_does(self):
        sweep = _Sweep()
        spoke, _out = sweep(walled_thread(), PAST_RESET)
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatched, [(
            EPIC, REPO, review_rerun.REASON_REVIEW_RETRY,
            review_rerun.TRIGGER_STATE_REVIEW)])
        # The same reason and trigger state the plain silence uses today.
        plain = _overdue(died_thread())
        self.assertEqual(plain["dispatch_reason"], review_rerun.REASON_REVIEW_RETRY)
        self.assertEqual(plain["trigger_state"], review_rerun.TRIGGER_STATE_REVIEW)
        self.assertEqual(len(sweep.posted), 1)
        self.assertIn(rw.FIRST_FIRING_WORDS, sweep.posted[0][1])
        sweep.linear.hold.apply.assert_not_called()
        sweep.linear.cmd_state.assert_not_called()


# --- 3. The recovery re-entered it -------------------------------------------

class TheRecoveryReEnteredIt(unittest.TestCase):

    def test_a_receipt_ten_minutes_old_is_quiet(self):
        reading = _read(sent_back_thread(_recovery(10)))
        self.assertIsNone(reading.found)
        self.assertIn("the limit recovery re-entered it 10 min ago — that run "
                      "owns it", reading.why)

    def test_at_the_grace_the_existing_rules_decide(self):
        records = sent_back_thread(_recovery(GRACE))
        self.assertEqual(_read(records), _read(sent_back_thread()))
        found = _overdue(records)
        self.assertIsNotNone(found)
        self.assertEqual(found["silence"], rw.SENT_BACK)
        self.assertEqual(found["firing"], 1)


# --- 4. The recovery handed it to a person -----------------------------------

class TheRecoveryHandedItToAPerson(unittest.TestCase):

    def records(self):
        return walled_thread(_handoff(NOW - timedelta(minutes=5)))

    def test_the_second_firing_is_due_at_once(self):
        found = _overdue(self.records())
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 2)

    def test_due_at_once_even_inside_the_grace(self):
        records = _opening() + [
            _rec(_tombstone(), NOW - timedelta(minutes=10)),
            _rec(_marker(), NOW - timedelta(minutes=9)),
            _handoff(NOW - timedelta(minutes=8)),
        ]
        found = _overdue(records)
        self.assertIsNotNone(found)
        self.assertEqual(found["firing"], 2)

    def test_report_parks_label_first_and_quotes_the_reason(self):
        sweep = _Sweep()
        spoke, _out = sweep(self.records(), NOW)
        self.assertEqual(spoke, [EPIC])
        self.assertEqual(sweep.dispatched, [])
        self.assertEqual(sweep.writes(), ["hold.apply", "cmd_state", "cmd_comment"])
        sweep.linear.hold.apply.assert_called_once_with(
            EPIC, "epic-rereview-twice", "none", "rereview_watch.py")
        sweep.linear.cmd_state.assert_called_once_with(EPIC, "Triage")
        note = sweep.posted[0][1]
        self.assertIn("limit recovery handed", note)
        self.assertIn(HANDOFF_REASON, note)
        self.assertTrue(note.rstrip().rstrip(".").endswith(pc.REAPPROVE_HOW), note)
        self.assertIn(rw.SECOND_FIRING_WORDS, note)
        self.assertNotIn(rw.FIRST_FIRING_WORDS, note)

    def test_a_park_already_said_is_not_said_twice(self):
        found = _overdue(self.records())
        records = self.records() + [
            _rec(rw.notice(EPIC, found), NOW + timedelta(minutes=1))]
        self.assertIsNone(_overdue(records, NOW + timedelta(minutes=20)))

    def test_a_marker_newer_than_the_hand_off_is_what_counts(self):
        """The newest limit record decides: a fresh death behind the hand-off
        waits on its own wall."""
        records = walled_thread(_handoff(NOW - timedelta(minutes=30)),
                                _rec(_marker(), NOW - timedelta(minutes=5)))
        reading = _read(records)
        self.assertIsNone(reading.found)
        self.assertIn("waiting on a claude limit death", reading.why)


# --- 5. Only the pipeline's own records count --------------------------------

class TheCredential(unittest.TestCase):

    def test_a_forged_marker_changes_nothing(self):
        forged = _rec(_marker(), MARKER_AT, pipeline=False)
        self.assertEqual(_read(died_thread(forged)), _read(died_thread()))

    def test_a_forged_recovery_receipt_changes_nothing(self):
        forged = dict(_recovery(10), authored_by_pipeline=False)
        self.assertEqual(_read(sent_back_thread(forged)),
                         _read(sent_back_thread()))

    def test_a_forged_hand_off_changes_nothing(self):
        forged = _handoff(NOW - timedelta(minutes=5), pipeline=False)
        self.assertEqual(_read(walled_thread(forged)), _read(walled_thread()))

    def test_a_marker_with_no_reset_reads_as_it_does_on_main(self):
        unknown = _rec(_marker(reset=None, assumed=False), MARKER_AT)
        with_marker = _read(died_thread(unknown))
        self.assertEqual(with_marker, _read(died_thread()))
        self.assertIsNotNone(with_marker.found)
        self.assertEqual(with_marker.found["firing"], 1)


# --- 6. The import direction -------------------------------------------------

def _module_level(body):
    """The statements a module runs at import, into `if`/`try` blocks but
    never into a function or class body: an import inside a function runs
    when it is called, so it cannot close a cycle at load time
    (`routing_verdict` reads `rereview_watch` that way)."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody", "handlers"):
            yield from _module_level(getattr(node, field, None) or [])


def _local_imports(name: str) -> set[str]:
    """The scripts `scripts/<name>.py` imports at load time."""
    path = os.path.join(SCRIPTS, f"{name}.py")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for node in _module_level(tree.body):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module.split(".")[0])
    return {m for m in out if os.path.exists(os.path.join(SCRIPTS, f"{m}.py"))}


class TheImportDirection(unittest.TestCase):

    def test_the_watcher_reads_limit_recovery(self):
        self.assertIn("limit_recovery", _local_imports("rereview_watch"))

    def test_nothing_the_watcher_imports_imports_it_back(self):
        seen: set[str] = set()
        todo = ["limit_recovery"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            todo.extend(_local_imports(name))
        self.assertNotIn("rereview_watch", seen)


if __name__ == "__main__":
    unittest.main()
