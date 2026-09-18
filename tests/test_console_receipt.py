"""RED-first: the console receipt, read and verified (DRE-3754, the pipeline half
of DRE-3735).

The CEO, 2026-09-12 21:28 PT: *"If I sign in, I am who I am by definition."*
The console proves who pressed the button (Cognito); what it lacked was a way
to carry that proof onto a Linear comment that the fleet — which holds the only
Linear key the console has — cannot forge. The answer is a signature: the
console holds an Ed25519 private key no workflow or agent can read, signs the
decision's canonical bytes with it, and publishes the public key. This module
is the drain's reader of that signature.

What these tests hold:

  * **The wire format is one set of bytes.** The vector in
    `console_receipt_vectors.py` was signed by `cryptography` (the console's
    library) and is verified here by the runner's `openssl` binary (the drain's
    verifier) — the cross-implementation proof that the two halves agree.
  * **The key comes from ONE place, fixed in code.** No repository or
    organisation variable can substitute a key or a URL: the fleet's GitHub App
    holds `actions_variables: write`, so a variable override is a key the fleet
    could set to its own. A different key is a code change behind the critic.
  * **Every failure refuses.** An unreachable endpoint, a malformed answer, a
    key id that does not match, an openssl that cannot do Ed25519 — each is a
    named refusal, never an acceptance.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_receipt.py -v
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402

OPENSSL = V.capable_openssl()


def _need_openssl():
    """Skip only where no OpenSSL 3 exists at all — a developer's Mac with
    nothing but LibreSSL. CI never skips: an ubuntu runner without a capable
    openssl is a broken runner, and that must be red rather than quiet."""
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")


@pytest.fixture(autouse=True)
def _fresh_probe(monkeypatch):
    """Every test starts with no cached openssl choice, pointed at the capable
    binary this machine has — the module's own probe then runs for real."""
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    if OPENSSL:
        monkeypatch.setenv("OPENSSL_BIN", OPENSSL)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serving(payload, seen=None, status=200):
    def urlopen(request, timeout=None):
        url = getattr(request, "full_url", request)
        if seen is not None:
            seen.append(url)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return FakeResponse(body, status)
    return urlopen


GOOD_KEY = {"alg": "Ed25519", "kid": V.KID, "public_key": V.PUBLIC_KEY_B64}


# --------------------------------------------------------------------------
# the wire format — one set of bytes
# --------------------------------------------------------------------------
def test_the_signed_bytes_are_the_vector_byte_for_byte():
    got = console_receipt.signed_bytes(V.MARKER, V.CARD, V.PROPOSAL, V.USER, V.AT)
    assert got == V.SIGNED_BYTES
    assert hashlib.sha256(got).hexdigest() == V.SIGNED_BYTES_SHA256


def test_the_spec_names_every_line_of_the_signed_bytes():
    """The spec is written once, as a docstring constant the PR body quotes and
    the console half copies. Each label it declares is a label the signer uses."""
    spec = console_receipt.SPEC
    for label in ("bureau-console-receipt/v1", "marker: ", "card: ",
                  "proposal: ", "user: ", "at: ", console_receipt.KEY_URL):
        assert label in spec, f"the spec never says {label!r}"


def test_the_key_id_is_the_first_sixteen_hex_of_the_raw_key_digest():
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    assert key.kid == V.KID
    assert len(key.raw) == 32


def test_the_trailer_the_writer_composes_is_the_vector_line():
    assert console_receipt.trailer(card=V.CARD, proposal=V.PROPOSAL,
                                   user=V.USER, at=V.AT, kid=V.KID,
                                   sig=V.SIG) == V.TRAILER


def test_the_trailer_is_read_off_the_last_line_of_the_comment():
    receipt = console_receipt.parse(V.COMMENT)
    assert receipt is not None
    assert (receipt.card, receipt.proposal, receipt.user, receipt.at,
            receipt.kid, receipt.sig) == (V.CARD, V.PROPOSAL, V.USER, V.AT,
                                          V.KID, V.SIG)
    assert console_receipt.marker_line(V.COMMENT) == V.MARKER


def test_a_trailer_that_is_not_the_last_line_is_no_receipt():
    """Only the last non-empty line is read. A trailer quoted in the middle of
    a comment is text ABOUT a receipt, the same way a marker mid-sentence is
    prose about a proposal."""
    moved = f"{V.MARKER}\n\n{V.TRAILER}\nand then some prose after it"
    assert console_receipt.parse(moved) is None


def test_two_trailer_lines_are_no_receipt():
    doubled = f"{V.COMMENT}\n{V.TRAILER}"
    assert console_receipt.parse(doubled) is None
    assert console_receipt.has_trailer(doubled)


def test_the_emoji_is_optional_on_the_trailer_like_every_marker():
    plain = V.COMMENT.replace("🔏 console-receipt:", "console-receipt:")
    assert console_receipt.parse(plain) is not None


# --------------------------------------------------------------------------
# the signature — verified by the binary the drain actually runs
# --------------------------------------------------------------------------
def test_the_vector_signed_by_cryptography_verifies_through_openssl():
    _need_openssl()
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    ok, why = console_receipt.verify_signature(
        key, V.SIGNED_BYTES, console_receipt.sig_bytes(V.SIG))
    assert ok, why


def test_one_changed_byte_of_the_message_does_not_verify():
    _need_openssl()
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    tampered = V.SIGNED_BYTES.replace(b"declined", b"approved")
    ok, why = console_receipt.verify_signature(
        key, tampered, console_receipt.sig_bytes(V.SIG))
    assert not ok
    assert "signature" in why


def test_an_openssl_that_cannot_do_ed25519_refuses_and_says_so(monkeypatch):
    """The operator's Mac carries LibreSSL as `openssl`, which cannot load an
    Ed25519 key. That must read as a named refusal, never as a bad signature
    and never as a pass."""
    monkeypatch.setenv("OPENSSL_BIN", "/nonexistent/openssl")
    monkeypatch.setattr(console_receipt, "OPENSSL_CANDIDATES",
                        ("/nonexistent/openssl",))
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    ok, why = console_receipt.verify_signature(
        key, V.SIGNED_BYTES, console_receipt.sig_bytes(V.SIG))
    assert not ok
    assert "openssl" in why


def test_a_binary_that_says_yes_to_everything_is_not_trusted(tmp_path, monkeypatch):
    """The capability probe checks a KNOWN-BAD signature fails as well as a
    known-good one passes, so a stub that prints `Signature Verified
    Successfully` and exits 0 is refused rather than believed."""
    liar = tmp_path / "openssl"
    liar.write_text("#!/bin/sh\necho 'Signature Verified Successfully'\nexit 0\n")
    liar.chmod(liar.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("OPENSSL_BIN", str(liar))
    monkeypatch.setattr(console_receipt, "OPENSSL_CANDIDATES", (str(liar),))
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    ok, why = console_receipt.verify_signature(
        key, V.SIGNED_BYTES.replace(b"declined", b"approved"),
        console_receipt.sig_bytes(V.SIG))
    assert not ok
    assert "openssl" in why


# --------------------------------------------------------------------------
# the public key — one fixed source, and every failure refuses
# --------------------------------------------------------------------------
def test_the_key_url_is_the_consoles_published_endpoint():
    assert console_receipt.KEY_URL == "https://app.agent-bureau.com/api/v1/receipt-key"


def test_no_variable_can_move_the_key_or_its_url(monkeypatch):
    """The fleet's GitHub App (`agent-bureau-bot`) holds `actions_variables:
    write`, checked 2026-09-13. A key or URL read from a variable is one the
    fleet could point at its own key — so none is read, whatever is set."""
    seen = []
    monkeypatch.setenv("CONSOLE_RECEIPT_KEY_URL", "https://evil.example/key")
    monkeypatch.setenv("CONSOLE_RECEIPT_PUBLIC_KEY", "AAAA")
    monkeypatch.setenv("CONSOLE_RECEIPT_KEY", "AAAA")
    monkeypatch.setattr(console_receipt, "urlopen", _serving(GOOD_KEY, seen))
    key = console_receipt.fetch_key()
    assert seen == [console_receipt.KEY_URL]
    assert key.kid == V.KID
    source = (ROOT / "scripts" / "console_receipt.py").read_text()
    for name in ("CONSOLE_RECEIPT_KEY", "os.environ.get(\"CONSOLE",
                 "os.getenv(\"CONSOLE"):
        assert name not in source


def test_an_unreachable_endpoint_is_a_named_refusal(monkeypatch):
    def down(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(console_receipt, "urlopen", down)
    with pytest.raises(console_receipt.KeyUnavailable) as err:
        console_receipt.fetch_key()
    assert "connection refused" in str(err.value)


def test_a_plain_http_url_is_refused_before_any_request(monkeypatch):
    seen = []
    monkeypatch.setattr(console_receipt, "urlopen", _serving(GOOD_KEY, seen))
    with pytest.raises(console_receipt.KeyUnavailable):
        console_receipt.fetch_key("http://app.agent-bureau.com/api/v1/receipt-key")
    assert seen == []


@pytest.mark.parametrize("payload, needle", [
    (b"<html>sign in</html>", "JSON"),
    ({"alg": "RS256", "kid": V.KID, "public_key": V.PUBLIC_KEY_B64}, "Ed25519"),
    ({"alg": "Ed25519", "kid": V.KID, "public_key": "AAAA"}, "32"),
    ({"alg": "Ed25519", "kid": "0000000000000000",
      "public_key": V.PUBLIC_KEY_B64}, "kid"),
])
def test_a_wrong_answer_from_the_endpoint_is_a_named_refusal(monkeypatch, payload,
                                                             needle):
    monkeypatch.setattr(console_receipt, "urlopen", _serving(payload))
    with pytest.raises(console_receipt.KeyUnavailable) as err:
        console_receipt.fetch_key()
    assert needle in str(err.value)


def test_a_non_200_answer_is_a_named_refusal(monkeypatch):
    # A 503 is a gateway that was not there for one request, so the bounded
    # retry (DRE-4153) asks again — the backoff is swallowed here, and
    # tests/test_unchecked_console_answer.py owns what the retry must do.
    monkeypatch.setattr(console_receipt.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(console_receipt, "urlopen", _serving(GOOD_KEY, status=503))
    with pytest.raises(console_receipt.KeyUnavailable) as err:
        console_receipt.fetch_key()
    assert "503" in str(err.value)


# --------------------------------------------------------------------------
# the whole check, one comment at a time
# --------------------------------------------------------------------------
def _verifier(loader=None):
    return console_receipt.Verifier(
        key_loader=loader or (lambda: console_receipt.PublicKey.from_b64(
            V.PUBLIC_KEY_B64)))


CREATED = "2026-09-13T15:15:03.412Z"   # Linear's own stamp, a second after `at`


def test_the_vector_comment_passes_the_whole_check():
    _need_openssl()
    why = _verifier().check(V.COMMENT, card=V.CARD, proposal=V.PROPOSAL,
                            created_at=CREATED)
    assert why is None


def test_the_key_is_fetched_once_per_run_and_a_failure_is_remembered():
    calls = []

    def loader():
        calls.append(1)
        raise console_receipt.KeyUnavailable("down")
    verifier = _verifier(loader)
    for _ in range(3):
        why = verifier.check(V.COMMENT, card=V.CARD, proposal=V.PROPOSAL,
                             created_at=CREATED)
        assert "down" in why
        assert console_receipt.KEY_URL in why
    assert calls == [1]


@pytest.mark.parametrize("kwargs, needle", [
    (dict(card="DRE-9999"), "DRE-9999"),
    (dict(proposal="ffffffffffff"), "ffffffffffff"),
    (dict(created_at="2026-09-13T16:15:03.000Z"), "stale"),
    (dict(created_at="2026-09-13T15:05:00.000Z"), "before"),
    (dict(created_at=None), "time"),
])
def test_a_receipt_off_its_own_card_proposal_or_time_is_refused(kwargs, needle):
    base = dict(card=V.CARD, proposal=V.PROPOSAL, created_at=CREATED)
    base.update(kwargs)
    why = _verifier().check(V.COMMENT, **base)
    assert why is not None
    assert needle in why


def test_a_comment_with_no_trailer_says_it_carries_no_receipt():
    why = _verifier().check(V.MARKER, card=V.CARD, proposal=V.PROPOSAL,
                            created_at=CREATED)
    assert why and "no console receipt" in why


def test_a_receipt_signed_by_another_key_is_refused():
    _need_openssl()
    other = console_receipt.PublicKey(bytes(range(32)))
    forged = V.COMMENT.replace(f"kid={V.KID}", f"kid={other.kid}")
    why = _verifier(lambda: other).check(forged, card=V.CARD,
                                         proposal=V.PROPOSAL, created_at=CREATED)
    assert why and "signature" in why


def test_a_receipt_naming_a_key_id_the_console_does_not_publish_is_refused():
    forged = V.COMMENT.replace(f"kid={V.KID}", "kid=00000000deadbeef")
    why = _verifier().check(forged, card=V.CARD, proposal=V.PROPOSAL,
                            created_at=CREATED)
    assert why and "00000000deadbeef" in why


def test_an_edited_marker_line_no_longer_verifies():
    """The fleet can EDIT a comment it authored. Turning the console's decline
    into an approval changes the signed marker line, so it stops verifying."""
    _need_openssl()
    edited = V.COMMENT.replace("groom-declined", "groom-approved", 1)
    why = _verifier().check(edited, card=V.CARD, proposal=V.PROPOSAL,
                            created_at=CREATED)
    assert why and "signature" in why
