#!/usr/bin/env python3
"""A release's `What's new:` lines become its `whats-new.json` asset (DRE-5513).

WHY
---
A person who uses a product learns what changed from one file per release,
`whats-new.json`, published beside the product's tag (`standards/whats-new.md`).
Every entry in it was written once, as the `What's new:` line of the pull
request that made the change. This module is the collector: it reads the lines
of the pull requests a release carries, drops the `none`s, assembles the
document, validates it with `whats_new.validate`, and publishes it as the
`whats-new.json` asset of the tag's GitHub Release. The train's after-release
hook calls `write` (DRE-5516); `publish` on the command line runs the same
`write` by hand.

WHICH PULL REQUESTS
-------------------
The same walk the Linear release uses — `release_linear.surface_filter` and
`release_linear.changes_since` — so the two never disagree about what a release
carries. Each change's subject names its pull request: `Merge pull request #n
from …` (the gate merges with `--merge`) or a trailing `(#n)` on a squash. A
change naming none is skipped by its sha as `uncarded`. Each pull request is
read once with `gh api`; its line is collected unless the branch owes none
(`exempt`), the line says `none`, there is no line (`no line`), it does not
parse, or its title fails the wording check — every one of those is named in a
printed line and none of them is fatal: a pull request merged before the gate
existed is ordinary. A pull request GitHub will not read — a 404, a 502, a
`(#n)` that names an issue — is skipped the same way, as `unreadable`, and the
release's other sentences still publish. A first release collects nothing, as
the Linear release attaches nothing.

Each collected entry records the card that delivered it as `cards` (DRE-6015):
the `DRE-<n>` its head branch names, read with `usage_reading.card_from_ref` —
the one `agent/DRE-<n>-<slug>` pattern. A branch naming no card writes no
`cards` key, never an empty guess.

THE RULE'S SWITCH IS NOT ASKED
------------------------------
Unlike the gate, this module never asks whether the What's New rule is
switched on and never reads the cutover file. A sentence is collected whenever one is present —
whether the pull request was opened before or after the switch-on, and whether
the gate was holding at all. The switch decides what the gate HOLDS, not what a
release SAYS. `whats_new.required_for` is the branch question alone.

THE VENDOR, ANSWERED (standards/vendor-boundaries.md)
-----------------------------------------------------
* The actor is the caller's `github.token`; a release it creates fires no other
  workflow.
* The asset needs `contents: write`, already in the caller stub. Reading a
  private repository's pull requests needs `pull-requests: read`, which the
  stubs grant through DRE-5579 (agent-bureau and the scaffold template) and
  DRE-5534 (portico). A forbidden answer — GitHub's `Resource not accessible
  by integration` — is one `problem` naming `caller stub lacks pull-requests:
  read`, and nothing is published; DRE-5516 turns that problem into a run
  annotation and a summary block. GitHub answers a rate limit with a 403 too,
  so a 403 alone never names the permission: a rate limit is its own
  `problem` and nothing is published, because every read after it would fail
  the same way and `publish` re-runs it by hand once the limit resets. Any
  other 403 skips that one pull request as `unreadable`.
* A re-run after the tag is idempotent: the asset is clobbered.
* A release this module creates is made with `--latest=false`: it exists to
  carry the file, and on a repository with several surfaces the last one to
  publish would otherwise take GitHub's Latest pointer.
* A crash after the tag loses only this lap's file, which `publish` re-creates
  by hand.

WHAT IT NEVER DOES
------------------
`write` never raises. Whatever goes wrong is the returned `problem` and a
`WARNING` line; the release itself is already live and unaffected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gh_read_retry import is_rate_limit_refusal  # noqa: E402
from release_linear import changes_since, surface_filter  # noqa: E402
from usage_reading import card_from_ref  # noqa: E402 — the one `agent/DRE-<n>-…` pattern
from whats_new import (  # noqa: E402
    Entry, WhatsNewError, check_wording, parse_line, required_for, validate)

#: Opens every line this module prints, inside the train's own `[surface]`.
TAG = "whats-new"

#: The GitHub Release asset's name, exactly.
ASSET_NAME = "whats-new.json"

#: `shipped` is the Pacific time the file was published, with its offset.
PACIFIC = ZoneInfo("America/Los_Angeles")

#: What a forbidden pull request read means, named so the hook can raise it.
FORBIDDEN = "caller stub lacks pull-requests: read"

DATA_PATH = ".github/bureau/release.json"

_MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from ")
_SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)\s*$")
_FORBIDDEN_ANSWER = re.compile(r"not accessible by integration", re.I)
_NO_LINE = "no `What's new:` line"


class Forbidden(RuntimeError):
    """GitHub refused to read a pull request: the token lacks the scope."""


class RateLimited(RuntimeError):
    """GitHub is throttling the token: every read after this one fails too."""


class Unreadable(RuntimeError):
    """GitHub would not answer for this one pull request; its message is
    `gh`'s stderr."""


def _run(argv, *, env=None):
    """(returncode, stdout, stderr) — the one seam to `gh`."""
    done = subprocess.run(list(argv), capture_output=True, text=True, env=env)
    return done.returncode, done.stdout, done.stderr


def pull_request_number(message: str) -> int | None:
    """The pull request a change's subject names, or None."""
    subject = (message or "").splitlines()[0] if message else ""
    match = _MERGE_SUBJECT.match(subject) or _SQUASH_SUBJECT.search(subject)
    return int(match.group(1)) if match else None


def read_pull_request(repo: str, number: int, *, run, env) -> dict:
    """`{"head", "body"}` for one pull request. Raises Forbidden when the token
    lacks the scope, RateLimited when GitHub is throttling it, Unreadable on
    any other failure."""
    code, out, err = run(["gh", "api", f"repos/{repo}/pulls/{number}",
                          "--jq", "{head: .head.ref, body: .body}"], env=env)
    if code != 0:
        answer = (err or "").strip()
        if is_rate_limit_refusal(answer):
            raise RateLimited(f"GitHub rate limited the read of #{number} ({answer})")
        if _FORBIDDEN_ANSWER.search(answer):
            raise Forbidden(f"{FORBIDDEN} — GitHub refused to read #{number} "
                            f"({answer})")
        raise Unreadable(answer or f"gh api exited {code}")
    answer = json.loads(out)
    return {"head": answer.get("head") or "", "body": answer.get("body") or ""}


def judge(head: str, body: str) -> tuple:
    """`(entry, None)` for a sentence to publish, `(None, reason)` otherwise."""
    if not required_for(head):
        return None, "exempt"
    try:
        entry = parse_line(body)
    except WhatsNewError as error:
        if _NO_LINE in error.problem:
            return None, "no line"
        return None, f"does not parse: {error.problem}"
    if entry is None:
        return None, "none"
    problems = check_wording(entry.title)
    if problems:
        return None, f"wording: {' '.join(problems)}"
    return entry, None


def collect(changes, *, repo: str, run, env, out) -> tuple:
    """`(entries, skipped)` for the changes, newest change first."""
    entries, skipped, seen = [], [], set()
    for sha, message in reversed(list(changes)):
        number = pull_request_number(message)
        if number is None:
            skipped.append((sha[:7], "uncarded"))
            out(f"{TAG}: {sha[:7]} skipped — uncarded, it names no pull request")
            continue
        if number in seen:
            continue
        seen.add(number)
        try:
            pull = read_pull_request(repo, number, run=run, env=env)
        except Unreadable as error:
            skipped.append((number, f"unreadable: {error}"))
            out(f"{TAG}: #{number} skipped — unreadable: {error}")
            continue
        entry, reason = judge(pull["head"], pull["body"])
        if entry is None:
            skipped.append((number, reason))
            out(f"{TAG}: #{number} skipped — {reason}")
            continue
        card = card_from_ref(pull["head"])
        if card is not None:
            entry = replace(entry, cards=(card,))
        entries.append(entry)
        out(f"{TAG}: #{number} collected — {entry.kind}, {entry.audience}: {entry.title}")
    return entries, skipped


def assemble(product: str, release: str, shipped: str, entries) -> dict:
    """The `whats-new.json` document, in the standard's shape."""
    return {"product": product, "release": release, "shipped": shipped,
            "items": [entry.as_item() for entry in entries]}


def shipped_at(now: datetime | None = None) -> str:
    """`now` on the Pacific clock, with its offset, to the second."""
    moment = datetime.now(PACIFIC) if now is None else now.astimezone(PACIFIC)
    return moment.isoformat(timespec="seconds")


def publish(document: dict, *, repo: str, tag: str, run, env) -> str | None:
    """Create the tag's release with the asset, or clobber the asset on the
    release it has. The release URL `gh` printed; raises RuntimeError with
    `gh`'s stderr on failure."""
    with tempfile.TemporaryDirectory() as folder:
        asset = Path(folder) / ASSET_NAME
        asset.write_text(json.dumps(document, indent=2) + "\n")
        code, out, err = run(["gh", "release", "view", tag, "--repo", repo,
                              "--json", "url", "--jq", ".url"], env=env)
        if code == 0:
            url = out.strip()
            code, _, err = run(["gh", "release", "upload", tag, str(asset),
                                "--clobber", "--repo", repo], env=env)
            command = "gh release upload"
        elif "not found" in (err or "").lower():
            code, out, err = run(["gh", "release", "create", tag, str(asset),
                                  "--repo", repo, "--title", tag,
                                  "--notes", f"What's new in {tag}",
                                  "--verify-tag", "--latest=false"], env=env)
            url = next((line.strip() for line in reversed(out.splitlines())
                        if line.strip().startswith("http")), "")
            command = "gh release create"
        else:
            raise RuntimeError(f"gh release view {tag} failed: {(err or '').strip()}")
        if code != 0:
            raise RuntimeError(f"{command} {tag} failed: {(err or '').strip()}")
        return url or None


def write(*, data, surface_name: str, repo: str, repo_root, version: str,
          sha: str, previous_tag: str | None, run=None, env=None, now=None,
          out=print, dry_run: bool = False) -> dict:
    """Collect this release's sentences and publish them. NEVER raises.

    Returns `{"items", "skipped", "url", "problem"}`: `items` the published
    item dicts, `skipped` one `(number_or_sha, reason)` per change left out,
    `url` the release's URL or None, `problem` None or the first thing that
    went wrong. `dry_run` prints the document and publishes nothing.
    """
    summary = {"items": [], "skipped": [], "url": None, "problem": None}

    def warn(message: str) -> dict:
        summary["items"], summary["url"] = [], None
        summary["problem"] = message
        out(f"{TAG}: WARNING {message} — nothing published; the release itself "
            f"is unaffected")
        return summary

    try:
        run = run or _run
        environ = dict(os.environ if env is None else env)
        if not previous_tag:
            out(f"{TAG}: {version} is the first release in its series — nothing "
                f"to collect, nothing published")
            return summary
        paths, exclude = surface_filter(surface_name, data)
        changes = changes_since(repo_root, previous_tag, sha, paths, exclude)
        out(f"{TAG}: {version} — since {previous_tag}; changes: {len(changes)}")
        try:
            entries, summary["skipped"] = collect(changes, repo=repo, run=run,
                                                  env=environ, out=out)
        except Forbidden as error:
            return warn(str(error))
        if not entries:
            out(f"{TAG}: nothing to say for {version} — nothing published")
            return summary
        document = assemble(repo.partition("/")[2] or repo, version,
                            shipped_at(now), entries)
        problems = validate(document)
        if problems:
            for problem in problems:
                out(f"{TAG}: {problem}")
            return warn(f"the document for {version} does not validate: {problems[0]}")
        if dry_run:
            out(json.dumps(document, indent=2))
            out(f"{TAG}: dry run — {len(entries)} items for {version}, nothing published")
            summary["items"] = document["items"]
            return summary
        summary["url"] = publish(document, repo=repo, tag=version, run=run, env=environ)
        summary["items"] = document["items"]
        out(f"{TAG}: published {ASSET_NAME} for {version} with "
            f"{len(entries)} items — {summary['url'] or 'no URL printed'}")
        return summary
    except Exception as error:  # noqa: BLE001 — never fail a release on What's New
        return warn(f"{ASSET_NAME} for {version} was not published: {error}")


# --------------------------------------------------------------------------- #
# The command line                                                             #
# --------------------------------------------------------------------------- #


def load(path) -> dict:
    """A caller's `release.json`; absent is an empty declaration."""
    path = Path(path)
    if not path.is_file():
        return {"surfaces": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    publisher = sub.add_parser(
        "publish", help="collect a release's lines and publish whats-new.json by hand")
    publisher.add_argument("--repo", required=True, help="owner/name")
    publisher.add_argument("--surface", required=True)
    publisher.add_argument("--tag", required=True)
    publisher.add_argument("--previous-tag", required=True,
                           help="the surface's previous tag; empty for a first release")
    publisher.add_argument("--sha", required=True)
    publisher.add_argument("--file", default=DATA_PATH, help="the caller's release.json")
    publisher.add_argument("--repo-root", default=".")
    publisher.add_argument("--dry-run", action="store_true",
                           help="print the document and publish nothing")
    args = parser.parse_args(argv)
    try:
        data = load(args.file)
    except (OSError, ValueError) as error:
        print(f"{TAG}: cannot read {args.file}: {error}", file=sys.stderr)
        return 1
    result = write(data=data, surface_name=args.surface, repo=args.repo,
                   repo_root=args.repo_root, version=args.tag, sha=args.sha,
                   previous_tag=args.previous_tag or None, dry_run=args.dry_run)
    return 1 if result["problem"] else 0


if __name__ == "__main__":
    sys.exit(main())
