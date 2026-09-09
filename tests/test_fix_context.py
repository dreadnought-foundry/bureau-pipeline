"""The comment-triggered start gate (DRE-3451).

An `**Operator decision**` comment used to restart nothing. agent-fix's
job-level `if` admitted a comment start ONLY from the qa-bot carrying
`VERDICT: REQUEST_CHANGES` (DRE-1988), so a human's answer fired a run that
skipped in one second and the real restart waited for the 15-minute reconcile
sweep (`restart_answered_blockers`, DRE-2409). Observed on PR #2365,
2026-09-08: decision 16:13 PT → restart 16:28 PT; decision 17:20 PT → fix run
17:30 PT. Two handoffs, 25 idle minutes, one PR.

The gate is now two halves. The job `if` admits any **User**-authored PR
comment — cheap, and evaluated before any step can mint a token — and
`fix_context --decision-trigger` decides, over the REST thread, whether the run
does anything. The security reason the gate was narrow is answered rather than
loosened: the decision must be authored by a User, must LEAD its first line
with the decision phrase, must be newer than the last worker-bot 🛑 blocker,
and must not already be consumed. Those are `is_decision_body`,
`operator_decision`, `decision_consumed` and `standing_decision` — the four
predicates the sweep already reads, so "the comment started the loop" and "the
sweep would have started the loop" cannot disagree.

This suite drives the CLI the workflow step actually runs, over a captured REST
thread, and pins the idempotence that keeps the sweep from dispatching a second
run fifteen minutes later: the receipt the proceeding run posts is composed
through the one writer, and `standing_decision` over the resulting thread
returns None.
"""

import copy
import json
import os
import subprocess  # nosec B404 — fixed-arg CLI invocation of our own script
import sys
import unittest

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import fix_context  # noqa: E402
import pipeline_act  # noqa: E402

FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "fix-decision-thread-pr2365.json"
)
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "agent-fix.yml")

WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"

# The restart act's idempotency key, read off the registry that declares it
# (config/pipeline-acts.json) — never retyped here, and never a second
# constant: reconcile.DECISION_RESTART_TAG and the workflow body are the two
# emitters and both key on this one string.
RESTART_TAG = pipeline_act.tag("fix-loop-restarted")


def thread() -> list:
    """The captured thread: two rejected rounds, a 🛑 hold, and a standing
    human answer to it."""
    with open(FIXTURE, encoding="utf-8") as fh:
        return fix_context.flatten_pages(json.load(fh))


def comment(login: str, body: str, kind: str = "User") -> dict:
    return {
        "user": {"login": login, "type": kind},
        "body": body,
        "created_at": "2026-09-08T23:30:00Z",
    }


def decide(comments: list) -> tuple:
    """Run the CLI the workflow step runs. Returns (verdict, reason line)."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "thread.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(comments, fh)
        proc = subprocess.run(  # nosec B603 — fixed argv, shell=False
            [sys.executable, os.path.join(SCRIPTS, "fix_context.py"),
             "--decision-trigger", "--comments-file", path,
             "--worker-login", WORKER],
            capture_output=True, text=True, check=False,
        )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    return lines[0], "\n".join(lines[2:])


class DecisionTriggerCliTest(unittest.TestCase):
    """PROCEED for a standing answer; SKIP naming the predicate otherwise."""

    def test_a_standing_unconsumed_user_decision_proceeds(self):
        verdict, reason = decide(thread())
        self.assertEqual(verdict, fix_context.TRIGGER_PROCEED, reason)
        self.assertIn(fix_context.TRIGGER_STANDING, reason)

    def test_a_bot_authored_decision_skips(self):
        # DRE-1988/1995: authorship decides meaning. The same words from a bot
        # are not a decision, however exactly they are typed.
        comments = thread()[:-1] + [
            comment("github-actions[bot]",
                    thread()[-1]["body"], kind="Bot")
        ]
        verdict, reason = decide(comments)
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_BOT_AUTHOR, reason)

    def test_a_decision_older_than_the_latest_blocker_skips(self):
        # The answer has to answer something. A decision the loop escalated
        # PAST is stale: a newer 🛑 blocker outranks it.
        comments = thread() + [
            comment(WORKER,
                    "🛑 Fix attempt 4 blocked: this still needs a call.",
                    kind="Bot")
        ]
        verdict, reason = decide(comments)
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_BEFORE_BLOCKER, reason)

    def test_a_comment_that_merely_mentions_the_phrase_skips(self):
        # The near miss (DRE-2409) — the shape that burned both live
        # incidents. It is refused, and the refusal names it.
        comments = thread()[:-1] + [
            comment("smeed652",
                    "I will post an operator decision once I have read the "
                    "critic's second round.")
        ]
        verdict, reason = decide(comments)
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_MENTION_ONLY, reason)

    def test_a_decision_already_receipted_skips(self):
        # Idempotence in the other direction: the sweep's restart receipt (or
        # this job's own) means the loop has already moved on this answer.
        comments = thread() + [
            comment(WORKER,
                    f"🔓 {RESTART_TAG}: an operator "
                    "decision landed after the last blocker.", kind="Bot")
        ]
        verdict, reason = decide(comments)
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_CONSUMED, reason)

    def test_a_thread_with_no_blocker_skips(self):
        # No escalation, nothing to answer — a stray decision comment on a
        # healthy PR steers nothing (the render's own rule, DRE-2030).
        comments = [c for c in thread()
                    if not (c.get("body") or "").startswith("🛑")]
        verdict, reason = decide(comments)
        self.assertEqual(verdict, fix_context.TRIGGER_SKIP)
        self.assertIn(fix_context.SKIP_NO_BLOCKER, reason)

    def test_every_skip_names_a_predicate_and_says_why(self):
        for slug in fix_context.SKIP_REASONS:
            with self.subTest(slug=slug):
                self.assertTrue(fix_context.SKIP_REASONS[slug].strip())

    def test_a_malformed_payload_fails_loud(self):
        # exit 2, never a silent "no decision" — a thread we could not read is
        # exactly the deadlock this path exists to remove.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "thread.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            proc = subprocess.run(  # nosec B603 — fixed argv, shell=False
                [sys.executable, os.path.join(SCRIPTS, "fix_context.py"),
                 "--decision-trigger", "--comments-file", path,
                 "--worker-login", WORKER],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_the_render_path_is_untouched(self):
        # The flag is additive: the step that renders the fix-loop thread
        # still works exactly as it did (DRE-2030).
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            comments = os.path.join(td, "thread.json")
            out = os.path.join(td, "fix-thread.md")
            with open(comments, "w", encoding="utf-8") as fh:
                json.dump(thread(), fh)
            proc = subprocess.run(  # nosec B603 — fixed argv, shell=False
                [sys.executable, os.path.join(SCRIPTS, "fix_context.py"),
                 "--comments-file", comments, "--worker-login", WORKER,
                 "--out", out],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rendered = open(out, encoding="utf-8").read()
        self.assertIn("## Operator decision", rendered)


class ThePredicatesAreTheSweepsTest(unittest.TestCase):
    """One reading, two callers. The CLI answers PROCEED exactly when the
    sweep's own `standing_decision` finds an answer to dispatch on."""

    def test_proceed_agrees_with_standing_decision(self):
        cases = {
            "standing": thread(),
            "consumed": thread() + [comment(
                WORKER, f"🔓 {RESTART_TAG}: picked up.",
                kind="Bot")],
            "bot author": thread()[:-1] + [comment(
                QA, thread()[-1]["body"], kind="Bot")],
        }
        for label, comments in cases.items():
            with self.subTest(case=label):
                verdict, _ = decide(comments)
                standing = fix_context.standing_decision(comments, WORKER)
                self.assertEqual(
                    verdict == fix_context.TRIGGER_PROCEED, standing is not None
                )


class RestartReceiptIsIdempotentTest(unittest.TestCase):
    """AC3: the proceeding run posts the sweep's own receipt through the one
    writer, so the sweep sees the answer as consumed and dispatches nothing
    fifteen minutes later."""

    def receipt_body(self) -> str:
        """The body the workflow's receipt step actually posts, read out of
        the shipped YAML rather than retyped — a reworded receipt that stops
        carrying the tag has to fail here."""
        doc = yaml.safe_load(open(WORKFLOW, encoding="utf-8"))
        step = next(
            s for s in doc["jobs"]["fix"]["steps"]
            if s.get("id") == "restart_receipt"
        )
        return step["run"]

    def test_the_workflow_composes_through_the_one_writer(self):
        run = self.receipt_body()
        self.assertIn("pipeline_act.py receipt fix-loop-restarted", run)
        self.assertIn(RESTART_TAG, run)
        self.assertIn("--body-file", run)

    def test_the_receipt_leaves_no_standing_decision(self):
        composed = pipeline_act.receipt(
            "fix-loop-restarted",
            f"🔓 {RESTART_TAG}: the operator decision "
            "started the fix loop directly.",
        )
        after = thread() + [comment(WORKER, composed, kind="Bot")]
        self.assertIsNotNone(fix_context.standing_decision(thread(), WORKER))
        self.assertIsNone(fix_context.standing_decision(after, WORKER))
        self.assertEqual(decide(after)[0], fix_context.TRIGGER_SKIP)

    def test_a_receipt_from_anyone_else_does_not_consume_the_answer(self):
        # DRE-1995: identity decides meaning. A human quoting the tag must not
        # be able to cancel their own answer.
        planted = copy.deepcopy(thread()) + [
            comment("someone-else",
                    f"🔓 {RESTART_TAG}: nice try.")
        ]
        self.assertIsNotNone(fix_context.standing_decision(planted, WORKER))


if __name__ == "__main__":
    unittest.main()
