"""The instruction a blocker PRINTS must name a trigger the gate ACCEPTS
(DRE-2548).

The closing line of every agent-fix blocker used to read:

    The fix loop picks it up and restarts itself — no dispatch needed.

in the same paragraph that required the answer be "written by a person, not a
bot". Both halves cannot be true of the comment event: agent-fix's own job-if
admits an `issue_comment` start ONLY from the qa-bot, so an operator who
follows the instruction exactly fires a run that completes/SKIPPED — green at
the run level, silent on the PR, no fix. Receipts: agent-bureau PR #2065
(four skipped comment runs, 2026-08-19) and PR #2087 (skip one second after
the decision, 2026-08-19), both released only by a hand
`gh workflow run agent-fix.yml -f pr_number=<n>`.

What actually restarts the loop is the 15-minute reconcile sweep
(`restart_answered_blockers`, DRE-2409): it reads the human decision and
`workflow_dispatch`es agent-fix, which the same job-if accepts. That is the
trigger the copy has to name.

This suite is the producer/consumer pin the card asks for — a string and a
condition that must agree and were checked by nobody:

  * the example the instruction prints, pasted by a human exactly as printed,
    must drive the sweep to a dispatch;
  * that dispatch must be an event the fix gate admits, and the comment alone
    must NOT be one — so the copy may not claim it is;
  * the "about 15 minutes" the copy promises must be the sweep's real cadence;
  * every message that parks a PR or a card carries the SAME restart
    sentence, so no site can drift back to the false one.
"""

import json
import os
import re
import sys
import unittest
from unittest import mock

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import fix_context  # noqa: E402
import reconcile  # noqa: E402

WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "agent-fix.yml")
SWEEP_STUB = os.path.join(REPO_ROOT, ".github", "workflows", "self-reconcile.yml")

WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"
HUMAN = "sid-ceo"

BLOCKER = (
    "🛑 Fix attempt 3 blocked: the critic wants B, the card says A — this "
    "needs the operator's call."
)

# The false sentence, and the shape of it — no shipped copy may say either.
BANNED = ("restarts itself", "no dispatch needed")


def wf_src() -> str:
    return open(WORKFLOW, encoding="utf-8").read()


def fix_job_if() -> str:
    """The gate: agent-fix's job-level `if`, the one condition that decides
    whether a triggering event runs the fixer or skips it."""
    return yaml.safe_load(wf_src())["jobs"]["fix"]["if"]


def printed_answer(answer: str = "go with B") -> str:
    """The comment an operator writes by doing exactly what the printed
    instruction says: copy the indented example line, replace the
    placeholder. Read OUT of the shipped copy, never re-typed here — a copy
    change that breaks the parser has to fail this suite."""
    example = next(
        line.strip()
        for line in fix_context.ANSWER_FORMAT.splitlines()
        if line.startswith("    ") and line.strip()
    )
    return example.replace("<your answer here>", answer)


def rest(login, body):
    """A comment in REST shape — what fix_context parses and the sweep
    fetches."""
    return {
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "body": body,
        "created_at": "2026-08-19T20:00:43Z",
    }


def sweep(comments):
    """Run the answered-blocker sweep against a fake GitHub holding one open
    agent PR whose thread is `comments`. Returns the dispatches it made."""
    dispatches = []
    prs = [{"number": 2065, "headRefName": "agent/DRE-2548-x",
            "mergeStateStatus": "BLOCKED"}]

    def gh(*args):
        if args[:2] == ("run", "list"):
            return "[]"
        if args[:2] == ("pr", "list"):
            return json.dumps(prs)
        if args[0] == "api":
            return json.dumps(comments)
        return ""

    with mock.patch.object(reconcile, "gh", side_effect=gh), \
         mock.patch.object(reconcile, "gh_dispatch",
                           side_effect=lambda *a: dispatches.append(a)), \
         mock.patch.object(reconcile, "_post_pr_note", return_value=True), \
         mock.patch.object(reconcile, "card_parked_for_human", return_value=True), \
         mock.patch.object(reconcile, "linear_ops", mock.MagicMock()):
        reconcile.restart_answered_blockers()
    return dispatches


class PrintedInstructionDrivesTheGateTest(unittest.TestCase):
    """The instruction's own example, followed literally, reaches a dispatch."""

    def test_the_printed_answer_restarts_the_loop(self):
        dispatches = sweep([rest(WORKER, BLOCKER), rest(HUMAN, printed_answer())])
        self.assertEqual(len(dispatches), 1, "the printed answer restarted nothing")
        joined = " ".join(dispatches[0])
        self.assertIn("agent-fix.yml", joined)
        self.assertIn("pr_number=2065", joined)

    def test_the_sweep_starts_the_fixer_the_way_the_gate_admits(self):
        # `gh workflow run` IS a workflow_dispatch — the one start the fix
        # gate accepts besides a qa-bot verdict comment.
        dispatches = sweep([rest(WORKER, BLOCKER), rest(HUMAN, printed_answer())])
        self.assertEqual(dispatches[0][:2], ("workflow", "run"))
        self.assertIn("github.event_name == 'workflow_dispatch'", fix_job_if())

    def test_the_comment_alone_is_not_a_trigger_the_gate_admits(self):
        # The premise of the whole card: the comment path is bot-gated, so a
        # human "Operator decision" comment can only ever skip.
        gate = fix_job_if()
        self.assertIn(f"github.event.comment.user.login == '{QA}'", gate)
        self.assertIn("VERDICT: REQUEST_CHANGES", gate)

    def test_the_instruction_never_claims_the_comment_restarts_it(self):
        for phrase in BANNED:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, fix_context.ANSWER_FORMAT)

    def test_the_instruction_names_the_sweep_and_warns_about_the_skip(self):
        self.assertIn(fix_context.RESTART_PROMISE, fix_context.ANSWER_FORMAT)
        self.assertIn(fix_context.SKIP_NOTICE, fix_context.ANSWER_FORMAT)
        self.assertIn("skip", fix_context.SKIP_NOTICE.lower())

    def test_the_promised_wait_is_the_sweeps_real_cadence(self):
        # "about 15 minutes" is a claim about a cron, so read the cron. (The
        # raw text, not yaml.safe_load: YAML 1.1 parses the `on:` key as the
        # boolean True, which is a worse thing to hardcode than a regex.)
        stub = open(SWEEP_STUB, encoding="utf-8").read()
        self.assertRegex(stub, r'cron:\s*"\*/15 \* \* \* \*"')
        self.assertIn("15 minutes", fix_context.RESTART_PROMISE)


class AuthorshipAgreementTest(unittest.TestCase):
    """The copy demands a person, not a bot — and the gate that consumes it
    must want the same thing. Contradicting halves are what made following
    the instruction correctly guarantee the skip."""

    def test_the_copy_asks_for_a_human_author(self):
        self.assertIn("written by a person, not a bot", fix_context.ANSWER_FORMAT)

    def test_a_human_authored_answer_is_accepted(self):
        self.assertEqual(
            len(sweep([rest(WORKER, BLOCKER), rest(HUMAN, printed_answer())])), 1
        )

    def test_a_bot_authored_copy_of_the_same_words_is_not(self):
        for login in (WORKER, QA, "github-actions[bot]"):
            with self.subTest(login=login):
                self.assertEqual(
                    sweep([rest(WORKER, BLOCKER), rest(login, printed_answer())]), []
                )


class HoldingMessagesCarryOneRestartSentenceTest(unittest.TestCase):
    """One sentence, every site. The two inline blocker bodies in the
    workflow cannot call fix_context (they run before the pipeline
    checkout), so they are pinned to the constant instead of reading it."""

    def bodies(self, marker: str) -> str:
        m = re.search(re.escape(marker) + r'(.*?)"', wf_src(), re.S)
        self.assertIsNotNone(m, f"comment body starting {marker!r} not found")
        return m.group(1)

    HOLDS = ("🛑 Fix budget exhausted", "🛑 Conflict-resolution budget exhausted")

    def test_pr_holds_carry_the_promise_and_the_skip_notice(self):
        for marker in self.HOLDS:
            with self.subTest(marker=marker):
                body = self.bodies(marker)
                self.assertIn(fix_context.RESTART_PROMISE, body)
                self.assertIn(fix_context.SKIP_NOTICE, body)

    def test_the_dispute_card_comment_carries_the_promise(self):
        self.assertIn(
            fix_context.RESTART_PROMISE, self.bodies("🙋 The fix agent disagrees")
        )

    def test_the_workflow_no_longer_promises_a_self_restart(self):
        src = wf_src()
        for phrase in BANNED:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, src)

    def test_fix_context_only_quotes_the_false_sentence_in_a_comment(self):
        # The retired wording may survive as the note explaining WHY it was
        # retired; it may not survive in anything printed to an operator.
        for i, line in enumerate(
            open(os.path.join(REPO_ROOT, "scripts", "fix_context.py"),
                 encoding="utf-8").read().splitlines(), 1
        ):
            for phrase in BANNED:
                if phrase in line:
                    self.assertTrue(
                        line.lstrip().startswith("#"),
                        f"fix_context.py:{i} still prints {phrase!r}: {line}",
                    )

    def test_the_new_sentences_survive_a_double_quoted_shell_body(self):
        for name in ("RESTART_PROMISE", "SKIP_NOTICE"):
            text = getattr(fix_context, name)
            for ch in ("`", "$", '"', "\\"):
                with self.subTest(constant=name, ch=ch):
                    self.assertNotIn(ch, text)


if __name__ == "__main__":
    unittest.main()
