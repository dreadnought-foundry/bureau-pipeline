"""An agent session reads through the read door via a local token broker,
and its read lines reach the run log (DRE-9168).

A model session is never handed the runner's OIDC request variables: with
them it could mint a token for any audience, an AWS role's included. So the
job starts `scripts/read_token_broker.py` before the model step, a process on
127.0.0.1 that holds the variables and mints door tokens only, one fresh
token per request. `bureau_read._mint` asks it only when the runner's own
variables are absent.

The session's read lines go to its transcript, not the job log, so
`BUREAU_READ_LINES_FILE` copies them to a file, and `print-lines` is the one
way that file reaches the log: filtered, because the session can write it.

No test reaches GitHub, Linear or the console: the OIDC endpoint and the door
are `tests/bureau_read_fakes.py`, on 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
BROKER = SCRIPTS / "read_token_broker.py"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
import proof_reach  # noqa: E402
import read_token_broker  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

AUDIENCE = "https://door.test/pipeline-read"
OIDC_ENV = ("ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
BROKER_ENV = ("BUREAU_READ_TOKEN_BROKER", "BUREAU_READ_TOKEN_BROKER_SECRET")


def _clean_env(**extra) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **extra)
    for name in (*OIDC_ENV, *BROKER_ENV, "BUREAU_READ", "BUREAU_READ_URL",
                 "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF", "BUREAU_READ_LINES_FILE",
                 "GITHUB_WORKFLOW", "GITHUB_JOB", "GITHUB_EVENT_NAME",
                 "LINEAR_API_KEY_FALLBACK", "LINEAR_KEY_HOME", "BUREAU_LINEAR_READ_ONLY"):
        if name not in extra:
            env.pop(name, None)
    return env


def _broker(*args, env=None, timeout=30) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BROKER), *args], capture_output=True,
                          text=True, env=env, timeout=timeout)


@pytest.fixture(autouse=True)
def _fresh_client(monkeypatch):
    for name in (*OIDC_ENV, *BROKER_ENV, "BUREAU_READ", "BUREAU_READ_URL",
                 "BUREAU_READ_AUDIENCE", "BUREAU_PIPELINE_REF", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "BUREAU_READ_LINES_FILE"):
        monkeypatch.delenv(name, raising=False)
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


@pytest.fixture(scope="module")
def issuer():
    with FakeIssuer() as fake:
        yield fake


@pytest.fixture(scope="module")
def broker(issuer, tmp_path_factory):
    """The broker as the workflow starts it: `start`, then `stop`."""
    state_file = tmp_path_factory.mktemp("broker") / "broker.json"
    env = _clean_env(ACTIONS_ID_TOKEN_REQUEST_URL=issuer.token_url,
                     ACTIONS_ID_TOKEN_REQUEST_TOKEN=issuer.request_token,
                     BUREAU_READ_AUDIENCE=AUDIENCE)
    started = _broker("start", "--state-file", str(state_file), env=env)
    assert started.returncode == 0, started.stdout + started.stderr
    state = json.loads(state_file.read_text())
    try:
        yield state
    finally:
        stopped = _broker("stop", "--state-file", str(state_file), env=_clean_env())
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def _point_at(monkeypatch, state):
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER", state["url"])
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER_SECRET", state["secret"])


def _ask(url: str, *, method: str = "GET", headers: dict | None = None) -> tuple[int, bytes]:
    import http.client
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request(method, target or "/", headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# ── the broker's state file and its start ───────────────────────────────────


def test_start_writes_the_state_file_and_the_broker_is_listening(broker):
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/token", broker["url"]), broker
    assert re.fullmatch(r"[0-9a-f]{32,}", broker["secret"]), broker
    assert isinstance(broker["pid"], int)
    os.kill(broker["pid"], 0)  # alive


@pytest.mark.parametrize("missing", OIDC_ENV)
def test_start_refuses_without_the_runners_request_variables(issuer, tmp_path, missing):
    env = _clean_env(ACTIONS_ID_TOKEN_REQUEST_URL=issuer.token_url,
                     ACTIONS_ID_TOKEN_REQUEST_TOKEN=issuer.request_token)
    env.pop(missing)
    state_file = tmp_path / "broker.json"
    proc = _broker("start", "--state-file", str(state_file), env=env)
    assert proc.returncode != 0
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    assert len(lines) == 1 and missing in lines[0], lines
    assert not state_file.exists()


def test_the_broker_does_not_carry_the_request_variables_in_its_environment(broker):
    environ = Path(f"/proc/{broker['pid']}/environ")
    if not environ.exists():
        pytest.skip("no /proc on this platform")
    names = {entry.split(b"=", 1)[0] for entry in environ.read_bytes().split(b"\0")}
    assert not names & {n.encode() for n in OIDC_ENV}, names


def test_stop_ends_the_broker(issuer, tmp_path):
    state_file = tmp_path / "broker.json"
    env = _clean_env(ACTIONS_ID_TOKEN_REQUEST_URL=issuer.token_url,
                     ACTIONS_ID_TOKEN_REQUEST_TOKEN=issuer.request_token)
    assert _broker("start", "--state-file", str(state_file), env=env).returncode == 0
    state = json.loads(state_file.read_text())
    assert _broker("stop", "--state-file", str(state_file), env=_clean_env()).returncode == 0
    with pytest.raises(OSError):
        _ask(state["url"], headers={"X-Bureau-Broker-Secret": state["secret"]})


# ── a client with no OIDC variables mints through it ────────────────────────


def test_a_client_with_no_oidc_variables_mints_through_the_broker(broker, issuer, monkeypatch):
    _point_at(monkeypatch, broker)
    before = len(issuer.requests)
    first, second = bureau_read._mint(), bureau_read._mint()
    seen = issuer.requests[before:]
    assert [r["audience"] for r in seen] == [AUDIENCE, AUDIENCE]
    assert first != second
    assert bureau_read.token_claims(first)["jti"] != bureau_read.token_claims(second)["jti"]
    assert bureau_read.token_claims(first)["aud"] == AUDIENCE


def test_a_whole_door_read_goes_through_the_broker(broker, issuer, monkeypatch):
    with FakeDoor({"DRE-1": card("DRE-1", "Todo")}) as door:
        for key, value in door_env(door_url=door.url, audience=AUDIENCE,
                                   pipeline_ref=None).items():
            monkeypatch.setenv(key, value)
        _point_at(monkeypatch, broker)
        read = bureau_read.cards(["DRE-1"], max_age=60)
        assert [n["identifier"] for n in read.nodes] == ["DRE-1"]
        bearer = door.requests[-1]["headers"]["authorization"]
        assert bureau_read.token_claims(bearer.split(" ", 1)[1])["aud"] == AUDIENCE


def test_the_broker_mints_the_default_audience_when_none_is_set(issuer, tmp_path, monkeypatch):
    state_file = tmp_path / "broker.json"
    env = _clean_env(ACTIONS_ID_TOKEN_REQUEST_URL=issuer.token_url,
                     ACTIONS_ID_TOKEN_REQUEST_TOKEN=issuer.request_token)
    assert _broker("start", "--state-file", str(state_file), env=env).returncode == 0
    try:
        _point_at(monkeypatch, json.loads(state_file.read_text()))
        before = len(issuer.requests)
        bureau_read._mint()
        assert issuer.requests[before]["audience"] == bureau_read.DEFAULT_AUDIENCE
    finally:
        _broker("stop", "--state-file", str(state_file), env=_clean_env())


# ── what the broker refuses ─────────────────────────────────────────────────


@pytest.mark.parametrize("path,method,headers", [
    ("/token", "GET", {"X-Bureau-Broker-Secret": "0" * 64}),
    ("/token", "GET", {}),
    ("/other", "GET", "secret"),
    ("/", "GET", "secret"),
    ("/token/x", "GET", "secret"),
    ("/token", "POST", "secret"),
    ("/token", "PUT", "secret"),
    ("/token", "DELETE", "secret"),
    ("/token", "BREW", "secret"),
    ("/token?audience=sts.amazonaws.com", "GET", "secret"),
    ("/token?api-version=2.0&audience=sts.amazonaws.com", "GET", "secret"),
], ids=["wrong-secret", "no-secret", "other-path", "root", "sub-path", "post", "put",
        "delete", "unknown-method", "audience", "audience-after-another"])
def test_the_broker_answers_403_and_mints_nothing(broker, issuer, path, method, headers):
    if headers == "secret":
        headers = {"X-Bureau-Broker-Secret": broker["secret"]}
    base = broker["url"].rsplit("/", 1)[0]
    before = len(issuer.requests)
    status, _body = _ask(base + path, method=method, headers=headers)
    assert status == 403
    assert len(issuer.requests) == before


def test_the_broker_answers_the_runners_shape(broker, issuer):
    status, body = _ask(broker["url"], headers={"X-Bureau-Broker-Secret": broker["secret"]})
    assert status == 200
    payload = json.loads(body)
    assert list(payload) == ["value"]
    assert bureau_read.token_claims(payload["value"])["aud"] == AUDIENCE


def test_the_broker_listens_on_127_0_0_1_only(broker):
    port = urllib.parse.urlsplit(broker["url"]).port
    # Every 127.0.0.0/8 address reaches the loopback device on Linux, so a
    # broker bound to every address would answer on 127.0.0.2 too.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.2", port), timeout=2).close()
    server = read_token_broker.make_server(request_url="http://127.0.0.1:9/x",
                                           request_token="t", audience=AUDIENCE,
                                           secret="s")
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


@pytest.mark.parametrize("url", [
    "http://localhost:{port}/token",
    "http://127.0.0.2:{port}/token",
    "http://10.0.0.1:{port}/token",
    "http://127.0.0.1.evil.test:{port}/token",
    "http://127.0.0.1@evil.test:{port}/token",
    "https://127.0.0.1:{port}/token",
    "ftp://127.0.0.1:{port}/token",
    "127.0.0.1:{port}/token",
])
def test_the_client_refuses_a_broker_not_on_127_0_0_1(broker, issuer, monkeypatch, url):
    port = urllib.parse.urlsplit(broker["url"]).port
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER", url.format(port=port))
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER_SECRET", broker["secret"])
    before = len(issuer.requests)
    with pytest.raises(bureau_read.ReadUnknown) as e:
        bureau_read._mint()
    assert e.value.reason == "no-token"
    assert "broker" in e.value.detail and "127.0.0.1" in e.value.detail
    assert len(issuer.requests) == before


# ── the runner's own variables win; a dead broker is no-token ───────────────


class _Recorder(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        self.server.seen.append(self.path)
        data = b'{"value": "not-a-token"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_a_client_holding_the_runners_variables_never_asks_the_broker(issuer, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    server.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", issuer.token_url)
        monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", issuer.request_token)
        monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER",
                           f"http://127.0.0.1:{server.server_address[1]}/token")
        monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER_SECRET", "s")
        before = len(issuer.requests)
        token = bureau_read._mint()
        assert bureau_read.token_claims(token)["jti"]
        assert len(issuer.requests) == before + 1
        assert server.seen == []
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_dead_broker_is_no_token_naming_the_broker(monkeypatch):
    url = f"http://127.0.0.1:{_closed_port()}/token"
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER", url)
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER_SECRET", "s")
    with pytest.raises(bureau_read.ReadUnknown) as e:
        bureau_read._mint()
    assert e.value.reason == "no-token"
    assert "broker" in e.value.detail and url in e.value.detail


def test_a_broker_refusal_is_no_token_naming_the_broker(broker, monkeypatch):
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER", broker["url"])
    monkeypatch.setenv("BUREAU_READ_TOKEN_BROKER_SECRET", "wrong")
    with pytest.raises(bureau_read.ReadUnknown) as e:
        bureau_read._mint()
    assert e.value.reason == "no-token"
    assert "broker" in e.value.detail and "403" in e.value.detail


@pytest.mark.parametrize("missing", BROKER_ENV)
def test_half_a_broker_is_no_broker(broker, monkeypatch, missing):
    _point_at(monkeypatch, broker)
    monkeypatch.delenv(missing)
    with pytest.raises(bureau_read.ReadUnknown) as e:
        bureau_read._mint()
    assert e.value.reason == "no-token"
    assert "ACTIONS_ID_TOKEN_REQUEST_URL" in e.value.detail


# ── BUREAU_READ_LINES_FILE copies every read line ───────────────────────────

_SESSION_SCRIPT = r'''
import json, runpy, sys, urllib.request
scripts = sys.argv[1]
sys.path.insert(0, scripts)

class R:
    headers = {"x-ratelimit-requests-remaining": "100"}
    def __init__(self, body): self.body = json.dumps(body).encode()
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False

ISSUE = {"id": "i1", "identifier": "DRE-1", "title": "t", "team": {"id": "t1"},
         "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
         "labels": {"nodes": []}, "children": {"nodes": []}, "project": None}

def urlopen(req, *a, **k):
    query = json.loads(req.data)["query"]
    if "commentCreate" in query:
        return R({"data": {"commentCreate": {"success": True}}})
    return R({"data": {"issue": ISSUE}})

urllib.request.urlopen = urlopen
sys.argv = [scripts + "/linear_ops.py", "comment", "DRE-1", "x"]
runpy.run_path(scripts + "/linear_ops.py", run_name="__main__")
'''


def _session(**extra) -> subprocess.CompletedProcess:
    env = _clean_env(LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet", BUREAU_READ="on",
                     BUREAU_READ_URL="https://door.invalid", GITHUB_WORKFLOW="Plan",
                     GITHUB_JOB="plan", **extra)
    return subprocess.run([sys.executable, "-c", _SESSION_SCRIPT, str(SCRIPTS)],
                          capture_output=True, env=env, timeout=60)


def test_the_lines_file_holds_every_read_line_byte_for_byte(tmp_path):
    lines_file = tmp_path / "read-lines.txt"
    proc = _session(BUREAU_READ_LINES_FILE=str(lines_file))
    assert proc.returncode == 0, proc.stderr.decode()
    printed = [ln for ln in proc.stderr.split(b"\n")
               if ln.startswith(tuple(t.encode() for t in bureau_read.LINES_FILE_TAGS))]
    for tag in ("read-fallback:", "read-door:", "linear-calls:", "linear-read:",
                "linear-door:"):
        assert any(ln.startswith(tag.encode()) for ln in printed), (tag, proc.stderr)
    assert lines_file.read_bytes() == b"".join(ln + b"\n" for ln in printed)


def test_the_lines_file_appends_to_what_is_there(tmp_path):
    lines_file = tmp_path / "read-lines.txt"
    lines_file.write_bytes(b"read-door: an earlier process\n")
    proc = _session(BUREAU_READ_LINES_FILE=str(lines_file))
    assert proc.returncode == 0
    held = lines_file.read_bytes()
    assert held.startswith(b"read-door: an earlier process\nread-door: mode on")


def _normalized(proc) -> tuple:
    err = re.sub(rb"process[=:] ?\S+", b"process=P", proc.stderr)
    err = re.sub(rb"\d+ms", b"Nms", err)
    return proc.returncode, proc.stdout, err


@pytest.mark.parametrize("where", ["a-directory", "under-a-missing-directory"])
def test_an_unwritable_lines_file_changes_nothing_else(tmp_path, where):
    path = tmp_path if where == "a-directory" else tmp_path / "missing" / "lines.txt"
    without = _session()
    with_bad_path = _session(BUREAU_READ_LINES_FILE=str(path))
    assert _normalized(with_bad_path) == _normalized(without)
    assert without.returncode == 0


def test_unset_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proc = _session()
    assert proc.returncode == 0
    assert list(tmp_path.iterdir()) == []


def test_the_tags_are_one_list():
    assert bureau_read.LINES_FILE_TAGS == (
        "read-fallback:", "read-door:", "linear-budget:", "linear-calls:",
        "linear-read:", "linear-door:")
    assert bureau_read.LINES_FILE_ENV == "BUREAU_READ_LINES_FILE"


# ── print-lines: the one way the file reaches the log ───────────────────────

READER = 'reader="Plan / plan / linear_ops.py"'
GOOD = [
    f"read-fallback: {READER} read=cards reason=no-token",
    "read-door: mode on (BUREAU_READ=on, read once for this run)",
    "linear-budget: 4900 → 4890 (spent 10 this run; window resets 10:00 PT; "
    "limit 5000; budget: planner)",
    "linear-calls: 2 request(s) this run (process: 1@h; budget: planner)",
    f"linear-read: {READER} site=linear_ops.comment count=1 budget=planner "
    "process=1@h caller=linear_ops._comment_target",
    f"linear-door: {READER} served=0 fallbacks=cards/no-token:1 budget=planner "
    "process=1@h",
]
BAD = [
    "::add-mask::hunter2",
    "::set-output name=verdict::APPROVE",
    " read-door: mode on (BUREAU_READ=on, read once for this run)",
    "read-door: served 0 unknown 0\r::warning::forged",
    "the session says hello",
    'linear-door: reader="Hygiene / keep / hygiene.py" served=9 fallbacks=- '
    "budget=fleet process=1@h",
    'read-fallback: reader="Hygiene / keep / hygiene.py" read=board reason=no-token',
]


def _print_lines(path, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BROKER), "print-lines", "--file", str(path)],
        capture_output=True, timeout=30,
        env=_clean_env(GITHUB_WORKFLOW="Plan", GITHUB_JOB="plan", **env))


def test_print_lines_prints_only_the_well_formed_lines_in_file_order(tmp_path):
    mixed = [GOOD[0], BAD[0], GOOD[1], BAD[1], BAD[2], GOOD[2], BAD[3], GOOD[3],
             BAD[4], BAD[5], GOOD[4], BAD[6], GOOD[5]]
    path = tmp_path / "read-lines.txt"
    path.write_bytes("".join(f"{ln}\n" for ln in mixed).encode())
    proc = _print_lines(path)
    assert proc.returncode == 0, proc.stderr
    expected = b"".join(f"{ln}\n".encode() for ln in GOOD)
    assert proc.stdout == expected + b"agent read lines: kept 6, dropped 7\n"


def test_a_missing_file_prints_the_no_file_line(tmp_path):
    path = tmp_path / "never-written.txt"
    proc = _print_lines(path)
    assert proc.returncode == 0
    assert proc.stdout.decode() == f"agent read lines: no file at {path}\n"


@pytest.mark.parametrize("line,kept", [
    (f"read-door: tab\tinside {READER}", True),
    ("read-door: a bell \x07", False),
    ("read-door: an escape \x1b[31m", False),
    ("read-door: a C1 control \x85", False),
    ("read-door: a line separator  ::warning::x", False),
    ('read-fallback: reader="Plan / planner / x" read=cards reason=no-token', False),
    ('read-fallback: reader="Plan / plan" read=cards reason=no-token', False),
    ('read-fallback: reader=Hygiene / keep / x read=cards reason=no-token', False),
    (f'read-fallback: {READER} xreader="Hygiene / keep / x" read=cards', False),
    ("linear-read:", True),
    ("read-door-diff: board DRE-1 title", False),
    ("Read-door: mode on", False),
])
def test_print_lines_rules(tmp_path, line, kept):
    path = tmp_path / "read-lines.txt"
    path.write_bytes(line.encode() + b"\n")
    proc = _print_lines(path)
    assert proc.returncode == 0
    summary = f"agent read lines: kept {int(kept)}, dropped {int(not kept)}\n".encode()
    assert proc.stdout == (line.encode() + b"\n" if kept else b"") + summary


def test_print_lines_reads_the_printing_jobs_reader_off_its_own_environment(tmp_path):
    path = tmp_path / "read-lines.txt"
    path.write_bytes(f"read-fallback: {READER} read=cards reason=no-token\n".encode())
    proc = subprocess.run(
        [sys.executable, str(BROKER), "print-lines", "--file", str(path)],
        capture_output=True, timeout=30,
        env=_clean_env(GITHUB_WORKFLOW="Hygiene", GITHUB_JOB="keep"))
    assert proc.stdout == b"agent read lines: kept 0, dropped 1\n"


def test_print_lines_drops_a_line_that_is_not_utf8_and_exits_0(tmp_path):
    path = tmp_path / "read-lines.txt"
    path.write_bytes(b"read-door: \xff\xfe\n" + GOOD[1].encode())
    proc = _print_lines(path)
    assert proc.returncode == 0
    assert proc.stdout == GOOD[1].encode() + b"\nagent read lines: kept 1, dropped 1\n"


def test_print_lines_exits_0_on_a_file_it_cannot_read(tmp_path):
    proc = _print_lines(tmp_path)  # a directory
    assert proc.returncode == 0
    (line,) = proc.stdout.decode().splitlines()
    assert line.startswith(read_token_broker.SUMMARY_TAG)


def test_print_lines_does_not_wait_on_a_fifo(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs on this platform")
    path = tmp_path / "read-lines.txt"
    os.mkfifo(path)
    proc = _print_lines(path)
    assert proc.returncode == 0
    (line,) = proc.stdout.decode().splitlines()
    assert line.startswith(read_token_broker.SUMMARY_TAG)


# ── what the modules say ────────────────────────────────────────────────────


def test_bureau_reads_docstring_names_the_broker_and_why():
    doc = " ".join(bureau_read.__doc__.split())
    assert "read_token_broker.py" in doc
    assert "BUREAU_READ_TOKEN_BROKER" in doc
    assert "the one way a session with no OIDC variables reaches the door" in doc
    assert "any audience" in doc


def test_the_brokers_docstring_says_what_print_lines_drops_and_why():
    doc = " ".join(read_token_broker.__doc__.split())
    assert "the session can write" in doc
    assert "print-lines" in doc
    assert "::" in doc
    assert "control character" in doc
    assert "GITHUB_WORKFLOW" in doc and "GITHUB_JOB" in doc
    assert "own report" in doc


# ── the summary line is declared where a proof run reads it ─────────────────


def test_the_summary_tag_is_declared_after_the_linear_reads_check_entry():
    assert read_token_broker.SUMMARY_TAG == "agent read lines:"
    entries = json.loads((ROOT / "config" / "proof-reach.json").read_text())["log_lines"]
    tags = [e["tag"] for e in entries]
    entry = entries[tags.index("linear-reads-check:") + 1]
    assert entry["tag"] == "agent read lines:"
    assert entry["printed_by"] == "scripts/read_token_broker.py"
    assert entry["constant"] == "SUMMARY_TAG"
    assert "print-lines" in entry["when"]
    assert tags.count("agent read lines:") == 1


def test_proof_reach_check_is_clean():
    assert proof_reach.check(ROOT) == []
