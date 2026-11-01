"""Once a day, each proof open more than seven days says what it waits on (DRE-8626).

DRE-8625 wrote `proof_overdue.reading`, the one reader of what an overdue
`PROOF:` card waits on, and nothing called it. These tests pin the daily pass
that does, `scripts/proof_overdue_sweep.py`:

  * one comment per overdue card per PT day, its first line the contract's,
    keyed on a pipeline-authored comment for today's PT day — across 23:59
    and 00:00 PT and the daylight-saving change, and never on a copy of the
    mark somebody else posted;
  * nothing posted on a card in `Green Light`, which the note names instead;
  * the note line on every pass, one WARN alert a day keyed on the PT day,
    the previous day's key resolved;
  * a dry run that writes nothing, and a card or write that fails turning the
    step red only after every other card is handled — except a card read
    GitHub refused for its rate limit, which is named, skipped and read on
    the next pass (DRE-8696);
  * the `Proof overdue` step in `reconcile.yml`, and the two config entries.

Run: cd bureau-pipeline && python3 -m pytest tests/test_proof_overdue_sweep.py -v
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_overdue  # noqa: E402
import proof_overdue_sweep as sweep_mod  # noqa: E402
import proof_reach  # noqa: E402

SLUG = "bureau-pipeline"
WORKFLOW = ROOT / ".github" / "workflows" / "reconcile.yml"
STEP = "Proof overdue"
DISPATCH = "Dispatch proof runs"
STUBS = (".github/workflows/self-proof-task.yml", ".github/workflows/proof-task.yml")


def _pt(y, mo, d, h, mi, offset_hours):
    """A UTC instant from a Pacific wall clock at the given UTC offset."""
    return datetime(y, mo, d, h, mi, tzinfo=timezone(timedelta(hours=offset_hours))
                    ).astimezone(timezone.utc)


#: 2026-11-01 is the day daylight saving ends: PDT (-7) until 02:00, PST (-8) after.
EVE = _pt(2026, 10, 31, 23, 59, -7)
MIDNIGHT = _pt(2026, 11, 1, 0, 0, -7)
NOON = _pt(2026, 11, 1, 12, 0, -8)
LAST_MINUTE = _pt(2026, 11, 1, 23, 59, -8)
NEXT_DAY = _pt(2026, 11, 2, 0, 0, -8)


def _diag(card="DRE-6008", age=12.4, cls="release", waits_on="DRE-7001",
          evidence="🔬 proof-waiting: the release is not cut — needs DRE-7001 shipped",
          eliminated=False):
    return proof_overdue.Diagnosis(
        card=card, repo=SLUG, age_days=age, clock_from=NOON - timedelta(days=age),
        cls=cls, action=proof_overdue.ACTIONS[cls], waits_on=waits_on, not_before=None,
        by_elimination=eliminated, evidence=evidence)


class World:
    """The board one sweep reads, every write recorded.

    `cards` maps each open PROOF card to `(lane, answer)`: the answer is the
    `Diagnosis` `proof_overdue.reading` returns, None for a card under the
    age, or an exception it raises. A comment posted lands in the card's
    thread as the pipeline's, so a later pass reads it back."""

    def __init__(self, cards, threads=None, *, list_error=None, post_errors=(),
                 capped=(), thread_errors=(), door="up"):
        self.cards = dict(cards)
        self.threads = {k: list(v) for k, v in (threads or {}).items()}
        self.list_error = list_error
        self.post_errors, self.capped = set(post_errors), set(capped)
        self.thread_errors = set(thread_errors)
        self.door = door
        self.posted, self.raised, self.resolved, self.read = [], [], [], []
        self.open_keys = set()

    # proof_overdue.LinearReads, as the sweep hands it to `reading`
    def proofs(self, slug):
        assert slug == SLUG, slug
        if self.list_error:
            raise self.list_error
        return list(self.cards)

    def card(self, ident):
        return {"identifier": ident, "state": {"name": self.cards[ident][0]}}

    def thread(self, ident):
        return [], "pipeline"

    def states(self, ids):
        return {}

    def reading(self, card, *, now, gh=None, linear=None):
        self.read.append(card)
        linear.card(card)
        answer = self.cards[card][1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    # linear_ops
    def comment_records(self, card, **_):
        if card in self.thread_errors:
            raise RuntimeError("Linear answered 503")
        return list(self.threads.get(card, []))

    def cmd_comment(self, card, body, *flags):
        if card in self.post_errors:
            raise RuntimeError("Linear refused the comment")
        if card in self.capped:
            return linear_ops.COMMENT_CAP_CONDITION
        self.posted.append((card, body))
        self.threads.setdefault(card, []).append(
            {"body": body, "authored_by_pipeline": True, "created_at": None})
        return None

    # pipeline_notice
    def raise_notice(self, key, **kw):
        self.raised.append((key, kw))
        if self.door == "down":
            return "lost"
        if key in self.open_keys:
            return "repeat"
        self.open_keys.add(key)
        return "raised"

    def resolve_notice(self, key, **_):
        self.resolved.append(key)
        if key in self.open_keys:
            self.open_keys.discard(key)
            return "resolved"
        return "not-open"

    def run(self, now, *, live=True):
        out = io.StringIO()
        with mock.patch.object(proof_overdue, "reading", self.reading), \
                mock.patch.object(linear_ops, "comment_records", self.comment_records), \
                mock.patch.object(linear_ops, "cmd_comment", self.cmd_comment), \
                mock.patch.object(pipeline_notice, "raise_notice", self.raise_notice), \
                mock.patch.object(pipeline_notice, "resolve_notice", self.resolve_notice), \
                redirect_stdout(out):
            status = sweep_mod.sweep(SLUG, now=now, live=live, linear=self, gh=lambda a: "")
        return status, out.getvalue()

    def posted_on(self, card):
        return [body for c, body in self.posted if c == card]


def _lines(text):
    return [line for line in text.splitlines() if line]


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


class ContractTest(unittest.TestCase):

    def test_the_tag_is_dre_8625s(self):
        self.assertEqual(proof_overdue.TAG, sweep_mod.TAG)

    def test_the_mark(self):
        self.assertEqual("⏳ proof-overdue:", sweep_mod.MARK)

    def test_the_first_line(self):
        self.assertEqual(
            "⏳ proof-overdue: 2026-11-01 · class=release · action=rerun-on-release · "
            "waits-on=DRE-7001 · age=12.4d",
            sweep_mod.first_line(_diag(), "2026-11-01"))

    def test_the_note_key(self):
        self.assertEqual("proof_overdue:bureau-pipeline:2026-11-01",
                         sweep_mod.note_key(SLUG, "2026-11-01"))


class PacificDayTest(unittest.TestCase):

    def test_the_day_turns_at_midnight_pacific(self):
        self.assertEqual("2026-10-31", sweep_mod.pt_day(EVE))
        self.assertEqual("2026-11-01", sweep_mod.pt_day(MIDNIGHT))

    def test_the_day_turns_at_midnight_standard_time_after_the_change(self):
        # 07:59 UTC: a fixed seven-hour offset would read 2026-11-02 00:59.
        self.assertEqual("2026-11-01", sweep_mod.pt_day(LAST_MINUTE))
        self.assertEqual("2026-11-02", sweep_mod.pt_day(NEXT_DAY))

    def test_the_spring_change_too(self):
        self.assertEqual("2027-03-13", sweep_mod.pt_day(_pt(2027, 3, 13, 23, 59, -8)))
        self.assertEqual("2027-03-14", sweep_mod.pt_day(_pt(2027, 3, 14, 0, 0, -8)))
        self.assertEqual("2027-03-14", sweep_mod.pt_day(_pt(2027, 3, 14, 23, 59, -7)))

    def test_the_previous_day(self):
        self.assertEqual("2026-10-31", sweep_mod.previous_day("2026-11-01"))
        self.assertEqual("2027-02-28", sweep_mod.previous_day("2027-03-01"))


# --------------------------------------------------------------------------- #
# the comment                                                                  #
# --------------------------------------------------------------------------- #


class CommentTest(unittest.TestCase):

    def test_one_comment_on_the_first_pass_and_none_on_the_second(self):
        world = World({"DRE-6008": ("In Review", _diag())})
        status, _ = world.run(MIDNIGHT)
        self.assertEqual(0, status)
        bodies = world.posted_on("DRE-6008")
        self.assertEqual(1, len(bodies))
        self.assertEqual(sweep_mod.first_line(_diag(), "2026-11-01"),
                         bodies[0].split("\n", 1)[0])
        status, out = world.run(NOON)
        self.assertEqual(0, status)
        self.assertEqual(1, len(world.posted_on("DRE-6008")), out)

    def test_the_day_turns_at_pacific_midnight(self):
        world = World({"DRE-6008": ("In Review", _diag())})
        world.run(EVE)
        world.run(MIDNIGHT)
        days = [b.split(" · ", 1)[0] for b in world.posted_on("DRE-6008")]
        self.assertEqual(["⏳ proof-overdue: 2026-10-31", "⏳ proof-overdue: 2026-11-01"], days)

    def test_across_the_daylight_saving_change(self):
        world = World({"DRE-6008": ("In Review", _diag())})
        world.run(MIDNIGHT)       # 00:00 PDT
        world.run(LAST_MINUTE)    # 23:59 PST, 25 hours later and the same day
        self.assertEqual(1, len(world.posted_on("DRE-6008")))
        world.run(NEXT_DAY)       # 00:00 PST
        self.assertEqual(2, len(world.posted_on("DRE-6008")))
        self.assertTrue(world.posted_on("DRE-6008")[1].startswith("⏳ proof-overdue: 2026-11-02 · "))

    def test_a_copy_of_the_mark_from_anyone_else_counts_for_nothing(self):
        copy = sweep_mod.first_line(_diag(), "2026-11-01")
        world = World({"DRE-6008": ("In Review", _diag())},
                      {"DRE-6008": [{"body": copy, "authored_by_pipeline": False}]})
        world.run(NOON)
        self.assertEqual(1, len(world.posted_on("DRE-6008")))

    def test_yesterdays_comment_is_not_todays(self):
        old = sweep_mod.first_line(_diag(), "2026-10-31")
        world = World({"DRE-6008": ("In Review", _diag())},
                      {"DRE-6008": [{"body": old, "authored_by_pipeline": True}]})
        world.run(NOON)
        self.assertEqual(1, len(world.posted_on("DRE-6008")))

    def test_the_mark_must_open_the_first_line(self):
        quoted = "As the sweep said:\n" + sweep_mod.first_line(_diag(), "2026-11-01")
        world = World({"DRE-6008": ("In Review", _diag())},
                      {"DRE-6008": [{"body": quoted, "authored_by_pipeline": True}]})
        world.run(NOON)
        self.assertEqual(1, len(world.posted_on("DRE-6008")))

    def test_a_card_under_the_age_gets_nothing(self):
        world = World({"DRE-6008": ("In Review", None)})
        status, out = world.run(NOON)
        self.assertEqual(0, status)
        self.assertEqual([], world.posted)
        self.assertIn("· 0 proof record(s) older than 7 days · oldest none", out)

    def test_a_card_in_green_light_gets_no_comment_and_is_named_in_the_note(self):
        world = World({"DRE-6008": ("Green Light", _diag()),
                       "DRE-5357": ("In Review", _diag("DRE-5357", age=9.0))})
        status, out = world.run(NOON)
        self.assertEqual(0, status)
        self.assertEqual([], world.posted_on("DRE-6008"))
        self.assertEqual(1, len(world.posted_on("DRE-5357")))
        note = next(line for line in _lines(out) if line.startswith("proof-overdue: note"))
        self.assertIn("· 2 proof record(s) older than 7 days", note)
        self.assertIn("DRE-6008", note)
        self.assertIn("in Green Light: DRE-6008", note)
        self.assertNotIn("in Green Light: DRE-5357", note)

    def test_the_body_says_it_in_plain_english(self):
        world = World({"DRE-6008": ("In Review", _diag())})
        world.run(NOON)
        body = world.posted_on("DRE-6008")[0]
        rest = body.split("\n", 1)[1]
        self.assertIn("open 12.4 days", rest)
        self.assertIn("DRE-7001", rest)
        self.assertIn("No person needs to act", rest)
        self.assertIn("> 🔬 proof-waiting: the release is not cut — needs DRE-7001 shipped", rest)
        self.assertNotIn("`", rest)
        self.assertNotIn("elimination", rest)
        for cls in proof_overdue.CLASSES:
            text = sweep_mod.comment(_diag(cls=cls, waits_on="x"), "2026-11-01")
            with self.subTest(cls=cls):
                self.assertIn("next", text.split("\n", 1)[1].lower())

    def test_an_elimination_is_said(self):
        d = _diag(cls="unrepeatable", waits_on="the console showed the banner",
                  eliminated=True)
        self.assertIn("by elimination", sweep_mod.comment(d, "2026-11-01"))


# --------------------------------------------------------------------------- #
# the note and its alert                                                       #
# --------------------------------------------------------------------------- #


class NoteTest(unittest.TestCase):

    def test_the_note_line(self):
        world = World({"DRE-6008": ("In Review", _diag(age=12.4)),
                       "DRE-5357": ("In Review", _diag("DRE-5357", age=8.0))})
        _, out = world.run(NOON)
        self.assertIn("proof-overdue: note · bureau-pipeline · 2 proof record(s) older than "
                      "7 days · oldest DRE-6008 (12.4 days, class=release)", _lines(out))

    def test_the_oldest_is_the_largest_age_and_a_tie_goes_to_the_lower_number(self):
        got = sweep_mod.oldest([_diag("DRE-10", age=9.0), _diag("DRE-9", age=9.0),
                                _diag("DRE-100", age=8.0)])
        self.assertEqual("DRE-9", got.card)
        got = sweep_mod.oldest([_diag("DRE-10", age=9.5), _diag("DRE-9", age=9.0)])
        self.assertEqual("DRE-10", got.card)
        self.assertIsNone(sweep_mod.oldest([]))

    def test_one_warn_alert_a_day_and_yesterdays_resolved(self):
        world = World({"DRE-6008": ("In Review", _diag())})
        world.open_keys.add("proof_overdue:bureau-pipeline:2026-10-31")
        _, out = world.run(MIDNIGHT)
        note = next(line for line in _lines(out) if line.startswith("proof-overdue: note"))
        self.assertEqual(["proof_overdue:bureau-pipeline:2026-11-01"],
                         [k for k, _ in world.raised])
        kw = world.raised[0][1]
        self.assertEqual("WARN", kw["severity"])
        self.assertIn(note, kw["body"])
        self.assertIn("proof_overdue:bureau-pipeline:2026-10-31", world.resolved)
        self.assertNotIn("proof_overdue:bureau-pipeline:2026-10-31", world.open_keys)
        # The rest of the day delivers nothing new: one key, open once.
        world.run(NOON)
        world.run(LAST_MINUTE)
        self.assertEqual({"proof_overdue:bureau-pipeline:2026-11-01"},
                         {k for k, _ in world.raised})
        self.assertEqual({"proof_overdue:bureau-pipeline:2026-11-01"}, world.open_keys)
        # The next day raises its own key and resolves this one.
        world.run(NEXT_DAY)
        self.assertEqual({"proof_overdue:bureau-pipeline:2026-11-02"}, world.open_keys)

    def test_with_none_overdue_nothing_is_raised_and_yesterdays_is_resolved(self):
        world = World({"DRE-6008": ("In Review", None)})
        world.open_keys.add("proof_overdue:bureau-pipeline:2026-10-31")
        status, out = world.run(NOON)
        self.assertEqual(0, status)
        self.assertEqual([], world.raised)
        self.assertEqual(["proof_overdue:bureau-pipeline:2026-10-31"], world.resolved)
        self.assertIn("proof-overdue: note · bureau-pipeline · 0 proof record(s) older than "
                      "7 days · oldest none", _lines(out))

    def test_a_green_light_card_alone_still_raises(self):
        world = World({"DRE-6008": ("Green Light", _diag())})
        world.run(NOON)
        self.assertEqual([], world.posted)
        self.assertEqual(["proof_overdue:bureau-pipeline:2026-11-01"],
                         [k for k, _ in world.raised])


# --------------------------------------------------------------------------- #
# the dry run                                                                  #
# --------------------------------------------------------------------------- #


class DryRunTest(unittest.TestCase):

    def test_a_dry_pass_writes_nothing_and_says_what_it_would(self):
        world = World({"DRE-6008": ("In Review", _diag()),
                       "DRE-6673": ("Green Light", _diag("DRE-6673", age=20.0))})
        status, out = world.run(NOON, live=False)
        self.assertEqual(0, status)
        self.assertEqual(([], [], []), (world.posted, world.raised, world.resolved))
        lines = _lines(out)
        self.assertIn("would: comment DRE-6008 — "
                      + sweep_mod.first_line(_diag(), "2026-11-01"), lines)
        self.assertTrue(any(line.startswith("would: raise proof_overdue:bureau-pipeline:2026-11-01")
                            for line in lines), out)
        self.assertIn("would: resolve proof_overdue:bureau-pipeline:2026-10-31", lines)
        self.assertFalse(any(line.startswith("would: comment DRE-6673") for line in lines))
        self.assertTrue(any(line.startswith("proof-overdue: note · ") for line in lines))

    def test_a_dry_pass_with_none_overdue_still_prints_the_note(self):
        world = World({})
        _, out = world.run(NOON, live=False)
        self.assertIn("proof-overdue: note · bureau-pipeline · 0 proof record(s) older than "
                      "7 days · oldest none", _lines(out))
        self.assertEqual([], world.resolved)

    def test_the_switch_is_exactly_true(self):
        for value, live in (("true", True), ("True", False), ("1", False), ("", False),
                            (None, False)):
            env = {} if value is None else {"PROOF_DISPATCH_LIVE": value}
            with self.subTest(value=value):
                self.assertEqual(live, sweep_mod.is_live(env))


# --------------------------------------------------------------------------- #
# failures: named, every other card handled, red at the end                    #
# --------------------------------------------------------------------------- #


class FailureTest(unittest.TestCase):

    def test_an_unreadable_card_is_named_and_the_rest_are_handled(self):
        world = World({"DRE-1": ("In Review", RuntimeError("GitHub answered 502")),
                       "DRE-2": ("In Review", _diag("DRE-2"))})
        status, out = world.run(NOON)
        self.assertEqual(1, status)
        self.assertIn("proof-overdue: DRE-1 · could not be read — GitHub answered 502",
                      _lines(out))
        self.assertEqual(1, len(world.posted_on("DRE-2")))
        self.assertEqual([], world.posted_on("DRE-1"))
        self.assertTrue(world.raised)

    def test_a_rate_limited_card_is_skipped_and_named_and_the_step_stays_green(self):
        # DRE-8696: Foundry's Reconcile went red because GitHub throttled the
        # shared installation while one proof record was read. The main sweep
        # records that as degraded; this pass now does the same.
        refused = RuntimeError("gh: API rate limit exceeded for installation ID 123249480")
        world = World({"DRE-1": ("In Review", refused),
                       "DRE-2": ("In Review", _diag("DRE-2"))})
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            status, out = world.run(NOON)
        self.assertEqual(0, status, out + err.getvalue())
        skipped = [line for line in _lines(out) if line.startswith("proof-overdue: DRE-1 · ")]
        self.assertEqual(1, len(skipped), out)
        self.assertIn("rate limit", skipped[0])
        self.assertIn("next pass", skipped[0])
        self.assertNotIn("ERROR:", err.getvalue())
        self.assertNotIn("ERROR:", out)
        self.assertEqual([], world.posted_on("DRE-1"))
        self.assertEqual(1, len(world.posted_on("DRE-2")))
        self.assertIn("proof-overdue: note · bureau-pipeline · 1 proof record(s) older than "
                      "7 days · oldest DRE-2 (12.4 days, class=release)", _lines(out))

    def test_the_rate_limit_is_read_from_the_whole_error_not_the_trimmed_one(self):
        # `_why` keeps 300 characters; GitHub's wording past them still counts.
        refused = RuntimeError("x" * 400 + " API rate limit exceeded for installation ID 1")
        world = World({"DRE-1": ("In Review", refused)})
        status, out = world.run(NOON)
        self.assertEqual(0, status, out)
        self.assertNotIn("could not be read", out)

    def test_a_permission_refusal_is_still_red(self):
        world = World({"DRE-1": ("In Review",
                                 RuntimeError("HTTP 403: Resource not accessible by integration"))})
        status, out = world.run(NOON)
        self.assertEqual(1, status)
        self.assertIn("proof-overdue: DRE-1 · could not be read — HTTP 403", out)

    def test_a_thread_that_cannot_be_read_posts_nothing_and_is_red(self):
        world = World({"DRE-1": ("In Review", _diag("DRE-1")),
                       "DRE-2": ("In Review", _diag("DRE-2"))}, thread_errors={"DRE-1"})
        status, out = world.run(NOON)
        self.assertEqual(1, status)
        self.assertEqual([], world.posted_on("DRE-1"))
        self.assertEqual(1, len(world.posted_on("DRE-2")))
        self.assertTrue(any(line.startswith("proof-overdue: DRE-1 · ") for line in _lines(out)))

    def test_a_comment_that_does_not_land_is_red_after_the_rest(self):
        world = World({"DRE-1": ("In Review", _diag("DRE-1")),
                       "DRE-2": ("In Review", _diag("DRE-2")),
                       "DRE-3": ("In Review", _diag("DRE-3"))},
                      post_errors={"DRE-1"}, capped={"DRE-2"})
        status, out = world.run(NOON)
        self.assertEqual(1, status)
        self.assertEqual(1, len(world.posted_on("DRE-3")))
        self.assertTrue(world.raised)
        self.assertIn("DRE-1", out)
        self.assertIn("DRE-2", out)

    def test_an_alert_that_does_not_land_is_red(self):
        world = World({"DRE-1": ("In Review", _diag("DRE-1"))}, door="down")
        status, _ = world.run(NOON)
        self.assertEqual(1, status)
        self.assertEqual(1, len(world.posted_on("DRE-1")))
        self.assertTrue(world.resolved)

    def test_an_unread_list_is_red_and_writes_nothing(self):
        world = World({}, list_error=RuntimeError("Linear answered 500"))
        status, out = world.run(NOON)
        self.assertEqual(1, status)
        self.assertEqual(([], [], []), (world.posted, world.raised, world.resolved))
        self.assertIn("could not be read", out)


# --------------------------------------------------------------------------- #
# the docstring                                                                #
# --------------------------------------------------------------------------- #


class DocstringTest(unittest.TestCase):

    def test_it_says_when_what_and_the_key(self):
        doc = " ".join((sweep_mod.__doc__ or "").split())
        self.assertIn("once per PT day", doc)
        self.assertIn("authored_by_pipeline", doc)
        self.assertIn("never comments on a card in `Green Light`", doc)
        self.assertIn("printed on every pass", doc)
        self.assertIn("proof_overdue:<slug>:<YYYY-MM-DD>", doc)


# --------------------------------------------------------------------------- #
# the two config entries                                                       #
# --------------------------------------------------------------------------- #


class ConfigTest(unittest.TestCase):

    def test_the_proof_reach_log_line(self):
        doc = json.loads((ROOT / "config" / "proof-reach.json").read_text(encoding="utf-8"))
        entry = next(e for e in doc["log_lines"] if e["tag"] == "proof-overdue:")
        self.assertEqual(("scripts/proof_overdue_sweep.py", "TAG"),
                         (entry["printed_by"], entry["constant"]))
        self.assertEqual([], proof_reach.check(ROOT))

    def test_the_unconverted_entry(self):
        doc = json.loads((ROOT / "config" / "pipeline-acts.json").read_text(encoding="utf-8"))
        rows = [u for u in doc["unconverted"] if u["file"] == "scripts/proof_overdue_sweep.py"]
        self.assertEqual(1, len(rows))
        self.assertIn(rows[0]["anchor"],
                      (ROOT / "scripts" / "proof_overdue_sweep.py").read_text(encoding="utf-8"))
        self.assertIn("DRE-8626", rows[0]["means"])

    def test_the_act_checks_pass(self):
        for argv in (["scripts/pipeline_act.py", "check"], ["scripts/check_act_receipts.py"]):
            done = subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True,
                                  text=True, timeout=120)
            with self.subTest(argv=argv):
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)


# --------------------------------------------------------------------------- #
# the workflow step                                                            #
# --------------------------------------------------------------------------- #


def _steps() -> list:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["sweep"]["steps"]


def _step(name: str) -> dict:
    found = [s for s in _steps() if s.get("name") == name]
    assert len(found) == 1, f"{name!r} is not one step of reconcile.yml"
    return found[0]


_FAKE = """\
import json, os, sys
open(os.environ["CALLS"], "a").write(json.dumps([sys.argv[0].rsplit("/", 1)[-1]] + sys.argv[1:]) + "\\n")
print("{line}")
sys.exit(int(os.environ.get("{exit_var}") or 0))
"""


class StepShapeTest(unittest.TestCase):

    def test_it_runs_after_the_proof_dispatch(self):
        names = [s.get("name") for s in _steps()]
        self.assertIn(STEP, names)
        self.assertLess(names.index(DISPATCH), names.index(STEP))
        # Right after the dispatch's own pair: the record close stays beside it.
        self.assertEqual(names.index("Close proof records that cannot merge") + 1,
                         names.index(STEP))

    def test_it_runs_under_the_dispatchs_gate(self):
        self.assertEqual(_step(DISPATCH)["if"], _step(STEP)["if"])

    def test_it_carries_the_dispatchs_tokens_key_and_switch(self):
        env, dispatch = _step(STEP)["env"], _step(DISPATCH)["env"]
        for name in ("GH_TOKEN", "GH_READ_TOKEN", "LINEAR_API_KEY", "REPO",
                     "PROOF_DISPATCH_LIVE", "BUREAU_READ", "BUREAU_PIPELINE_REF"):
            with self.subTest(name=name):
                self.assertEqual(dispatch[name], env[name])

    def test_it_asks_for_the_stub_as_the_dispatch_does(self):
        def check(run):
            return [line.strip() for line in run.splitlines()
                    if line.strip().startswith("if [ ! -f ")]
        self.assertEqual(check(_step(DISPATCH)["run"]), check(_step(STEP)["run"]))


class StepRunTest(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.scripts = self.tmp / ".bureau-pipeline" / "scripts"
        self.scripts.mkdir(parents=True)
        (self.scripts / "proof_overdue_sweep.py").write_text(_FAKE.format(
            line="proof-overdue: note · widgets · 1 proof record(s) older than 7 days · "
                 "oldest DRE-1 (9.0 days, class=date)", exit_var="SWEEP_EXIT"), encoding="utf-8")
        (self.tmp / ".github" / "workflows").mkdir(parents=True)
        self.calls = self.tmp / "calls.jsonl"
        self.summary = self.tmp / "summary.md"

    def tearDown(self):
        self._dir.cleanup()

    def _ship_act(self):
        (self.scripts / "proof_overdue_act.py").write_text(_FAKE.format(
            line="proof-overdue-act: acted", exit_var="ACT_EXIT"), encoding="utf-8")

    def _run(self, *, stub=True, sweep_exit=0, act_exit=0):
        if stub:
            (self.tmp / STUBS[0]).write_text("name: proof\n", encoding="utf-8")
        env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(self.tmp)),
               "CALLS": str(self.calls), "GITHUB_STEP_SUMMARY": str(self.summary),
               "GITHUB_REPOSITORY": "acme/Widgets", "REPO": "acme/Widgets",
               "SWEEP_EXIT": str(sweep_exit), "ACT_EXIT": str(act_exit)}
        return subprocess.run(["bash", "-e", "-c", _step(STEP)["run"]], cwd=self.tmp,
                              env=env, capture_output=True, text=True, timeout=60)

    def _called(self):
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines() if line]

    def test_with_no_stub_it_reads_nothing(self):
        self._ship_act()
        done = self._run(stub=False)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual([], self._called())
        self.assertIn("proof-overdue: no proof-run stub in this repository", done.stdout)

    def test_without_the_action_step_it_says_so_and_runs_the_sweep(self):
        done = self._run()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual([["proof_overdue_sweep.py", "--repo", "widgets"]], self._called())
        self.assertIn("proof-overdue: action step not shipped yet", done.stdout)
        self.assertIn("proof-overdue: note · widgets", self.summary.read_text(encoding="utf-8"))

    def test_with_the_action_step_it_runs_it_after_the_sweep(self):
        self._ship_act()
        done = self._run()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual([["proof_overdue_sweep.py", "--repo", "widgets"],
                          ["proof_overdue_act.py"]], self._called())
        self.assertNotIn("not shipped yet", done.stdout)

    def test_a_failed_sweep_still_runs_the_action_and_turns_the_step_red(self):
        self._ship_act()
        done = self._run(sweep_exit=1)
        self.assertNotEqual(0, done.returncode)
        self.assertEqual(2, len(self._called()))

    def test_a_failed_action_turns_the_step_red(self):
        self._ship_act()
        done = self._run(act_exit=3)
        self.assertNotEqual(0, done.returncode)
        self.assertEqual(2, len(self._called()))


if __name__ == "__main__":
    unittest.main()
