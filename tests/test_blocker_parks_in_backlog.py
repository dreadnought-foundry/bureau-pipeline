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


def in_progress_step() -> str:
    """The 'Card → In Progress' step body (start-of-build) in agent-task.yml."""
    src = open(WORKFLOW).read()
    m = re.search(
        r"name:\s*Card → In Progress(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    if not m:
        raise AssertionError("'Card → In Progress' step not found in agent-task.yml")
    return m.group(1)


class ProposedMarkerClearedOnBuildTest(unittest.TestCase):
    """DRE-1660: the `proposed` propose-gate marker must be cleared exactly once
    at build start, idempotently, so a card that returns to Todo gets a FRESH
    proposal instead of silently rebuilding off a stale one."""

    def test_in_progress_step_clears_proposed_marker(self):
        step = in_progress_step()
        self.assertIn("remove-label", step, "build start must remove the marker label")
        # `remove-label` and the `proposed` arg span a shell line-continuation,
        # so match across newlines.
        self.assertRegex(
            step,
            r"remove-label[\s\S]*?proposed",
            "build start must remove the `proposed` marker specifically",
        )

    def test_marker_clear_is_idempotent(self):
        # `|| true` so an absent marker (a card that never went through the
        # propose gate) is a no-op, never a failure.
        step = in_progress_step()
        m = re.search(r"remove-label[\s\S]*?proposed[^\n]*", step)
        self.assertIsNotNone(m)
        self.assertIn("|| true", m.group(0), "remove-label must not fail the build")

    def test_marker_cleared_exactly_once(self):
        # Exactly one discriminator clear in the whole build workflow.
        src = open(WORKFLOW).read()
        self.assertEqual(
            len(re.findall(r"remove-label[\s\S]*?proposed", src)),
            1,
            "the proposed marker must be cleared exactly once",
        )


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
