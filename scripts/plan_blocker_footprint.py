#!/usr/bin/env python3
"""A plan that chains two sibling cards whose files never meet is sent back,
unless the card's Blocked by line says why (DRE-8688).

`briefs/planner.md` already states the rule: disjoint footprints run in
parallel, and where two footprints intersect the cards are not parallel. The
second half was checked (`plan_footprint.collisions`, DRE-3040) and the first
never was. On 2026-11-01 epic DRE-8541's last build cards were planned as a
strict chain, DRE-8553 then DRE-8554 then DRE-8555, though the only file they
shared was a count ledger each card edits its own entries of. DRE-8554's
entries sit apart from DRE-8553's, so their pull requests would have merged
cleanly, and DRE-8554 waited about 22 hours anyway. This module asks the
reverse question at plan time: do two cards that block each other share
anything?

It is STRUCTURAL, in `plan_ceo_steps.py`'s shape: one line per refused edge,
appended to the structural file the seam gate forwards into the first critic's
result, so the round is a `SEND_BACK` whatever the model wrote. A plan with no
refused edge appends nothing.

## The edges it reads

Direct `blockedBy` relations between two children of this epic, both open
(`plan_critic.shipped_work_is_a_finding`), both build cards that are not
observation cards (`plan_seam.build_cards` minus `plan_seam.observation_cards`,
so a PROOF, demo, `[OPERATOR]` or `needs-human` + `no-code` card is never an
end), both labeled `repo:<caller>`, and both declaring a `Files:` section. Every
other edge is skipped and counted in the note by kind.

## A justified edge

1. The two `Files:` lines share a file that is not a declared ledger.
2. They share only declared ledgers, and in one of them their lines TOUCH —
   some line of one is within 1 of some line of the other. Git conflicts on
   hunks that touch and merges cleanly when one unchanged line lies between.
3. The blocked card's declaring line, the one naming this blocker, states a
   reason: `because` after the last card id, then three words or more and no
   card id. The critic judges what the reason says; the note quotes it.

A ledger is declared per repo in `config/ledger-files.json` and read from the
plan run's checkout of the product repo (the working directory). A card touches
the line of each map key one of its declared files ends with (`/` + key), and
the line above the map's LAST key too, because deleting that entry edits the
line above's trailing comma. Every rule here errs toward allowing an order:
extra lines can only make two cards look adjacent, and a ledger that is absent
or does not parse, or a card whose files map to no key, allows the order and
the note says so.

CLI:
  findings --detail-file D --caller SLUG [--structural-file S] [--note-file N]
      `children-json` records on stdin, `children-detail` records in D.
      Appends one `needless-blocker:` line per refused edge to S (never
      truncates it), and one `Blockers:` line, with its details under it, to
      N. Inputs it cannot read are `Blockers: UNKNOWN` and no line. Always
      exits 0.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blocker_prose  # noqa: E402
import plan_critic  # noqa: E402
import plan_footprint  # noqa: E402
import plan_seam  # noqa: E402

#: What each structural line opens with, so the epic's note says which check.
FINDING_PREFIX = "needless-blocker:"

#: What the note line opens with.
NOTE_PREFIX = "Blockers:"

#: The declared ledgers: repo slug → path globs.
LEDGER_FILES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, "config", "ledger-files.json")

#: The skip kinds, in the order the note counts them.
OUTSIDE, DELIVERED, NOT_BUILD, OTHER_REPO, NO_FILES = (
    "outside the epic", "delivered", "not a build card", "in another repo",
    "with no Files: section")
SKIPS = (OUTSIDE, DELIVERED, NOT_BUILD, OTHER_REPO, NO_FILES)

#: How an edge was justified, or refused.
SHARED, TOUCHING, UNREAD, REASON, REFUSED = (
    "shared", "touching", "unread", "reason", "refused")

_KEY = re.compile(r'"(?:[^"\\]|\\.)*"\s*:')
_BECAUSE = re.compile(r"\bbecause\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9][\w'’-]*")


def _collapse(text: str) -> str:
    return " ".join(str(text).split())


# --- The declared ledgers ---------------------------------------------------

def declared_ledgers(path: str | None = None) -> dict:
    """`config/ledger-files.json`, slug → globs. Raises on a file that is not
    that shape: the CLI reads it as UNKNOWN."""
    with open(path or LEDGER_FILES, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not all(
            isinstance(v, list) and all(isinstance(g, str) for g in v)
            for v in doc.values()):
        raise ValueError(f"{path or LEDGER_FILES} is not a map of slug to globs")
    return doc


def is_ledger(path: str, caller: str, ledgers: dict | None = None) -> bool:
    globs = (declared_ledgers() if ledgers is None else ledgers).get(caller) or []
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


class Ledger:
    """One ledger file read from `root`: each top-level key's line, or why it
    could not be read."""

    def __init__(self, path: str, root: str):
        self.path, self.lines, self.last, self.unread = path, {}, None, ""
        try:
            with open(os.path.join(root, path), encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            self.unread = "absent from the checkout"
            return
        except (OSError, UnicodeDecodeError) as exc:
            self.unread = f"not readable ({exc})"
            return
        try:
            doc = json.loads(text)
        except ValueError as exc:
            self.unread = f"not valid JSON ({exc})"
            return
        if not isinstance(doc, dict):
            self.unread = "not a JSON map"
            return
        for m in _KEY.finditer(text):
            key = json.loads(m.group(0).rstrip()[:-1].rstrip())
            if key in doc and key not in self.lines:
                self.lines[key] = text.count("\n", 0, m.start()) + 1
        if self.lines:
            self.last = max(self.lines, key=self.lines.get)

    def touched(self, files) -> list[int]:
        """The lines a card declaring `files` edits, sorted."""
        out = set()
        for key, line in self.lines.items():
            if any(f == key or f.endswith("/" + key) for f in files):
                out.add(line)
                if key == self.last and line > 1:
                    out.add(line - 1)
        return sorted(out)


def _touch(a: list[int], b: list[int]) -> bool:
    return any(abs(x - y) <= 1 for x in a for y in b)


# --- The stated reason ------------------------------------------------------

def stated_reason(body: str, blocker: str) -> str | None:
    """The reason the declaring line naming `blocker` states, or None.

    `because` after the line's last card id, followed by at least three words
    and no card id — read on the line's text (`blocker_prose.line_text`), so a
    link target never counts."""
    for line in (body or "").splitlines():
        if not blocker_prose.BLOCKER_LINE.search(line):
            continue
        text = blocker_prose.line_text(line)
        refs = list(blocker_prose.CARD_REF.finditer(text))
        if blocker.upper() not in {r.group(0).upper() for r in refs}:
            continue
        tail = text[refs[-1].end():]
        m = _BECAUSE.search(tail)
        if not m:
            continue
        reason = tail[m.end():]
        if blocker_prose.CARD_REF.search(reason) or len(_WORD.findall(reason)) < 3:
            continue
        return _collapse(reason).strip(" *_`~.")
    return None


# --- The reading ------------------------------------------------------------

def _lines(numbers: list[int]) -> str:
    return f"lines {', '.join(str(n) for n in numbers)}" if numbers else "no line"


class Reading:
    """Every edge of the plan, classified."""

    def __init__(self, cards, details, caller: str, root: str,
                 ledgers: dict | None = None):
        self.caller, self.root = caller, root
        self.ledgers = declared_ledgers() if ledgers is None else ledgers
        self._read: dict[str, Ledger] = {}
        records = plan_seam.join(cards, details)
        self.by_id = {r["identifier"]: r for r in records}
        observation = set(plan_seam.observation_cards(records))
        self.build = {i for i in plan_seam.build_cards(records) if i not in observation}
        self.files = plan_footprint.footprints(records)
        self.edges: list[tuple[str, str]] = [
            (r["identifier"], b) for r in records for b in r["blocked_by"]]
        self.counts = {kind: 0 for kind in SKIPS + (SHARED, TOUCHING, UNREAD,
                                                    REASON, REFUSED)}
        self.reasons: list[tuple[str, str, str]] = []
        self.unread: list[str] = []
        self.findings: list[str] = []
        for blocked, blocker in self.edges:
            kind = self._classify(blocked, blocker)
            self.counts[kind] += 1

    # The skip, or None when both ends are read.
    def _skip(self, blocked: str, blocker: str) -> str | None:
        ends = [self.by_id.get(blocked), self.by_id.get(blocker)]
        if ends[1] is None:
            return OUTSIDE
        if not all(plan_critic.shipped_work_is_a_finding(r) for r in ends):
            return DELIVERED
        if not (blocked in self.build and blocker in self.build):
            return NOT_BUILD
        label = f"repo:{self.caller}".lower()
        if not all(label in {str(l).strip().lower() for l in r["labels"]} for r in ends):
            return OTHER_REPO
        if blocked not in self.files or blocker not in self.files:
            return NO_FILES
        return None

    def _ledger(self, path: str) -> Ledger:
        if path not in self._read:
            self._read[path] = Ledger(path, self.root)
        return self._read[path]

    def _readable(self, ident: str) -> bool:
        rec = self.by_id.get(ident)
        return bool(rec and ident in self.build and ident in self.files
                    and plan_critic.shipped_work_is_a_finding(rec)
                    and f"repo:{self.caller}".lower()
                    in {str(l).strip().lower() for l in rec["labels"]})

    def _files_say(self, a: str, b: str):
        """`(kind, detail)` for what the two footprints say about an order:
        SHARED, TOUCHING or UNREAD with a note line, or `(None, apart)` where
        `apart` lists each shared ledger's lines for the finding."""
        shared = self.files[a] & self.files[b]
        if any(not is_ledger(p, self.caller, self.ledgers) for p in shared):
            return SHARED, None
        apart, unread = [], []
        for path in sorted(shared):
            ledger = self._ledger(path)
            if ledger.unread:
                unread.append(f"the declared ledger {path} could not be read "
                              f"({ledger.unread})")
                continue
            la, lb = ledger.touched(self.files[a]), ledger.touched(self.files[b])
            if _touch(la, lb):
                return TOUCHING, None
            empty = [i for i, lines in ((a, la), (b, lb)) if not lines]
            if empty:
                unread.append(f"in the declared ledger {path}, "
                              f"{' and '.join(empty)} declare(s) files that map to no key")
                continue
            apart.append(f"{path} ({a}: {_lines(la)}; {b}: {_lines(lb)})")
        if unread:
            return UNREAD, "; ".join(unread)
        return None, apart

    def _classify(self, blocked: str, blocker: str) -> str:
        skip = self._skip(blocked, blocker)
        if skip:
            return skip
        reason = stated_reason(self.by_id[blocked]["body"], blocker)
        if reason:
            self.reasons.append((blocked, blocker, reason))
        kind, detail = self._files_say(blocked, blocker)
        if kind == UNREAD:
            self.unread.append(f"{blocked} is blocked by {blocker}: {detail} — "
                               "the order is allowed")
        if kind:
            return kind
        if reason:
            return REASON
        self.findings.append(self._finding(blocked, blocker, detail))
        return REFUSED

    def _sibling(self, blocked: str, blocker: str) -> list[str]:
        """The siblings `blocked` shares a file or touching ledger lines with."""
        out = []
        for other in self.by_id:
            if other in (blocked, blocker) or not self._readable(other) \
                    or not self._readable(blocked):
                continue
            kind, _ = self._files_say(blocked, other)
            if kind in (SHARED, TOUCHING):
                out.append(other)
        return out

    def _finding(self, blocked: str, blocker: str, apart: list[str]) -> str:
        if apart:
            what = (f"the two share only the declared ledger {'; '.join(apart)}, "
                    "where their lines are apart")
        else:
            what = "the two share no file"
        siblings = self._sibling(blocked, blocker)
        hint = (f" {blocked} does share a file or touching ledger lines with "
                f"{', '.join(siblings)} — block on that sibling if the order is real."
                if siblings else "")
        return _collapse(
            f"{FINDING_PREFIX} {blocked} is blocked by {blocker}, and {what}, so "
            f"nothing in their files needs the order.{hint} Fix: drop the relation, "
            f"or add `because <the non-file dependency>` to {blocked}'s Blocked by line.")

    def note(self) -> list[str]:
        c = self.counts
        head = (f"{NOTE_PREFIX} {len(self.edges)} edge(s) read — "
                f"{c[SHARED]} justified by a shared file, {c[TOUCHING]} by touching "
                f"ledger lines, {c[REASON]} by a stated reason, {c[UNREAD]} allowed "
                f"on a ledger that could not be read, {c[REFUSED]} refused; skipped: "
                + ", ".join(f"{c[k]} {k}" for k in SKIPS) + ".")
        globs = self.ledgers.get(self.caller) or []
        if globs:
            head += (f" Declared ledgers ({', '.join(globs)}) are shared by design: an "
                     "order between two cards that share only a ledger is not "
                     "required where their lines are apart.")
        out = [head]
        out += [f"stated reason, {a} blocked by {b}: “{r}”" for a, b, r in self.reasons]
        out += self.unread
        out += self.findings
        return out


def unknown(why: str) -> list[str]:
    return [f"{NOTE_PREFIX} UNKNOWN — {why}; no edge is refused"]


# --- CLI --------------------------------------------------------------------

def _append(path: str | None, lines: list[str], what: str) -> None:
    """Appended, never written: the seam, the proof reach and the CEO-step
    check write the same structural file and the gate reads them all."""
    if not path or not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan blocker footprint: could not append to the {what} {path}: {exc}",
              file=sys.stderr)


def _append_note(path: str | None, note: list[str]) -> None:
    if not path:
        return
    try:
        lead = "\n" if os.path.isfile(path) and os.path.getsize(path) else ""
    except OSError:
        lead = ""
    _append(path, [lead + note[0]] + [f"- {line}" for line in note[1:]], "note file")


def _read_list(text: str, what: str) -> list:
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError(f"{what} is not a list of records")
    return value


def _caller(slug: str | None) -> str:
    return slug or (os.environ.get("GITHUB_REPOSITORY") or "").rsplit("/", 1)[-1]


def _cmd_findings(args) -> int:
    lines: list[str] = []
    try:
        cards = _read_list(sys.stdin.read(), "children-json on stdin")
    except (OSError, ValueError) as exc:
        note = unknown(f"the children on stdin could not be read ({exc})")
    else:
        try:
            with open(args.detail_file, encoding="utf-8") as fh:
                details = _read_list(fh.read(), f"the children detail {args.detail_file}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            note = unknown(f"the children detail could not be read ({exc})")
        else:
            caller = _caller(args.caller)
            if not caller:
                note = unknown("no caller repo was given")
            else:
                try:
                    reading = Reading(cards, details, caller, os.getcwd())
                    lines, note = reading.findings, reading.note()
                except Exception as exc:  # noqa: BLE001 — input to a critic, never a crash
                    note = unknown(f"the check could not read the plan ({exc!r})")
    _append(args.structural_file, lines, "structural file")
    _append_note(args.note_file, note)
    for line in note:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("findings", help="needless-blocker: lines for every refused "
                                        "edge; children-json on stdin")
    f.add_argument("--detail-file", required=True,
                   help="children-detail records, for titles, bodies and relations")
    f.add_argument("--caller", default=None,
                   help="the epic's repo: label slug (default: GITHUB_REPOSITORY's name)")
    f.add_argument("--structural-file", default=None,
                   help="the first critic's structural file, appended to")
    f.add_argument("--note-file", default=None,
                   help="the mechanical note, appended one Blockers: line")
    f.set_defaults(fn=_cmd_findings)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
