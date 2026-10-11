#!/usr/bin/env python3
"""Every Green Light row is a declared arrival (DRE-5282, epic DRE-5268).

Green Light is the CEO's "needs you" queue, and the epic this belongs to fixes
what may sit in it: a plan BOTH critics passed, the planner's business question
with a recommendation, a build's escalation to a person, (while the CEO's
acceptance of kind (c) stands) an approved epic queued under the cap, or the
sweep's question about an epic grown past the size he approved (DRE-6414). A row
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
   at its UNIT — `<file>#<enclosing def>` for a script, `<file>#<step>` for a
   workflow, the grammar `where` and `callers` use — and a unit no record
   names fails BY LOCATION. A workflow write's step is the parsed step whose
   lines hold it, whatever key the step opens with, named as
   `lane_callers` names it: its `name`, else its `id`, else `<job>[<index>]`.
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
   * `passed-plan` in a workflow: the `if:` of the step each write sits in
     carries BOTH `steps.post1.outputs.result == 'PASS'` (the second critic's
     decision word) and `steps.post1.outputs.pre_passed == 'true'` (the first
     critic's last word was a proceed), each a condition of its own joined by
     `&&` at the top level — an `||` there, or a gate negated or inside an
     `||`, lets a plan through on one critic. `action == 'proceed'` is not
     that gate — it covers a critic that never decided. A `passed-plan`
     function (`planning_route.py#_cmd_exit`) has no `if:` of its own: it is
     reconciled by its callers alone (rule 6), so a declared caller whose
     `if:` is weakened is not seen here.
   * `queued-epic`: the step's shell adds the `epic-queued` label BEFORE it
     writes the lane, read as the shell reads it — a continued line joined, a
     comment dropped.
   * `question`: the unit is `planning_escalation.py#escalate` and no other.
   * `agent-escalation`: `agent-task.yml#Report result to Linear`, whose shell
     posts the 🙋 escalation comment before the write — an argument opening
     with the receipt, then a `linear_ops.py comment` on a later line, both
     read as the shell reads them, so a receipt left in a comment is not one —
     or `proof-task.yml#Report proof result to Linear`, the proof run's park on
     a press only the CEO can make (DRE-5925), whose shell posts its own 🙋
     question the same way AND runs `linear_ops.py proof-waiting` first, the
     hold that keeps the dispatcher off the card — or `code_owner_hold.py#park`,
     or the sweep's park on a spent review budget,
     `reconcile.py#hand_review_nudge_to_person` (DRE-6181), or the sweep's ask
     for a build agent's blocker it cannot act on, `blocker_ask.py#resolve`
     (DRE-6459), reached from the resolver alone and reconciled by rule 6.
   * `epic-growth`: the unit is `reconcile.py#ask_epic_growth_question` and
     no other (DRE-6414) — the sweep's question about an epic grown past the
     size the CEO approved, created in the lane on a card of its own. Its
     caller, `reconcile.py#report_epic_growth`, is reconciled by rule 6.
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
import functools
import os
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
#: the contract clause's: `epic_cap.QUEUED_LABEL` names the same string today,
#: and a rename there makes this rule fail closed — a queued-epic write whose
#: label no longer matches goes red — rather than pass a row unlabeled.
QUEUED_LABEL = "epic-queued"

#: The workflow units an `agent-escalation` row may be written from, each with
#: the receipt its shell must post before the write: DRE-1655's
#: escalate-by-exception park, and the proof run's park on a press only the
#: CEO can make (DRE-5925). The step name is the unit `where` declares. The
#: proof run's receipt is a literal: no Python module posts it, the workflow
#: does, and a reworded receipt there fails this rule closed.
AGENT_ESCALATION_STEP = "agent-task.yml#Report result to Linear"
PROOF_PARK_STEP = "proof-task.yml#Report proof result to Linear"
AGENT_ESCALATION_STEPS = {
    AGENT_ESCALATION_STEP: planner_score.ESCALATION_RECEIPT_PREFIX,
    PROOF_PARK_STEP: "🙋 The proof run met a press only the CEO can make",
}

#: What the proof run's park must run before it writes the lane, besides its
#: question: the hold the dispatcher reads (`linear_ops.cmd_proof_waiting`).
#: Without it a parked proof card is one a sweep could chase.
PROOF_HOLD_COMMAND = "proof-waiting"

def _unit_of_function(fn) -> str:
    """`<file>#<function>` for a module-level function, named off the module."""
    module = sys.modules[fn.__module__]
    return f"{os.path.basename(module.__file__)}#{fn.__name__}"


#: The planner's question site — the only unit a `question` row comes from.
QUESTION_SITE = _unit_of_function(planning_escalation.escalate)

#: The merge gate's code-owner park — a python `agent-escalation` site.
CODE_OWNER_SITE = _unit_of_function(code_owner_hold.park)

#: The sweep's park on a spent review budget (DRE-6181) — the other python
#: `agent-escalation` site. A literal: importing the sweep here would read its
#: environment at import, and a renamed function fails rule 2 by name.
REVIEW_CAP_SITE = "reconcile.py#hand_review_nudge_to_person"

#: The sweep's ask for a build agent's blocker it cannot act on (DRE-6459) — a
#: python `agent-escalation` site, reached through the resolver. A literal, for
#: the reason REVIEW_CAP_SITE is one: the module imports the pipeline's writers.
BLOCKER_ASK_SITE = "blocker_ask.py#resolve"

#: The sweep's question about an epic grown past the size the CEO approved
#: (DRE-6414) — the only unit an `epic-growth` row comes from. A literal, for
#: the reason REVIEW_CAP_SITE is one.
EPIC_GROWTH_SITE = "reconcile.py#ask_epic_growth_question"


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


@functools.lru_cache(maxsize=16)
def _step_spans(text: str) -> tuple:
    """(first line, last line, name, step) for every step of every job, lines
    1-based in `text`. Read off the parsed nodes, so a step is found by where
    it sits, never by the key it happens to open with. The name is
    `lane_callers._steps`'s: `name`, else `id`, else `<job>[<index>]`."""
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(node, yaml.MappingNode) or not isinstance(doc, dict):
        return ()
    jobs_node = next((v for k, v in node.value if k.value == "jobs"), None)
    jobs = doc.get("jobs")
    if not isinstance(jobs_node, yaml.MappingNode) or not isinstance(jobs, dict):
        return ()
    out = []
    for (_, job_node), (job_name, job) in zip(jobs_node.value, jobs.items()):
        if not isinstance(job_node, yaml.MappingNode) or not isinstance(job, dict):
            continue
        steps_node = next((v for k, v in job_node.value if k.value == "steps"), None)
        steps = job.get("steps")
        if not isinstance(steps_node, yaml.SequenceNode) or not isinstance(steps, list):
            continue
        for index, (step_node, step) in enumerate(zip(steps_node.value, steps)):
            if not isinstance(step, dict):
                continue
            name = step.get("name") or step.get("id") or f"{job_name}[{index}]"
            out.append((step_node.start_mark.line + 1, step_node.end_mark.line + 1,
                        str(name), step))
    return tuple(out)


def _step_at(path: str, root: str, line: int) -> tuple:
    """(first line, name, step) of the step whose lines hold 1-based `line` in
    the workflow as `writes()` read it, or (None, None, None)."""
    try:
        spans = _step_spans(step_shell.workflow_source(os.path.abspath(path), root))
    except OSError:
        return None, None, None
    # A block step's end mark is where the next one starts: the latest start
    # at or above the line is the step that holds it.
    held = [s for s in spans if s[0] <= line <= s[1]]
    if not held:
        return None, None, None
    first, _, name, step = max(held, key=lambda s: s[0])
    return first, name, step


def unit_of(write, root: str = ROOT) -> str:
    """Where a discovered write sits, in the contract's grammar."""
    file, _, line = write.where.rpartition(":")
    path = os.path.join(root, file)
    base = os.path.basename(file)
    if write.how == "workflow":
        try:
            _, name, _ = _step_at(path, root, int(line))
        except ValueError:
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


def _step_of(root: str, write):
    """The parsed step a workflow write sits in, or None."""
    file, _, line = write.where.rpartition(":")
    return _step_at(os.path.join(root, file), root, int(line))[2]


def _before_write(root: str, write) -> str:
    """The text of the write's own step from its first line up to the write
    line, as `writes()` read it — what the shell has done before the lane
    moves."""
    file, _, line = write.where.rpartition(":")
    path = os.path.join(root, file)
    first, _, _ = _step_at(path, root, int(line))
    if first is None:
        return ""
    return "\n".join(_workflow_lines(path, root)[first - 1:int(line) - 1])


def _wrapped(expr: str) -> bool:
    """Whether the whole of `expr` is one parenthesized group."""
    if not expr.startswith("("):
        return False
    depth, quote = 0, False
    for i, c in enumerate(expr):
        if quote:
            quote = c != "'"
        elif c == "'":
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i == len(expr) - 1
    return False


def _conjuncts(expr: str):
    """The terms `expr` joins with `&&` at its top level, whitespace collapsed
    and a redundantly parenthesized `&&` group opened; None when an `||` sits
    at the top level, since then no term is required. A negated term, or a
    group holding an `||`, is kept whole, so it never equals a bare gate."""
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2].strip()
    parts, depth, quote, start, i = [], 0, False, 0, 0
    while i < len(expr):
        c = expr[i]
        if quote:
            quote = c != "'"  # `''` closes and reopens: the same string
        elif c == "'":
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and expr.startswith("||", i):
            return None
        elif depth == 0 and expr.startswith("&&", i):
            parts.append(expr[start:i])
            start = i + 2
            i += 1
        i += 1
    parts.append(expr[start:])
    out = []
    for part in (p.strip() for p in parts):
        inner = _conjuncts(part[1:-1]) if _wrapped(part) else None
        out += inner if inner is not None else [" ".join(part.split())]
    return out


def _shell_lines(text: str) -> list:
    """`text` read as the shell reads it: a whole-line comment dropped, a
    continued line joined, each line's arguments split with a trailing
    comment dropped."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return [lane_callers._shell_args(line)
            for line in lane_callers._CONTINUATION.sub(" ", "\n".join(lines)).splitlines()]


def _labels_queued(text: str) -> bool:
    """Whether `text`, read as the shell reads it, adds the queued label."""
    for args in _shell_lines(text):
        if "add-label" in args and QUEUED_LABEL in args[args.index("add-label") + 1:]:
            return True
    return False


def _module_constant(root: str, module: str, name: str):
    """The literal `name` is assigned at the top of `scripts/<module>.py`, or
    None — read, never imported, so the value checked is the one written."""
    try:
        with open(os.path.join(root, "scripts", f"{module}.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value.value
    return None


def _python_labels_queued(root: str, write) -> bool:
    """Whether the function a Python write sits in calls `add_label` with the
    queued label on an earlier line (DRE-6618) — the label spelled as a
    literal, or as `<module>.QUEUED_LABEL` whose module assigns this rule's
    string. Anything else fails closed, as a renamed label does."""
    file, _, line = write.where.rpartition(":")
    try:
        with open(os.path.join(root, file), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return False
    at = int(line)
    holder = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.lineno <= at <= (node.end_lineno or node.lineno):
            if holder is None or node.lineno >= holder.lineno:
                holder = node
    if holder is None:
        return False

    def is_label(arg) -> bool:
        if isinstance(arg, ast.Constant):
            return arg.value == QUEUED_LABEL
        if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                and arg.attr == "QUEUED_LABEL":
            return _module_constant(root, arg.value.id, arg.attr) == QUEUED_LABEL
        return False

    for node in ast.walk(holder):
        if not isinstance(node, ast.Call) or node.lineno >= at:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "add_label" and any(is_label(a) for a in node.args):
            return True
    return False


def _runs_linear_ops(text: str, command: str) -> bool:
    """Whether `text`, read as the shell reads it, runs `linear_ops.py
    <command>` — the command run, not merely named or left in a comment."""
    return any(
        os.path.basename(a) == "linear_ops.py" and args[i + 1:i + 2] == [command]
        for args in _shell_lines(text) for i, a in enumerate(args))


def _posts_receipt(text: str, receipt: str) -> bool:
    """Whether `text`, read as the shell reads it, writes an argument opening
    with `receipt` and then, on a later line, runs `linear_ops.py comment` —
    the receipt posted, not merely echoed or left in a comment."""
    written = False
    for args in _shell_lines(text):
        if written and any(
                os.path.basename(a) == "linear_ops.py" and args[i + 1:i + 2] == ["comment"]
                for i, a in enumerate(args)):
            return True
        written = written or any(a.startswith(receipt) for a in args)
    return False


# --------------------------------------------------------------------------- #
# the rules                                                                    #
# --------------------------------------------------------------------------- #


def _gate_problems(record: dict, writes_here: list, root: str, lane: str) -> list:
    kind, where = record.get("kind"), record.get("where", "")
    out: list[str] = []
    if kind == "passed-plan" and not _is_function(where):
        # Each write's own step is read. No write there: rule 2 names the
        # site, once.
        for write in writes_here:
            terms = _conjuncts(str((_step_of(root, write) or {}).get("if") or ""))
            missing = [g for g in PASSED_PLAN_GATES if g not in (terms or ())]
            if missing:
                out.append(
                    f"{where} ({write.where}) is a passed-plan arrival, and its "
                    f"step's `if:` does not require "
                    f"{' and '.join(f'`{g}`' for g in missing)} as a condition of "
                    f"its own joined by `&&` — a plan reaches {lane} only on BOTH "
                    "critics' pass; an `||` or a negation lets one through on "
                    "either, and `action == 'proceed'` covers a critic that never "
                    "decided"
                )
    elif kind == "queued-epic":
        for write in writes_here:
            labeled = (_labels_queued(_before_write(root, write))
                       if write.how == "workflow"
                       else _python_labels_queued(root, write))
            if not labeled:
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
    elif kind == "epic-growth" and where != EPIC_GROWTH_SITE:
        out.append(
            f"{where} is declared an 'epic-growth' arrival, and the only "
            f"epic-growth site is {EPIC_GROWTH_SITE} — the question about an "
            f"epic grown past its green light reaches {lane} through the sweep's "
            "one asking function and nowhere else; a caller of it belongs on "
            "that record's `callers`"
        )
    elif kind == "agent-escalation":
        if where in AGENT_ESCALATION_STEPS:
            receipt = AGENT_ESCALATION_STEPS[where]
            for write in writes_here:
                before = _before_write(root, write)
                if not _posts_receipt(before, receipt):
                    out.append(
                        f"{where} ({write.where}) is an agent-escalation arrival, "
                        f"and its step does not post the escalation comment "
                        f"(`{receipt}`, then `linear_ops.py comment`) before it "
                        f"writes {lane} — a receipt left in a comment, or written "
                        "and never posted, leaves a row with no question on it"
                    )
                if where == PROOF_PARK_STEP and not _runs_linear_ops(
                        before, PROOF_HOLD_COMMAND):
                    out.append(
                        f"{where} ({write.where}) is an agent-escalation arrival, "
                        f"and its step does not run `linear_ops.py "
                        f"{PROOF_HOLD_COMMAND}` before it writes {lane} — a "
                        "parked proof card with no hold on it is one the "
                        "dispatcher could chase"
                    )
        elif where not in (CODE_OWNER_SITE, REVIEW_CAP_SITE, BLOCKER_ASK_SITE):
            out.append(
                f"{where} is declared an 'agent-escalation' arrival, and the "
                f"only agent-escalation sites are "
                f"{', '.join(AGENT_ESCALATION_STEPS)}, {CODE_OWNER_SITE}, "
                f"{REVIEW_CAP_SITE} and {BLOCKER_ASK_SITE}"
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
