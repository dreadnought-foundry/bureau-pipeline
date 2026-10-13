#!/usr/bin/env python3
"""The groom run's lookups — the leg that reads GitHub (DRE-5308).

Before each card reaches the verify agent (`groom_verify_agent.py`), the groom
run looks for merged pull requests that touched the files the card names,
across every repo in `config/repo-map.json`. On 2026-09-29 that search failed
for every card one morning — the App token's installation read failed — and
every card was judged on nothing. The repos sit under three owners and an App
installation token sees one installation, so the GitHub half runs once per
owner, on that owner's own token: this module's `owner` command, run by the
lookup job's matrix once per owner (DRE-5429), reading the target rows the
groom job wrote and writing one document per owner.

## What one leg does

For each target row the leg reads — `lookups.looked_up` true, its paths in
`lookups.paths` — in target order (the Planning list, then the spares, so a cut
lands on spares first), and for each of this owner's repos in the map:

    GET /repos/{owner}/{repo}/commits?path=<path>&since=<created_at>&per_page=MAX_COMMITS
    GET /repos/{owner}/{repo}/commits/{sha}/pulls     (each sha once per repo)

keeping the merged pull requests. **A commits list cut at `MAX_COMMITS` is
named, never silent**: GitHub returns the newest commits first, so a list that
stops at five is not "nothing merged". A `Link` header with `rel="next"` puts
`{"repo", "path"}` in the card's `cut`; the leg never pages.

## What stops it, and what it says when it does

  * **No token** — the owner is `read: false`, no request is made.
  * **A repo that refuses** (any `gh` failure but a rate limit, or a request
    that reaches `REQUEST_TIMEOUT`) — named in the card's `unread_repos`, and
    the next repo is asked.
  * **A rate limit** — the leg stops; every card not finished is `read: false`.
  * **The request budget** — `--budget` when given, otherwise
    `min(MAX_REQUESTS, floor(remaining × BUDGET_SHARE))` with `remaining` read
    off the first answered response's `x-ratelimit-remaining` header (the
    reading `dispatch_pool` ranks on; never `GET /rate_limit`).
  * **The clock** — read at the start and before every request; past
    `MAX_SECONDS` no request is made. Every request also runs under
    `REQUEST_TIMEOUT`, so the leg ends at most that long after the clock.

The document is written on every path but a usage error, so a leg stopped by
either budget still uploads what it had. **Whether a card's lookup as a whole
succeeded is not this leg's to say** — a card whose files live under another
owner is not answered by this leg reading `[]` for it. That rule is DRE-5458's
`fold`, which reads this document as declared in `owner`'s docstring.

## Before the legs, and after them (DRE-5458)

  * `cards` — in the groom job, which holds the Linear key: each target row's
    paths (`paths_of`), and ONE Linear request per file-naming card for the
    newer cards naming those paths. It writes the row's `lookups` block, which
    the `owner` legs read.
  * `fold` — in the verify leg: every leg's document folded into the rows,
    and each looked-up card's `ok` decided **per card**. A mapped card is `ok`
    only when the repo its own files live in answered, whatever the other
    owners said — they are named in its evidence as not covered. An unmapped
    card is `ok` when any repo in the map answered. A card naming no file was
    never asked anything and is left as `cards` wrote it.

## The work matched to a card's problem (DRE-7236)

On 2026-10-10 two removed cards went unseen: DRE-4049's problem had been fixed
in another file under DRE-5942, and DRE-5657's delivered by DRE-5658. A file
lookup finds neither. So `cards` also searches each row's `problem_terms` —
the read door's search first, Linear's when the door cannot answer — keeps
the Done cards completed since the card was filed, and writes them, with the
pull requests on their attachments, to `row["matched_work"]`. The `owner`
legs read those pull requests in their own repos after the path lookups, and
`fold` writes them onto every row that carries them. A card naming no file
is searched, and its pull requests read, like any other; its `lookups` block,
and every other row's, is exactly what it was. `cards` states the contract.

CLI:

    python3 scripts/groom_lookups.py owner --owner <owner> --targets <file> \\
        --out <file> [--budget <text>] [--repo-map <file>]
    python3 scripts/groom_lookups.py fold --targets <file> \\
        --lookups-dir <dir> --out <file> [--repo-map <file>]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import string
import subprocess  # nosec B404 — a fixed-arg `gh` read, argv list, never a shell
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bureau_read  # noqa: E402 — the read door's search, before Linear's
import dispatch_pool  # noqa: E402 — the one case-insensitive header reader
import groomer  # noqa: E402 — `_FILE_REF`, the one reader of a card's paths
import sanitize_untrusted  # noqa: E402 — Linear text is fenced data

ROOT = Path(__file__).resolve().parent.parent
REPO_MAP = ROOT / "config" / "repo-map.json"

# --------------------------------------------------------------------------- #
# the contract constants                                                       #
# --------------------------------------------------------------------------- #

#: The most paths a card is looked up by. DRE-5458's `paths_of` caps at this.
MAX_PATHS = 3

#: The commits one query asks for (`per_page`). Past it the cut is named.
MAX_COMMITS = 5

#: The most requests one leg makes: what `MAX_SECONDS` can spend at about half
#: a second a call. One card costs one repo at most
#: MAX_PATHS × (1 + MAX_COMMITS) = 18; an ordinary card far fewer.
MAX_REQUESTS = 600

#: The share of the installation bucket a leg may draw when no `--budget` is
#: given — 1,250 of a full 5,000-an-hour bucket, so a morning the bucket is
#: already drawn on caps the leg lower.
BUDGET_SHARE = 0.25

#: The leg's wall clock, in seconds: the lookup stage sits serially between
#: groom and verify, and DRE-4968 needs the proposal posted by 06:30 PT.
MAX_SECONDS = 300

#: The `subprocess.run` timeout on every `gh api` call, so one hung request
#: carries the leg past `MAX_SECONDS` by at most this. DRE-5429 sizes the job
#: timeout above `MAX_SECONDS + REQUEST_TIMEOUT`.
REQUEST_TIMEOUT = 20

#: The credential the leg spends: this owner's App installation token.
TOKEN_ENV = "GH_TOKEN"

# The `why` values, exact (the contract DRE-5458's `fold` reads).
BUDGET_SPENT = "request budget of {n} spent"
TIME_SPENT = "time budget of {n} s spent"
RATE_LIMITED = "rate limited: {message}"
NO_TOKEN = "no token for {owner}: the App's installation could not be minted"
TIMED_OUT = "timed out after {n} s"
# Two that only a row or a map breaking its own contract can produce: a card
# asked with no creation date would be told of every merge to its files ever
# made, and an owner with no repo has nothing to be asked.
NO_CREATED_AT = "no creation date on the row to ask since"
NO_REPOS = "no repo of {owner} in the repo map"

USAGE_BUDGET = ('groom-lookups: --budget must be empty or a whole number, '
                'got "{text}"')

#: The lookup state every verdict carries (DRE-5458), exact. `ok`: the card's
#: home repo answered (any repo, for an unmapped card); `failed`: it did not;
#: `none`: the card names no file, or is excluded; `not-run`: no `fold` ran.
#: DRE-5317 reads it for the morning-wide stop.
LOOKUP_STATES = ("ok", "failed", "none", "not-run")
#: The `unverified` reason prefix of a mapped card whose own repo did not
#: answer: this, then the row's `lookups.why`.
LOOKUP_FAILED = "lookup failed: "

#: How many cards one aliased `searchIssues` field asks Linear for.
SEARCH_FIRST = 25

# --- the matched work (DRE-7236): the contract the input card reads -------- #
#: The most terms one card is searched by: its identifier, its paths, its
#: backticked spans and its title's two-word phrases, in that order.
MAX_TERMS = 12
#: The most backticked spans, and the length a span must have.
MAX_SPANS = 3
SPAN_MIN, SPAN_MAX = 4, 80
#: The most two-word phrases from the title, and the letters each word needs.
MAX_PHRASES = 6
PHRASE_WORD_MIN = 3
#: Words a title phrase never holds: a phrase of them finds every card.
STOPWORDS = (
    "about", "above", "after", "again", "all", "also", "and", "any", "are",
    "because", "been", "before", "being", "below", "both", "but", "can",
    "could", "did", "does", "doing", "done", "each", "either", "every", "for",
    "from", "had", "has", "have", "her", "here", "his", "how", "into", "its",
    "just", "may", "might", "more", "most", "must", "neither", "nor", "not",
    "now", "off", "once", "one", "only", "onto", "other", "our", "out",
    "over", "own", "per", "same", "shall", "should", "some", "still", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "too", "under", "very", "via", "was", "were", "what",
    "when", "where", "which", "while", "who", "whom", "whose", "why", "will",
    "with", "would", "yet", "you", "your",
)
#: The most search hits one match request reads: the Done ones, the most
#: terms matched first. It bounds the request, never the contract's cap.
MAX_CANDIDATES = 20
#: The most matched cards a row carries, the most pull requests per card,
#: and the parent levels one match request reads.
MAX_MATCHED_CARDS = 8
MAX_MATCHED_PRS = 3
MAX_ANCESTRY = 4
#: The most files read off one matched pull request (`per_page`), and the
#: characters its body is cut at.
MAX_PR_FILES = 30
PR_BODY_LIMIT = 1500
#: The one lane a matched card is in.
DONE = "Done"
# The `why` values of `matched_work`, exact.
NOT_SEARCHED = "not searched"
EXCLUDED_NOT_SEARCHED = "the card is excluded, so nothing was searched"
NO_LINEAR_KEY = "no Linear key, so nothing was searched"
DOOR_HELD = ("the read door says {reason}: Linear was not asked, so its held "
             "bucket is not spent")
NOT_LOOKED_UP = "not looked up"
PR_NOT_IN_MAP = "{repo} is not in the repo map"
#: The `fold` command's input files, found at any depth under --lookups-dir.
LOOKUPS_GLOB = "lookups-*.json"

# The evidence lines, exact.
NO_FILE_LINE = "the card names no file, so nothing was looked up"
NEWER_CARD_LINE = "newer card {identifier} ({state}) names {path}: {title}"
LEFT_OUT_LINE = "{n} more path(s) were not looked up"
MERGED_LINE = ("merged pull request #{number} in {repo} touched {path} on "
               "{day}: {title}")
CUT_LINE = ("more than {n} commits touched {path} in {repo} since the card was "
            "filed; only the newest {n} were followed to pull requests, so an "
            "older merged pull request may be missing")
NOT_COVERED_LINE = "the lookup did not cover {what}: {why}"
# The `why` values `fold` writes, exact.
NO_RECORD = "no lookup record for {owner}"
DID_NOT_ANSWER = "{repo} did not answer — {why}"
NO_REPO_ANSWERED = "no repo answered — {whys}"

_LIST_ORDER = {"planning": 0, "spare": 1}
_NEXT = re.compile(r'rel="?next"?')
_DIGITS = re.compile(r"[0-9]+")
_SPAN = re.compile(r"`([^`\n]+)`")
_PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
                     r"/pull/([0-9]+)(?:[/?#].*)?")
_WORD_EDGE = string.punctuation + "“”‘’—–…"


class GhError(RuntimeError):
    """`gh api` exited non-zero; the message is its stderr."""


class _Refused(Exception):
    """One repo refused one card: named in `unread_repos`, next repo asked."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class _Stop(Exception):
    """The leg stops: every card not finished is `read: false` with `why`."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


# --------------------------------------------------------------------------- #
# --budget                                                                     #
# --------------------------------------------------------------------------- #

def parse_budget(text: str | None) -> int | None:
    """The one reader of `--budget`. `None`, `""` and whitespace are "no
    budget given" — the empty string is what the workflow passes on every
    ordinary morning; digits are that many requests, `0` included; anything
    else raises `ValueError`."""
    if text is None or not text.strip():
        return None
    if not _DIGITS.fullmatch(text.strip()):
        raise ValueError(f"not a whole number: {text!r}")
    return int(text.strip())


# --------------------------------------------------------------------------- #
# the gh runner                                                                #
# --------------------------------------------------------------------------- #

def _one_line(text, width: int = 400) -> str:
    return " ".join(str(text or "").split())[:width]


def _split(raw: str) -> tuple[str, str]:
    """`gh api -i` output: the status line and headers, a blank line, the body."""
    parts = re.split(r"\r?\n\r?\n", raw or "", maxsplit=1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _gh(args, *, timeout=REQUEST_TIMEOUT) -> tuple[str, str]:
    """This module's own runner: `gh api -i <args>`, a fixed argument list,
    no shell, and a timeout on every call — `groom_context._gh_json` passes
    none, and the clock is read only BETWEEN requests, so one hung call would
    run the leg into the job kill. A timeout raises
    `subprocess.TimeoutExpired`; a non-zero exit raises `GhError`."""
    done = subprocess.run(  # nosec B603 B607 — fixed-arg gh call, shell=False
        ["gh", "api", "-i", *args], capture_output=True, text=True,
        check=False, shell=False, timeout=timeout)
    if done.returncode != 0:
        raise GhError(_one_line(done.stderr)
                      or f"gh api {' '.join(args)} exited {done.returncode}")
    return _split(done.stdout)


def _headers(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines()[1:]:
        name, sep, value = line.partition(":")
        if sep:
            out[name.strip()] = value.strip()
    return out


# --------------------------------------------------------------------------- #
# one leg                                                                      #
# --------------------------------------------------------------------------- #

class _Leg:
    """The two budgets, checked before every request."""

    def __init__(self, gh_run, clock, budget: int | None, started: float):
        self.gh_run = gh_run
        self.clock = clock
        self.started = started
        self.cap = budget
        self.sized = budget is not None
        self.requests = 0

    def elapsed(self) -> float:
        return self.clock() - self.started

    def request(self, endpoint: str) -> tuple[dict, str]:
        ceiling = self.cap if self.cap is not None else MAX_REQUESTS
        if self.requests >= ceiling:
            raise _Stop(BUDGET_SPENT.format(n=ceiling))
        if self.elapsed() >= MAX_SECONDS:
            raise _Stop(TIME_SPENT.format(n=MAX_SECONDS))
        self.requests += 1
        try:
            head, body = self.gh_run([endpoint])
        except subprocess.TimeoutExpired as e:
            raise _Refused(TIMED_OUT.format(n=REQUEST_TIMEOUT)) from e
        except Exception as e:  # noqa: BLE001 — every refusal is named
            message = _one_line(e) or type(e).__name__
            if "rate limit" in message.lower():
                raise _Stop(RATE_LIMITED.format(message=message)) from e
            raise _Refused(message) from e
        headers = _headers(head)
        if not self.sized:
            # The first answered response sizes the leg, and only the first:
            # the meter falls as the leg spends, and re-reading it would
            # shrink the cap under the leg's own requests.
            self.sized = True
            remaining = dispatch_pool._int_or_none(
                dispatch_pool._header(headers, "x-ratelimit-remaining"))
            self.cap = (MAX_REQUESTS if remaining is None
                        else min(MAX_REQUESTS,
                                 math.floor(max(remaining, 0) * BUDGET_SHARE)))
        return headers, body


def _rows_to_read(rows) -> list[dict]:
    """The rows whose `lookups.looked_up` is true, Planning before spares."""
    out = [r for r in rows or []
           if isinstance(r, dict) and isinstance(r.get("lookups"), dict)
           and r["lookups"].get("looked_up") is True and r.get("card")]
    return sorted(out, key=lambda r: _LIST_ORDER.get(r.get("list"), 2))


def _repos_of(owner: str, repo_map: dict) -> list[str]:
    out: list[str] = []
    for full in (repo_map or {}).values():
        if (str(full).split("/")[0].lower() == owner.lower()
                and full not in out):
            out.append(full)
    return out


def _json_list(body: str, what: str) -> list:
    try:
        doc = json.loads(body or "null")
    except ValueError as e:
        raise _Refused(f"{what} answered unreadable JSON: {e}") from e
    if not isinstance(doc, list):
        message = doc.get("message") if isinstance(doc, dict) else None
        raise _Refused(f"{what} answered {_one_line(message) or type(doc).__name__}"
                       f", not a list")
    return doc


def _blank() -> dict:
    return {"read": False, "why": None, "merged_prs": [], "cut": [],
            "unread_repos": {}}


def _read_card(leg: _Leg, row: dict, repos: list[str], card: dict,
               now: str | None) -> None:
    paths = [str(p) for p in row["lookups"].get("paths") or []]
    since = (row.get("context") or {}).get("created_at")
    for repo in repos:
        followed: set[str] = set()
        kept: set = set()
        try:
            for path in paths:
                query = {"path": path, "since": since, "per_page": MAX_COMMITS}
                if now:
                    query["until"] = now
                headers, body = leg.request(
                    f"repos/{repo}/commits?{urlencode(query)}")
                commits = _json_list(body, f"{repo} commits for {path}")
                link = dispatch_pool._header(headers, "link") or ""
                if any(_NEXT.search(part) for part in link.split(",")):
                    card["cut"].append({"repo": repo, "path": path})
                for commit in commits:
                    sha = commit.get("sha") if isinstance(commit, dict) else None
                    if not sha or sha in followed:
                        continue
                    followed.add(sha)
                    _, body = leg.request(f"repos/{repo}/commits/{sha}/pulls")
                    for found in _json_list(body, f"{repo} pulls for {sha}"):
                        if (not isinstance(found, dict)
                                or not found.get("merged_at")
                                or found.get("number") in kept):
                            continue
                        kept.add(found.get("number"))
                        card["merged_prs"].append({
                            "repo": repo, "number": found.get("number"),
                            "title": found.get("title") or "",
                            "url": found.get("html_url") or "",
                            "merged_at": found.get("merged_at"),
                            "path": path})
        except _Refused as refused:
            card["unread_repos"][repo] = refused.why


def matched_work_of(row) -> dict:
    """The row's `matched_work`. A row written before DRE-7236 has none,
    and every reader reads it as not searched."""
    work = row.get("matched_work") if isinstance(row, dict) else None
    if isinstance(work, dict):
        return work
    return {"searched": False, "why": NOT_SEARCHED, "cards": []}


def _prs_of_row(row) -> list[dict]:
    out = []
    for card in matched_work_of(row).get("cards") or []:
        if isinstance(card, dict):
            out += [pr for pr in card.get("prs") or [] if isinstance(pr, dict)]
    return out


def _rows_with_matched_prs(rows) -> list[dict]:
    """The rows whose `matched_work.cards` holds a pull request, Planning
    before spares — whatever their `lookups.looked_up` says: a card that
    names no file is searched like any other (DRE-7236)."""
    out = [r for r in rows or []
           if isinstance(r, dict) and r.get("card") and _prs_of_row(r)]
    return sorted(out, key=lambda r: _LIST_ORDER.get(r.get("list"), 2))


def _pr_key(repo: str, number) -> str:
    return f"{repo}#{number}"


def _matched_to_read(rows, repos: list[str]) -> list[tuple[str, str, int]]:
    """`(key, repo, number)` for every matched pull request in this owner's
    repos, each once, in row order. A repo's case is the map's."""
    own = {r.lower(): r for r in repos}
    out: list[tuple[str, str, int]] = []
    for row in _rows_with_matched_prs(rows):
        for pr in _prs_of_row(row):
            repo = own.get(str(pr.get("repo") or "").lower())
            number = pr.get("number")
            if (repo is None or not isinstance(number, int)
                    or isinstance(number, bool)):
                continue
            key = _pr_key(repo, number)
            if key not in {k for k, _, _ in out}:
                out.append((key, repo, number))
    return out


def _blank_pr() -> dict:
    return {"read": False, "why": None, "merged_at": None, "title": None,
            "body": None, "files": [], "files_cut": False}


def _json_pull(body: str, what: str) -> dict:
    try:
        doc = json.loads(body or "null")
    except ValueError as e:
        raise _Refused(f"{what} answered unreadable JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("number"), int):
        message = doc.get("message") if isinstance(doc, dict) else None
        raise _Refused(f"{what} answered {_one_line(message) or type(doc).__name__}"
                       f", not a pull request")
    return doc


def _pr_body(text) -> str:
    """Fenced data first, then cut, so the cut text is the sanitized text."""
    return sanitize_untrusted.sanitize_body(str(text or ""))[:PR_BODY_LIMIT]


def _read_pr(leg: _Leg, repo: str, number: int, entry: dict) -> None:
    _, body = leg.request(f"repos/{repo}/pulls/{number}")
    pull = _json_pull(body, f"{repo} pull request #{number}")
    entry.update(merged_at=pull.get("merged_at") or None,
                 title=_clean(pull.get("title")), body=_pr_body(pull.get("body")))
    headers, body = leg.request(
        f"repos/{repo}/pulls/{number}/files?per_page={MAX_PR_FILES}")
    listed = [str(f["filename"])
              for f in _json_list(body, f"{repo} files of #{number}")
              if isinstance(f, dict) and f.get("filename")]
    link = dispatch_pool._header(headers, "link") or ""
    changed = pull.get("changed_files")
    entry["files"] = listed[:MAX_PR_FILES]
    entry["files_cut"] = bool(
        len(listed) > MAX_PR_FILES
        or any(_NEXT.search(part) for part in link.split(","))
        or (isinstance(changed, int) and changed > len(entry["files"])))


def _read_matched(leg: _Leg, doc: dict, matched) -> None:
    """Each matched pull request, after the path lookups so they spend the
    leg exactly as before: a repo that refuses is named on that pull request
    and the next is read; a stop names every one not read."""
    stopped = doc["why"]
    for key, repo, number in matched:
        entry = doc["matched_prs"][key]
        if stopped:
            entry["why"] = stopped
            continue
        try:
            _read_pr(leg, repo, number, entry)
            entry["read"] = True
        except _Refused as refused:
            entry["why"] = refused.why
        except _Stop as stop:
            stopped = doc["why"] = entry["why"] = stop.why


def owner(rows, *, owner, gh_run, repo_map, now, budget=None,
          clock=time.monotonic) -> dict:
    """The `owner` command: one owner's repos, on that owner's token in
    `GH_TOKEN`, asked about every target row it reads. Returns the per-owner
    document, exact (DRE-5458's `fold` reads it):

        {"owner": str, "read": bool, "why": str | null, "requests": int,
         "budget": int | null, "seconds": float,
         "cards": {card: {"read": bool, "why": str | null,
                          "merged_prs": [{"repo", "number", "title", "url",
                                          "merged_at", "path"}],
                          "cut": [{"repo", "path"}],
                          "unread_repos": {"owner/repo": str}}}}

    Every row the leg reads has a card. `read: true` means this owner's repos
    were asked about it, with any that refused in `unread_repos`; `read:
    false` means the leg did not finish it, and `why` says why — a card the
    leg started and did not finish keeps what it had found, and `fold` reads
    it as unfinished all the same. The owner's `why` is what stopped the leg,
    or null when nothing did. `now` bounds every commits query (`until`), so
    the window asked is the card's creation to the moment the leg ran.

    The matched pull requests (DRE-7236). Every row whose
    `matched_work.cards` holds a pull request in one of this owner's repos —
    `_rows_with_matched_prs`, a card naming no file included — has it read,
    each once, AFTER the path lookups and under the same budget, clock and
    refusal rules:

        GET /repos/{owner}/{repo}/pulls/{n}
        GET /repos/{owner}/{repo}/pulls/{n}/files?per_page=MAX_PR_FILES

    into one more key, written only when there is one to read, so a morning
    with none writes the document above byte for byte:

        "matched_prs": {"owner/repo#n": {"read": bool, "why": str | null,
                        "merged_at": str | null, "title": str | null,
                        "body": str | null, "files": [str],
                        "files_cut": bool}}

    `body` is fenced (`sanitize_untrusted`) and cut at `PR_BODY_LIMIT`;
    `files_cut` is true when GitHub holds more files than were listed. A
    pull request not read says why: the repo's refusal, or what stopped the
    leg. `fold` writes them onto every row that carries them.
    """
    started = clock()
    targets = _rows_to_read(rows)
    cards = {r["card"]: _blank() for r in targets}
    doc = {"owner": owner, "read": True, "why": None, "requests": 0,
           "budget": budget, "seconds": 0.0, "cards": cards}
    matched = _matched_to_read(rows, _repos_of(owner, repo_map))
    if matched:
        doc["matched_prs"] = {key: _blank_pr() for key, _, _ in matched}

    def not_read(why: str) -> dict:
        doc.update(read=False, why=why)
        for card in cards.values():
            card["why"] = why
        for entry in (doc.get("matched_prs") or {}).values():
            entry["why"] = why
        doc["seconds"] = round(float(clock() - started), 1)
        return doc

    if not (os.environ.get(TOKEN_ENV) or "").strip():
        return not_read(NO_TOKEN.format(owner=owner))
    repos = _repos_of(owner, repo_map)
    if not repos:
        return not_read(NO_REPOS.format(owner=owner))

    leg = _Leg(gh_run, clock, budget, started)
    try:
        for row in targets:
            card = cards[row["card"]]
            if not (row.get("context") or {}).get("created_at"):
                card["why"] = NO_CREATED_AT
                continue
            _read_card(leg, row, repos, card, now)
            card["read"] = True
    except _Stop as stop:
        doc["why"] = stop.why
        for card in cards.values():
            if not card["read"] and card["why"] is None:
                card["why"] = stop.why
    _read_matched(leg, doc, matched)
    doc["requests"] = leg.requests
    doc["budget"] = leg.cap
    doc["seconds"] = round(float(leg.elapsed()), 1)
    return doc


def summary_line(doc: dict) -> str:
    if not doc["read"]:
        return f"groom-lookups: {doc['owner']} — not read: {doc['why']}"
    n = sum(1 for card in doc["cards"].values() if card["read"])
    budget = doc["budget"] if doc["budget"] is not None else "none"
    return (f"groom-lookups: {doc['owner']} — {n} card(s) read, "
            f"{doc['requests']} request(s) of a budget of {budget}, "
            f"{doc['seconds']:.1f} s of {MAX_SECONDS} s")


# --------------------------------------------------------------------------- #
# cards — the groom job's half (DRE-5458)                                      #
# --------------------------------------------------------------------------- #

def _moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _clean(text) -> str:
    return sanitize_untrusted.sanitize_line(str(text or ""))


def _say(row: dict, line: str) -> None:
    """One evidence line, once: `fold` re-run over its own output, or the
    paths-left-out line both halves name, is never written twice."""
    evidence = row.setdefault("evidence", [])
    if line not in evidence:
        evidence.append(line)


def _lookups(*, looked_up: bool, ok, paths=(), left_out: int = 0) -> dict:
    return {"looked_up": looked_up, "ok": ok, "why": None,
            "paths": list(paths), "paths_left_out": left_out,
            "newer_cards": [], "newer_cards_why": None, "merged_prs": [],
            "cut": [], "owners": {}}


def paths_of(row) -> tuple[list[str], int]:
    """The paths the row's body names in backticks — `groomer._FILE_REF`, the
    full path as written, each once — the first `MAX_PATHS` of them, and how
    many were left out."""
    found: list[str] = []
    for path in groomer._FILE_REF.findall((row or {}).get("body") or ""):
        if path not in found:
            found.append(path)
    return found[:MAX_PATHS], max(0, len(found) - MAX_PATHS)


def _search_query(n: int, prefix: str = "p") -> str:
    """One document, one aliased `searchIssues` field per path (`p<i>`) or
    per problem term (`t<i>`); each term a variable, so a path is never
    spliced into the query text."""
    params = ", ".join(f"${prefix}{i}: String!" for i in range(n))
    fields = "\n".join(
        f"  {prefix}{i}: searchIssues(term: ${prefix}{i}, first: {SEARCH_FIRST}) "
        "{ nodes { identifier title createdAt state { name } } }"
        for i in range(n))
    return f"query({params}) {{\n{fields}\n}}"


# --------------------------------------------------------------------------- #
# matched work — the problem search (DRE-7236)                                 #
# --------------------------------------------------------------------------- #

def _spans(text) -> list[str]:
    """The backticked spans that hold no whitespace, run `SPAN_MIN` to
    `SPAN_MAX` characters, and are not a path `_FILE_REF` already reads."""
    return [span for span in _SPAN.findall(text or "")
            if SPAN_MIN <= len(span) <= SPAN_MAX
            and not any(c.isspace() for c in span)
            and not groomer._FILE_REF.fullmatch(f"`{span}`")]


def _is_word(word: str) -> bool:
    return (len(word) >= PHRASE_WORD_MIN and word.isalpha()
            and word.lower() not in STOPWORDS)


def _phrases(title) -> list[str]:
    """Every two words adjacent in the title, each a word of at least
    `PHRASE_WORD_MIN` letters and neither a stopword, lowercased, in title
    order. Punctuation at a word's edge is not part of it; a token with any
    inside (`fail-back`, `DRE-5400's`) is not a word."""
    words = [token.strip(_WORD_EDGE) for token in str(title or "").split()]
    return [f"{a.lower()} {b.lower()}" for a, b in zip(words, words[1:])
            if _is_word(a) and _is_word(b)]


def problem_terms(row) -> list[str]:
    """What a card's problem is searched by, at most `MAX_TERMS`, each once,
    in this order: the card's identifier; its paths (`paths_of`); at most
    `MAX_SPANS` backticked spans from the title, then the body; at most
    `MAX_PHRASES` two-word phrases from the title."""
    row = row if isinstance(row, dict) else {}
    terms: list[str] = []

    def add(found, cap=None) -> None:
        added = 0
        for term in found:
            if cap is not None and added >= cap:
                return
            if term and term not in terms:
                terms.append(term)
                added += 1

    add([str(row.get("card") or "").strip()])
    add(paths_of(row)[0])
    add(_spans(row.get("title")) + _spans(row.get("body")), MAX_SPANS)
    add(_phrases(row.get("title")), MAX_PHRASES)
    return terms[:MAX_TERMS]


def _holds_key(lops) -> bool:
    """Does this `linear_ops` hold a Linear key? Read through its own
    `api_key`, which raises when there is none; a reader with no `api_key`
    to ask holds none either. Never printed."""
    reader = getattr(lops, "api_key", None)
    if not callable(reader):
        return False
    try:
        return bool(reader())
    except Exception:  # noqa: BLE001 — no key is a reason, not a failure
        return False


class _Held(Exception):
    """The door says Linear's bucket is held: skip, never fall back."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _door_search(terms: list[str]) -> dict | None:
    """Every term's nodes from the read door, or None when the door is off
    or could not answer them all — then Linear answers every term, so one
    card's answer is never half the door's."""
    if not bureau_read.enabled():
        return None
    found = {}
    for term in terms:
        try:
            found[term] = bureau_read.search(term, first=SEARCH_FIRST,
                                             scope="fleet").nodes
        except bureau_read.ReadUnknown as unknown:
            if unknown.skip:
                bureau_read.note_skip("the groom's problem search", unknown.reason)
                raise _Held(unknown.reason) from unknown
            return None
    return found


def _linear_search(lops, terms: list[str]) -> dict:
    found = lops.gql(_search_query(len(terms), prefix="t"),
                     {f"t{i}": t for i, t in enumerate(terms)}) or {}
    return {t: (found.get(f"t{i}") or {}).get("nodes") or []
            for i, t in enumerate(terms)}


def _candidates(row: dict, hits: dict) -> list[tuple[str, list[str], str]]:
    """`(identifier, terms it matched, title)` for each Done card a search
    found, never the card itself, the most terms first, at most
    `MAX_CANDIDATES`. The match request re-reads every one."""
    found: dict[str, tuple[list[str], str]] = {}
    for term, nodes in hits.items():
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            ident = node.get("identifier")
            state = node.get("state")
            if (not isinstance(ident, str) or not ident
                    or ident == row.get("card")
                    or not isinstance(state, dict) or state.get("name") != DONE):
                continue
            matched, _ = found.setdefault(ident, ([], str(node.get("title") or "")))
            if term not in matched:
                matched.append(term)
    ranked = sorted(found.items(), key=lambda kv: -len(kv[1][0]))
    return [(ident, matched, title)
            for ident, (matched, title) in ranked[:MAX_CANDIDATES]]


def _ancestry(levels: int) -> str:
    return ("parent { identifier " * levels).rstrip() + " }" * levels


def _match_query(n: int) -> str:
    """One document, one aliased `issue` per candidate: its lane, when it
    was completed, its attachments' urls and `MAX_ANCESTRY` levels of
    parents. Each identifier a variable."""
    params = ", ".join(f"$c{i}: String!" for i in range(n))
    fields = "\n".join(
        f"  c{i}: issue(id: $c{i}) {{ identifier title state {{ name }} "
        f"completedAt attachments {{ nodes {{ url }} }} "
        f"{_ancestry(MAX_ANCESTRY)} }}"
        for i in range(n))
    return f"query({params}) {{\n{fields}\n}}"


def _ancestors(node: dict) -> list[str]:
    out: list[str] = []
    parent = node.get("parent")
    while isinstance(parent, dict) and len(out) < MAX_ANCESTRY:
        ident = parent.get("identifier")
        if not isinstance(ident, str) or not ident:
            break
        out.append(_clean(ident))
        parent = parent.get("parent")
    return out


def _unread_pr(repo: str, number: int) -> dict:
    return {"repo": repo, "number": number,
            "url": f"https://github.com/{repo}/pull/{number}",
            "read": False, "why": NOT_LOOKED_UP, "merged_at": None,
            "title": None, "body": None, "files": [], "files_cut": False}


def _prs_on(node: dict) -> list[dict]:
    """The GitHub pull request links on the card's attachments, each once,
    at most `MAX_MATCHED_PRS`."""
    out: list[dict] = []
    seen: set = set()
    attachments = node.get("attachments")
    for item in ((attachments or {}).get("nodes") or []
                 if isinstance(attachments, dict) else []):
        url = item.get("url") if isinstance(item, dict) else None
        found = _PR_URL.fullmatch(url.strip()) if isinstance(url, str) else None
        if not found:
            continue
        repo, number = f"{found[1]}/{found[2]}", int(found[3])
        if (repo.lower(), number) in seen:
            continue
        seen.add((repo.lower(), number))
        out.append(_unread_pr(repo, number))
        if len(out) >= MAX_MATCHED_PRS:
            break
    return out


def _matched(row: dict, ranked, found: dict, since: datetime) -> list[dict]:
    """The Done cards completed after the card was created, ranked by terms
    matched and then newest completion, at most `MAX_MATCHED_CARDS`."""
    out = []
    for i, (ident, terms, title) in enumerate(ranked):
        node = found.get(f"c{i}")
        if not isinstance(node, dict):
            continue
        ident = node.get("identifier") or ident
        state = node.get("state")
        done_at = _moment(node.get("completedAt"))
        if (ident == row.get("card") or not isinstance(state, dict)
                or state.get("name") != DONE or done_at is None
                or done_at <= since):
            continue
        ancestors = _ancestors(node)
        out.append((done_at, {
            "identifier": _clean(ident),
            "title": _clean(node.get("title") or title),
            "state": DONE, "completed_at": _clean(node.get("completedAt")),
            "parent": ancestors[0] if ancestors else None,
            "ancestors": ancestors,
            "ancestors_cut": len(ancestors) >= MAX_ANCESTRY,
            "terms": list(terms), "prs": _prs_on(node)}))
    out.sort(key=lambda pair: pair[0], reverse=True)
    out.sort(key=lambda pair: -len(pair[1]["terms"]))
    return [card for _, card in out[:MAX_MATCHED_CARDS]]


def _unsearched(why: str, terms=()) -> dict:
    return {"searched": False, "source": None, "why": why,
            "terms": list(terms), "cards": []}


def _matched_work(row: dict, lops, holds_key: bool) -> dict:
    """The row's `matched_work` (the contract in `cards`). Whatever goes
    wrong is `searched: false` with the reason, never a failed lookup."""
    if row.get("excluded"):
        return _unsearched(EXCLUDED_NOT_SEARCHED)
    terms: list[str] = []
    try:
        terms = problem_terms(row)
        if not holds_key:
            return _unsearched(NO_LINEAR_KEY, terms)
        since = _moment((row.get("context") or {}).get("created_at"))
        if since is None:
            return _unsearched(NO_CREATED_AT, terms)
        try:
            hits, source = _door_search(terms), "door"
        except _Held as held:
            return _unsearched(DOOR_HELD.format(reason=held.reason), terms)
        if hits is None:
            hits, source = _linear_search(lops, terms), "linear"
        work = {"searched": True, "source": source, "why": None,
                "terms": terms, "cards": []}
        ranked = _candidates(row, hits)
        if ranked:
            found = lops.gql(_match_query(len(ranked)),
                             {f"c{i}": c[0] for i, c in enumerate(ranked)}) or {}
            work["cards"] = _matched(row, ranked, found, since)
        return work
    except Exception as e:  # noqa: BLE001 — a failed search is said, not fatal
        return _unsearched(_clean(_one_line(e)) or type(e).__name__, terms)


def cards(rows, *, lops, now) -> list[dict]:
    """Write each target row's `lookups` block, in place, and return the rows.

    An excluded row carries `lookups: null`. A row whose body names no file
    is asked nothing: `looked_up: false`, `ok: true`. Every other row —
    an unmapped one included, since the legs span every repo in the map — is
    looked up: ONE `lops.gql` request for the newer cards naming its paths,
    with `ok: null` until `fold` decides it. A request Linear refuses is named
    in `newer_cards_why` and the next card is asked; the card is not failed
    for it. Linear's cost: one request per file-naming, non-excluded row.

    Every row also gets `matched_work` (DRE-7236) — written FIRST, so a row
    that names no file is searched like any other, and never touching a
    byte of `lookups`:

        {"searched": bool, "source": "door" | "linear" | null,
         "why": str | null, "terms": [str],
         "cards": [{"identifier", "title", "state": "Done", "completed_at",
                    "parent": str | null, "ancestors": [str],
                    "ancestors_cut": bool, "terms": [str],
                    "prs": [{"repo", "number", "url", "read": false,
                             "why": "not looked up", "merged_at": null,
                             "title": null, "body": null, "files": [],
                             "files_cut": false}]}]}

    The card's `problem_terms` are asked of the read door's search first
    (`bureau_read.search`, `scope=fleet`); when the door is off, has no
    token, or cannot answer every term, Linear's `searchIssues` answers them
    all in ONE aliased document — `source` says which. The Done cards found
    (`MAX_CANDIDATES` of them) are re-read in ONE more Linear request for
    their lane, completion, attachments and `MAX_ANCESTRY` levels of
    parents; those completed after the card was created, never the card
    itself, are kept, ranked by terms matched and then newest completion, at
    most `MAX_MATCHED_CARDS`, each with its parent chain (`ancestors_cut`
    when the fourth level was reached) and the GitHub pull requests on its
    attachments (`MAX_MATCHED_PRS`), for the `owner` legs to read.

    `searched: false` says why nothing was asked, or why the search failed:
    an excluded row, no Linear key (`lops.api_key` — none, nothing is asked,
    the door included), no creation date, a door saying Linear's bucket is
    held (`linear-hold`: the phase is skipped, never sent to Linear), or the
    request's own error. Linear's cost: at most two more requests per
    non-excluded row."""
    limit = _moment(now)
    holds_key = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if not row.get("excluded") and holds_key is None:
            holds_key = _holds_key(lops)
        row["matched_work"] = _matched_work(row, lops, bool(holds_key))
        if row.get("excluded"):
            row["lookups"] = None
            continue
        paths, left_out = paths_of(row)
        if not paths:
            row["lookups"] = _lookups(looked_up=False, ok=True)
            _say(row, NO_FILE_LINE)
            continue
        look = row["lookups"] = _lookups(looked_up=True, ok=None,
                                         paths=paths, left_out=left_out)
        if left_out:
            _say(row, LEFT_OUT_LINE.format(n=left_out))
        since = _moment((row.get("context") or {}).get("created_at"))
        if since is None:
            look["newer_cards_why"] = NO_CREATED_AT
            continue
        try:
            found = lops.gql(_search_query(len(paths)),
                             {f"p{i}": p for i, p in enumerate(paths)}) or {}
        except Exception as e:  # noqa: BLE001 — a refused read is named, not fatal
            look["newer_cards_why"] = _clean(_one_line(e)) or type(e).__name__
            continue
        seen: set[str] = set()
        for i, path in enumerate(paths):
            for node in ((found.get(f"p{i}") or {}).get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                ident = node.get("identifier")
                at = _moment(node.get("createdAt"))
                if (not ident or ident == row.get("card") or ident in seen
                        or at is None or at <= since
                        or (limit is not None and at > limit)):
                    continue
                seen.add(ident)
                newer = {"identifier": _clean(ident),
                         "title": _clean(node.get("title")),
                         "state": _clean((node.get("state") or {}).get("name")),
                         "path": path}
                look["newer_cards"].append(newer)
                _say(row, NEWER_CARD_LINE.format(**newer))
    return rows


# --------------------------------------------------------------------------- #
# fold — the verify leg's half (DRE-5458)                                      #
# --------------------------------------------------------------------------- #

def _owners_of(repo_map: dict) -> list[str]:
    """Every owner in the map, each once, in the map's order."""
    out: list[str] = []
    for full in (repo_map or {}).values():
        owner = str(full).split("/")[0]
        if owner and owner.lower() not in {o.lower() for o in out}:
            out.append(owner)
    return out


def _entry(doc, card) -> dict | None:
    entry = ((doc or {}).get("cards") or {}) if isinstance(doc, dict) else {}
    found = entry.get(card) if isinstance(entry, dict) else None
    return found if isinstance(found, dict) else None


def _unread(entry) -> dict:
    found = (entry or {}).get("unread_repos")
    return found if isinstance(found, dict) else {}


def _why_of(owner: str, doc, entry) -> str:
    """Why this owner did not read the card, in order of what is known: the
    card's own `why`, the owner's, or no document at all."""
    for why in ((entry or {}).get("why"), (doc or {}).get("why")):
        if why:
            return _one_line(why)
    return NO_RECORD.format(owner=owner)


def _fold_row(row: dict, look: dict, owners: list[str], by_owner: dict,
              repo_map: dict) -> None:
    card = row.get("card")
    look["owners"] = {}
    merged = look.setdefault("merged_prs", [])
    cut = look.setdefault("cut", [])
    for owner in owners:
        doc = by_owner.get(owner.lower())
        entry = _entry(doc, card)
        read = bool(entry and entry.get("read") is True)
        why = None if read else _why_of(owner, doc, entry)
        look["owners"][owner] = {"read": read, "why": why}
        # A card the leg started and did not finish keeps what it found.
        for pr in (entry or {}).get("merged_prs") or []:
            if isinstance(pr, dict) and pr not in merged:
                merged.append(pr)
                _say(row, MERGED_LINE.format(
                    number=pr.get("number"), repo=pr.get("repo"),
                    path=pr.get("path"), day=str(pr.get("merged_at") or "")[:10],
                    title=_clean(pr.get("title"))))
        for item in (entry or {}).get("cut") or []:
            if isinstance(item, dict) and item not in cut:
                cut.append(item)
                _say(row, CUT_LINE.format(n=MAX_COMMITS, path=item.get("path"),
                                          repo=item.get("repo")))
        if not read:
            _say(row, NOT_COVERED_LINE.format(what=owner, why=why))
        for repo, repo_why in _unread(entry).items():
            if read:
                _say(row, NOT_COVERED_LINE.format(what=repo,
                                                  why=_one_line(repo_why)))
    if look.get("paths_left_out"):
        _say(row, LEFT_OUT_LINE.format(n=look["paths_left_out"]))

    home = row.get("repository")
    if home:
        # Mapped: the card's own repo answered, or the card failed.
        owner = str(home).split("/")[0]
        doc = by_owner.get(owner.lower())
        entry = _entry(doc, card)
        read = bool(entry and entry.get("read") is True)
        if read and home not in _unread(entry):
            look["ok"], look["why"] = True, None
            return
        reason = (_one_line(_unread(entry)[home]) if read
                  else _why_of(owner, doc, entry))
        look["ok"] = False
        look["why"] = DID_NOT_ANSWER.format(repo=home, why=reason)
        return
    # Unmapped: no home repo, so any repo in the map answering is an answer.
    whys: list[str] = []
    for owner in owners:
        entry = _entry(by_owner.get(owner.lower()), card)
        if not look["owners"][owner]["read"]:
            whys.append(f"{owner}: {look['owners'][owner]['why']}")
            continue
        unread = _unread(entry)
        if any(repo not in unread for repo in _repos_of(owner, repo_map)):
            look["ok"], look["why"] = True, None
            return
        whys += [f"{repo}: {_one_line(why)}" for repo, why in unread.items()]
    look["ok"] = False
    look["why"] = NO_REPO_ANSWERED.format(whys="; ".join(whys))


def _fold_matched(row: dict, by_owner: dict) -> None:
    """Each matched pull request on the row, as its owner's leg read it.
    One no leg read says why: that owner's reason, no document from it, or
    a repo outside the map, which no leg is ever asked about. The leg
    already fenced the title and body, so they are written as read — a
    second fence would prefix each defanged line twice."""
    for pr in _prs_of_row(row):
        repo = str(pr.get("repo") or "")
        owner = repo.split("/")[0]
        doc = by_owner.get(owner.lower())
        read = ((doc or {}).get("matched_prs")
                if isinstance(doc, dict) else None)
        index = ({str(k).lower(): v for k, v in read.items()}
                 if isinstance(read, dict) else {})
        entry = index.get(_pr_key(repo, pr.get("number")).lower())
        if isinstance(entry, dict):
            files = [str(f) for f in entry.get("files") or []
                     if isinstance(f, str)][:MAX_PR_FILES]
            pr.update(read=entry.get("read") is True,
                      why=_one_line(entry["why"]) if entry.get("why") else None,
                      merged_at=entry.get("merged_at") or None,
                      title=(str(entry["title"])
                             if entry.get("title") is not None else None),
                      body=(str(entry["body"])[:PR_BODY_LIMIT]
                            if entry.get("body") is not None else None),
                      files=files, files_cut=bool(entry.get("files_cut")))
            continue
        if doc is None or doc.get("read") is not True:
            why = _why_of(owner, doc, None)
        else:
            why = PR_NOT_IN_MAP.format(repo=repo)
        pr.update(read=False, why=why)


def fold(rows, docs: list[dict], *, repo_map) -> list[dict]:
    """Fold every leg's document into the rows, in place, and return them.

    Each looked-up row gets `owners[<owner>]` for every owner in the map, the
    merged pull requests and cuts every leg found with one evidence line each,
    one `the lookup did not cover …` line per owner or repo not read, and its
    `ok` and `why` — decided by its own repo when it is mapped, by any repo
    when it is not. A row that names no file, or whose `lookups` is null, is
    left as it was. The owner is the document's own `owner` key.

    Every row whose `matched_work` holds a pull request — a row naming no
    file included — then has each one written as its owner's leg read it
    (DRE-7236), and nothing else of the row changes for it."""
    by_owner: dict[str, dict] = {}
    for doc in docs or []:
        if isinstance(doc, dict) and isinstance(doc.get("owner"), str):
            by_owner.setdefault(doc["owner"].lower(), doc)
    owners = _owners_of(repo_map)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        look = row.get("lookups")
        if isinstance(look, dict) and look.get("looked_up") is True:
            _fold_row(row, look, owners, by_owner, repo_map)
    for row in _rows_with_matched_prs(rows):
        _fold_matched(row, by_owner)
    return rows


def _documents(directory) -> list[dict]:
    """Every `lookups-*.json` at ANY depth under the directory — the workflow
    downloads each leg's artifact into its own subdirectory. A file that does
    not parse is skipped and said; a directory that does not exist is no
    documents at all."""
    base = Path(directory)
    docs: list[dict] = []
    if not base.is_dir():
        return docs
    for path in sorted(base.rglob(LOOKUPS_GLOB)):
        if not path.is_file():
            continue
        try:
            doc = _load(path)
        except (OSError, ValueError) as e:
            print(f"groom-lookups: skipped {path}: not readable JSON: "
                  f"{_one_line(e)}", file=sys.stderr)
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("owner"), str) \
                or not doc["owner"].strip():
            print(f"groom-lookups: skipped {path}: no owner key",
                  file=sys.stderr)
            continue
        docs.append(doc)
    return docs


# --------------------------------------------------------------------------- #
# the command line                                                             #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("owner", help="one owner's lookups, one document")
    p.add_argument("--owner", required=True)
    p.add_argument("--targets", required=True)
    p.add_argument("--out", required=True)
    # TEXT, never an int: the workflow passes `--budget "$LOOKUP_BUDGET"` on
    # every run, and on an ordinary morning that is the empty string.
    p.add_argument("--budget", default=None)
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))

    p = sub.add_parser("fold", help="every leg's document, folded into the rows")
    p.add_argument("--targets", required=True)
    p.add_argument("--lookups-dir", dest="lookups_dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))
    return parser


def _load(path) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fold_main(args) -> int:
    try:
        rows = _load(args.targets)
        repo_map = _load(args.repo_map)
        if not isinstance(rows, list) or not isinstance(repo_map, dict):
            raise ValueError("the targets file must be a list and the repo "
                             "map an object")
    except (OSError, ValueError) as e:
        print(f"groom-lookups: fold could not read its inputs: {_one_line(e)}",
              file=sys.stderr)
        return 2
    # No documents is still a fold: every owner unread, every looked-up card
    # failed, and the agent still runs.
    docs = _documents(args.lookups_dir)
    fold(rows, docs, repo_map=repo_map)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    looks = [r["lookups"] for r in rows
             if isinstance(r, dict) and isinstance(r.get("lookups"), dict)]
    ok = sum(1 for look in looks if look.get("looked_up") and look.get("ok"))
    failed = sum(1 for look in looks
                 if look.get("looked_up") and look.get("ok") is False)
    none = sum(1 for look in looks if not look.get("looked_up"))
    print(f"groom-lookups: folded {len(docs)} document(s) — {ok} card(s) ok, "
          f"{failed} failed, {none} named no file")
    return 0


def main(argv=None, *, gh_run=None, clock=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fold":
        return _fold_main(args)
    try:
        budget = parse_budget(args.budget)
    except ValueError:
        print(USAGE_BUDGET.format(text=args.budget), file=sys.stderr)
        return 2

    try:
        rows = _load(args.targets)
        repo_map = _load(args.repo_map)
        if not isinstance(rows, list) or not isinstance(repo_map, dict):
            raise ValueError("the targets file must be a list and the repo "
                             "map an object")
    except (OSError, ValueError) as e:
        # Still a document: the leg's absence must read as not read, never
        # as a missing file nobody looked for.
        doc = {"owner": args.owner, "read": False,
               "why": f"the leg's inputs could not be read: {_one_line(e)}",
               "requests": 0, "budget": budget, "seconds": 0.0, "cards": {}}
    else:
        doc = owner(rows, owner=args.owner, gh_run=gh_run or _gh,
                    repo_map=repo_map, now=_now(), budget=budget,
                    clock=clock or time.monotonic)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    if doc["read"] and doc["why"]:
        print(f"groom-lookups: {args.owner} stopped: {doc['why']}",
              file=sys.stderr)
    print(summary_line(doc))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
