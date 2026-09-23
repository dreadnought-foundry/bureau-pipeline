"""A step that times out on EVERY commit is the code, not the weather (DRE-4674).

Portico's `main` failed `infra — typecheck & test` on four commits in a row from
2026-09-22 19:40 PT, each retried once, every time on
`##[error]The action 'Test' has timed out after 12 minutes`. The suite had
outgrown the step's clock — a deterministic, fixable cause — and
`red_main_repair.decide` read every one of those runs as infrastructure and
started nothing. Main sat red ~12 hours and surfaced only when a person asked.

A timeout can be infrastructure once. The SAME step timing out again is the
code, and these tests pin the memory that tells the two apart:

  * the current run timed out on a step, and that same step — same workflow
    file path, same job name, same step name, compared exactly — timed out on
    the previous ATTEMPT of this run or on one of the 3 prior main runs
    ⇒ the decision is `repair`, and it carries the job, the step, the limit
    and the commits it failed on;
  * a different step, a different job, or a different workflow is NOT a repeat;
  * a lone timeout with nothing behind it stays `infra-backoff` — the "once"
    half of the rule;
  * a missing, marker-filled or malformed history file reads as "no history",
    never as "repeated": the fetch failing must land on today's behaviour.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import red_main_repair  # noqa: E402

SHA = "a" * 40
PRIOR_SHA = "b" * 40
OTHER_SHA = "c" * 40

JOB = "infra — typecheck & test"
STEP = "Test"
WF_PATH = ".github/workflows/ci.yml"

#: `gh run view --log-failed` prefixes every line with the job name and the
#: step name, tab separated — which is what makes a job's own clock readable
#: out of a run-wide dump.
def _log(job=JOB, step=STEP, minutes=12):
    return (
        f"{job}\t{step}\t2026-09-22T19:52:06.0916773Z ##[group]Run make test\n"
        f"{job}\t{step}\t2026-09-22T20:04:07.1120041Z "
        f"##[error]The action '{step}' has timed out after {minutes} minutes\n"
    )


#: A perfectly ordinary red suite — no clock anywhere in it.
ASSERTION_LOG = """
=== FAILURES ===
____ test_widget_count ____
>       assert count_widgets() == 3
E       assert 4 == 3
=== 1 failed, 41 passed ===
"""


def _run(*, head_sha, job=JOB, step=STEP, path=WF_PATH, log=None,
         job_conclusion="failure", step_conclusion="failure", attempt=1):
    """One run in the history document, in the shape the gather step writes."""
    return {
        "run_id": 42,
        "run_attempt": attempt,
        "workflow_path": path,
        "head_sha": head_sha,
        "log": _log(job=job, step=step) if log is None else log,
        "jobs": [{
            "name": job,
            "conclusion": job_conclusion,
            "steps": [{"name": step, "conclusion": step_conclusion}],
        }],
    }


def _history(current, prior=()):
    return {"current": current, "prior": list(prior)}


def _decide(**overrides):
    kwargs = dict(
        conclusion="failure",
        head_branch="main",
        default_branch="main",
        head_sha=SHA,
        log_text=_log(),
        refs=[],
        pulls=[],
    )
    kwargs.update(overrides)
    return red_main_repair.decide(**kwargs)


def _pull(head_ref, state="open", merged=False):
    return {"head_ref": head_ref, "state": state, "merged": merged}


class RepeatedTimeoutIsCodeTest(unittest.TestCase):
    def test_the_same_step_on_a_prior_main_run_is_a_repair(self):
        # THE PORTICO CASE. Same workflow, same job, same step, one commit
        # earlier: the clock is not the weather, it is the suite.
        d = _decide(history=_history(
            _run(head_sha=SHA), [_run(head_sha=PRIOR_SHA)]))
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "repair")
        self.assertEqual(d["branch"], f"repair/{SHA}")
        self.assertFalse(d["escalate"])

    def test_the_previous_attempt_of_this_run_counts(self):
        # Portico retried each commit once and the retry timed out identically.
        # The previous attempt is the same commit, so the repeat is inside one
        # run — and it counts.
        d = _decide(history=_history(
            _run(head_sha=SHA, attempt=2),
            [_run(head_sha=SHA, attempt=1)],
        ))
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "repair")

    def test_a_job_github_itself_calls_timed_out_needs_no_log(self):
        # The other half of the definition: when GitHub concludes the JOB
        # `timed_out`, the clock is stated in the API and no log line is owed.
        d = _decide(
            log_text="",
            history=_history(
                _run(head_sha=SHA, log="", job_conclusion="timed_out",
                     step_conclusion="cancelled"),
                [_run(head_sha=PRIOR_SHA, log="", job_conclusion="timed_out",
                      step_conclusion="cancelled")],
            ),
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "repair")


class NotARepeatTest(unittest.TestCase):
    """"The same step" is exact — three fields, all of them."""

    def test_a_different_step_name_is_not_a_repeat(self):
        d = _decide(history=_history(
            _run(head_sha=SHA),
            [_run(head_sha=PRIOR_SHA, step="Lint")],
        ))
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_a_different_job_name_is_not_a_repeat(self):
        d = _decide(history=_history(
            _run(head_sha=SHA),
            [_run(head_sha=PRIOR_SHA, job="web — unit")],
        ))
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_a_different_workflow_file_is_not_a_repeat(self):
        # Two workflows can both own a job called "test"; the file path is
        # what makes the triple an identity rather than a coincidence.
        d = _decide(history=_history(
            _run(head_sha=SHA),
            [_run(head_sha=PRIOR_SHA, path=".github/workflows/harness.yml")],
        ))
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_another_jobs_clock_is_not_this_jobs_clock(self):
        # One `--log-failed` dump carries every failed job's lines. The prior
        # run's "web — unit" blew its clock while its "infra" job failed on an
        # assertion — attributing one job's timeout to the other's step is how
        # a repeat gets invented out of nothing.
        prior = _run(head_sha=PRIOR_SHA, log=_log(job="web — unit", step=STEP))
        prior["jobs"].append({
            "name": "web — unit", "conclusion": "failure",
            "steps": [{"name": "Vitest", "conclusion": "failure"}],
        })
        d = _decide(history=_history(_run(head_sha=SHA), [prior]))
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_a_single_isolated_timeout_still_backs_off(self):
        # The "once" half of the rule: nothing behind it, so it is still
        # allowed to have been the weather.
        d = _decide(history=_history(_run(head_sha=SHA), []))
        self.assertFalse(d["go"])
        self.assertFalse(d["escalate"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_no_history_at_all_backs_off(self):
        # What the gather step's failure marker lands on, and what a caller
        # pinned to an older reusable workflow gets: today's behaviour.
        d = _decide(history=None)
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_a_history_of_the_wrong_shape_is_no_history(self):
        for junk in ([], "FETCH-FAILED", {"current": "nonsense"},
                     {"current": _run(head_sha=SHA), "prior": "nope"}):
            d = _decide(history=junk)
            self.assertFalse(d["go"], junk)
            self.assertEqual(d["reason"], "infra-backoff", junk)

    def test_an_ordinary_red_suite_is_unaffected_by_history(self):
        # The history only ever ADDS a repair; a plain assertion failure
        # dispatches exactly as it always did.
        d = _decide(
            log_text=ASSERTION_LOG,
            history=_history(
                _run(head_sha=SHA, log=ASSERTION_LOG),
                [_run(head_sha=PRIOR_SHA)],
            ),
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")


class QuotingTheRunnersWordsTest(unittest.TestCase):
    """The DRE-3076 gap, one marker later.

    The harness's block receipt had to be scoped to the harness because this
    repo's own unit suite carries it in FIXTURES, and a red unit suite is
    exactly the failure a fix agent exists for. The timeout line is now in
    fixtures too — in this very file — so the same trap is open, and the same
    kind of scoping closes it: `gh run view --log-failed` says which step
    printed each line, and a genuine timeout line is printed by the step it
    names.
    """

    #: This file's own fixture, as a red `Pipeline Tests` run would print it:
    #: the failing step is "Unit tests" and the line it prints names "Test".
    QUOTED = (
        "scripts unit tests\tUnit tests\t2026-09-23T10:00:00.0Z "
        "E       AssertionError: 'infra — typecheck & test\tTest\t"
        "##[error]The action 'Test' has timed out after 12 minutes' != ''\n"
    )

    def test_a_red_suite_quoting_the_runners_words_is_not_a_timeout(self):
        self.assertFalse(red_main_repair.is_step_timeout(self.QUOTED))

    def test_and_it_still_dispatches_a_repair_agent(self):
        d = _decide(log_text=self.QUOTED, history=_history(
            _run(head_sha=SHA, log=self.QUOTED,
                 job="scripts unit tests", step="Unit tests"),
            [_run(head_sha=PRIOR_SHA, log=self.QUOTED,
                  job="scripts unit tests", step="Unit tests")],
        ))
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")

    def test_the_step_that_printed_it_is_the_step_it_names(self):
        self.assertEqual(
            red_main_repair.timed_out_actions(_log()),
            {(JOB, STEP): "12 minutes"},
        )

    def test_an_unattributable_log_is_taken_at_face_value(self):
        # No job/step prefixing to check against — a log gathered in some
        # other shape is read as the card's rule reads it.
        bare = "##[error]The action 'Test' has timed out after 12 minutes.\n"
        self.assertTrue(red_main_repair.is_step_timeout(bare))
        self.assertEqual(
            red_main_repair.timed_out_actions(bare), {("", STEP): "12 minutes"})


class WhatTheDecisionCarriesTest(unittest.TestCase):
    """The fix agent is sent at a named step with a named clock, or the
    escalation names them — otherwise the repair starts by re-deriving what
    the decision already knew."""

    def test_a_repair_carries_the_job_step_limit_and_commits(self):
        d = _decide(history=_history(
            _run(head_sha=SHA), [_run(head_sha=PRIOR_SHA)]))
        self.assertEqual(d["timeout_job"], JOB)
        self.assertEqual(d["timeout_step"], STEP)
        self.assertEqual(d["timeout_limit"], "12 minutes")
        self.assertIn(SHA, d["timeout_commits"])
        self.assertIn(PRIOR_SHA, d["timeout_commits"])

    def test_the_escalation_carries_them_too(self):
        # Two attempts spent on a repeated timeout: the human who picks this
        # up must be told which step and which clock, not just "main is red".
        d = _decide(
            refs=[f"repair/{SHA}", f"repair/{SHA}-2"],
            pulls=[
                _pull(f"repair/{SHA}", state="closed", merged=False),
                _pull(f"repair/{SHA}-2", state="closed", merged=False),
            ],
            history=_history(_run(head_sha=SHA), [_run(head_sha=PRIOR_SHA)]),
        )
        self.assertTrue(d["escalate"])
        self.assertEqual(d["reason"], "budget-exhausted")
        self.assertEqual(d["timeout_job"], JOB)
        self.assertEqual(d["timeout_step"], STEP)
        self.assertEqual(d["timeout_limit"], "12 minutes")
        self.assertIn(PRIOR_SHA, d["timeout_commits"])

    def test_every_decision_answers_the_keys(self):
        # The workflow reads these outputs unconditionally, so a decision that
        # omitted one would interpolate the literal string "null" into a card.
        for decision in (_decide(conclusion="success"), _decide(), _decide(
                log_text=ASSERTION_LOG)):
            for key in ("timeout_job", "timeout_step", "timeout_limit",
                        "timeout_commits"):
                self.assertIn(key, decision)
                self.assertIsInstance(decision[key], str)


class OutputsTest(unittest.TestCase):
    def test_the_timeout_detail_reaches_github_output(self):
        d = _decide(history=_history(
            _run(head_sha=SHA), [_run(head_sha=PRIOR_SHA)]))
        rendered = red_main_repair.outputs(d)
        self.assertIn(f"timeout_job={JOB}", rendered)
        self.assertIn(f"timeout_step={STEP}", rendered)
        self.assertIn("timeout_limit=12 minutes", rendered)
        self.assertIn("timeout_commits=", rendered)
        self.assertIn("reason=repair", rendered)


class CliHistoryFileTest(unittest.TestCase):
    """`--history-file` is the workflow's half of the contract."""

    def _cli(self, history_arg, *, log=None):
        with tempfile.TemporaryDirectory() as td:
            logf = os.path.join(td, "log.txt")
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            open(logf, "w").write(_log() if log is None else log)
            open(refs, "w").write("[]")
            open(pulls, "w").write("[]")
            argv = [
                sys.executable,
                os.path.join(SCRIPTS, "red_main_repair.py"), "decide",
                "--conclusion", "failure",
                "--head-branch", "main",
                "--default-branch", "main",
                "--head-sha", SHA,
                "--log-file", logf,
                "--refs-file", refs,
                "--pulls-file", pulls,
            ]
            if history_arg is not None:
                argv += ["--history-file", history_arg(td)]
            res = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return dict(
            line.split("=", 1)
            for line in res.stdout.strip().splitlines() if "=" in line
        )

    def _write(self, payload):
        def _make(td):
            path = os.path.join(td, "repair-history.json")
            with open(path, "w") as f:
                f.write(payload)
            return path
        return _make

    def test_a_repeated_timeout_reaches_the_decision_through_the_file(self):
        out = self._cli(self._write(json.dumps(_history(
            _run(head_sha=SHA), [_run(head_sha=PRIOR_SHA)]))))
        self.assertEqual(out["go"], "true")
        self.assertEqual(out["reason"], "repair")
        self.assertEqual(out["timeout_step"], STEP)
        self.assertEqual(out["timeout_limit"], "12 minutes")
        self.assertIn(PRIOR_SHA, out["timeout_commits"])

    def test_a_missing_history_file_falls_back_to_infra_backoff(self):
        out = self._cli(lambda td: os.path.join(td, "does-not-exist.json"))
        self.assertEqual(out["go"], "false")
        self.assertEqual(out["reason"], "infra-backoff")

    def test_the_gather_steps_failure_marker_is_no_history(self):
        out = self._cli(self._write("FETCH-FAILED"))
        self.assertEqual(out["go"], "false")
        self.assertEqual(out["reason"], "infra-backoff")

    def test_the_flag_is_optional(self):
        # A caller pinned to an older reusable workflow passes no history at
        # all, and must keep deciding exactly as it does today.
        out = self._cli(None, log=ASSERTION_LOG)
        self.assertEqual(out["reason"], "dispatch")


if __name__ == "__main__":
    unittest.main()
