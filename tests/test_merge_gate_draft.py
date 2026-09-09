"""RED-first tests for DRE-3467 — the merge gate must not try to merge a DRAFT.

What happened (bureau-pipeline PR #323, card DRE-3389). CI was green and the
critic's APPROVE was bound to the head, so the gate decided `merge` and ran

    gh pr merge --merge --delete-branch --match-head-commit 09b52e93…

which GitHub refused with `Pull Request is still a draft
(mergePullRequest)`, exit 1. The workflow's own failure classifier reads that
as "merge failed with the head still at $SHA — real failure" and exits 1, so
the gate ran RED twice on a pull request nothing was wrong with. The gate's
conditions never asked GitHub whether the PR was a draft — a vendor-boundary
gap of exactly the class `standards/vendor-boundaries.md` Q4 names: what are
the command's limitations.

The policy under test:

  * A draft pull request is `human` — the same not-ready arm a dependabot
    major takes (DRE-2039): merge-gate.yml posts the honest
    "waiting for human merge" state ONCE, exits 0, and touches nothing. It
    is not a hard error, and it is not silence.
  * Condition 4 is evaluated LAST, after CI (1), the critic (2) and the
    verifier (3). A draft whose CI is red or whose verdict is missing is
    ordinary work in progress and reads as today's `wait` — the honest note
    is posted only for the case this card is about, where the draft flag is
    the ONE thing standing between the pull request and `main`.
  * A conflicted draft still routes to the fix agent (condition 0 keeps its
    position): reconciling the branch is needed whatever the draft flag says.
  * `--is-draft` omitted reproduces the pre-DRE-3467 behavior exactly, for
    every caller that never passes it; an unparseable value is exit 2, never
    a guess (the gate never fails open).

Draft-as-not-ready is also what the rest of the pipeline already believes:
reconcile.py skips drafts in its conflict sweep, its no-checks watchdog and
its no-verdict sweep — "a draft is work in progress, not a stranding"
(reconcile.py:3695). The gate was the one reader of PR state that did not
ask.
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

# The live values from the failure, so the regression case below is the
# incident rather than a paraphrase of it.
HEAD = "09b52e93f4073fa97e7cd47391a45be4218e36ce"
STALE = "bb22" * 10
QA_LOGIN = "agent-bureau-qa-bot[bot]"

GREEN_CI = [
    {"name": "unit", "status": "completed", "conclusion": "success",
     "check_suite": {"id": 1}},
]
RED_CI = [
    {"name": "unit", "status": "completed", "conclusion": "failure",
     "check_suite": {"id": 1}},
]


def critic(token="APPROVE", sha=HEAD):
    return [{
        "user": {"login": QA_LOGIN, "type": "Bot"},
        "body": f"🔎 QA Critic — VERDICT: {token} @{sha}",
    }]


def verifier(token="PASS", sha=HEAD):
    return [{
        "user": {"login": QA_LOGIN, "type": "Bot"},
        "body": f"🧪 QA Verifier — VERDICT: {token} @{sha}",
    }]


def decide(checks=None, comments=None, merge_state="CLEAN", **kw):
    return merge_gate.decide(
        head_sha=HEAD,
        qa_login=QA_LOGIN,
        check_runs=GREEN_CI if checks is None else checks,
        comments=critic() if comments is None else comments,
        merge_state=merge_state,
        **kw,
    )


class DraftIsNotMergeableTest(unittest.TestCase):
    """The incident: everything else green, and the gate said `merge`."""

    def test_draft_pr_with_green_ci_and_bound_approve_is_not_merged(self):
        d = decide(is_draft=True)
        self.assertNotEqual(
            d.action, "merge",
            "the gate still decides merge for a draft — GitHub refuses it "
            "with 'Pull Request is still a draft (mergePullRequest)'",
        )

    def test_draft_takes_the_human_not_ready_arm(self):
        d = decide(is_draft=True)
        self.assertEqual(d.action, "human", d.reason)

    def test_the_reason_names_the_draft_state_in_plain_english(self):
        """Nobody here reads diffs: the run log and the PR note both come
        from this line, so it has to say what is wrong and what fixes it."""
        reason = decide(is_draft=True).reason.lower()
        self.assertIn("draft", reason)
        self.assertIn("ready for review", reason)

    def test_the_same_inputs_without_the_draft_flag_still_merge(self):
        """Anti-vacuity: the fixture above WOULD merge if the new condition
        were removed, so the assertions are about the draft flag and nothing
        else."""
        self.assertEqual(decide(is_draft=False).action, "merge")

    def test_a_bound_verifier_pass_does_not_rescue_a_draft(self):
        d = decide(comments=critic() + verifier(), is_draft=True)
        self.assertEqual(d.action, "human", d.reason)


class DraftIsEvaluatedLastTest(unittest.TestCase):
    """Condition 4 sits after 0-3, so ordinary work in progress reads as it
    does today and only a would-otherwise-merge draft gets the note."""

    def test_red_ci_on_a_draft_is_still_wait(self):
        d = decide(checks=RED_CI, is_draft=True)
        self.assertEqual(d.action, "wait", d.reason)

    def test_missing_verdict_on_a_draft_is_still_wait(self):
        d = decide(comments=[], is_draft=True)
        self.assertEqual(d.action, "wait", d.reason)

    def test_stale_verdict_on_a_draft_is_still_wait(self):
        d = decide(comments=critic(sha=STALE), is_draft=True)
        self.assertEqual(d.action, "wait", d.reason)

    def test_request_changes_on_a_draft_is_still_hold(self):
        d = decide(comments=critic("REQUEST_CHANGES"), is_draft=True)
        self.assertEqual(d.action, "hold", d.reason)

    def test_verifier_fail_on_a_draft_is_still_hold(self):
        d = decide(comments=critic() + verifier("FAIL"), is_draft=True)
        self.assertEqual(d.action, "hold", d.reason)

    def test_a_conflicted_draft_still_reaches_the_fix_agent(self):
        """Condition 0 keeps its position: the branch has to be reconciled
        with its base whatever the draft flag says, and the fix agent is the
        only thing that can do it."""
        d = decide(merge_state="DIRTY", is_draft=True)
        self.assertEqual(d.action, "conflict", d.reason)


class DraftDefaultIsBackwardsCompatibleTest(unittest.TestCase):
    """`is_draft` omitted = the pre-DRE-3467 behavior, exactly — the same
    shape `merge_state` (DRE-2416) and the dependabot record (DRE-2039) take
    for every caller that never passes them."""

    def test_omitting_the_flag_leaves_todays_decision_untouched(self):
        self.assertEqual(decide().action, "merge")

    def test_evaluate_draft_is_a_condition_of_its_own(self):
        self.assertIsNone(merge_gate.evaluate_draft(False))
        self.assertEqual(merge_gate.evaluate_draft(True).action, "human")


class DraftCliTest(unittest.TestCase):
    """End to end through the CLI the workflow actually runs, with the real
    payload shapes."""

    def run_cli(self, *extra, comments=None, checks=None):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cr, cm, wr, cp = (d / "cr.json", d / "cm.json",
                              d / "wr.json", d / "cp.json")
            cr.write_text(json.dumps(
                {"check_runs": GREEN_CI if checks is None else checks}))
            cm.write_text(json.dumps(critic() if comments is None else comments))
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(json.dumps({"status": "ahead"}))
            return subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp),
                 *extra],
                capture_output=True, text=True,
            )

    def parse(self, stdout):
        return dict(ln.split("=", 1) for ln in stdout.splitlines() if "=" in ln)

    def test_draft_is_human_end_to_end(self):
        proc = self.run_cli("--is-draft", "true")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = self.parse(proc.stdout)
        self.assertEqual(fields.get("decision"), "human")
        self.assertIn("draft", fields.get("reason", ""))

    def test_not_a_draft_merges_end_to_end(self):
        proc = self.run_cli("--is-draft", "false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "merge")

    def test_flag_omitted_merges_end_to_end(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.parse(proc.stdout).get("decision"), "merge")

    def test_an_unparseable_draft_flag_fails_loud_and_decides_nothing(self):
        """`gh pr view --json isDraft` yields the JSON literal true/false. A
        value that is neither means the caller broke — exit 2, the job goes
        red, and nothing merges. Never guess, never fail open."""
        proc = self.run_cli("--is-draft", "yes")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("decision=", proc.stdout)
        self.assertIn("is-draft", proc.stderr)


class Pr323RegressionTest(unittest.TestCase):
    """The incident's own inputs, through the CLI, in one place: green CI, an
    APPROVE bound to 09b52e93…, and `isDraft: true`. Before this card the
    only line here was `decision=merge`, and the merge that followed it
    failed with exit 1."""

    def test_the_gate_no_longer_hands_a_draft_to_gh_pr_merge(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cr, cm, wr, cp = (d / "cr.json", d / "cm.json",
                              d / "wr.json", d / "cp.json")
            cr.write_text(json.dumps({"check_runs": GREEN_CI}))
            cm.write_text(json.dumps(critic()))
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(json.dumps({"status": "ahead"}))
            proc = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                 "--check-runs-file", str(cr), "--comments-file", str(cm),
                 "--workflow-runs-file", str(wr), "--compare-file", str(cp),
                 "--merge-state", "DRAFT",
                 "--head-branch", "agent/DRE-3389-verdict-evidence",
                 "--pr-author", "agent-bureau-bot[bot]",
                 "--is-draft", "true"],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = dict(
            ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln
        )
        self.assertEqual(fields.get("decision"), "human", proc.stdout)


class WorkflowWiringTest(unittest.TestCase):
    """merge-gate.yml must READ the draft state from GitHub's own record and
    pass it to the decision — the gate's inputs all come from GitHub, never
    from an agent's claim."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(WORKFLOW.read_text())
        steps = cls.doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1
        cls.run_block = runs[0]

    def test_draft_state_comes_from_githubs_own_pr_record(self):
        self.assertIn("--json isDraft", self.run_block)
        self.assertIn('IS_DRAFT=', self.run_block)

    def test_the_draft_state_is_passed_to_the_decision(self):
        self.assertIn('--is-draft "$IS_DRAFT"', self.run_block)

    def test_the_read_is_not_swallowed(self):
        """No `|| true` / `|| echo` fallback on the draft read: an
        unreadable draft state must kill the step (set -e), exactly as the
        head SHA and mergeStateStatus reads do. A default of "not a draft"
        on a blip is the failure this card fixes, re-armed."""
        line = [ln for ln in self.run_block.splitlines() if "IS_DRAFT=" in ln]
        self.assertEqual(len(line), 1, self.run_block)
        self.assertNotIn("||", line[0])

    def test_the_human_arm_still_precedes_the_merge_guard(self):
        """The draft decision rides the arm DRE-2039 built: post once
        through gate_note.py, exit before `gh pr merge` is ever reached."""
        human = self.run_block.find('"$DECISION" = "human"')
        note = self.run_block.find("python3 .bureau-pipeline/scripts/gate_note.py")
        merge_guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        merge = self.run_block.find("gh pr merge")
        self.assertGreater(human, -1, "no human-decision arm")
        self.assertGreater(note, human, "honest state not posted")
        self.assertGreater(merge_guard, note)
        self.assertGreater(merge, merge_guard)


if __name__ == "__main__":
    unittest.main()
