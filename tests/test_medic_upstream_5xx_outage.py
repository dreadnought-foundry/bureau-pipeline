"""Regression pin (DRE-2488): a GitHub-wide 5xx is an UPSTREAM OUTAGE, not a
pipeline failure — the medic backs off and its own run stays green.

Origin (2026-08-17): portico's scheduled Reconcile sweeps died on
`HTTP 503: No server is currently available to service your request` straight
from api.github.com (run 32054736831). The medic retried into the same outage,
the diagnosis agent fired, and the operator got 10 red-run emails (5 reconcile +
5 medic) for something nobody in the fleet can fix.

Same shape as DRE-1921's critic rate-limit class, one class along: classify
FIRST, then back off. On `upstream_5xx` the medic dispatches no diagnosis agent,
fires no immediate retry (retrying into an outage makes it worse; scheduled
workflows like Reconcile re-run themselves), emits one `::notice::` naming the
outage, and ends the job green so no red email goes out.

The match is anchored to the API-error LINE SHAPE — a line that names
api.github.com and carries a 502/503/504, or the gh CLI's own
`gh: … (HTTP 503)` error line. Prose that merely mentions "503" (a diff hunk, a
card body quoted into an agent log — including THIS card's own text) must not
classify, or a genuine failure would be silently swallowed. That negative case
is pinned below with a fixture of exactly that shape.
"""

import os
import sys
import unittest

import yaml

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import medic_classify  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "medic.yml"
)

# The 2026-08-17 reconcile failure, verbatim (tests/fixtures/*.log).
INCIDENT_LOG = os.path.join(FIXTURES, "reconcile-github-503-2026-08-17.log")
NON_200_LOG = os.path.join(FIXTURES, "reconcile-github-503-non-200-2026-08-17.log")
# An agent-task log that merely QUOTES 503s (card body + a diff hunk).
QUOTED_503_LOG = os.path.join(FIXTURES, "agent-task-card-quotes-503.log")


def _fixture(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Classifier: the upstream_5xx class ───────────────────────────────────────
class UpstreamOutageClassifierTest(unittest.TestCase):
    def test_incident_log_classifies_as_upstream_5xx(self):
        log = _fixture(INCIDENT_LOG)
        # The fixture carries the incident's phrasing verbatim.
        self.assertIn("HTTP 503: No server is currently available", log)
        self.assertTrue(medic_classify.is_upstream_5xx(log))
        self.assertEqual(
            medic_classify.classify("Reconcile (reusable)", log), "upstream_5xx"
        )

    def test_non_200_status_code_phrasing_classifies(self):
        log = _fixture(NON_200_LOG)
        self.assertIn("non-200 OK status code: 503", log)
        self.assertEqual(
            medic_classify.classify("Reconcile (reusable)", log), "upstream_5xx"
        )

    def test_gh_cli_api_error_line_classifies(self):
        # `gh api`'s own error shape: "gh: <message> (HTTP 503)".
        log = (
            "sweep\tSweep\t2026-08-17T03:31:02.4712345Z "
            "gh: No server is currently available to service your request. (HTTP 503)\n"
        )
        self.assertTrue(medic_classify.is_upstream_5xx(log))

    def test_502_and_504_also_classify(self):
        for code in ("502", "504"):
            log = (
                "sweep\tSweep\t2026-08-17T03:40:00.0000000Z failed to get runs: "
                f"HTTP {code}: Bad gateway "
                "(https://api.github.com/repos/dreadnought-foundry/portico/actions/runs)\n"
            )
            self.assertTrue(medic_classify.is_upstream_5xx(log), code)

    # ── the negative: 500 is DELIBERATELY not an outage ──────────────────────
    def test_http_500_is_not_upstream_5xx(self):
        # 500 is excluded on purpose: it is also what a broken request of OURS
        # returns. Classifying it as an unfixable GitHub outage would back the
        # medic off a real bug of ours and silently swallow it — the exact
        # failure this whole class exists to avoid. Each line below carries the
        # api.github.com host and a genuine 500 error shape, so it WOULD
        # classify if `_UPSTREAM_5XX_SHAPES` were widened (e.g. to `50[0-4]`).
        lines = (
            "sweep\tSweep\t2026-08-17T03:40:00.0000000Z failed to get runs: "
            "HTTP 500: Internal Server Error "
            "(https://api.github.com/repos/dreadnought-foundry/portico/actions/runs)",
            "sweep\tSweep\t2026-08-17T03:40:01.0000000Z non-200 OK status code: "
            "500 Internal Server Error "
            "(https://api.github.com/repos/dreadnought-foundry/portico/issues)",
            "sweep\tSweep\t2026-08-17T03:40:02.0000000Z "
            "gh: Internal Server Error (HTTP 500)",
        )
        for line in lines:
            log = line + "\n"
            self.assertFalse(medic_classify.is_upstream_5xx(log), line)
            self.assertEqual(
                medic_classify.classify("Reconcile (reusable)", log), "normal", line
            )

    # ── the negative: prose that mentions 503 is NOT an outage ───────────────
    def test_quoted_503_in_card_body_or_diff_is_not_upstream_5xx(self):
        log = _fixture(QUOTED_503_LOG)
        # The fixture really does contain both phrasings — as quoted prose.
        self.assertIn("HTTP 503:", log)
        self.assertIn("non-200 OK status code: 503", log)
        self.assertFalse(medic_classify.is_upstream_5xx(log))
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", log), "normal"
        )

    def test_genuine_failure_is_still_normal(self):
        log = "FAILED tests/test_foo.py::test_bar - AssertionError: 3 != 4\n"
        self.assertEqual(
            medic_classify.classify("Agent Task (reusable)", log), "normal"
        )

    # ── the DRE-1921 class keeps its own answer ──────────────────────────────
    def test_critic_infra_crash_still_wins_its_class(self):
        log = "Error: API rate limit exceeded for installation ID 12345678.\n"
        self.assertEqual(
            medic_classify.classify("QA Review (reusable)", log),
            "critic_infra_crash",
        )
        self.assertTrue(medic_classify.is_critic_infra_crash("QA Review (reusable)", log))

    def test_cli_prints_the_class_alongside_infra_crash(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = medic_classify.main(["Reconcile (reusable)", INCIDENT_LOG])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("class=upstream_5xx", out)
        # The DRE-1921 output line is untouched — medic.yml still reads it.
        self.assertIn("infra_crash=false", out)

    def test_cli_prints_normal_class_for_a_real_failure(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            medic_classify.main(["Agent Task (reusable)", QUOTED_503_LOG])
        self.assertIn("class=normal", buf.getvalue())


# ── medic.yml wiring: no diagnosis, no retry, one notice, exit 0 ─────────────
def _medic():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


def _src() -> str:
    with open(WORKFLOW) as f:
        return f.read()


class MedicUpstreamOutageWiringTest(unittest.TestCase):
    def setUp(self):
        self.jobs = _medic()["jobs"]

    def test_classify_job_publishes_the_class(self):
        self.assertEqual(
            self.jobs["classify"]["outputs"].get("class"),
            "${{ steps.c.outputs.class }}",
        )

    def test_no_diagnosis_agent_on_upstream_5xx(self):
        # The only job that dispatches a diagnosis agent must exclude the class.
        for name, job in self.jobs.items():
            uses = [s.get("uses", "") for s in job.get("steps", [])]
            if any("claude-code-action" in u for u in uses):
                self.assertIn(
                    "class != 'upstream_5xx'",
                    job.get("if", ""),
                    f"{name} dispatches an agent without excluding upstream_5xx",
                )

    def test_no_retry_on_upstream_5xx(self):
        # Retrying into an outage deepens it — no job may rerun the failed run.
        for name, job in self.jobs.items():
            body = yaml.safe_dump(job)
            if "gh run rerun" in body:
                self.assertIn(
                    "class != 'upstream_5xx'",
                    job.get("if", ""),
                    f"{name} reruns without excluding upstream_5xx",
                )

    def test_upstream_outage_job_notices_and_exits_green(self):
        job = self.jobs["upstream_outage"]
        self.assertIn("class == 'upstream_5xx'", job["if"])
        self.assertEqual(job.get("needs"), "classify")
        body = yaml.safe_dump(job)
        self.assertIn("::notice::", body)
        # No rerun, no agent, and nothing that would fail the job (a red medic
        # run is exactly the operator email this card removes).
        self.assertNotIn("gh run rerun", body)
        self.assertNotIn("claude-code-action", body)
        self.assertNotIn("exit 1", body)

    def test_classify_keeps_gh_stderr_so_an_outage_is_visible(self):
        # During a GitHub outage the medic's OWN `gh run view --log-failed`
        # is the call most likely to 503 — and gh reports that on stderr. If
        # the step discarded stderr (and truncated the file on failure), the
        # classifier would see an empty log, answer `normal`, and the medic
        # would retry + diagnose straight into the outage. So stderr must land
        # in the log the classifier reads.
        step = next(
            s
            for s in self.jobs["classify"]["steps"]
            if s.get("id") == "c"
        )
        self.assertIn("2>&1", step["run"])
        self.assertNotIn("2>/dev/null", step["run"])

    def test_gate_expressions_read_the_classify_output(self):
        src = _src()
        self.assertIn("needs.classify.outputs.class", src)


if __name__ == "__main__":
    unittest.main()
