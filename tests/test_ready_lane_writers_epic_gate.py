"""The writer check proves every Todo seam carries the epic refusal (DRE-5319).

DRE-5316 put `epic_todo_gate.refusal` in the three places a card can be put in
Todo — `guarded_state_write`, `_create_card` and `create_card`. Nothing held it
there: a fourth seam written tomorrow, or one of the three losing its call in a
refactor, would put an epic in Todo again with every test still green.

`ready_lane_writers.epic_gate_problems` reads the write layer's seam functions
by AST and reports `epic-can-enter-todo`, naming the function, for any that can
write Todo and neither calls `epic_todo_gate.refusal` nor delegates its write to
one that does. It is fed copies of the real write layer here, each with one
call taken out, and a new seam nobody remembered.
"""

import ast
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ready_lane_writers as rlw  # noqa: E402

SEAM = os.path.join(ROOT, "scripts", rlw.SEAM_MODULE)
TAG = "epic-can-enter-todo"


def _source() -> str:
    with open(SEAM, encoding="utf-8") as fh:
        return fh.read()


def _without_refusal(source: str, function: str) -> str:
    """The write layer with `function`'s call of `epic_todo_gate.refusal`
    replaced by a call of something that is not it — every other function,
    `create_card`'s own call included, left exactly as it is."""
    tree = ast.parse(source)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == function)
    lines = source.splitlines(keepends=True)
    head = "".join(lines[: node.lineno - 1])
    body = "".join(lines[node.lineno - 1: node.end_lineno])
    tail = "".join(lines[node.end_lineno:])
    assert "epic_todo_gate.refusal(" in body, function
    return head + body.replace("epic_todo_gate.refusal(", "_not_the_refusal(") + tail


class _Copy:
    """A throwaway root whose scripts/ holds `source` as the write layer."""

    def __init__(self, source: str):
        self.source = source

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="rlw-epic-")
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", rlw.SEAM_MODULE), "w",
                  encoding="utf-8") as fh:
            fh.write(self.source)
        return self.root

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)


def _findings(problems, function):
    return [p for p in problems if TAG in p and f"`{function}`" in p]


class TheRealWriteLayer(unittest.TestCase):
    def test_the_real_checkout_produces_no_finding(self):
        self.assertEqual(rlw.epic_gate_problems(), [])

    def test_the_check_is_part_of_the_writer_problems(self):
        # The one call `check` and the suite already make.
        self.assertEqual([p for p in rlw.writer_problems() if TAG in p], [])

    def test_the_seams_it_holds_are_derived_not_listed(self):
        self.assertEqual(
            set(rlw.todo_seams()),
            {"guarded_state_write", "cmd_state", "cmd_advance", "_create_card",
             "create_card"},
        )


class AGuardedSeamWithoutItsCallIsNamed(unittest.TestCase):
    def test_guarded_state_write_without_the_refusal_is_one_finding(self):
        with _Copy(_without_refusal(_source(), "guarded_state_write")) as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(len(_findings(problems, "guarded_state_write")), 1, problems)
        # cmd_state and cmd_advance delegate their write to it: the hole is
        # the one function, named once, not three times.
        self.assertEqual(_findings(problems, "cmd_state"), [])
        self.assertEqual(_findings(problems, "cmd_advance"), [])

    def test_create_card_helper_without_the_refusal_is_one_finding(self):
        with _Copy(_without_refusal(_source(), "_create_card")) as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(len(_findings(problems, "_create_card")), 1, problems)

    def test_create_card_without_the_refusal_is_one_finding(self):
        with _Copy(_without_refusal(_source(), "create_card")) as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(len(_findings(problems, "create_card")), 1, problems)


class ASeamNobodyRememberedIsNamed(unittest.TestCase):
    def test_a_new_seam_that_writes_todo_without_the_refusal_is_named(self):
        extra = (
            "\n\ndef requeue_fast(identifier: str) -> None:\n"
            "    issue = get_issue(identifier)\n"
            "    _set_state(identifier, issue['id'],\n"
            "               state_id(issue['team']['id'], 'Todo'))\n"
        )
        with _Copy(_source() + extra) as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(_findings(problems, "requeue_fast")), 1, problems)
        self.assertEqual(len(problems), 1, problems)

    def test_a_new_seam_handed_its_lane_by_the_caller_is_named(self):
        extra = (
            "\n\ndef move(identifier: str, lane: str) -> None:\n"
            "    issue = get_issue(identifier)\n"
            "    _set_state(identifier, issue['id'],\n"
            "               state_id(issue['team']['id'], lane))\n"
        )
        with _Copy(_source() + extra) as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(_findings(problems, "move")), 1, problems)

    def test_a_new_seam_that_delegates_to_the_guarded_write_is_clean(self):
        extra = (
            "\n\ndef requeue(identifier: str) -> None:\n"
            "    issue = get_issue(identifier)\n"
            "    sid, stype = state_id_and_type(issue['team']['id'], 'Todo')\n"
            "    guarded_state_write(identifier, issue, sid, stype, 'Todo')\n"
        )
        with _Copy(_source() + extra) as root:
            self.assertEqual(rlw.epic_gate_problems(root), [])

    def test_a_new_seam_that_can_only_write_another_lane_is_clean(self):
        extra = (
            "\n\ndef park_for_planning(identifier: str) -> None:\n"
            "    issue = get_issue(identifier)\n"
            "    _set_state(identifier, issue['id'],\n"
            "               state_id(issue['team']['id'], 'Planning'))\n"
        )
        with _Copy(_source() + extra) as root:
            self.assertEqual(rlw.epic_gate_problems(root), [])

    def test_a_write_layer_that_cannot_be_read_is_reported_not_passed(self):
        with _Copy("def broken(:\n") as root:
            problems = rlw.epic_gate_problems(root)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(TAG, problems[0])


if __name__ == "__main__":
    unittest.main()
