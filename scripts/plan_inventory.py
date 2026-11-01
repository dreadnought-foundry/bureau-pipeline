#!/usr/bin/env python3
"""An epic built from an approved mockup is checked against the mockup's
committed inventory, row by row, before the plan reaches the CEO (DRE-8612).

The stuck-work research of 2026-10-31 counted 27 builds the CEO had to
correct. On 10-19 the Initiatives screens shipped without their chevrons:
nobody checked the planner's cards one-to-one against the mockup. Committed
inventories existed (DRE-7683, DRE-7692), and nothing read them at plan time.
This module reads them, in the shape of `plan_ceo_steps.py` (DRE-7523).

It is STRUCTURAL: one `inventory:` line per defect, appended to the structural
file the seam gate forwards into the first critic's result, so the round is a
`SEND_BACK` whatever the model wrote. A plan that names no approved mockup
appends nothing, and decides exactly as it did before this module existed.

## The trigger — the epic names an approved mockup

Read off three texts: the epic's description, the plan artifact, and the body
of every open child (`plan_critic.shipped_work_is_a_finding`). A path names an
approved mockup when it is under a design folder's `approved/` directory and
does not end `-inventory.md`, or is an `.html` file inside a folder named
`mockups` — how the approved mockups were named before they were committed. A
screen PNG is not one: `design_parity.py` keeps covering those.

## The inventory, and its rows

The plan names each inventory on a line opening `**Inventory:**`, as a
repo-relative path read from the plan run's own checkout — the working
directory. A row is every body row of every markdown table in the file, and
its key is its cells, trimmed with inner whitespace collapsed, joined with
` | `.

## What covers a row

A record in the `inventory-coverage` block of `## The cards` naming the row's
key and either an open child with one of its criteria
(`plan_proof_reach.criteria`), word for word with whitespace collapsed, or a
non-empty `deferred` reason. A screen-level match never covers a row. Whether
the criterion really delivers the row is the first critic's judgment: the
pairs are printed in the note it reads.

CLI:
  findings --artifact F --epic-file E [--structural-file S] [--note-file N]
      `children-json` records on stdin. Appends one `inventory:` line per
      defect to S (never truncates it), and the `Inventory:` lines, with one
      line per row under each, to N. Always exits 0.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_artifact  # noqa: E402
import plan_critic  # noqa: E402
import plan_proof_reach  # noqa: E402
import plan_seam  # noqa: E402

#: What each structural line opens with, so the epic's note says which check.
FINDING_PREFIX = "inventory:"

#: What each note line opens with.
NOTE_PREFIX = "Inventory:"

#: The line the plan names its inventories on.
OPENER = "**Inventory:**"

#: The fenced block inside `## The cards` that maps rows to cards.
COVERAGE_FENCE = "inventory-coverage"

#: What an inventory's file name ends with — a path ending so names an
#: inventory, never a mockup.
INVENTORY_SUFFIX = "-inventory.md"

NO_MOCKUP = f"{NOTE_PREFIX} none — the plan names no approved mockup"

# A path-shaped token: a run of characters markdown does not use to fence one
# in. Trailing sentence punctuation is stripped afterwards.
_TOKEN = re.compile(r"[^\s`'\"()<>\[\]{}|,;*]+")
_APPROVED = re.compile(r"(?:^|/)design/approved/[^/]")
_MOCKUPS = re.compile(r"(?:^|/)mockups/.*\.html$", re.IGNORECASE)
_OPENER_LINE = re.compile(r"^\s*(?:[-*+]\s+)?" + re.escape(OPENER) + r"(?P<rest>.*)$")
_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _collapse(text) -> str:
    return " ".join(str(text).split())


# --- The trigger -------------------------------------------------------------

def is_mockup(path: str) -> bool:
    """Whether `path` names an approved mockup."""
    if _APPROVED.search(path):
        return not path.endswith(INVENTORY_SUFFIX)
    return bool(_MOCKUPS.search(path))


def mockups(text: str) -> list[str]:
    """Every approved-mockup path in `text`, in order, once each."""
    out: list[str] = []
    for token in _TOKEN.findall(text or ""):
        path = token.rstrip(".:!?")
        if is_mockup(path) and path not in out:
            out.append(path)
    return out


def named_mockups(epic_text: str | None, artifact_text: str | None, records) -> list[str]:
    """The approved mockups the epic names across its three texts."""
    texts = [epic_text or "", artifact_text or ""]
    texts += [r.get("body") or "" for r in records or []
              if plan_critic.shipped_work_is_a_finding(r)]
    out: list[str] = []
    for text in texts:
        out += [p for p in mockups(text) if p not in out]
    return out


# --- The inventory -----------------------------------------------------------

def inventory_paths(artifact_text: str | None) -> list[str]:
    """Every path the plan names on an `**Inventory:**` line, in order."""
    out: list[str] = []
    for line in (artifact_text or "").splitlines():
        m = _OPENER_LINE.match(line)
        if not m:
            continue
        for part in m.group("rest").split(","):
            path = part.strip().strip("`").strip()
            if path and path not in out:
                out.append(path)
    return out


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [_collapse(c) for c in _CELL_SPLIT.split(inner)]


def key(row: str) -> str:
    """A row's key: its cells, trimmed and collapsed, joined with ` | `."""
    return " | ".join(_cells(row))


def rows(text: str) -> list[str]:
    """The key of every body row of every markdown table in `text`.

    A table is a run of lines opening with `|`; its first line is the header
    and its `|---|` line the separator, and neither is a row."""
    out: list[str] = []
    in_table = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        if not in_table:
            in_table = True
            continue
        if _SEPARATOR.match(stripped):
            continue
        out.append(key(stripped))
    return out


def read_inventory(path: str, root: str) -> tuple[list[str] | None, str]:
    """`(rows, "")`, or `(None, reason)` when the inventory cannot be read.

    An unreadable inventory is never "no inventory", and never a pass."""
    if os.path.isabs(path):
        return None, "it is not a repo-relative path"
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, path))
    if os.path.commonpath([base, full]) != base:
        return None, "it is outside this checkout"
    if not os.path.isfile(full):
        return None, "it is absent from this checkout"
    try:
        with open(full, encoding="utf-8") as fh:
            found = rows(fh.read())
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"it is unreadable ({exc})"
    if not found:
        return None, "it holds no table rows"
    return found, ""


def inventories_beside(mockup: str, root: str) -> list[str]:
    """The `*-inventory.md` files in a checked-out mockup's folder."""
    if os.path.isabs(mockup) or not os.path.isfile(os.path.join(root, mockup)):
        return []
    folder = os.path.dirname(mockup)
    found = glob.glob(os.path.join(root, folder, "*" + INVENTORY_SUFFIX))
    return sorted(os.path.relpath(p, root) for p in found)


# --- The coverage block ------------------------------------------------------

def coverage(artifact_text: str | None) -> tuple[list | None, str]:
    """`(records, "")`, or `(None, reason)` when `## The cards` carries no
    block that parses as a JSON list."""
    cards = plan_artifact.sections(artifact_text or "").get(
        plan_artifact.LEDGER_CHECK_SECTION) or ""
    blocks = plan_artifact.fenced_blocks(cards, COVERAGE_FENCE)
    if not blocks:
        return None, f"`## The cards` carries no ```{COVERAGE_FENCE} block"
    try:
        data = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        return None, f"the ```{COVERAGE_FENCE} block is not valid JSON ({exc})"
    if not isinstance(data, list):
        return None, f"the ```{COVERAGE_FENCE} block is not a JSON list"
    return data, ""


def open_children(records) -> dict[str, list[str]]:
    """Open child identifier → its criteria, whitespace collapsed."""
    return {r["identifier"]: [_collapse(c) for c in plan_proof_reach.criteria(r.get("body") or "")]
            for r in records or []
            if r.get("identifier") and plan_critic.shipped_work_is_a_finding(r)}


def _covers(record: dict, children: dict[str, list[str]]) -> tuple[str | None, str]:
    """`(how, "")` when the record covers its row — `→ <card> “<criterion>”`
    or `→ deferred — <reason>` — or `(None, reason)` when it does not."""
    card = _collapse(record.get("card") or "")
    criterion = _collapse(record.get("criterion") or "")
    reason = _collapse(record.get("deferred") or "")
    why = ""
    if card:
        if card not in children:
            why = f"{card} is not an open child of this epic"
        elif criterion not in children[card]:
            why = f"its criterion is not one of {card}'s acceptance criteria, word for word"
        else:
            return f"→ {card} “{criterion}”", ""
    if reason:
        return f"→ deferred — {reason}", ""
    return None, why or "it names no card and no deferral reason"


# --- The findings ------------------------------------------------------------

class Result:
    """What one run found: the structural lines and the note lines."""

    def __init__(self):
        self.lines: list[str] = []
        self.note: list[str] = []

    def finding(self, text: str) -> None:
        self.lines.append(_collapse(f"{FINDING_PREFIX} {text}"))


def findings(epic_text: str | None, artifact_text: str | None, records,
             root: str, epic_unread: str = "", artifact_unread: str = "") -> Result:
    """Every `inventory:` line and the note, for one plan.

    `epic_text` or `artifact_text` None is a text that could not be read, and
    the `*_unread` reasons say why. The check runs on the texts it has: a failed
    read never sends back a plan that may name no mockup at all."""
    out = Result()
    if epic_text is None:
        out.note.append(f"{NOTE_PREFIX} the epic's description could not be read "
                        f"({epic_unread}) — the check read the plan artifact and "
                        "the open children")
    if artifact_text is None:
        out.note.append(f"{NOTE_PREFIX} the plan artifact could not be read "
                        f"({artifact_unread}) — the check read the epic's "
                        "description and the open children")
    named = named_mockups(epic_text, artifact_text, records)
    if not named:
        out.note.append(NO_MOCKUP)
        return out
    paths = inventory_paths(artifact_text)
    if not paths:
        beside: list[str] = []
        for mockup in named:
            beside += [p for p in inventories_beside(mockup, root) if p not in beside]
        where = (f" — {', '.join(beside)} is in the same folder" if beside else "")
        unread = (f" (the plan artifact could not be read: {artifact_unread})"
                  if artifact_text is None else "")
        out.finding(f"the plan names the approved mockup {', '.join(named)} and "
                    f"carries no {OPENER} line{unread}{where} — name its "
                    f"committed inventory on a line opening {OPENER}, and cover "
                    f"every row in a ```{COVERAGE_FENCE} block under ## The cards")
        out.note.append(f"{NOTE_PREFIX} the plan names the approved mockup "
                        f"{', '.join(named)} and no {OPENER} line")
        return out

    inventories: dict[str, list[str]] = {}
    for path in paths:
        found, why = read_inventory(path, root)
        if found is None:
            out.finding(f"{path} could not be read — {why}; an inventory that "
                        "cannot be read is never a pass")
            out.note.append(f"{NOTE_PREFIX} {path} — could not be read: {why}")
        else:
            inventories[path] = found

    records_in, block_why = coverage(artifact_text)
    if records_in is None:
        for path, found in inventories.items():
            out.finding(f"{path} has {len(found)} rows and none is covered — "
                        f"{block_why}")
        records_in = []

    children = open_children(records)
    by_row: dict[tuple[str, str], str] = {}
    for i, record in enumerate(records_in, 1):
        if not isinstance(record, dict):
            out.finding(f"coverage record {i} is not a JSON object")
            continue
        inv = str(record.get("inventory") or "").strip().strip("`")
        row = key(str(record.get("row") or ""))
        if inv not in inventories:
            if inv not in paths:
                out.finding(f"the record for row “{row}” names the inventory "
                            f"“{inv}”, which no {OPENER} line names")
            continue
        if row not in inventories[inv]:
            out.finding(f"the record for row “{row}” matches no row of {inv}")
            continue
        how, why = _covers(record, children)
        if how is None:
            out.finding(f"the record for row “{row}” of {inv} covers nothing — {why}")
        else:
            by_row.setdefault((inv, row), how)

    for path, found in inventories.items():
        lines = []
        counts = {"covered": 0, "deferred": 0, "uncovered": 0}
        for row in found:
            how = by_row.get((path, row))
            if how is None:
                counts["uncovered"] += 1
                lines.append(f"- {row} → UNCOVERED")
                if block_why == "":
                    out.finding(f"{path} row “{row}” — no record covers it with an "
                                "open child's criterion or a deferral reason")
            else:
                counts["deferred" if how.startswith("→ deferred") else "covered"] += 1
                lines.append(f"- {row} {how}")
        out.note.append(f"{NOTE_PREFIX} {path} — {len(found)} rows: "
                        f"{counts['covered']} covered, {counts['deferred']} "
                        f"deferred, {counts['uncovered']} uncovered")
        out.note += lines
    return out


# --- CLI --------------------------------------------------------------------

def _append(path: str | None, lines: list[str], what: str) -> None:
    """Appended, never written: the seam, the proof reach, the held-repo and
    CEO-step checks write the same structural file and the gate reads them all."""
    if not path or not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan inventory: could not append to the {what} {path}: {exc}",
              file=sys.stderr)


def _append_note(path: str | None, note: list[str], lines: list[str]) -> None:
    if not path:
        return
    try:
        lead = "\n" if os.path.isfile(path) and os.path.getsize(path) else ""
    except OSError:
        lead = ""
    body = note + [f"- {line}" for line in lines]
    _append(path, [lead + body[0]] + body[1:], "note file")


def _read(path: str) -> tuple[str | None, str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(), ""
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{path}: {exc}"


def _cmd_findings(args) -> int:
    try:
        records = plan_proof_reach._records(plan_seam._stdin_json([]))
        epic_text, epic_unread = _read(args.epic_file)
        artifact_text, artifact_unread = _read(args.artifact)
        result = findings(epic_text, artifact_text, records, os.getcwd(),
                          epic_unread, artifact_unread)
        lines, note = result.lines, result.note
    except Exception as exc:  # noqa: BLE001 — a check that did not run is never a pass
        reason = _collapse(f"{type(exc).__name__}: {exc}")
        print(f"plan inventory: the check could not run ({reason})", file=sys.stderr)
        lines = [f"{FINDING_PREFIX} the check could not run — {reason}"]
        note = [f"{NOTE_PREFIX} UNKNOWN — the check could not run ({reason})"]
    _append(args.structural_file, lines, "structural file")
    _append_note(args.note_file, note, lines)
    for line in note + lines:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("findings", help="inventory: lines for every uncovered "
                                        "row; children-json on stdin")
    f.add_argument("--artifact", required=True, help="the plan artifact to read")
    f.add_argument("--epic-file", required=True,
                   help="the epic's description, saved by linear_ops.py description")
    f.add_argument("--structural-file", default=None,
                   help="the first critic's structural file, appended to")
    f.add_argument("--note-file", default=None,
                   help="the mechanical note, appended the Inventory: lines")
    f.set_defaults(fn=_cmd_findings)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
