"""The merge gate asks the Verifier's own rule before it holds for a fresh
verify (DRE-7165).

The Verifier decides afresh, on every head, whether a pull request is its to
gate (`verify_scope.decide`, the rule's one writer). The gate found a Verifier
verdict from an EARLIER head, called it stale and held for a fresh one, and
never asked whether the Verifier still gates this pull request. When the
Verifier then declined, no fresh verdict ever came.

Seen 2026-10-12 on agent-bureau #3741 (DRE-7075): the Verifier passed
4a26218, the pull request moved to 6b0dbca, the critic approved that head and
CI was green. The Verifier's run on 6b0dbca logged "single-system with no UI
signal … Verifier does not run", and the gate answered `verifier verdict is
for 4a2621857… but head is now 6b0dbcbae… — stale; holding for a fresh
verify`. Neither side moves.

Now only the stale arm of condition 3 changes. Given a card snapshot
(`--card` / `--card-snapshot-file`), the gate asks `verify_scope.decide` with
the Verifier's two inputs at the CURRENT head — the compare record's
`files[]` and the card:

  1. in scope        → hold for a fresh verify, naming the signal;
  2. out of scope    → the earlier verdict is not a requirement, a merge
                       carries a `note=` saying so, nothing is carried;
  3. cannot be asked → hold, naming the input it could not read.

With no snapshot flag the decision is exactly today's, and the stale hold
prints `verifier_scope=ask` so scripts/evaluate_and_merge.sh takes the one
card read and asks again. Part 2 runs that script against the stub `gh`.

Run: python3 -m pytest tests/test_merge_gate_verifier_scope.py -v
"""

from __future__ import annotations

import io
import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "evaluate_and_merge.sh"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import merge_gate  # noqa: E402
import verify_scope  # noqa: E402
from test_merge_gate_one_read import (  # noqa: E402
    CI_RUN_DONE, GH_STUB, GREEN_CI, LINEAR_STUB, view,
)

QA = "agent-bureau-qa-bot[bot]"
HEAD = "6b0dbcbae" + "1" * 31
EARLIER = "4a2621857" + "2" * 31
CARD = "DRE-7075"
CARD_BRANCH = f"agent/{CARD}-repo-labels"
NO_CARD_BRANCH = "agent/repo-labels-no-card"

CRITIC_APPROVE = f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"
VERIFIER_PASS_EARLIER = f"🧪 QA Verifier — VERDICT: PASS @{EARLIER}"

TODAYS_STALE_HOLD = (f"verifier verdict is for {EARLIER} but head is now {HEAD} — "
                     "stale; holding for a fresh verify")

SINGLE_BACKEND = ["backend/app/service.py", "backend/app/models.py"]
BACKEND_AND_WEB = ["backend/app/service.py", "web/src/App.tsx"]
FRONTEND_ONLY = ["web/src/App.tsx"]

NON_UI_CARD = {"identifier": CARD, "source": "linear",
               "description": "Route the relay by repo label.\n\n## Acceptance criteria\n- [ ] it routes",
               "labels": ["agent:engineer", "repo:agent-bureau"], "comments": []}


def card(**overrides) -> dict:
    snap = dict(NON_UI_CARD)
    snap.update(overrides)
    return snap


def compare(files) -> dict:
    """GitHub's three-dot compare record for `files`, whole."""
    return {"status": "ahead", "merge_base_commit": {"sha": "b" * 40},
            "commits": [{"sha": EARLIER}, {"sha": HEAD}],
            "files": [{"filename": f, "sha": f"{i:040x}", "status": "modified"}
                      for i, f in enumerate(files)]}


def gate(*, files=SINGLE_BACKEND, snapshot=None, card_id=CARD, branch=CARD_BRANCH,
         comments=(VERIFIER_PASS_EARLIER, CRITIC_APPROVE), ask=True, raw_compare=None):
    """merge_gate.main over the #3741 records. `snapshot` is the card snapshot
    (None: no file written); `ask=False` passes no card-snapshot flag at all,
    the way every caller did before DRE-7165. Returns (exit, fields, stdout)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "cr.json").write_text(json.dumps({"check_runs": GREEN_CI}))
        (td / "wr.json").write_text(json.dumps({"workflow_runs": [CI_RUN_DONE]}))
        (td / "cm.json").write_text(json.dumps(
            [{"id": i, "user": {"login": QA}, "body": b} for i, b in enumerate(comments)]))
        (td / "cp.json").write_text(json.dumps(
            raw_compare if raw_compare is not None else compare(files)))
        (td / "pc.json").write_text(json.dumps([{"sha": EARLIER}, {"sha": HEAD}]))
        argv = ["--head-sha", HEAD, "--qa-login", QA,
                "--check-runs-file", str(td / "cr.json"),
                "--comments-file", str(td / "cm.json"),
                "--workflow-runs-file", str(td / "wr.json"),
                "--compare-file", str(td / "cp.json"),
                "--pr-commits-file", str(td / "pc.json"),
                "--head-branch", branch]
        if ask:
            snap = td / "card-snapshot.json"
            if snapshot is not None:
                snap.write_text(json.dumps(snapshot))
            argv += ["--card", card_id, "--card-snapshot-file", str(snap)]
        out = io.StringIO()
        with redirect_stdout(out):
            code = merge_gate.main(argv)
    stdout = out.getvalue()
    fields: dict[str, str] = {}
    notes = []
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "note":
            notes.append(value)
        else:
            fields.setdefault(key, value)
    fields["notes"] = notes
    return code, fields, stdout


# --------------------------------------------------------------------------
# 1. the #3741 shape: the Verifier does not gate this head, so the gate merges
# --------------------------------------------------------------------------
class TheVerifierDoesNotGateThisHeadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code, cls.fields, cls.stdout = gate(snapshot=card())

    def test_the_gate_admits_the_merge(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.fields.get("decision"), "merge", self.stdout)

    def test_a_note_says_the_verifier_does_not_gate_the_head(self):
        notes = [n for n in self.fields["notes"] if "verifier does not gate head" in n]
        self.assertEqual(len(notes), 1, self.stdout)
        self.assertIn(HEAD, notes[0])
        self.assertIn(EARLIER, notes[0])
        self.assertIn("single-system, no UI signal", notes[0])
        self.assertIn("is not a requirement", notes[0])

    def test_the_ignored_verdict_is_not_carried(self):
        self.assertNotIn("verifier@", self.fields.get("carried", ""), self.stdout)
        self.assertNotIn("carried", self.fields, self.stdout)

    def test_no_second_look_is_asked_for(self):
        self.assertNotIn("verifier_scope", self.fields, self.stdout)

    def test_a_stale_fail_is_not_a_requirement_either(self):
        code, fields, stdout = gate(
            snapshot=card(),
            comments=(f"🧪 QA Verifier — VERDICT: FAIL @{EARLIER}", CRITIC_APPROVE))
        self.assertEqual(fields.get("decision"), "merge", stdout)

    def test_a_card_with_an_empty_description_is_no_card_signal(self):
        """`card_from_snapshot`'s rule, as the Verifier reads it: a snapshot
        that was read but says nothing gives no UI signal — not case 3."""
        code, fields, stdout = gate(snapshot=card(description="", labels=["ux"]),
                                    files=FRONTEND_ONLY)
        self.assertEqual(fields.get("decision"), "merge", stdout)


class ABranchNamingNoCardDecidesFromThePathsTest(unittest.TestCase):
    def test_a_single_bucket_compare_admits_the_merge(self):
        code, fields, stdout = gate(branch=NO_CARD_BRANCH, card_id="", snapshot=None)
        self.assertEqual(fields.get("decision"), "merge", stdout)
        self.assertTrue(any("verifier does not gate head" in n for n in fields["notes"]),
                        stdout)

    def test_a_two_bucket_compare_still_holds(self):
        code, fields, stdout = gate(branch=NO_CARD_BRANCH, card_id="", snapshot=None,
                                    files=BACKEND_AND_WEB)
        self.assertEqual(fields.get("decision"), "hold", stdout)
        self.assertIn("multi: backend, frontend", fields["reason"])


# --------------------------------------------------------------------------
# 2. the real hold: the Verifier gates this head, one case per signal
# --------------------------------------------------------------------------
class TheVerifierGatesThisHeadTest(unittest.TestCase):
    def assert_holds(self, signal, **kwargs):
        code, fields, stdout = gate(**kwargs)
        self.assertEqual(code, 0)
        self.assertEqual(fields.get("decision"), "hold", stdout)
        reason = fields["reason"]
        self.assertIn(f"verifier gates head {HEAD}", reason)
        self.assertIn(signal, reason)
        self.assertIn(f"its verdict is for {EARLIER}", reason)
        self.assertIn("holding for a fresh verify", reason)
        self.assertNotIn("verifier_scope", fields, stdout)
        return reason

    def test_a_design_line_holds(self):
        self.assert_holds("design", snapshot=card(
            description="Build the screen.\n\n**Design:** console/design/x.png\n"))

    def test_a_ui_role_label_on_a_frontend_diff_holds(self):
        self.assert_holds("ui_role", files=FRONTEND_ONLY,
                          snapshot=card(labels=["agent:frontend", "repo:agent-bureau"]))

    def test_two_buckets_on_a_non_ui_card_hold(self):
        self.assert_holds("multi: backend, frontend", files=BACKEND_AND_WEB,
                          snapshot=card())

    def test_a_ui_role_label_without_a_frontend_diff_does_not(self):
        code, fields, stdout = gate(snapshot=card(labels=["agent:frontend"]))
        self.assertEqual(fields.get("decision"), "merge", stdout)


# --------------------------------------------------------------------------
# 3. the rule cannot be asked: hold, naming the input it could not read
# --------------------------------------------------------------------------
class TheRuleCannotBeAskedTest(unittest.TestCase):
    def assert_holds(self, named, **kwargs):
        code, fields, stdout = gate(**kwargs)
        self.assertEqual(code, 0)
        self.assertEqual(fields.get("decision"), "hold", stdout)
        reason = fields["reason"]
        self.assertIn(f"could not ask whether the verifier gates head {HEAD}", reason)
        self.assertIn(named, reason)
        self.assertIn("holding for a fresh verify", reason)
        self.assertNotIn("verifier_scope", fields, stdout)

    def test_a_card_on_the_branch_with_no_snapshot_holds(self):
        self.assert_holds(f"no snapshot of {CARD}", snapshot=None)

    def test_an_unreadable_snapshot_holds(self):
        self.assert_holds(f"no snapshot of {CARD}", snapshot=["not", "a", "snapshot"])

    def test_an_empty_compare_record_holds(self):
        self.assert_holds("compare record", snapshot=card(), raw_compare={})

    def test_a_compare_record_of_300_files_holds(self):
        files = [f"backend/app/f{i}.py" for i in range(300)]
        self.assert_holds("300", snapshot=card(), files=files)

    def test_a_compare_record_of_299_files_is_asked(self):
        files = [f"backend/app/f{i}.py" for i in range(299)]
        code, fields, stdout = gate(snapshot=card(), files=files)
        self.assertEqual(fields.get("decision"), "merge", stdout)


# --------------------------------------------------------------------------
# 4. every other arm is unchanged, and no flag is exactly today
# --------------------------------------------------------------------------
class EveryOtherArmIsUnchangedTest(unittest.TestCase):
    def test_no_flag_is_todays_stale_hold_and_asks_for_the_second_look(self):
        code, fields, stdout = gate(ask=False)
        self.assertEqual(fields.get("decision"), "hold", stdout)
        self.assertEqual(fields["reason"], TODAYS_STALE_HOLD)
        self.assertEqual(fields.get("verifier_scope"), "ask", stdout)

    def test_no_flag_decides_exactly_as_before(self):
        direct = merge_gate.evaluate_verifier(VERIFIER_PASS_EARLIER, HEAD)
        self.assertEqual(direct[0].action, "hold")
        self.assertEqual(direct[0].reason, TODAYS_STALE_HOLD)

    def test_only_the_stale_hold_asks_for_the_second_look(self):
        for comments in (
            (CRITIC_APPROVE,),
            (f"🧪 QA Verifier — VERDICT: PASS @{HEAD}", CRITIC_APPROVE),
            (f"🧪 QA Verifier — VERDICT: FAIL @{HEAD}", CRITIC_APPROVE),
            ("🧪 QA Verifier — VERDICT: PASS", CRITIC_APPROVE),
            (VERIFIER_PASS_EARLIER, f"🔎 QA Critic — VERDICT: APPROVE @{EARLIER}"),
        ):
            with self.subTest(comments=comments):
                code, fields, stdout = gate(ask=False, comments=comments)
                self.assertNotIn("verifier_scope", fields, stdout)

    def test_a_fail_bound_to_the_head_holds_whatever_the_scope(self):
        code, fields, stdout = gate(
            snapshot=card(),
            comments=(f"🧪 QA Verifier — VERDICT: FAIL @{HEAD}", CRITIC_APPROVE))
        self.assertEqual(fields.get("decision"), "hold", stdout)
        self.assertEqual(fields["reason"], "latest verifier verdict is not PASS — holding")

    def test_a_verdict_naming_no_commit_holds_whatever_the_scope(self):
        code, fields, stdout = gate(
            snapshot=card(), comments=("🧪 QA Verifier — VERDICT: PASS", CRITIC_APPROVE))
        self.assertEqual(fields.get("decision"), "hold", stdout)
        self.assertIn("names no verified commit", fields["reason"])

    def test_a_verdict_that_carries_by_content_still_carries(self):
        payload = compare(BACKEND_AND_WEB)
        cid = merge_gate.content_id(payload)
        code, fields, stdout = gate(
            snapshot=card(), raw_compare=payload,
            comments=(f"{VERIFIER_PASS_EARLIER} content:{cid}", CRITIC_APPROVE))
        self.assertEqual(fields.get("decision"), "merge", stdout)
        self.assertIn(f"verifier@{EARLIER}", fields.get("carried", ""), stdout)


# --------------------------------------------------------------------------
# 5. the rule is decided in one place: the gate follows verify_scope.decide
# --------------------------------------------------------------------------
class TheRuleLivesInVerifyScopeTest(unittest.TestCase):
    def test_the_gate_follows_a_patched_decide(self):
        out_of_scope = verify_scope.Decision(in_scope=False, design=False,
                                             ui_role=False, multi=False,
                                             buckets={"backend", "frontend"})
        with mock.patch.object(verify_scope, "decide", return_value=out_of_scope) as d:
            code, fields, stdout = gate(snapshot=card(), files=BACKEND_AND_WEB)
        self.assertEqual(fields.get("decision"), "merge", stdout)
        d.assert_called_once()
        changed, snap = d.call_args.args
        self.assertEqual(sorted(changed), sorted(BACKEND_AND_WEB))
        self.assertEqual(snap["labels"], NON_UI_CARD["labels"])

    def test_the_gate_carries_no_copy_of_the_rule(self):
        source = (SCRIPTS / "merge_gate.py").read_text()
        self.assertIn("import verify_scope", source)
        for copy in ("agent:frontend", "Design:", "build-proxy", "migrations?/",
                     "UI_ROLE_LABELS", "BUCKETS"):
            with self.subTest(copy=copy):
                self.assertNotIn(copy, source)


# --------------------------------------------------------------------------
# 6. the wrapper: one card read, only after the stale-verifier hold
# --------------------------------------------------------------------------
CARD_SNAPSHOT_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CARD_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
snap = os.environ.get("CARD_SNAPSHOT", "")
if sys.argv[1:2] == ["take"] and snap:
    open(sys.argv[3], "w").write(snap)
    raise SystemExit(0)
sys.stderr.write("card-snapshot: no snapshot (stub)\n")
raise SystemExit(1)
'''


def _shadow_pipeline(td: Path) -> None:
    """`.bureau-pipeline` as the real checkout, with linear_ops.py and
    card_snapshot.py swapped for loggers that touch no network."""
    pipeline = td / ".bureau-pipeline"
    pipeline.mkdir()
    for entry in ROOT.iterdir():
        if entry.name != "scripts":
            os.symlink(entry, pipeline / entry.name)
    (pipeline / "scripts").mkdir()
    swapped = {"linear_ops.py": LINEAR_STUB, "card_snapshot.py": CARD_SNAPSHOT_STUB}
    for entry in (ROOT / "scripts").iterdir():
        if entry.name not in swapped:
            os.symlink(entry, pipeline / "scripts" / entry.name)
    for name, text in swapped.items():
        (pipeline / "scripts" / name).write_text(text)


class WrapperRun:
    def __init__(self, proc, calls, card_reads):
        self.proc, self.calls, self.card_reads = proc, calls, card_reads

    def explain(self) -> str:
        return (f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}\n"
                f"card reads: {self.card_reads}")

    def decision(self) -> str:
        """The answer acted on — the last `decision=` the run printed."""
        lines = [ln for ln in self.proc.stdout.splitlines() if ln.startswith("decision=")]
        return lines[-1].split("=", 1)[1] if lines else ""

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]


def run_wrapper(*, branch=CARD_BRANCH, files=SINGLE_BACKEND, snapshot=card(),
                seed=(VERIFIER_PASS_EARLIER, CRITIC_APPROVE)) -> WrapperRun:
    fixture = {"view": view(headRefName=branch, headRefOid=HEAD),
               "check_runs": GREEN_CI, "workflow_runs": [CI_RUN_DONE],
               "compare": compare(files),
               "pr_commits": [{"sha": EARLIER}, {"sha": HEAD}]}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "scratch").mkdir()
        script = td / "evaluate_and_merge.sh"
        script.write_text(SCRIPT.read_text().replace("/tmp/", f"{td / 'scratch'}/"))
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        _shadow_pipeline(td)
        for log in ("gh.log", "linear.log", "card.log"):
            (td / log).write_text("")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps(
            [{"id": i + 1, "user": {"login": QA}, "body": b} for i, b in enumerate(seed)]))
        env = {**os.environ,
               "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
               "PR": "3741", "GH_TOKEN": "qa-token", "REPO_FULL": "dreadnought-foundry/example",
               "WORKFLOW_TOKEN": "workflow-token", "QA_LOGIN": QA,
               "FIXTURE": str(td / "fixture.json"), "COMMENTS": str(td / "comments.json"),
               "GH_LOG": str(td / "gh.log"), "LINEAR_LOG": str(td / "linear.log"),
               "CARD_LOG": str(td / "card.log"),
               "CARD_SNAPSHOT": json.dumps(snapshot) if snapshot is not None else "",
               "MERGE_RETRY_SECONDS": "0", "READ_RETRY_SECONDS": "0",
               "FIX_LANE_RECHECK_SECONDS": "0", "FIX_LANE_RECHECK_CEILING_SECONDS": "0"}
        env.pop("LINEAR_API_KEY", None)
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own script
            ["bash", str(script)], cwd=td, capture_output=True, text=True, env=env)
        read = lambda p: [json.loads(ln) for ln in p.read_text().splitlines() if ln]  # noqa: E731
        return WrapperRun(proc, read(td / "gh.log"), read(td / "card.log"))


class TheWrapperReadsTheCardOnceTest(unittest.TestCase):
    def test_the_3741_shape_reads_the_card_once_and_merges(self):
        run = run_wrapper()
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.card_reads, [["take", CARD, run.card_reads[0][2]]],
                         run.explain())
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(len(run.merges), 1, run.explain())
        self.assertIn("verifier does not gate head", run.proc.stdout)

    def test_an_in_scope_head_reads_the_card_once_and_holds(self):
        run = run_wrapper(files=BACKEND_AND_WEB)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.card_reads), 1, run.explain())
        self.assertEqual(run.decision(), "hold", run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_a_failed_card_read_holds_and_the_step_stays_green(self):
        run = run_wrapper(snapshot=None)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.card_reads), 1, run.explain())
        self.assertEqual(run.decision(), "hold", run.explain())
        self.assertIn(f"no snapshot of {CARD}", run.proc.stdout)
        self.assertEqual(run.merges, [], run.explain())

    def test_no_verifier_verdict_reads_no_card(self):
        run = run_wrapper(seed=(CRITIC_APPROVE,))
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(run.card_reads, [], run.explain())

    def test_a_verdict_bound_to_the_head_reads_no_card(self):
        run = run_wrapper(seed=(f"🧪 QA Verifier — VERDICT: PASS @{HEAD}", CRITIC_APPROVE))
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(run.card_reads, [], run.explain())

    def test_a_branch_naming_no_card_reads_no_card_and_merges(self):
        run = run_wrapper(branch=NO_CARD_BRANCH)
        self.assertEqual(run.card_reads, [], run.explain())
        self.assertEqual(run.decision(), "merge", run.explain())


if __name__ == "__main__":
    unittest.main()
