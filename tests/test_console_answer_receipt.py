"""RED-first: the console ANSWER receipt, read and verified (DRE-3785).

The CEO, 2026-09-13 09:50 PT: *"a"* — when he answers a question in Green
Light, the console posts his words on the card, signed as him, and the agent
reads them and carries on, with no pasted key.

The console holds only the fleet's Linear key, so the answer it posts is
AUTHORED by `Agent-Bureau` — the pipeline's own identity, which can write any
comment it likes. What makes the words the CEO's is the console's Ed25519
signature on the comment's last line, made with the key DRE-3755 put in the
console backend and nowhere else. This file holds the answer receipt's wire
format and its check; `test_spoken_thread.py` holds the reader that decides
who said what on a card.

What these tests hold:

  * **The groom receipt does not move.** The answer receipt is a second kind
    beside it — its own trailer tag and its own domain line — and the groom
    SPEC's hash is pinned, so a groom decision already on a card keeps
    verifying byte for byte.
  * **The words are signed, not just the receipt.** The signature binds the
    SHA-256 of everything above the trailer, the "Answer from" line included,
    so the fleet cannot edit the CEO's answer after the console signed it, and
    cannot edit the name or the time he reads either.
  * **A receipt is for one card.** Copied to another card it is refused;
    rewritten to name that card it no longer verifies.
  * **No signature carries between the two kinds.** A groom signature is not
    an answer signature, and an answer receipt decides nothing in the groom
    drain.

Run: cd bureau-pipeline && python3 -m pytest tests/test_console_answer_receipt.py -v
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import console_receipt  # noqa: E402
import console_receipt_vectors as V  # noqa: E402

OPENSSL = V.capable_openssl()

#: Linear's own stamp on the answer comment — five seconds after the console
#: signed it.
CREATED = "2026-09-13T16:52:12.204Z"


def _need_openssl():
    if OPENSSL is None:
        if os.environ.get("CI"):
            pytest.fail("no Ed25519-capable openssl on a CI runner")
        pytest.skip("no Ed25519-capable openssl on this machine")


@pytest.fixture(autouse=True)
def _fresh_probe(monkeypatch):
    monkeypatch.setattr(console_receipt, "_OPENSSL", None, raising=False)
    if OPENSSL:
        monkeypatch.setenv("OPENSSL_BIN", OPENSSL)


def _verifier(loader=None):
    return console_receipt.Verifier(
        key_loader=loader or (lambda: console_receipt.PublicKey.from_b64(
            V.PUBLIC_KEY_B64)))


def _signed_answer(text: str, *, card: str = V.ANSWER_CARD,
                   at: str = V.ANSWER_AT, sign_card: str | None = None,
                   trailer_card: str | None = None) -> str:
    """An answer comment signed with the TEST key over `text` — for the
    variants the vector does not cover. `sign_card` signs for another card;
    `trailer_card` writes another card into the trailer than was signed."""
    digest = console_receipt.answer_sha256(text)
    signed = console_receipt.answer_signed_bytes(
        sign_card or card, digest, V.ANSWER_USER, at)
    sig = V.b64url(V.sign(signed, openssl=OPENSSL))
    trailer = console_receipt.answer_trailer(
        card=trailer_card or sign_card or card, sha256=digest,
        user=V.ANSWER_USER, at=at, kid=V.KID, sig=sig)
    return f"{text}\n\n{trailer}"


# --------------------------------------------------------------------------
# the groom receipt does not move
# --------------------------------------------------------------------------
def test_the_groom_spec_is_unchanged_byte_for_byte():
    """agent-bureau's suite pins this same hash (DRE-3755). The answer kind is
    added beside the groom kind, never into it."""
    got = hashlib.sha256(console_receipt.SPEC.encode("utf-8")).hexdigest()
    assert got == V.SPEC_SHA256


def test_the_groom_signed_bytes_are_unchanged_byte_for_byte():
    got = console_receipt.signed_bytes(V.MARKER, V.CARD, V.PROPOSAL, V.USER, V.AT)
    assert got == V.SIGNED_BYTES


def test_a_groom_receipt_is_still_valid():
    _need_openssl()
    why = _verifier().check(V.COMMENT, card=V.CARD, proposal=V.PROPOSAL,
                            created_at="2026-09-13T15:15:03.412Z")
    assert why is None, why


# --------------------------------------------------------------------------
# the answer wire format — one set of bytes
# --------------------------------------------------------------------------
def test_the_answer_signed_bytes_are_the_vector_byte_for_byte():
    got = console_receipt.answer_signed_bytes(
        V.ANSWER_CARD, V.ANSWER_SHA256, V.ANSWER_USER, V.ANSWER_AT)
    assert got == V.ANSWER_SIGNED_BYTES
    assert hashlib.sha256(got).hexdigest() == V.ANSWER_SIGNED_BYTES_SHA256


def test_the_answer_text_is_everything_above_the_trailer_header_included():
    """The "Answer from <name> …, <PT time>:" line is signed with the words: an
    answer whose name or time the fleet could change is not the CEO's."""
    assert console_receipt.answer_text(V.ANSWER_COMMENT) == V.ANSWER_TEXT
    assert console_receipt.answer_canonical(V.ANSWER_TEXT) == V.ANSWER_CANONICAL
    assert console_receipt.answer_sha256(V.ANSWER_TEXT) == V.ANSWER_SHA256


def test_the_answer_trailer_the_writer_composes_is_the_vector_line():
    assert console_receipt.answer_trailer(
        card=V.ANSWER_CARD, sha256=V.ANSWER_SHA256, user=V.ANSWER_USER,
        at=V.ANSWER_AT, kid=V.KID, sig=V.ANSWER_SIG) == V.ANSWER_TRAILER


def test_the_answer_trailer_is_read_off_the_last_line():
    receipt = console_receipt.parse_answer(V.ANSWER_COMMENT)
    assert receipt is not None
    assert (receipt.card, receipt.sha256, receipt.user, receipt.at,
            receipt.kid, receipt.sig) == (
        V.ANSWER_CARD, V.ANSWER_SHA256, V.ANSWER_USER, V.ANSWER_AT, V.KID,
        V.ANSWER_SIG)
    assert console_receipt.has_answer_trailer(V.ANSWER_COMMENT)


def test_an_answer_trailer_that_is_not_the_last_line_is_no_receipt():
    moved = f"{V.ANSWER_TRAILER}\n\n{V.ANSWER_TEXT}"
    assert console_receipt.parse_answer(moved) is None


def test_two_answer_trailers_are_no_receipt():
    doubled = f"{V.ANSWER_COMMENT}\n{V.ANSWER_TRAILER}"
    assert console_receipt.parse_answer(doubled) is None
    assert console_receipt.has_answer_trailer(doubled)


def test_the_emoji_is_optional_on_the_answer_trailer():
    plain = V.ANSWER_COMMENT.replace("🔏 console-answer:", "console-answer:")
    assert console_receipt.parse_answer(plain) is not None


def test_the_answer_spec_is_its_own_and_names_every_signed_line():
    spec = console_receipt.ANSWER_SPEC
    for label in ("bureau-console-answer/v1", "card: ", "sha256: ", "user: ",
                  "at: ", "console-answer: v1", console_receipt.KEY_URL):
        assert label in spec, f"the answer spec never says {label!r}"
    assert console_receipt.ANSWER_DOMAIN != console_receipt.DOMAIN
    assert console_receipt.ANSWER_TAG != console_receipt.TAG


# --------------------------------------------------------------------------
# the signature, and the whole check
# --------------------------------------------------------------------------
def test_the_answer_vector_signed_by_cryptography_verifies_through_openssl():
    _need_openssl()
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    ok, why = console_receipt.verify_signature(
        key, V.ANSWER_SIGNED_BYTES, console_receipt.sig_bytes(V.ANSWER_SIG))
    assert ok, why


def test_a_valid_answer_passes_the_whole_check():
    _need_openssl()
    why = _verifier().check_answer(V.ANSWER_COMMENT, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is None, why


def test_linear_reflowing_the_whitespace_still_verifies():
    """Linear may store blank lines and trailing spaces differently from how
    they were sent. Folding whitespace keeps an honest answer verifying; not
    one word can change."""
    _need_openssl()
    reflowed = V.ANSWER_COMMENT.replace("\n\n", "\n", 1).replace(
        "one more month.", "one more month.   ")
    why = _verifier().check_answer(reflowed, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is None, why


def test_a_body_edited_after_signing_is_refused():
    """The fleet can edit a comment it authored. Changing the CEO's words
    changes their hash, and the refusal says so plainly."""
    edited = V.ANSWER_COMMENT.replace("option B", "option A", 1)
    why = _verifier().check_answer(edited, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None
    assert "changed after" in why


def test_the_signed_name_and_time_cannot_be_edited_either():
    edited = V.ANSWER_COMMENT.replace("Test Owner", "Somebody Else", 1)
    why = _verifier().check_answer(edited, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "changed after" in why


def test_an_edit_that_also_rewrites_the_hash_fails_the_signature():
    _need_openssl()
    edited_text = V.ANSWER_TEXT.replace("option B", "option A")
    forged = (f"{edited_text}\n\n" + V.ANSWER_TRAILER.replace(
        V.ANSWER_SHA256, console_receipt.answer_sha256(edited_text)))
    why = _verifier().check_answer(forged, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "signature" in why


def test_a_receipt_copied_to_another_card_is_refused():
    why = _verifier().check_answer(V.ANSWER_COMMENT, card="DRE-9999",
                                   created_at=CREATED)
    assert why is not None
    assert "DRE-9999" in why and V.ANSWER_CARD in why


def test_a_copy_with_its_card_rewritten_fails_the_signature():
    _need_openssl()
    copied = V.ANSWER_COMMENT.replace(f"card={V.ANSWER_CARD}", "card=DRE-9999")
    why = _verifier().check_answer(copied, card="DRE-9999", created_at=CREATED)
    assert why is not None and "signature" in why


@pytest.mark.parametrize("created_at, needle", [
    ("2026-09-13T17:52:12.000Z", "stale"),
    ("2026-09-13T16:40:00.000Z", "before"),
    (None, "time"),
])
def test_an_answer_off_its_own_time_is_refused(created_at, needle):
    why = _verifier().check_answer(V.ANSWER_COMMENT, card=V.ANSWER_CARD,
                                   created_at=created_at)
    assert why is not None and needle in why


def test_an_answer_naming_a_key_the_console_does_not_publish_is_refused():
    forged = V.ANSWER_COMMENT.replace(f"kid={V.KID}", "kid=00000000deadbeef")
    why = _verifier().check_answer(forged, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "00000000deadbeef" in why


def test_an_unreadable_key_refuses_an_answer_and_says_why():
    def down():
        raise console_receipt.KeyUnavailable("connection refused")
    why = _verifier(down).check_answer(V.ANSWER_COMMENT, card=V.ANSWER_CARD,
                                       created_at=CREATED)
    assert why is not None
    assert "connection refused" in why and console_receipt.KEY_URL in why


def test_an_answer_with_no_words_is_refused():
    _need_openssl()
    empty = _signed_answer("")
    why = _verifier().check_answer(empty, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "no words" in why


def test_a_comment_with_no_answer_trailer_says_so():
    why = _verifier().check_answer(V.ANSWER_TEXT, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "no console answer receipt" in why


# --------------------------------------------------------------------------
# no signature carries between the two kinds
# --------------------------------------------------------------------------
def test_a_groom_receipt_is_not_an_answer():
    why = _verifier().check_answer(V.COMMENT, card=V.CARD,
                                   created_at="2026-09-13T15:15:03.412Z")
    assert why is not None and "no console answer receipt" in why


def test_an_answer_receipt_is_not_a_groom_receipt():
    why = _verifier().check(V.ANSWER_COMMENT, card=V.ANSWER_CARD,
                            proposal=V.PROPOSAL, created_at=CREATED)
    assert why is not None and "no console receipt" in why


def test_a_groom_signature_cannot_be_carried_onto_an_answer():
    """The same key signs both kinds, so the domain line is what keeps a
    signature from meaning one thing on one card and another on the next."""
    _need_openssl()
    carried = V.ANSWER_COMMENT.replace(f"sig={V.ANSWER_SIG}", f"sig={V.SIG}")
    why = _verifier().check_answer(carried, card=V.ANSWER_CARD,
                                   created_at=CREATED)
    assert why is not None and "signature" in why


def test_a_signature_made_over_groom_bytes_does_not_verify_an_answer():
    _need_openssl()
    key = console_receipt.PublicKey.from_b64(V.PUBLIC_KEY_B64)
    ok, _ = console_receipt.verify_signature(
        key, V.ANSWER_SIGNED_BYTES, console_receipt.sig_bytes(V.SIG))
    assert not ok
