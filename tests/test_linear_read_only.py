"""`BUREAU_LINEAR_READ_ONLY=1` refuses every Linear write sent through `gql()` (DRE-7285).

The proof shadow (DRE-7286) re-runs a real proof card on another model. It
has to read the card, its comments and its thread the way a real proof run
does, and it must never post, move or close anything. Its agent reaches
Linear through `scripts/linear_ops.py` and `scripts/spoken_thread.py`, and
both send every request through `linear_ops.gql()` — so the switch sits
there: with the variable exactly `1`, a document whose operation is a
`mutation` raises `LinearError` before `_send`, nothing goes out, nothing is
counted against the run's budget, and one stderr line opening
`linear-read-only: refused` names the mutation's first field. Queries go out
exactly as they do today, and any other value changes nothing.

The CLI tests run the real script in a subprocess with `urlopen` replaced by a
stub (installed through a `sitecustomize` on `PYTHONPATH`) that logs every
request it is handed and answers queries with one canned card — so "no
request was sent" is read off the HTTP layer itself, not off a mock of
`gql`.
"""

import json
import os
import subprocess  # nosec B404 - runs this repo's own script, fixed argv
import sys
import textwrap

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import linear_ops  # noqa: E402

ENV = "BUREAU_LINEAR_READ_ONLY"
REFUSED = "linear-read-only: refused"

# The stub HTTP layer: every request body is appended to $STUB_LOG as one JSON
# line, and a query is answered with one card that satisfies every read the
# commands under test make. A mutation is answered too, so a run with the
# switch off proves the write WOULD have gone out.
SITECUSTOMIZE = textwrap.dedent('''
    import json, os, urllib.request

    CARD = {
        "id": "issue-1", "identifier": "DRE-1", "title": "A card",
        "description": "the card's description",
        "team": {"id": "team-1", "key": "DRE"},
        "state": {"id": "s-progress", "name": "In Progress", "type": "started"},
        "labels": {"nodes": []},
        "children": {"nodes": []},
        "comments": {
            "nodes": [{"id": "c1", "body": "hello",
                       "createdAt": "2026-10-15T00:00:00.000Z",
                       "user": {"id": "u1", "name": "someone"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "history": {"nodes": []},
    }
    STATES = [
        {"id": "s-progress", "name": "In Progress", "type": "started"},
        {"id": "s-review", "name": "In Review", "type": "started"},
        {"id": "s-todo", "name": "Todo", "type": "unstarted"},
    ]

    class _Resp:
        headers = {}

        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout=None):
        payload = json.loads(req.data)
        with open(os.environ["STUB_LOG"], "a") as f:
            f.write(json.dumps(payload) + "\\n")
        q = payload["query"]
        if q.lstrip().startswith("mutation"):
            data = {"commentCreate": {"success": True},
                    "issueUpdate": {"success": True},
                    "issueLabelCreate": {"issueLabel": {"id": "label-1"}}}
        elif "workflowStates" in q:
            data = {"workflowStates": {"nodes": STATES}}
        elif "team(id" in q:
            data = {"team": {"labels": {"nodes": []}}}
        else:
            data = {"issue": CARD, "viewer": {"id": "u1"}}
        return _Resp(json.dumps({"data": data}).encode())

    urllib.request.urlopen = urlopen
''')


def _run_cli(tmp_path, *argv, read_only=None):
    """Run `linear_ops.py <argv>` against the stub; answer the completed
    process and the request bodies the stub was handed, in order."""
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text(SITECUSTOMIZE)
    log = tmp_path / "requests.jsonl"
    if log.exists():
        log.unlink()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(stub),
        "LINEAR_API_KEY": "lin_api_test",
        "GITHUB_ACTIONS": "true",
        "STUB_LOG": str(log),
    }
    if read_only is not None:
        env[ENV] = read_only
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell
        [sys.executable, os.path.join(SCRIPTS, "linear_ops.py"), *argv],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    sent = []
    if log.exists():
        sent = [json.loads(line) for line in log.read_text().splitlines() if line]
    return proc, sent


def _is_mutation(payload):
    return payload["query"].lstrip().startswith("mutation")


WRITES = {
    "comment": (["comment", "DRE-1", "a shadow must never post this"], "commentCreate"),
    "advance": (["advance", "DRE-1", "In Review", "In Progress"], "issueUpdate"),
    "add-label": (["add-label", "DRE-1", "some-label"], "issueLabelCreate"),
    "proof-waiting": (
        ["proof-waiting", "DRE-1", "the live run on the shadow model",
         "the next proof card dispatched"],
        "commentCreate",
    ),
}


@pytest.mark.parametrize("name", sorted(WRITES))
def test_a_write_command_is_refused_and_sends_no_write(tmp_path, name):
    argv, field = WRITES[name]
    proc, sent = _run_cli(tmp_path, *argv, read_only="1")
    assert proc.returncode != 0, (proc.stdout, proc.stderr)
    refusals = [line for line in proc.stderr.splitlines() if line.startswith(REFUSED)]
    assert len(refusals) == 1, proc.stderr
    assert field in refusals[0]
    # The HTTP layer itself: whatever the command read first went out as it
    # always does, and no write reached the wire.
    assert [p for p in sent if _is_mutation(p)] == []


@pytest.mark.parametrize("name", sorted(WRITES))
def test_the_same_write_command_sends_its_write_with_the_switch_off(tmp_path, name):
    # The control for the test above: the stub really does receive this
    # command's mutation when the switch is off, so "none sent" is not an
    # artifact of the stub.
    argv, field = WRITES[name]
    proc, sent = _run_cli(tmp_path, *argv)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert REFUSED not in proc.stderr
    assert any(_is_mutation(p) and field in p["query"] for p in sent)


READS = {
    "state-of": (["state-of", "DRE-1"], "In Progress\n"),
    "description": (["description", "DRE-1"], "the card's description"),
    "dump-comments": (["dump-comments", "DRE-1"], '["hello"]\n'),
}


@pytest.mark.parametrize("name", sorted(READS))
def test_a_read_command_answers_as_today_under_the_switch(tmp_path, name):
    argv, expected = READS[name]
    on, sent_on = _run_cli(tmp_path, *argv, read_only="1")
    off, sent_off = _run_cli(tmp_path, *argv)
    assert on.returncode == 0, on.stderr
    assert on.stdout == expected
    assert on.stdout == off.stdout
    assert REFUSED not in on.stderr
    assert sent_on and sent_on == sent_off
    assert not any(_is_mutation(p) for p in sent_on)


# --- gql() itself ---------------------------------------------------------

OK_BODY = json.dumps({"data": {"commentCreate": {"success": True}}}).encode()
MUTATION = """mutation($input: CommentCreateInput!) {
                 commentCreate(input: $input) { success } }"""


class _Resp:
    headers = {}

    def read(self):
        return OK_BODY

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def wire(monkeypatch):
    """Every request handed to urlopen, in order; the budget reset."""
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Resp()

    monkeypatch.setattr(linear_ops.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.delenv("LINEAR_API_KEY_FALLBACK", raising=False)
    monkeypatch.setitem(linear_ops._budget, "calls", 0)
    monkeypatch.setitem(linear_ops._budget, "refused_after", None)
    return sent


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1", "1 "])
def test_a_mutation_is_sent_as_today_unless_the_value_is_exactly_1(
    wire, monkeypatch, capsys, value
):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    assert linear_ops.gql(MUTATION, {"input": {"body": "x"}}) == {
        "commentCreate": {"success": True}}
    assert wire == [{"query": MUTATION, "variables": {"input": {"body": "x"}}}]
    assert REFUSED not in capsys.readouterr().err


SHAPES = {
    "plain": (MUTATION, "commentCreate"),
    "leading whitespace": ("\n\n   \t" + MUTATION, "commentCreate"),
    "comment line first": ("# post the receipt\n" + MUTATION, "commentCreate"),
    "comment lines and blanks first": (
        "\n  # one\n\n# two mutation-free\n  " + MUTATION, "commentCreate"),
    "named": ("mutation Foo { issueUpdate(id: \"x\", input: {}) { success } }",
              "issueUpdate"),
    "named with variables": (
        "mutation Post($input: CommentCreateInput!) "
        "{ commentCreate(input: $input) { success } }", "commentCreate"),
    "aliased field": ("mutation { posted: commentCreate(input: {}) { success } }",
                      "commentCreate"),
    "a fragment first": (
        "fragment F on Comment { id }\n"
        "mutation { commentCreate(input: {}) { comment { ...F } } }",
        "commentCreate"),
    "a query first, then a mutation": (
        "query Q { viewer { id } }\n"
        "mutation M { issueArchive(id: \"x\") { success } }", "issueArchive"),
    "a byte-order mark and commas": ("﻿,, mutation { issueDelete(id: \"x\") { success } }",
                                     "issueDelete"),
    # A lone `\r` ends a line in GraphQL as `\n` does: a comment or a string
    # stops there, and the mutation after it is still read.
    "a comment ended by a carriage return": (
        "# note\rmutation { issueArchive(id: \"x\") { success } }", "issueArchive"),
    "a string ended by a carriage return": (
        "query { a(b: \"x\r) { id } }\n"
        "mutation { issueArchive(id: \"y\") { success } }", "issueArchive"),
    "a string ended by an escaped line break": (
        "query { a(b: \"x\\\n) { id } }\n"
        "mutation { issueArchive(id: \"y\") { success } }", "issueArchive"),
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_shape_of_mutation_is_refused_before_anything_is_sent(
    wire, monkeypatch, capsys, name
):
    document, field = SHAPES[name]
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(document, {"input": {}})
    assert wire == []
    assert linear_ops._budget["calls"] == 0
    err = capsys.readouterr().err.splitlines()
    refusals = [line for line in err if line.startswith(REFUSED)]
    assert len(refusals) == 1, err
    assert refusals[0].startswith(f"{REFUSED} {field}")


QUERIES = {
    "plain": "query($id: String!) { issue(id: $id) { id } }",
    "shorthand": "{ viewer { id } }",
    "named": "query Thread { viewer { id } }",
    "the word in a comment": "# never a mutation here\nquery { viewer { id } }",
    "the word in a string": 'query { issues(filter: {title: {contains: "mutation {"}}) { nodes { id } } }',
    "the word in a block string": 'query { issues(filter: {title: {eq: """\nmutation { x }"""}}) { nodes { id } } }',
    "a field named like it": "query { mutationLog { id } mutation { id } }",
}


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_a_query_goes_out_exactly_as_today_under_the_switch(
    wire, monkeypatch, capsys, name
):
    monkeypatch.setenv(ENV, "1")
    linear_ops.gql(QUERIES[name], {"id": "DRE-1"})
    assert wire == [{"query": QUERIES[name], "variables": {"id": "DRE-1"}}]
    assert REFUSED not in capsys.readouterr().err


def test_gql_paged_reaches_the_same_check(wire, monkeypatch, capsys):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql_paged(
            "mutation($after: String) { issueArchive(id: $after) { success } }"
        )
    assert wire == []
    assert REFUSED in capsys.readouterr().err


def test_the_check_says_what_it_does_not_guard():
    doc = linear_ops._refuse_a_write_when_read_only.__doc__ or ""
    flat = " ".join(doc.split())
    assert "gql()" in flat
    assert ENV in flat
    # A write sent straight to Linear with the same key is outside it, and the
    # docstring says so in those terms.
    assert "curl" in flat
    assert "LINEAR_API_KEY" in flat
    assert "does not see" in flat or "would not see" in flat


def test_the_check_runs_inside_gql():
    # Not a helper nobody calls: gql() itself must reach it.
    import inspect

    assert "_refuse_a_write_when_read_only(" in inspect.getsource(linear_ops.gql)
