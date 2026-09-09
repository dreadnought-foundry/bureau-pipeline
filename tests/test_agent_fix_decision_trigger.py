"""agent-fix's job gate admits the operator's own comment (DRE-3451).

The gate had exactly two doors (DRE-1988): a `workflow_dispatch`, and an
`issue_comment` authored by `agent-bureau-qa-bot[bot]` carrying
`VERDICT: REQUEST_CHANGES`. That is why an operator's `**Operator decision**`
comment fired a run that skipped in one second, and why the restart it asked
for waited on the 15-minute reconcile sweep.

A third door opens here, and it is deliberately CHEAP: any **User**-authored
comment on a pull request. The expensive half — is this actually a standing,
unconsumed operator decision? — is a first step that runs `fix_context.py
--decision-trigger` over the REST thread and sets an output, because the phrase
rule (`is_decision_body`: tolerant of `**`, `##`, casing; anchored at the first
line) is a Python predicate the sweep already owns and a job `if` cannot
express without re-deriving it.

What must NOT change is the reason the gate was narrow. Anyone who can comment
could otherwise start a run holding the repo's secrets, so this suite pins the
old door shut exactly as it was: a bot comment without the verdict is still
refused, and `workflow_dispatch` is untouched. The gate is evaluated the way
GitHub evaluates it (`fix_concurrency.evaluate`), not matched as text.
"""

import json
import os
import re
import subprocess  # nosec B404 — executes this repo's own workflow step body
import sys
import tempfile
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import fix_concurrency as fc  # noqa: E402
import fix_context  # noqa: E402
import pipeline_act  # noqa: E402

# The restart act's idempotency key, off the registry that declares it.
RESTART_TAG = pipeline_act.tag("fix-loop-restarted")

WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "agent-fix.yml")
FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "fix-decision-thread-pr2365.json"
)

QA = "agent-bureau-qa-bot[bot]"
WORKER = "agent-bureau-bot[bot]"
HUMAN = "smeed652"
PR = 2365

VERDICT_BODY = "🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @abc"
DECISION_BODY = "**Operator decision** — go with the critic's reading."


def doc() -> dict:
    return yaml.safe_load(open(WORKFLOW, encoding="utf-8"))


def steps() -> list:
    return doc()["jobs"]["fix"]["steps"]


def step_with(key: str, value: str) -> dict:
    for step in steps():
        if step.get(key) == value:
            return step
    raise AssertionError(f"no step with {key}={value!r} in agent-fix.yml")


def admits(event: dict) -> bool:
    return fc.reaches_fix_agent(doc(), event)


class TheGateAdmitsAUserCommentTest(unittest.TestCase):
    """The new door, and only the new door."""

    def test_a_user_comment_on_a_pr_is_admitted(self):
        self.assertTrue(
            admits(fc.comment_event(PR, HUMAN, DECISION_BODY)),
            "a human's operator decision still cannot start the fix loop — "
            "the whole card is that it should",
        )

    def test_any_user_comment_is_admitted_because_the_step_decides(self):
        # The job `if` is cheap on purpose: the phrase rule lives in
        # fix_context.is_decision_body, which a GitHub expression cannot
        # express, so the gate admits the author class and the first step
        # applies the predicate.
        self.assertTrue(admits(fc.comment_event(PR, HUMAN, "looks good to me")))

    def test_a_bot_comment_without_the_verdict_is_still_refused(self):
        # DRE-1988's rule, kept: the fix loop's own "🔧 Fix attempt N pushed"
        # notice, a near-miss notice, github-actions — none of them may spawn
        # a code-writing agent, and none of them is a User.
        for login in (WORKER, QA, "github-actions[bot]", "dependabot[bot]"):
            with self.subTest(login=login):
                self.assertFalse(
                    admits(fc.comment_event(
                        PR, login,
                        "🔧 Fix attempt 2 pushed — CI and critic review "
                        "re-running.")),
                    f"{login} can start the fix agent by commenting",
                )

    def test_a_bot_forging_the_decision_phrase_is_still_refused(self):
        for login in (WORKER, QA, "github-actions[bot]"):
            with self.subTest(login=login):
                self.assertFalse(
                    admits(fc.comment_event(PR, login, DECISION_BODY))
                )

    def test_the_qa_bot_verdict_door_is_unchanged(self):
        self.assertTrue(admits(fc.comment_event(PR, QA, VERDICT_BODY)))

    def test_workflow_dispatch_is_unchanged(self):
        self.assertTrue(admits(fc.dispatch_event(PR)))

    def test_an_issue_comment_that_is_not_on_a_pr_is_refused(self):
        event = fc.comment_event(PR, HUMAN, DECISION_BODY)
        del event["github"]["event"]["issue"]["pull_request"]
        self.assertFalse(admits(event))


class TheFirstStepDecidesTest(unittest.TestCase):
    """The expensive half: one step, the sweep's own predicates, and a gate
    on everything downstream."""

    def test_the_decision_step_runs_the_fix_context_cli(self):
        step = step_with("id", "decision")
        self.assertIn("fix_context.py", step["run"])
        self.assertIn("--decision-trigger", step["run"])
        # REST, because REST is the only shape carrying user.type — the
        # sweep's own reason for re-fetching (reconcile.WORKER_REST_LOGIN).
        self.assertIn("issues/", step["run"])
        self.assertIn("comments", step["run"])

    def test_the_decision_step_sets_an_output(self):
        step = step_with("id", "decision")
        self.assertIn("start=", step["run"])
        self.assertIn("GITHUB_OUTPUT", step["run"])

    def test_the_decision_step_only_runs_for_a_comment_start(self):
        self.assertIn("issue_comment", str(step_with("id", "decision")["if"]))

    def test_the_decision_step_derives_the_worker_login_from_the_token(self):
        # DRE-1988 discipline: never a hardcoded name an App rename outdates.
        step = step_with("id", "decision")
        self.assertIn("app-slug", yaml.dump(step))

    def test_a_skip_gates_the_whole_job(self):
        # The Resolve step is what sets `go`; every later step reads it. If
        # the decision skips, Resolve never runs and nothing downstream can.
        resolve = step_with("id", "pr")
        self.assertIn("steps.decision.outputs.start", str(resolve["if"]))

    def test_a_dispatch_is_not_gated_by_the_decision_step(self):
        # workflow_dispatch skips the decision step entirely, so its output is
        # empty — the Resolve gate must read that as "carry on", never as a
        # skip, or every conflict repair and sweep restart dies here.
        self.assertIn("!=", str(step_with("id", "pr")["if"]))


# ── the decision step, executed for real ───────────────────────────────────
#
# Parsing the YAML proves the step is wired; it does not prove the shell in it
# works. This runs the shipped `run:` block against a stub `gh`, the way
# tests/test_fix_dispatch_clears_stale_hold.py runs Announce and Resolve —
# a gate nobody has executed is a gate nobody has tested.

GH_STUB = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[0] == "api":
    payload = json.load(open(os.environ["THREAD"]))
    print(json.dumps([payload] if "--slurp" in args else payload))
else:
    sys.stderr.write("unexpected gh call: %r\\n" % (args,))
    sys.exit(2)
'''


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ ... }}` substitutions Actions would make, and prove none
    survive — an unsubstituted expression is a hole in the harness."""
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def run_decision_step(thread: list, comment_type: str = "User") -> dict:
    """Execute the shipped 'Decide a comment-triggered start' body. Returns
    the step outputs Actions would have collected."""
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "bin"))
        stub = os.path.join(td, "bin", "gh")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(GH_STUB)
        os.chmod(stub, 0o755)  # nosec B103 — a test stub on PATH
        os.symlink(REPO_ROOT, os.path.join(td, ".bureau-pipeline"))
        thread_file = os.path.join(td, "thread.json")
        with open(thread_file, "w", encoding="utf-8") as fh:
            json.dump(thread, fh)
        out_file = os.path.join(td, "step-output")
        open(out_file, "w", encoding="utf-8").close()

        body = substitute(
            step_with("id", "decision")["run"],
            {"github.event.issue.number": str(PR), "github.repository": "o/r"},
        )
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-c", body], cwd=td, capture_output=True, text=True,
            env={
                **os.environ,
                "PATH": os.path.join(td, "bin") + os.pathsep + os.environ["PATH"],
                "GITHUB_OUTPUT": out_file, "RUNNER_TEMP": td,
                "THREAD": thread_file, "GH_TOKEN": "t",
                "WORKER_LOGIN": WORKER, "COMMENT_TYPE": comment_type,
            },
        )
        assert proc.returncode == 0, proc.stderr
        outputs = dict(
            line.split("=", 1)
            for line in open(out_file, encoding="utf-8").read().splitlines()
            if "=" in line
        )
    outputs["_stdout"] = proc.stdout
    return outputs


def held_thread() -> list:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class TheDecisionStepRunsTest(unittest.TestCase):
    """The shipped shell, executed."""

    def test_a_standing_decision_sets_start_decision(self):
        out = run_decision_step(held_thread())
        self.assertEqual(out["start"], "decision", out["_stdout"])

    def test_an_answer_already_receipted_sets_start_skip(self):
        thread = held_thread() + [{
            "user": {"login": WORKER, "type": "Bot"},
            "created_at": "2026-09-08T23:20:00Z",
            "body": f"🔓 {RESTART_TAG}: picked up.",
        }]
        out = run_decision_step(thread)
        self.assertEqual(out["start"], "skip")
        self.assertIn(fix_context.SKIP_CONSUMED, out["_stdout"])

    def test_ordinary_conversation_sets_start_skip(self):
        thread = held_thread()[:-1] + [{
            "user": {"login": HUMAN, "type": "User"},
            "created_at": "2026-09-08T23:20:00Z",
            "body": "any update on this one?",
        }]
        out = run_decision_step(thread)
        self.assertEqual(out["start"], "skip")
        self.assertIn(fix_context.SKIP_NO_DECISION, out["_stdout"])

    def test_a_bot_trigger_carries_on_into_resolve(self):
        # The qa-bot verdict door: not a decision start, and NOT a skip —
        # gating it out would kill the DRE-1988 route this card must not touch.
        out = run_decision_step(held_thread(), comment_type="Bot")
        self.assertEqual(out["start"], "verdict")

    def test_the_step_never_echoes_a_comment_body(self):
        # DRE-1996: every body on that thread is attacker-writable, and a run
        # log is a publication.
        out = run_decision_step(held_thread())
        self.assertNotIn("the critic is right about the payload",
                         out["_stdout"])


class TheProceedingRunReceiptsTheRestartTest(unittest.TestCase):
    """One act, one tag, the sweep's own writer — so the sweep stands down."""

    def test_the_receipt_step_exists_and_composes_through_pipeline_act(self):
        step = step_with("id", "restart_receipt")
        self.assertIn("pipeline_act.py receipt fix-loop-restarted", step["run"])

    def test_the_receipt_only_goes_up_on_a_decision_start(self):
        condition = str(step_with("id", "restart_receipt")["if"])
        self.assertIn("steps.decision.outputs.start", condition)
        self.assertIn("steps.pr.outputs.go == 'true'", condition)

    def test_the_receipt_goes_up_before_the_agent_runs(self):
        ids = [s.get("id") for s in steps()]
        self.assertLess(ids.index("restart_receipt"), ids.index("claude"))


if __name__ == "__main__":
    unittest.main()
