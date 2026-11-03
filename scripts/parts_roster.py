#!/usr/bin/env python3
"""Where one checked-out repo's design system stands, in one line (DRE-8950).

The rollout past Portico goes to every roster repo whose design system exists
by its files, and names each one skipped with the reason. This reads one
checkout and says which it is, read from the files on every run, never from a
stored list:

* `yes` — `design_parts.exists` says so (DRE-8717). The line names the source
  it read and the catalog's part count.
* `undeclared` — `design_parts.exists` says no, yet a folder named `atoms`,
  `molecules`, `organisms` or `templates` directly holds a part by
  `design_parts`' own rule: a part file, or a subfolder with an index UI
  file. A path with a `node_modules`, `.git`, `.bureau-pipeline` or
  test-fixture segment is never read: a fixture tree is a test's design
  system, never the repo's. At most eight folders are named, repo-relative
  and sorted, then `+<k> more`.
* `no` — neither; the reason is the one `design_parts.exists` gives.
* `UNKNOWN` — the declaration or `config/parts-count.json` cannot be read, or
  the root is not a directory.

The first three also say whether the count is on — `config/parts-count.json`
in the pipeline checkout lists the slug — and whether the Parts Check stub is
on: a job in one of the repo's `.github/workflows/` files whose `uses:` names
the reusable `parts-check.yml` at any ref. The `uses:` is read as a job's key,
so a comment, a step name or a step's `uses:` mentioning the file does not
count. `docs/parts-check-stub.md` is the stub.

It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, so it imports the standard library and
`design_parts`, nothing pip-installed. The weekly sweep (DRE-8713) may import
`read` rather than re-derive it.

CLI:
    parts_roster.py read --repo <slug> [--repo-root DIR] [--pipeline-root DIR]

Prints exactly one `parts-roster:` line to stdout. Exits 1 on `UNKNOWN`,
0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_parts  # noqa: E402

#: Opens the one line `read` prints.
TAG = "parts-roster:"

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
COUNT_CONFIG = "config/parts-count.json"
WORKFLOWS = ".github/workflows"
#: A job whose `uses:` starts with this calls the Parts Check reusable.
STUB_USES = "dreadnought-foundry/bureau-pipeline/.github/workflows/parts-check.yml@"

#: A path with one of these segments is never read.
SKIPPED_SEGMENTS = ("node_modules", ".git", ".bureau-pipeline", "fixtures", "__fixtures__")
#: How many undeclared folders the line names before `+<k> more`.
SHOWN = 8

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COMMENT = re.compile(r"(?:^|\s)#.*$")
_JOB_USES = re.compile(r"uses:\s*(?P<value>\S.*)$")


class Reading(NamedTuple):
    slug: str
    #: `yes`, `undeclared`, `no` or `UNKNOWN`.
    state: str
    #: What follows the state: the source and count, the folders, the reason
    #: or the problem.
    detail: str
    count: bool = False
    stub: bool = False

    @property
    def line(self) -> str:
        head = f"{TAG} {_CONTROL.sub(' ', self.slug)} — {self.state} — {self.detail}"
        if self.state == "UNKNOWN":
            return head
        return (f"{head}; count {'on' if self.count else 'off'}; "
                f"stub {'on' if self.stub else 'off'}")


# ---------------------------------------------------------------------------
# Undeclared layer folders
# ---------------------------------------------------------------------------

def layer_folders(repo_root) -> list:
    """Every folder named for a layer that directly holds a part — a part
    file, or a subfolder with an index UI file — repo-relative and sorted."""
    root = Path(repo_root)
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_SEGMENTS]
        path = Path(folder)
        if path == root or path.name not in design_parts.LAYERS:
            continue
        if (any(design_parts._is_part_file(path / name) for name in files)
                or any(design_parts._index(path / name) for name in dirs)):
            found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _folders_text(folders: list) -> str:
    shown = ", ".join(folders[:SHOWN])
    more = len(folders) - SHOWN
    return f"layer folder(s) {shown}" + (f", +{more} more" if more > 0 else "")


# ---------------------------------------------------------------------------
# Count and stub
# ---------------------------------------------------------------------------

def count_on(slug: str, pipeline_root) -> bool:
    """Whether `config/parts-count.json` lists `slug`; `ValueError` naming
    the problem when the file cannot be read."""
    path = Path(pipeline_root) / COUNT_CONFIG
    try:
        repos = json.loads(path.read_text(encoding="utf-8"))["repos"]
        return any(entry["repo"] == slug for entry in repos)
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{COUNT_CONFIG} cannot be read: "
                         f"{type(exc).__name__}: {exc}") from None


def job_uses(text: str) -> list:
    """Every `jobs.<id>.uses` value in one workflow file's text, in order.

    Read by indentation: the top-level `jobs:` key, each job id one level in,
    and a `uses:` key directly under a job id. A comment, a step and a block
    scalar's text sit elsewhere and are never read as one.
    """
    found = []
    in_jobs = False
    job_indent = key_indent = None
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            in_jobs = body == "jobs:"
            job_indent = key_indent = None
        elif not in_jobs:
            continue
        elif job_indent is None or indent <= job_indent:
            job_indent, key_indent = indent, None
        else:
            if key_indent is None:
                key_indent = indent
            match = _JOB_USES.fullmatch(body) if indent == key_indent else None
            if match:
                found.append(match.group("value").strip().strip("\"'"))
    return found


def stub_on(repo_root) -> bool:
    """Whether a job in the repo's workflows calls the Parts Check reusable."""
    folder = Path(repo_root) / WORKFLOWS
    if not folder.is_dir():
        return False
    for path in sorted(folder.iterdir()):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(value.startswith(STUB_USES) for value in job_uses(text)):
            return True
    return False


# ---------------------------------------------------------------------------
# The reading
# ---------------------------------------------------------------------------

def read(slug: str, repo_root, pipeline_root=PIPELINE_ROOT) -> Reading:
    """Where `slug`'s checkout at `repo_root` stands. Never raises."""
    root = Path(repo_root)
    try:
        if not root.is_dir():
            raise ValueError(f"{root} is not a directory")
        present, why = design_parts.exists(root)
        if present:
            state, detail = "yes", f"{why}, {len(design_parts.catalog(root))} part(s)"
        else:
            folders = layer_folders(root)
            state, detail = (("undeclared", _folders_text(folders)) if folders
                             else ("no", why))
        count, stub = count_on(slug, pipeline_root), stub_on(root)
    except Exception as exc:  # the reader always answers in one line
        problem = (str(exc) if isinstance(exc, (design_parts.Invalid, ValueError))
                   else f"{type(exc).__name__}: {exc}")
        return Reading(slug, "UNKNOWN", _CONTROL.sub(" ", problem))
    return Reading(slug, state, detail, count, stub)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_read(args) -> int:
    reading = read(args.repo, args.repo_root, args.pipeline_root)
    print(reading.line)
    return 1 if reading.state == "UNKNOWN" else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("read", help="say where one checkout's design system stands")
    command.add_argument("--repo", required=True, help="the repo's roster slug")
    command.add_argument("--repo-root", default=".")
    command.add_argument("--pipeline-root", default=str(PIPELINE_ROOT))
    command.set_defaults(func=cmd_read)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
