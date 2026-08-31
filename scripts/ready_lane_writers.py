#!/usr/bin/env python3
"""Who can put a card in a ready-work lane (DRE-2859).

The claim this module exists to check is an ABSENCE: no writer, no sweep and no
relay path places a card into a lane the pipeline treats as ready work without
it having passed through Planning.

An absence cannot be confirmed by reading code, because **the writer nobody
remembered is exactly the one still open**. Its sibling card (DRE-2858) pointed
the three writers we knew about at Planning; a check that enumerated those three
would prove only that the three we already fixed are still fixed. So nothing
here is listed. Every writer is DISCOVERED, and a writer added tomorrow with a
wrong destination is named by this check without anybody remembering to widen
anything.

## The seam, and why discovery through it is complete

There is exactly one door. Every lane write in this repository goes through
`scripts/linear_ops.py`, because `stateId` is Linear's own field and only that
module names it. `seam_functions()` reads the door out of that module rather
than listing it: a function is part of the seam when it touches `stateId` or one
of the two lookups that turn a lane NAME into one. `seam_problems()` then holds
the door shut — a module anywhere else that builds its own `stateId` mutation is
a writer this sweep would never see, and it is reported as such.

## What a writer is, and where its destination comes from

`writes()` finds every call of the seam in `scripts/**.py`, and every
`linear_ops.py` invocation in `.github/workflows/*.yml` that hands the write
layer a lane. The destination of each is resolved statically — a literal, a
module constant, a parameter default, a pure vocabulary reader called with
literal arguments, or, when the call site computes it, the module's own
published `destinations()`. A destination no rule can read is reported as
UNREAD rather than assumed innocent: unknown is never a pass.

A writer is named the way the lane contract names it — a module that a workflow
runs writes AS that workflow (`planning_route.py` is the planner), because that
is the actor the contract's permitted-writer clause is about.

## The rule

For every write whose destination is a ready-work lane, the writer must be one
`config/lane-contract.json` permits for that lane. The two halves lock together:
a ready-work lane's entrance requires the routing verdict — the stamp Planning's
exit writes and nothing else does, which `planning_escalation.bypass_problems()`
checks — and every actor that can reach the lane is one the contract declared.
A writer that is in neither is a path into ready work that nobody signed off,
and that is the one that skips Planning.

## WHAT THIS CANNOT SEE

Stated here rather than left to be discovered, because a check that implies
otherwise is worse than none:

  * **A hand write in the Linear UI.** A person dragging a card into Todo is not
    preventable by anything in this repository. The contract names `operator` as
    a permitted writer of a ready-work lane precisely because that write is a
    human's to make. `unseen_writers()` reports it, so the boundary is visible
    rather than implied.
  * **The relay.** It lives in agent-bureau (`cloud/relay`); the contract's own
    writer glossary carries it with `path: null` and there is no file here to
    read. It is reported by `unseen_writers()` for the same reason — the day it
    is declared a writer of a ready-work lane is a day this says so.
  * **Linear's team-level default.** `defaultIssueState` is a setting on the
    Linear team, mirrored in agent-bureau's `config/linear-workspace.json`. It is
    a writer no code path touches and the highest-leverage one — it carried
    `Backlog` when this was written. `observed_default()` reads it from a
    workspace declaration in reach or from Linear itself; with neither, it
    returns None and `default_problems(None)` REPORTS that rather than passing.
  * **A destination computed at run time from data.** Resolution is static.
  * **Anything past the door.** Once a write reaches Linear this module is not
    in the loop; it is a check on the source, not a run-time refusal.

CLI:

    python3 scripts/ready_lane_writers.py check
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lane_contract  # noqa: E402
import planning_escalation  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

#: The module every lane write goes through. Named once, here, because the
#: whole completeness argument is that there is exactly one of them.
SEAM_MODULE = "linear_ops.py"

#: Linear's own field, plus the two lookups this repository uses to turn a lane
#: NAME into one. A function in the write layer that touches any of them is part
#: of the door; a function that CALLS such a function is a writer, not the door.
SEAM_PRIMITIVES = (
    "stateId",
    "state_id",
    "state_id_and_type",
    "_set_state",
    "guarded_state_write",
)

#: The two functions that turn a lane NAME into the id Linear wants, and the
#: argument that carries the name. This is the ONE hand-written fact in the
#: module, and it is a fact about the Linear API rather than about any writer:
#: everything else — which functions are the seam, which of their parameters
#: carry a lane, which CLI subcommands take one — is derived from it by
#: `lane_parameters()`.
LANE_LOOKUPS = ("state_id", "state_id_and_type")
LANE_LOOKUP_ARG = 1

#: A Linear issue mutation. `stateId` outside one of these is prose about the
#: field, not a write of it — this module's own docstring says the word.
_ISSUE_MUTATION = re.compile(r"\bissue(?:Update|Create)\b")

#: The writer key for Linear's team-level default — the lane a card lands in
#: when whatever created it named none. Not a file, which is exactly why a
#: census of code writers misses it.
LINEAR_DEFAULT_WRITER = "linear:team-default"

#: Where a workspace declaration is looked for, in order. The real file lives in
#: agent-bureau; this repository does not carry one, and that is reported rather
#: than passed.
WORKSPACE_ENV = "LINEAR_WORKSPACE_CONFIG"
WORKSPACE_PATHS = (
    os.path.join("config", "linear-workspace.json"),
    os.path.join("..", "agent-bureau", "config", "linear-workspace.json"),
)

#: A module publishes this to say which lanes it can write when its call sites
#: compute the destination. The one general escape from "unread": a writer that
#: cannot be read at the call site must say where it can put a card.
DESTINATIONS_HOOK = "destinations"

_MAX_DEPTH = 8


@dataclass(frozen=True)
class LaneWrite:
    """One place a card can be put in a lane.

    `lane` is None when the destination could not be read — the honest answer,
    and a reported one.
    """

    writer: str       # the lane-contract writer key: 'reconcile.py', 'plan.yml'
    where: str        # 'scripts/reconcile.py:4436' — actionable without the test
    lane: str | None
    how: str          # 'python' | 'workflow' | 'linear-default'
    expression: str   # the destination as written, for a message a human can act on


# --------------------------------------------------------------------------- #
# what counts as ready work                                                    #
# --------------------------------------------------------------------------- #


def ready_lanes(contract: dict | None = None, doc: dict | None = None) -> tuple:
    """The lanes the pipeline treats as ready work.

    Derived, never typed: the work-segment lanes the planning segment can send a
    card to, read from the shape and routing vocabularies. Move a verdict's
    destination and this moves with it, which is the difference between policing
    the lanes that matter and policing the two somebody remembered.
    """
    return planning_escalation.work_lanes_reachable_from_planning(contract, doc)


def unseen_writers(contract: dict | None = None) -> tuple:
    """The permitted writers of a ready-work lane that this cannot check.

    A writer the contract carries with no path — a human, the relay — is outside
    this repository by construction. Reported rather than skipped: a boundary
    nobody can see is one nobody defends, and the day the relay is declared a
    writer of Todo, this is what says so.
    """
    known = lane_contract.writers(contract)
    out: list[str] = []
    for name in ready_lanes(contract):
        for key in lane_contract.lane_writers(name, contract=contract):
            entry = known.get(key) or {}
            if entry.get("path") is None and key not in out:
                out.append(key)
    return tuple(out)


# --------------------------------------------------------------------------- #
# the seam, read out of the write layer                                        #
# --------------------------------------------------------------------------- #


def _parse(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return None, ""
    try:
        return ast.parse(source), source
    except SyntaxError:
        return None, source


def _functions(tree) -> dict:
    """Module-level functions by name. Nested defs are deliberately not indexed:
    a nested writer is still discovered by its call site."""
    return {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def seam_functions(root: str = ROOT) -> tuple:
    """Every function in the write layer that puts a card in a lane.

    Derived from the module's own source: a function whose body names Linear's
    `stateId` field or one of the two name→id lookups. Listing these would be
    the same mistake this module exists to avoid — a new creation seam added
    beside `_create_card` joins this set by writing the code, not by anybody
    remembering to add it.
    """
    tree, _ = _parse(os.path.join(root, "scripts", SEAM_MODULE))
    if tree is None:
        return ()
    found = []
    for name, node in _functions(tree).items():
        body = ast.get_source_segment(_source_of(root, SEAM_MODULE), node) or ""
        if any(re.search(rf"\b{re.escape(p)}\b", body) for p in SEAM_PRIMITIVES):
            found.append(name)
    return tuple(sorted(found))


_SOURCE_CACHE: dict = {}


def _source_of(root: str, relmodule: str) -> str:
    key = (root, relmodule)
    if key not in _SOURCE_CACHE:
        _, src = _parse(os.path.join(root, "scripts", relmodule))
        _SOURCE_CACHE[key] = src
    return _SOURCE_CACHE[key]


def seam_problems(root: str = ROOT) -> list:
    """Everything that writes a card's lane without going through the seam.

    Discovery is only as complete as the door is exclusive. A module that builds
    its own `stateId` mutation is a writer `writes()` will never see, so it is
    reported here — the check on the check.
    """
    problems: list[str] = []
    for path in sorted(glob.glob(os.path.join(root, "scripts", "**", "*.py"),
                                 recursive=True)):
        rel = os.path.relpath(path, root)
        if os.path.basename(path) == SEAM_MODULE:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if not _ISSUE_MUTATION.search(text):
            continue
        for match in re.finditer(r"\bstateId\b", text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(
                f"{rel}:{line} sets a card's lane with Linear's `stateId` "
                f"directly, outside {SEAM_MODULE} — a write that does not go "
                "through the one door is a writer no discovery here can see"
            )
    return problems


# --------------------------------------------------------------------------- #
# resolving a destination                                                      #
# --------------------------------------------------------------------------- #


class _Repo:
    """The scripts tree, parsed once — modules, their constants, their functions."""

    def __init__(self, root: str):
        self.root = root
        self._modules: dict = {}

    def module(self, name: str):
        if name not in self._modules:
            path = os.path.join(self.root, "scripts", f"{name}.py")
            tree, source = _parse(path)
            self._modules[name] = (
                None
                if tree is None
                else {
                    "tree": tree,
                    "source": source,
                    "consts": _module_constants(tree),
                    "funcs": _functions(tree),
                }
            )
        return self._modules[name]


def _module_constants(tree) -> dict:
    out: dict = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = node.value.value
    return out


def _local_assignments(fn, name: str) -> list:
    if fn is None:
        return []
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    out.append(node.value)
    return out


def _param_default(fn, name: str):
    """The default of `name` in `fn`, or None. A parameter with no default is a
    destination the CALLER supplies — that call site is the write, not this one."""
    if fn is None:
        return None
    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    padded = [None] * (len(positional) - len(defaults)) + defaults
    for arg, default in zip(positional, padded):
        if arg.arg == name:
            return default
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if arg.arg == name:
            return default
    return None


def _positional_names(fn) -> list:
    return [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)]


def lane_parameters(root: str = ROOT) -> dict:
    """Seam function → the parameters of it that carry a lane NAME.

    `{function: (positional indexes, parameter names)}`, grown to a fixpoint from
    the two lookups that turn a name into a state id. This is what makes the
    check read an argument rather than guess one: `cmd_advance(card, to,
    from_csv)` takes a destination AND a comma-separated list of from-states,
    and both look like lane names on a command line. Only the second argument is
    a write, and nothing here had to be told which.
    """
    tree, _ = _parse(os.path.join(root, "scripts", SEAM_MODULE))
    if tree is None:
        return {}
    funcs = _functions(tree)
    known: dict = {}
    for name in LANE_LOOKUPS:
        if name in funcs:
            positional = _positional_names(funcs[name])
            names = {positional[LANE_LOOKUP_ARG]} if len(positional) > LANE_LOOKUP_ARG else set()
            known[name] = ({LANE_LOOKUP_ARG}, names)
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            positional = _positional_names(fn)
            found_pos, found_names = known.get(name, (set(), set()))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                callee = known.get(_called_name(node.func))
                if callee is None:
                    continue
                for arg in _lane_arguments(node, callee):
                    if not isinstance(arg, ast.Name) or arg.id in found_names:
                        continue
                    if arg.id in positional:
                        found_pos.add(positional.index(arg.id))
                    elif not any(a.arg == arg.id for a in fn.args.kwonlyargs):
                        continue
                    found_names.add(arg.id)
                    changed = True
            if (found_pos or found_names) and name not in known:
                known[name] = (found_pos, found_names)
                changed = True
    return {k: (tuple(sorted(v[0])), tuple(sorted(v[1]))) for k, v in known.items()}


def _lane_arguments(node, lane_params) -> list:
    """The arguments of this call that carry a lane, by the callee's own shape."""
    positions, names = lane_params
    out = [node.args[i] for i in positions if i < len(node.args)]
    out += [kw.value for kw in node.keywords if kw.arg in names]
    return out


def _evaluate(module_name: str, func_name: str, args: list):
    """Call a pure vocabulary reader with literal arguments and take its answer.

    `critic_score.escalation_lane()` reads `config/routing-verdicts.json`; the
    call site is where the lane comes from, so reading it is the only way to
    know. Anything that raises — a function that needs the network, a name that
    is not there — leaves the destination unread, which is reported.
    """
    try:
        module = __import__(module_name)
        value = getattr(module, func_name)(*args)
    except Exception:  # noqa: BLE001 — an unreadable destination is reported, not fatal
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {v for v in value if isinstance(v, str)}
    return set()


def _resolve(node, repo: _Repo, module_name: str, fn, depth: int = 0) -> set:
    """Every string this destination expression can be, as far as source can say."""
    if node is None or depth > _MAX_DEPTH:
        return set()
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, ast.BoolOp):
        out: set = set()
        for value in node.values:
            out |= _resolve(value, repo, module_name, fn, depth + 1)
        return out
    if isinstance(node, ast.IfExp):
        return (_resolve(node.body, repo, module_name, fn, depth + 1)
                | _resolve(node.orelse, repo, module_name, fn, depth + 1))
    if isinstance(node, ast.Name):
        out = set()
        for value in _local_assignments(fn, node.id):
            out |= _resolve(value, repo, module_name, fn, depth + 1)
        if out:
            return out
        default = _param_default(fn, node.id)
        if default is not None:
            return _resolve(default, repo, module_name, fn, depth + 1)
        mod = repo.module(module_name)
        if mod and node.id in mod["consts"]:
            return {mod["consts"][node.id]}
        return set()
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            other = repo.module(node.value.id)
            if other and node.attr in other["consts"]:
                return {other["consts"][node.attr]}
        return set()
    if isinstance(node, ast.Call):
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if len(args) != len(node.args):
            return set()
        if isinstance(node.func, ast.Name):
            mod = repo.module(module_name)
            if mod and node.func.id in mod["funcs"]:
                return _evaluate(module_name, node.func.id, args)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            other = repo.module(node.func.value.id)
            if other and node.func.attr in other["funcs"]:
                return _evaluate(node.func.value.id, node.func.attr, args)
        return set()
    return set()


# --------------------------------------------------------------------------- #
# attribution: a module writes as the actor that runs it                       #
# --------------------------------------------------------------------------- #


def _workflow_files(root: str) -> list:
    return sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml")))


def _attribution(root: str, contract: dict | None) -> dict:
    """module filename → the writer key(s) the contract would name for it.

    A module the glossary defines writes as itself. Anything else writes as the
    workflow that runs it — `planning_route.py` IS the planner, and the
    contract's permitted-writer clause is about the actor, not the file. A
    module nobody runs and nobody declared writes as itself, and is then
    reported when it reaches a ready-work lane, which is the point.
    """
    known = set(lane_contract.writers(contract))
    out: dict = {}
    for path in sorted(glob.glob(os.path.join(root, "scripts", "**", "*.py"),
                                 recursive=True)):
        base = os.path.basename(path)
        if base in known:
            out[base] = (base,)
            continue
        runners = []
        for wf in _workflow_files(root):
            try:
                with open(wf, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            if re.search(rf"scripts/{re.escape(base)}\b", text):
                runners.append(os.path.basename(wf))
        out[base] = tuple(runners) or (base,)
    return out


# --------------------------------------------------------------------------- #
# discovery                                                                    #
# --------------------------------------------------------------------------- #


def _python_writes(root: str, seam: tuple, live: set, contract: dict | None,
                   lane_params: dict) -> list:
    repo = _Repo(root)
    attribution = _attribution(root, contract)
    found: list[LaneWrite] = []
    for path in sorted(glob.glob(os.path.join(root, "scripts", "**", "*.py"),
                                 recursive=True)):
        base = os.path.basename(path)
        module_name = base[:-3]
        tree, source = _parse(path)
        if tree is None:
            continue
        rel = os.path.relpath(path, root)
        enclosing = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = _called_name(node.func)
            if called not in seam:
                continue
            fn = enclosing.get(id(node))
            lanes, expression = _destination_of(
                node, repo, module_name, fn, lane_params, live
            )
            where = f"{rel}:{node.lineno}"
            if not lanes:
                # The write layer calling itself with a lane its own caller
                # supplied is the door, not a writer — the caller's call site is
                # the write, and it is discovered there.
                if base == SEAM_MODULE:
                    continue
                lanes = _published_destinations(module_name, live)
                if not lanes:
                    for writer in attribution.get(base, (base,)):
                        found.append(LaneWrite(writer, where, None, "python", expression))
                    continue
            for lane in sorted(lanes):
                for writer in attribution.get(base, (base,)):
                    found.append(LaneWrite(writer, where, lane, "python", expression))
    return found


def _published_destinations(module_name: str, live: set) -> set:
    """The lanes a module says it can write, when its call sites compute them.

    The one general answer to "the destination is not at the call site": the
    module publishes `destinations()`. A writer that neither names its lane nor
    publishes the set it picks from is unread, and unread is reported.
    """
    try:
        module = __import__(module_name)
        value = getattr(module, DESTINATIONS_HOOK)()
    except Exception:  # noqa: BLE001
        return set()
    if isinstance(value, str):
        value = (value,)
    return {v for v in value if isinstance(v, str) and v in live}


def _enclosing_functions(tree) -> dict:
    """Call node id → the function it sits in. Built by descent so a nested
    function's parameters resolve against the right signature."""
    out: dict = {}

    def walk(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                out[id(child)] = fn
            walk(child, fn)

    walk(tree, None)
    return out


def _called_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _destination_of(node, repo, module_name, fn, lane_params, live):
    """The lanes this seam call names, and the expression it named them with.

    Only the callee's own lane parameters are read — never every argument that
    happens to look like a lane. `cmd_advance(card, CUTOVER_TO, CUTOVER_FROM)`
    moves cards OUT of the second one.
    """
    called = _called_name(node.func)
    params = lane_params.get(called, ((), ()))
    lanes: set = set()
    expressions: list[str] = []
    for arg in _lane_arguments(node, params):
        resolved = _resolve(arg, repo, module_name, fn) & live
        expressions.append(_unparse(arg))
        lanes |= resolved
    if not lanes and not expressions:
        # The call named no lane, so the callee's own default is the
        # destination — the card producer's `lane="Planning"` is a real
        # destination even when nobody passes one.
        callee = (repo.module(SEAM_MODULE[:-3]) or {}).get("funcs", {}).get(called)
        if callee is not None:
            for name in params[1]:
                default = _param_default(callee, name)
                resolved = _resolve(default, repo, SEAM_MODULE[:-3], callee) & live
                if resolved:
                    lanes |= resolved
                    expressions.append(f"{name}={_unparse(default)}")
    return lanes, ", ".join(expressions) or "<no lane argument>"


def _unparse(node) -> str:
    if node is None:
        return "<none>"
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "<unreadable>"


# --------------------------------------------------------------------------- #
# discovery: the workflows                                                     #
# --------------------------------------------------------------------------- #


def cli_lane_arguments(root: str = ROOT, lane_params: dict | None = None) -> dict:
    """CLI subcommand → the argument index that carries the lane.

    The write layer's own dispatch table, crossed with the lane parameters
    derived above. A subcommand whose handler decides the lane itself (`create`
    mints in Planning) is absent: its write lives in the Python and is
    discovered there, so a workflow calling it is not a second writer.
    """
    lane_params = lane_params if lane_params is not None else lane_parameters(root)
    tree, _ = _parse(os.path.join(root, "scripts", SEAM_MODULE))
    if tree is None:
        return {}
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(value, ast.Name):
                continue
            positions = lane_params.get(value.id, ((), ()))[0]
            if positions:
                out[key.value] = positions[0]
    return out


#: A shell line continued onto the next line. The workflows write the write
#: layer's longer invocations that way, and an invocation read one line at a
#: time loses its destination.
_CONTINUATION = re.compile(r"\\\s*\n\s*")
_INVOCATION = re.compile(r"linear_ops\.py((?:\\\s*\n|[^\n])*)")


def _workflow_writes(root: str, live: set, lane_args: dict) -> list:
    found: list[LaneWrite] = []
    for path in _workflow_files(root):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        writer = os.path.basename(path)
        for match in _INVOCATION.finditer(text):
            args = _shell_args(_CONTINUATION.sub(" ", match.group(1)))
            if not args:
                continue
            index = lane_args.get(args[0])
            if index is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            where = f"{os.path.relpath(path, root)}:{line}"
            value = args[index + 1] if len(args) > index + 1 else None
            found.append(
                LaneWrite(writer, where, value if value in live else None, "workflow",
                          value if value is not None else "<no argument>")
            )
    return found


def _shell_args(rest: str) -> list:
    """The invocation's arguments. A `run:` block is shell, and the destination
    is an argument in it — read as the shell would, not as a lane-shaped word
    somewhere on the line."""
    try:
        return shlex.split(rest, comments=True)
    except ValueError:
        return [
            a or b or c
            for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', rest)
        ]


# --------------------------------------------------------------------------- #
# Linear's team-level default                                                  #
# --------------------------------------------------------------------------- #


def observed_default(root: str = ROOT, gql=None) -> str | None:
    """The lane a card lands in when whatever created it named none, or None.

    A workspace declaration in reach wins — it is the file the setting is
    mirrored in and it can be read without credentials. Otherwise Linear itself
    is asked. None means neither answered, and None is REPORTED by
    `default_problems`, never taken for agreement.
    """
    for candidate in ([os.environ[WORKSPACE_ENV]] if os.environ.get(WORKSPACE_ENV)
                      else []) + [os.path.join(root, p) for p in WORKSPACE_PATHS]:
        try:
            with open(candidate, encoding="utf-8") as fh:
                declared = (json.load(fh) or {}).get("defaultIssueState")
        except (OSError, ValueError):
            continue
        if isinstance(declared, str) and declared:
            return declared
    if gql is None:
        if not os.environ.get("LINEAR_API_KEY"):
            return None
        try:
            import linear_ops

            gql = linear_ops.gql
        except Exception:  # noqa: BLE001
            return None
    try:
        data = gql(
            '{ teams(filter: {key: {eq: "DRE"}}) { nodes { '
            "defaultIssueState { name } } } }"
        )
        nodes = (((data or {}).get("teams") or {}).get("nodes")) or []
        name = ((nodes[0] if nodes else {}).get("defaultIssueState") or {}).get("name")
    except Exception:  # noqa: BLE001
        return None
    return name or None


def default_problems(default: str | None, *, contract: dict | None = None,
                     doc: dict | None = None) -> list:
    """Whether the lane a card lands in by default is one it may land in.

    This is the writer no code path touches, and the one a census of code
    writers misses entirely: nothing in this repository sets it, so nothing in
    this repository would ever have named it.
    """
    ready = ready_lanes(contract, doc)
    if default is None:
        return [
            f"{LINEAR_DEFAULT_WRITER}: the lane a card lands in when nobody "
            "names one could not be read — no workspace declaration in reach "
            "and no answer from Linear. Unknown is not a pass: a default of "
            f"{ready[0]!r} would put unplanned work straight into ready work, "
            "and this is the writer no code path touches"
        ]
    if default not in lane_contract.lane_names(status="live", contract=contract):
        return [
            f"{LINEAR_DEFAULT_WRITER}: Linear's team default is {default!r}, "
            "which is not a live lane — a card created without a state lands "
            "somewhere the contract does not name"
        ]
    if default in ready:
        return [
            f"{LINEAR_DEFAULT_WRITER}: Linear's team default is {default!r}, a "
            "lane the pipeline treats as ready work. Anything that creates a "
            "card without naming a state — an integration, an API call, a form "
            "— puts it straight into the build queue without it ever passing "
            "through Planning. No code path touches this setting, so no check "
            "over this repository's writers would have found it"
        ]
    return []


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def writes(root: str = ROOT, contract: dict | None = None) -> tuple:
    """Every place a card can be put in a lane, discovered rather than listed."""
    live = set(lane_contract.lane_names(status="live", contract=contract))
    seam = seam_functions(root)
    lane_params = lane_parameters(root)
    found = _python_writes(root, seam, live, contract, lane_params)
    found += _workflow_writes(root, live, cli_lane_arguments(root, lane_params))
    return tuple(found)


def writer_problems(root: str = ROOT, contract: dict | None = None,
                    doc: dict | None = None) -> list:
    """Every writer in this repository that can reach a ready-work lane it has
    not been declared for, and every one whose destination could not be read."""
    problems = list(seam_problems(root))
    ready = ready_lanes(contract, doc)
    everywhere = set.intersection(
        *[set(lane_contract.lane_writers(n, contract=contract)) for n in ready]
    ) if ready else set()
    for write in writes(root, contract):
        if write.lane is None:
            # An unreadable destination from a writer the contract already
            # permits in EVERY ready-work lane cannot become an undeclared
            # write — wherever it goes, it was allowed to go there. Anyone else
            # is reported: unknown is not a pass.
            if write.writer in everywhere:
                continue
            problems.append(
                f"{write.writer} ({write.where}) hands the write layer a "
                f"destination this cannot read ({write.expression}) — so "
                "nothing here can say whether it reaches a ready-work lane. "
                f"Name the lane at the call site, or publish "
                f"`{DESTINATIONS_HOOK}()` in the module"
            )
            continue
        if write.lane not in ready:
            continue
        permitted = lane_contract.lane_writers(write.lane, contract=contract)
        if write.writer not in permitted:
            problems.append(
                f"{write.writer} ({write.where}) puts a card in {write.lane!r}, "
                "a lane the pipeline treats as ready work, and the lane "
                f"contract does not permit it there — {write.lane} permits "
                f"{', '.join(permitted)}. A writer nobody declared is a way "
                "into ready work that never passed through Planning"
            )
    return problems


def problems(root: str = ROOT, contract: dict | None = None,
             doc: dict | None = None, default: str | None = None) -> list:
    """Everything that can put a card in a ready-work lane it may not."""
    found = writer_problems(root, contract, doc)
    found += default_problems(
        default if default is not None else observed_default(root),
        contract=contract, doc=doc,
    )
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if (args.command or "check") != "check":
        parser.print_usage(sys.stderr)
        return 2

    found = problems()
    for problem in found:
        print(f"  [FAIL] {problem}")
    discovered = writes()
    print(
        f"{len(discovered)} write(s) into {len(set(w.lane for w in discovered))} "
        f"lane(s) discovered; ready work is {', '.join(ready_lanes())}; "
        f"{len(found)} problem(s)"
    )
    unseen = unseen_writers()
    if unseen:
        print(f"  not checkable from here: {', '.join(unseen)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
