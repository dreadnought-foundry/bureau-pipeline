"""Tests for agent-fix's convergence halt (DRE-2024, DRE-4848).

Belt-and-braces companion to reconcile's human-park dispatch gate: even when
something DOES dispatch agent-fix (a stale sweep, merge-gate's conflict leg,
a manual workflow_dispatch), the workflow itself must refuse to run when the
last fix attempts on this PR ended in no-push infra failures on the SAME
head sha — that's a convergence failure (max-turns exhaustion: DeltaSolv
PR #120 died at "Reached maximum number of turns (60)" five times in one
evening), not a retryable blip. is_error deaths already have their own cap
(fix_dead_run.py); this closes the previously-uncapped escalate path.

DRE-4848 (Portico PR #687 / DRE-4146, 2026-09-24). Two fix runs pushed
nothing at `e719f03e`, the operator answered with an `Operator decision` at
20:52 PT, the sweep acknowledged it — and the halt refused the run it had
promised, then posted the IDENTICAL halt comment about forty times, once a
sweep, until 07:20 PT. Two defects, both expressed here:

  1. A person's decision did not clear the halt. It now does, ONCE: a
     decision by a person, newer than the newest halt on the commit, lets
     exactly one more run go at that commit. If that run also pushes
     nothing the halt stands again, posted once for the new round.
  2. The once-per-commit receipt did not hold. The card suspected an
     unpaginated read; the read was already paginated (`--paginate --slurp
     ...?per_page=100`, pinned below and proved on a 141-comment thread).
     The cause is WHO posted it: the Resolve step's GH_TOKEN is the pool
     reader (DRE-4282), so the halt went up as agent-bureau-bot-2/3/4 while
     the receipt check counted only agent-bureau-bot[bot]. It never found
     its own halts. The halt is now posted on the writer token, and a halt
     by any bot of the loop's own pool counts — so the ones already standing
     on live PRs keep suppressing a repeat.

The halt decision is fix_convergence.halt(); the workflow calls it through the
`halt` CLI and these tests run both against the same threads.
"""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fix_convergence as fc  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
SCRIPT = os.path.join(ROOT, "scripts", "fix_convergence.py")
PR687 = os.path.join(ROOT, "tests", "fixtures", "fix-halt-thread-pr687-2026-09-24.json")

WORKER_BOT = "agent-bureau-bot[bot]"
POOL_BOT = "agent-bureau-bot-3[bot]"
SHA8 = "d9f2c1ab"
PR687_SHA8 = "e719f03e"
HALT_MARKER = "fix-convergence-halt"


def no_progress(sha8=SHA8, attempt=1) -> str:
    """The exact no-progress body the Report step posts (sha in backticks)."""
    return (
        f"🛑 Fix attempt {attempt} pushed no new commit (branch still at "
        f"`{sha8}`) — the reviewer will not re-run and the last verdict "
        "stands. Escalating to a human rather than leaving this PR stuck."
    )


def halt_body(sha8=SHA8) -> str:
    return (
        f"🧯 {HALT_MARKER} @{sha8}: 2 fix runs on this exact commit already "
        "ended with nothing to show — the loop is not converging."
    )


def bot(login: str, body: str) -> dict:
    return {"user": {"login": login, "type": "Bot"}, "body": body}


def person(body: str, login: str = "smeed652") -> dict:
    return {"user": {"login": login, "type": "User"}, "body": body}


DECISION = "**Operator decision** — the approach is right; take one more run."


def workflow_src() -> str:
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def resolve_step() -> str:
    m = re.search(r"name:\s*Resolve PR.*?(?=\n      - name:|\Z)", workflow_src(), re.S)
    if not m:
        raise AssertionError("Resolve step not found in agent-fix.yml")
    return m.group(0)


def halt_branch() -> str:
    """The `if [ "$HALT" = "true" ]` block, to its OUTER fi (10 spaces)."""
    m = re.search(r'if \[ "\$HALT" = "true" \];.*?\n          fi\n', resolve_step(), re.S)
    if not m:
        raise AssertionError("halt branch not found in the Resolve step")
    return m.group(0)


def decide(comments, sha8=SHA8):
    return fc.halt(comments, sha8, WORKER_BOT)


def run_cli(pages, sha8=SHA8, worker=WORKER_BOT):
    """Run the real `halt` CLI over a --slurp shaped file; return (rc, env)."""
    with tempfile.TemporaryDirectory() as tmp:
        thread = os.path.join(tmp, "thread.json")
        env_out = os.path.join(tmp, "halt.env")
        with open(thread, "w", encoding="utf-8") as fh:
            json.dump(pages, fh)
        proc = subprocess.run(
            [sys.executable, SCRIPT, "halt", "--comments-file", thread,
             "--worker-login", worker, "--sha8", sha8, "--env-out", env_out],
            capture_output=True, text=True,
        )
        values = {}
        if os.path.exists(env_out):
            with open(env_out, encoding="utf-8") as fh:
                for line in fh.read().splitlines():
                    key, _, value = line.partition("=")
                    values[key] = value
        return proc.returncode, values


def load_pr687():
    with open(PR687, encoding="utf-8") as fh:
        return json.load(fh)


class HaltSourcePinTest(unittest.TestCase):
    """Source pins: the Resolve step carries the halt gate, through the CLI."""

    def test_resolve_step_asks_the_halt_cli(self):
        step = resolve_step()
        self.assertIn("scripts/fix_convergence.py halt", step)
        self.assertIn('--comments-file "$TMPD/thread.json"', step)
        # The worker's login from THIS run's token, never a literal (DRE-1988).
        self.assertIn('--worker-login "$WORKER_LOGIN"', step)
        self.assertIn('--sha8 "$SHA8"', step)
        # Sourced from a file of fixed-vocabulary values, never eval'd.
        self.assertIn('. "$TMPD/halt.env"', step)

    def test_the_old_inline_counts_are_gone(self):
        # Two jq counts keyed on one hardcoded login were the defect: the
        # receipt check never saw the halts the pool bots posted.
        step = resolve_step()
        self.assertNotIn('contains("fix-convergence-halt")', step)
        self.assertNotIn("HALTED=", step)

    def test_the_thread_read_is_paginated_in_full(self):
        # The card's suspect, checked rather than assumed: the one read the
        # halt decides on asks for every page, 100 at a time.
        step = resolve_step()
        m = re.search(r'--out "\$TMPD/thread\.json" \\\n\s*gh api --paginate --slurp \\\n\s*"([^"]+)"', step)
        self.assertIsNotNone(m, "thread.json is not read with gh api --paginate --slurp")
        self.assertIn("per_page=100", m.group(1))

    def test_halt_refuses_dispatch(self):
        block = halt_branch()
        self.assertIn('echo "go=false"', block)
        self.assertIn("exit 0", block)

    def test_halt_comment_posts_only_when_the_cli_says_so(self):
        block = halt_branch()
        self.assertIn('if [ "$POST_HALT" = "true" ]; then', block)
        # The receipt's idempotency key, and the act registry's anchor for it.
        self.assertIn("🧯 fix-convergence-halt @$SHA8:", block)

    def test_halt_is_posted_as_the_writer_not_the_pool_reader(self):
        # The proved cause of the forty repeats: GH_TOKEN here is the pool
        # reader, so a halt posted on it is authored by whichever pool bot
        # was selected. The post goes out on the boot App's token, the
        # identity the loop's own markers are counted by.
        m = re.search(
            r"- name: Resolve PR, mode, and attempt budget\n.*?\n        env:\n(.*?)\n        run: \|",
            workflow_src(), re.S,
        )
        self.assertIsNotNone(m)
        self.assertIn("WRITER_TOKEN: ${{ steps.app.outputs.token }}", m.group(1))
        self.assertIn('GH_TOKEN="$WRITER_TOKEN" gh pr comment "$PR"', halt_branch())

    def test_the_halt_says_how_a_person_releases_it(self):
        self.assertIn("Operator decision", halt_branch())


class HaltDecisionTest(unittest.TestCase):
    """fix_convergence.halt() — the halt, the receipt, and the decision."""

    def test_no_markers_no_halt(self):
        got = decide([])
        self.assertFalse(got.halted)
        self.assertFalse(got.post)
        self.assertEqual(got.noprog, 0)

    def test_one_no_push_run_does_not_halt(self):
        self.assertFalse(decide([bot(WORKER_BOT, no_progress())]).halted)

    def test_two_no_push_runs_halt_and_post(self):
        got = decide([bot(WORKER_BOT, no_progress())] * 2)
        self.assertTrue(got.halted)
        self.assertTrue(got.post)
        self.assertEqual(got.noprog, 2)

    def test_a_standing_halt_is_not_posted_again(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [bot(WORKER_BOT, halt_body())]
        got = decide(thread)
        self.assertTrue(got.halted)
        self.assertFalse(got.post)

    def test_markers_for_an_older_sha_do_not_count(self):
        # The branch moved: prior failures belong to a different head and
        # must not freeze the fresh attempt.
        thread = [bot(WORKER_BOT, no_progress())] * 5
        self.assertFalse(decide(thread, sha8="0badf00d").halted)

    def test_forged_no_push_markers_are_invisible(self):
        thread = [
            person(no_progress(), login="mallory"),
            bot("agent-bureau-qa-bot[bot]", no_progress()),
            bot("dependabot[bot]", no_progress()),
        ]
        self.assertEqual(decide(thread).noprog, 0)

    def test_push_and_chatter_comments_do_not_count(self):
        thread = [
            bot(WORKER_BOT, "🔧 Fix attempt 2 pushed — CI and critic review re-running."),
            person("looks stuck to me"),
        ]
        self.assertEqual(decide(thread).noprog, 0)

    # --- the decision (DRE-4848 defect 1) --------------------------------

    def test_a_decision_after_the_halt_lets_exactly_one_more_run_go(self):
        # AC1, in order: two no-push runs at X, a halt, a person's decision.
        thread = [bot(WORKER_BOT, no_progress(attempt=1)),
                  bot(WORKER_BOT, no_progress(attempt=2)),
                  bot(WORKER_BOT, halt_body()),
                  person(DECISION)]
        got = decide(thread)
        self.assertFalse(got.halted, "the decision must allow one more run")
        self.assertFalse(got.post)
        self.assertTrue(got.cleared)

        # That run pushes nothing too: the halt stands again, posted ONCE
        # for the new decision round.
        thread.append(bot(WORKER_BOT, no_progress(attempt=3)))
        got = decide(thread)
        self.assertTrue(got.halted, "a third no-push run must halt again")
        self.assertTrue(got.post)
        self.assertFalse(got.cleared)

        thread.append(bot(WORKER_BOT, halt_body()))
        got = decide(thread)
        self.assertTrue(got.halted)
        self.assertFalse(got.post, "the new round's halt is posted once")

        # And every sweep after that stays quiet.
        for _ in range(5):
            self.assertFalse(decide(thread).post)

    def test_the_decision_buys_one_run_not_a_fresh_budget_of_two(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            bot(WORKER_BOT, halt_body()), person(DECISION),
            bot(WORKER_BOT, no_progress(attempt=3)),
        ]
        self.assertTrue(decide(thread).halted)

    def test_a_decision_before_any_halt_was_posted_also_clears(self):
        # #687's own order: two no-push runs, the decision, and only then
        # the run the halt refused. The decision is newer than every halt
        # on the commit (there are none), so it buys its one run.
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [person(DECISION)]
        got = decide(thread)
        self.assertFalse(got.halted)
        self.assertTrue(got.cleared)

    def test_a_decision_older_than_the_newest_halt_does_not_clear(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            person(DECISION), bot(WORKER_BOT, no_progress(attempt=3)),
            bot(WORKER_BOT, halt_body()),
        ]
        got = decide(thread)
        self.assertTrue(got.halted)
        self.assertFalse(got.post)

    def test_a_decision_older_than_the_no_push_runs_does_not_clear(self):
        # A decision the loop already spent on an earlier run at this commit.
        thread = [person(DECISION)] + [bot(WORKER_BOT, no_progress())] * 2
        got = decide(thread)
        self.assertTrue(got.halted)
        self.assertTrue(got.post)

    def test_a_second_decision_buys_a_second_single_run(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            bot(WORKER_BOT, halt_body()), person(DECISION),
            bot(WORKER_BOT, no_progress(attempt=3)), bot(WORKER_BOT, halt_body()),
            person("Operator decision: one more, then I will take it by hand."),
        ]
        self.assertFalse(decide(thread).halted)

    def test_a_decision_by_a_bot_does_not_clear(self):
        # AC3. Authorship decides meaning (DRE-1988/1995): GitHub's own
        # user.type, never the login string.
        base = [bot(WORKER_BOT, no_progress())] * 2 + [bot(WORKER_BOT, halt_body())]
        for login in ("github-actions[bot]", "agent-bureau-qa-bot[bot]",
                      WORKER_BOT, POOL_BOT, "dependabot[bot]"):
            with self.subTest(author=login):
                got = decide(base + [bot(login, DECISION)])
                self.assertTrue(got.halted)
                self.assertFalse(got.post)
                self.assertFalse(got.cleared)

    def test_a_decision_from_a_deleted_account_does_not_clear(self):
        base = [bot(WORKER_BOT, no_progress())] * 2 + [bot(WORKER_BOT, halt_body())]
        self.assertTrue(decide(base + [{"user": None, "body": DECISION}]).halted)

    def test_a_mention_is_not_a_decision(self):
        base = [bot(WORKER_BOT, no_progress())] * 2 + [bot(WORKER_BOT, halt_body())]
        for body in ("I'll post an operator decision tomorrow.",
                     "> **Operator decision** — quoting the last one"):
            with self.subTest(body=body):
                self.assertTrue(decide(base + [person(body)]).halted)

    def test_any_decision_phrasing_the_loop_accepts_is_accepted_here(self):
        base = [bot(WORKER_BOT, no_progress())] * 2 + [bot(WORKER_BOT, halt_body())]
        for body in ("## Operator decision\nGo.", "operator decision — go",
                     "__Operator decision__: go"):
            with self.subTest(body=body):
                self.assertFalse(decide(base + [person(body)]).halted)

    # --- the receipt (DRE-4848 defect 2) ---------------------------------

    def test_a_halt_posted_by_a_pool_bot_counts_as_posted(self):
        # The proved cause: the halts on live PRs were written by the pool
        # reader. They must still suppress a repeat.
        for n in (2, 3, 4):
            with self.subTest(pool=n):
                thread = [bot(WORKER_BOT, no_progress())] * 2 + [
                    bot(f"agent-bureau-bot-{n}[bot]", halt_body())]
                got = decide(thread)
                self.assertTrue(got.halted)
                self.assertFalse(got.post)

    def test_a_pool_halt_also_closes_the_decision_window(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            person(DECISION), bot(WORKER_BOT, no_progress(attempt=3)),
            bot(POOL_BOT, halt_body())]
        got = decide(thread)
        self.assertTrue(got.halted)
        self.assertFalse(got.post)

    def test_a_forged_halt_neither_suppresses_the_receipt_nor_cancels_a_decision(self):
        forged = [person(halt_body(), login="mallory"),
                  bot("dependabot[bot]", halt_body()),
                  bot("agent-bureau-botany[bot]", halt_body())]
        got = decide([bot(WORKER_BOT, no_progress())] * 2 + forged)
        self.assertTrue(got.post)
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            bot(WORKER_BOT, halt_body()), person(DECISION)] + forged
        self.assertFalse(decide(thread).halted)

    def test_a_quoted_halt_is_not_a_halt(self):
        # Anchored at the first line: the loop's own prose that mentions the
        # marker (a hold, a summary) is not a receipt.
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            bot(WORKER_BOT, f"Earlier: {halt_body()}")]
        self.assertTrue(decide(thread).post)

    def test_a_halt_for_an_older_sha_does_not_suppress_a_fresh_one(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2 + [
            bot(WORKER_BOT, halt_body(sha8="00000000"))]
        self.assertTrue(decide(thread).post)


class PaginationTest(unittest.TestCase):
    """AC2: a thread past 100 comments, halt on page 2 — no new halt."""

    def test_a_halt_on_page_two_suppresses_the_repeat(self):
        page1 = [bot(WORKER_BOT, "⏳ merge-gate: waiting on checks.")] * 97 + [
            bot(WORKER_BOT, no_progress(attempt=1)),
            bot(WORKER_BOT, no_progress(attempt=2)),
            person(DECISION)]
        page2 = [bot(WORKER_BOT, no_progress(attempt=3)),
                 bot(WORKER_BOT, halt_body()),
                 bot(WORKER_BOT, "⏳ merge-gate: waiting on checks.")]
        self.assertEqual(len(page1), 100)
        rc, env = run_cli([page1, page2])
        self.assertEqual(rc, 0)
        self.assertEqual(env.get("HALT"), "true")
        self.assertEqual(env.get("POST_HALT"), "false")
        # Drop page 2 and the same thread reads as "no halt yet": the page
        # is what carries the answer.
        rc, env = run_cli([page1])
        self.assertEqual(env.get("HALT"), "false")

    def test_pr687_fixture_is_the_shape_the_card_describes(self):
        pages = load_pr687()
        comments = [c for page in pages for c in page]
        self.assertGreater(len(comments), 100)
        self.assertEqual(len(pages[0]), 100)
        halts = [i for i, c in enumerate(comments)
                 if (c.get("body") or "").startswith(f"🧯 {HALT_MARKER} @{PR687_SHA8}")]
        self.assertEqual(len(halts), 40, "about forty identical halts")
        self.assertGreaterEqual(min(halts), 100, "every halt is on page 2")
        noprog = [c for c in comments if "pushed no new commit" in (c.get("body") or "")
                  and PR687_SHA8 in c["body"]]
        self.assertEqual(len(noprog), 2)
        decisions = [c for c in comments if (c.get("user") or {}).get("type") == "User"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["created_at"], "2026-09-25T03:52:00Z")  # 20:52 PT

    def test_pr687_posts_no_new_halt(self):
        rc, env = run_cli(load_pr687(), sha8=PR687_SHA8)
        self.assertEqual(rc, 0)
        self.assertEqual(env.get("HALT"), "true")
        self.assertEqual(env.get("POST_HALT"), "false")

    def test_pr687_as_it_stood_when_the_decision_landed_lets_the_run_go(self):
        # Cut the thread where the first halt went up: the two no-push runs,
        # the decision, the sweep's acknowledgement. That run was refused.
        pages = load_pr687()
        comments = [c for page in pages for c in page]
        first_halt = next(i for i, c in enumerate(comments)
                          if (c.get("body") or "").startswith(f"🧯 {HALT_MARKER}"))
        cut = comments[:first_halt]
        rc, env = run_cli([cut[:100], cut[100:]], sha8=PR687_SHA8)
        self.assertEqual(rc, 0)
        self.assertEqual(env.get("HALT"), "false")
        self.assertEqual(env.get("CLEARED"), "true")

    def test_pr687_with_a_fresh_decision_gets_its_one_run(self):
        pages = copy.deepcopy(load_pr687())
        pages[-1].append(person(DECISION))
        rc, env = run_cli(pages, sha8=PR687_SHA8)
        self.assertEqual(env.get("HALT"), "false")


class HaltCliTest(unittest.TestCase):

    def test_env_values_are_a_fixed_vocabulary(self):
        thread = [bot(WORKER_BOT, no_progress())] * 2
        rc, env = run_cli([thread])
        self.assertEqual(rc, 0)
        self.assertEqual(set(env), {"HALT", "POST_HALT", "CLEARED", "NOPROG"})
        self.assertEqual(env["NOPROG"], "2")
        for key in ("HALT", "POST_HALT", "CLEARED"):
            self.assertIn(env[key], ("true", "false"))

    def test_a_flat_array_reads_like_one_page(self):
        rc, env = run_cli([bot(WORKER_BOT, no_progress())] * 2)
        self.assertEqual(env.get("HALT"), "true")

    def test_an_unreadable_thread_fails_loudly(self):
        # Never a default: an unreadable record is UNKNOWN (DRE-4157), and
        # "no halt" is the answer that runs another doomed fix.
        with tempfile.TemporaryDirectory() as tmp:
            thread = os.path.join(tmp, "thread.json")
            with open(thread, "w", encoding="utf-8") as fh:
                fh.write("API rate limit exceeded")
            proc = subprocess.run(
                [sys.executable, SCRIPT, "halt", "--comments-file", thread,
                 "--worker-login", WORKER_BOT, "--sha8", SHA8,
                 "--env-out", os.path.join(tmp, "halt.env")],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, "halt.env")))


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
