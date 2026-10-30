"""Stacked pull requests stay stacked (DRE-8503).

THE INCIDENT (agent-bureau, 2026-10-30). Two steps acted on a stacked pull
request — one whose base is another agent branch, not the default branch —
as if it were an ordinary one:

  1. At 4:01 PM PT the merge gate merged #4373 into
     `agent/DRE-8273-initiatives-contract-pr` and, a second later, #4372 into
     `agent/DRE-7945-composer-at-foot-pr`. #4373's work landed on a branch
     that then merged on and was deleted, so it never reached the stack and
     had to be re-filed as #4383.
  2. At 3:44 and 3:46 PM PT the fix agent committed `merge: resolve
     conflicts with main (round 1/2)` onto `agent/DRE-8281-send-to-planning-pr`,
     whose base is `agent/DRE-8277-initiatives-drafts-pr`. The diff against
     its base then carried main's own work, 251 files and 13,343 lines, the
     critic refused it as too large, and the card parked.

Both are one defect: neither step read the pull request's base before
acting. What this file pins:

  * condition B (`merge_gate.evaluate_base`): a pull request whose base is
    not the repository's default branch is a `hold` naming that base, and no
    merge is made — and the same pull request based on the default branch
    still merges (the inverse);
  * the hold's note goes up once per head, never on every gate pass — the
    SHIPPED `Evaluate and merge` step, run for real against a stub `gh`;
  * the fix agent's step 2b, and its conflict-mode instruction, name the
    pull request's own base ref, never `origin/<default-branch>`.

Run: python3 -m pytest tests/test_merge_gate_stacked_base.py -v
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess  # nosec B404 — fixed argv, our own scripts
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
MERGE_GATE_WF = ROOT / ".github" / "workflows" / "merge-gate.yml"
AGENT_FIX_WF = ROOT / ".github" / "workflows" / "agent-fix.yml"
RESOLVE_FIX = SCRIPTS / "resolve_fix_pr.sh"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import merge_gate  # noqa: E402
import step_shell  # noqa: E402
from test_hand_dispatch_no_work import GH_STUB as FIX_GH_STUB, WORKER  # noqa: E402

QA = "agent-bureau-qa-bot[bot]"
REPO = "dreadnought-foundry/agent-bureau"
PR = 4373
HEAD = "4373" * 10
NEW_HEAD = "beef" * 10
STACKED_BASE = "agent/DRE-1-x"
DEFAULT = "main"


def approved(at: str) -> dict:
    return {"id": 1, "user": {"login": QA},
            "body": f"🔎 QA Critic — VERDICT: APPROVE @{at}"}


def green_checks() -> list:
    return [{"name": "tests", "status": "completed", "conclusion": "success",
             "check_suite": {"id": 1}}]


def gate(**kw):
    """`decide()` for a pull request that is otherwise fully green and
    approved: CI green, its own critic APPROVE bound to its own head."""
    args = dict(head_sha=HEAD, qa_login=QA, check_runs=green_checks(),
                comments=[approved(HEAD)], pr_number=PR)
    args.update(kw)
    return merge_gate.decide(**args)


# --------------------------------------------------------------------------
# 1. the decision
# --------------------------------------------------------------------------
class AStackedBaseHoldsTest(unittest.TestCase):
    """Criterion 1: based on `agent/DRE-1-x`, green and approved — `hold`."""

    def setUp(self):
        self.decision = gate(base_ref=STACKED_BASE, default_branch=DEFAULT)

    def test_the_decision_is_hold(self):
        self.assertEqual(self.decision.action, "hold", self.decision.reason)

    def test_the_reason_names_the_non_default_base(self):
        self.assertIn(STACKED_BASE, self.decision.reason)
        self.assertIn(DEFAULT, self.decision.reason)

    def test_the_reason_says_it_waits_to_be_retargeted(self):
        self.assertRegex(self.decision.reason, r"(?i)retarget")

    def test_the_reason_carries_no_verdict_shaped_text(self):
        # It is posted on the pull request under the qa-bot login.
        for marker in merge_gate.VERDICT_MARKERS:
            self.assertNotIn(marker, self.decision.reason)

    def test_it_holds_ahead_of_the_draft_and_whats_new_conditions(self):
        decision = gate(base_ref=STACKED_BASE, default_branch=DEFAULT,
                        is_draft=True)
        self.assertEqual(decision.action, "hold", decision.reason)
        self.assertIn(STACKED_BASE, decision.reason)


class TheDefaultBaseStillMergesTest(unittest.TestCase):
    """Criterion 2, the inverse: the same pull request based on the default
    branch merges, so the hold above is the base and nothing else."""

    def test_based_on_the_default_branch_it_merges(self):
        decision = gate(base_ref=DEFAULT, default_branch=DEFAULT)
        self.assertEqual(decision.action, "merge", decision.reason)

    def test_a_default_branch_not_called_main_is_read_not_assumed(self):
        decision = gate(base_ref="trunk", default_branch="trunk")
        self.assertEqual(decision.action, "merge", decision.reason)
        decision = gate(base_ref="main", default_branch="trunk")
        self.assertEqual(decision.action, "hold", decision.reason)

    def test_a_caller_that_passes_no_base_decides_as_before(self):
        self.assertEqual(gate().action, "merge")


class AnUnreadableDefaultBranchHoldsTest(unittest.TestCase):
    """Fail closed: a default branch nobody could read is never "the base
    is the default branch"."""

    def test_an_empty_default_branch_holds_and_says_unknown(self):
        for unread in ("", None, "{}", "null"):
            with self.subTest(default=unread):
                decision = gate(base_ref=DEFAULT, default_branch=unread)
                self.assertEqual(decision.action, "hold", decision.reason)
                self.assertIn("UNKNOWN", decision.reason)

    def test_an_empty_base_holds_too(self):
        decision = gate(base_ref="", default_branch=DEFAULT)
        self.assertEqual(decision.action, "hold", decision.reason)
        self.assertIn("UNKNOWN", decision.reason)


class TheCommandLineCarriesTheBaseTest(unittest.TestCase):
    """The gate's own argv: --base-ref and --default-branch reach condition B."""

    def _main(self, base: str, default: str) -> str:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            files = {
                "check-runs": {"check_runs": green_checks()},
                "comments": [[approved(HEAD)]],
                "workflow-runs": {"workflow_runs": []},
                "compare": {"status": "ahead", "files": [{"filename": "a.py"}]},
            }
            for name, payload in files.items():
                (td / f"{name}.json").write_text(json.dumps(payload))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = merge_gate.main([
                    "--head-sha", HEAD, "--qa-login", QA,
                    "--check-runs-file", str(td / "check-runs.json"),
                    "--comments-file", str(td / "comments.json"),
                    "--workflow-runs-file", str(td / "workflow-runs.json"),
                    "--compare-file", str(td / "compare.json"),
                    "--pr-number", str(PR),
                    "--base-ref", base, "--default-branch", default,
                ])
            self.assertEqual(rc, 0)
            return out.getvalue()

    def test_a_stacked_base_prints_hold(self):
        out = self._main(STACKED_BASE, DEFAULT)
        self.assertIn("decision=hold", out)
        self.assertRegex(out, rf"(?m)^reason=.*{re.escape(STACKED_BASE)}")

    def test_the_default_base_prints_merge(self):
        self.assertIn("decision=merge", self._main(DEFAULT, DEFAULT))


# --------------------------------------------------------------------------
# 2. the shipped step — no merge call, one note per head
# --------------------------------------------------------------------------
GATE_GH_STUB = r'''#!/usr/bin/env python3
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


if args[:2] == ["pr", "view"] and not opt("--jq"):
    record = dict(fx["pr"], url="https://github.com/o/r/pull/1",
                  author={"is_bot": True, "login": "app/agent-bureau-bot"})
    emit({f: record[f] for f in opt("--json").split(",") if f in record})
if args[:2] == ["pr", "view"]:
    emit(str(fx["pr"][(opt("--jq") or "").lstrip(".")]))
if args[:2] == ["run", "list"]:
    emit([])
if args[:2] in (["pr", "merge"], ["workflow", "run"]):
    raise SystemExit(0)
if args[0] == "api":
    method = opt("--method") or "GET"
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if opt("--jq") == ".default_branch":
        emit(fx["default_branch"])
    if "pulls?state=open" in path:
        emit(json.dumps({"number": int(os.environ["PR"]),
                         "head_sha": fx["pr"]["headRefOid"]}))
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
        emit([{"sha": fx["pr"]["headRefOid"]}])
    if "/comments" in path:
        emit([] if "page=2" in path or "page=3" in path else comments())
    if "/pulls/" in path:
        emit("agent-bureau-bot[bot]" if opt("--jq") else {})
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def evaluate_body() -> str:
    doc = yaml.safe_load(step_shell.workflow_source(MERGE_GATE_WF))
    steps = doc["jobs"]["evaluate"]["steps"]
    runs = [step_shell.step_shell(s) for s in steps
            if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
    return runs[0]


class Shipped:
    """One pull request's thread across gate passes, the shipped step run
    for real each time with the real merge_gate.py deciding."""

    def __init__(self, base: str):
        self.base = base
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        (self.dir / "comments.json").write_text(json.dumps([approved(HEAD)]))

    def close(self):
        self._td.cleanup()

    @property
    def comments(self) -> list:
        return json.loads((self.dir / "comments.json").read_text())

    def approve(self, at: str):
        rows = self.comments
        rows.append(dict(approved(at), id=500 + len(rows)))
        (self.dir / "comments.json").write_text(json.dumps(rows))

    def evaluate(self, head: str = HEAD):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "bin").mkdir()
            stub = td / "bin" / "gh"
            stub.write_text(GATE_GH_STUB)
            stub.chmod(0o755)  # nosec B103 — a test stub on PATH
            os.symlink(ROOT, td / ".bureau-pipeline")
            (td / "tmp").mkdir()
            (td / "fixture.json").write_text(json.dumps({
                "pr": {"headRefName": "agent/DRE-8299-plan-panel-s3",
                       "state": "OPEN", "mergeStateStatus": "CLEAN",
                       "isDraft": False, "headRefOid": head,
                       "baseRefName": self.base, "body": "What's new: none",
                       "createdAt": "2026-10-30T22:00:00Z", "files": []},
                "default_branch": DEFAULT,
                "check_runs": green_checks(),
                "compare": {"status": "ahead", "behind_by": 0,
                            "files": [{"filename": "a.py"}]},
            }))
            (td / "gh.log").write_text("")
            body = evaluate_body().replace("/tmp/", f"{td}/tmp/")
            proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
                ["bash", "-c", body], cwd=td, capture_output=True, text=True,
                timeout=120,
                env={
                    **os.environ,
                    "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "PR": str(PR), "GH_TOKEN": "qa-token",
                    "LINEAR_API_KEY": "", "REPO_FULL": REPO,
                    "WORKFLOW_TOKEN": "workflow-token", "QA_LOGIN": QA,
                    "FIXTURE": str(td / "fixture.json"),
                    "COMMENTS": str(self.dir / "comments.json"),
                    "GH_LOG": str(td / "gh.log"),
                },
            )
            calls = [json.loads(ln) for ln in
                     (td / "gh.log").read_text().splitlines() if ln]
        return proc, calls

    def notes(self) -> list:
        return [c["body"] for c in self.comments
                if c["user"]["login"] == QA and c["body"].startswith("⏸️")]


def merges(calls) -> list:
    return [c for c in calls if c[:2] == ["pr", "merge"]]


def explain(proc, calls) -> str:
    return (f"exit {proc.returncode}\nstdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\ncalls: {calls}")


class TheShippedStepNeverMergesAStackedBaseTest(unittest.TestCase):
    """Criteria 1 and 4 through the step the incident ran in."""

    def setUp(self):
        self.pr = Shipped(STACKED_BASE)
        self.addCleanup(self.pr.close)

    def test_no_merge_call_and_one_plain_note(self):
        proc, calls = self.pr.evaluate()
        self.assertEqual(proc.returncode, 0, explain(proc, calls))
        self.assertEqual(merges(calls), [], explain(proc, calls))
        self.assertIn("decision=hold", proc.stdout)
        (note,) = self.pr.notes()
        self.assertIn(STACKED_BASE, note)
        self.assertRegex(note, r"(?i)retarget")
        for marker in merge_gate.VERDICT_MARKERS:
            self.assertNotIn(marker, note)

    def test_the_note_is_posted_once_per_head_not_on_every_pass(self):
        for _ in range(3):
            proc, calls = self.pr.evaluate()
            self.assertEqual(merges(calls), [], explain(proc, calls))
        self.assertEqual(len(self.pr.notes()), 1, self.pr.notes())

    def test_a_new_head_gets_one_new_note(self):
        self.pr.evaluate()
        self.pr.approve(NEW_HEAD)
        self.pr.evaluate(head=NEW_HEAD)
        self.pr.evaluate(head=NEW_HEAD)
        notes = self.pr.notes()
        self.assertEqual(len(notes), 2, notes)
        self.assertIn(f"@{HEAD}", notes[0])
        self.assertIn(f"@{NEW_HEAD}", notes[1])


class TheShippedStepStillMergesTheDefaultBaseTest(unittest.TestCase):
    """Criterion 2 through the shipped step: same records, base `main`."""

    def test_it_reaches_the_merge_command(self):
        pr = Shipped(DEFAULT)
        self.addCleanup(pr.close)
        proc, calls = pr.evaluate()
        self.assertEqual(proc.returncode, 0, explain(proc, calls))
        self.assertEqual(len(merges(calls)), 1, explain(proc, calls))
        self.assertEqual(pr.notes(), [])


# --------------------------------------------------------------------------
# 3. the fix agent merges the pull request's own base
# --------------------------------------------------------------------------
def fix_prompt() -> str:
    for step in yaml.safe_load(AGENT_FIX_WF.read_text())["jobs"]["fix"]["steps"]:
        if step.get("name") == "Fix":
            return step["with"]["prompt"]
    raise AssertionError("agent-fix.yml has no step named 'Fix'")


def step_2b() -> str:
    prompt = fix_prompt()
    start = prompt.index("2b. Check for a merge conflict")
    end = prompt.index("\n3. ", start)
    return " ".join(prompt[start:end].split())


BASE_EXPR = "${{ steps.pr.outputs.base_ref }}"


class StepTwoBNamesTheBaseRefTest(unittest.TestCase):
    """Criterion 3: the instruction names the pull request's base ref, and
    cannot drift back to `origin/<default-branch>`."""

    def test_the_merge_names_the_base_ref(self):
        self.assertIn(f"git merge origin/{BASE_EXPR}", step_2b())

    def test_the_default_branch_is_never_what_it_merges(self):
        step = step_2b()
        self.assertNotIn("origin/<default-branch>", step)
        self.assertNotRegex(step, r"git merge origin/(main|master)\b")

    def test_the_lockfile_copy_comes_from_the_base_too(self):
        self.assertIn(f"git checkout origin/{BASE_EXPR} -- <lockfile>", step_2b())

    def test_the_base_ref_output_exists(self):
        # The expression above resolves only if step `pr` publishes it.
        self.assertIn('echo "base_ref=$BASE_REF" >> "$GITHUB_OUTPUT"',
                      RESOLVE_FIX.read_text())


def conflict_escalation(base: str) -> str:
    """Run resolve_fix_pr.sh by path on a DIRTY pull request with no critic
    verdict standing — conflict mode — and return its `escalation` output."""
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(FIX_GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        (td / ".bureau-pipeline").symlink_to(ROOT)
        info = td / "pr-info.json"
        info.write_text(json.dumps({
            "state": "OPEN", "headRefName": "agent/DRE-8281-send-to-planning-pr",
            "headRefOid": "8281" * 10, "mergeStateStatus": "DIRTY",
            "baseRefName": base, "isDraft": False,
        }))
        comments = td / "comments.json"
        comments.write_text("[]")
        out_file = td / "step-output"
        out_file.write_text("")
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own script
            ["bash", str(RESOLVE_FIX)], cwd=td,
            env=dict(
                os.environ,
                PATH=f"{td}/bin:{os.environ['PATH']}",
                GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                GH_PR_INFO=str(info), GH_COMMENTS=str(comments),
                GH_LOG=str(td / "gh-writes.jsonl"),
                GH_TOKEN="test", WRITER_TOKEN="test-writer",
                WORKER_LOGIN=WORKER, EVENT_NAME="workflow_dispatch",
                TRIGGERING_ACTOR="github-actions[bot]",
                PR_NUMBER="4381", REPO=REPO,
            ),
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        outputs = dict(line.partition("=")[::2]
                       for line in out_file.read_text().splitlines() if "=" in line)
    assert outputs.get("mode") == "conflict", outputs
    return outputs["escalation"]


class ConflictModeMergesTheOwnBaseTest(unittest.TestCase):
    """The conflict-mode instruction is what produced the incident's
    `merge: resolve conflicts with main` commits; it names the base too."""

    def test_a_stacked_pull_request_merges_its_own_base(self):
        text = conflict_escalation("agent/DRE-8277-initiatives-drafts-pr")
        self.assertIn("origin/agent/DRE-8277-initiatives-drafts-pr", text)
        self.assertNotIn("origin/<default-branch>", text)
        self.assertNotIn("Merge the default branch", text)
        self.assertNotIn("resolve conflicts with main", text)
        self.assertIn("resolve conflicts with agent/DRE-8277-initiatives-drafts-pr", text)

    def test_an_ordinary_pull_request_still_merges_main(self):
        text = conflict_escalation("main")
        self.assertIn("origin/main", text)
        self.assertIn("resolve conflicts with main", text)


if __name__ == "__main__":
    unittest.main()
