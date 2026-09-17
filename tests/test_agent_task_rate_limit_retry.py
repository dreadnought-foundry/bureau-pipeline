"""RED-first guard: a build refused by GitHub BEFORE the model starts is retried (DRE-4108).

THE FAILURE (portico, 2026-09-16 — two Agent Task runs in a row). Both died
inside `anthropics/claude-code-action`'s allowed-bots setup, on

    GET /users/agent-bureau-bot-2%5Bbot%5D
    403 API rate limit exceeded for installation ID 123249480

before the model was ever started. The run exited 1 on the first refusal. No
turn was taken, nothing was billed, and the card got a pipeline-failure card
for a shared quota hiccup that would have cleared on its own. The CEO's signed
rule on DRE-4109 says it plainly: a brief refusal from an outside service is a
retry, not a failure.

WHY IT IS A WORKFLOW CHANGE. The failing call is inside the vendor action,
which the fleet pins to a commit and does not patch. So the retry wraps the
step in our own workflow — and it must be narrow enough never to re-run a
build that did real work.

WHAT THIS FILE PINS, and every assertion maps to one of the card's criteria:

  * **the decision** — `scripts/claude_rate_limit_retry.py` retries ONLY the
    signature: the step failed, the model never started, and GitHub is
    refusing this installation right now. A run that reached the model, a
    failure that is not a rate limit, and an exhausted retry budget each stop
    it, and each stops it for a NAMED reason rather than by falling through.
  * **the probe** — the classifier cannot read the failed step's log (GitHub
    publishes a job's log only after the job ends), so it re-issues the call
    the vendor made and reads GitHub's own answer. That is what makes the
    signature observable, and it is why the receipt line can name the call.
  * **the wiring** — agent-task.yml really carries the attempts, the backoff,
    and a resolved result the downstream gate and report read, with the
    vendor's pinned commit unchanged at every attempt.
  * **the outcome** — a refusal that clears leaves a GREEN run (the medic
    watches `Agent Task` for `conclusion == 'failure'`, so green files no
    card); a refusal that persists leaves a RED one.

The shell is EXECUTED, not grepped: the resolve step and the terminal fail
step are run as the workflow wires them, so "the run stays green" means a
process really exited 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"
SCRIPT = ROOT / "scripts" / "claude_rate_limit_retry.py"

sys.path.insert(0, str(ROOT / "scripts"))

import claude_rate_limit_retry as crl  # noqa: E402

# The exact call the two portico runs died on, and the exact body GitHub
# answered with. Both are quoted from the card so the fingerprint under test
# is the one production actually produced.
FAILING_CALL = "/users/agent-bureau-bot-2%5Bbot%5D"
RATE_LIMIT_BODY = json.dumps(
    {
        "message": "API rate limit exceeded for installation ID 123249480.",
        "documentation_url": "https://docs.github.com/rest/overview/"
        "resources-in-the-rest-api#rate-limiting",
    }
)

# What the same call answers when the bucket is healthy.
OK_BODY = json.dumps({"login": "agent-bureau-bot-2[bot]", "type": "Bot"})

# An execution record from a run that REACHED the model and then died. One
# turn is enough: a turn means the model was called (check_agent_result's own
# DRE-2365 boundary), so this run did real work and must never be re-run.
REACHED_THE_MODEL = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0.0,
    "result": "API Error: 401 authentication_error",
}

ALLOWED_BOTS = (
    "agent-bureau-bot,agent-bureau-bot-2,agent-bureau-bot-3,"
    "agent-bureau-bot-4,github-actions"
)


def _probe(status, body, reset_in=None):
    """A stand-in for the live GitHub probe, returning one canned answer."""

    def probe(_name):
        return crl.ProbeResult(status=status, body=body, reset_in=reset_in)

    return probe


def _steps() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text())
    return doc["jobs"]["execute"]["steps"]


def _step(step_id: str) -> dict:
    for step in _steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(
        f"agent-task.yml has no step with id {step_id!r} — if the step was "
        f"renamed, update this test rather than deleting it: it is what "
        f"keeps DRE-4108's retry from quietly disappearing"
    )


def _run_shell(script: str, env: dict) -> subprocess.CompletedProcess:
    """Execute a workflow `run:` block exactly as the runner would."""
    full = dict(os.environ)
    full.update({k: ("" if v is None else str(v)) for k, v in env.items()})
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=full,
        cwd=str(ROOT),
    )


# --------------------------------------------------------------------------- #
# The signature                                                                #
# --------------------------------------------------------------------------- #

class RateLimitSignature(unittest.TestCase):
    def test_the_403_the_runs_died_on_is_the_signature(self):
        self.assertTrue(crl.is_rate_limit_refusal(403, RATE_LIMIT_BODY))

    def test_a_secondary_rate_limit_is_the_same_class(self):
        body = json.dumps({"message": "You have exceeded a secondary rate limit."})
        self.assertTrue(crl.is_rate_limit_refusal(403, body))

    def test_a_403_that_is_not_a_rate_limit_is_not_the_signature(self):
        """The commonest pre-model failure after this one: a token that is not
        allowed to read the endpoint. Retrying it buys a second identical
        refusal, which is the DRE-1921 loop."""
        body = json.dumps({"message": "Resource not accessible by integration"})
        self.assertFalse(crl.is_rate_limit_refusal(403, body))

    def test_a_healthy_answer_is_not_the_signature(self):
        self.assertFalse(crl.is_rate_limit_refusal(200, OK_BODY))

    def test_a_401_naming_a_rate_limit_is_not_the_signature(self):
        """Status AND body, never the body alone — an auth failure whose prose
        happens to say 'rate limit' is an auth failure."""
        self.assertFalse(crl.is_rate_limit_refusal(401, RATE_LIMIT_BODY))

    def test_an_unreadable_probe_is_not_the_signature(self):
        self.assertFalse(crl.is_rate_limit_refusal(None, ""))


# --------------------------------------------------------------------------- #
# The decision table                                                           #
# --------------------------------------------------------------------------- #

class DecisionTable(unittest.TestCase):
    def _decide(self, **kw):
        args = dict(
            attempt=1,
            max_attempts=3,
            claude_outcome="failure",
            execution=None,
            branch_exists=False,
            probe=_probe(403, RATE_LIMIT_BODY),
            allowed_bots=ALLOWED_BOTS,
        )
        args.update(kw)
        return crl.decide(**args)

    def test_rate_limited_before_the_model_retries(self):
        d = self._decide()
        self.assertTrue(d.retry)
        self.assertEqual(d.reason, crl.REASON_RATE_LIMITED)
        self.assertGreater(d.delay, 0)

    def test_the_receipt_names_the_call_and_the_attempt(self):
        """The card asks for one line per retry naming the call and the
        attempt. It is the only trace the operator gets: the vendor step's own
        log says nothing a later step can read."""
        d = self._decide()
        self.assertIn(FAILING_CALL, d.line)
        self.assertIn("attempt 1 of 3", d.line)

    def test_a_run_that_reached_the_model_is_never_retried(self):
        """The narrowness the card insists on: no build that did real work is
        duplicated and no spend is repeated. GitHub being rate-limited at this
        moment does not change that."""
        d = self._decide(execution=REACHED_THE_MODEL)
        self.assertFalse(d.retry)
        self.assertEqual(d.reason, crl.REASON_MODEL_STARTED)

    def test_a_pushed_branch_means_the_agent_ran(self):
        d = self._decide(branch_exists=True)
        self.assertFalse(d.retry)
        self.assertEqual(d.reason, crl.REASON_MODEL_STARTED)

    def test_a_failure_that_is_not_a_rate_limit_is_not_retried(self):
        d = self._decide(probe=_probe(200, OK_BODY))
        self.assertFalse(d.retry)
        self.assertEqual(d.reason, crl.REASON_NOT_RATE_LIMITED)

    def test_an_unreadable_probe_fails_closed(self):
        """DRE-1921: re-running into a limit nobody confirmed is how six PRs
        looped and burned the bot's quota twice. No confirmation, no retry."""
        d = self._decide(probe=_probe(None, ""))
        self.assertFalse(d.retry)
        self.assertEqual(d.reason, crl.REASON_PROBE_UNREADABLE)

    def test_a_step_that_did_not_fail_is_not_retried(self):
        for outcome in ("success", "skipped", "cancelled", ""):
            with self.subTest(outcome=outcome):
                d = self._decide(claude_outcome=outcome)
                self.assertFalse(d.retry)
                self.assertEqual(d.reason, crl.REASON_NOT_A_FAILURE)

    def test_the_retries_are_bounded(self):
        d = self._decide(attempt=3, max_attempts=3)
        self.assertFalse(d.retry)
        self.assertEqual(d.reason, crl.REASON_EXHAUSTED)

    def test_the_last_attempt_never_probes(self):
        """An exhausted budget is decided before the network is touched — the
        probe must not spend a request to learn something that cannot change
        the answer."""
        calls = []

        def probe(name):
            calls.append(name)
            return crl.ProbeResult(status=403, body=RATE_LIMIT_BODY, reset_in=None)

        self._decide(attempt=3, max_attempts=3, probe=probe)
        self.assertEqual(calls, [])

    def test_the_model_check_precedes_the_probe(self):
        """Order matters for the same reason: a run that did real work is not
        a rate-limit question at all."""
        calls = []

        def probe(name):
            calls.append(name)
            return crl.ProbeResult(status=403, body=RATE_LIMIT_BODY, reset_in=None)

        self._decide(execution=REACHED_THE_MODEL, probe=probe)
        self.assertEqual(calls, [])

    def test_the_probe_reissues_the_call_the_vendor_made(self):
        calls = []

        def probe(name):
            calls.append(name)
            return crl.ProbeResult(status=403, body=RATE_LIMIT_BODY, reset_in=None)

        self._decide(probe=probe)
        self.assertEqual(calls, ["agent-bureau-bot"])


# --------------------------------------------------------------------------- #
# The backoff                                                                  #
# --------------------------------------------------------------------------- #

class Backoff(unittest.TestCase):
    def test_every_retry_waits(self):
        """DRE-2429, the same defect verify.yml and qa-review.yml both had: a
        retry issued as the immediately following step re-issues a failure at
        once and is charged twice."""
        for attempt in (1, 2, 3):
            with self.subTest(attempt=attempt):
                self.assertGreaterEqual(crl.backoff_seconds(attempt), 30)

    def test_the_wait_is_capped(self):
        """A job that sleeps out its own App installation token is a worse
        failure than one that fails fast and lets the medic re-run it: GitHub
        kills that token at 60 minutes (DRE-3043)."""
        for attempt in (1, 2, 3):
            with self.subTest(attempt=attempt):
                self.assertLessEqual(
                    crl.backoff_seconds(attempt, reset_in=3600),
                    crl.MAX_BACKOFF_SECONDS,
                )

    def test_the_total_wait_fits_the_jobs_own_timeout(self):
        """agent-task.yml's job cap is 120 minutes and a build needs most of
        it. The retries may not eat a tenth of that."""
        doc = yaml.safe_load(WORKFLOW.read_text())
        cap_seconds = doc["jobs"]["execute"]["timeout-minutes"] * 60
        total = sum(
            crl.backoff_seconds(n, reset_in=3600)
            for n in range(1, crl.MAX_ATTEMPTS)
        )
        self.assertLess(total, cap_seconds * 0.1)

    def test_a_window_that_clears_sooner_is_not_slept_through(self):
        self.assertLessEqual(crl.backoff_seconds(1, reset_in=0), crl.backoff_seconds(1))


# --------------------------------------------------------------------------- #
# The resolved result the rest of the job reads                                #
# --------------------------------------------------------------------------- #

class ResolvedResult(unittest.TestCase):
    def test_the_last_attempt_that_ran_is_the_result(self):
        outcome, exec_file, ran = crl.resolve_attempts(
            [("failure", ""), ("success", "/tmp/b.json"), ("skipped", "")]
        )
        self.assertEqual(outcome, "success")
        self.assertEqual(exec_file, "/tmp/b.json")
        self.assertEqual(ran, 2)

    def test_a_refusal_that_never_clears_stays_a_failure(self):
        outcome, _, ran = crl.resolve_attempts(
            [("failure", ""), ("failure", ""), ("failure", "")]
        )
        self.assertEqual(outcome, "failure")
        self.assertEqual(ran, 3)

    def test_a_cancelled_run_is_reported_as_cancelled(self):
        """DRE-2074: a killed agent is not a dead agent, and the Report step
        branches on exactly this string."""
        outcome, _, _ = crl.resolve_attempts([("cancelled", "")])
        self.assertEqual(outcome, "cancelled")

    def test_a_job_whose_agent_never_ran_reports_skipped(self):
        """check_agent_result.agent_started() reads `skipped` as GitHub itself
        saying the agent step never ran — the value must survive the resolve."""
        outcome, _, ran = crl.resolve_attempts([("skipped", ""), ("skipped", "")])
        self.assertEqual(outcome, "skipped")
        self.assertEqual(ran, 0)

    def test_an_execution_file_survives_a_later_attempt_that_left_none(self):
        outcome, exec_file, _ = crl.resolve_attempts(
            [("failure", "/tmp/a.json"), ("failure", "")]
        )
        self.assertEqual(outcome, "failure")
        self.assertEqual(exec_file, "/tmp/a.json")


# --------------------------------------------------------------------------- #
# The real script, driven the way the workflow drives it                       #
# --------------------------------------------------------------------------- #

class TheScriptAsTheWorkflowRunsIt(unittest.TestCase):
    def _decide(self, td: Path, *, attempt="1", outcome="failure",
                execution=None, branch="", fake=None) -> dict:
        exec_path = td / "claude-execution-output.json"
        if execution is not None:
            exec_path.write_text(json.dumps(execution))
        out = td / "github_output"
        out.touch()
        env = dict(os.environ)
        env["BUREAU_FAKE_BOT_LOOKUP"] = json.dumps(
            fake if fake is not None
            else {"status": 403, "body": RATE_LIMIT_BODY}
        )
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "decide",
             "--attempt", attempt,
             "--max-attempts", "3",
             "--claude-outcome", outcome,
             "--execution-file", str(exec_path),
             "--branch", branch,
             "--allowed-bots", ALLOWED_BOTS,
             "--github-output", str(out)],
            capture_output=True, text=True, check=False, env=env,
        )
        parsed = {}
        for line in out.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key] = value
        parsed["_stdout"] = proc.stdout
        parsed["_rc"] = proc.returncode
        return parsed

    def test_rate_limited_before_the_model_asks_for_a_retry(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td))
        self.assertEqual(got["retry"], "true")
        self.assertEqual(got["reason"], crl.REASON_RATE_LIMITED)
        self.assertTrue(int(got["delay"]) > 0)

    def test_it_writes_the_receipt_line_to_the_run_log(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td))
        self.assertIn(FAILING_CALL, got["_stdout"])
        self.assertIn("attempt 1 of 3", got["_stdout"])

    def test_a_run_that_reached_the_model_asks_for_no_retry(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td), execution=REACHED_THE_MODEL)
        self.assertEqual(got["retry"], "false")
        self.assertEqual(got["reason"], crl.REASON_MODEL_STARTED)

    def test_a_healthy_bucket_asks_for_no_retry(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td), fake={"status": 200, "body": OK_BODY})
        self.assertEqual(got["retry"], "false")
        self.assertEqual(got["reason"], crl.REASON_NOT_RATE_LIMITED)

    def test_the_last_attempt_asks_for_no_retry(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td), attempt="3")
        self.assertEqual(got["retry"], "false")
        self.assertEqual(got["reason"], crl.REASON_EXHAUSTED)

    def test_it_never_fails_the_build(self):
        """Fail-soft, like dispatch_pool.py: a classifier that cannot answer
        must leave the run exactly as it was, never red on its own account."""
        with tempfile.TemporaryDirectory() as td:
            got = self._decide(Path(td), fake={"status": None, "body": ""})
        self.assertEqual(got["_rc"], 0)
        self.assertEqual(got["retry"], "false")

    def test_a_broken_probe_seam_still_exits_clean(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "github_output"
            out.touch()
            env = dict(os.environ)
            env["BUREAU_FAKE_BOT_LOOKUP"] = "not json at all"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "decide",
                 "--attempt", "1", "--max-attempts", "3",
                 "--claude-outcome", "failure",
                 "--execution-file", str(Path(td) / "missing.json"),
                 "--allowed-bots", ALLOWED_BOTS,
                 "--github-output", str(out)],
                capture_output=True, text=True, check=False, env=env,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("retry=false", out.read_text())

    def test_resolve_prints_the_outputs_the_job_reads(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "github_output"
            out.touch()
            subprocess.run(
                [sys.executable, str(SCRIPT), "resolve",
                 "--attempt", "failure=",
                 "--attempt", "success=/tmp/b.json",
                 "--attempt", "skipped=",
                 "--github-output", str(out)],
                capture_output=True, text=True, check=False,
            )
            text = out.read_text()
        self.assertIn("outcome=success", text)
        self.assertIn("execution_file=/tmp/b.json", text)
        self.assertIn("attempts=2", text)


# --------------------------------------------------------------------------- #
# The wiring in agent-task.yml                                                 #
# --------------------------------------------------------------------------- #

VENDOR = "anthropics/claude-code-action"
ATTEMPT_IDS = ("claude", "claude_retry1", "claude_retry2")
DECISION_IDS = ("retry1", "retry2")


class WorkflowWiring(unittest.TestCase):
    def test_there_are_three_bounded_attempts(self):
        ids = [s.get("id") for s in _steps() if str(s.get("uses", "")).startswith(VENDOR)]
        self.assertEqual(ids, list(ATTEMPT_IDS))
        self.assertEqual(len(ATTEMPT_IDS), crl.MAX_ATTEMPTS)

    def test_the_vendor_pin_is_unchanged_at_every_attempt(self):
        """The card's criterion in one assertion: the retry is ours, the
        action is theirs, and its commit does not move."""
        pins = {_step(i)["uses"] for i in ATTEMPT_IDS}
        self.assertEqual(len(pins), 1, f"the attempts pin different commits: {pins}")
        pin = pins.pop()
        self.assertIn("@9c5ddab2e6d17b83ea679153b31f1d5f023cf636", pin)

    def test_the_attempts_run_the_same_agent(self):
        """GitHub Actions has no YAML anchors, so the retry `with:` blocks are
        deliberate duplicates of attempt 1's — the qa-review.yml pattern. A
        retry that quietly gained or lost an input is the drift that comment
        warns about; there is NO intended difference here, because the thing
        being retried never reached the model."""
        first = _step("claude")["with"]
        for retry_id in ATTEMPT_IDS[1:]:
            with self.subTest(step=retry_id):
                self.assertEqual(_step(retry_id)["with"], first)

    def test_every_attempt_runs_the_proved_binary(self):
        for step_id in ATTEMPT_IDS:
            with self.subTest(step=step_id):
                self.assertEqual(
                    _step(step_id)["with"]["path_to_claude_code_executable"],
                    "${{ steps.install_claude.outputs.executable }}",
                )

    def test_no_attempt_can_fail_the_job_on_its_own(self):
        """Criterion 3 lives here: a refusal that clears must leave NO failed
        run. The job's red light is the terminal gate below, which reads the
        RESOLVED outcome — so attempt 1 failing and attempt 2 succeeding ends
        green and the medic files nothing."""
        for step_id in ATTEMPT_IDS:
            with self.subTest(step=step_id):
                self.assertIs(_step(step_id).get("continue-on-error"), True)

    def test_each_retry_is_gated_on_its_own_decision(self):
        for decision_id, attempt_id in zip(DECISION_IDS, ATTEMPT_IDS[1:]):
            with self.subTest(step=attempt_id):
                self.assertIn(
                    f"steps.{decision_id}.outputs.retry == 'true'",
                    _step(attempt_id)["if"],
                )

    def test_every_retry_is_preceded_by_a_backoff_gated_identically(self):
        """DRE-2429's rule, and qa-review.yml's: the backoff's `if` is
        byte-identical to the retry's, or a healthy build sleeps for a retry
        that will never happen."""
        steps = _steps()
        for decision_id, attempt_id in zip(DECISION_IDS, ATTEMPT_IDS[1:]):
            with self.subTest(step=attempt_id):
                index = next(n for n, s in enumerate(steps) if s.get("id") == attempt_id)
                backoff = steps[index - 1]
                self.assertEqual(backoff.get("id"), f"backoff{decision_id[-1]}")
                self.assertEqual(backoff["if"], _step(attempt_id)["if"])
                self.assertIn(f"steps.{decision_id}.outputs.delay", backoff["run"])

    def test_the_backoff_clears_the_stale_execution_record(self):
        """qa-review.yml clears the stale verdict before its retry for the same
        reason: attempt 1's record must never be read as attempt 2's."""
        for decision_id in DECISION_IDS:
            with self.subTest(step=decision_id):
                self.assertIn("rm -f", _step(f"backoff{decision_id[-1]}")["run"])

    def test_the_decisions_are_fail_soft(self):
        for decision_id in DECISION_IDS:
            with self.subTest(step=decision_id):
                self.assertIs(_step(decision_id).get("continue-on-error"), True)

    def test_the_decisions_read_the_attempt_before_them(self):
        for decision_id, attempt_id in zip(DECISION_IDS, ATTEMPT_IDS):
            with self.subTest(step=decision_id):
                env = _step(decision_id)["env"]
                self.assertEqual(env["CLAUDE_OUTCOME"], f"${{{{ steps.{attempt_id}.outcome }}}}")

    def test_the_downstream_gate_and_report_read_the_resolved_result(self):
        """Every consumer of the agent step moved to the resolver — a consumer
        left reading `steps.claude.*` would judge the whole run by attempt 1
        and report a dead agent for a build the retry finished."""
        text = WORKFLOW.read_text()
        self.assertNotIn("steps.claude.outcome", text)
        self.assertNotIn("steps.claude.outputs.execution_file", text)
        self.assertIn("steps.claude_result.outputs.outcome", text)
        self.assertIn("steps.claude_result.outputs.execution_file", text)

    def test_the_resolver_runs_before_anything_reads_it(self):
        ids = [s.get("id") for s in _steps()]
        self.assertLess(ids.index("claude_result"), ids.index("rescue"))

    def test_the_terminal_gate_is_the_last_step(self):
        """It must run AFTER the rescue, the result gate and the Linear
        report, all of which are `always()` today and must keep running on a
        build that failed — the retry may not change what a dead run reports."""
        self.assertEqual(_steps()[-1].get("id"), "agent_step_failed")

    def test_the_retry_steps_are_bounded_by_the_same_gate_as_the_agent(self):
        """A bounced card and a duplicate dispatch skip the agent; they must
        skip its retries too, or a card the gate refused gets three of them."""
        for step_id in ATTEMPT_IDS[1:] + DECISION_IDS:
            with self.subTest(step=step_id):
                condition = _step(step_id)["if"]
                self.assertIn("steps.gate.outputs.bounced != 'true'", condition)
                self.assertIn("steps.dedupe.outputs.skip != 'true'", condition)


# --------------------------------------------------------------------------- #
# What the operator and the medic see — the shell, executed                    #
# --------------------------------------------------------------------------- #

class TheRunsConclusion(unittest.TestCase):
    """The medic watches `Agent Task` and files a pipeline-failure card on
    `conclusion == 'failure'` (self-medic.yml). So "files no card" and "files
    a card" are one observable fact: whether the terminal gate exits 0."""

    def _gate(self, outcome: str, attempts: str = "1") -> subprocess.CompletedProcess:
        return _run_shell(
            _step("agent_step_failed")["run"],
            {"AGENT_OUTCOME": outcome, "AGENT_ATTEMPTS": attempts},
        )

    def test_a_refusal_that_clears_leaves_the_run_green(self):
        proc = self._gate("success", attempts="2")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_refusal_that_persists_fails_the_run(self):
        proc = self._gate("failure", attempts="3")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("::error::", proc.stdout + proc.stderr)

    def test_a_cancelled_run_is_not_turned_into_a_failure(self):
        """DRE-2074: the 120-minute cap killing a healthy long build must not
        be re-reported as an agent failure by this new gate."""
        proc = self._gate("cancelled")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_skipped_agent_is_not_turned_into_a_failure(self):
        proc = self._gate("skipped", attempts="0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_failure_names_how_many_attempts_were_spent(self):
        proc = self._gate("failure", attempts="3")
        self.assertIn("3", proc.stdout + proc.stderr)


class TheResolveStepAsWired(unittest.TestCase):
    def _resolve(self, attempts: list[tuple[str, str]]) -> dict:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "github_output"
            out.touch()
            env = {"GITHUB_OUTPUT": str(out)}
            for n, (outcome, exec_file) in enumerate(attempts, start=1):
                env[f"ATTEMPT_{n}_OUTCOME"] = outcome
                env[f"ATTEMPT_{n}_FILE"] = exec_file
            for n in range(len(attempts) + 1, crl.MAX_ATTEMPTS + 1):
                env[f"ATTEMPT_{n}_OUTCOME"] = ""
                env[f"ATTEMPT_{n}_FILE"] = ""
            proc = _run_shell(_step("claude_result")["run"], env)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            parsed = {}
            for line in out.read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    parsed[key] = value
            return parsed

    def test_the_retry_that_succeeded_is_what_the_job_reports(self):
        got = self._resolve([("failure", ""), ("success", "/tmp/b.json")])
        self.assertEqual(got["outcome"], "success")
        self.assertEqual(got["execution_file"], "/tmp/b.json")

    def test_three_refusals_report_a_failure(self):
        got = self._resolve([("failure", ""), ("failure", ""), ("failure", "")])
        self.assertEqual(got["outcome"], "failure")
        self.assertEqual(got["attempts"], "3")


if __name__ == "__main__":
    unittest.main()
