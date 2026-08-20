"""The freshness race (DRE-2416): a green, approved branch is merged as it
stands — the gate no longer re-merges its base into it.

WHAT CHANGED, AND ON WHOSE AUTHORITY. The fleet does NOT require up-to-date
branches, and the MERGE GATE is the single writer of that rule (CEO decision
2026-08-20, recorded on DRE-2597; the rule itself is agent-bureau's
`architecture/decisions/adr-one-writer-per-fact.md`). Branch protection
cannot hold the rule fleet-wide — the reference deployment `EveryBite/atlas`
returns 403 on its protection endpoint — and `required_status_checks.strict`
reads `false` on every repo checked, so a re-merge buys nothing the base
branch demands.

THE RACE IT FIXES, measured live on `dreadnought-foundry/portico` PR 268
(DRE-2393, 2026-08-12 22:43-22:56 UTC). Condition 0 used to return `update`
for a stale-but-otherwise-merge-ready PR; the gate merged the base in, the
new head restarted the full CI suite, and by the time that suite was green
the base had moved again. Four full CI runs on one branch in thirteen
minutes, none of them on changed source, every one of them green, and the PR
still read BLOCKED:

    11d2b95  success 22:43:46  the card's own last commit
    ef2b1b9  success 22:49:22  bot merged main (after PR 266)
    9bfe6ee  success 22:52:59  bot merged main (after PR 267)
    208ed7b  in_progress 22:56:10  bot merged main (after PR 265)

The shape is self-feeding: each merge the gate completes moves the base,
which re-merges into every OTHER open agent branch and restarts their CI. N
concurrent PRs cost N-1 restarts per merge. DRE-2274 (currency evaluated
LAST) converted that waste into one restart per readiness cycle; this card
removes the restart entirely for the case that never needed it.

THE NEW CONDITION 0 — CONFLICT, not currency:

  * the branch conflicts with its base (mergeStateStatus DIRTY) → `conflict`:
    only reconciling the branch can change the answer, and `update-branch`
    cannot resolve a textual conflict, so the fix agent gets it (the arm the
    workflow already ran, now behind a machine-readable decision).
  * anything else → the compare status is recorded as a NOTE and never
    gates. Behind, diverged, unverifiable: the branch merges on CI green +
    a bound APPROVE, exactly as a current one does.

THE ACCEPTED COST, stated plainly because it is real: two individually-green
PRs can break the base together — the DRE-1924 `asana` class, which is why
condition 0 existed. It is caught in minutes by the push-to-main run going
red plus `medic.yml` filing a repair card
(`architecture/decisions/adr-red-main-auto-repair.md`), and made rare by the
disjoint-file-ownership rule. That trade is the decision above, not this
suite's to relitigate — what this suite pins is that the gate implements it.
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


def critic_ok(sha=HEAD):
    return [{
        "user": {"login": QA_LOGIN, "type": "Bot"},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{sha}",
    }]


def decide(compare_status="behind", merge_state="CLEAN", checks=None,
           comments=None, head_sha=HEAD, **kw):
    return merge_gate.decide(
        head_sha=head_sha,
        qa_login=QA_LOGIN,
        check_runs=GREEN_CI if checks is None else checks,
        comments=critic_ok(head_sha) if comments is None else comments,
        compare_status=compare_status,
        merge_state=merge_state,
        **kw,
    )


class NoRemergeWhenMergeableTest(unittest.TestCase):
    """Criterion 1: green + mergeable + not conflicting → NO re-merge.

    Every one of these decided `update` before this card, and every `update`
    cost a full CI suite on unchanged source."""

    def test_behind_and_merge_ready_merges_without_touching_the_branch(self):
        for status in ("behind", "diverged"):
            d = decide(compare_status=status)
            self.assertEqual(d.action, "merge", f"status={status}: {d.reason}")

    def test_current_branch_still_merges(self):
        for status in ("ahead", "identical"):
            d = decide(compare_status=status)
            self.assertEqual(d.action, "merge", f"status={status}: {d.reason}")

    def test_unverifiable_compare_no_longer_stalls_the_merge(self):
        """A compare-API blip used to be `wait`, fail-closed — sound while
        currency was a gate, and pure starvation once it is not: the PR sat
        green and approved waiting on a record that decides nothing. The
        blip still costs the CONTENT id (no carry — a stale verdict then
        needs a fresh review), which is where the fail-closed direction
        actually belongs."""
        for status in (None, "", "garbage"):
            d = decide(compare_status=status)
            self.assertEqual(d.action, "merge", f"status={status!r}: {d.reason}")

    def test_update_is_no_longer_in_the_decision_vocabulary(self):
        """Non-vacuous twin: `update` must be unreachable, not merely
        unreached on these inputs. Nothing in the gate may emit it."""
        for status in ("behind", "diverged", "ahead", "identical", None):
            for state in ("CLEAN", "UNSTABLE", "BLOCKED", "BEHIND", "UNKNOWN"):
                self.assertNotEqual(
                    decide(compare_status=status, merge_state=state).action,
                    "update",
                    f"status={status!r} merge_state={state!r} still updates",
                )

    def test_a_behind_head_says_so_in_a_note(self):
        """Nobody here reads diffs, so the run log has to explain itself:
        merging a behind-base head is deliberate and is recorded as such."""
        d = decide(compare_status="behind")
        self.assertTrue(
            any("behind" in n for n in d.notes),
            f"no currency note on a behind head: {d.notes}",
        )
        self.assertFalse(
            [n for n in decide(compare_status="ahead").notes if "behind" in n],
            "a current head must not claim to be behind",
        )


class ConflictStillReconcilesTest(unittest.TestCase):
    """Criterion 1's other half: conflicting → re-merge. A textual conflict
    is the one case where reconciling the branch with its base changes the
    answer — and the one case `update-branch` cannot do, so the fix agent
    owns it (the arm merge-gate.yml already ran on mergeStateStatus DIRTY,
    now behind the script's decision)."""

    def test_dirty_branch_decides_conflict(self):
        d = decide(merge_state="DIRTY")
        self.assertEqual(d.action, "conflict", d.reason)
        self.assertIn("conflict", d.reason)

    def test_conflict_beats_every_other_condition(self):
        """The pre-DRE-2416 workflow read mergeStateStatus BEFORE gathering
        anything else and dispatched the fix agent regardless of CI or
        verdict state. Moving the decision into the script must not change
        that: a conflicted branch reaches the fix agent even with red CI, no
        verdict, or a standing REQUEST_CHANGES."""
        self.assertEqual(decide(merge_state="DIRTY", checks=RED_CI).action,
                         "conflict")
        self.assertEqual(decide(merge_state="DIRTY", comments=[]).action,
                         "conflict")
        self.assertEqual(
            decide(
                merge_state="DIRTY",
                comments=[{"user": {"login": QA_LOGIN, "type": "Bot"},
                           "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}"}],
            ).action,
            "conflict",
        )

    def test_non_dirty_states_are_not_conflicts(self):
        """Faithful port of the shell's `[ "$MSTATE" = "DIRTY" ]`: UNKNOWN
        is GitHub still computing mergeability lazily (DRE-2121), not a
        conflict, and treating it as one would reintroduce exactly the
        indefinite stall this card exists to remove. reconcile owns the
        UNKNOWN poll."""
        for state in ("CLEAN", "UNSTABLE", "BLOCKED", "BEHIND", "HAS_HOOKS",
                      "UNKNOWN", "", None):
            self.assertEqual(decide(merge_state=state).action, "merge",
                             f"merge_state={state!r} is not a conflict")

    def test_conflict_is_not_a_merge_under_any_other_input(self):
        """Non-vacuous twin: DIRTY never merges, whatever else is green."""
        for status in ("ahead", "identical", "behind", "diverged", None):
            self.assertNotEqual(
                decide(compare_status=status, merge_state="DIRTY").action,
                "merge",
                f"DIRTY merged with compare status {status!r}",
            )


class BurstTest(unittest.TestCase):
    """Criterion 2: landing N PRs in a burst costs no CI restarts on the
    open branches.

    BEFORE (measured, portico PR 268, DRE-2393): four full CI suites on one
    branch in thirteen minutes — one for the branch's own last commit plus
    one per merge to main (PRs 266, 267, 265). Generalised: N concurrent
    PRs cost N-1 gate-initiated restarts per merge, because each merge moves
    the base and the base move re-merges into every other open branch.

    AFTER: zero. A gate-initiated head move is the ONLY restart this gate
    can cause, and the gate no longer moves a head. The live figure for a
    real burst is an observation the pipeline makes after this merges; what
    is proved here is that the decision producing those restarts is gone.
    """

    def test_a_burst_of_merges_produces_no_gate_initiated_restarts(self):
        """Five open agent PRs, each green with an APPROVE bound to its own
        head, while the base moves once per merge. Every gate wake — five
        PRs x five base moves — must decide `merge`, so no head ever moves
        and no CI suite is ever restarted by the gate."""
        heads = [f"{i:040x}" for i in range(1, 6)]
        restarts = 0
        actions = []
        for _base_move in range(len(heads)):
            for head in heads:
                d = decide(compare_status="behind", head_sha=head)
                actions.append(d.action)
                # The gate's only lever on a branch head is the re-merge it
                # no longer has; any non-merge, non-terminal decision that
                # moved the head would show up here.
                if d.action == "conflict":
                    restarts += 1
        self.assertEqual(set(actions), {"merge"}, f"burst decisions: {actions}")
        self.assertEqual(restarts, 0)

    def test_the_portico_268_sequence_costs_one_ci_run_not_four(self):
        """Replay of the measured sequence. The branch is green and approved
        at its own last commit (11d2b95's stand-in) and behind main; the
        three heads that followed existed ONLY because the gate re-merged
        main. The gate now merges at the first head, so the three
        bot-created heads — and their three CI suites — never exist."""
        first_head = "11d2b95" + "0" * 33
        d = decide(compare_status="behind", head_sha=first_head)
        self.assertEqual(d.action, "merge", d.reason)
        ci_runs_caused_by_the_gate = 0 if d.action == "merge" else 1
        self.assertEqual(ci_runs_caused_by_the_gate, 0)


class StillNeverMergesTest(unittest.TestCase):
    """What this card does NOT relax. Freshness stopped being a gate; every
    other condition is untouched, so a green-alone branch still cannot ride
    in on red CI or an unbound verdict."""

    def test_red_ci_still_waits(self):
        d = decide(checks=RED_CI)
        self.assertEqual(d.action, "wait")
        self.assertIn("not green", d.reason)

    def test_missing_verdict_still_waits(self):
        d = decide(comments=[])
        self.assertEqual(d.action, "wait")
        self.assertIn("no critic verdict yet", d.reason)

    def test_stale_verdict_still_waits(self):
        d = decide(comments=critic_ok("dd44" * 10))
        self.assertEqual(d.action, "wait")
        self.assertIn("stale", d.reason)

    def test_request_changes_still_holds(self):
        d = decide(comments=[{
            "user": {"login": QA_LOGIN, "type": "Bot"},
            "body": f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}",
        }])
        self.assertEqual(d.action, "hold")

    def test_dependabot_major_is_still_human_even_when_conflicted(self):
        """Condition D's `human` is the gate saying it will NEVER merge this
        PR. A DIRTY dependabot PR must still not reach the fix agent —
        Dependabot rebases and recreates its own conflicted PRs, and there
        is no card for the fix agent to work (DRE-2039). The conflict arm
        keeps that exemption; here the honest human state must still win for
        a clean one."""
        major = {"sha": "c" * 40, "commit": {"message": (
            "Bump requests from 2.32.0 to 3.0.0\n\n---\n"
            "updated-dependencies:\n- dependency-name: requests\n"
            "  update-type: version-update:semver-major\n...\n"
        )}}
        d = decide(
            compare_status="behind",
            head_branch="dependabot/pip/requests-3.0.0",
            pr_author="dependabot[bot]",
            pr_commits=[major],
        )
        self.assertEqual(d.action, "human", d.reason)


class CliContractTest(unittest.TestCase):
    """The workflow-facing contract: --merge-state carries GitHub's own
    mergeability answer, and the compare payload is still read (for the
    content id) without deciding currency."""

    def run_cli(self, compare_payload, merge_state=None, comments=None):
        with tempfile.TemporaryDirectory() as td:
            cr = Path(td) / "check-runs.json"
            cm = Path(td) / "comments.json"
            wr = Path(td) / "workflow-runs.json"
            cp = Path(td) / "compare.json"
            cr.write_text(json.dumps({"check_runs": GREEN_CI}))
            cm.write_text(json.dumps(critic_ok() if comments is None else comments))
            wr.write_text(json.dumps({"workflow_runs": []}))
            cp.write_text(compare_payload)
            argv = [sys.executable, str(SCRIPT),
                    "--head-sha", HEAD, "--qa-login", QA_LOGIN,
                    "--check-runs-file", str(cr), "--comments-file", str(cm),
                    "--workflow-runs-file", str(wr), "--compare-file", str(cp)]
            if merge_state is not None:
                argv += ["--merge-state", merge_state]
            return subprocess.run(argv, capture_output=True, text=True)

    def decision(self, proc):
        fields = dict(
            ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln
        )
        return fields.get("decision")

    def test_behind_payload_decides_merge(self):
        proc = self.run_cli(json.dumps({"status": "behind"}), "CLEAN")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_dirty_merge_state_decides_conflict(self):
        proc = self.run_cli(json.dumps({"status": "ahead"}), "DIRTY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "conflict")

    def test_merge_state_is_optional_and_defaults_to_no_conflict(self):
        """Every caller that never passes it keeps the pre-DRE-2416
        behaviour on this axis: no conflict claimed on absent data."""
        proc = self.run_cli(json.dumps({"status": "behind"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_blip_substitute_no_longer_blocks_the_merge(self):
        proc = self.run_cli("{}", "CLEAN")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.decision(proc), "merge")

    def test_malformed_compare_still_fails_loud_and_never_merges(self):
        proc = self.run_cli("not json", "CLEAN")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("decision=merge", proc.stdout)


class WiringTest(unittest.TestCase):
    """merge-gate.yml: the re-merge is gone, and the conflict arm is behind
    the script's machine-readable decision."""

    def setUp(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
        assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
        self.run_block = runs[0]

    def test_the_update_branch_call_is_gone(self):
        """THE fix: merge-gate.yml:350 was the freshening call this card
        removes. Nothing in the gate may re-merge a base into a branch."""
        self.assertNotIn("update-branch", self.run_block)
        self.assertNotIn('"$DECISION" = "update"', self.run_block)

    def test_merge_state_is_read_once_and_passed_to_the_script(self):
        self.assertIn("mergeStateStatus", self.run_block)
        self.assertIn('--merge-state "$MSTATE"', self.run_block)

    def test_conflict_arm_is_behind_the_conflict_decision(self):
        """Same shape the update arm had: the fix-agent dispatch is
        reachable only via `decision=conflict`, and sits before the merge
        guard so a conflicted PR never falls through to `gh pr merge`."""
        guard = self.run_block.find('[ "$DECISION" = "conflict" ]')
        self.assertGreater(guard, -1, "conflict decision guard missing")
        dispatch = self.run_block.find("gh workflow run")
        self.assertGreater(dispatch, guard,
                           "fix-agent dispatch not behind the guard")
        merge_guard = self.run_block.find('[ "$DECISION" = "merge" ] || exit 0')
        self.assertGreater(merge_guard, dispatch,
                           "conflict arm must precede the merge guard")

    def test_dependabot_conflicts_still_never_reach_the_fix_agent(self):
        """DRE-2039 exemption preserved across the move: Dependabot rebases
        and recreates its own conflicted PRs, and the fix agent has no card
        to work on one."""
        arm = self.run_block[self.run_block.find('[ "$DECISION" = "conflict" ]'):]
        self.assertIn("dependabot/*", arm)

    def test_compare_record_is_still_gathered_for_the_content_id(self):
        """Currency stopped being a gate; the SAME payload is still the
        content-binding record (DRE-2340), so the fetch and its fail-closed
        `{}` substitute must stay."""
        self.assertIn("compare/$BASE...$SHA", self.run_block)
        self.assertIn("--compare-file", self.run_block)
        self.assertIn("echo '{}' >", self.run_block)

    def test_merge_still_behind_qa_bot_token_and_head_pinned(self):
        """Untouched by this card: author != merger by App identity, and the
        merge is still pinned to the evaluated head (DRE-2117)."""
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["evaluate"]["steps"]
        step = next(s for s in steps if s.get("name") == "Evaluate and merge")
        self.assertEqual(step["env"]["GH_TOKEN"],
                         "${{ steps.qa.outputs.token }}")
        line = next(ln for ln in self.run_block.splitlines()
                    if "gh pr merge" in ln)
        self.assertNotIn("github.token", line)
        self.assertIn('--match-head-commit "$SHA"', line)


if __name__ == "__main__":
    unittest.main()
