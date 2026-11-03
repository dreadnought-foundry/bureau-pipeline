#!/usr/bin/env python3
"""The weekly sweep's count: each repo's copies of shared parts now and a week ago (DRE-8944).

Three commands, which the weekly workflow (DRE-8945) runs:

* `roster` reads `config/repo-map.json` at run time, never a copied list, and
  prints `repos=<JSON list>`, one row per key. A repo `config/parts-count.json`
  lists takes that entry's `agent`, `initiative` and `owned_by`, with
  `listed: true`. Any other repo gets `listed: false`, `agent:frontend`, no
  `owned_by`, and its initiative read off `validate_card._INITIATIVE_ALIAS`
  inverted: that table maps an initiative to the repo slug it routes to, so a
  slug that is a VALUE in it gets `initiative:<that key>` and any other slug
  `initiative:<slug>`. An unlisted repo's labels are used only on a
  replacement card a rejection files (DRE-8941); no cleanup card is filed
  under them.
* `count` runs in one repo's checkout, with its full history. With no design
  system (`design_parts.exists`) it writes a `skipped` record. Otherwise it
  counts copies with `parts_check.count`, writes the unchanged
  `parts_check.py count --json` object to `--count-out` for the filer
  (`parts_cleanup.py`), then counts again in a temporary `git worktree` of the
  newest commit on the checked-out branch at or before `--week-ago`, and
  removes the worktree. Nothing is stored between weeks.
* `record` writes the record of a repo the workflow did not count — `unread`,
  `archived` or `failed`, decided from GitHub.

Every command prints exactly one `parts-sweep:` (`TAG`) line to stderr per
repo, naming repos, counts, declaration files and reasons only.
`docs/parts-sweep.md` is the contract.

It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, so it imports the standard library and the
scripts beside it, nothing pip-installed.

CLI:
    parts_sweep.py roster [--repo <slug>]
    parts_sweep.py count --repo <slug> --repo-root DIR --week-ago <ISO 8601>
        --json-out FILE --count-out FILE
    parts_sweep.py record --repo <slug> --state <unread|archived|failed>
        --reason <text> --json-out FILE

`roster` exits 1 on a slug the map does not carry or a config it cannot read;
`count` exits 1 on `UNKNOWN`; `record` exits 1 for `failed`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_parts  # noqa: E402
import parts_check  # noqa: E402

#: Opens the one stderr line per repo.
TAG = "parts-sweep:"
#: Every state a count record carries.
STATES = ("counted", "skipped", "unread", "archived", "failed")
#: The states `record` writes, and its exit code for each.
RECORDED = {"unread": 0, "archived": 0, "failed": 1}

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
REPO_MAP = "config/repo-map.json"
COUNT_CONFIG = "config/parts-count.json"
#: The agent label of a repo `config/parts-count.json` does not list.
UNLISTED_AGENT = "agent:frontend"
#: How a count line ends for a repo `config/parts-count.json` does not list.
NOT_CARDED = f", not carded yet — no entry in {COUNT_CONFIG}"
#: A fall in the count is written with the minus sign, never a hyphen.
MINUS = "−"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Problem(Exception):
    """Why the run cannot answer: printed as the `UNKNOWN` line."""


def _clean(text) -> str:
    return _CONTROL.sub(" ", str(text)).strip()


def _line(slug: str, rest: str) -> str:
    return f"{TAG} {_clean(slug)} — {rest}"


def _json(root, name: str):
    path = Path(root) / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Problem(f"{name} cannot be read: {type(exc).__name__}: {exc}") from None


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------

def listed_entries(pipeline_root) -> dict:
    """Slug → its `config/parts-count.json` entry; `Problem` when unreadable."""
    doc = _json(pipeline_root, COUNT_CONFIG)
    repos = doc.get("repos") if isinstance(doc, dict) else None
    if not isinstance(repos, list):
        raise Problem(f"{COUNT_CONFIG} carries no `repos` list")
    entries = {}
    for i, entry in enumerate(repos):
        if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) and entry[key].strip()
                for key in ("repo", "agent", "initiative")):
            raise Problem(f"{COUNT_CONFIG} entry {i} carries no `repo`, `agent` and "
                          f"`initiative`")
        owner = entry.get("owned_by", "")
        if not isinstance(owner, str):
            raise Problem(f"{COUNT_CONFIG} entry {i} carries an `owned_by` that is not text")
        entries[entry["repo"]] = entry
    return entries


def unlisted_initiative(slug: str) -> str:
    """The initiative label of a repo `config/parts-count.json` does not list.

    `validate_card._INITIATIVE_ALIAS` maps an initiative to the repo slug it
    routes to, so it is read backwards: a slug that is a value in it gets the
    key, any other slug itself. Read on every call, never copied.
    """
    import validate_card  # noqa: PLC0415 — lazy: only `roster` needs the table

    inverted = {repo: initiative for initiative, repo in validate_card._INITIATIVE_ALIAS.items()}
    return f"initiative:{inverted.get(slug, slug)}"


def roster(pipeline_root=PIPELINE_ROOT) -> list:
    """One row per key of `config/repo-map.json`, in the map's order."""
    repo_map = _json(pipeline_root, REPO_MAP)
    if not isinstance(repo_map, dict):
        raise Problem(f"{REPO_MAP} must hold one JSON object")
    entries = listed_entries(pipeline_root)
    rows = []
    for slug, full in repo_map.items():
        owner, _, name = full.partition("/") if isinstance(full, str) else ("", "", "")
        if not owner or not name or "/" in name:
            raise Problem(f"{REPO_MAP} maps {slug!r} to {full!r}, not <owner>/<name>")
        entry = entries.get(slug)
        if entry is not None:
            labels = (entry["agent"], entry["initiative"], entry.get("owned_by", ""), True)
        else:
            labels = (UNLISTED_AGENT, unlisted_initiative(slug), "", False)
        rows.append(dict(zip(("repo", "owner", "name", "agent", "initiative", "owned_by",
                              "listed"), (slug, owner, name, *labels))))
    return rows


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def record(slug: str, state: str, *, listed=None, reason=None, source=None, copies=None,
           last_week=None, last_week_sha=None, last_week_reason=None) -> dict:
    return {"repo": slug, "state": state, "listed": listed, "reason": reason,
            "source": source, "copies": copies, "last_week": last_week,
            "last_week_sha": last_week_sha, "last_week_reason": last_week_reason}


def _write(path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------

def _instant(text: str) -> datetime:
    """`--week-ago` as an aware instant; a time with no offset is ambiguous."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise Problem(f"--week-ago {text!r} is not an ISO 8601 time") from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise Problem(f"--week-ago {text!r} carries no UTC offset")
    return when


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Problem(f"git {args[0]} failed: {_clean(done.stderr) or done.returncode}")
    return done.stdout.strip()


def _copies(root) -> tuple:
    """`(source, number of copies, the count JSON object)` at `root`."""
    counted = parts_check.count(root)
    return counted.source, len(counted.copies), counted


def week_ago(root: Path, when: datetime) -> tuple:
    """`(copies, sha, reason)` as the checked-out branch stood at `when`."""
    utc = when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    sha = _git(root, "rev-list", "-1", f"--before={utc}", "HEAD")
    if not sha:
        shallow = _git(root, "rev-parse", "--is-shallow-repository") == "true"
        return None, None, (f"no commit on the checked-out branch is at or before "
                            f"{when.isoformat()}" + (" (the checkout is shallow)"
                                                     if shallow else ""))
    holder = Path(tempfile.mkdtemp(prefix="parts-sweep-"))
    tree = holder / "week-ago"
    try:
        _git(root, "worktree", "add", "--detach", "--quiet", str(tree), sha)
        try:
            present, why = design_parts.exists(tree)
            if not present:
                return None, sha, f"no design system at {sha[:12]} ({why})"
            return _copies(tree)[1], sha, None
        except design_parts.Invalid as exc:
            return None, sha, (f"the design system's declaration cannot be read at "
                               f"{sha[:12]}: {_clean(exc)}")
        finally:
            _git(root, "worktree", "remove", "--force", str(tree))
    finally:
        shutil.rmtree(holder, ignore_errors=True)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)


def _change(now: int, then: int) -> str:
    if now > then:
        return f"+{now - then}"
    if now < then:
        return f"{MINUS}{then - now}"
    return "no change"


def count(slug: str, repo_root, when_text: str, pipeline_root=PIPELINE_ROOT) -> tuple:
    """`(record, count JSON object or None, line)`; `Problem` or any error
    when the repo cannot be counted."""
    root = Path(repo_root)
    when = _instant(when_text)
    listed = slug in listed_entries(pipeline_root)
    present, why = design_parts.exists(root)
    if not present:
        return (record(slug, "skipped", listed=listed, reason=why), None,
                _line(slug, f"skipped — no design system ({_clean(why)})"))
    source, now, counted = _copies(root)
    count_json = {"repo": slug, "source": counted.source, "reason": counted.reason,
                  "copies": [c._asdict() for c in counted.copies]}
    then, sha, then_reason = week_ago(root, when)
    if then_reason is None:
        text = f"{now} copy(ies) now, {then} a week ago ({_change(now, then)})"
    else:
        text = f"{now} copy(ies) now, not counted a week ago — {_clean(then_reason)}"
    if not listed:
        text += NOT_CARDED
    return (record(slug, "counted", listed=listed, source=source, copies=now,
                   last_week=then, last_week_sha=sha, last_week_reason=then_reason),
            count_json, _line(slug, text))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _problem(exc: Exception) -> str:
    known = isinstance(exc, (Problem, design_parts.Invalid))
    return _clean(str(exc) if known else f"{type(exc).__name__}: {exc}")


def cmd_roster(args) -> int:
    try:
        rows = roster(args.pipeline_root)
        if args.repo is not None:
            rows = [row for row in rows if row["repo"] == args.repo]
            if not rows:
                raise Problem(f"{REPO_MAP} carries no key {args.repo!r}")
    except Exception as exc:  # the roster always answers
        who = args.repo if args.repo is not None else "roster"
        print(_line(who, f"UNKNOWN — {_problem(exc)}"), file=sys.stderr)
        return 1
    print(f"repos={json.dumps(rows)}")
    return 0


def cmd_count(args) -> int:
    try:
        doc, count_json, line = count(args.repo, args.repo_root, args.week_ago,
                                      args.pipeline_root)
    except Exception as exc:  # the count always answers, and always records
        problem = _problem(exc)
        _write(args.json_out, record(args.repo, "failed", reason=problem))
        print(_line(args.repo, f"UNKNOWN — {problem}"), file=sys.stderr)
        return 1
    _write(args.json_out, doc)
    if count_json is not None:
        _write(args.count_out, count_json)
    print(line, file=sys.stderr)
    return 0


def cmd_record(args) -> int:
    reason = _clean(args.reason)
    if not reason:
        print(_line(args.repo, "UNKNOWN — --reason is empty"), file=sys.stderr)
        return 1
    _write(args.json_out, record(args.repo, args.state, reason=reason))
    print(_line(args.repo, f"{args.state} — {reason}"), file=sys.stderr)
    return RECORDED[args.state]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("roster", help="print one row per repo of the roster")
    listing.add_argument("--repo", default=None)
    listing.set_defaults(func=cmd_roster)
    counting = sub.add_parser("count", help="count one checkout now and a week ago")
    counting.add_argument("--repo", required=True)
    counting.add_argument("--repo-root", required=True)
    counting.add_argument("--week-ago", required=True)
    counting.add_argument("--json-out", required=True)
    counting.add_argument("--count-out", required=True)
    counting.set_defaults(func=cmd_count)
    recording = sub.add_parser("record", help="record a repo the workflow did not count")
    recording.add_argument("--repo", required=True)
    recording.add_argument("--state", required=True, choices=tuple(RECORDED))
    recording.add_argument("--reason", required=True)
    recording.add_argument("--json-out", required=True)
    recording.set_defaults(func=cmd_record)
    for command in (listing, counting, recording):
        command.add_argument("--pipeline-root", default=str(PIPELINE_ROOT),
                             help="the bureau-pipeline checkout whose config/ is read")
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
