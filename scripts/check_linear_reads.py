#!/usr/bin/env python3
"""Refuse any new direct Linear read in the pipeline's scripts and workflows
(DRE-9164), against one committed ledger of the reads that exist today.

On 2026-11-03, from 11:16 to 12:16 PT, this repo's own runs sent Linear 318
direct reads, counted from every run's `linear-read:` lines (DRE-8897), and new
direct readers kept landing. The CEO decided that day, at 11:58 PT, that this
check is built first, before anything is switched off. It discovers every
place that sends Linear a read itself and compares them with
`config/linear-direct-reads.json`, the list part two of the switch-off must
clear. A Linear write is allowed: the door the reads move to serves reads
only, so a write is not something the switch-off moves and never a finding.

WHAT A READ SITE IS. A site is the function whose own code hands a Linear
GraphQL document to a transport. It is the innermost function or method that
contains the call, named by its own name the way the `linear-read:` line's
`caller=` field names it: a method is `<module>.<method>`, its class not part
of the name (`groom_verify._Replacements._card` is the site
`groom_verify._card`), and an inner function is named by its own name (every
Hygiene board read is the one site `hygiene.linear`, the inner function of
`hygiene.read_only_linear`). Code outside any function is `<module>.<module>`.

THE SIX TRANSPORTS. The scanner reads the code of every Python file (an AST
walk) and recognizes exactly these:

  1. A call of `gql` or `gql_paged`, whatever it is read off: the bare name
     (imported from `linear_ops`, or a parameter or local of that name, as
     `hygiene.linear` calls the `gql` it was handed), or an attribute of that
     name on any expression at all — `linear_ops.gql(…)`, the module imported
     as `lops` (`planner_score.collect`), handed in as a parameter `lops` or
     `ops` (`groomer.read_population`, `hand_work_migration.read_population`)
     or kept on an object as `self.lops` (`groom_verify._card`). The receiver
     is never resolved or matched by name, so any receiver counts; nothing
     else in `scripts/` defines a `gql` or `gql_paged`. A callee that picks
     one — `(gql or linear_ops.gql)(…)` in `plan_initiative_context.block`,
     or `a if c else b` — is a transport when any of its operands is one.
     A callee bound to one is one too, and every binding is followed: an
     import under another name (`from linear_ops import gql as q`), a local,
     a module name, an enclosing function's local or a parameter's default
     assigned one (`run = linear_ops.gql`), `functools.partial` of one,
     `getattr(…, "gql")`, and `vars(linear_ops)["gql"]`. A `getattr` off
     `linear_ops` by a name the check cannot read counts too, failing closed.
     A function patched in as the transport (`linear_ops.gql = counted`, a
     counting seam) is plumbing like `gql`: the code calling it is the site.
  2. A `urllib.request.Request(…)` or `urlopen(…)`, under any import form,
     aimed at Linear: its URL is `linear_ops.API`, the bare `API` inside
     `linear_ops`, or a text holding `api.linear.app` however it is built —
     concatenated, an f-string, or names bound to its pieces
     (`check_linear_identities.viewer_of`, which prints no `linear-read:`
     line, so only this scan names it).
  3. A `Request(…)` or `urlopen(…)`, under any import form, in a function
     that also calls `api_key()`, bare or read off any receiver, whatever URL
     it names: the request carries the pipeline's Linear key, so it goes to
     Linear. `plan_initiative_context.fetch` downloads an approved design from
     Linear's upload host this way — no GraphQL document, a read all the same.
  4. Anything else aimed at Linear's host, failing closed: a child process or
     an HTTP client's verb (`subprocess.run`, `os.system`, `session.post`,
     `SENDERS`) handed `linear_ops.API` or a text holding `api.linear.app`,
     built the same ways, so a `curl` from a script is a site; and any literal
     in a function's own code holding `api.linear.app`, docstrings aside. A
     message naming the endpoint through the constant (`f"… from {API}"` in
     `linear_ops`'s errors) is not a literal, and is not a site.
  5. `linear_ops`'s own plumbing is not a site: `gql`, `gql_paged` and
     `_send`, which builds the `Request` behind both, are the transport every
     site calls. The functions that call them are the sites.
  6. In a workflow step (`.github/workflows/*.yml`), read whole — its `run:`
     block and every `with:` and `env:` value, so an `actions/github-script`
     step and a `run:` taking its URL from `env:` are both read: `curl` or
     `wget` to `api.linear.app`, an inline script naming `api.linear.app` or
     `linear_ops.API`, or a call of `gql(`. Each is a site named
     `<workflow file>#<job id>/<step name>`. A job's or the workflow's `env:`
     naming Linear is a site of its own, `<job id>/env` or `env`, since any
     step under it can send it. A step that runs an existing script is not a
     new site: the script's functions are.

A document whose operation is `mutation` is a write and is not a site. A
`query` or an anonymous `{ … }` document is a read. A document the check
cannot resolve statically — built at run time, or passed in from elsewhere, as
`hygiene.linear`'s is — counts as a read, so the check fails closed. A string
literal, a local assigned only string literals, and a module-level constant
(`VIEWER_QUERY`) are resolved; an f-string by the literal text it opens with.

WHICH FILES. Every `scripts/**/*.py`, walked recursively with `__pycache__`
skipped, plus `.github/workflows/*.yml`. `scripts/harness/` is in: the Harness
workflow runs it against the live board. `tests/` is out: no test talks to
Linear. A file directly in `scripts/` is named `<basename>.<function>`. A file
in a subdirectory is named by its path below `scripts/`, `/` written `.` and
`.py` dropped, then the function: `harness.scenarios.lane_contract.board_states`.
That can never equal a top-level file's name. The runtime `linear-read:` line
names a nested site by its basename alone (`lane_contract.board_states`) — the
same read under a shorter name, not a different read.

SCOPE: THIS REPO ALONE. The check scans bureau-pipeline's `scripts/` and
`.github/workflows/` and nothing else. agent-bureau's scripts and workflows
are not scanned, and neither is any step a fleet repo adds to its own
workflows. A fleet repo's pipeline runs call this repo's reusable workflows, so
their reads are this repo's scripts and are scanned here. A read another repo
makes on its own, outside those scripts, is not seen by this check; when it
goes through `linear_ops` it still prints its `linear-read:` line, which the
budget check counts for that repo.

A NEW CALLER OF A LISTED SITE IS NOT A FINDING. The check refuses a new
sender, never a new use of one. A new script calling `linear_ops.get_issue`,
or a new `ctx.linear(…)` read in Hygiene, adds no site and passes, because
the site is still `linear_ops.get_issue` or `hygiene.linear`. So "refuses any
new direct read" means any new function that sends Linear a read itself.

THE LEDGER IS READ AT THE MERGE BASE, the rule `check_tdd_commits.py` applies
to `config/tdd-exemptions.json`, so a change cannot admit its own new read by
adding a row beside it. `--base <ref>` names the ref whose merge base with
`HEAD` the ledger and the base tree are read at; with none it is `HEAD`. The
head is the working tree. Four rules:

  1. Listed. A site is listed only when the ledger at the merge base and the
     ledger at the head both have a row naming it. Any other site is `new`,
     and fails — so a change may not remove the row of a site it keeps.
  2. Pending. A row whose site exists neither in the merge base's tree nor in
     the head's is `pending`: counted, printed, never a finding. A new read is
     admitted in two pull requests — its row alone first, then, once that has
     merged, the site. A rename lands the new name's row first the same way;
     the check cannot tell a rename from a new read, and does not try.
  3. Gone. A row at the head whose site exists in the merge base's tree but
     not in the head's is `gone`, and fails: a change that deletes a site
     removes its row with it. The base's sites are read by scanning the merge
     base's own tree (`git archive`), never inferred from the ledger.
  4. No ledger at the merge base — the first run. The head's ledger stands in
     for it, and one line says so. A head with no ledger fails.

A row is `move` (part two moves it before the fallback is switched off) or
`kept` (named on a card as a read the store cannot serve, so it carries
`card` and `reason`). Exit 0 clean, 1 on any finding, 2 when git cannot answer.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

TAG = "linear-reads-check:"
LEDGER = "config/linear-direct-reads.json"
SCHEMA = "linear-direct-reads/1"
SCRIPTS = "scripts"
WORKFLOWS = ".github/workflows"
HOST = "api.linear.app"
CLASSES = ("move", "kept")

#: The opening of every row whose function carries no `It stays on Linear
#: (DRE-8898)` reason, so no builder writes a reason of its own.
DEFAULT_REASON = "Not yet moved to the door; part two of the switch-off decides."
#: The opening of such a docstring's reason.
STAYS_MARK = "It stays on Linear (DRE-8898):"
#: The last sentence of every row whose site other code calls: every
#: `linear_ops.*` row, and `hygiene.linear`. Words a later card adds go
#: immediately before it.
NEW_CALLER_SENTENCE = "A new caller of this function is not a new read site."
#: The one `kept` row this card writes: `plan_initiative_context.fetch`.
KEPT_FETCH_REASON = (
    "Downloads an approved design file from Linear's upload host with the "
    "pipeline's Linear key. The console's store holds no file bytes, so only "
    "Linear can serve it."
)

TRANSPORTS = frozenset({"gql", "gql_paged"})
REQUESTS = frozenset({"Request", "urlopen"})
#: Calls that send what they are handed out of the process: a child process
#: (`subprocess`, `os`) or an HTTP client's verb. One whose argument names
#: Linear's API, however that argument is built, is a site.
SENDERS = REQUESTS | frozenset({
    "run", "call", "check_call", "check_output", "Popen", "system", "popen",
    "getoutput", "getstatusoutput", "get", "post", "put", "patch", "request", "open"})
#: Rule 4: the transport every site calls.
PLUMBING = frozenset({"linear_ops.gql", "linear_ops.gql_paged", "linear_ops._send"})
#: A part of a built text the check cannot read, and how many readings of
#: one text it keeps.
_HOLE = "\x00"
_TEXTS_CAP = 64
_CARD = re.compile(r"DRE-\d+")


@dataclass(frozen=True)
class Site:
    name: str
    path: str  # repo-relative
    line: int


# ── reading a GraphQL document ───────────────────────────────────────────────

_COMMENT = re.compile(r"#[^\n]*")


def operation(text: str) -> str | None:
    """`write` for a document whose operation is `mutation`, `read` for a
    `query`, a `subscription` or an anonymous `{ … }`, None when the text it
    opens with names no operation (an f-string opening on a placeholder)."""
    head = _COMMENT.sub("", text).lstrip(" \t\r\n,\ufeff")
    if not head:
        return None
    if head.startswith("{"):
        return "read"
    word = re.match(r"[A-Za-z_]\w*", head)
    if not word:
        return None
    return "write" if word.group() == "mutation" else "read"


def _literal_text(node: ast.AST) -> str | None:
    """The literal text an expression opens with, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                break
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _literal_text(node.left)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return _literal_text(node.func.value)
    return None


# ── one module ───────────────────────────────────────────────────────────────


def module_name(rel: Path) -> str:
    """`foo` for `scripts/foo.py`, `harness.scenarios.x` for
    `scripts/harness/scenarios/x.py` (`rel` is below `scripts/`)."""
    return ".".join([*rel.parts[:-1], rel.stem])


class _Scope:
    """One function, method, lambda, class body or module: its own calls, the
    names its own code binds, and the strings it builds."""

    def __init__(self, name: str, node: ast.AST, parent: _Scope | None = None):
        self.name = name
        self.node = node
        self.parent = parent
        self.calls: list[ast.Call] = []
        self.assigned: dict[str, list[ast.AST]] = {}
        self.strings: list[ast.AST] = []

    @property
    def is_function(self) -> bool:
        return isinstance(self.node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring's constant: words about Linear, not a read."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def _scopes(tree: ast.Module) -> list[_Scope]:
    """Every scope in one walk, each holding its own code's calls and
    bindings. A nested function or class body is its own scope; its defaults,
    decorators and bases run in the enclosing one. A comprehension is not a
    scope: inlined since Python 3.12, its frame is the function's."""
    top = _Scope("<module>", tree)
    scopes = [top]
    docstrings = _docstrings(tree)

    def record(node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                scope.strings.append(node)
        elif isinstance(node, (ast.JoinedStr, ast.BinOp)):
            scope.strings.append(node)
        if isinstance(node, ast.Call):
            scope.calls.append(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope.assigned.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None:
            scope.assigned.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor,
                               ast.comprehension)) and isinstance(node.target, ast.Name):
            # Bound at run time: no longer one literal.
            scope.assigned.setdefault(node.target.id, []).append(node)
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            scope.assigned.setdefault(node.optional_vars.id, []).append(node)

    def enter(child: ast.AST, scope: _Scope) -> None:
        if not isinstance(child, _DEFS):
            record(child, scope)
            for grandchild in ast.iter_child_nodes(child):
                enter(grandchild, scope)
            return
        outer: list[ast.AST] = []
        if not isinstance(child, ast.Lambda):
            outer += child.decorator_list
        if isinstance(child, ast.ClassDef):
            outer += child.bases + [k.value for k in child.keywords]
            body = child.body
        else:
            outer += child.args.defaults
            outer += [d for d in child.args.kw_defaults if d is not None]
            body = [child.body] if isinstance(child, ast.Lambda) else child.body
        for part in outer:
            enter(part, scope)
        inner = _Scope("<lambda>" if isinstance(child, ast.Lambda) else child.name,
                       child, scope)
        scopes.append(inner)
        for stmt in body:
            enter(stmt, inner)

    for stmt in tree.body:
        enter(stmt, top)
    return scopes


def _named(func: ast.AST, names: frozenset | set) -> bool:
    if isinstance(func, ast.Name):
        return func.id in names
    if isinstance(func, ast.Attribute):
        return func.attr in names
    return False


def _first_arg(call: ast.Call, keyword: str) -> ast.AST | None:
    if call.args and not isinstance(call.args[0], ast.Starred):
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


class _Module:
    """One parsed file: its constants, its imports, its scopes."""

    def __init__(self, name: str, rel: str, tree: ast.Module):
        self.name = name
        self.rel = rel
        self.tree = tree
        self.scopes = _scopes(tree)
        # A function patched in as the transport (`linear_ops.gql = counted`,
        # a counting seam) takes its place for as long as it is there: it is
        # plumbing like `gql`, and the code calling `linear_ops.gql` is the site.
        self.stand_ins = {
            node.value.id for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
            and any(isinstance(t, ast.Attribute) and t.attr in TRANSPORTS
                    for t in node.targets)}
        self.constants: dict[str, ast.AST] = {}
        self.modules: dict[str, str] = {}  # alias -> module
        self.imported: dict[str, tuple[str, str]] = {}  # alias -> (module, name)
        seen: set[str] = set()
        for node in tree.body:
            targets: list = []
            if isinstance(node, ast.Assign):
                targets = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [(node.target, node.value)]
            for target, value in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in seen:
                    # Bound twice: which one a call reads is decided at run time.
                    self.constants.pop(target.id, None)
                    continue
                seen.add(target.id)
                self.constants[target.id] = value
        stack: list[ast.AST] = list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.modules[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    self.imported[alias.asname or alias.name] = (node.module, alias.name)
            # Statements only: an import is never inside an expression.
            for field in ("body", "orelse", "finalbody", "handlers", "cases"):
                stack.extend(x for x in getattr(node, field, ()) if isinstance(x, ast.AST))


class _Scanner:
    def __init__(self, modules: dict[str, _Module]):
        self.modules = modules

    # Resolving an expression to the text it opens with: a literal, a local
    # bound only to literals, a module constant, or another module's constant.
    def texts(self, node: ast.AST, mod: _Module, scope: _Scope | None,
              seen: frozenset = frozenset()) -> list[str] | None:
        literal = _literal_text(node)
        if literal is not None:
            return [literal]
        if isinstance(node, ast.IfExp):
            a = self.texts(node.body, mod, scope, seen)
            b = self.texts(node.orelse, mod, scope, seen)
            return None if a is None or b is None else a + b
        if isinstance(node, (ast.Tuple, ast.List, ast.Dict)):
            # A table of documents, read by a subscript: every entry counts.
            items = node.values if isinstance(node, ast.Dict) else node.elts
            out = []
            for item in items:
                got = self.texts(item, mod, scope, seen)
                if got is None:
                    return None
                out += got
            return out or None
        if isinstance(node, ast.Subscript):
            return self.texts(node.value, mod, scope, seen)
        if isinstance(node, ast.Name):
            key = (mod.name, node.id)
            if key in seen:
                return None
            seen = seen | {key}
            if scope is not None and scope.node is not mod.tree:
                bound = scope.assigned.get(node.id)
                if bound is not None:
                    out = []
                    for value in bound:
                        got = self.texts(value, mod, scope, seen)
                        if got is None:
                            return None
                        out += got
                    return out
                if self._is_parameter(scope.node, node.id):
                    return None
            if node.id in mod.constants:
                return self.texts(mod.constants[node.id], mod, None, seen)
            if node.id in mod.imported:
                other, name = mod.imported[node.id]
                target = self.modules.get(other)
                if target and name in target.constants:
                    return self.texts(target.constants[name], target, None, seen)
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            other = mod.modules.get(node.value.id)
            target = self.modules.get(other) if other else None
            if target and node.attr in target.constants:
                key = (target.name, node.attr)
                if key in seen:
                    return None
                return self.texts(target.constants[node.attr], target, None, seen | {key})
        return None

    @staticmethod
    def _is_parameter(func: ast.AST, name: str) -> bool:
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        a = func.args
        names = [x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
        names += [x.arg for x in (a.vararg, a.kwarg) if x is not None]
        return name in names

    @staticmethod
    def _default(func: ast.AST, name: str) -> ast.AST | None:
        a = func.args
        positional = [*a.posonlyargs, *a.args]
        for arg, default in zip(positional[len(positional) - len(a.defaults):], a.defaults):
            if arg.arg == name:
                return default
        for arg, default in zip(a.kwonlyargs, a.kw_defaults):
            if arg.arg == name:
                return default
        return None

    # Resolving a name to every expression it may hold, for the transport and
    # the host: the scope's own bindings, an enclosing function's, a
    # parameter's default, or the module's. Unlike `texts`, a name bound more
    # than once keeps every binding, so any one of them can make a read.
    def bindings(self, name: str, mod: _Module,
                 scope: _Scope | None) -> list[tuple[ast.AST, _Scope | None]]:
        first = True
        while scope is not None and scope.node is not mod.tree:
            if first or scope.is_function:
                if name in scope.assigned:
                    return [(value, scope) for value in scope.assigned[name]]
                if self._is_parameter(scope.node, name):
                    default = self._default(scope.node, name)
                    return [] if default is None else [(default, scope.parent)]
            first = False
            scope = scope.parent
        top = mod.scopes[0]
        return [(value, top) for value in top.assigned.get(name, [])]

    def _module_of(self, node: ast.AST, mod: _Module) -> _Module | None:
        """The scanned module a name is an import of."""
        if isinstance(node, ast.Name) and node.id in mod.modules:
            return self.modules.get(mod.modules[node.id])
        return None

    def _is_linear_ops(self, node: ast.AST, mod: _Module) -> bool:
        return isinstance(node, ast.Name) and mod.modules.get(node.id) == "linear_ops"

    def transport(self, node: ast.AST, mod: _Module, scope: _Scope | None,
                  seen: frozenset = frozenset()) -> bool:
        """Whether a callee is `gql` or `gql_paged`, however it is reached."""
        if isinstance(node, ast.Name):
            if node.id in TRANSPORTS or mod.imported.get(node.id, ("", ""))[1] in TRANSPORTS:
                return True
            key = (id(scope), node.id)
            if key in seen:
                return False
            return any(self.transport(value, mod, where, seen | {key})
                       for value, where in self.bindings(node.id, mod, scope))
        if isinstance(node, ast.Attribute):
            return node.attr in TRANSPORTS
        if isinstance(node, ast.BoolOp):
            return any(self.transport(v, mod, scope, seen) for v in node.values)
        if isinstance(node, ast.IfExp):
            return (self.transport(node.body, mod, scope, seen)
                    or self.transport(node.orelse, mod, scope, seen))
        if isinstance(node, ast.Subscript):
            # `vars(linear_ops)["gql"]`, `linear_ops.__dict__["gql_paged"]`
            return isinstance(node.slice, ast.Constant) and node.slice.value in TRANSPORTS
        if isinstance(node, ast.Call):
            if _named(node.func, {"partial"}) and node.args:
                return self.transport(node.args[0], mod, scope, seen)
            if _named(node.func, {"getattr"}) and len(node.args) >= 2:
                attr = node.args[1]
                if isinstance(attr, ast.Constant):
                    return attr.value in TRANSPORTS
                # A name the check cannot read, off `linear_ops`: fail closed.
                return self._is_linear_ops(node.args[0], mod)
        return False

    @staticmethod
    def document(call: ast.Call) -> ast.AST | None:
        """The document a transport call sends: a `partial`'s own when the
        callee is one that binds it, else the call's first argument."""
        func = call.func
        if isinstance(func, ast.Call) and _named(func.func, {"partial"}) and len(func.args) > 1:
            return func.args[1]
        return _first_arg(call, "query")

    def full_texts(self, node: ast.AST, mod: _Module, scope: _Scope | None,
                   seen: frozenset = frozenset(), follow: bool = True) -> list[str] | None:
        """Every whole text an expression may hold, a part the check cannot
        read standing as a hole; None when it reads no part at all. Where
        `texts` reads the opening of a document, this reads a URL whole, so a
        host built from pieces is still the host. With `follow` off it reads
        literals alone and never a name."""
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else None
        if not follow and isinstance(node, (ast.Name, ast.Attribute)):
            return None
        if isinstance(node, (ast.JoinedStr, ast.BinOp)):
            if isinstance(node, ast.BinOp):
                if not isinstance(node.op, (ast.Add, ast.Mod)):
                    return None
                parts = [node.left, node.right]
            else:
                parts = node.values
            out, known = [""], False
            for part in parts:
                if isinstance(part, ast.FormattedValue):
                    part = part.value
                got = self.full_texts(part, mod, scope, seen, follow)
                known = known or got is not None
                out = [a + b for a in out for b in (got or [_HOLE])][:_TEXTS_CAP]
            return out if known else None
        if isinstance(node, (ast.Tuple, ast.List, ast.IfExp)):
            items = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.elts
            out = []
            for item in items:
                out += self.full_texts(item, mod, scope, seen, follow) or []
            return out or None
        if isinstance(node, ast.Name):
            key = (mod.name, id(scope), node.id)
            if key in seen:
                return None
            if node.id in mod.imported:
                other, name = mod.imported[node.id]
                target = self.modules.get(other)
                if target is not None:
                    return self._top_texts(target, name, seen | {key})
            out = []
            for value, where in self.bindings(node.id, mod, scope):
                out += self.full_texts(value, mod, where, seen | {key}) or []
            return out or None
        if isinstance(node, ast.Attribute):
            target = self._module_of(node.value, mod)
            if target is not None:
                return self._top_texts(target, node.attr, seen)
        return None

    def _top_texts(self, target: _Module, name: str, seen: frozenset) -> list[str] | None:
        key = (target.name, None, name)
        if key in seen:
            return None
        out = []
        for value, where in self.bindings(name, target, None):
            out += self.full_texts(value, target, where, seen | {key}) or []
        return out or None

    def _is_linear_api(self, node: ast.AST, mod: _Module) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "API" and self._is_linear_ops(node.value, mod)
        if isinstance(node, ast.Name) and node.id == "API":
            return mod.name == "linear_ops" or mod.imported.get("API", ("",))[0] == "linear_ops"
        return False

    def names_linear(self, node: ast.AST, mod: _Module, scope: _Scope | None,
                     follow: bool = True) -> bool:
        """Whether an expression names Linear's API: `linear_ops.API`, or a
        text holding `api.linear.app` however it is built. With `follow` off,
        only a literal holding the host counts."""
        if isinstance(node, ast.Starred):
            node = node.value
        if follow and self._is_linear_api(node, mod):
            return True
        if isinstance(node, (ast.List, ast.Tuple)):
            return any(self.names_linear(e, mod, scope, follow) for e in node.elts)
        texts = self.full_texts(node, mod, scope, follow=follow)
        return bool(texts) and any(HOST in t for t in texts)

    def is_read(self, doc: ast.AST | None, mod: _Module, scope: _Scope) -> bool:
        """Fail closed: only a document every reading of which is a mutation
        is a write."""
        if doc is None:
            return True
        texts = self.texts(doc, mod, scope)
        if not texts:
            return True
        return any(operation(t) != "write" for t in texts)

    def aims_at_linear(self, url: ast.AST | None, mod: _Module, scope: _Scope) -> bool:
        if url is None:
            return False
        if isinstance(url, ast.Attribute) and url.attr == "API":
            return True
        return self.names_linear(url, mod, scope)

    def sites(self, mod: _Module) -> list[Site]:
        found: dict[str, Site] = {}
        for scope in mod.scopes:
            transports, requests, linear_requests, hosts = [], [], [], []
            calls_api_key = False
            for node in scope.calls:
                if self.transport(node.func, mod, scope):
                    if self.is_read(self.document(node), mod, scope):
                        transports.append(node.lineno)
                    continue
                if _named(node.func, REQUESTS):
                    requests.append(node.lineno)
                    if self.aims_at_linear(_first_arg(node, "url"), mod, scope):
                        linear_requests.append(node.lineno)
                elif _named(node.func, {"api_key"}):
                    calls_api_key = True
                arguments = [*node.args, *(kw.value for kw in node.keywords)]
                if _named(node.func, SENDERS) and any(
                        self.names_linear(a, mod, scope) for a in arguments):
                    hosts.append(node.lineno)
            if scope.is_function:
                hosts += [s.lineno for s in scope.strings
                          if self.names_linear(s, mod, scope, follow=False)]
            lines = transports + linear_requests + hosts
            if calls_api_key:
                lines += requests
            if not lines:
                continue
            name = f"{mod.name}.{scope.name}"
            if name in PLUMBING or (scope.is_function and scope.name in mod.stand_ins):
                continue
            line = min(lines)
            if name not in found or line < found[name].line:
                found[name] = Site(name, mod.rel, line)
        return list(found.values())


def _python_files(scripts: Path):
    for path in sorted(scripts.rglob("*.py")):
        if "__pycache__" in path.relative_to(scripts).parts:
            continue
        yield path


def scan_scripts(root: Path) -> list[Site]:
    scripts = root / SCRIPTS
    modules: dict[str, _Module] = {}
    if scripts.is_dir():
        for path in _python_files(scripts):
            rel = path.relative_to(scripts)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                raise ScanError(f"{SCRIPTS}/{rel.as_posix()} cannot be parsed: {exc}") from None
            name = module_name(rel)
            modules[name] = _Module(name, f"{SCRIPTS}/{rel.as_posix()}", tree)
    scanner = _Scanner(modules)
    out = []
    for mod in modules.values():
        out += scanner.sites(mod)
    return out


class ScanError(Exception):
    """A file the scan must read and cannot."""


# ── workflows ────────────────────────────────────────────────────────────────

_GQL_CALL = re.compile(r"\bgql(?:_paged)?\s*\(\s*(?:[rRbBuUfF]{0,2})(\"\"\"|'''|\"|')")
_GQL_ANY = re.compile(r"\bgql(?:_paged)?\s*\(")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def run_block_reads(text: str) -> bool:
    """Whether a step's text — its `run:` block, its `with:` and `env:`
    values — sends Linear a read itself (transport 6)."""
    if HOST in text or "linear_ops.API" in text:
        return True  # fail closed: the document is not read out of a shell line
    for call in _GQL_ANY.finditer(text):
        literal = _GQL_CALL.match(text, call.start())
        if not literal:
            return True
        start = literal.end()
        end = text.find(literal.group(1), start)
        if operation(text[start:end if end >= 0 else len(text)]) != "write":
            return True
    return False


def _line_of(lines: list[str], needle_lines: list[str], after: int) -> int:
    probes = [s.strip() for s in needle_lines if s.strip()]
    for i in range(after, len(lines)):
        if any(p and p in lines[i] for p in probes):
            return i + 1
    return after + 1


def _strings(value) -> list[str]:
    """Every string value under a YAML node, keys left out."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _hits(strings: list[str]) -> list[str]:
    return [ln for s in strings for ln in s.splitlines()
            if HOST in ln or "linear_ops.API" in ln or _GQL_ANY.search(ln)]


def scan_workflows(root: Path) -> list[Site]:
    """Transport 6. A step is read whole — its `run:` block and every
    `with:` and `env:` value, its name left out — so an
    `actions/github-script` step, or a `run:` reading its URL from `env:`, is
    a site. An `env:` naming Linear at the job or the workflow is a site of
    its own, `<job id>/env` or `env`: every step under it can read it, and the
    check cannot tell which does."""
    wf_dir = root / WORKFLOWS
    out: list[Site] = []
    if not wf_dir.is_dir():
        return out
    for path in sorted(wf_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if HOST not in text and "linear_ops.API" not in text and "gql" not in text:
            continue  # nothing transport 6 reads
        try:
            doc = yaml.load(text, Loader=_YAML_LOADER) or {}
        except yaml.YAMLError as exc:
            raise ScanError(f"{WORKFLOWS}/{path.name} cannot be parsed: {exc}") from None
        lines = text.splitlines()
        rel = f"{WORKFLOWS}/{path.name}"

        def site(name: str, strings: list[str], anchor: int) -> None:
            if run_block_reads("\n".join(strings)):
                out.append(Site(f"{path.name}#{name}", rel,
                                _line_of(lines, _hits(strings), anchor)))

        if isinstance(doc, dict):
            site("env", _strings(doc.get("env")), 0)
        jobs = doc.get("jobs") if isinstance(doc, dict) else None
        for job_id, job in (jobs or {}).items():
            if not isinstance(job, dict):
                continue
            anchor = next((i for i, ln in enumerate(lines)
                           if re.match(rf"\s*{re.escape(str(job_id))}:\s*$", ln)), 0)
            site(f"{job_id}/env", _strings(job.get("env")), anchor)
            for n, step in enumerate(job.get("steps") or [], 1):
                if not isinstance(step, dict):
                    continue
                step_name = step.get("name") or f"step {n}"
                at = anchor
                if step.get("name"):
                    named = re.compile(rf"\s*-?\s*name:\s*['\"]?{re.escape(str(step_name))}"
                                       r"['\"]?\s*$")
                    at = next((i for i in range(anchor, len(lines))
                               if named.match(lines[i])), anchor)
                site(f"{job_id}/{step_name}",
                     _strings({k: v for k, v in step.items() if k != "name"}), at)
    return out


def scan(root: Path) -> dict[str, Site]:
    """Every read site under `root`, by name."""
    sites: dict[str, Site] = {}
    for site in scan_scripts(root) + scan_workflows(root):
        sites.setdefault(site.name, site)
    return sites


# ── the docstring reason a row opens with ────────────────────────────────────


def stays_reason(root: Path, site: Site) -> str | None:
    """The `It stays on Linear (DRE-8898): …` paragraph a script site's
    function carries, whitespace collapsed, or None. An inner function with
    none of its own carries its enclosing function's: `linear_ops.landed` is
    the look `_card_look` returns."""
    if "#" in site.name or not site.path.endswith(".py"):
        return None
    tree = ast.parse((root / site.path).read_text(encoding="utf-8"))
    chain: list[ast.AST] = []
    node: ast.AST = tree
    while True:
        inner = next((child for child in ast.iter_child_nodes(node)
                      if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                            ast.ClassDef))
                      and child.lineno <= site.line <= (child.end_lineno or child.lineno)),
                     None)
        if inner is None:
            break
        if not isinstance(inner, ast.ClassDef):
            chain.append(inner)
        node = inner
    for func in reversed(chain):
        doc = ast.get_docstring(func) or ""
        at = doc.find(STAYS_MARK)
        if at >= 0:
            paragraph = re.split(r"\n\s*\n", doc[at:], maxsplit=1)[0]
            return " ".join(paragraph.split())
    return None


def calls_into(site: str) -> bool:
    """A site other code calls: its row ends with `NEW_CALLER_SENTENCE`."""
    return site.startswith("linear_ops.") or site == "hygiene.linear"


# ── the ledger ───────────────────────────────────────────────────────────────


def parse_ledger(text: str) -> tuple[dict[str, dict], list[str]]:
    """(rows by caller, problems). A problem is a finding."""
    problems: list[str] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, [f"{LEDGER} is not JSON: {exc}"]
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}, [f"{LEDGER} does not declare schema {SCHEMA}"]
    reads = data.get("reads")
    if not isinstance(reads, list):
        return {}, [f"{LEDGER} has no `reads` list"]
    rows: dict[str, dict] = {}
    for i, row in enumerate(reads):
        if not isinstance(row, dict) or set(row) != {"caller", "class", "reason", "card"}:
            problems.append(f"row {i} does not carry exactly caller, class, reason and card")
            continue
        caller = row["caller"]
        if not isinstance(caller, str) or not caller:
            problems.append(f"row {i} names no caller")
            continue
        if caller in rows:
            problems.append(f"{caller} is listed twice")
        if row["class"] not in CLASSES:
            problems.append(f"{caller} has class {row['class']!r}, not move or kept")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            problems.append(f"{caller} carries no reason")
        card = row["card"]
        if card is not None and not (isinstance(card, str) and _CARD.fullmatch(card)):
            problems.append(f"{caller} has card {card!r}, not DRE-<n> or null")
        if row["class"] == "kept" and card is None:
            problems.append(f"{caller} is kept and names no card")
        rows[caller] = row
    return rows, problems


@dataclass
class Verdict:
    found: int
    listed: list[str]
    kept: int
    pending: list[str]
    new: list[Site]
    gone: list[str]
    invalid: list[str]
    notes: list[str]

    @property
    def failed(self) -> bool:
        return bool(self.new or self.gone or self.invalid)

    def lines(self) -> list[str]:
        out = [
            f"{TAG} {self.found} read site(s) found, {len(self.listed)} listed "
            f"({self.kept} kept), {len(self.pending)} pending, {len(self.new)} new, "
            f"{len(self.gone)} gone"
        ]
        out += [f"{TAG} new {s.name} — {s.path}:{s.line}" for s in self.new]
        out += [f"{TAG} gone {name}" for name in self.gone]
        out += [f"{TAG} pending {name}" for name in self.pending]
        out += [f"{TAG} invalid {why}" for why in self.invalid]
        out += [f"{TAG} {line}" for line in self.notes]
        return out


def judge(head_sites: dict[str, Site], base_sites: dict[str, Site],
          head_text: str | None, base_text: str | None, base_sha: str = "") -> Verdict:
    """The four rules, on two scans and the two ledgers' text."""
    notes: list[str] = []
    invalid: list[str] = []
    if head_text is None:
        invalid.append(f"{LEDGER} is missing at the head")
        head_rows: dict[str, dict] = {}
    else:
        head_rows, problems = parse_ledger(head_text)
        invalid += problems
    if base_text is None:
        notes.append(f"no ledger at the merge base {base_sha[:7]} — judged against "
                     "the head's ledger")
        base_rows = head_rows
    else:
        base_rows, _ = parse_ledger(base_text)
    listed = sorted(n for n in head_sites if n in head_rows and n in base_rows)
    new = [head_sites[n] for n in sorted(head_sites) if n not in listed]
    gone = sorted(n for n in head_rows if n in base_sites and n not in head_sites)
    pending = sorted(n for n in head_rows if n not in base_sites and n not in head_sites)
    kept = sum(1 for n in listed if head_rows[n].get("class") == "kept")
    return Verdict(len(head_sites), listed, kept, pending, new, gone, invalid, notes)


# ── git ──────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str, binary: bool = False):
    done = subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=not binary)
    return done.stdout


def merge_base(root: Path, base: str | None) -> str:
    if base:
        return _git(root, "merge-base", base, "HEAD").strip()
    return _git(root, "rev-parse", "HEAD").strip()


def base_tree(root: Path, sha: str, into: Path) -> None:
    """Write the merge base's `scripts/` and workflows under `into`."""
    present = [p for p in (SCRIPTS, WORKFLOWS)
               if _git(root, "ls-tree", "--name-only", sha, "--", p).strip()]
    if not present:
        return
    archive = _git(root, "archive", "--format=tar", sha, "--", *present, binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")


def ledger_at(root: Path, sha: str) -> str | None:
    if not _git(root, "ls-tree", "--name-only", sha, "--", LEDGER).strip():
        return None
    return _git(root, "show", f"{sha}:{LEDGER}")


def check(root: Path, base: str | None) -> Verdict:
    sha = merge_base(root, base)
    head_sites = scan(root)
    with tempfile.TemporaryDirectory() as tmp:
        base_tree(root, sha, Path(tmp))
        base_sites = scan(Path(tmp))
    ledger = root / LEDGER
    head_text = ledger.read_text(encoding="utf-8") if ledger.is_file() else None
    return judge(head_sites, base_sites, head_text, ledger_at(root, sha), sha)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", help="the ref whose merge base with HEAD the "
                        "ledger and the base tree are read at (default: HEAD)")
    parser.add_argument("--root", default=".", help="the repository (default: .)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        verdict = check(root, args.base)
    except subprocess.CalledProcessError as exc:
        print(f"{TAG} cannot read git: {' '.join(exc.cmd[3:])} — "
              f"{(exc.stderr or '').strip()}", file=sys.stderr)
        return 2
    except ScanError as exc:
        print(f"{TAG} cannot scan: {exc}", file=sys.stderr)
        return 2
    for line in verdict.lines():
        print(line)
    return 1 if verdict.failed else 0


if __name__ == "__main__":
    sys.exit(main())
