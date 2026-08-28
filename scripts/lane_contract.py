#!/usr/bin/env python3
"""The lane contract, as data (DRE-2726).

`config/lane-contract.json` declares, per lane, the entrance condition, the exit
condition, the permitted writers and the evidence that justifies occupancy. This
module is the only reader: the guard (`lane_scope.py`) takes its flow from here,
the sweep (`reconcile.py`) takes its stall windows from here, `docs/lane-contract.md`
is RENDERED from here, and the integration harness asserts the live board against
it on every trunk commit.

Then the document cannot drift from the enforcement, because they are the same
object — the house rule `make check-channel-fleet` already runs on: *an
enumeration of a derivable set drifts the moment the set changes.*

## enforced_from — why every clause carries a phase

Most of the contract is enforced by mechanisms Wave 1.5 builds in Phase 5. A
Phase-2 harness that asserted the whole contract against the live board would
either fail red on every card for three phases, or silently assert only the
subset that exists — and a coverage claim resting on an assertion the harness
never made is worse than no claim at all.

So each clause carries `enforced_from: <phase>` and this module does two things
with it, of which the second is the load-bearing half:

* a clause whose phase has NOT shipped is **skipped**, and reported as skipped;
* a clause whose phase HAS shipped with nothing implementing it **fails**.

That turns the contract from a description into a schedule that checks itself.

## Unknown is never a pass

A rule whose input could not be read (the console lives in another repository)
is reported `unevaluated`, never `pass`. Unevaluated fails immediately unless
the rule names an `unevaluated_fails_from` phase it is still short of — and it
fails hard from that phase on. Console-honesty rule 2: report unknown as
unknown, never resolve it as agreement.

CLI:

    python3 scripts/lane_contract.py render          # rewrite docs/lane-contract.md
    python3 scripts/lane_contract.py check           # rules that need no network
    python3 scripts/lane_contract.py check --live    # ...plus the live Linear board
"""

from __future__ import annotations

import ast
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
CONTRACT_PATH = os.path.join(ROOT, "config", "lane-contract.json")

# Every clause a lane owes. The card names all four; a lane missing one is a
# lane whose occupancy is only partly justifiable.
CLAUSE_KINDS = ("entrance", "exit", "writers", "evidence")

# Directories the vocabulary scan walks by default: the pipeline's own code and
# its workflows. Derived, never enumerated per lane.
VOCABULARY_PATHS = (
    os.path.join(ROOT, "scripts"),
    os.path.join(ROOT, ".github", "workflows"),
)


class ContractError(RuntimeError):
    """The contract file is malformed. Raised rather than defaulted: a contract
    that silently loses a field is a contract that silently stops enforcing."""


class UnknownLane(Exception):
    """A lane the contract does not carry, asked for by name."""


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #

_CACHE: dict = {}


def load(path: str | None = None) -> dict:
    """Parse and validate the contract. Cached per path — this is imported on
    the hot path (linear_ops → lane_scope) and re-reading it per call would put
    a file read inside every card write."""
    path = path or CONTRACT_PATH
    if path not in _CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            raise ContractError(f"cannot read the lane contract at {path}: {e}") from e
        _validate(doc, path)
        _CACHE[path] = doc
    return _CACHE[path]


def _clean(mapping: dict) -> dict:
    """A mapping with its `_readme` prose stripped. JSON carries no comments, so
    the file explains itself in reserved keys; no reader should see them."""
    return {k: v for k, v in (mapping or {}).items() if not k.startswith("_")}


def _validate(doc: dict, path: str) -> None:
    order = tuple(doc.get("phases", {}).get("order") or ())
    if not order:
        raise ContractError(f"{path}: phases.order is empty")
    if doc.get("phases", {}).get("current") not in order:
        raise ContractError(
            f"{path}: phases.current {doc.get('phases', {}).get('current')!r} "
            f"is not one of {order}"
        )
    names = set()
    for lane in doc.get("lanes") or ():
        name = lane.get("name")
        if not name:
            raise ContractError(f"{path}: a lane has no name")
        if name in names:
            raise ContractError(f"{path}: lane {name!r} is declared twice")
        names.add(name)
        status = lane.get("status")
        if status not in ("live", "retiring"):
            raise ContractError(
                f"{path}: lane {name!r} has status {status!r}; expected "
                "'live' or 'retiring'"
            )
        if status == "retiring":
            for key in ("retired_by", "reason", "board_action"):
                if not (lane.get(key) or "").strip():
                    raise ContractError(
                        f"{path}: retiring lane {name!r} must say {key!r} — a "
                        "retirement with no recorded step never finishes"
                    )
            continue
        if lane.get("segment") not in ("planning", "work", "off-flow"):
            raise ContractError(
                f"{path}: lane {name!r} has segment {lane.get('segment')!r}"
            )
        if lane["segment"] == "off-flow" and not (
            lane.get("off_flow_reason") or ""
        ).strip():
            raise ContractError(
                f"{path}: off-flow lane {name!r} must say why it is off the flow"
            )
        for kind in CLAUSE_KINDS:
            clause = (lane.get("clauses") or {}).get(kind)
            if clause is None:
                raise ContractError(f"{path}: lane {name!r} has no {kind} clause")
            _validate_clause(clause, f"{name}.{kind}", order, path)
    for rule in doc.get("rules") or ():
        _validate_clause(rule, rule.get("id", "<unnamed rule>"), order, path)


def _validate_clause(clause: dict, ident: str, order: tuple, path: str) -> None:
    if not (clause.get("text") or "").strip():
        raise ContractError(f"{path}: clause {ident} says nothing")
    phase = clause.get("enforced_from")
    if not phase:
        raise ContractError(
            f"{path}: clause {ident} carries no enforced_from — the harness "
            "cannot tell an unshipped promise from an unenforced obligation"
        )
    if phase not in order:
        raise ContractError(
            f"{path}: clause {ident} is enforced_from {phase!r}, not one of {order}"
        )


# --------------------------------------------------------------------------- #
# accessors                                                                    #
# --------------------------------------------------------------------------- #


def phase_order(contract: dict | None = None) -> tuple:
    return tuple((contract or load())["phases"]["order"])


def current_phase(contract: dict | None = None) -> str:
    return (contract or load())["phases"]["current"]


def phase_has_shipped(phase: str, contract: dict | None = None) -> bool:
    """True when `phase` is at or before the phase the wave has reached."""
    order = phase_order(contract)
    return order.index(phase) <= order.index(current_phase(contract))


def lanes(status: str = "live", contract: dict | None = None) -> tuple:
    """Every lane with `status`, in the file's own order — which IS the flow
    order for the planning and work segments."""
    return tuple(
        lane for lane in (contract or load())["lanes"] if lane.get("status") == status
    )


def lane_names(status: str = "live", contract: dict | None = None) -> tuple:
    return tuple(lane["name"] for lane in lanes(status, contract))


def lane(name: str, status: str = "live", contract: dict | None = None) -> dict:
    for candidate in lanes(status, contract):
        if candidate["name"] == name:
            return candidate
    raise UnknownLane(f"{name!r} is not a {status} lane in {CONTRACT_PATH}")


def flow_lanes(contract: dict | None = None) -> tuple:
    """The lanes a card actually travels, in order: the planning segment, then
    the work segment. Off-flow lanes are excluded by their own declaration."""
    return tuple(
        lane["name"]
        for lane in lanes("live", contract)
        if lane["segment"] in ("planning", "work")
    )


def off_flow(contract: dict | None = None) -> dict:
    """name → the reason the lane sits off the flow."""
    return {
        lane["name"]: lane["off_flow_reason"]
        for lane in lanes("live", contract)
        if lane["segment"] == "off-flow"
    }


def aliases(contract: dict | None = None) -> dict:
    """Retired board names accepted on input, mapped to the lane they mean.

    Declared as a LIST of entries rather than a bare mapping so each retired
    name and its shim marker sit on one line — DRE-2722's repo-wide sweep for
    the old name exempts a line by the marker it carries, never by its path.
    """
    entries = (contract or load()).get("aliases", {}).get("entries") or ()
    return {entry["from"]: entry["to"] for entry in entries}


def planning_exit(contract: dict | None = None) -> tuple:
    exit_ = (contract or load())["planning_exit"]
    return (exit_["from"], exit_["to"])


def stale_minutes(contract: dict | None = None) -> dict:
    """lane → its stall window, for every lane that declares one."""
    return {
        lane["name"]: lane["stale_minutes"]
        for lane in lanes("live", contract)
        if lane.get("stale_minutes") is not None
    }


def writers(contract: dict | None = None) -> dict:
    return _clean((contract or load()).get("writers"))


def console(contract: dict | None = None) -> dict:
    return _clean((contract or load()).get("console"))


@dataclass(frozen=True)
class Clause:
    """One checkable promise: a global rule, or one of a lane's four clauses."""

    id: str
    kind: str  # "rule" | entrance | exit | writers | evidence
    lane: Optional[str]
    text: str
    enforced_from: str
    assertion: Optional[str]
    unevaluated_fails_from: Optional[str] = None
    who: tuple = ()
    pending: str = ""


def clauses(contract: dict | None = None) -> tuple:
    """Every clause the harness could ever assert: the global rules first, then
    each lane's four, in flow order."""
    doc = contract or load()
    out = []
    for rule in doc.get("rules") or ():
        out.append(
            Clause(
                id=rule["id"],
                kind="rule",
                lane=None,
                text=rule["text"],
                enforced_from=rule["enforced_from"],
                assertion=rule.get("assertion"),
                unevaluated_fails_from=rule.get("unevaluated_fails_from"),
                pending=rule.get("pending", ""),
            )
        )
    for lane_doc in lanes("live", doc):
        for kind in CLAUSE_KINDS:
            clause = lane_doc["clauses"][kind]
            out.append(
                Clause(
                    id=f"{lane_doc['name']}.{kind}",
                    kind=kind,
                    lane=lane_doc["name"],
                    text=clause["text"],
                    enforced_from=clause["enforced_from"],
                    assertion=clause.get("assertion"),
                    unevaluated_fails_from=clause.get("unevaluated_fails_from"),
                    who=tuple(clause.get("who") or ()),
                    pending=clause.get("pending", ""),
                )
            )
    return tuple(out)


# --------------------------------------------------------------------------- #
# the conformance report                                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    clause_id: str
    status: str  # "pass" | "fail" | "skipped" | "unevaluated"
    detail: str


@dataclass
class Report:
    findings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.status == "fail" for f in self.findings)

    def failures(self) -> list:
        return [f for f in self.findings if f.status == "fail"]

    def asserted(self) -> list:
        return [f for f in self.findings if f.status in ("pass", "fail")]

    def skipped(self) -> list:
        return [f for f in self.findings if f.status == "skipped"]

    def unevaluated(self) -> list:
        return [f for f in self.findings if f.status == "unevaluated"]

    def summary(self) -> str:
        return (
            f"{len(self.asserted())} asserted, {len(self.failures())} failed, "
            f"{len(self.skipped())} skipped (phase not shipped), "
            f"{len(self.unevaluated())} unevaluated"
        )

    def text(self) -> str:
        lines = [self.summary()]
        for f in self.findings:
            if f.status == "pass":
                continue
            lines.append(f"  [{f.status.upper():11}] {f.clause_id}: {f.detail}")
        return "\n".join(lines)


@dataclass
class _Inputs:
    contract: dict
    board: Optional[dict]      # lane name → number of cards in it
    console: Optional[list]    # the console's own state vocabulary
    vocabulary: Optional[set]  # lane names the pipeline's source names
    root: str
    clause: Optional[Clause] = None


# Assertion registry. `requires` names the input an assertion cannot run
# without; when that input is None the clause is unevaluated rather than
# guessed. A clause whose phase has shipped and whose assertion is not
# registered here FAILS — that is the half the amendment exists to protect.
ASSERTIONS: dict = {}


def assertion(name: str, requires: str | None = None) -> Callable:
    def register(fn: Callable) -> Callable:
        fn.requires = requires
        ASSERTIONS[name] = fn
        return fn

    return register


@assertion("board.every_state_is_named", requires="board")
def _every_state_is_named(inp: _Inputs) -> list:
    named = set(lane_names("live", inp.contract)) | set(
        lane_names("retiring", inp.contract)
    )
    return [
        f"Linear carries the state {state!r}, which the lane contract does not "
        "name — add it to config/lane-contract.json or archive it on the board"
        for state in sorted(set(inp.board) - named)
    ]


@assertion("board.every_lane_exists", requires="board")
def _every_lane_exists(inp: _Inputs) -> list:
    return [
        f"the contract names the lane {name!r}, which does not exist in Linear"
        for name in lane_names("live", inp.contract)
        if name not in inp.board
    ]


@assertion("board.retiring_lane_is_empty", requires="board")
def _retiring_lane_is_empty(inp: _Inputs) -> list:
    out = []
    for lane_doc in lanes("retiring", inp.contract):
        count = inp.board.get(lane_doc["name"])
        if count:
            out.append(
                f"the retiring lane {lane_doc['name']!r} still holds {count} "
                "card(s) — nothing writes to it any more, so they are stranded"
            )
    return out


@assertion("board.retired_entry_is_deleted", requires="board")
def _retired_entry_is_deleted(inp: _Inputs) -> list:
    return [
        f"the contract still carries the retiring entry {lane_doc['name']!r}, "
        "and Linear no longer has that state — the board caught up; delete the "
        "entry from config/lane-contract.json"
        for lane_doc in lanes("retiring", inp.contract)
        if lane_doc["name"] not in inp.board
    ]


@assertion("console.state_lists_carry_every_lane", requires="console")
def _console_state_lists(inp: _Inputs) -> list:
    live = set(lane_names("live", inp.contract))
    seen = set(inp.console)
    out = [
        f"the lane {name!r} is missing from the console's state lists — the "
        "console cannot show cards it does not know about"
        for name in sorted(live - seen)
    ]
    out += [
        f"the console's state lists carry {name!r}, which is not a lane — a "
        "column that can never fill, or a word nothing writes"
        for name in sorted(seen - live)
    ]
    return out


@assertion("pipeline.vocabulary_is_contract_lanes", requires="vocabulary")
def _pipeline_vocabulary(inp: _Inputs) -> list:
    live = set(lane_names("live", inp.contract))
    return [
        f"the pipeline's own source still names the state {name!r}, which is "
        "not a live lane — a write into it fails at the Linear call"
        for name in sorted(set(inp.vocabulary) - live)
    ]


@assertion("lane.writers_exist")
def _lane_writers_exist(inp: _Inputs) -> list:
    known = writers(inp.contract)
    out = []
    for key in inp.clause.who:
        entry = known.get(key)
        if entry is None:
            out.append(
                f"lane {inp.clause.lane!r} permits the writer {key!r}, which the "
                "contract's writer glossary does not define"
            )
            continue
        path = entry.get("path")
        if path and not os.path.exists(os.path.join(inp.root, path)):
            out.append(
                f"the writer {key!r} points at {path}, which does not exist — a "
                f"lane whose writer was deleted cannot be left legally"
            )
    if not inp.clause.who:
        out.append(f"lane {inp.clause.lane!r} permits no writer at all")
    return out


def check(
    *,
    contract: dict | None = None,
    board: dict | None = None,
    console: list | None = None,
    vocabulary: Iterable | None = None,
    root: str = ROOT,
) -> Report:
    """Run every clause the current phase has reached, and report the rest.

    `board` is lane name → card count, read live from Linear. `console` is the
    console's own state vocabulary, or None when it could not be reached.
    `vocabulary` is the set of lane names the pipeline's own source mentions.
    """
    doc = contract if contract is not None else load()
    inputs = _Inputs(
        contract=doc,
        board=board,
        console=list(console) if console is not None else None,
        vocabulary=set(vocabulary) if vocabulary is not None else None,
        root=root,
    )
    report = Report()
    for clause in clauses(doc):
        report.findings.extend(_run_clause(clause, inputs))
    return report


def _run_clause(clause: Clause, inputs: _Inputs) -> list:
    if not phase_has_shipped(clause.enforced_from, inputs.contract):
        note = f" ({clause.pending})" if clause.pending else ""
        return [
            Finding(
                clause.id,
                "skipped",
                f"promised from phase {clause.enforced_from}, which has not "
                f"shipped{note}",
            )
        ]

    fn = ASSERTIONS.get(clause.assertion) if clause.assertion else None
    if fn is None:
        return [
            Finding(
                clause.id,
                "fail",
                f"enforced_from phase {clause.enforced_from}, which has shipped, "
                f"and it is not enforced: assertion {clause.assertion!r} is not "
                "implemented",
            )
        ]

    required = getattr(fn, "requires", None)
    if required and getattr(inputs, required) is None:
        fails_from = clause.unevaluated_fails_from
        if fails_from and not phase_has_shipped(fails_from, inputs.contract):
            return [
                Finding(
                    clause.id,
                    "unevaluated",
                    f"the {required} could not be read; this becomes a failure "
                    f"from phase {fails_from}",
                )
            ]
        return [
            Finding(
                clause.id,
                "fail",
                f"the {required} could not be read, so the clause could not be "
                "asserted — an unevaluated clause is never a pass",
            )
        ]

    scoped = _Inputs(**{**inputs.__dict__, "clause": clause})
    failures = fn(scoped)
    if failures:
        return [Finding(clause.id, "fail", detail) for detail in failures]
    return [Finding(clause.id, "pass", clause.text)]


# --------------------------------------------------------------------------- #
# the vocabulary scan                                                          #
# --------------------------------------------------------------------------- #


def scan_vocabulary(paths: Iterable[str], known: Iterable[str]) -> set:
    """Which of `known` the pipeline's own source actually names.

    Python is read as an AST so only real string constants count — a comment
    recording the incident a retired lane was named in is history, not a write.
    YAML is read line by line with comment lines dropped, for the same reason:
    the workflows' shell lines are where a lane name becomes a Linear call.
    """
    wanted = [name for name in known]
    found = set()
    for base in paths:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                path = os.path.join(dirpath, filename)
                if filename.endswith(".py"):
                    found |= _scan_python(path, wanted)
                elif filename.endswith((".yml", ".yaml")):
                    found |= _scan_yaml(path, wanted)
    return found


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _scan_python(path: str, wanted: list) -> set:
    source = _read(path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for name in wanted:
                if name in node.value:
                    found.add(name)
    return found


def _scan_yaml(path: str, wanted: list) -> set:
    found = set()
    for line in _read(path).splitlines():
        if line.lstrip().startswith("#"):
            continue
        for name in wanted:
            if name in line:
                found.add(name)
    return found


def pipeline_vocabulary(contract: dict | None = None) -> set:
    """The lane names this checkout's own scripts and workflows name."""
    doc = contract or load()
    known = set(lane_names("live", doc)) | set(lane_names("retiring", doc))
    return scan_vocabulary(VOCABULARY_PATHS, known)


# --------------------------------------------------------------------------- #
# the rendered document                                                        #
# --------------------------------------------------------------------------- #

DOC_PATH = os.path.join(ROOT, "docs", "lane-contract.md")


def _phase_label(phase: str, contract: dict) -> str:
    shipped = phase_has_shipped(phase, contract)
    return f"Phase {phase} — {'live' if shipped else 'promised'}"


def _clause_rows(lane_doc: dict, contract: dict) -> list:
    rows = []
    for kind in CLAUSE_KINDS:
        clause = lane_doc["clauses"][kind]
        text = clause["text"]
        if kind == "writers" and clause.get("who"):
            text += "  \nPermitted writers: " + ", ".join(
                f"`{w}`" for w in clause["who"]
            )
        if clause.get("pending") and not phase_has_shipped(
            clause["enforced_from"], contract
        ):
            text += f"  \n_Waiting on: {clause['pending']}._"
        rows.append((kind, text, _phase_label(clause["enforced_from"], contract)))
    return rows


def render_markdown(contract: dict | None = None) -> str:
    doc = contract if contract is not None else load()
    out = []
    w = out.append
    w("# The lane contract")
    w("")
    w("<!-- GENERATED FILE — do not edit. Source: config/lane-contract.json.")
    w("     Regenerate with `python3 scripts/lane_contract.py render`. -->")
    w("")
    w(
        "Per lane: the entrance condition, the exit condition, the permitted "
        "writers, and the evidence that justifies occupancy. This document is "
        "rendered from the same file the guard, the sweep and the integration "
        "harness read, so it cannot drift from the enforcement."
    )
    w("")
    w(
        f"Wave phase reached: **{current_phase(doc)}** — "
        f"{doc['phases']['titles'].get(current_phase(doc), '')}. A clause marked "
        "**live** is asserted by the harness on every trunk commit. A clause "
        "marked **promised** is skipped until its phase ships, and fails the "
        "harness the moment its phase has passed with nothing enforcing it."
    )
    w("")

    w("## The flow")
    w("")
    w("| # | Lane | Segment | Stall window |")
    w("| --- | --- | --- | --- |")
    for i, name in enumerate(flow_lanes(doc), 1):
        lane_doc = lane(name, contract=doc)
        window = lane_doc.get("stale_minutes")
        w(
            f"| {i} | {name} | {lane_doc['segment']} | "
            f"{str(window) + ' min' if window else '—'} |"
        )
    w("")
    exit_from, exit_to = planning_exit(doc)
    w(
        f"Planning exit is the transition **{exit_from} → {exit_to}** — where the "
        "second critic writes its verdict, and the boundary the guard's scope is "
        "derived from."
    )
    w("")

    w("## Off the flow")
    w("")
    w("| Lane | Why it is off the flow |")
    w("| --- | --- |")
    for name, reason in off_flow(doc).items():
        w(f"| {name} | {reason} |")
    w("")

    w("## The lanes")
    w("")
    for lane_doc in lanes("live", doc):
        w(f"### {lane_doc['name']}")
        w("")
        if lane_doc.get("note"):
            w(f"> {lane_doc['note']}")
            w("")
        w("| Clause | What it requires | Enforcement |")
        w("| --- | --- | --- |")
        for kind, text, phase in _clause_rows(lane_doc, doc):
            w(f"| **{kind}** | {text} | {phase} |")
        w("")

    retiring = lanes("retiring", doc)
    if retiring:
        w("## Retiring")
        w("")
        w(
            "Lanes the pipeline no longer writes to, still on the board until the "
            "workspace apply archives them. The harness fails while one still "
            "holds a card, and fails again once the state is gone and the entry "
            "below has not been deleted."
        )
        w("")
        for lane_doc in retiring:
            w(f"### {lane_doc['name']} — retired by {lane_doc['retired_by']}")
            w("")
            w(lane_doc["reason"])
            w("")
            w(f"**Board step:** {lane_doc['board_action']}")
            w("")

    w("## The rules the harness asserts")
    w("")
    w("| Rule | What it means | Enforcement |")
    w("| --- | --- | --- |")
    for rule in doc.get("rules") or ():
        text = rule["text"]
        if rule.get("why"):
            text += f"  \n_{rule['why']}_"
        if rule.get("pending") and not phase_has_shipped(
            rule["enforced_from"], doc
        ):
            text += f"  \n_Waiting on: {rule['pending']}._"
        w(f"| `{rule['id']}` | {text} | {_phase_label(rule['enforced_from'], doc)} |")
    w("")

    w("## Writers")
    w("")
    w("| Writer | What it is | Where it lives |")
    w("| --- | --- | --- |")
    for key, entry in writers(doc).items():
        where = f"`{entry['path']}`" if entry.get("path") else (entry.get("note") or "—")
        w(f"| `{key}` | {entry['what']} | {where} |")
    w("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _live_board() -> dict:
    """lane name → number of cards, read from Linear. Read-only: one query, no
    mutation, and nothing about a card leaves this function."""
    import linear_ops

    query = """
    query {
      workflowStates(first: 100) {
        nodes { name issues(first: 250) { nodes { id } } }
      }
    }
    """
    data = linear_ops.gql(query)
    return {
        node["name"]: len(node["issues"]["nodes"])
        for node in data["workflowStates"]["nodes"]
    }


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "check"

    if command == "render":
        rendered = render_markdown()
        with open(DOC_PATH, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"wrote {DOC_PATH} ({len(rendered.splitlines())} lines)")
        return 0

    if command != "check":
        print(f"usage: {sys.argv[0]} [render|check [--live]]", file=sys.stderr)
        return 2

    # Read the board whenever we can. `--live` demands it (and a missing key is
    # then a hard error, not a quiet downgrade to a check that saw nothing).
    live = "--live" in argv
    if live or os.environ.get("LINEAR_API_KEY"):
        board = _live_board()
    else:
        board = None
        print(
            "note: LINEAR_API_KEY unset — the board rules cannot be evaluated, "
            "and an unevaluated rule is reported as a failure, never a pass"
        )
    report = check(board=board, vocabulary=pipeline_vocabulary())
    print(report.text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
