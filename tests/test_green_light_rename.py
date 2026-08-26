"""DRE-2722: one name stopped covering two jobs — the rename and the
escalation move ship together.

`Plan Review` meant two unrelated things. The LANE was where the pipeline
parked a card that needed a person: an unroutable repo, an agent that gave
up, a question only the CEO can answer. The PAGE of the same name is the
approve-the-plan queue, and it deliberately never read that lane.

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
"""
import os
import re
import subprocess  # nosec B404 — fixed-arg call to the git CLI only
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import linear_ops  # noqa: E402
import reconcile  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

#: The retired lane name, assembled so this guard never matches its own source.
OLD_LANE = "Plan " + "Review"
NEW_LANE = "Green Light"
PARKED_LANE = "Triage"


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


def step_body(workflow: str, step_name_re: str) -> str:
    """One named step's body, read from the LIVE workflow YAML."""
    body = src(workflow)
    m = re.search(
        rf"name:\s*{step_name_re}(.*?)(?:\n      - name:|\Z)", body, re.S
    )
    assert m, f"step matching {step_name_re!r} not found in {workflow}"
    return m.group(1)


def escalation_branch() -> str:
    """agent-task.yml's escalate-by-exception branch — the agent stopped to ask
    the CEO a decision (DRE-1655)."""
    step = step_body("agent-task.yml", "Report result to Linear")
    m = re.search(
        r"elif \[ -f /tmp/agent-escalation\.txt \](.*?)\n\s*elif \[ -f /tmp/agent-blocker\.txt \]",
        step,
        re.S,
    )
    assert m, "escalation branch not found in agent-task.yml"
    return m.group(1)


class NoStaleLaneNameTest(unittest.TestCase):
    """The sweep the card made a hard requirement: the two references it named
    were found by READING, and a third would break the same way. Nothing that
    ships from this repo — workflow, script, brief, standard, doc — may still
    name the retired lane."""

    def test_the_old_lane_name_is_gone_from_every_tracked_file(self):
        offenders = []
        for rel in tracked_files():
            path = os.path.join(ROOT, rel)
            try:
                text = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
                continue  # binaries / submodule entries have no lane name
            for i, line in enumerate(text.splitlines(), 1):
                if OLD_LANE in line:
                    offenders.append(f"{rel}:{i}")
        self.assertEqual(
            [], offenders,
            f"the retired lane name survives at: {offenders}. The escalation "
            f"meaning is now {PARKED_LANE!r}; the plan-approval meaning is "
            f"now {NEW_LANE!r}.",
        )


class EscalationsParkInTriageTest(unittest.TestCase):
    """Half one: every "a person is needed" path points at Triage."""

    def test_reconcile_parked_state_is_triage(self):
        self.assertEqual(PARKED_LANE, reconcile.PARKED_STATE)

    def test_a_card_in_triage_reads_as_human_parked(self):
        payload = {"issue": {"state": {"name": PARKED_LANE}, "labels": {"nodes": []}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            self.assertTrue(reconcile.card_parked_for_human("DRE-2009"))

    def test_a_card_in_the_green_light_lane_is_not_human_parked(self):
        # Green Light is the CEO's approve-the-plan queue, not a stuck-agent
        # signal: a PR whose card sits there must still get its fix agent.
        payload = {"issue": {"state": {"name": NEW_LANE}, "labels": {"nodes": []}}}
        with mock.patch.object(linear_ops, "gql", return_value=payload):
            self.assertFalse(reconcile.card_parked_for_human("DRE-2009"))

    def test_agent_task_escalation_parks_in_triage(self):
        branch = escalation_branch()
        self.assertIn(f'"{PARKED_LANE}"', branch)
        self.assertNotIn(NEW_LANE, branch)

    def test_agent_fix_report_parks_in_triage(self):
        step = step_body("agent-fix.yml", "Report")
        self.assertIn(f'"{PARKED_LANE}"', step)
        self.assertNotIn(NEW_LANE, step)

    def test_unfixable_check_gate_parks_in_triage(self):
        body = src("agent-fix.yml")
        m = re.search(r"unfixable_checks\.py.*?(?:\n      - name:|\Z)", body, re.S)
        self.assertIsNotNone(m, "unfixable_checks gate not found in agent-fix.yml")
        self.assertIn(f'"{PARKED_LANE}"', m.group(0))

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


if __name__ == "__main__":
    unittest.main()
