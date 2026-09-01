"""Regression pin (DRE-2923): a rate-limited sweep fails DISTINGUISHABLY —
named as a quota condition, and recognised by the medic as something to wait
out rather than escalate as a defect in the estate.

Origin (2026-09-01): four consecutive `reconcile.yml` runs in agent-bureau
died on `urllib.error.HTTPError: HTTP Error 400: Bad Request`. The cause was
the workspace's 2500/hour quota, exhausted by read-only diagnostics minutes
earlier — a transient, self-healing condition needing no code change at all.
Every one of those runs was red, medic fired, and nobody could tell from the
artifact whether the estate was broken or merely busy.

tests/test_linear_error_body.py pins the client half (the body is captured and
the condition is named). This file pins what happens to the RUN:

  * the sweep exits non-zero with a DISTINCT code and one named line, not a
    urllib traceback — a red run is still a red run, so a board that has
    stopped being reconciled never goes quiet;
  * the medic classifies it `linear_ratelimited` and BACKS OFF — no rerun (the
    quota is still exhausted; re-running deepens it, the DRE-1921 loop), no
    diagnosis agent (there is nothing to diagnose and it would burn more
    quota), one ::notice::, and the medic's own run ends green so the failure
    is not reported as a defect.

Exactly the shape DRE-2488 gave an upstream GitHub 5xx, one class along.

The match is line-anchored, for the same reason it is there: this card's own
body quotes the RATELIMITED payload, so an agent-task log that merely QUOTES
it must not classify — or a genuine failure would be silently swallowed. That
negative is pinned below with a fixture of exactly that shape.
"""

import contextlib
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

import yaml

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import linear_ops  # noqa: E402
import medic_classify  # noqa: E402
import reconcile  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "medic.yml"
)

# The 2026-09-01 reconcile failure as it reads AFTER this card.
INCIDENT_LOG = os.path.join(FIXTURES, "reconcile-linear-ratelimited-2026-09-01.log")
# An agent-task log that merely QUOTES the RATELIMITED body (this card's text).
QUOTED_LOG = os.path.join(FIXTURES, "agent-task-card-quotes-ratelimited.log")


def _fixture(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── the sweep: one named condition, a distinct exit ─────────────────────────
class RateLimitedSweepTest(unittest.TestCase):
    def test_sweep_exits_with_the_named_condition_not_a_traceback(self):
        """A quota exhaustion escaping the sweep is caught at the CLI edge and
        reported as itself. The traceback that ended inside urllib is what made
        four red runs unattributable."""
        boom = linear_ops.LinearRateLimited(
            f"Linear API returned 400 from {linear_ops.API}: "
            "rate limited: 2500 requests/hour exhausted — body: '...'"
        )
        err = io.StringIO()
        with mock.patch.object(reconcile, "main", side_effect=boom):
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as caught:
                    reconcile.run([])
        self.assertEqual(caught.exception.code, reconcile.RATE_LIMITED_EXIT)
        out = err.getvalue()
        self.assertIn("rate limited: 2500 requests/hour exhausted", out)
        # Distinguishable: the log says what it is and what to do about it.
        self.assertIn("not a defect", out.lower())

    def test_the_distinct_exit_code_is_non_zero_and_not_the_generic_one(self):
        """Non-zero, so a board that stopped being reconciled still says so;
        distinct, so it is not the generic failure every other cause exits."""
        self.assertNotEqual(reconcile.RATE_LIMITED_EXIT, 0)
        self.assertNotEqual(reconcile.RATE_LIMITED_EXIT, 1)

    def test_an_ordinary_linear_error_is_not_swallowed_by_the_rate_limit_path(self):
        """Only the quota condition gets the quiet treatment — a real Linear
        failure must keep its ordinary loud path."""
        with mock.patch.object(
            reconcile, "main", side_effect=linear_ops.LinearError("linear error: boom")
        ):
            with self.assertRaises(linear_ops.LinearError):
                reconcile.run([])

    def test_run_threads_the_cli_flags_through(self):
        with mock.patch.object(reconcile, "main") as main:
            reconcile.run(["--promote-only"])
        main.assert_called_once_with(
            promote_only=True, conflicts_only=False, close_only=False
        )


# ── the classifier: the linear_ratelimited class ────────────────────────────
class LinearRateLimitClassifierTest(unittest.TestCase):
    def test_incident_log_classifies_as_linear_ratelimited(self):
        log = _fixture(INCIDENT_LOG)
        self.assertIn("rate limited: 2500 requests/hour exhausted", log)
        self.assertTrue(medic_classify.is_linear_rate_limited(log))
        self.assertEqual(
            medic_classify.classify("Reconcile (reusable)", log), "linear_ratelimited"
        )

    def test_a_raw_ratelimited_body_on_the_api_line_classifies(self):
        log = (
            "sweep\tSweep\t2026-09-01T04:40:54.0000000Z linear_ops.LinearRateLimited: "
            "Linear API returned 400 from https://api.linear.app/graphql: "
            '\'{"errors":[{"extensions":{"code":"RATELIMITED"}}]}\'\n'
        )
        self.assertTrue(medic_classify.is_linear_rate_limited(log))

    def test_the_anchor_is_the_message_the_client_actually_composes(self):
        """The two halves must not drift apart, so the line under test is
        produced by the REAL client rather than restated here.

        This is also what covers the sweep's GUARDED paths: a quota exhaustion
        caught by one of reconcile's per-card `except LinearError` guards is
        recorded as an ordinary read failure and the run exits 1, not 75 — the
        medic still classifies it, because the anchor is the log line the
        client wrote, never the exit code.
        """
        body = (
            b'{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are '
            b'allowed per 1 hour and you have made 2500 requests in the last '
            b'hour.","extensions":{"type":"ratelimited","code":"RATELIMITED",'
            b'"statusCode":429,"userError":true}}]}'
        )

        def _boom(*_a, **_k):
            raise urllib.error.HTTPError(
                linear_ops.API, 400, "Bad Request", {}, io.BytesIO(body)
            )

        with mock.patch.object(linear_ops.urllib.request, "urlopen", _boom):
            with self.assertRaises(linear_ops.LinearRateLimited) as caught:
                linear_ops.gql("query { issues { nodes { id } } }")
        guarded = f"ERROR: escalate_aged_intake: {caught.exception}\n"
        self.assertTrue(medic_classify.is_linear_rate_limited(guarded))

    def test_a_body_truncated_past_the_extension_code_still_classifies(self):
        """The condition is composed in FRONT of the body for this reason: a
        long `message` pushes `code":"RATELIMITED"` past the 500-char cut, and
        the class must not depend on where the truncation landed."""
        body = (
            b'{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are '
            b'allowed per 1 hour. ' + b"padding. " * 200 + b'","extensions":'
            b'{"code":"RATELIMITED"}}]}'
        )

        def _boom(*_a, **_k):
            raise urllib.error.HTTPError(
                linear_ops.API, 400, "Bad Request", {}, io.BytesIO(body)
            )

        with mock.patch.object(linear_ops.urllib.request, "urlopen", _boom):
            with self.assertRaises(linear_ops.LinearRateLimited) as caught:
                linear_ops.gql("query { issues { nodes { id } } }")
        message = str(caught.exception)
        self.assertNotIn("RATELIMITED", message)  # the cut really did land there
        self.assertTrue(medic_classify.is_linear_rate_limited(message + "\n"))

    # ── the negative: prose quoting the payload is NOT a rate limit ─────────
    def test_quoted_ratelimited_body_in_a_card_is_not_a_rate_limit(self):
        log = _fixture(QUOTED_LOG)
        # The fixture really does carry the payload — as quoted card prose.
        self.assertIn("RATELIMITED", log)
        self.assertIn("Only 2500 requests", log)
        self.assertFalse(medic_classify.is_linear_rate_limited(log))
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", log), "normal"
        )

    def test_a_genuine_failure_is_still_normal(self):
        log = "FAILED tests/test_foo.py::test_bar - AssertionError: 3 != 4\n"
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", log), "normal"
        )

    def test_the_other_two_classes_keep_their_answers(self):
        critic = "Error: API rate limit exceeded for installation ID 12345678.\n"
        self.assertEqual(
            medic_classify.classify("QA Review (reusable)", critic),
            "critic_infra_crash",
        )
        outage = (
            "sweep\tSweep\t2026-08-17T03:31:02.0000000Z failed to get runs: "
            "HTTP 503: No server is currently available to service your request. "
            "(https://api.github.com/repos/dreadnought-foundry/portico/actions/runs)\n"
        )
        self.assertEqual(
            medic_classify.classify("Reconcile (reusable)", outage), "upstream_5xx"
        )

    def test_cli_prints_the_class(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = medic_classify.main(["Reconcile (reusable)", INCIDENT_LOG])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("class=linear_ratelimited", out)
        # The DRE-1921 output line is untouched — medic.yml still reads it.
        self.assertIn("infra_crash=false", out)


# ── medic.yml wiring: no diagnosis, no retry, one notice, exit 0 ────────────
def _medic():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


class MedicLinearRateLimitWiringTest(unittest.TestCase):
    def setUp(self):
        self.jobs = _medic()["jobs"]

    def test_no_diagnosis_agent_on_a_rate_limit(self):
        for name, job in self.jobs.items():
            uses = [s.get("uses", "") for s in job.get("steps", [])]
            if any("claude-code-action" in u for u in uses):
                self.assertIn(
                    "class != 'linear_ratelimited'",
                    job.get("if", ""),
                    f"{name} dispatches an agent without excluding a rate limit",
                )

    def test_no_retry_on_a_rate_limit(self):
        # Re-running into an exhausted quota deepens it — the DRE-1921 loop.
        for name, job in self.jobs.items():
            body = yaml.safe_dump(job)
            if "gh run rerun" in body:
                self.assertIn(
                    "class != 'linear_ratelimited'",
                    job.get("if", ""),
                    f"{name} reruns without excluding a rate limit",
                )

    def test_rate_limit_job_notices_and_ends_green(self):
        job = self.jobs["linear_rate_limited"]
        self.assertIn("class == 'linear_ratelimited'", job["if"])
        self.assertEqual(job.get("needs"), "classify")
        body = yaml.safe_dump(job)
        self.assertIn("::notice::", body)
        self.assertNotIn("gh run rerun", body)
        self.assertNotIn("claude-code-action", body)
        self.assertNotIn("exit 1", body)


if __name__ == "__main__":
    unittest.main()
