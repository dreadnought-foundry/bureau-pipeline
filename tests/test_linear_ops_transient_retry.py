"""Regression pin (DRE-3087): one transient network fault does not kill a sweep.

Origin (2026-09-04): two reconcile sweeps died overnight, each on a single
socket-level failure, and the next sweep passed both times:

    agent-bureau      run 33873466137, 05:34 PT
      urllib.error.URLError: <urlopen error [Errno 104] Connection reset by
      peer>                                                          exit 1
    agent-bureau-demo run 33885631073, 07:46 PT
      TimeoutError: The read operation timed out                     exit 1

A sweep is dozens of Linear and GitHub calls. One of them losing a socket
took the whole crawl down with a traceback, and every promotion, nudge and
dependency gate waited a full 15-minute interval for the next one. It is not
always cheap: the harness sandbox's own dead sweep left a proving run waiting
three hours on 2026-09-03 (DRE-3076).

So the shared request seam — `linear_ops.gql`, the ONE urlopen the sweep makes
of its own (its GitHub calls go through the `gh` CLI, pinned below) — retries
ONCE after a short backoff, and only on the shapes that a second attempt can
actually fix:

  * `ConnectionResetError` and `TimeoutError`, raised bare or wrapped in a
    `URLError` — the two shapes above;
  * HTTP 502/503/504 — a gateway that was not there for one request.

**Never a 4xx.** Linear answers its quota exhaustion with HTTP 400 on the wire
(DRE-2923 owns that classification), so retrying a 4xx spends the request that
proves we have none left. 500 is not retried either: the retryable set is the
gateway statuses the card named, not "any server error".

A second failure of the same call fails the sweep exactly as it does today,
with the traceback — the retry buys one hiccup, it does not paper over an
outage.
"""

import ast
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)

import linear_ops  # noqa: E402

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))

QUERY = "query { issues { nodes { id } } }"

OK_BODY = json.dumps({"data": {"issues": {"nodes": [{"id": "abc"}]}}}).encode()

MALFORMED_BODY = json.dumps(
    {"errors": [{"message": 'Field "nope" is not defined by type IssueFilter.'}]}
).encode()

RATELIMIT_BODY = json.dumps(
    {
        "errors": [
            {
                "message": (
                    "Rate limit exceeded. Only 2500 requests are allowed per 1 "
                    "hour and you have made 2500 requests in the last hour."
                ),
                "extensions": {"code": "RATELIMITED", "statusCode": 429},
            }
        ]
    }
).encode()


class _Resp:
    """What urlopen hands back on a 200 — a context manager over the body."""

    def __init__(self, body: bytes = OK_BODY):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Transport:
    """A urlopen stand-in driven by a list of outcomes, one per call: an
    exception instance is raised, anything else is returned. Counts its calls,
    which is how "retried once" is measured — the seam's own return value
    cannot tell one attempt from two."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _http_error(code: int, body: bytes = MALFORMED_BODY):
    return urllib.error.HTTPError(
        linear_ops.API, code, "reason", {}, io.BytesIO(body)
    )


def _reset():
    """The 05:34 fault, bare: what a socket raises on a peer-side reset."""
    return ConnectionResetError(104, "Connection reset by peer")


def _wrapped_reset():
    """The 05:34 fault as the traceback actually showed it — urllib wraps the
    OSError from a TLS handshake in a URLError."""
    return urllib.error.URLError(_reset())


def _read_timeout():
    """The 07:46 fault: socket.timeout IS TimeoutError since 3.10."""
    return TimeoutError("The read operation timed out")


@pytest.fixture
def transport(monkeypatch):
    """Installs a transport and swallows the backoff, so a retry test costs
    nothing in wall-clock. The recorded sleeps are asserted on directly."""
    sleeps: list[float] = []
    monkeypatch.setattr(linear_ops.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")

    def _install(*outcomes):
        t = _Transport(*outcomes)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        t.sleeps = sleeps
        return t

    return _install


# ── the two fault shapes from the card ──────────────────────────────────────
@pytest.mark.parametrize(
    "fault",
    [_reset(), _wrapped_reset(), _read_timeout(), urllib.error.URLError(_read_timeout())],
    ids=["bare-reset", "urlerror-reset", "read-timeout", "urlerror-timeout"],
)
def test_one_transient_fault_is_retried_once_and_the_call_succeeds(transport, fault):
    """The headline: resets once, then succeeds → one retry, and the caller
    gets its data instead of a dead sweep."""
    t = transport(fault, _Resp())
    assert linear_ops.gql(QUERY) == {"issues": {"nodes": [{"id": "abc"}]}}
    assert t.calls == 2, "the fault must be retried exactly once"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_status_is_retried_once(transport, status):
    t = transport(_http_error(status), _Resp())
    assert linear_ops.gql(QUERY) == {"issues": {"nodes": [{"id": "abc"}]}}
    assert t.calls == 2


def test_the_retry_waits_a_short_backoff_first(transport):
    """Immediately re-firing at a peer that just reset us is not a retry. The
    wait is short — a sweep runs every 15 minutes and holds nothing open."""
    t = transport(_reset(), _Resp())
    linear_ops.gql(QUERY)
    assert t.sleeps == [linear_ops.RETRY_BACKOFF_SECONDS]
    assert 0 < linear_ops.RETRY_BACKOFF_SECONDS <= 5


# ── the receipt ─────────────────────────────────────────────────────────────
def test_the_retry_is_logged_in_one_line(transport, capsys):
    """A sweep that needed a retry says so — otherwise the only evidence a
    fault happened at all is a run that took a second longer."""
    transport(_wrapped_reset(), _Resp())
    linear_ops.gql(QUERY)
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "transient network fault" in ln]
    assert len(lines) == 1, f"expected exactly one receipt line, got {err!r}"
    assert lines[0].startswith("transient network fault, retried once: ")
    assert "Connection reset by peer" in lines[0], "the receipt names the error"


def test_a_call_that_never_faults_logs_nothing(transport, capsys):
    """The receipt must mean something: no fault, no line."""
    transport(_Resp())
    linear_ops.gql(QUERY)
    assert "transient network fault" not in capsys.readouterr().err


# ── a second failure is still fatal, unchanged ──────────────────────────────
def test_two_resets_raise_the_original_error(transport):
    """Resets twice → the error the caller would have seen today. The retry
    buys one hiccup; it does not swallow an outage."""
    t = transport(_reset())
    with pytest.raises(ConnectionResetError) as exc_info:
        linear_ops.gql(QUERY)
    assert "Connection reset by peer" in str(exc_info.value)
    assert t.calls == 2, "exactly one retry, then out"


def test_two_read_timeouts_raise_the_original_error(transport):
    t = transport(_read_timeout())
    with pytest.raises(TimeoutError):
        linear_ops.gql(QUERY)
    assert t.calls == 2


def test_two_gateway_errors_still_raise_the_linear_error_shape(transport):
    """The second 503 fails exactly as today: a LinearError naming the
    endpoint and carrying the body (DRE-2923), not a bare HTTPError."""
    t = transport(_http_error(503), _http_error(503))
    with pytest.raises(linear_ops.LinearError) as exc_info:
        linear_ops.gql(QUERY)
    message = str(exc_info.value)
    assert "503" in message and linear_ops.API in message
    assert t.calls == 2


# ── no 4xx is ever retried ──────────────────────────────────────────────────
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429])
def test_no_4xx_is_ever_retried(transport, status):
    """Linear answers quota exhaustion with a 400 (DRE-2923). Retrying it
    spends the request that proves we have none left, and none of the other
    4xx shapes — bad token, bad query — is fixed by asking twice."""
    t = transport(_http_error(status))
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.calls == 1, f"a {status} must not be retried"


def test_a_ratelimited_400_is_not_retried_and_keeps_its_named_condition(transport):
    """The exact body DRE-2923 owns: still LinearRateLimited, still one call."""
    t = transport(_http_error(400, RATELIMIT_BODY))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.calls == 1


def test_a_ratelimited_200_errors_payload_is_not_retried(transport):
    """A quota exhaustion can arrive as a 200 with an errors payload. It is not
    a transport fault and must not be re-fired."""
    t = transport(_Resp(RATELIMIT_BODY))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.calls == 1


def test_a_500_is_not_retried(transport):
    """The retryable set is the three gateway statuses the incident named, not
    "any server error" — a 500 is Linear's own code path failing."""
    t = transport(_http_error(500))
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.calls == 1


def test_a_url_error_with_no_wrapped_exception_is_not_retried(transport):
    """`URLError('unknown url type')` is a programming error wearing a network
    error's type. Only a wrapped reset/timeout is transient."""
    t = transport(urllib.error.URLError("unknown url type: htp"))
    with pytest.raises(urllib.error.URLError):
        linear_ops.gql(QUERY)
    assert t.calls == 1


# ── the classifier, directly ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "exc,expected",
    [
        (_reset(), True),
        (_read_timeout(), True),
        (_wrapped_reset(), True),
        (urllib.error.URLError(_read_timeout()), True),
        (urllib.error.URLError("unknown url type"), False),
        (_http_error(502), True),
        (_http_error(503), True),
        (_http_error(504), True),
        (_http_error(400), False),
        (_http_error(429), False),
        (_http_error(500), False),
        (ValueError("not a network fault"), False),
    ],
)
def test_is_transient_classifies_each_shape(exc, expected):
    assert linear_ops.is_transient(exc) is expected


# ── the sweep's GitHub calls have no urlopen seam of their own ──────────────
def test_the_sweep_makes_no_github_https_call_of_its_own():
    """The card asks for the GitHub request helper too, "if it has its own".
    It does not: every GitHub call the sweep makes shells out to the `gh` CLI,
    which owns its own transport. Pinned rather than asserted in a PR body — a
    urlopen added to reconcile.py later would need this retry and nothing else
    would say so."""
    with open(os.path.join(SCRIPTS, "reconcile.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    urlopens = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "urlopen"
    ]
    assert not urlopens, (
        "reconcile.py now opens an HTTPS call directly — it needs the same "
        "one-shot transient retry linear_ops.gql has, or a stated reason why not"
    )
