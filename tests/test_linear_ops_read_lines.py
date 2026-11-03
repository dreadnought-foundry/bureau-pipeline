"""Every Linear read a pipeline process makes is counted apart from its writes
and named twice (DRE-8897): by the place in the calling code that asked for it
(`site`) and by the function that sent the query (`caller`).

Before this card a process said only its request total, reads and writes
mixed (`linear-calls:`), and the door's answers on a line that named no
bucket and no process (`read-door: served`). So nothing could tell a read
moved onto the door from one that stayed on Linear. At exit the process now
prints, after its `linear-calls:` lines:

    linear-read: reader="<workflow> / <job> / <script>" site=<module>.<function> count=<n> budget=<bucket> process=<token> caller=<module>.<function>
    linear-door: reader="<workflow> / <job> / <script>" served=<n> fallbacks=<read>/<reason>:<n>[,...] budget=<bucket> process=<token>

The site is read off the stack, so the callers here live in real files named
for the modules the criteria name (`reconcile.py`, `board_snapshot.py`),
written into a temporary directory. No test reaches Linear or the console:
the transport is a stub, and the door is `tests/bureau_read_fakes.py`.
"""

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bureau_read  # noqa: E402
import linear_ops  # noqa: E402
from bureau_read_fakes import FakeDoor, FakeIssuer, card, door_env  # noqa: E402

SCRIPT = os.path.basename(sys.argv[0])

READ = re.compile(
    r'^linear-read: reader="(?P<reader>[^"]+)" site=(?P<site>\S+) count=(?P<count>\d+) '
    r"budget=(?P<budget>\S+) process=(?P<process>\S+) caller=(?P<caller>\S+)$"
)
DOOR = re.compile(
    r'^linear-door: reader="(?P<reader>[^"]+)" served=(?P<served>\d+) '
    r"fallbacks=(?P<fallbacks>\S+) budget=(?P<budget>\S+) process=(?P<process>\S+)$"
)

ISSUE = {"id": "i1", "identifier": "DRE-1", "title": "t", "team": {"id": "t1"},
         "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
         "labels": {"nodes": []}, "children": {"nodes": []}, "project": None}


class _Resp:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode()
        self.headers = {"x-ratelimit-requests-remaining": "100"}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Transport:
    """A urlopen stand-in that answers by what the request asks: a mutation
    succeeds, a history read has one move, an issue read is `ISSUE`, and a
    paged `issues` read walks `pages` pages. An exception in `first` is raised
    for the request at that position instead."""

    def __init__(self, *, pages: int = 1, raises: dict | None = None):
        self.pages, self.raises = pages, dict(raises or {})
        self.sent: list[str] = []

    def __call__(self, req, *_a, **_k):
        query = json.loads(req.data)["query"]
        variables = json.loads(req.data)["variables"]
        self.sent.append(query)
        if len(self.sent) in self.raises:
            raise self.raises[len(self.sent)]
        if "mutation" in query:
            return _Resp({"data": {"commentCreate": {"success": True}}})
        if "history" in query:
            return _Resp({"data": {"issue": {"history": {"nodes": [
                {"createdAt": "2026-11-01T00:00:00Z", "toState": {"name": "Todo"}}]}}}})
        if "$after" in query:
            page = int(variables.get("after") or 0) + 1
            return _Resp({"data": {"issues": {
                "nodes": [{"id": f"n{page}"}],
                "pageInfo": {"hasNextPage": page < self.pages, "endCursor": str(page)}}}})
        return _Resp({"data": {"issue": ISSUE}})


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    for name in ("GITHUB_WORKFLOW", "GITHUB_JOB", "BUREAU_READ"):
        monkeypatch.delenv(name, raising=False)

    def _install(**kw):
        t = _Transport(**kw)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        return t

    return _install


def _module(tmp_path, filename: str, source: str):
    """A caller in a real file named `filename`, so its frames carry the name
    the site is read from. Loaded under a name of its own, so no real module
    of the same name is replaced."""
    path = tmp_path / filename
    path.write_text(textwrap.dedent(source))
    spec = importlib.util.spec_from_file_location(f"dre8897_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exit_lines(capsys) -> list[str]:
    capsys.readouterr()
    linear_ops._report_budget_at_exit()
    return capsys.readouterr().err.splitlines()


def _reads(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if line.startswith("linear-read:"):
            m = READ.match(line)
            assert m, f"not the contract's shape: {line!r}"
            out.append(m.groupdict())
    return out


def _doors(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if line.startswith("linear-door:"):
            m = DOOR.match(line)
            assert m, f"not the contract's shape: {line!r}"
            out.append(m.groupdict())
    return out


def test_the_tags_are_the_contracts():
    assert linear_ops.READ_TAG == "linear-read:"
    assert linear_ops.DOOR_TAG == "linear-door:"


# ── the read line ───────────────────────────────────────────────────────────


def test_two_reads_through_lane_history_and_one_mutation(transport, tmp_path, capsys):
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
            linear_ops.lane_history("DRE-2")
            linear_ops.gql("mutation { commentCreate(input: {}) { success } }")
    """)
    reconcile.build_started()
    lines = _exit_lines(capsys)
    (read,) = _reads(lines)
    assert read["site"] == "reconcile.build_started"
    assert read["caller"] == "linear_ops.lane_history"
    assert read["count"] == "2"
    assert read["budget"] == "fleet"
    assert read["process"] == linear_ops.process_token()
    (calls,) = [ln for ln in lines if ln.startswith("linear-calls:")]
    assert calls.startswith("linear-calls: 3 request(s) this run")


def test_the_read_lines_follow_the_calls_lines(transport, tmp_path, capsys):
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
    """)
    reconcile.build_started()
    lines = _exit_lines(capsys)
    tags = [ln.split(" ", 1)[0] for ln in lines]
    assert tags == ["linear-budget:", "linear-calls:", "linear-read:"]


def test_a_paged_read_counts_each_page(transport, tmp_path, capsys):
    transport(pages=3)
    board_snapshot = _module(tmp_path, "board_snapshot.py", """
        import linear_ops

        def read_board():
            return linear_ops.gql_paged(
                "query($after: String) { issues(after: $after) { nodes { id } "
                "pageInfo { hasNextPage endCursor } } }")
    """)
    assert len(board_snapshot.read_board()) == 3
    (read,) = _reads(_exit_lines(capsys))
    assert (read["site"], read["caller"], read["count"]) == (
        "board_snapshot.read_board", "board_snapshot.read_board", "3")


def test_one_site_through_two_functions_prints_two_lines(transport, tmp_path, capsys):
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
            linear_ops.get_issue("DRE-1")
            linear_ops.get_issue("DRE-2")
    """)
    reconcile.build_started()
    reads = _reads(_exit_lines(capsys))
    assert [(r["site"], r["caller"], r["count"]) for r in reads] == [
        ("reconcile.build_started", "linear_ops.get_issue", "2"),
        ("reconcile.build_started", "linear_ops.lane_history", "1"),
    ], "one line per caller, largest count first"


def test_a_caller_that_sends_gql_itself_is_its_own_site(transport, tmp_path, capsys):
    transport()
    rereview_watch = _module(tmp_path, "rereview_watch.py", """
        import linear_ops

        def _state_history():
            return linear_ops.gql("query { viewer { id } }")
    """)
    rereview_watch._state_history()
    (read,) = _reads(_exit_lines(capsys))
    assert read["site"] == read["caller"] == "rereview_watch._state_history"


def test_a_with_block_is_not_the_site(transport, tmp_path, capsys):
    """`_card_read_once` reads inside `contextlib`'s `__enter__`: the
    interpreter's own library is never the calling code."""
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            with linear_ops._card_read_once("DRE-1"):
                pass
    """)
    reconcile.build_started()
    (read,) = _reads(_exit_lines(capsys))
    assert (read["site"], read["caller"]) == ("reconcile.build_started",
                                              "linear_ops.get_issue")


def test_a_name_that_cannot_be_read_never_fails_the_read(transport, monkeypatch, capsys):
    def broken(_frame):
        raise RuntimeError("no stack")

    monkeypatch.setattr(linear_ops, "_read_names", broken)
    transport()
    assert linear_ops.gql("query { viewer { id } }")
    (read,) = _reads(_exit_lines(capsys))
    assert read["site"] == read["caller"] == "-"


def test_a_retry_of_the_same_read_counts_again(transport, tmp_path, capsys):
    transport(raises={1: urllib.error.URLError(ConnectionResetError("reset"))})
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
    """)
    reconcile.build_started()
    lines = _exit_lines(capsys)
    (read,) = _reads(lines)
    assert read["count"] == "2"
    assert any(ln.startswith("linear-calls: 2 request(s)") for ln in lines)


def _auth_401():
    body = json.dumps({"errors": [{"message": "Authentication required",
                                   "extensions": {"code": "AUTHENTICATION_ERROR"}}]}).encode()
    return urllib.error.HTTPError(linear_ops.API, 401, "err", {}, io.BytesIO(body))


def test_reads_on_two_buckets_after_a_fallback_print_one_line_per_bucket(
        transport, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "Bearer planner-token")
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, "lin_api_fleetkey")
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-oauth")
    transport(raises={2: _auth_401()})
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
            linear_ops.lane_history("DRE-2")
            linear_ops.lane_history("DRE-3")
    """)
    reconcile.build_started()
    lines = _exit_lines(capsys)
    reads = {r["budget"]: int(r["count"]) for r in _reads(lines)}
    # The first read and the refused request went out on the token; the resend
    # and the third read on the fleet key — exactly as the calls lines charge.
    assert reads == {"planner-oauth": 2, "fleet": 2}
    calls = [ln for ln in lines if ln.startswith("linear-calls:")]
    assert any("2 request(s)" in ln and "budget: planner-oauth)" in ln for ln in calls)
    assert any("2 request(s)" in ln and "budget: fleet)" in ln for ln in calls)


def test_the_reader_names_workflow_job_and_script(transport, tmp_path, capsys,
                                                    monkeypatch):
    monkeypatch.setenv("GITHUB_WORKFLOW", "Reconcile")
    monkeypatch.setenv("GITHUB_JOB", "sweep")
    monkeypatch.setattr(sys, "argv", ["/home/runner/scripts/proof_dispatch.py"])
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
    """)
    reconcile.build_started()
    (read,) = _reads(_exit_lines(capsys))
    assert read["reader"] == "Reconcile / sweep / proof_dispatch.py"


def test_the_reader_off_a_runner(transport, tmp_path, capsys):
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
    """)
    reconcile.build_started()
    (read,) = _reads(_exit_lines(capsys))
    assert read["reader"] == f"local / - / {SCRIPT}"


def test_only_mutations_print_no_read_line(transport, tmp_path, capsys):
    transport()
    writer = _module(tmp_path, "writer.py", """
        import linear_ops

        def write():
            linear_ops.gql("mutation { commentCreate(input: {}) { success } }")
            linear_ops.gql("# a note\\n mutation M { issueUpdate(id: \\"x\\") { success } }")
    """)
    writer.write()
    lines = _exit_lines(capsys)
    assert _reads(lines) == []
    assert any(ln.startswith("linear-calls: 2 request(s)") for ln in lines)


def test_a_process_that_sent_nothing_prints_nothing(transport, capsys):
    transport()
    assert _exit_lines(capsys) == []


# ── `linear_ops.py <command>` run from bash: the command is the site ────────

_COMMAND_SCRIPT = r'''
import json, runpy, sys, urllib.request
scripts = sys.argv[1]
sys.path.insert(0, scripts)

class R:
    headers = {"x-ratelimit-requests-remaining": "100"}
    def __init__(self, body): self.body = json.dumps(body).encode()
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False

ISSUE = json.loads(%r)
sent = []

def urlopen(req, *a, **k):
    query = json.loads(req.data)["query"]
    sent.append(query)
    if "commentCreate" in query:
        return R({"data": {"commentCreate": {"success": True}}})
    return R({"data": {"issue": ISSUE}})

urllib.request.urlopen = urlopen
sys.argv = [scripts + "/linear_ops.py", "comment", "DRE-1", "x"]
runpy.run_path(scripts + "/linear_ops.py", run_name="__main__")
'''


def test_a_comment_run_from_bash_names_the_command_as_its_site():
    env = dict(os.environ, LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet")
    for name in ("LINEAR_API_KEY_FALLBACK", "LINEAR_KEY_HOME", "BUREAU_READ",
                 "GITHUB_WORKFLOW", "GITHUB_JOB", "BUREAU_LINEAR_READ_ONLY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-c", _COMMAND_SCRIPT % json.dumps(ISSUE), str(ROOT / "scripts")],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "commented on DRE-1" in proc.stdout
    lines = proc.stderr.splitlines()
    (read,) = _reads(lines)
    assert (read["site"], read["caller"], read["count"]) == (
        "linear_ops.comment", "linear_ops.get_issue", "1")
    assert read["reader"] == "local / - / linear_ops.py"
    assert any(ln.startswith("linear-calls: 2 request(s)") for ln in lines)


# ── the door's answers, on the key they were made under ─────────────────────

WORLD = {"DRE-1": card("DRE-1", "Todo"), "DRE-2": card("DRE-2", "In Progress")}


@pytest.fixture
def door_client(monkeypatch):
    for name in ("BUREAU_READ", "BUREAU_READ_URL", "BUREAU_READ_AUDIENCE",
                 "BUREAU_PIPELINE_REF", "ACTIONS_ID_TOKEN_REQUEST_URL",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_REPOSITORY",
                 "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW", "GITHUB_JOB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    monkeypatch.delenv(linear_ops.FALLBACK_ENV, raising=False)
    monkeypatch.delenv(linear_ops.HOME_ENV, raising=False)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    bureau_read.reset_for_tests()
    yield
    bureau_read.reset_for_tests()


def _serve_three_and_one_stale(monkeypatch, mode: str = "on"):
    with FakeIssuer() as issuer, FakeDoor(WORLD) as door:
        for key, value in door_env(door_url=door.url, issuer=issuer, mode=mode).items():
            monkeypatch.setenv(key, value)
        for read in (lambda: bureau_read.board(["Todo"], max_age=120),
                     lambda: bureau_read.cards(["DRE-1"], max_age=60),
                     lambda: bureau_read.workflow_states(max_age=3600)):
            try:
                read()
            except bureau_read.ReadUnknown:
                assert mode == "off", "the door serves every read in on and shadow"
    bureau_read.note_fallback("cards", "stale")


def test_a_door_only_process_prints_one_door_line(door_client, monkeypatch, capsys):
    _serve_three_and_one_stale(monkeypatch)
    assert bureau_read.served() == 3
    lines = _exit_lines(capsys)
    (door,) = _doors(lines)
    assert door == {"reader": f"local / - / {SCRIPT}", "served": "3",
                    "fallbacks": "cards/stale:1", "budget": "fleet",
                    "process": linear_ops.process_token()}
    assert not [ln for ln in lines if ln.startswith(("linear-calls:", "linear-budget:",
                                                     "linear-read:"))]


def test_fallbacks_group_by_read_and_reason_sorted(door_client, monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    for read, reason in [("cards", "stale"), ("board", "stale"), ("cards", "stale"),
                         ("cards", "not-found")]:
        bureau_read.note_fallback(read, reason)
    (door,) = _doors(_exit_lines(capsys))
    assert door["served"] == "0"
    assert door["fallbacks"] == "board/stale:1,cards/not-found:1,cards/stale:2"


def test_no_fallbacks_is_a_dash(door_client, monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.mode()
    (door,) = _doors(_exit_lines(capsys))
    assert door["fallbacks"] == "-"


def test_the_door_line_follows_the_read_lines(door_client, monkeypatch, capsys,
                                              transport, tmp_path):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.mode()
    transport()
    reconcile = _module(tmp_path, "reconcile.py", """
        import linear_ops

        def build_started():
            linear_ops.lane_history("DRE-1")
    """)
    reconcile.build_started()
    lines = _exit_lines(capsys)
    tags = [ln.split(" ", 1)[0] for ln in lines]
    assert tags == ["linear-budget:", "linear-calls:", "linear-read:", "linear-door:"]


def test_the_door_line_names_the_planner_bucket(door_client, monkeypatch, capsys):
    monkeypatch.setenv("LINEAR_API_KEY", "Bearer planner-token")
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, "lin_api_fleetkey")
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-oauth")
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.mode()
    (door,) = _doors(_exit_lines(capsys))
    assert door["budget"] == "planner-oauth"


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_a_door_not_on_prints_no_door_line(door_client, monkeypatch, capsys, mode):
    _serve_three_and_one_stale(monkeypatch, mode=mode)
    assert _doors(_exit_lines(capsys)) == []


def test_a_process_that_never_imported_the_door_prints_no_door_line(
        door_client, monkeypatch, capsys):
    monkeypatch.setenv("BUREAU_READ", "on")
    bureau_read.mode()
    monkeypatch.delitem(sys.modules, "bureau_read")
    assert _doors(_exit_lines(capsys)) == []


def test_the_doors_own_served_line_is_unchanged(door_client, monkeypatch, capsys):
    _serve_three_and_one_stale(monkeypatch)
    assert re.fullmatch(
        r"read-door: served 3 unknown 0 unavailable 0 \(mode on\) \d+ms",
        bureau_read.exit_line())
    capsys.readouterr()
    bureau_read._report_at_exit()
    err = capsys.readouterr().err
    assert err == bureau_read.exit_line() + "\n"


_KILLED_DOOR_SCRIPT = r'''
import os, signal, sys, time
sys.path.insert(0, sys.argv[1])
import linear_ops, bureau_read
bureau_read.mode()
os.kill(os.getpid(), signal.SIGTERM)
time.sleep(30)
'''


def test_a_killed_process_still_prints_the_door_line():
    env = dict(os.environ, LINEAR_API_KEY="test-key", LINEAR_IDENTITY="fleet",
               BUREAU_READ="on")
    for name in ("LINEAR_API_KEY_FALLBACK", "LINEAR_KEY_HOME"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-c", _KILLED_DOOR_SCRIPT, str(ROOT / "scripts")],
        capture_output=True, text=True, env=env, timeout=60,
    )
    (door,) = _doors(proc.stderr.splitlines())
    assert (door["served"], door["fallbacks"], door["budget"]) == ("0", "-", "fleet")


# ── the tags are declared where a proof run reads them ──────────────────────


@pytest.mark.parametrize("tag,constant", [("linear-read:", "READ_TAG"),
                                          ("linear-door:", "DOOR_TAG")])
def test_the_tag_is_declared_in_proof_reach(tag, constant):
    entries = json.loads((ROOT / "config" / "proof-reach.json").read_text())["log_lines"]
    (entry,) = [e for e in entries if e["tag"] == tag]
    assert entry["printed_by"] == "scripts/linear_ops.py"
    assert entry["constant"] == constant
    assert "stderr" in entry["when"] and "exit" in entry["when"]
