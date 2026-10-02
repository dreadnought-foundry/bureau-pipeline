#!/usr/bin/env python3
"""Every Green Light row is a declared arrival (DRE-5282, epic DRE-5268).

Green Light is the CEO's "needs you" queue, and the epic this belongs to fixes
what may sit in it: a plan BOTH critics passed, the planner's business question
with a recommendation, a build's escalation to a person, or (while the CEO's
acceptance of kind (c) stands) an approved epic queued under the cap. A row
asking him to look again, re-run something, settle a critic loop or move a
card nobody has read is the failure. Until DRE-5281 three `plan.yml` sites
wrote Green Light for a critic outcome, and until DRE-5286 the sweep wrote it
for a stalled Planning card; nothing would have said so if another appeared.

`ready_lane_writers.py check` proves the matching ABSENCE for the ready-work
lanes by discovering every lane write rather than listing them. This is the
same proof, on the same seam, for Green Light. It discovers nothing itself:

* the write sites are `ready_lane_writers.writes()` — the one door
  (`linear_ops.py`'s `stateId` functions), the same static resolution, the
  same `unseen_writers()` boundary;
* the callers of a lane-writing function are `lane_callers.callers_of()`.

It then reconciles what they found, both ways, against the `arrivals` list
DRE-5275 wrote on Green Light's `entrance` in `config/lane-contract.json`.

## The rules

1. **Every discovered write into the lane is an arrival.** A write is located
   at its UNIT — `<file>#<enclosing def>` for a script, `<file>#<step name>`
   for a workflow, the grammar `where` and `callers` use — and a unit no record
   names fails BY LOCATION.
2. **Every arrival is a discovered write.** A record whose `where` holds no
   write into the lane fails BY NAME: the step or function is gone, or no
   longer writes there, and this cannot tell which.
3. **Every `kind` is in the vocabulary**, read off the entrance's `kinds` —
   never counted here. One outside it fails BY WORD.
4. **An unread destination is a problem.** `writes()` returns `lane=None` for a
   call site whose destination it cannot read. The sibling passes those for a
   writer permitted in every ready-work lane, because wherever such a write
   goes it was allowed to go. That escape does not carry here: Green Light is
   not a ready-work lane, so an unread write could be a Green Light write and
   nothing could say. Each one names its site and the hook that resolves it,
   `ready_lane_writers.DESTINATIONS_HOOK`.
5. **Each site's own gate is read, never the record's word for it.**
   * `passed-plan` in a workflow: the step's `if:` carries BOTH
     `steps.post1.outputs.result == 'PASS'` (the second critic's decision word)
     and `steps.post1.outputs.pre_passed == 'true'` (the first critic's last
     word was a proceed). `action == 'proceed'` is not that gate — it covers a
     critic that never decided.
   * `queued-epic`: the step's shell adds the `epic-queued` label BEFORE it
     writes the lane.
   * `question`: the unit is `planning_escalation.py#escalate` and no other.
   * `agent-escalation`: `agent-task.yml#Report result to Linear`, whose shell
     posts the 🙋 escalation comment before the write, or
     `code_owner_hold.py#park`.
6. **A borrowed write is attributed to its caller.** The DRE-4124 stall exit
   reached Green Light through `planning_escalation.escalate`, whose own write
   is the planner's declared question site; a discovery reading write sites
   alone passed it. So for every record whose `where` is a Python function the
   callers `callers_of` finds are reconciled against the record's `callers`:
   one the record does not declare fails BY CALLER, a declared one no longer
   found fails BY NAME, and a file `callers_of` could not read is a problem,
   never a pass. One hop across modules: a handler that reaches the lane
   through a function in another module is that function's caller, and its
   own callers are reconciled on its own record (`planning_route.py#_cmd_exit`
   on `escalate`'s record; the two `plan.yml` steps that run `exit` on
   `_cmd_exit`'s).

## What this does not own

It declares no arrival and edits neither the lane contract nor the two
discovery modules. The card that lands a new site declares its record in the
same pull request (DRE-5136 declares `queued-epic`). A kind in the vocabulary
with no record and no write is not a problem.

## WHAT THIS CANNOT SEE

Whatever `ready_lane_writers.unseen_writers()` names (a hand move in the
Linear UI), and everything `ready_lane_writers` and `lane_callers` say they
cannot see in their own docstrings. It is a check on the source, not a refusal
at run time.

CLI:

    python3 scripts/green_light_rows.py check
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import code_owner_hold  # noqa: E402 — the merge gate's code-owner park (DRE-4341)
import lane_callers  # noqa: E402 — caller discovery (DRE-5345)
import lane_contract  # noqa: E402
import planner_score  # noqa: E402 — agent-task.yml's escalation receipt prefix
import planning_escalation  # noqa: E402 — the planner's question (DRE-2848)
import ready_lane_writers  # noqa: E402 — write discovery (DRE-2859)
import step_shell  # noqa: E402 — a moved step's shell, where it now lives (DRE-5220)

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

#: The second critic's decision word and the first critic's last word, both
#: outputs of the review route's `post1` step (DRE-5276, DRE-5280). A
#: `passed-plan` step's `if:` must carry both. Workflow-output strings: no
#: Python module owns them.
PASSED_PLAN_GATES = (
    "steps.post1.outputs.result == 'PASS'",
    "steps.post1.outputs.pre_passed == 'true'",
)

#: The label a queued epic carries (DRE-5275's contract clause). The literal is
#: the card's: `epic_cap.QUEUED_LABEL` names the same string, and this module
#: does not import `epic_cap`, because DRE-5129's queue write lands after this
#: check and a rule exercised only on throwaway copies until then must not need
#: that module.
QUEUED_LABEL = "epic-queued"

#: The one workflow unit an `agent-escalation` row may be written from, and
#: what its shell must post before the write: DRE-1655's escalate-by-exception
#: park. The step name is the unit `where` declares.
AGENT_ESCALATION_STEP = "agent-task.yml#Report result to Linear"

_STEP_NAME = re.compile(r"^\s*-\s+name:\s*(?P<name>.+?)\s*$")


def _unit_of_function(fn) -> str:
    """`<file>#<function>` for a module-level function, named off the module."""
    module = sys.modules[fn.__module__]
    return f"{os.path.basename(module.__file__)}#{fn.__name__}"


#: The planner's question site — the only unit a `question` row comes from.
QUESTION_SITE = _unit_of_function(planning_escalation.escalate)

#: The merge gate's code-owner park — the python `agent-escalation` site.
CODE_OWNER_SITE = _unit_of_function(code_owner_hold.park)


# --------------------------------------------------------------------------- #
# the contract                                                                 #
# --------------------------------------------------------------------------- #


class ContractError(ValueError):
    """The lane whose arrivals are declared could not be read."""


def _arrival_lanes(contract: dict | None) -> list:
    return [
        lane for lane in lane_contract.lanes(status="live", contract=contract)
        if "arrivals" in ((lane.get("clauses") or {}).get("entrance") or {})
    ]


def lane_name(contract: dict | None = None) -> str:
    """The lane this check is about: the one live lane whose entrance declares
    its arrivals. Read off the contract, never typed; zero or two is an error,
    not a guess."""
    found = _arrival_lanes(contract)
    if len(found) != 1:
        raise ContractError(
            "exactly one live lane's entrance must declare `arrivals`; the lane "
            f"contract has {[lane.get('name') for lane in found] or 'none'}"
        )
    return found[0]["name"]


def _entrance(contract: dict | None) -> dict:
    lane_name(contract)
    return _arrival_lanes(contract)[0]["clauses"]["entrance"]


def kinds(contract: dict | None = None) -> tuple:
    """The vocabulary of arrival kinds, as the contract states it today."""
    return tuple(_entrance(contract).get("kinds") or ())


def arrivals(contract: dict | None = None) -> list:
    """One record per way a row may be written into the lane."""
    return list(_entrance(contract).get("arrivals") or [])


# --------------------------------------------------------------------------- #
# units                                                                        #
# --------------------------------------------------------------------------- #


def short_unit(unit: str) -> str:
    """`callers_of`'s repo-relative unit in the contract's grammar:
    `scripts/code_owner_hold.py#_cmd_hold` → `code_owner_hold.py#_cmd_hold`."""
    file, _, rest = unit.partition("#")
    return f"{os.path.basename(file)}#{rest}"


def _split_where(where: str) -> tuple:
    file, _, rest = where.partition("#")
    return file, rest


def _is_function(where: str) -> bool:
    return _split_where(where)[0].endswith(".py")


def _workflow_lines(path: str, root: str) -> list:
    """The workflow as `writes()` read it: delegated steps read through."""
    return step_shell.workflow_source(os.path.abspath(path), root).splitlines()


def _python_unit(path: str, line: int) -> str:
    """The innermost def enclosing `line`, or the module unit."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return lane_callers.MODULE_UNIT
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.lineno <= line <= (node.end_lineno or node.lineno):
            if best is None or node.lineno >= best.lineno:
                best = node
    return best.name if best is not None else lane_callers.MODULE_UNIT


def _step_start(lines: list, line: int) -> tuple:
    """(index of the nearest `- name:` at or above 1-based `line`, its name)."""
    for index in range(min(line, len(lines)) - 1, -1, -1):
        match = _STEP_NAME.match(lines[index])
        if match:
            try:
                name = yaml.safe_load(match.group("name"))
            except yaml.YAMLError:
                name = match.group("name")
            return index, str(name)
    return None, None


def unit_of(write, root: str = ROOT) -> str:
    """Where a discovered write sits, in the contract's grammar."""
    file, _, line = write.where.rpartition(":")
    path = os.path.join(root, file)
    base = os.path.basename(file)
    if write.how == "workflow":
        try:
            _, name = _step_start(_workflow_lines(path, root), int(line))
        except (OSError, ValueError):
            name = None
        return f"{base}#{name if name is not None else '<no step>'}"
    return f"{base}#{_python_unit(path, int(line))}"


# --------------------------------------------------------------------------- #
# discovery, read                                                              #
# --------------------------------------------------------------------------- #


def _writes(root: str, contract: dict | None) -> tuple:
    return ready_lane_writers.writes(root, contract)


def green_light_writes(root: str = ROOT, contract: dict | None = None) -> list:
    """(write, unit) for every discovered write into the lane."""
    lane = lane_name(contract)
    return [(w, unit_of(w, root)) for w in _writes(root, contract) if w.lane == lane]


def _step(root: str, file: str, name: str):
    """The parsed step called `name` in workflow `file`, or None."""
    path = os.path.join(root, ".github", "workflows", file)
    try:
        doc = yaml.safe_load(step_shell.workflow_source(os.path.abspath(path), root))
    except (OSError, yaml.YAMLError):
        return None
    for job in ((doc or {}).get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if isinstance(step, dict) and str(step.get("name")) == name:
                return step
    return None


def _before_write(root: str, write) -> str:
    """The step's text from its `- name:` line up to the write line, as
    `writes()` read it — what the shell has done before the lane moves."""
    file, _, line = write.where.rpartition(":")
    lines = _workflow_lines(os.path.join(root, file), root)
    start, _ = _step_start(lines, int(line))
    if start is None:
        return ""
    return "\n".join(lines[start:int(line) - 1])


# --------------------------------------------------------------------------- #
# the rules                                                                    #
# --------------------------------------------------------------------------- #


def _gate_problems(record: dict, writes_here: list, root: str, lane: str) -> list:
    kind, where = record.get("kind"), record.get("where", "")
    file, unit = _split_where(where)
    out: list[str] = []
    if kind == "passed-plan" and not _is_function(where):
        if not writes_here:
            return out  # no write there: rule 2 names the site, once
        step = _step(root, file, unit)
        gate = " ".join(str((step or {}).get("if") or "").split())
        missing = [g for g in PASSED_PLAN_GATES if g not in gate]
        if missing:
            out.append(
                f"{where} is a passed-plan arrival, and its step's `if:` lacks "
                f"{' and '.join(f'`{g}`' for g in missing)} — a plan reaches "
                f"{lane} only on BOTH critics' pass; `action == 'proceed'` covers "
                "a critic that never decided"
            )
    elif kind == "queued-epic":
        label = re.compile(rf"\badd-label\b[^\n]*\b{re.escape(QUEUED_LABEL)}\b")
        for write in writes_here:
            if write.how != "workflow" or not label.search(_before_write(root, write)):
                out.append(
                    f"{where} ({write.where}) is a queued-epic arrival, and its "
                    f"step does not add `{QUEUED_LABEL}` before it writes {lane} — "
                    "a queued row is told apart from a decision only by that label"
                )
    elif kind == "question" and where != QUESTION_SITE:
        out.append(
            f"{where} is declared a 'question' arrival, and the only question "
            f"site is {QUESTION_SITE} — a business question reaches {lane} "
            "through the planner's escalation and nowhere else; a caller of it "
            "belongs on that record's `callers`"
        )
    elif kind == "agent-escalation":
        if where == AGENT_ESCALATION_STEP:
            receipt = planner_score.ESCALATION_RECEIPT_PREFIX
            for write in writes_here:
                if receipt not in _before_write(root, write):
                    out.append(
                        f"{where} ({write.where}) is an agent-escalation arrival, "
                        f"and its step does not post the escalation comment "
                        f"(`{receipt}`) before it writes {lane}"
                    )
        elif where != CODE_OWNER_SITE:
            out.append(
                f"{where} is declared an 'agent-escalation' arrival, and the "
                f"only agent-escalation sites are {AGENT_ESCALATION_STEP} and "
                f"{CODE_OWNER_SITE}"
            )
    return out


def _caller_problems(record: dict, root: str) -> list:
    where = record.get("where", "")
    file, function = _split_where(where)
    try:
        report = lane_callers.callers_of(f"scripts/{file}", function, root)
    except lane_callers.UnknownTarget:
        return []  # the site is gone: rule 2 names it
    out: list[str] = []
    found = {short_unit(c) for c in report.callers}
    declared = set(record.get("callers") or [])
    for caller in sorted(found - declared):
        out.append(
            f"{caller} calls {where}, and its arrival record does not declare "
            "that caller — a write made through a function is attributed to its "
            "caller, so this is a way into the lane nobody signed off"
        )
    for caller in sorted(declared - found):
        out.append(
            f"{where}'s arrival record declares the caller {caller}, which no "
            "longer calls it — the record describes a path that is not there"
        )
    for path in sorted(report.unread):
        out.append(
            f"{path} could not be read while finding the callers of {where} — "
            "an unread caller is never a pass"
        )
    return out


def problems(root: str = ROOT, contract: dict | None = None) -> list:
    """Every way the rows written into the lane differ from its arrivals."""
    try:
        lane = lane_name(contract)
        vocabulary = set(kinds(contract))
        records = arrivals(contract)
    except ContractError as e:
        return [str(e)]
    out: list[str] = []
    discovered = _writes(root, contract)

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
            by_unit.setdefault(unit_of(write, root), []).append(write)
    declared = {r.get("where") for r in records}

    for unit, here in sorted(by_unit.items()):
        if unit not in declared:
            sites = ", ".join(w.where for w in here)
            out.append(
                f"{unit} ({sites}) writes {lane}, and no arrival on its entrance "
                "declares it — every row there is one of the declared kinds; the "
                "card that lands a new site declares its record with it"
            )

    for record in records:
        where = record.get("where", "")
        kind = record.get("kind")
        if kind not in vocabulary:
            out.append(
                f"{where} declares the kind {kind!r}, which is not in the "
                f"vocabulary ({', '.join(sorted(vocabulary))}) — a row of a kind "
                "the contract does not carry is one nobody agreed to"
            )
        if where not in by_unit:
            out.append(
                f"{where} is declared an arrival, and no {lane} write was "
                "discovered there — the step or function is gone or no longer "
                "writes the lane; retire the record with it"
            )
        out += _gate_problems(record, by_unit.get(where, []), root, lane)
        if _is_function(where):
            out += _caller_problems(record, root)
    return out


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def run_check(root: str = ROOT, contract: dict | None = None, out=print) -> int:
    """Print the sibling's shape and return the exit code."""
    found = problems(root, contract)
    for problem in found:
        out(f"  [FAIL] {problem}")
    try:
        lane = lane_name(contract)
        kind_of = {r.get("where"): r.get("kind") for r in arrivals(contract)}
        writes = green_light_writes(root, contract)
        declared = len(arrivals(contract))
    except ContractError:
        lane, kind_of, writes, declared = "the arrivals lane", {}, [], 0
    for write, unit in writes:
        out(f"  {unit} ({write.where}): {kind_of.get(unit) or 'UNDECLARED'}")
    out(
        f"{len(writes)} write(s) into {lane} discovered; "
        f"{declared} arrival(s) declared; {len(found)} problem(s)"
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
    # published `destinations()`. That function is pure (DRE-5286: no Linear
    # call, no environment read), so a placeholder changes nothing this check
    # reads; without one, the import fails and its six computed sites come
    # back unread from a bare terminal. The board snapshot does the same.
    os.environ.setdefault("REPO", "dreadnought-foundry/bureau-pipeline")
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
