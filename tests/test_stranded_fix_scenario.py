"""The DRE-4486 mechanisms, RUN rather than read.

`tests/test_stranded_fix.py` pins the decisions. This runs them: the shipped
`Refuse a push onto a merged pull request` step's own YAML body, executed by
bash in a real git repository with a real bare `origin` and a stub `gh`, and
then an actual `git push` through the hook it installs. Unit-green is not
live-working — the hook is three systems deep (Actions YAML → git's hook
protocol → the `gh` read), and every one of the four occurrences this card
exists for happened at a seam like that.

The occurrences: portico #611 (DRE-4183, +9 min, now the live bug DRE-4460),
portico #351 (DRE-2637, +4 min), DRE-2227 (recovered as DRE-2989), DRE-2591.

Run: cd bureau-pipeline && python3 -m pytest tests/test_stranded_fix_scenario.py -v
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

AGENT_FIX = ROOT / ".github" / "workflows" / "agent-fix.yml"
SCRIPT = ROOT / "scripts" / "stranded_fix.py"

REPO = "dreadnought-foundry/portico"
PR = 611
BRANCH = "agent/DRE-4183-auth-gate-veil"

#: A `gh` that answers only what these scenarios ask, off a JSON fixture, and
#: dies loudly on anything else — an unexpected call is a hole in the harness,
#: never something to wave through.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
fx = json.load(open(os.environ["FIXTURE"]))
with open(os.environ["GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")

if fx.get("die"):
    sys.stderr.write(fx["die"] + "\n")
    raise SystemExit(1)

def emit(value):
    print(value if isinstance(value, str) else json.dumps(value))
    raise SystemExit(0)

if args[:2] == ["pr", "view"]:
    emit(fx["pr"])
if args[:2] == ["run", "list"]:
    emit(fx["runs"])
if args[0] == "api" and "/jobs" in args[1]:
    emit(fx["jobs"].get(args[1].rsplit("/", 2)[-2], []))

sys.stderr.write("unexpected gh call: %r\n" % (args,))
raise SystemExit(2)
'''


def hook_step() -> dict:
    doc = yaml.safe_load(AGENT_FIX.read_text())
    steps = doc["jobs"]["fix"]["steps"]
    found = [s for s in steps
             if s.get("name") == "Refuse a push onto a merged pull request"]
    assert len(found) == 1, "expected exactly one hook-install step"
    return found[0]


def substitute(text: str, values: dict) -> str:
    """The `${{ }}` substitutions Actions would make. An expression with no
    value here is a hole in the harness, not something to skip."""
    def repl(m):
        key = m.group(1).strip()
        assert key in values, f"harness has no value for ${{{{ {key} }}}}"
        return values[key]

    out = re.sub(r"\$\{\{([^}]*)\}\}", repl, text)
    assert "${{" not in out
    return out


class _Workspace:
    """A product-repo checkout with a real bare `origin` and a stub `gh`."""

    def __init__(self, stack, fixture: dict):
        self.dir = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        stub = self.bin / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)  # nosec B103 — a test stub on PATH
        self.fixture = self.dir / "fixture.json"
        self.fixture.write_text(json.dumps(fixture))
        self.log = self.dir / "gh.log"
        self.log.write_text("")

        self.origin = self.dir / "origin.git"
        self.work = self.dir / "work"
        self._git_init()
        os.symlink(ROOT, self.work / ".bureau-pipeline")

    def _git(self, *args, cwd=None, check=True):
        return subprocess.run(  # nosec B603 B607 — fixed argv, our own repo
            ["git", *args], cwd=str(cwd or self.work), env=self.env(),
            capture_output=True, text=True, check=check)

    def _git_init(self):
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)],  # nosec B603 B607
                       check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.work)],  # nosec B603 B607
                       check=True)
        for key, value in (("user.email", "bot@example.invalid"),
                           ("user.name", "bureau bot")):
            self._git("config", key, value)
        (self.work / "a.txt").write_text("base\n")
        self._git("add", "a.txt")
        self._git("commit", "-qm", "base")
        self._git("remote", "add", "origin", str(self.origin))
        self._git("push", "-q", "origin", "main")
        self._git("checkout", "-qb", BRANCH)

    def env(self) -> dict:
        return {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FIXTURE": str(self.fixture),
            "GH_LOG": str(self.log),
        }

    def install_hook(self):
        step = hook_step()
        body = substitute(step["run"], {})
        env = {
            **self.env(),
            **{k: substitute(str(v), {
                "steps.reader.outputs.token": "reader-token",
                "github.repository": REPO,
                "steps.pr.outputs.number": str(PR),
                "github.workspace": str(self.work),
            }) for k, v in step["env"].items()},
        }
        return subprocess.run(  # nosec B603 B607 — our own workflow body
            ["bash", "-c", body], cwd=str(self.work), env=env,
            capture_output=True, text=True)

    def commit_a_fix(self):
        (self.work / "fix.txt").write_text("the fix\n")
        self._git("add", "fix.txt")
        self._git("commit", "-qm",
                  "fix(DRE-4183): address review findings (attempt 1)")

    def push(self):
        return self._git("push", "origin", BRANCH, check=False)

    def gh_calls(self):
        return [json.loads(ln) for ln in self.log.read_text().splitlines() if ln]


def _pr(state: str) -> dict:
    return {"state": state,
            "mergedAt": "2026-08-11T08:34:12Z" if state == "MERGED" else None}


class ThePrePushHookRefusesAMergedBranch(unittest.TestCase):
    """The shipped step, its hook, and a real `git push` through it."""

    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def _workspace(self, state: str) -> _Workspace:
        ws = _Workspace(self.stack, {"pr": json.dumps(_pr(state))})
        installed = ws.install_hook()
        self.assertEqual(installed.returncode, 0,
                         f"{installed.stdout}{installed.stderr}")
        ws.commit_a_fix()
        return ws

    def test_a_push_onto_a_merged_pull_request_is_refused(self):
        ws = self._workspace("MERGED")
        pushed = ws.push()
        self.assertNotEqual(pushed.returncode, 0,
                            "git push succeeded onto a merged branch")
        self.assertIn("merged", (pushed.stderr + pushed.stdout).lower())

    def test_and_the_commit_never_reaches_origin(self):
        """The whole point. A refusal that still left the commit on `origin`
        would be the same stranding with a louder log."""
        ws = self._workspace("MERGED")
        ws.push()
        remote = subprocess.run(  # nosec B603 B607 — our own bare repo
            ["git", "ls-remote", "--heads", str(ws.origin), BRANCH],
            capture_output=True, text=True, check=True)
        self.assertEqual(remote.stdout.strip(), "",
                         "the refused fix recreated the merged branch anyway")

    def test_an_open_pull_request_pushes_normally(self):
        """The control. A hook that refused everything would pass the test
        above and break every fix run in the fleet."""
        ws = self._workspace("OPEN")
        pushed = ws.push()
        self.assertEqual(pushed.returncode, 0,
                         f"{pushed.stdout}{pushed.stderr}")
        remote = subprocess.run(  # nosec B603 B607 — our own bare repo
            ["git", "ls-remote", "--heads", str(ws.origin), BRANCH],
            capture_output=True, text=True, check=True)
        self.assertIn(BRANCH, remote.stdout)

    def test_an_unreadable_pull_request_state_still_pushes(self):
        """FAIL OPEN, and deliberately: this hook runs on every push of every
        fix run, and one that refused on an API blip would break the loop it
        protects."""
        ws = _Workspace(self.stack, {"die": "gh: HTTP 403: rate limit"})
        self.assertEqual(ws.install_hook().returncode, 0)
        ws.commit_a_fix()
        self.assertEqual(ws.push().returncode, 0)

    def test_the_hook_asks_about_this_repo_and_this_pull_request(self):
        ws = self._workspace("MERGED")
        ws.push()
        asks = [c for c in ws.gh_calls() if c[:2] == ["pr", "view"]]
        self.assertTrue(asks, "the hook made no `gh pr view` call")
        self.assertIn(str(PR), asks[0])
        self.assertIn(REPO, asks[0])


class TheLaneGathererRunsForReal(unittest.TestCase):
    def _lane(self, fixture: dict) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            (td / "bin").mkdir()
            stub = td / "bin" / "gh"
            stub.write_text(GH_STUB)
            stub.chmod(0o755)  # nosec B103 — a test stub on PATH
            (td / "fixture.json").write_text(json.dumps(fixture))
            (td / "gh.log").write_text("")
            out = td / "lane.json"
            proc = subprocess.run(  # nosec B603 B607 — fixed argv
                [sys.executable, str(SCRIPT), "lane", "--repo", REPO,
                 "--out", str(out)],
                capture_output=True, text=True,
                env={**os.environ,
                     "PATH": f"{td / 'bin'}{os.pathsep}{os.environ['PATH']}",
                     "FIXTURE": str(td / "fixture.json"),
                     "GH_LOG": str(td / "gh.log")},
            )
            self.assertEqual(proc.returncode, 0,
                             f"{proc.stdout}{proc.stderr}")
            return json.loads(out.read_text())

    def test_an_in_flight_run_is_attributed_to_its_pull_request(self):
        record = self._lane({
            "runs": [{"status": "in_progress", "databaseId": 33231413617}],
            "jobs": {"33231413617": [f"fix PR #{PR}"]},
        })
        import stranded_fix as sf

        lane = sf.read_lane(record)
        self.assertEqual(lane.by_pr, {PR: 33231413617})
        self.assertIsNotNone(sf.lane_refusal(lane, PR))
        self.assertIsNone(sf.lane_refusal(lane, PR + 1))

    def test_a_finished_run_is_not_in_the_record(self):
        record = self._lane({
            "runs": [{"status": "completed", "databaseId": 1}], "jobs": {}})
        self.assertEqual(record, {"readable": True, "runs": []})

    def test_a_refused_listing_is_written_in_as_unreadable(self):
        """It must not fail the gate's step, and it must not read as idle."""
        import stranded_fix as sf

        record = self._lane({"die": "gh: HTTP 403: Resource not accessible"})
        self.assertFalse(record["readable"])
        self.assertIsNotNone(sf.lane_refusal(sf.read_lane(record), PR))


class LocalCompareReadsTheWorkspace(unittest.TestCase):
    """The commits a refused push leaves behind exist only in the clone, so
    the card that names them is composed from `git log`, not from a compare
    GitHub cannot make."""

    def test_it_names_the_commits_beyond_the_base(self):
        import contextlib

        import stranded_fix as sf

        with contextlib.ExitStack() as stack:
            ws = _Workspace(stack, {"pr": json.dumps(_pr("MERGED"))})
            ws.commit_a_fix()
            record = sf.local_compare("origin/main", cwd=str(ws.work))
        self.assertEqual(record["ahead_by"], 1)
        entry = record["commits"][0]
        self.assertEqual(entry["commit"]["message"],
                         "fix(DRE-4183): address review findings (attempt 1)")
        self.assertTrue(entry["commit"]["committer"]["date"])
        self.assertEqual(len(entry["sha"]), 40)

    def test_a_base_git_cannot_resolve_yields_nothing_rather_than_raising(self):
        import contextlib

        import stranded_fix as sf

        with contextlib.ExitStack() as stack:
            ws = _Workspace(stack, {"pr": json.dumps(_pr("MERGED"))})
            record = sf.local_compare("origin/no-such-ref", cwd=str(ws.work))
        self.assertEqual(record, {"ahead_by": 0, "commits": []})


if __name__ == "__main__":
    unittest.main()
