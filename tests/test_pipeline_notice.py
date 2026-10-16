"""The notice door's client, driven through real HTTP against a fake door (DRE-7451).

`scripts/pipeline_notice.py` is the one way a pipeline writer raises a console
alert in place of the notice card it used to file. What these pin, each
against the failure it exists to stop:

* a door answering 200 gets the contract body under a fresh OIDC token minted
  for the notice audience, and Linear is never touched;
* a door that cannot be reached, refuses, or is not configured never makes a
  notice silent: the writer's old card is filed (or commented on, when its
  title is already open), and a duplicate search that fails files nothing;
* long text is cut to the contract's limits before it is sent, so a long
  diagnosis is never turned into a card by the door's 400, and the full text
  goes to stderr and onto any fallback card uncut;
* a resolve carries the all-clear copy when given, and a refused resolve is
  logged and nothing else;
* exactly one `pipeline-notice:` line per call, on stdout.

No test here reaches the console, GitHub or Linear: the issuer is
`tests/bureau_read_fakes.py`'s, the door is below, both on 127.0.0.1, and
`linear_ops` is replaced function by function.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import linear_ops  # noqa: E402
import pipeline_notice  # noqa: E402
import proof_reach  # noqa: E402
from bureau_read_fakes import FakeIssuer  # noqa: E402

NOTICE_PATH = "/api/v1/pipeline/notices"
DEFAULT_AUDIENCE = "https://app.agent-bureau.com/pipeline-notice"
CUT_NOTE = "… [cut to fit the console; the full text is in this run's log]"


def token_claims_of(token: str) -> dict:
    """The JWT payload the fake issuer minted, unverified."""
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


# ── the fake door ───────────────────────────────────────────────────────────


class _DoorHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_POST(self):  # noqa: N802
        owner = self.server.owner
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode())
        except ValueError:
            body = None
        owner.requests.append({"path": self.path, "headers": dict(self.headers),
                               "body": body})
        if owner.delay:
            time.sleep(owner.delay)
        status, answer = owner.answer(body)
        data = json.dumps(answer).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:  # the client gave up on a slow answer
            pass


class FakeNoticeDoor:
    """`POST /api/v1/pipeline/notices` on an ephemeral loopback port.

    `status` is what every request is answered; `outcome` the outcome a 200
    carries. `strict` answers 400 to any text past the contract's limits —
    the real door's rule, which a client that does not cut would meet."""

    def __init__(self, *, status: int = 200, outcome: str = "raised",
                 strict: bool = False, delay: float = 0.0):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _DoorHandler)
        self.httpd.owner = self
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.status = status
        self.outcome = outcome
        self.strict = strict
        self.delay = delay
        self.requests: list[dict] = []

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def answer(self, body):
        if self.status != 200:
            return self.status, {"detail": f"refused {self.status}"}
        if self.strict and isinstance(body, dict):
            for name, limit in (("title", 200), ("recovery_title", 200),
                                ("body", 8000), ("recovery_body", 8000)):
                if len(body.get(name) or "") > limit:
                    return 400, {"detail": f"{name} is over {limit} characters"}
        kind = f"pipeline_notice:{(body or {}).get('notice')}"
        return 200, {"kind": kind, "outcome": self.outcome}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# ── the fake board ──────────────────────────────────────────────────────────


class FakeBoard:
    """`linear_ops`, function by function: the client's three Linear calls,
    and `gql` refusing so any OTHER call fails the test loudly."""

    def __init__(self, *, open_card: str | None = None, search_fails: bool = False,
                 create_fails: bool = False, comment_capped: bool = False):
        self.open_card = open_card
        self.search_fails = search_fails
        self.create_fails = create_fails
        self.comment_capped = comment_capped
        self.searches: list[str] = []
        self.created: list[dict] = []
        self.comments: list[tuple[str, str]] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(linear_ops, "find_open", self.find_open)
        monkeypatch.setattr(linear_ops, "create_card", self.create_card)
        monkeypatch.setattr(linear_ops, "cmd_comment", self.cmd_comment)
        monkeypatch.setattr(linear_ops, "gql", self.gql)
        return self

    def find_open(self, title):
        self.searches.append(title)
        if self.search_fails:
            raise linear_ops.LinearError("search answered 500")
        return self.open_card

    def create_card(self, title, description, *, repo_slug, labels=(), lane="Planning"):
        if self.create_fails:
            raise linear_ops.LinearError("create answered 500")
        self.created.append({"title": title, "description": description,
                             "repo_slug": repo_slug, "labels": list(labels),
                             "lane": lane})
        return {"identifier": "DRE-9001", "url": "https://linear.app/x/DRE-9001"}

    def cmd_comment(self, identifier, body, *flags):
        if self.comment_capped:
            # Linear's comment limit: `linear_ops.cmd_comment` writes nothing
            # and returns the condition rather than raising (DRE-3343).
            return linear_ops.COMMENT_CAP_CONDITION
        self.comments.append((identifier, body))
        return None

    def gql(self, *a, **kw):  # pragma: no cover — reaching it is the failure
        raise AssertionError("an unexpected Linear call")

    @property
    def calls(self) -> int:
        return len(self.searches) + len(self.created) + len(self.comments)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("BUREAU_READ_URL", "BUREAU_NOTICE_AUDIENCE",
                 "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "dreadnought-foundry/bureau-pipeline")
    monkeypatch.setattr(pipeline_notice, "CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(pipeline_notice, "TOTAL_TIMEOUT", 0.5)


@pytest.fixture
def board(monkeypatch):
    return FakeBoard().install(monkeypatch)


@pytest.fixture
def issuer():
    with FakeIssuer() as fake:
        yield fake


def _point(monkeypatch, door, issuer):
    monkeypatch.setenv("BUREAU_READ_URL", door.url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", issuer.token_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", issuer.request_token)


def _lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("pipeline-notice:")]


FALLBACK = pipeline_notice.Fallback(
    title="Pipeline failure: agent-task.yml", lane="Planning",
    labels=("medic", "no-code"))


def _raise(**over):
    kw = dict(severity="WARN", title="Agent Task failed", body="the diagnosis",
              fallback=FALLBACK)
    kw.update(over)
    return pipeline_notice.raise_notice("medic:atlas:agent-task.yml", **kw)


# ── the door answers ────────────────────────────────────────────────────────


def test_a_raise_posts_the_contract_body_and_prints_the_outcome(
        monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(outcome="raised") as door:
        _point(monkeypatch, door, issuer)
        got = _raise(action_url="https://github.com/x/y/actions/runs/1",
                     action_label="Open the run")
    assert got == "raised"
    assert len(door.requests) == 1
    req = door.requests[0]
    assert req["path"] == NOTICE_PATH
    assert req["body"] == {
        "action": "raise", "notice": "medic:atlas:agent-task.yml",
        "severity": "WARN", "title": "Agent Task failed", "body": "the diagnosis",
        "action_url": "https://github.com/x/y/actions/runs/1",
        "action_label": "Open the run",
    }
    bearer = req["headers"]["Authorization"]
    assert bearer.startswith("Bearer ")
    assert token_claims_of(bearer[len("Bearer "):])["aud"] == DEFAULT_AUDIENCE
    assert issuer.requests[-1]["audience"] == DEFAULT_AUDIENCE
    out = capsys.readouterr().out
    assert _lines(out) == ["pipeline-notice: raised kind=pipeline_notice:medic:atlas:agent-task.yml"]
    assert board.calls == 0


def test_the_audience_is_read_from_bureau_notice_audience(
        monkeypatch, capsys, issuer, board):
    monkeypatch.setenv("BUREAU_NOTICE_AUDIENCE", "https://console.test/notice")
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        _raise()
    assert issuer.requests[-1]["audience"] == "https://console.test/notice"
    bearer = door.requests[0]["headers"]["Authorization"]
    assert token_claims_of(bearer[len("Bearer "):])["aud"] == "https://console.test/notice"


def test_the_door_outcome_is_returned_as_it_answered(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(outcome="repeat") as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "repeat"
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: repeat kind=pipeline_notice:medic:atlas:agent-task.yml"]


def test_a_null_action_url_and_label_are_sent_as_null(monkeypatch, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        _raise()
    body = door.requests[0]["body"]
    assert body["action_url"] is None and body["action_label"] is None


# ── the door could not take it: the old card is filed ──────────────────────


def _assert_fallback(capsys, board, reason):
    out = capsys.readouterr().out
    assert _lines(out) == [
        f"pipeline-notice: fallback-card {reason} kind=pipeline_notice:"
        "medic:atlas:agent-task.yml card=DRE-9001"]
    assert board.searches == ["Pipeline failure: agent-task.yml"]
    assert len(board.created) == 1
    card = board.created[0]
    assert card["title"] == "Pipeline failure: agent-task.yml"
    assert card["lane"] == "Planning"
    assert card["labels"] == ["medic", "no-code"]
    assert card["repo_slug"] == "bureau-pipeline"
    assert card["description"].startswith("the diagnosis")
    assert "console door was not reached" in card["description"]
    assert reason in card["description"]


@pytest.mark.parametrize("status", [400, 401, 403, 503])
def test_a_refusing_door_files_the_fallback_card(monkeypatch, capsys, issuer, board, status):
    with FakeNoticeDoor(status=status) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "fallback-card"
    _assert_fallback(capsys, board, f"http-{status}")


def test_a_door_that_times_out_files_the_fallback_card(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(delay=2.0) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "fallback-card"
    _assert_fallback(capsys, board, "timeout")


def test_no_oidc_token_files_the_fallback_card(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL")
        assert _raise() == "fallback-card"
        assert door.requests == []
    _assert_fallback(capsys, board, "no-token")


def test_a_refusing_oidc_endpoint_names_its_answer(monkeypatch, capsys, issuer, board):
    """A refused mint carries the endpoint's own words to stderr, not only
    its status — a bare 500 is the unattributable red run the urlopen audit
    in tests/test_linear_error_body.py exists to stop."""
    issuer.status = 500
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "fallback-card"
        assert door.requests == []
    err = capsys.readouterr().err
    assert "the runner's OIDC endpoint answered 500" in err
    assert "issuer refused" in err
    assert len(board.created) == 1


def test_no_bureau_read_url_files_the_fallback_card(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        monkeypatch.delenv("BUREAU_READ_URL")
        assert _raise() == "fallback-card"
        assert door.requests == []
    _assert_fallback(capsys, board, "no-url")


def test_an_unreachable_door_files_the_fallback_card(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor() as door:
        url = door.url
    _point(monkeypatch, door, issuer)
    monkeypatch.setenv("BUREAU_READ_URL", url)  # nothing listens there now
    assert _raise() == "fallback-card"
    _assert_fallback(capsys, board, "unreachable")


def test_a_fallback_title_already_open_is_commented_on(monkeypatch, capsys, issuer):
    board = FakeBoard(open_card="DRE-77").install(monkeypatch)
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "fallback-card"
    assert board.created == []
    assert len(board.comments) == 1
    assert board.comments[0][0] == "DRE-77"
    assert board.comments[0][1].startswith("the diagnosis")
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: fallback-card http-503 kind=pipeline_notice:"
        "medic:atlas:agent-task.yml card=DRE-77"]


def test_a_failed_duplicate_search_files_nothing_and_is_lost(monkeypatch, capsys, issuer):
    board = FakeBoard(search_fails=True).install(monkeypatch)
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "lost"
    assert board.created == [] and board.comments == []
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: lost search-failed kind=pipeline_notice:"
        "medic:atlas:agent-task.yml"]


def test_a_failed_create_is_lost(monkeypatch, capsys, issuer):
    FakeBoard(create_fails=True).install(monkeypatch)
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "lost"
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: lost create-failed kind=pipeline_notice:"
        "medic:atlas:agent-task.yml"]


def test_a_fallback_card_at_its_comment_limit_is_lost(monkeypatch, capsys, issuer):
    board = FakeBoard(open_card="DRE-77", comment_capped=True).install(monkeypatch)
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise() == "lost"
    assert board.created == [] and board.comments == []
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: lost comment-capped kind=pipeline_notice:"
        "medic:atlas:agent-task.yml"]


def test_a_raise_with_no_fallback_the_door_could_not_take_is_lost(
        monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise(fallback=None) == "lost"
    assert board.calls == 0
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: lost http-503 kind=pipeline_notice:medic:atlas:agent-task.yml"]


def test_the_fallback_repo_is_read_from_a_repo_label(monkeypatch, capsys, issuer, board):
    fallback = pipeline_notice.Fallback(title="t", lane="Triage",
                                        labels=("repo:atlas", "red-main"))
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        _raise(fallback=fallback)
    assert board.created[0]["repo_slug"] == "atlas"
    assert board.created[0]["lane"] == "Triage"


# ── long text is cut, never refused ─────────────────────────────────────────


def test_long_text_is_cut_to_fit_and_written_to_stderr(monkeypatch, capsys, issuer, board):
    title = "T" * 249 + "!"
    body = "".join(chr(ord("a") + i % 26) for i in range(8999)) + "Z"
    with FakeNoticeDoor(strict=True) as door:
        _point(monkeypatch, door, issuer)
        assert _raise(title=title, body=body) == "raised"
    sent = door.requests[0]["body"]
    assert len(sent["title"]) == 200
    assert sent["title"] == title[:199] + "…"
    assert sent["body"] == body[:7900] + "\n\n" + CUT_NOTE
    assert board.calls == 0
    captured = capsys.readouterr()
    assert title in captured.err
    assert body in captured.err
    assert _lines(captured.out) == [
        "pipeline-notice: raised kind=pipeline_notice:medic:atlas:agent-task.yml"]


def test_text_exactly_at_the_limits_is_sent_unchanged(monkeypatch, capsys, issuer, board):
    title, body = "t" * 200, "b" * 8000
    with FakeNoticeDoor(strict=True) as door:
        _point(monkeypatch, door, issuer)
        assert _raise(title=title, body=body) == "raised"
    sent = door.requests[0]["body"]
    assert sent["title"] == title and sent["body"] == body
    assert title not in capsys.readouterr().err


def test_a_fallback_for_a_cut_body_carries_the_full_body(monkeypatch, capsys, issuer, board):
    body = "x" * 8500 + "THE END"
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        assert _raise(body=body) == "fallback-card"
    assert len(door.requests[0]["body"]["body"]) < len(body)
    assert board.created[0]["description"].startswith(body)
    assert CUT_NOTE not in board.created[0]["description"]


def test_a_resolve_cuts_its_recovery_text_the_same_way(monkeypatch, capsys, issuer, board):
    title, body = "R" * 300, "r" * 9000
    with FakeNoticeDoor(outcome="resolved", strict=True) as door:
        _point(monkeypatch, door, issuer)
        got = pipeline_notice.resolve_notice(
            "red_main:atlas:budget_exhausted", recovery_title=title, recovery_body=body)
    assert got == "resolved"
    sent = door.requests[0]["body"]
    assert sent["recovery_title"] == title[:199] + "…"
    assert sent["recovery_body"] == body[:7900] + "\n\n" + CUT_NOTE
    err = capsys.readouterr().err
    assert title in err and body in err


# ── resolve ─────────────────────────────────────────────────────────────────


def test_a_resolve_posts_the_all_clear_when_given(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(outcome="resolved") as door:
        _point(monkeypatch, door, issuer)
        got = pipeline_notice.resolve_notice(
            "reviewer_outage:fleet",
            recovery_title="Reviewer back — reviews are running again",
            recovery_body="The reconcile sweep at 09:15 PT found it cleared.")
    assert got == "resolved"
    assert door.requests[0]["body"] == {
        "action": "resolve", "notice": "reviewer_outage:fleet",
        "recovery_title": "Reviewer back — reviews are running again",
        "recovery_body": "The reconcile sweep at 09:15 PT found it cleared.",
    }
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: resolved kind=pipeline_notice:reviewer_outage:fleet"]
    assert board.calls == 0


def test_a_resolve_without_the_all_clear_sends_none(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(outcome="not-open") as door:
        _point(monkeypatch, door, issuer)
        assert pipeline_notice.resolve_notice("groom_stall") == "not-open"
    assert door.requests[0]["body"] == {"action": "resolve", "notice": "groom_stall"}
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: not-open kind=pipeline_notice:groom_stall"]


@pytest.mark.parametrize("status", [400, 401, 403, 503])
def test_a_refused_resolve_is_logged_and_nothing_else(monkeypatch, capsys, issuer, board, status):
    with FakeNoticeDoor(status=status) as door:
        _point(monkeypatch, door, issuer)
        got = pipeline_notice.resolve_notice("groom_stall")
    assert got == "resolve-refused"
    assert board.calls == 0
    assert _lines(capsys.readouterr().out) == [
        f"pipeline-notice: resolve-refused http-{status} kind=pipeline_notice:groom_stall"]


def test_a_recovery_body_without_a_recovery_title_is_refused(issuer, board):
    with pytest.raises(ValueError):
        pipeline_notice.resolve_notice("groom_stall", recovery_body="cleared")


# ── refused before any request ──────────────────────────────────────────────


@pytest.mark.parametrize("key", ["", "has space", "slash/inside", "x" * 121, "é"])
def test_a_bad_notice_key_is_refused_before_any_request(monkeypatch, issuer, board, key):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(ValueError):
            pipeline_notice.raise_notice(key, severity="WARN", title="t", body="b",
                                         fallback=FALLBACK)
        with pytest.raises(ValueError):
            pipeline_notice.resolve_notice(key)
    assert door.requests == [] and issuer.requests == [] and board.calls == 0


def test_the_longest_good_key_is_accepted(monkeypatch, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        pipeline_notice.raise_notice("a" * 120, severity="CRITICAL", title="t", body="b")
    assert door.requests[0]["body"]["notice"] == "a" * 120


def test_a_bad_severity_is_refused_before_any_request(monkeypatch, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(ValueError):
            pipeline_notice.raise_notice("groom_stall", severity="INFO", title="t", body="b")
    assert door.requests == []


# ── the CLI drives the same functions ───────────────────────────────────────


def test_cli_raise_with_a_body_file(monkeypatch, capsys, tmp_path, issuer, board):
    body = tmp_path / "body.md"
    body.write_text("the run list\n")
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main([
            "raise", "--notice", "groom_stall", "--severity", "WARN",
            "--title", "Groom queue stalled", "--body-file", str(body),
            "--action-url", "https://github.com/x", "--action-label", "Open"])
    assert code == 0
    assert door.requests[0]["body"] == {
        "action": "raise", "notice": "groom_stall", "severity": "WARN",
        "title": "Groom queue stalled", "body": "the run list\n",
        "action_url": "https://github.com/x", "action_label": "Open",
    }
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: raised kind=pipeline_notice:groom_stall"]


def test_cli_raise_falls_back_with_the_given_card(monkeypatch, capsys, tmp_path, issuer, board):
    body = tmp_path / "body.md"
    body.write_text("stalled")
    with FakeNoticeDoor(status=401) as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main([
            "raise", "--notice", "groom_stall", "--severity", "WARN",
            "--title", "Groom queue stalled", "--body-file", str(body),
            "--fallback-title", "Groom queue stalled", "--fallback-lane", "Triage",
            "--fallback-label", "groomer", "--fallback-label", "no-code"])
    assert code == 0
    assert board.created[0]["title"] == "Groom queue stalled"
    assert board.created[0]["lane"] == "Triage"
    assert board.created[0]["labels"] == ["groomer", "no-code"]
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: fallback-card http-401 kind=pipeline_notice:groom_stall card=DRE-9001"]


def test_cli_raise_with_no_fallback_the_door_refused_exits_non_zero(
        monkeypatch, capsys, tmp_path, issuer, board):
    body = tmp_path / "body.md"
    body.write_text("smoke")
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main([
            "raise", "--notice", "smoke:pipeline_notice", "--severity", "WARN",
            "--title", "smoke", "--body-file", str(body)])
    assert code != 0
    assert board.calls == 0
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: lost http-503 kind=pipeline_notice:smoke:pipeline_notice"]


def test_cli_raise_whose_fallback_search_fails_exits_non_zero(
        monkeypatch, capsys, tmp_path, issuer):
    FakeBoard(search_fails=True).install(monkeypatch)
    body = tmp_path / "body.md"
    body.write_text("x")
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main([
            "raise", "--notice", "groom_stall", "--severity", "WARN",
            "--title", "t", "--body-file", str(body), "--fallback-title", "t",
            "--fallback-lane", "Planning"])
    assert code != 0


def test_cli_resolve_with_the_all_clear(monkeypatch, capsys, tmp_path, issuer, board):
    body = tmp_path / "recovery.md"
    body.write_text("Agent Task passed on the default branch of atlas at 09:15 PT.")
    with FakeNoticeDoor(outcome="resolved") as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main([
            "resolve", "--notice", "red_main:atlas:budget_exhausted",
            "--recovery-title", "Main is green again in atlas",
            "--recovery-body-file", str(body)])
    assert code == 0
    assert door.requests[0]["body"] == {
        "action": "resolve", "notice": "red_main:atlas:budget_exhausted",
        "recovery_title": "Main is green again in atlas",
        "recovery_body": "Agent Task passed on the default branch of atlas at 09:15 PT.",
    }
    assert _lines(capsys.readouterr().out) == [
        "pipeline-notice: resolved kind=pipeline_notice:red_main:atlas:budget_exhausted"]


def test_cli_resolve_without_the_all_clear(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(outcome="not-open") as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main(["resolve", "--notice", "groom_stall"])
    assert code == 0
    assert door.requests[0]["body"] == {"action": "resolve", "notice": "groom_stall"}


def test_cli_refused_resolve_exits_zero_and_files_nothing(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor(status=503) as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main(["resolve", "--notice", "groom_stall"])
    assert code == 0
    assert board.calls == 0


def test_cli_refuses_a_bad_key_before_any_request(monkeypatch, capsys, issuer, board):
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        code = pipeline_notice.main(["resolve", "--notice", "has space"])
    assert code != 0
    assert door.requests == [] and issuer.requests == []


# ── the log line is declared for the proof ──────────────────────────────────


def test_the_log_line_is_declared_in_proof_reach():
    entries = [e for e in proof_reach.load()["log_lines"] if e["tag"] == "pipeline-notice:"]
    assert len(entries) == 1
    assert entries[0]["printed_by"] == "scripts/pipeline_notice.py"
    assert entries[0]["constant"] == "TAG"
    assert pipeline_notice.TAG == "pipeline-notice:"


def test_proof_reach_check_still_passes():
    assert proof_reach.check(ROOT) == []


# ── where a fallback card may go ────────────────────────────────────────────


def test_the_fallback_lanes_are_published_and_none_is_ready_work():
    """`ready_lane_writers.py` cannot read a lane the caller hands in, so the
    module publishes the lanes a fallback card may go to — the two the
    repointed writers filed theirs in."""
    assert pipeline_notice.destinations() == ("Planning", "Triage")


def test_a_fallback_lane_outside_them_is_refused_before_any_request(
        monkeypatch, issuer, board):
    fallback = pipeline_notice.Fallback(title="t", lane="Todo")
    with FakeNoticeDoor() as door:
        _point(monkeypatch, door, issuer)
        with pytest.raises(ValueError):
            _raise(fallback=fallback)
    assert door.requests == [] and board.calls == 0
