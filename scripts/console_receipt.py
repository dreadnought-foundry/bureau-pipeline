#!/usr/bin/env python3
"""The console receipt — the drain's reader of a console-signed decision
(DRE-3754, the pipeline half of DRE-3735).

The CEO, 2026-09-12 21:28 PT: *"If I sign in, I am who I am by definition."*

The console proves who pressed a button (Cognito). It holds Linear keys only
for the fleet (`Agent-Bureau`) and for operator-tools (`bureau-tools`), so a
groom decision it writes without a pasted personal key is authored by the
fleet — the drain's own identity, whose markers the drain refuses, because a
gate the proposer can pass by itself is not a gate (DRE-2721). Until now that
left the CEO refused on every Approve, Decline and "not this one".

So the console SIGNS. It holds an Ed25519 private key that no workflow and no
agent can read, signs the decision's canonical bytes with it, and puts the
signature on the comment's last line. This module is the one reader of that
line: `groomer.vouch` asks it about every fleet-authored decision marker, and
a marker whose receipt does not verify is refused exactly as a fleet marker
always was — with the reason named in the drain's record.

WHY THE FLEET CANNOT FORGE ONE. The fleet's Linear key can write any comment,
including one carrying this trailer. It cannot produce the signature: the
private key lives only in the console backend's database, encrypted at rest,
and is never served, logged or committed (DRE-3755). The public key is read
from ONE place, fixed below in code. Deliberately NOT from a repository or
organisation variable, although the card asked for one as an override: the
fleet's GitHub App (`agent-bureau-bot`) holds `actions_variables: write`
(checked 2026-09-13), so a variable-supplied key is a key the fleet could set
to its own. Changing the key's source is a code change, behind the critic —
the same boundary that protects the authorship rule itself.

WHY OPENSSL. The drain runs `python3 groomer.py` with no pip install, and the
standard library has no Ed25519. The house precedent (`harness/app_token.py`)
is the runner's `openssl` binary — OpenSSL 3.0.13 on the mini pool's image and
on `ubuntu-latest`, both checked. macOS ships LibreSSL as `openssl`, which
cannot load an Ed25519 key, so the binary is chosen by a known-answer probe
(a good signature must verify AND a bad one must fail) rather than by name,
and a machine with no capable binary refuses every receipted marker with that
reason — never a pass, never a misleading "bad signature".
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from urllib.request import Request, urlopen  # noqa: F401 — `urlopen` is patched in tests

SPEC = """\
CONSOLE RECEIPT, WIRE FORMAT v1 (DRE-3754 / DRE-3755) — the one definition.

A groom decision the console writes on the fleet's Linear key is ONE comment:

    <marker line>
    <blank line>
    <a line the CEO reads — free text, not signed, not read by the drain>
    🔏 console-receipt: v1 card=<CARD> proposal=<PROPOSAL> user=<USER> at=<AT> kid=<KID> sig=<SIG>

  marker line   the FIRST line of the comment body after strip() — exactly
                what the drain's marker readers match, e.g.
                `🧺 groom-declined: 2b10ecfb36f6 — not this batch`
  trailer       the LAST non-empty line. Emoji optional, as on every marker.
                Fields in this order, one space apart. Exactly one per comment.
  CARD          the card the comment is posted on, e.g. DRE-3700
  PROPOSAL      the batch id the marker names ([0-9a-f]{6,}), or `-` for a
                repo switch (groom-hold-repo / groom-release-repo), which
                names no batch
  USER          the console user id (the Cognito sub), [A-Za-z0-9._:@+-]{1,128}
  AT            the signing time, UTC, to the second: YYYY-MM-DDTHH:MM:SSZ
  KID           the first 16 hex characters of sha256(raw 32-byte public key)
  SIG           Ed25519 signature, base64url with no padding (86 characters)

THE SIGNED BYTES are UTF-8, six lines, each ending in a single "\\n":

    bureau-console-receipt/v1
    marker: <marker line>
    card: <CARD>
    proposal: <PROPOSAL>
    user: <USER>
    at: <AT>

THE PUBLIC KEY is served unauthenticated by the console:

    GET https://app.agent-bureau.com/api/v1/receipt-key
    200 {"alg": "Ed25519", "kid": "<KID>", "public_key": "<base64 of the raw 32 bytes>"}

THE DRAIN honours a FLEET-authored decision marker only when every one holds:
the trailer parses; CARD is the card being read; PROPOSAL is the batch the
marker names (or `-` on a repo switch); AT is no more than 10 minutes before
and no more than 2 minutes after the comment's own Linear createdAt; the key
could be read and its kid is KID; the signature verifies; and no earlier
comment in the thread carried the same signature. Anything else refuses, and
says why. A marker authored by anybody other than the fleet is read exactly as
before and never makes the drain fetch the key.
"""

VERSION = "v1"
TAG = "console-receipt"
MARK = "🔏"
DOMAIN = "bureau-console-receipt/v1"

#: The console's published key. A constant, not a variable — see the module
#: docstring for why no variable may move it.
KEY_URL = "https://app.agent-bureau.com/api/v1/receipt-key"
KEY_FETCH_TIMEOUT_SECONDS = 10

#: A repo switch names no batch; its receipt says so with this.
NO_PROPOSAL = "-"

#: How far the signed time may sit from the comment's own Linear createdAt.
#: The console signs, then posts: an honest receipt is seconds old when Linear
#: stamps it. The skew allows the console's clock to run a little fast.
MAX_AGE = timedelta(minutes=10)
CLOCK_SKEW = timedelta(minutes=2)

_CARD = r"[A-Z][A-Z0-9]*-\d+"
_PROPOSAL = r"(?:[0-9a-f]{6,}|-)"
_USER = r"[A-Za-z0-9._:@+-]{1,128}"
_AT = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
_TRAILER_SHAPED = re.compile(rf"^\s*(?:{MARK}\s*)?{TAG}\s*:", re.M)
_TRAILER = re.compile(
    rf"^\s*(?:{MARK}\s*)?{TAG}:\s*{VERSION}"
    rf" card=({_CARD}) proposal=({_PROPOSAL}) user=({_USER})"
    rf" at=({_AT}) kid=([0-9a-f]{{16}}) sig=([A-Za-z0-9_-]{{86}})\s*$")

#: The DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410): the raw 32
#: bytes follow it. A constant, not crypto — openssl reads the PEM it makes.
_SPKI_ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")


# --------------------------------------------------------------------------- #
# the wire format                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Receipt:
    card: str
    proposal: str
    user: str
    at: str
    kid: str
    sig: str


def marker_line(body: str | None) -> str:
    """The line the signature binds: the first line of the stripped body."""
    return (body or "").strip().split("\n", 1)[0].rstrip()


def signed_bytes(marker: str, card: str, proposal: str, user: str, at: str) -> bytes:
    """The canonical bytes, exactly as SPEC declares them."""
    return (f"{DOMAIN}\n"
            f"marker: {marker}\n"
            f"card: {card}\n"
            f"proposal: {proposal}\n"
            f"user: {user}\n"
            f"at: {at}\n").encode("utf-8")


def trailer(*, card: str, proposal: str, user: str, at: str, kid: str,
            sig: str) -> str:
    """The trailer line, composed — the writer half of `parse`."""
    return (f"{MARK} {TAG}: {VERSION} card={card} proposal={proposal} "
            f"user={user} at={at} kid={kid} sig={sig}")


def has_trailer(body: str | None) -> bool:
    """Does any line of `body` LOOK like a receipt? Distinguishes "carries no
    receipt" (refused as today) from "carries a receipt that is wrong"."""
    return bool(_TRAILER_SHAPED.search(body or ""))


def parse(body: str | None) -> Receipt | None:
    """The receipt on the last non-empty line, or None.

    None when there is no trailer there, when it is malformed, or when the
    comment carries more than one receipt-shaped line — one decision, one
    receipt, and a second is somebody arguing with the first.
    """
    text = (body or "").strip()
    if len(_TRAILER_SHAPED.findall(text)) != 1:
        return None
    last = text.rsplit("\n", 1)[-1]
    found = _TRAILER.match(last)
    if not found:
        return None
    return Receipt(*found.groups())


def sig_bytes(sig: str) -> bytes:
    """The 64 raw signature bytes from their base64url form."""
    return base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))


# --------------------------------------------------------------------------- #
# the public key                                                               #
# --------------------------------------------------------------------------- #

class KeyUnavailable(RuntimeError):
    """The console's public key could not be read — every receipted marker is
    refused with this reason, and nothing else is affected."""


@dataclass(frozen=True)
class PublicKey:
    raw: bytes

    @property
    def kid(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()[:16]

    @classmethod
    def from_b64(cls, text: str) -> "PublicKey":
        try:
            raw = base64.b64decode(text or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise KeyUnavailable(f"the public key is not base64 ({exc})") from exc
        if len(raw) != 32:
            raise KeyUnavailable(
                f"the public key is {len(raw)} bytes, and an Ed25519 key is 32")
        return cls(raw)

    def pem(self) -> bytes:
        body = base64.b64encode(_SPKI_ED25519_PREFIX + self.raw).decode()
        return (f"-----BEGIN PUBLIC KEY-----\n{body}\n"
                f"-----END PUBLIC KEY-----\n").encode()


def fetch_key(url: str = KEY_URL, *,
              timeout: float = KEY_FETCH_TIMEOUT_SECONDS) -> PublicKey:
    """The console's public key, read over HTTPS. Raises KeyUnavailable,
    naming what went wrong, on anything but a well-formed Ed25519 answer whose
    kid is the key's own."""
    if not url.startswith("https://"):
        raise KeyUnavailable(f"{url} is not an https URL")
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}),
                     timeout=timeout) as response:
            status = getattr(response, "status", 200)
            payload = response.read()
    except Exception as exc:  # noqa: BLE001 — every transport failure refuses
        raise KeyUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if status != 200:
        raise KeyUnavailable(f"the endpoint answered HTTP {status}")
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise KeyUnavailable("the endpoint did not answer JSON") from exc
    if not isinstance(doc, dict) or doc.get("alg") != "Ed25519":
        raise KeyUnavailable("the endpoint's key is not Ed25519")
    key = PublicKey.from_b64(str(doc.get("public_key") or ""))
    if doc.get("kid") != key.kid:
        raise KeyUnavailable(
            f"the endpoint's kid {doc.get('kid')!r} is not its key's own "
            f"({key.kid})")
    return key


# --------------------------------------------------------------------------- #
# the signature                                                                #
# --------------------------------------------------------------------------- #

#: Where an OpenSSL 3 is looked for, after `OPENSSL_BIN`. The runner images
#: carry it as `openssl`; the brew paths are the operator's Mac, where
#: `openssl` is LibreSSL.
OPENSSL_CANDIDATES = ("openssl", "/opt/homebrew/opt/openssl@3/bin/openssl",
                      "/opt/homebrew/opt/openssl/bin/openssl",
                      "/usr/local/opt/openssl@3/bin/openssl")

#: The known answer the capability probe checks, both ways. Public data only:
#: the TEST key's public half (tests/console_receipt_vectors.py) and a
#: signature it made. Never the real key.
_PROBE_KEY = "idpcXd94bXMNnZsZ61kSDsKlEJqlEdFb/sAWjsFW6yg="
_PROBE_MESSAGE = signed_bytes("🧺 groom-declined: 2b10ecfb36f6 — not this batch",
                              "DRE-3700", "2b10ecfb36f6",
                              "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
                              "2026-09-13T15:15:02Z")
_PROBE_SIG = ("JRVc9uuBVqWVdwDqacBaFkkgsrWxots6W1U0_K8ciF8P93tTLyL8c1_FA78QyRnB"
              "IytFAUiYmZpWWrJVjzGiCA")

#: The chosen binary for this process: None until probed, "" when none is
#: capable (and the reason in _OPENSSL_WHY).
_OPENSSL: str | None = None
_OPENSSL_WHY = ""


def _run_verify(binary: str, key: PublicKey, message: bytes,
                sig: bytes) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        paths = {name: os.path.join(tmp, name) for name in ("k.pem", "m", "s")}
        for name, data in (("k.pem", key.pem()), ("m", message), ("s", sig)):
            with open(paths[name], "wb") as fh:
                fh.write(data)
        out = subprocess.run(
            [binary, "pkeyutl", "-verify", "-pubin", "-inkey", paths["k.pem"],
             "-rawin", "-in", paths["m"], "-sigfile", paths["s"]],
            capture_output=True, timeout=20)
    said = (out.stdout + out.stderr).decode(errors="replace")
    return (out.returncode == 0 and "Signature Verified Successfully" in said,
            said.strip().splitlines()[0] if said.strip() else f"exit {out.returncode}")


def _resolve(path_or_name: str) -> str | None:
    if os.sep in path_or_name:
        return path_or_name if os.access(path_or_name, os.X_OK) else None
    return shutil.which(path_or_name)


def openssl() -> str | None:
    """The first binary that passes the known-answer probe both ways, cached
    for the process. None when there is none; `_OPENSSL_WHY` says why."""
    global _OPENSSL, _OPENSSL_WHY
    if _OPENSSL is not None:
        return _OPENSSL or None
    tried = []
    probe_key = PublicKey.from_b64(_PROBE_KEY)
    good = sig_bytes(_PROBE_SIG)
    for candidate in ((os.environ.get("OPENSSL_BIN") or "").strip(),
                      *OPENSSL_CANDIDATES):
        binary = _resolve(candidate) if candidate else None
        if not binary or binary in tried:
            continue
        tried.append(binary)
        try:
            passes, _ = _run_verify(binary, probe_key, _PROBE_MESSAGE, good)
            rejects, _ = _run_verify(binary, probe_key,
                                     _PROBE_MESSAGE + b"tampered", good)
        except (OSError, subprocess.SubprocessError):
            continue
        if passes and not rejects:
            _OPENSSL, _OPENSSL_WHY = binary, ""
            return binary
    _OPENSSL = ""
    _OPENSSL_WHY = (
        "no openssl on this machine can verify Ed25519 — tried "
        f"{', '.join(tried) or 'nothing (none found)'}; the drain needs "
        "OpenSSL 3 (set OPENSSL_BIN to one)")
    return None


def verify_signature(key: PublicKey, message: bytes,
                     sig: bytes) -> tuple[bool, str | None]:
    """`(True, None)` when `sig` is `key`'s Ed25519 signature over `message`;
    otherwise `(False, why)`."""
    binary = openssl()
    if binary is None:
        return False, _OPENSSL_WHY
    try:
        ok, said = _run_verify(binary, key, message, sig)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"openssl ({binary}) could not run: {exc}"
    if ok:
        return True, None
    return False, ("the signature does not verify against the console's key "
                   f"({said})")


# --------------------------------------------------------------------------- #
# the whole check                                                              #
# --------------------------------------------------------------------------- #

def _moment(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


class Verifier:
    """One run's reader of console receipts. The key is loaded at most once —
    on the first receipt that gets far enough to need it — and a failure to
    load it is remembered, so a console that is down refuses every receipted
    marker with one reason and costs one timeout, not one per comment."""

    def __init__(self, key_loader: Callable[[], PublicKey] = fetch_key):
        self._load = key_loader
        self._key: PublicKey | None = None
        self._key_why: str | None = None

    def key(self) -> tuple[PublicKey | None, str | None]:
        if self._key is None and self._key_why is None:
            try:
                self._key = self._load()
            except KeyUnavailable as exc:
                self._key_why = (f"the console's public key could not be read "
                                 f"from {KEY_URL} — {exc}")
        return self._key, self._key_why

    def check(self, body: str | None, *, card: str, proposal: str,
              created_at: str | None) -> str | None:
        """None when `body` carries a valid receipt for `card` and `proposal`,
        else why not — in words that go into the drain's record as written."""
        if not has_trailer(body):
            return "it carries no console receipt"
        receipt = parse(body)
        if receipt is None:
            return ("its console receipt is malformed, or is not the one last "
                    "line of the comment")
        if receipt.card != card:
            return (f"its console receipt was signed for {receipt.card}, and "
                    f"it sits on {card}")
        if receipt.proposal != proposal:
            return (f"its console receipt was signed for batch "
                    f"`{receipt.proposal}`, and the marker names `{proposal}`")
        signed, posted = _moment(receipt.at), _moment(created_at)
        if signed is None or posted is None:
            return ("the comment carries no creation time to hold its "
                    "console receipt's time against")
        if signed > posted + CLOCK_SKEW:
            return (f"its console receipt is dated {receipt.at}, after the "
                    f"comment was posted — a receipt is signed before its "
                    f"comment exists")
        if posted - signed > MAX_AGE:
            return (f"its console receipt is stale — signed {receipt.at}, "
                    f"posted {created_at}, more than "
                    f"{int(MAX_AGE.total_seconds() // 60)} minutes apart")
        key, why = self.key()
        if key is None:
            return why
        if receipt.kid != key.kid:
            return (f"its console receipt names key {receipt.kid}, and the "
                    f"console publishes {key.kid}")
        ok, why = verify_signature(
            key, signed_bytes(marker_line(body), card, proposal, receipt.user,
                              receipt.at), sig_bytes(receipt.sig))
        return None if ok else why
