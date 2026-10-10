"""The merge gate looks at the Agent Fix lane again when a fix run skips itself
(DRE-6577).

The critic's APPROVE comment wakes the merge gate and the Agent Fix stub in
the same second. The fix run sits `queued` for a few seconds and completes
`skipped`, because its job's `if:` admits no APPROVE. The gate read the lane
inside those seconds, saw the run queued for its own pull request, and
answered condition F's wait (DRE-4486). A skipped run pushes nothing and
posts nothing, so nothing woke the gate again: bureau-pipeline #904 sat
approved and green from 21:57 to 23:37 PT on 2026-10-09 until a person
re-ran the gate by hand.

Every scenario here runs scripts/evaluate_and_merge.sh as a file against a
scripted `gh` whose `run list` answers one lane per read, in order, and a
`sleep` that only logs, so no test waits. The replay ends `merge` on the
second lane read; without the re-read it ends `wait` on the first, which is
the incident.

Run: python3 -m pytest tests/test_fix_lane_recheck_scenario.py -v
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — fixed argv, our own script
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_and_merge.sh"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import stranded_fix  # noqa: E402
from test_merge_gate_one_read import LINEAR_STUB, _shadow_pipeline  # noqa: E402

# bureau-pipeline #904 at 21:57:29 PT on 2026-10-09.
PR = 904
HEAD = "8fcac4d2b490f285acae73362885c1a44ea8b83c"
REPO = "dreadnought-foundry/bureau-pipeline"
BRANCH = "agent/DRE-6533-critic-told-when-re-reviewing"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
APPROVE = f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"
FIX_RUN = 38025855568

GREEN_CI = [{"name": "Pipeline Tests", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}}]
CI_RUN_DONE = {"id": 11, "name": "Pipeline Tests",
               "path": ".github/workflows/tests.yml", "event": "pull_request",
               "status": "completed", "check_suite_id": 1}


def fix_run(status: str) -> dict:
    """Agent Fix #904 as `gh run list --json status,databaseId,displayTitle`
    renders it."""
    return {"status": status, "databaseId": FIX_RUN,
            "displayTitle": f"Agent Fix #{PR}"}


# The fix run as the gate's lane reads saw it: queued on the first read,
# completed (its conclusion `skipped`) on every later one.
SKIPPED_FIX = [[fix_run("queued")], [fix_run("completed")]]
WORKING_FIX = [[fix_run("in_progress")]]

TODAYS_REASON = stranded_fix.lane_refusal(
    stranded_fix.Lane(by_pr={PR: FIX_RUN}), PR)

# A stub `gh` that answers from a fixture file and logs every invocation.
# `run list` is the Agent Fix lane: the Nth listing answers `lanes[N-1]`,
# every listing past the end answers the last, and `"die"` fails them all.
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


if args[:2] == ["pr", "view"]:
    view = fx["view"]
    if opt("--jq"):
        emit(view["headRefOid"])
    emit({f: view[f] for f in opt("--json").split(",") if f in view})

if args[:2] == ["pr", "merge"]:
    emit("merged")

if args[:2] == ["run", "list"]:
    reads = sum(1 for ln in open(os.environ["GH_LOG"])
                if json.loads(ln)[:2] == ["run", "list"])
    lanes = fx["lanes"]
    if lanes == "die":
        sys.stderr.write("HTTP 502: Bad Gateway\n")
        raise SystemExit(1)
    emit(lanes[min(reads, len(lanes)) - 1])

if args[:2] == ["workflow", "run"]:
    emit("")

if args[0] == "api":
    method = opt("--method") or opt("-X") or "GET"
    path = [a for a in args[1:] if "/" in a and not a.startswith("-")][0]
    if method == "POST":
        rows = comments()
        body = json.loads(sys.stdin.read())["body"]
        row = {"id": 900 + len(rows), "user": {"login": os.environ["QA_LOGIN"]},
               "body": body}
        rows.append(row)
        json.dump(rows, open(os.environ["COMMENTS"], "w"))
        emit(row)
    if "check-runs" in path:
        page = {"total_count": len(fx["check_runs"]), "check_runs": fx["check_runs"]}
        emit([page] if "--slurp" in args else page)
    if "/compare/" in path:
        emit({"status": "ahead", "files": [{"filename": "scripts/a.py"}],
              "commits": [{"sha": fx["view"]["headRefOid"]}]})
    if "actions/runs" in path:
        emit({"workflow_runs": fx["workflow_runs"]})
    if path.endswith("/commits?per_page=100"):
        emit(fx.get("pr_commits", [{"sha": fx["view"]["headRefOid"]}]))
    if "/comments" in path:
        emit([comments()] if "--slurp" in args else comments())
    if "pulls?state=open" in path:
        emit(json.dumps({"number": int(os.environ["PR"]),
                         "head_sha": fx["view"]["headRefOid"]}))
    if "/rules/branches/" in path:
        emit([[]])
    if "/contents/" in path:
        sys.stderr.write("HTTP 404: Not Found\n")
        raise SystemExit(1)
    if path == "repos/" + os.environ["REPO_FULL"]:
        emit("main")
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''

# A `sleep` that logs how long it was asked to wait and returns at once.
SLEEP_STUB = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ["SLEEP_LOG"], "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
'''

KNOBS_AT_ZERO = {"FIX_LANE_RECHECK_SECONDS": "0",
                 "FIX_LANE_RECHECK_CEILING_SECONDS": "0"}


def view(**overrides) -> dict:
    """gh's own rendering of #904 at the approval."""
    record = {
        "headRefName": BRANCH, "state": "OPEN", "mergeStateStatus": "CLEAN",
        "isDraft": False, "headRefOid": HEAD, "baseRefName": "main",
        "body": "What's new: none", "createdAt": "2026-10-10T03:20:00Z",
        "author": {"is_bot": True, "login": "app/agent-bureau-bot"},
        "url": f"https://github.com/{REPO}/pull/{PR}", "files": [],
    }
    record.update(overrides)
    return record


class Run:
    def __init__(self, proc, calls, comments, seeded, linear, sleeps):
        self.proc = proc
        self.calls = calls
        self.comments = comments
        self.seeded = seeded
        self.linear = linear
        self.sleeps = sleeps

    def explain(self) -> str:
        return (f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}\n"
                "calls:\n" + "\n".join(map(str, self.calls)))

    @property
    def lane_reads(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ["run", "list"])

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def posted(self):
        return self.comments[self.seeded:]

    def decisions(self) -> list:
        return [ln.split("=", 1)[1] for ln in self.proc.stdout.splitlines()
                if ln.startswith("decision=")]

    def reasons(self) -> list:
        return [ln.split("=", 1)[1] for ln in self.proc.stdout.splitlines()
                if ln.startswith("reason=")]

    def reread_lines(self) -> list:
        return [ln for ln in self.proc.stdout.splitlines()
                if ln.startswith("fix-lane read ")]


def run_gate(lanes, *, view_overrides=None, seed=(APPROVE,), check_runs=None,
             workflow_runs=None, pr_commits=None, knobs=None) -> Run:
    """Run scripts/evaluate_and_merge.sh against the stubs, its `/tmp/`
    records moved into this run's directory. `knobs` is the env the re-read
    reads; None leaves both unset, so the script's defaults answer."""
    fixture = {
        "view": view(**(view_overrides or {})),
        "lanes": lanes,
        "check_runs": GREEN_CI if check_runs is None else check_runs,
        "workflow_runs": [CI_RUN_DONE] if workflow_runs is None else workflow_runs,
    }
    if pr_commits is not None:
        fixture["pr_commits"] = pr_commits
    env = {k: v for k, v in os.environ.items()
           if k not in ("FIX_LANE_RECHECK_SECONDS",
                        "FIX_LANE_RECHECK_CEILING_SECONDS")}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "scratch").mkdir()
        script = td / "evaluate_and_merge.sh"
        script.write_text(SCRIPT.read_text().replace("/tmp/", f"{td / 'scratch'}/"))
        (td / "bin").mkdir()
        for name, text in (("gh", GH_STUB), ("sleep", SLEEP_STUB)):
            (td / "bin" / name).write_text(text)
            (td / "bin" / name).chmod(0o755)  # nosec B103 — a test stub on PATH
        _shadow_pipeline(td)
        for log in ("gh.log", "linear.log", "sleep.log"):
            (td / log).write_text("")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps([
            {"id": i + 1, "user": {"login": QA_LOGIN}, "body": body}
            for i, body in enumerate(seed)
        ]))
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own script
            ["bash", str(script)],
            cwd=td, capture_output=True, text=True, timeout=120,
            env={
                **env,
                **(knobs or {}),
                "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                "PR": str(PR),
                "GH_TOKEN": "qa-token",
                "LINEAR_API_KEY": "test-key",
                "REPO_FULL": REPO,
                "WORKFLOW_TOKEN": "workflow-token",
                "QA_LOGIN": QA_LOGIN,
                "FIXTURE": str(td / "fixture.json"),
                "COMMENTS": str(td / "comments.json"),
                "GH_LOG": str(td / "gh.log"),
                "LINEAR_LOG": str(td / "linear.log"),
                "SLEEP_LOG": str(td / "sleep.log"),
                "MERGE_RETRY_SECONDS": "0",
            },
        )
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
        comments = json.loads((td / "comments.json").read_text())
        linear = [json.loads(ln) for ln in (td / "linear.log").read_text().splitlines() if ln]
        sleeps = (td / "sleep.log").read_text().split()
    return Run(proc, calls, comments, len(seed), linear, sleeps)


class TheIncidentReplayedTest(unittest.TestCase):
    """2026-10-09: the approval bound to the head, checks green, the fix run
    queued on the gate's first lane read and completed on every later one,
    and no other event."""

    @classmethod
    def setUpClass(cls):
        cls.gate = run_gate(SKIPPED_FIX, knobs=KNOBS_AT_ZERO)

    def test_the_run_exits_clean(self):
        self.assertEqual(self.gate.proc.returncode, 0, self.gate.explain())

    def test_the_first_look_is_todays_condition_f_wait(self):
        self.assertEqual(self.gate.decisions()[0], "wait", self.gate.explain())
        self.assertEqual(self.gate.reasons()[0], TODAYS_REASON, self.gate.explain())

    def test_the_gate_merges_on_the_second_lane_read(self):
        self.assertEqual(self.gate.decisions()[-1], "merge", self.gate.explain())
        self.assertEqual(self.gate.lane_reads, 2, self.gate.explain())

    def test_the_merge_is_pinned_to_the_evaluated_head(self):
        self.assertEqual(len(self.gate.merges), 1, self.gate.explain())
        self.assertIn("--match-head-commit", self.gate.merges[0])
        self.assertIn(HEAD, self.gate.merges[0])

    def test_with_the_knobs_at_zero_nothing_sleeps(self):
        self.assertEqual(self.gate.sleeps, ["0"], self.gate.explain())

    def test_nothing_is_posted_on_the_pull_request(self):
        self.assertEqual(self.gate.posted, [], self.gate.explain())

    def test_the_card_hears_only_the_merge_it_heard_before(self):
        """The merge path's own two card writes: the move to In Review and
        the auto-merge comment. The re-read adds none."""
        self.assertEqual([c[0] for c in self.gate.linear], ["advance", "comment"],
                         self.gate.linear)

    def test_the_log_names_the_read_that_decided(self):
        lines = self.gate.reread_lines()
        self.assertEqual(len(lines), 1, self.gate.explain())
        self.assertTrue(lines[0].startswith("fix-lane read 2: "), lines)
        self.assertIn("no Agent Fix run in flight", lines[0])


class TheDefaultsTest(unittest.TestCase):
    """With neither knob set, the pause is 15 seconds and the ceiling 60:
    at most four re-reads, and never more sleep than the ceiling."""

    def test_a_skipped_fix_run_is_seen_after_one_pause(self):
        run = run_gate(SKIPPED_FIX)
        self.assertEqual(run.decisions()[-1], "merge", run.explain())
        self.assertEqual(run.lane_reads, 2, run.explain())
        self.assertEqual(run.sleeps, ["15"], run.explain())

    def test_a_working_fix_run_is_read_four_more_times_inside_the_ceiling(self):
        run = run_gate(WORKING_FIX)
        self.assertEqual(run.lane_reads, 5, run.explain())
        self.assertEqual(run.sleeps, ["15"] * 4, run.explain())
        self.assertLessEqual(sum(int(s) for s in run.sleeps), 60)
        self.assertEqual(run.decisions()[-1], "wait", run.explain())


class AFixRunThatIsWorkingStillHoldsTest(unittest.TestCase):
    """A fix run `in_progress` on every read: the gate re-reads to the
    ceiling and ends in today's DRE-4486 wait."""

    @classmethod
    def setUpClass(cls):
        cls.gate = run_gate(WORKING_FIX, knobs=KNOBS_AT_ZERO)

    def test_it_ends_in_todays_wait(self):
        self.assertEqual(self.gate.proc.returncode, 0, self.gate.explain())
        self.assertEqual(self.gate.decisions()[-1], "wait", self.gate.explain())
        self.assertEqual(self.gate.reasons()[-1], TODAYS_REASON, self.gate.explain())

    def test_it_re_read_up_to_the_ceiling(self):
        self.assertEqual(self.gate.lane_reads, 2, self.gate.explain())
        lines = self.gate.reread_lines()
        self.assertEqual(len(lines), 1, self.gate.explain())
        self.assertIn(f"run {FIX_RUN} in_progress", lines[0])

    def test_it_never_merges_and_posts_nothing(self):
        self.assertEqual(self.gate.merges, [], self.gate.explain())
        self.assertEqual(self.gate.posted, [], self.gate.explain())
        self.assertEqual(self.gate.linear, [], self.gate.explain())


class AStillQueuedOrUnreadableLaneHoldsTest(unittest.TestCase):
    def test_a_run_still_queued_at_the_ceiling_waits(self):
        run = run_gate([[fix_run("queued")]], knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions()[-1], "wait", run.explain())
        self.assertEqual(run.reasons()[-1], TODAYS_REASON, run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_an_unreadable_lane_at_the_ceiling_waits(self):
        """`gh run list` failing on every read is the unreadable lane."""
        run = run_gate("die", knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions()[-1], "wait", run.explain())
        self.assertIn("DRE-4486", run.reasons()[-1])
        self.assertIn("could not be read", run.reasons()[-1])
        self.assertEqual(run.merges, [], run.explain())
        self.assertIn("unreadable", run.reread_lines()[0])


class OnlyConditionFIsReReadTest(unittest.TestCase):
    """The same queued-then-completed lane, and something else stands in the
    way: none of them merges, and none re-reads the lane."""

    def test_a_head_sent_back_ends_in_todays_hold(self):
        rejected = f"🔎 QA Critic — VERDICT: REQUEST_CHANGES @{HEAD}"
        run = run_gate(SKIPPED_FIX, seed=(rejected,), knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["hold"], run.explain())
        self.assertEqual(run.lane_reads, 1, run.explain())
        self.assertEqual(run.reread_lines(), [], run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_an_approval_of_another_head_ends_in_todays_wait_for_a_review(self):
        """Condition 2's wait, not condition F's: nothing re-reads."""
        stale = f"🔎 QA Critic — VERDICT: APPROVE @{'0' * 40}"
        run = run_gate(SKIPPED_FIX, seed=(stale,), knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["wait"], run.explain())
        self.assertIn("stale", run.reasons()[0])
        self.assertEqual(run.lane_reads, 1, run.explain())
        self.assertEqual(run.reread_lines(), [], run.explain())
        self.assertEqual(run.sleeps, [], run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_a_red_check_ends_in_the_precheck_wait_with_no_lane_read(self):
        red = [dict(GREEN_CI[0], conclusion="failure")]
        run = run_gate(SKIPPED_FIX, check_runs=red, knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["wait"], run.explain())
        self.assertEqual(run.lane_reads, 0, run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_an_unfinished_check_ends_in_the_precheck_wait_with_no_lane_read(self):
        running = [dict(GREEN_CI[0], status="in_progress", conclusion=None)]
        run = run_gate(SKIPPED_FIX, check_runs=running,
                       workflow_runs=[dict(CI_RUN_DONE, status="in_progress")],
                       knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["wait"], run.explain())
        self.assertEqual(run.lane_reads, 0, run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_a_conflicted_branch_ends_in_conflict(self):
        run = run_gate(SKIPPED_FIX, view_overrides={"mergeStateStatus": "DIRTY"},
                       knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["conflict"], run.explain())
        self.assertEqual(run.lane_reads, 1, run.explain())
        self.assertEqual(run.merges, [], run.explain())

    def test_a_human_decision_re_reads_nothing(self):
        """A major Dependabot bump is `human` at condition D, before the
        lane is ever consulted."""
        major = {"sha": HEAD, "commit": {"message": (
            "Bump requests from 2.32.3 to 3.0.0\n\n---\nupdated-dependencies:\n"
            "- dependency-name: requests\n  dependency-type: direct:production\n"
            "  update-type: version-update:semver-major\n...\n")}}
        run = run_gate(
            SKIPPED_FIX, seed=(),
            view_overrides={"headRefName": "dependabot/pip/requests-3.0.0",
                            "author": {"is_bot": True, "login": "app/dependabot"}},
            pr_commits=[major], knobs=KNOBS_AT_ZERO)
        self.assertEqual(run.decisions(), ["human"], run.explain())
        self.assertEqual(run.lane_reads, 1, run.explain())
        self.assertEqual(run.merges, [], run.explain())


class EveryLaneRefusalIsReadAsConditionFTest(unittest.TestCase):
    """The script tells condition F's wait by the card every one of
    `lane_refusal`'s wordings cites."""

    def test_each_wording_cites_dre_4486(self):
        lanes = {
            "unreadable": stranded_fix.Lane(readable=False, detail="HTTP 502"),
            "for this pull request": stranded_fix.Lane(by_pr={PR: FIX_RUN}),
            "unattributed": stranded_fix.Lane(unattributed=(FIX_RUN,)),
        }
        for name, lane in lanes.items():
            with self.subTest(name):
                self.assertRegex(stranded_fix.lane_refusal(lane, PR), r"DRE-4486(?!\d)")
        no_number = stranded_fix.lane_refusal(lanes["unattributed"], None)
        self.assertRegex(no_number, r"DRE-4486(?!\d)")


if __name__ == "__main__":
    unittest.main()
