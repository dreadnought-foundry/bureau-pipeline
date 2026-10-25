"""One lock for the tests that use a workflow's fixed /tmp paths (DRE-7025).

A few tests run a workflow's shell as written, so they read and write the
fixed paths it names — `/tmp/qa-verdict.md`, `/tmp/qa-comment.md`,
`/tmp/verify-verdict.md`, `/tmp/verify-comment.md` — and delete them after.
Two copies of the suite on one machine share /tmp: the suite-green job
(DRE-6865) runs one per case side by side, and on main a3042237 one copy
deleted another's `/tmp/qa-comment.md` before that copy read it.

`hold(test)` takes a lock every copy on the machine shares and gives it back
once the test's cleanups are done, so those tests take turns. Call it first in
`setUp`, before anything touches the fixed paths: cleanups run last-in
first-out, so the release then runs after every other cleanup and after
`tearDown`. `tests/test_fixed_tmp_paths_take_turns.py` names every class that
holds it.
"""

from __future__ import annotations

import fcntl
import os
import unittest

#: Beside the files it guards, so every copy on the machine finds the same one.
LOCK = os.path.join("/tmp", "bureau-pipeline-fixed-tmp.lock")  # nosec B108


def hold(test: unittest.TestCase) -> None:
    """Wait for the machine's lock, and give it back when `test` cleans up."""
    fd = os.open(LOCK, os.O_RDONLY | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    # Closing the descriptor drops the lock; a subprocess never inherits it.
    test.addCleanup(os.close, fd)
