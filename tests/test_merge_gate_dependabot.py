"""RED-first tests for DRE-2039 — Dependabot rides the merge-gate rail.

Policy under test (scripts/merge_gate.py + merge-gate.yml wiring):

  A `dependabot/**` PR is gate-mergeable ONLY when ALL of:
    (a) the latest critic verdict is APPROVE, SHA-bound to the current head
        (the existing condition 2 — unchanged, still gates),
    (b) CI is green (condition 1 — unchanged),
    (c) the PR is a minor/patch update — proven DETERMINISTICALLY from
        Dependabot's own machine-readable commit metadata (the
        `update-type: version-update:semver-<level>` lines Dependabot embeds
        in every version-update commit message — the same signal the
        official dependabot/fetch-metadata action parses). EVERY update in
        the (possibly grouped) PR must be semver-minor or semver-patch.

  A MAJOR update — or a PR whose semver level cannot be proven (no
  metadata), or a dependabot-named branch not actually authored by
  dependabot[bot] — gets the new `human` decision: the workflow posts the
  honest "waiting for human merge" state ONCE and does nothing. `human` is
  distinct from `wait` (no future event flips a major to auto-mergeable)
  and from `hold` (no negative verdict is standing).

  Fail-closed edges: an empty commit record (API blip substitute) is
  `wait` — never judge the semver level on unverifiable data. The policy
  applies ONLY to `dependabot/**` branches: agent/repair branches whose
  commit messages happen to contain update-type strings are untouched.
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

HEAD = "aa11" * 10
STALE = "bb22" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"
DEPENDABOT = "dependabot[bot]"
MINOR_BRANCH = "dependabot/pip/pip-minor-patch-1a2b3c4"
MAJOR_BRANCH = "dependabot/pip/requests-3.0.0"

GREEN_CI = [{"name": "unit", "status": "completed", "conclusion": "success"}]
RED_CI = [{"name": "unit", "status": "completed", "conclusion": "failure"}]
APPROVE = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
}]
STALE_APPROVE = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{STALE}",
}]
REQUEST_CHANGES = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}",
}]


def dependabot_commit(*update_types, subject="Bump the pip-minor-patch group with 2 updates"):
    """A commit shaped like Dependabot's, one updated-dependencies entry per
    update type — the exact trailer dependabot/fetch-metadata parses."""
    lines = [subject, "", "---", "updated-dependencies:"]
    for i, level in enumerate(update_types):
        lines += [
            f"- dependency-name: dep-{i}",
            "  dependency-type: direct:production",
            f"  update-type: version-update:semver-{level}",
        ]
    lines += ["...", "", f"Signed-off-by: {DEPENDABOT} <support@github.com>"]
    return {"sha": "c" * 40, "commit": {"message": "\n".join(lines)}}


def plain_commit(message="Merge branch 'main' into dependabot/pip/x"):
    return {"sha": "d" * 40, "commit": {"message": message}}


def decide(branch=MINOR_BRANCH, author=DEPENDABOT, commits=(),
           checks=None, comments=None, compare="ahead"):
    return merge_gate.decide(
        HEAD, QA_LOGIN,
        GREEN_CI if checks is None else checks,
        APPROVE if comments is None else comments,
        frozenset(), compare,
        head_branch=branch, pr_author=author, pr_commits=list(commits),
    )


class GroupedMinorPatchMergesTest(unittest.TestCase):
    """Acceptance: a simulated grouped minor/patch PR meets the gate's merge
    conditions — and ONLY with the existing conditions also satisfied."""

    def test_grouped_minor_patch_with_green_ci_and_bound_approve_merges(self):
        d = decide(commits=[dependabot_commit("minor", "patch")])
        self.assertEqual(d.action, "merge", d.reason)

    def test_single_patch_update_merges(self):
        d = decide(commits=[dependabot_commit("patch")])
        self.assertEqual(d.action, "merge", d.reason)

    def test_update_branch_merge_commit_does_not_spoil_the_signal(self):
        # After the gate's own update-branch (DRE-1924) the PR carries a
        # metadata-less merge commit alongside Dependabot's — still minor.
        d = decide(commits=[dependabot_commit("minor"), plain_commit()])
        self.assertEqual(d.action, "merge", d.reason)

    # The existing gate still gates — (c) never substitutes for (a)/(b).

    def test_minor_without_critic_verdict_waits(self):
        d = decide(commits=[dependabot_commit("minor")], comments=[])
        self.assertEqual(d.action, "wait")

    def test_minor_with_red_ci_waits(self):
        d = decide(commits=[dependabot_commit("minor")], checks=RED_CI)
        self.assertEqual(d.action, "wait")

    def test_minor_with_stale_approve_waits(self):
        d = decide(commits=[dependabot_commit("minor")], comments=STALE_APPROVE)
        self.assertEqual(d.action, "wait")

    def test_minor_with_request_changes_holds(self):
        d = decide(commits=[dependabot_commit("minor")], comments=REQUEST_CHANGES)
        self.assertEqual(d.action, "hold")

    def test_minor_on_stale_branch_still_updates_first(self):
        # Branch currency (condition 0) still applies to mergeable minors.
        d = decide(commits=[dependabot_commit("minor")], compare="behind")
        self.assertEqual(d.action, "update")


class MajorNeverAutoMergesTest(unittest.TestCase):
    """Acceptance: a simulated major does not meet the merge conditions —
    the gate reports the honest `human` state even with CI green and a
    bound APPROVE standing."""

    def test_single_major_is_human_despite_green_ci_and_approve(self):
        d = decide(branch=MAJOR_BRANCH,
                   commits=[dependabot_commit("major", subject="Bump requests from 2.32.0 to 3.0.0")])
        self.assertEqual(d.action, "human")
        self.assertIn("major", d.reason)
        self.assertIn("human", d.reason)

    def test_one_major_poisons_a_group(self):
        d = decide(commits=[dependabot_commit("minor", "major", "patch")])
        self.assertEqual(d.action, "human")

    def test_major_is_human_even_on_a_stale_branch(self):
        # Never update-branch a major: the gate does NOTHING but report.
        d = decide(branch=MAJOR_BRANCH,
                   commits=[dependabot_commit("major")], compare="behind")
        self.assertEqual(d.action, "human")

    def test_no_update_type_metadata_is_human_fail_closed(self):
        # A dependabot PR whose semver level cannot be PROVEN minor/patch
        # must not auto-merge.
        d = decide(commits=[plain_commit("Bump something somehow")])
        self.assertEqual(d.action, "human")

    def test_unknown_update_type_level_is_human_fail_closed(self):
        # Only the two known-safe levels are mergeable; anything else
        # (including future vocabulary) reads as not-proven-safe.
        commit = {"sha": "e" * 40, "commit": {"message":
            "Bump x\n\n---\nupdated-dependencies:\n- dependency-name: x\n"
            "  update-type: version-update:semver-mega\n...\n"}}
        d = decide(commits=[commit])
        self.assertEqual(d.action, "human")

    def test_empty_commit_record_waits_fail_closed(self):
        # The workflow substitutes [] on an API blip — unverifiable data
        # must wait, not read as "no majors, merge" nor spam a human note.
        d = decide(commits=[])
        self.assertEqual(d.action, "wait")


class DependabotIdentityTest(unittest.TestCase):
    """Branch names are attacker-choosable; the semver leniency is for
    genuine Dependabot PRs only (the [bot] suffix is GitHub-reserved)."""

    def test_dependabot_branch_not_authored_by_dependabot_is_human(self):
        d = decide(author="agent-bureau-bot[bot]",
                   commits=[dependabot_commit("minor")])
        self.assertEqual(d.action, "human")

    def test_human_author_on_dependabot_branch_is_human(self):
        d = decide(author="someuser", commits=[dependabot_commit("patch")])
        self.assertEqual(d.action, "human")


class PolicyScopedToDependabotBranchesTest(unittest.TestCase):
    """agent/repair branches are untouched — even when their commit
    messages happen to contain semver-major metadata strings."""

    def test_agent_branch_with_major_looking_commits_still_merges(self):
        d = decide(branch="agent/DRE-2039-dependabot-wiring",
                   author="agent-bureau-bot[bot]",
                   commits=[dependabot_commit("major")])
        self.assertEqual(d.action, "merge", d.reason)

    def test_repair_branch_unaffected(self):
        d = decide(branch="repair/ab12cd3", author="agent-bureau-bot[bot]",
                   commits=[dependabot_commit("major")])
        self.assertEqual(d.action, "merge", d.reason)

    def test_decide_without_new_inputs_behaves_exactly_as_before(self):
        # Every pre-DRE-2039 caller passes no branch/author/commits — the
        # gate must decide exactly as it did.
        d = merge_gate.decide(HEAD, QA_LOGIN, GREEN_CI, APPROVE,
                              frozenset(), "ahead")
        self.assertEqual(d.action, "merge")


class CliContractTest(unittest.TestCase):
    """The workflow drives the script via new optional flags; the grep-able
    stdout contract extends with decision=human."""

    def run_cli(self, commits, branch=MINOR_BRANCH, author=DEPENDABOT):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            pc = Path(td) / "pr-commits.json"
            cr.write_text(json.dumps({"check_runs": GREEN_CI}))
            cm.write_text(json.dumps(APPROVE))
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(json.dumps({"status": "ahead"}))
            # REST GET pulls/{pr}/commits returns a BARE array.
            pc.write_text(json.dumps(commits))
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp),
                 "--head-branch", branch, "--pr-author", author,
                 "--pr-commits-file", str(pc)],
                capture_output=True, text=True,
            )

    def parse(self, stdout):
        return dict(ln.split("=", 1) for ln in stdout.splitlines() if "=" in ln)

    def test_minor_merges_end_to_end(self):
        proc = self.run_cli([dependabot_commit("minor", "patch")])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "merge")

    def test_major_is_human_end_to_end(self):
        proc = self.run_cli([dependabot_commit("major")], branch=MAJOR_BRANCH)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.parse(proc.stdout)
        self.assertEqual(fields.get("decision"), "human")
        self.assertIn("waiting for human merge", fields.get("reason", ""))


class WorkflowWiringTest(unittest.TestCase):
    """merge-gate.yml must wake on dependabot branches, gather the identity
    + semver inputs from GitHub's own records, act on `human` by posting the
    honest state (once), and never dispatch the fix agent at Dependabot."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(WORKFLOW.read_text())
        steps = cls.doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1
        cls.run_block = runs[0]

    def test_workflow_run_leg_wakes_on_dependabot_branches(self):
        cond = self.doc["jobs"]["evaluate"]["if"]
        self.assertIn(
            "startsWith(github.event.workflow_run.head_branch, 'dependabot/')",
            cond,
            "CI finishing on a dependabot/** branch never wakes the gate",
        )

    def test_branch_case_admits_dependabot(self):
        self.assertIn("agent/*|repair/*|dependabot/*", self.run_block)

    def test_pr_author_comes_from_githubs_own_pr_record(self):
        # REST user.login ("dependabot[bot]"), not gh's app/-prefixed
        # GraphQL rendering — deterministic across gh versions.
        self.assertIn('pulls/$PR" --jq .user.login', self.run_block)
        self.assertIn('--pr-author "$AUTHOR"', self.run_block)

    def test_commit_record_is_gathered_and_passed_fail_closed(self):
        self.assertIn("pulls/$PR/commits", self.run_block)
        m = re.search(r"--pr-commits-file (\S+)", self.run_block)
        self.assertIsNotNone(m, "workflow does not pass --pr-commits-file")
        self.assertIn(f"> {m.group(1)}", self.run_block)
        # API blip substitute: an empty record (script → wait, fail-closed).
        self.assertIn("echo '[]'", self.run_block)

    def test_head_branch_is_passed(self):
        self.assertIn('--head-branch "$BRANCH"', self.run_block)

    def test_human_decision_posts_waiting_state_once_and_never_merges(self):
        human = self.run_block.find('"$DECISION" = "human"')
        comment = self.run_block.find("gh pr comment")
        merge_guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        merge = self.run_block.find("gh pr merge")
        self.assertGreater(human, -1, "no human-decision arm")
        self.assertGreater(comment, human, "honest state not posted")
        self.assertGreater(merge_guard, comment,
                           "human arm must exit before the merge guard")
        self.assertGreater(merge, merge_guard)
        self.assertIn("waiting for human merge", self.run_block)
        # Idempotence: re-evaluations must not spam the PR — the already-
        # fetched comments record is checked before posting.
        idem = self.run_block.find('grep -q "Merge gate: waiting for human merge" /tmp/comments.json')
        self.assertGreater(idem, human, "no idempotence guard on the comment")
        self.assertLess(idem, comment)

    def test_dependabot_conflicts_never_dispatch_the_fix_agent(self):
        # Dependabot rebases/recreates its own conflicted PRs; the fix agent
        # has no card to work and would thrash.
        guard = self.run_block.find('case "$BRANCH" in dependabot/*')
        # $FIX_WF, not a literal: the stub filename resolves per repo
        # (DRE-2056 — agent-fix.yml is the workflow_call-only reusable in
        # bureau-pipeline itself; test_self_stub_dispatch_parity.py).
        dispatch = self.run_block.find('gh workflow run "$FIX_WF"')
        self.assertGreater(guard, -1, "no dependabot guard in the DIRTY arm")
        self.assertLess(guard, dispatch,
                        "dependabot guard must precede the fix-agent dispatch")


class CriticReachabilityTest(unittest.TestCase):
    """Condition (a) requires a critic verdict, so the review job must be
    REACHABLE for dependabot PRs — otherwise the gate waits forever."""

    def test_qa_review_job_starts_on_dependabot_branches(self):
        doc = yaml.safe_load(QA_REVIEW.read_text())
        cond = doc["jobs"]["review"]["if"]
        self.assertIn(
            "startsWith(github.event.pull_request.head.ref, 'dependabot/')",
            cond,
            "qa-review's job gate never starts for dependabot/** — the "
            "merge gate would wait forever on a verdict that can't exist",
        )


if __name__ == "__main__":
    unittest.main()
