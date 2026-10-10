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
sys.path.insert(0, str(ROOT / "tests"))

import merge_gate  # noqa: E402
from test_check_runs_read_is_whole import pages, runs_143  # noqa: E402

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
    "baseRefName", "body", "createdAt", "author", "url", "files",
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
    # One answer per attempt, in order; past the list GitHub accepts (DRE-6242).
    tries = sum(1 for ln in open(os.environ["GH_LOG"]) if json.loads(ln)[:2] == ["pr", "merge"])
    errors = fx.get("merge_errors", [])
    if tries <= len(errors):
        fail(errors[tries - 1])
    emit("merged")

if args[:2] == ["run", "list"]:
    emit([])

if args[:2] == ["workflow", "run"]:
    emit("")

if args[0] == "api":
    method = opt("--method") or opt("-X") or "GET"
    path = [a for a in args[1:] if "/" in a and not a.startswith("-")][0]
    if method == "PUT" and path.endswith("/update-branch"):
        # The out-of-date refusal's one update (DRE-6195).
        if fx.get("update_branch_error"):
            fail(fx["update_branch_error"])
        emit({"message": "Updating pull request branch.",
              "url": "https://github.com/" + os.environ["REPO_FULL"]})
    if method == "POST":
        rows = comments()
        body = json.loads(sys.stdin.read())["body"]
        row = {"id": 900 + len(rows), "user": {"login": os.environ["QA_LOGIN"]},
               "body": body}
        rows.append(row)
        json.dump(rows, open(os.environ["COMMENTS"], "w"))
        emit(row)
    if "check-runs" in path:
        if fx.get("check_runs_fail"):
            fail("HTTP 502: Bad Gateway")
        # Every page for `--paginate --slurp`, GitHub's first page for any
        # other read (DRE-6532).
        pages = fx.get("check_run_pages") or [
            {"total_count": len(fx["check_runs"]), "check_runs": fx["check_runs"]}]
        emit(pages if "--slurp" in args else pages[0])
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
    if "/contents/architecture/" in path and "record" in fx:
        # The proof record at the head (DRE-6141), base64 as the API serves it.
        import base64
        emit({"encoding": "base64",
              "content": base64.b64encode(fx["record"].encode()).decode()})
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


# A stand-in for linear_ops.py that logs every card write and touches no
# network, for the runs whose branch names a card (DRE-6195).
LINEAR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["LINEAR_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
'''


def _shadow_pipeline(td: Path) -> None:
    """`.bureau-pipeline` as the real checkout, entry by entry, with only
    scripts/linear_ops.py swapped for LINEAR_STUB."""
    pipeline = td / ".bureau-pipeline"
    pipeline.mkdir()
    for entry in ROOT.iterdir():
        if entry.name != "scripts":
            os.symlink(entry, pipeline / entry.name)
    (pipeline / "scripts").mkdir()
    for entry in (ROOT / "scripts").iterdir():
        if entry.name != "linear_ops.py":
            os.symlink(entry, pipeline / "scripts" / entry.name)
    (pipeline / "scripts" / "linear_ops.py").write_text(LINEAR_STUB)


def run_gate(fixture: dict, seed=(APPROVE,), linear_stub=False) -> Run:
    """Run scripts/evaluate_and_merge.sh against the stub — the file's own
    text, with its fixed `/tmp/` records moved into this run's directory, so
    two suites running at once on one machine cannot read each other's
    records. `linear_stub` swaps linear_ops.py for a logger, so a branch
    naming a card can run here; its calls land in `Run.linear`."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "scratch").mkdir()
        script = td / "evaluate_and_merge.sh"
        script.write_text(SCRIPT.read_text().replace("/tmp/", f"{td / 'scratch'}/"))
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        if linear_stub:
            _shadow_pipeline(td)
        else:
            os.symlink(ROOT, td / ".bureau-pipeline")
        (td / "linear.log").write_text("")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps([
            {"id": i + 1, "user": {"login": QA_LOGIN}, "body": body}
            if isinstance(body, str) else {"id": i + 1, **body}
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
                "LINEAR_LOG": str(td / "linear.log"),
                "MERGE_RETRY_SECONDS": "0",
            },
        )
        calls = [json.loads(ln) for ln in (td / "gh.log").read_text().splitlines() if ln]
        comments = json.loads((td / "comments.json").read_text())
        linear = [json.loads(ln) for ln in (td / "linear.log").read_text().splitlines() if ln]
    run = Run(proc, calls, comments)
    run.linear = linear
    return run


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
        cls.gate =run_gate(green_fixture())

    def test_it_merges(self):
        self.assertEqual(self.gate.proc.returncode, 0, self.gate.explain())
        self.assertEqual(self.gate.decision(), "merge", self.gate.explain())
        self.assertEqual(len(self.gate.merges), 1, self.gate.explain())
        self.assertIn(HEAD, self.gate.merges[0])

    def test_the_pull_request_is_read_once(self):
        self.assertEqual(len(self.gate.views), 1, self.gate.explain())

    def test_the_one_read_asks_for_every_field_the_gate_uses(self):
        asked = self.gate.views[0][self.gate.views[0].index("--json") + 1]
        self.assertEqual(sorted(asked.split(",")), sorted(VIEW_FIELDS))

    def test_the_rest_author_read_is_gone(self):
        self.assertNotIn(f"repos/{REPO}/pulls/{PR}", self.gate.api_paths(),
                         self.gate.explain())

    def test_nine_github_reads_reach_the_merge(self):
        """The view, check runs, runs listing, compare, comments, commits, the
        fix lane, the open pull requests and the base branch's rules — nine
        where sixteen were before (seven views and the REST author read)."""
        self.assertEqual(len(self.gate.before_merge()), 9, self.gate.explain())

    def test_the_card_comment_link_comes_from_the_one_read(self):
        """The url the merge comment carries is in the view; no read after
        the merge (the old script re-read it for the Linear comment)."""
        after = self.gate.calls[self.gate.calls.index(self.gate.merges[0]) + 1:]
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
        self.assertEqual(update_branch_puts(run), [], run.explain())


# --------------------------------------------------------------------------
# 1a. GitHub's out-of-date refusal: one update per base tip (DRE-6195)
# --------------------------------------------------------------------------
# GitHub's own words, run 37691164107 on bureau-pipeline #785.
OUT_OF_DATE = ("GraphQL: Head branch is out of date. Review and try the merge "
               "again. (mergePullRequest)")
BASE_TIP = "3c" * 20
# #785's compare record: 39 behind, 4 ahead. A note, never a trigger.
DIVERGED = {
    "status": "diverged", "behind_by": 39, "ahead_by": 4,
    "files": [{"filename": "a.py", "status": "modified"}],
    "commits": [{"sha": HEAD}],
    "base_commit": {"sha": BASE_TIP},
    "merge_base_commit": {"sha": "4d" * 20},
}
UPDATE_MARK = f"Merge gate: updated onto {BASE_TIP}"
CARD_BRANCH = "agent/DRE-5746-groomer-owner-tokens-proofs"
VERDICT_SHAPES = ("VERDICT:", "QA Critic", "QA Verifier")


def update_branch_puts(run: Run) -> list:
    return [c for c in run.calls
            if c[:1] == ["api"] and "PUT" in c
            and any(a.endswith("/update-branch") for a in c)]


def update_notes(run: Run, tip: str = BASE_TIP) -> list:
    return [c["body"] for c in run.comments
            if merge_gate.opens_with_marker(c["body"],
                                            f"Merge gate: updated onto {tip}")]


def refused_fixture(merge_error: str = OUT_OF_DATE, compare=None,
                    branch: str = BRANCH, **extra) -> dict:
    fx = {"view": view(headRefName=branch), "check_runs": GREEN_CI,
          "workflow_runs": [CI_RUN_DONE],
          "compare": DIVERGED if compare is None else compare,
          "merge_error": merge_error}
    fx.update(extra)
    return fx


class TheOutOfDateRefusalUpdatesTheBranchOnceTest(unittest.TestCase):
    """`gh pr merge` refused with `Head branch is out of date`, the head
    unmoved: the gate updates the branch from its base once per base tip,
    pinned to the evaluated head, says so once, and exits 0. Not a merge."""

    @classmethod
    def setUpClass(cls):
        cls.gate = run_gate(refused_fixture(branch=CARD_BRANCH), linear_stub=True)

    def test_it_exits_clean(self):
        self.assertEqual(self.gate.proc.returncode, 0, self.gate.explain())
        self.assertEqual(self.gate.decision(), "merge", self.gate.explain())
        self.assertEqual(len(self.gate.merges), 1, self.gate.explain())
        self.assertNotIn("real failure", self.gate.proc.stdout)

    def test_exactly_one_update_pinned_to_the_evaluated_head(self):
        puts = update_branch_puts(self.gate)
        self.assertEqual(len(puts), 1, self.gate.explain())
        self.assertIn(f"repos/{REPO}/pulls/{PR}/update-branch", puts[0])
        self.assertIn(f"expected_head_sha={HEAD}", puts[0])

    def test_the_update_comes_after_the_refusal_and_the_head_reread(self):
        calls = self.gate.calls
        put = calls.index(update_branch_puts(self.gate)[0])
        self.assertLess(calls.index(self.gate.merges[0]), put)
        self.assertLess(calls.index(self.gate.views[1]), put)

    def test_exactly_one_note_naming_the_tip(self):
        notes = update_notes(self.gate)
        self.assertEqual(len(notes), 1, self.gate.comments)
        first = notes[0].splitlines()[0]
        self.assertIn(UPDATE_MARK, first)
        self.assertIn("Head branch is out of date", notes[0])
        self.assertIn("main", notes[0])
        for shape in VERDICT_SHAPES:
            self.assertNotIn(shape, notes[0])

    def test_it_is_not_a_merge(self):
        self.assertNotIn("merged PR", self.gate.proc.stdout)
        comments = [c for c in self.gate.linear if c[:1] == ["comment"]]
        self.assertEqual(comments, [], self.gate.linear)
        self.assertFalse(any("Auto-merged" in " ".join(c) for c in self.gate.linear))

    def test_the_card_stub_is_live(self):
        """The no-card-comment assertion above is not vacuous: the same
        branch, merged, comments the card through the same stub."""
        fx = refused_fixture(branch=CARD_BRANCH)
        del fx["merge_error"]
        run = run_gate(fx, linear_stub=True)
        self.assertIn("merged PR", run.proc.stdout, run.explain())
        self.assertTrue(any(c[:1] == ["comment"] and "Auto-merged" in " ".join(c)
                            for c in run.linear), run.linear)
        self.assertEqual(update_branch_puts(run), [])


class TheOutOfDateUpdateIsBoundedTest(unittest.TestCase):
    def test_a_tip_already_updated_onto_is_a_real_failure(self):
        note = f"♻️ {UPDATE_MARK} — GitHub refused the merge earlier"
        run = run_gate(refused_fixture(), seed=(APPROVE, note))
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertEqual(update_branch_puts(run), [], run.explain())
        self.assertEqual(len(update_notes(run)), 1, run.comments)
        self.assertIn("already updated onto", run.proc.stdout)
        self.assertIn("real failure", run.proc.stdout)

    def test_a_note_for_another_tip_does_not_bound_this_one(self):
        """The bound is per tip: `main` moved, so the gate updates again."""
        old = f"♻️ Merge gate: updated onto {'5e' * 20} — earlier"
        run = run_gate(refused_fixture(), seed=(APPROVE, old))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(update_branch_puts(run)), 1, run.explain())
        self.assertEqual(len(update_notes(run)), 1, run.comments)

    def test_a_person_quoting_the_marker_is_not_the_gates_note(self):
        """Only the gate's own note bounds it (gate_note.matching_notes)."""
        quoted = {"user": {"login": "octocat"}, "body": f"♻️ {UPDATE_MARK} — quoted"}
        run = run_gate(refused_fixture(), seed=(APPROVE, quoted))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(update_branch_puts(run)), 1, run.explain())

    def test_no_base_tip_is_a_real_failure(self):
        """The `{}` blip: without the tip the loop cannot be bounded."""
        run = run_gate(refused_fixture(compare={}))
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertEqual(update_branch_puts(run), [], run.explain())
        self.assertEqual([c for c in run.comments if "updated onto" in c["body"]], [])
        self.assertIn("real failure", run.proc.stdout)

    def test_a_refused_update_is_a_real_failure(self):
        refusal = "HTTP 422: expected_head_sha does not match the pull request head"
        run = run_gate(refused_fixture(update_branch_error=refusal))
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertEqual(len(update_branch_puts(run)), 1, run.explain())
        self.assertEqual(update_notes(run), [], run.comments)
        self.assertEqual(len(run.merges), 1, run.explain())
        self.assertNotIn("merged PR", run.proc.stdout)
        self.assertIn(refusal, run.proc.stdout + run.proc.stderr)
        self.assertIn("real failure", run.proc.stdout)

    def test_the_out_of_date_text_with_a_moved_head_is_the_moved_head_case(self):
        run = run_gate(refused_fixture(head_after_merge="2e" * 20))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("head moved since evaluation", run.proc.stdout)
        self.assertEqual(update_branch_puts(run), [], run.explain())


class EveryOtherRefusalIsStillARealFailureTest(unittest.TestCase):
    def test_other_refusal_text_never_updates(self):
        for refusal in ("GraphQL: Base branch was modified (mergePullRequest)",
                        "GraphQL: Pull Request is still a draft (mergePullRequest)",
                        "HTTP 403: Resource not accessible by integration"):
            with self.subTest(refusal=refusal):
                run = run_gate(refused_fixture(merge_error=refusal))
                self.assertEqual(run.proc.returncode, 1, run.explain())
                self.assertIn("real failure", run.proc.stdout)
                self.assertEqual(update_branch_puts(run), [], run.explain())
                self.assertEqual(update_notes(run), [], run.comments)

    def test_a_diverged_branch_github_accepts_merges_as_it_stands(self):
        """The compare status is a note, never the trigger (DRE-2416)."""
        fx = refused_fixture()
        del fx["merge_error"]
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("merged PR", run.proc.stdout)
        self.assertEqual(update_branch_puts(run), [], run.explain())

    def test_a_refusal_is_never_retried(self):
        """Only GitHub's own server error earns the second try (DRE-6242)."""
        run = run_gate(refused_fixture(
            merge_error="GraphQL: Base branch was modified (mergePullRequest)"))
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertEqual(len(run.merges), 1, run.explain())


# --------------------------------------------------------------------------
# 1b. GitHub's own server error is not a refusal: one more try (DRE-6242)
# --------------------------------------------------------------------------
# GitHub's own words, sandbox merge gate run 37703550700 on bureau-harness
# #3556: green, approved, decision=merge, and the merge call answered this.
# Nothing in the sandbox re-runs a red gate, so the pull request sat until
# main's harness run timed out at 1200s.
SERVER_ERROR = ("GraphQL: Something went wrong while executing your query on "
                "2026-10-07T23:40:21Z. Please include "
                "`2437:36E9BD:398876:BD2C5E:6AC6D860` when reporting this issue.")


class GitHubsServerErrorIsTriedOnceMoreTest(unittest.TestCase):
    def test_a_second_try_github_accepts_is_a_merge(self):
        fx = green_fixture()
        fx["merge_errors"] = [SERVER_ERROR]
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("merged PR", run.proc.stdout, run.explain())
        self.assertNotIn("real failure", run.proc.stdout)
        self.assertEqual(len(run.merges), 2, run.explain())

    def test_the_second_try_is_pinned_to_the_evaluated_head(self):
        fx = green_fixture()
        fx["merge_errors"] = [SERVER_ERROR]
        run = run_gate(fx)
        for merge in run.merges:
            self.assertEqual(merge[merge.index("--match-head-commit") + 1], HEAD,
                             run.explain())

    def test_the_second_try_comes_after_the_head_reread(self):
        """The race guard (DRE-2117) still answers first: a second try is
        made only for a head that has not moved."""
        fx = green_fixture()
        fx["merge_errors"] = [SERVER_ERROR]
        run = run_gate(fx)
        views = [i for i, c in enumerate(run.calls) if c[:2] == ["pr", "view"]]
        merges = [i for i, c in enumerate(run.calls) if c[:2] == ["pr", "merge"]]
        self.assertEqual(len(views), 2, run.explain())
        self.assertLess(merges[0], views[1], run.explain())
        self.assertLess(views[1], merges[1], run.explain())

    def test_only_one_more_try(self):
        fx = green_fixture()
        fx["merge_errors"] = [SERVER_ERROR, SERVER_ERROR]
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 1, run.explain())
        self.assertIn("real failure", run.proc.stdout)
        self.assertNotIn("merged PR", run.proc.stdout)
        self.assertEqual(len(run.merges), 2, run.explain())

    def test_a_moved_head_is_never_tried_again(self):
        fx = green_fixture()
        fx["merge_errors"] = [SERVER_ERROR]
        fx["head_after_merge"] = "2e" * 20
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertIn("head moved since evaluation", run.proc.stdout)
        self.assertEqual(len(run.merges), 1, run.explain())

    def test_an_out_of_date_answer_on_the_second_try_updates_the_branch(self):
        """The second try's own answer is the one the arms below read."""
        run = run_gate(refused_fixture(merge_error=None,
                                       merge_errors=[SERVER_ERROR, OUT_OF_DATE]))
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(len(run.merges), 2, run.explain())
        self.assertEqual(len(update_branch_puts(run)), 1, run.explain())
        self.assertEqual(len(update_notes(run)), 1, run.comments)


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


# gh's `author` renderings of one bot, one fixture each (Stage 2 review G3,
# addition 41). gh 2.100.0 renders the first (read live 2026-10-02 against
# bureau-pipeline #677: `{"is_bot":true,"login":"app/agent-bureau-bot-4"}`,
# where REST says `agent-bureau-bot-4[bot]`); the other two are the shapes a
# different gh version could print.
AUTHOR_APP_X = {"is_bot": True, "login": "app/dependabot"}    # `app/x`
AUTHOR_X_IS_BOT = {"is_bot": True, "login": "dependabot"}     # `x` with is_bot
AUTHOR_BARE_X = {"login": "dependabot"}                       # bare `x`
AUTHOR_REST_X = {"login": "dependabot[bot]"}                  # REST's own spelling


class TheAuthorIsSpelledTheRestWayTest(unittest.TestCase):
    """Condition D compares the author with `dependabot[bot]`, REST's
    spelling (DRE-2039). gh renders a bot as `app/<slug>`; the gate turns it
    back. A person's login can hold no slash, so `app/` cannot be forged.
    A rendering that says nothing of a bot is NOT guessed at: it fails loud,
    as condition D's `human` — one note, never a merge."""

    def dependabot(self, author: dict) -> Run:
        return run_gate({
            "view": view(headRefName=DEPENDABOT_BRANCH, author=author),
            "check_runs": GREEN_CI, "workflow_runs": [CI_RUN_DONE],
            "pr_commits": [PATCH_COMMIT],
        })

    def test_app_x_is_dependabot(self):
        run = self.dependabot(AUTHOR_APP_X)
        self.assertEqual(run.decision(), "merge", run.explain())

    def test_x_with_is_bot_is_dependabot(self):
        run = self.dependabot(AUTHOR_X_IS_BOT)
        self.assertEqual(run.decision(), "merge", run.explain())

    def test_rests_own_spelling_is_dependabot(self):
        run = self.dependabot(AUTHOR_REST_X)
        self.assertEqual(run.decision(), "merge", run.explain())

    def test_bare_x_fails_loud_and_never_merges(self):
        run = self.dependabot(AUTHOR_BARE_X)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "human", run.explain())
        self.assertIn("'dependabot'", run.proc.stdout)
        self.assertEqual(run.merges, [])
        self.assertEqual(
            len([c for c in run.comments if "waiting for human merge" in c["body"]]), 1,
            run.comments,
        )

    def test_a_person_is_not_dependabot(self):
        run = self.dependabot({"is_bot": False, "login": "dependabot-fan"})
        self.assertEqual(run.decision(), "human", run.explain())
        self.assertIn("'dependabot-fan'", run.proc.stdout)
        self.assertEqual(run.merges, [])


# --------------------------------------------------------------------------
# 1b. A proof record is opened before it merges (DRE-6141)
# --------------------------------------------------------------------------
PROOF_BRANCH = "agent/DRE-5798-proof-record"
RECORD_PATH = "architecture/proofs/planners-at-once.md"
RECORD_FILES = [{"path": RECORD_PATH, "additions": 30, "deletions": 0,
                 "changeType": "ADDED"}]
NOT_OBSERVED = ("| Criterion | Result |\n|---|---|\n"
                "| Two planners run at once | Not observed. |\n")
ALL_MET = ("| Criterion | Result |\n|---|---|\n"
           "| Two planners run at once | Met — 09:01 to 09:04 PT |\n")


class AProofRecordIsReadOnceAtTheHeadTest(unittest.TestCase):
    def proof_run(self, record: str) -> Run:
        fx = green_fixture(headRefName=PROOF_BRANCH, files=RECORD_FILES)
        fx["record"] = record
        return run_gate(fx)

    def test_a_record_with_a_row_not_observed_is_held_not_merged(self):
        run = self.proof_run(NOT_OBSERVED)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "hold", run.explain())
        self.assertEqual(run.merges, [], run.explain())
        notes = [c["body"] for c in run.comments
                 if c["body"].startswith(f"⏸️ Merge gate: declined @{HEAD}")]
        self.assertEqual(len(notes), 1, run.comments)
        self.assertIn("Two planners run at once", notes[0])

    def test_a_record_all_met_merges_with_one_more_read(self):
        run = self.proof_run(ALL_MET)
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertEqual(len(run.views), 1, run.explain())
        self.assertIn(f"repos/{REPO}/contents/{RECORD_PATH}?ref={HEAD}",
                      run.api_paths())
        self.assertEqual(len(run.before_merge()), 10, run.explain())

    def test_off_a_proof_record_branch_the_record_is_never_read(self):
        fx = green_fixture(files=RECORD_FILES)
        fx["record"] = NOT_OBSERVED
        run = run_gate(fx)
        self.assertEqual(run.decision(), "merge", run.explain())
        self.assertFalse(any("/contents/architecture/" in p for p in run.api_paths()))
        self.assertEqual(len(run.before_merge()), 9, run.explain())


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
        cls.gate =run_gate({"view": view(), "check_runs": SLOW_CI,
                            "workflow_runs": [CI_RUN_LIVE]})

    def test_it_waits_and_says_why(self):
        self.assertEqual(self.gate.proc.returncode, 0, self.gate.explain())
        self.assertEqual(self.gate.decision(), "wait", self.gate.explain())
        self.assertIn("not green", self.gate.proc.stdout)

    def test_it_reads_only_the_view_the_checks_and_the_runs(self):
        self.assertEqual(len(self.gate.calls), 3, self.gate.explain())
        paths = self.gate.api_paths()
        self.assertTrue(any("check-runs" in p for p in paths))
        self.assertTrue(any("actions/runs" in p for p in paths))
        for late in LATE_READS:
            self.assertFalse(any(late in p for p in paths), (late, paths))
        self.assertNotIn(["run", "list"], [c[:2] for c in self.gate.calls])

    def test_it_writes_nothing(self):
        self.assertEqual(self.gate.merges, [])
        self.assertEqual(len(self.gate.comments), 1)

    def test_an_unreadable_runs_listing_still_waits_early(self):
        """Condition 1 waits on an unreadable listing (DRE-5045) — the same
        answer, the same record, the same early stop."""
        run = run_gate({"view": view(), "check_runs": GREEN_CI,
                        "workflow_runs": "unreadable"})
        self.assertEqual(run.decision(), "wait", run.explain())
        self.assertEqual(len(run.calls), 3, run.explain())


# --------------------------------------------------------------------------
# The check runs are read whole (DRE-6532)
# --------------------------------------------------------------------------
class TheCheckRunsAreReadWholeTest(unittest.TestCase):
    """GitHub pages a commit's check runs 30 at a time, newest first, so an
    unpaged read drops the oldest — the CI jobs. The gate's one read takes
    every page of 100, and a read that fails on any page stops the step."""

    @staticmethod
    def check_run_reads(run: Run) -> list:
        return [c for c in run.calls
                if c[:1] == ["api"] and any("check-runs" in a for a in c)]

    def test_it_is_one_call_for_every_page_of_100(self):
        run = run_gate(green_fixture())
        self.assertEqual(run.decision(), "merge", run.explain())
        reads = self.check_run_reads(run)
        self.assertEqual(len(reads), 1, run.explain())
        self.assertIn("--paginate", reads[0])
        self.assertIn("--slurp", reads[0])
        self.assertIn(f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100", reads[0])

    def test_a_red_job_on_the_second_page_is_not_merged(self):
        fx = green_fixture()
        fx["check_run_pages"] = pages(runs_143())
        run = run_gate(fx)
        self.assertEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "wait", run.explain())
        self.assertIn("1 of 143 check runs not green", run.proc.stdout)
        self.assertEqual(run.merges, [])

    def test_a_failed_read_stops_the_step_with_no_merge(self):
        fx = green_fixture()
        fx["check_runs_fail"] = True
        run = run_gate(fx)
        self.assertNotEqual(run.proc.returncode, 0, run.explain())
        self.assertEqual(run.decision(), "", run.explain())
        self.assertEqual(run.merges, [])
        # Nothing is read or written after it.
        self.assertEqual(run.calls[-1], self.check_run_reads(run)[0], run.explain())
        self.assertEqual(len(run.comments), 1)


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
