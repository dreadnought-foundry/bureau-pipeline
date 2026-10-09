#!/usr/bin/env python3
"""Every place the pipeline writes a label, and none of them writes the CEO's mark (DRE-6362).

`routing_verdict.HAND_BUILT_LABEL` is the CEO's mark (DRE-6225): a person
builds the card and nothing is dispatched at it. His rule of 2026-10-07 is that
it is his alone and nothing automatic applies it. DRE-6228 switched the three
writers that did, and the claim left to hold is an ABSENCE: no place the
pipeline writes a label can write that one.

An absence cannot be confirmed by listing writers, because **the writer nobody
remembered is exactly the one still open**. So nothing here is listed. Every
write is DISCOVERED — the shape of `ready_lane_writers.py check` and
`green_light_rows.py check` — and a writer added tomorrow is named by this
check without anybody widening anything.

## The seam, read out of the write layer

Every label write goes through `scripts/linear_ops.py`, because `labelIds` is
Linear's own field and only that module names it. `seam_functions()` reads the
door out of that module rather than naming it: a function is part of the seam
when it touches `labelIds` or one of the two lookups that turn a label NAME
into an id. `label_parameters()` then derives, to a fixpoint, which parameters
of the write layer carry a label to one of those lookups — `add_label`'s
`label_name`, `create_card`'s `labels`, and the `--label <name>` pairs the
command-line creators take in their trailing flags. `seam_problems()` holds the
door shut: a module anywhere else that builds its own `labelIds` mutation is a
writer nothing here would see, and it is reported as such.

## What a write is

`writes()` finds every write through that seam:

  * every call of a label-taking seam function in `scripts/**/*.py` —
    `linear_ops.add_label`, the label tuple handed to `create_card`, the
    `--label` flags handed to `cmd_subissue` / `cmd_oneoff` / `cmd_create`;
  * every planned `hygiene.linear_label(…, True)` — the hygiene lanes plan a
    write and its executor applies it later, so the plan is where the label is
    named (`DEFERRED_SEAMS`);
  * every invocation of the write layer's command line — `linear_ops.py
    add-label`, `create --label …` — in every workflow and composite action
    under `.github/`, `.yml` or `.yaml` (read through
    `step_shell.workflow_source`, so a step moved to a script still counts) and
    in every `scripts/**/*.sh` no workflow delegates to;
  * every argv a Python module builds to run that command line —
    `[sys.executable, <the write layer>, "add-label", card, label]` — and
    every shell string it spells one in, a literal, f-string, `+` or `%` /
    `.format` template, its holes read as templates;
  * every `marks` list in the two vocabularies, `config/routing-verdicts.json`
    and `config/planning-shapes.json` — the stamps and the sweep apply them.

A helper that hands its own parameter to the seam is part of the door: the
write is wherever the helper is called, and its callers are found and read
there — a finding names the call site that handed the label in. A parameter
that arrives on a module's command line is read off that module's invocations
in the workflows, the same way the write layer's are. A seam function handed
on as a value, or reached with `getattr`, is a write whose calls cannot be
found, and is reported UNREAD — as is a `**` mapping unpacked into the seam
that is not a literal keyed by name, and a `from <seam> import *`.

## How a label is read

Statically, and only by rules that cannot be wrong about the mark: a literal; a
module constant, followed through the module that defines it; an f-string,
which can be the mark only if its literal text allows it; a collection, element
by element; a local, through every assignment, `append` and `+=` it receives —
and whatever a function it is handed to puts into it; a
function's result, through its `return`s with this call's arguments bound; a
loop variable, by what it iterates — or by the guard it sits under, when that
guard is a literal `startswith` or `==`; a record's field, through every value
the module hands a call under that name; and the vocabulary, when a `marks`
field is read off a record of a module that reads one. A label no rule can read
is reported UNREAD, never passed: unknown is not a pass.

The check fails on any write that can be `HAND_BUILT_LABEL` and on any UNREAD
write. It reads the mark from `routing_verdict` and spells no label of its own.

## WHAT THIS CANNOT SEE

Said here rather than left to be discovered, because a check that implies
otherwise is worse than none:

  * **A hand write in Linear.** The CEO applies his mark by hand, in Linear —
    that is the whole point of it — and a person can apply any label there.
    Nothing in this repository sees that write, and nothing should stop it.
  * **A label computed from data at run time.** Resolution is static. A label
    that comes out of a card, a file or a response is reported UNREAD unless a
    guard in the source already bounds it. Inside the write layer itself the
    labels a child inherits from its parent epic are such data; they are the
    door's own business and are not judged here.
  * **A collection filled through an alias.** `b = a; b.append(…)` fills `a`,
    and static reading follows names, not objects — nor a list handed to a
    function this cannot resolve.
  * **A shell string assembled across statements.** A command line in a
    Python string is read when one expression spells the write layer's name
    and its subcommand together. A command whose script path sits in a
    variable, or that is joined from pieces built elsewhere, is not seen as
    one; an argv list that names the script is.
  * **A composite action outside this repository.** Every workflow and
    composite action under `.github/` is read; a step that `uses:` an action
    from another repository runs code this cannot open.
  * **Anything past the door.** Once a write reaches Linear this module is not
    in the loop; it is a check on the source, not a refusal at run time.

CLI, from the repository root:

    PYTHONPATH=scripts python3 -m label_writers check
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
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import routing_verdict  # noqa: E402 — the mark, declared once (DRE-6225)
import step_shell  # noqa: E402 — reads a moved step's shell where it now lives

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

#: The module every label write goes through. Named once, here, because the
#: whole completeness argument is that there is exactly one of them.
SEAM_MODULE = "linear_ops.py"

#: Linear's own field, and the two lookups that turn a label NAME into the id
#: Linear wants — with the argument that carries the name. Facts about the
#: Linear API and the write layer, never about any writer: everything else is
#: derived from them by `label_parameters()`.
LABEL_FIELD = "labelIds"
LABEL_LOOKUPS = ("_team_label_id", "_team_label_ids")
LABEL_LOOKUP_ARG = 1
SEAM_PRIMITIVES = (LABEL_FIELD,) + LABEL_LOOKUPS

#: The write layer's command-line flag for a label: `create`, `oneoff` and
#: `subissue` take `--label <name>`, repeatable, in their trailing flags.
LABEL_FLAG = "--label"

#: A planned write applied later by its own executor: `hygiene.linear_label`
#: builds a record its lane hands to `hygiene.send`, which calls the seam
#: with the record's label. The planning call is where the label is named, so
#: that call is the write. `(module, function): (label parameter, add flag)`.
DEFERRED_SEAMS = {("hygiene", "linear_label"): ("label", "add")}

#: The two vocabularies, and the field their records carry labels in.
VOCABULARIES = ("routing-verdicts.json", "planning-shapes.json")
MARKS_KEY = "marks"

#: A Linear issue mutation, and the fields that set an issue's labels in one.
#: Spelled so that no string in this module's own source names a mutation.
_ISSUE_MUTATION = re.compile(r"\bissue(?:Update|Create|AddLabel|BatchUpdate)\b")
_LABEL_FIELDS = re.compile(r"\b(?:added)?(?:L|l)abelIds?\b")

#: Calls whose result is their first argument's elements, for this purpose.
_PASS_THROUGH = frozenset(
    ("tuple", "list", "set", "frozenset", "sorted", "reversed", "iter", "next", "str")
)
_STRING_METHODS = frozenset(("lower", "strip", "casefold", "copy"))
_ELEMENT_METHODS = frozenset(("append", "add"))
_SEQUENCE_METHODS = frozenset(("extend", "update"))

_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_MAX_DEPTH = 150

#: The selector a `--label` flag sequence is read through.
_FLAG = ("flag",)


@dataclass(frozen=True)
class LabelParams:
    """The parameters of a seam function that carry a label."""

    positions: tuple   # positional indexes
    names: tuple       # parameter names, positional or keyword-only
    flags: int | None  # index of the trailing flags that carry `--label <name>`


@dataclass(frozen=True)
class LabelWrite:
    """One place a label can be put on a card.

    `labels` is every value the label can take as far as the source can say —
    an f-string reads as its template, `repo:*`. `unread` says what could not
    be read, and a write with anything there is reported.
    """

    where: str        # '<file>:<line>' — actionable without the test
    how: str          # 'python' | 'workflow' | 'shell' | 'config'
    expression: str   # the label as written, for a message a human can act on
    labels: tuple
    unread: tuple
    via: tuple = ()   # the command lines a label arrives on, read in the workflows
    atoms: tuple = field(default=(), compare=False, repr=False)
    origins: tuple = field(default=(), compare=False, repr=False)  # (atom, call sites)


# --------------------------------------------------------------------------- #
# the mark                                                                     #
# --------------------------------------------------------------------------- #


def _mark() -> str:
    """Read at the moment of the check, never copied at import."""
    return routing_verdict.HAND_BUILT_LABEL


def _can_be(atom, mark: str) -> bool:
    """Whether this label value can be the mark — a literal compared as the
    write layer compares labels (without case), a template by its literal text."""
    if isinstance(atom, str):
        return atom.lower() == mark.lower()
    pattern = ".*".join(re.escape(part) for part in atom)
    return re.fullmatch(pattern, mark, re.IGNORECASE | re.DOTALL) is not None


def _render(atom) -> str:
    return atom if isinstance(atom, str) else "*".join(atom)


# --------------------------------------------------------------------------- #
# parsing                                                                      #
# --------------------------------------------------------------------------- #

_PARSE_CACHE: dict = {}


def _parse(path: str):
    """(tree, source) for `path`, or (None, source) when it does not parse.
    Cached on the file's size and mtime, so a file staged mid-run is read."""
    try:
        stat = os.stat(path)
    except OSError:
        return None, ""
    key = (path, stat.st_mtime_ns, stat.st_size)
    if key not in _PARSE_CACHE:
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            source = ""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        _PARSE_CACHE[key] = (tree, source)
    return _PARSE_CACHE[key]


def _python_files(root: str) -> list:
    return sorted(
        p for p in glob.glob(os.path.join(root, "scripts", "**", "*.py"), recursive=True)
        if "__pycache__" not in p
    )


def _functions(tree) -> dict:
    return {n.name: n for n in getattr(tree, "body", []) if isinstance(n, _FUNCTION)}


def _positional_names(fn) -> list:
    return [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)]


def _param_names(fn) -> set:
    args = fn.args
    names = set(_positional_names(fn)) | {a.arg for a in args.kwonlyargs}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    return names


def _param_default(fn, name: str):
    args = fn.args
    positional = list(args.posonlyargs) + list(args.args)
    padded = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, padded):
        if arg.arg == name:
            return default
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if arg.arg == name:
            return default
    return None


def _called_name(func) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _unparse(node) -> str:
    if node is None:
        return "<none>"
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return "<unreadable>"


def _docstrings(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef) + _FUNCTION):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _walk_scope(node):
    """Every node in `node`'s own scope — never into a nested function."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if not isinstance(child, _SCOPE):
            stack.extend(ast.iter_child_nodes(child))


# --------------------------------------------------------------------------- #
# the seam, read out of the write layer                                        #
# --------------------------------------------------------------------------- #


def seam_functions(root: str = ROOT) -> tuple:
    """Every function in the write layer that puts a label on a card.

    Derived from the module's own source: a function whose body names Linear's
    `labelIds` field or one of the two name→id lookups. A new label seam added
    beside `add_label` joins this set by being written, not by being listed.
    """
    tree, source = _parse(os.path.join(root, "scripts", SEAM_MODULE))
    if tree is None:
        return ()
    found = []
    for name, node in _functions(tree).items():
        body = ast.get_source_segment(source, node) or ""
        if any(re.search(rf"\b{re.escape(p)}\b", body) for p in SEAM_PRIMITIVES):
            found.append(name)
    return tuple(sorted(found))


def _linear_readers(funcs: dict) -> set:
    """The write layer's functions that read Linear: their result is Linear's
    data, never the argument a caller handed in. `gql` and, to a fixpoint,
    everything that calls it."""
    readers = {"gql"}
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in readers:
                continue
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in readers for n in ast.walk(fn)):
                readers.add(name)
                changed = True
    return readers


def _assigned(fn, name: str) -> list:
    """Every expression `name` takes a value from inside `fn`'s own scope."""
    out = []
    for node in _walk_scope(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if any(isinstance(t, ast.Name) and t.id == name for t in ast.walk(target)):
                    out.append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                out.append(node.value)
        elif isinstance(node, ast.NamedExpr) and node.target.id == name:
            out.append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if any(isinstance(t, ast.Name) and t.id == name for t in ast.walk(node.target)):
                out.append(node.iter)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            while isinstance(owner, ast.Subscript):
                owner = owner.value
            if isinstance(owner, ast.Name) and owner.id == name and \
                    node.func.attr in _ELEMENT_METHODS | _SEQUENCE_METHODS | {"insert"}:
                out.extend(node.args)
    return out


def _carriers(expr, readers: set, mark: str):
    """The names whose value `expr` can carry into a label."""
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in readers:
                continue  # Linear's data, not the caller's argument
            if isinstance(node.func, ast.Attribute):
                stack.append(node.func.value)
            stack.extend(node.args)
            stack.extend(k.value for k in node.keywords)
        elif isinstance(node, ast.JoinedStr):
            # A template whose own text already rules the mark out carries
            # nothing a caller could turn into it.
            if _can_be(_template(node), mark):
                stack.extend(ast.iter_child_nodes(node))
        else:
            stack.extend(ast.iter_child_nodes(node))


def _flows(expr, fn, readers: set, mark: str) -> set:
    names: set = set()
    stack = [expr]
    while stack:
        for name in _carriers(stack.pop(), readers, mark):
            if name not in names:
                names.add(name)
                stack.extend(_assigned(fn, name))
    return names


def _label_arguments(call, params: LabelParams) -> list:
    """[(argument, selector)] — the arguments of this call that carry a label,
    by the callee's shape. Trailing flags come back as one synthetic tuple,
    read through the `--label` selector."""
    out = [(call.args[i], ()) for i in params.positions if i < len(call.args)
           and not any(isinstance(a, ast.Starred) for a in call.args[:i + 1])]
    starred = next((i for i, a in enumerate(call.args) if isinstance(a, ast.Starred)), None)
    if starred is not None and params.positions and starred <= max(params.positions):
        out += [(a.value, ()) for a in call.args[starred:] if isinstance(a, ast.Starred)]
    out += [(k.value, ()) for k in call.keywords if k.arg in params.names]
    for unpacked in (k.value for k in call.keywords if k.arg is None):
        if isinstance(unpacked, ast.Dict):  # `**{"label_name": …}`, read by its keys
            out += [(v, ()) for key, v in zip(unpacked.keys, unpacked.values)
                    if key is None or (isinstance(key, ast.Constant) and key.value in params.names)]
    if params.flags is not None and len(call.args) > params.flags:
        out.append((ast.Tuple(elts=list(call.args[params.flags:]), ctx=ast.Load()), (_FLAG,)))
    return out


def _unpacked_unread(call, params: LabelParams) -> list:
    """What a `**` unpacking into a label-taking seam call hides: a mapping
    this cannot read key by key could carry the label under its name."""
    if not params.names:
        return []
    out = []
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        value = keyword.value
        if not isinstance(value, ast.Dict):
            out.append(f"`**{_unparse(value)}` is unpacked into the seam, so the "
                       "label it may carry cannot be read")
        elif any(key is not None and not isinstance(key, ast.Constant) for key in value.keys):
            out.append(f"`**{_unparse(value)}` is keyed at run time, so the label "
                       "it may carry cannot be read")
    return out


def label_parameters(root: str = ROOT) -> dict:
    """Seam function → the `LabelParams` that carry a label to a lookup.

    Grown to a fixpoint from the two lookups: a parameter of a write-layer
    function carries a label when its value can reach a label argument of a
    function already known to — through local assignments, through helpers,
    through the flag parser — but never through a function that reads Linear,
    whose result is Linear's data. `remove_label` touches `labelIds` and is
    absent: nothing it is handed reaches a lookup.
    """
    tree, _ = _parse(os.path.join(root, "scripts", SEAM_MODULE))
    if tree is None:
        return {}
    funcs = _functions(tree)
    readers = _linear_readers(funcs)
    mark = _mark()
    known: dict = {}
    for name in LABEL_LOOKUPS:
        if name in funcs:
            positional = _positional_names(funcs[name])
            if len(positional) > LABEL_LOOKUP_ARG:
                known[name] = LabelParams((LABEL_LOOKUP_ARG,),
                                          (positional[LABEL_LOOKUP_ARG],), None)
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in LABEL_LOOKUPS:
                continue
            params = _param_names(fn)
            carried: set = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func.id if isinstance(node.func, ast.Name) else None
                if callee not in known or callee == name:
                    continue
                for arg, _ in _label_arguments(node, known[callee]):
                    carried |= _flows(arg, fn, readers, mark) & params
            if not carried:
                continue
            positional = _positional_names(fn)
            vararg = fn.args.vararg.arg if fn.args.vararg else None
            found = LabelParams(
                tuple(sorted(positional.index(p) for p in carried if p in positional)),
                tuple(sorted(p for p in carried if p != vararg)),
                len(positional) if vararg in carried else None,
            )
            if known.get(name) != found:
                known[name] = found
                changed = True
    return known


def seam_problems(root: str = ROOT) -> list:
    """Everything that sets a card's labels without going through the seam.

    Discovery is only as complete as the door is exclusive. A module that
    builds its own Linear issue mutation carrying `labelIds` is a writer
    `writes()` will never see, so it is reported here — the check on the check.
    Docstrings are prose, not mutations, and are not read.
    """
    problems: list[str] = []
    for path in _python_files(root):
        if os.path.basename(path) == SEAM_MODULE:
            continue
        tree, _ = _parse(path)
        if tree is None:
            continue
        docs = _docstrings(tree)
        strings = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
                   and isinstance(n.value, str) and id(n) not in docs]
        if not any(_ISSUE_MUTATION.search(n.value) for n in strings):
            continue
        rel = os.path.relpath(path, root)
        for node in strings:
            if _LABEL_FIELDS.search(node.value):
                problems.append(
                    f"{rel}:{node.lineno} sets a card's labels with Linear's "
                    f"`{LABEL_FIELD}` directly, outside {SEAM_MODULE} — a write "
                    "that does not go through the one door is a writer no "
                    "discovery here can see"
                )
    return problems


# --------------------------------------------------------------------------- #
# the tree, parsed once                                                        #
# --------------------------------------------------------------------------- #


class _Module:
    """One module, walked once: every node's parent, its imports, its calls
    and attribute reads, its module-level bindings and its command line."""

    def __init__(self, name: str, path: str, root: str, tree, source: str):
        self.name = name
        self.rel = os.path.relpath(path, root)
        self.tree = tree
        self.source = source
        self.funcs = _functions(tree)
        self.parents: dict = {}
        self.imports: dict = {}       # local name → module
        self.from_imports: dict = {}  # local name → (module, attribute)
        self.calls: list = []
        self.attributes: list = []
        self.sequences: list = []     # list and tuple displays: an argv, perhaps
        self.star_imports: list = []  # (module, line) for every `from m import *`
        dicts: list = []
        stack = [tree]
        while stack:
            node = stack.pop()
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node
                stack.append(child)
            if isinstance(node, ast.Call):
                self.calls.append(node)
            elif isinstance(node, ast.Attribute):
                self.attributes.append(node)
            elif isinstance(node, ast.Dict):
                dicts.append(node)
            elif isinstance(node, (ast.List, ast.Tuple)):
                self.sequences.append(node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = \
                        alias.name.split(".")[-1]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    if alias.name == "*":
                        self.star_imports.append((node.module.split(".")[-1], node.lineno))
                        continue
                    self.from_imports[alias.asname or alias.name] = (
                        node.module.split(".")[-1], alias.name)
        self.calls.sort(key=lambda n: (n.lineno, n.col_offset))
        self.attributes.sort(key=lambda n: (n.lineno, n.col_offset))
        self.assigns: dict = {}       # module-level name → [(value, index)]
        stack = list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.If, ast.Try, ast.With)):
                for part in ("body", "orelse", "finalbody", "handlers"):
                    stack.extend(getattr(node, part, []) or [])
                continue
            if isinstance(node, ast.ExceptHandler):
                stack.extend(node.body)
                continue
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                       and node.value is not None else [])
            for target in targets:
                if isinstance(target, ast.Name):
                    self.assigns.setdefault(target.id, []).append((node.value, None))
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for index, elt in enumerate(target.elts):
                        if isinstance(elt, ast.Name):
                            self.assigns.setdefault(elt.id, []).append((node.value, index))
        # A function the module's command line dispatches to by name.
        self.dispatch: dict = {}      # handler → (subcommand, ...)
        if "sys.argv" in source:
            for node in dicts:
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str) \
                            and isinstance(value, ast.Name) and value.id in self.funcs:
                        self.dispatch[value.id] = self.dispatch.get(value.id, ()) + (key.value,)

    def parent(self, node):
        return self.parents.get(id(node))

    def scopes(self, node) -> list:
        """The functions enclosing `node`, outermost first."""
        out = []
        current = self.parent(node)
        while current is not None:
            if isinstance(current, _SCOPE):
                out.append(current)
            current = self.parent(current)
        return out[::-1]


_MODULE_CACHE: dict = {}


class _Repo:
    def __init__(self, root: str):
        self.root = root
        self.modules: dict = {}
        for path in _python_files(root):
            name = os.path.basename(path)[:-3]
            if name in self.modules:
                continue
            tree, source = _parse(path)
            if tree is None:
                continue
            key = (root, path, id(tree))
            if key not in _MODULE_CACHE:
                _MODULE_CACHE[key] = _Module(name, path, root, tree, source)
            self.modules[name] = _MODULE_CACHE[key]
        self.calls: dict = {}  # called name → [(module, call)]
        for module in self.modules.values():
            for node in module.calls:
                name = _called_name(node.func)
                if name:
                    self.calls.setdefault(name, []).append((module, node))

    def module(self, name: str):
        return self.modules.get(name)


# --------------------------------------------------------------------------- #
# reading a label                                                              #
# --------------------------------------------------------------------------- #


class _Read:
    """What a label expression can be: literals and templates, what could not
    be read, the command lines it arrives on, and — for a label a caller
    handed in — the call site each value came from."""

    __slots__ = ("labels", "unread", "commands", "origins")

    def __init__(self, labels=(), unread=(), commands=(), origins=None):
        self.labels = set(labels)
        self.unread = list(unread)
        self.commands = set(commands)
        self.origins = dict(origins or {})

    def add(self, other: "_Read") -> "_Read":
        self.labels |= other.labels
        self.unread += [u for u in other.unread if u not in self.unread]
        self.commands |= other.commands
        for atom, where in other.origins.items():
            self.origins[atom] = self.origins.get(atom, frozenset()) | where
        return self


def _unknown(why: str) -> _Read:
    return _Read(unread=(why,))


def _template(node) -> tuple:
    parts = [""]
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts[-1] += str(value.value)
        else:
            parts.append("")
    return tuple(parts)


class _Ctx:
    """Where an expression is read: its module, the function it sits in, what
    that function's parameters are bound to (None: unknown, find the callers),
    the enclosing function's context, and comprehension variables in scope."""

    __slots__ = ("mod", "fn", "bindings", "outer", "overlay")

    def __init__(self, mod, fn=None, bindings=None, outer=None, overlay=None):
        self.mod = mod
        self.fn = fn
        self.bindings = bindings
        self.outer = outer
        self.overlay = overlay or {}

    def key(self) -> tuple:
        out = []
        ctx = self
        while ctx is not None:
            out.append((id(ctx.fn), id(ctx.bindings) if ctx.bindings is not None else None,
                        tuple(sorted((k, id(v[0])) for k, v in ctx.overlay.items()))))
            ctx = ctx.outer
        return tuple(out)


class _Reader:
    def __init__(self, repo: _Repo, seam_names: set, vocabulary: set, forward: bool = True):
        self.repo = repo
        self.seam_names = seam_names
        self.vocabulary = vocabulary
        self.forward = forward
        self._active: set = set()
        self._forwarded: dict = {}
        self._fields: dict = {}

    # -- contexts ------------------------------------------------------------

    def ctx_at(self, node, mod) -> _Ctx:
        """The context of `node`: its enclosing functions, outermost first,
        none of them bound — their callers are found when a label needs them."""
        ctx = _Ctx(mod)
        for fn in mod.scopes(node):
            ctx = _Ctx(mod, fn, None, ctx)
        return ctx

    def ctx_of(self, fn, mod, bindings) -> _Ctx:
        ctx = _Ctx(mod)
        for outer in mod.scopes(fn):
            ctx = _Ctx(mod, outer, None, ctx)
        return _Ctx(mod, fn, bindings, ctx)

    # -- the entry point -----------------------------------------------------

    def read(self, node, ctx: _Ctx, path: tuple = ()) -> _Read:
        key = (id(node), ctx.key(), path)
        if key in self._active:
            return _Read()  # a cycle adds nothing the other paths do not
        if len(self._active) > _MAX_DEPTH:
            return _unknown(f"`{_unparse(node)}` is too deep to follow")
        self._active.add(key)
        try:
            return self._read(node, ctx, path)
        finally:
            self._active.discard(key)

    def _read(self, node, ctx, path) -> _Read:
        if isinstance(node, ast.Constant):
            if node.value is None or isinstance(node.value, (bool, int, float)):
                return _Read()
            if isinstance(node.value, str) and not path:
                return _Read({node.value})
            return _unknown(f"`{_unparse(node)}`")
        if isinstance(node, ast.JoinedStr):
            if path:
                return _unknown(f"`{_unparse(node)}`")
            parts = _template(node)
            formatted = [v for v in node.values if isinstance(v, ast.FormattedValue)]
            if parts == ("", "") and len(formatted) == 1 and formatted[0].format_spec is None:
                return self.read(formatted[0].value, ctx)
            return _Read({parts})
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return self._sequence(node, ctx, path)
        if isinstance(node, ast.Dict):
            return self._mapping(node, ctx, path)
        if isinstance(node, ast.BoolOp):
            out = _Read()
            for value in node.values:
                out.add(self.read(value, ctx, path))
            return out
        if isinstance(node, ast.IfExp):
            return self.read(node.body, ctx, path).add(self.read(node.orelse, ctx, path))
        if isinstance(node, (ast.Starred, ast.NamedExpr)):
            return self.read(node.value, ctx, path)
        if isinstance(node, ast.Subscript):
            return self._subscript(node, ctx, path)
        if isinstance(node, ast.Name):
            return self._name(node, ctx, path)
        if isinstance(node, ast.Attribute):
            return self._attribute(node, ctx, path)
        if isinstance(node, ast.Call):
            return self._call(node, ctx, path)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._comprehension(node, ctx, path)
        return _unknown(f"`{_unparse(node)}`")

    # -- containers ----------------------------------------------------------

    def _sequence(self, node, ctx, path) -> _Read:
        if path and path[0] == _FLAG:
            out = _Read()
            elts = node.elts
            for index, elt in enumerate(elts):
                if isinstance(elt, ast.Starred):
                    out.add(self.read(elt.value, ctx, path))
                elif index and isinstance(elts[index - 1], ast.Constant) \
                        and elts[index - 1].value == LABEL_FLAG:
                    out.add(self.read(elt, ctx, path[1:]))
            return out
        if path and path[0][0] == "index" and isinstance(node, ast.Tuple) \
                and not any(isinstance(e, ast.Starred) for e in node.elts):
            index = path[0][1]
            if index < len(node.elts):
                return self.read(node.elts[index], ctx, path[1:])
            return _Read()
        out = _Read()
        for elt in node.elts:
            out.add(self.read(elt, ctx, path))
        return out

    def _mapping(self, node, ctx, path) -> _Read:
        out = _Read()
        if path and path[0][0] == "key":
            for key, value in zip(node.keys, node.values):
                if key is None:
                    out.add(self.read(value, ctx, path))
                elif isinstance(key, ast.Constant) and key.value == path[0][1]:
                    out.add(self.read(value, ctx, path[1:]))
                elif not isinstance(key, ast.Constant):
                    out.add(_unknown(f"a mapping keyed by `{_unparse(key)}`"))
            return out
        for value in node.values:
            out.add(self.read(value, ctx, path))
        return out

    def _subscript(self, node, ctx, path) -> _Read:
        index = node.slice
        if isinstance(index, ast.Constant) and isinstance(index.value, str):
            if index.value == MARKS_KEY and not path and self._vocabulary_record(node.value, ctx):
                return _Read(self.vocabulary)
            return self.read(node.value, ctx, (("key", index.value),) + path)
        if isinstance(index, ast.Constant) and isinstance(index.value, int):
            return self.read(node.value, ctx, (("index", index.value),) + path)
        return self.read(node.value, ctx, path)

    def _vocabulary_record(self, node, ctx) -> bool:
        """`node` is a call into a module that reads one of the vocabularies —
        so a `marks` field read off it is one of the vocabularies' marks."""
        if not isinstance(node, ast.Call):
            return False
        found = self._def_of(node.func, ctx)
        return found is not None and any(v in found[0].source for v in VOCABULARIES)

    # -- names ---------------------------------------------------------------

    def _name(self, node, ctx, path) -> _Read:
        name = node.id
        scope = ctx
        while scope is not None:
            if name in scope.overlay:
                iterated, ictx, conditions = scope.overlay[name]
                guarded = self._guards(name, conditions, ictx)
                return guarded if guarded is not None else self.read(iterated, ictx, path)
            if scope.fn is not None:
                found = self._local(name, node, scope, path)
                if found is not None:
                    return found
            scope = scope.outer
        return self._module_name(ctx.mod, name, path)

    def _guards(self, name, conditions, ctx):
        """The label a literal guard proves: under `name.startswith(C)` it is
        the template `C*`, under `name == C` it is `C`. None when no guard."""
        for test in conditions:
            for clause in (test.values if isinstance(test, ast.BoolOp)
                           and isinstance(test.op, ast.And) else [test]):
                if isinstance(clause, ast.Call) and isinstance(clause.func, ast.Attribute) \
                        and clause.func.attr == "startswith" and clause.args \
                        and isinstance(clause.func.value, ast.Name) \
                        and clause.func.value.id == name:
                    prefix = self.read(clause.args[0], ctx)
                    if prefix.labels and not prefix.unread and \
                            all(isinstance(p, str) for p in prefix.labels):
                        return _Read({(p, "") for p in prefix.labels})
                if isinstance(clause, ast.Compare) and len(clause.ops) == 1 \
                        and isinstance(clause.ops[0], ast.Eq) \
                        and isinstance(clause.left, ast.Name) and clause.left.id == name:
                    return self.read(clause.comparators[0], ctx)
        return None

    def _local(self, name, use, ctx, path):
        """`name` read inside `ctx.fn`: under a guard, through every binding
        in the function, and through its parameter. None when it is none of
        the function's own."""
        fn = ctx.fn
        mod = ctx.mod
        conditions = []
        current, child = mod.parent(use), use
        while current is not None and current is not fn:
            if isinstance(current, (ast.If, ast.While)) and child in current.body:
                conditions.append(current.test)
            current, child = mod.parent(current), current
        guarded = self._guards(name, conditions, ctx)
        if guarded is not None:
            return guarded

        # Inside a loop over it, `name` is that loop's element — and whatever
        # the loop body itself assigns it — never what another loop bound.
        region = fn
        current = mod.parent(use)
        while current is not None and current is not fn:
            if isinstance(current, (ast.For, ast.AsyncFor)) \
                    and self._target(current.target, name) is not None:
                region = current
                break
            current = mod.parent(current)

        found = False
        out = _Read()
        if region is not fn:
            found = True
            hit = self._target(region.target, name)
            out.add(self.read(region.iter, ctx, path) if not hit
                    else _unknown(f"`{name}` unpacked from `{_unparse(region.iter)}`"))
        scope = (_walk_scope(region) if region is not fn or not isinstance(fn, ast.Lambda)
                 else ())
        for node in scope:
            if node is region:
                continue
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    hit = self._target(target, name)
                    if hit is not None:
                        found = True
                        out.add(self.read(node.value, ctx, hit + path))
                    rest = self._stored(target, name, path)
                    if rest is not None:
                        found = True
                        out.add(self.read(node.value, ctx, rest))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(node.target, ast.Name) and node.target.id == name \
                        and node.value is not None:
                    found = True
                    out.add(self.read(node.value, ctx, path))
            elif isinstance(node, ast.NamedExpr) and node.target.id == name:
                found = True
                out.add(self.read(node.value, ctx, path))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                hit = self._target(node.target, name)
                if hit is not None:
                    found = True
                    out.add(self.read(node.iter, ctx, path) if not hit
                            else _unknown(f"`{name}` unpacked from `{_unparse(node.iter)}`"))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None and \
                            self._target(item.optional_vars, name) is not None:
                        found = True
                        out.add(_unknown(f"`{name}` from `{_unparse(item.context_expr)}`"))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any((a.asname or a.name.split(".")[0]) == name for a in node.names):
                    return None  # a module, read at module level
            elif isinstance(node, ast.Call):
                hit = self._mutation(node, name, path) \
                    if isinstance(node.func, ast.Attribute) else None
                if hit is not None:
                    found = True
                    out.add(hit(ctx))
                filled = self._filled_by(node, name, ctx, path)
                if filled is not None:
                    found = True
                    out.add(filled)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == name:
                return _unknown(f"`{name}` is a definition, not a label")
        if region is fn and name in _param_names(fn):
            found = True
            out.add(self._parameter(name, ctx, path))
        return out if found else None

    @staticmethod
    def _target(target, name):
        """The selector path at which an assignment target binds `name`, or None."""
        if isinstance(target, ast.Name):
            return () if target.id == name else None
        if isinstance(target, (ast.Tuple, ast.List)):
            for index, elt in enumerate(target.elts):
                if isinstance(elt, ast.Name) and elt.id == name:
                    return (("index", index),)
        return None

    @staticmethod
    def _within(node, name, path):
        """`node` is `name`, or a subscript of it — `name["k"]["j"]` — and
        `path` reaches it: the rest of `path` past those keys, or None."""
        keys = []
        while isinstance(node, ast.Subscript):
            key = node.slice
            keys.insert(0, ("key", key.value) if isinstance(key, ast.Constant)
                        and isinstance(key.value, str) else None)
            node = node.value
        if not (isinstance(node, ast.Name) and node.id == name):
            return None
        rest = path
        for key in keys:
            if rest and rest[0][0] == "key":
                if key is not None and key != rest[0]:
                    return None
                rest = rest[1:]
            elif rest and key is not None:
                return None
        return rest

    def _stored(self, target, name, path):
        """`name["k"] = v`: the selector `v` is read through, or None."""
        if not isinstance(target, ast.Subscript):
            return None
        return self._within(target, name, path)

    def _mutation(self, call, name, path):
        """A call that puts something into `name` — `name.append(x)`,
        `name["k"].extend(xs)` — as a reader of what it puts in, or None."""
        method = call.func.attr
        if method not in _ELEMENT_METHODS | _SEQUENCE_METHODS | {"insert"}:
            return None
        rest = self._within(call.func.value, name, path)
        if rest is None:
            return None
        args = call.args[-1:] if method == "insert" else call.args
        return lambda ctx: self._union(args, ctx, rest)

    def _filled_by(self, call, name, ctx, path):
        """`name` handed bare to a function this can read, which puts things
        into its parameter: what it puts in, read there. None otherwise."""
        positions = [i for i, a in enumerate(call.args)
                     if isinstance(a, ast.Name) and a.id == name]
        keywords = [k.arg for k in call.keywords
                    if isinstance(k.value, ast.Name) and k.value.id == name and k.arg]
        if not positions and not keywords:
            return None
        found = self._def_of(call.func, ctx)
        if found is None:
            return None
        dmod, fn = found
        positional = _positional_names(fn)
        params = [positional[i] for i in positions if i < len(positional)] + keywords
        bindings = self._bind(fn, call, ctx, False)
        if bindings is None:
            return None
        fctx = self.ctx_of(fn, dmod, bindings)
        out = None
        for node in _walk_scope(fn):
            for param in params:
                filled = None
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    hit = self._mutation(node, param, path)
                    filled = hit(fctx) if hit is not None else None
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        rest = self._stored(target, param, path)
                        if rest is not None:
                            filled = self.read(node.value, fctx, rest)
                elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                        and node.target.id == param:
                    filled = self.read(node.value, fctx, path)
                if filled is not None:
                    out = (out or _Read()).add(filled)
        return out

    def _union(self, nodes, ctx, path) -> _Read:
        out = _Read()
        for node in nodes:
            out.add(self.read(node, ctx, path))
        return out

    def _module_name(self, mod, name, path) -> _Read:
        if name in mod.assigns:
            out = _Read()
            top = _Ctx(mod)
            for value, index in mod.assigns[name]:
                selector = (("index", index),) if index is not None else ()
                out.add(self.read(value, top, selector + path))
            return out
        if name in mod.from_imports:
            other_name, attr = mod.from_imports[name]
            other = self.repo.module(other_name)
            if other is not None:
                return self._module_name(other, attr, path)
        return _unknown(f"`{name}` is not defined in {mod.rel} where this can read it")

    def _attribute(self, node, ctx, path) -> _Read:
        owner = node.value
        if isinstance(owner, ast.Name) and owner.id in ctx.mod.imports:
            other = self.repo.module(ctx.mod.imports[owner.id])
            if other is not None:
                return self._module_name(other, node.attr, path)
            return _unknown(f"`{_unparse(node)}` is outside scripts/")
        return self._field(node.attr, ctx.mod, path)

    def _field(self, attr, mod, path) -> _Read:
        """A field read off a record — every value the module hands a call as
        `attr=`, which is how the module's records are built."""
        key = (mod.name, attr, path)
        if key in self._fields:
            return self._fields[key]
        self._fields[key] = _Read()
        out = _Read()
        found = False
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.keyword) and node.arg == attr:
                found = True
                out.add(self.read(node.value, self.ctx_at(node, mod), path))
        if not found:
            out = _unknown(f"the field `.{attr}` is set nowhere in {mod.rel}")
        self._fields[key] = out
        return out

    # -- parameters and the callers that fill them ---------------------------

    def _parameter(self, name, ctx, path) -> _Read:
        fn = ctx.fn
        if ctx.bindings is not None:
            if name in ctx.bindings:
                expr, ectx = ctx.bindings[name]
                return self.read(expr, ectx, path)
            default = _param_default(fn, name)
            if default is not None:
                return self.read(default, ctx.outer or _Ctx(ctx.mod), path)
            if fn.args.vararg is not None and fn.args.vararg.arg == name:
                return _Read()
            return _unknown(f"`{name}` is not passed to `{getattr(fn, 'name', 'lambda')}`")
        if not self.forward:
            return _Read()  # the write layer's own parameter: its caller is the write
        key = (id(fn), name, path)
        if key not in self._forwarded:
            self._forwarded[key] = _Read()  # recursion through the same parameter
            self._forwarded[key] = self._forward(fn, name, ctx, path)
        return self._forwarded[key]

    def _forward(self, fn, name, ctx, path) -> _Read:
        """`name` is a parameter of `fn` and nothing here binds it: the label is
        whatever `fn`'s callers hand it, so read it at every caller."""
        mod = ctx.mod
        if isinstance(fn, ast.Lambda):
            return self._lambda(fn, name, ctx, path)
        method = isinstance(mod.parent(fn), ast.ClassDef)
        if fn.name in self.seam_names and (method or mod.name == SEAM_MODULE[:-3]):
            # A stand-in for the seam (an injectable `ops.create_card`): every
            # call of the name is discovered as a write of its own.
            return _Read()
        callers = self._callers(fn, mod, method)
        if not callers:
            if fn.name in mod.dispatch:
                return _Read(commands={(mod.name, fn.name, name)})
            default = _param_default(fn, name)
            if default is not None:
                return self.read(default, ctx.outer or _Ctx(mod), path)
            return _unknown(f"nothing calls `{fn.name}` ({mod.rel}) where this can read it")
        out = _Read()
        for cmod, call in callers:
            bindings = self._bind(fn, call, self.ctx_at(call, cmod), method)
            if bindings is None:
                out.add(_unknown(f"{cmod.rel}:{call.lineno} calls `{fn.name}` with "
                                 "arguments this cannot line up"))
                continue
            read = self._parameter(name, self.ctx_of(fn, mod, bindings), path)
            here = f"{cmod.rel}:{call.lineno}"
            for atom in read.labels:
                read.origins.setdefault(atom, frozenset((here,)))
            read.unread = [u if "handed in at" in u else f"{u}, handed in at {here}"
                           for u in read.unread]
            out.add(read)
        return out

    def _callers(self, fn, mod, method: bool) -> list:
        out = []
        scopes = mod.scopes(fn)
        for cmod, call in self.repo.calls.get(fn.name, []):
            func = call.func
            if scopes and not method:
                # A nested function: called by name inside its enclosing one.
                if cmod is mod and isinstance(func, ast.Name) and scopes[-1] in mod.scopes(call):
                    out.append((cmod, call))
                continue
            if isinstance(func, ast.Attribute):
                owner = func.value
                aliased = isinstance(owner, ast.Name) and owner.id in cmod.imports
                if method and not aliased:
                    out.append((cmod, call))
                elif not method and aliased and cmod.imports[owner.id] == mod.name:
                    out.append((cmod, call))
            elif not method and isinstance(func, ast.Name):
                if cmod is mod or cmod.from_imports.get(fn.name) == (mod.name, fn.name):
                    out.append((cmod, call))
        if not method and not scopes:
            for cmod in self.repo.modules.values():
                for alias, target in cmod.from_imports.items():
                    if target == (mod.name, fn.name) and alias != fn.name:
                        out += [(m, c) for m, c in self.repo.calls.get(alias, [])
                                if m is cmod and isinstance(c.func, ast.Name)]
        return out

    def _bind(self, fn, call, cctx, method: bool):
        """`fn`'s parameters → (expression, context) for this call, or None."""
        positional = _positional_names(fn)
        if method and positional and isinstance(call.func, ast.Attribute):
            positional = positional[1:]
        bindings: dict = {}
        extra = []
        for index, arg in enumerate(call.args):
            if isinstance(arg, ast.Starred):
                for rest in positional[index:]:
                    bindings.setdefault(rest, (arg.value, cctx))
                extra.append(arg)
                continue
            if index < len(positional):
                bindings[positional[index]] = (arg, cctx)
            else:
                extra.append(arg)
        if fn.args.vararg is not None:
            bindings[fn.args.vararg.arg] = (ast.Tuple(elts=extra, ctx=ast.Load()), cctx)
        for keyword in call.keywords:
            if keyword.arg is None:
                return None
            bindings[keyword.arg] = (keyword.value, cctx)
        return bindings

    def _lambda(self, fn, name, ctx, path) -> _Read:
        """A lambda handed to a function as `K=lambda …`: its parameter is what
        that function passes when it calls `K`."""
        mod = ctx.mod
        holder = mod.parent(fn)
        call = mod.parent(holder) if isinstance(holder, ast.keyword) else None
        if not isinstance(call, ast.Call):
            return _unknown(f"a lambda's `{name}`, and the lambda is not handed to a "
                            "function this can read")
        cctx = self.ctx_at(call, mod)
        found = self._def_of(call.func, cctx)
        if found is None:
            return _unknown(f"a lambda's `{name}`, handed to `{_unparse(call.func)}`, "
                            "which this cannot read")
        dmod, target = found
        method = isinstance(dmod.parent(target), ast.ClassDef)
        bindings = self._bind(target, call, cctx, method)
        if bindings is None:
            return _unknown(f"`{_unparse(call.func)}` is called with arguments this "
                            "cannot line up")
        index = [a.arg for a in fn.args.args].index(name) if name in \
            [a.arg for a in fn.args.args] else None
        if index is None:
            return _unknown(f"a lambda's `{name}`")
        top = self.ctx_of(target, dmod, bindings)
        out = _Read()
        used = False
        for node in ast.walk(target):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == holder.arg and index < len(node.args):
                used = True
                inner = top
                scopes = dmod.scopes(node)
                for scope in scopes[scopes.index(target) + 1:]:
                    inner = _Ctx(dmod, scope, None, inner)
                out.add(self.read(node.args[index], inner, path))
        return out if used else _Read()

    # -- calls ---------------------------------------------------------------

    def _def_of(self, func, ctx):
        """(module, function) a call's callee resolves to, or None."""
        mod = ctx.mod
        if isinstance(func, ast.Name):
            for scope in reversed(mod.scopes(func)):
                for node in _walk_scope(scope):
                    if isinstance(node, _FUNCTION) and node.name == func.id:
                        return mod, node
            if func.id in mod.funcs:
                return mod, mod.funcs[func.id]
            if func.id in mod.from_imports:
                other_name, attr = mod.from_imports[func.id]
                other = self.repo.module(other_name)
                if other is not None and attr in other.funcs:
                    return other, other.funcs[attr]
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id in mod.imports:
            other = self.repo.module(mod.imports[func.value.id])
            if other is not None and func.attr in other.funcs:
                return other, other.funcs[func.attr]
        return None

    def _call(self, node, ctx, path) -> _Read:
        func = node.func
        if isinstance(func, ast.Name) and func.id in _PASS_THROUGH \
                and func.id not in ctx.mod.funcs:
            return self._union(node.args[:1] + node.args[1:2] * (func.id == "next"), ctx, path)
        if isinstance(func, ast.Attribute):
            if func.attr in _STRING_METHODS and not node.args:
                return self.read(func.value, ctx, path)
            if func.attr == "fromkeys" and isinstance(func.value, ast.Name) \
                    and func.value.id == "dict" and node.args:
                return self.read(node.args[0], ctx, path)
            if func.attr == "get" and node.args and self._def_of(func, ctx) is None:
                key = node.args[0]
                selector = (("key", key.value),) if isinstance(key, ast.Constant) else ()
                out = self.read(func.value, ctx, selector + path)
                return out.add(self._union(node.args[1:2], ctx, path))
        found = self._def_of(func, ctx)
        if found is None:
            return _unknown(f"`{_unparse(node)}`")
        dmod, fn = found
        bindings = self._bind(fn, node, ctx, False)
        if bindings is None:
            return _unknown(f"`{_unparse(node)}` is called with arguments this cannot line up")
        fctx = self.ctx_of(fn, dmod, bindings)
        out = _Read()
        for inner in _walk_scope(fn):
            if isinstance(inner, ast.Return) and inner.value is not None:
                out.add(self.read(inner.value, fctx, path))
            elif isinstance(inner, (ast.Yield, ast.YieldFrom)) and inner.value is not None:
                out.add(self.read(inner.value, fctx, path))
        return out

    def _comprehension(self, node, ctx, path) -> _Read:
        overlay = dict(ctx.overlay)
        inner = ctx
        for generator in node.generators:
            if not isinstance(generator.target, ast.Name):
                return _unknown(f"`{_unparse(node)}` unpacks what it iterates")
            overlay[generator.target.id] = (generator.iter, inner, list(generator.ifs))
            inner = _Ctx(ctx.mod, ctx.fn, ctx.bindings, ctx.outer, dict(overlay))
        return self.read(node.elt, inner, path)


# --------------------------------------------------------------------------- #
# discovery: Python                                                            #
# --------------------------------------------------------------------------- #


def _vocabulary_files(root: str) -> list:
    return [os.path.join(root, "config", name) for name in VOCABULARIES]


def _marks_in(doc) -> list:
    """[(record name, marks)] for every `marks` list anywhere in `doc`."""
    out = []
    stack = [doc]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            if isinstance(node.get(MARKS_KEY), list):
                out.append((node.get("name"), node[MARKS_KEY]))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _load_vocabularies(root: str) -> dict:
    docs = {}
    for path in _vocabulary_files(root):
        rel = os.path.relpath(path, root)
        try:
            with open(path, encoding="utf-8") as fh:
                docs[rel] = json.load(fh)
        except (OSError, ValueError) as exc:
            docs[rel] = exc
    return docs


def _seam_call(call, mod, params: dict, seam: str) -> str | None:
    """The seam function this call writes through, or None."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id in mod.imports \
                and mod.imports[owner.id] != seam:
            return None  # another module's function of the same name
        return func.attr if func.attr in params else None
    if isinstance(func, ast.Name):
        if mod.name == seam and func.id in params:
            return func.id
        module, name = mod.from_imports.get(func.id, (None, None))
        if module == seam and name in params:
            return name
    return None


def _deferred_call(call, mod, repo):
    """(label argument, add argument) when this call plans a deferred label
    write, else None."""
    func = call.func
    for (module, function), (label, add) in DEFERRED_SEAMS.items():
        if isinstance(func, ast.Attribute):
            owner = func.value
            if func.attr != function or not (isinstance(owner, ast.Name)
                                            and mod.imports.get(owner.id) == module):
                continue
        elif isinstance(func, ast.Name):
            if func.id != function or not (mod.name == module or
                                           mod.from_imports.get(func.id) == (module, function)):
                continue
        else:
            continue
        target = repo.module(module)
        fn = target.funcs.get(function) if target else None
        if fn is None:
            continue
        positional = _positional_names(fn)

        def argument(name):
            for keyword in call.keywords:
                if keyword.arg == name:
                    return keyword.value
            index = positional.index(name) if name in positional else None
            return call.args[index] if index is not None and index < len(call.args) else None

        return argument(label), argument(add)
    return None


def _executor(arguments: list, mod) -> bool:
    """A deferred seam's own executor: in the seam's module, handing the write
    layer nothing but the planned record's label field."""
    for (module, _function), (label, _add) in DEFERRED_SEAMS.items():
        if mod.name == module and arguments and all(
                isinstance(a, ast.Attribute) and a.attr == label for a, _ in arguments):
            return True
    return False


def _indirect_seam(call, mod, params: dict, seam: str) -> str | None:
    """A call whose callee is chosen at run time among seam functions —
    `(linear_ops.add_label if add else linear_ops.remove_label)(…)`."""
    if not isinstance(call.func, (ast.IfExp, ast.BoolOp)):
        return None
    for node in ast.walk(call.func):
        if isinstance(node, ast.Attribute) and node.attr in params:
            owner = node.value
            if not (isinstance(owner, ast.Name) and owner.id in mod.imports
                    and mod.imports[owner.id] != seam):
                return node.attr
    return None


def _handed_on(node, mod, params: dict, seam: str) -> bool:
    """`node` names a label-taking seam function without calling it — the
    function handed on as a value, called somewhere this cannot follow — or
    reaches into the write layer by `getattr`. A callee chosen between seam
    functions is `_indirect_seam`'s, not this."""
    if isinstance(node, ast.Call):
        func, args = node.func, node.args
        if not (isinstance(func, ast.Name) and func.id == "getattr" and args
                and isinstance(args[0], ast.Name) and mod.imports.get(args[0].id) == seam):
            return False
        name = args[1] if len(args) > 1 else None
        return not (isinstance(name, ast.Constant) and name.value not in params)
    if node.attr not in params:
        return False
    owner = node.value
    if not (isinstance(owner, ast.Name) and mod.imports.get(owner.id) == seam):
        return False
    current, child = mod.parent(node), node
    while isinstance(current, (ast.IfExp, ast.BoolOp)):
        current, child = mod.parent(current), current
    return not (isinstance(current, ast.Call) and current.func is child)


def _write(where, how, expression, read: _Read) -> LabelWrite:
    # A template that is nothing but substitution says nothing about the label.
    open_ended = [a for a in read.labels if not isinstance(a, str) and not any(a)]
    read = _Read(read.labels - set(open_ended),
                 read.unread + [f"`{expression}` is substituted at run time"] * bool(open_ended),
                 read.commands, read.origins)
    atoms = tuple(sorted(read.labels, key=_render))
    labels = tuple(sorted({_render(a) for a in atoms}))
    via = tuple(sorted(f"{module}.py `{handler}` ({name})"
                       for module, handler, name in read.commands))
    origins = tuple((atom, tuple(sorted(read.origins[atom])))
                    for atom in atoms if atom in read.origins)
    return LabelWrite(where, how, expression, labels, tuple(read.unread), via, atoms,
                      origins)


def _python_writes(repo: _Repo, params: dict, reader: _Reader):
    """(writes, command lines a label arrives on)."""
    seam = SEAM_MODULE[:-3]
    inside = _Reader(repo, reader.seam_names, reader.vocabulary, forward=False)
    found: list[LabelWrite] = []
    commands: set = set()
    doors = {seam} | {module for module, _ in DEFERRED_SEAMS}
    for mod in repo.modules.values():
        for module, line in mod.star_imports:
            if module in doors and module != mod.name:
                found.append(_write(
                    f"{mod.rel}:{line}", "python", f"from {module} import *",
                    _unknown(f"every name in {module} is imported with `*`, so its "
                             "calls cannot be told from this module's own")))
        if mod.name != seam:
            for node in mod.attributes:
                if _handed_on(node, mod, params, seam):
                    found.append(_write(
                        f"{mod.rel}:{node.lineno}", "python", _unparse(node),
                        _unknown("the seam is handed on as a value, so its calls "
                                 "cannot be found")))
        for node in mod.calls:
            where = f"{mod.rel}:{node.lineno}"
            if mod.name != seam and _handed_on(node, mod, params, seam):
                found.append(_write(where, "python", _unparse(node),
                                    _unknown("the write layer is reached by name at run "
                                             "time, so the label cannot be read")))
                continue
            deferred = _deferred_call(node, mod, repo)
            if deferred is not None:
                ctx = reader.ctx_at(node, mod)
                label, add = deferred
                if isinstance(add, ast.Constant) and not add.value:
                    continue  # a planned removal
                read = reader.read(label, ctx) if label is not None \
                    else _unknown("no label argument")
                commands |= read.commands
                found.append(_write(where, "python", _unparse(label), read))
                continue
            called = _seam_call(node, mod, params, seam) or _indirect_seam(node, mod, params, seam)
            if called is None:
                continue
            arguments = _label_arguments(node, params[called])
            hidden = _unpacked_unread(node, params[called])
            if not (arguments or hidden) or _executor(arguments, mod):
                continue
            ctx = reader.ctx_at(node, mod)
            read = _Read(unread=hidden)
            use = inside if mod.name == seam else reader
            for argument, selector in arguments:
                read.add(use.read(argument, ctx, selector))
            if mod.name == seam:
                # The write layer handing on what its own caller supplied is the
                # door, not a writer: the caller's call site is the write.
                read.unread = []
                if not read.labels:
                    continue
            commands |= read.commands
            expression = ", ".join(_unparse(a) for a, _ in arguments) or _unparse(node)
            found.append(_write(where, "python", expression, read))
    return found, commands


# --------------------------------------------------------------------------- #
# discovery: the command line, in the workflows and the scripts                #
# --------------------------------------------------------------------------- #

_CONTINUATION = re.compile(r"\\\s*\n\s*")
_INVOCATION = re.compile(r"\b(?P<module>\w+)\.py[\"']?(?P<rest>(?:\\\s*\n|[^\n])*)")
_SHELL_VALUE = re.compile(r"\$\{\{.*?\}\}|\$\{[^}]*\}|\$\([^)]*\)|\$\w+|\$\{\{.*")


def _command_lines(repo: _Repo, params: dict, commands: set) -> dict:
    """module → {subcommand: LabelParams} for every command line a label
    arrives on: the write layer's own, and every one a write above traced a
    label to."""
    out: dict = {}
    seam = repo.module(SEAM_MODULE[:-3])
    if seam is not None:
        for handler, subcommands in seam.dispatch.items():
            if handler in params:
                for sub in subcommands:
                    out.setdefault(seam.name, {})[sub] = params[handler]
    for module, handler, name in commands:
        mod = repo.module(module)
        fn = mod.funcs.get(handler) if mod else None
        if fn is None:
            continue
        positional = _positional_names(fn)
        vararg = fn.args.vararg.arg if fn.args.vararg else None
        entry = LabelParams(
            (positional.index(name),) if name in positional else (),
            (name,) if name != vararg else (),
            len(positional) if name == vararg else None,
        )
        for sub in mod.dispatch.get(handler, ()):
            out.setdefault(module, {})[sub] = entry
    return out


def _shell_args(rest: str) -> list:
    try:
        return shlex.split(rest, comments=True)
    except ValueError:
        return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', rest)]


def _shell_label(word: str):
    """A shell word as a label value: a literal, or a template where the shell
    or the workflow substitutes."""
    if "$" not in word:
        return word
    return tuple(_SHELL_VALUE.split(word))


def _text_writes(rel: str, how: str, text: str, lines: dict, first_line: int = 1) -> list:
    found = []
    for match in _INVOCATION.finditer(text):
        table = lines.get(match.group("module"))
        if not table:
            continue
        args = _shell_args(_CONTINUATION.sub(" ", match.group("rest")))
        if not args or args[0] not in table:
            continue
        entry = table[args[0]]
        rest = args[1:]
        values, unread = [], []
        for index in entry.positions:
            if index < len(rest):
                values.append(rest[index])
            else:
                unread.append(f"no argument at position {index + 1}")
        if entry.flags is not None:
            tail = rest[entry.flags:]
            values += [tail[i + 1] for i, word in enumerate(tail[:-1]) if word == LABEL_FLAG]
        if not values and not unread:
            continue
        line = text.count("\n", 0, match.start()) + first_line
        read = _Read({_shell_label(v) for v in values}, unread)
        found.append(_write(f"{rel}:{line}", how,
                            " ".join(shlex.quote(a) for a in args[:6]), read))
    return found


def _names_script(node, mod, script: str) -> bool:
    """`node` is the path of `script` in an argv: a literal ending in it, or a
    module constant built from one."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and os.path.basename(node.value) == script
    if isinstance(node, ast.Name):
        return any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                   and os.path.basename(c.value) == script
                   for value, _ in mod.assigns.get(node.id, []) for c in ast.walk(value))
    return False


def _python_command_writes(repo: _Repo, reader, lines: dict) -> list:
    """An argv a Python module builds to run a command line a label arrives on
    — `[sys.executable, <the write layer>, "add-label", card, label]`."""
    found = []
    for mod in repo.modules.values():
        for node in mod.sequences:
            elts = node.elts
            for index, elt in enumerate(elts[:-1]):
                table = next((t for m, t in lines.items()
                              if _names_script(elt, mod, f"{m}.py")), None)
                sub = elts[index + 1]
                if not table or not (isinstance(sub, ast.Constant) and sub.value in table):
                    continue
                entry, rest = table[sub.value], elts[index + 2:]
                ctx = reader.ctx_at(node, mod)
                read = _Read()
                for position in entry.positions:
                    read.add(reader.read(rest[position], ctx) if position < len(rest)
                             else _unknown(f"no argument at position {position + 1}"))
                if entry.flags is not None:
                    tail = ast.Tuple(elts=list(rest[entry.flags:]), ctx=ast.Load())
                    read.add(reader.read(tail, ctx, (_FLAG,)))
                found.append(_write(f"{mod.rel}:{node.lineno}", "python", _unparse(node), read))
    return found


#: Where a string a module builds is filled in at run time — an f-string's
#: hole, a `%` or `.format` placeholder, a value concatenated in — spelled as
#: a shell substitution, so a label there reads as a template like any other.
_HOLE = "${_}"
_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdirf]")


def _built_string(node):
    """(text, the nodes filled into it) for a string a module builds — a
    literal, an f-string, a `+` concatenation or a `%` format — or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _PLACEHOLDER.sub(lambda _: _HOLE, node.value), []
    if isinstance(node, ast.JoinedStr):
        text, filled = "", []
        for value in node.values:
            if isinstance(value, ast.Constant):
                text += str(value.value)
            else:
                text += _HOLE
                filled.append(value)
        return text, filled
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _built_string(node.left)
        if isinstance(node.op, ast.Mod):
            return (left[0], left[1] + [node.right]) if left else None
        right = _built_string(node.right)
        if left is None and right is None:
            return None
        ltext, lfilled = left or (_HOLE, [node.left])
        rtext, rfilled = right or (_HOLE, [node.right])
        return ltext + rtext, lfilled + rfilled
    return None


def _python_string_writes(repo: _Repo, lines: dict) -> list:
    """A command line a Python module spells as one string — for a shell, an
    `os.system`, a `shell=True` — read as the workflows' are. What is filled
    in at run time reads as a template, and a label that is nothing but one
    is reported UNREAD."""
    found = []
    for mod in repo.modules.values():
        docs = _docstrings(mod.tree)
        stack = [mod.tree]
        while stack:
            node = stack.pop()
            built = None if id(node) in docs else _built_string(node)
            if built is None:
                stack.extend(ast.iter_child_nodes(node))
                continue
            text, filled = built
            stack.extend(filled)
            if ".py" in text:
                found += _text_writes(mod.rel, "python", text, lines, node.lineno)
    return found


def _github_yaml(root: str) -> list:
    """Every file under `.github/` a step can run in: the workflows and the
    composite actions, spelled `.yml` or `.yaml`, at any depth."""
    return sorted(
        path for ext in ("yml", "yaml")
        for path in glob.glob(os.path.join(root, ".github", "**", f"*.{ext}"), recursive=True)
    )


def _delegated(root: str) -> set:
    out: set = set()
    for path in _github_yaml(root):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    match = re.match(r"^\s*run:\s*(?P<value>\S.*)$", line)
                    script = step_shell.delegated_script(match.group("value")) if match else None
                    if script:
                        out.add(script)
        except OSError:
            continue
    return out


def _shell_writes(root: str, lines: dict) -> list:
    found = []
    for path in _github_yaml(root):
        try:
            text = step_shell.workflow_source(os.path.abspath(path), root)
        except (OSError, UnicodeDecodeError):
            continue
        found += _text_writes(os.path.relpath(path, root), "workflow", text, lines)
    delegated = _delegated(root)
    for path in sorted(glob.glob(os.path.join(root, "scripts", "**", "*.sh"), recursive=True)):
        rel = os.path.relpath(path, root)
        if rel.replace(os.sep, "/") in delegated:
            continue  # read through the workflow that runs it, above
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        found += _text_writes(rel, "shell", text, lines)
    return found


# --------------------------------------------------------------------------- #
# discovery: the vocabularies                                                  #
# --------------------------------------------------------------------------- #


def config_writes(root: str = ROOT, docs: dict | None = None) -> list:
    """Every label a vocabulary's `marks` puts on a card — the stamp and the
    sweep apply them. `docs` (relpath → document) stands in for the files."""
    docs = docs if docs is not None else _load_vocabularies(root)
    found = []
    for rel, doc in docs.items():
        if isinstance(doc, Exception):
            found.append(LabelWrite(rel, "config", MARKS_KEY, (),
                                    (f"the vocabulary could not be read ({doc})",)))
            continue
        for name, marks in _marks_in(doc):
            labels = tuple(m for m in marks if isinstance(m, str))
            unread = tuple(f"`{m!r}` is not a label" for m in marks if not isinstance(m, str))
            found.append(LabelWrite(f"{rel} {name or '<unnamed>'}", "config",
                                    f"{MARKS_KEY} {list(marks)}", labels, unread))
    return found


def _vocabulary(root: str) -> set:
    marks = set()
    for write in config_writes(root):
        marks |= set(write.labels)
    return marks


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def writes(root: str = ROOT) -> tuple:
    """Every place a label can be put on a card, discovered rather than listed."""
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 20000))
    try:
        repo = _Repo(root)
        params = label_parameters(root)
        reader = _Reader(repo, set(params), _vocabulary(root))
        found, commands = _python_writes(repo, params, reader)
        lines = _command_lines(repo, params, commands)
        found += _python_command_writes(repo, reader, lines)
        found += _python_string_writes(repo, lines)
        found += _shell_writes(root, lines)
        found += config_writes(root)
    finally:
        sys.setrecursionlimit(limit)
    return tuple(found)


def _judge(write: LabelWrite, mark: str) -> list:
    out = []
    atoms = [a for a in (write.atoms or write.labels) if _can_be(a, mark)]
    if atoms:
        hits = sorted({_render(a) for a in atoms})
        handed = sorted({w for atom, sites in write.origins if atom in atoms for w in sites})
        source = f", handed in at {', '.join(handed)}" if handed else ""
        out.append(("FAIL", (
            f"{write.where} can write {mark!r}: its label ({write.expression}) "
            f"reads as {', '.join(repr(h) for h in hits)}{source} — the CEO's "
            "mark, which he applies by hand and nothing automatic may (DRE-6225)"
        )))
    if write.unread:
        out.append(("UNREAD", (
            f"{write.where} hands the label seam a label this cannot read "
            f"({write.expression}): {'; '.join(write.unread)} — unknown is not a "
            "pass. Name the label at the call site or as a module constant"
        )))
    return out


def config_problems(docs: dict, mark: str | None = None) -> list:
    """The vocabularies' own findings, for documents in hand."""
    mark = mark or _mark()
    return [text for write in config_writes(docs=docs) for _, text in _judge(write, mark)]


def findings(root: str = ROOT, discovered: tuple | None = None) -> list:
    """[(kind, text)] — SEAM, FAIL or UNREAD — for everything that can write
    the mark or cannot be read. `discovered` is `writes(root)`, when in hand."""
    mark = _mark()
    out = [("SEAM", text) for text in seam_problems(root)]
    for write in (discovered if discovered is not None else writes(root)):
        out += _judge(write, mark)
    return out


def problems(root: str = ROOT) -> list:
    """Every label write that can produce the mark, every one this cannot
    read, and every label mutation outside the seam."""
    return [text for _, text in findings(root)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if (args.command or "check") != "check":
        parser.print_usage(sys.stderr)
        return 2

    discovered = writes()
    found = findings(discovered=discovered)
    for kind, text in found:
        print(f"  [{kind}] {text}")
    by_how: dict = {}
    for write in discovered:
        by_how[write.how] = by_how.get(write.how, 0) + 1
    print(
        f"{len(discovered)} label write(s) discovered "
        f"({', '.join(f'{n} {how}' for how, n in sorted(by_how.items()))}); "
        f"the mark is {_mark()!r}; {len(found)} problem(s)"
    )
    print("  not checkable from here: a hand write in Linear; a label computed "
          "from data at run time is reported UNREAD")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
