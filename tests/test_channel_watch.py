"""RED-first tests for the channel-staleness alarm (DRE-2552, Wave 1 Step 3).

Every backstop in the estate watches for something GOING WRONG. Nothing
watched for something CEASING TO HAPPEN — and that is the only reason July
went unnoticed for a month: the tag move did not fail, it simply stopped being
invoked, and no signal existed for "stopped".

So this is the missing sentence, and the tests below are mostly about the ways
a watcher can be worse than useless:

  1. **It must not cry wolf.** A quiet trunk is not a stale channel. A muted
     alarm on the thing protecting the fleet is how we get back to July, so
     ordinary quiet (nothing to promote) is silent BY CONSTRUCTION, and the
     thresholds come from this repo's measured commit cadence, not a round
     number.
  2. **A held channel is held, not broken.** DRE-2551's hold was approved as a
     switch, not a habit — so it is reported as a hold, with who and when, and
     it keeps being reported. An un-alarmed hold is indistinguishable from an
     abandoned channel.
  3. **Unknown is rendered as unknown** (standards/console-honesty.md rules
     2-3). A watcher that cannot read the channel must never report a moving
     one; the absent-data path is tested, not assumed.
  4. **The alarm cannot become the thing it watches.** The watcher reports its
     own missed ticks, its name is in the medic's watch list so a red run is
     diagnosed, and it holds NO write on the channel — structurally incapable
     of promoting anything, the model-drift guarantee.
  5. **One condition, one card.** The titles are stable while the condition
     is, so `find-open` matches yesterday's card instead of minting a daily
     duplicate — the alarm must not become the inbox we are escaping.
"""

import os
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import channel_watch  # noqa: E402
import promote_channel  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WATCH_WORKFLOW = ROOT / ".github" / "workflows" / "channel-watch.yml"
PROMOTE_WORKFLOW = ROOT / ".github" / "workflows" / "promote-channel.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"
README = ROOT / "README.md"

DAY = 24.0


def _watch(**kw):
    """A healthy channel by default; each test perturbs one fact."""
    args = dict(commits_ahead=3, channel_age_hours=2.0)
    args.update(kw)
    return channel_watch.evaluate(**args)


class QuietTrunkTest(unittest.TestCase):
    """1. Ordinary quiet must be silent — a noisy alarm gets muted."""

    def test_a_moving_channel_is_not_an_alarm(self):
        v = _watch()
        self.assertFalse(v.alarm)
        self.assertEqual(v.state, channel_watch.MOVING)

    def test_nothing_to_promote_is_never_stale(self):
        """The longest quiet stretch main has ever had is 10.7 days. With zero
        commits ahead there is nothing the channel COULD have promoted, so a
        quiet fortnight must not fire."""
        v = _watch(commits_ahead=0, channel_age_hours=20 * DAY)
        self.assertFalse(v.alarm)
        self.assertEqual(v.state, channel_watch.MOVING)

    def test_one_slow_harness_run_is_not_an_alarm(self):
        """Just under both thresholds: a handful of commits waiting on a run
        that has not finished is the system working, not stopping."""
        v = _watch(
            commits_ahead=channel_watch.STALE_AFTER_COMMITS - 1,
            channel_age_hours=channel_watch.STALE_AFTER_HOURS - 1,
        )
        self.assertFalse(v.alarm)


class StalenessTest(unittest.TestCase):
    """1. …and it must actually fire on the shape that hid July."""

    def test_july_fires_and_says_the_sentence(self):
        v = _watch(commits_ahead=174, channel_age_hours=29 * DAY)
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.STALE)
        self.assertIn("29 days", v.headline)
        self.assertIn("174 commits", v.headline)

    def test_the_threshold_boundary_fires(self):
        v = _watch(
            commits_ahead=channel_watch.STALE_AFTER_COMMITS,
            channel_age_hours=channel_watch.STALE_AFTER_HOURS,
        )
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.STALE)

    def test_a_single_commit_stranded_for_a_fortnight_still_fires(self):
        """The backstop. Below the commit threshold the trunk was quiet — but
        two weeks is longer than any quiet stretch this repo has ever had, so
        even one unpromoted commit is abnormal by then."""
        v = _watch(commits_ahead=1, channel_age_hours=15 * DAY)
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.STALE)

    def test_a_single_commit_below_the_backstop_does_not_fire(self):
        v = _watch(commits_ahead=1, channel_age_hours=13 * DAY)
        self.assertFalse(v.alarm)

    def test_the_alarm_carries_its_own_derivation(self):
        """The card's ask: a later reader must be able to tell a considered
        threshold from a guess, from the alarm itself."""
        v = _watch(commits_ahead=174, channel_age_hours=29 * DAY)
        self.assertIn(channel_watch.DERIVATION, v.detail)
        self.assertIn(str(channel_watch.STALE_AFTER_HOURS), channel_watch.DERIVATION)
        self.assertIn(str(channel_watch.STALE_AFTER_COMMITS), channel_watch.DERIVATION)

    def test_the_thresholds_are_the_measured_ones(self):
        """Pinned so a later edit to a rounder number has to argue with the
        measurement in the docstring rather than slide past review."""
        self.assertEqual(channel_watch.STALE_AFTER_HOURS, 72)
        self.assertEqual(channel_watch.STALE_AFTER_COMMITS, 8)
        self.assertEqual(channel_watch.QUIET_BACKSTOP_HOURS, 14 * DAY)


class HeldChannelTest(unittest.TestCase):
    """2. Held is a state, not a breakage — and it does not go quiet."""

    HOLD = "who=Ada since=2026-08-18T09:00:00Z rehearsing the DRE-2534 sandbox"

    def test_a_long_hold_is_reported_as_held_not_broken(self):
        v = _watch(hold=self.HOLD, hold_age_hours=30.0)
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.HELD)
        self.assertIn("held", v.headline.lower())
        self.assertNotIn("broken", v.headline.lower())

    def test_a_held_channel_names_who_and_when(self):
        v = _watch(hold=self.HOLD, hold_age_hours=30.0)
        self.assertIn("Ada", v.detail)
        self.assertIn("2026-08-18", v.detail)
        # The operator's own words survive — a hold whose reason is dropped is
        # indistinguishable from a breakage.
        self.assertIn("DRE-2534", v.detail)

    def test_a_hold_that_names_nobody_says_so(self):
        """Unknown as unknown (console-honesty rule 2) — never a plausible
        blank. And it still alarms: an anonymous hold is worse, not better."""
        v = _watch(hold="paused while we look at something", hold_age_hours=30.0)
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.HELD)
        self.assertIn("does not say who", v.detail.lower())

    def test_a_hold_that_says_nothing_about_when_says_so(self):
        v = _watch(hold="who=Ada testing", hold_age_hours=None)
        self.assertTrue(v.alarm)
        self.assertIn("does not say when", v.detail.lower())

    def test_a_switch_flipped_and_cleared_inside_a_day_is_not_an_alarm(self):
        """D2 approved a hold as a switch. A switch in use for an hour must not
        page anyone, or the alarm teaches the CEO to ignore it."""
        v = _watch(hold=self.HOLD, hold_age_hours=1.0)
        self.assertFalse(v.alarm)
        self.assertEqual(v.state, channel_watch.HELD)

    def test_a_held_channel_reads_as_held_even_when_badly_stale(self):
        """The two reasons a channel is quiet are different facts with
        different next actions. Held wins the headline — and the drift is still
        reported, because both are true."""
        v = _watch(hold=self.HOLD, hold_age_hours=30 * DAY,
                   commits_ahead=174, channel_age_hours=29 * DAY)
        self.assertEqual(v.state, channel_watch.HELD)
        self.assertIn("174", v.detail)

    def test_a_blank_hold_is_not_a_hold(self):
        """Autonomy by default — the same rule promote_channel.py applies."""
        for blank in (None, "", "   "):
            v = _watch(hold=blank, hold_age_hours=99.0)
            self.assertEqual(v.state, channel_watch.MOVING, f"{blank!r} held it")
            self.assertFalse(v.alarm)


class AbsentDataTest(unittest.TestCase):
    """3. console-honesty rules 2-3: stale/absent data has its own rendering,
    and it is tested rather than assumed."""

    def test_unreadable_drift_is_unknown_not_moving(self):
        v = _watch(commits_ahead=None)
        self.assertEqual(v.state, channel_watch.UNKNOWN)
        self.assertTrue(v.alarm)
        self.assertNotIn("moving", v.headline.lower())

    def test_a_missing_channel_ref_is_unknown_not_moving(self):
        v = _watch(channel_age_hours=None)
        self.assertEqual(v.state, channel_watch.UNKNOWN)
        self.assertTrue(v.alarm)

    def test_unknown_says_which_fact_it_could_not_read(self):
        v = _watch(commits_ahead=None, channel_age_hours=None)
        self.assertIn("could not read", v.detail.lower())

    def test_a_hold_still_explains_an_unreadable_channel(self):
        """Hold is checked first, exactly as in promote_channel.evaluate: a
        deliberately paused channel reads as paused, never as broken."""
        v = _watch(commits_ahead=None, channel_age_hours=None,
                   hold="who=Ada paused", hold_age_hours=30.0)
        self.assertEqual(v.state, channel_watch.HELD)


class WatcherLivenessTest(unittest.TestCase):
    """4. The watcher can also stop. It says so about itself."""

    def test_a_missed_tick_is_itself_an_alarm(self):
        v = _watch(watcher_gap_hours=channel_watch.MISSED_TICK_HOURS + 1)
        self.assertTrue(v.alarm)
        self.assertEqual(v.state, channel_watch.WATCHER_GAP)

    def test_an_on_time_watcher_is_silent(self):
        v = _watch(watcher_gap_hours=channel_watch.INTERVAL_HOURS)
        self.assertFalse(v.alarm)

    def test_a_never_run_watcher_is_not_a_gap(self):
        """First run of its life: nothing to compare against, and inventing a
        gap would be the fabricated-empty alarm reconcile.py refuses."""
        v = _watch(watcher_gap_hours=None)
        self.assertFalse(v.alarm)

    def test_a_stale_channel_outranks_the_watchers_own_gap(self):
        v = _watch(commits_ahead=174, channel_age_hours=29 * DAY,
                   watcher_gap_hours=200.0)
        self.assertEqual(v.state, channel_watch.STALE)
        # …and the missed ticks are still on the record.
        self.assertIn("missed", v.detail.lower())


class OneConditionOneCardTest(unittest.TestCase):
    """5. Idempotent titles: the card is a standing condition, not a diary."""

    def test_the_stale_title_does_not_move_with_the_numbers(self):
        a = _watch(commits_ahead=174, channel_age_hours=29 * DAY).title
        b = _watch(commits_ahead=201, channel_age_hours=31 * DAY).title
        self.assertEqual(a, b)

    def test_the_held_title_does_not_move_with_the_reason(self):
        a = _watch(hold="who=Ada one reason", hold_age_hours=30.0).title
        b = _watch(hold="who=Bo another reason", hold_age_hours=99.0).title
        self.assertEqual(a, b)

    def test_each_condition_has_its_own_title(self):
        titles = {
            _watch(commits_ahead=174, channel_age_hours=29 * DAY).title,
            _watch(hold="who=Ada x", hold_age_hours=30.0).title,
            _watch(commits_ahead=None).title,
            _watch(watcher_gap_hours=500.0).title,
        }
        self.assertEqual(len(titles), 4, f"titles collide: {titles}")

    def test_no_backticks_in_a_title(self):
        """The workflow passes the title through the shell; a backtick in one
        is command substitution waiting to happen."""
        for t in (channel_watch.STALE_TITLE, channel_watch.HELD_TITLE,
                  channel_watch.UNKNOWN_TITLE, channel_watch.WATCHER_TITLE):
            self.assertNotIn("`", t)
            self.assertNotIn("$", t)


class HoldParsingTest(unittest.TestCase):
    def test_who_is_read_from_the_hold_text(self):
        self.assertEqual(channel_watch.holder("who=Ada paused for X"), "Ada")
        self.assertEqual(channel_watch.holder("by=Ada Lovelace"), "Ada Lovelace")

    def test_no_who_is_none_not_a_guess(self):
        self.assertIsNone(channel_watch.holder("paused for the rehearsal"))

    def test_since_is_read_from_the_hold_text_when_the_api_will_not_say(self):
        """Reading a repository variable's own updated_at needs admin scope the
        workflow token does not have, so the text is the fallback source."""
        self.assertEqual(
            channel_watch.hold_started("who=Ada since=2026-08-18T09:00:00Z x"),
            "2026-08-18T09:00:00Z",
        )
        self.assertIsNone(channel_watch.hold_started("who=Ada x"))

    def test_hours_since_is_none_on_anything_unreadable(self):
        for bad in (None, "", "not-a-date"):
            self.assertIsNone(channel_watch.hours_since(bad, now="2026-08-20T00:00:00Z"))

    def test_hours_since_measures_real_elapsed_time(self):
        self.assertAlmostEqual(
            channel_watch.hours_since("2026-08-19T00:00:00Z", now="2026-08-20T12:00:00Z"),
            36.0,
            places=3,
        )


class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        self.text = WATCH_WORKFLOW.read_text()
        self.wf = yaml.safe_load(self.text)
        # PyYAML parses the bare key `on:` as the boolean True.
        self.on = self.wf.get("on", self.wf.get(True))

    def test_it_runs_on_a_schedule_and_on_demand(self):
        self.assertTrue(self.on.get("schedule"), "the watcher needs a cron")
        self.assertIn("workflow_dispatch", self.on,
                      "the deliberate-hold demonstration needs a manual run")

    def test_the_schedule_matches_the_interval_the_decision_assumes(self):
        """A cron and a MISSED_TICK_HOURS that disagree would report gaps that
        are not gaps — the producer/consumer drift rule, in one file."""
        crons = [e["cron"] for e in self.on["schedule"]]
        self.assertTrue(
            any(channel_watch.cron_interval_hours(c) == channel_watch.INTERVAL_HOURS
                for c in crons),
            f"{crons} does not run every {channel_watch.INTERVAL_HOURS}h",
        )

    def test_it_calls_the_decision_module(self):
        self.assertIn("scripts/channel_watch.py", self.text)

    def test_it_reads_the_same_hold_variable_the_promoter_reads(self):
        """Two names for one fact is how a hold gets alarmed on while the
        promoter is reading something else."""
        self.assertIn("vars.CHANNEL_HOLD", self.text)
        self.assertIn("vars.CHANNEL_HOLD", PROMOTE_WORKFLOW.read_text())

    def test_it_watches_the_ref_the_promoter_moves(self):
        self.assertEqual(channel_watch.CHANNEL, promote_channel.CHANNEL)
        self.assertIn(f"tags/{promote_channel.CHANNEL}", self.text)

    def test_it_opens_one_card_and_keeps_saying_it(self):
        self.assertIn("find-open", self.text)
        self.assertIn("linear_ops.py create", self.text)
        self.assertIn("linear_ops.py comment", self.text)

    def test_it_cannot_move_the_channel(self):
        """Structurally incapable, the model-drift guarantee: an alarm that
        can write the ref it watches is a promoter nobody reviewed."""
        self.assertEqual((self.wf.get("permissions") or {}).get("contents"), "read")
        self.assertNotIn("git/refs", self.text)
        self.assertNotIn("--method PATCH", self.text)

    def test_the_medic_watches_the_watcher(self):
        """The one part of "what if the watcher itself stops" that is
        mechanical: a red run is diagnosed like any other stage."""
        medic = yaml.safe_load(MEDIC_STUB.read_text())
        on = medic.get("on", medic.get(True))
        self.assertIn(self.wf["name"], on["workflow_run"]["workflows"])


class TheDerivationIsWrittenDownTest(unittest.TestCase):
    """The card asks for the reasoning, not just the number, on the record."""

    def test_the_readme_records_the_threshold_and_where_it_came_from(self):
        readme = README.read_text()
        self.assertIn("Channel staleness alarm", readme)
        for token in ("72", "8 commits", "10.7 days"):
            self.assertIn(token, readme, f"README does not record {token}")

    def test_the_readme_says_how_the_watcher_is_known_to_be_alive(self):
        self.assertIn("what if the watcher stops", README.read_text().lower())


if __name__ == "__main__":
    unittest.main()
