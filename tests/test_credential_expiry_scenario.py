"""AC 1: a run whose start token is invalidated still pushes and opens its PR.

DRE-3043's acceptance criterion is a FIXTURE RUN, not a unit assertion: "a
fixture run whose start token is invalidated after 60 minutes still pushes its
branch and opens its PR". Unit-green is not live-working — the mechanism
crosses a shell step in `agent-task.yml`, a git credential written into
`.git/config` by `actions/checkout`, a real `git push` and a real `gh pr
create`. So this drives the REAL "Push rescue" step's `run:` block, read out of
the workflow file, against:

  * a REAL git repository with a REAL bare remote, cloned and committed on;
  * a `git` on PATH that behaves exactly like GitHub's server for the credential
    question — it reads `http.https://github.com/.extraheader` out of the
    repository's own config, and REFUSES every network operation
    (`push`/`fetch`/`ls-remote`) unless that header carries EXACTLY ONE value
    and that value is the freshly minted token. A stale header, an absent one,
    or a fresh one appended beside the stale one all answer
    `fatal: Authentication failed`, which is what run 33822932627 saw at 18:53
    PT. Everything else is delegated to the real git binary;
  * a `gh` on PATH that answers `HTTP 401: Bad credentials` to any token but
    the fresh one, and records `pr create`.

The negative control is the load-bearing half: the SAME fixture, run with the
job-start token instead of a fresh mint, must fail to push and must say so.
Without it a green result here would prove only that the fixture is generous.

Follows tests/test_platform_fault_scenario.py, which drives the Report step the
same way.

Run: python3 -m pytest tests/test_credential_expiry_scenario.py -v
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "agent-task.yml"

CARD = "DRE-3043"
BRANCH = f"agent/{CARD}-push-before-token-expiry"
REPO = "dreadnought-foundry/bureau-pipeline"
CARD_URL = "https://linear.app/dreadnoughtfoundry/issue/DRE-3043/x"
CARD_TITLE = "a build run longer than an hour cannot push"

# The two credentials the incident is about: the one minted at the top of the
# job (dead at the 60-minute mark) and the one the rescue step mints for the
# push itself.
STALE_TOKEN = "ghs_minted_at_job_start_dead_at_sixty_minutes"
FRESH_TOKEN = "ghs_minted_for_the_push"

EXTRAHEADER = "http.https://github.com/.extraheader"


def rescue_step() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in doc["jobs"]["execute"]["steps"]:
        if step.get("name") == "Push rescue":
            return step
    raise AssertionError("agent-task.yml has no 'Push rescue' step")


def substitute(run: str, values: dict) -> str:
    """Apply the `${{ }}` substitutions Actions would make. An expression the
    fixture has no value for is a hole in the harness, not a pass."""

    def repl(m):
        key = m.group(1).strip()
        if key not in values:
            raise AssertionError(f"fixture has no value for ${{{{ {key} }}}}")
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, run)
    assert "${{" not in out
    return out


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o755)


# The `git` GitHub would be. Network subcommands are gated on the repository's
# own credential header; everything else is the real binary, so the commits,
# refs and shas in this fixture are genuine.
GIT_SHIM = '''#!/usr/bin/env python3
import base64, os, subprocess, sys

REAL = os.environ["FIXTURE_REAL_GIT"]
BARE = os.environ["FIXTURE_BARE"]
FRESH = os.environ["FIXTURE_FRESH_TOKEN"]
HEADER = "http.https://github.com/.extraheader"
NETWORK = {"push", "fetch", "ls-remote"}

argv = sys.argv[1:]
# `git -C <dir> <cmd> …` — the form push_rescue.py uses.
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
    # EXACTLY ONE value: http.extraheader is multi-valued and git sends every
    # value it finds, so a fresh header appended beside the dead one is two
    # Authorization headers — GitHub refuses the pair.
    if [v.strip() for v in got] != [want]:
        sys.stderr.write(
            "remote: Invalid username or password.\\n"
            "fatal: Authentication failed for "
            "'https://github.com/dreadnought-foundry/bureau-pipeline/'\\n")
        sys.exit(128)
    rest = [BARE if a == "origin" else a for a in rest]

sys.exit(subprocess.run([REAL, "-C", workdir, *rest]).returncode)
'''

# `gh`, with the same credential question asked of GH_TOKEN.
GH_SHIM = '''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
journal = os.environ["FIXTURE_GH_LOG"]
with open(journal, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\\n")

if os.environ.get("GH_TOKEN") != os.environ["FIXTURE_FRESH_TOKEN"]:
    sys.stderr.write("gh: Bad credentials (HTTP 401)\\n")
    sys.exit(1)

if argv[:2] == ["pr", "list"]:
    print(json.dumps(json.loads(os.environ.get("FIXTURE_OPEN_PRS") or "[]")))
elif argv[:2] == ["pr", "create"]:
    print("https://github.com/dreadnought-foundry/bureau-pipeline/pull/244")
sys.exit(0)
'''


class Fixture:
    """A sandbox that is a real repository with a real remote."""

    def __init__(self, td: str, *, token: str, open_prs=()):
        self.root = Path(td)
        self.token = token
        self.bare = self.root / "remote.git"
        self.work = self.root / "work"
        self.bin = self.root / "bin"
        self.gh_log = self.root / "gh.jsonl"
        self.output = self.root / "github_output"
        self.real_git = shutil.which("git")
        self.open_prs = list(open_prs)

        subprocess.run([self.real_git, "init", "--bare", "-q", str(self.bare)],
                       check=True)
        subprocess.run([self.real_git, "init", "-q", "-b", "main", str(self.work)],
                       check=True)
        self._git("config", "user.email", "bot@example.com")
        self._git("config", "user.name", "agent-bureau-bot")
        (self.work / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")
        self._git("remote", "add", "origin", "https://github.com/%s.git" % REPO)
        # Seed the remote's default branch directly — the base the rescue
        # counts the branch's own commits against.
        self._git("push", "-q", str(self.bare), "main:refs/heads/main")
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")

        # The credential actions/checkout leaves behind: the job-start token.
        self._git("config", "--local", EXTRAHEADER, _header(STALE_TOKEN))

        self.bin.mkdir()
        _executable(self.bin / "git", GIT_SHIM)
        _executable(self.bin / "gh", GH_SHIM)
        self.output.write_text("", encoding="utf-8")

        # The pipeline checkout the workflow step calls into.
        shutil.copytree(ROOT / "scripts", self.root / ".bureau-pipeline" / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))

    def _git(self, *args):
        subprocess.run([self.real_git, "-C", str(self.work), *args], check=True)

    def agent_finished_its_work(self):
        """The state the DRE-3029 agent was in at 18:53 PT: the whole card
        built and committed on its branch, and not one byte of it on GitHub."""
        self._git("checkout", "-q", "-b", BRANCH)
        (self.work / "feature.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "feat(DRE-3043): the work that never left the runner")
        return subprocess.run(
            [self.real_git, "-C", str(self.work), "rev-parse", BRANCH],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def run_the_rescue_step(self):
        run = substitute(rescue_step()["run"], {})
        script = self.root / "rescue.sh"
        script.write_text("set -eo pipefail\n" + run, encoding="utf-8")
        env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            GITHUB_OUTPUT=str(self.output),
            PUSH_TOKEN=self.token,
            CARD=CARD,
            CARD_URL=CARD_URL,
            CARD_TITLE=CARD_TITLE,
            REPO=REPO,
            BASE="main",
            FIXTURE_REAL_GIT=self.real_git,
            FIXTURE_BARE=str(self.bare),
            FIXTURE_FRESH_TOKEN=FRESH_TOKEN,
            FIXTURE_GH_LOG=str(self.gh_log),
            FIXTURE_OPEN_PRS=json.dumps(self.open_prs),
        )
        return subprocess.run(["bash", str(script)], cwd=str(self.work), env=env,
                              capture_output=True, text=True)

    # ── what the sandbox SHOWS ───────────────────────────────────────────────
    def remote_sha(self, ref: str = BRANCH) -> str:
        done = subprocess.run(
            [self.real_git, "-C", str(self.bare), "rev-parse", ref],
            capture_output=True, text=True,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in
                self.gh_log.read_text(encoding="utf-8").splitlines() if line]

    def outputs(self) -> dict:
        out = {}
        for line in self.output.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out


def _header(token: str) -> str:
    return "AUTHORIZATION: basic " + base64.b64encode(
        f"x-access-token:{token}".encode()).decode()


class AnExpiredStartTokenStillDelivers(unittest.TestCase):
    """The incident, replayed: the token the run started with is dead and the
    work is finished on the runner."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name, token=FRESH_TOKEN)
        self.sha = self.fx.agent_finished_its_work()
        self.proc = self.fx.run_the_rescue_step()

    def test_the_step_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_the_branch_reaches_github(self):
        self.assertEqual(self.fx.remote_sha(), self.sha, self.proc.stderr)

    def test_the_pull_request_is_opened(self):
        creates = [c for c in self.fx.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, self.fx.gh_calls())
        self.assertIn(BRANCH, creates[0])
        self.assertIn(CARD_URL, " ".join(creates[0]))

    def test_the_step_reports_what_it_did(self):
        out = self.fx.outputs()
        self.assertEqual(out.get("local_work"), "true", out)
        self.assertEqual(out.get("pushed"), "true", out)
        self.assertEqual(out.get("pr_opened"), "true", out)
        self.assertEqual(out.get("rescued"), "true", out)

    def test_the_stale_credential_does_not_survive_beside_the_fresh_one(self):
        values = subprocess.run(
            [shutil.which("git"), "-C", str(self.fx.work), "config",
             "--get-all", EXTRAHEADER],
            capture_output=True, text=True,
        ).stdout.splitlines()
        self.assertEqual([v.strip() for v in values], [_header(FRESH_TOKEN)])

    def test_no_token_is_printed(self):
        printed = self.proc.stdout + self.proc.stderr
        self.assertNotIn(FRESH_TOKEN, printed)
        self.assertNotIn(STALE_TOKEN, printed)


class TheNegativeControl(unittest.TestCase):
    """The same fixture with NO fresh mint — the job-start token, as today.

    If this passed, the fixture above would be proving nothing: it is the only
    thing standing between "the re-mint works" and "this sandbox lets anything
    through".
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(self.td.name, token=STALE_TOKEN)
        self.sha = self.fx.agent_finished_its_work()
        self.proc = self.fx.run_the_rescue_step()

    def test_the_expired_token_cannot_push(self):
        self.assertEqual(self.fx.remote_sha(), "")

    def test_no_pull_request_is_claimed(self):
        self.assertEqual(self.fx.outputs().get("pr_opened"), "false")
        self.assertEqual(self.fx.outputs().get("rescued"), "false")

    def test_the_work_on_the_runner_is_still_reported(self):
        # This is what the Report step reads to say `credential`, not `died`.
        self.assertEqual(self.fx.outputs().get("local_work"), "true")

    def test_the_step_does_not_fail_the_job(self):
        # A red step here summons the medic to re-run a run that already did
        # the work — the DRE-1921 shape.
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)


class TheHappyPathIsUntouched(unittest.TestCase):
    """The agent pushed and opened its own PR, as almost every run does."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.fx = Fixture(
            self.td.name, token=FRESH_TOKEN,
            open_prs=[{"url": "https://github.com/x/y/pull/240", "number": 240}],
        )
        self.sha = self.fx.agent_finished_its_work()
        # The agent's own push, with its own (still live) credential.
        subprocess.run(
            [shutil.which("git"), "-C", str(self.fx.work), "push", "-q",
             str(self.fx.bare), f"{BRANCH}:refs/heads/{BRANCH}"], check=True,
        )
        self.proc = self.fx.run_the_rescue_step()

    def test_no_second_pull_request_is_opened(self):
        creates = [c for c in self.fx.gh_calls() if c[:2] == ["pr", "create"]]
        self.assertEqual(creates, [])

    def test_it_reports_that_it_did_nothing(self):
        out = self.fx.outputs()
        self.assertEqual(out.get("rescued"), "false", out)
        self.assertEqual(out.get("local_work"), "false", out)

    def test_the_branch_is_where_the_agent_left_it(self):
        self.assertEqual(self.fx.remote_sha(), self.sha)


if __name__ == "__main__":
    unittest.main()
