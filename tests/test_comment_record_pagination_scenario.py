"""Scenario: the SHIPPED merge-gate step, executed, against a PR whose
APPROVE and PASS sit past the first page of its comment record (DRE-4139).

The unit suite (tests/test_comment_record_pagination.py) proves the decision.
This proves the seam the incident actually crossed: merge-gate.yml's own
`Evaluate and merge` body, run for real by bash against a stub `gh` that pages
the comments endpoint the way GitHub pages it — the FIRST page is the OLDEST
comments, `per_page` decides its size, and a caller that does not ask for the
next page never learns there is one.

agent-bureau#2588 is the fixture's shape: both reviewers approved the same
head with the same content fingerprint, CI was green, and the gate answered
`decision=wait` because the newest critic comment it could see was a neutral
`could not run` notice with no sha in it.

Three directions, so none of the three can pass vacuously:

  * the shipped step merges — the verdicts on the last page are read;
  * the PRE-FIX step, the shipped body with `--paginate --slurp` stripped and
    nothing else changed, strands the same pull request, forever;
  * a comment fetch that DIES PART WAY through the record decides `wait` and
    never `merge` — a short read must read as "no verdicts yet", not as a
    missing verdict on a PR that has one.

Stubbing the live tool is what makes this different from a unit-green claim:
locally `gh` is authed and would have given a false green
(`standards/engineering.md`, test rigor).

Run: cd bureau-pipeline && python3 -m pytest \
    tests/test_comment_record_pagination_scenario.py -v
"""

import json
import os
import re
import subprocess  # nosec B404 — fixed argv, our own workflow body
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"
sys.path.insert(0, str(ROOT / "scripts"))

from verdict_content import content_id  # noqa: E402

PR = 2588
HEAD = "0226c32587d1e0f4a9bb1c2d3e4f5061728394a5"
OLD = "1111111111111111111111111111111111111111"
# No DRE-N in the branch on purpose: the merge arm calls linear_ops.py with
# the card it greps out of the branch name, and this harness has no Linear.
BRANCH = "agent/capacity-ledger-desktop-design"
REPO = "dreadnought-foundry/agent-bureau"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"

COMPARE = {
    "status": "ahead",
    "merge_base_commit": {"sha": OLD},
    "files": [{"filename": "console/src/CapacityLedger.tsx",
               "status": "modified", "sha": "a" * 40}],
}
CONTENT = content_id(COMPARE)

#: agent-bureau#2588's own page-1 last word — a critic comment carrying no
#: reviewed commit, which `verdict_sha()` reads as no verdict at all.
CRASH_NOTICE = "🔎 QA Critic could not run (infra error) — re-review needed…"

# A `gh` that pages an issue-comments endpoint the way GitHub does, records
# every invocation, and answers the gate's other reads from a fixture.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, re, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def serve_comments(path):
    """GitHub's paging contract, in miniature: `per_page` (default 30) sets
    the page size, page 1 is the OLDEST comments, and only `--paginate`
    walks past it. `--slurp` collects the pages into one array."""
    rows = json.load(open(os.environ["COMMENTS"]))
    m = re.search(r"per_page=(\d+)", path)
    size = min(int(m.group(1)) if m else 30, 100)
    pages = [rows[i:i + size] for i in range(0, len(rows), size)] or [[]]
    if "--paginate" not in args:
        pages = pages[:1]
    elif os.environ.get("COMMENTS_DIE_AT_PAGE"):
        # A mid-pagination failure. The first page is already on stdout —
        # the harsher shape than gh's own buffering — and gh exits non-zero.
        die_at = int(os.environ["COMMENTS_DIE_AT_PAGE"])
        for page in pages[:die_at - 1]:
            print(json.dumps(page))
        sys.stderr.write("gh: HTTP 502 while fetching page %d\n" % die_at)
        raise SystemExit(1)
    if "--slurp" in args:
        emit(pages)
    for page in pages:
        print(json.dumps(page))
    raise SystemExit(0)


if args[:2] == ["pr", "view"]:
    field = (opt("--jq") or "").lstrip(".")
    emit(json.dumps(fx["pr"][field]).strip('"'))

if args[:2] == ["pr", "merge"]:
    emit("merged")

if args[:2] == ["run", "list"]:
    # DRE-4486: the merge gate reads the Agent Fix lane before it merges.
    # An idle lane is the fixture for every scenario here — the stranded-fix
    # race has its own suite (tests/test_stranded_fix.py).
    emit([])

if args[0] == "api":
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if (opt("--method") or "GET") == "POST" or "-F" in args:
        emit({})
    if "check-runs" in path:
        emit({"check_runs": fx["check_runs"]})
    if "/compare/" in path:
        emit(fx["compare"])
    if "actions/runs" in path:
        emit({"workflow_runs": []})
    if "/comments" in path:
        serve_comments(path)
    if "/commits" in path:
        emit(fx["pr_commits"])
    if "/pulls/" in path:
        emit(fx["author"] if opt("--jq") else {})
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def _comment(body: str, login: str = QA_LOGIN) -> dict:
    return {"id": 0, "user": {"login": login}, "body": body}


def thread() -> list:
    """140 comments, oldest first — longer than GitHub's default page (30)
    AND longer than its largest page (100), so nothing here can be read by
    one request however the page size is set.

    The comment sitting at each ceiling is a verdict that decides `wait`, so
    a truncated read answers with a stale fact rather than with nothing.
    """
    rows = [_comment(f"review round chatter ({n})", WORKER_LOGIN)
            for n in range(1, 141)]
    rows[19] = _comment(f"🔎 QA Critic — VERDICT: APPROVE @{OLD}")
    rows[98] = _comment(CRASH_NOTICE)
    rows[137] = _comment(f"🔎 QA Critic — VERDICT: APPROVE @{HEAD} "
                         f"content:{CONTENT}")
    rows[138] = _comment(f"🧪 QA Verifier — VERDICT: PASS @{HEAD}")
    for n, row in enumerate(rows, 1):
        row["id"] = n
    return rows


def evaluate_body() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["evaluate"]["steps"]
    runs = [s["run"] for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(runs) == 1, "expected exactly one 'Evaluate and merge' step"
    return runs[0]


def substitute(run: str) -> str:
    """Apply the `${{ }}` substitutions Actions would make; an expression
    with no value here is a hole in the harness, not something to skip."""
    values = {
        "github.repository": REPO,
        "github.token": "workflow-token",
        "steps.qa.outputs.app-slug": "agent-bureau-qa-bot",
    }

    def repl(m):
        key = m.group(1).strip()
        assert key in values, f"harness has no value for ${{{{ {key} }}}}"
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


class ShippedStepResult:
    def __init__(self, proc, calls):
        self.proc = proc
        self.calls = calls

    @property
    def merge_attempts(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def decision(self):
        for line in self.proc.stdout.splitlines():
            if line.startswith("decision="):
                return line.split("=", 1)[1]
        return ""

    @property
    def comment_reads(self):
        return [c for c in self.calls
                if c and c[0] == "api" and any("/comments" in a for a in c)]


def run_shipped_step(body=None, die_at_page: int = 0) -> ShippedStepResult:
    """Execute merge-gate.yml's `Evaluate and merge` body for real."""
    fixture = {
        "pr": {
            "headRefName": BRANCH,
            "state": "OPEN",
            "mergeStateStatus": "CLEAN",
            "isDraft": False,
            "headRefOid": HEAD,
            "baseRefName": "main",
            "url": f"https://github.com/{REPO}/pull/{PR}",
        },
        "check_runs": [
            {"name": "console tests", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}},
        ],
        "compare": COMPARE,
        "pr_commits": [{"sha": HEAD}],
        "author": WORKER_LOGIN,
    }
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        os.symlink(ROOT, td / ".bureau-pipeline")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps(thread()))
        (td / "gh.log").write_text("")
        env = {
            **{k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"},
            "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PR": str(PR),
            "GH_TOKEN": "qa-token",
            "FIXTURE": str(td / "fixture.json"),
            "COMMENTS": str(td / "comments.json"),
            "GH_LOG": str(td / "gh.log"),
            # The step `env:` the shipped workflow declares (DRE-4486 moved
            # the repository and token substitutions out of the script and
            # up there); an env the harness does not model is the same hole
            # as a `${{ }}` `substitute()` has no value for.
            "REPO_FULL": REPO,
            "WORKFLOW_TOKEN": "workflow-token",
        }
        if die_at_page:
            env["COMMENTS_DIE_AT_PAGE"] = str(die_at_page)
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-e", "-c",
             substitute(body if body is not None else evaluate_body())],
            cwd=td, capture_output=True, text=True, env=env,
        )
        calls = [json.loads(ln)
                 for ln in (td / "gh.log").read_text().splitlines() if ln]
    return ShippedStepResult(proc, calls)


class AVerdictPastTheFirstPageStillMergesTest(unittest.TestCase):
    """Criterion 1: a PR whose APPROVE and PASS sit beyond comment 30 is
    merged by the gate."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_shipped_step()

    def test_the_gate_merges(self):
        self.assertEqual(
            self.result.decision, "merge",
            f"stdout:\n{self.result.proc.stdout}\n"
            f"stderr:\n{self.result.proc.stderr}",
        )
        self.assertEqual(len(self.result.merge_attempts), 1)

    def test_the_run_is_green(self):
        self.assertEqual(self.result.proc.returncode, 0,
                         self.result.proc.stderr)
        self.assertNotIn("Traceback", self.result.proc.stderr)

    def test_the_fetch_asked_for_every_page(self):
        reads = self.result.comment_reads
        self.assertTrue(reads, "the step never read the comment record")
        self.assertTrue(
            all("--paginate" in c for c in reads),
            f"a comment read stopped at page 1: {reads}",
        )

    def test_the_record_handed_to_the_script_is_one_flat_list(self):
        """Criterion 4: not an array of pages — `latest_verdict_comment`
        would call `.get` on an inner list and the run would go RED."""
        self.assertNotIn("decision=", self.result.proc.stderr)
        self.assertNotIn("cannot read comments", self.result.proc.stdout)


class WithoutPaginationThePullRequestIsStrandedForeverTest(unittest.TestCase):
    """Anti-vacuity: the pre-DRE-4139 step — the shipped body with the
    pagination flags removed and nothing else changed — is agent-bureau#2588,
    reproduced from the code that is in the repository today."""

    def test_the_pre_fix_step_waits_on_a_verdict_it_already_has(self):
        body = evaluate_body()
        pre_fix = body.replace("gh api --paginate --slurp \\\n", "gh api \\\n")
        self.assertNotEqual(pre_fix, body, "the fetch is spelled differently now")
        result = run_shipped_step(body=pre_fix)
        self.assertEqual(result.decision, "wait", result.proc.stdout)
        self.assertEqual(result.merge_attempts, [])
        self.assertIn("names no reviewed commit", result.proc.stdout)

    def test_and_it_still_reports_success(self):
        """Why nothing caught it: the job exits 0 while it strands, so every
        run of the gate on that PR showed `success`."""
        body = evaluate_body()
        pre_fix = body.replace("gh api --paginate --slurp \\\n", "gh api \\\n")
        self.assertEqual(run_shipped_step(body=pre_fix).proc.returncode, 0)


class AShortReadDecidesWaitTest(unittest.TestCase):
    """Criterion 2: a truncated or failed comment fetch still decides `wait`,
    never `merge`. `--paginate` must not turn a mid-pagination failure into a
    partial record that reads as a MISSING verdict on a PR that has one — the
    fail-closed `[]` substitute has to survive."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_shipped_step(die_at_page=2)

    def test_it_never_merges_on_a_partial_record(self):
        self.assertEqual(self.result.merge_attempts, [],
                         self.result.proc.stdout)
        self.assertEqual(self.result.decision, "wait", self.result.proc.stdout)

    def test_the_run_stays_green_rather_than_going_red(self):
        self.assertEqual(self.result.proc.returncode, 0,
                         f"stderr:\n{self.result.proc.stderr}")

    def test_it_reads_as_no_verdicts_yet(self):
        self.assertIn("no critic verdict yet", self.result.proc.stdout)


if __name__ == "__main__":
    unittest.main()
