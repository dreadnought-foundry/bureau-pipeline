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
import subprocess  # nosec B404 — a fixed-arg `gh` read, argv list, never a shell
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    """
    started = clock()
    targets = _rows_to_read(rows)
    cards = {r["card"]: _blank() for r in targets}
    doc = {"owner": owner, "read": True, "why": None, "requests": 0,
           "budget": budget, "seconds": 0.0, "cards": cards}

    def not_read(why: str) -> dict:
        doc.update(read=False, why=why)
        for card in cards.values():
            card["why"] = why
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


def _search_query(n: int) -> str:
    """One document, one aliased `searchIssues` field per path; each term a
    variable, so a path is never spliced into the query text."""
    params = ", ".join(f"$p{i}: String!" for i in range(n))
    fields = "\n".join(
        f"  p{i}: searchIssues(term: $p{i}, first: {SEARCH_FIRST}) "
        "{ nodes { identifier title createdAt state { name } } }"
        for i in range(n))
    return f"query({params}) {{\n{fields}\n}}"


def cards(rows, *, lops, now) -> list[dict]:
    """Write each target row's `lookups` block, in place, and return the rows.

    An excluded row carries `lookups: null`. A row whose body names no file
    is asked nothing: `looked_up: false`, `ok: true`. Every other row —
    an unmapped one included, since the legs span every repo in the map — is
    looked up: ONE `lops.gql` request for the newer cards naming its paths,
    with `ok: null` until `fold` decides it. A request Linear refuses is named
    in `newer_cards_why` and the next card is asked; the card is not failed
    for it. Linear's cost: one request per file-naming, non-excluded row."""
    limit = _moment(now)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
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


def fold(rows, docs: list[dict], *, repo_map) -> list[dict]:
    """Fold every leg's document into the rows, in place, and return them.

    Each looked-up row gets `owners[<owner>]` for every owner in the map, the
    merged pull requests and cuts every leg found with one evidence line each,
    one `the lookup did not cover …` line per owner or repo not read, and its
    `ok` and `why` — decided by its own repo when it is mapped, by any repo
    when it is not. A row that names no file, or whose `lookups` is null, is
    left as it was. The owner is the document's own `owner` key."""
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
