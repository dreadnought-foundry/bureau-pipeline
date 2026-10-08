"""RED-first tests: a draft pull request is not reviewed, not fixed, and never parks its card (DRE-5801).

THE BUG (2026-10-04, found during the lane cleanup). Draft pull requests that
a person was still building by hand drew a critic REQUEST_CHANGES. The verdict
started the fix loop, the fix agent disagreed with the finding, and the Report
step parked the card in Triage with `needs-human` and an Operator-decision ask.
Seen on agent-bureau #3134 (DRE-5600), #3142 (DRE-5602), #3139 (DRE-5775) and
bureau-pipeline #714 (DRE-4973) and #713 (DRE-5412); each card had to be moved
back to Hand-work by hand.

The rest of the pipeline already reads a draft as work in progress: the merge
gate never merges one (DRE-3467), the reconcile sweeps skip them, and the
hand-built sweep leaves a draft's card where it is (DRE-4356). The critic and
the fix loop were the two that did not.

FIX UNDER TEST, one property per section:

  1. `should_review_pr.py` says skip for a draft — on a pull_request event and
     on a dispatched re-review alike — and an unreadable draft flag reviews,
     the step's fail-soft direction.
  2. qa-review.yml's Decide review step, executed, hands the script the PR
     record's own `isDraft`, so a draft ends review=false.
  3. Marking the PR ready starts the normal review: the self-host stub fires
     on `ready_for_review`, and the review job's lane step moves the card on
     that event and never on a draft.
  4. The fix loop does no fix-mode work on a draft (`resolve_fix_pr.sh`), so a
     standing verdict from before the PR went back to draft starts nothing. A
     conflicted draft still gets its conflict round (DRE-3467's rule, kept).
  5. The fix path never parks a draft's card (`report_fix_result.sh`): no
     `needs-human`, no Triage, whatever the round ended as.

Run: python3 -m pytest tests/test_draft_pr_no_review_no_park.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import should_review_pr  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    CARD,
    POST_SHA,
    _checkout,
    _open_handoff,
    _report_env,
)
from test_hand_dispatch_no_work import ATTEMPT, GH_STUB, WORKER, rest  # noqa: E402

QA = "agent-bureau-qa-bot[bot]"
VERDICT = "🔎 QA Critic — VERDICT: REQUEST_CHANGES cause:defect @" + "d" * 40


def _job(wf: str, job: str) -> dict:
    return yaml.safe_load((WORKFLOWS / wf).read_text())["jobs"][job]


def _step(wf: str, job: str, step_id: str) -> dict:
    return next(s for s in _job(wf, job)["steps"] if s.get("id") == step_id)


def _outputs(path: Path) -> dict:
    return dict(
        line.partition("=")[::2]
        for line in path.read_text().splitlines() if "=" in line
    )


# --------------------------------------------------------------------------
# 1: should_review_pr.py — a draft is not reviewed
# --------------------------------------------------------------------------
def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "should_review_pr.py"), *args],
        capture_output=True, text=True, timeout=60,
    )


class ADraftIsNotReviewedTest(unittest.TestCase):

    def test_the_function_skips_a_draft(self):
        self.assertFalse(
            should_review_pr.should_review("agent/DRE-5600-x", is_draft=True))

    def test_the_function_still_reviews_a_ready_pull_request(self):
        self.assertTrue(
            should_review_pr.should_review("agent/DRE-5600-x", is_draft=False))

    def test_the_cli_skips_a_draft_and_says_why(self):
        proc = _cli("agent/DRE-5600-x", "--is-draft", "true")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("review=false", proc.stdout.splitlines())
        self.assertIn("draft=true", proc.stdout.splitlines())
        self.assertIn("ready for review", proc.stdout)
        # A draft skip is not a carried verdict: nothing may re-publish an
        # APPROVE check for it.
        self.assertFalse(
            [line for line in proc.stdout.splitlines()
             if line.startswith("carried_sha=") and line != "carried_sha="])

    def test_the_cli_reviews_a_ready_pull_request(self):
        proc = _cli("agent/DRE-5600-x", "--is-draft", "false")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("review=true", proc.stdout.splitlines())

    def test_an_unreadable_draft_flag_reviews(self):
        """The step decides whether to SPEND a review, and a blip degrades to
        reviewing — the cheap mistake — never to a silent skip."""
        for raw in ("", "null", "maybe"):
            proc = _cli("agent/DRE-5600-x", "--is-draft", raw)
            self.assertEqual(proc.returncode, 0, f"{raw!r}: {proc.stdout}")
            self.assertIn("review=true", proc.stdout.splitlines(), raw)

    def test_a_requested_review_skips_a_draft(self):
        """The dispatched re-review (the refutation re-review, a sweep, a
        hand dispatch) is the second way a critic reaches a draft."""
        proc = _cli("--requested", "--is-draft", "true")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("review=false", proc.stdout.splitlines())

    def test_a_requested_review_of_a_ready_pull_request_runs(self):
        proc = _cli("--requested", "--is-draft", "false")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("review=true", proc.stdout.splitlines())


# --------------------------------------------------------------------------
# 2: the Decide review step, executed, reads the record's own isDraft
# --------------------------------------------------------------------------
DECIDE_GH_STUB = r"""#!/bin/sh
case "$*" in
  "pr view"*) cat "$GH_PR" ;;
  *"/compare/"*) printf '{"files": []}\n' ;;
  *"/comments"*) printf '[[]]\n' ;;
  *"/commits?"*) printf '[]\n' ;;
  *) printf '{}\n' ;;
esac
"""


def _run_decide(event: str, is_draft: bool) -> dict:
    run = _step("qa-review.yml", "review", "decide")["run"]
    run = run.replace("${{ github.repository }}", "dreadnought-foundry/agent-bureau")
    assert "${{" not in run, "an expression this test does not expand"
    record = {"headRefName": "agent/DRE-5600-x", "headRefOid": "a" * 40,
              "baseRefName": "main", "changedFiles": 1, "additions": 1,
              "deletions": 0, "isDraft": is_draft}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        gh = td / "bin" / "gh"
        gh.write_text(DECIDE_GH_STUB)
        gh.chmod(0o755)
        (td / "pr.json").write_text(json.dumps(record))
        temp = td / "runner-temp"
        temp.mkdir()
        out = td / "github-output"
        out.write_text("")
        env = {
            "PATH": f"{td / 'bin'}:{os.environ['PATH']}", "HOME": raw,
            "GH_PR": str(td / "pr.json"), "RUNNER_TEMP": str(temp),
            "GITHUB_OUTPUT": str(out), "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "dreadnought-foundry/agent-bureau",
            "PIPELINE_DIR": str(ROOT), "GH_TOKEN": "t", "PR": "3134",
            "EVENT": event, "HEAD_REF": "agent/DRE-5600-x" if event == "pull_request" else "",
            "QA_LOGIN": QA,
            "PR_RECORD_FIELDS": _job("qa-review.yml", "review")["env"]["PR_RECORD_FIELDS"],
        }
        proc = subprocess.run(["bash", "-e", "-c", run], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        return _outputs(out)


class TheDecideStepReadsTheDraftFlagTest(unittest.TestCase):

    def test_the_record_carries_the_draft_flag(self):
        fields = _job("qa-review.yml", "review")["env"]["PR_RECORD_FIELDS"].split(",")
        self.assertIn("isDraft", fields)

    def test_a_draft_opened_is_not_reviewed(self):
        self.assertEqual(_run_decide("pull_request", True).get("review"), "false")

    def test_a_ready_pull_request_is_reviewed(self):
        self.assertEqual(_run_decide("pull_request", False).get("review"), "true")

    def test_a_dispatched_review_of_a_draft_is_not_run(self):
        self.assertEqual(_run_decide("workflow_dispatch", True).get("review"), "false")

    def test_a_dispatched_review_of_a_ready_pull_request_runs(self):
        self.assertEqual(_run_decide("workflow_dispatch", False).get("review"), "true")


# --------------------------------------------------------------------------
# 3: marking the PR ready starts the normal review
# --------------------------------------------------------------------------
class MarkingItReadyStartsTheReviewTest(unittest.TestCase):

    def test_the_self_host_stub_fires_on_ready_for_review(self):
        doc = yaml.safe_load((WORKFLOWS / "pr-review.yml").read_text())
        # PyYAML reads the bare `on:` key as the boolean True.
        on = doc.get("on", doc.get(True))
        types = on["pull_request"]["types"]
        for event in ("opened", "reopened", "synchronize", "ready_for_review"):
            self.assertIn(event, types)

    def test_the_lane_step_moves_the_card_when_the_pull_request_is_marked_ready(self):
        self.assertIn("'ready_for_review'", _step("qa-review.yml", "review", "lane")["if"])

    def test_the_lane_step_never_moves_a_drafts_card(self):
        """A draft is not "being checked" — the evidence `In Review` asks for
        is not there yet (the hand-built sweep draws the same line)."""
        condition = _step("qa-review.yml", "review", "lane")["if"]
        self.assertIn("!github.event.pull_request.draft", condition.replace(" ", ""))


# --------------------------------------------------------------------------
# 4: the fix loop does no fix-mode work on a draft
# --------------------------------------------------------------------------
def _resolve(merge_state: str, is_draft: bool, thread: list) -> tuple[dict, str]:
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        (td / ".bureau-pipeline").symlink_to(ROOT)
        info = td / "pr-info.json"
        info.write_text(json.dumps({
            "state": "OPEN", "headRefName": "agent/DRE-5600-hand-built",
            "headRefOid": "d" * 40, "mergeStateStatus": merge_state,
            "baseRefName": "main", "isDraft": is_draft,
        }))
        comments = td / "comments.json"
        comments.write_text(json.dumps(thread))
        out_file = td / "step-output"
        out_file.write_text("")
        log = td / "gh-writes.jsonl"
        proc = subprocess.run(
            ["bash", str(SCRIPTS / "resolve_fix_pr.sh")], cwd=td,
            env=dict(
                os.environ,
                PATH=f"{td}/bin:{os.environ['PATH']}",
                GITHUB_OUTPUT=str(out_file), RUNNER_TEMP=str(td),
                GH_PR_INFO=str(info), GH_COMMENTS=str(comments), GH_LOG=str(log),
                GH_TOKEN="test", WRITER_TOKEN="test-writer",
                WORKER_LOGIN=WORKER, EVENT_NAME="issue_comment",
                TRIGGERING_ACTOR=QA, PR_NUMBER="3134",
                REPO="dreadnought-foundry/agent-bureau",
            ),
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        posted = log.read_text() if log.exists() else ""
        return _outputs(out_file), posted


class TheFixLoopLeavesADraftAloneTest(unittest.TestCase):

    def test_a_verdict_on_a_draft_starts_no_fix_agent(self):
        outputs, posted = _resolve("CLEAN", True, [rest(QA, VERDICT)])
        self.assertEqual(outputs.get("go"), "false")
        self.assertEqual(outputs.get("no_work"), "true")
        self.assertNotEqual(outputs.get("held"), "true")
        self.assertEqual(posted, "", "nothing is posted on a person's draft")

    def test_the_same_verdict_on_a_ready_pull_request_starts_one(self):
        """The control: the draft flag is the only thing that changed."""
        outputs, _ = _resolve("CLEAN", False, [rest(QA, VERDICT)])
        self.assertEqual(outputs.get("go"), "true")
        self.assertEqual(outputs.get("mode"), "fix")

    def test_an_outstanding_verdict_on_a_conflicted_draft_starts_nothing(self):
        """DIRTY with an unanswered REQUEST_CHANGES reads as fix mode — the
        verdict, not the conflict — and a draft gets no fix-mode run."""
        outputs, _ = _resolve("DIRTY", True, [rest(QA, VERDICT)])
        self.assertEqual(outputs.get("go"), "false")

    def test_a_conflicted_draft_still_gets_its_conflict_round(self):
        """DRE-3467 kept: the branch has to be reconciled with its base
        whatever the flag says, and that is not the review's fix loop."""
        outputs, _ = _resolve("DIRTY", True, [rest(WORKER, ATTEMPT.format(n=1))])
        self.assertEqual(outputs.get("mode"), "conflict")
        self.assertEqual(outputs.get("go"), "true")


# --------------------------------------------------------------------------
# 5: the fix path never parks a draft's card
# --------------------------------------------------------------------------
def _report_gh_stub(td: str, log: str, is_draft: bool) -> str:
    """`gh` answering the Report step's reads: the draft flag, an OPEN state,
    an unmoved head — and recording every comment."""
    binary = os.path.join(td, "bin")
    os.makedirs(binary, exist_ok=True)
    path = os.path.join(binary, "gh")
    with open(path, "w") as fh:
        fh.write(f"""#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
if argv[:2] == ["pr", "view"]:
    fields = argv[argv.index("--json") + 1] if "--json" in argv else ""
    if fields == "isDraft":
        print({json.dumps("true" if is_draft else "false")})
    elif fields == "state":
        print("OPEN")
    else:
        print("{POST_SHA}")
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    open({log!r}, "a").write(json.dumps(argv) + "\\n")
    sys.exit(0)
if argv[:1] == ["api"]:
    print("[]")
    sys.exit(0)
sys.exit(0)
""")
    os.chmod(path, 0o755)
    return binary


def _report_blocked_round(is_draft: bool) -> list[list[str]]:
    """The 10-04 shape: the fix agent disputes the finding and pushes
    nothing. Returns every linear_ops.py and hold.py call the Report step
    made."""
    with tempfile.TemporaryDirectory() as td:
        base = _checkout(td)
        linear_log = os.path.join(td, "linear.jsonl")
        with open(os.path.join(base, "scripts", "linear_ops.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
        # DRE-6179: the hold goes on through the registry's writer, logged
        # into the same file so it reads in order with the lane move.
        with open(os.path.join(base, "scripts", "hold.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(['hold.py'] + sys.argv[1:]) + '\\n')\n")
        binary = _report_gh_stub(td, os.path.join(td, "comments.jsonl"), is_draft)
        _open_handoff(td, blocked=True)
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="test", **_report_env(td, "fix"))
        proc = subprocess.run(["bash", str(SCRIPTS / "report_fix_result.sh")],
                              cwd=td, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        if not os.path.exists(linear_log):
            return []
        with open(linear_log) as fh:
            return [json.loads(line) for line in fh.read().splitlines()]


def _parks(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls
            if (c[:1] == ["add-label"] and "needs-human" in c)
            or c[:2] == ["hold.py", "apply"]
            or (c[:1] in (["advance"], ["state"]) and "Triage" in c)]


class TheFixPathNeverParksADraftTest(unittest.TestCase):

    def test_a_disputed_round_on_a_draft_parks_nothing(self):
        self.assertEqual(_parks(_report_blocked_round(is_draft=True)), [])

    def test_the_same_round_on_a_ready_pull_request_parks_the_card(self):
        """The control: a ready PR's dispute still goes to a person."""
        parks = _parks(_report_blocked_round(is_draft=False))
        self.assertTrue([c for c in parks if c[:3] == ["hold.py", "apply", CARD]])
        self.assertTrue([c for c in parks if c[0] == "advance" and "Triage" in c])


if __name__ == "__main__":
    unittest.main()
