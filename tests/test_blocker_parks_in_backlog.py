"""Regression pin: a blocked card parks in the queue lane, never returns to Todo.

Origin (2026-06-12): DRE-1286's agent wrote a blocker note and the workflow
moved the card to Todo — but the relay dispatches a fresh agent on EVERY
Todo transition, and blockers are deterministic (same card text, same wall),
so a second agent was dispatched 6 seconds later into an infinite
block/redispatch loop. Backlog is inert: nothing dispatches from it until
a human or the dependency gate releases the card.

The blocker branch lives in shell inside agent-task.yml, so this pins the
script text of that branch specifically (not the dead-run branch, which
legitimately requeues to Todo for its first 2 attempts).

The lane is `lane_contract.QUEUE_LANE` (DRE-6745), read by the shell through
the seam the step shells read every pipeline constant from: `Backlog` until
DRE-6742 moves the planning exit, `Queued` after. No script that parks a card
names the lane itself.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lane_contract  # noqa: E402
import step_shell  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

#: The seam: `lane_contract.QUEUE_LANE`, printed by the pipeline's own module.
QUEUE_SEAM = (
    "QUEUE_LANE=$(python3 -c 'import sys; sys.path.insert(0, \".bureau-pipeline/scripts\"); "
    "import lane_contract; print(lane_contract.QUEUE_LANE)')"
)

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-task.yml"
)


def blocker_branch() -> str:
    src = step_shell.workflow_source(WORKFLOW)
    m = re.search(r'elif \[ -f "\$NOTES_DIR"/agent-blocker\.txt \]; then(.*?)\n\s*else\b', src, re.S)
    if not m:
        raise AssertionError("blocker branch not found in agent-task.yml")
    return m.group(1)


def report_step() -> str:
    """The 'Report result to Linear' step body in agent-task.yml."""
    src = step_shell.workflow_source(WORKFLOW)
    m = re.search(
        r"name:\s*Report result to Linear(.*?)(?:\n      - name:|\Z)", src, re.S
    )
    if not m:
        raise AssertionError("'Report result to Linear' step not found in agent-task.yml")
    return m.group(1)


def escalation_branch() -> str:
    """The escalation branch of the Report step (DRE-1655): the agent stopped to
    ask the CEO a decision and the workflow parks the card in Green Light."""
    src = report_step()
    m = re.search(
        r'elif \[ -f "\$NOTES_DIR"/agent-escalation\.txt \](.*?)\n\s*elif \[ -f "\$NOTES_DIR"/agent-blocker\.txt \]',
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
        src = step_shell.workflow_source(WORKFLOW)
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
    plain-English question, and the workflow parks the card in Green Light (the
    CEO's "needs you" queue — DRE-2776). This is distinct from the
    blocker→Backlog path."""

    def test_escalation_branch_parks_in_green_light(self):
        self.assertIn('"Green Light"', escalation_branch())

    def test_escalation_branch_does_not_go_to_backlog(self):
        # Escalation is a human DECISION that unblocks the build; it must NOT be
        # parked in Backlog (that is the impossible-as-specified blocker path).
        self.assertNotIn('"Backlog"', escalation_branch())

    def test_escalation_posts_the_question_comment(self):
        branch = escalation_branch()
        self.assertIn("linear_ops.py comment", branch)
        self.assertIn("agent-escalation.txt", branch)

    def test_escalation_checked_before_blocker(self):
        # Order matters: an escalation note (→ Green Light) takes precedence over
        # the blocker note (→ Backlog) so a decision card isn't buried in Backlog.
        src = report_step()
        esc = src.index("agent-escalation.txt")
        blk = src.index("agent-blocker.txt")
        self.assertLess(esc, blk, "escalation must be checked before blocker")

    def test_gate_treats_escalation_as_evidence(self):
        # The agent-result gate must not flag an escalation (no branch/PR) as a
        # silent death — it passes --escalation-file so the gate stays green.
        src = step_shell.workflow_source(WORKFLOW)
        self.assertIn("--escalation-file /tmp/agent-escalation.txt", src)


class BlockerParksInBacklogTest(unittest.TestCase):
    def test_blocker_branch_parks_in_the_queue_lane(self):
        self.assertIn('state "$CARD" "$QUEUE_LANE" --park', blocker_branch())
        self.assertNotIn('"Backlog"', blocker_branch())

    def test_the_queue_lane_is_read_through_the_seam(self):
        self.assertIn(QUEUE_SEAM, report_step())
        src = report_step()
        self.assertLess(src.index(QUEUE_SEAM), src.index('"$QUEUE_LANE"'))

    def test_blocker_branch_never_returns_to_todo(self):
        self.assertNotIn('"Todo"', blocker_branch())

    def test_blocker_branch_posts_one_marker_carrying_the_class(self):
        # DRE-6444: one posting command whose own text carries the act
        # registry's anchor, so `check_act_receipts.py` matches one site.
        branch = re.split(r"\n\s*elif\b", blocker_branch(), 1)[0]
        self.assertEqual(1, branch.count("linear_ops.py comment"))
        self.assertIn('"🛑 Agent blocked: class=$BLOCKER_CLASS · ', branch)

    def test_the_class_comes_from_the_one_reader(self):
        # The script restates no phrase list and no stamp grammar (DRE-6438).
        src = report_step()
        self.assertIn('blocker_class.py classify "$NOTES_DIR"/agent-blocker.txt', src)
        self.assertIn('blocker_class.py reason "$NOTES_DIR"/agent-blocker.txt', src)
        self.assertNotIn("blocker-class:", src)

    def test_dead_run_branch_still_requeues_to_todo(self):
        # The dead-run path is nondeterministic (timeouts, turn limits) — a
        # fresh agent CAN succeed there, so its capped Todo requeue stays.
        src = step_shell.workflow_source(WORKFLOW)
        tail = src.split("agent-blocker.txt ]; then", 1)[1]
        dead_branch = tail.split("\n          else\n", 1)[1]
        self.assertIn('state "$CARD" "Todo"', dead_branch)


class NoParkWriterNamesTheQueueLaneTest(unittest.TestCase):
    """DRE-6745: the five scripts that park a card name no lane the queue
    replaces — every write and every receipt reads `QUEUE_LANE`, and every
    read tests membership in `QUEUE_LANES`. A `#` comment recording an
    incident keeps its words; `_scan_yaml` skips those lines."""

    PYTHON = ("dead_run.py", "medic_retry.py", "limit_recovery.py")
    SHELL = ("report_agent_result.sh", "report_fix_result.sh")

    def test_no_string_constant_in_the_python_names_backlog(self):
        for name in self.PYTHON:
            with self.subTest(script=name):
                self.assertEqual(
                    set(), lane_contract._scan_python(os.path.join(SCRIPTS, name), ["Backlog"]))

    def test_no_code_line_in_the_shells_names_backlog(self):
        for name in self.SHELL:
            with self.subTest(script=name):
                self.assertEqual(
                    set(), lane_contract._scan_yaml(os.path.join(SCRIPTS, name), ["Backlog"]))

    def test_each_shell_reads_the_lane_through_the_seam(self):
        for name in self.SHELL:
            with self.subTest(script=name):
                with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                    self.assertIn(QUEUE_SEAM, fh.read())

    def test_the_scan_is_not_vacuous(self):
        # The python scan parses: on this file's own source it finds the word.
        self.assertEqual({"Backlog"}, lane_contract._scan_python(__file__, ["Backlog"]))


if __name__ == "__main__":
    unittest.main()
