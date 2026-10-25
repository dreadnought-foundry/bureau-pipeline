"""RED-first tests: a dropped connection is retried like any transient fault (DRE-7232).

Harness run 38365040174 went red on main at 750c2d38150c. Every leg of
`gate_paths` had already passed — stale, skew and named all logged their
results — when the scenario's last read died with:

    gate_paths: FAIL at verify
      - verify: RemoteDisconnected: Remote end closed connection without response

The client retries a 5xx and a `URLError`, but `urlopen` only wraps a fault
raised while SENDING the request. A fault raised while reading the answer —
`http.client.RemoteDisconnected`, a bare `ConnectionResetError`, a
`TimeoutError` — comes through unwrapped, skipped the retry loop entirely,
and was not even a `GitHubError` for the callers that catch one. These are
the same shapes `linear_ops.is_transient` already retries (DRE-3087).

Run: python3 -m pytest tests/test_harness_dropped_connection.py -v
"""

import http.client
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import github_api  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

REPO = "dreadnought-foundry/bureau-harness"
OPEN_PRS = [{"number": 1, "head": {"ref": "dependabot/pip/pytest-9.1.1"}}]


class Flaky:
    """An opener scripted per request: each step is an exception to raise or
    a payload to answer with. Counts the requests it saw."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, req):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return 200, json.dumps(step).encode(), {}


class DroppedConnectionTest(unittest.TestCase):
    def setUp(self):
        # The client's transient-failure backoff sleeps between attempts.
        patcher = mock.patch.object(github_api.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _gh(self, opener):
        return GitHub("tok", opener=opener, log=lambda _line: None)

    def test_remote_disconnected_on_a_read_is_retried(self):
        """Run 38365040174, replayed: the read's first answer never came."""
        opener = Flaky([
            http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            ),
            OPEN_PRS,
        ])
        self.assertEqual(self._gh(opener).list_open_prs(REPO), OPEN_PRS)
        self.assertEqual(opener.calls, 2)

    def test_a_bare_connection_reset_is_retried(self):
        opener = Flaky([ConnectionResetError("Connection reset by peer"), OPEN_PRS])
        self.assertEqual(self._gh(opener).list_open_prs(REPO), OPEN_PRS)

    def test_a_bare_read_timeout_is_retried(self):
        opener = Flaky([TimeoutError("The read operation timed out"), OPEN_PRS])
        self.assertEqual(self._gh(opener).list_open_prs(REPO), OPEN_PRS)

    def test_a_connection_that_never_answers_raises_github_error(self):
        """Out of retries, the fault is a GitHubError like every other one,
        so the callers that catch GitHubError still hold."""
        opener = Flaky([
            http.client.RemoteDisconnected("closed")
            for _ in range(github_api._RETRIES)
        ])
        with self.assertRaises(GitHubError) as caught:
            self._gh(opener).list_open_prs(REPO)
        self.assertEqual(caught.exception.status, 0)
        self.assertIn("closed", str(caught.exception))
        self.assertEqual(opener.calls, github_api._RETRIES)

    def test_a_programming_error_is_not_retried(self):
        """Only network faults earn a second attempt."""
        opener = Flaky([ValueError("bad request object"), OPEN_PRS])
        with self.assertRaises(ValueError):
            self._gh(opener).list_open_prs(REPO)
        self.assertEqual(opener.calls, 1)


if __name__ == "__main__":
    unittest.main()
