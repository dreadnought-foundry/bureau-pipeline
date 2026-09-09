"""Scenario: the SHIPPED merge-gate step, executed, against a draft PR
(DRE-3467).

The unit suite (tests/test_merge_gate_draft.py) proves the decision. This
proves the seam the incident actually crossed: merge-gate.yml's own
`Evaluate and merge` body, run for real by bash against a stub `gh` that
answers with GitHub's records — including `isDraft: true` — and that FAILS
the merge command exactly as GitHub did on PR #323:

    Pull Request is still a draft (mergePullRequest)   → exit 1

So the assertion is not "the script returned a string". It is that the step
never reaches the merge command at all, exits 0, and leaves one honest note
on the pull request. Stubbing the live tool to fail is what makes this
different from a unit-green claim: locally `gh` is authed and would have
given a false green (`standards/engineering.md`, test rigor).

Anti-vacuity is executable here rather than asserted: the last test strips
the draft flag out of the shipped body — the pre-DRE-3467 step, byte for
byte otherwise — and proves THAT one calls the merge command and dies with
the incident's own error. If the fix regresses, that test's twin above goes
red for the same reason the gate went red twice on 2026-09-08.
"""

import json
import os
import re
import subprocess  # nosec B404 — fixed argv, our own workflow body
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

PR = 323
HEAD = "09b52e93f4073fa97e7cd47391a45be4218e36ce"
BRANCH = "agent/DRE-3389-verdict-evidence"
REPO = "dreadnought-foundry/bureau-pipeline"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"

# GitHub's own refusal, verbatim from the failed run the card reports.
DRAFT_REFUSAL = "GraphQL: Pull Request is still a draft (mergePullRequest)"

HUMAN_MARK = "Merge gate: waiting for human merge"

# A stub `gh` that answers from a fixture file, records every invocation,
# and keeps the PR's comments in a file so gate_note.py's post-then-converge
# pass sees its own write. `pr merge` fails the way GitHub failed.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def comments():
    return json.load(open(os.environ["COMMENTS"]))


def write_comments(rows):
    with open(os.environ["COMMENTS"], "w") as fh:
        json.dump(rows, fh)


if args[:2] == ["pr", "view"]:
    field = (opt("--jq") or "").lstrip(".")
    value = fx["pr"][field]
    emit("true" if value is True else "false" if value is False else str(value))

if args[:2] == ["pr", "merge"]:
    sys.stderr.write(os.environ["MERGE_ERROR"] + "\n")
    raise SystemExit(1)

if args[0] == "api":
    method = opt("--method") or "GET"
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if method == "POST":
        rows = comments()
        body = json.loads(sys.stdin.read())["body"]
        row = {"id": 900 + len(rows), "user": {"login": os.environ["QA_LOGIN"]},
               "body": body}
        rows.append(row)
        write_comments(rows)
        emit(row)
    if method == "DELETE":
        cid = int(path.rsplit("/", 1)[1])
        write_comments([c for c in comments() if c["id"] != cid])
        emit({})
    if "check-runs" in path:
        emit({"check_runs": fx["check_runs"]})
    if "/compare/" in path:
        emit(fx["compare"])
    if "actions/runs" in path:
        emit({"workflow_runs": []})
    if "/commits" in path:
        emit(fx["pr_commits"])
    if "/comments" in path:
        emit([] if "page=2" in path or "page=3" in path else comments())
    if "/pulls/" in path:
        emit(fx["author"] if opt("--jq") else {})
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def evaluate_body() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["evaluate"]["steps"]
    runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
    return runs[0]


def substitute(run: str) -> str:
    """Apply the `${{ }}` substitutions Actions would make; an expression
    with no value here is a hole in the harness, not something to skip."""
    values = {
        "github.repository": REPO,
        "github.token": "workflow-token",
        "steps.qa.outputs.app-slug": "agent-bureau-qa-bot",
    }

    def repl(m):
        key = m.group(1).strip()
        assert key in values, f"harness has no value for ${{{{ {key} }}}}"
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


class ShippedStepResult:
    def __init__(self, proc, calls, comments):
        self.proc = proc
        self.calls = calls
        self.comments = comments

    @property
    def merge_attempts(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def notes(self):
        return [c["body"] for c in self.comments if HUMAN_MARK in c["body"]]


def run_shipped_step(is_draft: bool, body=None) -> ShippedStepResult:
    """Execute merge-gate.yml's `Evaluate and merge` body for real."""
    fixture = {
        "pr": {
            "headRefName": BRANCH,
            "state": "OPEN",
            "mergeStateStatus": "DRAFT" if is_draft else "CLEAN",
            "isDraft": is_draft,
            "headRefOid": HEAD,
            "baseRefName": "main",
        },
        "check_runs": [
            {"name": "scripts unit tests", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}},
        ],
        "compare": {"status": "ahead", "files": [{"filename": "a.py"}]},
        "pr_commits": [{"sha": HEAD}],
        "author": WORKER_LOGIN,
    }
    seed_comments = [{
        "id": 1,
        "user": {"login": QA_LOGIN},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
    }]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        os.symlink(ROOT, td / ".bureau-pipeline")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps(seed_comments))
        (td / "gh.log").write_text("")
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-c", substitute(body if body is not None else evaluate_body())],
            cwd=td, capture_output=True, text=True,
            env={
                **os.environ,
                "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                "PR": str(PR),
                "GH_TOKEN": "qa-token",
                "LINEAR_API_KEY": "test-key",
                "FIXTURE": str(td / "fixture.json"),
                "COMMENTS": str(td / "comments.json"),
                "GH_LOG": str(td / "gh.log"),
                "QA_LOGIN": QA_LOGIN,
                "MERGE_ERROR": DRAFT_REFUSAL,
            },
        )
        calls = [
            json.loads(ln)
            for ln in (td / "gh.log").read_text().splitlines() if ln
        ]
        comments = json.loads((td / "comments.json").read_text())
    return ShippedStepResult(proc, calls, comments)


class DraftPrIsNeverHandedToTheMergeCommandTest(unittest.TestCase):
    """The incident, replayed through the shipped step."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_shipped_step(is_draft=True)

    def test_the_step_never_calls_the_merge_command(self):
        self.assertEqual(
            self.result.merge_attempts, [],
            "the gate still tries to merge a draft — GitHub answers "
            f"{DRAFT_REFUSAL!r}\n{self.result.proc.stdout}",
        )

    def test_the_step_exits_clean_instead_of_going_red(self):
        self.assertEqual(
            self.result.proc.returncode, 0,
            f"stdout:\n{self.result.proc.stdout}\n"
            f"stderr:\n{self.result.proc.stderr}",
        )

    def test_the_decision_is_human_and_says_why(self):
        self.assertIn("decision=human", self.result.proc.stdout)
        self.assertIn("draft", self.result.proc.stdout)

    def test_exactly_one_honest_note_lands_on_the_pull_request(self):
        self.assertEqual(len(self.result.notes), 1, self.result.comments)
        note = self.result.notes[0]
        self.assertIn("draft", note)
        self.assertIn("ready for review", note)

    def test_the_note_carries_no_verdict_shaped_text(self):
        """A gate note is a status, never an approval credential — and it
        must not re-wake the gate's own issue_comment leg, which triggers on
        a body containing the critic's marker (standards/untrusted-content)."""
        note = self.result.notes[0]
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, note)

    def test_a_second_wake_posts_nothing_further(self):
        """The gate wakes on every CI completion, every verdict and
        reconcile's ~15-minute nudge; the note is posted once."""
        again = run_shipped_step(is_draft=True)
        self.assertEqual(len(again.notes), 1)

    def test_the_draft_state_was_read_from_github(self):
        reads = [c for c in self.result.calls if c[:2] == ["pr", "view"]]
        self.assertTrue(
            any("isDraft" in " ".join(c) for c in reads),
            f"the step never asked GitHub whether the PR is a draft: {reads}",
        )


class ReadyPrStillMergesTest(unittest.TestCase):
    """Anti-vacuity, direction one: the same step, the same inputs, the
    draft flag off — the gate must still merge, or the test above would pass
    on a gate that merges nothing."""

    def test_a_ready_pr_reaches_the_merge_command(self):
        result = run_shipped_step(is_draft=False)
        self.assertEqual(
            len(result.merge_attempts), 1,
            f"stdout:\n{result.proc.stdout}\nstderr:\n{result.proc.stderr}",
        )
        self.assertIn("--match-head-commit", " ".join(result.merge_attempts[0]))
        self.assertEqual(result.notes, [], "a ready PR must get no note")


class WithoutTheFixTheIncidentReproducesTest(unittest.TestCase):
    """Anti-vacuity, direction two: the pre-DRE-3467 step — the shipped body
    with the draft flag removed and nothing else changed — merges a draft,
    GitHub refuses, and the run goes red. That is bureau-pipeline's 2026-09-08
    gate failure, reproduced from the code that is in the repository today."""

    def test_the_pre_fix_step_calls_merge_and_dies_on_githubs_refusal(self):
        body = evaluate_body()
        pre_fix = re.sub(r"^[ \t]*--is-draft .*\n", "", body, flags=re.M)
        self.assertNotEqual(pre_fix, body, "the flag is spelled differently now")
        self.assertNotIn("--is-draft", pre_fix)
        result = run_shipped_step(is_draft=True, body=pre_fix)
        self.assertEqual(len(result.merge_attempts), 1)
        self.assertEqual(result.proc.returncode, 1)
        self.assertIn("real failure", result.proc.stdout)


if __name__ == "__main__":
    unittest.main()
