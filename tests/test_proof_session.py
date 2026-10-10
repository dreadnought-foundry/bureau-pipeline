"""The proof run's browser, second half: `login` and `screenshot` (DRE-6047).

`scripts/proof_session.py` holds the two commands the AGENT runs during a
proof, on the venv and the local run `scripts/proof_browser.py prepare`
(DRE-6025) stood up before it. `login` runs the repo's declared
`login.command` once, from the default-branch checkout, and quotes the last
line of its standard output; `screenshot` opens a declared page in chromium,
signs the browser in from the file that command wrote, writes the PNG, and
records the requests the page made with no query string, fragment, header or
body.

The browser tests drive a REAL chromium against a stub app and a stub sign-in
page on 127.0.0.1, served from a thread of this process so every request and
every submit is counted here. There is no skip guard: Playwright is imported
at the top and chromium is launched in a module fixture, so a machine without
the browser fails this file with the install command in the message instead
of passing it vacuously. `tests.yml` installs chromium in the part that runs
this file (`tests/test_proof_session_ci.py`).
"""

from __future__ import annotations

import ast
import http.server
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

INSTALL = ("python3 -m pip install -r requirements-dev.txt && "
           "python3 -m playwright install --with-deps chromium")

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover — the message is the point
    raise ImportError(
        f"tests/test_proof_session.py drives a real chromium and Playwright "
        f"is not importable ({exc}) — install it: {INSTALL}") from exc

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "proof_session.py"

RELEASE = {"surfaces": {"web": {"tag_series": ["web-v*"], "paths": []}}}
PAGES = {"home": "/", "items": "/items"}
FORM = {"username": "input[name=user]", "password": "input[name=pass]",
        "submit": "#go"}

USERNAME = "proof-bot-user-7f3a"
PASSWORD = "pw-never-printed-91c2"
IDENTITY = "proof-bot"
STATUS = "hosted sign-in ration: 1 of 5 spent today (2026-10-10 PT), 4 remaining"
REFUSAL = "hosted sign-in ration: 5 of 5 spent today (2026-10-10 PT), 0 remaining"
NOISE = ("added 212 packages, and audited 213 packages in 3s",
         "found 0 vulnerabilities")
STDERR_LINE = "npm warn deprecated stderr-only-line"
COMMIT = "0123456789abcdef0123456789abcdef01234567"

#: The declared `login.command`'s stub. It records its working directory and
#: the login file it was handed, prints install-style noise to stdout and one
#: line to stderr, then either writes the login file and the status line, or
#: prints the refusal line and exits 3. `argv[2]` picks which.
LOGIN_PY = """\
import json, os, sys
record, mode = sys.argv[1], sys.argv[2]
with open(record, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cwd": os.getcwd(),
                         "login_file": os.environ.get("PROOF_LOGIN_FILE")}) + "\\n")
for line in %(noise)r:
    print(line)
print(%(stderr)r, file=sys.stderr)
if mode == "refuse":
    print(%(refusal)r)
    sys.exit(3)
data = {"username": %(username)r, "password": %(password)r,
        "identity": %(identity)r,
        "ration": {"spent": 1, "cap": 5, "remaining": 4, "date": "2026-10-10"}}
for key in mode.split(",")[1:]:
    data.pop(key)
file_mode = 0o644 if mode.startswith("wide") else 0o600
fd = os.open(os.environ["PROOF_LOGIN_FILE"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
os.chmod(os.environ["PROOF_LOGIN_FILE"], file_mode)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh)
print(%(status)r)
print("")
""" % {"noise": NOISE, "stderr": STDERR_LINE, "refusal": REFUSAL,
       "username": USERNAME, "password": PASSWORD, "identity": IDENTITY,
       "status": STATUS}

#: Stands in for the venv's interpreter: records the argv it was handed.
REEXEC_STUB = """\
#!{python}
import json, sys
with open({record!r}, "w", encoding="utf-8") as fh:
    json.dump(sys.argv, fh)
"""


# ---------------------------------------------------------------------------
# The stub site: an app behind a stub hosted sign-in, on one 127.0.0.1 port
# ---------------------------------------------------------------------------

APP_HTML = b"""<!doctype html><title>stub app</title>
<h1 id="ready">signed in</h1>
<script>
fetch('/api/items?token=secret-123#frag').then(r => r.json()).then(j => {
  const p = document.createElement('p');
  p.id = 'loaded';
  p.textContent = j.items.join(', ');
  document.body.appendChild(p);
});
</script>"""

LOGIN_HTML = b"""<!doctype html><title>stub sign-in</title>
<form method="post" action="/login">
  <input name="user"><input name="pass" type="password">
  <button id="go" type="submit">Sign in</button>
</form>"""


class Site:
    """The stub app and its stub sign-in. `requests` is every request the
    server saw (method and raw path), `submits` every sign-in form posted."""

    def __init__(self):
        self.requests: list = []
        self.submits: list = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, code, body=b"", headers=()):
                self.send_response(code)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _signed_in(self):
                return "session=ok" in (self.headers.get("Cookie") or "")

            def do_GET(self):
                site.requests.append(("GET", self.path))
                path = urllib.parse.urlsplit(self.path).path
                if path in PAGES.values():
                    if self._signed_in():
                        self._send(200, APP_HTML, [("Content-Type", "text/html")])
                    else:
                        self._send(302, headers=[("Location", "/login")])
                elif path == "/login":
                    self._send(200, LOGIN_HTML, [("Content-Type", "text/html")])
                elif path == "/callback":
                    self._send(302, headers=[("Location", "/"),
                                             ("Set-Cookie", "session=ok; Path=/")])
                elif path == "/api/items" and self._signed_in():
                    self._send(200, b'{"items": ["a", "b"]}',
                               [("Content-Type", "application/json")])
                else:
                    self._send(404)

            def do_POST(self):
                site.requests.append(("POST", self.path))
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.submits.append(dict(urllib.parse.parse_qsl(body.decode())))
                self._send(302, headers=[("Location", "/callback?code=abc123&state=xyz")])

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def chromium():
    """A real chromium, launched once for the module. No skip: without the
    browser the file FAILS, naming the command that installs it."""
    try:
        playwright = sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Playwright could not start ({exc}) — install it: {INSTALL}")
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        playwright.stop()
        first = (str(exc).strip().splitlines() or ["no message"])[0]
        pytest.fail(f"chromium could not launch ({first}) — install it: "
                    f"python3 -m playwright install --with-deps chromium")
    yield browser.version
    browser.close()
    playwright.stop()


@pytest.fixture
def site():
    stub = Site()
    yield stub
    stub.close()


# ---------------------------------------------------------------------------
# The layout one proof run sees
# ---------------------------------------------------------------------------

class Run:
    """A default-branch checkout with a declaration, a runner temp directory
    holding the released worktree, and the `PROOF_*` environment."""

    def __init__(self, tmp_path: Path, url: str = "http://127.0.0.1:9"):
        self.tmp = tmp_path
        self.checkout = tmp_path / "checkout"
        self.runner_temp = tmp_path / "runner-temp"
        self.worktree = self.runner_temp / "proof-local-run"
        self.records = tmp_path / "records"
        self.requests_dir = tmp_path / "requests"
        self.shots = tmp_path / "record-dir"
        for path in (self.checkout / ".github" / "bureau", self.worktree,
                     self.records, self.requests_dir, self.shots):
            path.mkdir(parents=True, exist_ok=True)
        self.login_record = self.records / "login-calls.jsonl"
        self.stub = self.records / "login_stub.py"
        self.stub.write_text(LOGIN_PY, encoding="utf-8")
        self.login_file = self.runner_temp / "proof-login.json"
        self.state = self.runner_temp / "proof-browser-state.json"
        self.url = url
        self.declare()

    def declare(self, mode: str = "ok", **overrides) -> None:
        command = (f"{sys.executable} {self.stub} {self.login_record} {mode}")
        data = {"surface": "web", "start": "true",
                "ready_url": f"{self.url}/", "pages": PAGES,
                "login": {"command": command, "form": FORM}}
        data.update(overrides)
        bureau = self.checkout / ".github" / "bureau"
        (bureau / "proof-local.json").write_text(json.dumps(data), encoding="utf-8")
        (bureau / "release.json").write_text(json.dumps(RELEASE), encoding="utf-8")

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PROOF_")}
        env.update(RUNNER_TEMP=str(self.runner_temp),
                   PROOF_LOCAL_URL=self.url, PROOF_LOCAL_COMMIT=COMMIT,
                   PROOF_LOCAL_TAG="web-v1.2.3", PROOF_LOCAL_STATUS="up",
                   PROOF_LOCAL_NOTE="", PROOF_PYTHON=sys.executable,
                   PROOF_LOGIN_FILE=str(self.login_file),
                   PROOF_BROWSER_STATE=str(self.state),
                   PROOF_REQUESTS_DIR=str(self.requests_dir))
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=self.checkout, env=env or self.env(),
                              capture_output=True, text=True, timeout=180)

    def login_calls(self) -> list:
        if not self.login_record.exists():
            return []
        return [json.loads(line) for line in
                self.login_record.read_text(encoding="utf-8").splitlines()]

    def write_login_file(self) -> None:
        fd = os.open(self.login_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"username": USERNAME, "password": PASSWORD,
                       "identity": IDENTITY,
                       "ration": {"spent": 1, "cap": 5, "remaining": 4,
                                  "date": "2026-10-10"}}, fh)

    def screenshot(self, key, *extra, env=None):
        png = self.shots / f"{key}.png"
        done = self.run("screenshot", "--page", key, "--out", str(png), *extra,
                        env=env)
        return done, png, self.requests_dir / f"proof-requests-{key}.txt"


def _files(root: Path) -> set:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def test_login_runs_from_the_checkout_and_quotes_only_the_last_stdout_line(tmp_path):
    run = Run(tmp_path)
    done = run.run("login")

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == f"proof-login: {IDENTITY} — {STATUS}\n"
    assert done.stderr == ""
    said = done.stdout + done.stderr
    for secret in (USERNAME, PASSWORD, str(run.login_file), run.login_file.name,
                   STDERR_LINE, *NOISE):
        assert secret not in said, secret

    calls = run.login_calls()
    assert len(calls) == 1
    assert Path(calls[0]["cwd"]).resolve() == run.checkout.resolve()
    assert Path(calls[0]["cwd"]).resolve() != run.worktree.resolve()
    assert calls[0]["login_file"] == str(run.login_file)

    assert stat.S_IMODE(run.login_file.stat().st_mode) == 0o600
    # Neither stream reaches the disk: under $RUNNER_TEMP there is the login
    # file and the once-per-run marker beside it, and nothing else.
    left = _files(run.runner_temp)
    assert run.login_file in left
    others = left - {run.login_file}
    assert len(others) == 1, others
    (marker,) = others
    assert marker.parent == run.login_file.parent
    for path in others:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in (STATUS, STDERR_LINE, *NOISE, PASSWORD):
            assert line not in text


def test_login_quotes_the_refusal_line_and_exits_1(tmp_path):
    run = Run(tmp_path)
    run.declare(mode="refuse")
    done = run.run("login")

    assert done.returncode == 1
    assert done.stdout == f"proof-login: refused — {REFUSAL}\n"
    for line in (STDERR_LINE, *NOISE):
        assert line not in done.stdout + done.stderr
    assert len(run.login_calls()) == 1


@pytest.mark.parametrize("missing", ["username", "password", "identity", "ration"])
def test_login_exits_1_when_the_file_is_short_a_key(tmp_path, missing):
    run = Run(tmp_path)
    run.declare(mode=f"ok,{missing}")
    done = run.run("login")

    assert done.returncode == 1, done.stdout
    assert missing in done.stdout
    assert not done.stdout.startswith(f"proof-login: {IDENTITY} — ")
    for secret in (USERNAME, PASSWORD, str(run.login_file)):
        assert secret not in done.stdout + done.stderr


def test_login_exits_1_when_the_file_is_not_mode_600(tmp_path):
    run = Run(tmp_path)
    run.declare(mode="wide")
    done = run.run("login")

    assert done.returncode == 1, done.stdout
    assert "600" in done.stdout
    assert not done.stdout.startswith(f"proof-login: {IDENTITY} — ")
    for secret in (USERNAME, PASSWORD, str(run.login_file)):
        assert secret not in done.stdout + done.stderr


def test_login_refuses_a_second_call_in_the_same_run(tmp_path):
    run = Run(tmp_path)
    assert run.run("login").returncode == 0
    run.login_file.unlink()          # even with the file gone, it is spent

    again = run.run("login")
    assert again.returncode == 1
    assert again.stdout.startswith("proof-login: ")
    assert len(run.login_calls()) == 1, "the second call ran the command"
    assert not run.login_file.exists()


# ---------------------------------------------------------------------------
# screenshot — the browser's interpreter
# ---------------------------------------------------------------------------

def test_screenshot_reexecutes_itself_under_proof_python(tmp_path):
    run = Run(tmp_path)
    record = tmp_path / "reexec-argv.json"
    stub = tmp_path / "venv" / "bin" / "python"
    stub.parent.mkdir(parents=True)
    stub.write_text(REEXEC_STUB.format(python=sys.executable, record=str(record)),
                    encoding="utf-8")
    stub.chmod(0o755)
    args = ["screenshot", "--page", "home", "--out", str(run.shots / "home.png"),
            "--signed-in", "--wait-for", "#ready"]

    done = run.run(*args, env=run.env(PROOF_PYTHON=str(stub)))

    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == [
        str(stub), str(SCRIPT), *args]
    assert done.stdout == ""
    assert not (run.shots / "home.png").exists()
    assert _files(run.requests_dir) == set()


@pytest.mark.parametrize("how", ["unset", "not-executable"])
def test_screenshot_without_a_browser_interpreter_exits_1(tmp_path, how):
    run = Run(tmp_path)
    if how == "unset":
        expected = run.runner_temp / "proof-venv" / "bin" / "python"
        env = run.env(PROOF_PYTHON=None)
    else:
        expected = tmp_path / "venv" / "bin" / "python"
        expected.parent.mkdir(parents=True)
        expected.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        expected.chmod(0o644)
        env = run.env(PROOF_PYTHON=str(expected))

    done, png, sidecar = run.screenshot("home", env=env)

    assert done.returncode == 1
    assert done.stdout == (f"proof-screenshot: no browser interpreter at {expected} "
                           f"— prepare did not install one\n")
    assert not png.exists()
    assert not sidecar.exists()


# ---------------------------------------------------------------------------
# screenshot — a real chromium against the stub site
# ---------------------------------------------------------------------------

def test_signed_in_fills_the_form_once_and_the_next_page_reuses_the_session(
        chromium, site, tmp_path):
    run = Run(tmp_path, url=site.url)
    assert run.run("login").returncode == 0
    assert run.login_file.exists()

    first, _, _ = run.screenshot("home", "--signed-in", "--wait-for", "#loaded")
    assert first.returncode == 0, first.stdout + first.stderr
    assert site.submits == [{"user": USERNAME, "pass": PASSWORD}]
    assert not run.login_file.exists(), "the login file outlived the first page"
    assert run.state.exists()

    second, png, _ = run.screenshot("items", "--signed-in", "--wait-for", "#loaded")
    assert second.returncode == 0, second.stdout + second.stderr
    assert png.exists()
    assert len(site.submits) == 1, "the second page signed in again"
    assert ("GET", "/items") in site.requests
    assert len(run.login_calls()) == 1
    for secret in (USERNAME, PASSWORD):
        assert secret not in first.stdout + first.stderr + second.stdout + second.stderr


def test_the_png_is_written_and_the_line_names_it_and_the_sidecar(
        chromium, site, tmp_path):
    run = Run(tmp_path, url=site.url)
    run.write_login_file()

    done, png, sidecar = run.screenshot("home", "--signed-in", "--wait-for", "#loaded")

    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == (f"proof-screenshot: home — {site.url}/ at {COMMIT[:7]}, "
                           f"{png}, requests {sidecar}\n")
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert sidecar.is_file()


def test_the_sidecar_carries_no_query_string_or_fragment(chromium, site, tmp_path):
    run = Run(tmp_path, url=site.url)
    run.write_login_file()

    done, png, sidecar = run.screenshot("home", "--signed-in", "--wait-for", "#loaded")

    assert done.returncode == 0, done.stdout + done.stderr
    # The page really made both requests WITH their query strings.
    assert ("GET", "/api/items?token=secret-123") in site.requests
    assert ("GET", "/callback?code=abc123&state=xyz") in site.requests

    rows = sidecar.read_text(encoding="utf-8").splitlines()
    assert f"GET {site.url}/api/items 200" in rows
    assert f"GET {site.url}/callback 302" in rows
    assert f"POST {site.url}/login 302" in rows
    text = sidecar.read_text(encoding="utf-8")
    for forbidden in ("secret-123", "abc123", "code=", "state=", "?", "#",
                      USERNAME, PASSWORD):
        assert forbidden not in text, forbidden
    # In the order the page made them: the sign-in before the callback before
    # the page's own fetch.
    assert (rows.index(f"POST {site.url}/login 302")
            < rows.index(f"GET {site.url}/callback 302")
            < rows.index(f"GET {site.url}/api/items 200"))

    assert sidecar.parent == run.requests_dir
    assert sidecar.parent != png.parent
    assert not any(p.name.startswith("proof-requests") for p in png.parent.iterdir())


def test_an_unknown_page_key_exits_1_naming_the_declared_keys(chromium, site, tmp_path):
    run = Run(tmp_path, url=site.url)
    run.write_login_file()

    done, png, sidecar = run.screenshot("settings", "--signed-in")

    assert done.returncode == 1
    assert done.stdout.startswith("proof-screenshot: ")
    assert "settings" in done.stdout
    for key in PAGES:
        assert key in done.stdout
    assert not png.exists()
    assert not sidecar.exists()
    assert site.requests == []
    assert run.login_file.exists()


def test_signed_in_with_no_login_file_and_no_state_never_signs_in_by_itself(
        chromium, site, tmp_path):
    run = Run(tmp_path, url=site.url)
    assert not run.login_file.exists() and not run.state.exists()

    done, png, sidecar = run.screenshot("home", "--signed-in")

    assert done.returncode == 1
    assert done.stdout == (f"proof-screenshot: no login file at {run.login_file} "
                           f"— run proof_session.py login first\n")
    assert site.submits == []
    assert site.requests == [], "a page was opened"
    assert run.login_calls() == [], "the declared login.command ran"
    assert not png.exists()
    assert not sidecar.exists()
    assert not run.state.exists()


# ---------------------------------------------------------------------------
# The module's imports
# ---------------------------------------------------------------------------

#: The sibling pipeline modules the session may import at module level. Both
#: import only the standard library and each other's siblings, and both run on
#: the runner's own python3.
SIBLINGS = {"proof_local", "proof_browser"}


def _module_level_imports(tree) -> set:
    found = set()

    def visit(nodes):
        for node in nodes:
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
            elif isinstance(node, (ast.If, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, []))
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)

    visit(tree.body)
    return found


def test_the_module_imports_playwright_only_inside_screenshot_after_the_reexec():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top = _module_level_imports(tree)
    assert "playwright" not in top
    foreign = {name for name in top - {"__future__"}
               if name not in sys.stdlib_module_names and name not in SIBLINGS}
    assert not foreign, f"module-level imports beyond the standard library: {foreign}"

    holders = [fn for fn in ast.walk(tree)
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(n, ast.ImportFrom) and n.module == "playwright.sync_api"
                       for n in ast.walk(fn))]
    assert holders, "no function imports playwright.sync_api"

    # The re-execution check comes first: `screenshot` calls it before it
    # reaches the function that imports Playwright.
    screenshot = next(fn for fn in ast.walk(tree)
                      if isinstance(fn, ast.FunctionDef) and fn.name == "screenshot")
    calls = [n for n in ast.walk(screenshot) if isinstance(n, ast.Call)]
    names = [getattr(c.func, "id", getattr(c.func, "attr", "")) for c in calls]
    assert "reexec" in names, "screenshot never checks its interpreter"
    first_reexec = min(c.lineno for c, n in zip(calls, names) if n == "reexec")
    imports = [n.lineno for fn in holders for n in ast.walk(fn)
               if isinstance(n, ast.ImportFrom) and n.module == "playwright.sync_api"]
    holder_calls = [c.lineno for c, n in zip(calls, names)
                    if n in {fn.name for fn in holders}]
    assert all(first_reexec < line for line in holder_calls + [
        i for i in imports if screenshot.lineno <= i <= screenshot.end_lineno])
