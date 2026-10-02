"""The merge gate reads its pull request ONCE, and stops early while CI runs
(Stage 2 #19, package BP-5).

Measured on the fleet before this change: the gate woke 3–6 times per pull
request head, 67–82% of its runs decided nothing, and every run read the same
pull request 8–9 times — seven `gh pr view` calls, one per field, plus a REST
`pulls/{pr}` read for the author. Two changes, both proved here by running
scripts/evaluate_and_merge.sh as a file against a stub `gh` that logs every
call:

  1. ONE `gh pr view --json <ten fields>`, written to a file and read from it.
     Each old read failed in its own way, and every way survives:
       - a failed read kills the step, as each `--jq` read it replaces did;
       - a decision field that comes back absent or null kills the step too
         (the file cannot fail the way `--jq` did, so the script checks), and
         `isDraft` above all (DRE-3467);
       - the body and createdAt stay soft: absent or null is "nothing was
         read" and condition W stays off (DRE-5511);
       - the author is gh's rendering (`app/<slug>`, `is_bot`), turned back
         into the REST spelling (`<slug>[bot]`) condition D compares
         (DRE-2039) — a slash never appears in a person's login.
     The merge-refusal re-read of the head (DRE-2117) stays: it is the race
     guard and must be fresh.
  2. When condition 1 is going to answer `wait`, the gate says so from the
     check runs and the runs listing alone, and exits BEFORE the compare,
     comment, commit, lane, stack and owner reads. Conditions 0 and D come
     first in merge_gate.py, so the early answer is asked only where neither
     can fire: never on a DIRTY branch (the fix agent must still be sent),
     never on a dependabot/* branch (condition D needs the commits). The rule
     lives in merge_gate.py (`precheck`), and its parity test proves it never
     answers differently from the full decision.

Run: python3 -m pytest tests/test_merge_gate_one_read.py -v
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

import merge_gate  # noqa: E402

PR = 4242
HEAD = "1f" * 20
REPO = "dreadnought-foundry/example"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
# No card id in the branch, so no run here reaches Linear.
BRANCH = "agent/one-read-gate"
DEPENDABOT_BRANCH = "dependabot/pip/requests-2.32.4"
APPROVE = f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}"

VIEW_FIELDS = (
    "headRefName", "state", "mergeStateStatus", "isDraft", "headRefOid",
    "baseRefName", "body", "createdAt", "author", "url",
)
# The fields a decision is made on: absent or null stops the step.
HARD_FIELDS = (
    "state", "headRefName", "mergeStateStatus", "isDraft", "headRefOid",
    "baseRefName", "author",
)

PATCH_COMMIT = {
    "sha": HEAD,
    "commit": {"message": (
        "Bump requests from 2.32.3 to 2.32.4\n\n---\nupdated-dependencies:\n"
        "- dependency-name: requests\n  dependency-type: direct:production\n"
        "  update-type: version-update:semver-patch\n...\n"
    )},
}

GREEN_CI = [{"name": "unit", "status": "completed", "conclusion": "success",
             "check_suite": {"id": 1}}]
CI_RUN_DONE = {"id": 11, "name": "CI", "path": ".github/workflows/ci.yml",
               "event": "pull_request", "status": "completed",
               "check_suite_id": 1}

# A stub `gh` that answers from a fixture file and logs every invocation.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def fail(msg):
    sys.stderr.write(msg + "\n")
    raise SystemExit(1)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def comments():
    return json.load(open(os.environ["COMMENTS"]))


if args[:2] == ["pr", "view"]:
    if fx.get("view_fails"):
        fail("HTTP 502: Bad Gateway (https://api.github.com/graphql)")
    view = fx["view"]
    if opt("--jq"):
        # The merge-refusal re-read of the head (DRE-2117).
        emit(str(fx.get("head_after_merge", view["headRefOid"])))
    emit({f: view[f] for f in opt("--json").split(",") if f in view})

if args[:2] == ["pr", "merge"]:
    if fx.get("merge_error"):
        fail(fx["merge_error"])
    emit("merged")

if args[:2] == ["run", "list"]:
    emit([])

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
        emit({"check_runs": fx["check_runs"]})
    if "/compare/" in path:
        emit(fx.get("compare", {"status": "ahead", "files": [{"filename": "a.py"}],
                                "commits": [{"sha": fx["view"]["headRefOid"]}]}))
    if "actions/runs" in path:
        if fx.get("workflow_runs") == "unreadable":
            fail("HTTP 502")
        emit({"workflow_runs": fx.get("workflow_runs", [])})
    if path.endswith("/commits?per_page=100"):
        emit(fx.get("pr_commits", [{"sha": fx["view"]["headRefOid"]}]))
    if "/comments" in path:
        if "--slurp" in args:
            emit([comments()])
        emit(comments() if path.endswith("page=1") or "page=" not in path else [])
    if "pulls?state=open" in path:
        emit(json.dumps({"number": int(os.environ["PR"]),
                         "head_sha": fx["view"]["headRefOid"]}))
    if "/rules/branches/" in path:
        emit([[]])
    if "/contents/" in path:
        fail("HTTP 404: Not Found")
    if path.endswith("/pulls/" + os.environ["PR"]):
        # The REST author read the one view replaced (DRE-2039).
        emit(fx.get("rest_author", "agent-bureau-bot[bot]"))
    if path == "repos/" + os.environ["REPO_FULL"]:
        emit("main")
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def view(**overrides) -> dict:
    """gh's own rendering of one open, ready agent pull request."""
    record = {
        "headRefName": BRANCH,
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "headRefOid": HEAD,
        "baseRefName": "main",
        "body": "What's new: none",
        "createdAt": "2026-09-01T17:00:00Z",
        "author": {"is_bot": True, "login": "app/agent-bureau-bot"},
        "url": f"https://github.com/{REPO}/pull/{PR}",
    }
    record.update(overrides)
    return record


class Run:
    def __init__(self, proc, calls, comments):
        self.proc = proc
        self.calls = calls
        self.comments = comments

    def explain(self) -> str:
        return (f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}\n"
                f"calls:\n" + "\n".join(map(str, self.calls)))

    @property
    def views(self):
        return [c for c in self.calls if c[:2] == ["pr", "view"]]

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    def api_paths(self):
        return [next(a for a in c[1:] if "/" in a and not a.startswith("-"))
                for c in self.calls if c[:1] == ["api"]]

    def before_merge(self):
        out = []
        for c in self.calls:
            if c[:2] == ["pr", "merge"]:
                break
            out.append(c)
        return out

    def decision(self) -> str:
        lines = [ln for ln in self.proc.stdout.splitlines() if ln.startswith("decision=")]
        return lines[0].split("=", 1)[1] if lines else ""


def run_gate(fixture: dict, seed=(APPROVE,)) -> Run:
    """Run scripts/evaluate_and_merge.sh against the stub — the file's own
    text, with its fixed `/tmp/` records moved into this run's directory, so
    two suites running at once on one machine cannot read each other's
    records."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "scratch").mkdir()
        script = td / "evaluate_and_merge.sh"
        script.write_text(SCRIPT.read_text().replace("/tmp/", f"{td / 'scratch'}/"))
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        os.symlink(ROOT, td / ".bureau-pipeline")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps([
            {"id": i + 1, "user": {"login": QA_LOGIN}, "body": body}
            for i, body in enumerate(seed)
        ]))
        (td / "gh.log").write_text("")
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own script
            ["bash", str(script)],
            cwd=td, capture_output=True, text=True,
            env={
                **os.environ,
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
            },
        )
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
        comments = json.loads((td / "comments.json").read_text())
    return Run(proc, calls, comments)


def green_fixture(**view_overrides) -> dict:
    return {"view": view(**view_overrides), "check_runs": GREEN_CI,
            "workflow_runs": [CI_RUN_DONE]}


# --------------------------------------------------------------------------
# 1. One read of the pull request
# --------------------------------------------------------------------------
class OneReadOfThePullRequestTest(unittest.TestCase):
    """A ready, approved, green pull request: the whole path to the merge."""

    @classmethod
    def setUpClass(cls):
        cls.run = run_gate(green_fixture())

    def test_it_merges(self):
        self.assertEqual(self.run.proc.returncode, 0, self.run.explain())
        self.assertEqual(self.run.decision(), "merge", self.run.explain())
        self.assertEqual(len(self.run.merges), 1, self.run.explain())
        self.assertIn(HEAD, self.run.merges[0])

    def test_the_pull_request_is_read_once(self):
        self.assertEqual(len(self.run.views), 1, self.run.explain())

    def test_the_one_read_asks_for_every_field_the_gate_uses(self):
        asked = self.run.views[0][self.run.views[0].index("--json") + 1]
        self.assertEqual(sorted(asked.split(",")), sorted(VIEW_FIELDS))

    def test_the_rest_author_read_is_gone(self):
        self.assertNotIn(f"repos/{REPO}/pulls/{PR}", self.run.api_paths(),
                         self.run.explain())

    def test_nine_github_reads_reach_the_merge(self):
        """The view, check runs, runs listing, compare, comments, commits, the
        fix lane, the open pull requests and the base branch's rules — nine
        where sixteen were before (seven views and the REST author read)."""
        self.assertEqual(len(self.run.before_merge()), 9, self.run.explain())

    def test_the_card_comment_link_comes_from_the_one_read(self):
        """The url the merge comment carries is in the view; no read after
        the merge (the old script re-read it for the Linear comment)."""
        after = self.run.calls[self.run.calls.index(self.run.merges[0]) + 1:]
        self.assertEqual([c for c in after if c[:2] == ["pr", "view"]], [])


class TheMergeRefusalStillRereadsTheHeadTest(unittest.TestCase):
    """DRE-2117's race guard is the one read that must be FRESH: it asks
    whether the head moved after the evaluation. It stays."""

    def test_an_unmoved_head_is_a_real_failure(self):
        fx = green_fixture()
        fx["merge_error"] = "GraphQL: Base branch was modified (mergePullRequest)"
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertIn("real failure", run.proc.stdout)
        self.assertEqual(len(run.views), 2, run.explain())

    def test_a_moved_head_exits_clean(self):
        fx = green_fixture()
        fx["merge_error"] = "GraphQL: Head branch was modified (mergePullRequest)"
        fx["head_after_merge"] = "2e" * 20
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("head moved since evaluation", run.proc.stdout)


class EveryReadFailsTheWayItDidTest(unittest.TestCase):
    def test_a_failed_read_kills_the_step_before_any_other_call(self):
        fx = green_fixture()
        fx["view_fails"] = True
        run = run_gate(fx)
        self.assertNotEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.calls), 1, run.explain())
        self.assertEqual(run.merges, [])

    def test_a_missing_decision_field_kills_the_step(self):
        """`--jq .isDraft` on a failed read died; a record with no isDraft
        must die too, and before anything else is read or written. Defaulting
        it to "not a draft" is DRE-3467 re-armed."""
        for field in HARD_FIELDS:
            for value in ("absent", None):
                with self.subTest(field=field, value=value):
                    record = view()
                    if value == "absent":
                        del record[field]
                    else:
                        record[field] = None
                    run = run_gate({"view": record, "check_runs": GREEN_CI,
                                    "workflow_runs": [CI_RUN_DONE]})
                    self.assertNotEqual(run.proc.returncode, 0, run.explain())
                    self.assertEqual(len(run.calls), 1, run.explain())
                    self.assertIn(field, run.proc.stderr)

    def test_the_body_and_creation_time_stay_soft(self):
        """DRE-5511: a body nobody could read is no body, and condition W
        stays off — the pull request still merges."""
        for overrides in ({"body": None, "createdAt": None}, {}):
            with self.subTest(overrides=overrides):
                record = view(**overrides)
                if not overrides:
                    del record["body"], record["createdAt"]
                run = run_gate({"view": record, "check_runs": GREEN_CI,
                                "workflow_runs": [CI_RUN_DONE]})
                self.assertEqual(run.proc.returncode, 0, run.explain())
                self.assertEqual(run.decision(), "merge", run.explain())

    def test_a_closed_pull_request_stops_after_the_one_read(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                run = run_gate(green_fixture(state=state))
                self.assertEqual(run.proc.returncode, 0, run.explain())
                self.assertIn("nothing to do", run.proc.stdout)
                self.assertEqual(len(run.calls), 1, run.explain())

    def test_a_branch_the_pipeline_does_not_own_stops_after_the_one_read(self):
        run = run_gate(green_fixture(headRefName="feature/by-hand"))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("not an agent branch", run.proc.stdout)
        self.assertEqual(len(run.calls), 1, run.explain())


class TheAuthorIsSpelledTheRestWayTest(unittest.TestCase):
    """Condition D compares the author with `dependabot[bot]`, REST's
    spelling (DRE-2039). gh renders a bot as `app/<slug>`; the gate turns it
    back. A person's login can hold no slash, so `app/` cannot be forged."""

    def dependabot(self, author: dict) -> Run:
        return run_gate({
            "view": view(headRefName=DEPENDABOT_BRANCH, author=author),
            "check_runs": GREEN_CI, "workflow_runs": [CI_RUN_DONE],
            "pr_commits": [PATCH_COMMIT],
        })

    def test_every_bot_rendering_is_the_real_dependabot(self):
        for author in ({"is_bot": True, "login": "app/dependabot"},
                       {"is_bot": True, "login": "dependabot"},
                       {"login": "dependabot[bot]"}):
            with self.subTest(author=author):
                run = self.dependabot(author)
                self.assertEqual(run.decision(), "merge", run.explain())

    def test_a_person_is_not_dependabot(self):
        run = self.dependabot({"is_bot": False, "login": "dependabot-fan"})
        self.assertEqual(run.decision(), "human", run.explain())
        self.assertIn("'dependabot-fan'", run.proc.stdout)
        self.assertEqual(run.merges, [])


# --------------------------------------------------------------------------
# 2. The early answer while CI runs
# --------------------------------------------------------------------------
SLOW_CI = GREEN_CI + [{"name": "e2e", "status": "in_progress", "conclusion": None,
                       "check_suite": {"id": 1}}]
CI_RUN_LIVE = dict(CI_RUN_DONE, status="in_progress")
CRITIC_CHECK_LIVE = {"name": "call / review", "status": "in_progress",
                     "conclusion": None, "check_suite": {"id": 2}}
CRITIC_RUN_LIVE = {"id": 12, "name": "QA Review",
                   "path": ".github/workflows/qa-review.yml",
                   "event": "pull_request", "status": "in_progress",
                   "check_suite_id": 2}
LATE_READS = ("/compare/", "/comments", "/commits?", "pulls?state=open",
              "/rules/branches/")


class TheGateStopsEarlyWhileCiRunsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run = run_gate({"view": view(), "check_runs": SLOW_CI,
                            "workflow_runs": [CI_RUN_LIVE]})

    def test_it_waits_and_says_why(self):
        self.assertEqual(self.run.proc.returncode, 0, self.run.explain())
        self.assertEqual(self.run.decision(), "wait", self.run.explain())
        self.assertIn("not green", self.run.proc.stdout)

    def test_it_reads_only_the_view_the_checks_and_the_runs(self):
        self.assertEqual(len(self.run.calls), 3, self.run.explain())
        paths = self.run.api_paths()
        self.assertTrue(any("check-runs" in p for p in paths))
        self.assertTrue(any("actions/runs" in p for p in paths))
        for late in LATE_READS:
            self.assertFalse(any(late in p for p in paths), (late, paths))
        self.assertNotIn(["run", "list"], [c[:2] for c in self.run.calls])

    def test_it_writes_nothing(self):
        self.assertEqual(self.run.merges, [])
        self.assertEqual(len(self.run.comments), 1)

    def test_an_unreadable_runs_listing_still_waits_early(self):
        """Condition 1 waits on an unreadable listing (DRE-5045) — the same
        answer, the same record, the same early stop."""
        run = run_gate({"view": view(), "check_runs": GREEN_CI,
                        "workflow_runs": "unreadable"})
        self.assertEqual(run.decision(), "wait", run.explain())
        self.assertEqual(len(run.calls), 3, run.explain())


class TheEarlyAnswerNeverChangesADecisionTest(unittest.TestCase):
    def test_a_critic_still_reviewing_does_not_stop_the_gate(self):
        """The critic's own check is excluded from condition 1 by verified
        origin (DRE-1994), which only the runs listing can tell — so an
        unfinished REVIEW check is no reason to stop, and the gate goes on to
        merge on the standing APPROVE exactly as before."""
        run = run_gate({"view": view(),
                        "check_runs": GREEN_CI + [CRITIC_CHECK_LIVE],
                        "workflow_runs": [CI_RUN_DONE, CRITIC_RUN_LIVE]})
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertTrue(any("/compare/" in p for p in run.api_paths()))

    def test_a_conflicted_branch_still_sends_the_fix_agent(self):
        """Condition 0 comes before condition 1: DIRTY is `conflict` whatever
        CI says, and the conflict arm dispatches the fix agent."""
        run = run_gate({"view": view(mergeStateStatus="DIRTY"),
                        "check_runs": SLOW_CI, "workflow_runs": [CI_RUN_LIVE]})
        self.assertEqual(run.decision(), "conflict", run.explain())
        dispatched = [c for c in run.calls if c[:2] == ["workflow", "run"]]
        self.assertEqual(len(dispatched), 1, run.explain())
        self.assertIn("agent-fix.yml", dispatched[0])

    def test_a_dependabot_branch_takes_the_full_path(self):
        """Condition D comes before condition 1 and needs the commits, so a
        dependabot/* branch is never answered early: here a person-authored
        one gets condition D's `human`, not condition 1's `wait`."""
        run = run_gate({
            "view": view(headRefName=DEPENDABOT_BRANCH,
                         author={"is_bot": False, "login": "octocat"}),
            "check_runs": SLOW_CI, "workflow_runs": [CI_RUN_LIVE],
            "pr_commits": [PATCH_COMMIT],
        })
        self.assertEqual(run.decision(), "human", run.explain())
        self.assertTrue(any(p.endswith("/commits?per_page=100") for p in run.api_paths()))


class PrecheckRuleTest(unittest.TestCase):
    """merge_gate.precheck: conditions 0 → D → 1, answered from the two
    records condition 1 reads, and nothing else."""

    def precheck(self, check_runs, workflow_runs, merge_state="CLEAN",
                 head_branch=BRANCH):
        return merge_gate.precheck(check_runs, workflow_runs,
                                   merge_state=merge_state,
                                   head_branch=head_branch)

    def test_unfinished_ci_waits(self):
        d = self.precheck(SLOW_CI, [CI_RUN_LIVE])
        self.assertEqual(d.action, "wait")

    def test_a_queued_run_with_no_checks_yet_waits(self):
        d = self.precheck(GREEN_CI, [CI_RUN_DONE, dict(CI_RUN_LIVE, id=13, check_suite_id=3)])
        self.assertEqual(d.action, "wait")

    def test_no_checks_yet_waits(self):
        self.assertEqual(self.precheck([], []).action, "wait")

    def test_an_unreadable_listing_waits(self):
        self.assertEqual(self.precheck(GREEN_CI, None).action, "wait")

    def test_green_ci_goes_on(self):
        self.assertIsNone(self.precheck(GREEN_CI, [CI_RUN_DONE]))

    def test_a_review_still_running_goes_on(self):
        self.assertIsNone(self.precheck(GREEN_CI + [CRITIC_CHECK_LIVE],
                                        [CI_RUN_DONE, CRITIC_RUN_LIVE]))

    def test_a_conflict_goes_on(self):
        self.assertIsNone(self.precheck(SLOW_CI, [CI_RUN_LIVE], merge_state="DIRTY"))

    def test_a_dependabot_branch_goes_on(self):
        self.assertIsNone(self.precheck(SLOW_CI, [CI_RUN_LIVE],
                                        head_branch=DEPENDABOT_BRANCH))

    def test_every_early_answer_is_the_full_decisions_answer(self):
        """Parity: wherever precheck answers, merge_gate.decide — given the
        same two records and anything at all for the rest — answers the same
        action and the same reason."""
        cases = [
            (SLOW_CI, [CI_RUN_LIVE]),
            (GREEN_CI, [CI_RUN_DONE, dict(CI_RUN_LIVE, id=13, check_suite_id=3)]),
            ([], []),
            (GREEN_CI, None),
            (GREEN_CI + [CRITIC_CHECK_LIVE], [CI_RUN_LIVE, CRITIC_RUN_LIVE]),
        ]
        rests = [
            {"comments": [], "is_draft": False},
            {"comments": [{"user": {"login": QA_LOGIN}, "body": APPROVE}],
             "is_draft": True},
        ]
        paths = frozenset(merge_gate.DEFAULT_REVIEW_WORKFLOWS)
        for check_runs, workflow_runs in cases:
            early = self.precheck(check_runs, workflow_runs)
            self.assertIsNotNone(early, (check_runs, workflow_runs))
            suites = (frozenset() if workflow_runs is None
                      else merge_gate.review_suite_ids(workflow_runs, paths))
            unfinished = merge_gate.unfinished_runs(workflow_runs, paths)
            for rest in rests:
                full = merge_gate.decide(
                    HEAD, QA_LOGIN, check_runs, rest["comments"], suites,
                    head_branch=BRANCH, merge_state="CLEAN",
                    is_draft=rest["is_draft"], unfinished_runs=unfinished,
                )
                self.assertEqual((early.action, early.reason),
                                 (full.action, full.reason))

    def test_the_cli_says_wait_or_evaluate(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "checks.json").write_text(json.dumps({"check_runs": SLOW_CI}))
            (td / "runs.json").write_text(json.dumps({"workflow_runs": [CI_RUN_LIVE]}))
            (td / "unreadable.json").write_text(merge_gate.UNREADABLE_WORKFLOW_RUNS)
            (td / "green.json").write_text(json.dumps({"check_runs": GREEN_CI}))
            (td / "done.json").write_text(json.dumps({"workflow_runs": [CI_RUN_DONE]}))

            def cli(checks, runs):
                return subprocess.run(  # nosec B603 — fixed argv, our own script
                    [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
                     "precheck", "--check-runs-file", str(td / checks),
                     "--workflow-runs-file", str(td / runs),
                     "--merge-state", "CLEAN", "--head-branch", BRANCH],
                    capture_output=True, text=True, check=False,
                )

            waits = cli("checks.json", "runs.json")
            self.assertEqual(waits.returncode, 0, waits.stderr)
            self.assertIn("precheck=wait", waits.stdout.splitlines())
            self.assertTrue(any(ln.startswith("reason=") for ln in waits.stdout.splitlines()))
            self.assertIn("precheck=wait",
                          cli("green.json", "unreadable.json").stdout.splitlines())
            self.assertIn("precheck=evaluate",
                          cli("green.json", "done.json").stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
