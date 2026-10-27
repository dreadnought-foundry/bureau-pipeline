#!/usr/bin/env python3
"""The one redaction seam and the one name guard for text leaving a run (DRE-8166, stdlib only).

Every place text leaves a run — the proof run's record, its pull request body
and comments, the shadow push, the critic's comment — goes through this module.
It reads the live roster (`name_roster.read`, DRE-8165) and never holds a name
anywhere but in memory.

REDACT. `redact(text, roster)` replaces each roster form with its role,
`[the CEO]` or `[a person]`. The forms are tried longest literal first and a
span one form has claimed is never matched by another, so a full name is never
half-replaced into a surname. A role already in the text is claimed before any
form is tried, so a role is never matched again. Text with no form comes back
unchanged.

FINDINGS. `findings(lines, roster)` reads `(file, line, text)` triples and
reports each remaining form as `Finding(file, line, kind)` — never the text.

THE READERS. The guard reads only what is about to leave, never the whole tree,
because `main` already holds names that are the operator's to deal with:

* a text file — what is about to be posted;
* a git range — the added lines of `git diff --unified=0 <base>...<head>` (git
  diffs from the merge base), by new-file line number, then every line of each
  non-merge commit message in `<base>..<head>` as `commit:<sha7>`, because a
  squash merge concatenates the branch's commit messages;
* git's pre-push stdin — `<local ref> <local sha> <remote ref> <remote sha>`
  per line, a deletion skipped, each pushed sha read as a range from
  `--default-ref`. Before reading, the self-test runs and prints its line, so
  every push is its own positive control.

THE SWITCH. The guard is live only when `NAME_GUARD_LIVE` (`SWITCH`) is exactly
`true` in the environment; the wiring sets it from the repository variable.
Live, a remaining form exits 1, and an unreadable roster or input exits 2.
Not live, the same things are read, `dry` lines are printed and the exit is 0.
A roster that cannot be read is never passed when live.

THE LINES. Every line opens with `name-guard:` (`TAG`, declared in
`config/proof-reach.json`) and names a file, a line and a kind, or a read that
failed — never a name. A file whose path carries a form is printed redacted:

    name-guard: refused — <file>:<line> carries a <kind> form            exit 1
    name-guard: refused — roster unreadable: <reason>                    exit 2
    name-guard: ok — <n> lines read, nothing to refuse                   exit 0
    name-guard: dry — would refuse <file>:<line> (<kind>)                exit 0
    name-guard: dry — roster unreadable: <reason>                        exit 0
    name-guard: self-test ok — 1 form refused, redacted to <role>

    python3 scripts/name_redact.py redact --file F [--in-place] --owner <org> [--actor LOGIN …]
    python3 scripts/name_redact.py guard --text-file F --owner <org> [--actor LOGIN …]
    python3 scripts/name_redact.py guard --range <base> <head> [--cwd DIR] --owner <org> [--actor …]
    python3 scripts/name_redact.py guard --pre-push --default-ref origin/<default> --owner <org> [--actor …]
    python3 scripts/name_redact.py self-test --owner <org> [--actor …]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import name_roster  # noqa: E402 — after the path insert, by design

#: The repository switch, read from the environment variable of the same name.
SWITCH = "NAME_GUARD_LIVE"

#: The tag every line opens with (config/proof-reach.json).
TAG = "name-guard:"

ROLES = (name_roster.ROLE_CEO, name_roster.ROLE_PERSON)

#: One git call's ceiling, in seconds.
GIT_TIMEOUT = 120

_HUNK = re.compile(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@")


class Finding(NamedTuple):
    file: str
    line: int
    kind: str


class InputUnreadable(RuntimeError):
    """What the guard was asked to read could not be read; the reason names the read, never its text."""


def live(env=os.environ) -> bool:
    return env.get(SWITCH) == "true"


def _claims(text: str, roster) -> list[tuple[int, int, "name_roster.Form"]]:
    """Each form's matches, longest form first, never overlapping a role or an earlier claim."""
    if not text:
        return []
    taken: list[tuple[int, int]] = []
    for role in ROLES:
        at = text.find(role)
        while at != -1:
            taken.append((at, at + len(role)))
            at = text.find(role, at + len(role))
    claims = []
    for form in roster.forms:
        pos = 0
        while pos <= len(text):
            m = form.pattern.search(text, pos)
            if not m:
                break
            start, end = m.span()
            if end == start or any(start < t_end and t_start < end for t_start, t_end in taken):
                pos = start + 1
                continue
            taken.append((start, end))
            claims.append((start, end, form))
            pos = end
    return sorted(claims, key=lambda c: c[0])


def redact(text: str, roster) -> str:
    """`text` with every roster form replaced by its role."""
    claims = _claims(text, roster)
    if not claims:
        return text
    out, last = [], 0
    for start, end, form in claims:
        out += [text[last:start], form.role]
        last = end
    out.append(text[last:])
    return "".join(out)


def findings(lines: Iterable[tuple[str, int, str]], roster) -> list[Finding]:
    """Each form remaining in `(file, line, text)` triples, once per kind per line, in order."""
    found: list[Finding] = []
    seen: set[Finding] = set()
    for file, number, text in lines:
        for _, _, form in _claims(text, roster):
            f = Finding(file, number, form.kind)
            if f not in seen:
                seen.add(f)
                found.append(f)
    return found


def _git(args: list[str], cwd) -> str:
    # Bytes, decoded here: text mode would turn a bare `\r` into a line break and split a line.
    try:
        done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd,
                              capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise InputUnreadable(f"range unreadable: git {args[0]} did not run") from None
    if done.returncode != 0:
        # git's own message names the ref it could not read; only the code is kept.
        raise InputUnreadable(f"range unreadable: git {args[0]} exited {done.returncode}")
    return done.stdout.decode("utf-8", "replace")


def _path(target: str) -> str | None:
    """The new-file path of a `+++ ` header, or None for a deletion."""
    if target == "/dev/null":
        return None
    if target.startswith('"') and target.endswith('"'):
        target = (target[1:-1].encode("utf-8").decode("unicode_escape")
                  .encode("latin-1").decode("utf-8", "replace"))
    else:
        # git closes a header path holding a space with a tab.
        target = target.rstrip("\t")
    return target[2:] if target.startswith("b/") else target


def added_lines(base: str, head: str, cwd=".") -> list[tuple[str, int, str]]:
    """The added lines of `<base>...<head>`, then each non-merge commit message of `<base>..<head>`."""
    diff = _git(["diff", "--unified=0", "--no-color", "--no-ext-diff", "--find-renames",
                 "--src-prefix=a/", "--dst-prefix=b/", f"{base}...{head}", "--"], cwd)
    out: list[tuple[str, int, str]] = []
    path, number, remaining = None, 0, 0
    for raw in diff.split("\n"):
        if remaining > 0:
            if raw.startswith("+"):
                if path is not None:
                    out.append((path, number, raw[1:]))
                number += 1
                remaining -= 1
            continue
        if raw.startswith("diff --git "):
            path = None
        elif raw.startswith("+++ "):
            path = _path(raw[4:])
        elif raw.startswith("@@ "):
            m = _HUNK.match(raw)
            if not m:
                raise InputUnreadable("range unreadable: a hunk header git diff printed")
            number = int(m.group(1))
            remaining = int(m.group(2)) if m.group(2) is not None else 1

    log = _git(["log", "--no-merges", "--format=%x1e%H%x00%B", f"{base}..{head}", "--"], cwd)
    for record in log.split("\x1e")[1:]:
        sha, _, body = record.partition("\x00")
        for n, text in enumerate(body.rstrip("\n").split("\n"), start=1):
            out.append((f"commit:{sha.strip()[:7]}", n, text))
    return out


def _text_lines(path: str) -> list[tuple[str, int, str]]:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise InputUnreadable(f"text file unreadable: {type(exc).__name__}") from None
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return [(path, n, row.rstrip("\r")) for n, row in enumerate(rows, start=1)]


def _pre_push_lines(stdin, default_ref: str, cwd) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for n, raw in enumerate(stdin, start=1):
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 4:
            raise InputUnreadable(f"pre-push unreadable: line {n} holds {len(parts)} fields, not 4")
        _, local_sha, _, _ = parts
        if set(local_sha) == {"0"}:
            continue  # a deletion adds nothing
        for row in added_lines(default_ref, local_sha, cwd=cwd):
            if row not in seen:
                seen.add(row)
                out.append(row)
    return out


def _literal(form) -> str | None:
    """The text a form matches, rebuilt from its pattern, held in memory only."""
    body = form.pattern.pattern
    head, tail = r"(?<!\w)", r"(?!\w)"
    if not (body.startswith(head) and body.endswith(tail)):
        return None
    words = body[len(head):-len(tail)].split(r"\s+")
    return " ".join(re.sub(r"\\(.)", r"\1", w) for w in words) or None


def _self_test(roster) -> tuple[str | None, str]:
    """(None, role) when one built line is refused and then redacted clean, else (the half that failed, "")."""
    if not roster.forms:
        return "findings", ""
    form = roster.forms[0]
    literal = _literal(form)
    line = f"self-test: {literal} ." if literal else ""
    if findings([("self-test", 1, line)], roster) != [Finding("self-test", 1, form.kind)]:
        return "findings", ""
    clean = redact(line, roster)
    if clean != f"self-test: {form.role} ." or findings([("self-test", 1, clean)], roster):
        return "redact", ""
    return None, form.role


def _verb(is_live: bool) -> str:
    return "refused" if is_live else "dry"


def _read_roster(args, reader):
    return reader(owner=args.owner, extra_logins=list(args.actor))


def _run_self_test(roster, is_live, say) -> bool:
    failed, role = _self_test(roster)
    if failed:
        say(f"{TAG} {_verb(is_live)} — self-test failed: {failed}")
        return False
    say(f"{TAG} self-test ok — 1 form refused, redacted to {role}")
    return True


def _guard(args, reader, is_live, stdin, say) -> int:
    try:
        roster = _read_roster(args, reader)
    except name_roster.RosterUnreadable as exc:
        say(f"{TAG} {_verb(is_live)} — roster unreadable: {exc}")
        return 2 if is_live else 0
    if args.pre_push and not args.default_ref:
        say(f"{TAG} {_verb(is_live)} — pre-push unreadable: no --default-ref")
        return 2 if is_live else 0
    if args.pre_push and not _run_self_test(roster, is_live, say) and is_live:
        return 1
    try:
        if args.text_file is not None:
            lines = _text_lines(args.text_file)
        elif args.range is not None:
            lines = added_lines(args.range[0], args.range[1], cwd=args.cwd)
        else:
            lines = _pre_push_lines(stdin, args.default_ref, args.cwd)
    except InputUnreadable as exc:
        say(f"{TAG} {_verb(is_live)} — {exc}")
        return 2 if is_live else 0
    found = findings(lines, roster)
    for f in found:
        where = redact(f.file, roster)  # a path can carry a name too
        if is_live:
            say(f"{TAG} refused — {where}:{f.line} carries a {f.kind} form")
        else:
            say(f"{TAG} dry — would refuse {where}:{f.line} ({f.kind})")
    if not found:
        say(f"{TAG} ok — {len(lines)} lines read, nothing to refuse")
    return 1 if found and is_live else 0


def _redact_file(args, reader, is_live, stdout, stderr) -> int:
    try:
        with open(args.file, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"{TAG} {_verb(is_live)} — text file unreadable: {type(exc).__name__}", file=stderr)
        return 2 if is_live else 0
    try:
        roster = _read_roster(args, reader)
    except name_roster.RosterUnreadable as exc:
        print(f"{TAG} {_verb(is_live)} — roster unreadable: {exc}", file=stderr)
        if is_live:
            return 2
        if not args.in_place:
            stdout.write(text)
        return 0
    clean = redact(text, roster)
    if not args.in_place:
        stdout.write(clean)
    elif clean != text:
        with open(args.file, "w", encoding="utf-8", newline="") as fh:
            fh.write(clean)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def roster_args(p):
        p.add_argument("--owner", required=True, help="the GitHub organization")
        p.add_argument("--actor", action="append", default=[], metavar="LOGIN",
                       help="a login to read into the roster (a run's actor); repeatable")

    red = sub.add_parser("redact", help="print the text with every form replaced by its role")
    red.add_argument("--file", required=True)
    red.add_argument("--in-place", action="store_true", help="rewrite the file instead")
    roster_args(red)

    guard = sub.add_parser("guard", help="refuse text that still carries a form")
    which = guard.add_mutually_exclusive_group(required=True)
    which.add_argument("--text-file", metavar="F", help="what is about to be posted")
    which.add_argument("--range", nargs=2, metavar=("BASE", "HEAD"),
                       help="the added lines and commit messages of a branch")
    which.add_argument("--pre-push", action="store_true", help="git's pre-push stdin")
    guard.add_argument("--default-ref", help="the default branch's ref, for --pre-push")
    guard.add_argument("--cwd", default=".", help="the repository a range is read in")
    roster_args(guard)

    test = sub.add_parser("self-test", help="refuse and redact one built line, printing neither")
    roster_args(test)
    return parser


def main(argv=None, *, reader=name_roster.read, env=None, stdin=None, stdout=None,
         stderr=None) -> int:
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    args = _parser().parse_args(argv)
    is_live = live(env)

    def say(line: str) -> None:
        print(line, file=stdout)

    if args.command == "redact":
        return _redact_file(args, reader, is_live, stdout, stderr)
    if args.command == "guard":
        return _guard(args, reader, is_live, stdin, say)
    try:
        roster = _read_roster(args, reader)
    except name_roster.RosterUnreadable as exc:
        say(f"{TAG} {_verb(is_live)} — roster unreadable: {exc}")
        return 2 if is_live else 0
    return 0 if _run_self_test(roster, is_live, say) or not is_live else 1


if __name__ == "__main__":
    sys.exit(main())
