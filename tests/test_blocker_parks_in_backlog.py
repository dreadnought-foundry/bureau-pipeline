"""Regression pin: a blocked card parks in Backlog, never returns to Todo.

Origin (2026-06-12): DRE-1286's agent wrote a blocker note and the workflow
moved the card to Todo — but the relay dispatches a fresh agent on EVERY
Todo transition, and blockers are deterministic (same card text, same wall),
so a second agent was dispatched 6 seconds later into an infinite
block/redispatch loop. Backlog is inert: nothing dispatches from it until
a human or the dependency gate releases the card.

The blocker branch lives in shell inside agent-task.yml, so this pins the
script text of that branch specifically (not the dead-run branch, which
legitimately requeues to Todo for its first 2 attempts).
"""

import os
import re
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)


def blocker_branch() -> str:
    src = open(WORKFLOW).read()
    m = re.search(r"elif \[ -f /tmp/agent-blocker\.txt \]; then(.*?)\n\s*else\b", src, re.S)
    if not m:
        raise AssertionError("blocker branch not found in agent-task.yml")
    return m.group(1)


def report_step() -> str:
    """The 'Report result to Linear' step body in agent-task.yml."""
    src = open(WORKFLOW).read()
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    if not m:
        raise AssertionError("'Report result to Linear' step not found in agent-task.yml")
    return m.group(1)


def escalation_branch() -> str:
    """The escalation branch of the Report step (DRE-1655): the agent stopped to
    ask the CEO a decision and the workflow parks the card in Plan Review."""
    src = report_step()
    m = re.search(
        r"elif \[ -f /tmp/agent-escalation\.txt \](.*?)\n\s*elif \[ -f /tmp/agent-blocker\.txt \]",
        src,
        re.S,
    )
    if not m:
        raise AssertionError("escalation branch not found in agent-task.yml")
    return m.group(1)


class ProposeMachineryRetiredTest(unittest.TestCase):
    """DRE-1662: the shelved propose-first hard-stop machinery is retired. The
    `proposed` marker (and its build-start clear) must be GONE from the build
    workflow — nothing may re-stamp or read it."""

    def test_no_proposed_marker_anywhere_in_build_workflow(self):
        src = open(WORKFLOW).read()
        self.assertNotIn(
            "proposed", src,
            "the retired `proposed` propose-gate marker must not appear in agent-task.yml",
        )

    def test_propose_workflow_file_removed(self):
        propose = os.path.join(os.path.dirname(WORKFLOW), "propose.yml")
        self.assertFalse(
            os.path.exists(propose),
            "the shelved read-only propose.yml workflow must be retired/removed",
        )


class EscalateByExceptionTest(unittest.TestCase):
    """DRE-1655/1706: on genuine uncertainty the agent stops BEFORE a PR, posts a
    plain-English question, and the workflow parks the card in Plan Review (the
    existing "needs you" lane). This is distinct from the blocker→Backlog path."""

    def test_escalation_branch_parks_in_plan_review(self):
        self.assertIn('"Plan Review"', escalation_branch())

    def test_escalation_branch_does_not_go_to_backlog(self):
        # Escalation is a human DECISION that unblocks the build; it must NOT be
        # parked in Backlog (that is the impossible-as-specified blocker path).
        self.assertNotIn('"Backlog"', escalation_branch())

    def test_escalation_posts_the_question_comment(self):
        branch = escalation_branch()
        self.assertIn("linear_ops.py comment", branch)
        self.assertIn("agent-escalation.txt", branch)

    def test_escalation_checked_before_blocker(self):
        # Order matters: an escalation note (→ Plan Review) takes precedence over
        # the blocker note (→ Backlog) so a decision card isn't buried in Backlog.
        src = report_step()
        esc = src.index("agent-escalation.txt")
        blk = src.index("agent-blocker.txt")
        self.assertLess(esc, blk, "escalation must be checked before blocker")

    def test_gate_treats_escalation_as_evidence(self):
        # The agent-result gate must not flag an escalation (no branch/PR) as a
        # silent death — it passes --escalation-file so the gate stays green.
        src = open(WORKFLOW).read()
        self.assertIn("--escalation-file /tmp/agent-escalation.txt", src)


class BlockerParksInBacklogTest(unittest.TestCase):
    def test_blocker_branch_parks_in_backlog(self):
        self.assertIn('state "$CARD" "Backlog"', blocker_branch())

    def test_blocker_branch_never_returns_to_todo(self):
        self.assertNotIn('"Todo"', blocker_branch())

    def test_dead_run_branch_still_requeues_to_todo(self):
        # The dead-run path is nondeterministic (timeouts, turn limits) — a
        # fresh agent CAN succeed there, so its capped Todo requeue stays.
        src = open(WORKFLOW).read()
        tail = src.split("agent-blocker.txt ]; then", 1)[1]
        dead_branch = tail.split("\n          else\n", 1)[1]
        self.assertIn('state "$CARD" "Todo"', dead_branch)


if __name__ == "__main__":
    unittest.main()
