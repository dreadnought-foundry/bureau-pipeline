"""Red-main repair — the third way to end: the fault is already gone (DRE-5700).

Before this the repair agent had two sanctioned endings, a pull request or an
escalation note, and the Report step failed every other run on purpose so the
medic would look at it. On 2026-10-03 repair run 37092915226 decided to
dispatch at 03:21 UTC, before any later run on main was green, so the
superseded rule (DRE-5069) correctly did not fire. Attempt 2's agent finished
cleanly after 42 turns, found the red already fixed on main by DRE-5680, and
opened nothing — the right call. The Report step ran at 04:00:55Z, 41 seconds
after main went green on 44eb259, and failed the run with "repair agent
produced neither a PR nor an escalation": a medic retry, a failure card and a
death receipt for a run that had done its job.

Now the agent writes one line naming the fix to `/tmp/repair-superseded.txt`
and stops, and the Report step does not take its word for it: it re-gathers
the run listing (`repair_history.py gather`) and asks the SAME rule the decide
step asks (`red_main_repair.superseding_run`) through the `superseded`
subcommand. A later green run on another commit ends the job green; anything
else — no such run, or a listing nobody could read — fails exactly as before.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import red_main_repair  # noqa: E402
import repair_history  # noqa: E402
import step_shell  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "red-main-repair.yml")
ADR = os.path.join(ROOT, "architecture", "decisions",
                   "adr-red-main-auto-repair.md")

# 2026-10-03, from the run records. CI run 37091531249 on 8671bf7 hit the
# Test step's cap at 03:21:08Z; DRE-5680 raised the caps and merged as 44eb259,
# whose run 37093287722 went green at 04:00:14Z. Only short shas are in the
# record, so the full ones are padded.
FAILED_SHA = "8671bf7" + "0" * 33
FAILED_RUN = 37091531249
FAILED_CREATED = "2026-10-03T02:58:00Z"
FIX_SHA = "44eb259" + "1" * 33
GREEN_RUN = 37093287722
GREEN_CREATED = "2026-10-03T03:28:10Z"
CLAIM = "the red was already fixed on main by DRE-5680 (PR #897), 44eb259"


def _branch_run(*, run_id=GREEN_RUN, head_sha=FIX_SHA,
                created_at=GREEN_CREATED, conclusion="success", event="push"):
    return {"run_id": run_id, "head_sha": head_sha, "created_at": created_at,
            "status": "completed", "conclusion": conclusion, "event": event}


def _history(*branch_runs):
    return {
        "current": {"run_id": FAILED_RUN, "run_attempt": 1,
                    "workflow_path": ".github/workflows/ci.yml",
                    "head_sha": FAILED_SHA, "created_at": FAILED_CREATED,
                    "log": "", "jobs": []},
        "prior": [],
        "branch_runs": list(branch_runs),
    }


def _cli(text):
    """Run `red_main_repair.py superseded` on a history file holding `text`
    (None: no file at all)."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "history.json")
        if text is not None:
            with open(path, "w") as fh:
                fh.write(text)
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "red_main_repair.py"),
             "superseded", "--history-file", path],
            capture_output=True, text=True, check=False)


# --------------------------------------------------------------------------- #
# The subcommand — the decision lives in the script, never in shell            #
# --------------------------------------------------------------------------- #

class SupersededSubcommandTest(unittest.TestCase):

    def test_a_later_green_run_on_another_commit_prints_its_id(self):
        done = _cli(json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), str(GREEN_RUN))

    def test_no_green_run_exits_non_zero(self):
        done = _cli(json.dumps(_history(
            _branch_run(conclusion="failure"),
            _branch_run(run_id=GREEN_RUN + 1, conclusion="cancelled"))))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_no_runs_at_all_exits_non_zero(self):
        done = _cli(json.dumps(_history()))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_a_green_run_on_the_failed_commit_exits_non_zero(self):
        # A re-run of the failed commit going green proves a flake, not that
        # main has moved past the fault on a later commit.
        done = _cli(json.dumps(_history(_branch_run(head_sha=FAILED_SHA))))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_a_green_run_created_before_the_failed_run_exits_non_zero(self):
        # Green before the failure is the commit the fault landed on top of.
        done = _cli(json.dumps(_history(
            _branch_run(created_at="2026-10-03T02:10:00Z"))))
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_the_gathers_failed_marker_exits_non_zero(self):
        # Unreadable is never green.
        done = _cli(repair_history.FETCH_FAILED + "\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_malformed_json_exits_non_zero(self):
        done = _cli("{not json")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_a_missing_history_file_exits_non_zero(self):
        done = _cli(None)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), "")

    def test_the_reason_is_said_on_stderr(self):
        done = _cli(json.dumps(_history()))
        self.assertIn("no later green run", done.stderr)

    def test_the_subcommand_asks_the_one_rule(self):
        # One copy of "later and green": the subcommand answers whatever
        # `superseding_run` answers, for both outcomes, on the same document.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "history.json")
            with open(path, "w") as fh:
                json.dump(_history(), fh)
            seen = []

            def rule(history):
                seen.append(history)
                return "4242"

            with mock.patch.object(red_main_repair, "superseding_run", rule), \
                    mock.patch("sys.stdout") as out:
                code = red_main_repair.main(
                    ["superseded", "--history-file", path])
            self.assertEqual(code, 0)
            self.assertEqual(seen, [_history()])
            self.assertIn("4242", "".join(
                c.args[0] for c in out.write.call_args_list))

            with open(path, "w") as fh:
                json.dump(_history(_branch_run()), fh)
            with mock.patch.object(red_main_repair, "superseding_run",
                                   lambda history: ""):
                self.assertNotEqual(red_main_repair.main(
                    ["superseded", "--history-file", path]), 0)


# --------------------------------------------------------------------------- #
# The Report step's own script, run in bash with every helper stubbed but the  #
# decision itself                                                              #
# --------------------------------------------------------------------------- #

_RECORDER = """\
import os, sys
open(os.environ["CALLS"], "a").write({name!r} + " " + " ".join(sys.argv[1:]) + "\\n")
"""

_CARD_PR_STUB = _RECORDER.format(name="card_pr")

# No branch was pushed, so the finish helper has nothing to open.
_FINISH_STUB = _RECORDER.format(name="repair_finish") + """\
sys.stdout.write("finish=no-branch\\npr_url=\\ncard=DRE-5696\\n")
sys.exit(2)
"""

_LINEAR_STUB = _RECORDER.format(name="linear_ops")

# The gather writes whatever the test put in FAKE_HISTORY to its --out, and
# records the token it was handed.
_HISTORY_STUB = _RECORDER.format(name="repair_history") + """\
open(os.environ["CALLS"], "a").write("history-token " + os.environ.get("GH_TOKEN", "") + "\\n")
out = sys.argv[sys.argv.index("--out") + 1]
open(out, "w").write(os.environ["FAKE_HISTORY"])
"""

# The real decision, run from the real scripts directory.
_DECISION_SHIM = """\
import os, runpy, sys
open(os.environ["CALLS"], "a").write("red_main_repair " + " ".join(sys.argv[1:]) + "\\n")
sys.path.insert(0, {scripts!r})
sys.argv[0] = os.path.join({scripts!r}, "red_main_repair.py")
runpy.run_path(sys.argv[0], run_name="__main__")
"""

WORKER_TOKEN = "worker-token-not-for-actions-reads"
HISTORY_TOKEN = "github-token-with-actions-read"


def report_step() -> dict:
    with open(WORKFLOW) as fh:
        steps = yaml.safe_load(fh)["jobs"]["repair"]["steps"]
    return next(s for s in steps if s.get("name") == "Report")


class ReportSupersededExitTest(unittest.TestCase):
    """The step's `run:` body, executed as GitHub executes it (`bash -e`)."""

    def run_step(self, *, claim, history):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, ".bureau-pipeline", "scripts")
            os.makedirs(scripts)
            for name, text in (
                    ("card_pr.py", _CARD_PR_STUB),
                    ("repair_finish.py", _FINISH_STUB),
                    ("linear_ops.py", _LINEAR_STUB),
                    ("repair_history.py", _HISTORY_STUB),
                    ("red_main_repair.py",
                     _DECISION_SHIM.format(scripts=os.path.abspath(SCRIPTS)))):
                with open(os.path.join(scripts, name), "w") as fh:
                    fh.write(text)
            # Every absolute /tmp path the step reads or writes is pointed into
            # the sandbox, so a stray file on this machine cannot steer it.
            body = step_shell.step_shell(report_step())
            for name in ("repair-escalation.txt", "repair-superseded.txt",
                         "repair-history-report.json"):
                body = body.replace(f"/tmp/{name}", os.path.join(tmp, name))
            if claim is not None:
                with open(os.path.join(tmp, "repair-superseded.txt"), "w") as fh:
                    fh.write(claim)
            calls = os.path.join(tmp, "calls.txt")
            summary = os.path.join(tmp, "summary.md")
            open(calls, "w").close()
            open(summary, "w").close()
            env = {
                **os.environ,
                "CALLS": calls,
                "FAKE_HISTORY": history,
                "GH_TOKEN": WORKER_TOKEN,
                "HISTORY_GH_TOKEN": HISTORY_TOKEN,
                "BRANCH": f"repair/DRE-5696-{FAILED_SHA[:12]}",
                "RUN_URL": f"https://github.com/x/portico/actions/runs/{FAILED_RUN}",
                "WF_NAME": "CI",
                "CARD_URL": "https://linear.app/x/issue/DRE-5696",
                "DEFAULT_BRANCH": "main",
                "GITHUB_REPOSITORY": "dreadnought-foundry/portico",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_STEP_SUMMARY": summary,
                "WORKFLOW_ID": "4242",
                "RUN_ID": str(FAILED_RUN),
                "RUN_ATTEMPT": "1",
                "RUN_CREATED_AT": FAILED_CREATED,
                "WORKFLOW_PATH": ".github/workflows/ci.yml",
                "HEAD_SHA": FAILED_SHA,
            }
            done = subprocess.run(["bash", "-e", "-c", body], cwd=tmp, env=env,
                                  capture_output=True, text=True, check=False)
            with open(calls) as fh, open(summary) as sh:
                return done, fh.read(), sh.read()

    # -- verified ----------------------------------------------------------- #

    def test_a_verified_claim_ends_the_job_green(self):
        done, calls, summary = self.run_step(
            claim=CLAIM + "\n", history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn("::error::", done.stdout)
        lines = [ln for ln in done.stdout.splitlines()
                 if ln.startswith("superseded:")]
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertIn(str(GREEN_RUN), lines[0])
        self.assertIn(CLAIM, lines[0])
        self.assertIn(str(GREEN_RUN), summary)
        self.assertIn(CLAIM, summary)

    def test_a_verified_claim_writes_nothing_to_linear_or_github(self):
        # No PR, no card, no comment: the repair card is `settle`'s to close.
        done, calls, _ = self.run_step(
            claim=CLAIM, history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn("linear_ops", calls)
        self.assertNotIn("gh pr create", done.stdout + done.stderr)

    def test_the_record_is_re_gathered_on_the_actions_read_token(self):
        done, calls, _ = self.run_step(
            claim=CLAIM, history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        gather = [ln for ln in calls.splitlines()
                  if ln.startswith("repair_history ")]
        self.assertEqual(len(gather), 1, calls)
        for flag in ("gather", "--repo dreadnought-foundry/portico",
                     "--workflow-id 4242", f"--run-id {FAILED_RUN}",
                     "--run-attempt 1", f"--run-created-at {FAILED_CREATED}",
                     "--branch main",
                     "--workflow-path .github/workflows/ci.yml",
                     f"--head-sha {FAILED_SHA}"):
            self.assertIn(flag, gather[0])
        self.assertIn(f"history-token {HISTORY_TOKEN}", calls)
        self.assertIn("red_main_repair superseded", calls)

    def test_the_agents_line_cannot_issue_a_workflow_command(self):
        # The note is the agent's text; printed after a prefix, on one line,
        # it can never start a `::` command line.
        done, _, _ = self.run_step(
            claim="::error::forged\n::set-output name=x::y\n",
            history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        for line in done.stdout.splitlines():
            self.assertFalse(line.startswith("::"), line)
        self.assertNotIn("set-output", done.stdout)

    # -- not verified ------------------------------------------------------- #

    UNVERIFIED = "::error::the repair agent claimed the fault was superseded"

    def test_no_later_green_run_fails_with_the_claim_named(self):
        done, calls, _ = self.run_step(
            claim=CLAIM, history=json.dumps(_history(
                _branch_run(conclusion="failure"))))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(self.UNVERIFIED, done.stdout)
        self.assertIn("no later green run", done.stdout)
        self.assertNotIn("superseded: run", done.stdout)
        self.assertNotIn("linear_ops", calls)

    def test_a_green_run_on_the_failed_commit_still_fails(self):
        done, _, _ = self.run_step(
            claim=CLAIM, history=json.dumps(_history(
                _branch_run(head_sha=FAILED_SHA))))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(self.UNVERIFIED, done.stdout)

    def test_a_history_the_gather_could_not_read_fails(self):
        done, _, _ = self.run_step(
            claim=CLAIM, history=repair_history.FETCH_FAILED + "\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(self.UNVERIFIED, done.stdout)

    # -- no claim: today's failure, unchanged ------------------------------- #

    DEAD = ("::error::repair agent produced neither a PR nor an escalation — "
            "failing for medic visibility")

    def test_no_note_fails_as_before_and_gathers_nothing(self):
        done, calls, _ = self.run_step(
            claim=None, history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 1)
        self.assertIn(self.DEAD, done.stdout)
        self.assertNotIn("repair_history", calls)

    def test_an_empty_note_is_no_note(self):
        done, calls, _ = self.run_step(
            claim="", history=json.dumps(_history(_branch_run())))
        self.assertEqual(done.returncode, 1)
        self.assertIn(self.DEAD, done.stdout)
        self.assertNotIn("repair_history", calls)


# --------------------------------------------------------------------------- #
# The record                                                                   #
# --------------------------------------------------------------------------- #

class AdrTest(unittest.TestCase):

    def test_the_adr_says_the_rule_is_asked_twice(self):
        with open(ADR) as fh:
            text = fh.read()
        start = text.index("(DRE-5069).**")
        end = text.index("- **Bounded attempts", start)
        bullet = text[start:end]
        for fact in ("DRE-5700", "/tmp/repair-superseded.txt",
                     "Report step", "superseding_run"):
            self.assertIn(fact, bullet)


if __name__ == "__main__":
    unittest.main()
