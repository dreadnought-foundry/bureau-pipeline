#!/usr/bin/env python3
"""One import-safe reader of a repo's sweep holds (DRE-7750).

The sweep holds a repo two ways: a WIP cap of 0, read off the caller's own
reconcile stub (`cap_and_source`), and the Intake-hold switch
(`intake_controls.hold`). Both were answered only inside a sweep, because
`reconcile.py` reads REPO and LINEAR_API_KEY at import. The cap reading lives
here now, `reconcile.py` binds these same objects, and planning asks the same
question by calling `read`, never by copying the logic (epic DRE-7671).

`read` adds the one thing planning needs on top: the stub's own comment about
its cap line, so a plan for a held repo can say why it is held in the words
the stub uses. That comment is read only from inside the job that holds the
cap line, and only when it is about the cap — a file header, a cron note
under `on:` or another job's comments is never quoted as the hold's reason.

Standard library only, and no REPO, LINEAR_API_KEY or YAML parser needed:

    python3 scripts/sweep_holds.py read [--workspace W]

prints the record as one JSON object, reading MAX_WIP, BUREAU_MAX_WIP and
INTAKE_HOLD from the environment. It never exits non-zero on a hold question.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import NamedTuple

import intake_controls

# The ONE work-in-progress fallback (DRE-2529). It applies only when none of
# the run's `max_wip` input, the caller's reconcile.yml stub and the caller's
# BUREAU_MAX_WIP variable names a cap (`cap_and_source` below).
# scripts/check_wip_cap.py reads this constant, and since DRE-3994 fails the
# build if any reusable workflow re-declares it as an input default: a declared
# default renders "8" whenever a stub is silent, and the script could then
# never fall through to the repo's own cap.
# It was 4 while the workflows all said 8 — a fifth cap hiding behind an unset
# env var, which is the same defect DRE-2529 removed from the workflows.
DEFAULT_MAX_WIP = 8


def _whole_number(raw):
    """`raw` as a whole number of 0 or more, or None. "-1", "12.0", "lots" and
    an unresolved `${{ … }}` are all None; so is unset and empty."""
    text = "" if raw is None else str(raw).strip()
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


# The reusable a caller's reconcile stub calls. Matched on a `uses:` line that
# is not a comment, so the stub that owns the repo's cap is found by what it
# DOES, whatever the file is named (bureau-pipeline's own is self-reconcile.yml).
_RECONCILE_STUB_USES = re.compile(
    r"^[ \t]*uses:[ \t]*['\"]?[^\s#'\"]*bureau-pipeline/\.github/workflows/reconcile\.yml@",
    re.MULTILINE,
)
# A `max_wip:` key, never a comment line that mentions one (portico's stub
# carries "# No max_wip: ..." — read as a cap, that is a hold nobody set).
_STUB_MAX_WIP = re.compile(r"^[ \t]*max_wip:[ \t]*(.*?)[ \t]*(?:#.*)?$", re.MULTILINE)

# A comment block is about the cap when it names one of these as a whole word.
_ABOUT_THE_CAP = re.compile(r"\b(?:max_wip|wip|cap|hold|held)\b", re.IGNORECASE)


class SweepHolds(NamedTuple):
    """Whether the sweep holds this repo, and why, as `read` found it."""

    cap: int
    cap_source: str
    stub_path: str | None
    stub_comment: str
    intake_hold: str | None
    max_wip_held: bool
    intake_held: bool
    held: bool


class _CapLine(NamedTuple):
    """The ONE `max_wip:` line that gave a stub's lowest cap."""

    cap: int
    path: str
    lines: list[str]
    index: int
    trailing: str


def cap_and_source(raw, workspace, variable=None, ignored=None) -> tuple[int, str]:
    """The WIP cap this run promotes at, and where it was read (DRE-3994).

    The order, and why:

      1. The run's `max_wip` input, when it names a number. A stub that passes
         a cap on this path (agent-bureau passes "12" on all three) keeps it.
      2. The caller's reconcile.yml stub, when the input is empty. That file is
         the one place a product repo writes its cap — it is what the sweep
         runs at — and every job that promotes has the caller checked out at
         $GITHUB_WORKSPACE. Before this, plan.yml's activate route and
         linear-sync.yml's merge handler took an empty input as the reusable's
         default of 8: on 2026-09-15 approving DRE-3777 promoted three cards in
         portico, held at "0" for a week, and atlas and agent-bureau-demo
         (also "0") and deltasolv ("4") would have leaked the same way.
      3. The caller's BUREAU_MAX_WIP Actions variable (DRE-6631), when neither
         names a number. It is what the console writes, and GitHub resolves it
         repo-level first, then org-level, so one read gives each project its
         own setting and each org a default. It sits BELOW the stub on
         purpose: the sweep receives the stub's literal as its input, so the
         two fast paths, whose stubs pass nothing, must prefer that literal
         too, or a repo with both set promotes at two caps (DRE-2529).
         Setting a variable therefore changes nothing while a literal stands.
      4. DEFAULT_MAX_WIP, saying why.

    A non-empty value that is not a whole number of 0 or more ("lots", "-1",
    "12.0", an unresolved `${{ … }}`) is skipped to the next source, and one
    `promotion: ignored <source> = <value>: …` line is appended to `ignored`
    for the run log. An empty value is the normal "not set" and says nothing.

    Read with the standard library on purpose: the promotion steps install
    nothing, and reconcile.py imports no YAML parser. Never raises — a cap
    question must not turn a promotion step red.
    """
    notes = ignored if ignored is not None else []

    def _ignore(source, value):
        shown = " ".join(str(value).split())
        notes.append(
            f"promotion: ignored {source} = {shown}: not a whole number of 0 or more"
        )

    def _read(source, value):
        cap = _whole_number(value)
        if cap is None and value is not None and str(value).strip():
            _ignore(source, str(value).strip())
        return cap

    cap = _read("this run's max_wip input", raw)
    if cap is not None:
        return cap, "this run's max_wip input"

    stub_cap, why_not_stub = _stub_cap(workspace, _ignore)
    if stub_cap is not None:
        return stub_cap, why_not_stub

    variable_source = "the caller's BUREAU_MAX_WIP variable"
    cap = _read(variable_source, variable)
    if cap is not None:
        return cap, variable_source
    return DEFAULT_MAX_WIP, (
        f"the default {DEFAULT_MAX_WIP} — {why_not_stub}, and no BUREAU_MAX_WIP "
        "variable names a number"
    )


def _stub_cap(workspace, ignore) -> tuple[int | None, str]:
    """(cap, source) from the caller's reconcile stub, or (None, why not).

    A `max_wip:` value that is not a whole number of 0 or more is handed to
    `ignore` and skipped; an empty one is skipped in silence."""
    line, why_not = _cap_line(workspace, ignore)
    if line is None:
        return None, why_not
    return line.cap, f"the caller's reconcile stub {line.path}"


def _cap_line(workspace, ignore) -> tuple[_CapLine | None, str]:
    """The cap line of the caller's reconcile stubs, or (None, why not).

    Every whole-number `max_wip:` line of every reconcile stub is read and the
    lowest cap kept. On a tie it is the first stub in file-name order, then the
    first such line in that file — so when the cap came from the stub, the
    line is in the stub `cap_and_source` names. One scan serves `_stub_cap`
    and `read`, so the line `read` quotes is the line the cap was taken from.
    """
    if not workspace:
        return None, "no caller checkout to read a reconcile stub from"
    workflows = os.path.join(str(workspace), ".github", "workflows")
    try:
        names = sorted(
            n for n in os.listdir(workflows) if n.endswith((".yml", ".yaml"))
        )
    except OSError:
        names = []
    stubs: list[tuple[str, str]] = []
    for name in names:
        try:
            with open(os.path.join(workflows, name), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if _RECONCILE_STUB_USES.search(text):
            stubs.append((f".github/workflows/{name}", text))
    if not stubs:
        return None, "no reconcile stub in the caller's .github/workflows"
    lowest: _CapLine | None = None
    for path, text in stubs:
        for match in _STUB_MAX_WIP.finditer(text):
            value = match.group(1).strip().strip("'\"").strip()
            cap = _whole_number(value)
            if cap is None:
                if value:
                    ignore(f"the caller's reconcile stub {path}", value)
                continue
            # More than one stub or line is not a fleet shape; if it ever
            # happens, the LOWER cap is the one that cannot build past
            # anybody's hold. Strictly lower, so a tie keeps the first.
            if lowest is None or cap < lowest.cap:
                rest = text[match.end(1):match.end()].strip()
                lowest = _CapLine(
                    cap,
                    path,
                    text.splitlines(),
                    text.count("\n", 0, match.start()),
                    rest[1:].strip() if rest.startswith("#") else "",
                )
    if lowest is not None:
        return lowest, f"the caller's reconcile stub {lowest.path}"
    return None, (
        f"the caller's reconcile stub {', '.join(p for p, _ in stubs)} "
        "names no max_wip number"
    )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_yaml(line: str) -> bool:
    return not _is_blank(line) and not _is_comment(line)


def _parent(lines: list[str], index: int | None) -> int | None:
    """The nearest YAML line above `index` indented less than it, or None."""
    if index is None:
        return None
    depth = _indent(lines[index])
    for i in range(index - 1, -1, -1):
        if _is_yaml(lines[i]) and _indent(lines[i]) < depth:
            return i
    return None


def _text(block: list[str]) -> str:
    words = (line.strip().lstrip("#").strip() for line in block)
    return " ".join(w for w in words if w)


def _about_the_cap(block: list[str]) -> bool:
    return bool(_ABOUT_THE_CAP.search(_text(block)))


def _block_above(lines: list[str], top: int) -> list[str] | None:
    """The comment block above the job key at `top`, separated from it by
    blank lines alone — or None when there is none, or it is the previous
    job's (indented inside it, directly under one of its lines)."""
    i = top - 1
    while i >= 0 and _is_blank(lines[i]):
        i -= 1
    end = i
    while i >= 0 and _is_comment(lines[i]):
        i -= 1
    if end == i:
        return None
    block = lines[i + 1:end + 1]
    attached = i >= 0 and _is_yaml(lines[i]) and _indent(lines[i]) >= _indent(lines[top])
    if attached and _indent(block[0]) > _indent(lines[top]):
        return None
    return block


def _comment_above(lines: list[str], index: int) -> list[str] | None:
    """Steps 2 and 3: the nearest block about the cap, walking up through the
    job that holds the cap line and, above its key, the one block separated
    from it by blank lines alone."""
    job = _parent(lines, _parent(lines, index))
    stop = job if job is not None else _parent(lines, index)
    block: list[str] = []
    for i in range(index - 1, -1 if stop is None else stop, -1):
        if stop is None and _is_yaml(lines[i]):
            break  # no job holds the line: read no further than its own
        if _is_comment(lines[i]):
            block.insert(0, lines[i])
            continue
        if block and _about_the_cap(block):
            return block
        block = []
    if block and _about_the_cap(block):
        return block
    if job is None:
        return None
    above = _block_above(lines, job)
    return above if above is not None and _about_the_cap(above) else None


def _comment_below(lines: list[str], index: int) -> list[str] | None:
    """Step 4: a block directly after the cap line, or after a later key of
    the same `with:` block, indented at least as deep as the cap line."""
    depth = _indent(lines[index])
    block: list[str] = []
    for line in lines[index + 1:]:
        if _is_comment(line) and _indent(line) >= depth:
            block.append(line)
            continue
        if block and _about_the_cap(block):
            return block
        block = []
        if not _is_yaml(line) or _indent(line) < depth:
            return None
    return block if block and _about_the_cap(block) else None


def _stub_comment(line: _CapLine) -> str:
    """The stub's own comment about its cap line: the nearest block about the
    cap (above first, below only when above keeps none), then the cap line's
    trailing comment, joined with single spaces."""
    block = _comment_above(line.lines, line.index)
    if block is None:
        block = _comment_below(line.lines, line.index)
    return " ".join(t for t in (_text(block or []), line.trailing) if t)


def read(workspace, max_wip_input=None, bureau_max_wip=None,
         intake_hold_raw=None) -> SweepHolds:
    """Whether the sweep holds this repo, and the stub's own words for why.

    `cap` and `cap_source` are exactly what `cap_and_source` returns. The cap
    line is found in the caller's checkout whatever supplied `cap` — the
    sweep itself is handed its stub's literal as the input — and quoted only
    when it names the `cap` that won. Never raises on a hold question.
    """
    cap, cap_source = cap_and_source(max_wip_input, workspace, bureau_max_wip)
    line, _why_not = _cap_line(workspace, lambda *_: None)
    if line is not None and line.cap == cap:
        stub_path, stub_comment = line.path, _stub_comment(line)
    else:
        stub_path, stub_comment = None, ""
    intake_hold = intake_controls.hold(intake_hold_raw)
    max_wip_held = cap == 0
    intake_held = intake_hold is not None
    return SweepHolds(
        cap=cap,
        cap_source=cap_source,
        stub_path=stub_path,
        stub_comment=stub_comment,
        intake_hold=intake_hold,
        max_wip_held=max_wip_held,
        intake_held=intake_held,
        held=max_wip_held or intake_held,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    read_cmd = sub.add_parser("read", help="print the repo's sweep holds as JSON")
    read_cmd.add_argument(
        "--workspace",
        default=os.environ.get("GITHUB_WORKSPACE"),
        help="the caller checkout (default: $GITHUB_WORKSPACE)",
    )
    args = parser.parse_args(argv)
    holds = read(
        args.workspace,
        os.environ.get("MAX_WIP"),
        os.environ.get("BUREAU_MAX_WIP"),
        os.environ.get(intake_controls.ENV_HOLD),
    )
    print(json.dumps(holds._asdict()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
