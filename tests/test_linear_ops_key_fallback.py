"""A dead planner token falls back to the fleet key — on authentication only (DRE-5589).

plan.yml hands every step `secrets.LINEAR_PLANNER_KEY || secrets.LINEAR_API_KEY`
as `LINEAR_API_KEY`, and the fleet key again as `LINEAR_API_KEY_FALLBACK`.
`||` only falls back on an EMPTY secret. A token the console published and
then could not keep alive — expired while the console was down, a refused
refresh chain, re-authorization pending — still holds a value, and the console
never un-publishes it. So without this seam the planner would get a 401 on
every call instead of planning on the fleet key.

`linear_ops.gql` therefore, on an authentication refusal from the primary key:
retries THAT request once on the fallback, when one is set and differs; keeps
using it for the rest of the process; and prints one line saying so. Never on
a 400 that is not authentication, never on a 429, never on a RATELIMITED body:
Linear answers quota exhaustion with HTTP 400 (DRE-2923), and a rate limit on
the planner's bucket is not a reason to spend the fleet's.

And the `linear-budget:` line names the bucket the process actually spent:
`budget: planner-oauth` on the token, `budget: fleet` after a fallback or
when no planner key was published.
"""

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

QUERY = "query { viewer { id } }"
OK_BODY = json.dumps({"data": {"viewer": {"id": "u1"}}}).encode()

PLANNER = "Bearer planner-token"
FLEET = "lin_api_fleetkey"

AUTH_BODY = json.dumps({
    "errors": [{
        "message": "Authentication required, not authenticated",
        "extensions": {"type": "authentication error",
                       "code": "AUTHENTICATION_ERROR", "statusCode": 401},
    }]
}).encode()

RATELIMIT_BODY = json.dumps({
    "errors": [{
        "message": ("Rate limit exceeded. Only 5000 requests are allowed per 1 "
                    "hour and you have made 5000 requests in the last hour."),
        "extensions": {"code": "RATELIMITED", "statusCode": 429},
    }]
}).encode()

INVALID_BODY = json.dumps({
    "errors": [{"message": "Cannot query field \"nope\" on type \"Query\".",
                "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"}}]
}).encode()

NOTICE = "linear-key: planner-oauth refused (401) — fell back to fleet"


def _headers(remaining=None, limit=None):
    h = {}
    if remaining is not None:
        h["x-ratelimit-requests-remaining"] = str(remaining)
    if limit is not None:
        h["x-ratelimit-requests-limit"] = str(limit)
    return h


class _Resp:
    def __init__(self, body=OK_BODY, headers=None):
        self._body = body
        self.headers = dict(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http(code, body, headers=None):
    return urllib.error.HTTPError(
        linear_ops.API, code, "err", headers or {}, io.BytesIO(body)
    )


class _Transport:
    """urlopen stand-in: one outcome per call, and the Authorization header of
    every request it was handed — that list is how "which key" is measured."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.keys = []

    def __call__(self, req, *_a, **_k):
        self.keys.append(req.get_header("Authorization"))
        outcome = self.outcomes[min(len(self.keys) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def planner(monkeypatch):
    """A planner step as plan.yml builds it, with the token published."""
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", PLANNER)
    monkeypatch.setenv(linear_ops.FALLBACK_ENV, FLEET)
    monkeypatch.setenv(linear_ops.IDENTITY_ENV, "fleet")
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-oauth")

    def _install(*outcomes):
        t = _Transport(*outcomes)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        return t

    return _install


# ── the fallback ────────────────────────────────────────────────────────────
def test_a_401_retries_that_request_once_on_the_fleet_key_and_stays_there(planner, capsys):
    t = planner(_http(401, AUTH_BODY), _Resp(), _Resp())
    assert linear_ops.gql(QUERY) == {"viewer": {"id": "u1"}}
    assert linear_ops.gql(QUERY) == {"viewer": {"id": "u1"}}
    assert t.keys == [PLANNER, FLEET, FLEET]
    err = capsys.readouterr().err
    assert err.count("linear-key:") == 1
    assert NOTICE in err


def test_the_notice_never_carries_a_key(planner, capsys):
    planner(_http(401, AUTH_BODY), _Resp())
    linear_ops.gql(QUERY)
    err = capsys.readouterr().err
    assert "planner-token" not in err and FLEET not in err


def test_an_authentication_error_body_on_a_400_falls_back_too(planner, capsys):
    """Linear can answer a refused key with a 400 whose body says
    AUTHENTICATION_ERROR — the body, not the status, is the classification,
    the same rule the rate limit follows (DRE-2923)."""
    t = planner(_http(400, AUTH_BODY), _Resp())
    linear_ops.gql(QUERY)
    assert t.keys == [PLANNER, FLEET]
    assert "linear-key: planner-oauth refused (AUTHENTICATION_ERROR) — fell back to fleet" in (
        capsys.readouterr().err
    )


def test_an_authentication_error_in_a_200_errors_payload_falls_back_too(planner):
    t = planner(_Resp(AUTH_BODY), _Resp())
    assert linear_ops.gql(QUERY) == {"viewer": {"id": "u1"}}
    assert t.keys == [PLANNER, FLEET]


# ── never on anything else ──────────────────────────────────────────────────
def test_a_plain_400_never_falls_back(planner, capsys):
    t = planner(_http(400, INVALID_BODY), _Resp())
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]
    assert "linear-key:" not in capsys.readouterr().err


def test_a_ratelimited_400_never_falls_back(planner, capsys):
    t = planner(_http(400, RATELIMIT_BODY), _Resp())
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]
    assert "linear-key:" not in capsys.readouterr().err


def test_a_429_never_falls_back(planner, capsys):
    t = planner(_http(429, RATELIMIT_BODY), _Resp())
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]
    assert "linear-key:" not in capsys.readouterr().err


def test_a_ratelimited_200_payload_never_falls_back(planner):
    t = planner(_Resp(RATELIMIT_BODY), _Resp())
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]


def test_a_fallback_equal_to_the_primary_does_nothing(planner, monkeypatch, capsys):
    """No planner key published: `||` already chose the fleet key, and its
    401 is the fleet key's own — there is nothing different to fall back to."""
    monkeypatch.setenv("LINEAR_API_KEY", FLEET)
    t = planner(_http(401, AUTH_BODY), _Resp())
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.keys == [FLEET]
    assert "linear-key:" not in capsys.readouterr().err


def test_no_fallback_set_does_nothing(planner, monkeypatch):
    monkeypatch.delenv(linear_ops.FALLBACK_ENV)
    t = planner(_http(401, AUTH_BODY), _Resp())
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]


def test_a_401_on_the_fleet_key_too_is_raised_after_exactly_two_requests(planner, capsys):
    t = planner(_http(401, AUTH_BODY), _http(401, AUTH_BODY), _Resp())
    with pytest.raises(linear_ops.LinearError):
        linear_ops.gql(QUERY)
    assert t.keys == [PLANNER, FLEET]
    assert capsys.readouterr().err.count("linear-key:") == 1


# ── whose bucket the line names ─────────────────────────────────────────────
def test_the_budget_line_names_the_planner_bucket_and_its_limit(planner):
    planner(_Resp(headers=_headers(4998, 5000)), _Resp(headers=_headers(4996, 5000)))
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 4998 → 4996 (spent 2 this run; ")
    assert line.endswith("; limit 5000; budget: planner-oauth)")


def test_after_a_fallback_the_line_names_the_fleet_and_only_its_readings(planner):
    planner(
        _Resp(headers=_headers(4998, 5000)),
        _http(401, AUTH_BODY),
        _Resp(headers=_headers(351, 2500)),
        _Resp(headers=_headers(350, 2500)),
    )
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 351 → 350 (spent 1 this run; ")
    assert line.endswith("; limit 2500; budget: fleet)")


def test_with_no_planner_key_published_the_line_says_fleet(planner, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", FLEET)
    planner(_Resp(headers=_headers(351)))
    linear_ops.gql(QUERY)
    assert linear_ops.budget_line().endswith("; budget: fleet)")


def test_a_home_the_declaration_does_not_carry_is_not_a_bucket(planner, monkeypatch):
    monkeypatch.setenv(linear_ops.HOME_ENV, "planner-typo\nbudget: forged")
    planner(_Resp(headers=_headers(10)))
    linear_ops.gql(QUERY)
    assert linear_ops.budget_line().endswith("; budget: fleet)")


def test_the_refusal_line_names_the_bucket_too(planner):
    planner(_http(400, RATELIMIT_BODY))
    with pytest.raises(linear_ops.LinearRateLimited) as refused:
        linear_ops.gql(QUERY)
    assert str(refused.value).endswith("budget: planner-oauth")


def test_a_fallback_is_process_state_the_test_reset_clears(planner):
    planner(_http(401, AUTH_BODY), _Resp())
    linear_ops.gql(QUERY)
    linear_ops._reset_budget_state()
    t = planner(_Resp())
    linear_ops.gql(QUERY)
    assert t.keys == [PLANNER]
