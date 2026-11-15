#!/usr/bin/env python3
"""Every group a hygiene hold can be read into, placed in one of three buckets
(DRE-9290).

    python3 scripts/hold_placement.py check

`scripts/hold_waits.py` (DRE-9274) reads each open `hyg-hand-step`,
`hyg-blocker-owner` and `hand-work-overdue` hold into a group by its receipt's
own words. Run against the live board on 2026-11-12 it read 105 open holds on
46 cards in ten groups, and could not say which of them anyone was meant to
act on. `config/hold-placements.json` says so for every group the three
writers can produce: `pipeline` — the pipeline does the step and the hold ends
with a receipt; `owner` — the hold goes to its real owner automatically;
`operator` — it stays a hold and says why. This module is the table's one
reader.

## groups

`groups()` enumerates every `(kind, group)` the writers can produce, read off
their own vocabularies and never off the table:

* `hyg-hand-step` — `machine, kind <k>` for each kind in
  `config/operator-step-kinds.json`, and `person, kind person`, the cause
  `hygiene_hand_step` writes from `operator_step_kind.classify`'s answer;
* `hyg-blocker-owner` — `<lane>, holds up open work`, `<lane>, holds up
  nothing open` and `<lane>, state unread` for each lane in
  `hygiene_blockers.WAITING`, composed by `hold_waits.blocker_group`;
* `hand-work-overdue` — each `Kind:` lead `reconcile.py` can write, composed
  by `hold_deadline`: every module-level `KIND_LEAD_` text there, the
  operator's card over `routing_verdict.hand_marks()` and over each person
  verdict with its destination lane, and the CEO-press hold over every live
  lane in `config/lane-contract.json` but the return branch's, where
  `alarm_held_proofs` posts nothing;
* for each kind, `hold_waits.UNREADABLE`; and the kindless
  `hold_waits.CUT_SHORT`, whose kind is None.

## placement

`placement(kind, group)` is the row that places the group, or None. A row's
`group` may be a template whose `<…>` parts are placeholders; it places every
enumerated group of its kind the template matches. Groups compare with
`str.casefold`: `reconcile.hand_built_reason` writes a label as the card
carries it, so a card labeled `Operator-Step` reads the `labeled
'<mark>'` row.

## the end of a hold

`END_LINE` opens a line that ends holds, naming each by `END_WORDS` — never by
its tag, because a registry tag in a body is read as that act's receipt:
`wait ended: <words>[, <words>…] — <what ended it>`. It ends every open hold
of each named kind on that card whose receipt is older than the line, when
the pipeline's own Linear user wrote it (`hold_waits.read`).

## check

`python3 scripts/hold_placement.py check` prints one line per enumerated
group with its bucket, and exits 1 naming every enumerated group no row
places, every row that places no enumerated group, every group placed twice,
a bucket outside `BUCKETS`, a kind outside the three tags, and a `who` or
`why` that is missing or that a receipt would refuse (a `·` or a line break).
`tests/test_hold_placement.py` runs it against the shipped files, which is
how CI runs it.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hold_deadline  # noqa: E402
import hold_waits  # noqa: E402
import hygiene_blockers  # noqa: E402
import lane_contract  # noqa: E402
import operator_step_kind  # noqa: E402
import routing_verdict  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "config" / "hold-placements.json"
STEP_KINDS = operator_step_kind.CONFIG_PATH

TAG = "hold-placement:"
#: The three buckets, in the order the read counts them.
BUCKETS = ("pipeline", "owner", "operator")
FIELDS = ("kind", "group", "bucket", "who", "why")

#: A line opening with this ends holds — the contract every card of the epic
#: shares.
END_LINE = "wait ended:"
#: The words `END_LINE` names each hold kind by.
END_WORDS = {
    hold_waits.HAND_STEP: "the hand-step class",
    hold_waits.BLOCKER_OWNER: "the blocker notice",
    hold_waits.OVERDUE: "the Hand-work age alarm",
}

#: The prefix of a `hold_deadline` text read as a `Kind:` lead.
LEAD_PREFIX = "KIND_LEAD_"
#: What a receipt refuses, so a `why` quoted into one may not carry it.
REFUSED = ("·", "\n", "\r")
_PLACEHOLDER = re.compile(r"<[^<>]+>")


def load(path: Path | str | None = None) -> list:
    """The table's rows."""
    with open(path or TABLE, encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("rows") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError("config/hold-placements.json carries no `rows` list")
    return rows


# --------------------------------------------------------------------------- #
# groups: off the writers' own vocabularies                                    #
# --------------------------------------------------------------------------- #


def _step_kinds(path) -> list:
    """The machine kinds, read off the file itself — `operator_step_kind.load`
    refuses a kind it does not know, and a new one is what this must see."""
    with open(path or STEP_KINDS, encoding="utf-8") as fh:
        return [row["kind"] for row in json.load(fh)["kinds"]]


def overdue_leads() -> list:
    """Every `Kind:` lead `reconcile.py` can write, through `hold_deadline`."""
    import proof_dispatch  # noqa: PLC0415 — the return branch's lane

    leads = [text for name, text in vars(hold_deadline).items()
             if name.startswith(LEAD_PREFIX) and isinstance(text, str)]
    for mark in routing_verdict.hand_marks():
        leads.append(hold_deadline.kind_lead_operators_card(hold_deadline.labeled_reason(mark)))
    for verdict in routing_verdict.verdicts():
        if routing_verdict.is_person_verdict(verdict):
            leads.append(hold_deadline.kind_lead_operators_card(hold_deadline.routed_reason(
                verdict, routing_verdict.destination(verdict))))
    for lane in lane_contract.lane_names("live"):
        if lane != proof_dispatch.RETURN_LANE:
            leads.append(hold_deadline.kind_lead_ceo_press(lane))
    return leads


def groups(step_kinds: Path | str | None = None) -> list:
    """Every `(kind, group)` the three writers can produce, in a fixed order."""
    found = [(hold_waits.HAND_STEP, f"{operator_step_kind.MACHINE}, kind {kind}")
             for kind in _step_kinds(step_kinds)]
    found.append((hold_waits.HAND_STEP,
                  f"{operator_step_kind.PERSON}, kind {operator_step_kind.PERSON}"))
    for lane in hygiene_blockers.WAITING:
        for split in hold_waits.BLOCKER_SPLITS:
            found.append((hold_waits.BLOCKER_OWNER, hold_waits.blocker_group(lane, split)))
    found += [(hold_waits.OVERDUE, lead) for lead in overdue_leads()]
    found += [(kind, hold_waits.UNREADABLE) for kind in hold_waits.TAGS]
    found.append((None, hold_waits.CUT_SHORT))
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------- #
# placement                                                                    #
# --------------------------------------------------------------------------- #


def name(kind: str | None, group: str) -> str:
    """A group as `hold_waits.py` prints it."""
    return f"{kind} — {group}" if kind else group


def _template(group: str) -> re.Pattern:
    parts = _PLACEHOLDER.split(group.casefold())
    return re.compile(".+?".join(re.escape(p) for p in parts))


def _row_problems(row) -> list:
    if not isinstance(row, dict):
        return [f"a row is not an object: {row!r}"]
    called = name(row.get("kind"), row.get("group"))
    out: list = []
    if set(row) != set(FIELDS):
        out.append(f"{called}: its fields are {sorted(row)}, not {list(FIELDS)}")
    if row.get("kind") is not None and row.get("kind") not in hold_waits.TAGS:
        out.append(f"{called}: kind {row.get('kind')!r} is not one of "
                   f"{', '.join(hold_waits.TAGS)}, nor null")
    if not isinstance(row.get("group"), str) or not row["group"].strip():
        out.append(f"{called}: group must be a text")
    if row.get("bucket") not in BUCKETS:
        out.append(f"{called}: bucket {row.get('bucket')!r} is not one of {', '.join(BUCKETS)}")
    for field in ("who", "why"):
        text = row.get(field)
        if not isinstance(text, str) or not text.strip():
            out.append(f"{called}: its {field} must be a sentence")
        elif any(c in text for c in REFUSED):
            out.append(f"{called}: its {field} carries a `·` or a line break, "
                       "which hygiene's receipt refuses")
    return out


def placements(rows: list, enumerated: list) -> tuple:
    """`({(kind, casefolded group): row}, problems)` — each enumerated group
    the rows place, and every way the table and the writers disagree."""
    index: dict = {}
    problems: list = []
    for row in rows:
        problems += _row_problems(row)
        if not isinstance(row, dict) or not isinstance(row.get("group"), str):
            continue
        pattern = _template(row["group"])
        hits = [(k, g) for k, g in enumerated
                if k == row.get("kind") and pattern.fullmatch(g.casefold())]
        if not hits:
            problems.append(f"{name(row.get('kind'), row['group'])}: no writer produces "
                            "this group — the row places nothing groups() enumerates")
        for k, g in hits:
            key = (k, g.casefold())
            if key in index:
                problems.append(f"{name(k, g)}: placed twice — by {index[key]['group']!r} "
                                f"and {row['group']!r}")
            else:
                index[key] = row
    for k, g in enumerated:
        if (k, g.casefold()) not in index:
            problems.append(f"{name(k, g)}: no row places this group")
    return index, problems


@functools.lru_cache(maxsize=1)
def _shipped() -> dict:
    return placements(load(), groups())[0]


def placement(kind: str | None, group: str, *, index: dict | None = None) -> dict | None:
    """The row that places `group` of `kind`, or None — compared case-blind."""
    return (_shipped() if index is None else index).get((kind, (group or "").casefold()))


# --------------------------------------------------------------------------- #
# check                                                                        #
# --------------------------------------------------------------------------- #


def check(table: Path | str | None = None, step_kinds: Path | str | None = None) -> int:
    try:
        rows = load(table)
        enumerated = groups(step_kinds)
    except (OSError, ValueError, KeyError) as e:
        print(f"{TAG} a file is unreadable — {e}")
        return 1
    index, found = placements(rows, enumerated)
    for k, g in enumerated:
        row = index.get((k, g.casefold()))
        said = f"bucket {row['bucket']}" if row else "unplaced"
        print(f"{TAG} {name(k, g)}: {said}")
    for problem in found:
        print(f"{TAG} {problem}")
    print(f"{TAG} {len(enumerated)} group(s), {len(rows)} row(s), {len(found)} problem(s)")
    return 1 if found else 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"])
    ap.add_argument("--table", type=Path, default=TABLE)
    ap.add_argument("--step-kinds", type=Path, default=STEP_KINDS)
    args = ap.parse_args(argv)
    return check(args.table, args.step_kinds)


if __name__ == "__main__":
    sys.exit(main())
