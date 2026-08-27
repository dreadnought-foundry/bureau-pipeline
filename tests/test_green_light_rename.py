"""DRE-2722: one name stopped covering two jobs — the rename and the
escalation move ship together.

The retired lane name meant two unrelated things. The LANE was where the
pipeline parked a card that needed a person: an unroutable repo, an agent
that gave up, a question only the CEO can answer. The console PAGE of the
same name is the approve-the-plan queue, and it deliberately never read that
lane.

The plan-approval meaning is the real one, so it keeps its job under a name
that says what the CEO does with it: **Green Light**. What did not belong —
the pipeline parking broken cards there — moves to **Triage**, the lane for
a card that went wrong (DRE-2723).

The coupling is why this is one change and not two: `reconcile.PARKED_STATE`
and the relay's own copy of the literal are BOTH the escalation meaning.
Rename the lane without moving the escalations and those paths point at a
lane that no longer exists — a silent break in the one mechanism that tells
the CEO an agent is stuck.

These tests pin BOTH halves, plus the sweep that made the card a single
change: the literal must not survive anywhere in the repo. The needle is
assembled from two fragments on purpose so this file does not match itself.

*Amended 2026-08-27 (DRE-2776): "what did not belong" was drawn too wide here.
The criteria DRE-2722 was accepted against moved the UNROUTABLE and the HELD
card to Triage — not the agent's escalate-by-exception question, which is a
card waiting on a judgement rather than a card that went wrong, and which goes
to Green Light. This file keeps the broken-card half; the escalation half is
pinned in `test_escalations_park_in_green_light.py`.*
"""
import os
import re
import subprocess  # nosec B404 — fixed-arg call to the git CLI only
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_scope  # noqa: E402
import linear_ops  # noqa: E402
import reconcile  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

#: The retired lane name, assembled so this guard never matches its own source.
OLD_LANE = "Plan " + "Review"
NEW_LANE = "Green Light"
PARKED_LANE = "Triage"

#: The one thing allowed to still name the retired lane: the transitional alias
#: that keeps both directions of the board transition working until the rename
#: is actually clicked in Linear. A line may carry the old name only if it
#: carries this marker, so the exemption is a declared intent and not a
#: path allow-list.
SHIM_MARKER = "lane-rename-shim"

#: Where that marker is allowed to appear — the alias itself, and the tests that
#: exercise it. `linear_ops` derives its write-direction fallback from the same
#: dict rather than restating the literal, so it is deliberately NOT here.
SHIM_FILES = ["scripts/lane_scope.py", "tests/test_lane_scope.py"]


def src(name: str) -> str:
    return open(os.path.join(WORKFLOWS, name)).read()


def tracked_files() -> list[str]:
    """Every file git tracks — the repo's own content, never the runtime
    `.bureau-pipeline/` checkout a self-hosted run drops in the working tree
    (untracked, and pinned to whatever ref that run consumed)."""
    out = subprocess.run(  # nosec B603 B607 — fixed args, no shell
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    files = [p for p in out.stdout.split("\0") if p]
    assert files, "git ls-files returned nothing — cannot run the sweep"
    return files


def retired_lane_mentions() -> list[tuple[str, int, str]]:
    """`(path, lineno, line)` for every tracked line still naming the old lane."""
    hits = []
    for rel in tracked_files():
        path = os.path.join(ROOT, rel)
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue  # binaries / submodule entries have no lane name
        for i, line in enumerate(text.splitlines(), 1):
            if OLD_LANE in line:
                hits.append((rel, i, line))
    return hits


def step_body(workflow: str, step_name_re: str) -> str:
    """One named step's body, read from the LIVE workflow YAML."""
    body = src(workflow)
    m = re.search(
        rf"name:\s*{step_name_re}(.*?)(?:\n      - name:|\Z)", body, re.S
    )
    assert m, f"step matching {step_name_re!r} not found in {workflow}"
    return m.group(1)


class NoStaleLaneNameTest(unittest.TestCase):
    """The sweep the card made a hard requirement: the two references it named
    were found by READING, and a third would break the same way. Nothing that
    ships from this repo — workflow, script, brief, standard, doc — may still
    name the retired lane."""

    def test_the_old_lane_name_is_gone_from_every_tracked_file(self):
        offenders = [
            f"{rel}:{i}" for rel, i, line in retired_lane_mentions()
            if SHIM_MARKER not in line
        ]
        self.assertEqual(
            [], offenders,
            f"the retired lane name survives at: {offenders}. The escalation "
            f"meaning is now {PARKED_LANE!r}; the plan-approval meaning is "
            f"now {NEW_LANE!r}.",
        )

    def test_the_only_survivors_are_the_declared_transitional_shim(self):
        # The exemption above is a hole in the sweep, so pin its size: the alias
        # and its own tests, nowhere else. Anything that widens it has to widen
        # this list too, in the open.
        self.assertEqual(
            SHIM_FILES, sorted({rel for rel, _, _ in retired_lane_mentions()})
        )

    def test_the_alias_is_declared_exactly_once(self):
        # One literal, so the board rename is one deletion and cannot leave half
        # the transition behind. The write direction inverts this dict instead of
        # repeating it.
        self.assertEqual({OLD_LANE: NEW_LANE}, lane_scope.LANE_ALIASES)
        self.assertEqual(
            1,
            len([1 for rel, _, _ in retired_lane_mentions()
                 if rel == "scripts/lane_scope.py"]),
        )


class BrokenCardsParkInTriageTest(unittest.TestCase):
    """Half one, as DRE-2776 narrowed it: every path that parks a card because
    the card WENT WRONG points at Triage. The fix loop that could not converge,
    a check no agent can satisfy, a red default branch. The agent's question to
    the CEO is not one of these and left this class — see
    `test_escalations_park_in_green_light.py`."""

    def test_reconcile_parked_state_is_triage(self):
        self.assertEqual(PARKED_LANE, reconcile.PARKED_STATE)

    def test_a_card_in_triage_reads_as_human_parked(self):
        payload = {"issue": {"state": {"name": PARKED_LANE}, "labels": {"nodes": []}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            self.assertTrue(reconcile.card_parked_for_human("DRE-2009"))

    def test_agent_fix_report_parks_in_triage(self):
        step = step_body("agent-fix.yml", "Report")
        self.assertIn(f'"{PARKED_LANE}"', step)
        self.assertNotIn(f'"{NEW_LANE}"', step)

    def test_unfixable_check_gate_parks_in_triage(self):
        step = step_body(
            "agent-fix.yml", "Escalate checks the loop structurally cannot fix"
        )
        self.assertIn("unfixable_checks.py", step)
        self.assertIn(f'"{PARKED_LANE}"', step)
        self.assertNotIn(f'"{NEW_LANE}"', step)

    def test_red_main_repair_escalation_parks_in_triage(self):
        body = src("red-main-repair.yml")
        self.assertIn("/tmp/repair-escalation.txt", body)
        self.assertIn(f'"{PARKED_LANE}"', body)


class PlanApprovalKeepsItsJobTest(unittest.TestCase):
    """Half two: the approve-the-plan gate survives the rename intact — same
    step, same position, new name."""

    def test_planned_epic_moves_to_green_light(self):
        step = step_body("plan.yml", "Epic → Green Light")
        self.assertIn(f'state "$EPIC" "{NEW_LANE}"', step)

    def test_the_epic_gate_is_not_the_parked_lane(self):
        # A planned epic waiting on the CEO is not a card that went wrong.
        step = step_body("plan.yml", "Epic → Green Light")
        self.assertNotIn(f'"{PARKED_LANE}"', step)

    def test_green_light_epic_does_not_promote_its_children(self):
        self.assertNotIn(NEW_LANE, reconcile.EPIC_ACTIVE_STATES)


class GreenLightResolvesBeforeTheBoardIsRenamedTest(unittest.TestCase):
    """The gap the code cannot close by itself: renaming the LANE is a manual
    click in the Linear workspace, and this code lands first. Until that click,
    the live team answers with the retired name only — so an exact-match lookup
    would make plan.yml's approval step raise on the first epic that finishes
    planning, and the CEO would simply stop being handed plans.

    These run the real lookup against a live-SHAPED `workflowStates` reply, so
    they pin the thing the YAML-text tests could not: that the literal the
    workflow passes actually resolves."""

    #: The team as the board reads TODAY — retired lane present, new one absent.
    BOARD_TODAY = {"workflowStates": {"nodes": [
        {"id": "s-backlog", "name": "Backlog", "type": "backlog"},
        {"id": "s-triage", "name": PARKED_LANE, "type": "unstarted"},
        {"id": "s-todo", "name": "Todo", "type": "unstarted"},
        {"id": "s-old", "name": OLD_LANE, "type": "unstarted"},
        {"id": "s-done", "name": "Done", "type": "completed"},
    ]}}

    #: The team AFTER someone does the rename — both cannot coexist, but pin
    #: the ordering anyway so the shim can never shadow the real lane.
    BOARD_RENAMED = {"workflowStates": {"nodes": [
        {"id": "s-green", "name": NEW_LANE, "type": "unstarted"},
        {"id": "s-old", "name": OLD_LANE, "type": "unstarted"},
    ]}}

    def resolve(self, board, name):
        with mock.patch.object(linear_ops, "gql", return_value=board):
            return linear_ops.state_id_and_type("team-1", name)

    def test_the_approval_lane_resolves_against_the_board_as_it_is_today(self):
        self.assertEqual(("s-old", "unstarted"), self.resolve(self.BOARD_TODAY, NEW_LANE))

    def test_the_renamed_lane_wins_over_the_fallback(self):
        self.assertEqual(("s-green", "unstarted"), self.resolve(self.BOARD_RENAMED, NEW_LANE))

    def test_an_unknown_lane_still_fails_loud(self):
        # The shim is a bridge for ONE rename, not a blanket "close enough".
        with self.assertRaises(linear_ops.LinearError):
            self.resolve(self.BOARD_TODAY, "Some Lane Nobody Made")

    def test_the_escalation_lane_needs_no_fallback(self):
        # Triage predates this change on the live board, so it resolves on its
        # own name — and must never acquire a fallback that hides its absence.
        self.assertEqual(("s-triage", "unstarted"), self.resolve(self.BOARD_TODAY, PARKED_LANE))
        with self.assertRaises(linear_ops.LinearError):
            self.resolve(self.BOARD_RENAMED, PARKED_LANE)


if __name__ == "__main__":
    unittest.main()
