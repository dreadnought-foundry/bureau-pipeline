"""RED-first tests for DRE-3521 — the six RELEASE acts, and their cadences.

`config/pipeline-acts.json` declared twenty-two acts and not one of them was
about a release. So the console's release row — the row the sibling epic draws
on the Activity tab — had no declared cadence to read for any stage of a train
run, and every stage would have rendered `unknown` or, worse, been given a
number somebody typed into a component.

This card copies the console's declaration into the registry. It declares
nothing of its own: `release-ci-green`, `release-cut`, `release-build`,
`release-roll-out`, `release-verify` and `release-live`, all `kind: progress`,
one per stage of `CI green → cut → build → roll out → verify → live`.

THE NUMBERS ARE NOT THIS CARD'S TO CHOOSE. DRE-3518 declared them in
`console/backend/receipts.py` on agent-bureau `main` first (merged as
agent-bureau PR #2422), each beside a comment naming the bound it was read
off; this file copies them. That is the same console-first ordering DRE-3389
followed for the three lifecycle acts, and for the same reason: whoever builds
second would otherwise have to invent a number twice. So the tests below pin
two different things and both matter:

  1. the registry says exactly what the console said — the numbers as
     literals, and the source each one was read off in the row's
     `cadence_why` (offline, off this repo's own file);
  2. the registry and the console still AGREE — read out of agent-bureau at
     run time and compared, so a later edit to either side is a failure rather
     than a drift nobody sees. That one skips offline and is red in the `act
     registry consumers` job, exactly as its neighbours in
     `tests/test_progress_acts.py` and `tests/test_pipeline_acts_consumers.py`
     are.

WHY THESE ACTS EXIST AT ALL, and why nothing emits them. The train relays the
surface script's output AFTER the script has exited — `scripts/release_train.py`,
`run_surface`, runs it with `capture_output=True` and prints the captured lines
once the process is done — so the run's own log carries no per-stage
timestamps. The stages are recorded instead by the caller's surface script, as
GitHub deployment statuses (agent-bureau DRE-3519), and the console reads them
from there. Every row is therefore `adopted: false`: nothing in this tree posts
a receipt carrying the tag, and nothing will.

Run: cd bureau-pipeline && python3 -m pytest tests/test_release_acts.py -v
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
import lane_contract  # noqa: E402
import pipeline_act  # noqa: E402

CONFIG = os.path.join(ROOT, "config", "pipeline-acts.json")
DOC = os.path.join(ROOT, "docs", "pipeline-acts.md")

#: The workflow that runs a release, named once. It is the `next_actor` and the
#: `subscriber` on all six rows: the train is what is doing the work these acts
#: announce, and the train is what a reader goes to when one goes quiet.
TRAIN = "release-train.yml"

#: The epic's six stage words, in order (DRE-3336). The console exports the
#: same tuple as `RELEASE_STAGES`; this is the registry's side of the contract.
STAGES = ("CI green", "cut", "build", "roll out", "verify", "live")

#: The contract, verbatim: stage word → (act name, tag, cadence_s). Written out
#: as literals on purpose — a silent edit to the registry must fail a test here
#: rather than move a contract three other cards read. `release-live` carries
#: `None`: nothing is expected to follow a landed release, and the console
#: renders that as complete rather than overdue.
RELEASE = {
    "CI green": ("release-ci-green", "release-ci-green-observed", 600),
    "cut": ("release-cut", "release-cut-observed", 3600),
    "build": ("release-build", "release-build-observed", 1440),
    "roll out": ("release-roll-out", "release-roll-out-observed", 600),
    "verify": ("release-verify", "release-verify-observed", 3600),
    "live": ("release-live", "release-live-observed", None),
}

ACT_NAMES = tuple(act for act, _tag, _cadence in RELEASE.values())

#: What each row's `cadence_why` must show a reader who does not trust the
#: number: the bound the console read it off, in the console's own words. A
#: number with none of that is a guess, and this repo did not even choose it.
CADENCE_SOURCE = {
    "release-ci-green": ("timeout-minutes: 10", "release-train.yml"),
    "release-cut": ("timeout-minutes: 60", "release-train.yml"),
    "release-build": ("aws ecs wait tasks-stopped", "release-console.sh"),
    "release-roll-out": ("ROLLOUT_TIMEOUT", "release-console.sh"),
    "release-verify": ("timeout-minutes: 60", "release-train.yml"),
    "release-live": ("nothing is expected to follow",),
}

#: The finding that shaped the epic, which every row owes in its `why`: the
#: train cannot time its own stages, so the stages are read off GitHub.
RELAY_FINDING = ("run_surface", "scripts/release_train.py", "DRE-3519", "deployment status")


def _doc() -> dict:
    return copy.deepcopy(pipeline_act.load())


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# 1. the six rows                                                              #
# --------------------------------------------------------------------------- #
class TestTheSixReleaseActs(unittest.TestCase):
    def test_the_registry_declares_all_six(self):
        for act in ACT_NAMES:
            self.assertIn(act, pipeline_act.acts())

    def test_the_stage_words_are_the_epics_six_in_order(self):
        """The registry's half of the contract the console exports as
        `RELEASE_STAGES`. Pinned here so a stage cannot be quietly renamed on
        one side of the two repositories."""
        self.assertEqual(tuple(RELEASE), STAGES)

    def test_each_one_carries_the_tag_the_console_declared(self):
        for act, tag, _cadence in RELEASE.values():
            self.assertEqual(pipeline_act.tag(act), tag, act)

    def test_each_one_carries_the_cadence_the_console_declared(self):
        for act, _tag, cadence in RELEASE.values():
            self.assertEqual(pipeline_act.cadence_s(act), cadence, act)

    def test_each_one_is_a_progress_act_that_moves_nothing(self):
        for act in ACT_NAMES:
            entry = pipeline_act.record(act)
            self.assertEqual(entry["kind"], "progress", act)
            self.assertEqual(
                entry["state"], "unchanged",
                f"{act} announces that a release is moving; it does not move it",
            )
            self.assertIsNone(
                entry["discharges"],
                f"{act} answers no prior obligation — nothing was owed",
            )

    def test_the_train_is_the_next_actor_and_the_subscriber(self):
        """Both, and the same workflow for both: the train is what is doing the
        work the act announces, and the train is where a reader goes when a
        stage goes quiet. There is no third party in a release."""
        for act in ACT_NAMES:
            entry = pipeline_act.record(act)
            self.assertEqual(entry["next_actor"], TRAIN, act)
            self.assertEqual(entry["subscriber"], TRAIN, act)

    def test_the_lane_contract_knows_the_train_as_an_actor(self):
        """`pipeline_act.problems()` binds `next_actor` to the lane contract's
        writer glossary — an act may not hand work to somebody the contract has
        never heard of. Six acts naming the train means the contract has to
        carry it, with a path that still resolves in this checkout."""
        self.assertIn(TRAIN, lane_contract.writers())
        entry = lane_contract.load()["writers"][TRAIN]
        self.assertTrue((entry.get("what") or "").strip())
        self.assertTrue(os.path.exists(os.path.join(ROOT, entry["path"])))

    def test_none_of_them_is_a_live_idempotency_key(self):
        """Nothing in this tree posts a receipt carrying these tags: the train
        relays the surface script's output after it exits, so it has no stage
        to announce, and the stages are recorded as GitHub deployment statuses
        by the caller's own script (DRE-3519). `problems()` proves the absence,
        which is what keeps `adopted: false` honest."""
        for act in ACT_NAMES:
            self.assertIs(pipeline_act.record(act)["adopted"], False, act)

    def test_each_row_carries_every_field_every_other_row_carries(self):
        """The same shape as the other twenty-two, or the console's mirror
        reads a row with a hole in it off the same parse that gets it the
        tag."""
        reference = set(pipeline_act.record("build-heartbeat"))
        for act in ACT_NAMES:
            self.assertEqual(set(pipeline_act.record(act)), reference, act)

    def test_each_cadence_why_names_the_source_the_console_cites(self):
        """The number is not this repo's, and the row says whose it is and what
        it was read off. Console-first is the rule (docs/pipeline-acts.md), so
        a row that could not point at the console's own source would be a
        number this file had quietly chosen."""
        for act, fragments in CADENCE_SOURCE.items():
            why = pipeline_act.record(act)["cadence_why"]
            self.assertIn("console/backend/receipts.py", why, act)
            self.assertIn("check_act_consumers.py", why, act)
            for fragment in fragments:
                self.assertIn(fragment, why, f"{act}: {fragment!r} is missing")

    def test_each_why_records_the_relay_finding(self):
        """Why the stages are read off GitHub rather than off the run's log, in
        the row itself — the finding that shaped the whole epic, and the reason
        nothing here emits these tags."""
        for act in ACT_NAMES:
            why = pipeline_act.record(act)["why"]
            for fragment in RELAY_FINDING:
                self.assertIn(fragment, why, f"{act}: {fragment!r} is missing")

    def test_each_one_is_anchored_in_this_repos_own_tree(self):
        """`emits` names a file and an anchor HERE — the train workflow for the
        two stages the train itself decides, and the surface contract in
        `standards/release-train.md` for the four a surface script owes, which
        is where this repo declares what happens at each of them.

        Deliberately NOT `scripts/release_train.py`: the binding check reads
        every `*_TAG` constant out of a `.py` file a row points at and demands
        a row for each, so anchoring there would drag the train's own tag
        constants into the registry."""
        for act in ACT_NAMES:
            emits = pipeline_act.record(act)["emits"]
            self.assertNotEqual(emits["file"], "scripts/release_train.py", act)
            text = _read(os.path.join(ROOT, emits["file"]))
            self.assertEqual(
                text.count(emits["anchor"]), 1,
                f"{act}: {emits['anchor']!r} must appear exactly once in "
                f"{emits['file']}",
            )

    def test_no_release_tag_collides_with_any_other(self):
        """The collision rule (`pipeline_act._collision_problems`): `tag in
        body` is how every other receipt here is counted, and six new tags
        sharing a `release-` prefix is exactly where one ends up inside
        another."""
        tags = [pipeline_act.tag(n) for n in pipeline_act.acts()]
        names = list(pipeline_act.acts())
        for act, tag, _cadence in RELEASE.values():
            for other in tags:
                if other != tag:
                    self.assertNotIn(other, tag, f"{other!r} inside {tag!r}")
                    self.assertNotIn(tag, other, f"{tag!r} inside {other!r}")
            for name in names:
                self.assertNotIn(tag, name, f"{tag!r} inside the act name {name!r}")
            self.assertEqual(act, act.strip())

    def test_the_registry_still_validates(self):
        self.assertEqual(pipeline_act.problems(), [])


# --------------------------------------------------------------------------- #
# 2. the console still agrees                                                  #
# --------------------------------------------------------------------------- #
#: A console declaring the six on the act's own row, in milliseconds — the
#: shape the console serves `cadenceMs` from. `release-live` carries no number
#: because nothing is expected to follow it.
CONSOLE_AGREES = textwrap.dedent(
    '''
    """The console's receipt vocabulary."""

    ACTS = {
        "release-ci-green": {"kind": "progress", "cadence_ms": 600_000},
        "release-cut": {"kind": "progress", "cadence_ms": 3_600_000},
        "release-build": {"kind": "progress", "cadence_ms": 1_440_000},
        "release-roll-out": {"kind": "progress", "cadence_ms": 600_000},
        "release-verify": {"kind": "progress", "cadence_ms": 3_600_000},
        "release-live": {"kind": "progress", "cadence_ms": None},
    }

    # DRE-3389's three, carried here for the same reason DRE-3521's six are
    # carried in `tests/test_progress_acts.py`: an unplaced `progress` cadence
    # is FATAL to `cadences()`, so a fixture console short of one kind's rows
    # would fail these tests on rows that are not theirs.
    LIFECYCLE_CADENCE_MS = {
        "build-heartbeat": 6_300_000,
        "review-run": 3_000_000,
        "merge-gate-watch": 3_900_000,
    }
    '''
)

#: The same console with ONE number moved — the difference the guard must fail
#: on, naming both the act and the number this file declares.
CONSOLE_DISAGREES = CONSOLE_AGREES.replace("1_440_000", "1_400_000")


class TestTheConsoleStillAgrees(unittest.TestCase):
    def test_a_console_that_agrees_is_ok(self):
        report = guard.cadences(doc=_doc(), source=CONSOLE_AGREES)
        self.assertFalse(report.skipped, report.text())
        self.assertTrue(report.ok, report.text())

    def test_one_number_moved_is_a_failure_that_names_both(self):
        report = guard.cadences(doc=_doc(), source=CONSOLE_DISAGREES)
        self.assertFalse(report.skipped, report.text())
        self.assertFalse(report.ok, report.text())
        text = report.text()
        self.assertIn("release-build", text)
        self.assertIn("1440", text)

    def test_the_null_row_is_not_compared_at_all(self):
        """`release-live` declares `cadence_s: null`, and a null is not a
        number to disagree with: `cadences()` only compares rows carrying an
        integer. A console that says nothing about it is agreement, not a
        gap."""
        report = guard.cadences(
            doc=_doc(), source=CONSOLE_AGREES.replace(
                '"release-live": {"kind": "progress", "cadence_ms": None},', ""
            ),
        )
        self.assertTrue(report.ok, report.text())

    def test_a_console_that_never_heard_of_them_fails_the_existing_guard(self):
        report = guard.check(doc=_doc(), source='ACTS = {"reviewer-down": "hold"}')
        self.assertFalse(report.ok)
        for act in ACT_NAMES:
            self.assertIn(act, report.unknown)

    def test_the_critics_receipt_carries_the_release_rows_too(self):
        """The receipt qa-review hands the critic on a registry PR. A reviewer
        told only that the console KNOWS each act would read a clean receipt as
        a clean registry, and the number on the row is the other half."""
        text = guard.context(
            ["config/pipeline-acts.json"], doc=_doc(), source=CONSOLE_DISAGREES
        )
        self.assertIn("release-build", text)
        self.assertIn("1440", text)


# --------------------------------------------------------------------------- #
# 3. the file on disk, and the page that explains it                           #
# --------------------------------------------------------------------------- #
class TestTheShippedFile(unittest.TestCase):
    def test_the_check_command_still_exits_zero(self):
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "pipeline_act.py"), "check"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_list_carries_the_six_new_rows(self):
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "pipeline_act.py"), "list"],
            capture_output=True, text=True, cwd=ROOT, check=False,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rows = {r["act"]: r for r in json.loads(out.stdout)}
        for act, tag, cadence in RELEASE.values():
            self.assertEqual(rows[act]["tag"], tag)
            self.assertEqual(rows[act]["kind"], "progress")
            self.assertEqual(rows[act]["cadence_s"], cadence)

    def test_the_six_are_declared_in_stage_order(self):
        """A reader of the raw file sees the release journey in the order it
        happens. Cheap to keep, and the only order that is not arbitrary."""
        declared = [n for n in pipeline_act.acts() if n in set(ACT_NAMES)]
        self.assertEqual(tuple(declared), ACT_NAMES)

    def test_the_page_names_the_six_and_states_the_relay_finding(self):
        """A change that contradicts a document updates that document in the
        same pull request. `docs/pipeline-acts.md` is where console-first is
        written down, so it is where these six and the reason nothing emits
        them belong."""
        page = " ".join(_read(DOC).split())
        for act in ACT_NAMES:
            self.assertIn(act, page, f"{act} is not named on {DOC}")
        self.assertIn("run_surface", page)
        self.assertIn("deployment status", page)
        self.assertIn("DRE-3519", page)


if __name__ == "__main__":
    unittest.main()
