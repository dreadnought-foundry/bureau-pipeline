"""Mint GitHub App installation tokens for the harness driver.

harness.yml mints both App tokens once at job start
(create-github-app-token), but installation tokens live exactly one hour
and a full scenario run can outlast it — run 29795108949's late scenarios
died `401 Bad credentials`. Workflow steps cannot re-run mid-job, so the
driver re-mints for itself: an RS256 App JWT — signed via the runner's
openssl binary, the one primitive the stdlib lacks (no crypto dependency,
matching the client's stdlib-only stance) — exchanged for a fresh
installation token scoped to the SAME single repo the workflow's own mint
step names.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import time

from harness.github_api import API_URL, GitHub

# GitHub rejects App JWTs whose exp is more than 10 minutes out; the
# backdated iat absorbs clock skew between the runner and GitHub (the
# shape GitHub's own docs use).
_JWT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_rs256(signing_input: bytes, private_key_pem: str) -> bytes:
    """RSASSA-PKCS1-v1_5-SHA256 via openssl. The key touches disk only as
    a NamedTemporaryFile on the ephemeral runner (openssl cannot read a
    key from stdin while the data is on stdin too)."""
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as key_file:
        key_file.write(private_key_pem)
        key_file.flush()
        out = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=signing_input,
            capture_output=True,
        )
    if out.returncode != 0:
        raise RuntimeError(
            "openssl RS256 signing failed: "
            + out.stderr.decode(errors="replace")[:200]
        )
    return out.stdout


def app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """A short-lived App JWT — the credential that authenticates AS the
    App itself, good only for the mint exchange below."""
    issued = int(time.time() if now is None else now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "iat": issued - _JWT_BACKDATE_SECONDS,
                "exp": issued + _JWT_LIFETIME_SECONDS,
                "iss": app_id,
            }
        ).encode()
    )
    signature = _sign_rs256(f"{header}.{payload}".encode(), private_key_pem)
    return f"{header}.{payload}.{_b64url(signature)}"


def mint_installation_token(
    app_id: str, private_key_pem: str, repo: str, api_url: str = API_URL, opener=None
) -> str:
    """A fresh installation token for `repo` (owner/name), scoped to that
    single repository — never the whole installation. Two REST calls, both
    JWT-authenticated, riding the existing client's retry logic."""
    gh = GitHub(app_jwt(app_id, private_key_pem), api_url=api_url, opener=opener)
    installation = gh.request("GET", f"/repos/{repo}/installation")
    out = gh.request(
        "POST",
        f"/app/installations/{installation['id']}/access_tokens",
        {"repositories": [repo.split("/", 1)[1]]},
    )
    return out["token"]
