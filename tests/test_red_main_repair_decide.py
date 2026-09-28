"""Red-main auto-repair — the dispatch decision (DRE-1927, adr-red-main-auto-repair).

scripts/red_main_repair.py is the deterministic brain of the repair trigger:
given the failed run's facts (conclusion, branch, failing head SHA, logs) and
the mechanical attempt record (existing repair/* refs + repair/* PRs), it
decides dispatch / no-op / escalate BEFORE any agent spins up. These tests pin
the ADR's guardrails 2 (no crash-loop) and 3 (concurrency lock) as a decision
table:

  * classify first — an infra-fingerprinted failure (rate-limit, auth death,
    runner flake) backs off entirely: no agent, no retry (reuses
    medic_classify's signatures — the DRE-1921 discipline);
  * bounded attempts — at most 2 per distinct failing head SHA, tracked by
    the repair/<sha> branch + PR record alone (no external state); budget
    exhausted → escalate to a human, never a third swing;
  * one repair in flight per repo — any open repair/* PR makes a new failure
    event a no-op;
  * debounce by SHA — the repair/<sha> branch already existing makes a
    duplicate event a no-op;
  * fail-closed — unreadable attempt records mean no dispatch (a blind
    dispatch could double-run repairs), and only a full 40-hex SHA ever
    becomes a branch name;
  * superseded (DRE-5069) — a LATER run of the same workflow on the default
    branch that concluded `success` means `main` has already moved past the
    fault: no card, no agent. Anything short of a later green leaves the
    decision exactly where it was.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import red_main_repair  # noqa: E402

WORKFLOW = os.path.join(
    os.path.dirname(__file__), "..", ".github", "workflows",
    "red-main-repair.yml")

SHA = "a" * 40
OTHER_SHA = "b" * 40

STALE_ASSERTION_LOG = """
=== FAILURES ===
____ test_widget_count ____
    def test_widget_count():
>       assert count_widgets() == 3
E       assert 4 == 3
=== 1 failed, 41 passed ===
"""

RATE_LIMIT_LOG = "gh: API rate limit exceeded for installation ID 12345"
RUNNER_FLAKE_LOG = "The runner has received a shutdown signal."

HARNESS_WORKFLOW = "Integration Harness"

# Run 34258403698, verbatim shape: the harness reached no verdict at all
# because the SANDBOX's own reconcile sweep had died on Linear's hourly quota.
# Note what is NOT here — no medic signature matches: "rate limited" is not
# `\brate limit\b`, and the quota belongs to Linear, not to GitHub.
SANDBOX_BLOCKED_LOG = """
[gate_paths] verify
sandbox probe: harness blocked: sandbox Reconcile failure at \
2026-09-08T17:46:51Z (linear_ratelimited): reconcile: Linear API returned 400 \
from https://api.linear.app/graphql: rate limited: 2500 requests/hour exhausted

== harness summary ==
  bot_pr_flow: PASS
  gate_paths: BLOCKED at verify

harness BLOCKED BY SANDBOX — this commit is NOT proven and NOT disproven; \
the next run re-proves it.
##[error]harness blocked: sandbox Reconcile failure at 2026-09-08T17:46:51Z
##[error]Process completed with exit code 3.
"""

# The same marker, printed by the UNIT SUITE rather than by the harness:
# tests/test_harness_sandbox_deadline.py carries "harness blocked: …" fixtures,
# so a genuine code failure in that file puts the string in a Pipeline Tests
# log. That failure is a fix agent's job and must still dispatch.
UNIT_SUITE_QUOTING_THE_MARKER_LOG = """
=== FAILURES ===
____ test_a_blocked_probe_ends_the_wait ____
>       self.assertEqual(ctx.blocked, "harness blocked: sandbox is down")
E       AssertionError: None != 'harness blocked: sandbox is down'
=== 1 failed, 4823 passed ===
"""


def _decide(**overrides):
    kwargs = dict(
        conclusion="failure",
        head_branch="main",
        default_branch="main",
        head_sha=SHA,
        log_text=STALE_ASSERTION_LOG,
        refs=[],
        pulls=[],
    )
    kwargs.update(overrides)
    return red_main_repair.decide(**kwargs)


def _pull(head_ref, state="open", merged=False):
    return {"head_ref": head_ref, "state": state, "merged": merged}


class ClassifyTest(unittest.TestCase):
    """Guardrail 2: classify before acting — infra is NOT fixable by an agent."""

    def test_real_test_failure_is_not_infra(self):
        self.assertFalse(red_main_repair.is_infra_failure(STALE_ASSERTION_LOG))

    def test_rate_limit_is_infra_even_outside_qa_review(self):
        # medic_classify scopes its verdict to the QA-Review workflow; the
        # repair trigger backs off on the SAME signatures for ANY main-CI
        # failure — re-running against an exhausted limit deepens it.
        self.assertTrue(red_main_repair.is_infra_failure(RATE_LIMIT_LOG))

    def test_runner_flake_is_infra(self):
        self.assertTrue(red_main_repair.is_infra_failure(RUNNER_FLAKE_LOG))

    def test_signatures_come_from_medic_classify(self):
        # Single source of truth: the medic's rate-limit/auth signature list
        # (DRE-1921) must be a subset of the repair trigger's — a signature
        # added there must not silently miss here.
        import medic_classify

        for sig in medic_classify._INFRA_SIGNATURES:
            self.assertIn(
                sig, red_main_repair.INFRA_SIGNATURES,
                "repair must reuse medic_classify's infra signatures",
            )

    def test_medic_signatures_alone_do_not_see_a_sandbox_block(self):
        # The gap this class of failure fell through, pinned so the sandbox
        # clause below is provably load-bearing: run 34258403698's log carries
        # no medic fingerprint. "rate limited" is not `\\brate limit\\b`, and
        # the exhausted quota is Linear's, not GitHub's.
        self.assertFalse(
            any(sig.search(SANDBOX_BLOCKED_LOG)
                for sig in red_main_repair.INFRA_SIGNATURES),
            "if a medic signature already matched, the sandbox clause is dead code",
        )

    def test_sandbox_blocked_harness_run_is_infra(self):
        # Run 34258403698: the harness proved NOTHING about the commit — the
        # sandbox's Linear quota was exhausted — and said so in its own words.
        # No diff can turn that green; the next harness run re-proves the sha.
        self.assertTrue(
            red_main_repair.is_infra_failure(
                SANDBOX_BLOCKED_LOG, workflow_name=HARNESS_WORKFLOW
            )
        )

    def test_the_marker_alone_outside_the_harness_is_not_infra(self):
        # Scoped like medic_classify's neutral marker (_is_qa_review): the
        # string only means "the sandbox blocked us" when the HARNESS printed
        # it. The unit suite quotes it in fixtures, and a red unit suite is
        # exactly the failure a fix agent exists for.
        self.assertFalse(
            red_main_repair.is_infra_failure(
                UNIT_SUITE_QUOTING_THE_MARKER_LOG,
                workflow_name="Pipeline Tests",
            )
        )

    def test_an_unnamed_workflow_still_dispatches(self):
        # Missing information must fall toward the CURRENT behaviour: a
        # wrongly-dispatched agent costs one run, a wrongly-suppressed one
        # leaves main red with nothing watching it.
        self.assertFalse(red_main_repair.is_infra_failure(SANDBOX_BLOCKED_LOG))

    def test_a_real_harness_failure_still_dispatches(self):
        # The harness going red on its OWN assertions is a code failure and
        # stays one — the backoff keys on the block receipt, not the workflow.
        self.assertFalse(
            red_main_repair.is_infra_failure(
                "[gate_paths] FAIL at verify: expected merge by qa-bot",
                workflow_name=HARNESS_WORKFLOW,
            )
        )

    def test_block_marker_comes_from_promote_channel(self):
        # Single source of truth: the harness writes this receipt through
        # promote_channel.BLOCKED_MARKER (scripts/harness/__main__.py's
        # write_blocked_receipt) and the channel reads the same constant.
        import promote_channel

        self.assertEqual(
            red_main_repair.SANDBOX_BLOCKED_MARKER,
            promote_channel.BLOCKED_MARKER,
        )


class DecideTest(unittest.TestCase):
    def test_fresh_failure_dispatches_attempt_1(self):
        d = _decide()
        self.assertTrue(d["go"])
        self.assertEqual(d["attempt"], 1)
        self.assertEqual(d["branch"], f"repair/{SHA}")
        self.assertEqual(d["reason"], "dispatch")
        self.assertFalse(d["escalate"])

    def test_non_failure_conclusion_is_a_noop(self):
        d = _decide(conclusion="success")
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "not-a-failure")

    def test_non_default_branch_is_out_of_scope(self):
        # Branch CI failures already route through agent-fix and the medic.
        d = _decide(head_branch="agent/DRE-1-x")
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "not-default-branch")

    def test_infra_failure_backs_off_no_dispatch(self):
        # Guardrail 2: infra-crash != retry. No agent, no branch, no escalate
        # (the medic owns the retry-once; a rate-limit resets on its own).
        d = _decide(log_text=RATE_LIMIT_LOG)
        self.assertFalse(d["go"])
        self.assertFalse(d["escalate"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_sandbox_blocked_harness_run_backs_off_no_dispatch(self):
        # Guardrail 2 end to end: run 34258403698 spent a repair agent on a
        # commit the harness never judged. Same shape as any other infra
        # backoff — no agent, no branch, no triage card.
        d = _decide(
            log_text=SANDBOX_BLOCKED_LOG, workflow_name=HARNESS_WORKFLOW
        )
        self.assertFalse(d["go"])
        self.assertFalse(d["escalate"])
        self.assertEqual(d["reason"], "infra-backoff")

    def test_a_red_unit_suite_quoting_the_marker_still_dispatches(self):
        d = _decide(
            log_text=UNIT_SUITE_QUOTING_THE_MARKER_LOG,
            workflow_name="Pipeline Tests",
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")

    def test_open_repair_pr_anywhere_locks_the_repo(self):
        # Guardrail 3: one repair in flight per repo — even for a DIFFERENT
        # failing SHA (the in-flight merge will re-run CI and either clear
        # the newer failure or produce a fresh event).
        d = _decide(pulls=[_pull(f"repair/{OTHER_SHA}")])
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "repair-in-flight")

    def test_existing_branch_debounces_duplicate_events(self):
        # Guardrail 3: matrix duplicates / re-runs off the same failing SHA
        # collapse into one repair — branch exists, no PR yet → no-op.
        d = _decide(refs=[f"repair/{SHA}"])
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "duplicate-event")

    def test_closed_unmerged_pr_earns_attempt_2_on_a_new_branch(self):
        d = _decide(
            refs=[f"repair/{SHA}"],
            pulls=[_pull(f"repair/{SHA}", state="closed", merged=False)],
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["attempt"], 2)
        self.assertEqual(d["branch"], f"repair/{SHA}-2")

    def test_two_attempts_exhaust_the_budget_and_escalate(self):
        # Guardrail 2: never a third swing at the same wall.
        d = _decide(
            refs=[f"repair/{SHA}", f"repair/{SHA}-2"],
            pulls=[
                _pull(f"repair/{SHA}", state="closed", merged=False),
                _pull(f"repair/{SHA}-2", state="closed", merged=False),
            ],
        )
        self.assertFalse(d["go"])
        self.assertTrue(d["escalate"])
        self.assertEqual(d["reason"], "budget-exhausted")

    def test_merged_repair_for_this_sha_is_a_noop(self):
        # A re-run of the original failed run after the fix merged must not
        # dispatch a second repair of an already-repaired failure.
        d = _decide(pulls=[_pull(f"repair/{SHA}", state="closed", merged=True)])
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "already-repaired")

    def test_other_shas_history_does_not_consume_this_budget(self):
        d = _decide(
            pulls=[_pull(f"repair/{OTHER_SHA}", state="closed", merged=True)]
        )
        self.assertTrue(d["go"])
        self.assertEqual(d["attempt"], 1)

    def test_malformed_sha_never_becomes_a_branch(self):
        # Fail-closed: only a full 40-hex SHA is a valid branch key.
        d = _decide(head_sha="main; rm -rf /")
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "bad-head-sha")


# --------------------------------------------------------------------------- #
# Superseded (DRE-5069)                                                        #
# --------------------------------------------------------------------------- #

# 2026-09-25, from the run records. `main` forked into two alembic heads at
# 13:06 PT; CI on the fork commit 56111d9 (run 36188016837) concluded
# `failure` at 15:33 PT, twenty-one minutes after the fix (#2785) had merged
# and a later commit on `main` had already gone green. Only the short sha is
# in the record, so the full one here is padded; the green run's id is
# illustrative — its order relative to the failed run is what the rule reads.
FORK_SHA = "56111d9" + "0" * 33
FAILED_RUN = 36188016837
FAILED_CREATED = "2026-09-25T20:06:14Z"      # 13:06 PT
FIX_SHA = "0e2785f" + "1" * 33               # sorts BELOW the fork sha
GREEN_RUN = 36196100001
GREEN_CREATED = "2026-09-25T22:12:40Z"       # 15:12 PT, after the fix merged


def _branch_run(*, run_id=GREEN_RUN, head_sha=FIX_SHA, created_at=GREEN_CREATED,
                status="completed", conclusion="success", event="push"):
    return {"run_id": run_id, "head_sha": head_sha, "created_at": created_at,
            "status": status, "conclusion": conclusion, "event": event}


def _superseding_history(*branch_runs, run_attempt=1,
                         created_at=FAILED_CREATED, head_sha=FORK_SHA):
    return {
        "current": {"run_id": FAILED_RUN, "run_attempt": run_attempt,
                    "workflow_path": ".github/workflows/ci.yml",
                    "head_sha": head_sha, "created_at": created_at,
                    "log": STALE_ASSERTION_LOG, "jobs": []},
        "prior": [],
        "branch_runs": list(branch_runs),
    }


class SupersededTest(unittest.TestCase):
    """A later green run of the same workflow on `main` supersedes the red."""

    def _decide(self, history, **overrides):
        return _decide(head_sha=FORK_SHA, history=history, **overrides)

    def test_the_2026_09_25_failure_reads_superseded(self):
        # Attempt 1 of run 36188016837 bought repair run 36197298146 at
        # 15:33 PT — a card and an agent against a main with nothing broken.
        d = self._decide(_superseding_history(_branch_run()))
        self.assertFalse(d["go"])
        self.assertEqual(d["reason"], "superseded")
        self.assertFalse(d["escalate"])
        self.assertEqual(d["branch"], "")
        self.assertEqual(d["superseded_by"], str(GREEN_RUN))

    def test_the_re_run_of_the_failed_jobs_reads_superseded_too(self):
        # Attempt 2 of the same run concluded `failure` at 16:45 PT and bought
        # repair run 36202241737. A re-run keeps the run's created_at, so the
        # green run is still the later one.
        d = self._decide(_superseding_history(_branch_run(), run_attempt=2))
        self.assertEqual(d["reason"], "superseded")
        self.assertFalse(d["go"])

    def test_without_the_later_green_run_the_same_failure_dispatches(self):
        # The control: the fixture above is superseded BECAUSE of the green
        # run, not because of anything else in it.
        d = self._decide(_superseding_history())
        self.assertTrue(d["go"])
        self.assertEqual(d["reason"], "dispatch")
        self.assertEqual(d["superseded_by"], "")

    def test_superseded_outranks_the_budget_so_no_triage_card_is_filed(self):
        # A spent budget raises a card for a human. A fault main has already
        # moved past needs no human either.
        d = self._decide(
            _superseding_history(_branch_run()),
            refs=[f"repair/{FORK_SHA}", f"repair/{FORK_SHA}-2"],
            pulls=[_pull(f"repair/{FORK_SHA}", state="closed"),
                   _pull(f"repair/{FORK_SHA}-2", state="closed")],
        )
        self.assertEqual(d["reason"], "superseded")
        self.assertFalse(d["escalate"])

    def test_an_earlier_green_run_does_not_supersede(self):
        # Green BEFORE the failure is the commit the fault landed on top of.
        d = self._decide(_superseding_history(
            _branch_run(created_at="2026-09-25T19:40:00Z")))
        self.assertEqual(d["reason"], "dispatch")

    def test_later_is_read_from_the_record_never_from_the_sha_strings(self):
        # The fix sha sorts below the fork sha and is still the later commit;
        # an earlier green whose sha sorts ABOVE the fork sha is still earlier.
        self.assertLess(FIX_SHA, FORK_SHA)
        self.assertEqual(
            self._decide(_superseding_history(_branch_run()))["reason"],
            "superseded")
        self.assertEqual(
            self._decide(_superseding_history(_branch_run(
                head_sha="f" * 40, created_at="2026-09-25T19:40:00Z",
            )))["reason"],
            "dispatch")

    def test_a_later_green_run_on_the_same_commit_is_not_a_later_commit(self):
        d = self._decide(_superseding_history(_branch_run(head_sha=FORK_SHA)))
        self.assertEqual(d["reason"], "dispatch")

    def test_an_unordered_failed_run_is_never_superseded(self):
        # No created_at on the failed run means no "later" can be read at all;
        # that is "nothing is known", which lands where the decision did
        # before this rule existed.
        d = self._decide(_superseding_history(_branch_run(), created_at=""))
        self.assertEqual(d["reason"], "dispatch")

    def test_an_unordered_green_run_is_never_superseding(self):
        d = self._decide(_superseding_history(_branch_run(created_at=None)))
        self.assertEqual(d["reason"], "dispatch")

    def test_a_pull_request_run_is_not_a_run_of_the_default_branch(self):
        # A fork's PR from a branch it happened to name `main` answers the
        # same branch filter, and proves nothing about this repo's main.
        d = self._decide(_superseding_history(
            _branch_run(event="pull_request")))
        self.assertEqual(d["reason"], "dispatch")

    def test_no_history_is_not_superseded(self):
        self.assertEqual(self._decide(None)["reason"], "dispatch")

    def test_the_docstring_keeps_the_two_wasted_runs(self):
        doc = red_main_repair.__doc__
        for fact in ("36197298146", "15:33 PT", "36202241737", "16:45 PT"):
            self.assertIn(fact, doc)

    def test_every_decision_answers_superseded_by(self):
        for d in (_decide(conclusion="success"), _decide(),
                  self._decide(_superseding_history(_branch_run()))):
            self.assertIsInstance(d["superseded_by"], str)


class NotSupersededTest(unittest.TestCase):
    """A later run that is not a SUCCESS changes nothing: the same fault may
    still stand, and the debounce, budget and backoff apply as before."""

    NOT_GREEN = (
        {"status": "in_progress", "conclusion": None},
        {"status": "completed", "conclusion": "cancelled"},
        {"status": "completed", "conclusion": "failure"},
    )

    # Every decision the later run must leave untouched, by its reason today.
    SCENARIOS = {
        "dispatch": {},
        "infra-backoff": {"log_text": RATE_LIMIT_LOG},
        "duplicate-event": {"refs": [f"repair/{FORK_SHA}"]},
        "budget-exhausted": {
            "refs": [f"repair/{FORK_SHA}", f"repair/{FORK_SHA}-2"],
            "pulls": [_pull(f"repair/{FORK_SHA}", state="closed"),
                      _pull(f"repair/{FORK_SHA}-2", state="closed")],
        },
        "repair-in-flight": {"pulls": [_pull(f"repair/{OTHER_SHA}")]},
    }

    def test_a_later_run_short_of_success_leaves_the_decision_as_it_was(self):
        for reason, scenario in self.SCENARIOS.items():
            today = _decide(head_sha=FORK_SHA, **scenario)
            self.assertEqual(today["reason"], reason)
            for later in self.NOT_GREEN:
                with self.subTest(today=reason, later=later):
                    d = _decide(
                        head_sha=FORK_SHA,
                        history=_superseding_history(_branch_run(**later)),
                        **scenario,
                    )
                    self.assertEqual(d, today)

    def test_one_later_green_among_later_reds_still_supersedes(self):
        d = _decide(head_sha=FORK_SHA, history=_superseding_history(
            _branch_run(run_id=GREEN_RUN + 1, conclusion="failure",
                        created_at="2026-09-25T22:30:00Z"),
            _branch_run(),
        ))
        self.assertEqual(d["reason"], "superseded")
        self.assertEqual(d["superseded_by"], str(GREEN_RUN))


class SupersededReceiptTest(unittest.TestCase):
    """The skip is readable in the run log: the reason and the green run."""

    def _cli(self, history):
        with tempfile.TemporaryDirectory() as td:
            paths = {name: os.path.join(td, name)
                     for name in ("log", "refs", "pulls", "history")}
            open(paths["log"], "w").write(STALE_ASSERTION_LOG)
            open(paths["refs"], "w").write("[]")
            open(paths["pulls"], "w").write("[]")
            open(paths["history"], "w").write(json.dumps(history))
            return subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, "red_main_repair.py"),
                 "decide", "--conclusion", "failure",
                 "--head-branch", "main", "--default-branch", "main",
                 "--head-sha", FORK_SHA, "--log-file", paths["log"],
                 "--refs-file", paths["refs"], "--pulls-file", paths["pulls"],
                 "--history-file", paths["history"]],
                capture_output=True, text=True,
            )

    def test_the_cli_emits_the_reason_and_the_green_run(self):
        res = self._cli(_superseding_history(_branch_run()))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = CliTest._outputs(res.stdout)
        self.assertEqual(out["go"], "false")
        self.assertEqual(out["reason"], "superseded")
        self.assertEqual(out["superseded_by"], str(GREEN_RUN))
        self.assertIn(str(GREEN_RUN), res.stderr)

    @staticmethod
    def _steps():
        return yaml.safe_load(open(WORKFLOW))["jobs"]["repair"]["steps"]

    def test_the_workflow_prints_the_skip_and_the_green_run(self):
        receipts = [
            s for s in self._steps()
            if "steps.decide.outputs.reason == 'superseded'" in (s.get("if") or "")
        ]
        self.assertEqual(len(receipts), 1, "no step reports a superseded skip")
        step = receipts[0]
        env = step.get("env") or {}
        self.assertEqual(env.get("SUPERSEDED_BY"),
                         "${{ steps.decide.outputs.superseded_by }}")
        run = step.get("run") or ""
        self.assertIn("superseded", run)
        self.assertRegex(run, r"\$\{?SUPERSEDED_BY")

    def test_the_superseded_decision_reaches_no_card_and_no_agent(self):
        # go is false on a superseded decision, and these are the steps that
        # would file a card or spend a model run; each is gated on go.
        by_id = {s.get("id"): s for s in self._steps() if s.get("id")}
        for step_id in ("card", "claude"):
            self.assertIn("steps.decide.outputs.go == 'true'",
                          by_id[step_id].get("if") or "")
        triage = [s for s in self._steps()
                  if "escalate == 'true'" in (s.get("if") or "")]
        self.assertTrue(triage)


class CliTest(unittest.TestCase):
    """The workflow contract: stdout carries only key=value lines appended
    verbatim to $GITHUB_OUTPUT."""

    def _run(self, refs_payload, pulls_payload, **kw):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "log.txt")
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            open(log, "w").write(kw.get("log", STALE_ASSERTION_LOG))
            open(refs, "w").write(refs_payload)
            open(pulls, "w").write(pulls_payload)
            return subprocess.run(
                [
                    sys.executable,
                    os.path.join(SCRIPTS, "red_main_repair.py"),
                    "decide",
                    "--conclusion", kw.get("conclusion", "failure"),
                    "--head-branch", kw.get("head_branch", "main"),
                    "--default-branch", "main",
                    "--head-sha", kw.get("head_sha", SHA),
                    "--log-file", log,
                    "--refs-file", refs,
                    "--pulls-file", pulls,
                    "--workflow-name", kw.get("workflow_name", ""),
                ],
                capture_output=True,
                text=True,
            )

    @staticmethod
    def _outputs(stdout):
        return dict(
            line.split("=", 1) for line in stdout.strip().splitlines() if "=" in line
        )

    def test_dispatch_emits_github_output_lines(self):
        # Raw REST shapes: matching-refs is a list of {"ref": ...}; pulls is
        # a list of PR objects with head.ref / state / merged_at.
        res = self._run("[]", "[]")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = self._outputs(res.stdout)
        self.assertEqual(out["go"], "true")
        self.assertEqual(out["branch"], f"repair/{SHA}")
        self.assertEqual(out["attempt"], "1")
        self.assertEqual(out["escalate"], "false")
        self.assertEqual(out["reason"], "dispatch")

    def test_cli_normalizes_raw_rest_payloads(self):
        refs = json.dumps([{"ref": f"refs/heads/repair/{SHA}"}])
        pulls = json.dumps([
            {"head": {"ref": f"repair/{SHA}"}, "state": "closed",
             "merged_at": None}
        ])
        res = self._run(refs, pulls)
        out = self._outputs(res.stdout)
        self.assertEqual(out["go"], "true")
        self.assertEqual(out["attempt"], "2")
        self.assertEqual(out["branch"], f"repair/{SHA}-2")

    def test_unreadable_records_fail_closed(self):
        # A records-API blip must NOT dispatch blind (it could double-run a
        # repair); the next failure event retries with fresh records.
        res = self._run("FETCH-FAILED", "[]")
        self.assertEqual(res.returncode, 0, res.stderr)
        out = self._outputs(res.stdout)
        self.assertEqual(out["go"], "false")
        self.assertEqual(out["reason"], "records-unreadable")

    def test_workflow_name_reaches_the_classifier(self):
        # The name is what scopes the sandbox-block marker, so it has to
        # survive the CLI boundary the workflow actually crosses.
        res = self._run(
            "[]", "[]",
            log=SANDBOX_BLOCKED_LOG,
            workflow_name=HARNESS_WORKFLOW,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = self._outputs(res.stdout)
        self.assertEqual(out["go"], "false")
        self.assertEqual(out["reason"], "infra-backoff")

    def test_the_workflow_name_flag_is_optional(self):
        # A caller pinned to an older reusable workflow omits the flag; that
        # must keep working, and must keep dispatching.
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "log.txt")
            refs = os.path.join(td, "refs.json")
            pulls = os.path.join(td, "pulls.json")
            open(log, "w").write(STALE_ASSERTION_LOG)
            open(refs, "w").write("[]")
            open(pulls, "w").write("[]")
            res = subprocess.run(
                [
                    sys.executable,
                    os.path.join(SCRIPTS, "red_main_repair.py"),
                    "decide",
                    "--conclusion", "failure",
                    "--head-branch", "main",
                    "--default-branch", "main",
                    "--head-sha", SHA,
                    "--log-file", log,
                    "--refs-file", refs,
                    "--pulls-file", pulls,
                ],
                capture_output=True,
                text=True,
            )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self._outputs(res.stdout)["reason"], "dispatch")


if __name__ == "__main__":
    unittest.main()
