"""RED-first tests: a rescued BUILD run's delivery stands down when the card's
work is already up as an open pull request, and joins one that lacks it,
rather than opening a second (DRE-6868).

On 2026-10-11 the build run for DRE-6833 (bureau-pipeline run 38117134293) had
its push refused and its two commits saved as `rescue-DRE-6833.patch`. The work
reached GitHub twice: pull request #997 on `agent/DRE-6833-session-agents` at
00:05 PT, and 21 minutes later the rescue-delivery follow-up replayed the same
patch onto `agent/DRE-6833-rescued-delivery` and opened #998 — the same twelve
files, +1117/-114, a second review and a second test run for one build. The
only check `apply` made was for a pull request on its OWN branch.

Driven the way tests/test_deliver_rescue_fix_branch.py drives `apply`: through
`main()`, against a REAL bare remote and a stub `gh` on PATH, because what is
under test is what lands on the remote and which pull requests get opened.

Run: cd bureau-pipeline && python3 -m pytest tests/test_deliver_rescue_open_pull_request.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — git on our own throwaway repositories
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import deliver_rescue  # noqa: E402

CARD = "DRE-6833"
RUN_ID = "38117134293"
ARTIFACT = f"rescue-{CARD}.patch"
REPO = "dreadnought-foundry/bureau-pipeline"
AGENT_BRANCH = f"agent/{CARD}-session-agents"
OPEN_PR = 997
NEW_PR_URL = "https://github.com/dreadnought-foundry/bureau-pipeline/pull/998"

# The stub `gh`: logs its argv; answers `pr list` from the fixture's rows,
# narrowed to `--head` when one is asked for (as GitHub narrows it) and to
# nothing else — a state filter is the code's to apply, so a closed row reaches
# it. `fail` breaks only the card-wide listing (no `--head`), so the read on
# the rescued-delivery branch still answers. `--limit` cuts the listing as
# GitHub cuts it, and `filler` puts that many other cards' open pull requests
# ahead of the fixture's rows, so a busy repository can be laid out. `pr
# create` answers a new pull request's url.
GH_STUB = '''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")
fx = json.load(open(os.environ["GH_FIXTURE"], encoding="utf-8"))
if argv[:2] == ["pr", "list"]:
    if fx.get("fail") and "--head" not in argv:
        sys.stderr.write("HTTP 401: Bad credentials\\n")
        sys.exit(1)
    rows = [{"number": 2000 + n, "headRefName": f"agent/DRE-1{n:04d}-filler",
             "state": "OPEN", "url": ""} for n in range(fx.get("filler", 0))]
    rows += fx.get("rows", [])
    if "--head" in argv:
        head = argv[argv.index("--head") + 1]
        rows = [r for r in rows if r.get("headRefName") == head]
    if "--limit" in argv:
        rows = rows[:int(argv[argv.index("--limit") + 1])]
    print(json.dumps(rows))
elif argv[:2] == ["pr", "create"]:
    print(fx.get("created", ""))
sys.exit(0)
'''

_IDENTITY = {
    "GIT_AUTHOR_NAME": "build agent", "GIT_AUTHOR_EMAIL": "build@example.invalid",
    "GIT_COMMITTER_NAME": "build agent", "GIT_COMMITTER_EMAIL": "build@example.invalid",
}


class _Rescue:
    """A build run's rescue, laid out the way the follow-up job receives it.

    `origin.git` is the remote, holding `main`. `runner` is the build run's
    checkout: two commits on the agent's branch, written to the patch the
    refused push left behind — and no sidecar, because a build run writes
    none. `work` is the follow-up job's own fresh clone.
    """

    def __init__(self, td: Path):
        self.dir = td
        self.origin = td / "origin.git"
        self.runner = td / "runner"
        self.work = td / "work"
        self.download = td / "download"
        self.bin = td / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        self.gh_log = td / "gh.log"
        self.gh_log.write_text("")
        self.fixture = td / "fixture.json"
        self.answer([])

        self.git("init", "--bare", "-q", "-b", "main", str(self.origin), cwd=td)
        self.git("init", "-q", "-b", "main", str(self.runner), cwd=td)
        self.git("remote", "add", "origin", str(self.origin))
        (self.runner / "a.txt").write_text("base\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "base")
        self.git("push", "-q", "origin", "main")
        self.base = self.rev("HEAD")

        # The build's two commits, which never reached the remote.
        self.git("checkout", "-qb", AGENT_BRANCH)
        (self.runner / "a.txt").write_text("base\nthe session agents\n")
        self.git("commit", "-qam", f"test({CARD}): the session agents, RED")
        (self.runner / "b.txt").write_text("the implementation\n")
        self.git("add", "b.txt")
        self.git("commit", "-qm", f"feat({CARD}): the session agents")
        self.head = self.rev("HEAD")
        self.shas = self.git("rev-list", "--reverse",
                             f"{self.base}..{self.head}").split()
        self.subjects = self.git(
            "log", "--reverse", "--format=%s", f"{self.base}..{self.head}").splitlines()

        self.download.mkdir()
        nested = self.download / "artifact"
        nested.mkdir()
        self.patch = nested / ARTIFACT
        self.patch.write_text(self.git(
            "format-patch", "--stdout", f"{self.base}..{self.head}"))

        self.git("clone", "-q", str(self.origin), str(self.work), cwd=td)
        for key, value in (("user.email", "bot@example.invalid"),
                           ("user.name", "agent-bureau-bot")):
            self.git("config", key, value, cwd=self.work)

    def env(self) -> dict:
        return {**os.environ, **_IDENTITY}

    def git(self, *args, cwd=None, env=None) -> str:
        done = subprocess.run(  # nosec B603 B607 — fixed argv, our own repo
            ["git", *args], cwd=str(cwd or self.runner), env=env or self.env(),
            capture_output=True, text=True, check=True)
        return done.stdout

    def rev(self, ref: str, *, cwd=None) -> str:
        return self.git("rev-parse", ref, cwd=cwd).strip()

    def remote_branches(self) -> list[str]:
        return self.git("for-each-ref", "--format=%(refname:short)",
                        "refs/heads", cwd=self.origin).split()

    def remote_head(self, branch: str) -> str:
        return self.rev(f"refs/heads/{branch}", cwd=self.origin)

    def patch_ids(self, rev_range: str, *, cwd=None) -> list[str]:
        log = self.git("log", "--reverse", "-p", "--format=commit %H", rev_range,
                       cwd=cwd)
        done = subprocess.run(  # nosec B603 B607 — fixed argv
            ["git", "patch-id", "--stable"], input=log, cwd=str(cwd or self.runner),
            capture_output=True, text=True, check=True)
        return [line.split()[0] for line in done.stdout.splitlines()]

    def push_the_same_work_under_new_shas(self, branch: str) -> str:
        """The branch the agent's own push opened: the same two changes, the
        same patch ids, different shas — a replay rewrites the committer."""
        self.git("checkout", "-qb", f"pushed-{branch.replace('/', '-')}", self.base)
        later = {**self.env(), "GIT_COMMITTER_NAME": "someone else",
                 "GIT_COMMITTER_DATE": "2026-10-11T07:05:00Z"}
        self.git("cherry-pick", *self.shas, env=later)
        tip = self.rev("HEAD")
        self.git("push", "-q", "origin", f"{tip}:refs/heads/{branch}")
        self.git("checkout", "-q", AGENT_BRANCH)
        return tip

    def push_other_work(self, branch: str) -> str:
        """An open pull request for the card that does not carry the patch."""
        self.git("checkout", "-qb", f"other-{branch.replace('/', '-')}", self.base)
        (self.runner / "c.txt").write_text("an earlier attempt\n")
        self.git("add", "c.txt")
        self.git("commit", "-qm", f"feat({CARD}): an earlier attempt")
        tip = self.rev("HEAD")
        self.git("push", "-q", "origin", f"{tip}:refs/heads/{branch}")
        self.git("checkout", "-q", AGENT_BRANCH)
        return tip

    def push_part_of_the_work(self, branch: str) -> str:
        """An open pull request that carries the build's FIRST commit under a
        new sha, and lacks the second."""
        self.git("checkout", "-qb", f"part-{branch.replace('/', '-')}", self.base)
        later = {**self.env(), "GIT_COMMITTER_NAME": "someone else",
                 "GIT_COMMITTER_DATE": "2026-10-11T07:05:00Z"}
        self.git("cherry-pick", self.shas[0], env=later)
        tip = self.rev("HEAD")
        self.git("push", "-q", "origin", f"{tip}:refs/heads/{branch}")
        self.git("checkout", "-q", AGENT_BRANCH)
        return tip

    def push_conflicting_work(self, branch: str) -> str:
        """An open pull request whose own change to `a.txt` the build's first
        commit cannot apply over."""
        self.git("checkout", "-qb", f"clash-{branch.replace('/', '-')}", self.base)
        (self.runner / "a.txt").write_text("base\na different line\n")
        self.git("commit", "-qam", f"feat({CARD}): a clashing attempt")
        tip = self.rev("HEAD")
        self.git("push", "-q", "origin", f"{tip}:refs/heads/{branch}")
        self.git("checkout", "-q", AGENT_BRANCH)
        return tip

    def answer(self, rows, *, fail: bool = False, filler: int = 0):
        self.fixture.write_text(json.dumps(
            {"fail": fail, "rows": rows, "filler": filler,
             "created": NEW_PR_URL}))

    def gh_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def pr_creates(self) -> list[list[str]]:
        return [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]


def _row(number: int, branch: str, state: str = "OPEN") -> dict:
    return {"number": number, "headRefName": branch, "state": state,
            "url": f"https://github.com/{REPO}/pull/{number}"}


class _BuildRunCase(unittest.TestCase):
    """`apply` through `main()`, as the workflow runs it."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.r = _Rescue(self.td)
        self.posted: list[str] = []
        self.calls: list[list[str]] = []
        env = mock.patch.dict(os.environ, {
            "PATH": f"{self.r.bin}{os.pathsep}{os.environ['PATH']}",
            "GH_LOG": str(self.r.gh_log), "GH_FIXTURE": str(self.r.fixture),
            "PUSH_TOKEN": "",
        })
        env.start()
        self.addCleanup(env.stop)
        real_run = deliver_rescue._run

        def recording_run(argv, **kw):
            self.calls.append(list(argv))
            return real_run(argv, **kw)

        for target, value in (
            ("_run", recording_run),
            ("_post_comment", lambda card, body: self.posted.append(body)),
        ):
            patcher = mock.patch.object(deliver_rescue, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def apply(self) -> int:
        return deliver_rescue.main([
            "apply", "--card", CARD, "--repo", REPO, "--run-id", RUN_ID,
            "--patch-dir", str(self.r.download), "--artifact", ARTIFACT,
            "--base", "main", "--card-url", "https://linear/DRE-6833",
            "--workdir", str(self.r.work),
        ])

    def git_pushes(self) -> list[list[str]]:
        return [c for c in self.calls if c[:1] == ["git"] and "push" in c]

    def receipt(self) -> str:
        self.assertEqual(len(self.posted), 1, self.posted)
        body = self.posted[0]
        self.assertIn(deliver_rescue.DELIVERED_TAG, body.splitlines()[0])
        return body

    def assert_todays_delivery(self):
        """What `apply` did before this card: the rescued-delivery branch is
        pushed with the build's commits and one pull request is opened on it."""
        rescued = deliver_rescue.delivery_branch(CARD)
        self.assertIn(rescued, self.r.remote_branches())
        tip = self.r.remote_head(rescued)
        landed = self.r.git("log", "--reverse", "--format=%s", f"main..{tip}",
                            cwd=self.r.origin).splitlines()
        self.assertEqual(landed, self.r.subjects)
        creates = self.r.pr_creates()
        self.assertEqual(len(creates), 1, self.r.gh_calls())
        self.assertEqual(creates[0][creates[0].index("--head") + 1], rescued)
        self.assertIn(NEW_PR_URL, self.receipt())


# --------------------------------------------------------------------------
# 1. tonight's shape: the work is already up under other shas — stand down
# --------------------------------------------------------------------------
class TheWorkIsAlreadyUp(_BuildRunCase):
    def setUp(self):
        super().setUp()
        self.before = self.r.push_the_same_work_under_new_shas(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH)])

    def test_the_fixture_is_tonights_shape(self):
        """Same patch ids, different shas — so a sha comparison would miss it."""
        pushed = self.r.git("rev-list", "--reverse", f"main..{self.before}",
                            cwd=self.r.origin).split()
        self.assertEqual(len(pushed), 2)
        self.assertTrue(set(pushed).isdisjoint(self.r.shas))
        self.assertEqual(self.r.patch_ids(f"main..{self.before}", cwd=self.r.origin),
                         self.r.patch_ids(f"{self.r.base}..{self.r.head}"))

    def test_no_branch_is_pushed(self):
        self.assertEqual(self.apply(), 0, self.calls)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(sorted(self.r.remote_branches()),
                         sorted(["main", AGENT_BRANCH]))
        self.assertEqual(self.r.remote_head(AGENT_BRANCH), self.before)

    def test_no_pull_request_is_opened(self):
        self.apply()
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())

    def test_the_handoff_names_the_existing_pull_request(self):
        self.apply()
        body = self.receipt()
        self.assertEqual(len(body.strip().splitlines()), 1, body)
        self.assertIn(f"#{OPEN_PR}", body)
        self.assertIn(AGENT_BRANCH, body)
        self.assertIn("already up", body)
        self.assertIn(RUN_ID, body)

    def test_the_outcome_says_so(self):
        out = deliver_rescue.deliver(
            CARD, repo=REPO, run_id=RUN_ID, patch_dir=str(self.r.download),
            base="main", artifact=ARTIFACT, workdir=str(self.r.work),
            post=self.posted.append)
        self.assertEqual(out.outcome, deliver_rescue.OUTCOME_ALREADY_UP)
        self.assertFalse(out.pushed)
        self.assertFalse(out.pr_opened)
        self.assertEqual(out.pr_url, _row(OPEN_PR, AGENT_BRANCH)["url"])

    def test_a_repair_branch_counts_for_the_card_too(self):
        repair = f"repair/{CARD}-red-main"
        self.r.push_the_same_work_under_new_shas(repair)
        self.r.answer([_row(1001, repair)])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assertEqual(self.r.pr_creates(), [])
        self.assertIn("#1001", self.receipt())

    def test_the_listing_asks_for_open_pull_requests_of_every_author(self):
        self.apply()
        reads = [c for c in self.r.gh_calls()
                 if c[:2] == ["pr", "list"] and "--head" not in c]
        self.assertEqual(len(reads), 1, self.r.gh_calls())
        argv = reads[0]
        self.assertEqual(argv[argv.index("--repo") + 1], REPO)
        self.assertEqual(argv[argv.index("--state") + 1], "open")
        self.assertNotIn("--author", argv)
        fields = argv[argv.index("--json") + 1].split(",")
        for field in ("number", "headRefName", "state", "url"):
            self.assertIn(field, fields)


# --------------------------------------------------------------------------
# 2. an open pull request lacks the work — it lands on that branch
# --------------------------------------------------------------------------
class AnOpenPullRequestLacksTheWork(_BuildRunCase):
    def setUp(self):
        super().setUp()
        self.before = self.r.push_other_work(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH)])

    def test_the_patch_lands_on_that_pull_requests_branch(self):
        self.assertEqual(self.apply(), 0, self.calls)
        tip = self.r.remote_head(AGENT_BRANCH)
        self.assertNotEqual(tip, self.before)
        self.assertEqual(self.r.rev(f"{tip}~2", cwd=self.r.origin), self.before)
        landed = self.r.git("log", "--reverse", "--format=%s",
                            f"{self.before}..{tip}", cwd=self.r.origin).splitlines()
        self.assertEqual(landed, self.r.subjects)

    def test_no_second_pull_request_is_opened(self):
        self.apply()
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        self.assertNotIn(deliver_rescue.delivery_branch(CARD),
                         self.r.remote_branches())

    def test_the_push_is_never_forced(self):
        self.apply()
        pushes = self.git_pushes()
        self.assertEqual(len(pushes), 1, self.calls)
        for flag in ("-f", "--force", "--force-with-lease"):
            self.assertNotIn(flag, pushes[0])
        self.assertFalse(any(arg.startswith("+") for arg in pushes[0]), pushes)

    def test_one_receipt_names_the_branch_and_the_pull_request(self):
        self.apply()
        body = self.receipt()
        self.assertIn(AGENT_BRANCH, body)
        self.assertIn(f"#{OPEN_PR}", body)
        self.assertNotIn("already up", body)

    def test_a_refused_push_is_red_and_no_delivery_is_claimed(self):
        hook = self.r.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'refused by a test hook' >&2\nexit 1\n")
        hook.chmod(0o755)  # nosec B103 — a test hook in a throwaway repo
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.r.remote_head(AGENT_BRANCH), self.before)
        self.assertFalse(any(deliver_rescue.DELIVERED_TAG in b for b in self.posted))


# --------------------------------------------------------------------------
# 2b. an open pull request carries part of the work — only the rest lands
# --------------------------------------------------------------------------
class AnOpenPullRequestCarriesPartOfTheWork(_BuildRunCase):
    def setUp(self):
        super().setUp()
        self.before = self.r.push_part_of_the_work(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH)])

    def test_only_the_missing_commit_lands(self):
        self.assertEqual(self.apply(), 0, self.calls)
        tip = self.r.remote_head(AGENT_BRANCH)
        self.assertEqual(self.r.rev(f"{tip}~1", cwd=self.r.origin), self.before)
        landed = self.r.git("log", "--format=%s", f"{self.before}..{tip}",
                            cwd=self.r.origin).splitlines()
        self.assertEqual(landed, self.r.subjects[1:])
        self.assertEqual(self.r.patch_ids(f"{self.before}..{tip}", cwd=self.r.origin),
                         self.r.patch_ids(f"{self.r.shas[0]}..{self.r.head}"))

    def test_the_receipt_is_the_onto_open_pull_request_one(self):
        self.apply()
        body = self.receipt()
        self.assertIn(f"#{OPEN_PR}", body)
        self.assertNotIn("already up", body)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())

    def test_the_outcome_says_so(self):
        out = deliver_rescue.deliver(
            CARD, repo=REPO, run_id=RUN_ID, patch_dir=str(self.r.download),
            base="main", artifact=ARTIFACT, workdir=str(self.r.work),
            post=self.posted.append)
        self.assertEqual(out.outcome, deliver_rescue.OUTCOME_ONTO_OPEN_PR)
        self.assertTrue(out.pushed)


# --------------------------------------------------------------------------
# 2c. several open pull requests for the card
# --------------------------------------------------------------------------
class SeveralOpenPullRequestsForTheCard(_BuildRunCase):
    OLDER = f"agent/{CARD}-an-earlier-attempt"

    def test_when_none_carries_the_work_it_lands_on_the_newest(self):
        """`gh` lists newest first, so the first row is the one joined."""
        newest = self.r.push_other_work(AGENT_BRANCH)
        older = self.r.push_other_work(self.OLDER)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH), _row(990, self.OLDER)])
        self.assertEqual(self.apply(), 0, self.calls)
        tip = self.r.remote_head(AGENT_BRANCH)
        self.assertEqual(self.r.rev(f"{tip}~2", cwd=self.r.origin), newest)
        self.assertEqual(self.r.remote_head(self.OLDER), older)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        self.assertIn(f"#{OPEN_PR}", self.receipt())

    def test_when_the_second_carries_the_work_nothing_is_pushed(self):
        newest = self.r.push_other_work(AGENT_BRANCH)
        older = self.r.push_the_same_work_under_new_shas(self.OLDER)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH), _row(990, self.OLDER)])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(self.r.remote_head(AGENT_BRANCH), newest)
        self.assertEqual(self.r.remote_head(self.OLDER), older)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        body = self.receipt()
        self.assertIn("already up", body)
        self.assertIn("#990", body)
        self.assertIn(self.OLDER, body)

    def test_a_conflicting_replay_is_red_and_delivers_nothing(self):
        before = self.r.push_conflicting_work(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH)])
        self.assertEqual(self.apply(), 1)
        self.assertIn(["git", "-C", str(self.r.work), "cherry-pick", "--abort"],
                      self.calls)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(self.r.remote_head(AGENT_BRANCH), before)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        self.assertFalse(any(deliver_rescue.DELIVERED_TAG in b for b in self.posted))


# --------------------------------------------------------------------------
# 3. no open pull request for the card — today's delivery, unchanged
# --------------------------------------------------------------------------
class NoOpenPullRequestForTheCard(_BuildRunCase):
    def test_todays_delivery_is_unchanged(self):
        self.r.answer([])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assert_todays_delivery()

    def test_another_cards_pull_request_does_not_count(self):
        """`DRE-683` is a prefix of `DRE-6833`'s number, not the same card."""
        other = "agent/DRE-683-session-agents"
        self.r.push_the_same_work_under_new_shas(other)
        self.r.answer([_row(500, other), _row(501, "agent/DRE-68330-x")])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assert_todays_delivery()

    def test_an_unreadable_listing_delivers_nothing_and_is_red(self):
        """Could not tell is never go ahead (DRE-2034): opening a pull request
        here could be the second one for a card that already has one."""
        self.r.answer([], fail=True)
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(self.r.pr_creates(), [])
        self.assertEqual(self.r.remote_branches(), ["main"])

    def test_a_listing_cut_at_the_limit_is_not_none(self):
        """A repository with the limit's worth of open pull requests or more:
        the card's could be past the cut, so the listing cannot say "none"."""
        self.r.answer([], filler=deliver_rescue._OPEN_PR_LIMIT)
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        self.assertEqual(self.r.remote_branches(), ["main"])

    def test_the_cards_pull_request_past_the_cut_is_not_missed(self):
        self.r.push_the_same_work_under_new_shas(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH)],
                      filler=deliver_rescue._OPEN_PR_LIMIT)
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_pushes(), [], self.calls)
        self.assertEqual(self.r.pr_creates(), [], self.r.gh_calls())
        self.assertNotIn(deliver_rescue.delivery_branch(CARD),
                         self.r.remote_branches())

    def test_a_listing_one_short_of_the_limit_is_complete(self):
        self.r.answer([], filler=deliver_rescue._OPEN_PR_LIMIT - 1)
        self.assertEqual(self.apply(), 0, self.calls)
        self.assert_todays_delivery()


# --------------------------------------------------------------------------
# 4. a closed or merged pull request for the card is not "already up"
# --------------------------------------------------------------------------
class AClosedPullRequestIsNotAlreadyUp(_BuildRunCase):
    def _not_open(self, state: str):
        self.r.push_the_same_work_under_new_shas(AGENT_BRANCH)
        self.r.answer([_row(OPEN_PR, AGENT_BRANCH, state)])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assert_todays_delivery()
        self.assertNotIn("already up", self.posted[0])

    def test_merged(self):
        self._not_open("MERGED")

    def test_closed(self):
        self._not_open("CLOSED")


if __name__ == "__main__":
    unittest.main()
