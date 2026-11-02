#!/usr/bin/env python3
"""A pull request's diff, read against the parts catalog (DRE-8719).

The design critic judges whether a pull request uses the shared parts
(DRE-8708, rules 1 to 5 and the look-alike half of rule 9). This script does
the mechanical half: it reads the pull request's unified diff beside the
checkout's parts catalog (`design_parts.py`, DRE-8717) and hands the critic
grounded candidates, each with the shared part's name and "ships at" path
already looked up. The critic still decides; its wiring is DRE-8721.

Where each side is read from. The critic's checkout is not always the pull
request's head: a `pull_request` run holds the pull request merged into its
base, a `workflow_dispatch` re-review holds the default branch. So every
component the diff adds or changes, and its props, is read from the diff's
own lines (added and context; for a file the diff adds, the whole file), and
no file the diff names is ever opened in the checkout. Only the catalog is
read from the checkout, and every part the diff adds is taken back out of it:
that is the catalog before the diff, and every finding is judged against it.
A catalog part at a path the diff changes or deletes has its props read from
the diff's old side for the same reason.

Each component declared in an added line of a UI file gets one finding, the
first kind that fits:

    copy             its name is a part's name, its file outside every folder
    look-alike       outside every folder, >= 2 shared props, Jaccard >= 0.6
    new-part         declared in a part file the diff adds inside a folder
    local-component  any other component outside the folders

A part file the diff adds whose component no pattern recognizes (a `.vue` or
`.svelte` part, say) still gets its `new-part` finding, under its part name.

Every finding is one line:

    FINDING <kind> (suggested verdict: <REQUEST_CHANGES|APPROVE|NOTE>) — <text>

`NOTE` is the local component's: a note for the critic's judgment, never
blocking by itself. No finding line raises a design question — only the
critic does.

It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, so it imports the standard library and
`design_parts` beside it, nothing pip-installed. The CI check and the weekly
sweep (DRE-8712, DRE-8713) read the provisional record through
`provisional_records`.

CLI:
    parts_review.py review --diff <unified diff file> [--repo-root DIR]

It always exits 0 and prints exactly one `parts-review:` line to stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_parts  # noqa: E402

#: Opens the one stderr line every run prints.
TAG = "parts-review:"
#: Opens the provisional record line in the critic's APPROVE verdict.
PROVISIONAL_MARK = "🪴 part-provisional:"

REVIEW = "PARTS REVIEW:"
CATALOG = design_parts.CATALOG

KINDS = ("copy", "look-alike", "new-part", "local-component")
#: A look-alike shares at least this many prop names with a part …
MIN_SHARED = 2
#: … at a Jaccard similarity of at least this.
MIN_SIMILARITY = 0.6

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_NAME = r"([A-Z][A-Za-z0-9]*)"
_DECLARATIONS = (
    re.compile(rf"^[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?(?:async[ \t]+)?"
               rf"function[ \t]+{_NAME}[ \t]*(?:<[^>(\n]*>)?[ \t]*\(", re.M),
    re.compile(rf"^[ \t]*(?:export[ \t]+)?const[ \t]+{_NAME}[ \t]*(?::[^=\n]+)?="
               rf"[ \t]*(?P<ref>(?:React\.)?forwardRef[ \t]*(?:<[^(\n]*>)?[ \t]*\()?"
               rf"(?(ref)|(?:async[ \t]*)?\()", re.M),
)
_MEMBER = re.compile(r"^(?:readonly\s+)?([A-Za-z_$][\w$]*)\s*\??\s*[:(]")
_KEY = re.compile(r"^([A-Za-z_$][\w$]*)")
_PROVISIONAL = re.compile(
    rf"^[ \t]*{re.escape(PROVISIONAL_MARK)} `([^`\n]+)` at `([^`\n]+)` — "
    rf"reasoning: (.+?)[ \t]*$", re.M)


class DiffError(ValueError):
    """The diff cannot be parsed; the message names the problem."""


class FileDiff(NamedTuple):
    #: The file's repo-relative path: the new path, or the old for a deletion.
    path: str
    added: bool
    deleted: bool
    #: `(kind, text)` per hunk line, kind one of " ", "+", "-".
    lines: tuple


class Finding(NamedTuple):
    kind: str
    verdict: str
    text: str


class Provisional(NamedTuple):
    name: str
    path: str
    reasoning: str


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------

def _header_path(rest: str) -> str | None:
    """The path a `---`/`+++` header names, or None for `/dev/null`."""
    path = rest.split("\t", 1)[0].strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        path = path[1:-1]
    if path == "/dev/null":
        return None
    if path[:2] in ("a/", "b/"):
        path = path[2:]
    return path


def parse_diff(text: str) -> list:
    """Every file a unified diff touches, as `FileDiff`, in diff order.

    Lines outside a file's hunks (git's `diff --git`, `index`, mode and
    rename lines, a patch's message) are passed over; inside a hunk every
    line is counted against the hunk header. `DiffError` names the first
    thing wrong.
    """
    lines = text.splitlines()
    files = []
    i = 0
    current = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = _header_path(line[4:]), _header_path(lines[i + 1][4:])
            if old is None and new is None:
                raise DiffError(f"line {i + 1}: both sides of a file header are /dev/null")
            current = {"path": new if new is not None else old,
                       "added": old is None, "deleted": new is None, "lines": []}
            files.append(current)
            i += 2
            continue
        if line.startswith("--- "):
            raise DiffError(f"line {i + 1}: a `--- ` header with no `+++ ` header after it")
        if line.startswith("@@"):
            match = _HUNK.match(line)
            if not match:
                raise DiffError(f"line {i + 1}: a malformed hunk header")
            if current is None:
                raise DiffError(f"line {i + 1}: a hunk before any file header")
            old_left = int(match[2]) if match[2] is not None else 1
            new_left = int(match[4]) if match[4] is not None else 1
            header, i = i + 1, i + 1
            while old_left or new_left:
                if i >= len(lines):
                    raise DiffError(f"the hunk at line {header} ends before its header says")
                body = lines[i]
                kind, rest = body[:1], body[1:]
                if kind == " " or body == "":
                    old_left -= 1
                    new_left -= 1
                    current["lines"].append((" ", rest))
                elif kind == "-":
                    old_left -= 1
                    current["lines"].append(("-", rest))
                elif kind == "+":
                    new_left -= 1
                    current["lines"].append(("+", rest))
                elif kind != "\\":
                    raise DiffError(f"line {i + 1}: a hunk line that opens with "
                                    f"neither ` `, `+` nor `-`")
                if old_left < 0 or new_left < 0:
                    raise DiffError(f"line {i + 1}: a hunk longer than its header says")
                i += 1
            continue
        i += 1
    if not files:
        raise DiffError("it holds no file header (`--- ` then `+++ `)")
    return [FileDiff(f["path"], f["added"], f["deleted"], tuple(f["lines"]))
            for f in files]


def _side(diff: FileDiff, kinds: str) -> list:
    return [text for kind, text in diff.lines if kind in kinds]


# ---------------------------------------------------------------------------
# Components and their props
# ---------------------------------------------------------------------------

def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text) and text[i] != quote:
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _strip_comments(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] in "\"'`":
            end = _skip_string(text, i)
            out.append(text[i:end])
            i = end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _braced(text: str, start: int) -> str | None:
    """The text inside the `{…}` that opens at `start`, or None when it
    never closes."""
    depth, i = 0, start
    while i < len(text):
        char = text[i]
        if char in "\"'`":
            i = _skip_string(text, i)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def _top_level(body: str, separators: str) -> list:
    """`body` split on `separators` wherever no bracket is open."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(body):
        char = body[i]
        if char in "\"'`":
            i = _skip_string(body, i)
            continue
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif depth == 0 and char in separators:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    parts.append(body[start:])
    return [part.strip() for part in parts if part.strip()]


def _type_props(text: str, name: str) -> list | None:
    """The member names of `<name>Props`, or None when `text` declares none."""
    found = re.search(rf"\b(?:interface|type)\s+{re.escape(name)}Props\b", text)
    if not found:
        return None
    brace = text.find("{", found.end())
    if brace < 0:
        return None
    body = _braced(text, brace)
    if body is None:
        return None
    names = []
    for member in _top_level(_strip_comments(body), ";,\n"):
        match = _MEMBER.match(member)
        if match and match[1] not in names:
            names.append(match[1])
    return names


def _param_props(text: str, declaration: re.Match) -> list:
    """The names a component destructures from its props parameter."""
    rest = text[declaration.end():]
    if declaration.groupdict().get("ref"):
        opened = re.match(r"\s*(?:function\s*\w*\s*)?(?:<[^>(]*>\s*)?\(", rest)
        if not opened:
            return []
        rest = rest[opened.end():]
    opened = re.match(r"\s*\{", rest)
    if not opened:
        return []
    body = _braced(rest, opened.end() - 1)
    if body is None:
        return []
    names = []
    for item in _top_level(_strip_comments(body), ","):
        match = _KEY.match(item)
        if match and not item.startswith("...") and match[1] not in names:
            names.append(match[1])
    return names


def _declarations(text: str) -> list:
    """Every PascalCase component declared in `text`, as regex matches, in
    order."""
    found = []
    for pattern in _DECLARATIONS:
        for match in pattern.finditer(text):
            if any(char.islower() for char in match[1]):
                found.append(match)
    return sorted(found, key=lambda match: match.start())


def _props(text: str, name: str, declaration: re.Match | None) -> frozenset:
    typed = _type_props(text, name)
    if typed is not None:
        return frozenset(typed)
    if declaration is None:
        return frozenset()
    return frozenset(_param_props(text, declaration))


def _added_components(diff: FileDiff) -> list:
    """`(name, props)` for every component declared on an added line,
    read from the file's added and context lines alone."""
    new_side = [(kind, text) for kind, text in diff.lines if kind != "-"]
    text = "\n".join(line for _, line in new_side) + "\n"
    starts, offset = [], 0
    for _, line in new_side:
        starts.append(offset)
        offset += len(line) + 1
    added_starts = {start for start, (kind, _) in zip(starts, new_side) if kind == "+"}
    components, seen = [], set()
    for match in _declarations(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if line_start not in added_starts or match[1] in seen:
            continue
        seen.add(match[1])
        components.append((match[1], _props(text, match[1], match)))
    return components


def _part_props(text: str, name: str) -> frozenset:
    declaration = next((m for m in _declarations(text) if m[1] == name), None)
    return _props(text, name, declaration)


# ---------------------------------------------------------------------------
# The catalog before the diff, and classification
# ---------------------------------------------------------------------------

def _folders(declared) -> list:
    return [folder for layer in design_parts.LAYERS for folder in declared.folders[layer]]


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _part_name(path: str, folders: list) -> str | None:
    """The part name of `path` when it is a part file in a declared folder."""
    pure = PurePosixPath(path)
    parent = str(pure.parent)
    if parent in folders and design_parts._is_part_file(pure):
        return pure.stem
    if (pure.parent.parent.as_posix() in folders and pure.stem == "index"
            and pure.suffix in design_parts.UI_EXTENSIONS):
        return pure.parent.name
    return None


def _similarity(mine: frozenset, theirs: frozenset) -> tuple:
    shared = mine & theirs
    union = mine | theirs
    return (len(shared) / len(union) if union else 0.0), shared


def _nearest(props: frozenset, parts: list) -> tuple | None:
    """`(part, shared)` for the most similar part that qualifies, or None."""
    best = None
    for part, theirs in parts:
        score, shared = _similarity(props, theirs)
        if len(shared) >= MIN_SHARED and score >= MIN_SIMILARITY:
            if best is None or (score, len(shared)) > best[0]:
                best = ((score, len(shared)), part, shared)
    return None if best is None else (best[1], best[2])


def _look_alike(name: str, file: str, part, shared) -> str:
    return (f"uses-the-shared-parts: `{name}` in `{file}` looks like the shared "
            f"part `{part.name}` (shared props: {', '.join(sorted(shared))}) — "
            f"use `{part.ships_at}` and delete the copy.")


def classify(files: list, parts: list, folders: list) -> list:
    """One `Finding` per component, judged against `parts` — the catalog
    before the diff, as `(Part, props)`."""
    findings = []
    for diff in files:
        if diff.deleted or not design_parts.is_ui_file(diff.path):
            continue
        components = _added_components(diff)
        inside = any(_inside(diff.path, folder) for folder in folders)
        new_part = _part_name(diff.path, folders) if diff.added else None
        if new_part is not None and not components:
            text = "\n".join(_side(diff, " +"))
            components = [(new_part, _part_props(text, new_part))]
        for name, props in components:
            if new_part is not None:
                near = _nearest(props, parts)
                if near is not None:
                    findings.append(Finding("new-part", "REQUEST_CHANGES",
                                            _look_alike(name, diff.path, *near)))
                else:
                    findings.append(Finding("new-part", "APPROVE", (
                        f"new-part: `{name}` at `{diff.path}` — no shared part is near "
                        f"it; approve it as provisional with its `{PROVISIONAL_MARK}` "
                        f"line unless rule 4(c) applies.")))
                continue
            if inside:
                continue
            same = next((part for part, _ in parts if part.name == name), None)
            if same is not None:
                findings.append(Finding("copy", "REQUEST_CHANGES", (
                    f"uses-the-shared-parts: `{name}` in `{diff.path}` is a copy of the "
                    f"shared part `{same.name}` — use `{same.ships_at}` and delete "
                    f"the copy.")))
                continue
            near = _nearest(props, parts)
            if near is not None:
                findings.append(Finding("look-alike", "REQUEST_CHANGES",
                                        _look_alike(name, diff.path, *near)))
                continue
            findings.append(Finding("local-component", "NOTE", (
                f"local-component: `{name}` in `{diff.path}` is declared outside every "
                f"design-system folder and is near no shared part — the critic judges "
                f"whether it belongs in the shared parts; never blocking by itself.")))
    return findings


def _catalog_before(root: Path, files: list) -> tuple:
    """`(block lines, [(Part, props)])` for the checkout's catalog with every
    part the diff adds taken out. A part at a path the diff names is never
    opened; its props come from the diff's old side."""
    named = {diff.path: diff for diff in files}
    added = {diff.path for diff in files if diff.added}
    source = design_parts.load(root).source
    parts = []
    for part in design_parts.catalog(root):
        if part.ships_at in added:
            continue
        if part.ships_at in named:
            text = "\n".join(_side(named[part.ships_at], " -"))
        else:
            try:
                text = (root / part.ships_at).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        parts.append((part, _part_props(text, part.name)))
    if not parts:
        return [f"{CATALOG} none — no part ships in the folders read from {source}"], parts
    return ([f"{CATALOG} {len(parts)} part(s) from {source}"]
            + [f"- {p.name} ({p.layer}) — ships at {p.ships_at}" for p, _ in parts]), parts


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return _CONTROL.sub(" ", text)


def _read_diff(path: Path) -> tuple:
    """`(files, None)` or `(None, reason)` for a diff that cannot be used."""
    if not path.is_file():
        return None, f"the diff file {path} is missing"
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"the diff file {path} cannot be read: {exc}"
    if not text.strip():
        return None, f"the diff file {path} is empty"
    try:
        return parse_diff(text), None
    except DiffError as exc:
        return None, f"the diff file {path} cannot be parsed: {exc}"


def review(diff_path, repo_root) -> tuple:
    """`(stdout lines, finding count or None)` for one review."""
    root = Path(repo_root)
    files, why = _read_diff(Path(diff_path))
    if files is None:
        return [f"{REVIEW} UNKNOWN — {_clean(why)}"] + design_parts._catalog_lines(root), None
    ui = [diff.path for diff in files
          if not diff.deleted and design_parts.is_ui_file(diff.path)]
    if not ui:
        extensions = ", ".join(design_parts.UI_EXTENSIONS)
        return [f"{REVIEW} does not apply — the diff adds or changes no UI file "
                f"({extensions})"], None
    try:
        present, reason = design_parts.exists(root)
    except design_parts.Invalid as exc:
        return ([f"{REVIEW} UNKNOWN — the design system's declaration cannot be read: "
                 f"{_clean(str(exc))}"] + design_parts._catalog_lines(root)), None
    if not present:
        return [f"{REVIEW} does not apply — the repo's design system does not exist "
                f"({reason})"], None
    block, parts = _catalog_before(root, files)
    folders = _folders(design_parts.load(root))
    findings = classify(files, parts, folders)
    return ([f"{REVIEW} applies — {len(set(ui))} UI file(s) changed in a repo whose "
             f"design system exists", *block]
            + [f"FINDING {f.kind} (suggested verdict: {f.verdict}) — {f.text}"
               for f in findings]), len(findings)


def provisional_records(text: str) -> list:
    """`Provisional(name, path, reasoning)` for every line in a verdict's
    text that opens with `PROVISIONAL_MARK`."""
    return [Provisional(*match.groups()) for match in _PROVISIONAL.finditer(text)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_review(args) -> int:
    try:
        lines, count = review(args.diff, args.repo_root)
    except Exception as exc:  # the review always answers, and exits 0
        lines, count = [f"{REVIEW} UNKNOWN — {_clean(f'{type(exc).__name__}: {exc}')}"], None
    print("\n".join(lines))
    tail = "" if count is None else f", {count} finding(s)"
    print(f"{TAG} {lines[0][len(REVIEW) + 1:]}{tail}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("review", help="read a diff against the parts catalog")
    command.add_argument("--diff", required=True)
    command.add_argument("--repo-root", default=".")
    command.set_defaults(func=cmd_review)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
