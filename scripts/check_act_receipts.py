#!/usr/bin/env python3
"""The completeness guard: no receipt is posted without composing it (DRE-2826).

`scripts/pipeline_act.py` can write a receipt. This file answers the question
that actually decays: **does every site that posts one use it?**

Without a guard this card is true on the day it merges and false the first time
somebody adds a recovery in a hurry — and a receipt with no trailer is exactly
the failure this epic is named after: it looks like nothing happened. From
`mx-8c2822`: *"A mechanism reaches a correct conclusion and nothing acts on it —
the failure signature is SILENCE, so it is found by running the system, never by
reading it."* A new unwrapped receipt is found here, at the diff, or it is found
by incident.

## What is checked

Every call in `scripts/*.py` and `.github/workflows/*.yml` that WRITES a
comment. The card named three of them (`gh pr comment`, `gh issue comment`,
`_post_pr_note`); the corpus has been widened twice since, both times because a
real posting pathway was invisible and the guard's own "0 problem(s)" was a
fact about the scanner rather than about the tree:

  * `linear_ops.cmd_comment` — a card comment never touches `gh` at all, it
    posts straight to the Linear GraphQL API, and eight of the ten
    `reconcile.py` sites this epic converts go that way.
  * `python3 …/linear_ops.py comment` — the SAME write from shell, which is how
    the workflow side posts: forty-odd calls across seven files.
  * `scripts/gate_note.py` and the `gh api --method POST …/comments` it posts
    through — the merge gate's post-exactly-once writer (DRE-2508), and the
    pathway its hand-off to a human takes.

The rule that produced each widening: a form nobody scans is a form nobody
converts, so the question to ask of this file is never "does it pass?" but
"which ways of writing a comment can it see?" — and the answer belongs in a
test (`tests/test_check_act_receipts.py`), because that is the only place it
cannot quietly regress.

Each site is in exactly one of three states:

  * **composed** — its body comes from `pipeline_act.receipt()` (Python) or from
    `pipeline_act.py receipt <act> --out <file>` (shell). Nothing to say.
  * **declared unconverted** — `config/pipeline-acts.json`'s `unconverted` block
    names it, with the reason it posts no trailer. Nothing to say, and the
    reason is on the record where anyone can count it.
  * **neither** — a finding, and CI goes red.

A declaration is checked as hard as the thing it excuses: it names a file and an
anchor (and optionally the workflow STEP, when a file posts the same command
from several steps), and it must match **exactly one** site in the corpus. A
declaration that matches nothing has rotted (the site it excused was moved or
reworded and the excuse outlived it); a declaration that matches two is excusing
a site nobody chose. Both fail, so the block cannot quietly become a place to
put things.

## Why the two directions are not symmetrical

`pipeline_act.problems()` binds the registry to the code: every declared act is
emitted, every emitted tag is declared. This file binds the code to the
registry: every emission goes through the writer. The registry check cannot see
a call site nobody declared — that is precisely the site this file is for.

CLI:

    python3 scripts/check_act_receipts.py          # the guard; exit 1 on findings
    python3 scripts/check_act_receipts.py list     # every site and its state
"""

from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_act  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# The corpus, and every call form that writes a comment. `scripts/` and
# `.github/workflows/` are where the pipeline writes from; a receipt posted
# from anywhere else does not exist.
SCRIPT_GLOB = "scripts/*.py"
WORKFLOW_GLOB = ".github/workflows/*.yml"

# The shell posters: `(the invocation, what its whole command must ALSO contain
# to be a write)`. Anchored so a listing is not read as a write — `gh pr
# comments` fails the word boundary, and `linear_ops.py add-label` is not
# `comment`.
#
#   * the two `gh` forms the card names.
#   * `linear_ops.py comment` — THE SAME `cmd_comment()` GraphQL write the
#     Python form makes, reached from shell instead. This is how the workflow
#     side posts: forty-odd calls across seven files, none of which a
#     `gh`-only scanner could see. The module docstring below says this guard
#     exists to catch "a recovery added in a hurry"; added in this form it was
#     invisible, and the "0 problem(s)" it reported was a fact about the
#     scanner (DRE-2826 review, round 1).
#   * `gate_note.py` — the merge gate's post-exactly-once writer (DRE-2508),
#     and the pathway its hand-off to a human takes. Required to look like an
#     INVOCATION, because `harness.yml` also names the file in a `paths:` list
#     and a guard that reads a trigger path as a receipt site is a guard
#     nobody keeps.
#   * `gh api --method POST …/comments` — what `gate_note.py` posts through,
#     and a third mechanism the literal `gh pr comment` match never sees. The
#     endpoint is the discriminator: `promote-channel.yml` POSTs a git ref.
_SHELL_POSTERS = (
    (re.compile(r"gh pr comment\b"), None),
    (re.compile(r"gh issue comment\b"), None),
    (re.compile(r"linear_ops\.py comment\b"), None),
    (re.compile(r"python3?\s+\S*gate_note\.py\b"), None),
    (re.compile(r"gh api --method POST\b"), re.compile(r"/comments\b")),
)

# The Python posters, and where each one's BODY sits in its argument list.
# `_post_pr_note` is reconcile's own seam over `gh pr comment`; `cmd_comment`
# is `linear_ops`' card-side write, and it is the pathway MOST receipts
# actually take — eight of the ten `reconcile.py` sites this epic converts post
# through it and never touch `gh` at all, because it goes straight to the
# Linear GraphQL API. A guard that reads only the `gh` forms would be blind
# exactly where the traffic is (DRE-2826 review). `create_comment` is
# `gate_note.py`'s own write, and it takes the body FIRST — reading every
# poster at index 1 would report a composed call as raw. All three take a body
# they cannot know the meaning of — that is what makes them seams — so their
# CALLERS are the sites and their own bodies are not scanned again.
_PY_POSTER_FUNCTIONS = {"_post_pr_note": 1, "cmd_comment": 1, "create_comment": 0}
_PY_POSTER_ARGV = (("gh", "pr", "comment"), ("gh", "issue", "comment"))

# `python3 …/pipeline_act.py receipt <act> … --out <path>` — the shell seam.
_SHELL_RECEIPT = re.compile(
    r"pipeline_act\.py\s+receipt\s+(?P<act>[a-z0-9-]+)\b"
)
_SHELL_OUT = re.compile(r"--out[=\s]+(?P<path>\S+)")
_BODY_FILE = re.compile(r"--body-file[=\s]+(?P<path>\S+)")

# `linear_ops.py comment <card> <body> --act=<name>` — the card-side seam. Not a
# poster in this corpus, but a typo'd act name here kills a live run, and it
# costs ten lines to read it at the diff instead.
_SHELL_ACT_FLAG = re.compile(r"--act[=\s]+(?P<act>[a-z0-9-]+)")

# …and the same flag built one line earlier into a shell variable, which is how
# a branch chooses whether to pass one at all (`ACT_FLAG="--act=…"` in
# agent-fix.yml). Anchored at an assignment so prose about the flag is not read
# as one — a line beginning `#` cannot match.
_SHELL_ACT_ASSIGNMENT = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=[\"']?--act[=\s]")
_SHELL_ACT_VARIABLE = re.compile(
    r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)=[\"']?--act[=\s](?P<act>[a-z0-9-]+)"
)
# `$ACT_FLAG` / `${ACT_FLAG}` — how the assembled flag reaches the call.
_SHELL_VARIABLE_USE = re.compile(r"\$\{?(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}?")


# How far back a declaration's anchor may reach. A shell receipt is routinely
# one arm of an if/elif whose only distinguishing text is the branch condition
# — four of agent-fix.yml's sites post the literal same command — so the anchor
# has to be able to name the branch. The window never crosses into a PREVIOUS
# site's own lines (see shell_sites), so two sites can never both match an
# anchor that belongs to one of them.
_LOOKBACK = 6

# The other coordinate, and the one a lookback window cannot supply. plan.yml
# runs the plan critic three times and posts its note and its round record the
# same way each time; the three call sites are byte-identical for ten lines
# back, and what tells them apart is the STEP they sit in ("First critic —
# round 1 decision" / "round 2" / "Second critic"). A declaration may name that
# step, and then the anchor only has to be unique WITHIN it.
_STEP_NAME = re.compile(r"^\s*-\s+name:\s*(?P<name>\S.*?)\s*$")


class Site:
    """One place a receipt is posted."""

    def __init__(self, path: str, line: int, text: str, composed_as: str | None,
                 context: str | None = None, step: str | None = None):
        self.path = path
        self.line = line
        self.text = text
        self.context = context if context is not None else text
        self.composed_as = composed_as  # the act name, when it composes
        self.step = step  # the workflow step it sits in, when it is one

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}"

    def __repr__(self) -> str:  # pragma: no cover — debugging only
        return f"<Site {self.where} composed={self.composed_as}>"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _paths(pattern: str, root: str | None = None) -> list:
    return sorted(glob.glob(os.path.join(root or ROOT, pattern)))


# --------------------------------------------------------------------------- #
# shell                                                                        #
# --------------------------------------------------------------------------- #


def _odd_quotes(line: str) -> bool:
    """Does this line leave a double quote open?

    Counted, not parsed. These are workflow shell lines, not arbitrary shell:
    the alternative is a shell grammar, and a guard nobody can read is a guard
    nobody keeps.
    """
    return len(re.findall(r'(?<!\\)"', line)) % 2 == 1


def _commented(line: str, index: int) -> bool:
    """Is the poster at `index` inside a comment rather than a command?

    Both files in the corpus talk ABOUT these calls — a YAML comment above a CI
    step, a shell comment explaining why a body is shaped the way it is. A
    guard that reads its own documentation as a receipt site cannot be run at
    all, and the first thing anyone would do is delete the sentence rather than
    the finding.

    A `#` only opens a comment when it starts a word and is not inside quotes.
    `echo "PR #$PR"; gh pr comment …` is a real site, and reading it as prose
    would be a way to hide one.
    """
    single = double = False
    previous = " "
    for character in line[:index]:
        if character == "'" and not double:
            single = not single
        elif character == '"' and not single:
            double = not double
        elif (
            character == "#"
            and not single and not double
            and (previous.isspace() or previous == "")
        ):
            return True
        previous = character
    return False


def _command_span(lines: list, start: int) -> tuple:
    """The whole logical command beginning at `start`.

    A receipt's body is routinely a multi-line double-quoted string with blank
    lines in it, so the span follows both backslash continuations and an open
    quote. Reading only the first line would make every long-form notice look
    like a bodyless call.
    """
    buf = [lines[start]]
    index = start
    open_quote = _odd_quotes(lines[start])
    continued = lines[start].rstrip().endswith("\\")
    while (open_quote or continued) and index + 1 < len(lines):
        index += 1
        buf.append(lines[index])
        if _odd_quotes(lines[index]):
            open_quote = not open_quote
        continued = lines[index].rstrip().endswith("\\")
    return "\n".join(buf), index


def _shell_flag_act(span: str, line: int, variables: dict) -> str | None:
    """The act a shell command composes through `--act=`, flag or variable.

    Literal first, then the assembled form: `ACT_FLAG="--act=…"` a few lines
    above and `$ACT_FLAG` on the call. The assignment must come BEFORE the
    call, the same order rule `--body-file` already obeys — a variable set
    below the post is not what the post passed.
    """
    match = _SHELL_ACT_FLAG.search(span)
    if match:
        return match.group("act")
    for use in _SHELL_VARIABLE_USE.finditer(span):
        assigned = variables.get(use.group("var"))
        if assigned and assigned[1] < line:
            return assigned[0]
    return None


def shell_sites(root: str | None = None) -> list:
    """Every comment-writing invocation in the workflows (see _SHELL_POSTERS)."""
    root = root or ROOT
    out: list = []
    for path in _paths(WORKFLOW_GLOB, root):
        relative = os.path.relpath(path, root)
        text = _read(path)
        lines = text.splitlines()
        # act name -> the file it writes its composed receipt to, and where.
        composed: dict = {}
        # shell variable -> the act its `--act=` flag names, and where.
        variables: dict = {}
        for number, line in enumerate(lines, start=1):
            assignment = _SHELL_ACT_VARIABLE.match(line)
            if assignment:
                variables[assignment.group("var")] = (
                    assignment.group("act"), number
                )
            match = _SHELL_RECEIPT.search(line)
            if not match or _commented(line, match.start()):
                continue
            span, _ = _command_span(lines, number - 1)
            out_path = _SHELL_OUT.search(span)
            if out_path:
                composed[out_path.group("path").strip("'\"")] = (
                    match.group("act"), number
                )
        previous_end = 0  # 1-based line of the last site's final line
        step = None
        for number, line in enumerate(lines, start=1):
            named = _STEP_NAME.match(line)
            if named:
                step = named.group("name")
            found = [
                requires for pattern, requires in _SHELL_POSTERS
                if (m := pattern.search(line)) and not _commented(line, m.start())
            ]
            if not found:
                continue
            span, end = _command_span(lines, number - 1)
            # A form that only writes at one endpoint is a site only there.
            if all(r is not None and not r.search(span) for r in found):
                continue
            body_file = _BODY_FILE.search(span)
            act = None
            if body_file:
                written = composed.get(body_file.group("path").strip("'\""))
                # Written BEFORE the post, or the post reads last run's file.
                if written and written[1] < number:
                    act = written[0]
            if act is None:
                act = _shell_flag_act(span, number, variables)
            start = max(previous_end + 1, number - _LOOKBACK)
            context = "\n".join(lines[start - 1:end + 1])
            out.append(Site(relative, number, span, act, context, step))
            previous_end = end + 1
    return out


def shell_act_flags(root: str | None = None) -> list:
    """Every `--act=<name>` in the workflows, as (file, line, name).

    Both where it is passed and where it is BUILT: a branch that chooses
    between an act and no act assembles the flag into a variable a few lines
    above the call, and a typo there dies at the write in a live run exactly as
    a typo on the call itself would.
    """
    root = root or ROOT
    out: list = []
    for path in _paths(WORKFLOW_GLOB, root):
        relative = os.path.relpath(path, root)
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if ("linear_ops.py" not in line
                    and "pipeline_act.py" not in line
                    and not _SHELL_ACT_ASSIGNMENT.match(line)):
                continue
            for match in _SHELL_ACT_FLAG.finditer(line):
                if _commented(line, match.start()):
                    continue
                out.append((relative, number, match.group("act")))
    return out


# --------------------------------------------------------------------------- #
# python                                                                       #
# --------------------------------------------------------------------------- #


def _receipt_act(node) -> str | None:
    """The act name, if `node` is a `pipeline_act.receipt(...)` call."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    named = (
        isinstance(func, ast.Attribute) and func.attr == "receipt"
        and isinstance(func.value, ast.Name) and func.value.id == "pipeline_act"
    ) or (isinstance(func, ast.Name) and func.id == "receipt")
    if not named or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) else "<computed>"


def _poster_call(func) -> int | None:
    """Where this call's body sits, if `func` is a Python poster — else None.

    `linear_ops.cmd_comment(...)`, `lops.cmd_comment(...)` and the bare
    `cmd_comment(...)` inside `linear_ops` itself are the same write, so the
    attribute's name is what decides — not the module alias in front of it,
    which every caller spells differently. The index matters because the
    posters do not agree on arity: `create_comment(body)` takes it first,
    `cmd_comment(identifier, body)` second.
    """
    if isinstance(func, ast.Name):
        return _PY_POSTER_FUNCTIONS.get(func.id)
    if isinstance(func, ast.Attribute):
        return _PY_POSTER_FUNCTIONS.get(func.attr)
    return None


def _flag_act(node, after: int) -> str | None:
    """The act named by an `--act=<name>` flag among a poster call's extra args.

    `cmd_comment(identifier, body, *flags)` takes the same flags the CLI seam
    does, so a caller can compose through `--act=` instead of wrapping the body
    — the Python mirror of `_SHELL_ACT_FLAG`.
    """
    for argument in node.args[after:]:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            match = _SHELL_ACT_FLAG.search(argument.value)
            if match:
                return match.group("act")
    return None


def _argv_body(node) -> tuple:
    """`(is_a_poster, the body expression)` for a `subprocess.run([...])`."""
    if not isinstance(node, ast.Call) or not node.args:
        return False, None
    argv = node.args[0]
    if not isinstance(argv, ast.List):
        return False, None
    head = tuple(
        e.value for e in argv.elts[:3]
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )
    if head not in _PY_POSTER_ARGV:
        return False, None
    for index, element in enumerate(argv.elts):
        if isinstance(element, ast.Constant) and element.value == "--body":
            return True, argv.elts[index + 1] if index + 1 < len(argv.elts) else None
    return True, None


def _bound_receipts(function) -> dict:
    """`name -> act` for locals in `function` assigned from `receipt()`."""
    out: dict = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            act = _receipt_act(node.value)
            if act:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = act
    return out


def python_sites(root: str | None = None) -> list:
    """Every poster call in `scripts/*.py`, with what composes its body."""
    root = root or ROOT
    out: list = []
    for path in _paths(SCRIPT_GLOB, root):
        relative = os.path.relpath(path, root)
        source = _read(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as e:  # a broken script is not this guard's finding
            print(f"WARNING: {relative} does not parse ({e}) — skipped", file=sys.stderr)
            continue
        for function in _functions(tree):
            # A poster's own body is not a site: it takes a body it cannot know
            # the meaning of, and its callers are what this guard reads.
            if function.name in _PY_POSTER_FUNCTIONS:
                continue
            bound = _bound_receipts(function)
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                body = None
                found = False
                flagged = None
                at = _poster_call(node.func)
                if at is not None:
                    found = True
                    body = node.args[at] if len(node.args) > at else None
                    flagged = _flag_act(node, at + 1)
                else:
                    found, body = _argv_body(node)
                if not found:
                    continue
                out.append(Site(
                    relative, node.lineno,
                    ast.get_source_segment(source, node) or "",
                    flagged or _composing_act(body, bound),
                ))
    return out


def _functions(tree) -> list:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _composing_act(body, bound: dict) -> str | None:
    if body is None:
        return None
    act = _receipt_act(body)
    if act:
        return act
    if isinstance(body, ast.Name):
        return bound.get(body.id)
    return None


# --------------------------------------------------------------------------- #
# the check                                                                    #
# --------------------------------------------------------------------------- #


def sites(root: str | None = None) -> list:
    return shell_sites(root) + python_sites(root)


def declarations(doc: dict | None = None) -> tuple:
    return tuple((doc or pipeline_act.load()).get("unconverted") or ())


def _matches(declaration: dict, site: Site) -> bool:
    """Does this declaration name this site — file, step (if given), anchor?

    `step` is optional and narrowing only: a declaration that gives one is
    saying "this anchor is unique inside that step", which is the only way to
    tell apart three byte-identical plan-critic postings. Omitting it keeps the
    old rule, so it can never make a declaration match MORE than it did.
    """
    if declaration.get("file") != site.path:
        return False
    step = declaration.get("step")
    if step and step != site.step:
        return False
    return (declaration.get("anchor") or "\0") in site.context


def problems(doc: dict | None = None, root: str | None = None) -> list:
    """Every receipt site that neither composes nor is declared, plus every
    declaration that no longer names one."""
    doc = doc if doc is not None else pipeline_act.load()
    found: list = []
    known = set(pipeline_act.acts(doc))
    all_sites = sites(root)
    declared = declarations(doc)

    for declaration in declared:
        hits = [s for s in all_sites if _matches(declaration, s)]
        anchor = declaration.get("anchor")
        if not (declaration.get("file") or "") or not (anchor or ""):
            found.append(
                "an `unconverted` entry names no file or no anchor — an excuse "
                "that points at nothing excuses everything"
            )
            continue
        if not (declaration.get("why") or "").strip():
            found.append(
                f"the `unconverted` entry for {anchor!r} gives no reason — the "
                "value of this block is that the debt is countable, and a "
                "reasonless row is just a mute button"
            )
        if len(hits) != 1:
            found.append(
                f"the `unconverted` entry {anchor!r} in "
                f"{declaration.get('file')} matches {len(hits)} receipt "
                "site(s), not one — a declaration that matches nothing has "
                "outlived the site it excused, and one that matches two is "
                "excusing a site nobody chose"
            )
        if hits and hits[0].composed_as:
            found.append(
                f"{hits[0].where} composes through pipeline_act.receipt() AND "
                f"is declared unconverted — remove the `unconverted` entry "
                f"{anchor!r}, it now hides a site that is fine"
            )

    for site in all_sites:
        if site.composed_as:
            if site.composed_as not in known and site.composed_as != "<computed>":
                found.append(
                    f"{site.where} composes a receipt for {site.composed_as!r}, "
                    "which the registry does not declare"
                )
            continue
        if any(_matches(d, site) for d in declared):
            continue
        found.append(
            f"{site.where} posts a comment whose body is not composed through "
            "pipeline_act.receipt(). Compose it — or, if it is deliberately "
            "not an act, declare it in config/pipeline-acts.json's "
            "`unconverted` block with the reason. A receipt with no trailer is "
            "a thing the pipeline did that nothing downstream can read."
        )

    for path, line, act in shell_act_flags(root):
        if act not in known:
            found.append(
                f"{path}:{line} names the act {act!r}, which the registry does "
                "not declare — the run would die at the write"
            )
    return found


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check")
    sub.add_parser("list")

    args = parser.parse_args(argv)
    command = args.command or "check"

    if command == "list":
        declared = declarations()
        print(json.dumps([
            {
                "site": s.where,
                "step": s.step,
                "composes": s.composed_as,
                "declared_unconverted": any(_matches(d, s) for d in declared),
            }
            for s in sites()
        ], indent=2, ensure_ascii=False))
        return 0

    found = problems()
    for problem in found:
        print(f"  [FAIL] {problem}")
    total = sites()
    composed = [s for s in total if s.composed_as]
    print(
        f"{len(total)} receipt site(s): {len(composed)} composed through "
        f"pipeline_act.receipt(), {len(declarations())} declared unconverted, "
        f"{len(found)} problem(s)"
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
