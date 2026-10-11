"""The merge gate's new-test refresh, wired (DRE-6872, after DRE-6871).

DRE-6871 taught `scripts/order_sensitive_refresh.py` a second rule: with
`"recheck_new_tests": true` in `.github/bureau/merge-recheck.json`, a pull
request is refreshed once when `main` gained a test since the pull request's
last test run began and that test names a file the pull request changes (or
the mirror). bureau-pipeline #871 is the shape: `main` gained
`tests/test_planning_escalation_prior_answer.py`, naming
`planning_escalation`, while a pull request changing
`scripts/planning_escalation.py` sat green on a run that never saw it.

The rule reads a payload the fork refresh does not: what `main` gained since
the ANCHOR run was created — the head's newest `pull_request` run at a
non-review workflow path, `merge_gate.last_test_run`. The gate reads `main`'s
tip at that moment (`commits?sha=main&until=<created_at>`) and the compare
from it to `main`, and hands that to the decision as `--since-run-file`.

Three halves:

  * LastTestRunTest — the pure function and its subcommand.
  * WiringTest — the block's text in the shipped step: the `rule=` line read
    by its key, and the header comment naming the third exception.
  * The scenario classes — the shipped step run for real by bash against a
    stub `gh` that records every call, in the shape
    tests/test_merge_gate_fork_refresh_wiring.py uses, and a stand-in
    `merge_gate.py` that records every subcommand it is run with.
"""

import hashlib
import json
import os
import subprocess  # nosec B404 — fixed argv, our own workflow body
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))

import merge_gate  # noqa: E402
import order_sensitive_refresh as osr  # noqa: E402
import step_shell  # noqa: E402
import verdict_content  # noqa: E402

TESTS_YML = ".github/workflows/tests.yml"
PR_REVIEW = ".github/workflows/pr-review.yml"
QA_REVIEW = ".github/workflows/qa-review.yml"

PR = 871
HEAD = "871a11ce0de5ea1b2c3d4e5f60718293a4b5c6d7"
REFRESHED_HEAD = "871b22ce0de5ea1b2c3d4e5f60718293a4b5c6d7"
MERGE_BASE = "86fb0115a3b1e0f2c3d4e5f60718293a4b5c6d7e"
TIP_THEN = "8700c0de5ea1b2c3d4e5f60718293a4b5c6d7e8f"
TIP = "8775c0de5ea1b2c3d4e5f60718293a4b5c6d7e8f"
BRANCH = "agent/DRE-6801-planning-escalation-answer"
CARD = "DRE-6801"
REPO = "dreadnought-foundry/bureau-pipeline"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"

SOURCE = "scripts/planning_escalation.py"
NEW_TEST = "tests/test_planning_escalation_prior_answer.py"
NEW_TEST_PATCH = ("@@ -0,0 +1,3 @@\n+import planning_escalation\n+\n"
                  "+def test_prior_answer(): pass\n")
VERSIONS = "console/backend/alembic/versions/"

NEW_TESTS_PREFIX = "Merge gate: refreshed for new tests onto "
NEW_TESTS_NOTE_PREFIX = "♻️ " + NEW_TESTS_PREFIX
ORDER_NOTE_PREFIX = "♻️ Merge gate: refreshed onto "

# The three test runs of one head, oldest first, and the runs that must
# never be the anchor whatever their `created_at`.
T_FIRST = "2026-10-10T17:02:11Z"
T_SECOND = "2026-10-10T18:15:40Z"
T_NEWEST = "2026-10-10T19:30:05Z"
T_REVIEW = "2026-10-10T21:44:00Z"
T_PUSH = "2026-10-10T22:10:00Z"


def run(path, event, created_at, suite, status="completed"):
    return {"id": suite * 1000, "path": path, "event": event,
            "created_at": created_at, "check_suite_id": suite,
            "status": status, "conclusion": "success", "pull_requests": []}


TEST_RUN = run(TESTS_YML, "pull_request", T_NEWEST, 1)


def evaluate_step() -> dict:
    doc = yaml.safe_load(step_shell.workflow_source(WORKFLOW))
    steps = doc["jobs"]["evaluate"]["steps"]
    found = [s for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(found) == 1, "expected exactly one 'Evaluate and merge' step"
    return found[0]


def evaluate_body() -> str:
    return step_shell.step_shell(evaluate_step())


# --------------------------------------------------------------------------- #
# The anchor run                                                               #
# --------------------------------------------------------------------------- #


class LastTestRunTest(unittest.TestCase):
    """`merge_gate.last_test_run`: the newest `pull_request` run at a path
    that is not a review workflow — condition 1's filter for CI of the
    commit, so a review run created later never moves the anchor."""

    def test_the_newest_of_three_test_runs_wins(self):
        runs = [run(TESTS_YML, "pull_request", T_SECOND, 2),
                run(TESTS_YML, "pull_request", T_NEWEST, 3),
                run(TESTS_YML, "pull_request", T_FIRST, 1)]
        self.assertEqual(merge_gate.last_test_run(runs)["created_at"],
                         T_NEWEST)

    def test_review_runs_never_anchor_whatever_their_created_at(self):
        for review in (PR_REVIEW, QA_REVIEW):
            runs = [TEST_RUN, run(review, "pull_request", T_REVIEW, 9)]
            self.assertEqual(merge_gate.last_test_run(runs), TEST_RUN, review)

    def test_a_push_run_never_anchors(self):
        """A `push` run tests the head alone, not its merge with `main`."""
        runs = [TEST_RUN, run(TESTS_YML, "push", T_PUSH, 8)]
        self.assertEqual(merge_gate.last_test_run(runs), TEST_RUN)

    def test_no_candidate_is_none(self):
        for runs in ([run(PR_REVIEW, "pull_request", T_REVIEW, 9),
                      run(QA_REVIEW, "pull_request", T_REVIEW, 7)],
                     [run(TESTS_YML, "push", T_PUSH, 8)],
                     []):
            self.assertIsNone(merge_gate.last_test_run(runs), runs)

    def test_the_review_paths_are_the_callers(self):
        runs = [TEST_RUN, run(".github/workflows/ci.yml", "pull_request",
                              T_REVIEW, 4)]
        self.assertEqual(
            merge_gate.last_test_run(
                runs, review_workflows=(".github/workflows/ci.yml",)),
            TEST_RUN)

    def cli(self, payload):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runs.json"
            path.write_text(payload if isinstance(payload, str)
                            else json.dumps(payload))
            return subprocess.run(  # nosec B603 B607 — fixed argv
                [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
                 "last-test-run", "--workflow-runs-file", str(path),
                 "--review-workflows", f"{QA_REVIEW},{PR_REVIEW}"],
                capture_output=True, text=True)

    def test_the_subcommand_prints_the_anchors_created_at(self):
        proc = self.cli({"workflow_runs": [
            run(PR_REVIEW, "pull_request", T_REVIEW, 9), TEST_RUN,
            run(TESTS_YML, "push", T_PUSH, 8)]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, f"created_at={T_NEWEST}\n")

    def test_the_subcommand_prints_none_with_no_test_run(self):
        proc = self.cli({"workflow_runs": [
            run(PR_REVIEW, "pull_request", T_REVIEW, 9)]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "created_at=none\n")

    def test_an_unreadable_listing_names_no_anchor(self):
        """The listing's blip substitute is not "no test run": no
        `created_at=` line at all, so the gate writes `{}` and waits."""
        proc = self.cli(merge_gate.UNREADABLE_WORKFLOW_RUNS)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("created_at=", proc.stdout)


# --------------------------------------------------------------------------- #
# The block's text                                                             #
# --------------------------------------------------------------------------- #


class WiringTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = evaluate_body()

    def test_the_rule_line_is_read_by_its_key(self):
        self.assertIn("grep -m1 '^rule=' /tmp/refresh-decision", self.body)
        for by_line in ("sed -n 4p", "sed -n '4p'", "sed -n 3p",
                        "head -4", "tail -1 /tmp/refresh-decision"):
            self.assertNotIn(by_line, self.body)

    def test_the_decision_gets_the_since_run_payload(self):
        call = self.body[self.body.find("order_sensitive_refresh.py decide"):]
        call = call[:call.find("> /tmp/refresh-decision")]
        self.assertIn("--since-run-file /tmp/since-run.json", call)

    def test_the_anchor_is_asked_of_merge_gate_over_the_gates_own_listing(self):
        self.assertIn(
            "python3 .bureau-pipeline/scripts/merge_gate.py last-test-run "
            "--workflow-runs-file /tmp/workflow-runs.json --review-workflows "
            '"$REVIEW_WORKFLOWS"', self.body)

    def test_the_header_names_the_third_exception(self):
        header = step_shell.workflow_source(WORKFLOW).split("\nname:", 1)[0]
        flat = " ".join(header.replace("#", " ").split())
        for needle in ("third", "DRE-6871", "not currency",
                       "last test run", "newest `pull_request` run",
                       "non-review", "no test run", "12%", "15%",
                       "12.7", "15.0"):
            self.assertIn(needle, flat)


# --------------------------------------------------------------------------- #
# Scenario — the shipped step, executed                                        #
# --------------------------------------------------------------------------- #

# A stub `gh` answering from a fixture and recording every call, extended from
# the fork-refresh wiring test's with the runs listing, `main`'s tip at a
# moment, and the compare from that tip.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def fail():
    sys.stderr.write("HTTP 502\n")
    raise SystemExit(1)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def comments():
    return json.load(open(os.environ["COMMENTS"]))


def write_comments(rows):
    with open(os.environ["COMMENTS"], "w") as fh:
        json.dump(rows, fh)


if args[:2] == ["pr", "view"] and not opt("--jq"):
    login = fx["author"]
    record = dict(fx["pr"], url="https://github.com/o/r/pull/1",
                  author={"is_bot": True, "login": "app/" + login[:-5]})
    emit({f: record[f] for f in opt("--json").split(",") if f in record})
if args[:2] == ["pr", "view"]:
    emit(str(fx["pr"][(opt("--jq") or "").lstrip(".")]))

if args[:2] == ["run", "list"]:
    emit([])

if args[:2] == ["pr", "merge"]:
    emit("merged")

if args[0] == "api":
    method = opt("--method") or opt("-X") or "GET"
    path = next(a for a in args if a.startswith("repos/"))
    if path.endswith("/update-branch"):
        emit({"message": "Updating pull request branch."})
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
        spec = path.split("/compare/", 1)[1]
        if spec.startswith(fx["merge_base"] + "..."):
            emit(fx["base_advance"])
        if spec.startswith(fx["tip_then"] + "..."):
            if fx["since_run"] is None:
                fail()
            emit(fx["since_run"])
        emit(fx["compare"])
    if "actions/runs" in path:
        if fx["workflow_runs"] is None:
            fail()
        emit({"total_count": len(fx["workflow_runs"]),
              "workflow_runs": fx["workflow_runs"]})
    if "/commits?" in path and "until=" in path:
        if fx["tip_then_fails"]:
            fail()
        emit([{"sha": fx["tip_then"]}])
    if "/commits" in path:
        emit(fx["pr_commits"])
    if "/issues/" in path and "/comments" in path:
        if "--slurp" in args:
            emit([comments()])
        emit(comments())
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''

LINEAR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["LINEAR_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
'''

# Stands in for merge_gate.py: records the argv it is RUN with, then is the
# real module — imported or run — so nothing else about the gate changes.
MERGE_GATE_STUB = r'''import json, os, sys
REAL = {real!r}
if __name__ == "__main__":
    with open(os.environ["MERGE_GATE_LOG"], "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")
__file__ = REAL
exec(compile(open(REAL).read(), REAL, "exec"))
'''


def entry(name, status="modified", patch=None, sha=None):
    row = {"filename": name, "status": status,
           "sha": sha or hashlib.sha1(name.encode()).hexdigest()}  # nosec B324 — a fixture blob id
    if patch is not None:
        row["patch"] = patch
    return row


PR_FILES = [entry(SOURCE)]
SINCE_RUN = {"status": "ahead", "ahead_by": 2, "behind_by": 0,
             "base_commit": {"sha": TIP_THEN},
             "files": [entry(NEW_TEST, "added", NEW_TEST_PATCH)]}


def compare_payload(files, behind_by=4):
    return {
        "status": "diverged" if behind_by else "ahead",
        "ahead_by": 1,
        "behind_by": behind_by,
        "merge_base_commit": {"sha": MERGE_BASE},
        "base_commit": {"sha": TIP},
        "files": files,
    }


class ShippedStepResult:
    def __init__(self, proc, calls, linear, comments, gate_calls, since_run):
        self.proc = proc
        self.calls = calls
        self.linear = linear
        self.comments = comments
        self.gate_calls = gate_calls
        self.since_run = since_run

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def puts(self):
        return [c for c in self.calls if c[:1] == ["api"] and "-X" in c
                and c[c.index("-X") + 1] == "PUT"]

    @property
    def until_reads(self):
        return [c for c in self.calls if any("until=" in a for a in c)]

    @property
    def since_run_reads(self):
        return [c for c in self.calls
                if any(f"/compare/{TIP_THEN}..." in a for a in c)]

    @property
    def last_test_run_calls(self):
        return [c for c in self.gate_calls if c[:1] == ["last-test-run"]]

    @property
    def advances(self):
        return [c for c in self.linear if c[:1] == ["advance"]]

    @property
    def new_tests_notes(self):
        return [c["body"] for c in self.comments
                if c["body"].startswith(NEW_TESTS_NOTE_PREFIX)]

    @property
    def order_notes(self):
        return [c["body"] for c in self.comments
                if c["body"].startswith(ORDER_NOTE_PREFIX)]

    def explain(self):
        return (f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}\n"
                f"gh calls: {self.calls}\nmerge_gate.py: {self.gate_calls}\n"
                f"linear: {self.linear}")


def declaration(recheck=True, paths=None):
    doc = {"$schema_note": "merge recheck (DRE-4912, DRE-6871)",
           "order_sensitive_paths": paths if paths is not None else [VERSIONS]}
    if recheck is not None:
        doc["recheck_new_tests"] = recheck
    return doc


def run_shipped_step(*, declared=None, workflow_runs=None, since_run=SINCE_RUN,
                     tip_then_fails=False, pr_files=None, main_files=None,
                     behind_by=4, head=HEAD, seed_comments=None,
                     pr_commits=None, verdict=None, refresh_stub=None,
                     runs_unreadable=False) -> ShippedStepResult:
    """Execute merge-gate.yml's `Evaluate and merge` body for real."""
    workflow_runs = [TEST_RUN] if workflow_runs is None else workflow_runs
    pr_files = PR_FILES if pr_files is None else pr_files
    compare = compare_payload(pr_files, behind_by)
    fixture = {
        "pr": {
            "headRefName": BRANCH,
            "state": "OPEN",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "headRefOid": head,
            "baseRefName": "main",
            "url": f"https://github.com/{REPO}/pull/{PR}",
        },
        "check_runs": [
            {"name": "scripts unit tests", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}},
        ],
        "workflow_runs": None if runs_unreadable else workflow_runs,
        "compare": compare,
        "merge_base": MERGE_BASE,
        "base_advance": {"status": "ahead", "ahead_by": behind_by,
                         "files": main_files or [entry("README.md")]},
        "tip_then": TIP_THEN,
        "tip_then_fails": tip_then_fails,
        "since_run": since_run,
        "pr_commits": pr_commits or [{"sha": head,
                                      "commit": {"message": "feat"}}],
        "author": WORKER_LOGIN,
    }
    comments = [{
        "id": 1,
        "user": {"login": QA_LOGIN},
        "body": verdict or f"🔎 QA Critic — VERDICT: APPROVE @{head}",
    }] + list(seed_comments or [])
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        own = {"linear_ops.py", "merge_gate.py"}
        if refresh_stub is not None:
            own.add("order_sensitive_refresh.py")
            (scripts / "order_sensitive_refresh.py").write_text(refresh_stub)
        for item in (ROOT / "scripts").iterdir():
            if item.name not in own:
                os.symlink(item, scripts / item.name)
        (scripts / "linear_ops.py").write_text(LINEAR_STUB)
        (scripts / "merge_gate.py").write_text(MERGE_GATE_STUB.format(
            real=str(ROOT / "scripts" / "merge_gate.py")))
        if declared is not None:
            (td / ".github" / "bureau").mkdir(parents=True)
            (td / ".github" / "bureau" / "merge-recheck.json").write_text(
                json.dumps(declared))
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps(comments))
        for log in ("gh.log", "linear.log", "merge-gate.log"):
            (td / log).write_text("")
        Path("/tmp/since-run.json").unlink(missing_ok=True)
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-c", evaluate_body()],
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
                "LINEAR_LOG": str(td / "linear.log"),
                "MERGE_GATE_LOG": str(td / "merge-gate.log"),
                "REPO_FULL": REPO,
                "WORKFLOW_TOKEN": "workflow-token",
                "QA_LOGIN": QA_LOGIN,
                "FIX_LANE_RECHECK_SECONDS": "0",
                "FIX_LANE_RECHECK_CEILING_SECONDS": "0",
                "MERGE_RETRY_SECONDS": "0",
            },
        )
        read = lambda p: [json.loads(ln) for ln in  # noqa: E731
                          p.read_text().splitlines() if ln]
        try:
            since = json.loads(Path("/tmp/since-run.json").read_text())
        except (OSError, ValueError):
            since = None
        return ShippedStepResult(proc, read(td / "gh.log"),
                                 read(td / "linear.log"),
                                 json.loads((td / "comments.json").read_text()),
                                 read(td / "merge-gate.log"), since)


def todays_calls(*, base_advance):
    """Every `gh` call the step made before this card, in order, for a head
    behind `main` that merges — recorded from the step as it stood on `main`
    at f1f4e511 and written out, so a new call anywhere fails the comparison.
    The base advance is read only under a declaration (DRE-5070)."""
    calls = [
        ["pr", "view", str(PR), "--json",
         "headRefName,state,mergeStateStatus,isDraft,headRefOid,"
         "baseRefName,body,createdAt,author,url,files"],
        ["api", "--paginate", "--slurp",
         f"repos/{REPO}/commits/{HEAD}/check-runs?per_page=100"],
        ["api", f"repos/{REPO}/actions/runs?head_sha={HEAD}&per_page=100"],
        ["api", f"repos/{REPO}/compare/main...{HEAD}"],
        ["api", "--paginate", "--slurp",
         f"repos/{REPO}/issues/{PR}/comments?per_page=100"],
        ["api", f"repos/{REPO}/pulls/{PR}/commits?per_page=100"],
        ["run", "list", "--repo", REPO, "--workflow", "self-agent-fix.yml",
         "--limit", "20", "--json", "status,databaseId,displayTitle"],
        ["api", "--paginate", f"repos/{REPO}/pulls?state=open&per_page=100",
         "--jq", ".[] | {number, head_sha: .head.sha}"],
        ["api", "--paginate", "--slurp", f"repos/{REPO}/rules/branches/main"],
    ]
    if base_advance:
        calls.append(["api", f"repos/{REPO}/compare/{MERGE_BASE}...main"])
    calls.append(["pr", "merge", str(PR), "--merge", "--delete-branch",
                  "--match-head-commit", HEAD])
    return calls


class SwitchOffCostsNothingTest(unittest.TestCase):
    """Criterion 1: with no declaration, or one without
    `"recheck_new_tests": true`, the gate makes today's `gh` calls and no
    others — no `last-test-run`, no `commits?until`, no second compare."""

    def assert_today(self, result, *, base_advance):
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(result.calls,
                         todays_calls(base_advance=base_advance),
                         result.explain())
        self.assertEqual(result.until_reads, [])
        self.assertEqual(result.since_run_reads, [])
        self.assertEqual(result.last_test_run_calls, [], result.explain())
        self.assertEqual(result.since_run, {}, result.explain())

    def test_no_declaration(self):
        self.assert_today(run_shipped_step(declared=None),
                          base_advance=False)

    def test_a_declaration_without_the_key(self):
        self.assert_today(run_shipped_step(declared=declaration(recheck=None)),
                          base_advance=True)

    def test_a_declaration_with_the_key_false(self):
        self.assert_today(
            run_shipped_step(declared=declaration(recheck=False)),
            base_advance=True)

    def test_the_key_on_but_current_reads_nothing_more(self):
        result = run_shipped_step(declared=declaration(), behind_by=0)
        self.assertEqual(result.last_test_run_calls, [], result.explain())
        self.assertEqual(result.until_reads, [])
        self.assertEqual(result.since_run_reads, [])
        self.assertEqual(len(result.merges), 1, result.explain())


class TheAnchorIsTheLastTestRunTest(unittest.TestCase):
    """Criterion 3: with the key on and the branch behind, exactly two more
    reads — `main`'s tip at the anchor's `created_at`, and the compare from
    that tip to `main` — and the `until` read carries the anchor's time."""

    def until_of(self, result):
        self.assertEqual(len(result.until_reads), 1, result.explain())
        call = result.until_reads[0]
        path = next(a for a in call if "until=" in a)
        self.assertEqual(
            path, f"repos/{REPO}/commits?sha=main&until="
                  f"{path.split('until=', 1)[1].split('&', 1)[0]}"
                  f"&per_page=1", call)
        return path.split("until=", 1)[1].split("&", 1)[0]

    def assert_two_extra_reads(self, result):
        # Every read beyond today's, leaving out the refresh's own writes:
        # the update and the note (gate_note.py's post and re-read).
        extra = [c for c in result.calls
                 if c not in todays_calls(base_advance=True)
                 and c not in result.puts
                 and not any(f"/issues/{PR}/comments" in a for a in c)]
        self.assertEqual(
            extra,
            [["api", f"repos/{REPO}/commits?sha=main&until="
                     f"{self.until_of(result)}&per_page=1"],
             ["api", f"repos/{REPO}/compare/{TIP_THEN}...main"]],
            result.explain())
        self.assertEqual(result.since_run, SINCE_RUN)
        self.assertEqual(len(result.last_test_run_calls), 1)
        self.assertEqual(
            result.last_test_run_calls[0],
            ["last-test-run", "--workflow-runs-file", "/tmp/workflow-runs.json",
             "--review-workflows", f"{QA_REVIEW},{PR_REVIEW}"])

    def test_the_newest_of_three_test_runs(self):
        result = run_shipped_step(declared=declaration(), workflow_runs=[
            run(TESTS_YML, "pull_request", T_SECOND, 2),
            run(TESTS_YML, "pull_request", T_NEWEST, 1),
            run(TESTS_YML, "pull_request", T_FIRST, 3)])
        self.assertEqual(self.until_of(result), T_NEWEST)
        self.assert_two_extra_reads(result)

    def test_a_later_review_run_is_not_the_anchor(self):
        for review in (PR_REVIEW, QA_REVIEW):
            result = run_shipped_step(declared=declaration(), workflow_runs=[
                TEST_RUN, run(review, "pull_request", T_REVIEW, 9)])
            self.assertEqual(self.until_of(result), T_NEWEST, review)
            self.assert_two_extra_reads(result)

    def test_a_later_push_run_is_not_the_anchor(self):
        result = run_shipped_step(declared=declaration(), workflow_runs=[
            TEST_RUN, run(TESTS_YML, "push", T_PUSH, 8)])
        self.assertEqual(self.until_of(result), T_NEWEST)
        self.assert_two_extra_reads(result)


class NoTestRunMergesAsTodayTest(unittest.TestCase):
    """Criterion 4, first half: a readable listing holding no `pull_request`
    run at a non-review path writes `{"no_test_run": true}`, the decision
    answers `proceed`, and the gate merges with no update and no note."""

    def test_review_runs_only_push_runs_only_or_empty(self):
        for runs in ([run(PR_REVIEW, "pull_request", T_REVIEW, 9)],
                     [run(TESTS_YML, "push", T_PUSH, 8)],
                     []):
            result = run_shipped_step(declared=declaration(),
                                      workflow_runs=runs)
            self.assertEqual(result.proc.returncode, 0, result.explain())
            self.assertEqual(result.since_run, {"no_test_run": True},
                             result.explain())
            self.assertEqual(result.until_reads, [], result.explain())
            self.assertEqual(result.since_run_reads, [], result.explain())
            self.assertEqual(len(result.merges), 1, result.explain())
            self.assertEqual(result.puts, [], result.explain())
            self.assertEqual(result.new_tests_notes, [])
            self.assertIn("fork-refresh: decision=proceed", result.proc.stdout)


class AFailedReadWaitsTest(unittest.TestCase):
    """Criterion 4, second half: a failed `commits?until` or compare read
    writes `{}`, and the gate exits without merging or updating. An
    unreadable listing never reaches the block: condition 1 waits on it."""

    def assert_waited(self, result):
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(result.merges, [], result.explain())
        self.assertEqual(result.puts, [], result.explain())
        self.assertEqual(result.advances, [], result.explain())
        self.assertEqual(result.new_tests_notes, [])

    def test_a_failed_until_read(self):
        result = run_shipped_step(declared=declaration(), tip_then_fails=True)
        self.assert_waited(result)
        self.assertEqual(result.since_run, {}, result.explain())
        self.assertEqual(result.since_run_reads, [], result.explain())
        self.assertIn("fork-refresh: decision=wait", result.proc.stdout)

    def test_a_failed_compare_read(self):
        result = run_shipped_step(declared=declaration(), since_run=None)
        self.assert_waited(result)
        self.assertEqual(result.since_run, {}, result.explain())
        self.assertEqual(len(result.since_run_reads), 1, result.explain())
        self.assertIn("fork-refresh: decision=wait", result.proc.stdout)

    def test_an_unreadable_listing(self):
        result = run_shipped_step(declared=declaration(), runs_unreadable=True)
        self.assert_waited(result)
        self.assertEqual(result.until_reads, [], result.explain())
        self.assertIn("decision=wait", result.proc.stdout)


class TheNewTestRefreshTest(unittest.TestCase):
    """Criterion 5: on `rule=new-tests` the branch is updated once as the
    qa-bot, pinned to the evaluated head, one note says so, and nothing
    merges."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_shipped_step(declared=declaration())

    def test_the_step_exits_clean_without_merging(self):
        self.assertEqual(self.result.proc.returncode, 0, self.result.explain())
        self.assertEqual(self.result.merges, [], self.result.explain())
        self.assertEqual(self.result.advances, [], self.result.explain())
        self.assertIn("fork-refresh: rule=new-tests", self.result.proc.stdout)

    def test_one_update_bound_to_the_evaluated_head(self):
        self.assertEqual(len(self.result.puts), 1, self.result.explain())
        self.assertEqual(
            self.result.puts[0],
            ["api", "-X", "PUT", f"repos/{REPO}/pulls/{PR}/update-branch",
             "-f", f"expected_head_sha={HEAD}"])

    def test_one_note_names_the_tip_the_test_and_the_file(self):
        self.assertEqual(len(self.result.new_tests_notes), 1,
                         self.result.comments)
        self.assertEqual(self.result.order_notes, [])
        note = self.result.new_tests_notes[0]
        first = note.splitlines()[0]
        self.assertTrue(first.startswith(
            f"{NEW_TESTS_NOTE_PREFIX}{TIP} — "), first)
        sentence = first.split(" — ", 1)[1]
        self.assertIn(NEW_TEST, sentence)
        self.assertIn(SOURCE, sentence)
        # The marker is the decision module's own receipt.
        self.assertTrue(first.startswith(
            "♻️ " + osr.new_tests_receipt_marker(TIP)))
        body = note.split("\n", 1)[1]
        for needle in ("updated from main", "combined", "DRE-2340", "once"):
            self.assertIn(needle, body)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, note)

    def test_the_rule_is_read_by_key_whatever_prefix_carries(self):
        """A decision whose third line carries a prefix is handled exactly as
        the real one, whose `prefix=` is empty."""
        stub = ("import sys\nprint('decision=refresh')\n"
                "print('reason=main added tests/test_x.py, which names "
                "scripts/x_module.py, changed by this pull request')\n"
                f"print('prefix={VERSIONS}')\nprint('rule=new-tests')\n")
        result = run_shipped_step(declared=declaration(), refresh_stub=stub)
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(len(result.puts), 1, result.explain())
        self.assertEqual(len(result.new_tests_notes), 1, result.comments)
        self.assertEqual(result.order_notes, [])
        self.assertEqual(result.merges, [])

    def test_a_second_evaluation_at_the_refreshed_head_merges(self):
        """Criterion 6: the receipt now on the thread makes the decision
        `proceed`, so the refreshed head with green CI and a carried verdict
        merges and no second update is made."""
        compare = compare_payload(PR_FILES)
        cid = verdict_content.content_id(compare)
        self.assertIsNotNone(cid)
        receipt = {"id": 50, "user": {"login": QA_LOGIN},
                   "body": self.result.new_tests_notes[0]}
        again = run_shipped_step(
            declared=declaration(), head=REFRESHED_HEAD,
            seed_comments=[receipt],
            verdict=f"🔎 QA Critic — VERDICT: APPROVE @{HEAD} content:{cid}",
            pr_commits=[{"sha": HEAD, "commit": {"message": "feat"}},
                        {"sha": REFRESHED_HEAD,
                         "commit": {"message": "Merge main"}}])
        self.assertEqual(again.proc.returncode, 0, again.explain())
        self.assertIn("carried", again.proc.stdout)
        self.assertEqual(again.puts, [], again.explain())
        self.assertEqual(len(again.merges), 1, again.explain())
        self.assertEqual(len(again.new_tests_notes), 1)


class TheOrderSensitiveNoteIsUnchangedTest(unittest.TestCase):
    """Criterion 7: with the key on, an order-sensitive refresh posts today's
    note, byte for byte."""

    def test_the_note_is_todays(self):
        ours = VERSIONS + "0062_workflow_run_actor.py"
        theirs = VERSIONS + "0062_runner_ledger.py"
        result = run_shipped_step(
            declared=declaration(), pr_files=[entry(ours, "added")],
            main_files=[entry(theirs, "added")])
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(len(result.puts), 1, result.explain())
        self.assertEqual(result.new_tests_notes, [])
        reason = osr.decide(
            compare=compare_payload([entry(ours, "added")]),
            base_advance={"files": [entry(theirs, "added")]},
            declaration=declaration()).reason
        self.assertEqual(result.order_notes, [
            f"♻️ Merge gate: refreshed onto {TIP} — {reason}\n\n"
            "Not merged: main gained a file under the same order-sensitive "
            "path since this branch's merge base, so merging now could fork "
            "main (DRE-4912). The branch was updated from main and CI re-runs "
            "on the result, where the repo's own check sees both files "
            "together. The standing review carries if this pull request's "
            "own changes are unchanged (DRE-2340).\n"], result.comments)


if __name__ == "__main__":
    unittest.main()
