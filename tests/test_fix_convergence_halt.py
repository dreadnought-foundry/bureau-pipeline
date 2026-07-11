"""RED-first tests for agent-fix's convergence halt (DRE-2024).

Belt-and-braces companion to reconcile's human-park dispatch gate: even when
something DOES dispatch agent-fix (a stale sweep, merge-gate's conflict leg,
a manual workflow_dispatch), the workflow itself must refuse to run when the
last fix attempts on this PR ended in no-push infra failures on the SAME
head sha — that's a convergence failure (max-turns exhaustion: DeltaSolv
PR #120 died at "Reached maximum number of turns (60)" five times in one
evening), not a retryable blip. is_error deaths already have their own cap
(fix_dead_run.py); this closes the previously-uncapped escalate path.

Live-extraction harness: the tests execute the workflow's REAL jq counting
expressions against sample comment JSON (the test_agent_fix_identity_gate.py
pattern), so a drifted filter fails here, not in production.
"""

import json
import os
import re
import subprocess
import unittest

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows", "agent-fix.yml"
)

WORKER_BOT = "agent-bureau-bot[bot]"
SHA8 = "d9f2c1ab"
# The exact no-progress body the Report step posts (sha cited in backticks).
NO_PROGRESS = (
    "🛑 Fix attempt 1 pushed no new commit (branch still at `d9f2c1ab`) — "
    "the reviewer will not re-run and the last verdict stands. Escalating to "
    "a human rather than leaving this PR stuck."
)
HALT_MARKER = "fix-convergence-halt"


def workflow_src() -> str:
    return open(WORKFLOW).read()


def resolve_step() -> str:
    m = re.search(r"name:\s*Resolve PR.*?(?=\n      - name:|\Z)", workflow_src(), re.S)
    if not m:
        raise AssertionError("Resolve step not found in agent-fix.yml")
    return m.group(0)


def extract_sha_jq(marker: str) -> str:
    """Pull the single sha-parameterized jq program containing `marker` out
    of the live workflow, so the harness executes the REAL filter."""
    exprs = [
        e
        for e in re.findall(r"jq --arg sha8 \"\$SHA8\" '([^']*)'", workflow_src())
        if marker in e
    ]
    if len(exprs) != 1:
        raise AssertionError(
            f"expected exactly one sha-parameterized jq program containing "
            f"{marker!r} in agent-fix.yml, found {len(exprs)}"
        )
    return exprs[0]


def run_jq(expr: str, pages, sha8=SHA8) -> str:
    """Execute a jq program against --paginate --slurp shaped input (a JSON
    array of pages, each page an array of comments)."""
    proc = subprocess.run(
        ["jq", "--arg", "sha8", sha8, expr],
        input=json.dumps(pages),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"jq failed: {proc.stderr}")
    return proc.stdout.strip()


def comment(login: str, body: str) -> dict:
    return {"user": {"login": login}, "body": body}


class HaltSourcePinTest(unittest.TestCase):
    """Source pins: the Resolve step must carry the halt gate."""

    def test_resolve_step_counts_no_progress_markers(self):
        expr = extract_sha_jq("pushed no new commit")
        # Worker-bot-authored only (DRE-1995 discipline): a planted marker
        # must not be able to freeze the fix loop on a healthy PR.
        self.assertIn(f'.user.login == "{WORKER_BOT}"', expr)
        # Bound to the CURRENT head sha: a new commit resets the count.
        self.assertIn("contains($sha8)", expr)

    def test_halt_threshold_is_two_prior_no_push_runs(self):
        self.assertIn('[ "${NOPROG:-0}" -ge 2 ]', resolve_step())

    def test_halt_refuses_dispatch(self):
        # Inside the halt branch the step must set go=false and stop — the
        # refusal, not another 8-minute doomed run.
        step = resolve_step()
        # Anchor on the OUTER fi (10-space indent) — the once-per-sha
        # receipt guard nests its own if/fi (12-space) inside the block.
        m = re.search(
            r'if \[ "\$\{NOPROG:-0\}" -ge 2 \];.*?\n          fi\n', step, re.S
        )
        self.assertIsNotNone(m, "halt branch not found in Resolve step")
        self.assertIn('echo "go=false"', m.group(0))
        self.assertIn("exit 0", m.group(0))

    def test_halt_comment_posts_once_per_sha(self):
        # No-op LOUDLY, but only once: the halt comment is keyed on the halt
        # marker + sha, so sweep after sweep adds no new spam.
        step = resolve_step()
        self.assertIn(HALT_MARKER, step)
        self.assertIn('[ "${HALTED:-0}" -eq 0 ]', step)


class NoProgressCountHarnessTest(unittest.TestCase):
    """Execute the real no-progress counting jq against sample comment JSON."""

    def _count(self, comments, sha8=SHA8) -> int:
        return int(run_jq(extract_sha_jq("pushed no new commit"), [comments], sha8))

    def test_counts_worker_bot_markers_on_current_sha(self):
        thread = [
            comment(WORKER_BOT, NO_PROGRESS),
            comment(WORKER_BOT, NO_PROGRESS),
        ]
        self.assertEqual(self._count(thread), 2)

    def test_markers_for_an_older_sha_do_not_count(self):
        # The branch moved: prior failures belong to a different head and
        # must not freeze the fresh attempt.
        thread = [comment(WORKER_BOT, NO_PROGRESS)] * 5
        self.assertEqual(self._count(thread, sha8="0badf00d"), 0)

    def test_forged_markers_are_invisible(self):
        thread = [
            comment("mallory", NO_PROGRESS),
            comment("agent-bureau-qa-bot[bot]", NO_PROGRESS),
            comment("dependabot[bot]", NO_PROGRESS),
        ]
        self.assertEqual(self._count(thread), 0)

    def test_push_and_chatter_comments_do_not_count(self):
        thread = [
            comment(WORKER_BOT, "🔧 Fix attempt 2 pushed — CI and critic review re-running."),
            comment("someone", "looks stuck to me"),
        ]
        self.assertEqual(self._count(thread), 0)

    def test_pagination_pages_are_flattened(self):
        # gh api --paginate --slurp yields one array per page; a marker on
        # page 2 (a busy PR — #120 had 8 by 23:00Z) must still count.
        pages = [[comment(WORKER_BOT, NO_PROGRESS)], [comment(WORKER_BOT, NO_PROGRESS)]]
        self.assertEqual(int(run_jq(extract_sha_jq("pushed no new commit"), pages)), 2)


class HaltReceiptHarnessTest(unittest.TestCase):
    """Execute the real once-per-sha halt-receipt jq."""

    def _count(self, comments, sha8=SHA8) -> int:
        return int(run_jq(extract_sha_jq(HALT_MARKER), [comments], sha8))

    def test_worker_bot_halt_receipt_on_current_sha_counts(self):
        body = f"🧯 {HALT_MARKER} @{SHA8}: refusing to dispatch another identical run."
        self.assertEqual(self._count([comment(WORKER_BOT, body)]), 1)

    def test_receipt_for_an_older_sha_does_not_suppress_a_fresh_one(self):
        body = f"🧯 {HALT_MARKER} @00000000: refusing."
        self.assertEqual(self._count([comment(WORKER_BOT, body)]), 0)

    def test_forged_receipt_is_invisible(self):
        body = f"🧯 {HALT_MARKER} @{SHA8}: refusing."
        self.assertEqual(self._count([comment("mallory", body)]), 0)


class LockfileGuidanceTest(unittest.TestCase):
    """Secondary DRE-2024 fix: hand-merging a generated lockfile is what eats
    the 60-turn budget (mobile/package-lock.json on PR #120). Both conflict-
    mode instructions must say take-main's-and-regenerate instead."""

    def test_conflict_escalation_names_lockfiles_and_regeneration(self):
        step = resolve_step()
        self.assertIn("package-lock.json", step)
        self.assertIn("regenerate", step)

    def test_fix_prompt_names_lockfiles_and_regeneration(self):
        prompt = workflow_src().split("prompt: |", 1)[1]
        self.assertIn("package-lock.json", prompt)
        self.assertIn("regenerate", prompt)


if __name__ == "__main__":
    unittest.main()
