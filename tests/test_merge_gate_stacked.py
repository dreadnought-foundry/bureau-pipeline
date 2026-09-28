"""The gate refuses to land another open PR's unapproved work (DRE-4103).

THE INCIDENT (2026-09-16 17:08 PT, agent-bureau). Three branches were
stacked, each on the one below:

    ec7336be (#2582 head) --7 commits--> 81af6ed (#2583 head)
                          --3 commits--> ce204d38 (#2585 head)

#2582 and #2583 stood under REQUEST_CHANGES; #2585 carried an APPROVE bound
to its own head. Merge Gate run 35165261547 read #2585's verdicts and
nothing else, decided `merge`, and the merge commit made all three heads
reachable from `main` — so GitHub marked the two rejected pull requests
merged too, and linear-sync closed all three cards as Done.

`decide()` was right about the pull request it was given. The hole was that
the gate had no notion of stacking at all: it never asked which OTHER open
pull requests' heads the branch carries. Condition S (`evaluate_stack`) asks
that, over the stack record `stacked_prs.py gather` writes, and HOLDS while
any of them lacks an APPROVE bound to its own head — naming each one, on
the PR, in plain English.

The shape below is the real one: the SHAs are the incident's (padded to the
full 40 hex the gate requires), and the compare record lists every commit
#2585 would bring into `main`, the two lower heads among them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "merge-gate.yml"

sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/agent-bureau")
os.environ.setdefault("REPO_SLUG", "agent-bureau")
os.environ.setdefault("GH_TOKEN", "x")

import merge_gate  # noqa: E402
import reconcile  # noqa: E402
import stacked_prs  # noqa: E402

QA = "agent-bureau-qa-bot[bot]"
REPO = "dreadnought-foundry/agent-bureau"


def sha(prefix: str) -> str:
    """A full 40-hex SHA from the incident's abbreviated one."""
    return (prefix + "0" * 40)[:40]


HEAD_2582 = sha("ec7336be")
HEAD_2583 = sha("81af6ed1")
HEAD_2585 = sha("ce204d38")

# #2585's own contribution relative to main: #2582's commits up to its head,
# the seven that make #2583, and the three that make #2585.
BRANCH_SHAS = (
    [sha(f"a{i:02d}") for i in range(4)] + [HEAD_2582]
    + [sha(f"b{i:02d}") for i in range(6)] + [HEAD_2583]
    + [sha(f"c{i:02d}") for i in range(2)] + [HEAD_2585]
)
COMPARE = {
    "status": "ahead",
    "behind_by": 0,
    "total_commits": len(BRANCH_SHAS),
    "commits": [{"sha": s} for s in BRANCH_SHAS],
    "files": [{"filename": "console/src/capacity.tsx", "sha": "f" * 40,
               "status": "modified", "additions": 3, "deletions": 1,
               "changes": 4}],
}
COMMITS = stacked_prs.branch_commits(COMPARE)

OPEN_ALL = [
    {"number": 2582, "head_sha": HEAD_2582},
    {"number": 2583, "head_sha": HEAD_2583},
    {"number": 2585, "head_sha": HEAD_2585},
]


def verdict(token: str, at: str, login: str = QA) -> dict:
    return {"user": {"login": login, "type": "Bot"},
            "body": f"🔎 QA Critic — VERDICT: {token} @{at}\n\nreasons"}


def rejected(at: str) -> dict:
    return verdict("REQUEST_CHANGES", at)


def approved(at: str) -> dict:
    return verdict("APPROVE", at)


def green_checks() -> list:
    return [{"name": "tests", "status": "completed", "conclusion": "success",
             "check_suite": {"id": 1}}]


def stack_record(open_prs=OPEN_ALL, comments=None, **extra) -> dict:
    """The record `stacked_prs.py gather` writes, in its on-disk shape:
    comment records are `gh api --paginate --slurp` pages."""
    record = {"readable": True, "open_prs": list(open_prs),
              "comments": {str(n): [c] for n, c in (comments or {}).items()}}
    record.update(extra)
    return record


INCIDENT = stack_record(comments={
    2582: [rejected(HEAD_2582)],
    2583: [rejected(HEAD_2583)],
})
ALL_APPROVED = stack_record(comments={
    2582: [approved(HEAD_2582)],
    2583: [approved(HEAD_2583)],
})


def gate(record=INCIDENT, commits=COMMITS, comments=None, **kw):
    """`decide()` for #2585 at the moment of the incident: CI green, its own
    critic APPROVE bound to its own head."""
    base = dict(
        head_sha=HEAD_2585,
        qa_login=QA,
        check_runs=green_checks(),
        comments=comments if comments is not None else [approved(HEAD_2585)],
        pr_number=2585,
        stack=None if record is None else stacked_prs.read_stack(record),
        branch_commits=commits,
    )
    base.update(kw)
    return merge_gate.decide(**base)


class TheIncidentTest(unittest.TestCase):
    """Criterion 4, first shape: a three-deep stack, the bottom two
    rejected."""

    def test_the_merge_the_gate_made_that_evening(self):
        """THE MUTATION CHECK. Withhold the stack record — the pre-DRE-4103
        gate — and the same inputs merge. Everything below is proving the
        stack record is what holds, not some other condition."""
        self.assertEqual(gate(record=None).action, "merge")

    def test_the_gate_holds(self):
        decision = gate()
        self.assertEqual(decision.action, "hold", decision.reason)

    def test_the_refusal_names_each_blocking_pull_request_and_its_verdict(self):
        reason = gate().reason
        for number in ("#2582", "#2583"):
            self.assertIn(number, reason)
        self.assertEqual(reason.count("REQUEST_CHANGES"), 2, reason)
        self.assertIn("asked for changes", reason)

    def test_the_blockers_are_reported_for_the_note_on_the_pr(self):
        self.assertEqual(gate().stacked_on, [2582, 2583])

    def test_the_refusal_carries_no_verdict_shaped_text(self):
        """The reason is posted on the PR under the qa-bot login. A body
        carrying the critic's marker would re-wake the gate's own
        issue_comment leg and would BE a verdict-shaped credential
        (standards/untrusted-content.md)."""
        reason = gate().reason
        for forbidden in (merge_gate.CRITIC_MARKER, merge_gate.VERIFIER_MARKER,
                          "VERDICT:"):
            self.assertNotIn(forbidden, reason)

    def test_one_rejected_pull_request_below_is_enough(self):
        record = stack_record(comments={
            2582: [approved(HEAD_2582)],
            2583: [rejected(HEAD_2583)],
        })
        decision = gate(record)
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.stacked_on, [2583])
        self.assertNotIn("#2582", decision.reason)

    def test_the_pull_requests_own_rejection_still_reads_first(self):
        """The stack is asked only once the pull request's own verdicts say
        merge; its own REQUEST_CHANGES is the more direct answer."""
        decision = gate(comments=[rejected(HEAD_2585)])
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.stacked_on, [])
        self.assertNotIn("#2582", decision.reason)

    def test_a_draft_on_a_rejected_stack_is_held_not_handed_to_a_human(self):
        """Condition 4's note says "everything else is green". On a rejected
        stack that sentence is false, so the stack answers first."""
        decision = gate(is_draft=True)
        self.assertEqual(decision.action, "hold")


class EveryReadingThatIsNotAnApprovalHoldsTest(unittest.TestCase):
    """What "a standing non-APPROVE critic verdict" covers. The gate merges
    a stack only when every open pull request under it is APPROVED at its
    own head; anything else is named and held."""

    def reading_for_2583(self, *comments):
        record = stack_record(comments={
            2582: [approved(HEAD_2582)],
            2583: list(comments),
        })
        decision = gate(record)
        self.assertEqual(decision.action, "hold", decision.reason)
        self.assertEqual(decision.stacked_on, [2583])
        return decision.reason

    def test_no_review_yet(self):
        self.assertIn("not reviewed", self.reading_for_2583())

    def test_an_approval_of_an_older_commit(self):
        reason = self.reading_for_2583(approved(sha("0dd")))
        self.assertIn("older commit", reason)

    def test_a_forged_approval(self):
        """Only the qa-bot App's verdicts count, below as on the PR itself."""
        reason = self.reading_for_2583(
            verdict("APPROVE", HEAD_2583, login="someone"))
        self.assertIn("not reviewed", reason)

    def test_the_latest_verdict_wins(self):
        self.reading_for_2583(approved(HEAD_2583), rejected(HEAD_2583))
        record = stack_record(comments={
            2582: [approved(HEAD_2582)],
            2583: [rejected(HEAD_2583), approved(HEAD_2583)],
        })
        self.assertEqual(gate(record).action, "merge")


class LegitimateStacksStillMergeTest(unittest.TestCase):
    """Criterion 3 and criterion 4's other two shapes."""

    def test_a_three_deep_stack_fully_approved_merges(self):
        decision = gate(ALL_APPROVED)
        self.assertEqual(decision.action, "merge", decision.reason)
        self.assertEqual(decision.stacked_on, [])

    def test_a_stack_whose_parents_have_since_merged_merges(self):
        """#2582 and #2583 merged: they are no longer open, so they are no
        longer anything this merge could carry in unapproved. Their old
        REQUEST_CHANGES verdicts are still on their threads — the record
        never fetches them, because nothing open is stacked."""
        record = stack_record(open_prs=[OPEN_ALL[2]])
        decision = gate(record)
        self.assertEqual(decision.action, "merge", decision.reason)

    def test_a_stack_whose_direct_parent_has_merged_still_answers_for_the_rest(self):
        record = stack_record(open_prs=[OPEN_ALL[0], OPEN_ALL[2]],
                              comments={2582: [rejected(HEAD_2582)]})
        decision = gate(record)
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.stacked_on, [2582])

    def test_an_unrelated_rejected_pull_request_does_not_block(self):
        """Only a head this branch CARRIES blocks. A rejected pull request
        elsewhere in the repo is not in #2585's commits and is not asked."""
        record = stack_record(open_prs=OPEN_ALL[2:] + [
            {"number": 2600, "head_sha": sha("dead")}])
        self.assertEqual(gate(record).action, "merge")

    def test_no_other_open_pull_request_needs_no_commit_record(self):
        """With nothing else open there is nothing to be stacked on, so a
        blipped compare record (no commit list) costs nothing."""
        record = stack_record(open_prs=[OPEN_ALL[2]])
        self.assertEqual(gate(record, commits=None).action, "merge")


class UnknownHoldsTest(unittest.TestCase):
    """Criterion 5: a reading that cannot be taken says UNKNOWN and holds —
    never "there are no open pull requests"."""

    def test_open_pull_requests_that_cannot_be_listed(self):
        record = {"readable": False, "detail": "HTTP 502"}
        decision = gate(record)
        self.assertEqual(decision.action, "hold")
        self.assertTrue(decision.reason.startswith("UNKNOWN"), decision.reason)
        self.assertIn("HTTP 502", decision.reason)

    def test_a_record_that_is_not_a_record(self):
        for payload in (None, [], "x", {"readable": True},
                        {"readable": True, "open_prs": [{"number": "x"}]}):
            with self.subTest(payload=payload):
                decision = gate(payload if payload is not None else {})
                self.assertEqual(decision.action, "hold")
                self.assertIn("UNKNOWN", decision.reason)

    def test_a_commit_record_that_cannot_prove_what_the_branch_carries(self):
        """Other pull requests are open and the compare record is blipped or
        truncated: nothing can say none of their heads is in this branch."""
        decision = gate(commits=None)
        self.assertEqual(decision.action, "hold")
        self.assertTrue(decision.reason.startswith("UNKNOWN"))

    def test_a_stacked_pull_request_whose_thread_cannot_be_read(self):
        record = stack_record(comments={2582: [approved(HEAD_2582)]})
        record["comments"]["2583"] = None
        decision = gate(record)
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.stacked_on, [2583])
        self.assertIn("UNKNOWN", decision.reason)

    def test_a_stacked_pull_request_the_gatherer_never_read(self):
        """The gatherer and the gate compute the stack with the same function
        over the same compare record; if they ever disagree, the pull request
        the gate found and the gatherer did not is UNKNOWN, not approved."""
        record = stack_record(comments={2582: [approved(HEAD_2582)]})
        self.assertEqual(gate(record).action, "hold")


class HoldIsNotAFailureTest(unittest.TestCase):
    """Criterion 2: the refusal is a `hold` — exit 0, reconsidered the way
    any other held pull request is."""

    def run_cli(self, record, extra=()):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            files = {
                "check-runs": {"check_runs": green_checks()},
                "comments": [[approved(HEAD_2585)]],
                "workflow-runs": {"workflow_runs": []},
                "compare": COMPARE,
            }
            for name, payload in files.items():
                (td / f"{name}.json").write_text(json.dumps(payload))
            argv = [sys.executable, str(SCRIPT),
                    "--head-sha", HEAD_2585, "--qa-login", QA,
                    "--pr-number", "2585"]
            for name in files:
                argv += [f"--{name}-file", str(td / f"{name}.json")]
            if record is not None:
                (td / "stack.json").write_text(json.dumps(record))
            argv += ["--stack-file", str(td / "stack.json"), *extra]
            return subprocess.run(argv, capture_output=True, text=True)

    def test_the_incident_is_decided_hold_with_exit_zero(self):
        proc = self.run_cli(INCIDENT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("decision=hold", proc.stdout)
        self.assertIn("stacked_on=#2582, #2583", proc.stdout)

    def test_a_fully_approved_stack_merges_from_the_cli(self):
        proc = self.run_cli(ALL_APPROVED)
        self.assertIn("decision=merge", proc.stdout, proc.stderr)
        self.assertNotIn("stacked_on=", proc.stdout)

    def test_a_missing_stack_file_is_unknown_and_holds(self):
        proc = self.run_cli(None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("decision=hold", proc.stdout)
        self.assertIn("UNKNOWN", proc.stdout)

    def test_the_held_pull_request_is_one_reconcile_keeps_waking_the_gate_for(self):
        """Reconsidered "the way any other held PR is": the sweep re-triggers
        the gate for any In Review pull request whose own verdict binds its
        head, and a stack-held pull request's own APPROVE does."""
        pr = {"headRefOid": HEAD_2585, "comments": [{
            "author": {"login": "agent-bureau-qa-bot"},
            "body": approved(HEAD_2585)["body"],
        }]}
        self.assertTrue(reconcile.has_verdict(pr))


class AncestryTest(unittest.TestCase):
    """`stacked_prs` — the one home of "which open heads does this branch
    carry", read by both the gatherer and the gate."""

    def test_the_incidents_stack(self):
        self.assertEqual(
            stacked_prs.stacked_on(
                stacked_prs.read_stack(INCIDENT).open_prs, 2585, COMMITS),
            [(2582, HEAD_2582), (2583, HEAD_2583)],
        )

    def test_a_complete_commit_record(self):
        self.assertEqual(COMMITS, frozenset(BRANCH_SHAS))

    def test_a_truncated_or_blipped_commit_record_proves_nothing(self):
        truncated = dict(COMPARE, total_commits=300)
        for payload in ({}, None, [], truncated,
                        dict(COMPARE, commits="x"),
                        {"commits": COMPARE["commits"]}):
            with self.subTest(payload=payload if payload != truncated else "truncated"):
                self.assertIsNone(stacked_prs.branch_commits(payload))


def gh_answers(open_lines=None, threads=None, fail=()):
    """A stand-in for `stacked_prs._gh`: `(stdout, None)` or `(None, detail)`,
    recording every call."""
    calls = []

    def fake(args):
        calls.append(list(args))
        path = next(a for a in args[1:] if not a.startswith("-"))
        if any(f in path for f in fail):
            return None, "HTTP 502"
        if "pulls?state=open" in path:
            return "\n".join(json.dumps(x) for x in (open_lines or [])), None
        m = re.search(r"issues/(\d+)/comments", path)
        if m:
            return json.dumps([(threads or {}).get(int(m.group(1)), [])]), None
        return None, f"unexpected call {args}"

    return fake, calls


class GathererTest(unittest.TestCase):
    """`stacked_prs.gather_stack` — the one seam here that touches the
    network. It never raises: an unreadable read is written INTO the record,
    and the gate holds on it."""

    def gather(self, fake, compare=COMPARE):
        with mock.patch.object(stacked_prs, "_gh", fake):
            return stacked_prs.gather_stack(REPO, 2585, compare)

    def test_the_incident_record_reproduces_the_hold(self):
        fake, calls = gh_answers(OPEN_ALL, {2582: [rejected(HEAD_2582)],
                                            2583: [rejected(HEAD_2583)]})
        record = self.gather(fake)
        self.assertEqual(gate(record).action, "hold")
        self.assertEqual(gate(record).stacked_on, [2582, 2583])

    def test_only_stacked_pull_requests_threads_are_read(self):
        """One listing, plus one thread per pull request this branch carries
        — an unstacked repo, the ordinary case, costs exactly one call."""
        others = OPEN_ALL[2:] + [{"number": 2600, "head_sha": sha("dead")}]
        fake, calls = gh_answers(others)
        record = self.gather(fake)
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(gate(record).action, "merge")

    def test_the_listing_walks_every_page(self):
        fake, calls = gh_answers(OPEN_ALL[2:])
        self.gather(fake)
        self.assertIn("--paginate", calls[0])
        self.assertIn(f"repos/{REPO}/pulls?state=open&per_page=100", calls[0])

    def test_a_failed_listing_is_recorded_unreadable(self):
        fake, _ = gh_answers(fail=("pulls?state=open",))
        record = self.gather(fake)
        self.assertFalse(record["readable"])
        self.assertEqual(gate(record).action, "hold")

    def test_an_unparseable_listing_is_recorded_unreadable(self):
        def fake(args):
            return "<html>rate limited</html>", None
        record = self.gather(fake)
        self.assertFalse(record["readable"])

    def test_a_failed_thread_read_is_recorded_as_unknown(self):
        fake, _ = gh_answers(OPEN_ALL, {2582: [approved(HEAD_2582)]},
                             fail=("issues/2583/",))
        record = self.gather(fake)
        self.assertIsNone(record["comments"]["2583"])
        decision = gate(record)
        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.stacked_on, [2583])

    def test_a_blipped_compare_record_reads_no_threads(self):
        fake, calls = gh_answers(OPEN_ALL)
        record = self.gather(fake, compare={})
        self.assertEqual(len(calls), 1)
        self.assertEqual(gate(record, commits=None).action, "hold")

    def test_the_cli_writes_the_record_and_exits_zero_even_when_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "stack.json"
            (Path(td) / "compare.json").write_text("not json")
            fake, _ = gh_answers(fail=("pulls",))
            with mock.patch.object(stacked_prs, "_gh", fake):
                rc = stacked_prs.main([
                    "gather", "--repo", REPO, "--pr", "2585",
                    "--compare-file", str(Path(td) / "compare.json"),
                    "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(out.read_text())["readable"])


def evaluate_step() -> dict:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["evaluate"]["steps"]
    return next(s for s in steps if s.get("name") == "Evaluate and merge")


class WiringTest(unittest.TestCase):
    """merge-gate.yml gathers the record, threads it, and posts the refusal
    on the pull request."""

    def setUp(self):
        self.step = evaluate_step()
        self.run_block = self.step["run"]

    def test_the_record_is_gathered_from_the_compare_the_gate_already_read(self):
        gather = self.run_block.index("stacked_prs.py gather")
        invoke = self.run_block.index("scripts/merge_gate.py")
        compare = self.run_block.index("> /tmp/compare.json")
        self.assertLess(compare, gather)
        self.assertLess(gather, invoke)
        call = self.run_block[gather:self.run_block.index("\n\n", gather)]
        for part in ('--repo "$REPO_FULL"', '--pr "$PR"',
                     "--compare-file /tmp/compare.json",
                     "--out /tmp/stack.json"):
            self.assertIn(part, call)

    def test_the_record_is_handed_to_the_gate(self):
        self.assertIn("--stack-file /tmp/stack.json", self.run_block)

    def test_the_optional_output_cannot_kill_the_step(self):
        line = next(ln for ln in self.run_block.splitlines()
                    if "'^stacked_on='" in ln)
        self.assertTrue(line.rstrip().endswith("|| true"), line)

    def test_the_refusal_is_posted_once_through_gate_note(self):
        arm = self.run_block[self.run_block.index("'^stacked_on='"):
                             self.run_block.index('[ "$DECISION" = "merge" ] || exit 0')]
        self.assertIn("gate_note.py", arm)
        self.assertIn('--author "$QA_LOGIN"', arm)
        self.assertIn('--marker "$STACK_MARK"', arm)

    def test_the_login_is_still_derived_from_the_minted_token(self):
        self.assertEqual(self.step["env"]["QA_LOGIN"],
                         "${{ steps.qa.outputs.app-slug }}[bot]")


# --------------------------------------------------------------------------- #
# the scenario: the SHIPPED step, executed, against the incident             #
# --------------------------------------------------------------------------- #

GH_STUB = r'''#!/usr/bin/env python3
import json, os, re, sys

args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
fx = json.load(open(os.environ["FIXTURE"]))
threads_file = os.environ["THREADS"]
threads = json.load(open(threads_file))


def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


if args[:2] == ["pr", "view"]:
    field = (opt("--jq") or "").lstrip(".")
    value = fx["pr"][field]
    emit("true" if value is True else "false" if value is False else str(value))
if args[:2] == ["run", "list"]:
    emit([])
if args[:2] == ["pr", "merge"]:
    emit("merged")
if args[0] == "api":
    method = opt("--method") or "GET"
    path = [a for a in args[1:] if not a.startswith("-")][0]
    m = re.search(r"issues/(\d+)/comments", path)
    if method == "POST" and m:
        rows = threads[m.group(1)]
        row = {"id": 900 + len(rows), "user": {"login": os.environ["QA_LOGIN"]},
               "body": json.loads(sys.stdin.read())["body"]}
        rows.append(row)
        json.dump(threads, open(threads_file, "w"))
        emit(row)
    if method == "DELETE":
        emit({})
    if "pulls?state=open" in path:
        emit("\n".join(json.dumps(p) for p in fx["open_prs"]))
    if m:
        rows = threads[m.group(1)]
        paging = path.replace("per_page", "")
        if "page=" in paging and "page=1" not in paging:
            emit([])
        emit([rows] if "--slurp" in args else rows)
    if "check-runs" in path:
        emit({"check_runs": fx["check_runs"]})
    if "/compare/" in path:
        emit(fx["compare"])
    if "actions/runs" in path:
        emit({"workflow_runs": []})
    if "/commits" in path:
        emit([{"sha": fx["pr"]["headRefOid"]}])
    if "/pulls/" in path:
        emit("agent-bureau-bot[bot]" if opt("--jq") else {})
    emit({})
sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def run_shipped_step(threads) -> tuple:
    fixture = {
        "pr": {"headRefName": "agent/DRE-4070-capacity-ipad", "state": "OPEN",
               "mergeStateStatus": "CLEAN", "isDraft": False,
               "headRefOid": HEAD_2585, "baseRefName": "main",
               "url": f"https://github.com/{REPO}/pull/2585"},
        "check_runs": green_checks(),
        "compare": COMPARE,
        "open_prs": OPEN_ALL,
    }
    body = re.sub(r"\$\{\{[^}]*\}\}", "", evaluate_step()["run"])
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        (td / "bin").mkdir()
        stub = td / "bin" / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        os.symlink(ROOT, td / ".bureau-pipeline")
        (td / "fixture.json").write_text(json.dumps(fixture))
        (td / "threads.json").write_text(json.dumps(
            {str(n): rows for n, rows in threads.items()}))
        (td / "gh.log").write_text("")
        env = {
            **{k: v for k, v in os.environ.items() if k != "LINEAR_API_KEY"},
            "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "PR": "2585", "GH_TOKEN": "qa-token", "REPO_FULL": REPO,
            "WORKFLOW_TOKEN": "workflow-token", "QA_LOGIN": QA,
            "FIXTURE": str(td / "fixture.json"),
            "THREADS": str(td / "threads.json"),
            "GH_LOG": str(td / "gh.log"),
        }
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, our own body
            ["bash", "-e", "-c", body], cwd=td, capture_output=True,
            text=True, env=env)
        calls = [json.loads(ln)
                 for ln in (td / "gh.log").read_text().splitlines() if ln]
        final = json.loads((td / "threads.json").read_text())
    return proc, calls, final


class TheShippedStepHoldsTheIncidentTest(unittest.TestCase):
    """The seam the incident crossed: merge-gate.yml's own step, run for
    real, gathers the stack, never calls the merge command, and says why on
    #2585 — once."""

    @classmethod
    def setUpClass(cls):
        cls.proc, cls.calls, cls.threads = run_shipped_step({
            2582: [rejected(HEAD_2582)],
            2583: [rejected(HEAD_2583)],
            2585: [approved(HEAD_2585)],
        })

    def test_the_step_exits_clean(self):
        self.assertEqual(self.proc.returncode, 0,
                         f"{self.proc.stdout}\n{self.proc.stderr}")

    def test_the_merge_command_is_never_called(self):
        self.assertEqual([c for c in self.calls if c[:2] == ["pr", "merge"]],
                         [], self.proc.stdout)
        self.assertIn("decision=hold", self.proc.stdout)

    def test_the_refusal_is_on_the_pull_request(self):
        notes = [c["body"] for c in self.threads["2585"]
                 if "Merge gate: held" in c["body"]]
        self.assertEqual(len(notes), 1, self.threads["2585"])
        note = notes[0]
        self.assertTrue(
            note.startswith("⏸️ Merge gate: held for #2582, #2583 until approved"),
            note)
        self.assertEqual(note.count("REQUEST_CHANGES"), 2)
        for forbidden in ("QA Critic", "QA Verifier", "VERDICT:"):
            self.assertNotIn(forbidden, note)

    def test_nothing_is_written_on_the_rejected_pull_requests(self):
        self.assertEqual(len(self.threads["2582"]), 1)
        self.assertEqual(len(self.threads["2583"]), 1)


class TheShippedStepMergesAnApprovedStackTest(unittest.TestCase):

    def test_it_merges(self):
        proc, calls, threads = run_shipped_step({
            2582: [approved(HEAD_2582)],
            2583: [approved(HEAD_2583)],
            2585: [approved(HEAD_2585)],
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len([c for c in calls if c[:2] == ["pr", "merge"]]),
                         1, proc.stdout)
        self.assertEqual(len(threads["2585"]), 1)


if __name__ == "__main__":
    unittest.main()
