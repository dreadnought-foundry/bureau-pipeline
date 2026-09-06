"""RED-first tests for DRE-2924 — the size router calls a one-pass the critic
cannot finish, and the gate then blames the wrong thing.

THE BUG (portico PR #364, 2026-08-31). 18 files / 2,059 changed lines.
`pr_size_strategy.py` sized it and routed it `standard`:

    [qa-size] 18 files / 2,059 changed lines … → strategy: standard —
    reviewable size 18 files / 2,059 lines is within the one-pass threshold
    (50 files / 5,000 lines)

The critic then failed to produce a verdict TWICE. Attempt one ran 62 of its
80 turns and billed ~$2.56 before ending with nothing readable where the
verdict should have been. The retry was reported as

    The adversarial reviewer crashed twice (startup/auth failure, no
    inference).

which was wrong about its own cause — the run demonstrably authenticated, did
real work, and was billed for it. That exact misdiagnosis has cost three days
across two repos once already (portico's `critic-dies-when-the-oauth-token-is-
stale`: the real auth signature is instant, `num_turns: 1`,
`total_cost_usd: 0`).

THE MEASUREMENT, same repo, same night — five reviews, one config:

    #361   4 files /   507 lines   APPROVED
    #362   5 files /   975 lines   APPROVED
    #363   6 files / 1,026 lines   APPROVED
    #366   4 files /  ~700 lines   reviewed
    #364  18 files / 2,059 lines   NO VERDICT, TWICE

Everything the critic completed was ≤ 6 files / ~1,030 lines. The one PR at 3×
the files is the only one it could not finish, and it was resolved by splitting
it into #367 (11 files) and #368 (7 files).

THE ACTUAL DEFECT. The threshold is expressed in files and lines; the
constraint is TURNS (`max_turns=80` / `retry_max_turns=120`). A 50-file /
5,000-line PR routed `standard` cannot be reviewed in 80 turns — the router
will happily send something 2.5× larger than the one that already failed. And
a reviewer that runs out of turns does not fail loudly: it burns the budget
doing real work, leaves no verdict, and the gate reports a cause that is not
true, which invites a retry into the same wall.

WHAT THIS FILE PINS:

1. A PR the size of #364 routes to the multi-pass `large` strategy, and every
   review the critic ACTUALLY completed that night keeps the one-pass path.
2. The threshold constant carries the measurement that justifies it, beside
   the constant — a number nobody can trace back to evidence is the number
   that was wrong here.
3. A no-verdict run whose TURNS WERE SPENT reports turn exhaustion. The gate
   says `turn_exhaustion`, and the comment the pull request gets names it and
   never claims a startup/auth failure.
4. Every failing path carries the run's own turns and cost. The first #364
   message included them and that is the only reason the cause was
   diagnosable at all.

Two rules bound the rewording, exactly as in
tests/test_qa_review_no_verdict_message.py, and both are load-bearing for
merges: the comment must keep `QA Critic` (or merge-gate stops treating it as
the latest verdict and a stale APPROVE survives), and it must never contain
`VERDICT: APPROVE` (or the neutral status IS a merge credential).
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
SCRIPTS = ROOT / "scripts"
GATE = SCRIPTS / "check_critic_result.py"

sys.path.insert(0, str(SCRIPTS))

import execution_result  # noqa: E402
import pr_size_strategy as pss  # noqa: E402


# ── the night of 2026-08-31, as the run logs recorded it ───────────────────

#: (label, files, changed lines) for every review the critic FINISHED.
COMPLETED = (
    ("#361", 4, 507),
    ("#362", 5, 975),
    ("#363", 6, 1_026),
    ("#366", 4, 700),
)

#: The one it could not finish, twice. Measured off the head sha preserved on
#: `agent/DRE-2888-one-definition-of-answered`.
PR_364 = ("#364", 18, 2_059)


def compare(files):
    """A GitHub compare record: [(path, additions, deletions), ...]."""
    return {
        "files": [
            {"filename": p, "additions": a, "deletions": d}
            for p, a, d in files
        ]
    }


def spread(n_files, total_lines, prefix="src/mod"):
    """`n_files` hand-written source files carrying `total_lines` between
    them — the shape of a real pull request, not one giant file."""
    each, extra = divmod(total_lines, n_files)
    return [
        (f"{prefix}{i}.py", each + (1 if i < extra else 0), 0)
        for i in range(n_files)
    ]


def strategy_for(files, lines):
    return pss.choose(pss.measure(compare(spread(files, lines)), None))


# ── the execution records, from the run that produced them ─────────────────

#: portico PR #364, attempt 1: 62 of 80 turns, ~$2.56, no verdict. The action
#: reports a ceiling death as `error_max_turns` with `is_error: true`.
TURN_EXHAUSTED = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 62,
    "total_cost_usd": 2.5637,
    "duration_ms": 431000,
    "result": "",
}

#: The genuine stale-token signature the crash wording was written for, and
#: the one it must go on describing: instant, one turn, $0.
AUTH_DEATH = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 634,
    "num_turns": 1,
    "total_cost_usd": 0,
    "result": "API Error: 401 authentication_error",
    "terminal_reason": "api_error",
}

#: The stub the critic writes before it starts reading (DRE-2466) — what a
#: run that never got to the end of its review leaves behind.
UNFINISHED = (
    "VERDICT: REQUEST_CHANGES\n"
    "<!-- QA-REVIEW-INCOMPLETE -->\n"
    "## Summary\nThis review has not finished.\n"
)


def gate_outputs(td: Path, execution: dict | None, verdict_text=None) -> dict:
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


def wf_steps(job="review"):
    doc = yaml.safe_load(QA_REVIEW.read_text())
    return doc["jobs"][job]["steps"]


def wf_step(step_id):
    for step in wf_steps():
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"no step id={step_id!r} in qa-review.yml")


def _resolve_expressions(run: str) -> str:
    for expr, value in {
        "github.repository": "dreadnought-foundry/portico",
        "github.run_id": "31891083751",
    }.items():
        run = re.sub(r"\$\{\{\s*" + re.escape(expr) + r"\s*\}\}", value, run)
    return run


def run_post(td: Path, gate2: dict, *, gate1: dict | None = None,
             real: str = "false") -> tuple[subprocess.CompletedProcess, str]:
    """Execute the post step's own shell with the gate's own outputs as env.

    Nothing about the message is asserted by grepping YAML — the shell either
    produces the right words or it does not.
    """
    bin_dir = td / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\nexit 0\n")
    gh.chmod(0o755)

    run = _resolve_expressions(wf_step("post")["run"])
    assert "${{" not in run, "an unresolved GitHub expression reached the shell"
    script = td / "post.sh"
    script.write_text("set -euo pipefail\n" + run.replace("/tmp/", str(td) + "/"))

    gate1 = gate1 if gate1 is not None else gate2
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.update({
        "CARD": "", "REAL": real, "PR": "364",
        "REVIEWED_SHA": "a" * 40, "CONTENT_ID": "",
        "MODEL_ID": "claude-sonnet-5", "MODEL_WHY": "advisory ladder top",
        "A1_OUTCOME": gate1.get("outcome", ""),
        "A1_TURNS": gate1.get("turns", ""),
        "A1_COST": gate1.get("cost", ""),
        "A2_OUTCOME": gate2.get("outcome", ""),
        "A2_TURNS": gate2.get("turns", ""),
        "A2_COST": gate2.get("cost", ""),
    })
    proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                          capture_output=True, text=True)
    comment = td / "qa-comment.md"
    return proc, comment.read_text() if comment.exists() else ""


def comment_for(execution, verdict_text=None, **kwargs) -> str:
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        gate2 = gate_outputs(td, execution, verdict_text)
        proc, body = run_post(td, gate2, **kwargs)
        assert proc.returncode == 0, proc.stderr
        return body


# ── 1. the router sends #364 to the multi-pass strategy ────────────────────

class SizeRouterTest(unittest.TestCase):
    def test_a_pull_request_the_size_of_364_is_not_routed_one_pass(self):
        """The headline criterion. 18 files / 2,059 lines is the ONE size
        that demonstrably cannot be finished in 80 turns."""
        label, files, lines = PR_364
        self.assertEqual(strategy_for(files, lines), "large",
                         f"{label} ({files} files / {lines:,} lines) is still "
                         f"routed to the one-pass review it could not finish")

    def test_it_routes_large_off_the_pr_totals_alone(self):
        """The compare record can be absent (an API blip writes `{}`); the
        `gh pr view` totals must be enough on their own."""
        _, files, lines = PR_364
        m = pss.measure(None, {"changedFiles": files, "additions": lines,
                               "deletions": 0})
        self.assertEqual(pss.choose(m), "large")

    def test_every_review_the_critic_finished_keeps_the_one_pass_path(self):
        """The one-pass path works and the card is explicit that it must not
        be removed — every PR under the ceiling used it successfully."""
        for label, files, lines in COMPLETED:
            with self.subTest(pr=label):
                self.assertEqual(strategy_for(files, lines), "standard")

    def test_the_threshold_sits_between_the_proven_and_the_failed(self):
        """A threshold picked from data, not from the current constant: above
        everything that completed, below the thing that did not."""
        worst_files = max(f for _, f, _ in COMPLETED)
        worst_lines = max(ln for _, _, ln in COMPLETED)
        _, bad_files, bad_lines = PR_364
        self.assertGreaterEqual(pss.LARGE_FILES, worst_files)
        self.assertGreaterEqual(pss.LARGE_LINES, worst_lines)
        self.assertLess(pss.LARGE_FILES, bad_files)
        self.assertLess(pss.LARGE_LINES, bad_lines)

    def test_the_large_path_still_gets_the_bigger_turn_budget(self):
        """Routing above the threshold is only a fix if the path it routes to
        has the turns the one-pass path ran out of."""
        std_first, std_retry = pss.turn_budget("standard")
        big_first, big_retry = pss.turn_budget("large")
        self.assertGreater(big_first, std_first)
        self.assertGreater(big_retry, std_retry)

    def test_raising_max_turns_was_not_the_fix(self):
        """The card's explicit `Do not`: a larger budget on an unbounded diff
        moves the wall. The standard one-pass ceiling is unchanged."""
        self.assertEqual(pss.turn_budget("standard"), (80, 120))


class TheLargeBlockIsHonestAtTheBottomOfItsBandTest(unittest.TestCase):
    """The strategy block the critic reads makes a claim about the PR's size,
    and lowering the entry threshold moved the bottom of the band under it.

    It used to open "several times larger than any change reviewed here in
    one pass" — true of #297 at 118 files, false of the 11-file pull request
    that now takes this path. The prompt is what the reviewer believes about
    the diff in front of it; a false premise there is not a cosmetic one.
    """

    def block_for(self, files, lines) -> str:
        m = pss.measure(compare(spread(files, lines)), None)
        self.assertEqual(pss.choose(m), "large")
        return pss.strategy_context("large", m, "364")

    def test_a_pull_request_just_over_the_line_is_not_called_several_times_larger(self):
        block = self.block_for(pss.LARGE_FILES + 1, pss.LARGE_LINES + 100)
        self.assertNotIn("several times larger", block)

    def test_it_still_forbids_the_single_exhaustive_pass(self):
        block = self.block_for(pss.LARGE_FILES + 1, pss.LARGE_LINES + 100)
        self.assertRegex(block, r"(?i)do not.*single.*pass")
        self.assertIn("--name-only", block)

    def test_it_names_running_out_of_turns_as_a_way_the_one_pass_fails(self):
        """#297 finished early; #364 ran out of turns. Both end in no
        verdict and the reviewer is being told to avoid both."""
        self.assertRegex(self.block_for(18, 2_059).lower(), r"out of turns")


class ThresholdCarriesItsEvidenceTest(unittest.TestCase):
    """Criterion 2. The number that was wrong here was wrong *and*
    untraceable — nothing beside it said which reviews it was derived from.
    The replacement has to carry its measurement where the next person to
    change it will read it."""

    def evidence_block(self) -> str:
        """The comment block immediately above the LARGE_FILES constant."""
        source = (SCRIPTS / "pr_size_strategy.py").read_text()
        head, sep, _ = source.partition("LARGE_FILES")
        self.assertTrue(sep, "LARGE_FILES is gone — update this test")
        # Everything back to the last blank line: the comment attached to it.
        return head.split("\n\n")[-1]

    def test_the_failing_pull_request_is_named_beside_the_constant(self):
        block = self.evidence_block()
        self.assertIn("364", block)
        self.assertIn("18", block)
        self.assertIn("2,059", block)

    def test_the_completed_reviews_are_named_beside_the_constant(self):
        block = self.evidence_block()
        for label, _, _ in COMPLETED:
            with self.subTest(pr=label):
                self.assertIn(label.lstrip("#"), block)

    def test_it_says_the_constraint_is_turns(self):
        """The whole diagnosis in one word: the threshold is in files and
        lines, the thing that runs out is turns."""
        self.assertRegex(self.evidence_block().lower(), r"\bturns?\b")


# ── 2. the gate calls turn exhaustion by its name ──────────────────────────

class GateOutcomeTest(unittest.TestCase):
    def test_a_turn_ceiling_death_is_not_reported_as_a_crash(self):
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), TURN_EXHAUSTED, UNFINISHED)
        self.assertEqual(parsed["outcome"], "turn_exhaustion")

    def test_a_turn_ceiling_death_carries_its_turns_and_cost(self):
        """`completed_no_verdict` has published numbers since DRE-2465 and
        this path published none — which is how a run that spent 62 turns and
        $2.56 came to be described as having done no inference."""
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), TURN_EXHAUSTED, UNFINISHED)
        self.assertEqual(parsed["turns"], "62")
        self.assertEqual(parsed["cost"], "2.56")

    def test_the_auth_death_is_still_a_crash(self):
        """The fingerprint this gate was built for is untouched."""
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), AUTH_DEATH)
        self.assertEqual(parsed["outcome"], "crash")

    def test_the_auth_death_publishes_its_own_fingerprint(self):
        """1 turn and $0 IS the stale-token signature — printing it is what
        lets the next reader confirm the diagnosis instead of guessing it."""
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), AUTH_DEATH)
        self.assertEqual(parsed["turns"], "1")
        self.assertEqual(parsed["cost"], "0.00")

    def test_a_completed_run_is_still_completed_no_verdict(self):
        ran = {"type": "result", "subtype": "success", "is_error": False,
               "num_turns": 25, "total_cost_usd": 3.3887}
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), ran)
        self.assertEqual(parsed["outcome"], "completed_no_verdict")

    def test_a_missing_execution_record_is_still_unknown(self):
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(Path(raw), None)
        self.assertEqual(parsed["outcome"], "unknown")
        self.assertNotIn("turns", parsed)

    def test_a_ceiling_death_that_finished_its_verdict_still_passes(self):
        """DRE-2422's exception is untouched: a review that hit the ceiling
        with a COMPLETE verdict already written keeps it."""
        with tempfile.TemporaryDirectory() as raw:
            parsed = gate_outputs(
                Path(raw), TURN_EXHAUSTED,
                "VERDICT: APPROVE\n\n## Summary\nAll good.\n")
        self.assertEqual(parsed["outcome"], "ok")

    def test_no_field_can_inject_another_step_output(self):
        """$GITHUB_OUTPUT is line-oriented; `real` is the merge-relevant key
        and must stay unforgeable from anything the execution file carries."""
        hostile = dict(TURN_EXHAUSTED)
        hostile["num_turns"] = "62\nreal=true"
        hostile["subtype"] = "error_max_turns\nreal=true"
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            gate_outputs(td, hostile, UNFINISHED)
            raw_text = (td / "github_output").read_text()
        self.assertNotIn("real=true", raw_text)
        for line in raw_text.splitlines():
            self.assertRegex(line, r"^(outcome|turns|cost)=[\w.]*$")


class SpendScalarsTest(unittest.TestCase):
    """The numbers a FAILING run spent, whitelisted the same way the clean
    run's are (execution_result.py's discipline: numbers only, never the
    agent's own prose)."""

    def test_a_dead_run_still_reports_what_it_spent(self):
        scalars = execution_result.spend_scalars(TURN_EXHAUSTED)
        self.assertEqual(scalars["num_turns"], 62)
        self.assertEqual(scalars["total_cost_usd"], 2.5637)

    def test_only_numbers_survive(self):
        hostile = dict(TURN_EXHAUSTED, num_turns="62\nreal=true",
                       result="whatever the agent said about the diff")
        scalars = execution_result.spend_scalars(hostile)
        self.assertNotIn("num_turns", scalars)
        self.assertNotIn("result", scalars)
        for key, value in scalars.items():
            self.assertIsInstance(value, (int, float), key)
            self.assertNotIsInstance(value, bool, key)

    def test_no_record_spends_nothing(self):
        self.assertEqual(execution_result.spend_scalars(None), {})
        self.assertEqual(execution_result.spend_scalars("nonsense"), {})


# ── 3. what the pull request is actually told ──────────────────────────────

class TurnExhaustionMessageTest(unittest.TestCase):
    """The real post block from qa-review.yml, executed, on the #364 path."""

    def body(self) -> str:
        return comment_for(TURN_EXHAUSTED, UNFINISHED)

    def test_it_never_claims_a_startup_or_auth_failure(self):
        """The criterion, and the three days it cost. The run authenticated,
        did real work and was billed for it."""
        body = self.body()
        for false_claim in ("startup/auth failure", "no inference",
                            "crashed twice", "infra error"):
            with self.subTest(claim=false_claim):
                self.assertNotIn(false_claim, body)

    def test_it_names_turn_exhaustion_as_the_cause(self):
        self.assertRegex(self.body().lower(), r"turn (ceiling|budget)|out of turns|turn exhaustion")

    def test_it_states_the_turns_and_the_cost(self):
        body = self.body()
        self.assertIn("62", body)
        self.assertIn("2.56", body)

    def test_it_says_out_loud_that_no_credential_needs_rotating(self):
        body = self.body().lower()
        self.assertIn("not an authentication", body)
        self.assertRegex(body, r"credential")

    def test_it_names_the_remedy_that_actually_works(self):
        """A retry hits the same wall. The next move is a smaller pull
        request — that is what resolved #364."""
        self.assertRegex(self.body().lower(), r"split|smaller")

    def test_a_ceiling_death_on_either_attempt_is_enough(self):
        """The retry is the one that gets the higher ceiling; a first attempt
        that merely completed empty must not hide a retry that exhausted."""
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            first = {"outcome": "completed_no_verdict", "turns": "29",
                     "cost": "4.21"}
            second = {"outcome": "turn_exhaustion", "turns": "118",
                      "cost": "5.02"}
            proc, body = run_post(td, second, gate1=first)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("startup/auth failure", body)
        for number in ("29", "4.21", "118", "5.02"):
            with self.subTest(number=number):
                self.assertIn(number, body)


class TheCrashWordingStillDescribesACrashTest(unittest.TestCase):
    """DRE-2282 quotes the crash sentence as evidence of a genuine auth
    death. It must still be exactly what a genuine auth death produces."""

    def test_a_real_auth_death_keeps_the_current_wording(self):
        body = comment_for(AUTH_DEATH)
        self.assertIn("could not run (infra error)", body)
        self.assertIn("crashed twice (startup/auth failure, no inference)",
                      body)

    def test_no_execution_record_at_all_keeps_the_crash_wording(self):
        self.assertIn("crashed twice (startup/auth failure, no inference)",
                      comment_for(None))

    def test_the_crash_notice_now_carries_the_runs_own_numbers(self):
        """The auth death's numbers ARE its fingerprint (instant, 1 turn,
        $0). Publishing them on every failure is what makes the next
        misdiagnosis checkable instead of arguable."""
        body = comment_for(AUTH_DEATH)
        self.assertIn("1 turn", body)
        self.assertIn("0.00", body)


class MergeGateContractTest(unittest.TestCase):
    """The two rules the new branch may not break, pinned on every path."""

    def _bodies(self) -> list[str]:
        return [comment_for(TURN_EXHAUSTED, UNFINISHED),
                comment_for(AUTH_DEATH),
                comment_for(None)]

    def test_every_neutral_comment_carries_the_qa_critic_marker(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertIn("QA Critic", body.splitlines()[0])

    def test_no_neutral_comment_can_be_read_as_an_approval(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertNotIn("VERDICT: APPROVE", body)
                self.assertNotIn("VERDICT: REQUEST_CHANGES", body)

    def test_every_neutral_comment_still_says_it_is_not_a_rejection(self):
        for body in self._bodies():
            with self.subTest(body=body[:40]):
                self.assertIn("not a code rejection", body.lower())
                self.assertIn("not a request for changes", body.lower())

    def test_a_hostile_turn_count_is_never_executed(self):
        """The gate's outputs reach a double-quoted shell string. They are
        numbers by construction; this proves nothing executes if that ever
        stops being true."""
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            marker = td / "PWNED"
            gate2 = {"outcome": "turn_exhaustion",
                     "turns": f"$(touch {marker})`touch {marker}`",
                     "cost": "2.56"}
            proc, body = run_post(td, gate2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(marker.exists(), "a gate output was executed as shell")
        self.assertIn("QA Critic", body)

    def test_the_turn_exhaustion_notice_is_not_the_medics_infra_marker(self):
        """`medic_classify.CRITIC_NEUTRAL_MARKER` is the string that means
        "the critic itself declared an INFRA failure" — back off, do not
        diagnose. Turn exhaustion is not that, and must not borrow it."""
        import medic_classify  # noqa: E402

        self.assertNotIn(medic_classify.CRITIC_NEUTRAL_MARKER,
                         comment_for(TURN_EXHAUSTED, UNFINISHED))


class TheJobErrorAnnotationTest(unittest.TestCase):
    """The red annotation on the run says the same thing the comment does —
    it is what the medic and the operator read first."""

    def fail_step(self) -> dict:
        for step in wf_steps():
            if "crashed on both attempts" in (step.get("run") or ""):
                return step
        raise AssertionError("the crash-failure step is gone — update this test")

    def test_the_failure_step_can_see_each_attempts_outcome(self):
        env = self.fail_step().get("env") or {}
        joined = json.dumps(env)
        self.assertIn("gate1.outputs.outcome", joined)
        self.assertIn("gate2.outputs.outcome", joined)

    def _annotation(self, **env_extra) -> str:
        with tempfile.TemporaryDirectory() as raw:
            script = Path(raw) / "fail.sh"
            run = _resolve_expressions(self.fail_step()["run"])
            self.assertNotIn("${{", run)
            script.write_text("set -uo pipefail\n" + run)
            env = dict(os.environ, A1_OUTCOME="", A2_OUTCOME="")
            env.update(env_extra)
            proc = subprocess.run(["bash", str(script)], env=env,
                                  capture_output=True, text=True)
        return proc.stdout + proc.stderr

    def test_a_turn_exhausted_run_is_not_annotated_as_a_crash(self):
        out = self._annotation(A1_OUTCOME="turn_exhaustion",
                               A2_OUTCOME="turn_exhaustion")
        self.assertIn("::error::", out)
        self.assertNotIn("crashed on both attempts", out)
        self.assertRegex(out.lower(), r"turn")

    def test_a_genuine_crash_keeps_its_annotation(self):
        out = self._annotation(A1_OUTCOME="crash", A2_OUTCOME="crash")
        self.assertIn("crashed on both attempts", out)


if __name__ == "__main__":
    unittest.main()
