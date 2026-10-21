#!/usr/bin/env python3
"""Every Triage write states its reason, or the registry says why not (DRE-7525).

The weekly agent report (DRE-7512) counted thirty-seven stops in one week that
carried no record of why they happened, so nobody could tell whether they
needed a person at all. This is the code side of that: every place in this
repository that moves a card into `Triage` is declared in
`config/triage-reasons.json`, with the reason it posts or with why it posts
none, and this check holds the registry to the tree. It copies the
`needs-human` registry (`scripts/hold.py`, `config/holds.json`, DRE-6173).

It discovers nothing itself and keeps no list of sites:

* the writes are `ready_lane_writers.writes()`, kept to the ones into Triage;
* each is located at its UNIT by `green_light_rows.unit_of()` —
  `<file>#<function>` for a script, `<file>#<step>` for a workflow;
* the callers of a lane-writing function are `lane_callers.callers_of()`;
* an anchor is matched by `hold.row_matches()`, against the reasoning unit's
  text: the function's source, or the step's text read through
  `step_shell.workflow_source`.

## The registry

`reasons` holds one row per unit that posts a reason: `unit`, `reason_at` (the
unit whose text carries it — the write's own unit, or a discovered caller of
that function), `anchor` (a literal phrase of the reason's text, or a constant
the reasoning unit names, and never the lane-write line itself) and `note` (one
plain line saying what the reason tells a reader). A reason is a card comment,
a `🔒 hold:` stamp, or the description of a card the unit creates directly in
Triage. `unreasoned` holds one row per unit that posts none, with its `why`.

## The rules

1. A discovered Triage unit no row names fails BY LOCATION.
2. A row whose `unit` is not a discovered Triage write fails BY NAME: the site
   moved, or no longer writes Triage.
3. An `anchor` not in its `reason_at` text fails naming the unit and the
   anchor, and one standing on a lane-write line of the unit fails the same
   way: the move is not the reason.
4. A `reason_at` that is neither the unit nor a discovered caller of it fails
   naming both. A workflow step has no caller: its reason is its own.
5. An `unreasoned` row with an empty `why`, a unit named in both blocks, a
   unit named twice in one, and a `reasons` row missing a field fail by name.
6. A write whose destination `writes()` could not read (`lane=None`) fails
   naming the site, because it could be a Triage write — the rule
   `green_light_rows.py` applies to Green Light.

## WHAT THIS CANNOT SEE

It is a check on the source, never a refusal at run time: no move is refused,
defaulted or warned on. It cannot see a hand move in the Linear UI
(`ready_lane_writers.unseen_writers()`), a move the relay (agent-bureau)
makes, or a reason post that fails at run time after its move. Everything
`ready_lane_writers` and `lane_callers` say they cannot see in their own
docstrings is unseen here too. `docs/triage-reasons.md` is the page a person
reads.

CLI:

    python3 scripts/triage_reasons.py check
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import green_light_rows  # noqa: E402 — a write's unit, a step's span (DRE-5282)
import hold  # noqa: E402 — the anchor rule (DRE-6173)
import lane_callers  # noqa: E402 — caller discovery (DRE-5345)
import lane_contract  # noqa: E402
import ready_lane_writers  # noqa: E402 — write discovery (DRE-2859)
import step_shell  # noqa: E402 — a moved step's shell, where it now lives (DRE-5220)

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

REGISTRY = os.path.join("config", "triage-reasons.json")

#: The broken-card lane. The lane contract has no clause that picks it out the
#: way Green Light's `arrivals` do, so the name is written once here and read
#: back off the contract: a rename there fails the check rather than passing an
#: empty set of writes.
TRIAGE = "Triage"

#: The fields a `reasons` row carries, each non-empty.
REASON_FIELDS = ("unit", "reason_at", "anchor", "note")


# --------------------------------------------------------------------------- #
# the registry                                                                 #
# --------------------------------------------------------------------------- #


def load(root: str = ROOT) -> dict:
    """`config/triage-reasons.json` under `root`."""
    with open(os.path.join(root, REGISTRY), encoding="utf-8") as fh:
        return json.load(fh)


def lane_name(contract: dict | None = None) -> str:
    """The lane this check is about, confirmed live on the contract."""
    if TRIAGE not in lane_contract.lane_names(status="live", contract=contract):
        raise ValueError(f"the lane contract carries no live {TRIAGE!r} lane")
    return TRIAGE


# --------------------------------------------------------------------------- #
# discovery, read                                                              #
# --------------------------------------------------------------------------- #


def triage_writes(root: str = ROOT, contract: dict | None = None) -> list:
    """(write, unit) for every discovered write into Triage."""
    lane = lane_name(contract)
    return [(w, green_light_rows.unit_of(w, root))
            for w in ready_lane_writers.writes(root, contract) if w.lane == lane]


def _file_of(where: str) -> str:
    """`scripts/reconcile.py:3154` → `scripts/reconcile.py`."""
    return where.rpartition(":")[0]


def _is_workflow(path: str) -> bool:
    return path.endswith((".yml", ".yaml"))


def _unit_text(root: str, path: str, scope: str) -> str | None:
    """The text a unit's anchor is looked for in, or None when it is not there.

    A script's unit is the source of the function so named (every def of that
    name, nested ones included, as `unit_of` names the innermost), or the
    whole module for `<module>`. A workflow's unit is its step's lines, read
    through `step_shell.workflow_source` so a delegated step's script is part
    of the step."""
    full = os.path.join(root, path)
    if _is_workflow(path):
        try:
            text = step_shell.workflow_source(os.path.abspath(full), root)
        except OSError:
            return None
        lines = text.splitlines()
        spans = [s for s in green_light_rows._step_spans(text) if s[2] == scope]
        if not spans:
            return None
        return "\n".join("\n".join(lines[first - 1:last]) for first, last, _, _ in spans)
    try:
        with open(full, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)
    except (OSError, SyntaxError, ValueError):
        return None
    if scope == lane_callers.MODULE_UNIT:
        return source
    found = [ast.get_source_segment(source, node) or "" for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == scope]
    return "\n".join(found) if found else None


def _write_line(root: str, write) -> str:
    """The line a write sits on, as `writes()` read it."""
    path, _, line = write.where.rpartition(":")
    full = os.path.join(root, path)
    try:
        if write.how == "workflow":
            lines = step_shell.workflow_source(os.path.abspath(full), root).splitlines()
        else:
            with open(full, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        return lines[int(line) - 1]
    except (OSError, ValueError, IndexError):
        return ""


def _callers(root: str, path: str, scope: str) -> tuple:
    """({short caller unit: its repo-relative file}, unread files) for a
    script unit; nothing for a step, or a unit that is not a top-level def."""
    if _is_workflow(path):
        return {}, frozenset()
    try:
        report = lane_callers.callers_of(path, scope, root)
    except lane_callers.UnknownTarget:
        return {}, frozenset()
    found = {}
    for caller in report.callers:
        file, _, rest = caller.partition("#")
        found[green_light_rows.short_unit(caller)] = (file, rest)
    return found, report.unread


# --------------------------------------------------------------------------- #
# the rules                                                                    #
# --------------------------------------------------------------------------- #


def _shape_problems(doc: dict) -> list:
    out: list[str] = []
    reasons = doc.get("reasons") or []
    unreasoned = doc.get("unreasoned") or []
    for index, row in enumerate(reasons):
        missing = [f for f in REASON_FIELDS if not str(row.get(f) or "").strip()]
        if missing:
            out.append(
                f"{row.get('unit') or f'reasons row {index}'}: a `reasons` row needs "
                f"{', '.join(f'`{f}`' for f in missing)} — each says where the "
                "reason is written and what it tells a reader"
            )
    for index, row in enumerate(unreasoned):
        if not str(row.get("why") or "").strip():
            out.append(
                f"{row.get('unit') or f'unreasoned row {index}'} is declared "
                "unreasoned with no `why` — a gap is declared with the reason it "
                "stands, or it is not declared"
            )
    for block, rows in (("reasons", reasons), ("unreasoned", unreasoned)):
        units = [r.get("unit") for r in rows]
        for unit in sorted({u for u in units if u and units.count(u) > 1}):
            out.append(f"{unit} is named {units.count(unit)} times in `{block}` — "
                       "one unit, one row")
    both = {r.get("unit") for r in reasons} & {r.get("unit") for r in unreasoned}
    for unit in sorted(u for u in both if u):
        out.append(
            f"{unit} is named in both `reasons` and `unreasoned` — it either "
            "posts a reason or it does not"
        )
    return out


def _reason_problems(row: dict, here: list, root: str) -> list:
    """Rules 3 and 4 for one `reasons` row whose unit was discovered."""
    unit, reason_at, anchor = row["unit"], row["reason_at"], row["anchor"]
    path = _file_of(here[0].where)
    scope = unit.partition("#")[2]
    out: list[str] = []
    for write in here:
        if anchor in _write_line(root, write):
            out.append(
                f"{unit}: its anchor {anchor!r} is the lane-write line "
                f"({write.where}) — the move is not the reason; anchor on a "
                "phrase of the reason's text or a constant the unit names"
            )
    if reason_at == unit:
        reason_path, reason_scope = path, scope
    else:
        callers, unread = _callers(root, path, scope)
        if reason_at not in callers:
            unread_note = (f"; {', '.join(sorted(unread))} could not be read"
                           if unread else "")
            out.append(
                f"{unit}: its reason_at {reason_at} is neither the unit nor a "
                f"discovered caller of it{unread_note} — a reason counts where "
                "the write's own unit, or a unit that reaches it, posts it"
            )
            return out
        reason_path, reason_scope = callers[reason_at]
    text = _unit_text(root, reason_path, reason_scope)
    site = hold.Site(reason_path, reason_scope, 0, text or "", "reason")
    if text is None or not hold.row_matches(
            {"file": reason_path, "scope": reason_scope, "anchor": anchor}, site):
        out.append(
            f"{unit}: its anchor {anchor!r} is not in the text of {reason_at} — "
            "the reason was reworded or moved; point the row at where it is "
            "written now"
        )
    return out


def problems(doc: dict | None = None, root: str = ROOT,
             contract: dict | None = None) -> list:
    """Every way the registry and the Triage writes in the tree disagree."""
    doc = doc if doc is not None else load(root)
    try:
        lane = lane_name(contract)
    except ValueError as e:
        return [str(e)]
    out: list[str] = []
    discovered = ready_lane_writers.writes(root, contract)

    for write in discovered:
        if write.lane is None:
            out.append(
                f"UNREAD {write.writer} ({write.where}) hands the write layer a "
                f"destination this cannot read ({write.expression}) — it could be "
                f"{lane}, and nothing here could say. Name the lane at the call "
                f"site, or publish `{ready_lane_writers.DESTINATIONS_HOOK}()` in "
                "the module"
            )

    by_unit: dict = {}
    for write in discovered:
        if write.lane == lane:
            by_unit.setdefault(green_light_rows.unit_of(write, root), []).append(write)

    out += _shape_problems(doc)
    reasons = doc.get("reasons") or []
    unreasoned = doc.get("unreasoned") or []
    named = {r.get("unit") for r in reasons} | {r.get("unit") for r in unreasoned}

    for unit, here in sorted(by_unit.items()):
        if unit not in named:
            sites = ", ".join(w.where for w in here)
            out.append(
                f"{unit} ({sites}) writes {lane}, and no row in {REGISTRY} names "
                "it — post a reason and name it under `reasons`, or declare under "
                "`unreasoned` why it posts none"
            )

    for block, rows in (("reasons", reasons), ("unreasoned", unreasoned)):
        for row in rows:
            unit = row.get("unit")
            if unit and unit not in by_unit:
                out.append(
                    f"{unit} is named in `{block}`, and no {lane} write was "
                    "discovered there — the step or function is gone or no longer "
                    "writes the lane; retire the row with it"
                )

    for row in reasons:
        unit = row.get("unit")
        if unit in by_unit and all(str(row.get(f) or "").strip() for f in REASON_FIELDS):
            out += _reason_problems(row, by_unit[unit], root)
    return out


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def run_check(root: str = ROOT, doc: dict | None = None,
              contract: dict | None = None, out=print) -> int:
    """Print every problem, then every unit with its reason or its why."""
    doc = doc if doc is not None else load(root)
    found = problems(doc, root, contract)
    for problem in found:
        out(f"  [FAIL] {problem}")
    reasons = {r.get("unit"): r for r in doc.get("reasons") or []}
    unreasoned = {r.get("unit"): r for r in doc.get("unreasoned") or []}
    try:
        writes = triage_writes(root, contract)
    except ValueError:
        writes = []
    units = sorted({unit for _, unit in writes})
    for unit in units:
        if unit in reasons:
            out(f"  {unit}: reason at {reasons[unit].get('reason_at')} "
                f"({reasons[unit].get('anchor')!r})")
        elif unit in unreasoned:
            out(f"  {unit}: UNREASONED — {unreasoned[unit].get('why')}")
        else:
            out(f"  {unit}: UNDECLARED")
    out(
        f"{len(writes)} write(s) into {TRIAGE} discovered in {len(units)} unit(s); "
        f"{len(reasons)} reasoned, {len(unreasoned)} unreasoned; "
        f"{len(found)} problem(s)"
    )
    unseen = ready_lane_writers.unseen_writers(contract)
    if unseen:
        out(f"  not checkable from here: {', '.join(unseen)}")
    return 1 if found else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if (args.command or "check") != "check":
        parser.print_usage(sys.stderr)
        return 2
    # `reconcile` reads REPO at import, and `writes()` imports it to read its
    # published `destinations()`, as `green_light_rows.main` says.
    os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
