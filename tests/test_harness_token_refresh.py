"""RED-first tests for harness App-token refresh (DRE-2103 follow-up).

Harness run 29795108949 proved the defect live: the workflow mints both
App installation tokens ONCE at job start, GitHub installation tokens
live exactly one hour, and the run outlasted it — every GitHub call in
the late scenarios died `401 Bad credentials` (gate_paths failed in
verify AND cleanup, so the sandbox was left unswept too). The mint steps
cannot re-run mid-job, so the DRIVER must be able to re-mint for itself:

  * github_api.GitHub grows an optional `token_supplier` — a callable
    returning a fresh installation token. The client re-mints
    proactively once its token is ~50 minutes old (inside the hour) and
    once reactively per request on a 401 (the margin's race), then
    surfaces the 401 honestly. A supplier-less client (local PAT runs)
    behaves exactly as before.
  * harness/app_token.py mints real installation tokens from the App
    credentials: an RS256 App JWT — signed via the runner's openssl
    binary, the one primitive the stdlib lacks — exchanged for a token
    scoped to the single sandbox repo, mirroring harness.yml's own
    create-github-app-token scoping.
  * __main__ builds the suppliers from HARNESS_*_APP_ID/_PRIVATE_KEY and
    harness.yml threads those from the same App secrets the mint steps
    already use.

These tests must FAIL before the refresh support exists, and PASS after.
"""

import base64
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from harness import github_api  # noqa: E402
from harness.github_api import GitHub, GitHubError  # noqa: E402

WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "harness.yml"
)


def _http_error(code: int, body: bytes = b'{"message":"Bad credentials"}'):
    return urllib.error.HTTPError(
        "https://api.github.com/x", code, "err", None, io.BytesIO(body)
    )


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RefreshingClientTest(unittest.TestCase):
    """GitHub(token_supplier=...) — proactive re-mint at the age
    threshold, one reactive re-mint per request on a 401."""

    def _client(self, opener, supplier=None):
        clock = FakeClock()
        gh = GitHub(
            "tok-initial", opener=opener, token_supplier=supplier, clock=clock
        )
        return gh, clock

    @staticmethod
    def _supplier(tokens):
        minted = []

        def mint():
            minted.append(True)
            return tokens[len(minted) - 1]

        mint.calls = minted
        return mint

    def test_fresh_token_is_used_without_minting(self):
        seen = []

        def opener(req):
            seen.append(req.headers["Authorization"])
            return 200, b"{}"

        supplier = self._supplier(["tok-2"])
        gh, _ = self._client(opener, supplier)
        gh.request("GET", "/rate_limit")
        self.assertEqual(seen, ["Bearer tok-initial"])
        self.assertEqual(len(supplier.calls), 0)

    def test_stale_token_is_reminted_before_the_request(self):
        # Installation tokens die at 60 minutes; the client must re-mint
        # BEFORE that — at ~50 minutes — not discover the corpse mid-run.
        seen = []

        def opener(req):
            seen.append(req.headers["Authorization"])
            return 200, b"{}"

        supplier = self._supplier(["tok-2"])
        gh, clock = self._client(opener, supplier)
        gh.request("GET", "/rate_limit")
        clock.now += github_api.TOKEN_REFRESH_SECONDS + 1
        gh.request("GET", "/rate_limit")
        self.assertEqual(seen, ["Bearer tok-initial", "Bearer tok-2"])
        self.assertEqual(len(supplier.calls), 1)

    def test_refresh_threshold_sits_inside_the_hour(self):
        self.assertLess(github_api.TOKEN_REFRESH_SECONDS, 60 * 60)
        self.assertGreaterEqual(github_api.TOKEN_REFRESH_SECONDS, 30 * 60)

    def test_401_reminted_once_and_retried(self):
        # The reactive arm: whatever the age math says, a live 401 means
        # the token is dead — one fresh mint, one retry, then honesty.
        seen = []

        def opener(req):
            seen.append(req.headers["Authorization"])
            if req.headers["Authorization"] == "Bearer tok-initial":
                raise _http_error(401)
            return 200, b'{"ok": true}'

        supplier = self._supplier(["tok-2"])
        gh, _ = self._client(opener, supplier)
        out = gh.request("GET", "/rate_limit")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(seen, ["Bearer tok-initial", "Bearer tok-2"])
        self.assertEqual(len(supplier.calls), 1)

    def test_persistent_401_surfaces_after_one_remint(self):
        calls = []

        def opener(req):
            calls.append(req.headers["Authorization"])
            raise _http_error(401)

        supplier = self._supplier(["tok-2", "tok-3", "tok-4"])
        gh, _ = self._client(opener, supplier)
        with self.assertRaises(GitHubError) as caught:
            gh.request("GET", "/rate_limit")
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(supplier.calls), 1, "exactly one re-mint, no loop")
        self.assertEqual(calls, ["Bearer tok-initial", "Bearer tok-2"])

    def test_supplierless_client_keeps_the_old_behavior(self):
        # Local runs hand the driver a static PAT — a 401 must surface
        # immediately, exactly as before the refresh support existed.
        def opener(req):
            raise _http_error(401)

        gh = GitHub("tok-static", opener=opener)
        with self.assertRaises(GitHubError) as caught:
            gh.request("GET", "/rate_limit")
        self.assertEqual(caught.exception.status, 401)


class AppTokenTest(unittest.TestCase):
    """harness/app_token.py — a real RS256 App JWT (openssl-signed) and
    the two-call mint exchange, sandbox-repo-scoped."""

    @classmethod
    def setUpClass(cls):
        from harness import app_token

        cls.app_token = app_token
        cls.key_pem = subprocess.run(
            ["openssl", "genrsa", "2048"], capture_output=True, check=True
        ).stdout.decode()

    @staticmethod
    def _decode_segment(segment: str) -> dict:
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

    def test_app_jwt_claims_and_openssl_verifiable_signature(self):
        jwt = self.app_token.app_jwt("3350400", self.key_pem, now=1_700_000_000)
        header_b64, payload_b64, sig_b64 = jwt.split(".")

        self.assertEqual(
            self._decode_segment(header_b64), {"alg": "RS256", "typ": "JWT"}
        )
        payload = self._decode_segment(payload_b64)
        self.assertEqual(payload["iss"], "3350400")
        self.assertLess(payload["iat"], 1_700_000_000, "iat backdated for skew")
        self.assertLessEqual(
            payload["exp"] - 1_700_000_000, 600, "GitHub rejects exp > 10 min out"
        )

        # The signature must be real RSASSA-PKCS1-v1_5-SHA256 — openssl
        # itself is the arbiter, not our own code round-tripping.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "key.pem").write_text(self.key_pem)
            pub = subprocess.run(
                ["openssl", "rsa", "-in", str(Path(tmp) / "key.pem"), "-pubout"],
                capture_output=True,
                check=True,
            ).stdout
            (Path(tmp) / "pub.pem").write_bytes(pub)
            sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
            (Path(tmp) / "sig.bin").write_bytes(sig)
            (Path(tmp) / "data").write_bytes(f"{header_b64}.{payload_b64}".encode())
            verify = subprocess.run(
                [
                    "openssl", "dgst", "-sha256",
                    "-verify", str(Path(tmp) / "pub.pem"),
                    "-signature", str(Path(tmp) / "sig.bin"),
                    str(Path(tmp) / "data"),
                ],
                capture_output=True,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr.decode())

    def test_mint_exchanges_the_jwt_for_a_repo_scoped_token(self):
        requests = []

        def opener(req):
            requests.append(req)
            if req.full_url.endswith("/repos/dreadnought-foundry/bureau-harness/installation"):
                return 200, b'{"id": 4242}'
            if req.full_url.endswith("/app/installations/4242/access_tokens"):
                return 201, b'{"token": "ghs_fresh"}'
            raise AssertionError(f"unexpected call {req.full_url}")

        token = self.app_token.mint_installation_token(
            "3350400",
            self.key_pem,
            "dreadnought-foundry/bureau-harness",
            opener=opener,
        )
        self.assertEqual(token, "ghs_fresh")
        self.assertEqual(len(requests), 2)
        for req in requests:
            auth = req.headers["Authorization"]
            self.assertTrue(auth.startswith("Bearer "))
            self.assertEqual(auth.removeprefix("Bearer ").count("."), 2, "JWT auth")
        body = json.loads(requests[1].data)
        self.assertEqual(
            body, {"repositories": ["bureau-harness"]},
            "the mint must stay scoped to the single sandbox repo — the "
            "same scope harness.yml's own mint step names",
        )


class DriverWiringTest(unittest.TestCase):
    """__main__ builds the suppliers from the env; absent credentials fall
    back to the static-token behavior (local PAT runs)."""

    def test_supplier_absent_without_app_credentials(self):
        from harness import __main__ as harness_main

        self.assertIsNone(
            harness_main.token_supplier("worker", "", "", "o/r")
        )
        self.assertIsNone(
            harness_main.token_supplier("worker", "123", "", "o/r")
        )

    def test_supplier_mints_via_app_token(self):
        from harness import __main__ as harness_main

        minted = []

        def fake_mint(app_id, private_key_pem, repo):
            minted.append((app_id, private_key_pem, repo))
            return "ghs_fresh"

        supplier = harness_main.token_supplier(
            "worker", "123", "PEM", "o/r", mint=fake_mint, log=lambda *_: None
        )
        self.assertEqual(supplier(), "ghs_fresh")
        self.assertEqual(minted, [("123", "PEM", "o/r")])


class WorkflowWiringTest(unittest.TestCase):
    """harness.yml threads the App credentials so the driver can re-mint —
    the same secrets the job-start mint steps already read."""

    def _run_env(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        env = {}
        for step in doc["jobs"]["harness"].get("steps") or []:
            env.update(step.get("env") or {})
        return env

    def test_worker_and_qa_app_credentials_reach_the_driver(self):
        env = self._run_env()
        self.assertEqual(env.get("HARNESS_WORKER_APP_ID"), "${{ secrets.BUREAU_APP_ID }}")
        self.assertEqual(
            env.get("HARNESS_WORKER_APP_PRIVATE_KEY"),
            "${{ secrets.BUREAU_APP_PRIVATE_KEY }}",
        )
        self.assertEqual(env.get("HARNESS_QA_APP_ID"), "${{ secrets.BUREAU_QA_APP_ID }}")
        self.assertEqual(
            env.get("HARNESS_QA_APP_PRIVATE_KEY"),
            "${{ secrets.BUREAU_QA_APP_PRIVATE_KEY }}",
        )


if __name__ == "__main__":
    unittest.main()
