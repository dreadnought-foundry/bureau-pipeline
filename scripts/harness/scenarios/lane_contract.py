"""Scenario `lane_contract` — the harness asserts the lane contract (DRE-2726).

The harness already proves a pipeline commit before the channel advances. It
never looked at lane movement, which is why a lane left over from an April epic
sat on the board for four months with one card ever and zero code references,
and why the console's vocabulary carried two words that are not Linear states at
all. Both are drift a conformance check finds immediately and no human ever
noticed. (Those retired lanes are named nowhere in this repo's scripts or
workflows any more — DRE-2818 deleted the last entries carrying them once Linear
archived the states — and naming one here would be the very drift this checks
for.)

This scenario reads three things and runs `lane_contract.check()` over them:

* **the live Linear board** — every workflow state and how many cards sit in it;
* **the console's own state lists** — located by module name in the console's
  repository, because a remembered path is the enumeration of a derivable set
  this repo keeps being bitten by;
* **the checkout's own lane vocabulary** — every lane name the pipeline's
  scripts and workflows actually name, read out of the AST and the workflow
  bodies rather than out of memory.

Three properties this scenario holds to, all pinned by
`tests/test_harness_lane_contract.py`:

1. **It writes nothing.** The harness's standing promise is zero Linear writes
   and zero permanent sandbox state; a conformance check must not be the thing
   that breaks it. The only Linear traffic is one GraphQL `query`.
2. **Unknown is never a pass.** A console it cannot reach is reported
   UNEVALUATED — never treated as agreement, and never treated as an empty list
   (which would fail every lane for the wrong reason). The contract says from
   which phase that unevaluated state becomes a hard failure.
3. **A report that asserted nothing is a failure.** A green run that checked
   nothing is precisely the failure mode this card exists to prevent.

It is in the DEFAULT sweep, unlike the DRE-2490 agent scenarios: it costs two
API reads rather than a build-agent run, and the whole point is that every trunk
commit is checked before the channel advances.
"""

from __future__ import annotations

import ast
import os

import lane_contract as contract
from harness.framework import Scenario, ScenarioFailure


def board_states(gql) -> dict:
    """lane name → number of cards in it, from Linear. Read-only: one query."""
    query = """
    query {
      workflowStates(first: 100) {
        nodes { name issues(first: 250) { nodes { id } } }
      }
    }
    """
    data = gql(query)
    return {
        node["name"]: len(node["issues"]["nodes"])
        for node in data["workflowStates"]["nodes"]
    }


class _Unresolvable(Exception):
    """One piece of the console's list the reader cannot resolve; the message
    names it, because the harness log has to say what it could not read."""


def _snippet(node) -> str:
    text = ast.unparse(node)
    return text if len(text) <= 80 else text[:77] + "..."


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _module_scope_nodes(node):
    """`node` and every node under it that runs in the module's own scope.
    A def or class is yielded — it binds its name here — but never entered:
    the names in its body are not the module's."""
    yield node
    if isinstance(node, _NESTED_SCOPES):
        return
    for child in ast.iter_child_nodes(node):
        yield from _module_scope_nodes(child)


def _module_bindings(tree) -> dict:
    """name → every module-level binding of it.

    A binding is the assigned expression when the statement is a plain
    `NAME = value` (or annotated) at the top of the module, and otherwise a
    sentence saying what bound it — an import, a def, an unpacking, a loop —
    which the resolver reports rather than follows.
    """
    bindings: dict = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
            continue  # `NAME: type` declares, it binds nothing
        direct = {}
        if isinstance(stmt, ast.Assign):
            direct = {id(t): stmt.value for t in stmt.targets if isinstance(t, ast.Name)}
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            direct = {id(stmt.target): stmt.value}
        for node in _module_scope_nodes(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bindings.setdefault(node.id, []).append(
                    direct.get(id(node))
                    or f"bound by `{_snippet(stmt)}` on line {stmt.lineno}"
                )
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    bindings.setdefault(name, []).append(
                        f"imported from another module (`{_snippet(node)}`)"
                    )
            elif isinstance(node, _NESTED_SCOPES) and not isinstance(node, ast.Lambda):
                bindings.setdefault(node.name, []).append(
                    f"defined by a `{type(node).__name__}` on line {node.lineno}"
                )
    return bindings


def _resolve(expr, owner: str, bindings: dict, resolving: tuple) -> list:
    if isinstance(expr, (ast.Tuple, ast.List)):
        states = []
        for element in expr.elts:
            if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                raise _Unresolvable(
                    f"{owner} holds `{_snippet(element)}`, which is not a string"
                )
            states.append(element.value)
        return states
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _resolve(expr.left, owner, bindings, resolving) + _resolve(
            expr.right, owner, bindings, resolving
        )
    if isinstance(expr, ast.Name):
        return _resolve_name(expr.id, bindings, resolving)
    raise _Unresolvable(
        f"{owner} uses `{_snippet(expr)}`, which is not a list or tuple "
        "literal, a name, or a `+` join of them"
    )


def _resolve_name(name: str, bindings: dict, resolving: tuple) -> list:
    if name in resolving:
        raise _Unresolvable(
            f"{name} is part of a cycle ({' → '.join(resolving + (name,))})"
        )
    found = bindings.get(name, [])
    if not found:
        raise _Unresolvable(f"{name} has no module-level assignment")
    if len(found) > 1:
        # Which binding a `+` saw depends on statement order; picking one
        # could hand back a list the console never builds.
        raise _Unresolvable(f"{name} is bound {len(found)} times at module level")
    if isinstance(found[0], str):
        raise _Unresolvable(f"{name} is {found[0]}")
    return _resolve(found[0], name, bindings, resolving + (name,))


def resolve_state_list(source: str, symbol: str) -> tuple:
    """`(states, "")` with `symbol`'s list fully resolved, or `(None, reason)`.

    The console writes its list as names joined with `+` (DRE-7050), so each
    name is resolved the same way, recursively, from its module-level
    assignment: a tuple or list of strings, or more names and literals joined
    with `+`. Anything else — an import, a call, a comprehension, a non-string
    element, a cycle — makes the whole read None. A partly resolved list is
    never returned: it would fail lanes for a reason that is not true, the
    same harm as an empty one.

    Parsed, never regexed: the console's list is Python, and a regex over it
    would quietly disagree with the console's own reading of the same file.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, f"the module does not parse ({exc.msg}, line {exc.lineno})"
    try:
        return _resolve_name(symbol, _module_bindings(tree), ()), ""
    except _Unresolvable as exc:
        return None, str(exc)


def read_console(gh, spec: dict) -> tuple:
    """`(states, "")` from the console's repository, or `(None, reason)` —
    the reason is what the harness log says it could not read."""
    if gh is None:
        return None, "there is no client for the console's repository"
    if not spec.get("repo"):
        return None, "the contract names no console repository"
    module = f"{spec.get('module', '')}.py"
    symbol = spec.get("symbol", "")
    reason = f"no {module} in the repository's tree"
    try:
        _, ref = gh.default_branch(spec["repo"])
        paths = [p for p in gh.list_tree(spec["repo"], ref) if os.path.basename(p) == module]
        for path in paths:
            source = gh.get_file(spec["repo"], path, ref)
            if not source:
                reason = f"{path} came back empty"
                continue
            states, why = resolve_state_list(source, symbol)
            if states is not None:
                return states, ""
            reason = f"{path}: {why}"
    except Exception as exc:
        # The type only: an exception's text can carry a request URL, and
        # this line lands in a public run log.
        return None, f"the read raised {type(exc).__name__}"
    return None, reason


def console_states(gh, spec: dict) -> list | None:
    """The console's own state vocabulary, or None when it cannot be read.

    None means UNKNOWN and the contract decides what unknown costs. Returning
    an empty list instead would read as "the console lists no states" and fail
    every lane for a reason that is not true.
    """
    return read_console(gh, spec)[0]


class LaneContractConformance(Scenario):
    name = "lane_contract"
    requires_agent = False

    def setup(self, ctx) -> None:
        """Read the board. A read failure is a scenario failure — the board is
        the whole subject, and 'could not look' is not 'nothing is wrong'."""
        import linear_ops

        ctx.state["board"] = board_states(linear_ops.gql)
        ctx.log(
            f"[{self.name}] Linear carries {len(ctx.state['board'])} state(s): "
            + ", ".join(f"{k} ({v})" for k, v in sorted(ctx.state["board"].items()))
        )

    def exercise(self, ctx) -> None:
        spec = contract.console()
        states, reason = read_console(getattr(ctx, "gh_console", None), spec)
        if states is None:
            ctx.log(
                f"[{self.name}] the console's state lists at "
                f"{spec.get('repo')}:{spec.get('module')}.{spec.get('symbol')} "
                f"could not be read ({reason}) — reporting UNEVALUATED, never a pass"
            )
        else:
            ctx.log(f"[{self.name}] the console names {len(states)} state(s)")
        vocabulary = contract.pipeline_vocabulary()
        ctx.log(f"[{self.name}] the pipeline names {sorted(vocabulary)}")
        ctx.state["report"] = contract.check(
            board=ctx.state.get("board"),
            console=states,
            vocabulary=vocabulary,
        )

    def verify(self, ctx) -> None:
        report = ctx.state["report"]
        ctx.log(f"[{self.name}] {report.text()}")
        if not report.asserted():
            raise ScenarioFailure(
                "the lane contract asserted NOTHING — every clause was skipped "
                "or unevaluated, which is a green run that checked nothing"
            )
        if not report.ok:
            raise ScenarioFailure(
                "the live system does not match the lane contract:\n"
                + "\n".join(f"  - {f.clause_id}: {f.detail}" for f in report.failures())
            )

    def cleanup(self, ctx) -> None:
        """Nothing to clean: this scenario creates no branch, no PR, no file,
        no comment and no card. Kept explicit so the promise is visible."""
        ctx.log(f"[{self.name}] read-only scenario — nothing to clean up")


SCENARIO = LaneContractConformance()
