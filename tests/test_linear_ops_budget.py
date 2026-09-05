"""The Linear seam reports its budget, and refuses after the first RATELIMITED
(DRE-3202).

Origin (2026-09-05): the workspace's 2,500-requests-per-hour quota ran dry
twice in one day, and nothing could say which run spent it. Every pipeline
script talks to Linear through ONE seam — `linear_ops.gql` — and Linear
answers every call with two headers that say exactly where the budget stands:

    x-ratelimit-requests-remaining   what is left in the window
    x-ratelimit-requests-reset       when the window rolls (epoch ms)

So the seam remembers the FIRST and LAST `remaining` it saw, counts the
requests it sent, and on exit prints ONE line:

    linear-budget: <first> → <last> (spent <N> this run; window resets <HH:MM> PT)

Once a process is rate-limited it stops asking: every later call raises
`LinearRateLimited` at once, with no request sent — asking again spends the
request that proves there are none left, and a sweep that keeps going after
its first RATELIMITED burns the rest of the fleet's hour for nothing.

The line goes to STDERR, not stdout, deliberately: `children`, `count-comments`,
`find-open`, `description` and `children-detail` are read through `$(...)` and
`> file` by the workflows, so a trailer on stdout would corrupt a parsed
answer. `gh run view --log` shows both streams, so the reader
(`check_linear_budget.py`) sees it either way.
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
import medic_classify  # noqa: E402

QUERY = "query { issues { nodes { id } } }"

OK_BODY = json.dumps({"data": {"issues": {"nodes": [{"id": "abc"}]}}}).encode()

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

# 2026-09-05 22:00:00 UTC — September is PDT (UTC-7), so 15:00 PT. Hard-coded
# rather than derived, so the test checks the conversion instead of mirroring it.
RESET_MS = 1788645600000
RESET_PT = "15:00"


def _headers(remaining: int | None, reset_ms: int | None = RESET_MS) -> dict:
    h = {}
    if remaining is not None:
        h["x-ratelimit-requests-remaining"] = str(remaining)
    if reset_ms is not None:
        h["x-ratelimit-requests-reset"] = str(reset_ms)
    return h


class _Resp:
    """What urlopen hands back on a 200 — a context manager over the body,
    carrying the response headers the way an HTTPResponse does."""

    def __init__(self, body: bytes = OK_BODY, headers: dict | None = None):
        self._body = body
        self.headers = dict(headers or {})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _Transport:
    """A urlopen stand-in driven by a list of outcomes, one per call: an
    exception instance is raised, anything else is returned. Counts its calls —
    that count is how "no request was sent" is measured."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _ratelimited_400(headers: dict | None = None):
    """The exact wire shape (DRE-2923): HTTP 400, RATELIMITED in the body, and
    the budget headers on the error response too."""
    return urllib.error.HTTPError(
        linear_ops.API, 400, "Bad Request", headers or {}, io.BytesIO(RATELIMIT_BODY)
    )


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(linear_ops.time, "sleep", lambda _s: None)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")

    def _install(*outcomes):
        t = _Transport(*outcomes)
        monkeypatch.setattr(linear_ops.urllib.request, "urlopen", t)
        return t

    return _install


# ── (a) the budget line, exact ──────────────────────────────────────────────
def test_budget_line_reports_first_last_spent_and_reset_in_pt(transport):
    transport(
        _Resp(headers=_headers(2400)),
        _Resp(headers=_headers(2399)),
        _Resp(headers=_headers(2397)),
    )
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    assert linear_ops.budget_line() == (
        f"linear-budget: 2400 → 2397 (spent 3 this run; window resets {RESET_PT} PT)"
    )


def test_budget_line_names_no_key_and_no_url(transport, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_SECRETVALUE")
    transport(_Resp(headers=_headers(10)))
    linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert "SECRETVALUE" not in line
    assert "http" not in line and "api.linear.app" not in line


def test_a_window_that_rolls_mid_run_says_so_instead_of_a_negative_number(transport):
    """`remaining` going UP is the one unambiguous sign the window rolled.
    (The reset epoch moving is NOT read as a roll: Linear documents a leaky
    bucket and does not promise that value holds still within a window.)
    It must never become a negative spend."""
    transport(
        _Resp(headers=_headers(5, RESET_MS)),
        _Resp(headers=_headers(2499, RESET_MS + 3_600_000)),
    )
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 5 → 2499 (window rolled")
    assert "-" not in line.split("(")[1]
    assert "spent" not in line


def test_a_moving_reset_epoch_alone_is_not_a_roll(transport):
    """A leaky bucket's reset can drift per request. Only `remaining` going
    up says the window rolled; a drifting clock with a falling `remaining`
    is an ordinary spend, and the LATEST reset is the one on the line."""
    transport(
        _Resp(headers=_headers(2400, RESET_MS)),
        _Resp(headers=_headers(2398, RESET_MS + 250)),
    )
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    assert linear_ops.budget_line() == (
        f"linear-budget: 2400 → 2398 (spent 2 this run; window resets {RESET_PT} PT)"
    )


# ── (b) the first RATELIMITED stops the process asking ──────────────────────
def test_after_one_ratelimited_the_next_call_is_refused_without_a_request(transport):
    t = transport(_ratelimited_400(_headers(0)))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.calls == 1

    with pytest.raises(linear_ops.LinearRateLimited) as refused:
        linear_ops.gql(QUERY)
    assert t.calls == 1, "a rate-limited process must not send another request"
    message = str(refused.value)
    assert "refused after 1 calls" in message
    assert f"{RESET_PT} PT" in message


def test_the_refusal_still_classifies_for_the_medic(transport):
    """The medic reads `api.linear.app` and the condition on ONE line to file a
    run as `linear_ratelimited` (back off) rather than `normal` (retry into the
    exhausted quota — the DRE-1921 loop). A refusal is that condition too."""
    transport(_ratelimited_400(_headers(0)))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    with pytest.raises(linear_ops.LinearRateLimited) as refused:
        linear_ops.gql(QUERY)
    assert medic_classify.is_linear_rate_limited(str(refused.value))


def test_a_200_with_a_ratelimited_errors_payload_also_arms_the_refusal(transport):
    """The quota exhaustion can arrive as a 200 with an errors payload; the
    stop is keyed on the classification, not the status."""
    t = transport(_Resp(RATELIMIT_BODY, _headers(0)))
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    assert t.calls == 1


def test_the_budget_line_after_a_ratelimit_says_refused_after_n_calls(transport):
    transport(
        _Resp(headers=_headers(2)),
        _Resp(headers=_headers(1)),
        _ratelimited_400(_headers(0)),
    )
    linear_ops.gql(QUERY)
    linear_ops.gql(QUERY)
    with pytest.raises(linear_ops.LinearRateLimited):
        linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 2 → 0 (spent 2 this run; ")
    assert f"window resets {RESET_PT} PT" in line
    assert "refused after 3 calls" in line


# ── (c) no headers ──────────────────────────────────────────────────────────
def test_no_headers_reports_unknown(transport):
    transport(_Resp())
    linear_ops.gql(QUERY)
    assert linear_ops.budget_line() == (
        "linear-budget: unknown (no rate-limit headers seen)"
    )


def test_a_transport_without_a_headers_attribute_is_tolerated(transport):
    """The older test fakes (and any stand-in) have no `.headers` at all. The
    seam must degrade to `unknown`, never fail a healthy call over telemetry."""

    class _Bare:
        def read(self):
            return OK_BODY

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    transport(_Bare())
    assert linear_ops.gql(QUERY) == {"issues": {"nodes": [{"id": "abc"}]}}
    assert linear_ops.budget_line().startswith("linear-budget: unknown")


def test_remaining_without_a_reset_header_does_not_crash(transport):
    transport(_Resp(headers=_headers(7, None)))
    linear_ops.gql(QUERY)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 7 → 7 (spent 0 this run; ")
    assert "window resets unknown" in line


# ── (d) healthy calls are unchanged ─────────────────────────────────────────
def test_two_healthy_calls_still_send_exactly_two_requests(transport):
    t = transport(_Resp(headers=_headers(100)), _Resp(headers=_headers(99)))
    assert linear_ops.gql(QUERY) == {"issues": {"nodes": [{"id": "abc"}]}}
    assert linear_ops.gql(QUERY) == {"issues": {"nodes": [{"id": "abc"}]}}
    assert t.calls == 2


# ── the exit hook ───────────────────────────────────────────────────────────
def test_the_exit_hook_prints_the_line_once_on_stderr_when_a_call_was_made(
    transport, capsys
):
    transport(_Resp(headers=_headers(50)))
    linear_ops.gql(QUERY)
    linear_ops._report_budget_at_exit()
    linear_ops._report_budget_at_exit()
    out, err = capsys.readouterr()
    assert out == "", "stdout is parsed by $(...) callers — the line must not land there"
    assert err.count("linear-budget:") == 1
    assert "50 → 50 (spent 0 this run" in err


def test_the_exit_hook_is_silent_when_no_linear_call_was_made(capsys):
    linear_ops._report_budget_at_exit()
    out, err = capsys.readouterr()
    assert out == "" and err == ""


def test_the_exit_hook_is_registered_with_atexit():
    import atexit  # noqa: F401 — the registration is what is pinned

    with open(linear_ops.__file__, encoding="utf-8") as f:
        source = f.read()
    assert "atexit.register(_report_budget_at_exit)" in source


# ── DRE-3224: a refill mid-run is not a rolled window ───────────────────────
# Seen live on the channel (agent-bureau reconcile, 2026-09-05): a sweep that
# went 1675 → 1605 printed `window rolled`, and so did 2186 → 2118. A leaky
# bucket refills WHILE a long sweep runs, so a reading above the previous one
# is ordinary; only a run that ENDS above where it started has rolled.
def _sequence(transport, *readings):
    """One response per reading, the reset epoch drifting a few seconds
    between responses, the way the bucket's reset slides as it refills."""
    transport(
        *[
            _Resp(headers=_headers(remaining, RESET_MS + i * 3_000))
            for i, remaining in enumerate(readings)
        ]
    )
    for _ in readings:
        linear_ops.gql(QUERY)


def test_run_33994820702_1675_to_1605_is_a_spend_of_70_not_a_roll(transport):
    _sequence(transport, 1675, 1640, 1652, 1620, 1605)
    line = linear_ops.budget_line()
    assert "window rolled" not in line
    assert line.startswith("linear-budget: 1675 → 1605 (spent 70 this run")


def test_run_at_16_05_pt_2186_to_2118_is_a_spend_of_68_not_a_roll(transport):
    _sequence(transport, 2186, 2150, 2158, 2118)
    line = linear_ops.budget_line()
    assert "window rolled" not in line
    assert line.startswith("linear-budget: 2186 → 2118 (spent 68 this run")


def test_a_mid_run_refill_that_still_ends_lower_says_so_after_the_number(transport):
    """The number is an honest lower bound; the refill is named so the reader
    knows why the run's true spend is higher than first − last."""
    _sequence(transport, 1675, 1640, 1652, 1605)
    reset_pt = linear_ops._reset_clock()
    assert linear_ops.budget_line() == (
        f"linear-budget: 1675 → 1605 (spent 70 this run (refilled mid-run); "
        f"window resets {reset_pt} PT)"
    )


def test_a_plain_decrease_carries_no_refill_note(transport):
    _sequence(transport, 1862, 1820, 1784)
    line = linear_ops.budget_line()
    assert "refilled" not in line and "spent 78 this run;" in line


def test_a_genuine_roll_that_ends_above_the_start_still_reports_window_rolled(transport):
    _sequence(transport, 5, 3, 2499, 2497)
    line = linear_ops.budget_line()
    assert line.startswith("linear-budget: 5 → 2497 (window rolled;")
    assert "spent" not in line and "refilled" not in line
