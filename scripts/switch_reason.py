#!/usr/bin/env python3
"""One catalog of the pipeline's switches, and one reader of a switch's off-reason (DRE-6434).

A pipeline switch is a repository variable a reusable workflow reads from
`vars.*` and treats as live only when it is exactly `true`. Before this card a
switch that was off carried no reason, so nothing could say when the reason
was over. `config/switches.json` is the list of switches as data, and this
module reads a switch's declared off-reason and composes the one line every
reader prints. It posts nothing, reads no Linear and edits no workflow: the
sweep step, the receipt and the alarm are the sibling cards, and they import
this.

## The contract (identical in every sibling card)

The companion variable of a switch `<SWITCH>` is `<SWITCH>_OFF_UNTIL`
(`companion`). A switch is on when its own variable is exactly `true`
(`is_on`); every other value, absent included, is off. The companion's value
names the cards the switch waits on — `DRE-<n>` ids separated by commas or
spaces, free text around them kept verbatim (`parse_reason`).

`off_line(name, env)` is exactly one of:

    <SWITCH> is on
    <SWITCH> is on — its off-reason is stale, delete <SWITCH>_OFF_UNTIL
    <SWITCH> is off — no reason given
    <SWITCH> is off — until DRE-A, DRE-B land
    <SWITCH> is off — its reason names no card: <text>

`reading(name, env, states, now)` adds the cards' Linear states: a reason is
cleared only when every named card is in `prose_blockers.TERMINAL`, and a card
with no state is `unread`, never terminal.

## The check

    python3 scripts/switch_reason.py check [--root DIR]

holds the catalog to the tree at DIR (this repository by default). The reads
are DISCOVERED, never listed: every `vars.<NAME>_LIVE` under
`.github/workflows/`, outside a YAML comment, attributed to the step whose
`name` encloses it. A read with no row fails by its name, and a row whose
`reader` does not read `vars.<name>` inside its `step` fails by the row's.

Import-safe and pure: no I/O at import, and every function but `load`,
`discover` and `problems` reads only its arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Mapping, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prose_blockers  # noqa: E402 — import-safe: no I/O at import

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CATALOG = os.path.join("config", "switches.json")
WORKFLOWS = os.path.join(".github", "workflows")

# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #

COMPANION_SUFFIX = "_OFF_UNTIL"
ON_VALUE = "true"
UNREAD = "unread"
CATALOG_VERSION = 1
ROW_FIELDS = ("name", "reader", "step", "means")

#: A card id the companion may name. Bounded both sides, so `XDRE-12` and
#: `DRE-12a` are free text rather than a card.
_CARD = re.compile(r"(?<![A-Za-z0-9-])DRE-\d+(?![A-Za-z0-9])")


class Reason(NamedTuple):
    text: str
    cards: tuple


class Reading(NamedTuple):
    line: str
    off: bool
    cleared: bool
    cards: tuple
    unread: tuple


def companion(name: str) -> str:
    """The variable that carries `name`'s off-reason."""
    return f"{name}{COMPANION_SUFFIX}"


def is_on(name: str, env: Mapping) -> bool:
    """True only when the switch's own variable is exactly `true`."""
    return env.get(name) == ON_VALUE


def parse_reason(value: str | None) -> Reason | None:
    """The companion's text and the card ids it names, in the order written,
    duplicates dropped; None for an empty or unset value."""
    text = (value or "").strip()
    if not text:
        return None
    return Reason(text, tuple(dict.fromkeys(_CARD.findall(text))))


def off_line(name: str, env: Mapping) -> str:
    """The line for `name` with no card states — one of the five contract forms."""
    reason = parse_reason(env.get(companion(name)))
    if is_on(name, env):
        if reason is None:
            return f"{name} is on"
        return f"{name} is on — its off-reason is stale, delete {companion(name)}"
    if reason is None:
        return f"{name} is off — no reason given"
    if not reason.cards:
        return f"{name} is off — its reason names no card: {reason.text}"
    return f"{name} is off — until {', '.join(reason.cards)} land"


def reading(name: str, env: Mapping, states: Mapping | None, now) -> Reading:
    """The line for `name` with each named card's Linear state.

    `states` maps a card id to its state name; a missing id, or one mapped to
    nothing, is unread. `now` is the moment every reader of one pass shares;
    the line itself carries no time.
    """
    states = states or {}
    line = off_line(name, env)
    reason = parse_reason(env.get(companion(name)))
    cards = reason.cards if reason else ()
    unread = tuple(card for card in cards if not states.get(card))
    off = not is_on(name, env)
    if not off or not cards:
        return Reading(line, off, False, cards, unread)
    named = ", ".join(f"{card} {states.get(card) or UNREAD}" for card in cards)
    cleared = not unread and all(states[card] in prose_blockers.TERMINAL
                                 for card in cards)
    if cleared:
        line = (f"{name} is off — its reason cleared: {named}; "
                "the switch may be turned on")
    else:
        line = f"{line}: {named}"
    return Reading(line, off, cleared, cards, unread)


# --------------------------------------------------------------------------- #
# the catalog                                                                  #
# --------------------------------------------------------------------------- #


def load(path: str | None = None) -> dict:
    """The catalog, from `config/switches.json` unless `path` names another."""
    with open(path or os.path.join(ROOT, CATALOG), encoding="utf-8") as handle:
        return json.load(handle)


class Read(NamedTuple):
    name: str
    file: str
    line: int
    step: str | None


_READ = re.compile(r"\bvars\.([A-Z][A-Z0-9_]*_LIVE)\b")
_ITEM = re.compile(r"^(?P<indent>\s*)-\s")
_STEP_NAME = re.compile(r"^(?P<indent>\s*)-\s+name:\s*(?P<name>\S.*?)\s*$")
_NAME_KEY = re.compile(r"^(?P<indent>\s*)name:\s*(?P<name>\S.*?)\s*$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _step_name(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].rstrip()


def _reads(rel: str, text: str) -> list:
    """Every switch read in one workflow's text, each with its enclosing step.

    A step opens at a list item; its `name` is the item's own `- name:` or a
    `name:` key two columns in. It closes at the next item at its column or at
    any line left of that column — a job key, the next job.
    """
    found = []
    step, column = None, None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent(line)
        item = _ITEM.match(line)
        if column is not None and (indent < column or (item and indent == column)):
            step, column = None, None
        if item and column is None:
            column = indent
            named = _STEP_NAME.match(line)
            step = _step_name(named.group("name")) if named else None
        elif column is not None and step is None:
            key = _NAME_KEY.match(line)
            if key and len(key.group("indent")) == column + 2:
                step = _step_name(key.group("name"))
        code = line.split(" #", 1)[0]
        for name in _READ.findall(code):
            found.append(Read(name, rel, number, step))
    return found


def discover(root: str | None = None) -> list:
    """Every `vars.<NAME>_LIVE` read under `<root>/.github/workflows/`."""
    base = Path(root or ROOT)
    found = []
    for path in sorted((base / WORKFLOWS).rglob("*")):
        if path.suffix not in (".yml", ".yaml") or not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        found += _reads(rel, path.read_text(encoding="utf-8"))
    return found


def _header_problems(doc: dict) -> list:
    found = []
    if not str(doc.get("_readme") or "").strip():
        found.append("the catalog carries no _readme")
    if doc.get("version") != CATALOG_VERSION:
        found.append(f"the catalog's version is {doc.get('version')!r}, "
                     f"not {CATALOG_VERSION}")
    if doc.get("companion_suffix") != COMPANION_SUFFIX:
        found.append(f"the catalog's companion_suffix is "
                     f"{doc.get('companion_suffix')!r}; the contract fixes "
                     f"{COMPANION_SUFFIX!r}")
    hours = doc.get("alarm_after_hours")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
        found.append(f"the catalog's alarm_after_hours is {hours!r}, "
                     "not a positive number of hours")
    return found


def problems(doc: dict | None = None, root: str | None = None) -> list:
    """Everything wrong with the catalog against the tree at `root`."""
    root = root or ROOT
    doc = doc if doc is not None else load(os.path.join(root, CATALOG))
    found = _header_problems(doc)
    reads = discover(root)
    rows = doc.get("switches") or []
    names = [row.get("name") for row in rows]
    for index, row in enumerate(rows):
        name = row.get("name") or f"row {index}"
        missing = [field for field in ROW_FIELDS if not str(row.get(field) or "").strip()]
        if missing:
            found.append(f"{name} names no {', no '.join(missing)}")
        if row.get("name") and names.count(row["name"]) > 1 and names.index(row["name"]) == index:
            found.append(f"{name} has more than one row in {CATALOG}")
        if missing:
            continue
        if not any(r.name == row["name"] and r.file == row["reader"]
                   and r.step == row["step"] for r in reads):
            found.append(f"{name}: {row['reader']} reads no vars.{row['name']} "
                         f"inside the step {row['step']!r} — a row whose read "
                         "is gone has outlived its switch, and a moved read "
                         "moves its row")
    for read in reads:
        if read.name not in names:
            step = repr(read.step) if read.step else "outside any step"
            found.append(f"{read.name} is read at {read.file}:{read.line} "
                         f"(step {step}) and has no row in {CATALOG} — add one: "
                         "its reader, its step and what it means")
    return found


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--root", default=ROOT)
    args = parser.parse_args(argv)

    doc = load(os.path.join(args.root, CATALOG))
    found = problems(doc, root=args.root)
    for problem in found:
        print(f"  [FAIL] {problem}")
    print(f"{len(discover(args.root))} switch read(s), "
          f"{len(doc.get('switches') or [])} row(s), {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
