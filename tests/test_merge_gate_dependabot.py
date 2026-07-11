"""RED-first suite for DRE-2039 — Dependabot PRs ride the merge gate.

Policy: a `dependabot/**` branch is gate-mergeable ONLY when
  (a) the critic's APPROVE is SHA-bound to the current head (unchanged),
  (b) CI is green (unchanged), AND
  (c) the PR is provably a minor/patch update — read DETERMINISTICALLY from
      Dependabot's own per-dependency metadata: the
      `update-type: version-update:semver-<major|minor|patch>` lines it
      embeds verbatim in every commit message (the same record
      dependabot/fetch-metadata parses). Works identically for single bumps
      and grouped PRs (a group carries one line per updated dependency).

Anything NOT provably minor/patch is decision `human` — a major anywhere in
the group, a missing/indeterminate signal, or a `dependabot/**` branch whose
PR author is not dependabot[bot] (commit messages are author-controlled, so
the signal only counts on Dependabot's own PRs — GitHub reserves the [bot]
suffix). On `human` the workflow posts the honest "waiting for human merge"
state once and merges nothing; no future CI/verdict event changes the
answer, which is why the policy is evaluated FIRST (before currency/CI/
critic) — the gate must neither wait on nor mutate a human-owned PR.

Wiring (merge-gate.yml): the gate's event filter and branch case admit
dependabot/** heads, the PR author + commits are gathered from GitHub's own
records (fail-closed `[]` blip substitute), the `human` decision posts the
waiting-for-human comment idempotently, and a DIRTY (merge-conflict)
dependabot PR must NOT dispatch the fix agent — Dependabot or a human
resolves its conflicts, an agent pushing to its branch would fight it.
qa-review.yml's job gate must let dependabot/** PRs start the critic at all
(should_review_pr.py opts them in — tested in test_should_review_pr.py).
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
QA_REVIEW = ROOT / ".github" / "workflows" / "qa-review.yml"
SCRIPT = ROOT / "scripts" / "merge_gate.py"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

# ── shared vocabulary (same shapes as test_merge_gate_decision_table.py) ──
HEAD = "aa11" * 10
STALE = "bb22" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"
DEPENDABOT_LOGIN = "dependabot[bot]"
DEPENDABOT_REF = "dependabot/pip/pip-minor-patch-0a1b2c3d4e"

GREEN_CI = [{"name": "unit", "status": "completed", "conclusion": "success"}]
RED_CI = [{"name": "unit", "status": "completed", "conclusion": "failure"}]


def critic(verdict, sha=None):
    base = f"🔎 QA Critic — VERDICT: {verdict}"
    return f"{base} @{sha}" if sha else base


def comment(login, body):
    return {"user": {"login": login, "type": "Bot"}, "body": body}


APPROVE_AT_HEAD = [comment(QA_LOGIN, critic("APPROVE", HEAD))]


def bump_commit(update_types, subject="Bump the pip-minor-patch group with updates"):
    """One commit as GET pulls/{pr}/commits returns it, carrying Dependabot's
    verbatim updated-dependencies metadata — one update-type per dependency,
    exactly the block Dependabot signs its commits with."""
    deps = "\n".join(
        f"- dependency-name: dep-{i}\n"
        f"  dependency-version: 1.2.{i}\n"
        f"  dependency-type: direct:production\n"
        f"  update-type: version-update:semver-{t}"
        for i, t in enumerate(update_types)
    )
    message = (
        f"{subject}\n\n"
        f"---\nupdated-dependencies:\n{deps}\n...\n\n"
        f"Signed-off-by: dependabot[bot] <support@github.com>"
    )
    return {"sha": "cc33" * 10, "commit": {"message": message}}


def decide(pr_commits, head_ref=DEPENDABOT_REF, pr_author=DEPENDABOT_LOGIN,
           checks=GREEN_CI, comments=APPROVE_AT_HEAD, compare_status="ahead"):
    return merge_gate.decide(
        HEAD, QA_LOGIN, checks, comments,
        compare_status=compare_status,
        head_ref=head_ref, pr_author=pr_author, pr_commits=pr_commits,
    )


class DependabotSemverPolicyTest(unittest.TestCase):
    """Condition (c): the deterministic minor/patch proof, majors → human."""

    def test_grouped_minor_patch_pr_merges(self):
        # The acceptance row: a grouped minor+patch PR with CI green and a
        # bound APPROVE meets every merge condition.
        d = decide([bump_commit(["minor", "patch", "patch"])])
        self.assertEqual(d.action, "merge")

    def test_single_patch_bump_merges(self):
        d = decide([bump_commit(["patch"], subject="Bump requests from 2.32.0 to 2.32.1")])
        self.assertEqual(d.action, "merge")

    def test_major_pr_is_human_even_when_everything_else_is_green(self):
        # The acceptance row's negative: a simulated major fails condition
        # (c) despite green CI + bound APPROVE.
        d = decide([bump_commit(["major"], subject="Bump pytest from 8.4.1 to 9.0.0")])
        self.assertEqual(d.action, "human")
        self.assertIn("waiting for human merge", d.reason)

    def test_one_major_anywhere_in_a_group_poisons_the_group(self):
        d = decide([bump_commit(["minor", "major", "patch"])])
        self.assertEqual(d.action, "human")

    def test_major_across_multiple_commits_is_still_seen(self):
        d = decide([bump_commit(["minor"]), bump_commit(["major"])])
        self.assertEqual(d.action, "human")

    def test_no_semver_signal_is_human_fail_closed(self):
        # A dependabot/** PR whose commits carry NO update-type metadata is
        # not provably minor/patch — never merge on absence of proof.
        plain = {"sha": "dd44" * 10, "commit": {"message": "Bump something"}}
        d = decide([plain])
        self.assertEqual(d.action, "human")
        self.assertIn("waiting for human merge", d.reason)

    def test_empty_commit_record_is_human_fail_closed(self):
        # The workflow substitutes [] on a commits-API blip.
        self.assertEqual(decide([]).action, "human")

    def test_policy_is_evaluated_before_ci_and_critic(self):
        # A major must surface the honest human state immediately — not
        # `wait` behind red CI or a missing verdict that will never matter.
        d = decide([bump_commit(["major"])], checks=RED_CI, comments=[])
        self.assertEqual(d.action, "human")

    def test_policy_is_evaluated_before_branch_currency(self):
        # Never fire the update-branch mutation on a human-owned PR.
        d = decide([bump_commit(["major"])], compare_status="behind")
        self.assertEqual(d.action, "human")


class DependabotAuthorshipTest(unittest.TestCase):
    """The signal only counts on Dependabot's own PRs — commit messages are
    author-controlled, so a dependabot/**-named branch from anyone else must
    not buy auto-merge with forged metadata."""

    def test_forged_dependabot_branch_by_another_author_is_human(self):
        d = decide([bump_commit(["patch"])], pr_author="agent-bureau-bot[bot]")
        self.assertEqual(d.action, "human")
        self.assertIn("waiting for human merge", d.reason)

    def test_missing_author_record_is_human(self):
        d = decide([bump_commit(["patch"])], pr_author="")
        self.assertEqual(d.action, "human")


class DependabotStillNeedsTheNormalGateTest(unittest.TestCase):
    """Conditions (a) and (b) are unchanged: minor/patch proof alone merges
    nothing — every existing gate condition still applies."""

    def test_minor_without_critic_verdict_waits(self):
        d = decide([bump_commit(["minor"])], comments=[])
        self.assertEqual(d.action, "wait")

    def test_minor_with_stale_verdict_waits(self):
        stale = [comment(QA_LOGIN, critic("APPROVE", STALE))]
        d = decide([bump_commit(["minor"])], comments=stale)
        self.assertEqual(d.action, "wait")

    def test_minor_with_request_changes_holds(self):
        rc = [comment(QA_LOGIN, critic("REQUEST_CHANGES", HEAD))]
        d = decide([bump_commit(["minor"])], comments=rc)
        self.assertEqual(d.action, "hold")

    def test_minor_with_red_ci_waits(self):
        d = decide([bump_commit(["minor"])], checks=RED_CI)
        self.assertEqual(d.action, "wait")

    def test_minor_on_stale_branch_updates(self):
        d = decide([bump_commit(["minor"])], compare_status="behind")
        self.assertEqual(d.action, "update")


class NonDependabotBranchesUnaffectedTest(unittest.TestCase):
    """The policy keys on the head ref: agent/repair work never enters it,
    and the pre-DRE-2039 call shape (no new kwargs) is byte-compatible."""

    def test_agent_branch_ignores_dependabot_policy(self):
        d = decide([], head_ref="agent/DRE-1-x", pr_author="agent-bureau-bot[bot]")
        self.assertEqual(d.action, "merge")

    def test_default_call_shape_unchanged(self):
        d = merge_gate.decide(
            HEAD, QA_LOGIN, GREEN_CI, APPROVE_AT_HEAD, compare_status="ahead"
        )
        self.assertEqual(d.action, "merge")


class CliContractTest(unittest.TestCase):
    """The workflow's actual call shape: files + flags in, decision= out."""

    def run_cli(self, commits, head_ref=DEPENDABOT_REF,
                pr_author=DEPENDABOT_LOGIN, comments=APPROVE_AT_HEAD,
                with_commits_file=True):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "check-runs.json").write_text(
                json.dumps({"total_count": 1, "check_runs": GREEN_CI}))
            (td / "comments.json").write_text(json.dumps(comments))
            (td / "workflow-runs.json").write_text(json.dumps({"workflow_runs": []}))
            (td / "compare.json").write_text(json.dumps({"status": "ahead"}))
            (td / "pr-commits.json").write_text(json.dumps(commits))
            argv = [sys.executable, str(SCRIPT),
                    "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                    "--check-runs-file", str(td / "check-runs.json"),
                    "--comments-file", str(td / "comments.json"),
                    "--workflow-runs-file", str(td / "workflow-runs.json"),
                    "--compare-file", str(td / "compare.json"),
                    "--head-ref", head_ref, "--pr-author", pr_author]
            if with_commits_file:
                argv += ["--pr-commits-file", str(td / "pr-commits.json")]
            return subprocess.run(argv, capture_output=True, text=True)

    def parse(self, stdout):
        return dict(ln.split("=", 1) for ln in stdout.splitlines() if "=" in ln)

    def test_grouped_minor_merges_end_to_end(self):
        proc = self.run_cli([bump_commit(["minor", "patch"])])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "merge")

    def test_major_is_human_end_to_end(self):
        proc = self.run_cli([bump_commit(["major"])])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.parse(proc.stdout)
        self.assertEqual(fields.get("decision"), "human")
        self.assertIn("waiting for human merge", fields.get("reason", ""))

    def test_omitted_commits_file_is_human_fail_closed(self):
        proc = self.run_cli([bump_commit(["minor"])], with_commits_file=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "human")

    def test_malformed_commits_file_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "check-runs.json").write_text(
                json.dumps({"total_count": 1, "check_runs": GREEN_CI}))
            (td / "comments.json").write_text("[]")
            (td / "workflow-runs.json").write_text(json.dumps({"workflow_runs": []}))
            (td / "compare.json").write_text(json.dumps({"status": "ahead"}))
            (td / "pr-commits.json").write_text("not json")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(td / "check-runs.json"),
                 "--comments-file", str(td / "comments.json"),
                 "--workflow-runs-file", str(td / "workflow-runs.json"),
                 "--compare-file", str(td / "compare.json"),
                 "--head-ref", DEPENDABOT_REF,
                 "--pr-author", DEPENDABOT_LOGIN,
                 "--pr-commits-file", str(td / "pr-commits.json")],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("decision=merge", proc.stdout)


def evaluate_step_run():
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["evaluate"]["steps"]
    runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1
    return runs[0]


class MergeGateWiringTest(unittest.TestCase):
    """merge-gate.yml admits dependabot/** and threads the new inputs."""

    def setUp(self):
        self.run_block = evaluate_step_run()

    def test_workflow_run_leg_admits_dependabot_heads(self):
        cond = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"]["if"]
        self.assertIn(
            "startsWith(github.event.workflow_run.head_branch, 'dependabot/')",
            cond,
            "CI finishing on a dependabot PR must wake the gate",
        )

    def test_branch_case_admits_dependabot(self):
        self.assertIn("agent/*|repair/*|dependabot/*", self.run_block)

    def test_pr_author_and_commits_are_gathered_and_passed(self):
        # Author from GitHub's own PR record (REST user.login keeps the
        # [bot] suffix gh's author field strips); commits with the
        # fail-closed [] blip substitute.
        self.assertIn("pulls/$PR/commits", self.run_block)
        m = re.search(r"--pr-commits-file (\S+)", self.run_block)
        self.assertIsNotNone(m, "workflow does not pass --pr-commits-file")
        self.assertIn(f"> {m.group(1)}", self.run_block)
        self.assertIn("echo '[]'", self.run_block)
        self.assertIn('--head-ref "$BRANCH"', self.run_block)
        self.assertIn('--pr-author "$AUTHOR"', self.run_block)
        self.assertIn(".user.login", self.run_block)

    def test_human_decision_posts_waiting_state_and_never_merges(self):
        human = self.run_block.find('"$DECISION" = "human"')
        guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        self.assertGreater(human, -1, "no human-decision arm")
        self.assertLess(human, guard, "human arm must sit before the merge guard")
        self.assertIn("waiting for human merge", self.run_block)
        self.assertIn("gh pr comment", self.run_block)

    def test_human_state_comment_is_idempotent(self):
        # The gate re-runs on every CI/verdict event; the honest state must
        # be posted once, checked against the already-fetched comments.
        human_arm = self.run_block[self.run_block.find('"$DECISION" = "human"'):]
        post = human_arm[:human_arm.find("gh pr comment")]
        self.assertIn("/tmp/comments.json", post,
                      "posting must be guarded by a check of the existing "
                      "PR comments, or every gate run appends a duplicate")

    def test_dirty_dependabot_pr_never_dispatches_the_fix_agent(self):
        dirty = self.run_block.find('"$MSTATE" = "DIRTY"')
        dispatch = self.run_block.find("agent-fix.yml")
        self.assertGreater(dirty, -1)
        self.assertGreater(dispatch, dirty)
        between = self.run_block[dirty:dispatch]
        self.assertIn("dependabot/*", between,
                      "a conflicted dependabot PR must exit before the "
                      "fix-agent dispatch — Dependabot/a human resolves it")


class QaReviewWiringTest(unittest.TestCase):
    def test_qa_review_job_gate_admits_dependabot_heads(self):
        cond = yaml.safe_load(QA_REVIEW.read_text())["jobs"]["review"]["if"]
        self.assertIn(
            "startsWith(github.event.pull_request.head.ref, 'dependabot/')",
            cond,
            "the critic must start on dependabot PRs — its APPROVE is merge "
            "condition (a)",
        )


if __name__ == "__main__":
    unittest.main()
