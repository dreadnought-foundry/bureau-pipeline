"""Branch currency, RETIRED as a gate (DRE-2416) — what the fleet gave up
when it stopped requiring up-to-date branches, and what still catches it.

This suite was the DRE-1924 guard: a stale-but-green PR was updated from its
base and re-checked, never merged blind. That guard is gone. The fleet does
NOT require up-to-date branches, and the merge gate is the single writer of
that rule — not branch protection (CEO decision 2026-08-20, recorded on
DRE-2597; the rule lives in agent-bureau's
`architecture/decisions/adr-one-writer-per-fact.md`). Two reasons, from
settings read live that day: `EveryBite/atlas` — the reference deployment —
returns 403 on its protection endpoint, so a protection-held rule cannot be
a fleet rule; and `required_status_checks.strict` is `false` on every repo
checked, so being behind never blocked a merge in the first place.

What the guard cost is why it went. Every gate-initiated update was a new
head, every new head restarted the full CI suite, and on a busy repo the
base moved again before that suite finished — portico PR 268 burned four
green CI runs on unchanged source in thirteen minutes and still read
BLOCKED (DRE-2393, the DRE-2416 card's measurement). The decision table for
the replacement lives in tests/test_merge_gate_freshness_race.py; what THIS
file keeps is the honest record of the trade:

  • CurrencyIsNotAGateTest — the inverted decisions, stated explicitly so
    the retirement is executable rather than prose.
  • ReproductionTest — the original `asana` incident, still reproduced
    against a real git repository. It proves the accepted cost is REAL (the
    two branches are still green alone and red together, and the gate now
    merges the stale one) and that the catch is CI ON THE BASE BRANCH,
    which is where the 2026-07-11 incident was actually caught. medic.yml
    files the repair card off that red run
    (`architecture/decisions/adr-red-main-auto-repair.md`).
  • WiringTest — the update mutation is gone from merge-gate.yml, and the
    conflict arm that replaced it is behind the machine-readable decision.
"""

import json
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


class CurrencyIsNotAGateTest(unittest.TestCase):
    """The retirement, decision by decision. Each of these returned
    `update` or `wait` under DRE-1924/DRE-2274."""

    def test_stale_but_green_now_merges(self):
        """THE inversion. The old gate updated this PR and paid for a full
        CI suite; the new one merges it."""
        for status in ("diverged", "behind"):
            decision = decide(status)
            self.assertEqual(decision.action, "merge",
                             f"status={status}: {decision.reason}")

    def test_current_branch_still_merges(self):
        for status in ("ahead", "identical"):
            self.assertEqual(decide(status).action, "merge")

    def test_unknown_currency_no_longer_waits(self):
        """The `{}` compare blip used to be fail-closed `wait`. With
        currency out of the rule set there is nothing to fail closed ABOUT
        — the record's remaining job is the content id (DRE-2340), whose
        absence still costs the carry, not the merge."""
        for status in (None, "", "garbage"):
            self.assertEqual(decide(status).action, "merge",
                             f"status={status!r} still stalls")

    def test_the_other_conditions_still_beat_a_stale_branch(self):
        """Non-vacuous twin: dropping currency relaxed currency and nothing
        else. Red CI still waits, a missing verdict still waits."""
        self.assertEqual(decide("diverged", checks=RED_CI).action, "wait")
        self.assertEqual(decide("diverged", comments=[]).action, "wait")


class ReproductionTest(unittest.TestCase):
    """The 2026-07-11 `asana` incident, reproduced with real git: an
    Asana-connector change on the base and a stale test branch, each green
    on its own tree, red once combined.

    This is the ACCEPTED COST of DRE-2416, kept executable rather than
    described. The semantic conflict is still invisible to git and to
    either branch's own CI; the gate now merges the stale branch; and the
    breakage surfaces on the BASE BRANCH's own CI run — which is exactly
    where it surfaced in 2026-07-11, and what medic.yml turns into a repair
    card."""

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
            # -B: no __pycache__ — stray bytecode in the work tree would
            # block the branch switches below.
            proc = subprocess.run(
                [sys.executable, "-B", str(test)], cwd=repo,
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

    def test_asana_times_stale_test_merges_and_the_base_run_catches_it(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._git(repo, "init", "-q")
            self._git(repo, "config", "user.email", "t@t")
            self._git(repo, "config", "user.name", "t")
            self._git(repo, "checkout", "-q", "-b", "main")

            # Base: a registry that does not know asana.
            (repo / "registry.py").write_text('CONNECTORS = ["slack"]\n')
            self._git(repo, "add", "registry.py")
            self._git(repo, "commit", "-qm", "base: connector registry")

            # Test PR, cut from base: asserts asana is unknown. Green on
            # ITS tree — the branch's registry has no asana.
            self._git(repo, "checkout", "-q", "-b", "agent/test-pr")
            (repo / "test_registry.py").write_text(
                "from registry import CONNECTORS\n"
                'assert "asana" not in CONNECTORS, "asana must be unknown"\n'
            )
            self._git(repo, "add", "test_registry.py")
            self._git(repo, "commit", "-qm", "test PR: asana is unknown")
            self.assertTrue(self._suite(repo), "test PR must be green alone")

            # Meanwhile the Asana connector PR merges to main. Main is
            # green alone too — it carries no asana test.
            self._git(repo, "checkout", "-q", "main")
            (repo / "registry.py").write_text(
                'CONNECTORS = ["slack", "asana"]\n'
            )
            self._git(repo, "add", "registry.py")
            self._git(repo, "commit", "-qm", "connector PR: register asana")
            self.assertTrue(self._suite(repo), "main must be green alone")

            # Red together: the branches merge with NO textual conflict —
            # the conflict is semantic, invisible to git and to either
            # branch's own CI.
            self._git(repo, "checkout", "-q", "-b", "landed", "main")
            self._git(repo, "merge", "-q", "--no-edit", "agent/test-pr")
            self.assertFalse(
                self._suite(repo),
                "green alone must be RED together — the cost DRE-2416 accepts",
            )

            # The gate's view of the stale test PR: compare says diverged,
            # GitHub reports no conflict (the merge above succeeded), so
            # the gate merges it. The pre-DRE-2416 gate returned `update`.
            status = self._compare_status(repo, "main", "agent/test-pr")
            self.assertEqual(status, "diverged")
            self.assertEqual(decide(status).action, "merge")

            # THE CATCH: the base branch's own CI run on the merged result
            # is red — the state `landed` is already in. That red run is
            # what medic.yml files a repair card from
            # (adr-red-main-auto-repair.md), within minutes rather than the
            # CI suite this gate used to spend on every open branch.
            self._git(repo, "checkout", "-q", "main")
            self._git(repo, "merge", "-q", "--no-edit", "agent/test-pr")
            self.assertFalse(
                self._suite(repo),
                "the base branch's own run must be the thing that goes red",
            )

    def test_the_behind_head_is_recorded_in_the_run_log(self):
        """Nobody in this pipeline reads diffs, so merging a behind-base
        head has to say so — the audit line replaces the update push."""
        notes = decide("behind").notes
        self.assertTrue(any("behind" in n for n in notes), notes)


class CliContractTest(unittest.TestCase):
    """The workflow-facing contract: --compare-file is still required (it
    is the content-binding record), and its status no longer decides."""

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

    def test_diverged_payload_decides_merge(self):
        proc = self.run_cli(json.dumps({"status": "diverged"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_ahead_payload_decides_merge(self):
        proc = self.run_cli(json.dumps({"status": "ahead"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_blip_substitute_decides_merge(self):
        proc = self.run_cli("{}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_malformed_compare_fails_loud_and_never_merges(self):
        """Unchanged: an unreadable payload is a shape failure, not a
        decision. The job goes red and nothing merges."""
        proc = self.run_cli("not json")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)


class WiringTest(unittest.TestCase):
    """merge-gate.yml no longer mutates a branch, and the conflict arm that
    replaced the update arm sits behind the script's decision."""

    def setUp(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
        self.run_block = runs[0]

    def test_compare_record_gathered_and_passed(self):
        """The content-binding input still comes from GitHub's own compare
        record for the PR's base and current head, written to the exact
        file the script is handed, with the `{}` substitute on a blip."""
        self.assertIn("compare/$BASE...$SHA", self.run_block)
        self.assertIn("--compare-file", self.run_block)
        self.assertIn("echo '{}' >", self.run_block)

    def test_base_is_the_prs_own_base_ref(self):
        self.assertIn("baseRefName", self.run_block)

    def test_no_update_mutation_remains(self):
        """The DRE-1924 update push is gone — matched on the API path and
        the mutating verb, not on prose."""
        self.assertNotIn("/update-branch", self.run_block)
        self.assertNotIn("-X PUT", self.run_block)

    def test_shell_behind_fast_path_still_absent(self):
        """BEHIND is reported only when branch protection's up-to-date
        toggle is already on. It was dead code before DRE-1924 and it must
        not come back now that freshness is not a gate at all."""
        self.assertNotIn('"$MSTATE" = "BEHIND"', self.run_block)

    def test_conflict_dispatch_preserved(self):
        """The textual-conflict arm survives the move: it is now behind
        `decision=conflict` (DRE-2416) instead of an inline
        mergeStateStatus test, and still dispatches the fix agent."""
        self.assertIn('[ "$DECISION" = "conflict" ]', self.run_block)
        self.assertIn("mergeStateStatus", self.run_block)
        self.assertIn('--merge-state "$MSTATE"', self.run_block)

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
