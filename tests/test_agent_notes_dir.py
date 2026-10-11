"""The agent's three notes live in a directory a test can move (DRE-6961).

A build agent that stops before a pull request leaves one of three notes —
`agent-blocker.txt`, `agent-escalation.txt`, `agent-handback.txt` — and
`scripts/report_agent_result.sh` reads them. In a real run they sit in /tmp,
because that is where the prompt tells the agent to write them, and that path
does not move. The script reads the directory from `AGENT_NOTES_DIR`, which no
workflow sets, so a real run still reads /tmp.

The tests that drive the script used to write and delete the three notes at
their fixed /tmp paths. Two copies of the suite on one machine — the
suite-green harness (DRE-6865), or a workstation running several trees — then
read each other's notes and failed. Each test now points the variable at a
directory of its own.

Run: python3 -m pytest tests/test_agent_notes_dir.py -v
"""

from __future__ import annotations

import ast
import glob
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "report_agent_result.sh")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS)

import test_report_agent_result as report  # noqa: E402

VARIABLE = "AGENT_NOTES_DIR"
NAMES = ("agent-blocker.txt", "agent-escalation.txt", "agent-handback.txt")
#: Built from parts so this file's own source never carries a fixed path.
FIXED = tuple(os.path.join("/tmp", name) for name in NAMES)  # nosec B108

#: The test classes that write a note and run the script to read it.
NOTE_WRITERS = (
    "tests/test_report_agent_result.py::ABlockerIsClassifiedBeforeItParks",
    "tests/test_turn_budget_scenario.py::HandBackBeforeDyingScenario",
    "tests/test_turn_budget_scenario.py::EveryTurnDeathLeavesTheTagScenario",
)


class TwoCopiesRunSideBySide(unittest.TestCase):
    """The acceptance test: the same note-writing tests, twice, at once."""

    def test_two_processes_running_the_note_tests_both_pass(self):
        env = {k: v for k, v in os.environ.items() if k != VARIABLE}
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   *NOTE_WRITERS]
        copies = [subprocess.Popen(command, cwd=ROOT, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                  for _ in range(2)]
        outputs = [copy.communicate(timeout=600)[0] for copy in copies]
        for copy, output in zip(copies, outputs):
            self.assertEqual(0, copy.returncode, output[-4000:])
            self.assertIn(" passed", output)


class TheScriptReadsTheVariable(unittest.TestCase):
    """Traced with `bash -x`: every note the script tests for, by path."""

    def _trace(self, env_extra=None) -> str:
        with mock.patch.dict(os.environ):
            os.environ.pop(VARIABLE, None)
            proc, _ = report.run_pr_opened(
                self, ["bash", "-x", SCRIPT], card_pr=report.CARD_PR_NO_PR_STUB,
                env_extra=env_extra)
        self.assertEqual(0, proc.returncode, proc.stderr[-4000:])
        return proc.stderr

    def test_unset_it_reads_the_three_tmp_paths_as_today(self):
        trace = self._trace()
        self.assertIn("-f /tmp/agent-blocker.txt", trace)
        self.assertIn("-f /tmp/agent-handback.txt", trace)
        self.assertIn("-f /tmp/agent-escalation.txt", trace)

    def test_set_it_reads_the_directory_it_names_and_never_tmp(self):
        notes = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, notes)
        trace = self._trace({VARIABLE: notes})
        for name in NAMES:
            self.assertIn(f"-f {os.path.join(notes, name)}", trace)
        for path in FIXED:
            self.assertNotIn(path, trace)

    def test_the_turn_cap_reader_is_handed_the_same_hand_back(self):
        """`dead_run.py decide` reads the hand-back on a turn-cap death; the
        script hands it the path rather than letting it assume /tmp."""
        with open(SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f'"${{{VARIABLE}:-/tmp}}"', text)
        self.assertIn('--handback-file "$NOTES_DIR"/agent-handback.txt', text)


class TheHarnessReadsTheVariable(unittest.TestCase):
    """`scripts/harness/agent_run.py` clears and reads two of the notes around
    the agent it runs; its tests run it with a stand-in agent, so it honors
    the same variable and never deletes a /tmp note during the suite."""

    def test_a_stale_note_is_cleared_and_the_new_one_read_from_the_directory(self):
        from types import SimpleNamespace

        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from harness import agent_run

        with tempfile.TemporaryDirectory() as td:
            notes = os.path.join(td, "notes")
            os.makedirs(notes)
            with open(os.path.join(notes, "agent-blocker.txt"), "w") as fh:
                fh.write("a stale note from an earlier scenario")

            def runner(argv, **kwargs):
                if argv[0] == "git":
                    if argv[1] == "clone":
                        os.makedirs(argv[-1], exist_ok=True)
                else:
                    with open(os.path.join(notes, "agent-escalation.txt"), "w") as fh:
                        fh.write("Which of A or B?")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            card = agent_run.SeededCard(identifier="DRE-6961", title="t",
                                        description="d", url="https://x")
            with mock.patch.dict(os.environ, {VARIABLE: notes,
                                              "HARNESS_AGENT_MODEL": "claude-opus-5"}):
                result = agent_run.run_agent(
                    card, repo="dreadnought-foundry/bureau-harness", token="t",
                    workdir=os.path.join(td, "clone"), pipeline_root=ROOT,
                    runner=runner, log=lambda *a, **k: None)
        self.assertEqual("Which of A or B?", result.escalation)
        self.assertEqual("", result.blocker)


class NoWorkflowSetsIt(unittest.TestCase):

    def test_no_workflow_file_names_the_variable(self):
        files = sorted(glob.glob(os.path.join(WORKFLOWS, "*.yml")))
        self.assertTrue(files)
        naming = [os.path.basename(f) for f in files
                  if VARIABLE in open(f, encoding="utf-8").read()]
        self.assertEqual([], naming)


def _asserting(node: ast.AST, parents: dict) -> bool:
    """True when `node` sits in an `assert` statement or an `assert*` call."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name.startswith("assert"):
                return True
    return False


def _docstring(node: ast.AST, parents: dict) -> bool:
    return isinstance(parents.get(node), ast.Expr)


def _asserted_phrases(tree: ast.AST, parents: dict) -> set:
    """The strings a `for phrase in (...): assert...` loop checks one by one,
    whether the loop names the tuple or a module constant holding it."""
    constants = {target.id: node.value for node in getattr(tree, "body", [])
                 if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)}
    phrases = set()
    for loop in ast.walk(tree):
        if not isinstance(loop, ast.For):
            continue
        if not any(_asserting(n, parents) for stmt in loop.body
                   for n in ast.walk(stmt) if isinstance(n, ast.Constant)
                   or isinstance(n, ast.Name)):
            continue
        source = loop.iter
        if isinstance(source, ast.Name):
            source = constants.get(source.id)
        if isinstance(source, (ast.Tuple, ast.List, ast.Set)):
            phrases.update(id(element) for element in source.elts)
    return phrases


def fixed_path_uses(path: str) -> list[str]:
    """`file:line` for each string in `path` that names a fixed note path
    anywhere but in prose or an assertion that a text names the default."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    asserted = _asserted_phrases(tree, parents)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not any(fixed in node.value for fixed in FIXED):
            continue
        if (_docstring(node, parents) or _asserting(node, parents)
                or id(node) in asserted):
            continue
        found.append(f"{os.path.relpath(path, ROOT)}:{node.lineno}")
    return found


class NoTestTouchesTheFixedPaths(unittest.TestCase):

    def test_no_test_source_names_a_fixed_note_path_outside_an_assertion(self):
        found = []
        for path in sorted(glob.glob(os.path.join(TESTS, "**", "*.py"), recursive=True)):
            found += fixed_path_uses(path)
        self.assertEqual([], found)

    def test_the_search_finds_a_note_written_to_a_fixed_path(self):
        """The search itself, on a source that does what the old tests did."""
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "test_old_shape.py")
            with open(source, "w", encoding="utf-8") as fh:
                fh.write(f"BLOCKER = {FIXED[0]!r}\n"
                         f"open(BLOCKER, 'w').write('x')\n"
                         f"assert {FIXED[1]!r} in 'text'\n"
                         f"for phrase in ('a', {FIXED[2]!r}):\n"
                         f"    assert phrase in 'text'\n")
            self.assertEqual([f"{os.path.relpath(source, ROOT)}:1"],
                             fixed_path_uses(source))


if __name__ == "__main__":
    unittest.main()
