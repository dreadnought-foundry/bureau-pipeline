"""Scenario `lane_contract` — the harness asserts the lane contract (DRE-2726).

The harness already proves a pipeline commit before the channel advances. It
never looked at lane movement, which is why a lane left over from an April epic
sat on the board for four months with one card ever and zero code references,
and why the console's vocabulary carried two words that are not Linear states at
all. Both are drift a conformance check finds immediately and no human ever
noticed. (The retired lanes are named in config/lane-contract.json, which is the
one place they may be — naming one here would be the very drift this checks for.)

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


def _extract_state_list(source: str, symbol: str) -> list | None:
    """The string members of `symbol`'s assigned collection, or None.

    Parsed, never regexed: the console's list is Python, and a regex over it
    would quietly disagree with the console's own reading of the same file.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if symbol not in names:
            continue
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return None
        return [
            e.value
            for e in value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    return None


def console_states(gh, spec: dict) -> list | None:
    """The console's own state vocabulary, or None when it cannot be read.

    None means UNKNOWN and the contract decides what unknown costs. Returning
    an empty list instead would read as "the console lists no states" and fail
    every lane for a reason that is not true.
    """
    if gh is None or not spec.get("repo"):
        return None
    module = f"{spec.get('module', '')}.py"
    symbol = spec.get("symbol", "")
    try:
        _, ref = gh.default_branch(spec["repo"])
        paths = [p for p in gh.list_tree(spec["repo"], ref) if os.path.basename(p) == module]
        for path in paths:
            source = gh.get_file(spec["repo"], path, ref)
            if not source:
                continue
            states = _extract_state_list(source, symbol)
            if states is not None:
                return states
    except Exception:
        return None
    return None


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
        states = console_states(getattr(ctx, "gh_console", None), spec)
        if states is None:
            ctx.log(
                f"[{self.name}] the console's state lists at "
                f"{spec.get('repo')}:{spec.get('module')}.{spec.get('symbol')} "
                "could not be read — reporting UNEVALUATED, never a pass"
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
