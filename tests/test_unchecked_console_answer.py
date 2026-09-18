"""RED-first: a CEO answer whose receipt COULD NOT BE CHECKED is not a refused
one, and the key fetch retries before it gives up (DRE-4153).

Origin (2026-09-17 09:38 PT, DRE-4141). The CEO's correctly signed console
answer landed on the card. Three minutes later the plan critic sent the card
back — its second send-back, the bound — because "the one attempt to check his
signed answer failed". The answer was valid: re-checked read-only against
`stable`'s `console_receipt.py`, the published key and the comment exactly as
Linear returns it, the sha256 matches the receipt and `check_answer` passes.

What failed was the KEY FETCH. The console proxy logged
`GET /api/v1/receipt-key … durationMs 10009, aborted: true` while the console
backend wrote nothing at all — health checks included — for ~40 seconds. The
same thing had happened the day before (run 35162071431, DRE-3938), with the
reason visible on the card: `REFUSED — the console's public key could not be
read … TimeoutError: The read operation timed out`. Over ~27 h, 2 of 50 key
requests aborted at the 10 s limit and one took 8.45 s.

Two defects, one consequence — a real decision did not stick:

  1. `fetch_key` made ONE request and `Verifier.key()` cached that failure as
     the reason for every receipt in the run. One slow request was the whole
     answer.
  2. `spoken_thread.voices` filed "the key could not be read" under **REFUSED**
     — the same label as a signature that does not verify — so a genuine answer
     read to the critic as a forged voice.

What these tests hold:

  * **The fetch retries, bounded.** A key URL that times out once and then
    answers verifies the receipt; one that never answers costs a pinned number
    of attempts and a pinned total wait, and nothing more.
  * **"Could not be checked" is its own kind.** An unreadable key yields
    UNCHECKED, whose label says the check could not run, whose text is
    withheld, and which is never counted as the CEO's voice.
  * **REFUSED still means refused.** A receipt checked against a readable key
    that does not verify is REFUSED, exactly as before — the two facts have
    different next actions and must never share a label.

Run: cd bureau-pipeline && python3 -m pytest tests/test_unchecked_console_answer.py -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LINEAR_API_KEY", "test-key")
os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")

import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402
import linear_ops  # noqa: E402
import spoken_thread  # noqa: E402

OPENSSL = V.capable_openssl()
FLEET = "user-agent-bureau"
CARD = V.ANSWER_CARD
#: Linear's stamp on the vector answer — five seconds after it was signed.
POSTED = "2026-09-13T16:52:12.204Z"
CEO_LABEL = "the CEO, via the console, at 2026-09-13 09:52 PT"
GOOD_KEY = {"alg": "Ed25519", "kid": V.KID, "public_key": V.PUBLIC_KEY_B64}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """No cached openssl choice and no verifier carried over from another
    test — every test here drives the key fetch itself."""
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")
    monkeypatch.setenv("OPENSSL_BIN", OPENSSL)
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    monkeypatch.setattr(spoken_thread, "_VERIFIER", None, raising=False)
    linear_ops.reset_pass_cache()


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Transport:
    """A `urlopen` stand-in driven by one outcome per call: an exception is
    raised, anything else is served as the endpoint's body. Counts its calls
    and records the timeout each one asked for — the fetch's own return value
    cannot tell one attempt from three."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.timeouts: list[float] = []
        self.sleeps: list[float] = []

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        body = outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode()
        return _Response(body)


@pytest.fixture
def transport(monkeypatch):
    """Installs a transport and swallows the backoff, so a bounded-retry test
    costs no wall clock. The recorded sleeps are asserted on directly."""
    sleeps: list[float] = []
    monkeypatch.setattr(console_receipt.time, "sleep",
                        lambda seconds: sleeps.append(seconds))

    def _install(*outcomes):
        t = _Transport(*outcomes)
        t.sleeps = sleeps
        monkeypatch.setattr(console_receipt, "urlopen", t)
        return t
    return _install


def _timeout():
    """The 09:39:25 PT fault: the proxy aborted the read at the 10 s limit.
    `socket.timeout` IS `TimeoutError` since 3.10."""
    return TimeoutError("The read operation timed out")


def _http_error(code: int):
    return urllib.error.HTTPError(console_receipt.KEY_URL, code, "reason", {},
                                  io.BytesIO(b"{}"))


def node(body, *, by=FLEET, at=POSTED):
    return {"body": body, "createdAt": at, "user": {"id": by} if by else None}


def _read_one(body, *, card=CARD):
    """The one comment, read by a verifier that has fetched nothing yet."""
    return spoken_thread.voices([node(body)], FLEET, card=card,
                                verifier=console_receipt.Verifier())


def _render(mode, *nodes):
    thread = {"viewer": {"id": FLEET},
              "issue": {"comments": {"nodes": list(reversed(nodes))}}}
    with patch.object(linear_ops, "gql", return_value=thread):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert spoken_thread.main([mode, CARD]) == 0
    return buf.getvalue()


# --------------------------------------------------------------------------
# 1. the key fetch retries — one slow request is not an answer
# --------------------------------------------------------------------------
def test_a_key_fetch_that_times_out_once_and_then_answers_is_retried(transport):
    t = transport(_timeout(), GOOD_KEY)
    assert console_receipt.fetch_key().kid == V.KID
    assert t.calls == 2, "the timed-out fetch must be asked again"
    assert t.sleeps == [console_receipt.KEY_FETCH_BACKOFF_SECONDS], (
        "re-firing with no pause at a console that just aborted is not a retry")


def test_a_valid_answer_verifies_when_the_key_fetch_times_out_once(transport):
    """The headline, end to end: this is DRE-4141's answer, and today's code
    reads it as REFUSED because the one key fetch was slow."""
    t = transport(_timeout(), GOOD_KEY)
    [voice] = _read_one(V.ANSWER_COMMENT)
    assert voice.kind == spoken_thread.CEO_VIA_CONSOLE, voice.why
    assert voice.label == CEO_LABEL
    assert voice.body == V.ANSWER_COMMENT
    assert t.calls == 2


def test_the_retry_is_logged_in_one_line(transport, capsys):
    """A run that needed the retry says so — otherwise the only evidence the
    console stalled is a step that took a second longer than usual."""
    transport(_timeout(), GOOD_KEY)
    console_receipt.fetch_key()
    lines = [ln for ln in capsys.readouterr().err.splitlines()
             if "console key fetch" in ln]
    assert len(lines) == 1, lines
    assert "The read operation timed out" in lines[0]


def test_a_fetch_that_answers_first_time_retries_nothing(transport, capsys):
    """The receipt must mean something: no fault, no retry, no line."""
    t = transport(GOOD_KEY)
    assert console_receipt.fetch_key().kid == V.KID
    assert t.calls == 1 and t.sleeps == []
    assert "console key fetch" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# 2. …and the retry is BOUNDED — in attempts and in total wait
# --------------------------------------------------------------------------
def test_a_key_url_that_never_answers_is_bounded_in_count_and_in_wait(transport):
    t = transport(_timeout())
    with pytest.raises(console_receipt.KeyUnavailable):
        console_receipt.fetch_key()
    assert console_receipt.KEY_FETCH_ATTEMPTS == 3
    assert console_receipt.KEY_FETCH_BACKOFF_SECONDS == 1.0
    assert t.calls == console_receipt.KEY_FETCH_ATTEMPTS, "one fetch, three asks"
    assert t.timeouts == [console_receipt.KEY_FETCH_TIMEOUT_SECONDS] * 3
    assert t.sleeps == [console_receipt.KEY_FETCH_BACKOFF_SECONDS] * 2
    # The whole wait, pinned as a number rather than described as "short".
    assert console_receipt.KEY_FETCH_MAX_WAIT_SECONDS == 32.0
    assert sum(t.timeouts) + sum(t.sleeps) == \
        console_receipt.KEY_FETCH_MAX_WAIT_SECONDS


def test_a_key_url_that_never_answers_is_never_the_ceos_voice(transport):
    t = transport(_timeout())
    [voice] = _read_one(V.ANSWER_COMMENT)
    assert voice.kind != spoken_thread.CEO_VIA_CONSOLE
    assert voice.kind == spoken_thread.UNCHECKED
    assert voice.body is None, "an unchecked answer's text must not reach the agent"
    assert t.calls == console_receipt.KEY_FETCH_ATTEMPTS


def test_a_run_pays_the_bounded_wait_once_however_many_answers_it_reads(transport):
    """`Verifier.key()` remembers the failure. Three answer receipts in one
    thread cost one bounded fetch between them, not three."""
    t = transport(_timeout())
    voices = spoken_thread.voices(
        [node(V.ANSWER_COMMENT), node(V.ANSWER_COMMENT),
         node(V.ANSWER_COMMENT)], FLEET, card=CARD,
        verifier=console_receipt.Verifier())
    assert [v.kind for v in voices] == [spoken_thread.UNCHECKED] * 3
    assert t.calls == console_receipt.KEY_FETCH_ATTEMPTS


@pytest.mark.parametrize("outcome, needle", [
    (_http_error(404), "404"),
    (urllib.error.URLError("unknown url type: htp"), "unknown url type"),
    (b"<html>sign in</html>", "JSON"),
    ({"alg": "RS256", "kid": V.KID, "public_key": V.PUBLIC_KEY_B64}, "Ed25519"),
    ({"alg": "Ed25519", "kid": V.KID, "public_key": "AAAA"}, "32"),
])
def test_a_fault_a_retry_cannot_clear_is_asked_once(transport, outcome, needle):
    """The retry buys the fault that heals itself. A 404, a malformed key, a
    URL error that is a programming mistake wearing a network error's type —
    asking again only spends the wait (DRE-3087's rule, same as the sweep's)."""
    t = transport(outcome)
    with pytest.raises(console_receipt.KeyUnavailable) as err:
        console_receipt.fetch_key()
    assert needle in str(err.value)
    assert t.calls == 1, f"{needle}: a second ask cannot change this answer"
    assert t.sleeps == []


@pytest.mark.parametrize("exc, expected", [
    (ConnectionResetError(104, "Connection reset by peer"), True),
    (TimeoutError("The read operation timed out"), True),
    (urllib.error.URLError(TimeoutError("timed out")), True),
    (urllib.error.URLError("unknown url type"), False),
    (_http_error(502), True),
    (_http_error(503), True),
    (_http_error(504), True),
    (_http_error(400), False),
    (_http_error(429), False),
    (_http_error(500), False),
    (ValueError("not a network fault"), False),
])
def test_the_key_fetchs_classifier_answers_exactly_as_the_sweeps(exc, expected):
    """Two copies of one rule drift. The sweep's classifier (DRE-3087) is the
    original; this pins the key fetch's to the same answers, shape by shape,
    so a change to either must come past this test."""
    assert console_receipt.is_transient(exc) is expected
    assert linear_ops.is_transient(exc) is expected


# --------------------------------------------------------------------------
# 3. an unreadable key is COULD NOT BE CHECKED — never REFUSED
# --------------------------------------------------------------------------
def test_an_unreadable_key_yields_the_unchecked_kind_and_says_the_check_could_not_run(
        transport):
    transport(_timeout())
    [voice] = _read_one(V.ANSWER_COMMENT)
    assert voice.kind == spoken_thread.UNCHECKED
    assert voice.kind != spoken_thread.REFUSED
    assert "the check could not run" in voice.label
    assert console_receipt.is_unchecked(voice.why)
    assert console_receipt.KEY_URL in voice.why


def test_the_unchecked_reason_is_recorded_in_the_step_log(transport, capsys):
    transport(_timeout())
    _read_one(V.ANSWER_COMMENT)
    logged = capsys.readouterr().err
    assert "COULD NOT BE CHECKED" in logged and CARD in logged
    assert "REFUSED" not in logged, (
        "an unreadable key must never be logged as a refusal")


def test_the_people_render_names_the_unchecked_answer_and_withholds_its_text(
        transport):
    transport(_timeout())
    out = _render("people", node(V.ANSWER_COMMENT))
    assert "COULD NOT BE CHECKED" in out
    assert "REFUSED" not in out, "the console being slow is not a forged voice"
    assert "Go with option B" not in out, "the unchecked words reached the agent"
    assert "could not be read" in out, "the render must say what went wrong"


def test_the_status_line_counts_the_unchecked_answer_and_not_the_ceo(transport):
    transport(_timeout())
    out = _render("people", node(V.ANSWER_COMMENT))
    status = [ln for ln in out.splitlines()
              if ln.startswith(spoken_thread.STATUS)]
    assert len(status) == 1, status
    assert "0 the CEO's" in status[0], "an unchecked answer is not his voice"
    assert "COULD NOT BE CHECKED" in status[0]


def test_the_thread_render_labels_the_unchecked_answer_too(transport):
    transport(_timeout())
    out = _render("thread", node(V.ANSWER_COMMENT))
    assert "COULD NOT BE CHECKED" in out
    assert "Go with option B" not in out


def test_the_peoples_rule_tells_the_agent_what_the_new_heading_means(transport):
    """The heading only helps if the block that explains the headings names
    it — that rule is the whole of what the agent has to read it by."""
    assert "could not be checked" in spoken_thread._PEOPLE_RULE.lower()
    transport(_timeout())
    assert spoken_thread._PEOPLE_RULE in _render("people", node(V.ANSWER_COMMENT))


# --------------------------------------------------------------------------
# 4. a receipt that WAS checked and failed is still REFUSED
# --------------------------------------------------------------------------
def test_a_wrong_signature_against_a_readable_key_is_still_refused(transport):
    """The other half of the split: the key read fine, the signature was
    checked, and it does not verify. That is a forged voice and keeps its
    label."""
    t = transport(GOOD_KEY)
    forged = V.ANSWER_COMMENT.replace(f"sig={V.ANSWER_SIG}", f"sig={V.SIG}")
    [voice] = _read_one(forged)
    assert voice.kind == spoken_thread.REFUSED
    assert voice.kind != spoken_thread.UNCHECKED
    assert not console_receipt.is_unchecked(voice.why)
    assert "does not verify" in voice.why
    assert voice.body is None
    assert t.calls == 1, "the key was readable — nothing to retry"


def test_an_edited_answer_is_refused_and_never_costs_a_key_fetch(transport):
    """A comment that fails a cheap check is refused before the key is asked
    for, so an unreadable key cannot turn a real forgery into an unchecked
    one."""
    t = transport(_timeout())
    edited = V.ANSWER_COMMENT.replace("option B", "option A", 1)
    [voice] = _read_one(edited)
    assert voice.kind == spoken_thread.REFUSED
    assert "changed after" in voice.why
    assert t.calls == 0, "a comment that is obviously not his cost a key fetch"


def test_the_people_render_still_refuses_a_forged_answer(transport):
    transport(GOOD_KEY)
    forged = V.ANSWER_COMMENT.replace(f"sig={V.ANSWER_SIG}", f"sig={V.SIG}")
    out = _render("people", node(forged))
    assert "REFUSED" in out
    assert "COULD NOT BE CHECKED" not in out
    assert "Go with option B" not in out


# --------------------------------------------------------------------------
# 5. every reader of the voice kinds handles the new one
# --------------------------------------------------------------------------
def test_the_new_kind_is_distinct_from_every_other():
    kinds = {spoken_thread.CEO_VIA_CONSOLE, spoken_thread.PIPELINE,
             spoken_thread.PERSON, spoken_thread.INTEGRATION,
             spoken_thread.UNKNOWN, spoken_thread.REFUSED,
             spoken_thread.UNCHECKED}
    assert len(kinds) == 7, "the new kind must be its own value, not an alias"


def test_spoken_thread_is_the_only_reader_of_the_voice_kinds():
    """The card asks that EVERY reader of the kinds handles the new one. The
    renders and the counts in `spoken_thread.py` are the whole set — pinned
    here, so a second reader added later comes past this test rather than
    silently missing UNCHECKED."""
    pattern = re.compile(
        r"spoken_thread\.(CEO_VIA_CONSOLE|PIPELINE|PERSON|INTEGRATION|"
        r"UNKNOWN|REFUSED|UNCHECKED)\b")
    readers = sorted(path.name for path in (ROOT / "scripts").glob("*.py")
                     if path.name != "spoken_thread.py"
                     and pattern.search(path.read_text(encoding="utf-8")))
    assert readers == [], (
        f"{readers} now read the voice kinds and must handle UNCHECKED — an "
        "unchecked answer is not a refused one")


def test_every_kind_a_render_can_meet_is_rendered(transport):
    """Nothing falls through unlabelled: one thread carrying all of them, and
    every voice appears in the thread render under a heading of its own."""
    transport(_timeout())
    forged = V.ANSWER_COMMENT.replace("option B", "option A", 1)
    out = _render("thread", node("⏳ 1/5 reading the card"),
                  node("Go with B.", by="user-frederick"),
                  node("synced from GitHub", by=None),
                  node(forged), node(V.ANSWER_COMMENT))
    headings = [ln for ln in out.splitlines() if ln.startswith("### ")]
    assert len(headings) == 5, headings
    assert any("the pipeline" in h for h in headings)
    assert any("a person, in Linear" in h for h in headings)
    assert any("an integration" in h for h in headings)
    assert any("REFUSED" in h and "COULD NOT BE CHECKED" not in h
               for h in headings)
    assert any("COULD NOT BE CHECKED" in h for h in headings)
