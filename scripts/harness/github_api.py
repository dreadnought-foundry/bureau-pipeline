"""Thin GitHub REST client for the harness driver (stdlib urllib only).

Deliberately minimal: exactly the calls the scenarios need, returning the
raw REST shapes so the fakes in tests mirror real payloads 1:1. Retries
transient 5xx/URLError blips with a short backoff; 4xx raises GitHubError
with the status so callers can branch on 404/409 (e.g. idempotent ref
deletes, branch-protection refusals).
"""

from __future__ import annotations

import base64
import collections
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

# CONDITIONAL READS (DRE-4132). The driver waits on the sandbox by asking the
# same URL again and again — `gate_paths`' stale leg asks two every five
# seconds for as long as a critic review takes — and GitHub bills every one of
# those although the answer has not changed. On 2026-09-17 that was most of
# what emptied the worker installation's 5,000 requests an hour: ~150 a minute
# with four harness runs live, and while it was empty every job in the fleet
# holding the worker token was refused.
#
# GitHub does NOT bill a conditional request it answers `304 Not Modified`
# (measured that day: 20 `If-None-Match` reads moved `x-ratelimit-used` by 0;
# the same 20 unconditional moved it by 20). So a client remembers each GET's
# ETag with the body it came with, sends `If-None-Match` the next time it asks
# that URL, and answers from memory on a 304. The poll keeps its cadence — the
# five seconds ARE the stale-verdict race — and only the unchanged answers
# stop costing anything. A 200 always replaces the memory, so the driver is
# never served anything older than GitHub's own reply.
#
# Bounded, oldest-out: forgetting a URL costs one billed read, never a wrong
# answer. 256 is several times the distinct URLs a wait ever cycles through.
ETAG_CACHE_ENTRIES = 256


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


#: How long a client with NO other identity to turn to (the worker) waits for
#: GitHub's meter to reset before sending a refused call once more (DRE-4575).
#: Sized against the SWEEP's budget, not the job's timeout: harness.yml's
#: scenario receipt warns past `BUDGET_MINUTES: "40"` — and that annotation
#: says a run that long "holds the sandbox, holds every queued run behind it,
#: and holds the release channel with them" — while healthy runs take
#: 9m40s–18m. A cap AT the budget would make every waited run trip it. 25
#: leaves the sweep its room and still covers the real refusal: the counter
#: that turned away run 35664409350's four attempts resets at about :04 past
#: the hour, so a refusal at :40 waits ~25.
RATE_LIMIT_WAIT_CAP_SECONDS = 25 * 60


class RateLimited(GitHubError):
    """A 403/429 whose body names GitHub's rate limit (DRE-4575).

    Still a GitHubError — every caller that catches the parent holds. The
    subclass carries `reset`: the epoch second the refusing counter resets,
    read off the response HEADERS (`retry-after`, else `x-ratelimit-reset`).
    The body alone — all the client read before this card — says nothing
    about how long a refusal lasts. None when GitHub sent neither header.
    """

    def __init__(self, status: int, message: str, reset: int | None = None):
        super().__init__(status, message)
        self.reset = reset


def _is_rate_limit_refusal(status: int, detail: str) -> bool:
    """`API rate limit exceeded for installation ID …` on a 403/429. A
    permission 403 (`Resource not accessible by integration`) is not one."""
    return status in (403, 429) and "rate limit" in detail.lower()


def _header(headers, name: str) -> str | None:
    """One header, whatever the mapping's case rules: an HTTPMessage matches
    case-insensitively, a test's plain dict does not."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for key in (name, name.lower(), name.title(), name.upper()):
        value = getter(key)
        if value is not None:
            return value
    return None


def _reset_note(err: "RateLimited", now: float) -> str:
    if err.reset is None:
        return "no reset named"
    return f"reset in {max(0, int((err.reset - now) // 60))}m"


def _reset_from_headers(headers, now: float) -> int | None:
    """When the counter that refused THIS call resets, as an epoch second.

    `retry-after` FIRST, `x-ratelimit-reset` only when it is absent — that is
    GitHub's documented order, and reading it the other way round is what the
    first cut of this card got wrong. A SECONDARY rate limit answers with
    `retry-after` (typically ~60 s) while the very same response still carries
    the PRIMARY window's `x-ratelimit-reset`, which can be the best part of an
    hour out. Preferring the primary header there turns a one-minute pause
    into a reset past the cap, and the client gives up on a refusal it only
    had to sit out.
    """
    raw = _header(headers, "retry-after")
    if raw is not None:
        try:
            return int(now + float(raw))
        except ValueError:
            pass
    raw = _header(headers, "x-ratelimit-reset")
    if raw is not None:
        try:
            return int(float(raw))
        except ValueError:
            pass
    return None


class GitHub:
    """One authenticated identity against api.github.com. The harness
    mints one client per actor (worker bot, …) — WHICH identity performs
    an action is the thing under test, so it is explicit, never ambient.

    A READ is not an action anyone attributes — no scenario asserts who
    asked — so a client may be given a `reader` (DRE-4282): another client,
    minted from a dispatch-pool App with headroom, that every
    `GET` this client would have sent goes out as instead. Writes stay this
    client's, and so does `current_token()`, the credential the agent
    scenarios clone and push with. The reader keeps the ETag memory and the
    spend ledger for the reads it makes, so `github-spend:` still says whose
    hour each request cost."""

    def __init__(
        self,
        token: str,
        api_url: str = API_URL,
        opener=None,
        token_supplier=None,
        clock=time.monotonic,
        conditional: bool = True,
        reader: "GitHub | None" = None,
        identity: str | None = None,
        fallback_suppliers=None,
        sleeper=time.sleep,
        wall_clock=time.time,
        log=print,
    ):
        self._token = token
        self._api = api_url.rstrip("/")
        # Who this client is, for the run log and the spend line (DRE-4575):
        # `identity` is who the requests go out as NOW, `identity_trail` every
        # identity in order — a reader that moved slots names both.
        self.identity = identity or "client"
        self.identity_trail = [self.identity]
        # (name, supply) pairs for OTHER pool slots, in the order to try them
        # when GitHub refuses this one for rate limit. Empty for a client whose
        # identity IS the thing under test (the worker): it waits instead.
        self._fallbacks = list(fallback_suppliers or [])
        self._sleeper = sleeper
        self._wall = wall_clock
        self._log = log
        # The client whose identity GETs go out as; None = this one.
        self._reader = reader
        # opener(urllib.request.Request) -> (status, bytes, headers);
        # injectable so the retry/error logic is unit-testable without a
        # network. The older (status, bytes) pair is still accepted.
        self._opener = opener or self._urlopen
        # token_supplier() -> fresh token; None = the token is static (a
        # PAT, or an App JWT) and expiry surfaces as the 401 it is.
        self._supplier = token_supplier
        self._clock = clock
        self._minted_at = clock()
        # url -> (etag, raw json bytes). Bytes, not the parsed object: every
        # answer is parsed fresh, so a scenario that mutates what it was
        # handed cannot poison the next poll. `conditional=False` is the off
        # switch — every read billed, exactly the pre-DRE-4132 client.
        self._conditional = conditional
        self._remembered: collections.OrderedDict = collections.OrderedDict()
        # The ledger the driver prints at the end of a run: `billed` is every
        # request GitHub charged to this identity's hour (errors included —
        # a 404 costs what a 200 does), `free` is every 304.
        self._billed = 0
        self._free = 0
        # One (identity, billed, free) snapshot per identity this client has
        # LEFT, taken at the moment it moved (DRE-4575). DRE-4132's ledger
        # exists so a run can say WHOSE hour it cost; a reader that moved
        # slots mid-run spent two installations' allowances and has to report
        # them separately, or the next dry slot is undiagnosable.
        self._ledger_marks: list[tuple[str, int, int]] = []

    @staticmethod
    def _urlopen(req: urllib.request.Request):
        # opener(req) -> (status, bytes, headers). The headers are how the
        # client learns an ETag; an injected opener that returns the older
        # (status, bytes) pair is still accepted and is simply never
        # conditional.
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return resp.status, resp.read(), resp.headers

    def spend(self) -> dict:
        """`{"billed": n, "free": m}` — requests GitHub charged to this
        identity's hourly allowance vs. 304s it did not (DRE-4132).

        The whole client, every identity it has been. `spend_history()` is
        the same totals split by the identity that actually paid them.
        """
        return {"billed": self._billed, "free": self._free}

    def spend_history(self) -> list:
        """`[(identity, {"billed": n, "free": m}), …]` — the ledger split per
        identity, in the order this client went out as them (DRE-4575).

        One entry for a client that never moved, so the caller needs no
        special case; the entries always sum to `spend()`.
        """
        out, billed, free = [], 0, 0
        for identity, at_billed, at_free in self._ledger_marks:
            out.append(
                (identity, {"billed": at_billed - billed, "free": at_free - free})
            )
            billed, free = at_billed, at_free
        out.append(
            (
                self.identity,
                {"billed": self._billed - billed, "free": self._free - free},
            )
        )
        return out

    def _remember(self, url: str, etag: str, payload: bytes) -> None:
        self._remembered[url] = (etag, payload)
        self._remembered.move_to_end(url)
        while len(self._remembered) > ETAG_CACHE_ENTRIES:
            self._remembered.popitem(last=False)

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
        no fixed margin can close).

        A GET goes out as the `reader` when one was given (DRE-4282) — its
        token, its re-mint, its ledger."""
        if method == "GET" and self._reader is not None:
            return self._reader.request(method, path, body)
        if self._supplier and self._clock() - self._minted_at >= TOKEN_REFRESH_SECONDS:
            self._remint()
        return self._send(lambda: self._attempt(method, path, body))

    def request_bytes(self, method: str, path: str) -> bytes:
        """One REST call whose body is NOT json — the Actions log archive is a
        zip (DRE-3076). Same auth, retry and re-mint path as `request`, and
        the same reader for a GET."""
        if method == "GET" and self._reader is not None:
            return self._reader.request_bytes(method, path)
        if self._supplier and self._clock() - self._minted_at >= TOKEN_REFRESH_SECONDS:
            self._remint()
        return self._send(lambda: self._attempt(method, path, raw=True))

    def _send(self, attempt):
        """One request through the refusal and expiry paths.

        A `RateLimited` answer is recovered by moving to the next identity
        this client can turn to — as many as it has, since a mint can fail —
        and then, when none is left, once by waiting; the request goes out
        again after each. When nothing recovers it the ORIGINAL refusal
        stands, never some later mint's error. A 401 re-mints once (the
        mint-time race no fixed margin can close) and re-enters this loop, so
        a refusal on the post-re-mint attempt is still moved or waited out.
        Anything else raises as it always did.
        """
        waited = reminted = False
        while True:
            try:
                return attempt()
            except RateLimited as e:
                if self._move_to_next_identity(e):
                    continue
                if not waited and self._wait_for_reset(e):
                    waited = True
                    continue
                raise
            except GitHubError as e:
                if e.status == 401 and not reminted and self._remint():
                    reminted = True
                    continue
                raise

    def _move_to_next_identity(self, err: RateLimited) -> bool:
        """A READER's answer to a refusal (DRE-4575): mint from the next pool
        slot and go out as it from now on. A read is not an action anyone
        attributes, so the identity may move; and the slot moved TO becomes
        this client's re-mint source, or the 50-minute refresh would put the
        late scenarios straight back on the refused one.

        Why not the same slot again: run 35664409350's four attempts were
        refused on every call for the rest of the hour — the counter behind
        the refusal was empty, not blipping.

        THE MINT IS GUARDED, and nothing is committed until it returns. A
        supply() is two live REST calls behind an openssl signature
        (`app_token.mint_installation_token`) and can raise — 404 when the App
        is not installed on the sandbox, 401, 5xx, an openssl failure. Left
        unguarded that one unlucky moment replaced a recoverable refusal with
        an unrelated error, skipped every healthy slot still in the list, and
        left `_supplier` pointing at the dead slot so every later re-mint
        raised too. So: try each slot in turn, log the ones that will not
        mint, and return False when the list is spent — the client is then
        exactly where it was, and `_send` still has the wait to try.
        """
        while self._fallbacks:
            name, supply = self._fallbacks.pop(0)
            self._log(
                f"github: {self.identity} refused for rate limit "
                f"({_reset_note(err, self._wall())}) — re-minting from {name} "
                "and sending the request again as it"
            )
            try:
                token = supply()
            except Exception as mint_error:  # noqa: BLE001 — see the docstring
                self._log(
                    f"github: {name} would not mint ({mint_error}) — staying "
                    f"as {self.identity} and trying the next slot"
                )
                continue
            self._ledger_marks.append((self.identity, self._billed, self._free))
            self._supplier = supply
            self._token = token
            self._minted_at = self._clock()
            self.identity = name
            self.identity_trail.append(name)
            return True
        return False

    def _wait_for_reset(self, err: RateLimited) -> bool:
        """The WORKER's answer to a refusal (DRE-4575): its identity IS the
        thing under test, so it cannot move — it waits for the counter to
        reset, bounded by RATE_LIMIT_WAIT_CAP_SECONDS, and sends once more.
        False when there is no reset to read or it is past the cap: the
        refusal stands, and the run says why."""
        if err.reset is None:
            self._log(
                f"github: {self.identity} refused for rate limit and GitHub "
                "named no reset — nothing to wait for"
            )
            return False
        wait = err.reset - self._wall()
        if wait > RATE_LIMIT_WAIT_CAP_SECONDS:
            self._log(
                f"github: {self.identity} refused for rate limit and the reset "
                f"is {int(wait // 60)}m out — past the "
                f"{RATE_LIMIT_WAIT_CAP_SECONDS // 60}m cap, not waiting"
            )
            return False
        wait = max(wait, 0.0) + 1.0
        self._log(
            f"github: {self.identity} refused for rate limit — waiting "
            f"{int(wait)}s for the reset, then sending the request once more"
        )
        self._sleeper(wait)
        return True

    def _attempt(self, method: str, path: str, body: dict | None = None,
                 raw: bool = False):
        url = path if path.startswith("http") else f"{self._api}{path}"
        data = json.dumps(body).encode() if body is not None else None
        # Only a JSON GET is ever conditional: a write must always be sent,
        # and the raw log archive is read once and is not worth remembering.
        recall = (
            self._remembered.get(url)
            if self._conditional and method == "GET" and not raw
            else None
        )
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
                    **({"If-None-Match": recall[0]} if recall else {}),
                },
            )
            try:
                answer = self._opener(req)
                status, payload = answer[0], answer[1]
                headers = answer[2] if len(answer) > 2 else None
                self._billed += 1
                if raw:
                    return payload or b""
                etag = headers.get("ETag") if headers is not None else None
                if self._conditional and method == "GET" and etag and payload:
                    self._remember(url, etag, payload)
                return json.loads(payload) if payload else None
            except urllib.error.HTTPError as e:
                if e.code == 304 and recall:
                    # Unchanged since we last asked, and not billed. Answer
                    # from memory — parsed fresh, see __init__.
                    self._free += 1
                    if url in self._remembered:
                        self._remembered.move_to_end(url)
                    return json.loads(recall[1])
                self._billed += 1
                detail = e.read().decode(errors="replace")[:500]
                if _is_rate_limit_refusal(e.code, detail):
                    # Named, with the reset off the HEADERS — `retry-after`
                    # first, `x-ratelimit-reset` second (see
                    # _reset_from_headers: a secondary limit sends both, and
                    # only the first is about THIS refusal). The body alone
                    # was all this read before DRE-4575, and it says nothing
                    # about how long the refusal lasts.
                    raise RateLimited(
                        e.code, detail, _reset_from_headers(e.headers, self._wall())
                    ) from e
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

    def list_recent_runs(self, repo, per_page: int = 50) -> list[dict]:
        """The sandbox's most recent runs of ANY status, newest first.

        Deliberately not `list_workflow_runs`, which filters to `completed`:
        this call answers "is anything happening at all?" (DRE-3453), and a
        critic review still IN PROGRESS is the loudest possible yes. Reading
        the completed-only listing would have called a 40-minute review an
        idle sandbox — the false FAIL on a healthy pipeline that run
        33274348041 already cost us once.
        """
        out = self.request(
            "GET", f"/repos/{repo}/actions/runs?per_page={int(per_page)}"
        )
        runs = out.get("workflow_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    def list_workflow_runs_for(
        self, repo, workflow_file: str, event: str | None = None,
        per_page: int = 30,
    ) -> list[dict]:
        """Runs of ONE workflow file, newest first — the rehearsal's way of
        finding the run its dispatch produced (DRE-3486).

        A `repository_dispatch` answers 204 with no run id, so the run has to
        be found afterwards; scoping the listing to the workflow file and the
        event keeps that search to one page. A 404 here means the sandbox has
        no such workflow at all, and the caller says so rather than reporting
        a workflow that does not parse.
        """
        query = f"per_page={int(per_page)}"
        if event:
            query += f"&event={urllib.parse.quote(event)}"
        out = self.request(
            "GET",
            f"/repos/{repo}/actions/workflows/"
            f"{urllib.parse.quote(workflow_file)}/runs?{query}",
        )
        runs = out.get("workflow_runs") if isinstance(out, dict) else None
        return runs if isinstance(runs, list) else []

    def get_workflow_run(self, repo, run_id) -> dict:
        """One run record. Carries `referenced_workflows` — which reusable
        workflow a caller actually compiled, and at which commit."""
        out = self.request("GET", f"/repos/{repo}/actions/runs/{int(run_id)}")
        return out if isinstance(out, dict) else {}

    def list_run_jobs(self, repo, run_id) -> dict:
        """The run's jobs, as GitHub's `{total_count, jobs}` envelope.

        The COUNT is the load-bearing part: a workflow GitHub refused to
        compile concludes having started nothing, and `total_count: 0` on a
        completed run is that refusal's signature (2026-09-09, DRE-3484).
        """
        out = self.request(
            "GET", f"/repos/{repo}/actions/runs/{int(run_id)}/jobs?per_page=100"
        )
        return out if isinstance(out, dict) else {}

    def run_timing(self, repo, run_id) -> dict:
        """Billable runner time for a run. Corroborates a zero-job refusal:
        GitHub bills nothing at all for a run that never started a job."""
        out = self.request("GET", f"/repos/{repo}/actions/runs/{int(run_id)}/timing")
        return out if isinstance(out, dict) else {}

    def cancel_workflow_run(self, repo, run_id) -> bool:
        """Cancel a run; False when GitHub says it is already finished.

        The rehearsal proves the workflow PARSES and stops there — it must not
        leave a real build agent running in the sandbox behind it.
        """
        try:
            self.request(
                "POST", f"/repos/{repo}/actions/runs/{int(run_id)}/cancel"
            )
            return True
        except GitHubError as e:
            if e.status in (404, 409):
                return False
            raise

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

    # ── repository_dispatch (the relay's own door, DRE-3486) ────────────
    def repository_dispatch(self, repo, event_type: str, client_payload: dict) -> None:
        """Fire the event the bureau-linear-relay fires.

        The rehearsal uses the same door the fleet does on purpose: GitHub
        validates a CALLED workflow only at dispatch, so a stub that parses as
        YAML and passes every linter can still be refused at the moment it is
        used — which is exactly what happened to `agent-task.yml` on
        2026-09-09. Answers 204 with no body and no run id.
        """
        self.request(
            "POST",
            f"/repos/{repo}/dispatches",
            {"event_type": event_type, "client_payload": dict(client_payload or {})},
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
