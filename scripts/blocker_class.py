#!/usr/bin/env python3
"""One vocabulary of blocker classes, and the one reader that names a blocker's class (DRE-6438).

A build agent that cannot build its card writes `/tmp/agent-blocker.txt`, the
workflow posts it as a `🛑 Agent blocked:` comment, and until now the sweep
held the card for a person whatever the reason said. `config/blocker-classes.json`
declares the classes a reason can belong to, and this module names a reason's
class, so the poster (DRE-6444) and the sweep (DRE-6448) act on the class
instead of the prose. It writes nothing to Linear and changes no behavior: it
is the contract the siblings read. `docs/blocker-classes.md` is the page a
person reads.

## The contract (identical in every sibling card)

The classes are `nothing-to-change`, `wrong-repo`, `branch-without-pr` and
`question`, the default. Each names the `scripts/<action>.py` that resolves it.

The STAMP is the first line of the agent's note: `blocker-class: <class>`
(`stamp_class`). A stamp naming a word that is not a class is no stamp. For
`wrong-repo` the second line is `repo: <slug>`, and the poster keeps it as the
first line of the quoted reason (`strip_stamp`).

The MARKER is the posted comment:

    🛑 Agent blocked: class=<class> · <reason> — parked in Backlog until … Run: <url>

`marker_class` reads `class=` and is None for a legacy marker without one;
`marker_reason` is the text between the `·` (a legacy marker: the prefix) and
the LAST ` — parked in Backlog`, stripped, so a multi-line reason survives.

`classify(text)`: a valid first-line stamp wins; else the phrases, matched
case-insensitively against the whole text; phrases of more than one class, or
of none, are `question`. It never raises.

`open_blocker(bodies, *, machine_prefixes, resolved_tag)` walks a thread
newest → oldest as `reconcile.has_unresolved_blocker` does. The caller hands
in both keywords: this module holds no tag and no copy of the sweep's prefixes.

`NotNow` is raised by an action module when a fact it needs could not be read
this pass; the resolver (DRE-6508) leaves the marker open for the next sweep.
A person's call is a None return, never `NotNow`.

## The CLI

    python3 scripts/blocker_class.py check [--config PATH]
    python3 scripts/blocker_class.py classify <file>     # the bare class
    python3 scripts/blocker_class.py reason <file>       # the note, stamp line removed
    python3 scripts/blocker_class.py classify-card <CARD>
    python3 scripts/blocker_class.py board               # needs REPO and REPO_SLUG

The library is stdlib-only and import-safe. `classify-card` and `board` are
the two subcommands that import the pipeline's readers, and they import them
inside the subcommand: `reconcile` reads `REPO` when it is imported. Neither
writes anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONFIG = os.path.join("config", "blocker-classes.json")

#: The poster's trailing clause; the reason ends at its LAST occurrence.
PARKED = " — parked in Backlog"

_ACTION = re.compile(r"[a-z_]+")
_MARKER_CLASS = re.compile(r"class=([^\s·]+)\s*·\s*")


class Blocker(NamedTuple):
    cls: str
    reason: str
    body: str


class NotNow(Exception):
    """A fact an action module needs could not be read this pass — a pull
    request state GitHub would not answer, a Linear write that was refused.
    The resolver prints the class and the reason and leaves the marker open
    for the next sweep. Never raised for a person's call: that is a None
    return, and the sweep asks the person."""


# --------------------------------------------------------------------------- #
# the vocabulary                                                               #
# --------------------------------------------------------------------------- #

_loaded: dict = {}


def load(path: str | None = None) -> dict:
    """The vocabulary, from `config/blocker-classes.json` unless `path` names
    another. The shipped file is read once per process."""
    if path is not None:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    if not _loaded:
        with open(os.path.join(ROOT, CONFIG), encoding="utf-8") as handle:
            _loaded.update(json.load(handle))
    return _loaded


def _classes() -> dict:
    return load().get("classes") or {}


def _default() -> str:
    return load().get("default") or "question"


def _stem() -> str:
    """The marker without its colon — `reconcile.BLOCKER_MARKER`."""
    return load()["marker"].rstrip(":")


# --------------------------------------------------------------------------- #
# the note                                                                     #
# --------------------------------------------------------------------------- #


def stamp_class(text: str | None) -> str | None:
    """The class the note's FIRST line stamps, or None — no stamp, or a stamp
    naming a word that is not a class."""
    first = (text or "").split("\n", 1)[0].strip()
    prefix = load()["stamp"].strip()
    if not first.startswith(prefix):
        return None
    word = first[len(prefix):].strip()
    return word if word in _classes() else None


def strip_stamp(text: str | None) -> str:
    """The note with a valid stamp line removed and nothing else changed — a
    `repo:` line after it stays."""
    text = text or ""
    if stamp_class(text) is None:
        return text
    return text.split("\n", 1)[1] if "\n" in text else ""


def classify(text: str | None) -> str:
    """One of the class names, always: the stamp, else the one class whose
    phrases the text names, else the default."""
    try:
        stamped = stamp_class(text)
        if stamped:
            return stamped
        lowered = (text or "").lower()
        named = [name for name, row in _classes().items()
                 if any(phrase and phrase in lowered for phrase in row.get("phrases") or ())]
        return named[0] if len(named) == 1 else _default()
    except Exception:  # noqa: BLE001 — classify never raises; a person decides
        return "question"


# --------------------------------------------------------------------------- #
# the marker                                                                   #
# --------------------------------------------------------------------------- #


def _after_prefix(body: str) -> str | None:
    """The marker's text after its prefix, or None when `body` is no marker."""
    text = (body or "").lstrip()
    for prefix in (load()["marker"], _stem()):
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
    return None


def marker_class(body: str | None) -> str | None:
    """The class a marker carries as `class=<class> · `, or None — a legacy
    marker, or a word that is not a class."""
    rest = _after_prefix(body or "")
    match = _MARKER_CLASS.match(rest or "")
    if not match:
        return None
    return match.group(1) if match.group(1) in _classes() else None


def marker_reason(body: str | None) -> str:
    """The reason a marker quotes: after the `·` (a legacy marker: after the
    prefix), up to the LAST ` — parked in Backlog`, stripped."""
    rest = _after_prefix(body or "")
    if rest is None:
        return ""
    match = _MARKER_CLASS.match(rest)
    if match:
        rest = rest[match.end():]
    cut = rest.rfind(PARKED)
    return (rest[:cut] if cut >= 0 else rest).strip()


def _blocker(body: str) -> Blocker:
    reason = marker_reason(body)
    return Blocker(marker_class(body) or classify(reason), reason, body)


def is_marker(body: str | None) -> bool:
    return (body or "").lstrip().startswith(_stem())


def newest_marker(bodies) -> Blocker | None:
    """The newest marker in `bodies` (oldest→newest), open or not."""
    for body in reversed(list(bodies or ())):
        if is_marker(body):
            return _blocker(body)
    return None


def open_blocker(bodies, *, machine_prefixes, resolved_tag) -> Blocker | None:
    """The card's open blocker, walking `bodies` (oldest→newest) newest first:
    a comment carrying `resolved_tag` resolves it, the first marker met is
    open, and a comment not opening with one of `machine_prefixes` is a
    person's reply and resolves it."""
    prefixes = tuple(machine_prefixes)
    for body in reversed(list(bodies or ())):
        body = body or ""
        if resolved_tag and resolved_tag in body:
            return None
        if is_marker(body):
            return _blocker(body)
        if not body.lstrip().startswith(prefixes):
            return None
    return None


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def problems(doc: dict) -> list:
    """Everything wrong with the vocabulary against the contract."""
    found = []
    classes = doc.get("classes") or {}
    default = doc.get("default")
    if default not in classes:
        found.append(f"default {default!r} is not a class")
    owner: dict = {}
    for name, row in classes.items():
        row = row or {}
        if not str(row.get("means") or "").strip():
            found.append(f"{name} has no means")
        phrases = row.get("phrases") or []
        if name != default and not phrases:
            found.append(f"{name} has no phrase — only the default may name none")
        for phrase in phrases:
            if phrase != phrase.lower() or not phrase.strip():
                found.append(f"{name}: the phrase {phrase!r} is not a lower-case substring")
            if phrase in owner and owner[phrase] != name:
                found.append(f"the phrase {phrase!r} is under two classes: "
                             f"{owner[phrase]} and {name}")
            owner.setdefault(phrase, name)
        action = row.get("action")
        if not action:
            found.append(f"{name} has no action")
        elif not isinstance(action, str) or not _ACTION.fullmatch(action):
            found.append(f"{name}: the action {action!r} is not a [a-z_]+ module name")
    return found


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _classify_card(card: str) -> int:
    import linear_ops  # noqa: PLC0415 — the library stays stdlib-only

    # The whole thread: the marker may be older than the fifty newest comments.
    found = newest_marker(linear_ops.comment_bodies(card, whole_thread=True))
    print(f"{card} class={found.cls}" if found else f"{card} no blocker")
    return 0


def _board() -> int:
    if not os.environ.get("REPO") or not os.environ.get("REPO_SLUG"):
        print("board needs REPO and REPO_SLUG", file=sys.stderr)
        return 2
    import linear_ops  # noqa: PLC0415 — the library stays stdlib-only
    import reconcile  # noqa: PLC0415 — reads REPO at import

    for card in reconcile.backlog_children(from_linear=True):
        if reconcile.card_repo(card) != reconcile.REPO_SLUG:
            continue
        # The sweep's own predicate decides "open" — this command holds no tag.
        if not reconcile.has_unresolved_blocker(card):
            continue
        bodies = [node.get("body") or ""
                  for node in linear_ops.window_nodes(card.get("comments"))]
        found = newest_marker(bodies)
        if found is None:
            print(f"{card['identifier']} no blocker")
            continue
        print(f"{card['identifier']} class={found.cls} · {_one_line(found.reason)[:80]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--config", default=os.path.join(ROOT, CONFIG))
    sub.add_parser("classify").add_argument("file")
    sub.add_parser("reason").add_argument("file")
    sub.add_parser("classify-card").add_argument("card")
    sub.add_parser("board")
    args = parser.parse_args(argv)

    if args.command == "check":
        doc = load(args.config)
        found = problems(doc)
        for problem in found:
            print(f"  [FAIL] {problem}")
        print(f"{len(doc.get('classes') or {})} class(es), {len(found)} problem(s)")
        return 1 if found else 0
    if args.command == "classify":
        print(classify(_read(args.file)))
        return 0
    if args.command == "reason":
        sys.stdout.write(strip_stamp(_read(args.file)))
        return 0
    if args.command == "classify-card":
        return _classify_card(args.card)
    return _board()


if __name__ == "__main__":
    sys.exit(main())
