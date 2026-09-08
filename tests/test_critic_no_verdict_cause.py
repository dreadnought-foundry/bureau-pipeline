"""DRE-3304 — a critic that FINISHES without a verdict must say so, and say why.

THE RUN. agent-bureau PR #2344, run 34170941436 attempt 1, head `d5e40993f`,
2026-09-07. Both critic attempts ended `subtype: success`, `is_error: false` —
52 turns / $1.56 and 29 turns / $0.93. Neither crashed. The job's red line said:

    QA critic crashed on both attempts — no real verdict.

Nothing crashed. Worse, the run log two steps earlier already knew exactly what
had happened, in the gate's own words:

    the review left an UNFINISHED verdict (1006 chars) — it wrote the stub and
    never replaced it

and the stub itself said why, in the reviewer's own prose: it was waiting on the
`Operator rollback harness (pytest)` CI job before finalising — a job the head
commit under review had just raised from a 10-minute cap to 20, and which was
still running when both attempts ended (it was finally cancelled at 00:03:10Z,
nine minutes after attempt 2 gave up). The reviewer parked itself behind a clock
longer than its own session, twice.

So the cause was never unknown. It was computed by `check_critic_result.py`,
printed to the log, and then thrown away: `outcome()` collapses every
verdict-file failure into the single word `completed_no_verdict`, and the fail
step turns anything that is not `turn_exhaustion` into "crashed". Three facts
the gate held — which of the four ways the file was unusable, that both attempts
ended cleanly, and which run this was — never reached the one line a medic or an
operator reads first.

WHAT IS PINNED HERE

1. The gate publishes the CAUSE it already computes, as a step output, for each
   of the four ways a verdict file can be unusable — and publishes NOTHING on a
   crash, where the file is never consulted. Inventing a file cause for a run
   whose file was never read would be a new version of the same lie.

2. The fail step's annotation reads as what happened. `completed_no_verdict`
   never says "crashed", names BOTH attempts, names the run, and names the
   cause. A genuine crash still reads as a crash. DRE-2924's turn exhaustion
   still reads as turn exhaustion.

3. The retry is not a coin flip. Attempt 2 differed from attempt 1 by a 120s
   sleep and a higher turn ceiling, neither of which had anything to do with
   why attempt 1 stopped — so it re-blocked on the same unfinished CI job and
   came back with LESS (29 turns against 52). The retry prompt now carries what
   happened to attempt 1 and the one instruction that addresses it: decide on
   the code, do not wait on anything outside the checkout.

The security shape is unchanged and is tested as hard as the feature. The cause
is an enum from a fixed table in the gate, and its sentence comes from the same
table — never a byte of the verdict file, which is written by an agent that has
just read a pull request authored by anyone.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
GATE = ROOT / "scripts" / "check_critic_result.py"

# Attempt 1 of run 34170941436, as its execution file recorded it.
RAN_CLEAN = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 52,
    "total_cost_usd": 1.5637050000000001,
    "duration_ms": 273169,
    "result": "I reviewed the diff.",
}

# The stub the reviewer wrote first and never replaced — the verdict file as it
# actually stood at the end of attempt 1, trimmed to its shape.
UNFINISHED_STUB = (
    "VERDICT: REQUEST_CHANGES\n"
    "<!-- QA-REVIEW-INCOMPLETE -->\n"
    "## Summary\n"
    "This review has not finished.\n\n"
    "## Progress notes (in-progress, not final)\n"
    "Waiting on live CI result for `Operator rollback harness (pytest)`.\n"
)

# The auth/startup death the crash wording was written for and still describes.
AUTH_DEATH = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 634,
    "num_turns": 1,
    "total_cost_usd": 0,
    "result": "API Error: 401 authentication_error",
}

# DRE-2924's turn wall: is_error, but the opposite of an agent that never ran.
TURN_WALL = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 62,
    "total_cost_usd": 2.56,
    "duration_ms": 480000,
}

RUN_URL = (
    "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/34170941436"
)


def gate_outputs(td: Path, execution, verdict_text=None) -> dict:
    """Run the real gate the way the workflow does, and read its outputs."""
    exec_path = td / "claude-execution-output.json"
    verdict_path = td / "qa-verdict.md"
    if execution is not None:
        exec_path.write_text(json.dumps(execution))
    if verdict_text is not None:
        verdict_path.write_text(verdict_text)
    out_path = td / "github_output"
    out_path.touch()
    subprocess.run(
        [sys.executable, str(GATE), str(exec_path), str(verdict_path),
         "--github-output", str(out_path)],
        capture_output=True, text=True, check=False,
    )
    parsed = {}
    for line in out_path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return parsed


def cause_for(execution, verdict_text=None) -> str:
    with tempfile.TemporaryDirectory() as raw:
        return gate_outputs(Path(raw), execution, verdict_text).get("cause", "")


def _step(step_id: str) -> dict:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    for step in doc["jobs"]["review"]["steps"]:
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"qa-review.yml has no step with id {step_id!r}")


def _step_by_name(name: str) -> dict:
    doc = yaml.safe_load(QA_REVIEW.read_text())
    for step in doc["jobs"]["review"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"qa-review.yml has no step named {name!r}")


def _resolve(text: str) -> str:
    """Fill in the GitHub expressions the fail step's env block carries."""
    for expr, value in {
        "github.server_url": "https://github.com",
        "github.repository": "dreadnought-foundry/agent-bureau",
        "github.run_id": "34170941436",
    }.items():
        text = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, text)
    return text


def annotation(a1: dict, a2: dict) -> str:
    """Execute the REAL fail step's shell with two gates' real outputs."""
    step = _step("critic_fail")
    run = _resolve(step["run"])
    assert "${{" not in run, "an unresolved GitHub expression reached the shell"

    # Whatever the step's env block names, resolved the way Actions would, with
    # the two gate steps' outputs substituted in. Nothing is invented here: if
    # the step stops reading a value, this stops supplying it.
    env = dict(os.environ)
    for key, raw in (step.get("env") or {}).items():
        value = _resolve(str(raw))
        if "steps.gate1.outputs." in value:
            value = a1.get(value.rsplit(".", 1)[-1].rstrip(" }"), "")
        elif "steps.gate2.outputs." in value:
            value = a2.get(value.rsplit(".", 1)[-1].rstrip(" }"), "")
        env[key] = value

    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        script = td / "fail.sh"
        script.write_text("set -uo pipefail\n" + run)
        proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                              capture_output=True, text=True)
    assert proc.returncode == 1, (
        f"the fail step must still fail the job:\n{proc.stderr}"
    )
    return proc.stdout


class TheGatePublishesTheCauseItAlreadyKnowsTest(unittest.TestCase):
    """The four ways a verdict file is unusable are four different causes."""

    def test_the_unfinished_stub_is_named_as_such(self):
        # Run 34170941436's actual shape, both attempts.
        self.assertEqual(cause_for(RAN_CLEAN, UNFINISHED_STUB),
                         "unfinished_stub")

    def test_an_absent_verdict_file_is_named_as_such(self):
        self.assertEqual(cause_for(RAN_CLEAN, None), "verdict_absent")

    def test_an_empty_verdict_file_is_named_as_such(self):
        self.assertEqual(cause_for(RAN_CLEAN, "   \n\n"), "verdict_empty")

    def test_a_file_with_no_verdict_line_is_named_as_such(self):
        # The bolded near-miss: a real review that the gate cannot read.
        self.assertEqual(
            cause_for(RAN_CLEAN, "## Summary\n**VERDICT: APPROVE**\n"),
            "no_verdict_line",
        )

    def test_a_crash_publishes_no_file_cause_at_all(self):
        """The file is never consulted on a crash, so naming one would lie.

        This is the whole discipline of the card: the fix for a message that
        asserted more than the run supported is not a message that asserts
        something else it does not know.
        """
        self.assertEqual(cause_for(AUTH_DEATH, UNFINISHED_STUB), "")

    def test_a_real_verdict_publishes_no_cause(self):
        self.assertEqual(
            cause_for(RAN_CLEAN, "VERDICT: APPROVE\n\n## Summary\nFine.\n"),
            "",
        )

    def test_the_cause_is_always_a_bare_token(self):
        """A step output that could carry a newline could forge `real=true`."""
        for verdict in (None, "", "   ", UNFINISHED_STUB, "no verdict here"):
            cause = cause_for(RAN_CLEAN, verdict)
            self.assertRegex(cause, r"^[a-z_]*$", f"cause {cause!r} is not a token")


class TheAnnotationSaysWhatHappenedTest(unittest.TestCase):
    """The red line a medic and an operator read first."""

    def setUp(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            self.no_verdict = gate_outputs(td, RAN_CLEAN, UNFINISHED_STUB)
        with tempfile.TemporaryDirectory() as raw:
            self.crash = gate_outputs(Path(raw), AUTH_DEATH)
        with tempfile.TemporaryDirectory() as raw:
            self.turns = gate_outputs(Path(raw), TURN_WALL)

    def test_two_completed_attempts_are_never_called_a_crash(self):
        line = annotation(self.no_verdict, self.no_verdict)
        self.assertNotIn("crash", line.lower(),
                         "nothing crashed in run 34170941436")

    def test_it_says_the_reviewer_finished_and_wrote_no_verdict(self):
        line = annotation(self.no_verdict, self.no_verdict).lower()
        self.assertIn("no verdict", line)
        self.assertIn("finished", line)

    def test_it_names_both_attempts(self):
        line = annotation(self.no_verdict, self.no_verdict).lower()
        self.assertIn("attempt 1", line)
        self.assertIn("attempt 2", line)

    def test_it_names_the_run(self):
        self.assertIn(RUN_URL, annotation(self.no_verdict, self.no_verdict))

    def test_it_names_the_cause_in_plain_english(self):
        line = annotation(self.no_verdict, self.no_verdict).lower()
        self.assertIn("stub", line,
                      "the gate knew it was the unreplaced stub; say so")

    def test_a_cause_it_cannot_determine_is_admitted_not_invented(self):
        """No cause recorded means we say we could not tell, not a guess."""
        blank = dict(self.no_verdict, cause="")
        line = annotation(blank, blank).lower()
        self.assertNotIn("crash", line)
        self.assertTrue(
            "could not" in line or "not recorded" in line,
            f"an unknown cause must be admitted, got: {line}",
        )

    def test_a_mixed_pair_still_reports_the_completed_attempt_honestly(self):
        """One clean-and-empty attempt is enough to disprove "crashed twice"."""
        line = annotation(self.crash, self.no_verdict).lower()
        self.assertIn("no verdict", line)
        self.assertIn("attempt 1 crashed", line)

    def test_a_mixed_pair_does_not_claim_both_attempts_exited_cleanly(self):
        """The fault being fixed here, pointed the other way.

        One attempt died and one finished empty. Saying "the reviewer exited
        cleanly" over that pair asserts of both what was true of one — which
        is precisely how "crashed on both attempts" came to be printed over
        two runs that did not crash.
        """
        line = annotation(self.crash, self.no_verdict).lower()
        self.assertNotIn("exited cleanly", line)
        self.assertNotIn("no credential needs rotating", line)

    def test_two_clean_attempts_do_say_no_credential_needs_rotating(self):
        line = annotation(self.no_verdict, self.no_verdict).lower()
        self.assertIn("no credential needs rotating", line)


class TheOtherTwoOutcomesStillReadAsThemselvesTest(unittest.TestCase):
    """The regression this card must not cause."""

    def setUp(self):
        with tempfile.TemporaryDirectory() as raw:
            self.no_verdict = gate_outputs(Path(raw), RAN_CLEAN, UNFINISHED_STUB)
        with tempfile.TemporaryDirectory() as raw:
            self.crash = gate_outputs(Path(raw), AUTH_DEATH)
        with tempfile.TemporaryDirectory() as raw:
            self.turns = gate_outputs(Path(raw), TURN_WALL)

    def test_a_genuine_crash_still_reads_as_a_crash(self):
        self.assertIn("crash", annotation(self.crash, self.crash).lower())

    def test_turn_exhaustion_still_reads_as_dre_2924s_message(self):
        line = annotation(self.turns, self.turns).lower()
        self.assertIn("turn", line)
        self.assertNotIn("crashed on both", line)

    def test_turn_exhaustion_outranks_a_merely_empty_attempt(self):
        """DRE-2924's ordering: exhaustion is the actionable cause."""
        line = annotation(self.no_verdict, self.turns).lower()
        self.assertIn("turn", line)

    def test_no_execution_record_at_all_keeps_the_crash_wording(self):
        with tempfile.TemporaryDirectory() as raw:
            unknown = gate_outputs(Path(raw), None)
        self.assertEqual(unknown.get("outcome"), "unknown")
        self.assertIn("crash", annotation(unknown, unknown).lower())

    def test_the_step_still_fails_the_job_on_every_path(self):
        # annotation() asserts returncode 1; this states the contract out loud.
        for pair in ((self.crash, self.crash), (self.turns, self.turns),
                     (self.no_verdict, self.no_verdict)):
            annotation(*pair)


class TheRetryIsNotACoinFlipTest(unittest.TestCase):
    """Attempt 2 must differ from attempt 1 in a way that bears on the cause."""

    def setUp(self):
        self.first = _step_by_name("Critic review")["with"]["prompt"]
        self.retry = _step("critic_retry")["with"]["prompt"]

    def test_the_retry_knows_the_first_attempt_produced_nothing(self):
        self.assertIn("steps.gate1.outputs.cause_text", self.retry,
                      "the retry must be told WHY attempt 1 came back empty")

    def test_the_first_attempt_is_not_told_about_a_previous_attempt(self):
        self.assertNotIn("gate1.outputs.cause", self.first,
                         "attempt 1 has no previous attempt to learn from")

    def test_the_retry_is_told_not_to_wait_on_anything_external(self):
        """The observed cause: it parked behind a CI job that outlived it."""
        retry = self.retry.lower()
        self.assertIn("do not wait", retry)
        self.assertIn("ci", retry)

    def test_the_retry_is_told_to_finish_the_file(self):
        self.assertIn("finish", self.retry.lower())

    def test_the_two_prompts_differ_by_exactly_the_retry_briefing(self):
        """Today they are byte-identical — that IS the "not a strategy" bug.

        Asserted as the presence of ONE marked block rather than as raw
        inequality, so the drift this file must not introduce stays visible:
        everything outside the briefing is still the same review.
        """
        marker = "THIS IS THE RETRY"
        self.assertIn(marker, self.retry)
        self.assertNotIn(marker, self.first)
        # The retry is a superset: attempt 1's review plus what it learned.
        self.assertIn("You are an adversarial code reviewer", self.retry)
        stripped = self.retry.split(marker)[0] + self.retry.split(marker)[-1]
        self.assertGreater(
            len(stripped), 0.8 * len(self.first),
            "the retry must still be the same review, plus the briefing",
        )


class TheFallbackSentenceHasOneSourceTest(unittest.TestCase):
    """The workflow's fallback and the gate's constant are the same sentence.

    Two spellings of "we could not tell" is how the gate and the red line
    quietly stop agreeing about the same run — the class of drift the
    duplicated attempt-1/attempt-2 gate blocks in this workflow already carry
    a warning about.
    """

    def test_the_workflow_fallback_is_the_gates_own_constant(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_critic_result

        self.assertIn(check_critic_result.UNKNOWN_CAUSE_TEXT,
                      _step("critic_fail")["run"])

    def test_every_cause_sentence_is_a_single_line(self):
        """A newline in a step output writes a step output of its own."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_critic_result

        sentences = list(check_critic_result._NO_VERDICT_CAUSES.values())
        sentences.append(check_critic_result.UNKNOWN_CAUSE_TEXT)
        for sentence in sentences:
            self.assertNotIn("\n", sentence)


class TheCommentNamesTheCauseTooTest(unittest.TestCase):
    """The CEO-facing half of the same fact.

    The neutral comment told its reader the run log "records what was found
    where the verdict should have been". By the time anyone reads it the
    runner is destroyed and /tmp/qa-verdict.md with it, so that sentence was
    an instruction to go and look at nothing.
    """

    def _comment(self, execution, verdict_text=None) -> str:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            gate = gate_outputs(td, execution, verdict_text)
            step = _step("post")
            run = re.sub(
                r"\$\{\{\s*github\.repository\s*\}\}",
                "dreadnought-foundry/agent-bureau", step["run"])
            run = re.sub(r"\$\{\{\s*github\.run_id\s*\}\}", "34170941436", run)
            assert "${{" not in run, "an unresolved expression reached the shell"
            run = run.replace("/tmp/", str(td) + "/")
            bin_dir = td / "bin"
            bin_dir.mkdir(exist_ok=True)
            gh = bin_dir / "gh"
            gh.write_text("#!/usr/bin/env bash\nexit 0\n")
            gh.chmod(0o755)
            script = td / "post.sh"
            script.write_text("set -euo pipefail\n" + run)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            # Always set on a runner; the local shell is the only place it is
            # not, which is why the sibling suites fail here and pass in CI.
            env.setdefault("GITHUB_REPOSITORY", "dreadnought-foundry/agent-bureau")
            env.update({
                "CARD": "", "REAL": "false", "PR": "2344",
                "REVIEWED_SHA": "d5e40993f" + "0" * 31, "CONTENT_ID": "",
                "MODEL_ID": "claude-sonnet-5", "MODEL_WHY": "advisory ladder top",
                "A1_OUTCOME": gate.get("outcome", ""),
                "A1_TURNS": gate.get("turns", ""),
                "A1_COST": gate.get("cost", ""),
                "A1_CAUSE_TEXT": gate.get("cause_text", ""),
                "A2_OUTCOME": gate.get("outcome", ""),
                "A2_TURNS": gate.get("turns", ""),
                "A2_COST": gate.get("cost", ""),
                "A2_CAUSE_TEXT": gate.get("cause_text", ""),
            })
            proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                                  capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
            return (td / "qa-comment.md").read_text()

    def test_the_unfinished_stub_is_named_in_the_comment(self):
        body = self._comment(RAN_CLEAN, UNFINISHED_STUB)
        self.assertIn("stub", body.lower())

    def test_the_comment_still_holds_the_merge(self):
        """Unchanged contract: merge-gate's marker in, an approval out."""
        body = self._comment(RAN_CLEAN, UNFINISHED_STUB)
        self.assertIn("QA Critic", body)
        self.assertNotIn("VERDICT: APPROVE", body)
        self.assertIn("not a request for changes", body.lower())

    def test_a_crash_comment_names_no_cause(self):
        body = self._comment(AUTH_DEATH)
        self.assertNotIn("What was found in its place", body)


if __name__ == "__main__":
    unittest.main()
