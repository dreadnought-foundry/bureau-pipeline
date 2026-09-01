"""Regression pin (DRE-2923): a Linear HTTP error carries the response BODY
and the endpoint — and a RATELIMITED body is its own named condition.

Origin (2026-09-01): four consecutive `reconcile.yml` runs in agent-bureau
failed and the log said only

    urllib.error.HTTPError: HTTP Error 400: Bad Request

No endpoint, no message, no code, and a traceback ending inside urllib — it did
not even name which call failed. The reason was in the response body, which the
client discarded:

    {"errors":[{"message":"Rate limit exceeded. Only 2500 requests are allowed
    per 1 hour ...","extensions":{"type":"ratelimited","code":"RATELIMITED",
    "statusCode":429,...

**Linear answers a rate limit with HTTP 400 on the wire and 429 only inside the
body.** A client that reads the status and drops the body cannot tell "you are
over quota, wait" from a malformed query, a bad token or a schema error — and
those need opposite responses. A bare `400: Bad Request` reads as *our request
was malformed* (a code defect) when the truth was *we are over quota* (a
transient, self-healing condition needing no code change at all).

So, three things pinned here:
  * every HTTP error the client raises names the endpoint and carries the body
    (truncated), matching agent-bureau/scripts/linear_workspace.py:237;
  * a RATELIMITED body raises its OWN exception type and is named
    `rate limited: N requests/hour exhausted`, because the operator response is
    *wait*, not *debug*;
  * the sibling audit — no other urlopen in scripts/ may raise an HTTPError
    without reading its body first. Fixing only the client that bit us is how
    this recurs in the next module, so the rule is checked structurally over
    the tree rather than over a hand-written list.

The sweep-side half (a rate-limited sweep fails DISTINGUISHABLY, and the medic
recognises the condition instead of escalating it as a defect) is pinned in
tests/test_medic_linear_rate_limit.py.
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
import model_catalog  # noqa: E402

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))

# The 2026-09-01 body, verbatim in shape: the quota sentence lives in
# `message`, and the only mention of 429 is inside `extensions`.
RATELIMIT_BODY = json.dumps(
    {
        "errors": [
            {
                "message": (
                    "Rate limit exceeded. Only 2500 requests are allowed per 1 "
                    "hour and you have made 2500 requests in the last hour."
                ),
                "extensions": {
                    "type": "ratelimited",
                    "code": "RATELIMITED",
                    "statusCode": 429,
                    "userError": True,
                },
            }
        ]
    }
).encode()

MALFORMED_BODY = json.dumps(
    {"errors": [{"message": 'Field "nope" is not defined by type IssueFilter.'}]}
).encode()


def _http_error(body: bytes, code: int = 400):
    """A urllib.error.HTTPError whose `.read()` returns `body` — exactly what
    urlopen raises on a non-2xx, and the object whose body the client dropped."""
    return urllib.error.HTTPError(
        linear_ops.API, code, "Bad Request", {}, io.BytesIO(body)
    )


def _raising_urlopen(exc):
    def _open(*_args, **_kwargs):
        raise exc

    return _open


# ── the client: body + endpoint on every HTTP error ─────────────────────────
def test_http_error_carries_the_body_and_the_endpoint(monkeypatch):
    """A 400 that is NOT a rate limit still names the endpoint and the body —
    the bare status is what made the four red runs unattributable."""
    monkeypatch.setattr(
        linear_ops.urllib.request,
        "urlopen",
        _raising_urlopen(_http_error(MALFORMED_BODY)),
    )
    with pytest.raises(linear_ops.LinearError) as exc_info:
        linear_ops.gql("query { issues { nodes { id } } }")
    message = str(exc_info.value)
    assert "400" in message
    assert linear_ops.API in message, "the error must name the endpoint"
    assert "is not defined by type IssueFilter" in message, "the body is the reason"
    # Not the bare urllib text that told nobody anything.
    assert message != "HTTP Error 400: Bad Request"


def test_http_error_is_a_linear_error_not_a_bare_httperror(monkeypatch):
    """Callers isolate a failure with `except linear_ops.LinearError`. A raw
    HTTPError sails past that — which is why the reconcile traceback ended
    inside urllib rather than naming the call."""
    monkeypatch.setattr(
        linear_ops.urllib.request,
        "urlopen",
        _raising_urlopen(_http_error(MALFORMED_BODY)),
    )
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql("query { issues { nodes { id } } }")


def test_body_is_truncated(monkeypatch):
    """Truncated, not unbounded: a Linear error body can carry a whole schema
    dump, and the log line has to stay readable."""
    huge = b'{"errors":[{"message":"' + b"x" * 20000 + b'"}]}'
    monkeypatch.setattr(
        linear_ops.urllib.request, "urlopen", _raising_urlopen(_http_error(huge))
    )
    with pytest.raises(linear_ops.LinearError) as exc_info:
        linear_ops.gql("query { issues { nodes { id } } }")
    assert len(str(exc_info.value)) < 1000
    assert linear_ops.BODY_CHARS <= 500


# ── the rate-limit condition, named ─────────────────────────────────────────
def test_ratelimited_body_raises_the_rate_limit_type_with_the_quota_sentence(
    monkeypatch,
):
    """The card's headline case: a stubbed 400 whose body is RATELIMITED. The
    raised message must contain the quota sentence the server already sent —
    reproducing the call by hand to read it is what this removes."""
    monkeypatch.setattr(
        linear_ops.urllib.request,
        "urlopen",
        _raising_urlopen(_http_error(RATELIMIT_BODY)),
    )
    with pytest.raises(linear_ops.LinearRateLimited) as exc_info:
        linear_ops.gql("query { issues { nodes { id } } }")
    message = str(exc_info.value)
    assert "Only 2500 requests are allowed per 1 hour" in message
    # Named as the condition it is — the operator response is wait, not debug.
    assert "rate limited: 2500 requests/hour exhausted" in message
    assert "Bad Request" not in message
    assert linear_ops.API in message
    # Still a LinearError, so every existing `except LinearError` keeps working.
    assert isinstance(exc_info.value, linear_ops.LinearError)


def test_a_plain_400_is_not_a_rate_limit(monkeypatch):
    """The whole point of reading the body: these two are indistinguishable on
    the wire and must not be conflated."""
    monkeypatch.setattr(
        linear_ops.urllib.request,
        "urlopen",
        _raising_urlopen(_http_error(MALFORMED_BODY)),
    )
    with pytest.raises(linear_ops.LinearError) as exc_info:
        linear_ops.gql("query { issues { nodes { id } } }")
    assert not isinstance(exc_info.value, linear_ops.LinearRateLimited)


def test_rate_limit_condition_reads_the_quota_out_of_the_body():
    assert (
        linear_ops.rate_limit_condition(RATELIMIT_BODY.decode())
        == "rate limited: 2500 requests/hour exhausted"
    )


def test_rate_limit_condition_without_a_quota_sentence_still_names_the_condition():
    """A RATELIMITED body whose wording we cannot parse is still a rate limit —
    naming it beats falling back to `Bad Request` because a regex missed."""
    condition = linear_ops.rate_limit_condition('{"extensions":{"code":"RATELIMITED"}}')
    assert condition is not None
    assert condition.startswith("rate limited:")


def test_rate_limit_condition_is_none_for_an_ordinary_error():
    assert linear_ops.rate_limit_condition(MALFORMED_BODY.decode()) is None


def test_ratelimited_in_a_200_errors_payload_is_also_named(monkeypatch):
    """Linear can also answer 200 with an errors payload. Same condition, same
    type — the classification lives with the body, not with the status."""

    class _Resp:
        def read(self):
            return RATELIMIT_BODY

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        linear_ops.urllib.request, "urlopen", lambda *a, **k: _Resp()
    )
    with pytest.raises(linear_ops.LinearRateLimited) as exc_info:
        linear_ops.gql("query { issues { nodes { id } } }")
    message = str(exc_info.value)
    assert "rate limited: 2500 requests/hour exhausted" in message
    # The endpoint too, on the same line as the condition — the medic's
    # classifier requires both together, and without it this branch's message
    # classified as `normal` (see tests/test_medic_linear_rate_limit.py::
    # test_the_200_errors_payload_message_is_classifiable_too).
    assert linear_ops.API in message, "the error must name the endpoint"


# ── sibling audit: no other urlopen drops its body ──────────────────────────
def _urlopen_sites():
    """Every `urllib.request.urlopen(...)` call in scripts/, as
    (relative path, enclosing function node, module node). Discovered by
    walking the tree — never a hand-written list, because the client nobody
    remembered is exactly the one still dropping its body."""
    sites = []
    for root, _dirs, files in os.walk(SCRIPTS):
        if "__pycache__" in root:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "urlopen"
                    ):
                        sites.append((os.path.relpath(path, SCRIPTS), func, tree))
                        break
    return sites


def _body_readers(module) -> set:
    """Module-level function names whose body calls `.read()` — the small
    helpers a handler delegates the read to (linear_ops' `_error_body`), so
    extracting that read does not read as dropping it."""
    names = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "read"
            for n in ast.walk(node)
        ):
            names.add(node.name)
    return names


def _reads_the_body(scope, readers=frozenset(), *, allow_swallow=True) -> bool:
    """True when an HTTPError raised inside `scope` cannot escape it without
    its body having been read: either an `except ...HTTPError` handler that
    calls `.read()` (directly or through one of `readers`), or — only when
    `allow_swallow` — a catch-all handler that swallows rather than re-raises.

    `allow_swallow=False` is what the module-scope fallback uses. A catch-all
    that swallows proves the HTTPError cannot escape THAT function; it proves
    nothing at module scope, where an unrelated `except Exception: return`
    three functions away would otherwise vouch for a client that drops its
    body — which is exactly how this guard was briefly vacuous.
    """
    for handler in (n for n in ast.walk(scope) if isinstance(n, ast.ExceptHandler)):
        names = ast.dump(handler.type) if handler.type is not None else ""
        catches_http = "HTTPError" in names
        catches_all = allow_swallow and (handler.type is None or "Exception" in names)
        if not (catches_http or catches_all):
            continue
        if catches_http:
            reads = any(
                isinstance(n, ast.Call)
                and (
                    (isinstance(n.func, ast.Attribute) and n.func.attr == "read")
                    or (isinstance(n.func, ast.Name) and n.func.id in readers)
                )
                for n in ast.walk(handler)
            )
            if reads:
                return True
        if catches_all:
            # A handler that returns/continues cannot propagate the HTTPError
            # at all, so there is no bare status for anyone to read.
            swallows = any(
                isinstance(n, (ast.Return, ast.Continue, ast.Pass))
                for n in ast.walk(handler)
            )
            reraises = any(isinstance(n, ast.Raise) for n in ast.walk(handler))
            if swallows and not reraises:
                return True
    return False


def test_every_urlopen_site_in_scripts_reads_the_error_body():
    """The audit criterion. Fixing only the client that bit us is how this
    recurs in the next module — so a NEW urlopen that drops its body fails
    this build rather than becoming the next unattributable red run.

    The fallback to module scope is deliberate and is the check's one known
    limit: `harness/github_api.py` puts its urlopen in a one-line injectable
    opener and handles the HTTPError in the caller that owns the retry loop, so
    a function-scoped rule alone would fail a client that already does exactly
    the right thing. It means a SECOND, body-dropping urlopen added to a module
    that already has a compliant handler would pass — stated here rather than
    left to be discovered.
    """
    sites = _urlopen_sites()
    assert sites, "found no urlopen call sites — the walker is broken"
    offenders = [
        f"{path}:{func.name}"
        for path, func, module in sites
        if not (
            _reads_the_body(func, _body_readers(module))
            or _reads_the_body(module, _body_readers(module), allow_swallow=False)
        )
    ]
    assert not offenders, (
        "these urlopen sites can raise an HTTP error without reading the "
        f"response body: {offenders}"
    )


def test_the_audit_would_catch_a_body_dropping_client():
    """The guard above is only worth having if it goes red on the shape it
    exists to catch — the pre-fix `gql`, which had no handler at all."""
    func = ast.parse(
        "def gql():\n"
        "    with urllib.request.urlopen(req) as resp:\n"
        "        return json.loads(resp.read())\n"
    ).body[0]
    assert not _reads_the_body(func)


def test_an_unrelated_swallow_elsewhere_in_the_module_does_not_vouch():
    """The module-scope fallback exists for ONE shape — a thin injectable
    opener whose caller owns the HTTPError handler. It must not let an
    unrelated `except Exception: return` three functions away vouch for a
    client that drops its body; that made this guard silently vacuous once."""
    module = ast.parse(
        "def gql():\n"
        "    with urllib.request.urlopen(req) as resp:\n"
        "        return json.loads(resp.read())\n"
        "\n"
        "def unrelated():\n"
        "    try:\n"
        "        return int(x)\n"
        "    except Exception:\n"
        "        return None\n"
    )
    readers = _body_readers(module)
    assert not _reads_the_body(module, readers, allow_swallow=False)
    # ...and the loose form is exactly what it must not be called with.
    assert _reads_the_body(module, readers, allow_swallow=True)


# ── the sibling that raised bare: the model catalog ─────────────────────────
def test_model_catalog_http_error_carries_body_and_endpoint(monkeypatch):
    """`_fetch_real` raised urllib's bare HTTPError too. Its caller turns any
    failure into an empty catalog, so the exception's own message is the only
    place the reason can survive — it must not be `HTTP Error 400`."""
    monkeypatch.setattr(model_catalog, "auth_headers", lambda: {"x-api-key": "k"})
    body = b'{"type":"error","error":{"type":"authentication_error",' b'"message":"invalid x-api-key"}}'
    import urllib.request as _ur

    monkeypatch.setattr(
        _ur,
        "urlopen",
        _raising_urlopen(
            urllib.error.HTTPError(
                model_catalog.CATALOG_URL, 401, "Unauthorized", {}, io.BytesIO(body)
            )
        ),
    )
    with pytest.raises(Exception) as exc_info:
        model_catalog._fetch_real()
    message = str(exc_info.value)
    assert "401" in message
    assert model_catalog.CATALOG_URL in message
    assert "invalid x-api-key" in message
