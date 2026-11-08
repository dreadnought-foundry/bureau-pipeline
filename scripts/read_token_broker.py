#!/usr/bin/env python3
"""A local token broker for a model session's door reads, and the one way
that session's read lines reach the job log (DRE-9168).

## Why a broker

A model session (claude-code-action) is not handed the runner's OIDC request
variables, `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN`,
so every door read it made fell back with `reason=no-token`. Handing them over
is not the fix: with them the session could mint a token for any audience, an
AWS role's included, and it reads card text the pipeline treats as untrusted.

So the job starts this broker before the model step:

    python3 scripts/read_token_broker.py start --state-file <path>

It returns once the broker is listening, having written `<path>` as JSON
`{"url": "http://127.0.0.1:<port>/token", "secret": "<hex>", "pid": <pid>}`.
The broker is a process bound to 127.0.0.1 that holds the request variables
and mints only door tokens: `GET /token` carrying header
`X-Bureau-Broker-Secret: <secret>` answers 200 and `{"value": "<jwt>"}`, the
runner's own response shape, minted for `BUREAU_READ_AUDIENCE` (default
`bureau_read.DEFAULT_AUDIENCE`), one fresh token per request, so the door's
one-claim-per-`jti` rule still holds. A wrong or missing secret, any other
path or method, or any query at all — an audience parameter among them — gets
403 and mints nothing. The variables reach it on its stdin, never its
environment, so they are not in `/proc/<pid>/environ` for the session to
read. `start` exits non-zero with one line naming why when either variable is
absent. The broker serves until

    python3 scripts/read_token_broker.py stop --state-file <path>

The client side is `bureau_read._mint`, which asks the broker only when the
runner's own variables are absent and both `BUREAU_READ_TOKEN_BROKER` and
`BUREAU_READ_TOKEN_BROKER_SECRET` are set.

## The lines file, and why it is filtered

A session's stderr lands in its transcript, not the job log, so
`scripts/check_linear_budget.py` never counted its `read-fallback:` lines.
`BUREAU_READ_LINES_FILE=<path>` makes `bureau_read` and `linear_ops` copy
every read line to a file as well, and the workflow prints the file into the
job log after the model step.

That file is written where the session can write: the runner's temp
directory. Printed raw, a line the session wrote could forge a
`read-fallback:` or `linear-door:` line for another workflow's reader, which
the budget check and the proof would count as real, or open with `::` and be
run as a workflow command. So the file reaches the log only through

    python3 scripts/read_token_broker.py print-lines --file <path>

which prints a line unchanged only when all three hold, and drops every
other line:

1. It opens, at its first character, with one of `bureau_read.LINES_FILE_TAGS`
   — the tags the file is written with. So no printed line opens with `::`.
2. It holds no control character other than a tab — nor a Unicode line or
   paragraph separator — so one line in the file is one line in the log. A
   line that is not UTF-8 is dropped too.
3. Every `reader=` it carries reads `reader="<workflow> / <job> / …`, the
   workflow and job read from the print step's own `GITHUB_WORKFLOW` and
   `GITHUB_JOB`. So a line can speak only for the job that prints it, never
   for Hygiene or Linear Sync.

After the lines it prints `agent read lines: kept <k>, dropped <d>`; a missing
file prints `agent read lines: no file at <path>`. Both open with
`SUMMARY_TAG`, and it exits 0 whatever the file holds.

What the filter cannot do: a line that passes all three is printed whether a
process or the session wrote it. So the planning job's read lines are that
job's own report about itself — never evidence about any other job.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402

#: The tag every summary line `print-lines` prints opens with, declared under
#: `log_lines` in `config/proof-reach.json`.
SUMMARY_TAG = "agent read lines:"
#: The tag of the broker's own `start` and `stop` lines.
BROKER_LINE = "read-token-broker:"
TOKEN_PATH = "/token"
OIDC_URL_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL"
OIDC_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
#: How long `start` waits for the broker to listen, and `stop` for it to end.
START_TIMEOUT = 10.0
STOP_TIMEOUT = 5.0
_SERVE = "_serve"
_LINE_BREAKS = frozenset("  ")


# ── The broker ──────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    server_version = "bureau-read-token-broker"
    sys_version = ""

    def log_message(self, *_args):  # quiet: its stderr is /dev/null anyway
        pass

    def __getattr__(self, name):
        # Every method but GET — POST, PUT, one nobody has named — is a 403,
        # never the 501 the base class answers for a method it lacks.
        if name.startswith("do_"):
            return self._refuse
        raise AttributeError(name)

    def do_GET(self):  # noqa: N802
        server = self.server
        given = (self.headers.get(bureau_read.BROKER_SECRET_HEADER) or "").encode()
        # The exact path, no query: an audience parameter is never read.
        if self.path != TOKEN_PATH or not hmac.compare_digest(given, server.secret.encode()):
            self._refuse()
            return
        try:
            value = bureau_read.mint_from_runner(server.request_url, server.request_token,
                                                 server.audience)
        except bureau_read.ReadUnknown as e:
            self._send(502, {"message": e.detail})
            return
        self._send(200, {"value": value})

    def _refuse(self):
        self._send(403, {"message": "refused"})

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the client gave up
            pass


def make_server(*, request_url: str, request_token: str, audience: str,
                secret: str) -> ThreadingHTTPServer:
    """The broker's server, listening on 127.0.0.1 at a free port."""
    server = ThreadingHTTPServer((bureau_read.BROKER_HOST, 0), _Handler)
    server.daemon_threads = True
    server.request_url = request_url
    server.request_token = request_token
    server.audience = audience
    server.secret = secret
    return server


def _write_state(path: str, state: dict) -> None:
    """Written whole or not at all, readable by this user only: `start`
    polls for it, and a half-written file must never parse."""
    partial = f"{path}.{os.getpid()}.partial"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
    os.replace(partial, path)


def _serve(state_file: str) -> int:
    """The broker process itself. Its configuration arrives on stdin."""
    config = json.loads(sys.stdin.read())
    server = make_server(request_url=config["request_url"],
                         request_token=config["request_token"],
                         audience=config["audience"], secret=secrets.token_hex(32))
    host, port = server.server_address[:2]
    _write_state(state_file, {"url": f"http://{host}:{port}{TOKEN_PATH}",
                              "secret": server.secret, "pid": os.getpid()})
    server.serve_forever(poll_interval=0.2)
    return 0


def _read_state(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _listening(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    try:
        socket.create_connection((parts.hostname, parts.port), timeout=1).close()
    except OSError:
        return False
    return True


def start(state_file: str) -> int:
    """Start the broker; return once it listens. Non-zero, with one line,
    when it cannot be started."""
    url = os.environ.get(OIDC_URL_ENV)
    request_token = os.environ.get(OIDC_TOKEN_ENV)
    for name, value in ((OIDC_URL_ENV, url), (OIDC_TOKEN_ENV, request_token)):
        if not value:
            print(f"{BROKER_LINE} not started — {name} is not set (the job does not "
                  "grant id-token: write)", file=sys.stderr)
            return 2
    try:
        os.unlink(state_file)
    except FileNotFoundError:
        pass
    env = {k: v for k, v in os.environ.items() if k not in (OIDC_URL_ENV, OIDC_TOKEN_ENV)}
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), _SERVE, "--state-file", state_file],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True, close_fds=True)
    proc.stdin.write(json.dumps({"request_url": url, "request_token": request_token,
                                 "audience": bureau_read.door_audience()}).encode())
    proc.stdin.close()
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            print(f"{BROKER_LINE} not started — the broker exited with {code} before "
                  "it listened", file=sys.stderr)
            return 1
        state = _read_state(state_file)
        if state and state.get("pid") == proc.pid and _listening(state.get("url", "")):
            print(f"{BROKER_LINE} listening on {state['url']} (pid {proc.pid})")
            return 0
        time.sleep(0.05)
    proc.kill()
    print(f"{BROKER_LINE} not started — the broker did not listen within "
          f"{START_TIMEOUT:g}s", file=sys.stderr)
    return 1


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"  # a zombie is gone
    except (OSError, IndexError):
        return True


def _is_broker(pid: int) -> bool:
    """Is `pid` a broker this script started? The state file sits where the
    session can write, so its pid is checked before anything is signaled.
    Where `/proc` cannot say, it is taken as written."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = fh.read().split(b"\0")
    except FileNotFoundError:
        return not os.path.isdir("/proc")
    except OSError:
        return False
    return (any(os.path.basename(a) == b"read_token_broker.py" for a in argv)
            and _SERVE.encode() in argv)


def stop(state_file: str) -> int:
    """End the broker the state file names, and remove the file. Exit 0
    whether or not it was still running: a job's cleanup never goes red."""
    state = _read_state(state_file)
    pid = state.get("pid") if state else None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        print(f"{BROKER_LINE} nothing to stop — no broker named in {state_file}")
        return 0
    if _alive(pid) and _is_broker(pid):
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
        print(f"{BROKER_LINE} stopped (pid {pid})")
    else:
        print(f"{BROKER_LINE} pid {pid} is not a running broker — nothing signaled")
    try:
        os.unlink(state_file)
    except OSError:
        pass
    return 0


# ── print-lines ─────────────────────────────────────────────────────────────


def reader_prefix(env=None) -> str:
    """`<workflow> / <job> / `, spelled as `bureau_read._reader` spells it."""
    env = os.environ if env is None else env
    return f"{env.get('GITHUB_WORKFLOW') or 'local'} / {env.get('GITHUB_JOB') or '-'} / "


def keep(raw: bytes, prefix: str) -> bool:
    """Whether one line of the file is printed (the three rules above)."""
    try:
        line = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not line.startswith(bureau_read.LINES_FILE_TAGS):
        return False
    if any(ch != "\t" and (unicodedata.category(ch) == "Cc" or ch in _LINE_BREAKS)
           for ch in line):
        return False
    at = line.find("reader=")
    while at != -1:
        if not line.startswith(f'"{prefix}', at + len("reader=")):
            return False
        at = line.find("reader=", at + 1)
    return True


def print_lines(path: str, out=None, env=None) -> int:
    """Print the lines file's kept lines, byte for byte and in order, then
    the summary. Exit 0 whatever the file holds."""
    out = sys.stdout.buffer if out is None else out
    prefix = reader_prefix(env)

    def say(text: str) -> None:
        out.write(f"{SUMMARY_TAG} {text}\n".encode("utf-8", "replace"))
        out.flush()

    try:
        # Non-blocking, so a FIFO put where the file was cannot hang the step.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        say(f"no file at {path}")
        return 0
    except OSError as e:
        say(f"could not read {path} ({type(e).__name__})")
        return 0
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        os.close(fd)
        say(f"could not read {path} (not a regular file)")
        return 0
    with os.fdopen(fd, "rb") as fh:
        kept = dropped = 0
        try:
            for raw in fh:
                raw = raw[:-1] if raw.endswith(b"\n") else raw
                if keep(raw, prefix):
                    out.write(raw + b"\n")
                    kept += 1
                else:
                    dropped += 1
        except OSError as e:
            out.flush()
            say(f"kept {kept}, dropped {dropped} — reading stopped ({type(e).__name__})")
            return 0
    out.flush()
    say(f"kept {kept}, dropped {dropped}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "stop", _SERVE):
        sub.add_parser(name).add_argument("--state-file", required=True)
    sub.add_parser("print-lines").add_argument("--file", required=True)
    args = parser.parse_args(argv)
    if args.command == "start":
        return start(args.state_file)
    if args.command == "stop":
        return stop(args.state_file)
    if args.command == _SERVE:
        return _serve(args.state_file)
    return print_lines(args.file)


if __name__ == "__main__":
    sys.exit(main())
