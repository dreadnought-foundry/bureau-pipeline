#!/usr/bin/env python3
"""The proof run's browser, first half: `prepare` and `stop` (DRE-6025).

A proof card's `Local screen:` criterion is observed in a browser on a local
run of the released commit. The workflow (`proof-task.yml`, DRE-6027) runs
`prepare` before the agent and `stop` after it, `always()`; the two commands
the agent runs during the proof, `login` and `screenshot`, are DRE-6047's, in
`scripts/proof_session.py`. `docs/proof-local-run.md` is the human contract.

`prepare` reads the repo's declaration through the sibling reader
(`proof_local.load`, `proof_local.released`), adds a git worktree for the
released commit at `$RUNNER_TEMP/proof-local-run` — never the checkout, where
the agent writes its record — installs the browser, runs the declared `setup`
once in that worktree, starts `start` there detached, and polls `ready_url`
until it answers 200. Every failure is a STATUS, never an exception out of
the step: the run goes on and the rows that needed the browser read
`Not observed.` with the note.

The module runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, and never imports Playwright. The browser
lives in a virtual environment of its own at `$RUNNER_TEMP/proof-venv`,
installed by the venv's own pip — exempt from PEP 668's
`EXTERNALLY-MANAGED` marker on every runner, so no `sudo`, no `--user`, no
`--break-system-packages`. The Playwright version has one home, the
`playwright==` line in `requirements-dev.txt`, so a Dependabot bump moves the
proof run and the suite together.

`stop` ends the process group the pid file names, prints the start log's
last 20 lines, and overwrites then unlinks the browser's state file and any
login file left behind, so a run that died mid-proof leaves no credential on
the runner.

CLI:
    proof_browser.py prepare --github-output PATH --summary PATH [--repo-root .]
    proof_browser.py stop

Both exit 0 on every outcome they can name.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import proof_local  # noqa: E402

#: Opens every line `stop` prints, and every summary line `prepare` appends.
SUMMARY = "proof local run"

PROOF_VENV_DEFAULT = "$RUNNER_TEMP/proof-venv"
WORKTREE_DEFAULT = "$RUNNER_TEMP/proof-local-run"
PID_FILE_DEFAULT = "$RUNNER_TEMP/proof-local-run.pid"
#: The same names and defaults DRE-6047's `proof_session.py` writes under.
LOGIN_FILE_DEFAULT = "$RUNNER_TEMP/proof-login.json"
BROWSER_STATE_DEFAULT = "$RUNNER_TEMP/proof-browser-state.json"

#: The one home of the Playwright pin — `.bureau-pipeline/requirements-dev.txt`
#: on a product repo's runner.
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements-dev.txt"

#: The GitHub output keys `prepare` writes — these and no others.
OUTPUT_KEYS = ("status", "note", "url", "commit", "tag", "pid_file", "python")

LOG_TAIL = 20
POLL_SECONDS = 0.5
STOP_GRACE_SECONDS = 10

_PIN = re.compile(r"^\s*playwright\s*==\s*([^\s;#]+)\s*(?:[;#].*)?$", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class Failed(Exception):
    """`prepare` cannot stand the run up; the message is the one-line note."""


class NotDeclared(Exception):
    """The repo declares no local run — `status=none`, not a failure."""


# ---------------------------------------------------------------------------
# Paths and the pin
# ---------------------------------------------------------------------------

def runner_path(value) -> Path:
    """`value` with `$RUNNER_TEMP` expanded — the system temp directory when
    the variable is unset, so a run off Actions still has somewhere to write."""
    temp = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    return Path(str(value).replace("$RUNNER_TEMP", temp))


def _env_path(name: str, default: str) -> Path:
    return runner_path(os.environ.get(name) or default)


def log_path(pid_file) -> Path:
    """The start log sits beside the pid file, so `stop` finds both from one."""
    return Path(pid_file).with_suffix(".log")


def playwright_pin(requirements=REQUIREMENTS) -> str | None:
    """The version `requirements-dev.txt`'s one `playwright==` line pins, or
    None when it pins none."""
    try:
        text = Path(requirements).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    pins = [found.group(1) for found in map(_PIN.match, text.splitlines()) if found]
    if len(pins) > 1:
        raise Failed(f"requirements-dev.txt pins playwright {len(pins)} times")
    return pins[0] if pins else None


def install_commands(python3: str, venv, pin: str) -> list:
    """The browser install, in order: the venv, the package into it with the
    venv's own pip, then chromium with its system dependencies."""
    venv = str(venv)
    python = venv + "/bin/python"
    return [
        [python3, "-m", "venv", "--system-site-packages", venv],
        [python, "-m", "pip", "install", "--disable-pip-version-check",
         "playwright==" + pin],
        [python, "-m", "playwright", "install", "--with-deps", "chromium"],
    ]


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def _one_line(text) -> str:
    return _CONTROL.sub(" ", str(text)).strip()


def _last_line(output: str) -> str:
    lines = [line for line in str(output).splitlines() if line.strip()]
    return _one_line(lines[-1]) if lines else ""


def run_install(argv) -> tuple:
    """The default runner: (exit code, combined output). The output goes on to
    stderr too, so the step's log shows what the installer said."""
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, errors="replace")
    except OSError as exc:
        return 127, str(exc)
    sys.stderr.write(done.stdout)
    return done.returncode, done.stdout


def _git(repo_root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)


def _add_worktree(repo_root, worktree: Path, sha: str) -> None:
    """A detached worktree at `sha`, replacing one an earlier attempt left."""
    if worktree.exists():
        _git(repo_root, "worktree", "remove", "--force", str(worktree))
    _git(repo_root, "worktree", "prune")
    done = _git(repo_root, "worktree", "add", "--detach", str(worktree), sha)
    if done.returncode != 0:
        raise Failed(f"the released worktree could not be added: "
                     f"{_last_line(done.stderr) or f'git exited {done.returncode}'}")


def _install(runner, python3: str, venv: Path, pin: str) -> None:
    for argv in install_commands(python3, venv, pin):
        try:
            code, output = runner(argv)
        except OSError as exc:
            code, output = 127, str(exc)
        if code != 0:
            raise Failed("the browser could not be installed: "
                         + (_last_line(output) or f"{argv[2]} exited {code}"))


def _end_group(pgid: int, grace: float = STOP_GRACE_SECONDS) -> str:
    """SIGTERM the group, wait up to `grace`, then SIGKILL. Returns what
    happened, in a word: `stopped`, `killed` or `gone`."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "gone"
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.waitpid(pgid, os.WNOHANG)      # reaps the leader when it is ours
        except ChildProcessError:
            pass
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return "stopped"
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    try:
        os.waitpid(pgid, 0)
    except ChildProcessError:
        pass
    return "killed"


def _ready(url: str, timeout: float) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _serve(declaration, worktree: Path, pid_file: Path,
           poll_seconds: float) -> None:
    """`setup` once, then `start` detached, then poll `ready_url`. A start
    that fails is ended here, so a broken server never outlives `prepare`."""
    log = log_path(pid_file)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as out:
        if declaration.setup:
            done = subprocess.run(declaration.setup, shell=True, cwd=worktree,
                                  stdout=out, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL)
            if done.returncode != 0:
                raise Failed(f"`setup` exited {done.returncode} in the released worktree")
        out.flush()
        process = subprocess.Popen(declaration.start, shell=True, cwd=worktree,
                                   stdout=out, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")

    url = declaration.ready_url
    timeout = declaration.ready_timeout_seconds
    deadline = time.monotonic() + timeout
    while True:
        code = process.poll()
        if code is not None:
            _end_group(process.pid, grace=1)
            pid_file.unlink(missing_ok=True)
            raise Failed(f"`start` exited {code} before {url} answered")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _ready(url, timeout=max(0.1, min(2.0, remaining))):
            return
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
    _end_group(process.pid)
    pid_file.unlink(missing_ok=True)
    raise Failed(f"{url} did not answer 200 within {timeout}s")


def _origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _stand_up(values: dict, repo_root, declaration_path, runner, python3, venv,
              worktree, pid_file, requirements, poll_seconds) -> str:
    """Fills `values` as far as it gets; returns the `up` summary line or
    raises `Failed`/`FileNotFoundError`."""
    try:
        declaration = proof_local.load(declaration_path)
    except FileNotFoundError:
        raise NotDeclared from None
    except proof_local.Invalid as exc:
        raise Failed(f"the declaration is invalid: {exc}") from None
    try:
        found = proof_local.released(repo_root, declaration)
    except (proof_local.Invalid, RuntimeError, OSError) as exc:
        raise Failed(f"the released commit could not be read: {exc}") from None
    if found is None:
        raise Failed(f"surface {declaration.surface} has no release yet")
    values.update(commit=found.sha, tag=found.tag)

    pin = playwright_pin(requirements)
    if not pin:
        raise Failed("requirements-dev.txt pins no playwright")

    _add_worktree(repo_root, worktree, found.sha)
    _install(runner, python3, venv, pin)
    values["python"] = str(venv) + "/bin/python"
    _serve(declaration, worktree, pid_file, poll_seconds)
    values.update(status="up", url=_origin(declaration.ready_url),
                  pid_file=str(pid_file))
    return (f"{SUMMARY}: up — {found.surface} at {found.sha[:7]} ({found.tag}) "
            f"serving {declaration.ready_url}")


def prepare(*, github_output, summary, repo_root=".", declaration=None,
            runner=None, python3=None, venv=None, worktree=None, pid_file=None,
            requirements=REQUIREMENTS, poll_seconds=POLL_SECONDS) -> dict:
    """Stand the released commit up locally with a browser beside it, and
    write the seven output keys and one summary line. Never raises for a run
    it cannot stand up — that is `status=failed` with a one-line note."""
    repo_root = Path(repo_root)
    declaration_path = Path(declaration) if declaration else repo_root / proof_local.DECLARATION
    values = dict.fromkeys(OUTPUT_KEYS, "")
    try:
        line = _stand_up(
            values, repo_root, declaration_path, runner or run_install,
            python3 or sys.executable, Path(venv or runner_path(PROOF_VENV_DEFAULT)),
            Path(worktree or runner_path(WORKTREE_DEFAULT)),
            Path(pid_file or _env_path("PROOF_LOCAL_PID_FILE", PID_FILE_DEFAULT)),
            requirements, poll_seconds)
    except NotDeclared:
        values.update(status="none", note=f"no {proof_local.DECLARATION}")
        line = f"{SUMMARY}: none, no {proof_local.DECLARATION}"
    except Exception as exc:  # noqa: BLE001 — every failure is a status
        note = str(exc) if isinstance(exc, Failed) else f"prepare failed: {exc}"
        values.update(status="failed", note=_one_line(note))
        line = f"{SUMMARY}: failed — {values['note']}"

    with open(github_output, "a", encoding="utf-8") as fh:
        fh.writelines(f"{key}={_one_line(values[key])}\n" for key in OUTPUT_KEYS)
    line = _one_line(line)
    with open(summary, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)
    return values


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

def _shred(path: Path, out) -> None:
    """Overwrite `path` in place, then unlink it."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        out(f"{SUMMARY}: no file at {path}")
        return
    try:
        with open(path, "r+b") as fh:
            fh.write(b"\0" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        out(f"{SUMMARY}: could not overwrite {path}: {exc.strerror or exc}")
    try:
        path.unlink()
    except FileNotFoundError:
        out(f"{SUMMARY}: no file at {path}")
        return
    except OSError as exc:
        out(f"{SUMMARY}: could not remove {path}: {exc.strerror or exc}")
        return
    out(f"{SUMMARY}: removed {path}")


def stop(*, pid_file=None, login_file=None, browser_state=None, out=print) -> int:
    """End the local run and leave no credential behind. Always 0."""
    pid_file = Path(pid_file or _env_path("PROOF_LOCAL_PID_FILE", PID_FILE_DEFAULT))
    login_file = Path(login_file or _env_path("PROOF_LOGIN_FILE", LOGIN_FILE_DEFAULT))
    browser_state = Path(browser_state
                         or _env_path("PROOF_BROWSER_STATE", BROWSER_STATE_DEFAULT))

    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        out(f"{SUMMARY}: no pid file at {pid_file} — nothing to stop")
    except OSError as exc:
        out(f"{SUMMARY}: could not read {pid_file}: {exc.strerror or exc}")
    else:
        if not raw.isdigit() or int(raw) <= 1 or int(raw) == os.getpgrp():
            out(f"{SUMMARY}: {pid_file} names no process ({raw[:40]!r})")
        else:
            pgid = int(raw)
            ended = _end_group(pgid)
            if ended == "gone":
                out(f"{SUMMARY}: process group {pgid} was already gone")
            else:
                out(f"{SUMMARY}: {ended} process group {pgid}")
        pid_file.unlink(missing_ok=True)

    log = log_path(pid_file)
    try:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL:]
    except FileNotFoundError:
        out(f"{SUMMARY}: no start log at {log}")
    except OSError as exc:
        out(f"{SUMMARY}: could not read {log}: {exc.strerror or exc}")
    else:
        out(f"{SUMMARY}: the last {len(tail)} line(s) of {log}:")
        for line in tail:
            out(line)

    for path in (browser_state, login_file):
        _shred(path, out)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="stand the released commit up locally")
    prep.add_argument("--github-output", required=True)
    prep.add_argument("--summary", required=True)
    prep.add_argument("--repo-root", default=".")
    prep.add_argument("--declaration", default=None,
                      help=f"default: <repo-root>/{proof_local.DECLARATION}")

    sub.add_parser("stop", help="end the local run and delete the browser's files")

    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(github_output=args.github_output, summary=args.summary,
                repo_root=args.repo_root, declaration=args.declaration)
        return 0
    return stop()


if __name__ == "__main__":
    sys.exit(main())
