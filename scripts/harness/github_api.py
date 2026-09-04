"""Thin GitHub REST client for the harness driver (stdlib urllib only).

Deliberately minimal: exactly the calls the scenarios need, returning the
raw REST shapes so the fakes in tests mirror real payloads 1:1. Retries
transient 5xx/URLError blips with a short backoff; 4xx raises GitHubError
with the status so callers can branch on 404/409 (e.g. idempotent ref
deletes, branch-protection refusals).
"""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API_URL = "https://api.github.com"
_RETRIES = 3
_BACKOFF_SECONDS = 5

# Bounds on the Actions log archive read (DRE-3076). The driver wants one
# failing LINE out of a sandbox run; it must never pull a multi-megabyte
# archive into memory while a scenario is already past its deadline.
_LOG_MEMBER_CAP = 40
_LOG_BYTE_CAP = 256 * 1024

# App installation tokens die exactly one hour after mint. A client given
# a token_supplier re-mints proactively at 50 minutes — comfortably inside
# the hour — so a long scenario run never carries a corpse into its late
# scenarios (run 29795108949: gate_paths 401ed in verify AND cleanup).
TOKEN_REFRESH_SECONDS = 50 * 60


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHub:
    """One authenticated identity against api.github.com. The harness
    mints one client per actor (worker bot, …) — WHICH identity performs
    an action is the thing under test, so it is explicit, never ambient."""

    def __init__(
        self,
        token: str,
        api_url: str = API_URL,
        opener=None,
        token_supplier=None,
        clock=time.monotonic,
    ):
        self._token = token
        self._api = api_url.rstrip("/")
        # opener(urllib.request.Request) -> (status, bytes); injectable so
        # the retry/error logic is unit-testable without a network.
        self._opener = opener or self._urlopen
        # token_supplier() -> fresh token; None = the token is static (a
        # PAT, or an App JWT) and expiry surfaces as the 401 it is.
        self._supplier = token_supplier
        self._clock = clock
        self._minted_at = clock()

    @staticmethod
    def _urlopen(req: urllib.request.Request):
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return resp.status, resp.read()

    def _remint(self) -> bool:
        """Swap in a fresh token from the supplier; False when the client
        was built with a static token only."""
        if not self._supplier:
            return False
        self._token = self._supplier()
        self._minted_at = self._clock()
        return True

    def current_token(self) -> str:
        """The live token, re-minted first if it is past the refresh window.

        For anything that hands the credential to ANOTHER process — the
        DRE-2490 agent scenarios' `git clone` and agent CLI — which cannot
        ride request()'s reactive 401 retry: a long run's later scenarios
        would otherwise clone with an expired token (run 29795108949's class,
        one layer out).
        """
        if self._supplier and self._clock() - self._minted_at >= TOKEN_REFRESH_SECONDS:
            self._remint()
        return self._token

    def request(self, method: str, path: str, body: dict | None = None):
        """One REST call, retried through transient failures. Returns the
        parsed JSON (None for empty responses). With a token_supplier the
        token is refreshed before it ages past TOKEN_REFRESH_SECONDS, and
        once reactively when GitHub answers 401 anyway (the mint-time race
        no fixed margin can close)."""
        if self._supplier and self._clock() - self._minted_at >= TOKEN_REFRESH_SECONDS:
            self._remint()
        try:
            return self._attempt(method, path, body)
        except GitHubError as e:
            if e.status == 401 and self._remint():
                return self._attempt(method, path, body)
            raise

    def request_bytes(self, method: str, path: str) -> bytes:
        """One REST call whose body is NOT json — the Actions log archive is a
        zip (DRE-3076). Same auth, retry and re-mint path as `request`."""
        if self._supplier and self._clock() - self._minted_at >= TOKEN_REFRESH_SECONDS:
            self._remint()
        try:
            return self._attempt(method, path, raw=True)
        except GitHubError as e:
            if e.status == 401 and self._remint():
                return self._attempt(method, path, raw=True)
            raise

    def _attempt(self, method: str, path: str, body: dict | None = None,
                 raw: bool = False):
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
                if raw:
                    return payload or b""
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

    def list_tree(self, repo, ref) -> list[str]:
        """Every blob path in `repo` at `ref`, in one recursive call.

        The lane-contract scenario locates the console's state-list module BY
        NAME rather than by a remembered path (DRE-2726) — a path written down
        here is exactly the enumeration of a derivable set this repo keeps
        being bitten by. A truncated tree is returned as far as it goes; the
        caller reports "not found" as unknown, never as agreement.
        """
        try:
            out = self.request(
                "GET",
                f"/repos/{repo}/git/trees/{urllib.parse.quote(ref)}?recursive=1",
            )
        except GitHubError as e:
            if e.status == 404:
                return []
            raise
        if not isinstance(out, dict):
            return []
        return [
            entry.get("path", "")
            for entry in out.get("tree") or []
            if entry.get("type") == "blob"
        ]

    def get_file(self, repo, path, ref) -> str | None:
        """A file's decoded text at `ref` (a branch name or a sha), or None
        when it is absent there. What the driver reads to see what a PR's head
        actually contains."""
        try:
            out = self.request(
                "GET",
                f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref="
                + urllib.parse.quote(ref),
            )
        except GitHubError as e:
            if e.status in (404, 422):
                return None
            raise
        if not isinstance(out, dict) or out.get("encoding") != "base64":
            return None
        return base64.b64decode(out.get("content") or "").decode(errors="replace")

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

    def last_commit_date(self, repo, ref: str, path: str | None = None) -> str | None:
        """When `ref` (or the last commit touching `path` on it) was
        committed, as GitHub's ISO8601 string — None when there is nothing
        to read.

        The sweep's only way to tell a dead run's leftover from a live
        run's (DRE-3075). None is returned rather than raised for an absent
        ref/path, because "I could not date it" and "it is old" must stay
        different answers: the sweep deletes only on the second.
        """
        try:
            if path is None:
                out = self.request(
                    "GET", f"/repos/{repo}/commits/{urllib.parse.quote(ref)}"
                )
                commits = [out] if isinstance(out, dict) else []
            else:
                out = self.request(
                    "GET",
                    f"/repos/{repo}/commits?sha={urllib.parse.quote(ref)}"
                    f"&path={urllib.parse.quote(path)}&per_page=1",
                )
                commits = out if isinstance(out, list) else []
        except GitHubError as e:
            if e.status in (404, 409, 422):
                return None
            raise
        if not commits:
            return None
        committer = (commits[0].get("commit") or {}).get("committer") or {}
        return committer.get("date")

    def list_check_runs(self, repo, sha: str) -> list[dict]:
        """Check runs on a commit (the record merge-gate.yml itself reads —
        the qa App token is the proven reader for it)."""
        out = self.request(
            "GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100"
        )
        runs = out.get("check_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    # ── actions (is the sandbox's own machinery alive? DRE-3076) ─────────
    def list_workflow_runs(self, repo, per_page: int = 50) -> list[dict]:
        """The sandbox's most recent COMPLETED workflow runs, newest first.

        One call answers "what did the sweep / the gate / linear-sync last
        do?" — the question a scenario that has been waiting past its deadline
        needs answered before it decides the sandbox is dead.
        """
        out = self.request(
            "GET",
            f"/repos/{repo}/actions/runs?status=completed&per_page={int(per_page)}",
        )
        runs = out.get("workflow_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    def run_log_text(self, repo, run_id) -> str | None:
        """A completed run's logs as text, or None when GitHub will not serve
        them (410 past retention, 403 without `actions: read`, 404).

        GitHub answers with a zip archive of one file per step; the driver
        wants the failing LINE, so the members are concatenated in name order
        — the same text `gh run view --log` prints, which is what
        `medic_classify` already reads.
        """
        try:
            payload = self.request_bytes(
                "GET", f"/repos/{repo}/actions/runs/{int(run_id)}/logs"
            )
        except (GitHubError, ValueError, TypeError):
            return None
        if not payload:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = sorted(
                    n for n in archive.namelist() if not n.endswith("/")
                )
                chunks = []
                for name in names[:_LOG_MEMBER_CAP]:
                    with archive.open(name) as member:
                        chunks.append(
                            member.read(_LOG_BYTE_CAP).decode(errors="replace")
                        )
            return "\n".join(chunks)
        except (zipfile.BadZipFile, OSError):
            # Not a zip: GitHub occasionally serves plain text on small runs.
            return payload.decode(errors="replace")

    # ── pull requests ────────────────────────────────────────────────────
    def create_pr(self, repo, head, base, title, body) -> dict:
        return self.request(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def get_pr(self, repo, number: int) -> dict:
        return self.request("GET", f"/repos/{repo}/pulls/{number}")

    def list_prs(self, repo, state: str = "all") -> list[dict]:
        """PRs in any state, newest first — the adversarial scenarios have to
        see a PR the sandbox's gate already merged (or the agent closed), not
        only the ones still open."""
        return (
            self.request(
                "GET",
                f"/repos/{repo}/pulls?state={urllib.parse.quote(state)}"
                "&sort=created&direction=desc&per_page=100",
            )
            or []
        )

    def list_pr_files(self, repo, number: int) -> list[dict]:
        """The files a PR contributes: filename + blob sha. The blob sha is
        the byte-identity evidence — same names AND same blobs means the same
        diff, which is what portico#316 resubmitted."""
        return (
            self.request("GET", f"/repos/{repo}/pulls/{number}/files?per_page=100")
            or []
        )

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

    # ── issues (the sandbox's carrier for a seeded card, DRE-2490) ───────
    def create_issue(self, repo, title: str, body: str) -> dict:
        return self.request(
            "POST", f"/repos/{repo}/issues", {"title": title, "body": body}
        )

    def list_issues(self, repo) -> list[dict]:
        """Open ISSUES only. GitHub's issues endpoint also returns PRs; they
        carry a `pull_request` key, and a sweep that closed those would close
        the very PRs the scenarios are asserting on."""
        out = (
            self.request("GET", f"/repos/{repo}/issues?state=open&per_page=100")
            or []
        )
        return [i for i in out if "pull_request" not in i]

    def close_issue(self, repo, number: int) -> None:
        self.request("PATCH", f"/repos/{repo}/issues/{number}", {"state": "closed"})
