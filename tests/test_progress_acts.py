"""RED-first tests for DRE-3389 — the three LIFECYCLE acts, and their cadences.

`config/pipeline-acts.json` declared nineteen acts and the seven that carried a
`cadence_s` were every one of them a `recovery`. Nothing said how often a
HEALTHY build, review or gate wait is expected to show a sign of life, so the
console's pulse (DRE-3300) could only speak in the hour after a fix attempt or a
sweep — the CEO's recording of production on 2026-09-08 11:20 PT shows every row
standing still.

This card copies the console's declaration into the registry. It declares
nothing of its own:

  * a fourth `kind`, `progress` — an act that says the ordinary work is still
    moving. It holds nothing and repairs nothing;
  * `build-heartbeat`, `review-run` and `merge-gate-watch` under it, each
    carrying the `cadence_s` DRE-3388 MEASURED and declared first in
    `console/backend/receipts.py` on agent-bureau `main`.

THE NUMBERS ARE NOT THIS CARD'S TO CHOOSE. DRE-3388 measured them against live
Actions runs on 2026-09-08 and shipped them in the console (agent-bureau
PR #2370, merged); the ordering was inverted on purpose after the pre-approval
critic found that whoever built second would otherwise have to invent them.
So the tests below pin two different things and both matter:

  1. the registry says exactly what the measurement said — the numbers, and the
     measurement itself in each row's `why`, so no round number stands without
     one (offline, off this repo's own file);
  2. the registry and the console still AGREE — read out of agent-bureau at run
     time and compared, so a later edit to either side is a failure rather than
     a drift nobody sees. That one skips offline and is red in the `act registry
     consumers` job, exactly as its neighbour in
     `tests/test_pipeline_acts_consumers.py` is.

Run: cd bureau-pipeline && python3 -m pytest tests/test_progress_acts.py -v
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import check_act_consumers as guard  # noqa: E402
import pipeline_act  # noqa: E402

GUARD_SCRIPT = os.path.join(SCRIPTS, "check_act_consumers.py")
CONFIG = os.path.join(ROOT, "config", "pipeline-acts.json")
CONFIG_README = os.path.join(ROOT, "config", "README.md")
DOC = os.path.join(ROOT, "docs", "pipeline-acts.md")
MODULE = os.path.join(SCRIPTS, "pipeline_act.py")

#: The sentence the four documents that define an act must now carry, written
#: identically in each. Spelled out here rather than matched loosely: the whole
#: failure this pins is a document that goes on describing three kinds after a
#: fourth ships, which is exactly what a fuzzy match would let through.
FOUR_KINDS = "a refusal, a recovery, a hold or a progress act"

#: The three acts and the cadence DRE-3388 measured for each, in seconds.
#: Copied verbatim from the measurement comment on that card — this table is
#: the card's own restatement of it, and the live test further down is what
#: proves the console still says the same thing.
MEASURED = {
    "build-heartbeat": 6300,
    "review-run": 3000,
    "merge-gate-watch": 3900,
}

#: What each row's `why` must be able to show a reader who does not trust the
#: number: how many runs were looked at, when, and the longest green gap the
#: sample actually held. A round number with none of that is a guess.
MEASUREMENT_EVIDENCE = {
    "build-heartbeat": ("100 runs", "2026-09-08", "69.7 min", "4181", "6272"),
    "review-run": ("100 runs", "2026-09-08", "32.7 min", "1960", "2940"),
    "merge-gate-watch": ("100 runs", "2026-09-08", "1.0 min", "3900"),
}


def _doc() -> dict:
    return copy.deepcopy(pipeline_act.load())


# --------------------------------------------------------------------------- #
# 1. the fourth kind                                                           #
# --------------------------------------------------------------------------- #
class TestTheFourthKind(unittest.TestCase):
    def test_progress_is_a_declared_kind(self):
        self.assertIn("progress", pipeline_act.kinds())

    def test_the_kind_says_what_a_progress_act_is_and_is_not(self):
        """It holds nothing and repairs nothing — that is the whole distinction
        from the three kinds beside it, and it is what stops the next reader
        filing a heartbeat as a recovery."""
        said = pipeline_act.load()["kinds"]["progress"].lower()
        self.assertIn("moving", said)
        self.assertIn("holds nothing", said)
        self.assertIn("repairs nothing", said)

    def test_the_readme_sentence_names_all_four_kinds(self):
        """`an ACT is … a refusal, a recovery, or a hold` is written in four
        places in this repo and every one of them is now short by a kind. A
        change that contradicts a document updates that document in the same
        pull request (standards/engineering.md)."""
        for path in (CONFIG, CONFIG_README, DOC, MODULE):
            text = open(path, encoding="utf-8").read()
            self.assertIn(
                FOUR_KINDS, text,
                f"{path} does not name all four kinds — it still says three",
            )

    def test_the_readme_says_the_idempotency_key_rule_does_not_apply(self):
        """A heartbeat repeats BY DESIGN. Every other tag in this file is an
        idempotency key or a per-sha budget counter — `tag in body` suppresses
        the repeat — and a reader who applied that rule here would read a
        working build as a bug."""
        readme = " ".join(pipeline_act.load()["_readme"]).lower()
        self.assertIn("repeats by design", readme)
        self.assertIn("idempotency", readme)


# --------------------------------------------------------------------------- #
# 2. the three rows                                                            #
# --------------------------------------------------------------------------- #
class TestTheThreeLifecycleActs(unittest.TestCase):
    def test_the_registry_declares_all_three(self):
        for name in MEASURED:
            self.assertIn(name, pipeline_act.acts())

    def test_each_one_is_a_progress_act_that_moves_nothing(self):
        for name in MEASURED:
            entry = pipeline_act.record(name)
            self.assertEqual(entry["kind"], "progress", name)
            self.assertEqual(
                entry["state"], "unchanged",
                f"{name} announces that work is moving; it does not move it",
            )
            self.assertIsNone(
                entry["discharges"],
                f"{name} answers no prior obligation — nothing was owed",
            )

    def test_each_one_carries_the_measured_cadence(self):
        for name, seconds in MEASURED.items():
            self.assertEqual(pipeline_act.cadence_s(name), seconds, name)

    def test_each_row_carries_every_field_every_other_row_carries(self):
        """The same shape as the other nineteen, or the console's mirror reads a
        row with a hole in it off the same parse that gets it the tag."""
        reference = set(pipeline_act.record("fix-attempt-landed"))
        for name in MEASURED:
            self.assertEqual(set(pipeline_act.record(name)), reference, name)

    def test_each_why_carries_the_measurement(self):
        """Sample size, date, longest green gap — no round number without one.
        The acceptance criterion says it in those words, and this is it."""
        for name, evidence in MEASUREMENT_EVIDENCE.items():
            why = pipeline_act.record(name)["why"]
            self.assertIn("DRE-3388", why, name)
            for fragment in evidence:
                self.assertIn(fragment, why, f"{name}: {fragment!r} is missing")

    def test_the_tags_are_not_live_idempotency_keys(self):
        """A heartbeat repeats by design, so its tag is not a suppression key
        and nothing in the tree emits it. `problems()` proves the absence — the
        registry may not claim an emission it does not have."""
        for name in MEASURED:
            self.assertIs(pipeline_act.record(name)["adopted"], False, name)

    def test_the_registry_still_validates(self):
        self.assertEqual(pipeline_act.problems(), [])

    def test_no_progress_tag_collides_with_a_live_key(self):
        """`tag in body` is how every other receipt here is counted. A progress
        tag that were a substring of one of those would put a budget counter on
        a comment posted five times a build."""
        tags = [pipeline_act.tag(n) for n in pipeline_act.acts()]
        for name in MEASURED:
            mine = pipeline_act.tag(name)
            for other in tags:
                if other != mine:
                    self.assertNotIn(other, mine, f"{other!r} inside {mine!r}")
                    self.assertNotIn(mine, other, f"{mine!r} inside {other!r}")


# --------------------------------------------------------------------------- #
# 3. reading the console's numbers                                             #
# --------------------------------------------------------------------------- #
#: A console that declares its cadences on the act's own row, in milliseconds
#: — the shape the console serves `cadenceMs` from.
CONSOLE_AGREES = textwrap.dedent(
    '''
    """The console's receipt vocabulary."""

    _REVIEW_BOUND = 65 * 60 * 1000

    ACTS = {
        "build-heartbeat": {"kind": "progress", "cadence_ms": 6_300_000},
        "review-run": {"kind": "progress", "cadence_ms": 3_000_000},
        "merge-gate-watch": {"kind": "progress", "cadence_ms": _REVIEW_BOUND},
    }
    '''
)

#: The same console with ONE number moved. This is the difference the
#: acceptance criterion says the test must fail on.
CONSOLE_DISAGREES = CONSOLE_AGREES.replace("6_300_000", "6_000_000")

#: A console that keys its cadences in a table of its own, beside `ACTS`.
#: The reader must find them there too — the console owns its own shape, and a
#: guard that pinned one would go red on a refactor that changed nothing about
#: what the console knows.
CONSOLE_SEPARATE_TABLE = textwrap.dedent(
    '''
    ACTS = {"build-heartbeat": "progress", "review-run": "progress",
            "merge-gate-watch": "progress"}

    CADENCE_MS = {
        "build-heartbeat": 6_300_000,
        "review-run": 3_000_000,
        "merge-gate-watch": 3_900_000,
    }
    '''
)


class TestTheConsoleCadenceReader(unittest.TestCase):
    """What `ok` means here, because it is two different questions.

    A DIFFERENCE is always a failure: the console declares a number for an act
    and this file declares another. An act whose number cannot be found at all
    is `unconfirmed` — reported, and fatal only for a `progress` act, which is
    the one thing this card exists to keep equal. The asymmetry is deliberate:
    the console owns its own literal, and a producer-side reader that demanded
    to locate every one of them would go red on a console refactor that changed
    nothing about what the console knows.
    """

    def test_a_console_that_agrees_is_ok(self):
        report = guard.cadences(doc=_doc(), source=CONSOLE_AGREES)
        self.assertFalse(report.skipped, report.text())
        self.assertTrue(report.ok, report.text())

    def test_one_number_moved_is_a_failure_that_names_both(self):
        report = guard.cadences(doc=_doc(), source=CONSOLE_DISAGREES)
        self.assertFalse(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        text = report.text()
        self.assertIn("build-heartbeat", text)
        self.assertIn("6300", text)

    def test_a_cadence_table_beside_ACTS_is_read_too(self):
        report = guard.cadences(doc=_doc(), source=CONSOLE_SEPARATE_TABLE)
        self.assertFalse(report.skipped, report.text())
        self.assertTrue(report.ok, report.text())

    def test_seconds_and_milliseconds_are_the_same_number(self):
        """The console serves `cadenceMs`; this file declares seconds. The
        comparison is of the CADENCE, not of the unit either side stores it in
        — and 6300 against 6_300_000 is agreement, not a difference."""
        report = guard.cadences(
            doc=_doc(),
            source='ACTS = {"build-heartbeat": 6300, "review-run": 3000, '
                   '"merge-gate-watch": 3900}',
        )
        self.assertTrue(report.ok, report.text())

    def test_a_console_that_declares_no_cadence_at_all_is_a_failure(self):
        """Not a silent pass. A progress act whose number the console does not
        carry is the drift this card exists to catch, and it looks exactly like
        a console that has not shipped DRE-3388 yet."""
        report = guard.cadences(
            doc=_doc(), source='ACTS = {"build-heartbeat": "progress"}'
        )
        self.assertFalse(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        self.assertIn("build-heartbeat", report.text())

    def test_an_existing_act_the_reader_cannot_place_is_reported_not_failed(self):
        """The console owns its own literal. `fix-loop-restarted` is not this
        card's business, so a console this reader cannot find its number in is
        said out loud and left alone — only a DIFFERENCE fails."""
        report = guard.cadences(doc=_doc(), source=CONSOLE_AGREES)
        self.assertTrue(report.ok, report.text())
        self.assertIn("fix-loop-restarted", report.text())

    def test_an_unreadable_console_is_a_skip_never_a_pass(self):
        report = guard.cadences(doc=_doc(), source=None, reason="no token here")
        self.assertTrue(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        self.assertIn("no token here", report.text())

    def test_an_unparseable_console_is_a_skip(self):
        report = guard.cadences(doc=_doc(), source="ACTS = {")
        self.assertTrue(report.skipped, report.text())

    def test_the_cli_answers_zero_one_and_three(self):
        def run(env_extra, expected):
            env = {
                k: v for k, v in os.environ.items()
                if k not in ("BUREAU_CONSOLE_TOKEN", "BUREAU_CONSOLE_ACTS_FILE")
            }
            env.update(env_extra)
            out = subprocess.run(
                [sys.executable, GUARD_SCRIPT, "cadences"],
                capture_output=True, text=True, cwd=ROOT, env=env, check=False,
            )
            self.assertEqual(out.returncode, expected, out.stdout + out.stderr)
            return out

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            agrees = os.path.join(tmp, "receipts.py")
            with open(agrees, "w", encoding="utf-8") as fh:
                fh.write(CONSOLE_AGREES)
            disagrees = os.path.join(tmp, "other.py")
            with open(disagrees, "w", encoding="utf-8") as fh:
                fh.write(CONSOLE_DISAGREES)

            run({"BUREAU_CONSOLE_ACTS_FILE": agrees}, guard.EXIT_OK)
            run({"BUREAU_CONSOLE_ACTS_FILE": disagrees}, guard.EXIT_GAP)
            skipped = run({}, guard.EXIT_SKIPPED)
            self.assertIn("BUREAU_CONSOLE_TOKEN", skipped.stdout)


# --------------------------------------------------------------------------- #
# 4. the existing consumer guard still passes with three more acts             #
# --------------------------------------------------------------------------- #
class TestTheConsumerGuardCoversTheNewActs(unittest.TestCase):
    def test_the_three_acts_are_checked_against_the_console_like_any_other(self):
        """`check` reads NAMES and TAGS; the console keys these three on the
        names the card gave them, which is why the registry uses those names."""
        report = guard.check(doc=_doc(), source=CONSOLE_AGREES)
        self.assertFalse(report.skipped, report.text())
        for name in MEASURED:
            self.assertNotIn(name, report.unknown, report.text())

    def test_a_console_that_never_heard_of_them_fails_the_existing_guard(self):
        report = guard.check(doc=_doc(), source='ACTS = {"reviewer-down": "hold"}')
        self.assertFalse(report.ok)
        for name in MEASURED:
            self.assertIn(name, report.unknown)


# --------------------------------------------------------------------------- #
# 5. the file on disk is what everything else reads                            #
# --------------------------------------------------------------------------- #
class TestTheShippedFile(unittest.TestCase):
    def test_the_check_command_still_exits_zero(self):
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "pipeline_act.py"), "check"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_list_carries_the_three_new_rows(self):
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "pipeline_act.py"), "list"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rows = {r["act"]: r for r in json.loads(out.stdout)}
        for name, seconds in MEASURED.items():
            self.assertEqual(rows[name]["cadence_s"], seconds)
            self.assertEqual(rows[name]["kind"], "progress")


if __name__ == "__main__":
    unittest.main()
