"""Every reader of a PR's comment record reads EVERY page (DRE-4139).

`merge-gate.yml` fetched `issues/{pr}/comments` with no pagination, so it saw
GitHub's default page size of **30** — and page 1 of that endpoint is the
**OLDEST** 30. Once a pull request crosses thirty comments the gate's verdict
window is frozen: no verdict posted afterwards can ever enter it, re-running
the reviewers cannot help (each new verdict lands further outside), and every
comment the pipeline itself posts pushes it further out. The gate waits
forever and still exits 0, so the run reports `success` while it strands.

Observed on agent-bureau#2588 (DRE-4113), 39 comments: critic `APPROVE` and
verifier `PASS` both bound to head `0226c3258…`, CI green, and the gate said

    reason=critic verdict names no reviewed commit (pre-DRE-1990 format or
    neutral status) — treated as NO verdict; waiting for a fresh review of …

because the newest critic comment INSIDE the oldest thirty was a neutral
`🔎 QA Critic could not run (infra error)` notice, which carries no sha.

It fails CLOSED — it waits rather than merging something unreviewed — so this
is a stranding bug, not a safety bug. Four other readers of the same record
had the same defect and are pinned here too: the review skip (`qa-review.yml`,
whose own comment promises the "same call shape merge-gate.yml uses"), the fix
loop's fix-vs-conflict routing, and the consecutive-death cap.

The shape is the one nine other reads in these workflows already use —
`gh api --paginate --slurp …?per_page=100` — so the record arrives as ONE
array per page and the consuming script flattens it. A bare `--paginate`
would print one JSON array per page back to back and `json.load` would raise;
`--slurp` alone hands over a list of PAGES, and `latest_verdict_comment` would
call `.get` on an inner list. Both are red runs, which is worse than today's
wait, so the flattening is proved here rather than assumed.

Run: cd bureau-pipeline && python3 -m pytest tests/test_comment_record_pagination.py -v
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 — fixed argv, this repo's own scripts
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "scripts"))

import fix_dead_run  # noqa: E402
import merge_gate  # noqa: E402
from verdict_content import content_id  # noqa: E402

HEAD = "0226c32587d1e0f4a9bb1c2d3e4f5061728394a5"
OLD = "1111111111111111111111111111111111111111"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"
REPO = "dreadnought-foundry/agent-bureau"

#: GitHub's default page size on the comments endpoint — the ceiling that
#: stranded agent-bureau#2588 — and the largest page it will serve, which is
#: the ceiling DRE-2681 ("the promoter never sees past row 100") is about.
DEFAULT_PAGE = 30
MAX_PAGE = 100

COMPARE = {
    "status": "ahead",
    "merge_base_commit": {"sha": OLD},
    "files": [{"filename": "scripts/merge_gate.py", "status": "modified",
               "sha": "a" * 40}],
}
CONTENT = content_id(COMPARE)

CRASH_NOTICE = "🔎 QA Critic could not run (infra error) — re-review needed…"


def _comment(body: str, login: str = QA_LOGIN) -> dict:
    return {"user": {"login": login}, "body": body}


def long_record() -> list[dict]:
    """A 140-comment thread whose verdicts are the last thing in it.

    140 on purpose, not 39: it is longer than BOTH ceilings, so one fixture
    proves the read is not stopping at GitHub's default thirty and is not
    stopping at the largest single page either. The comment at each ceiling
    is a verdict that decides `wait`, so a truncated read answers with a
    stale fact rather than with nothing — no assertion below can be met by
    reading no comments at all.
    """
    rows = [_comment(f"a note from the middle of the thread ({n})", WORKER_LOGIN)
            for n in range(1, 141)]
    # Inside the oldest thirty: an APPROVE for a head three pushes ago.
    rows[19] = _comment(f"🔎 QA Critic — VERDICT: APPROVE @{OLD}")
    # Inside the oldest hundred: the infra-crash notice that carries no sha —
    # agent-bureau#2588's own last word on page 1.
    rows[98] = _comment(CRASH_NOTICE)
    # The verdicts that actually bind this head, as comments 138 and 139.
    rows[137] = _comment(f"🔎 QA Critic — VERDICT: APPROVE @{HEAD} "
                         f"content:{CONTENT}")
    rows[138] = _comment(f"🧪 QA Verifier — VERDICT: PASS @{HEAD}")
    return rows


def pages(rows: list[dict], size: int) -> list[list[dict]]:
    """The record as `gh api --paginate --slurp` hands it over: one array per
    page, oldest page first."""
    return [rows[i:i + size] for i in range(0, len(rows), size)] or [[]]


def _write(td: Path, name: str, payload) -> str:
    path = td / name
    path.write_text(json.dumps(payload))
    return str(path)


def run_gate(td: Path, comments_payload) -> subprocess.CompletedProcess:
    """merge_gate.py, run exactly as merge-gate.yml runs it, against a
    comments file holding `comments_payload` verbatim."""
    return subprocess.run(  # nosec B603 — fixed argv, our own script
        [sys.executable, str(ROOT / "scripts" / "merge_gate.py"),
         "--head-sha", HEAD,
         "--qa-login", QA_LOGIN,
         "--check-runs-file", _write(td, "check-runs.json", {"check_runs": [
             {"name": "scripts unit tests", "status": "completed",
              "conclusion": "success", "check_suite": {"id": 1}}]}),
         "--comments-file", _write(td, "comments.json", comments_payload),
         "--workflow-runs-file", _write(td, "runs.json", {"workflow_runs": []}),
         "--compare-file", _write(td, "compare.json", COMPARE),
         "--merge-state", "CLEAN",
         "--is-draft", "false",
         "--head-branch", "agent/DRE-4113-capacity-ledger",
         "--pr-author", WORKER_LOGIN,
         "--pr-commits-file", _write(td, "pr-commits.json", [{"sha": HEAD}])],
        capture_output=True, text=True,
    )


def gate_decision(comments_payload) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as raw:
        return run_gate(Path(raw), comments_payload)


def decision_of(proc: subprocess.CompletedProcess) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith("decision="):
            return line.split("=", 1)[1]
    return ""


class TheGateReadsAPaginatedRecordAsOneListTest(unittest.TestCase):
    """Criterion 4: a two-page record reaches merge_gate.py as one flat list —
    the gate decides `merge`, with no crash and no red run."""

    def test_a_two_page_record_merges(self):
        payload = pages(long_record(), MAX_PAGE)
        self.assertEqual(len(payload), 2, "the fixture must span two pages")
        proc = gate_decision(payload)
        self.assertEqual(decision_of(proc), "merge",
                         f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_the_five_page_record_the_default_page_size_would_yield(self):
        """The same thread as `--paginate` without `?per_page=100` pages it:
        five arrays rather than two. The gate must not care how many."""
        proc = gate_decision(pages(long_record(), DEFAULT_PAGE))
        self.assertEqual(decision_of(proc), "merge", proc.stdout)

    def test_a_flat_record_still_decides_the_same_way(self):
        """The pre-DRE-4139 payload shape is untouched: every other caller of
        this script hands it a flat array and must keep working."""
        proc = gate_decision(long_record())
        self.assertEqual(decision_of(proc), "merge", proc.stdout)

    def test_an_empty_record_is_still_no_verdict(self):
        for payload in ([], [[]]):
            with self.subTest(payload=payload):
                proc = gate_decision(payload)
                self.assertEqual(decision_of(proc), "wait", proc.stdout)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_page_of_something_other_than_comments_is_refused(self):
        """Fail closed on a shape nobody can read — never a partial list."""
        proc = gate_decision([["not a comment object"]])
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotEqual(decision_of(proc), "merge")


class WithoutEveryPageTheGateStrandsTest(unittest.TestCase):
    """Anti-vacuity: the truncated reads the fix removes, with agent-bureau
    #2588's own two reasons. If these ever decide `merge`, the tests above
    prove nothing."""

    def test_the_oldest_thirty_strand_the_pull_request(self):
        proc = gate_decision(long_record()[:DEFAULT_PAGE])
        self.assertEqual(decision_of(proc), "wait", proc.stdout)
        self.assertIn("stale", proc.stdout)

    def test_the_oldest_hundred_strand_it_too(self):
        """The `?per_page=100` ceiling is the same defect with a higher roof:
        the newest critic comment inside it is the infra-crash notice, and the
        gate reports agent-bureau#2588's exact reason."""
        proc = gate_decision(long_record()[:MAX_PAGE])
        self.assertEqual(decision_of(proc), "wait", proc.stdout)
        self.assertIn("names no reviewed commit", proc.stdout)


class TheReviewSkipReadsTheSameRecordTest(unittest.TestCase):
    """qa-review.yml's own comment promises the skip and the gate "can never
    disagree about the standing verdict". Fixing only the gate breaks that
    promise, so should_review_pr.py reads every page the same way."""

    def _review(self, td: Path, comments_payload) -> str:
        proc = subprocess.run(  # nosec B603 — fixed argv, our own script
            [sys.executable, str(ROOT / "scripts" / "should_review_pr.py"),
             "agent/DRE-4113-capacity-ledger",
             "--comments-file", _write(td, "comments.json", comments_payload),
             "--compare-file", _write(td, "compare.json", COMPARE),
             "--pr-commits-file", _write(td, "commits.json", [{"sha": HEAD}]),
             "--qa-login", QA_LOGIN],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        return proc.stdout

    def test_a_carried_approve_on_the_last_page_is_found(self):
        with tempfile.TemporaryDirectory() as raw:
            out = self._review(Path(raw), pages(long_record(), MAX_PAGE))
        self.assertIn("review=false", out)
        self.assertIn(f"carried_sha={HEAD}", out)

    def test_the_truncated_record_orders_a_review_it_does_not_need(self):
        with tempfile.TemporaryDirectory() as raw:
            out = self._review(Path(raw), long_record()[:MAX_PAGE])
        self.assertIn("review=true", out)


class TheDeadRunCapReadsTheSameRecordTest(unittest.TestCase):
    """agent-fix.yml hands the comment list to fix_dead_run.py for the
    consecutive-death cap. Truncated, it counts the wrong deaths — and the cap
    is what stops a dead fix loop re-running forever."""

    def _record(self) -> list[dict]:
        rows = [_comment(f"chatter ({n})", WORKER_LOGIN) for n in range(120)]
        rows += [_comment(f"🪦 {fix_dead_run.OUTAGE_TAG}: the model was "
                          f"unavailable ({n})", WORKER_LOGIN)
                 for n in range(3)]
        return rows

    def _decide(self, td: Path, comments_payload) -> str:
        exec_path = td / "execution.json"
        exec_path.write_text(json.dumps(
            [{"type": "result", "is_error": True, "subtype": "error_during_execution"}]
        ))
        proc = subprocess.run(  # nosec B603 — fixed argv, our own script
            [sys.executable, str(ROOT / "scripts" / "fix_dead_run.py"),
             "decide", str(exec_path),
             "--comments-json", _write(td, "comments.json", comments_payload)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.splitlines()[0]

    def test_the_cap_counts_the_deaths_on_the_last_page(self):
        with tempfile.TemporaryDirectory() as raw:
            action = self._decide(Path(raw), pages(self._record(), MAX_PAGE))
        self.assertEqual(action, "hold")

    def test_the_truncated_record_retries_past_the_cap(self):
        with tempfile.TemporaryDirectory() as raw:
            action = self._decide(Path(raw), self._record()[:MAX_PAGE])
        self.assertEqual(action, "retry")


# --------------------------------------------------------------------------
# The wiring: every workflow read of this record asks for every page.
# --------------------------------------------------------------------------
#: Any path on GitHub's issue-comments endpoint, however the workflow spells
#: the repo and the PR number.
_COMMENT_ENDPOINT = re.compile(r"issues/[^\s\"']+/comments")
#: A write, not a read — the gate's carry note and every `gh api … -F body=`.
_WRITE_FLAGS = ("-F body=", "--method POST", "--input", "-f body=")


def logical_lines(text: str):
    """The file's shell commands with backslash continuations joined, so a
    flag on the line after `gh api` is seen as part of the same command."""
    out, buf, start = [], "", 0
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not buf:
            start = n
            buf = stripped
        else:
            buf += " " + stripped
        if buf.endswith("\\"):
            buf = buf[:-1]
            continue
        out.append((start, buf))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def comment_reads():
    """Every `gh api` READ of the comment record in every workflow here."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for lineno, line in logical_lines(path.read_text(encoding="utf-8")):
            if "gh api" not in line or not _COMMENT_ENDPOINT.search(line):
                continue
            if any(flag in line for flag in _WRITE_FLAGS):
                continue
            found.append((f"{path.name}:{lineno}", line))
    return found


def step_body(workflow: str, job: str, name: str) -> str:
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    runs = [s["run"] for s in doc["jobs"][job]["steps"] if s.get("name") == name]
    assert len(runs) == 1, f"expected exactly one {name!r} step in {workflow}"
    return runs[0]


class EveryWorkflowReadsEveryPageTest(unittest.TestCase):
    """Criterion 3: a test fails if the fetch loses pagination again."""

    def test_no_workflow_reads_the_comment_record_unpaginated(self):
        offenders = [
            f"{where}: {line}" for where, line in comment_reads()
            if "--paginate" not in line
        ]
        self.assertEqual(offenders, [], (
            "these reads see GitHub's default page size — the OLDEST 30 "
            "comments — and freeze on any PR longer than that:\n"
            + "\n".join(offenders)
        ))

    def test_the_reads_are_actually_found(self):
        """Anti-vacuity for the guard itself: a regex that matched nothing
        would pass the assertion above on any repo at all."""
        self.assertGreaterEqual(len(comment_reads()), 10, comment_reads())

    def test_the_gate_writes_one_flat_record_for_the_decision(self):
        body = step_body("merge-gate.yml", "evaluate", "Evaluate and merge")
        fetch = [line for _, line in logical_lines(body)
                 if "gh api" in line and "/tmp/comments.json" in line
                 and not any(f in line for f in _WRITE_FLAGS)]
        self.assertEqual(len(fetch), 1, body)
        self.assertIn("--paginate", fetch[0])
        self.assertIn("--slurp", fetch[0])
        self.assertIn("per_page=100", fetch[0])
        self.assertIn("|| echo '[]' > /tmp/comments.json", fetch[0],
                      "the fail-closed substitute must survive (criterion 2)")

    def test_the_carried_verdict_mark_is_searched_in_the_whole_record(self):
        """merge-gate.yml greps the fetched record for the carry mark before
        re-posting it. It reads the same file the fetch writes, so fixing the
        fetch fixes it — pinned so the two cannot drift apart."""
        body = step_body("merge-gate.yml", "evaluate", "Evaluate and merge")
        self.assertIn('grep -q "$CARRY_MARK" /tmp/comments.json', body)

    def test_the_review_skip_reads_the_same_record_the_gate_does(self):
        body = step_body("qa-review.yml", "review", "Decide review")
        fetch = [line for _, line in logical_lines(body)
                 if "gh api" in line and "/tmp/decide-comments.json" in line]
        self.assertEqual(len(fetch), 1, body)
        self.assertIn("--paginate", fetch[0])
        self.assertIn("--slurp", fetch[0])

    def test_the_fix_loop_routes_on_the_whole_record(self):
        """agent-fix.yml chooses fix mode or conflict mode on a DIRTY PR from
        the latest critic verdict and the last push marker. Truncated, a long
        PR picks the wrong mode."""
        body = step_body("agent-fix.yml", "fix",
                         "Resolve PR, mode, and attempt budget")
        for needle in ("VERDICT=", "LAST_VERDICT_IDX=", "LAST_PUSH_IDX="):
            line = [ln for _, ln in logical_lines(body) if ln.startswith(needle)]
            self.assertEqual(len(line), 1, f"{needle} in:\n{body}")
            self.assertNotIn("gh api", line[0], (
                f"{needle} still reads the endpoint itself; the three routing "
                "reads share one paginated fetch"
            ))

    def test_the_death_cap_reads_the_whole_record(self):
        body = step_body("agent-fix.yml", "fix", "Report")
        fetch = [line for _, line in logical_lines(body)
                 if "gh api" in line and _COMMENT_ENDPOINT.search(line)
                 and not any(f in line for f in _WRITE_FLAGS)]
        self.assertTrue(fetch, body)
        for line in fetch:
            self.assertIn("--paginate", line)
            self.assertIn("--slurp", line)


class TheFlattenerHasOneDefinitionTest(unittest.TestCase):
    """`gh api --paginate --slurp` emits one array per page, and nine reads in
    these workflows already relied on a single flattener (DRE-2030). The gate
    joins them rather than growing a second copy of the rule."""

    def test_the_gate_owns_the_flattener_and_fix_context_reuses_it(self):
        import fix_context

        self.assertIs(fix_context.flatten_pages, merge_gate.flatten_pages)

    def test_it_flattens_pages_and_passes_a_flat_list_through(self):
        rows = [{"body": "a"}, {"body": "b"}, {"body": "c"}]
        self.assertEqual(merge_gate.flatten_pages([rows[:2], rows[2:]]), rows)
        self.assertEqual(merge_gate.flatten_pages(rows), rows)
        self.assertEqual(merge_gate.flatten_pages([]), [])
        self.assertEqual(merge_gate.flatten_pages([[]]), [])

    def test_an_unreadable_payload_raises_rather_than_half_reading(self):
        for payload in ({"nope": 1}, [1, 2], [["a"]]):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    merge_gate.flatten_pages(payload)


if __name__ == "__main__":
    unittest.main()
