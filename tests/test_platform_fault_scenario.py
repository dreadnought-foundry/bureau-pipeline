"""Scenario: the DRE-2911 run trio, driven by EXECUTING the result gate.

Unit-green is not live-working, and DRE-2931 crosses three systems: a shell
branch in `agent-task.yml`, the classifier and the budget in `scripts/`, and
the Linear writes at the end of it. `tests/test_platform_fault_not_a_model_death.py`
proves the decisions and pins the shell at the source; neither of them RUNS the
step. So this does — the real `Report result to Linear` block, under the
runner's own shell flags, with `linear_ops.py`, `card_pr.py` and `git` stubbed
and the REAL `check_agent_result.py`, `dead_run.py` and `medic_classify.py` in
the checkout, exactly as an agent run has them.

What it catches that a source-level pin cannot: the branch order being wrong so
a pre-agent fault falls through to the death path anyway, `$RATE_FLAGS`
word-splitting into the wrong argument, `--failed-step` losing its spaces, a
park that writes the label and then the state independently, or a receipt that
posts while the writes it describes did not happen.

The three runs it drives are DRE-2911's own, from the receipts:

  1. run 33468806067 — 151 turns against a 150 cap, $18.37, is_error. Real.
  2. and 3. ~20 seconds, $0, no execution result: died at `Card → In Progress`
     on Linear's HTTP 400 while the workspace key was over its 2,500/hour
     quota. Neither reached a model.

Follows the harness in tests/test_act_emission_scenario.py.

Run: python3 -m pytest tests/test_platform_fault_scenario.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-task.yml")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
os.environ.setdefault("GH_TOKEN", "x")

import dead_run  # noqa: E402

CARD = "DRE-2911"
REPO = "dreadnought-foundry/agent-bureau"
RUN_URL = "https://github.com/dreadnought-foundry/agent-bureau/actions/runs/33468806067"

# Attempt 1's execution record: the one death the card actually earned.
TURN_CAP_DEATH = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "duration_ms": 1_200_000,
    "num_turns": 151,
    "total_cost_usd": 18.37,
    "terminal_reason": "max_turns",
    "result": "Reached maximum number of turns (150)",
}

# What `Card → In Progress` printed on attempts 2 and 3, composed by the real
# client (linear_ops._api_error) once DRE-2923 taught it to read the body.
RATELIMIT_LOG = (
    "Traceback (most recent call last):\n"
    "linear_ops.LinearRateLimited: Linear API returned 400 from "
    "https://api.linear.app/graphql: rate limited: 2500 requests/hour "
    'exhausted — body: \'{"errors":[{"extensions":{"code":"RATELIMITED"}}]}\'\n'
)


def report_step() -> dict:
    for step in yaml.safe_load(open(WORKFLOW))["jobs"]["execute"]["steps"]:
        if step.get("name") == "Report result to Linear":
            return step
    raise AssertionError("the Report step is gone from agent-task.yml")


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ }}` substitutions Actions would make, and prove none
    survive — an unsubstituted expression is a hole in the harness, not a pass."""
    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"harness has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def _executable(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


# A `linear_ops` that RECORDS instead of writing, importable as a module (that
# is how dead_run._cmd_park reaches it) and runnable as the CLI (that is how the
# shell reaches it). LINEAR_STUB_LOG names the journal; LINEAR_STUB_FAIL names
# the one call that raises, which is how the failing-state-write park is driven.
LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys


class LinearError(RuntimeError):
    pass


def _log(op, *args):
    with open(os.environ["LINEAR_STUB_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"op": op, "args": list(args)}) + "\\n")
    if op in (os.environ.get("LINEAR_STUB_FAIL") or "").split(","):
        raise LinearError(f"stub refuses {op}")


def get_issue(identifier):
    _log("get_issue", identifier)
    labels = [{"name": n} for n in
              (os.environ.get("LINEAR_STUB_LABELS") or "").split(",") if n]
    return {"identifier": identifier, "state": {"name": "In Progress"},
            "labels": {"nodes": labels}}


def _label_names(issue):
    return [(l.get("name") or "") for l in
            ((issue.get("labels") or {}).get("nodes") or [])]


def add_label(identifier, name):
    _log("add_label", identifier, name)


def remove_label(identifier, name):
    _log("remove_label", identifier, name)


def cmd_state(identifier, name, *flags):
    _log("state", identifier, name, *flags)


def cmd_comment(identifier, body, *flags):
    _log("comment", identifier, body)


def main(argv):
    if not argv:
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "comment":
            cmd_comment(rest[0], rest[1])
        elif cmd == "state":
            cmd_state(rest[0], rest[1], *rest[2:])
        elif cmd == "advance":
            _log("advance", *rest)
        elif cmd == "add-label":
            add_label(rest[0], rest[1])
        elif cmd == "count-comments":
            _log("count-comments", *rest)
            print(os.environ.get("LINEAR_STUB_PRIOR", "0"))
        else:
            _log(cmd, *rest)
    except LinearError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

# `card_pr.py find` with no PR: exit 0, empty state and url. Exit 3 would mean
# UNREADABLE, which is a different branch entirely (DRE-2034).
CARD_PR_STUB = '''#!/usr/bin/env python3
import sys
print("\\t")
sys.exit(0)
'''


def _checkout(td: str) -> str:
    """A `.bureau-pipeline` checkout: the real classifier, the real budget and
    the real rate-limit fingerprint, with only the Linear seam and the PR
    lookup replaced."""
    base = os.path.join(td, ".bureau-pipeline")
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(base, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
    _executable(os.path.join(base, "scripts", "linear_ops.py"), LINEAR_STUB)
    _executable(os.path.join(base, "scripts", "card_pr.py"), CARD_PR_STUB)
    return base


def _git_stub(td: str) -> str:
    """A `git` that reports no agent branch for this card."""
    binary = os.path.join(td, "bin")
    os.makedirs(binary, exist_ok=True)
    _executable(os.path.join(binary, "git"), "#!/bin/sh\nexit 0\n")
    return binary


def run_report(
    td: str,
    *,
    execution=None,
    claude_outcome="failure",
    inprogress_outcome="success",
    pre_agent_log="",
    prior="0",
    labels="",
    fail="",
):
    """Execute the real Report block. Returns (proc, journal)."""
    _checkout(td)
    binary = _git_stub(td)
    log = os.path.join(td, "linear.jsonl")
    exec_file = os.path.join(td, "claude-execution-output.json")
    if execution is not None:
        with open(exec_file, "w", encoding="utf-8") as fh:
            json.dump(execution, fh)
    pre_log = os.path.join(td, "preagent.log")
    if pre_agent_log:
        with open(pre_log, "w", encoding="utf-8") as fh:
            fh.write(pre_agent_log)

    # The three stop-before-a-PR notes the step checks for. None of them
    # exists in any of these scenarios, and a leftover from another test would
    # silently take the branch — so they are cleared, not assumed absent.
    for path in ("/tmp/agent-handback.txt", "/tmp/agent-escalation.txt",
                 "/tmp/agent-blocker.txt"):
        if os.path.exists(path):
            os.remove(path)

    run = substitute(report_step()["run"], {
        "github.server_url": "https://github.com",
        "github.repository": REPO,
        "github.run_id": "33468806067",
        "steps.claude.outputs.execution_file": exec_file,
    })
    script = os.path.join(td, "report.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("set -eo pipefail\n" + run)
    proc = subprocess.run(
        ["bash", script],
        cwd=td,
        env=dict(
            os.environ,
            PATH=binary + os.pathsep + os.environ["PATH"],
            CARD=CARD,
            MODEL_USED="claude-opus-5",
            CLAUDE_OUTCOME=claude_outcome,
            DEDUPE_OUTCOME="success",
            MODEL_OUTCOME="success",
            CTX_OUTCOME="success",
            SANITIZE_OUTCOME="success",
            INPROGRESS_OUTCOME=inprogress_outcome,
            PRE_AGENT_LOG=pre_log,
            GH_TOKEN="test",
            LINEAR_API_KEY="test-key",
            LINEAR_STUB_LOG=log,
            LINEAR_STUB_PRIOR=prior,
            LINEAR_STUB_LABELS=labels,
            LINEAR_STUB_FAIL=fail,
        ),
        capture_output=True, text=True,
    )
    journal = [
        json.loads(line)
        for line in (open(log, encoding="utf-8").read().splitlines()
                     if os.path.exists(log) else [])
    ]
    return proc, journal


def ops(journal, op):
    return [entry for entry in journal if entry["op"] == op]


def comments(journal):
    return [entry["args"][1] for entry in ops(journal, "comment")]


class PlatformFaultScenario(unittest.TestCase):
    """Attempts 2 and 3: the quota was exhausted and no model was called."""

    def report(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            return run_report(
                td,
                execution=None,
                claude_outcome="skipped",
                inprogress_outcome="failure",
                pre_agent_log=RATELIMIT_LOG,
                **kw,
            )

    def test_the_step_succeeds_and_posts_exactly_one_receipt(self):
        proc, journal = self.report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(comments(journal)), 1, comments(journal))

    def test_the_receipt_spends_neither_budget(self):
        _, journal = self.report()
        body = comments(journal)[0]
        self.assertNotIn(dead_run.DEAD_TAG, body)
        self.assertNotIn(dead_run.TURN_TAG, body)

    def test_the_receipt_names_no_model_as_having_failed(self):
        _, journal = self.report()
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, comments(journal)[0])
        self.assertNotIn("claude-opus-5", comments(journal)[0])

    def test_it_is_reported_as_a_quota_wait_naming_the_step(self):
        _, journal = self.report()
        body = comments(journal)[0]
        self.assertIn("quota", body.lower())
        # `--failed-step "Card → In Progress"` survives the shell intact.
        self.assertIn("Card → In Progress", body)

    def test_the_card_is_neither_moved_nor_labelled(self):
        _, journal = self.report()
        self.assertEqual(ops(journal, "state"), [])
        self.assertEqual(ops(journal, "advance"), [])
        self.assertEqual(ops(journal, "add_label"), [])

    def test_linear_is_not_asked_for_a_count_it_cannot_answer(self):
        # Under the exhaustion that caused this, count-comments fails too —
        # and the answer could not change the decision anyway.
        _, journal = self.report()
        self.assertEqual(ops(journal, "count-comments"), [])

    def test_the_run_carries_the_warning_the_card_does_not(self):
        proc, _ = self.report()
        self.assertIn("::warning", proc.stdout)
        self.assertIn("NOT charged to DRE-2911", proc.stdout)

    def test_a_card_at_the_cap_is_still_not_parked(self):
        # DRE-2911 was at 2 prior deaths when the platform faults landed. That
        # is exactly when the old code parked it.
        _, journal = self.report(prior=str(dead_run.REQUEUE_CAP))
        self.assertEqual(ops(journal, "add_label"), [])
        self.assertEqual(ops(journal, "state"), [])

    def test_a_plain_pre_agent_failure_reads_differently(self):
        with tempfile.TemporaryDirectory() as td:
            _, journal = run_report(
                td, execution=None, claude_outcome="skipped",
                inprogress_outcome="failure",
                pre_agent_log="linear_ops.LinearError: 500 from api.linear.app",
            )
        body = comments(journal)[0]
        self.assertNotIn("quota", body.lower())
        self.assertIn("Card → In Progress", body)
        self.assertNotIn(dead_run.DEAD_TAG, body)


class TurnCapDeathScenario(unittest.TestCase):
    """Attempt 1: it ran, it counted, and the receipt says what it spent."""

    def report(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            return run_report(td, execution=TURN_CAP_DEATH, **kw)

    def test_the_receipt_carries_the_turns_and_the_dollars(self):
        proc, journal = self.report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = comments(journal)[0]
        self.assertIn("151 turns", body)
        self.assertIn("$18.37", body)
        self.assertIn("150-turn cap", body)

    def test_it_is_not_described_as_hung_or_lost_or_a_model_error(self):
        _, journal = self.report()
        body = comments(journal)[0]
        self.assertIn("ran out of steps", body)
        self.assertNotIn("hung", body)
        self.assertNotIn("API/model error", body)
        self.assertNotIn(dead_run.ERROR_MARKER_PREFIX, body)

    def test_it_spends_its_own_budget_and_requeues(self):
        _, journal = self.report()
        self.assertIn(dead_run.TURN_TAG, comments(journal)[0])
        self.assertEqual(
            [e["args"][1] for e in ops(journal, "state")], ["Todo"]
        )

    def test_it_reads_the_turn_budget_not_the_death_budget(self):
        _, journal = self.report()
        counted = ops(journal, "count-comments")
        self.assertEqual(len(counted), 1)
        self.assertIn(dead_run.TURN_TAG, counted[0]["args"])


class UnlandedParkScenario(unittest.TestCase):
    """The hold at the cap, with Linear refusing the state write."""

    def report(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            return run_report(
                td,
                execution={"subtype": "success", "is_error": True,
                           "num_turns": 30, "total_cost_usd": 2.5},
                prior=str(dead_run.REQUEUE_CAP),
                **kw,
            )

    def test_a_clean_park_writes_both(self):
        _, journal = self.report()
        self.assertEqual(
            [e["args"][1] for e in ops(journal, "add_label")],
            [dead_run.HOLD_LABEL],
        )
        self.assertEqual(
            [e["args"][1] for e in ops(journal, "state")], [dead_run.PARK_STATE]
        )
        self.assertIn(dead_run.DEAD_TAG, comments(journal)[0])

    def test_a_refused_state_write_leaves_no_label_behind(self):
        # DRE-2911's park, exactly: the label landed and the state did not.
        # It must not survive this run.
        proc, journal = self.report(fail="state")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            [e["args"][1] for e in ops(journal, "remove_label")],
            [dead_run.HOLD_LABEL],
        )

    def test_a_refused_state_write_is_retried_before_giving_up(self):
        _, journal = self.report(fail="state")
        self.assertEqual(len(ops(journal, "state")), dead_run.PARK_ATTEMPTS)

    def test_the_receipt_does_not_claim_a_park_that_did_not_happen(self):
        _, journal = self.report(fail="state")
        body = comments(journal)[0]
        self.assertIn("did NOT land", body)
        self.assertIn(dead_run.DEAD_TAG, body)
        self.assertNotIn("parked in Backlog", body)

    def test_the_run_says_so_where_run_faults_belong(self):
        proc, _ = self.report(fail="state")
        self.assertIn("::error", proc.stdout)

    def test_a_label_the_card_already_had_is_left_alone(self):
        # AC 7 through the real step: a card parked earlier keeps its hold.
        _, journal = self.report(fail="state", labels=dead_run.HOLD_LABEL)
        self.assertEqual(ops(journal, "remove_label"), [])
        self.assertEqual(ops(journal, "add_label"), [])


class UnlandedTurnExhaustionParkScenario(unittest.TestCase):
    """The OTHER hold decide() can reach, with Linear refusing the state write.

    `decide()` produces "hold" from two independent caps, and the workflow's
    park step is uniform for either. When the park does not land, the whole
    receipt is replaced — so a receipt hardcoded to the dead-run tag loses the
    turn strike that was actually spent and bills the dead-run budget for a
    death that never happened. The overlap is exactly the one DRE-2931 is
    written around: a real cap reached while Linear is refusing writes.
    """

    def report(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            return run_report(
                td,
                execution=TURN_CAP_DEATH,
                prior=str(dead_run.TURN_REQUEUE_CAP),
                fail="state",
                **kw,
            )

    def test_the_park_is_attempted_and_does_not_land(self):
        proc, journal = self.report()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            [e["args"][1] for e in ops(journal, "remove_label")],
            [dead_run.HOLD_LABEL],
        )
        self.assertIn("did NOT land", comments(journal)[0])

    def test_the_receipt_spends_the_turn_budget_not_the_dead_run_budget(self):
        _, journal = self.report()
        body = comments(journal)[0]
        self.assertIn(dead_run.TURN_TAG, body)
        self.assertNotIn(dead_run.DEAD_TAG, body)

    def test_it_still_read_the_turn_budget_to_get_here(self):
        # Guards the fixture itself: if this drove the dead-run cap instead,
        # the assertion above would pass for the wrong reason.
        _, journal = self.report()
        counted = ops(journal, "count-comments")
        self.assertEqual(len(counted), 1)
        self.assertIn(dead_run.TURN_TAG, counted[0]["args"])


if __name__ == "__main__":
    unittest.main()
