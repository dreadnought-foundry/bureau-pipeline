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
    becomes a branch name.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

import red_main_repair  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
