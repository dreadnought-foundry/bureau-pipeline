"""Branch-currency gate (DRE-1924): a stale-but-green PR is updated and
re-checked, never merged blind.

Origin (2026-07-11): a semantic merge conflict turned main red. The Asana
connector PR (registered `asana`) and a test PR (asserted `asana` is
unknown) were each green on their own branch, but red once both landed —
neither branch's CI could see the other's change. The gate's only defense
was the shell `mergeStateStatus == BEHIND` fast-path, and GitHub reports
BEHIND **only when branch protection's "require branches to be up to date"
toggle is already on** — off (the default), a stale branch reads CLEAN and
the gate merged it blind.

The fix moves branch currency into scripts/merge_gate.py as condition 0,
fed by GitHub's own compare record (GET compare/{base}...{head_sha} —
works regardless of branch protection):

  • status ahead / identical  → the head contains the base's tip: current,
    fall through to conditions 1-3.
  • status behind / diverged  → the base has commits the head lacks: the
    branch's green was earned against an older base. Decision `update` —
    the workflow updates the branch and exits; CI re-runs on the merged
    result and the gate re-evaluates when it completes.
  • anything else (compare API blip → the workflow substitutes `{}`) →
    `wait`, fail-closed: never merge past an unverifiable base, and never
    mutate the branch on unverifiable data either.

Condition 0 is evaluated FIRST — the same position the old shell BEHIND
fast-path held (before any verdict logic): green checks and a bound
APPROVE on a stale branch prove nothing about the merged result.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
SCRIPT = ROOT / "scripts" / "merge_gate.py"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402

HEAD = "aa11" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"

GREEN_CI = [
    {"name": "unit", "status": "completed", "conclusion": "success",
     "check_suite": {"id": 1}},
]
RED_CI = [
    {"name": "unit", "status": "completed", "conclusion": "failure",
     "check_suite": {"id": 1}},
]
CRITIC_OK = [{
    "user": {"login": QA_LOGIN, "type": "Bot"},
    "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
}]


def decide(compare_status, checks=None, comments=None):
    return merge_gate.decide(
        head_sha=HEAD,
        qa_login=QA_LOGIN,
        check_runs=GREEN_CI if checks is None else checks,
        comments=CRITIC_OK if comments is None else comments,
        compare_status=compare_status,
    )


class CurrencyDecisionTest(unittest.TestCase):
    """Condition 0 of merge_gate.decide — the tested replacement for the
    untested shell BEHIND fast-path."""

    def test_stale_but_green_is_update_not_merge(self):
        """THE incident class: checks all green, critic APPROVE bound to the
        current head — but the branch has diverged from its base. The old
        gate (no currency input, branch protection off) merged this blind."""
        for status in ("diverged", "behind"):
            decision = decide(status)
            self.assertEqual(decision.action, "update",
                             f"status={status}: {decision.reason}")
            self.assertIn(status, decision.reason)

    def test_current_branch_falls_through_to_merge(self):
        for status in ("ahead", "identical"):
            decision = decide(status)
            self.assertEqual(decision.action, "merge",
                             f"status={status}: {decision.reason}")

    def test_unknown_currency_waits_fail_closed(self):
        """A compare-API blip (the workflow substitutes `{}` → status None)
        or an unrecognized status must WAIT — never merge past an
        unverifiable base, and never fire the update mutation on it."""
        for status in (None, "", "garbage"):
            decision = decide(status)
            self.assertEqual(decision.action, "wait",
                             f"status={status!r}: {decision.reason}")
            self.assertIn("currency", decision.reason)

    def test_staleness_evaluated_before_checks_and_verdicts(self):
        """Condition 0 holds the old shell fast-path's position: it fires
        before CI/verdict evaluation — a stale branch is updated even while
        its (meaningless) checks are red or its verdict is missing, so CI
        runs once on the merged result instead of twice."""
        self.assertEqual(decide("diverged", checks=RED_CI).action, "update")
        self.assertEqual(decide("diverged", comments=[]).action, "update")

    def test_wait_on_unknown_currency_beats_green_and_approve(self):
        self.assertEqual(decide(None).action, "wait")


class ReproductionTest(unittest.TestCase):
    """The exact green-alone-red-together case, reproduced with real git:
    an Asana-connector branch and a stale test branch, each green on its
    own tree, red once combined — and the gate decision that now blocks it
    pre-merge."""

    def _git(self, repo, *args):
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def _suite(self, repo):
        """The tree's own test suite: run every test_*.py it carries.
        Returns True when green."""
        for test in sorted(Path(repo).glob("test_*.py")):
            proc = subprocess.run(
                [sys.executable, str(test)], cwd=repo,
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                return False
        return True

    def _compare_status(self, repo, base, head):
        """GitHub's compare/{base}...{head} status field, derived from the
        same ahead/behind counts GitHub documents for it."""
        ahead = int(self._git(repo, "rev-list", "--count", f"{base}..{head}"))
        behind = int(self._git(repo, "rev-list", "--count", f"{head}..{base}"))
        if ahead and behind:
            return "diverged"
        if behind:
            return "behind"
        return "ahead" if ahead else "identical"

    def test_asana_times_stale_test_is_blocked_pre_merge(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "t@t")
            self._git(repo, "config", "user.name", "t")
            self._git(repo, "checkout", "-q", "-b", "main")

            # Base: a registry that does not know asana.
            (repo / "registry.py").write_text('CONNECTORS = ["slack"]\n')
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "base: connector registry")

            # Test PR, cut from base: asserts asana is unknown. Green on
            # ITS tree — the branch's registry has no asana.
            self._git(repo, "checkout", "-q", "-b", "agent/test-pr")
            (repo / "test_registry.py").write_text(
                "from registry import CONNECTORS\n"
                'assert "asana" not in CONNECTORS, "asana must be unknown"\n'
            )
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "test PR: asana is unknown")
            self.assertTrue(self._suite(repo), "test PR must be green alone")

            # Meanwhile the Asana connector PR merges to main. Main is
            # green alone too — it carries no asana test.
            self._git(repo, "checkout", "-q", "main")
            (repo / "registry.py").write_text(
                'CONNECTORS = ["slack", "asana"]\n'
            )
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-qm", "connector PR: register asana")
            self.assertTrue(self._suite(repo), "main must be green alone")

            # Red together: the branches merge with NO textual conflict —
            # the conflict is semantic, invisible to git and to either
            # branch's own CI.
            self._git(repo, "checkout", "-q", "-b", "landed", "main")
            self._git(repo, "merge", "-q", "--no-edit", "agent/test-pr")
            self.assertFalse(
                self._suite(repo),
                "green alone must be RED together — the semantic conflict "
                "this card exists for",
            )

            # The gate's view of the stale test PR: compare says diverged.
            status = self._compare_status(repo, "main", "agent/test-pr")
            self.assertEqual(status, "diverged")

            # Blind (currency-unaware) inputs — green CI, APPROVE bound to
            # head — would merge; the currency condition turns it into
            # `update` instead. Blocked pre-merge.
            self.assertEqual(decide("ahead").action, "merge",
                             "the blind decision this guard corrects")
            self.assertEqual(decide(status).action, "update")

            # After the update (base merged into the branch), the branch's
            # own CI now sees the true merged state and goes red — and a
            # red check makes the gate wait. Red never reaches main.
            self._git(repo, "checkout", "-q", "agent/test-pr")
            self._git(repo, "merge", "-q", "--no-edit", "main")
            self.assertFalse(self._suite(repo),
                             "the updated branch must expose the breakage")
            updated_status = self._compare_status(
                repo, "main", "agent/test-pr"
            )
            self.assertEqual(updated_status, "ahead")
            self.assertEqual(decide(updated_status, checks=RED_CI).action,
                             "wait")


class CliContractTest(unittest.TestCase):
    """The workflow-facing contract: --compare-file is a required input,
    the raw compare payload's status field drives condition 0, and the
    `{}` blip substitute reads as wait."""

    def run_cli(self, compare_payload):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            cr.write_text(json.dumps({"check_runs": GREEN_CI}))
            cm.write_text(json.dumps(CRITIC_OK))
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(compare_payload)
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp)],
                capture_output=True, text=True,
            )

    def decision(self, proc):
        fields = dict(
            ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln
        )
        return fields.get("decision")

    def test_compare_file_is_required(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--head-sha", HEAD, "--qa-login", QA_LOGIN],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--compare-file", proc.stderr)

    def test_diverged_payload_decides_update(self):
        proc = self.run_cli(json.dumps({"status": "diverged"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "update")

    def test_ahead_payload_decides_merge(self):
        proc = self.run_cli(json.dumps({"status": "ahead"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_blip_substitute_decides_wait(self):
        proc = self.run_cli("{}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "wait")

    def test_malformed_compare_fails_loud_and_never_merges(self):
        proc = self.run_cli("not json")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)


class WiringTest(unittest.TestCase):
    """merge-gate.yml gathers the compare record from GitHub and acts on
    `decision=update` — and the untested shell BEHIND fast-path is gone
    (the currency decision lives ONLY in the tested script)."""

    def setUp(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
        self.run_block = runs[0]

    def test_compare_record_gathered_and_passed(self):
        """The currency input comes from GitHub's own compare record for
        the PR's base and current head, written to the exact file the
        script is handed, with the fail-closed `{}` substitute on a blip."""
        self.assertIn("compare/$BASE...$SHA", self.run_block)
        m = re.search(r"--compare-file (\S+)", self.run_block)
        self.assertIsNotNone(m, "workflow does not pass --compare-file")
        self.assertIn(f"> {m.group(1)}", self.run_block)
        self.assertIn("echo '{}' >", self.run_block)

    def test_base_is_the_prs_own_base_ref(self):
        self.assertIn("baseRefName", self.run_block)

    def test_update_action_wired_behind_the_update_decision(self):
        """`update-branch` must be reachable only behind the machine-readable
        `decision=update`, and sit before the merge guard so an update run
        never falls through to `gh pr merge`."""
        guard = self.run_block.find('[ "$DECISION" = "update" ]')
        self.assertGreater(guard, -1, "update decision guard missing")
        put = self.run_block.find("update-branch")
        self.assertGreater(put, guard, "update-branch not behind the guard")
        merge_guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        self.assertGreater(merge_guard, put,
                           "update arm must precede the merge guard")

    def test_shell_behind_fast_path_removed(self):
        """BEHIND is reported only when branch protection's up-to-date
        toggle is already on — the shell fast-path was dead code without it
        and untested with it. Currency is the script's condition 0 now."""
        self.assertNotIn('"$MSTATE" = "BEHIND"', self.run_block)

    def test_conflict_dispatch_preserved(self):
        """The DIRTY (textual conflict) arm stays in the shell — update-
        branch cannot resolve a conflict; the fix agent can."""
        self.assertIn('"$MSTATE" = "DIRTY"', self.run_block)

    def test_merge_still_behind_qa_bot_token(self):
        """Author != merger: the merge still runs as the qa-bot App (the
        step's GH_TOKEN), not the workflow's own token."""
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        step = next(s for s in steps if s.get("name") == "Evaluate and merge")
        self.assertEqual(step["env"]["GH_TOKEN"],
                         "${{ steps.qa.outputs.token }}")
        line = next(ln for ln in self.run_block.splitlines()
                    if "gh pr merge" in ln)
        self.assertNotIn("github.token", line)


if __name__ == "__main__":
    unittest.main()
