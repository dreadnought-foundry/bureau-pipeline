#!/usr/bin/env python3
"""Is the release carrying the siblings' merges live? (DRE-5922)

A proof that observes production must not start before the release carrying
its siblings' merges is live, and "live" is read off the release record, never
assumed: `main` is not released. This module is the one reader of that
question, so the proof dispatcher never re-derives the rule. It reads GitHub
through an injected `read(path)` — `gh api <path>`, raising on failure — so
the tests drive every rule with fixtures and no network.

The rule, in the release train's own terms (`scripts/release_train.py`: "the
tag IS the receipt"):

  1. Read `.github/bureau/release.json` at the repo's default branch. No file,
     or a file declaring no surfaces, means there is no release to wait for:
     `ready`, and the line says so.
  2. A merge TOUCHES a surface when a file its pull request changed falls
     under the surface's `paths` and matches none of its `ignore` globs and
     none of `release_train.DOCUMENTATION_IGNORE`. An empty `paths` is the
     whole repository, as `release_train._pathspec` reads it. The glob
     semantics are git's: `paths` are plain pathspecs, the ignores are
     `:(exclude,glob)` pathspecs — `tests/test_proof_release.py` holds the
     parity against git itself.
  3. A touched surface's record is the newest tag across ALL its `tag_series`
     globs, by creator date (`release_train.newest_tag`'s ordering): the
     tagger date of an annotated tag, the commit date of a lightweight one.
     For `record: channel` that is the moving tag itself.
  4. The merge is CARRIED when `compare/{tag}...{sha}` answers
     `channel_record.CARRIED`; `ahead` or `diverged` is `waiting`.
  5. A touched surface with no tag at all is `waiting` — a first release is
     exactly what a "nothing changed" reading would swallow.
  6. A read that fails is `unknown`, which is never `ready`.

CLI:
    proof_release.py check --repo OWNER/NAME \\
        --merge DRE-N:<pr>:<sha>[:<file>,<file>…] …

prints `proof-release: <state> — …` per line; exit 0 for ready/waiting, 2 for
unknown. A merge given without files reads its pull request's file list.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Callable, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from channel_record import CARRIED  # noqa: E402
import release_train  # noqa: E402
from release_train import DOCUMENTATION_IGNORE  # noqa: E402

#: Opens every line the CLI prints.
TAG = "proof-release"

RELEASE_JSON = ".github/bureau/release.json"
STATES = ("ready", "waiting", "unknown")
SHORT = 7
#: GitHub's page size for a pull request's files, and the most pages it
#: serves (3,000 files).
FILES_PAGE = 100
FILES_PAGES = 30
WHY_LIMIT = 200

_WILDCARD = re.compile(r"[*?\[]")


class Missing(LookupError):
    """GitHub answered 404 — `gh_read` raises it, so "no file" is told from
    "could not read"."""


class Merge(NamedTuple):
    card: str
    number: int
    sha: str
    files: list


class Reading(NamedTuple):
    state: str          # one of STATES
    lines: list


def gh_read(path: str):
    """`gh api <path>` as JSON — the default `read`. A 404 is `Missing`; any
    other refusal is a `RuntimeError` carrying GitHub's own words."""
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if done.returncode != 0:
        detail = done.stderr.strip() or f"gh api {path} exited {done.returncode}"
        raise (Missing if "HTTP 404" in detail else RuntimeError)(detail)
    text = done.stdout.strip()
    return json.loads(text) if text else None


def _missing(error: Exception) -> bool:
    return isinstance(error, Missing) or "HTTP 404" in str(error)


def _why(error) -> str:
    return " ".join(str(error or "").split())[:WHY_LIMIT] or "no reason given"


# --------------------------------------------------------------------------- #
# The touch rule — git's pathspec semantics, in Python                         #
# --------------------------------------------------------------------------- #


def _glob_regex(glob: str) -> re.Pattern:
    """git's `glob` magic: `*` and `?` stop at `/`, `**/` is any depth
    including none, a trailing `/**` is everything inside."""
    out, i, n = [], 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            j = i
            while j < n and glob[j] == "*":
                j += 1
            segment = i == 0 or glob[i - 1] == "/"
            if j - i >= 2 and segment and j == n:
                out.append(".*")
            elif j - i >= 2 and segment and glob[j] == "/":
                out.append("(?:.*/)?")
                j += 1
            else:
                out.append("[^/]*")
            i = j
            continue
        if c == "?":
            out.append("[^/]")
        elif c == "[" and "]" in glob[i + 2:]:
            end = glob.index("]", i + 2)
            body = glob[i + 1:end]
            if body[:1] == "!":
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = end + 1
            continue
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(glob[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _literal_prefix(path: str, spec: str) -> bool:
    """A pathspec with no wildcard names a file or everything under it."""
    spec = spec.rstrip("/")
    return path == spec or path.startswith(spec + "/")


def under(path: str, paths) -> bool:
    """Is `path` under the surface's `paths`? Plain pathspecs: a literal
    names a file or a directory, a wildcard is `fnmatch` with `*` crossing
    `/`. No paths at all is the whole repository."""
    if not paths:
        return True
    return any(_literal_prefix(path, spec)
               or (_WILDCARD.search(spec) and fnmatch.fnmatchcase(path, spec))
               for spec in paths)


def ignored(path: str, ignore) -> bool:
    """Does `path` match one of `ignore`, read as `:(exclude,glob)`?"""
    return any(_glob_regex(glob).match(path) if _WILDCARD.search(glob)
               else _literal_prefix(path, glob)
               for glob in ignore)


def touching(surface, files) -> list:
    """The files that make `surface` owe a release: under its `paths`, and
    neither its `ignore` nor documentation."""
    return [path for path in files
            if under(path, surface.paths)
            and not ignored(path, surface.ignore)
            and not ignored(path, DOCUMENTATION_IGNORE)]


# --------------------------------------------------------------------------- #
# GitHub's records                                                             #
# --------------------------------------------------------------------------- #


def _release_json(repo: str, read):
    """The repo's `release.json` at its default branch, or None when there is
    none. A 404 is only "no file" once the repo itself reads — a repo the
    token cannot see answers 404 too, and that is unreadable, not empty."""
    try:
        answer = read(f"repos/{repo}/contents/{RELEASE_JSON}")
    except Exception as error:  # noqa: BLE001 — classified below
        if not _missing(error):
            raise
        read(f"repos/{repo}")
        return None
    if not isinstance(answer, dict) or "content" not in answer:
        raise ValueError(f"the contents API answered no file for {RELEASE_JSON}")
    return json.loads(base64.b64decode(answer["content"]))


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def _tag_date(repo: str, target: dict, read) -> datetime:
    """An annotated tag's tagger date, a lightweight tag's commit date — what
    git's `creatordate` sorts on."""
    sha = target["sha"]
    if target.get("type") == "tag":
        return _when(read(f"repos/{repo}/git/tags/{sha}")["tagger"]["date"])
    return _when(read(f"repos/{repo}/git/commits/{sha}")["committer"]["date"])


def newest_tag(repo: str, series, *, read) -> str | None:
    """The newest tag across ALL the globs, by creator date, or None. The refs
    are read by each glob's literal prefix and filtered by the glob itself."""
    best, seen = None, set()
    for glob in series:
        prefix = _WILDCARD.split(glob, maxsplit=1)[0]
        for ref in read(f"repos/{repo}/git/matching-refs/tags/{prefix}") or []:
            name = str(ref.get("ref") or "").removeprefix("refs/tags/")
            if name in seen or not fnmatch.fnmatch(name, glob):
                continue
            seen.add(name)
            when = _tag_date(repo, ref.get("object") or {}, read)
            if best is None or when > best[1]:
                best = (name, when)
    return best[0] if best else None


def _files(repo: str, merge: Merge, read) -> list:
    """The merge's changed files — as handed in, or its pull request's."""
    if merge.files:
        return list(merge.files)
    files = []
    for page in range(1, FILES_PAGES + 1):
        answer = read(f"repos/{repo}/pulls/{merge.number}/files"
                      f"?per_page={FILES_PAGE}&page={page}") or []
        files += [entry["filename"] for entry in answer]
        if len(answer) < FILES_PAGE:
            break
    return files


# --------------------------------------------------------------------------- #
# The reading                                                                  #
# --------------------------------------------------------------------------- #


def _who(merge: Merge) -> str:
    return f"#{merge.number} ({merge.sha[:SHORT]}) for {merge.card}"


def _label(surface) -> str:
    return surface.name if surface.paths else f"{surface.name} (the whole repository)"


def _surface_lines(repo: str, surface, merges, files, read) -> list:
    """`(state, line)` for one surface: its untouched merges in one line,
    then one line per touched merge."""
    label = _label(surface)
    out, touched, untouched = [], [], []
    for merge in merges:
        try:
            changed = files(merge)
        except Exception as error:  # noqa: BLE001 — unreadable is unknown
            out.append(("unknown", f"unknown — {label}: the files {_who(merge)} "
                                   f"changed could not be read: {_why(error)}"))
            continue
        (touched if touching(surface, changed) else untouched).append(merge)
    if untouched:
        where = ", ".join(surface.paths) or "the repository"
        out.insert(0, ("ready", f"ready — {label}: untouched — "
                                f"{', '.join(map(_who, untouched))} changed "
                                f"nothing under {where} that it does not ignore"))
    if not touched:
        return out
    try:
        tag = newest_tag(repo, surface.tag_series, read=read)
    except Exception as error:  # noqa: BLE001
        return out + [("unknown", f"unknown — {label}: its tags in "
                                  f"{', '.join(surface.tag_series)} could not "
                                  f"be read: {_why(error)}")]
    if not tag:
        series = ", ".join(surface.tag_series) or "an empty tag series"
        return out + [("waiting", f"waiting — {label}: no tag in {series} yet, "
                                  f"so nothing carries {_who(m)}")
                      for m in touched]
    record = tag if surface.record == "channel" else f"newest {tag}"
    for merge in touched:
        try:
            status = (read(f"repos/{repo}/compare/{tag}...{merge.sha}")
                      or {}).get("status")
        except Exception as error:  # noqa: BLE001
            out.append(("unknown", f"unknown — {label}: comparing {tag} with "
                                   f"{_who(merge)} failed: {_why(error)}"))
            continue
        if status in CARRIED:
            out.append(("ready", f"ready — {label}: {record} carries {_who(merge)}"))
        elif status in ("ahead", "diverged"):
            out.append(("waiting", f"waiting — {label}: {record} does not "
                                   f"carry {_who(merge)}"))
        else:
            out.append(("unknown", f"unknown — {label}: comparing {tag} with "
                                   f"{_who(merge)} answered {status!r}"))
    return out


def reading(repo: str, merges: list, *, read: Callable = gh_read) -> Reading:
    """Is the release carrying `merges` live in `repo`? One `Reading`."""
    try:
        data = _release_json(repo, read)
        declared = release_train.surfaces(data) if data is not None else {}
    except Exception as error:  # noqa: BLE001 — unreadable holds the proof
        return Reading("unknown", [f"unknown — the release record at "
                                   f"{RELEASE_JSON} could not be read: "
                                   f"{_why(error)}"])
    if data is None:
        return Reading("ready", [f"ready — no release record at {RELEASE_JSON}: "
                                 "nothing to wait for"])
    if not declared:
        return Reading("ready", [f"ready — the release record at {RELEASE_JSON} "
                                 "declares no surfaces: nothing to wait for"])
    known: dict = {}

    def files(merge):
        key = (merge.card, merge.number, merge.sha)
        if key not in known:
            known[key] = _files(repo, merge, read)
        return known[key]

    pairs = [pair for surface in declared.values()
             for pair in _surface_lines(repo, surface, merges, files, read)]
    states = {state for state, _ in pairs}
    state = next((s for s in ("unknown", "waiting") if s in states), "ready")
    return Reading(state, [line for _, line in pairs])


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _merge(text: str) -> Merge:
    """`DRE-N:<pr>:<sha>[:<file>,<file>…]`."""
    parts = text.split(":", 3)
    if len(parts) < 3 or not parts[1].isdigit() or not parts[0] or not parts[2]:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not DRE-N:<pr>:<sha>[:<file>,<file>…]")
    files = [f for f in (parts[3] if len(parts) > 3 else "").split(",") if f]
    return Merge(parts[0], int(parts[1]), parts[2], files)


def main(argv=None, *, read: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="read whether the release is live")
    check.add_argument("--repo", required=True)
    check.add_argument("--merge", action="append", type=_merge, required=True)
    args = parser.parse_args(argv)
    got = reading(args.repo, args.merge, read=read or gh_read)
    for line in got.lines:
        print(f"{TAG}: {line}")
    return 2 if got.state == "unknown" else 0


if __name__ == "__main__":
    sys.exit(main())
