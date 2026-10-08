"""RED-first tests: a rescued FIX run is replayed onto its own pull request's
branch, never onto a fresh one (DRE-6349).

`deliver_rescue.py apply` knew one shape of work — a build run's — so it always
branched `agent/<CARD>-rescued-delivery` off the default branch and opened a new
pull request. A fix run's commits belong on the pull request it was fixing, and
a second pull request for one card is worse than the push it lost. The rescue
now leaves a sidecar beside the patch (`rescue-<CARD>.target.json`, written by
`push_rescue.py` under DRE-6348) naming the branch and its two heads, and
`apply` delivers onto that branch — after asking the pull request's state and
the branch's head, because thirty minutes is long enough for both to change.

Driven the way tests/test_push_rescue_delivery.py drives `apply`, but against a
REAL bare remote and a stub `gh` on PATH: the outcome under test is what lands
on the remote, which an injected `run` cannot show.

Run: cd bureau-pipeline && python3 -m pytest tests/test_deliver_rescue_fix_branch.py -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess  # nosec B404 — git on our own throwaway repositories
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")

import deliver_rescue  # noqa: E402
import step_shell  # noqa: E402

CARD = "DRE-4911"
BRANCH = "agent/DRE-4911-x"
RUN_ID = "34500000001"
ARTIFACT = f"rescue-{CARD}.patch"
REPO = "dreadnought-foundry/agent-bureau"
PR = 77

WORKFLOWS = ROOT / ".github" / "workflows"
DELIVER = WORKFLOWS / "deliver-rescue.yml"
DELIVER_STUB = WORKFLOWS / "self-deliver-rescue.yml"

# The stub `gh`: logs its argv, answers `pr list` from a fixture file, and
# exits non-zero when the fixture says the read fails. Anything else it is
# asked — above all `pr create` — is logged and answered with nothing.
GH_STUB = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
fx = json.load(open(os.environ["GH_FIXTURE"], encoding="utf-8"))
if sys.argv[1:3] == ["pr", "list"]:
    if fx.get("fail"):
        sys.stderr.write("HTTP 401: Bad credentials\\n")
        sys.exit(1)
    print(json.dumps(fx.get("rows", [])))
sys.exit(0)
'''

_IDENTITY = {
    "GIT_AUTHOR_NAME": "fix agent", "GIT_AUTHOR_EMAIL": "fix@example.invalid",
    "GIT_COMMITTER_NAME": "fix agent", "GIT_COMMITTER_EMAIL": "fix@example.invalid",
}


class _Rescue:
    """A fix run's rescue, laid out the way the follow-up job receives it.

    `origin.git` is the remote. `runner` is the fix run's checkout: it starts at
    the pull request's head (`remote_head`), commits the fix (`head`) and writes
    the patch and the sidecar the refused push left behind. `work` is the
    follow-up job's own fresh checkout of the default branch.
    """

    def __init__(self, td: Path, *, empty_commit: bool = False):
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

        self.git("init", "--bare", "-q", "-b", "main", str(self.origin), cwd=td)
        self.git("init", "-q", "-b", "main", str(self.runner), cwd=td)
        self.git("remote", "add", "origin", str(self.origin))
        (self.runner / "a.txt").write_text("base\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "base")
        self.git("push", "-q", "origin", "main")
        # The pull request's branch as the fix run found it.
        self.git("checkout", "-qb", BRANCH)
        (self.runner / "a.txt").write_text("base\nthe build's work\n")
        self.git("commit", "-qam", f"feat({CARD}): the build's work")
        self.git("push", "-q", "origin", BRANCH)
        self.remote_head = self.rev("HEAD")
        # The fix run's own work, which never reached the remote.
        if empty_commit:
            self.git("commit", "-q", "--allow-empty", "-m",
                     f"fix({CARD}): answer the held What's new line")
        else:
            (self.runner / "b.txt").write_text("the fix\n")
            self.git("add", "b.txt")
            self.git("commit", "-qm", f"fix({CARD}): the reviewer's finding")
        self.head = self.rev("HEAD")
        self.subjects = self.git(
            "log", "--format=%s", f"{self.remote_head}..{self.head}").splitlines()

        self.download.mkdir()
        patch = self.git("format-patch", "--always", "--stdout",
                         f"{self.remote_head}..{self.head}")
        # `gh run download` unpacks into a subdirectory; the sidecar is found
        # anywhere under the download, the same glob discipline as the patch.
        nested = self.download / "artifact"
        nested.mkdir()
        self.patch = nested / ARTIFACT
        self.patch.write_text(patch)
        self.sidecar = nested / f"rescue-{CARD}.target.json"
        self.write_sidecar()

        self.git("clone", "-q", str(self.origin), str(self.work), cwd=td)
        for key, value in (("user.email", "bot@example.invalid"),
                           ("user.name", "agent-bureau-bot")):
            self.git("config", key, value, cwd=self.work)

    def env(self) -> dict:
        return {**os.environ, **_IDENTITY}

    def git(self, *args, cwd=None) -> str:
        done = subprocess.run(  # nosec B603 B607 — fixed argv, our own repo
            ["git", *args], cwd=str(cwd or self.runner), env=self.env(),
            capture_output=True, text=True, check=True)
        return done.stdout

    def rev(self, ref: str, *, cwd=None) -> str:
        return self.git("rev-parse", ref, cwd=cwd).strip()

    def remote_branch(self) -> str:
        return self.git("rev-parse", f"refs/heads/{BRANCH}", cwd=self.origin).strip()

    def remote_branches(self) -> list[str]:
        return self.git("for-each-ref", "--format=%(refname:short)",
                        "refs/heads", cwd=self.origin).split()

    def write_sidecar(self, **override):
        doc = {"card": CARD, "branch": BRANCH, "head": self.head,
               "remote_head": self.remote_head, **override}
        self.sidecar.write_text(json.dumps(doc))

    def answer(self, state: str = "OPEN", *, fail: bool = False, rows=None):
        if rows is None:
            rows = [{"number": PR, "state": state, "headRefOid": self.remote_head}]
        self.fixture.write_text(json.dumps({"fail": fail, "rows": rows}))

    def gh_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def move_remote_branch_to(self, sha: str):
        self.git("push", "-q", "--force", "origin", f"{sha}:refs/heads/{BRANCH}")

    def refuse_pushes(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'refused by a test hook' >&2\nexit 1\n")
        hook.chmod(0o755)  # nosec B103 — a test hook in a throwaway repo


class _FixRunCase(unittest.TestCase):
    """`apply` through `main()`, as the workflow runs it, with the card write and
    the git/gh seam recorded."""

    empty_commit = False

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.r = _Rescue(self.td, empty_commit=self.empty_commit)
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
            "--base", "main", "--card-url", "https://linear/DRE-4911",
            "--workdir", str(self.r.work),
        ])

    def git_commands(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if c[:1] == ["git"] and name in c]

    def fetched_the_branch(self) -> bool:
        return any(BRANCH in " ".join(c) for c in self.git_commands("fetch"))

    def assert_nothing_replayed(self, before: str):
        self.assertEqual(self.git_commands("am"), [], self.calls)
        self.assertEqual(self.git_commands("apply"), [], self.calls)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertEqual(self.r.remote_branch(), before)
        self.assertNotIn(deliver_rescue.delivery_branch(CARD), self.r.remote_branches())
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in self.r.gh_calls()))

    def receipt(self) -> str:
        self.assertEqual(len(self.posted), 1, self.posted)
        body = self.posted[0]
        self.assertIn(deliver_rescue.DELIVERED_TAG, body.splitlines()[0])
        return body


# --------------------------------------------------------------------------
# 4. the branch is where the run left it — the replay lands on it
# --------------------------------------------------------------------------
class TheBranchIsWhereTheRunLeftIt(_FixRunCase):
    def test_the_commits_land_on_the_pull_requests_own_branch(self):
        self.r.answer("OPEN")
        code = self.apply()
        self.assertEqual(code, 0, self.calls)
        tip = self.r.remote_branch()
        self.assertNotEqual(tip, self.r.remote_head)
        landed = self.r.git("log", "--format=%s", f"{self.r.remote_head}..{tip}",
                            cwd=self.r.origin).splitlines()
        self.assertEqual(landed, self.r.subjects)

    def test_it_opens_no_pull_request_and_no_rescued_delivery_branch(self):
        self.r.answer("OPEN")
        self.apply()
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in self.r.gh_calls()),
                         self.r.gh_calls())
        self.assertNotIn(deliver_rescue.delivery_branch(CARD), self.r.remote_branches())

    def test_the_push_is_never_forced(self):
        self.r.answer("OPEN")
        self.apply()
        pushes = self.git_commands("push")
        self.assertEqual(len(pushes), 1, self.calls)
        for flag in ("-f", "--force", "--force-with-lease"):
            self.assertNotIn(flag, pushes[0])
        self.assertFalse(any(arg.startswith("+") for arg in pushes[0]), pushes)

    def test_the_replay_is_git_am_three_way_keeping_empty_commits(self):
        self.r.answer("OPEN")
        self.apply()
        am = self.git_commands("am")
        self.assertTrue(am, self.calls)
        self.assertIn("-3", am[0])
        self.assertIn("--empty=keep", am[0])

    def test_one_receipt_names_the_branch_and_the_pull_request(self):
        self.r.answer("OPEN")
        self.apply()
        body = self.receipt()
        self.assertIn(BRANCH, body)
        self.assertIn(f"#{PR}", body)
        self.assertIn(RUN_ID, body)

    def test_the_pull_request_state_is_asked_with_the_jobs_own_read(self):
        self.r.answer("OPEN")
        self.apply()
        reads = [c for c in self.r.gh_calls() if c[:2] == ["pr", "list"]]
        self.assertEqual(len(reads), 1, self.r.gh_calls())
        argv = reads[0]
        self.assertEqual(argv[argv.index("--head") + 1], BRANCH)
        self.assertEqual(argv[argv.index("--state") + 1], "all")
        self.assertEqual(argv[argv.index("--repo") + 1], REPO)
        fields = argv[argv.index("--json") + 1].split(",")
        for field in ("number", "state", "headRefOid"):
            self.assertIn(field, fields)

    def test_a_refused_push_is_red_and_reported_in_the_jobs_words(self):
        self.r.answer("OPEN")
        self.r.refuse_pushes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out.getvalue())
        self.assertEqual(self.r.remote_branch(), self.r.remote_head)
        self.assertFalse(any(deliver_rescue.DELIVERED_TAG in b for b in self.posted),
                         "a push that did not land is not a delivery")

    def test_a_patch_that_will_not_replay_is_red_and_pushes_nothing(self):
        self.r.answer("OPEN")
        self.r.patch.write_text("this is not a patch\n")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertEqual(self.r.remote_branch(), self.r.remote_head)


class AReplayThatAddsNothingIsNotADelivery(_FixRunCase):
    def test_no_new_commit_means_no_push_and_a_red_job(self):
        """`git am` handed a directory reads it as an empty Maildir and exits
        0 having applied nothing. Pushing then answers "Everything up-to-date"
        — and a receipt saying the work was replayed would be a lie."""
        self.r.patch.unlink()
        self.r.patch.mkdir()
        self.r.answer("OPEN")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertFalse(any(deliver_rescue.DELIVERED_TAG in b for b in self.posted))


class AnEmptyCommitReplays(_FixRunCase):
    """The fix loop's one sanctioned empty commit (DRE-5632) — the answer to a
    held `What's new:` line — must survive the replay rather than be dropped."""

    empty_commit = True

    def test_the_empty_commit_is_the_remotes_new_head(self):
        self.r.answer("OPEN")
        self.assertEqual(self.apply(), 0, self.calls)
        tip = self.r.remote_branch()
        self.assertEqual(self.r.rev(f"{tip}^", cwd=self.r.origin), self.r.remote_head)
        self.assertEqual(
            self.r.git("log", "-1", "--format=%s", tip, cwd=self.r.origin).strip(),
            self.r.subjects[0],
        )
        self.assertEqual(self.r.rev(f"{tip}^{{tree}}", cwd=self.r.origin),
                         self.r.rev(f"{self.r.remote_head}^{{tree}}", cwd=self.r.origin))


# --------------------------------------------------------------------------
# 1. the pull request is merged or closed — nothing is replayed
# --------------------------------------------------------------------------
class ThePullRequestIsNoLongerOpen(_FixRunCase):
    def _closed(self, state: str):
        self.r.answer(state)
        before = self.r.remote_branch()
        code = self.apply()
        self.assertEqual(code, 0, self.calls)
        self.assertFalse(self.fetched_the_branch(), self.calls)
        self.assert_nothing_replayed(before)
        body = self.receipt()
        self.assertIn("not delivered", body)
        self.assertIn(state.lower(), body.lower())
        self.assertIn(RUN_ID, body)
        self.assertIn(ARTIFACT, body)

    def test_merged(self):
        self._closed("MERGED")

    def test_closed(self):
        self._closed("CLOSED")

    def test_an_unreadable_state_is_not_an_answer(self):
        self.r.answer(fail=True)
        before = self.r.remote_branch()
        self.assertEqual(self.apply(), 1)
        self.assert_nothing_replayed(before)
        self.assertFalse(any(deliver_rescue.DELIVERED_TAG in b for b in self.posted),
                         "an unread state must leave the delivery pending")

    def test_no_pull_request_on_the_branch_is_not_an_answer_either(self):
        """A fix run's branch has a pull request by definition. Finding none is
        a read that did not say what it should have — never a licence to push
        commits nothing will review."""
        self.r.answer(rows=[])
        before = self.r.remote_branch()
        self.assertEqual(self.apply(), 1)
        self.assert_nothing_replayed(before)

    def test_an_open_pull_request_wins_over_an_older_closed_one(self):
        """`--state all` lists every pull request the branch ever had; the
        branch is live while any of them is open."""
        self.r.answer(rows=[
            {"number": PR, "state": "OPEN", "headRefOid": self.r.remote_head},
            {"number": 12, "state": "CLOSED", "headRefOid": self.r.remote_head},
        ])
        self.assertEqual(self.apply(), 0, self.calls)
        self.assertNotEqual(self.r.remote_branch(), self.r.remote_head)


# --------------------------------------------------------------------------
# 2. the branch already holds the work
# --------------------------------------------------------------------------
class TheBranchAlreadyHoldsTheWork(_FixRunCase):
    def test_nothing_is_applied_and_the_receipt_says_so(self):
        self.r.answer("OPEN")
        self.r.move_remote_branch_to(self.r.head)
        code = self.apply()
        self.assertEqual(code, 0, self.calls)
        self.assert_nothing_replayed(self.r.head)
        body = self.receipt()
        self.assertIn("already there", body)
        self.assertIn(BRANCH, body)


# --------------------------------------------------------------------------
# 3. the branch moved on without the work
# --------------------------------------------------------------------------
class TheBranchMovedOn(_FixRunCase):
    def test_the_replay_is_skipped_and_both_heads_are_named(self):
        self.r.answer("OPEN")
        self.r.git("checkout", "-q", self.r.remote_head)
        self.r.git("commit", "-q", "--allow-empty", "-m", "a later hand's push")
        third = self.r.rev("HEAD")
        self.r.move_remote_branch_to(third)
        code = self.apply()
        self.assertEqual(code, 0, self.calls)
        self.assert_nothing_replayed(third)
        body = self.receipt()
        self.assertIn("moved", body)
        self.assertIn("skipped", body)
        self.assertIn(self.r.remote_head, body)
        self.assertIn(self.r.head, body)
        self.assertIn(third, body)


# --------------------------------------------------------------------------
# the sidecar itself
# --------------------------------------------------------------------------
class TheSidecar(_FixRunCase):
    def test_it_is_read_off_the_download(self):
        target = deliver_rescue.read_target(str(self.r.download), CARD)
        self.assertEqual(target.branch, BRANCH)
        self.assertEqual(target.head, self.r.head)
        self.assertEqual(target.remote_head, self.r.remote_head)

    def test_no_sidecar_is_none(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(deliver_rescue.read_target(empty, CARD))

    def test_a_sidecar_for_another_card_delivers_nothing(self):
        self.r.write_sidecar(card="DRE-1")
        self.r.answer("OPEN")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertEqual(self.r.remote_branch(), self.r.remote_head)
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in self.r.gh_calls()))

    def test_a_branch_that_could_read_as_an_option_delivers_nothing(self):
        self.r.write_sidecar(branch="--upload-pack=x")
        with self.assertRaises(ValueError):
            deliver_rescue.read_target(str(self.r.download), CARD)
        self.r.answer("OPEN")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.r.gh_calls(), [])

    def test_an_unknown_pull_request_state_is_not_an_answer(self):
        self.r.answer("DRAFTISH")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertEqual(self.r.remote_branch(), self.r.remote_head)

    def test_a_sidecar_missing_a_key_delivers_nothing(self):
        doc = json.loads(self.r.sidecar.read_text())
        del doc["remote_head"]
        self.r.sidecar.write_text(json.dumps(doc))
        self.r.answer("OPEN")
        self.assertEqual(self.apply(), 1)
        self.assertEqual(self.git_commands("push"), [], self.calls)
        self.assertFalse(any(c[:2] == ["pr", "create"] for c in self.r.gh_calls()))


# --------------------------------------------------------------------------
# the hand-off names the branch, and says what will really happen
# --------------------------------------------------------------------------
class TheHandoffNamesTheBranch(unittest.TestCase):
    def setUp(self):
        self.posted: list[str] = []

    def handoff(self, code=0):
        return deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT, status="400",
            mints=2, branch=BRANCH, run=lambda argv, **kw: (code, "", "HTTP 404"),
            post=self.posted.append,
        )

    def marker_line(self) -> str:
        lines = [l for l in self.posted[0].splitlines()
                 if deliver_rescue.FAILED_TAG in l]
        self.assertEqual(len(lines), 1, self.posted)
        return lines[0]

    def test_the_marker_line_ends_with_the_branch(self):
        self.handoff()
        self.assertTrue(
            self.marker_line().endswith(f"on run {RUN_ID} for branch {BRANCH}"),
            self.marker_line(),
        )

    def test_the_parser_reads_the_branch_back(self):
        self.handoff()
        marker = deliver_rescue.parse_marker(self.posted[0])
        self.assertEqual(marker.branch, BRANCH)
        self.assertEqual(marker.run_id, RUN_ID)
        self.assertEqual(marker.artifact, ARTIFACT)
        self.assertEqual(deliver_rescue.pending_delivery(self.posted).branch, BRANCH)

    def test_the_body_promises_no_fresh_branch_and_no_new_pull_request(self):
        for code in (0, 1):
            self.posted.clear()
            self.handoff(code)
            body = self.posted[0]
            self.assertNotIn("fresh branch", body)
            self.assertNotIn("opens the pull request", body)
            self.assertIn(BRANCH, body.split(self.marker_line(), 1)[1])

    def test_the_announcement_takes_the_branch(self):
        line = deliver_rescue.announcement(
            CARD, artifact=ARTIFACT, run_id=RUN_ID, status="400", branch=BRANCH)
        self.assertTrue(line.endswith(f"on run {RUN_ID} for branch {BRANCH}"), line)

    def test_a_marker_written_before_this_card_still_parses(self):
        old = ("🚨 rescue-push-failed: status 400 on both mints — the work is in "
               "artifact rescue-DRE-3165.patch on run 34045232203; nothing will "
               "open a PR until it is delivered")
        marker = deliver_rescue.parse_marker(old)
        self.assertEqual(marker.run_id, "34045232203")
        self.assertEqual(marker.artifact, "rescue-DRE-3165.patch")
        self.assertEqual(marker.branch, "")

    def test_without_a_branch_the_build_run_wording_is_unchanged(self):
        deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT, status="400",
            run=lambda argv, **kw: (0, "", ""), post=self.posted.append)
        self.assertNotIn("for branch", self.posted[0])
        self.assertIn("fresh branch", self.posted[0])

    def test_the_cli_takes_the_flag(self):
        with mock.patch.object(deliver_rescue, "_run",
                               lambda argv, **kw: (0, "", "")), \
                mock.patch.object(deliver_rescue, "_post_comment",
                                  lambda card, body: self.posted.append(body)):
            code = deliver_rescue.main([
                "handoff", "--card", CARD, "--repo", REPO, "--run-id", RUN_ID,
                "--artifact", ARTIFACT, "--status", "400", "--branch", BRANCH,
            ])
        self.assertEqual(code, 0)
        self.assertTrue(
            self.marker_line().endswith(f"on run {RUN_ID} for branch {BRANCH}"))

    def test_the_dispatch_is_unchanged_by_the_branch(self):
        """The branch travels in the artifact, never in a workflow input."""
        calls: list[list[str]] = []
        deliver_rescue.handoff(
            CARD, repo=REPO, run_id=RUN_ID, artifact=ARTIFACT, branch=BRANCH,
            run=lambda argv, **kw: (calls.append(list(argv)) or (0, "", "")),
            post=self.posted.append)
        self.assertEqual(calls, [deliver_rescue.dispatch_argv(
            REPO, run_id=RUN_ID, card=CARD, artifact=ARTIFACT)])
        self.assertNotIn(BRANCH, " ".join(calls[0]))


# --------------------------------------------------------------------------
# no delivery stub changes anywhere in the fleet
# --------------------------------------------------------------------------
def _inputs(path: Path, trigger: str) -> list[str]:
    doc = yaml.safe_load(step_shell.workflow_source(path))
    on = doc.get("on", doc.get(True))
    return sorted(((on or {}).get(trigger) or {}).get("inputs") or {})


class TheDeliveryInputsAreUnchanged(unittest.TestCase):
    """Pinned to what `main` carried when this card was written: a new input
    would be a stub change in every product repo, and the branch needs none
    because it rides in the artifact's sidecar."""

    def test_the_self_host_stub(self):
        self.assertEqual(_inputs(DELIVER_STUB, "workflow_dispatch"),
                         ["artifact", "card", "card_url", "run_id"])

    def test_the_reusable_workflow(self):
        self.assertEqual(_inputs(DELIVER, "workflow_call"),
                         ["artifact", "card", "card_url", "pipeline_ref", "run_id"])


if __name__ == "__main__":
    unittest.main()
