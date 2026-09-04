"""Scenario: DRE-3088's three deaths, driven by EXECUTING agent-task.yml.

Unit-green is not live-working, and DRE-3097 crosses three systems: two shell
blocks in `agent-task.yml`, `scripts/turn_budget.py` and `scripts/dead_run.py`,
and the Linear reads and writes at either end. `tests/test_turn_budget.py`
proves the decisions and pins the shell at the source; neither of them RUNS the
steps. So this does — the real `Select model` and `Report result to Linear`
blocks, under the runner's own shell flags, with `linear_ops.py`, `card_pr.py`
and `git` stubbed and the REAL `turn_budget.py`, `dead_run.py` and
`check_agent_result.py` in the checkout, exactly as an agent run has them.

What it catches that a source-level pin cannot: `$TURNS` never reaching
`$GITHUB_OUTPUT`, the selector's note corrupting the captured number, the
comments dump failing and taking the whole step with it under `set -e`, or the
diagnosis reaching `dead_run.py` as a word-split flag that lands on the wrong
argument.

The thread it drives is DRE-3088's own, from the receipts: three runs on
2026-09-04, $19.12 / $19.12 / $17.53, each reaching `⏳ 3/5 implementation
green` and dying in the two steps after it — the third on the XS version of the
card.

Follows the harness in tests/test_platform_fault_scenario.py.

Run: python3 -m pytest tests/test_turn_budget_scenario.py -v
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

import dedupe_dispatch  # noqa: E402
import turn_budget  # noqa: E402

CARD = "DRE-3088"
REPO = "dreadnought-foundry/bureau-pipeline"
RUN_ID = "33999123456"

#: The third death's own execution record: 151 turns against the 150 cap,
#: $17.53, on the card AFTER it had been split to XS.
TURN_CAP_DEATH = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "duration_ms": 1_500_000,
    "num_turns": 151,
    "total_cost_usd": 17.53,
    "terminal_reason": "max_turns",
    "result": "Reached maximum number of turns (150)",
}


def _thread(*runs: int, turns: int = 150) -> list[str]:
    """A card thread: one run per argument, reaching `⏳ n/5` and dying."""
    bodies: list[str] = []
    for i, reached in enumerate(runs, start=1):
        bodies.append(
            f"🧠 model-attempt: claude-opus-5 — engineer agent starting "
            f"(turns={turns}). preferred. "
            f"Run: https://github.com/{REPO}/actions/runs/{i}"
        )
        for n in range(1, reached + 1):
            bodies.append(f"⏳ {n}/5 phase {n}")
        bodies.append(f"🪦 turn-exhaustion-requeue: the agent ran out of steps.")
    return bodies


def step(ref: str) -> dict:
    for entry in yaml.safe_load(open(WORKFLOW))["jobs"]["execute"]["steps"]:
        if entry.get("id") == ref or entry.get("name") == ref:
            return entry
    raise AssertionError(f"{ref!r} is gone from agent-task.yml")


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ }}` substitutions Actions would make, and prove none
    survive — an unsubstituted expression is a hole in the harness."""
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


# A `linear_ops` that RECORDS instead of writing, importable as a module (how
# dead_run._cmd_park and turn_budget._labels_of reach it) and runnable as the
# CLI (how the shell reaches it). LINEAR_STUB_THREAD names a JSON file holding
# the card's comment bodies, which is what `dump-comments` prints.
LINEAR_STUB = '''#!/usr/bin/env python3
import json, os, sys


class LinearError(RuntimeError):
    pass


def _log(op, *args):
    with open(os.environ["LINEAR_STUB_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"op": op, "args": list(args)}) + "\\n")
    if op in (os.environ.get("LINEAR_STUB_FAIL") or "").split(","):
        raise LinearError(f"stub refuses {op}")


def _thread():
    path = os.environ.get("LINEAR_STUB_THREAD") or ""
    if not path or not os.path.exists(path):
        return []
    return json.load(open(path, encoding="utf-8"))


def get_issue(identifier):
    _log("get_issue", identifier)
    labels = [{"name": n} for n in
              (os.environ.get("LINEAR_STUB_LABELS") or "").split(",") if n]
    return {"identifier": identifier, "state": {"name": "In Progress"},
            "labels": {"nodes": labels}}


def _label_names(issue):
    return [(l.get("name") or "") for l in
            ((issue.get("labels") or {}).get("nodes") or [])]


def comment_bodies(identifier):
    _log("dump-comments", identifier)
    return _thread()


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
        elif cmd == "dump-comments":
            print(json.dumps(comment_bodies(rest[0])))
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

CARD_PR_STUB = '''#!/usr/bin/env python3
import sys
print("\\t")
sys.exit(0)
'''

# `model_fallback.py select` without the network probe: the Select model block
# runs the REAL turn_budget.py, and the model half is not what this file
# proves. It must still write its --explain-file, or the block's `cat` fails.
MODEL_STUB = '''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if "--explain-file" in args:
    open(args[args.index("--explain-file") + 1], "w").write("preferred.\\n")
print("claude-opus-5")
'''


def _checkout(td: str) -> str:
    base = os.path.join(td, ".bureau-pipeline")
    if os.path.exists(base):
        return base
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(base, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "config"), os.path.join(base, "config"))
    _executable(os.path.join(base, "scripts", "linear_ops.py"), LINEAR_STUB)
    _executable(os.path.join(base, "scripts", "card_pr.py"), CARD_PR_STUB)
    return base


def _git_stub(td: str) -> str:
    binary = os.path.join(td, "bin")
    os.makedirs(binary, exist_ok=True)
    _executable(os.path.join(binary, "git"), "#!/bin/sh\nexit 0\n")
    return binary


def _write_thread(td: str, bodies: list[str]) -> str:
    path = os.path.join(td, "thread.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bodies, fh)
    return path


def _bash(td: str, name: str, body: str, env: dict):
    script = os.path.join(td, name)
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("set -eo pipefail\n" + body)
    return subprocess.run(["bash", script], cwd=td, env=env,
                          capture_output=True, text=True)


def _journal(log: str) -> list[dict]:
    if not os.path.exists(log):
        return []
    return [json.loads(line) for line in
            open(log, encoding="utf-8").read().splitlines()]


def comments(journal) -> list[str]:
    return [e["args"][1] for e in journal if e["op"] == "comment"]


# --------------------------------------------------------------------------- #
# the SELECT half: a `turns:250` card really does get 250 turns                 #
# --------------------------------------------------------------------------- #

class SelectModelScenario(unittest.TestCase):
    """Drive the real `Select model` block. Its output is what
    `--max-turns ${{ steps.model.outputs.turns }}` interpolates."""

    def _run(self, labels: str):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        base = _checkout(td)
        _executable(os.path.join(base, "scripts", "model_fallback.py"), MODEL_STUB)
        outputs = os.path.join(td, "github_output")
        open(outputs, "w").close()
        summary = os.path.join(td, "step_summary")
        open(summary, "w").close()
        body = substitute(step("model")["run"], {
            "steps.gate.outputs.role": "engineer",
            "github.event.client_payload.identifier": CARD,
        })
        proc = _bash(td, "select.sh", body, dict(
            os.environ,
            PATH=_git_stub(td) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=td,
            GITHUB_OUTPUT=outputs,
            GITHUB_STEP_SUMMARY=summary,
            LINEAR_API_KEY="test-key",
            LINEAR_STUB_LOG=os.path.join(td, "linear.jsonl"),
            LINEAR_STUB_LABELS=labels,
        ))
        self.assertEqual(0, proc.returncode, proc.stderr)
        written = dict(
            line.split("=", 1)
            for line in open(outputs, encoding="utf-8").read().splitlines()
            if "=" in line
        )
        return written, proc

    def test_a_card_labelled_turns_250_runs_with_250(self):
        """The acceptance criterion, as far as a test can carry it: the
        number the workflow interpolates into --max-turns is 250."""
        written, _ = self._run("repo:bureau-pipeline,agent:engineer,turns:250")
        self.assertEqual("250", written["turns"])

    def test_a_card_with_neither_label_still_runs_with_150(self):
        written, _ = self._run("repo:bureau-pipeline,agent:engineer")
        self.assertEqual("150", written["turns"])

    def test_the_size_label_alone_picks_the_rung(self):
        written, _ = self._run("repo:bureau-pipeline,size:M")
        self.assertEqual("250", written["turns"])

    def test_the_selection_note_never_corrupts_the_captured_number(self):
        """`TURNS=$(… select …)` captures stdout. The note has to stay off
        it — the same discipline model_fallback.py keeps for the model id."""
        written, _ = self._run("turns:400")
        self.assertEqual("400", written["turns"])
        self.assertIn("turns:400", written["turns_why"])
        self.assertNotIn("\n", written["turns_why"])

    def test_the_receipt_the_next_run_reads_carries_the_budget(self):
        """The `🧠 model-attempt` line is assembled from these outputs. Render
        it and read it back with the two readers that share it."""
        written, _ = self._run("turns:250")
        receipt = substitute(
            "🧠 model-attempt: ${{ steps.model.outputs.model }} — engineer "
            "agent starting (turns=${{ steps.model.outputs.turns }}). "
            "${{ steps.model.outputs.why }} ${{ steps.model.outputs.turns_why }} "
            "Run: https://github.com/x/y/actions/runs/" + RUN_ID,
            {
                "steps.model.outputs.model": written["model"],
                "steps.model.outputs.turns": written["turns"],
                "steps.model.outputs.why": written["why"],
                "steps.model.outputs.turns_why": written["turns_why"],
            },
        )
        self.assertIn("turns=250", receipt)
        self.assertEqual(250, turn_budget.current_budget([receipt]))
        self.assertEqual(RUN_ID, dedupe_dispatch.heartbeat_run_id([receipt]))

    def test_an_unreadable_card_costs_the_raise_and_not_the_run(self):
        """Linear refuses the label read. The step must still exit 0 with the
        default — a budget lookup that can fail a build is worse than no
        budget lookup."""
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        base = _checkout(td)
        _executable(os.path.join(base, "scripts", "model_fallback.py"), MODEL_STUB)
        outputs = os.path.join(td, "github_output")
        open(outputs, "w").close()
        summary = os.path.join(td, "step_summary")
        open(summary, "w").close()
        body = substitute(step("model")["run"], {
            "steps.gate.outputs.role": "engineer",
            "github.event.client_payload.identifier": CARD,
        })
        proc = _bash(td, "select.sh", body, dict(
            os.environ,
            PATH=_git_stub(td) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=td,
            GITHUB_OUTPUT=outputs,
            GITHUB_STEP_SUMMARY=summary,
            LINEAR_API_KEY="test-key",
            LINEAR_STUB_LOG=os.path.join(td, "linear.jsonl"),
            LINEAR_STUB_FAIL="get_issue",
        ))
        self.assertEqual(0, proc.returncode, proc.stderr)
        written = dict(
            line.split("=", 1)
            for line in open(outputs, encoding="utf-8").read().splitlines()
            if "=" in line
        )
        self.assertEqual("150", written["turns"])


# --------------------------------------------------------------------------- #
# the REPORT half: the park receipt names the right remedy                     #
# --------------------------------------------------------------------------- #

class ParkReceiptScenario(unittest.TestCase):
    """Drive the real `Report result to Linear` block on the SECOND turn-cap
    death — the one that parks the card."""

    def _run(self, thread: list[str], prior="1"):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        _checkout(td)
        log = os.path.join(td, "linear.jsonl")
        exec_file = os.path.join(td, "claude-execution-output.json")
        with open(exec_file, "w", encoding="utf-8") as fh:
            json.dump(TURN_CAP_DEATH, fh)
        for path in ("/tmp/agent-handback.txt", "/tmp/agent-escalation.txt",
                     "/tmp/agent-blocker.txt"):
            if os.path.exists(path):
                os.remove(path)
        body = substitute(step("Report result to Linear")["run"], {
            "github.server_url": "https://github.com",
            "github.repository": REPO,
            "github.run_id": RUN_ID,
            "steps.claude.outputs.execution_file": exec_file,
            "steps.rescue.outputs.local_work": "false",
        })
        proc = _bash(td, "report.sh", body, dict(
            os.environ,
            PATH=_git_stub(td) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=td,
            CARD=CARD,
            MODEL_USED="claude-opus-5",
            CLAUDE_OUTCOME="failure",
            DEDUPE_OUTCOME="success",
            MODEL_OUTCOME="success",
            CTX_OUTCOME="success",
            SANITIZE_OUTCOME="success",
            INPROGRESS_OUTCOME="success",
            PRE_AGENT_LOG=os.path.join(td, "preagent.log"),
            GH_TOKEN="test",
            LINEAR_API_KEY="test-key",
            LINEAR_STUB_LOG=log,
            LINEAR_STUB_PRIOR=prior,
            LINEAR_STUB_THREAD=_write_thread(td, thread),
            LINEAR_STUB_LABELS="repo:bureau-pipeline,agent:engineer",
            LINEAR_STUB_FAIL="",
        ))
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        return proc, _journal(log)

    def test_dre_3088s_three_green_deaths_park_with_the_BUDGET_remedy(self):
        """The card this was written from. Splitting it had already been
        tried, and the receipt used to ask for it again."""
        _, journal = self._run(_thread(3, 3, 3))
        posted = comments(journal)
        self.assertEqual(1, len(posted), posted)
        self.assertIn("budget, not size", posted[0])
        self.assertIn("turns:250", posted[0])
        self.assertNotIn("splits it into smaller pieces", posted[0])

    def test_the_park_still_lands_both_writes(self):
        """The diagnosis changes the words, never the act: Backlog plus the
        needs-human label, atomically (DRE-2931)."""
        _, journal = self._run(_thread(3, 3, 3))
        self.assertTrue([e for e in journal
                         if e["op"] == "add_label" and e["args"][1] == "needs-human"])
        self.assertTrue([e for e in journal
                         if e["op"] == "state" and e["args"][1] == "Backlog"])

    def test_early_stalling_deaths_still_ask_for_a_SPLIT(self):
        """The other half of the fork, driven the same way — the message the
        pipeline has always sent, on the evidence that supports it."""
        _, journal = self._run(_thread(1, 1))
        posted = comments(journal)
        self.assertIn("splits it into smaller pieces", posted[0])
        self.assertNotIn("budget, not size", posted[0])

    def test_a_card_already_at_250_is_told_to_go_to_400(self):
        _, journal = self._run(_thread(3, 3, turns=250))
        self.assertIn("turns:400", comments(journal)[0])

    def test_an_unreadable_thread_does_not_fail_the_step(self):
        """The dump is a nice-to-have on a path that is already handling a
        death. If Linear refuses it, the receipt degrades to the split text
        and the park still happens."""
        _, journal = self._run(_thread(3, 3, 3))  # control: the good path
        self.assertIn("budget, not size", comments(journal)[0])

        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        _checkout(td)
        log = os.path.join(td, "linear.jsonl")
        exec_file = os.path.join(td, "claude-execution-output.json")
        with open(exec_file, "w", encoding="utf-8") as fh:
            json.dump(TURN_CAP_DEATH, fh)
        body = substitute(step("Report result to Linear")["run"], {
            "github.server_url": "https://github.com",
            "github.repository": REPO,
            "github.run_id": RUN_ID,
            "steps.claude.outputs.execution_file": exec_file,
            "steps.rescue.outputs.local_work": "false",
        })
        proc = _bash(td, "report.sh", body, dict(
            os.environ,
            PATH=_git_stub(td) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=td,
            CARD=CARD, MODEL_USED="claude-opus-5", CLAUDE_OUTCOME="failure",
            DEDUPE_OUTCOME="success", MODEL_OUTCOME="success",
            CTX_OUTCOME="success", SANITIZE_OUTCOME="success",
            INPROGRESS_OUTCOME="success",
            PRE_AGENT_LOG=os.path.join(td, "preagent.log"),
            GH_TOKEN="test", LINEAR_API_KEY="test-key",
            LINEAR_STUB_LOG=log, LINEAR_STUB_PRIOR="1",
            LINEAR_STUB_THREAD=_write_thread(td, _thread(3, 3, 3)),
            LINEAR_STUB_FAIL="dump-comments",
        ))
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        posted = comments(_journal(log))
        self.assertEqual(1, len(posted), posted)
        self.assertIn("splits it into smaller pieces", posted[0])

    def test_the_FIRST_turn_cap_death_still_only_requeues(self):
        """The diagnosis belongs to the park. One death is not a pattern, and
        this run must still go back to Todo untouched."""
        _, journal = self._run(_thread(3), prior="0")
        posted = comments(journal)
        self.assertIn("turn-exhaustion-requeue", posted[0])
        self.assertNotIn("budget, not size", posted[0])
        self.assertTrue([e for e in journal
                         if e["op"] == "state" and e["args"][1] == "Todo"])


if __name__ == "__main__":
    unittest.main()
