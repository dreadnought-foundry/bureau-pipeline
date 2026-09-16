"""Scaffolding for the bot-branch publish tests (DRE-3879) — not a test module.

A throwaway repository with a real bare `origin`, a `scripts/` link to this
repo's own scripts, and a fake `gh` on `PATH` that records every call it is
given. The suites that use it RUN the publishing step rather than reading the
workflow for the right words: what has to be proved is WHERE the commit lands
and HOW MANY pull requests a week of runs opens, and neither is visible in a
grep of a YAML file.

The fake `gh` answers the two subcommands this path uses and nothing else:

  * `pr list --json …` prints whatever the store file holds (`[]` by default,
    i.e. no open pull request), or fails when `FAKE_GH_FAIL_LIST` is set — the
    "GitHub would not say" case, which must never be read as "no PR";
  * `pr create …` records the call, writes the new pull request into the store
    so the NEXT run finds it open, and prints its url.

Every call's argv is appended to `FAKE_GH_LOG` as one JSON array per line — so
a test can assert both the number of `pr create` calls and the exact body the
pull request was opened with. JSON rather than a delimiter because the body is
multi-line prose, and any separator that is not escaped puts one call across
several lines of the log.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — fixed-arg git/bash calls against a temp repo
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_GH = '''#!/usr/bin/env python3
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
store = os.environ["FAKE_GH_PRS"]
if argv[:2] == ["pr", "list"]:
    if os.environ.get("FAKE_GH_FAIL_LIST"):
        sys.stderr.write("gh: could not read the pull request list\\n")
        sys.exit(1)
    try:
        with open(store) as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        rows = []
    print(json.dumps(rows))
    sys.exit(0)
if argv[:2] == ["pr", "create"]:
    head = argv[argv.index("--head") + 1]
    url = "https://example.invalid/pull/1"
    with open(store, "w") as fh:
        json.dump([{"url": url, "number": 1, "headRefName": head}], fh)
    print(url)
    sys.exit(0)
sys.exit(0)
'''


def git(*args, cwd):
    return subprocess.run(  # nosec B603 B607 — fixed args, temp repo
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


class BotBranchRepo:
    """A repository a scheduled job could run in, and a fake GitHub."""

    def __init__(self, cleanup):
        self._tmp = tempfile.TemporaryDirectory()
        cleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        self.work = root / "work"
        self.runner_temp = root / "runner-temp"
        self.runner_temp.mkdir()
        self.gh_log = root / "gh-calls.log"
        self.gh_log.write_text("")
        self.gh_store = root / "gh-prs.json"
        self.gh_store.write_text("[]")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.bin = bin_dir
        subprocess.run(  # nosec B603 B607
            ["git", "init", "--bare", "-b", "main", str(self.origin)],
            capture_output=True, check=True)
        self.work.mkdir()
        git("init", "-b", "main", cwd=self.work)
        # The scripts under test, reached exactly as a checkout reaches them.
        (self.work / "scripts").symlink_to(ROOT / "scripts")
        self.fail_pr_list = False

    # -- seeding ---------------------------------------------------------

    def write(self, path: str, text: str) -> None:
        target = self.work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def seed(self) -> None:
        """Commit what has been written so far and publish it as `main`."""
        git("add", "-A", cwd=self.work)
        git("-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-m", "seed", cwd=self.work)
        git("remote", "add", "origin", str(self.origin), cwd=self.work)
        git("push", "origin", "main", cwd=self.work)

    # -- running ---------------------------------------------------------

    def env(self) -> dict:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.work),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GH_TOKEN": "unused-in-this-harness",
            "GITHUB_REPOSITORY": "dreadnought-foundry/bureau-pipeline",
            "RUNNER_TEMP": str(self.runner_temp),
            "DEFAULT_BRANCH": "main",
            "FAKE_GH_LOG": str(self.gh_log),
            "FAKE_GH_PRS": str(self.gh_store),
        }
        if self.fail_pr_list:
            env["FAKE_GH_FAIL_LIST"] = "1"
        return env

    def run(self, script: str):
        """Run a shell script in the work tree, the way a step would."""
        return subprocess.run(  # nosec B603 B607 — the workflow's own script
            ["bash", "-e", "-c", script], cwd=str(self.work), env=self.env(),
            capture_output=True, text=True)

    # -- reading back ----------------------------------------------------

    def origin_ref(self, ref: str) -> str:
        """The sha `origin` holds for `ref`, or "" when it holds none."""
        done = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=str(self.origin), capture_output=True, text=True)
        return done.stdout.strip()

    def origin_files(self, ref: str) -> list[str]:
        out = git("show", "--name-only", "--format=", ref, cwd=self.origin)
        return sorted(out.stdout.split())

    def origin_log(self, ref: str) -> str:
        return git("log", "-1", "--format=%an|%ae|%s", ref, cwd=self.origin).stdout

    def gh_calls(self) -> list[list[str]]:
        text = self.gh_log.read_text()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def pr_creates(self) -> list[list[str]]:
        return [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]

    def flag(self, call: list[str], name: str) -> str:
        return call[call.index(name) + 1] if name in call else ""
