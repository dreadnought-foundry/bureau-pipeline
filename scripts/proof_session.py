#!/usr/bin/env python3
"""The proof run's browser, second half: `login` and `screenshot` (DRE-6047).

The two commands the AGENT runs during a proof, on the venv and the local run
`scripts/proof_browser.py prepare` (DRE-6025) stood up before it. Both read the
same `PROOF_*` environment `proof-task.yml` sets (DRE-6027);
`docs/proof-local-run.md` is the human contract.

`login` runs the repo's declared `login.command` ONCE per run, from the root of
the default-branch checkout — the directory this module is invoked from, never
the released worktree — with `PROOF_LOGIN_FILE` added to the environment. Both
of the command's streams are captured in memory and written nowhere. The line
it quotes is the last non-empty line of the command's standard output, on
success and on refusal alike; standard error is never quoted. Every call is a
counted hosted sign-in, so a marker beside the login file refuses a second.

`screenshot` re-executes itself under `$PROOF_PYTHON` (the venv's interpreter)
before it imports Playwright, so the documented command is one command
wherever it is written. It opens `PROOF_LOCAL_URL` plus the declared page in
chromium; with `--signed-in` it loads the saved storage state, or signs the
browser in from the login file once, saves the state, and overwrites and
deletes the login file. It never signs in on its own account: no state and no
login file is an exit 1 that opens no page. Each request the page made is
recorded as `<method> <scheme>://<host>[:<port>]<path> <status>` — no query
string, fragment, header or body — in `$PROOF_REQUESTS_DIR`, never beside the
PNG.

The module runs on the runner's own `python3` up to the re-execution and
imports only the standard library and its sibling pipeline modules at module
level; `playwright.sync_api` is imported inside `screenshot`'s browser half.

CLI:
    proof_session.py login      [--declaration PATH]
    proof_session.py screenshot --page KEY --out PNG [--signed-in]
                                [--wait-for CSS] [--declaration PATH]

Both exit 0 when they did what they say and 1 otherwise, with one line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import proof_browser  # noqa: E402
import proof_local  # noqa: E402

LOGIN = "proof-login"
SCREENSHOT = "proof-screenshot"

#: The keys the login file's contract names (DRE-6024), and for the `session`
#: kind of `login` (DRE-6536), which carries `session` in place of the
#: username and password.
LOGIN_FILE_KEYS = ("username", "password", "identity", "ration")
SESSION_FILE_KEYS = ("identity", "ration", "session")
LOGIN_FILE_MODE = 0o600

#: The browser's interpreter, where `prepare` installs it.
PROOF_PYTHON_DEFAULT = proof_browser.PROOF_VENV_DEFAULT + "/bin/python"
REQUESTS_DIR_DEFAULT = "$RUNNER_TEMP"
#: Set on the re-executed process, so an interpreter that reports itself
#: under another path is not re-executed forever.
REEXECUTED = "PROOF_SESSION_REEXECUTED"

#: Beside the login file: `login` already ran in this run.
MARKER_SUFFIX = ".ran"

#: How long a page may keep fetching after `load` before it is shot anyway.
SETTLE_MS = 5000

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class Refused(Exception):
    """The command cannot go on; the message is the one line it prints."""


# ---------------------------------------------------------------------------
# Paths and lines
# ---------------------------------------------------------------------------

def _env_path(name: str, default: str) -> Path:
    return proof_browser.runner_path(os.environ.get(name) or default)


def login_file() -> Path:
    return _env_path("PROOF_LOGIN_FILE", proof_browser.LOGIN_FILE_DEFAULT)


def browser_state() -> Path:
    return _env_path("PROOF_BROWSER_STATE", proof_browser.BROWSER_STATE_DEFAULT)


def requests_dir() -> Path:
    return _env_path("PROOF_REQUESTS_DIR", REQUESTS_DIR_DEFAULT)


def proof_python() -> Path:
    return _env_path("PROOF_PYTHON", PROOF_PYTHON_DEFAULT)


def marker(path: Path) -> Path:
    return path.with_name(path.name + MARKER_SUFFIX)


def _one_line(text) -> str:
    return _CONTROL.sub(" ", str(text)).strip()


def last_line(output: str) -> str:
    """The last non-empty line of `output`, on one line."""
    lines = [line for line in str(output).splitlines() if line.strip()]
    return _one_line(lines[-1]) if lines else ""


def _declaration(path):
    try:
        return proof_local.load(path)
    except FileNotFoundError:
        raise Refused(f"no {path} — this repo declares no local run") from None
    except proof_local.Invalid as exc:
        raise Refused(f"{path} is invalid: {exc}") from None


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def _check_login_file(path: Path, keys=LOGIN_FILE_KEYS) -> str:
    """The file's `identity`, or `Refused` naming what is wrong — never its
    path or anything it holds. `keys` are the keys the declared kind's file
    must carry."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        raise Refused("login.command wrote no login file") from None
    if mode != LOGIN_FILE_MODE:
        raise Refused(f"the login file is mode {mode:o}, not 600")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refused("the login file is not readable JSON") from None
    if not isinstance(data, dict):
        raise Refused("the login file is not one JSON object")
    missing = [key for key in keys if key not in data]
    if missing:
        raise Refused(f"the login file is missing {', '.join(missing)}")
    if "session" in keys:
        session = data["session"]
        if not isinstance(session, dict) or not all(
                isinstance(value, str) for value in session.values()):
            raise Refused("the login file's session is not an object of strings")
    identity = data["identity"]
    if not isinstance(identity, str) or not _one_line(identity):
        raise Refused("the login file's identity is empty")
    return _one_line(identity)


def login(*, declaration=proof_local.DECLARATION, out=print) -> int:
    """Run `login.command` once and quote its status line."""
    try:
        found = _declaration(declaration)
    except Refused as exc:
        out(f"{LOGIN}: {exc}")
        return 1
    if not found.login:
        out(f"{LOGIN}: {declaration} declares no login")
        return 1

    path = login_file()
    ran = marker(path)
    if ran.exists():
        out(f"{LOGIN}: already ran once in this run — every call is a counted "
            f"hosted sign-in, so it is never run twice")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written BEFORE the command runs: a command that dies half way has still
    # spent a sign-in.
    ran.write_text("login.command ran in this run\n", encoding="utf-8")

    env = dict(os.environ, PROOF_LOGIN_FILE=str(path))
    try:
        done = subprocess.run(found.login["command"], shell=True, cwd=os.getcwd(),
                              env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace")
    except OSError as exc:
        out(f"{LOGIN}: login.command could not run: {exc.strerror or exc}")
        return 1
    line = last_line(done.stdout)
    if done.returncode != 0:
        out(f"{LOGIN}: refused — "
            + (line or f"login.command exited {done.returncode} and printed no line"))
        return 1
    keys = SESSION_FILE_KEYS if "session" in found.login else LOGIN_FILE_KEYS
    try:
        identity = _check_login_file(path, keys)
    except Refused as exc:
        out(f"{LOGIN}: {exc} — {line}" if line else f"{LOGIN}: {exc}")
        return 1
    if not line:
        out(f"{LOGIN}: login.command printed no status line")
        return 1
    out(f"{LOGIN}: {identity} — {line}")
    return 0


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------

def reexec(argv) -> None:
    """Replace this process with `$PROOF_PYTHON` running this module with the
    same arguments, unless this already is that interpreter. Raises `Refused`
    when there is no interpreter there to run."""
    python = proof_python()
    if not (python.is_file() and os.access(python, os.X_OK)):
        raise Refused(f"no browser interpreter at {python} — prepare did not "
                      f"install one")
    here = os.path.normpath(os.path.abspath(sys.executable))
    if here == os.path.normpath(os.path.abspath(python)) or os.environ.get(REEXECUTED):
        return
    sys.stdout.flush()
    sys.stderr.flush()
    env = dict(os.environ, **{REEXECUTED: str(python)})
    os.execve(str(python), [str(python), os.path.abspath(__file__), *argv], env)


def request_row(method: str, url: str, status) -> str:
    """`<method> <scheme>://<host>[:<port>]<path> <status>` — the scheme, the
    host and the path, and nothing else of the URL."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        # A data: or blob: URL's "path" is its content.
        return f"{_one_line(method)} {scheme}: {status}"
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None:
        host += f":{port}"
    return f"{_one_line(method)} {scheme}://{host}{_one_line(parts.path)} {status}"


def _sha7() -> str:
    return (os.environ.get("PROOF_LOCAL_COMMIT") or "").strip()[:7] or "an unknown commit"


def _read_login_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refused(f"the login file at {path} is not readable JSON — run "
                      f"proof_session.py login first") from None
    if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) for key in ("username", "password")):
        raise Refused(f"the login file at {path} has no username and password")
    return data


def _same_page(current: str, target: str) -> bool:
    a, b = urllib.parse.urlsplit(current), urllib.parse.urlsplit(target)
    return (a.scheme, a.netloc, a.path) == (b.scheme, b.netloc, b.path)


def _settle(page, wait_for) -> None:
    if wait_for:
        page.wait_for_selector(wait_for, state="visible")
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:  # noqa: BLE001 — a page that keeps polling is shot anyway
        pass


def _shoot(*, url, out_png, signed_in, wait_for, form, state, credentials,
           login_path, sidecar) -> None:
    """The browser half: Playwright is imported here, after `reexec`."""
    from playwright.sync_api import sync_playwright

    seen: list = []
    statuses: dict = {}

    def on_request(request):
        seen.append(request)

    def on_response(response):
        statuses[id(response.request)] = response.status

    def on_failed(request):
        statuses.setdefault(id(request), "failed")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            options = {"storage_state": str(state)} if signed_in and state.exists() else {}
            context = browser.new_context(**options)
            context.on("request", on_request)
            context.on("response", on_response)
            context.on("requestfailed", on_failed)
            page = context.new_page()
            page.goto(url, wait_until="load")
            if credentials is not None:
                page.wait_for_selector(form["username"], state="visible")
                page.fill(form["username"], credentials["username"])
                page.fill(form["password"], credentials["password"])
                with page.expect_navigation(wait_until="load"):
                    page.click(form["submit"])
                credentials.clear()
                _settle(page, None)
                state.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state))
                os.chmod(state, LOGIN_FILE_MODE)
                # The password is on disk only between `login` and here.
                proof_browser._shred(login_path, lambda _line: None)
                if not _same_page(page.url, url):
                    page.goto(url, wait_until="load")
            _settle(page, wait_for)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_png), full_page=True)
            if signed_in:
                context.storage_state(path=str(state))
                os.chmod(state, LOGIN_FILE_MODE)
            rows = [request_row(r.method, r.url, statuses.get(id(r), "-")) for r in seen]
        finally:
            browser.close()
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("".join(row + "\n" for row in rows), encoding="utf-8")


def screenshot(argv, *, page_key, out_png, signed_in=False, wait_for=None,
               declaration=proof_local.DECLARATION, out=print) -> int:
    """Shoot one declared page; `argv` is this command's own arguments, for
    the re-execution under `$PROOF_PYTHON`."""
    try:
        reexec(argv)
        found = _declaration(declaration)
        if page_key not in found.pages:
            raise Refused(f"no page {page_key} in {declaration} — the declared "
                          f"pages are {', '.join(found.pages)}")
        base = (os.environ.get("PROOF_LOCAL_URL") or "").rstrip("/")
        if not base:
            status = os.environ.get("PROOF_LOCAL_STATUS") or "unset"
            note = _one_line(os.environ.get("PROOF_LOCAL_NOTE") or "")
            raise Refused(f"no local run to open — PROOF_LOCAL_URL is empty "
                          f"(status {status}{', ' + note if note else ''})")
        url = base + found.pages[page_key]

        state, login_path, credentials, form = browser_state(), login_file(), None, None
        if signed_in and not state.exists():
            if not login_path.exists():
                raise Refused(f"no login file at {login_path} — run "
                              f"proof_session.py login first")
            if not found.login:
                raise Refused(f"{declaration} declares no login form to fill")
            if "session" in found.login:
                # The planting step is the follow-up to DRE-6536.
                raise Refused(f"{declaration} declares the `session` kind of "
                              f"login, and planting a session in the browser "
                              f"is not built yet — no page was opened")
            form = found.login["form"]
            credentials = _read_login_file(login_path)
    except Refused as exc:
        out(f"{SCREENSHOT}: {exc}")
        return 1

    out_png = Path(out_png).absolute()
    sidecar = requests_dir() / f"proof-requests-{page_key}.txt"
    try:
        _shoot(url=url, out_png=out_png, signed_in=signed_in,
               wait_for=wait_for, form=form, state=state, credentials=credentials,
               login_path=login_path, sidecar=sidecar)
    except ImportError as exc:
        out(f"{SCREENSHOT}: Playwright is not importable under {sys.executable} "
            f"({exc}) — prepare did not install the browser")
        return 1
    except Exception as exc:  # noqa: BLE001 — Playwright's errors are one line here
        first = _one_line((str(exc).strip().splitlines() or [type(exc).__name__])[0])
        out(f"{SCREENSHOT}: {page_key} — {url} could not be shot: {first}")
        return 1
    out(f"{SCREENSHOT}: {page_key} — {url} at {_sha7()}, {out_png}, requests {sidecar}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    log = sub.add_parser("login", help="run the declared login.command once")
    log.add_argument("--declaration", default=proof_local.DECLARATION)

    shot = sub.add_parser("screenshot", help="shoot one declared page")
    shot.add_argument("--page", required=True, help="a key of the declared pages")
    shot.add_argument("--out", required=True, help="the PNG to write")
    shot.add_argument("--signed-in", action="store_true",
                      help="sign the browser in first (after `login`)")
    shot.add_argument("--wait-for", default=None,
                      help="a CSS selector to wait for before the shot")
    shot.add_argument("--declaration", default=proof_local.DECLARATION)

    args = parser.parse_args(argv)
    if args.command == "login":
        return login(declaration=args.declaration)
    return screenshot(argv, page_key=args.page, out_png=args.out,
                      signed_in=args.signed_in, wait_for=args.wait_for,
                      declaration=args.declaration)


if __name__ == "__main__":
    sys.exit(main())
