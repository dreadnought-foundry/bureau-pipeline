"""RED-first tests: a fix run whose machine was taken away is retried once, never escalated (DRE-6572).

THE INCIDENT (2026-10-09, PT). Run 38025638252, attempt 1, on agent-bureau
#3502 at head a3fa0c44. Twelve minutes into the fix run the cloud provider
reclaimed the Spot machine. The job's annotations said so — "AWS interrupted
the EC2 Spot instance running this job. RunsOn can retry it after the workflow
run finishes." and "Process completed with exit code 137." — the agent step
ended `failure`, and no result file reached the Report step. The Report read
"no result" as "ran and changed nothing", posted "🛑 Fix attempt 1 pushed no new
commit … Escalating to a human" and moved DRE-6556 to Triage. A second fix run
on the same commit finished fifteen minutes later and said no person was needed.

FIX UNDER TEST. "No result record, the agent step did not finish, and the job
says the machine was taken" is its own answer in `fix_dead_run.decide`: a
`fix-run-runner-lost @<sha8>` marker, one retry per head through the reconcile
sweep (after RUNNER_LOST_WAIT_MINUTES, so RunsOn's own retry gets there first),
and a 🛑 hold for a person on the second lost machine in a row on the same head.
Anything short of all three facts takes today's path, unchanged.

The harnesses are the ones tests/test_report_fix_result.py and
tests/test_reconcile_retries_dead_fix_runs.py already import or define.

Run: python3 -m pytest tests/test_fix_runner_lost.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SCRIPT = os.path.join(SCRIPTS, "report_fix_result.sh")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "agent-fix.yml")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# reconcile reads these at import, as tests/test_reconcile_retries_dead_fix_runs.py sets them.
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/test")
os.environ.setdefault("GH_TOKEN", "test")

import check_act_receipts  # noqa: E402
import fix_convergence  # noqa: E402
import fix_dead_run  # noqa: E402
import fix_handoff  # noqa: E402
import pipeline_act  # noqa: E402
import reconcile  # noqa: E402
from test_act_emission_scenario import (  # noqa: E402
    CARD,
    PR,
    REPO,
    _checkout,
    _report_env,
)
from test_reconcile_retries_dead_fix_runs import sweep  # noqa: E402

WORKER = "agent-bureau-bot[bot]"
QA = "agent-bureau-qa-bot[bot]"
HEAD = "a3fa0c44d7cad59023d1bc6f59b45da5041cba11"
OTHER_HEAD = "b" * 40
RUN_URL = f"https://github.com/{REPO}/actions/runs/38025638252"
RUNNER = "runs-on--i-0f3c9a1b2c3d4e5f6--fix"
JOB_ID = 114135785854

SPOT = ("AWS interrupted the EC2 Spot instance running this job. RunsOn can "
        "retry it after the workflow run finishes.")
EXIT_137 = "Process completed with exit code 137."
LOST_COMMS = ("The self-hosted runner: runs-on-1 lost communication with the "
              "server. Verify the machine is running and has a healthy network "
              "connection.")
SHUTDOWN = ("The runner has received a shutdown signal. This can happen when "
            "the runner service is stopped, or a manually started runner is "
            "canceled.")

RETRY = "retry-runner-lost"
HOLD = "hold-runner-lost"


def annotation(message: str, level: str = "failure") -> dict:
    """One Checks API annotation, in the shape the endpoint answers."""
    return {"path": ".github", "start_line": 1, "end_line": 1,
            "annotation_level": level, "title": "", "message": message,
            "raw_details": None}


#: The job's annotations exactly as quoted on the card, job 114135785854.
TONIGHT = [annotation(SPOT, "warning"), annotation(EXIT_137)]


def rest(login: str, body: str) -> dict:
    return {"user": {"login": login,
                     "type": "Bot" if login.endswith("[bot]") else "User"},
            "body": body, "created_at": "2026-10-10T05:07:55Z"}


def decide(execution=None, outcome="failure", annotations=TONIGHT,
           prior_lost=0, head=HEAD, **kw):
    return fix_dead_run.decide(
        execution, 0, run_url=RUN_URL, agent_step_outcome=outcome,
        annotations=annotations, head=head, prior_lost=prior_lost, **kw)


# --------------------------------------------------------------------------
# 1: the decision, pure
# --------------------------------------------------------------------------
class ALostMachineIsRetriedTest(unittest.TestCase):

    def test_each_wording_is_a_lost_machine(self):
        for message in (SPOT, LOST_COMMS, SHUTDOWN):
            with self.subTest(message=message):
                d = decide(annotations=[annotation(message)])
                self.assertEqual(RETRY, d.action)
                self.assertIn(fix_dead_run.RUNNER_LOST_TAG, d.comment)

    def test_the_wordings_are_the_cards_three(self):
        self.assertEqual(
            ("AWS interrupted the EC2 Spot instance running this job",
             "lost communication with the server",
             "The runner has received a shutdown signal"),
            fix_dead_run.RUNNER_LOST_WORDINGS)

    def test_the_match_ignores_case(self):
        d = decide(annotations=[annotation(SHUTDOWN.upper())])
        self.assertEqual(RETRY, d.action)

    def test_a_cancelled_agent_step_qualifies(self):
        self.assertEqual(RETRY, decide(outcome="cancelled").action)

    def test_a_missing_empty_or_unreadable_record_is_no_record(self):
        with tempfile.TemporaryDirectory() as td:
            empty = os.path.join(td, "empty.json")
            open(empty, "w").close()
            broken = os.path.join(td, "broken.json")
            with open(broken, "w") as fh:
                fh.write('{"type": "result", "is_err')
            for path in (os.path.join(td, "missing.json"), empty, broken):
                with self.subTest(path=os.path.basename(path)):
                    execution = fix_dead_run.check_agent_result._load_execution(path)
                    self.assertEqual(RETRY, decide(execution).action)

    def test_the_retry_line_opens_with_the_tag_and_the_head(self):
        d = decide()
        first = d.comment.splitlines()[0]
        self.assertTrue(first.startswith(
            f"⚡ {fix_dead_run.RUNNER_LOST_TAG} @{HEAD[:8]}:"), first)

    def test_the_retry_line_quotes_the_annotation(self):
        d = decide()
        self.assertIn(
            '"AWS interrupted the EC2 Spot instance running this job."',
            d.comment)
        self.assertNotIn("RunsOn can retry", d.comment)
        self.assertIn("not a failed fix", d.comment)
        self.assertIn("lost machine 1/2", d.comment)
        self.assertIn(f"Run: {RUN_URL}", d.comment)


class AnythingShortOfAllThreeEscalatesTest(unittest.TestCase):

    def test_no_wording_escalates(self):
        d = decide(annotations=[annotation("Something else went wrong.")])
        self.assertEqual(fix_dead_run.Decision("escalate", ""), d)

    def test_exit_137_alone_escalates(self):
        """It is also what a memory kill looks like (scripts/out_of_memory.py)."""
        d = decide(annotations=[annotation(EXIT_137)])
        self.assertEqual(fix_dead_run.Decision("escalate", ""), d)

    def test_unreadable_annotations_escalate(self):
        self.assertEqual(fix_dead_run.Decision("escalate", ""),
                         decide(annotations=None))

    def test_empty_annotations_escalate(self):
        self.assertEqual(fix_dead_run.Decision("escalate", ""),
                         decide(annotations=[]))

    def test_a_skipped_or_finished_agent_step_escalates(self):
        for outcome in ("skipped", "success", ""):
            with self.subTest(outcome=outcome):
                self.assertEqual(fix_dead_run.Decision("escalate", ""),
                                 decide(outcome=outcome))

    def test_a_missing_record_alone_still_escalates(self):
        """The call shape tests/test_fix_model_death.py uses."""
        self.assertEqual(fix_dead_run.Decision("escalate", ""),
                         fix_dead_run.decide(None, 0))

    def test_a_record_that_shows_the_agent_finished_escalates(self):
        finished = {"type": "result", "subtype": "success", "is_error": False,
                    "num_turns": 30}
        self.assertEqual(fix_dead_run.Decision("escalate", ""),
                         decide(finished))

    def test_the_reason_names_the_missing_fact(self):
        cases = (
            (dict(annotations=None), "could not be read"),
            (dict(annotations=[annotation(EXIT_137)]), "no lost machine"),
            (dict(outcome="skipped"), "skipped"),
            (dict(execution={"is_error": False}), "result record"),
        )
        for kw, words in cases:
            with self.subTest(kw=kw):
                args = dict(execution=None, outcome="failure", annotations=TONIGHT)
                args.update(kw)
                quote, why = fix_dead_run.runner_lost(
                    args["execution"], args["outcome"], args["annotations"])
                self.assertEqual("", quote)
                self.assertIn(words, why)


class OneRetryPerHeadTest(unittest.TestCase):

    def marker(self, head=HEAD, login=WORKER):
        return rest(login, decide(head=head).comment)

    def count(self, thread, head=HEAD):
        return fix_dead_run.runner_lost_markers(thread, head)

    def test_a_first_lost_machine_retries(self):
        self.assertEqual(0, self.count([]))
        self.assertEqual(RETRY, decide(prior_lost=0).action)

    def test_a_second_on_the_same_head_holds(self):
        thread = [self.marker()]
        self.assertEqual(1, self.count(thread))
        d = decide(prior_lost=self.count(thread))
        self.assertEqual(HOLD, d.action)
        self.assertTrue(d.comment.startswith("🛑 Two fix runs in a row"), d.comment)
        self.assertIn("lost their machine", d.comment)
        self.assertIn("The work itself has not failed", d.comment)
        self.assertIn("AWS interrupted the EC2 Spot instance running this job",
                      d.comment)
        self.assertNotIn(fix_dead_run.RUNNER_LOST_TAG, d.comment)

    def test_a_new_commit_re_arms_the_count(self):
        thread = [self.marker(head=OTHER_HEAD)]
        self.assertEqual(0, self.count(thread))
        self.assertEqual(RETRY, decide(prior_lost=self.count(thread)).action)

    def test_a_planted_marker_is_not_counted(self):
        self.assertEqual(0, self.count([self.marker(login="mallory")]))

    def test_a_quoted_marker_below_the_first_line_is_not_counted(self):
        quoted = rest(WORKER, "a note\n> " + decide().comment)
        self.assertEqual(0, self.count([quoted]))

    def test_the_cli_counts_off_the_thread_and_the_head(self):
        with tempfile.TemporaryDirectory() as td:
            thread = os.path.join(td, "thread.json")
            with open(thread, "w") as fh:
                json.dump([[self.marker()]], fh)
            notes = os.path.join(td, "annotations.json")
            with open(notes, "w") as fh:
                json.dump(TONIGHT, fh)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = fix_dead_run.main([
                    "decide", os.path.join(td, "missing.json"),
                    "--comments-json", thread, "--run-url", RUN_URL,
                    "--head", HEAD, "--agent-step-outcome", "failure",
                    "--annotations-json", notes])
            self.assertEqual(0, rc)
            self.assertEqual(HOLD, out.getvalue().splitlines()[0])

    def test_the_cli_reads_an_unreadable_annotation_file_as_unread(self):
        with tempfile.TemporaryDirectory() as td:
            notes = os.path.join(td, "annotations.json")
            open(notes, "w").close()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                fix_dead_run.main([
                    "decide", os.path.join(td, "missing.json"),
                    "--head", HEAD, "--agent-step-outcome", "failure",
                    "--annotations-json", notes])
            self.assertEqual("escalate", out.getvalue().splitlines()[0])
            self.assertIn("could not be read", err.getvalue())


class TheNewLinesStayOutOfOtherCountsTest(unittest.TestCase):

    @property
    def LINES(self):
        return (decide().comment, decide(prior_lost=1).comment)

    def test_neither_line_reads_as_no_progress_a_push_or_an_attempt(self):
        for line in self.LINES:
            with self.subTest(line=line[:40]):
                self.assertNotIn(fix_convergence.NO_PROGRESS, line)
                self.assertNotIn(fix_dead_run.PUSH_MARKER, line)
                self.assertFalse(line.startswith("🔧"))

    def test_the_retry_line_is_never_a_blocker(self):
        self.assertTrue(self.LINES[0].startswith("⚡"))
        self.assertFalse(self.LINES[0].startswith("🛑"))

    def test_both_lines_at_the_head_do_not_halt_the_loop(self):
        thread = [rest(WORKER, line) for line in self.LINES]
        halt = fix_convergence.halt(thread, HEAD[:8], WORKER)
        self.assertEqual(0, halt.noprog)
        self.assertFalse(halt.halted)

    def test_the_budgets_stay_apart(self):
        lost = rest(WORKER, self.LINES[0])
        outage = rest(WORKER, f"⚡ {fix_dead_run.OUTAGE_TAG}: the fix run died")
        turns = rest(WORKER, f"⚡ {fix_dead_run.TURN_CAP_TAG}: ran out of steps")
        for tag in (fix_dead_run.OUTAGE_TAG, fix_dead_run.TURN_CAP_TAG):
            self.assertEqual(
                0, fix_dead_run.consecutive_prior_markers([lost, lost], tag))
        self.assertEqual(
            0, fix_dead_run.runner_lost_markers([outage, turns], HEAD))

    def test_no_tag_contains_another(self):
        tags = [getattr(fix_dead_run, name) for name in dir(fix_dead_run)
                if name.endswith("_TAG")]
        self.assertIn(fix_dead_run.RUNNER_LOST_TAG, tags)
        for outer in tags:
            for inner in tags:
                if outer != inner:
                    self.assertNotIn(inner, outer)

    def test_the_registry_has_no_problem(self):
        self.assertEqual([], pipeline_act.problems())

    def test_it_is_a_retry_marker(self):
        self.assertIn(fix_dead_run.RUNNER_LOST_TAG, fix_dead_run.RETRY_MARKERS)
        self.assertEqual(10, fix_dead_run.RUNNER_LOST_WAIT_MINUTES)


# --------------------------------------------------------------------------
# 2: the sweep's retry
# --------------------------------------------------------------------------
def _iso(minutes_ago: float) -> str:
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(body: str, created: str | None) -> dict:
    comment = {"author": {"login": "agent-bureau-bot"}, "body": body}
    if created is not None:
        comment["createdAt"] = created
    return {"number": 3502, "headRefName": "agent/DRE-6556-x",
            "headRefOid": HEAD, "mergeStateStatus": "BLOCKED",
            "comments": [comment]}


class TheSweepWaitsForRunsOnTest(unittest.TestCase):

    @property
    def LOST(self):
        return decide().comment

    def run_sweep(self, prs, busy="[]"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            calls = sweep(prs, busy=busy)
        return calls, out.getvalue()

    def test_it_dispatches_once_the_marker_is_old_enough(self):
        calls, log = self.run_sweep([_pr(self.LOST, _iso(11))])
        self.assertEqual(1, len(calls))
        self.assertIn("pr_number=3502", " ".join(calls[0]))
        self.assertIn("lost its machine", log)
        self.assertNotIn("model/API error", log)

    def test_it_waits_on_a_young_marker(self):
        calls, _ = self.run_sweep([_pr(self.LOST, _iso(3))])
        self.assertEqual([], calls)

    def test_an_unreadable_timestamp_waits(self):
        for created in (None, ""):
            with self.subTest(created=created):
                calls, _ = self.run_sweep([_pr(self.LOST, created)])
                self.assertEqual([], calls)

    def test_it_backs_off_while_a_fix_run_is_queued_or_running(self):
        for status in ("queued", "in_progress"):
            with self.subTest(status=status):
                calls, _ = self.run_sweep(
                    [_pr(self.LOST, _iso(30))],
                    busy=json.dumps([{"status": status}]))
                self.assertEqual([], calls)

    def test_the_other_markers_keep_dispatching_with_no_wait(self):
        for tag in (fix_dead_run.OUTAGE_TAG, fix_dead_run.TURN_CAP_TAG):
            with self.subTest(tag=tag):
                calls, _ = self.run_sweep([_pr(f"⚡ {tag}: died", _iso(0))])
                self.assertEqual(1, len(calls))

    def test_the_hold_stops_the_sweep(self):
        calls, _ = self.run_sweep([_pr(decide(prior_lost=1).comment, _iso(60))])
        self.assertEqual([], calls)


# --------------------------------------------------------------------------
# 3: the Report step, executed — tonight's replay
# --------------------------------------------------------------------------
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
log = os.environ["GH_LOG"]
def record(kind, **kw):
    open(log, "a").write(json.dumps(dict(kind=kind, argv=argv,
                                         token=os.environ.get("GH_TOKEN"), **kw)) + "\n")
if argv[:2] == ["pr", "view"]:
    fields = argv[argv.index("--json") + 1] if "--json" in argv else ""
    if fields == "isDraft":
        print("false")
    elif fields == "state":
        print("OPEN")
    else:
        print(os.environ["GH_HEAD"])
    sys.exit(0)
if argv[:2] == ["pr", "comment"]:
    body = None
    if "--body-file" in argv:
        body = open(argv[argv.index("--body-file") + 1], encoding="utf-8").read()
    elif "--body" in argv:
        body = argv[argv.index("--body") + 1]
    record("comment", body=body)
    sys.exit(0)
if argv[:1] == ["api"]:
    path = next((a for a in argv[1:] if a.startswith("repos/")), "")
    if "/attempts/" in path and path.endswith("/jobs") or "/jobs?" in path:
        record("jobs")
        print(open(os.environ["GH_JOBS"]).read())
        sys.exit(0)
    if "/annotations" in path:
        record("annotations")
        if os.environ.get("GH_ANNOTATIONS_FAIL") == "1":
            sys.stderr.write("HTTP 403: Resource not accessible by integration\n")
            sys.exit(1)
        print(open(os.environ["GH_ANNOTATIONS"]).read())
        sys.exit(0)
    print(open(os.environ["GH_THREAD"]).read())
    sys.exit(0)
sys.exit(0)
'''


def critic(token: str, sha: str) -> str:
    return f"🔎 QA Critic — VERDICT: {token} @{sha}\n\nfindings…"


APPROVE = critic("APPROVE", HEAD)


def report(*, annotations=TONIGHT, annotations_fail=False, outcome="failure",
           thread=None, exec_text=None):
    """Run the real report_fix_result.sh on tonight's case.
    Returns (proc, gh calls, linear calls, the attribution line)."""
    thread = [rest(QA, APPROVE)] if thread is None else thread
    with tempfile.TemporaryDirectory() as td:
        base = _checkout(td)
        linear_log = os.path.join(td, "linear.jsonl")
        with open(os.path.join(base, "scripts", "linear_ops.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
        with open(os.path.join(base, "scripts", "hold.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\n"
                     f"open({linear_log!r}, 'a').write(json.dumps(['hold.py'] + sys.argv[1:]) + '\\n')\n")
        # The run was sent WITH the APPROVE (the sweep's approved-but-red
        # repair), so fix_exit does not read it as newer than the round.
        with open(os.path.join(base, "critic-verdict.md"), "w", encoding="utf-8") as fh:
            fh.write(APPROVE)
        binary = os.path.join(td, "bin")
        os.makedirs(binary)
        with open(os.path.join(binary, "gh"), "w") as fh:
            fh.write(GH_STUB)
        os.chmod(os.path.join(binary, "gh"), 0o755)

        def write(name, value):
            path = os.path.join(td, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(value, fh)
            return path

        thread_file = write("thread.json", [thread])
        jobs_file = write("jobs.json", {"total_count": 2, "jobs": [
            {"id": 114135780000, "name": "Resolve", "runner_name": "GitHub Actions 7"},
            {"id": JOB_ID, "name": "fix", "runner_name": RUNNER},
        ]})
        notes_file = write("annotations.json", annotations)
        fix_handoff.open_handoff(td, REPO, PR, HEAD,
                                 legacy_dir=os.path.join(td, "legacy"))
        env = dict(os.environ, PATH=binary + os.pathsep + os.environ["PATH"],
                   CLASSIFICATION="", DISPATCH_TOKEN="workflow-token",
                   GH_LOG=os.path.join(td, "gh.jsonl"), GH_HEAD=HEAD,
                   GH_THREAD=thread_file, GH_JOBS=jobs_file,
                   GH_ANNOTATIONS=notes_file,
                   GH_ANNOTATIONS_FAIL="1" if annotations_fail else "0",
                   RUN_ID="38025638252", GITHUB_RUN_ATTEMPT="1",
                   RUNNER_NAME=RUNNER, AGENT_STEP_OUTCOME=outcome,
                   **_report_env(td, "fix"))
        env.update(PRE_SHA=HEAD, ATTEMPT="1", RUN_URL=RUN_URL)
        if exec_text is not None:
            with open(env["EXEC_FILE"], "w", encoding="utf-8") as fh:
                fh.write(exec_text)
        proc = subprocess.run(["bash", SCRIPT], cwd=td, env=env,
                              capture_output=True, text=True, timeout=120)

        def lines(path):
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh.read().splitlines()]

        trailer = fix_handoff.attribution(td, REPO, PR, HEAD, CARD)
        return proc, lines(env["GH_LOG"]), lines(linear_log), trailer


def pr_comments(calls):
    return [c["body"] for c in calls if c["kind"] == "comment"]


def card_comments(calls):
    return [c for c in calls if c[:1] == ["comment"]]


def parks(calls):
    return [c for c in calls
            if (c[:1] == ["add-label"] and "needs-human" in c)
            or c[:2] == ["hold.py", "apply"]
            or (c[:1] in (["advance"], ["state"]) and "Triage" in c)]


RETRY_NOTE = ("🤖 The last fix run's machine was taken away by the cloud provider "
              "before the agent finished — nothing is wrong with the work. The "
              "pipeline retries once automatically; no action needed.")


class TonightsReplayTest(unittest.TestCase):
    """Run 38025638252 attempt 1, replayed through the real Report script."""

    def test_it_posts_the_lost_machine_line_and_parks_nothing(self):
        proc, gh, linear, trailer = report()
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        bodies = pr_comments(gh)
        self.assertEqual(1, len(bodies), bodies)
        self.assertTrue(bodies[0].startswith(
            f"⚡ {fix_dead_run.RUNNER_LOST_TAG} @a3fa0c44:"), bodies[0])
        self.assertIn('"AWS interrupted the EC2 Spot instance running this job."',
                      bodies[0])
        self.assertTrue(bodies[0].rstrip("\n").endswith(trailer))
        self.assertNotIn("Escalating to a human", bodies[0])
        self.assertNotIn(fix_convergence.NO_PROGRESS, bodies[0])
        self.assertEqual([["comment", CARD, RETRY_NOTE]], card_comments(linear))
        self.assertEqual([], parks(linear))

    def test_the_two_reads_ride_the_right_tokens(self):
        _, gh, _, _ = report()
        jobs = [c for c in gh if c["kind"] == "jobs"]
        notes = [c for c in gh if c["kind"] == "annotations"]
        self.assertEqual(1, len(jobs))
        self.assertEqual("workflow-token", jobs[0]["token"])
        self.assertIn(f"repos/{REPO}/actions/runs/38025638252/attempts/1/jobs",
                      " ".join(jobs[0]["argv"]))
        self.assertEqual(1, len(notes))
        self.assertEqual("test", notes[0]["token"])
        self.assertIn(f"repos/{REPO}/check-runs/{JOB_ID}/annotations",
                      " ".join(notes[0]["argv"]))

    def _assert_today(self, result):
        proc, gh, linear, _ = result
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        bodies = pr_comments(gh)
        self.assertEqual(1, len(bodies), bodies)
        self.assertIn("pushed no new commit", bodies[0])
        self.assertIn("Escalating to a human", bodies[0])
        self.assertNotIn(fix_dead_run.RUNNER_LOST_TAG, bodies[0])
        self.assertTrue(parks(linear), linear)
        self.assertIn(["advance", CARD, "Triage", "In Review,In Progress,Todo"],
                      linear)

    def test_an_annotation_read_that_errors_escalates_as_today(self):
        result = report(annotations_fail=True)
        self._assert_today(result)
        self.assertIn("could not be read", result[0].stdout + result[0].stderr)

    def test_exit_137_alone_escalates_as_today(self):
        result = report(annotations=[annotation(EXIT_137)])
        self._assert_today(result)
        self.assertIn("no lost machine", result[0].stdout + result[0].stderr)

    def test_a_skipped_agent_step_escalates_and_reads_nothing(self):
        result = report(outcome="skipped")
        self._assert_today(result)
        self.assertEqual([], [c for c in result[1]
                              if c["kind"] in ("jobs", "annotations")])

    def test_a_run_with_a_record_reads_nothing_and_escalates_as_today(self):
        record = json.dumps({"type": "result", "subtype": "success",
                             "is_error": False, "num_turns": 30})
        result = report(exec_text=record)
        self._assert_today(result)
        self.assertEqual([], [c for c in result[1]
                              if c["kind"] in ("jobs", "annotations")])

    def test_a_second_lost_machine_on_the_head_holds_for_a_person(self):
        first = rest(WORKER, decide().comment)
        proc, gh, linear, _ = report(thread=[rest(QA, APPROVE), first])
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        bodies = pr_comments(gh)
        self.assertEqual(1, len(bodies), bodies)
        self.assertTrue(bodies[0].startswith("🛑 Two fix runs in a row"), bodies[0])
        self.assertNotIn(fix_dead_run.RUNNER_LOST_TAG, bodies[0])
        self.assertNotIn(fix_convergence.NO_PROGRESS, bodies[0])
        said = card_comments(linear)
        self.assertEqual(1, len(said), said)
        self.assertIn("lost their machine", said[0][2])
        self.assertIn("has not failed", said[0][2])
        self.assertTrue(parks(linear), linear)


# --------------------------------------------------------------------------
# 4: the wiring
# --------------------------------------------------------------------------
class TheWiringTest(unittest.TestCase):

    def test_the_report_step_is_handed_the_agent_steps_outcome(self):
        for entry in yaml.safe_load(open(WORKFLOW))["jobs"]["fix"]["steps"]:
            if entry.get("name") == "Report":
                self.assertEqual("${{ steps.claude.outcome }}",
                                 entry["env"]["AGENT_STEP_OUTCOME"])
                self.assertEqual("bash .bureau-pipeline/scripts/report_fix_result.sh",
                                 entry["run"].strip())
                return
        self.fail("the Report step is gone")

    def test_four_unconverted_rows_name_the_new_sites(self):
        rows = [r for r in pipeline_act.load()["unconverted"]
                if r.get("file") == ".github/workflows/agent-fix.yml"
                and r.get("step") == "Report"
                and ("runner-lost" in r.get("anchor", "")
                     or "lost their machine" in r.get("anchor", "")
                     or "machine was taken away" in r.get("anchor", ""))]
        self.assertEqual(4, len(rows), rows)
        for row in rows:
            self.assertEqual("undeclared-act", row["kind"])
            self.assertTrue(row["means"].strip() and row["why"].strip())

    def test_every_comment_site_is_composed_or_declared(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = check_act_receipts.main([])
        self.assertEqual(0, rc, out.getvalue())

    def test_the_recovery_doc_names_the_marker(self):
        with open(os.path.join(ROOT, "docs", "held-pr-recovery.md"),
                  encoding="utf-8") as fh:
            rows = [line for line in fh if line.startswith("|")
                    and fix_dead_run.RUNNER_LOST_TAG in line]
        self.assertTrue(any(line.startswith("| dead-fix-run |") for line in rows), rows)
        self.assertTrue(any(not line.startswith("| dead-fix-run |") for line in rows), rows)


if __name__ == "__main__":
    unittest.main()
