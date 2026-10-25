"""The tests that use a workflow's fixed /tmp paths take turns (DRE-7025).

A few tests run a workflow's own shell as written, so they read and write the
fixed paths it names — `/tmp/qa-verdict.md`, `/tmp/qa-comment.md`,
`/tmp/verify-verdict.md`, `/tmp/verify-comment.md` — and delete them after.
Two copies of the suite on one machine share /tmp. The suite-green job
(DRE-6865) runs one copy per case side by side, and on main a3042237 one copy
deleted another's `/tmp/qa-comment.md` before that copy read it.

`tests/fixed_tmp.py` holds one lock for the whole machine, at a fixed path
beside the files it guards. Each test class that touches those paths takes it
in `setUp` and gives it back after its cleanups, so the copies take turns.

Run: python3 -m pytest tests/test_fixed_tmp_paths_take_turns.py -v
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS)

import fixed_tmp  # noqa: E402

#: The test classes that read and write the fixed /tmp verdict paths.
WRITERS = (
    "tests/test_post_verdict.py::TheFileRunsAsAFileTest",
    "tests/test_verdict_sha_binding.py::ProducerFormatTest",
    "tests/test_verdict_content_binding.py::ProducerLineTest",
)

#: Exits 0 when it takes the lock without waiting, 1 when somebody holds it.
TRY_LOCK = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o666)\n"
    "try:\n"
    "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    sys.exit(1)\n"
)

#: Exits 0 once it has taken the lock, waiting for whoever holds it.
WAIT_FOR_LOCK = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_CREAT, 0o666)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
)


def _another_process_can_take_it_now() -> bool:
    proc = subprocess.run([sys.executable, "-c", TRY_LOCK, fixed_tmp.LOCK],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def _another_process_takes_it_in_time() -> bool:
    """Another copy of the suite may hold it a moment; it never holds it long."""
    proc = subprocess.run([sys.executable, "-c", WAIT_FOR_LOCK, fixed_tmp.LOCK],
                          capture_output=True, text=True, timeout=120)
    return proc.returncode == 0


class _Probe(unittest.TestCase):
    def runTest(self):
        pass


class TheLockIsTheMachines(unittest.TestCase):

    def test_it_sits_in_tmp_beside_the_files_it_guards(self):
        self.assertEqual(os.path.dirname(fixed_tmp.LOCK), "/tmp")

    def test_while_one_test_holds_it_no_other_process_can_take_it(self):
        probe = _Probe()
        fixed_tmp.hold(probe)
        try:
            self.assertFalse(_another_process_can_take_it_now())
        finally:
            probe.doCleanups()
        self.assertTrue(_another_process_takes_it_in_time())


class EveryWriterHoldsIt(unittest.TestCase):
    """Each writer class holds the lock from its `setUp` until its cleanups
    are done — `tearDown` deletes the files, so it must run under the lock."""

    def test_each_writer_holds_the_lock_through_its_test(self):
        for writer in WRITERS:
            path, name = writer.split("::")
            module = importlib.import_module(os.path.basename(path)[:-3])
            cls = getattr(module, name)
            method = next(m for m in dir(cls) if m.startswith("test_"))
            with self.subTest(writer=writer):
                case = cls(method)
                case.setUp()
                try:
                    self.assertFalse(_another_process_can_take_it_now(),
                                     f"{writer} touches the fixed /tmp paths without the lock")
                finally:
                    case.tearDown()
                    case.doCleanups()
                self.assertTrue(_another_process_takes_it_in_time())


class TwoCopiesRunSideBySide(unittest.TestCase):
    """The acceptance test: the same writer tests, twice, at once."""

    def test_two_processes_running_the_writers_both_pass(self):
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   *WRITERS]
        for _ in range(3):
            copies = [subprocess.Popen(command, cwd=ROOT, text=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                      for _ in range(2)]
            outputs = [copy.communicate(timeout=600)[0] for copy in copies]
            for copy, output in zip(copies, outputs):
                self.assertEqual(0, copy.returncode, output[-4000:])
                self.assertIn(" passed", output)


if __name__ == "__main__":
    unittest.main()
