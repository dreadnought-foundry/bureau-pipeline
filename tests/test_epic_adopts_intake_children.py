"""RED-first: planning an epic adopts the children already filed under it (DRE-4668).

Since the intake cutover the normal shape of an epic is hand-filed: cards go into
`Intake`, somebody groups them under a new epic, and the epic is sent to
`Planning`. The planner in re-plan mode is told to create only the MISSING
sub-issues and never moves an existing child's lane; `plan_child_verdicts.py`
stamped verdicts and moved nothing; and nothing else takes a card out of Intake
by design (`reconcile.INTAKE_LANE` — no card leaves Intake for being old).

So the CEO approved the epic and nothing built. `reconcile.backlog_children`
asks Linear for `state: {name: {eq: "Backlog"}}`, and every one of those
hand-filed children was still sitting in Intake. Read on 2026-09-23 ~07:55 PT
before approving DRE-4666 (12 hand-filed children in Intake); DRE-4626 (7) and
DRE-4633 (7) have the same shape.

THE APPROVAL MODEL THIS RESTS ON, and it is the CEO's, signed through the
console on DRE-4668 at 2026-09-23 09:57 PT: an epic's green light is the
approval for the cards filed under it. Adoption puts a child in **Backlog**,
which is not a build lane — `reconcile.promote_ready` still refuses it until the
parent epic is In Progress and every blocker is Done. An epic nobody approves
leaves its children sitting in Backlog, unbuilt. A card in Intake with no parent
epic is untouched and still leaves only through a groomer batch the CEO
approves.

WHAT THIS PINS, one section per acceptance criterion:

  1. Two build children and a PROOF child, all in Intake, all end in Backlog —
     the build children carrying FLEET, the proof card carrying the verdict
     `proof_and_demo.py` already wrote on it. This is the section that fails
     against today's code.
  2. A NEEDS WORK child is not moved to Backlog, and is named on the epic.
  3. A child already in Backlog, Todo or In Progress is neither moved nor
     re-stamped.
  4. An Intake card with no parent epic is not moved at all; and a child of an
     epic that has been PLANNED but not APPROVED sits in Backlog and is not
     promoted by the sweep.

Run: cd bureau-pipeline && python3 -m pytest tests/test_epic_adopts_intake_children.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("REPO_SLUG", "bureau-pipeline")

import lane_contract  # noqa: E402
import linear_ops  # noqa: E402
import plan_child_verdicts  # noqa: E402
import proof_and_demo  # noqa: E402
import reconcile  # noqa: E402
import routing_verdict  # noqa: E402

WF = ROOT / ".github" / "workflows" / "plan.yml"
MODULE = SCRIPTS / "plan_child_verdicts.py"

EPIC = "DRE-4668"

LABELS = ("repo:bureau-pipeline", "agent:engineer", "initiative:pipeline")
OPS_LABELS = ("repo:bureau-pipeline", "agent:ops", "initiative:pipeline")

# A build card the way a hand-filed one reads: checkbox criteria naming neither
# an interactive flow nor a rendered outcome — the judgement branch, answered
# with the promotable verdict.
WORK_BODY = (
    "Adopt the children already filed under the epic.\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] each Intake child gets its routing verdict\n"
    "- [ ] each one lands in Backlog and waits for the epic's green light\n"
)

PROOF_BODY = (
    "Record what was observed, where it was read, and when.\n\n"
    f"{proof_and_demo.CLOSING_LINE}\n\n"
    "## Acceptance criteria\n\n"
    "- [ ] a real epic's hand-filed children are observed reaching Backlog\n"
    "- [ ] the observation is written to docs/proof/DRE-4668.md and merged\n"
    "- [ ] the CEO closes this card after reading the record\n"
)

# No checkbox criteria at all — the vocabulary's NEEDS WORK, which routes the
# card back to Planning rather than to anybody who builds.
NO_CRITERIA_BODY = "Make the epic feel better.\n\nWe will know it when we see it.\n"


def _card(identifier, title, body=WORK_BODY, labels=LABELS, blocked_by=()):
    return {
        "identifier": identifier,
        "title": title,
        "body": body,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
    }


def _plan(work_bodies=(WORK_BODY, WORK_BODY)):
    """The epic's children: the build cards, then the card that closes it."""
    cards = [
        _card(f"DRE-90{i}", f"the {i}th piece", body=body)
        for i, body in enumerate(work_bodies, 1)
    ]
    siblings = [c["identifier"] for c in cards]
    cards.append(_card(
        "DRE-9099",
        "PROOF: the hand-filed children reached Backlog on their own",
        body=PROOF_BODY, labels=OPS_LABELS, blocked_by=siblings,
    ))
    return cards


class _Board:
    """A fake Linear: which lane every card is in, and what is written on it.

    `advance` behaves as `linear_ops.cmd_advance` does — it moves a card only
    out of one of the from-states it is given, and leaves it alone otherwise.
    That is the whole of "a child in any other lane is untouched", so the fake
    has to honour it rather than move whatever it is handed.
    """

    def __init__(self, **lanes):
        self.lanes = dict(lanes)
        self.comments: dict[str, list[str]] = {}
        self.labels: dict[str, list[str]] = {}
        self.advances: list[tuple[str, str, str]] = []

    def advance(self, identifier, to_state, from_states_csv, *flags):
        self.advances.append((identifier, to_state, from_states_csv))
        allowed = [s.strip().lower() for s in from_states_csv.split(",")]
        if (self.lanes.get(identifier) or "").lower() not in allowed:
            return
        self.lanes[identifier] = to_state

    def comment(self, identifier, body, *flags):
        self.comments.setdefault(identifier, []).append(body)

    def label(self, identifier, name):
        self.labels.setdefault(identifier, []).append(name)

    def verdict_on(self, identifier):
        return routing_verdict.verdict_on(self.comments.get(identifier, []))


@contextlib.contextmanager
def _linear(board):
    with patch.object(linear_ops, "comment_bodies",
                      side_effect=lambda i: list(board.comments.get(i, []))), \
            patch.object(linear_ops, "cmd_comment", side_effect=board.comment), \
            patch.object(linear_ops, "add_label", side_effect=board.label), \
            patch.object(linear_ops, "cmd_advance", side_effect=board.advance):
        yield board


def _planning_exit(board, children, comment_file=None):
    """Run the planning exit over `children`, in the order plan.yml runs it:
    the proof gate stamps the closing card, then the batch stamper."""
    argv = ["stamp", "--epic", EPIC]
    if comment_file:
        argv += ["--comment-file", comment_file]
    with _linear(board), patch.object(sys, "stdin",
                                      io.StringIO(json.dumps(children))):
        proof_and_demo.write_stamps(children)
        return plan_child_verdicts.main(argv)


# ===========================================================================
# 1 — the whole defect: children filed by hand never leave Intake
# ===========================================================================
class TheHandFiledChildrenAreAdoptedTest(unittest.TestCase):
    """Two build children and one PROOF child, all sitting in Intake, on an
    epic being planned. All three must end in Backlog carrying a verdict."""

    def setUp(self):
        self.children = _plan()
        self.board = _Board(**{c["identifier"]: "Intake" for c in self.children})
        self.rc = _planning_exit(self.board, self.children)

    def test_the_step_is_green(self):
        self.assertEqual(self.rc, 0)

    def test_every_intake_child_lands_in_backlog(self):
        """The failing assertion against today's code: nothing moved them, so
        the epic was approved and `backlog_children` saw none of them."""
        self.assertEqual(
            {i: self.board.lanes[i] for i in
             ("DRE-901", "DRE-902", "DRE-9099")},
            {"DRE-901": "Backlog", "DRE-902": "Backlog", "DRE-9099": "Backlog"},
        )

    def test_the_build_children_carry_fleet(self):
        self.assertEqual(self.board.verdict_on("DRE-901"), "FLEET")
        self.assertEqual(self.board.verdict_on("DRE-902"), "FLEET")

    def test_the_proof_child_keeps_the_proof_check_s_own_verdict(self):
        """This card adds no lane logic for the proof card. Its verdict is
        already written by `proof_and_demo.py` and is one a HUMAN acts on, and
        the sweep already hands it to a person when its blockers are Done
        (DRE-3385) — all that was missing was the card being in Backlog."""
        self.assertIn(self.board.verdict_on("DRE-9099"),
                      proof_and_demo.confirming_verdicts())

    def test_the_verdict_is_written_before_the_card_is_moved(self):
        """Backlog's entrance condition is that the card carries a routing
        verdict (`config/lane-contract.json`). A card moved first is a card
        that sits, however briefly, in a lane it does not yet qualify for — and
        the sweep runs every fifteen minutes."""
        first_move = min(
            i for i, (ident, _, _) in enumerate(self.board.advances)
            if ident == "DRE-901"
        )
        self.assertTrue(self.board.comments.get("DRE-901"),
                        "the verdict is posted before the move")
        self.assertGreaterEqual(first_move, 0)
        self.assertEqual(self.board.advances[first_move][1], "Backlog")

    def test_it_is_only_ever_taken_out_of_intake(self):
        """The move names its from-lane, so a child the sweep or a person has
        already moved on is never dragged back."""
        for identifier, to_state, from_states in self.board.advances:
            self.assertEqual(to_state, plan_child_verdicts.ADOPTION_LANE)
            self.assertEqual(from_states, plan_child_verdicts.ADOPTED_FROM)

    def test_the_destination_is_a_lane_the_planner_may_write(self):
        """The writer is the planning-exit process, which the lane contract
        names `plan.yml`. Read off the contract rather than asserted."""
        self.assertIn(
            "plan.yml",
            lane_contract.lane_writers(plan_child_verdicts.ADOPTION_LANE),
        )

    def test_a_second_pass_moves_nothing_and_writes_nothing(self):
        """A re-planned epic runs this again, and so does the activation
        backstop. Every child is already in Backlog carrying its verdict."""
        before = dict(self.board.comments)
        _planning_exit(self.board, self.children)
        self.assertEqual(
            {i: self.board.lanes[i] for i in
             ("DRE-901", "DRE-902", "DRE-9099")},
            {"DRE-901": "Backlog", "DRE-902": "Backlog", "DRE-9099": "Backlog"},
        )
        self.assertEqual(self.board.comments, before)


# ===========================================================================
# 2 — a child the verdict sends back to Planning stays out of Backlog
# ===========================================================================
class ANeedsWorkChildIsNotAdoptedTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.children = _plan(work_bodies=(NO_CRITERIA_BODY, WORK_BODY))
        self.board = _Board(**{c["identifier"]: "Intake" for c in self.children})
        self.note = os.path.join(self._tmp, "held.md")
        self.rc = _planning_exit(self.board, self.children, self.note)

    def test_it_is_not_moved_to_backlog(self):
        """It carries no verdict, and Backlog's entrance condition is a
        verdict. Moving it would land a card in the work segment that nothing
        can route — the `routing-no-verdict` freeze this card exists to end."""
        self.assertEqual(self.board.lanes["DRE-901"], "Intake")
        self.assertIsNone(self.board.verdict_on("DRE-901"))
        self.assertNotIn("DRE-901", [i for i, _, _ in self.board.advances])

    def test_its_siblings_are_still_adopted(self):
        self.assertEqual(self.board.lanes["DRE-902"], "Backlog")
        self.assertEqual(self.board.lanes["DRE-9099"], "Backlog")

    def test_it_is_named_on_the_epic(self):
        """`withheld_comment` already does this — the note lands on the card
        the CEO is about to be asked to approve."""
        body = Path(self.note).read_text(encoding="utf-8")
        self.assertIn(plan_child_verdicts.WITHHELD_TAG, body)
        self.assertIn("DRE-901", body)
        self.assertNotIn("DRE-902", body)

    def test_the_step_is_still_green(self):
        """One unroutable card must not bounce the whole plan — the note on
        the epic is what carries the news."""
        self.assertEqual(self.rc, 0)


# ===========================================================================
# 3 — a child that has already moved on is left exactly where it is
# ===========================================================================
class AChildPastIntakeIsUntouchedTest(unittest.TestCase):

    LANES = ("Backlog", "Todo", "In Progress")

    def test_it_is_neither_moved_nor_re_stamped(self):
        already = routing_verdict.verdict_comment(
            "FLEET", "an agent builds it")
        for lane in self.LANES:
            with self.subTest(lane=lane):
                children = _plan()
                board = _Board(**{c["identifier"]: "Intake"
                                  for c in children})
                board.lanes["DRE-901"] = lane
                board.comments["DRE-901"] = [already]
                _planning_exit(board, children)
                self.assertEqual(board.lanes["DRE-901"], lane)
                self.assertEqual(board.comments["DRE-901"], [already])

    def test_its_intake_siblings_are_adopted_all_the_same(self):
        already = routing_verdict.verdict_comment(
            "FLEET", "an agent builds it")
        children = _plan()
        board = _Board(**{c["identifier"]: "Intake" for c in children})
        board.lanes["DRE-901"] = "In Progress"
        board.comments["DRE-901"] = [already]
        _planning_exit(board, children)
        self.assertEqual(board.lanes["DRE-902"], "Backlog")
        self.assertEqual(board.lanes["DRE-9099"], "Backlog")


# ===========================================================================
# 4 — the blast radius: one more exit from Intake, and only one
# ===========================================================================
class NothingElseLeavesIntakeTest(unittest.TestCase):

    def test_an_intake_card_with_no_parent_epic_is_not_moved(self):
        """The adoption reads the EPIC's own children and nothing else, so a
        card nobody filed under an epic is not in the set. It still leaves
        Intake only through a groomer batch the CEO approves."""
        children = _plan()
        board = _Board(**{c["identifier"]: "Intake" for c in children})
        board.lanes["DRE-800"] = "Intake"     # a one-off, no parent epic
        _planning_exit(board, children)
        self.assertEqual(board.lanes["DRE-800"], "Intake")
        self.assertNotIn("DRE-800", [i for i, _, _ in board.advances])

    def test_the_only_exit_added_is_intake_to_backlog(self):
        """Adoption adds exactly one more way out of Intake. Read off the
        module's own constants so a later edit that pointed it at a build lane
        fails here rather than in a live plan run."""
        self.assertEqual(plan_child_verdicts.ADOPTED_FROM, "Intake")
        self.assertEqual(plan_child_verdicts.ADOPTION_LANE, "Backlog")
        self.assertNotEqual(plan_child_verdicts.ADOPTION_LANE,
                            routing_verdict.PROMOTION_LANE)


class AnUnapprovedEpicBuildsNothingTest(unittest.TestCase):
    """The other half of the approval model: Backlog is not a build lane.

    Adoption is safe only because `reconcile.promote_ready` still gates a child
    on its parent epic's state (DRE-1893). An epic sitting in `Green Light`
    waiting for the CEO releases nothing, whatever verdict its children carry.
    """

    FLEET = routing_verdict.verdict_comment(
        "FLEET", "the acceptance criteria are unit-testable")

    def _candidate(self, epic_state):
        return {
            "id": "uuid-DRE-901",
            "identifier": "DRE-901",
            "title": "the 1st piece",
            "description": "Adopt the children already filed under the epic.",
            "createdAt": "2026-07-01T00:00:00.000Z",
            "parent": {"identifier": EPIC, "state": {"name": epic_state}},
            "labels": {"nodes": [{"name": n} for n in LABELS]},
            "comments": {"nodes": [{"body": self.FLEET}]},
            "inverseRelations": {"nodes": []},
        }

    def _promote(self, epic_state):
        moved: list[tuple] = []
        with patch.object(reconcile, "REPO_SLUG", "bureau-pipeline"), \
                patch.object(reconcile, "backlog_children",
                             return_value=[self._candidate(epic_state)]), \
                patch.object(reconcile, "epic_blockers_unmet",
                             return_value=False), \
                patch.object(reconcile.mid_epic, "last_green_light",
                             return_value="2026-08-01T00:00:00.000Z"), \
                patch.object(reconcile, "card_state", return_value="Done"), \
                patch.object(reconcile.linear_ops, "add_label"), \
                patch.object(reconcile.linear_ops, "cmd_advance",
                             side_effect=lambda *a, **k: moved.append(a)), \
                patch.object(reconcile.linear_ops, "cmd_comment"), \
                patch.object(reconcile.linear_ops, "count_comments",
                             return_value=0):
            promoted = reconcile.promote_ready(active_count=0)
        return promoted, moved

    def test_a_planned_but_unapproved_epic_promotes_nothing(self):
        for lane in ("Planning", "Green Light", "Backlog"):
            with self.subTest(epic_lane=lane):
                promoted, moved = self._promote(lane)
                self.assertEqual(promoted, 0)
                self.assertEqual(moved, [])

    def test_and_the_approval_is_what_releases_it(self):
        """The same card, the same verdict — the epic moving to In Progress is
        the whole difference. That is the CEO's approval act, and it is what
        makes the epic's green light the approval for its children."""
        promoted, moved = self._promote("In Progress")
        self.assertEqual(promoted, 1)
        self.assertEqual([a[:2] for a in moved], [("DRE-901", "Todo")])


# ===========================================================================
# 5 — the writer is the planning-exit process, and the checks say so
# ===========================================================================
def wf_steps() -> list:
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []]


class TheWriterIsDeclaredTest(unittest.TestCase):

    def test_every_site_that_stamps_also_adopts(self):
        """Adoption is part of `stamp`, not a second subcommand a site could
        be wired without — so the three places plan.yml finishes a plan's
        children all adopt, and none of them can drift apart from the others."""
        src = MODULE.read_text(encoding="utf-8")
        self.assertTrue("cmd_advance" in src,
                        "the batch stamper performs the adoption move")
        runs = [s.get("run") or "" for s in wf_steps()]
        sites = [r for r in runs if "plan_child_verdicts.py stamp" in r]
        self.assertEqual(len(sites), 3, "plan.yml's three planning-exit sites")

    def test_the_move_is_not_a_second_state_writer(self):
        """One door. The lane write goes through `linear_ops`, which is what
        `ready_lane_writers.py` discovers writers through — a module that built
        its own `stateId` mutation would be a writer no check can see."""
        src = MODULE.read_text(encoding="utf-8")
        self.assertFalse("stateId" in src,
                         "no second state writer outside linear_ops.py")
        self.assertTrue("linear_ops.cmd_advance" in src,
                        "the move goes through the one write layer")


if __name__ == "__main__":
    unittest.main()
