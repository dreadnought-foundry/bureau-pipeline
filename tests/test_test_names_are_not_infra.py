"""A test's name is not an infrastructure failure (DRE-6522).

On 2026-10-09 `main` went red on one plain failing test, and both safety nets
stood down. The red-main repair printed `infra-backoff` and started no agent;
the medic printed `class=linear_ratelimited` and neither retried nor
diagnosed. The text they matched was the NAMES of passing tests: a
parametrized pytest id carries a whole rate-limit line in its brackets, and
`pytest -v` prints it beside `PASSED`. `main` stayed red for 36 minutes with
nothing working on it.

DRE-5272 had already written the filter that drops every line a test runner
wrote about a test (`reviewer_environment.message_lines`). This file pins that
the repair's signatures and the medic's three line readers read through it:

  * the 2026-10-09 log, replayed from a fixture FILE, dispatches the repair and
    classifies `normal` in the medic;
  * the real thing — a step's own rate-limit, Linear quota or GitHub 5xx line —
    still classifies, one test each;
  * for each of the four readers, the same line inside a `PASSED` report, a
    `FAILED … - …` summary and an `E   …` explanation classifies as nothing;
  * there is one test-report pattern in `scripts/`;
  * an `infra-backoff` says which signature matched, and on which line.

No test here is parametrized with a log line, on purpose: its id would be one
more passing-test line carrying the very text these readers must ignore.
"""

import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import medic_classify  # noqa: E402
import red_main_repair  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# Pipeline Tests run 38000926514 on `main` (c0e68f6a7), its failed-step log cut
# to the lines that matter and enough around them to keep the log's shape: six
# passing test ids that carry rate-limit text, and the real `FAILED` summary.
RED_MAIN_LOG = os.path.join(
    FIXTURES, "pipeline-tests-red-main-38000926514.log"
)

SHA = "c" * 40

# How `gh run view --log-failed` attributes a line.
_PREFIX = "scripts unit tests (part 3)\tRun tests\t2026-10-09T22:56:21.7996654Z "


def _fixture() -> str:
    with open(RED_MAIN_LOG, encoding="utf-8") as f:
        return f.read()


def _decide(log_text, workflow_name="Pipeline Tests"):
    return red_main_repair.decide(
        conclusion="failure",
        head_branch="main",
        default_branch="main",
        head_sha=SHA,
        log_text=log_text,
        refs=[],
        pulls=[],
        workflow_name=workflow_name,
    )


# The real thing, one line per reader, each as a step would print it.
GH_RATE_LIMIT = "gh: API rate limit exceeded for installation ID 123. (HTTP 403)"
LINEAR_RATE_LIMIT = (
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql: rate limited: 2500 requests/hour "
    "exhausted — body: '{\"errors\":[{\"extensions\":{\"code\":\"RATELIMITED\"}}]}'"
)
GH_UPSTREAM_503 = (
    "failed to get runs: HTTP 503: No server is currently available to "
    "service your request. (https://api.github.com/repos/o/r/actions/runs)"
)


def _readers():
    """(name, reader, the real line it must still see) for all four."""
    return (
        ("red_main_repair.is_infra_failure",
         lambda log: red_main_repair.is_infra_failure(log, "Pipeline Tests"),
         GH_RATE_LIMIT),
        ("medic_classify.is_critic_infra_crash",
         lambda log: medic_classify.is_critic_infra_crash(
             "QA Review (reusable)", log),
         GH_RATE_LIMIT),
        ("medic_classify.is_linear_rate_limited",
         medic_classify.is_linear_rate_limited,
         LINEAR_RATE_LIMIT),
        ("medic_classify.is_upstream_5xx",
         medic_classify.is_upstream_5xx,
         GH_UPSTREAM_503),
    )


def _reported_by_the_suite(line: str) -> dict:
    """The three ways a test runner prints a line it was handed."""
    return {
        "a passing test id": (
            f"tests/test_some_reader.py::test_it_reads[{line}] PASSED [ 43%]"
        ),
        "a FAILED summary": (
            f"FAILED tests/test_some_reader.py::test_it_reads - "
            f"AssertionError: {line!r} was not read"
        ),
        "an E line": f"E       AssertionError: {line}",
    }


class TheRedMainOf20261009Test(unittest.TestCase):
    """Replayed from the fixture: what both safety nets should have said."""

    def test_the_fixture_is_the_log_that_misled_both(self):
        log = _fixture()
        self.assertIn(
            "FAILED tests/test_planning_escalation_prior_answer.py::"
            "TestTheBlockReadsTheWholeThread::"
            "test_a_retry_whose_note_is_on_the_card_never_reads_it",
            log,
        )
        # The six passing test ids that each matched a signature on the day.
        matched = [
            line for line in log.splitlines()
            if "PASSED" in line and (
                any(s.search(line) for s in red_main_repair.INFRA_SIGNATURES)
                or (medic_classify._LINEAR_API_HOST in line and any(
                    s.search(line)
                    for s in medic_classify._LINEAR_RATELIMIT_SHAPES))
            )
        ]
        self.assertEqual(len(matched), 6, matched)

    def test_the_repair_does_not_read_it_as_infrastructure(self):
        self.assertFalse(
            red_main_repair.is_infra_failure(_fixture(), "Pipeline Tests")
        )

    def test_the_repair_dispatches(self):
        d = _decide(_fixture())
        self.assertEqual(d["reason"], "dispatch")
        self.assertTrue(d["go"])

    def test_the_medic_reads_it_as_a_normal_failure(self):
        self.assertEqual(
            medic_classify.classify("Pipeline Tests", _fixture()), "normal"
        )


class TheRealThingStillClassifiesTest(unittest.TestCase):
    """A line a STEP printed is still the condition it names."""

    def test_a_step_rate_limit_is_infrastructure_to_the_repair(self):
        self.assertTrue(red_main_repair.is_infra_failure(
            _PREFIX + GH_RATE_LIMIT, "Pipeline Tests"))
        self.assertEqual(
            _decide(_PREFIX + GH_RATE_LIMIT)["reason"], "infra-backoff")

    def test_a_linear_client_error_is_linear_ratelimited(self):
        self.assertEqual(
            medic_classify.classify("Reconcile", _PREFIX + LINEAR_RATE_LIMIT),
            "linear_ratelimited",
        )

    def test_a_github_503_is_upstream_5xx(self):
        self.assertEqual(
            medic_classify.classify("Reconcile", _PREFIX + GH_UPSTREAM_503),
            "upstream_5xx",
        )

    def test_a_qa_review_rate_limit_is_a_critic_infra_crash(self):
        self.assertEqual(
            medic_classify.classify(
                "QA Review (reusable)", _PREFIX + GH_RATE_LIMIT),
            "critic_infra_crash",
        )


class WhatTheSuitePrintsAboutItsTestsTest(unittest.TestCase):
    """For each reader: the real line counts, the suite reporting it does not."""

    def test_each_reader_sees_the_real_line(self):
        # Without this, the negatives below could pass on a reader that
        # simply never matches anything.
        for name, reader, line in _readers():
            with self.subTest(reader=name):
                self.assertTrue(reader(line))
                self.assertTrue(reader(_PREFIX + line))

    def test_no_reader_classifies_a_test_report(self):
        for name, reader, line in _readers():
            for shape, report in _reported_by_the_suite(line).items():
                for text in (report, _PREFIX + report):
                    with self.subTest(reader=name, shape=shape,
                                      prefixed=text != report):
                        self.assertFalse(reader(text))

    def test_the_whole_classifier_reads_a_test_report_as_normal(self):
        for workflow, line in (
            ("Pipeline Tests", LINEAR_RATE_LIMIT),
            ("Pipeline Tests", GH_UPSTREAM_503),
            ("QA Review (reusable)", GH_RATE_LIMIT),
        ):
            for shape, report in _reported_by_the_suite(line).items():
                with self.subTest(workflow=workflow, shape=shape):
                    self.assertEqual(
                        medic_classify.classify(workflow, _PREFIX + report),
                        "normal",
                    )

    def test_the_critics_neutral_marker_is_still_read_whole(self):
        # The card changes the signature readers only: the critic saying it
        # could not run is believed wherever the QA Review log carries it.
        report = (f"tests/test_x.py::test_it["
                  f"{medic_classify.CRITIC_NEUTRAL_MARKER}] PASSED [ 1%]")
        self.assertTrue(medic_classify.is_critic_infra_crash(
            "QA Review (reusable)", report))


class OneTestReportPatternTest(unittest.TestCase):
    """The filter is shared, never copied: one pattern, in one module."""

    # The regex fragment that recognises `path.py::name`: a backslash-escaped
    # dot, which only a pattern carries — a comment naming a test does not.
    FRAGMENT = r"\.py::"

    def _string_constants(self, path):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield node.value

    def test_there_is_exactly_one_test_report_pattern_in_scripts(self):
        holders = []
        for root, _dirs, files in os.walk(SCRIPTS):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                if any(self.FRAGMENT in s for s in self._string_constants(path)):
                    holders.append(os.path.relpath(path, SCRIPTS))
        self.assertEqual(holders, ["reviewer_environment.py"])

    def test_the_filter_is_public(self):
        import reviewer_environment

        self.assertTrue(callable(reviewer_environment.message_lines))


class TheBackoffSaysWhyTest(unittest.TestCase):
    """`infra-backoff` names its signature and the line it matched."""

    _SAYS = re.compile(
        r"^repair decide: infra-backoff matched (?P<signature>.+?) "
        r"on the line: (?P<line>.*)$", re.M)

    def _run(self, log_text):
        with tempfile.TemporaryDirectory() as td:
            paths = {}
            for key, body in (("log", log_text), ("refs", "[]"),
                              ("pulls", "[]")):
                paths[key] = os.path.join(td, key)
                with open(paths[key], "w", encoding="utf-8") as f:
                    f.write(body)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = red_main_repair.main([
                    "decide", "--conclusion", "failure",
                    "--head-branch", "main", "--default-branch", "main",
                    "--head-sha", SHA, "--log-file", paths["log"],
                    "--refs-file", paths["refs"],
                    "--pulls-file", paths["pulls"],
                    "--workflow-name", "Pipeline Tests",
                ])
        self.assertEqual(rc, 0)
        return out.getvalue(), err.getvalue()

    def test_the_decision_carries_what_matched(self):
        d = _decide("ok\n" + _PREFIX + GH_RATE_LIMIT + "\nmore")
        self.assertEqual(d["reason"], "infra-backoff")
        self.assertEqual(d["infra_signature"], "api rate limit exceeded")
        self.assertEqual(d["infra_line"], GH_RATE_LIMIT)

    def test_the_step_log_names_the_signature_and_the_line(self):
        _out, err = self._run("ok\n" + _PREFIX + GH_RATE_LIMIT + "\nmore")
        said = self._SAYS.findall(err)
        self.assertEqual(len(said), 1, err)
        signature, line = said[0]
        self.assertEqual(signature, "'api rate limit exceeded'")
        self.assertEqual(line, GH_RATE_LIMIT)

    def test_the_line_is_cut_to_200_characters(self):
        long_line = GH_RATE_LIMIT + " " + "x" * 400
        _out, err = self._run(_PREFIX + long_line)
        said = self._SAYS.findall(err)
        self.assertEqual(len(said), 1, err)
        self.assertEqual(said[0][1], long_line[:200])

    def test_a_dispatch_says_no_such_line(self):
        _out, err = self._run(_fixture())
        self.assertNotIn("infra-backoff", err)
        self.assertEqual(self._SAYS.findall(err), [])

    def test_a_timeout_backoff_names_the_runners_line(self):
        timeout = "##[error]The action 'Test' has timed out after 12 minutes."
        d = _decide("scripts unit tests\tTest\t2026-10-09T22:56:21Z " + timeout)
        self.assertEqual(d["reason"], "infra-backoff")
        self.assertEqual(d["infra_signature"], "step timeout")
        self.assertEqual(d["infra_line"], timeout)

    def test_the_new_keys_are_empty_on_every_other_decision(self):
        d = _decide(_fixture())
        self.assertEqual((d["infra_signature"], d["infra_line"]), ("", ""))
        # And the workflow's outputs are unchanged by them.
        self.assertNotIn("infra_", red_main_repair.outputs(d))
        json.dumps(d)


if __name__ == "__main__":
    unittest.main()
