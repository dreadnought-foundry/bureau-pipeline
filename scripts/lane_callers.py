#!/usr/bin/env python3
"""Every caller of a lane-writing function, discovered rather than listed (DRE-5345).

A function that writes a lane is reached from wherever calls it, so a check
that reads write sites alone attributes a borrowed write to the wrong owner:
the DRE-4124 stall exit reached Green Light through
`planning_escalation.escalate`, whose own lane write is the planner's declared
question site, and a discovery reading sites passed it. The Green Light
writers check (DRE-5282) therefore reconciles the CALLERS of each lane-writing
function against the callers the lane contract declares. This module is the
discovery half. It reads nothing from the lane contract.

## The caller grammar (DRE-5275)

A caller is a UNIT, written `<file>#<unit>` with the file repo-relative:

* **A script.** In `scripts/*.py`, every `<module>.<function>(` call, every
  `<function>(` call under a `from <module> import <function>` (an `as` alias
  followed), and, inside the defining module itself, every bare `<function>(`
  call. Each is resolved with `ast` to the innermost enclosing `def`:
  `scripts/code_owner_hold.py#_cmd_hold`. A call at module level, outside any
  `def`, is `<file>#<module>`: it runs at import, so it is a caller too.
* **Not the defining module's `main`.** A call in the defining module's
  top-level `main`, or its `if __name__ == "__main__":` block, is a
  subcommand's dispatch. The workflow step that runs the subcommand is the
  caller in its place, so that `main` is never reported. A call from ANY OTHER
  module's `main` or `__main__` block is that module doing the work, not
  dispatching to it, and is reported as `<file>#main` (operator decision on
  PR #599, finding 1(b)): `reconcile.py`'s sweep moves cards through
  `linear_ops.cmd_state` from its `main`, and is reported so.
* **A workflow step.** In `.github/workflows/*.yml`, every step whose `run:`
  shell runs `scripts/<module>.py <subcommand>` (under any path prefix: the
  workflows run it as `.bureau-pipeline/scripts/…` or
  `"$PIPELINE_DIR"/scripts/…`), where the defining module's CLI maps that
  subcommand to the function or to an in-module caller of it. Resolved to
  `<file>#<step name>`. Shell comment lines and YAML comments are not calls.
  A fully quoted path (`"$PIPELINE_DIR/scripts/…py" go`) reads the same.
  A step whose `run:` delegates to `scripts/<name>.sh` (DRE-3488) is read
  through to its script (`step_shell.workflow_source`) and keeps its step
  name (DRE-5220).
  Composite actions (`.github/actions/*/action.yml`) are not read: none of
  their `run:` steps writes a lane today.

Discovery is DIRECT: `callers_of` names the units that call THIS function. A
step running `planning_route.py exit` reaches `escalate` through `_cmd_exit`, a
function in another module, and so it is `_cmd_exit`'s caller, never
`escalate`'s. Applying that one hop is DRE-5282's job, by calling `callers_of`
for the handler too.

## The CLI map, and what happens when it cannot be read

The map is read off the defining module's `main` and its `__main__` block, in
the three forms it takes on `main` today:

1. an `if command == "<name>": return <handler>(…)` chain
   (`planning_escalation.py`, `planning_route.py`); a branch that does its work
   inline maps the subcommand to no handler, and still counts as read — if
   the branch itself calls the function, or an in-module caller of it, the
   subcommand reaches the function. A branch holding a call whose callee is
   neither a name nor an attribute (`HANDLERS[command](…)`) cannot be
   resolved, and puts the module in `unread`;
2. `p = sub.add_parser("<name>")` followed by `p.set_defaults(fn=<handler>)`
   (`code_owner_hold.py`), read in source order because `p` is rebound;
3. a `{"<name>": <handler>}` dict whose values are the module's own top-level
   functions (`linear_ops.py`, in its `__main__` block).

A module that maps subcommands in any other form is reported in `unread`,
never silently passed. Three things show that a map exists which the reader
did not read: an `add_parser("<name>")` the three forms leave unmapped, a
workflow step running the module with a literal subcommand the read map does
not hold, or a call in `main` or the `__main__` block that reaches the
function from outside every `if command == "<name>":` branch, so no
subcommand can be named for it. A chain branch with a call it cannot resolve
is unread the same way. When the module is unread, none of its steps are reported (which
step reaches the function is exactly what could not be read). Its script
callers do not go through the map and are still reported.

`unread` also carries any file discovery had to read and could not: a script
that does not parse, whose calls are then unknown, or a workflow that is not
valid YAML. A step whose subcommand is not a literal (`linear_ops.py "$CMD"`)
names nothing the map could resolve and is not read as a call.

CLI:
  python3 scripts/lane_callers.py callers <module_path> <function>
prints one caller per line, sorted, then `UNREAD <path>` for each unread file,
and exits 0. A module or function that does not exist exits 2, with its name.
"""

from __future__ import annotations

import argparse
import ast
import glob
import os
import re
import shlex
import sys
from dataclasses import dataclass

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_shell  # noqa: E402 — reads a moved step's shell where it now lives (DRE-5220)

#: The unit a call at module level, outside any def, is reported as.
MODULE_UNIT = "<module>"

#: A call inside the defining module's `main` or `__main__` block: dispatch,
#: never a caller.
_DISPATCH = object()

#: A subcommand as a workflow writes it literally. `"$CMD"` is not one.
_LITERAL_SUBCOMMAND = re.compile(r"^[A-Za-z][\w-]*$")

#: A shell line continued onto the next line (the same shape
#: `ready_lane_writers.py` reads the workflows with).
_CONTINUATION = re.compile(r"\\\s*\n\s*")


@dataclass(frozen=True)
class CallerReport:
    """The units that call a function, and the files that could not be read."""

    callers: frozenset
    unread: frozenset


class UnknownTarget(LookupError):
    """The module or the function asked about does not exist."""


# --------------------------------------------------------------------------- #
# reading python                                                               #
# --------------------------------------------------------------------------- #


def _parse(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return None


def _is_main_guard(node) -> bool:
    """`if __name__ == "__main__":`, either way round."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
    consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
    return bool(names and consts)


def _call_units(tree, defining: bool) -> dict:
    """Call node id → its unit: the innermost enclosing def's name, or
    MODULE_UNIT at module level. In the defining module, a call inside `main`
    or the `__main__` block is _DISPATCH instead; in any other module both are
    that module's `main`, and are reported so."""
    out: dict = {}

    def walk(node, unit):
        for child in ast.iter_child_nodes(node):
            inner = unit
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top_main = node is tree and child.name == "main"
                inner = _DISPATCH if top_main and defining else child.name
            elif node is tree and _is_main_guard(child):
                inner = _DISPATCH if defining else "main"
            if isinstance(child, ast.Call):
                out[id(child)] = unit
            walk(child, inner)

    walk(tree, MODULE_UNIT)
    return out


def _import_names(tree, module: str, function: str) -> tuple:
    """(names bound to the module, names bound to the function) in a file."""
    modules, functions = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == module and not node.level:
            for alias in node.names:
                if alias.name == function:
                    functions.add(alias.asname or alias.name)
    return modules, functions


def _script_callers(tree, rel: str, module: str, function: str,
                    defining: bool) -> set:
    """The units in one script that call the function."""
    modules, functions = _import_names(tree, module, function)
    if defining:
        functions.add(function)
    if not modules and not functions:
        return set()
    units = _call_units(tree, defining)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        hit = (
            (isinstance(func, ast.Name) and func.id in functions)
            or (isinstance(func, ast.Attribute) and func.attr == function
                and isinstance(func.value, ast.Name) and func.value.id in modules)
        )
        unit = units.get(id(node))
        if hit and unit is not _DISPATCH and unit is not None:
            found.add(unit)
    return {f"{rel}#{unit}" for unit in found}


# --------------------------------------------------------------------------- #
# reading the CLI map                                                          #
# --------------------------------------------------------------------------- #


def _entry_points(tree) -> list:
    """The top-level `main` and `__main__` block: where the map lives."""
    return [
        node for node in tree.body
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main") or _is_main_guard(node)
    ]


def _add_parser_name(node) -> str | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser" and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)):
        return node.args[0].value
    return None


def _chain_name(node) -> str | None:
    """The subcommand an `if command == "<name>":` branch is for, or None."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return None
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    sides = [test.left, test.comparators[0]]
    subject = [s for s in sides if isinstance(s, (ast.Name, ast.Attribute))]
    names = [s.value for s in sides
             if isinstance(s, ast.Constant) and isinstance(s.value, str)]
    if not subject or len(names) != 1 or names[0] == "__main__":
        return None
    return names[0]


def _chain_form(entry, cli: dict) -> None:
    """Form 1: `if command == "<name>": return <handler>(…)`."""
    for node in ast.walk(entry):
        name = _chain_name(node)
        if name is None:
            continue
        handler = None
        for stmt in node.body:
            if (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)):
                handler = stmt.value.func.id
                break
        cli[name] = handler


def _dispatch_calls(entry) -> list:
    """(name called, the chain branches it sits in) for every bare `name(…)`
    call an entry point makes itself — a def nested in it is its own unit. A
    call inside a chain branch whose callee is neither a name nor an attribute
    is recorded with the name None: that branch cannot be resolved."""
    out = []

    def walk(node, branches):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            return
        name = _chain_name(node)
        if name is not None:
            walk(node.test, branches)
            for stmt in node.body:
                walk(stmt, branches | {name})
            for stmt in node.orelse:
                walk(stmt, branches)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.append((node.func.id, branches))
        elif (isinstance(node, ast.Call) and branches
              and not isinstance(node.func, ast.Attribute)):
            out.append((None, branches))
        for child in ast.iter_child_nodes(node):
            walk(child, branches)

    for child in ast.iter_child_nodes(entry):
        walk(child, frozenset())
    return out


def _parser_form(entry, cli: dict) -> None:
    """Form 2: `p = sub.add_parser("<name>")` then `p.set_defaults(fn=<h>)`,
    read in source order because `p` is rebound for every subcommand."""
    events = []
    for node in ast.walk(entry):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and _add_parser_name(node.value) is not None):
            events.append((node.lineno, node.col_offset, "bind", node))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "set_defaults"):
            events.append((node.lineno, node.col_offset, "defaults", node))
    bound: dict = {}
    for _, _, kind, node in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == "bind":
            bound[node.targets[0].id] = _add_parser_name(node.value)
            continue
        owner = node.func.value
        if isinstance(owner, ast.Name):
            name = bound.get(owner.id)
        else:
            name = _add_parser_name(owner)  # sub.add_parser("x").set_defaults(…)
        handlers = [k.value.id for k in node.keywords if isinstance(k.value, ast.Name)]
        if name is not None and handlers:
            cli[name] = handlers[0]


def _dict_form(entry, cli: dict, functions: set) -> None:
    """Form 3: `{"<name>": <handler>}`, the handlers the module's own defs."""
    for node in ast.walk(entry):
        if not isinstance(node, ast.Dict) or not node.keys:
            continue
        pairs = list(zip(node.keys, node.values))
        if all(isinstance(k, ast.Constant) and isinstance(k.value, str)
               and isinstance(v, ast.Name) and v.id in functions for k, v in pairs):
            for key, value in pairs:
                cli[key.value] = value.id


def cli_map(tree) -> tuple:
    """(subcommand → handler or None, subcommands declared by `add_parser`)."""
    functions = {n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    cli: dict = {}
    declared: set = set()
    for entry in _entry_points(tree):
        for node in ast.walk(entry):
            name = _add_parser_name(node)
            if name is not None:
                declared.add(name)
        _chain_form(entry, cli)
        _parser_form(entry, cli)
        _dict_form(entry, cli, functions)
    return cli, declared


# --------------------------------------------------------------------------- #
# reading the workflows                                                        #
# --------------------------------------------------------------------------- #


def _shell_args(rest: str) -> list:
    """The invocation's arguments, read as the shell would."""
    try:
        return shlex.split(rest, comments=True)
    except ValueError:
        return [a or b or c
                for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', rest)]


def _steps(doc) -> list:
    """(step name, run text) for every step of every job."""
    out = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return out
    for job_name, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            name = step.get("name") or step.get("id") or f"{job_name}[{index}]"
            out.append((str(name), step["run"]))
    return out


def _invocations(run: str, module: str) -> list:
    """The subcommand token of every `scripts/<module>.py` run in a step, or
    None where the invocation passes none."""
    lines = [line for line in run.splitlines() if not line.lstrip().startswith("#")]
    text = _CONTINUATION.sub(" ", "\n".join(lines))
    # The closing quote of a fully quoted path is part of the path, not the
    # first argument: `"$PIPELINE_DIR/scripts/<m>.py" go`.
    pattern = re.compile(
        rf"(?<![\w.-])scripts/{re.escape(module)}\.py[\"']?(?![\w.-])([^\n]*)")
    out = []
    for match in pattern.finditer(text):
        args = _shell_args(match.group(1))
        out.append(args[0] if args and not args[0].startswith("-") else None)
    return out


def _workflow_invocations(root: str, module: str, unread: set) -> list:
    """(unit, subcommand token) for every step that runs the module."""
    found = []
    for path in sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml"))):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            # A step moved to `scripts/<name>.sh` (DRE-3488) is read through
            # to its script, so it keeps its step name as the caller.
            doc = yaml.safe_load(step_shell.workflow_source(os.path.abspath(path), root))
        except (OSError, yaml.YAMLError):
            unread.add(rel)
            continue
        for name, run in _steps(doc):
            for token in _invocations(run, module):
                found.append((f"{rel}#{name}", token))
    return found


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #


def callers_of(module_path: str, function: str, root: str = ".") -> CallerReport:
    """Every unit that calls `function`, a top-level def of `module_path`.

    `module_path` is repo-relative (`scripts/planning_escalation.py`); `root`
    is the repository to read. Raises UnknownTarget when the module or the
    function does not exist.
    """
    rel_module = os.path.normpath(module_path).replace(os.sep, "/")
    path = os.path.join(root, rel_module)
    if not os.path.isfile(path):
        raise UnknownTarget(f"no module {module_path} under {root}")
    tree = _parse(path)
    if tree is None:
        raise UnknownTarget(f"module {module_path} does not parse")
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
               for n in tree.body):
        raise UnknownTarget(f"no top-level function {function} in {module_path}")
    module = os.path.splitext(os.path.basename(rel_module))[0]

    callers: set = set()
    unread: set = set()
    for script in sorted(glob.glob(os.path.join(root, "scripts", "*.py"))):
        rel = os.path.relpath(script, root).replace(os.sep, "/")
        defining = rel == rel_module
        script_tree = tree if defining else _parse(script)
        if script_tree is None:
            unread.add(rel)
            continue
        callers |= _script_callers(script_tree, rel, module, function, defining)

    # The handlers a subcommand can map to and so reach the function: the
    # function itself, or a unit of its own module that calls it.
    prefix = f"{rel_module}#"
    reach = {function} | {c[len(prefix):] for c in callers if c.startswith(prefix)}
    cli, declared = cli_map(tree)
    reaching = {name for name, handler in cli.items() if handler in reach}
    # A dispatch call that reaches the function itself, not through a mapped
    # handler: a chain branch doing its work inline reaches it for the
    # subcommands whose branches hold the call. One in no branch reaches it
    # for a subcommand the reader cannot name.
    unplaced = False
    for entry in _entry_points(tree):
        for name, branches in _dispatch_calls(entry):
            if name is None:
                unplaced = True  # a branch whose callee cannot be named
                continue
            if name not in reach:
                continue
            reaching |= branches
            unplaced = unplaced or not branches
    invocations = _workflow_invocations(root, module, unread)
    literal = {t for _, t in invocations if t and _LITERAL_SUBCOMMAND.match(t)}
    if (declared - cli.keys()) or (literal - cli.keys()) or unplaced:
        unread.add(rel_module)
    else:
        callers |= {unit for unit, token in invocations if token in reaching}
    return CallerReport(callers=frozenset(callers), unread=frozenset(unread))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    callers_cmd = sub.add_parser("callers")
    callers_cmd.add_argument("module_path")
    callers_cmd.add_argument("function")
    args = parser.parse_args(argv)
    command = args.command

    if command == "callers":
        return _cmd_callers(args.module_path, args.function)

    parser.print_usage(sys.stderr)
    return 2


def _cmd_callers(module_path: str, function: str) -> int:
    try:
        report = callers_of(module_path, function)
    except UnknownTarget as exc:
        print(f"lane_callers: {exc}", file=sys.stderr)
        return 2
    for caller in sorted(report.callers):
        print(caller)
    for path in sorted(report.unread):
        print(f"UNREAD {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
