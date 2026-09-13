"""The console receipt v1 TEST vectors (DRE-3754, DRE-3755).

ONE set of bytes both halves are held to. agent-bureau's
`console/backend/tests/test_console_receipt_signing.py` carries these same
constants, and each side proves the other's implementation against them: the
console SIGNS with `cryptography`, the drain VERIFIES with the runner's
`openssl` binary, and the signature below was produced by the first and is
checked here by the second. If either side drifts by one byte — the domain
line, a field label, a trailing newline — the vector stops verifying on that
side's suite, not in production on the CEO's click.

THE KEY IS A TEST KEY. Its seed is the SHA-256 of a public sentence, printed
right here, so it proves nothing about anybody and must never be anything but
a fixture: the console generates its real key on first use, holds it
encrypted, and never writes it anywhere a repo can see (DRE-3755).
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile

#: sha256(b"bureau-console-receipt v1 TEST KEY - never used outside tests")
SEED_HEX = "20772fa8b439c476b653b7e5c4684caf351917b44a09d553551c53dac369daa0"
assert hashlib.sha256(
    b"bureau-console-receipt v1 TEST KEY - never used outside tests"
).hexdigest() == SEED_HEX

PUBLIC_KEY_B64 = "idpcXd94bXMNnZsZ61kSDsKlEJqlEdFb/sAWjsFW6yg="
KID = "2edce07e90edcb4d"

MARKER = "🧺 groom-declined: 2b10ecfb36f6 — not this batch"
CARD = "DRE-3700"
PROPOSAL = "2b10ecfb36f6"
USER = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
AT = "2026-09-13T15:15:02Z"

#: The exact bytes signed — typed out, never derived, so the spec is pinned by
#: a literal rather than by the function under test.
SIGNED_BYTES = (
    "bureau-console-receipt/v1\n"
    "marker: 🧺 groom-declined: 2b10ecfb36f6 — not this batch\n"
    "card: DRE-3700\n"
    "proposal: 2b10ecfb36f6\n"
    "user: 0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0\n"
    "at: 2026-09-13T15:15:02Z\n"
).encode("utf-8")
SIGNED_BYTES_SHA256 = (
    "e6617f0ac43fd1bd15eba23abb23efff970cf1ae56c20c43c42785856522dadf")

#: Produced by `cryptography`'s Ed25519 over SIGNED_BYTES with the test key.
SIG = ("JRVc9uuBVqWVdwDqacBaFkkgsrWxots6W1U0_K8ciF8P93tTLyL8c1_FA78QyRnBIytFAUiY"
       "mZpWWrJVjzGiCA")

#: The whole comment the console writes for that decision.
TRAILER = (f"🔏 console-receipt: v1 card={CARD} proposal={PROPOSAL} user={USER} "
           f"at={AT} kid={KID} sig={SIG}")
COMMENT = (f"{MARKER}\n\n"
           "Decided in the console by Test Owner (tenant owner) at "
           "2026-09-13 08:15 PT.\n"
           f"{TRAILER}")

#: The SHA-256 of the groom receipt's SPEC, pinned (DRE-3785). The answer
#: receipt is added BESIDE it, never into it: agent-bureau's suite pins this
#: same hash, and a groom receipt already on a card must keep verifying byte
#: for byte.
SPEC_SHA256 = "8de5187c77781e0eeccbaf4c1ccefe70e936a98a9248b6edc6d40cfd051ae207"

# --- the ANSWER receipt v1 (DRE-3785 / DRE-3786) -----------------------------
#
# The CEO's answer to a question on a card, as the console writes it on the
# fleet's Linear key. Same test key, same console user; its own domain line, so
# no signature carries between the two kinds. agent-bureau's
# `console/backend/tests/test_console_answer_signing.py` carries these same
# constants and must SIGN them to exactly ANSWER_SIG with `cryptography`; this
# side VERIFIES them with the runner's `openssl`.

#: The SHA-256 of `console_receipt.ANSWER_SPEC`, pinned here and in
#: agent-bureau's suite, so the two copies of the answer format cannot drift.
ANSWER_SPEC_SHA256 = (
    "636e864233000dcab3267c316590948b96ae5f9a569df94ff9ed528386b2d0c0")

ANSWER_CARD = "DRE-3700"
ANSWER_USER = USER
ANSWER_AT = "2026-09-13T16:52:07Z"          # 09:52 PT
ANSWER_HEAD = ("Answer from Test Owner (signed in to the console), "
               "2026-09-13 09:52 PT:")
ANSWER_WORDS = ("Go with option B — keep the old export for one more month.\n"
                "Then remove it.")
#: Everything above the trailer — the "Answer from" line included.
ANSWER_TEXT = f"{ANSWER_HEAD}\n\n{ANSWER_WORDS}"
#: The answer text with every whitespace run folded to one space — typed out.
ANSWER_CANONICAL = (
    "Answer from Test Owner (signed in to the console), 2026-09-13 09:52 PT: "
    "Go with option B — keep the old export for one more month. "
    "Then remove it.")
ANSWER_SHA256 = (
    "c3cd4b0e5a1c1ce0568732501c02a20a3d4226579fe71770dcb57cb74af49c8f")

#: The exact bytes signed — typed out, never derived.
ANSWER_SIGNED_BYTES = (
    "bureau-console-answer/v1\n"
    "card: DRE-3700\n"
    "sha256: c3cd4b0e5a1c1ce0568732501c02a20a3d4226579fe71770dcb57cb74af49c8f\n"
    "user: 0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0\n"
    "at: 2026-09-13T16:52:07Z\n"
).encode("utf-8")
ANSWER_SIGNED_BYTES_SHA256 = (
    "2172a62045f99037db2695e7467a64f366b85d67214d2f5986efcab5c11d3f99")

#: Ed25519 over ANSWER_SIGNED_BYTES with the test key — `cryptography` and
#: OpenSSL 3 both produce exactly this (Ed25519 is deterministic).
ANSWER_SIG = ("o07OWJxgFCMOt6RLBzWcwuA6yAJ5tCl8_JXjXcREter5opyudt194HOUk3FsvonT"
              "_s04y8XNWaDQfu1DIOGqDQ")

ANSWER_TRAILER = (f"🔏 console-answer: v1 card={ANSWER_CARD} sha256={ANSWER_SHA256} "
                  f"user={ANSWER_USER} at={ANSWER_AT} kid={KID} sig={ANSWER_SIG}")
#: The whole comment the console writes for that answer.
ANSWER_COMMENT = f"{ANSWER_TEXT}\n\n{ANSWER_TRAILER}"

# --- signing variants, for tests only ---------------------------------------

_PKCS8_ED25519_PREFIX = bytes.fromhex("302e020100300506032b657004220420")

#: Where an OpenSSL 3 is found on a developer's machine. CI's ubuntu runner and
#: the mini pool's image both carry OpenSSL 3.0.x as `openssl`; macOS ships
#: LibreSSL there, which cannot load an Ed25519 key at all.
CANDIDATES = ("openssl", "/opt/homebrew/opt/openssl@3/bin/openssl",
              "/opt/homebrew/opt/openssl/bin/openssl",
              "/usr/local/opt/openssl@3/bin/openssl")


def _private_pem() -> bytes:
    der = _PKCS8_ED25519_PREFIX + bytes.fromhex(SEED_HEX)
    body = base64.encodebytes(der).decode().replace("\n", "")
    return (f"-----BEGIN PRIVATE KEY-----\n{body}\n"
            f"-----END PRIVATE KEY-----\n").encode()


def capable_openssl() -> str | None:
    """An openssl that can SIGN Ed25519 raw input here, or None."""
    for name in CANDIDATES:
        path = shutil.which(name) if os.sep not in name else (
            name if os.path.exists(name) else None)
        if not path:
            continue
        try:
            sign(b"probe", openssl=path)
        except (OSError, RuntimeError):
            continue
        return path
    return None


def sign(message: bytes, *, openssl: str) -> bytes:
    """Ed25519 over `message` with the TEST key, via openssl — the fixture
    signer for every variant the vector does not cover (another card, a stale
    time). The console signs with `cryptography`; the cross-implementation
    proof is SIG above, not this helper."""
    with tempfile.TemporaryDirectory() as tmp:
        key = os.path.join(tmp, "k.pem")
        msg = os.path.join(tmp, "m")
        with open(key, "wb") as fh:
            fh.write(_private_pem())
        with open(msg, "wb") as fh:
            fh.write(message)
        out = subprocess.run([openssl, "pkeyutl", "-sign", "-inkey", key,
                              "-rawin", "-in", msg],
                             capture_output=True, timeout=20)
    if out.returncode != 0 or len(out.stdout) != 64:
        raise RuntimeError(out.stderr.decode(errors="replace")[:200])
    return out.stdout


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
