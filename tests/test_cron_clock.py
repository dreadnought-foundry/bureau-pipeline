"""The three clock helpers the channel-staleness alarm left behind (DRE-6053).

The channel-staleness alarm (DRE-2552) was retired on 2026-10-06: the console's
channel-health monitor raises the same alarm, around the clock since DRE-6050.
Three of its
helpers had other readers — Nightly Watch phrases its elapsed time with two of
them, and the split ledger's and Nightly Watch's wiring tests read a cron's
cadence with the third — so they moved, unchanged, into `scripts/cron_clock.py`
and the rest of the module went.

The tests for `hours_since` are the ones the retired module's test file
carried. `elapsed_days` (formerly its private `_days`) and `cron_interval_hours` were
only ever exercised through their callers; they are pinned here directly, so a
change to either is a change somebody meant.
"""

import json
import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_workflow_watchers  # noqa: E402
import cron_clock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: The retired job's names, each spelled in two halves so the card's retirement
#: grep over the tree for these three names stays clean and only ever finds a
#: real leftover.
RETIRED_WORKFLOW = ".github/workflows/channel" "-watch.yml"
RETIRED_MODULE = "channel" "_watch"
RETIRED_NAME = "Channel" " Watch"


class HoursSinceTest(unittest.TestCase):
    def test_hours_since_is_none_on_anything_unreadable(self):
        for bad in (None, "", "not-a-date"):
            self.assertIsNone(cron_clock.hours_since(bad, now="2026-08-20T00:00:00Z"))

    def test_hours_since_measures_real_elapsed_time(self):
        self.assertAlmostEqual(
            cron_clock.hours_since("2026-08-19T00:00:00Z", now="2026-08-20T12:00:00Z"),
            36.0,
            places=3,
        )

    def test_a_naive_timestamp_is_read_as_utc(self):
        self.assertAlmostEqual(
            cron_clock.hours_since("2026-08-19T00:00:00", now="2026-08-19T06:00:00Z"),
            6.0,
            places=3,
        )

    def test_an_unreadable_now_is_unknown_not_zero(self):
        self.assertIsNone(
            cron_clock.hours_since("2026-08-19T00:00:00Z", now="not-a-date"))


class ElapsedDaysTest(unittest.TestCase):
    """'29 days' / '30 hours' — the unit a human would have used."""

    def test_under_two_days_is_said_in_hours(self):
        self.assertEqual(cron_clock.elapsed_days(30), "30 hours")
        self.assertEqual(cron_clock.elapsed_days(47.4), "47 hours")

    def test_two_days_and_over_is_said_in_days(self):
        self.assertEqual(cron_clock.elapsed_days(48), "2 days")
        self.assertEqual(cron_clock.elapsed_days(29 * 24), "29 days")

    def test_one_is_singular(self):
        self.assertEqual(cron_clock.elapsed_days(1), "1 hour")
        self.assertEqual(cron_clock.elapsed_days(0.6), "1 hour")


class CronIntervalHoursTest(unittest.TestCase):
    def test_a_daily_cron_is_twenty_four_hours(self):
        self.assertEqual(cron_clock.cron_interval_hours("41 14 * * *"), 24.0)

    def test_an_hourly_cron_is_one_hour(self):
        self.assertEqual(cron_clock.cron_interval_hours("17 * * * *"), 1.0)

    def test_anything_else_is_none_not_a_guess(self):
        for cron in ("", None, "0 3 * *", "0 3 1 * *", "0 3 * 1 *",
                     "0 3 * * 1", "0 */6 * * *", "*/30 3 * * *"):
            self.assertIsNone(cron_clock.cron_interval_hours(cron), cron)

    def test_it_is_not_a_cron_engine(self):
        """Moved unchanged: a wildcard hour reads as hourly whatever the
        minute field says. Pinned so the limit is known rather than found."""
        self.assertEqual(cron_clock.cron_interval_hours("*/5 * * * *"), 1.0)


class TheChannelAlarmIsRetiredTest(unittest.TestCase):
    """The job, its decision module and its registry row are gone, and nothing
    still imports the module the helpers above came out of."""

    def test_the_workflow_and_its_module_are_gone(self):
        for path in (RETIRED_WORKFLOW,
                     f"scripts/{RETIRED_MODULE}.py",
                     f"tests/test_{RETIRED_MODULE}.py"):
            self.assertFalse((ROOT / path).exists(), path)

    def test_the_medic_no_longer_watches_it(self):
        medic = yaml.safe_load(
            (ROOT / ".github" / "workflows" / "self-medic.yml").read_text())
        watched = check_workflow_watchers.on_block(medic)["workflow_run"]["workflows"]
        self.assertNotIn(RETIRED_NAME, watched)

    def test_the_act_registry_has_no_row_for_it(self):
        acts = json.loads((ROOT / "config" / "pipeline-acts.json").read_text())
        files = [r.get("file") for r in acts.get("unconverted") or []]
        self.assertNotIn(RETIRED_WORKFLOW, files)

    def test_nothing_imports_the_retired_module(self):
        for path in sorted((ROOT / "scripts").glob("*.py")) + sorted(
                (ROOT / "tests").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                text, rf"(?m)^\s*(import {RETIRED_MODULE}\b|from {RETIRED_MODULE} import)",
                path.name)


if __name__ == "__main__":
    unittest.main()
