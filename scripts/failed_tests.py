#!/usr/bin/env python3
"""The failing test ids in a failed run's saved log (DRE-6870, stdlib only).

A test that failed on main and passed on the retry was forgotten each time.
Nothing read a failing test's name: the repair reads the log for machine
trouble and a timed-out step, through `reviewer_environment.message_lines`,
and that filter throws away every line a test tool wrote about a test — the
`FAILED …` lines included. So the same flaky test turned main red again and
again before anyone fixed it: agent-bureau's review-snapshot clock test four
times in three days (2026-10-06 to 10-08), fixed only on the fourth.

`failed_tests(log_text)` is the read that was missing. It answers the failing
test ids in log order, no repeats, from two shapes and nothing else:

  * pytest, in both repos — a line that opens `FAILED <path>.py::<name>`. The
    id runs up to the ` - ` pytest writes before the failure's message, so a
    hyphen inside a path stays part of it. Inside a parametrized id's brackets
    even a spaced ` - ` (`test_a[a - b]`) is the id's own, so two cases that
    differ only after it stay two ids.
  * vitest, agent-bureau's web suite — a line that, its color codes gone,
    opens `FAIL <file> > <suite> > <test>`. The id is the whole
    `file > suite > test` text. A `FAIL <file>` line with no `>` is a file
    that failed to load, and names no test.

Every other test tool (jest, the browser page-hold job, the integration
harness's own scenario output) records nothing, and a log with no line of
either shape is handled exactly as it was before.

`repair_card.py open --log-file` writes what this answers onto the repair
card, which is the whole record: `repair_card.settle` reads it back off the
cards when main goes green, and the second time a test turns up there it files
a card to fix that test.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reviewer_environment  # noqa: E402

#: `job\tstep\t<ISO timestamp> ` — the prefix every saved log line opens with.
#: The filter's own pattern, never a second copy of it.
_LOG_PREFIX = reviewer_environment._LOG_PREFIX

#: A terminal color or style code (`ESC[41m`, `ESC[22m`).
_COLOR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: pytest's summary line. The id must look like a test id, so `FAILED to fetch
#: the artifact - 502` is not one. Its `path.py::` head is the filter's own,
#: never a second copy of it.
_PYTEST = re.compile(
    r"^FAILED\s+(?P<rest>" + reviewer_environment.PYTEST_ID_HEAD + r"\S.*)$")

#: vitest's failure line: a file path, then at least one ` > `.
_VITEST = re.compile(r"^FAIL\s+(?P<id>\S+\s+>\s+.+)$")

#: An id longer than this is cut to it — a parametrized id can carry a whole
#: log line in its brackets, and the id is written onto a card.
MAX_ID_CHARS = 300

#: At most this many ids from one log. A run that failed more tests than this
#: failed for a reason no single test names.
MAX_IDS = 20


def _pytest_id(rest: str) -> str:
    """`<path>.py::<name>[…]` off the front of pytest's `<id> - <message>`.

    The message starts at the first ` - ` outside every `[…]`, so a spaced
    hyphen in a parameter is part of the id. A parameter holding a lone `[`
    leaves the brackets unbalanced, and there the id ends at the first ` - `
    after a `]` — where pytest closed it — or, with no `]` at all, the first.
    """
    depth = 0
    for at, char in enumerate(rest):
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(depth - 1, 0)
        elif depth == 0 and rest.startswith(" - ", at):
            return rest[:at].strip()
    if depth == 0:
        return rest.strip()
    closed = rest.find("] - ")
    if closed != -1:
        return rest[:closed + 1].strip()
    return rest.split(" - ", 1)[0].strip()


def _test_id(line: str) -> str | None:
    """The test id one log line names, or None."""
    found = _PYTEST.match(line)
    if found:
        return _pytest_id(found.group("rest"))
    found = _VITEST.match(line)
    if found:
        return found.group("id").strip()
    return None


def failed_tests(log_text: str | None) -> list[str]:
    """Every failing test id in the log, in log order, no repeats."""
    out: list[str] = []
    for raw in (log_text or "").splitlines():
        line = _COLOR.sub("", _LOG_PREFIX.sub("", raw, count=1)).strip()
        test_id = _test_id(line)
        if not test_id:
            continue
        test_id = test_id[:MAX_ID_CHARS]
        if test_id in out:
            continue
        out.append(test_id)
        if len(out) == MAX_IDS:
            break
    return out


if __name__ == "__main__":
    for test_id in failed_tests(sys.stdin.read()):
        print(test_id)
