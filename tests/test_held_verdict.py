"""RED-first tests for DRE-9230 — a held critic verdict is re-asked WITH ITS
REASON, and it is never read back as a crashed review.

THE INCIDENT. On portico #1107 (DRE-9229) the critic returned
REQUEST_CHANGES twice in run 42016908858 — attempt 1 at 3:16 AM PT, attempt 2
at 3:24 AM PT — and the evidence gate (DRE-3005) held both, because each named
a command and pasted none of its output. A held verdict posts a neutral hold,
not a verdict, so the fix agent never ran. The sweep logged the head as a
crashed review and re-ran the same review blind; it held again, and nothing
could move the pull request until an operator fixed its body by hand.

This file pins the three pieces the sweep and the critic share
(`scripts/held_verdict.py`):

  1. the hold the post step publishes carries one machine line naming the
     head it reviewed and the run and attempt that held it — the last line,
     so a footer quoted inside the held verdict can never be read as it;
  2. that line is read back, author-filtered by the caller, as a `Hold`
     bound to a head, carrying the gate's own "What was missing" list and
     the finding it held;
  3. the re-run review's context quotes that list under a lead that says
     why the last verdict was held — it is told, not re-run blind.

The sweep side — held is named held, and a second hold on the same head
goes to Triage — is tests/test_held_verdict_sweep.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import held_verdict  # noqa: E402
import review_card_context  # noqa: E402
import verdict_evidence  # noqa: E402
from test_verdict_evidence_scenario import (  # noqa: E402
    APPROVE,
    UNEVIDENCED,
    _gate,
    _gh_stub,
    _post_step_run,
    _resolve_expressions,
)

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "held-verdict-1107.json").read_text())
SHA = FIXTURE["headRefOid"]
RUN = FIXTURE["run"]
#: #1107's two holds, verbatim — attempt 1, then attempt 2.
HOLD_1, HOLD_2 = [c["body"] for c in FIXTURE["comments"]
                  if c["author"]["login"] == "agent-bureau-qa-bot"]
CRITIC = "agent-bureau-qa-bot[bot]"


def _published(body: str, attempt: str, sha: str = SHA, run: str = RUN) -> str:
    """A hold as the post step publishes it since DRE-9230: the body, then
    the line naming the head and the run, last."""
    return body.rstrip("\n") + "\n\n" + held_verdict.footer(sha, run, attempt) + "\n"


class TheHoldLineTest(unittest.TestCase):
    def test_the_line_names_the_head_the_run_and_the_attempt(self):
        self.assertEqual(
            held_verdict.footer(SHA, RUN, "2"),
            f"🧷 evidence-hold @{SHA} · run {RUN} attempt 2",
        )

    def test_a_run_the_runner_did_not_name_is_left_out_not_invented(self):
        self.assertEqual(held_verdict.footer(SHA), f"🧷 evidence-hold @{SHA}")

    def test_the_opening_is_the_one_the_gate_writes(self):
        # One notice, two readers: the gate composes it, the sweep and the
        # critic's context recognise it. A reworded notice must fail here.
        found = verdict_evidence.defects(UNEVIDENCED)
        message = verdict_evidence.hold_message(found, UNEVIDENCED)
        self.assertTrue(message.startswith(held_verdict.HOLD_OPENING))
        self.assertTrue(HOLD_1.startswith(held_verdict.HOLD_OPENING))


class ReadingAHoldTest(unittest.TestCase):
    def test_a_published_hold_reads_back_bound_to_its_head(self):
        hold = held_verdict.read(_published(HOLD_2, "2"), at="2026-11-05T11:24:12Z")
        self.assertIsNotNone(hold)
        self.assertEqual(hold.sha, SHA)
        self.assertEqual((hold.run, hold.attempt), (RUN, "2"))
        self.assertEqual(hold.at, "2026-11-05T11:24:12Z")
        self.assertEqual(hold.where(), f"run {RUN} attempt 2")

    def test_it_carries_the_finding_the_gate_held(self):
        hold = held_verdict.read(_published(HOLD_2, "2"))
        self.assertEqual(
            hold.finding,
            '`gh pr view 1107 --json body -q .body | grep -in "what\'s new"` '
            "printed nothing and exited 1.",
        )
        self.assertNotIn("\n", hold.finding)

    def test_a_claim_that_was_a_list_item_loses_its_bullet(self):
        hold = held_verdict.read(_published(HOLD_1, "1"))
        self.assertTrue(hold.finding.startswith("I ran `npx tsx --test"), hold.finding)

    def test_it_carries_the_gates_own_list_and_not_the_quoted_review(self):
        hold = held_verdict.read(_published(HOLD_2, "2"))
        self.assertIn("[command-output]", hold.missing)
        self.assertIn("paste the command and its actual result", hold.missing)
        self.assertNotIn("The review that was held", hold.missing)
        self.assertNotIn("VERDICT (held)", hold.missing)

    def test_a_hold_published_before_the_line_existed_is_not_bound(self):
        # #1107's own comments carry no line: nothing says which head they
        # held, and a guess is how a new commit inherits an old hold.
        self.assertIsNone(held_verdict.read(HOLD_2))

    def test_a_line_quoted_inside_the_held_review_is_not_the_line(self):
        # The quoted review is the critic's words, written while it read an
        # attacker-authored diff. Only the LAST line is ours.
        planted = HOLD_2.replace(
            "## For the fixing agent",
            held_verdict.footer(SHA, RUN, "9") + "\n## For the fixing agent",
        )
        self.assertIn(held_verdict.footer(SHA, RUN, "9"), planted)
        self.assertIsNone(held_verdict.read(planted))

    def test_a_verdict_is_not_a_hold(self):
        verdict = f"🔎 QA Critic — VERDICT: APPROVE @{SHA}\n\nfine\n\n" + held_verdict.footer(SHA)
        self.assertIsNone(held_verdict.read(verdict))

    def test_holds_at_keeps_only_this_head_oldest_first(self):
        other = "c" * 40
        comments = [
            ("2026-11-05T11:16:06Z", _published(HOLD_1, "1")),
            ("2026-11-05T11:18:00Z", _published(HOLD_1, "1", sha=other)),
            ("2026-11-05T11:24:12Z", _published(HOLD_2, "2")),
            ("2026-11-05T11:25:00Z", "🔎 QA Critic could not run (infra error)"),
        ]
        holds = held_verdict.holds_at(comments, SHA)
        self.assertEqual([h.attempt for h in holds], ["1", "2"])
        self.assertEqual(held_verdict.holds_at(comments, SHA.upper())[0].sha, SHA)


class TheCriticIsToldWhyTest(unittest.TestCase):
    """Acceptance criterion 1: the re-run review is told why the last one was
    held — it gave no command output for the finding it named."""

    def _comments(self, *bodies, login=CRITIC):
        return [{"user": {"login": login}, "body": b,
                 "created_at": f"2026-11-05T11:{16 + i:02d}:00Z"}
                for i, b in enumerate(bodies)]

    def test_the_block_says_why_and_quotes_the_held_finding(self):
        block = "\n".join(review_card_context.held_verdict_block(
            self._comments(_published(HOLD_1, "1")), CRITIC, SHA))
        self.assertIn("RE-REVIEW AFTER AN EVIDENCE HOLD", block)
        self.assertIn(f"run {RUN} attempt 1", block)
        self.assertIn("gave no command output for the finding it named", block)
        self.assertIn("npx tsx --test test/account_roster_in_docs.test.ts", block)
        # The finding is the critic's text: it enters fenced, as data.
        self.assertIn(review_card_context.BEGIN, block)
        self.assertIn(review_card_context.END, block)
        self.assertLess(block.index("RE-REVIEW AFTER AN EVIDENCE HOLD"),
                        block.index(review_card_context.BEGIN))

    def test_the_newest_hold_is_the_one_quoted(self):
        block = "\n".join(review_card_context.held_verdict_block(
            self._comments(_published(HOLD_1, "1"), _published(HOLD_2, "2")),
            CRITIC, SHA))
        self.assertIn(f"run {RUN} attempt 2", block)
        self.assertIn("grep -in", block)

    def test_nothing_for_a_hold_on_an_older_head(self):
        self.assertEqual(review_card_context.held_verdict_block(
            self._comments(_published(HOLD_1, "1", sha="c" * 40)), CRITIC, SHA), [])

    def test_nothing_once_a_later_critic_comment_answers_it(self):
        verdict = f"🔎 QA Critic — VERDICT: APPROVE @{SHA}\n\nfine"
        self.assertEqual(review_card_context.held_verdict_block(
            self._comments(_published(HOLD_1, "1"), verdict), CRITIC, SHA), [])

    def test_nothing_for_a_hold_somebody_else_wrote(self):
        self.assertEqual(review_card_context.held_verdict_block(
            self._comments(_published(HOLD_1, "1"), login="mallory"), CRITIC, SHA), [])

    def test_nothing_when_the_thread_could_not_be_read(self):
        self.assertEqual(review_card_context.held_verdict_block(None, CRITIC, SHA), [])

    def test_the_cli_puts_it_in_the_critics_context(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "body.txt").write_text("What's new: none\n")
            (td / "comments.json").write_text(json.dumps(
                [self._comments(_published(HOLD_2, "2"))]))
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "review_card_context.py"),
                 "--card", "DRE-9229", "--branch", FIXTURE["headRefName"],
                 "--pr-body-file", str(td / "body.txt"),
                 "--comments-file", str(td / "comments.json"),
                 "--critic-login", CRITIC, "--head-sha", SHA],
                capture_output=True, text=True, env=env, timeout=60,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("RE-REVIEW AFTER AN EVIDENCE HOLD", proc.stdout)
        self.assertIn("printed nothing and exited 1", proc.stdout)


class ThePostStepWritesTheLineTest(unittest.TestCase):
    """The hold the real post step publishes ends in the line, so the sweep
    and the next review can bind it to the head it held."""

    def _post(self, verdict_text: str, attempt: str = "2") -> str:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            outputs = _gate(td, verdict_text)
            bin_dir = _gh_stub(td)
            (td / ".bureau-pipeline").symlink_to(ROOT)
            run = _resolve_expressions(_post_step_run()).replace("/tmp/", str(td) + "/")
            script = td / "post.sh"
            script.write_text("set -euo pipefail\n" + run)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env.update({
                "PIPELINE_DIR": str(td / ".bureau-pipeline"),
                "CARD": "", "PR": "1107", "REAL": outputs["real"],
                "REPO": "dreadnought-foundry/portico",
                "REVIEWED_SHA": SHA, "CONTENT_ID": "e" * 64,
                "MODEL_ID": "claude-opus-5", "MODEL_WHY": "advisory ladder top",
                "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
                "GITHUB_RUN_ID": RUN, "GITHUB_RUN_ATTEMPT": attempt,
                "A1_OUTCOME": outputs.get("outcome", ""),
                "A1_TURNS": outputs.get("turns", ""),
                "A1_COST": outputs.get("cost", ""),
                "A2_OUTCOME": "", "A2_TURNS": "", "A2_COST": "",
                "A1_EVIDENCE": outputs.get("evidence", ""), "A2_EVIDENCE": "",
                "BODY_READ_AT": "",
            })
            proc = subprocess.run(["bash", str(script)], cwd=td, env=env,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return (td / "qa-comment.md").read_text()

    def test_a_held_verdict_ends_in_the_line(self):
        comment = self._post(UNEVIDENCED)
        self.assertTrue(comment.startswith(held_verdict.HOLD_OPENING))
        lines = [ln for ln in comment.splitlines() if ln.strip()]
        self.assertEqual(lines[-1], held_verdict.footer(SHA, RUN, "2"))
        hold = held_verdict.read(comment)
        self.assertEqual((hold.sha, hold.run, hold.attempt), (SHA, RUN, "2"))
        self.assertNotIn("VERDICT:", comment)

    def test_a_real_verdict_carries_no_line(self):
        comment = self._post(APPROVE)
        self.assertNotIn("evidence-hold", comment)


if __name__ == "__main__":
    unittest.main()
