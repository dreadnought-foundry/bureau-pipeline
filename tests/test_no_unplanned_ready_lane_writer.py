"""No writer puts a card in a ready-work lane it has not been declared for (DRE-2859).

The deliverable is an ABSENCE: nothing in this repository places a card into a
lane the pipeline treats as ready work without it having passed through
Planning. An absence cannot be confirmed by reading code — the writer nobody
remembered is exactly the one still open — so every test here DISCOVERS the
writers instead of listing them, and three of them prove the discovery by
adding a new writer and watching the check name it.

## What the mechanism sees

`scripts/ready_lane_writers.py` reads the lane-write seam out of
`scripts/linear_ops.py` (every function that reaches the guarded state write or
mints a card with a `stateId`), then finds every call of that seam in
`scripts/**.py` and every `linear_ops.py` invocation in `.github/workflows/*.yml`
that hands the write layer a live lane name. The destination of each is
resolved — literal, module constant, parameter default, or the module's own
published `destinations()` — and a ready-work destination is checked against the
lane contract's permitted writers for that lane.

## WHAT THE MECHANISM CANNOT SEE — said plainly, because a test that implies
otherwise is worse than none

* **A hand write in the Linear UI.** A person dragging a card into Todo is not
  preventable by anything in this repository, and nothing here pretends to
  prevent it. The lane contract names `operator` as a permitted writer of a
  ready-work lane precisely because that write is a human's to make;
  `test_the_writers_this_cannot_see_are_named` asserts the mechanism reports
  that boundary rather than leaving it to be discovered.
* **The relay.** It lives in agent-bureau (`cloud/relay`) and the contract's own
  writer glossary carries it with `path: null` — there is no file here to read.
  It is covered the same way as the operator: named as unseen, so a day it is
  declared a writer of a ready-work lane is a day this says so.
* **Linear's team-level default.** `defaultIssueState` is a setting on the
  Linear team, mirrored in agent-bureau's `config/linear-workspace.json`. A unit
  test has no credentials and no such file, so the tests below drive
  `default_problems()` with the value directly — including the `Backlog` the
  setting actually carried when this card was written, and the unreadable case,
  which is reported rather than passed. `observed_default()` reads the real one
  when a checkout or an API key provides it.
* **A destination computed at run time from data.** Resolution is static. A
  writer whose destination no rule here can read is REPORTED as unread rather
  than assumed innocent — `test_a_new_writer_whose_destination_cannot_be_read_is_reported`
  is that guarantee.
"""

import copy
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402
import ready_lane_writers as rlw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class _Staged:
    """A file that exists in the real tree for the length of one test.

    The proof the card asks for is a NEW writer, added where the check actually
    looks — not in a copy of the tree that the check might read differently.
    """

    def __init__(self, relpath: str, text: str):
        self.path = ROOT / relpath
        self.text = text

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.text, encoding="utf-8")
        return self.path

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        return False


def _named(problems, needle: str) -> bool:
    return any(needle in p for p in problems)


class TheReadyWorkLanesAreDerived(unittest.TestCase):
    """A ready-work lane is one the planning segment can send a card to and the
    pipeline then treats as work. Read out of the vocabularies, never typed."""

    def test_the_ready_work_lanes_are_backlog_and_todo_today(self):
        self.assertEqual(rlw.ready_lanes(), ("Backlog", "Todo"))

    def test_a_lane_becomes_ready_work_because_the_vocabulary_says_so(self):
        # Re-point a shape's exit and the set moves with it. A hardcoded pair
        # would not, and the check would then police the wrong lanes.
        doc = {
            "version": 1,
            "shapes": [
                {"name": "one-off", "means": "x", "destination": "In Progress",
                 "actor": "reconcile.py", "promotable": True, "marks": [],
                 "why": "the vocabulary decides, not this test"},
            ],
        }
        self.assertIn("In Progress", rlw.ready_lanes(doc=doc))

    def test_a_lane_stops_being_ready_work_when_the_contract_moves_it(self):
        # The other half of the derivation: the segment comes from the lane
        # contract, so a lane the contract stops calling work stops being
        # policed as ready work.
        contract = copy.deepcopy(lane_contract.load())  # load() is cached, shared
        for entry in contract["lanes"]:
            if entry.get("name") == "Todo":
                entry["segment"] = "planning"
        self.assertNotIn("Todo", rlw.ready_lanes(contract=contract))


class TheDiscoveryActuallyFindsSomething(unittest.TestCase):
    """Guards the guard. Every assertion below passes trivially over an empty
    sweep, which is the failure mode a discovery check dies of."""

    def test_the_sweep_finds_writers_in_both_python_and_the_workflows(self):
        found = rlw.writes()
        self.assertTrue(any(w.how == "python" for w in found), found)
        self.assertTrue(any(w.how == "workflow" for w in found), found)

    def test_the_writers_it_finds_include_the_ones_we_know_are_there(self):
        pairs = {(w.writer, w.lane) for w in rlw.writes()}
        for expected in (
            ("reconcile.py", "Todo"),      # the sweep promoting a FLEET card
            ("reconcile.py", "Backlog"),   # the dead-run park
            ("linear_ops.py", "Backlog"),  # an epic's children
            ("agent-task.yml", "Todo"),    # the build run requeueing itself
        ):
            self.assertIn(expected, pairs)

    def test_every_unread_destination_belongs_to_a_writer_declared_everywhere(self):
        # Some destinations are computed from data and cannot be read from the
        # source. Each one that survives here belongs to a writer the contract
        # already permits in EVERY ready-work lane, so wherever it goes it was
        # allowed to go there. Any OTHER unread destination is reported —
        # `test_a_new_writer_whose_destination_cannot_be_read_is_reported` is
        # that half, and this one is why the suppression is not a blind spot.
        everywhere = set.intersection(
            *[set(lane_contract.lane_writers(n)) for n in rlw.ready_lanes()]
        )
        unread = [
            w for w in rlw.writes() if w.lane is None and w.writer not in everywhere
        ]
        self.assertEqual(
            unread, [],
            "a destination nothing here can read is a writer nothing here can "
            "check: " + "; ".join(f"{w.where} ({w.expression})" for w in unread),
        )


class TheSeamIsTheOnlyWayToWriteALane(unittest.TestCase):
    """Discovery is only complete if the seam is the only door. A module that
    builds its own `stateId` mutation is a writer the sweep never sees."""

    def test_nothing_outside_the_write_layer_sets_a_state_directly(self):
        self.assertEqual(rlw.seam_problems(), [])

    def test_the_seam_was_derived_and_is_not_empty(self):
        seam = rlw.seam_functions()
        self.assertIn("cmd_state", seam)
        self.assertIn("_create_card", seam)

    def test_a_module_that_writes_a_state_outside_the_seam_is_named(self):
        rogue = "zz_rogue_direct_mutation.py"
        source = (
            "MUT = '''mutation($id: String!, $input: IssueUpdateInput!) {\n"
            "  issueUpdate(id: $id, input: $input) { success } }'''\n"
            "def go(gql, issue_id, sid):\n"
            "    gql(MUT, {'id': issue_id, 'input': {'stateId': sid}})\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            problems = rlw.seam_problems()
        self.assertTrue(_named(problems, rogue), problems)


class TheRepositoryIsClean(unittest.TestCase):
    def test_no_writer_here_reaches_a_ready_work_lane_it_is_not_declared_for(self):
        self.assertEqual(rlw.writer_problems(), [])


class ANewWriterWithAWrongDestinationIsCaught(unittest.TestCase):
    """The card's central claim, proven three ways rather than argued for.

    Each of these adds a writer that did not exist when the check was written,
    in the place the check actually reads, and asserts the check goes red AND
    names it — a failure nobody has to open the test to act on.
    """

    def test_a_new_python_writer_pointed_at_a_ready_work_lane_is_named(self):
        rogue = "zz_rogue_promoter.py"
        source = (
            "import linear_ops\n\n"
            "def promote(card):\n"
            "    linear_ops.cmd_state(card, 'Todo')\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            problems = rlw.writer_problems()
        self.assertTrue(_named(problems, rogue), problems)
        self.assertTrue(_named(problems, "Todo"), problems)

    def test_a_new_workflow_step_pointed_at_a_ready_work_lane_is_named(self):
        rogue = "zz-rogue-promoter.yml"
        source = (
            "name: Rogue\n"
            "on: workflow_dispatch\n"
            "jobs:\n"
            "  go:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Skip the queue\n"
            "        run: |\n"
            '          python3 .bureau-pipeline/scripts/linear_ops.py state "$CARD" "Backlog"\n'
        )
        with _Staged(f".github/workflows/{rogue}", source):
            problems = rlw.writer_problems()
        self.assertTrue(_named(problems, rogue), problems)
        self.assertTrue(_named(problems, "Backlog"), problems)

    def test_a_new_writer_whose_destination_cannot_be_read_is_reported(self):
        # Unknown is never a pass. A writer that computes its lane from data no
        # rule here can follow is exactly the one worth hearing about.
        rogue = "zz_rogue_computed.py"
        source = (
            "import linear_ops\n\n"
            "def promote(card, board):\n"
            "    linear_ops.cmd_state(card, board['lane'])\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            problems = rlw.writer_problems()
        self.assertTrue(_named(problems, rogue), problems)

    def test_a_new_writer_pointed_at_a_lane_it_is_declared_for_is_not_named(self):
        # The check must not simply hate every new file: reconcile.py is a
        # declared Todo writer, so a second promotion inside it is fine. Without
        # this the three tests above would pass against a check that fails on
        # anything it has not seen before.
        rogue = "zz_rogue_planning_writer.py"
        source = (
            "import linear_ops\n\n"
            "def send_back(card):\n"
            "    linear_ops.cmd_state(card, 'Planning')\n"
        )
        with _Staged(f"scripts/{rogue}", source):
            problems = rlw.writer_problems()
        self.assertFalse(_named(problems, rogue), problems)


class LinearsOwnTeamDefaultIsCovered(unittest.TestCase):
    """The writer no code path touches, and the one a list of code writers
    would have missed entirely.

    Linear's team-level `defaultIssueState` is the lane a card lands in when
    the writer names none — an integration, a Slack create, an API call that
    omits `stateId`. It carried `Backlog` when this card was written.
    """

    def test_a_team_default_of_backlog_is_reported_and_named(self):
        problems = rlw.default_problems("Backlog")
        self.assertTrue(_named(problems, "Backlog"), problems)
        self.assertTrue(_named(problems, rlw.LINEAR_DEFAULT_WRITER), problems)

    def test_a_team_default_outside_the_work_segment_is_clean(self):
        self.assertEqual(rlw.default_problems("Intake"), [])

    def test_a_default_that_could_not_be_read_is_reported_not_passed(self):
        problems = rlw.default_problems(None)
        self.assertTrue(_named(problems, rlw.LINEAR_DEFAULT_WRITER), problems)

    def test_a_default_naming_a_lane_that_does_not_exist_is_reported(self):
        problems = rlw.default_problems("Somewhere Else")
        self.assertTrue(_named(problems, "Somewhere Else"), problems)

    def test_the_default_is_read_from_the_workspace_declaration_when_there_is_one(self):
        with _Staged(
            "config/linear-workspace.json",
            '{"team": "DRE", "defaultIssueState": "Backlog"}\n',
        ):
            self.assertEqual(rlw.observed_default(), "Backlog")

    def test_the_default_is_none_when_nothing_in_reach_declares_it(self):
        # No workspace file in this checkout and no API key that answers — and
        # that is reported by `default_problems`, never treated as agreement.
        self.assertIsNone(rlw.observed_default(gql=lambda *a, **k: None))


class TheWritersThisCannotSeeAreNamed(unittest.TestCase):
    def test_the_writers_this_cannot_see_are_named(self):
        unseen = rlw.unseen_writers()
        self.assertIn("operator", unseen)
        for key in unseen:
            self.assertIsNone(lane_contract.writers()[key]["path"], key)


if __name__ == "__main__":
    unittest.main()
