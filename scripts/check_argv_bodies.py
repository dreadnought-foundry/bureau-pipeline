#!/usr/bin/env python3
"""No workflow hands a file's contents or a multi-line step output to a
command argument or an env value (DRE-8820).

Linux refuses any single argument or environment string over 131,072 bytes
(`MAX_ARG_STRLEN`) with E2BIG, and bash reports that as exit code 126. The
wall has been met four times — DRE-3328 and DRE-3330 (a prompt in argv),
DRE-6683 (a 207 KB check result handed to a step as one environment value)
and DRE-8698 (a mechanical note in argv, exit 126) — and each was fixed only
where it was found. `tests/test_linear_body_never_in_argv.py` (DRE-8758)
guards one shape, a `linear_ops.py` body read with `$(cat …)`. This is the
general check, so the next site is refused in CI before it ships.

It discovers every workflow under `.github/workflows/` by glob — no list of
files, steps or sites is carried — reads every `run:` block and every `env:`
map (workflow, job and step level), and reports each SITE:

1. A file's contents in a command argument. A command bash `execve`s —
   anything that is not a builtin or a keyword (`BUILTINS`, `KEYWORDS`):
   `echo`, `printf`, `test`, `[`, `[[`, `case`, `read`, `:`, `local`,
   `export`, an assignment and the like never leave the process — whose
   argument holds `$(cat …)`, `$(< …)`, `$(head …)` or a backquoted `cat`:
   directly, nested inside another substitution, or through a variable
   assigned from such a read earlier in the same `run:` block, followed
   transitively. Backslash-continued lines are one command.
2. A multi-line step output in a command argument or an `env:` value.
   `${{ steps.<id>.outputs.<name> }}` in a command's argument in `run:` text
   (directly or through a variable, as above), or in an `env:` value, where
   the step with id `<id>` writes `<name>` to `$GITHUB_OUTPUT` in the heredoc
   form `<name><<<delimiter>` from its `run:` block.

Not a site: a read bounded in the substitution itself, `$(head -c N FILE)`
with `N` a literal integer no larger than `BOUNDED_READ_MAX` bytes; a
redirect (`< file`, `<<< "$X"`, `--body-file=…`, `-F file`); a `cat` whose
only input is a here-document written in the script; a read whose value only
reaches builtins.

What it cannot see:

* a step output a Python script writes through `github_output.render`: only
  the heredoc form written by a `run:` block's own text is recognized, so an
  output whose writer is a script is read as single-line;
* a generator's stdout captured with `$(python3 …)` (or any other command):
  the check knows a file read by its spelling, never the size of what a
  program prints;
* `with:` inputs to an action — they are handed to the action, not to a
  shell, and are not read at all.

The shell is read by a small lexer, not by bash: quotes, `$( )`, `${ }`,
backquotes, comments, here-documents and continuations are honored, and an
`${{ … }}` expression is replaced before lexing, exactly as Actions replaces
it before bash runs. A `run:` block under a `shell:` that is not bash or sh
is not read.

    python3 scripts/check_argv_bodies.py check [--root DIR] [WORKFLOW ...]

`check` prints every site, then one summary line either way,
`argv-bodies: <w> workflows, <r> run blocks read, <s> sites`, and exits 1
when any site is found. With no WORKFLOW it reads every `*.yml`/`*.yaml`
under `<root>/.github/workflows/` (the root defaults to this checkout).

Standard library plus PyYAML.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: The largest literal byte count `$(head -c N FILE)` may read and still be
#: bounded — the contract the workflow-sites card (DRE-8819) reads titles by.
BOUNDED_READ_MAX = 4096

#: The kernel's ceiling on one argument or environment string.
MAX_ARG_STRLEN = 131072

LIMIT = ("Linux refuses any single argument or environment string over "
         "131,072 bytes (MAX_ARG_STRLEN) with E2BIG; bash reports it as "
         "exit code 126")
FIX = ("pass the path instead (`--body-file=PATH`, `-F PATH`, or stdin), or "
       f"bound the read with `$(head -c N FILE)`, N ≤ {BOUNDED_READ_MAX}")

#: Bash builtins: a command named here runs inside the shell, so its
#: arguments never reach `execve`.
BUILTINS = frozenset("""
    : . [ alias bg bind break builtin caller cd compgen complete compopt
    continue declare dirs disown echo enable eval exit export false fc fg
    getopts hash help history jobs kill let local logout mapfile popd printf
    pushd pwd read readarray readonly return set shift shopt source suspend
    test times trap true type typeset ulimit umask unalias unset wait
""".split())

#: Words that start a compound command or prefix a simple one. Each is
#: skipped and the command that follows it is read.
KEYWORDS = frozenset("""
    ! { } if then elif else fi while until do done time esac ]]
""".split())

#: Compound commands whose remaining words are not a command's arguments.
#: `[[` is a keyword too: its words are tested, never executed.
WHOLE_KEYWORDS = frozenset("case for select function [[ ((".split())

#: Builtins whose `NAME=value` arguments assign a variable.
DECLARERS = frozenset("declare export local readonly typeset".split())

#: Prefixes that run the next word as the command.
PASS_THROUGH = frozenset("command exec".split())

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=")
_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_EXPR_TOKEN = "__ARGV_BODIES_EXPR_{}__"
_EXPR_TOKEN_RE = re.compile(r"__ARGV_BODIES_EXPR_(\d+)__")
_STEP_OUTPUT = re.compile(r"\bsteps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")
_HEREDOC_OUTPUT = re.compile(r"[\"']([A-Za-z_][A-Za-z0-9_-]*)<<(?!<)")
#: A here-document operator: `<<` or `<<-`, then a delimiter word — never
#: `<<<`, and never the shift in `(( x << 2 ))`.
_HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?=[A-Za-z_\"'\\])")
_REDIRECT = re.compile(r"\d*(<<<|<<-?|>>|>&|<&|<>|>\||<|>)")


@dataclass(frozen=True)
class Site:
    """One place a body reaches `execve` or an environment string whole."""
    line: int
    job: str
    step: str
    what: str

    def message(self, path: str) -> str:
        return (f"{path}:{self.line}: job {self.job!r}, step {self.step!r}: "
                f"{self.what}. {LIMIT}. Fix: {FIX}.")


# --- The lexer ---------------------------------------------------------------

@dataclass
class Word:
    text: str
    line: int
    redirect: str = ""        # the operator, when this word is its target


@dataclass
class Command:
    words: list
    line: int


def _skip_quoted(s: str, i: int, quote: str) -> int:
    """`i` is just past an opening quote: the index just past its close."""
    n = len(s)
    if quote == "'":
        j = s.find("'", i)
        return n if j < 0 else j + 1
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif c == "`":
            i = _skip_backquote(s, i + 1)
        elif s.startswith("$(", i):
            i = _skip_paren(s, i + 2)
        elif s.startswith("${", i):
            i = _skip_brace(s, i + 2)
        else:
            i += 1
    return n


def _skip_backquote(s: str, i: int) -> int:
    n = len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
        elif s[i] == "`":
            return i + 1
        else:
            i += 1
    return n


def _skip_brace(s: str, i: int) -> int:
    depth, n = 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            i = _skip_quoted(s, i + 1, c)
            continue
        if s.startswith("$(", i):
            i = _skip_paren(s, i + 2)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _skip_paren(s: str, i: int) -> int:
    """`i` is just past `$(` (or `(`): the index just past the matching `)`.
    Here-documents inside are skipped, so their text never closes it."""
    depth, n = 1, len(s)
    pending = []
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            i = _skip_quoted(s, i + 1, c)
            continue
        if c == "`":
            i = _skip_backquote(s, i + 1)
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\n;|&("):
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if s.startswith("<<<", i):
            i += 3
            continue
        m = _HEREDOC_OP.match(s, i)
        if m:
            delim, i = _heredoc_delim(s, m.end())
            if delim:
                pending.append((delim, m.group(1) == "-"))
            continue
        if c == "\n" and pending:
            i = _skip_heredocs(s, i + 1, pending)
            pending = []
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _heredoc_delim(s: str, i: int):
    """The delimiter word at `i`, unquoted, and the index past it."""
    n, out = len(s), []
    while i < n and s[i] not in " \t\n;|&<>()":
        c = s[i]
        if c in "'\"":
            j = s.find(c, i + 1)
            j = n if j < 0 else j
            out.append(s[i + 1:j])
            i = j + 1
        elif c == "\\":
            out.append(s[i + 1:i + 2])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out), i


def _skip_heredocs(s: str, i: int, pending) -> int:
    """`i` starts the line after the operator: the index past every body."""
    n = len(s)
    for delim, strip_tabs in pending:
        while i < n:
            j = s.find("\n", i)
            end = n if j < 0 else j
            line = s[i:end]
            i = end + 1 if j >= 0 else n
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                break
    return i


def lex(text: str, base_line: int = 1) -> list:
    """Every simple command in a shell script, as `Command`s of `Word`s.

    A newline, `;`, `&`, `|`, `(` or `)` ends a command; a word written
    after a redirect operator is that redirect's target, never an argument.
    """
    commands = []
    words: list = []
    line = base_line
    start = None
    redirect = ""
    pending = []
    i, n = 0, len(text)

    def end():
        nonlocal words, start
        if words:
            commands.append(Command(words, start))
        words, start = [], None

    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2
            line += 1
            continue
        if c in " \t":
            i += 1
            continue
        if c == "\n":
            end()
            i += 1
            line += 1
            if pending:
                j = _skip_heredocs(text, i, pending)
                line += text.count("\n", i, j)
                i, pending = j, []
            continue
        if c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in ";&|()":
            end()
            redirect = ""
            i += 1
            continue
        m = _REDIRECT.match(text, i)
        if m:
            op = m.group(1)
            here = _HEREDOC_OP.match(text, m.start(1)) if op in ("<<", "<<-") else None
            if here:
                delim, i = _heredoc_delim(text, here.end())
                pending.append((delim, op == "<<-"))
                if start is None:
                    start = line
                words.append(Word("<<" + delim, line, op))
                continue
            i = m.end()
            redirect = op
            continue
        # One word.
        j = i
        while j < n and text[j] not in " \t\n;&|()<>":
            ch = text[j]
            if ch == "\\":
                j += 2
            elif ch in "'\"":
                j = _skip_quoted(text, j + 1, ch)
            elif ch == "`":
                j = _skip_backquote(text, j + 1)
            elif text.startswith("$((", j):
                j = _skip_paren(text, j + 3)
                if j < n and text[j] == ")":
                    j += 1
            elif text.startswith("$(", j):
                j = _skip_paren(text, j + 2)
            elif text.startswith("${", j):
                j = _skip_brace(text, j + 2)
            else:
                j += 1
        word = text[i:j]
        if start is None:
            start = line
        words.append(Word(word, line, redirect))
        redirect = ""
        line += word.count("\n")
        i = j
    end()
    return commands


def substitutions(word: str):
    """Every command substitution in a word, outside single quotes, as
    `(body, offset of the body in the word)`."""
    out = []
    i, n = 0, len(word)
    in_double = False
    while i < n:
        c = word[i]
        if c == "\\":
            i += 2
        elif c == "'" and not in_double:
            j = word.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            in_double = not in_double
            i += 1
        elif c == "`":
            j = _skip_backquote(word, i + 1)
            out.append((word[i + 1:j - 1], i + 1))
            i = j
        elif word.startswith("$((", i):
            j = _skip_paren(word, i + 3)
            if j < n and word[j] == ")":
                j += 1
            i = j
        elif word.startswith("$(", i):
            j = _skip_paren(word, i + 2)
            out.append((word[i + 2:j - 1], i + 2))
            i = j
        elif word.startswith("${", i):
            i = _skip_brace(word, i + 2)
        else:
            i += 1
    return out


def _unquoted(word: str) -> str:
    """A word with its quotes removed, for comparing it to a literal."""
    return re.sub(r"""['"\\]""", "", word)


def _bounded(words) -> bool:
    """`head -c N FILE` with N a literal no larger than BOUNDED_READ_MAX."""
    plain = [_unquoted(w.text) for w in words if not w.redirect]
    return (len(plain) == 4 and plain[1] == "-c" and plain[2].isdigit()
            and int(plain[2]) <= BOUNDED_READ_MAX)


def _command_word(words):
    """The index of the word bash runs, after assignments and keywords, or
    None when nothing is executed. The second value is True when the
    command is a compound one whose words are not arguments."""
    k = 0
    while k < len(words):
        w = words[k]
        if w.redirect:
            k += 1
            continue
        t = w.text
        if _ASSIGN.match(t):
            k += 1
            continue
        if t in WHOLE_KEYWORDS or t.startswith("(("):
            return None, True
        if t in KEYWORDS or t in PASS_THROUGH:
            k += 1
            continue
        return k, False
    return None, False


# --- The analysis ------------------------------------------------------------

class _Script:
    """One `run:` block, read command by command in order."""

    def __init__(self, exprs: dict, heredoc_outputs: set):
        self.exprs = exprs              # token number -> expression text
        self.heredoc_outputs = heredoc_outputs
        self.tainted: dict = {}         # variable -> what it holds
        self.found: list = []           # (line, what)

    def _multi_line_output(self, text: str):
        for m in _EXPR_TOKEN_RE.finditer(text):
            for out in _STEP_OUTPUT.finditer(self.exprs.get(int(m.group(1)), "")):
                if (out.group(1), out.group(2)) in self.heredoc_outputs:
                    return f"the multi-line step output steps.{out.group(1)}.outputs.{out.group(2)}"
        return None

    def _read(self, body: str, base_line: int):
        """What a substitution's body reads whole, or None."""
        stripped = body.lstrip()
        if stripped.startswith("<") and not stripped.startswith("<<"):
            return f"the file read $({stripped.strip()})"
        cmds = lex(body, base_line)
        if not cmds:
            return None
        first = cmds[0]
        k, _ = _command_word(first.words)
        if k is None:
            return None
        name = _unquoted(first.words[k].text)
        args = first.words[k + 1:]
        if name == "cat":
            if any(w.redirect in ("<<", "<<-", "<<<") for w in args) and \
                    not any(not w.redirect and not w.text.startswith("-") for w in args):
                return None
            return f"the file read $({' '.join(w.text for w in first.words[k:])})"
        if name == "head":
            if len(cmds) == 1 and _bounded(first.words[k:]):
                return None
            return f"the file read $({' '.join(w.text for w in first.words[k:])})"
        return None

    def holds(self, word: Word):
        """What body a word carries whole, or None."""
        text = word.text
        what = self._multi_line_output(text)
        if what:
            return what
        for body, offset in substitutions(text):
            base = word.line + text[:offset].count("\n")
            what = self._read(body, base)
            if what:
                return what
            for cmd in lex(body, base):
                for w in cmd.words:
                    if w.redirect:
                        continue
                    what = self.holds(w)
                    if what:
                        return what
        expanded = _without_single_quotes(text)
        for name, what in self.tainted.items():
            if re.search(r"\$\{?" + re.escape(name) + r"(?![A-Za-z0-9_])", expanded):
                return f"${name}, which holds {what}"
        return None

    def nested(self, word: Word):
        """Read the commands inside every substitution of a word."""
        for body, offset in substitutions(word.text):
            base = word.line + word.text[:offset].count("\n")
            self.run(lex(body, base), subshell=True)

    def run(self, commands, subshell: bool = False):
        for cmd in commands:
            for w in cmd.words:
                self.nested(w)
            k, whole = _command_word(cmd.words)
            words = cmd.words
            assigns = []
            # `VAR=value cmd` sets VAR for cmd alone; only a bare assignment
            # keeps it for the commands after.
            for w in words if k is None else []:
                m = _ASSIGN.match(w.text)
                if m and not w.redirect:
                    assigns.append((m.group(1), Word(w.text[m.end():], w.line)))
            if k is not None and not whole:
                name = _unquoted(words[k].text)
                if name in DECLARERS:
                    for w in words[k + 1:]:
                        m = _ASSIGN.match(w.text)
                        if m and not w.redirect:
                            assigns.append((m.group(1), Word(w.text[m.end():], w.line)))
                elif name not in BUILTINS:
                    for w in words[k + 1:]:
                        if w.redirect:
                            continue
                        what = self.holds(w)
                        if what:
                            self.found.append(
                                (cmd.line, f"`{name}` is handed {what} as one argument"))
                            break
            if subshell:
                continue
            for var, value in assigns:
                what = self.holds(value)
                if what:
                    self.tainted[var] = what


def _without_single_quotes(text: str) -> str:
    out, i, n, in_double = [], 0, len(text), False
    while i < n:
        c = text[i]
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif c == "'" and not in_double:
            j = text.find("'", i + 1)
            i = n if j < 0 else j + 1
        else:
            if c == '"':
                in_double = not in_double
            out.append(c)
            i += 1
    return "".join(out)


# --- The workflow ------------------------------------------------------------

def _get(node, key):
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return v
    return None


def _items(node):
    return node.value if isinstance(node, yaml.MappingNode) else []


def _first_line(node) -> int:
    """The 1-based source line of a scalar's first line of text."""
    if node.style in ("|", ">"):
        return node.start_mark.line + 2
    return node.start_mark.line + 1


def _bash(shell) -> bool:
    if shell is None or not isinstance(shell, yaml.ScalarNode):
        return True
    word = shell.value.split()[0] if shell.value.split() else ""
    return os.path.basename(word) in ("bash", "sh")


def _steps(root):
    """Every job as `(job id, job node, its steps)`."""
    for k, job in _items(_get(root, "jobs")):
        steps = _get(job, "steps")
        yield k.value, job, steps.value if isinstance(steps, yaml.SequenceNode) else []


def _step_name(step, index: int) -> str:
    for key in ("name", "id"):
        v = _get(step, key)
        if isinstance(v, yaml.ScalarNode) and v.value:
            return v.value
    return f"step {index + 1}"


def _heredoc_outputs(root) -> set:
    """`(step id, output name)` for every output a step's `run:` block writes
    to `$GITHUB_OUTPUT` in the heredoc form."""
    found = set()
    for _job, _node, steps in _steps(root):
        for step in steps:
            sid, run = _get(step, "id"), _get(step, "run")
            if not isinstance(sid, yaml.ScalarNode) or not isinstance(run, yaml.ScalarNode):
                continue
            if "GITHUB_OUTPUT" not in run.value:
                continue
            for m in _HEREDOC_OUTPUT.finditer(run.value):
                found.add((sid.value, m.group(1)))
    return found


def _replace_exprs(text: str):
    exprs = {}

    def sub(m):
        exprs[len(exprs)] = m.group(1)
        return _EXPR_TOKEN.format(len(exprs) - 1) + "\n" * m.group(0).count("\n")
    return _EXPR.sub(sub, text), exprs


def _env_sites(env, job: str, step: str, heredoc_outputs: set) -> list:
    out = []
    for k, v in _items(env):
        if not isinstance(v, yaml.ScalarNode):
            continue
        for m in _STEP_OUTPUT.finditer(v.value):
            if (m.group(1), m.group(2)) in heredoc_outputs and "${{" in v.value:
                out.append(Site(
                    v.start_mark.line + 1, job, step,
                    f"env {k.value} is set to the multi-line step output "
                    f"steps.{m.group(1)}.outputs.{m.group(2)} as one "
                    f"environment string"))
                break
    return out


def read(workflow_text: str):
    """`(sites, run blocks read)` for one workflow's text."""
    root = yaml.compose(workflow_text)
    if root is None:
        return [], 0
    heredoc_outputs = _heredoc_outputs(root)
    found = []
    blocks = 0
    found += _env_sites(_get(root, "env"), "(workflow)", "(workflow env)", heredoc_outputs)
    default_shell = _get(_get(_get(root, "defaults"), "run"), "shell")
    for job, node, steps in _steps(root):
        found += _env_sites(_get(node, "env"), job, "(job env)", heredoc_outputs)
        job_shell = _get(_get(_get(node, "defaults"), "run"), "shell") or default_shell
        for index, step in enumerate(steps):
            name = _step_name(step, index)
            found += _env_sites(_get(step, "env"), job, name, heredoc_outputs)
            run = _get(step, "run")
            if not isinstance(run, yaml.ScalarNode):
                continue
            if not _bash(_get(step, "shell") or job_shell):
                continue
            blocks += 1
            text, exprs = _replace_exprs(run.value)
            script = _Script(exprs, heredoc_outputs)
            script.run(lex(text, _first_line(run)))
            seen = set()
            for line, what in script.found:
                if (line, what) not in seen:
                    seen.add((line, what))
                    found.append(Site(line, job, name, what))
    found.sort(key=lambda s: s.line)
    return found, blocks


def sites(workflow_text: str) -> list:
    """Every site in one workflow's text, in line order."""
    return read(workflow_text)[0]


def workflows(root: str = ROOT) -> list:
    """Every workflow under `<root>/.github/workflows/`, by glob."""
    base = os.path.join(root, ".github", "workflows")
    return sorted(glob.glob(os.path.join(base, "*.yml"))
                  + glob.glob(os.path.join(base, "*.yaml")))


def check(paths) -> tuple:
    """`(messages, summary)` for the workflows at `paths`."""
    messages, blocks, count = [], 0, 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            found, n = read(fh.read())
        blocks += n
        count += len(found)
        rel = os.path.relpath(path, ROOT) if os.path.abspath(path).startswith(ROOT + os.sep) else path
        messages += [s.message(rel) for s in found]
    summary = f"argv-bodies: {len(paths)} workflows, {blocks} run blocks read, {count} sites"
    return messages, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="report every site; exit 1 when any is found")
    c.add_argument("--root", default=ROOT,
                   help="the checkout whose .github/workflows/ is read")
    c.add_argument("workflow", nargs="*", help="read only these workflow files")
    args = parser.parse_args(argv)
    paths = args.workflow or workflows(args.root)
    messages, summary = check(paths)
    for m in messages:
        print(m)
    print(summary)
    return 1 if messages else 0


if __name__ == "__main__":
    sys.exit(main())
