"""RED-first tests for DRE-5228 — the merge gate wakes on the Verifier's
verdict, and its hold leaves a note on the pull request.

Two gaps in merge-gate.yml, one job:

  1. THE WAKE. The `issue_comment` leg admitted only a qa-bot comment
     carrying `QA Critic`. The Verifier's verdict (`🧪 QA Verifier — VERDICT:
     FAIL @<sha>`) woke the gate only where the calling stub's
     `workflow_run` list happened to name `Verify` — agent-bureau's does,
     the scaffold's and this repo's own do not. The comment leg now admits
     either marker, inside the same qa-bot-authored clause, so the wake no
     longer depends on per-repo data.
  2. THE NOTE. `decision=hold` fell through to `[ "$DECISION" = "merge" ]
     || exit 0` — a green run that said nothing on the PR. A generic hold
     (one with no `stacked_on=` and no `owner_hold=`, which already speak)
     now posts ONE note through scripts/gate_note.py, keyed on
     `Merge gate: declined @<head sha> — <reason>`, so the same hold on the
     same head never posts twice and a new head or a new reason does.

The wake is tested through the job-if evaluator tests/test_merge_gate_one_job.py
already uses (scripts/fix_concurrency.py, live-extracted from the workflow).
The note is tested by running the shipped `Evaluate and merge` body under
bash against a stub `gh`, stub pipeline scripts, and a `gate_note.py` stub
that runs the REAL `gate_note.post_once` over a file-backed comment thread —
so "posts nothing new" is the module's own idempotence, not an assumption.

Run: cd bureau-pipeline && python3 -m pytest tests/test_merge_gate_hold_note.py -v
"""

from __future__ import annotations

import ast
import json
import os
import subprocess  # nosec B404 — fixed argv, our own workflow body
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
SCRIPTS = ROOT / "scripts"
ACTS = ROOT / "config" / "pipeline-acts.json"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import merge_gate  # noqa: E402
from test_merge_gate_one_job import (  # noqa: E402
    QA_BOT, comment_event, evaluate_runs, resolve_runs,
)

PR = 5228
REPO = "dreadnought-foundry/bureau-pipeline"
BRANCH = "agent/DRE-5228-gate-hold-note"
HEAD = "5228" * 10
NEW_HEAD = "beef" * 10
VERIFIER_HOLD = "latest verifier verdict is not PASS — holding"
CRITIC_HOLD = "latest verdict is not APPROVE — holding"
# A real reason merge_gate.py emits that ends on punctuation, not a word: the
# shape the note's marker has to survive (see the arm's comment).
STACK_UNKNOWN_HOLD = (
    "UNKNOWN: the record of this branch's commits is missing or truncated, "
    "and other pull requests are open — the gate cannot prove this branch "
    "carries none of their unapproved work, so it holds (DRE-4103)"
)
VERIFIER_FAIL_BODY = f"🧪 QA Verifier — VERDICT: FAIL @{HEAD}"
VERIFIER_NOTICE_BODY = (
    "🧪 QA Verifier could not run (infra error) — re-verify needed, this is "
    "NOT a feature rejection."
)
# GitHub's contains() is case-insensitive, so these are compared lowered.
VERDICT_SHAPED = ("VERDICT:", "QA Critic", "QA Verifier")


def note_line(sha: str, reason: str) -> str:
    return f"⏸️ Merge gate: declined @{sha} — {reason}"


# --------------------------------------------------------------------------
# 1. the wake
# --------------------------------------------------------------------------
class VerifierWakeTest(unittest.TestCase):
    def test_a_verifier_fail_from_the_qa_bot_wakes_the_gate(self):
        event = comment_event(VERIFIER_FAIL_BODY)
        self.assertFalse(resolve_runs(event), "issue.number names the PR — no lookup job")
        self.assertTrue(evaluate_runs(event))

    def test_the_verifiers_could_not_run_notice_wakes_the_gate(self):
        self.assertTrue(evaluate_runs(comment_event(VERIFIER_NOTICE_BODY)))

    def test_a_verifier_pass_and_skip_wake_the_gate_too(self):
        for token in ("PASS", "SKIP"):
            with self.subTest(token=token):
                body = f"🧪 QA Verifier — VERDICT: {token} @{HEAD}"
                self.assertTrue(evaluate_runs(comment_event(body)))

    def test_a_human_typing_qa_verifier_does_not_wake_the_gate(self):
        self.assertFalse(evaluate_runs(comment_event(VERIFIER_FAIL_BODY, login="someone")))
        self.assertFalse(evaluate_runs(comment_event("QA Verifier", login="someone")))

    def test_the_gates_own_decline_note_does_not_wake_the_gate(self):
        for reason in (VERIFIER_HOLD, CRITIC_HOLD, STACK_UNKNOWN_HOLD):
            with self.subTest(reason=reason):
                self.assertFalse(evaluate_runs(comment_event(note_line(HEAD, reason))))

    def test_the_critic_still_wakes_the_gate(self):
        self.assertTrue(evaluate_runs(comment_event(f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}")))

    def test_the_author_equality_is_written_once_in_the_comment_clause(self):
        cond = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"]["if"]
        self.assertEqual(cond.count(f"github.event.comment.user.login == '{QA_BOT}'"), 1)
        flat = " ".join(cond.split())
        self.assertIn(
            "(contains(github.event.comment.body, 'QA Critic') || "
            "contains(github.event.comment.body, 'QA Verifier'))",
            flat,
        )


# --------------------------------------------------------------------------
# 2. the note — the shipped step body, run for real
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
def opt(name):
    return args[args.index(name) + 1] if name in args else None
if args[:2] == ["pr", "view"]:
    view = {".headRefName": os.environ["BRANCH"], ".state": "OPEN",
            ".mergeStateStatus": "CLEAN", ".isDraft": "false",
            ".headRefOid": os.environ["HEAD_SHA"], ".baseRefName": "main",
            ".url": "https://github.com/x/y/pull/1"}
    print(view.get(opt("--jq"), ""))
    raise SystemExit(0)
if args[:2] in (["pr", "merge"], ["workflow", "run"]):
    raise SystemExit(0)
if args[:1] == ["api"]:
    if opt("--jq") == ".user.login":
        print("agent-bureau-bot[bot]")
    elif opt("--jq") == ".default_branch":
        print("main")
    else:
        print("[]" if "comments" in " ".join(args) else "{}")
    raise SystemExit(0)
sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''

# Records every call; stands in for scripts the step runs that this card does
# not change (the gatherers, the Linear write, the fork-refresh decision).
RECORD_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["SCRIPT_LOG"], "a") as fh:
    fh.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\n")
if os.path.basename(sys.argv[0]) == "order_sensitive_refresh.py":
    print("decision=proceed")
    print("reason=nothing order-sensitive")
'''

# The decision script, answering whatever the scenario says it decided.
DECISION_STUB = r'''#!/usr/bin/env python3
import os, sys
sys.stdout.write(os.environ["DECISION_OUT"])
'''

# gate_note.py: records what the step handed it, then runs the REAL
# post_once over a comment thread kept in a JSON file, so a second
# evaluation sees what the first one posted.
GATE_NOTE_STUB = r'''#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, os.environ["REAL_SCRIPTS"])
import gate_note
args = gate_note.build_parser().parse_args(sys.argv[1:])
body = open(args.body_file, encoding="utf-8").read()
with open(os.environ["NOTE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "body": body}) + "\n")
path = os.environ["THREAD"]
class FileThread:
    def _load(self):
        with open(path) as fh:
            return json.load(fh)
    def list_comments(self):
        return self._load()
    def create_comment(self, text):
        thread = self._load()
        c = {"id": len(thread) + 1, "user": {"login": args.author}, "body": text}
        thread.append(c)
        with open(path, "w") as fh:
            json.dump(thread, fh)
        return c
    def delete_comment(self, cid):
        thread = [c for c in self._load() if c["id"] != cid]
        with open(path, "w") as fh:
            json.dump(thread, fh)
gate_note.post_once(FileThread(), args.marker, body, args.author)
'''

STUBBED = ("stranded_fix.py", "stacked_prs.py", "code_owner_hold.py",
           "linear_ops.py", "order_sensitive_refresh.py")


def evaluate_body() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"]["steps"]
    found = [s for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(found) == 1
    return found[0]["run"]


def decision_out(decision: str, reason: str, *extra: str) -> str:
    return "\n".join([f"decision={decision}", f"reason={reason}", *extra]) + "\n"


class Thread:
    """One pull request's comment thread, persisting across evaluations."""

    def __init__(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        (self.dir / "thread.json").write_text("[]")

    def close(self):
        self._td.cleanup()

    @property
    def comments(self) -> list:
        return json.loads((self.dir / "thread.json").read_text())

    def evaluate(self, out: str, sha: str = HEAD) -> "Run":
        """Run the shipped step once against this thread."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "bin").mkdir()
            (td / "tmp").mkdir()
            gh = td / "bin" / "gh"
            gh.write_text(GH_STUB)
            gh.chmod(0o755)  # nosec B103 — a test stub on PATH
            scripts = td / ".bureau-pipeline" / "scripts"
            scripts.mkdir(parents=True)
            for name in STUBBED:
                (scripts / name).write_text(RECORD_STUB)
            (scripts / "merge_gate.py").write_text(DECISION_STUB)
            (scripts / "gate_note.py").write_text(GATE_NOTE_STUB)
            for log in ("gh.log", "scripts.log", "notes.log"):
                (td / log).write_text("")
            # The body writes its scratch files under /tmp; point them at
            # this run's own directory so parallel runs never share them.
            body = evaluate_body().replace("/tmp/", f"{td}/tmp/")
            proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
                ["bash", "-c", body], cwd=td, capture_output=True, text=True,
                env={
                    **os.environ,
                    "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "PR": str(PR),
                    "GH_TOKEN": "qa-token",
                    "LINEAR_API_KEY": "test-key",
                    "REPO_FULL": REPO,
                    "WORKFLOW_TOKEN": "workflow-token",
                    "QA_LOGIN": QA_BOT,
                    "BRANCH": BRANCH,
                    "HEAD_SHA": sha,
                    "DECISION_OUT": out,
                    "REAL_SCRIPTS": str(SCRIPTS),
                    "THREAD": str(self.dir / "thread.json"),
                    "GH_LOG": str(td / "gh.log"),
                    "SCRIPT_LOG": str(td / "scripts.log"),
                    "NOTE_LOG": str(td / "notes.log"),
                },
            )
            read = lambda p: [json.loads(ln) for ln in  # noqa: E731
                              p.read_text().splitlines() if ln]
            return Run(proc, read(td / "gh.log"), read(td / "scripts.log"),
                       read(td / "notes.log"))


class Run:
    def __init__(self, proc, gh, scripts, notes):
        self.proc, self.gh, self.scripts, self.notes = proc, gh, scripts, notes

    @property
    def merges(self):
        return [c for c in self.gh if c[:2] == ["pr", "merge"]]

    @property
    def dispatches(self):
        return [c for c in self.gh if c[:2] == ["workflow", "run"]]

    @property
    def markers(self):
        return [n["argv"][n["argv"].index("--marker") + 1] for n in self.notes]

    def explain(self):
        return (f"exit {self.proc.returncode}\nstdout:\n{self.proc.stdout}\n"
                f"stderr:\n{self.proc.stderr}\ngh: {self.gh}\n"
                f"scripts: {self.scripts}\nnotes: {self.notes}")


class ThreadCase(unittest.TestCase):
    def setUp(self):
        self.thread = Thread()
        self.addCleanup(self.thread.close)

    def declines(self):
        return [c for c in self.thread.comments
                if c["body"].startswith("⏸️ Merge gate: declined @")]


class AVerifierFailHoldSpeaksTest(ThreadCase):
    """A critic APPROVE, green CI and a Verifier FAIL: the decision script
    says hold, and the PR now says so."""

    def setUp(self):
        super().setUp()
        self.run_ = self.thread.evaluate(decision_out("hold", VERIFIER_HOLD))

    def test_the_step_exits_clean_without_merging(self):
        self.assertEqual(self.run_.proc.returncode, 0, self.run_.explain())
        self.assertEqual(self.run_.merges, [], self.run_.explain())

    def test_one_note_whose_first_line_names_the_head_and_the_reason(self):
        self.assertEqual(len(self.thread.comments), 1, self.run_.explain())
        first = self.thread.comments[0]["body"].splitlines()[0]
        self.assertEqual(first, note_line(HEAD, VERIFIER_HOLD))

    def test_the_marker_is_the_head_and_the_reason(self):
        self.assertEqual(self.run_.markers,
                         [f"Merge gate: declined @{HEAD} — {VERIFIER_HOLD}"])

    def test_it_is_posted_through_gate_note_as_the_qa_bot(self):
        (note,) = self.run_.notes
        argv = note["argv"]
        self.assertEqual(argv[argv.index("--author") + 1], QA_BOT)
        self.assertEqual(argv[argv.index("--repo") + 1], REPO)
        self.assertEqual(argv[argv.index("--pr") + 1], str(PR))
        self.assertTrue(argv[argv.index("--body-file") + 1].endswith("/tmp/hold-note.md"))
        self.assertEqual(self.thread.comments[0]["user"]["login"], QA_BOT)

    def test_the_note_carries_no_verdict_shaped_text(self):
        body = self.thread.comments[0]["body"].lower()
        for phrase in VERDICT_SHAPED:
            self.assertNotIn(phrase.lower(), body)


class OncePerHeadAndReasonTest(ThreadCase):
    def test_the_same_hold_on_the_same_head_posts_nothing_new(self):
        out = decision_out("hold", VERIFIER_HOLD)
        first = self.thread.evaluate(out)
        second = self.thread.evaluate(out)
        self.assertEqual(second.proc.returncode, 0, second.explain())
        self.assertEqual(len(self.declines()), 1, first.explain() + second.explain())
        self.assertEqual(first.markers, second.markers)

    def test_a_new_head_gets_a_new_note(self):
        out = decision_out("hold", VERIFIER_HOLD)
        self.thread.evaluate(out)
        run = self.thread.evaluate(out, sha=NEW_HEAD)
        self.assertEqual(run.markers, [f"Merge gate: declined @{NEW_HEAD} — {VERIFIER_HOLD}"])
        self.assertEqual(
            [c["body"].splitlines()[0] for c in self.declines()],
            [note_line(HEAD, VERIFIER_HOLD), note_line(NEW_HEAD, VERIFIER_HOLD)],
        )

    def test_the_same_head_with_a_different_reason_gets_a_new_note(self):
        self.thread.evaluate(decision_out("hold", CRITIC_HOLD))
        run = self.thread.evaluate(decision_out("hold", VERIFIER_HOLD))
        self.assertEqual(run.markers, [f"Merge gate: declined @{HEAD} — {VERIFIER_HOLD}"])
        self.assertEqual(
            [c["body"].splitlines()[0] for c in self.declines()],
            [note_line(HEAD, CRITIC_HOLD), note_line(HEAD, VERIFIER_HOLD)],
        )

    def test_a_reason_ending_on_punctuation_is_still_posted_once(self):
        """gate_note.py reads a note as posted when its first line opens with
        the marker AND a word boundary follows (merge_gate.opens_with_marker).
        A reason ending `(DRE-4103)` leaves no boundary after a marker that
        carries it whole, so the same hold would re-post on every wake."""
        out = decision_out("hold", STACK_UNKNOWN_HOLD)
        first = self.thread.evaluate(out)
        second = self.thread.evaluate(out)
        self.assertEqual(len(self.declines()), 1, first.explain() + second.explain())
        self.assertEqual(self.declines()[0]["body"].splitlines()[0],
                         note_line(HEAD, STACK_UNKNOWN_HOLD))

    def test_every_marker_the_arm_hands_over_matches_its_own_note(self):
        for reason in (VERIFIER_HOLD, CRITIC_HOLD, STACK_UNKNOWN_HOLD):
            with self.subTest(reason=reason):
                thread = Thread()
                try:
                    run = thread.evaluate(decision_out("hold", reason))
                    (note,) = run.notes
                    (marker,) = run.markers
                    self.assertTrue(merge_gate.opens_with_marker(note["body"], marker),
                                    run.explain())
                finally:
                    thread.close()


class HoldsThatAlreadySpeakTest(ThreadCase):
    def test_a_stack_hold_posts_only_its_own_note(self):
        run = self.thread.evaluate(decision_out(
            "hold", "this branch also carries other open pull requests", "stacked_on=#7"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.markers, ["Merge gate: held for #7 until approved"])
        self.assertEqual(self.declines(), [])

    def test_a_code_owner_hold_posts_no_generic_note(self):
        run = self.thread.evaluate(decision_out(
            "hold", "a required code-owner review is missing (DRE-4341)",
            'owner_hold=[{"owners": ["@a"], "folders": ["x/"]}]'))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.notes, [])
        self.assertIn("code_owner_hold.py", [c[0] for c in run.scripts])
        self.assertEqual(self.declines(), [])


class OtherDecisionsUnchangedTest(ThreadCase):
    def test_wait_posts_nothing_and_merges_nothing(self):
        run = self.thread.evaluate(decision_out("wait", "CI still running"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual((run.notes, run.merges, run.dispatches), ([], [], []))

    def test_conflict_dispatches_the_fix_agent_and_posts_nothing(self):
        run = self.thread.evaluate(decision_out("conflict", "merge conflict with main"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.dispatches), 1, run.explain())
        self.assertEqual((run.notes, run.merges), ([], []))

    def test_human_posts_only_the_waiting_for_human_note(self):
        run = self.thread.evaluate(decision_out("human", "semver major"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.markers, ["Merge gate: waiting for human merge"])
        self.assertEqual((self.declines(), run.merges), ([], []))

    def test_merge_merges_and_posts_no_decline(self):
        run = self.thread.evaluate(decision_out("merge", "all green"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.merges), 1, run.explain())
        self.assertEqual(run.notes, [])


class ArmPlacementTest(unittest.TestCase):
    def test_the_arm_sits_after_the_code_owner_arm_and_before_the_merge_guard(self):
        body = evaluate_body()
        owner = body.find("code_owner_hold.py hold")
        arm = body.find("--body-file /tmp/hold-note.md")
        guard = body.find('[ "$DECISION" = "merge" ] || exit 0')
        self.assertGreater(owner, -1)
        self.assertGreater(arm, owner)
        self.assertGreater(guard, arm)


# --------------------------------------------------------------------------
# 3. the reasons the note quotes carry no verdict-shaped text
# --------------------------------------------------------------------------
def hold_reason_literals() -> list[str]:
    """Every string literal in the reason argument of a `Decision("hold", …)`
    call in scripts/merge_gate.py — f-string pieces included."""
    tree = ast.parse((SCRIPTS / "merge_gate.py").read_text())
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Decision" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "hold"):
            continue
        reason = node.args[1] if len(node.args) > 1 else next(
            k.value for k in node.keywords if k.arg == "reason")
        found.append("".join(
            n.value for n in ast.walk(reason)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)))
    return found


class HoldReasonsAreNotVerdictShapedTest(unittest.TestCase):
    def test_the_reasons_are_found(self):
        reasons = hold_reason_literals()
        self.assertGreaterEqual(len(reasons), 6, reasons)
        self.assertIn(VERIFIER_HOLD, reasons)
        self.assertIn(CRITIC_HOLD, reasons)

    def test_no_hold_reason_carries_a_verdict_marker_in_any_case(self):
        for reason in hold_reason_literals():
            for phrase in VERDICT_SHAPED:
                with self.subTest(reason=reason, phrase=phrase):
                    self.assertNotIn(phrase.lower(), reason.lower())


# --------------------------------------------------------------------------
# 4. the registry and the header
# --------------------------------------------------------------------------
class RegistryTest(unittest.TestCase):
    def test_the_note_is_declared_unconverted_right_after_the_refresh_note(self):
        entries = json.loads(ACTS.read_text())["unconverted"]
        anchors = [e.get("anchor") for e in entries]
        self.assertIn("--body-file /tmp/hold-note.md", anchors)
        at = anchors.index("--body-file /tmp/hold-note.md")
        self.assertEqual(anchors[at - 1], "--body-file /tmp/refresh-note.md")
        entry = entries[at]
        self.assertEqual(entry["file"], ".github/workflows/merge-gate.yml")
        self.assertEqual(entry["step"], "Evaluate and merge")
        self.assertTrue(entry.get("why"))

    def test_check_act_receipts_passes(self):
        proc = subprocess.run(  # nosec B603 — fixed argv, our own script
            [sys.executable, str(SCRIPTS / "check_act_receipts.py")],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class HeaderTest(unittest.TestCase):
    def test_the_header_names_the_verifier_wake_and_the_hold_note(self):
        header = WORKFLOW.read_text().split("\nname:", 1)[0]
        self.assertIn("QA Verifier", header)
        self.assertIn("declined @", header)


if __name__ == "__main__":
    unittest.main()
