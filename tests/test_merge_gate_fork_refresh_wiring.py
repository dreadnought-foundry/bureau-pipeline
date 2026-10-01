"""The merge gate's fork refresh (DRE-5070, the second half of DRE-4912).

On 2026-09-25 the gate merged agent-bureau #2773 at 13:06 PT on a green CI
that had run against `main` as it stood two hours earlier — before #2777
added a sibling `0062_*` alembic migration. `main` forked: every open pull
request's CI went red (#2786 inherited both heads through no fault of its
own), the release train no-op'd on every commit, and the operator's console
deploy died at the migration task after pushing images.

The gate is the one place that runs at the moment of merging and already
holds the compare record, so it asks `scripts/order_sensitive_refresh.py`
(DRE-5066) one question right before it merges. On `refresh` it updates the
branch from `main` instead of merging, so CI re-runs on a fresh merge ref and
the repo's own migration-head gate refuses the second head before it lands.
On `wait` it stops. Only `proceed` reaches the card advance and the merge.

What this is NOT is the currency gate DRE-2416 retired: a behind head that
adds nothing under a declared order-sensitive path merges exactly as it does
today, and a repo with no `.github/bureau/merge-recheck.json` pays nothing.

Two halves, the same shape as tests/test_merge_gate_draft_scenario.py:

  * WiringTest — the block's place in the shipped `Evaluate and merge` step:
    after the merge guard, before the Linear advance, which still precedes
    `gh pr merge`, with no lane write of its own.
  * The scenario classes — that step's body run for real by bash against a
    stub `gh` answering with 2026-09-25's payloads, and a stub
    `linear_ops.py` recording whether the advance was reached.
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

import order_sensitive_refresh as osr  # noqa: E402
import step_shell  # noqa: E402

GUARD = '[ "$DECISION" = "merge" ] || exit 0'
# Byte for byte as they stand on `main` before this card (criterion 7).
ADVANCE_LINE = ('[ -n "$CARD" ] && python3 .bureau-pipeline/scripts/'
                'linear_ops.py advance "$CARD" "In Review" "In Progress" '
                '|| true')
MERGE_LINE = ('if ! gh pr merge "$PR" --merge --delete-branch '
              '--match-head-commit "$SHA"; then')
DECIDE_CALL = "order_sensitive_refresh.py decide"
REFRESH_READ = "grep -m1 '^decision=' /tmp/refresh-decision"
REFRESH_BRANCH = 'if [ "$REFRESH" = "refresh" ]; then'
# The note's marker, verbatim prefix (the contract shared with DRE-5066):
# the decision module's own receipt, which is how the note becomes the
# at-most-once-per-tip receipt the module reads back.
MARKER_PREFIX = "Merge gate: refreshed onto "
NOTE_PREFIX = "♻️ " + MARKER_PREFIX

VERSIONS = "console/backend/alembic/versions/"
OURS = VERSIONS + "0062_workflow_run_actor.py"
THEIRS = VERSIONS + "0062_runner_ledger.py"
DECLARATION = {
    "$schema_note": "order-sensitive paths (DRE-4912)",
    "order_sensitive_paths": [VERSIONS],
}

PR = 2773
HEAD = "2773a11ce0de5ea1b2c3d4e5f60718293a4b5c6d"
MERGE_BASE = "0061b0115a3b1e0f2c3d4e5f60718293a4b5c6d7"
TIP = "2777c0de5ea1b2c3d4e5f60718293a4b5c6d7e8f"
BRANCH = "agent/DRE-4880-workflow-run-actor"
CARD = "DRE-4880"
REPO = "dreadnought-foundry/agent-bureau"
QA_LOGIN = "agent-bureau-qa-bot[bot]"
WORKER_LOGIN = "agent-bureau-bot[bot]"


def evaluate_step() -> dict:
    doc = yaml.safe_load(step_shell.workflow_source(WORKFLOW))
    steps = doc["jobs"]["evaluate"]["steps"]
    found = [s for s in steps if s.get("name") == "Evaluate and merge"]
    assert len(found) == 1, "expected exactly one 'Evaluate and merge' step"
    return found[0]


def evaluate_body() -> str:
    return step_shell.step_shell(evaluate_step())


def branch_span(block: str, opener: str) -> str:
    """The text of the shell `if` branch opened by the line `opener`, up to
    the `else`/`elif`/`fi` at the opener's own indentation."""
    lines = block.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == opener)
    indent = len(lines[start]) - len(lines[start].lstrip())
    for end in range(start + 1, len(lines)):
        ln = lines[end]
        if (len(ln) - len(ln.lstrip()) == indent
                and ln.strip().split(" ")[0] in ("fi", "else", "elif")):
            return "\n".join(lines[start:end + 1])
    raise AssertionError(f"unterminated branch: {opener}")


class WiringTest(unittest.TestCase):
    """Where the fork-refresh block sits in the shipped step, and what it
    may and may not write."""

    @classmethod
    def setUpClass(cls):
        cls.body = evaluate_body()
        cls.guard = cls.body.find(GUARD)
        cls.advance = cls.body.find(ADVANCE_LINE)
        cls.merge = cls.body.find(MERGE_LINE)
        cls.decide = cls.body.find(DECIDE_CALL)
        # The whole block: from the merge guard to the advance line.
        cls.block = cls.body[cls.guard:cls.advance]

    def test_three_indices_in_source_order(self):
        """guard < refresh block < advance < merge. A block landed between
        the advance and the merge would have walked the card to In Review
        before deciding not to merge — this ordering fails it."""
        for name in ("guard", "advance", "merge", "decide"):
            self.assertGreater(getattr(self, name), -1, f"{name} not found")
        self.assertLess(self.guard, self.decide)
        self.assertLess(self.decide, self.advance,
                        "the fork-refresh decision must precede the advance")
        self.assertLess(self.advance, self.merge)
        for needle in ("/update-branch", "refresh-decision",
                       "base-advance.json", MARKER_PREFIX):
            where = self.body.find(needle)
            self.assertGreater(where, self.guard, needle)
            self.assertLess(where, self.advance, needle)

    def test_the_advance_and_the_merge_stay_one_adjacent_arm(self):
        """Only comments may stand between the advance and the merge — the
        one merge arm they are on `main` today."""
        between = self.body[self.advance + len(ADVANCE_LINE):self.merge]
        for ln in between.splitlines():
            self.assertTrue(ln.strip() == "" or ln.strip().startswith("#"),
                            f"code between the advance and the merge: {ln!r}")

    def test_the_block_writes_nothing_to_the_card_lane(self):
        """A refreshed pull request is one whose merge did not happen, so
        the card stays exactly where the gate found it."""
        self.assertNotIn("linear_ops.py", self.block)

    def test_the_base_advance_is_fetched_only_when_behind(self):
        """GET compare/{merge_base}...{base}, from `merge_base_commit.sha`,
        and only under a `behind_by` check — a current head costs no call."""
        self.assertIn("behind_by", self.block)
        self.assertIn("merge_base_commit", self.block)
        fetch = 'compare/$MERGE_BASE...$BASE'
        self.assertIn(fetch, self.block)
        guard = next(ln.strip() for ln in self.block.splitlines()
                     if ln.strip().startswith("if ")
                     and "$BEHIND" in ln and "-gt 0" in ln)
        self.assertIn(fetch, branch_span(self.block, guard))
        # The blip substitute: `{}` never reads as "safe to merge" — the
        # module answers `wait` on it.
        self.assertIn("echo '{}' > /tmp/base-advance.json", self.block)

    def test_the_decision_module_gets_the_contracted_inputs(self):
        call = self.body[self.decide:self.body.find("\n", self.body.find(
            "--receipts-file", self.decide))]
        for flag in ("--compare-file /tmp/compare.json",
                     "--base-advance-file /tmp/base-advance.json",
                     "--declaration-file .github/bureau/merge-recheck.json",
                     "--receipts-file"):
            self.assertIn(flag, call)

    def test_the_put_is_in_the_refresh_branch_and_carries_the_head(self):
        arm = branch_span(self.block, REFRESH_BRANCH)
        self.assertGreater(self.block.find(REFRESH_BRANCH),
                           self.block.find(REFRESH_READ))
        put = next(ln for ln in arm.splitlines() if "-X PUT" in ln)
        self.assertIn('pulls/$PR/update-branch', put)
        self.assertIn('expected_head_sha="$SHA"', put)

    def test_the_note_uses_gate_note_and_the_verbatim_marker(self):
        arm = branch_span(self.block, REFRESH_BRANCH)
        self.assertIn("gate_note.py", arm)
        self.assertIn(f'{MARKER_PREFIX}$TIP', arm)
        self.assertIn("♻️", arm)
        # The marker IS the decision module's receipt, so the note is what
        # holds the gate to one refresh per `main` tip.
        self.assertEqual(osr.receipt_marker("X"), MARKER_PREFIX + "X")
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, self.block)

    def test_the_put_and_the_note_run_as_the_qa_bot(self):
        """Premortem Q1: a qa-bot write fires `synchronize` as
        agent-bureau-qa-bot, which qa-review.yml admits; a `github.token`
        write would fire no workflows at all."""
        self.assertEqual(evaluate_step()["env"]["GH_TOKEN"],
                         "${{ steps.qa.outputs.token }}")
        self.assertNotIn("WORKFLOW_TOKEN", self.block)
        self.assertNotIn("github.token", self.block)
        self.assertNotIn("GH_TOKEN=", self.block)

    def test_refresh_and_wait_exit_before_the_advance(self):
        arm = branch_span(self.block, REFRESH_BRANCH)
        self.assertEqual(arm.splitlines()[-2].strip(), "exit 0")
        # Anything but `proceed` exits clean — fail closed on a shape drift.
        tail = self.block[self.block.find(REFRESH_BRANCH) + len(arm):]
        self.assertIn('[ "$REFRESH" = "proceed" ]', tail)
        self.assertIn("exit 0", tail)

    def test_the_header_records_the_rule_and_its_dre_2416_boundary(self):
        header = step_shell.workflow_source(WORKFLOW).split("\nname:", 1)[0]
        for needle in ("DRE-4912", "DRE-2416", "order-sensitive",
                       "fork", "once per"):
            self.assertIn(needle, header)


# --------------------------------------------------------------------------- #
# Scenario — the shipped step, executed                                        #
# --------------------------------------------------------------------------- #

# A stub `gh` answering from a fixture, recording every call, keeping the PR's
# comments in a file so gate_note.py's post-then-converge pass sees its own
# write. `--slurp` answers the way GitHub's CLI does: one array per page.
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


def write_comments(rows):
    with open(os.environ["COMMENTS"], "w") as fh:
        json.dump(rows, fh)


if args[:2] == ["pr", "view"]:
    field = (opt("--jq") or "").lstrip(".")
    value = fx["pr"][field]
    emit("true" if value is True else "false" if value is False else str(value))

if args[:2] == ["run", "list"]:
    emit([])

if args[:2] == ["pr", "merge"]:
    emit("merged")

if args[0] == "api":
    method = opt("--method") or opt("-X") or "GET"
    path = next(a for a in args if a.startswith("repos/"))
    if path.endswith("/update-branch"):
        if fx["update_branch_fails"]:
            sys.stderr.write("HTTP 422: expected head sha didn't match\n")
            raise SystemExit(1)
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
        if path.split("/compare/", 1)[1].startswith(fx["merge_base"] + "..."):
            if fx["base_advance"] is None:
                sys.stderr.write("HTTP 502\n")
                raise SystemExit(1)
            emit(fx["base_advance"])
        emit(fx["compare"])
    if "actions/runs" in path:
        emit({"workflow_runs": []})
    if "/commits" in path:
        emit(fx["pr_commits"])
    if "/issues/" in path and "/comments" in path:
        if "--slurp" in args:
            emit([comments()])
        emit([] if "page=2" in path or "page=3" in path else comments())
    if "/pulls/" in path:
        emit(fx["author"] if opt("--jq") else {})
    emit({})

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''

# Stands in for the Linear write: records every call, touches no network.
LINEAR_STUB = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["LINEAR_LOG"], "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
'''


def added(name, status="added"):
    return {"filename": name, "status": status}


def compare_payload(files, behind_by=4):
    """`/tmp/compare.json`: GET compare/main...{head}."""
    return {
        "status": "diverged" if behind_by else "ahead",
        "ahead_by": 3,
        "behind_by": behind_by,
        "merge_base_commit": {"sha": MERGE_BASE},
        "base_commit": {"sha": TIP},
        "files": files,
    }


class ShippedStepResult:
    def __init__(self, proc, calls, linear, comments):
        self.proc = proc
        self.calls = calls
        self.linear = linear
        self.comments = comments

    @property
    def merges(self):
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]

    @property
    def puts(self):
        return [c for c in self.calls if c[:1] == ["api"] and "-X" in c
                and c[c.index("-X") + 1] == "PUT"]

    @property
    def base_advance_reads(self):
        return [c for c in self.calls
                if any(f"/compare/{MERGE_BASE}..." in a for a in c)]

    @property
    def advances(self):
        return [c for c in self.linear if c[:1] == ["advance"]]

    @property
    def notes(self):
        return [c["body"] for c in self.comments
                if c["body"].startswith(NOTE_PREFIX)]

    def explain(self):
        return (f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}\n"
                f"gh calls: {self.calls}\nlinear: {self.linear}")


def run_shipped_step(*, pr_files, main_files, declared=True, behind_by=4,
                     update_branch_fails=False, seed_comments=None,
                     base_advance_blips=False, body=None) -> ShippedStepResult:
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
            {"name": "Console backend (pytest)", "status": "completed",
             "conclusion": "success", "check_suite": {"id": 1}},
        ],
        "compare": compare_payload(pr_files, behind_by),
        "merge_base": MERGE_BASE,
        "base_advance": None if base_advance_blips else {
            "status": "ahead", "ahead_by": behind_by, "files": main_files,
        },
        "update_branch_fails": update_branch_fails,
        "pr_commits": [{"sha": HEAD, "commit": {"message": "feat"}}],
        "author": WORKER_LOGIN,
    }
    comments = [{
        "id": 1,
        "user": {"login": QA_LOGIN},
        "body": f"🔎 QA Critic — VERDICT: APPROVE @{HEAD}",
    }] + list(seed_comments or [])
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        # The pipeline checkout: every real script, except the Linear write.
        scripts = td / ".bureau-pipeline" / "scripts"
        scripts.mkdir(parents=True)
        for entry in (ROOT / "scripts").iterdir():
            if entry.name != "linear_ops.py":
                os.symlink(entry, scripts / entry.name)
        (scripts / "linear_ops.py").write_text(LINEAR_STUB)
        # The product checkout: the declaration, when the repo made one.
        if declared:
            (td / ".github" / "bureau").mkdir(parents=True)
            (td / ".github" / "bureau" / "merge-recheck.json").write_text(
                json.dumps(DECLARATION))
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "comments.json").write_text(json.dumps(comments))
        (td / "gh.log").write_text("")
        (td / "linear.log").write_text("")
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-c", body if body is not None else evaluate_body()],
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
                "REPO_FULL": REPO,
                "WORKFLOW_TOKEN": "workflow-token",
                "QA_LOGIN": QA_LOGIN,
            },
        )
        read = lambda p: [json.loads(ln) for ln in  # noqa: E731
                          p.read_text().splitlines() if ln]
        return ShippedStepResult(proc, read(td / "gh.log"),
                                 read(td / "linear.log"),
                                 json.loads((td / "comments.json").read_text()))


class TheForkIsRefreshedNotMergedTest(unittest.TestCase):
    """2026-09-25, replayed through the shipped step: the pull request adds
    `0062_workflow_run_actor.py`, `main` since the merge base added
    `0062_runner_ledger.py`, and the repo declares the versions directory."""

    @classmethod
    def setUpClass(cls):
        cls.result = run_shipped_step(pr_files=[added(OURS)],
                                      main_files=[added(THEIRS)])

    def test_the_step_exits_clean(self):
        self.assertEqual(self.result.proc.returncode, 0, self.result.explain())

    def test_the_gate_never_merges_the_fork(self):
        self.assertEqual(self.result.merges, [], self.result.explain())

    def test_the_card_is_not_advanced(self):
        """The advance is the first act of the merge arm; a refresh never
        reaches it, so the card stays where the gate found it."""
        self.assertEqual(self.result.advances, [], self.result.explain())
        self.assertEqual(self.result.linear, [], self.result.explain())

    def test_exactly_one_update_branch_bound_to_the_evaluated_head(self):
        self.assertEqual(len(self.result.puts), 1, self.result.explain())
        put = self.result.puts[0]
        self.assertIn(f"repos/{REPO}/pulls/{PR}/update-branch", put)
        self.assertIn(f"expected_head_sha={HEAD}", put)

    def test_what_main_gained_was_read_from_the_merge_base(self):
        self.assertEqual(len(self.result.base_advance_reads), 1)
        self.assertIn(f"repos/{REPO}/compare/{MERGE_BASE}...main",
                      self.result.base_advance_reads[0])

    def test_one_note_names_the_tip_and_what_main_gained(self):
        self.assertEqual(len(self.result.notes), 1, self.result.comments)
        note = self.result.notes[0]
        self.assertTrue(note.startswith(NOTE_PREFIX + TIP), note)
        self.assertIn(THEIRS, note)
        for forbidden in ("VERDICT:", "QA Critic", "QA Verifier"):
            self.assertNotIn(forbidden, note)

    def test_a_second_wake_before_the_refresh_lands_does_nothing(self):
        """The note is the receipt: at most one refresh per `main` tip. The
        branch still reads behind (the update is async) and the gate waits —
        no second PUT, no merge, no second note."""
        again = run_shipped_step(
            pr_files=[added(OURS)], main_files=[added(THEIRS)],
            seed_comments=[{"id": 50, "user": {"login": QA_LOGIN},
                            "body": self.result.notes[0]}])
        self.assertEqual(again.proc.returncode, 0, again.explain())
        self.assertEqual(again.puts, [], again.explain())
        self.assertEqual(again.merges, [], again.explain())
        self.assertEqual(again.advances, [])
        self.assertEqual(len(again.notes), 1)


class ProceedStillMergesTest(unittest.TestCase):
    """Anti-vacuity: the same step reaches the advance and the merge
    unchanged wherever merging cannot fork `main`."""

    def assert_merged_untouched(self, result):
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(len(result.merges), 1, result.explain())
        self.assertIn("--match-head-commit", result.merges[0])
        self.assertEqual(result.advances,
                         [["advance", CARD, "In Review", "In Progress"]])
        self.assertEqual(result.puts, [], result.explain())
        self.assertEqual(result.notes, [])

    def test_no_declaration_merges_the_same_payloads(self):
        """The harness repo's shape: no declaration, the module answers
        `proceed` before it reads anything else — and the gate pays no
        base-advance read for it."""
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added(THEIRS)], declared=False)
        self.assert_merged_untouched(result)
        self.assertEqual(result.base_advance_reads, [])

    def test_a_behind_head_with_nothing_under_the_prefix_merges(self):
        """DRE-2416 stands: currency is not a gate."""
        result = run_shipped_step(
            pr_files=[added("console/backend/app/runs.py", "modified")],
            main_files=[added("console/frontend/src/App.tsx", "modified")])
        self.assert_merged_untouched(result)

    def test_only_one_side_adding_under_the_prefix_merges(self):
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added("README.md", "modified")])
        self.assert_merged_untouched(result)

    def test_a_current_head_merges_without_a_base_advance_read(self):
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added(THEIRS)], behind_by=0)
        self.assert_merged_untouched(result)
        self.assertEqual(result.base_advance_reads, [])


class FailClosedTest(unittest.TestCase):

    def test_a_refused_put_posts_no_note_and_does_not_merge(self):
        """Premortem Q3: a concurrent push answers 422 against
        `expected_head_sha`. No note — the next evaluation retries."""
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added(THEIRS)],
                                  update_branch_fails=True)
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(len(result.puts), 1)
        self.assertEqual(result.notes, [], result.comments)
        self.assertEqual(result.merges, [])
        self.assertEqual(result.advances, [])

    def test_an_unreadable_base_advance_waits(self):
        """A blip is not "safe to merge": the module answers `wait`."""
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added(THEIRS)],
                                  base_advance_blips=True)
        self.assertEqual(result.proc.returncode, 0, result.explain())
        self.assertEqual(result.merges, [], result.explain())
        self.assertEqual(result.puts, [])
        self.assertEqual(result.advances, [])
        self.assertIn("decision=wait", result.proc.stdout)


class WithoutTheArmTheForkMergesTest(unittest.TestCase):
    """Anti-vacuity, direction two: the step as it stood on `main` before
    this card — the block cut out and nothing else changed — merges the
    fork and advances the card. That is 13:06 PT on 2026-09-25."""

    def test_the_pre_fix_step_merges_the_second_head(self):
        body = evaluate_body()
        start = body.find(GUARD) + len(GUARD)
        end = body.find("# Both gates green")
        self.assertGreater(end, start)
        pre_fix = body[:start] + "\n\n" + body[end:]
        self.assertNotIn(DECIDE_CALL, pre_fix)
        result = run_shipped_step(pr_files=[added(OURS)],
                                  main_files=[added(THEIRS)], body=pre_fix)
        self.assertEqual(len(result.merges), 1, result.explain())
        self.assertEqual(len(result.advances), 1)
        self.assertEqual(result.puts, [])


if __name__ == "__main__":
    unittest.main()
