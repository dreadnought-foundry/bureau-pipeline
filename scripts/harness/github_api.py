"""Thin GitHub REST client for the harness driver (stdlib urllib only).

Deliberately minimal: exactly the calls the scenarios need, returning the
raw REST shapes so the fakes in tests mirror real payloads 1:1. Retries
transient 5xx/URLError blips with a short backoff; 4xx raises GitHubError
with the status so callers can branch on 404/409 (e.g. idempotent ref
deletes, branch-protection refusals).
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.github.com"
_RETRIES = 3
_BACKOFF_SECONDS = 5


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    """One authenticated identity against api.github.com. The harness
    mints one client per actor (worker bot, …) — WHICH identity performs
    an action is the thing under test, so it is explicit, never ambient."""

    def __init__(self, token: str, api_url: str = API_URL, opener=None):
        self._token = token
        self._api = api_url.rstrip("/")
        # opener(urllib.request.Request) -> (status, bytes); injectable so
        # the retry/error logic is unit-testable without a network.
        self._opener = opener or self._urlopen

    @staticmethod
    def _urlopen(req: urllib.request.Request):
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return resp.status, resp.read()

    def request(self, method: str, path: str, body: dict | None = None):
        """One REST call, retried through transient failures. Returns the
        parsed JSON (None for empty responses)."""
        url = path if path.startswith("http") else f"{self._api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        last_error: Exception | None = None
        for attempt in range(1, _RETRIES + 1):
            req = urllib.request.Request(  # nosec B310 — https API host only
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "bureau-pipeline-harness",
                    **({"Content-Type": "application/json"} if data else {}),
                },
            )
            try:
                status, payload = self._opener(req)
                return json.loads(payload) if payload else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code >= 500 and attempt < _RETRIES:
                    last_error = GitHubError(e.code, detail)
                else:
                    raise GitHubError(e.code, detail) from e
            except urllib.error.URLError as e:
                if attempt >= _RETRIES:
                    raise GitHubError(0, str(e)) from e
                last_error = e
            time.sleep(_BACKOFF_SECONDS * attempt)
        raise GitHubError(0, f"exhausted retries: {last_error}")

    # ── repo / refs ──────────────────────────────────────────────────────
    def default_branch(self, repo: str) -> tuple[str, str]:
        name = self.request("GET", f"/repos/{repo}")["default_branch"]
        ref = self.request("GET", f"/repos/{repo}/git/ref/heads/{name}")
        return name, ref["object"]["sha"]

    def matching_refs(self, repo: str, prefix: str) -> list[str]:
        """Branch names starting with `prefix` (the sweep's input)."""
        quoted = urllib.parse.quote(prefix)
        refs = self.request("GET", f"/repos/{repo}/git/matching-refs/heads/{quoted}")
        return [r["ref"].removeprefix("refs/heads/") for r in refs or []]

    def create_ref(self, repo: str, branch: str, sha: str) -> None:
        self.request(
            "POST",
            f"/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def delete_ref(self, repo: str, branch: str) -> bool:
        """Idempotent: True if deleted, False if it was already gone."""
        try:
            self.request(
                "DELETE",
                f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch)}",
            )
            return True
        except GitHubError as e:
            if e.status in (404, 422):
                return False
            raise

    # ── contents (one commit per call, authored as this client) ─────────
    def put_file(self, repo, branch, path, content, message) -> str:
        """Create/update one file on `branch`; returns the new commit sha."""
        body = {
            "message": message,
            "branch": branch,
            "content": base64.b64encode(content.encode()).decode(),
        }
        existing = self.get_file_sha(repo, path, branch)
        if existing:
            body["sha"] = existing
        out = self.request(
            "PUT", f"/repos/{repo}/contents/{urllib.parse.quote(path)}", body
        )
        return out["commit"]["sha"]

    def get_file_sha(self, repo, path, ref):
        try:
            out = self.request(
                "GET",
                f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref="
                + urllib.parse.quote(ref),
            )
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        return out.get("sha") if isinstance(out, dict) else None

    def list_dir(self, repo, path, ref) -> list[dict]:
        """Directory listing on `ref`; [] when the directory is absent."""
        try:
            out = self.request(
                "GET",
                f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref="
                + urllib.parse.quote(ref),
            )
        except GitHubError as e:
            if e.status == 404:
                return []
            raise
        return out if isinstance(out, list) else []

    def delete_file(self, repo, branch, path, message) -> bool:
        sha = self.get_file_sha(repo, path, branch)
        if not sha:
            return False
        self.request(
            "DELETE",
            f"/repos/{repo}/contents/{urllib.parse.quote(path)}",
            {"message": message, "branch": branch, "sha": sha},
        )
        return True

    # ── commits / checks ─────────────────────────────────────────────────
    def get_commit(self, repo, sha: str) -> dict:
        """The full commit record — parents (update-branch merge shape) and
        the author/committer identities GitHub attributes it to."""
        return self.request("GET", f"/repos/{repo}/commits/{sha}")

    def list_check_runs(self, repo, sha: str) -> list[dict]:
        """Check runs on a commit (the record merge-gate.yml itself reads —
        the qa App token is the proven reader for it)."""
        out = self.request(
            "GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100"
        )
        runs = out.get("check_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    # ── pull requests ────────────────────────────────────────────────────
    def create_pr(self, repo, head, base, title, body) -> dict:
        return self.request(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def get_pr(self, repo, number: int) -> dict:
        return self.request("GET", f"/repos/{repo}/pulls/{number}")

    def list_open_prs(self, repo) -> list[dict]:
        # One page of 100 is far beyond anything the sandbox accumulates;
        # the sweep logs what it saw so a silent cap can't hide leftovers.
        return self.request("GET", f"/repos/{repo}/pulls?state=open&per_page=100") or []

    def close_pr(self, repo, number: int) -> None:
        self.request("PATCH", f"/repos/{repo}/pulls/{number}", {"state": "closed"})

    def list_pr_commits(self, repo, number: int) -> list[dict]:
        """The PR's commit list — carries Dependabot's machine-readable
        update-type trailer (the semver signal merge_gate condition D reads)."""
        return (
            self.request("GET", f"/repos/{repo}/pulls/{number}/commits?per_page=100")
            or []
        )

    def list_comments(self, repo, number: int) -> list[dict]:
        return (
            self.request(
                "GET", f"/repos/{repo}/issues/{number}/comments?per_page=100"
            )
            or []
        )

    def create_comment(self, repo, number: int, body: str) -> dict:
        return self.request(
            "POST", f"/repos/{repo}/issues/{number}/comments", {"body": body}
        )
