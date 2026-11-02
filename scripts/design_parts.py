#!/usr/bin/env python3
"""Whether a repo's design system exists, and which parts it ships (DRE-8717).

A repo names the folders each layer of its design system lives in, in
`.github/bureau/design-system.json`, beside `proof-local.json` and
`release.json`; this module is the file's one reader.
`docs/design-system-declaration.md` is the human contract.

Both answers are read from the checkout's files at run time, never from a
stored list. The design system exists when `design/LOCK.json` is present, or
when the declaration names at least one folder that is present. A part is a
UI file directly inside a declared folder, or a direct subfolder holding an
`index` UI file. With a lock and no declaration, the folders are the
`design/<layer>` ones `standards/design-system.md` fixes.

It runs on the runner's own `python3`, like every other
`.bureau-pipeline/scripts/…` step, so it imports the standard library and
nothing pip-installed. DRE-8719 (the critic's parts review) imports `load`,
`catalog`, `UI_EXTENSIONS` and `TAG`.

CLI:
    design_parts.py check   [--repo-root DIR]
    design_parts.py catalog [--repo-root DIR]
    design_parts.py exists  [--repo-root DIR]

`check` exits 0 for `ok` and `none`, 1 for `invalid`. `catalog` always exits
0 and prints exactly one `design-parts:` line to stderr. `exists` exits 0 for
`yes` and `no`, 1 when the declaration cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import NamedTuple

#: Opens every line `check` prints, and the one stderr line of `catalog`.
TAG = "design-parts:"

DECLARATION = ".github/bureau/design-system.json"
LOCK = "design/LOCK.json"
LAYERS = ("atoms", "molecules", "organisms", "templates")
#: What makes a file a UI file — and, inside a declared folder, a part.
UI_EXTENSIONS = (".tsx", ".jsx", ".vue", ".svelte")
#: A file whose name carries one of these is a test or a story, never a part.
NOT_PARTS = (".test.", ".spec.", ".stories.")

CATALOG = "PARTS CATALOG:"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Invalid(ValueError):
    """The declaration is wrong or unreadable; the message names the problem."""


class Declared(NamedTuple):
    #: layer → the repo-relative folders it lives in, in declared order.
    folders: dict
    #: The file the folders were read from: DECLARATION or LOCK.
    source: str


class Part(NamedTuple):
    name: str
    layer: str
    ships_at: str


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def _unknown(data: dict, known, where: str, label: str) -> None:
    extra = sorted(set(data) - set(known))
    if extra:
        raise Invalid(f"{where} carries unknown key(s) {', '.join(extra)} "
                      f"({label} {', '.join(known)})")


def _folder(value, at: str) -> str:
    """One declared folder, normalized, or `Invalid` naming what is wrong."""
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"`{at}` must be a non-empty folder path")
    if _CONTROL.search(value):
        raise Invalid(f"`{at}` must be one line with no control characters")
    if "\\" in value:
        raise Invalid(f"`{at}` {value} carries a `\\` — separate folders with `/`")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise Invalid(f"`{at}` {value} is an absolute path — name a folder "
                      f"relative to the repo root")
    if ".." in path.parts:
        raise Invalid(f"`{at}` {value} has a `..` segment — name a folder "
                      f"inside the repo")
    return path.as_posix()


def validate(data) -> dict:
    """`data` (the parsed declaration) as layer → tuple of folders, or
    `Invalid` naming the first thing wrong."""
    if not isinstance(data, dict):
        raise Invalid(f"{DECLARATION} must hold one JSON object")
    _unknown(data, ("parts",), DECLARATION, "the one key is")
    if "parts" not in data:
        raise Invalid(f"{DECLARATION} has no `parts` key")
    parts = data["parts"]
    if not isinstance(parts, dict):
        raise Invalid("`parts` must be an object of " + ", ".join(LAYERS))
    _unknown(parts, LAYERS, "`parts`", "the keys are")
    missing = [layer for layer in LAYERS if layer not in parts]
    if missing:
        raise Invalid(f"`parts` is missing layer key(s) {', '.join(missing)} "
                      f"(every one of {', '.join(LAYERS)} is required, and a "
                      f"list may be empty)")
    folders = {}
    for layer in LAYERS:
        listed = parts[layer]
        if not isinstance(listed, list):
            raise Invalid(f"`parts.{layer}` must be a list of folder paths")
        folders[layer] = tuple(_folder(value, f"parts.{layer}[{i}]")
                               for i, value in enumerate(listed))
    return folders


def load(repo_root) -> Declared | None:
    """The folders per layer, and the file they were read from.

    The declaration when the repo carries one; the `design/<layer>` folders
    when it carries `design/LOCK.json` instead; None when it carries neither.
    A declaration that cannot be read or is wrong raises `Invalid`.
    """
    root = Path(repo_root)
    path = root / DECLARATION
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise Invalid(f"{DECLARATION} cannot be read: {exc}") from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise Invalid(f"{DECLARATION} is not valid JSON: {exc}") from None
        return Declared(validate(data), DECLARATION)
    if (root / LOCK).is_file():
        return Declared({layer: (f"design/{layer}",) for layer in LAYERS}, LOCK)
    return None


# ---------------------------------------------------------------------------
# Exists, and the catalog
# ---------------------------------------------------------------------------

def _present(root: Path, declared: Declared) -> list:
    return [folder for layer in LAYERS for folder in declared.folders[layer]
            if (root / folder).is_dir()]


def exists(repo_root) -> tuple:
    """`(True, source)` or `(False, reason)`; `Invalid` when the declaration
    cannot be read."""
    root = Path(repo_root)
    declared = load(root)
    if declared is not None and declared.source == DECLARATION and _present(root, declared):
        return True, DECLARATION
    if (root / LOCK).is_file():
        return True, LOCK
    if declared is None:
        return False, f"no {DECLARATION} and no {LOCK}"
    if not any(declared.folders.values()):
        return False, f"{DECLARATION} names no folder"
    return False, f"no folder {DECLARATION} names is in the checkout"


def _is_part_file(path: Path) -> bool:
    name = path.name
    return (path.suffix in UI_EXTENSIONS and not name.startswith("index.")
            and not any(mark in name for mark in NOT_PARTS))


def _index(folder: Path) -> Path | None:
    for ext in UI_EXTENSIONS:
        candidate = folder / f"index{ext}"
        if candidate.is_file():
            return candidate
    return None


def _parts_in(root: Path, folder: str, layer: str) -> list:
    found = []
    for entry in (root / folder).iterdir():
        if entry.is_file() and _is_part_file(entry):
            found.append(Part(entry.stem, layer, str(PurePosixPath(folder) / entry.name)))
        elif entry.is_dir():
            index = _index(entry)
            if index is not None:
                found.append(Part(entry.name, layer, str(
                    PurePosixPath(folder) / entry.name / index.name)))
    return sorted(found, key=lambda part: (part.name.casefold(), part.ships_at))


def catalog(repo_root) -> list:
    """Every part the checkout ships, as `Part(name, layer, ships_at)`, in
    layer order, then declared folder order, then by name. Empty when the
    design system does not exist; `Invalid` when the declaration cannot be
    read."""
    root = Path(repo_root)
    declared = load(root)
    if declared is None:
        return []
    parts = []
    for layer in LAYERS:
        for folder in declared.folders[layer]:
            if (root / folder).is_dir():
                parts.extend(_parts_in(root, folder, layer))
    return parts


def is_ui_file(path) -> bool:
    """Whether `path` is a UI file — one of `UI_EXTENSIONS`."""
    return PurePosixPath(str(path)).suffix in UI_EXTENSIONS


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _catalog_lines(repo_root) -> list:
    """The catalog block: its first line, then one line per part."""
    try:
        present, why = exists(repo_root)
        if not present:
            return [f"{CATALOG} none — {why}"]
        parts = catalog(repo_root)
        source = load(repo_root).source
    except Exception as exc:  # the catalog always answers, and exits 0
        problem = str(exc) if isinstance(exc, Invalid) else f"{type(exc).__name__}: {exc}"
        return [f"{CATALOG} UNKNOWN — {_CONTROL.sub(' ', problem)}"]
    if not parts:
        return [f"{CATALOG} none — no part ships in the folders read from {source}"]
    return ([f"{CATALOG} {len(parts)} part(s) from {source}"]
            + [f"- {p.name} ({p.layer}) — ships at {p.ships_at}" for p in parts])


def cmd_catalog(args) -> int:
    lines = _catalog_lines(args.repo_root)
    print("\n".join(lines))
    print(f"{TAG} {lines[0][len(CATALOG) + 1:]}", file=sys.stderr)
    return 0


def cmd_check(args) -> int:
    try:
        declared = load(args.repo_root)
    except Invalid as exc:
        print(f"{TAG} invalid — {exc}")
        return 1
    if declared is None or declared.source != DECLARATION:
        print(f"{TAG} none — no {DECLARATION}")
        return 0
    count = sum(len(folders) for folders in declared.folders.values())
    print(f"{TAG} ok — {DECLARATION} declares {count} folder(s)")
    return 0


def cmd_exists(args) -> int:
    try:
        present, why = exists(args.repo_root)
    except Invalid as exc:
        print(f"unknown — {exc}")
        return 1
    print(f"{'yes' if present else 'no'} — {why}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
            ("check", cmd_check, "validate the declaration"),
            ("catalog", cmd_catalog, "print the parts catalog"),
            ("exists", cmd_exists, "say whether the design system exists")):
        command = sub.add_parser(name, help=text)
        command.add_argument("--repo-root", default=".")
        command.set_defaults(func=func)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
