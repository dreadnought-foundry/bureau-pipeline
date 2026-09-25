"""RED-first tests for the missing-nightly alarm (DRE-4805).

PR CI now runs only the suites a change can reach, and a nightly `schedule:`
run on `main` runs everything (standards/engineering.md, "CI: narrow per
change, whole every night"). The nightly is therefore the ONLY thing that runs
the suites a pull request skipped — and if it quietly stops running, the
skipped tests are never run again and nobody finds out. A schedule GitHub
disabled after 60 days of repo inactivity, a cron typo, a workflow that errors
before any job starts, a run stuck in the queue: none of them fail anything,
because nothing failed. Something simply ceased to happen.

That is the same shape as the channel-staleness alarm (DRE-2552), and these
tests hold this watcher to the same rules:

  1. **The watched set is DERIVED, never listed.** A workflow is watched
     because ITS OWN FILE on the default branch carries both `pull_request`
     and `schedule` — that is what "PR CI that narrows, with a nightly behind
     it" looks like in data. No roster of repos, no workflow named `CI`, no
     special case for this repo's own `Pipeline Tests`. A repo that adds a
     nightly is watched from its first merge; one that removes it stops being
     watched. `check_workflow_watchers.py` is the same idea one repo down: a
     list that is remembered is a list that drifts.
  2. **It must not cry wolf.** A nightly that ran 25 hours ago is a nightly
     that ran. A run that completed RED is not this alarm's business at all —
     Red-Main Repair (`red-main-repair.yml`) fires on any failed run whose
     head branch is the default branch, schedule runs included. What nothing
     watches is the run that NEVER HAPPENED.
  3. **UNKNOWN is rendered as unknown** (standards/console-honesty.md rules
     2-3). A repo the token cannot read and a workflow file that will not
     fetch are reported as unknown and alarm; neither is ever reported as
     "nightly ok".
  4. **The alarm reaches a human the way the existing one does.** One
     deduplicated Linear card through `linear_ops.py`, asserted against
     `channel-watch.yml` rather than restated here, so the two cannot drift.
  5. **It is a report, not an act.** One `unconverted` / `not-an-act` row in
     `config/pipeline-acts.json`, copied from the row `channel-watch.yml`
     already carries, and nothing added to `acts`.
"""

import base64
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import channel_watch  # noqa: E402
import check_act_receipts  # noqa: E402
import check_workflow_watchers  # noqa: E402
import nightly_watch  # noqa: E402
from gh_read_retry import GhReadError  # noqa: E402
import pipeline_act  # noqa: E402
import release_train  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WATCH_WORKFLOW = ROOT / ".github" / "workflows" / "nightly-watch.yml"
CHANNEL_WORKFLOW = ROOT / ".github" / "workflows" / "channel-watch.yml"
MEDIC_STUB = ROOT / ".github" / "workflows" / "self-medic.yml"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

NOW = "2026-09-25T12:00:00Z"


def _ago(hours: float) -> str:
    """An ISO timestamp `hours` before NOW — the shape GitHub hands back."""
    then = dt.datetime.fromisoformat(NOW.replace("Z", "+00:00")) - dt.timedelta(hours=hours)
    return then.isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# fixture workflow files — what the watcher actually reads                     #
# --------------------------------------------------------------------------- #

#: PR CI that narrows, with the nightly behind it. The shape this alarm exists
#: for (agent-bureau's CI today, Portico's under DRE-4804).
NIGHTLY_CI = """
name: CI
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "37 9 * * *"
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo everything
"""

#: The same repo's CI before its nightly lands: no `schedule:`, so nothing to
#: watch and nothing to alarm about.
PR_ONLY_CI = """
name: CI
on:
  pull_request:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo narrow
"""

#: A scheduled sweep. It runs on a cron and is not CI — no `pull_request`
#: trigger — so it is NOT a nightly full run and is not watched.
SWEEP = """
name: Reconcile
on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:
jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - run: echo sweeping
"""

#: A nightly on a workflow that is not called `CI`. Named `Pipeline Tests` on
#: purpose — this repo's own CI will carry a nightly under DRE-4807, and the
#: watcher must pick it up by its TRIGGERS, with no line of code mentioning
#: either name. A fixture, deliberately not the live file.
PIPELINE_TESTS_NIGHTLY = """
name: Pipeline Tests
on:
  pull_request:
  schedule:
    - cron: "11 8 * * *"
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - run: echo every suite
"""

#: A file that is not YAML at all. Unknown, never "no nightly here".
BROKEN = "name: CI\non:\n  pull_request:\n   - [unclosed\n"


# --------------------------------------------------------------------------- #
# the fake GitHub — every read `collect` makes, and nothing else               #
# --------------------------------------------------------------------------- #


class FakeRepo:
    """One repo as the watcher sees it: a default branch, workflow files, runs."""

    def __init__(self, workflows=None, runs=None, default_branch="main",
                 unreadable=False, unreadable_files=(), listing_unreadable=False,
                 changed=None, commits_unreadable=False, listed_only=()):
        # path -> (display name, file text)
        self.workflows = dict(workflows or {})
        # workflow file name -> the newest schedule run, or None
        self.runs = dict(runs or {})
        self.default_branch = default_branch
        self.unreadable = unreadable
        self.unreadable_files = set(unreadable_files)
        self.listing_unreadable = listing_unreadable
        # path -> hours since the file last changed on the default branch. A
        # file not named here last changed a month ago: an established nightly.
        self.changed = dict(changed or {})
        self.commits_unreadable = commits_unreadable
        # path -> display name: listed by the Actions API (a run happened once,
        # on some branch) but NOT a file on the default branch — GitHub answers
        # the contents read with a 404.
        self.listed_only = dict(listed_only or {})


def run_record(*, status="completed", conclusion="success", hours_ago=2.0):
    return {"status": status, "conclusion": conclusion, "created_at": _ago(hours_ago)}


class FakeGitHub:
    """`api(path) -> parsed JSON`, or `Unreadable` — the seam `collect` takes.

    Deliberately faithful to the real answers: workflow file contents arrive
    base64-encoded from the contents API, and a workflow with no schedule run
    yet answers with an EMPTY `workflow_runs` list rather than an error.
    """

    def __init__(self, repos):
        self.repos = repos
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        repo, rest = self._split(path)
        if repo is None or repo.unreadable:
            raise nightly_watch.Unreadable(f"GitHub refused {path}")
        if rest == "":
            return {"default_branch": repo.default_branch}
        if rest.startswith("/actions/workflows?"):
            if repo.listing_unreadable:
                raise nightly_watch.Unreadable(f"GitHub refused {path}")
            listed = {p: name for p, (name, _) in repo.workflows.items()}
            listed.update(repo.listed_only)
            return {"workflows": [
                {"path": p, "name": name, "state": "active"}
                for p, name in sorted(listed.items())
            ]}
        contents = re.match(r"^/contents/(?P<file>[^?]+)", rest)
        if contents:
            wanted = contents.group("file")
            if wanted in repo.unreadable_files:
                raise nightly_watch.Unreadable(f"GitHub refused {path}")
            if wanted in repo.listed_only:
                raise nightly_watch.Unreadable(
                    "gh: Not Found (HTTP 404)", not_found=True)
            text = repo.workflows[wanted][1]
            return {
                "encoding": "base64",
                "content": base64.b64encode(text.encode()).decode(),
            }
        runs = re.match(r"^/actions/workflows/(?P<file>[^/]+)/runs\?", rest)
        if runs:
            record = repo.runs.get(runs.group("file"))
            return {"workflow_runs": [record] if record else []}
        commits = re.match(r"^/commits\?path=(?P<file>[^&]+)&sha=(?P<sha>[^&]+)"
                           r"&per_page=1$", rest)
        if commits:
            if repo.commits_unreadable:
                raise nightly_watch.Unreadable(f"GitHub refused {path}")
            assert commits.group("sha") == repo.default_branch, path
            hours = repo.changed.get(commits.group("file"), 24.0 * 30)
            return [{"sha": "abc123",
                     "commit": {"committer": {"date": _ago(hours)}}}]
        raise AssertionError(f"the watcher made a read nothing declares: {path}")

    def _split(self, path):
        m = re.match(r"^repos/(?P<repo>[^/]+/[^/?]+)(?P<rest>.*)$", path)
        if not m:
            raise AssertionError(f"not a repo read: {path}")
        return self.repos.get(m.group("repo")), m.group("rest")


def _collect(repos, roster=None, now=NOW):
    api = FakeGitHub(repos)
    roster = roster or {name.split("/")[-1]: name for name in repos}
    return nightly_watch.collect(api, roster=roster, now=now), api


def _states(readings):
    return {r.subject: r.state for r in readings}


# --------------------------------------------------------------------------- #
# 1. the watched set is derived from the workflow files                        #
# --------------------------------------------------------------------------- #


class WatchedSetTest(unittest.TestCase):
    """Who is watched, computed rather than listed."""

    def test_both_triggers_is_a_nightly(self):
        self.assertIs(nightly_watch.is_nightly(NIGHTLY_CI), True)

    def test_pull_request_without_a_schedule_is_not_watched(self):
        """CI that has not got its nightly yet. Nothing to alarm about — the
        repo is not narrowing behind a nightly that does not exist."""
        self.assertIs(nightly_watch.is_nightly(PR_ONLY_CI), False)

    def test_a_scheduled_sweep_is_not_watched(self):
        """`reconcile.yml` runs on a cron and is not CI. A sweep that stops is
        somebody else's alarm."""
        self.assertIs(nightly_watch.is_nightly(SWEEP), False)

    def test_a_workflow_not_named_ci_is_watched(self):
        """The criterion: `Pipeline Tests` carries both triggers, so it is
        watched — asserted against a FIXTURE, not this repo's live file, whose
        own nightly arrives with DRE-4807."""
        self.assertIs(nightly_watch.is_nightly(PIPELINE_TESTS_NIGHTLY), True)

    def test_a_file_that_is_not_yaml_is_unknown(self):
        """Not `False`. "I could not read it" and "it has no nightly" are
        different facts and only one of them is safe to be quiet about."""
        self.assertIsNone(nightly_watch.is_nightly(BROKEN))

    def test_the_yaml_on_key_trap_is_read_the_one_way(self):
        """YAML 1.1 parses the bare key `on` as the boolean True, which is why
        `check_workflow_watchers.on_block` exists. One reader, not two."""
        self.assertIs(nightly_watch.on_block, check_workflow_watchers.on_block)

    def test_this_repos_own_ci_is_read_by_derivation_not_by_name(self):
        """No special case for `Pipeline Tests`, in either direction. Today it
        has no `schedule:` and is not watched; when DRE-4807 gives it one it is
        watched, and this assertion holds on both sides of that card because it
        asserts the DERIVATION rather than today's answer."""
        text = TESTS_WORKFLOW.read_text()
        on = check_workflow_watchers.on_block(yaml.safe_load(text))
        self.assertEqual(
            nightly_watch.is_nightly(text),
            "pull_request" in on and "schedule" in on,
        )

    def test_the_watched_set_is_derived_not_hardcoded(self):
        """The mutation check. This fleet is built so that every plausible
        hardcoding picks the wrong workflow:

          * by file name — the only nightly here is `everything.yml`, and there
            IS a `ci.yml` in the same repo that is not one;
          * by display name — the nightly is called `Pipeline Tests` and the
            non-nightly is called `CI`;
          * by repo — the second repo has no nightly at all, and a watcher that
            listed repos rather than reading their files would report on it.
        """
        repos = {
            "dreadnought-foundry/portico": FakeRepo(
                workflows={
                    ".github/workflows/ci.yml": ("CI", PR_ONLY_CI),
                    ".github/workflows/everything.yml": (
                        "Pipeline Tests", PIPELINE_TESTS_NIGHTLY),
                    ".github/workflows/reconcile.yml": ("Reconcile", SWEEP),
                },
                runs={"everything.yml": run_record(hours_ago=2)},
            ),
            "dreadnought-foundry/agent-bureau-demo": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", PR_ONLY_CI)},
            ),
        }
        readings, api = _collect(repos)
        self.assertEqual(
            [r.subject for r in readings],
            ["dreadnought-foundry/portico · Pipeline Tests"],
        )
        # …and the decoys really are in the fixture, or the assertion above
        # would pass for a watcher that hardcoded any of the three.
        self.assertIn(
            ".github/workflows/ci.yml",
            repos["dreadnought-foundry/portico"].workflows,
        )
        self.assertEqual(
            repos["dreadnought-foundry/portico"].workflows[
                ".github/workflows/ci.yml"][0], "CI")
        self.assertTrue(
            any("/contents/.github/workflows/everything.yml" in p
                for p in api.paths),
            "the watcher must READ the workflow file to decide it is a nightly",
        )

    def test_a_repo_with_no_nightly_produces_no_reading(self):
        readings, _ = _collect({
            "dreadnought-foundry/agent-bureau-demo": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", PR_ONLY_CI)}),
        })
        self.assertEqual(readings, [])
        self.assertFalse(nightly_watch.evaluate(readings).alarm)

    def test_the_roster_is_the_fleet_map_the_relay_routes_on(self):
        """`config/repo-map.json`, read through the one reader the fleet wake
        already uses — a repo onboarded into the map is watched with no second
        edit, which is the whole reason no roster is kept here."""
        self.assertIs(nightly_watch.roster, release_train.fleet_roster)

    def test_only_the_named_owners_repos_are_read(self):
        """The token is scoped to ONE App installation, so the run is per owner
        (fleet-wake.yml's premortem Q1/Q2). A repo in another owner is not
        read at all here rather than read and reported unreadable."""
        fleet = {
            "portico": "dreadnought-foundry/portico",
            "atlas": "EveryBite/atlas",
        }
        repos = {
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=2)}),
            "EveryBite/atlas": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=99)}),
        }
        api = FakeGitHub(repos)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repo-map.json"
            path.write_text(json.dumps(fleet))
            narrowed = nightly_watch.roster(str(path), "dreadnought-foundry")
        readings = nightly_watch.collect(api, roster=narrowed, now=NOW)
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico · CI": nightly_watch.OK})
        self.assertFalse([p for p in api.paths if "EveryBite" in p])


# --------------------------------------------------------------------------- #
# 2. the reading: what a nightly's newest schedule run means                   #
# --------------------------------------------------------------------------- #


class ReadingTest(unittest.TestCase):
    """26 hours is a nightly plus two hours' slack; 3 hours of queue is stuck."""

    def _fleet(self, record):
        return {
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": record},
            ),
        }

    def test_a_nightly_27_hours_old_raises_the_alarm(self):
        """The headline criterion: the schedule stopped firing and nothing
        else in the estate would have said so."""
        readings, _ = _collect(self._fleet(run_record(hours_ago=27)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.LATE})
        verdict = nightly_watch.evaluate(readings)
        self.assertTrue(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.MISSING)
        self.assertIn("agent-bureau", verdict.headline)

    def test_a_nightly_25_hours_old_is_silent(self):
        """A nightly that ran is a nightly that ran. Ordinary quiet must never
        fire, or the alarm gets muted and we are back to nobody watching."""
        readings, _ = _collect(self._fleet(run_record(hours_ago=25)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.OK})
        self.assertFalse(nightly_watch.evaluate(readings).alarm)

    def test_the_threshold_is_a_nightly_plus_two_hours_of_slack(self):
        self.assertEqual(nightly_watch.STALE_AFTER_HOURS, 26.0)
        at = nightly_watch.read_run(
            "completed", "success", nightly_watch.STALE_AFTER_HOURS)
        past = nightly_watch.read_run(
            "completed", "success", nightly_watch.STALE_AFTER_HOURS + 0.5)
        self.assertEqual(at, nightly_watch.OK)
        self.assertEqual(past, nightly_watch.LATE)

    def test_a_nightly_that_completed_red_is_not_this_alarm(self):
        """Red-Main Repair owns a failed run on the default branch, schedule
        runs included — it keys on the branch, never on the event. Two alarms
        on one fact is how both get ignored."""
        readings, _ = _collect(self._fleet(
            run_record(status="completed", conclusion="failure", hours_ago=2)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.OK})
        self.assertFalse(nightly_watch.evaluate(readings).alarm)

    def test_a_red_nightly_that_is_also_late_still_alarms(self):
        """…but "it went red yesterday" is not a licence to stop running. The
        conclusion is Red-Main Repair's business; the DATE is this one's."""
        readings, _ = _collect(self._fleet(
            run_record(status="completed", conclusion="failure", hours_ago=30)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.LATE})

    def test_a_run_still_queued_at_three_hours_alarms(self):
        readings, _ = _collect(self._fleet(
            run_record(status="queued", conclusion=None, hours_ago=3)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.STUCK})
        self.assertTrue(nightly_watch.evaluate(readings).alarm)

    def test_a_run_still_in_progress_at_three_hours_alarms(self):
        readings, _ = _collect(self._fleet(
            run_record(status="in_progress", conclusion=None, hours_ago=3.5)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.STUCK})
        self.assertTrue(nightly_watch.evaluate(readings).alarm)

    def test_a_run_in_progress_for_an_hour_is_the_system_working(self):
        readings, _ = _collect(self._fleet(
            run_record(status="in_progress", conclusion=None, hours_ago=1)))
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.OK})
        self.assertFalse(nightly_watch.evaluate(readings).alarm)

    def test_a_watched_repo_with_no_schedule_run_at_all_alarms(self):
        """The 60-day disable, the cron typo, the workflow that errors before
        any job starts: all of them look like this."""
        readings, _ = _collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={}),
        })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.NEVER})
        verdict = nightly_watch.evaluate(readings)
        self.assertTrue(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.MISSING)

    def test_the_newest_schedule_run_on_the_default_branch_is_what_is_read(self):
        """Not the newest run of any kind: a push run that went green five
        minutes ago says nothing about whether the nightly fired."""
        repos = {
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=2)},
                default_branch="trunk"),
        }
        _, api = _collect(repos)
        reads = [p for p in api.paths if "/runs?" in p]
        self.assertEqual(len(reads), 1)
        self.assertIn("event=schedule", reads[0])
        self.assertIn("branch=trunk", reads[0])


# --------------------------------------------------------------------------- #
# 3. unknown is its own reading and never passes                               #
# --------------------------------------------------------------------------- #


class UnknownTest(unittest.TestCase):
    """standards/console-honesty.md rules 2-3, on a watcher that reads six
    repos it does not own."""

    def test_a_repo_the_token_cannot_read_is_unknown(self):
        readings, _ = _collect({
            "dreadnought-foundry/portico": FakeRepo(unreadable=True),
        })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico": nightly_watch.UNKNOWN})
        verdict = nightly_watch.evaluate(readings)
        self.assertTrue(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.UNKNOWN)
        self.assertNotIn(nightly_watch.OK, [r.state for r in readings])

    def test_a_workflow_listing_that_cannot_be_read_is_unknown(self):
        readings, _ = _collect({
            "dreadnought-foundry/portico": FakeRepo(listing_unreadable=True),
        })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico": nightly_watch.UNKNOWN})
        self.assertTrue(nightly_watch.evaluate(readings).alarm)

    def test_a_workflow_file_that_cannot_be_fetched_is_unknown(self):
        """Not skipped. A file we cannot read might be the nightly that
        stopped, and "we did not look" must never render as "nightly ok"."""
        readings, _ = _collect({
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                unreadable_files=[".github/workflows/ci.yml"]),
        })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico · CI":
                          nightly_watch.UNKNOWN})
        self.assertTrue(nightly_watch.evaluate(readings).alarm)

    def test_a_workflow_listing_longer_than_one_page_is_unknown(self):
        """GitHub pages the workflow list. A repo with more workflows than one
        page would have its nightly fall off the end — and the watcher would
        report on the ones it happened to see, which is watching less without
        saying so. It says so."""

        class Truncated(FakeGitHub):
            def __call__(self, path):
                answer = super().__call__(path)
                if "/actions/workflows?" in path:
                    answer["total_count"] = len(answer["workflows"]) + 7
                return answer

        api = Truncated({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=2)}),
        })
        readings = nightly_watch.collect(
            api, roster={"agent-bureau": "dreadnought-foundry/agent-bureau"},
            now=NOW)
        self.assertEqual(
            _states(readings),
            {"dreadnought-foundry/agent-bureau": nightly_watch.UNKNOWN})
        self.assertTrue(nightly_watch.evaluate(readings).alarm)

    def test_a_run_listing_that_cannot_be_read_is_unknown_not_never(self):
        """"GitHub did not answer" and "the nightly never ran" are different
        facts with different next actions."""

        class RefusesRuns(FakeGitHub):
            def __call__(self, path):
                if "/runs?" in path:
                    self.paths.append(path)
                    raise nightly_watch.Unreadable("GitHub refused the runs")
                return super().__call__(path)

        api = RefusesRuns({
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)}),
        })
        readings = nightly_watch.collect(
            api, roster={"portico": "dreadnought-foundry/portico"}, now=NOW)
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico · CI":
                          nightly_watch.UNKNOWN})

    def test_unknown_is_named_in_the_body_even_when_a_nightly_is_missing(self):
        """A missing nightly takes the title — it is the actionable one — but
        the unknown repo is still reported, or the alarm quietly shrinks the
        world to what it happened to be able to read."""
        readings, _ = _collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=40)}),
            "dreadnought-foundry/portico": FakeRepo(unreadable=True),
        })
        verdict = nightly_watch.evaluate(readings)
        self.assertEqual(verdict.state, nightly_watch.MISSING)
        self.assertIn("portico", verdict.detail)
        self.assertIn("agent-bureau", verdict.detail)

    def test_a_healthy_fleet_says_nothing(self):
        readings, _ = _collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=3)}),
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=11)}),
        })
        verdict = nightly_watch.evaluate(readings)
        self.assertFalse(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.OK)
        self.assertEqual(verdict.title, "")

    def test_a_run_listing_answer_with_no_run_list_is_unknown_not_never(self):
        """DRE-4851, the third defect on the card (DRE-4867): Portico's nightly
        ran, and the watcher said it never had. An answer that carries no
        `workflow_runs` list is an answer we could not read — not an empty
        history. Only a real, empty list is "no schedule run yet"."""

        class NoRunList(FakeGitHub):
            def __call__(self, path):
                if "/runs?" in path:
                    self.paths.append(path)
                    return {"message": "Not Found"}
                return super().__call__(path)

        api = NoRunList({
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)}),
        })
        readings = nightly_watch.collect(
            api, roster={"portico": "dreadnought-foundry/portico"}, now=NOW)
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico · CI":
                          nightly_watch.UNKNOWN})
        self.assertNotIn(nightly_watch.NEVER, [r.state for r in readings])

    def test_an_empty_answer_to_the_run_listing_is_unknown_not_never(self):
        """`gh` exiting 0 with nothing on stdout is not GitHub saying "no runs".
        Read through the real `_gh_api`, the path the watch job takes."""
        answers = _GhAnswers(runs=lambda: "")
        readings = answers.collect({
            "dreadnought-foundry/portico": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)}),
        })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/portico · CI":
                          nightly_watch.UNKNOWN})


# --------------------------------------------------------------------------- #
# 3b. a new schedule gets a night's grace (DRE-4851)                           #
# --------------------------------------------------------------------------- #


class NewScheduleGraceTest(unittest.TestCase):
    """DRE-4844: bureau-pipeline's `Pipeline Tests` gained its schedule at
    19:39 PT and the watcher called it "never ran" at 20:34 PT — 55 minutes
    later, eight hours before the cron could first fire. A workflow with no
    schedule run yet is judged from when its file last changed on the default
    branch, against the same 26 hours a running nightly gets."""

    PATH = ".github/workflows/tests.yml"
    SUBJECT = "dreadnought-foundry/bureau-pipeline · Pipeline Tests"

    def _fleet(self, **repo):
        return {
            "dreadnought-foundry/bureau-pipeline": FakeRepo(
                workflows={self.PATH: ("Pipeline Tests", PIPELINE_TESTS_NIGHTLY)},
                runs={}, **repo),
        }

    def test_a_schedule_added_55_minutes_ago_does_not_alarm(self):
        readings, _ = _collect(self._fleet(changed={self.PATH: 55 / 60}))
        self.assertEqual(_states(readings),
                         {self.SUBJECT: nightly_watch.WAITING})
        verdict = nightly_watch.evaluate(readings)
        self.assertFalse(verdict.alarm)
        self.assertEqual(verdict.title, "")

    def test_a_new_schedule_is_not_reported_as_having_run(self):
        """Waiting is not ok. A nightly that has never run has run nothing,
        and the report must not say it did."""
        readings, _ = _collect(self._fleet(changed={self.PATH: 25.5}))
        self.assertEqual(_states(readings),
                         {self.SUBJECT: nightly_watch.WAITING})
        self.assertNotEqual(readings[0].state, nightly_watch.OK)
        verdict = nightly_watch.evaluate(readings)
        self.assertNotIn("Ran:", verdict.detail)
        self.assertNotIn("has run", verdict.headline)
        self.assertIn("first", readings[0].detail)

    def test_a_file_unchanged_for_more_than_26_hours_still_reads_never(self):
        """The grace ends where a running nightly's slack ends. After that,
        no run is the alarm it always was, in the sentence it always had."""
        readings, _ = _collect(self._fleet(changed={self.PATH: 26.5}))
        self.assertEqual(_states(readings), {self.SUBJECT: nightly_watch.NEVER})
        verdict = nightly_watch.evaluate(readings)
        self.assertTrue(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.MISSING)
        self.assertIn("no scheduled run has EVER happened on the default branch",
                      verdict.detail)

    def test_a_refused_last_changed_read_is_unknown_never_ok(self):
        readings, _ = _collect(self._fleet(commits_unreadable=True))
        self.assertEqual(_states(readings),
                         {self.SUBJECT: nightly_watch.UNKNOWN})
        verdict = nightly_watch.evaluate(readings)
        self.assertTrue(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.UNKNOWN)

    def test_a_last_change_github_will_not_date_is_unknown(self):
        """An empty history or a commit with no date is a read we did not
        finish — the file is on the branch, so it has a last change."""
        for answer in ([], [{"sha": "abc123", "commit": {}}], {"message": "x"}):
            with self.subTest(answer=answer):

                class Undated(FakeGitHub):
                    def __call__(self, path):
                        if "/commits?" in path:
                            self.paths.append(path)
                            return answer
                        return super().__call__(path)

                api = Undated(self._fleet())
                readings = nightly_watch.collect(
                    api, roster={"bureau-pipeline":
                                 "dreadnought-foundry/bureau-pipeline"},
                    now=NOW)
                self.assertEqual(_states(readings),
                                 {self.SUBJECT: nightly_watch.UNKNOWN})

    def test_the_last_change_is_read_for_the_file_on_the_default_branch(self):
        readings, api = _collect(self._fleet(default_branch="trunk",
                                             changed={self.PATH: 1}))
        reads = [p for p in api.paths if "/commits?" in p]
        self.assertEqual(reads, [
            "repos/dreadnought-foundry/bureau-pipeline/commits"
            f"?path={self.PATH}&sha=trunk&per_page=1",
        ])
        self.assertEqual(_states(readings),
                         {self.SUBJECT: nightly_watch.WAITING})

    def test_a_nightly_that_has_run_is_not_asked_when_its_file_changed(self):
        """The grace is for a nightly with no run yet. One that has run is
        judged by its run, and costs no extra read."""
        _, api = _collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=2)}),
        })
        self.assertFalse([p for p in api.paths if "/commits?" in p])

    def test_a_new_nightly_beside_a_running_one_is_quiet(self):
        readings, _ = _collect({
            **self._fleet(changed={self.PATH: 2}),
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=3)}),
        })
        verdict = nightly_watch.evaluate(readings)
        self.assertFalse(verdict.alarm)
        self.assertEqual(verdict.state, nightly_watch.OK)
        self.assertIn(self.SUBJECT, verdict.detail)

    def test_the_derivation_states_the_grace(self):
        """The thresholds text printed in every alarm says a new schedule is
        given a night before it counts as never run."""
        derivation = nightly_watch.DERIVATION
        self.assertIn("last changed on the default branch", derivation)
        self.assertIn("grace", derivation)
        self.assertIn(f"{nightly_watch.STALE_AFTER_HOURS:.0f}h", derivation)


# --------------------------------------------------------------------------- #
# 3c. a workflow file that is not on the default branch (DRE-4851)             #
# --------------------------------------------------------------------------- #


class _GhAnswers:
    """Reads through the REAL `_gh_api`, with `gh` itself replaced.

    `FakeGitHub` answers every read the watcher makes; the reads named here
    are instead handed to `nightly_watch._gh_api` with `gh_read` patched to
    fail or answer as the gh CLI does. That is the one place a 404 is told
    apart from every other refusal, so the tests go through it.
    """

    def __init__(self, contents=None, runs=None):
        self.contents = contents   # callable -> gh stdout, or raises GhReadError
        self.runs = runs

    def collect(self, repos, roster=None):
        outer = self

        class Through(FakeGitHub):
            def __call__(self, path):
                gh = None
                if outer.contents and "/contents/" in path:
                    gh = outer.contents
                if outer.runs and "/runs?" in path:
                    gh = outer.runs
                if gh is None:
                    return super().__call__(path)
                self.paths.append(path)
                with mock.patch.object(nightly_watch, "gh_read",
                                       lambda args, log=None: gh()):
                    return nightly_watch._gh_api(path)

        api = Through(repos)
        roster = roster or {name.split("/")[-1]: name for name in repos}
        return nightly_watch.collect(api, roster=roster, now=NOW)


def _gh_fails(stderr, *, rate_limited=False, returncode=1):
    def fail():
        raise GhReadError(f"gh api failed rc={returncode}: {stderr}",
                          returncode=returncode, stderr=stderr,
                          rate_limited=rate_limited)
    return fail


#: What the gh CLI prints on stderr for each refusal of the file read.
NOT_FOUND = "gh: Not Found (HTTP 404)"
REFUSALS = {
    "403": "gh: Resource not accessible by integration (HTTP 403)",
    "5xx": "gh: Server Error (HTTP 502)",
    "throttle": "gh: API rate limit exceeded for installation ID 123249480. "
                "(HTTP 403)",
}


class NotOnDefaultBranchTest(unittest.TestCase):
    """agent-bureau's `probe-workflow-ref-caller.yml` ran once, on a probe
    branch since deleted, and was never on `main`. The Actions API still lists
    it as `active`. Its file is not on the default branch, so it is not a
    nightly on main — no reading, no card. Only GitHub's 404 means that."""

    PROBE = ".github/workflows/probe-workflow-ref-caller.yml"

    def _fleet(self):
        return {
            "dreadnought-foundry/agent-bureau": FakeRepo(
                workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                runs={"ci.yml": run_record(hours_ago=2)},
                listed_only={self.PROBE: "Probe caller — DRE-2606"}),
        }

    def test_a_listed_workflow_whose_file_404s_produces_no_reading(self):
        readings, api = _collect(self._fleet())
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.OK})
        self.assertFalse(nightly_watch.evaluate(readings).alarm)
        # …and the probe really was listed and really was asked for, or the
        # assertion above would pass for a watcher that never saw it.
        self.assertTrue(any(f"/contents/{self.PROBE}" in p for p in api.paths))
        self.assertFalse([p for p in api.paths
                          if "probe-workflow-ref-caller.yml/runs" in p])

    def test_a_repo_whose_only_listed_workflow_404s_files_no_card(self):
        readings, _ = _collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                listed_only={self.PROBE: "Probe caller — DRE-2606"}),
        })
        self.assertEqual(readings, [])
        verdict = nightly_watch.evaluate(readings, owner="dreadnought-foundry")
        self.assertFalse(verdict.alarm)
        self.assertEqual(verdict.title, "")

    def test_the_gh_cli_404_is_read_as_not_found(self):
        """Through the real `_gh_api`: `Not Found (HTTP 404)` on stderr is the
        only thing `gh` says, so that is what is read."""
        readings = _GhAnswers(contents=_gh_fails(NOT_FOUND)).collect({
            "dreadnought-foundry/agent-bureau": FakeRepo(
                listed_only={self.PROBE: "Probe caller — DRE-2606"}),
        })
        self.assertEqual(readings, [])

    def test_every_other_refusal_of_the_file_stays_unknown(self):
        """A 403, a 5xx, a throttle, or an answer that will not parse: none of
        them says the file is absent, so each stays UNKNOWN and alarms."""
        cases = {name: _gh_fails(stderr, rate_limited=(name == "throttle"))
                 for name, stderr in REFUSALS.items()}
        cases["not JSON"] = lambda: "<!DOCTYPE html><title>Unicorn</title>"
        cases["empty"] = lambda: ""
        for name, gh in cases.items():
            with self.subTest(refusal=name):
                readings = _GhAnswers(contents=gh).collect({
                    "dreadnought-foundry/agent-bureau": FakeRepo(
                        workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                        runs={"ci.yml": run_record(hours_ago=2)}),
                })
                self.assertEqual(_states(readings),
                                 {"dreadnought-foundry/agent-bureau · CI":
                                  nightly_watch.UNKNOWN})
                verdict = nightly_watch.evaluate(readings)
                self.assertTrue(verdict.alarm)
                self.assertEqual(verdict.state, nightly_watch.UNKNOWN)

    def test_a_throttle_that_mentions_404_is_still_a_throttle(self):
        """The flag comes from GitHub's status, never from a number that
        happens to appear in the message."""
        stderr = ("gh: API rate limit exceeded for installation ID 1404 on "
                  "repos/o/r/contents/404.yml (HTTP 403)")
        readings = _GhAnswers(
            contents=_gh_fails(stderr, rate_limited=True)).collect({
                "dreadnought-foundry/agent-bureau": FakeRepo(
                    workflows={".github/workflows/ci.yml": ("CI", NIGHTLY_CI)},
                    runs={"ci.yml": run_record(hours_ago=2)}),
            })
        self.assertEqual(_states(readings),
                         {"dreadnought-foundry/agent-bureau · CI":
                          nightly_watch.UNKNOWN})

    def test_only_a_404_sets_the_not_found_flag(self):
        with mock.patch.object(nightly_watch, "gh_read",
                               lambda args, log=None: _gh_fails(NOT_FOUND)()):
            with self.assertRaises(nightly_watch.Unreadable) as caught:
                nightly_watch._gh_api("repos/o/r/contents/x.yml?ref=main")
        self.assertTrue(caught.exception.not_found)
        for name, stderr in REFUSALS.items():
            with self.subTest(refusal=name), mock.patch.object(
                    nightly_watch, "gh_read",
                    lambda args, log=None, s=stderr, n=name: _gh_fails(
                        s, rate_limited=(n == "throttle"))()):
                with self.assertRaises(nightly_watch.Unreadable) as caught:
                    nightly_watch._gh_api("repos/o/r/contents/x.yml?ref=main")
                self.assertFalse(caught.exception.not_found)


# --------------------------------------------------------------------------- #
# 4. one condition, one card — and the same mechanism as the sibling alarm     #
# --------------------------------------------------------------------------- #


def _steps(workflow: Path) -> list:
    doc = yaml.safe_load(workflow.read_text())
    return [step for job in (doc.get("jobs") or {}).values()
            for step in (job.get("steps") or [])]


def _alarm_step(workflow: Path) -> dict:
    """The step that raises the alarm — the one that writes to Linear."""
    found = [s for s in _steps(workflow) if "linear_ops.py" in (s.get("run") or "")]
    assert len(found) == 1, f"{workflow.name}: expected one alarm step, got {len(found)}"
    return found[0]


def _linear_commands(step: dict) -> set:
    return set(re.findall(r"linear_ops\.py\s+([a-z-]+)", step.get("run") or ""))


class AlarmMechanismTest(unittest.TestCase):
    """The criterion: asserted AGAINST channel-watch.yml, never restated."""

    def test_the_alarm_uses_the_same_linear_seam_as_the_channel_watcher(self):
        self.assertEqual(
            _linear_commands(_alarm_step(WATCH_WORKFLOW)),
            _linear_commands(_alarm_step(CHANNEL_WORKFLOW)),
            "the nightly alarm must reach a human through the same "
            "linear_ops.py seam the channel alarm already uses — a second "
            "notification path is a second thing to keep working",
        )

    def test_the_alarm_is_gated_on_the_decision_the_same_way(self):
        self.assertEqual(
            _alarm_step(WATCH_WORKFLOW).get("if"),
            _alarm_step(CHANNEL_WORKFLOW).get("if"),
        )

    def test_the_card_carries_a_repo_label_the_same_way(self):
        """A card with no `repo:<slug>` label cannot be routed out of Planning
        (DRE-2680). Derived from the run's own repository, as the sibling does."""
        for workflow in (WATCH_WORKFLOW, CHANNEL_WORKFLOW):
            self.assertIn("--repo", _alarm_step(workflow)["run"])
            self.assertIn("GITHUB_REPOSITORY", _alarm_step(workflow)["run"])

    def test_one_condition_one_title_so_the_card_dedups(self):
        """`find-open` matches on equality, so a title that moved with the
        numbers would mint a fresh card every hour."""
        one = nightly_watch.evaluate(
            [nightly_watch.Reading("a · CI", nightly_watch.LATE, "late")],
            owner="dreadnought-foundry")
        many = nightly_watch.evaluate(
            [nightly_watch.Reading("a · CI", nightly_watch.LATE, "late"),
             nightly_watch.Reading("b · CI", nightly_watch.NEVER, "never")],
            owner="dreadnought-foundry")
        self.assertEqual(one.title, many.title)
        self.assertIn("dreadnought-foundry", one.title)

    def test_each_owner_gets_its_own_title(self):
        """Three owners, three concurrent matrix jobs. One shared title and two
        of them would race to create the same card."""
        self.assertNotEqual(
            nightly_watch.evaluate(
                [nightly_watch.Reading("a · CI", nightly_watch.NEVER, "n")],
                owner="EveryBite").title,
            nightly_watch.evaluate(
                [nightly_watch.Reading("a · CI", nightly_watch.NEVER, "n")],
                owner="DeltaSolv").title,
        )

    def test_a_standing_alarm_is_re_confirmed_daily_not_hourly(self):
        """The watcher runs hourly so a missing nightly is FOUND within the
        hour. Saying it again is a different cadence: 24 comments a day on one
        standing card is the inbox this programme exists to escape, and the
        sibling alarm — which runs daily — sets the rate that reads as
        deliberate rather than as a stuck process."""
        self.assertEqual(nightly_watch.RECONFIRM_AFTER_HOURS, 24.0)
        speaks = [hour for hour in range(24)
                  if nightly_watch.should_reconfirm(
                      now=f"2026-09-25T{hour:02d}:30:00Z")]
        self.assertEqual(len(speaks), 1)

    def test_the_first_card_is_filed_the_hour_it_is_found(self):
        """The re-confirm cadence must not delay the FINDING. The workflow
        gates only the comment on an existing card; the create branch is
        reached on whichever hour the alarm first fires."""
        run = _alarm_step(WATCH_WORKFLOW)["run"]
        create = run.index("linear_ops.py create")
        guard = run.index("RECONFIRM")
        comment = run.index("linear_ops.py comment")
        self.assertLess(guard, comment, "the guard is on the re-confirm")
        self.assertLess(comment, create, "…and never on the first filing")

    def test_the_headline_is_one_line(self):
        """It goes through GITHUB_OUTPUT, which is line-based."""
        verdict = nightly_watch.evaluate([
            nightly_watch.Reading("a · CI", nightly_watch.LATE, "late"),
            nightly_watch.Reading("b · CI", nightly_watch.NEVER, "never"),
        ])
        self.assertNotIn("\n", verdict.headline)

    def test_the_elapsed_time_is_phrased_by_the_one_formatter(self):
        """Same sentence shape as the channel alarm, from the same code."""
        self.assertIs(nightly_watch.hours_since, channel_watch.hours_since)


class WiringTest(unittest.TestCase):
    """The watcher runs on its own schedule and can do nothing but read."""

    def setUp(self):
        self.doc = yaml.safe_load(WATCH_WORKFLOW.read_text())
        self.on = check_workflow_watchers.on_block(self.doc)

    def test_it_runs_hourly_on_a_schedule_of_its_own(self):
        """It must not depend on the repos it watches running anything."""
        crons = [entry["cron"] for entry in self.on["schedule"]]
        self.assertEqual(len(crons), 1)
        minute, hour, dom, month, dow = crons[0].split()
        self.assertEqual((hour, dom, month, dow), ("*", "*", "*", "*"))
        self.assertNotEqual(minute, "0", "off the hour — GitHub delays "
                                         "scheduled jobs hardest at :00")

    def test_the_cron_and_the_declared_interval_agree(self):
        self.assertEqual(
            channel_watch.cron_interval_hours(self.on["schedule"][0]["cron"]),
            nightly_watch.INTERVAL_HOURS,
        )

    def test_it_holds_no_write_anywhere(self):
        """An alarm that can change what it watches is not an alarm."""
        self.assertEqual(self.doc["permissions"], {"contents": "read",
                                                   "actions": "read"})
        text = WATCH_WORKFLOW.read_text()
        for forbidden in ("git push", "--method POST", "--method PUT",
                          "--method DELETE", "gh workflow run"):
            self.assertNotIn(forbidden, text)

    def test_the_medic_watches_it(self):
        """A red watcher is a watchdog nobody is watching — and
        `check_workflow_watchers.py` fails the build without this."""
        medic = yaml.safe_load(MEDIC_STUB.read_text())
        watched = check_workflow_watchers.on_block(medic)["workflow_run"]["workflows"]
        self.assertIn(self.doc["name"], watched)

    def test_the_owner_matrix_comes_from_the_roster(self):
        """One App installation token per owner (fleet-wake.yml's premortem
        Q1/Q2). The owners are computed from `config/repo-map.json`, so a repo
        onboarded into the map is watched with no second edit."""
        text = WATCH_WORKFLOW.read_text()
        self.assertIn("nightly_watch.py owners", text)
        self.assertIn("matrix.owner", text)

    def test_it_runs_the_decision_module(self):
        self.assertIn("nightly_watch.py watch", WATCH_WORKFLOW.read_text())


# --------------------------------------------------------------------------- #
# 5. a report about the world, not an act the pipeline took                    #
# --------------------------------------------------------------------------- #


class ActRegistryTest(unittest.TestCase):
    """One `unconverted` row, copied from the one channel-watch.yml carries."""

    def setUp(self):
        self.doc = pipeline_act.load()
        self.rows = self.doc.get("unconverted") or []
        self.sibling = [r for r in self.rows
                        if r.get("file") == ".github/workflows/channel-watch.yml"]
        self.mine = [r for r in self.rows
                     if r.get("file") == ".github/workflows/nightly-watch.yml"]

    def test_exactly_one_row_for_the_new_alarm(self):
        self.assertEqual(len(self.mine), 1)

    def test_it_is_declared_the_way_the_sibling_alarm_is(self):
        self.assertEqual(len(self.sibling), 1)
        self.assertEqual(self.mine[0]["kind"], self.sibling[0]["kind"])
        self.assertEqual(self.mine[0]["kind"], "not-an-act")

    def test_it_names_its_own_anchor_and_step(self):
        step = self.mine[0]["step"]
        anchor = self.mine[0]["anchor"]
        run = _alarm_step(WATCH_WORKFLOW)["run"]
        self.assertEqual(step, _alarm_step(WATCH_WORKFLOW)["name"])
        self.assertIn(anchor, run)

    def test_the_reason_says_it_is_a_report_about_the_world(self):
        why = self.mine[0]["why"]
        self.assertIn("report", why.lower())
        self.assertTrue(len(why) > 80, "a reasonless row is just a mute button")

    def test_no_act_is_added_for_it(self):
        """No row in `acts`, so no console change is needed first and the
        console-first rule in docs/pipeline-acts.md does not apply."""
        self.assertEqual(
            [name for name in pipeline_act.acts(self.doc) if "nightly" in name],
            [],
        )

    def test_the_act_receipt_guard_is_satisfied(self):
        problems = [p for p in check_act_receipts.problems()
                    if "nightly" in p or "nightly-watch" in p]
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
