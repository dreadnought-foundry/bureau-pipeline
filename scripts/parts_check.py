#!/usr/bin/env python3
"""The duplicate-parts check: copies of shared parts, counted from the files (DRE-8841).

A copy is a shared part declared again. Every part in the checkout's catalog
(`design_parts.catalog`, DRE-8717) that is declared again as a component, under
the part's name, in any other UI file of the repo is one copy. The other UI
files are read inside the declared folders and outside them; a `.test.`,
`.spec.` or `.stories.` file, and any path with a `node_modules`, `.git` or
`.bureau-pipeline` segment, is never read. A copy's identity is (repo, part
name, copy file).

Nothing is kept. The copies are computed from the checkout's files on every
run — `git ls-files` when the root is a git work tree, the directory tree
otherwise — and no baseline is written or read. Components are recognized
with the patterns the critic's parts review already uses
(`parts_review._declarations`), and the diff is read with its parser
(`parts_review.parse_diff`).

`check --diff` judges only the pull request's diff. It fails for a copy one of
whose two declarations — the part's or the copy's — sits on a line the diff
adds, and for a UI file the diff adds or changes, outside every declared
folder and not a test, spec or story, that declares a component and imports
none of the shared parts. A file imports a shared part when an `import`
statement binds a part's name (`import { Notice as N }` counts as `Notice`; a
default import counts by its local name), or names a relative specifier that
resolves, against the file's own folder, into a declared folder.

It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, so it imports the standard library and the
two scripts beside it, nothing pip-installed. `docs/parts-check.md` is the
contract.

CLI:
    parts_check.py check --diff <unified diff file> [--repo-root DIR]
    parts_check.py count [--repo-root DIR] [--repo SLUG] [--json]

Each prints exactly one tagged line to stderr. `check` exits 1 on `fail` and
`UNKNOWN`; `count` exits 1 on `UNKNOWN` only.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_parts  # noqa: E402
import parts_review  # noqa: E402

#: Opens the one stderr line of `check`, and every finding line it prints.
CHECK_TAG = "parts-check:"
#: Opens the one stderr line of `count`.
COUNT_TAG = "parts-count:"

#: A path with one of these segments is never read.
SKIPPED_SEGMENTS = ("node_modules", ".git", ".bureau-pipeline")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_IMPORT_FROM = re.compile(
    r"^[ \t]*import\s+(?:type\s+)?(?P<clause>[\w$*{}\s,]+?)\s*from\s*"
    r"(?P<quote>[\"'])(?P<spec>[^\"'\n]+)(?P=quote)", re.M)
_IMPORT_BARE = re.compile(r"^[ \t]*import\s*(?P<quote>[\"'])(?P<spec>[^\"'\n]+)(?P=quote)",
                          re.M)


class Copy(NamedTuple):
    part: str
    layer: str
    part_path: str
    part_line: int
    copy_path: str
    copy_line: int


class Counted(NamedTuple):
    #: The declaration source, or None when the design system does not exist.
    source: str | None
    #: Why nothing was counted, or None when the copies were counted.
    reason: str | None
    copies: list


# ---------------------------------------------------------------------------
# The files
# ---------------------------------------------------------------------------

def _read(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _skipped(path: str) -> bool:
    pure = PurePosixPath(path)
    return (any(part in SKIPPED_SEGMENTS for part in pure.parts[:-1])
            or any(mark in pure.name for mark in design_parts.NOT_PARTS))


def _git_files(root: Path) -> list | None:
    """Every file `git ls-files` names under `root` — tracked, and untracked
    but not ignored — or None when `root` is not in a git work tree."""
    try:
        inside = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
    except OSError:
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True)
    names = listed.stdout.decode("utf-8", "surrogateescape").split("\0")
    return sorted({name for name in names if name and (root / name).is_file()})


def _walked_files(root: Path) -> list:
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_SEGMENTS]
        rel = Path(folder).relative_to(root)
        found.extend((rel / name).as_posix() for name in files)
    return sorted(found)


def ui_files(repo_root) -> list:
    """Every UI file of the checkout the count reads, repo-relative, sorted."""
    root = Path(repo_root)
    files = _git_files(root)
    if files is None:
        files = _walked_files(root)
    return [path for path in files
            if design_parts.is_ui_file(path) and not _skipped(path)]


def _line_of(text: str, match: re.Match) -> int:
    return text.count("\n", 0, match.start()) + 1


def _declared(text: str) -> dict:
    """Component name → the line of its first declaration in `text`."""
    lines = {}
    for match in parts_review._declarations(text):
        lines.setdefault(match[1], _line_of(text, match))
    return lines


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------

def count(repo_root) -> Counted:
    """Every copy of a shared part in the checkout, sorted by (part, copy
    path, copy line). `design_parts.Invalid` when the declaration cannot be
    read."""
    root = Path(repo_root)
    present, why = design_parts.exists(root)
    if not present:
        return Counted(None, f"the repo's design system does not exist ({why})", [])
    source = design_parts.load(root).source
    parts = {}
    for part in design_parts.catalog(root):
        parts.setdefault(part.name, part)
    if not parts:
        return Counted(source, f"no part ships in the folders read from {source}", [])
    part_lines = {}
    for name, part in parts.items():
        text = _read(root, part.ships_at)
        # A part no pattern recognizes (a `.vue` or `.svelte` part, say) is
        # declared by its file, from its first line.
        part_lines[name] = _declared(text or "").get(name, 1)
    copies = []
    for path in ui_files(root):
        text = _read(root, path)
        if text is None:
            continue
        for name, line in _declared(text).items():
            part = parts.get(name)
            if part is not None and path != part.ships_at:
                copies.append(Copy(name, part.layer, part.ships_at, part_lines[name],
                                   path, line))
    copies.sort(key=lambda c: (c.part, c.copy_path, c.copy_line))
    return Counted(source, None, copies)


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------

def added_lines(text: str) -> dict:
    """Path → the new-side line numbers of every line the diff adds.

    The diff is parsed by `parts_review.parse_diff` first, which raises
    `parts_review.DiffError` on anything malformed; this walk then only
    numbers the lines that parse already accepted.
    """
    parts_review.parse_diff(text)
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    added, path, i = {}, None, 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            new = parts_review._header_path(lines[i + 1][4:])
            path = new
            if new is not None:
                added.setdefault(new, set())
            i += 2
            continue
        match = parts_review._HUNK.match(line) if line.startswith("@@") else None
        if match is None:
            i += 1
            continue
        old_left = int(match[2]) if match[2] is not None else 1
        new_left = int(match[4]) if match[4] is not None else 1
        number = int(match[3])
        i += 1
        while old_left or new_left:
            kind = lines[i][:1]
            if kind == " " or lines[i] == "":
                old_left, new_left, number = old_left - 1, new_left - 1, number + 1
            elif kind == "-":
                old_left -= 1
            elif kind == "+":
                if path is not None:
                    added[path].add(number)
                new_left, number = new_left - 1, number + 1
            i += 1
    return added


def _folders(repo_root) -> list:
    return parts_review._folders(design_parts.load(repo_root))


def _inside_any(path: str, folders: list) -> bool:
    return any(parts_review._inside(path, folder) for folder in folders)


def _bound_names(clause: str) -> set:
    """The names an import clause binds a part by: each named binding's
    imported name, and a default import's local name."""
    names = set()
    braced = re.search(r"\{([^}]*)\}", clause)
    if braced:
        for item in braced[1].split(","):
            words = item.split()
            if words[:1] == ["type"]:
                words = words[1:]
            if not words:
                continue
            imported = words[0]
            if imported == "default" and len(words) == 3 and words[1] == "as":
                imported = words[2]
            names.add(imported)
    default = clause[:braced.start()] if braced else clause
    first = default.split(",")[0].strip()
    if first and not first.startswith("*"):
        names.add(first)
    return names


def _resolves_into(path: str, spec: str, folders: list) -> bool:
    if not (spec in (".", "..") or spec.startswith(("./", "../"))):
        return False
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    return not resolved.startswith("..") and _inside_any(resolved, folders)


def imports_a_part(path: str, text: str, names: set, folders: list) -> bool:
    """Whether an `import` statement in `text` imports a shared part."""
    code = parts_review._strip_comments(text)
    for match in _IMPORT_FROM.finditer(code):
        if _bound_names(match["clause"]) & names:
            return True
        if _resolves_into(path, match["spec"], folders):
            return True
    return any(_resolves_into(path, match["spec"], folders)
               for match in _IMPORT_BARE.finditer(code))


def _new_side(diff) -> str:
    return "\n".join(text for kind, text in diff.lines if kind != "-") + "\n"


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return _CONTROL.sub(" ", text)


def check(diff_path, repo_root) -> tuple:
    """`(status line, finding lines, exit code)` for one diff."""
    root = Path(repo_root)
    path = Path(diff_path)
    files, why = parts_review._read_diff(path)
    if files is None:
        return f"{CHECK_TAG} UNKNOWN — {_clean(why)}", [], 1
    try:
        present, reason = design_parts.exists(root)
    except design_parts.Invalid as exc:
        return (f"{CHECK_TAG} UNKNOWN — the design system's declaration cannot be "
                f"read: {_clean(str(exc))}"), [], 1
    if not present:
        return (f"{CHECK_TAG} does not apply — the repo's design system does not exist "
                f"({reason})"), [], 0
    ui = []
    for diff in files:
        if not diff.deleted and design_parts.is_ui_file(diff.path) and diff not in ui:
            ui.append(diff)
    if not ui:
        extensions = ", ".join(design_parts.UI_EXTENSIONS)
        return (f"{CHECK_TAG} does not apply — the diff adds or changes no UI file "
                f"({extensions})"), [], 0
    counted = count(root)
    if counted.reason is not None:
        return f"{CHECK_TAG} does not apply — {counted.reason}", [], 0
    added = added_lines(path.read_bytes().decode("utf-8"))
    copies = [c for c in counted.copies
              if c.copy_line in added.get(c.copy_path, ())
              or c.part_line in added.get(c.part_path, ())]
    names = {part.name for part in design_parts.catalog(root)}
    folders = _folders(root)
    lonely = []
    for diff in ui:
        name = PurePosixPath(diff.path).name
        if (_inside_any(diff.path, folders) or diff.path in lonely
                or any(mark in name for mark in design_parts.NOT_PARTS)):
            continue
        text = _read(root, diff.path)
        if text is None:
            text = _new_side(diff)
        if parts_review._declarations(text) and not imports_a_part(
                diff.path, text, names, folders):
            lonely.append(diff.path)
    findings = ([f"{CHECK_TAG} COPY {c.part} — shared part declared at "
                 f"{c.part_path}:{c.part_line}; copy declared at {c.copy_path}:{c.copy_line}"
                 for c in copies]
                + [f"{CHECK_TAG} NO-SHARED-PART {p} — imports none of the shared parts"
                   for p in lonely])
    if findings:
        return (f"{CHECK_TAG} fail — {len(copies)} copy(ies) added, {len(lonely)} UI "
                f"file(s) importing no shared part"), findings, 1
    checked = len({diff.path for diff in ui})
    return (f"{CHECK_TAG} pass — {checked} UI file(s) checked, 0 copies added, 0 UI "
            f"files importing no shared part"), [], 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_check(args) -> int:
    try:
        status, findings, code = check(args.diff, args.repo_root)
    except Exception as exc:  # the check always answers
        status, findings, code = (f"{CHECK_TAG} UNKNOWN — "
                                  f"{_clean(f'{type(exc).__name__}: {exc}')}"), [], 1
    for line in findings:
        print(line)
    print(status, file=sys.stderr)
    return code


def cmd_count(args) -> int:
    who = args.repo if args.repo else str(Path(args.repo_root).resolve())
    try:
        counted = count(args.repo_root)
    except Exception as exc:  # the count always answers
        problem = str(exc) if isinstance(exc, design_parts.Invalid) else \
            f"{type(exc).__name__}: {exc}"
        problem = _clean(problem)
        if args.json:
            print(json.dumps({"repo": args.repo, "source": None, "reason": problem,
                              "copies": []}))
        print(f"{COUNT_TAG} {who} — UNKNOWN — {problem}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"repo": args.repo, "source": counted.source,
                          "reason": counted.reason,
                          "copies": [c._asdict() for c in counted.copies]}))
    else:
        for c in counted.copies:
            print(f"COPY {c.part} — shared part declared at {c.part_path}:{c.part_line}; "
                  f"copy declared at {c.copy_path}:{c.copy_line}")
    if counted.reason is not None:
        print(f"{COUNT_TAG} {who} — none — {counted.reason}", file=sys.stderr)
    else:
        print(f"{COUNT_TAG} {who} — {len(counted.copies)} copy(ies) of shared parts "
              f"from {counted.source}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    checking = sub.add_parser("check", help="judge a pull request's diff")
    checking.add_argument("--diff", required=True)
    checking.add_argument("--repo-root", default=".")
    checking.set_defaults(func=cmd_check)
    counting = sub.add_parser("count", help="count every copy of a shared part")
    counting.add_argument("--repo-root", default=".")
    counting.add_argument("--repo", default=None)
    counting.add_argument("--json", action="store_true")
    counting.set_defaults(func=cmd_count)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
