"""RED-first tests for the shared bot-branch publisher (DRE-3879).

THE INCIDENT. `split-ledger.yml` and `model-drift.yml` each ended in
`git push origin HEAD:main`. Branch protection on `main` does not admit the
bureau App for a direct push, so GitHub answered `GH006: Protected branch
update failed` — "Changes must be made through a pull request" — and the daily
ledger job failed that way every day since the card was opened, the weekly
drift job since at least 2026-08-10. Both jobs did their work correctly and
then could not save it.

The CEO's signed answer (2026-09-14 08:14 PT) chose the remedy: open a pull
request, change no branch protection. This module pins the mechanism both jobs
now share — one module, because two hand-written copies of "commit, push, open
a PR if there isn't one" is how the eight copies of the branch-ownership test
came to give four different answers (DRE-2426).

What is pinned here, and why each line exists:

  1. **It never pushes to the base.** The whole bug. Proved by watching
     `origin/main` across every case below, not by grepping the source for a
     string.
  2. **One branch, one pull request.** A second run with a second change
     UPDATES the open pull request; it does not open another. A job that
     opened a PR per run would file 365 of them a year.
  3. **Nothing changed ⇒ nothing happens, and the run is green.** No commit,
     no branch, no pull request, exit 0.
  4. **The staged set is PROVEN, not asserted.** The guarantee that made these
     scheduled jobs safe to give `contents: write` — stage the generated paths
     by name, read the index back, refuse anything else — moved into this
     module with the rest of the step, so it is tested here rather than in two
     copies of shell.
  5. **"Could not tell" is never "no pull request."** An unreadable PR list
     must not open a second one (the DRE-2034 shape); it is a red run instead.
  6. **The pull request body names the branch it is on**, which is the card's
     own acceptance criterion: a reader who sees one of these PRs must not have
     to guess which scheduled job produced it.
"""

import os
import subprocess  # nosec B404 — fixed-arg calls against a temp repo
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import bot_branch_harness as H  # noqa: E402
import bot_branch_pr  # noqa: E402

SCRIPT = ROOT / "scripts" / "bot_branch_pr.py"
LEDGER = "config/split-ledger.json"
DOC = "docs/split-ledger.md"


class PrBodyTest(unittest.TestCase):
    """The body is what a human (and the critic) reads first."""

    def test_the_body_names_the_branch_it_is_on(self):
        body = bot_branch_pr.pr_body("why this exists", "bot/split-ledger", "main")
        self.assertIn("bot/split-ledger", body)

    def test_the_body_keeps_the_callers_own_explanation(self):
        body = bot_branch_pr.pr_body("regenerated daily by the ledger job",
                                     "bot/split-ledger", "main")
        self.assertIn("regenerated daily by the ledger job", body)

    def test_the_body_names_the_base_it_targets(self):
        body = bot_branch_pr.pr_body("x", "bot/model-drift", "main")
        self.assertIn("main", body)


class StagedSetTest(unittest.TestCase):
    """The model-drift guarantee, now in one place: exactly the named paths."""

    def test_nothing_unexpected_is_no_offence(self):
        self.assertEqual(
            bot_branch_pr.unexpected_staged([LEDGER, DOC], [LEDGER, DOC]), [])

    def test_a_third_path_is_named(self):
        self.assertEqual(
            bot_branch_pr.unexpected_staged([LEDGER, "agents.yaml"],
                                            [LEDGER, DOC]),
            ["agents.yaml"])

    def test_a_path_that_merely_starts_the_same_is_unexpected(self):
        # Whole-line equality, never a prefix: `models.json.bak` is not
        # `models.json`, and a prefix test would wave it through.
        self.assertEqual(
            bot_branch_pr.unexpected_staged(["models.json.bak"], ["models.json"]),
            ["models.json.bak"])


class TheBranchIsNeverTheBaseTest(unittest.TestCase):
    """The structural half of "never pushes to main": the publisher refuses to
    be pointed at its own base, so no edit to a workflow can turn it back into
    the direct push this card removes."""

    def test_publishing_onto_the_base_is_refused(self):
        with self.assertRaises(ValueError):
            bot_branch_pr.publish(repo="o/r", branch="main", base="main",
                                  paths=["models.json"], title="t", body="b",
                                  run=lambda *a, **k: (0, "", ""))

    def test_an_empty_branch_is_refused(self):
        with self.assertRaises(ValueError):
            bot_branch_pr.publish(repo="o/r", branch="", base="main",
                                  paths=["models.json"], title="t", body="b",
                                  run=lambda *a, **k: (0, "", ""))


class PublishRunsTest(unittest.TestCase):
    """The publisher, RUN — a real repository, a real `origin`, a fake GitHub.

    Every case asserts what `origin/main` did, because "it no longer pushes to
    main" is the card and a source-level assertion would not prove it.
    """

    BRANCH = "bot/split-ledger"

    def setUp(self):
        self.repo = H.BotBranchRepo(self.addCleanup)
        self.repo.write(LEDGER, '{"generated_at": "2026-09-15T04:41:00Z"}\n')
        self.repo.write(DOC, "# The split ledger\n\nGenerated 2026-09-15.\n")
        self.repo.write("agents.yaml", "# a file this job may never touch\n")
        self.repo.seed()
        self.before = self.repo.origin_ref("main")

    def publish(self, *extra):
        script = (
            f"python3 scripts/bot_branch_pr.py publish "
            f"--repo \"$GITHUB_REPOSITORY\" --branch {self.BRANCH} "
            f"--base main --path {LEDGER} --path {DOC} "
            f"--title 'chore(ledger): regenerate the split ledger' "
            f"--body 'the ledger, regenerated' " + " ".join(extra)
        )
        return self.repo.run(script)

    def _regenerate(self, stamp: str):
        self.repo.write(LEDGER, '{"generated_at": "%s"}\n' % stamp)
        self.repo.write(DOC, f"# The split ledger\n\nGenerated {stamp}.\n")

    def test_a_change_lands_on_the_bot_branch_and_never_on_main(self):
        self._regenerate("2026-09-16T04:41:00Z")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.repo.origin_ref("main"), self.before,
                         "main moved — this is the push branch protection "
                         "refuses, and the card exists to remove it")
        head = self.repo.origin_ref(self.BRANCH)
        self.assertTrue(head, f"{self.BRANCH} never reached origin")
        self.assertEqual(self.repo.origin_files(self.BRANCH), sorted([LEDGER, DOC]))
        self.assertIn("agent-bureau-bot[bot]", self.repo.origin_log(self.BRANCH))

    def test_it_opens_one_pull_request_naming_the_branch(self):
        self._regenerate("2026-09-16T04:41:00Z")
        self.publish()
        creates = self.repo.pr_creates()
        self.assertEqual(len(creates), 1, creates)
        call = creates[0]
        self.assertEqual(self.repo.flag(call, "--head"), self.BRANCH)
        self.assertEqual(self.repo.flag(call, "--base"), "main")
        self.assertIn(self.BRANCH, self.repo.flag(call, "--body"))

    def test_a_second_run_updates_the_open_pull_request(self):
        """The acceptance criterion in one test: never a new PR per run. A
        daily job that opened one each morning would file 365 a year, and the
        critic would review every one of them."""
        self._regenerate("2026-09-16T04:41:00Z")
        self.assertEqual(self.publish().returncode, 0)
        first = self.repo.origin_ref(self.BRANCH)
        self._regenerate("2026-09-17T04:41:00Z")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.repo.pr_creates()), 1,
                         "a second pull request was opened for the same branch")
        self.assertNotEqual(self.repo.origin_ref(self.BRANCH), first,
                            "the open pull request was not updated")
        self.assertEqual(self.repo.origin_ref("main"), self.before)

    def test_a_run_with_nothing_changed_opens_no_pull_request_and_is_green(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.repo.pr_creates(), [])
        self.assertEqual(self.repo.origin_ref(self.BRANCH), "",
                         "an empty derivation pushed a branch anyway")
        self.assertEqual(self.repo.origin_ref("main"), self.before)

    def test_it_refuses_when_anything_else_is_staged(self):
        """A path an earlier step left behind, or a later edit to a workflow,
        is refused — and nothing is pushed anywhere."""
        self._regenerate("2026-09-16T04:41:00Z")
        self.repo.write("agents.yaml", "# a quietly rewritten ladder\n")
        H.git("add", "agents.yaml", cwd=self.repo.work)
        result = self.publish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("agents.yaml", result.stdout + result.stderr)
        self.assertEqual(self.repo.origin_ref(self.BRANCH), "")
        self.assertEqual(self.repo.origin_ref("main"), self.before)
        self.assertEqual(self.repo.pr_creates(), [])

    def test_an_untracked_neighbour_is_not_swept_in(self):
        self._regenerate("2026-09-16T04:41:00Z")
        self.repo.write("stray.txt", "left behind by some other step\n")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("stray.txt", self.repo.origin_files(self.BRANCH))

    def test_an_unreadable_pull_request_list_never_opens_a_second_one(self):
        """"GitHub would not say" is not "there is no PR" (DRE-2034). The work
        is on the branch either way; the run goes red so the next one retries
        instead of filing a duplicate."""
        self._regenerate("2026-09-16T04:41:00Z")
        self.repo.fail_pr_list = True
        result = self.publish()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.repo.pr_creates(), [])
        self.assertEqual(self.repo.origin_ref("main"), self.before)


class TheSourceNeverNamesTheBaseAsAPushTargetTest(unittest.TestCase):
    """The belt to the behavioural braces above: `HEAD:main` was the literal
    that failed every day, and no file in this repo may carry it again."""

    def test_no_workflow_or_script_pushes_head_to_main(self):
        offenders = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")) + \
                sorted((ROOT / "scripts").glob("*.py")):
            if "HEAD:main" in path.read_text():
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [],
                         "a direct push to main is back — branch protection "
                         "refuses it with GH006 and the job fails daily")


class TheScriptIsExecutableTest(unittest.TestCase):
    def test_it_answers_help_without_a_repository(self):
        done = subprocess.run(  # nosec B603 — fixed args
            [sys.executable, str(SCRIPT), "publish", "--help"],
            capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--branch", done.stdout)


if __name__ == "__main__":
    unittest.main()
