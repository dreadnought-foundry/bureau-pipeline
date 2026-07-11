"""RED-first tests for the TDD commit-discipline check (DRE-2022).

Origin: bureau-pipeline's own PRs are hand-built (not dispatched), so TDD was
enforced only by convention in builder prompts plus the critic's judgment.
The rail's discipline — fail tests → implementation → checks → PR → critic →
merge — needs a mechanical enforcer here too.

The fix is a cheap, deterministic script (no LLM call) run as a job in the
Pipeline Tests workflow: on the PR's ordered commit list, at least one commit
touching files under `tests/` must appear BEFORE the first commit that changes
non-test code. Docs-only and ops-only PRs are exempt, classified by changed
paths. The failure message says exactly what's missing in plain language.

These tests must FAIL before scripts/check_tdd_commits.py exists / before
tests.yml gains the job, and PASS after.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SCRIPT = ROOT / "scripts" / "check_tdd_commits.py"

sys.path.insert(0, str(ROOT / "scripts"))

import check_tdd_commits  # noqa: E402


def commit(paths, subject="a commit"):
    """A commit record the way the checker consumes them (oldest-first list)."""
    return {"sha": "f" * 40, "subject": subject, "paths": list(paths)}


class ClassifyPathTest(unittest.TestCase):
    """Path → category. The categories drive both the ordering rule (test vs
    code) and the exemption (docs-only / ops-only PRs)."""

    def test_tests_tree_is_test(self):
        self.assertEqual(check_tdd_commits.classify_path("tests/test_x.py"), "test")

    def test_scripts_tree_is_code(self):
        self.assertEqual(check_tdd_commits.classify_path("scripts/reconcile.py"), "code")

    def test_docs_tree_is_docs(self):
        self.assertEqual(check_tdd_commits.classify_path("docs/self-hosting.md"), "docs")

    def test_any_markdown_is_docs(self):
        # README, standards/, briefs/ — prose lives in .md wherever it sits.
        self.assertEqual(check_tdd_commits.classify_path("README.md"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("standards/comms.md"), "docs")
        self.assertEqual(check_tdd_commits.classify_path("briefs/engineer.md"), "docs")

    def test_github_tree_is_ops(self):
        self.assertEqual(
            check_tdd_commits.classify_path(".github/workflows/tests.yml"), "ops"
        )

    def test_config_tree_is_ops(self):
        self.assertEqual(check_tdd_commits.classify_path("config/repos.yml"), "ops")

    def test_agents_registry_is_ops(self):
        self.assertEqual(check_tdd_commits.classify_path("agents.yaml"), "ops")

    def test_unknown_paths_default_to_code(self):
        # Fail-closed: anything unrecognized counts as implementation, so a
        # new source tree can't silently dodge the discipline.
        self.assertEqual(check_tdd_commits.classify_path("relay/handler.py"), "code")


class CheckCommitsTest(unittest.TestCase):
    """The ordering rule on an oldest-first commit list."""

    # --- the acceptance cases, verbatim from the card -------------------

    def test_red_test_then_fix_passes(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["tests/test_widget.py"], "test(DRE-1): RED"),
            commit(["scripts/widget.py"], "fix(DRE-1): make it pass"),
        ])
        self.assertTrue(ok)

    def test_implementation_first_fails_with_plain_language_message(self):
        ok, reason = check_tdd_commits.check_commits([
            commit(["scripts/widget.py"], "fix(DRE-1): implementation"),
            commit(["tests/test_widget.py"], "test(DRE-1): after the fact"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_docs_only_pr_passes_without_a_test_commit(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/self-hosting.md", "README.md"], "docs: notes"),
        ])
        self.assertTrue(ok)

    # --- exemption boundaries --------------------------------------------

    def test_ops_only_pr_passes_without_a_test_commit(self):
        ok, _ = check_tdd_commits.check_commits([
            commit([".github/workflows/reconcile.yml"], "ops: tweak schedule"),
        ])
        self.assertTrue(ok)

    def test_mixed_docs_and_ops_pr_is_still_exempt(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/adr.md", ".github/workflows/medic.yml"], "chore"),
        ])
        self.assertTrue(ok)

    def test_tests_only_pr_passes(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["tests/test_more_coverage.py"], "test: backfill"),
        ])
        self.assertTrue(ok)

    def test_empty_commit_list_passes(self):
        ok, _ = check_tdd_commits.check_commits([])
        self.assertTrue(ok)

    def test_docs_beside_code_does_not_exempt(self):
        # A README edit riding along with implementation is NOT a docs-only
        # PR — the code still needs a preceding test commit.
        ok, reason = check_tdd_commits.check_commits([
            commit(["README.md", "scripts/widget.py"], "feat: with docs"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    # --- split-commit discipline: SAME commit is not "before" ------------

    def test_test_and_code_in_one_commit_fails(self):
        # The standard is split commits: history must SHOW the test existed
        # before the fix. A mixed commit proves nothing about order.
        ok, reason = check_tdd_commits.check_commits([
            commit(["tests/test_widget.py", "scripts/widget.py"], "feat: all at once"),
        ])
        self.assertFalse(ok)
        self.assertEqual(reason, check_tdd_commits.FAILURE_MESSAGE)

    def test_test_commit_after_first_code_commit_does_not_rescue(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["scripts/a.py"], "fix: impl"),
            commit(["tests/test_a.py"], "test: late"),
            commit(["scripts/b.py"], "fix: more impl"),
        ])
        self.assertFalse(ok)

    def test_docs_commits_before_the_red_test_are_harmless(self):
        ok, _ = check_tdd_commits.check_commits([
            commit(["docs/plan.md"], "docs: plan"),
            commit(["tests/test_widget.py"], "test: RED"),
            commit(["scripts/widget.py"], "fix: green"),
        ])
        self.assertTrue(ok)

    def test_failure_message_is_the_exact_plain_language_string(self):
        # The card pins the wording — a builder reading the red check must be
        # told exactly what's missing, no jargon.
        self.assertEqual(
            check_tdd_commits.FAILURE_MESSAGE,
            "no test commit precedes the implementation — commit the RED test first",
        )


class DependabotExemptionTest(unittest.TestCase):
    """DRE-2049: dependabot-authored PRs are exempt from the RED-test-first
    rule. A dependency bump has no behavior of its own to test — its proof is
    the whole suite running against the bumped pins (the `unit` job installs
    from requirements-dev.txt). Without this, every dependabot PR carries a
    permanent red check and the merge gate can never auto-merge a
    critic-APPROVEd minor (live: bp #93, 2026-07-11)."""

    def test_dependabot_author_login_shapes_all_count(self):
        # Same normalization as reconcile.is_dependabot_pr: GraphQL surfaces
        # "dependabot", REST "dependabot[bot]", gh's bot marker "app/dependabot".
        for login in ("dependabot", "dependabot[bot]", "app/dependabot"):
            self.assertTrue(
                check_tdd_commits.is_dependabot_author(login),
                f"login shape {login!r} is dependabot and must be exempt",
            )

    def test_human_and_agent_authors_are_not_exempt(self):
        for login in ("alice", "agent-bureau-bot", "", None):
            self.assertFalse(check_tdd_commits.is_dependabot_author(login))

    def test_dependabot_impersonating_substring_is_not_exempt(self):
        # The match is exact on the normalized login — a user account NAMED
        # to look like the bot must not dodge the discipline.
        for login in ("notdependabot", "dependabot-fan", "dependabot2[bot]"):
            self.assertFalse(check_tdd_commits.is_dependabot_author(login))


class GitCliTest(unittest.TestCase):
    """End-to-end against a real (temp) git repo, invoked the way the
    workflow invokes it: `check_tdd_commits.py <base> <head>`. Exit 0 = pass,
    1 = discipline violation, 2 = cannot evaluate (fail loud, never pass)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("README.md", "seed")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: seed")

    def tearDown(self):
        self._td.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def write(self, rel, content):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def add_commit(self, rel, msg):
        self.write(rel, f"content for {msg}")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def run_check(self, base="main", head="HEAD", author=None):
        env = {**os.environ}
        env.pop("PR_AUTHOR", None)
        if author is not None:
            env["PR_AUTHOR"] = author
        return subprocess.run(
            [sys.executable, str(SCRIPT), base, head],
            cwd=self.repo, capture_output=True, text=True, env=env,
        )

    def test_test_first_branch_exits_0(self):
        self.git("checkout", "-q", "-b", "agent/DRE-1-x")
        self.add_commit("tests/test_widget.py", "test(DRE-1): RED")
        self.add_commit("scripts/widget.py", "fix(DRE-1): green")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_implementation_first_branch_exits_1_with_message(self):
        self.git("checkout", "-q", "-b", "agent/DRE-2-x")
        self.add_commit("scripts/widget.py", "fix(DRE-2): impl first")
        self.add_commit("tests/test_widget.py", "test(DRE-2): late")
        p = self.run_check()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(
            "no test commit precedes the implementation — commit the RED test first",
            p.stdout,
        )

    def test_docs_only_branch_exits_0(self):
        self.git("checkout", "-q", "-b", "docs/DRE-3-notes")
        self.add_commit("docs/notes.md", "docs(DRE-3): notes")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_merging_advanced_main_into_the_branch_does_not_flag(self):
        # main moves on (someone else's code-only squash-merge) while the
        # branch is open; the branch merges main back in. Those mainline
        # commits are NOT the PR's own work and must not trip the check.
        self.git("checkout", "-q", "-b", "agent/DRE-4-x")
        self.add_commit("tests/test_widget.py", "test(DRE-4): RED")
        self.add_commit("scripts/widget.py", "fix(DRE-4): green")
        self.git("checkout", "-q", "main")
        self.add_commit("scripts/other.py", "feat: unrelated mainline work")
        self.git("checkout", "-q", "agent/DRE-4-x")
        self.git("merge", "-q", "--no-edit", "main")
        p = self.run_check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_unknown_ref_exits_2_never_passes(self):
        p = self.run_check(head="no-such-ref")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    # --- dependabot exemption end-to-end (DRE-2049) -----------------------

    def test_dependabot_authored_bump_exits_0_without_a_test_commit(self):
        # A dependency bump: one code-classified commit (the pinned
        # manifest), no test commit anywhere — the exact shape of bp #93.
        self.git("checkout", "-q", "-b", "dependabot/pip/pip-minor-patch-1a2b3c")
        self.add_commit("requirements-dev.txt", "build(deps): bump the pip group")
        p = self.run_check(author="dependabot[bot]")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("exempt", p.stdout)
        self.assertIn("dependabot", p.stdout)

    def test_human_author_env_does_not_exempt(self):
        # PR_AUTHOR set but not dependabot: the discipline holds unchanged.
        self.git("checkout", "-q", "-b", "agent/DRE-5-x")
        self.add_commit("scripts/widget.py", "fix(DRE-5): impl first")
        p = self.run_check(author="alice")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)


class WorkflowWiringTest(unittest.TestCase):
    """tests.yml must actually run the checker on PRs — the script without
    the job enforces nothing."""

    def setUp(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        jobs = [
            j for j in doc["jobs"].values()
            if any("check_tdd_commits.py" in (s.get("run") or "")
                   for s in j.get("steps", []))
        ]
        self.assertEqual(
            len(jobs), 1,
            "expected exactly one Pipeline Tests job invoking check_tdd_commits.py",
        )
        self.job = jobs[0]

    def test_job_runs_only_on_pull_requests(self):
        # The workflow also fires on push to main, where there is no PR
        # commit list to judge — the job must gate itself out there.
        self.assertIn("pull_request", self.job.get("if", ""))

    def test_checkout_fetches_full_history(self):
        # The check walks the PR's commit list; a depth-1 checkout can't.
        checkouts = [
            s for s in self.job["steps"]
            if "actions/checkout" in (s.get("uses") or "")
        ]
        self.assertTrue(checkouts, "job has no checkout step")
        self.assertEqual(checkouts[0].get("with", {}).get("fetch-depth"), 0)

    def test_base_and_head_come_from_the_pr_event_via_env(self):
        # Refs are passed through env, not interpolated into the shell line —
        # a crafted branch name must never become shell input.
        step = next(
            s for s in self.job["steps"]
            if "check_tdd_commits.py" in (s.get("run") or "")
        )
        env = step.get("env", {})
        self.assertIn(
            "github.event.pull_request.base.ref", str(env.get("BASE_REF", ""))
        )
        self.assertIn(
            "github.event.pull_request.head.sha", str(env.get("HEAD_SHA", ""))
        )
        self.assertIn('"origin/$BASE_REF"', step["run"])
        self.assertIn('"$HEAD_SHA"', step["run"])

    def test_pr_author_reaches_the_check_for_the_dependabot_exemption(self):
        # DRE-2049: the script decides the dependabot exemption off the PR's
        # author — GitHub-attested identity, not a spoofable branch name. It
        # rides env like the refs, never shell interpolation.
        step = next(
            s for s in self.job["steps"]
            if "check_tdd_commits.py" in (s.get("run") or "")
        )
        self.assertIn(
            "github.event.pull_request.user.login",
            str(step.get("env", {}).get("PR_AUTHOR", "")),
            "the job must hand the PR author to the check via PR_AUTHOR",
        )


if __name__ == "__main__":
    unittest.main()
