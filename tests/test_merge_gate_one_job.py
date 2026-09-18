"""RED-first tests for DRE-4279 — a merge-gate evaluation bills one minute, not two.

GitHub bills every job rounded UP to a whole minute. The gate ran TWO jobs per
evaluation — `resolve` (~0.1 min, "which PR is this event about?") and
`evaluate` (~0.3 min) — so 24 seconds of work billed two minutes, and it did so
1,200+ times in three days across agent-bureau and bureau-harness. Measured
2026-09-15..18 on agent-bureau + portico: 975 non-skipped gate wakes, ~265 a
day, every one of them two jobs.

The rule these tests express:

  1. When the event already NAMES its pull request, the gate is ONE job.
     `issue_comment` carries `issue.number`, `workflow_dispatch` carries
     `inputs.pr_number`, and a `workflow_run` for a same-repo `pull_request`
     run carries `workflow_run.pull_requests[0].number` (24 of 24 live CI
     runs sampled on agent-bureau and portico named their PR). `resolve`
     runs ONLY for a workflow_run that names no PR — the lookup is the
     fallback, not the funnel.
  2. Two evaluations of ONE pull request never overlap, whichever leg woke
     them: the concurrency group renders to the same string for the same PR
     from every leg, and the PR the job evaluates is the PR it is keyed on —
     one expression, pinned equal (DRE-2508's property, kept).
  3. The event-leg filter (DRE-1987 qa-bot authorship, the branch prefixes,
     `workflow_run.event == 'pull_request'`) moves with the funnel: it lives
     ONCE, on `evaluate`, and no leg reaches the evaluation around it. A
     FAILED resolve still cannot let the gate evaluate a guessed PR.
  4. The `issue_comment` predicate is UNCHANGED, on purpose, and the reason is
     the three-day measurement (see the last class): the only comment class
     that provably cannot change gate state was 3 of 279 wakes.

The job-ifs and the group are LIVE-EXTRACTED from merge-gate.yml and evaluated
the way GitHub evaluates them (scripts/fix_concurrency.py, extended here with
the array index, `startsWith()` and `cancelled()` the gate's expressions use),
so a revert turns this suite red instead of passing against a copy.

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_one_job.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import fix_concurrency as fc  # noqa: E402

PR = 2612
QA_BOT = "agent-bureau-qa-bot[bot]"
APPROVE_BODY = f"🔎 QA Critic — VERDICT: APPROVE @{'ab' * 20} content:{'cd' * 32}"
REQUEST_CHANGES_BODY = f"🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @{'ab' * 20}"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _job(name: str) -> dict:
    return _doc()["jobs"][name]


def _if(name: str) -> str:
    cond = _job(name).get("if")
    assert cond, f"merge-gate.yml job {name!r} has no `if:`"
    return str(cond)


def _needs(result: str, pr: str = "") -> dict:
    """What `needs.resolve` reads as after that job ended in `result`. A
    skipped job has no outputs; GitHub renders them as the empty string."""
    return {"needs": {"resolve": {"result": result, "outputs": {"pr": pr}}}}


def workflow_run_event(branch: str, pr: int | None = PR,
                       run_event: str = "pull_request") -> dict:
    """The context the gate sees when a CI workflow completes: the
    triggering run's own event, head branch, and — for a same-repo
    pull_request run — the pull request(s) it was created for."""
    return {
        "github": {
            "event_name": "workflow_run",
            "event": {
                "workflow_run": {
                    "event": run_event,
                    "head_branch": branch,
                    "pull_requests": [] if pr is None else [
                        {"number": pr, "head": {"ref": branch}}
                    ],
                },
            },
        },
        "inputs": {},
    }


def comment_event(body: str, login: str = QA_BOT, pr: int = PR) -> dict:
    return fc.comment_event(pr, login, body)


def dispatch_event(pr: int = PR) -> dict:
    return fc.dispatch_event(pr)


def _ctx(event: dict, needs: dict | None = None) -> dict:
    ctx = dict(event)
    ctx.update(needs or _needs("skipped"))
    return ctx


def resolve_runs(event: dict) -> bool:
    return fc._truthy(fc.evaluate(_if("resolve"), _ctx(event)))


def evaluate_runs(event: dict, needs: dict | None = None) -> bool:
    return fc._truthy(fc.evaluate(_if("evaluate"), _ctx(event, needs)))


# --------------------------------------------------------------------------
# The expression support the gate's `if:` needs (scripts/fix_concurrency.py)
# --------------------------------------------------------------------------
class ExpressionSupportTest(unittest.TestCase):
    """The evaluator raises on anything it cannot parse rather than guessing;
    these are the three shapes the gate's expressions add to the two shipped
    ones it was written for."""

    def test_an_array_index_reads_the_member(self):
        ctx = {"github": {"event": {"workflow_run": {"pull_requests": [{"number": 7}]}}}}
        self.assertEqual(
            fc.evaluate("github.event.workflow_run.pull_requests[0].number", ctx), 7
        )

    def test_an_index_into_an_empty_array_is_null(self):
        """`pull_requests` is EMPTY for a fork PR and for a run whose PR closed
        before it finished — the fallback to `resolve` hangs on this reading
        null, never raising."""
        ctx = {"github": {"event": {"workflow_run": {"pull_requests": []}}}}
        self.assertIsNone(
            fc.evaluate("github.event.workflow_run.pull_requests[0].number", ctx)
        )
        self.assertTrue(
            fc.evaluate("!github.event.workflow_run.pull_requests[0].number", ctx)
        )

    def test_an_index_into_a_missing_path_is_null(self):
        self.assertIsNone(fc.evaluate("github.event.workflow_run.pull_requests[0].number", {}))

    def test_starts_with_is_case_insensitive_like_githubs(self):
        ctx = {"github": {"event": {"workflow_run": {"head_branch": "Agent/DRE-1-x"}}}}
        self.assertTrue(fc.evaluate("startsWith(github.event.workflow_run.head_branch, 'agent/')", ctx))
        self.assertFalse(fc.evaluate("startsWith(github.event.workflow_run.head_branch, 'repair/')", ctx))
        self.assertFalse(fc.evaluate("startsWith(github.event.missing, 'agent/')", ctx))

    def test_cancelled_reads_the_runs_status(self):
        self.assertFalse(fc.evaluate("cancelled()", {}))
        self.assertTrue(fc.evaluate("!cancelled()", {}))
        self.assertTrue(fc.evaluate("cancelled()", {"cancelled": True}))


# --------------------------------------------------------------------------
# 1. an event that names its PR is ONE job; resolve is the fallback
# --------------------------------------------------------------------------
class OneJobWhenTheEventNamesItsPrTest(unittest.TestCase):
    def test_a_verdict_comment_skips_resolve_and_evaluates(self):
        event = comment_event(APPROVE_BODY)
        self.assertFalse(resolve_runs(event), "issue.number names the PR — no lookup job")
        self.assertTrue(evaluate_runs(event, _needs("skipped")))

    def test_a_hand_dispatch_skips_resolve_and_evaluates(self):
        event = dispatch_event()
        self.assertFalse(resolve_runs(event), "inputs.pr_number names the PR — no lookup job")
        self.assertTrue(evaluate_runs(event, _needs("skipped")))

    def test_a_ci_completion_that_names_its_pr_skips_resolve_and_evaluates(self):
        event = workflow_run_event("agent/DRE-4279-one-gate-job", pr=PR)
        self.assertFalse(
            resolve_runs(event),
            "workflow_run.pull_requests names the PR — no lookup job",
        )
        self.assertTrue(evaluate_runs(event, _needs("skipped")))

    def test_a_ci_completion_that_names_no_pr_still_gets_resolve(self):
        """The fallback: a fork PR, or a run whose PR closed before it
        finished, carries an empty `pull_requests` — the lookup job runs and
        evaluate follows it, exactly as before this card."""
        event = workflow_run_event("agent/DRE-4279-one-gate-job", pr=None)
        self.assertTrue(resolve_runs(event))
        self.assertTrue(evaluate_runs(event, _needs("success", str(PR))))

    def test_resolve_never_runs_for_a_push_run(self):
        # CI on main completing is not a PR event — no lookup minute for it.
        event = workflow_run_event("main", pr=None, run_event="push")
        self.assertFalse(resolve_runs(event))
        self.assertFalse(evaluate_runs(event, _needs("skipped")))

    def test_the_resolve_step_only_has_the_lookup_left(self):
        """The shell no longer branches on the event: it resolves ONE thing,
        the open PR for a workflow_run's head branch, and names its state."""
        run = "\n".join(s.get("run", "") for s in _job("resolve")["steps"])
        self.assertIn("gh pr list", run)
        self.assertIn("--state open", run)
        for gone in ("workflow_dispatch)", "IC_PR", "INPUT_PR"):
            self.assertNotIn(gone, run, f"resolve still handles a leg that names its PR: {gone}")


# --------------------------------------------------------------------------
# 2. two evaluations of one PR never overlap
# --------------------------------------------------------------------------
class NeverOverlapTest(unittest.TestCase):
    def setUp(self):
        self.job = _job("evaluate")
        self.group = self.job["concurrency"]["group"]

    def _rendered(self, event: dict, needs: dict) -> str:
        return fc.interpolate(self.group, _ctx(event, needs))

    def test_every_leg_renders_the_same_group_for_the_same_pr(self):
        legs = {
            "issue_comment": (comment_event(APPROVE_BODY), _needs("skipped")),
            "workflow_dispatch": (dispatch_event(), _needs("skipped")),
            "workflow_run named": (workflow_run_event("agent/x", pr=PR), _needs("skipped")),
            "workflow_run resolved": (workflow_run_event("agent/x", pr=None), _needs("success", str(PR))),
        }
        rendered = {leg: self._rendered(*args) for leg, args in legs.items()}
        self.assertEqual(
            len(set(rendered.values())), 1,
            f"the same PR lands in different groups by leg — they can run "
            f"concurrently (DRE-2508): {rendered}",
        )
        self.assertIn(str(PR), next(iter(rendered.values())))

    def test_two_prs_never_share_a_group(self):
        a = self._rendered(comment_event(APPROVE_BODY, pr=PR), _needs("skipped"))
        b = self._rendered(comment_event(APPROVE_BODY, pr=PR + 1), _needs("skipped"))
        self.assertNotEqual(a, b)

    def test_the_resolved_number_wins_over_the_events_own(self):
        # When resolve DID run, its answer is the identity — the event's own
        # field is empty on that leg by construction, but pin the precedence.
        rendered = self._rendered(workflow_run_event("agent/x", pr=None), _needs("success", "4444"))
        self.assertIn("4444", rendered)

    def test_the_evaluated_pr_is_the_keyed_pr(self):
        """One expression, two uses: the `PR` the step evaluates and the group
        the job is serialized on must be the SAME expression, so the
        serialization and the evaluation can never be about different PRs."""
        steps = self.job["steps"]
        env = next(s for s in steps if s.get("name") == "Evaluate and merge")["env"]
        pr_expr = env["PR"].strip()
        self.assertTrue(pr_expr.startswith("${{") and pr_expr.endswith("}}"), pr_expr)
        self.assertIn(pr_expr, self.group, "the group is not keyed on the PR expression the step evaluates")

    def test_a_queued_evaluation_is_not_cancelled(self):
        # Cancelling would drop the wake that was going to merge; the gate
        # would then wait for reconcile's ~15-minute nudge.
        self.assertIs(self.job["concurrency"]["cancel-in-progress"], False)


# --------------------------------------------------------------------------
# 3. the leg filter lives on evaluate, once, and nothing gets around it
# --------------------------------------------------------------------------
class LegFilterTest(unittest.TestCase):
    def test_evaluate_runs_when_resolve_was_skipped(self):
        """`needs: resolve` plus the implicit success() would SKIP evaluate
        whenever resolve was skipped — i.e. on every leg this card makes one
        job. The `if:` must open with a status function that admits a skipped
        dependency and still refuses a cancelled run."""
        job = _job("evaluate")
        self.assertIn("resolve", job.get("needs") or [])
        cond = " ".join(_if("evaluate").split())
        self.assertTrue(
            cond.startswith("!cancelled() &&"),
            f"evaluate's if does not open with !cancelled(): {cond[:60]}",
        )
        self.assertFalse(evaluate_runs(comment_event(APPROVE_BODY), {**_needs("skipped"), "cancelled": True}))

    def test_a_failed_resolve_never_lets_the_gate_evaluate_a_guessed_pr(self):
        event = workflow_run_event("agent/x", pr=None)
        self.assertFalse(evaluate_runs(event, _needs("failure")))
        self.assertFalse(evaluate_runs(event, _needs("success", "")), "an empty resolution evaluates nothing")

    def test_a_human_typing_qa_critic_does_not_wake_the_gate(self):
        event = comment_event(APPROVE_BODY, login="someone")
        self.assertFalse(resolve_runs(event))
        self.assertFalse(evaluate_runs(event))

    def test_a_qa_bot_comment_without_the_marker_does_not_wake_the_gate(self):
        event = comment_event("⏸️ Merge gate: waiting for human merge — semver major")
        self.assertFalse(evaluate_runs(event))

    def test_a_comment_on_an_issue_does_not_wake_the_gate(self):
        event = comment_event(APPROVE_BODY)
        del event["github"]["event"]["issue"]["pull_request"]
        self.assertFalse(evaluate_runs(event))

    def test_ci_on_a_hand_named_branch_does_not_wake_the_gate(self):
        for branch in ("fix/typo", "feature/x", "bot/anything-else", "main"):
            with self.subTest(branch=branch):
                event = workflow_run_event(branch, pr=PR)
                self.assertFalse(resolve_runs(event))
                self.assertFalse(evaluate_runs(event))

    def test_ci_on_every_gated_branch_shape_wakes_the_gate(self):
        for branch in (
            "agent/DRE-1-x", "repair/red-main-1", "dependabot/pip/x-1.2",
            "bot/standards-sync", "bot/split-ledger", "bot/model-drift",
        ):
            with self.subTest(branch=branch):
                self.assertTrue(evaluate_runs(workflow_run_event(branch, pr=PR)))

    def test_a_dispatch_without_a_pr_number_evaluates_nothing(self):
        event = dispatch_event()
        event["github"]["event"]["inputs"]["pr_number"] = ""
        event["inputs"]["pr_number"] = ""
        self.assertFalse(evaluate_runs(event))

    def test_the_qa_bot_identity_gate_is_written_once(self):
        """The hardcoded login (DRE-2120's roster) sits on evaluate's if and
        nowhere else in the file's expressions."""
        text = WORKFLOW.read_text()
        sites = [
            ln for ln in text.splitlines()
            if QA_BOT in ln and not ln.lstrip().startswith("#")
        ]
        self.assertEqual(len(sites), 1, sites)
        self.assertIn(f"github.event.comment.user.login == '{QA_BOT}'", _if("evaluate"))
        self.assertNotIn(QA_BOT, _if("resolve"))


# --------------------------------------------------------------------------
# 4. the issue_comment predicate, measured and deliberately unchanged
# --------------------------------------------------------------------------
class CommentPredicateMeasuredTest(unittest.TestCase):
    """The card asks that comment events which cannot change gate state be
    declined, with the predicate chosen from a three-day count. Counted
    2026-09-15..18 (agent-bureau 94 non-skipped comment wakes, portico 185):

        279 / 279  qa-bot "QA Critic" comments — nothing else got through
        130        VERDICT: APPROVE
         90        VERDICT: REQUEST_CHANGES
         59        neutral holds (infra error, too large, no verdict, evidence)
          3        posted after the PR had already closed (the one provable no-op)

    The 149 non-APPROVE wakes are NOT declined: merge_gate.decide() runs the
    conflict arm FIRST, so a REQUEST_CHANGES or a hold on a conflicted PR
    dispatches the fix agent, and 20 of agent-bureau's 94 verdicts came from
    workflow_dispatch re-reviews whose completion never wakes the gate — the
    comment is their only wake. The 3 closed-PR wakes (1%) do not pay for a
    predicate. A comment that can change state still wakes the gate; the
    saving is the job the wake no longer needs."""

    def test_a_request_changes_verdict_still_wakes_the_gate(self):
        self.assertTrue(evaluate_runs(comment_event(REQUEST_CHANGES_BODY)))

    def test_a_neutral_hold_still_wakes_the_gate(self):
        hold = "🔎 QA Critic could not run (infra error) — re-review needed, this is NOT a code rejection."
        self.assertTrue(evaluate_runs(comment_event(hold)))

    def test_an_approve_verdict_wakes_the_gate_in_one_job(self):
        event = comment_event(APPROVE_BODY)
        self.assertFalse(resolve_runs(event))
        self.assertTrue(evaluate_runs(event))


if __name__ == "__main__":
    unittest.main()
