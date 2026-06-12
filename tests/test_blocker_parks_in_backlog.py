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
