#!/usr/bin/env python3
"""One registry of every `needs-human` writer, and the one module that reads it (DRE-6173).

The `needs-human` label is the pipeline's hold: every sweep, the fix loop's
dispatch, the medic and limit recovery leave a card that wears it alone. Before
this card it was written from fifteen sites in six files and nothing listed
them. The bare label was the whole record, so nothing could tell a review cap
from a dead-run cap, and nothing knew what would lift either.

This module is the vocabulary as code, `config/holds.json` is the vocabulary
and the list of writers as data, and `docs/holds.md` is the page a person
reads. Every card of epic DRE-6172 reads the hold through here.

## The registry

`config/holds.json` names each writer site by `file`, by `scope` (the enclosing
Python function, `<module>` for a module-level literal, the workflow step's
`name`, or the shell function in a script no workflow step delegates to) and by
`anchor`, a literal phrase in that scope's text: a phrase of the receipt the
site posts beside its label write, or a constant the scope names, and NEVER the
label-write line itself. So swapping `add_label` for `hold.apply` at a site
changes no row; a row changes only when its site moves scope or its receipt is
reworded. When one scope holds two sites, each site's text is a window of
`_LOOKBACK` lines before and `_LOOKAHEAD` after it, never crossing the other
site, and the anchor must sit in exactly one window. This is the
file-plus-step-plus-anchor rule `check_act_receipts._matches` applies to
`config/pipeline-acts.json`'s `unconverted` block.

The sites are DISCOVERED, never listed (`discover`):

  * `scripts/**/*.py` by AST — a call to `add_label` whose label is
    `HOLD_LABEL`, `dead_run.HOLD_LABEL`, `_HOLD_LABEL`, `NEEDS_HUMAN` or the
    literal; a call to `dead_run.park`, or to `park` inside `dead_run.py`; a
    tuple or list literal carrying the label (a card created already held);
    a `hold.apply` call. This file is the seam and is not read: its callers
    are the sites, as a poster's callers are `check_act_receipts`'.
  * `.github/workflows/*.yml` by text, read through `step_shell.workflow_source`
    so a step moved to `scripts/<name>.sh` keeps its workflow and its step —
    `add-label <card> needs-human`, `--label needs-human`, `hold.py apply`.
  * `scripts/*.sh` by the same text, for a script no workflow step delegates
    to (one that is delegated to is read through its workflow above).

## The stamp

    🔒 hold: reason=<code> at=<qualifier or none> lifts=<lift-kind> by=<writer-file>

One line, the first line of its own comment, posted right after the label. The
qualifier is the full head sha for a `new-head` reason, `repo:<slug>` for
`no-route` and `none` otherwise; `stamp_line` refuses any other pairing. The
newest stamp is the card's hold unless it is SPENT: a newer comment opening
`🔓 hold lifted:`, or carrying `hyg-hold-cleared` or `dead-run-budget-reset`,
retires every stamp older than it, and a label standing over a spent stamp is
a person's — `manual`.

CLI:

    python3 scripts/hold.py apply <CARD> --reason <code> [--at <q>] --by <file>
    python3 scripts/hold.py lift <CARD> --because <kind> --by <file>
    python3 scripts/hold.py reason <CARD>     # prints reason_of; always exit 0
    python3 scripts/hold.py check             # the registry against the tree

Import-safe: no I/O at import. `linear_ops` is imported by the commands that
need Linear, never at the top, as `dead_run._cmd_park` does.
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dead_run  # noqa: E402 — import-safe: no I/O at import
import step_shell  # noqa: E402 — reads a moved step where it now lives

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

HOLD_LABEL = dead_run.HOLD_LABEL  # "needs-human"

# --------------------------------------------------------------------------- #
# the contract (identical in every card of DRE-6172)                           #
# --------------------------------------------------------------------------- #

#: The reason codes, the lift kinds and the readers, exact strings. The file
#: carries them too, for the page; `check` holds the file to these.
REASONS = (
    "stranded-no-run", "no-route", "review-cap-spent", "dead-run-cap",
    "turn-cap-park", "epic-rereview-twice", "plan-critic-bound", "fix-dispute",
    "unfixable-check", "operator-step", "manual",
)
LIFT_KINDS = ("run-started", "repo-on-rail", "new-head", "unpark-marker",
              "blockers-terminal", "manual")
READERS = ("sweep", "fix-dispatch", "medic", "limit-recovery")

#: A planner-filed operator step: a person does it once its blockers are
#: Done (DRE-6426). Lifted when every blockedBy relation is terminal.
OPERATOR_STEP_REASON = "operator-step"
BLOCKERS_TERMINAL = "blockers-terminal"

#: The lift kind of each reason, fixed by the contract. A row that names
#: another fails `check` by name — the file cannot vote itself a looser lift.
CONTRACT_LIFTS = {
    "stranded-no-run": "run-started",
    "no-route": "repo-on-rail",
    "review-cap-spent": "new-head",
    "fix-dispute": "new-head",
    "unfixable-check": "new-head",
    "dead-run-cap": "unpark-marker",
    "turn-cap-park": "unpark-marker",
    "epic-rereview-twice": "manual",
    "plan-critic-bound": "manual",
    OPERATOR_STEP_REASON: BLOCKERS_TERMINAL,
    "manual": "manual",
}

#: A reason whose lift a reader makes itself. That reader is the lifter and
#: never a reader of it: a row carrying the reason that names it fails
#: `check`, so `respects(..., <lifter>)` can only answer False once a row
#: exists. The sweep's promotion gate reads the blockers off the card's
#: relations and calls `lift` (DRE-6427).
LIFTERS = {OPERATOR_STEP_REASON: "sweep"}

#: The one universal lift: every reason, when the card is Done or Canceled.
CARD_CLOSED = "card-closed"
CLOSED_LANES = ("Done", "Canceled")

#: What a lift line may say lifted the hold, besides a lift kind.
LIFT_BECAUSE = LIFT_KINDS + (CARD_CLOSED, "operator")

STAMP_PREFIX = "🔒 hold:"
LIFT_PREFIX = "🔓 hold lifted:"

#: The hygiene agent's lift receipt carries this tag (DRE-6180); it retires
#: every stamp older than it.
HYG_CLEARED_TAG = "hyg-hold-cleared"

#: The first line of the receipt that hands a dead-run-capped card to the
#: planner (DRE-6178, DRE-6186).
DEAD_SPLIT_MARK = "✂️ dead-run-cap → Planning:"

#: Keys some reader already counts. Neither line may carry one, or a stamp
#: would spend a budget or read as a console hold. Every tag in
#: `config/pipeline-acts.json` is refused too — `tests/test_hold_registry.py`
#: holds both lines to the registry, since reading it here would be I/O.
FORBIDDEN = (
    "budget exhausted", "holding for a human", "held-for-human", HOLD_LABEL,
    dead_run.DEAD_TAG, dead_run.TURN_TAG,
)

#: A run receipt: every run posts one the moment it starts — the same two
#: prefixes `reconcile._LIFE_PREFIXES` reads.
RUN_RECEIPT_PREFIXES = ("🧠", "⏳")

_STAMP = re.compile(
    r"^🔒 hold: reason=(?P<reason>\S+) at=(?P<at>\S+) "
    r"lifts=(?P<lifts>\S+) by=(?P<by>\S+)$"
)
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_REPO_QUALIFIER = re.compile(r"repo:[a-z0-9][a-z0-9._-]*")
_WRITER_FILE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|sh|yml)")

# --------------------------------------------------------------------------- #
# the registry                                                                 #
# --------------------------------------------------------------------------- #

_LOADED: dict = {}


def load(path: str | None = None) -> dict:
    """`config/holds.json`, parsed. Read once per process per path."""
    path = path or os.path.join(ROOT, "config", "holds.json")
    if path not in _LOADED:
        with open(path, encoding="utf-8") as fh:
            _LOADED[path] = json.load(fh)
    return _LOADED[path]


def reasons(doc: dict | None = None) -> list:
    return list((doc or load()).get("reasons") or {})


def lift_kinds(doc: dict | None = None) -> list:
    return list((doc or load()).get("lift_kinds") or {})


def readers(doc: dict | None = None) -> list:
    return list((doc or load()).get("readers") or {})


# --------------------------------------------------------------------------- #
# the stamp and the lift line                                                  #
# --------------------------------------------------------------------------- #


def _refuse_forbidden(line: str) -> None:
    for key in FORBIDDEN:
        if key in line:
            raise ValueError(f"{line!r} carries {key!r}, a key a reader already counts")


def _writer(by: str) -> str:
    if not _WRITER_FILE.fullmatch(by or ""):
        raise ValueError(f"by={by!r} is not a writer file (a .py, .sh or .yml path)")
    return by


def stamp_line(reason: str, at: str | None, by: str) -> str:
    """The stamp a writer posts right after the label. Raises ValueError on a
    reason outside the vocabulary or a qualifier that does not fit it, so a
    wrong pairing goes red at the writer and never at the lift."""
    if reason not in CONTRACT_LIFTS:
        raise ValueError(f"reason {reason!r} is not in the hold vocabulary")
    at = at or "none"
    kind = CONTRACT_LIFTS[reason]
    if kind == "new-head":
        if not _FULL_SHA.fullmatch(at):
            raise ValueError(f"{reason} is stamped with the full head sha, not {at!r}")
    elif reason == "no-route":
        if not _REPO_QUALIFIER.fullmatch(at):
            raise ValueError(f"no-route is stamped with repo:<slug>, not {at!r}")
    elif at != "none":
        raise ValueError(f"{reason} is stamped at=none, not {at!r}")
    line = f"{STAMP_PREFIX} reason={reason} at={at} lifts={kind} by={_writer(by)}"
    _refuse_forbidden(line)
    return line


def lift_line(reason: str, because: str, by: str) -> str:
    """The line `lift` posts after the label comes off."""
    if because not in LIFT_BECAUSE:
        raise ValueError(f"because={because!r} is not a lift kind, card-closed or operator")
    if not reason or re.search(r"\s", reason):
        raise ValueError(f"reason {reason!r} is not one word")
    line = f"{LIFT_PREFIX} reason={reason} because={because} by={_writer(by)}"
    _refuse_forbidden(line)
    return line


def _first_line(body: str | None) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _retires(body: str | None) -> bool:
    """Does this comment spend every stamp older than it?"""
    text = body or ""
    return (_first_line(text).startswith(LIFT_PREFIX)
            or HYG_CLEARED_TAG in text or dead_run.RESET_TAG in text)


def read_stamp(bodies) -> dict | None:
    """The card's live stamp: the newest `🔒 hold:` line not retired by a
    newer lift line, `hyg-hold-cleared` receipt or `dead-run-budget-reset`
    marker. `bodies` is oldest→newest, as every reader here holds it; None
    when the newest stamp is spent or there is none.

    `{"reason", "at", "lifts", "by", "line"}` — `line` is the stamp as posted,
    which is how `lift_due` finds what is newer than it."""
    for body in reversed(list(bodies or [])):
        match = _STAMP.match(_first_line(body))
        if match:
            return {**match.groupdict(), "line": match.group(0)}
        if _retires(body):
            return None
    return None


def _label_names(labels) -> list:
    return [
        (label.get("name") if isinstance(label, dict) else label) or ""
        for label in (labels or [])
    ]


def _held(labels) -> bool:
    return any(name.lower() == HOLD_LABEL for name in _label_names(labels))


def reason_of(labels, bodies) -> str | None:
    """Why the card is held: None without the label; `manual` for the label
    with no live stamp — a person's hold; else the live stamp's reason."""
    if not _held(labels):
        return None
    stamp = read_stamp(bodies)
    return stamp["reason"] if stamp else "manual"


def respects(labels, bodies, reader: str, doc: dict | None = None) -> bool:
    """Does `reader` stand down for this card? No label → False. The label
    with no live stamp, or a reason the registry has no row for → True: fail
    closed. Otherwise whether a row carrying the reason names the reader."""
    reason = reason_of(labels, bodies)
    if reason is None:
        return False
    rows = [
        row for row in (doc or load()).get("sites") or []
        if any(e.get("reason") == reason for e in row.get("reasons") or [])
    ]
    if reason == "manual" or not rows:
        return True
    return any(reader in (row.get("readers") or []) for row in rows)


def _repo_slugs(labels) -> list:
    return [
        name.lower()[len("repo:"):].rsplit("/", 1)[-1]
        for name in _label_names(labels) if name.lower().startswith("repo:")
    ]


def _newer_than(stamp: dict, bodies) -> list | None:
    """The comments newer than the stamp's newest posting; None when the
    stamp is not in `bodies`, so nothing can be read as newer than it."""
    bodies = list(bodies or [])
    for index in range(len(bodies) - 1, -1, -1):
        if _first_line(bodies[index]) == stamp.get("line"):
            return bodies[index + 1:]
    return None


def lift_due(stamp: dict | None, *, lane: str, labels, pr_head: str | None,
             rail_slugs, bodies) -> str | None:
    """The lift kind now met for this stamp, else None. Pure.

    `card-closed` whenever the lane is Done or Canceled, stamp or none. The
    rest by the contract's lift kind for the stamp's reason: `new-head` when
    the open pull request's head differs from the stamped sha (no head, no
    lift); `repo-on-rail` when every `repo:` label the card wears NOW is on
    the rail — read off `labels`, never off the stamp, so a corrected label
    lifts and a stamped slug that later joined the rail does not;
    `run-started` on a 🧠 or ⏳ run receipt newer than the stamp;
    `unpark-marker` on a `dead-run-budget-reset` marker newer than it. A
    `manual` reason, or one outside the vocabulary, never lifts here.

    Nor does `blockers-terminal` (`operator-step`): no caller passes the
    card's blockers — the hygiene lane never asks about it — so the sweep's
    promotion gate is its only lifter, reading the blockedBy relations
    itself and calling `lift` (DRE-6427). It falls through to None."""
    if lane in CLOSED_LANES:
        return CARD_CLOSED
    if not stamp:
        return None
    kind = CONTRACT_LIFTS.get(stamp.get("reason"))
    if kind == "new-head":
        head = (pr_head or "").lower()
        return kind if head and head != (stamp.get("at") or "").lower() else None
    if kind == "repo-on-rail":
        slugs = _repo_slugs(labels)
        rail = set(rail_slugs or ())
        return kind if slugs and all(s in rail for s in slugs) else None
    if kind in ("run-started", "unpark-marker"):
        newer = _newer_than(stamp, bodies)
        if not newer:
            return None
        if kind == "run-started":
            met = any((b or "").lstrip().startswith(RUN_RECEIPT_PREFIXES) for b in newer)
        else:
            met = any(dead_run.RESET_TAG in (b or "") for b in newer)
        return kind if met else None
    return None


# --------------------------------------------------------------------------- #
# the writes                                                                   #
# --------------------------------------------------------------------------- #


def _read_card(card: str) -> tuple:
    """The card's labels and its comment window (oldest→newest), in ONE read."""
    import linear_ops  # local: only the Linear commands need the seam

    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) {
             labels { nodes { name } } %s } }""" % linear_ops.COMMENT_WINDOW_GQL,
        {"id": card},
    )
    issue = (data or {}).get("issue") or {}
    bodies = [n.get("body") or "" for n in linear_ops.window_nodes(issue.get("comments"))]
    return linear_ops._label_names(issue), bodies


def apply(card: str, reason: str, at: str | None, by: str) -> None:
    """Hold the card: the label, then the stamp. The stamp is composed first,
    so a bad pairing raises before anything is written."""
    import linear_ops  # local: only the Linear commands need the seam

    stamp = stamp_line(reason, at, by)
    linear_ops.add_label(card, HOLD_LABEL)
    post_stamp(card, stamp)


def post_stamp(card: str, stamp: str) -> None:
    """Post a stamp `stamp_line` composed, on its own. `apply` posts through
    here, and so does a writer whose label write is its own — `dead_run.park`
    lands the label and the lane both or neither (DRE-6178), so `apply`'s
    second label write is not its to make. Anything that is not a stamp is
    refused before it reaches the card."""
    import linear_ops  # local: only the Linear commands need the seam

    if not _STAMP.match(stamp or ""):
        raise ValueError(f"{stamp!r} is not a hold stamp")
    linear_ops.cmd_comment(card, stamp)


def lift(card: str, because: str, by: str, *, reason: str | None = None) -> None:
    """Lift the hold: the label off, then the lift line, which spends every
    stamp before it. The reason is read off the card's live stamp unless the
    caller already holds it; with no live stamp it is `manual`."""
    import linear_ops  # local: only the Linear commands need the seam

    if because not in LIFT_BECAUSE:
        raise ValueError(f"because={because!r} is not a lift kind, card-closed or operator")
    if reason is None:
        _, bodies = _read_card(card)
        stamp = read_stamp(bodies)
        reason = stamp["reason"] if stamp else "manual"
    lifted = lift_line(reason, because, by)
    linear_ops.remove_label(card, HOLD_LABEL)
    linear_ops.cmd_comment(card, lifted)


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #

# How far a site's window reaches when its scope holds more than one site.
_LOOKBACK = 12
_LOOKAHEAD = 12

_LABEL_NAMES = frozenset({"HOLD_LABEL", "_HOLD_LABEL", "NEEDS_HUMAN"})

_SHELL_WRITES = (
    re.compile(r"\badd-label\s+\S+\s+needs-human\b"),
    re.compile(r"--label[=\s]+[\"']?needs-human\b"),
    re.compile(r"\bhold\.py\s+apply\b"),
)
_STEP_NAME = re.compile(r"^\s*-\s+name:\s*(?P<name>\S.*?)\s*$")
_SHELL_FUNCTION = re.compile(
    r"^(?P<indent>\s*)(?:function\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?\s*$"
)
_RUN_VALUE = re.compile(r"^\s*run:\s*(?P<value>\S.*)$")

# This module is the seam; its callers are the sites.
_SEAM = "scripts/hold.py"


class Site:
    """One place the hold is applied."""

    def __init__(self, file: str, scope: str, line: int, source: str, kind: str,
                 end_line: int | None = None, col: int = 0, end_col: int = 0):
        self.file = file
        self.scope = scope
        self.line = line
        self.end_line = end_line or line
        self.col = col
        self.end_col = end_col
        self.source = source
        self.kind = kind  # "call", "literal" or "shell"
        self.context = source

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"

    def __repr__(self) -> str:  # pragma: no cover — debugging only
        return f"<Site {self.where} {self.scope}>"


def _is_hold_label(node) -> bool:
    if isinstance(node, ast.Constant):
        return node.value == HOLD_LABEL
    if isinstance(node, ast.Name):
        return node.id in _LABEL_NAMES
    return (isinstance(node, ast.Attribute) and node.attr == "HOLD_LABEL"
            and isinstance(node.value, ast.Name) and node.value.id == "dead_run")


def _python_kind(node, in_dead_run: bool) -> str | None:
    if isinstance(node, (ast.Tuple, ast.List)):
        if any(isinstance(e, ast.Constant) and e.value == HOLD_LABEL for e in node.elts):
            return "literal"
        return None
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else None)
    owner = func.value.id if (isinstance(func, ast.Attribute)
                              and isinstance(func.value, ast.Name)) else None
    if name == "add_label":
        label = node.args[1] if len(node.args) > 1 else next(
            (k.value for k in node.keywords if k.arg == "label_name"), None)
        return "call" if label is not None and _is_hold_label(label) else None
    if name == "park" and (owner == "dead_run" or (owner is None and in_dead_run)):
        return "call"
    if name == "apply" and owner == "hold":
        return "call"
    return None


def _python_sites(root: str) -> list:
    """`[(site, scope key, scope start, scope end)]` for `scripts/**/*.py`."""
    out: list = []
    paths = glob.glob(os.path.join(root, "scripts", "**", "*.py"), recursive=True)
    for path in sorted(paths):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel == _SEAM or "__pycache__" in rel:
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            print(f"WARNING: {rel} does not parse ({exc}) — skipped", file=sys.stderr)
            continue
        in_dead_run = os.path.basename(rel) == "dead_run.py"
        total = len(source.splitlines())

        def visit(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child)
                    continue
                kind = _python_kind(child, in_dead_run)
                if kind:
                    name = scope.name if scope is not None else "<module>"
                    start = scope.lineno if scope is not None else 1
                    end = scope.end_lineno if scope is not None else total
                    site = Site(rel, name, child.lineno,
                                ast.get_source_segment(source, child) or "", kind,
                                child.end_lineno, child.col_offset, child.end_col_offset)
                    out.append((site, (rel, id(scope)), start, end, source))
                visit(child, scope)

        visit(tree, None)
    return out


def _delegated(root: str) -> set:
    """Every `scripts/<name>.sh` a workflow step delegates to."""
    out: set = set()
    for path in glob.glob(os.path.join(root, ".github", "workflows", "*.yml")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                match = _RUN_VALUE.match(line)
                script = step_shell.delegated_script(match.group("value")) if match else None
                if script:
                    out.add(script)
    return out


def _commented(line: str, index: int) -> bool:
    import check_act_receipts  # local: the one owner of the rule

    return check_act_receipts._commented(line, index)


def _shell_lines(rel: str, text: str, scopes) -> list:
    """Sites in one shell text. `scopes` maps a 1-based line to (name, start, end)."""
    out: list = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if not any((m := p.search(line)) and not _commented(line, m.start())
                   for p in _SHELL_WRITES):
            continue
        name, start, end = scopes(number)
        site = Site(rel, name, number, line.strip(), "shell")
        out.append((site, (rel, name, start), start, end, text))
    return out


def _step_scopes(lines: list):
    starts = [(i, m.group("name")) for i, line in enumerate(lines, start=1)
              if (m := _STEP_NAME.match(line))]

    def scope(number: int):
        found = ("<workflow>", 1, starts[0][0] - 1 if starts else len(lines))
        for index, (start, name) in enumerate(starts):
            if start <= number:
                end = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
                found = (name, start, end)
        return found

    return scope


def _function_scopes(lines: list):
    functions: list = []
    for i, line in enumerate(lines, start=1):
        match = _SHELL_FUNCTION.match(line)
        if not match:
            continue
        indent = match.group("indent")
        end = len(lines)
        for j in range(i + 1, len(lines) + 1):
            if re.fullmatch(re.escape(indent) + r"\}\s*", lines[j - 1]):
                end = j
                break
        functions.append((match.group("name"), i, end))

    def scope(number: int):
        inside = [f for f in functions if f[1] <= number <= f[2]]
        return max(inside, key=lambda f: f[1]) if inside else ("<script>", 1, len(lines))

    return scope


def _shell_sites(root: str) -> list:
    out: list = []
    for path in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        text = step_shell.workflow_source(os.path.abspath(path), Path(root))
        out += _shell_lines(rel, text, _step_scopes(text.splitlines()))
    delegated = _delegated(root)
    for path in sorted(glob.glob(os.path.join(root, "scripts", "*.sh"))):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel in delegated:
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        out += _shell_lines(rel, text, _function_scopes(text.splitlines()))
    return out


def _with_contexts(found: list) -> list:
    """Each site's `context`: its scope's text when the scope holds one site,
    else a window around it that never crosses another site in the scope."""
    groups: dict = {}
    for entry in found:
        groups.setdefault(entry[1], []).append(entry)
    sites: list = []
    for entries in groups.values():
        entries.sort(key=lambda e: e[0].line)
        _, _, start, end, text = entries[0]
        lines = text.splitlines()
        for index, (site, _, _, _, _) in enumerate(entries):
            low, high = start, end
            if len(entries) > 1:
                previous = entries[index - 1][0].end_line if index else start - 1
                following = entries[index + 1][0].line if index + 1 < len(entries) else end + 1
                low = max(start, site.line - _LOOKBACK, previous + 1)
                high = min(end, site.end_line + _LOOKAHEAD, following - 1)
            site.context = "\n".join(lines[low - 1:high])
            sites.append(site)
    return sorted(sites, key=lambda s: (s.file, s.line))


def discover(root: str | None = None) -> list:
    """Every site in the tree that applies the hold, with its scope and text."""
    root = root or ROOT
    return _with_contexts(_python_sites(root) + _shell_sites(root))


def row_matches(row: dict, site: Site) -> bool:
    """Does this registry row name this site — file, scope, anchor?"""
    anchor = row.get("anchor") or ""
    return (bool(anchor) and row.get("file") == site.file
            and row.get("scope") == site.scope and anchor in site.context)


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #

_LABEL_WRITES = ("add_label", "add-label", "hold.apply", "hold.py apply")


def _receipt_corpus(root: str) -> str:
    """Every text under `scripts/` and `.github/workflows/*.yml`: where a
    `tried_first` receipt must occur, or nothing records the attempt."""
    texts: list = []
    paths = glob.glob(os.path.join(root, "scripts", "**", "*"), recursive=True)
    paths += glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))
    for path in sorted(paths):
        if "__pycache__" in path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                texts.append(fh.read())
        except (OSError, UnicodeDecodeError):
            continue
    return "\n\0\n".join(texts)


def _vocabulary_problems(doc: dict) -> list:
    found: list = []
    for what, have, want in (("reasons", reasons(doc), REASONS),
                             ("lift_kinds", lift_kinds(doc), LIFT_KINDS),
                             ("readers", readers(doc), READERS)):
        if tuple(have) != want:
            found.append(f"config/holds.json `{what}` is {have}, the contract's is {list(want)}")
    for reason, entry in (doc.get("reasons") or {}).items():
        want = CONTRACT_LIFTS.get(reason)
        if want and (entry or {}).get("lifts") != want:
            found.append(f"config/holds.json `reasons.{reason}` lifts by "
                         f"{(entry or {}).get('lifts')!r}; the contract fixes {want!r}")
    return found


def _tried_first_problems(name: str, tried, corpus: str) -> list:
    if isinstance(tried, str):
        rest = tried[len("none — "):].strip() if tried.startswith("none — ") else ""
        return [] if rest else [
            f"{name}: tried_first {tried!r} is neither a step and receipt nor "
            "`none — <reason>`"]
    if not isinstance(tried, dict) or not (tried.get("step") or "").strip() \
            or not (tried.get("receipt") or "").strip():
        return [f"{name}: tried_first must be {{\"step\", \"receipt\"}} or `none — <reason>`"]
    if tried["receipt"] not in corpus:
        return [f"{name}: its tried_first receipt {tried['receipt']!r} occurs nowhere "
                "under scripts/ or .github/workflows/ — no row may name an attempt "
                "nothing records"]
    return []


def problems(doc: dict | None = None, root: str | None = None) -> list:
    """Everything wrong with the registry against the tree at `root`."""
    doc = doc if doc is not None else load()
    root = root or ROOT
    found = _vocabulary_problems(doc)
    sites = discover(root)
    corpus = _receipt_corpus(root)
    rows = doc.get("sites") or []
    for index, row in enumerate(rows):
        anchor = row.get("anchor") or ""
        name = (f"row {index} ({row.get('file')} · {row.get('scope')} · "
                f"anchor {anchor!r})")
        if not row.get("file") or not row.get("scope") or not anchor:
            found.append(f"{name} names no file, scope or anchor")
        if any(word in anchor for word in _LABEL_WRITES):
            found.append(f"{name}: the anchor is a label-write line — it must be "
                         "a receipt phrase or a constant the scope names, so a "
                         "swap to hold.apply changes no row")
        if not row.get("readers"):
            found.append(f"{name} names no readers")
        for reader in row.get("readers") or []:
            if reader not in READERS:
                found.append(f"{name} names reader {reader!r} outside the vocabulary")
        if not row.get("reasons"):
            found.append(f"{name} carries no reason")
        for entry in row.get("reasons") or []:
            reason = entry.get("reason")
            lifts = entry.get("lifts")
            if reason not in REASONS:
                found.append(f"{name} names reason {reason!r} outside the vocabulary")
            if not lifts:
                found.append(f"{name} omits the lift kind of {reason!r}")
            elif lifts not in LIFT_KINDS:
                found.append(f"{name} names lift kind {lifts!r} outside the vocabulary")
            elif CONTRACT_LIFTS.get(reason) and lifts != CONTRACT_LIFTS[reason]:
                found.append(f"{name} carries {reason} with lifts={lifts}; the "
                             f"contract fixes lifts={CONTRACT_LIFTS[reason]}")
            lifter = LIFTERS.get(reason)
            if lifter and lifter in (row.get("readers") or []):
                found.append(f"{name} carries {reason} and names {lifter} as a "
                             f"reader — {lifter} lifts {reason}, it never stands "
                             "down for it")
            found += _tried_first_problems(name, entry.get("tried_first"), corpus)
        hits = [s for s in sites if row_matches(row, s)]
        if len(hits) != 1:
            found.append(f"{name} matches {len(hits)} site(s), not one — a row "
                         "matching nothing has outlived its site, and one "
                         "matching two names a site nobody chose")
    for site in sites:
        hits = [r for r in rows if row_matches(r, site)]
        if not hits:
            found.append(f"{site.where} ({site.scope}) applies the hold and has "
                         "no row in config/holds.json — add one: its reason, its "
                         "lift kind, its readers and what is tried first")
        elif len(hits) > 1:
            found.append(f"{site.where} ({site.scope}) is matched by {len(hits)} rows")
    return found


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("card")
    p_apply.add_argument("--reason", required=True)
    p_apply.add_argument("--at", default="none")
    p_apply.add_argument("--by", required=True)
    p_lift = sub.add_parser("lift")
    p_lift.add_argument("card")
    p_lift.add_argument("--because", required=True)
    p_lift.add_argument("--by", required=True)
    p_reason = sub.add_parser("reason")
    p_reason.add_argument("card")
    sub.add_parser("check")
    args = parser.parse_args(argv)

    if args.command == "apply":
        try:
            stamp_line(args.reason, args.at, args.by)
        except ValueError as exc:
            print(f"hold apply {args.card}: {exc} — nothing written", file=sys.stderr)
            return 2
        try:
            apply(args.card, args.reason, args.at, args.by)
        except Exception as exc:  # any Linear failure
            print(f"hold apply {args.card}: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "lift":
        if args.because not in LIFT_BECAUSE:
            print(f"hold lift {args.card}: because={args.because!r} is not a lift "
                  "kind, card-closed or operator — nothing written", file=sys.stderr)
            return 2
        try:
            lift(args.card, args.because, args.by)
        except ValueError as exc:
            print(f"hold lift {args.card}: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # any Linear failure
            print(f"hold lift {args.card}: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "reason":
        # Exit 0 in every case: the reader is a workflow step that must not
        # fail on it (DRE-6247). An unread card prints nothing.
        try:
            labels, bodies = _read_card(args.card)
        except (Exception, SystemExit) as exc:
            print(f"hold reason {args.card}: could not read the card ({exc})",
                  file=sys.stderr)
            return 0
        reason = reason_of(labels, bodies)
        if reason:
            print(reason)
        return 0

    found = problems(load(), root=ROOT)
    for problem in found:
        print(f"  [FAIL] {problem}")
    print(f"{len(discover(ROOT))} hold writer site(s), "
          f"{len(load().get('sites') or [])} row(s), {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
