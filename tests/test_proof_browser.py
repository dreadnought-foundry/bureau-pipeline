"""The proof run's browser, first half: `prepare` and `stop` (DRE-6025).

`scripts/proof_browser.py prepare` is the workflow step before the agent: it
resolves the released commit through the sibling reader (`proof_local.load`,
`proof_local.released`), adds a git worktree for it, installs Playwright and
chromium into a virtual environment of its own, runs the declared `setup` and
`start` in that worktree, and polls `ready_url`. `stop` is the `always()` step
after it: it ends the process group, prints the start log's tail, and deletes
the browser's state file and any login file left behind.

Every test here runs against a throwaway git repository with a real tag and a
stub app on 127.0.0.1. The browser install is stubbed through the injected
runner, so nothing here needs the network or a browser; one test reads the
three install commands the runner was handed, and one reads the module's
imports, because the workflow runs both subcommands on the runner's own
`python3` with nothing pip-installed into it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import proof_browser  # noqa: E402
import proof_local  # noqa: E402

SCRIPT = ROOT / "scripts" / "proof_browser.py"
REQUIREMENTS = ROOT / "requirements-dev.txt"
PY = shlex.quote(sys.executable)

RELEASE = {"surfaces": {"web": {"tag_series": ["web-v*"], "paths": []}}}

#: Records which subcommand ran and from which directory, one line each.
RECORD_PY = """\
import os, sys
with open(sys.argv[2], "a", encoding="utf-8") as fh:
    fh.write(sys.argv[1] + " " + os.getcwd() + "\\n")
"""

#: The stub app: records its working directory, then answers 200 on every GET.
APP_PY = """\
import http.server, os, sys
with open(sys.argv[2], "a", encoding="utf-8") as fh:
    fh.write("start " + os.getcwd() + "\\n")
print("stub app listening", flush=True)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass

http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git(root, *args) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


class _Stub:
    """No network: records every command it is handed and answers each one
    with `answers[i]` (default: success)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.answers:
            return self.answers.pop(0)
        return 0, "ok\n"


class _Case(unittest.TestCase):
    """A repo whose released commit (tag `web-v1.0.0`) is not `main`, a
    declaration serving the stub app, and a runner temp for everything
    `prepare` writes."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.temp = base / "runner-temp"
        self.temp.mkdir()
        self.record = self.temp / "record.txt"
        self.port = _free_port()
        self.bureau = self.repo / ".github" / "bureau"
        self.bureau.mkdir(parents=True)
        (self.bureau / "release.json").write_text(json.dumps(RELEASE))
        (self.repo / "record.py").write_text(RECORD_PY)
        (self.repo / "app.py").write_text(APP_PY)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "released")
        self.released = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "tag", "web-v1.0.0")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
        self.main = _git(self.repo, "rev-parse", "HEAD")
        self.declare()
        self.output = self.temp / "github-output"
        self.summary = self.temp / "step-summary"
        self.worktree = self.temp / "proof-local-run"
        self.venv = self.temp / "proof-venv"
        self.pid_file = self.temp / "proof-local-run.pid"
        self.addCleanup(self._stop_quietly)

    def declare(self, **over):
        data = {
            "surface": "web",
            "setup": f"{PY} record.py setup {shlex.quote(str(self.record))}",
            "start": f"{PY} app.py {self.port} {shlex.quote(str(self.record))}",
            "ready_url": f"http://127.0.0.1:{self.port}/health",
            "ready_timeout_seconds": 20,
            "pages": {"home": "/"},
        }
        data.update(over)
        data = {k: v for k, v in data.items() if v is not None}
        (self.bureau / "proof-local.json").write_text(json.dumps(data))

    def prepare(self, runner=None, **over):
        kwargs = dict(repo_root=self.repo, github_output=self.output,
                      summary=self.summary, runner=runner or _Stub(),
                      venv=self.venv, worktree=self.worktree,
                      pid_file=self.pid_file, requirements=REQUIREMENTS)
        kwargs.update(over)
        return proof_browser.prepare(**kwargs)

    def outputs(self) -> dict:
        lines = self.output.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines)

    def summary_lines(self) -> list:
        return self.summary.read_text(encoding="utf-8").splitlines()

    def _stop_quietly(self):
        if self.pid_file.exists():
            proof_browser.stop(pid_file=self.pid_file,
                               login_file=self.temp / "none-login",
                               browser_state=self.temp / "none-state",
                               out=lambda *_: None)

    def assertFailed(self, note_prefix: str) -> str:
        found = self.outputs()
        self.assertEqual(found["status"], "failed", found)
        self.assertTrue(found["note"].startswith(note_prefix), found["note"])
        self.assertEqual(self.summary_lines(),
                         [f"proof local run: failed — {found['note']}"])
        self.assertEqual(found["url"], "")
        return found["note"]


# ---------------------------------------------------------------------------
# prepare — a healthy run
# ---------------------------------------------------------------------------

class PrepareUpTest(_Case):

    def test_a_healthy_prepare_serves_the_released_commit(self):
        stub = _Stub()
        result = self.prepare(runner=stub)
        found = self.outputs()

        self.assertEqual(tuple(found), proof_browser.OUTPUT_KEYS)
        self.assertEqual(len(proof_browser.OUTPUT_KEYS), 7)
        self.assertEqual(found["status"], "up", found)
        self.assertEqual(result["status"], "up")
        self.assertEqual(found["commit"], self.released)
        self.assertEqual(found["tag"], "web-v1.0.0")
        self.assertEqual(found["url"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(found["pid_file"], str(self.pid_file))
        self.assertEqual(found["python"], f"{self.venv}/bin/python")
        self.assertTrue(os.path.isabs(found["python"]))

        # The worktree is the released commit, never main and never the checkout.
        self.assertEqual(_git(self.worktree, "rev-parse", "HEAD"), self.released)
        self.assertNotEqual(self.released, self.main)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), self.main)

        # `setup` ran once, then `start`, both in the worktree.
        here = os.path.realpath(self.worktree)
        self.assertEqual(self.record.read_text().splitlines(),
                         [f"setup {here}", f"start {here}"])

        self.assertEqual(self.summary_lines(), [
            f"proof local run: up — web at {self.released[:7]} (web-v1.0.0) "
            f"serving http://127.0.0.1:{self.port}/health"])

        # The process is up and its pid is in the file.
        pid = int(self.pid_file.read_text().strip())
        os.kill(pid, 0)

    def test_the_summary_line_is_appended_never_overwritten(self):
        self.summary.write_text("proof identity: earlier line\n")
        self.prepare()
        self.assertEqual(self.summary_lines()[0], "proof identity: earlier line")
        self.assertTrue(self.summary_lines()[1].startswith("proof local run: up — "))

    def test_a_declaration_without_setup_starts_without_one(self):
        self.declare(setup=None)
        self.prepare()
        here = os.path.realpath(self.worktree)
        self.assertEqual(self.outputs()["status"], "up")
        self.assertEqual(self.record.read_text().splitlines(), [f"start {here}"])

    def test_a_worktree_left_by_an_earlier_attempt_is_replaced(self):
        _git(self.repo, "worktree", "add", "--detach", str(self.worktree), self.main)
        self.prepare()
        self.assertEqual(self.outputs()["status"], "up")
        self.assertEqual(_git(self.worktree, "rev-parse", "HEAD"), self.released)


# ---------------------------------------------------------------------------
# prepare — the install, three commands in the venv
# ---------------------------------------------------------------------------

def _pinned() -> str:
    pins = re.findall(r"^playwright==(\S+)\s*$", REQUIREMENTS.read_text(), re.M | re.I)
    assert len(pins) == 1, pins
    return pins[0]


class InstallTest(_Case):

    def test_the_runner_is_handed_exactly_the_three_commands(self):
        stub = _Stub()
        self.prepare(runner=stub)
        venv_python = f"{self.venv}/bin/python"
        self.assertEqual(stub.calls, [
            [sys.executable, "-m", "venv", "--system-site-packages", str(self.venv)],
            [venv_python, "-m", "pip", "install", "--disable-pip-version-check",
             "playwright==" + _pinned()],
            [venv_python, "-m", "playwright", "install", "--with-deps", "chromium"],
        ])
        self.assertEqual(stub.calls[0][0], sys.executable)
        for call in stub.calls[1:]:
            self.assertEqual(call[0], venv_python)
        for call in stub.calls:
            self.assertNotIn("--user", call)
            self.assertNotIn("--break-system-packages", call)
            if call[0] == sys.executable:
                self.assertNotIn("pip", call)

    def test_install_commands_is_the_contract(self):
        self.assertEqual(proof_browser.install_commands("/usr/bin/python3", "/t/v", "9.9.9"), [
            ["/usr/bin/python3", "-m", "venv", "--system-site-packages", "/t/v"],
            ["/t/v/bin/python", "-m", "pip", "install", "--disable-pip-version-check",
             "playwright==9.9.9"],
            ["/t/v/bin/python", "-m", "playwright", "install", "--with-deps", "chromium"],
        ])

    def test_the_pin_is_read_off_the_manifest_never_a_constant(self):
        self.assertEqual(proof_browser.playwright_pin(), _pinned())
        self.assertEqual(proof_browser.REQUIREMENTS.resolve(), REQUIREMENTS.resolve())
        manifest = self.temp / "requirements-dev.txt"
        manifest.write_text("pytest==9.1.1\nplaywright==0.0.1\n")
        stub = _Stub()
        self.prepare(runner=stub, requirements=manifest)
        self.assertEqual(stub.calls[1][-1], "playwright==0.0.1")
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(_pinned(), source, "the pin is restated as a constant")

    def test_the_venv_default_is_pinned(self):
        self.assertEqual(proof_browser.PROOF_VENV_DEFAULT, "$RUNNER_TEMP/proof-venv")


# ---------------------------------------------------------------------------
# prepare — every failure is a status, never an exception
# ---------------------------------------------------------------------------

class PrepareFailedTest(_Case):

    def test_an_invalid_declaration_is_failed(self):
        self.declare(ready_url="http://localhost:5173/")
        stub = _Stub()
        self.prepare(runner=stub)
        note = self.assertFailed("the declaration is invalid: ")
        self.assertIn("ready_url", note)
        self.assertEqual(stub.calls, [])

    def test_a_surface_with_no_release_is_failed(self):
        _git(self.repo, "tag", "-d", "web-v1.0.0")
        stub = _Stub()
        self.prepare(runner=stub)
        self.assertEqual(self.assertFailed("surface web has no release yet"),
                         "surface web has no release yet")
        self.assertEqual(stub.calls, [])
        self.assertFalse(self.worktree.exists())

    def test_a_manifest_with_no_pin_is_failed(self):
        manifest = self.temp / "requirements-dev.txt"
        manifest.write_text("pytest==9.1.1\nPyYAML==6.0.3\n")
        stub = _Stub()
        self.prepare(runner=stub, requirements=manifest)
        self.assertEqual(self.assertFailed("requirements-dev.txt pins no playwright"),
                         "requirements-dev.txt pins no playwright")
        self.assertEqual(stub.calls, [])

    def test_a_failed_browser_install_is_failed_with_its_last_line(self):
        for failing in range(3):
            with self.subTest(command=failing):
                answers = [(0, "ok\n")] * failing + [
                    (1, "Collecting playwright\nERROR: no matching distribution\n\n")]
                stub = _Stub(*answers)
                self.output.unlink(missing_ok=True)
                self.summary.unlink(missing_ok=True)
                self.prepare(runner=stub)
                self.assertEqual(
                    self.assertFailed("the browser could not be installed: "),
                    "the browser could not be installed: ERROR: no matching distribution")
                self.assertEqual(len(stub.calls), failing + 1)
                self.assertEqual(self.outputs()["python"], "")
                self.assertFalse(self.record.exists(), "setup ran after a failed install")

    def test_a_runner_with_no_venv_module_is_failed_not_an_exception(self):
        def missing(argv):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        self.prepare(runner=missing)
        self.assertFailed("the browser could not be installed: ")

    def test_a_start_command_that_exits_is_failed(self):
        self.declare(start=f"{PY} -c 'import sys; sys.exit(3)'",
                     ready_timeout_seconds=60)
        began = time.monotonic()
        self.prepare()
        self.assertLess(time.monotonic() - began, 30, "it waited out the timeout")
        note = self.assertFailed("`start` exited")
        self.assertIn("3", note)

    def test_a_ready_url_that_never_answers_is_failed(self):
        self.declare(start=f"{PY} -c 'import time; time.sleep(60)'",
                     ready_timeout_seconds=1)
        self.prepare()
        note = self.assertFailed(f"http://127.0.0.1:{self.port}/health did not answer 200")
        self.assertIn("1s", note)
        self.assertFalse(self.pid_file.exists(), "the failed start was left running")

    def test_a_setup_that_fails_is_failed(self):
        self.declare(setup=f"{PY} -c 'import sys; sys.exit(4)'")
        self.prepare()
        self.assertFailed("`setup` exited 4")
        self.assertFalse(self.record.exists(), "start ran after a failed setup")

    def test_the_failure_notes_are_distinct(self):
        notes = set()
        cases = [
            lambda: (self.declare(pages={}), self.prepare()),
            lambda: (_git(self.repo, "tag", "-d", "web-v1.0.0"), self.prepare()),
            lambda: self.prepare(requirements=self.temp / "absent.txt"),
            lambda: self.prepare(runner=_Stub((1, "boom\n"))),
            lambda: (self.declare(start="exit 3"), self.prepare()),
            lambda: (self.declare(start=f"{PY} -c 'import time; time.sleep(60)'",
                                  ready_timeout_seconds=1), self.prepare()),
        ]
        for case in cases:
            self.setUp()
            case()
            found = self.outputs()
            self.assertEqual(found["status"], "failed", found)
            notes.add(found["note"])
        self.assertEqual(len(notes), 6, notes)

    def test_no_declaration_is_none(self):
        (self.bureau / "proof-local.json").unlink()
        stub = _Stub()
        self.prepare(runner=stub)
        found = self.outputs()
        self.assertEqual(found["status"], "none")
        self.assertEqual(self.summary_lines(),
                         ["proof local run: none, no .github/bureau/proof-local.json"])
        self.assertEqual(stub.calls, [])

    def test_the_cli_writes_none_and_exits_0(self):
        (self.bureau / "proof-local.json").unlink()
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "prepare", "--github-output", str(self.output),
             "--summary", str(self.summary)],
            cwd=self.repo, capture_output=True, text=True,
            env={**os.environ, "RUNNER_TEMP": str(self.temp)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.outputs()["status"], "none")
        self.assertEqual(tuple(self.outputs()), proof_browser.OUTPUT_KEYS)
        self.assertEqual(self.summary_lines(),
                         ["proof local run: none, no .github/bureau/proof-local.json"])

    def test_the_cli_writes_failed_and_exits_0(self):
        self.declare(pages={})
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "prepare", "--github-output", str(self.output),
             "--summary", str(self.summary)],
            cwd=self.repo, capture_output=True, text=True,
            env={**os.environ, "RUNNER_TEMP": str(self.temp)})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertFailed("the declaration is invalid: ")

    def test_a_note_never_carries_a_second_line(self):
        stub = _Stub((1, "first\nsecond\r\nthird\x1b[0m\n"))
        self.prepare(runner=stub)
        for line in self.output.read_text().splitlines():
            self.assertRegex(line, r"^[a-z_]+=")
        self.assertEqual(len(self.summary_lines()), 1)


# ---------------------------------------------------------------------------
# stop — ends the group, prints the tail, deletes the credentials
# ---------------------------------------------------------------------------

class StopTest(unittest.TestCase):

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.temp = Path(self._tmp.name)
        self.pid_file = self.temp / "proof-local-run.pid"
        self.log = self.temp / "proof-local-run.log"
        self.login = self.temp / "proof-login.json"
        self.state = self.temp / "proof-browser-state.json"
        self.lines = []

    def stop(self) -> int:
        return proof_browser.stop(pid_file=self.pid_file, login_file=self.login,
                                  browser_state=self.state,
                                  out=lambda line="": self.lines.append(line))

    def _group(self):
        """A leader and a child in a session of their own, as `prepare`
        starts them; the child's pid is in the file the leader writes."""
        child_pid = self.temp / "child.pid"
        code = ("import subprocess, sys, time; "
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"open({str(child_pid)!r}, 'w').write(str(p.pid)); time.sleep(60)")
        leader = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        self.addCleanup(lambda: leader.poll() is None and leader.kill())
        for _ in range(100):
            if child_pid.exists() and child_pid.read_text():
                break
            time.sleep(0.05)
        return leader, int(child_pid.read_text())

    def test_it_ends_the_process_group_and_deletes_the_credentials(self):
        leader, child = self._group()
        self.pid_file.write_text(f"{leader.pid}\n")
        self.log.write_text("".join(f"line {n}\n" for n in range(1, 31)))
        self.login.write_text('{"username": "u", "password": "secret"}')
        self.state.write_text('{"cookies": []}')

        self.assertEqual(self.stop(), 0)

        self.assertIsNotNone(leader.poll(), "the leader is still running")
        for _ in range(100):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail("the group's child is still running")
        self.assertFalse(self.login.exists())
        self.assertFalse(self.state.exists())
        text = "\n".join(self.lines)
        for n in range(11, 31):
            self.assertIn(f"line {n}", self.lines)
        self.assertNotIn("line 10", self.lines)
        self.assertNotIn("secret", text)
        self.assertFalse(self.pid_file.exists())

    def test_the_files_are_overwritten_before_they_are_unlinked(self):
        # A second name for the same inode outlives the unlink, so it shows
        # whether the bytes were overwritten in place first.
        for path in (self.login, self.state):
            path.write_text('{"password": "secret"}')
            os.link(path, f"{path}.witness")
        self.stop()
        for path in (self.login, self.state):
            self.assertFalse(path.exists())
            left = Path(f"{path}.witness").read_bytes()
            self.assertNotIn(b"secret", left, f"{path.name} was unlinked without being overwritten")
            self.assertEqual(len(left), len('{"password": "secret"}'))

    def test_a_missing_pid_file_and_absent_files_are_plain_lines(self):
        self.assertEqual(self.stop(), 0)
        text = "\n".join(self.lines)
        self.assertIn(f"no pid file at {self.pid_file}", text)
        self.assertIn(f"no file at {self.login}", text)
        self.assertIn(f"no file at {self.state}", text)
        self.assertNotIn("Traceback", text)

    def test_a_group_already_gone_is_a_plain_line(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        gone.wait()
        self.pid_file.write_text(str(gone.pid))
        self.assertEqual(self.stop(), 0)
        self.assertIn("already gone", "\n".join(self.lines))

    def test_the_cli_reads_the_contract_env_names(self):
        self.login.write_text("{}")
        self.state.write_text("{}")
        env = {**os.environ, "RUNNER_TEMP": str(self.temp)}
        for name in ("PROOF_LOGIN_FILE", "PROOF_BROWSER_STATE", "PROOF_LOCAL_PID_FILE"):
            env.pop(name, None)
        done = subprocess.run([sys.executable, str(SCRIPT), "stop"], env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(self.login.exists(), "the default PROOF_LOGIN_FILE was kept")
        self.assertFalse(self.state.exists(), "the default PROOF_BROWSER_STATE was kept")

        other = self.temp / "elsewhere"
        other.mkdir()
        login, state = other / "l.json", other / "s.json"
        login.write_text("{}")
        state.write_text("{}")
        env.update(PROOF_LOGIN_FILE=str(login), PROOF_BROWSER_STATE=str(state),
                   PROOF_LOCAL_PID_FILE=str(other / "run.pid"))
        done = subprocess.run([sys.executable, str(SCRIPT), "stop"], env=env,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(login.exists())
        self.assertFalse(state.exists())
        self.assertIn(f"no pid file at {other / 'run.pid'}", done.stdout)

    def test_the_defaults_match_the_contract(self):
        self.assertEqual(proof_browser.PID_FILE_DEFAULT, "$RUNNER_TEMP/proof-local-run.pid")
        self.assertEqual(proof_browser.LOGIN_FILE_DEFAULT, "$RUNNER_TEMP/proof-login.json")
        self.assertEqual(proof_browser.BROWSER_STATE_DEFAULT,
                         "$RUNNER_TEMP/proof-browser-state.json")
        self.assertEqual(proof_browser.WORKTREE_DEFAULT, "$RUNNER_TEMP/proof-local-run")


class PrepareThenStopTest(_Case):

    def test_stop_ends_what_prepare_started(self):
        self.prepare()
        pid = int(self.pid_file.read_text())
        lines = []
        proof_browser.stop(pid_file=self.pid_file, login_file=self.temp / "l",
                           browser_state=self.temp / "s",
                           out=lambda line="": lines.append(line))
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)
        self.assertIn("stub app listening", lines)


# ---------------------------------------------------------------------------
# The manifest and the runner's own python3
# ---------------------------------------------------------------------------

class ManifestTest(unittest.TestCase):

    def test_the_manifest_holds_exactly_one_playwright_pin(self):
        lines = [line for line in REQUIREMENTS.read_text().splitlines()
                 if line.lower().startswith("playwright")]
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^playwright==\d+\.\d+\.\d+$")


class ImportsTest(unittest.TestCase):

    def test_it_imports_only_the_standard_library_and_its_siblings(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        foreign = {n for n in names if n not in sys.stdlib_module_names
                   and n not in ("proof_local", "release_train")}
        self.assertEqual(foreign, set())
        self.assertIn("proof_local", names)
        everywhere = {a.name.split(".")[0] for node in ast.walk(tree)
                      if isinstance(node, ast.Import) for a in node.names}
        self.assertNotIn("playwright", everywhere)

    def test_it_runs_with_site_packages_switched_off(self):
        with TemporaryDirectory() as tmp:
            done = subprocess.run(
                [sys.executable, "-I", "-S", str(SCRIPT), "stop"],
                capture_output=True, text=True,
                env={**os.environ, "RUNNER_TEMP": tmp})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_it_reads_the_declaration_through_the_sibling(self):
        self.assertIs(proof_browser.proof_local, proof_local)


if __name__ == "__main__":
    unittest.main()
