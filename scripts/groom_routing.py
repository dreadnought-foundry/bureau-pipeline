#!/usr/bin/env python3
"""The groom run's routing lookups — where each repo's jobs ran (DRE-7237).

Two cards on the 2026-10-10 list were about problems that could not happen any
more: DRE-4417, a build SIGKILLed on the Mac mini's light tier, and DRE-3701, a
stale verdict file in a self-hosted runner's `/tmp`. Those jobs run on RunsOn
now, and the verify agent could not know it, because where jobs run is set per
repo outside the code. This module reads where jobs ACTUALLY RAN recently, off
GitHub's own record of each job, and writes it onto every targets row as
`routing`, so the agent can see whether a problem's trigger still exists.
Nothing here reads a repository setting: the facts are what GitHub says the
jobs ran on.

## What one owner's leg does

The `owner` command runs once per owner, beside the lookup leg, on that
owner's App installation token (`actions: read`). For each of the owner's
repos in `config/repo-map.json`:

    GET /repos/{o}/{r}/actions/runs?status=completed&per_page=100

following the `next` link for at most `ROUTING_RUN_PAGES` pages — the newest
300 completed runs. From those it keeps the newest `ROUTING_RUNS_PER_WORKFLOW`
runs of each distinct workflow `path`, and for each kept run:

    GET /repos/{o}/{r}/actions/runs/{id}/jobs?per_page=100

**Per workflow, never across them.** A window of the newest runs across every
workflow is filled by the sweep and Linear Sync, which run every few minutes,
and leaves out the build and review jobs the cards were about. The newest run
of every workflow, in every repo, is read before any workflow's second run, so
a budget cut drops second runs first. A workflow that last completed a run
before the newest 300 has no fact, and the fact list says nothing about it.

The jobs are collapsed into one fact per `(workflow path, job name)`: how many
jobs ran, the distinct runner labels and runner group names, and the first and
last completion times. A job that never got a runner (`runner_name` empty — a
skipped job, or one canceled while queued) ran nowhere and is not counted. A
repo keeps at most `ROUTING_FACTS` facts, the most recently completed.

## What stops it, and what it says when it does

Every cut is named in the document's `why`, never silent:

  * **No token** — `read: false`, no request is made.
  * **A page limit** — a fourth page of runs exists; workflows older than the
    newest 300 runs have no fact.
  * **A repo's facts past `ROUTING_FACTS`**, and a run with more jobs than one
    page.
  * **A repo that refuses** (any `gh` failure but a rate limit, or a request
    reaching `REQUEST_TIMEOUT`) — named, asked nothing more, and the next repo
    is asked. What it answered before refusing is kept.
  * **A rate limit, the request budget (`MAX_REQUESTS`) or the clock
    (`MAX_SECONDS`)** — the leg stops, and keeps what it read.

The document is written on every path. `read` is true when at least one repo
answered its runs query.

## In the verify leg

`fold` reads every `routing-*.json` under `--dir`, at any depth, and writes the
same `routing` block onto every targets row — the one key it writes:

    {"read": bool, "why": str | null, "read_at": str | null,
     "facts": [{"id", "repo", "workflow", "job", "jobs", "labels",
                "runner_groups", "first_completed_at", "last_completed_at"}]}

A fact's `id` is `<owner>/<repo>:<workflow path>:<job name>`, exactly. `read`
is true when at least one owner answered; `why` names each owner that did not,
and every cut an owner named. A row with no `routing` key, or `read: false`,
has no routing facts, and every reader says so rather than guessing.

CLI:

    python3 scripts/groom_routing.py owner --owner <owner> \\
        --out routing-<owner>.json [--repo-map <file>]
    python3 scripts/groom_routing.py fold --targets <file> --dir <dir> \\
        --out <file> [--repo-map <file>]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess  # nosec B404 — only to name `TimeoutExpired`; `gh` runs in groom_lookups
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch_pool  # noqa: E402 — the one case-insensitive header reader
import groom_lookups  # noqa: E402 — the `gh api -i` runner and the map readers
import sanitize_untrusted  # noqa: E402 — job names reach the agent as data

ROOT = Path(__file__).resolve().parent.parent
REPO_MAP = ROOT / "config" / "repo-map.json"

# --------------------------------------------------------------------------- #
# the contract constants                                                       #
# --------------------------------------------------------------------------- #

#: The most pages of completed runs one repo is asked for, `per_page` 100 each.
ROUTING_RUN_PAGES = 3

#: The newest runs of each distinct workflow path whose jobs are read.
ROUTING_RUNS_PER_WORKFLOW = 2

#: The most facts one repo keeps, the most recently completed.
ROUTING_FACTS = 40

#: The most requests one leg makes. Three pages and two runs per workflow for
#: four repos of about 25 active workflows is about 210, so the largest owner
#: can be cut — on second runs, and said.
MAX_REQUESTS = 200

#: The leg's wall clock, in seconds, read before every request.
MAX_SECONDS = 120

#: The `subprocess.run` timeout on every `gh api` call.
REQUEST_TIMEOUT = 20

#: `per_page` on both queries — GitHub's largest.
PER_PAGE = 100

#: The credential the leg spends: this owner's App installation token.
TOKEN_ENV = "GH_TOKEN"

#: The `fold` command's input files, found at any depth under --dir.
ROUTING_GLOB = "routing-*.json"

API_ROOT = "https://api.github.com/"

# The `why` values, exact.
NO_TOKEN = "no token for {owner}: the App's installation could not be minted"
NO_REPOS = "no repo of {owner} in the repo map"
BUDGET_SPENT = "request budget of {n} spent"
TIME_SPENT = "time budget of {n} s spent"
RATE_LIMITED = "rate limited: {message}"
TIMED_OUT = "timed out after {n} s"
RUNS_NOT_READ = "{n} kept run(s) were not read"
REFUSED = "{repo} did not answer — {why}"
PAGE_LIMIT = ("{repo}: more than {pages} pages of completed runs; only the "
              "newest {runs} were read, so a workflow that last ran before "
              "them has no fact")
JOBS_CUT = ("{repo}: run {id} has more than {n} jobs; only the first {n} were "
            "read")
FACTS_CUT = ("{repo}: {n} fact(s) past {limit} were left out, the least "
             "recently completed")
INPUTS_UNREAD = "the leg's inputs could not be read: {why}"
NO_RECORD = "no routing record"
NOT_READ = "not read"

_NEXT_URL = re.compile(r'^\s*<([^>]*)>\s*;.*\brel="?next"?', re.IGNORECASE)

GhError = groom_lookups.GhError
_one_line = groom_lookups._one_line


def _gh(args) -> tuple[str, str]:
    """`groom_lookups._gh` — `gh api -i`, a fixed argument list, no shell —
    under this leg's own `REQUEST_TIMEOUT`."""
    return groom_lookups._gh(args, timeout=REQUEST_TIMEOUT)


def _clean(text) -> str:
    return sanitize_untrusted.sanitize_line(str(text or ""))


class _Refused(Exception):
    """One repo refused: named, asked nothing more, next repo asked."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class _Stop(Exception):
    """The leg stops, keeping what it read; `why` says what stopped it."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


# --------------------------------------------------------------------------- #
# one leg                                                                      #
# --------------------------------------------------------------------------- #

class _Leg:
    """The two budgets, checked before every request."""

    def __init__(self, gh_run, clock, started: float):
        self.gh_run = gh_run
        self.clock = clock
        self.started = started
        self.requests = 0

    def elapsed(self) -> float:
        return self.clock() - self.started

    def request(self, endpoint: str) -> tuple[dict, str]:
        if self.requests >= MAX_REQUESTS:
            raise _Stop(BUDGET_SPENT.format(n=MAX_REQUESTS))
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
        return groom_lookups._headers(head), body


def _next_endpoint(headers: dict) -> str | None:
    """The `rel="next"` URL of a `Link` header, as a `gh api` endpoint."""
    link = dispatch_pool._header(headers, "link") or ""
    for part in link.split(","):
        found = _NEXT_URL.match(part)
        if found:
            url = found.group(1).strip()
            return url[len(API_ROOT):] if url.startswith(API_ROOT) else url
    return None


def _list_in(body: str, key: str, what: str) -> list:
    try:
        doc = json.loads(body or "null")
    except ValueError as e:
        raise _Refused(f"{what} answered unreadable JSON: {e}") from e
    found = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(found, list):
        message = doc.get("message") if isinstance(doc, dict) else None
        raise _Refused(f"{what} answered {_one_line(message) or 'no ' + key}")
    return [item for item in found if isinstance(item, dict)]


class _Repo:
    """One repo's reading: its kept runs, its jobs, and what it said."""

    def __init__(self, full: str):
        self.full = full
        self.answered = False
        self.refused: str | None = None
        self.notes: list[str] = []
        # The kept runs, by rank: ranks[0] is the newest run of every
        # workflow, newest workflow first; ranks[1] every second run.
        self.ranks: list[list[dict]] = [[] for _ in range(ROUTING_RUNS_PER_WORKFLOW)]
        self.jobs: list[tuple[str, dict]] = []
        self.runs_read = 0


def _read_runs(leg: _Leg, repo: _Repo) -> None:
    endpoint = (f"repos/{repo.full}/actions/runs?"
                f"{urlencode({'status': 'completed', 'per_page': PER_PAGE})}")
    by_path: dict[str, list[dict]] = {}
    seen: set = set()
    for page in range(1, ROUTING_RUN_PAGES + 1):
        headers, body = leg.request(endpoint)
        runs = _list_in(body, "workflow_runs", f"{repo.full} runs")
        repo.answered = True
        for run in runs:
            path, run_id = run.get("path"), run.get("id")
            if not path or run_id is None or run_id in seen:
                continue
            seen.add(run_id)
            kept = by_path.setdefault(str(path), [])
            if len(kept) < ROUTING_RUNS_PER_WORKFLOW:
                kept.append(run)
        endpoint = _next_endpoint(headers)
        if endpoint is None:
            break
        if page == ROUTING_RUN_PAGES:
            repo.notes.append(PAGE_LIMIT.format(
                repo=repo.full, pages=ROUTING_RUN_PAGES,
                runs=ROUTING_RUN_PAGES * PER_PAGE))
    for kept in by_path.values():
        for rank, run in enumerate(kept):
            repo.ranks[rank].append(run)


def _read_jobs(leg: _Leg, repo: _Repo, run: dict) -> None:
    run_id = run["id"]
    headers, body = leg.request(
        f"repos/{repo.full}/actions/runs/{run_id}/jobs?"
        f"{urlencode({'per_page': PER_PAGE})}")
    jobs = _list_in(body, "jobs", f"{repo.full} jobs of run {run_id}")
    repo.runs_read += 1
    if _next_endpoint(headers) is not None:
        repo.notes.append(JOBS_CUT.format(repo=repo.full, id=run_id, n=PER_PAGE))
    for found in jobs:
        if found.get("runner_name"):
            repo.jobs.append((str(run["path"]), found))


def _facts_of(repo: _Repo) -> list[dict]:
    """One fact per `(workflow path, job name)`, the newest completed first,
    at most `ROUTING_FACTS` of them — a cut is named in the repo's notes."""
    by_key: dict[tuple[str, str], dict] = {}
    for path, found in repo.jobs:
        workflow, name = _clean(path), _clean(found.get("name"))
        fact = by_key.get((workflow, name))
        if fact is None:
            fact = by_key[(workflow, name)] = {
                "id": f"{repo.full}:{workflow}:{name}", "repo": repo.full,
                "workflow": workflow, "job": name, "jobs": 0,
                "labels": set(), "runner_groups": set(), "times": []}
        fact["jobs"] += 1
        fact["labels"].update(_clean(label) for label in found.get("labels") or []
                              if label)
        if found.get("runner_group_name"):
            fact["runner_groups"].add(_clean(found["runner_group_name"]))
        if found.get("completed_at"):
            fact["times"].append(str(found["completed_at"]))
    facts = []
    for fact in by_key.values():
        times = sorted(fact.pop("times"))
        fact["labels"] = sorted(fact["labels"])
        fact["runner_groups"] = sorted(fact["runner_groups"])
        fact["first_completed_at"] = times[0] if times else None
        fact["last_completed_at"] = times[-1] if times else None
        facts.append(fact)
    facts.sort(key=lambda f: f["id"])
    facts.sort(key=lambda f: f["last_completed_at"] or "", reverse=True)
    if len(facts) > ROUTING_FACTS:
        repo.notes.append(FACTS_CUT.format(repo=repo.full,
                                           n=len(facts) - ROUTING_FACTS,
                                           limit=ROUTING_FACTS))
    return facts[:ROUTING_FACTS]


def owner(*, owner, gh_run, repo_map, now, clock=time.monotonic) -> dict:
    """The `owner` command: one owner's repos, on that owner's token in
    `GH_TOKEN`. Returns the per-owner document `fold` reads:

        {"owner": str, "read": bool, "why": str | null, "read_at": str,
         "requests": int, "budget": int, "seconds": float,
         "repos": {"owner/repo": {"read": bool, "why": str | null}},
         "facts": [fact, ...]}

    `read` is true when at least one repo answered its runs query; `why`
    names every refusal and cut, and what stopped the leg, joined by `; `,
    or is null when nothing was left out. A repo's `read` says its runs were
    answered, and its `why` that it refused."""
    started = clock()
    doc = {"owner": owner, "read": False, "why": None, "read_at": now,
           "requests": 0, "budget": MAX_REQUESTS, "seconds": 0.0,
           "repos": {}, "facts": []}
    if not (os.environ.get(TOKEN_ENV) or "").strip():
        doc["why"] = NO_TOKEN.format(owner=owner)
        return doc
    repos = [_Repo(full) for full in groom_lookups._repos_of(owner, repo_map)]
    if not repos:
        doc["why"] = NO_REPOS.format(owner=owner)
        return doc

    leg = _Leg(gh_run, clock, started)
    stopped: str | None = None
    try:
        for repo in repos:
            try:
                _read_runs(leg, repo)
            except _Refused as refused:
                repo.refused = refused.why
        # The newest run of every workflow in every repo before any second.
        for rank in range(ROUTING_RUNS_PER_WORKFLOW):
            for repo in repos:
                for run in repo.ranks[rank]:
                    if repo.refused is not None:
                        break
                    try:
                        _read_jobs(leg, repo, run)
                    except _Refused as refused:
                        repo.refused = refused.why
    except _Stop as stop:
        stopped = stop.why

    notes: list[str] = []
    for repo in repos:
        if repo.refused is not None:
            notes.append(REFUSED.format(repo=repo.full, why=repo.refused))
        doc["repos"][repo.full] = {"read": repo.answered, "why": repo.refused}
        doc["facts"] += _facts_of(repo)
        notes += repo.notes
    if stopped:
        notes.append(stopped)
        unread = sum(sum(map(len, repo.ranks)) - repo.runs_read
                     for repo in repos if repo.refused is None)
        if unread:
            notes.append(RUNS_NOT_READ.format(n=unread))
    doc["read"] = any(repo.answered for repo in repos)
    doc["why"] = "; ".join(notes) or None
    doc["requests"] = leg.requests
    doc["seconds"] = round(float(leg.elapsed()), 1)
    return doc


def summary_line(doc: dict) -> str:
    if not doc["read"]:
        return f"groom-routing: {doc['owner']} — not read: {doc['why']}"
    return (f"groom-routing: {doc['owner']} — {len(doc['facts'])} fact(s) "
            f"from {sum(1 for r in doc['repos'].values() if r['read'])} "
            f"repo(s), {doc['requests']} request(s) of a budget of "
            f"{doc['budget']}, {doc['seconds']:.1f} s of {MAX_SECONDS} s")


# --------------------------------------------------------------------------- #
# fold — the verify leg's half                                                 #
# --------------------------------------------------------------------------- #

def routing(docs: list[dict], *, repo_map) -> dict:
    """The one `routing` block every row carries, from every owner's
    document. The owners are the map's, then any document's own."""
    by_owner: dict[str, dict] = {}
    for doc in docs or []:
        if isinstance(doc, dict) and isinstance(doc.get("owner"), str):
            by_owner.setdefault(doc["owner"].lower(), doc)
    owners = groom_lookups._owners_of(repo_map)
    for doc in by_owner.values():
        if doc["owner"].lower() not in {o.lower() for o in owners}:
            owners.append(doc["owner"])

    read = False
    whys: list[str] = []
    facts: list[dict] = []
    read_at: list[str] = []
    for name in owners:
        doc = by_owner.get(name.lower())
        if doc is None:
            whys.append(f"{name}: {NO_RECORD}")
            continue
        why = _one_line(doc.get("why"), width=4000) or None
        if doc.get("read") is not True:
            whys.append(f"{name}: {why or NOT_READ}")
            continue
        read = True
        if why:
            whys.append(f"{name}: {why}")
        facts += [f for f in doc.get("facts") or [] if isinstance(f, dict)]
        if isinstance(doc.get("read_at"), str):
            read_at.append(doc["read_at"])
    return {"read": read,
            "why": "; ".join(whys) or None,
            "read_at": max(read_at) if read_at else None,
            "facts": facts}


def fold(rows, docs: list[dict], *, repo_map) -> list[dict]:
    """Write the same `routing` block onto every row, in place, and return
    the rows. `routing` is the one key written; no other field changes."""
    block = routing(docs, repo_map=repo_map)
    for row in rows or []:
        if isinstance(row, dict):
            row["routing"] = copy.deepcopy(block)
    return rows


def _documents(directory) -> list[dict]:
    """Every `routing-*.json` at ANY depth under the directory — the workflow
    downloads each leg's artifact into its own subdirectory. A file that does
    not parse is skipped and said; a directory that does not exist is no
    documents at all."""
    base = Path(directory)
    docs: list[dict] = []
    if not base.is_dir():
        return docs
    for path in sorted(base.rglob(ROUTING_GLOB)):
        if not path.is_file():
            continue
        try:
            doc = _load(path)
        except (OSError, ValueError) as e:
            print(f"groom-routing: skipped {path}: not readable JSON: "
                  f"{_one_line(e)}", file=sys.stderr)
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("owner"), str) \
                or not doc["owner"].strip():
            print(f"groom-routing: skipped {path}: no owner key",
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


def _load(path) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("owner", help="one owner's job routing, one document")
    p.add_argument("--owner", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))

    p = sub.add_parser("fold", help="every owner's document, onto every row")
    p.add_argument("--targets", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))
    return parser


def _fold_main(args) -> int:
    try:
        rows = _load(args.targets)
        repo_map = _load(args.repo_map)
        if not isinstance(rows, list) or not isinstance(repo_map, dict):
            raise ValueError("the targets file must be a list and the repo "
                             "map an object")
    except (OSError, ValueError) as e:
        print(f"groom-routing: fold could not read its inputs: {_one_line(e)}",
              file=sys.stderr)
        return 2
    # No documents is still a fold: every row says routing was not read.
    docs = _documents(args.dir)
    fold(rows, docs, repo_map=repo_map)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    block = rows[0]["routing"] if rows and isinstance(rows[0], dict) else \
        routing(docs, repo_map=repo_map)
    print(f"groom-routing: folded {len(docs)} document(s) — "
          f"{'read' if block['read'] else 'not read'}, "
          f"{len(block['facts'])} fact(s)"
          + (f"; {block['why']}" if block["why"] else ""))
    return 0


def main(argv=None, *, gh_run=None, clock=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "fold":
        return _fold_main(args)
    try:
        repo_map = _load(args.repo_map)
        if not isinstance(repo_map, dict):
            raise ValueError("the repo map must be an object")
    except (OSError, ValueError) as e:
        # Still a document: the leg's absence must read as not read, never
        # as a missing file nobody looked for.
        doc = {"owner": args.owner, "read": False,
               "why": INPUTS_UNREAD.format(why=_one_line(e)), "read_at": _now(),
               "requests": 0, "budget": MAX_REQUESTS, "seconds": 0.0,
               "repos": {}, "facts": []}
    else:
        doc = owner(owner=args.owner, gh_run=gh_run or _gh, repo_map=repo_map,
                    now=_now(), clock=clock or time.monotonic)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    if doc["read"] and doc["why"]:
        print(f"groom-routing: {args.owner} left out: {doc['why']}",
              file=sys.stderr)
    print(summary_line(doc))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
