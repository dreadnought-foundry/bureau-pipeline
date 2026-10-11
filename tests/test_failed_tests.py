"""The failing test ids, read out of a failed run's saved log (DRE-6870).

A test that fails on main and passes on the retry was forgotten each time: the
repair read the failed log for machine trouble and a timed-out step, and the
filter it reads through throws every `FAILED …` line away. So the same flaky
test turned main red again and again before anyone fixed it — agent-bureau's
review-snapshot clock test four times in three days, 2026-10-06 to 10-08.

`failed_tests.failed_tests(log_text)` is the new read: the failing test ids in
log order, no repeats, from two shapes and nothing else — pytest's `FAILED`
summary line and vitest's `FAIL file > suite > test` line. The two real lines
below are quoted from the card, as GitHub saved them.

Run: cd bureau-pipeline && python3 -m pytest tests/test_failed_tests.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import failed_tests  # noqa: E402

ESC = "\x1b"

#: agent-bureau CI run 38123079608, 2026-10-11.
PYTEST_LINE = (
    "Console backend shard 2\tUNKNOWN STEP\t2026-10-11T08:20:22.2855317Z FAILED "
    "console/backend/tests/test_planner_run.py::"
    "test_the_default_fetch_answers_from_the_record_never_github - "
    "console.backend.record_reads.RecordGap: the Record does not hold "
    "2026-10-08T08:02:44Z → 2026-10-11T08:13:03Z: the heartbeat has not been "
    "seen since 2026-10-11T08:02:44Z"
)
PYTEST_ID = (
    "console/backend/tests/test_planner_run.py::"
    "test_the_default_fetch_answers_from_the_record_never_github"
)

#: agent-bureau CI run 38115044206, 2026-10-10, with its real escapes.
VITEST_LINE = (
    "Console web (vitest) (0)\tUNKNOWN STEP\t2026-10-11T06:13:18.6892147Z "
    f"{ESC}[41m{ESC}[1m FAIL {ESC}[22m{ESC}[49m "
    f"src/components/oneriver/RiverRow.fold.test.ts{ESC}[2m > {ESC}[22m"
    "under the phone line the pill and the action take a line of their own"
    f"{ESC}[2m > {ESC}[22m"
    "the action cell stands under the row, the whole row wide — Green Light’s "
    "own rule"
)
VITEST_ID = (
    "src/components/oneriver/RiverRow.fold.test.ts > under the phone line the "
    "pill and the action take a line of their own > the action cell stands "
    "under the row, the whole row wide — Green Light’s own rule"
)

PREFIX = "unit\tRun tests\t2026-10-10T07:21:00.0000000Z "


def logged(*lines: str) -> str:
    """Lines as `gh run view --log-failed` saves them: job, step, timestamp."""
    return "\n".join(PREFIX + line for line in lines)


class TheRealLinesTest(unittest.TestCase):
    """The two lines the card quotes, read back to exactly their ids."""

    def test_the_real_pytest_line(self):
        self.assertEqual([PYTEST_ID], failed_tests.failed_tests(PYTEST_LINE))

    def test_the_real_vitest_line_with_its_escape_characters(self):
        self.assertEqual([VITEST_ID], failed_tests.failed_tests(VITEST_LINE))

    def test_both_in_one_log_keep_log_order(self):
        self.assertEqual(
            [VITEST_ID, PYTEST_ID],
            failed_tests.failed_tests(VITEST_LINE + "\n" + PYTEST_LINE))


class OnlyFailingLinesTest(unittest.TestCase):
    """Passing lines, and every other line, read as no id."""

    def test_passing_lines_alone_give_no_id(self):
        log = logged(
            "tests/test_a.py::test_one PASSED                [ 10%]",
            "PASSED tests/test_a.py::test_two[FAILED tests/test_b.py::x - y]",
            "tests/test_a.py::test_three[FAILED] PASSED      [ 30%]",
            " ✓ src/a.test.ts > suite > passes",
            "===== 3 passed in 0.12s =====",
        )
        self.assertEqual([], failed_tests.failed_tests(log))

    def test_a_vitest_file_line_with_no_suite_is_not_an_id(self):
        # `FAIL  src/a.test.ts [ src/a.test.ts ]` is a file that failed to
        # load: no `>`, no test to name.
        self.assertEqual([], failed_tests.failed_tests(
            logged("FAIL  src/a.test.ts [ src/a.test.ts ]")))

    def test_an_error_line_is_not_a_failed_line(self):
        self.assertEqual([], failed_tests.failed_tests(
            logged("ERROR tests/test_a.py::test_one - ImportError")))

    def test_a_failed_line_that_is_not_a_test_id_is_skipped(self):
        self.assertEqual([], failed_tests.failed_tests(
            logged("FAILED to fetch the artifact - 502")))

    def test_nothing_reads_as_nothing(self):
        self.assertEqual([], failed_tests.failed_tests(""))
        self.assertEqual([], failed_tests.failed_tests(None))


class ShapeOfTheIdTest(unittest.TestCase):
    """No repeats, a hyphenated path survives, the bounds hold."""

    def test_a_repeat_is_kept_once_in_first_seen_order(self):
        log = logged(
            "FAILED tests/test_b.py::test_two - AssertionError",
            "FAILED tests/test_a.py::test_one - AssertionError",
            "FAILED tests/test_b.py::test_two - AssertionError",
        )
        self.assertEqual(
            ["tests/test_b.py::test_two", "tests/test_a.py::test_one"],
            failed_tests.failed_tests(log))

    def test_a_line_with_no_message_is_read(self):
        self.assertEqual(["tests/test_a.py::test_one"], failed_tests.failed_tests(
            logged("FAILED tests/test_a.py::test_one")))

    def test_a_hyphen_inside_the_path_is_part_of_the_id(self):
        self.assertEqual(
            ["tests/test_red-main.py::test_one[a-b]"],
            failed_tests.failed_tests(logged(
                "FAILED tests/test_red-main.py::test_one[a-b] - assert 1 == 2")))

    def test_a_spaced_hyphen_inside_the_brackets_is_part_of_the_id(self):
        self.assertEqual(
            ["tests/test_x.py::test_a[a - b]"],
            failed_tests.failed_tests(logged(
                "FAILED tests/test_x.py::test_a[a - b] - AssertionError: x")))

    def test_two_cases_differing_after_a_spaced_hyphen_stay_two_ids(self):
        self.assertEqual(
            ["tests/test_x.py::test_a[a - b]", "tests/test_x.py::test_a[a - c]"],
            failed_tests.failed_tests(logged(
                "FAILED tests/test_x.py::test_a[a - b] - AssertionError: x",
                "FAILED tests/test_x.py::test_a[a - c] - AssertionError: x")))

    def test_a_spaced_hyphen_in_the_brackets_with_no_message_is_read(self):
        self.assertEqual(
            ["tests/test_x.py::test_a[a - b]"],
            failed_tests.failed_tests(logged(
                "FAILED tests/test_x.py::test_a[a - b]")))

    def test_brackets_that_never_close_end_the_id_at_the_last_bracket(self):
        # A parameter value holding a lone `[` leaves the id unbalanced; the
        # id still ends where pytest closed it, before the message.
        self.assertEqual(
            ["tests/test_x.py::test_a[a[b - c]"],
            failed_tests.failed_tests(logged(
                "FAILED tests/test_x.py::test_a[a[b - c] - AssertionError: x")))

    def test_a_colored_pytest_line_is_read(self):
        self.assertEqual(["tests/test_a.py::test_one"], failed_tests.failed_tests(
            logged(f"{ESC}[31mFAILED{ESC}[0m tests/test_a.py::test_one - boom")))

    def test_an_id_is_cut_to_its_first_300_characters(self):
        name = "test_" + "x" * 400
        got = failed_tests.failed_tests(
            logged(f"FAILED tests/test_a.py::{name} - boom"))
        self.assertEqual(1, len(got))
        self.assertEqual(failed_tests.MAX_ID_CHARS, len(got[0]))
        self.assertEqual(300, failed_tests.MAX_ID_CHARS)
        self.assertEqual(f"tests/test_a.py::{name}"[:300], got[0])

    def test_at_most_20_ids_are_kept(self):
        log = logged(*(f"FAILED tests/test_a.py::test_{n} - boom"
                       for n in range(30)))
        got = failed_tests.failed_tests(log)
        self.assertEqual(20, failed_tests.MAX_IDS)
        self.assertEqual([f"tests/test_a.py::test_{n}" for n in range(20)], got)


class TheOnePrefixPatternTest(unittest.TestCase):
    """The log prefix is stripped by the pattern the filter already uses."""

    def test_it_is_reviewer_environments_own_pattern(self):
        import reviewer_environment

        self.assertIs(reviewer_environment._LOG_PREFIX, failed_tests._LOG_PREFIX)


if __name__ == "__main__":
    unittest.main()
