"""DRE-6348: push_rescue.py delivers a NAMED existing branch, for a fix run.

A build run's rescue finds its branch by the `agent/<CARD>-*` glob, opens a
pull request when the card has none, and honors the build agent's three stop
notes. A fix run differs on four counts, and each is a test class here:

  * the branch is named by the run (`--branch`), not found by glob, and the
    commits beyond `--base` — never a note written beside them — decide
    whether there is work (DRE-4883: a blocker written after a 401 push);
  * the pull request already exists (`--existing-pr`) and is never
    duplicated, and its state is read with the FRESH credential before any
    push — MERGED, CLOSED and unreadable all refuse (DRE-4486: a push onto a
    merged pull request's deleted branch recreates it);
  * the fix loop's one sanctioned EMPTY commit (DRE-5632) survives into the
    patch (`format-patch --always`);
  * whenever the push was not made, `rescue-<CARD>.target.json` beside the
    patch says which branch the patch belongs on (DRE-6349 reads it).

Driven the way `tests/test_credential_expiry_scenario.py` drives the build
path: a REAL repository, a REAL bare remote, a `git` on PATH that refuses any
network operation unless the checkout's credential header is exactly the
fresh token, and a `gh` on PATH that answers only to the fresh token. The CLI
is the real `python3 scripts/push_rescue.py rescue …` the fix workflow will
call (DRE-6350).

Run: python3 -m pytest tests/test_push_rescue_fix_branch.py -v
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "push_rescue.py"
sys.path.insert(0, str(ROOT / "scripts"))

import push_rescue  # noqa: E402

CARD = "DRE-4911"
BRANCH = f"agent/{CARD}-x"
REPO = "o/r"
PR = "7"

STALE_TOKEN = "ghs_minted_at_job_start_dead_by_now"
FRESH_TOKEN = "ghs_minted_for_the_push"
OTHER_DEAD_TOKEN = "ghs_a_second_mint_github_also_refuses"

EXTRAHEADER = "http.https://github.com/.extraheader"

# The ten keys the build workflow already reads, in their order, and the two
# this card appends after them.
EXISTING_KEYS = ["branch", "local_work", "pushed", "pr_opened", "rescued",
                 "pr_url", "push_status", "attempts", "patch", "error"]
NEW_KEYS = ["target_branch", "sidecar"]


def _header(token: str) -> str:
    return "AUTHORIZATION: basic " + base64.b64encode(
        f"x-access-token:{token}".encode()).decode()


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o755)


# GitHub's half of the credential question, and a journal of every call, so a
# test can say "no push was attempted" from what git was actually asked.
GIT_SHIM = '''#!/usr/bin/env python3
import base64, json, os, subprocess, sys

REAL = os.environ["FIXTURE_REAL_GIT"]
BARE = os.environ["FIXTURE_BARE"]
FRESH = os.environ["FIXTURE_FRESH_TOKEN"]
HEADER = "http.https://github.com/.extraheader"
NETWORK = {"push", "fetch", "ls-remote"}

argv = sys.argv[1:]
with open(os.environ["FIXTURE_GIT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")
workdir = "."
rest = list(argv)
if rest[:1] == ["-C"]:
    workdir, rest = rest[1], rest[2:]
cmd = rest[0] if rest else ""

if cmd in NETWORK:
    got = subprocess.run(
        [REAL, "-C", workdir, "config", "--get-all", HEADER],
        capture_output=True, text=True,
    ).stdout.splitlines()
    want = "AUTHORIZATION: basic " + base64.b64encode(
        ("x-access-token:" + FRESH).encode()).decode()
    if [v.strip() for v in got] != [want]:
        sys.stderr.write(
            "remote: Invalid username or password.\\n"
            "fatal: Authentication failed for 'https://github.com/o/r/'\\n")
        sys.exit(128)
    rest = [BARE if a == "origin" else a for a in rest]

sys.exit(subprocess.run([REAL, "-C", workdir, *rest]).returncode)
'''

# `gh`: the fresh token or a 401, and `pr view` answers whatever state the
# test chose — or fails the read outright.
GH_SHIM = '''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["FIXTURE_GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "token": os.environ.get("GH_TOKEN")})
             + "\\n")

if os.environ.get("GH_TOKEN") != os.environ["FIXTURE_FRESH_TOKEN"]:
    sys.stderr.write("gh: Bad credentials (HTTP 401)\\n")
    sys.exit(1)

if argv[:2] == ["pr", "view"]:
    state = os.environ.get("FIXTURE_PR_STATE", "OPEN")
    if state == "FAIL":
        sys.stderr.write("GraphQL: something went wrong (HTTP 502)\\n")
        sys.exit(1)
    merged = "2026-10-08T01:02:03Z" if state == "MERGED" else None
    print(json.dumps({"state": state, "mergedAt": merged}))
elif argv[:2] == ["pr", "list"]:
    print("[]")
elif argv[:2] == ["pr", "create"]:
    print("https://github.com/o/r/pull/999")
sys.exit(0)
'''


class Fixture:
    """A fix run's runner: the pull request's branch already on the remote,
    checked out, with a stale credential left by checkout."""

    def __init__(self, td: str, *, pr_state: str = "OPEN"):
        self.root = Path(td)
        self.bare = self.root / "remote.git"
        self.work = self.root / "work"
        self.bin = self.root / "bin"
        self.git_log = self.root / "git.jsonl"
        self.gh_log = self.root / "gh.jsonl"
        self.real_git = shutil.which("git")
        self.pr_state = pr_state

        subprocess.run([self.real_git, "init", "--bare", "-q", str(self.bare)],
                       check=True)
        subprocess.run([self.real_git, "init", "-q", "-b", "main",
                        str(self.work)], check=True)
        self.git("config", "user.email", "bot@example.com")
        self.git("config", "user.name", "agent-bureau-bot")
        (self.work / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("remote", "add", "origin", f"https://github.com/{REPO}.git")
        self.git("push", "-q", str(self.bare), "main:refs/heads/main")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

        # The pull request's branch as the build run left it: one commit of
        # its own, already on GitHub. The fix run starts from this head.
        self.git("checkout", "-q", "-b", BRANCH)
        (self.work / "feature.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "feat(DRE-4911): the build run's work")
        self.git("push", "-q", str(self.bare), f"{BRANCH}:refs/heads/{BRANCH}")
        self.start_sha = self.sha(BRANCH)

        self.git("config", "--local", "--add", EXTRAHEADER, _header(STALE_TOKEN))

        self.bin.mkdir()
        _executable(self.bin / "git", GIT_SHIM)
        _executable(self.bin / "gh", GH_SHIM)

    def git(self, *args):
        subprocess.run([self.real_git, "-C", str(self.work), *args], check=True)

    def sha(self, ref: str) -> str:
        return subprocess.run(
            [self.real_git, "-C", str(self.work), "rev-parse", ref],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def agent_committed_a_fix(self) -> str:
        (self.work / "feature.py").write_text("value = 2\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "fix(DRE-4911): answer the review")
        return self.sha(BRANCH)

    def agent_committed_an_empty_resubmission(self) -> str:
        self.git("commit", "-q", "--allow-empty", "-m",
                 "chore(DRE-4911): resubmit for the What's new line")
        return self.sha(BRANCH)

    def env(self, **tokens) -> dict:
        env = dict(os.environ)
        for key in ("PUSH_TOKEN", "PUSH_TOKEN_RETRY", "PUSH_TOKEN_SOURCE",
                    "PUSH_TOKEN_RETRY_SOURCE"):
            env.pop(key, None)
        env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=str(self.root),
            FIXTURE_REAL_GIT=self.real_git,
            FIXTURE_BARE=str(self.bare),
            FIXTURE_FRESH_TOKEN=FRESH_TOKEN,
            FIXTURE_GIT_LOG=str(self.git_log),
            FIXTURE_GH_LOG=str(self.gh_log),
            FIXTURE_PR_STATE=self.pr_state,
        )
        env.update(tokens)
        return env

    def cli(self, *extra, **tokens):
        """The call the fix workflow will make (DRE-6350)."""
        argv = [sys.executable, str(SCRIPT), "rescue", "--card", CARD,
                "--repo", REPO, "--base", self.start_sha, *extra]
        return subprocess.run(argv, cwd=str(self.work), env=self.env(**tokens),
                              capture_output=True, text=True)

    def run_seam(self):
        """push_rescue's `run` seam, through the fixture's git and gh."""
        base_env = self.env()

        def run(argv, *, cwd=None, env=None):
            merged = dict(base_env)
            # `gh` calls hand their own environment; only its token is theirs.
            for key in ("GH_TOKEN", "GITHUB_TOKEN"):
                if env and key in env:
                    merged[key] = env[key]
            done = subprocess.run(argv, cwd=cwd or str(self.work), env=merged,
                                  capture_output=True, text=True)
            return done.returncode, done.stdout or "", done.stderr or ""
        return run

    # ── what the sandbox SHOWS ───────────────────────────────────────────────
    def remote_sha(self, ref: str = BRANCH) -> str:
        done = subprocess.run(
            [self.real_git, "-C", str(self.bare), "rev-parse", ref],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    def git_calls(self) -> list[list[str]]:
        if not self.git_log.exists():
            return []
        return [json.loads(line) for line in
                self.git_log.read_text(encoding="utf-8").splitlines() if line]

    def pushes(self) -> list[list[str]]:
        return [c for c in self.git_calls() if "push" in c]

    def gh_calls(self) -> list[dict]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines() if line]

    def pr_creates(self) -> list[dict]:
        return [c for c in self.gh_calls() if c["argv"][:2] == ["pr", "create"]]

    @property
    def sidecar(self) -> Path:
        return self.root / f"rescue-{CARD}.target.json"

    @property
    def patch(self) -> Path:
        return self.root / f"rescue-{CARD}.patch"


def outputs(proc) -> dict:
    out = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    return out


def output_keys(proc) -> list[str]:
    return [line.split("=", 1)[0] for line in proc.stdout.splitlines()
            if "=" in line]


class AnOpenPullRequestIsDeliveredWithTheFreshToken(unittest.TestCase):
    """AC 1: stale credential on the checkout, a fresh PUSH_TOKEN, `pr view 7`
    answers OPEN — the named branch reaches the remote and no second pull
    request is opened."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name, pr_state="OPEN")
        self.head = self.fx.agent_committed_a_fix()
        self.proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                                PUSH_TOKEN=FRESH_TOKEN)

    def test_the_step_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_the_named_branch_reaches_the_remote(self):
        self.assertEqual(self.fx.remote_sha(), self.head, self.proc.stderr)

    def test_no_pull_request_is_created(self):
        self.assertEqual(self.fx.pr_creates(), [], self.fx.gh_calls())

    def test_the_state_was_read_with_the_fresh_token_before_the_push(self):
        views = [c for c in self.fx.gh_calls() if c["argv"][:3] == ["pr", "view", PR]]
        self.assertTrue(views, self.fx.gh_calls())
        self.assertEqual(views[0]["token"], FRESH_TOKEN)
        self.assertIn("--repo", views[0]["argv"])
        self.assertIn(REPO, views[0]["argv"])
        self.assertIn("state,mergedAt", views[0]["argv"])

    def test_the_outputs(self):
        out = outputs(self.proc)
        self.assertEqual(out.get("pushed"), "true", out)
        self.assertEqual(out.get("local_work"), "true", out)
        self.assertEqual(out.get("pr_opened"), "false", out)
        self.assertEqual(out.get("pr_url"), "", out)
        self.assertEqual(out.get("target_branch"), BRANCH, out)
        self.assertEqual(out.get("sidecar"), "", out)

    def test_a_delivered_push_writes_no_sidecar(self):
        self.assertFalse(self.fx.sidecar.exists())

    def test_the_new_keys_follow_the_ten_existing_ones(self):
        self.assertEqual(output_keys(self.proc), EXISTING_KEYS + NEW_KEYS)


class _StateRefuses:
    """AC 2: the pull request's state refuses the push."""

    STATE = ""
    ERROR = ""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name, pr_state=self.STATE)
        self.head = self.fx.agent_committed_a_fix()
        self.proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                                PUSH_TOKEN=FRESH_TOKEN)

    def test_the_step_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_no_push_is_attempted(self):
        self.assertEqual(self.fx.pushes(), [], self.fx.git_calls())
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)

    def test_no_pull_request_is_created(self):
        self.assertEqual(self.fx.pr_creates(), [])

    def test_the_patch_carries_the_work(self):
        out = outputs(self.proc)
        self.assertEqual(out.get("patch"), str(self.fx.patch), self.proc.stderr)
        text = self.fx.patch.read_text(encoding="utf-8")
        self.assertIn("answer the review", text)
        self.assertIn("value = 2", text)
        # Patched against --base, the run's start: the build run's commit is
        # already on the pull request and is not the agent's.
        self.assertNotIn("the build run's work", text)

    def test_the_sidecar_is_written(self):
        self.assertEqual(json.loads(self.fx.sidecar.read_text(encoding="utf-8")), {
            "card": CARD, "branch": BRANCH, "head": self.head,
            "remote_head": self.fx.start_sha,
        })
        self.assertEqual(outputs(self.proc).get("sidecar"), str(self.fx.sidecar))

    def test_the_outputs_name_the_state_verbatim(self):
        out = outputs(self.proc)
        self.assertEqual(out.get("pushed"), "false", out)
        self.assertEqual(out.get("local_work"), "true", out)
        self.assertEqual(out.get("pr_opened"), "false", out)
        self.assertEqual(out.get("error"), self.ERROR, out)
        self.assertEqual(out.get("push_status"), "", out)
        self.assertEqual(out.get("target_branch"), BRANCH, out)

    def test_the_log_says_which_refusal_it_was(self):
        self.assertIn(self.ERROR, self.proc.stderr)


class AMergedPullRequestIsNeverPushedOnto(_StateRefuses, unittest.TestCase):
    STATE = "MERGED"
    ERROR = "pull request is MERGED"


class AClosedPullRequestIsNeverPushedOnto(_StateRefuses, unittest.TestCase):
    STATE = "CLOSED"
    ERROR = "pull request is CLOSED"


class AnUnreadableStateFailsClosed(_StateRefuses, unittest.TestCase):
    STATE = "FAIL"
    ERROR = "pull request state unreadable"


class TheCommitsDecideWhetherThereIsWork(unittest.TestCase):
    """AC 3: `--base` is the run's start, so nothing beyond it is nothing to
    deliver — and the one sanctioned EMPTY commit is still work."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name)

    def test_a_branch_at_its_base_is_nothing_to_deliver(self):
        proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                           PUSH_TOKEN=FRESH_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = outputs(proc)
        self.assertEqual(out.get("local_work"), "false", out)
        self.assertEqual(out.get("pushed"), "false", out)
        self.assertEqual(out.get("sidecar"), "", out)
        self.assertEqual(self.fx.pushes(), [])
        self.assertFalse(self.fx.sidecar.exists())

    def test_one_empty_commit_is_work_and_survives_into_the_patch(self):
        self.fx.agent_committed_an_empty_resubmission()
        proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                           PUSH_TOKEN=STALE_TOKEN,
                           PUSH_TOKEN_RETRY=OTHER_DEAD_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = outputs(proc)
        self.assertEqual(out.get("local_work"), "true", out)
        self.assertEqual(out.get("pushed"), "false", out)
        self.assertEqual(out.get("patch"), str(self.fx.patch), proc.stderr)
        text = self.fx.patch.read_text(encoding="utf-8")
        self.assertTrue(text.strip())
        self.assertIn("resubmit for the What's new line", text)
        # Whether a bare format-patch drops an empty commit varies by git
        # version (2.55 keeps it), so the artifact alone cannot prove the
        # flag: the contract is that the fix path asks for `--always`.
        patches = [c for c in self.fx.git_calls() if "format-patch" in c]
        self.assertTrue(patches, self.fx.git_calls())
        self.assertTrue(all("--always" in c for c in patches), patches)

    def test_one_empty_commit_is_pushed_when_the_push_is_allowed(self):
        head = self.fx.agent_committed_an_empty_resubmission()
        proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                           PUSH_TOKEN=FRESH_TOKEN)
        self.assertEqual(outputs(proc).get("pushed"), "true", proc.stderr)
        self.assertEqual(self.fx.remote_sha(), head)

    def test_a_branch_that_does_not_exist_is_nothing_to_deliver(self):
        proc = self.fx.cli("--branch", f"agent/{CARD}-gone", "--existing-pr",
                           PR, PUSH_TOKEN=FRESH_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to deliver", proc.stderr)
        out = outputs(proc)
        self.assertEqual(out.get("local_work"), "false", out)
        self.assertEqual(out.get("pushed"), "false", out)
        self.assertEqual(out.get("sidecar"), "", out)
        self.assertEqual(self.fx.pushes(), [])
        self.assertFalse(self.fx.sidecar.exists())

    def test_the_named_branch_wins_over_the_card_glob(self):
        # Another branch matching `agent/<CARD>-*` sorts first; `--branch`
        # skips the lookup, so the run's own branch is the one delivered.
        self.fx.git("branch", f"agent/{CARD}-a-decoy", "main")
        head = self.fx.agent_committed_a_fix()
        proc = self.fx.cli("--branch", BRANCH, "--existing-pr", PR,
                           PUSH_TOKEN=FRESH_TOKEN)
        self.assertEqual(outputs(proc).get("branch"), BRANCH, proc.stdout)
        self.assertEqual(self.fx.remote_sha(), head)
        self.assertEqual(self.fx.remote_sha(f"agent/{CARD}-a-decoy"), "")


class BothMintsRefusedLeaveThePatchAndTheSidecar(unittest.TestCase):
    """AC 4: the sidecar beside the patch, with exactly the contract's keys."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name)
        self.head = self.fx.agent_committed_a_fix()
        self.proc = self.fx.cli("--branch", BRANCH,
                                PUSH_TOKEN=STALE_TOKEN,
                                PUSH_TOKEN_RETRY=OTHER_DEAD_TOKEN)

    def test_both_mints_were_tried_and_refused(self):
        out = outputs(self.proc)
        self.assertEqual(out.get("pushed"), "false", out)
        self.assertEqual(out.get("attempts"), "2", out)
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)

    def test_the_patch_is_written(self):
        self.assertEqual(outputs(self.proc).get("patch"), str(self.fx.patch))
        self.assertIn("value = 2", self.fx.patch.read_text(encoding="utf-8"))

    def test_the_sidecar_sits_beside_the_patch(self):
        self.assertEqual(self.fx.sidecar.parent, self.fx.patch.parent)
        self.assertTrue(self.fx.sidecar.exists(), self.proc.stderr)
        self.assertEqual(
            push_rescue.target_sidecar_path(CARD, str(self.fx.patch)),
            str(self.fx.sidecar))

    def test_the_sidecar_carries_exactly_the_four_keys(self):
        body = json.loads(self.fx.sidecar.read_text(encoding="utf-8"))
        # Both credentials were refused, so `ls-remote` could not read the
        # remote: an unreadable remote head is "", never a guess.
        self.assertEqual(body, {"card": CARD, "branch": BRANCH,
                                "head": self.head, "remote_head": ""})

    def test_the_outputs_name_the_sidecar_and_the_target(self):
        lines = self.proc.stdout.splitlines()
        self.assertIn(f"sidecar={self.fx.sidecar}", lines)
        self.assertIn(f"target_branch={BRANCH}", lines)
        self.assertEqual(output_keys(self.proc), EXISTING_KEYS + NEW_KEYS)


class NoCredentialAtAllStillLeavesTheSidecar(unittest.TestCase):
    def test_no_mint_writes_the_patch_and_the_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            head = fx.agent_committed_a_fix()
            proc = fx.cli("--branch", BRANCH, "--existing-pr", PR)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = outputs(proc)
            self.assertEqual(out.get("pushed"), "false", out)
            self.assertEqual(out.get("sidecar"), str(fx.sidecar), out)
            self.assertEqual(json.loads(fx.sidecar.read_text(encoding="utf-8")),
                             {"card": CARD, "branch": BRANCH, "head": head,
                              "remote_head": ""})
            self.assertTrue(fx.patch.exists())
            self.assertEqual(fx.pushes(), [])


class AStopNoteDoesNotOutrankACommittedFix(unittest.TestCase):
    """AC 5: on the fix path the commits decide (DRE-4883). The note is handed
    in through `stop_notes`, never written to the real /tmp."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name)
        self.head = self.fx.agent_committed_a_fix()
        self.note = os.path.join(self.td.name, "agent-blocker.txt")
        with open(self.note, "w", encoding="utf-8") as fh:
            fh.write("the push answered 401, so I am blocked\n")

    def _rescue(self, **kw):
        return push_rescue.rescue(
            CARD, REPO, FRESH_TOKEN, base=self.fx.start_sha,
            workdir=str(self.fx.work), run=self.fx.run_seam(),
            log=lambda *_: None, stop_notes=(self.note,), **kw,
        )

    def test_with_branch_the_note_is_not_consulted(self):
        out = self._rescue(branch=BRANCH, existing_pr=PR)
        self.assertTrue(out.pushed)
        self.assertEqual(self.fx.remote_sha(), self.head)

    def test_without_branch_the_same_note_still_stops_the_push(self):
        out = self._rescue()
        self.assertFalse(out.pushed)
        self.assertFalse(out.local_work)
        self.assertEqual(self.fx.pushes(), [])
        self.assertEqual(self.fx.remote_sha(), self.fx.start_sha)


class TheBuildPathIsUnchanged(unittest.TestCase):
    """AC 6, at the edges this card touches: no flag, no new file, the two new
    keys present but empty, and format-patch without `--always`."""

    def test_no_sidecar_and_empty_new_keys_without_branch(self):
        with tempfile.TemporaryDirectory() as td:
            fx = Fixture(td)
            fx.agent_committed_a_fix()
            proc = fx.cli(PUSH_TOKEN=STALE_TOKEN)
            out = outputs(proc)
            self.assertEqual(out.get("pushed"), "false", out)
            self.assertEqual(out.get("target_branch"), "", out)
            self.assertEqual(out.get("sidecar"), "", out)
            self.assertFalse(fx.sidecar.exists())
            self.assertTrue(fx.patch.exists())
            patches = [c for c in fx.git_calls() if "format-patch" in c]
            self.assertTrue(patches)
            self.assertTrue(all("--always" not in c for c in patches), patches)

    def test_the_target_sidecar_path_defaults_beside_the_default_patch(self):
        with tempfile.TemporaryDirectory() as td:
            saved = os.environ.get("RUNNER_TEMP")
            os.environ["RUNNER_TEMP"] = td
            try:
                self.assertEqual(
                    push_rescue.target_sidecar_path(CARD, ""),
                    os.path.join(td, f"rescue-{CARD}.target.json"))
            finally:
                if saved is None:
                    os.environ.pop("RUNNER_TEMP", None)
                else:
                    os.environ["RUNNER_TEMP"] = saved


if __name__ == "__main__":
    unittest.main()
